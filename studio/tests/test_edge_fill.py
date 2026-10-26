"""Fill Edges (user, 2026-10-13; core/removal/edge_fill.py): the strip a deep warp leaves at a
frame's edge, measured from the splat mask and refilled by ROSE.

Made-up clips in a temp folder; ROSE itself is stood in for (its worker writes every masked
pixel one known colour), so this runs without the GPU: which files are found, how wide the
strip is, the grain, and the rewrite -- only the strip changes, the format is the file's own,
the eye with no strip is left alone, the originals are kept and a second scan says done."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
from core.removal import edge_fill as F  # noqa: E402
from core.removal import erase as E  # noqa: E402

W, H, N = 320, 144, 12          # one eye
STRIP = 18                      # the mask's holes reach this far in from the R eye's left edge
FILL = (250, 20, 200)           # what the stand-in ROSE paints


def _encode(path, frames, args):
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", "24", "-i", "-", *args, path], stdin=subprocess.PIPE)
    p.stdin.write(b"".join(np.ascontiguousarray(f).tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0, path


def _picture(i, w=W):
    """A gradient with a moving bar: plain enough to compare, not flat."""
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    img = np.concatenate([60 + 120 * x + 0 * y, 90 + 60 * y + 0 * x, 140 - 40 * x + 0 * y], axis=2)
    img[:, (40 + 4 * i) % w: (40 + 4 * i) % w + 12] += 40
    return np.clip(img, 0, 255).astype(np.uint8)


def _mask_panel(i):
    m = np.zeros((H, W), np.uint8)
    m[:, :STRIP] = 255                       # the strip
    m[: H // 3, STRIP:STRIP + 3] = 255        # ragged on a few rows: under the 95th percentile... mostly
    m[50:60, 150:170] = 255                   # a hole in the middle: not an edge
    return np.repeat(m[..., None], 3, axis=2)


def _fake_worker(job_path, n_chunks, steps, progress, stop, lo, hi, bars=False):
    """ROSE's stand-in: the frames back, the masked part painted FILL."""
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    for c in job["chunks"]:
        fr = np.load(c["frames"]).copy()
        m = np.load(c["masks"]) > 0
        fr[m] = FILL
        np.save(c["out"], fr)
    _fake_worker.calls.append(job)


class EdgeFillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "merged")
        self.masks = os.path.join(self.tmp, "splats")
        os.makedirs(self.out)
        os.makedirs(os.path.join(self.masks, "finished"))
        eyes = [_picture(i) for i in range(N)]
        right = [e.copy() for e in eyes]
        for r in right:
            r[:, :STRIP] = (20, 10, 40)           # the inpainter's dark bar
        _encode(os.path.join(self.out, f"shot_{W}_merged_full_sbs.mp4"),
                [np.hstack([l, r]) for l, r in zip(eyes, right)], ["-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p"])
        dnx = ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"]
        _encode(os.path.join(self.out, f"shot_{W}_merged_full_sbs_L.mov"), eyes, dnx)
        _encode(os.path.join(self.out, f"shot_{W}_merged_full_sbs_R.mov"), right, dnx)
        _encode(os.path.join(self.masks, "finished", f"shot_{W}_splatted2.mkv"),
                [np.hstack([_mask_panel(i), _picture(i)]) for i in range(N)], ["-c:v", "ffv1", "-pix_fmt", "bgr0"])
        _fake_worker.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_clip_and_its_mask(self):
        groups = F.find_groups(self.out)
        self.assertEqual([g.name for g in groups], [f"shot_{W}_merged_full_sbs"])
        self.assertEqual(sorted(groups[0].files), ["L", "R", "sbs"])
        mask = F.find_mask(groups[0], self.masks)
        self.assertTrue(mask and mask.endswith(f"shot_{W}_splatted2.mkv"), mask)

    def test_measures_the_strip_from_the_mask(self):
        m = F.measure(F.find_mask(F.find_groups(self.out)[0], self.masks))
        self.assertEqual(set(m["eyes"]), {"R"})
        self.assertIn(m["eyes"]["R"]["left"], (STRIP, STRIP + 3))   # the 95th percentile of the rows
        self.assertEqual(m["eyes"]["R"]["right"], 0)
        self.assertEqual(m["width"], W)

    def test_grain_brings_a_smooth_fill_to_the_picture(self):
        rng = np.random.default_rng(1)
        base = np.tile(np.linspace(80, 170, 96, dtype=np.float32)[None, :, None], (128, 1, 3))
        ref = [base + rng.normal(0, 3.0, base.shape).astype(np.float32) for _ in range(6)]
        smooth = [base + rng.normal(0, 0.8, base.shape).astype(np.float32) for _ in range(6)]
        plan = F.plan_grain(ref, smooth, rng)
        self.assertIsNotNone(plan)
        after = [plan.add(s, rng) for s in smooth]
        got = F.grain_stats(after)
        want = F.grain_stats(ref)
        g, w = (np.sqrt(np.trace(s["covs"].mean(0)) / 3) for s in (got, want))
        self.assertAlmostEqual(g / w, 1.0, delta=0.15)

    def test_fill_rewrites_only_the_strip(self):
        g = F.find_groups(self.out)[0]
        mask = F.find_mask(g, self.masks)
        before = {k: list(E._reader(p, *((2 * W, H) if k == "sbs" else (W, H)), "rgb24")) for k, p in g.files.items()}
        l_id = F._file_id(g.files["L"])
        with mock.patch.object(E, "_run_worker", _fake_worker), \
                mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            r = F.fill(g, mask, lambda *a: None, threading.Event(), steps=4, grain=False, settings={})
        self.assertEqual(r["edges"][0]["eye"], "R")
        self.assertEqual(r["edges"][0]["edge"], "left")
        strip = r["edges"][0]["strip"]
        self.assertGreaterEqual(strip, STRIP)
        self.assertEqual(_fake_worker.calls[0]["steps"], 4)
        self.assertEqual(len(_fake_worker.calls), 1, "one ROSE run per clip")

        self.assertEqual(F._file_id(g.files["L"]), l_id, "the left eye has no strip: its file is left alone")
        kept = os.path.join(self.out, F.ORIGINALS_DIR)
        self.assertEqual(sorted(os.listdir(kept)), sorted(os.path.basename(g.files[k]) for k in ("sbs", "R")))

        for k, x0 in (("R", 0), ("sbs", W)):
            p = g.files[k]
            self.assertEqual(F.done(g), True)
            fmt = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=codec_name,nb_frames", "-of", "csv=p=0", p], capture_output=True, text=True).stdout
            self.assertIn("dnxhd" if k == "R" else "h264", fmt)
            w = 2 * W if k == "sbs" else W
            after = list(E._reader(p, w, H, "rgb24"))
            self.assertEqual(len(after), N)
            for i in (0, N // 2, N - 1):
                a, b = after[i].astype(int), before[k][i].astype(int)
                fill = a[:, x0: x0 + strip - 2].reshape(-1, 3).mean(axis=0)
                self.assertLess(np.abs(fill - FILL).max(), 25, f"{k} frame {i}: the strip is ROSE's")
                far = np.abs(a[:, x0 + strip + F.FEATHER + 2:x0 + W] - b[:, x0 + strip + F.FEATHER + 2:x0 + W]).mean()
                self.assertLess(far, 3.0, f"{k} frame {i}: past the fade the picture is as it was")
                if k == "sbs":
                    self.assertLess(np.abs(a[:, :W] - b[:, :W]).mean(), 3.0, "the SBS's left eye is as it was")

        scan_done = F.done(F.find_groups(self.out)[0])
        self.assertTrue(scan_done, "a second scan says the clip is done")

    def test_the_fill_follows_each_rows_hole(self):
        """2026-10-13, like V1's Hi-Res Blend: the fill reaches as far as each row's hole did,
        frame by frame -- a row without one keeps its picture, a narrow one isn't filled to the
        widest. (The straight strip filled every row to the widest.)"""
        top = H // 2
        def panel(i):
            m = np.zeros((H, W), np.uint8)
            m[:top, :STRIP] = 255                 # the top half: the strip
            m[top:top + 20, :6] = 255             # a sliver below it: narrower
            return np.repeat(m[..., None], 3, axis=2)
        _encode(F.find_mask(F.find_groups(self.out)[0], self.masks),
                [np.hstack([panel(i), _picture(i)]) for i in range(N)], ["-c:v", "ffv1", "-pix_fmt", "bgr0"])
        g = F.find_groups(self.out)[0]
        before = list(E._reader(g.files["R"], W, H, "rgb24"))
        with mock.patch.object(E, "_run_worker", _fake_worker), \
                mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            r = F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), grain=False, settings={})
        strip = r["edges"][0]["strip"]
        after = list(E._reader(g.files["R"], W, H, "rgb24"))
        for i in (0, N - 1):
            a, b = after[i].astype(int), before[i].astype(int)
            fill_top = a[: top - 8, : STRIP - 2].reshape(-1, 3).mean(axis=0)
            self.assertLess(np.abs(fill_top - FILL).max(), 25, f"frame {i}: the strip's rows are filled")
            low = slice(top + 20 + 8, H)                      # past the rows' spread
            self.assertLess(np.abs(a[low, :strip] - b[low, :strip]).mean(), 3.0,
                            f"frame {i}: rows without a hole keep their picture")
            sliver = slice(top + 4, top + 16)
            past = 6 + 8 + 16 + 1                             # its hole + the pad + the fade
            self.assertLess(np.abs(a[sliver, past:past + 8] - b[sliver, past:past + 8]).mean(), 3.0,
                            f"frame {i}: a narrow hole's row is filled only as far as its hole")
            self.assertLess(np.abs(a[sliver, :4].reshape(-1, 3).mean(axis=0) - FILL).max(), 25,
                            f"frame {i}: ...but filled there")

    def test_size_sets_roses_height(self):
        """Merging's Size: ROSE works the crop at that height (or the frame's, if smaller)."""
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", _fake_worker), \
                mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), grain=False, settings={},
                   rose_height=72)
        rh, rw = _fake_worker.calls[0]["size"]
        self.assertEqual(rh, 80 if round(72 / 16) * 16 == 80 else 64)
        self.assertLess(rw, W)

    def test_rose_gets_the_strip_prefilled_smooth_not_stretched(self):
        """2026-10-26: ROSE sees under its mask (its pipeline doesn't blank it), and the last good
        column stretched across the strip came back as horizontal streaks. The strip goes to ROSE
        as erase.prefill_frames' wide blur from around it -- no dark bar, no copied column."""
        got = []

        def worker(job_path, *a, **k):
            with open(job_path, encoding="utf-8") as f:
                for c in json.load(f)["chunks"]:
                    got.append((np.load(c["frames"]), np.load(c["masks"]) > 0))
            _fake_worker(job_path, *a, **k)
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", worker),                 mock.patch.object(F.rose_env, "ready", lambda: True),                 mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), grain=False, settings={})
        self.assertTrue(got)
        for frames, masks in got:
            mw = int(masks[0, 0].sum())
            self.assertGreater(mw, STRIP)
            strip = frames[:, :, :mw].astype(np.float32)
            stretched = np.repeat(frames[:, :, mw:mw + 1], mw, axis=2).astype(np.float32)
            self.assertGreater(np.abs(strip - stretched).mean(), 2.0, "not the last good column stretched")
            self.assertGreater(strip.std(axis=2).mean(), 1.0, "it varies across the strip, like the picture")
            self.assertGreater(np.abs(strip.mean(axis=(0, 1, 2)) - (20, 10, 40)).max(), 30, "the dark bar is gone")
            np.testing.assert_array_equal(frames, E.prefill_frames(frames, masks), "erase's pre-fill, as it is")

    def test_prefill_stretched_is_the_last_good_column(self):
        """The Pre-fill dropdown's other choice: the last good column across the strip, as before."""
        got = []

        def worker(job_path, *a, **k):
            with open(job_path, encoding="utf-8") as f:
                for c in json.load(f)["chunks"]:
                    got.append((np.load(c["frames"]), np.load(c["masks"]) > 0))
            _fake_worker(job_path, *a, **k)
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", worker), \
                mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), grain=False, settings={},
                   prefill="Stretched")
        self.assertTrue(got)
        for frames, masks in got:
            mw = int(masks[0, 0].sum())
            np.testing.assert_array_equal(frames[:, :, :mw], np.repeat(frames[:, :, mw:mw + 1], mw, axis=2))

    def test_prefill_setting_is_one_of_its_choices(self):
        from studio.stages.edgefill import settings as ES
        self.assertEqual(ES.DEFAULTS["prefill"], "Blur")
        self.assertEqual(ES.coerce("prefill", "Stretched"), "Stretched")
        self.assertEqual(ES.coerce("prefill", "nonsense"), "Blur")
        self.assertEqual(ES.normalise({})["prefill"], "Blur")

    def test_no_strip_no_rose(self):
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True):
            r = F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(),
                       strips={"eyes": {"R": {"left": 1, "right": 0}}, "width": W})
        self.assertIn("skipped", r)
        self.assertEqual(_fake_worker.calls, [])

    def test_a_stop_leaves_the_files(self):
        g = F.find_groups(self.out)[0]
        ids = {k: F._file_id(p) for k, p in g.files.items()}
        stop = threading.Event()

        def stopping(*a, **k):
            stop.set()
            raise E.Stopped()
        with mock.patch.object(E, "_run_worker", stopping), mock.patch.object(F.rose_env, "ready", lambda: True):
            with self.assertRaises(E.Stopped):
                F.fill(g, F.find_mask(g, self.masks), lambda *a: None, stop)
        self.assertEqual({k: F._file_id(p) for k, p in g.files.items()}, ids)
        self.assertFalse([n for n in os.listdir(self.out) if n.startswith(".edgefill_")], "its temp folder is gone")


