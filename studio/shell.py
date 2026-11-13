"""shell.py -- app-wide state for Studio v2: palettes, fonts, project folder.

Seeded once from v1's ``config_studio.json`` (palette name, project folder and
each stage's chapter), then kept in ``config/studio.json``.

Typography matches v1's ``core/ui/app_fonts.py`` exactly, by role:

    heading  Bahnschrift SemiBold, caps, 15      (ships with Windows)
    body     Oxanium, 13 (small 11)              (bundled, assets/fonts)
    button   Cal Sans, caps with a double word gap, 13   (bundled)
    tab      Bahnschrift, caps, 13; SemiBold for the current tab

The bundled files are added with QFontDatabase.addApplicationFont, which is
private to this process just like v1's AddFontResourceExW(FR_PRIVATE).
"""

from __future__ import annotations

import importlib.util
import logging
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QByteArray, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtCore import Qt

from . import settings_store
from .legacy import REPO

ICONS_DIR = REPO / "assets" / "icons"
DEFAULT_APP_ICON = "navy_blue_matte"      # user, 2026-10-08

logger = logging.getLogger(__name__)

_FALLBACK_ACCENT = "#6ea8fe"


# ---------------------------------------------------------------------------
# palettes
# ---------------------------------------------------------------------------


def _load_themes() -> Dict[str, dict]:
    path = REPO / "core" / "ui" / "theme_manager.py"
    try:
        spec = importlib.util.spec_from_file_location("_v2_theme_manager", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.THEMES)
    except Exception:
        logger.warning("Could not read Studio palettes; using Graphite only", exc_info=True)
        return {"Graphite": {"is_dark": True, "bg": "#2b2b2b", "fg": "white", "entry_bg": "#3c3c3c",
                             "panel_bg": "#3a4047", "panel_trough": "#262b31", "panel_bar": "#ffffff"}}


#: Themes renamed since (old -> new): a saved choice follows its theme.
_RENAMED_THEMES = {"Dark Red": "The Nile"}

#: Studio's own extra themes, beside the built-in list (core/ui/theme_manager.py
#: THEMES, left alone). ADD A THEME HERE: "Name": ("the theme it is listed
#: after", {colours}). The colours:
#:   is_dark       True for the Dark Themes group, False for Light Themes
#:   bg            the window's ground          fg         the text
#:   entry_bg      fields, popups, buttons      panel_bg   the sections
#:   panel_trough  empty slider / bar tracks    panel_bar  the accent (fills,
#:                 the selected button); "white" for a white accent
#:   tab_accent    the selected stage tab's label
#:   section_border  the sections' outline
#: The Nile (user, 2026-09-27, named 2026-10-02; was "Dark Red"): a deep red
#: ground with a white accent, as Graphite's. A new theme gets the default
#: film grain until one is tuned for it.
_EXTRA_THEMES = {
    "The Nile": ("True Black", {
        "is_dark": True,
        "bg": "#240a0b", "fg": "#f3eaea", "entry_bg": "#3a1416",
        "panel_bg": "#2e0e10", "panel_trough": "#160506", "panel_bar": "white",
        "tab_accent": "#ffffff",
        "section_border": "#5c2a2c",
    }),
}


#: Studio's own changes to built-in themes (core/ui/theme_manager.py THEMES is
#: left alone). Graphite (user, 2026-09-27): the old blue-grey sections
#: (#3a4047) read blue against the neutral window -- a neutral grey of the
#: same depth instead.
_THEME_OVERRIDES = {
    "Graphite": {"panel_bg": "#3d3e40", "panel_trough": "#28292b"},
    # Dark grey accents (user, 2026-09-27): Light's blue and True Black's dark
    # red go. On True Black the fills (bars, the selected button) take the dark
    # grey, and the selected stage tab's label a lighter grey, as it had a
    # lighter red -- dark grey text on black would not read.
    "Light": {"panel_bar": "#3c3c3c", "tab_accent": "#3c3c3c"},
    # True Black truly black (user, 2026-09-27): the window and the sections
    # #000; the sections' grey outline (section_border) sets them apart, and
    # the bars' empty track lifts a little so it still shows.
    # Its clip lists' bars (user, 2026-10-24): filled or not they read the same grey -- the grey is
    # their empty track there, the fill white
    "True Black": {"panel_bar": "#4a4a4a", "tab_accent": "#a6a6a6",
                   "bg": "#000000", "panel_bg": "#000000", "panel_trough": "#1a1a1a",
                   "list_bar": "#ffffff", "list_trough": "#4a4a4a"},
}


def _with_extra_themes(themes: Dict[str, dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for name, p in themes.items():
        out[name] = {**p, **_THEME_OVERRIDES.get(name, {})}
        for extra, (after, q) in _EXTRA_THEMES.items():
            if after == name and extra not in themes:
                out[extra] = q
    for extra, (_after, q) in _EXTRA_THEMES.items():
        out.setdefault(extra, q)
    return out


def _luminance(hex_color: str) -> float:
    c = QColor(hex_color)

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def palette_for_qml(name: str, p: dict) -> dict:
    bar = p.get("panel_bar", _FALLBACK_ACCENT)
    # Graphite's accent is v1's plain white (user, 2026-09-26: v2 had swapped
    # it for a blue); accentFg below turns text on it dark.
    accent = "#ffffff" if bar.lower() == "white" else bar
    return {
        "name": name,
        "isDark": bool(p.get("is_dark", True)),
        "bg": p.get("bg", "#2b2b2b"),
        "fg": p.get("fg", "white"),
        "surface": p.get("panel_bg", p.get("entry_bg", "#3a4047")),
        "field": p.get("entry_bg", "#3c3c3c"),
        "trough": p.get("panel_trough", "#262b31"),
        "accent": accent,
        "accentInk": p.get("tab_accent", accent),
        # the clip lists' row bars (Upcoming Jobs): the accent over the trough, unless the theme says
        "listFill": p.get("list_bar", accent),
        "listTrack": p.get("list_trough", p.get("panel_trough", "#262b31")),
        "accentFg": "#101010" if _luminance(accent) > 0.36 else "#ffffff",
        # v1's section outline colour (ctk_theme: ctk_outline, else the
        # palette's section_border, else its bar) -- View > Show Section Outlines.
        "outline": p.get("ctk_outline") or p.get("section_border") or bar,
    }


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------


#: Vertical metrics for fonts whose line box is built for another script, as
#: (ascent, descent) in font units. Noto Sans Nandinagari reserves 900 above
#: and 560 below the baseline for Nandinagari's marks; Latin text sat high in
#: every box. Rebalanced on its Latin (caps 714, accents 944, descenders 240):
#: the box is centred on the capitals, line height 1.23 em (Oxanium's 1.25).
#: Poppins keeps its centring but not its 1.5 em line (1050 / 350 + a 100
#: gap): 975 / 280 clears its accents (927), brackets (934) and descenders
#: (270) at 1.26 em, so rows keep the height they had.
_LATIN_METRICS = {"NotoSansNandinagari-Regular": (974, 260),
                  "Poppins-Regular": (975, 280), "Poppins-SemiBold": (975, 280)}


def _with_latin_metrics(path: Path, ascent: int, descent: int) -> Optional[bytes]:
    """The font at `path` with its hhea / typo / win metrics set to
    (ascent, descent), as bytes -- the file itself is left as downloaded.
    None if fontTools is missing (then the font loads unchanged)."""
    try:
        import io
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    try:
        font = TTFont(str(path))
        hhea, os2 = font["hhea"], font["OS/2"]
        hhea.ascent, hhea.descent, hhea.lineGap = ascent, -descent, 0
        os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = ascent, -descent, 0
        os2.usWinAscent, os2.usWinDescent = ascent, descent
        buf = io.BytesIO()
        font.save(buf)
        return buf.getvalue()
    except Exception:
        logger.warning("Could not rebalance %s's metrics; loading it as it is", path.name, exc_info=True)
        return None


def load_fonts() -> Dict[str, str]:
    font_dir = REPO / "assets" / "fonts"
    loaded: Dict[str, str] = {}
    for stem in ("Poppins-Regular", "Poppins-SemiBold", "NotoSansNandinagari-Regular", "Oxanium-Regular",
                 "Oxanium-Bold", "CalSans-Regular"):
        path = font_dir / f"{stem}.ttf"
        data = _with_latin_metrics(path, *_LATIN_METRICS[stem]) if stem in _LATIN_METRICS else None
        fid = (QFontDatabase.addApplicationFontFromData(QByteArray(data)) if data is not None
               else QFontDatabase.addApplicationFont(str(path)))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            loaded[stem] = fams[0]
        else:
            logger.warning("Bundled font did not load: %s", stem)
    installed = set(QFontDatabase.families())
    fallback = "Segoe UI"
    bahn = "Bahnschrift" if "Bahnschrift" in installed else fallback
    return {
        "heading": bahn,
        # Body text: Poppins (user, 2026-09-26, after v1's Oxanium read too
        # square and Noto Sans Nandinagari too plain), Regular + SemiBold.
        # Noto, then Oxanium, are the fallbacks should the bundled files fail.
        "body": loaded.get("Poppins-Regular", loaded.get("NotoSansNandinagari-Regular",
                                                         loaded.get("Oxanium-Regular", fallback))),
        "button": loaded.get("CalSans-Regular", fallback),
        "tab": bahn,
        "icons": next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in installed), "Segoe MDL2 Assets"),
        "mono": next((f for f in ("Cascadia Mono", "Consolas") if f in installed), "Consolas"),
    }


