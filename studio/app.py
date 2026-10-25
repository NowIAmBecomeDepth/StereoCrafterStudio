"""app.py -- start StereoCrafter Studio.

    python -m studio.app          (or double-click "StereoCrafter Studio.bat")

Everything QML sees is set as a context property here:

    Shell        app-wide state (palettes, fonts, project folder)      shell.py
    Backdrop     Mica / Acrylic glass                                 platform_win.py
    WinFrame     the custom caption's native half                     platform_win.py
    Log          the console's All tab (a LogModel)                   log_model.py
    Logs         the console window's tabs (LogHub)                    log_model.py
    ConsoleFrame the console window's custom caption                   platform_win.py
    Updater      Settings > Studio > Check for Updates                  updater.py
    DepthCrafter the DepthCrafter stage                               stages/depthcrafter/controller.py
    Inpainting   the Inpainting (V1, SVD) stage                       stages/inpainting/controller.py
    InpaintingV2 the Inpainting V2 (Wan VACE) stage                   stages/inpainting_v2/controller.py
    Merging      the Merging stage                                    stages/merging/controller.py
    SceneCut     Scene Cut, on the Extra tab                          stages/scenecut/controller.py
    DepthEdit    the Depth Map Editor, on the Extra tab               stages/depthedit/controller.py
    EdgeFill     Edge Fill, on the Extra tab                          stages/edgefill/controller.py
    Beginner     Studio > Use Beginner Mode                            beginner.py
    Splatting    the Splatting stage                                  stages/splatting/controller.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Windowless (pythonw), every console program Studio starts -- torch.compile's compiler once a kernel,
# ffmpeg, git -- would open a window that takes the focus: hidden, before anything can start one
from . import no_console as _no_console  # noqa: E402

_no_console.install()


def _missing_dependency(exc: BaseException) -> None:
    msg = ("StereoCrafter Studio needs PySide6, which is not installed in this "
           "environment yet.\n\nFrom the StereoCrafter folder, run:\n\n    uv sync\n\n"
           f"({exc})")
    print(msg, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("StereoCrafter Studio", msg)
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQuickControls2 import QQuickStyle
except ImportError as _exc:  # pragma: no cover - depends on the environment
    _missing_dependency(_exc)

from .legacy import REPO, install as _install_legacy  # noqa: E402

_install_legacy()

from .log_model import LogHub, ModelHandler, install_stream_tee  # noqa: E402
from .gpu_jobs import GpuJobs  # noqa: E402
from .job_stats import JobStats, VramMonitor  # noqa: E402
from . import log_model as _log_model  # noqa: E402
from .platform_win import Backdrop, WindowFrame  # noqa: E402
from .shell import StudioShell  # noqa: E402
from .stages.depthcrafter.controller import DepthCrafterController  # noqa: E402
from .stages.inpainting.controller import InpaintingController  # noqa: E402
from .stages.inpainting_v2.controller import InpaintingV2Controller  # noqa: E402
from .stages.merging.controller import MergingController  # noqa: E402
from .stages.scenecut.controller import SceneCutController  # noqa: E402
from .stages.edgefill.controller import EdgeFillController  # noqa: E402
from .stages.depthedit.controller import DepthEditController  # noqa: E402
from .beginner import Beginner  # noqa: E402
from .stages.splatting.controller import SplattingController  # noqa: E402

logger = logging.getLogger("studio")


def _utf8_streams() -> None:
    """The launcher redirects stdout/stderr to a file, which Python opens in
    the ANSI code page (cp1252): the first '█' of draw_progress_bar raised
    UnicodeEncodeError and failed the Hi-Res finalize of every clip."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:   # None under a bare pythonw, or not a TextIOWrapper
            pass


def _setup_logging(model: LogHub) -> None:
    _utf8_streams()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = ModelHandler(model)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s - %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    # After the StreamHandler, which keeps the real stderr: the progress bars
    # reach the console, log records do not arrive twice.
    install_stream_tee(model)


