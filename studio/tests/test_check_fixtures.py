"""The checks' fixture cache (audit 2026-09-28 #30). run_all pre-built only
"small" and "hd", so Merging checks running side by side each found "merge"
missing and built it over one another -- path() rmtree'd a half-built folder
another check was still filling. Now run_all builds every kind first, and
path() builds into a private folder and renames it into place, so two
builders can't collide even outside run_all. A stand-in builder, a temp cache."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "studio" / "tests" / "checks"))
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import _fixtures  # noqa: E402


class FixtureCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = (_fixtures.CACHE, dict(_fixtures._BUILDERS))
        self.addCleanup(lambda: (setattr(_fixtures, "CACHE", saved[0]),
                                 _fixtures._BUILDERS.clear(), _fixtures._BUILDERS.update(saved[1])))
        _fixtures.CACHE = Path(tmp.name)

    def test_two_builders_at_once_both_get_a_whole_fixture(self):
        def slow(root: Path) -> None:       # writes its files over half a second
            for i in range(5):
                (root / "src" / f"clip{i}.mp4").write_bytes(b"x" * 1000)
                time.sleep(0.1)
        _fixtures._BUILDERS["race"] = slow
        seen, errors = [], []

        def use():
            try:
                root = _fixtures.path("race")
                time.sleep(0.3)             # the check reads it a moment later
                seen.append(sorted(p.name for p in (root / "src").iterdir()))
            except Exception as e:          # noqa: BLE001
                errors.append(repr(e))
        threads = [threading.Thread(target=use) for _ in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.05)
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(seen, [[f"clip{i}.mp4" for i in range(5)]] * 3, "a check saw a half-built fixture")

    def test_run_all_prebuilds_every_kind(self):
        src = (REPO / "studio" / "tests" / "checks" / "run_all.py").read_text(encoding="utf-8")
        # every shared kind (batch 8: the SAM 3 clips are built by their own checks, which run alone)
        self.assertTrue("for kind in _fixtures.PREBUILT" in src, "run_all doesn't pre-build PREBUILT")
        shared = {k for k in _fixtures._BUILDERS if not k.startswith("sam3_") and k != "race"}
        self.assertEqual(shared - set(_fixtures.PREBUILT), set(), "a shared kind isn't pre-built")


if __name__ == "__main__":
    unittest.main()
