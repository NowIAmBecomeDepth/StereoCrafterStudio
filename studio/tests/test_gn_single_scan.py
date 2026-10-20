"""Global Normalisation decodes each depth map once, not twice (bug sweep
2026-10-09, A4): compute_depth_normalization's "max content" scan ran
unconditionally, and with GN on its number was the second scan's max anyway.
The range found is the same; without GN the first scan still runs.

    python -m unittest studio.tests.test_gn_single_scan
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

CHECKS = Path(__file__).resolve().parent / "checks"


class SingleScanTest(unittest.TestCase):
    def run_norm(self, gn):
        sys.path.insert(0, str(CHECKS))
        import _fixtures
        from studio.legacy import install
        install()
        import core.splatting.batch_processing as B
        depth = _fixtures.path("small") / "depth" / "clipA_depth.mp4"
        cls = next(c for c in vars(B).values() if isinstance(c, type) and hasattr(c, "compute_depth_normalization"))
        calls = []
        real = B.compute_global_depth_stats

        def counted(**kw):
            calls.append(1)
            return real(**kw)
        with mock.patch.object(B, "compute_global_depth_stats", counted):
            out = cls.compute_depth_normalization(SimpleNamespace(logger=logging.getLogger("t")), str(depth), gn,
                                                  48, 16, 640, 360)
        return out, len(calls)

    def test_gn_scans_once_and_finds_the_range(self):
        (raw, lo, hi, peak), n = self.run_norm(True)
        self.assertEqual(n, 1)
        self.assertFalse(raw)
        self.assertLess(lo, hi)
        self.assertEqual(peak, hi, "the max content value is the scan's max")

    def test_without_gn_the_content_scan_still_runs(self):
        (raw, lo, hi, peak), n = self.run_norm(False)
        self.assertEqual(n, 1)
        self.assertTrue(raw)
        self.assertEqual(hi, 255.0)            # an 8-bit map: its codes go to 255


if __name__ == "__main__":
    unittest.main()
