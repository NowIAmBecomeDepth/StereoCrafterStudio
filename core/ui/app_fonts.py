"""app_fonts.py -- the bundled typefaces, loaded without installing anything.

Three faces, each doing one job:

  * **Bahnschrift SemiBold** for section headings, set in caps. It is NOT
    bundled: Bahnschrift ships with Windows 10 and later, and SemiBold is a
    named instance of it that Tk can ask for directly, so it is a real weight
    rather than a bold Windows synthesises. Off Windows it is simply missing,
    and the headings fall back like any other face here. Headings are
    uppercased at the point of use by :func:`heading_text`, since the face has
    a lowercase.

    Earlier trials, all rejected in use: Bungee, then Mochiy Pop One.
  * **Oxanium** for body text, in ordinary title case. Caps were tried for the
    body and rejected: they cost about 18% of the label column
    ("Convergence Plane" goes from 177px to 208px) and read as shouting across
    a page of forty settings.
  * **Cal Sans** for buttons, in caps, at :data:`BUTTON_SIZE`. One weight
    only, and a heavy one, so buttons are never asked for bold: Tk would
    synthesise a thicker copy of an already-heavy face. It replaced
    Montserrat bold; Gurajada was tried in between and dropped for its
    very tall line box, which made every button about half again as tall.

All three are SIL Open Font License, which permits bundling and redistributing
the files with software. The licences sit beside them in assets/fonts and must
stay there.

Nothing is installed
--------------------
``AddFontResourceExW(path, FR_PRIVATE, 0)`` loads a font for THIS PROCESS
only: no install, no admin rights, no registry, and nothing added to the
user's font list. ``RemoveFontResourceExW`` takes it back out. Measured
behaviour, not assumed -- a family loaded this way appears in
``tkinter.font.families()`` immediately, including on a Tk root that already
exists, so there is no ordering constraint on when this runs.

Everything here degrades rather than fails. Off Windows, or if a file is
missing or the call is refused, :func:`family_for` hands back a stock family
and the app looks as it did before.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: Where the bundled files live, beside the licences that cover them.
FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "fonts",
)

HEADING = "Bahnschrift SemiBold"

#: Point size for a section heading in the heading face.
#:
#: Chosen by cap height, not by point size: the headings are all caps, so the
#: capital is the whole heading. Bahnschrift's capital is 0.71 em, and at 15
#: it matches the height the previous heading face had at its own size.
#:
#: It is also a narrow face -- 'OUTPUT & ENCODING SETTINGS' measures 285px at
#: 15, well inside the 476px the section narrows to in a stacked layout.
HEADING_SIZE = 15
BODY = "Oxanium"
BUTTON = "Cal Sans"

#: Point size for button labels. Cal Sans's capital is 0.70 em, the same
#: as Montserrat's, so 13 would have matched the old letter height
#: exactly; 14 was chosen by eye in the trial, one step larger. The face is
#: narrower, so even at 14 the longest label (IMPORT FROM DEPTHCRAFTER)
#: comes out narrower than it did in Montserrat bold at 13.
BUTTON_SIZE = 13

#: The tab strips. Bahnschrift, like the headings, at two weights: the
#: current tab in SemiBold and the rest Regular, so weight still marks the
#: selection the way it did when these were Montserrat. Both ship with
#: Windows; off it they fall back like any other face here.
TAB = "Bahnschrift"
TAB_CURRENT = "Bahnschrift SemiBold"

#: What to use when a bundled face is not available. Segoe UI is what the app
#: used throughout before this module existed, so a failed load is invisible.
FALLBACK = "Segoe UI"

#: filename stem -> the family it provides
_FILES = {
    "Oxanium-Regular": BODY,
    "Oxanium-Bold": BODY,
    "CalSans-Regular": BUTTON,
}

_FR_PRIVATE = 0x10

_loaded: list = []
_load_attempted = False
_available_cache: Dict[str, bool] = {}


def _gdi():
    """A PRIVATE handle on gdi32, never ctypes.windll.gdi32.

    ctypes.windll caches one library object per DLL for the whole process, and
    its function objects are shared with every other caller. Declaring
    argtypes on windll.gdi32.AddFontResourceExW therefore redeclares it for
    everyone -- and CustomTkinter calls that exact function to load its own
    Roboto, passing byref(buffer). With a c_wchar_p declared on it, CTk's call
    raises ArgumentError and its font loading breaks.

    So: a separate WinDLL instance, and arguments typed at the call site
    rather than on the function. Nothing global is mutated either way.
    """
    return ctypes.WinDLL("gdi32", use_last_error=True)


def load() -> bool:
    """Load the bundled faces for this process. True when all of them took.

    Idempotent: calling it again is free. Safe to call before or after the Tk
    root exists.
    """
    global _load_attempted
    if _load_attempted:
        return len(_loaded) == len(_FILES)
    _load_attempted = True

    if not sys.platform.startswith("win"):
        logger.debug("Bundled fonts are loaded through a Win32 call; skipping.")
        return False
    try:
        gdi = _gdi()
    except Exception:
        logger.debug("Could not reach gdi32 to load the bundled fonts", exc_info=True)
        return False

    for stem in _FILES:
        path = os.path.join(FONT_DIR, f"{stem}.ttf")
        if not os.path.isfile(path):
            logger.warning("Bundled font missing: %s", path)
            continue
        try:
            if gdi.AddFontResourceExW(
                ctypes.c_wchar_p(path), ctypes.c_uint(_FR_PRIVATE), None
            ):
                _loaded.append(path)
            else:
                logger.warning("Windows refused the bundled font %s", stem)
        except Exception:
            logger.debug("Could not load the bundled font %s", stem, exc_info=True)

    _available_cache.clear()
    logger.info("Bundled fonts loaded: %d of %d", len(_loaded), len(_FILES))
    return len(_loaded) == len(_FILES)


def unload() -> None:
    """Hand the fonts back. Only worth calling on a clean shutdown."""
    if not _loaded:
        return
    try:
        gdi = _gdi()
        for path in _loaded:
            gdi.RemoveFontResourceExW(
                ctypes.c_wchar_p(path), ctypes.c_uint(_FR_PRIVATE), None
            )
    except Exception:
        logger.debug("Could not release the bundled fonts", exc_info=True)
    _loaded.clear()
    _available_cache.clear()


def is_available(family: str, widget=None) -> bool:
    """Whether Tk can actually draw with `family` right now.

    Asked of Tk rather than inferred from the load result: a font can be
    present because the user installed it themselves, and a load that reported
    success is still worth confirming before the whole UI is pointed at it.
    """
    if family in _available_cache:
        return _available_cache[family]
    try:
        import tkinter.font as tkfont

        # `widget` is whatever the caller had to hand, and not every caller
        # is a widget: DepthCrafterGUI subclasses the themed-stage mixin
        # ALONE, with no ttk.Frame behind it, so it has no .tk and Tk's
        # font query raises on it. Anything without .tk falls through to
        # the default root, which answers the same question.
        root = widget if getattr(widget, "tk", None) is not None else None
        names = tkfont.families(root) if root is not None else tkfont.families()
        families = {f.lower() for f in names}
        answer = family.lower() in families
    except Exception:
        # No Tk root yet, or no display. Don't cache a guess.
        return False
    _available_cache[family] = answer
    return answer


def family_for(role: str, widget=None) -> str:
    """The family to use for "heading" / "body" / "button", or the fallback."""
    wanted = {"heading": HEADING, "body": BODY, "button": BUTTON,
              "tab": TAB, "tab_current": TAB_CURRENT}.get(role, BODY)
    return wanted if is_available(wanted, widget) else FALLBACK


def heading_text(text: str) -> str:
    """Section headings are set in caps.

    A transform at the point of use, for the same reason as
    :func:`button_text`: the source keeps the readable, searchable string.
    Applied only for a stage using the bundled face -- a stage still on
    Segoe UI keeps its title-case headings.
    """
    return str(text).upper()


#: Capital height as a fraction of the em, for the faces buttons are drawn in.
#: Measured from the font files (the H glyph / OS/2 sCapHeight) rather than
#: read at runtime, so the app needs no font-parsing library to use them.
CAP_HEIGHTS = {
    BUTTON: 0.700,
    TAB: 0.710,
    TAB_CURRENT: 0.710,
    HEADING: 0.710,
}


def cap_lift(font) -> float:
    """How far, in pixels, a font's capitals sit BELOW its line box's centre.

    Tk centres a label by its whole line box, ascent plus descent. A face with
    a tall ascent and a short descent -- Cal Sans reserves room above its
    capitals for accents and almost nothing below -- therefore draws caps-only
    text visibly low: 1.6px at 14, over two on a 150% screen. Zero for any
    face not in :data:`CAP_HEIGHTS`, so nothing unknown is ever moved.
    """
    # Read from the font's own description and Tk's metrics OF THAT
    # description -- never through `font actual`. Tk caches each font's
    # "actual" point size with the `tk scaling` in force when the font was
    # first made, and in Sharp mode that scaling changes as the window
    # crosses monitors; a font re-created mid-change was then read as 15pt
    # where it is 20px, and the padding worked out from it drifted every time
    # Studio moved screen. The metrics of a description are the real pixels
    # the label is drawn with.
    try:
        import tkinter
        import tkinter.font as tkfont

        if isinstance(font, tkfont.Font):
            family, size = str(font.cget("family")), int(font.cget("size"))
            tk_app, desc = font._tk, font.name
        else:
            tk_app = tkinter._default_root.tk
            parts = tk_app.splitlist(font) if isinstance(font, str) else tuple(font)
            family, size, desc = str(parts[0]), int(float(parts[1])), font
        cap = CAP_HEIGHTS.get(family)
        if cap is None:
            return 0.0
        # Negative Tk sizes are pixels (what CTk uses); positive are points,
        # converted with Tk's own pixels-per-point so a scaled display agrees.
        em = abs(size) if size < 0 else size * float(tk_app.call("tk", "scaling"))
        ascent = int(tk_app.call("font", "metrics", desc, "-ascent"))
        descent = int(tk_app.call("font", "metrics", desc, "-descent"))
        return (ascent - cap * em / 2.0) - (ascent + descent) / 2.0
    except Exception:
        return 0.0


def _is_word(token: str) -> bool:
    """A token with a letter or digit in it -- not "<", ">", "/" or "..."."""
    return any(ch.isalnum() for ch in token)


def button_text(text: str) -> str:
    """Button labels are set in caps, with a wider gap between words.

    Done as a transform at the point of use rather than by rewriting every
    label in the source, so the strings stay readable where they are defined
    and searchable when something goes wrong.

    Two spaces between WORDS: in caps, and in a face as tightly set as Cal
    Sans, a single space let "BROWSE FOLDER" read as one long word. Only
    between two words, though -- "< PREV" keeps its single space, because the
    arrow is punctuation, and doubling that gap pushed it away from the word
    it points with.
    """
    tokens = str(text).upper().split()
    if not tokens:
        return str(text).upper()
    out = tokens[0]
    for before, token in zip(tokens, tokens[1:]):
        out += ("  " if _is_word(before) and _is_word(token) else " ") + token
    return out


def tab_text(text: str) -> str:
    """Tab labels: caps only. The word gap is for buttons, not the tab strips."""
    return str(text).upper()


def apply_ctk_default(widget=None) -> Optional[str]:
    """Point CustomTkinter's default font family at the body face.

    CTk resolves ``CTkFont()`` against ``ThemeManager.theme["CTkFont"]``, so
    setting the family there covers every CTk widget that does not ask for
    something specific -- which is most of them. Returns the family applied.
    """
    try:
        import customtkinter as ctk

        family = family_for("body", widget)
        ctk.ThemeManager.theme.setdefault("CTkFont", {})["family"] = family
        return family
    except Exception:
        logger.debug("Could not set the CustomTkinter default font", exc_info=True)
        return None
