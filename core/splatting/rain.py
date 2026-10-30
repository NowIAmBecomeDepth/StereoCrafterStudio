"""rain.py -- falling rain kept apart from the scene in Splatting (user,
2026-10-06: "a rain button in splatting ... allow the depth controls to
affect the rain's placement and strength").

DepthCrafter sees through rain: its map is the scene's, and a plain warp
paints the rain onto whatever is behind it. With Rain on, each frame's rain
is separated from it (using its neighbours), the clean frame is warped as
usual, and the rain is warped on its own -- through the same forward warp,
at depths of its own in front of the scene -- and added back. The left eye
stays the source frame. Because the rain goes through the same warp,
Disparity, Convergence, Gamma's result, Pop Out Boost and Pop-Out act on it
as on the scene: the nearest rain is pushed out the way the nearest objects
are.

Separating it (prototypes/rain/mc_rain.py, measured on a rainy night street):

  * the neighbours (RADIUS either side) are lined up on this frame -- DIS
    optical flow on a copy with thin bright streaks opened away -- unless the
    camera is static; pixels whose motion doesn't check out forward and back
    are left out;
  * the background is a low quantile of the lined-up frames: rain only adds
    light, and in a downpour a pixel holds rain in half its frames;
  * the rain is what is brighter than that, thin (a top-hat), and answers
    ALONG the rain's direction and not across it, per pixel -- piece-by-piece
    streak tests fail in a downpour (streaks break into dots or merge into
    webs). One direction per frame (searched every 5 degrees), or, with Any
    Direction, each pixel's best of every 15 degrees (rain falling at the
    lens, looking up into it).
  * Keep Strands Out (on by default; user, 2026-10-07 -- a web-like strand read
    as rain in 00115290): thin bright things that run on, in the frame itself,
    are not rain -- webs, wires, cables, kerb edges. The frame's top-hat,
    along a 31 px line minus across it at the best of every 7.5 degrees (half
    size), with hysteresis; pieces at least 80 px long are kept out (grown a
    little). Wet-ground glitter is as bright across as along, rain streaks
    are shorter and fainter (prototypes/rain/web_solid.py: on the four other
    ch.28 rain clips it took 1-10% of what read as rain, mostly kerb and
    object edges).

Keep Lights Out (on by default; user, 2026-10-10 -- ch.28 00114672: lamps, lit windows and the
lamp over a sign taken, "even with guard at 1.0"; Guard only protects what is NEAR): with the
camera moving, a lamp lands a few px off in the lined-up neighbours, and the bright left-over
reads as a short streak. A light is bright in the frame itself (brightest channel > LIGHT_ABS),
stands out from what is around it (> LIGHT_STAND over a LIGHT_AROUND px blur -- a bright
backlit rain sheet or a lit wet street does not) and is wider than a drop (survives an
opening of LIGHT_WIDTH px); grown LIGHT_GROW px for its halo and the misalignment, no rain is
taken there. On the five ch.28 rain clips (3 frames each, scratchpad rainlights/survey.py) it
took 2-7% of the rain, the lights; without the contrast test up to a third (lit rain sheets).

Lit-up frames (user, 2026-10-29 -- ch.28 00115209, the girl and the man in the hat: "pretty
sensitive when lighting hits picking up false positives even with guard all the way up"):
there the light flickers (a frame 35% brighter than the next, bluer) and the camera shakes,
so a frame is brighter and sharper than its lined-up neighbours. Rain only adds light, so
everything the frame has over the background's low quantile counted: the lit brick wall's
texture read as short streaks, and placed in front it landed on her face and shoulder in the
right eye (Guard keeps rain off NEAR things, not out of the rain). Two steps, for rain (not
sparks):
- Relight: each lined-up neighbour's light is matched to the frame's, per channel, where it
  is lit up past RELIGHT_FROM (its light blurred RELIGHT_BLUR px over where it lines up;
  scaled fully from RELIGHT_FULL) -- under that, as before.
- The scene's own texture: no rain where the frame's fine detail is in EVERY lined-up
  neighbour too (local correlation of the band-passed grey >= TEXTURE_FROM, the least of
  the neighbours) -- light and a little blur don't change it; rain, splashes and glitter
  are new each frame (a long streak still overlaps itself the next frame, hence every
  neighbour). Not for mist (its own misalignment test).
On 00115209 the lit frames' rain light fell to ~a quarter (frame 0: 10.2k -> 2.5k), an
ordinary frame keeps its streaks (it loses the web's edges). The old golden frames
(downpour_golden.py, Keep lights out off): 00115290 -7% of the rain's light (the lamp, the
falling people's edges, some street glitter), 00117796 -10% (mostly the rooftops' texture);
00114672 loses its brick wall, kerbs and lamp, 00115480 keeps 88%. CPU = GPU 99.97%.

Depth: Layers planes (1..10, user 2026-10-07), the rain split between them
by streak length (long = near) at this clip's own length quantiles, so each
plane holds an equal share (the cuts smoothed over the clip). The planes are
spread evenly from half way between the scene's middle and its nearest part
(the farthest) to the scene's nearest part plus Rain Depth (0..1) of the way
to the camera (the nearest); one layer sits half way between. The scene's
figures are smoothed over the clip.

Scenario (user, 2026-10-07: a test clip -- a close-up in soft, out-of-focus rain,
barely picked up). "downpour" is the detector as it was (the first test clips' rain must not
change: proven bit-identical, prototypes/rain/downpour_golden.py). "soft" looks for
wider, fainter streaks: a 21 px top-hat, along 45 / across 21, 2.5 x the noise with a
lower floor, the threshold relative to the local light (rain adds light in proportion
to what lights it: dark areas would otherwise get none), and the rain's direction
found at the downpour scale (at the wide scale a broad light band won it). In a
downpour of wide streaks a pixel is rained on in most frames, and the flow is thrown
by the streaks (neighbours failed the forward-backward check, leaving the frame alone
as its own background): soft also lines the neighbours up on a copy with the wide
streaks opened away (21 px), uses 4 neighbours a side and the 10th percentile --
~2.8x the rain on that close-up (prototypes/rain/). Use it with Moving camera:
Static took a moving face's edges for rain, and that clip's camera drifts.

"mist" (user, 2026-10-07: another test clip -- a dense sheet of fine rain in a backlit
haze, barely picked up; Any Direction took the clothing's reflections): the rain is most of
the residual there, so a threshold set from the residual's own spread sits above the rain.
No threshold: the rain is the residual itself, averaged a little along the rain's
direction (grain has none), never more than was there, a third of its colour (the
residual's colour noise would move with it); kept out where the neighbours couldn't be
lined up on an edge (moving clothing, rims: misalignment, not rain -- feathered) and from
strands; only its fine part (a backlit glow that shifts as someone moves, a lit window
sliding over the haze -- broad or bright -- read as rain and was moved to its depth:
the residual less its blur at 6 px, capped at 3x the rain's median). Its layers go by
brightness (brighter = nearer). With Soft and Mist a direction
search that comes out a tie (score vs the one across it -- grain has no direction)
falls back to vertical: on that clip every direction tied with its right angle and 0
degrees won by default. Downpour's search is as it was.

Foreground guard (0 = off, the default): no rain taken where the scene is near -- the
scene depth's share of the way from its middle to its nearest above (1 - guard), grown a
little. For people in rain: what is taken from them is their own misaligned texture, put
back at the rain's depth. Applied where the depth is known (keep_far()).

Particles (user, 2026-10-07: "use our rain math to detect sparks ... a drop down to switch
to a spark detection mode rather than mixing with the rain presets"): `particle` is "rain"
(everything above) or "sparks" -- tested on a clip of orange specks over a
red set and two more (a spark shower, then a fireball; and
(white-hot streaks in foliage; prototypes/sparks/). A spark is a small bright thing
(the frame's own top-hat, 21 px, strongest channel, > 0.06) that has MOVED: brighter
than both the frame before and the frame after, lined up on it (the median of the three
is then the brighter neighbour) -- a lamp or a catchlight stays put, a misaligned edge
(hair, a fence, foliage) is rarely brighter than both. Pieces are judged whole: not
bluish on average (a white-hot spark's blue fringe stays), dots or short thin streaks
in any direction (<= 100 px long, <= 8 px wide), not where candidates pack together
(> 25% of a 61 px window: fire, a fireball's inside -- texture). Each is grown into the
bright detail around it (> 0.03 within 15 px: its tips and glow). Its light is measured
in the frame itself (the top-hat per channel), not against the neighbours: slow embers
sit in the same place across a 7-frame window, so a neighbours' background kept them
(the first version, against the 25th / 75th percentiles of 7 frames, left them in).
Layers by streak length, as rain's. Known miss: a dense continuous stream (KATTWIRL5's
cluster), where a neighbour has another spark at the same spot. (Colour alone failed: skin
highlights are warm too, and a shower's sparks are white-pink.)

On the GPU (rain_gpu.py, user 2026-10-07): with CUDA the extractor runs every image
step in torch -- the same kernels and borders as the OpenCV calls below -- and keeps
only the optical flow and the grouping into streaks on the CPU (~5x faster). Without
CUDA, or with device="cpu", this file's code runs exactly as before.

Web: a third particle that lives in personal/web, the user's own tools, not released (user,
2026-10-09). When that folder is there it is offered and runs through the same take-out /
warp / add-back (web_module() below); without it, a "web" setting reads as rain.

Pop (user, 2026-10-07): extra Pop-Out for the rain alone, in the same units,
on top of the scene's: Pop-Out's ramp (zero at the screen, u**2) topped at
the rain's OWN nearest plane, so the nearest rain gets all of it, the far
planes less, rain behind the screen and the scene none. (Topped at the
scene's nearest, as the scene's Pop-Out is, most rain sits low on the ramp
and barely moved.)
"""
from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MODES = ("off", "moving", "static")
def web_module():
    """personal.web.web -- the Web particle, kept out of the release (personal/README.md) -- or
    None when this install hasn't got it."""
    try:
        from personal.web import web
        return web
    except ImportError:
        return None


