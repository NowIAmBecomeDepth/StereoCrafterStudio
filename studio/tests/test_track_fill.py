"""Copy From Other Frames with AllTracker (user, 2026-11-07; core/removal/track_fill.py): each source frame's
real picture followed into its neighbours' hidden strips.

test_flow_fill's made-up pan (a textured scene sliding right, 4 px a frame) and a stand-in for AllTracker that
finds the pan's own motion (phase correlation on the picture beside the strip), so this runs on the CPU without
the model: the real picture copied in, the inpainter left only what no frame shows, a still shot skipped, no
copy across a cut, sources that disagree dropped, and which model runs when one isn't downloaded."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import torch  # noqa: E402

from core.removal import edge_fill as EF  # noqa: E402
from core.removal import flow_fill as FF  # noqa: E402
from core.removal import track_fill as TF  # noqa: E402
from studio.tests.test_flow_fill import M, N, SPEED, _pan  # noqa: E402


class _PanTracker:
    """AllTracker's stand-in: it knows the pan (frame i shifted SPEED * i to the right), recognises each frame
    by its picture beside the strip, and moves frame 0's every pixel by the true shift (none across a cut:
    frames it doesn't know are left where they are). `noise`: a random extra shift per pair."""

    def __init__(self, noise=0.0, pan=None):
        self.noise = noise
        self.rng = np.random.default_rng(0)
        self.calls = 0
        self.pan = _pan() if pan is None else pan

    def _index(self, img):
        right = img[:, M + 8:].astype(np.int16)
        for i, f in enumerate(self.pan):
            if np.abs(right - f[:, M + 8:].astype(np.int16)).mean() < 2:
                return i
        return None

    def track(self, seq):
        self.calls += 1
        imgs = seq.permute(0, 2, 3, 1).round().byte().cpu().numpy()
        S, h, w = imgs.shape[:3]
        flows = torch.zeros((S, 2, h, w))
        i0 = self._index(imgs[0])
        for j in range(1, S):
            ij = self._index(imgs[j])
            if i0 is not None and ij is not None:
                flows[j, 0] = SPEED * (ij - i0) + (float(self.rng.normal(0, self.noise)) if self.noise else 0.0)
        return flows, torch.ones((S, h, w))

    def release(self):
        pass


def _run(frames, tracker=None):
    with mock.patch.object(TF, "_make_tracker", lambda: tracker or _PanTracker()):
        return TF.propagate(frames, M, lambda *a: None, threading.Event())


class TrackFillTest(unittest.TestCase):
    def setUp(self):
        FF.clear_cache()            # each test tracks for itself (the session's cache would hand back the last)
    def test_the_real_picture_is_copied_into_the_strip(self):
        real = _pan()
        frames = real.copy()
        frames[:, :, :M] = (250, 20, 200)                  # the strip as a merged clip has it: wrong
        fill, copy, rose = _run(frames)
        took = copy > 127
        self.assertGreater(took[: N - 6].mean(), 0.9, "where later frames show the strip, it is copied")
        err = np.abs(fill.astype(int) - real[:, :, :M].astype(int))[took].mean()
        self.assertLess(err, 4.0, "the copy is the real picture")
        self.assertTrue(rose[-1].any(), "the last frame's strip no frame shows: the inpainter paints it")
        self.assertFalse(rose[2].any(), "an early frame's strip wholly copied: nothing left to paint")

    def test_a_still_shot_is_skipped_after_a_quick_look(self):
        still = np.repeat(_pan()[:1], N, axis=0)
        tr = _PanTracker()
        self.assertIsNone(_run(still, tr))
        self.assertLessEqual(tr.calls, 2 * TF.LOOK_SOURCES, "only the quick look ran (each source both ways)")

    def test_nothing_is_copied_across_a_cut(self):
        real = _pan()
        other = np.ascontiguousarray((real[:, ::-1] * np.array([0.2, 0.35, 1.0])).astype(np.uint8))  # another shot: blue
        cut = N // 2
        frames = np.concatenate([real[:cut], other[cut:]])
        self.assertEqual(list(TF.shots(frames)), [0] * cut + [1] * (N - cut))
        fill, copy, _rose = _run(frames.copy())
        took = copy > 127
        if took[cut - 3: cut].any():
            err = np.abs(fill[cut - 3: cut].astype(int) - real[cut - 3: cut, :, :M].astype(int))[took[cut - 3: cut]]
            self.assertLess(err.mean(), 4.0, "the first shot's last frames copy only from their own shot")

    def test_sources_that_disagree_are_dropped(self):
        frames = _pan()
        _f, calm, _r = _run(frames.copy())
        FF.clear_cache()                                     # the same pan, tracked again (shakily)
        _f, shaky, _r = _run(frames.copy(), _PanTracker(noise=6.0))
        self.assertLess((shaky > 127).mean(), (calm > 127).mean() * 0.8,
                        "tracks that land at different colours are left to the inpainter")


