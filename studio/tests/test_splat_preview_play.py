"""Splatting preview playback and the job queue (audit 2026-09-28, minor).

- a play frame that failed left `_play_inflight` set: playback was dead until
  the clip was reloaded;
- a Seek still queued when a Render came was dropped for it -- the render drew
  the old frame, and the picture and counter jumped back;
- in Wigglegram, playback cached the single "frame" image (stale: wiggle shows
  the two eyes), so the loop replayed one frozen frame;
- a play frame rendered with the old settings could be cached after a change.

No engine and no worker: the queue and the result handler are driven directly.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.splatting.preview import SplattingPreview  # noqa: E402

_app = QGuiApplication.instance() or QGuiApplication(sys.argv)


def _image(shade: int) -> QImage:
    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(shade)
    return img


class PlayTests(unittest.TestCase):
    def setUp(self):
        self.pv = SplattingPreview(lambda: {"loop_playback": True})
        self.pv._thread = object()          # no worker: jobs stay queued for the test to read
        self.pv._synced = True
        self.pv._loaded, self.pv._frames, self.pv._fps = True, 10, 24.0

    def tearDown(self):
        self.pv._play_timer.stop()

    def result(self, kind, frame, **extra):
        r = {"kind": kind, "frame": frame, "frames": 10, "index": 0, "count": 1, "loaded": True,
             "writes": {}, "info": {}, "fps": 24.0}
        r.update(extra)
        return r

    def queued(self):
        return [(j[0], j[1]) for j in self.pv._jobs if j is not None]

    def play_one(self):
        self.pv._set_playing(True, 1)
        self.pv._play_tick()
        self.assertTrue(self.pv._play_inflight)
        return self.pv._frame

    def test_a_failed_play_frame_does_not_kill_playback(self):
        self.play_one()
        self.pv._on_done({"kind": "play", "error": "boom"})
        self.assertFalse(self.pv._play_inflight, "playback stays dead after one failed frame")
        self.pv._jobs.clear()
        self.pv._set_playing(True, 1)
        self.pv._play_tick()
        self.assertIn("play", [k for k, _a in self.queued()], "Play asked for no frame after the failure")

    def test_a_render_after_a_queued_seek_keeps_the_seek_frame(self):
        self.pv.seek(7)
        self.pv._submit("render")
        self.assertEqual(self.queued(), [("seek", 7)], "the seek's frame was dropped for a render")

    def test_wigglegram_playback_is_not_cached_as_a_frozen_frame(self):
        frame = self.play_one()
        self.pv.provider.put("frame", _image(0x111111))   # the last single-image frame, stale
        self.pv._wiggle = True
        self.pv._on_done(self.result("play", frame))
        self.assertNotIn(frame, self.pv._play_cache, "a wigglegram frame was cached as the stale single image")

    def test_a_play_frame_from_before_a_settings_change_is_not_cached(self):
        frame = self.play_one()
        self.pv.provider.put("frame", _image(0x222222))
        self.pv._drop_play_cache()                         # a setting changed while it rendered
        self.pv._on_done(self.result("play", frame))
        self.assertNotIn(frame, self.pv._play_cache, "a frame of the old settings was cached")

    def test_a_current_play_frame_is_cached(self):
        frame = self.play_one()
        self.pv.provider.put("frame", _image(0x333333))
        self.pv._on_done(self.result("play", frame))
        self.assertIn(frame, self.pv._play_cache)


class ToQImageTest(unittest.TestCase):
    """audit 2026-09-28 (PERF): _to_qimage no longer copies the frame a second
    time. The QImage wraps the bytes; PySide6 keeps them alive for as long as
    any (shallow) copy of the image lives."""

    def test_the_pixels_outlive_the_first_image(self):
        import gc
        import numpy as np
        from PIL import Image
        from studio.stages.splatting.preview import _to_qimage
        arr = np.random.default_rng(1).integers(0, 255, (90, 160, 3), dtype=np.uint8)
        q = _to_qimage(Image.fromarray(arr))
        kept = QImage(q)                        # what the provider / play cache hold
        del q
        gc.collect()
        back = np.frombuffer(kept.constBits(), np.uint8).reshape(90, kept.bytesPerLine())[:, :160 * 3]
        np.testing.assert_array_equal(back.reshape(90, 160, 3), arr)


if __name__ == "__main__":
    unittest.main()
