"""Merging's check that every clip that needs its original has one (core.common.merge_preflight;
user, 2026-10-07). Temp folders with empty stand-in files.

    python -m unittest studio.tests.test_merge_preflight
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import merge_preflight as P  # noqa: E402


class Preflight(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for d in ("inp", "mask", "orig"):
            (self.root / d).mkdir()

    def touch(self, *parts):
        p = self.root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def missing(self, videos, **kw):
        return P.missing_originals(videos, str(self.root / "mask"), str(self.root / "orig"), **kw)

    def test_single_eye_needs_its_original(self):
        v = self.touch("inp", "shot_1920_inpainted_right_eye.mp4")
        self.touch("mask", "shot_1408_splatted2.mp4")
        self.assertEqual(self.missing([v]), ["shot"])
        self.touch("orig", "shot.mov")                               # any video extension
        self.assertEqual(self.missing([v]), [])

    def test_split_pair_needs_it_too(self):
        v = self.touch("inp", "shot_1920_inpainted_right_eye.mp4")
        self.touch("mask", "splat", "shot_1920_splat.mov")
        self.assertEqual(self.missing([v]), ["shot"])

    def test_quad_and_dual_eye_do_not(self):
        q = self.touch("inp", "quad_1920_inpainted_right_eye.mp4")
        self.touch("mask", "quad_1408_splatted4.mp4")
        d = self.touch("inp", "dual_1920_inpainted_dual_sbs.mp4")
        self.assertEqual(self.missing([q, d]), [])

    def test_no_splat_at_all_is_left_to_the_batch(self):
        v = self.touch("inp", "lonely_1920_inpainted_right_eye.mp4")
        self.assertEqual(self.missing([v]), [])

    def test_explicit_files(self):
        v = self.touch("inp", "shot_1920_inpainted_right_eye.mp4")
        quad = self.touch("mask", "shot_1408_splatted4.mp4")
        self.assertEqual(self.missing([v], explicit_splatted_file=quad), [])
        two = self.touch("mask", "x", "shot_1408_splatted2.mp4")
        self.assertEqual(self.missing([v], explicit_splatted_file=two), ["shot"])
        orig = self.touch("elsewhere", "whatever.mp4")
        self.assertEqual(self.missing([v], explicit_original_file=orig, explicit_splatted_file=two), [])

    def test_the_question_names_the_clips_and_what_continue_does(self):
        q = P.question(["shotA", "shotB"], "D:/orig")
        self.assertIn("2 clip(s): shotA, shotB", q)
        self.assertIn("D:/orig", q)
        self.assertIn("right eye only", q)
        self.assertIn("that clip", P.question(["one"], "D:/orig"))

    def test_folder_missing_and_message(self):
        v = self.touch("inp", "shot_1920_inpainted_right_eye.mp4")
        self.touch("mask", "shot_1408_splatted2.mp4")
        self.touch("orig", "shot.mp4")
        self.assertEqual(P.missing_originals([v], str(self.root / "mask"), str(self.root / "nowhere")), ["shot"])
        msg = P.describe(["a", "b", "c", "d", "e", "f", "g"], "X:/orig")
        self.assertIn("7 clip(s)", msg)
        self.assertIn("and 2 more", msg)
        self.assertIn("X:/orig", msg)


if __name__ == "__main__":
    unittest.main()
