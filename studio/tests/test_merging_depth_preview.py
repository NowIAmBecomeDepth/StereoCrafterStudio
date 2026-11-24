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


def _write_map(path, level):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 32))
    for _ in range(6):
        w.write(np.full((32, 64, 3), level, np.uint8))
    w.release()


class MultiMapDepthFrame(unittest.TestCase):
    """Tester carlobs, 2026-11-24: "When i splat with Multi Map then in Merge all the depthmap preview disappear"
    ("Depth Map preview: no ..._depth map in [...\\Depth\\DepthCrafter, ...\\Depth\\sidecars]"): it looked only
    in the subfolder Splatting has picked now. It reads the map the clip's sidecar chose, wherever that is."""

    def test_reads_the_map_the_clips_sidecar_chose(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            _write_map(os.path.join(d, "DepthCrafter", "clip_depth.mp4"), 20)
            _write_map(os.path.join(d, "VDA", "finished", "clip_depth.mp4"), 230)
            os.makedirs(os.path.join(d, "sidecars", "finished"))
            with open(os.path.join(d, "sidecars", "finished", "clip_depth.fssidecar"), "w") as f:
                json.dump({"selected_depth_map": "VDA"}, f)
            fake = _Fake()
            fake.depth_sidecar_folders = [os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars")]
            out = fake._preview_depth_frame({"core_name": "clip"}, torch.zeros(1, 3, 16, 32))
            fake._depth_cap.release()
            self.assertGreater(float(out.mean()), 0.7)          # VDA's map, not DepthCrafter's

    def test_newest_chosen_reads_the_folder_itself(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            _write_map(os.path.join(d, "DepthCrafter", "clip_depth.mp4"), 20)
            _write_map(os.path.join(d, "finished", "clip_depth.mp4"), 230)
            os.makedirs(os.path.join(d, "sidecars"))
            with open(os.path.join(d, "sidecars", "clip_depth.fssidecar"), "w") as f:
                json.dump({"selected_depth_map": "."}, f)
            fake = _Fake()
            fake.depth_sidecar_folders = [os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars")]
            out = fake._preview_depth_frame({"core_name": "clip"}, torch.zeros(1, 3, 16, 32))
            fake._depth_cap.release()
            self.assertGreater(float(out.mean()), 0.7)

    def test_controller_hands_every_multi_map_folder(self):
        from types import SimpleNamespace
        from studio.stages.merging.controller import MergingController
        with tempfile.TemporaryDirectory() as d:
            for sub in ("DepthCrafter", "VDA", "sidecars", "finished"):
                os.makedirs(os.path.join(d, sub))
            ctl = SimpleNamespace(_depth_folders=lambda: [os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars")],
                                  splatting=SimpleNamespace(_values=lambda: {"multi_map": True, "input_depth_maps": d}))
            got = MergingController._depth_map_folders(ctl)
            self.assertEqual(sorted(got), sorted([os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars"), d,
                                                  os.path.join(d, "VDA")]))
            self.assertEqual(got[:2], [os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars")])


class MultiMapFrameBreak(unittest.TestCase):
    """Tester carlobs, 2026-11-24: Merging no longer applied Splatting's Frame Break. A Multi-Map splat moves the
    clip's depth sidecar (where Frame Break is saved) into <its map's subfolder>/finished; the job looked only
    in the subfolder Splatting has picked now and sidecars/."""

    def test_job_finds_the_sidecar_in_another_maps_finished(self):
        import json
        import types
        from types import SimpleNamespace
        from core.common.sidecar_manager import frame_break_settings
        from studio.stages.merging.controller import MergingController
        with tempfile.TemporaryDirectory() as d:
            for sub in ("DepthCrafter", "sidecars", os.path.join("VDA", "finished")):
                os.makedirs(os.path.join(d, sub))
            with open(os.path.join(d, "VDA", "finished", "clip_depth.fssidecar"), "w") as f:
                json.dump({"selected_depth_map": "VDA", "frame_break_bars": 0.2}, f)
            ctl = SimpleNamespace(_engine=SimpleNamespace(), _engine_values={}, _page_values=lambda: {}, _values=lambda: {},
                                  splatting=SimpleNamespace(_values=lambda: {"multi_map": True, "input_depth_maps": d}))
            ctl._depth_folders = lambda: [os.path.join(d, "DepthCrafter"), os.path.join(d, "sidecars")]
            ctl._depth_map_folders = types.MethodType(MergingController._depth_map_folders, ctl)
            eng = MergingController._ensure_engine(ctl)
            self.assertEqual(frame_break_settings("clip", None, eng.depth_sidecar_folders).get("frame_break_bars"), 0.2)


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

    def test_release_closes_the_depth_map_reader(self):
        """A release lets go of the Depth Map view's depth map, so a Rerun can move it aside (tester carlobs,
        2026-11-24: "open in Studio itself" until the 3rd or 4th try)."""
        from PySide6.QtGui import QGuiApplication
        from studio.stages.merging.preview import MergingPreview
        app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clip_depth.mp4")
            w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 32))
            for i in range(4):
                w.write(np.full((32, 64, 3), 40 * i, np.uint8))
            w.release()
            pv = MergingPreview(lambda: {})
            pv.depth_folders_fn = lambda: [d]
            pv._thread = object()            # no worker: the job is run here
            eng = pv._ensure_engine()
            eng.depth_sidecar_folders = [d]
            self.assertIsNotNone(eng._preview_depth_frame({"core_name": "clip"}, torch.zeros(1, 3, 16, 32)))
            pv._submit("release")
            kind, arg, changes, _gen = pv._jobs.popleft()
            pv._do(kind, arg, changes)
            self.assertIsNone(eng._depth_cap)
            os.replace(path, path + ".moved")    # Windows refuses while the reader has it open


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
