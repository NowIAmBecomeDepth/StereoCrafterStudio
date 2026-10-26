"""edge_fill.py -- refill the strip a deep warp leaves at a frame's edge (user, 2026-10-13).

Splatting shifts the picture; at a frame's edge the shift uncovers a strip no
source pixel reaches (the right eye's left edge; with dual-eye splats the left
eye's right edge too). The inpainter guesses it from a thin margin, and with
more depth the guess shows: a dark bar, or the edge smeared across it. This
pass, on Merging's finished clips:

  1. measures the strip on each eye from the clip's splat mask (``_splatted2*``, or a
     ``_splatted4`` grid's mask row: how far the holes reach in from each edge), plus STRIP_PAD px -- the
     inpainter's smear goes a little past the hole;
  2. hands ROSE (rose_worker.py, in its own environment) a crop at that edge
     at about ROSE_HEIGHT px tall, masked just past the fill's fade -- wider and
     ROSE carries lines straight on from further in, off where they slope --
     with the masked part pre-filled as erase.prefill_frames' wide blur from
     beside it: ROSE sees under its mask, so black there, or the old smear,
     leaked in and left a seam line; the last good column stretched across it
     came back as horizontal streaks (2026-10-26: +0.7 dB on two clips, the
     streaks gone);
  3. keeps ROSE's fill where the mask's holes were -- row by row, frame by frame, as far as
     each row's hole reaches in (plus STRIP_PAD), like Inpainting V1's Hi-Res Blend -- faded
     into the picture over FEATHER px; ROSE itself works a straight strip as wide as the widest;
  4. adds grain like the picture's beside the strip (ROSE's fill is smoother);
  5. rewrites the clip's files -- the SBS and its _L / _R -- in their own
     format (an intermediate as itself; H.264 / H.265 with Merging's encoder
     settings); the originals are kept in ORIGINALS_DIR, and a refill starts from them.

Measured 2026-10-13 on a 1920x1038 clip, masking the same strip in the LEFT
eye, where the edge is real (.claude/HANDOFF.md): the crop matched the whole
frame's fill (strip PSNR 27.9 vs 26.6 dB, the seam line gone), 6 steps matched
50, the grain came out at 1.23 against the real 1.22, and no colour correction
helped (ROSE drifts under a level). 36 frames: ~24 s, most of it ROSE starting.

No Qt here: Merging's controller runs fill() on a worker thread.
"""

from __future__ import annotations

import glob
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import erase as E
from . import rose_env

logger = logging.getLogger(__name__)

STEPS = 6                 # ROSE's steps: 6 matched 50 on the bench; 3-4 lost detail
STRIP_PAD = 8             # px past the mask's holes: the inpainter's smear
FEATHER = 16              # px the fill fades into the picture over, past the strip
OVERMASK = 4              # px ROSE refills past that fade (28 extrapolated a sloping roof
                          # line 1.75 px off; 4: 1.22 px, strip 29.1 vs 27.9 dB, no seam line)
ROSE_HEIGHT = 560         # ROSE works the crop at about this height (0.54 of 1038); Merging's Size
                          # offers 784 too: a sloping line 0.49 px off instead of 1.22, but a wall's
                          # ridges softer and ~2x the time (2026-10-13)
CROP_OF_HEIGHT = 512 / 1038.0   # ROSE sees at least this much of the frame's height across: the
                                # bench's 512 px of a 1038-tall frame, at either Size
#: Native (rose_height 0; user, 2026-10-14): the crop at the clip's own size, in tiles of
#: TILE_HEIGHT rows (near ROSE's training size -- the whole 1038 rows at once smeared a wall
#: brown) overlapping at least TILE_OVERLAP, filled on their own and faded into each other.
#: Bench (a test clip, the left eye's real edge hidden): strip PSNR 30.21 dB against 560's 29.13,
#: detail 5.50 against the real 5.29 (560: 3.02), the join 2.5 against the picture's own 7.0
#: row to row, ~2.4x 560's ROSE time. Chaining the tiles (each given the one above's fill)
#: lost ridges and cost more.
TILE_HEIGHT = 560
TILE_OVERLAP = 80
#: Native and the larger sizes drift in colour (tiles ~7 levels dark): the fill's offset from
#: the real picture where ROSE repainted it (past the strip), per row, smoothed down the frame
#: and over time, is taken off. 784: +0.5 dB; 560 lost 0.3 dB with it, so it isn't used there.
COLOUR_MATCH_FROM = 700         # the Resolutions from this up (784) and Native
GRAIN_BAND = 96           # px of picture past the mask whose grain the fill gets
GRAIN_FRAMES = 24         # frames sampled for the grain's statistics
#: Match Detail (user, 2026-10-14; first tried on a cage-bars erase, where it took ROSE's fills from
#: 6.8 to 9.2 against the picture's 9.3, in mean |Laplacian|): an unsharp mask of this sigma, its
#: strength picked per edge so the fill's fine detail matches the picture's, at most DETAIL_MOST.
DETAIL_SIGMA = 1.2
DETAIL_MOST = 1.0
MAX_STRIP_FRACTION = 0.25  # a "strip" wider than this much of the eye is something else
MIN_STRIP_FRACTION = 1 / 160.0   # narrower (12 px at 1920) the inpainter's own fill holds up
RECORD = ".edge_fill.json"
#: Each clip's strip as measured for Fill Edges' grid (user, 2026-10-14: don't measure again each
#: time), by clip name, kept while its splat mask is the same file.
STRIPS = ".edge_fill_strips.json"
#: The rows' reach is spread over this many rows and frames (each way) before the blend: a
#: notch a row or a frame wide in the mask is no reason for a notch in the fill.
ROW_SPREAD, FRAME_SPREAD = 4, 1
ORIGINALS_DIR = "edge_fill_originals"   # each clip's unfilled original, a refill's source (2026-10-26)

Progress = Callable[[float, str], None]

#: A merged output: <clip>_<width>_merged_<kind>[_L|_R].<ext> (as merge_all reads them).
MERGED_RE = re.compile(r"^(?P<stem>.+?)_(?P<w>\d+)_merged_(?P<kind>full_sbsx|full_sbs|half_sbs)"
                       r"(?:_(?P<eye>[LR]))?\.(?P<ext>mp4|mov|mkv)$", re.IGNORECASE)


# ---- the clips ---------------------------------------------------------------------------

@dataclass
class Group:
    """One merged clip: its SBS and / or its _L and _R files."""
    folder: str
    stem: str
    width: str
    kind: str                                   # full_sbs | full_sbsx | half_sbs
    files: Dict[str, str] = field(default_factory=dict)   # "sbs" / "L" / "R" -> path

    @property
    def name(self) -> str:
        return f"{self.stem}_{self.width}_merged_{self.kind}"


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_groups(folder: str) -> List[Group]:
    """The merged clips directly in `folder`, in name order."""
    groups: Dict[Tuple[str, str, str], Group] = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        m = MERGED_RE.match(name)
        if not m or not os.path.isfile(os.path.join(folder, name)):
            continue
        key = (m["stem"], m["w"], m["kind"].lower())
        g = groups.setdefault(key, Group(folder, *key))
        g.files[m["eye"].upper() if m["eye"] else "sbs"] = os.path.join(folder, name)
    return sorted(groups.values(), key=lambda g: _natural(g.name))


