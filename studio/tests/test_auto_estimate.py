"""Auto 3D (Thomas's patch; user, 2026-11-07; core/splatting/auto_estimate.py): each clip's Disparity from how
close its shot is. The curve, the three views, the depth-only fallback, and the batch's handling: the result in
the clip's sidecar (its other keys kept), reused while Min / Mid / Max and the clip are the same, estimated again
when they change, and no sidecar made for a clip that has none. No model, no GPU: the estimate is stood in for."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import torch  # noqa: E402
from core.splatting import auto_estimate as A  # noqa: E402
from core.splatting.batch_processing import BatchProcessor, ProcessingSettings  # noqa: E402


def _ps(**kw):
    return ProcessingSettings(input_source_clips="", input_depth_maps="", output_splatted="", **kw)


class CurveTest(unittest.TestCase):
    def test_mid_at_half_and_the_ends(self):
        self.assertAlmostEqual(A.strength_curve(0.5, 10, 40, 80), 40.0, places=6)
        self.assertAlmostEqual(A.strength_curve(0.0, 10, 40, 80), 10.0)
        self.assertAlmostEqual(A.strength_curve(1.0, 10, 40, 80), 80.0)
        self.assertLess(A.strength_curve(0.3, 10, 40, 80), 40.0)
        self.assertGreater(A.strength_curve(0.8, 10, 40, 80), 40.0)

    def test_closeness_is_clamped_and_mid_kept_inside(self):
        self.assertAlmostEqual(A.strength_curve(1.7, 10, 40, 80), 80.0)
        self.assertAlmostEqual(A.strength_curve(-1, 10, 40, 80), 10.0)
        # a Mid at Min is held 2% in: the curve still rises
        self.assertAlmostEqual(A.strength_curve(0.5, 10, 10, 80), 10 + 70 * 0.02, places=6)

    def test_depth_closeness(self):
        d = np.zeros((2, 9, 4), np.float32)
        d[:, :3] = 0.2                       # top third
        d[:, 3:6] = 0.5
        d[:, 6:] = 0.8                       # bottom third: the floor nearer
        want = 0.958 * 0.5 - 0.526 * (0.8 - 0.2) + 0.282
        self.assertAlmostEqual(A.depth_closeness(d), want, places=5)
        self.assertEqual(A.depth_closeness(np.ones((1, 9, 4), np.float32)), 1.0)


class ViewsTest(unittest.TestCase):
    def test_three_crops_along_the_long_side(self):
        x = torch.zeros(2, 3, 108, 192)
        x[..., :10] = 1.0                    # the left edge white: only the first crop holds it
        v = A.three_views(x)
        self.assertEqual(tuple(v.shape), (6, 3, 224, 224))
        self.assertGreater(float(v[0, :, :, :5].mean()), 0.9)
        self.assertLess(float(v[1, :, :, :5].mean()), 0.1)
        tall = A.three_views(torch.rand(1, 3, 300, 100))
        self.assertEqual(tuple(tall.shape), (3, 3, 224, 224))

    def test_the_encoder_has_open_clips_names(self):
        keys = set(A.ClipVisual().state_dict())
        for k in ("conv1.weight", "class_embedding", "positional_embedding", "ln_pre.weight", "proj",
                  "transformer.resblocks.11.attn.in_proj_weight", "transformer.resblocks.0.attn.out_proj.bias",
                  "transformer.resblocks.0.mlp.c_fc.weight", "transformer.resblocks.0.mlp.c_proj.bias",
                  "ln_post.bias"):
            self.assertIn(k, keys)
        self.assertEqual(tuple(A.ClipVisual()(torch.zeros(1, 3, 224, 224)).shape), (1, 512))

    def test_visual_weights_from_a_whole_checkpoint(self):
        tmp = tempfile.mkdtemp()
        try:
            sd = {"visual." + k: v for k, v in A.ClipVisual().state_dict().items()}
            sd["token_embedding.weight"] = torch.zeros(2, 2)
            torch.save({"state_dict": {"module." + k: v for k, v in sd.items()}}, os.path.join(tmp, "c.pt"))
            got = A.visual_weights(os.path.join(tmp, "c.pt"))
            self.assertEqual(set(got), set(A.ClipVisual().state_dict()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_prompts_file(self):
        p = A.load_prompts()
        self.assertEqual(p["text"].shape, (6, 512))
        self.assertTrue(np.allclose(np.linalg.norm(p["text"], axis=1), 1.0, atol=1e-4))
        self.assertEqual(list(p["values"]), [0.0, 0.2, 0.4, 0.55, 0.8, 1.0])
        self.assertEqual(p["temperature"], 50.0)


def _clip(path, frames, gray=False):
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p", path],
                         stdin=subprocess.PIPE)
    p.stdin.write(b"".join(np.ascontiguousarray(f).tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0


class ClipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rgb = os.path.join(self.tmp, "shot.mp4")
        self.depth = os.path.join(self.tmp, "shot_depth.mp4")
        _clip(self.rgb, [np.full((64, 96, 3), 90, np.uint8)] * 13)
        _clip(self.depth, [np.full((64, 96, 3), 200, np.uint8)] * 13)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_depth_fallback(self):
        r = A.auto_strength_for_clip(self.rgb, self.depth, 10, 40, 80, use_model=False)
        c = min(1.0, 0.958 * 200 / 255 + 0.282)
        self.assertEqual(r["model"], "depth")
        self.assertEqual(r["frames"], 3, "every 6th of 13 frames: 0, 6, 12")
        self.assertAlmostEqual(r["closeness"], c, places=2)
        self.assertEqual(r["strength"], round(A.strength_curve(c, 10, 40, 80), 1))

    def test_the_model_sees_every_6th_frame(self):
        seen = []

        class Fake:
            def closeness(self, x):
                seen.append(tuple(x.shape))
                return torch.full((x.shape[0],), 0.5)
        r = A.auto_strength_for_clip(self.rgb, self.depth, 10, 40, 80, model=Fake())
        self.assertEqual(seen, [(3, 3, 64, 96)])
        self.assertEqual((r["strength"], r["model"]), (40.0, A.MODEL_NAME))

    def test_a_stop_returns_nothing(self):
        stop = threading.Event()
        stop.set()
        self.assertIsNone(A.auto_strength_for_clip(self.rgb, self.depth, 10, 40, 80, stop_event=stop, use_model=False))


class BatchTest(unittest.TestCase):
    """BatchProcessor._auto_3d: where the result goes, and when it is reused."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "shot.mp4")
        Path(self.video).write_bytes(b"x" * 10)
        self.sidecar = os.path.join(self.tmp, "shot_depth.fssidecar")
        self.calls = []
        self.bp = BatchProcessor(queue.Queue(), threading.Event())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **kw):
        return _ps(sidecar_folder=self.tmp, auto_strength=True, **kw)

    def run_auto(self, settings, value=55.5):
        def fake(rgb, depth, lo, mid, hi, every=6, stop_event=None):
            self.calls.append((lo, mid, hi, every))
            return {"closeness": 0.7, "frames": 4, "strength": value, "min": lo, "max": hi, "typical": mid,
                    "every": every, "model": A.MODEL_NAME}
        vid = {"max_disparity_percentage": 30.0, "actual_depth_map_path": "d.mp4"}
        with mock.patch.object(A, "auto_strength_for_clip", fake):
            self.bp._auto_3d(self.video, settings, vid)
        return vid["max_disparity_percentage"]

    def test_the_result_goes_into_the_sidecar_its_other_keys_kept(self):
        Path(self.sidecar).write_text(json.dumps({"convergence_plane": 0.3, "max_disparity": 25.0,
                                                  "auto_estimate": {"convergence": {"x": 1}}}))
        self.assertEqual(self.run_auto(self.settings()), 55.5)
        d = json.loads(Path(self.sidecar).read_text())
        self.assertEqual(d["max_disparity"], 55.5)
        self.assertEqual(d["convergence_plane"], 0.3)
        self.assertEqual(d["auto_estimate"]["convergence"], {"x": 1}, "another estimate's record kept")
        rec = d["auto_estimate"]["auto_strength"]
        self.assertEqual((rec["min"], rec["max"], rec["typical"], rec["every"], rec["value"]), (10.0, 80.0, 40.0, 6, 55.5))

    def test_a_rerun_reuses_it_and_says_so(self):
        Path(self.sidecar).write_text("{}")
        self.run_auto(self.settings())
        with self.assertLogs("core.splatting.batch_processing", logging.INFO) as logs:
            self.assertEqual(self.run_auto(self.settings(), value=11.0), 55.5)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any("(from its sidecar)" in m for m in logs.output), logs.output)

    def test_new_min_mid_max_or_a_new_clip_estimate_again(self):
        Path(self.sidecar).write_text("{}")
        self.run_auto(self.settings())
        self.run_auto(self.settings(auto_strength_mid=45.0))
        self.assertEqual(len(self.calls), 2)
        self.run_auto(self.settings(auto_strength_mid=45.0))
        self.assertEqual(len(self.calls), 2)
        Path(self.video).write_bytes(b"y" * 12)           # the clip cut again
        self.run_auto(self.settings(auto_strength_mid=45.0))
        self.assertEqual(len(self.calls), 3)

    def test_no_sidecar_none_is_made(self):
        self.assertEqual(self.run_auto(self.settings()), 55.5)
        self.assertFalse(os.path.exists(self.sidecar), "a made sidecar would put its defaults over the page's")
        cache = os.path.join(self.tmp, "shot_depth.auto3d.json")
        self.assertEqual(json.loads(Path(cache).read_text())["auto_strength"]["value"], 55.5)
        self.assertEqual(self.run_auto(self.settings(), value=1.0), 55.5)
        self.assertEqual(len(self.calls), 1, "reused from its record")

    def test_max_not_above_min_leaves_the_disparity(self):
        self.assertEqual(self.run_auto(self.settings(auto_strength_min=50.0, auto_strength_max=50.0)), 30.0)
        self.assertEqual(self.calls, [])

    def test_a_failed_estimate_leaves_the_disparity_and_the_sidecar(self):
        Path(self.sidecar).write_text(json.dumps({"max_disparity": 25.0}))
        vid = {"max_disparity_percentage": 25.0, "actual_depth_map_path": "d.mp4"}
        with mock.patch.object(A, "auto_strength_for_clip", lambda *a, **k: None):
            self.bp._auto_3d(self.video, self.settings(), vid)
        self.assertEqual(vid["max_disparity_percentage"], 25.0)
        self.assertEqual(json.loads(Path(self.sidecar).read_text()), {"max_disparity": 25.0})


