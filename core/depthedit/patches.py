"""patches.py -- adding to a layer after it has been tracked (user, 2026-10-05).

"I want to add to the tracking after running the tracker once, but when I
try to fill in the gaps it deselects what was tracked and tracks what I
clicked." The cause is SAM 3's: a click can't join a text search's session
(it breaks tracking of every object -- Sam3Engine.replay), so a click on a
text layer turned the layer into mask-seeded tracking from the text frame,
much weaker than the text search finding the object on every frame.

So once a layer is tracked, an Add click or a box OUTSIDE its mask starts a
PATCH: the patch's clicks live apart from the layer's own prompts
(Layer.patch_clicks), are segmented and tracked in a click-only session of
their own (those track well), and what they find is ADDED to the layer's
masks -- never put in their place. A click inside a patch's mask refines that
patch (Remove included). Start Tracking with a patch pending tracks only the
patch; a full re-track of the layer tracks the patches again after it.

The engine calls in here from a few hooks (studio/tools/gen_depthedit_engine
PATCHES); `eng` is the editor's engine. Worker-thread functions say so.
"""

from __future__ import annotations

from typing import Optional

from core.depthedit import hires
from core.depthedit import sam3_backend as sam
from core.depthedit.project import Click

#: Patch objects' ids start here, apart from the layer's own (CLICK_OBJ_BASE 1000).
PATCH_OBJ_BASE = 5000


def uid(layer) -> str:
    """The SAM session owner and _results key of a layer's patches."""
    return layer.uid + "#patch"


def is_tracked(layer) -> bool:
    return bool(layer.tracked) and layer.masks is not None and len(layer.masks) > 0


def pending(layer) -> bool:
    """A patch made since the layer (or its patches) were last tracked."""
    return layer.patch_base is not None and bool(layer.patch_clicks)


def _hit(eng, layer, frame: int, rel) -> Optional[int]:
    result = eng._results.get((uid(layer), frame))
    return sam.object_at(result, *rel) if result else None


def wants_click(eng, layer, frame: int, rel, label: int, hit: Optional[int]) -> bool:
    """Does this click belong to a patch? Inside a patch's mask: yes (to
    refine it). An Add click outside the layer's mask on a tracked layer:
    yes (a new patch). Anything else is the layer's own, as before."""
    if layer is None:
        return False
    if layer.patch_clicks and _hit(eng, layer, frame, rel) is not None:
        return True
    return label == 1 and hit is None and is_tracked(layer)


def wants_box(layer) -> bool:
    return layer is not None and is_tracked(layer)


def _begin(layer) -> None:
    if layer.patch_base is None:
        layer.patch_base = (layer.masks.snapshot(),
                            layer.masks_hi.snapshot() if layer.masks_hi is not None else None)


def _new_id(layer) -> int:
    used = {c.obj_id for c in layer.patch_clicks}
    return max(used) + 1 if used else PATCH_OBJ_BASE


def click(eng, layer, frame: int, rel, label: int) -> None:
    """An Add / Remove click for a patch (UI thread)."""
    obj_id = _hit(eng, layer, frame, rel)
    if obj_id is None:
        if label != 1:
            eng._status("Nothing to remove there -- click inside the patch's mask.")
            return
        obj_id = _new_id(layer)
    _begin(layer)
    layer.patch_clicks.append(Click(frame=frame, obj_id=obj_id, x=rel[0], y=rel[1], label=label))
    _send(eng, layer, frame, obj_id)


def box(eng, layer, a, b) -> None:
    """A box on a tracked layer: a new patch object (UI thread)."""
    frame = eng.frame_idx
    obj_id = _new_id(layer)
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    _begin(layer)
    layer.patch_clicks.append(Click(frame, obj_id, x0, y0, 2))
    layer.patch_clicks.append(Click(frame, obj_id, x1, y1, 3))
    _send(eng, layer, frame, obj_id)


