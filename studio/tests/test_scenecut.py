"""Scene Cut (Extra tab): the generated engine is today's v1, and the settings
round-trip v1's config file. The real run is checks/check_scenecut.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.scenecut import settings as S  # noqa: E402
try:                    # the Prototype's generators don't ship (release, 2026-10-17)
    from studio.tools import gen_scenecut_engine as gen  # noqa: E402
except ImportError:
    gen = None


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    def test_engine_matches_v1(self):
        """engine.py is exactly what the generator makes from today's v1.

        Fails when scenecut_gui.py has changed since engine.py was generated:
        re-run studio.tools.gen_scenecut_engine."""
        fresh = gen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, gen.TARGET.read_bytes(),
                         "engine.py is stale; re-run: python -m studio.tools.gen_scenecut_engine")


class SettingsTest(unittest.TestCase):
    V1 = {"video_path": "E:/a.mkv", "output_dir": "E:/out", "detector_type": "OmniShotCut",
          "detector_params": {"AutoShot": {"threshold": "0.24"}, "OmniShotCut": {"mode": "clean_shot"}},
          "refinements_enabled": False, "snap_cuts": True, "ai_validate": True, "ai_window": 5,
          "flash_sensitivity": 20, "export_csv": True, "export_html": False, "export_sc": True,
          "save_images": False, "split_video": True, "split_by_chapter": False, "num_images": 4,
          "frame_margin": 2, "sc_offset": -1, "ffmpeg_codec": "libx265", "ffmpeg_preset": "p5", "ffmpeg_cq": 20}

    def test_round_trip(self):
        """v1's file in, v1's file out: same keys, same values -- except the
        codec, which v1's own load fixes to hevc_nvenc."""
        out = S.to_v1(S.seed_from_v1(self.V1))
        self.assertEqual(list(out), list(self.V1))
        expected = dict(self.V1, ffmpeg_codec="hevc_nvenc")
        self.assertEqual(out, expected)

    def test_defaults_are_v1s_load_fallbacks(self):
        d = S.normalise({})
        self.assertEqual(d["ai_window"], 3)          # _load_settings' fallback, not the var's 5
        self.assertEqual(d["detector_type"], "AutoShot")
        self.assertTrue(d["refinements_enabled"])
        self.assertEqual(d["detector_params"], {})

    def test_spins_are_clamped_to_v1s_ranges(self):
        self.assertEqual(S.coerce("ffmpeg_cq", 99), 30)
        self.assertEqual(S.coerce("sc_offset", -9), -5)
        self.assertEqual(S.coerce("num_images", "7"), 7)


class StopTests(unittest.TestCase):
    """audit 2026-09-28 #13 (user 2026-09-29): a scene worker's encoder is
    ended by Stop now (abort_now) -- its .part discarded -- while the safe
    stop (abort_flag alone) lets the scenes being cut finish. A real child
    process stands in for ffmpeg: it writes its .part, then would run 30 s."""

    def run_scene(self, now):
        import subprocess
        import tempfile
        import threading
        import time
        from studio.legacy import install
        install()
        import studio.stages.scenecut.engine as E
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        part = Path(tmp.name) / "clip-Scene-001.mp4.part"
        eng = type("Eng", (), {})()
        eng.abort_flag, eng.abort_now = threading.Event(), threading.Event()
        cmd = [sys.executable, "-c",
               "import sys, time; open(sys.argv[1], 'wb').write(b'half'); time.sleep(30)", str(part)]
        threading.Timer(1.0, lambda: (eng.abort_flag.set(), now and eng.abort_now.set())).start()
        started = time.monotonic()
        try:
            result = E._SplitProcs(eng).run(cmd, capture_output=True, text=True, check=False, timeout=6)
        except subprocess.TimeoutExpired:
            result = "ran on"
        except Exception as e:
            result = e
        return result, time.monotonic() - started, part

    def test_stop_now_ends_the_encoder_and_discards_its_part(self):
        result, took, part = self.run_scene(now=True)
        self.assertIsInstance(result, Exception)
        self.assertEqual(type(result).__name__, "ScenesCancelled")
        self.assertLess(took, 4, "the encoder ran on after Stop now")
        self.assertFalse(part.exists(), "the half-written .part was left")

    def test_the_safe_stop_lets_the_encoder_run(self):
        result, took, part = self.run_scene(now=False)
        self.assertEqual(result, "ran on")       # still running at the 6 s timeout
        self.assertGreater(took, 5)


