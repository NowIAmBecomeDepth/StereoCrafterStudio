"""Put back > Sample (DepthEditController.samplePutBack -> _sampled): the
track is saved and the "Put back at the surface's depth" toast is shown with
the factor, frame count and Splatting settings it sampled with. _sampled used
to build the toast from names it never had (factor, n, conv, maxd, where), so
a NameError ate the toast after the settings were saved.

A stand-in engine and controller in a temp folder; no Qt window, no model.

    .venv\Scripts\python.exe -m unittest studio.tests.test_putback_sample -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

import studio.tests  # noqa: F401  -- the data redirect first: tests never touch the user's data
from core.removal import putback as P
from studio.stages.depthedit.controller import DepthEditController as C


class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Engine:
    def __init__(self, clip, depth, frames):
        self.project = SimpleNamespace(rgb_path=clip, depth_path=depth)
        self.codes = object()
        self.frames = frames

    def _num_frames(self):
        return len(self.frames)

    def _unit(self, idx):
        return self.frames[idx]

    def _status(self, text):
        pass

    def _submit(self, name, job):
        job()                                   # the worker, run in place

    def _ui(self, fn, *a):
        fn(*a)


class _Ctl:
    """Just what samplePutBack and _sampled reach for, the real methods bound."""
    def __init__(self, eng, layers):
        self._engine = eng
        self._from_splat = None
        self._layers = layers
        self.message, self.toast = _Sig(), _Sig()

    def _erase_layers(self, eng):
        return self._layers

    def _dirty(self):
        pass

    samplePutBack = C.samplePutBack
    _sample_track = staticmethod(C._sample_track)
    _sampled = C._sampled
    _splat_settings = C._splat_settings


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clip = os.path.join(self.dir, "shot.mp4")
        depth = os.path.join(self.dir, "shot_depth.mp4")
        self.frames = [np.full((8, 8), v, np.float32) for v in (0.2, 0.4, 0.6)]
        mask = np.zeros((8, 8), bool)
        mask[2:5, 2:5] = True
        layer = SimpleNamespace(masks={0: mask, 2: mask})   # frame 1 takes the nearest
        self.ctl = _Ctl(_Engine(self.clip, depth, self.frames), [layer])

    def test_sample_saves_the_track_and_shows_the_toast(self):
        s = P.load_settings(self.clip)
        s["factor"] = 0.5
        P.save_settings(self.clip, s)
        self.ctl.samplePutBack()
        lo = P.parallax_for_depth(0.2 * 0.5, 0.5, 20.0)
        hi = P.parallax_for_depth(0.6 * 0.5, 0.5, 20.0)
        saved = P.load_settings(self.clip)
        self.assertEqual(len(saved["track"]), 3)
        self.assertEqual(self.ctl.message.calls, [])
        self.assertEqual(len(self.ctl.toast.calls), 1)
        kind, title, text = self.ctl.toast.calls[0]
        self.assertEqual((kind, title), ("info", "Put back at the surface's depth"))
        self.assertEqual(text, f"x0.5 of the depth under the mask on each of 3 frames: parallax "
                               f"{min(lo, hi):+.2f}% to {max(lo, hi):+.2f}% (convergence 0.5, max disparity 20, "
                               f"Splatting's defaults: no sidecar found).")

    def test_no_mask_says_so_and_no_toast(self):
        self.ctl._layers = [SimpleNamespace(masks={})]
        self.ctl.samplePutBack()
        self.assertEqual(self.ctl.toast.calls, [])
        self.assertEqual(self.ctl.message.calls[0][1], "Sample")


if __name__ == "__main__":
    unittest.main()
