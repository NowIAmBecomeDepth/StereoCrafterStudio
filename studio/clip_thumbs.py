"""clip_thumbs.py -- a small still of a clip, for lists (Merging's Upcoming Jobs).

``image://clipthumb/<url-encoded path>``: one frame from 40% into the clip (past
a black lead-in), scaled to THUMB_HEIGHT (72 px: the row's 36-px box at up to
200%), cached by path and modification time.
QML asks with ``asynchronous: true``, so Qt calls requestImage on its own
loader thread: nothing here runs on the UI thread, and a list is only ever
asked for its thumbnails when its rows are made -- not per frame of a job.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Optional, Tuple
from urllib.parse import unquote

from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

logger = logging.getLogger("studio.clip_thumbs")

THUMB_HEIGHT = 72
#: How far into the clip the still is taken (a fade-in's first frames are black).
POSITION = 0.4
CACHE_SIZE = 400


def _blank() -> QImage:
    image = QImage(2, 1, QImage.Format.Format_ARGB32)
    image.fill(0)
    return image


def _grab_cv2(path: str, height: int, frame_no: int = -1, part: str = "whole") -> Optional[QImage]:
    """cv2 seeks to the frame (decoding from the keyframe before it, at full
    size -- there is no smaller decode) and scales it down. Measured against
    ffmpeg decoding only that keyframe, scaled in the decoder (2026-09-27,
    1080p): 120 vs 111 ms a clip, the process start eating the saving -- so
    not worth a process per row."""
    import cv2
    cap = cv2.VideoCapture(path)
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_no >= 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(frame_no, max(0, n - 1)) if n > 0 else frame_no)
        elif n > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * POSITION))
        ok, frame = cap.read()
        if not ok and n > 1:                  # some files seek badly: take the first frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        return None
    if part in ("left", "right"):                 # one eye of a side-by-side
        half = frame.shape[1] // 2
        frame = frame[:, :half] if part == "left" else frame[:, half:2 * half]
    h, w = frame.shape[:2]
    width = max(1, int(round(w * height / float(h))))
    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return QImage(frame.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


def grab(path: str, height: int = THUMB_HEIGHT, frame_no: int = -1, part: str = "whole") -> Optional[QImage]:
    """One frame of `path` (`frame_no`, or 40% in), `height` tall -- the whole frame, or the
    "left" / "right" half of a side-by-side -- or None when it cannot be read."""
    try:
        return _grab_cv2(path, height, frame_no, part)
    except Exception:
        logger.debug("No thumbnail for %s", path, exc_info=True)
        return None


class ClipThumbProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache: "OrderedDict[Tuple[str, float], QImage]" = OrderedDict()
        self._last: "OrderedDict[str, QImage]" = OrderedDict()   # by path, whatever its time
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        path = unquote(str(image_id))
        asked = os.path.normcase(os.path.abspath(path))
        # A clip the batch has moved to finished/ is read there, and one gone
        # altogether keeps the picture it had (audit 2026-09-28, minor: its
        # row turned blank when it was made again).
        if not os.path.exists(path):
            moved = os.path.join(os.path.dirname(path), "finished", os.path.basename(path))
            if os.path.isfile(moved):
                path = moved
        try:
            key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
        except OSError:
            key = None
        image = None
        if key is not None:
            with self._lock:
                image = self._cache.get(key)
                if image is not None:
                    self._cache.move_to_end(key)
            if image is None:
                image = grab(path)
                if image is not None:
                    with self._lock:
                        self._cache[key] = image
                        while len(self._cache) > CACHE_SIZE:
                            self._cache.popitem(last=False)
        with self._lock:
            if image is not None:
                self._last[asked] = image
                self._last.move_to_end(asked)
                while len(self._last) > CACHE_SIZE:
                    self._last.popitem(last=False)
            else:
                image = self._last.get(asked)
        if image is None:
            image = _blank()
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


EDGE_HEIGHT = 324      # Fill Edges' tiles: up to 420 px wide (the size slider), sharp at 150%


class EdgeThumbProvider(QQuickImageProvider):
    """``image://edgethumb/<frame>/<whole|left|right>[/<version>]/<url-encoded path>``: one eye of a merged
    clip at the frame given (-1: 40% in), for Fill Edges' grid (user, 2026-10-14: see which
    clips have the dark strip). Cached by path, modification time, frame and half."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache: "OrderedDict[tuple, QImage]" = OrderedDict()
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        image = None
        try:
            # the version (the file's mtime) is only there so a rewritten clip gets a new URL:
            # Qt caches stills by URL (bug sweep 2026-10-14); the cache here keys on the mtime itself
            frame_s, part, rest = str(image_id).split("/", 2)
            ver, _sep, tail = rest.partition("/")
            enc = tail if _sep and ver.isdigit() else rest
            path = unquote(enc)
            key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path), int(frame_s), part)
        except (ValueError, OSError):
            key = None
        if key is not None:
            with self._lock:
                image = self._cache.get(key)
            if image is None:
                image = grab(path, EDGE_HEIGHT, key[2], key[3])
                if image is not None:
                    with self._lock:
                        self._cache[key] = image
                        while len(self._cache) > CACHE_SIZE // 4:
                            self._cache.popitem(last=False)
        if image is None:
            image = _blank()
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image
