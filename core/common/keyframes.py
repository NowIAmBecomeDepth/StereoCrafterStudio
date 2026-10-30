"""
Per-clip keyframe tracks for Special FX parameters (Sep 2026).

A keyframe pins a snapshot of every Pop Out Boost / Depth Map Pre-processing /
Stereo Projection slider value to a specific frame of the CURRENT clip. Two
keyframe types:

  - "static":  values jump to this keyframe's snapshot the instant its frame
    is reached, and hold there until the next keyframe changes them.
  - "dynamic": values ramp linearly from the PREVIOUS keyframe's snapshot to
    THIS keyframe's snapshot, across the frame span between them. The ramp is
    a property of the keyframe being arrived at, not the one departed from --
    a dynamic keyframe following a static one still ramps in.

A dynamic keyframe may carry a CURVE (user, 2026-10-06: "an LFO shape
controller for dynamic keyframes"): how the values travel from the previous
keyframe's snapshot to this one's instead of a straight line. The two
snapshots are the two ends of the swing -- whatever differs between them is
what moves -- and every shape starts on the first and lands on the second:

  - linear (the default), ease in, ease out, ease in-out: one sweep;
  - sine, triangle, square, saw, random: an LFO swinging between the two
    looks; ``cycles`` full swings back and forth before the final arrival
    (0 = a single sweep in that shape). Random holds a new level per swing,
    seeded by the keyframe's frame, so the preview and the render agree;
  - custom: in-between points the user places in the shaper -- each a
    (time, level) within the span, like a dynamic keyframe inside it; the
    values ramp from point to point. Any shape can be baked into points
    (bake_curve) to start from. Each stretch between two points may be BENT
    (user, 2026-10-06: a handle on the line, dragged): ``bends[i]`` = (a, b),
    the handle at fraction ``a`` (0.25..0.75) of the stretch's time, ``b``
    above (+) or below (-) the straight line; the stretch becomes the
    quadratic curve through the handle -- ``a`` sets where the bend sits,
    ``b`` how strong it is.

This module defines the data structure, its sidecar (de)serialization (same
"stripped out entirely when empty" convention as dilate_regions, so untouched clips gain no new sidecar keys) and the
interpolation itself -- see :meth:`KeyframeTrack.values_at`.

Interpolation lives HERE rather than beside the GUI variables it started
next to, because both the live preview and the export render read it and
they have to agree frame for frame. The render consumes it through
core/splatting/render_processor.py, which loads each clip's track from that
clip's own sidecar and segments its batches wherever the values change.
"""

import math
import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

SIDECAR_KEY = "keyframe_track"

VALID_TYPES = ("static", "dynamic")

#: A dynamic keyframe's curve shapes; "linear" is today's ramp and the default.
CURVE_SHAPES = ("linear", "ease_in", "ease_out", "ease_in_out", "sine", "triangle", "square", "saw", "random", "custom")
#: The shapes that oscillate (``cycles`` applies to them).
LFO_SHAPES = ("sine", "triangle", "square", "saw", "random")
MAX_CYCLES = 64
#: The shapes as the shapers offer them (Splatting, Depth Map Editor): (stored name, label).
CURVE_LABELS = (("linear", "Linear"), ("ease_in", "Ease In"), ("ease_out", "Ease Out"),
                ("ease_in_out", "Ease In-Out"), ("sine", "Sine"), ("triangle", "Triangle"),
                ("square", "Square"), ("saw", "Saw"), ("random", "Random"), ("custom", "Custom"))
#: A custom curve's in-between points at most.
MAX_POINTS = 256
#: Where a bend's handle may sit along its stretch: the quadratic stays a
#: function of time (its control point within the stretch) inside this.
BEND_MIN, BEND_MAX = 0.25, 0.75


def _clean_points(points) -> List[List[float]]:
    """[[t, level], ...] strictly inside the span, levels 0..1, time-sorted,
    one per time (the later wins)."""
    out = {}
    for p in points or []:
        try:
            t, v = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0.0 < t < 1.0 and math.isfinite(v):
            out[round(t, 6)] = max(0.0, min(1.0, v))
    return [[t, out[t]] for t in sorted(out)][:MAX_POINTS]


