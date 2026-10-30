"""Settings > Studio > Check for Updates (2026-09-30): the check against the
user's repo, the refusals (uncommitted changes, diverged), and the helper that
fast-forwards after Studio exits -- run for real on throwaway repos.

    python -m unittest studio.tests.test_updater
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import studio.settings_store as ss
from studio import updater as U

REPO = Path(__file__).resolve().parents[2]

from studio.tests._qt import app as _qt_app
_app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.autocrlf=false", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo, name, text, msg):
    (Path(repo) / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hub, self.local = root / "github", root / "local"
        self.hub.mkdir()
        git(self.hub, "init", "-q", "-b", "studio")
        commit(self.hub, "app.py", "v1\n", "first")
        git(root, "clone", "-q", "-b", "studio", str(self.hub), str(self.local))
        self.url = str(self.hub)

    def tearDown(self):
        self.tmp.cleanup()

    def test_up_to_date(self):
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(r["state"], "up_to_date", r)

    def test_new_commits_are_listed_newest_first(self):
        commit(self.hub, "app.py", "v2\n", "Console in its own window")
        sha = commit(self.hub, "app.py", "v3\n", "Check for Updates button")
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(r["state"], "available")
        self.assertEqual(r["sha"], sha)
        self.assertEqual([c["subject"] for c in r["commits"]], ["Check for Updates button", "Console in its own window"])
        self.assertFalse(r["deps"])
        self.assertEqual(U.refuse_reason(r), "")

    def test_each_commit_brings_its_changelog(self):
        """The dialog's What's new (user, 2026-10-18): the commit's body, trailers dropped, git's
        wrapped lines joined back, bullets kept."""
        commit(self.hub, "app.py", "v2\n", "Edge Fill: filled clips green\n\nA clip filled in an earlier\nrun stays green.\n\n"
               "- one\n- two\n\nCo-Authored-By: Someone <x@y>")
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(r["commits"][0]["subject"], "Edge Fill: filled clips green")
        self.assertEqual(r["commits"][0]["body"], "A clip filled in an earlier run stays green.\n- one\n- two")
        self.assertEqual(U.changelog_text(""), "")
        # a long note whole (user, 2026-10-29: no 600-character cap)
        self.assertEqual(U.changelog_text("word " * 400), ("word " * 400).strip())

    def test_the_files_it_changes_are_listed(self):
        """The dialog's file list (user, 2026-10-18): added, changed, deleted, renamed."""
        commit(self.hub, "new.py", "x\n", "add")
        (self.hub / "app.py").write_text("v9\n", encoding="utf-8"); git(self.hub, "add", "app.py")
        git(self.hub, "commit", "-q", "-m", "change")
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(sorted((f["kind"], f["path"]) for f in r["files"]),
                         [("added", "new.py"), ("changed", "app.py")])
        self.assertEqual(U.changed_files("D\tgone.py\nR100\told.py\tnew.py\n"),
                         [{"kind": "deleted", "path": "gone.py", "from": ""},
                          {"kind": "renamed", "path": "new.py", "from": "old.py"}])

    def test_a_dependency_change_is_flagged(self):
        commit(self.hub, "uv.lock", "lock\n", "new package")
        self.assertTrue(U.check(self.local, self.url, "studio")["deps"])

    def test_local_commits_only_is_up_to_date(self):
        commit(self.local, "mine.py", "x\n", "not pushed yet")
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(r["state"], "up_to_date")
        self.assertIn("1 commit(s) not on GitHub", r["message"])

    def test_diverged_is_refused(self):
        commit(self.local, "mine.py", "x\n", "local")
        commit(self.hub, "app.py", "v2\n", "remote")
        r = U.check(self.local, self.url, "studio")
        self.assertEqual(r["state"], "diverged")
        self.assertTrue(U.refuse_reason(r))

    def test_uncommitted_changes_are_refused_never_discarded(self):
        commit(self.hub, "app.py", "v2\n", "remote")
        (self.local / "app.py").write_text("my edit\n", encoding="utf-8")
        r = U.check(self.local, self.url, "studio")
        self.assertTrue(r["dirty"])
        self.assertIn("aren't committed", U.refuse_reason(r))

    def test_untracked_files_do_not_block(self):
        commit(self.hub, "app.py", "v2\n", "remote")
        (self.local / "scratch.txt").write_text("notes\n", encoding="utf-8")
        self.assertEqual(U.refuse_reason(U.check(self.local, self.url, "studio")), "")

    def test_a_bad_address_says_so(self):
        r = U.check(self.local, str(Path(self.tmp.name) / "nowhere"), "studio")
        self.assertEqual(r["state"], "error")
        self.assertIn("Could not reach", r["message"])

    def test_not_set_up_yet(self):
        self.assertEqual(U.check(self.local, "", "studio")["state"], "error")

    # -- the helper, run for real -----------------------------------------
    def run_helper(self, sha):
        done = subprocess.Popen([sys.executable, "-c", "pass"]); done.wait()   # a Studio that has exited
        result, log = Path(self.tmp.name) / "result.json", Path(self.tmp.name) / "update.log"
        script = Path(self.tmp.name) / "helper.ps1"
        script.write_text(U.helper_script(self.local, done.pid, sha, "", result, log, False), encoding="utf-8-sig")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, timeout=120)
        return json.loads(result.read_text(encoding="utf-8-sig"))

    def test_helper_fast_forwards_to_the_checked_commit(self):
        sha = commit(self.hub, "app.py", "v2\n", "remote")
        r = U.check(self.local, self.url, "studio")
        out = self.run_helper(r["sha"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), sha)
        self.assertEqual((self.local / "app.py").read_text(encoding="utf-8"), "v2\n")

    def test_helper_changes_nothing_when_it_cannot_fast_forward(self):
        commit(self.hub, "app.py", "v2\n", "remote")
        sha = U.check(self.local, self.url, "studio")["sha"]
        mine = commit(self.local, "mine.py", "x\n", "local since the check")
        out = self.run_helper(sha)
        self.assertFalse(out["ok"])
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), mine)

    def test_helper_quotes_paths(self):
        s = U.helper_script(Path("C:/it's here"), 5, "abc", "", Path("r.json"), Path("l.log"), True)
        self.assertIn("'C:\\it''s here'", s.replace("/", "\\"))
        self.assertIn("uv sync", s)