def find_mask(group: Group, mask_folder: str) -> Optional[str]:
    """The clip's splat (mask + warped eye) in Merging's mask folder or its finished/:
    the same width's first, a _splatted2 before a _splatted4, the newest of several. Only
    <stem>_<width>_splatted2... / _splatted4...: a glob on <stem>_* also took clip "shot_b"'s splat
    for clip "shot" (bug sweep 2026-10-14). _splatted4 is Splatting's default output (Dual Output Only
    off) -- its bottom row is a _splatted2 frame (user, 2026-10-18: a fresh project found no mask)."""
    own = re.compile(re.escape(group.stem) + r"_\d+_splatted[24][^.]*\.(?:mp4|mov|mkv)$", re.IGNORECASE)
    found = []
    for d in (mask_folder, os.path.join(mask_folder, "finished")):
        if not d or not os.path.isdir(d):
            continue
        for ext in ("mp4", "mov", "mkv"):
            found += [q for q in glob.glob(os.path.join(glob.escape(d), f"{glob.escape(group.stem)}_*_splatted*.{ext}"))
                      if own.match(os.path.basename(q))]
    if not found:
        return None
    same = [p for p in found if f"_{group.width}_splatted" in os.path.basename(p).lower()]
    pool = same or found
    pool = [p for p in pool if "_splatted2" in os.path.basename(p).lower()] or pool
    return max(pool, key=lambda p: os.path.getmtime(p))


def _file_id(path: str):
    st = os.stat(path)
    return [st.st_size, int(st.st_mtime)]


