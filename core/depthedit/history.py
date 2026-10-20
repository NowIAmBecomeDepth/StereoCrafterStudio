"""history.py -- Undo / Redo for the Depth Map Editor (user, 2026-10-05).

Each edit records the project as it was just before it (Project.snapshot:
cheap -- the masks share their packed arrays), with the selected layer and
SAM 3's cached results, so Undo puts back exactly what was there. Only an
edit that really changed something leaves a step (fingerprint): a question
answered No (Delete layer?) leaves none. A run of changes to one thing -- a
dial dragged, a layer name typed -- is one step while each change follows the
last within COALESCE_S.

Steps are capped by count (LIMIT) and by the memory only they hold
(BUDGET_BYTES): a re-track replaces every frame's masks, and the step before
it keeps the old ones -- hundreds of MB a re-track on a long clip (bug sweep
2026-10-09, A9). Arrays the open project still uses aren't counted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Steps kept; the oldest go first.
LIMIT = 100
#: A run of changes to one thing within this many seconds of each other is one step.
COALESCE_S = 1.2
#: Memory the steps may hold that the open project doesn't; the oldest go first.
BUDGET_BYTES = 1536 * 2 ** 20


@dataclass
class State:
    label: str
    project: Any                 # a Project.snapshot()
    selected: Optional[int]
    results: Dict


def fingerprint(project, selected: Optional[int] = None) -> Tuple:
    """What an edit can change, cheaply comparable: two equal fingerprints
    mean nothing an Undo would put back has changed. The selection is left
    out -- picking a layer is not an edit."""
    if project is None:
        return ()
    layers = []
    for ly in project.layers:
        masks = getattr(ly, "masks", None)
        hi = getattr(ly, "masks_hi", None)
        keys = getattr(ly, "keys", None)
        layers.append((
            ly.uid, ly.name, ly.enabled, ly.source, ly.colour, ly.text, ly.text_frame, ly.tracked,
            repr(ly.settings),
            tuple((c.frame, c.obj_id, c.x, c.y, c.label) for c in ly.clicks),
            tuple((c.frame, c.obj_id, c.x, c.y, c.label) for c in getattr(ly, "patch_clicks", ())),
            repr(keys.to_list()) if keys is not None else "",     # with the shaper's curves
            repr(getattr(ly, "shapes", None) or []),
            # the stores' packed arrays are replaced, never changed in place: their ids say it
            tuple((f, id(a)) for f, a in sorted(masks._frames.items())) if masks is not None else None,
            tuple((f, id(a)) for f, a in sorted(hi._frames.items())) if hi is not None else None,
        ))
    return (repr(project.global_settings), tuple(layers))


def _arrays(project, results) -> Dict[int, int]:
    """id -> nbytes of the big arrays a state holds: its layers' packed masks
    (both stores) and SAM 3's packed results. The stores and results share
    arrays between states, so ids are what's counted, once."""
    out: Dict[int, int] = {}
    for ly in getattr(project, "layers", None) or ():
        for store in (getattr(ly, "masks", None), getattr(ly, "masks_hi", None)):
            for a in (getattr(store, "_frames", None) or {}).values():
                out[id(a)] = int(getattr(a, "nbytes", 0))
    for r in (results or {}).values():
        try:
            bits = r[1].bits if hasattr(r[1], "bits") else r[1]
            out[id(bits)] = int(getattr(bits, "nbytes", 0))
        except (IndexError, TypeError):
            pass
    return out


class History:
    def __init__(self, limit: int = LIMIT, clock: Callable[[], float] = time.monotonic,
                 budget: int = BUDGET_BYTES) -> None:
        self.limit = limit
        self.budget = budget
        self._clock = clock
        self._undo: List[State] = []
        self._redo: List[State] = []
        self._run_key: Optional[str] = None
        self._run_at = 0.0

    # -- recording ---------------------------------------------------------

    def push(self, state: State, coalesce: Optional[str] = None, live: Optional[Tuple] = None) -> bool:
        """`state` is the project before an edit that changed it. With
        `coalesce`, an edit to the same thing right after the last one adds
        no step: the step before the run already holds what Undo restores.
        `live`: (project, results) open now, whose arrays the budget doesn't
        count. Returns whether a step was added."""
        now = self._clock()
        joined = (coalesce is not None and coalesce == self._run_key
                  and now - self._run_at <= COALESCE_S and bool(self._undo))
        self._run_key, self._run_at = coalesce, now
        self._redo.clear()
        if joined:
            return False
        self._undo.append(state)
        del self._undo[:-self.limit]
        self._trim(live)
        return True

    def held_bytes(self, live: Optional[Tuple] = None) -> int:
        """Memory the steps hold that `live` doesn't."""
        return sum(self._held(live)[0].values())

    def _held(self, live):
        skip = set(_arrays(*live)) if live else set()
        sizes: Dict[int, int] = {}
        counts: Dict[int, int] = {}
        per_state = []
        for st in self._undo + self._redo:
            arrs = {k: v for k, v in _arrays(st.project, st.results).items() if k not in skip}
            per_state.append(arrs)
            for k, v in arrs.items():
                sizes[k] = v
                counts[k] = counts.get(k, 0) + 1
        return sizes, counts, per_state

    def _trim(self, live) -> None:
        """Drop the oldest steps while they hold more than the budget (the
        newest step always stays)."""
        sizes, counts, per_state = self._held(live)
        total = sum(sizes.values())
        dropped = 0
        while total > self.budget and len(self._undo) - dropped > 1:
            for k in per_state[dropped]:
                counts[k] -= 1
                if counts[k] == 0:
                    total -= sizes[k]
            dropped += 1
        if dropped:
            del self._undo[:dropped]

    def break_run(self) -> None:
        """The next edit starts a step of its own, however soon it comes."""
        self._run_key = None

    # -- moving ------------------------------------------------------------

    def undo(self, current: State) -> Optional[State]:
        """The state to restore, `current` kept for Redo; None if nothing to undo."""
        if not self._undo:
            return None
        state = self._undo.pop()
        current.label = state.label
        self._redo.append(current)
        self.break_run()
        return state

    def redo(self, current: State) -> Optional[State]:
        if not self._redo:
            return None
        state = self._redo.pop()
        current.label = state.label
        self._undo.append(current)
        self.break_run()
        return state

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self.break_run()

    # -- what the buttons show ---------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""
