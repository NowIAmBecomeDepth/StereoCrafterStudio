"""Put Back (user, 2026-10-15; core/putback/engine.py): an erased object laid back over the finished
3D at its own depth from the original clip's depth map. No clips: arrays, and on the GPU (when there
is one) Splatting's own warp."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.putback import engine as P  # noqa: E402


class DepthFitTest(unittest.TestCase):
    """DepthCrafter normalises each clip to its own range: the original's depth comes onto the clean
    clip's scale by a fit over the scene both see -- outside the object."""

    def test_the_scale_is_found_and_the_object_ignored(self):
        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:200, 0:300].astype(np.float32)
        clean = 200 + 300 * xx / 300 + 50 * np.sin(yy / 17) + rng.normal(0, 2, (200, 300))
        orig = (clean - 40) / 2.5                                  # another range: gain 2.5, offset 40
        mask = np.zeros((200, 300), np.float32)
        mask[60:140, 100:180] = 1
        orig[mask > 0] = 900                                       # the object, near, only in the original
        gain, offset, frac = P.fit_scale(orig, clean, mask)
        self.assertAlmostEqual(gain, 2.5, delta=0.05)
        self.assertAlmostEqual(offset, 40, delta=5)
        self.assertGreater(frac, 0.8)

    def test_fits_are_evened_and_a_bad_frame_doesnt_pull(self):
        fits = [(2.0, 10.0, 0.9)] * 10
        fits[5] = (9.0, 400.0, 0.9)                                 # one bad frame
        fits[7] = (float("nan"), 0.0, 0.0)                         # one with no fit
        out = P.smooth_fits(fits, 3)
        self.assertEqual(len(out), 10)
        self.assertTrue(all(abs(g - 2.0) < 1e-9 for g, _b in out), out)

    def test_steady_holds_the_first_frames_fit(self):
        fits = [(2.0 + 0.1 * i, 10.0, 0.9) for i in range(20)]
        out = P.smooth_fits(fits, 3, steady=4)
        self.assertTrue(all(abs(g - 2.15) < 1e-9 for g, _b in out), out[:3])


class EdgeTest(unittest.TestCase):
    def test_choke_and_feather(self):
        m = np.zeros((40, 40), np.float32)
        m[10:30, 10:30] = 1
        self.assertEqual(float(P.shape_alpha(m, 0, 0).sum()), 400.0)
        self.assertLess(float(P.shape_alpha(m, 2, 0).sum()), 400.0 * 0.85)     # 2 px off each side
        soft = P.shape_alpha(m, 0, 2)
        self.assertTrue(0 < soft[20, 10] < 1)                                  # a soft edge
        self.assertAlmostEqual(float(soft[20, 20]), 1.0, places=3)


@unittest.skipUnless(__import__("torch").cuda.is_available(), "no GPU")
class WarpMatchesTheSceneTest(unittest.TestCase):
    """The object goes where Splatting's warp puts its depth: the same as the whole frame's warp there."""

    def test_the_layer_moves_with_the_scene(self):
        import torch
        from core.splatting.forward_warp import ForwardWarpStereo, execute_forward_warp
        H, W = 120, 640
        geo = P.Geometry(vid={}, track=None,
                         kf_base={"max_disp": 30.0, "zero_disparity_anchor_val": 0.5, "depth_gamma": 1.0,
                                  "boost_thresh": 0.85, "boost_amount": 0.0, "boost_curve": 2.0, "boost_knee": 0.0,
                                  "boost_prefilter_radius": 0, "boost_prefilter_eps": 1e-3, "pop_amount": 0.0},
                         assume_raw=True, gmin=0.0, gmax=1.0, max_raw=1023.0, tv_comp=1.0, dual_eye=False,
                         dual_eye_bias=0.5, input_bias=0.0, peak_raw=1.0, match_depth_res=True)
        rng = np.random.default_rng(1)
        rgb = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
        alpha = np.zeros((H, W), np.float32)
        alpha[30:90, 200:260] = 1
        raw = np.full((H, W), 0.9 * 1023, np.float32)               # near: in front of the screen
        lay = P.ObjectWarp(geo, W)(0, rgb, alpha, raw)
        pm, al = lay["right"]
        # the whole frame at that depth, as Splatting warps it
        whole, _occ = execute_forward_warp(
            stereo_projector=ForwardWarpStereo(occlu_map=True).cuda(),
            source_tensor=torch.from_numpy(rgb.transpose(2, 0, 1).copy())[None].cuda(),
            depth_tensor=torch.full((1, 1, H, W), 0.9, device="cuda"), target_width=W, max_disp=30.0,
            zero_disparity_anchor_val=0.5)
        whole = whole[0].cpu().numpy().transpose(1, 2, 0)
        inside = al > 0.99
        self.assertGreater(int(inside.sum()), 2000)
        self.assertLess(float(np.abs(pm[inside] - whole[inside]).max()), 1e-3)
        cols = np.flatnonzero(inside.any(0))
        self.assertNotEqual(int(cols.min()), 200, "it moved")



