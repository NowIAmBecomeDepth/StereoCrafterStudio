"""Splatting settings that silently did nothing (audit 2026-09-28 #16-#19).

  #16 Boost Pre-filter: forward_warp called a function it never imported;
      the NameError was swallowed as a warning and the depth went unfiltered.
  #17 Single-eye renders dropped Boost Knee and both Pre-filter values, and
      no render said it was the low-res pass (Mask Mode "Dual" ran Hybrid on
      the low-res pass too).
  #18 Enable Global Normalization: the batch never computed the clip's depth
      range, so raw codes were divided by 0..1 and clipped white.
  #19 Spatial / Temporal AQ reached the pipe encoder only with Lookahead on.

The GPU tests need CUDA (tiny tensors); #18 runs the real batch over the
small fixture with the renderer stood in for, so it reads what it is handed.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_splat_settings_reach -v
"""

from __future__ import annotations

import logging
import queue
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import numpy as np  # noqa: E402

try:
    import torch  # noqa: E402
    CUDA = torch.cuda.is_available()
except Exception:  # pragma: no cover
    CUDA = False


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@unittest.skipUnless(CUDA, "needs CUDA")
class BoostPrefilterTest(unittest.TestCase):
    """#16: the pre-filter (Edge Clean) really runs (no swallowed NameError) and
    changes the result. Since 2026-10-01 it shapes the EXTRA pop, so the depth
    here reaches into the boost (0.8-1.0, past the 0.85 threshold)."""

    def warp(self, radius):
        from core.splatting import forward_warp as FW
        torch.manual_seed(0)
        src = torch.rand(1, 3, 32, 32, device="cuda")
        depth = (torch.rand(1, 1, 32, 32, device="cuda") * 0.2 + 0.8)
        rec = _Records()
        FW.logger.addHandler(rec)
        try:
            right, _mask = FW.execute_forward_warp(
                stereo_projector=FW.ForwardWarpStereo(occlu_map=True).cuda(), source_tensor=src,
                depth_tensor=depth, target_width=32, max_disp=20.0, zero_disparity_anchor_val=0.5,
                boost_amount=1.0, boost_prefilter_radius=radius, boost_prefilter_eps=1e-2)
        finally:
            FW.logger.removeHandler(rec)
        return right, rec.messages

    def test_the_prefilter_runs(self):
        plain, _ = self.warp(0)
        filtered, messages = self.warp(3)
        skipped = [m for m in messages if "pre-filter skipped" in m or "Edge Clean skipped" in m]
        self.assertEqual(skipped, [], "the pre-filter failed and was skipped")
        self.assertFalse(torch.equal(plain, filtered), "the pre-filter changed nothing")


@unittest.skipUnless(CUDA, "needs CUDA")
class SingleEyeArgsTest(unittest.TestCase):
    """#17: what the renderer hands execute_forward_warp, for each eye path."""

    def call(self, dual_eye):
        from core.splatting import forward_warp as FW
        from core.splatting.render_processor import RenderProcessor
        seen = {}
        real = FW.execute_forward_warp

        def spy(**kw):
            seen.update(kw)
            z = torch.zeros(1, 3, 8, 8, device="cuda")
            return (z, z, z, z) if kw.get("dual_eye") else (z, z)
        FW.execute_forward_warp = spy
        try:
            RenderProcessor(threading.Event(), queue.Queue())._process_gpu_splatting(
                stereo_projector=None, batch_video_numpy=np.zeros((1, 8, 8, 3), np.uint8),
                batch_depth_numpy_float=np.zeros((1, 8, 8), np.float32), target_width=8, target_height=8,
                max_disp=10.0, zero_disparity_anchor_val=0.5, input_bias=0.0, dual_eye=dual_eye,
                boost_amount=0.5, boost_knee=0.3, boost_prefilter_radius=4, boost_prefilter_eps=0.02,
                is_low_res_task=True)
        finally:
            FW.execute_forward_warp = real
        return seen

    def test_single_eye_gets_knee_prefilter_and_the_pass(self):
        kw = self.call(dual_eye=False)
        self.assertEqual((kw.get("boost_knee"), kw.get("boost_prefilter_radius"), kw.get("boost_prefilter_eps")),
                         (0.3, 4, 0.02))
        self.assertTrue(kw.get("is_low_res_task"), "the low-res pass was not said")

    def test_dual_eye_gets_the_pass(self):
        self.assertTrue(self.call(dual_eye=True).get("is_low_res_task"))