def web_available() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("personal.web.web") is not None
    except (ImportError, ValueError):
        return False


PARTICLES = ("rain", "sparks", "web") if web_available() else ("rain", "sparks")
#: Web's Scenario (personal.web.web.WEB_KINDS): strands (a still shot) | orb (one web, moving)
WEB_PRESETS = ("strands", "orb")
#: Where the particles go in depth (user, 2026-10-07: "the bright sparks behind the goblin bomb
#: appear in front of it"). front: on planes from the scene's middle toward the camera, the same
#: all over the frame -- rain in the air between us and the scene. place: each one at the scene's
#: own depth where it is (smoothed, PLACE_SIGMA px at 1920), lifted toward the camera by its layer
#: (the nearest by PLACE_LIFT of the scene's spread) -- sparks off something in the middle distance.
PLACEMENTS = ("front", "place")
PLACE_SIGMA, PLACE_LIFT = 6.0, 0.25
#: Depth (user, 2026-10-07: "so miniscule in how much it adjusts ... id like the slider being at 0
#: to show the preview with 0 particle effects applied"): 0 = each particle at the scene's own
#: depth right where it is -- as if painted on, no effect; 1 = the full placement, whose nearest
#: plane is DEPTH_SPAN of the scene's spread in front of the scene's nearest part -- the larger of
#: its middle-to-nearest and the room left before 1 (what Depth 1 reached before), at least
#: MIN_SPREAD. Before, only the room left: next to nothing on a map stretched to 0..1 (iw3's).
DEPTH_SPAN, MIN_SPREAD = 1.0, 0.1
#: The layers' own spacing (user, 2026-10-08: "can the depth controls be more manipulated like
#: distance between layers"): Spread scales the distance between them (1 as before -- the farthest
#: half way from the scene's middle to its nearest; 0 all on the nearest plane; 2 twice as far
#: back), Spacing how they share it out (1 even; above 1 they crowd toward the nearest plane,
#: below toward the farthest). In place: Spread scales the lift, Spacing shapes it per layer.
MAX_SPREAD, SPACING_RANGE = 2.0, (0.25, 4.0)
#: The particles' own Convergence (user, 2026-10-08), as an offset from the scene's: + puts their
#: screen plane further back (they sit further behind / come out less), - brings them out. A
#: particle's shift is (its depth - the convergence), so this is their depth moved the other way.
MAX_CONV = 1.0


def spread_of(mid: float, near: float) -> float:
    """How far Depth 1 brings the nearest particles in front of the scene's nearest part."""
    return max(near - mid, 1.0 - near, MIN_SPREAD)
#: Grow (user, 2026-10-07: "a dilate/erode slider for the particle cutouts"): px at 1920 the
#: cutouts are grown (+) or shrunk (-) by. Grown, a pixel takes no more than the frame's own
#: light above the background there, so the scene never loses what it didn't have.
GROW_MAX = 10
SPARK_SIZE = 21                          # px at 1920: the top-hat a spark fits inside
SPARK_ABS, SPARK_BLUE = 0.06, 0.05
SPARK_MAX_LEN, SPARK_MAX_W, SPARK_DENSE, SPARK_GROW = 100, 8, 0.25, 5
SPARK_REACH, SPARK_WEAK = 15, 0.03       # a spark grown into its bright residual (tips, glow)
#: Keep reflections out (user, 2026-10-08, the Goblin's mask: "reflection is being picked up too
#: much no matter what i try"): the fire in his eye lenses flickers and slides with him, so it is
#: brighter than the frames just before and after -- like a spark. Two and three frames on, a
#: spark is gone; the reflection is still about there. A piece is dropped when most of it is lit
#: (REFLECT_SHARE of its own light above the frame's background, within REFLECT_DRIFT px at 1920)
#: in at least REFLECT_NEED of the lined-up frames REFLECT_FRAMES. Off by default: a slow
#: floating ember stays about there too (one test clip lost ~20% of its embers with it on).
REFLECT_FRAMES = (-3, -2, 2, 3)
REFLECT_DRIFT, REFLECT_NEED, REFLECT_SHARE = 2, 3, 0.5
RADIUS = 3                      # neighbours either side
QUANT = 0.25                    # the background's quantile of the lined-up frames
FB_TOL, FB_REL = 1.5, 0.15      # forward-backward check: px at half size + share of the motion
FLOW_SCALE = 0.5
MAX_LAYERS = 10
MAX_POP = 30.0
#: Scenario -> (top-hat, along, across) px at 1920 wide, x noise, floor, light-relative
PRESETS = {
    "downpour": (7, 15, 7, 3.0, 0.003, False),
    "soft": (21, 45, 21, 2.5, 0.0012, True),
    "mist": (7, 15, 7, 3.0, 0.003, False),        # scale only for the strands / direction
}
#: Scenario -> (neighbours a side, background quantile, flow copy's opening px at 1920)
BACKGROUND = {
    "downpour": (RADIUS, QUANT, 5),
    "soft": (4, 0.1, 21),
    "mist": (4, 0.1, 21),
}


def radius_of(settings) -> int:
    """Neighbours either side the scenario needs (read them around each frame)."""
    if getattr(settings, "particle", "rain") == "web":
        return 0                        # the web is found once for the clip (personal.web)
    if getattr(settings, "particle", "rain") == "sparks":
        if getattr(settings, "reflections", False):
            return max(abs(k) for k in REFLECT_FRAMES)       # ... and further, to keep reflections out
        return 1                        # the frame before and after
    return BACKGROUND.get(getattr(settings, "preset", "downpour"), BACKGROUND["downpour"])[0]


