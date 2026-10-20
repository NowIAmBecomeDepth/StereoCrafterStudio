"""The Depth Map Editor's Add mode and Render at source resolution (user,
2026-10-03: a mannequin DepthCrafter left as a few polygons -- track its real
outline on the RGB and put it in the depth map, as Detect Text does for text).

* Alter (as before) changes the depth already there; Add replaces it inside
  the mask with the layer's own depth, so the shape is the mask's.
* Render at source resolution: the edit at the RGB's size, RGB layers with
  SAM 3's full-size masks -- a detail thinner than one depth-map pixel
  survives -- and the depth-map pixel settings scaled to match.
* The full-size masks are kept by RGB layers only, and saved with the project.

Arrays and temp files only; no SAM 3, no GPU.

    python -m unittest studio.tests.test_depthedit_hires
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import edit_ops, hires  # noqa: E402
from core.depthedit.project import Layer, MaskStore, Project  # noqa: E402


def scene(h=24, w=32):
    """A far background (0.2) with a blob DepthCrafter made of the object (0.6)."""
    v = np.full((h, w), 0.2, np.float32)
    v[8:16, 12:20] = 0.6
    return v


def silhouette(h=24, w=32):
    """The object's real outline: taller and wider than the blob."""
    m = np.zeros((h, w), bool)
    m[4:22, 10:22] = True
    return m


class AddModeTests(unittest.TestCase):
    def settings(self, **kw):
        base = dict(feather=0.0, edge_snap=4)
        base.update(kw)
        return edit_ops.LayerSettings(**base)

    def test_alter_changes_what_is_there(self):
        v, m = scene(), silhouette()
        out = edit_ops.apply_layer(v, m, self.settings(brightness=0.1))
        # Edge snap (on by default) pulls the outline onto the depth map's own
        # edges: the blob moves, the rest of the real outline does not -- the
        # shape stays the depth map's (what Add is for)
        self.assertAlmostEqual(float(out[10, 15]), 0.7, places=5)
        self.assertAlmostEqual(float(out[5, 11]), 0.2, places=5)
        # without the snap it moves the background inside the outline too
        out = edit_ops.apply_layer(v, m, self.settings(brightness=0.1, edge_snap=0))
        self.assertAlmostEqual(float(out[5, 11]), 0.3, places=5)

    def test_add_is_the_masks_shape(self):
        v, m = scene(), silhouette()
        out = edit_ops.apply_layer(v, m, self.settings(mode="Add", add_depth=0.65))
        self.assertTrue(np.allclose(out[m], 0.65))                   # the whole outline, blob or not
        self.assertTrue(np.array_equal(out[~m], v[~m]))              # nothing outside it
        # Edge snap does not pull the outline onto the blob's edges
        self.assertAlmostEqual(float(out[5, 11]), 0.65, places=5)

    def test_add_takes_push_pull_and_opacity(self):
        v, m = scene(), silhouette()
        out = edit_ops.apply_layer(v, m, self.settings(mode="Add", add_depth=0.5, brightness=0.1, opacity=0.5))
        self.assertAlmostEqual(float(out[5, 11]), 0.2 + (0.6 - 0.2) * 0.5, places=5)

    def test_sample_is_the_objects_nearest_part(self):
        v, m = scene(), silhouette()
        self.assertAlmostEqual(edit_ops.sample_add_depth(v, m), 0.6, places=5)
        self.assertIsNone(edit_ops.sample_add_depth(v, None))
        self.assertIsNone(edit_ops.sample_add_depth(v, np.zeros_like(m)))

    def test_an_old_layer_is_alter(self):
        s = edit_ops.layer_settings_from_dict({"brightness": 0.2})
        self.assertEqual((s.mode, s.add_depth), ("Alter", 0.5))


class SourceResolutionTests(unittest.TestCase):
    def test_a_detail_finer_than_a_depth_pixel_survives(self):
        lo = scene()                                         # 32 x 24
        size = (128, 96)                                     # the RGB: 4x
        hi = np.zeros((96, 128), bool)
        hi[20:80, 50:51] = True                              # one RGB pixel wide: a quarter of a depth pixel
        s = edit_ops.LayerSettings(mode="Add", add_depth=0.9, feather=0.0)
        out = hires.render_frame(lo, edit_ops.GlobalSettings(), [(s, None, hi)], 0, size)
        self.assertEqual(out.shape, (96, 128))
        self.assertTrue(np.allclose(out[20:80, 50], 0.9))     # the line is there, at full size...
        self.assertAlmostEqual(float(out[25, 52]), 0.2, places=3)   # ...and only the line (beside it: background)
        # at the depth map's size the same line is lost to its pixels
        lo_mask = hi.reshape(24, 4, 32, 4).mean(axis=(1, 3)) >= 0.5
        self.assertFalse(lo_mask.any())

    def test_a_layer_without_full_size_masks_is_enlarged(self):
        lo = scene()
        s = edit_ops.LayerSettings(mode="Add", add_depth=0.9, feather=0.0, edge_snap=0)
        out = hires.render_frame(lo, edit_ops.GlobalSettings(), [(s, silhouette(), None)], 0, (64, 48))
        self.assertAlmostEqual(float(out[20, 30]), 0.9, places=5)   # the silhouette, twice the size
        self.assertAlmostEqual(float(out[2, 2]), 0.2, places=4)

    def test_pixel_settings_scale(self):
        s = edit_ops.LayerSettings(grow=2.0, feather=1.5, smooth=3.0, edge_top=-1.0, edge_bottom=0.5, edge_snap=4)
        k = hires.scaled(s, 2.5)
        self.assertEqual((k.grow, k.feather, k.smooth, k.edge_top, k.edge_bottom, k.edge_snap),
                         (5.0, 3.75, 7.5, -2.5, 1.25, 10))
        self.assertEqual(hires.scaled(s, 2.5, snap=False).edge_snap, 0)
        self.assertEqual((s.grow, s.edge_snap), (2.0, 4))                  # the original untouched

    def test_info_at(self):
        class Info:
            width, height, fps = 768, 448, "24/1"
        info = Info()
        big = hires.info_at(info, (1920, 1038))
        self.assertEqual((big.width, big.height, big.fps), (1920, 1038, "24/1"))
        self.assertEqual((info.width, info.height), (768, 448))
        self.assertIs(hires.info_at(info, None), info)


