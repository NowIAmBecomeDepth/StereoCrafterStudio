"""Splatting's encoder feed on the GPU (audit 2026-09-28 O1).

The grid the encoder gets used to be built on the CPU from float32 downloads
(np.clip, concatenate, * 65535, astype uint16, RGB -> BGR: ~8 passes over
~100 MB a frame at 1080p) and written synchronously. It is now built on the
device and downloaded once as uint16, and a writer thread feeds the pipe.

The render itself is not deterministic run to run (the forward warp's atomics),
so the proof is here, on identical inputs: the same splat results through the
old CPU writers and through _grid_frames_gpu give byte-identical bytes, for
every layout the renderer writes."""

from __future__ import annotations

import io
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting import render_processor as RP  # noqa: E402


class _Pipe:
    def __init__(self):
        self.stdin = io.BytesIO()


def _cpu_results(panels, dual_eye):
    """What _process_gpu_splatting handed the CPU writers (its old tail)."""
    cpu = {k: v.cpu().numpy() for k, v in panels.items()}
    out = []
    for j in range(next(iter(cpu.values())).shape[0]):
        out.append({k: np.clip(v[j].transpose(1, 2, 0), 0.0, 1.0) for k, v in cpu.items()})
    return out


@unittest.skipUnless(torch.cuda.is_available(), "needs CUDA")
class GridOnTheGpuTests(unittest.TestCase):
    def panels(self, dual_eye, b=3, h=36, w=64):
        g = torch.Generator(device="cuda").manual_seed(7)
        def t(c):   # values past both ends too: the clip must match
            return torch.rand((b, c, h, w), device="cuda", generator=g) * 1.4 - 0.2
        if dual_eye:
            return {"left": t(3), "left_mask": t(1), "right": t(3), "occlusion": t(1)}
        # the left eye as the renderer makes it: uint8 up, / 255 on the device
        u8 = torch.randint(0, 256, (b, h, w, 3), dtype=torch.uint8, generator=torch.Generator().manual_seed(3))
        left = u8.cuda().permute(0, 3, 1, 2).float() / 255.0
        return {"left": left, "right": t(3), "occlusion": t(1), "depth": t(1)}

    def old_bytes(self, panels, layout):
        rp = RP.RenderProcessor.__new__(RP.RenderProcessor)
        pipe = _Pipe()
        res = _cpu_results(panels, layout == "dual_eye")
        if layout == "dual_eye":
            rp._write_dual_eye_to_ffmpeg(pipe, res)
        elif layout == "split":
            rp._write_split_to_ffmpeg(None, pipe, res)
        else:
            rp._write_to_ffmpeg(pipe, res, dual_output=(layout == "2panel"))
        return pipe.stdin.getvalue()

    def new_bytes(self, panels, layout):
        frames = RP._grid_frames_gpu(panels, "2panel" if layout == "split" else layout)
        return b"".join(f.tobytes() for f in frames)

    def test_every_layout_is_byte_identical(self):
        for layout in ("4panel", "2panel", "split", "dual_eye"):
            with self.subTest(layout=layout):
                panels = self.panels(dual_eye=(layout == "dual_eye"))
                old, new = self.old_bytes(panels, layout), self.new_bytes(panels, layout)
                self.assertEqual(len(old), len(new))
                self.assertTrue(old == new, f"{layout}: the GPU grid differs from the CPU one")

    def test_the_uint8_upload_gives_the_same_left_eye(self):
        u8 = np.random.default_rng(1).integers(0, 256, (2, 20, 30, 3), dtype=np.uint8)
        before = torch.from_numpy(u8).permute(0, 3, 1, 2).float().cuda() / 255.0   # the old upload
        after = torch.from_numpy(u8).cuda().permute(0, 3, 1, 2).float() / 255.0
        self.assertTrue(torch.equal(before, after))


class PipeWriterTests(unittest.TestCase):
    def test_writes_every_frame_in_order(self):
        pipe = _Pipe()
        w = RP._PipeWriter(pipe, depth=2)
        frames = [np.full((4, 5, 3), i, np.uint16) for i in range(20)]
        for f in frames:
            w.put(f)
        w.close()
        self.assertEqual(pipe.stdin.getvalue(), b"".join(f.tobytes() for f in frames))

    def test_a_write_error_comes_back_to_the_render(self):
        class Broken:
            def write(self, data):
                raise BrokenPipeError("ffmpeg went away")
        pipe = _Pipe(); pipe.stdin = Broken()
        w = RP._PipeWriter(pipe, depth=2)
        w.put(np.zeros((2, 2, 3), np.uint16))
        time.sleep(0.2)
        with self.assertRaises(BrokenPipeError):
            w.put(np.zeros((2, 2, 3), np.uint16))
        with self.assertRaises(BrokenPipeError):
            w.close()

    def test_abandon_drops_what_is_still_waiting(self):
        release = threading.Event()
        written = []

        class Slow:
            def write(self, data):
                release.wait(5)
                written.append(bytes(data))
        pipe = _Pipe(); pipe.stdin = Slow()
        w = RP._PipeWriter(pipe, depth=5)
        for i in range(4):
            w.put(np.full((1, 1, 3), i, np.uint16))
        time.sleep(0.1)
        release.set()
        w.close(abandon=True)
        self.assertLess(len(written), 4, "a stopped render's queued frames were all written")


if __name__ == "__main__":
    unittest.main()
