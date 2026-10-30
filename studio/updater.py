"""updater.py -- Settings > Studio > Check for Updates (user, 2026-09-30).

Studio is a git checkout (the installer clones it). The update source is
Studio's own repository's main branch, built in (user, 2026-10-18: nobody has to
type it); the Updates dialog can point a copy at a fork instead.

* **Check** fetches that branch into FETCH_HEAD (no remote is added or
  changed) and compares it with HEAD: up to date, N new commits (listed), or
  diverged (both sides have commits: git can't just move forward).
* **Install** never discards anything. It refuses with uncommitted changes to
  tracked files, or when the branches have diverged. Otherwise it starts a
  small PowerShell helper and Studio closes: the helper waits for Studio's
  process to end, fast-forwards (git merge --ff-only, the exact commit that
  was checked), runs ``uv sync`` when pyproject.toml / uv.lock changed, writes
  the result and starts Studio again. Doing it after exit means no file is
  swapped under the running app.
* Next start, :attr:`Updater.notice` carries the result for a toast.
* **A copy downloaded as a ZIP** (user, 2026-10-21: "fatal: not a git repository" -- the
  installer, run inside an extracted ZIP, finds Studio there and clones nothing) has no
  history to compare. Check fetches the latest commit into a git folder of its own
  (config/update_git, shallow) and compares its files with the copy's, touching nothing
  else; Install's helper then makes the folder a git copy at that exact commit (git init,
  fetch from config/update_git, checkout -f), so later updates are the usual ones.
  Settings, models, projects and outputs (untracked, ignored) stay; edits to Studio's own
  files are replaced -- the dialog says so.
* **Check at startup** (user, 2026-10-05; off by default): once, a few seconds
  after Studio opens, the same check runs in the background -- only when an
  update source is set -- and a toast says when there is something new. It
  never installs anything, and it does not check again that session; Check
  for Updates does, by hand.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import settings_store
from .legacy import REPO
from .sage1 import SAGE1_PINS

logger = logging.getLogger("studio.updater")

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
FETCH_TIMEOUT_S = 90
MAX_COMMITS = 30
#: How long the helper waits for Studio to close before giving up (nothing changed).
WAIT_FOR_EXIT_S = 600
_DEPS = ("pyproject.toml", "uv.lock")
#: Where Studio's releases are (user, 2026-10-18): the installer clones this; Check for Updates
#: uses it unless the dialog was given another repository.
STUDIO_REPO = "https://github.com/NowIAmBecomeDepth/StereoCrafterStudio.git"
STUDIO_BRANCH = "main"


def _git(repo: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    # a fetch moving under 1 KB/s for 30 s ends on its own (a stalled network)
    env = dict(os.environ, GIT_HTTP_LOW_SPEED_LIMIT="1000", GIT_HTTP_LOW_SPEED_TIME="30")
    return _run(["git", "-C", str(repo), *args], timeout, env)


def _run(cmd, timeout: float, env=None) -> subprocess.CompletedProcess:
    """subprocess.run, but a timeout ends the whole process tree before the
    output is read (bug sweep 2026-10-09, A1): run() kills only git itself and
    then reads its pipes with no limit -- git's own helper (git-remote-https)
    still holds them while a fetch is stalled, so "Looking for updates..." stayed
    up until Studio restarted."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env, creationflags=_NO_WINDOW)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=_NO_WINDOW)
        proc.kill()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def current_branch(repo: Path) -> str:
    try:
        r = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        name = r.stdout.strip()
        return name if r.returncode == 0 and name != "HEAD" else ""
    except Exception:
        return ""


#: Trailer lines a commit message ends with -- not part of what changed.
_TRAILERS = ("co-authored-by:", "signed-off-by:", "reviewed-by:")