class ViewTests(unittest.TestCase):
    """The RGB view at up to 720p (user, 2026-10-03) and the outline drawn on it."""

    def test_view_size(self):
        gb = 3 * 1024 ** 3
        self.assertEqual(hires.view_size((1920, 1038), (768, 448), 70, gb), (1332, 720))
        self.assertEqual(hires.view_size((512, 320), (256, 160), 24, gb), (512, 320))     # under 720p: its own
        # no finer than the map, too long for the cache, or no source: the map's size
        self.assertEqual(hires.view_size((640, 360), (768, 448), 70, gb), (768, 448))
        self.assertEqual(hires.view_size((1920, 1038), (768, 448), 2000, gb), (768, 448))
        self.assertEqual(hires.view_size(None, (768, 448), 70, gb), (768, 448))

    def test_outline_is_the_full_size_mask(self):
        lo = scene()                                             # 32 x 24
        hi = np.zeros((96, 128), bool)
        hi[20:80, 50:51] = True                                  # lost at the map's size
        s = edit_ops.LayerSettings(mode="Add", feather=0.0)
        lo_mask = np.zeros((24, 32), bool)
        m = hires.view_mask(lo_mask, hi, s, lo, (128, 96), use_hi=True)
        self.assertEqual(m.shape, (96, 128))
        self.assertTrue(m[20:80, 50].all() and m.sum() == 60)
        # Render at source resolution off: what Render uses, the map's mask enlarged
        self.assertFalse(hires.view_mask(lo_mask, hi, s, lo, (128, 96), use_hi=False).any())

    def test_outline_at_the_maps_size_is_the_editors(self):
        v, m = scene(), silhouette()
        s = edit_ops.LayerSettings(edge_snap=4)
        self.assertTrue(np.array_equal(hires.view_mask(m, None, s, v, (32, 24), True),
                                       edit_ops.effective_mask(m, s, v)))
        # Add ignores Edge snap: so does its outline
        add = hires.view_mask(m, None, edit_ops.LayerSettings(mode="Add", edge_snap=4), v, (32, 24), True)
        self.assertTrue(np.array_equal(add, m))


class MaskTests(unittest.TestCase):
    def result(self, h, w):
        m = np.zeros((1, h, w), bool)
        m[0, h // 4:h // 2, w // 4:w // 2] = True
        return [1], m

    def test_rgb_layers_keep_full_size_masks(self):
        rgb, depth = Layer(name="rgb", source="RGB"), Layer(name="depth", source="Depth")
        for layer in (rgb, depth):
            hires.store_result(layer, 3, self.result(96, 128), (128, 96))
        self.assertEqual((rgb.masks_hi.width, rgb.masks_hi.height), (128, 96))
        self.assertTrue(rgb.masks_hi.get(3).any())
        self.assertIsNone(depth.masks_hi)
        hires.store_result(rgb, 4, self.result(96, 128), None)       # no RGB source: nothing
        self.assertIsNone(rgb.masks_hi.get(4))

    def test_saved_and_loaded_with_the_project(self):
        p = Project(rgb_path="a.mp4", depth_path="a_depth.mp4")
        layer = p.new_layer(24, 32)
        m = np.zeros((24, 32), bool); m[2:5, 3:9] = True
        layer.masks.set(0, m)
        layer.masks_hi = MaskStore(96, 128)
        hm = np.zeros((96, 128), bool); hm[10:20, 30:31] = True
        layer.masks_hi.set(0, hm)
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "a_depthedit.json")
            p.save(path)
            q = Project.load(path, 24, 32)
        got = q.layers[0]
        self.assertTrue(np.array_equal(got.masks.get(0), m))
        self.assertTrue(np.array_equal(got.masks_hi.get(0), hm))
        snap = q.snapshot().layers[0]
        self.assertTrue(np.array_equal(snap.masks_hi.get(0), hm))


if __name__ == "__main__":
    unittest.main()
