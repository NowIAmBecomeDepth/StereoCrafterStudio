"""core.common.video_io readers and caches (audit 2026-09-28 #26 / #27), on a
small clip ffmpeg makes in a temp folder -- never the user's footage."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import video_io as V  # noqa: E402


def make_clip(path: Path, size="64x48", pix_fmt="yuv420p", frames=12) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=12", "-frames:v", str(frames), "-pix_fmt", pix_fmt,
                    "-c:v", "libx264" if pix_fmt == "yuv420p" else "libx265", str(path)], check=True)


class RGBPipeReaderTests(unittest.TestCase):
    """#26: the reader had no close(): its ffmpeg lived on holding the clip
    open (WinError 32 when the clip is moved to finished/), one per clip;
    a restart killed the old one without reaping it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        make_clip(self.clip)

    def reader(self):
        return V.FFmpegRGBPipeReader(str(self.clip), 64, 48, 12.0, 12)

    def moved(self):
        """The clip can be moved (nothing holds it open) -- and moved back."""
        dest = self.dir / "finished.mp4"
        for _ in range(20):
            try:
                os.replace(self.clip, dest)
                os.replace(dest, self.clip)
                return True
            except PermissionError:
                time.sleep(0.1)
        return False

    def test_close_ends_its_ffmpeg_and_lets_the_clip_move(self):
        r = self.reader()
        self.assertEqual(r.get_batch([0, 1]).asnumpy().shape, (2, 48, 64, 3))
        proc = r._proc
        r.close()
        self.assertIsNotNone(proc.poll(), "ffmpeg still running after close()")
        self.assertTrue(self.moved(), "the clip is still held open")

    def test_dropping_the_reader_ends_its_ffmpeg(self):
        r = self.reader()
        r.get_batch([0])
        proc = r._proc
        del r
        gc.collect()
        self.assertIsNotNone(proc.wait(5), "ffmpeg outlived its reader")

    def test_a_restart_reaps_the_old_ffmpeg(self):
        r = self.reader()
        self.addCleanup(lambda: getattr(r, "close", lambda: None)())
        r.get_batch([6])
        old = r._proc
        r.get_batch([0])            # backwards: restarts
        self.assertIsNot(r._proc, old)
        self.assertIsNotNone(old.poll(), "the old ffmpeg was not reaped")



class StreamInfoCacheTests(unittest.TestCase):
    """#27: get_video_stream_info cached by path for the whole session and
    handed every caller the same dict: a depth map re-rendered under its
    name (8-bit -> 10-bit) kept its old pix_fmt, and one caller's edit to
    the dict changed it for all."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clip = Path(tmp.name) / "clip_depth.mp4"
        V._INFO_CACHE.clear()

    def test_a_re_rendered_file_is_probed_again(self):
        make_clip(self.clip)
        self.assertEqual(V.get_video_stream_info(str(self.clip))["pix_fmt"], "yuv420p")
        time.sleep(0.05)
        make_clip(self.clip, pix_fmt="yuv420p10le")     # the same name, re-rendered at 10 bits
        self.assertEqual(V.get_video_stream_info(str(self.clip))["pix_fmt"], "yuv420p10le")

    def test_callers_get_their_own_copy(self):
        make_clip(self.clip)
        V.get_video_stream_info(str(self.clip))["pix_fmt"] = "changed by a caller"
        self.assertEqual(V.get_video_stream_info(str(self.clip))["pix_fmt"], "yuv420p")


class TransferTagTests(unittest.TestCase):
    """bug sweep 2026-10-09 #17: the probe asked ffprobe for
    `transfer_characteristics`, which it doesn't know (it says color_transfer):
    the transfer was never read, PQ / HLG chains came out with no transfer tag
    (players show SDR) and HDR was never detected."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clip = Path(tmp.name) / "hdr10.mp4"
        V._INFO_CACHE.clear()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=64x48:rate=12", "-frames:v", "6",
                        # setparams: this ffmpeg ignores -color_trc / -color_primaries
                        "-vf", "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
                        "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-movflags", "+write_colr", str(self.clip)],
                       check=True)

    def test_the_transfer_is_read(self):
        info = V.get_video_stream_info(str(self.clip))
        self.assertEqual(info.get("transfer_characteristics"), "smpte2084")

    def test_the_pipe_encoder_tags_it(self):
        from unittest import mock
        info = V.get_video_stream_info(str(self.clip))
        # the CPU encoder: NVENC's probe would run through the mocked Popen
        with mock.patch.object(V, "nvenc_available", lambda *a, **k: False),                 mock.patch.object(V.subprocess, "Popen") as popen:
            V.start_ffmpeg_pipe_process(64, 48, str(self.clip.with_name("out.mp4")), 12.0, video_stream_info=info)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-color_trc") + 1], "smpte2084", " ".join(map(str, cmd)))


