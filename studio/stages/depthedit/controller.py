"""controller.py -- the Depth Map Editor (Extra tab) as QML sees it.

The editor is v1's own: ``DepthEditEngine`` in the generated engine.py is v1
DepthEditorGUI method for method -- its worker thread, SAM 3 prompts,
tracking, layers, gradient, whole-frame edit, project and render. This file
is what v1's window was around it:

* the engine loads on a thread the first time the page shows;
* v1's main-thread queue (``_ui_queue``, drained by v1's ``_poll`` every
  50 ms) is drained here on a QTimer, so v1's callbacks run on Qt's thread;
* v1's ``after`` is a Qt single-shot timer;
* the picture: v1 ``_render_preview`` drew on a Tk canvas -- here the same
  image (the view, the edit, the mask tint) goes to QML through an image
  provider, with v1's markers (clicks, boxes, the gradient line) as data;
* the pointer: v1's canvas handlers (``_on_press`` / ``_on_release`` /
  ``_on_right_click``) take the picture-relative point QML hands them;
* v1's yes/no questions (delete a layer, re-segment on the other picture,
  render untracked layers / replace the output, the install plan) are asked
  in the page, and the action is run again with the answer in place;
* everything the page shows is one ``state`` map, rebuilt after each change.
"""

from __future__ import annotations

import logging
import os
import re
import threading

from studio.gpu_jobs import wait_for_gpu
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from ... import settings_store
from ...legacy import REPO
from ..splatting.preview import PreviewImageProvider

logger = logging.getLogger("depthedit.v2")

# ---- Use in Splatting's files (audit 2026-09-28, minor; user 2026-09-29) --------------
#
# The edited map takes the live map's place (same name, so its sidecar still
# applies). What it replaces goes to ONE folder beside the maps, edit_history/:
# the first time the original, as <name>.original.mp4 (the map the editor
# opens from then on, so saved layers aren't applied twice); after that each
# earlier edit, as <name>.edit-NN.mp4. The editor's project and its render
# live there too. It used to be edited/originals/, and edited/ held a
# *_depth.mp4 -- Multi-Map took it for a map, and editing from it nested
# edited/edited/originals/ without end; Multi-Map skips edit_history.
#
# The size and time of what was written are kept beside the original: it
# stands for the live map only while the live map is still that edit (a map
# regenerated in DepthCrafter becomes the new original; the old one is kept
# as <name>.original-NN.mp4).

# in core.depthedit.project, which Splatting's clip list reads too (the
# "depth map edited" badge, user 2026-09-30)
from core.depthedit.project import edit_record_path as _edit_record  # noqa: E402
from core.depthedit.project import file_id as _file_id  # noqa: E402
from core.common.keyframes import CURVE_LABELS as _CURVE_LABELS  # noqa: E402
from core.depthedit.project import live_is_edit as _live_is_our_edit  # noqa: E402


def history_dir(depth: str) -> str:
    from core.depthedit.project import HISTORY_DIR
    return os.path.join(os.path.dirname(depth), HISTORY_DIR)


def _old_copy_path(live: str, kind: str) -> str:
    """edit_history/<name>.<kind>-NN<ext>, one past the highest kept."""
    import re
    stem, ext = os.path.splitext(os.path.basename(live))
    folder = history_dir(live)
    pat = re.compile(re.escape(f"{stem}.{kind}-") + r"(\d+)" + re.escape(ext) + "$", re.IGNORECASE)
    try:
        taken = [int(m.group(1)) for m in map(pat.match, os.listdir(folder)) if m]
    except OSError:
        taken = []
    return os.path.join(folder, f"{stem}.{kind}-{max(taken, default=0) + 1:02d}{ext}")


def _adopt_old_layout(live: str) -> None:
    """A clip edited before edit_history: its original (and the record
    beside it) moves from edited/originals/, its project from edited/."""
    from core.depthedit.project import masks_path_for, project_path_for
    folder = os.path.dirname(live)
    old = os.path.join(folder, "edited", "originals", os.path.basename(live))
    new = DepthEditController.backup_path(live)
    if not os.path.isfile(old) or os.path.exists(new):
        return
    os.makedirs(history_dir(live), exist_ok=True)
    moves = [(old, new), (_edit_record(old), _edit_record(new))]
    old_proj = project_path_for(os.path.join(folder, "edited"), live)
    new_proj = project_path_for(history_dir(live), live)
    if not os.path.exists(new_proj):
        moves += [(old_proj, new_proj), (masks_path_for(old_proj), masks_path_for(new_proj))]
    for src, dst in moves:
        if os.path.isfile(src):
            try:
                os.replace(src, dst)
            except OSError:
                logger.warning("Could not move %s to %s", src, dst)
    for gone in (os.path.join(folder, "edited", "originals"), os.path.join(folder, "edited")):
        try:
            os.rmdir(gone)  # only when empty
        except OSError:
            pass


def map_to_edit(depth: str) -> str:
    """The map the editor opens for Splatting's depth map: its kept original
    while the live map is our edit, else the live map itself."""
    _adopt_old_layout(depth)
    backup = DepthEditController.backup_path(depth)
    if os.path.isfile(backup) and os.path.isfile(depth) and _live_is_our_edit(depth, backup):
        return backup.replace("\\", "/")
    return depth.replace("\\", "/")


