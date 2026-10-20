"""Erase from Clip's Pre-fill and Refine at Full Size (user, 2026-10-15; core/removal/erase.py).

ROSE sees the clip un-blanked under the mask, so a big object (a person filling 30% of a close-up) came
back redrawn: pre-filled, the hole is a smooth guess from around it. Refine runs ROSE again at the clip's
own size in tiles over the first pass, its tone kept from the first pass. ROSE is stood in for by a worker
that hands its input back: what's tested is what goes in and what's put together after."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.removal import erase as E  # noqa: E402


def _scene(h=96, w=160):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    bg = np.stack([60 + 100 * xx / w, 80 + 60 * yy / h, 120 + 0 * xx], -1)
    return bg


class PrefillTest(unittest.TestCase):
    def test_the_object_is_gone_and_the_hole_smooth(self):
        bg = _scene()
        frame = bg.copy()
        hole = np.zeros(bg.shape[:2], bool)
        hole[20:80, 50:110] = True
        frame[hole] = (230, 40, 200)                      # the object: magenta, nothing like the scene
        out = E.prefill_frames(frame[None].astype(np.uint8), hole[None])[0].astype(np.float32)
        inside = out[hole]
        self.assertGreater(float(np.abs(inside - (230, 40, 200)).sum(axis=1).min()), 60, "the object shows through")
        self.assertLess(float(np.abs(inside.mean(0) - bg[hole].mean(0)).max()), 25, "not the scene's colour")
        self.assertTrue(np.array_equal(out[~hole], frame[~hole].astype(np.uint8)), "outside the hole changed")
        # smooth: no outline drawn inside (the coarse-to-fine fill's rings traced a silhouette)
        gy = np.abs(np.diff(out, axis=0))[hole[1:] & hole[:-1]].mean()
        self.assertLess(float(gy), 3.0)

    def test_auto_switches_on_for_a_big_object(self):
        small = {0: np.zeros((3, 100, 100), np.uint8)}
        small[0][:, 40:50, 40:50] = 255                    # 1%
        big = {0: np.zeros((3, 100, 100), np.uint8)}
        big[0][:, 20:70, 20:60] = 255                      # 20%
        self.assertLess(E._mask_share(small), E.PREFILL_AUTO_SHARE)
        self.assertGreaterEqual(E._mask_share(big), E.PREFILL_AUTO_SHARE)


class RefineTilesTest(unittest.TestCase):
    def test_3_by_2_at_1080p_and_sides_of_16(self):
        t = E.refine_tiles(1920, 1038)
        self.assertEqual(len(t), 6)
        for ya, yb, xa, xb in t:
            self.assertLessEqual((xb - xa, yb - ya), E.REFINE_TILE)
            self.assertEqual(((xb - xa) % 16, (yb - ya) % 16), (0, 0))
        covered = np.zeros((1038, 1920), bool)
        for ya, yb, xa, xb in t:
            covered[ya:yb, xa:xb] = True
        self.assertTrue(covered.all())


WORKER = (
    "import json, os, sys, numpy as np\n"
    "job = json.load(open(sys.argv[1]))\n"
    "open(sys.argv[1] + '.ran', 'w').write(str(len(job['chunks'])))\n"
    "for k, c in enumerate(job['chunks']):\n"
    "    print('CHUNK', k + 1, len(job['chunks']), flush=True)\n"
    "    np.save(c['out'], np.load(c['frames']))\n"
    "    print('DONE', k + 1, '0s', flush=True)\n")


class EraseWithBothTest(unittest.TestCase):
    """A whole erase, ROSE stood in by a worker that hands its input back: the first pass then shows what
    pre-fill gave it, the full-size pass what the first pass gave it."""

    def run_erase(self, prefill, refine, seconds=1, **kw):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        clip = tmp / "shot.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x4060a0:size=320x192:rate=24:d={seconds}",
                        "-c:v", "libx264", "-pix_fmt", "yuv444p", "-qp", "0", str(clip)], check=True)
        worker = tmp / "copy_worker.py"
        worker.write_text(WORKER)
        hole = np.zeros((192, 320), bool)
        hole[40:150, 100:220] = True                       # 21% of the frame: Auto turns pre-fill on
        jobs = []
        real_run = E._run_worker

        def run(job_path, *a, **k):
            jobs.append(job_path)
            if len(jobs) == 1:                             # the first pass's input, before the worker
                ch = json.load(open(job_path, encoding="utf-8"))["chunks"][0]
                self.first_masks = np.load(ch["masks"])
                self.first_frames = np.load(ch["frames"])
            return real_run(job_path, *a, **k)
        # the object: magenta in the clip -- painted into every frame read
        real_reader = E._reader

        def reader(path, w, h, pix_fmt, scale=None):
            for f in real_reader(path, w, h, pix_fmt, scale):
                f = f.copy()
                m = hole if scale is None else (np.array(__import__("cv2").resize(hole.astype(np.uint8), scale,
                                                                                  interpolation=0)) > 0)
                f[m] = (65535, 0, 65535) if f.dtype == np.uint16 else (255, 0, 255)
                yield f
        with mock.patch.object(E.rose_env, "ready", return_value=True), \
                mock.patch.object(E.rose_env, "python", return_value=Path(sys.executable)), \
                mock.patch.object(E.rose_env, "WORKER", worker), \
                mock.patch.object(E, "frame_mask", lambda layers, i, size: hole), \
                mock.patch.object(E, "_run_worker", run), mock.patch.object(E, "_reader", reader):
            E.erase(str(clip), [], lambda f, m: None, threading.Event(), steps=2, grow=0, prefill=prefill,
                    refine=refine, **kw)
        rec = json.load(open(E.record_path(str(clip)), encoding="utf-8"))
        self.all_out = list(real_reader(str(clip), 320, 192, "rgb24"))
        out = self.all_out[0]
        left = [n for n in os.listdir(tmp) if n.startswith(".erase_")]
        return rec, out, hole, jobs, left

    def test_prefill_and_refine(self):
        rec, out, hole, jobs, left = self.run_erase("Auto", True)
        self.assertTrue(rec["prefill"] and rec["refine"], rec)
        self.assertEqual(len(jobs), 2, "a first pass and a full-size pass")
        inner = out[50:140, 110:210].reshape(-1, 3).astype(np.float32)
        self.assertGreater(float(np.abs(inner - (255, 0, 255)).sum(1).mean()), 150, "the object came back")
        self.assertEqual(left, [], "the work folder was left")

    def test_padding_past_the_end_keeps_the_mask(self):
        """24 frames run as 25: the 25th is the last frame again -- and the object in it is to be erased
        too, not kept (unmasked, ROSE faded the person back in over the clip's end, 2026-10-15)."""
        rec, out, hole, jobs, left = self.run_erase("Auto", False)
        self.assertEqual(len(self.first_masks), 25)
        self.assertTrue(self.first_masks[24].any(), "the padding frame had no mask")
        np.testing.assert_array_equal(self.first_masks[24], self.first_masks[23])
        inner = self.first_frames[24][30:65, 60:100].reshape(-1, 3).astype(np.float32)
        self.assertGreater(float(np.abs(inner - (255, 0, 255)).sum(1).mean()), 150, "the padding wasn't pre-filled")

    def test_off_redraws_as_before(self):
        rec, out, hole, jobs, left = self.run_erase("Off", False)
        self.assertFalse(rec["prefill"] or rec["refine"], rec)
        self.assertEqual(len(jobs), 1)
        inner = out[50:140, 110:210].reshape(-1, 3).astype(np.float32)
        self.assertLess(float(np.abs(inner - (255, 0, 255)).sum(1).mean()), 60,
                        "the stand-in hands back what it saw: without pre-fill, the object")

    def test_work_size_equal_to_the_clip_with_windows_cross_faded(self):
        """A clip already at ROSE's work size (a 1280x720 clip at 720p) over several windows: the
        cross-faded windows are divided by their weights. They weren't (review 2026-10-17): a frame
        near a window's end came out at a fraction of its brightness -- frame 0 at 1/16, nearly black."""
        rec, out, hole, jobs, left = self.run_erase("Off", False, seconds=2, work_area=320 * 192, window=33)
        self.assertEqual(rec["work_size"], [320, 192], "the clip's own size: the case under test")
        self.assertGreater(len(rec["windows"]), 1, "one window has no cross-fade")
        for i in (0, 20, 30, len(self.all_out) - 1):        # a window's start, the overlap, the end
            inner = self.all_out[i][50:140, 110:210].reshape(-1, 3).astype(np.float32)
            self.assertLess(float(np.abs(inner - (255, 0, 255)).sum(1).mean()), 60,
                            f"frame {i}: the stand-in's own picture, at its own brightness")


if __name__ == "__main__":
    unittest.main()
