"""Shapes in the Depth Map Editor (user, 2026-10-17: "simple polygon shapes for those locked off shots that don't
need to be tracked" -- "one option should be a point connector for custom shapes"; core.depthedit.shapes):
a layer drawn as shapes holds their mask on every frame, cuts work, and the project keeps the shapes (not a
mask per frame) and draws them again on load. Arrays and a temp folder only."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import shapes as SH  # noqa: E402
from core.depthedit.project import Project, masks_path_for  # noqa: E402

TRI = {"kind": "Points", "pts": [[0.1, 0.1], [0.5, 0.1], [0.1, 0.5]]}


class MoveEdgeTest(unittest.TestCase):
    """One edge dragged (user, 2026-10-25): "drag a line and it extends only that one line of a shape.
    vertical lines can be dragged horizontally and horizontal lines dragged vertically"."""

    L = {"kind": "Points", "pts": [[.1, .1], [.5, .1], [.5, .2], [.2, .2], [.2, .6], [.1, .6]]}   # an L
    BOX = {"kind": "Rectangle", "pts": [[.6, .5], [.2, .1]]}

    def test_an_l_arm_extends_and_nothing_else_moves(self):
        out = SH.move_edge(self.L, 1, "x", .2)                      # the top arm's end, out to the right
        self.assertEqual(out["pts"], [[.1, .1], [.7, .1], [.7, .2], [.2, .2], [.2, .6], [.1, .6]])
        out = SH.move_edge(self.L, 4, "y", .1)                      # the upright arm's foot, down
        self.assertEqual(out["pts"][4:], [[.2, .7], [.1, .7]])
        self.assertEqual(out["pts"][:4], self.L["pts"][:4])

    def test_a_rectangle_moves_one_side(self):
        self.assertEqual(SH.edges(self.BOX)[0], [[.2, .1], [.6, .1]], "top first, whatever corners it was drawn by")
        self.assertEqual(SH.move_edge(self.BOX, 1, "x", .1)["pts"], [[.2, .1], [.7, .5]])        # right, out
        self.assertEqual(SH.move_edge(self.BOX, 0, "y", -.05)["pts"], [[.2, .05], [.6, .5]])     # top, up

    def test_an_edge_never_slides_along_itself_and_stays_in_the_frame(self):
        self.assertEqual(SH.move_edge(self.BOX, 0, "x", .3)["pts"], [[.2, .1], [.6, .5]])
        self.assertEqual(SH.move_edge(self.BOX, 1, "x", 5)["pts"], [[.2, .1], [1.0, .5]])
        self.assertEqual(SH.move_edge(self.L, 0, "y", -1)["pts"][:2], [[.1, 0.0], [.5, 0.0]])

    def test_ellipses_have_no_edges(self):
        ell = {"kind": "Ellipse", "pts": [[.1, .1], [.4, .4]]}
        self.assertEqual(SH.edges(ell), [])
        self.assertEqual(SH.move_edge(ell, 0, "x", .1)["pts"], ell["pts"])


class DrawTest(unittest.TestCase):
    def test_a_point_connector_fills_its_outline(self):
        m = SH.draw([TRI], 100, 200)
        self.assertTrue(m[20, 30])                 # inside the triangle
        self.assertFalse(m[45, 90])                # past its long side
        self.assertFalse(m[80, 150])

    def test_rectangle_and_ellipse(self):
        m = SH.draw([{"kind": "Rectangle", "pts": [[0.5, 0.5], [0.25, 0.2]]}], 100, 100)   # corners either way
        self.assertEqual(int(m.sum()), 25 * 30)
        e = SH.draw([{"kind": "Ellipse", "pts": [[0.2, 0.2], [0.8, 0.8]]}], 100, 100)
        self.assertTrue(e[50, 50])
        self.assertFalse(e[21, 21])                # the box's corner is outside the ellipse
        self.assertAlmostEqual(int(e.sum()) / (3.14159 * 30 * 30), 1.0, delta=0.05)

    def test_a_cut_takes_its_area_out(self):
        big = {"kind": "Rectangle", "pts": [[0.0, 0.0], [1.0, 1.0]]}
        hole = {"kind": "Rectangle", "pts": [[0.4, 0.4], [0.6, 0.6]], "cut": True}
        m = SH.draw([big, hole], 100, 100)
        self.assertFalse(m[50, 50])
        self.assertTrue(m[10, 10])

    def test_bad_shapes_are_dropped(self):
        self.assertEqual(SH.clean([{"kind": "Points", "pts": [[0, 0], [1, 1]]},           # two points
                                   {"kind": "Rectangle", "pts": [[0.3, 0.3], [0.3, 0.9]]},  # no width
                                   {"kind": "Star", "pts": [[0, 0], [1, 1], [0, 1]]},
                                   "nonsense"]), [])
        self.assertEqual(SH.clean([{"kind": "Points", "pts": [[-1, 0.5], [2, 0.5], [0.5, 1.5]]}])[0]["pts"],
                         [[0.0, 0.5], [1.0, 0.5], [0.5, 1.0]])


class LayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def project(self):
        p = Project()
        tracked = p.new_layer(60, 80)
        tracked.masks.set(3, np.ones((60, 80), bool))     # a SAM layer's mask on one frame
        drawn = p.new_layer(60, 80)
        drawn.shapes = SH.clean([TRI])
        SH.fill(drawn, 40)
        return p, tracked, drawn

    def test_on_every_frame_nothing_tracked(self):
        _p, _t, drawn = self.project()
        self.assertEqual(drawn.masks.frames(), list(range(40)))
        np.testing.assert_array_equal(drawn.masks.get(0), drawn.masks.get(39))
        self.assertEqual(len({id(a) for a in drawn.masks._frames.values()}), 1, "one packed copy, shared")
        self.assertTrue(drawn.tracked)
        self.assertFalse(drawn.has_prompts())

    def test_saved_as_shapes_and_drawn_again_on_load(self):
        p, tracked, drawn = self.project()
        path = str(self.tmp / "edit.json")
        p.save(path)
        with np.load(masks_path_for(path)) as blobs:
            self.assertIn(f"{tracked.uid}__frames", blobs)
            self.assertNotIn(f"{drawn.uid}__frames", blobs, "a shape layer's masks were saved frame by frame")
        back = Project.load(path, 60, 80)
        d2 = next(ly for ly in back.layers if ly.uid == drawn.uid)
        self.assertEqual(d2.shapes, drawn.shapes)
        self.assertEqual(d2.masks.frames(), list(range(40)))
        np.testing.assert_array_equal(d2.masks.get(17), SH.draw([TRI], 60, 80))
        t2 = next(ly for ly in back.layers if ly.uid == tracked.uid)
        self.assertEqual(t2.masks.frames(), [3])

    def test_loaded_at_another_size_the_shapes_redraw_there(self):
        p, _t, drawn = self.project()
        path = str(self.tmp / "edit.json")
        p.save(path)
        back = Project.load(path, 120, 160)
        d2 = next(ly for ly in back.layers if ly.uid == drawn.uid)
        np.testing.assert_array_equal(d2.masks.get(0), SH.draw([TRI], 120, 160))

    def test_undo_sees_a_shape_and_a_snapshot_keeps_its_own(self):
        from core.depthedit.history import fingerprint
        p, _t, drawn = self.project()
        before = fingerprint(p)
        snap = p.snapshot()
        drawn.shapes = drawn.shapes + SH.clean([{"kind": "Rectangle", "pts": [[0.6, 0.6], [0.9, 0.9]]}])
        SH.fill(drawn, 40)
        self.assertNotEqual(fingerprint(p), before)
        self.assertEqual(len(next(ly for ly in snap.layers if ly.uid == drawn.uid).shapes), 1)

    def test_no_shapes_left_no_mask(self):
        _p, _t, drawn = self.project()
        drawn.shapes = []
        SH.fill(drawn, 40)
        self.assertEqual(len(drawn.masks), 0)
        self.assertFalse(drawn.tracked)


if __name__ == "__main__":
    unittest.main()
