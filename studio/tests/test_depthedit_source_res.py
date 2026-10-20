"""The Depth Map Editor's Left / Right edge moves and Upscale Map (user,
2026-10-05: "add left and right edge alignment knobs next to top and
bottom"; "upscale the depthmap right before it enters the editor so it has
that more fine pixel adjustments immediately" -- a button beside Load Clip).

* Left / right are top / bottom turned on their side: the same one-sided
  window, along a row -- checked against the transposed top / bottom.
* Upscale Map: depth_io reads the map enlarged, bilinear (as Splatting
  enlarges it); hires.edit_size picks the size or says why not; the layers'
  masks come along (hires.upscale_layers); a saved project's masks are
  reused across the change of size; the RGB view stays within its cache.

Arrays, temp files and ffmpeg only; no SAM 3, no GPU.

    python -m unittest studio.tests.test_depthedit_source_res
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import depth_io, edit_ops, hires, keys  # noqa: E402
from core.depthedit.project import Layer, MaskStore, Project  # noqa: E402


def scene(h=24, w=32):
    """A near box on a far ramp, and the box's mask."""
    v = np.tile(np.linspace(0.1, 0.3, w, dtype=np.float32), (h, 1))
    v[6:16, 9:21] = 0.8
    m = np.zeros((h, w), bool)
    m[6:16, 9:21] = True
    return v, m


class SidewaysEdgeTests(unittest.TestCase):
    def test_left_and_right_are_top_and_bottom_on_their_side(self):
        v, _m = scene()
        for px in (-3, -2.5, 2, 3.5):
            for side, turned in (("left", "top"), ("right", "bottom")):
                got = edit_ops.edge_shift(v, px, side=side)
                want = edit_ops.edge_shift(np.ascontiguousarray(v.T), px, side=turned).T
                self.assertTrue(np.allclose(got, want, atol=1e-6), (side, px))

    def test_the_old_top_flag_still_works(self):
        v, _m = scene()
        self.assertTrue(np.array_equal(edit_ops.edge_shift(v, -2, top=True), edit_ops.edge_shift(v, -2, side="top")))
        self.assertTrue(np.array_equal(edit_ops.edge_shift(v, 2, top=False),
                                       edit_ops.edge_shift(v, 2, side="bottom")))

    def test_a_layer_moves_one_side_only(self):
        v, m = scene()
        base = dict(edge_snap=0, feather=0.0)
        out = edit_ops.apply_layer(v, m, edit_ops.LayerSettings(edge_left=-3, **base))
        self.assertTrue(np.allclose(out[6:16, 9:12], v[6:16, 6:9]), "the left 3 columns took the depth beside them")
        self.assertTrue(np.array_equal(out[6:16, 12:21], v[6:16, 12:21]), "the right side stays")
        out = edit_ops.apply_layer(v, m, edit_ops.LayerSettings(edge_right=2, **base))
        self.assertTrue(np.allclose(out[6:16, 21:23], 0.8), "pushed out: two more columns of the box")
        self.assertTrue(np.array_equal(out[:, :21], v[:, :21]), "nothing else moved")

    def test_a_layer_matches_its_transposed_top_bottom_edit(self):
        v, m = scene()
        vt, mt = np.ascontiguousarray(v.T), np.ascontiguousarray(m.T)
        base = dict(edge_snap=0, feather=1.0)
        for left, right in ((-2.5, 0.0), (0.0, 3.0), (-1.0, 1.5)):
            got = edit_ops.apply_layer(v, m, edit_ops.LayerSettings(edge_left=left, edge_right=right, **base))
            want = edit_ops.apply_layer(vt, mt, edit_ops.LayerSettings(edge_top=left, edge_bottom=right, **base)).T
            self.assertTrue(np.allclose(got, want, atol=1e-5), (left, right))

    def test_settings_keys_and_scaling_know_them(self):
        s = edit_ops.layer_settings_from_dict({"edge_left": -3, "edge_right": 2})
        self.assertEqual((s.edge_left, s.edge_right), (-3.0, 2.0))
        self.assertEqual(type(s.edge_left), float)
        back = edit_ops.layer_settings_from_dict(edit_ops.settings_to_dict(s))
        self.assertEqual((back.edge_left, back.edge_right), (-3.0, 2.0))
        old = edit_ops.layer_settings_from_dict({"edge_top": -1})          # a project from before
        self.assertEqual((old.edge_left, old.edge_right), (0.0, 0.0))
        k = hires.scaled(s, 2.5)
        self.assertEqual((k.edge_left, k.edge_right), (-7.5, 5.0))
        self.assertIn(("edge_left",), keys.KEY_PATHS)
        self.assertIn(("edge_right",), keys.KEY_PATHS)


