"""No console window pops up while DepthCrafter works (a user, 2026-10-24: "when the output is merged a
command window pops up and takes focus away from whatever else you may be using"). Studio runs
windowless (pythonw), so a console program it starts -- ffmpeg, ffprobe, nvidia-smi -- opens a window of
its own unless told not to (CREATE_NO_WINDOW). DepthCrafter's code and core/common start none without
it; mediapy, which writes the depth map and starts ffmpeg itself, gets the flag through its own
`subprocess` name (depthcrafter.utils._hide_mediapy_windows).

    python -m unittest studio.tests.test_no_console_windows
"""

from __future__ import annotations

import ast
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

CHECKED = ("depthcrafter", "core/common", "studio/stages/depthcrafter")
CALLS = ("run", "Popen", "call", "check_output", "check_call")


def unhidden_calls():
    """subprocess.<call>(...) with no creationflags / startupinfo, as file:line."""
    out = []
    for folder in CHECKED:
        for p in sorted((REPO / folder).rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in CALLS
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
                        and not any(k.arg in ("creationflags", "startupinfo") or k.arg is None for k in n.keywords)):
                    out.append(f"{p.relative_to(REPO).as_posix()}:{n.lineno}")
    return out


class NoWindowTest(unittest.TestCase):
    def test_depthcrafter_and_core_start_nothing_with_a_window(self):
        self.assertEqual(unhidden_calls(), [])

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_mediapy_starts_ffmpeg_hidden(self):
        import mediapy
        import depthcrafter.utils  # noqa: F401  -- its import hides mediapy's windows
        self.assertTrue(getattr(mediapy.subprocess, "_studio_no_window", False),
                        "mediapy still starts ffmpeg with the real subprocess (a window each time)")
        self.assertIsNot(mediapy.subprocess, subprocess, "the whole subprocess module was changed, not mediapy's")

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_the_flag_is_added_and_others_kept(self):
        from depthcrafter.utils import _hide_mediapy_windows
        seen = []
        fake_sub = types.SimpleNamespace(Popen=lambda *a, **k: seen.append(("Popen", k)),
                                         run=lambda *a, **k: seen.append(("run", k)), PIPE=-1)
        lib = types.SimpleNamespace(subprocess=types.ModuleType("subprocess"))
        lib.subprocess.__dict__.update(vars(fake_sub))
        _hide_mediapy_windows(lib)
        lib.subprocess.Popen(["ffmpeg"], stdin=-1)
        lib.subprocess.run(["ffprobe"], creationflags=0x200)
        no_window = subprocess.CREATE_NO_WINDOW
        self.assertEqual(seen[0], ("Popen", {"stdin": -1, "creationflags": no_window}))
        self.assertEqual(seen[1], ("run", {"creationflags": 0x200 | no_window}))
        self.assertEqual(lib.subprocess.PIPE, -1, "the rest of subprocess is as it was")
        _hide_mediapy_windows(lib)                      # twice: wrapped once
        lib.subprocess.Popen(["ffmpeg"])
        self.assertEqual(seen[2], ("Popen", {"creationflags": no_window}))


if __name__ == "__main__":
    unittest.main()
