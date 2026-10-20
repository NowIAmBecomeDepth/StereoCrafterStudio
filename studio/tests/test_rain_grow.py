"""Particles: Grow (user, 2026-10-07: "can we add a dilate/erode slider for the particle
cutouts too"). Grown, the cutouts reach further but never take more than the frame's own
light above the background there; shrunk, their edges are dropped; 0 is untouched. A render
(extract_lengths) is the preview's parts with Grow applied; the GPU path the same. The
setting reaches the render, the sidecar, the preview's frame-buffer key and the page, and a
preview doesn't separate again for it.

    python -m unittest studio.tests.test_rain_grow
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.splatting.rain as R  # noqa: E402
from studio.tests.test_sparks import clip  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def parts(device="cpu", grow=0):
    win, _ = clip()
    s = R.RainSettings.from_values(mode="static", particle="sparks", grow=grow)
    return R.RainExtractor(s, 480, device=device), win


class Grow(unittest.TestCase):

    def test_settings(self):
        self.assertEqual(R.RainSettings.from_values().grow, 0)
        self.assertEqual(R.RainSettings.from_values(grow="3").grow, 3)
        self.assertEqual(R.RainSettings.from_values(grow=99).grow, R.GROW_MAX)
        self.assertEqual(R.RainSettings.from_values(grow=-99).grow, -R.GROW_MAX)
        self.assertEqual(R.RainSettings.from_values(grow="x").grow, 0)

    def test_zero_untouched(self):
        ext, win = parts()
        rain, length, src = ext.extract_parts(win, [2])[0]
        r2, l2 = R.grow_cutout(rain, length, src, 0)
        self.assertIs(r2, rain)
        self.assertIs(l2, length)

    def test_grown_reaches_further_within_the_frames_light(self):
        ext, win = parts()
        rain, length, src = ext.extract_parts(win, [2])[0]
        r2, l2 = R.grow_cutout(rain, length, src, 3, 480 / 1920.0 * 4)    # 3 px at 1920 = 3 px here x4
        on, on2 = length > 0, l2 > 0
        self.assertTrue((on2 >= on).all(), "the pieces stay")
        self.assertGreater(on2.sum(), on.sum() * 1.2, "and reach further")
        self.assertTrue(np.array_equal(r2[on], rain[on]), "what was taken is unchanged")
        self.assertTrue((r2 <= np.maximum(src, rain) + 1e-6).all(), "never more than the frame's light")
        self.assertTrue((r2[~on2] == 0).all())

    def test_shrunk_drops_the_edges(self):
        ext, win = parts()
        rain, length, src = ext.extract_parts(win, [2])[0]
        r2, l2 = R.grow_cutout(rain, length, src, -1, 1.0)
        on, on2 = length > 0, l2 > 0
        self.assertTrue((on2 <= on).all())
        self.assertLess(on2.sum(), on.sum())
        self.assertTrue(np.array_equal(r2[on2], rain[on2]))
        self.assertTrue((r2[~on2] == 0).all())

    def test_render_is_the_preview_with_grow(self):
        ext, win = parts(grow=4)
        rain, length = ext.extract_lengths(win, [2])[0]
        ext0, _ = parts(grow=4)
        r0, l0, src = ext0.extract_parts(win, [2])[0]
        r1, l1 = R.grow_cutout(r0, l0, src, 4, ext0.scale)
        self.assertTrue(np.array_equal(rain, r1) and np.array_equal(length, l1))

    def test_gpu_the_same(self):
        try:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("no CUDA")
        except Exception as e:
            self.skipTest(str(e))
        for grow in (0, 4, -2):
            ec, win = parts("cpu", grow)
            eg, _ = parts("cuda", grow)
            rc, lc = ec.extract_lengths(win, [2])[0]
            rg, lg = eg.extract_lengths(win, [2])[0]
            iou = ((lc > 0) & (lg > 0)).sum() / max(((lc > 0) | (lg > 0)).sum(), 1)
            self.assertGreater(iou, 0.97, (grow, iou))


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        vals = dict(S.DEFAULTS)
        self.assertEqual(vals["rain_grow"], 0)
        vals["rain_grow"] = -3
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual(int(ps.rain_grow), -3)
        self.assertEqual(_saved_or({"rain_grow": 2}, "rain_grow", -3), 2)

    def test_in_the_sidecar(self):
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertIn("rain_grow", SidecarConfigManager.SIDECAR_KEY_MAP)
        self.assertIn("rain_grow", SidecarConfigManager.KEEP_GUI_WHEN_MISSING)

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        self.assertNotEqual(buf._generate_signature_from_params({"rain_grow": 0}, "x.mp4"),
                            buf._generate_signature_from_params({"rain_grow": 3}, "x.mp4"))

    def test_the_preview_keeps_its_separation(self):
        """Grow isn't in the preview's separation cache key: moving it only re-applies it."""
        src = (REPO / "core/splatting/rain.py").read_text(encoding="utf-8")
        start = src.index("    key = (_clip_key(pv), id(reader)")          # the separation cache's key
        key = src[start:src.index(")\n", start)]
        self.assertNotIn("grow", key)

    def test_on_the_page(self):
        qml = (REPO / "studio/qml/Studio/SplattingPage.qml").read_text(encoding="utf-8")
        self.assertIn('key: "rain_grow"', qml)


if __name__ == "__main__":
    unittest.main()
