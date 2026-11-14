"""test_merging_depth_preview.py -- Merging's Depth Map preview for a dual-splat clip reads the clip's own
depth map (a tester, 2026-11-11), and Inpainting V1's Skip Hi-Res Blend setting reaches the engine."""

import os
import sys
import tempfile
import unittest

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.inpainting import settings as IS
from studio.stages.merging import preview_engine as PE


class _Fake:
    depth_sidecar_folders = []
    _preview_depth_frame = PE.MergingPreviewEngine._preview_depth_frame

    class previewer:
        class frame_scrubber_var:
            @staticmethod
            def get():
                return 3


class DepthFrame(unittest.TestCase):
    def test_reads_depth_map_frame(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clip_depth.mp4")
            w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 32))
            for i in range(6):
                w.write(np.full((32, 64, 3), 40 * i, np.uint8))
            w.release()
            fake = _Fake()
            fake.depth_sidecar_folders = [d]
            ref = torch.zeros(1, 3, 16, 32)
            out = fake._preview_depth_frame({"core_name": "clip"}, ref)
            self.assertEqual(tuple(out.shape), (1, 3, 16, 32))
            self.assertGreater(float(out.mean()), 0.2)
            self.assertIsNone(fake._preview_depth_frame({"core_name": "nope"}, ref))
            fake._depth_cap.release()


class PreviewGetsTheFolders(unittest.TestCase):
    """The preview's own engine is told the depth folders (testers, 2026-11-13: only the job's engine was)."""

    def test_render_hands_the_controllers_folders_to_the_engine(self):
        from PySide6.QtGui import QGuiApplication
        from studio.stages.merging.preview import MergingPreview
        app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
        with tempfile.TemporaryDirectory() as d:
            pv = MergingPreview(lambda: {})
            pv.depth_folders_fn = lambda: [d]
            pv._thread = object()            # no worker: the job is run here
            pv._submit("render")
            kind, arg, changes, _gen = pv._jobs.popleft()
            pv._do(kind, arg, changes)
            self.assertEqual(pv._engine.depth_sidecar_folders, [d])


class SkipHires(unittest.TestCase):
    def test_setting_default_and_run_view(self):
        self.assertIs(IS.DEFAULTS["skip_hires_blend"], False)
        self.assertEqual(IS.attr_for("skip_hires_blend"), "skip_hires_blend_var")
        self.assertIs(IS.coerce("skip_hires_blend", "true"), True)

    def test_engine_patch_present(self):
        src = open(os.path.join(os.path.dirname(PE.__file__), "..", "inpainting", "engine.py"), encoding="utf-8").read()
        self.assertIn('get("skip_hires_blend_var")', src)


if __name__ == "__main__":
    unittest.main()