def _copy_atomic(src: str, dst: str) -> None:
    """The whole copy lands, or the old file stays. A destination held open
    by another reader (Splatting's preview decodes the live map through
    decord, which doesn't let it be renamed over) gets the finished copy
    written over it in place instead -- as before, but from a complete file."""
    import shutil
    tmp = dst + ".part"
    try:
        shutil.copy2(src, tmp)
        try:
            os.replace(tmp, dst)
        except PermissionError:
            shutil.copyfile(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _move(src: str, dst: str) -> None:
    """A rename; a file held open (decord in Splatting's preview) is copied
    whole instead, and the source left for what replaces it."""
    try:
        os.replace(src, dst)
    except PermissionError:
        _copy_atomic(src, dst)


def use_edited_map(written: str, live: str) -> None:
    """Put the rendered map in place of the live one (same name, so its
    sidecar still applies), and what it replaces into edit_history: the
    original the first time (or when DepthCrafter regenerated the map),
    else the previous edit. A render made in edit_history is moved, one
    elsewhere copied. Raises OSError, with the live map left as it was."""
    import json
    _adopt_old_layout(live)
    backup = DepthEditController.backup_path(live)
    os.makedirs(history_dir(live), exist_ok=True)
    undo = []                                   # (moved-to, from), newest first
    try:
        if os.path.isfile(live):
            if os.path.isfile(backup) and not _live_is_our_edit(live, backup):
                old = _old_copy_path(live, "original")
                _move(backup, old)
                undo.insert(0, (old, backup))
            aside = backup if not os.path.isfile(backup) else _old_copy_path(live, "edit")
            _move(live, aside)
            undo.insert(0, (aside, live))
        mine = os.path.normcase(os.path.abspath(os.path.dirname(written))) == \
            os.path.normcase(os.path.abspath(history_dir(live)))
        if mine:
            _move(written, live)
            if os.path.isfile(written):
                os.remove(written)              # copied over a held-open map
        else:
            _copy_atomic(written, live)
    except OSError:
        for moved, back in undo:
            if not os.path.exists(back):
                try:
                    os.replace(moved, back)
                except OSError:
                    logger.exception("Could not put %s back", back)
        raise
    with open(_edit_record(backup), "w", encoding="utf-8") as f:
        json.dump({"live": _file_id(live)}, f)


#: The var each QML setter writes, and v1's widget command that follows it.
_VAR_COMMANDS = {
    "view": "_schedule_preview", "overlay": "_schedule_preview", "colormap": "_schedule_preview",
    "tool": "_on_tool_change", "page": "_show_page", "layer_source": "_on_layer_source_change",
    "layer_enabled": "_on_layer_enabled", "layer_name": None, "text": None, "offload": None,
    "handoff": None, "rgb_path": None, "depth_path": None, "output_dir": None,
    "render_hires": None,
}
#: Saved as v1 saves them (config_depthedit.json's keys).
_SETTINGS_SEED = ("rgb_path", "depth_path", "output_dir", "view", "overlay", "colormap", "offload_frames",
                  "send_to_splatting")


def _seed(v1: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v1[k] for k in _SETTINGS_SEED if k in v1}


class _Hooks:
    """What the generated engine calls. message() may come from the worker;
    the rest runs on Qt's thread (v1's main thread)."""

    def __init__(self, ctl: "DepthEditController"):
        self.ctl = ctl

    def message(self, kind, title, text):
        self.ctl._sigMessage.emit(str(kind), str(title), str(text))

    def widget(self, name, options):
        self.ctl._widgets.setdefault(name, {}).update(options)
        self.ctl._dirty()

    def after(self, ms, fn):
        return self.ctl._after(ms, fn)

    def after_cancel(self, job):
        self.ctl._after_cancel(job)

    def preview(self):
        self.ctl._render_preview()

    def page(self, name):
        self.ctl._dirty()

    def settings_save(self, data):
        settings_store.save("depthedit", dict(data))

    def settings_load(self):
        return settings_store.load("depthedit", REPO / "config_depthedit.json", _seed)


class DepthEditController(QObject):
    stateChanged = Signal()
    frameChanged = Signal()
    message = Signal(str, str, str)
    #: v1 asked yes/no: the page asks, then answer(yes).
    question = Signal(str, str)
    #: v1's "pick a clip DepthCrafter processed" file dialog: the page opens its own.
    pickFile = Signal(str, str)
    toast = Signal(str, str, str)
    #: A render finished: (source clip, the depth map written).
    rendered = Signal(str, str)

    _sigMessage = Signal(str, str, str)
    _sigLoaded = Signal(object)
    #: Erase from Clip (user, 2026-10-05): progress (0..1, text) and the end (ok, text)
    _sigErase = Signal(float, str)
    _sigEraseDone = Signal(bool, str)
    _sigModelsMoved = Signal()
    #: the 3D view's picture from Splatting's preview: (request, QImage or None, error)
    _sigWarp = Signal(int, object, str)
    warpNoteChanged = Signal()

    #: The 3D view (user, 2026-09-30): the edited map through Splatting's own
    #: preview, so an edit is judged in 3D before its map is rendered.
    VIEW_3D = "3D"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self.provider = PreviewImageProvider()
        self._module = None
        self._engine = None
        self._loading = False
        self._load_error = ""
        #: Splatting's Edit Depth Map: {"rgb", "depth"} it opened, and a load
        #: waiting for the engine's first import.
        self._from_splat: Optional[Dict[str, str]] = None
        self._pending_open = None
        self._widgets: Dict[str, Dict[str, Any]] = {}
        self._timers: Dict[int, QTimer] = {}
        self._next_timer = 0
        self._pending = None              # the action a question is holding
        # Undo / Redo (user, 2026-10-05; core.depthedit.history), for one clip at a
        # time: Load Clip and Upscale Map start it afresh
        from core.depthedit.history import History
        self._history = History()
        self._history_info = None
        self._state: Dict[str, Any] = {"ready": False}
        self._serial = 0
        # the picture's v2-only options (user, 2026-09-28), kept in their own
        # settings file: v1's config_depthedit.json is written whole by the engine
        self._view_opts = dict(self.VIEW_DEFAULTS)
        self._view_opts.update({k: v for k, v in settings_store.load("depthedit_view").items()
                                if k in self.VIEW_DEFAULTS})
        self._markers: Dict[str, Any] = {}
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(0)
        self._rebuild.timeout.connect(self._build_state)
        self._poll = QTimer(self)
        self._poll.setInterval(50)          # v1 _poll
        self._poll.timeout.connect(self._drain)
        q = Qt.ConnectionType.QueuedConnection
        self._sigMessage.connect(self.message, q)
        self._sigLoaded.connect(self._on_loaded, q)
        self._sigWarp.connect(self._on_warp, q)
        self._sigErase.connect(self._on_erase_progress, q)
        self._sigEraseDone.connect(self._on_erase_done, q)
        # the models folder changed, or Settings > Studio > Models downloaded SAM 3 (2026-10-20):
        # the Segment page's status says so (the listener may run on a download's thread)
        self._sigModelsMoved.connect(lambda: self._run("_refresh_sam_status"), q)
        from core import weights_paths
        weights_paths.on_change(self._models_moved)
        # Erase from Clip / ROSE set-up: one at a time, on their own thread
        self._erase_thread: Optional[threading.Thread] = None
        self._erase_stop = threading.Event()
        self._erase = {"task": "", "progress": 0.0, "text": ""}
        self._rose_ready: Optional[bool] = None
        # Erase from Clip's own settings (user, 2026-10-06), kept between sessions
        self._erase_opts = dict(self.ERASE_DEFAULTS)
        self._erase_opts.update({k: v for k, v in settings_store.load("depthedit_erase").items()
                                 if k in self.ERASE_DEFAULTS})
        self.splatting = None          # app.py: the Splatting controller, for the 3D view
        self._warp_req = 0
        self._warp_glasses = False          # the newest 3D request is Glasses-Free 3D
        self._stereo_serial = -1            # the serial of the frame that is its left eye
        self._warp_note = ""

    # ---- loading ------------------------------------------------------------------

    state = Property("QVariantMap", lambda self: self._state, notify=stateChanged)

    # ---- the picture's own options (user, 2026-09-28) ------------------------------------
    #: maskStyle: "tint" (tinted, the selected layer outlined -- v1's look) or
    #: "outline" (every layer's border only, nothing tinted, to see the edge
    #: under it); rgbOverlay: the clip's own frame blended over the Depth and
    #: Edited views at rgbOpacity, to line a mask or an edit up with the edge.
    VIEW_DEFAULTS = {"maskStyle": "tint", "rgbOverlay": False, "rgbOpacity": 0.5, "warpMode": "anaglyph",
                     # the depth views dithered to show the map's 10 bits (user, 2026-10-05)
                     "dither": True,
                     # Start Here's direction: "forward" (the next frames) or "backward" (the previous)
                     "trackDir": "forward",
                     # Highlight Specks (user, 2026-10-12): DepthCrafter's left-edge specks tinted
                     "specks": False}
    viewOptionsChanged = Signal()
    viewOptions = Property("QVariantMap", lambda self: dict(self._view_opts), notify=viewOptionsChanged)

    @Slot(str, "QVariant")
    def setViewOption(self, name: str, value) -> None:
        if name not in self.VIEW_DEFAULTS:
            return
        if name == "maskStyle":
            value = "outline" if str(value) == "outline" else "tint"
        elif name in ("rgbOverlay", "dither", "specks"):
            value = bool(value)
        elif name == "warpMode":
            value = str(value) if str(value) in ("holes", "glasses") else "anaglyph"
        elif name == "trackDir":
            value = "backward" if str(value) == "backward" else "forward"
        else:
            value = round(max(0.0, min(1.0, float(value))), 2)
        if self._view_opts.get(name) == value:
            return
        self._view_opts[name] = value
        settings_store.save("depthedit_view", dict(self._view_opts))
        self.viewOptionsChanged.emit()
        if self._engine is not None:
            self._render_preview()
    imageSerial = Property(int, lambda self: self._serial, notify=frameChanged)
    #: Glasses-Free 3D (studio/sr_display.py): the frame shown is its left eye, the SR
    #: display weaves the pair -- only while that frame is the one on screen
    stereo3d = Property(bool, lambda self: self._stereo_serial == self._serial and self._serial > 0,
                        notify=frameChanged)
    #: why the 3D view shows no (new) picture, or "" (QML lays it over the picture)
    warpNote = Property(str, lambda self: self._warp_note, notify=warpNoteChanged)

    def _set_warp_note(self, text: str) -> None:
        if text != self._warp_note:
            self._warp_note = text
            self.warpNoteChanged.emit()

    def _request_warp(self, idx: int) -> None:
        """The 3D view: the frame's EDITED map -- as the render will write it,
        10-bit codes -- through Splatting's preview at this frame. The picture
        shown stays until the new one comes; only the newest request counts."""
        eng = self._engine
        sp = self.splatting
        if sp is None:
            self._set_warp_note("The 3D view needs Splatting, which isn't available.")
            return
        jobs = getattr(self, "jobs", None)
        running = jobs.running() if jobs is not None else None
        if running:
            self._set_warp_note(f"The 3D view waits while {running[1]} runs a job.")
            return
        video = str(eng.rgb_path_var.get() or "")
        if not video or not os.path.isfile(video):
            self._set_warp_note("The 3D view needs the clip's source video (Source video, above).")
            return
        from core.depthedit import depth_io
        self._ensure_match_ref()
        codes = depth_io.from_unit(eng._edited_unit(idx), eng.info)
        mode = self._view_opts.get("warpMode")
        holes = mode == "holes"
        # Glasses-Free 3D only with the display there; a saved choice without it draws the anaglyph
        from studio import sr_display
        hub = sr_display._HUB
        glasses = mode == "glasses" and hub is not None and hub.property("available")
        self._warp_req += 1
        req = self._warp_req
        self._warp_glasses = bool(glasses)
        self._warp_idx, self._warp_holes = idx, holes
        self._set_warp_note("")
        source = sr_display.SOURCE if glasses else ("Splat Result" if holes else "Dubois Anaglyph")
        # the masks and shapes on the left eye only (user, 2026-10-25); the holes view shows no left eye
        overlay = None if holes else self._left_overlay(idx)
        sp.preview.render_external(video, idx, codes, source, holes,
                                   lambda img, err, req=req: self._sigWarp.emit(req, img, err),
                                   stage="depthedit", depth=str(eng.depth_path_var.get() or ""),
                                   left_overlay=overlay)

    def _left_overlay(self, idx: int):
        """The 3D view's masks and shapes for its left eye: (colour HxWx3, strength HxW), float 0..1 at
        the frame's size -- the other views' overlay exactly (the masks tinted, the selected layer's
        outline; Outline only), worked out from it drawn over black and over white. None with the
        overlay off or nothing to draw."""
        import numpy as np
        eng = self._engine
        if not eng.overlay_var.get() or eng.project is None or not eng.project.layers:
            return None
        if eng.rgb is not None and idx < len(eng.rgb):
            h, w = eng.rgb[idx].shape[:2]
        else:
            h, w = eng._unit(idx).shape[:2]
        black = self._overlay(np.zeros((h, w, 3), np.uint8), idx, smooth=True).astype(np.float32)
        white = self._overlay(np.full((h, w, 3), 255, np.uint8), idx, smooth=True).astype(np.float32)
        strength = np.clip(1.0 - (white - black).mean(axis=2) / 255.0, 0.0, 1.0)
        if not (strength > 0.004).any():
            return None
        colour = np.clip(black / 255.0 / np.maximum(strength, 1e-3)[..., None], 0.0, 1.0)
        return colour.astype(np.float32), strength.astype(np.float32)

    def _on_warp(self, req: int, img, err: str) -> None:
        if req != self._warp_req or self._engine is None or self._engine.view_var.get() != self.VIEW_3D:
            return                              # an older frame or setting, or the view has changed
        if img is None:
            self._set_warp_note(err or "The 3D view could not be drawn.")
            return
        self._set_warp_note("")
        self.provider.put("frame", img)
        # its clicks, boxes and gradient over the picture, to edit on -- not on the holes view's right eye
        self._markers = ({} if getattr(self, "_warp_holes", True)
                         else self._marker_data(min(getattr(self, "_warp_idx", 0), max(0, self._engine._num_frames() - 1))))
        self._serial += 1
        if self._warp_glasses:
            self._stereo_serial = self._serial
        self.frameChanged.emit()
    markers = Property("QVariantMap", lambda self: self._markers, notify=frameChanged)

    @Slot()
    def ensureLoaded(self) -> None:
        if self._module is not None or self._loading:
            return
        self._loading = True
        self._dirty()

        def load():
            try:
                from . import engine
                self._sigLoaded.emit(engine)
            except Exception as e:
                logger.exception("Depth Map Editor could not load")
                self._sigLoaded.emit(e)

        threading.Thread(target=load, name="depthedit-import", daemon=True).start()

    @Slot(object)
    def _on_loaded(self, result) -> None:
        self._loading = False
        if isinstance(result, Exception):
            self._load_error = f"{type(result).__name__}: {result}"
            self._dirty()
            return
        self._module = result
        self._engine = result.DepthEditEngine(shell=self._shell, hooks=_Hooks(self))
        self._engine.wait_gpu = lambda: wait_for_gpu(self)     # SAM 3 loads after the hand-over (#14)
        self._sync_erase_fill()        # Erase's depth fill follows the Method (Dust / Static: none)
        # Every var the page shows: a change to any of them redraws the state.
        for name in dir(self._engine):
            if name.endswith("_var"):
                getattr(self._engine, name).trace_add("write", lambda *_a: self._dirty())
        for _path, var in self._engine._layer_controls + [(n, v) for n, v in self._engine._global_controls]:
            var.trace_add("write", lambda *_a: self._dirty())
        self._poll.start()
        self._engine._schedule_preview()
        # v1 says "Wrote <path> (N frames, ...)" when a render lands: tell the
        # page, so it can offer the way back to Splatting
        eng, status = self._engine, self._engine._status

        def _status(text, *a, **k):
            r = status(text, *a, **k)
            m = re.match(r"Wrote (.+?) \(\d+ frames", str(text))
            if m:
                self.rendered.emit(str(getattr(eng.project, "rgb_path", "") or ""), m.group(1))
            return r
        self._engine._status = _status
        self._dirty()
        if self._pending_open is not None:
            todo, self._pending_open = self._pending_open, None
            todo()

    # ---- v1's Tk loop: after(), the UI queue ---------------------------------------------

    def _after(self, ms: int, fn) -> int:
        self._next_timer += 1
        tid = self._next_timer
        t = QTimer(self)
        t.setSingleShot(True)

        def fire():
            self._timers.pop(tid, None)
            t.deleteLater()
            self._act(fn)
        t.timeout.connect(fire)
        self._timers[tid] = t
        t.start(max(0, int(ms)))
        return tid

    def _after_cancel(self, job) -> None:
        t = self._timers.pop(job, None)
        if t is not None:
            t.stop()
            t.deleteLater()

    def _drain(self) -> None:
        """v1 _poll: run what the worker posted, on this thread."""
        eng = self._engine
        if eng is None:
            return
        import queue as _q
        for _ in range(200):
            try:
                fn, args = eng._ui_queue.get_nowait()
            except _q.Empty:
                break
            self._act(lambda fn=fn, args=args: fn(*args))

    # ---- v1's yes/no questions ------------------------------------------------------------

    def _act(self, action) -> None:
        """Run a v1 action; if it asked a yes/no question, hold it, ask in QML,
        and run it again once answered (answer())."""
        mb = self._module.messagebox if self._module is not None else None
        if mb is None:
            action()
            return
        mb.asked = []
        try:
            action()
        except Exception:
            logger.exception("Depth Map Editor action failed")
        finally:
            asked, mb.asked = mb.asked, []
            self._dirty()
        if asked:
            title, text = asked[0]
            self._pending = (action, dict(mb.answers), text)
            self.question.emit(title, text)

    @Slot(bool)
    def answer(self, yes: bool) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        action, answers, text = pending
        mb = self._module.messagebox
        mb.answers = dict(answers)
        mb.answers[text] = bool(yes)
        try:
            self._act(action)
        finally:
            # Answers hold for the action they were given to, and its re-runs.
            if self._pending is None:
                mb.answers = {}

    # ---- setters and actions ------------------------------------------------------------

    def _models_moved(self) -> None:
        self._sigModelsMoved.emit()

    def _run(self, name: str, *args) -> None:
        if self._engine is None:
            return
        fn = getattr(self._engine, name)
        self._act(lambda: fn(*args))

    @Slot(str, "QVariant")
    def setVar(self, name: str, value) -> None:
        """A v1 variable, and the widget command v1 bound to it."""
        eng = self._engine
        if eng is None or name not in _VAR_COMMANDS:
            return
        var = getattr(eng, name + "_var")

        def action():
            var.set(value)
            cmd = _VAR_COMMANDS[name]
            if cmd:
                getattr(eng, cmd)()
        if name in self.LAYER_VARS:
            self._edit(self.LAYER_VARS[name], action, coalesce="var." + name)
        else:
            self._act(action)
        if name in ("rgb_path", "depth_path", "output_dir", "offload", "handoff", "overlay", "colormap", "view",
                    "render_hires"):
            eng._save_settings()

    # ---- Local Match (user, 2026-10-13; edit_ops.local_match) -------------------------

    def _ensure_match_ref(self) -> None:
        """Local Match's reference frame, as the map has it, loaded when Local Match is on and
        what is loaded isn't the frame its settings name -- switched on, a new frame, Undo, a
        project opened -- and dropped when it's off. On the UI thread, before a frame is drawn."""
        eng = self._engine
        g = getattr(getattr(eng, "project", None), "global_settings", None)
        if g is None:
            return
        E = self._module.edit_ops
        if not g.match_on:
            if getattr(g, "_match_ref", None) is not None:
                E.set_match_reference(g, None, None)
            return
        want = int(g.match_frame)
        if getattr(g, "_match_ref", None) is not None and getattr(g, "_match_ref_frame", None) == want:
            return
        n = eng._num_frames()
        if n <= 0:
            return
        try:
            E.set_match_reference(g, want, eng._unit(max(0, min(want, n - 1))))
        except Exception:
            logger.debug("Local Match: could not read its reference frame %d", want, exc_info=True)

    @Slot()
    def matchUseThisFrame(self) -> None:
        """Local Match's reference: the frame showing (undoable, like any control)."""
        eng = self._engine
        if eng is None or getattr(eng, "project", None) is None:
            return
        frame = int(eng.frame_idx)
        for n, var in eng._global_controls:
            if n == "match_frame":
                # a step of its own each press -- not merged like a dragged slider's
                self._edit("Local Match frame", lambda: (var.set(frame), eng._on_global_control(n, var)))
                return

    # ---- keyframes per layer (user, 2026-10-05; core.depthedit.keys) ---------------

    @Slot(str)
    def addKeyframe(self, kind: str) -> None:
        """S / D: a static or dynamic key on the current frame for the selected
        layer, of its number settings as the controls show them now."""
        eng = self._engine
        layer = eng._layer() if eng is not None and getattr(eng, "project", None) else None
        if layer is None or kind not in ("static", "dynamic"):
            return
        K = self._module.keys
        values = {K.key_name(p): float(var.get()) for p, var in eng._layer_controls if K.is_keyed_path(p)}

        def action():
            layer.keys.set_keyframe(eng.frame_idx, kind, values)
            eng._refresh_layer_panel(keep_list=True)
            eng._schedule_preview()
        self._edit("Static key" if kind == "static" else "Dynamic key", action)

    @Slot()
    def removeKeyframe(self) -> None:
        """Delete the selected layer's key on the current frame."""
        eng = self._engine
        layer = eng._layer() if eng is not None and getattr(eng, "project", None) else None
        if layer is not None:
            def action():
                if layer.keys.clear_frame(eng.frame_idx):
                    eng._refresh_layer_panel(keep_list=True)
                    eng._schedule_preview()
            self._edit("Delete key", action)

    @Slot()
    def clearKeyframes(self) -> None:
        """Every key of the selected layer: its settings hold on every frame again."""
        eng = self._engine
        layer = eng._layer() if eng is not None and getattr(eng, "project", None) else None
        if layer is not None and not layer.keys.is_empty():
            layer.keys.clear_all()
            self._act(lambda: (eng._refresh_layer_panel(keep_list=True), eng._schedule_preview()))

    # ---- the shaper: a dynamic key's curve (user, 2026-10-06: as Splatting's) ----
    curveShapes = Property("QVariantList", lambda self: [{"key": k, "label": lab} for k, lab in _CURVE_LABELS],
                           constant=True)

    @Slot(int, "QVariant")
    def setKeyCurve(self, frame: int, curve) -> None:
        """The curve of the selected layer's dynamic key at `frame` (None or
        linear: a straight ramp). Undoable; saved with the project."""
        eng = self._engine
        layer = eng._layer() if eng is not None and getattr(eng, "project", None) else None
        if layer is None:
            return
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        c = dict(c) if c else None

        def action():
            if layer.keys.set_curve(int(frame), c):
                eng._refresh_layer_panel(keep_list=True)
                eng._schedule_preview()
        self._edit("Key shape", action)

    @Slot("QVariant", int, int, result="QVariantList")
    def curveSamples(self, curve, seed: int, n: int = 120):
        """The curve at n+1 even steps of its span, for drawing: the same
        function the preview and the render use."""
        from core.common.keyframes import curve_fraction
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        n = max(2, int(n))
        return [curve_fraction(i / n, dict(c) if c else None, seed=int(seed)) for i in range(n + 1)]

    @Slot("QVariant", int, result="QVariantList")
    def bakeCurve(self, curve, seed: int):
        """Any curve as custom points that trace it."""
        from core.common.keyframes import bake_curve
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        return bake_curve(dict(c) if c else None, seed=int(seed))

    @Slot(int)
    def jumpKeyframe(self, direction: int) -> None:
        """To the selected layer's next (1) or previous (-1) key."""
        eng = self._engine
        layer = eng._layer() if eng is not None and getattr(eng, "project", None) else None
        if layer is None:
            return
        frames = layer.keys.sorted_frames()
        here = eng.frame_idx
        target = (next((f for f in frames if f > here), None) if direction > 0
                  else next((f for f in reversed(frames) if f < here), None))
        if target is not None:
            self.setFrame(target)

    @Slot(int)
    def setFrame(self, frame: int) -> None:
        eng = self._engine
        if eng is None:
            return
        eng.frame_var.set(int(frame))
        self._run("_on_frame_slider")

    #: The layer settings shown in VIDEO pixels (user, 2026-10-01). The edit
    #: works in depth-map pixels, and DepthCrafter's maps are smaller than the
    #: clip (768 wide against 1920), so a whole depth pixel moved an edge ~2.5
    #: pixels of the finished picture -- and Splatting's Dilate counts clip
    #: pixels, so "3 px" meant different things in the two places. Shown here
    #: in clip pixels in half-pixel steps; the settings (and project files)
    #: keep depth pixels, fractional now (core.depthedit.edit_ops).
    #: key -> (display range lo, hi, signed readout, axis: "y" or "xy")
    VIDEO_PX = {"edge_top": (-60.0, 60.0, True, "y"), "edge_bottom": (-60.0, 60.0, True, "y"),
                "edge_left": (-60.0, 60.0, True, "x"), "edge_right": (-60.0, 60.0, True, "x"),
                "grow": (-40.0, 40.0, True, "xy"), "feather": (0.0, 60.0, False, "xy"),
                "close_gaps": (0.0, 40.0, False, "xy"),
                "smooth": (0.0, 60.0, False, "xy")}
    VIDEO_PX_STEP = 0.5

    def _px_scale(self, axis: str) -> float:
        """Video pixels per depth-map pixel for the loaded clip (1 without a
        source video): the height ratio for "y", the mean ratio for "xy"."""
        eng = self._engine
        info = getattr(eng, "info", None) if eng is not None else None
        rgb = str(getattr(getattr(eng, "project", None), "rgb_path", "") or "") if eng is not None else ""
        if info is None or not rgb or not os.path.isfile(rgb):
            return 1.0
        cache = getattr(self, "_rgb_size", None)
        if cache is None or cache[0] != rgb:
            try:
                from core.depthedit import depth_io
                p = depth_io.probe(rgb, count=False)   # on the UI thread: the size only, never a decode (2026-10-11)
                cache = (rgb, int(p.width), int(p.height))
            except Exception:
                cache = (rgb, 0, 0)
            self._rgb_size = cache
        _path, vw, vh = cache
        if not vw or not vh or not info.width or not info.height:
            return 1.0
        sx, sy = vw / float(info.width), vh / float(info.height)
        return sy if axis == "y" else sx if axis == "x" else (sx + sy) / 2.0

    @Slot(str, "QVariant")
    def setControl(self, key: str, value) -> None:
        """An edit control (v1 _on_layer_control / _on_global_control). `key`
        is the settings path joined with '.'; whole-frame ones start 'global.'.
        The VIDEO_PX ones arrive in video pixels."""
        eng = self._engine
        if eng is None:
            return
        if key in self.VIDEO_PX:
            value = float(value) / self._px_scale(self.VIDEO_PX[key][3])
        self._set_control_raw(key, value)

    def _set_control_raw(self, key: str, value) -> None:
        """setControl in the settings' own units (depth-map pixels)."""
        eng = self._engine
        if eng is None:
            return
        if key.startswith("global."):
            name = key[len("global."):]
            for n, var in eng._global_controls:
                if n == name:
                    self._edit(self._control_label(key), lambda: (var.set(value), eng._on_global_control(n, var)),
                               coalesce="control." + key)
                    return
            return
        path = tuple(key.split("."))
        for p, var in eng._layer_controls:
            if p == path:
                self._edit(self._control_label(key), lambda: (var.set(value), eng._on_layer_control(p, var)),
                           coalesce="control." + key)
                return

    @Slot()
    def sampleAddDepth(self) -> None:
        """Add's Depth from the object on the frame showing: the nearest part
        of what the depth map has under the layer's mask (user, 2026-10-03)."""
        eng = self._engine
        if eng is None:
            return

        def action():
            layer = eng._layer()
            if layer is None or layer.masks is None:
                return
            idx = eng.frame_idx
            value = self._module.edit_ops.sample_add_depth(eng._unit(idx), layer.masks.get(idx, layer.settings.steady))
            if value is None:
                eng._status("The layer has no mask on this frame to sample.")
                return
            for p, var in eng._layer_controls:
                if p == ("add_depth",):
                    var.set(round(value, 3))
                    eng._on_layer_control(p, var)
            eng._status(f"Add depth sampled: {value:.3f} (the nearest part of the object).")
        self._edit("Sample depth", action)

    @Slot()
    def upscaleMap(self) -> None:
        """Upscale Map (user, 2026-10-05): the loaded clip's depth map read
        again, enlarged to the source video's size (bilinear, as Splatting
        enlarges it), so every edit from here works in the clip's own pixels.
        The layers stay: their masks come along at the new size."""
        eng = self._engine
        if eng is None:
            return
        M = self._module

        def action():
            if eng.info is None or eng.codes is None or eng.project is None:
                eng._status("Load a clip first.")
                return
            if eng._job_pending():
                eng._status("Wait for the current job to finish.")
                return
            size, note = M.hires.edit_size(eng.project.rgb_path, eng.info)
            if not size:
                eng._status(note)
                return
            old = eng.info
            big = M.depth_io.at_size(old, size)

            def job():
                eng._ui(eng._status, "Upscaling the depth map...")
                codes = M.depth_io.load_codes(
                    big, progress=lambda n, t: eng._ui(eng._status, f"Upscaling the depth map... {n}/{t}"),
                    stop=eng._stop)
                if not eng._stop.is_set():
                    eng._ui(self._map_upscaled, old, big, codes)
            # as a load: the spinner shows, and Track / Render wait for it
            eng._submit("Loading clip", job)
        self._act(action)

    def _map_upscaled(self, old, big, codes) -> None:
        eng = self._engine
        if eng is None or eng.info is not old:
            return                          # another clip was loaded meanwhile
        enlarged = self._module.hires.upscale_layers(eng.project, (big.width, big.height))
        eng.info, eng.codes = big, codes
        msg = (f"Depth map upscaled to {big.width}x{big.height} (from {old.width}x{old.height}); "
               "every edit now works in the source's pixels.")
        if enlarged:
            msg += (" Enlarged masks on " + ", ".join(enlarged)
                    + " -- Start Tracking again for SAM 3's full-detail outline.")
        eng._status(msg)
        eng._schedule_preview()
        self._dirty()

    # ---- Undo / Redo (user, 2026-10-05; core.depthedit.history) --------------------------

    #: run() actions that edit, and what Undo calls them
    EDIT_OPS = {"_new_layer": "New layer", "_delete_layer": "Delete layer", "_find_text": "Find",
                "_clear_prompts": "Clear layer prompts", "_track": "Tracking",
                "_sample_gradient": "Sample gradient", "_swap_gradient": "Swap gradient ends",
                "_reset_gradient": "Reset gradient", "_reset_global": "Reset whole frame"}
    #: setVar names that edit the selected layer
    LAYER_VARS = {"layer_name": "Layer name", "layer_enabled": "Layer on / off", "layer_source": "Segment on"}

    def _control_label(self, key: str) -> str:
        """The dial's own label, from the engine's slider tables (spec[1])."""
        M = self._module
        name = key[len("global."):] if key.startswith("global.") else key
        for table in ("LAYER_SLIDERS", "EDGE_SLIDERS", "GRADIENT_SLIDERS", "GLOBAL_SLIDERS"):
            for spec in getattr(M, table, ()):
                path = spec[0]
                if (".".join(path) if isinstance(path, tuple) else str(path)) == name:
                    return str(spec[1])
        return {"mode": "Mask Alter / Add", "fill_holes": "Fill holes", "gradient.enabled": "Gradient on / off",
                "gradient.shape": "Gradient shape", "gradient.blend": "Gradient blend",
                "gradient.follow_object": "Gradient follows the object", "invert": "Invert"}.get(name, name)

    def _history_live(self) -> bool:
        """The history belongs to the clip and size it was made on: a Load Clip
        or Upscale Map (a new eng.info) starts it afresh."""
        eng = self._engine
        info = getattr(eng, "info", None) if eng is not None else None
        if info is not self._history_info:
            self._history.clear()
            self._history_info = info
        return info is not None and getattr(eng, "project", None) is not None

    def _capture(self, label: str):
        from core.depthedit.history import State
        eng = self._engine
        return State(label, eng.project.snapshot(), eng.selected, dict(eng._results))

    def _edit(self, label: str, action, coalesce: Optional[str] = None, keep: bool = False) -> None:
        """Run an editing action as _act does, recording the project as it was
        before it for Undo -- when it changed something (or `keep`: a job that
        changes it later)."""
        if self._engine is None or not self._history_live():
            self._act(action)
            return
        from core.depthedit.history import fingerprint

        def recorded():
            eng = self._engine
            if eng is None or getattr(eng, "project", None) is None:
                return action()
            before = self._capture(label)
            fp = fingerprint(eng.project)
            sent = getattr(eng, "_submitted", 0)
            action()
            # `keep`: only a job actually sent is a step -- a Track or Find that
            # did nothing cleared Redo (bug sweep 2026-10-09, A9)
            if (keep and getattr(eng, "_submitted", 0) != sent) or fingerprint(eng.project) != fp:
                self._history.push(before, coalesce, live=(eng.project, eng._results))
        self._act(recorded)

    def _restore(self, state) -> None:
        eng = self._engine
        eng.project = state.project.snapshot()       # the history keeps its own copy
        n = len(eng.project.layers)
        eng.selected = state.selected if state.selected is not None and 0 <= state.selected < n else (0 if n else None)
        eng._results = dict(state.results)
        # SAM 3 forgets this session's prompts: the next click replays the layer's own
        eng.engine.owner = None
        eng._load_global_controls()
        eng._refresh_layer_panel()
        eng._schedule_preview()

    def _step_history(self, back: bool) -> None:
        eng = self._engine
        if eng is None or not self._history_live():
            return
        if eng._job_pending():
            eng._status("Wait for the current job to finish before undoing or redoing.")
            self._dirty()
            return
        current = self._capture("")
        state = self._history.undo(current) if back else self._history.redo(current)
        if state is None:
            return
        self._act(lambda: self._restore(state))
        eng._status(("Undone: " if back else "Redone: ") + current.label + ".")
        self._dirty()

    @Slot()
    def trackHere(self) -> None:
        """Start Here (user, 2026-10-05): track from the frame showing in one
        direction only -- forward over the next frames, or back over the
        previous ones (the arrow beside the button); every other frame keeps
        the mask it has."""
        eng = self._engine
        if eng is None or not self._claim_for_sam():
            return
        direction = self._view_opts.get("trackDir", "forward")
        label = "Track forward from here" if direction == "forward" else "Track back from here"
        self._edit(label, lambda: eng._track(direction), keep=True)

    @Slot()
    def undo(self) -> None:
        self._step_history(True)

    @Slot()
    def redo(self) -> None:
        self._step_history(False)

    @Slot(str)
    def resetControl(self, key: str) -> None:
        """v1: double-click a slider -> the value a new layer / frame starts with."""
        if self._module is None:
            return
        if key.startswith("global."):
            value = self._module._default_for((key[len("global."):],), "global")
        else:
            value = self._module._default_for(tuple(key.split(".")), "layer")
        self._set_control_raw(key, value)      # a default is in the settings' units

    @Slot(str)
    def run(self, name: str) -> None:
        """A v1 button with no argument (Load Clip, New, Delete, Find, Start
        Tracking, Render...). Only v1's own action methods are allowed."""
        allowed = {"_load_clip", "_pick_from_depthcrafter", "_new_layer", "_delete_layer", "_find_text",
                   "_clear_prompts", "_track", "_request_stop", "_toggle_model", "_install_packages",
                   "_download_weights", "_sample_gradient", "_swap_gradient", "_reset_gradient",
                   "_reset_global", "_save_project", "_render"}
        if name not in allowed:
            raise ValueError(f"no action {name!r}")
        if name == "_render":
            self._ensure_match_ref()        # Render copies the settings: the reference with them
        if name == "_pick_from_depthcrafter":
            self._pick_from_depthcrafter()
            return
        if name == "_download_weights" and getattr(self, "models", None) is not None:
            # Settings > Studio > Models' download (a child process: Stop ends it at once, the part-file
            # resumes next time); the page shows its bar and Stop, the status follows when it lands
            why = self.models.start("sam3")
            if why:
                self.message.emit("warning", "Download Weights", why)
            return
        if name in ("_track", "_find_text") and not self._claim_for_sam():
            return
        if name == "_load_clip" and self._engine is not None and not self._engine._job_pending():
            # audit #12 (v1's _load_clip refuses while a job runs). Queued on the
            # worker, which runs it before the load: compressing every layer's
            # masks stalled the window a second or more a layer (bug sweep A9)
            self._keep_edits(later=True)
        if name in self.EDIT_OPS and self._engine is not None:
            fn = getattr(self._engine, name)
            # Tracking and Find change the masks later, on the worker: a step when a job was sent
            self._edit(self.EDIT_OPS[name], lambda: fn(), keep=name in ("_track", "_find_text"))
            return
        self._run(name)

    def _keep_edits(self, later: bool = False) -> None:
        """Save the open clip's project (layers, prompts, masks, the whole-frame
        edit) beside its output, as Render does, before it is replaced or the
        app closes (audit 2026-09-28 #12: Edit Depth Map on another clip, Load
        Clip and closing dropped them unsaved). A clip left untouched writes no
        file. ``later``: queued on the editor's worker, after the job running
        now (a track still writing masks)."""
        eng = self._engine
        project = getattr(eng, "project", None)
        if project is None or getattr(eng, "info", None) is None or not project.depth_path:
            return
        from core.depthedit.edit_ops import GlobalSettings
        from core.depthedit.project import project_path_for
        out_dir = eng.output_dir_var.get().strip() or os.path.join(os.path.dirname(project.depth_path), "edited")
        path = project_path_for(out_dir, project.depth_path)
        if not project.layers and project.global_settings == GlobalSettings() and not os.path.isfile(path):
            return

        def save():
            try:
                os.makedirs(out_dir, exist_ok=True)
                project.save(path)
                logger.info("Saved the Depth Map Editor project %s", path)
            except Exception as e:
                logger.exception("Could not save the Depth Map Editor project %s", path)
                self.toast.emit("warning", "Depth Map Editor", f"Could not save the edits to\n{path}\n\n{e}")
        if later:
            eng._submit("Saving project", save)
        else:
            save()

    def _claim_for_sam(self) -> bool:
        """Before an action that loads SAM 3 (~4 GB): claim the GPU like any
        other stage (audit #15) -- refused while another stage renders, and
        every other stage's resident weights released first. Nothing to claim
        while SAM 3 is already loaded."""
        eng = self._engine
        if eng is None or eng.engine.loaded:
            return True
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("depthedit", self)

    @Slot(int)
    def step(self, delta: int) -> None:
        self._run("_step", int(delta))

    @Slot(int)
    def moveLayer(self, delta: int) -> None:
        eng = self._engine
        if eng is not None:
            self._edit("Move layer", lambda: eng._move_layer(int(delta)))

    @Slot(int)
    def selectLayer(self, index: int) -> None:
        eng = self._engine
        if eng is None:
            return
        eng.layer_list.selection_set(int(index))
        self._run("_on_layer_select")

    @Slot(str)
    def setRange(self, which: str) -> None:
        eng = self._engine
        if eng is not None and which in ("start", "end", "clear"):
            self._edit("Layer frames", lambda: eng._set_range(which))

    def _pick_from_depthcrafter(self) -> None:
        mb = self._module.filedialog
        mb.answer, mb.asked = "", None
        self._run("_pick_from_depthcrafter")
        asked, mb.asked = mb.asked, None
        if asked:
            self.pickFile.emit(str(asked.get("title", "")), str(asked.get("initialdir", "")))

    # ---- the pointer (v1 _on_press / _on_release / _on_right_click) -----------------------

    @Slot(float, float, bool)
    def click(self, x: float, y: float, ctrl: bool) -> None:
        """A left click at picture-relative (x, y) with Add or Remove."""
        eng = self._engine
        if eng is None or eng.codes is None or not (0 <= x <= 1 and 0 <= y <= 1):
            return
        tool = eng.tool_var.get()
        if tool in ("Box", "Gradient", "Shape"):          # Shape: the page draws it (addShape)
            return
        if not self._claim_for_sam():
            return
        self._edit("Add click" if tool == "Add" else "Remove click",
                   lambda: eng._prompt_click((x, y), 1 if tool == "Add" else 0, new_object=bool(ctrl) and tool == "Add"))

    @Slot(float, float)
    def rightClick(self, x: float, y: float) -> None:
        eng = self._engine
        if eng is not None and eng.codes is not None and 0 <= x <= 1 and 0 <= y <= 1:
            if eng.tool_var.get() == "Shape":
                return
            if not self._claim_for_sam():
                return
            self._edit("Remove click", lambda: eng._prompt_click((x, y), 0, new_object=False))

    @Slot("QVariantList")
    def deletePoints(self, indices) -> None:
        """Delete key on picked points (user, 2026-10-13): those clicks of the selected
        layer on the frame showing go, and SAM 3 runs the layer's prompts again
        without them. `indices` are their places in the layer's clicks (markers' "i")."""
        eng = self._engine
        layer = eng._layer() if eng is not None and eng.codes is not None else None
        if layer is None:
            return
        if getattr(self, "_marker_layer", None) != layer.uid:
            # the points were picked on another layer's markers (a switch the picture hadn't
            # redrawn yet): their places would delete this layer's clicks (bug sweep 2026-10-14c)
            self._dirty()
            return
        frame = eng.frame_idx
        gone = sorted({int(i) for i in (indices or []) if 0 <= int(i) < len(layer.clicks)
                       and layer.clicks[int(i)].frame == frame and layer.clicks[int(i)].label in (0, 1)})
        if not gone:
            return
        if eng._job_pending():
            eng._status("Wait for the current job to finish before deleting points.")
            self._dirty()
            return
        left = [c for i, c in enumerate(layer.clicks) if i not in set(gone)]
        rerun = any(c.frame == frame for c in left) or (bool(layer.text) and layer.text_frame == frame)
        if rerun and not self._claim_for_sam():
            return
        label = "Delete point" if len(gone) == 1 else f"Delete {len(gone)} points"
        self._edit(label, lambda: self._delete_clicks(layer, frame, gone, rerun))

    def _delete_clicks(self, layer, frame: int, gone, rerun: bool) -> None:
        eng = self._engine
        drop = set(gone)
        layer.clicks = [c for i, c in enumerate(layer.clicks) if i not in drop]
        layer.tracked = False
        # SAM 3's session still holds them: the layer's prompts go in again from scratch
        if eng.engine.owner == layer.uid:
            eng.engine.owner = None
        n = len(gone)
        what = "point" if n == 1 else f"{n} points"
        if rerun:
            # the frame's mask from the deleted clicks goes too: SAM 3's replay sets it again from
            # what's left, and a replay that finds nothing left it standing (bug sweep 2026-10-14c)
            layer.masks.set(frame, None)
            if layer.masks_hi is not None:
                layer.masks_hi.set(frame, None)
            eng._results.pop((layer.uid, frame), None)
            if not eng._sam_ready():         # it says why; the clicks are gone all the same
                eng._refresh_layer_panel()
                eng._schedule_preview()
                return
            offload = eng.offload_var.get()

            def job() -> None:
                eng._session_for(layer, offload)
                eng._ui(eng._status, f"{layer.name}: {what} deleted on frame {frame + 1}. "
                                     "Start Tracking when it looks right.")

            eng._submit("Segmenting", job)
        else:
            # nothing left to prompt on this frame: its mask goes with them
            layer.masks.set(frame, None)
            if layer.masks_hi is not None:
                layer.masks_hi.set(frame, None)
            eng._results.pop((layer.uid, frame), None)
            eng._status(f"{layer.name}: {what} deleted on frame {frame + 1}, and the frame's mask with them.")
        eng._refresh_layer_panel()
        eng._schedule_preview()

    # ---- Shapes (user, 2026-10-17; core.depthedit.shapes): a layer drawn, held on every frame -------

    _shape_kind = "Points"

    @Slot(str)
    def setShapeKind(self, kind: str) -> None:
        from core.depthedit import shapes as SH
        if kind in SH.KINDS and kind != self._shape_kind:
            self._shape_kind = kind
            self._dirty()

    @Slot(str, "QVariantList", bool)
    def addShape(self, kind: str, pts, cut: bool = False) -> None:
        """A shape the page drew -- `pts` frame-relative [x, y]s: a polygon's points, or a rectangle's or
        ellipse's two corners; `cut`: it takes its area out instead. It goes on the selected layer when that
        layer is a shape layer (or empty); a layer SAM 3 tracks gets a new one, so a Track never wipes a
        shape and a shape is never tracked. The layer's mask: its shapes on every frame."""
        from core.depthedit import shapes as SH
        eng = self._engine
        if eng is None or eng.codes is None:
            return
        shape = {"kind": str(kind), "pts": [[float(p[0]), float(p[1])] for p in (pts or [])], "cut": bool(cut)}
        if not SH.valid(shape):
            eng._status("A shape needs three points or more (or a drag for a rectangle or an ellipse).")
            return
        if eng._job_pending():
            eng._status("Wait for the current job to finish before drawing a shape.")
            return

        def action():
            layer = eng._layer()
            if layer is None or layer.has_prompts() or (len(layer.masks) and not layer.shapes):
                layer = eng._new_layer()
                if layer is None:
                    return
                layer.name = f"Shape {sum(1 for ly in eng.project.layers if ly.shapes) + 1}"
            layer.shapes = SH.clean(list(layer.shapes) + [shape])
            if eng.engine.owner == layer.uid:
                eng.engine.owner = None
            for key in [k for k in eng._results if k[0] == layer.uid]:
                eng._results.pop(key, None)
            SH.fill(layer, eng._num_frames())
            eng._status(f"{layer.name}: {kind.lower()} {'cut out' if cut else 'added'} -- on every frame, "
                        "nothing to track.")
            eng._refresh_layer_panel()
            eng._schedule_preview()
        self._edit("Cut shape" if cut else "Shape", action)

    @Slot(int, int, str, float)
    def moveShapeEdge(self, index: int, edge: int, axis: str, delta: float) -> None:
        """One edge of the selected layer's shape `index` moved `delta` (frame-relative) along `axis` --
        an upright edge sideways ("x"), a flat one up or down ("y"): that side of the shape stretches,
        nothing else moves (user, 2026-10-25). Points and Rectangle shapes; Undo puts it back."""
        from core.depthedit import shapes as SH
        eng = self._engine
        layer = eng._layer() if eng is not None and eng.codes is not None else None
        if layer is None or not 0 <= index < len(layer.shapes or []) or axis not in ("x", "y") or not delta:
            return
        if eng._job_pending():
            eng._status("Wait for the current job to finish before moving a shape's edge.")
            return
        moved = SH.move_edge(layer.shapes[index], edge, axis, delta)
        if not SH.valid(moved) or moved["pts"] == layer.shapes[index]["pts"]:
            return

        def action():
            shapes = list(layer.shapes)
            shapes[index] = moved
            layer.shapes = SH.clean(shapes)
            SH.fill(layer, eng._num_frames())
            eng._status(f"{layer.name}: a {moved['kind'].lower()}'s edge moved -- on every frame.")
            eng._refresh_layer_panel()
            eng._schedule_preview()
        self._edit("Move shape edge", action)

    @Slot()
    def clearShapes(self) -> None:
        """The selected layer's shapes gone, and its mask with them."""
        from core.depthedit import shapes as SH
        eng = self._engine
        layer = eng._layer() if eng is not None and eng.codes is not None else None
        if layer is None or not layer.shapes:
            return

        def action():
            layer.shapes = []
            SH.fill(layer, eng._num_frames())
            eng._status(f"{layer.name}: shapes cleared.")
            eng._refresh_layer_panel()
            eng._schedule_preview()
        self._edit("Clear shapes", action)

    @Slot(float, float, float, float)
    def drag(self, ax: float, ay: float, bx: float, by: float) -> None:
        """A drag with Box or Gradient, start inside the picture (QML dropped
        anything under v1's 6 px); the end is clamped to the picture."""
        eng = self._engine
        if eng is None or eng.codes is None or not (0 <= ax <= 1 and 0 <= ay <= 1):
            return
        b = (min(max(bx, 0.0), 1.0), min(max(by, 0.0), 1.0))
        if eng.tool_var.get() == "Box":
            if not self._claim_for_sam():
                return
            self._edit("Box", lambda: eng._prompt_box((ax, ay), b))
        elif eng.tool_var.get() == "Gradient":
            self._edit("Gradient line", lambda: eng._set_gradient_line((ax, ay), b))

    # ---- the picture (v1 _render_preview, minus the canvas) -------------------------------

    def _render_preview(self) -> None:
        eng = self._engine
        if eng is None:
            return
        M = self._module
        n = eng._num_frames()
        eng.frame_label.configure(text=f"Frame {eng.frame_idx + 1} / {n}" if n else "Frame - / -")
        if not n:
            self.provider.put("frame", None)
            self._markers = {}
            self._serial += 1
            self.frameChanged.emit()
            return
        idx = min(eng.frame_idx, n - 1)
        view = eng.view_var.get()
        if view == self.VIEW_3D:
            self._request_warp(idx)
            return
        self._set_warp_note("")
        try:
            if view == "RGB" and eng.rgb is not None and idx < len(eng.rgb):
                img = eng.rgb[idx]
            else:
                if view == "Edited":
                    self._ensure_match_ref()
                unit = eng._edited_unit(idx) if view == "Edited" else eng._unit(idx)
                # the map's 10 bits shown, not 8: dithered, Colour depth from the
                # float depth (core.depthedit.display; user, 2026-10-05)
                from core.depthedit import display
                img = display.depth_view(unit, bool(eng.colormap_var.get()), bool(self._view_opts.get("dither", True)))
                img = self._rgb_over(img, idx)
            if self._view_opts.get("specks"):
                img = self._speck_tint(img, idx)
            if eng.overlay_var.get():
                img = self._overlay(img, idx, smooth=(view == "RGB"))
        except Exception:
            logger.exception("Depth Map Editor preview failed")
            return
        import numpy as np
        img = np.ascontiguousarray(img)
        h, w = img.shape[:2]
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.provider.put("frame", qimg)
        self._markers = self._marker_data(idx)
        self._serial += 1
        self.frameChanged.emit()

    #: Highlight Specks' colours: still to cover, and lowered by a Despeck layer
    SPECK_FREE = (13, 204, 255)
    SPECK_TAKEN = (255, 140, 13)

    def _speck_tint(self, img, idx: int):
        """Highlight Specks: every left-edge speck Despeck finds on this frame,
        tinted by how far it would come down -- cyan where no Despeck layer
        covers it yet, orange where one does -- in any view but 3D, so a layer
        can be placed on what is there and its catch checked frame by frame."""
        import cv2
        import numpy as np
        eng = self._engine
        g = eng.project.global_settings if eng.project is not None else None
        E = self._module.edit_ops
        unit = eng._unit(idx)
        if g is not None and g.match_on:
            # the specks of the map as Despeck gets it: Local Match runs first (bug sweep 2026-10-14c)
            self._ensure_match_ref()
            if g.has_match():
                unit = E.local_match(unit, g._match_ref, float(g.match_strength))
        took, covered = E.speck_highlight(unit, eng._layer_stack(idx), idx,
                                                              whole=bool(g is not None and g.has_despeck()),
                                                              global_args=(dict(g.despeck_args(), zone=g.despeck_zone())
                                                                           if g is not None else None))
        w = np.clip((took - 0.003) / 0.04, 0.0, 1.0)
        h, wd = img.shape[:2]
        if w.shape != (h, wd):
            w = cv2.resize(w, (wd, h), interpolation=cv2.INTER_LINEAR)
            covered = cv2.resize(covered.astype(np.uint8), (wd, h), interpolation=cv2.INTER_NEAREST) > 0
        col = np.where(covered[..., None], np.float32(self.SPECK_TAKEN), np.float32(self.SPECK_FREE))
        out = img.astype(np.float32) * (1.0 - w[..., None]) + col * w[..., None]
        return np.clip(out, 0, 255).astype(np.uint8)

    #: The preview is drawn at about this width so the selected mask's outline
    #: is one SCREEN pixel, not one depth pixel blown up by the view.
    OVERLAY_WIDTH = 1600

    def _overlay(self, img, idx: int, smooth: bool):
        """The masks over the picture (v1 _tint_masks, finer -- user,
        2026-09-28: its 2-depth-pixel outline, scaled up by the view, was too
        thick to judge the fit to an edge). Each layer's mask AS EDITED
        (snapped, grown or shrunk) is tinted lightly; the picture is then
        enlarged to about OVERLAY_WIDTH -- depth views by nearest neighbour, so
        each depth pixel stays a crisp block -- and the selected layer gets a
        one-pixel line along the inside of its mask's own border."""
        import cv2
        import numpy as np
        eng, M = self._engine, self._module
        h, w = img.shape[:2]
        s = max(1, min(3, int(round(self.OVERLAY_WIDTH / max(w, 1)))))
        # the RGB view is decoded finer than the depth map (up to 720p, user
        # 2026-10-03): each mask at the picture's own size -- an RGB layer's
        # full-size one when Render will use it (core.depthedit.hires)
        hires = M.hires
        use_hi = bool(eng.render_hires_var.get())
        masks, unit = [], None
        for i, layer in enumerate(eng.project.layers):
            if layer.masks is None:
                continue
            m = layer.masks.get(idx, layer.settings.steady)       # Steady (user, 2026-10-11)
            if m is None:
                continue
            if unit is None:
                unit = eng._unit(idx)
            hi = layer.masks_hi.get(idx, layer.settings.steady) if layer.masks_hi is not None else None
            em = hires.view_mask(m, hi, M.keys.settings_at(layer.settings, layer.keys, idx), unit, (w, h), use_hi)
            if em is not None:
                masks.append((i, layer, em.astype(bool)))
        outline_only = self._view_opts.get("maskStyle") == "outline"
        out = img.astype(np.float32)
        for i, layer, m in ([] if outline_only else masks):
            colour = np.array(M._hex_rgb(layer.colour), np.float32)
            strength = 0.3 if i == eng.selected else 0.2
            if not layer.enabled:
                strength *= 0.4
            out[m] = out[m] * (1 - strength) + colour * strength
        out = np.clip(out, 0, 255).astype(np.uint8)
        if s > 1:
            out = cv2.resize(out, (w * s, h * s), interpolation=cv2.INTER_LINEAR if smooth else cv2.INTER_NEAREST)
        # outline only: every layer's border (the selected one solid, the rest
        # half-strength, a switched-off layer faint); otherwise the selected one's
        for i, layer, m in masks:
            if i != eng.selected and not outline_only:
                continue
            mu = m.astype(np.uint8)
            if s > 1:
                mu = cv2.resize(mu, (w * s, h * s), interpolation=cv2.INTER_NEAREST)
            edge = (mu > 0) & (cv2.erode(mu, np.ones((3, 3), np.uint8)) == 0)
            colour = np.array(M._hex_rgb(layer.colour), np.float32)
            k = 1.0 if i == eng.selected else 0.55
            if not layer.enabled:
                k *= 0.4
            out[edge] = (out[edge].astype(np.float32) * (1 - k) + colour * k).astype(np.uint8)
        return out

    def _rgb_over(self, img, idx: int):
        """RGB over depth: the clip's frame (shrunk to the map's size) blended
        over a depth view at rgbOpacity."""
        eng = self._engine
        a = float(self._view_opts.get("rgbOpacity", 0.5))
        if not self._view_opts.get("rgbOverlay") or a <= 0 or eng.rgb is None or idx >= len(eng.rgb):
            return img
        import cv2
        import numpy as np
        rgb = eng.rgb[idx]
        if rgb.shape[:2] != img.shape[:2]:
            rgb = cv2.resize(rgb, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
        return np.clip(img.astype(np.float32) * (1 - a) + rgb.astype(np.float32) * a, 0, 255).astype(np.uint8)

    def _marker_data(self, idx: int) -> Dict[str, Any]:
        """v1 _draw_markers, as data in picture-relative coordinates."""
        eng = self._engine
        layer = eng._layer()
        out: Dict[str, Any] = {"points": [], "boxes": [], "gradient": None, "colour": "", "zone": None}
        # the Global Despeck's zone, drawn while its page is open (user, 2026-10-12)
        g = eng.project.global_settings if eng.project is not None else None
        if g is not None and g.has_despeck() and eng.page_var.get() == "Global" and g.despeck_zone() is not None:
            out["zone"] = list(g.despeck_zone())
        if layer is None:
            return out
        out["colour"] = layer.colour
        self._marker_layer = layer.uid      # whose clicks the drawn points' "i" index
        box = [cl for cl in layer.clicks if cl.frame == idx and cl.label in (2, 3)]
        for i in range(0, len(box) - 1, 2):
            out["boxes"].append([box[i].x, box[i].y, box[i + 1].x, box[i + 1].y])
        for i, cl in enumerate(layer.clicks):
            if cl.frame == idx and cl.label in (0, 1):
                # i: its place in the layer's clicks, so the picture can pick it to delete
                out["points"].append({"x": cl.x, "y": cl.y, "add": cl.label == 1, "i": i})
        g = layer.settings.gradient
        if g.enabled and (eng.tool_var.get() == "Gradient" or eng.page_var.get() == "Gradient"):
            E = self._module.edit_ops
            m = layer.masks.get(idx, layer.settings.steady) if layer.masks is not None else None
            bbox = E.mask_bbox(E.effective_mask(m, layer.settings, eng._unit(idx))) if m is not None else None
            (ax, ay), (bx, by) = E.gradient_endpoints(g, eng.info.width, eng.info.height, bbox)
            out["gradient"] = [ax / eng.info.width, ay / eng.info.height, bx / eng.info.width, by / eng.info.height]
        return out

    # ---- the state QML reads ----------------------------------------------------------------

    def _dirty(self) -> None:
        self._rebuild.start()

    def _build_state(self) -> None:
        eng = self._engine
        s: Dict[str, Any] = {"ready": eng is not None, "loading": self._loading, "loadError": self._load_error}
        if eng is not None:
            M = self._module
            for name in ("rgb_path", "depth_path", "output_dir", "view", "tool", "page", "overlay", "colormap",
                         "offload", "handoff", "render_hires", "text", "frame", "status", "sam_status", "layer_name",
                         "layer_source", "layer_enabled", "layer_info", "range"):
                s[name] = getattr(eng, name + "_var").get()
            w = self._widgets
            s["frames"] = eng._num_frames()
            s["frameText"] = (w.get("frame_label") or {}).get("text", "Frame - / -")
            lst = w.get("layer_list") or {}
            s["layers"] = [{"name": str(t).strip(), "colour": c or "#888888"}
                           for t, c in zip(lst.get("items", []), lst.get("colours", []))]
            s["selected"] = eng.selected if eng.selected is not None else -1
            for btn in ("track_btn", "stop_btn", "render_btn", "render_stop_btn", "install_btn", "weights_btn",
                        "model_btn"):
                s[btn] = (w.get(btn) or {}).get("state", "normal") != "disabled"
            s["trackProgress"] = float((w.get("track_progress") or {}).get("value", 0) or 0)
            s["renderProgress"] = float((w.get("render_progress") or {}).get("value", 0) or 0)
            s["busyJob"] = eng._busy_job or ""
            s["busy"] = bool(eng.is_busy())
            s["aspect"] = float(eng.rgb_aspect or 16 / 9)
            s["hasClip"] = eng.codes is not None
            live = self._history_live()
            s["canUndo"] = live and self._history.can_undo
            s["canRedo"] = live and self._history.can_redo
            s["undoLabel"] = self._history.undo_label if live else ""
            s["redoLabel"] = self._history.redo_label if live else ""
            # Upscale Map (user, 2026-10-05): done for this clip once the map is read enlarged
            s["upscaled"] = bool(eng.info is not None and getattr(eng.info, "file_size", None))
            s["hasLayer"] = eng._layer() is not None
            s["shapeKind"] = self._shape_kind
            s["shapeCount"] = len(getattr(eng._layer(), "shapes", None) or []) if eng._layer() is not None else 0
            # the selected layer's shapes, for dragging one edge (2026-10-25): kind, points, edges
            from core.depthedit import shapes as _SH
            s["shapes"] = [{"kind": sh["kind"], "pts": sh["pts"], "edges": _SH.edges(sh)}
                           for sh in (getattr(eng._layer(), "shapes", None) or [])] if eng._layer() is not None else []
            s["controls"] = self._controls(M, eng)
            layer = eng._layer()
            track = layer.keys if layer is not None else None
            s["keyframes"] = ([{"frame": f, "kind": k, "curve": track.curve_at(f)} for f, k, _v in track.items_sorted()]
                              if track is not None else [])
            here = track.frame_at(eng.frame_idx) if track is not None else None
            s["onKey"] = here["type"] if here else ""
            self._video_px(s["controls"], M)
            s["erase"] = self._erase_state(eng)
        self._state = s
        self.stateChanged.emit()

    @staticmethod
    def _controls(M, eng) -> Dict[str, Any]:
        """Every edit control: {key: {value, label, from, to, step, readout, help}}."""
        out: Dict[str, Any] = {}

        def entry(key, var, spec=None):
            value = var.get()
            d: Dict[str, Any] = {"value": value}
            if spec is not None:
                _path, label, lo, hi, steps, fmt, help_key = spec
                d.update({"label": label, "from": lo, "to": hi, "step": (hi - lo) / max(int(steps), 1),
                          "decimals": 0 if isinstance(lo, int) and isinstance(hi, int) else 3,
                          "readout": fmt(value), "help": eng.help_data.get(help_key, "")})
            out[key] = d
        for path, var in eng._layer_controls:
            entry(".".join(path), var, eng.control_specs.get(path))
        for name, var in eng._global_controls:
            entry("global." + name, var, eng.control_specs.get(("global", name)))
        return out

    def _video_px(self, controls: Dict[str, Any], M) -> None:
        """Re-express the VIDEO_PX controls in video pixels, half-pixel steps;
        every control also gets its default, in the units it shows."""
        for key, d in controls.items():
            if "from" not in d:
                continue
            if key.startswith("global."):
                d["default"] = M._default_for((key[len("global."):],), "global")
            else:
                d["default"] = M._default_for(tuple(key.split(".")), "layer")
            if key not in self.VIDEO_PX:
                continue
            lo, hi, signed, axis = self.VIDEO_PX[key]
            k = self._px_scale(axis)
            shown = float(d["value"]) * k
            d.update({"value": shown, "from": lo, "to": hi, "step": self.VIDEO_PX_STEP, "decimals": 1,
                      "default": float(d["default"]) * k,
                      "readout": (f"{shown:+.1f} px" if signed else f"{shown:.1f} px").replace("+0.0", "0.0")})

    @Property("QVariantMap", notify=stateChanged)
    def tables(self):
        """Each section's sliders, in v1's order (the tables the generator
        lifted from v1's page builders)."""
        M = self._module
        if M is None:
            return {"layer": [], "edge": [], "gradient": [], "global": []}
        return {"layer": [".".join(s[0]) for s in M.LAYER_SLIDERS],
                "edge": [".".join(s[0]) for s in M.EDGE_SLIDERS],
                "gradient": [".".join(s[0]) for s in M.GRADIENT_SLIDERS],
                "global": ["global." + s[0] for s in M.GLOBAL_SLIDERS]}

    @Property("QVariantMap", notify=stateChanged)
    def choices(self):
        if self._module is None:
            return {}
        E = self._module.edit_ops
        from core.depthedit import shapes as SH
        tools = list(self._module.TOOLS) + ([] if "Shape" in self._module.TOOLS else ["Shape"])   # 2026-10-17
        return {"view": list(self._module.VIEWS) + [self.VIEW_3D], "tool": tools, "shapeKinds": list(SH.KINDS),
                "page": list(self._module.PAGES),
                "source": list(self._module.SOURCES), "shape": list(E.GRADIENT_SHAPES),
                "mode": ["Alter", "Add", "Erase"],
                "blend": list(E.GRADIENT_BLENDS)}

    @Property("QVariantMap", constant=True)
    def help(self):
        try:
            import json
            with open(REPO / "dependency" / "depthedit_help.json", "r", encoding="utf-8") as f:
                return {k: v for k, v in json.load(f).items() if isinstance(v, str)}
        except Exception:
            return {}

    # ---- the stage contract --------------------------------------------------------------

    busy = Property(bool, lambda self: self.is_busy(), notify=stateChanged)
    status = Property(str, lambda self: str(self._engine.status_var.get()) if self._engine else "", notify=stateChanged)

    def is_busy(self) -> bool:
        return bool(self._erasing() or (self._engine is not None and self._engine.is_busy()))

    def holds_gpu(self) -> bool:
        return bool(self._engine is not None and self._engine.holds_gpu())

    def release_gpu(self):
        """gpu_jobs: another stage is starting (the Event: when SAM 3 is gone)."""
        return self.releaseGpu()

    # ---- Splatting <-> the editor (user, 2026-09-28) ------------------------------------

    @staticmethod
    def backup_path(depth: str) -> str:
        """Where "Use in Splatting" keeps the original of a depth map it
        replaced: edit_history/<name>.original<ext> beside it (Multi-Map
        skips that folder)."""
        stem, ext = os.path.splitext(os.path.basename(depth))
        return os.path.join(history_dir(depth), f"{stem}.original{ext}")

    fromSplattingChanged = Signal()
    fromSplatting = Property(str, lambda self: (self._from_splat or {}).get("rgb", ""), notify=fromSplattingChanged)

    @Slot(str, str)
    def openFromSplatting(self, rgb: str, depth: str) -> None:
        """Splatting's Edit Depth Map: this clip and its depth map, loaded. A
        map already replaced by an edit is read from its backed-up original,
        so the saved layers are not applied twice."""
        rgb, depth = str(rgb).replace("\\", "/"), str(depth).replace("\\", "/")
        self._from_splat = {"rgb": rgb, "depth": depth}
        self.fromSplattingChanged.emit()
        source = map_to_edit(depth)
        out_dir = history_dir(depth).replace("\\", "/")

        def go():
            eng = self._engine
            self._keep_edits(later=True)  # audit #12: the clip open now; on the worker (bug sweep A9)
            eng.rgb_path_var.set(rgb)
            eng.depth_path_var.set(source)
            eng.output_dir_var.set(out_dir)
            eng._save_settings()
            # v1's _load_clip, with the output folder given: its default-folder
            # rule would put it in edited/
            self._act(lambda: eng._submit("Loading clip", lambda: eng._load_job(rgb, source, out_dir)))
        if self._engine is not None:
            go()
        else:
            self._pending_open = go
            self.ensureLoaded()

    @Slot(str, result=str)
    def useInSplatting(self, written: str) -> str:
        """The rendered map takes the place of the one Splatting reads (same
        name, so its sidecar still applies); what it replaces is kept in
        edit_history. Returns the path Splatting reads, or
        "" (with a message) when it could not."""
        info = self._from_splat or {}
        live = info.get("depth", "")
        if not live or not os.path.isfile(written):
            return ""
        backup = self.backup_path(live)
        try:
            use_edited_map(written, live)
        except OSError as e:
            self.message.emit("error", "Use in Splatting", f"Could not replace\n{live}\n\n{e}")
            return ""
        self.toast.emit("info", "Depth map updated",
                        f"Splatting now reads the edited map. The original and earlier edits are kept in\n"
                        f"{os.path.dirname(backup)}")
        return live

    @Slot()
    def uninstallSam(self) -> None:
        """Settings > SAM 3 > Uninstall (user, 2026-09-28): the model unloaded
        and its checkpoints (weights/SAM3, several GB) sent to the Recycle Bin.
        The packages stay: they are small, declared in pyproject.toml, and
        scipy is used elsewhere. The page's SAM 3 section comes back so it can
        be set up again."""
        from core.common.recycle_bin import send_to_recycle_bin
        from core.depthedit import sam3_backend as sam
        if self._engine is not None:
            self._engine.release_gpu()
        gone, kept = [], []
        for version in sam.MODELS:
            path = sam.checkpoint_path(version)
            if path.is_file():
                (gone if send_to_recycle_bin(str(path)) else kept).append(path.name)
        if self._engine is not None:
            self._run("_refresh_sam_status")
        if kept:
            self.message.emit("warning", "Uninstall SAM 3",
                              "Could not move to the Recycle Bin (in use, or no permission):\n\n"
                              + "\n".join(kept) + f"\n\nThey are in {sam.WEIGHTS_DIR}.")
        elif gone:
            self.toast.emit("info", "SAM 3 uninstalled",
                            "Its weights (" + ", ".join(gone) + ") are in the Recycle Bin. "
                            "Download Weights on the Segment page sets it up again.")
        else:
            self.toast.emit("info", "SAM 3", "No SAM 3 weights were found to remove.")

    # ---- Erase from Clip (user, 2026-10-05; core.removal) ---------------------------------

    #: Erase from Clip's settings (user, 2026-10-06): steps (quality vs time), the
    #: seed (another fill), ROSE's working size, frames per pass (VRAM), and how
    #: far past the object it repaints / how soft the blend back is (clip px).
    #: method (user, 2026-10-06): "ROSE" takes an object out with ROSE; "Static" takes off
    #: reflections that hold still on glass in a locked-off shot (core.removal.static_reflect),
    #: no model -- static_size: the widest reflection shape kept, clip px
    ERASE_DEFAULTS = {"method": "ROSE", "steps": 50, "seed": 42, "size": "480p", "window": 81, "grow": 12,
                      "feather": 6, "static_size": 31,
                      # Dust (user, 2026-10-17; static_reflect.erase_dust): the widest speck, clip px
                      "dust_size": 9,
                      # user, 2026-10-15: a big object pre-filled (else ROSE redraws it); a full-size pass
                      "prefill": "Auto", "refine": False}
    ERASE_METHODS = ("ROSE", "Static", "Dust")
    ERASE_LIMITS = {"steps": (1, 200), "seed": (0, 2**31 - 1), "grow": (0, 200), "feather": (0, 100),
                    "static_size": (3, 301), "dust_size": (3, 61)}

    @Slot(str, "QVariant")
    def setEraseOption(self, name: str, value) -> None:
        from core.removal import erase as E
        if name not in self.ERASE_DEFAULTS:
            return
        try:
            if name == "method":
                value = str(value)
                if value not in self.ERASE_METHODS:
                    raise ValueError(value)
            elif name == "size":
                value = str(value)
                if value not in E.SIZES:
                    raise ValueError(value)
            elif name == "window":
                value = int(float(value))
                if value not in E.WINDOWS:
                    raise ValueError(value)
            elif name == "prefill":
                value = str(value)
                if value not in E.PREFILLS:
                    raise ValueError(value)
            elif name == "refine":
                value = bool(value)
            else:
                lo, hi = self.ERASE_LIMITS[name]
                value = max(lo, min(hi, int(float(str(value).strip()))))
        except (TypeError, ValueError):
            self._dirty()                     # the field shows the value it had again
            return
        self._erase_opts[name] = value
        settings_store.save("depthedit_erase", dict(self._erase_opts))
        if name == "method":
            self._sync_erase_fill()
        self._dirty()

    #: the clip _sync_erase_fill last decided for (a clip switch decides again)
    _fill_clip = None

    def _sync_erase_fill(self) -> None:
        """Erase layers fill the depth only for ROSE (edit_ops.ERASE_FILLS_DEPTH; user, 2026-10-17): Dust and
        Static leave the map as it is -- the glass is seen through. An erased clip by its own erase, else by
        the Method (putback.erase_fills_depth). The picture is drawn again when it changes."""
        from core.removal import putback as P
        M = self._module
        if M is None:
            return
        eng = self._engine
        clip = eng.project.rgb_path if eng is not None and getattr(eng, "project", None) is not None else ""
        self._fill_clip = clip
        try:
            fills = P.erase_fills_depth(clip, self._erase_opts.get("method", "ROSE"))
        except Exception:
            fills = self._erase_opts.get("method", "ROSE") == "ROSE"
        if M.edit_ops.ERASE_FILLS_DEPTH != fills:
            M.edit_ops.ERASE_FILLS_DEPTH = fills
            eng = self._engine
            if eng is not None and getattr(eng, "codes", None) is not None:
                eng._schedule_preview()

    def _erasing(self) -> bool:
        return self._erase_thread is not None and self._erase_thread.is_alive()

    def _erase_layers(self, eng):
        return [ly for ly in (eng.project.layers if eng.project is not None else [])
                if ly.enabled and ly.settings.mode == "Erase"]

    def _erase_state(self, eng) -> Dict[str, Any]:
        from core.removal import erase as E, rose_env
        clip = eng.project.rgb_path if eng.project is not None else ""
        if clip != self._fill_clip:
            self._sync_erase_fill()           # another clip: its own erase decides the depth fill
        erased = bool(clip) and os.path.isfile(clip) and E.source_for(clip) != clip
        if self._rose_ready is None:          # a few file checks: once, not per redraw
            self._rose_ready = rose_env.ready()
        return {"ready": self._rose_ready, "layers": len(self._erase_layers(eng)), "clip": bool(clip),
                "task": self._erase["task"], "progress": self._erase["progress"], "text": self._erase["text"],
                "erased": erased, "downloadGb": rose_env.DOWNLOAD_GB, "opts": dict(self._erase_opts),
                "sizes": list(E.SIZES), "windows": [str(w) for w in E.WINDOWS], "methods": list(self.ERASE_METHODS),
                "prefills": list(E.PREFILLS),
                "putback": self._putback_state(clip) if erased else {}}

    # ---- the erased object put back in 3D (user, 2026-10-06; core.removal.putback) ----------

    def _putback_state(self, clip: str) -> Dict[str, Any]:
        from core.removal import putback as P
        s = P.load_settings(clip)
        s["opacity"] = round(s["opacity"] * 100)
        s["modes"] = list(P.modes_for(P.erased(clip)))
        if s["mode"] not in s["modes"]:
            s["mode"] = s["modes"][0]        # a Static erase: Add (bug sweep A9)
        s["tracked"] = bool(s.pop("track"))   # the per-frame values stay in the file
        return s

    @Slot(str, "QVariant")
    def setPutBack(self, name: str, value) -> None:
        """Put back on / off, Add / Over, parallax (% of width, + in front),
        opacity (%): saved beside the clip, where Merging reads it."""
        from core.removal import putback as P
        eng = self._engine
        clip = eng.project.rgb_path if eng is not None and eng.project is not None else ""
        if not clip or P.erased(clip) is None or name not in P.DEFAULTS:
            return
        s = P.load_settings(clip)
        try:
            if name == "enabled":
                s["enabled"] = bool(value)
            elif name == "mode":
                if value not in P.modes_for(P.erased(clip)):
                    raise ValueError(value)
                s["mode"] = str(value)
            elif name == "parallax":
                s["parallax"] = max(-10.0, min(10.0, float(str(value).strip().replace("%", ""))))
                s["track"] = []                   # typed: one parallax on every frame
            elif name == "factor":
                s["factor"] = max(0.0, min(4.0, float(str(value).strip().lstrip("x×"))))
            elif name == "opacity":
                s["opacity"] = max(0.0, min(1.0, float(str(value).strip().replace("%", "")) / 100.0))
        except (TypeError, ValueError):
            self._dirty()
            return
        P.save_settings(clip, s)
        self._dirty()

    @Slot()
    def samplePutBack(self) -> None:
        """A parallax for every frame (user, 2026-10-06): the map's median
        under the Erase layers' masks on that frame, before erasing, times
        the depth factor (x1 on the surface, x0.5 inside it, as a mirror
        image sits), through the clip's Splatting settings. Frames without a
        mask take the nearest frame's value. Merging follows the track.

        It reads every frame, so it runs on the editor's worker, after any job
        there: on the UI thread a long clip froze the window for seconds (bug
        sweep 2026-10-09, A9)."""
        from core.removal import putback as P
        eng = self._engine
        if eng is None or eng.project is None or eng.codes is None:
            return
        clip = eng.project.rgb_path
        conv, maxd, where = self._splat_settings()
        layers = self._erase_layers(eng)
        n = eng._num_frames()

        def job() -> None:
            factor = float(P.load_settings(clip).get("factor", 1.0))
            track = self._sample_track(eng, layers, n, factor, conv, maxd)
            eng._ui(self._sampled, clip, track, factor, conv, maxd, where)

        eng._status("Sampling the put-back parallax...")
        eng._submit("Sampling", job)
        self._dirty()

    @staticmethod
    def _sample_track(eng, layers, n: int, factor: float, conv, maxd):
        """samplePutBack's per-frame parallax (None: no Erase mask anywhere)."""
        import bisect
        import numpy as np
        from core.removal import putback as P
        track = [None] * n
        for idx in range(n):
            m = None
            for ly in layers:
                lm = ly.masks.get(idx) if ly.masks is not None else None
                if lm is not None:
                    m = lm if m is None else (m | lm)
            if m is not None and m.any():
                track[idx] = P.parallax_for_depth(float(np.median(eng._unit(idx)[m])) * factor, conv, maxd)
        have = [i for i, v in enumerate(track) if v is not None]
        if not have:
            return None
        for i in range(n):                     # gaps: the nearest frame's value
            if track[i] is None:
                k = bisect.bisect_left(have, i)
                near = [have[j] for j in (k - 1, k) if 0 <= j < len(have)]
                track[i] = track[min(near, key=lambda j: abs(j - i))]
        return track

    def _sampled(self, clip: str, track, factor: float, conv: float, maxd: float, where: str) -> None:
        """samplePutBack's result, on the UI thread (the factor and Splatting
        settings it sampled with, for the toast)."""
        import numpy as np
        from core.removal import putback as P
        eng = self._engine
        if track is None:
            if eng is not None:
                eng._status("")
            self.message.emit("info", "Sample", "No Erase layer has a mask on any frame. Track the object first.")
            self._dirty()
            return
        s = P.load_settings(clip)
        s["track"] = [round(v, 3) for v in track]
        s["parallax"] = round(float(np.median(track)), 2)
        P.save_settings(clip, s)
        if eng is not None:
            eng._status(f"Put-back parallax sampled over {len(track)} frames.")
        self._dirty()
        self.toast.emit("info", "Put back at the surface's depth",
                        f"x{factor:g} of the depth under the mask on each of {len(track)} frames: parallax "
                        f"{min(track):+.2f}% to {max(track):+.2f}% (convergence {conv:g}, max disparity {maxd:g}, {where}).")

    def _splat_settings(self):
        """(convergence, max disparity, where from) for the clip's map: its
        Splatting sidecar (<stem>_depth.fssidecar beside the map, or in its
        sidecars/), else Splatting's defaults."""
        from core.common.sidecar_manager import SidecarConfigManager
        eng = self._engine
        live = (self._from_splat or {}).get("depth") or (eng.project.depth_path if eng.project else "")
        folder = os.path.dirname(live)
        if os.path.basename(folder).lower() == "edit_history":
            folder = os.path.dirname(folder)
        stem = os.path.splitext(os.path.basename(live))[0].split(".")[0]
        for f in (os.path.join(folder, f"{stem}.fssidecar"), os.path.join(folder, "sidecars", f"{stem}.fssidecar")):
            if os.path.isfile(f):
                d = SidecarConfigManager().load_sidecar_data(f)
                return float(d.get("convergence_plane", 0.5)), float(d.get("max_disparity", 20.0)), "its sidecar"
        return 0.5, 20.0, "Splatting's defaults: no sidecar found"

    def _start_erase_thread(self, task: str, work) -> None:
        self._erase_stop.clear()
        self._erase = {"task": task, "progress": 0.0, "text": "Starting"}

        def run():
            try:
                ok, text = work()
            except Exception as e:
                logger.exception("%s failed", task)
                ok, text = False, f"{e.__class__.__name__}: {e}"
            self._sigEraseDone.emit(ok, text)
        self._erase_thread = threading.Thread(target=run, name=f"depthedit-{task}", daemon=True)
        self._erase_thread.start()
        self._dirty()

    @Slot()
    def eraseFromClip(self) -> None:
        """Every enabled Erase layer's object out of the clip, with ROSE. The
        page asked first (it replaces the clip; it takes minutes)."""
        from core.removal import erase as E, rose_env
        eng = self._engine
        if eng is None or eng.project is None or self._erasing() or eng.is_busy():
            return
        clip = eng.project.rgb_path
        if not clip or not os.path.isfile(clip):
            self.message.emit("warning", "Erase from Clip", "Erase works on the colour clip: load the clip with its RGB.")
            return
        method = self._erase_opts.get("method")
        static = method in ("Static", "Dust")          # no ROSE (Dust: the GPU, briefly)
        if not static and not rose_env.ready():
            self.message.emit("warning", "Erase from Clip", "ROSE isn't set up yet: Set Up ROSE first.")
            return
        import copy
        layers = [E.EraseLayer(copy.deepcopy(ly.settings), copy.deepcopy(ly.keys), ly.masks.snapshot(),
                               ly.masks_hi.snapshot() if ly.masks_hi is not None else None)
                  for ly in self._erase_layers(eng)]
        if not layers:
            self.message.emit("info", "Erase from Clip", "No layer is set to Erase. Pick a layer, set its Mask to Erase.")
            return
        opts = dict(self._erase_opts)         # as they are when Erase is pressed
        jobs = getattr(self, "jobs", None)
        if static:
            # no model, no GPU -- but the claim makes the other stages' previews
            # let go of the clip, which the erase replaces at the end (bug sweep
            # 2026-10-09 #11: the rename failed and the work was thrown away)
            from core.removal import static_reflect
            if jobs is not None and not jobs.claim("depthedit", self):
                return

            def work_static():
                wait_for_gpu(self)
                progress = lambda f, t: self._sigErase.emit(float(f), str(t))  # noqa: E731
                if method == "Dust":
                    original = static_reflect.erase_dust(clip, layers, progress, self._erase_stop,
                                                         size=int(opts["dust_size"]))
                    # the dust goes back on the glass: Put Back on, Add, Glass Plane (user, 2026-10-17)
                    try:
                        from core.removal import putback as _P
                        s = _P.load_settings(clip)
                        s.update(enabled=True, mode="Add", depth="Glass Plane")
                        _P.save_settings(clip, s)
                    except Exception:
                        logger.warning("Put Back: could not set the dust's put-back", exc_info=True)
                    return True, (f"The dust is off {os.path.basename(clip)}, in place. Merging's Put Back lays it on "
                                  f"the glass (Add, Glass Plane). The original is kept as {original}.")
                original = static_reflect.erase_static(
                    clip, layers, progress, self._erase_stop, size=int(opts["static_size"]))
                return True, (f"The still reflections are off {os.path.basename(clip)}, in place. Put back sets them at "
                              f"their own depth. The original is kept as {original}.")
            self._start_erase_thread("erase", work_static)
            return
        if jobs is not None and not jobs.claim("depthedit", self):
            return
        released = eng.release_gpu()          # SAM 3 out of the way: ROSE needs the card (~5 GB)
        # the depth map the clip has now -- the object in it -- for Put Back's Depth Map (user, 2026-10-15):
        # DepthCrafter on the erased clip writes its map under the same name
        depth_map = (self._from_splat or {}).get("depth") or (eng.project.depth_path if eng.project else "")

        def work():
            if released is not None:
                released.wait(60)
            wait_for_gpu(self)
            o = opts
            original = E.erase(clip, layers, lambda f, t: self._sigErase.emit(float(f), str(t)), self._erase_stop,
                               steps=o["steps"], seed=o["seed"], work_area=E.SIZES[o["size"]], window=o["window"],
                               grow=o["grow"], feather=o["feather"], prefill=o["prefill"], refine=bool(o["refine"]))
            try:
                from core.removal import putback as _P
                _P.keep_original_depth(clip, depth_map)
            except Exception:
                logger.warning("Put Back: could not keep the clip's depth map; it will use parallax", exc_info=True)
            return True, (f"{os.path.basename(clip)} is erased in place; Splatting and DepthCrafter now read it. "
                          f"The original is kept as {original}.")
        self._start_erase_thread("erase", work)

    @Slot()
    def setupRose(self) -> None:
        """Clone ROSE, build its environment, fetch its weights (the page
        asked first: it downloads ~3.7 GB)."""
        from core.removal import rose_env
        if self._erasing():
            return

        def work():
            def log(line):
                logger.info("ROSE setup: %s", line)
                self._sigErase.emit(0.0, line[-120:])
            try:
                rose_env.setup(log, self._erase_stop)
            except RuntimeError as e:
                if self._erase_stop.is_set():
                    return False, "Set-up stopped. Set Up ROSE again carries on where it left off."
                raise
            return True, "ROSE is set up."
        self._start_erase_thread("setup", work)

    @Slot()
    def cancelErase(self) -> None:
        if self._erasing():
            self._erase_stop.set()
            self._erase["text"] = "Stopping..."
            self._dirty()

    @Slot(float, str)
    def _on_erase_progress(self, fraction: float, text: str) -> None:
        self._erase["progress"], self._erase["text"] = fraction, text
        self._dirty()

    @Slot(bool, str)
    def _on_erase_done(self, ok: bool, text: str) -> None:
        task = self._erase["task"]
        self._erase_thread = None
        self._rose_ready = None
        stopped = self._erase_stop.is_set()
        self._erase = {"task": "", "progress": 0.0, "text": ""}
        title = "Erase from Clip" if task == "erase" else "Set Up ROSE"
        if stopped and task == "erase":
            self.toast.emit("info", title, "Stopped. The clip is as it was.")
        elif ok:
            self.toast.emit("success", title, text)
        else:
            self.message.emit("error", title, text)
        self._sync_erase_fill()               # the clip is an erase now: its method decides
        self._dirty()

    @Slot()
    def releaseGpu(self):
        """SAM 3 unloads on the editor's worker; the Event says when (#14)."""
        if self._engine is not None:
            done = self._engine.release_gpu()
            self._dirty()
            return done
        return None

    @Slot()
    def save(self) -> None:
        """Save All Settings: the editor's settings (it has none to write
        before its engine is built)."""
        if self._engine is not None:
            self._engine._save_settings()

    def shutdown(self) -> None:
        if self._erasing():
            self._erase_stop.set()            # ROSE's process is killed; the clip stays as it was
            # long enough for its temp folder to go too (a leftover one is swept
            # at the next erase -- bug sweep 2026-10-09, A9)
            self._erase_thread.join(timeout=30)
        self._poll.stop()
        for t in list(self._timers.values()):
            t.stop()
        self._timers.clear()
        if self._engine is not None:
            self._engine.shutdown()
            self._keep_edits()  # audit #12: after the worker has stopped

    def image_for_test(self) -> Optional[QImage]:
        return self.provider._images.get("frame")
