"""Flat projects (user, 2026-11-12): a tester's LaCasa/{input,depth,splat,inpaint,merged} with the clips
straight in the stage folders -- no "Chapter N" folders -- got no chapter list, so no stage followed the
project. Now a flat unit has a "Whole project" entry (the stage folders themselves), more stage-folder
names are known without one stealing another's folder, "_" folders are never stage folders, a unit can
name its own folders (.studio_project.json, or Studio's settings when the unit can't be written), the
job handoff rebases flat units, and DVD / VDA say they made the maps. Temp folders only; Studio's
settings and the job record redirected.

    python -m unittest studio.tests.test_flat_projects
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.ui import chapters, job_handoff as jh  # noqa: E402

TESTER = ("input", "depth", "splat", "inpaint", "merged")
WHOLE = getattr(chapters, 'WHOLE', 99999)      # (getattr: the test also runs, red, on the code before)
WHOLE_NAME = getattr(chapters, 'WHOLE_NAME', 'Whole project')
CAPS = ("Source", "Depth", "Splat", "Inpaint", "Merged")


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def flat(root: Path, name: str, names=TESTER, clips=True) -> Path:
    base = root / name
    for n in names:
        (base / n).mkdir(parents=True)
    (base / names[2] / "hires" / "finished").mkdir(parents=True)
    (base / names[2] / "lowres").mkdir(parents=True)
    if clips:
        (base / names[0] / "shot_001.mp4").write_bytes(b"x")
    return base


def chaptered(root: Path, name: str, prefix: str, chapter: str = "Chapter 6 - Six") -> Path:
    base = root / name
    for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
        (base / f"{prefix} {st}" / chapter).mkdir(parents=True)
    (base / f"{prefix} Splats" / chapter / "hires").mkdir()
    (base / f"{prefix} Splats" / chapter / "lowres").mkdir()
    return base


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(lambda: getattr(chapters, 'set_extra_overrides', lambda m: None)({}))


class FlatLayoutTests(_Tmp):
    def check_layout(self, names):
        base = flat(self.root, "LaCasa", names)
        b = fwd(base)
        dirs = chapters.find_stage_dirs(b)
        self.assertEqual(dirs, {r: f"{b}/{n}" for r, n in zip(chapters.ROLES, names)})
        self.assertEqual(chapters.list_entries(b), [(WHOLE, WHOLE_NAME)])
        self.assertEqual(chapters.stage_paths("depthcrafter", b, WHOLE),
                         {"input": f"{b}/{names[0]}", "output": f"{b}/{names[1]}"})
        self.assertEqual(chapters.stage_paths("inpainting", b, WHOLE),
                         {"lowres": f"{b}/{names[2]}/lowres", "hires": f"{b}/{names[2]}/hires",
                          "output": f"{b}/{names[3]}"})
        m = chapters.stage_paths("merging", b, WHOLE)
        self.assertEqual(m["mask"], f"{b}/{names[2]}/hires/finished")
        self.assertEqual(m["output"], f"{b}/{names[4]}")
        for sub in (names[1], f"{names[2]}/hires/finished", f"{names[2]}/lowres", names[4]):
            self.assertEqual(chapters.locate_chapter(f"{b}/{sub}"), (b, WHOLE, ""), sub)

    def test_the_testers_layout(self):
        self.check_layout(TESTER)

    def test_capitalised_short_names(self):
        self.check_layout(CAPS)

    def test_chaptered_projects_locate_as_before(self):
        sm = chaptered(self.root, "My Film", "SM")
        at = chapters.locate_chapter(fwd(sm / "SM Splats" / "Chapter 6 - Six" / "hires"))
        self.assertEqual(at, (fwd(sm), 6, "Chapter 6 - Six"))
        self.assertEqual(chapters.list_entries(fwd(sm)), [(6, "Chapter 6 - Six")])   # no Whole entry

    def test_clips_straight_in_a_chaptered_source_add_the_whole_entry(self):
        sm = chaptered(self.root, "My Film", "SM")
        (sm / "SM Source Clips" / "loose.mp4").write_bytes(b"x")
        self.assertEqual(chapters.list_entries(fwd(sm)),
                         [(WHOLE, WHOLE_NAME), (6, "Chapter 6 - Six")])

    def test_a_folder_beside_the_stage_folders_is_not_located(self):
        base = flat(self.root, "LaCasa")
        (base / "notes").mkdir()
        self.assertIsNone(chapters.locate_chapter(fwd(base / "notes")))
        self.assertIsNone(chapters.locate_chapter(fwd(base / "input" / "extras")))


class NamesTests(_Tmp):
    def names(self, *folders) -> dict:
        base = self.root / "P"
        for f in folders:
            (base / f).mkdir(parents=True)
        return {r: p.rsplit("/", 1)[-1] for r, p in chapters.find_stage_dirs(fwd(base)).items()}

    def test_underscore_and_dot_folders_are_never_stage_folders(self):
        got = self.names("SM Source Clips", "SM Splats", "_inpaint_resume", ".depth_cache")
        self.assertNotIn("inpainted", got)
        self.assertNotIn("depth", got)

    def test_clips_does_not_steal_the_lr_folder(self):
        got = self.names("SM Source Clips", "SM DepthMaps", "SM Converted LR Clips")
        self.assertEqual(got["source"], "SM Source Clips")
        self.assertEqual(got["merged"], "SM Converted LR Clips")
        got = self.names_fresh("SM DepthMaps", "SM Splats", "SM Converted LR Clips")
        self.assertNotIn("source", got)
        self.assertEqual(got["merged"], "SM Converted LR Clips")

    def names_fresh(self, *folders) -> dict:
        shutil.rmtree(self.root / "P", True)
        return self.names(*folders)

    def test_aliases(self):
        got = self.names("Original", "Depth Maps", "Splats", "Inpainted", "Final")
        self.assertEqual((got["source"], got["merged"]), ("Original", "Final"))
        got = self.names_fresh("Clips", "Depth", "Splat", "Inpaint", "Output")
        self.assertEqual((got["source"], got["merged"]), ("Clips", "Output"))
        got = self.names_fresh("Merged Clips", "Input", "Depth")     # a Merged folder is not Source
        self.assertEqual((got["source"], got["merged"]), ("Input", "Merged Clips"))

    def test_a_brand_new_project_gets_the_default_names(self):
        (self.root / "New").mkdir()
        b = fwd(self.root / "New")
        self.assertEqual(chapters.find_units(b), [])
        self.assertEqual(chapters.list_entries(b), [(WHOLE, WHOLE_NAME)])
        self.assertEqual(chapters.stage_paths("depthcrafter", b, WHOLE),
                         {"input": f"{b}/Source Clips", "output": f"{b}/DepthMaps"})
        self.assertFalse((self.root / "New" / "Source Clips").exists())       # nothing made


class OverrideTests(_Tmp):
    def test_the_units_own_file_wins(self):
        base = flat(self.root, "LaCasa")
        (base / "my maps").mkdir()
        (base / chapters.PROJECT_FILE).write_text(json.dumps({"folders": {"depth": "my maps",
                                                                           "merged": "not yet"}}))
        b = fwd(base)
        dirs = chapters.find_stage_dirs(b)
        self.assertEqual(dirs["depth"], f"{b}/my maps")
        self.assertNotIn("merged", dirs)                     # chosen but not made yet
        self.assertEqual(chapters.stage_dirs(b)["merged"], f"{b}/not yet")
        self.assertEqual(chapters.locate_chapter(f"{b}/my maps"), (b, WHOLE, ""))

    def test_written_relative_inside_absolute_outside(self):
        base = flat(self.root, "LaCasa")
        outside = self.root / "scratch" / "depth"
        self.assertTrue(chapters.write_override_file(fwd(base), {"depth": fwd(base / "deep"),
                                                                 "merged": fwd(outside)}))
        stored = json.loads((base / chapters.PROJECT_FILE).read_text())["folders"]
        self.assertEqual(stored["depth"], "deep")
        self.assertEqual(Path(stored["merged"]), outside)
        self.assertTrue(chapters.write_override_file(fwd(base), {}))
        self.assertFalse((base / chapters.PROJECT_FILE).exists())

    def test_a_broken_file_is_ignored(self):
        base = flat(self.root, "LaCasa")
        (base / chapters.PROJECT_FILE).write_text("{not json")
        self.assertEqual(chapters.find_stage_dirs(fwd(base))["depth"], fwd(base / "depth"))

    def test_the_registry_wins_over_the_file(self):
        base = flat(self.root, "LaCasa")
        (base / "elsewhere").mkdir()
        chapters.set_extra_overrides({fwd(base): {"depth": "elsewhere"}})
        self.assertEqual(chapters.find_stage_dirs(fwd(base))["depth"], fwd(base / "elsewhere"))


class HandoffTests(_Tmp):
    def setUp(self):
        super().setUp()
        saved = jh._RECORD_PATH
        jh._RECORD_PATH = str(self.root / "last_jobs.json")
        self.addCleanup(setattr, jh, "_RECORD_PATH", saved)

    def rec(self, stage):
        return json.loads(Path(jh._RECORD_PATH).read_text())[stage]

    def test_rebase_in_a_flat_unit(self):
        a, b = flat(self.root, "Other"), flat(self.root, "LaCasa")
        jh.record_job("splatting", {"source": fwd(a / "input"), "depth": fwd(a / "depth"),
                                    "splat_out": fwd(a / "splat"), "hires": fwd(a / "splat/hires")})
        jh.record_job("inpainting", {"hires": fwd(b / "splat/hires"), "lowres": fwd(b / "splat/lowres"),
                                     "inpainted_out": fwd(b / "inpaint")})
        rec = self.rec("inpainting")
        self.assertEqual(rec["source"], fwd(b / "input"))
        self.assertEqual(rec["depth"], fwd(b / "depth"))
        self.assertEqual(rec["splat_out"], fwd(b / "splat"))
        self.assertNotIn("Other", json.dumps(rec))

    def test_dvd_and_vda_name_themselves(self):
        jh.record_job("depthcrafter", {"source": "D:/s", "depth": "D:/d", "_origin": "dvd"})
        self.assertEqual(jh.previous_stage_label("splatting"), "DVD")
        jh.record_job("depthcrafter", {"source": "D:/s", "depth": "D:/d", "_origin": "vda"})
        self.assertEqual(jh.previous_stage_label("splatting"), "VDA")
        jh.record_job("splatting", {"source": "D:/s", "hires": "D:/h"})
        self.assertNotIn(jh.ORIGIN_KEY, self.rec("splatting"))          # not carried downstream
        self.assertEqual(jh.previous_stage_label("inpainting"), "Splatting")
        jh.record_job("depthedit", {"source": "D:/s", "depth": "D:/e"})
        self.assertEqual(jh.previous_stage_label("splatting"), "Depth Map Editor")
        jh.record_job("depthcrafter", {"source": "D:/s", "depth": "D:/d"})
        self.assertNotIn(jh.ORIGIN_KEY, self.rec("depthcrafter"))
        self.assertEqual(jh.previous_stage_label("splatting"), "DepthCrafter")

    def test_the_dvd_and_vda_tabs_record_their_origin(self):
        from studio.legacy import install
        install()
        from studio.tests._qt import app
        app()
        from studio.tests.test_splatting import _Shell
        from studio.stages.dvd import controller as D
        from studio.stages.vda import controller as V
        for mod, origin in ((D, "dvd"), (V, "vda")):
            ctl = getattr(mod, "DvdController" if mod is D else "VdaController")(_Shell())
            ctl._record_job_handoff({"input_dir_or_file_var": "D:/s", "output_dir": "D:/d"})
            self.assertEqual(self.rec("depthcrafter").get(jh.ORIGIN_KEY), origin)


class ShellTests(_Tmp):
    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app
        cls.app = app()

    def setUp(self):
        super().setUp()
        import studio.settings_store as ss
        saved = ss.CONFIG_DIR
        ss.CONFIG_DIR = self.root / "config"
        self.addCleanup(setattr, ss, "CONFIG_DIR", saved)
        from studio.shell import StudioShell
        self.shell = StudioShell()

    def test_a_flat_project_lists_its_whole_project(self):
        base = flat(self.root, "LaCasa")
        self.shell.projectFolder = fwd(base)
        self.assertEqual(self.shell.listChapters(), [{"num": WHOLE, "name": "Whole project"}])
        p = self.shell.stagePaths("splatting", WHOLE, "Whole project")
        self.assertEqual(Path(p["depth"]), base / "depth")
        self.assertEqual(Path(p["output"]), base / "splat")
        self.assertEqual(Path(p["source"]), base / "input")       # its clips are there
        self.assertEqual(self.shell.chapterAt(fwd(base / "splat" / "hires")), WHOLE)

    def test_a_show_with_a_flat_episode(self):
        show = self.root / "My Show"
        flat(show / "Season 1", "Episode 1", ("1. S1E1 Source", "2. S1E1 Depth", "3. S1E1 Splat",
                                              "4. S1E1 Inpaint", "5. S1E1 Merged"))
        (show / "Season 1" / "Episode 2" / "S1E2 Source Clips" / "Chapter 1").mkdir(parents=True)
        self.shell.projectFolder = fwd(show)
        names = [e["name"] for e in self.shell.listChapters()]
        self.assertEqual(names, ["S1E1 · Whole episode", "S1E2 · Chapter 1"])

    def test_an_empty_project_is_one_flat_unit(self):
        (self.root / "Brand New").mkdir()
        self.shell.projectFolder = fwd(self.root / "Brand New")
        self.assertTrue(self.shell.projectEmpty)
        self.assertEqual(self.shell.listChapters(), [{"num": WHOLE, "name": "Whole project"}])
        self.assertTrue(self.shell.stagePaths("scenecut", -1, "Whole Movie").get("output"))
        self.assertTrue(self.shell.createProjectFolders(""))
        self.assertTrue((self.root / "Brand New" / "Source Clips").is_dir())
        self.assertFalse(self.shell.projectEmpty)

    def test_switching_from_a_show_forgets_its_episodes(self):
        show = self.root / "My Show"
        for ep in (1, 2):
            (show / "Season 1" / f"Episode {ep}" / f"S1E{ep} Source Clips" / "Chapter 1").mkdir(parents=True)
        movie = chaptered(self.root, "My Film", "SM")
        self.shell.projectFolder = fwd(show)
        self.shell.listChapters()
        self.assertEqual(self.shell.stagePaths("scenecut", -1, "Whole Movie"), {})   # a show: none
        self.shell.projectFolder = fwd(movie)
        # the movie's whole-movie folder, not the show's units still in the table
        self.assertEqual(Path(self.shell.stagePaths("scenecut", -1, "Whole Movie")["output"]),
                         movie / "SM Source Clips")

    def test_project_folders_panel(self):
        base = flat(self.root, "LaCasa")
        self.shell.projectFolder = fwd(base)
        rows = {r["role"]: r for r in self.shell.projectFolders("")}
        self.assertEqual(rows["source"]["clips"], 1)
        self.assertTrue(rows["depth"]["exists"])
        self.assertFalse(rows["depth"]["overridden"])
        (base / "maps").mkdir()
        self.assertTrue(self.shell.setUnitFolder("", "depth", fwd(base / "maps")))
        self.assertTrue((base / chapters.PROJECT_FILE).is_file())
        self.assertEqual(Path(self.shell.stagePaths("depthcrafter", WHOLE, "")["output"]), base / "maps")
        self.assertTrue(self.shell.clearUnitFolder("", "depth"))
        self.assertEqual(Path(self.shell.stagePaths("depthcrafter", WHOLE, "")["output"]), base / "depth")

    def test_an_unwritable_unit_keeps_its_choice_in_studios_settings(self):
        base = flat(self.root, "LaCasa")
        self.shell.projectFolder = fwd(base)
        (base / "maps").mkdir()
        with mock.patch.object(chapters, "write_override_file", return_value=False):
            self.assertTrue(self.shell.setUnitFolder("", "depth", fwd(base / "maps")))
        self.assertFalse((base / chapters.PROJECT_FILE).exists())
        mem = self.shell._data["project_memory"][self.shell._project_key(fwd(base))]
        self.assertEqual(mem["folders"][""], {"depth": "maps"})
        self.assertEqual(Path(self.shell.stagePaths("depthcrafter", WHOLE, "")["output"]), base / "maps")
        self.assertTrue(self.shell.projectFolders("")[1]["overridden"])

    def test_foreign_project(self):
        root = self.root / "Projects"
        a, b = flat(root, "LaCasa"), chaptered(root, "My Film", "SM")
        self.shell.projectsRoot = fwd(root)
        self.shell.projectFolder = fwd(a)
        self.assertEqual(self.shell.foreignProject(fwd(b / "SM Splats" / "Chapter 6 - Six")), "My Film")
        self.assertEqual(self.shell.foreignProject(fwd(a / "depth")), "")
        self.assertEqual(self.shell.foreignProject(fwd(self.root / "scratch")), "")
        self.assertEqual(self.shell.foreignProject(""), "")


if __name__ == "__main__":
    unittest.main()