def _clean_bends(bends, n: int) -> List[Optional[List[float]]]:
    """One entry per stretch (n = points + 1): None (straight) or [a, b]."""
    out: List[Optional[List[float]]] = []
    for i in range(n):
        b = (bends or [None] * n)[i] if bends and i < len(bends) else None
        try:
            a, v = float(b[0]), float(b[1])
            if not (math.isfinite(a) and math.isfinite(v)) or abs(v) < 1e-6:
                raise ValueError
            out.append([max(BEND_MIN, min(BEND_MAX, a)), max(-1.0, min(1.0, v))])
        except (TypeError, ValueError, IndexError):
            out.append(None)
    return out


def bend_limits(a: float, y0: float, y1: float) -> Tuple[float, float]:
    """How far a stretch's handle (at fraction `a`) may sit below / above its
    straight line: as far as keeps the curve's control point -- and so the
    whole curve -- between the two keyframes' looks (0..1), so a bend never
    overshoots and flattens against them."""
    base = 2 * (y0 + a * (y1 - y0)) - (y0 + y1) / 2
    return (0.0 - base) / 2, (1.0 - base) / 2


def bent(t: float, x0: float, y0: float, x1: float, y1: float, bend) -> float:
    """The level at time `t` on the stretch (x0, y0)-(x1, y1), straight or
    bent through its handle (a, b): the quadratic Bezier that passes through
    the handle at its middle, solved for t."""
    if x1 <= x0:
        return y1
    if not bend:
        return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
    a, b = bend
    b = max(bend_limits(a, y0, y1)[0], min(bend_limits(a, y0, y1)[1], b))
    hx = x0 + a * (x1 - x0)
    hy = y0 + a * (y1 - y0) + b
    cx, cy = 2 * hx - (x0 + x1) / 2, 2 * hy - (y0 + y1) / 2     # the control point
    qa, qb, qc = x0 - 2 * cx + x1, 2 * (cx - x0), x0 - t        # x(s) = t
    if abs(qa) < 1e-12:
        s = -qc / qb if qb else 0.0
    else:
        disc = max(qb * qb - 4 * qa * qc, 0.0)
        r = math.sqrt(disc)
        s1, s2 = (-qb + r) / (2 * qa), (-qb - r) / (2 * qa)
        s = s1 if -1e-9 <= s1 <= 1 + 1e-9 else s2
    s = max(0.0, min(1.0, s))
    y = (1 - s) ** 2 * y0 + 2 * (1 - s) * s * cy + s * s * y1
    return max(0.0, min(1.0, y))


