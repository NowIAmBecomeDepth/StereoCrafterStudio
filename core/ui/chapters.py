"""chapters.py -- find a movie's chapter folders across the pipeline's stages.

A project is one parent folder holding a folder per stage, each of which holds
a folder per chapter:

    My Film/
        MF Source Clips/Chapter 37 - The Finale/       (Scene Cut out, DepthCrafter in)
        MF DepthMaps/Chapter 37 - The Finale/          (DepthCrafter out, Splatting in)
        MF Splats/Chapter 37 - The Finale/{hires,lowres}
        MF Inpainted/Chapter 37 - The Finale/
        MF Converted LR Clips/Chapter 37 - The Finale/ (Merging out)

A TV show nests that same layout once per episode (user, 2026-10-01):

    My Show/Season 1/Episode 1/
        1. S1E1 Source Clips/Chapter 1/
        2. S1E1 Depth Maps/ ... 5. S1E1 Merged/

find_units() finds each folder that holds stage folders (an "episode"; for a
movie, the project itself), and everything below works on one of those.

Stage folders are found by keyword, and chapters are matched by NUMBER, never
by name: the names drift from stage to stage ("Chapter 14- Goblin" beside
"Chapter 14 - Goblin", "Chapterr 36", "C2 SMBITEORIGIN"), and the number is
the one thing every copy agrees on. Folders with no chapter number in them
(CameraViewfinderScenes, _inpaint_resume, a stray finished/) are ignored.

The "finished" subfolders follow core.ui.job_handoff.resolve_subfolder: a
stage moves the files it has consumed one level down, so a later stage reads
from there -- but only once that folder exists.
"""

from __future__ import annotations

import difflib
import os
import re
from typing import Dict, List, Optional, Tuple

from core.ui.job_handoff import resolve_subfolder

#: The five stage folders, in pipeline order.
ROLES = ("source", "depth", "splats", "inpainted", "merged")

#: A stage folder's name contains one of these (case-insensitive).
_KEYWORDS = {
    "source": ("source",),
    "depth": ("depth",),
    "splats": ("splat",),
    "inpainted": ("inpaint",),
    "merged": ("merged",),
}
#: The merged folder's fallback: "LR" as a word of its own, so "SM Converted
#: LR Clips" matches and a name that merely contains the letters does not.
_LR_WORD = re.compile(r"(?<![a-z0-9])lr(?![a-z0-9])", re.IGNORECASE)

#: "Chapter 37 - ...", "Chapter 14- ...", "Chapterr 36", "Ch5", "C2 SMBITE...".
#: A C, optionally followed by letters starting with h, then the number.
#: "CameraGridFix" has letters but no h after the C, and no number, so it
#: never matches.
_CHAPTER = re.compile(r"^\s*c(?:h[a-z]*)?\s*[-_.]?\s*(\d+)", re.IGNORECASE)


def chapter_number(name: str) -> Optional[int]:
    """The chapter number in a folder name, or None if it is not a chapter."""
    match = _CHAPTER.match(str(name or ""))
    return int(match.group(1)) if match else None


def _subdirs(path: str) -> List[str]:
    try:
        return sorted(n for n in os.listdir(path) if os.path.isdir(os.path.join(path, n)))
    except OSError:
        return []


def _join(*parts: str) -> str:
    return "/".join(str(p).replace("\\", "/").rstrip("/") for p in parts if p)


def find_stage_dirs(project: str) -> Dict[str, str]:
    """{role: folder} for each stage folder found in `project`."""
    names = _subdirs(project)
    found: Dict[str, str] = {}
    for role in ROLES:
        hits = [n for n in names if any(k in n.lower() for k in _KEYWORDS[role])]
        if not hits and role == "merged":
            hits = [n for n in names if _LR_WORD.search(n)]
        # Never the same folder for two roles, and the shortest name when
        # several qualify: "SM Splats" over "SM Splats (old)".
        hits = [n for n in hits if _join(project, n) not in found.values()]
        if hits:
            found[role] = _join(project, min(hits, key=len))
    return found


#: What a stage folder is called when the project does not have one yet.
#: Each matches its own keyword above, so once created it is found like any
#: other.
DEFAULT_NAMES = {
    "source": "Source Clips",
    "depth": "DepthMaps",
    "splats": "Splats",
    "inpainted": "Inpainted",
    "merged": "Merged",
}