class OriginalEyesTest(unittest.TestCase):
    """Put Back's Original Eyes (user, 2026-10-16: "lets just try isolating him from the left and right eyes that
    already exist"): his disparity measured between the original merge's two eyes, his mask moved into the
    right eye by it, and the merge's files found."""

    def test_the_mask_lands_on_him_in_the_right_eye(self):
        import torch
        from core.putback.engine import EYES_SEARCH, eyes_disparity, eyes_right_mask
        g = torch.Generator().manual_seed(3)
        h, w, shift = 160, 320, 14
        bg_l = torch.rand(1, 3, h, w, generator=g)
        obj = torch.rand(1, 3, h, w, generator=g)
        a = torch.zeros(1, 1, h, w)
        a[..., 40:120, 100:180] = 1.0
        left = bg_l * (1 - a) + obj * a
        a_r_true = torch.roll(a, -shift, 3)                       # in the right eye he's 14 px to the left
        right = bg_l * (1 - a_r_true) + torch.roll(obj, -shift, 3) * a_r_true
        inside = -torch.nn.functional.max_pool2d(-a, 15, 1, 7)
        disp = eyes_disparity(left, right, inside, EYES_SEARCH * w)
        self.assertAlmostEqual(float(disp[0, 0, 80, 140]), float(shift), delta=1.0)
        a_r = eyes_right_mask(a, disp)
        cols = torch.nonzero(a_r[0, 0, 80] > 0.5).flatten()
        self.assertEqual((int(cols.min()), int(cols.max())), (100 - shift, 179 - shift))
        self.assertGreater(float((a_r * a_r_true).sum() / a_r_true.sum()), 0.98)

    def test_the_merge_is_found(self):
        import os
        import tempfile
        from core.putback.engine import find_eyes
        d = tempfile.mkdtemp()
        for f in ("shot_1920_merged_full_sbs_L.mov", "shot_1920_merged_full_sbs_R.mov", "other_1920_merged_L.mov"):
            open(os.path.join(d, f), "wb").close()
        self.assertEqual(find_eyes(d, "shot"), (os.path.join(d, "shot_1920_merged_full_sbs_L.mov"),
                                                os.path.join(d, "shot_1920_merged_full_sbs_R.mov")))
        self.assertEqual(find_eyes(os.path.join(d, "shot_1920_merged_full_sbs_R.mov"), "shot")[1],
                         os.path.join(d, "shot_1920_merged_full_sbs_R.mov"))
        self.assertIsNone(find_eyes(d, "missing"))
        open(os.path.join(d, "sbs_1920_merged_full_sbs.mp4"), "wb").close()
        p = os.path.join(d, "sbs_1920_merged_full_sbs.mp4")
        self.assertEqual(find_eyes(d, "sbs"), (p, p))


