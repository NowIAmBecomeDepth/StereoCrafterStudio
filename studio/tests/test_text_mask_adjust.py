"""Text Grow / Text Soften / Text Clean (user, 2026-10-02): Splatting's own
adjustments to the Detect Text mask, apart from the depth map's Dilate / Blur.
What they do to a mask (Text Clean: to the depth map under it), and that the dial's value gets to the render (Studio setting ->
processing settings; a clip's sidecar over it) and into the preview's cache
key. CPU only.

    python -m unittest studio.tests.test_text_mask_adjust
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.subtitle_mask_adjust import adjust_mask, clean_depth, is_active  # noqa: E402


def _block():
    m = np.zeros((60, 100), np.uint8)
    m[25:35, 30:70] = 255
    return m


class AdjustTests(unittest.TestCase):
    def test_zero_is_untouched(self):
        m = _block()
        self.assertIs(adjust_mask(m, 0, 0), m)
        self.assertFalse(is_active(0.4, 0))           # rounds to 0 px

    def test_grow_widens_by_that_many_pixels(self):
        g = adjust_mask(_block(), 4, 0)
        rows = np.flatnonzero(g[:, 50] > 0)
        cols = np.flatnonzero(g[30] > 0)
        self.assertEqual((rows[0], rows[-1], cols[0], cols[-1]), (21, 38, 26, 73))

    def test_negative_shrinks(self):
        g = adjust_mask(_block(), -3, 0)
        rows = np.flatnonzero(g[:, 50] > 0)
        self.assertEqual((rows[0], rows[-1]), (28, 31))

    def test_soften_feathers_the_edge(self):
        m = np.zeros((60, 100), np.uint8)
        m[10:50, 30:70] = 255                               # big enough to keep a full middle
        g = adjust_mask(m, 0, 3)
        self.assertEqual(int(g[30, 50]), 255)
        row = g[30, 20:40].astype(int)
        self.assertTrue(np.all(np.diff(row) >= 0))          # ramps up into it
        self.assertGreater(int(g[30, 27]), 0)               # reaches past the edge
        self.assertLess(int(g[30, 31]), 255)                # and the edge is no longer hard

    def test_a_batch(self):
        b = np.stack([_block(), _block()])
        out = adjust_mask(b, 2, 1)
        self.assertEqual(out.shape, b.shape)
        self.assertTrue(np.array_equal(out[0], adjust_mask(_block(), 2, 1)))


def _ghost_scene():
    """A depth map like DepthCrafter's: background 0.3 sloping to 0.4, and
    its copy of the text (0.8) 5 px bigger than the corrected mask."""
    depth = np.tile(np.linspace(0.3, 0.4, 100, dtype=np.float32), (60, 1))
    depth[20:40, 25:75] = 0.8                            # the ghost: the mask block + 5 px
    mask = _block().astype(np.float32) / 255.0           # [25:35, 30:70]
    return depth, mask


class CleanTests(unittest.TestCase):
    """Text Clean: the text DepthCrafter put in the depth is replaced by the
    background around it, so its rim past the mask no longer warps."""

    def test_off_is_untouched(self):
        depth, mask = _ghost_scene()
        self.assertIs(clean_depth(depth, mask, 0), depth)
        self.assertIs(clean_depth(depth, np.zeros_like(mask), 8), depth)

    def test_the_ghost_rim_past_the_mask_is_gone(self):
        depth, mask = _ghost_scene()
        out = clean_depth(depth, mask, 8)
        rim = np.zeros_like(mask, bool)
        rim[20:40, 25:75] = True
        rim[mask > 0] = False                            # the ghost outside the mask
        self.assertGreater(float(depth[rim].min()), 0.79)
        self.assertLess(float(out[rim].max()), 0.45)      # background now, not text
        self.assertLess(float(out[mask > 0].max()), 0.45) # under the mask too (the pin sets it later)

    def test_too_small_a_distance_leaves_some(self):
        depth, mask = _ghost_scene()
        out = clean_depth(depth, mask, 2)                 # the ghost reaches 5 px out
        self.assertGreater(float(out[22, 50]), 0.79)

    def test_the_fill_follows_the_background(self):
        depth, mask = _ghost_scene()
        out = clean_depth(depth, mask, 8)
        self.assertLess(out[30, 28], out[30, 72])          # the slope carries through
        far = np.ones_like(mask, bool)
        far[5:55, 5:95] = False
        self.assertTrue(np.array_equal(out[far], depth[far]))   # nothing away from it changes

    def test_a_channel_axis(self):
        depth, mask = _ghost_scene()
        out = clean_depth(depth[..., None], mask, 8)
        self.assertEqual(out.shape, (60, 100, 1))
        self.assertTrue(np.allclose(out[..., 0], clean_depth(depth, mask, 8)))

    def test_a_mask_covering_the_whole_frame(self):
        depth, _ = _ghost_scene()
        out = clean_depth(depth, np.ones((60, 100), np.float32), 4)
        self.assertTrue(np.isfinite(out).all())


class PlumbingTests(unittest.TestCase):
    def test_the_setting_reaches_processing_settings(self):
        from core.splatting import ProcessingSettings
        from studio.stages.splatting import settings as S
        values = dict(S.DEFAULTS)
        values.update({"input_source_clips": "s", "input_depth_maps": "d", "output_splatted": "o",
                       "submask_grow": 6, "submask_soften": 2.5, "submask_clean": 9, "submask_conv": -0.2})
        ps = ProcessingSettings.from_config(S.config_for_run(values))
        self.assertEqual((ps.submask_grow, ps.submask_soften, ps.submask_clean, ps.submask_conv), (6.0, 2.5, 9.0, -0.2))

    def test_the_sidecar_keys(self):
        from core.common.sidecar_manager import SidecarConfigManager
        for k in ("submask_grow", "submask_soften", "submask_clean", "submask_conv"):
            self.assertIn(k, SidecarConfigManager.SIDECAR_KEY_MAP)

    def test_the_render_takes_them(self):
        import inspect
        from core.splatting.render_processor import RenderProcessor
        params = inspect.signature(RenderProcessor.render_video).parameters
        self.assertIn("subtitle_mask_grow", params)
        self.assertIn("subtitle_mask_soften", params)
        self.assertIn("subtitle_mask_clean", params)
        self.assertIn("subtitle_mask_conv", params)          # Text Conv (2026-10-11)

    def test_the_preview_cache_key_changes(self):
        from core.ui import preview_buffer
        src = Path(preview_buffer.__file__).read_text(encoding="utf-8")
        self.assertIn('params.get("submask_grow"', src)
        self.assertIn('params.get("submask_soften"', src)
        self.assertIn('params.get("submask_clean"', src)
        self.assertIn('params.get("submask_conv"', src)


class PreviewTest(unittest.TestCase):
    """The preview engine's own _preview_submask_frame grows the mask it reads
    (a mask video in a temp folder; a stand-in for the engine's state)."""

    def test_the_preview_applies_them(self):
        import subprocess, tempfile, types
        import torch  # noqa: F401  (before decord)
        from studio.legacy import install
        install()
        from studio.stages.splatting.preview_engine import SplattingPreviewEngine as SplatterGUI
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "clip.mp4"; src.write_bytes(b"")
            raw = b"".join(_block().tobytes() for _ in range(3))
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                            "-s", "100x60", "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "0",
                            "-pix_fmt", "yuv444p", str(Path(d) / "clip_submask.mp4")], input=raw, check=True)
            fake = types.SimpleNamespace(
                previewer=types.SimpleNamespace(current_video_index=0, video_list=[{"source_video": str(src)}],
                                                frame_scrubber_var=types.SimpleNamespace(get=lambda: 1)),
                _get_current_sidecar_paths_and_data=lambda: (None, None, {"submask_enabled": True}))
            plain = SplatterGUI._preview_submask_frame(fake, 60, 100, False)
            fake._preview_submask_path = None
            grown = SplatterGUI._preview_submask_frame(fake, 60, 100, False, 4, 0)
            self.assertIsNotNone(plain)
            self.assertGreater(int((grown > 0.5).sum()), int((plain > 0.5).sum()) + 200)
            fake._preview_submask_reader = None                      # let the temp file go
            import gc
            gc.collect()


