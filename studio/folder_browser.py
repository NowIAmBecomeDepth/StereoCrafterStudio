"""folder_browser.py -- what Studio's folder picker (FolderPicker.qml) asks of
the disk (user, 2026-10-05: a Columns picker in place of Windows' folder
dialog, from prototypes/folder_picker). It lists; it writes only when the
user right-clicks: New Folder makes a new, empty folder (2026-10-05), and Move
to Recycle Bin, once the picker has asked, sends a folder or clip to the bin
(2026-10-10) -- never deleted for good: a drive with no bin keeps it.

Each folder comes with how many folders and clips it holds, so the picker can
show where the work is before going in. Listings are capped (MAX_ENTRIES) so
a huge folder or a slow drive can't stall the window for long.
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path
from typing import List

from PySide6.QtCore import QObject, QUrl, Slot

from core.common import recycle_bin as _recycle_bin
from core.common.recycle_bin import has_recycle_bin, send_to_recycle_bin

#: [(path, what it is)] the picker must not move to the Recycle Bin: the open project, the
#: folders a running job uses (user, 2026-10-14). Set by the app; None: nothing guarded.
PROTECTED = None


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)

VIDEO = ("mp4", "mkv", "mov", "avi", "webm", "m4v", "flv")
MAX_ENTRIES = 400


def natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def drives() -> List[str]:
    found = getattr(os, "listdrives", None)
    if found:
        try:
            return list(found())
        except OSError:
            pass
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


#: Names Windows keeps for devices.
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def _hidden(name: str) -> bool:
    return name.startswith(("$", ".")) or name in ("System Volume Information",)


class FolderBrowser(QObject):

    @Slot(result="QVariantList")
    def drives(self):
        return [{"name": d.rstrip("\\"), "path": d} for d in drives()]

    @Slot("QVariant", result=str)
    def toPath(self, where) -> str:
        """A url or a path, as a local path ('' for nothing)."""
        if isinstance(where, QUrl):
            return where.toLocalFile() if where.isLocalFile() else ""
        s = str(where or "")
        if s.startswith("file:"):
            return QUrl(s).toLocalFile()
        return os.path.normpath(s) if s else ""

    @Slot(result="QVariantList")
    def videoTypes(self):
        """The clip types listed when a picker names none (no dot)."""
        return list(VIDEO)

    @Slot(str, result=QUrl)
    def toUrl(self, path: str) -> QUrl:
        return QUrl.fromLocalFile(path) if path else QUrl()

    @Slot(str, result=bool)
    def isFile(self, path: str) -> bool:
        return bool(path) and os.path.isfile(path)

    @Slot(str, result=bool)
    def isDir(self, path: str) -> bool:
        return bool(path) and os.path.isdir(path)

    @Slot(str, result=bool)
    def hasDirs(self, path: str) -> bool:
        """Whether `path` holds a (shown) folder: one scandir that stops at the
        first. The picker asked dirs() -- every child scanned for its counts --
        only to learn this (bug sweep A2)."""
        try:
            with os.scandir(path) as it:
                for e in it:
                    if not _hidden(e.name) and e.is_dir():
                        return True
        except OSError:
            pass
        return False

    @Slot(str, result="QVariantList")
    def dirs(self, path: str):
        """The folders in `path`: name, path, and how many folders and clips each holds."""
        out = []
        try:
            with os.scandir(path) as it:
                entries = sorted((e for e in it if not _hidden(e.name) and e.is_dir()), key=lambda e: natural(e.name))
        except OSError:
            return out
        for e in entries[:MAX_ENTRIES]:
            sub = clips = 0
            try:
                with os.scandir(e.path) as it2:
                    for i, f in enumerate(it2):
                        if i > 2000:
                            break
                        try:
                            if f.is_dir():
                                sub += 1
                            elif f.name.rsplit(".", 1)[-1].lower() in VIDEO:
                                clips += 1
                        except OSError:
                            pass
            except OSError:
                pass
            out.append({"name": e.name, "path": e.path, "dirs": sub, "clips": clips})
        return out

    @Slot(str, "QVariantList", result="QVariantList")
    def files(self, path: str, types):
        """The files in `path` of the given extensions (no dot; empty: video)."""
        exts = tuple(str(t).lower().lstrip(".") for t in (types or [])) or VIDEO
        try:
            with os.scandir(path) as it:
                names = sorted((e.name for e in it if e.is_file() and e.name.rsplit(".", 1)[-1].lower() in exts),
                               key=natural)
        except OSError:
            return []
        return names[:MAX_ENTRIES]

    @Slot(str, result="QVariantList")
    def crumbs(self, path: str):
        """The path as steps from its drive: [{name, path}]."""
        if not path:
            return []
        p = Path(path)
        steps = []
        while True:
            steps.append({"name": p.name or str(p).rstrip("\\/"), "path": str(p)})
            if p.parent == p:
                break
            p = p.parent
        return list(reversed(steps))

    @Slot(str, result=str)
    def parentOf(self, path: str) -> str:
        p = Path(path) if path else None
        return str(p.parent) if p and p.parent != p else ""

    @Slot(str, str, result=str)
    def join(self, folder: str, name: str) -> str:
        return os.path.join(folder, name)

    @Slot(str, str, result="QVariantMap")
    def makeDir(self, parent: str, name: str):
        """New Folder: an empty folder `name` in `parent`. {"path"} when it was
        made, {"error"} with why not (a name Windows refuses, one that is
        there already, no permission)."""
        name = str(name or "").strip().rstrip(".")
        if not name:
            return {"error": "Type a name for the folder."}
        bad = set(r'<>:"/\|?*') & set(name)
        if bad:
            return {"error": "A folder name can't contain " + " ".join(sorted(bad)) + "."}
        if name.upper().split(".")[0] in _RESERVED:
            return {"error": f"Windows doesn't allow a folder called \"{name}\"."}
        if not os.path.isdir(parent):
            return {"error": "The folder it would go in isn't there any more."}
        path = os.path.join(parent, name)
        if os.path.exists(path):
            return {"error": f'"{name}" is there already.'}
        try:
            os.mkdir(path)
        except OSError as e:
            return {"error": f"Couldn't make the folder: {e.strerror or e}"}
        return {"path": path}

    @Slot(str, result="QVariantMap")
    def recycle(self, path: str):
        """Move to Recycle Bin (user, 2026-10-10): `path`, a folder or a clip, to
        the bin -- the picker has asked first. {"ok": True}, or {"error"} with
        why not. Nothing is ever deleted for good: on a drive with no Recycle
        Bin (a network share, some USB drives) it stays where it is."""
        path = os.path.normpath(str(path or "")) if path else ""
        if not path or not os.path.exists(path):
            return {"error": "It isn't there any more."}
        if Path(path).parent == Path(path):
            return {"error": "A whole drive can't be moved to the Recycle Bin."}
        # the open project, and what a running job is using (user, 2026-10-14)
        for held, what, inside_too in (PROTECTED() if PROTECTED else []):
            if held and (_inside(held, path) or (inside_too and _inside(path, held))):
                return {"error": f"That's {what}:\n{held}\n\nIt can't be moved to the Recycle Bin while "
                                 + ("it's open." if not inside_too else "the job runs.")}
        if not has_recycle_bin(path):
            return {"error": "This drive has no Recycle Bin, so it would be deleted for good -- it was left "
                             "where it is. Delete it in File Explorer if you're sure."}
        if send_to_recycle_bin(path):
            return {"ok": True}
        if _recycle_bin.last_kept == "too_big":        # asked (too big for the bin), and kept
            return {"kept": True}
        return {"error": "Windows couldn't move it to the Recycle Bin. Something may have it open "
                         "(a stage's preview, a player, File Explorer); close that and try again."}