class SweepFixesTest(EdgeFillTest.__base__):
    """Bug sweep 2026-10-14: each test fails on the code before its fix (c6a6c6e)."""

    setUp = EdgeFillTest.setUp
    tearDown = EdgeFillTest.tearDown

    def _patches(self):
        return (mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True),
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False))

    def test_a_clip_takes_only_its_own_splat(self):
        """EF1-01: clip "shot" took the newer splat of clip "shot_b" (a glob on shot_*)."""
        other = os.path.join(self.masks, f"shot_b_{W}_splatted2D.mkv")
        shutil.copy(os.path.join(self.masks, "finished", f"shot_{W}_splatted2.mkv"), other)
        os.utime(other, (time.time() + 60, time.time() + 60))           # the newer one
        mask = F.find_mask(F.find_groups(self.out)[0], self.masks)
        self.assertTrue(mask.endswith(f"shot_{W}_splatted2.mkv"), mask)

    def test_a_stop_while_writing_leaves_every_file_as_it_was(self):
        """EF1-02 / EF2-01: a Stop on the clip's second file left the first one already filled."""
        g = F.find_groups(self.out)[0]
        ids = {k: F._file_id(p) for k, p in g.files.items()}
        stop, seen = threading.Event(), []
        real_write = F._write

        def write(path, *args, **kw):
            seen.append(os.path.basename(path))
            if len(seen) == 2:
                stop.set()                      # Stop as the second file starts
            return real_write(path, *args, **kw)
        a, b, c = self._patches()
        with a, b, c, mock.patch.object(F, "_write", write), self.assertRaises(E.Stopped):
            F.fill(g, F.find_mask(g, self.masks), lambda *x: None, stop, steps=4, grain=False, settings={})
        self.assertEqual(len(seen), 2, seen)
        self.assertEqual({k: F._file_id(p) for k, p in g.files.items()}, ids, "every file as it was")
        self.assertFalse([n for n in os.listdir(self.out) if n.endswith(".part") or n.startswith(F.TEMP_PREFIX)],
                         os.listdir(self.out))
        self.assertFalse(os.path.isdir(os.path.join(self.out, F.ORIGINALS_DIR)), "no original moved")

    def test_a_work_folder_left_by_a_quit_is_swept(self):
        """OLD1-01: a quit mid-fill left its .edgefill_ folder; nothing removed it. Sweep (b): only
        once it's old -- a live fill's folder (a look starting as a fill does, a second Studio) went
        too; and a half-written clip a crash left goes the same way."""
        old = time.time() - F.SWEEP_AGE_S - 60
        left = os.path.join(self.out, ".edgefill_abc123")
        os.makedirs(left)
        open(os.path.join(left, "e0w0_frames.npy"), "wb").write(b"x" * 100)
        os.utime(os.path.join(left, "e0w0_frames.npy"), (old, old)); os.utime(left, (old, old))
        live = os.path.join(self.out, ".edgefill_live99")
        os.makedirs(live)
        open(os.path.join(live, "job.json"), "w").write("{}")
        part = os.path.join(self.out, f"shot_{W}_merged_full_sbs_R.mov.part")
        open(part, "wb").write(b"x" * 100)
        os.utime(part, (old, old))
        g = F.find_groups(self.out)[0]
        a, b, c = self._patches()
        with a, b, c:
            F.fill(g, F.find_mask(g, self.masks), lambda *x: None, threading.Event(), steps=4, grain=False, settings={})
        self.assertFalse(os.path.exists(left), "an old work folder goes")
        self.assertTrue(os.path.exists(live), "a fresh one -- maybe a live fill's -- stays")
        self.assertFalse(os.path.exists(part), "an old half-written clip goes")

    def test_a_record_cut_short_keeps_the_old_one(self):
        """EF1-05: the record was written in place; a crash mid-write left it empty, and every clip
        in the folder then looked unfilled."""
        rec = os.path.join(self.out, F.RECORD)
        with open(rec, "w", encoding="utf-8") as f:
            json.dump({"other.mov": {"file": [1, 2]}}, f)
        real_dump = json.dump

        def dump(obj, f, *a, **kw):
            if isinstance(obj, dict) and "other.mov" in obj:          # the record, not the strips cache
                f.write("{")
                raise OSError("disk full")
            return real_dump(obj, f, *a, **kw)
        g = F.find_groups(self.out)[0]
        a, b, c = self._patches()
        with a, b, c, mock.patch.object(F.json, "dump", dump), self.assertRaises(OSError):
            F.fill(g, F.find_mask(g, self.masks), lambda *x: None, threading.Event(), steps=4, grain=False, settings={})
        with open(rec, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"other.mov": {"file": [1, 2]}})

    def test_a_record_that_isnt_a_table_reads_as_nothing_done(self):
        """EF1-05: a record holding a list raised out of done()."""
        with open(os.path.join(self.out, F.RECORD), "w", encoding="utf-8") as f:
            json.dump(["x"], f)
        self.assertFalse(F.done(F.find_groups(self.out)[0]))

    def test_a_damaged_remembered_strip_is_measured_again(self):
        """EF2-09: an entry without its fields stopped the grid's look with a KeyError."""
        g = F.find_groups(self.out)[0]
        mask = F.find_mask(g, self.masks)
        F.remember(g, mask, {"px": 18})               # no eye / edge / frame / eyeWidth
        self.assertIsNone(F.remembered(g, mask))
        whole = {"eye": "R", "edge": "left", "px": 18, "frame": 3, "eyeWidth": W}
        F.remember(g, mask, whole)
        self.assertEqual(F.remembered(g, mask), whole)
        self.assertFalse([n for n in os.listdir(self.out) if n.endswith(".part")], "no temp file left")

    def test_the_blend_frame_by_frame_is_the_whole_clips(self):
        """EF1-07: the blend is now made a frame at a time (row_reach + alpha_of): the same numbers."""
        rng = np.random.default_rng(3)
        runs = rng.integers(0, 30, (7, 40)).astype(np.uint16)
        whole = F.row_alpha(runs, 1.5, 80, 9, 40, 56)
        reach = F.row_reach(runs, 1.5, 80, 9, 40)
        np.testing.assert_array_equal(np.stack([F.alpha_of(r, 56) for r in reach]), whole)