class DecombTest(unittest.TestCase):
    """The warped object's combed strip dropped, its outline and its body kept (user, 2026-10-16: "this splat
    pattern is appearing and it shrinks his outline too much if i shrink until the pattern disappears"):
    the original map's soft, wide object pulls a strip of background apart into stripes."""

    def test_the_strip_goes_the_object_stays(self):
        import torch
        from core.putback.engine import decomb
        t = torch.zeros(4, 120, 200)
        t[:, 20:100, 30:100] = 1.0                 # the object: solid, a clean outline
        t[:, 20:100:2, 100:150] = 1.0              # beside it, a strip pulled apart: every other row
        t[:, 60, 60] = 0.0                         # one lone gap inside the object
        out = decomb(t)
        a = out[3]
        self.assertLess(float(a[20:100, 106:150].sum()), 5.0, "the combed strip stayed")
        body = a[22:98, 30:94].clone()
        body[60 - 22, 60 - 30] = 1.0               # the lone gap: checked below
        self.assertEqual(float(body.min()), 1.0, "the object lost some of its body")
        self.assertEqual(float(a[22:98, 30].min()), 1.0, "its outline was shrunk")
        self.assertGreater(float(a[60, 60]), 0.85, "the lone gap wasn't filled")


class DisparityAndConvergenceTest(unittest.TestCase):
    """Put Back's Disparity (user, 2026-10-16: "a divergence slider when the depth map option is selected")
    scales how far the object moves, as Splatting's Disparity would; Convergence moves its convergence
    plane, with a wider range, so a big setting slides the whole object and keeps its shape (shifting the
    depth flattened it against 0 or 1)."""

    H, W = 120, 640

    def lay(self, raw, conv=0.0, disp=1.0):
        geo = P.Geometry(vid={}, track=None,
                         kf_base={"max_disp": 30.0, "zero_disparity_anchor_val": 0.5, "depth_gamma": 1.0,
                                  "boost_thresh": 0.85, "boost_amount": 0.0, "boost_curve": 2.0, "boost_knee": 0.0,
                                  "boost_prefilter_radius": 0, "boost_prefilter_eps": 1e-3, "pop_amount": 0.0},
                         assume_raw=True, gmin=0.0, gmax=1.0, max_raw=1023.0, tv_comp=1.0, dual_eye=False,
                         dual_eye_bias=0.5, input_bias=0.0, peak_raw=1.0, match_depth_res=True)
        rgb = np.ones((self.H, self.W, 3), np.float32)
        alpha = np.zeros((self.H, self.W), np.float32)
        alpha[30:90, 300:360] = 1
        _pm, al = P.ObjectWarp(geo, self.W)(0, rgb, alpha, raw, conv, disp)["right"]
        return al

    def left_edge(self, al, row=60):
        return int(np.flatnonzero(al[row] > 0.5).min())

    def test_disparity_scales_the_move(self):
        raw = np.full((self.H, self.W), 0.9 * 1023, np.float32)
        moved = [self.left_edge(self.lay(raw, disp=k)) - 300 for k in (0.0, 1.0, 2.0)]
        self.assertEqual(moved[0], 0, "0%: flat at the screen")
        self.assertNotEqual(moved[1], 0)
        self.assertLessEqual(abs(moved[2] - 2 * moved[1]), 1, moved)

    def test_a_big_convergence_keeps_the_objects_shape(self):
        raw = np.full((self.H, self.W), 0.5 * 1023, np.float32)
        raw[:60] = 0.9 * 1023                          # its top half nearer than its bottom
        al = self.lay(raw, conv=-1.2)                 # brought well out: past where depth clipped it
        top, bottom = self.left_edge(al, 45), self.left_edge(al, 75)
        self.assertNotEqual(top, bottom, "flattened: both halves at the same depth")
        # the halves' depth gap (0.9 - 0.5) as the warp moves it: x 2 x 30% / 1000 x 640 / 2 = 7.7 px
        self.assertLessEqual(abs(abs(top - bottom) - 0.4 * 30 / 1000 * self.W), 1.5, (top, bottom))

    def test_settings_saved_and_kept_in_range(self):
        import json
        import os
        import tempfile
        from core.removal import putback as R
        d = tempfile.mkdtemp()
        clip = os.path.join(d, "shot.mp4")
        os.makedirs(os.path.join(d, "edit_history"))
        self.assertEqual(R.load_settings(clip)["disp"], 100.0)
        with open(R.settings_path(clip), "w", encoding="utf-8") as f:
            json.dump({"disp": 999, "conv": 1.5}, f)
        s = R.load_settings(clip)
        self.assertEqual((s["disp"], s["conv"]), (300.0, 1.5))
        with open(R.settings_path(clip), "w", encoding="utf-8") as f:
            json.dump({"disp": "x", "conv": -9}, f)
        s = R.load_settings(clip)
        self.assertEqual((s["disp"], s["conv"]), (100.0, -2.0))


