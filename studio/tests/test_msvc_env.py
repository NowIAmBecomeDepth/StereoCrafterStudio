"""Visual Studio's compiler for torch.compile, loaded by Studio itself (user, 2026-10-12): the .exe starts
pythonw directly, so Inductor's C++ parts never found cl.exe ("cl is not found") and ran uncompiled.
core.common.msvc_env runs the first vcvars64.bat in a hidden cmd and copies its environment in, once,
when V1's or V2's Torch Compile is set up. A stand-in vcvars64.bat puts a stand-in cl on the PATH here."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
from core.common import msvc_env as M  # noqa: E402

WINDOWS = sys.platform == "win32"


def fresh():
    M._tried, M._result = False, None


@unittest.skipUnless(WINDOWS, "vcvars64.bat is Windows only")
class LoadTest(unittest.TestCase):
    def setUp(self):
        fresh()
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "msvc bin"                      # a space, as in "Program Files"
        self.bin.mkdir()
        (self.bin / "cl.bat").write_text("@echo off\r\n", encoding="ascii")
        self.bat = self.tmp / "vcvars64.bat"
        self.bat.write_text(f'@echo off\r\nset "PATH={self.bin};%PATH%"\r\nset "INCLUDE=C:\\fake\\include"\r\n'
                            "echo this goes to nul\r\n", encoding="ascii")
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        self.env = mock.patch.dict(os.environ, {"PATH": system32, "INCLUDE": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        fresh()

    def test_loads_vcvars_and_finds_cl(self):
        with mock.patch.object(M, "vcvars_candidates", return_value=[str(self.tmp / "missing.bat"), str(self.bat)]):
            cl = M.load_msvc_env()
        self.assertIsNotNone(cl)
        self.assertEqual(Path(cl).parent, self.bin)
        self.assertEqual(os.environ["INCLUDE"], r"C:\fake\include")
        self.assertIn(str(self.bin), os.environ["PATH"])

    def test_runs_once(self):
        with mock.patch.object(M, "vcvars_candidates", return_value=[str(self.bat)]), \
                mock.patch.object(M.subprocess, "run", wraps=M.subprocess.run) as run:
            M.load_msvc_env()
            os.environ["PATH"] = os.environ["PATH"].replace(str(self.bin), "")   # as if it fell off again
            M.load_msvc_env()
        self.assertEqual(run.call_count, 1)

    def test_no_visual_studio_changes_nothing(self):
        with mock.patch.object(M, "vcvars_candidates", return_value=[str(self.tmp / "missing.bat")]):
            self.assertIsNone(M.load_msvc_env())
        self.assertEqual(os.environ["INCLUDE"], "")

    def test_cl_already_there_runs_nothing(self):
        os.environ["PATH"] = f"{self.bin};{os.environ['PATH']}"
        with mock.patch.object(M.subprocess, "run") as run:
            self.assertIsNotNone(M.load_msvc_env())
        run.assert_not_called()


class ParseTest(unittest.TestCase):
    def test_set_lines(self):
        env = M.parse_set_output("PATH=C:\\a;C:\\b\nINCLUDE=x=y\n=C:=C:\\\nnot a pair\n")
        self.assertEqual(env, {"PATH": "C:\\a;C:\\b", "INCLUDE": "x=y"})

    def test_2022_before_18(self):
        c = M.vcvars_candidates()
        self.assertLess(min(i for i, p in enumerate(c) if "\\2022\\" in p),
                        min(i for i, p in enumerate(c) if "\\18\\" in p))

    def test_vswhere_installs_come_after_the_usual_paths(self):
        """A custom drive or a Preview: found through vswhere (bug sweep 2026-10-14c)."""
        with mock.patch.object(M, "_vswhere_installs", return_value=[r"D:\VS\Preview"]):
            c = M.vcvars_candidates()
        self.assertEqual(c[-1], r"D:\VS\Preview\VC\Auxiliary\Build\vcvars64.bat")
        self.assertIn("\\2022\\", c[0])


class CompileWithoutCompilerTest(unittest.TestCase):
    def test_no_compiler_is_a_warning_not_enabled(self):
        from pipelines import stereo_video_inpainting as P
        pipe = SimpleNamespace(unet=mock.Mock(), vae=SimpleNamespace(decoder=mock.Mock()))
        with mock.patch.object(M, "load_msvc_env", return_value=None), \
                self.assertLogs(P.logger, "WARNING") as logs:
            P._enable_torch_compile(pipe)
        self.assertTrue(any("WITHOUT its C++ parts" in m for m in logs.output), logs.output)


class CompileSetupCallsItTest(unittest.TestCase):
    """V1's and V2's Torch Compile set-up load the compiler first."""

    def test_v1(self):
        from pipelines import stereo_video_inpainting as P
        pipe = SimpleNamespace(unet=mock.Mock(), vae=SimpleNamespace(decoder=mock.Mock()))
        with mock.patch.object(M, "load_msvc_env") as load:
            P._enable_torch_compile(pipe)
        load.assert_called_once()
        pipe.unet.compile.assert_called_once()

    def test_v2(self):
        from studio.stages.inpainting_v2 import engine as E
        with mock.patch.object(M, "load_msvc_env") as load:
            E._setup_compile()
        load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