class ZipCopyTests(unittest.TestCase):
    """A copy downloaded as a ZIP (user, 2026-10-21: "fatal: not a git repository"): the check
    compares its files with the latest version without touching it; Install's helper makes it a
    git copy at the checked commit, keeping what isn't Studio's; later updates are the usual."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hub, self.copy = root / "github", root / "zipcopy"
        self.hub.mkdir()
        git(self.hub, "init", "-q", "-b", "main")
        (self.hub / ".gitignore").write_text("config/\n", encoding="utf-8")
        git(self.hub, "add", ".gitignore")
        commit(self.hub, "app.py", "v1\n", "first")
        self.v2 = commit(self.hub, "new.py", "n\n", "second")
        (self.hub / "app.py").write_text("v2\n", encoding="utf-8")
        git(self.hub, "commit", "-q", "-am", "third")
        self.sha = git(self.hub, "rev-parse", "HEAD")
        # the ZIP of the first commit, extracted, then used: settings and a file of the user's own
        self.copy.mkdir()
        (self.copy / ".gitignore").write_text("config/\n", encoding="utf-8")
        (self.copy / "app.py").write_text("v1\n", encoding="utf-8")
        (self.copy / "config").mkdir()
        (self.copy / "config" / "studio.json").write_text("{}", encoding="utf-8")
        (self.copy / "my_notes.txt").write_text("mine", encoding="utf-8")
        self.git_dir = self.copy / "config" / "update_git"
        self.url = str(self.hub)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self):
        return U.check(self.copy, self.url, "main", git_dir=self.git_dir)

    def test_the_check_lists_what_differs_and_touches_nothing(self):
        r = self.check()
        self.assertEqual(r["state"], "available", r)
        self.assertTrue(r["zip"])
        self.assertEqual(r["sha"], self.sha)
        self.assertEqual(sorted((f["kind"], f["path"]) for f in r["files"]), [("added", "new.py"), ("changed", "app.py")])
        self.assertIn("downloaded as a ZIP", r["message"])
        self.assertEqual(U.refuse_reason(r), "")
        self.assertFalse((self.copy / ".git").exists(), "the check made the copy a git copy")
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "v1\n")

    def test_a_copy_matching_the_latest_still_offers_the_setup(self):
        (self.copy / "app.py").write_text("v2\n", encoding="utf-8")
        (self.copy / "new.py").write_text("n\n", encoding="utf-8")
        r = self.check()
        self.assertEqual((r["state"], r["files"]), ("available", []), r)
        self.assertIn("matches the latest version", r["message"])

    def run_helper(self, r):
        done = subprocess.Popen([sys.executable, "-c", "pass"]); done.wait()   # a Studio that has exited
        result, log = Path(self.tmp.name) / "result.json", Path(self.tmp.name) / "update.log"
        script = Path(self.tmp.name) / "helper.ps1"
        script.write_text(U.helper_script(self.copy, done.pid, r["sha"], "", result, log, False,
                                          zip_from=Path(r["git_dir"]), url=self.url), encoding="utf-8-sig")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, timeout=120)
        return json.loads(result.read_text(encoding="utf-8-sig"))

    def test_install_makes_it_a_git_copy_and_later_updates_are_the_usual(self):
        out = self.run_helper(self.check())
        self.assertTrue(out["ok"], out)
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), self.sha)
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "v2\n")
        self.assertTrue((self.copy / "new.py").exists())
        self.assertEqual((self.copy / "config" / "studio.json").read_text(encoding="utf-8"), "{}", "settings kept")
        self.assertEqual((self.copy / "my_notes.txt").read_text(encoding="utf-8"), "mine", "the user's file kept")
        self.assertFalse(self.git_dir.exists(), "the check's git folder left behind")
        self.assertEqual(git(self.copy, "remote", "get-url", "origin"), self.url)
        self.assertEqual(git(self.copy, "status", "--porcelain", "--untracked-files=no"), "")
        later = commit(self.hub, "app.py", "v3\n", "fourth")
        r = U.check(self.copy, self.url, "main")
        self.assertEqual((r["state"], [c["subject"] for c in r["commits"]]), ("available", ["fourth"]), r)
        self.assertNotIn("zip", r)
        self.assertTrue(self.run_helper_plain(r["sha"])["ok"])
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), later)

    def run_helper_plain(self, sha):
        done = subprocess.Popen([sys.executable, "-c", "pass"]); done.wait()
        result, log = Path(self.tmp.name) / "result2.json", Path(self.tmp.name) / "update2.log"
        script = Path(self.tmp.name) / "helper2.ps1"
        script.write_text(U.helper_script(self.copy, done.pid, sha, "", result, log, False), encoding="utf-8-sig")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, timeout=120)
        return json.loads(result.read_text(encoding="utf-8-sig"))

    def test_install_as_studio_starts_it_updates_and_reopens(self):
        """A user, 2026-10-24: "when i click install update studio closes to desktop ... it doesn't reopen
        ... its giving me the update notification again". Started detached (DETACHED_PROCESS) from a
        closing Studio, the helper never ran a line. Here a windowless Studio (pythonw) starts it as
        Install does and closes at once; the helper waits, updates, and starts the copy again."""
        r = self.check()
        (self.copy / "StereoCrafter Studio.bat").write_text('@echo off\r\necho reopened> "%~dp0reopened.txt"\r\n',
                                                          encoding="utf-8")
        cfg = self.copy / "config"
        studio = Path(self.tmp.name) / "studio.py"
        studio.write_text(
            "import json, os, sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from pathlib import Path\n"
            "from studio import updater as U\n"
            f"r = json.loads({json.dumps(json.dumps(r))})\n"
            f"copy, cfg = Path({str(self.copy)!r}), Path({str(cfg)!r})\n"
            "script = cfg / 'update_helper.ps1'\n"
            "script.write_text(U.helper_script(copy, os.getpid(), r['sha'], U.launch_target(copy),\n"
            "                  cfg / 'update_result.json', cfg / 'update_last.log', False,\n"
            f"                  zip_from=Path(r['git_dir']), url={self.url!r}), encoding='utf-8-sig')\n"
            "U.start_helper(script, copy)\n", encoding="utf-8")
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.run([str(pythonw if pythonw.exists() else sys.executable), str(studio)], timeout=60)
        result, reopened = cfg / "update_result.json", self.copy / "reopened.txt"
        end = time.time() + 90
        while time.time() < end and not (result.exists() and reopened.exists()):
            time.sleep(0.5)
        log = (cfg / "update_last.log").read_text(encoding="utf-8-sig") if (cfg / "update_last.log").exists() else "(no log)"
        self.assertTrue(result.exists(), "the helper never ran: " + log)
        self.assertTrue(json.loads(result.read_text(encoding="utf-8-sig"))["ok"], log)
        self.assertTrue(reopened.exists(), "Studio wasn't started again: " + log)
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "v2\n")
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), self.sha)

    def test_a_failed_install_leaves_no_half_made_git_copy(self):
        r = self.check()
        r["sha"] = "0" * 40                                      # a commit it doesn't have
        out = self.run_helper(r)
        self.assertFalse(out["ok"], out)
        self.assertFalse((self.copy / ".git").exists())
        self.assertEqual((self.copy / "app.py").read_text(encoding="utf-8"), "v1\n")


class UpdaterObjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._cfg = ss.CONFIG_DIR
        ss.CONFIG_DIR = Path(self.tmp.name) / "config"

    def tearDown(self):
        ss.CONFIG_DIR = self._cfg
        self.tmp.cleanup()

    def wait(self, u):
        import time
        end = time.time() + 10
        while u.property("busy") and time.time() < end:
            QCoreApplication.processEvents(); time.sleep(0.01)

    def test_source_is_saved_and_check_reports(self):
        u = U.Updater(repo=Path(self.tmp.name), run_check=lambda repo, url, br: {
            "state": "available", "message": "2 update(s) available.", "sha": "abc", "dirty": True, "deps": False,
            "commits": [{"sha": "abc", "date": "2026-09-30", "subject": "x"}]})
        u.setSource("https://github.com/me/StereoCrafter.git", "studio")
        self.assertEqual(ss.load("updater"), {"repo": "https://github.com/me/StereoCrafter.git", "branch": "studio"})
        u.check(); self.wait(u)
        self.assertEqual(u.property("state"), "available")
        self.assertEqual(len(u.property("commits")), 1)
        self.assertIn("aren't committed", u.property("blocked"))
        self.assertIn("aren't committed", u.install())     # refused: no helper, no quit

    def test_check_at_startup_is_off_by_default_and_runs_once(self):
        # user, 2026-10-05: off by default; on, one check per session, and only
        # an update found says anything
        calls = []
        def fake(repo, url, br):
            calls.append(br)
            return {"state": "available", "message": "2 update(s) available.", "sha": "abc", "dirty": False,
                    "deps": False, "commits": [{"sha": "a"}, {"sha": "b"}]}
        u = U.Updater(repo=Path(self.tmp.name), run_check=fake)
        found = []
        u.updateFound.connect(lambda n, br: found.append((n, br)))
        u.setSource("https://github.com/me/StereoCrafter.git", "studio")
        self.assertFalse(u.property("checkOnStart"))
        self.assertFalse(u.startupCheck())                  # off: nothing
        u.setCheckOnStart(True)
        self.assertTrue(ss.load("updater")["check_on_start"])
        self.assertTrue(U.Updater(repo=Path(self.tmp.name)).property("checkOnStart"))   # remembered
        self.assertTrue(u.startupCheck()); self.wait(u)
        QCoreApplication.processEvents()
        self.assertEqual(found, [(2, "studio")])
        self.assertFalse(u.startupCheck())                  # once a session
        u.check(); self.wait(u); QCoreApplication.processEvents()
        self.assertEqual(found, [(2, "studio")])            # a manual check: the dialog shows it, no toast
        self.assertEqual(len(calls), 2)

    def test_studios_repository_is_built_in(self):
        """Nobody types an address (user, 2026-10-18): Studio's repo and main, whatever branch the copy
        is on; a fork's address is kept, and clearing it goes back to Studio's."""
        calls = []
        u = U.Updater(repo=Path(self.tmp.name), run_check=lambda repo, url, branch: calls.append((url, branch))
                      or {"state": "uptodate", "message": "", "commits": []})
        self.assertEqual((u.property("repoUrl"), u.property("branch")), (U.STUDIO_REPO, "main"))
        u.setCheckOnStart(True)
        self.assertTrue(u.startupCheck()); self.wait(u)
        self.assertEqual(calls, [(U.STUDIO_REPO, "main")])
        u.setSource("https://github.com/me/fork.git", "dev")
        self.assertEqual((u.property("repoUrl"), u.property("branch")), ("https://github.com/me/fork.git", "dev"))
        u.setSource("", "dev")
        self.assertEqual(u.property("repoUrl"), U.STUDIO_REPO)
        u.setSource(U.STUDIO_REPO, "main")                  # typed out in full: still the built-in one
        self.assertEqual(ss.load("updater").get("repo"), "")

    def test_last_update_result_becomes_a_notice_once(self):
        ss.CONFIG_DIR.mkdir(parents=True)
        (ss.CONFIG_DIR / "update_result.json").write_text(json.dumps({"ok": True, "message": "Studio was updated."}))
        u = U.Updater(repo=Path(self.tmp.name))
        self.assertEqual(u.property("notice"), {"ok": True, "message": "Studio was updated."})
        self.assertFalse((ss.CONFIG_DIR / "update_result.json").exists())


if __name__ == "__main__":
    unittest.main()