class CacheTest(unittest.TestCase):
    """The session's cache (user, 2026-11-07): the same clip filled again isn't tracked again; any change to what
    goes in is; the oldest entries go past the cap. In RAM, for as long as Studio runs."""

    def setUp(self):
        FF.clear_cache()

    def tearDown(self):
        FF.clear_cache()

    def test_the_same_input_again_is_not_tracked_again(self):
        frames = _pan()
        first = _PanTracker()
        a = _run(frames.copy(), first)
        again = _PanTracker()
        b = _run(frames.copy(), again)
        self.assertGreater(first.calls, 0)
        self.assertEqual(again.calls, 0, "reused")
        for x, y in zip(a, b):
            self.assertTrue(np.array_equal(x, y))
        b[0][:] = 0                                          # a caller changing its copy...
        c = _run(frames.copy(), _PanTracker())
        self.assertTrue(np.array_equal(c[0], a[0]), "...doesn't change what is kept")

    def test_a_change_tracks_again(self):
        frames = _pan()
        _run(frames.copy())
        for changed in (frames[:, :, ::-1].copy(), frames[:-1].copy()):
            tr = _PanTracker()
            _run(changed, tr)
            self.assertGreater(tr.calls, 0)
        near = np.zeros(frames.shape[:3], np.uint8)
        tr = _PanTracker()
        with mock.patch.object(TF, "_make_tracker", lambda: tr):
            TF.propagate(frames.copy(), M, lambda *a: None, threading.Event(), near=near)
        self.assertGreater(tr.calls, 0, "Depth Aware's near map is part of the input")

    def test_a_still_shot_is_remembered_too(self):
        still = np.repeat(_pan()[:1], N, axis=0)
        self.assertIsNone(_run(still.copy()))
        tr = _PanTracker()
        self.assertIsNone(_run(still.copy(), tr))
        self.assertEqual(tr.calls, 0, "not even the quick look again")

    def test_the_oldest_go_past_the_cap(self):
        frames = _pan()
        a = _run(frames.copy())
        other = frames.copy()
        other[0, 0, 0] ^= 1                                  # a second clip (a pixel apart: tracked alike)
        with mock.patch.object(FF, "CACHE_BYTES", sum(x.nbytes for x in a) + 1):
            self.assertIsNotNone(_run(other))                # over the cap: the first goes
            tr = _PanTracker()
            _run(frames.copy(), tr)
        self.assertGreater(tr.calls, 0)


class CopierChoiceTest(unittest.TestCase):
    def _ready(self, copier, at, sea, cuda=True):
        with mock.patch.object(TF, "available", lambda: at), mock.patch.object(FF, "available", lambda: sea), \
                mock.patch.object(torch.cuda, "is_available", lambda: cuda):
            return EF._copy_ready(copier)

    def test_the_model_asked_for_else_the_other_else_none(self):
        self.assertIs(self._ready("AllTracker", True, True), TF)
        self.assertIs(self._ready("SEA-RAFT", True, True), FF)
        self.assertIs(self._ready("AllTracker", False, True), FF, "AllTracker not downloaded: SEA-RAFT")
        self.assertIs(self._ready("SEA-RAFT", True, False), TF)
        self.assertIsNone(self._ready("AllTracker", False, False))
        self.assertIsNone(self._ready("AllTracker", True, True, cuda=False), "no NVIDIA card: the inpainter")

    def test_the_setting_and_its_models_row(self):
        from studio import model_catalog as MC
        from studio.stages.edgefill import settings as S
        self.assertEqual(S.DEFAULTS["copy_with"], "AllTracker")
        self.assertEqual(S.coerce("copy_with", "RAFT"), "AllTracker")
        self.assertEqual(S.coerce("copy_with", "SEA-RAFT"), "SEA-RAFT")
        m = MC.MODELS_BY_KEY["alltracker"]
        self.assertEqual((m.parts[0].folder, m.parts[0].files[0][0]), (TF.WEIGHTS[0], TF.WEIGHTS[1]))


class TwoFrameWindowTest(unittest.TestCase):
    """A window of two frames (a source next to a cut, or at the clip's end): AllTracker's forward_sliding
    answers T <= 2 in its own shape -- frame 1's flow [B, 2, h, w] and visconf [B, 2, h, w], no time axis --
    and track() read it as [S, ...]: the confidence came out [2, w], and the upscale failed with "Input and
    output must have the same number of spatial dimensions" (user, 2026-11-08: a 1920 clip, Depth Aware on)."""

    class _Net:
        def __init__(self, h, w):
            self.h, self.w = h, w

        def forward_sliding(self, seq, **kw):
            b, t = seq.shape[:2]
            if t <= 2:              # alltracker.py: the T <= 2 branch
                return (torch.full((b, 2, self.h, self.w), 3.0), torch.full((b, 2, self.h, self.w), 0.25),
                        None, None)
            return (torch.full((b, t, 2, self.h, self.w), 3.0), torch.full((b, t, 2, self.h, self.w), 0.25),
                    None, None)

    def _track(self, frames):
        tr = TF._Tracker.__new__(TF._Tracker)       # no checkpoint: the network stood in for
        tr.torch, tr.net = torch, self._Net(24, 40)
        with mock.patch.object(torch, "autocast", lambda *a, **k: torch.no_grad()):
            return tr.track(torch.zeros(frames, 3, 24, 40))

    def test_two_frames_come_back_per_frame(self):
        flows, conf = self._track(2)
        self.assertEqual((tuple(flows.shape), tuple(conf.shape)), ((2, 2, 24, 40), (2, 24, 40)))
        self.assertEqual(float(flows[0].abs().max()), 0.0)     # frame 0 is the query: it stays where it is
        self.assertEqual(float(flows[1].min()), 3.0)
        self.assertEqual(float(conf[1].min()), 0.25)

    def test_longer_windows_as_before(self):
        flows, conf = self._track(5)
        self.assertEqual((tuple(flows.shape), tuple(conf.shape)), ((5, 2, 24, 40), (5, 24, 40)))


if __name__ == "__main__":
    unittest.main()
