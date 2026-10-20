"""Detect Text's selections (user, 2026-10-02): several frame ranges, each with
its own settings, rendered into one mask video -- the union of the selections
covering each frame, black in the gaps -- and kept in the sidecar
(submask_segments, the first one in the old single keys too). An old sidecar
with just the single keys opens as one selection over the whole clip.
Freeze: a frozen selection uses its freeze frame's mask, at full strength,
throughout (fades were removed, 2026-10-02). A small clip made in a temp folder;
never the user's footage; CPU.

    python -m unittest studio.tests.test_submask_segments
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.splatting import submask as SM  # noqa: E402

N = 14


class SidecarTests(unittest.TestCase):
    def test_round_trip(self):
        segs = [dict(SM.DEFAULTS, **{"from": 2, "to": 5, "core": 180, "rect": (1, 2, 30, 40)}),
                dict(SM.DEFAULTS, **{"from": 9, "to": 12, "edge": 120, "color": "Yellow", "invert": True})]
        data = SM.segments_to_sidecar(segs)
        self.assertEqual(data["submask_core"], 180)                 # the first, in the old keys
        self.assertEqual(data["submask_rect"], [1, 2, 30, 40])
        back = SM.segments_from_sidecar(data, N)
        self.assertEqual([(s["from"], s["to"]) for s in back], [(2, 5), (9, 12)])
        self.assertEqual(back[0]["rect"], (1, 2, 30, 40))
        self.assertEqual((back[1]["edge"], back[1]["color"], back[1]["invert"]), (120, "Yellow", True))

    def test_an_old_sidecar_is_one_selection_over_the_clip(self):
        old = {"submask_core": 150, "submask_rect": [0, 0, 10, 10]}
        segs = SM.segments_from_sidecar(old, N)
        self.assertEqual(len(segs), 1)
        self.assertEqual((segs[0]["from"], segs[0]["to"], segs[0]["core"]), (0, N - 1, 150))

    def test_ranges_are_clamped_to_the_clip(self):
        segs = SM.segments_from_sidecar({"submask_segments": [{"from": -4, "to": 99}]}, N)
        self.assertEqual((segs[0]["from"], segs[0]["to"]), (0, N - 1))


class EditorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:size=64x48:rate=12", "-frames:v", str(N), "-pix_fmt", "yuv420p", str(self.clip)],
                       check=True)
        self.ed = SM.SubtitleMask(eng=None, job_running=lambda: True)     # the CPU path
        self.addCleanup(self.ed.close)
        self.ed.cap, self.ed.video_path = cv2.VideoCapture(str(self.clip)), str(self.clip)
        self.ed.n_frames, self.ed.vw, self.ed.vh, self.ed.fps, self.ed.open = N, 64, 48, 12.0, True
        self.ed.segments = [dict(SM.DEFAULTS, **{"from": 0, "to": N - 1})]
        self.ed.seek(3)

    def test_add_select_remove(self):
        ed = self.ed
        ed.set({"core": 90}, False)
        ed.seek(6)
        ed.add_segment()
        self.assertEqual((len(ed.segments), ed.current), (2, 1))
        self.assertEqual((ed.settings["from"], ed.settings["to"], ed.settings["core"]), (6, N - 1, 90))
        ed.set({"core": 40}, False)                       # edits the new one only
        self.assertEqual((ed.segments[0]["core"], ed.segments[1]["core"]), (90, 40))
        self.assertIsNone(ed.set_range("8", "11"))
        self.assertEqual(ed.set_range("x", "3"), "Bad From/To frame numbers.")
        self.assertEqual(ed.set_range("9", "2"), "To must be >= From.")
        ed.select_segment(0)
        self.assertEqual(ed.current, 0)
        ed.select_segment(1)                              # the frame showing (6) is outside 8..11: it jumps
        self.assertEqual(ed.frame_idx, 8)
        ed.remove_segment()
        self.assertEqual((len(ed.segments), ed.current), (1, 0))
        st = ed.state()
        self.assertEqual((st["segments"], st["current"]),
                         ([{"from": 0, "to": N - 1, "hold": -1}], 0))
        ed.remove_segment()                               # the last one too (user, 2026-10-02)
        self.assertEqual(len(ed.segments), 0)

    def test_the_last_selection_can_go_and_save_removes_the_mask(self):
        ed = self.ed
        mask_path = Path(SM.submask_output_path(str(self.clip)))
        mask_path.write_bytes(b"an old mask")
        ed.remove_segment()
        self.assertEqual(ed.segments, [])
        st = ed.state()
        self.assertEqual((st["segments"], st["current"]), ([], -1))
        self.assertIsNone(ed.mask)
        ed.set({"core": 50}, False)                       # nothing to edit: harmless
        ed.mark_in(); ed.freeze_here()
        self.assertEqual(ed.segments, [])
        result = {}
        self.assertIsNone(ed.save(None, None, lambda *a: None, lambda *a: result.update(args=a)))
        self.assertEqual(result["args"][1], [])           # done at once, no render
        self.assertFalse(mask_path.exists())
        # its sidecar: off, no selections; reopened, none
        self.assertEqual(SM.segments_from_sidecar({"submask_enabled": False, "submask_segments": [],
                                                   "submask_core": 150}, N), [])
        ed.seek(6); ed.add_segment()                      # and back to one
        self.assertEqual((len(ed.segments), ed.settings["from"]), (1, 6))

    def test_reset_all_starts_over(self):
        ed = self.ed
        ed.set({"core": 60}, False)
        ed.set_rect(1, 1, 40, 30)
        ed.seek(3); ed.freeze_here()
        ed.seek(7); ed.add_segment()
        mask_path = Path(SM.submask_output_path(str(self.clip)))
        mask_path.write_bytes(b"an old mask")
        epoch = ed.state()["epoch"]
        msg = ed.reset_all()
        self.assertEqual(len(ed.segments), 1)
        seg = ed.segments[0]
        self.assertEqual((seg["from"], seg["to"]), (0, N - 1))
        self.assertEqual((seg["core"], seg["rect"], seg["hold"]), (SM.DEFAULTS["core"], None, None))
        self.assertFalse(mask_path.exists())
        self.assertIn("mask removed", msg)
        self.assertEqual(ed.state()["epoch"], epoch + 1)            # the window re-reads its controls

    def test_mark_in_and_out_at_the_frame_showing(self):
        ed = self.ed
        ed.seek(4); ed.mark_in()
        ed.seek(9); ed.mark_out()
        self.assertEqual((ed.settings["from"], ed.settings["to"]), (4, 9))
        ed.seek(11); ed.mark_in()                         # past To: To moves along
        self.assertEqual((ed.settings["from"], ed.settings["to"]), (11, 11))
        ed.seek(2); ed.mark_out()                         # before From: From moves along
        self.assertEqual((ed.settings["from"], ed.settings["to"]), (2, 2))

    def test_render_is_the_union_with_black_gaps(self):
        ed = self.ed
        # on a black clip: invert makes a selection's frames white, so the
        # video shows exactly which frames each selection covered
        ed.segments = [dict(SM.DEFAULTS, **{"from": 2, "to": 4, "invert": True}),
                       dict(SM.DEFAULTS, **{"from": 8, "to": 10, "invert": True}),
                       dict(SM.DEFAULTS, **{"from": 9, "to": 9, "invert": False})]   # overlaps: union
        done = threading.Event()
        result = {}
        self.assertIsNone(ed.save(None, None, lambda *a: None,
                                  lambda *a: (result.update(args=a), done.set())))
        self.assertTrue(done.wait(60))
        out_path, segs, n_done, cancelled, error = result["args"]
        self.assertIsNone(error)
        self.assertEqual(len(segs), 3)
        cap = cv2.VideoCapture(out_path)
        means = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            means.append(float(f.mean()))
        cap.release()
        self.assertEqual(len(means), 11)                  # up to the last selection's To
        white = [i for i, m in enumerate(means) if m > 200]
        self.assertEqual(white, [2, 3, 4, 8, 9, 10], [round(m) for m in means])


class NoFadesTests(unittest.TestCase):
    """Fades were removed (user, 2026-10-02: a fading mask let the warped depth
    back onto letters still on screen). A sidecar saved with fade keyframes
    opens without them, and its selection renders at full strength."""

    def test_old_fade_keys_are_dropped(self):
        segs = SM.segments_from_sidecar({"submask_segments": [
            {"from": 2, "to": 9, "hold": 4, "fade_in": 4, "fade_out": 7}]}, N)
        self.assertEqual((segs[0]["hold"], "fade_in" in segs[0], "fade_out" in segs[0]), (4, False, False))
        self.assertNotIn("fade_in", SM.segments_to_sidecar(segs)["submask_segments"][0])
        self.assertFalse(hasattr(SM, "fade_alpha"))


class FreezeTests(unittest.TestCase):
    """A clip whose 'text' (a bright block) is only bright on frame 5 -- dim
    elsewhere, as fading text is: detection catches it on 5 alone; frozen on
    5, every frame of the selection gets it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clip = Path(tmp.name) / "fade.mp4"
        frames = []
        for i in range(N):
            f = np.zeros((48, 64, 3), np.uint8)
            f[30:40, 10:54] = 250 if i == 5 else 120
            frames.append(f)
        raw = b"".join(f.tobytes() for f in frames)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                        "-s", "64x48", "-r", "12", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p",
                        str(clip)], input=raw, check=True)
        self.ed = SM.SubtitleMask(eng=None, job_running=lambda: True)
        self.addCleanup(self.ed.close)
        self.ed.cap, self.ed.video_path = cv2.VideoCapture(str(clip)), str(clip)
        self.ed.n_frames, self.ed.vw, self.ed.vh, self.ed.fps, self.ed.open = N, 64, 48, 12.0, True
        self.ed.segments = [dict(SM.DEFAULTS, **{"from": 2, "to": 10, "r": 0})]

    def test_freeze_carries_the_caught_mask(self):
        ed = self.ed
        ed.seek(8)
        self.assertEqual(int(ed.mask.max()), 0)                    # dim: not caught
        ed.seek(5); ed.freeze_here()
        self.assertEqual(ed.settings["hold"], 5)
        ed.seek(8)
        self.assertGreater(int((ed.mask > 0).sum()), 300)          # frozen: the block, on 8 too
        ed.seek(12)                                                # outside 2..10: frozen shows nothing
        self.assertIsNone(ed.mask)
        ed.seek(8)
        self.assertGreater(int((ed.mask > 0).sum()), 300)
        ed.unfreeze()
        self.assertEqual(int(ed.mask.max()), 0)

    def test_reading_on_lands_on_the_right_frame(self):
        # a few frames ahead the decoder reads on instead of seeking: same frames
        ed = self.ed
        ed._next_pos = None
        ed.seek(2); ed.seek(5)                                     # 3 ahead: read on
        self.assertGreater(int(ed.frame_bgr[35, 30].mean()), 200)  # frame 5: the bright one
        ed.seek(6)
        self.assertLess(int(ed.frame_bgr[35, 30].mean()), 150)
        ed.seek(1); ed.seek(5)                                     # 4 ahead: a seek
        self.assertGreater(int(ed.frame_bgr[35, 30].mean()), 200)
        ed.seek(5)                                                 # the same frame again (behind: a seek)
        self.assertEqual(ed.frame_idx, 5)
        self.assertGreater(int(ed.frame_bgr[35, 30].mean()), 200)

    def test_the_rectangle_limits_the_work_not_the_answer(self):
        # inside the rectangle the mask is the whole-frame one's
        from core.ui.subtitle_mask_editor import compute_mask
        ed = self.ed
        ed.seek(5)
        s = dict(SM.DEFAULTS, rect=(5, 25, 60, 45), r=2, edge_mode="Soft")
        full = compute_mask(ed.frame_bgr, s)
        self.assertTrue(np.array_equal(ed._mask(ed.frame_bgr, s), full))
        s["invert"] = True
        self.assertTrue(np.array_equal(ed._mask(ed.frame_bgr, s), compute_mask(ed.frame_bgr, s)))

    def test_render_frozen_full_strength(self):
        ed = self.ed
        ed.seek(5); ed.freeze_here()
        done = threading.Event(); result = {}
        ed.save(None, None, lambda *a: None, lambda *a: (result.update(args=a), done.set()))
        self.assertTrue(done.wait(60))
        self.assertIsNone(result["args"][4])
        cap = cv2.VideoCapture(result["args"][0])
        peak = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            peak.append(int(f[30:40, 10:54].mean()))
        cap.release()
        self.assertEqual(len(peak), 11)
        self.assertLess(peak[1], 10)                               # before From
        self.assertTrue(all(p > 240 for p in peak[2:11]), peak)    # 2..10 all full: frozen, no ramps
        segs = SM.segments_from_sidecar(SM.segments_to_sidecar(result["args"][1]), N)
        self.assertEqual(segs[0]["hold"], 5)