def build(app: QGuiApplication):
    """Create the engine and every object QML expects. Split out of main() so
    a check script can drive the same window without calling exec()."""
    # The UI thread answers Windows (the title bars' hit-test is Python, every
    # message) and the console's rows in Python, so each needs the GIL; with a
    # job thread busy in Python it waited up to the 5 ms default switch
    # interval every time -- a console that would not drag and tabs taking a
    # second to switch during a job (user, 2026-10-01). 0.5 ms: a tab switch
    # under a Python-busy thread went 1190 -> 19 ms; a real Splatting job
    # timed the same (median 10.10 s at 5 ms, 9.85 s at 0.5 ms).
    sys.setswitchinterval(0.0005)
    # OpenCV's PNG writes and reads for any path: a clip named in Cyrillic lost a 3-hour Inpainting
    # at its first hi-res frame (user, 2026-10-24; core/common/image_io.py)
    from core.common import image_io
    image_io.install()
    logs = LogHub()
    _setup_logging(logs)

    shell = StudioShell()
    # the models folder (Settings > Studio > Models, 2026-10-20): before any stage looks for a model
    from .models_controller import ModelsController, apply_saved_folder
    apply_saved_folder(shell)
    # something too big for the Recycle Bin: Studio asks before deleting it for good (user,
    # 2026-10-14) -- not Windows' own box, which came up from job threads, ownerless
    from core.common import recycle_bin as _recycle_bin
    _recycle_bin.ASK_PERMANENT = shell.ask_permanent
    backdrop = Backdrop()
    depthcrafter = DepthCrafterController(shell)
    inpainting = InpaintingController(shell)
    inpainting_v2 = InpaintingV2Controller(shell, sibling=inpainting)
    inpainting.sibling = inpainting_v2
    inpainting_m2s = _personal_m2svid(shell)    # None in the release (personal/ is the user's own)
    splatting = SplattingController(shell)
    merging = MergingController(shell)
    scenecut = SceneCutController(shell)
    depthedit = DepthEditController(shell)
    depthedit.splatting = splatting      # the editor's 3D view renders through Splatting's preview
    edgefill = EdgeFillController(shell)
    edgefill.merging = merging           # an H.264 / H.265 clip is written again with Merging's encoder settings
    # Put Back's Depth Map (user, 2026-10-15; core.removal.putback): the clean clip's depth map and
    # Splatting's settings for it -- its geometry, as Splatting rendered it
    def _putback_geometry(core_name):
        base = str(splatting.settings.value("input_depth_maps") or "")
        if not base:
            return None
        from studio import chapter_paths
        for folder in chapter_paths.finished_chain(base) if os.path.isdir(base) else [os.path.dirname(base)]:
            for ext in (".mp4", ".mkv", ".mov"):
                cand = os.path.join(folder, f"{core_name}_depth{ext}")
                if os.path.isfile(cand):
                    return cand, splatting.build_settings()
        return None
    from core.removal import putback as _putback
    _putback.GEOMETRY = _putback_geometry
    # Splatting's and Inpainting's console tabs open up with their Debug option (v1)
    for tab, ctl, key in (("splatting", splatting, "debug_logging"), ("inpainting", inpainting, "debug_mode")):
        def _debug(changed=key, _v=None, tab=tab, ctl=ctl, key=key):
            if changed == key:
                logs.set_tab_debug(tab, bool(ctl.settings.value(key)))
        _debug()
        ctl.settings.valueChanged.connect(_debug)
    # Studio > Use Beginner Mode (v1 beginner_mode.py): after the stages, whose
    # settings it pins and whose help texts it maps to plain language.
    beginner = Beginner(shell, {"depthcrafter": depthcrafter, "splatting": splatting, "inpainting": inpainting,
                                "inpainting_v2": inpainting_v2, "merging": merging, "scenecut": scenecut,
                                "depthedit": depthedit})
    # v1's shell request_job: one stage renders at a time, and a stage about
    # to start has every other stage's resident GPU state released first.
    jobs = GpuJobs()
    for stage_id, name, ctl in (("depthcrafter", "DepthCrafter", depthcrafter), ("splatting", "Splatting", splatting),
                                ("inpainting", "Inpainting", inpainting), ("inpainting", "Inpainting V2", inpainting_v2),
                                ("merging", "Merging", merging), ("scenecut", "Scene Cut", scenecut),
                                ("depthedit", "Depth Map Editor", depthedit), ("edgefill", "Edge Fill", edgefill)):
        jobs.register(stage_id, name, ctl)
    if inpainting_m2s is not None:
        # its own id: GpuJobs keeps it from running beside V1 / V2, and has them let go of the card
        jobs.register("inpainting_m2s", "Inpainting M2S", inpainting_m2s)
        inpainting_m2s.splatting, inpainting_m2s.inpainting = splatting, inpainting
    # The live job numbers (DepthCrafter, Inpainting) and the VRAM readout.
    job_stats = JobStats()
    _log_model.BAR_LISTENERS.append(job_stats.feed_bar)
    inpainting.job_stats = job_stats
    inpainting_v2.job_stats = job_stats     # V1's numbers on V2's page too (user, 2026-10-24)
    depthcrafter.job_stats = job_stats      # its ETA (user, 2026-10-12)
    # M2S (personal/m2svid) reports under Inpainting's numbers, as V1 does (user, 2026-10-24)
    _m2s_stats = ((("inpainting", "m2s", inpainting_m2s),) if inpainting_m2s is not None else ())
    if inpainting_m2s is not None:
        inpainting_m2s.job_stats = job_stats
    # The owner: which of Inpainting's pages runs the job -- each page shows its own job's numbers
    # only, not another's (user, 2026-10-24: M2S's ring mirrored V1's run)
    for stage_id, owner, ctl in (("depthcrafter", "", depthcrafter), ("inpainting", "v1", inpainting),
                                 ("inpainting", "v2", inpainting_v2), ("merging", "", merging)) + _m2s_stats:
        def _watch(stage_id=stage_id, owner=owner, ctl=ctl, was=[False]):
            busy = bool(ctl.property("busy"))
            if busy and not was[0]:
                job_stats.begin(stage_id, owner)
            elif was[0] and not busy:
                job_stats.end(stage_id)     # no bars read for it after (audit 2026-09-28)
            was[0] = busy
        ctl.stateChanged.connect(_watch)
    # The console's tabs: shared code logs to the tab of the job running --
    # the stage whose busy flag is up, as Main's status line shows it.
    def _active_job(*_):
        logs.set_active(next((sid for sid, _n, c in jobs._stages if c.property("busy")), None))
    for _sid, _name, ctl in jobs._stages:
        ctl.stateChanged.connect(_active_job)
    vram = VramMonitor()
    frame = WindowFrame()
    console_frame = WindowFrame()
    from .updater import Updater
    updater = Updater()
    updater.job_running = lambda: (jobs.running() or ("", ""))[1]   # Install waits for a job (bug sweep A1)
    models = ModelsController(shell)
    # the Segment page's Download Weights runs Settings > Studio > Models' download: a child process with
    # a Stop (user, 2026-10-21; the editor's own ran in-process and couldn't be stopped)
    depthedit.models = models
    # a download waits for a job; the folder can't change during one (the busy flag, as Main shows it)
    models.job_running = lambda: next((name for _sid, name, c in jobs._stages if c.property("busy")), "")
    from .folder_browser import FolderBrowser
    folders = FolderBrowser()            # the Columns folder picker's disk (FolderPicker.qml)

    # what the picker's Move to Recycle Bin must leave alone (user, 2026-10-14): the open project
    # (or a folder holding it), and every folder a running job uses -- or anything in one
    def _protected():
        held = []
        if shell.projectFolder:
            held.append((shell.projectFolder, "the open project", False))
        running = jobs.running()
        if running is not None:
            values = getattr(running[2], "_values", None)
            try:
                vals = values() if callable(values) else {}
            except Exception:
                vals = {}
            for v in vals.values():
                if isinstance(v, str) and len(v) > 3 and os.path.exists(v):
                    held.append((v, f"a folder {running[1]}'s job is using", True))
        return held
    import studio.folder_browser as _folder_browser
    _folder_browser.PROTECTED = _protected
    console_backdrop = Backdrop()        # the console window's glass (user, 2026-10-01)
    # Glasses-Free 3D: the previews woven for an SR display (user, 2026-10-11)
    from .sr_display import GlassesFree3D
    glasses = GlassesFree3D(shell)
    objects = {"Shell": shell, "Backdrop": backdrop, "WinFrame": frame, "ConsoleFrame": console_frame,
               "ConsoleBackdrop": console_backdrop,
               "Log": logs.tab("all"), "Logs": logs, "Updater": updater,
               "DepthCrafter": depthcrafter, "Inpainting": inpainting, "InpaintingV2": inpainting_v2,
               "Splatting": splatting, "Merging": merging, "SceneCut": scenecut,
               "DepthEdit": depthedit, "EdgeFill": edgefill, "Beginner": beginner, "GpuJobs": jobs,
               "JobStats": job_stats, "Vram": vram, "Folders": folders, "Glasses3D": glasses, "Models": models}
    if inpainting_m2s is not None:
        objects["InpaintingM2S"] = inpainting_m2s

    # Real per-pixel alpha, so the tint over the glass is translucent.
    QQuickWindow.setDefaultAlphaBuffer(True)

    engine = QQmlApplicationEngine()
    # The Splatting preview's frames: image://splatpreview/frame/<serial>.
    engine.addImageProvider("splatpreview", splatting.preview.provider)
    # ...and Merging's: image://mergepreview/frame/<serial>.
    engine.addImageProvider("mergepreview", merging.preview.provider)
    # a still per clip for lists (Merging's Upcoming Jobs)
    from studio.clip_thumbs import ClipThumbProvider
    engine.addImageProvider("clipthumb", ClipThumbProvider())
    # ...and Fill Edges' grid: one eye at the frame its strip is widest
    from studio.clip_thumbs import EdgeThumbProvider
    engine.addImageProvider("edgethumb", EdgeThumbProvider())
    # ...and the Depth Map Editor's picture: image://depthedit/frame/<serial>.
    engine.addImageProvider("depthedit", depthedit.provider)
    # the film grain's tile over the window: image://grain/<settings>
    from studio.grain import GrainProvider
    engine.addImageProvider("grain", GrainProvider())
    engine.addImportPath(str(HERE / "qml"))
    ctx = engine.rootContext()
    for name, obj in objects.items():
        ctx.setContextProperty(name, obj)
    engine.loadFromModule("Studio", "Main")
    roots = engine.rootObjects()
    if roots:
        backdrop.bind(roots[0])
        # a Windows notification when a job finishes while the window is
        # minimized (user, 2026-10-05; studio.desktop_notify)
        from . import outcome
        from .desktop_notify import JobNotifier
        notifier = JobNotifier(roots[0], REPO / "assets" / "StereoCrafter.ico", parent=app)
        outcome.FINISH_LISTENERS[:] = [notifier.job_finished]
        objects["JobNotifier"] = notifier
        objects["Shell"].apply_app_icon(notifier)      # the one picked in the theme popout
    logger.info("StereoCrafter Studio ready.")
    return engine, objects


