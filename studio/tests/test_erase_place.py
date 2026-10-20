"""erase.place: the erased clip in the clip's place (core.removal.erase) --
what happens when the clip is held open elsewhere (bug sweep 2026-10-09 #11).

Tiny ffmpeg-made clips in a temp folder; no model, no GPU.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_erase_place -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import torch  # noqa: F401  -- before decord (it segfaults on import otherwise here)
from decord import VideoReader, cpu

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.removal import erase as E
from core.removal import putback as P


def _clip(path, colour):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={colour}:size=64x48:rate=24:duration=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clip = os.path.join(self.dir, "shot.mp4")
        _clip(self.clip, "red")
        self.work = os.path.join(self.dir, "work")
        os.makedirs(self.work)
        self.out = os.path.join(self.work, "shot.erased.mp4")
        _clip(self.out, "blue")
        self.masks = []
        for name in ("mask", "obj"):
            p = os.path.join(self.work, f"{name}.mkv")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=64x48:rate=24:duration=0.5",
                            "-c:v", "ffv1", p], check=True)
            self.masks.append(p)
        self.before = Path(self.clip).read_bytes()

    def place(self):
        return E.place(self.clip, self.clip, self.out, *self.masks, {"method": "test"})

    def test_a_clip_held_open_is_left_as_it_was_and_the_erase_kept(self):
        reader = VideoReader(self.clip, ctx=cpu(0))
        reader.get_batch([0])
        with self.assertRaises(RuntimeError) as cm:
            self.place()
        del reader
        kept = os.path.join(E.history_dir(self.clip), "shot.erased.mp4")
        self.assertIn(kept, str(cm.exception))
        self.assertEqual(Path(self.clip).read_bytes(), self.before)
        self.assertTrue(os.path.isfile(kept))
        self.assertFalse(os.path.exists(os.path.join(E.history_dir(self.clip), "shot.original.mp4")))

    def test_merging_put_back_readers_are_closed_first(self):
        # an earlier erase, its put-back on Add (which reads the clip itself), previewed in Merging
        original = self.place()
        P.save_settings(self.clip, {"enabled": True, "mode": "Add", "parallax": 0.0, "opacity": 1.0})
        pb = P.find(self.dir, "shot")
        self.assertIsNotNone(pb)
        pb._read(self.clip, [0], 64, 48)            # Merging's preview reading the erased clip
        _clip(self.out, "green")
        os.makedirs(self.work, exist_ok=True)
        for p in self.masks:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=64x48:rate=24:duration=0.5",
                            "-c:v", "ffv1", p], check=True)
        E.place(self.clip, original, self.out, *self.masks, {"method": "test"})   # erasing again
        self.assertFalse(os.path.exists(self.out))


class WriterFailureTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): an encoder that exited early showed
    "BrokenPipe / Errno 22" and threw ffmpeg's reason away."""

    def test_the_erase_writers_say_why(self):
        import sys
        dying = E._with_stderr(subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('Cannot load nvcuda.dll\\n'); sys.exit(1)"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE))
        written = 0
        for _ in range(200):
            try:
                dying.stdin.write(bytes(1 << 16))
                dying.stdin.flush()
            except OSError:
                break
            written += 1
        try:
            dying.stdin.close()
        except OSError:
            pass
        dying.wait()
        msg = E.writers_failed((dying,), written, 200)
        self.assertIn("ffmpeg exit 1", msg)
        self.assertIn("nvcuda", msg)

    def test_the_depth_writer_says_why(self):
        import numpy as np
        from core.depthedit import depth_io
        info = depth_io.ClipInfo(path="x", width=64, height=48, num_frames=50, fps="24", pix_fmt="yuv420p10le",
                                 full_range=False)
        w = depth_io.DepthWriter(os.path.join(tempfile.gettempdir(), "no_such_dir_sc", "x", "out.mp4"), info,
                                 use_nvenc=False)
        with self.assertRaisesRegex(RuntimeError, "ffmpeg exited"):
            for _ in range(2000):
                w.write(np.zeros((48, 64), np.uint16))
        w.abort()

    def test_a_leftover_temp_folder_is_swept(self):
        left = os.path.join(self.dir, ".erase_abc123")
        os.makedirs(left)
        Path(left, "w0_frames.npy").write_bytes(b"x" * 1000)
        keep = os.path.join(self.dir, "erase_notes")
        os.makedirs(keep)
        E.sweep_stale_temp(self.clip)
        self.assertFalse(os.path.exists(left))
        self.assertTrue(os.path.isdir(keep))
        self.assertTrue(os.path.isfile(self.clip))

    setUp = PlaceTests.setUp


class StaticPutBackTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): put-back Over on a Static erase laid the
    original's whole glass area over both eyes. A Static erase is Add only."""

    def apply(self, method):
        import numpy as np
        import torch
        pb = P.PutBack("clip.mp4", {"mode": "Over", "opacity": 1.0, "parallax": 0.0, "track": []},
                       {"original": "orig.mp4", "method": method})
        pb.repaint_mask = pb.object_mask = "mask.mkv"
        bar = np.zeros((1, 8, 8, 3), np.uint8)
        bar[:, :, 3:5] = 255

        def read(path, idx, w, h):
            src = {"mask.mkv": np.full((1, 8, 8, 3), 255, np.uint8), "orig.mp4": bar}.get(path, np.zeros_like(bar))
            return np.repeat(src, len(idx), 0)
        pb._read = read
        left, _right = pb.apply(torch.full((1, 3, 8, 8), 0.5), torch.full((1, 3, 8, 8), 0.5), 0)
        return pb, float(left[0, 0, 0, 0])

    def test_static_is_add_only(self):
        pb, outside = self.apply("static")
        self.assertEqual(pb.modes, ("Add",))
        self.assertAlmostEqual(outside, 0.5, places=4, msg="Over pasted the original's glass area")

    def test_rose_keeps_over(self):
        pb, outside = self.apply("rose")
        self.assertEqual(pb.modes, ("Add", "Over"))
        self.assertAlmostEqual(outside, 0.0, places=4)



class WindowLengthTest(unittest.TestCase):
    """ROSE takes 4k+1 frames; a stretch shorter than the window was padded to the next 16k+1 (user,
    2026-10-15: a 70-frame clip ran 81 frames, 11 of them its last frame again)."""

    def test_a_short_clip_is_padded_to_the_next_4k_plus_1(self):
        from core.removal import erase as E
        self.assertEqual(E.plan_windows([True] * 70), [(0, 73)])
        flags = [False] * 100
        for i in range(20, 30):
            flags[i] = True
        self.assertEqual(E.plan_windows(flags), [(12, 29)])         # 12..38 with context: 26 -> 29
        self.assertEqual(E.plan_windows([True] * 5)[0][1], 17)      # never below 17

    def test_every_window_is_4k_plus_1_and_long_clips_keep_full_windows(self):
        from core.removal import erase as E
        for n in range(1, 300, 7):
            for a, length in E.plan_windows([True] * n):
                self.assertEqual((length - 1) % 4, 0, (n, length))
                self.assertLessEqual(length, E.WINDOW)
        self.assertTrue(all(length == E.WINDOW for _a, length in E.plan_windows([True] * 200)))


if __name__ == "__main__":
    unittest.main()