# ---------------------------------------------------------------------------


#: View > UI Scale: "auto" (v1's per-screen factor, see Main.qml's zoom) or
#: one of these, fixed. Main.qml scales the whole UI by it -- text, controls
#: and the window's fit together.
UI_SCALES = ("1.0", "0.95", "0.9", "0.85", "0.8", "0.75")
#: View > Glass Opacity: how much of its tint the window keeps over the glass.
#: "auto" = the palette's default (Theme.qml: 60% dark, 50% light).
GLASS_OPACITIES = ("0.9", "0.8", "0.7", "0.6", "0.5", "0.4", "0.3")


#: The wigglegram's eye switches per second (user, 2026-10-09): the slider's
#: range and step; 7.5 is about the 130 ms per eye it always had.
WIGGLE_SPEED = (2.0, 20.0, 0.5, 7.5)
#: How many folder shortcuts the folder picker has, under the drives.
PICKER_SHORTCUTS = 4


def _wiggle_speed(value: Any) -> float:
    lo, hi, step, default = WIGGLE_SPEED
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, round(v / step) * step))


def _opacity_mode(value: Any) -> str:
    text = str(value or "auto")
    if text == "auto":
        return text
    try:
        v = float(text)
    except ValueError:
        return "auto"
    return min(GLASS_OPACITIES, key=lambda s: abs(float(s) - v))


#: What a mouse side button can do (user, 2026-10-30): Splatting's one-click actions, as their buttons
#: (SplattingPage.mouseAction runs them) -- no dials, nothing that starts or stops a job or clears them all
MOUSE_ACTIONS = ("None", "Flip Preview Card", "Pick Convergence", "Play / Pause", "Fast Forward",
                 "Previous Frame", "Next Frame", "Back 10 Frames", "Forward 10 Frames", "Previous Clip", "Next Clip",
                 "First Frame", "Last Frame", "Add Static Keyframe", "Add Dynamic Keyframe", "Clear Current Keyframe",
                 "Create / Update Sidecar", "Estimate Depth Pop", "Preview Auto-Converge", "Edit Depth Map",
                 "Detect Text", "Hole Shift", "Full Preview")


def _mouse_action(value: Any) -> str:
    return value if value in MOUSE_ACTIONS else "None"


def _scale_mode(value: Any) -> str:
    text = str(value or "auto")
    if text == "auto":
        return text
    try:
        v = float(text)
    except ValueError:
        return "auto"
    return min(UI_SCALES, key=lambda s: abs(float(s) - v))


def personal_m2svid_page() -> str:
    """The M2SVid sub tab's page (personal/m2svid, the user's own -- not released) as a URL, or ""
    where that folder isn't: the release runs without it."""
    try:
        if importlib.util.find_spec("personal.m2svid.controller") is None:
            return ""
    except (ImportError, ValueError):
        return ""
    page = REPO / "personal" / "m2svid" / "M2SvidPage.qml"
    return QUrl.fromLocalFile(str(page)).toString() if page.is_file() else ""


def personal_inpainting_tabs() -> List[str]:
    """Inpainting's sub tab names on the user's own copy (personal/ui_names.py, not released), or []:
    the release's own names."""
    try:
        if importlib.util.find_spec("personal.ui_names") is None:
            return []
        from personal.ui_names import INPAINTING_TABS
        return [str(t) for t in INPAINTING_TABS]
    except Exception:
        return []


