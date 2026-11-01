"""Splatting's grids record their warp and mask (2026-10-22): the file's comment tag says which
splat backend and mask mode the render really ran, so a stage that needs an M2S warp can tell an SC
splat from it. The pipe writers put a comment in the file when given one (and nothing when not); the
comment reads back; an M2S backend that didn't load is recorded as the SC warp it fell back to.

    python -m unittest studio.tests.test_grid_comment
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import video_io as V  # noqa: E402
from core.splatting import m2s_backends as M  # noqa: E402


def _comment(path) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment",
                           "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout.strip()


class PipeCommentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="grid-comment-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, start, path, **kw):
        p = start(320, 192, str(path), 24.0, **kw)       # NVENC and DNxHR refuse much smaller
        frame = np.zeros((192, 320, 3), np.uint16).tobytes()
        p.communicate(frame * 2, timeout=60)
        self.assertEqual(p.returncode, 0)

    def test_the_grid_writer_records_the_comment(self):
        out = self.tmp / "a_320_splatted2.mp4"
        self.write(V.start_ffmpeg_pipe_process, out, comment="StereoCrafter Splatting; splat_backend=M2S; mask_mode=SC")
        self.assertEqual(M.parse_grid_comment(_comment(out)), {"splat_backend": "M2S", "mask_mode": "SC"})

    def test_no_comment_none_written(self):
        out = self.tmp / "b_320_splatted2.mp4"
        self.write(V.start_ffmpeg_pipe_process, out)
        self.assertEqual(_comment(out), "")

    def test_the_dnxhr_writer_too(self):
        out = self.tmp / "c_320_splatted2.mov"
        self.write(V.start_ffmpeg_pipe_process_dnxhr, out, comment="StereoCrafter Splatting; splat_backend=SC; mask_mode=SC")
        self.assertEqual(M.parse_grid_comment(_comment(out))["splat_backend"], "SC")


class GridCommentTest(unittest.TestCase):
    def test_what_the_render_ran(self):
        loaded = M.backend_available()
        c = M.parse_grid_comment(M.grid_comment("M2S+Fix", "Dual", is_low_res_task=True))
        self.assertEqual(c["splat_backend"], "M2S+Fix" if loaded else "SC")
        self.assertEqual(c["mask_mode"], "SC")                  # Dual is SC at low res
        c = M.parse_grid_comment(M.grid_comment("M2S", "Dual", is_low_res_task=False))
        self.assertEqual(c["mask_mode"], "HYBRID" if loaded else "SC")
        self.assertEqual(M.parse_grid_comment(M.grid_comment("SC", "SC")), {"splat_backend": "SC", "mask_mode": "SC"})

    def test_an_m2s_backend_that_did_not_load_is_recorded_as_sc(self):
        saved = M._hybrid_mask
        M._hybrid_mask = None
        try:
            self.assertEqual(M.parse_grid_comment(M.grid_comment("M2S", "M2S"))["splat_backend"], "SC")
        finally:
            M._hybrid_mask = saved

    def test_other_comments_are_not_ours(self):
        self.assertEqual(M.parse_grid_comment(""), {})
        self.assertEqual(M.parse_grid_comment("Lavf; encoded by someone"), {})



class M2sFolderTest(unittest.TestCase):
    """An M2S warp's splats in m2s/hires and m2s/lowres, apart from SC's (user, 2026-10-30): Splatting's
    tasks, and the record Inpainting's and Merging's Import read. An M2S backend that didn't load warps
    with SC, and its splats stay with SC's."""

    def tasks(self, backend):
        from types import SimpleNamespace
        from core.splatting.batch_processing import BatchProcessor
        s = SimpleNamespace(enable_full_resolution=True, enable_low_resolution=True, full_res_batch_size=4,
                            low_res_batch_size=8, low_res_width=640, low_res_height=272, splat_backend=backend)
        return [t.output_subdir.replace("\\", "/") for t in BatchProcessor.get_defined_tasks(None, s)]

    def test_sc_splats_where_they_were(self):
        self.assertEqual(M.output_subfolder("SC"), "")
        self.assertEqual(self.tasks("SC"), ["hires", "lowres"])

    @unittest.skipUnless(M.backend_available(), "the M2S backend (dependency_R) isn't here")
    def test_m2s_splats_in_m2s(self):
        for backend in ("M2S", "M2S+Fix"):
            self.assertEqual(M.output_subfolder(backend), "m2s", backend)
            self.assertEqual(self.tasks(backend), ["m2s/hires", "m2s/lowres"], backend)

    @unittest.skipUnless(M.backend_available(), "the M2S backend (dependency_R) isn't here")
    def test_the_next_stages_import_from_m2s(self):
        from studio.stages.splatting import settings as SS
        m2s = SS.job_fields({"output_splatted": "D:/ch/splats", "splat_backend": "M2S"})
        self.assertEqual((m2s["hires"], m2s["lowres"]), ("D:/ch/splats/m2s/hires", "D:/ch/splats/m2s/lowres"))
        sc = SS.job_fields({"output_splatted": "D:/ch/splats", "splat_backend": "SC"})
        self.assertEqual((sc["hires"], sc["lowres"]), ("D:/ch/splats/hires", "D:/ch/splats/lowres"))

    def test_an_m2s_backend_that_didnt_load_stays_with_sc(self):
        saved = M._hybrid_mask
        M._hybrid_mask = None
        try:
            self.assertEqual(M.output_subfolder("M2S"), "")
            self.assertEqual(self.tasks("M2S+Fix"), ["hires", "lowres"])
        finally:
            M._hybrid_mask = saved


if __name__ == "__main__":
    unittest.main()
