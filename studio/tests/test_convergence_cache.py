"""The Splatting preview's per-clip caches (core.splatting.convergence_cache)
follow the file, not just its path (bug sweep 2026-10-09, A4): a depth map
replaced under the same name -- the Depth Map Editor's Use, a DepthCrafter
re-run -- kept its old Global Normalisation range, Auto-Converge values and
Total(D+P) estimate for the session. A stopped or failed scan's (0, 1) isn't
stored as the clip's range.

    python -m unittest studio.tests.test_convergence_cache
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.analysis_service import AnalysisService  # noqa: E402
from core.splatting.convergence_cache import ConvergenceCache  # noqa: E402


class StampTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = os.path.join(d.name, "clip_depth.mp4")
        Path(self.path).write_bytes(b"first map")

    def replace(self):
        time.sleep(0.02)
        Path(self.path).write_bytes(b"the edited map, longer")

    def test_clip_norm_drops_with_a_replaced_file(self):
        c = ConvergenceCache()
        c.store_clip_norm(self.path, 0.1, 0.9)
        self.assertEqual(c.get_clip_norm(self.path), (0.1, 0.9))
        self.replace()
        self.assertIsNone(c.get_clip_norm(self.path))

    def test_auto_conv_goes_stale_with_a_replaced_file(self):
        c = ConvergenceCache()
        c.auto_conv_path = self.path
        self.assertFalse(c.is_auto_conv_stale(self.path))
        self.replace()
        self.assertTrue(c.is_auto_conv_stale(self.path))
        self.assertTrue(c.is_auto_conv_stale("other.mp4"))

    def test_the_dp_estimate_key_follows_the_file(self):
        k0 = AnalysisService.make_dp_cache_key(self.path, 20.0, 1.0)
        self.assertEqual(k0, AnalysisService.make_dp_cache_key(self.path, 20.0, 1.0))
        self.replace()
        self.assertNotEqual(k0, AnalysisService.make_dp_cache_key(self.path, 20.0, 1.0))

    def test_a_stopped_scan_isnt_stored(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import preview_engine as PE
        cls = next(c for c in vars(PE).values() if isinstance(c, type) and hasattr(c, "_compute_clip_global_depth_stats"))
        src = inspect.getsource(cls._compute_clip_global_depth_stats)
        self.assertIn("!= (0.0, 1.0)", src)
        self.assertIn("stop_event.is_set()", src)


if __name__ == "__main__":
    unittest.main()
