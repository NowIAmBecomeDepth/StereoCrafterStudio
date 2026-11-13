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

A FLAT unit (user, 2026-11-12: a tester's LaCasa/{input,depth,splat,inpaint,
merged} with the clips straight in the stage folders) has no chapter folders,
or clips lying directly in its Source folder. It gets one more entry, WHOLE:
the stage folders themselves. A unit may also name its own stage folders in
PROJECT_FILE (the Project Folders panel), which wins over name matching.
"""

from __future__ import annotations

import difflib
import json
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
#: Other names a stage folder goes by (user, 2026-11-12: "input" was not taken
#: for the Source folder). Weaker than the keywords: a name holding another
#: stage's keyword is never taken by an alias ("Merged Clips" is not Source).
_ALIASES = {
    "source": ("input", "original", "clips"),
    "merged": ("merge", "final", "output", "sbs"),
}
#: The merged folder's fallback: "LR" as a word of its own, so "SM Converted
#: LR Clips" matches and a name that merely contains the letters does not.
_LR_WORD = re.compile(r"(?<![a-z0-9])lr(?![a-z0-9])", re.IGNORECASE)

#: The chapter number of a flat unit's one entry: its stage folders themselves.
#: No real chapter is numbered this high, and it stays below the Shell's
#: per-episode key step (100000).
WHOLE = 99999
WHOLE_NAME = "Whole project"

#: A unit's own stage folders (the Project Folders panel): {"folders": {role:
#: path}}, a path relative to the unit when it lies inside it.
PROJECT_FILE = ".studio_project.json"

#: Clips (or frames) -- what tells a flat Source folder.
MEDIA_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mxf",
              ".png", ".exr", ".tif", ".tiff", ".jpg", ".jpeg"}

#: Overrides kept outside the unit (its folder not writable): {normcase unit
#: path: {role: path}}, filled by the Shell from its own settings. They win over
#: the unit's PROJECT_FILE.
_EXTRA_OVERRIDES: Dict[str, Dict[str, str]] = {}


def _ukey(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))


def set_extra_overrides(mapping: Dict[str, Dict[str, str]]) -> None:
    """Replace the overrides kept outside the units ({unit path: {role: path}})."""
    _EXTRA_OVERRIDES.clear()
    for unit, folders in (mapping or {}).items():
        if folders:
            _EXTRA_OVERRIDES[_ukey(unit)] = {r: str(p) for r, p in dict(folders).items() if r in ROLES and p}


def _resolve_override(unit: str, path: str) -> str:
    p = str(path).replace("\\", "/")
    if not os.path.isabs(p):
        p = _join(unit, p)
    return p.rstrip("/")


def read_override_file(unit: str) -> Dict[str, str]:
    """The unit's PROJECT_FILE folders, as stored ({} when none or unreadable)."""
    path = os.path.join(str(unit or ""), PROJECT_FILE)
    try:
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        folders = data.get("folders") if isinstance(data, dict) else None
        if not isinstance(folders, dict):
            return {}
        return {r: str(p) for r, p in folders.items() if r in ROLES and isinstance(p, str) and p.strip()}
    except Exception:
        return {}


def overrides(unit: str) -> Dict[str, str]:
    """{role: absolute folder} the unit names for itself. Never raises."""
    try:
        found = dict(read_override_file(unit))
        found.update(_EXTRA_OVERRIDES.get(_ukey(unit), {}))
        return {r: _resolve_override(unit, p) for r, p in found.items()}
    except Exception:
        return {}


def stored_override(unit: str, folder: str) -> str:
    """How an override is written: relative to the unit when inside it."""
    p = os.path.normpath(str(folder))
    u = os.path.normpath(str(unit))
    try:
        rel = os.path.relpath(p, u)
    except ValueError:                      # another drive
        rel = ""
    if rel and not rel.startswith("..") and not os.path.isabs(rel) and rel != ".":
        return rel.replace("\\", "/")
    return p.replace("\\", "/")


def write_override_file(unit: str, folders: Dict[str, str]) -> bool:
    """Write the unit's PROJECT_FILE ({role: path}, stored relative where it
    can be); no folders removes the file. False when it cannot be written."""
    path = os.path.join(str(unit or ""), PROJECT_FILE)
    try:
        clean = {r: stored_override(unit, _resolve_override(unit, p))
                 for r, p in (folders or {}).items() if r in ROLES and p}
        if not clean:
            if os.path.isfile(path):
                os.remove(path)
            return True
        if not os.path.isdir(unit):
            return False
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"folders": clean}, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def has_clips(path: str) -> bool:
    """Any clip (or frame) directly in `path`."""
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in MEDIA_EXTS:
                    return True
    except OSError:
        pass
    return False

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


def _word_start(low: str, key: str) -> bool:
    """`key` in `low` at the start of a word ("SM Splats", "DepthMaps")."""
    at = low.find(key)
    while at >= 0:
        if at == 0 or not low[at - 1].isalnum():
            return True
        at = low.find(key, at + 1)
    return False