class ReadEnlargedTests(unittest.TestCase):
    """depth_io.at_size: the map read enlarged as it is decoded."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.path = str(cls.tmp / "clip_depth.mp4")
        info = depth_io.ClipInfo(cls.path, 32, 24, 3, "24/1", "yuv420p10le", False)
        rng = np.random.default_rng(1)
        w = depth_io.DepthWriter(cls.path, info, use_nvenc=False)
        for _ in range(3):
            w.write((rng.random((24, 32)) * 800 + 100).astype(np.uint16))
        w.close()
        cls.info = depth_io.probe(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_its_own_size_is_itself(self):
        self.assertIs(depth_io.at_size(self.info, None), self.info)
        self.assertIs(depth_io.at_size(self.info, (32, 24)), self.info)

    def test_read_enlarged_bilinear_as_splatting(self):
        big = depth_io.at_size(self.info, (64, 48))
        self.assertEqual((big.width, big.height, big.file_size), (64, 48, (32, 24)))
        self.assertEqual((self.info.width, self.info.height, self.info.file_size), (32, 24, None),
                         "the clip's own info is left alone")
        native = depth_io.load_codes(self.info)
        got = depth_io.load_codes(big)
        self.assertEqual(got.shape, (3, 48, 64))
        for a, b in zip(native, got):
            self.assertTrue(np.array_equal(cv2.resize(a, (64, 48), interpolation=cv2.INTER_LINEAR), b))
        # enlarging twice still means from the file's own size
        again = depth_io.at_size(big, (96, 72))
        self.assertEqual(again.file_size, (32, 24))
        self.assertIs(depth_io.at_size(big, (32, 24)).file_size, None)

    def test_written_at_the_enlarged_size(self):
        big = depth_io.at_size(self.info, (64, 48))
        out = str(self.tmp / "out_depth.mp4")
        w = depth_io.DepthWriter(out, big, use_nvenc=False)
        for codes in depth_io.iter_codes(big):
            w.write(codes)
        w.close()
        written = depth_io.probe(out)
        self.assertEqual((written.width, written.height, written.num_frames), (64, 48, 3))


class EditSizeTests(unittest.TestCase):
    def info(self, w=768, h=448, n=750):
        return depth_io.ClipInfo("d.mp4", w, h, n, "24/1", "yuv420p10le", False)

    def test_the_sources_size(self):
        with mock.patch.object(hires, "source_size", lambda p: (1920, 1038)):
            self.assertEqual(hires.edit_size("a.mp4", self.info()), ((1920, 1038), ""))

    def test_no_source_says_so(self):
        with mock.patch.object(hires, "source_size", lambda p: None):
            size, note = hires.edit_size("", self.info())
        self.assertIsNone(size)
        self.assertIn("source video", note)

    def test_a_map_already_that_size_is_left(self):
        with mock.patch.object(hires, "source_size", lambda p: (768, 448)):
            size, note = hires.edit_size("a.mp4", self.info())
        self.assertIsNone(size)
        self.assertIn("already", note)

    def test_too_long_says_so(self):
        with mock.patch.object(hires, "source_size", lambda p: (1920, 1038)):
            size, note = hires.edit_size("a.mp4", self.info(n=750), budget=1024 ** 3)
        self.assertIsNone(size)
        self.assertIn("too long", note)

    def test_the_rgb_view_fits_its_cache(self):
        gb3 = 3 * 1024 ** 3
        # a 750-frame map read at 1920x1038: the view at 720p, not the map's size
        self.assertEqual(hires.view_size((1920, 1038), (1920, 1038), 750, gb3), (1332, 720))
        # a short one fits at the map's own size, outlines exact
        self.assertEqual(hires.view_size((1920, 1038), (1920, 1038), 100, gb3), (1920, 1038))

    def test_no_second_mask_store_at_the_same_size(self):
        self.assertIsNone(hires.new_store((64, 48), (64, 48)))
        self.assertEqual(hires.new_store((64, 48), (32, 24)).width, 64)
        layer = Layer(name="rgb", source="RGB")
        layer.masks = MaskStore(48, 64)
        m = np.zeros((1, 48, 64), bool); m[0, 5:9, 5:9] = True
        hires.store_result(layer, 0, ([1], m), (64, 48))
        self.assertIsNone(layer.masks_hi)


class UpscaleLayersTests(unittest.TestCase):
    """The open project's masks after Upscale Map."""

    def test_rgb_full_size_masks_become_the_masks_others_are_enlarged(self):
        p = Project(rgb_path="a.mp4", depth_path="a_depth.mp4")
        rgb = p.new_layer(24, 32); rgb.source = "RGB"; rgb.name = "rgb"
        dep = p.new_layer(24, 32); dep.source = "Depth"; dep.name = "depth"
        empty = p.new_layer(24, 32); empty.name = "empty"
        m = np.zeros((24, 32), bool); m[6:12, 8:16] = True
        rgb.masks.set(0, m); dep.masks.set(0, m)
        rgb.masks_hi = MaskStore(48, 64)
        fine = np.zeros((48, 64), bool); fine[12:24, 16:31] = True; fine[30, 5] = True   # a detail the map lacks
        rgb.masks_hi.set(0, fine)
        enlarged = hires.upscale_layers(p, (64, 48))
        self.assertEqual(enlarged, ["depth"], "only the layer whose mask was enlarged is named")
        self.assertTrue(np.array_equal(rgb.masks.get(0), fine))
        self.assertIsNone(rgb.masks_hi)
        self.assertTrue(np.array_equal(dep.masks.get(0), hires.enlarge_mask(m, (64, 48))))
        self.assertEqual((empty.masks.height, empty.masks.width), (48, 64))
        self.assertIsNone(empty.masks.get(0))


