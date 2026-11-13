"""controller.py -- Edge Fill as QML sees it (Extra > Edge Fill; user, 2026-10-14).

Refills the strip a deep 3D shift leaves at a frame's edge (core/removal/edge_fill.py,
ROSE): a folder of merged clips shown as a grid of stills, each clip's strip measured
from its splat mask (remembered beside the clips), the ticked ones filled one after
another. It began as Merging's Fill Edges dialog (2026-10-13); Merging's button now
opens this page on its own folders, and its Upcoming Jobs' right-click fills one clip
here (fillClip).

* scan(): the merged folder's clips, each with its splat mask looked up -> scanned;
  then, on the same thread, each clip's strip (remembered ones at once) -> measured.
* fill(names) / fillClip(path, merged, masks): a job like any stage's -- GpuJobs'
  claim, busy, Stop -- logging to the console's Extra tab with a bar per stretch
  (reading, each ROSE window's steps, blend + write).

Bug sweep 2026-10-14: a look doesn't run during another stage's job (it reads videos);
each look is numbered and an older one's results are dropped; Fill works on the folder
the grid was looked at in; quit waits for the job (its ROSE process ended, its work
folder gone); the folders follow a project switch (Merging's Output and Mask folders).

Chapters (user, 2026-10-14: "a chapter dropdown like the stages have"): pickChapter points
the folders at a chapter's Merged and Splat folders (the project's merging stage paths:
"output" and "mask"); a hand edit leaves it ("Manual paths"); the pick is kept per project
(project_memory), and a project Edge Fill hasn't been on yet takes Merging's chapter or folders.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from studio import chapter_paths, project_memory
from . import settings as S

logger = logging.getLogger("studio.stages.edgefill")

HELP: Dict[str, str] = {
    "method": (
        "How the strip is dealt with. Fill: the Inpainter paints it (the settings below). Black Bar: the strip "
        "is painted black -- on both eyes, the same width, so the two still match. Zoom: the strip is cropped "
        "off both eyes and the rest scaled back up to the frame, a little of the top and bottom going too, "
        "so the shape stays. Black Bar and Zoom need no models and take one quick pass; each clip is covered "
        "by its own measured strip, so Zoom's amount can differ from clip to clip."),
    "merged_folder": (
        "The merged clips to look at and fill: Full SBS files and / or their _L / _R eyes, as Merging writes "
        "them (<clip>_<width>_merged_full_sbs...)."),
    "depth_folder": (
        "The clips' depth maps (Splatting's depth-map folder): Depth Aware reads each clip's map from here -- "
        "<clip>_depth.mp4, or in its finished folder -- to tell what is near the strip. Left empty, Splatting's "
        "Input Depth Maps folder is used; a clip with no map has its depth measured from its two eyes instead "
        "(less steady). Picking a chapter fills it in."),
    "mask_folder": (
        "The clips' splats from Splatting (<clip>_<width>_splatted2...), here or in its finished folder: each "
        "one's mask says how wide the strip at the frame's edge is, row by row."),
    "inpainter": (
        "What paints the strip. MiniMax-Remover (the default): tested against ROSE on eight shots with their "
        "edge hidden, its fill met the picture beside it more closely on all eight and flickered less on "
        "seven, the most on pans, in about 2.6x less time; it runs its own 6 steps and blanks the strip "
        "itself, so Steps and Pre-fill are ROSE's only. Needs MiniMax-Remover in Settings > Studio > Models "
        "(2.6 GB); its weights are for non-commercial use only (CC-BY-NC-4.0). ROSE: the inpainter Edge Fill "
        "began with (set up in the Depth Map Editor's Erase)."),
    "mmr_steps": (
        "MiniMax-Remover's denoising steps per window. 6 (the default) scored a little better than 12 on the "
        "eight test shots and takes about 60% of 12's time; 12 is the authors' own example."),
    "steps": (
        "ROSE's denoising steps per window. 6 matched 50 when tested; fewer is faster but loses fine detail "
        "(3-4 softened a wall's ridges)."),
    "size": (
        "The resolution ROSE works the edge at. 560: the fill keeps more texture (ridges, grain) and is quicker. "
        "784: lines running into the edge (a roof, a railing) carry on straighter, but texture softens "
        "and it takes about twice as long. Native: the clip's own size, in overlapping tiles near ROSE's own "
        "size -- the sharpest and closest to the real picture (ridges, rivets, lines all kept), about 2.5x "
        "560's time; its colour is matched to the picture beside the strip (784's too)."),
    "prefill": (
        "What ROSE starts from in the strip -- it sees under its mask, so what is there steers its fill. "
        "Blur: a wide blur of the picture beside the strip, so ROSE invents what's there. Stretched: the "
        "last column before the strip copied across it, which ROSE tends to keep as horizontal streaks; "
        "on a picture that really is horizontal lines (a building's floors) it can be the closer guess."),
    "grain": (
        "Adds grain like the picture's next to the strip -- measured per brightness, the fill's own grain "
        "counted -- so the filled strip isn't smoother than the frame."),
    "both_edges": (
        "Splatting leaves the strip on the right eye's left edge, so that is the only edge looked at "
        "(and filled). Tick this to look at the right eye's right edge too, for a clip whose strip really "
        "is on that side. The strips are measured again when you change it."),
    "detail": (
        "Sharpens the Inpainter's fill to the picture's own fine detail. It works the edge smaller than the frame, "
        "so its fill comes back softer; the strength is measured per clip where it repainted real picture "
        "(the band just past the strip) and is only used when the fill really is softer."),
    "copy_with": (
        "What finds the real picture in other frames. AllTracker (the default) follows every pixel of the "
        "frames that show the strip's picture into the strip: tested on eight pans with the edge hidden, it "
        "came out sharper on seven and flickered less, and is a little quicker -- but it needs about 6 GB of "
        "graphics memory. SEA-RAFT borrows the motion beside the strip; 1 GB. Each downloads in Settings > "
        "Studio > Models; when the one picked isn't there the other is used."),
    "depth_aware": (
        "Leaves what is nearer than the background alone: a person or object passing over the frame's edge is "
        "real picture there (Splatting moved it over the edge), so the fill, its fade and the copies from other "
        "frames stop at it instead of painting over it. Depth is measured from the merged left and right eyes "
        "beside the strip; it adds a few seconds a clip."),
    "copy_frames": (
        "Before ROSE paints, copies the real picture into the strip from the frames where a pan or a move "
        "shows it -- signs, text and edges come back as they were, not invented -- and ROSE fills only what "
        "no frame shows. A still shot shows nothing new: Edge Fill sees that in a few seconds and leaves "
        "the strip to ROSE. Needs AllTracker (66 MB) or SEA-RAFT (79 MB) from Settings > Studio > Models -- "
        "Copy With picks which; about 0.5 s a frame."),
    "fill": (
        "Refills the strip at the edge of each ticked clip with the Inpainter. Each clip's files are written again in "
        "their own format with only the strip changed. The originals are kept in edge_fill_originals beside "
        "them, and filling a clip again starts from its original (the once-filled file goes to the Recycle "
        "Bin); delete that folder once you're happy with the fills."),
}


def _positive_int(text: str) -> int:
    n = int(float(text))
    if n < 1:
        raise ValueError(f"{n} is below 1")
    return n


_NUMERIC = {"steps": _positive_int}


class EdgeFillController(QObject):
    stateChanged = Signal()
    progressChanged = Signal()
    chapterChanged = Signal()
    #: the merged / mask folder changed, from anywhere (a chapter, Merging's button, a project switch)
    foldersChanged = Signal()
    toast = Signal(str, str, str)
    message = Signal(str, str, str)
    fieldRejected = Signal(str)
    #: the folder's clips: {"folder", "masks", "clips": [...], "rose", "minimax"} or {"error"}
    scanned = Signal("QVariantMap")
    #: one clip's strip, measured for the grid: {name, px, eyeWidth, frame, eye, edge, thumb, part}
    measured = Signal("QVariantMap")
    #: the clips filled this run changed (filledNow)
    filledChanged = Signal()
    #: which clips have a sidecar
    ownChanged = Signal()

    _sigScanned = Signal(object)
    _sigMeasured = Signal(object)
    _sigDone = Signal(object)
    _sigFilled = Signal(str)
    _sigProgress = Signal(float, str)
    #: the job thread, for shutdown to wait on (its ROSE process ends with it)
    JOIN_S = 30.0

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        stored = S.normalise(settings_store.load("edgefill", settings_store.CONFIG_DIR / "merging.json",
                                                 S.seed_from_merging))
        self._map = QQmlPropertyMap(self)
        for key, value in stored.items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = dict(stored)
        # the page's settings, and the clips' sidecars (user, 2026-11-07): a clip with one is filled with it
        self._global: Dict[str, Any] = dict(stored)
        self._own: Dict[str, Dict[str, Any]] = {}
        self._own_folder = ""
        self._map.valueChanged.connect(self._on_value_changed)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._scan = None                    # the last scan's result (None: none, or it failed)
        self._scanning = False
        self._busy = False
        self._stop = threading.Event()
        self._look = threading.Event()       # set: the grid's strip measuring stops
        self._shown = 0.0
        self._progress = 0.0                 # 0..1 over the whole job
        self._status = "Ready"
        self._count = ""                     # "2 / 5"
        self._filled_now: list = []          # the clips this run filled: green on the grid till the next
        self._gen = 0                        # each look's number: an older look's results are dropped
        self._thread = None                  # the job's thread
        self._follow_later = False           # a project switch during a fill: the folders after it
        self._chapters: list = []            # the project's chapters (the stages' dropdown)
        self._chapter_num = -1               # -1: Manual paths
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False       # the folders set by a chapter, not by hand
        self._restoring = False              # a project switch restoring the chapter: no toast
        # asked for during a fill, applied when it ends (bug sweep 2026-10-14b): a chapter (and
        # whether it was a quiet restore), Merging's folders
        self._pending_chapter = None
        self._pending_folders = None

        q = Qt.ConnectionType.QueuedConnection
        self._sigScanned.connect(self._on_scanned, q)
        self._sigMeasured.connect(self._on_measured, q)
        self._sigDone.connect(self._on_done, q)
        self._sigFilled.connect(self._on_filled, q)
        self._sigProgress.connect(self._on_progress, q)
        # the folders follow a project switch, as the stages' do: this page's own chapter or
        # folders for a project it has been on, else Merging's (user, 2026-10-14)
        if hasattr(shell, "projectChanged"):
            shell.projectChanged.connect(self.refreshChapters)
            from studio import project_memory
            project_memory.attach(self, "edgefill", shell, ("merged_folder", "mask_folder", "depth_folder"))
            shell.projectChanged.connect(lambda: QTimer.singleShot(0, self._follow_project))
            # project_memory restores the folders without a signal: the page looks again
            shell.projectChanged.connect(lambda: QTimer.singleShot(0, self.foldersChanged.emit))
            self.refreshChapters()

    # ---- settings ------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    help = Property("QVariantMap", lambda self: HELP, constant=True)
    sizes = Property("QVariantList", lambda self: list(S.SIZES), constant=True)
    prefills = Property("QVariantList", lambda self: list(S.PREFILLS), constant=True)
    inpainters = Property("QVariantList", lambda self: list(S.INPAINTERS), constant=True)
    methods = Property("QVariantList", lambda self: list(S.METHODS), constant=True)
    copiers = Property("QVariantList", lambda self: list(S.COPIERS), constant=True)
    mmrSteps = Property("QVariantList", lambda self: list(S.MMR_STEPS), constant=True)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        if key in _NUMERIC:
            try:
                _NUMERIC[key](str(value).strip())
            except (TypeError, ValueError, OverflowError):     # "inf" raised past this (sweep 2026-10-14)
                self._map.insert(key, self._last_good.get(key, S.DEFAULTS.get(key)))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value) if key in S.DEFAULTS else value
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        self._global[key] = coerced
        if key in ("merged_folder", "mask_folder"):
            if not self._chapter_applying:
                self._maybe_leave_chapter()
            self.foldersChanged.emit()
        elif key == "both_edges":
            self.foldersChanged.emit()           # the page looks again: each strip on the edges now asked for
        self._save_timer.start()

    @Slot()
    def save(self) -> None:
        settings_store.save("edgefill", S.normalise(self._global))

    # ---- sidecars: a clip's own settings (user, 2026-11-07: "like splatting where theres a create/update sidecar
    # button that saves whatever settings are currently displayed") ---------------------------------------------

    sidecarClips = Property("QVariantList", lambda self: sorted(self._own), notify=ownChanged)

    def _load_own(self, folder: str) -> None:
        folder = os.path.abspath(folder) if folder else ""
        if os.path.normcase(folder) == os.path.normcase(self._own_folder):
            return
        self._own_folder = folder
        self._own = S.load_clip_settings(folder) if folder else {}
        self.ownChanged.emit()

    def _save_sidecars(self) -> bool:
        if not self._own_folder or not os.path.isdir(self._own_folder):
            return False
        return S.save_clip_settings(self._own_folder, self._own)

    @Slot("QVariantList")
    def saveSidecar(self, names) -> None:
        """The settings the page shows, saved as clips `names`' sidecar (made, or replaced)."""
        names = [str(n) for n in (names or []) if str(n)]
        if not names or self._busy:
            return
        vals = S.clip_values(S.normalise(self._values()))
        new = sum(1 for n in names if n not in self._own)
        for n in names:
            self._own[n] = dict(vals)
        if not self._save_sidecars():
            self.toast.emit("warning", "Sidecar not saved", f"Couldn't write to {self._own_folder or 'the folder'}.")
            return
        self.ownChanged.emit()
        what = (f"{names[0]}" if len(names) == 1 else f"{len(names)} clips")
        self.toast.emit("success", "Sidecar saved" if len(names) == 1 else "Sidecars saved",
                        f"{what}: {S.describe(S.fill_options({**self._global, **vals}))}."
                        + ("" if new == len(names) else " (updated)"))

    @Slot(str)
    def loadSidecar(self, name: str) -> None:
        """Clip `name`'s sidecar settings onto the page (to look at, change, and save again)."""
        own = self._own.get(str(name or ""))
        if not own or self._busy:
            return
        for k, v in own.items():
            if self._map.value(k) != v:
                self._map.insert(k, v)
                self._on_value_changed(k, v)

    @Slot(str)
    def removeSidecar(self, name: str) -> None:
        """Clip `name` back on the page's settings: its sidecar forgotten."""
        if self._own.pop(str(name or ""), None) is not None:
            self._save_sidecars()
            self.ownChanged.emit()

    def _clip_options(self, name: str) -> Dict[str, Any]:
        own = self._own.get(name) or {}
        return S.fill_options({**self._global, **own})

    def depth_folder(self) -> str:
        """Depth Aware's depth maps: the page's folder, else Splatting's Input Depth Maps (its folder, when it
        names one map)."""
        folder = str(self._map.value("depth_folder") or "").strip()
        if not folder:
            sp = getattr(self, "splatting", None)
            folder = str(sp.settings.value("input_depth_maps") or "").strip() if sp is not None else ""
            if os.path.isfile(folder):
                folder = os.path.dirname(folder)
        return os.path.abspath(folder) if folder else ""

    def _folders(self):
        v = self._values()
        merged, masks = str(v.get("merged_folder") or "").strip(), str(v.get("mask_folder") or "").strip()
        return (os.path.abspath(merged) if merged else "", os.path.abspath(masks) if masks else "")

    @Slot(str, str)
    def useFolders(self, merged: str, masks: str) -> None:
        """Merging's Fill Edges button: this page on Merging's Output and Mask folders."""
        if self._busy:
            self.toast.emit("info", "Edge Fill is busy",
                            "It keeps its folders until its fill ends; Merging's are used after that.")
            # these folders, after the fill (a deferred follow dropped them for a remembered project)
            self._pending_folders = (str(merged or ""), str(masks or ""))
            return
        # Merging on a chapter: the same chapter here
        merging = getattr(self, "merging", None)
        num = int(merging.property("chapterNum")) if merging is not None else -1
        if num >= 0 and any(c["num"] == num for c in self._chapters):
            paths = self._chapter_paths(num)
            if chapter_paths.same_place(paths.get("output"), merged) and chapter_paths.same_place(paths.get("mask"), masks):
                self.pickChapter(num)
                return
        self._map.insert("merged_folder", str(merged or "").replace("\\", "/"))
        self._map.insert("mask_folder", str(masks or "").replace("\\", "/"))
        self._on_value_changed("merged_folder", self._map.value("merged_folder"))
        self._on_value_changed("mask_folder", self._map.value("mask_folder"))
        project_memory.needs_chapter(self)       # folders set: no longer waiting for a chapter

    def _follow_project(self) -> None:
        merging = getattr(self, "merging", None)
        if merging is None:
            return
        if self._busy:
            self._follow_later = True
            return
        # a project this page has been on keeps its own chapter / folders (project_memory) -- but
        # an empty pair remembered is no memory (it restored blank folders and stopped this)
        remembered = self._shell.rememberedStagePaths("edgefill") or {}
        # (nor are another project's folders -- project_memory dropped them, 2026-11-12)
        if self._chapter_num >= 0 or any(str(v or "").strip() and not project_memory.foreign(self._shell, v)
                                         for v in remembered.values()):
            return
        v = merging._values()
        self._restoring = True                   # a project switch: no "Chapter selected" toast
        try:
            self.useFolders(str(v.get("output_folder") or ""), str(v.get("mask_folder") or ""))
        finally:
            self._restoring = False

    # ---- chapters (the stages' dropdown) --------------------------------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)
    # on the project with no chapter and no folders yet: Look / Fill are refused (project_memory)
    needsChapter = Property(bool, lambda self: project_memory.needs_chapter(self), notify=chapterChanged)

    @Slot()
    def useProjectFolders(self) -> None:
        """A folder's "Use <project>'s folder": this page back on the open project."""
        project_memory.reland(self)

    def _chapter_paths(self, num: int) -> Dict[str, str]:
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        return self._shell.stagePaths("merging", num, name) or {}

    @Slot()
    def refreshChapters(self) -> None:
        """The project's chapters (a project switch too: its folders may have been restored)."""
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("edgefill")
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        self._chapter_expected = self._chapter_paths(self._chapter_num) if self._chapter_num >= 0 else {}
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        """The chapter's merged clips and their splats: the project's Merged and Splat folders
        for it, as Merging's chapter fills its Output and Mask (-1: Manual paths)."""
        if self._busy:
            # kept, and applied when the fill ends: a project switch during a fill left the picker
            # on the new project's chapter and the folders on the old one's (sweep 2026-10-14b)
            self._pending_chapter = (num, self._restoring)
            if not self._restoring:
                self.toast.emit("info", "Edge Fill is busy", "The chapter is picked once its fill ends.")
            self.chapterChanged.emit()          # the dropdown back on the chapter in use
            return
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("edgefill", {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._chapter_paths(num)
        self._chapter_applying = True
        try:
            for role, key in (("output", "merged_folder"), ("mask", "mask_folder")):
                if paths.get(role):
                    self._map.insert(key, paths[role].replace("\\", "/"))
                    self._on_value_changed(key, self._map.value(key))
            # Depth Aware's depth maps: the chapter's (Splatting's own Depth folder)
            depth = (self._shell.stagePaths("splatting", num, name) or {}).get("depth")
            if depth:
                self._map.insert("depth_folder", depth.replace("\\", "/"))
                self._on_value_changed("depth_folder", self._map.value("depth_folder"))
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("edgefill", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not self._restoring:                 # a project switch restores quietly
            self.toast.emit("info", "Chapter selected",
                            f"{name}\nMerged and Mask folders now point at this chapter.")

    def _maybe_leave_chapter(self) -> None:
        """A folder edited by hand: Manual paths (a job moving clips a finished level down isn't one)."""
        if self._chapter_num < 0:
            return
        for role, key in (("output", "merged_folder"), ("mask", "mask_folder")):
            exp = self._chapter_expected.get(role)
            if exp and not chapter_paths.same_place(exp, self._map.value(key)):
                self._chapter_num = -1
                self._shell.setStageChapter("edgefill", {})
                self.chapterChanged.emit()
                return

    # ---- the job's state -----------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    scanning = Property(bool, lambda self: self._scanning, notify=stateChanged)
    progress = Property(float, lambda self: self._progress, notify=progressChanged)
    status = Property(str, lambda self: self._status, notify=progressChanged)
    clipCount = Property(str, lambda self: self._count, notify=progressChanged)
    # the clips filled this run, each as it finishes -- a light green on the grid, as the clip
    # lists show what a run finished (user, 2026-10-18); cleared when the next fill starts
    filledNow = Property("QVariantList", lambda self: list(self._filled_now), notify=filledChanged)

    def is_busy(self) -> bool:
        return self._busy

    def holds_gpu(self) -> bool:
        return False                         # ROSE runs in its own process, gone when a fill ends

    def release_gpu(self) -> None:
        pass

    @Slot(float, str)
    def _on_progress(self, value: float, status: str) -> None:
        self._progress = value
        if status:
            self._status = status
        self.progressChanged.emit()

    @Slot(str)
    def _on_filled(self, name: str) -> None:
        if name not in self._filled_now:
            self._filled_now.append(name)
            self.filledChanged.emit()

    def _set_busy(self, on: bool) -> None:
        self._busy = on
        self.stateChanged.emit()

    # ---- the grid: the folder's clips and their strips ---------------------------------------

    @Slot()
    def scan(self) -> None:
        """The merged folder's clips, each with its splat mask looked up -> scanned; then each
        one's strip, for the grid -> measured (remembered ones at once, the rest one by one)."""
        if project_memory.refuse_start(self):
            return
        merged, masks = self._folders()
        self._look.set()                     # an earlier look's measuring, if still going
        look = self._look = threading.Event()
        self._gen += 1
        gen = self._gen
        # a look reads every mask and still: never during another stage's job (sweep 2026-10-14)
        jobs = getattr(self, "jobs", None)
        other = jobs.running() if jobs is not None else None
        if self._busy or (other is not None and other[2] is not self):
            self._sigScanned.emit({"gen": gen, "waiting": other[1] if other and other[2] is not self else "Edge Fill"})
            return
        self._scanning = True
        self.stateChanged.emit()
        both = bool(self._values().get("both_edges", False))     # the right eye's right edge too

        def run():
            from core.removal import edge_fill, minimax_env, rose_env
            measure = []
            try:
                if merged and os.path.isdir(merged):
                    edge_fill.sweep(merged)          # a quit mid-fill's work folder
                if not merged:
                    info = {"error": "Pick the folder of merged clips (Merged Folder)."}
                elif not os.path.isdir(merged):
                    info = {"error": f"The merged folder isn't there:\n{merged}"}
                else:
                    clips = []
                    for g in edge_fill.find_groups(merged):
                        mask = edge_fill.find_mask(g, masks) if masks else None
                        if g.kind == "half_sbs" and not ({"L", "R"} & set(g.files)):
                            state, note = "no", "Half SBS: fill its _L / _R files instead"
                        elif mask is None:
                            state, note = "no", "no splat mask in the Mask folder"
                        elif edge_fill.done(g):
                            state, note = "done", "already filled"
                        else:
                            state, note = "ready", os.path.basename(mask)
                        if mask is not None and state != "no":
                            measure.append((g, mask))
                        clips.append({"name": g.name, "files": sorted(os.path.basename(p) for p in g.files.values()),
                                      "state": state, "note": note, **self._thumb(g, "R"),
                                      # its hi-res splat, for "Open Hi-Res Splat in Explorer" (user, 2026-10-18)
                                      "mask": mask.replace("\\", "/") if mask else ""})
                    info = {"folder": merged.replace("\\", "/"), "masks": masks.replace("\\", "/"), "clips": clips,
                            "rose": rose_env.ready(), "minimax": minimax_env.available()}
                    if not clips:
                        info = {"error": f"No merged clips (Full SBS or _L / _R) in:\n{merged}"}
            except Exception as e:
                logger.exception("Edge Fill: could not look through %s", merged)
                info = {"error": f"Could not look through the folder:\n\n{e}"}
            info["gen"] = gen
            self._sigScanned.emit(info)
            if "error" in info:
                return                           # no strips under an error
            todo = []
            for g, mask in measure:
                if look.is_set():
                    return
                w = edge_fill.remembered(g, mask, both)
                if w:
                    self._sigMeasured.emit({"gen": gen, "name": g.name, **w, **self._thumb(g, w["eye"])})
                else:
                    todo.append((g, mask))
            for g, mask in todo:
                if look.is_set():
                    return
                try:
                    m = edge_fill.measure(mask, look)
                    w = dict(edge_fill.worst(m, both), eyeWidth=m["width"])
                    edge_fill.remember(g, mask, w, both)
                    logger.info(f"Edge Fill: measured {g.name}: "
                                + (f"{w['px']} px strip, {w['eye']} eye {w['edge']} edge, widest at frame {w['frame']}"
                                   if w["px"] else "no strip"))
                    self._sigMeasured.emit({"gen": gen, "name": g.name, **w, **self._thumb(g, w["eye"])})
                except Exception as e:
                    if look.is_set():
                        return
                    logger.warning("Edge Fill: could not measure %s: %s", g.name, e)
                    self._sigMeasured.emit({"gen": gen, "name": g.name, "px": -1})
        threading.Thread(target=run, name="fill-edges-scan", daemon=True).start()

    @staticmethod
    def _thumb(g, eye):
        """Which file shows `eye` of merged clip `g`, and which half of it."""
        def ver(path):           # the still's URL changes with the file: a filled clip's still
            try:                 # was Qt's cached pre-fill picture (bug sweep 2026-10-14)
                return str(os.stat(path).st_mtime_ns)    # text: a QML int is 32-bit (it overflowed)
            except OSError:
                return "0"
        def original(path):      # its unfilled original, kept beside it (user, 2026-11-07: play the old one too)
            from core.removal import edge_fill
            kept = edge_fill.kept_original(path)
            return kept.replace("\\", "/") if kept else ""
        if eye in g.files:
            return {"thumb": g.files[eye].replace("\\", "/"), "part": "whole", "ver": ver(g.files[eye]),
                    "original": original(g.files[eye])}
        sbs = g.files.get("sbs")
        if sbs:
            return {"thumb": sbs.replace("\\", "/"), "part": "left" if eye == "L" else "right", "ver": ver(sbs),
                    "original": original(sbs)}
        return {"thumb": "", "part": "whole", "ver": "0", "original": ""}

    @Slot(object)
    def _on_scanned(self, info) -> None:
        if info.get("gen") != self._gen:
            return                           # an older look's, finished after a newer one began
        self._scan = info if "error" not in info and "waiting" not in info else None
        if self._scan is not None:
            self._load_own(str(info.get("folder") or ""))
        self._scanning = False
        self.stateChanged.emit()
        self.scanned.emit(info)

    @Slot(object)
    def _on_measured(self, info) -> None:
        if info.get("gen") == self._gen:
            self.measured.emit(info)

    @Slot(result=str)
    def otherStage(self) -> str:
        """The stage whose job is running now ("" for none, or Edge Fill's own)."""
        jobs = getattr(self, "jobs", None)
        other = jobs.running() if jobs is not None else None
        return other[1] if other is not None and other[2] is not self else ""

    @Slot()
    def stopLooking(self) -> None:
        """The page went away: its measuring stops."""
        self._look.set()

    # ---- filling ------------------------------------------------------------------------------

    @Slot("QVariantList")
    def fill(self, names) -> None:
        """Fill the edges of the last scan's clips called `names`, one after another."""
        if self._scan is None or project_memory.refuse_start(self):
            return
        # the folders the grid was looked at in, not the fields as typed since (sweep 2026-10-14)
        merged = os.path.abspath(self._scan["folder"])
        masks = os.path.abspath(self._scan["masks"]) if self._scan.get("masks") else ""
        self._run([str(n) for n in (names or [])], merged, masks)

    @Slot(str, str, str)
    def fillClip(self, path, merged, masks) -> None:
        """Merging's Upcoming Jobs right-click (user, 2026-10-14): fill the edges of the merged
        clip made from inpainted clip `path`, in `merged` with its splat in `masks`, now."""
        from core.removal import edge_fill, minimax_env, rose_env
        if self._busy:
            self.toast.emit("warning", "Edge Fill is busy", "Wait for its job to finish, then try again.")
            return
        base = os.path.basename(str(path))
        m = re.match(r"^(?P<stem>.+)_(?P<w>\d+)_inpainted_", base, re.IGNORECASE)
        if not str(merged or "").strip() or not str(masks or "").strip():
            # abspath("") is Studio's own folder (sweep 2026-10-14)
            self.toast.emit("warning", "No folders", "Set Merging's Output and Mask folders first.")
            return
        merged, masks = os.path.abspath(str(merged)), os.path.abspath(str(masks))
        groups = [g for g in edge_fill.find_groups(merged) if m and g.stem == m["stem"]]
        groups.sort(key=lambda g: (g.width != m["w"], g.name))     # the same width first
        if not groups:
            self.toast.emit("warning", "Not merged yet",
                            f"No merged clip of {base} in Merging's output folder: merge it first.")
            return
        g = groups[0]
        if edge_fill.find_mask(g, masks) is None:
            self.toast.emit("warning", "No splat mask", f"{g.name}: its splat mask isn't in the Mask folder.")
            return
        o = self._clip_options(g.name)           # its sidecar's settings, when it has one
        if o["method"] != "Fill":
            pass                                 # Black Bar / Zoom need no model
        elif o["inpainter"] == "MiniMax-Remover":
            if not minimax_env.available():
                self.toast.emit("warning", "MiniMax-Remover isn't downloaded", minimax_env.missing_message())
                return
        elif not rose_env.ready():
            self.toast.emit("warning", "ROSE isn't set up", "Depth Map Editor > Erase > Set Up ROSE first.")
            return
        if edge_fill.done(g):
            self.toast.emit("info", "Already filled",
                            f"{g.name} has had its edges filled. To fill it again, tick it in Extra > Edge Fill.")
            return
        self._run([g.name], merged, masks)

    def _run(self, names, merged, masks) -> None:
        """Fill the edges of `merged`'s clips called `names`, one after another."""
        if not names or self._busy:
            return
        jobs = getattr(self, "jobs", None)
        if jobs is not None and not jobs.claim("edgefill", self):
            return                               # refused: the grid's measuring goes on
        self._look.set()
        depth_folder = self.depth_folder()
        # each clip's settings: its sidecar, else the page's (user, 2026-11-07), taken now
        shared = S.fill_options(self._global)
        per_clip = {n: self._clip_options(n) for n in names}
        owned = {n for n in names if n in self._own}
        # an H.264 / H.265 clip is written again with Merging's encoder settings (app.py sets .merging)
        merging = getattr(self, "merging", None)
        enc = merging._values() if merging is not None else {}
        self._stop.clear()
        self._progress, self._status, self._count = 0.0, "Starting...", f"0 / {len(names)}"
        self.progressChanged.emit()
        self._filled_now = []
        self.filledChanged.emit()
        self._set_busy(True)

        def run():
            # whatever happens, the page hears the job ended: a raise after the job's own try
            # (a console write) left it busy until a restart (sweep 2026-10-14)
            try:
                job()
            except BaseException:
                logger.exception("Edge Fill's job ended badly")
                self._sigDone.emit({"filled": [], "skipped": [], "failed": ["the job ended unexpectedly"],
                                    "stopped": self._stop.is_set()})

        def job():
            import sys
            import time
            from core.common.cli_utils import draw_progress_bar, format_duration
            from core.removal import edge_fill
            from core.removal.erase import Stopped
            result = {"filled": [], "skipped": [], "failed": []}
            began = time.monotonic()
            # The console's bars, one per stretch of a clip's fill (user, 2026-10-14: like DC's
            # and Inpainting's): reading the edges, then each ROSE window's steps (erase.step_bar,
            # as ROSE reports them), then the blend and the files written. fill()'s progress:
            # 0-0.08 reading, ROSE up to 0.75, then the rest.
            # Copy From Other Frames its own bar (2026-11-03: its lines drew in "Blend + write")
            STRETCH = {"read": (0.0, 0.08, "Reading edges"), "copy": (0.08, 0.25, "Copy from frames"),
                       "finish": (0.75, 1.0, "Blend + write"), "cover": (0.08, 1.0, "Covering")}
            bar = {"open": False, "phase": None, "t0": 0.0}

            def end_bar():
                # a bar cut short (stopped, failed, skipped) still ends its line: the next log
                # line gets a row of its own
                if bar["open"]:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    bar["open"] = False

            def phase_of(frac, text):
                if text.startswith(("ROSE", "MiniMax")):
                    return "rose"
                if text.startswith("Copy From Other Frames"):
                    return "copy"
                if text.startswith(("Black Bar", "Zoom")):     # cover(): one pass per file
                    return "cover"
                return "read" if frac < 0.08 else "finish"

            def draw_bar(frac, text):
                try:                               # a console that can't show the bar never ends the fill
                    _draw_bar(frac, text)
                except (UnicodeError, OSError, ValueError):
                    pass

            def _draw_bar(frac, text):
                phase = phase_of(frac, text)
                if phase != bar["phase"]:
                    if bar["open"] and bar["phase"] in STRETCH:       # the stretch before it, finished
                        draw_progress_bar(1, 1, bar_length=40, prefix=f"  {STRETCH[bar['phase']][2]}:",
                                          suffix=f"[{format_duration(time.monotonic() - bar['t0'])}]", keep_suffix=True)
                    bar.update(open=False, phase=phase, t0=time.monotonic())
                if phase == "rose":
                    return                                         # ROSE's own step bars
                lo, hi, label = STRETCH[phase]
                part = max(0.0, min(1.0, (frac - lo) / (hi - lo)))
                took = time.monotonic() - bar["t0"]
                draw_progress_bar(int(round(part * 1000)), 1000, bar_length=40, prefix=f"  {label}:",
                                  suffix=f"{text[:40]} [{format_duration(took)}]".ljust(56), keep_suffix=True)
                bar["open"] = part < 1

            logger.info(f"=== Edge Fill: {len(names)} clip(s); {S.describe(shared)}"
                        + (f"; {len(owned)} with a sidecar" if owned else "") + " ===")
            try:
                from studio.gpu_jobs import wait_for_gpu
                wait_for_gpu(self)
                groups = {g.name: g for g in edge_fill.find_groups(merged)}
                for k, name in enumerate(names):
                    if self._stop.is_set():
                        break
                    g = groups.get(name)
                    mask = edge_fill.find_mask(g, masks) if g is not None else None
                    if g is None or mask is None:
                        result["skipped"].append(f"{name}: its files or splat mask are gone")
                        continue
                    self._count = f"{k + 1} / {len(names)}"
                    o = per_clip[name]
                    logger.info(f"[{k + 1}/{len(names)}] {name}"
                                + (f" -- its sidecar: {S.describe(o)}" if name in owned else ""))
                    started = time.monotonic()
                    bar["phase"] = None

                    def progress(frac, text, k=k):
                        now = time.monotonic()
                        # twice a second: no redraw storm -- but a new stretch always, so its bar
                        # ends the one before before ROSE's step bars start
                        if frac < 1 and now - self._shown < 0.5 and phase_of(frac, text) == bar["phase"]:
                            return
                        self._shown = now
                        self._sigProgress.emit((k + min(frac, 1.0)) / len(names), f"{name}: {text}")
                        # the console's bars, drawn in the theme's accent like the stages' own
                        # (log_model.parse_bar)
                        draw_bar(frac, text)
                    try:
                        if o["method"] != "Fill":
                            r = edge_fill.cover(g, mask, progress, self._stop, method=o["method"], settings=enc,
                                                both_edges=o["both_edges"])
                        else:
                            r = edge_fill.fill(g, mask, progress, self._stop, steps=o["steps"], grain=o["grain"],
                                               settings=enc, rose_height=o["height"], detail=o["detail"],
                                               both_edges=o["both_edges"], prefill=o["prefill"],
                                               copy_frames=o["copy_frames"], copier=o["copier"],
                                               depth_aware=o["depth_aware"], depth_folder=depth_folder,
                                               inpainter=o["inpainter"])
                    except Stopped:
                        end_bar()
                        logger.info(f"  Stopped: {name} is as it was.")
                        break
                    except Exception as e:
                        end_bar()
                        logger.exception("Edge Fill failed on %s", name)
                        result["failed"].append(f"{name}: {e}")
                        continue
                    end_bar()
                    if r.get("skipped"):
                        result["skipped"].append(f"{name}: {r['skipped']}")
                        logger.info(f"  Skipped: {r['skipped']}")
                    else:
                        if r.get("method"):          # a cover: both eyes alike
                            result["filled"].append(f"{name}: {r['method'].lower()}, " + ", ".join(
                                f"{e['edge']} edge {e['strip']} px" for e in r["edges"]) + ", both eyes")
                        else:
                            result["filled"].append(name + ": " + ", ".join(
                                f"{e['eye']} eye {e['edge']} edge {e['strip']} px" for e in r["edges"]))
                        logger.info(f"  {'Covered' if r.get('method') else 'Filled'} in "
                                    f"{format_duration(time.monotonic() - started)}.")
                        self._sigFilled.emit(name)
            except Exception as e:
                logger.exception("Edge Fill failed")
                result["failed"].append(str(e))
            end_bar()
            result["stopped"] = self._stop.is_set()
            logger.info(f"=== Edge Fill {'stopped' if result['stopped'] else 'done'}: {len(result['filled'])} filled, "
                        f"{len(result['skipped'])} skipped, {len(result['failed'])} failed, "
                        f"in {format_duration(time.monotonic() - began)} ===")
            self._sigDone.emit(result)
        self._thread = threading.Thread(target=run, name="fill-edges", daemon=True)
        self._thread.start()

    @Slot(object)
    def _on_done(self, result) -> None:
        filled, skipped, failed = result.get("filled", []), result.get("skipped", []), result.get("failed", [])
        lines = [f"Filled {t}" for t in filled] + [f"Skipped {t}" for t in skipped] + [f"Failed {t}" for t in failed]
        if result.get("stopped"):
            self._status = "Stopped."
            self.toast.emit("info", "Edge Fill stopped", "\n".join(lines + ["The clip being filled is as it was."]))
        elif failed:
            self._status = "Finished with errors."
            self.message.emit("error", "Edge Fill", "\n\n".join(lines))
        else:
            self._status, self._progress = "Done.", 1.0
            self.toast.emit("success", f"Filled {len(filled)} clip(s)" if filled else "Nothing to fill",
                            "\n".join(lines) or "No clip had an edge strip.")
        self.progressChanged.emit()
        # a minimised window hears too, as the stages' finishes do (Windows notification)
        try:
            from studio.outcome import announce
            if result.get("stopped"):
                announce("warning", "Edge Fill stopped", "\n".join(lines) or "Stopped.")
            elif failed:
                announce("error", "Edge Fill finished with errors", "\n".join(lines))
            else:
                announce("success", "Edge Fill finished", "\n".join(lines) or "No clip had an edge strip.")
        except Exception:
            logger.debug("Edge Fill: no finish notification", exc_info=True)
        self._set_busy(False)                # the page, if it shows, looks again (filled clips as filled)
        pending, self._pending_chapter = self._pending_chapter, None
        folders, self._pending_folders = self._pending_folders, None
        follow, self._follow_later = self._follow_later, False
        if pending is not None:
            num, quiet = pending
            self._restoring = quiet
            try:
                self.pickChapter(num)
            finally:
                self._restoring = False
        elif folders is not None:
            self.useFolders(*folders)
        elif follow:
            self._follow_project()

    @Slot()
    def stop(self) -> None:
        """Stop now: the clip being written is left as it was."""
        self._look.set()
        if self._busy:
            self._stop.set()

    def shutdown(self) -> None:
        """Quit: the job stopped and waited for -- its ROSE process killed and its work folder
        removed before Studio ends (the process outlived Studio, holding its VRAM; sweep
        2026-10-14, as Erase's shutdown does)."""
        self._save_timer.stop()
        self._look.set()
        if self._busy:
            self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=self.JOIN_S)
        try:
            self.save()
        except Exception:
            logger.exception("Could not save Edge Fill's settings")
