"""Where each stage lands on a project switch (user, 2026-11-12): a tester's flat project kept another
project's folders in every stage, a left project remembered those foreign folders as its own, and the
Import buttons filled inputs but left Output on the previous project. Now a stage lands on its own
chapter, else its own folders (never another project's), else its neighbours' chapter, else the
project's one entry, else "needs a chapter" (Start refused); the switch is summed up once; and an
Import fills the whole stage from one chapter of the open project. Real Shell (settings redirected),
stand-in stages (the real ones' chapter code, without their engines) plus the real Inpainting and
Merging controllers for the Imports. Temp folders only.

    python -m unittest studio.tests.test_project_landing
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()
from studio.tests._qt import app as _qt_app  # noqa: E402
_app = _qt_app()
from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: E402
from PySide6.QtQml import QQmlPropertyMap  # noqa: E402
from core.ui import chapters, job_handoff as jh  # noqa: E402
from studio import chapter_paths, project_memory  # noqa: E402

WHOLE = getattr(chapters, "WHOLE", 99999)   # (getattr: the test also runs, red, on the code before)

FIELDS = {
    "depthcrafter": (("input", "dc_in"), ("output", "dc_out")),
    "splatting": (("source", "sp_src"), ("depth", "sp_depth"), ("output", "sp_out")),
    "inpainting": (("lowres", "ip_lo"), ("hires", "ip_hi"), ("output", "ip_out")),
    "merging": (("inpainted", "mg_in"), ("original", "mg_orig"), ("mask", "mg_mask"), ("output", "mg_out")),
}


class Stage(QObject):
    """A stage's chapter code as the real controllers have it, nothing else."""
    chapterChanged = Signal()
    toast = Signal(str, str, str)

    def __init__(self, shell, stage):
        super().__init__()
        self._shell, self.stage, self.fields = shell, stage, FIELDS[stage]
        self._map = QQmlPropertyMap(self)
        for _r, k in self.fields:
            self._map.insert(k, "")
        self._last_good = {}
        self._chapters, self._chapter_num, self._chapter_expected = [], -1, {}
        self._chapter_applying = False
        self.toasts = []
        self.toast.connect(lambda *a: self.toasts.append(a))
        shell.projectChanged.connect(self.refreshChapters)
        project_memory.attach(self, stage, shell, [k for _r, k in self.fields])
        self.refreshChapters()

    def value(self, role):
        return str(self._map.value(dict(self.fields)[role]) or "")

    def refreshChapters(self):
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter(self.stage)
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        self.chapterChanged.emit()

    def pickChapter(self, num):
        if num < 0:
            self._chapter_num = -1
            self._shell.setStageChapter(self.stage, {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths(self.stage, num, name)
        self._chapter_applying = True
        for role, key in self.fields:
            if paths.get(role):
                self._map.insert(key, paths[role])
        self._chapter_applying = False
        self._chapter_num, self._chapter_expected = num, paths
        self._shell.setStageChapter(self.stage, {"num": num, "name": name})
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):
            self.toast.emit("info", "Chapter selected", name)


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def flat(root: Path, name: str) -> Path:
    base = root / name
    for n in ("input", "depth", "splat/hires", "splat/lowres", "inpaint", "merged"):
        (base / n).mkdir(parents=True)
    (base / "input" / "shot.mp4").write_bytes(b"x")
    return base


def chaptered(root: Path, name: str, prefix: str, nums=(1, 2)) -> Path:
    base = root / name
    for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
        for n in nums:
            (base / f"{prefix} {st}" / f"Chapter {n}").mkdir(parents=True)
    for n in nums:
        for sub in ("hires", "lowres"):
            (base / f"{prefix} Splats" / f"Chapter {n}" / sub).mkdir()
    return base


class _Base(unittest.TestCase):
    def setUp(self):
        import studio.settings_store as ss
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
        ss.CONFIG_DIR, jh._RECORD_PATH = self.root / "config", str(self.root / "last_jobs.json")
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(jh, "_RECORD_PATH", saved[1])))
        self.addCleanup(lambda: getattr(chapters, "set_extra_overrides", lambda m: None)({}))
        from studio.shell import StudioShell
        self.shell = StudioShell()
        self.projects = self.root / "Projects"
        self.shell.projectsRoot = fwd(self.projects)
        self.lacasa = flat(self.projects, "LaCasa")
        self.film = chaptered(self.projects, "My Film", "MF")
        self.landed = []
        if hasattr(self.shell, "projectLanded"):
            self.shell.projectLanded.connect(self.landed.append)

    def stages(self, *names):
        return {n: Stage(self.shell, n) for n in names}

    def go(self, project):
        self.shell.projectFolder = fwd(project)
        for _ in range(3):
            QCoreApplication.processEvents()

    def key(self, n):
        return next(c["num"] for c in self.shell.listChapters() if c["name"] == f"Chapter {n}")


