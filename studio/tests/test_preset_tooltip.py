"""The 8-bit / 10-bit preset tooltip (user, 2026-10-25): it sent you to the Prototype's File > Save as ...
Preset, which Studio's Encoding Settings doesn't have, said its ending twice, called 10-bit slower, and
its 8-bit and 10-bit advice read as contradicting each other. Splatting's buttons had none.

    python -m unittest studio.tests.test_preset_tooltip
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.merging.controller import MergingController  # noqa: E402
from studio.stages.splatting.controller import SplattingController  # noqa: E402


class PresetTooltipTest(unittest.TestCase):
    def tip(self, ctl):
        prop = getattr(ctl, "presetTooltip", None)
        self.assertIsNotNone(prop, f"{ctl.__name__} has no presetTooltip")
        return prop.fget(None)

    def test_merging_says_it_once_and_right(self):
        tip = self.tip(MergingController)
        self.assertNotIn("File >", tip)
        self.assertNotIn("slower", tip)
        self.assertNotIn("adds no quality", tip)
        self.assertEqual(tip.count("CRF"), 1, "what a preset does: said once, not after each preset")
        self.assertEqual(tip.count("no new banding"), 1)
        self.assertIn("DNxHR", tip)
        self.assertIn("very large", tip)
        self.assertTrue(tip.rstrip().endswith("neither button is lit."), tip[-120:])

    def test_splatting_shows_the_same(self):
        self.assertEqual(self.tip(SplattingController), self.tip(MergingController))


if __name__ == "__main__":
    unittest.main()
