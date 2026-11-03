"""Copy From Other Frames (user, 2026-11-02; core/removal/flow_fill.py): Edge Fill's strip copied from
the frames where a pan shows it, before ROSE paints the rest.

A made-up pan (a textured scene sliding right, 4 px a frame, so later frames show what the strip hides)
and a stand-in for SEA-RAFT that knows the true motion, so this runs on the CPU without the model: what
is copied, where ROSE still paints, a still shot skipped, a copy whose round trip fails refused, and a
frame that missed its copy filled from its neighbours (a letter went soft for one frame without it)."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import torch  # noqa: E402

from core.removal import flow_fill as FF  # noqa: E402

N, H, W, M = 30, 64, 160, 24     # frames, the crop, the hidden strip
SPEED = 4                         # px a frame the picture slides right


def _scene():
    rng = np.random.default_rng(3)
    wide = W + SPEED * N + 8
    base = rng.uniform(40, 215, (H // 8 + 2, wide // 8 + 2, 3)).astype(np.float32)
    import cv2
    img = cv2.resize(base, (wide, H), interpolation=cv2.INTER_CUBIC)     # smooth texture, every column its own
    img[::9] = 20                                                          # lines across the strip
    return np.clip(img, 0, 255).astype(np.uint8)


def _pan(speed=SPEED):
    """frame t shows the scene from column (N - t) * speed: the picture slides right, the strip's
    hidden columns are on screen in later frames."""
    scene = _scene()
    return np.stack([scene[:, (N - t) * speed:(N - t) * speed + W] for t in range(N)])


class _TrueFlow:
    """SEA-RAFT's stand-in: the pan's own motion, t -> k = speed * (k - t) across; `bad_back` frames
    get a reverse flow that doesn't lead back (the round trip fails)."""

    def __init__(self, speed=SPEED, bad_back=()):
        self.speed, self.bad_back = speed, set(bad_back)

    def between(self, pairs, h, w):
        out = torch.zeros((len(pairs), 2, h, w))
        for j, (a, b) in enumerate(pairs):
            out[j, 0] = self.speed * (b - a)
            if b in self.bad_back:          # (k, t): k -> t -- frame t's copies can't come back
                out[j, 0] += 9.0
        return out

    def release(self):
        pass


def _run(frames, flow):
    with mock.patch.object(FF, "_make_flow", lambda: flow):
        return FF.propagate(frames, M, lambda *a: None, threading.Event())


class FlowFillTest(unittest.TestCase):
    def test_a_pan_copies_the_real_picture(self):
        frames = _pan()
        got = _run(frames, _TrueFlow())
        self.assertIsNotNone(got)
        fill, copy, rose = got
        self.assertEqual(fill.shape, (N, H, M, 3))
        early = slice(0, N - 17)          # every frame with a later one (up to 16 on) that shows all its strip
        self.assertGreater((copy[early] > 200).mean(), 0.9, "the strip is copied where a later frame shows it")
        self.assertLess(rose[early].mean(), 0.1)
        err = np.abs(fill[early].astype(int) - frames[early, :, :M].astype(int))
        self.assertLess(err[copy[early] > 200].mean(), 2.0, "the copy is the real picture")
        self.assertTrue(rose[-1].all(), "the last frame has no later one: ROSE paints it")

    def test_a_still_shot_is_left_to_rose(self):
        frames = np.repeat(_pan()[:1], N, axis=0)
        self.assertIsNone(_run(frames, _TrueFlow(speed=0)), "nothing new in any frame: the pass is skipped")

    def test_a_pan_the_other_way_copies_from_earlier_frames(self):
        """sliding left, the strip's columns were on screen in the frames before"""
        frames = _pan()[::-1].copy()
        fill, copy, rose = _run(frames, _TrueFlow(speed=-SPEED))
        late = slice(17, N)
        self.assertGreater((copy[late] > 200).mean(), 0.9)
        self.assertTrue(rose[0].all(), "the first frame has no earlier one: ROSE paints it")

    def test_a_copy_whose_round_trip_fails_is_refused(self):
        """a person walking against the background smeared: their copies failed the round trip"""
        frames = _pan()
        got = _run(frames, _TrueFlow(bad_back=range(N)))
        self.assertIsNone(got, "no copy passes: nothing is copied")

    def test_a_frame_that_missed_its_copy_takes_its_neighbours(self):
        """2026-11-02: a letter went soft for one frame where that frame alone found no copy"""
        frames = _pan()
        t0 = 6
        fill, copy, rose = _run(frames, _TrueFlow(bad_back={t0}))
        self.assertGreater((copy[t0] > 0).mean(), 0.9, "frame 6's strip is filled from frames 4-8")
        self.assertLess(rose[t0].mean(), 0.1)
        err = np.abs(fill[t0].astype(int) - frames[t0, :, :M].astype(int))
        self.assertLess(err[copy[t0] > 0].mean(), 3.0, "and it is the real picture, not a blur")


if __name__ == "__main__":
    unittest.main()