@dataclass
class RainSettings:
    mode: str = "off"           # off | moving | static
    any_direction: bool = False
    depth: float = 0.5          # how far from the scene's nearest part toward the camera the near rain comes
    amount: float = 1.0         # how strongly the rain is put back (1 = as it was)
    layers: int = 3             # depth planes the rain is spread over (1..MAX_LAYERS)
    strands: bool = True        # keep long thin bright things (webs, wires) out of the rain
    lights: bool = True         # keep lamps, lit windows, signs out of the rain (LIGHT_*)
    pop: float = 0.0            # extra Pop-Out for the rain only (Disparity-dial units)
    preset: str = "downpour"    # the scenario: downpour | soft | mist (PRESETS)
    guard: float = 0.0          # foreground guard: no rain taken where the scene is near (0 = off)
    particle: str = "rain"      # rain | sparks (PARTICLES)
    placement: str = "front"    # front | place (PLACEMENTS)
    grow: int = 0               # the cutouts grown (+) / shrunk (-), px at 1920 (GROW_MAX)
    reflections: bool = False   # Sparks: keep reflections out (REFLECT_FRAMES)
    spread: float = 1.0         # the distance between the layers (MAX_SPREAD)
    spacing: float = 1.0        # how they share it out: 1 even (SPACING_RANGE)
    conv: float = 0.0           # their own Convergence, from the scene's (MAX_CONV)

    @property
    def on(self) -> bool:
        return self.mode in ("moving", "static")

    @classmethod
    def from_values(cls, mode=None, any_direction=None, depth=None, amount=None, layers=None,
                    strands=None, pop=None, preset=None, guard=None, particle=None,
                    placement=None, grow=None, reflections=None, spread=None, spacing=None,
                    conv=None, lights=None) -> "RainSettings":
        m = str(mode or "off").lower()
        try:
            n = int(round(float(layers if layers is not None else 3)))
        except (TypeError, ValueError):
            n = 3
        part = str(particle).lower() if str(particle or "").lower() in PARTICLES else "rain"
        pre = str(preset or "").lower()
        if pre not in tuple(PRESETS) + WEB_PRESETS or (pre in WEB_PRESETS and part != "web"):
            # a Web scenario only means something to Web: on an install without it
            # (personal/), a sidecar's "orb" fell into Rain and ran half of the soft
            # extractor on downpour sizes (bug sweep 2026-10-09, A5)
            pre = "downpour"
        return cls(mode=m if m in MODES else "off", any_direction=bool(any_direction),
                   depth=float(np.clip(float(depth if depth is not None else 0.5), 0.0, 1.0)),
                   amount=float(np.clip(float(amount if amount is not None else 1.0), 0.0, 3.0)),
                   layers=int(np.clip(n, 1, MAX_LAYERS)),
                   strands=True if strands is None else _truthy(strands),
                   lights=True if lights is None else _truthy(lights),
                   pop=float(np.clip(_num(pop, 0.0), 0.0, MAX_POP)),
                   preset=pre,
                   guard=float(np.clip(_num(guard, 0.0), 0.0, 1.0)),
                   particle=part,
                   placement=str(placement).lower() if str(placement or "").lower() in PLACEMENTS else "front",
                   grow=int(np.clip(round(_num(grow, 0.0)), -GROW_MAX, GROW_MAX)),
                   reflections=False if reflections is None else _truthy(reflections),
                   spread=float(np.clip(_num(spread, 1.0), 0.0, MAX_SPREAD)),
                   spacing=float(np.clip(_num(spacing, 1.0), *SPACING_RANGE)),
                   conv=float(np.clip(_num(conv, 0.0), -MAX_CONV, MAX_CONV)))


def _num(v, default: float) -> float:
    try:
        return default if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return default


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def _odd(x: float) -> int:
    n = max(3, int(round(x)))
    return n if n % 2 else n + 1


def along(img: np.ndarray, angle: float, length: int) -> np.ndarray:
    """img averaged along a line at `angle` degrees (0 = horizontal, 90 = vertical)."""
    k = np.zeros((length, length), np.float32)
    c = (length - 1) / 2
    a = np.radians(angle)
    for r in np.linspace(-c, c, length * 2):
        k[int(round(c - r * np.sin(a))), int(round(c + r * np.cos(a)))] = 1
    return cv2.filter2D(img, -1, k / k.sum())


def _streak_lengths(mask: np.ndarray) -> np.ndarray:
    """Per pixel of the mask, the length of the piece it belongs to (its long axis)."""
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    ys, xs = np.nonzero(lab)
    if not len(ys):
        return np.zeros(mask.shape, np.float32)
    L = lab[ys, xs]
    m0 = np.bincount(L, minlength=n).astype(np.float64) + 1e-9
    mx, my = np.bincount(L, xs, n) / m0, np.bincount(L, ys, n) / m0
    dx, dy = xs - mx[L], ys - my[L]
    cxx = np.bincount(L, dx * dx, n) / m0 + 1 / 12
    cyy = np.bincount(L, dy * dy, n) / m0 + 1 / 12
    cxy = np.bincount(L, dx * dy, n) / m0
    tr, det = cxx + cyy, cxx * cyy - cxy ** 2
    l1 = tr / 2 + np.sqrt(np.maximum(tr ** 2 / 4 - det, 0))
    length = np.sqrt(12 * l1)
    length[0] = 0
    return length[lab].astype(np.float32)


STRAND_LINE, STRAND_LO, STRAND_HI, STRAND_MIN_LEN, STRAND_GROW = 31, 0.06, 0.13, 80, 9
#: Keep Lights Out (see the top): brightness, standing out by, over a blur of (px at 1920),
#: wider than (px), grown by (px)
LIGHT_ABS, LIGHT_STAND, LIGHT_AROUND, LIGHT_WIDTH, LIGHT_GROW = 0.4, 0.15, 20, 5, 12
#: Relight (see the top): blur (px at 1920), dark floor, the ratio it starts at / is full at, at most
RELIGHT_BLUR, RELIGHT_FLOOR, RELIGHT_FROM, RELIGHT_FULL, RELIGHT_MAX = 20, 0.03, 1.25, 1.4, 3.0
#: The scene's own texture (see the top): band-pass (px at 1920), correlation window (px at
#: 1920), no rain where every lined-up neighbour correlates at least this much, dark floor
TEXTURE_FINE, TEXTURE_COARSE, TEXTURE_WIN, TEXTURE_FROM, TEXTURE_FLOOR = 1.5, 6.0, 15, 0.5, 0.004
#: both on (False: the detector as before them -- prototypes/rain/downpour_golden.py)
RELIGHT = TEXTURE = True


def _band(grey_half: np.ndarray, scale: float) -> np.ndarray:
    s = scale * 0.5
    return cv2.GaussianBlur(grey_half, (0, 0), TEXTURE_FINE * s) - cv2.GaussianBlur(grey_half, (0, 0), TEXTURE_COARSE * s)


def texture_match(frame: np.ndarray, img: np.ndarray, ok: np.ndarray, scale: float) -> np.ndarray:
    """How much frame's fine detail is img's (lined up on it, where ok), around each pixel:
    local normalised correlation of the band-passed grey at half size, float32 [H/2, W/2],
    nan where img doesn't line up. Light and a little blur don't change it; rain and
    splashes, new every frame, don't correlate."""
    def half(x):
        g = x.mean(-1, dtype=np.float32) / 255.0
        return cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    a, b = _band(half(frame), scale), _band(half(img), scale)
    v = cv2.resize(ok.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST).astype(np.float32)
    a, b = a * v, b * v
    sig = TEXTURE_WIN * scale * 0.5 / 2
    ab = cv2.GaussianBlur(a * b, (0, 0), sig)
    aa = cv2.GaussianBlur(a * a, (0, 0), sig) + TEXTURE_FLOOR ** 2
    bb = cv2.GaussianBlur(b * b, (0, 0), sig) + TEXTURE_FLOOR ** 2
    out = (ab / np.sqrt(aa * bb)).astype(np.float32)
    out[cv2.GaussianBlur(v, (0, 0), sig) < 0.5] = np.nan
    return out


