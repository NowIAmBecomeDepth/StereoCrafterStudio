"""Inpainting V2's per-chunk checkpoints (user, 2026-10-18; studio/stages/inpainting_v2/resume.py).

The real run_inpaint loop runs on a small made-up quad clip; only the 14B model's parts are
fakes -- the denoise (spatial_tiled_process) draws its noise from torch's RNG as the real one
does, the VAE decodes deterministically, and the writer keeps the frames in memory:

  * a clip that crashes after two chunks keeps their checkpoints; the next run denoises only
    the chunks left and writes exactly the frames an unbroken run writes (the overlap
    conditioning and the noise both carry on);
  * a finished clip deletes its checkpoints (V1's own in the folder are left alone);
  * a run with other settings deletes the stale ones and denoises every chunk.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting_v2_resume -v
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
import torch  # noqa: E402,F401  -- before decord (the engine's VideoReader): the order is load-bearing
import torch.nn.functional as F  # noqa: E402

from studio.stages.inpainting_v2 import engine as E  # noqa: E402
from studio.stages.inpainting_v2 import resume as R  # noqa: E402

EYE_W, EYE_H, FRAMES = 64, 48, 41
SETTINGS = dict(frames_chunk=17, frames_overlap=4, tile_overlap=0, tile_num=1, inference_steps=3, seed=7)


def _make_quad_clip(path: str) -> None:
    """A 2x2 quad clip (left eye | -, mask | warped right), moving so every chunk differs."""
    h, w = EYE_H * 2, EYE_W * 2
    frames = np.zeros((FRAMES, h, w, 3), np.uint8)
    for t in range(FRAMES):
        frames[t, :EYE_H, :EYE_W] = (40 + 4 * t) % 256                       # left eye
        frames[t, EYE_H:, :EYE_W, :][:, :12] = 255                            # mask: a strip at the left
        frames[t, EYE_H:, EYE_W:] = np.uint8((90 + 3 * t) % 256)              # warped right
        frames[t, EYE_H:, EYE_W + (t % 40):EYE_W + (t % 40) + 8] = 200
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                        "-r", "24", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", path],
                       input=frames.tobytes(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace"))


class _Config:
    z_dim = 16
    latents_mean = [0.0] * 16
    latents_std = [1.0] * 16


class _FakeVae:
    config = _Config()
    dtype = torch.float32

    def decode(self, latents, return_dict=False):
        """[1, 16, Tl, h, w] -> [1, 3, (Tl-1)*4+1, h*8, w*8] in -1..1, deterministic."""
        _, _, tl, h, w = latents.shape
        video = F.interpolate(latents[:, :3].float(), size=((tl - 1) * 4 + 1, h * 8, w * 8), mode="trilinear",
                              align_corners=True)
        return (torch.tanh(video),)


class _FakeEngine:
    device = "cpu"
    vae = _FakeVae()
    transformer = object()
    videoprocessor = object()
    transformer_patch_size = 2
    vae_scale_factor_temporal = 4
    vae_scale_factor_spatial = 8
    offload_mode = "none"
    dtype = torch.float32

    def get_prompt_embeds(self, prompt):
        return torch.zeros(1)

    def release_text_encoder(self):
        pass


class _MemoryWriter:
    """Stands in for StreamingVideoWriter: the frames kept per output, by name."""
    written: dict = {}

    def __init__(self, output_path, fps, w, h):
        self.output_path = output_path
        self.frames = []
        _MemoryWriter.written[os.path.basename(output_path)] = self.frames

    def append(self, arr):
        self.frames.extend(np.array(f) for f in arr)

    def close(self):
        return self.output_path

    def abort(self):
        pass


class ChunkResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v2resume_")
        self.clip = os.path.join(self.tmp, "shot_1920_splatted4.mp4")
        _make_quad_clip(self.clip)
        self.out = os.path.join(self.tmp, "out")
        self._saved = (E.spatial_tiled_process, E.StreamingVideoWriter)
        self.calls = []
        self.crash_at = None

        def fake_denoise(cond, mask, *a, should_stop=None, **k):
            # the real one: noise from torch's RNG, shaped by the chunk's conditioning (the overlap)
            self.calls.append(cond.shape[2])
            if self.crash_at is not None and len(self.calls) == self.crash_at:
                raise RuntimeError("out of memory (a crash, for the test)")
            _, _, t, h, w = cond.shape
            noise = torch.randn(1, 16, (t - 1) // 4 + 1, h // 8, w // 8)
            return noise * 0.3 + cond.mean() * 2.0

        E.spatial_tiled_process = fake_denoise
        E.StreamingVideoWriter = _MemoryWriter

    def tearDown(self):
        E.spatial_tiled_process, E.StreamingVideoWriter = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_clip(self, **over):
        _MemoryWriter.written = {}
        E.run_inpaint(_FakeEngine(), self.clip, self.out, prompt="", is_dual_input=False,
                      free_text_encoder=False, **{**SETTINGS, **over})
        return {k: np.stack(v) for k, v in _MemoryWriter.written.items()}

    def checkpoints(self):
        d = os.path.join(self.out, R.RESUME_DIR)
        return sorted(f for f in os.listdir(d) if ".v2chunk_" in f) if os.path.isdir(d) else []

    def test_a_crashed_clip_resumes_from_its_last_finished_chunk(self):
        whole = self.run_clip()
        n_chunks = len(self.calls)
        self.assertGreaterEqual(n_chunks, 3, "the clip should take several chunks")
        self.assertEqual(self.checkpoints(), [], "a finished clip keeps no checkpoints")

        self.calls.clear()
        self.crash_at = 3
        with self.assertRaises(RuntimeError):
            self.run_clip()
        self.assertEqual(len(self.checkpoints()), 2, "the two chunks finished before the crash are kept")

        self.calls.clear()
        self.crash_at = None
        resumed = self.run_clip()
        self.assertEqual(len(self.calls), n_chunks - 2, "only the chunks left are denoised")
        self.assertEqual(sorted(resumed), sorted(whole))
        for name in whole:
            self.assertEqual(resumed[name].shape, whole[name].shape, name)
            self.assertTrue(np.array_equal(resumed[name], whole[name]),
                            f"{name}: the resumed clip differs from an unbroken run")
        self.assertEqual(self.checkpoints(), [], "the resumed clip, finished, deletes its checkpoints")

    def test_other_settings_start_over(self):
        self.crash_at = 2
        with self.assertRaises(RuntimeError):
            self.run_clip()
        self.assertEqual(len(self.checkpoints()), 1)
        self.calls.clear()
        self.crash_at = None
        self.run_clip(inference_steps=4)               # a different run: the old chunk is stale
        n = len(self.calls)
        self.calls.clear()
        self.run_clip(inference_steps=4)
        self.assertEqual(n, len(self.calls), "every chunk denoised: nothing taken from the stale checkpoint")
        self.assertEqual(self.checkpoints(), [])

    def test_v1_checkpoints_in_the_folder_are_left_alone(self):
        d = os.path.join(self.out, R.RESUME_DIR)
        os.makedirs(d)
        v1 = os.path.join(d, "shot_1920_splatted4.chunk_000000.pt")
        Path(v1).write_bytes(b"v1")
        self.run_clip()
        self.assertTrue(os.path.exists(v1), "V1's checkpoint is V1's")


class CheckpointFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v2ckpt_")
        self.sig = R.signature(__file__, frames_chunk=81, frames_overlap=10, tile_num=2, tile_overlap=128,
                               inference_steps=5, seed=0, prompt="", is_dual_input=False, total_frames=97,
                               model={"offload": "mmgp"})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_random_state_comes_back_with_the_latents(self):
        ck = R.ChunkCheckpoints(self.tmp, "clip", self.sig)
        torch.manual_seed(3)
        torch.randn(5)
        lat = torch.randn(1, 16, 3, 4, 4)
        ck.save(0, 9, lat)
        expected = torch.randn(4)                       # what the next chunk would draw
        torch.manual_seed(99)
        got = R.ChunkCheckpoints(self.tmp, "clip", self.sig).load(0, 9, "cpu")
        self.assertTrue(torch.equal(got, lat))
        self.assertTrue(torch.equal(torch.randn(4), expected))

    def test_another_clip_whose_name_starts_the_same_is_not_touched(self):
        R.ChunkCheckpoints(self.tmp, "clip_b", self.sig).save(0, 9, torch.zeros(1))
        R.ChunkCheckpoints(self.tmp, "clip", {**self.sig, "inference_steps": 9}).clear()
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, R.RESUME_DIR))), 1)

    def test_a_damaged_checkpoint_is_ignored_and_dropped(self):
        ck = R.ChunkCheckpoints(self.tmp, "clip", self.sig)
        Path(ck.path(0, 9)).parent.mkdir(parents=True, exist_ok=True)
        Path(ck.path(0, 9)).write_bytes(b"not a checkpoint")
        self.assertIsNone(ck.load(0, 9, "cpu"))
        R.ChunkCheckpoints(self.tmp, "clip", self.sig)
        self.assertFalse(os.path.exists(ck.path(0, 9)))


if __name__ == "__main__":
    unittest.main()