class ProjectSizeTests(unittest.TestCase):
    """A saved project's masks across Edit at source resolution on / off."""

    def save_load(self, p, h, w):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "a_depthedit.json")
            p.save(path)
            return Project.load(path, h, w)

    def project(self, h, w, hi=None):
        p = Project(rgb_path="a.mp4", depth_path="a_depth.mp4")
        layer = p.new_layer(h, w)
        layer.tracked = True
        m = np.zeros((h, w), bool); m[h // 4:h // 2, w // 4:w // 2] = True
        layer.masks.set(0, m)
        if hi:
            hh, hw = hi
            layer.masks_hi = MaskStore(hh, hw)
            big = np.zeros((hh, hw), bool); big[hh // 4:hh // 2, hw // 4:hw // 2 + 1] = True
            layer.masks_hi.set(0, big)
        return p

    def test_turned_on_the_full_size_masks_are_the_masks(self):
        p = self.project(24, 32, hi=(48, 64))
        want = p.layers[0].masks_hi.get(0)
        got = self.save_load(p, 48, 64).layers[0]
        self.assertTrue(got.tracked, "no tracking again")
        self.assertTrue(np.array_equal(got.masks.get(0), want))
        self.assertIsNone(got.masks_hi, "the same size: no second copy")

    def test_turned_off_the_large_masks_are_shrunk_and_kept_full_size(self):
        p = self.project(48, 64)
        big = p.layers[0].masks.get(0)
        got = self.save_load(p, 24, 32).layers[0]
        self.assertTrue(got.tracked)
        self.assertEqual(got.masks.get(0).shape, (24, 32))
        self.assertTrue(np.array_equal(got.masks.get(0),
                                       cv2.resize(big.astype(np.float32), (32, 24), interpolation=cv2.INTER_AREA) >= 0.5))
        self.assertTrue(np.array_equal(got.masks_hi.get(0), big), "kept for Render at source resolution")

    def test_enlarged_without_full_size_masks_needs_tracking(self):
        got = self.save_load(self.project(24, 32), 48, 64).layers[0]
        self.assertFalse(got.tracked)
        self.assertIsNone(got.masks.get(0))
        self.assertTrue(got.clicks is not None)

    def test_the_same_size_is_as_before(self):
        p = self.project(24, 32, hi=(48, 64))
        got = self.save_load(p, 24, 32).layers[0]
        self.assertTrue(np.array_equal(got.masks.get(0), p.layers[0].masks.get(0)))
        self.assertEqual((got.masks_hi.height, got.masks_hi.width), (48, 64))


if __name__ == "__main__":
    unittest.main()
