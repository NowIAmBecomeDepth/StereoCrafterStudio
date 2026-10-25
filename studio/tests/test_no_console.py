"""No console windows from a windowless Studio (a user, 2026-10-24: "a ton of console windows appearing
and closing when torch compile is warming up", for half an hour, each taking the focus). studio.no_console
hides every console program Studio starts while it has no console of its own -- torch's and Triton's
compiler calls included, which pass no flag of their own.

Proved with a real windowless parent (pythonw) that starts a console program through
subprocess.check_output, as torch and Triton do: the child gets no window. On the old code it gets one.

    python -m unittest studio.tests.test_no_console
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import no_console as NC  # noqa: E402

@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowlessStudioTest(unittest.TestCase):
    def run_windowless(self, install: bool) -> list:
        """A pythonw 'Studio' starts console programs as torch and Triton do (check_output, check_call,
        no flag of their own); the creation flags each reached Windows' CreateProcess with."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "flags.txt"
            script = Path(d) / "studio.py"
            py = str(Path(sys.executable).with_name("python.exe"))
            script.write_text(
                "import _winapi, subprocess, sys\n"
                f"sys.path.insert(0, {str(REPO)!r})\n"
                + ("from studio import no_console\nno_console.install()\n" if install else "")
                + "seen = []\n"
                "real = _winapi.CreateProcess\n"
                "def spy(*a):\n"
                "    seen.append(a[5])\n"
                "    return real(*a)\n"
                "_winapi.CreateProcess = spy\n"
                f"subprocess.check_output([{py!r}, '-c', 'pass'])\n"        # Inductor's cpp_builder
                f"subprocess.check_call([{py!r}, '-c', 'pass'])\n"          # Triton's build
                f"open({str(out)!r}, 'w').write(' '.join(map(str, seen)))\n", encoding="utf-8")
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            subprocess.run([str(pythonw), str(script)], timeout=60)
            return [int(x) for x in out.read_text().split()] if out.exists() else []

    def test_a_console_program_started_by_windowless_studio_gets_no_window(self):
        flags = self.run_windowless(install=True)
        self.assertEqual(len(flags), 2, flags)
        self.assertTrue(all(f & NC.CREATE_NO_WINDOW for f in flags),
                        f"started with a window of its own: creation flags {flags}")

    def test_with_a_console_nothing_changes(self):
        # this test runs in a console: children share it, as before
        if NC.has_console():
            self.assertFalse(NC.install())


class FlagsTest(unittest.TestCase):
    def test_hidden_unless_a_console_is_asked_for(self):
        self.assertEqual(NC.hidden(0), NC.CREATE_NO_WINDOW)
        self.assertEqual(NC.hidden(0x200), 0x200 | NC.CREATE_NO_WINDOW)        # others kept
        self.assertEqual(NC.hidden(NC.CREATE_NEW_CONSOLE), NC.CREATE_NEW_CONSOLE)
        self.assertEqual(NC.hidden(NC.DETACHED_PROCESS), NC.DETACHED_PROCESS)
        self.assertEqual(NC.hidden(None), NC.CREATE_NO_WINDOW)

    def test_the_hook_sees_keyword_and_positional_flags(self):
        seen = []

        class Probe:
            def __init__(self, *args, **kwargs):
                seen.append((args, kwargs))
        real = subprocess.Popen.__init__
        subprocess.Popen.__init__ = Probe.__init__
        installed = NC._installed
        try:
            NC._installed = False
            self.assertTrue(NC.install(force=True))
            subprocess.Popen.__init__(object(), ["x"], creationflags=0x200)
            subprocess.Popen.__init__(object(), ["x"], *([None] * 12), 0x10)       # CREATE_NEW_CONSOLE, positional
        finally:
            subprocess.Popen.__init__ = real
            NC._installed = installed
        self.assertEqual(seen[0][1]["creationflags"], 0x200 | NC.CREATE_NO_WINDOW)
        self.assertEqual(seen[1][0][NC._FLAGS_AT], 0x10)


if __name__ == "__main__":
    unittest.main()
