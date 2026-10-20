"""Pop-Out and the reworked Edge Clean (user, 2026-10-01), on the CPU.

The real warp (core.splatting.forward_warp.execute_forward_warp) with a stand-in
projector that only records the disparity map it is handed -- no GPU.

    python -m unittest studio.tests.test_pop_out
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.depth_boost import pop_out_extra  # noqa: E402
from core.splatting.forward_warp import execute_forward_warp  # noqa: E402

W, H = 240, 80
M, C, K_PX = 30.0, 0.5, None          # Disparity, Convergence


def _scene(soft_px=4.0):
    """Depth (1,1,H,W): a background behind the screen, a subject just in front,
    and a small near object with a SOFT depth edge (as DepthCrafter's are);
    its picture has a SHARP edge there."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    depth = 0.1 + 0.35 * x / W                                   # 0.10..0.45: all behind the screen
    depth[:, 40:90] = 0.6                                         # the subject
    r = np.hypot(x - 170, y - 40)
    near = np.clip((26 - r) / soft_px + 0.5, 0, 1)               # a soft disc
    depth = np.maximum(depth, 0.95 * near + depth * (1 - near))
    rgb = np.full((3, H, W), 0.35, np.float32)
    rgb[:, r < 26] = 0.9                                          # the picture's edge is sharp
    return torch.from_numpy(depth)[None, None], torch.from_numpy(rgb)[None]


class _Recorder:
    def __call__(self, src, disp):
        return src, torch.zeros_like(disp)


def _disp(depth, rgb, **kw):
    probe = {}
    execute_forward_warp(_Recorder(), rgb, depth, target_width=W, max_disp=M, zero_disparity_anchor_val=C,
                         disp_probe=probe, depth_peak=float(depth.max()), **kw)
    return probe["right"][0, 0].numpy()


def _px_per_unit():
    return 2.0 * (M / 20.0 / 100.0) * W


class PopOutTests(unittest.TestCase):
    def setUp(self):
        self.depth, self.rgb = _scene()
        self.d = self.depth[0, 0].numpy()
        self.base = _disp(self.depth, self.rgb)

    def test_off_the_shift_is_the_plain_formula(self):
        self.assertTrue(np.allclose(self.base, (self.d - C) * _px_per_unit(), atol=1e-5))

    def test_on_and_behind_the_screen_nothing_moves(self):
        pop = _disp(self.depth, self.rgb, pop_amount=10.0)
        behind = self.d <= C
        self.assertTrue(behind.any())
        self.assertTrue(np.array_equal(pop[behind], self.base[behind]))

    def test_the_nearest_gets_the_full_pop_a_subject_a_little(self):
        pop = _disp(self.depth, self.rgb, pop_amount=10.0)
        extra = pop - self.base
        peak = self.d.max()
        full = (10.0 / M) * _px_per_unit()
        self.assertAlmostEqual(float(extra[self.d == peak].max()), full, places=4)
        u = (0.6 - C) / (peak - C)
        self.assertAlmostEqual(float(extra[8, 60]), u * u * full, places=4)     # the subject
        self.assertLess(float(extra[8, 60]), 0.05 * full)

    def test_depth_order_is_kept(self):
        pop = _disp(self.depth, self.rgb, pop_amount=20.0)
        order = np.argsort(self.d.ravel(), kind="stable")
        self.assertTrue(np.all(np.diff(pop.ravel()[order]) >= -1e-5))

    def test_pop_out_stretches_outlines_less_than_boost_for_the_same_pop(self):
        boost = _disp(self.depth, self.rgb, boost_amount=10.0, boost_thresh=0.85, boost_curve=2.0)
        pop = _disp(self.depth, self.rgb, pop_amount=10.0)
        # same pop at the nearest pixel...
        self.assertAlmostEqual(float((boost - self.base).max()), float((pop - self.base).max()), places=3)
        # ...and how sharply the shift changes across the object's soft outline:
        # what stretches it in the warp
        row = 40
        g_boost = np.abs(np.diff(boost[row, 130:210])).max()
        g_pop = np.abs(np.diff(pop[row, 130:210])).max()
        g_base = np.abs(np.diff(self.base[row, 130:210])).max()
        self.assertLess(g_pop, g_boost)
        self.ratio = (g_boost / g_base, g_pop / g_base)

    def test_edge_clean_evens_the_push_up_to_the_picture_edge(self):
        # What warps an outline: the object's rim, where its depth tapers off,
        # gets less push than its middle (and a little leaks onto the
        # background beyond). Edge Clean shapes the extra pop along the
        # picture's own (sharp) edge instead.
        depth, rgb = _scene(soft_px=6.0)
        base = _disp(depth, rgb)
        y, x = np.mgrid[0:H, 0:W]
        r = np.hypot(x - 170, y - 40)
        inside, rim, outside = r < 25, (r >= 21) & (r < 26), (r >= 26.5) & (r < 32)
        for amount in (dict(pop_amount=10.0), dict(boost_amount=10.0)):
            plain = _disp(depth, rgb, **amount) - base
            clean = _disp(depth, rgb, **amount, boost_prefilter_radius=24, boost_prefilter_eps=1e-3) - base
            self.assertGreaterEqual(float(clean.min()), -1e-6)
            rim_plain = plain[rim].mean() / plain[inside].max()
            rim_clean = clean[rim].mean() / clean[inside].max()
            self.assertGreater(rim_clean, rim_plain + 0.2, (amount, rim_plain, rim_clean))
            self.assertGreater(rim_clean, 0.9, amount)
            self.assertLess(clean[outside].mean() / clean[inside].max(), 0.02, amount)
            # behind the screen: exactly unchanged without Edge Clean (tested
            # above); with it, the filter's averaging reaches ~2 radii, so the
            # background beside a popped object can pick up a sliver -- far
            # below a pixel (measured 0.06 px here)
            behind = depth[0, 0].numpy() <= C
            self.assertLess(float(np.abs(clean[behind]).max()), 0.1, amount)

    def test_pop_out_extra_on_numpy_matches_torch(self):
        d = self.d
        a = pop_out_extra(d, 10.0, C, M, float(d.max()))
        b = pop_out_extra(torch.from_numpy(d), 10.0, C, M, float(d.max())).numpy()
        self.assertTrue(np.allclose(a, b, atol=1e-6))
        self.assertEqual(float(pop_out_extra(d, 0.0, C, M).max()), 0.0)


class PopOutReachesTheRenderTests(unittest.TestCase):
    """The dial's value gets to the batch render: Studio's setting into the
    processing settings, a clip's own sidecar over it, and a Pop-Out-only clip
    still gets the clip-peak anchor the render needs."""

    def test_the_setting_reaches_processing_settings(self):
        from core.splatting import ProcessingSettings
        from studio.stages.splatting import settings as S
        values = dict(S.DEFAULTS)
        values.update({"input_source_clips": "s", "input_depth_maps": "d", "output_splatted": "o", "pop_amount": 7.5})
        config = S.config_for_run(values)
        self.assertEqual(ProcessingSettings.from_config(config).depth_pop_amount, 7.5)

    def test_pop_only_clips_get_the_peak_anchor(self):
        from core.splatting.batch_processing import boost_kwargs_for
        kw = boost_kwargs_for({"pop_amount": 6.0, "depth_max_raw": 0.8})
        self.assertEqual((kw.get("pop_amount"), kw.get("boost_amount"), kw.get("depth_peak_raw")), (6.0, 0.0, 0.8))
        self.assertEqual(boost_kwargs_for({"pop_amount": 0.0, "boost_amount": 0.0}), {})

    def test_the_sidecar_key(self):
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertIn("pop_out", SidecarConfigManager.SIDECAR_KEY_MAP)


class OldSidecarTests(unittest.TestCase):
    """A sidecar saved before Detect Text's Grow / Soften / Clean existed leaves
    the page's values standing (bug sweep 2026-10-09: the loader filled them with
    0). One that saved them still wins. Pop-Out is per clip, like Boost (user,
    2026-10-09): a clip without a saved Pop-Out renders 0, never the page's; only
    Process Single Clip takes the page's."""

    def _resolve(self, sidecar: dict | None, single: bool = False):
        import json
        import os
        import tempfile
        from types import SimpleNamespace
        from core.common.sidecar_manager import SidecarConfigManager
        from core.splatting import ProcessingSettings
        from core.splatting.batch_processing import BatchProcessor
        from studio.stages.splatting import settings as S
        tmp = tempfile.mkdtemp(prefix="old-sidecar-")
        values = dict(S.DEFAULTS)
        values.update({"input_source_clips": tmp, "input_depth_maps": tmp, "output_splatted": tmp,
                       "pop_amount": 6.0, "submask_grow": 3.0, "submask_soften": 2.0, "submask_clean": 1.0})
        ps = ProcessingSettings.from_config(S.config_for_run(values))
        ps.sidecar_folder = tmp
        if sidecar is not None:
            with open(os.path.join(tmp, f"clip_depth{ps.sidecar_ext}"), "w", encoding="utf-8") as f:
                json.dump(sidecar, f)
        fake = SimpleNamespace(sidecar_manager=SidecarConfigManager(),
                               _resolve_depth_path=lambda *a: os.path.join(tmp, "clip_depth.mp4"))
        res = BatchProcessor._get_video_specific_settings(fake, os.path.join(tmp, "clip.mp4"), ps, single)
        return (res["pop_amount"], res["submask_grow"], res["submask_soften"], res["submask_clean"])

    def test_a_sidecar_from_before_them_keeps_the_page(self):
        old = {"convergence_plane": 0.6, "max_disparity": 25.0, "gamma": 1.0, "boost_amount": 0.0}
        self.assertEqual(self._resolve(old), (0.0, 3.0, 2.0, 1.0))

    def test_a_sidecar_that_saved_them_wins(self):
        new = {"convergence_plane": 0.6, "pop_out": 0.0, "submask_grow": 0.5, "submask_soften": 0.0,
               "submask_clean": 4.0}
        self.assertEqual(self._resolve(new), (0.0, 0.5, 0.0, 4.0))

    def test_no_sidecar_keeps_the_page_but_pop_out_is_0(self):
        self.assertEqual(self._resolve(None), (0.0, 3.0, 2.0, 1.0))

    def test_a_saved_pop_out_renders(self):
        self.assertEqual(self._resolve({"pop_out": 4.5})[0], 4.5)

    def test_process_single_clip_renders_the_page(self):
        self.assertEqual(self._resolve(None, single=True)[0], 6.0)

    def test_the_preview_zeroes_pop_out_on_each_clip(self):
        import inspect
        from studio.stages.splatting.preview_engine import SplattingPreviewEngine
        src = inspect.getsource(SplattingPreviewEngine.update_gui_from_sidecar)
        self.assertLess(src.index("self.pop_amount_var.set(0.0)"), src.index("sync_to_gui"))

    def test_the_preview_keeps_the_page_too(self):
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertNotIn("pop_out", SidecarConfigManager.KEEP_GUI_WHEN_MISSING)
        for k in ("submask_grow", "submask_soften", "submask_clean"):
            self.assertIsNone(SidecarConfigManager.SIDECAR_KEY_MAP[k][1], k)
            self.assertIn(k, SidecarConfigManager.KEEP_GUI_WHEN_MISSING, k)


class PeakFractionTests(unittest.TestCase):
    """Peak-anchored Boost / Pop (bug sweep 2026-10-09): the clip scan returns raw
    codes, the sidecar's depth_max_raw and the render take a fraction of the code
    range. The preview divides by the stream's range before saving; a raw peak
    never passed the "< 0.999" gate, so every clip rendered on the flat anchor."""

    def _scan(self, kind, name):
        from core.splatting.analysis_service import AnalysisService
        from studio.tests.checks import _fixtures
        clip = _fixtures.path(kind) / "depth" / name   # read only
        return AnalysisService.compute_clip_global_depth_stats(str(clip), None)

    def test_the_scan_returns_raw_codes_8_and_10_bit(self):
        for kind, name, top in (("small", "clipA_depth.mp4", 255.0), ("hd", "hd_depth.mp4", 1023.0)):
            lo, hi = self._scan(kind, name)
            self.assertGreater(hi, 1.0, f"{kind}: raw codes, not a fraction")
            self.assertLessEqual(hi, top, kind)
            self.assertLessEqual(hi / top, 1.0, kind)

    def test_the_preview_saves_a_fraction(self):
        import inspect
        from studio.stages.splatting.preview_engine import SplattingPreviewEngine
        src = inspect.getsource(SplattingPreviewEngine._preview_processing_callback)
        self.assertIn("_raw_peak = _hi / max(final_scaling_factor, 1.0)", src)
        self.assertNotIn("_raw_peak = float(_cn[1])\n", src)

    def test_a_fraction_anchors_the_render(self):
        from core.splatting.batch_processing import boost_kwargs_for
        kw = boost_kwargs_for({"pop_amount": 6.0, "depth_max_raw": 200.0 / 255.0})
        self.assertLess(kw["depth_peak_raw"], 0.999, "passes render_processor._peak_warp's gate")


if __name__ == "__main__":
    unittest.main()
