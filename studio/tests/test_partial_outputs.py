"""Partial outputs never sit under a final name (audit 2026-09-28 #5).

core/common/partial_output and the encoders built on it, with real ffmpeg on
tiny synthetic frames in a temp folder: an encode that is stopped or fails
leaves the previous good output untouched and no partial file behind; one
that finishes replaces it.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_partial_outputs -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import partial_output as P  # noqa: E402


class HelperTest(unittest.TestCase):
    def test_part_names_hide_from_clip_globs(self):
        self.assertEqual(P.part_path("a/x.mp4"), "a/x.mp4.part")
        self.assertEqual(P.final_ext("x.mov.part"), ".mov")
        self.assertEqual(P.ffmpeg_output_args("x.mkv.part"), ["-f", "matroska", "x.mkv.part"])
        self.assertEqual(P.ffmpeg_output_args("x.mp4"), ["x.mp4"])

    def test_publish_and_discard(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            final, part = tmp / "x.mp4", tmp / "x.mp4.part"
            final.write_bytes(b"old"); part.write_bytes(b"new")
            self.assertTrue(P.publish(str(part), str(final)))
            self.assertEqual((final.read_bytes(), part.exists()), (b"new", False))
            part.write_bytes(b"torn")
            P.discard(str(part))
            self.assertEqual((final.read_bytes(), part.exists()), (b"new", False))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SplatRenderEndTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A4): a stopped or failed Splatting render waited up
    to 180 s for ffmpeg to finish a file it then deleted, and a Frame Break
    mask pipe that failed was left running while its .part was deleted (on
    Windows the delete failed). Both are reaped now. (The render itself runs
    in checks/check_splat_resplat.py, which stops one.)"""

    def test_an_abandoned_render_reaps_its_encoders(self):
        src = (Path(__file__).resolve().parents[2] / "core/splatting/render_processor.py").read_text(encoding="utf-8")
        shut = src[src.index("def _shutdown_pipe(proc, label):"):src.index("return ok", src.index("def _shutdown_pipe"))]
        self.assertLess(shut.index("if not encoding_successful:"), shut.index("proc.wait(timeout=180)"))
        self.assertIn("reap(proc)", shut)
        fb = src[src.index("[FRAME BREAK] Mask write failed"):]
        self.assertLess(fb.index("reap(frame_break_mask_process)"), fb.index("frame_break_mask_process = None"))


class _StopWhenWriting(threading.Event):
    """Stop pressed the moment ffmpeg has opened its output (the part, or --
    before the fix -- the final file itself)."""

    def __init__(self, final: Path, old: bytes):
        super().__init__()
        self.final, self.old = final, old

    def is_set(self):
        part = Path(P.part_path(self.final))
        if part.exists() or (self.final.exists() and self.final.read_bytes() != self.old):
            self.set()
        return super().is_set()


class EncodeFramesTest(unittest.TestCase):
    """core.common.video_io.encode_frames_to_mp4 (Inpainting V1's encoder)."""

    FRAMES = 1500     # enough that the encode is still running when Stop lands

    @classmethod
    def setUpClass(cls):
        cls.src = Path(tempfile.mkdtemp())
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc2=size=320x180:rate=24:duration={cls.FRAMES / 24}",
                        str(cls.src / "%05d.png")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pngs = self.tmp / "pngs"
        shutil.copytree(self.src, self.pngs)
        self.final = self.tmp / "clip_inpainted_right_eye.mp4"
        self.old = b"the previous good output"
        self.final.write_bytes(self.old)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def encode(self, stop=None):
        from core.common.video_io import encode_frames_to_mp4
        return encode_frames_to_mp4(str(self.pngs), str(self.final), 24, self.FRAMES, None, stop_event=stop)

    def left_behind(self):
        return sorted(p.name for p in self.tmp.iterdir())

    def test_stop_keeps_the_previous_output(self):
        stop = _StopWhenWriting(self.final, self.old)
        self.assertFalse(self.encode(stop))
        self.assertTrue(stop.is_set(), "the encode ended before Stop could land")
        self.assertEqual(self.final.read_bytes(), self.old, "Stop left a truncated file under the final name")
        self.assertEqual(self.left_behind(), [self.final.name], "a partial file or the frames were left behind")

    def test_a_finished_encode_replaces_it(self):
        self.assertTrue(self.encode())
        data = self.final.read_bytes()
        self.assertNotEqual(data, self.old)
        self.assertIn(b"ftyp", data[:64])
        self.assertEqual(self.left_behind(), [self.final.name])


class DetectTextTest(unittest.TestCase):
    """Splatting's Detect Text mask render (submask.py): Cancel mid-render
    must keep the clip's previous _submask.mp4, not leave it truncated."""

    def setUp(self):
        from studio.legacy import install
        install()
        sys.path.insert(0, str(REPO / "studio" / "tests" / "checks"))
        import _fixtures
        self.tmp = Path(tempfile.mkdtemp())
        _fixtures.copy("small", self.tmp / "fx")
        self.clip = self.tmp / "fx" / "src" / "clipA.mp4"
        from core.ui.subtitle_mask_editor import submask_output_path
        self.final = Path(submask_output_path(str(self.clip)))
        self.old = b"the previous good mask"
        self.final.write_bytes(self.old)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, cancel_after=None, overstate=0):
        import cv2
        from studio.stages.splatting.submask import SubtitleMask
        sm = SubtitleMask(None)
        cap = cv2.VideoCapture(str(self.clip))
        sm.video_path, sm.n_frames = str(self.clip), int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) + overstate
        sm.fps, sm.vw, sm.vh = cap.get(cv2.CAP_PROP_FPS) or 24.0, int(cap.get(3)), int(cap.get(4))
        cap.release()
        done = threading.Event()
        result = {}

        def on_progress(n, total):
            if cancel_after is not None and n >= cancel_after:
                sm.cancel()

        def on_done(out_path, settings, n, cancelled, error):
            result.update(out=out_path, n=n, cancelled=cancelled, error=error)
            done.set()

        self.assertIsNone(sm.save("0", str(sm.n_frames - 1), on_progress, on_done))
        self.assertTrue(done.wait(60))
        return result

    def left_behind(self):
        return sorted(p.name for p in self.final.parent.iterdir() if "submask" in p.name)

    def test_cancel_keeps_the_previous_mask(self):
        r = self.render(cancel_after=10)
        self.assertTrue(r["cancelled"])
        self.assertEqual(self.final.read_bytes(), self.old, "Cancel left a truncated mask under the final name")
        self.assertEqual(self.left_behind(), [self.final.name])

    # bug sweep 2026-10-09 (A6): the decode running out before To (a frame count cv2
    # overstates) published a short mask as a success
    def test_a_render_that_runs_out_early_keeps_the_previous_mask(self):
        r = self.render(overstate=10)
        self.assertTrue(r["error"] and "previous mask was kept" in r["error"], r)
        self.assertEqual(self.final.read_bytes(), self.old, "a short mask replaced the good one")
        self.assertEqual(self.left_behind(), [self.final.name])

    def test_a_finished_render_replaces_it(self):
        r = self.render()
        self.assertFalse(r["cancelled"] or r["error"])
        self.assertNotEqual(self.final.read_bytes(), self.old)
        self.assertEqual(self.left_behind(), [self.final.name])


if __name__ == "__main__":
    unittest.main()
