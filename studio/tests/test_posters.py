"""Project posters from a video's embedded cover art (user, 2026-10-02): an
MP4 attached picture or an MKV image attachment -- only REAL cover art
(portrait, or named cover / poster), never a landscape frame grab saved as a
thumbnail; a picture lying in the project folder still wins. Made-up videos
in a temp folder; settings redirected.

    python -m unittest studio.tests.test_posters
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


def _ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


class PosterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        import studio.settings_store as ss
        self._cfg = ss.CONFIG_DIR
        ss.CONFIG_DIR = self.d / "config"
        self.addCleanup(lambda: setattr(ss, "CONFIG_DIR", self._cfg))
        from studio import posters
        posters._memo.clear()
        self.posters = posters
        self.video = self.d / "plain.mp4"
        _ff("-f", "lavfi", "-i", "testsrc2=size=128x72:rate=12", "-frames:v", "6", "-pix_fmt", "yuv420p", str(self.video))

    def picture(self, name, w, h, colour=(200, 40, 40)):
        p = self.d / name
        Image.fromarray(np.full((h, w, 3), colour, np.uint8)).save(p)
        return p

    def project(self, name):
        p = self.d / "Conversions" / name
        (p / "SM Source Clips" / "Chapter 1").mkdir(parents=True)
        return p

    def mp4_with_cover(self, out, pic):
        _ff("-i", str(self.video), "-i", str(pic), "-map", "0", "-map", "1", "-c", "copy",
            "-disposition:v:1", "attached_pic", str(out))

    def mkv_with_attachment(self, out, pic, name):
        _ff("-i", str(self.video), "-attach", str(pic), "-metadata:s:t", "mimetype=image/jpeg",
            "-metadata:s:t", f"filename={name}", "-c", "copy", str(out))

    def test_mp4_portrait_cover(self):
        proj = self.project("Film A")
        self.mp4_with_cover(proj / "Film A.mp4", self.picture("cover.jpg", 60, 90))
        got = self.posters.video_cover(str(proj))
        self.assertTrue(got.endswith(".png"), got)
        with Image.open(got) as im:
            self.assertEqual(im.size, (60, 90))

    def test_a_landscape_frame_thumbnail_is_not_a_poster(self):
        proj = self.project("Film B")
        self.mp4_with_cover(proj / "Film B.mp4", self.picture("frame.jpg", 160, 90))
        self.assertEqual(self.posters.video_cover(str(proj)), "")

    def test_mkv_cover_attachment(self):
        proj = self.project("Film C")
        self.mkv_with_attachment(proj / "Film C.mkv", self.picture("p.jpg", 60, 90), "cover.jpg")
        self.assertTrue(self.posters.video_cover(str(proj)).endswith(".png"))

    def test_mkv_landscape_thumbnail_attachment_skipped(self):
        proj = self.project("Film D")
        self.mkv_with_attachment(proj / "Film D.mkv", self.picture("t.jpg", 160, 90), "thumb.jpg")
        self.assertEqual(self.posters.video_cover(str(proj)), "")

    def test_a_landscape_attachment_before_the_cover(self):
        """bug sweep A2: the scan gave up at the first image attachment that
        wasn't a poster, so a real cover after a landscape still was never found.
        WebP: Matroska shows JPEG / PNG attachments as attached pictures (the
        other branch, which already went on); a WebP stays an attachment."""
        proj = self.project("Film F")
        still, cover = self.picture("still.webp", 160, 90), self.picture("poster.webp", 60, 90, (40, 40, 200))
        _ff("-i", str(self.video), "-attach", str(still), "-attach", str(cover),
            "-metadata:s:t:0", "mimetype=image/webp", "-metadata:s:t:0", "filename=still.webp",
            "-metadata:s:t:1", "mimetype=image/webp", "-metadata:s:t:1", "filename=poster.webp",
            "-c", "copy", str(proj / "Film F.mkv"))
        got = self.posters.video_cover(str(proj))
        self.assertTrue(got.endswith(".png"), got)
        with Image.open(got) as im:
            self.assertEqual(im.size, (60, 90))

    def test_one_folder_down_and_not_the_shots(self):
        proj = self.project("Film E")
        (proj / "Movie").mkdir()
        self.mp4_with_cover(proj / "Movie" / "Film E.mp4", self.picture("c.jpg", 60, 90))
        self.mp4_with_cover(proj / "SM Source Clips" / "Chapter 1" / "shot.mp4", self.picture("x.jpg", 60, 90))
        self.assertTrue(self.posters.video_cover(str(proj)))
        self.assertEqual([Path(v).name for v in self.posters._videos(str(proj))], ["Film E.mp4"])

    def test_a_picture_in_the_folder_still_wins(self):
        from studio.shell import StudioShell
        proj = self.project("Film F")
        self.mp4_with_cover(proj / "Film F.mp4", self.picture("c.jpg", 60, 90))
        self.assertTrue(StudioShell._poster(str(proj)).endswith(".png"))     # the video's, as a file URL
        Image.fromarray(np.zeros((90, 60, 3), np.uint8)).save(proj / "poster.jpg")
        self.assertTrue(StudioShell._poster(str(proj)).endswith("poster.jpg"))


class PickerOffTheUiThreadTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A2): listProjects read every picture-less
    project's videos for cover art (an ffprobe, maybe an ffmpeg, each) before
    the picker could open. The list comes back at once; the cover art follows
    from a worker, through posterFound."""
    setUp, project, picture, mp4_with_cover = (PosterTests.setUp, PosterTests.project, PosterTests.picture,
                                               PosterTests.mp4_with_cover)

    def test_the_list_returns_before_the_cover_art(self):
        import time
        from unittest import mock
        from studio.tests._qt import app
        from studio.shell import StudioShell
        qapp = app()
        root = self.d / "Conversions"
        self.project("Film G")                                      # under Conversions
        self.mp4_with_cover(root / "Film G" / "Film G.mp4", self.picture("c.jpg", 60, 90))
        shell = StudioShell()
        shell.projectsRoot = str(root)
        got = []
        shell.posterFound.connect(lambda path, url: got.append((path, url)))
        calls = []
        real = self.posters.video_cover

        def slow(folder):
            import threading
            calls.append(threading.current_thread() is threading.main_thread())
            time.sleep(0.3)
            return real(folder)
        with mock.patch.object(self.posters, "video_cover", slow):
            t0 = time.monotonic()
            lst = shell.listProjects()
            took = time.monotonic() - t0
            end = time.monotonic() + 20
            while not got and time.monotonic() < end:
                qapp.processEvents()
                time.sleep(0.01)
        self.assertLess(took, 0.25, "listProjects waited for the cover art")
        self.assertEqual([p["poster"] for p in lst], [""])
        self.assertEqual(calls, [False], "the cover art was read on the UI thread")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0][0].endswith("Film G") and got[0][1].endswith(".png"), got)


if __name__ == "__main__":
    unittest.main()