def _rank(name: str, role: str) -> Optional[int]:
    """How well a folder name fits a role (lower is better), or None: a
    keyword at a word's start, a keyword anywhere, then the same for an alias.
    An alias never takes a name holding another stage's keyword, and Source's
    never takes the LR folder or a Merged alias ("SM Converted LR Clips")."""
    low = name.lower()
    hits = [k for k in _KEYWORDS[role] if k in low]
    if hits:
        return 0 if any(_word_start(low, k) for k in hits) else 1
    hits = [k for k in _ALIASES.get(role, ()) if k in low]
    if not hits:
        return None
    if any(k in low for other, keys in _KEYWORDS.items() if other != role for k in keys):
        return None
    if role == "source" and (_LR_WORD.search(name) or any(k in low for k in _ALIASES["merged"])):
        return None
    return 2 if any(_word_start(low, k) for k in hits) else 3


def find_stage_dirs(project: str) -> Dict[str, str]:
    """{role: folder} for each stage folder found in `project`.

    The unit's own choices (PROJECT_FILE) first -- an overridden role is that
    folder, or missing while it does not exist yet; then by name. Folders
    starting with "_" or "." are never stage folders (a "_inpaint_resume" was
    taken for Inpainted)."""
    pinned = overrides(project)
    names = [n for n in _subdirs(project) if not n.startswith(("_", "."))]
    found: Dict[str, str] = {r: p for r, p in pinned.items() if os.path.isdir(p)}
    taken = {os.path.normcase(p) for p in pinned.values()}
    for role in ROLES:
        if role in pinned:
            continue
        free = [n for n in names if os.path.normcase(_join(project, n)) not in taken]
        ranked = [(r, n) for n in free for r in [_rank(n, role)] if r is not None]
        if not ranked and role == "merged":
            ranked = [(4, n) for n in free if _LR_WORD.search(n)]
        # Never the same folder for two roles, and the shortest name when
        # several qualify: "SM Splats" over "SM Splats (old)".
        if ranked:
            best = min(ranked, key=lambda rn: (rn[0], len(rn[1])))[1]
            found[role] = _join(project, best)
            taken.add(os.path.normcase(found[role]))
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
        keys = _KEYWORDS[role] + _ALIASES.get(role, ())
        at = min((name.lower().find(k) for k in keys if k in name.lower()),
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
    found.update(overrides(project))        # an overridden folder, made or not
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


def is_flat(unit: str) -> bool:
    """A unit worked on whole: no chapter folders in any stage folder, or clips
    lying directly in its Source folder (or the finished/ below it). A unit
    with no stage folders yet counts: the stages make them when they run."""
    try:
        if not list_chapters(unit):
            return True
        src = find_stage_dirs(unit).get("source")
        return bool(src) and (has_clips(src) or has_clips(_join(src, "finished")))
    except Exception:
        return False


def list_entries(unit: str) -> List[Tuple[int, str]]:
    """list_chapters, with (WHOLE, WHOLE_NAME) first for a flat unit."""
    out = list_chapters(unit)
    return ([(WHOLE, WHOLE_NAME)] + out) if is_flat(unit) else out


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
    if num == WHOLE:                     # a flat unit: the stage folders themselves
        return dict(dirs)
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
    and the Import buttons place a folder by it).

    A stage folder itself -- or its hires / lowres / finished below it -- is
    the unit's WHOLE entry: (unit, WHOLE, "") (a flat unit, 2026-11-12)."""
    p = os.path.normpath(str(path or "")).replace("\\", "/").rstrip("/")
    plain = True                    # only hires / lowres / finished passed so far
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
        if plain and name.lower() not in _BELOW_STAGE:
            if not head.endswith(":") and is_unit(head):
                stages = {os.path.normcase(v) for v in find_stage_dirs(head).values()}
                if os.path.normcase(p) in stages:
                    return head, WHOLE, ""
            plain = False
        p = head
    return None


#: Folders below a stage folder that are still that stage's (locate_chapter).
_BELOW_STAGE = {"hires", "lowres", "finished"}


def stage_paths(stage: str, project: str, num: int, name_hint: str = "") -> Dict[str, str]:
    """{field role: path} for one stage's inputs and output.

    Field roles are each stage's own ("input", "output", "source", "depth",
    "lowres", "hires", "inpainted", "original", "mask"); a role is missing
    when the stage folder it comes from could not be found. `num` WHOLE: the
    stage folders themselves (a flat unit).
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
        # Inpainting V2 reads the hi-res splats (its record's "hires": Merging's Mask Folder)
        "inpainting_v2": {"hires": _join(splats, "hires") if splats else None, "output": inpainted},
        # Blend: Splatting's depth-map folder, where its blend goes too
        "blend": {"depth": depth},
        "merging": {
            "inpainted": inpainted,
            "original": fin(src, 2),
            "mask": fin(_join(splats, "hires"), 1) if splats else None,
            "output": merged,
        },
    }
    return {role: path for role, path in table.get(stage, {}).items() if path}