class LandingTests(_Base):
    def test_the_testers_case(self):
        """On My Film chapter 1, then the flat LaCasa: every stage on LaCasa's Whole project."""
        st = self.stages("depthcrafter", "splatting", "inpainting", "merging")
        self.go(self.film)
        for s in st.values():
            s.pickChapter(self.key(1))
        self.go(self.lacasa)
        for name, s in st.items():
            self.assertEqual(s._chapter_num, WHOLE, name)
            for role, _k in s.fields:
                self.assertTrue(s.value(role).startswith(fwd(self.lacasa)), (name, role, s.value(role)))
        self.assertEqual(self.landed[-1], "LaCasa: 4 stages on Whole project")
        self.assertEqual(len(self.landed), 2)                            # one line per switch
        self.assertFalse([t for s in st.values() for t in s.toasts if t[1] == "Chapter selected"
                          and t[2] == "Whole project"])                  # quietly
        self.go(self.film)                                               # rule 1: their own chapter back
        self.assertTrue(all(s._chapter_num == self.key(1) for s in st.values()))

    def test_a_stage_follows_its_neighbours_on_its_first_visit_only(self):
        st = self.stages("depthcrafter", "splatting", "inpainting", "merging")
        self.go(self.lacasa)
        mem = self.shell._project_memory(fwd(self.film))
        mem["stage_chapters"] = {"depthcrafter": {"num": self.key_in_film(1), "name": "Chapter 1"},
                                 "splatting": {"num": self.key_in_film(2), "name": "Chapter 2"}}
        self.go(self.film)
        self.assertEqual(st["depthcrafter"]._chapter_num, self.key(1))
        self.assertEqual(st["splatting"]._chapter_num, self.key(2))
        self.assertEqual(st["inpainting"]._chapter_num, self.key(2))        # nearest earlier: Splatting
        self.assertEqual(st["merging"]._chapter_num, self.key(2))           # Inpainting had none: Splatting
        self.assertIn("Inpainting following Splatting: Chapter 2", self.landed[-1])
        st["splatting"].pickChapter(self.key(1))
        self.go(self.lacasa)
        self.go(self.film)
        self.assertEqual(st["inpainting"]._chapter_num, self.key(2))        # its own now: not followed again

    def key_in_film(self, n):
        return n                                                            # a movie's key is its number

    def test_no_chapter_to_land_on(self):
        st = self.stages("splatting")
        s = st["splatting"]
        s._map.insert("sp_out", fwd(self.lacasa / "splat"))
        self.go(self.film)                                                  # two chapters, no memory
        self.assertEqual(s._chapter_num, -1)
        self.assertEqual([s.value(r) for r, _k in s.fields], ["", "", ""])
        self.assertTrue(project_memory.needs_chapter(s))
        self.assertTrue(project_memory.refuse_start(s))
        self.assertEqual(s.toasts[-1][1], "Pick a chapter first")
        self.assertIn("Splatting needs a chapter", self.landed[-1])
        s.pickChapter(self.key(2))
        self.assertFalse(project_memory.needs_chapter(s))
        self.assertFalse(project_memory.refuse_start(s))

    def test_a_folder_set_by_hand_ends_needs_a_chapter(self):
        s = self.stages("splatting")["splatting"]
        self.go(self.film)
        self.assertTrue(project_memory.needs_chapter(s))
        s._map.insert("sp_out", "X:/somewhere")
        self.assertFalse(project_memory.needs_chapter(s))

    def test_a_real_stage_refuses_to_start(self):
        from studio.stages.inpainting import controller as C
        ctl = C.InpaintingController(self.shell)
        toasts = []
        ctl.toast.connect(lambda *a: toasts.append(a))
        self.go(self.film)
        self.assertTrue(ctl.property("needsChapter"))
        self.assertEqual(fwd(ctl._map.value("output_folder")), "")
        ctl.start()
        self.assertEqual(toasts[-1][1], "Pick a chapter first")
        self.assertFalse(ctl.property("busy") if ctl.metaObject().indexOfProperty("busy") >= 0 else False)
        ctl.pickChapter(self.key(1))
        self.assertFalse(ctl.property("needsChapter"))

    def test_another_projects_folders_are_not_remembered(self):
        s = self.stages("merging")["merging"]
        self.go(self.film)
        s.pickChapter(-1)
        for role, k in s.fields:                          # hand-set, but LaCasa's
            s._map.insert(k, fwd(self.lacasa / "merged"))
        self.go(self.lacasa)
        kept = self.shell._project_memory(fwd(self.film)).get("stage_paths", {}).get("merging", {})
        self.assertFalse([v for v in kept.values() if "LaCasa" in str(v)], kept)

    def test_poisoned_memory_is_not_restored_but_a_scratch_folder_is(self):
        st = self.stages("splatting", "merging")
        self.go(self.lacasa)
        mem = self.shell._project_memory(fwd(self.film))
        mem["stage_paths"] = {
            "merging": {k: fwd(self.lacasa / "merged") for _r, k in FIELDS["merging"]},       # LaCasa's
            "splatting": {k: fwd(self.root / "scratch" / k) for _r, k in FIELDS["splatting"]}}  # nobody's
        self.go(self.film)
        self.assertEqual(st["splatting"].value("output"), fwd(self.root / "scratch" / "sp_out"))
        self.assertFalse(st["merging"].value("output").startswith(fwd(self.lacasa)))
        self.assertIn("Splatting kept its own folders", self.landed[-1])

    def test_pick_chapter_for_all_and_the_common_chapter(self):
        st = self.stages("depthcrafter", "splatting", "inpainting", "merging")
        self.go(self.film)
        self.assertEqual(self.shell.commonChapter, "")
        st["splatting"].pickChapter(self.key(1))
        self.assertEqual(self.shell.commonChapter, "Mixed")
        self.shell.pickChapterForAll(self.key(2))
        self.assertTrue(all(s._chapter_num == self.key(2) for s in st.values()))
        self.assertEqual(self.shell.commonChapter, "Chapter 2")

    def test_use_the_projects_folder(self):
        st = self.stages("splatting", "inpainting")
        self.go(self.film)
        st["splatting"].pickChapter(self.key(2))
        ip = st["inpainting"]
        ip.pickChapter(-1)
        ip._map.insert("ip_out", fwd(self.lacasa / "inpaint"))
        self.assertEqual(self.shell.foreignProject(ip.value("output")), "LaCasa")
        project_memory.reland(ip)
        self.assertEqual(ip._chapter_num, self.key(2))                     # Splatting's
        self.assertEqual(self.shell.foreignProject(ip.value("output")), "")