class SweepBFixesTest(EdgeFillTest.__base__):
    """Bug sweep 2026-10-14b: each fails on f03e4d7."""

    setUp = EdgeFillTest.setUp
    tearDown = EdgeFillTest.tearDown

    def _fill(self, g, **kw):
        with mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            return F.fill(g, F.find_mask(g, self.masks), kw.pop("progress", lambda *a: None), kw.pop("stop", threading.Event()),
                          steps=4, grain=False, settings={}, **kw)

    def test_a_clip_file_open_elsewhere_changes_nothing(self):
        """N2-01: the _R open in a player -- its move failed after the SBS was already swapped."""
        g = F.find_groups(self.out)[0]
        ids = {k: F._file_id(p) for k, p in g.files.items()}
        held = open(g.files["R"], "rb")          # Windows: no move while it's open
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._fill(g)
        finally:
            held.close()
        self.assertIn("open in another program", str(ctx.exception))
        self.assertEqual({k: F._file_id(p) for k, p in g.files.items()}, ids, "every file as it was")
        self.assertFalse([n for n in os.listdir(self.out) if n.endswith(".part") or n.endswith(".edgefill-check")])

    def test_a_second_fill_keeps_the_true_original(self):
        """N2-03: with no Recycle Bin, filling a clip again overwrote its original in
        edge_fill_originals/ with the once-filled one. (2026-10-26: a refill starts from that
        original, so it is the only one kept.)"""
        g = F.find_groups(self.out)[0]
        first = open(g.files["R"], "rb").read()
        self._fill(g)
        self._fill(F.find_groups(self.out)[0])
        kept = sorted(os.listdir(os.path.join(self.out, F.ORIGINALS_DIR)))
        self.assertEqual(kept, sorted(os.path.basename(g.files[k]) for k in ("sbs", "R")), kept)
        with open(os.path.join(self.out, F.ORIGINALS_DIR, f"shot_{W}_merged_full_sbs_R.mov"), "rb") as f:
            self.assertEqual(f.read(), first, "the untouched original is still there")

    def test_a_refill_starts_from_the_unfilled_original(self):
        """2026-10-26: Stretched, then Blur on the same clip -- the second fill read the first one's
        output, and its fade blended into the old fill: a line where that fill ended. A refill comes
        out as one fill of the untouched original would."""
        pristine = os.path.join(self.tmp, "pristine")
        shutil.copytree(self.out, pristine)
        self._fill(F.find_groups(self.out)[0])
        other = (20, 230, 60)
        with mock.patch.dict(globals(), FILL=other):
            self._fill(F.find_groups(self.out)[0])
            self._fill(F.find_groups(pristine)[0])
        again = list(E._reader(F.find_groups(self.out)[0].files["R"], W, H, "rgb24"))
        once = list(E._reader(F.find_groups(pristine)[0].files["R"], W, H, "rgb24"))
        for i in (0, N // 2, N - 1):
            diff = np.abs(again[i].astype(int) - once[i].astype(int))[:, :STRIP + 8 + F.FEATHER + 4]
            self.assertLess(diff.mean(), 2.0, f"frame {i}: the refill is the original's, not the first fill's")

    def test_the_original_is_kept_beside_the_clip_not_recycled(self):
        """A refill needs it: the Recycle Bin can't be read back. The once-filled file a refill
        replaces is what goes to the Recycle Bin."""
        recycled = []

        def recycle(p):
            trash = os.path.join(self.tmp, "trash")
            os.makedirs(trash, exist_ok=True)
            recycled.append(os.path.basename(p))
            shutil.move(p, os.path.join(trash, f"{len(recycled)}_{os.path.basename(p)}"))
            return True
        g = F.find_groups(self.out)[0]
        first = open(g.files["R"], "rb").read()
        with mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", recycle):
            F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), steps=4, grain=False, settings={})
            self.assertEqual(recycled, [], "the first fill's originals are kept, not recycled")
            with open(os.path.join(self.out, F.ORIGINALS_DIR, f"shot_{W}_merged_full_sbs_R.mov"), "rb") as f:
                self.assertEqual(f.read(), first)
            F.fill(F.find_groups(self.out)[0], F.find_mask(g, self.masks), lambda *a: None, threading.Event(), steps=4,
                   grain=False, settings={})
        self.assertEqual(sorted(recycled), sorted(os.path.basename(g.files[k]) for k in ("sbs", "R")),
                         "a refill recycles the once-filled files")

    def test_a_clip_merged_again_is_its_own_original(self):
        """Merging wrote the clip again after a fill: that new file is the original now, and the
        old kept one goes."""
        g = F.find_groups(self.out)[0]
        self._fill(g)
        new = [np.full((H, W, 3), (90, 120, 150), np.uint8) for _ in range(N)]
        for f in new:
            f[:, :STRIP] = (20, 10, 40)
        os.remove(g.files["R"])
        _encode(g.files["R"], new, ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"])
        merged_again = open(g.files["R"], "rb").read()
        self._fill(F.find_groups(self.out)[0])
        with open(os.path.join(self.out, F.ORIGINALS_DIR, f"shot_{W}_merged_full_sbs_R.mov"), "rb") as f:
            self.assertEqual(f.read(), merged_again, "the newly merged file is the original kept")
        after = list(E._reader(g.files["R"], W, H, "rgb24"))
        far = after[0][:, STRIP + F.FEATHER + 8:].reshape(-1, 3).astype(int).mean(axis=0)
        self.assertLess(np.abs(far - (90, 120, 150)).max(), 12, "filled from the new merge, not the old original")

    def test_a_record_save_waits_out_a_reader(self):
        """N2-05: a look reading the record a moment made the replace fail (Windows) after the files
        were swapped -- the fill reported a failure."""
        real = os.replace
        calls = []

        def busy_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError(13, "in use")
            return real(src, dst)
        target = os.path.join(self.out, "x.json")
        with mock.patch.object(F.os, "replace", busy_once):
            F._save_json(target, {"a": 1})
        with open(target, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_an_unreadable_strips_cache_isnt_rewritten_with_one_entry(self):
        """N2-06: a failed read gave {} and remember() wrote back only its own clip."""
        g = F.find_groups(self.out)[0]
        mask = F.find_mask(g, self.masks)
        whole = {"eye": "R", "edge": "left", "px": 18, "frame": 3, "eyeWidth": W}
        other = F.Group(self.out, "other", str(W), "full_sbs", {})
        F.remember(other, mask, whole)
        real_open = open

        def flaky(path, *a, **kw):
            if str(path).endswith(F.STRIPS) and "r" in (a[0] if a else kw.get("mode", "r")):
                raise PermissionError(13, "in use")
            return real_open(path, *a, **kw)
        with mock.patch("builtins.open", flaky):
            F.remember(g, mask, whole)
        self.assertEqual(F.remembered(other, mask), whole, "the other clip's strip is still there")

    def test_bool_isnt_a_width(self):
        g = F.find_groups(self.out)[0]
        mask = F.find_mask(g, self.masks)
        F.remember(g, mask, {"eye": "R", "edge": "left", "px": True, "frame": 3, "eyeWidth": W})
        self.assertIsNone(F.remembered(g, mask))

    def test_the_colour_pass_heeds_stop_and_says_so(self):
        """N1-06: Native's colour pass ran every frame with no Stop check and no progress."""
        g = F.find_groups(self.out)[0]
        stop, said = threading.Event(), []

        def progress(frac, text):
            said.append(text)
            if text == "Matching colour":
                stop.set()
        orig = F.tile_rows
        with mock.patch.object(F, "tile_rows", lambda h: orig(h, 96, 32)), self.assertRaises(E.Stopped):
            self._fill(g, progress=progress, stop=stop, rose_height=0)
        self.assertIn("Matching colour", said)


class NativeTilesTest(EdgeFillTest.__base__):
    """Native (user, 2026-10-14): ROSE at the clip's own size in overlapping tiles, faded into each
    other, the colour matched to the picture beside the strip."""

    setUp = EdgeFillTest.setUp
    tearDown = EdgeFillTest.tearDown

    def test_tiles_cover_the_frame_evenly_with_their_overlap(self):
        tiles = F.tile_rows(1038)
        self.assertEqual(tiles, [(0, 560), (478, 1038)])
        self.assertEqual(F.tile_rows(400), [(0, 400)], "a frame that fits: one tile")
        many = F.tile_rows(2160)
        self.assertEqual(many[0][0], 0)
        self.assertEqual(many[-1][1], 2160)
        self.assertTrue(all(b - a == F.TILE_HEIGHT for a, b in many))
        self.assertTrue(all(many[k][1] - many[k + 1][0] >= F.TILE_OVERLAP for k in range(len(many) - 1)))

    def test_the_fade_weights_add_up_and_ramp_across_each_overlap(self):
        tiles = F.tile_rows(1038)
        w = F.tile_weights(tiles, 1038)
        np.testing.assert_allclose(w.sum(axis=0), 1.0, atol=1e-6)
        self.assertEqual(w[0, 100], 1.0)
        self.assertEqual(w[1, 1000], 1.0)
        mid = w[1, 478:560]
        self.assertTrue((np.diff(mid) > 0).all(), "the lower tile fades in across the overlap")

    def test_the_colour_offset_is_the_drift_not_the_detail(self):
        rng = np.random.default_rng(2)
        real = rng.uniform(40, 200, (5, 60, 12, 3)).astype(np.float32)
        filled = real - np.array([8.0, 6.0, 5.0], np.float32) + rng.normal(0, 3, real.shape).astype(np.float32)
        off = F.colour_offsets(real, filled)
        self.assertEqual(off.shape, (5, 60, 3))
        np.testing.assert_allclose(off.mean(axis=(0, 1)), [8.0, 6.0, 5.0], atol=0.6)
        self.assertLess(off.std(axis=1).max(), 1.0, "smooth down the frame: a drift, not detail")

    def test_native_is_the_size_choice_0(self):
        from studio.stages.edgefill import settings as ES
        self.assertEqual(ES.height("Native (tiled, sharpest)"), 0)
        self.assertEqual(ES.height("784 (straighter lines)"), 784)
        self.assertIn("Native (tiled, sharpest)", ES.SIZES)

    def test_a_native_fill_runs_rose_tile_by_tile_at_the_clips_size(self):
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False), \
                mock.patch.object(F, "TILE_HEIGHT", 96), mock.patch.object(F, "TILE_OVERLAP", 32):
            orig = F.tile_rows
            with mock.patch.object(F, "tile_rows", lambda h: orig(h, 96, 32)):
                r = F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), steps=4, grain=False,
                           settings={}, rose_height=0)
        job = _fake_worker.calls[0]
        self.assertEqual(job["size"][0], 96, "each chunk a tile's rows, at the clip's own size")
        self.assertEqual(len(job["chunks"]), 2, "one window, two tiles")
        self.assertTrue(r["files"], r)
        after = list(E._reader(g.files["R"], W, H, "rgb24"))
        for a_, b_ in ((0, 48), (96, H)):           # rows only the top tile covers, and only the bottom
            self.assertTrue(all(np.abs(f[a_:b_, 2:STRIP].astype(np.float32).mean(axis=(0, 1)) - (20, 10, 40)).max() > 30
                                for f in after), f"the dark bar is gone in rows {a_}-{b_}")


