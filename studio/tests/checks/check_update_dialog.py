"""Settings > Studio > Check for Updates (user, 2026-09-30), in the real app
offscreen: the item is in Settings; the dialog asks for the repository when
none is set; the address is saved; a check lists the new commits; Install is
refused over uncommitted changes and while a job runs; Install starts the
helper (recorded here, never run) and closes Studio; the last update's result
is shown once at the next start. The git check is faked: the real repo, the
network and the running Studio are never touched. Configs and last_jobs.json
redirected."""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
# the last update's result, as the helper leaves it
ss.CONFIG_DIR.mkdir(parents=True)
(ss.CONFIG_DIR / "update_result.json").write_text(json.dumps({"ok": True, "sha": "abc", "message": "Studio was updated."}))
import studio.updater as U
launched = []
# the helper is recorded, never run -- on the updater's own copy of subprocess: patching the
# module itself broke every later Popen, torch's import included (2026-10-10: the check died
# importing torch, "partially initialized module")
import subprocess as _sp, types as _types
U.subprocess = _types.SimpleNamespace(**{k: getattr(_sp, k) for k in dir(_sp) if not k.startswith("__")})
_real_popen = _sp.Popen
def _popen(args, **kw):
    if any("git" == str(a).lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1].replace(".exe", "") for a in args[:1]):
        return _real_popen(args, **kw)      # the updater's own git reads (its branch) run as they would
    launched.append(args)
U.subprocess.Popen = _popen
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(0.8)

up = objs["Updater"]
calls = []
RESULT = {"state": "available", "message": "2 update(s) available.", "sha": "0123456789abcdef", "dirty": False,
          "deps": True, "files": [{"kind": "added", "path": "studio/console.py", "from": ""},
                                  {"kind": "changed", "path": "studio/app.py", "from": ""}],
          "commits": [{"sha": "0123456", "date": "2026-10-01", "subject": "Console in its own window",
                                     "body": "The console opens in a window of its own, a tab per stage."},
                                    {"sha": "89abcde", "date": "2026-09-30", "subject": "Check for Updates"}]}
def fake(repo, url, branch):
    calls.append((url, branch)); return dict(RESULT)
up._run_check = fake

check("last update's result, once, at start", up.property("notice") == {"ok": True, "message": "Studio was updated."}
      and not (ss.CONFIG_DIR / "update_result.json").exists())

dlg = win.findChild(QObject, "updateDialog")
check("an Updates dialog exists", dlg is not None)
QMetaObject.invokeMethod(dlg, "open"); pump(0.8)
root = dlg.property("contentItem")
def named(n): return find(root, lambda c: c.objectName() == n)
msg, install_btn, commits, blocked = named("updateMessage"), named("updateInstall"), named("updateCommits"), named("updateBlocked")
# Studio's repository is built in, with no address or branch to type (user, 2026-10-18): the dialog
# shows neither, and checks Studio's main as it opens
import studio.updater as _U
check("no address or branch fields; Studio's main checked as it opens",
      named("updateRepo") is None and named("updateBranch") is None and calls[:1] == [(_U.STUDIO_REPO, "main")],
      str(calls))
calls.clear()

up.check(); pump(1.0)
check("a check lists the new commits", calls and commits.property("count") == 2 and "2 update" in msg.property("text"),
      f"{commits.property('count')} {msg.property('text')}")
check("...and Install is on", install_btn.property("enabled") is True)
# the changelog (user, 2026-10-18): What's new and each change's description, only with an update waiting
_heading = named("updateChangelogHeading")
def _items(it, out):
    for c in it.childItems():
        out.append(c); _items(c, out)
    return out
_bodies = [c for c in _items(commits, []) if c.objectName() == "updateCommitBody" and c.isVisible()]
check("...under What's new, each change with its description",
      _heading is not None and _heading.isVisible() and commits.isVisible()
      and any("window of its own" in str(t.property("text")) for t in _bodies), str(len(_bodies)))
# the files it changes (user, 2026-10-18): a folded list, its count on the heading
_fh, _fl = named("updateFilesHeading"), named("updateFiles")
check("...the files it changes, folded, with their count",
      _fh is not None and _fh.isVisible() and "(2)" in _fh.property("text") and not _fl.isVisible(),
      str(_fh.property("text") if _fh else None))
_fh.setProperty("open", True); pump(0.3)
_paths = [str(c.property("text")) for c in _items(_fl, []) if c.objectName() == "updateFilePath"]
check("...opened: each file", _fl.isVisible() and "studio/console.py" in _paths, str(_paths))
_saved_result = dict(RESULT)
RESULT.update({"state": "up_to_date", "message": "Studio is up to date.", "commits": []}); up.check(); pump(1.0)
check("...and none of it when Studio is up to date", not _heading.isVisible() and not commits.isVisible()
      and not named("updateFilesHeading").isVisible())
RESULT.clear(); RESULT.update(_saved_result); up.check(); pump(1.0)

# uncommitted changes: refused, with the reason shown
RESULT["dirty"] = True; up.check(); pump(1.0)
check("uncommitted changes: Install off, the reason shown",
      not install_btn.property("enabled") and "committed" in blocked.property("text"), blocked.property("text"))
RESULT["dirty"] = False; up.check(); pump(1.0)

# a job running: Install waits
merging = objs["Merging"]
_fixtures.fake_busy(merging, True); pump(0.3)
check("a job running: Install off, says why", not install_btn.property("enabled") and "job" in blocked.property("text"),
      blocked.property("text"))
_fixtures.fake_busy(merging, False); pump(0.3)

# Install: the helper is started and Studio closes
check("Install on again once the job is done", install_btn.property("enabled") is True)
why = up.install(); pump(1.0)
script = ss.CONFIG_DIR / "update_helper.ps1"
check("Install starts the helper (PowerShell, the written script)",
      why == "" and launched and launched[-1][0] == "powershell" and str(script) in launched[-1], str(launched[-1:]))
body = script.read_text(encoding="utf-8-sig") if script.exists() else ""
check("...which waits for this Studio, fast-forwards to the checked commit and runs uv sync",
      f"Wait-Process -Id {os.getpid()}" in body and "merge --ff-only $sha" in body and "0123456789abcdef" in body
      and "$true) {" in body)
check("...and Studio closes", not win.isVisible())

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