def _common_prefix(found: Dict[str, str]) -> str:
    """The prefix the existing stage folders share -- "SM " in "SM Splats" --
    so a folder created alongside them matches. Read off the words BEFORE
    each keyword; the LR folder is left out, its keyword being mid-name."""
    prefixes = []
    for role, path in found.items():
        name = path.rsplit("/", 1)[-1]
        at = min((name.lower().find(k) for k in _KEYWORDS[role] if k in name.lower()),
                 default=-1)
        if at >= 0:
            prefixes.append(name[:at])
    if not prefixes:
        return ""
    best = max(set(prefixes), key=prefixes.count)
    return best if prefixes.count(best) * 2 > len(prefixes) else ""


def stage_dirs(project: str) -> Dict[str, str]:
    """{role: folder} for all five stages: the ones found, and for the rest
    the folder each WOULD be.

    A new project is laid out by running it: nothing here creates a folder,
    but every stage creates its own output folder when it runs, and that
    folder is the next stage's input -- Scene Cut makes the chapter folders
    DepthCrafter reads, DepthCrafter the ones Splatting reads, and so on.
    """
    found = find_stage_dirs(project)
    prefix = _common_prefix(found)
    return {role: found.get(role) or _join(project, prefix + DEFAULT_NAMES[role])
            for role in ROLES}


def chapters_in(stage_dir: str) -> Dict[int, List[str]]:
    """{number: [folder names]} for the chapter folders in one stage folder."""
    out: Dict[int, List[str]] = {}
    for name in _subdirs(stage_dir):
        num = chapter_number(name)
        if num is not None:
            out.setdefault(num, []).append(name)
    return out


#: How far below the project find_units looks for stage folders: Season /
#: Episode is two levels; the margin is for a show grouped one level more.
UNIT_DEPTH = 4


def is_unit(path: str) -> bool:
    """A folder that holds stage folders: a Source folder, or two stages."""
    found = find_stage_dirs(path)
    return "source" in found or len(found) >= 2


def _natural(name: str):
    """'Episode 10' after 'Episode 2'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def find_units(project: str) -> List[str]:
    """The folders below `project` holding stage folders, as paths relative
    to it, in natural order: [""] when the project holds them itself (a
    movie), ["Season 1/Episode 1", ...] for a show. A unit's own folders are
    not searched further, nor are stage or "finished" folders."""
    if is_unit(project):
        return [""]
    out: List[str] = []

    def walk(rel: str, depth: int) -> None:
        if depth > UNIT_DEPTH:
            return
        here = _join(project, rel) if rel else project
        for name in sorted(_subdirs(here), key=_natural):
            if name.lower() == "finished" or name.startswith((".", "_")):
                continue
            sub_rel = _join(rel, name) if rel else name
            if is_unit(_join(project, sub_rel)):
                out.append(sub_rel)
            else:
                walk(sub_rel, depth + 1)

    walk("", 1)
    return out


_SEASON = re.compile(r"(?:season|series|^s)\s*(\d+)\s*$", re.IGNORECASE)
_EPISODE = re.compile(r"(?:episode|ep|^e)\s*(\d+)\s*$", re.IGNORECASE)


def is_show(project: str, depth: int = UNIT_DEPTH) -> bool:
    """A TV show: a folder with "episode" in its name somewhere inside it
    (user, 2026-10-02). Stage and "finished" folders are not searched."""
    def walk(folder: str, d: int) -> bool:
        if d > depth:
            return False
        for name in _subdirs(folder):
            low = name.lower()
            if "episode" in low:
                return True
            if low == "finished" or name.startswith((".", "_")):
                continue
            if any(k in low for keys in _KEYWORDS.values() for k in keys):
                continue                       # a stage folder: chapters, not episodes
            if walk(_join(folder, name), d + 1):
                return True
        return False
    return walk(project, 1)


def unit_label(rel: str) -> str:
    """A unit's short name: "S1E1" for Season 1/Episode 1, else its folders
    joined with " / "."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    season = next((m.group(1) for p in parts for m in [_SEASON.search(p.strip())] if m), None)
    episode = next((m.group(1) for p in parts for m in [_EPISODE.search(p.strip())] if m), None)
    if season and episode:
        return f"S{int(season)}E{int(episode)}"
    if episode and len(parts) == 1:
        return f"E{int(episode)}"
    return " / ".join(parts)