#: Stands in for ffmpeg on a damaged clip: 200 KB of decode errors on stderr
#: (Windows' pipe holds ~4 KB), then 120 frames of 48x27 rgb24 on stdout.
CHATTY_FFMPEG = (
    "import sys\n"
    "sys.stderr.write('[h264 @ 0x1] error while decoding MB 12 7, bytestream -5\\n' * 4000)\n"
    "sys.stderr.flush()\n"
    "sys.stdout.buffer.write(bytes(48 * 27 * 3 * 120))\n"
)


class DamagedClipTest(unittest.TestCase):
    """bug sweep 2026-10-09 #13: AutoShot / OmniShotCut's reader left ffmpeg's
    stderr in an unread pipe; a damaged clip filled it and detection hung."""

    def read_all(self, generator):
        import subprocess
        import threading
        from unittest import mock
        real = subprocess.Popen
        procs, got = [], []

        def chatty(cmd, **kw):
            procs.append(real([sys.executable, "-c", CHATTY_FFMPEG], **kw))
            return procs[-1]

        def run():
            got.append(sum(len(c) for c in generator("damaged.mp4")))
        with mock.patch("subprocess.Popen", chatty):
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(20)
        for proc in procs:          # a hung one is ended here
            if proc.poll() is None:
                proc.kill()
        return got

    def test_the_reader_drains_stderr(self):
        from core.scenecut.frames import autoshot_frame_generator
        self.assertEqual(self.read_all(autoshot_frame_generator), [120])

    def test_the_engine_reads_with_it_and_so_does_omnishot(self):
        from core.scenecut import detection, frames
        from studio.stages.scenecut import engine
        self.assertIs(engine._autoshot_frame_generator, frames.autoshot_frame_generator)
        self.assertIs(detection._autoshot_frame_generator, frames.autoshot_frame_generator)


class OmniShotRoomTest(unittest.TestCase):
    """audit 2026-09-28 (minor): OmniShotCut decodes the whole clip into a
    temp file (frames x its process size x 3 -- tens of GB for a film) with
    no look at the free space first; a full disk failed it mid-decode."""

    def test_too_little_room_is_refused_before_decoding(self):
        from unittest import mock
        from collections import namedtuple
        from core.scenecut import detection as D
        usage = namedtuple("usage", "total used free")
        with mock.patch("shutil.disk_usage", return_value=usage(0, 0, 2 * 2 ** 30)):
            with self.assertRaisesRegex(OSError, "GB free"):
                D.check_omnishot_room(100_000, 384, 216, "X:/tmp")        # ~23 GB
            D.check_omnishot_room(1_000, 384, 216, "X:/tmp")              # ~0.2 GB: fine
            D.check_omnishot_room(0, 384, 216, "X:/tmp")                  # unknown length: not checked


class ChapterHandoffTest(unittest.TestCase):
    """audit 2026-09-28 (low confidence, settled): a whole-movie run records
    the handoff once per chapter, so the last chapter's folder won -- and
    DepthCrafter's Import from Scene Cut took just that folder. The first
    chapter that produced clips is the one handed on."""

    def test_a_chapter_run_hands_on_its_first_chapter(self):
        from types import SimpleNamespace
        from unittest import mock
        from studio.stages.scenecut import engine as E
        cls = next(c for c in vars(E).values() if isinstance(c, type) and hasattr(c, "_record_job_handoff"))
        got = []
        fake = SimpleNamespace(_chapter_run=True, _chapter_handoff=None)
        with mock.patch("core.ui.job_handoff.record_job", lambda stage, rec: got.append(rec["source"])):
            for folder in ("M/Ch01", "M/Ch02", "M/Ch03"):
                cls._record_job_handoff(fake, folder)
            plain = SimpleNamespace()
            cls._record_job_handoff(plain, "clips")          # not a chapter run: as before
        self.assertEqual(got, ["M/Ch01", "clips"])

    def test_the_chapter_loop_marks_its_run(self):
        import inspect
        from studio.stages.scenecut import engine as E
        cls = next(c for c in vars(E).values() if isinstance(c, type) and hasattr(c, "_write_outputs_by_chapter"))
        src = inspect.getsource(cls._write_outputs_by_chapter)
        self.assertIn("self._chapter_run", src)


