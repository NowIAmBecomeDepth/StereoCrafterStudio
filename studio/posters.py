"""posters.py -- a project's poster from the cover art inside its video
(user, 2026-10-02).

The project picker's tile shows a picture lying in the project folder
(StudioShell._poster); without one, the cover art embedded in a video in the
project folder (or one folder down, stage folders aside): an MP4 / MKV picture
stream marked "attached picture", or an MKV image attachment. Only REAL cover
art: a picture that is portrait (taller than wide, as a poster is) or named /
titled cover, poster or front -- a frame grab saved as a thumbnail is
landscape and is skipped.

The cover is extracted once into <config>/posters/ and reused while the
video is unchanged (path, size, mtime); "none" is remembered the same way, so
the picker's popup only probes a video the first time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".mov")
COVER_WORDS = ("cover", "poster", "front")
#: A picture this much taller than wide counts as a poster.
PORTRAIT = 1.15

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_memo: Dict[str, str] = {}          # video key -> poster path or ""


def _cache_dir() -> str:
    from studio import settings_store
    d = os.path.join(str(settings_store.CONFIG_DIR), "posters")
    os.makedirs(d, exist_ok=True)
    return d


def _videos(project: str) -> List[str]:
    """Video files in the project folder, then one folder down (not the
    stage folders, whose clips are shots, or finished/)."""
    from core.ui import chapters
    out: List[str] = []

    def files(folder: str) -> List[str]:
        try:
            return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                          if n.lower().endswith(VIDEO_EXTS) and os.path.isfile(os.path.join(folder, n)))
        except OSError:
            return []

    out += files(project)
    stage = {os.path.normcase(p) for p in chapters.find_stage_dirs(project).values()}
    for name in chapters._subdirs(project):
        sub = os.path.join(project, name)
        low = name.lower()
        if os.path.normcase(sub.replace("\\", "/")) in stage or low == "finished" \
                or any(k in low for keys in chapters._KEYWORDS.values() for k in keys):
            continue
        out += files(sub)
    return out


def _probe(video: str) -> list:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=index,codec_type,width,height:stream_disposition=attached_pic:"
                        "stream_tags=filename,mimetype,title",
                        "-of", "json", video], capture_output=True, text=True, timeout=30,
                       creationflags=_NO_WINDOW)
    return json.loads(r.stdout or "{}").get("streams", [])


def _named_cover(tags: dict) -> bool:
    text = " ".join(str(tags.get(k, "")) for k in ("filename", "title")).lower()
    return any(w in text for w in COVER_WORDS)


def _is_poster(path: str, named: bool) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return False
    return named or h >= w * PORTRAIT


def _extract(video: str, out: str) -> bool:
    """The video's cover art into `out` (a .png), if it has REAL cover art."""
    for st in _probe(video):
        idx, kind, tags = st.get("index"), st.get("codec_type"), st.get("tags") or {}
        named = _named_cover(tags)
        if kind == "video" and (st.get("disposition") or {}).get("attached_pic"):
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
            if not named and not (w and h >= w * PORTRAIT):
                continue                                   # a frame grab, not a poster
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", video, "-map", f"0:{idx}", "-frames:v", "1", out]
        elif kind == "attachment" and str(tags.get("mimetype", "")).lower().startswith("image/"):
            raw = out + ".att"
            cmd = ["ffmpeg", "-v", "error", "-y", f"-dump_attachment:{idx}", raw, "-i", video]
            subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            try:
                from PIL import Image
                ok = os.path.isfile(raw) and _is_poster(raw, named)
                if ok:
                    with Image.open(raw) as im:
                        im.convert("RGB").save(out)
                if ok and os.path.isfile(out):
                    return True
                continue        # not a poster (a landscape still): the next stream may be (bug sweep A2)
            except Exception:
                continue
            finally:
                if os.path.exists(raw):
                    os.remove(raw)
        else:
            continue
        subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_NO_WINDOW)
        if os.path.isfile(out) and _is_poster(out, named):
            return True
        if os.path.exists(out):
            os.remove(out)
    return False


def video_cover(project: str) -> str:
    """A poster file from a video's embedded cover art, or ""."""
    for video in _videos(project):
        try:
            stat = os.stat(video)
        except OSError:
            continue
        key = hashlib.sha1(f"{os.path.normcase(video)}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:20]
        if key in _memo:
            if _memo[key]:
                return _memo[key]
            continue
        try:
            d = _cache_dir()
            out, none = os.path.join(d, key + ".png"), os.path.join(d, key + ".none")
            if os.path.isfile(out):
                found = out
            elif os.path.isfile(none):
                found = ""
            else:
                found = out if _extract(video, out) else ""
                if not found:
                    open(none, "w").close()
        except Exception:
            logger.debug("No cover art read from %s", video, exc_info=True)
            found = ""
        _memo[key] = found
        if found:
            return found
    return ""