def changelog_text(body: str) -> str:
    """A commit message's body as the dialog's changelog shows it: the trailers dropped, lines
    joined back into paragraphs (git wraps them), bullets kept. Never cut (user, 2026-10-29: the
    600-character cap is gone; the dialog wraps each note and scrolls the list)."""
    paras, cur = [], []
    for raw in str(body or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if line.lower().startswith(_TRAILERS):
            continue
        if not line or line.startswith(("- ", "* ")):
            if cur:
                paras.append(" ".join(cur))
            cur = [line] if line else []
        else:
            cur.append(line)
    if cur:
        paras.append(" ".join(cur))
    return "\n".join(p for p in paras if p)


MAX_FILES = 500
_FILE_KINDS = {"A": "added", "M": "changed", "D": "deleted", "R": "renamed", "C": "copied", "T": "changed"}


def changed_files(name_status: str) -> List[Dict]:
    """`git diff --name-status` as [{kind, path}], at most MAX_FILES; a rename shows its new path."""
    out = []
    for line in str(name_status or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        kind = _FILE_KINDS.get(parts[0][0], "changed")
        path = parts[-1] if kind in ("renamed", "copied") else parts[1]
        out.append({"kind": kind, "path": path, "from": parts[1] if kind in ("renamed", "copied") else ""})
        if len(out) >= MAX_FILES:
            break
    return out


def is_checkout(repo: Path) -> bool:
    """Studio's folder is a git copy of its own (not a ZIP's files, nor inside another repository)."""
    return (Path(repo) / ".git").exists()


def zip_git_dir() -> Path:
    return settings_store.CONFIG_DIR / "update_git"


def zip_check(repo: Path, url: str, branch: str, git_dir: Path) -> Dict:
    """check() for a copy with no history (a ZIP's files): the latest commit fetched into
    `git_dir` (shallow, kept as refs/heads/update for Install) and its files compared with
    the copy's -- what Install would change. The copy itself is only read."""
    out = {"state": "error", "message": "", "sha": "", "commits": [], "files": [], "dirty": False, "deps": False,
           "zip": True, "git_dir": str(git_dir)}

    def g(*args, timeout=30):
        return _git(Path(git_dir), "--git-dir=" + str(git_dir), "--work-tree=" + str(repo), *args, timeout=timeout)

    try:
        if not (Path(git_dir) / "HEAD").exists():
            Path(git_dir).mkdir(parents=True, exist_ok=True)
            _run(["git", "init", "--quiet", "--bare", str(git_dir)], 30)
        f = g("fetch", "--quiet", "--no-tags", "--depth", "1", url, branch, timeout=FETCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        out["message"] = "GitHub did not answer in time. Check your connection and try again."
        return out
    except FileNotFoundError:
        out["message"] = "Git is not installed or not on PATH, so Studio can't look for updates."
        return out
    if f.returncode != 0:
        err = (f.stderr or f.stdout).strip().splitlines()
        out["message"] = "Could not reach " + url + " (" + branch + "): " + (err[-1] if err else "git fetch failed")
        return out
    sha = g("rev-parse", "FETCH_HEAD").stdout.strip()
    g("update-ref", "refs/heads/update", sha)
    # the commit's files as an index, refreshed against the copy's: what differs is left
    g("read-tree", sha)
    g("update-index", "-q", "--refresh", timeout=300)
    files = []
    for line in g("diff-files", "--name-status").stdout.splitlines():
        kind, _, path = line.partition("\t")
        if path:
            # a file the copy lacks is one the latest version added
            files.append({"kind": "added" if kind.startswith("D") else "changed", "path": path, "from": ""})
    out["sha"], out["files"] = sha, files[:MAX_FILES]
    out["deps"] = any(f["path"] in _DEPS for f in files)
    out["state"] = "available"
    keep = ("Your settings, models, projects and outputs stay; changes made to Studio's own files are replaced.")
    if files:
        out["message"] = (f"This copy was downloaded as a ZIP, so it has no update history. {len(files)} of its "
                          f"files differ from the latest version. Install & Restart brings it up to date and sets "
                          f"it up so later updates work as usual. " + keep)
    else:
        out["message"] = ("This copy was downloaded as a ZIP and matches the latest version, but it has no update "
                          "history. Install & Restart sets it up so later updates work as usual (none of its "
                          "files change).")
    return out


def check(repo: Path, url: str, branch: str, git_dir: Optional[Path] = None) -> Dict:
    """Compare HEAD with ``branch`` at ``url``. Returns a dict:
    state (up_to_date | available | diverged | error), message, sha (the
    fetched commit), commits [{sha, date, subject}], dirty, deps; a ZIP's copy
    (no .git) goes to zip_check."""
    out = {"state": "error", "message": "", "sha": "", "commits": [], "files": [], "dirty": False, "deps": False}
    url, branch = str(url or "").strip(), str(branch or "").strip()
    if not url or not branch:
        out["message"] = "Set the repository and branch to check first."
        return out
    if not is_checkout(repo):
        return zip_check(repo, url, branch, git_dir or zip_git_dir())
    try:
        f = _git(repo, "fetch", "--quiet", "--no-tags", url, branch, timeout=FETCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        out["message"] = "GitHub did not answer in time. Check your connection and try again."
        return out
    except FileNotFoundError:
        out["message"] = "Git is not installed or not on PATH, so Studio can't look for updates."
        return out
    if f.returncode != 0:
        err = (f.stderr or f.stdout).strip().splitlines()
        out["message"] = "Could not reach " + url + " (" + branch + "): " + (err[-1] if err else "git fetch failed")
        return out
    sha = _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    out["sha"] = sha
    new = int(_git(repo, "rev-list", "--count", "HEAD.." + sha).stdout.strip() or 0)
    ahead = int(_git(repo, "rev-list", "--count", sha + "..HEAD").stdout.strip() or 0)
    out["dirty"] = bool(_git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip())
    if new == 0:
        out["state"] = "up_to_date"
        out["message"] = "Studio is up to date." + (f" (This copy has {ahead} commit(s) not on GitHub yet.)"
                                                    if ahead else "")
        return out
    # the changelog the dialog shows (user, 2026-10-18): each commit's title and what its message says
    log = _git(repo, "log", f"-n{MAX_COMMITS}", "--date=short", "--format=%h%x09%ad%x09%s%x09%b%x1e", "HEAD.." + sha)
    for record in log.stdout.split("\x1e"):
        parts = record.strip("\r\n").split("\t", 3)
        if len(parts) >= 3:
            out["commits"].append({"sha": parts[0], "date": parts[1], "subject": parts[2],
                                   "body": changelog_text(parts[3] if len(parts) > 3 else "")})
    out["deps"] = bool(_git(repo, "diff", "--name-only", "HEAD", sha, "--", *_DEPS).stdout.strip())
    # the files the update would change (user, 2026-10-18): added / changed / deleted / renamed
    out["files"] = changed_files(_git(repo, "diff", "--name-status", "-M", "HEAD", sha).stdout)
    if ahead:
        out["state"] = "diverged"
        out["message"] = (f"GitHub has {new} new commit(s), but this copy also has {ahead} of its own. "
                          "They have to be combined by hand (git pull), so Studio won't install this.")
    else:
        out["state"] = "available"
        out["message"] = f"{new} update(s) available."
    return out


def refuse_reason(result: Dict) -> str:
    """Why Install can't go ahead for this check result, or ''."""
    if result.get("state") != "available":
        return "Nothing to install." if result.get("state") == "up_to_date" else (result.get("message") or "Check first.")
    if result.get("dirty"):
        return ("This copy has changes that aren't committed. Commit them first (Studio never "
                "discards your changes), then install.")
    return ""


def _ps(s) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(s).replace("'", "''") + "'"


def helper_script(repo: Path, pid: int, sha: str, launch: str, result_path: Path, log_path: Path,
                  run_uv: bool, zip_from: Optional[Path] = None, url: str = "") -> str:
    """The PowerShell that runs after Studio exits (see the module doc). `zip_from`: a ZIP's
    copy -- made a git copy at `sha`, fetched from that folder (zip_check's), instead of merged."""
    if zip_from is not None:
        apply = [
            f"$zipgit = {_ps(zip_from)}; $url = {_ps(url)}",
            "Log 'Making this copy a git copy (it was downloaded as a ZIP)'",
            "git init --quiet 2>&1 | ForEach-Object { Log $_ }",
            "git fetch --quiet --no-tags --depth 1 $zipgit refs/heads/update 2>&1 | ForEach-Object { Log $_ }",
            "if ($LASTEXITCODE -eq 0) { git checkout --quiet -f -B main $sha 2>&1 | ForEach-Object { Log $_ } }",
            # a half-made one would read as a git copy with no commit: gone, so the next check is a ZIP's again
            "if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath (Join-Path $repo '.git') -Recurse -Force "
            "-ErrorAction SilentlyContinue; Done $false 'The update could not be applied (git checkout failed). "
            "Nothing was changed. See config/update_last.log.' }",
            "if ($url) { git remote add origin $url 2>&1 | ForEach-Object { Log $_ } }",
            "Remove-Item -LiteralPath $zipgit -Recurse -Force -ErrorAction SilentlyContinue",
        ]
    else:
        apply = [
            "git merge --ff-only $sha 2>&1 | ForEach-Object { Log $_ }",
            "if ($LASTEXITCODE -ne 0) { Done $false 'The update could not be applied (git merge failed). "
            "Nothing was changed. See config/update_last.log.' }",
        ]
    return "\n".join([
        "$ErrorActionPreference = 'Continue'",
        f"$repo = {_ps(repo)}; $sha = {_ps(sha)}; $launch = {_ps(launch)}",
        f"$result = {_ps(result_path)}; $log = {_ps(log_path)}",
        "function Log($m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format 'HH:mm:ss') + '  ' + $m) }",
        "function Done($ok, $msg) {",
        "  $o = [ordered]@{ ok = $ok; sha = $sha; message = $msg }",
        "  Set-Content -LiteralPath $result -Value ($o | ConvertTo-Json) -Encoding UTF8",
        "  Log $msg",
        "  if ($launch) { Start-Process -FilePath $launch -WorkingDirectory $repo }",
        "  exit",
        "}",
        "Set-Content -LiteralPath $log -Value '' -Encoding UTF8",
        f"Log 'Waiting for Studio (pid {int(pid)}) to close'",
        f"try {{ Wait-Process -Id {int(pid)} -Timeout {WAIT_FOR_EXIT_S} -ErrorAction Stop }} catch {{ }}",
        f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ $launch = ''; "
        "Done $false 'Studio did not close, so nothing was updated.' }",
        "Set-Location -LiteralPath $repo",
        *apply,
        "$uvNote = ''",
        "if (" + ("$true" if run_uv else "$false") + ") {",
        "  $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source",
        "  if (-not $uv) { $cand = Join-Path $env:USERPROFILE '.local\\bin\\uv.exe'; if (Test-Path $cand) { $uv = $cand } }",
        "  if ($uv) {",
        # Inpainting's TensorRT is an extra (2026-10-29): a plain sync would take it out again
        "    $uvArgs = @('sync')",
        "    if (Test-Path -LiteralPath (Join-Path $repo '.venv\\Lib\\site-packages\\tensorrt')) "
        "{ $uvArgs += @('--extra', 'tensorrt') }",
        # SageAttention 1 for RTX 20 series (2026-10-30): the sync puts the pinned Sage 2 and Triton 3.6
        # back, so it is installed again after
        "    $sage1 = @(Get-ChildItem -LiteralPath (Join-Path $repo '.venv\\Lib\\site-packages') "
        "-Filter 'sageattention-1.*.dist-info' -Directory -ErrorAction SilentlyContinue).Count -gt 0",
        "    Log ('uv ' + ($uvArgs -join ' '))",
        "    & $uv @uvArgs 2>&1 | ForEach-Object { Log $_ }",
        "    if ($LASTEXITCODE -ne 0) { $uvNote = ' Updating the Python packages failed: run uv sync in the Studio folder.' }",
        "    elseif ($sage1) {",
        "      $sageArgs = @('pip', 'install', '--python', (Join-Path $repo '.venv\\Scripts\\python.exe'), "
        + ", ".join(f"'{p}'" for p in SAGE1_PINS) + ")",
        "      Log ('uv ' + ($sageArgs -join ' '))",
        "      & $uv @sageArgs 2>&1 | ForEach-Object { Log $_ }",
        "      if ($LASTEXITCODE -ne 0) { $uvNote = ' Putting SageAttention 1 back failed: install it again in "
        "Settings > Studio > Models.' }",
        "    }",
        "  } else { $uvNote = ' New Python packages are needed: run uv sync in the Studio folder (uv was not found).' }",
        "}",
        "Done ($uvNote -eq '') ('Studio was updated.' + $uvNote)",
    ]) + "\n"


#: How the helper is started: hidden, in its own process group so it outlives Studio. NOT
#: DETACHED_PROCESS (0x8): Windows PowerShell started with no console at all ends before it runs a
#: line -- Install closed Studio, nothing was updated and nothing reopened (a user, 2026-10-24).
HELPER_FLAGS = _NO_WINDOW | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def start_helper(script: Path, cwd: Path) -> subprocess.Popen:
    """Start the update helper (helper_script, written to `script`); it waits for Studio to close."""
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                             "-File", str(script)], cwd=str(cwd), close_fds=True, creationflags=HELPER_FLAGS)


def launch_target(repo: Path) -> str:
    for name in ("StereoCrafter Studio.exe", "StereoCrafter Studio.bat"):
        if (repo / name).exists():
            return str(repo / name)
    return ""


class Updater(QObject):
    """The Updates dialog's state. Check runs on a worker thread (a network
    fetch); everything QML reads is set on the UI thread via ``_done``."""

    changed = Signal()
    _done = Signal(dict)
    #: Install started the helper: Main closes Studio.
    quitForUpdate = Signal()
    #: The startup check found an update: (new commits, branch) for a toast.
    updateFound = Signal(int, str)

    def __init__(self, repo: Path = REPO, run_check: Callable = check, parent=None):
        super().__init__(parent)
        self._repo = Path(repo)
        self._run_check = run_check
        self._data = settings_store.load("updater")
        self._result: Dict = {"state": "idle", "message": "", "commits": []}
        self._busy = False
        #: () -> the running job's stage name, or "" (app.py: GpuJobs.running)
        self.job_running: Callable = lambda: ""
        self._auto = False                 # the running check is the startup one
        self._auto_done = False            # ...which runs once a session
        self._notice = self._read_result()
        self._done.connect(self._on_done)
        #: the checkout's branch, read once: every read of `branch` ran git on the UI
        #: thread while none was saved (bug sweep A1); it can't change under a running Studio
        self._head_branch: Optional[str] = None

    # -- where to look ------------------------------------------------------
    def _get_repo_url(self) -> str:
        return str(self._data.get("repo", "") or "") or STUDIO_REPO

    def _get_branch(self) -> str:
        saved = str(self._data.get("branch", "") or "")
        if saved:
            return saved
        if self._get_repo_url() == STUDIO_REPO:
            return STUDIO_BRANCH             # Studio's releases: main, whatever branch this copy is on
        if self._head_branch is None:
            self._head_branch = current_branch(self._repo)
        return self._head_branch or "main"

    @Slot(str, str)
    def setSource(self, url: str, branch: str) -> None:
        url, branch = str(url or "").strip(), str(branch or "").strip()
        if url in ("", STUDIO_REPO):         # Studio's own: kept built in, so a later address change follows
            url = ""
            branch = "" if branch == STUDIO_BRANCH else branch
        self._data["repo"] = url
        self._data["branch"] = branch
        settings_store.save("updater", self._data)
        self._result = {"state": "idle", "message": "", "commits": []}
        self.changed.emit()

    # -- check at startup ----------------------------------------------------
    def _get_check_on_start(self) -> bool:
        return bool(self._data.get("check_on_start", False))

    @Slot(bool)
    def setCheckOnStart(self, on: bool) -> None:
        self._data["check_on_start"] = bool(on)
        settings_store.save("updater", self._data)
        self.changed.emit()

    @Slot(result=bool)
    def startupCheck(self) -> bool:
        """The check at startup: once a session, only when it is turned on and
        an update source is set. True when it started."""
        if self._auto_done or not self._get_check_on_start() or not self._get_repo_url() or self._busy:
            return False
        self._auto_done = True
        self._auto = True
        logger.info("Checking for updates (Check for Updates at Startup is on)")
        self.check()
        return True

    # -- check --------------------------------------------------------------
    @Slot()
    def check(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._result = {"state": "checking", "message": "Looking for updates...", "commits": []}
        self.changed.emit()
        url, branch = self._get_repo_url(), self._get_branch()

        def work():
            try:
                res = self._run_check(self._repo, url, branch)
            except Exception as e:
                logger.exception("Update check failed")
                res = {"state": "error", "message": f"The check failed: {e}", "commits": []}
            self._done.emit(res)
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_done(self, res: dict) -> None:
        self._busy = False
        self._result = dict(res)
        logger.info("Update check: %s", self._result.get("message", ""))
        self.changed.emit()
        if self._auto:
            self._auto = False
            if self._result.get("state") == "available":
                if not self._result.get("zip"):
                    self.updateFound.emit(len(self._result.get("commits", [])), self._get_branch())
                elif self._result.get("files"):         # a ZIP's copy that differs: no count to give
                    self.updateFound.emit(-1, self._get_branch())

    # -- install ------------------------------------------------------------
    @Slot(result=str)
    def install(self) -> str:
        """Start the helper and ask Main to close. Returns '' when it did, else
        the reason it can't (for the dialog)."""
        why = refuse_reason(self._result)
        if why:
            return why
        # Not during a job (bug sweep 2026-10-09, A1): the helper started, the close was
        # then refused ("a job is running"), and the update went in at a later close the
        # user never connected with it. The dialog disables Install too; this is the rule.
        try:
            running = self.job_running()
        except Exception:
            running = ""
        if running:
            return (f"{running} is running a job. Let it finish (or stop it), then install -- "
                    f"installing closes Studio.")
        cfg = settings_store.CONFIG_DIR
        cfg.mkdir(parents=True, exist_ok=True)
        script = cfg / "update_helper.ps1"
        zip_from = Path(self._result["git_dir"]) if self._result.get("zip") and self._result.get("git_dir") else None
        script.write_text(helper_script(self._repo, os.getpid(), self._result["sha"], launch_target(self._repo),
                                        cfg / "update_result.json", cfg / "update_last.log",
                                        bool(self._result.get("deps")), zip_from=zip_from,
                                        url=self._get_repo_url()), encoding="utf-8-sig")
        try:
            start_helper(script, self._repo)
        except Exception as e:
            logger.exception("Could not start the update helper")
            return f"Could not start the update: {e}"
        logger.info("Update helper started for %s; closing Studio", self._result["sha"][:10])
        self.quitForUpdate.emit()
        return ""

    # -- the last update's result, for a toast at start ---------------------
    def _read_result(self) -> dict:
        path = settings_store.CONFIG_DIR / "update_result.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            path.unlink()
            return {"ok": bool(data.get("ok")), "message": str(data.get("message", ""))}
        except FileNotFoundError:
            return {}
        except Exception:
            logger.debug("Unreadable update result", exc_info=True)
            return {}

    checkOnStart = Property(bool, _get_check_on_start, notify=changed)
    repoUrl = Property(str, _get_repo_url, notify=changed)
    branch = Property(str, _get_branch, notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    state = Property(str, lambda self: str(self._result.get("state", "idle")), notify=changed)
    message = Property(str, lambda self: str(self._result.get("message", "")), notify=changed)
    commits = Property("QVariantList", lambda self: list(self._result.get("commits", [])), notify=changed)
    needsDeps = Property(bool, lambda self: bool(self._result.get("deps")), notify=changed)
    files = Property("QVariantList", lambda self: list(self._result.get("files", [])), notify=changed)
    blocked = Property(str, lambda self: refuse_reason(self._result) if self._result.get("state") == "available" else "",
                       notify=changed)
    notice = Property("QVariantMap", lambda self: self._notice, constant=True)