class WorstTest(unittest.TestCase):
    """Fill Edges' grid (user, 2026-10-14): the frame a clip's strip is widest at, for its still."""

    def test_the_widest_frame_and_edge(self):
        rows = np.zeros((5, 8), np.uint16)
        rows[3] = 20                                   # frame 3: the strip at its widest
        rows[1] = 6
        none = np.zeros((5, 8), np.uint16)
        m = {"eyes": {"R": {"left": 20, "right": 0}}, "width": 320,
             "rows": {"R": {"left": rows, "right": none}}}
        self.assertEqual(F.worst(m), {"eye": "R", "edge": "left", "px": 20, "frame": 3})

    def test_a_wider_strip_on_the_other_eye_wins(self):
        z = np.zeros((4, 8), np.uint16)
        lr = z.copy(); lr[2] = 30
        rl = z.copy(); rl[0] = 12
        m = {"eyes": {"L": {"left": 0, "right": 30}, "R": {"left": 12, "right": 0}}, "width": 320,
             "rows": {"L": {"left": z, "right": lr}, "R": {"left": rl, "right": z}}}
        self.assertEqual(F.worst(m), {"eye": "L", "edge": "right", "px": 30, "frame": 2})

    def test_no_strip(self):
        z = np.zeros((4, 8), np.uint16)
        m = {"eyes": {"R": {"left": 0, "right": 0}}, "width": 320, "rows": {"R": {"left": z, "right": z}}}
        self.assertEqual(F.worst(m)["px"], 0)

    def test_a_strip_is_remembered_while_its_mask_is_the_same(self):
        """user, 2026-10-14: the grid measured every clip again on each look."""
        d = tempfile.mkdtemp()
        try:
            mask = os.path.join(d, "shot_320_splatted2.mkv")
            with open(mask, "wb") as f:
                f.write(b"x" * 10)
            g = F.Group(d, "shot", "320", "full_sbs", {})
            self.assertIsNone(F.remembered(g, mask), "nothing measured yet")
            strip = {"eye": "R", "edge": "left", "px": 18, "frame": 3, "eyeWidth": 320}
            F.remember(g, mask, strip)
            self.assertEqual(F.remembered(g, mask), strip)
            with open(mask, "ab") as f:                      # a new splat: measured again
                f.write(b"y")
            self.assertIsNone(F.remembered(g, mask))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class DetailTest(unittest.TestCase):
    """Match Detail (user, 2026-10-14): how strongly a soft fill is sharpened to the picture's detail."""

    @staticmethod
    def picture(seed):
        """A texture with some size to it (noise softened a little), as a filmed picture has."""
        import cv2
        rng = np.random.default_rng(seed)
        return cv2.GaussianBlur(np.clip(128 + rng.normal(0, 30, (60, 40, 3)), 0, 255).astype(np.float32), (0, 0), 1.0)

    def test_a_soft_fill_is_sharpened_to_the_picture(self):
        import cv2
        real = [self.picture(i) for i in range(4)]
        soft = [cv2.GaussianBlur(r, (0, 0), 0.7) for r in real]       # ROSE's fill: a touch softer
        k = F.detail_gain(real, soft)
        self.assertGreater(k, 0.0)
        self.assertLessEqual(k, F.DETAIL_MOST)
        before = abs(np.mean([F._detail(f) for f in soft]) - np.mean([F._detail(r) for r in real]))
        after = abs(np.mean([F._detail(F.sharpen(f, k)) for f in soft]) - np.mean([F._detail(r) for r in real]))
        self.assertLess(after, before / 2, "sharpened, the fill's detail is much nearer the picture's")

    def test_a_fill_as_sharp_as_the_picture_is_left(self):
        real = [self.picture(i) for i in range(4)]
        self.assertEqual(F.detail_gain(real, [r.copy() for r in real]), 0.0)
        self.assertIs(F.sharpen(real[0], 0.0), real[0])

    def test_the_band_is_cut_after_sharpening(self):
        import cv2
        real = [self.picture(i)[:, 10:30] for i in range(4)]
        soft = [cv2.GaussianBlur(self.picture(i), (0, 0), 0.7) for i in range(4)]
        self.assertGreater(F.detail_gain(real, soft, slice(10, 30)), 0.0)


