"""image_io.py -- OpenCV's imwrite / imread for any path, Cyrillic, CJK or accents included.

On Windows OpenCV opens files through the ANSI code page: a path outside it fails -- imwrite returns
False, imread None -- with no reason given. A user's Inpainting of "Противостояние (2001)_1360_
splatted4.mp4" ran three hours, then could not write the first hi-res PNG into its temp folder, which
is named after the clip, and threw the clip away (2026-10-24: "Failed to write PNG frame 0").

``install()`` (studio.app, at start) replaces cv2.imwrite / cv2.imread for the whole process: a path
that isn't plain ASCII is encoded / decoded in memory and written / read through Python's own file I/O,
which takes any path. ASCII paths go to OpenCV's own functions as before.
"""

from __future__ import annotations

import os

_installed = False
_imwrite = None
_imread = None


def _ascii(path) -> bool:
    try:
        os.fspath(path).encode("ascii")
        return True
    except (UnicodeEncodeError, TypeError):
        return False


def imwrite(path, img, params=None) -> bool:
    import cv2
    if _ascii(path):
        return bool((_imwrite or cv2.imwrite)(path, img, params) if params is not None
                    else (_imwrite or cv2.imwrite)(path, img))
    path = os.fspath(path)
    ext = os.path.splitext(path)[1] or ".png"
    try:
        ok, buf = cv2.imencode(ext, img, params if params is not None else [])
        if not ok:
            return False
        buf.tofile(path)
        return True
    except (OSError, cv2.error):
        return False


def imread(path, flags=None):
    import cv2
    import numpy as np
    flags = cv2.IMREAD_COLOR if flags is None else flags
    if _ascii(path):
        return (_imread or cv2.imread)(path, flags)
    try:
        data = np.fromfile(os.fspath(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


def install() -> bool:
    """cv2.imwrite / cv2.imread take any path from here on (the whole process). True when it did."""
    global _installed, _imwrite, _imread
    if _installed:
        return True
    try:
        import cv2
    except Exception:
        return False
    _imwrite, _imread = cv2.imwrite, cv2.imread
    cv2.imwrite, cv2.imread = imwrite, imread
    _installed = True
    return True