class ExactFrameTests(unittest.TestCase):
    """User, 2026-10-02: on long-GOP HEVC with B-frames OpenCV's seek lands on
    the wrong frame (the picture jumped, the frame number was off, Save's mask
    a frame out of step). A clip like that, each frame its own brightness: the
    editor's frames must be the in-order read's, and the mask in step."""

    M = 60

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clip = Path(tmp.name) / "hevc.mp4"
        raw = b"".join(np.full((48, 64, 3), 20 + 3 * i, np.uint8).tobytes() for i in range(self.M))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                        "-s", "64x48", "-r", "24", "-i", "-", "-c:v", "libx265", "-x265-params",
                        "keyint=24:min-keyint=24:bframes=3:log-level=error", "-pix_fmt", "yuv420p", str(self.clip)],
                       input=raw, check=True)
        cap = cv2.VideoCapture(str(self.clip)); self.seq = []          # the truth: read in order
        while True:
            ok, f = cap.read()
            if not ok:
                break
            self.seq.append(f)
        cap.release()
        self.assertEqual(len(self.seq), self.M)
        cap = cv2.VideoCapture(str(self.clip)); cap.set(cv2.CAP_PROP_POS_FRAMES, 40); ok, f = cap.read(); cap.release()
        self.opencv_seek_is_off = not np.array_equal(f, self.seq[40])  # what the fix is for (here: True)
        self.ed = SM.SubtitleMask(eng=None, job_running=lambda: True)     # no background pass: the decode path
        self.addCleanup(self.ed.close)
        self.ed.video_path, self.ed.cap, self.ed._next_pos = str(self.clip), cv2.VideoCapture(str(self.clip)), 0
        self.ed.n_frames, self.ed.vw, self.ed.vh, self.ed.fps, self.ed.open = self.M, 64, 48, 24.0, True

    def test_any_order_lands_on_the_frame(self):
        ed = self.ed
        ed.segments = []
        for idx in (39, 40, 41, 24, 31, 0, 59, 12, 47, 30, 30, 5):
            ed.seek(idx)
            self.assertEqual(ed.frame_idx, idx)
            self.assertTrue(np.array_equal(ed.frame_bgr, self.seq[idx]), idx)

    def test_after_the_background_pass_too(self):
        ed = self.ed
        ed._job_running = lambda: False
        ed._start_prefill(); ed._prefill.join()
        self.assertEqual(len(ed._cache), self.M)
        for idx in (44, 3, 27):
            ed.seek(idx)
            self.assertTrue(np.array_equal(ed.frame_bgr, self.seq[idx]), idx)

    def test_the_mask_is_in_step(self):
        # keyed between frame 39's and frame 40's brightness: lit from 40 exactly
        gray = [int(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()) for f in self.seq]
        self.assertLess(gray[39], gray[40])
        ed = self.ed
        ed.segments = [dict(SM.DEFAULTS, **{"from": 30, "to": 50, "core": gray[40], "refine": False, "r": 0})]
        done = threading.Event(); result = {}
        ed.save(None, None, lambda *a: None, lambda *a: (result.update(args=a), done.set()))
        self.assertTrue(done.wait(60))
        self.assertIsNone(result["args"][4])
        cap = cv2.VideoCapture(result["args"][0]); lit = []; i = 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if f.mean() > 128:
                lit.append(i)
            i += 1
        cap.release()
        self.assertEqual(lit, list(range(40, 51)))