def relight_gain(frame: np.ndarray, img: np.ndarray, ok: np.ndarray, scale: float) -> Optional[np.ndarray]:
    """How much brighter frame is than the lined-up neighbour img (both [H, W, 3], any range
    0..255) around each pixel, per channel, where img lines up (ok): float32 [H/2, W/2, 3] at
    half size, 1 where the light is the same; None when nowhere is it lit up past RELIGHT_FROM."""
    def low(x):
        h = cv2.resize(x, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(h, (0, 0), RELIGHT_BLUR * scale * 0.5)
    v = ok.astype(np.float32)
    lv = low(v)
    f = frame.astype(np.float32) * (v / 255.0)[..., None]
    n = img.astype(np.float32) * (v / 255.0)[..., None]
    g = (low(f) + RELIGHT_FLOOR * lv[..., None]) / (low(n) + RELIGHT_FLOOR * lv[..., None] + 1e-6)
    g[lv < 0.25] = 1.0
    if not (g >= RELIGHT_FROM).any():
        return None
    w = np.clip((g - RELIGHT_FROM) / (RELIGHT_FULL - RELIGHT_FROM), 0, 1)
    return np.minimum(1.0 + w * (g - 1.0), RELIGHT_MAX).astype(np.float32)


def lights(frame: np.ndarray, scale: float) -> np.ndarray:
    """Lamps, lit windows, signs in the frame (uint8 RGB), grown: bool [H, W]."""
    lum = frame.max(-1).astype(np.float32) / 255.0
    around = cv2.GaussianBlur(lum, (0, 0), LIGHT_AROUND * scale)
    cand = ((lum > LIGHT_ABS) & (lum - around > LIGHT_STAND)).astype(np.uint8)
    k = max(3, _odd(LIGHT_WIDTH * scale))
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    g = max(3, 2 * int(round(LIGHT_GROW * scale)) + 1)
    return cv2.dilate(cand, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g))).astype(bool)


def strands(frame: np.ndarray, scale: float) -> np.ndarray:
    """Long thin bright things in the frame (uint8 RGB): webs, wires, cables. bool [H, W]."""
    g = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    k = _odd(15 * scale)
    th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    small = cv2.resize(th, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    ll = _odd(STRAND_LINE * scale * 0.5)
    a = np.max([along(small, d, ll) - along(small, d + 90, ll) for d in np.arange(0, 180, 7.5)], axis=0)
    a = cv2.resize(a, (th.shape[1], th.shape[0]), interpolation=cv2.INTER_LINEAR)
    weak = (a > STRAND_LO) & (th > 0.5 * STRAND_LO)
    n, lab = cv2.connectedComponents(weak.astype(np.uint8), connectivity=8)
    strong = np.zeros(n, bool)
    strong[np.unique(lab[weak & (a > STRAND_HI)])] = True
    strong[0] = False
    cand = strong[lab]
    keep = _streak_lengths(cand) >= STRAND_MIN_LEN * scale
    gk = _odd(STRAND_GROW * scale)
    return cv2.dilate(keep.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gk, gk))).astype(bool)