def normalise_curve(curve: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"shape", "cycles"} (or {"shape": "custom", "points"}) cleaned up;
    None for the default straight ramp."""
    if not curve:
        return None
    shape = str(curve.get("shape", "linear"))
    if shape == "custom":
        points = _clean_points(curve.get("points"))
        out = {"shape": "custom", "points": points}
        bends = _clean_bends(curve.get("bends"), len(points) + 1)
        if any(b is not None for b in bends):
            out["bends"] = bends
        return out
    if shape not in CURVE_SHAPES or shape == "linear":
        return None
    try:
        cycles = int(round(float(curve.get("cycles", 1))))
    except (TypeError, ValueError):
        cycles = 1
    return {"shape": shape, "cycles": max(0, min(MAX_CYCLES, cycles))}


def curve_fraction(t: float, curve: Optional[Dict[str, Any]], seed: int = 0) -> float:
    """Where along the way from the previous snapshot (0) to this one (1) the
    values are at `t` (0..1 of the span). Always 0 at t=0 and 1 at t=1."""
    t = max(0.0, min(1.0, float(t)))
    curve = normalise_curve(curve)
    if curve is None or t <= 0.0 or t >= 1.0:
        return t
    shape = curve["shape"]
    if shape == "custom":
        pts = [(0.0, 0.0)] + [tuple(p) for p in curve["points"]] + [(1.0, 1.0)]
        bends = curve.get("bends") or [None] * (len(pts) - 1)
        for i, ((t0, v0), (t1, v1)) in enumerate(zip(pts, pts[1:])):
            if t <= t1:
                return bent(t, t0, v0, t1, v1, bends[i])
        return 1.0
    c = curve["cycles"]
    if shape == "ease_in":
        return t * t
    if shape == "ease_out":
        return 1.0 - (1.0 - t) ** 2
    if shape == "ease_in_out":
        return t * t * (3.0 - 2.0 * t)
    swings = 2 * c + 1                       # out to the second look and back, c times, then arrive
    p = t * swings                           # 0..swings: in which swing we are, and how far
    if shape == "sine":
        return 0.5 - 0.5 * math.cos(math.pi * p)
    if shape == "triangle":
        k, f = divmod(p, 1.0)
        return f if int(k) % 2 == 0 else 1.0 - f
    if shape == "square":
        return 0.0 if int(p) % 2 == 0 else 1.0
    if shape == "saw":
        n = c + 1                            # c + 1 rises, each snapping back but the last
        q = t * n
        return 1.0 if q >= n else q - math.floor(q)
    if shape == "random":
        k = int(p)
        if k >= swings - 1:
            return 1.0
        return random.Random(seed * 1000003 + k).random()
    return t


def bake_curve(curve: Optional[Dict[str, Any]], seed: int = 0) -> List[List[float]]:
    """Any curve as custom points that trace it: a preset's corners exactly
    (triangle, square, saw, random) and smooth shapes sampled, so adding a
    point in the shaper starts from the shape that was there."""
    curve = normalise_curve(curve)
    if curve is None:
        return []
    if curve["shape"] == "custom":
        return [list(p) for p in curve["points"]]
    shape, c = curve["shape"], curve.get("cycles", 0)
    eps = 1e-4
    if shape in ("triangle",):
        swings = 2 * c + 1
        return [[k / swings, float(k % 2)] for k in range(1, swings)]
    if shape in ("square", "random"):
        swings = 2 * c + 1
        pts = [[eps, curve_fraction(2 * eps, curve, seed)]]               # the first level from the start
        for k in range(1, swings):
            t = k / swings
            pts.append([t - eps, curve_fraction(t - 2 * eps, curve, seed)])
            pts.append([t, curve_fraction(t + eps, curve, seed)])
        pts.append([1 - eps, curve_fraction(1 - 2 * eps, curve, seed)])   # the last level holds to the end
        return _clean_points(pts)
    if shape == "saw":
        n = c + 1
        pts = []
        for k in range(1, n):
            t = k / n
            pts += [[t - eps, 1.0], [t, 0.0]]
        return _clean_points(pts)
    n = 8 if shape.startswith("ease") else min(MAX_POINTS, 6 * (2 * c + 1))
    return _clean_points([[k / (n + 1), curve_fraction(k / (n + 1), curve, seed)] for k in range(1, n + 1)])


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


class KeyframeTrack:
    """An ordered set of keyframes for one clip, keyed by frame index."""

    def __init__(self) -> None:
        # frame_index -> {"type": "static"|"dynamic", "values": {var_name: float}}
        self._by_frame: Dict[int, Dict[str, Any]] = {}

    # --- mutation ---------------------------------------------------------
    def set_keyframe(self, frame: int, kind: str, values: Dict[str, float],
                     curve: Optional[Dict[str, Any]] = None) -> None:
        """A keyframe at `frame`. A dynamic one keeps the curve it had there
        when no new one is given (re-snapshotting a key keeps its shape)."""
        if kind not in VALID_TYPES:
            raise ValueError(f"Unknown keyframe type: {kind!r}")
        old = self._by_frame.get(int(frame)) or {}
        entry = {
            "type": kind,
            "values": {k: float(v) for k, v in values.items() if _is_number(v)},
        }
        curve = normalise_curve(curve if curve is not None else old.get("curve"))
        if kind == "dynamic" and curve is not None:
            entry["curve"] = curve
        self._by_frame[int(frame)] = entry

    def set_curve(self, frame: int, curve: Optional[Dict[str, Any]]) -> bool:
        """The curve of the DYNAMIC keyframe at `frame` (None: a straight ramp).
        False when there is no dynamic keyframe there."""
        entry = self._by_frame.get(int(frame))
        if entry is None or entry["type"] != "dynamic":
            return False
        curve = normalise_curve(curve)
        if curve is None:
            entry.pop("curve", None)
        else:
            entry["curve"] = curve
        return True

    def curve_at(self, frame: int) -> Optional[Dict[str, Any]]:
        entry = self._by_frame.get(int(frame))
        return dict(entry["curve"]) if entry and entry.get("curve") else None

    def clear_frame(self, frame: int) -> bool:
        """Removes the keyframe at `frame`, if any. Returns True if one was removed."""
        return self._by_frame.pop(int(frame), None) is not None

    def clear_all(self) -> None:
        self._by_frame.clear()

    # --- queries ------------------------------------------------------------
    def is_empty(self) -> bool:
        return not self._by_frame

    def frame_at(self, frame: int) -> Optional[Dict[str, Any]]:
        return self._by_frame.get(int(frame))

    def sorted_frames(self) -> List[int]:
        return sorted(self._by_frame.keys())

    def items_sorted(self) -> Iterable[Tuple[int, str, Dict[str, float]]]:
        """Yields (frame, type, values) in ascending frame order."""
        for f in self.sorted_frames():
            entry = self._by_frame[f]
            yield f, entry["type"], entry["values"]


    # --- interpolation ------------------------------------------------------
    def values_at(self, frame: int, hold_first: bool = True) -> Optional[Dict[str, float]]:
        """{var_name: value} for `frame`, or None when the track is empty.

        hold_first False (Splatting, user 2026-10-30): before the first keyframe nothing is keyed (None),
        so the clip's own settings show there -- a key made partway through a clip changes things from
        its frame on, not back to the start. True (the Depth Map Editor): the first snapshot holds.

        None means "change nothing" -- the common case, since most clips carry
        no keyframes at all, and it lets every caller skip its work entirely
        rather than apply a no-op override.

        Static keyframes hold their snapshot until the next keyframe. Dynamic
        keyframes ramp linearly from the previous keyframe's snapshot to their
        own, across the frames between them; the ramp belongs to the keyframe
        being ARRIVED at, regardless of the previous one's type. Before the
        first keyframe the values hold at that first snapshot rather than
        drifting up to it.

        This lives here, not in the GUI, because the live preview and the
        export render must agree frame for frame: two implementations of this
        would be two answers to the same question.
        """
        if self.is_empty():
            return None

        prev_entry = None
        next_entry = None
        for f, kind, values in self.items_sorted():
            if f <= frame:
                prev_entry = (f, kind, values)
            else:
                next_entry = (f, kind, values)
                break

        if prev_entry is None:
            if not hold_first:
                return None
            return dict(next_entry[2]) if next_entry else None

        prev_frame, _prev_kind, prev_values = prev_entry
        if next_entry is None or next_entry[1] != "dynamic":
            return dict(prev_values)

        next_frame, _next_kind, next_values = next_entry
        span = next_frame - prev_frame
        if span <= 0:
            return dict(next_values)

        # the arriving keyframe's curve: a straight ramp unless it has one
        t = curve_fraction((frame - prev_frame) / span, self._by_frame[next_frame].get("curve"), seed=next_frame)
        out = {}
        for k in set(prev_values) | set(next_values):
            a, b = prev_values.get(k), next_values.get(k)
            if a is None:
                out[k] = b
            elif b is None:
                out[k] = a
            else:
                out[k] = a + (b - a) * t
        return out

    def count_by_type(self) -> Tuple[int, int]:
        """Returns (n_static, n_dynamic)."""
        n_static = sum(1 for e in self._by_frame.values() if e["type"] == "static")
        n_dynamic = len(self._by_frame) - n_static
        return n_static, n_dynamic

    # --- (de)serialization --------------------------------------------------
    def to_list(self) -> List[Dict[str, Any]]:
        """[{"frame": int, "type": str, "values": {...}}, ...], frame-sorted."""
        out = []
        for f, kind, values in self.items_sorted():
            entry = {"frame": f, "type": kind, "values": dict(values)}
            curve = self._by_frame[f].get("curve")
            if curve:
                entry["curve"] = dict(curve)
            out.append(entry)
        return out

    @classmethod
    def from_dict(cls, data: Optional[Any]) -> "KeyframeTrack":
        track = cls()
        if not data:
            return track
        from core.common.tolerant import load_each

        def add(entry):
            frame = int(entry.get("frame"))
            kind = str(entry.get("type", "static"))
            if kind not in VALID_TYPES:
                kind = "static"
            values = entry.get("values") or {}
            track.set_keyframe(frame, kind, values, curve=entry.get("curve"))

        # A malformed entry is skipped, the rest kept (bug sweep 2026-10-09 A6: one
        # bad entry emptied the track, and the next autosave deleted it from the file).
        entries = data if isinstance(data, list) else (data.get("keyframes", []) if isinstance(data, dict) else None)
        load_each(entries, add, "Keyframe")
        return track


def inject_into_sidecar_dict(sidecar_dict: Dict[str, Any], track: Optional["KeyframeTrack"]) -> None:
    """Writes `track` into sidecar_dict under SIDECAR_KEY. Stripped out entirely
    when empty, same convention as dilate_regions.inject_into_sidecar_dict, so a clip with no keyframes gains
    no new sidecar keys."""
    if track is None or track.is_empty():
        sidecar_dict.pop(SIDECAR_KEY, None)
        return
    sidecar_dict[SIDECAR_KEY] = track.to_list()
