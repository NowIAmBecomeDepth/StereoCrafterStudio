"""The Depth Map Editor's Local Match (user, 2026-10-13, chap2_00097213): on a locked-off shot
DepthCrafter re-judged a hedge -- grey at the start, black from frame ~160 -- which no whole-frame
gain moves. Each frame's coarse depth is matched to a picked frame's, measured only where both are
smooth and close (an outline that moved is left out, so nothing gets a halo), filled in across the
frame; a frame where something close fills the picture is left alone. The reference frame's depth
rides on the settings object at run time, never in the project file."""

from __future__ import annotations

import copy
import sys
import unittest
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import numpy as np  # noqa: E402

from core.depthedit import edit_ops as E  # noqa: E402

H, W = 120, 200


def scene(figure_x=110, hedge=0.10):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    v = 0.20 + 0.25 * yy / H                       # ground rising toward the camera
    v[:, :50] = hedge                              # the hedge on the left, far
    v[20:100, figure_x:figure_x + 30] = 0.80       # a figure, near
    return v.astype(np.float32)


class LocalMatchTest(unittest.TestCase):
    def test_a_rejudged_area_comes_to_the_reference(self):
        ref, start = scene(hedge=0.10), scene(hedge=0.16)        # the hedge grey at the start
        out = E.local_match(start, ref)
        self.assertAlmostEqual(float(out[10:110, 5:40].mean()), 0.10, delta=0.01)
        self.assertLess(float(np.abs(out[:, 80:] - ref[:, 80:]).max()), 0.01)   # the rest: as it was

    def test_a_moved_outline_gets_no_halo(self):
        ref, moved = scene(figure_x=110), scene(figure_x=104)    # the figure shifted 6 px
        out = E.local_match(moved, ref)
        ring = np.zeros((H, W), bool)
        ring[10:110, 90:150] = True
        ring[20:100, 104:134] = False                            # around the figure, not on it
        self.assertLess(float(np.abs(out - moved)[ring].max()), 0.02)
        self.assertTrue(np.allclose(out[30:90, 108:132], 0.80, atol=0.02))       # the figure kept where it is

    def test_a_frame_filled_by_something_close_is_left_alone(self):
        ref, close = scene(), scene()
        close[:, 70:] = np.linspace(0.6, 1.0, W - 70)[None, :]   # a person right at the lens
        self.assertIs(E.local_match(close, ref).dtype, np.dtype(np.float32))
        self.assertTrue(np.array_equal(E.local_match(close, ref), close))

    def test_strength_goes_part_way(self):
        out = E.local_match(scene(hedge=0.16), scene(hedge=0.10), strength=0.5)
        self.assertAlmostEqual(float(out[10:110, 5:40].mean()), 0.13, delta=0.01)

    def test_a_larger_frame_takes_a_map_sized_reference(self):
        import cv2
        big = cv2.resize(scene(hedge=0.16), (W * 2, H * 2), interpolation=cv2.INTER_LINEAR)
        out = E.local_match(big, scene(hedge=0.10))              # Render at source resolution
        self.assertAlmostEqual(float(out[20:220, 10:80].mean()), 0.10, delta=0.015)


class SettingsTest(unittest.TestCase):
    def test_off_or_without_its_reference_it_does_nothing(self):
        v = scene(hedge=0.16)
        g = E.GlobalSettings(match_on=True, match_frame=5)
        self.assertTrue(g.is_identity())                          # no reference loaded yet
        E.set_match_reference(g, 4, scene())
        self.assertTrue(g.is_identity())                          # a reference of another frame
        E.set_match_reference(g, 5, scene())
        self.assertFalse(g.is_identity())
        self.assertAlmostEqual(float(E.apply_global(v, g)[10:110, 5:40].mean()), 0.10, delta=0.01)
        g.match_on = False
        self.assertTrue(g.is_identity())

    def test_the_reference_is_never_saved_but_render_copies_it(self):
        g = E.GlobalSettings(match_on=True, match_frame=2)
        E.set_match_reference(g, 2, scene())
        d = asdict(g)
        self.assertEqual((d["match_on"], d["match_frame"], d["match_strength"]), (True, 2, 1.0))
        self.assertFalse(any(k.startswith("_") for k in d))
        self.assertTrue(E.global_settings_from_dict(d).is_identity())   # reopened: loads it again first
        self.assertFalse(copy.deepcopy(g).is_identity())                # Render's copy keeps it


if __name__ == "__main__":
    unittest.main()
