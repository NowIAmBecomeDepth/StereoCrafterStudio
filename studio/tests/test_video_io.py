"""core.common.video_io readers and caches (audit 2026-09-28 #26 / #27), on a
small clip ffmpeg makes in a temp folder -- never the user's footage."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import tempfile
import threading
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

    def test_read_ahead_gives_the_same_frames_and_frees_the_clip(self):
        """Merging's chunks (user, 2026-10-30): the next run of frames read on a thread of its own while this
        one is used; a jump or a single frame read there and then; dropped, the clip can be moved."""
        import numpy as np
        plain = self.reader()
        want = {k: plain.get_batch(list(range(k, min(k + 4, 12)))).asnumpy() for k in (0, 4, 8)}
        plain.close()
        r = V.ReadAhead(self.reader(), name="test-read")
        for k in (0, 4, 8):
            got = r.get_batch(list(range(k, min(k + 4, 12)))).asnumpy()
            self.assertTrue(np.array_equal(got, want[k]), f"chunk {k}")
            if k < 8:
                self.assertEqual(r._next[0], tuple(range(k + 4, min(k + 8, 12))), "the next chunk read ahead")
        self.assertIsNone(r._next, "nothing past the end")
        self.assertTrue(np.array_equal(r.get_batch([2]).asnumpy()[0], want[0][2]), "a jump back")
        self.assertEqual(len(r), 12)
        names = {t.name.split("_")[0] for t in threading.enumerate()}
        self.assertIn("test-read", names)
        del r
        import gc
        gc.collect()
        self.assertNotIn("test-read", {t.name.split("_")[0] for t in threading.enumerate()}, "its thread ended")
        self.assertTrue(self.moved(), "the clip is still held open")

    def test_a_file_ffmpeg_cant_read_says_why(self):
        """A 12 GB user's Merging failed on every clip with only "reached EOF" in the log (2026-10-30)."""
        bad = self.dir / "broken_splatted2.mp4"
        bad.write_bytes(b"not a video at all" * 100)
        r = V.FFmpegRGBPipeReader(str(bad), 64, 48, 12.0, 12)
        self.addCleanup(r.close)
        with self.assertRaises(EOFError) as caught:
            r.get_batch([0])
        self.assertIn("broken_splatted2.mp4: FFmpeg said:", str(caught.exception))
        self.assertIn("Invalid data", str(caught.exception))



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



def _cuda() -> bool:
    import torch
    return torch.cuda.is_available()


@unittest.skipUnless(_cuda(), "the native-YUV read is for CUDA machines")
@unittest.skipUnless(_cuda(), "the GPU resize needs CUDA")
class DepthGpuResizeTest(unittest.TestCase):
    """Splatting's depth read at its own size and resized on the GPU (user, 2026-10-30: FFmpeg's CPU scale
    of 768x320 up to 2580x1080 was a fifth of a clip's time). The resize is the preview's own (cv2 bilinear
    on floats), and within a few codes of the FFmpeg scale it replaces."""

    def test_the_gpu_resize_is_the_previews_and_near_ffmpegs(self):
        import cv2
        import numpy as np
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        depth = Path(tmp.name) / "d.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "nullsrc=size=192x80:rate=24:duration=0.5,geq=lum='400+300*sin(X/11)*cos(Y/7)':cb=512:cr=512",
                        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error:lossless=1",
                        str(depth)], check=True)
        idx = list(range(6))
        native = V.FFmpegDepthPipeReader(str(depth), 192, 80, 10, 12, "yuv420p10le")
        ffmpeg = V.FFmpegDepthPipeReader(str(depth), 645, 270, 10, 12, "yuv420p10le")
        gpu = V.FFmpegDepthPipeReader(str(depth), 645, 270, 10, 12, "yuv420p10le", src_size=(192, 80))
        for r in (native, ffmpeg, gpu):
            self.addCleanup(r.close)
        self.assertEqual(gpu._frame_bytes, 192 * 80 * 2, "FFmpeg decodes it at its own size")
        got = gpu.get_batch(idx).asnumpy()
        self.assertEqual((got.shape, got.dtype), ((6, 270, 645, 1), np.float32))
        own = native.get_batch(idx).asnumpy()[..., 0].astype(np.float32)
        preview = np.stack([cv2.resize(f, (645, 270), interpolation=cv2.INTER_LINEAR) for f in own])
        self.assertLess(float(np.abs(got[..., 0] - preview).max()), 0.05, "the preview's resize")
        old = ffmpeg.get_batch(idx).asnumpy()[..., 0].astype(np.float32)
        diff = np.abs(got[..., 0] - old)
        self.assertLess(float(diff.mean()), 1.0)
        self.assertLess(float(diff.max()), 6.0, "within a few codes of FFmpeg's scale")
        self.assertEqual(V.FFmpegDepthPipeReader(str(depth), 192, 80, 10, 12, "yuv420p10le",
                                                 src_size=(192, 80))._gpu_from, None, "same size: as it was")


