"""project_memory.py -- each stage's folders remembered per project (user,
2026-10-02: picking a project in the title bar should put every stage back on
the chapter / folders it last used in THAT project).

The Shell keeps, per project folder, the chapter each stage last picked and the
folders each stage last had (studio.json "project_memory"). It asks the stages
for their folders just before it switches (projectLeaving) and, once switched
(projectChanged, after the stages' own refreshChapters), each stage lands
(2026-11-12: a tester's flat project kept another project's folders):

 1. its own chapter, when the project still has it -- picked again, quietly;
 2. its own folders, unless one of them lies in a DIFFERENT project (another
    project Studio knows: Shell.foreignProject). A folder outside every
    project (a scratch drive) is a fine manual path and is kept;
 3. nothing of its own there: the chapter its pipeline neighbours are on in
    this project -- the nearest earlier stage, earlier ones, then later ones;
 4. the project's one entry (Whole project, or a single chapter);
 5. else no chapter: its project folders emptied, needsChapter on, and Start
    refused until a chapter is picked or a folder set.

Each landing is told to the Shell (noteLanding), which sums the switch up in
one line (projectLanded). Folders from another project are never remembered
as the one being left. The Import buttons fill a whole stage from one chapter
of the open project (import_plan). Inpainting V2 and Blend land the same way
but are side pages (SIDE_STAGES): nothing follows them.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Each stage's place in the pipeline (rule 3). VDA and DVD sit with DepthCrafter.
POSITION = {"scenecut": 0, "depthcrafter": 1, "vda": 1, "dvd": 1, "blend": 1, "splatting": 2,
            "inpainting": 3, "inpainting_v2": 3, "merging": 4, "edgefill": 5}
STAGE_LABELS = {"scenecut": "Scene Cut", "depthcrafter": "DepthCrafter", "vda": "VDA", "dvd": "DVD",
                "blend": "Blend", "splatting": "Splatting", "inpainting": "Inpainting",
                "inpainting_v2": "Inpainting V2", "merging": "Merging", "edgefill": "Edge Fill"}
#: Side pages (2026-11-12): they land and take a chapter for all like any stage, but no stage
#: follows them, the title bar's chip leaves them out (a page never opened is no "Mixed"), and
#: the switch's summary names them only when they need a chapter.
SIDE_STAGES = frozenset({"inpainting_v2", "blend"})
#: Scene Cut's Whole Movie: not a chapter another stage follows.
WHOLE_MOVIE = -2

#: An Import's folders, as its tooltip names them.
ROLE_LABELS = {"input": "Input", "output": "Output", "source": "Source clips", "depth": "Depth maps",
               "lowres": "Lo-res splats", "hires": "Hi-res splats", "inpainted": "Inpainted",
               "original": "Original", "mask": "Mask"}


def label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def neighbours(stage: str) -> List[str]:
    """The stages `stage` follows, nearest first: its own position (VDA beside
    DepthCrafter), the earlier ones from the nearest back, then the later ones.
    Never a side page (SIDE_STAGES)."""
    pos = POSITION.get(stage)
    if pos is None:
        return []
    others = [s for s in POSITION if s != stage and s not in SIDE_STAGES]
    same = [s for s in others if POSITION[s] == pos]
    earlier = sorted((s for s in others if POSITION[s] < pos), key=lambda s: -POSITION[s])
    later = sorted((s for s in others if POSITION[s] > pos), key=lambda s: POSITION[s])
    return same + earlier + later


def foreign(shell, path) -> str:
    """The other project `path` lies in ("" when none, or the shell can't tell)."""
    fn = getattr(shell, "foreignProject", None)
    if fn is None or not str(path or "").strip():
        return ""
    try:
        return str(fn(str(path)) or "")
    except Exception:
        return ""


def _entries(ctl) -> Dict[int, str]:
    return {c["num"]: str(c.get("name", "")) for c in (getattr(ctl, "_chapters", None) or [])}


def _pick(ctl, num: int) -> None:
    was = getattr(ctl, "_restoring", False)
    ctl._restoring = True                  # quietly: no "Chapter selected" toast
    try:
        ctl.pickChapter(int(num))
    finally:
        ctl._restoring = was


def _put(ctl, key: str, value) -> None:
    if hasattr(ctl, "_set"):
        ctl._set(key, value)
    else:
        ctl._map.insert(key, value)
        if hasattr(ctl, "_last_good"):
            ctl._last_good[key] = value


def _put_all(ctl, keys: Sequence[str], paths: dict) -> None:
    ctl._chapter_applying = True           # not a hand edit
    try:
        for k in keys:
            if k in paths and paths[k] is not None:
                _put(ctl, k, paths[k])
    finally:
        ctl._chapter_applying = False
    timer = getattr(ctl, "_save_timer", None)
    if timer is not None:
        timer.start()


# ---- needs a chapter ---------------------------------------------------------------------------

def _keys(ctl) -> Tuple[str, ...]:
    info = getattr(ctl, "_landing_info", None)
    return tuple(info[1]) if info else ()


def needs_chapter(ctl) -> bool:
    """The stage is on the project with no chapter and no folders (rule 5) --
    until a chapter is picked or any of its folders is set, by hand or not."""
    if not getattr(ctl, "_needs_chapter", False):
        return False
    filled = getattr(ctl, "_chapter_num", -1) != -1 or any(
        str(ctl._map.value(k) or "").strip() for k in _keys(ctl))
    if filled:
        _set_needs(ctl, False)
        return False
    return True


def _set_needs(ctl, on: bool) -> None:
    if bool(getattr(ctl, "_needs_chapter", False)) != bool(on):
        ctl._needs_chapter = bool(on)
        sig = getattr(ctl, "chapterChanged", None)
        if sig is not None:
            sig.emit()


def refuse_start(ctl) -> bool:
    """True (and says so) when the stage has no chapter in the open project yet."""
    if not needs_chapter(ctl):
        return False
    ctl.toast.emit("warning", "Pick a chapter first",
                   "This stage has no folders in this project yet. Pick a chapter, or set its folders.")
    return True


# ---- landing -----------------------------------------------------------------------------------

def land(ctl, stage: str, shell, keys: Sequence[str], switching: bool = True) -> Tuple[str, str]:
    """Put `ctl` on the open project by the rules in the module doc. (kind, detail):
    kind "chapter" / "folders" / "followed" / "only" / "needs" / "kept", or "" with
    no project open. `switching`: a project switch (rule 2 restores remembered
    folders); otherwise (Use the project's folder) the stage's own chapter, else
    rules 3-5."""
    if not getattr(shell, "projectFolder", ""):
        return "", ""
    entries = _entries(ctl)
    saved = shell.stageChapter(stage) or {}
    num = saved.get("num")
    if not switching and getattr(ctl, "_chapter_num", -1) != -1:
        num = ctl._chapter_num
    # 1. its own chapter
    if num is not None and (num in entries or (stage == "scenecut" and num == WHOLE_MOVIE and entries)):
        _pick(ctl, num)
        _set_needs(ctl, False)
        return "chapter", entries.get(num) or str(saved.get("name", ""))
    # 2. its own folders -- none of them another project's
    if switching:
        paths = {k: v for k, v in (shell.rememberedStagePaths(stage) or {}).items() if k in keys}
        own = [v for v in paths.values() if str(v or "").strip()]
        if own and not any(foreign(shell, v) for v in own):
            _put_all(ctl, keys, paths)
            _set_needs(ctl, False)
            return "folders", ""
    if stage == "scenecut":
        # its refreshChapters put it on Whole Movie where the project has one; else its
        # folder stays, unless another project's
        if any(foreign(shell, ctl._map.value(k)) for k in keys):
            return _no_chapter(ctl, keys)
        return "kept", ""
    # 3. the chapter its neighbours are on here (as the switch found them)
    look = getattr(shell, "landingChapter", None) or shell.stageChapter
    for other in neighbours(stage):
        n = (look(other) or {}).get("num")
        if isinstance(n, int) and n >= 0 and n in entries:
            _pick(ctl, n)
            _set_needs(ctl, False)
            return "followed", f"{label(other)}: {entries[n]}"
    # 4. the project's one entry
    if len(entries) == 1:
        n = next(iter(entries))
        _pick(ctl, n)
        _set_needs(ctl, False)
        return "only", entries[n]
    # 5. no chapter yet
    return _no_chapter(ctl, keys)


def _no_chapter(ctl, keys: Sequence[str]) -> Tuple[str, str]:
    _put_all(ctl, keys, {k: "" for k in keys})
    ctl._chapter_num = -1
    ctl._chapter_expected = {}
    ctl._needs_chapter = False             # so _set_needs emits
    _set_needs(ctl, True)
    return "needs", ""


def reland(ctl) -> Tuple[str, str]:
    """A folder field's "Use <project>'s folder": the stage back on the open
    project -- its chapter, else its neighbours', the one entry, or none."""
    info = getattr(ctl, "_landing_info", None)
    if not info:
        return "", ""
    stage, keys, shell = info
    try:
        return land(ctl, stage, shell, keys, switching=False)
    except Exception:
        logger.debug("Could not put %s back on the project", stage, exc_info=True)
        return "", ""


def summary(name: str, notes: Sequence[Tuple[str, str, str]]) -> str:
    """One line on a switch's landings: "LaCasa: 4 stages on Whole project,
    Inpainting following Splatting: Chapter 6, Merging kept its own folders"."""
    on: Dict[str, List[str]] = {}
    followed, folders, needs = [], [], []
    for stage, kind, detail in sorted(notes, key=lambda n: POSITION.get(n[0], 99)):   # pipeline order
        lab = label(stage)
        if stage in SIDE_STAGES and kind != "needs":
            continue                        # a side page is named only when it needs a chapter
        if kind in ("chapter", "only"):
            on.setdefault(detail or "its chapter", []).append(lab)
        elif kind == "followed":
            followed.append(f"{lab} following {detail}")
        elif kind == "folders":
            folders.append(lab)
        elif kind == "needs":
            needs.append(lab)
    parts = [f"{stages[0] if len(stages) == 1 else f'{len(stages)} stages'} on {chapter}"
             for chapter, stages in on.items()]
    parts += followed
    if folders:
        parts.append(f"{_and(folders)} kept {'its' if len(folders) == 1 else 'their'} own folders")
    if needs:
        parts.append(f"{_and(needs)} {'needs' if len(needs) == 1 else 'need'} a chapter")
    return f"{name}: " + ", ".join(parts) if parts else ""


def _and(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# ---- Import buttons ----------------------------------------------------------------------------

def import_plan(ctl, shell, path_stage: str, record: Optional[dict], record_keys: Sequence[str],
                upstream: Sequence[str]) -> Optional[dict]:
    """With a project open: the one chapter an Import fills `ctl` from, or None.
    The previous stage's record when it lies in a chapter of the open project,
    else that stage's saved chapter here (`upstream`, in order), else the
    project's one entry -- offered only when its input folders are on disk.
    {num, name, paths, fromRecord, description}; `description` names each
    folder the Import fills (inputs and output)."""
    if not getattr(shell, "projectFolder", ""):
        return None
    entries = _entries(ctl)
    if not entries:
        return None
    key, from_record = None, False
    at = getattr(shell, "chapterAt", None)
    if record and at is not None:
        for k in record_keys:
            value = record.get(k)
            if value:
                n = at(str(value))
                if n in entries:
                    key, from_record = n, True
                    break
    if key is None:
        for st in upstream:
            n = (shell.stageChapter(st) or {}).get("num")
            if isinstance(n, int) and n >= 0 and n in entries:
                key = n
                break
    if key is None and len(entries) == 1:
        key = next(iter(entries))
    if key is None:
        return None
    from studio.chapter_paths import INPUT_ROLES
    paths = dict(shell.stagePaths(path_stage, key, entries[key]) or {})
    inputs = INPUT_ROLES.get(path_stage, ())
    if not inputs or not all(paths.get(r) and os.path.isdir(paths[r]) for r in inputs):
        return None
    lines = [f"{ROLE_LABELS.get(r, r)}: {paths[r]}" for r in (*inputs, "output") if paths.get(r)]
    return {"num": key, "name": entries[key], "paths": paths, "fromRecord": from_record,
            "description": "\n".join(lines)}


def apply_import(ctl, plan: dict) -> str:
    """Put `ctl` on the plan's chapter (quietly, the way the picker would);
    the folders it filled, for the "Imported ..." status line."""
    _pick(ctl, plan["num"])
    roles = [ROLE_LABELS.get(r, r).lower() for r in plan.get("paths", {})]
    return ", ".join(roles)


# ---- wiring ------------------------------------------------------------------------------------

def attach(ctl, stage: str, shell, keys: Sequence[str]) -> None:
    """Remember / restore `stage`'s path settings `keys` on `ctl` (a stage
    controller with refreshChapters / pickChapter / _chapters / _map) across
    project switches. Connect it AFTER ctl's own projectChanged ->
    refreshChapters, so the chapter list is the new project's when it runs."""
    keys = tuple(keys)
    ctl._landing_info = (stage, keys, shell)
    if not hasattr(ctl, "_needs_chapter"):
        ctl._needs_chapter = False
    if not hasattr(shell, "projectLeaving"):
        return              # a shell without project memory (the unit tests' stand-ins)
    if hasattr(shell, "registerChapterStage") and stage not in SIDE_STAGES:
        shell.registerChapterStage(stage)   # the chip's stages (Shell.commonChapter)

    def leaving(_old: str) -> None:
        try:
            # another project's folders are not this one's (they poisoned the memory)
            shell.rememberStagePaths(stage, {k: ctl._map.value(k) for k in keys
                                             if not foreign(shell, ctl._map.value(k))})
        except Exception:
            logger.debug("Could not remember %s's folders", stage, exc_info=True)

    def changed() -> None:
        try:
            kind, detail = land(ctl, stage, shell, keys)
            if kind and hasattr(shell, "noteLanding"):
                shell.noteLanding(stage, kind, detail)
        except Exception:
            logger.debug("Could not restore %s's folders", stage, exc_info=True)

    def for_all(num: int) -> None:
        if stage == "scenecut":
            return                          # Scene Cut takes the whole movie
        try:
            if int(num) in _entries(ctl):
                _pick(ctl, int(num))
        except Exception:
            logger.debug("Could not put %s on chapter %s", stage, num, exc_info=True)

    def refreshed() -> None:
        """The project's folders changed (not a switch): look again."""
        try:
            ctl.refreshChapters()
            if getattr(ctl, "_chapter_num", -1) != -1:
                _pick(ctl, ctl._chapter_num)
            elif getattr(ctl, "_needs_chapter", False):
                land(ctl, stage, shell, keys, switching=False)
            _refresh_imports()
        except Exception:
            logger.debug("Could not refresh %s's folders", stage, exc_info=True)

    def _refresh_imports() -> None:
        for name in ("refreshImport", "refreshSceneCut"):
            fn = getattr(ctl, name, None)
            if fn is not None:
                fn()

    def value_changed(key: str, _value) -> None:
        if key in keys:
            needs_chapter(ctl)

    shell.projectLeaving.connect(leaving)
    shell.projectChanged.connect(changed)
    for sig, slot in (("chapterForAll", for_all), ("projectRefreshed", refreshed),
                      ("projectLanded", lambda _text: _refresh_imports())):
        if hasattr(shell, sig):
            getattr(shell, sig).connect(slot)
    if hasattr(ctl, "chapterChanged"):
        ctl.chapterChanged.connect(lambda: needs_chapter(ctl))
    try:
        ctl._map.valueChanged.connect(value_changed)
    except Exception:
        pass
