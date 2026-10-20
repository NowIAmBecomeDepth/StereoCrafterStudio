"""
themed_menubar.py — A ttk menu strip that Tk can actually colour.

Why this exists
---------------
`root.config(menu=tk.Menu(root))` hands the top-level menu strip to the OS. On
Windows that strip is drawn natively and ignores Tk's `bg`/`fg`, so a dark app
keeps a light grey menu bar no matter what the theme sets. The dropdowns are
real Tk menus and *do* theme correctly, which is why only the strip looks wrong.

There is no Win32 API to recolour a native menubar, so the only fix is to stop
using one: this builds the strip out of ttk widgets inside the window, which Tk
owns completely.

Drop-in usage
-------------
Replaces the two lines each GUI already has:

    self.menubar = tk.Menu(self)          ->  self.menubar = ThemedMenuBar(self)
    self.config(menu=self.menubar)            (no second line needed)

`add_cascade(label=..., menu=...)` works as before, and `.config(bg=, fg=,
activebackground=, activeforeground=)` is accepted so existing theme code keeps
working with no changes.

Tradeoff
--------
This is not an OS menubar, so it does not get free Alt-mnemonic handling. F10
and Alt+<letter> are wired up manually below to cover the common cases.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def menu_check_color(is_dark: bool) -> str:
    """Colour of the check / radio mark in dropdown menus.

    Tk draws a checkbutton or radiobutton entry's mark in the menu's
    ``selectcolor``, which defaults to black -- invisible on a dark menu. So:
    white on dark palettes, black on light ones.
    """
    return "white" if is_dark else "black"


def set_menu_check_color(menu: Any, color: str) -> None:
    """Apply ``selectcolor`` to a menu and every cascade submenu under it.

    Submenus (the Theme list, for instance) are separate tk.Menu widgets that a
    colour pass on the parent never reaches, so they are walked explicitly.
    """
    seen = set()
    stack = [menu]
    while stack:
        current = stack.pop()
        if current is None or str(current) in seen:
            continue
        seen.add(str(current))
        try:
            current.configure(selectcolor=color)
        except Exception:
            continue
        try:
            last = current.index("end")
        except Exception:
            last = None
        if last is None:
            continue
        for i in range(last + 1):
            try:
                if current.type(i) == "cascade":
                    name = current.entrycget(i, "menu")
                    if name:
                        stack.append(current.nametowidget(name))
            except Exception:
                continue


class ThemedMenuBar(ttk.Frame):
    """A horizontal menu strip built from ttk widgets.

    Mirrors the small slice of the `tk.Menu` menubar API the GUIs actually use,
    so it can be swapped in without touching menu construction or theme code.
    """

    def __init__(self, master: Any, bind_accelerators: bool = True, **kwargs: Any):
        # The strip sits at the top of the window, above every other widget.
        # Created before the rest of the UI in all four GUIs, so a plain pack
        # here lands it in the right place.
        #
        # bind_accelerators: F10 and Alt+<letter> are bound on winfo_toplevel()
        # with add="+", which is correct for a standalone window and wrong when
        # several strips share one. All four GUIs have a File menu, so four
        # strips in one notebook would mean a single Alt+F posts four menus at
        # once. The unified shell passes True for its own strip and False for
        # every stage strip, leaving exactly one owner of the keybindings.
        super().__init__(master, style="Menubar.TFrame")
        self._master = master
        self._bind_accelerators = bind_accelerators
        self._buttons: List[tk.Widget] = []
        self._menus: List[tk.Menu] = []
        self._menu_for: Dict[str, tk.Menu] = {}
        self._open_menu: Optional[tk.Menu] = None

        # Entries can belong to a group. Ungrouped entries (group=None) are the
        # host application's own and are always shown; grouped entries belong to
        # one notebook stage and are shown only while that stage is on screen.
        # This is what lets four stages share a single strip instead of each
        # stacking its own below the host's.
        self._entries: List[Dict[str, Any]] = []
        self._default_group: Optional[str] = None
        self._active_group: Optional[str] = None

        self._bg = kwargs.get("bg", "#3c3c3c")
        self._fg = kwargs.get("fg", "white")
        self._active_bg = kwargs.get("activebackground", "#555555")
        self._active_fg = kwargs.get("activeforeground", "white")

        self.pack(side="top", fill="x")

        # F10 opens the first menu, matching native behaviour closely enough
        # to not feel broken to keyboard users.
        try:
            toplevel = self.winfo_toplevel()
            if self._bind_accelerators:
                toplevel.bind("<F10>", self._on_f10, add="+")
            # A posted tk.Menu grabs input, so a click outside it lands as an
            # <Unmap>/<FocusOut> on the menu rather than a click on the app.
            # Clear our own bookkeeping when that happens, otherwise the next
            # click on a title would try to unpost an already-closed menu.
            toplevel.bind("<Button-1>", self._on_root_click, add="+")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # tk.Menu-compatible API
    # ------------------------------------------------------------------

    def add_cascade(self, label: str = "", menu: Optional[tk.Menu] = None, **kwargs: Any) -> None:
        """Add a top-level entry. Signature matches tk.Menu.add_cascade."""
        if menu is None:
            return

        # A plain tk.Label, not a Menubutton: Tk only posts a Menubutton's menu
        # when that menu is a *descendant* of the button, and the GUIs create
        # their menus as children of the menubar (tk.Menu(self.menubar)) long
        # before add_cascade sees them. Reparenting a live menu isn't possible,
        # so the click is bound and the menu posted manually instead, which
        # works regardless of where the menu sits in the widget tree.
        btn = tk.Label(
            self,
            text=label,
            bg=self._bg,
            fg=self._fg,
            padx=8,
            pady=3,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
        )
        btn.pack(side="left")

        self._buttons.append(btn)
        self._menus.append(menu)
        self._menu_for[str(btn)] = menu
        self._entries.append({"btn": btn, "menu": menu, "label": label, "group": self._default_group})
        if self._default_group is not None and self._default_group != self._active_group:
            # Added while its stage is not the visible one: keep it hidden
            # until set_active_group says otherwise.
            try:
                btn.pack_forget()
            except Exception:
                pass

        btn.bind("<Button-1>", lambda e, b=btn: self._post(b), add="+")
        btn.bind("<Enter>", lambda e, b=btn: self._hover(b, True), add="+")
        btn.bind("<Leave>", lambda e, b=btn: self._hover(b, False), add="+")

        # Alt+<first letter> as a stand-in for the native mnemonic. Only for
        # the host's own entries: stage entries come and go with the visible
        # tab, and several stages have a "File" menu, so binding theirs would
        # make one Alt+F ambiguous.
        mnemonic = (
            self._mnemonic_of(label)
            if (self._bind_accelerators and self._default_group is None)
            else None
        )
        if mnemonic:
            try:
                toplevel = self.winfo_toplevel()
                toplevel.bind(
                    f"<Alt-{mnemonic}>",
                    lambda e, b=btn: self._post(b),
                    add="+",
                )
            except Exception:
                pass

    @contextlib.contextmanager
    def group(self, name: str) -> Iterator[None]:
        """Tag every cascade added inside this block as belonging to *name*.

        Lets a stage build its menus with unmodified ``add_cascade`` calls:

            with shell.menubar.group("splatting"):
                app = SplatterGUI(page, shell=shell)
        """
        previous = self._default_group
        self._default_group = name
        try:
            yield
        finally:
            self._default_group = previous

    def set_active_group(self, name: Optional[str]) -> None:
        """Show the host entries plus one group's; hide every other group.

        Re-packs from scratch in insertion order, so the host's entries always
        stay to the left of the active stage's however many times this runs.
        """
        self._active_group = name
        self._unpost_all()
        for entry in self._entries:
            try:
                entry["btn"].pack_forget()
            except Exception:
                pass
        for entry in self._entries:
            if entry["group"] is None or entry["group"] == name:
                try:
                    entry["btn"].pack(side="left")
                except Exception:
                    pass

    def add_command(self, label: str = "", command: Any = None, **kwargs: Any) -> None:
        """A top-level entry with no dropdown (rare, but tk.Menu allows it)."""
        btn = tk.Label(
            self,
            text=label,
            bg=self._bg,
            fg=self._fg,
            padx=8,
            pady=3,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
        )
        btn.pack(side="left")
        self._buttons.append(btn)
        if command is not None:
            btn.bind("<Button-1>", lambda e, c=command: c(), add="+")
        btn.bind("<Enter>", lambda e, b=btn: self._hover(b, True), add="+")
        btn.bind("<Leave>", lambda e, b=btn: self._hover(b, False), add="+")

    def config(self, **kwargs: Any) -> None:  # type: ignore[override]
        """Accept tk.Menu colour options so existing theme code keeps working.

        Any non-colour kwargs are passed to ttk.Frame, and silently dropped if
        it rejects them -- a theme call must never break the app.
        """
        colour_keys = ("bg", "background", "fg", "foreground",
                       "activebackground", "activeforeground")
        if any(k in kwargs for k in colour_keys):
            self._bg = kwargs.get("bg", kwargs.get("background", self._bg))
            self._fg = kwargs.get("fg", kwargs.get("foreground", self._fg))
            self._active_bg = kwargs.get("activebackground", self._active_bg)
            self._active_fg = kwargs.get("activeforeground", self._active_fg)
            self._restyle()

        rest = {k: v for k, v in kwargs.items() if k not in colour_keys}
        if rest:
            try:
                super().configure(**rest)
            except Exception:
                pass

    configure = config

    def entryconfig(self, *args: Any, **kwargs: Any) -> None:
        """No-op: index-based entry config isn't meaningful for this strip."""
        return

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _restyle(self) -> None:
        """Repaint the strip and its buttons after a theme change."""
        try:
            style = ttk.Style()
            style.configure("Menubar.TFrame", background=self._bg)
        except Exception:
            pass

        for btn in self._buttons:
            try:
                btn.configure(
                    bg=self._bg,
                    fg=self._fg,
                    activebackground=self._active_bg,
                    activeforeground=self._active_fg,
                )
            except Exception:
                pass

        # The dropdowns themselves are normal tk.Menus; the GUIs already theme
        # them, but recolour here too so the strip stays self-consistent even
        # if a caller only configures the menubar.
        #
        # Check marks follow the menu text: light text means a dark menu, so a
        # white mark. Read from the colour rather than an is_dark flag because
        # config() only ever receives colours.
        check = menu_check_color(self._is_dark_color(self._bg))
        for menu in self._menus:
            set_menu_check_color(menu, check)
        for menu in self._menus:
            try:
                menu.config(
                    bg=self._bg,
                    fg=self._fg,
                    activebackground=self._active_bg,
                    activeforeground=self._active_fg,
                )
            except Exception:
                pass

    def _is_dark_color(self, color: str) -> bool:
        """Perceived luminance of a Tk colour; named colours work too."""
        try:
            r, g, b = self.winfo_rgb(color)  # each 0..65535
        except Exception:
            return True
        return (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0 < 0.5

    def _hover(self, btn: tk.Widget, entering: bool) -> None:
        try:
            btn.configure(bg=self._active_bg if entering else self._bg)
        except Exception:
            pass

    def _post(self, btn: tk.Widget) -> str:
        """Open a button's dropdown under it, as a native menubar would."""
        menu = self._menu_for.get(str(btn))
        if menu is None:
            return "break"
        try:
            if not btn.winfo_ismapped():
                return "break"
        except Exception:
            pass
        try:
            # Close any menu already showing, so clicking a second title
            # switches menus instead of stacking them.
            self._unpost_all()
            menu.post(btn.winfo_rootx(),
                      btn.winfo_rooty() + btn.winfo_height())
            self._open_menu = menu
        except Exception as e:
            logger.debug(f"ThemedMenuBar: could not post menu: {e}")
        return "break"

    def _unpost_all(self) -> None:
        for m in self._menus:
            try:
                m.unpost()
            except Exception:
                pass
        self._open_menu = None

    def _on_root_click(self, event: Any = None) -> None:
        """Forget the open menu when a click lands outside the strip."""
        try:
            if event is not None and str(event.widget).startswith(str(self)):
                return
        except Exception:
            pass
        self._open_menu = None

    def _on_f10(self, event: Any = None) -> str:
        if self._buttons:
            return self._post(self._buttons[0])
        return "break"

    @staticmethod
    def _mnemonic_of(label: str) -> Optional[str]:
        for ch in label:
            if ch.isalpha():
                return ch.lower()
        return None