#: Each job stage's Stop now slot (audit #13): the one the page's Stop runs.
_STOP_NOW = {"DepthCrafter": "cancel", "SceneCut": "abort", "Splatting": "stop", "Inpainting": "stop",
             "InpaintingV2": "stop", "InpaintingM2S": "stop", "Merging": "stop", "EdgeFill": "stop"}


def _personal_m2svid(shell):
    """Inpainting's M2SVid engine (personal/m2svid: the user's own, not released), or None where that
    folder isn't -- or can't load, which is logged and leaves the rest of Studio as it is."""
    from .shell import personal_m2svid_page
    if not personal_m2svid_page():
        return None
    try:
        from personal.m2svid.controller import M2SvidController
        return M2SvidController(shell)
    except Exception:
        logger.exception("personal/m2svid is there but could not load; Inpainting runs without it")
        return None


def stop_running_jobs_now(objects) -> list:
    """Quit during a job (audit #13, user 2026-09-29): every running stage is
    told to stop NOW and the encoders Studio started are ended, so the item in
    progress is discarded, not finished. Returns the stages that were running."""
    from .child_procs import kill_encoders
    running = []
    for name, slot in _STOP_NOW.items():
        if name not in objects:             # a personal/ engine this install doesn't have
            continue
        try:
            if objects[name].property("busy"):
                running.append(name)
                getattr(objects[name], slot)()
        except Exception:
            logger.exception("Could not stop %s at quit", name)
    if running:
        logger.info("Quit during a job (%s): stopping it now.", ", ".join(running))
        kill_encoders()
    return running


