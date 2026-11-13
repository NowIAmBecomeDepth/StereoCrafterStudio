"""The project list's Add Project tile and remove button, and Project Folders for any project
(user, 2026-11-12): a folder added on its own is remembered in studio.json ("added_projects") and
listed after a restart, in its own tab, once; a missing one is still listed (marked); removing a
tile only takes it off the list ("hidden_projects" for one found in the Projects Folder) and an
add brings it back; the panel's Shell slots take a project path, a change to a project that is
not open leaves the open one's stages alone, and the choices Studio keeps for an unwritable unit
go under that project's own key. Temp folders only; Studio's settings and the job record
redirected.

    python -m unittest studio.tests.test_added_projects
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.ui import chapters  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "last_jobs.json"


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def _hash():
    return hashlib.sha256(RECORD.read_bytes()).hexdigest() if RECORD.exists() else None


def movie(folder: Path) -> Path:
    (folder / "SM Source Clips" / "Chapter 1").mkdir(parents=True)
    (folder / "SM DepthMaps" / "Chapter 1").mkdir(parents=True)
    return folder


def show(folder: Path) -> Path:
    for ep in (1, 2):
        (folder / "Season 1" / f"Episode {ep}" / f"S1E{ep} Source Clips" / "Chapter 1").mkdir(parents=True)
    return folder


class AddedProjectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app
        cls.app = app()
        cls.h0 = _hash()

    @classmethod
    def tearDownClass(cls):
        assert _hash() == cls.h0, "last_jobs.json changed"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(lambda: chapters.set_extra_overrides({}))
        import studio.settings_store as ss
        import core.ui.job_handoff as jh
        saved, rec = ss.CONFIG_DIR, jh._RECORD_PATH
        ss.CONFIG_DIR = self.root / "config"
        jh._RECORD_PATH = str(self.root / "last_jobs.json")
        self.addCleanup(setattr, ss, "CONFIG_DIR", saved)
        self.addCleanup(setattr, jh, "_RECORD_PATH", rec)
        self.projects = self.root / "Projects"
        self.film = movie(self.projects / "My Film")
        self.other = movie(self.projects / "Other Film")
        self.elsewhere = movie(self.root / "Elsewhere" / "Spider-Man")
        self.shell = self.new_shell()
        self.shell.projectsRoot = fwd(self.projects)

    def new_shell(self):
        from studio.shell import StudioShell
        return StudioShell()

    def names(self, shell=None):
        return [p["name"] for p in (shell or self.shell).listProjects()]

    def entry(self, name, shell=None):
        return next(p for p in (shell or self.shell).listProjects() if p["name"] == name)

    def stored(self):
        return json.loads((self.root / "config" / "studio.json").read_text(encoding="utf-8"))

    # ---- adding ----------------------------------------------------------------------------

    def test_an_added_folder_is_listed_and_kept_after_a_restart(self):
        r = self.shell.addProject(str(self.elsewhere))          # backslashes, as a picker hands it
        self.assertEqual(r["path"], fwd(self.elsewhere))
        self.assertFalse(r["show"])
        self.assertEqual(self.names(), ["My Film", "Other Film", "Spider-Man"])
        e = self.entry("Spider-Man")
        self.assertTrue(e["added"])
        self.assertFalse(e["missing"])
        self.assertFalse(e["show"])
        self.assertEqual(self.stored()["added_projects"], [fwd(self.elsewhere)])
        again = self.new_shell()                                  # a restart
        self.assertIn("Spider-Man", self.names(again))

    def test_an_added_show_goes_to_tv_shows(self):
        s = show(self.root / "Elsewhere" / "My Show")
        r = self.shell.addProject(fwd(s))
        self.assertTrue(r["show"])
        self.assertTrue(self.entry("My Show")["show"])

    def test_adding_a_listed_folder_again_adds_no_duplicate(self):
        self.shell.addProject(fwd(self.elsewhere))
        r = self.shell.addProject(str(self.elsewhere).upper() + "\\")
        self.assertEqual(r["path"], fwd(self.elsewhere))         # the listed one's spelling
        self.assertEqual(self.names().count("Spider-Man"), 1)
        self.assertEqual(len(self.stored()["added_projects"]), 1)
        # one found in the Projects Folder: opened, never stored as added
        r = self.shell.addProject(str(self.film))
        self.assertEqual(r["path"], fwd(self.film))
        self.assertEqual(self.names().count("My Film"), 1)
        self.assertEqual(self.stored()["added_projects"], [fwd(self.elsewhere)])

    def test_a_missing_added_folder_is_still_listed(self):
        self.shell.addProject(fwd(self.elsewhere))
        shutil.rmtree(self.elsewhere)                              # the drive unplugged
        e = self.entry("Spider-Man")
        self.assertTrue(e["missing"])
        self.assertEqual(e["poster"], "")
        self.assertEqual(self.shell.addProject(fwd(self.root / "Nowhere")), {})   # not a folder: nothing

    def test_added_projects_are_known_projects(self):
        self.shell.addProject(fwd(self.elsewhere))
        self.shell.projectFolder = fwd(self.film)
        self.assertEqual(self.shell.foreignProject(fwd(self.elsewhere / "SM DepthMaps" / "Chapter 1")), "Spider-Man")

    # ---- removing ----------------------------------------------------------------------------

    def test_removing_an_added_project_drops_it(self):
        self.shell.addProject(fwd(self.elsewhere))
        self.assertTrue(self.shell.removeProject(fwd(self.elsewhere)))
        self.assertNotIn("Spider-Man", self.names())
        self.assertEqual(self.stored().get("added_projects"), [])
        self.assertTrue((self.elsewhere / "SM Source Clips" / "Chapter 1").is_dir())   # nothing touched

    def test_removing_a_found_project_hides_it_and_an_add_brings_it_back(self):
        self.assertTrue(self.shell.removeProject(str(self.other)))
        self.assertEqual(self.names(), ["My Film"])
        self.assertEqual(self.stored()["hidden_projects"], [fwd(self.other)])
        self.assertTrue(self.other.is_dir())
        self.assertNotIn("Other Film", self.names(self.new_shell()))   # kept hidden after a restart
        r = self.shell.addProject(fwd(self.other).lower())
        self.assertEqual(r["path"].lower(), fwd(self.other).lower())
        self.assertEqual(self.names(), ["My Film", "Other Film"])
        self.assertEqual(self.stored()["hidden_projects"], [])
        self.assertEqual(self.stored().get("added_projects", []), [])   # found there: not stored as added

    def test_removing_the_open_project_keeps_it_open(self):
        self.shell.projectFolder = fwd(self.film)
        self.assertTrue(self.shell.removeProject(fwd(self.film)))
        self.assertEqual(self.shell.projectFolder, fwd(self.film))
        self.assertNotIn("My Film", self.names())

    def test_the_list_says_when_it_changed(self):
        seen = []
        self.shell.projectListChanged.connect(lambda: seen.append(1))
        self.shell.addProject(fwd(self.elsewhere))
        self.shell.removeProject(fwd(self.elsewhere))
        self.assertEqual(len(seen), 2)

    # ---- Project Folders for any project -----------------------------------------------------

    def test_folders_of_a_project_that_is_not_open(self):
        self.shell.projectFolder = fwd(self.film)
        rows = {r["role"]: r for r in self.shell.projectFoldersFor(fwd(self.elsewhere), "")}
        self.assertEqual(Path(rows["source"]["path"]), self.elsewhere / "SM Source Clips")
        self.assertTrue(rows["depth"]["exists"])
        self.assertFalse(rows["splats"]["exists"])
        units = self.shell.projectUnitsFor(fwd(self.elsewhere))
        self.assertEqual(units, [{"unit": "", "label": "Spider-Man"}])
        s = show(self.root / "Elsewhere" / "My Show")
        self.assertEqual([u["label"] for u in self.shell.projectUnitsFor(fwd(s))], ["S1E1", "S1E2"])
        # the open project's own slots unchanged
        self.assertEqual(self.shell.projectUnits(), [{"unit": "", "label": "My Film"}])

    def test_a_change_to_another_project_leaves_the_open_ones_stages_alone(self):
        self.shell.projectFolder = fwd(self.film)
        refreshed, edited = [], []
        self.shell.projectRefreshed.connect(lambda: refreshed.append(1))
        self.shell.projectFoldersEdited.connect(lambda p: edited.append(p))
        maps = self.elsewhere / "maps"
        maps.mkdir()
        self.assertTrue(self.shell.setUnitFolderFor(fwd(self.elsewhere), "", "depth", fwd(maps)))
        self.assertTrue((self.elsewhere / chapters.PROJECT_FILE).is_file())
        self.assertFalse((self.film / chapters.PROJECT_FILE).exists())
        self.assertEqual(refreshed, [])
        self.assertEqual(edited, [fwd(self.elsewhere)])
        self.assertTrue(self.shell.projectFoldersFor(fwd(self.elsewhere), "")[1]["overridden"])
        self.assertFalse(self.shell.projectFolders("")[1]["overridden"])
        self.assertTrue(self.shell.clearUnitFolderFor(fwd(self.elsewhere), "", "depth"))
        self.assertEqual(refreshed, [])
        self.assertFalse(self.shell.projectFoldersFor(fwd(self.elsewhere), "")[1]["overridden"])
        # the open project's, through the same slot: the stages look again
        (self.film / "maps").mkdir()
        self.assertTrue(self.shell.setUnitFolderFor(str(self.film), "", "depth", fwd(self.film / "maps")))
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(Path(self.shell.stagePaths("depthcrafter", 1, "Chapter 1")["output"]),
                         self.film / "maps" / "Chapter 1")

    def test_an_unwritable_unit_of_another_project_keeps_its_choice_under_its_own_key(self):
        self.shell.projectFolder = fwd(self.film)
        (self.film / "fmaps").mkdir()
        with mock.patch.object(chapters, "write_override_file", return_value=False):
            self.assertTrue(self.shell.setUnitFolder("", "depth", fwd(self.film / "fmaps")))
            maps = self.elsewhere / "maps"
            maps.mkdir()
            self.assertTrue(self.shell.setUnitFolderFor(fwd(self.elsewhere), "", "depth", fwd(maps)))
        mem = self.shell._data["project_memory"]
        self.assertEqual(mem[self.shell._project_key(fwd(self.elsewhere))]["folders"][""], {"depth": "maps"})
        self.assertEqual(mem[self.shell._project_key(fwd(self.film))]["folders"][""], {"depth": "fmaps"})
        # each project reads its own choice; the open one's stages still follow theirs
        self.assertEqual(Path(self.shell.projectFoldersFor(fwd(self.elsewhere), "")[1]["path"]), maps)
        self.assertTrue(self.shell.projectFoldersFor(fwd(self.elsewhere), "")[1]["overridden"])
        self.assertEqual(Path(self.shell.projectFolders("")[1]["path"]), self.film / "fmaps")
        self.assertEqual(Path(chapters.stage_dirs(fwd(self.film))["depth"]), self.film / "fmaps")
        # after a restart too
        again = self.new_shell()
        again.projectFolder = fwd(self.film)
        self.assertEqual(Path(again.projectFoldersFor(fwd(self.elsewhere), "")[1]["path"]), maps)
        self.assertEqual(Path(again.projectFolders("")[1]["path"]), self.film / "fmaps")

    def test_create_folders_in_another_project(self):
        self.shell.projectFolder = fwd(self.film)
        empty = self.root / "Elsewhere" / "Brand New"
        empty.mkdir(parents=True)
        refreshed = []
        self.shell.projectRefreshed.connect(lambda: refreshed.append(1))
        self.assertTrue(self.shell.createProjectFoldersFor(fwd(empty), ""))
        self.assertTrue((empty / "Source Clips").is_dir())
        self.assertEqual(refreshed, [])
        self.assertTrue(all(r["exists"] for r in self.shell.projectFoldersFor(fwd(empty), "")))
        self.assertFalse(self.shell.createProjectFoldersFor(fwd(self.root / "Nowhere"), ""))


if __name__ == "__main__":
    unittest.main()