class RainExtractor:
    """Separates the rain of frames given with their neighbours. One per
    render (it keeps the rain's direction and the scene's depth figures
    from frame to frame)."""

    def __init__(self, settings: RainSettings, width: int, device: Optional[str] = None):
        self.s = settings
        self.scale = max(width, 1) / 1920.0
        thin, line, short, self.k, self.floor, self.relative = PRESETS.get(
            getattr(settings, "preset", "downpour"), PRESETS["downpour"])
        self.soft = getattr(settings, "preset", "downpour") != "downpour"
        self.radius, self.quant, self.flow_open = BACKGROUND.get(
            getattr(settings, "preset", "downpour"), BACKGROUND["downpour"])
        self.sparks = getattr(settings, "particle", "rain") == "sparks"
        if self.sparks:
            self.radius, self.quant, self.flow_open = 1, 0.5, BACKGROUND["downpour"][2]
        self.thin = _odd(thin * self.scale)
        self.line = _odd(line * self.scale)
        self.short = _odd(short * self.scale)
        # the direction is always found at the downpour scale
        self.angle_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(7 * self.scale),) * 2)
        self._cuts: Optional[np.ndarray] = None      # streak-length cuts between the layers, smoothed
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.thin, self.thin))
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.angle: Optional[float] = None
        self._recent: List[float] = []
        self.scene: Optional[Tuple[float, float]] = None       # (middle, nearest), smoothed
        if device is None:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        self.device = device
        self.gpu = False
        if str(device).startswith("cuda"):
            try:
                import torch
                if torch.cuda.is_available():
                    from . import rain_gpu
                    self.ops = rain_gpu.Ops(device)
                    self.gpu = True
            except Exception as e:                               # no torch / no GPU: the CPU code
                logger.warning(f"Rain on the CPU: {e}")

    # ---- the background -----------------------------------------------------------
    def _flow_grey(self, f: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        g = cv2.resize(g, None, fx=FLOW_SCALE, fy=FLOW_SCALE, interpolation=cv2.INTER_AREA)
        k = _odd(self.flow_open * self.scale * FLOW_SCALE * 2)
        return cv2.morphologyEx(g, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))

    def _background(self, window: np.ndarray, t: int, greys, quants=None):
        """The frame without its rain: a low quantile of its neighbours lined up on it."""
        H, W = window.shape[1:3]
        lo, hi = max(0, t - self.radius), min(len(window), t + self.radius + 1)
        stack, valid = [window[t]], [np.ones((H, W), bool)]
        if self.s.mode == "moving":
            h2, w2 = greys[t].shape
            gx, gy = np.meshgrid(np.arange(w2, dtype=np.float32), np.arange(h2, dtype=np.float32))
            GX, GY = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        for k in range(lo, hi):
            if k == t:
                continue
            if self.s.mode == "static":
                stack.append(window[k]); valid.append(np.ones((H, W), bool))
                continue
            f_tk = self.dis.calc(greys[t], greys[k], None)
            f_kt = self.dis.calc(greys[k], greys[t], None)
            back = cv2.remap(f_kt, gx + f_tk[..., 0], gy + f_tk[..., 1], cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=1e3)
            ok = np.hypot(*(f_tk + back).transpose(2, 0, 1)) < FB_TOL + FB_REL * np.hypot(*f_tk.transpose(2, 0, 1))
            F = cv2.resize(f_tk, (W, H), interpolation=cv2.INTER_LINEAR) / FLOW_SCALE
            okF = cv2.resize(ok.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)
            mx, my = GX + F[..., 0], GY + F[..., 1]
            okF &= (mx >= 0) & (mx <= W - 1) & (my >= 0) & (my <= H - 1)
            stack.append(cv2.remap(window[k], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
            valid.append(okF)
        if RELIGHT and not self.sparks:
            for i in range(1, len(stack)):
                g = relight_gain(stack[0], stack[i], valid[i], self.scale)
                if g is not None:
                    g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)
                    stack[i] = np.clip(stack[i].astype(np.float32) * g, 0, 255)
        self.last_texture = None                # the least any lined-up neighbour correlates
        if TEXTURE and not self.sparks and len(stack) > 1 and getattr(self.s, "preset", "downpour") != "mist":
            m = np.stack([texture_match(stack[0], stack[i], valid[i], self.scale) for i in range(1, len(stack))])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)          # nowhere lined up: nan
                m = np.nanmin(m, axis=0)
            self.last_texture = np.nan_to_num(m, nan=0.0)
        S = np.stack(stack).astype(np.float32) / 255.0
        V = np.stack(valid)
        self.last_lined_up = (V[1:].sum(0) / max(len(V) - 1, 1)) if len(V) > 1 else None
        if quants is not None:                                  # several from the one stack (Sparks)
            try:
                import torch
                St = torch.from_numpy(S).to(self.device)
                Vt = torch.from_numpy(V).to(self.device)[..., None].expand_as(St)
                q = torch.tensor(list(quants), dtype=St.dtype, device=St.device)
                out = torch.nanquantile(torch.where(Vt, St, torch.nan), q, dim=0).cpu().numpy()
                return [out[i] for i in range(len(quants))]
            except Exception:
                S[~np.broadcast_to(V[..., None], S.shape)] = np.nan
                return [np.nanquantile(S, q, axis=0).astype(np.float32) for q in quants]
        try:
            import torch
            St = torch.from_numpy(S).to(self.device)
            Vt = torch.from_numpy(V).to(self.device)[..., None].expand_as(St)
            out = torch.nanquantile(torch.where(Vt, St, torch.nan), self.quant, dim=0)
            return out.cpu().numpy()
        except Exception:                                       # no torch / no memory: numpy
            S[~np.broadcast_to(V[..., None], S.shape)] = np.nan
            return np.nanquantile(S, self.quant, axis=0).astype(np.float32)

    # ---- the rain -------------------------------------------------------------------
    def _angle(self, th: np.ndarray) -> float:
        """This frame's rain direction: every 5 degrees searched at half size, each
        frame, and the median of the last few kept. (Searching only near the last
        frame's answer, at quarter size, locked onto a wrong angle at the start of a
        clip with thin streaks -- they all but vanish at quarter size -- and stayed.)"""
        q = cv2.resize(th, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        score = {a: float(np.clip(along(q, a, 9) - along(q, a + 90, 9), 0, None).mean()) for a in range(0, 180, 5)}
        return self._angle_pick(score)

    def _angle_pick(self, score: dict) -> float:
        """The direction from the search's scores (shared by the CPU and GPU code)."""
        best = max(score, key=score.get)
        if self.soft and score[best] < 1.03 * score[(best + 90) % 180]:
            best = 90                   # no direction to go on (grain): rain falls
        self._recent.append(best)
        del self._recent[:-5]
        # a median on the circle of directions: unwrap around the newest
        ref = self._recent[-1]
        unwrapped = [ref + ((a - ref + 90) % 180 - 90) for a in self._recent]
        return float(np.median(unwrapped) % 180)

    def _sheet(self, frame: np.ndarray, bg: np.ndarray, res: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Mist: the residual as the rain (see the top)."""
        g = res.mean(-1)
        if self.s.any_direction:
            ang = None
        else:
            self.angle = self._angle(cv2.morphologyEx(g, cv2.MORPH_TOPHAT, self.angle_kernel))
            ang = self.angle
        L = _odd(5 * self.scale)
        if ang is None:
            rain = res.copy()
        else:
            rain = np.stack([along(np.ascontiguousarray(res[..., i]), ang, L) for i in range(3)], -1)
        rain = np.minimum(rain, res + 0.004)
        # only the fine part: smooth glows out, then capped near the rain's own level
        g2 = rain.mean(-1)
        fine = np.clip(g2 - cv2.GaussianBlur(g2, (0, 0), 6 * self.scale), 0, None)
        rain = rain * np.clip(fine / (g2 + 1e-5), 0, 1)[..., None]
        on = rain.mean(-1)
        if (on > 0.5 / 255).any():
            cap = 3.0 * float(np.median(on[on > 0.5 / 255]))
            rain = rain * np.minimum(1.0, cap / np.maximum(on, 1e-6))[..., None]
        grey = rain.mean(-1, keepdims=True)
        rain = grey + (rain - grey) / 3.0                       # a third of its colour
        keep = np.ones(g.shape, np.float32)
        lined = getattr(self, "last_lined_up", None)
        if lined is not None:
            lined = cv2.resize(lined.astype(np.float32), (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR)
            bgg = bg.mean(-1)
            edges = np.hypot(cv2.Sobel(bgg, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(bgg, cv2.CV_32F, 0, 1, ksize=3)) > 0.04
            bad = ((lined < 0.75) & edges).astype(np.uint8)
            bad = cv2.dilate(bad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(9 * self.scale),) * 2))
            keep = 1.0 - np.clip(cv2.GaussianBlur(bad.astype(np.float32), (0, 0), 4 * self.scale) * 2, 0, 1)
        if self.s.strands:
            keep[strands(frame, self.scale)] = 0
        if getattr(self.s, "lights", True):
            keep[lights(frame, self.scale)] = 0
        rain = np.clip(rain * keep[..., None], 0, None).astype(np.float32)
        length = rain.mean(-1)                                  # layers by brightness: brighter = nearer
        length[length < 0.5 / 255] = 0
        rain[length == 0] = 0
        self.last_src = res
        return rain, length.astype(np.float32)

    def _sparks(self, frame: np.ndarray, nb: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Sparks (see the top): (light, float32 [H, W, 3]; streak length [H, W]).
        nb: the median of the frame and its two lined-up neighbours."""
        sc = self.scale
        f = frame.astype(np.float32) / 255.0
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(SPARK_SIZE * sc),) * 2)
        detail = np.stack([cv2.morphologyEx(np.ascontiguousarray(f[..., i]), cv2.MORPH_TOPHAT, se)
                           for i in range(3)], -1)
        moved = np.clip(f - nb, 0, None).max(-1)
        cand = (detail.max(-1) > SPARK_ABS) & (moved > SPARK_ABS)
        dens = cv2.blur(cand.astype(np.float32), (_odd(61 * sc),) * 2)
        grown = _spark_pieces(cand, detail, dens, sc)
        light = (detail * grown[..., None]).astype(np.float32)
        length = _streak_lengths(grown)
        length[~grown] = 0
        self.last_src = detail
        return light, length

    def _rain(self, frame: np.ndarray, bg: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        of = frame.astype(np.float32) / 255.0
        res = np.clip(of - bg, 0, None)
        if getattr(self.s, "preset", "downpour") == "mist":
            return self._sheet(frame, bg, res)
        th = cv2.morphologyEx(res.mean(-1), cv2.MORPH_TOPHAT, self.kernel)
        if self.s.any_direction:
            aniso = np.max([along(th, a, self.line) - along(th, a + 90, self.short) for a in range(0, 180, 15)], axis=0)
        else:
            self.angle = self._angle(th if not self.soft else
                                     cv2.morphologyEx(res.mean(-1), cv2.MORPH_TOPHAT, self.angle_kernel))
            aniso = along(th, self.angle, self.line) - along(th, self.angle + 90, self.short)
        if self.relative:                       # as if lit like the frame's middle
            light = cv2.GaussianBlur(bg.mean(-1), (0, 0), 40 * self.scale)
            ref = float(np.median(light))
            aniso = aniso * (ref / np.maximum(light, 0.25 * ref + 1e-6))
        noise = 1.4826 * float(np.median(np.abs(aniso - np.median(aniso))))
        mask = (aniso > max(self.floor, self.k * noise)) & (th > 0)
        if self.s.strands:
            mask &= ~strands(frame, self.scale)
        if getattr(self.s, "lights", True):
            mask &= ~lights(frame, self.scale)
        tex = getattr(self, "last_texture", None)
        if TEXTURE and tex is not None:
            tex = cv2.resize(tex, (mask.shape[1], mask.shape[0]), interpolation=cv2.INTER_LINEAR)
            mask &= tex < TEXTURE_FROM
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))).astype(bool)
        rain = res * mask[..., None]
        length = _streak_lengths(mask)
        length[~mask] = 0
        self.last_src = res
        return rain.astype(np.float32), length

    def bands_of(self, length: np.ndarray) -> np.ndarray:
        """Each rain pixel's layer, uint8: 0 none, 1 farthest .. layers nearest -- cut
        at this clip's streak-length quantiles (equal shares), smoothed over the clip."""
        n = self.s.layers
        on = length > 0
        band = np.zeros(length.shape, np.uint8)
        if n <= 1 or not on.any():
            band[on] = 1
            return band
        cuts = np.quantile(length[on], np.arange(1, n) / n).astype(np.float32)
        if self._cuts is None or len(self._cuts) != len(cuts):
            self._cuts = cuts
        else:
            self._cuts = 0.9 * self._cuts + 0.1 * cuts
        band[on] = (1 + np.searchsorted(self._cuts, length[on], side="right")).astype(np.uint8)
        return band

    def extract_lengths(self, window: np.ndarray, centres: Sequence[int]) -> List[Tuple[np.ndarray, np.ndarray]]:
        """(rain, float32 [H, W, 3] in 0..1; streak length, float32 [H, W], 0 = none)
        for each centre frame of `window` (uint8 RGB [N, H, W, 3]), Grow applied."""
        px = int(getattr(self.s, "grow", 0) or 0)
        if not px:
            return [(r, l) for r, l, _ in self._extract(window, centres, False)]
        return [grow_cutout(r, l, src, px, self.scale) for r, l, src in self._extract(window, centres, True)]

    def extract_parts(self, window: np.ndarray, centres: Sequence[int]) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """(rain, length, the light it was cut from -- float32 [H, W, 3]) per centre, before
        Grow: a preview caches these and applies Grow as a dial (grow_cutout)."""
        return self._extract(window, centres, True)

    def _extract(self, window, centres, keep_src: bool):
        if self.gpu:
            from . import rain_gpu
            try:
                return rain_gpu.extract_lengths(self, window, centres, keep_src)
            except Exception as e:                               # e.g. out of memory: this once on the CPU
                logger.warning(f"Rain on the GPU failed ({e}); this window on the CPU.")
        greys = [self._flow_grey(f) for f in window] if self.s.mode == "moving" else None
        out = []
        for t in centres:
            part = self._sparks if self.sparks else self._rain
            nb = self._background(window, t, greys)
            rain, length = part(window[t], nb)
            src = self.last_src
            if self.sparks and getattr(self.s, "reflections", False):
                rain, length = self.drop_steady(window, t, greys, nb, rain, length)
            out.append((rain, length, src.astype(np.float32) if keep_src else None))
        return out

    def _lined_up(self, window: np.ndarray, t: int, k: int, greys) -> Tuple[np.ndarray, np.ndarray]:
        """window[k] lined up on window[t] (as _background does it), and where that holds."""
        H, W = window.shape[1:3]
        if self.s.mode != "moving":
            return window[k], np.ones((H, W), bool)
        h2, w2 = greys[t].shape
        gx, gy = np.meshgrid(np.arange(w2, dtype=np.float32), np.arange(h2, dtype=np.float32))
        GX, GY = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        f_tk = self.dis.calc(greys[t], greys[k], None)
        f_kt = self.dis.calc(greys[k], greys[t], None)
        back = cv2.remap(f_kt, gx + f_tk[..., 0], gy + f_tk[..., 1], cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=1e3)
        ok = np.hypot(*(f_tk + back).transpose(2, 0, 1)) < FB_TOL + FB_REL * np.hypot(*f_tk.transpose(2, 0, 1))
        F = cv2.resize(f_tk, (W, H), interpolation=cv2.INTER_LINEAR) / FLOW_SCALE
        okF = cv2.resize(ok.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)
        mx, my = GX + F[..., 0], GY + F[..., 1]
        okF &= (mx >= 0) & (mx <= W - 1) & (my >= 0) & (my <= H - 1)
        return cv2.remap(window[k], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE), okF

    def drop_steady(self, window: np.ndarray, t: int, greys, nb, light: np.ndarray,
                    length: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Keep reflections out (REFLECT_FRAMES): the sparks of window[t] without the pieces
        that are still about there two and three frames on. nb: the frame's background
        (the sparks' median of it and its neighbours), [H, W, 3] in 0..1."""
        m = length > 0
        if not m.any():
            return light, length
        nb = np.asarray(nb, np.float32)
        f = window[t].astype(np.float32).max(-1) / 255.0
        base = nb.max(-1)
        own = REFLECT_SHARE * np.maximum(f - base, 0.03)
        r = max(1, int(round(REFLECT_DRIFT * self.scale)))
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        lit = np.zeros(m.shape, np.int32)
        frames = [t + k for k in REFLECT_FRAMES if 0 <= t + k < len(window)]
        for k in frames:
            img, ok = self._lined_up(window, t, k, greys)
            n = cv2.dilate(img.max(-1).astype(np.float32) / 255.0, se)
            lit += ((n - base) >= own) & ok
        need = min(REFLECT_NEED, len(frames))
        if need <= 0:
            return light, length
        n_lab, lab = cv2.connectedComponents(m.astype(np.uint8), connectivity=8)
        steady = np.bincount(lab[m], (lit[m] >= need).astype(np.float64), n_lab)
        drop = steady / np.maximum(np.bincount(lab[m], minlength=n_lab), 1) > 0.5
        drop[0] = False
        keep = ~drop[lab]
        return (light * keep[..., None]).astype(np.float32), np.where(keep, length, 0).astype(np.float32)

    def extract(self, window: np.ndarray, centres: Sequence[int]) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Each centre frame of `window` (uint8 RGB [N, H, W, 3], the batch with
        its neighbours): (rain, float32 [H, W, 3] in 0..1; band, uint8 [H, W],
        0 = none, 1 farthest .. layers nearest)."""
        return [(rain, self.bands_of(length)) for rain, length in self.extract_lengths(window, centres)]

    # ---- its depth --------------------------------------------------------------------
    def depth_of(self, band: np.ndarray, scene_depth: np.ndarray) -> np.ndarray:
        """The rain's depth (same domain as the scene's: post gamma, 0..1), per
        pixel; 0 where there is no rain (behind everything)."""
        d = scene_depth.astype(np.float32)
        mid_now, near_now = float(np.percentile(d, 50)), float(np.percentile(d, 98))
        if self.scene is None:
            self.scene = (mid_now, near_now)
        else:                                               # smoothed: no jumps from frame to frame
            m, n = self.scene
            self.scene = (0.9 * m + 0.1 * mid_now, 0.9 * n + 0.1 * near_now)
        mid, near = self.scene
        return particle_depth(band, d, self.s.layers, self.s.depth, getattr(self.s, "placement", "front"),
                              mid, near, getattr(self.s, "spread", 1.0), getattr(self.s, "spacing", 1.0),
                              getattr(self.s, "conv", 0.0))

    def pop_peak(self) -> Optional[float]:
        """The depth the rain's own Pop is full at: a nearest-plane drop over the
        scene's near part (its smoothed 98th percentile), the same frame to frame.
        Pop took the batch's nearest drop instead, so it moved with where the
        drops happened to fall -- ~10% at a short last batch of sparse rain (bug
        sweep 2026-10-09, A4). None before depth_of has seen the scene."""
        if self.scene is None:
            return None
        mid, near = self.scene
        s = self.s
        n = max(int(s.layers), 1)
        if getattr(s, "placement", "front") == "place":
            full = near + spaced(1.0, getattr(s, "spacing", 1.0)) * PLACE_LIFT * float(getattr(s, "spread", 1.0)) \
                * spread_of(mid, near)
        else:
            full = float(full_levels(mid, near, n, getattr(s, "spread", 1.0), getattr(s, "spacing", 1.0))[n])
        peak = near + float(s.depth) * (full - near)
        conv = float(getattr(s, "conv", 0.0) or 0.0)
        return max(peak - conv, 1e-4) if conv else peak


def grow_cutout(rain: np.ndarray, length: np.ndarray, src: np.ndarray, px: int,
                scale: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """The cutouts grown (px > 0) or shrunk (px < 0) by |px| (at 1920; an ellipse). Grown:
    each new pixel takes the brightest cutout near it, but never more than `src` -- the
    frame's own light above the background there -- and the nearest piece's length (its
    layer). Shrunk: the pieces' edges dropped. (rain, length) like extract_lengths."""
    r = int(round(abs(int(px)) * scale))
    if not px or r < 1:
        return rain, length
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    on = (length > 0).astype(np.uint8)
    if px < 0:
        keep = cv2.erode(on, se, borderType=cv2.BORDER_CONSTANT, borderValue=1).astype(bool)
        return (rain * keep[..., None]).astype(np.float32), np.where(keep, length, 0).astype(np.float32)
    grown = np.minimum(cv2.dilate(rain, se), src.astype(np.float32))
    reach = cv2.dilate(on, se).astype(bool)
    grown = np.where(on[..., None].astype(bool), rain, grown * reach[..., None])
    lit = (grown.max(-1) > 0) & reach
    glen = np.where(lit, cv2.dilate(length.astype(np.float32), se), 0)
    glen = np.where(on.astype(bool), length, glen)
    return grown.astype(np.float32), glen.astype(np.float32)


def spaced(t, spacing: float):
    """Where each layer sits between the farthest (0) and the nearest (1): t (0..1, even) bent
    by Spacing -- 1 even, above 1 crowding toward the nearest, below toward the farthest."""
    g = float(spacing)
    return t if g == 1.0 else 1.0 - (1.0 - t) ** g


def full_levels(mid: float, near: float, layers: int, spread: float = 1.0, spacing: float = 1.0) -> np.ndarray:
    """Placement "front" at Depth 1: [0 (none), farthest plane .. nearest plane]."""
    reach = spread_of(mid, near)
    near_level = near + DEPTH_SPAN * reach
    far_level = near_level - float(spread) * (near_level - (mid + 0.5 * (near - mid)))
    if layers <= 1:
        planes = [0.5 * (far_level + near_level)]
    else:
        t = spaced(np.linspace(0.0, 1.0, layers), spacing)
        planes = list(far_level + (near_level - far_level) * t)
    return np.array([0.0] + planes, np.float32)


def particle_depth(band: np.ndarray, scene_depth: np.ndarray, layers: int, depth: float,
                   placement: str, mid: float, near: float, spread: float = 1.0,
                   spacing: float = 1.0, conv: float = 0.0) -> np.ndarray:
    """The particles' depth per pixel (the scene's domain, near = 1; past 1 is nearer than the
    scene's nearest), 0 where there is none: Depth of the way from the scene's own depth there
    (0: as if painted on) to the placement's (1). mid, near: the scene's 50th / 98th percentiles."""
    d = scene_depth.astype(np.float32)
    n = max(int(layers), 1)
    b = np.minimum(band, n)
    if placement == "place":
        local = cv2.GaussianBlur(d, (0, 0), PLACE_SIGMA * d.shape[1] / 1920.0, borderType=cv2.BORDER_REFLECT_101)
        frac = spaced(b.astype(np.float32) / n, spacing)
        full = local + frac * PLACE_LIFT * float(spread) * spread_of(mid, near)
    else:
        full = full_levels(mid, near, n, spread, spacing)[b]
    out = d + float(depth) * (full - d)
    if conv:
        out = np.maximum(out - float(conv), 1e-4)      # their own Convergence (0 stays "none")
    return np.where(band > 0, out, 0.0).astype(np.float32)


def in_place_depth(band: np.ndarray, scene_depth: np.ndarray, layers: int, depth: float) -> np.ndarray:
    """Placement "place" for one frame on its own (no smoothing): particle_depth."""
    d = scene_depth.astype(np.float32)
    return particle_depth(band, d, layers, depth, "place", float(np.percentile(d, 50)), float(np.percentile(d, 98)))


def _spark_pieces(cand: np.ndarray, res: np.ndarray, dens: np.ndarray, sc: float) -> np.ndarray:
    """From the candidates to the sparks' pixels (bool): pieces judged whole -- not bluish
    on average (a white-hot spark's blue fringe stays), dots / short thin streaks, not in
    texture -- then each grown into the bright residual around it (its tips and glow,
    which the candidates' top-hat cuts short), at most SPARK_REACH px."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros(cand.shape, bool)
    ys, xs = np.nonzero(cand)
    lb = lab[ys, xs]
    s = res[ys, xs].sum(-1) + 1e-6
    blue = np.bincount(lb, (res[ys, xs, 2] - res[ys, xs, 0]) / s, n) / np.maximum(np.bincount(lb, minlength=n), 1)
    length = _streak_lengths(cand)
    L = np.zeros(n, np.float32)
    L[lb] = length[ys, xs]
    area = st[:, 4].astype(np.float32)
    ok = ((blue <= SPARK_BLUE) & (L <= SPARK_MAX_LEN * sc) & (area / np.maximum(L, 1) <= max(SPARK_MAX_W * sc, 3.0))
          & (area >= 3))
    ok[0] = False
    m = ok[lab] & (dens <= SPARK_DENSE)
    reach = cv2.dilate(m.astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(SPARK_REACH * sc),) * 2)).astype(bool)
    near = cv2.dilate(m.astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(SPARK_GROW * sc),) * 2)).astype(bool)
    return near | (reach & (res.max(-1) > SPARK_WEAK))


def apply_guard(rain: np.ndarray, band: np.ndarray, scene_depth: np.ndarray, guard: float,
                scale: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """Foreground guard on one frame's (rain, band): the rain dimmed where the scene is
    near, and its layer cleared where none is left -- so no depth is kept for rain that
    was taken away. The preview cleared it and the render didn't, and Pop's peak could
    come from rain Guard had removed (bug sweep 2026-10-09, A5). One helper for both."""
    keep = keep_far(scene_depth, guard, scale)
    if keep is None:
        return rain, band
    return rain * keep[..., None], band * (keep > 0.5)


def keep_far(scene_depth: np.ndarray, guard: float, scale: float = 1.0) -> Optional[np.ndarray]:
    """Foreground guard: float32 [H, W], 1 where rain may be taken, 0 where the scene is near
    (feathered); None when off. scene_depth: near = 1, any range."""
    if not guard or guard <= 0:
        return None
    d = scene_depth.astype(np.float32)
    mid, near = float(np.percentile(d, 50)), float(np.percentile(d, 98))
    u = (d - mid) / max(near - mid, 1e-6)
    near_mask = (u > 1.0 - float(guard)).astype(np.uint8)
    near_mask = cv2.dilate(near_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(21 * scale),) * 2))
    return 1.0 - np.clip(cv2.GaussianBlur(near_mask.astype(np.float32), (0, 0), 4 * scale) * 2, 0, 1)


def window_indices(batch_indices: Sequence[int], total: int, radius: int = RADIUS) -> Tuple[List[int], List[int]]:
    """The frames to read for a batch with its neighbours (radius_of(settings)
    either side), and where each batch frame sits in them."""
    lo = max(0, min(batch_indices) - radius)
    hi = min(total - 1, max(batch_indices) + radius)
    window = list(range(lo, hi + 1))
    return window, [i - lo for i in batch_indices]


class WindowFrames:
    """The render's frames for Rain / Sparks: each batch with its neighbours,
    read so the source only ever moves forward. Consecutive windows overlap by
    2 x radius; the shared frames are kept, so each read starts where the last
    one ended (bug sweep 2026-10-09 #18: the batch, then its window from
    `radius` frames back, then the next batch from inside that window -- two
    backward seeks per batch: a keyframe seek and up to a GOP of decoding with
    decord, an ffmpeg restart with Strict decode)."""

    def __init__(self, reader, radius: int):
        self.reader, self.radius = reader, int(radius)
        self._frames: dict = {}

    def read(self, batch_indices: Sequence[int]):
        """(window frames as read, where each batch frame sits in them)."""
        window, centres = window_indices(batch_indices, len(self.reader), self.radius)
        need = [i for i in window if i not in self._frames]
        if need:
            for i, frame in zip(need, self.reader.get_batch(need).asnumpy()):
                self._frames[i] = frame
        for i in [i for i in self._frames if i < window[0]]:
            del self._frames[i]                    # behind every later window
        return np.stack([self._frames[i] for i in window]), centres


# ---- the warp, and the Splatting preview ----------------------------------------------
def rain_pop_extra(rain_depth, pop: float, convergence: float, max_disp: float, peak: Optional[float] = None):
    """The rain's own Pop as depth to add (None when off): Pop-Out's ramp, full at
    the rain's nearest plane -- `peak` (RainExtractor.pop_peak: steady), else the
    batch's nearest drop. rain_depth: [B, 1, H, W], 0 where there is no rain."""
    if not pop or pop <= 0:
        return None
    from core.splatting.depth_boost import pop_out_extra
    peak = float(peak) if peak is not None else float(rain_depth.max())
    if peak <= float(convergence) + 1e-3:
        return None
    return pop_out_extra(rain_depth, float(pop), float(convergence), float(max_disp), depth_peak=peak)


def warp_rain(rain_tensor, depth_tensor, amount: float, dual_eye: bool, pop: float = 0.0, **warp_kwargs):
    """The rain [B, 3, H, W] warped at its own depth [B, 1, H, W] through the
    same forward warp as the scene (plain splatting: no Edge Clean), plus the
    rain's own Pop. Returns (left, right): left is None
    unless dual_eye."""
    from core.splatting.forward_warp import execute_forward_warp
    for k in ("mask_mode", "splat_backend", "disp_probe",
              "boost_prefilter_radius", "boost_prefilter_eps", "source_tensor", "depth_tensor", "dual_eye"):
        warp_kwargs.pop(k, None)
    extra = rain_pop_extra(depth_tensor, pop, warp_kwargs.get("zero_disparity_anchor_val", 0.5),
                           warp_kwargs.get("max_disp", 20.0), peak=_PREVIEW_POP_PEAK.get("peak"))
    out = execute_forward_warp(source_tensor=rain_tensor * float(amount), depth_tensor=depth_tensor,
                               mask_mode="SC", splat_backend="SC", dual_eye=dual_eye, extra_depth=extra,
                               **warp_kwargs)
    return (out[0], out[2]) if dual_eye else (None, out[0])


_PREVIEW_CACHE: dict = {}


def _clip_key(pv):
    """The clip the preview shows, as a cache key: its path and modified time.
    A reader's id alone was the key (bug sweep 2026-10-09, A5) -- a new clip's
    reader can reuse the freed address of the old one's, and then frame 0 of
    the new clip got the old clip's rain (or web track)."""
    path = getattr(pv, "last_loaded_video_path", None) if pv is not None else None
    if not path:
        return None
    try:
        return (os.path.normcase(os.path.abspath(str(path))), os.stat(path).st_mtime_ns)
    except OSError:
        return (os.path.normcase(os.path.abspath(str(path))), None)


def preview_rain(engine, left_tensor, depth_tensor, params: dict):
    """For the Splatting preview: this frame's rain [1, 3, H, W] and its depth
    [1, 1, H, W] (on the tensors' device), or (None, None) with Rain off.
    The neighbours come from the preview's own source reader; the last few
    results are kept, so moving a dial doesn't separate the rain again."""
    import torch
    rs = RainSettings.from_values(
        mode=_var(engine, "rain_mode_var", "off"), any_direction=_var(engine, "rain_any_direction_var", False),
        depth=_var(engine, "rain_depth_var", 0.5), amount=_var(engine, "rain_amount_var", 1.0),
        layers=_var(engine, "rain_layers_var", 3), strands=_var(engine, "rain_strands_var", True),
        lights=_var(engine, "rain_lights_var", True),
        preset=_var(engine, "rain_preset_var", "downpour"), guard=_var(engine, "rain_guard_var", 0.0),
        particle=_var(engine, "rain_particle_var", "rain"),
        placement=_var(engine, "rain_placement_var", "front"), grow=_var(engine, "rain_grow_var", 0),
        reflections=_var(engine, "rain_reflections_var", False),
        spread=_var(engine, "rain_spread_var", 1.0), spacing=_var(engine, "rain_spacing_var", 1.0),
        conv=_var(engine, "rain_conv_var", 0.0))
    if not rs.on:
        _WEB_SCENE.clear()
        return None, None
    pv = getattr(engine, "previewer", None)
    _PREVIEW_POP_PEAK.clear()                  # set below for this frame; none for Web (its own max)
    if rs.particle == "web":
        return _preview_web(engine, pv, rs, left_tensor, depth_tensor, params)
    _WEB_SCENE.clear()
    reader = (getattr(pv, "source_readers", None) or {}).get("source_video") if pv is not None else None
    if reader is None:
        return None, None
    idx = int(getattr(pv, "last_loaded_frame_index", 0) or 0)
    H, W = int(left_tensor.shape[2]), int(left_tensor.shape[3])
    flip = bool(params.get("flip_horizontal", False))
    key = (_clip_key(pv), id(reader), idx, rs.mode, rs.any_direction, rs.strands, rs.lights, rs.preset, rs.particle,
           rs.reflections, H, W, flip)
    hit = _PREVIEW_CACHE.get(key)
    if hit is None:
        win_idx, centres = window_indices([idx], len(reader), radius_of(rs))
        win = reader.get_batch(win_idx).asnumpy()
        if win.dtype != np.uint8:                                  # some readers hand floats
            win = np.clip(win * (255.0 if win.max() <= 1.0 else 1.0), 0, 255).astype(np.uint8)
        if win.shape[1:3] != (H, W):
            win = np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA) for f in win])
        if flip:
            win = np.ascontiguousarray(win[:, :, ::-1])
        ext = RainExtractor(rs, W)
        hit = ext.extract_parts(win, centres)[0]               # before Grow: Grow is a dial here
        if len(_PREVIEW_CACHE) >= 6:
            _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))
        _PREVIEW_CACHE[key] = hit
    rain, length, src = hit
    if rs.grow:
        rain, length = grow_cutout(rain, length, src, rs.grow, W / 1920.0)
    ext = RainExtractor(rs, W)                                     # the layers and depths: this frame's, as they are now
    band = ext.bands_of(length)
    d = depth_tensor[0, 0].detach().float().cpu().numpy()
    if d.shape != (H, W):
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)
    rain, band = apply_guard(rain, band, d, rs.guard, W / 1920.0)   # Foreground guard: the near part keeps its rain
    rd = ext.depth_of(band, d)
    _PREVIEW_POP_PEAK["peak"] = ext.pop_peak()             # warp_rain's Pop: as the render's
    dev = left_tensor.device
    return (torch.from_numpy(rain).permute(2, 0, 1).unsqueeze(0).to(dev),
            torch.from_numpy(rd).unsqueeze(0).unsqueeze(0).to(dev))