class DepthBitScaleTests(unittest.TestCase):
    """bug sweep 2026-10-09 A11: the 10-bit depth reader decided shift-or-scale
    from the first batch it read; a near-black first batch latched "no scaling"
    and every later batch came back on the 16-bit scale, up to 64x too large."""

    def test_a_clip_opening_on_black_reads_right_after_it(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dark_open_depth.mkv"
            w, h = 32, 16
            codes = [10] * 4 + [800] * 4                    # 10-bit codes: near-black, then bright
            raw = b"".join(np.full((h, w), c, "<u2").tobytes() for c in codes)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray10le",
                            "-s", f"{w}x{h}", "-r", "12", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray10le",
                            str(path)], input=raw, check=True)
            r = V.FFmpegDepthPipeReader(str(path), w, h, 10, len(codes), pix_fmt="gray10le")
            try:
                first = r.get_batch([0, 1, 2, 3]).asnumpy()
                later = r.get_batch([4, 5, 6, 7]).asnumpy()
            finally:
                r.close()
            self.assertEqual(int(first.max()), 10)
            self.assertEqual(int(later.max()), 800)


class NoConsoleWindowTest(unittest.TestCase):
    """audit 2026-09-28 (minor): video_io started ffmpeg / ffprobe without
    CREATE_NO_WINDOW -- under the .exe (no console) each one flashed a
    console window."""

    def test_every_process_video_io_starts_has_no_window(self):
        import ast
        tree = ast.parse(Path(V.__file__).read_text(encoding="utf-8"))
        bare = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in ("Popen", "run", "check_output", "call", "check_call")
                    and not any(k.arg == "creationflags" for k in node.keywords)):
                bare.append(node.lineno)
        self.assertEqual(bare, [], "subprocess calls without creationflags at these lines")


class DepthSeekTest(unittest.TestCase):
    """audit 2026-09-28 (PERF): FFmpegDepthPipeReader.seek read the whole skip
    in ONE read -- a custom range starting at frame 5000 of a 1080p 16-bit map
    asked for a 20 GB bytes object. It now skips in bounded pieces."""

    def test_a_long_skip_is_read_in_bounded_pieces(self):
        import io
        reads = []

        class _Out(io.RawIOBase):
            def read(self, n=-1):
                reads.append(n)
                return bytes(n) if n <= 2 ** 28 else b""   # never hand back a huge buffer

            def readinto(self, b):
                reads.append(len(b))
                return len(b)

        class _Proc:
            stdout = _Out()

        r = V.FFmpegDepthPipeReader.__new__(V.FFmpegDepthPipeReader)
        r._proc, r._next_index, r._frame_bytes = _Proc(), 0, 1920 * 1080 * 2
        self.addCleanup(setattr, r, "_proc", None)
        r.seek(5000)
        self.assertEqual(r._next_index, 5000)
        self.assertLessEqual(max(reads), 64 * 2 ** 20, f"one read of {max(reads) / 2 ** 30:.1f} GB")
        self.assertEqual(sum(reads), 5000 * r._frame_bytes)


class NvencFallbackTest(unittest.TestCase):
    """audit 2026-09-28 (minor): video_io's fallbacks read CUDA_AVAILABLE,
    imported by value -- False forever -- so with no encoding options the
    encode was always CPU. They ask nvenc_available() now."""

    def test_the_pipe_fallback_takes_nvenc_when_it_is_there(self):
        from unittest import mock
        cmds = []

        class _P:
            stdin = stdout = stderr = None
            def poll(self):
                return None

        def popen(cmd, *a, **k):
            cmds.append(cmd)
            return _P()
        with mock.patch.object(V, "nvenc_available", return_value=True),                 mock.patch.object(V.subprocess, "Popen", popen),                 mock.patch.object(V, "_StderrDrain", lambda *a, **k: None, create=True):
            V.start_ffmpeg_pipe_process(64, 32, str(Path(tempfile.gettempdir()) / "x.mp4"), 24.0)
        self.assertTrue(cmds, "ffmpeg was not started")
        self.assertIn("h264_nvenc", cmds[0])


if __name__ == "__main__":
    unittest.main()