class MergingControlsTest(unittest.TestCase):
    """Merging's Put Back controls (user, 2026-10-15): Grow (+ dilate / - erode) and Blur on the object's
    mask; settings from before them read as before; Erase from Clip keeps the clip's depth map."""

    def test_grow_and_blur(self):
        import torch
        from core.removal import putback as R
        a = torch.zeros(1, 1, 40, 40)
        a[..., 10:30, 10:30] = 1
        self.assertEqual(float(R._shape(a, 0, 0).sum()), 400.0)
        self.assertEqual(float(R._shape(a, 2, 0).sum()), 24.0 * 24.0)        # 2 px out each side
        self.assertEqual(float(R._shape(a, -2, 0).sum()), 16.0 * 16.0)       # 2 px in
        soft = R._shape(a, 0, 4)
        self.assertTrue(0 < float(soft[0, 0, 20, 10]) < 1)
        self.assertAlmostEqual(float(soft[0, 0, 20, 20]), 1.0, places=3)

    def test_old_settings_read_as_before(self):
        import json
        import os
        import tempfile
        from core.removal import putback as R
        d = tempfile.mkdtemp()
        clip = os.path.join(d, "shot.mp4")
        os.makedirs(os.path.join(d, "edit_history"))
        with open(R.settings_path(clip), "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "mode": "Over", "parallax": 1.5}, f)
        s = R.load_settings(clip)
        self.assertEqual((s["depth"], s["conv"], s["grow"], s["blur"], s["parallax"]), ("Parallax", 0.0, 0.0, 0.0, 1.5))
        with open(R.settings_path(clip), "w", encoding="utf-8") as f:
            json.dump({"grow": 99, "blur": -3, "conv": "x", "depth": "Sideways"}, f)
        s = R.load_settings(clip)
        self.assertEqual((s["grow"], s["blur"], s["conv"], s["depth"]), (10.0, 0.0, 0.0, "Parallax"))

    def test_erase_keeps_the_depth_map(self):
        import os
        import tempfile
        from core.removal import putback as R
        d = tempfile.mkdtemp()
        clip = os.path.join(d, "shot.mp4")
        depth = os.path.join(d, "shot_depth.mp4")
        with open(depth, "wb") as f:
            f.write(b"depth")
        kept = R.keep_original_depth(clip, depth)
        with open(kept, "rb") as f:
            self.assertEqual(f.read(), b"depth")
        s = R.load_settings(clip)
        self.assertEqual((s["original_depth"], s["depth"]), (kept, "Original Depth"))
        s["depth"] = "Parallax"
        R.save_settings(clip, s)
        R.keep_original_depth(clip, depth)                                   # erased again
        self.assertEqual(R.load_settings(clip)["depth"], "Parallax", "a choice made isn't undone")

if __name__ == "__main__":
    unittest.main()
