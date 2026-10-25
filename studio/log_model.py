"""log_model.py -- the Python log as a list model QML can show.

A ``logging.Handler`` formats each record and hands it to the model through a
queued signal, so records logged on the DepthCrafter worker thread are added
on the UI thread. The model keeps the newest :data:`MAX_ROWS` lines; older
ones are dropped in blocks so a long batch does not grow without bound.

Progress bars never go through logging: tqdm (diffusers' denoise loop) and
``core.common.cli_utils.draw_progress_bar`` write straight to stderr/stdout
and redraw with '\\r'. :class:`StreamTee` replaces both streams, as v1's
console did, and keeps one *live row* per bar that each '\\r' overwrites in
place; the newline that ends the bar commits it.

:class:`LogHub` is the console window's tabs (v1 unified_gui/console_window,
user 2026-09-30): one model per stage plus All. A record goes to its stage's
tab by logger name; shared code (core/, video_io ...) goes to the stage whose
job is running, or to All only when none is. Everything reaches All.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time

from PySide6.QtCore import (QAbstractListModel, QByteArray, QModelIndex, QObject, Qt, Signal, Slot,
                            Property)

MAX_ROWS = 5000
_TRIM = 500

# ---- progress bars, as v1's console read them (unified_gui/console_window) ----
#
# Two producers write '\r' bars: core.common.cli_utils.draw_progress_bar
# ("Prefix |█████-----| 45.3% suffix") and tqdm ("desc: 45%|####5  | 5/10 [..]").
# The console draws every recognised bar itself -- an accent-coloured span over
# a faint trough, stretched across the pane (user, 2026-09-30: v1's style) --
# so the model hands QML its parts: (left, fraction 0-1, right).
_BAR_RE = re.compile(r"^(?P<left>[^|\n]*)\|(?P<bar>[█▉▊▋▌▍▎▏#0-9 \-]{4,})\|(?P<right>[^\n]*)$")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_COUNT_RE = re.compile(r"(?<![\d.])(\d+)/(\d+)(?![\d.])")
_EIGHTHS = {"▏": 1, "▎": 2, "▍": 3, "▌": 4, "▋": 5, "▊": 6, "▉": 7}


def parse_bar(line: str):
    """v1 _parse_bar: (left, fraction, right) for a progress-bar line, else
    None. The fraction from tqdm's n/total, then the printed %, then the glyphs."""
    m = _BAR_RE.match(line or "")
    if m is None:
        return None
    left, bar, right = m.group("left"), m.group("bar"), m.group("right")
    if not any(ch in bar for ch in "█▉▊▋▌▍▎▏#-"):
        if bar.strip() or not left.rstrip().endswith("%"):
            return None
    fraction = None
    count = _COUNT_RE.search(right)
    if count is not None and int(count.group(2)) > 0:
        fraction = int(count.group(1)) / int(count.group(2))
    if fraction is None:
        pct = _PERCENT_RE.search(left[-6:]) or _PERCENT_RE.search(right)
        if pct is not None:
            fraction = float(pct.group(1)) / 100.0
    if fraction is None:
        filled = 0.0
        for ch in bar:
            if ch in ("█", "#"):
                filled += 1
            elif ch in _EIGHTHS:
                filled += _EIGHTHS[ch] / 8
            elif ch.isdigit():
                filled += int(ch) / 10
        fraction = filled / len(bar)
    return left.rstrip(), max(0.0, min(1.0, fraction)), right.rstrip()


def _row(text: str, level: int, stamp: str, record: bool):
    """(text, level, stamp, prefix, bar): a record gets v1's 'HH:MM:SS - LEVEL - '
    prefix; a printed line none, and its bar parts when it is a bar."""
    prefix = f"{stamp} - {logging.getLevelName(level)} - " if record else ""
    bar = [] if record else list(parse_bar(text) or [])
    return (text, level, stamp, prefix, bar)


