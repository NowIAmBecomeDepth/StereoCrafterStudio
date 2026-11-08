"""core.depthblend: Depth Map's Blend tab (user, 2026-10-26), the parts that need no card.

* the fit: recovers a scale and shift either way round; never a negative scale (near would turn far);
  per frame follows a change the clip-wide fit averages away;
* the blend: Mix's ends are the two maps, Layout + Detail with nothing to add is the base map;
* where the maps are, and what a new map of each kind does to the one in its place (core.vda.output).

    python -m unittest studio.tests.test_depthblend
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")      # the maths on the CPU here
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import numpy as np  # noqa: E402

from core import depthblend as B  # noqa: E402
from core.vda import output as OUT  # noqa: E402


def sums(x, y):
    x, y = np.asarray(x, np.float64).ravel(), np.asarray(y, np.float64).ravel()
    return np.array([x.size, x.sum(), y.sum(), (x * x).sum(), (y * y).sum(), (x * y).sum()])


class FitTest(unittest.TestCase):
    def test_recovers_scale_and_shift_either_way(self):
        x = np.linspace(0, 1, 500)                    # VDA
        y = 2.0 * x - 0.4                             # DepthCrafter
        a, b = B._fit(sums(x, y), "DepthCrafter")     # VDA onto DepthCrafter
        self.assertAlmostEqual(a, 2.0, places=6); self.assertAlmostEqual(b, -0.4, places=6)
        a, b = B._fit(sums(x, y), "VDA")              # DepthCrafter onto VDA
        self.assertAlmostEqual(a, 0.5, places=6); self.assertAlmostEqual(b, 0.2, places=6)

    def test_never_a_negative_scale(self):
        x = np.linspace(0, 1, 500)
        a, _b = B._fit(sums(x, 1.0 - x), "DepthCrafter")   # inverted: least squares says -1
        self.assertGreater(a, 0)

    def test_per_frame_follows_what_per_clip_averages(self):
        x = np.linspace(0, 1, 100)
        stats = np.array([sums(x, (1.0 if i < 30 else 3.0) * x) for i in range(60)])
        clip = B.fits(stats, "DepthCrafter", "Per clip")
        frame = B.fits(stats, "DepthCrafter", "Per frame")
        self.assertTrue(np.allclose(clip[:, 0], clip[0, 0]))
        self.assertAlmostEqual(frame[0, 0], 1.0, places=6)
        self.assertAlmostEqual(frame[-1, 0], 3.0, places=6)
        self.assertTrue((np.diff(frame[:, 0]) >= -1e-9).all())          # eased, not a jump back and forth
        self.assertTrue(np.allclose(B.fits(stats, "DepthCrafter", "Off"), [1.0, 0.0]))


class BlendMathTest(unittest.TestCase):
    def setUp(self):
        import torch
        self.dc = torch.rand(1, 1, 32, 48)
        self.vda = torch.rand(1, 1, 32, 48)

    def _opts(self, **kw):
        o = {"mode": "Mix", "mix": 0.5, "base": "DepthCrafter", "detail_size": 4.0, "detail_amount": 1.0}
        o.update(kw)
        return o

    def test_mix_ends_are_the_two_maps(self):
        self.assertTrue(np.allclose(B.blend(self.dc, self.vda, (1.0, 0.0), self._opts(mix=0.0)), self.dc))
        self.assertTrue(np.allclose(B.blend(self.dc, self.vda, (1.0, 0.0), self._opts(mix=1.0)), self.vda))

    def test_mix_fits_the_other_map_onto_the_base(self):
        out = B.blend(self.dc, self.vda, (0.5, 0.1), self._opts(mix=1.0))
        self.assertTrue(np.allclose(out, (self.vda * 0.5 + 0.1).clamp(0, 1), atol=1e-6))

    def test_layout_and_detail_with_no_detail_is_the_base_blurred(self):
        out = B.blend(self.dc, self.vda, (1.0, 0.0), self._opts(mode="Layout + Detail", detail_amount=0.0))
        self.assertTrue(np.allclose(out, B._blur(self.dc, 4.0).clamp(0, 1), atol=1e-6))

    def test_layout_and_detail_of_one_map_with_itself_is_that_map(self):
        out = B.blend(self.dc, self.dc.clone(), (1.0, 0.0), self._opts(mode="Layout + Detail"))
        self.assertTrue(np.allclose(out, self.dc, atol=1e-5))


class ManualControlsTest(unittest.TestCase):
    """By Depth, Edge Keep, Detail Limit / Noise Floor, Edge Band (user, 2026-11-07)."""

    def _opts(self, **kw):
        o = {"mode": "Mix", "mix": 0.5, "base": "DepthCrafter", "detail_size": 4.0, "detail_amount": 1.0}
        o.update(kw)
        return o

    def test_off_they_change_nothing(self):
        import torch
        dc, vda = torch.rand(1, 1, 32, 48), torch.rand(1, 1, 32, 48)
        for mode in ("Mix", "Layout + Detail"):
            plain = B.blend(dc.clone(), vda.clone(), (1.0, 0.0), self._opts(mode=mode))
            off = B.blend(dc.clone(), vda.clone(), (1.0, 0.0), self._opts(
                mode=mode, mix_by_depth=False, edge_keep=0.0, detail_limit=0.0, detail_floor=0.0, edge_band=0.0))
            self.assertTrue(torch.equal(plain, off), mode)

    def test_by_depth_takes_near_mix_up_close_and_far_mix_far_away(self):
        import torch
        dc = torch.full((1, 1, 32, 64), 0.1)
        dc[..., 32:] = 0.9                                  # the right half near
        vda = dc + 0.05                                     # VDA's own values: told apart from DepthCrafter's
        out = B.blend(dc, vda, (1.0, 0.0), self._opts(mix_by_depth=True, mix_near=1.0, mix_far=0.0,
                                                      mix_split=0.5, mix_soft=0.1))
        self.assertTrue(torch.allclose(out[..., :24], dc[..., :24]), "far: all DepthCrafter")
        self.assertTrue(torch.allclose(out[..., 40:], vda[..., 40:]), "near: all VDA")
        m = B.depth_mix(torch.full((1, 1, 8, 8), 0.5), {"mix_near": 0.8, "mix_far": 0.2, "mix_split": 0.5, "mix_soft": 0.2})
        self.assertTrue(torch.allclose(m, torch.tensor(0.5)), "at Split, halfway")

    def _step(self):
        import torch
        x = torch.arange(64, dtype=torch.float32)
        base = torch.where(x < 32, 0.2, 0.8).view(1, 1, 1, 64).expand(1, 1, 16, 64).clone()
        other = torch.where(x < 32, 0.35, 0.65).view(1, 1, 1, 64).expand(1, 1, 16, 64).clone()
        return base, other

    def test_edge_keep_leaves_no_halo_beside_a_step(self):
        base, other = self._step()                         # the same edge, another height: no real detail
        halo = {}
        for keep in (0.0, 1.0):
            out = B.blend(base, other, (1.0, 0.0), self._opts(mode="Layout + Detail", detail_size=6.0, edge_keep=keep))
            halo[keep] = float((out - base).abs().max())
        self.assertGreater(halo[0.0], 0.05, "the plain blur's halo")
        self.assertLess(halo[1.0], 0.01, str(halo))

    def test_detail_limit_caps_softly_and_noise_floor_drops_wiggles(self):
        import torch
        d = torch.tensor([-0.2, -0.004, 0.0, 0.004, 0.01, 0.2])
        f = B.shape_detail(d, 0.0, 0.5)                     # floor 0.5%
        self.assertTrue(torch.allclose(f, torch.tensor([-0.195, 0.0, 0.0, 0.0, 0.005, 0.195])), f)
        c = B.shape_detail(d, 5.0, 0.0)                     # limit 5%
        self.assertLess(float(c.abs().max()), 0.05)
        self.assertAlmostEqual(float(c[3]), 0.004, places=4, msg="small detail as it was")
        self.assertTrue(torch.equal(B.shape_detail(d, 0.0, 0.0), d))

    def test_edge_band_takes_the_other_map_at_the_left_and_top(self):
        import torch
        dc, vda = torch.zeros(1, 1, 40, 60), torch.ones(1, 1, 40, 60)
        out = B.blend(dc, vda, (1.0, 0.0), self._opts(mix=0.0, edge_band=10, edge_band_sides="Left + Top"))
        self.assertGreater(float(out[0, 0, 20, 0]), 0.95, "the left edge: the other map")
        self.assertGreater(float(out[0, 0, 0, 30]), 0.95, "the top edge")
        self.assertEqual(float(out[0, 0, 20, 30]), 0.0, "inside: the blend")
        self.assertEqual(float(out[0, 0, 39, 30]), 0.0, "the bottom left alone")
        self.assertEqual(float(out[0, 0, 20, 12]), 0.0, "past the band")
        left = B.blend(dc, vda, (1.0, 0.0), self._opts(mix=0.0, edge_band=10, edge_band_sides="Left"))
        self.assertEqual(float(left[0, 0, 0, 30]), 0.0, "Left only: the top alone")
        every = B.blend(dc, vda, (1.0, 0.0), self._opts(mix=0.0, edge_band=10, edge_band_sides="All edges"))
        self.assertGreater(float(every[0, 0, 39, 30]), 0.95)
        self.assertGreater(float(every[0, 0, 20, 59]), 0.95)

    def test_the_settings(self):
        from studio.stages.blend import settings as BS
        got = BS.normalise({"mix_by_depth": "true", "detail_limit": 99, "edge_band": -4, "edge_band_sides": "Nope"})
        self.assertEqual((got["mix_by_depth"], got["detail_limit"], got["edge_band"], got["edge_band_sides"]),
                         (True, 20.0, 0.0, "Left + Top"))
        d = BS.normalise({})
        self.assertEqual((d["mix_by_depth"], d["edge_keep"], d["detail_limit"], d["detail_floor"], d["edge_band"]),
                         (False, 0.0, 0.0, 0.0, 0.0), "off by default: a blend as before")
        self.assertIn("All edges", BS.choices()["edge_band_sides"])


class SmoothingLevelsTest(unittest.TestCase):
    """Detail Smoothing and Levels (user, 2026-11-07: "go ahead with 5-7")."""

    def test_smoothing_evens_out_shimmer_but_not_motion(self):
        import torch
        torch.manual_seed(0)
        base = torch.zeros(1, 1, 8, 8)
        shimmer = [base + 0.004 * torch.randn(1, 1, 8, 8) for _ in range(7)]
        out = B.smooth_detail(shimmer, 3, 3)
        self.assertLess(float(out.std()), 0.6 * float(shimmer[3].std()), "shimmer evened out")
        moving = [base.clone() for _ in range(7)]
        moving[3][..., 2:4] = 0.2                          # an edge here this frame only: motion
        out = B.smooth_detail(moving, 3, 3)
        self.assertGreater(float(out[..., 2:4].min()), 0.19, "the edge isn't averaged away")

    def test_smoothing_weights_by_the_full_radius_at_a_clips_ends(self):
        import torch
        d = [torch.full((1, 1, 2, 2), v) for v in (0.0, 0.003)]     # the clip's first frame and the next
        got = float(B.smooth_detail(d, 0, 3)[0, 0, 0, 0])
        w0, w1 = 4.0, 3.0 * math.exp(-(0.003 ** 2) / (2 * B.SMOOTH_SIGMA ** 2))
        self.assertAlmostEqual(got, w1 * 0.003 / (w0 + w1), places=6)

    def test_smoothing_is_layout_and_detail_only(self):
        self.assertEqual(B.smoothing_radius({"mode": "Mix", "detail_smooth": 3}), 0)
        self.assertEqual(B.smoothing_radius({"mode": "Layout + Detail", "detail_smooth": 3}), 3)

    def test_levels(self):
        import torch
        t = torch.tensor([0.0, 0.1, 0.5, 0.9, 1.0])
        self.assertTrue(torch.equal(B.levels(t, {}), t), "the defaults leave it")
        got = B.levels(t, {"level_black": 0.1, "level_white": 0.9})
        self.assertTrue(torch.allclose(got, torch.tensor([0.0, 0.0, 0.5, 1.0, 1.0])))
        up = B.levels(t, {"level_gamma": 2.0})
        self.assertGreater(float(up[2]), 0.5, "above 1: the middle nearer")
        self.assertEqual((float(up[0]), float(up[4])), (0.0, 1.0))

    def test_blend_applies_levels_last(self):
        import torch
        dc, vda = torch.full((1, 1, 4, 4), 0.5), torch.full((1, 1, 4, 4), 0.5)
        o = {"mode": "Mix", "mix": 0.5, "base": "DepthCrafter", "detail_size": 4.0, "detail_amount": 1.0,
             "level_white": 0.5}
        self.assertTrue(torch.allclose(B.blend(dc, vda, (1.0, 0.0), o), torch.ones(1)))


class OwnSettingsTest(unittest.TestCase):
    """A clip's own settings (user, 2026-11-07): kept beside the maps, over the shared ones in a run."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_kept_beside_the_maps_and_read_back(self):
        from studio.stages.blend import settings as BS
        self.assertEqual(BS.load_clip_settings(str(self.d)), {})
        BS.save_clip_settings(str(self.d), {"a": {"mix": 0.9, "depth_folder": "x", "mode": "Mix"}})
        got = BS.load_clip_settings(str(self.d))
        self.assertEqual(got, {"a": {"mix": 0.9, "mode": "Mix"}}, "only a clip's own kind of setting")
        BS.save_clip_settings(str(self.d), {})
        self.assertFalse((self.d / BS.CLIPS_FILE).exists(), "no clip with its own: no file")
        (self.d / BS.CLIPS_FILE).write_text("{oops", encoding="utf-8")
        self.assertEqual(BS.load_clip_settings(str(self.d)), {})

    def test_a_clips_own_over_the_shared_ones(self):
        from studio.stages.blend import settings as BS
        shared = BS.normalise({"mode": "Mix", "mix": 0.2, "other": "DVD", "base": "DVD"})
        o = BS.opts_for(shared, {"mix": 0.8, "other": "VDA", "base": "VDA"})
        self.assertEqual((o["mix"], o["other"], o["base"]), (0.8, "DVD", "DVD"),
                         "its Mix; the shared Other Map, and its Base follows it")

    def test_the_runner_uses_each_clips_own(self):
        from unittest import mock
        from studio.stages.blend import runner as R
        from studio.stages.blend import settings as BS
        from studio.stages.vda.runner import Hooks
        seen = {}

        def fake(pair, opts, *a, **k):
            seen[pair["clip"]] = opts["mix"]
            return {"frames": 1, "set_aside": "", "size": (2, 2), "note": ""}
        pairs = [{"clip": c, "dc": "", "other": "", "out": ""} for c in ("a", "b")]
        run = R.BlendRunner(BS.snapshot({"mix": 0.3}), Hooks(), __import__("threading").Event(),
                            per_clip={"b": {"mix": 0.9}})
        with mock.patch.object(B, "blend_clip", fake):
            run.run_batch(pairs)
        self.assertEqual(seen, {"a": 0.3, "b": 0.9})


