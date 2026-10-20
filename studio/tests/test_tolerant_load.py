"""One malformed entry no longer empties a saved list (bug sweep 2026-10-09
A6 / A11): the keyframe track and the Dilate regions loaded all or nothing,
the preview kept the empty set, and the next autosave deleted every other
entry from the sidecar. Now the bad entry is skipped and the rest kept.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_tolerant_load -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from core.common.dilate_regions import DilateRegions  # noqa: E402
from core.common.keyframes import KeyframeTrack  # noqa: E402

SQUARE = [[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]]


class KeyframeTrackTests(unittest.TestCase):
    def test_a_bad_keyframe_is_skipped_and_the_rest_kept(self):
        track = KeyframeTrack.from_dict({"keyframes": [
            {"frame": 0, "type": "static", "values": {"max_disp": 20.0}},
            {"frame": "twelve", "type": "static", "values": {"max_disp": 30.0}},     # hand-edited
            {"frame": 40, "type": "dynamic", "values": {"max_disp": 25.0}},
        ]})
        self.assertEqual([e["frame"] for e in track.to_list()], [0, 40])

    def test_not_a_list_is_empty(self):
        self.assertEqual(KeyframeTrack.from_dict({"keyframes": "junk"}).to_list(), [])
        self.assertEqual(KeyframeTrack.from_dict("junk").to_list(), [])


class DilateRegionsTests(unittest.TestCase):
    def test_a_bad_keyframe_or_region_is_skipped_and_the_rest_kept(self):
        d = DilateRegions.from_dict({"version": 1, "enabled": True, "keyframes": [
            {"frame": 0, "regions": [
                {"polys": SQUARE, "offset_x": 2.0},
                {"polys": SQUARE, "offset_x": "lots"},                                 # a bad region
            ]},
            {"frame": "x", "regions": []},                                              # a bad keyframe
            {"frame": 30, "regions": [{"polys": SQUARE, "offset_y": -1.0}]},
        ]})
        self.assertEqual([k.frame for k in d.keyframes], [0, 30])
        self.assertEqual([len(k.regions) for k in d.keyframes], [1, 1])
        self.assertEqual(d.keyframes[0].regions[0].offset_x, 2.0)


if __name__ == "__main__":
    unittest.main()