class ImportTests(_Base):
    def inpainting(self):
        from studio.stages.inpainting import controller as C
        return C.InpaintingController(self.shell)

    def test_import_from_splatting_fills_the_whole_stage_in_the_project(self):
        other = fwd(self.film / "MF Splats" / "Chapter 1")
        jh.record_job("splatting", {"hires": other + "/hires", "lowres": other + "/lowres", "splat_out": other})
        self.go(self.lacasa)
        ctl = self.inpainting()
        ctl.refreshImport()
        desc = ctl.property("importDescription")
        self.assertIn(fwd(self.lacasa / "splat" / "lowres"), desc)
        self.assertIn(fwd(self.lacasa / "inpaint"), desc)                  # the output too
        self.assertNotIn("My Film", desc)
        ctl._map.insert("output_folder", fwd(self.film / "MF Inpainted" / "Chapter 1"))
        ctl.importFromSplatting()
        self.assertEqual(fwd(ctl._map.value("input_folder")), fwd(self.lacasa / "splat" / "lowres"))
        self.assertEqual(fwd(ctl._map.value("output_folder")), fwd(self.lacasa / "inpaint"))
        self.assertEqual(ctl._chapter_num, WHOLE)

    def test_the_record_picks_the_chapter_in_this_project(self):
        sp2 = fwd(self.film / "MF Splats" / "Chapter 2")
        jh.record_job("splatting", {"hires": sp2 + "/hires", "lowres": sp2 + "/lowres", "splat_out": sp2})
        self.go(self.film)
        self.shell.setStageChapter("splatting", {"num": self.key(1), "name": "Chapter 1"})
        ctl = self.inpainting()
        ctl.importFromSplatting()
        self.assertEqual(fwd(ctl._map.value("output_folder")), fwd(self.film / "MF Inpainted" / "Chapter 2"))

    def test_splattings_import_names_the_tab_that_made_the_maps(self):
        from studio.stages.splatting import controller as C
        (self.lacasa / "depth" / "shot_depth.mp4").write_bytes(b"x")
        jh.record_job("depthcrafter", {"source": fwd(self.lacasa / "input"), "depth": fwd(self.lacasa / "depth"),
                                       "_origin": "dvd"})
        self.go(self.lacasa)
        ctl = C.SplattingController(self.shell)
        ctl.refreshImport()
        self.assertEqual(ctl.property("importOrigin"), "DVD")
        self.assertIn(fwd(self.lacasa / "splat"), ctl.property("importDescription"))      # its output too
        other = fwd(self.film / "MF DepthMaps" / "Chapter 1")
        jh.record_job("depthcrafter", {"source": other, "depth": other, "_origin": "vda"})
        ctl.refreshImport()                     # another project's job: the chapter found on disk instead
        self.assertEqual(ctl.property("importOrigin"), "DepthCrafter")
        self.assertIn(fwd(self.lacasa / "depth"), ctl.property("importDescription"))

    def test_merging_without_a_project_keeps_to_the_inpainted_folders_project(self):
        """A flat project's inpainted folder whose record carries another project's source and hires."""
        from studio.stages.merging import controller as C
        from studio.tests.test_splatting import _Shell
        film1 = fwd(self.film / "MF Splats" / "Chapter 1")
        record = {"inpainted_out": fwd(self.lacasa / "inpaint"),
                  "source": fwd(self.film / "MF Source Clips" / "Chapter 1"),
                  "hires": film1 + "/hires"}
        ctl = C.MergingController(_Shell())
        ctl._import_record(record, "Inpainting")
        self.assertEqual(fwd(ctl._map.value("original_folder")), fwd(self.lacasa / "input"))
        self.assertEqual(fwd(ctl._map.value("mask_folder")), fwd(self.lacasa / "splat" / "hires"))
        self.assertEqual(fwd(ctl._map.value("output_folder")), fwd(self.lacasa / "merged"))

    def test_merging_imports_the_whole_stage(self):
        from studio.stages.merging import controller as C
        jh.record_job("inpainting", {"inpainted_out": fwd(self.film / "MF Inpainted" / "Chapter 2"),
                                     "hires": fwd(self.film / "MF Splats" / "Chapter 2" / "hires")})
        self.go(self.lacasa)
        ctl = C.MergingController(self.shell)
        ctl.refreshImport()
        self.assertIn(fwd(self.lacasa / "merged"), ctl.property("importDescription"))   # not My Film's job
        ctl.importFromInpainting()
        for key, want in (("inpainted_folder", "inpaint"), ("original_folder", "input"),
                          ("mask_folder", "splat/hires"), ("output_folder", "merged")):
            self.assertEqual(fwd(ctl._map.value(key)), fwd(self.lacasa / want), key)


if __name__ == "__main__":
    unittest.main()