_WEB_TRACKS: dict = {}
#: the preview's last frame's Pop peak (RainExtractor.pop_peak), for warp_rain
_PREVIEW_POP_PEAK: dict = {}
#: the preview's last web frame: the scene's cleaned depth, for preview_scene_depth()
_WEB_SCENE: dict = {}


def _preview_web(engine, pv, rs, left_tensor, depth_tensor, params):
    """The web in the preview: found once per clip (the same median of sampled frames as the
    render), this frame's light and strand depths, and the scene's cleaned depth kept for
    preview_scene_depth()."""
    import torch
    web = web_module()
    if web is None:
        return None, None
    readers = (getattr(pv, "source_readers", None) or {}) if pv is not None else {}
    reader, dreader = readers.get("source_video"), readers.get("depth_map")
    if reader is None:
        return None, None
    idx = int(getattr(pv, "last_loaded_frame_index", 0) or 0)
    H, W = int(left_tensor.shape[2]), int(left_tensor.shape[3])
    flip = bool(params.get("flip_horizontal", False))
    kind = "orb" if rs.preset == "orb" else "strands"
    key = (_clip_key(pv), id(reader), id(dreader), H, W, flip, kind)
    track = _WEB_TRACKS.get(key)
    if track is None:
        _WEB_TRACKS.clear()
        track = web.build_track(kind, reader, dreader, (W, H), flip=flip)
        _WEB_TRACKS[key] = track
    frame = left_tensor[0].detach().permute(1, 2, 0).float().clamp(0, 1).mul(255).byte().cpu().numpy()
    d = depth_tensor[0, 0].detach().float().cpu().numpy()
    if d.shape != (H, W):
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)
    lit, zf, scene = track.frame(frame, d, grow=rs.grow, smooth=False, index=idx)
    rd = web.placed(zf, scene, rs.depth, rs.conv)
    _WEB_SCENE.clear()
    _WEB_SCENE["scene"] = (idx, H, W, scene)
    dev = left_tensor.device
    return (torch.from_numpy(lit).permute(2, 0, 1).unsqueeze(0).to(dev),
            torch.from_numpy(rd).unsqueeze(0).unsqueeze(0).to(dev))


def preview_scene_depth(depth_tensor):
    """With Web on: the scene's depth for the preview's warp, cleaned of DepthCrafter's strand
    bands (the clean frame warped with them would tear around them); else None."""
    hit = _WEB_SCENE.get("scene")
    if hit is None:
        return None
    import torch
    _idx, H, W, scene = hit
    t = torch.from_numpy(scene).to(depth_tensor.device, depth_tensor.dtype)
    if tuple(depth_tensor.shape[-2:]) != (H, W):
        return None
    return t.reshape(depth_tensor.shape)


def _var(engine, name, default):
    try:
        v = getattr(engine, name).get()
        return default if v is None or v == "" else v
    except Exception:
        return default
