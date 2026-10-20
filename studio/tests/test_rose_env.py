"""rose_env.setup carries on where a stopped or failed Set Up left off (bug
sweep 2026-10-09 #12) -- git and uv faked, a temp folder for weights/ROSE;
nothing is downloaded and the real weights/ROSE is never touched.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_rose_env -v
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.removal import rose_env as R


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rose = self.tmp / "weights" / "ROSE"
        self.cmds = []
        self.head = None
        for name, value in (("ROSE_DIR", self.rose), ("CLONE_MARKER", self.rose / ".studio_clone_ok"),
                            ("_run", self.fake_run), ("_head", lambda git: self.head),
                            ("missing", lambda: [])):
            p = mock.patch.object(R, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(R.shutil, "which", lambda n: n)
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, cmd, log, stop=None, cwd=None):
        cmd = [str(c) for c in cmd]
        self.cmds.append(cmd)
        if cmd[:2] == ["git", "clone"]:          # the repository, with its own models/ and weights/
            side = Path(cmd[3])
            for rel in (".git/HEAD", "rose/pipeline/__init__.py", "requirements.txt",
                        "weights/put weights here.txt", "models/put base models here.txt"):
                (side / rel).parent.mkdir(parents=True, exist_ok=True)
                (side / rel).write_text("repo")
        elif cmd[:2] == ["uv", "venv"]:
            R.python().parent.mkdir(parents=True, exist_ok=True)
            R.python().write_text("python")

    def kinds(self):
        return [" ".join(c[:3]) for c in self.cmds]

    def test_a_stopped_package_install_is_run_again(self):
        # the clone is done and python.exe exists, but the torch download was stopped
        self.rose.mkdir(parents=True)
        R.CLONE_MARKER.write_text(R.ROSE_COMMIT)
        R.python().parent.mkdir(parents=True)
        R.python().write_text("python")
        R.setup(lambda line: None)
        self.assertEqual(self.kinds()[:2], ["uv pip install", "uv pip install"])
        self.assertEqual(len(self.cmds), 3)                  # then the weights

    def test_a_cut_short_clone_is_redone_and_the_downloads_kept(self):
        (self.rose / "rose").mkdir(parents=True)                       # the clone stopped here
        weights = self.rose / "weights" / "transformer" / "diffusion_pytorch_model.safetensors"
        weights.parent.mkdir(parents=True)
        weights.write_text("3 GB")
        R.setup(lambda line: None)
        self.assertEqual(self.kinds()[:3], ["git clone " + R.GIT_URL, f"git -C {self.rose}.clone", "uv venv --python"])
        self.assertTrue((self.rose / "rose" / "pipeline" / "__init__.py").is_file())
        self.assertTrue((self.rose / "weights" / "put weights here.txt").is_file())
        self.assertEqual(weights.read_text(), "3 GB")
        self.assertEqual(R.CLONE_MARKER.read_text(), R.ROSE_COMMIT)
        self.assertFalse(Path(f"{self.rose}.clone").exists())

    def test_an_install_from_before_the_marker_is_not_cloned_again(self):
        (self.rose / ".git").mkdir(parents=True)
        self.head = R.ROSE_COMMIT
        R.setup(lambda line: None)
        self.assertNotIn("git clone", " | ".join(self.kinds()))
        self.assertEqual(R.CLONE_MARKER.read_text(), R.ROSE_COMMIT)


if __name__ == "__main__":
    unittest.main()
