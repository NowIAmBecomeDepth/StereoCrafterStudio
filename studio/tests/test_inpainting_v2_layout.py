"""Inpainting V2 and the splat's layout (tester, 2026-11-12).

V2 took any name it didn't know for a 2x2 quad: a plain clip (Clip_0001.mp4) came out as its
top-left quarter beside an "inpainted" bottom-right quarter, and a dual-eye splat (_splatted2D)
had its left-eye mask written as the left eye. Now:

  * the layout comes from the name (splat_layout); a video that isn't a splat is refused;
  * a dual-eye splat has both eyes inpainted: its SBS holds, on each side, exactly what a
    dual splat (mask | warped) of that eye alone gives.

The real run_inpaint loop over tiny lossless clips; the model's parts are the resume test's fakes.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting_v2_layout -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

import numpy as np  # noqa: E402
import torch  # noqa: E402  -- before decord (the engine's VideoReader): the order is load-bearing
import torch.nn.functional as F  # noqa: E402

from studio.stages.inpainting_v2 import engine as E  # noqa: E402
from studio.stages.inpainting_v2 import resume as R  # noqa: E402
from studio.tests.test_inpainting_v2_resume import _FakeEngine, _MemoryWriter  # noqa: E402

EYE_W, EYE_H, FRAMES = 64, 48, 30
SETTINGS = dict(frames_chunk=17, frames_overlap=4, tile_overlap=0, tile_num=1, inference_steps=3, seed=7)


def _write(path, frames):
    t, h, w, _ = frames.shape
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                        "-r", "24", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", path],
                       input=frames.tobytes(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace"))


def _eye(t_count, base, mask_at):
    """One eye's (mask, warped) panels: the warped picture moving, a hole strip in the mask."""
    mask = np.zeros((t_count, EYE_H, EYE_W, 3), np.uint8)
    warp = np.zeros((t_count, EYE_H, EYE_W, 3), np.uint8)
    for t in range(t_count):
        mask[t, :, mask_at:mask_at + 10] = 255
        warp[t] = (base + 3 * t) % 256
        warp[t, :, (t * 2) % 50:(t * 2) % 50 + 8] = 230
    return mask, warp


def _fake_denoise(cond, mask, *a, should_stop=None, **k):
    """Deterministic: the latents are the conditioning, pooled to latent size (overlap included)."""
    _, _, t, h, w = cond.shape
    pooled = F.adaptive_avg_pool3d(cond[0], ((t - 1) // 4 + 1, h // 8, w // 8)).unsqueeze(0)
    return torch.cat([pooled * 2 - 1, torch.zeros(1, 13, *pooled.shape[2:])], dim=1)


class SplatLayoutTest(unittest.TestCase):
    def test_names(self):
        for name, layout in [("a_960_splatted2.mp4", "dual"), ("a_960_splatted2F.mkv", "dual"),
                             ("a_960_splatted4.mp4", "quad"), ("a_960_splatted4F.mp4", "quad"),
                             ("a_splatting_results.mp4", "quad"), ("a_960_splatted2D.mp4", "dual_eye"),
                             ("a_960_splatted2DF.mov", "dual_eye"), ("Clip_0001.mp4", None),
                             ("a_960_splatted2_framebreak.mkv", None)]:
            self.assertEqual(E.splat_layout(name), layout, name)


class RunLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v2layout_")
        self.out = os.path.join(self.tmp, "out")
        self._saved = (E.spatial_tiled_process, E.StreamingVideoWriter)
        E.spatial_tiled_process = _fake_denoise
        E.StreamingVideoWriter = _MemoryWriter

    def tearDown(self):
        E.spatial_tiled_process, E.StreamingVideoWriter = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_clip(self, path):
        _MemoryWriter.written = {}
        E.run_inpaint(_FakeEngine(), path, self.out, prompt="", free_text_encoder=False, **SETTINGS)
        return {k: np.stack(v) for k, v in _MemoryWriter.written.items()}

    def test_a_plain_clip_is_refused(self):
        clip = os.path.join(self.tmp, "Clip_0001.mp4")
        _write(clip, np.full((FRAMES, EYE_H * 2, EYE_W * 2, 3), 90, np.uint8))
        _MemoryWriter.written = {}
        with self.assertRaisesRegex(ValueError, "isn't a splat"):
            E.run_inpaint(_FakeEngine(), clip, self.out, prompt="", free_text_encoder=False, **SETTINGS)
        self.assertEqual(_MemoryWriter.written, {})

    def test_a_dual_eye_splat_inpaints_both_eyes(self):
        lmask, lwarp = _eye(FRAMES, 50, 40)
        rmask, rwarp = _eye(FRAMES, 170, 8)
        quad = np.concatenate([np.concatenate([lmask, lwarp], 2), np.concatenate([rmask, rwarp], 2)], 1)
        _write(os.path.join(self.tmp, "shot_128_splatted2D.mp4"), quad)
        _write(os.path.join(self.tmp, "left_128_splatted2.mp4"), np.concatenate([lmask, lwarp], 2))
        _write(os.path.join(self.tmp, "right_128_splatted2.mp4"), np.concatenate([rmask, rwarp], 2))

        got = self.run_clip(os.path.join(self.tmp, "shot_128_splatted2D.mp4"))
        left = self.run_clip(os.path.join(self.tmp, "left_128_splatted2.mp4"))
        right = self.run_clip(os.path.join(self.tmp, "right_128_splatted2.mp4"))

        self.assertEqual(sorted(got), ["shot_128_splatted2D_inpainting_results_anaglyph.mp4",
                                       "shot_128_splatted2D_inpainting_results_sbs.mp4"])
        sbs = got["shot_128_splatted2D_inpainting_results_sbs.mp4"]
        self.assertEqual(sbs.shape, (FRAMES, EYE_H, EYE_W * 2, 3))
        np.testing.assert_array_equal(sbs[:, :, :EYE_W], left["left_128_splatted2_inpainting_results_right_eye.mp4"])
        np.testing.assert_array_equal(sbs[:, :, EYE_W:], right["right_128_splatted2_inpainting_results_right_eye.mp4"])
        # the eyes' checkpoints went with the finished clip
        d = os.path.join(self.out, R.RESUME_DIR)
        self.assertEqual([f for f in os.listdir(d) if ".v2chunk_" in f] if os.path.isdir(d) else [], [])


if __name__ == "__main__":
    unittest.main()
