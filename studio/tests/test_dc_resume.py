"""DepthCrafter's Resume (user, 2026-10-25: "add a resume checkbox like the other stages so any stage can
turn off using finished folders"): on by default -- a folder run moves each finished source into finished/
as before; off, nothing is moved. Never for a single clip or a Test Render.

    python -m unittest studio.tests.test_dc_resume
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.depthcrafter import settings as S  # noqa: E402


class DcResumeTest(unittest.TestCase):
    def test_on_by_default_as_before(self):
        opts = S.snapshot({})
        self.assertIs(opts["move_to_finished"], True)
        self.assertTrue(S.moves_finished(opts, single=False, test=False))

    def test_off_nothing_moves(self):
        opts = S.snapshot({"move_to_finished": False})
        self.assertIs(opts["move_to_finished"], False)
        self.assertFalse(S.moves_finished(opts, single=False, test=False))

    def test_never_for_a_single_clip_or_a_test_render(self):
        opts = S.snapshot({})
        self.assertFalse(S.moves_finished(opts, single=True, test=False))
        self.assertFalse(S.moves_finished(opts, single=False, test=True))

    def test_the_controller_asks_it(self):
        src = (Path(S.__file__).with_name("controller.py")).read_text(encoding="utf-8")
        self.assertIn("move_originals=S.moves_finished(opts, single, test)", src)


if __name__ == "__main__":
    unittest.main()
