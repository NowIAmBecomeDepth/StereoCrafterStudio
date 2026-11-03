"""Inpainting's "Rerun in Splatting" on a chapter picked once its clips were done (a tester,
2026-11-02). chapter_paths.locate then points each stage's inputs where the clips are:
Inpainting at <splats>/lowres/finished and <splats>/hires/finished, Splatting at
<source>/finished/finished. The rerun left the old splat where Inpainting read it, so the next
Inpainting run took the old one again; the new splats, written at the top of <splats>/lowres and
<splats>/hires, were never moved to finished/; and Splatting's Resume sank the source a level
below where Merging reads its original. The clip never showed up in Merging.

Files only, in a temp dir; the stages' own moves (Splatting's and Inpainting's Resume) made as
their engines make them (core.common.file_organizer.move_files_to_finished)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
from studio import chapter_paths, send_back  # noqa: E402
from studio.clip_queue import inpaint_clips, inpaint_hires_of, merge_clips  # noqa: E402
from core.common.file_organizer import move_files_to_finished  # noqa: E402


def touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(p.name.encode())


class LocatedRerunTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.S, self.D, self.SP = self.t / "src", self.t / "depth", self.t / "splats"
        for c in ("c", "d"):                    # the chapter done, through Inpainting
            touch(self.S / "finished" / "finished" / f"{c}.mp4")
            touch(self.D / "finished" / f"{c}_depth.mp4")
            touch(self.SP / "lowres" / "finished" / f"{c}_640_splatted2.mp4")
            touch(self.SP / "hires" / "finished" / f"{c}_1920_splatted2.mp4")
        # the chapter picked now: its inputs where the clips are
        self.low = chapter_paths.locate(str(self.SP / "lowres"))
        self.hi = chapter_paths.locate(str(self.SP / "hires"))
        self.src = chapter_paths.locate(str(self.S / "finished"))
        self.dep = chapter_paths.locate(str(self.D))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def test_layout_is_the_located_one(self):
        self.assertEqual(Path(self.low), self.SP / "lowres" / "finished")
        self.assertEqual(Path(self.src), self.S / "finished" / "finished")

    def test_rerun_splat_inpaint_reaches_merging(self):
        vals = {"input_folder": self.low, "hires_blend_folder": self.hi}
        tv = {"input_source_clips": self.src, "input_depth_maps": self.dep, "output_splatted": str(self.SP),
              "multi_map": False, "move_to_finished": True}
        p = send_back.plan("inpainting", "splatting", [os.path.join(self.low, "c_640_splatted2.mp4")], vals, tv)
        self.assertFalse(p.get("error"), p)
        self.assertEqual(send_back.apply(p), [])
        own = p.get("own_fields") or {}
        low = own.get("input_folder", self.low)
        hi = own.get("hires_blend_folder", self.hi)
        # Inpainting reads where Splatting writes
        self.assertEqual(Path(low), self.SP / "lowres")
        self.assertEqual(Path(hi), self.SP / "hires")
        # the old splats out of every scan
        self.assertFalse((self.SP / "lowres" / "finished" / "c_640_splatted2.mp4").exists())
        self.assertFalse((self.SP / "hires" / "finished" / "c_1920_splatted2.mp4").exists())
        self.assertTrue((self.SP / "lowres" / "finished" / "d_640_splatted2.mp4").exists())   # the other clip stays
        # Splatting: its batch over the ticked clip -- the new splats, then its Resume move
        src = p["fields"].get("input_source_clips", self.src)
        dep = p["fields"].get("input_depth_maps", self.dep)
        out = p["fields"].get("output_splatted", str(self.SP))
        self.assertEqual([os.path.dirname(x) for x in p["ticks"]], [src])
        touch(Path(out) / "lowres" / "c_640_splatted2.mp4")
        touch(Path(out) / "hires" / "c_1920_splatted2.mp4")
        move_files_to_finished([(os.path.join(src, "c.mp4"), src), (os.path.join(dep, "c_depth.mp4"), dep)])
        # Inpainting: the new splat listed, inpainted, it and its Hi-Res moved as its engine moves them
        rows = [r for r in inpaint_clips({"input_folder": low}) if os.path.basename(r).startswith("c_")]
        self.assertEqual([os.path.basename(r) for r in rows], ["c_640_splatted2.mp4"])
        h = inpaint_hires_of(rows[0], hi)
        self.assertTrue(h)
        touch(self.t / "inpainted" / "c_640_inpainted_right_eye.mp4")
        move_files_to_finished([(rows[0], low), (h, hi)])
        # Merging, on the chapter's folders: the clip listed, its original where it reads it
        mask = chapter_paths.locate(str(self.SP / "hires" / "finished"))
        orig = chapter_paths.locate(str(self.S / "finished" / "finished"))
        listed = merge_clips({"inpainted_folder": str(self.t / "inpainted"), "mask_folder": mask})
        self.assertEqual([os.path.basename(x) for x in listed], ["c_640_inpainted_right_eye.mp4"])
        self.assertTrue((self.SP / "hires" / "finished" / "c_1920_splatted2.mp4").exists())
        self.assertTrue((self.SP / "lowres" / "finished" / "c_640_splatted2.mp4").exists())
        self.assertTrue(os.path.exists(os.path.join(orig, "c.mp4")), orig)
        self.assertFalse((self.S / "finished" / "finished" / "finished").exists())

    def test_usual_layout_unchanged(self):
        """Inpainting at the top of lowres/ (the chapter's own folders): nothing of its own changes."""
        vals = {"input_folder": str(self.SP / "lowres"), "hires_blend_folder": str(self.SP / "hires")}
        tv = {"input_source_clips": str(self.S / "finished"), "input_depth_maps": str(self.D),
              "output_splatted": str(self.SP), "multi_map": False, "move_to_finished": True}
        row = str(self.SP / "lowres" / "finished" / "c_640_splatted2.mp4")
        p = send_back.plan("inpainting", "splatting", [row], vals, tv)
        self.assertEqual(p.get("own_fields"), {})
        self.assertEqual(p["fields"], {})
        self.assertEqual([Path(x) for x in p["ticks"]], [self.S / "finished" / "c.mp4"])


class OwnFieldsTest(unittest.TestCase):
    """ClipQueue hands the sending stage its own fields once the moves are done."""

    def test_on_sent_sets_and_saves(self):
        from PySide6.QtCore import QObject
        from studio.tests._qt import app as _qt_app
        from studio.clip_queue import ClipQueue
        _qt_app()

        class Owner(QObject):
            def __init__(self):
                super().__init__()
                self.set_calls, self.saved = [], 0

            def _set(self, k, v):
                self.set_calls.append((k, v))

            def save(self):
                self.saved += 1

        class Target:
            took = None

            def takeBack(self, plan):
                Target.took = plan
        owner = Owner()
        q = ClipQueue("inpainting", lambda: {}, owner)
        q._start = lambda: None
        plan = {"target": "splatting", "moves": [], "aside": [], "failed": [],
                "own_fields": {"input_folder": "X:/splats/lowres"}}
        q._on_sent((plan, Target()))
        self.assertEqual(owner.set_calls, [("input_folder", os.path.normpath("X:/splats/lowres"))])
        self.assertEqual(owner.saved, 1)
        self.assertIs(Target.took, plan)


if __name__ == "__main__":
    unittest.main()