#: How long the window may be gone before the process is ended anyway.
EXIT_WATCHDOG_S = 20.0


def _start_exit_watchdog(seconds: float = EXIT_WATCHDOG_S) -> None:
    """User, 2026-10-02: after a Stop After Clip, closing the window left
    Studio running hidden (4 GB, holding the launcher's log, so no new Studio
    would open) and logged nothing. From the moment the window closes, this
    gives the shutdown `seconds`; past that it writes every thread's stack to
    the log -- what was stuck -- and ends the process."""
    import faulthandler
    import threading
    import time as _time

    def run():
        _time.sleep(seconds)
        try:
            logger.error("Studio was still running %.0f s after its window closed: every thread's stack "
                         "follows, then the process is ended.", seconds)
            out = sys.__stderr__ or sys.stderr
            faulthandler.dump_traceback(file=out, all_threads=True)
            out.flush()
        except Exception:
            pass
        try:
            from studio.child_procs import kill_encoders
            kill_encoders()
        except Exception:
            pass
        try:
            logging.shutdown()
        finally:
            os._exit(0)

    threading.Thread(target=run, name="exit-watchdog", daemon=True).start()


def quit_with_main_window(app, engine) -> None:
    """The main window gone is Studio closing (user, 2026-10-02). Qt's own
    quit first asks every other window to close, and one may refuse -- Detect
    Text refuses while it thinks its mask is rendering -- which cancels the
    quit: Studio stayed running with nothing on screen, and as aboutToQuit
    never came, neither the shutdown nor the exit watchdog ran. exit() ends
    the event loop without asking (aboutToQuit still comes, so the shutdown
    runs). A close the main window turned down (a job running) leaves it
    visible."""
    win = engine.rootObjects()[0]
    win.visibleChanged.connect(lambda shown: None if shown else app.exit(0))


