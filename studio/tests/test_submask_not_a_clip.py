"""Detect Text's <clip>_submask.mp4 is not a clip (user, 2026-10-02: Splatting
listed it and splatted it as a clip of its own). Upcoming Jobs, the batch,
the preview's scan and DepthCrafter's source list skip it; a finished clip's
mask goes to finished/ with it. Empty stand-in files in a temp folder; CPU.

    python -m unittest studio.tests.test_submask_not_a_clip
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common.submask_files import is_subtitle_mask  # noqa: E402


class SubmaskNotAClipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        self.src, self.depth = d / "src", d / "depth"
        self.src.mkdir(); self.depth.mkdir()
        for n in ("shot_0001.mp4", "shot_0001_submask.mp4", "shot_0002.mp4"):
            (self.src / n).write_bytes(b"")
        for n in ("shot_0001_depth.mp4", "shot_0002_depth.mp4"):
            (self.depth / n).write_bytes(b"")

    def test_the_name_test(self):
        self.assertTrue(is_subtitle_mask("A:/x/shot_0001_submask.mp4"))
        self.assertTrue(is_subtitle_mask("shot_SUBMASK.MOV"))
        self.assertFalse(is_subtitle_mask("A:/x/shot_0001.mp4"))
        self.assertFalse(is_subtitle_mask("submarine.mp4"))

    def test_upcoming_jobs(self):
        from studio import clip_queue
        rows = clip_queue.splat_pairs({"input_source_clips": str(self.src), "input_depth_maps": str(self.depth)})
        self.assertEqual(sorted(os.path.basename(v) for v, _ in rows), ["shot_0001.mp4", "shot_0002.mp4"])

    def test_move_to_finished_takes_the_mask_along(self):
        from studio import clip_queue
        moves = clip_queue.splat_finished_moves({}, [(str(self.src / "shot_0001.mp4"), str(self.depth / "shot_0001_depth.mp4"))])
        self.assertIn((str(self.src / "shot_0001_submask.mp4"), str(self.src)), moves)

    def test_the_batch(self):
        import queue, threading
        from core.splatting.batch_processing import BatchProcessor, ProcessingSettings
        bp = BatchProcessor(queue.Queue(), threading.Event())
        ps = ProcessingSettings(input_source_clips=str(self.src), input_depth_maps=str(self.depth),
                                output_splatted=str(self.src.parent / "out"))
        res = bp.setup_batch_processing(ps)
        self.assertEqual(sorted(os.path.basename(v) for v in res.input_videos), ["shot_0001.mp4", "shot_0002.mp4"])

    def test_the_preview_scan(self):
        import types
        import torch  # noqa: F401  (before decord)
        from studio.legacy import install
        install()
        from studio.stages.splatting.preview_engine import SplattingPreviewEngine

        def var(v):
            return types.SimpleNamespace(get=lambda: v, set=lambda x: None)

        class Fake(types.SimpleNamespace):          # the engine's other state: inert
            def __getattr__(self, n):
                return var(False) if n.endswith("_var") else (lambda *a, **k: None)

        fake = Fake(input_source_clips_var=var(str(self.src)), input_depth_maps_var=var(str(self.depth)))
        rows = SplattingPreviewEngine._find_preview_sources_callback(fake)
        self.assertEqual(sorted(os.path.basename(r["source_video"]) for r in rows), ["shot_0001.mp4", "shot_0002.mp4"])

    def test_depthcrafter_sources(self):
        from studio.stages.depthcrafter import runner
        specs, _ = runner.discover_sources(str(self.src))
        self.assertEqual(sorted(s["basename"] for s in specs), ["shot_0001", "shot_0002"])


if __name__ == "__main__":
    unittest.main()