class NativeYuvReadTest(unittest.TestCase):
    """2026-10-27: a clip read at its own size comes as YUV and is converted in torch on the GPU
    (NVDEC where it decodes) -- faster at 10 bits, and exact, where FFmpeg's rgb48 ran up to 1.0
    of 255 dark (white at 254.0) and its rgb24 up to 3 codes low. Flat colours, against the
    BT.709 maths of the decoded codes."""

    def _flat(self, path, pix_fmt, y, u, v, w=64, h=32):
        import numpy as np
        cw = w // 2
        ch = h // 2 if "420" in pix_fmt else h
        frame = np.concatenate([np.full(w * h, y), np.full(cw * ch, u), np.full(cw * ch, v)]).astype(np.uint16)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-color_range", "tv",
                        "-colorspace", "bt709", "-s", f"{w}x{h}", "-i", "-", "-c:v", "ffv1", str(path)],
                       input=frame.tobytes() * 3, check=True)
        dec = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", pix_fmt, "-"], capture_output=True, check=True).stdout
        a = np.frombuffer(dec, np.uint16)
        y, u, v = int(a[0]), int(a[w * h]), int(a[w * h + cw * ch])   # the codes as stored
        yn, un, vn = (y - 64) / 876, (u - 512) / 896, (v - 512) / 896
        r, bl = yn + 1.5748 * vn, yn + 1.8556 * un
        return np.clip([r, (yn - 0.2126 * r - 0.0722 * bl) / 0.7152, bl], 0, 1) * 255

    def test_flat_10_bit_colours_come_out_exact(self):
        import numpy as np
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for pix_fmt in ("yuv420p10le", "yuv422p10le"):
                for yuv in ((940, 512, 512), (300, 512, 512), (700, 600, 450), (420, 300, 700)):
                    clip = Path(tmp) / f"{pix_fmt}_{yuv[0]}.mkv"
                    want = self._flat(clip, pix_fmt, *yuv)
                    r = V.open_rgb_reader(str(clip))
                    got = r.get_batch([1]).asnumpy()[0, 16, 32].astype(np.float64)
                    r.close()
                    self.assertTrue(np.allclose(got, want, atol=0.15), f"{pix_fmt} {yuv}: {got} vs {want}")

    def test_flat_8_bit_colours_come_out_rounded_exactly(self):
        import numpy as np
        w, h = 64, 32
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for i, (y, u, v) in enumerate(((235, 128, 128), (75, 128, 128), (180, 100, 160), (120, 150, 110))):
                clip = Path(tmp) / f"f{i}.mkv"
                frame = np.concatenate([np.full(w * h, y), np.full(w * h // 4, u), np.full(w * h // 4, v)]).astype(np.uint8)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                                "-color_range", "tv", "-colorspace", "bt709", "-s", f"{w}x{h}", "-i", "-",
                                "-c:v", "ffv1", str(clip)], input=frame.tobytes() * 3, check=True)
                yn, un, vn = (y - 16) / 219, (u - 128) / 224, (v - 128) / 224
                r, bl = yn + 1.5748 * vn, yn + 1.8556 * un
                want = np.round(np.clip([r, (yn - 0.2126 * r - 0.0722 * bl) / 0.7152, bl], 0, 1) * 255)
                rd = V.open_rgb_reader(str(clip))
                got = rd.get_batch([1]).asnumpy()[0, 16, 32]
                rd.close()
                self.assertEqual((str(got.dtype), got.tolist()), ("uint8", want.astype(int).tolist()), f"YUV {y},{u},{v}")

    def test_a_failed_native_read_falls_back_to_rgb48(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            clip = Path(tmp) / "c.mkv"
            self._flat(clip, "yuv420p10le", 500, 400, 640)
            r = V.open_rgb_reader(str(clip))
            self.assertIsNotNone(r._yuv)
            r._force_fallback = True       # as get_batch sets it on a short read
            f = r.get_batch([0, 1]).asnumpy()
            r.close()
            self.assertIsNone(r._yuv)
            self.assertEqual((f.shape, str(f.dtype)), ((2, 32, 64, 3), "float16"))



def _with_container_crop(src: Path, dest: Path, crop_right: int) -> None:
    """`src` (an MP4, moov after mdat) with a container crop: a 'clap' box in its video sample entry,
    `crop_right` columns off the right -- what a 4070 SUPER user's cutting tool wrote (2026-10-30)."""
    import struct
    buf = bytearray(src.read_bytes())
    assert buf.find(b"moov") > buf.find(b"mdat"), "chunk offsets stay valid"
    parents, s, e = [], 0, len(buf)
    for kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"):
        while struct.unpack(">4s", buf[s + 4:s + 8])[0] != kind:
            s += struct.unpack(">I", buf[s:s + 4])[0]
        parents.append(s)
        s, e = s + 8, s + struct.unpack(">I", buf[s:s + 4])[0]
    entry = parents[-1] + 16
    w, h = struct.unpack(">HH", buf[entry + 32:entry + 36])          # the visual sample entry's size
    clap = struct.pack(">I4s8i", 40, b"clap", w - crop_right, 1, h, 1, -crop_right, 2, 0, 1)
    end = entry + struct.unpack(">I", buf[entry:entry + 4])[0]
    buf[end:end] = clap
    for off in parents + [entry]:
        buf[off:off + 4] = struct.pack(">I", struct.unpack(">I", buf[off:off + 4])[0] + len(clap))
    dest.write_bytes(bytes(buf))


class ContainerCropTest(unittest.TestCase):
    """A clip whose container crops it (2026-10-30: a user's 2580-wide original with a 2-pixel 'clap'
    crop): FFmpeg 7.1+ applied it, ffprobe still said 2580, and Merging read 2578-wide rows as 2580 --
    the left eye sheared and green. Read with -apply_cropping codec, it is the same clip as without."""

    def setUp(self):
        if subprocess.run(["ffmpeg", "-v", "error", "-apply_cropping", "codec", "-f", "lavfi", "-i",
                           "color=s=16x16:d=0.04", "-f", "null", "-"], capture_output=True).returncode:
            self.skipTest("this FFmpeg applies no container crop (no -apply_cropping)")
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.plain, self.crop = Path(tmp.name) / "plain.mp4", Path(tmp.name) / "crop.mp4"
        make_clip(self.plain, size="128x64", frames=6)
        _with_container_crop(self.plain, self.crop, 2)
        info = V.get_video_stream_info(str(self.crop))
        self.assertEqual((info["width"], info["height"]), (128, 64), "ffprobe: the uncropped size")

    def frames(self, path, fallback=False):
        r = V.open_rgb_reader(str(path))
        r._force_fallback = fallback
        try:
            return r.get_batch(list(range(6))).asnumpy()
        finally:
            r.close()

    def test_the_rgb_read_is_the_uncropped_clip(self):
        import numpy as np
        want = self.frames(self.plain, fallback=True)
        got = self.frames(self.crop, fallback=True)
        self.assertEqual(got.shape, (6, 64, 128, 3))
        self.assertTrue(np.array_equal(got, want), "the crop stretched back by FFmpeg's scale")

    @unittest.skipUnless(_cuda(), "the native-YUV read is for CUDA machines")
    def test_the_native_yuv_read_is_the_uncropped_clip(self):
        import numpy as np
        want = self.frames(self.plain)
        got = self.frames(self.crop)
        self.assertEqual(got.shape, (6, 64, 128, 3))
        self.assertTrue(np.array_equal(got, want), "rows read at the wrong width: sheared and green")

    def test_erases_read_is_the_uncropped_clip(self):
        import numpy as np
        from core.removal import erase as E
        want = np.stack(list(E._reader(str(self.plain), 128, 64, "rgb24")))
        got = np.stack(list(E._reader(str(self.crop), 128, 64, "rgb24")))
        self.assertEqual(got.shape, want.shape)
        self.assertTrue(np.array_equal(got, want))


if __name__ == "__main__":
    unittest.main()