def _cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


@unittest.skipUnless(_cuda(), "needs CUDA")
class RenderCleanTest(unittest.TestCase):
    """The real Splatting batch on the small fixture, a Detect Text mask and a
    sidecar enabling it: Text Clean runs before Dilate / Blur (the depth it is
    handed has not been dilated yet), at the clip's distance scaled to the
    render's size, and changes the splat; 0 never calls it."""

    def run_batch(self, clean, conv=0.0):
        import json, queue, shutil, subprocess, tempfile, threading
        from studio.legacy import install
        install()
        sys.path.insert(0, str(Path(__file__).resolve().parent / "checks"))
        import _fixtures
        from core.common.sidecar_manager import SidecarConfigManager
        from core.splatting import BatchProcessor, ProcessingSettings
        from core.splatting import subtitle_mask_adjust as SMA
        from core.splatting import depth_processing as DP
        from studio.stages.splatting import settings as S

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        fx = _fixtures.copy("small", tmp / "fx")
        (fx / "src" / "clipB.mp4").unlink()
        (fx / "depth" / "clipB_depth.mp4").unlink()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=black:size=640x360:rate=24:duration=2,drawbox=x=200:y=150:w=240:h=50:color=white:t=fill",
                        "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", str(fx / "src" / "clipA_submask.mp4")],
                       check=True)
        (fx / "depth" / "clipA_depth.fssidecar").write_text(json.dumps(
            {"submask_enabled": True, "submask_clean": clean, "submask_conv": conv}))
        values = S.normalise({**S.DEFAULTS, "input_source_clips": str(fx / "src"),
                              "input_depth_maps": str(fx / "depth"), "output_splatted": str(tmp / "out"),
                              "enable_full_res": True, "enable_low_res": False, "multi_map": False,
                              "move_to_finished": False, "auto_convergence_mode": "Off",
                              "depth_dilate_size_x": 7, "depth_dilate_size_y": 7})
        settings = ProcessingSettings.from_config(S.config_for_run(values), **S.processing_overrides(values))

        calls, order = [], []
        real_clean, real_proc = SMA.clean_depth, DP.process_depth_batch

        def clean_spy(depth, mask, radius):
            calls.append(radius); order.append("clean")
            return real_clean(depth, mask, radius)

        def proc_spy(*a, **k):
            order.append("dilate")
            return real_proc(*a, **k)
        SMA.clean_depth = clean_spy
        import core.splatting.render_processor as RP
        rp_proc = RP.process_depth_batch
        RP.process_depth_batch = proc_spy
        try:
            BatchProcessor(queue.Queue(), threading.Event(), SidecarConfigManager()).run_batch_process(
                settings=settings, from_index=0, to_index=None, video_list=None)
        finally:
            SMA.clean_depth = real_clean
            RP.process_depth_batch = rp_proc
        outs = sorted((tmp / "out").rglob("*.mp4"))
        self.assertTrue(outs, "no splat written")
        import cv2
        cap = cv2.VideoCapture(str(outs[0]))
        cap.set(cv2.CAP_PROP_POS_FRAMES, 10)
        ok, frame = cap.read()
        cap.release()
        self.assertTrue(ok)
        return calls, order, frame

    def test_clean_runs_before_dilate_and_changes_the_splat(self):
        calls0, _o, plain = self.run_batch(0)
        self.assertEqual(calls0, [])
        calls, order, cleaned = self.run_batch(12)
        self.assertTrue(calls, "Text Clean never ran")
        self.assertAlmostEqual(calls[0], 12.0, places=3)          # source size = render size here
        self.assertLess(order.index("clean"), order.index("dilate"))
        self.assertGreater(int(np.abs(plain.astype(int) - cleaned.astype(int)).max()), 0, "the splat did not change")

    def test_text_conv_moves_the_text_off_the_screen(self):
        """Text Conv (user, 2026-10-11): 0 keeps the text on the screen plane, as before; an offset
        either way moves the masked text in the splat (the output is the render's grid, so the
        whole frame is compared: nothing else differs between the runs)."""
        _c, _o, flat = self.run_batch(0)
        for conv in (-0.3, 0.6):
            _c, _o, moved = self.run_batch(0, conv=conv)
            diff = np.abs(flat.astype(int) - moved.astype(int)).max(axis=2)
            self.assertGreater(int((diff > 40).sum()), 1000, f"the text did not move at {conv:+}")


if __name__ == "__main__":
    unittest.main()