class GlobalNormTest(unittest.TestCase):
    """#18: with Enable Global Normalization the render is handed the clip's
    real depth range, not the 0..1 defaults."""

    def test_the_clip_range_reaches_the_render(self):
        from studio.legacy import install
        install()
        sys.path.insert(0, str(REPO / "studio" / "tests" / "checks"))
        import _fixtures
        from core.common.sidecar_manager import SidecarConfigManager
        from core.splatting import BatchProcessor, ProcessingSettings
        from core.splatting import render_processor as RP
        from studio.stages.splatting import settings as S

        tmp = Path(tempfile.mkdtemp())
        try:
            fx = _fixtures.copy("small", tmp / "fx")
            values = S.normalise({**S.DEFAULTS, "input_source_clips": str(fx / "src"),
                                  "input_depth_maps": str(fx / "depth"), "output_splatted": str(tmp / "out"),
                                  "enable_full_res": False, "enable_low_res": True, "multi_map": False,
                                  "move_to_finished": False, "auto_convergence_mode": "Off",
                                  "enable_global_norm": True})
            settings = ProcessingSettings.from_config(S.config_for_run(values), **S.processing_overrides(values))
            handed = []
            real = RP.RenderProcessor.render_video

            def render(self, **kw):
                handed.append((kw["assume_raw_input"], kw["global_depth_min"], kw["global_depth_max"]))
                return True
            RP.RenderProcessor.render_video = render
            try:
                BatchProcessor(queue.Queue(), threading.Event(), SidecarConfigManager()).run_batch_process(
                    settings=settings, from_index=0, to_index=None, video_list=None)
            finally:
                RP.RenderProcessor.render_video = real
            self.assertEqual(len(handed), 2, handed)
            for raw, lo, hi in handed:
                self.assertFalse(raw)
                self.assertGreater(hi, 1.0, f"the depth range was never computed: {lo}..{hi}")
                self.assertLess(lo, hi)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AqWithoutLookaheadTest(unittest.TestCase):
    """#19: Spatial / Temporal AQ reach the pipe encoder with Lookahead off."""

    def test_aq_flags_reach_ffmpeg(self):
        from core.common import video_io as V
        cmds = []

        real = V.subprocess.Popen

        def Popen(cmd, *a, **kw):
            if "rawvideo" in cmd:                # the encoder pipe: record it, start nothing
                cmds.append(cmd)
                return None
            return real(cmd, *a, **kw)          # anything else (an encoder probe) runs for real
        V.subprocess.Popen = Popen
        try:
            V.start_ffmpeg_pipe_process(
                content_width=64, content_height=64, final_output_mp4_path=str(Path(tempfile.gettempdir()) / "x.mp4"),
                fps=24, video_stream_info=None,
                encoding_options={"codec": "H.265", "encoder": "NVENC", "nvenc_lookahead_enabled": False,
                                  "nvenc_spatial_aq": True, "nvenc_temporal_aq": True, "nvenc_aq_strength": 11})
        finally:
            V.subprocess.Popen = real
        cmd = cmds[-1]
        self.assertIn("-spatial-aq", cmd, " ".join(cmd))
        self.assertIn("-temporal-aq", cmd, " ".join(cmd))
        self.assertIn("11", cmd)


if __name__ == "__main__":
    unittest.main()