def _seed_from_v1_studio(v1: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    if v1.get("theme_name"):
        out["palette"] = v1["theme_name"]
    if v1.get("project_folder"):
        out["project_folder"] = v1["project_folder"]
    if isinstance(v1.get("stage_chapters"), dict):
        out["stage_chapters"] = v1["stage_chapters"]
    if v1.get("frosted_glass"):
        out["glass"] = "acrylic"
    return out


class StudioShell(QObject):
    changed = Signal()
    projectChanged = Signal()
    #: (project path, poster url): a video's cover art, read off the UI thread
    #: after listProjects returned (bug sweep 2026-10-09, A2)
    posterFound = Signal(str, str)
    # just before the project switches, with the old one: the stages hand in
    # their folders (project_memory.py)
    projectLeaving = Signal(str)
    #: after a project switch has settled: one line on where the stages landed, for a toast
    #: ("LaCasa: 4 stages on Whole project, Merging kept its own folders"; project_memory)
    projectLanded = Signal(str)
    #: the open project's stage folders changed (Project Folders panel, folders made): the
    #: stages look again -- not a switch, nothing remembered or left
    projectRefreshed = Signal()
    #: projectEmpty / projectUnits / projectFolders may read differently
    projectFoldersChanged = Signal()
    #: (project path): a project's stage folders were changed or made -- the open one's or
    #: another's (the project list's hover panel, 2026-11-12)
    projectFoldersEdited = Signal(str)
    #: a project was added to or removed from the project list (addProject / removeProject)
    projectListChanged = Signal()
    #: pickChapterForAll: every chaptered stage (not Scene Cut) on this chapter key
    chapterForAll = Signal(int)
    commonChapterChanged = Signal()
    focusWindowChanged = Signal()
    #: (id, path, size text): something too big for the Recycle Bin -- delete it for good?
    #: Main asks and answers with answerPermanentDelete (user, 2026-10-14)
    askPermanentDelete = Signal(int, str, str)
    _sigAskPermanent = Signal(int, str, str)
    #: (title, what to do): something this PC lacks for Studio -- no NVIDIA driver, one too old, no FFmpeg
    #: (studio.env_check; release check 2026-10-17). Main shows each as a warning, kept in the bell.
    envWarning = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._asks: Dict[int, list] = {}      # id -> [threading.Event, answer]
        self._ask_next = 0
        self._sigAskPermanent.connect(self.askPermanentDelete, Qt.ConnectionType.QueuedConnection)
        themes = _with_extra_themes(_load_themes())
        self._palettes = [palette_for_qml(n, p) for n, p in themes.items()]
        self._fonts = load_fonts()
        self._data = settings_store.load("studio", REPO / "config_studio.json", _seed_from_v1_studio)
        names = [p["name"] for p in self._palettes]
        wanted = self._data.get("palette", "Graphite")
        wanted = _RENAMED_THEMES.get(wanted, wanted)
        self._palette_index = names.index(wanted) if wanted in names else 0
        self._landings: List[tuple] = []          # (stage, kind, detail) during a switch
        self._landing_chapters: Optional[dict] = None   # stage_chapters as the switch found them
        self._landing_pending = False
        self._known_cache = (0.0, None)
        self._sync_overrides()
        app = QGuiApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(lambda *_a: self.focusWindowChanged.emit())

    # The window with keyboard focus. QML's Window.active is no use for "is
    # this window the one being typed at": QWindow.isActive is true for every
    # window in the focus window's transient chain, so the main window and an
    # editor window opened from it are "active" together, and an arrow-key
    # Shortcut enabled in both matches twice and fires neither.
    focusWindow = Property(QObject, lambda self: QGuiApplication.focusWindow(), notify=focusWindowChanged)

    # ---- deleting for good: asked, from any thread (core.common.recycle_bin.ASK_PERMANENT) ----

    def ask_permanent(self, path: str, size: int) -> bool:
        """Something too big for the Recycle Bin: True when the user says to delete it for good.
        A job thread waits for the answer; the UI thread (the folder picker, SAM 3's uninstall)
        waits in a local event loop while the question is up. Quitting answers no."""
        from PySide6.QtCore import QEventLoop
        self._ask_next += 1
        rid = self._ask_next
        done = threading.Event()
        self._asks[rid] = [done, False]
        size_text = (f"{size / 2 ** 30:.1f} GB" if size >= 2 ** 30 else f"{size / 2 ** 20:.0f} MB"
                     if size >= 2 ** 20 else f"{max(1, size // 1024)} KB")
        app = QGuiApplication.instance()
        if app is not None and threading.current_thread() is threading.main_thread():
            loop = QEventLoop()
            self._asks[rid].append(loop)
            self.askPermanentDelete.emit(rid, str(path), size_text)
            if not done.is_set():
                loop.exec()
        else:
            self._sigAskPermanent.emit(rid, str(path), size_text)
            while not done.wait(0.25):
                if app is None or getattr(self, "_closing", False):
                    break
        entry = self._asks.pop(rid, [None, False])
        return bool(entry[1])

    @Slot(int, bool)
    def answerPermanentDelete(self, rid: int, yes: bool) -> None:
        entry = self._asks.get(int(rid))
        if entry is None:
            return
        entry[1] = bool(yes)
        entry[0].set()
        if len(entry) > 2:
            entry[2].quit()

    def answer_all_no(self) -> None:
        """Quit: every question still open is answered no (the files are kept)."""
        self._closing = True
        for rid in list(self._asks):
            self.answerPermanentDelete(rid, False)

    @Slot()  # Save All Settings calls it from QML (it wasn't a slot: a TypeError)
    def save(self) -> None:
        self._data["palette"] = self._palettes[self._palette_index]["name"]
        settings_store.save("studio", self._data)

    # -- palettes / fonts ----------------------------------------------------

    palettes = Property("QVariantList", lambda self: self._palettes, constant=True)
    fonts = Property("QVariantMap", lambda self: self._fonts, constant=True)

    def _get_palette_index(self) -> int:
        return self._palette_index

    def _set_palette_index(self, i: int) -> None:
        i = max(0, min(int(i), len(self._palettes) - 1))
        if i != self._palette_index:
            self._palette_index = i
            self.save()
            self.changed.emit()

    paletteIndex = Property(int, _get_palette_index, _set_palette_index, notify=changed)

    def _pref(self, key, default):
        return self._data.get(key, default)

    def _set_pref(self, key, value, save: bool = True):
        if self._data.get(key) != value:
            self._data[key] = value
            if save:
                self.save()
            self.changed.emit()

    # Read at start only: the window's flags are fixed when it is created, so
    # the menu's switch takes effect on the next launch.
    customTitleBar = Property(bool, lambda self: bool(self._pref("custom_titlebar", True)),
                              lambda self, v: self._set_pref("custom_titlebar", bool(v)), notify=changed)

    @Property(bool, constant=True)
    def customTitleBarAtStart(self) -> bool:
        # Always the custom title bar (user, 2026-09-27): the switch is gone from
        # Settings -- it was found to have no effect on job speed -- so a saved
        # "off" from before must not leave the window stuck without it.
        return True

    uiScale = Property(str, lambda self: _scale_mode(self._pref("ui_scale", "auto")),
                       lambda self, v: self._set_pref("ui_scale", _scale_mode(v)), notify=changed)
    uiScales = Property("QVariantList", lambda self: list(UI_SCALES), constant=True)

    glassOpacity = Property(str, lambda self: _opacity_mode(self._pref("glass_opacity", "auto")),
                            lambda self, v: self._set_pref("glass_opacity", _opacity_mode(v)), notify=changed)
    glassOpacities = Property("QVariantList", lambda self: list(GLASS_OPACITIES), constant=True)
    # The mouse's side buttons (user, 2026-10-30): each runs the Splatting action picked for it in
    # Settings > Studio ("None": nothing). SplattingPage.mouseAction() knows the names.
    mouseActions = Property("QVariantList", lambda self: list(MOUSE_ACTIONS), constant=True)
    mouseBackAction = Property(str, lambda self: _mouse_action(self._pref("mouse_back_action", "None")),
                               lambda self, v: self._set_pref("mouse_back_action", _mouse_action(v)), notify=changed)
    mouseForwardAction = Property(str, lambda self: _mouse_action(self._pref("mouse_forward_action", "None")),
                                  lambda self, v: self._set_pref("mouse_forward_action", _mouse_action(v)),
                                  notify=changed)
    # Merging's preview flip card: the Preview Source on each side.
    # Splatting's flip card (user, 2026-09-27): the preview source on each side
    splatFlipFront = Property(str, lambda self: str(self._pref("splat_flip_front", "Dubois Anaglyph")),
                              lambda self, v: self._set_pref("splat_flip_front", str(v)), notify=changed)
    splatFlipBack = Property(str, lambda self: str(self._pref("splat_flip_back", "Splat Result")),
                             lambda self, v: self._set_pref("splat_flip_back", str(v)), notify=changed)
    mergeFlipFront = Property(str, lambda self: str(self._pref("merge_flip_front", "Dubois Anaglyph")),
                              lambda self, v: self._set_pref("merge_flip_front", str(v)), notify=changed)
    mergeFlipBack = Property(str, lambda self: str(self._pref("merge_flip_back", "Blended Image")),
                             lambda self, v: self._set_pref("merge_flip_back", str(v)), notify=changed)
    # Glasses-Free 3D's Keep Eye Tracking On (user, 2026-10-17): the camera kept on between
    # clips, Full Preview and jobs while a preview shows it (sr_display.py)
    glassesKeepTracking = Property(bool, lambda self: bool(self._pref("glasses_keep_tracking", False)),
                                   lambda self, v: self._set_pref("glasses_keep_tracking", bool(v)), notify=changed)
    # The wigglegram's speed, in eye switches per second (user, 2026-10-09): one for
    # both previews and the Full Preview window
    wiggleSpeed = Property(float, lambda self: _wiggle_speed(self._pref("wiggle_speed", WIGGLE_SPEED[3])),
                           lambda self, v: self._set_pref("wiggle_speed", _wiggle_speed(v)), notify=changed)

    # The folder picker's four shortcuts, under the drives (user, 2026-10-10):
    # a folder each, "" for an empty one
    def _picker_shortcuts(self) -> list:
        saved = self._pref("picker_shortcuts", [])
        saved = [str(p or "") for p in saved] if isinstance(saved, list) else []
        return (saved + [""] * PICKER_SHORTCUTS)[:PICKER_SHORTCUTS]

    pickerShortcuts = Property("QVariantList", _picker_shortcuts, notify=changed)

    @Slot(int, str)
    def setPickerShortcut(self, index: int, path: str) -> None:
        if 0 <= index < PICKER_SHORTCUTS:
            shortcuts = self._picker_shortcuts()
            shortcuts[index] = str(path or "")
            self._set_pref("picker_shortcuts", shortcuts)

    # The folder picker's view (user, 2026-10-31: the Explorer design): Details, Tiles or Large icons,
    # and whether the preview pane shows -- as it was left, in every picker
    pickerView = Property(str, lambda self: self._pref("picker_view", "details")
                          if self._pref("picker_view", "details") in ("details", "tiles", "icons") else "details",
                          lambda self, v: self._set_pref("picker_view", v if v in ("details", "tiles", "icons") else "details"),
                          notify=changed)
    pickerPreview = Property(bool, lambda self: bool(self._pref("picker_preview", True)),
                             lambda self, v: self._set_pref("picker_preview", bool(v)), notify=changed)
    # Merging's Shadow fold (its five shadow dials), closed by default.
    # Settings > Studio > Hide Beginner Mode Switch (user, 2026-10-11: for experts who
    # never use it). The header shows the switch anyway while the mode is on.
    hideBeginnerSwitch = Property(bool, lambda self: bool(self._pref("hide_beginner_switch", False)),
                                  lambda self, v: self._set_pref("hide_beginner_switch", bool(v)), notify=changed)
    # Settings > Studio > Pick Folders with Windows Explorer (user, 2026-10-31: off by default; some
    # prefer the basic window): every folder picker opens Windows' own dialog instead of Studio's
    explorerPickers = Property(bool, lambda self: bool(self._pref("explorer_pickers", False)),
                               lambda self, v: self._set_pref("explorer_pickers", bool(v)), notify=changed)
    # The tips above each stage's progress bar (Settings > Appearance)
    showTips = Property(bool, lambda self: bool(self._pref("show_tips", True)),
                        lambda self, v: self._set_pref("show_tips", bool(v)), notify=changed)
    # Splatting: Border Width / Border Bias knobs shown (the "Border Settings" box)
    splatBorderSettings = Property(bool, lambda self: bool(self._pref("splat_border_settings", True)),
                                   lambda self, v: self._set_pref("splat_border_settings", bool(v)), notify=changed)
    mergeShadowOpen = Property(bool, lambda self: bool(self._pref("merge_shadow_open", False)),
                               lambda self, v: self._set_pref("merge_shadow_open", bool(v)), notify=changed)
    # Film grain over the window (user, 2026-09-28), per theme (grain.py); the
    # theme popout's switch turns it off everywhere.
    filmGrain = Property(bool, lambda self: bool(self._pref("film_grain", True)),
                         lambda self, v: self._set_pref("film_grain", bool(v)), notify=changed)

    # The Web particle (personal/web): the user's own, not released -- Splatting offers it
    # only where it is installed (core.splatting.rain.web_module)
    @Property(bool, constant=True)
    def personalWeb(self):
        from core.splatting.rain import web_available
        return web_available()

    # A third Inpainting engine of the user's own (personal/m2svid, not released): its page's URL
    # where it is installed, "" elsewhere -- InpaintingPage offers its sub tab only then
    @Property(str, constant=True)
    def personalM2svidPage(self):
        return personal_m2svid_page()

    # Inpainting's sub tab names on the user's own copy (personal/ui_names.py), [] elsewhere
    @Property("QVariantList", constant=True)
    def personalInpaintingTabs(self):
        return personal_inpainting_tabs()

    # -- the app icon (user, 2026-10-08): picked in the theme popout ----------
    # assets/icons/, built by studio/tools/build_app_icons.py; the window, the
    # taskbar, the title bar and the job notifications all follow it. The
    # launcher .exe keeps the icon it was built with (assets/StereoCrafter.ico).

    @Property("QVariantList", constant=True)
    def appIcons(self):
        """[{id, name, finish, url}] in picker order: url is the 128 px thumbnail."""
        try:
            listing = json.loads((ICONS_DIR / "icons.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [dict(i, url=QUrl.fromLocalFile(str(ICONS_DIR / (i["id"] + ".png"))).toString())
                for i in listing if (ICONS_DIR / (i["id"] + ".ico")).exists()]

    def _get_app_icon(self) -> str:
        ident = str(self._pref("app_icon", DEFAULT_APP_ICON))
        return ident if (ICONS_DIR / (ident + ".ico")).exists() else DEFAULT_APP_ICON

    def _set_app_icon(self, ident: str) -> None:
        if not (ICONS_DIR / (str(ident) + ".ico")).exists():
            return
        self._set_pref("app_icon", str(ident))
        self.apply_app_icon()
        from . import taskbar_win
        taskbar_win.icon_changed(self.app_icon_path())   # a pinned button shows the pin's icon

    appIcon = Property(str, _get_app_icon, _set_app_icon, notify=changed)
    appIconUrl = Property(str, lambda self: QUrl.fromLocalFile(str(self.app_icon_path())).toString(), notify=changed)

    def app_icon_path(self) -> Path:
        path = ICONS_DIR / (self._get_app_icon() + ".ico")
        return path if path.exists() else REPO / "assets" / "StereoCrafter.ico"

    def apply_app_icon(self, notifier=None) -> None:
        """The chosen icon on the window and the taskbar (and on the notifier's tray icon)."""
        app = QGuiApplication.instance()
        path = self.app_icon_path()
        if app is not None:
            app.setWindowIcon(QIcon(str(path)))
        if notifier is not None:
            self._notifier = notifier
        if getattr(self, "_notifier", None) is not None:
            self._notifier.set_icon(path)

    @Property("QVariantMap", constant=True)
    def grainTable(self):
        """Each theme's grain, as saved by the grain tuner; missing: grainDefault."""
        from . import grain
        if not hasattr(self, "_grain_table"):
            self._grain_table = grain.load()
        return self._grain_table

    @Property("QVariantMap", constant=True)
    def grainDefault(self):
        from . import grain
        return dict(grain.DEFAULTS)

    # View > Show Section Outlines (v1 show_section_outlines, on by default).
    sectionOutlines = Property(bool, lambda self: bool(self._pref("show_section_outlines", True)),
                               lambda self, v: self._set_pref("show_section_outlines", bool(v)), notify=changed)

    glass = Property(str, lambda self: self._pref("glass", "none"),
                     lambda self, v: self._set_pref("glass", str(v)), notify=changed)
    lastStage = Property(int, lambda self: int(self._pref("last_stage", 0)),
                         # kept, and written with the rest at quit (Studio audit
                         # 2026-09-28, PERF: studio.json was rewritten on every
                         # stage switch)
                         lambda self, v: self._set_pref("last_stage", int(v), save=False), notify=changed)
    # ...and the Extra tab's tool with it (user, 2026-10-11: closed in the Depth Map Editor,
    # Studio reopened on Scene Cut -- only the tab was kept). 0 Scene Cut, 1 Depth Map Editor.
    lastExtraTool = Property(int, lambda self: int(self._pref("last_extra_tool", 0)),
                             lambda self, v: self._set_pref("last_extra_tool", int(v), save=False), notify=changed)
    # Full Preview's screen (user, 2026-10-13): full screen opens where it was last (a 3D TV, say),
    # Studio's own screen the first time. A screen's name ("" before any).
    fullPreviewScreen = Property(str, lambda self: str(self._pref("full_preview_screen", "") or ""),
                                 lambda self, v: self._set_pref("full_preview_screen", str(v or "")), notify=changed)

    # Hide Folders on Manual paths (user, 2026-09-30): {stage: True} while a
    # stage's folder fields are put away. (With a chapter picked they fold by
    # themselves; that stays the page's own, per session.)
    foldersHidden = Property("QVariantMap", lambda self: dict(self._pref("folders_hidden", {}) or {}), notify=changed)

    @Slot(str, bool)
    def setFoldersHidden(self, stage: str, hidden: bool) -> None:
        cur = dict(self._pref("folders_hidden", {}) or {})
        if hidden:
            cur[str(stage)] = True
        else:
            cur.pop(str(stage), None)
        self._set_pref("folders_hidden", cur)

    # The console window (user, 2026-09-30): where it was and how big, kept
    # with the rest at quit; its text size, saved at once.
    consoleGeometry = Property("QVariantList", lambda self: list(self._pref("console_geometry", []) or []),
                               lambda self, v: self._set_pref("console_geometry", [int(n) for n in (v or [])][:4],
                                                              save=False), notify=changed)
    consoleFontSize = Property(int, lambda self: int(self._pref("console_font_size", 12)),
                               lambda self, v: self._set_pref("console_font_size", max(9, min(20, int(v)))),
                               notify=changed)

    #: A page asking for another stage (Main switches): (stage index, the
    #: Extra tab's tool, or -1). Splatting <-> the Depth Map Editor, 2026-09-28.
    openStageRequested = Signal(int, int)

    @Slot(int, int)
    def openStage(self, stage: int, tool: int = -1) -> None:
        self.openStageRequested.emit(int(stage), int(tool))

    @Slot(bool)
    def setDark(self, dark: bool) -> None:
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "setColorScheme"):
            hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

    # -- project folder and chapters (v1: shell + core.ui.chapters) ----------

    def _get_project(self) -> str:
        return self._data.get("project_folder", "")

    @staticmethod
    def _project_key(path: str) -> str:
        return str(path or "").replace("\\", "/").rstrip("/").lower()

    def _project_memory(self, path: str) -> dict:
        """This project's remembered stage chapters and folders (user,
        2026-10-02), created on first use."""
        mem = self._data.setdefault("project_memory", {})
        return mem.setdefault(self._project_key(path), {})

    def _set_project(self, path: str) -> None:
        path = str(path or "").replace("\\", "/")
        old = self._data.get("project_folder", "")
        if path != old:
            if old:
                # what each stage used in the project being left: its chapter,
                # and (projectLeaving -> rememberStagePaths) its folders
                self._project_memory(old)["stage_chapters"] = dict(self._data.get("stage_chapters") or {})
                self.projectLeaving.emit(old)
            self._data["project_folder"] = path
            # The chapters each stage last picked in THIS project, if any
            # (before 2026-10-02 they were simply forgotten on a switch)
            self._data["stage_chapters"] = dict(self._project_memory(path).get("stage_chapters") or {})
            self._chapter_table = {}
            self._chapter_units = None
            self._sync_overrides()
            self.save()
            # the stages land (project_memory): each says how, and the switch is summed
            # up once it has settled; a stage follows its neighbours' chapters as the
            # switch found them, not as the stages before it just landed
            self._landings = []
            self._landing_chapters = dict(self._data["stage_chapters"])
            try:
                self.projectChanged.emit()
            finally:
                self._landing_chapters = None
            self.projectFoldersChanged.emit()
            self.commonChapterChanged.emit()
            if not self._landing_pending:
                self._landing_pending = True
                QTimer.singleShot(0, self._landing_settled)

    @Slot(str, "QVariantMap")
    def rememberStagePaths(self, stage: str, paths: dict) -> None:
        """A stage's folders, kept for the current project (project_memory.py)."""
        project = self._get_project()
        if project:
            self._project_memory(project).setdefault("stage_paths", {})[stage] = dict(paths or {})

    @Slot(str, result="QVariantMap")
    def rememberedStagePaths(self, stage: str) -> dict:
        project = self._get_project()
        if not project:
            return {}
        return dict((self._project_memory(project).get("stage_paths") or {}).get(stage) or {})

    projectFolder = Property(str, _get_project, _set_project, notify=projectChanged)
    # The movie's name for the caption (user, 2026-10-01): the project
    # folder's own name -- it holds the stage folders. "" when none is set.
    projectName = Property(str, lambda self: os.path.basename(self._get_project().rstrip("/")),
                           notify=projectChanged)

    # A TV show (user, 2026-10-01) holds the stage folders once per episode
    # (core.ui.chapters.find_units), so a chapter is (episode, number). The
    # stages and the picker know a chapter by one int; for a show that int is
    # a key made here -- (episode index + 1) * EPISODE_KEY + number -- and
    # _chapter_table turns it back into the episode folder and the chapter.
    # A movie's keys are its chapter numbers, as before. Saved choices keep the
    # episode's folder and the chapter number, so they survive new episodes
    # changing the keys.
    EPISODE_KEY = 100000

    def _build_chapter_table(self) -> List[dict]:
        self._chapter_table = {}
        project = self._get_project()
        if not project or not os.path.isdir(project):
            return []
        from core.ui import chapters
        # a project with no stage folders yet is one flat unit: its stages make
        # the folders (the default names) when they run (2026-11-12)
        units = chapters.find_units(project) or [""]
        entries = []
        for i, unit in enumerate(units):
            base = os.path.join(project, unit).replace("\\", "/") if unit else project
            # a flat unit (no chapter folders, or clips straight in Source) has a
            # Whole entry too: its stage folders themselves
            for n, name in chapters.list_entries(base):
                key = n if unit == "" else (i + 1) * self.EPISODE_KEY + n
                single = unit == "" or len(units) == 1
                if n == chapters.WHOLE:
                    name = chapters.WHOLE_NAME
                    label = name if single else f"{chapters.unit_label(unit)} · Whole episode"
                else:
                    # a movie in a subfolder of its own (one unit) needs no prefix
                    label = name if single else f"{chapters.unit_label(unit)} · {name}"
                self._chapter_table[key] = (unit, n, name, label)
                entries.append({"num": key, "name": label})
        self._chapter_units = units
        return entries

    def _units(self) -> List[str]:
        if getattr(self, "_chapter_units", None) is None:
            self._build_chapter_table()
        return getattr(self, "_chapter_units", None) or [""]

    def _chapter_entry(self, key: int):
        if key not in getattr(self, "_chapter_table", {}):
            self._build_chapter_table()
        return self._chapter_table.get(key)

    # -- the projects folder (user, 2026-10-01) ------------------------------
    # The folder that holds the projects -- each subfolder a movie or a show --
    # set in Settings; the caption's project button lists them (ProjectPicker).

    projectsRoot = Property(str, lambda self: str(self._pref("projects_root", "")),
                            lambda self, v: self._set_pref("projects_root", str(v or "").replace("\\", "/")),
                            notify=changed)
    # Optional (user, 2026-10-02: shows kept apart from the movies): every
    # folder in it is a TV show. "" = shows are found in the Projects Folder.
    showsRoot = Property(str, lambda self: str(self._pref("shows_root", "")),
                         lambda self, v: self._set_pref("shows_root", str(v or "").replace("\\", "/")),
                         notify=changed)

    #: A Projects Folder may hold category folders instead of projects: these
    #: names (lower case) are looked into, and decide their projects' tab.
    MOVIE_FOLDERS = ("movies", "movie", "films", "film")
    SHOW_FOLDERS = ("tv shows", "tv show", "tv", "shows", "series", "tv series")

    POSTER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    POSTER_WORDS = ("poster", "cover", "folder", "front")

    @classmethod
    def _poster(cls, folder: str, video: bool = True) -> str:
        """A picture lying in the project folder itself (not its subfolders),
        as a file URL: one named poster / cover / folder / front first, else
        the first by name; with none (and `video`), a video's embedded cover
        art (posters.video_cover). "" when there is neither."""
        try:
            pics = sorted(n for n in os.listdir(folder)
                          if n.lower().endswith(cls.POSTER_EXTS) and os.path.isfile(os.path.join(folder, n)))
        except OSError:
            return ""
        if not pics:
            if not video:
                return ""
            # no picture: the cover art inside the project's video, if it has
            # real cover art (user, 2026-10-02; posters.py)
            try:
                from studio.posters import video_cover
                cover = video_cover(folder)
            except Exception:
                logger.debug("Video cover art not read", exc_info=True)
                cover = ""
            return QUrl.fromLocalFile(cover).toString() if cover else ""
        named = [n for n in pics if any(w in n.lower() for w in cls.POSTER_WORDS)]
        return QUrl.fromLocalFile(os.path.join(folder, (named or pics)[0])).toString()

    @staticmethod
    def _subfolders(folder: str) -> List[str]:
        try:
            return [n for n in os.listdir(folder)
                    if not n.startswith((".", "_")) and os.path.isdir(os.path.join(folder, n))]
        except OSError:
            return []

    @Slot(result="QVariantList")
    def listProjects(self) -> List[dict]:
        """Every project, as [{name, path, show, poster}] in natural order,
        found three ways (user, 2026-10-02):

        * the Projects Folder's own folders, movies and shows mixed -- a show
          has an "episode" folder somewhere inside (chapters.is_show);
        * category folders in it (Movies / Films, TV Shows / Shows / Series):
          their folders are projects, of that kind;
        * the optional TV Shows Folder: every folder in it is a show.

        poster: a picture in the project folder, as a URL, or "".
        Plus the folders added on their own (Add Project, 2026-11-12: added True; missing
        True when the folder isn't there now), less the ones removed from the list
        (hidden_projects)."""
        from core.ui import chapters
        found: Dict[str, dict] = {}
        hidden = set(self._pkey(p) for p in self._path_list("hidden_projects"))
        added = self._path_list("added_projects")
        added_keys = set(self._pkey(p) for p in added)

        def add(path: str, name: str, show, missing: bool = False) -> None:
            key = path.lower()
            if key in found or key in hidden:
                return
            if show is None:
                try:
                    show = chapters.is_show(path) if not missing else False
                except Exception:
                    show = False
            # the pictures here; a video's cover art (an ffprobe, maybe an ffmpeg, per
            # video on its first look) is read after, off the UI thread (bug sweep A2)
            found[key] = {"name": name, "path": path, "show": bool(show),
                          "poster": "" if missing else self._poster(path, video=False),
                          "added": key in added_keys, "missing": bool(missing)}

        for path, name, show in self._root_folders():
            add(path, name, show)
        for path in added:
            add(path, os.path.basename(path.rstrip("/")) or path, None, missing=not os.path.isdir(path))
        bare = [p["path"] for p in found.values() if not p["poster"] and not p["missing"]]
        if bare:
            threading.Thread(target=self._find_video_posters, args=(bare,), name="project-posters",
                             daemon=True).start()
        return sorted(found.values(), key=lambda p: chapters._natural(p["name"]))

    def _root_folders(self):
        """(path, name, show or None for "look") for every folder the Projects / TV Shows
        folders hold as a project -- hidden ones too."""
        root = str(self._pref("projects_root", "") or "")
        if root and os.path.isdir(root):
            for name in self._subfolders(root):
                low = name.strip().lower()
                if low in self.MOVIE_FOLDERS or low in self.SHOW_FOLDERS:
                    sub = os.path.join(root, name)
                    for inner in self._subfolders(sub):
                        yield os.path.join(sub, inner).replace("\\", "/"), inner, low in self.SHOW_FOLDERS
                else:
                    yield os.path.join(root, name).replace("\\", "/"), name, None
        shows = str(self._pref("shows_root", "") or "")
        if shows and os.path.isdir(shows):
            for name in self._subfolders(shows):
                yield os.path.join(shows, name).replace("\\", "/"), name, True

    # -- projects added on their own / removed from the list (2026-11-12) ----
    # studio.json "added_projects": folders added with the project list's Add Project tile
    # (anywhere, listed whether or not they lie in the Projects Folder); "hidden_projects":
    # folders in the Projects / TV Shows folders taken off the list. Both forward slashes.

    def _pkey(self, path: str) -> str:
        """A folder's key: case and slashes aside."""
        p = str(path or "").strip()
        return self._project_key(os.path.normpath(p)) if p else ""

    def _path_list(self, key: str) -> List[str]:
        return [str(p) for p in (self._data.get(key) or []) if isinstance(p, str) and p.strip()]

    @staticmethod
    def _clean_path(path: str) -> str:
        p = os.path.normpath(str(path or "").strip()).replace("\\", "/")
        return p if p.endswith(":/") else p.rstrip("/")

    @Slot(str, result="QVariantMap")
    def addProject(self, path: str) -> dict:
        """Add a folder to the project list, for good: {path (as listed), show, added (new
        to the list)}, or {} when it isn't a folder. One listed already (any case or
        slashes) is just returned; one removed from the list comes back."""
        if not str(path or "").strip() or not os.path.isdir(str(path)):
            return {}
        from core.ui import chapters
        clean = self._clean_path(path)
        key = self._pkey(clean)
        hidden = self._path_list("hidden_projects")
        kept = [p for p in hidden if self._pkey(p) != key]
        changed = len(kept) != len(hidden)
        self._data["hidden_projects"] = kept
        listed, show = None, None
        for p, _name, s in self._root_folders():
            if self._pkey(p) == key:
                listed, show = p, s
                break
        if listed is None:
            listed = next((p for p in self._path_list("added_projects") if self._pkey(p) == key), None)
        new = listed is None
        if new:
            listed = clean
            self._data["added_projects"] = self._path_list("added_projects") + [clean]
        if show is None:
            try:
                show = chapters.is_show(listed)
            except Exception:
                show = False
        if new or changed:
            self._known_cache = (0.0, None)
            self.save()
            self.projectListChanged.emit()
        return {"path": listed, "show": bool(show), "added": new}

    @Slot(str, result=bool)
    def removeProject(self, path: str) -> bool:
        """Take a project off the list -- nothing on disk is touched: an added one is
        forgotten, one in the Projects / TV Shows folders hidden. The open project stays open."""
        key = self._pkey(path)
        if not key:
            return False
        added = self._path_list("added_projects")
        kept = [p for p in added if self._pkey(p) != key]
        changed = len(kept) != len(added)
        self._data["added_projects"] = kept
        found = next((p for p, _n, _s in self._root_folders() if self._pkey(p) == key), None)
        hidden = self._path_list("hidden_projects")
        if found is not None and not any(self._pkey(p) == key for p in hidden):
            self._data["hidden_projects"] = hidden + [found]
            changed = True
        if changed:
            self._known_cache = (0.0, None)
            self.save()
            self.projectListChanged.emit()
        return changed

    def _find_video_posters(self, paths: List[str]) -> None:
        """listProjects' projects with no picture: their videos' cover art,
        each sent to the picker as it's found (posterFound)."""
        from studio.posters import video_cover
        for path in paths:
            try:
                cover = video_cover(path)
            except Exception:
                logger.debug("Video cover art not read", exc_info=True)
                cover = ""
            if cover:
                self.posterFound.emit(path, QUrl.fromLocalFile(cover).toString())

    @Slot(result="QVariantList")
    def listChapters(self) -> List[dict]:
        try:
            return self._build_chapter_table()
        except Exception:
            logger.debug("Could not list chapters", exc_info=True)
            return []

    @Slot(int, result=str)
    def chapterBase(self, num: int) -> str:
        """The folder holding chapter `num`'s stage folders: the project's for a
        movie, the episode's for a show ("" for a show with no chapter picked:
        which episode is unknown). Merge All works on it (user, 2026-10-11)."""
        project = self._get_project()
        if not project:
            return ""
        entry = self._chapter_entry(num) if num >= 0 else None
        if entry is not None:
            unit = entry[0]
            return (os.path.join(project, unit) if unit else project).replace("\\", "/")
        units = self._units()
        if len(units) == 1:                     # a movie (in a subfolder of its own, maybe)
            return (os.path.join(project, units[0]) if units[0] else project).replace("\\", "/")
        return ""

    @Slot(str, int, str, result="QVariantMap")
    def stagePaths(self, stage: str, num: int, name: str) -> dict:
        try:
            from core.ui import chapters
            from studio import chapter_paths
            project = self._get_project()
            entry = self._chapter_entry(num) if num >= 0 else None
            if entry is not None:
                unit, n, real_name, _label = entry
                base = os.path.join(project, unit).replace("\\", "/") if unit else project
                paths = chapters.stage_paths(stage, base, n, real_name)
            elif num < 0 and project and self._units() != [""]:
                return {}       # a show has no whole-movie Source folder: pick an episode's chapter
            else:
                paths = chapters.stage_paths(stage, project, num, name)
            # v1's convention, then each input moved to the finished level that
            # actually holds the clips (a re-run nests them one level deeper)
            return chapter_paths.resolve(stage, paths)
        except Exception:
            logger.debug("Could not resolve chapter paths", exc_info=True)
            return {}

    @Slot(str, result="QVariantMap")
    def stageChapter(self, stage: str) -> dict:
        return self._resolve_saved(dict((self._data.get("stage_chapters") or {}).get(stage) or {}))

    def landingChapter(self, stage: str) -> dict:
        """`stage`'s saved chapter in this project as the switch found it (while the
        stages land; their current one otherwise) -- what a stage follows."""
        table = self._landing_chapters if self._landing_chapters is not None else self._data.get("stage_chapters")
        return self._resolve_saved(dict((table or {}).get(stage) or {}))

    def _resolve_saved(self, saved: dict) -> dict:
        if "unit" in saved:             # a show's chapter: its key may have moved
            if not getattr(self, "_chapter_table", None):
                self._build_chapter_table()
            for key, (unit, n, _name, label) in self._chapter_table.items():
                if unit == saved["unit"] and n == saved.get("chapter"):
                    return {"num": key, "name": label}
            return {}
        return saved

    @Slot(str, "QVariantMap")
    def setStageChapter(self, stage: str, chapter: dict) -> None:
        chapters = dict(self._data.get("stage_chapters") or {})
        if chapter:
            key = int(chapter.get("num", 0))
            entry = self._chapter_entry(key) if key >= 0 else None
            if entry is not None and entry[0]:
                chapters[stage] = {"num": key, "name": entry[3], "unit": entry[0], "chapter": entry[1]}
            else:
                chapters[stage] = {"num": key, "name": str(chapter.get("name", ""))}
        else:
            chapters.pop(stage, None)
        self._data["stage_chapters"] = chapters
        self.save()
        self.commonChapterChanged.emit()

    # -- where the stages landed on a switch (project_memory, 2026-11-12) ----

    @Slot(str, str, str)
    def noteLanding(self, stage: str, kind: str, detail: str = "") -> None:
        """A stage's landing on the project just switched to: kind "chapter" (its own),
        "folders" (its own), "followed" (detail: "<Stage>: <chapter>"), "only" (the
        project's one entry), "needs" (no chapter yet), "kept" (left as it was)."""
        self._landings.append((str(stage), str(kind), str(detail or "")))

    def _landing_settled(self) -> None:
        self._landing_pending = False
        notes, self._landings = self._landings, []
        project = self._get_project()
        if not project or not notes:
            return
        from studio.project_memory import summary
        text = summary(os.path.basename(project.rstrip("/")), notes)
        if text:
            self.projectLanded.emit(text)

    # -- one chapter for every stage, and the title bar's chip ----------------

    @Slot(int)
    def pickChapterForAll(self, num: int) -> None:
        """Every chaptered stage (Scene Cut keeps its whole movie) quietly on `num`."""
        self.chapterForAll.emit(int(num))
        self.commonChapterChanged.emit()

    def registerChapterStage(self, stage: str) -> None:
        """A stage that follows the project (project_memory.attach)."""
        if not hasattr(self, "_chapter_stages"):
            self._chapter_stages = []
        if stage not in self._chapter_stages:
            self._chapter_stages.append(stage)

    def _common_chapter(self) -> str:
        project = self._get_project()
        if not project:
            return ""
        try:
            if not getattr(self, "_chapter_table", None):
                self._build_chapter_table()
        except Exception:
            return ""
        if not self._chapter_table:
            return ""
        stages = [s for s in getattr(self, "_chapter_stages", []) if s != "scenecut"]
        nums = [self.stageChapter(s).get("num") for s in stages]
        picked = [n for n in nums if isinstance(n, int) and n in self._chapter_table]
        if not picked:
            return ""
        if len(picked) == len(nums) and len(set(picked)) == 1:
            return self._chapter_table[picked[0]][3]
        return "Mixed"

    #: The chapter every stage is on ("Chapter 6", "Whole project"); "Mixed" when they
    #: differ; "" with no project, no chapters, or no stage on one.
    commonChapter = Property(str, _common_chapter, notify=commonChapterChanged)

    @Slot(str, result=int)
    def chapterAt(self, path: str) -> int:
        """The open project's chapter key `path` lies in (any folder within it, or a flat
        unit's stage folder), or -1."""
        project = self._get_project()
        if not project or not path:
            return -1
        try:
            from core.ui import chapters
            at = chapters.locate_chapter(str(path))
            if not at:
                return -1
            unit_abs, n, _name = at
            proj = os.path.normpath(project).replace("\\", "/").rstrip("/").lower()
            u = os.path.normpath(unit_abs).replace("\\", "/").rstrip("/").lower()
            if u == proj:
                rel = ""
            elif u.startswith(proj + "/"):
                rel = u[len(proj) + 1:]
            else:
                return -1
            if not getattr(self, "_chapter_table", None):
                self._build_chapter_table()
            for key, (unit, num, _nm, _label) in self._chapter_table.items():
                if num == n and unit.replace("\\", "/").lower() == rel:
                    return key
        except Exception:
            logger.debug("Could not place %s in the project", path, exc_info=True)
        return -1

    # -- which project a folder belongs to ------------------------------------

    def _known_projects(self) -> Dict[str, str]:
        """{project key: its name} for every project Studio knows: those it has
        remembered stages for, and the folders in the Projects / TV Shows folders."""
        import time
        now = time.monotonic()
        stamp, for_project, cached = self._known_cache if len(self._known_cache) == 3 else (0.0, None, None)
        if cached is not None and for_project == self._get_project() and now - stamp < 2.0:
            return cached
        found: Dict[str, str] = {}

        def add(folder: str, name: str) -> None:
            found.setdefault(self._project_key(os.path.join(folder, name)), name)

        root = str(self._pref("projects_root", "") or "")
        if root and os.path.isdir(root):
            for name in self._subfolders(root):
                low = name.strip().lower()
                if low in self.MOVIE_FOLDERS or low in self.SHOW_FOLDERS:
                    sub = os.path.join(root, name)
                    for inner in self._subfolders(sub):
                        add(sub, inner)
                else:
                    add(root, name)
        shows = str(self._pref("shows_root", "") or "")
        if shows and os.path.isdir(shows):
            for name in self._subfolders(shows):
                add(shows, name)
        # the ones added on their own (2026-11-12); a removed one stays known
        for path in self._path_list("added_projects"):
            found.setdefault(self._pkey(path), os.path.basename(path.rstrip("/")) or path)
        for key in (self._data.get("project_memory") or {}):
            if key and key not in found:
                found[key] = self._real_name(key)
        self._known_cache = (now, self._get_project(), found)
        return found

    @staticmethod
    def _real_name(key: str) -> str:
        """A remembered project's folder name as it is on disk (the key is lower case)."""
        parent, base = os.path.split(key.rstrip("/"))
        try:
            return next((n for n in os.listdir(parent) if n.lower() == base.lower()), base)
        except OSError:
            return base

    @Slot(str, result=str)
    def foreignProject(self, path: str) -> str:
        """The name of ANOTHER project `path` lies in; "" when no project is open, the
        path is empty, inside the open project, or outside every known project (a
        scratch drive's folder is a fine manual path)."""
        project = self._get_project()
        if not project or not str(path or "").strip():
            return ""
        cur = self._project_key(os.path.normpath(project))
        p = self._project_key(os.path.normpath(str(path)))
        if p == cur or p.startswith(cur + "/"):
            return ""
        best, name = "", ""
        for key, label in self._known_projects().items():
            if key == cur or cur.startswith(key + "/"):
                continue                    # the open project, or a folder holding it
            if (p == key or p.startswith(key + "/")) and len(key) > len(best):
                best, name = key, label
        return name

    @Slot(QObject, result="QVariantList")
    def projectFieldKeys(self, owner) -> List[str]:
        """The settings keys of `owner`'s project folder fields -- the ones its chapter
        picker fills (project_memory.attach) -- for the fields' out-of-project tag."""
        info = getattr(owner, "_landing_info", None)
        return [str(k) for k in info[1]] if info else []

    # -- the Project Folders panel (2026-11-12) -------------------------------

    def _sync_overrides(self) -> None:
        """The folder choices kept here (a unit folder not writable) handed to
        core.ui.chapters, which reads the rest from each unit's own file: every
        project's (the project list's hover panel shows any project, 2026-11-12), the
        open project's last so its own win where two projects share a unit."""
        try:
            from core.ui import chapters
            project = self._get_project()
            open_key = self._project_key(project) if project else ""
            memory = self._data.get("project_memory") or {}
            mapping = {}
            for key in sorted(memory, key=lambda k: k == open_key):
                mem = memory.get(key)
                folders = dict(mem.get("folders") or {}) if isinstance(mem, dict) else {}
                where = project if (project and key == open_key) else key
                for rel, roles in folders.items():
                    base = os.path.join(where, rel).replace("\\", "/") if rel else where
                    mapping[base] = dict(roles or {})
            chapters.set_extra_overrides(mapping)
        except Exception:
            logger.debug("Could not hand over the project's folder choices", exc_info=True)

    def _target(self, project: str = "") -> tuple:
        """(project folder, is it the open one) for the panel's slots: `project`, or
        the open project when "" ."""
        cur = self._get_project()
        if not str(project or "").strip():
            return cur, bool(cur)
        p = self._clean_path(project)
        if cur and self._pkey(p) == self._pkey(cur):
            return cur, True
        return p, False

    def _unit_base(self, unit: str, project: Optional[str] = None) -> str:
        project = self._get_project() if project is None else project
        unit = str(unit or "").replace("\\", "/").strip("/")
        return (os.path.join(project, unit) if unit else project).replace("\\", "/")

    def _folders_changed(self, project: Optional[str] = None, is_open: bool = True) -> None:
        """A project's stage folders changed: the open one's stages look again; another
        project's change is only said (projectFoldersEdited), its stages aren't open."""
        if is_open:
            self._chapter_table = {}
            self._chapter_units = None
            self.projectFoldersChanged.emit()
            self.projectRefreshed.emit()
            self.commonChapterChanged.emit()
        self.projectFoldersEdited.emit(str(self._get_project() if project is None else project))

    ROLE_LABELS = {"source": "Source", "depth": "Depth Maps", "splats": "Splats",
                   "inpainted": "Inpainted", "merged": "Merged"}
    CLIP_COUNT_CAP = 999

    @Slot(result="QVariantList")
    def projectUnits(self) -> List[dict]:
        """[{unit, label}]: the open project's units -- one ("" for the project
        itself) for a movie, an episode each for a show."""
        return self.projectUnitsFor("")

    @Slot(str, result="QVariantList")
    def projectUnitsFor(self, project: str) -> List[dict]:
        """projectUnits for any project folder ("" = the open one)."""
        project, is_open = self._target(project)
        if not project:
            return []
        from core.ui import chapters
        if is_open:
            units = self._units()
        elif os.path.isdir(project):
            try:
                units = chapters.find_units(project) or [""]
            except Exception:
                units = [""]
        else:
            return []
        name = os.path.basename(project.rstrip("/"))
        return [{"unit": u, "label": chapters.unit_label(u) if u else name} for u in units]

    @Slot(str, result="QVariantList")
    def projectFolders(self, unit: str) -> List[dict]:
        """The unit's five stage folders: [{role, label, path, exists, clips, overridden}].
        path: the folder found (or chosen), else the one the stage would make; clips:
        media files directly in it or in its chapter folders (capped)."""
        return self.projectFoldersFor("", unit)

    @Slot(str, str, result="QVariantList")
    def projectFoldersFor(self, project: str, unit: str) -> List[dict]:
        """projectFolders for any project folder ("" = the open one)."""
        project, _is_open = self._target(project)
        if not project:
            return []
        from core.ui import chapters
        base = self._unit_base(unit, project)
        dirs = chapters.stage_dirs(base)
        own = chapters.overrides(base)
        out = []
        for role in chapters.ROLES:
            path = dirs[role]
            out.append({"role": role, "label": self.ROLE_LABELS[role], "path": path,
                        "exists": os.path.isdir(path), "clips": self._count_clips(path, chapters),
                        "overridden": role in own})
        return out

    def _count_clips(self, folder: str, chapters) -> int:
        cap = self.CLIP_COUNT_CAP
        count = 0

        def scan(path: str, sub: bool) -> None:
            nonlocal count
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if count >= cap:
                            return
                        if entry.is_file():
                            if os.path.splitext(entry.name)[1].lower() in chapters.MEDIA_EXTS:
                                count += 1
                        elif sub and entry.is_dir() and chapters.chapter_number(entry.name) is not None:
                            scan(entry.path, False)
            except OSError:
                pass
        scan(folder, True)
        return min(count, cap)

    @Slot(str, str, str, result=bool)
    def setUnitFolder(self, unit: str, role: str, path: str) -> bool:
        """The unit's `role` stage folder is `path` (any folder, made or not), kept in
        the unit's own .studio_project.json -- or here, when that cannot be written."""
        return self.setUnitFolderFor("", unit, role, path)

    @Slot(str, str, str, str, result=bool)
    def setUnitFolderFor(self, project: str, unit: str, role: str, path: str) -> bool:
        """setUnitFolder for any project folder ("" = the open one); kept here under that
        project's own key when the unit can't be written. Only the open project's
        stages look again."""
        from core.ui import chapters
        project, is_open = self._target(project)
        path = str(path or "").strip().replace("\\", "/")
        if not project or role not in chapters.ROLES:
            return False
        if not path:
            return self.clearUnitFolderFor(project, unit, role)
        unit = str(unit or "").replace("\\", "/").strip("/")
        base = self._unit_base(unit, project)
        mem = self._project_memory(project).setdefault("folders", {})
        own = dict(chapters.read_override_file(base))
        own[role] = path
        if chapters.write_override_file(base, own):
            kept = mem.get(unit) or {}
            kept.pop(role, None)
            if not kept:
                mem.pop(unit, None)
        else:
            mem.setdefault(unit, {})[role] = chapters.stored_override(base, path)
        self.save()
        self._sync_overrides()
        self._folders_changed(project, is_open)
        return True

    @Slot(str, str, result=bool)
    def clearUnitFolder(self, unit: str, role: str) -> bool:
        """The unit's `role` stage folder found by its name again. False when the
        unit's own file holds the choice and cannot be written."""
        return self.clearUnitFolderFor("", unit, role)

    @Slot(str, str, str, result=bool)
    def clearUnitFolderFor(self, project: str, unit: str, role: str) -> bool:
        """clearUnitFolder for any project folder ("" = the open one)."""
        from core.ui import chapters
        project, is_open = self._target(project)
        if not project or role not in chapters.ROLES:
            return False
        unit = str(unit or "").replace("\\", "/").strip("/")
        base = self._unit_base(unit, project)
        mem = self._project_memory(project).setdefault("folders", {})
        kept = mem.get(unit) or {}
        kept.pop(role, None)
        if not kept:
            mem.pop(unit, None)
        ok = True
        own = dict(chapters.read_override_file(base))
        if role in own:
            own.pop(role)
            ok = chapters.write_override_file(base, own)
        self.save()
        self._sync_overrides()
        self._folders_changed(project, is_open)
        return ok

    @Slot(str, result=bool)
    def createProjectFolders(self, unit: str) -> bool:
        """Make the unit's missing stage folders, by the default names (with the
        prefix the others share) or the ones chosen for it."""
        return self.createProjectFoldersFor("", unit)

    @Slot(str, str, result=bool)
    def createProjectFoldersFor(self, project: str, unit: str) -> bool:
        """createProjectFolders for any project folder ("" = the open one)."""
        from core.ui import chapters
        project, is_open = self._target(project)
        if not project:
            return False
        base = self._unit_base(unit, project)
        if not os.path.isdir(base):
            return False
        try:
            for path in chapters.stage_dirs(base).values():
                os.makedirs(path, exist_ok=True)
        except OSError:
            logger.warning("Could not make the stage folders in %s", base, exc_info=True)
            self._folders_changed(project, is_open)
            return False
        self._folders_changed(project, is_open)
        return True

    def _project_empty(self) -> bool:
        project = self._get_project()
        if not project or not os.path.isdir(project):
            return False
        try:
            from core.ui import chapters
            return not chapters.find_units(project)
        except Exception:
            return False

    #: The open project has no stage folders yet (offer to make them).
    projectEmpty = Property(bool, _project_empty, notify=projectFoldersChanged)

    # -- small helpers QML cannot do itself ---------------------------------

    @Slot(QUrl, result=str)
    def urlToPath(self, url: QUrl) -> str:
        return os.path.normpath(url.toLocalFile()) if url.isLocalFile() else url.toString()

    @Slot(QUrl, result="QVariantMap")
    def dropInfo(self, url: QUrl) -> Dict[str, Any]:
        """What a drag from Explorer carries (Drop Zones, user 2026-10-25): its
        path, whether it is a folder, and a file's extension (lower case, no
        dot). An empty path for anything that is not a local file."""
        if not url.isLocalFile():
            return {"path": "", "dir": False, "ext": ""}
        path = os.path.normpath(url.toLocalFile())
        is_dir = os.path.isdir(path)
        ext = "" if is_dir else os.path.splitext(path)[1].lstrip(".").lower()
        return {"path": path, "dir": is_dir, "ext": ext}

    @Slot(str, result=bool)
    def openManual(self, where: str) -> bool:
        """F1 (user, 2026-10-02): the manual, at `where` ("part2.html#splatting";
        "" for the contents), in the default browser. Windows drops a file
        link's #section on the way to the browser, so a section goes through
        a one-line page in the temp folder that jumps there. False when the
        manual is not in this Studio folder."""
        page, _, section = str(where or "").partition("#")
        path = REPO / "docs" / "manual" / (page or "index.html")
        if not path.is_file():
            return False
        url = QUrl.fromLocalFile(str(path))
        if section:
            import tempfile
            url.setFragment(section)
            jump = Path(tempfile.gettempdir()) / "stereocrafter_manual.html"
            try:
                jump.write_text('<!doctype html><meta charset="utf-8">'
                                f'<meta http-equiv="refresh" content="0;url={url.toString()}">', encoding="utf-8")
                url = QUrl.fromLocalFile(str(jump))
            except OSError:
                url.setFragment("")
        return QDesktopServices.openUrl(url)

    @Slot(str, str, result="QVariantList")
    def glyphOffset(self, family: str, glyph: str):
        """[dx, dy], as fractions of the font size, that move an icon glyph so
        the middle of its INK is the middle of its box (user, 2026-10-05: the
        ticks and i's in round badges sat a little off). Icon fonts put a
        glyph anywhere in its em box, and Qt's measured outline of these is
        out by up to 2 px, so each glyph is drawn once, large, off screen, and
        its ink found. Cached per (font, glyph)."""
        key = (family, glyph)
        cache = self.__dict__.setdefault("_glyph_offsets", {})
        if key in cache:
            return cache[key]
        off = [0.0, 0.0]
        try:
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter
            size = 128
            f = QFont(family)
            f.setPixelSize(size)
            f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            fm = QFontMetricsF(f)
            adv, lh = fm.horizontalAdvance(glyph), fm.height()
            n = int(size * 2)
            img = QImage(n, n, QImage.Format.Format_Grayscale8)
            img.fill(0)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setFont(f)
            p.setPen(QColor("white"))
            # laid out as a centred Text lays it out: its advance x line box, centred
            p.drawText(QRectF((n - adv) / 2, (n - lh) / 2, adv, lh), Qt.AlignmentFlag.AlignCenter, glyph)
            p.end()
            import numpy as np
            a = np.frombuffer(img.constBits(), np.uint8).reshape(n, img.bytesPerLine())[:, :n]
            ys, xs = np.nonzero(a > 64)
            if xs.size:
                off = [round(float(n / 2 - (xs.min() + xs.max() + 1) / 2) / size, 4),
                       round(float(n / 2 - (ys.min() + ys.max() + 1) / 2) / size, 4)]
        except Exception:
            logger.debug("glyphOffset failed for %r", glyph, exc_info=True)
        cache[key] = off
        return off

    @Slot(str, result=QUrl)
    def pathToUrl(self, path: str) -> QUrl:
        if path and os.path.isfile(path):
            path = os.path.dirname(path)
        return QUrl.fromLocalFile(path) if path and os.path.isdir(path) else QUrl()

    @Slot(str, str, result=QUrl)
    def fileUrl(self, folder: str, name: str) -> QUrl:
        return QUrl.fromLocalFile(os.path.join(folder or "", name))

    @Slot(QObject, result="QVariantMap")
    def screenRoom(self, window) -> dict:
        """The free area (logical px) of the screen `window` is on -- QML's
        Screen only offers the whole desktop's, which adds stacked or side-by-
        side screens together (user, 2026-09-28).

        STUDIO_SCREEN_ROOM ("WxH", logical px) pretends to be a screen, for the checks
        (2026-10-08: the window's height stops at the screen's room, the page scrolls past it)."""
        fake = os.environ.get("STUDIO_SCREEN_ROOM", "")
        if fake:
            try:
                w, h = (int(v) for v in fake.lower().split("x"))
                return {"width": w, "height": h}
            except ValueError:
                pass
        try:
            g = window.screen().availableGeometry()
            return {"width": g.width(), "height": g.height()}
        except Exception:
            return {"width": 1920, "height": 1040}

    @Property(bool, constant=True)
    def capToScreen(self) -> bool:
        """Whether the window's minimum height stops at its screen's room (the page scrolls past
        it). Offscreen (the checks) there is no real screen, so no -- unless STUDIO_SCREEN_ROOM
        pretends to be one."""
        return QGuiApplication.platformName() != "offscreen" or bool(os.environ.get("STUDIO_SCREEN_ROOM"))

    @Slot(str, result=str)
    def baseName(self, path: str) -> str:
        return os.path.splitext(os.path.basename(path or ""))[0]

    @Slot()
    def checkEnvironment(self) -> None:
        """Once, after the window shows: what this PC is missing (studio.env_check), on its own thread --
        NVML and PATH only, nothing on the card -- each problem an envWarning."""
        if getattr(self, "_env_checked", False):
            return
        self._env_checked = True

        def run():
            try:
                from studio import env_check
                found = env_check.problems()
            except Exception:
                logger.warning("Environment check failed", exc_info=True)
                return
            for title, text in found:
                logger.warning("%s: %s", title, text)
                self.envWarning.emit(title, text)
        threading.Thread(target=run, name="env-check", daemon=True).start()

    @Slot(result=str)
    def releaseGpu(self) -> str:
        """v1 GPU > Release GPU Memory: collect and empty the CUDA cache."""
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                return "CUDA cache released."
            return "No CUDA device."
        except Exception as e:
            return f"Could not release GPU memory: {e}"

    @Slot(result=str)
    def vramStatus(self) -> str:
        """v1 GPU > Show VRAM Status."""
        try:
            import torch
            if not torch.cuda.is_available():
                return "No CUDA device available."
            free, total = torch.cuda.mem_get_info()
            gib = 1024 ** 3
            return (f"{torch.cuda.get_device_name(0)}\n"
                    f"Used: {(total - free) / gib:.2f} GiB of {total / gib:.2f} GiB\n"
                    f"Free: {free / gib:.2f} GiB\n"
                    f"Allocated by this process: {torch.cuda.memory_allocated() / gib:.2f} GiB\n"
                    f"Reserved by this process: {torch.cuda.memory_reserved() / gib:.2f} GiB")
        except Exception as e:
            return f"VRAM status unavailable: {e}"

    @Slot(str, result=str)
    def openFile(self, path: str) -> str:
        """A clip in the user's default player (Upcoming Jobs' "Play ...", user,
        2026-10-10). Returns "" on success, else the reason."""
        if not path or not os.path.exists(path):
            return "It isn't there any more."
        try:
            os.startfile(os.path.normpath(path))        # Windows: whatever plays this kind of file
            return ""
        except OSError as e:
            return e.strerror or str(e)

    @Slot(str, result=str)
    def revealFile(self, path: str) -> str:
        """Explorer with the file selected (the clip lists' "Open ... in Explorer", user,
        2026-10-18). Returns "" on success, else the reason."""
        try:
            from core.ui.folder_open import reveal_file
            ok, message = reveal_file(path)
            return "" if ok else message
        except Exception as e:
            return str(e)

    @Slot(str, result=str)
    def openFolder(self, path: str) -> str:
        """Open in Explorer. Returns "" on success, else the reason."""
        try:
            from core.ui.folder_open import open_folder
            ok, message = open_folder(path)
            return "" if ok else message
        except Exception as e:
            return str(e)
