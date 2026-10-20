"""project_memory.py -- each stage's folders remembered per project (user,
2026-10-02: picking a project in the title bar should put every stage back on
the chapter / folders it last used in THAT project).

The Shell keeps, per project folder, the chapter each stage last picked and the
folders each stage last had (studio.json "project_memory"). It asks the stages
for their folders just before it switches (projectLeaving) and, once switched
(projectChanged, after the stages' own refreshChapters), each stage is put back:
on its remembered chapter -- picked again, so its folders follow the chapter's
folders as they are now -- else on its remembered folders (Manual paths). A
project with nothing remembered for a stage leaves that stage as it is.
"""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)


def attach(ctl, stage: str, shell, keys: Sequence[str]) -> None:
    """Remember / restore `stage`'s path settings `keys` on `ctl` (a stage
    controller with refreshChapters / pickChapter / _chapters / _map) across
    project switches. Connect it AFTER ctl's own projectChanged ->
    refreshChapters, so the chapter list is the new project's when it runs."""
    keys = tuple(keys)
    if not hasattr(shell, "projectLeaving"):
        return              # a shell without project memory (the unit tests' stand-ins)

    def leaving(_old: str) -> None:
        try:
            shell.rememberStagePaths(stage, {k: ctl._map.value(k) for k in keys})
        except Exception:
            logger.debug("Could not remember %s's folders", stage, exc_info=True)

    def changed() -> None:
        try:
            saved = shell.stageChapter(stage)
            num = saved.get("num")
            nums = {c["num"] for c in (ctl._chapters or [])}
            if num is not None and (num in nums or (stage == "scenecut" and num == -2 and nums)):
                ctl._restoring = True              # no "Chapter selected" toast per stage
                try:
                    ctl.pickChapter(int(num))
                finally:
                    ctl._restoring = False
                return
            paths = shell.rememberedStagePaths(stage)
            if not paths:
                return
            ctl._chapter_applying = True           # not a hand edit
            try:
                for k in keys:
                    if k in paths and paths[k] is not None:
                        if hasattr(ctl, "_set"):
                            ctl._set(k, paths[k])
                        else:
                            ctl._map.insert(k, paths[k])
                            if hasattr(ctl, "_last_good"):
                                ctl._last_good[k] = paths[k]
            finally:
                ctl._chapter_applying = False
            timer = getattr(ctl, "_save_timer", None)
            if timer is not None:
                timer.start()
        except Exception:
            logger.debug("Could not restore %s's folders", stage, exc_info=True)

    shell.projectLeaving.connect(leaving)
    shell.projectChanged.connect(changed)