class QuadSplatTest(EdgeFillTest.__base__):
    """Splatting's default output is the _splatted4 grid ([left | depth] over [mask | right eye]), not
    Dual Output Only's _splatted2: a fresh project's every clip said "no splat mask in the Mask folder"
    (user, 2026-10-18). Its bottom row is the _splatted2 frame."""
    setUp = EdgeFillTest.setUp
    tearDown = EdgeFillTest.tearDown

    def _quad(self, folder):
        depth = np.repeat(np.linspace(0, 255, W, dtype=np.uint8)[None, :, None].repeat(H, 0), 3, axis=2)
        _encode(os.path.join(folder, f"shot_{W}_splatted4.mkv"),
                [np.vstack([np.hstack([_picture(i), depth]), np.hstack([_mask_panel(i), _picture(i)])])
                 for i in range(N)], ["-c:v", "ffv1", "-pix_fmt", "bgr0"])

    def test_a_quad_splat_is_found_and_measured_as_the_dual_one(self):
        group = F.find_groups(self.out)[0]
        dual = F.measure(F.find_mask(group, self.masks))
        os.remove(os.path.join(self.masks, "finished", f"shot_{W}_splatted2.mkv"))
        self._quad(self.masks)
        mask = F.find_mask(group, self.masks)
        self.assertTrue(mask and mask.endswith(f"shot_{W}_splatted4.mkv"), mask)
        quad = F.measure(mask)
        self.assertEqual(quad["eyes"], dual["eyes"])
        self.assertEqual(quad["width"], W)
        self.assertTrue(np.array_equal(quad["rows"]["R"]["left"], dual["rows"]["R"]["left"]))

    def test_the_dual_splat_wins_when_both_are_there(self):
        self._quad(self.masks)
        os.utime(os.path.join(self.masks, f"shot_{W}_splatted4.mkv"), (time.time() + 60,) * 2)   # newer, still second
        mask = F.find_mask(F.find_groups(self.out)[0], self.masks)
        self.assertTrue(mask.endswith(f"shot_{W}_splatted2.mkv"), mask)