def list_chapters(project: str) -> List[Tuple[int, str]]:
    """Every chapter in the project as (number, name), in number order.

    The name is the Source folder's where there is one -- that is where a
    chapter starts -- and otherwise the first stage that has it.
    """
    dirs = find_stage_dirs(project)
    names: Dict[int, str] = {}
    for role in ROLES:
        if role not in dirs:
            continue
        for num, found in chapters_in(dirs[role]).items():
            names.setdefault(num, found[0])
    return sorted(names.items())


def _pick(candidates: List[str], ref: str) -> str:
    """The candidate closest to `ref`; LR holds two "Chapter 31" folders."""
    if len(candidates) == 1 or not ref:
        return candidates[0]
    return max(candidates, key=lambda n: difflib.SequenceMatcher(
        None, n.lower(), ref.lower()).ratio())


def chapter_dirs(project: str, num: int, name_hint: str = "") -> Dict[str, str]:
    """{role: this chapter's folder in that stage} for every stage folder.

    Where a stage has no folder for the chapter yet, the path it WOULD have
    -- the Source chapter's name inside that stage's folder -- so an output
    can be pointed at it and the stage creates it when it runs.
    """
    # Every stage, found or not: a missing stage folder is where that stage
    # will create its output (see stage_dirs).
    dirs = stage_dirs(project)
    found = {role: chapters_in(path).get(num, []) for role, path in dirs.items()}
    ref = (found.get("source") or [""])[0] or name_hint
    if not ref:
        ref = next((names[0] for names in found.values() if names), f"Chapter {num}")
    out = {}
    for role, path in dirs.items():
        names = found.get(role) or []
        out[role] = _join(path, _pick(names, ref) if names else ref)
    return out


def locate_chapter(path: str, levels: int = 6) -> Optional[Tuple[str, int, str]]:
    """Which project chapter `path` lies in: (the project -- or the episode,
    for a show -- its chapter number, the chapter folder's name), or None
    when it is not inside a project's stage folder. Any folder within the
    chapter counts: ".../SM Splats/Chapter 6 - Name/hires/finished" is chapter
    6 of the project holding "SM Splats" (user, 2026-10-02: the job handoff
    and the Import buttons place a folder by it)."""
    p = os.path.normpath(str(path or "")).replace("\\", "/").rstrip("/")
    for _ in range(levels):
        if not p or "/" not in p:
            return None
        head, name = p.rsplit("/", 1)
        num = chapter_number(name)
        if num is not None and "/" in head:
            unit = head.rsplit("/", 1)[0]
            stages = {os.path.normcase(v) for v in find_stage_dirs(unit).values()}
            if os.path.normcase(head) in stages:
                return unit, num, name
        p = head
    return None


def stage_paths(stage: str, project: str, num: int, name_hint: str = "") -> Dict[str, str]:
    """{field role: path} for one stage's inputs and output.

    Field roles are each stage's own ("input", "output", "source", "depth",
    "lowres", "hires", "inpainted", "original", "mask"); a role is missing
    when the stage folder it comes from could not be found.
    """
    if stage == "scenecut" and num < 0:
        # Whole movie: the clips are filed into chapter folders under Source.
        return {"output": stage_dirs(project)["source"]}
    ch = chapter_dirs(project, num, name_hint)
    src, depth, splats = ch.get("source"), ch.get("depth"), ch.get("splats")
    inpainted, merged = ch.get("inpainted"), ch.get("merged")

    def fin(path, levels=1):
        for _ in range(levels):
            path = resolve_subfolder(path, "finished") if path else path
        return path.replace("\\", "/") if path else path

    table = {
        "scenecut": {"output": src},
        "depthcrafter": {"input": src, "output": depth},
        "splatting": {"source": fin(src), "depth": depth, "output": splats},
        "inpainting": {
            "lowres": _join(splats, "lowres") if splats else None,
            "hires": _join(splats, "hires") if splats else None,
            "output": inpainted,
        },
        "merging": {
            "inpainted": inpainted,
            "original": fin(src, 2),
            "mask": fin(_join(splats, "hires"), 1) if splats else None,
            "output": merged,
        },
    }
    return {role: path for role, path in table.get(stage, {}).items() if path}