class SettingsTest(unittest.TestCase):
    def test_the_page_reaches_the_batch_off_by_default(self):
        from studio.stages.splatting import settings as S
        p = ProcessingSettings.from_config(S.config_for_run(dict(S.DEFAULTS)))
        self.assertEqual((p.auto_strength, p.auto_strength_min, p.auto_strength_mid, p.auto_strength_max),
                         (False, 10.0, 40.0, 80.0))
        on = dict(S.DEFAULTS, auto_strength=True, auto_strength_mid="35")
        p = ProcessingSettings.from_config(S.config_for_run(on))
        self.assertEqual((p.auto_strength, p.auto_strength_mid), (True, 35.0))

    def test_off_never_estimates(self):
        bp = BatchProcessor(queue.Queue(), threading.Event())
        vid = {"convergence_plane": 0.5, "max_disparity_percentage": 30.0, "actual_depth_map_path": "d.mp4"}
        for on in (False, True):
            with mock.patch.object(bp, "_auto_3d") as auto,                     mock.patch.object(bp, "_get_video_specific_settings", return_value=dict(vid)),                     mock.patch.object(bp, "get_defined_tasks", return_value=[]):
                try:
                    bp._process_single_video_orchestration("x.mp4", _ps(auto_strength=on), None, 0, False)
                except Exception:
                    pass                     # past the hook: what follows needs a real clip
            self.assertEqual(auto.called, on)


if __name__ == "__main__":
    unittest.main()