def _clip(path, tag=""):
    tags = ["-metadata", f"comment={tag}"] if tag else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x32:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", *tags, str(path)], check=True)


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="blend-folder-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_pairs_where_vdas_rerun_leaves_them(self):
        (self.d / "DepthCrafter").mkdir(); (self.d / "VDA").mkdir()
        _clip(self.d / "a_depth.mp4", OUT.TAG)                   # VDA's in place, DepthCrafter's set aside
        _clip(self.d / "DepthCrafter" / "a_depth.mp4")
        _clip(self.d / "b_depth.mp4")                            # DepthCrafter's in place, VDA's beside
        _clip(self.d / "VDA" / "b_depth.mp4", OUT.TAG)
        _clip(self.d / "c_depth.mp4")                            # one map only
        got = {p["clip"]: (os.path.relpath(p["dc"], self.d), os.path.relpath(p["other"], self.d)) for p in B.pairs(str(self.d))}
        self.assertEqual(got, {"a": (os.path.join("DepthCrafter", "a_depth.mp4"), "a_depth.mp4"),
                               "b": ("b_depth.mp4", os.path.join("VDA", "b_depth.mp4"))})

    def test_pairs_with_dvds_map_instead(self):
        # Other Map DVD (user, 2026-11-07): DVD's map in place or in DVD/; VDA's maps are not its pairs
        (self.d / "DepthCrafter").mkdir(); (self.d / "DVD").mkdir(); (self.d / "VDA").mkdir()
        _clip(self.d / "a_depth.mp4", OUT.DVD_TAG)
        _clip(self.d / "DepthCrafter" / "a_depth.mp4")
        _clip(self.d / "b_depth.mp4")
        _clip(self.d / "DVD" / "b_depth.mp4", OUT.DVD_TAG)
        _clip(self.d / "c_depth.mp4")
        _clip(self.d / "VDA" / "c_depth.mp4", OUT.TAG)
        got = {p["clip"]: (os.path.relpath(p["dc"], self.d), os.path.relpath(p["other"], self.d))
               for p in B.pairs(str(self.d), other="DVD")}
        self.assertEqual(got, {"a": (os.path.join("DepthCrafter", "a_depth.mp4"), "a_depth.mp4"),
                               "b": ("b_depth.mp4", os.path.join("DVD", "b_depth.mp4"))})
        self.assertEqual([p["clip"] for p in B.pairs(str(self.d))], ["c"])

    def test_base_and_size_follow_the_other_map(self):
        from studio.stages.blend import settings as BS
        self.assertEqual(BS.choices("DVD")["base"], ["DepthCrafter", "DVD"])
        self.assertEqual(BS.choices("VDA")["size"], ["Larger", "DepthCrafter", "VDA"])
        got = BS.normalise({"other": "DVD", "base": "VDA", "size": "VDA"})
        self.assertEqual((got["base"], got["size"]), ("DVD", "DVD"))
        got = BS.normalise({"other": "DVD", "base": "DepthCrafter", "size": "Larger"})
        self.assertEqual((got["base"], got["size"]), ("DepthCrafter", "Larger"))
        self.assertEqual(BS.normalise({})["other"], "VDA")          # a blend.json from before: VDA's, as it was

    def test_each_kind_sets_another_aside_into_its_folder(self):
        root = self.d / "a_depth.mp4"
        _clip(root, OUT.BLEND_TAG)
        self.assertEqual(OUT.kind_of(str(root)), "blend")
        moved = OUT.set_aside(str(root), "vda")                  # a VDA rerun over a blend
        self.assertEqual(Path(moved).parent.name, "Blend")
        _clip(root, OUT.TAG)
        self.assertIsNone(OUT.set_aside(str(root), "vda"))       # its own kind: replaced
        moved = OUT.set_aside(str(root), "blend")                # a blend over VDA's
        self.assertEqual(Path(moved).parent.name, "VDA")


if __name__ == "__main__":
    unittest.main()
