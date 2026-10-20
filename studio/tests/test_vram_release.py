"""The VRAM tracking leaves behind, and the 3D view's folder scan (user,
2026-10-05: 15.5 of 16 GB held after tracking two objects; every 3D frame
re-scanned Splatting's folders). No GPU needed: torch.cuda is stood in for.

    python -m unittest studio.tests.test_vram_release
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import gpu_ops  # noqa: E402
from core.depthedit import sam3_backend as S  # noqa: E402
from studio.tests._qt import app as _qt_app  # noqa: E402

_app = _qt_app()
from studio.stages.splatting.preview import SplattingPreview  # noqa: E402

GB = 2 ** 30


class FakeCuda:
    """torch.cuda as far as vram_note reads it: 2 GB in use, 6 reserved,
    empty_cache hands back all but the 2 in use."""
    def __init__(self):
        self.reserved, self.emptied = 6 * GB, 0

    def patch(self):
        c = gpu_ops.torch.cuda
        return [mock.patch.object(c, "is_available", lambda: True),
                mock.patch.object(c, "memory_allocated", lambda *a: 2 * GB),
                mock.patch.object(c, "memory_reserved", lambda *a: self.reserved),
                mock.patch.object(c, "empty_cache", self.empty)]

    def empty(self):
        self.emptied += 1
        self.reserved = 2 * GB


class VramNoteTests(unittest.TestCase):
    def run_note(self, release, level):
        cuda = FakeCuda()
        log = logging.getLogger("test_vram_release")
        log.setLevel(level)
        patches = cuda.patch()
        for p in patches:
            p.start()
        try:
            with self.assertLogs(log, logging.DEBUG) if level <= logging.DEBUG else mock.patch.object(log, "debug") as cm:
                gpu_ops.vram_note(log, "SAM 3 tracking", release=release)
        finally:
            for p in patches:
                p.stop()
        return cuda, cm

    def test_release_hands_the_cache_back_and_says_how_much(self):
        cuda, cm = self.run_note(True, logging.DEBUG)
        self.assertEqual(cuda.emptied, 1)
        line = cm.output[0]
        self.assertIn("2.00 GB in use, 6.00 GB reserved", line)
        self.assertIn("4.00 GB handed back", line)

    def test_released_even_without_debug_logging(self):
        cuda, cm = self.run_note(True, logging.INFO)
        self.assertEqual(cuda.emptied, 1)
        cm.assert_not_called()

    def test_a_note_alone_releases_nothing(self):
        cuda, cm = self.run_note(False, logging.DEBUG)
        self.assertEqual(cuda.emptied, 0)
        self.assertNotIn("handed back", cm.output[0])

    def test_tracking_releases_when_it_ends(self):
        eng = S.Sam3Engine()
        eng._session, eng._prompted = "s", {0}

        class Pred:
            def handle_stream_request(self, req):
                for f in range(3):
                    yield {"frame_index": f, "outputs": None}
        eng._predictor = Pred()
        with mock.patch.object(S, "_unpack", lambda o: ([], None)),                 mock.patch.object(gpu_ops, "vram_note") as note:
            frames = [f for f, _ in eng.propagate()]
            self.assertEqual(frames, [0, 1, 2])
            note.assert_called_once()
            self.assertTrue(note.call_args.kwargs.get("release"))
            # a cancelled / abandoned track releases too
            note.reset_mock()
            it = eng.propagate()
            next(it)
            it.close()
            note.assert_called_once()


class Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class ScanCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "src").mkdir(); (self.tmp / "dep").mkdir()
        self.scans = 0
        test = self

        class Eng:
            input_source_clips_var = Var(str(self.tmp / "src"))
            input_depth_maps_var = Var(str(self.tmp / "dep"))
            multi_map_var = Var(False)          # part of the key since bug sweep A6
            selected_depth_map_var = Var("")

            def _find_preview_sources_callback(self):
                test.scans += 1
                return [{"source_video": p} for p in sorted(os.listdir(test.tmp / "src"))]
        self.eng = Eng()
        self.pv = SplattingPreview.__new__(SplattingPreview)    # only _ext_sources is used

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sources(self):
        return SplattingPreview._ext_sources(self.pv, self.eng)

    def test_scanned_once_while_nothing_changes(self):
        (self.tmp / "src" / "a.mp4").write_bytes(b"")
        first = self.sources()
        for _ in range(5):
            self.assertEqual(self.sources(), first)
        self.assertEqual(self.scans, 1)

    def test_a_new_file_scans_again(self):
        self.sources()
        time.sleep(0.02)
        (self.tmp / "src" / "b.mp4").write_bytes(b"")
        self.assertEqual([e["source_video"] for e in self.sources()], ["b.mp4"])
        self.assertEqual(self.scans, 2)

    def test_new_folders_scan_again(self):
        self.sources()
        other = self.tmp / "src2"; other.mkdir()
        self.eng.input_source_clips_var = Var(str(other))
        self.sources()
        self.assertEqual(self.scans, 2)

    def test_a_missing_folder_is_never_cached(self):
        self.eng.input_source_clips_var = Var(str(self.tmp / "gone"))
        self.eng._find_preview_sources_callback = lambda: (setattr(self, "scans", self.scans + 1) or [])
        self.sources(); self.sources()
        self.assertEqual(self.scans, 2)

    def test_the_cached_list_cannot_be_edited_through_a_copy(self):
        (self.tmp / "src" / "a.mp4").write_bytes(b"")
        got = self.sources()
        got.clear()
        again = self.sources()
        again[0]["source_video"] = "changed"
        self.assertEqual(self.sources()[0]["source_video"], "a.mp4")


if __name__ == "__main__":
    unittest.main()
