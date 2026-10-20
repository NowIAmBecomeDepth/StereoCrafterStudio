"""DepthCrafter's segment merge: memory and output (Studio audit 2026-09-28, PERF).

The merge held about ten float32 copies of the clip at its peak (the segments
copied twice on load, again to align, a list then np.array to stitch, copies
in the normalisation, gamma and the robust second output) -- the likely cause
of the MemoryError on long clips. It now peaks near three, and its output is
bit-for-bit what it was (checked against the old code when this was changed:
five scenarios, identical sha256 of every array saved).

Here: the peak stays under four copies of the merged clip, and the stitched
output matches a plain reference of the same algorithm.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from depthcrafter import merge_depth_segments as M  # noqa: E402

F, H, W, OV, NSEG = 40, 128, 224, 8, 4


def _build(root: str):
    rng = np.random.default_rng(3)
    jobs, segs = [], []
    for i in range(NSEG):
        frames = (rng.random((1, H, W), dtype=np.float32) * 0.3
                  + np.linspace(0.2, 0.9, F, dtype=np.float32)[:, None, None] * (1 + 0.1 * i)).astype(np.float32)
        segs.append(frames)
        np.savez(os.path.join(root, f"s{i}.npz"), frames=frames)
        jobs.append({"segment_id": i, "status": "success", "output_segment_filename": f"s{i}.npz",
                     "output_segment_format": "npz", "processed_at_fps": 24.0})
    meta = {"global_processing_settings": {"processed_as_segments": True,
                                           "segment_definition_output_overlap_frames": OV},
            "jobs_info": jobs, "total_expected_jobs": NSEG, "original_video_basename": "clip"}
    path = os.path.join(root, "m.json")
    with open(path, "w") as f:
        json.dump(meta, f)
    return path, segs


def _reference(segs):
    """shift_scale alignment + linear overlap blend, written plainly."""
    from depthcrafter import dav_util as util
    aligned = [segs[0].copy()]
    for cur in segs[1:]:
        s, t = util.compute_scale_and_shift_full(cur[:OV].reshape(-1), aligned[-1][-OV:].reshape(-1),
                                                 np.ones(OV * H * W, np.float32))
        aligned.append(s * cur + t)
    out = list(aligned[0][:-OV])
    for i in range(1, NSEG):
        out += util.get_interpolate_frames(list(aligned[i - 1][-OV:]), list(aligned[i][:OV]))
        out += list(aligned[i][OV:] if i == NSEG - 1 else aligned[i][OV:-OV])
    return np.array(out, dtype=np.float32)


class MergeMemoryTest(unittest.TestCase):
    def setUp(self):
        self.saved = []
        real = M._save_output_to_disk
        M._save_output_to_disk = lambda v, *a, **k: self.saved.append(v.copy())
        self.addCleanup(setattr, M, "_save_output_to_disk", real)

    def test_the_peak_stays_under_four_copies_of_the_clip(self):
        with tempfile.TemporaryDirectory() as d:
            meta, _segs = _build(d)
            tracemalloc.start()
            M.merge_depth_segments(meta, output_path_arg=os.path.join(d, "o.mp4"), use_percentile_norm=True,
                                   apply_gamma_correction=True, enable_dual_output_robust_norm=True)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        clip = (NSEG * F - (NSEG - 1) * OV) * H * W * 4
        # the saved copies this test keeps are one clip each
        self.assertLess(peak - 2 * clip, 4 * clip, f"peak {peak / clip:.1f} copies of the clip")

    def test_the_output_is_the_merge_it_always_was(self):
        with tempfile.TemporaryDirectory() as d:
            meta, segs = _build(d)
            M.merge_depth_segments(meta, output_path_arg=os.path.join(d, "o.mp4"))
        ref = _reference(segs)
        lo, hi = ref.min(), ref.max()
        expect = np.clip((ref - lo) / (hi - lo), 0.0, 1.0)
        self.assertEqual(self.saved[0].dtype, np.float32)
        np.testing.assert_array_equal(self.saved[0], expect)


if __name__ == "__main__":
    unittest.main()
