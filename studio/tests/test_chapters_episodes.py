"""TV shows (user, 2026-10-01): the stage folders sit once per episode,
Show/Season 1/Episode 1/1. S1E1 Source Clips/Chapter 1. The chapter list finds
every episode, each chapter keeps its own episode's folders, a saved pick
survives a new episode, and a movie works exactly as before. Temp folders
only; Studio's settings redirected.

    python -m unittest studio.tests.test_chapters_episodes
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.ui import chapters  # noqa: E402

STAGES = ("1. {p} Source Clips", "2. {p} Depth Maps", "3. {p} Splats", "4. {p} Inpainted", "5. {p} Merged")


def _episode(root: Path, season: int, ep: int, chapters_=(1,)) -> Path:
    d = root / f"Season {season}" / f"Episode {ep}"
    tag = f"S{season}E{ep}"
    for st in STAGES:
        (d / st.format(p=tag)).mkdir(parents=True)
    for c in chapters_:
        (d / STAGES[0].format(p=tag) / f"Chapter {c}").mkdir()
    return d


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.show = Path(self.tmp.name) / "My Show"

    def tearDown(self):
        self.tmp.cleanup()

    def test_episodes_found_in_natural_order(self):
        for ep in (10, 2, 1):
            _episode(self.show, 1, ep)
        _episode(self.show, 2, 1)
        self.assertEqual(chapters.find_units(str(self.show)),
                         ["Season 1/Episode 1", "Season 1/Episode 2", "Season 1/Episode 10", "Season 2/Episode 1"])

    def test_a_movie_is_its_own_unit(self):
        movie = Path(self.tmp.name) / "Movie"
        for n in ("SM Source Clips/Chapter 1", "SM DepthMaps"):
            (movie / n).mkdir(parents=True)
        self.assertEqual(chapters.find_units(str(movie)), [""])

    def test_a_show_has_an_episode_folder(self):
        _episode(self.show, 1, 1)
        self.assertTrue(chapters.is_show(str(self.show)))
        nested = Path(self.tmp.name) / "Other Film"                  # a movie kept in a subfolder
        (nested / "Movie" / "BR Source Clips" / "Chapter 1").mkdir(parents=True)
        (nested / "Movie" / "BR Depth Maps").mkdir(parents=True)
        self.assertFalse(chapters.is_show(str(nested)))
        self.assertEqual(chapters.find_units(str(nested)), ["Movie"])

    def test_labels(self):
        self.assertEqual(chapters.unit_label("Season 1/Episode 1"), "S1E1")
        self.assertEqual(chapters.unit_label("Season 02/Episode 11"), "S2E11")
        self.assertEqual(chapters.unit_label("Episode 3"), "E3")
        self.assertEqual(chapters.unit_label("Pilot/Cut A"), "Pilot / Cut A")

    def test_an_episodes_paths_stay_in_that_episode(self):
        ep = _episode(self.show, 1, 1)
        paths = chapters.stage_paths("depthcrafter", str(ep), 1)
        self.assertEqual(paths["input"], (ep / "1. S1E1 Source Clips/Chapter 1").as_posix())
        self.assertEqual(paths["output"], (ep / "2. S1E1 Depth Maps/Chapter 1").as_posix())


class ShellTests(unittest.TestCase):
    """The Shell side: one int key per (episode, chapter)."""

    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app
        cls.app = app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import studio.settings_store as ss
        self._cfg = ss.CONFIG_DIR
        ss.CONFIG_DIR = Path(self.tmp.name) / "config"
        from studio.shell import StudioShell
        self.shell = StudioShell()
        self.show = Path(self.tmp.name) / "My Show"

    def tearDown(self):
        import studio.settings_store as ss
        ss.CONFIG_DIR = self._cfg
        self.tmp.cleanup()

    def test_show_chapters_and_paths(self):
        e1 = _episode(self.show, 1, 1, (1, 2))
        e2 = _episode(self.show, 1, 2, (1,))
        self.shell.projectFolder = str(self.show)
        entries = self.shell.listChapters()
        self.assertEqual([e["name"] for e in entries], ["S1E1 · Chapter 1", "S1E1 · Chapter 2", "S1E2 · Chapter 1"])
        self.assertEqual(len({e["num"] for e in entries}), 3)
        key = entries[2]["num"]
        paths = self.shell.stagePaths("splatting", key, entries[2]["name"])
        self.assertEqual(Path(paths["depth"]), e2 / "2. S1E2 Depth Maps" / "Chapter 1")
        self.assertEqual(Path(paths["output"]), e2 / "3. S1E2 Splats" / "Chapter 1")
        self.assertTrue(str(Path(self.shell.stagePaths("splatting", entries[0]["num"], "")["output"])).startswith(str(e1)))
        # no whole-movie Source folder for a show
        self.assertEqual(self.shell.stagePaths("scenecut", -1, "Whole Movie"), {})

    def test_a_saved_pick_survives_a_new_episode(self):
        _episode(self.show, 1, 2, (1,))
        self.shell.projectFolder = str(self.show)
        entries = self.shell.listChapters()
        self.shell.setStageChapter("splatting", entries[0])           # S1E2 · Chapter 1
        _episode(self.show, 1, 1, (1,))                                # sorts BEFORE it
        entries = self.shell.listChapters()
        saved = self.shell.stageChapter("splatting")
        self.assertEqual(saved["name"], "S1E2 · Chapter 1")
        self.assertIn(saved["num"], {e["num"] for e in entries})
        self.assertEqual([e["name"] for e in entries if e["num"] == saved["num"]], ["S1E2 · Chapter 1"])

    def test_a_movie_in_a_subfolder_needs_no_prefix(self):
        movie = Path(self.tmp.name) / "Other Film"
        (movie / "Movie" / "1. BR Source Clips" / "Chapter 1 - Intro").mkdir(parents=True)
        (movie / "Movie" / "2. BR Depth Maps").mkdir(parents=True)
        self.shell.projectFolder = str(movie)
        self.assertEqual([e["name"] for e in self.shell.listChapters()], ["Chapter 1 - Intro"])

    def _movie(self, folder: Path) -> Path:
        (folder / "SM Source Clips" / "Chapter 1").mkdir(parents=True)
        return folder

    def _names(self):
        return [(p["name"], p["show"]) for p in self.shell.listProjects()]

    def test_projects_mixed_in_one_folder(self):
        root = Path(self.tmp.name) / "Conversions"
        self._movie(root / "Film A")
        _episode(root / "Show B", 1, 1)
        self.shell.projectsRoot = str(root)
        self.assertEqual(self._names(), [("Film A", False), ("Show B", True)])

    def test_projects_in_movies_and_tv_shows_folders(self):
        root = Path(self.tmp.name) / "Projects"
        self._movie(root / "Movies" / "Film A")
        self._movie(root / "Films" / "Film C")
        (root / "TV Shows" / "Show B").mkdir(parents=True)          # no episodes yet: still a show
        self.shell.projectsRoot = str(root)
        self.assertEqual(self._names(), [("Film A", False), ("Film C", False), ("Show B", True)])

    def test_a_separate_tv_shows_folder(self):
        movies, shows = Path(self.tmp.name) / "D" / "Movies3D", Path(self.tmp.name) / "E" / "TV3D"
        self._movie(movies / "Film A")
        (shows / "Show B").mkdir(parents=True)
        self.shell.projectsRoot = str(movies)
        self.shell.showsRoot = str(shows)
        self.assertEqual(self._names(), [("Film A", False), ("Show B", True)])
        self.shell.showsRoot = ""
        self.assertEqual(self._names(), [("Film A", False)])

    def test_a_movie_keeps_its_chapter_numbers(self):
        movie = Path(self.tmp.name) / "Movie"
        for n in ("SM Source Clips/Chapter 7 - Seven", "SM DepthMaps/Chapter 7 - Seven"):
            (movie / n).mkdir(parents=True)
        self.shell.projectFolder = str(movie)
        self.assertEqual(self.shell.listChapters(), [{"num": 7, "name": "Chapter 7 - Seven"}])
        self.shell.setStageChapter("depthcrafter", {"num": 7, "name": "Chapter 7 - Seven"})
        self.assertEqual(self.shell.stageChapter("depthcrafter"), {"num": 7, "name": "Chapter 7 - Seven"})
        p = self.shell.stagePaths("depthcrafter", 7, "Chapter 7 - Seven")
        self.assertEqual(Path(p["output"]), movie / "SM DepthMaps" / "Chapter 7 - Seven")


if __name__ == "__main__":
    unittest.main()