def _record(folder: str) -> Dict:
    try:
        with open(os.path.join(folder, RECORD), "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(path: str, data) -> None:
    """`data` to `path` whole: a temp file beside it, then swapped in -- a crash mid-write left a
    truncated record, and every clip in the folder then looked unfilled (bug sweep 2026-10-14)."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".part", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        for attempt in range(20):            # a look reading it a moment: Windows refuses the replace
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


#: Old runs' work folders (a quit or crash mid-fill): swept before the next fill or look.
TEMP_PREFIX = ".edgefill_"
#: ...once this old: a live fill's folder must never go (a look starting as a fill does, a
#: second Studio on the same folder -- bug sweep 2026-10-14b); nothing fills for this long.
SWEEP_AGE_S = 6 * 3600


def _age(path: str) -> float:
    """Seconds since anything in `path` (a folder: its newest file) last changed."""
    try:
        newest = os.path.getmtime(path)
        if os.path.isdir(path):
            for name in os.listdir(path):
                newest = max(newest, os.path.getmtime(os.path.join(path, name)))
        return time.time() - newest
    except OSError:
        return 0.0


def sweep(folder: str) -> None:
    """Remove the work folders and half-written clips (<clip>.part) earlier fills left in
    `folder`, once they're SWEEP_AGE_S old (Erase sweeps its own the same way)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(TEMP_PREFIX) and os.path.isdir(path) and _age(path) > SWEEP_AGE_S:
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                logger.info(f"Removed {name}, left by a fill that didn't finish.")
        elif name.endswith(".part") and MERGED_RE.match(name[:-len(".part")]) and os.path.isfile(path) \
                and _age(path) > SWEEP_AGE_S:
            try:
                os.remove(path)
                logger.info(f"Removed {name}, a clip a fill didn't finish writing.")
            except OSError:
                pass


def done(group: Group) -> bool:
    """Every file of the clip is the one this pass wrote (unchanged since)."""
    rec = _record(group.folder)
    try:
        return bool(group.files) and all(
            isinstance(rec.get(os.path.basename(p)), dict)
            and rec[os.path.basename(p)].get("file") == _file_id(p) for p in group.files.values())
    except OSError:
        return False


# ---- the strip, from the splat's mask --------------------------------------------------------

def _gray_frames(path: str, w: int, h: int):
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        while True:
            buf = proc.stdout.read(w * h)
            if len(buf) < w * h:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _mask_half(row: np.ndarray) -> np.ndarray:
    """The mask panel of a [mask | warp] row, whichever side it is on: a mask is nearly all
    0 or 255, a picture is not (as Merging's detect_mask_panel_side)."""
    half = row.shape[1] // 2
    a, b = row[:, :half], row[:, half:2 * half]

    def bimodal(x):
        return float(((x < 16) | (x > 240)).mean())
    return a if bimodal(a) >= bimodal(b) else b


def _runs(hole: np.ndarray) -> np.ndarray:
    """How far each row's hole reaches in from column 0 (0: the row starts with picture)."""
    return np.where(hole.all(axis=1), hole.shape[1], np.argmax(~hole, axis=1))


def _reach(hole: np.ndarray) -> int:
    """How far the holes reach in from column 0: the 95th percentile of the rows' runs."""
    return int(np.percentile(_runs(hole), 95))


def measure(mask_path: str, stop: Optional[threading.Event] = None) -> Dict:
    """{"eyes": {"L"|"R": {"left": px, "right": px}}, "width": the mask's eye width,
    "rows": {eye: {edge: (frames, rows) uint16}}}: the widest the holes reach in from each
    edge of each eye the splat has (a dual-eye ``_splatted2D`` grid: L on top, R below; a
    ``_splatted2``: R; a ``_splatted4`` grid: R, from its bottom row [mask | right eye] -- the
    top row is [left | depth]), over the whole clip (0: no strip); and how far each row's reaches,
    frame by frame, for the blend."""
    info = E._probe(mask_path)
    W, H = info["w"], info["h"]
    dual = "splatted2d" in os.path.basename(mask_path).lower()
    quad = "_splatted4" in os.path.basename(mask_path).lower()
    eyes = ("L", "R") if dual else ("R",)
    out = {eye: {"left": 0, "right": 0} for eye in eyes}
    rows_out = {eye: {"left": [], "right": []} for eye in eyes}
    for frame in _gray_frames(mask_path, W, H):
        if stop is not None and stop.is_set():
            raise E.Stopped()
        rows = ((frame[: H // 2], frame[H // 2: 2 * (H // 2)]) if dual
                else (frame[H // 2: 2 * (H // 2)],) if quad else (frame,))
        for eye, row in zip(eyes, rows):
            hole = _mask_half(row) > 127
            cap = int(hole.shape[1] * MAX_STRIP_FRACTION)
            for edge, h in (("left", hole), ("right", hole[:, ::-1])):
                runs = np.minimum(_runs(h), cap)
                rows_out[eye][edge].append(runs.astype(np.uint16))
                out[eye][edge] = max(out[eye][edge], int(np.percentile(runs, 95)))
    for eye in out:
        for edge in out[eye]:
            if out[eye][edge] < 2:
                out[eye][edge] = 0
    rows = {eye: {edge: np.stack(v) if v else np.zeros((0, 0), np.uint16) for edge, v in d.items()}
            for eye, d in rows_out.items()}
    return {"eyes": out, "width": W // 2, "rows": rows}


def _strips(folder: str, strict: bool = False) -> Dict:
    """The strips cache ({} when there's none). `strict`: a file that is there but can't be read
    raises -- remember() must not write back only its own entry over the others."""
    path = os.path.join(folder, STRIPS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}


_STRIP_KEYS = {"eye": str, "edge": str, "px": int, "frame": int, "eyeWidth": int}
_strips_lock = threading.Lock()              # two looks' measuring threads, one file


def remembered(group: Group, mask_path: str, both: bool = False) -> Optional[Dict]:
    """The strip remember() kept for this clip, if its splat mask is still the same file and it was
    looked for on the same edges (`both`) (and the entry is whole: a damaged one stopped the grid's
    look -- bug sweep 2026-10-14)."""
    got = _strips(group.folder).get(group.name)
    try:
        if (isinstance(got, dict) and got.get("mask") == [os.path.basename(mask_path), *_file_id(mask_path)]
                # one kept before the edges were asked for (2026-10-18) may be the far edge: looked at again
                and "both" in got and bool(got["both"]) == bool(both)):
            strip = got.get("strip")
            if isinstance(strip, dict) and all(isinstance(strip.get(k), t) and not isinstance(strip.get(k), bool)
                                               for k, t in _STRIP_KEYS.items()):
                return strip
    except OSError:
        pass
    return None


def remember(group: Group, mask_path: str, strip: Dict, both: bool = False) -> None:
    """Keep `strip` (worst() plus the eye's width) for this clip, tied to its splat mask and to the
    edges it was looked for on (`both`)."""
    try:
        with _strips_lock:
            try:
                rec = _strips(group.folder, strict=True)
            except ValueError:               # damaged: a fresh cache (its entries were unusable)
                rec = {}
            rec[group.name] = {"mask": [os.path.basename(mask_path), *_file_id(mask_path)], "strip": strip,
                               "both": bool(both)}
            _save_json(os.path.join(group.folder, STRIPS), rec)
    except OSError as e:                     # a read-only folder: measured again next time
        logger.debug("Edge Fill: could not keep the strip of %s: %s", group.name, e)


#: Where Splatting leaves the edge hole: the right eye's left edge (its warp shifts the picture right),
#: and with Dual Eye the left eye's right edge. Only these are looked at unless both edges are asked for
#: (user, 2026-10-18: a clip's far edge was picked for the fill when the hole was on the usual side).
USUAL_EDGE = {"R": "left", "L": "right"}


def edges_wanted(meas: Dict, both: bool = False) -> Dict:
    """`meas` (measure()'s) with only each eye's usual edge (USUAL_EDGE) kept, the other at 0 -- or as
    it is, with `both`."""
    if both:
        return meas
    keep = lambda eye, edge: edge == USUAL_EDGE.get(eye)                       # noqa: E731
    eyes = {e: {d: (px if keep(e, d) else 0) for d, px in ed.items()} for e, ed in meas["eyes"].items()}
    rows = {e: {d: (r if keep(e, d) else np.zeros((0, 0), np.uint16)) for d, r in ed.items()}
            for e, ed in (meas.get("rows") or {}).items()}
    return dict(meas, eyes=eyes, rows=rows)


def worst(meas: Dict, both: bool = False) -> Dict:
    """Where a measure()'s strip is widest, for a look at it: {"eye", "edge", "px" (in the mask's
    eye width; 0: no strip), "frame"} -- only each eye's usual edge (USUAL_EDGE) unless `both`, and
    then the right eye's left edge first, as it's the usual one."""
    meas = edges_wanted(meas, both)
    best = {"eye": "R", "edge": "left", "px": 0, "frame": 0}
    order = sorted(((e, d) for e in meas["eyes"] for d in meas["eyes"][e]),
                   key=lambda k: (k != ("R", "left"), k))
    for eye, edge in order:
        px = meas["eyes"][eye][edge]
        runs = meas["rows"][eye][edge]
        if px > best["px"] and runs.size:
            per_frame = np.percentile(runs.astype(np.float32), 95, axis=1)
            best = {"eye": eye, "edge": edge, "px": int(px), "frame": int(np.argmax(per_frame))}
    return best


def row_alpha(runs: np.ndarray, ratio: float, h: int, n: int, strip: int, keep: int) -> np.ndarray:
    """(n, h, keep) float32: how much of the fill each pixel takes. A row's fill reaches as far
    as its hole did (scaled to the eye, plus STRIP_PAD, at most `strip`), then fades out over
    FEATHER px; a row whose hole doesn't touch the edge keeps its picture. `runs` (frames, rows)
    is the mask's: its rows stretched to `h`, its last frame held past its end. (fill() takes
    row_reach() once and alpha_of() frame by frame: this whole at once ran to GBs on a long clip.)"""
    reach = row_reach(runs, ratio, h, n, strip)
    return np.stack([alpha_of(r, keep) for r in reach]) if n else np.zeros((0, h, keep), np.float32)


def row_reach(runs: np.ndarray, ratio: float, h: int, n: int, strip: int) -> np.ndarray:
    """(n, h) float32: how far each row's fill reaches in, frame by frame (0: not at all)."""
    m = runs.astype(np.float32)
    if m.shape[0] < n:
        m = np.concatenate([m, np.repeat(m[-1:], n - m.shape[0], axis=0)])
    m = m[:n]
    if m.shape[1] != h:
        src = (np.arange(h) + 0.5) * m.shape[1] / float(h) - 0.5
        m = np.stack([np.interp(src, np.arange(m.shape[1]), row) for row in m]).astype(np.float32)
    m = cv2.dilate(m, np.ones((2 * FRAME_SPREAD + 1, 2 * ROW_SPREAD + 1), np.uint8))   # frames x rows
    return np.where(m > 0, np.minimum(np.ceil(m * ratio) + STRIP_PAD, strip), 0).astype(np.float32)


def alpha_of(reach: np.ndarray, keep: int) -> np.ndarray:
    """(h, keep) float32: one frame's blend, from its rows' reach (row_reach)."""
    x = np.arange(keep, dtype=np.float32)[None, :]
    a = np.clip(1.0 - (x - reach[:, None] + 1.0) / (FEATHER + 1.0), 0.0, 1.0)
    a[reach <= 0] = 0.0
    return a.astype(np.float32)


# ---- grain --------------------------------------------------------------------------------

HP_SIGMA = 1.5            # the grain is what this blur takes away
BINS = 5                  # grain strength per brightness band


def _hp(img, s=HP_SIGMA, axis=None):
    """High-pass: the picture minus its blur -- both ways, or along one axis ('x' or 'y')."""
    if axis is None:
        return img - cv2.GaussianBlur(img, (0, 0), s)
    k = cv2.getGaussianKernel(2 * int(math.ceil(3 * s)) + 1, s)
    one = np.ones((1, 1), np.float64)
    blur = cv2.sepFilter2D(img, -1, k, one) if axis == "x" else cv2.sepFilter2D(img, -1, one, k)
    return img - blur


def _flat(img, q=40):
    """Pixels without strong edges (where the high-pass is mostly grain)."""
    g = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 2.0), cv2.COLOR_RGB2GRAY)
    mag = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return mag <= np.percentile(mag, q)


def _robust_cov(r):
    s = 1.4826 * np.median(np.abs(r - np.median(r, axis=0)), axis=0) + 1e-6
    r = r[(np.abs(r) < 3 * s).all(axis=1)]
    return (np.cov(r.T) / 0.973) if len(r) >= 200 else None   # 0.973: what a 3-sigma cut takes off


def grain_stats(regions) -> Dict:
    """regions: (h, w, 3) float32 crops, 0..255. Per brightness band, the 3x3 covariance of a
    one-axis high-pass on flat pixels, along whichever axis has less: grain is the same both
    ways, texture (ridges, railings) is not, so the quieter axis is the grain alone -- measured
    both ways at once the wall's ridges counted as grain and the fill went speckled. Plus the
    grain's size: the fine / coarse high-pass ratio on the quieter axis."""
    per = {"x": [], "y": []}
    fine = {"x": [], "y": []}
    lum = []
    for img in regions:
        f = _flat(img)
        lum.append(img.mean(axis=2)[f])
        for ax in ("x", "y"):
            per[ax].append(_hp(img, axis=ax)[f])
            fine[ax].append(_hp(img, 0.7, axis=ax)[f])
    lum = np.concatenate(lum)
    per = {ax: np.concatenate(v) for ax, v in per.items()}
    fine = {ax: np.concatenate(v) for ax, v in fine.items()}
    edges = np.percentile(lum, np.linspace(0, 100, BINS + 1)) if len(lum) else np.zeros(BINS + 1)
    centres, covs = [], []
    for b in range(BINS):
        m = (lum >= edges[b]) & (lum <= edges[b + 1])
        if m.sum() < 200:
            continue
        cs = [c for c in (_robust_cov(per["x"][m]), _robust_cov(per["y"][m])) if c is not None]
        if not cs:
            continue
        centres.append(float(lum[m].mean()))
        covs.append(min(cs, key=np.trace))
    ratio = 0.0
    if len(lum):
        quiet = min(("x", "y"), key=lambda ax: per[ax].var())
        ratio = float(fine[quiet].std() / max(per[quiet].std(), 1e-6))
    return {"centres": np.array(centres), "covs": np.array(covs), "ratio": ratio}


def _synth_ratio(sig, rng):
    n = rng.standard_normal((256, 256)).astype(np.float32)
    if sig > 0:
        n = cv2.GaussianBlur(n, (0, 0), sig)
    hp = _hp(n, axis="y")
    return float(_hp(n, 0.7, axis="y").std() / hp.std()), float(hp.std() / n.std())


def grain_size(ratio, rng) -> Tuple[float, float]:
    """The blur of white noise whose fine / coarse ratio matches the grain's; and how much of
    that noise's spread the one-axis high-pass keeps (what it is scaled up by)."""
    best = (9.0, 0.0, 1.0)
    for sig in np.arange(0.0, 1.61, 0.1):
        r, keep = _synth_ratio(sig, rng)
        if abs(r - ratio) < best[0]:
            best = (abs(r - ratio), float(sig), keep)
    return best[1], best[2]


def _psd_sqrt(c):
    w, v = np.linalg.eigh(c)
    return v @ np.diag(np.sqrt(np.clip(w, 0, None))) @ v.T


@dataclass
class Grain:
    """What to add to a fill: per brightness band, the 3x3 mix of unit noise that brings the
    fill's grain to the picture's (the shortfall only -- never takes grain away)."""
    centres: np.ndarray
    mixes: np.ndarray
    sigma: float

    def add(self, fill: np.ndarray, rng) -> np.ndarray:
        """fill (h, w, 3) float32 0..255 -> with fresh grain (new noise every call)."""
        n = rng.standard_normal(fill.shape).astype(np.float32)
        if self.sigma > 0:
            n = cv2.GaussianBlur(n, (0, 0), self.sigma)
        n /= n.reshape(-1, 3).std(axis=0) + 1e-6
        cs = self.centres
        lum = fill.mean(axis=2)
        if len(cs) > 1:
            idx = np.clip(np.searchsorted(cs, lum) - 1, 0, len(cs) - 2)
            t = np.clip((lum - cs[idx]) / np.maximum(cs[idx + 1] - cs[idx], 1e-6), 0, 1)[..., None, None]
            m = self.mixes[idx] * (1 - t) + self.mixes[idx + 1] * t
        else:
            m = np.broadcast_to(self.mixes[0], lum.shape + (3, 3))
        return (fill + np.einsum("hwij,hwj->hwi", m, n)).astype(np.float32)


def plan_grain(reference: List[np.ndarray], fills: List[np.ndarray], rng) -> Optional[Grain]:
    """The grain the fills lack against the reference crops; None when either has too few
    plain pixels to say."""
    ref, got = grain_stats(reference), grain_stats(fills)
    if not len(ref["centres"]):
        return None
    sigma, keep = grain_size(ref["ratio"], rng)
    mixes = []
    for c_ref, centre in zip(ref["covs"], ref["centres"]):
        c_fill = (got["covs"][int(np.argmin(np.abs(got["centres"] - centre)))]
                  if len(got["centres"]) else np.zeros((3, 3)))
        mixes.append(_psd_sqrt(c_ref - c_fill) / keep)
    return Grain(ref["centres"], np.array(mixes, np.float32), sigma)


# ---- the fill ------------------------------------------------------------------------------

def _round16(x: float) -> int:
    return max(16, int(round(x / 16.0)) * 16)


def tile_rows(h: int, th: int = TILE_HEIGHT, least: int = TILE_OVERLAP) -> List[Tuple[int, int]]:
    """Native's tiles down a frame `h` rows tall: (first row, end), `th` rows each, spread
    evenly, overlapping at least `least` rows. One tile when the frame fits in one."""
    if h <= th:
        return [(0, h)]
    count = max(2, int(math.ceil((h - least) / float(th - least))))
    return [(round(i * (h - th) / (count - 1)), round(i * (h - th) / (count - 1)) + th) for i in range(count)]


def tile_weights(tiles: List[Tuple[int, int]], h: int) -> np.ndarray:
    """(tiles, h) float32: how much of each tile a row takes -- each overlap faded across."""
    w = np.zeros((len(tiles), h), np.float32)
    for k, (a, b) in enumerate(tiles):
        up = np.ones(b - a, np.float32)
        down = np.ones(b - a, np.float32)
        if k > 0:
            top = tiles[k - 1][1] - a
            up[:top] = np.linspace(0, 1, top + 2)[1:-1]
        if k < len(tiles) - 1:
            bot = b - tiles[k + 1][0]
            down[-bot:] = np.linspace(1, 0, bot + 2)[1:-1]
        # both ramps at once where three tiles meet (one written over the other left a plateau)
        w[k, a:b] = np.minimum(up, down)
    return w / np.maximum(w.sum(axis=0, keepdims=True), 1e-6)


def colour_offsets(real: np.ndarray, filled: np.ndarray, rows_sigma: float = 40.0, frames: int = 9) -> np.ndarray:
    """(n, h, 3): what to add to a fill so its colour meets the real picture's. `real` and
    `filled` (n, h, w, 3): the same band, where ROSE repainted real picture. Per row and
    channel, smoothed down the frame (a drift, not detail) and over `frames` frames."""
    off = (real.astype(np.float32) - filled.astype(np.float32)).mean(axis=2)          # (n, h, 3)
    n, h = off.shape[:2]
    flat = cv2.GaussianBlur(np.ascontiguousarray(off.transpose(1, 0, 2).reshape(h, n * 3)), (0, 0),
                            sigmaX=0.01, sigmaY=rows_sigma) if h > 1 else off.transpose(1, 0, 2).reshape(h, n * 3)
    off = flat.reshape(h, n, 3).transpose(1, 0, 2)
    if n > 1 and frames > 1:
        r = frames // 2
        pad = np.concatenate([np.repeat(off[:1], r, 0), off, np.repeat(off[-1:], r, 0)])
        c = np.cumsum(np.concatenate([np.zeros((1,) + off.shape[1:], np.float32), pad]), axis=0)
        off = (c[2 * r + 1:] - c[:-(2 * r + 1)]) / float(2 * r + 1)
    return off.astype(np.float32)


@dataclass
class _Edge:
    """One eye's one edge to refill, in the eye's own pixels."""
    eye: str
    edge: str                       # left | right
    strip: int                      # px the fill replaces outright
    keep: int = 0                   # ...and where its fade into the picture ends
    mask: int = 0                   # px ROSE refills
    crop: int = 0                   # px of the eye ROSE sees
    need: int = 0                   # px of full-size picture kept for the blend and the grain
    hi: Optional[np.ndarray] = None       # (n, h, need, 3) uint16, the edge on the left
    small: Optional[np.ndarray] = None    # (n, rh, rw, 3) uint8, ROSE's size
    patch: Optional[np.ndarray] = None    # (n, h, keep, 3) uint16: what goes back
    runs: Optional[np.ndarray] = None     # (frames, mask rows): each row's hole reach, the mask's px
    ratio: float = 1.0                    # the mask's px -> the eye's


def _eye_sources(group: Group, probes: Dict[str, Dict]) -> Dict[str, Tuple[str, int, int, int]]:
    """eye -> (file, x of the eye in it, eye width, height): its own file, else its SBS half."""
    out = {}
    for eye in ("L", "R"):
        if eye in group.files:
            p = probes[group.files[eye]]
            out[eye] = (group.files[eye], 0, p["w"], p["h"])
        elif "sbs" in group.files and group.kind in ("full_sbs", "full_sbsx"):
            p = probes[group.files["sbs"]]
            w = p["w"] // 2
            first = "L" if group.kind == "full_sbs" else "R"
            out[eye] = (group.files["sbs"], 0 if eye == first else w, w, p["h"])
    return out


def _regions(group: Group, path: str, probe: Dict, eye: str, eye_w: int) -> List[int]:
    """Where `eye` sits in the file `path`: x offsets (none if it isn't in it)."""
    if path == group.files.get(eye):
        return [0]
    if path == group.files.get("sbs") and group.kind in ("full_sbs", "full_sbsx"):
        first = "L" if group.kind == "full_sbs" else "R"
        return [0 if eye == first else probe["w"] // 2]
    return []


def _crop_reader(path: str, x: int, w: int, h: int, flip: bool):
    vf = f"crop={w}:{h}:{x}:0" + (",hflip" if flip else "")
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vf", vf, "-f", "rawvideo",
                             "-pix_fmt", "rgb48le", "-"],
                            stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = w * h * 6
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint16).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def encoder_args(probe: Dict, settings: Dict) -> List[str]:
    """Video arguments to write a file again in its own format: an intermediate (DNxHR,
    ProRes, FFV1) as itself; H.264 / H.265 with Merging's own encoder settings, in the file's
    codec and bit depth."""
    from core.common.encoding_utils import video_args_from_settings
    codec = probe["codec"]
    if codec in ("dnxhd", "prores", "ffv1", "mjpeg"):
        from studio.merge_all import _matching_args
        return _matching_args(probe)
    s = dict(settings or {})
    s["dnxhr_fullres_split"] = False
    s["codec"] = "H.264" if codec == "h264" else "H.265"
    s["bit_depth"] = "10-bit (Main10)" if ("10" in probe.get("pix_fmt", "")) else "8-bit"
    s["pix_fmt"] = "Auto"
    args, _ext = video_args_from_settings(s)
    return args


def _detail(img: np.ndarray) -> float:
    """Fine detail: the mean |Laplacian| of the picture's brightness, its border left out."""
    g = img.mean(axis=-1) if img.ndim == 3 else img
    lap = cv2.Laplacian(np.ascontiguousarray(g, dtype=np.float32), cv2.CV_32F)
    return float(np.abs(lap[1:-1, 1:-1]).mean()) if lap.shape[0] > 2 and lap.shape[1] > 2 else 0.0


def sharpen(img: np.ndarray, k: float) -> np.ndarray:
    """An unsharp mask: `img` plus `k` times what a DETAIL_SIGMA blur takes away."""
    if k <= 0:
        return img
    return img + k * (img - cv2.GaussianBlur(img, (0, 0), DETAIL_SIGMA))


def detail_gain(real: List[np.ndarray], filled: List[np.ndarray], cols: Optional[slice] = None) -> float:
    """How strongly to sharpen ROSE's fill: `real` and `filled` are the same stretch of picture
    (the band ROSE repainted past the strip, where the real picture still is), frame by frame.
    0 when the fill is already as sharp; else the strength, in 0.05 steps up to DETAIL_MOST, whose
    fill comes closest to the real picture's detail. `cols`: the band's columns in `filled`, which are
    sharpened whole (as the fill is) and then cut."""
    cols = cols or slice(None)
    want = float(np.mean([_detail(r) for r in real]))
    have = float(np.mean([_detail(f[:, cols]) for f in filled]))
    if want <= 0 or have >= 0.97 * want:
        return 0.0
    best, err = 0.0, abs(have - want)
    for k in np.arange(0.05, DETAIL_MOST + 1e-6, 0.05):
        d = float(np.mean([_detail(sharpen(f, float(k))[:, cols]) for f in filled]))
        if abs(d - want) < err:
            best, err = float(k), abs(d - want)
        if d > want:
            break
    return round(best, 2)


def fill(group: Group, mask_path: str, progress: Progress, stop: threading.Event,
         steps: int = STEPS, grain: bool = True, settings: Optional[Dict] = None,
         strips: Optional[Dict] = None, rose_height: int = ROSE_HEIGHT, detail: bool = False,
         both_edges: bool = False, prefill: str = "Blur") -> Dict:
    """Refill the edge strips of one merged clip, its files rewritten in place -- each eye's usual edge
    (USUAL_EDGE), or every edge with a strip with `both_edges`. `prefill`: what ROSE sees under its
    mask -- "Blur" (erase.prefill_frames) or "Stretched" (the last good column across it). Returns {"edges": [...], "files": [...]}
    or {"skipped": why}. Raises E.Stopped, RuntimeError."""
    if not rose_env.ready():
        raise RuntimeError("ROSE isn't set up: Set Up ROSE in the Depth Map Editor's Erase first.")
    from studio.merge_all import probe as probe_file
    # a refill reads each file's unfilled original (2026-10-26: Blur over an earlier Stretched fill
    # faded into that fill -- a line where it ended); `read` is the clip as it is read
    src = {k: kept_original(p) or p for k, p in group.files.items()}
    read = Group(group.folder, group.stem, group.width, group.kind, dict(src))
    probes = {p: probe_file(p) for p in src.values()}
    sources = _eye_sources(read, probes)
    if not sources:
        return {"skipped": "Half SBS isn't supported: fill the _L / _R files, or a Full SBS."}
    progress(0.0, "Measuring the strip from the splat mask")
    strips = edges_wanted(strips or measure(mask_path, stop), both_edges)
    edges = []
    for eye, d in strips["eyes"].items():
        if eye not in sources:
            continue
        ratio = sources[eye][2] / float(strips["width"] or sources[eye][2])   # the mask's size -> the eye's
        least = max(2, int(sources[eye][2] * MIN_STRIP_FRACTION))
        rows = (strips.get("rows") or {}).get(eye, {})
        edges += [_Edge(eye, edge, int(math.ceil(px * ratio)) + STRIP_PAD, runs=rows.get(edge), ratio=ratio)
                  for edge, px in d.items() if px * ratio >= least]
    if not edges:
        return {"skipped": "No edge strip wide enough to need it in its splat mask."}
    logger.info("  Strip: " + ", ".join(f"{e.eye} eye {e.edge} edge {e.strip} px" for e in edges)
                + f" (mask {os.path.basename(mask_path)})")

    n = min(probes[sources[e.eye][0]]["frames"] for e in edges)
    if n <= 0:
        raise RuntimeError(f"Couldn't count the frames of {group.name}.")
    # one crop size for every edge: ROSE takes one size per run
    h = sources[edges[0].eye][3]
    ew = min(sources[e.eye][2] for e in edges)
    # Native: the clip's own size, in tiles (rose_height 0)
    tiles = tile_rows(h) if rose_height <= 0 else None
    tiled = tiles is not None and len(tiles) > 1
    scale = 1.0 if rose_height <= 0 else min(1.0, rose_height / float(h))
    match_colour = rose_height <= 0 or rose_height >= COLOUR_MATCH_FROM   # the Resolution picked, not the clip's scale
    for e in edges:
        e.keep = e.strip + FEATHER
        e.mask = e.keep + OVERMASK
    widest = max(e.mask for e in edges)
    crop = min(ew, max(int(round(h * CROP_OF_HEIGHT)), 2 * (widest + 4 + GRAIN_BAND)))
    rw, rh = _round16(crop * scale), (h if tiled else _round16(h * scale))
    if not tiled:
        tiles = [(0, rh)]
    if widest >= crop - 8:
        return {"skipped": f"The edge strip ({max(e.strip for e in edges)} px) is too wide for the frame."}
    rng = np.random.default_rng(int(time.time()))
    sweep(group.folder)
    tmp = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=group.folder)
    # the frames whose grain and detail are measured: the only ones kept past the blend's width
    pick = np.unique(np.linspace(0, n - 1, min(n, GRAIN_FRAMES)).astype(int))
    picked = set(int(i) for i in pick)
    try:
        # 1. each edge's crop: as wide as the blend for every frame (the grain band and the
        # detail band only for the sampled ones -- the whole clip at that width ran to GBs),
        # and ROSE's size for ROSE
        for k, e in enumerate(edges):
            path, x0, ew, h = sources[e.eye]
            e.crop = crop
            e.need = min(e.crop, e.mask + 4 + GRAIN_BAND)
            e.hi = np.empty((n, h, e.keep, 3), np.uint16)
            e.sample = {}
            # on disk, mapped: at Native the whole clip at full height ran to GBs in memory
            e.small = np.lib.format.open_memmap(os.path.join(tmp, f"e{k}_small.npy"), mode="w+",
                                                dtype=np.uint8, shape=(n, rh, rw, 3))
            x = x0 if e.edge == "left" else x0 + ew - e.crop
            got = 0
            for i, fr in enumerate(_crop_reader(path, x, e.crop, h, e.edge == "right")):
                if i >= n:
                    break
                if stop.is_set():
                    raise E.Stopped()
                e.hi[i] = fr[:, :e.keep]
                if i in picked:
                    e.sample[i] = fr[:, :e.need].copy()
                e.small[i] = cv2.resize((fr >> 8).astype(np.uint8), (rw, rh), interpolation=cv2.INTER_AREA)
                got += 1
                if got % 16 == 0:
                    progress(0.02 + 0.06 * (k + got / n) / len(edges), f"Reading the {e.eye} eye's {e.edge} edge")
            if got != n:
                raise RuntimeError(f"Read {got} of {n} frames of {os.path.basename(path)}.")

        # 2. ROSE: every edge's windows in one run (one model load)
        windows = E.plan_windows([True] * n, window=E.WINDOW, overlap=E.OVERLAP, context=0)
        overlap = E.OVERLAP
        chunks, owner = [], []
        for k, e in enumerate(edges):
            rw = e.small.shape[2]
            mw = int(math.ceil(e.mask * rw / float(e.crop)))
            for j, (a, length) in enumerate(windows):
                for ti, (ta, tb) in enumerate(tiles):
                    frames = np.empty((length, tb - ta, rw, 3), np.uint8)
                    for t in range(length):
                        frames[t] = e.small[min(a + t, n - 1)][ta:tb]
                    masks = np.zeros((length, tb - ta, rw), np.uint8)
                    masks[:, :, :mw] = 255
                    if prefill == "Stretched":
                        frames[:, :, :mw] = frames[:, :, mw:mw + 1]    # the last good column, stretched
                    else:
                        frames = E.prefill_frames(frames, masks > 0)    # a wide blur from beside the strip
                    fp, mp, op = (os.path.join(tmp, f"e{k}w{j}t{ti}_{s}.npy") for s in ("frames", "masks", "clean"))
                    np.save(fp, frames)
                    np.save(mp, masks)
                    chunks.append({"frames": fp, "masks": mp, "out": op})
                    owner.append((k, j, ti))
        job = {"rose_dir": str(rose_env.ROSE_DIR), "embeds": str(rose_env.EMBEDS),
               "size": [tiles[0][1] - tiles[0][0], rw],
               "steps": int(steps), "seed": 42, "guidance": E.GUIDANCE, "chunks": chunks}
        job_path = os.path.join(tmp, "job.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f)
        for e in edges:
            e.small = None
        progress(0.08, "ROSE: starting")          # the reading bar ends before ROSE's lines
        if tiled:
            logger.info(f"  ROSE: {n} frames in {len(windows)} window(s) x {len(tiles)} tiles per edge at "
                        f"{rw}x{tiles[0][1] - tiles[0][0]} (Native: a {crop}-px crop at its own size, the tiles "
                        f"overlapping {tiles[0][1] - tiles[1][0]} rows), {int(steps)} steps")
        else:
            logger.info(f"  ROSE: {n} frames in {len(windows)} window(s) per edge at {rw}x{rh} "
                        f"(a {crop}-px crop at {scale:.2f}), {int(steps)} steps")
        E._run_worker(job_path, len(chunks), int(steps), progress, stop, 0.08, 0.75, bars=True)

        # 3. + 4. each edge's fill at full size, grained, faded in
        for k, e in enumerate(edges):
            cleaned = {(j, ti): np.load(chunks[c]["out"], mmap_mode="r")
                       for c, (kk, j, ti) in enumerate(owner) if kk == k}
            h = e.hi.shape[1]
            tw = tile_weights(tiles, tiles[-1][1]) if tiled else None

            def raw_at(i):
                tiles_out = []
                for ti in range(len(tiles)):
                    acc, wsum = None, 0.0
                    for j, (a, length) in enumerate(windows):
                        t = i - a
                        if 0 <= t < length:
                            wgt = float(min(t + 1, length - t, overlap)) / max(overlap, 1) if len(windows) > 1 else 1.0
                            f = cleaned[(j, ti)][t].astype(np.float32) * wgt
                            acc = f if acc is None else acc + f
                            wsum += wgt
                    tiles_out.append(acc / wsum)
                if tiled:                       # the tiles faded into each other down the frame
                    whole = np.zeros((h,) + tiles_out[0].shape[1:], np.float32)
                    for ti, (ta, tb) in enumerate(tiles):
                        whole[ta:tb] += tiles_out[ti] * tw[ti, ta:tb, None, None]
                else:
                    whole = tiles_out[0]
                up = cv2.resize(whole, (e.crop, h), interpolation=cv2.INTER_CUBIC) \
                    if whole.shape[:2] != (h, e.crop) else whole
                return up[:, :e.need]

            # the colour drift taken off (Native, 784): read where ROSE repainted real picture
            off = None
            if match_colour and e.keep - e.strip >= 6:
                band = slice(e.strip + 2, e.keep)
                real_band = e.hi[:, :, band]
                fill_band = np.empty((n, h, band.stop - band.start, 3), np.float32)
                for i in range(n):
                    if stop.is_set():
                        raise E.Stopped()
                    fill_band[i] = raw_at(i)[:, band]
                    if i % 16 == 0:
                        progress(0.75 + 0.02 * (k + i / n) / len(edges), "Matching colour")
                off = colour_offsets(real_band.astype(np.float32) / 257.0, fill_band)
                logger.info(f"  Colour: matched to the picture beside the {e.eye} eye's {e.edge} strip "
                            f"(by {', '.join(f'{v:+.1f}' for v in off.mean(axis=(0, 1)))} RGB, at most {np.abs(off).max():.1f})")
                del fill_band

            def fill_at(i):
                f = raw_at(i)
                return f + off[i][:, None, :] if off is not None else f

            plan = None
            if grain:
                b0, b1 = min(e.mask + 4, e.need - 8), e.need
                ref = [e.sample[i][:, b0:b1].astype(np.float32) / 257.0 for i in pick]
                fills = [fill_at(i)[:, 2:e.strip].astype(np.float32) for i in pick]
                try:
                    plan = plan_grain(ref, fills, rng)
                except Exception:
                    logger.warning("Edge Fill: couldn't measure the grain; filling without it.", exc_info=True)
                if plan is not None:
                    logger.info(f"  Grain: matched to the picture beside the {e.eye} eye's {e.edge} strip")
                if plan is None:
                    logger.info(f"Edge Fill: too little plain picture beside the {e.eye} eye's {e.edge} "
                                f"strip to measure its grain; no grain added.")
            # Match Detail: ROSE works the edge smaller and its fill comes back up softer than the
            # picture. Its strength is read where ROSE repainted real picture -- the band past the
            # strip, up to its mask's edge -- the same picture both ways, the grain on as it will be.
            gain = 0.0
            if detail and e.mask - e.strip >= 6:
                drng = np.random.default_rng(7)
                real = [e.sample[i][:, e.strip:e.mask].astype(np.float32) / 257.0 for i in pick]
                filled = []
                for i in pick:
                    f = fill_at(i).astype(np.float32)
                    if plan is not None:
                        f = plan.add(f, drng)
                    filled.append(f[:, :e.keep + OVERMASK])
                gain = detail_gain(real, filled, slice(e.strip, e.mask))
                logger.info(f"  Detail: " + (f"sharpened {gain:.2f} to match the picture beside the {e.eye} eye's "
                                             f"{e.edge} strip" if gain > 0 else "the fill is as sharp as the picture"))
            e.sample = None
            if e.runs is not None and e.runs.size:
                reach = row_reach(e.runs, e.ratio, h, n, e.strip)                  # (n, h)
            else:
                straight = np.zeros(e.keep, np.float32)
                straight[:e.strip] = 1.0
                straight[e.strip:e.keep] = np.linspace(1.0, 0.0, FEATHER + 2, dtype=np.float32)[1:-1]
                reach = None
            e.patch = np.empty((n, h, e.keep, 3), np.uint16)
            for i in range(n):
                if stop.is_set():
                    raise E.Stopped()
                alpha = (alpha_of(reach[i], e.keep) if reach is not None
                         else np.broadcast_to(straight, (h, e.keep)))[..., None]
                orig = e.hi[i].astype(np.float32)
                if not alpha.any():
                    e.patch[i] = e.hi[i]
                    continue
                f = fill_at(i)[:, :e.keep].astype(np.float32)
                if plan is not None:
                    f = plan.add(f, rng)
                f = sharpen(f, gain)
                e.patch[i] = np.clip(orig * (1 - alpha) + f * 257.0 * alpha + 0.5, 0, 65535).astype(np.uint16)
                if i % 16 == 0:
                    progress(0.75 + 0.05 * (k + i / n) / len(edges), "Grain and blend")
            e.hi = None
            if e.edge == "right":
                e.patch = np.ascontiguousarray(e.patch[:, :, ::-1])
            cleaned.clear()

        # 5. the clip's files, rewritten in their own format: every one written beside itself
        # first, and only then all swapped in -- a Stop or a failure on the second file left the
        # first one filled and the clip's SBS and _R disagreeing (bug sweep 2026-10-14)
        from core.common.partial_output import discard
        parts = []
        files = list(group.files.items())
        try:
            for fi, (key, path) in enumerate(files):
                p = probes[src[key]]
                places = []
                for e in edges:
                    _src, _x0, ew, _h = sources[e.eye]
                    for x in _regions(read, src[key], p, e.eye, ew):
                        places.append((x if e.edge == "left" else x + ew - e.keep, e.patch))
                if not places:
                    continue
                parts.append((path, _write(path, p, places, n, settings or {}, stop,
                                           lambda d, fi=fi: progress(0.8 + 0.2 * (fi + d) / len(files),
                                                                     f"Writing {os.path.basename(path)}"),
                                           source=src[key])))
        except BaseException:
            for _path, part in parts:
                discard(part)
            raise
        # every original must be movable before any is swapped: one open in a player (the grid's
        # own right-click Play) failed its move after an earlier file was already replaced -- the
        # SBS filled, the _R not (bug sweep 2026-10-14b)
        for path, _part in parts:
            probe = path + ".edgefill-check"
            try:
                os.rename(path, probe)
            except OSError as err:
                for _p, part in parts:
                    discard(part)
                raise RuntimeError(f"{os.path.basename(path)} is open in another program (a player?): close "
                                   f"it and fill again. Nothing was changed.") from err
            os.rename(probe, path)
        written = []
        refills = {group.files[key] for key in src if src[key] != group.files[key]}
        for k, (path, part) in enumerate(parts):
            try:
                _swap(path, part, path in refills)
            except Exception as err:
                for _path, rest in parts[k + 1:]:
                    discard(rest)
                if written:                      # said plainly: the clip's files now disagree
                    raise RuntimeError(f"{err} {', '.join(os.path.basename(w) for w in written)} "
                                       f"{'was' if len(written) == 1 else 'were'} already filled.") from err
                raise
            written.append(path)
            logger.info(f"  Wrote {os.path.basename(path)}")
        rec = _record(group.folder)
        for path in group.files.values():         # the untouched ones too: the clip is done
            rec[os.path.basename(path)] = {
                "file": _file_id(path), "steps": int(steps), "grain": bool(grain), "mask": os.path.basename(mask_path),
                "edges": [{"eye": e.eye, "edge": e.edge, "strip": e.strip} for e in edges],
                "date": time.strftime("%Y-%m-%d %H:%M")}
            if path in written or path in refills:  # its kept original, tied to it (kept_original)
                rec[os.path.basename(path)]["original"] = _file_id(_kept_path(path))
        _save_json(os.path.join(group.folder, RECORD), rec)
        progress(1.0, "Filled")
        return {"edges": [{"eye": e.eye, "edge": e.edge, "strip": e.strip} for e in edges], "files": written}
    finally:
        for e in edges:                       # the mapped frames let go, so their file can go
            e.small = None
        shutil.rmtree(tmp, ignore_errors=True)


def _write(path: str, probe: Dict, places, n: int, settings: Dict, stop: threading.Event,
           progress: Callable[[float], None], source: Optional[str] = None) -> str:
    """`path` again with each (x, patch) laid over its frames, to <path>.part (returned);
    _swap() puts it in place. `source`: the file whose frames (and audio) are read, if not
    `path` -- a refill's kept original."""
    source = source or path
    from core.common.color_tags import with_color_tags
    from core.common.partial_output import discard, ffmpeg_output_args, part_path, publish
    info = E._probe(source)
    W, H = info["w"], info["h"]
    color = []
    for key, flag in (("color_space", "-colorspace"), ("color_transfer", "-color_trc"),
                      ("color_primaries", "-color_primaries"), ("color_range", "-color_range")):
        if key in info["color"]:
            color += [flag, info["color"][key]]
    audio = ["-i", source, "-map", "0:v", "-map", "1:a?", "-c:a", "copy"] if info["audio"] else []
    part = part_path(path)
    movflags = ["-movflags", "write_colr"] if path.lower().endswith(".mov") else []
    cmd = with_color_tags(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le",
                           "-s", f"{W}x{H}", "-r", info["rate"], "-i", "-", *audio,
                           *encoder_args(probe, settings), *color, *movflags, *ffmpeg_output_args(part)])
    writer = E._with_stderr(subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    count = 0
    try:
        for i, fr in enumerate(E._reader(source, W, H, "rgb48le")):
            if stop.is_set():
                raise E.Stopped()
            if i < n:
                fr = fr.copy()
                for x, patch in places:
                    fr[:, x:x + patch.shape[2]] = patch[i]
            try:
                writer.stdin.write(np.ascontiguousarray(fr).tobytes())
            except OSError:
                break
            count += 1
            if count % 16 == 0:
                progress(count / max(1, probe["frames"] or n))
    except BaseException:
        try:
            writer.stdin.close()
        except OSError:
            pass
        writer.kill()
        writer.wait()
        writer.err_thread.join(timeout=5)
        writer.stderr.close()
        discard(part)
        raise
    try:
        writer.stdin.close()
    except OSError:
        pass
    writer.wait()
    writer.err_thread.join(timeout=5)
    writer.stderr.close()
    failed = E.writers_failed((writer,), count, max(count, n), f"Writing {os.path.basename(path)}")
    if failed or count < n:
        discard(part)
        raise RuntimeError(failed or f"Writing {os.path.basename(path)} stopped at frame {count} of {n}.")
    return part


def _kept_path(path: str) -> str:
    """Where `path`'s unfilled original is kept: ORIGINALS_DIR beside it, under its own name."""
    return os.path.join(os.path.dirname(path), ORIGINALS_DIR, os.path.basename(path))


def kept_original(path: str) -> Optional[str]:
    """`path`'s kept unfilled original, while `path` is still the file a fill wrote from it (the
    record's ids match); None for a clip never filled, or merged again since (it is its own)."""
    keep = _kept_path(path)
    try:
        rec = _record(os.path.dirname(path)).get(os.path.basename(path))
        if os.path.isfile(keep) and isinstance(rec, dict) and rec.get("file") == _file_id(path)                 and rec.get("original") in (None, _file_id(keep)):
            return keep
    except OSError:
        pass
    return None


def _swap(path: str, part: str, refill: bool = False) -> None:
    """The filled `part` in `path`'s place. The original is kept in ORIGINALS_DIR, where a refill
    reads it (user, 2026-10-26: it went to the Recycle Bin, which can't be read back); a `refill`'s
    once-filled file goes to the Recycle Bin instead (deleted without one: its original is kept)."""
    from core.common.partial_output import discard, publish
    try:
        if refill:
            gone = "is in the Recycle Bin" if _recycle(path) else "was deleted"
            if os.path.exists(path):
                os.remove(path)
            where, what = ORIGINALS_DIR, f"the once-filled one {gone}"
        else:
            where, what = _keep(path), "it was moved"
    except Exception:
        discard(part)                         # the original stays where it was
        raise
    if not publish(part, path):
        raise RuntimeError(f"Couldn't put the filled {os.path.basename(path)} in place: it is kept as "
                           f"{os.path.basename(part)}; {what}, and the original is in {where}.")


def _recycle(path: str) -> bool:
    from core.common import recycle_bin
    try:
        return bool(recycle_bin.send_to_recycle_bin(path)) and not os.path.exists(path)
    except Exception:
        logger.debug("Recycle Bin refused %s", path, exc_info=True)
        return False


def _free(path: str) -> str:
    """`path`, or `<stem> (k)<ext>` beside it if that is taken."""
    stem, ext = os.path.splitext(path)
    dest, k = path, 2
    while os.path.exists(dest):
        dest, k = f"{stem} ({k}){ext}", k + 1
    return dest


def _keep(path: str) -> str:
    """The original into ORIGINALS_DIR under its own name. One already there is an earlier merge's
    (this file isn't the fill made from it): to the Recycle Bin, or kept under a free name -- a
    second fill overwrote its true original here with the once-filled one (bug sweep 2026-10-14b)."""
    dest = _kept_path(path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and not _recycle(dest):
        os.rename(dest, _free(dest))
    shutil.move(path, dest)
    return os.path.dirname(dest)
