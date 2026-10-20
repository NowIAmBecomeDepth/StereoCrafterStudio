"""movie_chapters.py -- split a whole movie's scenes into chapter folders.

Scene Cut's "Whole Movie (all chapters)" run detects scenes across the entire
film once, then files them by the chapter markers embedded in the movie (the
chapter list a Blu-ray or DVD rip carries), one folder per chapter under the
project's Source folder:

    SM Source Clips/Chapter 1 - .../   <- scenes that start in marker 1
    SM Source Clips/Chapter 2 - .../   <- ... in marker 2

Numbering follows the markers, the first one being Chapter 1. A chapter that
already has a Source folder keeps it (matched by number, see
core.ui.chapters); a new one is named "Chapter N", plus the marker's title
when it has a real one rather than a generic "Chapter 01".

A scene that runs across a chapter start is cut there, so no clip belongs to
two chapters. Kept out of core/scenecut/detection.py, which stays
diff-comparable with upstream SceneCutGUI.
"""

from __future__ import annotations

import bisect
import json
import re
import subprocess
import sys
from typing import Dict, List, Tuple

from core.ui import chapters as project_chapters


class MarkersUnreadable(RuntimeError):
    """ffprobe couldn't read the movie's chapters (missing, timed out, failed)."""


def read_markers(video_path: str, strict: bool = False) -> List[Tuple[float, float, str]]:
    """The movie's chapter markers as (start s, end s, title), in order.

    Empty when the file has none. When ffprobe could not read it: empty too,
    or with `strict` MarkersUnreadable, so "no markers" and "couldn't look"
    aren't the same answer (bug sweep 2026-10-09, A9).
    """
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", str(video_path)],
            capture_output=True, text=True, creationflags=flags, check=False, timeout=60)
        if strict and result.returncode != 0:
            raise MarkersUnreadable((result.stderr or "").strip()[-400:]
                                    or f"ffprobe exited with code {result.returncode}")
        data = json.loads(result.stdout or "{}")
    except subprocess.TimeoutExpired:
        if strict:
            raise MarkersUnreadable("ffprobe took over a minute to read them.")
        return []
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        if strict:
            raise MarkersUnreadable(str(err) or type(err).__name__)
        return []
    out = []
    for chapter in data.get("chapters") or []:
        try:
            start = float(chapter.get("start_time", 0.0))
            end = float(chapter.get("end_time", start))
        except (TypeError, ValueError):
            continue
        title = str((chapter.get("tags") or {}).get("title", "")).strip()
        out.append((start, end, title))
    return sorted(out)


#: Titles a disc gives every chapter, which say nothing: "Chapter 01",
#: "Chapter 1", "01", "00:04:12.000".
_GENERIC_TITLE = re.compile(
    r"^\s*(?:(?:chapter|ch|kapitel|chapitre|cap[ií]tulo)\s*[-_.#]?\s*)?\d+\s*$"
    r"|^\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\s*$",
    re.IGNORECASE)
#: Not allowed in a Windows folder name.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def real_title(title: str) -> str:
    """The marker's title if it names something, else ""."""
    return "" if not title or _GENERIC_TITLE.match(title) else title


def folder_for(num: int, title: str, source_dir: str) -> str:
    """Where chapter `num`'s clips go: its existing Source folder, or a new one."""
    existing = project_chapters.chapters_in(source_dir).get(num)
    base = str(source_dir).replace("\\", "/").rstrip("/")
    if existing:
        return f"{base}/{project_chapters._pick(existing, title)}"
    clean = _BAD_CHARS.sub("", real_title(title)).strip().rstrip(". ")
    return f"{base}/Chapter {num}" + (f" - {clean}" if clean else "")


def group_scenes(scenes: List[Tuple[int, int]],
                 chapter_starts: List[int]) -> Dict[int, List[Tuple[int, int]]]:
    """{chapter number (1-based): [(start, end) frames]} for every scene.

    Scenes are (start, end) with an EXCLUSIVE end, as Scene Cut keeps them.
    One that spans a chapter start is cut at it. Frames before the first
    marker, if any, go to chapter 1.
    """
    starts = sorted(chapter_starts)
    cuts = [f for f in starts if f > 0]
    pieces: List[Tuple[int, int]] = []
    for start, end in scenes:
        for cut in cuts:
            if start < cut < end:
                pieces.append((start, cut))
                start = cut
        pieces.append((start, end))
    groups: Dict[int, List[Tuple[int, int]]] = {}
    for start, end in pieces:
        num = max(1, bisect.bisect_right(starts, start))
        groups.setdefault(num, []).append((start, end))
    return groups
