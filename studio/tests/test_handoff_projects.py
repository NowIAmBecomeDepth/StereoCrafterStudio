"""Job handoff and projects (user, 2026-10-02): Inpainting ran on My Film
chapter 6 while the last Splatting job was My Show; the Inpainting record kept
the show's source, and Merging's Import filled Original from it. Now a record
never carries an earlier stage's folders from another chapter or project, a
folder can be placed in its project chapter, and an Import offers only the
picked project's jobs. Made-up projects in a temp folder; the record redirected.

    python -m unittest studio.tests.test_handoff_projects
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.ui import chapters, job_handoff as jh  # noqa: E402


def _project(root: Path, name: str, prefix: str, chapter: str, unit: str = "") -> Path:
    base = root / name / unit if unit else root / name
    for stage in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
        (base / f"{prefix} {stage}" / chapter).mkdir(parents=True)
    (base / f"{prefix} Splats" / chapter / "hires" / "finished").mkdir(parents=True)
    (base / f"{prefix} Splats" / chapter / "lowres").mkdir(parents=True)
    return base


def fwd(p) -> str:
    return str(p).replace("\\", "/")


class LocateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_movie_chapter_from_any_folder_inside_it(self):
        sm = _project(self.root, "My Film", "SM", "Chapter 6 - First Side Effects")
        at = chapters.locate_chapter(fwd(sm / "SM Splats" / "Chapter 6 - First Side Effects" / "hires" / "finished"))
        self.assertEqual(at, (fwd(sm), 6, "Chapter 6 - First Side Effects"))

    def test_a_show_episode(self):
        ep = _project(self.root, "My Show", "S1E1", "Chapter 1", unit="Season 1/Episode 1")
        at = chapters.locate_chapter(fwd(ep / "S1E1 Inpainted" / "Chapter 1"))
        self.assertEqual(at[0], fwd(ep))
        self.assertEqual(at[1], 1)

    def test_outside_a_project(self):
        (self.root / "loose" / "Chapter 3").mkdir(parents=True)
        self.assertIsNone(chapters.locate_chapter(fwd(self.root / "loose" / "Chapter 3")))
        self.assertIsNone(chapters.locate_chapter(""))


class RecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self._rec = jh._RECORD_PATH
        jh._RECORD_PATH = str(self.root / "last_jobs.json")
        self.addCleanup(lambda: setattr(jh, "_RECORD_PATH", self._rec))
        self.ep = _project(self.root, "My Show", "S1E1", "Chapter 1", unit="Season 1/Episode 1")
        self.sm = _project(self.root, "My Film", "SM", "Chapter 6 - First Side Effects")

    def test_the_users_case(self):
        show = lambda s: fwd(self.ep / f"S1E1 {s}" / "Chapter 1")   # noqa: E731
        jh.record_job("splatting", {"source": show("Source Clips"), "depth": show("DepthMaps"),
                                    "splat_out": show("Splats"), "hires": show("Splats") + "/hires",
                                    "lowres": show("Splats") + "/lowres"})
        sm6 = fwd(self.sm / "SM Splats" / "Chapter 6 - First Side Effects")
        jh.record_job("inpainting", {"hires": sm6 + "/hires", "lowres": sm6 + "/lowres",
                                     "inpainted_out": fwd(self.sm / "SM Inpainted" / "Chapter 6 - First Side Effects")})
        rec = json.loads(Path(jh._RECORD_PATH).read_text())["inpainting"]
        self.assertEqual(rec["source"], fwd(self.sm / "SM Source Clips" / "Chapter 6 - First Side Effects"))
        self.assertEqual(rec["depth"], fwd(self.sm / "SM DepthMaps" / "Chapter 6 - First Side Effects"))
        self.assertEqual(rec["splat_out"], sm6)
        self.assertNotIn("My Show", json.dumps(rec))

    def test_the_same_chapter_carries_over(self):
        ch = lambda s: fwd(self.sm / f"SM {s}" / "Chapter 6 - First Side Effects")   # noqa: E731
        jh.record_job("splatting", {"source": ch("Source Clips") + "/finished", "depth": ch("DepthMaps"),
                                    "hires": ch("Splats") + "/hires"})
        jh.record_job("inpainting", {"hires": ch("Splats") + "/hires", "inpainted_out": ch("Inpainted")})
        rec = json.loads(Path(jh._RECORD_PATH).read_text())["inpainting"]
        self.assertEqual(rec["source"], ch("Source Clips") + "/finished")     # kept as it was

    def test_outside_a_project_nothing_changes(self):
        jh.record_job("splatting", {"source": "D:/somewhere/src", "hires": "D:/somewhere/hires"})
        jh.record_job("inpainting", {"hires": "E:/else/hires", "inpainted_out": "E:/else/out"})
        rec = json.loads(Path(jh._RECORD_PATH).read_text())["inpainting"]
        self.assertEqual(rec["source"], "D:/somewhere/src")


class InProjectTests(unittest.TestCase):
    def test_in_project(self):
        rec = {"inpainted_out": "D:/Projects/My Film/SM Inpainted/Chapter 6"}
        self.assertTrue(jh.in_project(rec, "D:/Projects/My Film", ("inpainted_out",)))
        self.assertTrue(jh.in_project(rec, "d:\\projects\\my film\\", ("inpainted_out",)))
        self.assertFalse(jh.in_project(rec, "D:/Projects/My Show", ("inpainted_out",)))
        self.assertFalse(jh.in_project(rec, "D:/Projects/My", ("inpainted_out",)))   # a prefix, not the folder
        self.assertTrue(jh.in_project(rec, "", ("inpainted_out",)))           # no project picked
        self.assertFalse(jh.in_project({}, "", ("inpainted_out",)))


if __name__ == "__main__":
    unittest.main()
