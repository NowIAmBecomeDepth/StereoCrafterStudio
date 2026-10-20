"""folder_open.py -- open an output folder in the OS file manager.

Shared by all four stages so "Open Output Folder" behaves identically
everywhere, including the part that is easy to get wrong: the button stays
usable while a render is running. On a long batch the whole point is to check
the clips that have already finished without waiting for the rest, so this
button must never be added to a stage's widgets_to_disable_during_processing.

Opening a folder is read-only and touches nothing the worker thread owns, so
there is no reason to gate it on processing state.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def reveal_file(path: str) -> tuple[bool, str]:
    """Open Explorer at *path* with it selected (user, 2026-10-18: the clip lists' "Open ... in
    Explorer"); a folder opens as itself. A file that isn't there any more opens the nearest
    folder that is, as open_folder does. Returns (ok, message)."""
    if not path or not str(path).strip():
        return False, "No file is set."
    target = os.path.abspath(os.path.expanduser(str(path).strip()))
    if os.path.isdir(target) or not os.path.isfile(target):
        return open_folder(target)
    try:
        if sys.platform.startswith("win"):
            # Explorer's own form, /select,"<path>" as one string: a list let Python quote it its own way
            subprocess.Popen(f'explorer /select,"{os.path.normpath(target)}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target)])
        return True, target
    except Exception as e:
        logger.exception("Could not show %s in the file manager", target)
        return False, str(e)


def open_folder(path: str) -> tuple[bool, str]:
    """Open *path* in Explorer/Finder/the desktop file manager.

    Returns (ok, message). The message is only worth showing when ok is False.

    If the path doesn't exist yet -- a common case, since the output folder is
    usually created by the first render -- the nearest existing parent is
    opened instead. That is more useful than an error, because it puts the user
    where the folder is about to appear.
    """
    if not path or not str(path).strip():
        return False, "No output folder is set."

    target = os.path.abspath(os.path.expanduser(str(path).strip()))

    if not os.path.isdir(target):
        probe = target
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:  # reached the root without finding anything
                probe = ""
                break
            probe = parent
        if not probe:
            return False, f"Folder does not exist:\n{target}"
        logger.info("Output folder %s does not exist yet; opening %s", target, probe)
        target = probe

    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # noqa: S606 - documented Windows API for this
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return True, target
    except Exception as e:
        logger.exception("Could not open folder %s", target)
        return False, f"Could not open folder:\n{target}\n\n{type(e).__name__}: {e}"