def _engine_cls():
    from studio.legacy import install
    install()
    from studio.stages.scenecut import engine as E
    return E, E.SceneCutEngine


class _Sig:
    def emit(self, *a):
        pass


class FinishTookTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): with Split video on, the split's last update
    is 100 % too; it read (and forgot) the claim time, so "Done!" lost its
    "Took ..." line."""

    def test_done_keeps_its_took_line(self):
        import queue
        import time
        from types import SimpleNamespace
        from unittest import mock
        from studio import gpu_jobs
        from studio.stages.scenecut.controller import SceneCutController
        dialogs = []
        eng = SimpleNamespace(progress_queue=queue.Queue(), dialog_queue=queue.Queue(),
                              _queue_dialog=lambda kind, title, text: dialogs.append(text),
                              is_busy=lambda: True, detected_scenes=[1, 2, 3])
        eng.progress_queue.put((100.0, "Splitting scenes Parallel (3/3)"))
        eng.progress_queue.put((100, "Done! Found 3 scenes."))
        fake = SimpleNamespace(_engine=eng, _progress=0.0, _status="", progressChanged=_Sig(),
                               stateChanged=_Sig(), message=_Sig(), _busy=True,
                               _console_bar=lambda *a: None)    # the console's bar (2026-10-18)
        gpu_jobs._claimed_at["scenecut"] = time.monotonic() - 65
        self.addCleanup(gpu_jobs._claimed_at.pop, "scenecut", None)
        with mock.patch("studio.outcome.announce"):
            SceneCutController._process_queues(fake)
        self.assertEqual(len(dialogs), 1)
        self.assertIn("Took ", dialogs[0])


class ChapterLeftBehindTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): a project with no Scene Cut chapter kept the
    last project's chapter paths, and a picked video wrote into that folder."""

    def test_a_project_without_a_chapter_forgets_the_old_paths(self):
        from types import SimpleNamespace
        from studio.stages.scenecut.controller import SceneCutController
        shell = SimpleNamespace(listChapters=lambda: [], stageChapter=lambda stage: {})
        fake = SimpleNamespace(_shell=shell, _chapters=[], _chapter_num=3,
                               _chapter_expected={"output": "E:/A/Chapter 3"}, chapterChanged=_Sig())
        SceneCutController.refreshChapters(fake)
        self.assertEqual(fake._chapter_num, -1)
        self.assertEqual(fake._chapter_expected, {})


class WholeMovieByItselfTest(unittest.TestCase):
    """2026-10-11: no chapter dropdown on Scene Cut (it takes the whole movie). A
    project applies Whole Movie by itself unless the user set their own output."""

    def run_refresh(self, saved, whole_paths={"output": "E:/M/SM Source Clips"}, project="E:/M"):
        from types import SimpleNamespace
        from studio.stages.scenecut.controller import SceneCutController
        picked = []
        shell = SimpleNamespace(projectFolder=project, listChapters=lambda: [{"num": 1, "name": "Chapter 1"}],
                                stageChapter=lambda stage: dict(saved),
                                stagePaths=lambda stage, num, name: dict(whole_paths))
        fake = SimpleNamespace(_shell=shell, _chapters=[], _chapter_num=-1, _chapter_expected={},
                               chapterChanged=_Sig(), pickChapter=picked.append)
        SceneCutController.refreshChapters(fake)
        return picked, fake

    def test_a_new_project_gets_whole_movie(self):
        from studio.stages.scenecut.controller import WHOLE_MOVIE
        self.assertEqual(self.run_refresh({})[0], [WHOLE_MOVIE])

    def test_a_single_chapter_from_the_old_dropdown_becomes_whole_movie(self):
        from studio.stages.scenecut.controller import WHOLE_MOVIE
        self.assertEqual(self.run_refresh({"num": 1, "name": "Chapter 1"})[0], [WHOLE_MOVIE])

    def test_the_users_own_output_is_kept(self):
        picked, fake = self.run_refresh({"num": -1, "name": "manual"})
        self.assertEqual(picked, [])
        self.assertEqual(fake._chapter_num, -1)

    def test_a_show_or_no_project_stays_manual(self):
        self.assertEqual(self.run_refresh({}, whole_paths={})[0], [])        # a show: no whole-movie folder
        self.assertEqual(self.run_refresh({}, project="")[0], [])


class RefusedClaimTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): a refused GPU claim left the status reading
    "Starting..." with nothing running."""

    def test_the_claim_comes_before_starting(self):
        import inspect
        _E, cls = _engine_cls()
        src = inspect.getsource(cls._start_detection)
        self.assertLess(src.index("claim_gpu"), src.index('text="Starting..."'))
        self.assertLess(src.index("claim_gpu"), src.index("start_button.configure"))


class MarkersFirstTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): the whole-movie run read the chapter markers
    only after detection (minutes on a film), and a failed ffprobe read like
    "no chapter markers"."""

    def run_task(self, markers):
        import queue
        import tempfile
        import threading
        from types import SimpleNamespace
        from unittest import mock
        E, cls = _engine_cls()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dialogs, said = [], []
        fake = SimpleNamespace(progress_queue=queue.Queue(), abort_flag=threading.Event(),
                               _opt=lambda k, d=None: {"whole_movie": True}.get(k, d),
                               _queue_dialog=lambda kind, title, text: dialogs.append(title))
        with mock.patch.object(E, "get_video_info", lambda path: (24.0, 1000)), \
                mock.patch("core.scenecut.movie_chapters.read_markers", markers):
            cls._detection_and_output_task(fake, "movie.mkv", tmp.name, {"type": "AutoShot"})
        while not fake.progress_queue.empty():
            said.append(fake.progress_queue.get()[1])
        return dialogs, said

    def test_no_markers_stops_before_detecting(self):
        dialogs, said = self.run_task(lambda path, strict=False: [])
        self.assertEqual(dialogs, ["No chapter markers"])
        self.assertIn("No chapter markers in this movie.", said)
        self.assertFalse(any("AutoShot" in s for s in said), said)

    def test_an_unreadable_file_says_so(self):
        from core.scenecut import movie_chapters as MC

        def fail(path, strict=False):
            raise MC.MarkersUnreadable("Invalid data found when processing input")
        dialogs, said = self.run_task(fail)
        self.assertEqual(dialogs, ["Chapter markers unreadable"])
        self.assertTrue(said[-1].startswith("Error"), said)

    def test_the_reader_tells_failed_from_none(self):
        import subprocess
        from unittest import mock
        from core.scenecut import movie_chapters as MC
        bad = subprocess.CompletedProcess([], 1, "", "movie.mkv: Invalid data found")
        with mock.patch("subprocess.run", return_value=bad):
            self.assertEqual(MC.read_markers("movie.mkv"), [])
            with self.assertRaisesRegex(MC.MarkersUnreadable, "Invalid data"):
                MC.read_markers("movie.mkv", strict=True)
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("ffprobe")):
            with self.assertRaises(MC.MarkersUnreadable):
                MC.read_markers("movie.mkv", strict=True)
        none = subprocess.CompletedProcess([], 0, '{"chapters": []}', "")
        with mock.patch("subprocess.run", return_value=none):
            self.assertEqual(MC.read_markers("movie.mkv", strict=True), [])


class FailedSceneTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): a scene that failed to split kept the clip of
    its number from an earlier cut, and the folder went on to DepthCrafter."""

    def test_a_failed_scenes_old_clip_goes(self):
        import queue
        import subprocess
        import tempfile
        import threading
        from types import SimpleNamespace
        from unittest import mock
        E, cls = _engine_cls()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "out"
        out.mkdir()
        binned = Path(tmp.name) / "bin"
        binned.mkdir()
        for k in (1, 2):
            (out / f"v-Scene-00{k}.mp4").write_bytes(b"old cut")

        def ffmpeg(cmd, **kw):               # scene 2 fails; the rest write their .part
            target = str(cmd[-1])
            if "Scene-002" in target:
                return subprocess.CompletedProcess(cmd, 1, "", "nvenc: OpenEncodeSessionEx failed")
            Path(target).write_bytes(b"new cut")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def to_bin(path):                    # never the real Recycle Bin
            Path(path).rename(binned / Path(path).name)
            return True
        tc = lambda f: SimpleNamespace(get_frames=lambda: f)  # noqa: E731
        fake = SimpleNamespace(abort_flag=threading.Event(), abort_now=threading.Event(),
                               progress_queue=queue.Queue(), _opt=lambda k, d=None: d, _clip_prefix=None,
                               detected_scenes=[(tc(0), tc(10)), (tc(10), tc(20)), (tc(20), tc(30))],
                               _record_job_handoff=lambda folder: None)
        with mock.patch("subprocess.run", ffmpeg), \
                mock.patch.object(E, "probe_stream_props", lambda p: {"has_audio": False, "fps_num": 24,
                                                                     "fps_den": 1, "is_cfr": True}), \
                mock.patch.object(E, "has_nvenc_hevc", lambda: False), \
                mock.patch.object(E, "can_hwdecode", lambda p: False), \
                mock.patch.object(E, "probe_frame_count", lambda p: 10), \
                mock.patch.object(E, "send_to_recycle_bin", to_bin):
            cls._split_video(fake, "v.mp4", str(out), 24.0)
        self.assertEqual(fake._split_failed, 1)
        self.assertEqual((out / "v-Scene-001.mp4").read_bytes(), b"new cut")
        self.assertEqual((out / "v-Scene-003.mp4").read_bytes(), b"new cut")
        self.assertFalse((out / "v-Scene-002.mp4").exists(), "the earlier cut's scene 2 was left")
        self.assertTrue((binned / "v-Scene-002.mp4").exists())



class AutoCropTest(unittest.TestCase):
    """Auto Crop (user, 2026-10-11): each split shot cut to its own picture. A
    real split (ffmpeg, the CPU encoder) of a synthetic clip whose first shot is
    letterboxed to 2.39:1 and whose second fills the 16:9 frame."""

    def test_each_shot_gets_its_own_picture(self):
        import queue
        import subprocess
        import tempfile
        import threading
        from types import SimpleNamespace
        from unittest import mock
        E, cls = _engine_cls()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "movie.mp4"
        out = Path(tmp.name) / "out"
        out.mkdir()
        fc = ("[0:v]pad=640:360:0:46:black,setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x268:rate=24:duration=1",
                        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=1", "-filter_complex", fc,
                        "-map", "[v]", "-c:v", "libx264", "-g", "6", "-pix_fmt", "yuv420p", str(src)], check=True)
        tc = lambda f: SimpleNamespace(get_frames=lambda: f)  # noqa: E731
        opts = {"auto_crop": True, "ffmpeg_preset": "p7", "ffmpeg_cq": 16}
        fake = SimpleNamespace(abort_flag=threading.Event(), abort_now=threading.Event(),
                               progress_queue=queue.Queue(), _opt=lambda k, d=None: opts.get(k, d), _clip_prefix=None,
                               detected_scenes=[(tc(0), tc(24)), (tc(24), tc(48))],
                               _record_job_handoff=lambda folder: None)
        with mock.patch.object(E, "has_nvenc_hevc", lambda: False), mock.patch.object(E, "can_hwdecode", lambda p: False):
            cls._split_video(fake, str(src), str(out), 24.0)

        def size(name):
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0:s=x", str(out / name)],
                               capture_output=True, text=True)
            return r.stdout.strip()
        self.assertEqual(size("movie-Scene-001.mp4"), "640x268", "the letterboxed shot, its bars cut")
        self.assertEqual(size("movie-Scene-002.mp4"), "640x360", "the full-frame shot left whole")

    def test_on_the_gpu_nvdec_crops_as_it_decodes(self):
        # user, 2026-10-11: "do the cropped shots have to use cpu?" -- no: NVDEC's own
        # crop (h264_cuvid -crop), frames on the GPU end to end, the same frames
        import queue
        import subprocess
        import tempfile
        import threading
        from types import SimpleNamespace
        from core.scenecut import autocrop as AC
        E, cls = _engine_cls()
        if not E.has_nvenc_hevc():
            self.skipTest("no NVENC here")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "movie.mp4"
        out = Path(tmp.name) / "out"
        out.mkdir()
        fc = ("[0:v]pad=640:360:0:46:black,setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x268:rate=24:duration=1",
                        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=1", "-filter_complex", fc,
                        "-map", "[v]", "-c:v", "libx264", "-g", "6", "-pix_fmt", "yuv420p", str(src)], check=True)
        if not E.can_hwdecode(str(src)):
            self.skipTest("no NVDEC here")
        self.assertEqual(AC.cuvid_decoder(str(src)), "h264_cuvid")
        tc = lambda f: SimpleNamespace(get_frames=lambda: f)  # noqa: E731
        opts = {"auto_crop": True, "ffmpeg_preset": "p4", "ffmpeg_cq": 16}
        fake = SimpleNamespace(abort_flag=threading.Event(), abort_now=threading.Event(),
                               progress_queue=queue.Queue(), _opt=lambda k, d=None: opts.get(k, d), _clip_prefix=None,
                               detected_scenes=[(tc(0), tc(24)), (tc(24), tc(48))],
                               _record_job_handoff=lambda folder: None)
        cls._split_video(fake, str(src), str(out), 24.0)

        def probe(name):
            r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height,nb_read_frames", "-of", "csv=p=0:s=x", str(out / name)],
                               capture_output=True, text=True)
            return r.stdout.strip()
        self.assertEqual(probe("movie-Scene-001.mp4"), "640x268x24", "cropped on the GPU, every frame")
        self.assertEqual(probe("movie-Scene-002.mp4"), "640x360x24")

    def test_off_leaves_every_shot_whole(self):
        from core.scenecut import autocrop as AC
        self.assertIsNone(AC.choose([], [], 640, 360))
        bars = (0, 46, 640, 314)
        self.assertEqual(AC.choose([bars], [bars], 640, 360), (640, 268, 0, 46))
        # a dark shot's lopsided box: the movie's real bars, never its own
        self.assertEqual(AC.bar_sizes([(120, 46, 640, 314)] * 5, 3, 640, 360), [])
        self.assertEqual(AC.choose([(120, 46, 640, 314)], [bars], 640, 360), (640, 268, 0, 46))



class AutoCropEdgeTest(unittest.TestCase):
    """bug sweep 2026-10-14c: an odd frame size gave an odd crop; a container with no keyframe
    flags left every shot whole without a word; a failed ffmpeg read as "no bars"."""

    def test_an_odd_frame_still_crops_to_even(self):
        from core.scenecut import autocrop as AC
        w, h, _x, _y = AC.choose([(0, 40, 639, 321)], [(0, 40, 639, 321)], 639, 361)
        self.assertEqual((w % 2, h % 2), (0, 0), (w, h))

    def test_no_keyframes_reads_each_shot(self):
        from unittest import mock
        from core.scenecut import autocrop as AC
        bars = (0, 46, 640, 314)
        with mock.patch.object(AC, "frame_size", return_value=(640, 360)),                 mock.patch.object(AC, "keyframe_boxes", return_value=[]),                 mock.patch.object(AC, "shot_boxes", return_value=[bars] * 3) as shots:
            crops = AC.shot_crops("x.mp4", [(0, 24), (24, 48)], 24, 1, 0.0)
        self.assertEqual(crops, {0: (640, 268, 0, 46), 24: (640, 268, 0, 46)})
        self.assertEqual(shots.call_count, 2, "each shot read once")

    def test_a_failed_ffmpeg_is_logged(self):
        import sys as _sys
        from core.scenecut import autocrop as AC
        with self.assertLogs(AC.logger, "WARNING") as logs:
            AC._run([_sys.executable, "-c", "import sys; sys.stderr.write('bad file'); sys.exit(3)",
                     "-i", "x.mp4"])
        self.assertTrue(any("code 3" in m and "bad file" in m for m in logs.output), logs.output)

if __name__ == "__main__":
    unittest.main()