def main() -> int:
    # one Studio at a time (bug sweep 2026-10-09, A1): a second one shared the
    # config files and the GPU -- this one brings the running window forward
    from studio import single_instance
    if not single_instance.claim():
        single_instance.bring_forward()
        print("StereoCrafter Studio is already running: brought its window forward.", flush=True)
        return 0
    QQuickStyle.setStyle("Basic")  # the native Windows style refuses custom backgrounds
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    from studio import taskbar_win
    taskbar_win.set_process_app_id()  # before any window: one taskbar button with the pin
    app = QGuiApplication(sys.argv)
    app.setApplicationName("StereoCrafter Studio")
    app.setOrganizationName("StereoCrafter")
    icon = REPO / "assets" / "StereoCrafter.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    engine, objects = build(app)
    if not engine.rootObjects():
        return 1
    taskbar_win.set_window_properties(int(engine.rootObjects()[0].winId()), objects["Shell"].app_icon_path())

    # Quit while a job runs is a forced stop (audit #13, user 2026-09-29): the
    # window asked first. The job is told to stop NOW and its encoders are
    # ended, so its partial output is discarded rather than finished; Studio
    # then exits even if a GPU thread is still unwinding.
    forced = []

    def _shutdown():
        _start_exit_watchdog()
        try:
            objects["Shell"].answer_all_no()      # a delete still being asked about: kept
        except Exception:
            logger.exception("Could not answer the open delete questions")
        forced.extend(stop_running_jobs_now(objects))
        # each on its own (audit 2026-09-28, minor): one that raised skipped
        # every stage after it, and the shell's own save
        # Edge Fill first: it waits for its fill (ROSE ended, the clip's files as they were), well
        # inside the exit watchdog, before the slower stages' shutdowns (bug sweep 2026-10-14b)
        for name in ("EdgeFill", "Vram", "DepthCrafter", "Inpainting", "InpaintingV2", "InpaintingM2S", "Splatting",
                     "Merging", "SceneCut", "DepthEdit", "Models"):
            if name not in objects:         # a personal/ engine this install doesn't have
                continue
            try:
                objects[name].shutdown()
            except Exception:
                logger.exception("%s did not shut down cleanly", name)
        try:
            objects["Shell"].save()
        except Exception:
            logger.exception("Could not save the Studio settings")

    app.aboutToQuit.connect(_shutdown)
    quit_with_main_window(app, engine)
    code = app.exec()
    if forced:
        # A cut-off job's thread may still be inside CUDA or a pipe: don't let
        # it hold the process (or its GPU memory) open after the window is gone.
        from studio.child_procs import kill_encoders
        kill_encoders()
    # Always out now (user, 2026-10-02): a plain return made Python wait for
    # every non-daemon thread -- a stage's worker, a torch.compile pool -- and
    # one that never ended kept Studio alive, hidden, after the window closed.
    # Everything that must happen at quit has (the stages' shutdown, the
    # settings saved); nothing relies on atexit. Name what was left, for the log.
    import threading
    left = [t.name for t in threading.enumerate()
            if t is not threading.main_thread() and t.is_alive() and not t.daemon]
    if left:
        logger.info("Closing with threads still running (ended with the process): %s", ", ".join(left))
    logger.info("Studio closed.")
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
