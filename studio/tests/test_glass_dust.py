"""Dust on glass (user, 2026-10-17: the dust on a windshield "stands out in 3D as wrong" -- "it needs to be nearer on
the right and further on the left following the car" -- "as long as there is a convergence setting"):
Erase from Clip's Dust (core.removal.static_reflect.erase_dust) takes each frame's own dust off the glass, and Put
Back's Glass Plane (core.putback.engine) lays it on each window's own plane, fitted to the depth map around it.
Synthetic clips in a temp folder only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.putback import engine as P  # noqa: E402
from core.removal import putback as P_  # noqa: E402


def _write(path, frames, rate="24"):
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}",
                          "-r", rate, "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuv444p16le", str(path)],
                         stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    p.wait()


class DustEraseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_each_frames_dust_comes_off_and_nothing_else(self):
        import cv2
        from core.depthedit.project import LayerSettings, MaskStore
        from core.depthedit.keys import KeyframeTrack
        from core.removal import erase as E, static_reflect as S
        h, w, n = 96, 160, 20
        rng = np.random.default_rng(4)
        specks = [(30, 40), (50, 90), (70, 120), (40, 130)]
        frames, truth = [], []
        for i in range(n):
            # the world behind the glass moves (a scrolling texture), grain on top
            base = np.roll(cv2.GaussianBlur(rng.uniform(0.15, 0.35, (h, w)).astype(np.float32), (0, 0), 3), i * 3, 1)
            img = np.repeat(base[..., None], 3, -1) + rng.normal(0, 0.004, (h, w, 3)).astype(np.float32)
            dust = np.zeros((h, w), np.float32)
            gain = 0.6 + 1.2 * (i % 5) / 4.0                 # the light on the dust changes: 0.6x .. 1.8x
            for y, x in specks:
                dust[y - 1:y + 2, x - 1:x + 2] = 0.04 * gain
            truth.append(dust)
            frames.append((np.clip(img + dust[..., None], 0, 1) * 65535).astype(np.uint16))
        clip = self.tmp / "car.mkv"
        _write(clip, frames)
        glass = np.zeros((h, w), bool)
        glass[15:85, 20:150] = True
        store = MaskStore(h, w)
        for i in range(n):
            store.set(i, glass)
        st = LayerSettings()
        st.mode = "Erase"
        layer = E.EraseLayer(st, KeyframeTrack(), store, None)
        original = S.erase_dust(str(clip), [layer], lambda f, t: None, threading.Event(), size=5, feather=1.0)
        self.assertTrue(os.path.isfile(original))
        rec = json.load(open(E.record_path(str(clip)), encoding="utf-8"))
        self.assertEqual(rec["method"], "dust")
        out = [f.astype(np.float32) / 65535 for f in E._reader(str(clip), w, h, "rgb48le")]
        orig = [f.astype(np.float32) / 65535 for f in frames]
        for i in (0, 4, 9):                                   # dim, bright, middling dust
            left = [out[i][y, x].mean() - out[i][y, x + 6].mean() for y, x in specks]
            self.assertLess(max(left), 0.008, f"frame {i}: dust left behind {left}")
        away = ~(cv2.dilate(glass.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
        # off the glass: only the write-out's own loss (HEVC 10-bit on a grain test picture), the same near the
        # glass as far from it -- nothing taken off there
        err = np.abs(out[3] - orig[3]).mean(-1)
        ring = (cv2.dilate(glass.astype(np.uint8), np.ones((25, 25), np.uint8)) > 0) & away
        self.assertLess(float(err[away].mean()), 0.004, "the clip changed off the glass")
        self.assertLess(abs(float(err[ring].mean()) - float(err[away & ~ring].mean())), 0.0015,
                        (float(err[ring].mean()), float(err[away & ~ring].mean())))

    def test_dust_counts_as_add_only(self):
        from core.removal import putback as R
        self.assertEqual(R.modes_for({"method": "dust"}), ("Add",))
        self.assertIn("Glass Plane", R.ADD_DEPTHS)


class EraseDepthTest(unittest.TestCase):
    """An Erase layer fills the depth only for ROSE (user, 2026-10-17: with Dust, the windows' depth was filled from
    the car frame -- streaks -- and the road behind the glass lost its depth)."""

    def test_dust_and_static_leave_the_depth(self):
        from core.depthedit import edit_ops as O
        v = np.tile(np.linspace(0.2, 0.8, 80, dtype=np.float32), (60, 1))
        v[20:40, 30:50] = 0.05                                   # the road seen through a window: far
        m = np.zeros((60, 80), bool)
        m[20:40, 30:50] = True
        st = O.LayerSettings()
        st.mode = "Erase"
        saved = O.ERASE_FILLS_DEPTH
        self.addCleanup(setattr, O, "ERASE_FILLS_DEPTH", saved)
        O.ERASE_FILLS_DEPTH = True
        self.assertGreater(float(O.apply_layer(v, m, st, v)[30, 40]), 0.2, "ROSE: the object's depth filled")
        O.ERASE_FILLS_DEPTH = False
        np.testing.assert_array_equal(O.apply_layer(v, m, st, v), v)

    def test_an_erased_clip_goes_by_its_own_erase(self):
        """Erase's Method is one setting for every clip: a ROSE-erased clip opened with Dust chosen kept the
        object's shape in its depth map (review 2026-10-17). An erased clip goes by its record; one not
        erased yet by the Method."""
        from core.removal import erase as E
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        clip = os.path.join(tmp, "shot.mp4")
        original = os.path.join(tmp, "shot.original.mp4")
        for p in (clip, original):
            with open(p, "wb") as f:
                f.write(b"x")
        for method in ("ROSE", "Dust", "Static"):
            self.assertEqual(P_.erase_fills_depth(clip, method), method == "ROSE", f"not erased, {method} chosen")
        for rec_method, fills in (("rose", True), ("dust", False), ("static", False)):
            os.makedirs(os.path.dirname(E.record_path(clip)), exist_ok=True)
            with open(E.record_path(clip), "w", encoding="utf-8") as f:
                json.dump({"method": rec_method, "live": E._file_id(clip), "original": original}, f)
            for method in ("ROSE", "Dust", "Static"):
                self.assertEqual(P_.erase_fills_depth(clip, method), fills, f"a {rec_method} erase, {method} chosen")


class GlassPlaneTest(unittest.TestCase):
    def test_each_window_its_own_plane(self):
        h, w = 120, 200
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        codes = np.full((h, w), 200.0, np.float32)
        codes[:, :100] = 300 + 3.0 * xx[:, :100]               # the car around window A slopes left to right
        codes[:, 100:] = 900 - 2.0 * (xx[:, 100:] - 100)       # around window B the other way
        seen_through = codes.copy()
        mask = np.zeros((h, w), bool)
        mask[30:90, 20:80] = True                               # window A
        mask[30:90, 120:180] = True                             # window B
        seen_through[mask] = 50.0                               # through the glass: the road, far
        out = P.glass_planes(seen_through, mask)
        self.assertAlmostEqual(float(out[60, 25]), 300 + 3 * 25, delta=6)
        self.assertAlmostEqual(float(out[60, 75]), 300 + 3 * 75, delta=6)
        self.assertAlmostEqual(float(out[60, 125]), 900 - 2 * 25, delta=6)
        self.assertAlmostEqual(float(out[60, 175]), 900 - 2 * 75, delta=6)
        self.assertEqual(float(out[5, 5]), float(seen_through[5, 5]), "off the windows: the map as it is")


class GlassWarpTest(unittest.TestCase):
    def layer(self, plane_codes):
        gl = object.__new__(P.GlassLayer)
        gl.geo = P.Geometry(vid={}, track=None,
                            kf_base={"max_disp": 30.0, "zero_disparity_anchor_val": 0.5, "depth_gamma": 1.0,
                                     "boost_thresh": 0.85, "boost_amount": 0.0, "boost_curve": 2.0, "boost_knee": 0.0,
                                     "boost_prefilter_radius": 0, "boost_prefilter_eps": 1e-3, "pop_amount": 0.0},
                            assume_raw=True, gmin=0.0, gmax=1.0, max_raw=1023.0, tv_comp=1.0, dual_eye=False,
                            dual_eye_bias=0.5, input_bias=0.0, peak_raw=1.0, match_depth_res=True)
        gl.dh, gl.dw = plane_codes.shape
        gl.n = 1
        gl._warp = {}
        gl.plane = lambda i: plane_codes
        return gl

    def moved(self, gl, conv=0.0, disp=1.0, x=100):
        import torch
        h, w = 60, 400
        lay = torch.zeros(3, h, w)
        lay[:, 30, x] = 0.5
        a = torch.ones(1, h, w)
        out = gl.warp(0, lay, a, conv, disp)["right"]
        cols = torch.nonzero(out[0, 30].abs() > 0.05).flatten()
        return float(cols.float().mean()) - x

    def test_on_the_plane_with_convergence_and_disparity(self):
        h, w = 60, 400
        near = np.tile(np.linspace(0.3, 0.9, w, dtype=np.float32) * 1023, (h, 1))   # far on the left, near on the right
        gl = self.layer(near)
        left_end, right_end = self.moved(gl, x=40), self.moved(gl, x=360)
        want = lambda x: (near[0, x] / 1023 - 0.5) * 30 / 1000 * w      # Splatting's warp of the plane's depth there
        self.assertAlmostEqual(abs(left_end), abs(want(40)), delta=1.0)
        self.assertAlmostEqual(abs(right_end), abs(want(360)), delta=1.0)
        self.assertNotEqual(np.sign(left_end), np.sign(right_end), "the plane didn't slope through the screen")
        base = self.moved(gl, x=200)
        self.assertNotAlmostEqual(self.moved(gl, conv=0.3, x=200), base, delta=0.5, msg="Convergence did nothing")
        self.assertAlmostEqual(self.moved(gl, disp=2.0, x=360), 2 * right_end, delta=1.0)


if __name__ == "__main__":
    unittest.main()
