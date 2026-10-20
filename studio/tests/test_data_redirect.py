"""Every unit test redirects the user's data before it imports the project
(bug sweep 2026-10-09 #16).

studio/tests/__init__.py points the settings folder and last_jobs.json at a
temp folder -- but only when the studio.tests package is imported. Run as a
script (`python studio/tests/test_x.py`) or discovered without `-t .`, a test
module is imported on its own; test_inpainting_v2's batch then recorded its
temp output in the user's real last_jobs.json. So each test_*.py imports
studio.tests itself, first.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_data_redirect -v
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

HERE = Path(__file__).resolve().parent
PROJECT = ("studio", "core", "depthcrafter", "dependency", "dependency_R", "personal")


def _names(node):
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module]
    return []


class RedirectFirstTest(unittest.TestCase):
    def test_every_test_module_imports_the_redirect_before_the_project(self):
        late = []
        for path in sorted(HERE.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:          # top level only: what runs at import
                names = _names(node)
                if "studio.tests" in names:
                    break
                if any(n.split(".")[0] in PROJECT for n in names):
                    late.append(f"{path.name}:{node.lineno} imports {names[0]} before studio.tests")
                    break
            else:
                late.append(f"{path.name}: never imports studio.tests")
        self.assertEqual(late, [], "add `import studio.tests` before the first project import")

    def test_a_test_run_as_a_script_records_in_the_temp_file(self):
        code = ("import runpy, sys; sys.argv = ['x']; "
                "g = runpy.run_path(sys.argv[1] if len(sys.argv) > 1 else r'%s', run_name='not_main'); "
                "import core.ui.job_handoff as jh, studio.settings_store as ss; "
                "print(jh._RECORD_PATH); print(ss.CONFIG_DIR)") % (HERE / "test_inpainting_v2.py")
        env = {k: v for k, v in os.environ.items() if not k.startswith("STEREOCRAFTER_TEST_")}
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        out = subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env, capture_output=True,
                             text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        record, config = out.stdout.strip().splitlines()[-2:]
        self.assertNotEqual(Path(record).resolve(), (REPO / "last_jobs.json").resolve())
        self.assertNotEqual(Path(config).resolve(), (REPO / "config").resolve())


if __name__ == "__main__":
    unittest.main()
