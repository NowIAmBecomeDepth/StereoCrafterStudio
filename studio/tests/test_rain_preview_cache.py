"""The Particles preview's cache and a clip change (bug sweep 2026-10-09, A5):
it was keyed on id(reader), and a new clip's reader can reuse the freed address
of the old one's -- frame 0 of the new clip then showed the old clip's rain.
Here one reader object stands in for both clips (the worst case of id reuse):
the second clip must be separated afresh, not served from the first's entry."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import numpy as np  # noqa: E402
import torch  # noqa: E402


class _Batch:
    def __init__(self, a):
        self.a = a

    def asnumpy(self):
        return self.a


class _Reader:
    def __init__(self, n=10, h=24, w=32):
        self.frames = np.zeros((n, h, w, 3), np.uint8)

    def __len__(self):
        return len(self.frames)

    def get_batch(self, idx):
        return _Batch(self.frames[list(idx)])


def _var(v):
    return types.SimpleNamespace(get=lambda: v)


class PreviewCacheTests(unittest.TestCase):
    def test_a_new_clip_with_a_reused_reader_is_not_served_the_old_rain(self):
        from core.splatting import rain as R
        tmp = Path(tempfile.mkdtemp())
        clip_a, clip_b = tmp / "a.mp4", tmp / "b.mp4"
        clip_a.write_bytes(b"a")
        clip_b.write_bytes(b"b")
        reader = _Reader()
        pv = types.SimpleNamespace(source_readers={"source_video": reader}, last_loaded_frame_index=0,
                                   last_loaded_video_path=str(clip_a))
        engine = types.SimpleNamespace(previewer=pv, rain_mode_var=_var("moving"))
        left = torch.zeros(1, 3, 24, 32)
        depth = torch.zeros(1, 1, 24, 32)
        empty = (np.zeros((24, 32, 3), np.float32), np.zeros((24, 32), np.float32),
                 np.zeros((24, 32, 3), np.float32))
        R._PREVIEW_CACHE.clear()
        with mock.patch.object(R.RainExtractor, "extract_parts", return_value=[empty]) as extract:
            R.preview_rain(engine, left, depth, {})
            R.preview_rain(engine, left, depth, {})               # the same clip, the same frame: cached
            self.assertEqual(extract.call_count, 1)
            pv.last_loaded_video_path = str(clip_b)             # Next clip; the reader's id is the same
            R.preview_rain(engine, left, depth, {})
            self.assertEqual(extract.call_count, 2, "clip B was served clip A's rain")
        R._PREVIEW_CACHE.clear()


class WebPresetTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A5): on an install without personal/ (no Web), a
    sidecar's particle "web" became Rain but kept its Web scenario ("orb"), and
    Rain then ran half of its soft extractor on downpour sizes."""

    def test_a_web_scenario_without_web_falls_back_to_downpour(self):
        from core.splatting import rain as R
        with mock.patch.object(R, "PARTICLES", ("rain", "sparks")):          # a release install
            rs = R.RainSettings.from_values(mode="moving", particle="web", preset="orb")
        self.assertEqual((rs.particle, rs.preset), ("rain", "downpour"))
        with mock.patch.object(R, "PARTICLES", ("rain", "sparks", "web")):   # with Web: kept
            rs = R.RainSettings.from_values(mode="moving", particle="web", preset="orb")
        self.assertEqual((rs.particle, rs.preset), ("web", "orb"))
        rs = R.RainSettings.from_values(mode="moving", particle="rain", preset="mist")
        self.assertEqual(rs.preset, "mist", "a rain scenario was changed")


if __name__ == "__main__":
    unittest.main()
