"""Edge Fill's cheap covers (user, 2026-11-07; core/removal/edge_fill.cover): Black Bar paints the
measured strip black, Zoom crops it off and scales the rest back up -- both on both eyes alike (user:
"the crop and zoom would have to affect both eyes"), dual-eye splats too (user: "make sure dual eye
outputs are supported").

The clips are test_edge_fill's made-up ones: the left eye and the right eye are the same picture,
the right eye with a dark bar over its left STRIP columns. So once the strip is covered alike on both
eyes, the two eyes match again everywhere."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
from core.removal import edge_fill as F  # noqa: E402
from core.removal import erase as E  # noqa: E402
from studio.tests import test_edge_fill as T  # noqa: E402  -- a module: its tests aren't run twice
from studio.tests.test_edge_fill import H, N, STRIP, W, _encode, _mask_panel, _picture  # noqa: E402

DARK = (20, 10, 40)


def _frames(path, w):
    return [f.astype(int) for f in E._reader(path, w, H, "rgb24")]


def _no_recycle():
    return mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False)


class CoverTest(unittest.TestCase):
    setUp = T.EdgeFillTest.setUp
    tearDown = T.EdgeFillTest.tearDown

    def cover(self, method, stop=None, **kw):
        g = F.find_groups(self.out)[0]
        with _no_recycle():
            return g, F.cover(g, F.find_mask(g, self.masks), lambda *a: None, stop or threading.Event(),
                              method=method, settings={}, **kw)

    def expected_px(self):
        """The cover: the measured strip (ragged rows take it past STRIP) plus STRIP_PAD, even."""
        g = F.find_groups(self.out)[0]
        px = F.measure(F.find_mask(g, self.masks))["eyes"]["R"]["left"]
        self.assertGreaterEqual(px, STRIP)
        return F._even_up(px + F.STRIP_PAD)

    def test_black_bar_blacks_the_same_columns_in_both_eyes(self):
        before_l = _frames(F.find_groups(self.out)[0].files["L"], W)
        g, r = self.cover("Black Bar")
        px = self.expected_px()
        self.assertEqual(r["method"], "Black Bar")
        self.assertEqual(r["edges"], [{"eye": "both", "edge": "left", "strip": px}])
        self.assertEqual(sorted(r["files"]), sorted(g.files.values()), "every file, the left eye's too")
        sbs, left, right = _frames(g.files["sbs"], 2 * W), _frames(g.files["L"], W), _frames(g.files["R"], W)
        for i in (0, N // 2, N - 1):
            for name, eye in (("sbs L", sbs[i][:, :W]), ("sbs R", sbs[i][:, W:]), ("L", left[i]), ("R", right[i])):
                self.assertLess(eye[:, :px].max(), 24, f"{name} frame {i}: the strip is black")
                self.assertGreater(eye[:, px + 2:].mean(), 60, f"{name} frame {i}: past it, the picture")
            # past the bar the left eye is as it was, and both eyes match again
            self.assertLess(np.abs(left[i][:, px + 2:] - before_l[i][:, px + 2:]).mean(), 3.0)
            self.assertLess(np.abs(left[i] - right[i]).mean(), 3.0, f"frame {i}: the eyes match")
            self.assertLess(np.abs(sbs[i][:, :W] - sbs[i][:, W:]).mean(), 3.0)
        self.assertEqual(len(sbs), N)
        self.assertTrue(F.done(g))
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, F.ORIGINALS_DIR))),
                         sorted(os.path.basename(p) for p in g.files.values()))
        rec = F._record(self.out)[os.path.basename(g.files["sbs"])]
        self.assertEqual(rec["method"], "Black Bar")

    def test_zoom_crops_and_scales_both_eyes_alike(self):
        g = F.find_groups(self.out)[0]
        fmt = {k: subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=codec_name,width,height", "-of", "csv=p=0", p],
                                 capture_output=True, text=True).stdout for k, p in g.files.items()}
        g, r = self.cover("Zoom")
        px = self.expected_px()
        self.assertEqual(r["edges"], [{"eye": "both", "edge": "left", "strip": px}])
        for k, p in g.files.items():
            now = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=codec_name,width,height", "-of", "csv=p=0", p],
                                 capture_output=True, text=True).stdout
            self.assertEqual(now, fmt[k], f"{k}: the same codec and size")
        sbs, left, right = _frames(g.files["sbs"], 2 * W), _frames(g.files["L"], W), _frames(g.files["R"], W)
        orig_l = _frames(F.kept_original(g.files["L"]), W)
        for i in (0, N // 2, N - 1):
            for name, eye in (("sbs R", sbs[i][:, W:]), ("R", right[i])):
                dark = np.abs(eye[:, :8] - np.array(DARK)).mean()
                self.assertGreater(dark, 30, f"{name} frame {i}: the dark strip is cropped off")
            self.assertLess(np.abs(left[i] - right[i]).mean(), 4.0, f"frame {i}: the eyes match")
            self.assertLess(np.abs(sbs[i][:, :W] - sbs[i][:, W:]).mean(), 4.0)
            # zoomed: the first column now shows what was `px` in, and the left eye changed too
            self.assertLess(np.abs(left[i][H // 2, 0] - orig_l[i][H // 2, px]).max(), 12)
            self.assertGreater(np.abs(left[i] - orig_l[i]).mean(), 3.0)
        self.assertEqual(len(left), N)

    def test_a_second_cover_starts_from_the_original(self):
        g, _ = self.cover("Zoom")
        orig_l = _frames(F.kept_original(g.files["L"]), W)
        with mock.patch.object(F, "_recycle", lambda p: False):
            g, r = self.cover("Black Bar")
        px = self.expected_px()
        left = _frames(g.files["L"], W)
        self.assertLess(np.abs(left[3][:, px + 2:] - orig_l[3][:, px + 2:]).mean(), 3.0,
                        "a bar over the original, not over the zoom")

    def test_a_stop_leaves_the_files(self):
        g = F.find_groups(self.out)[0]
        ids = {k: F._file_id(p) for k, p in g.files.items()}
        strips = F.measure(F.find_mask(g, self.masks))      # measured: the stop lands in the writing
        stop = threading.Event()
        stop.set()
        with self.assertRaises(E.Stopped):
            self.cover("Black Bar", stop=stop, strips=strips)
        self.assertEqual({k: F._file_id(p) for k, p in g.files.items()}, ids)
        self.assertFalse(F.done(g))
        self.assertEqual([n for n in os.listdir(self.out) if n.endswith(".part")], [])

    def test_no_strip_is_skipped(self):
        strips = {"eyes": {"R": {"left": 0, "right": 0}}, "width": W,
                  "rows": {"R": {"left": np.zeros((N, H), np.uint16), "right": np.zeros((N, H), np.uint16)}}}
        g, r = self.cover("Black Bar", strips=strips)
        self.assertIn("skipped", r)
        self.assertFalse(F.done(g))


class DualEyeCoverTest(unittest.TestCase):
    """A dual-eye splat (_splatted2D: the left eye's grid row over the right's) leaves a strip on the
    right eye's left edge AND the left eye's right edge: both sides are covered on both eyes."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "merged")
        self.masks = os.path.join(self.tmp, "splats")
        os.makedirs(self.out)
        os.makedirs(self.masks)
        eyes = [_picture(i) for i in range(N)]
        left = [e.copy() for e in eyes]
        right = [e.copy() for e in eyes]
        for a, b in zip(left, right):
            a[:, W - STRIP:] = DARK
            b[:, :STRIP] = DARK
        _encode(os.path.join(self.out, f"shot_{W}_merged_full_sbs.mp4"),
                [np.hstack([a, b]) for a, b in zip(left, right)], ["-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p"])
        mask_l = [_mask_panel(i)[:, ::-1] for i in range(N)]       # its strip on the right edge
        _encode(os.path.join(self.masks, f"shot_{W}_splatted2D.mkv"),
                [np.vstack([np.hstack([ml, _picture(i)]), np.hstack([_mask_panel(i), _picture(i)])])
                 for i, ml in enumerate(mask_l)], ["-c:v", "ffv1", "-pix_fmt", "bgr0"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cover(self, method):
        g = F.find_groups(self.out)[0]
        with _no_recycle():
            return g, F.cover(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(),
                              method=method, settings={})

    def test_black_bar_covers_both_sides_of_both_eyes(self):
        meas = F.measure(F.find_mask(F.find_groups(self.out)[0], self.masks))["eyes"]
        self.assertGreaterEqual(meas["L"]["right"], STRIP)
        self.assertEqual(meas["L"]["right"], meas["R"]["left"])
        px = F._even_up(meas["R"]["left"] + F.STRIP_PAD)
        g, r = self.cover("Black Bar")
        self.assertEqual([(e["edge"], e["strip"]) for e in r["edges"]], [("left", px), ("right", px)])
        for fr in _frames(g.files["sbs"], 2 * W)[::5]:
            for eye in (fr[:, :W], fr[:, W:]):
                self.assertLess(eye[:, :px].max(), 24)
                self.assertLess(eye[:, W - px:].max(), 24)
                self.assertGreater(eye[:, px + 2:W - px - 2].mean(), 60)
            self.assertLess(np.abs(fr[:, :W] - fr[:, W:]).mean(), 3.0, "the eyes match")

    def test_zoom_crops_both_sides_of_both_eyes(self):
        g, r = self.cover("Zoom")
        for fr in _frames(g.files["sbs"], 2 * W)[::5]:
            for eye in (fr[:, :W], fr[:, W:]):
                for cols in (eye[:, :6], eye[:, -6:]):
                    self.assertGreater(np.abs(cols - np.array(DARK)).mean(), 30, "no dark strip left")
            self.assertLess(np.abs(fr[:, :W] - fr[:, W:]).mean(), 4.0, "the eyes match")


class GraphTest(unittest.TestCase):
    def test_cover_px_scales_pads_and_rounds_to_even(self):
        self.assertEqual(F.cover_px(0, 960, 1920), 0)
        self.assertEqual(F.cover_px(5, 1920, 1920), 0, "under MIN_STRIP_FRACTION: left alone")
        self.assertEqual(F.cover_px(40, 1920, 1920), 48)
        self.assertEqual(F.cover_px(20, 960, 1920), 48, "the mask's px at the eye's width")
        self.assertEqual(F.cover_px(41, 1920, 1920), 50)
        self.assertEqual(F.cover_px(40, 1920, 1920, 0.5), 24, "Half SBS: each eye squeezed")

    def test_the_zoom_keeps_the_shape(self):
        g = F.cover_graph("Zoom", [(0, 1920)], 1080, 48, 0)
        self.assertEqual(g, "crop=1872:1052:48:14,scale=1920:1080:flags=lanczos")
        two = F.cover_graph("Zoom", [(0, 1920), (1920, 1920)], 1080, 48, 0)
        self.assertIn("crop=1872:1052:1968:14", two)
        self.assertTrue(two.startswith("split=2[e0][e1];") and two.endswith("hstack=inputs=2"), two)

    def test_black_bar_boxes_each_eye(self):
        g = F.cover_graph("Black Bar", [(0, 960), (960, 960)], 540, 24, 10)
        self.assertEqual(g.count("drawbox"), 4)
        self.assertIn("drawbox=x=960:y=0:w=24", g)
        self.assertIn("drawbox=x=1910:y=0:w=10", g)


if __name__ == "__main__":
    unittest.main()
