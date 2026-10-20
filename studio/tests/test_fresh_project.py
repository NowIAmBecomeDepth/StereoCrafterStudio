"""A new project run start to finish with Studio's defaults (user, 2026-10-18 -- found making the manual's
demo project): Inpainting makes its chapter's output folder like every other stage; an erased clip keeps
its Put Back after a stage moves it into finished/ (its edit_history stays where the erase put it).
Edge Fill's _splatted4 masks are in test_edge_fill.QuadSplatTest. Temp folders; configs and
last_jobs.json redirected (studio.tests)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()
from studio.tests._qt import app as _qt_app  # noqa: E402
_app = _qt_app()
from studio.tests.test_splatting import _Shell  # noqa: E402
from core.removal import erase as E  # noqa: E402
from core.removal import putback as P  # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
        ss.CONFIG_DIR, jh._RECORD_PATH = self.tmp / "config", str(self.tmp / "last_jobs.json")
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(jh, "_RECORD_PATH", saved[1])))


class InpaintingMakesItsOutputFolderTest(_Tmp):
    """A new chapter has no Inpainted/<chapter> until Inpainting runs: Start was refused with "Invalid
    input folder/file or output folder"."""

    def start(self, out):
        from studio.stages.inpainting import controller as C
        inp = self.tmp / "Splats" / "Chapter 1" / "lowres"
        inp.mkdir(parents=True)
        (inp / "shot_1408_splatted4.mp4").write_bytes(b"x")
        ctl = C.InpaintingController(_Shell())
        ctl._map.insert("input_folder", str(inp))
        ctl._map.insert("output_folder", str(out))
        toasts = []
        ctl.toast.connect(lambda *a: toasts.append(a))

        class Thread:
            def __init__(self, target=None, name=None, daemon=None):
                pass

            def start(self):
                pass
        with mock.patch.object(ctl, "_claim", return_value=True), mock.patch.object(C.threading, "Thread", Thread):
            ctl.start()
        return ctl, toasts

    def test_a_missing_output_folder_is_made_and_the_job_starts(self):
        out = self.tmp / "Inpainted" / "Chapter 1"
        ctl, toasts = self.start(out)
        self.assertTrue(out.is_dir())
        self.assertFalse([t for t in toasts if t[0] == "error"], toasts)
        self.assertEqual(os.path.normpath(ctl._run_paths["output"]), os.path.normpath(str(out)))

    def test_a_start_refused_for_its_input_makes_no_folder(self):
        from studio.stages.inpainting import controller as C
        ctl = C.InpaintingController(_Shell())
        out = self.tmp / "Inpainted" / "Chapter 9"
        ctl._map.insert("input_folder", str(self.tmp / "nowhere"))
        ctl._map.insert("output_folder", str(out))
        toasts = []
        ctl.toast.connect(lambda *a: toasts.append(a))
        with mock.patch.object(ctl, "_claim", return_value=True):
            ctl.start()
        self.assertTrue([t for t in toasts if t[0] == "error"], toasts)
        self.assertFalse(out.exists())

    def test_no_output_folder_at_all_is_still_refused(self):
        ctl, toasts = self.start("")
        self.assertTrue([t for t in toasts if t[0] == "error"], toasts)
        self.assertFalse(getattr(ctl, "_run_paths", None))


class ErasedClipMovedTest(_Tmp):
    """Erase from Clip keeps the original in edit_history/ beside the clip, with a record of the erase.
    Splatting (and DepthCrafter's batch) then move the clip into finished/, without its edit_history:
    Merging lost the Put Back section and the Object removed badge."""

    def setUp(self):
        super().setUp()
        self.src = self.tmp / "Source Clips" / "Chapter 4" / "finished"
        hist = self.src / E.HISTORY_DIR
        hist.mkdir(parents=True)
        self.clip = self.src / "shot.mp4"
        self.clip.write_bytes(b"erased picture")
        original = hist / "shot.original.mp4"
        original.write_bytes(b"the real footage")
        (hist / "shot.mp4.erase.json").write_text(
            json.dumps({"live": E._file_id(str(self.clip)), "original": str(original), "method": "rose"}),
            encoding="utf-8")

    def move_down(self, clip, levels=1):
        dest = clip.parent
        for _ in range(levels):
            dest = dest / "finished"
        dest.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(clip), str(dest / clip.name)))

    def test_where_it_was_erased(self):
        self.assertIsNotNone(P.erased(str(self.clip)))
        self.assertEqual(os.path.normpath(E.history_dir(str(self.clip))), os.path.normpath(str(self.src / E.HISTORY_DIR)))

    def test_moved_into_finished_it_is_still_erased(self):
        moved = self.move_down(self.clip)
        self.assertIsNotNone(P.erased(str(moved)), "the erase record wasn't found after the move")
        self.assertEqual(E.source_for(str(moved)), str(self.src / E.HISTORY_DIR / "shot.original.mp4"))

    def test_moved_down_two_levels_too(self):
        moved = self.move_down(self.clip, 2)
        self.assertIsNotNone(P.erased(str(moved)))

    def test_restored_back_up_it_is_still_erased(self):
        up = self.src.parent / "shot.mp4"
        shutil.move(str(self.clip), str(up))
        self.assertIsNotNone(P.erased(str(up)))

    def test_its_put_back_settings_follow_it(self):
        P.save_settings(str(self.clip), dict(P.load_settings(str(self.clip)), enabled=True, mode="Over"))
        moved = self.move_down(self.clip)
        s = P.load_settings(str(moved))
        self.assertTrue(s["enabled"])
        self.assertEqual(s["mode"], "Over")

    def test_the_clip_lists_badge_follows_it(self):
        from studio import clip_queue
        moved = self.move_down(self.clip)
        self.assertEqual(clip_queue._erased_flags([str(moved)]), [True])

    def test_a_clip_never_erased_keeps_its_history_beside_it(self):
        other = self.src / "other.mp4"
        other.write_bytes(b"x")
        self.assertEqual(os.path.normpath(E.history_dir(str(other))), os.path.normpath(str(self.src / E.HISTORY_DIR)))
        self.assertIsNone(P.erased(str(other)))


if __name__ == "__main__":
    unittest.main()