class LogModel(QAbstractListModel):
    TextRole = Qt.ItemDataRole.UserRole + 1
    LevelRole = Qt.ItemDataRole.UserRole + 2
    TimeRole = Qt.ItemDataRole.UserRole + 3
    PrefixRole = Qt.ItemDataRole.UserRole + 4
    BarRole = Qt.ItemDataRole.UserRole + 5

    countChanged = Signal()
    _append = Signal(str, int, str, bool)
    _live = Signal(str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []           # _row(): text, level, stamp, prefix, bar
        self._warnings = 0
        self._live_row = -1   # the row a '\r' redraw overwrites, or -1
        self._append.connect(self._on_append, Qt.ConnectionType.QueuedConnection)
        self._live.connect(self._on_live, Qt.ConnectionType.QueuedConnection)

    def roleNames(self):
        return {self.TextRole: QByteArray(b"line"), self.LevelRole: QByteArray(b"level"),
                self.TimeRole: QByteArray(b"time"), self.PrefixRole: QByteArray(b"prefix"),
                self.BarRole: QByteArray(b"bar")}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        text, level, stamp, prefix, bar = self._rows[index.row()]
        if role in (self.TextRole, Qt.ItemDataRole.DisplayRole):
            return text
        if role == self.LevelRole:
            return level
        if role == self.TimeRole:
            return stamp
        if role == self.PrefixRole:
            return prefix
        if role == self.BarRole:
            return bar
        return None

    def post(self, text: str, level: int, record: bool = False) -> None:
        """Thread-safe: may be called from any thread. ``record``: a log record
        (v1's time - LEVEL - prefix), else a printed line."""
        self._append.emit(text, level, time.strftime("%H:%M:%S"), bool(record))

    def post_live(self, text: str, final: bool = False) -> None:
        """Thread-safe. Show ``text`` in the live row; ``final`` commits it so
        the next bar gets a row of its own. Empty text with ``final`` just
        commits whatever the row last showed."""
        self._live.emit(text, final, time.strftime("%H:%M:%S"))

    @Slot(str, bool, str)
    def _on_live(self, text, final, stamp):
        if text:
            last = len(self._rows) - 1
            if 0 <= self._live_row == last:
                self._rows[last] = _row(text, logging.INFO, stamp, False)
                idx = self.index(last)
                self.dataChanged.emit(idx, idx)
            else:
                # Log lines arrived under the bar: move it to the bottom,
                # where a following console is looking.
                if self._live_row >= 0:
                    self.beginRemoveRows(QModelIndex(), self._live_row, self._live_row)
                    del self._rows[self._live_row]
                    self.endRemoveRows()
                self._on_append(text, logging.INFO, stamp, False)
                self._live_row = len(self._rows) - 1
        if final:
            self._live_row = -1

    @Slot(str, int, str, bool)
    def _on_append(self, text, level, stamp, record=False):
        if len(self._rows) >= MAX_ROWS:
            self.beginRemoveRows(QModelIndex(), 0, _TRIM - 1)
            del self._rows[:_TRIM]
            self.endRemoveRows()
            if self._live_row >= 0:
                self._live_row = max(-1, self._live_row - _TRIM)
        n = len(self._rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self._rows.append(_row(text, level, stamp, record))
        self.endInsertRows()
        if level >= logging.WARNING:
            self._warnings += 1
        self.countChanged.emit()

    @Slot()
    def clear(self):
        self.beginResetModel()
        self._rows.clear()
        self._warnings = 0
        self._live_row = -1
        self.endResetModel()
        self.countChanged.emit()

    @Slot(int, int, int, int, result=str)
    def textRange(self, r0: int, p0: int, r1: int, p1: int) -> str:
        """The console's selection (user, 2026-10-01): rows r0..r1 as shown
        (prefix + line), the first from character p0, the last up to p1. A
        bar row comes whole -- its drawing has no characters to pick."""
        n = len(self._rows)
        if n == 0:
            return ""
        r0, r1 = max(0, min(r0, n - 1)), max(0, min(r1, n - 1))
        if (r1, p1) < (r0, p0):
            r0, p0, r1, p1 = r1, p1, r0, p0
        out = []
        for r in range(r0, r1 + 1):
            text, _lvl, _stamp, prefix, bar = self._rows[r]
            shown = prefix + text
            if not bar:
                a = max(0, p0) if r == r0 else 0
                b = min(len(shown), p1) if r == r1 else len(shown)
                shown = shown[a:b]
            out.append(shown)
        return "\n".join(out)

    @Slot(result=str)
    def allText(self) -> str:
        return "\n".join(prefix + text for text, _lvl, _stamp, prefix, _bar in self._rows)

    count = Property(int, lambda self: len(self._rows), notify=countChanged)
    warnings = Property(int, lambda self: self._warnings, notify=countChanged)

    def post_record(self, text: str, level: int, name: str) -> None:
        self.post(text, level, True)


#: The console window's tabs, in order: the four stages, Extra (Scene Cut and
#: the Depth Map Editor, as on the main window), then everything.
TABS = (("depthcrafter", "DepthCrafter"), ("splatting", "Splatting"), ("inpainting", "Inpainting"),
        ("merging", "Merging"), ("extra", "Extra"), ("all", "All"))

#: Logger-name prefixes that belong to one tab. A name matches a prefix
#: exactly or as a dotted child ("inpainting.v2.wan" under "inpainting").
#: Shared modules (core.common, core.splatting, video_io ...) are NOT here:
#: they run for whichever stage holds the job, so they follow the active job.
STAGE_LOGGER_PREFIXES = {
    "depthcrafter": ("depthcrafter", "depthcrafter_gui_seg", "studio.stages.depthcrafter"),
    "splatting": ("splatting", "splatting_gui", "studio.stages.splatting", "dependency_R"),
    "inpainting": ("inpainting", "inpainting_gui", "inpainting_gui_v2", "pipelines",
                   "studio.stages.inpainting", "studio.stages.inpainting_v2"),
    "merging": ("merging", "merging_gui", "studio.stages.merging"),
    "extra": ("scenecut", "depthedit", "SceneCutGUI", "DepthEditor", "CutValidator", "core.scenecut",
              "core.depthedit", "studio.stages.scenecut", "studio.stages.depthedit", "studio.stages.edgefill"),
}

#: A GpuJobs stage id -> its tab.
_JOB_TAB = {"depthcrafter": "depthcrafter", "splatting": "splatting", "inpainting": "inpainting",
            "merging": "merging", "scenecut": "extra", "depthedit": "extra", "edgefill": "extra"}

#: v1: a logger whose last name part is this is routine between-clips
#: bookkeeping; below WARNING it goes to All only, so the stage tab stays readable.
HOUSEKEEPING_LOGGER_SUFFIX = "housekeeping"

#: v1's per-stage console levels (console_log_level): Splatting and Inpainting
#: configured no logging standalone, so their tabs show only what they print
#: -- the "[n/total] clip" banner and the Hi-Res / Low-Res bars -- plus
#: warnings and errors; the rest of their log goes to All (user, 2026-09-30).
#: Their Debug option opens the tab up again (LogHub.set_tab_debug). Tabs not
#: listed show INFO, as v1's DepthCrafter, Merging and Extra did.
#: DepthCrafter joined them (user, 2026-10-04: only the window bars and the
#: total-time lines on its tab), keeping the INFO lines in QUIET_KEEP.
QUIET_TABS = ("splatting", "inpainting", "depthcrafter")

#: A quiet tab's INFO lines that still show on it, by how they start: for
#: DepthCrafter the clip's total time, and a Re-Merge's / Visual Generation's.
QUIET_KEEP = {
    "depthcrafter": ("[Source complete]", "--- Re-Merge for:", "--- Segment Visual Generation for:"),
}


def tab_for_logger(name: str):
    """The stage tab a logger belongs to, or None for shared code."""
    for tab, prefixes in STAGE_LOGGER_PREFIXES.items():
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix + "."):
                return tab
    return None


class LogHub(QObject):
    """One LogModel per console tab. Thread-safe to post to: the models queue
    their rows onto the UI thread, and the active stage is a plain attribute
    set on the UI thread (app.py, from each stage's busy flag)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._models = {tab: LogModel(self) for tab, _name in TABS}
        self._active = None      # the running job's tab, or None
        self._debug = {}         # quiet tab -> its Debug option is on

    def tab(self, tab_id: str) -> LogModel:
        return self._models[tab_id]

    @Slot(str, result=QObject)
    def model(self, tab_id: str):
        return self._models.get(tab_id)

    tabs = Property("QVariantList", lambda self: [{"id": t, "name": n} for t, n in TABS], constant=True)

    def set_active(self, stage_id) -> None:
        """The stage whose job runs now (a GpuJobs stage id), or None."""
        # an engine of a stage's own id ("inpainting_m2s") logs to that stage's tab
        self._active = (_JOB_TAB.get(stage_id) or _JOB_TAB.get(str(stage_id).split("_", 1)[0])) if stage_id else None

    def set_tab_debug(self, tab_id: str, on: bool) -> None:
        """A quiet tab's Debug option: on, it shows its whole log (v1)."""
        self._debug[tab_id] = bool(on)

    def post_record(self, text: str, level: int, name: str) -> None:
        self._models["all"].post(text, level, True)
        if level < logging.WARNING and name.rsplit(".", 1)[-1] == HOUSEKEEPING_LOGGER_SUFFIX:
            return
        tab = tab_for_logger(name) or self._active
        if (tab and level < logging.WARNING and tab in QUIET_TABS and not self._debug.get(tab)
                and not text.startswith(QUIET_KEEP.get(tab, ()))):
            return                          # All only (v1's console_log_level)
        if tab:
            self._models[tab].post(text, level, True)

    # StreamTee's side: printed lines and progress bars, for the running job
    def post(self, text: str, level: int) -> None:
        self._models["all"].post(text, level)
        tab = self._active
        if tab:
            self._models[tab].post(text, level)

    def post_live(self, text: str, final: bool = False) -> None:
        self._models["all"].post_live(text, final)
        tab = self._active
        if tab:
            self._models[tab].post_live(text, final)


class ModelHandler(logging.Handler):
    """Hands each record to a LogModel or a LogHub (``post_record``)."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.model.post_record(self.format(record), record.levelno, record.name)
        except Exception:
            pass


_BREAKS = re.compile(r"(\r\n|\r|\n)")


#: Called with each redraw of a captured progress bar's text (job_stats'
#: s/it). Keep them cheap: they run on the writing thread.
BAR_LISTENERS: list = []


class StreamTee:
    """Stands in for sys.stdout / sys.stderr: everything still reaches the real
    stream (the launcher's log file), and a copy reaches the console.

    A '\\r' puts the stream in bar mode: the text after it is shown in the
    model's live row, redrawn on every write, until a newline commits it.
    Plain text waits for its newline and becomes an ordinary row.
    """

    def __init__(self, model, stream):     # a LogModel or a LogHub
        self._model = model
        self._stream = stream   # None under a bare pythonw
        self._buf = ""
        self._bar = False
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
        if not s:
            return 0
        bars = []                  # the bar states this write showed, for BAR_LISTENERS
        with self._lock:
            for piece in _BREAKS.split(s):
                if piece == "\r":
                    self._buf = ""
                    self._bar = True
                elif piece in ("\n", "\r\n"):
                    if self._bar and self._buf.strip():
                        bars.append(self._buf.rstrip())      # a bar's last state ("5/5"), as it commits
                    self._commit()
                elif piece:
                    self._buf += piece
            bar = self._buf.rstrip() if self._bar and self._buf.strip() else ""
            if bar:
                self._model.post_live(bar)
                bars.append(bar)
        if bars and BAR_LISTENERS:
            for text in bars[-2:]:     # the committed one and the live one, at most
                for listener in BAR_LISTENERS:
                    try:
                        listener(text)
                    except Exception:
                        pass
        return len(s)

    def _commit(self) -> None:
        text = self._buf.rstrip()
        if self._bar:
            self._model.post_live(text, final=True)
        elif text.strip():
            self._model.post(text, logging.INFO)
        self._buf = ""
        self._bar = False

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):   # encoding, fileno, buffer ... from the real stream
        return getattr(self._stream, name)


def install_stream_tee(model) -> None:
    """Route stdout and stderr through :class:`StreamTee`. Idempotent. Call
    after the logging StreamHandler is made, so it keeps the real stderr and
    log records are not shown twice."""
    if not isinstance(sys.stdout, StreamTee):
        sys.stdout = StreamTee(model, sys.stdout)
    if not isinstance(sys.stderr, StreamTee):
        sys.stderr = StreamTee(model, sys.stderr)
