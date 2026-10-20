"""Auto-Converge's fallback (bug sweep 2026-10-09, A4): when the scan failed
(a clip decord can't read, an OOM in the model) the error path returned two
values to a caller that unpacks four, so the preview said "not enough values
to unpack" instead of using the fallback."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import torch  # noqa: E402,F401  -- before decord (core.splatting imports it): the other order segfaults


class FallbackTests(unittest.TestCase):
    def test_a_failed_scan_returns_the_fallback_as_four_values(self):
        from core.splatting.convergence import ConvergenceEstimatorWrapper
        w = ConvergenceEstimatorWrapper.__new__(ConvergenceEstimatorWrapper)   # no model load
        import logging
        w.logger = logging.getLogger("test.convergence")
        w._estimator = types.SimpleNamespace(model=object())                   # "loaded"
        with self.assertLogs("test.convergence", level="ERROR"):
            got = w.estimate_convergence("no_such_clip.mp4", "no_such_depth.mp4", fallback_value=0.42)
        avg, peak, edge_l, edge_r = got                                        # what the preview unpacks
        self.assertEqual((avg, peak, edge_l, edge_r), (0.42, 0.42, None, None))


if __name__ == "__main__":
    unittest.main()