def _send(eng, layer, frame: int, obj_id: int) -> None:
    group = sorted((c for c in layer.patch_clicks if c.frame == frame and c.obj_id == obj_id),
                   key=lambda c: 0 if c.label in (2, 3) else 1)
    offload = eng.offload_var.get()

    def job() -> None:
        if not open_session(eng, layer, offload):
            result = eng.engine.add_points(frame, obj_id, [(c.x, c.y) for c in group], [c.label for c in group])
            _accept(eng, layer, frame, result)
        eng._ui(eng._status, f"{layer.name}: patch on frame {frame + 1}, added to the tracked mask. "
                             "Start Tracking tracks the patch through the clip.")

    eng._submit("Segmenting", job)
    eng._refresh_layer_panel()
    eng._schedule_preview()


def open_session(eng, layer, offload: bool) -> bool:
    """The patches' own SAM session (worker thread). True when their clicks
    were replayed into it -- the click that called is in already."""
    if not eng.engine.loaded:
        eng._ui(eng._status, f"Loading {sam.model_label()} (first use takes a moment)...")
        eng.engine.load()
    eng.engine.open(eng._resource_for(layer), offload)
    if eng.engine.owner != uid(layer):
        results = eng.engine.replay("", 0, layer.patch_clicks)
        eng.engine.owner = uid(layer)
        for frame, result in results.items():
            _accept(eng, layer, frame, result)
        return True
    return False


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a | b


def _accept(eng, layer, frame: int, result) -> None:
    """A patch's mask on one frame, added to the masks as they were before the
    patch (worker thread; posts the redraw)."""
    eng._results[(uid(layer), frame)] = sam.pack_result(result)
    base_lo, base_hi = layer.patch_base or (layer.masks, layer.masks_hi)
    lo = layer.masks
    lo.set(frame, _union(base_lo.get(frame), sam.union_mask(result, lo.height, lo.width)))
    hi = layer.masks_hi
    if hi is not None:
        hi.set(frame, _union(base_hi.get(frame) if base_hi is not None else None,
                             sam.union_mask(result, hi.height, hi.width)))
    eng._ui(eng._after_prompt, layer.uid)


def propagate_into(eng, layer, offload: bool, lo, hi, direction: str = "both", start: Optional[int] = None,
                   progress=None) -> int:
    """Track the layer's patches and ADD them to `lo` / `hi` (worker thread).
    Returns the frames done; stops with eng._stop."""
    open_session(eng, layer, offload)
    n = eng._num_frames()
    done = 0
    for frame, result in eng.engine.propagate(direction=direction, start_frame=start):
        if eng._stop.is_set():
            break
        if frame < n:
            lo.set(frame, _union(lo.get(frame), sam.union_mask(result, lo.height, lo.width)))
            if hi is not None:
                hi.set(frame, _union(hi.get(frame), sam.union_mask(result, hi.height, hi.width)))
            eng._results[(uid(layer), frame)] = sam.pack_result(result)
        done += 1
        if progress is not None and done % 4 == 0:
            progress(done, frame)
    return done


def track(eng, layer, offload: bool, direction: str = "both", start: Optional[int] = None,
          total: Optional[int] = None) -> None:
    """Start Tracking with a patch pending: only the patches are tracked,
    added to the masks the layer had before them (UI thread; submits)."""
    n = eng._num_frames()
    total = total or n

    def job() -> None:
        base_lo, base_hi = layer.patch_base
        lo = base_lo.snapshot()
        hi = base_hi.snapshot() if base_hi is not None else None
        done = propagate_into(eng, layer, offload, lo, hi, direction, start,
                              lambda d, f: eng._ui(eng._track_progress, d, total, f))
        if eng._stop.is_set():
            eng._ui(eng._status, "Tracking stopped; the patch stays on the frames it was made on.")
            return
        layer.masks, layer.masks_hi, layer.patch_base = lo, hi, None
        eng._ui(eng._track_progress, total, total, None)
        eng._ui(eng._status, f"{layer.name}: patch tracked ({done} frames) and added to the layer's masks.")
        eng._ui(eng._after_prompt, layer.uid)

    eng._submit("Tracking", job)


def clear(eng, layer) -> None:
    """The layer's prompts cleared: its patches go with them."""
    layer.patch_clicks = []
    layer.patch_base = None
    for key in [k for k in eng._results if k[0] == uid(layer)]:
        del eng._results[key]
    if eng.engine.owner == uid(layer):
        eng.engine.owner = None