class UsualEdgeTest(unittest.TestCase):
    """Splatting leaves the strip on the right eye's left edge; the grid picked a clip's right edge
    when that was wider (user, 2026-10-18). Only the usual edge counts unless Right Edge Too is on."""

    def meas(self):
        z = np.zeros((4, 8), np.uint16)
        left = z.copy(); left[1] = 10
        right = z.copy(); right[2] = 38
        return {"eyes": {"R": {"left": 10, "right": 38}}, "width": 320, "rows": {"R": {"left": left, "right": right}}}

    def test_the_right_eyes_left_edge_even_when_the_right_is_wider(self):
        self.assertEqual(F.worst(self.meas()), {"eye": "R", "edge": "left", "px": 10, "frame": 1})

    def test_right_edge_too_lets_the_wider_right_edge_win(self):
        self.assertEqual(F.worst(self.meas(), both=True), {"eye": "R", "edge": "right", "px": 38, "frame": 2})

    def test_a_strip_measured_on_other_edges_is_measured_again(self):
        d = tempfile.mkdtemp()
        try:
            mask = os.path.join(d, "shot_320_splatted2.mkv")
            with open(mask, "wb") as f:
                f.write(b"x" * 10)
            g = F.Group(d, "shot", "320", "full_sbs", {})
            strip = {"eye": "R", "edge": "left", "px": 10, "frame": 1, "eyeWidth": 320}
            F.remember(g, mask, strip)
            self.assertEqual(F.remembered(g, mask), strip)
            self.assertIsNone(F.remembered(g, mask, both=True), "Right Edge Too on: looked at again")
            rec = json.load(open(os.path.join(d, F.STRIPS), encoding="utf-8"))
            del rec[g.name]["both"]                              # kept before the edges were asked for
            with open(os.path.join(d, F.STRIPS), "w", encoding="utf-8") as f:
                json.dump(rec, f)
            self.assertIsNone(F.remembered(g, mask), "an older strip, maybe of the far edge: measured again")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class UsualEdgeFillTest(EdgeFillTest.__base__):
    """...and the fill: a strip only on the right eye's right edge is left alone unless asked for."""
    setUp = EdgeFillTest.setUp
    tearDown = EdgeFillTest.tearDown

    def strips(self):
        return {"eyes": {"R": {"left": 0, "right": STRIP}}, "width": W,
                "rows": {"R": {"left": np.zeros((N, H), np.uint16), "right": np.full((N, H), STRIP, np.uint16)}}}

    def run_fill(self, both):
        g = F.find_groups(self.out)[0]
        with mock.patch.object(E, "_run_worker", _fake_worker), mock.patch.object(F.rose_env, "ready", lambda: True), \
                mock.patch("core.common.recycle_bin.send_to_recycle_bin", lambda p: False):
            return F.fill(g, F.find_mask(g, self.masks), lambda *a: None, threading.Event(), steps=4, grain=False,
                          settings={}, strips=self.strips(), both_edges=both)

    def test_a_right_edge_strip_is_skipped_by_default(self):
        r = self.run_fill(False)
        self.assertIn("skipped", r)
        self.assertEqual(_fake_worker.calls, [])

    def test_right_edge_too_fills_it(self):
        r = self.run_fill(True)
        self.assertEqual([(e["eye"], e["edge"]) for e in r["edges"]], [("R", "right")])


if __name__ == "__main__":
    unittest.main()