class _Eng:
    """What saved() and reset_all() ask of the preview's engine: the sidecar
    written is recorded, the previewer does nothing."""

    def __init__(self):
        self.sidecar = {}
        self._preview_submask_reader = self._preview_submask_path = None

        class _Pv:
            source_readers = []

            def invalidate_frame_buffer(self):
                pass

            def update_preview(self):
                pass
        self.previewer = _Pv()

    def _save_current_sidecar_data(self, is_auto_save=False, extra_data=None):
        for k, v in (extra_data or {}).items():
            if v is None:
                self.sidecar.pop(k, None)
            else:
                self.sidecar[k] = v
        return True


class FlattenTests(unittest.TestCase):
    """Flatten Layers (user, 2026-10-02: opening credits need many masks): the
    selections so far are saved and set aside; the list starts empty; every
    later Save renders them too; the picture shows them; Reset All clears them."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:size=64x48:rate=12", "-frames:v", str(N), "-pix_fmt", "yuv420p", str(self.clip)],
                       check=True)
        self.eng = _Eng()
        self.ed = SM.SubtitleMask(eng=self.eng, job_running=lambda: True)
        self.addCleanup(self.ed.close)
        self.ed.cap, self.ed.video_path = cv2.VideoCapture(str(self.clip)), str(self.clip)
        self.ed.n_frames, self.ed.vw, self.ed.vh, self.ed.fps, self.ed.open = N, 64, 48, 12.0, True
        # on a black clip, invert makes a selection's frames white
        self.ed.segments = [dict(SM.DEFAULTS, **{"from": 2, "to": 4, "invert": True})]
        self.ed.seek(3)

    def run_op(self, op):
        done = threading.Event()
        result = {}
        self.assertIsNone(getattr(self.ed, op)(None, None, lambda *a: None,
                                               lambda *a: (result.update(args=a), done.set())))
        self.assertTrue(done.wait(60))
        out_path, segs, n_done, cancelled, error = result["args"]
        self.assertIsNone(error)
        msg = self.ed.saved(segs, out_path)            # as the preview does, on the worker
        return out_path, msg

    def white_frames(self, path):
        cap = cv2.VideoCapture(path)
        out, i = [], 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if f.mean() > 200:
                out.append(i)
            i += 1
        cap.release()
        return out

    def test_flatten_then_more(self):
        ed = self.ed
        out, msg = self.run_op("flatten")
        self.assertIn("Flattened", msg)
        self.assertEqual((len(ed.flat), ed.segments), (1, []))           # set aside, the list empty
        self.assertEqual(self.white_frames(out), [2, 3, 4])
        st = ed.state()
        self.assertEqual((st["segments"], st["flat"], st["current"]), ([], [{"from": 2, "to": 4}], -1))
        # the sidecar: flattened kept, the list empty, the mask on
        sc = self.eng.sidecar
        self.assertEqual((len(sc["submask_flat"]), sc["submask_segments"], sc["submask_enabled"]), (1, [], True))
        self.assertEqual(SM.segments_from_sidecar(sc, N), [])           # reopened: no selections...
        self.assertEqual(len(SM.flat_from_sidecar(sc, N)), 1)           # ...and the flattened one
        # the picture shows the flattened one, only on its frames
        ed.seek(3)
        self.assertTrue(ed.mask is not None and ed.mask.mean() > 200)
        ed.seek(7)
        self.assertIsNone(ed.mask)
        # a fresh selection, then Save: both in the mask
        ed.seek(8); ed.add_segment()
        self.assertEqual((len(ed.segments), ed.settings["from"]), (1, 8))
        ed.settings["invert"] = True
        self.assertIsNone(ed.set_range("8", "10"))
        out, _msg = self.run_op("save")
        self.assertEqual(self.white_frames(out), [2, 3, 4, 8, 9, 10])
        self.assertEqual((len(self.eng.sidecar["submask_flat"]), len(self.eng.sidecar["submask_segments"])), (1, 1))
        # flatten again: both flattened
        out, _msg = self.run_op("flatten")
        self.assertEqual((len(ed.flat), ed.segments), (2, []))
        self.assertEqual(self.white_frames(out), [2, 3, 4, 8, 9, 10])

    def test_nothing_to_flatten(self):
        self.ed.segments = []
        self.assertIn("Nothing to flatten", self.ed.flatten(None, None, lambda *a: None, lambda *a: None))
        self.assertFalse(self.ed._flattening)

    def test_save_with_only_flattened_keeps_the_mask(self):
        ed = self.ed
        self.run_op("flatten")
        out, msg = self.run_op("save")                                 # no selections, flattened ones
        self.assertTrue(os.path.exists(out))
        self.assertEqual(self.white_frames(out), [2, 3, 4])
        self.assertIn("saved", msg)

    def test_reset_all_clears_them(self):
        ed = self.ed
        self.run_op("flatten")
        ed.reset_all()
        self.assertEqual(ed.flat, [])
        self.assertNotIn("submask_flat", self.eng.sidecar)
        self.assertFalse(os.path.exists(SM.submask_output_path(str(self.clip))))


if __name__ == "__main__":
    unittest.main()
