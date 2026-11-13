"""Inpainting V2, Blend and the Depth Map Editor follow the open project (2026-11-12: they were the three
pages left out of the project / chapter overhaul). V2 and Blend land like every stage -- V2 beside
Inpainting (its input the chapter's Splats/hires, its output the chapter's Inpainted folder), Blend beside
DepthCrafter (the chapter's Depth Maps folder) -- but as side pages: no stage follows them, the title bar's
chapter chip leaves them out of "Mixed", and the switch's one-line summary names them only when they need
a chapter. The Depth Map Editor never re-points a loaded clip: its Pick From Last DepthCrafter Job skips
another project's job, its dialogs start in the open project's folders, and its "In <other>" tag clears
fields only while no clip is loaded. Real Shell (settings redirected), temp folders only.

    python -m unittest studio.tests.test_side_pages_project
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.tests.test_project_landing import WHOLE, Stage, _Base, fwd  # noqa: E402
from core.ui import chapters, job_handoff as jh  # noqa: E402
from studio import chapter_paths, project_memory  # noqa: E402


def _v2(shell):
    from studio.stages.inpainting_v2 import controller as C
    return C.InpaintingV2Controller(shell)


def _blend(shell):
    from studio.stages.blend import controller as C
    return C.BlendController(shell)


def _toasts(ctl):
    out = []
    ctl.toast.connect(lambda *a: out.append(a))
    return out


def _edit(ctl, key, value):
    """A field typed in on the page: the map changes, then its valueChanged."""
    ctl._map.insert(key, value)
    ctl._on_value_changed(key, value)


class PathsTests(unittest.TestCase):
    def test_the_side_pages_have_their_chapter_folders(self):
        import tempfile, shutil
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        from studio.tests.test_project_landing import chaptered
        film = chaptered(root, "My Film", "MF")
        v2 = chapters.stage_paths("inpainting_v2", fwd(film), 1)
        self.assertEqual(fwd(v2["hires"]), fwd(film / "MF Splats" / "Chapter 1" / "hires"))
        self.assertEqual(fwd(v2["output"]), fwd(film / "MF Inpainted" / "Chapter 1"))
        self.assertEqual(set(v2), {"hires", "output"})
        bl = chapters.stage_paths("blend", fwd(film), 1)
        self.assertEqual({k: fwd(v) for k, v in bl.items()}, {"depth": fwd(film / "MF DepthMaps" / "Chapter 1")})
        self.assertEqual(chapter_paths.INPUT_ROLES.get("inpainting_v2"), ("hires",))
        self.assertEqual(chapter_paths.INPUT_ROLES.get("blend"), ("depth",))
        self.assertEqual(project_memory.label("inpainting_v2"), "Inpainting V2")
        self.assertEqual(project_memory.label("blend"), "Blend")


class InpaintingV2Tests(_Base):
    def test_it_lands_beside_inpainting(self):
        st = self.stages("splatting", "inpainting")
        v2 = _v2(self.shell)
        self.go(self.film)
        st["inpainting"].pickChapter(self.key(2))
        self.go(self.lacasa)
        self.assertEqual(v2._chapter_num, WHOLE)                  # LaCasa's one entry
        self.assertEqual(fwd(v2._map.value("input_folder")), fwd(self.lacasa / "splat" / "hires"))
        self.assertEqual(fwd(v2._map.value("output_folder")), fwd(self.lacasa / "inpaint"))
        self.go(self.film)                                        # V2 had nothing of its own here
        self.assertEqual(v2._chapter_num, self.key(2))            # Inpainting's
        self.assertEqual(fwd(v2._map.value("input_folder")), fwd(self.film / "MF Splats" / "Chapter 2" / "hires"))
        self.assertEqual(fwd(v2._map.value("output_folder")), fwd(self.film / "MF Inpainted" / "Chapter 2"))
        self.assertEqual(self.shell.projectFieldKeys(v2), ["input_folder", "output_folder"])

    def test_no_chapter_refuses_start(self):
        v2 = _v2(self.shell)
        toasts = _toasts(v2)
        self.go(self.film)                                        # two chapters, nothing to follow
        self.assertTrue(v2.property("needsChapter"))
        self.assertEqual(v2._map.value("input_folder"), "")
        v2.start()
        self.assertEqual(toasts[-1][1], "Pick a chapter first")
        self.assertFalse(v2.property("busy"))
        v2.pickChapter(self.key(1))
        self.assertFalse(v2.property("needsChapter"))

    def test_a_hand_edit_leaves_the_chapter(self):
        v2 = _v2(self.shell)
        self.go(self.film)
        v2.pickChapter(self.key(1))
        _edit(v2, "output_folder", fwd(self.root / "scratch"))
        self.assertEqual(v2._chapter_num, -1)
        self.assertEqual(self.shell.stageChapter("inpainting_v2"), {})
        project_memory.reland(v2)                                 # the "In <other>" tag's click
        self.assertEqual(v2._chapter_num, -1)                     # nothing to land on in a 2-chapter film
        v2.pickChapter(self.key(2))
        v2.useProjectFolders()
        self.assertEqual(v2._chapter_num, self.key(2))

    def test_merging_still_imports_from_v2(self):
        from studio.stages.merging import controller as C
        own = self.film / "MF Inpainted" / "Chapter 2" / "v2"
        own.mkdir()
        jh.record_job("inpainting_v2", {"inpainted_out": fwd(own),
                                        "hires": fwd(self.film / "MF Splats" / "Chapter 2" / "hires")})
        self.go(self.film)
        ctl = C.MergingController(self.shell)
        ctl.refreshImport()
        self.assertTrue(ctl.property("importV2Description"))
        ctl.importFromInpaintingV2()
        self.assertEqual(fwd(ctl._map.value("inpainted_folder")), fwd(own))
        self.assertEqual(fwd(ctl._map.value("output_folder")), fwd(self.film / "MF Merged" / "Chapter 2"))


class BlendTests(_Base):
    def test_it_lands_beside_depthcrafter(self):
        st = self.stages("depthcrafter", "splatting")
        bl = _blend(self.shell)
        self.go(self.film)
        st["depthcrafter"].pickChapter(self.key(2))
        st["splatting"].pickChapter(self.key(1))
        self.go(self.lacasa)
        self.assertEqual(fwd(bl._map.value("depth_folder")), fwd(self.lacasa / "depth"))
        self.go(self.film)                                        # Blend had nothing of its own here
        self.assertEqual(bl._chapter_num, self.key(2))            # DepthCrafter's, beside it
        self.assertEqual(fwd(bl._map.value("depth_folder")), fwd(self.film / "MF DepthMaps" / "Chapter 2"))
        self.assertEqual(fwd(bl._global["depth_folder"]), fwd(self.film / "MF DepthMaps" / "Chapter 2"))
        self.assertEqual(self.shell.projectFieldKeys(bl), ["depth_folder"])

    def test_no_chapter_refuses_start(self):
        bl = _blend(self.shell)
        toasts = _toasts(bl)
        self.go(self.film)
        self.assertTrue(bl.property("needsChapter"))
        bl.start()
        self.assertEqual(toasts[-1][1], "Pick a chapter first")
        bl.pickChapter(self.key(1))
        self.assertFalse(bl.property("needsChapter"))

    def test_a_hand_edit_leaves_the_chapter(self):
        bl = _blend(self.shell)
        self.go(self.film)
        bl.pickChapter(self.key(1))
        _edit(bl, "depth_folder", fwd(self.root / "scratch"))
        self.assertEqual(bl._chapter_num, -1)


class SidePagesTests(_Base):
    def test_the_chip_leaves_the_side_pages_out(self):
        st = self.stages("depthcrafter", "splatting", "inpainting", "merging")
        v2, bl = _v2(self.shell), _blend(self.shell)
        self.go(self.film)                                        # nobody on a chapter yet
        for s in st.values():
            s.pickChapter(self.key(1))
        self.assertEqual(v2._chapter_num, -1)                     # never opened: still needs one
        self.assertEqual(self.shell.commonChapter, "Chapter 1")   # not "Mixed"
        self.shell.pickChapterForAll(self.key(2))
        self.assertEqual((v2._chapter_num, bl._chapter_num), (self.key(2), self.key(2)))
        v2.pickChapter(self.key(1))
        self.assertEqual(self.shell.commonChapter, "Chapter 2")

    def test_no_stage_follows_a_side_page(self):
        st = self.stages("splatting", "inpainting", "depthcrafter")
        self.go(self.lacasa)
        mem = self.shell._project_memory(fwd(self.film))
        mem["stage_chapters"] = {"splatting": {"num": 1, "name": "Chapter 1"},
                                 "inpainting_v2": {"num": 2, "name": "Chapter 2"},
                                 "blend": {"num": 2, "name": "Chapter 2"}}
        self.go(self.film)
        self.assertEqual(st["inpainting"]._chapter_num, self.key(1))      # Splatting's, not V2's
        self.assertEqual(st["depthcrafter"]._chapter_num, self.key(1))    # not Blend's

    def test_the_summary_names_side_pages_only_when_they_need_a_chapter(self):
        notes = [("depthcrafter", "chapter", "Chapter 1"), ("blend", "followed", "DepthCrafter: Chapter 1"),
                 ("splatting", "chapter", "Chapter 1"), ("inpainting", "followed", "Splatting: Chapter 1"),
                 ("inpainting_v2", "followed", "Inpainting: Chapter 1"), ("merging", "folders", "")]
        self.assertEqual(project_memory.summary("Film", notes),
                         "Film: 2 stages on Chapter 1, Inpainting following Splatting: Chapter 1, "
                         "Merging kept its own folders")
        self.assertEqual(project_memory.summary("Film", [("inpainting_v2", "only", "Whole project"),
                                                         ("blend", "chapter", "Chapter 1")]), "")
        self.assertEqual(project_memory.summary("Film", [("splatting", "needs", ""), ("inpainting_v2", "needs", ""),
                                                         ("blend", "needs", "")]),
                         "Film: Blend, Splatting and Inpainting V2 need a chapter")


class DepthEditTests(_Base):
    def _engine(self):
        from studio.legacy import install
        install()
        from studio.stages.depthedit import engine as E
        return E.DepthEditEngine

    def test_pick_from_depthcrafter_skips_another_projects_job(self):
        E = self._engine()
        src = fwd(self.film / "MF Source Clips" / "Chapter 1")
        jh.record_job("depthcrafter", {"source": src, "depth": fwd(self.film / "MF DepthMaps" / "Chapter 1")})
        fake = SimpleNamespace()
        self.assertEqual(E._depthcrafter_record(fake).get("source"), src)       # no provider: as before
        project = {"path": fwd(self.lacasa)}
        jh.set_project_provider(lambda: project["path"])
        self.addCleanup(jh.set_project_provider, None)
        self.assertEqual(jh.current_project(), fwd(self.lacasa))
        self.assertEqual(E._depthcrafter_record(fake), {})                       # My Film's job, LaCasa open
        project["path"] = fwd(self.film)
        self.assertEqual(E._depthcrafter_record(fake).get("source"), src)
        project["path"] = ""
        self.assertEqual(E._depthcrafter_record(fake).get("source"), src)       # no project open

    def _editor(self):
        from studio.stages.depthedit import controller as C
        ctl = C.DepthEditController(self.shell)
        vars_ = {n: SimpleNamespace(value="") for n in ("rgb_path", "depth_path", "output_dir")}
        for v in vars_.values():
            v.get = (lambda v=v: v.value)
            v.set = (lambda x, v=v: setattr(v, "value", x))
        eng = SimpleNamespace(codes=None, project=None, _save_settings=lambda: None,
                              **{n + "_var": v for n, v in vars_.items()})
        ctl._engine = eng
        ctl._act = lambda fn: fn()
        return ctl, eng

    def test_dialogs_start_in_the_projects_folders(self):
        ctl, eng = self._editor()
        self.go(self.film)
        sp = Stage(self.shell, "splatting")
        sp.pickChapter(self.key(2))
        src = fwd(self.film / "MF Source Clips" / "Chapter 2")
        depth = fwd(self.film / "MF DepthMaps" / "Chapter 2")
        self.assertEqual(fwd(ctl.dialogFolder("rgb_path")), src)
        self.assertEqual(fwd(ctl.dialogFolder("depth_path")), depth)
        self.assertEqual(fwd(ctl.dialogFolder("output_dir")), depth)
        eng.depth_path_var.set(fwd(self.lacasa / "depth" / "shot_depth.mp4"))       # another project's
        self.assertEqual(fwd(ctl.dialogFolder("depth_path")), depth)
        mine = fwd(self.film / "MF DepthMaps" / "Chapter 1" / "a_depth.mp4")
        eng.depth_path_var.set(mine)                                               # this project's: kept
        self.assertEqual(fwd(ctl.dialogFolder("depth_path")), mine)
        self.assertEqual(eng.rgb_path_var.get(), "")                               # nothing filled in

    def test_the_tag_never_repoints_a_loaded_clip(self):
        ctl, eng = self._editor()
        toasts = _toasts(ctl)
        self.go(self.film)
        other = fwd(self.lacasa / "depth" / "shot_depth.mp4")
        eng.depth_path_var.set(other)
        eng.rgb_path_var.set(fwd(self.lacasa / "input" / "shot.mp4"))
        eng.codes = object()                                                       # a clip is loaded
        ctl.useProjectFolders()
        self.assertEqual(eng.depth_path_var.get(), other)
        self.assertEqual(toasts[-1][0], "warning")
        self.assertIn("LaCasa", toasts[-1][2])
        eng.codes = None                                                           # no clip loaded
        mine = fwd(self.root / "scratch" / "out")
        eng.output_dir_var.set(mine)
        ctl.useProjectFolders()
        self.assertEqual((eng.depth_path_var.get(), eng.rgb_path_var.get()), ("", ""))
        self.assertEqual(eng.output_dir_var.get(), mine)                          # nobody's folder: kept


if __name__ == "__main__":
    unittest.main()
