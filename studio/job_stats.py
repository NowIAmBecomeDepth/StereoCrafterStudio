"""job_stats.py -- the live job numbers under DepthCrafter's and Inpainting's
progress bar, and the VRAM readout in the stage strip (user, 2026-09-26).

Both are built not to cost the job anything (the Theme.calm rule, measured
on Inpainting: a redraw every frame cost 8%):

* JobStats publishes only when a diffusion STEP or a CHUNK completes -- once
  every few seconds -- never on the bar's own redraws;
* VramMonitor reads NVML (nvml.dll, the driver's own library: no CUDA
  context, ~5 us a call) every 2 s and publishes only when the figure moves.

Where the numbers come from, without touching the engines' code:

* s/it: the tqdm step bars the pipelines already print, as log_model's
  StreamTee captures them for the console (``BAR_LISTENERS``);
* DepthCrafter's chunks: its bar's own "Window a/b" label;
* Inpainting's chunks: the controller wraps the engine's per-chunk inference
  call for the run (``InpaintingChunks``), the total worked out from the
  clip's frame count the way its loop steps (stride = chunk - overlap, a tail
  that adds no new frames skipped). Which frames a chunk covers comes from
  the engine's own "Starting inference for chunk i-end" / "Reused cached
  inference chunk i-end" log lines, so a resumed clip is placed right. Once
  the chunks are done the clip is rendered: "Hi-Res chunk k/n: frames a-b"
  lines (the hi-res blend), then the encode (``encode_frames_to_mp4``,
  wrapped) -- ``phase`` says which, so the bar can start again for them.

Inpainting's ETA (user, 2026-10-09), worked out when a step or chunk lands:
the running chunk's steps left x its mean s/it, plus the previous chunk's
overhead (its start-to-start time less its steps: the decode, the saves),
plus the chunks after it x that start-to-start time, plus the clip's tail --
the hi-res blend and the encode. InpaintingChunks times the tail (last chunk
landed -> encode done) per frame and keeps it per hi-res resolution across
runs (config/inpainting_eta.json; "lowres" without a blend), so a known
resolution has it from the first clip; an unknown one shows ``etaPartial``
("+ render") until a clip has been timed. The page shows it as a finish time,
so nothing has to tick between updates.

The batch's ETA (user, 2026-10-21: "the eta timer of the individual clips [is]
accurate"): the clip's ETA, plus the frames of the clips not reached yet x
seconds a frame -- the clips finished this run as they took (start to next
start), with the running one as its ETA projects it (so far + ETA). The
controller hands over its run's clips and their frames (``batch_begin``, from
Upcoming Jobs' counts) and names each clip as it starts (``batch_clip``).
DepthCrafter's Process as Segments: the running source's later segments are
not in it (its ETA is the segment's).

Only real inference counts (user, 2026-10-09: "< 1 min" while the model
loaded): Inpainting reads a bar only when its length is the steps of one tile
pass, so a loading bar ("5/5" at 130 it/s) moves nothing; a pass's first step
(tile setup, a torch.compile warm-up) is no s/it sample; until there is one
the ETA is unknown (N/A); and a chunk whose first step took over 5x its
others' (a warm-up) does not set the chunk time.
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import time
from typing import Any, List, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

logger = logging.getLogger("studio.job_stats")

#: "Window 3/7:  40%|####      | 2/5 [00:05<00:07,  2.50s/it]" -- the step
#: count, and the rate (s/it, or it/s which is turned round).
_STEP = re.compile(r"(\d+)/(\d+) \[[^\]]*?(?:,\s*)?([\d.]+)\s*(s/it|it/s)")
_WINDOW = re.compile(r"Window\s+(\d+)/(\d+)")
#: The bar's count alone ("0/15 [00:00<?, ?it/s]" has no rate yet).
_COUNT = re.compile(r"(\d+)/(\d+) \[")
#: The Inpainting engine's chunk lines: "Starting inference for chunk 20-45".
_RUN_CHUNK = re.compile(r"Starting inference for chunk (\d+)-(\d+)")
_CACHED_CHUNK = re.compile(r"Reused cached inference chunk (\d+)-(\d+)")
#: ...and its final render: "Hi-Res chunk 3/16: frames 50-75", then "... complete"
_HIRES_CHUNK = re.compile(r"Hi-Res chunk (\d+)/(\d+): frames (\d+)-(\d+)")
#: Merging's console bars (cli_utils.draw_progress_bar): "  Grain match: |###---|
#: 42.0% clip", "  Encoding out.mp4: |...| 42.0% ", "  Splitting into L/R videos: ..."
_MERGE_BAR = re.compile(r"^\s*(Grain match|Encoding[^:|]*|Splitting into L/R videos):\s*\|[^|]*\|\s*([\d.]+)%\s*(.*)$")
_HIRES_DONE = re.compile(r"Hi-Res (?:chunk processing|streaming finalize) complete")
#: ...and the size it blends to, logged as the clip is set up
_HIRES_RES = re.compile(r"Hi-Res blending enabled\. Target resolution: (\d+)x(\d+)")


def chunk_count(frames: int, chunk: int, overlap: int) -> int:
    """Inpainting's main loop, counted: range(0, frames, chunk - overlap),
    less a tail chunk that holds only overlap."""
    frames, chunk, overlap = int(frames), int(chunk), int(overlap)
    if frames <= 0 or chunk <= 0:
        return 0
    stride = max(1, chunk - overlap)
    n = 0
    for i in range(0, frames, stride):
        length = min(i + chunk, frames) - i
        if i > 0 and overlap > 0 and length <= overlap:
            continue
        n += 1
    return n


def dc_windows(frames: int, window: int, overlap: int) -> List[int]:
    """DepthCrafter's windows for a clip, as its pipeline steps them
    (depth_crafter_ppl.__call__): one window of the whole clip when it fits,
    else starts every window - overlap frames while the start is below
    frames - overlap, each min(window, frames - start) frames long."""
    frames, window, overlap = int(frames), int(window), int(overlap)
    if frames <= 0 or window <= 0:
        return []
    if frames <= window:
        return [frames]
    stride = window - overlap
    if stride <= 0:
        return [window]
    out, start = [], 0
    while start < frames - overlap:
        out.append(min(window, frames - start))
        start += stride
    return out


def _pixels(key: str) -> int:
    try:
        w, h = (int(v) for v in str(key).lower().split("x"))
        return w * h
    except (TypeError, ValueError):
        return 0


class DcEta:
    """DepthCrafter's ETA for the clip running (user, 2026-10-12). A clip is a
    head (read, CLIP and VAE encode), its windows (steps x a step's time, which
    is close to proportional to the window's frames, plus a little between
    windows) and a tail (VAE decode, stabilise, save); head and tail cost about
    the same per frame every time. So:

        ETA = head left + r x (the running window's steps left x its frames
              + steps x the frames of each window to come) + overhead per
              window to come + tail x frames

    r (seconds per step per frame) comes from the bar's s/it, live, a window's
    first step left out (it carries the window's setup -- the first one also the
    warm-up); the head, the tail and r are kept per resolution across runs
    (config/depthcrafter_eta.json), a size not timed yet scaled by pixels from
    the nearest one that has been. On the user's 257-frame clip (8 windows of
    120 / overlap 100, 10 steps) it came to 588 s against 583 s taken.

    Process as Segments runs a clip as one job per segment, each one window of its
    own frames (user, 2026-10-21: the ETA ran out at every segment's end): the
    runner names the clip's segments up front (``segments_planned``, their source
    frames), and the ones after the running segment add (head + steps x r + tail)
    x their frames, scaled as the running one's plan scaled its own (a frame rate
    change). The merge of the segments at the end is not in it.
    The clock is passed in, so it is the caller's."""

    KINDS = ("head", "tail", "step")
    RECENT = 10                 # r: the mean of the last this many steps, so a slowdown shows

    def __init__(self, rates: Optional[dict] = None):
        self.rates = {k: dict((rates or {}).get(k) or {}) for k in self.KINDS}
        self.queue: List[int] = []      # Process as Segments: the clip's segments not started yet
        self.later: List[int] = []      # ...those after the running one
        self.raw = 0                    # ...and the running one's source frames
        self.clip_started(None)

    def segments_planned(self, frames: List[int]) -> None:
        self.queue = [max(0, int(f)) for f in frames]

    def clip_started(self, now: Optional[float]) -> None:
        """A clip -- or a segment, which the queue then names -- starts."""
        if now is not None:
            self.raw = self.queue.pop(0) if self.queue else 0
            self.later = list(self.queue)
        self.frames, self.steps, self.wins, self.key = 0, 0, [], ""
        self.t_clip, self.t_denoise, self.t_tail = now, None, None
        self.window, self.step_n, self.window_t0 = 0, 0, None
        self.samples: List[float] = []
        self.overhead = 0.0

    def plan(self, frames: int, steps: int, window: int, overlap: int, key: str) -> None:
        self.frames, self.steps = max(0, int(frames)), max(1, int(steps))
        self.wins, self.key = dc_windows(frames, window, overlap), str(key)

    def window_started(self, index: int, now: float) -> None:
        if self.t_denoise is None:
            self.t_denoise = now
        if self.window_t0 is not None and index == self.window + 1 and 1 <= self.window <= len(self.wins):
            r = self.rate()
            if r > 0:               # the last window's start-to-start time, less its steps
                self.overhead = max(0.0, now - self.window_t0 - self.steps * r * self.wins[self.window - 1])
        self.window, self.step_n, self.window_t0 = int(index), 0, now

    def step(self, n: int, total: int, seconds_per_step: float, now: float) -> None:
        self.step_n = int(n)
        if n >= 2 and seconds_per_step > 0 and 1 <= self.window <= len(self.wins):
            self.samples = (self.samples + [seconds_per_step / self.wins[self.window - 1]])[-self.RECENT:]
        if self.wins and self.window >= len(self.wins) and n >= total and self.t_tail is None:
            self.t_tail = now       # the last window's last step: the decode and the save follow

    def known(self, kind: str) -> float:
        """What a frame (a frame-step for "step") has cost at this size; a size not
        timed yet from the nearest one that has, by pixels; 0 when none has."""
        table = self.rates.get(kind) or {}
        try:
            if float(table.get(self.key, 0)) > 0:
                return float(table[self.key])
        except (TypeError, ValueError):
            pass
        px = _pixels(self.key)
        sizes = [(abs(_pixels(k) - px), k) for k, v in table.items()
                 if _pixels(k) > 0 and isinstance(v, (int, float)) and v > 0]
        if px <= 0 or not sizes:
            return 0.0
        nearest = min(sizes)[1]
        return float(table[nearest]) * px / _pixels(nearest)

    def rate(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else self.known("step")

    def later_frames(self) -> float:
        """The segments after this one, in this one's plan frames."""
        if not self.later:
            return 0.0
        scale = self.frames / self.raw if self.raw > 0 and self.frames > 0 else 1.0
        return sum(self.later) * scale

    def _later_s(self, head: float, tail: float, r: float) -> float:
        return self.later_frames() * (head + self.steps * r + tail)

    def eta(self, now: float):
        """(seconds left in the clip or -1 when unknown, partial: the head or tail not known yet)."""
        if not self.wins or self.frames <= 0:
            return -1.0, False
        head, tail, r = self.known("head"), self.known("tail"), self.rate()
        partial = tail <= 0 or (bool(self.later) and head <= 0)
        tail_s = tail * self.frames
        later_s = self._later_s(head, tail, r)
        if self.t_tail is not None:
            return (max(0.0, tail_s - (now - self.t_tail)) + later_s if tail > 0 else -1.0), partial
        if r <= 0:
            return -1.0, partial
        if self.window <= 0:        # still in the head
            head_left = 0.0
            if head > 0:
                head_left = head * self.frames - (now - self.t_clip if self.t_clip is not None else 0.0)
            else:
                partial = True
            denoise = sum(self.steps * r * f for f in self.wins) + self.overhead * (len(self.wins) - 1)
            return max(0.0, head_left) + denoise + tail_s + later_s, partial
        i = min(self.window, len(self.wins))
        running = max(0, self.steps - self.step_n) * r * self.wins[i - 1]
        after = sum(self.steps * r * f for f in self.wins[i:]) + self.overhead * (len(self.wins) - i)
        return running + after + tail_s + later_s, partial

    def clip_done(self, now: float) -> dict:
        """The clip is saved: its head, tail and r, half into what was kept for its size."""
        took = {}
        if self.frames > 0 and self.key:
            if self.t_clip is not None and self.t_denoise is not None:
                took["head"] = (self.t_denoise - self.t_clip) / self.frames
            if self.t_tail is not None:
                took["tail"] = (now - self.t_tail) / self.frames
            if self.samples:
                took["step"] = sum(self.samples) / len(self.samples)
        for kind, value in took.items():
            old = (self.rates[kind] or {}).get(self.key, 0)
            old = float(old) if isinstance(old, (int, float)) else 0.0
            self.rates[kind][self.key] = round(value if old <= 0 else 0.5 * old + 0.5 * value, 6)
        return self.rates


class JobStats(QObject):
    changed = Signal()
    _sigBar = Signal(str)
    _sigChunk = Signal(str, int, int, bool)
    _sigPerChunk = Signal(int, int)
    _sigFrames = Signal(int, int, int, int)
    _sigPhase = Signal(str, int, int)
    _sigMerge = Signal(str, float, str)
    _sigTail = Signal(float)
    _sigDc = Signal(str, object)
    _sigBatch = Signal(str, object)
    _sigBatchClip = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stage = ""
        self._rate = 0.0
        self._chunk = 0            # the chunk running (1-based), 0 = none yet
        self._chunks = 0
        self._done = 0
        self._unit = "Chunk"
        self._last_step: Optional[tuple] = None
        self._part = 0.0            # how far the running chunk is (0..1)
        self._step = 0              # the step bar's own count: iteration n of m
        self._steps = 0
        self._steps_in_chunk = 0    # Inpainting: steps seen since the chunk began
        self._per_chunk = 0         # Inpainting: steps a chunk takes (tile passes x steps)
        self._per_pass = 0          # ...and one tile pass (its bar's length): other bars are not steps
        self._frames = 0            # Inpainting: the clip's frames,
        self._frames_done = 0       # ...how many the finished chunks cover,
        self._range = (-1, -1)      # ...and the latest chunk's (from, to)
        self._phase = ""            # Inpainting: "inpaint", "render" (hi-res blend), "encode"
        self._render_done = 0       # ...frames the render has finished, of
        self._render_total = 0
        # Merging (user, 2026-09-27): the part of the clip running -- "grain"
        # (auto-match), "render", "split" (L/R) -- its percentage, and the clip
        self._merge_phase = ""
        self._merge_pct = 0.0
        self._merge_clip = ""
        self._merge_last = None     # (phase, whole percent): publish on a change only
        self._clock = time.monotonic
        self._eta_reset()
        self._eta_tail_per_frame = 0.0  # the previous clip's render + encode, per frame
        self._sigBar.connect(self._on_bar)
        self._sigChunk.connect(self._on_chunk)
        self._sigPerChunk.connect(self._on_per_chunk)
        self._sigFrames.connect(self._on_frames)
        self._sigPhase.connect(self._on_phase)
        self._sigMerge.connect(self._on_merge)
        self._sigTail.connect(self._on_tail)
        self._dc: Optional[DcEta] = None    # DepthCrafter's ETA, while a DepthCrafter job runs
        self._sigDc.connect(self._on_dc)
        self._batch_reset()
        self._sigBatch.connect(self._on_batch)
        self._sigBatchClip.connect(self._on_batch_clip)

    # ---- QML --------------------------------------------------------------------
    stage = Property(str, lambda self: self._stage, notify=changed)
    rate = Property(float, lambda self: self._rate, notify=changed)
    chunk = Property(int, lambda self: self._chunk, notify=changed)
    chunks = Property(int, lambda self: self._chunks, notify=changed)
    done = Property(int, lambda self: self._done, notify=changed)
    unit = Property(str, lambda self: self._unit, notify=changed)
    step = Property(int, lambda self: self._step, notify=changed)
    steps = Property(int, lambda self: self._steps, notify=changed)
    frames = Property(int, lambda self: self._frames, notify=changed)
    framesDone = Property(int, lambda self: self._frames_done, notify=changed)
    rangeFrom = Property(int, lambda self: self._range[0], notify=changed)
    rangeTo = Property(int, lambda self: self._range[1], notify=changed)
    phase = Property(str, lambda self: self._phase, notify=changed)
    renderDone = Property(int, lambda self: self._render_done, notify=changed)
    renderTotal = Property(int, lambda self: self._render_total, notify=changed)
    mergePhase = Property(str, lambda self: self._merge_phase, notify=changed)
    mergePercent = Property(float, lambda self: self._merge_pct, notify=changed)
    mergeClip = Property(str, lambda self: self._merge_clip, notify=changed)
    #: Inpainting: seconds until the clip is done, -1 while unknown; partial
    #: when the render + encode at its end is not in it yet (the first clip).
    eta = Property(float, lambda self: self._eta, notify=changed)
    etaPartial = Property(bool, lambda self: self._eta_partial, notify=changed)
    #: the whole batch: seconds until its last clip is done, -1 while unknown or with no
    #: clip after this one (partial as etaPartial); and how many clips come after this one
    batchEta = Property(float, lambda self: self._batch_eta, notify=changed)
    batchClips = Property(int, lambda self: len(self._batch), notify=changed)

    def _fraction(self) -> float:
        """How far through the clip: finished chunks, plus the running one's
        share of its steps -- so the ring climbs with every step, not only
        when a chunk lands."""
        if self._chunks <= 0:
            return 0.0
        running = self._part if self._done < self._chunk else 0.0
        return max(0.0, min(1.0, (self._done + running) / self._chunks))

    fraction = Property(float, _fraction, notify=changed)
    #: The running chunk's own progress (0..1); 1 once it has landed. Chunk 0
    #: is "none started yet" (a clip's start), not a landed one (audit
    #: 2026-09-28: the ring read full at the start of every Inpainting clip).
    part = Property(float, lambda self: (1.0 if self._chunks > 0 and self._chunk > 0 and self._done >= self._chunk
                                         else self._part), notify=changed)

    @Slot(str)
    def begin(self, stage: str) -> None:
        """A job starts on `stage`: the numbers are its from here."""
        self._stage = stage
        self._live = True
        self._rate = 0.0
        self._chunk = self._chunks = self._done = 0
        self._unit = "Window" if stage == "depthcrafter" else "Chunk"
        self._last_step = None
        self._part = 0.0
        self._steps_in_chunk = 0
        self._per_chunk = 0
        self._per_pass = 0
        self._step = self._steps = 0
        self._frames = self._frames_done = 0
        self._range = (-1, -1)
        self._phase = ""
        self._render_done = self._render_total = 0
        self._merge_phase, self._merge_pct, self._merge_clip, self._merge_last = "", 0.0, "", None
        self._eta_reset()
        self._eta_tail_per_frame = 0.0
        self._dc = DcEta(self._dc_rates_load()) if stage == "depthcrafter" else None
        self._batch_reset()
        self.changed.emit()

    def _clear_clip(self) -> None:
        """The clip's own numbers back to none: a new clip. Its bar read the clip before's 100% until
        its first step -- the video loading, the hi-res match (user, 2026-10-15)."""
        self._chunk = self._chunks = self._done = 0
        self._part = 0.0
        self._steps_in_chunk = 0
        self._last_step = None
        self._step = self._steps = 0
        self._frames = self._frames_done = 0
        self._range = (-1, -1)
        self._phase = ""
        self._render_done = self._render_total = 0

    @Slot(str)
    def clip_changed(self, stage: str) -> None:
        """The job moved on to another clip (Inpainting's clip name): until it reports, nothing done."""
        if stage != self._stage or (self._chunks == 0 and self._done == 0 and self._part == 0.0):
            return
        self._clear_clip()
        self._eta_reset()
        self.changed.emit()

    # ---- Inpainting's ETA ---------------------------------------------------------
    def _eta_reset(self) -> None:
        self._eta = -1.0
        self._eta_partial = False
        self._eta_t0 = None         # when the running chunk began (None: not timed)
        self._eta_sum = 0.0         # its steps' s/it, summed,
        self._eta_n = 0             # ...and counted
        self._eta_prev_mean = 0.0   # the last chunk's mean, until this one has a sample
        self._eta_first = 0.0       # this chunk's first step (a warm-up shows here)
        self._eta_chunk_s = 0.0     # the previous computed chunk, start to start
        self._eta_overhead = 0.0    # ...less its steps
        self._eta_tail_t0 = None    # the clip's last chunk landed
        self._eta_tail_frames = 0

    def _eta_mean(self) -> float:
        return self._eta_sum / self._eta_n if self._eta_n else self._eta_prev_mean

    def _eta_chunk_starts(self, index: int) -> None:
        now = self._clock()
        if index == 0:
            self._eta_t0 = None     # a new clip: the last one's chunk ran into its render
            self._eta_tail_t0 = None
        elif self._eta_t0 is not None and self._eta_n > 0:
            span = now - self._eta_t0
            mean = self._eta_mean()
            if not self._eta_first > 5 * mean:     # a warm-up chunk (compile) is no chunk time
                self._eta_chunk_s = span
                self._eta_overhead = max(0.0, span - mean * self._per_chunk)
        if index > 0:
            if self._eta_n:
                self._eta_prev_mean = self._eta_sum / self._eta_n
            self._eta_t0 = now
            self._eta_sum, self._eta_n = 0.0, 0
            self._eta_first = 0.0

    def _eta_update(self) -> None:
        self._eta_update_clip()
        self._batch_update()

    def _eta_update_clip(self) -> None:
        if self._stage == "depthcrafter" and self._dc is not None:
            self._eta, self._eta_partial = self._dc.eta(self._clock())
            return
        if self._stage != "inpainting":
            return
        tail = self._eta_tail_per_frame * self._frames
        self._eta_partial = tail <= 0
        if self._phase in ("render", "encode"):
            if tail > 0 and self._eta_tail_t0 is not None:
                self._eta = max(0.0, tail - (self._clock() - self._eta_tail_t0))
            else:
                self._eta = -1.0
            return
        mean = self._eta_mean()
        if self._chunks <= 0 or self._chunk <= 0 or mean <= 0 or self._per_chunk <= 0:
            self._eta = -1.0
            return
        last = self._chunk >= self._chunks
        steps_left = 0 if self._done >= self._chunk else max(0, self._per_chunk - self._steps_in_chunk)
        timed = self._eta_chunk_s > 0
        per_chunk = self._eta_chunk_s if timed else self._per_chunk * mean
        current = steps_left * mean + (self._eta_overhead if timed and not last else 0.0)
        self._eta = current + (self._chunks - self._chunk) * per_chunk + tail

    @Slot(str)
    def end(self, stage: str) -> None:
        """`stage`'s job is over: its numbers stay on show, but no bar is read
        for it any more (audit 2026-09-28: a later bar of another stage --
        a Splatting render after DepthCrafter -- moved them)."""
        if stage == self._stage:
            self._live = False

    # ---- the batch's ETA (user, 2026-10-21) --------------------------------------------
    @staticmethod
    def _batch_key(name: str) -> str:
        """A clip's name as both sides give it: the runner's (a basename, with or without its
        extension) and Upcoming Jobs' paths."""
        base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
        stem, dot, ext = base.rpartition(".")
        return (stem if dot and 1 <= len(ext) <= 4 else base).lower()

    def _batch_reset(self) -> None:
        self._batch = {}                # clips not reached yet: key -> frames
        self._batch_spent = 0.0         # the clips finished this run: seconds,
        self._batch_spent_frames = 0    # ...and their frames
        self._batch_t0 = None           # when the running clip started
        self._batch_frames = 0          # ...and its frames, once known
        self._batch_later = 0.0         # DepthCrafter's segments after the running one: in its ETA
        self._batch_eta = -1.0

    def batch_begin(self, stage: str, clips) -> None:
        """The run's clips, [(path or name, frames)], for `stage` (any thread; after begin)."""
        self._sigBatch.emit(str(stage), [(str(p), int(f or 0)) for p, f in clips])

    def batch_clip(self, stage: str, name: str) -> None:
        """`stage`'s job is on the clip `name` (any thread)."""
        self._sigBatchClip.emit(str(stage), str(name))

    @Slot(str, object)
    def _on_batch(self, stage: str, clips) -> None:
        if stage != self._stage:
            return
        self._batch = {}
        for p, f in clips:
            if f > 0:
                self._batch[self._batch_key(p)] = f
        self._batch_update()
        self.changed.emit()

    @Slot(str, str)
    def _on_batch_clip(self, stage: str, name: str) -> None:
        if stage != self._stage or not name or name == "N/A":
            return
        if self._batch.pop(self._batch_key(name), None) is not None:
            self._batch_update()
            self.changed.emit()

    def _batch_clip_started(self, frames: int = 0) -> None:
        """A clip (or a DepthCrafter segment) starts: the one before it is done, its time
        from its start to this one's. `frames`: this one's, when known already (Inpainting
        reports them first; DepthCrafter with its plan)."""
        now = self._clock()
        if self._batch_t0 is not None and self._batch_frames > 0:
            self._batch_spent += now - self._batch_t0
            self._batch_spent_frames += self._batch_frames
        self._batch_t0, self._batch_frames, self._batch_later = now, max(0, int(frames)), 0.0

    def _batch_update(self) -> None:
        left = sum(self._batch.values())
        frames = self._batch_frames
        if left <= 0 or self._eta < 0 or self._batch_t0 is None or frames <= 0:
            self._batch_eta = -1.0
            return
        took = self._batch_spent + (self._clock() - self._batch_t0) + self._eta
        self._batch_eta = self._eta + took / (self._batch_spent_frames + frames + self._batch_later) * left

    # ---- feeds (any thread) -----------------------------------------------------
    def feed_bar(self, text: str) -> None:
        """A tqdm bar redrew (StreamTee). Cheap; handed to the UI thread."""
        if not getattr(self, "_live", False):
            return
        if self._stage == "merging":
            self._feed_merge(text)
        elif self._stage and "/" in text:
            self._sigBar.emit(text)

    def _feed_merge(self, text: str) -> None:
        m = _MERGE_BAR.match(text)
        if not m:
            return
        label, pct, tail = m.group(1), float(m.group(2)), m.group(3).strip()
        if label.startswith("Grain"):
            phase, clip = "grain", ("" if tail.startswith("Complete") else tail)
        elif label.startswith("Encoding"):
            phase, clip = "render", label[len("Encoding"):].strip()
        else:
            phase, clip = "split", ""
        key = (phase, int(pct))
        if key == self._merge_last:             # the bar redraws far more often than it moves
            return
        self._merge_last = key
        self._sigMerge.emit(phase, pct, clip)

    def chunk_event(self, stage: str, index: int, total: int, finished: bool) -> None:
        self._sigChunk.emit(stage, int(index), int(total), bool(finished))

    def frames_event(self, total: int, done: int, first: int, last: int) -> None:
        """Inpainting: the clip's frames, how many are inpainted, and the
        latest chunk's frames (-1, -1: none yet)."""
        self._sigFrames.emit(int(total), int(done), int(first), int(last))

    def tail_rate(self, seconds_per_frame: float) -> None:
        """Inpainting: what the hi-res blend + encode take per frame (0: not known yet)."""
        self._sigTail.emit(float(seconds_per_frame))

    @Slot(float)
    def _on_tail(self, seconds_per_frame: float) -> None:
        self._eta_tail_per_frame = max(0.0, seconds_per_frame)
        self._eta_update()
        self.changed.emit()

    # ---- DepthCrafter's ETA (DcEta; user, 2026-10-12) ---------------------------------
    _DC_RATES_OWNER = "depthcrafter_eta"
    _DC_RATE_KEYS = {"head": "head_seconds_per_frame", "tail": "tail_seconds_per_frame",
                     "step": "step_seconds_per_frame"}

    def dc_event(self, kind: str, data: Any = None) -> None:
        """DepthCrafter, from its worker (DepthCrafterTimeline): "clip" (a clip starts),
        "plan" ({frames, steps, window, overlap, key}), "done" (the clip is saved)."""
        self._sigDc.emit(str(kind), data)

    def _dc_rates_load(self) -> dict:
        try:
            from studio import settings_store
            saved = settings_store.load(self._DC_RATES_OWNER)
        except Exception:
            saved = {}
        return {k: dict(saved.get(name) or {}) for k, name in self._DC_RATE_KEYS.items()}

    @Slot(str, object)
    def _on_dc(self, kind: str, data: Any) -> None:
        dc = self._dc
        if dc is None or self._stage != "depthcrafter":
            return
        now = self._clock()
        if kind == "clip":
            dc.clip_started(now)
            self._batch_clip_started()
            self._clear_clip()      # not the clip before's full bar until its first window
        elif kind == "plan" and isinstance(data, dict):
            dc.plan(data.get("frames", 0), data.get("steps", 1), data.get("window", 0),
                    data.get("overlap", 0), data.get("key", ""))
            self._batch_frames = dc.frames
            self._batch_later = dc.later_frames()
        elif kind == "segments" and isinstance(data, (list, tuple)):
            dc.segments_planned(list(data))
        elif kind == "done":
            rates = dc.clip_done(now)
            try:
                from studio import settings_store
                settings_store.save(self._DC_RATES_OWNER, {name: rates[k] for k, name in self._DC_RATE_KEYS.items()})
            except Exception:
                logger.debug("Could not keep DepthCrafter's ETA rates", exc_info=True)
        self._eta_update()
        self.changed.emit()

    def phase_event(self, phase: str, done: int = 0, total: int = 0) -> None:
        """Inpainting: which part of the clip is running, and for the render
        how many of its frames are finished."""
        self._sigPhase.emit(str(phase), int(done), int(total))

    def steps_per_chunk(self, n: int, per_pass: int = 0) -> None:
        """Inpainting: the steps one chunk takes (tile passes x diffusion steps)."""
        self._sigPerChunk.emit(int(n), int(per_pass))

    @Slot(int, int)
    def _on_per_chunk(self, n: int, per_pass: int = 0) -> None:
        self._per_chunk = max(0, n)
        self._per_pass = max(0, per_pass)

    @Slot(str, float, str)
    def _on_merge(self, phase: str, pct: float, clip: str) -> None:
        if phase != self._merge_phase:
            self._merge_clip = ""
        self._merge_phase, self._merge_pct = phase, max(0.0, min(100.0, pct))
        if clip:
            self._merge_clip = clip
        self.changed.emit()

    @Slot(str, int, int)
    def _on_phase(self, phase: str, done: int, total: int) -> None:
        self._phase, self._render_done, self._render_total = phase, max(0, min(done, total)), max(0, total)
        self._eta_update()
        self.changed.emit()

    @Slot(int, int, int, int)
    def _on_frames(self, total: int, done: int, first: int, last: int) -> None:
        if self._stage == "inpainting" and done == 0 and (first, last) == (-1, -1):
            self._batch_clip_started(total)     # a clip starts: InpaintingChunks.process_single_video
        self._frames, self._frames_done, self._range = total, min(done, total), (first, last)
        self.changed.emit()

    # ---- UI thread ------------------------------------------------------------
    @Slot(str)
    def _on_bar(self, text: str) -> None:
        if not getattr(self, "_live", False):
            return
        publish = False
        w = _WINDOW.search(text)
        m = _STEP.search(text)
        if w and self._stage == "depthcrafter":
            a, b = int(w.group(1)), int(w.group(2))
            # The next clip's window can carry the same label ("Window 1/1"
            # after "Window 1/1"): its steps starting again mark it new (user,
            # 2026-10-05: the last clip's bar sat at 100%).
            c = _COUNT.search(text)
            restarted = c is not None and (a, b) == (self._chunk, self._chunks) and int(c.group(1)) < self._step
            if (a, b) != (self._chunk, self._chunks) or restarted:
                self._chunk, self._chunks, self._done = a, b, a - 1
                self._part = 0.0
                if self._dc is not None:
                    self._dc.window_started(a, self._clock())
                publish = True
        if m and self._stage == "inpainting" and int(m.group(2)) != self._per_pass:
            m = None                                # not a diffusion pass: a loading bar (or before the clip)
        if m:
            n, total = int(m.group(1)), int(m.group(2))
            value = float(m.group(3))
            if m.group(4) == "it/s":
                value = 1.0 / value if value > 0 else 0.0
            key = (n, total, text[: m.start()])
            # One sample per completed step: tqdm redraws the same step many times.
            if (n, total) != (self._step, self._steps):
                self._step, self._steps = n, total
                publish = True
            if n > 0 and value > 0 and key != self._last_step:
                self._last_step = key
                self._rate = value
                if self._stage == "depthcrafter" and w:
                    self._part = n / total if total else 0.0
                    if self._dc is not None:
                        self._dc.step(n, total, value, self._clock())
                elif self._per_chunk > 0:
                    self._steps_in_chunk += 1
                    self._part = min(1.0, self._steps_in_chunk / self._per_chunk)
                    if n == 1 and not self._eta_first:
                        self._eta_first = value
                    if n >= 2 or total == 1:            # a pass's first step carries its setup
                        self._eta_sum += value
                        self._eta_n += 1
                publish = True
            if w and self._stage == "depthcrafter" and n >= total and self._done < self._chunk:
                self._done = self._chunk
                publish = True
        if publish:
            self._eta_update()
            self.changed.emit()

    @Slot(str, int, int, bool)
    def _on_chunk(self, stage: str, index: int, total: int, finished: bool) -> None:
        if stage != self._stage:
            return
        # 0: the clip's chunk count is unknown (bug sweep A2) -- no fraction, rather
        # than one that reads 100% after each chunk and drops when the next starts
        self._chunks = max(total, index) if total > 0 else 0
        if index != self._chunk:
            self._steps_in_chunk = 0
            self._part = 0.0
            if not finished:
                self._eta_chunk_starts(index)
        self._chunk = index
        self._done = index if finished else max(0, index - 1)
        if finished and index >= self._chunks:
            self._eta_tail_t0 = self._clock()
            self._eta_tail_frames = self._frames
        self._eta_update()
        self.changed.emit()


class InpaintingChunks:
    """For one Inpainting V1 run: wraps the engine's per-clip and per-chunk
    calls (module-level names the generated engine looks up at call time) and
    reports each chunk to JobStats. ``restore()`` puts them back."""

    def __init__(self, engine_module, stats: JobStats):
        self._E = engine_module
        self._stats = stats
        self._index = 0
        self._total = 0
        self._frames = 0            # the clip's frames, as the loop counts them
        self._chunk_len = 0
        self._stride = 1
        self._reverse = False
        self._done_frames = 0
        self._end = 0
        self._range = (-1, -1)
        self._placed = False        # a log line placed the chunk about to run
        self._tail_key = "lowres"   # the clip's hi-res size ("1920x1038"), or no blend
        self._tail_t0 = None        # its last chunk landed
        self._tails = None          # {key: seconds a frame}, read on first use
        self._counted = True        # the clip's frames are known (else: progress unknown)
        self._handler = None
        self._handler_on: List[logging.Logger] = []
        # The numbers are a nicety: if what they hang on is not there (a
        # stand-in engine, a renamed function), step aside -- never stop a job.
        eng_cls = getattr(engine_module, "InpaintingEngine", None)
        orig_tiled = getattr(engine_module, "spatial_tiled_process", None)
        orig_single = getattr(eng_cls, "process_single_video", None) if eng_cls is not None else None
        self._saved = None
        if orig_tiled is None or orig_single is None:
            logger.debug("Inpainting chunk timeline: nothing to hook, skipped")
            return
        self._saved = (orig_tiled, orig_single)
        E, me = engine_module, self

        def process_single_video(eng, *a, **kw):
            try:
                me._index = 0
                me._placed = False
                me._done_frames = me._end = 0
                chunk, overlap = int(kw.get("frames_chunk", 23)), int(kw.get("overlap", 3))
                me._chunk_len, me._stride = chunk, max(1, chunk - overlap)
                # Reverse runs the loop over the reversed clip: show real frame numbers
                me._reverse = kw.get("inpaint_direction") == "Reverse"
                me._frames = me._count_frames(kw.get("input_video_path") or (a[1] if len(a) > 1 else ""),
                                              kw.get("process_length", -1))
                me._total = chunk_count(me._frames, chunk, overlap)
                me._counted = me._frames > 0
                stats.frames_event(me._frames, 0, -1, -1)
                stats.phase_event("inpaint")
                # A chunk is tile_num x tile_num passes of the diffusion steps.
                tiles = max(1, int(kw.get("tile_num", 1) or 1))
                steps = max(1, int(kw.get("num_inference_steps", 1) or 1))
                stats.steps_per_chunk(tiles * tiles * steps, steps)
                stats.chunk_event("inpainting", 0, me._total, False)
                me._tail_key, me._tail_t0 = "lowres", None
                stats.tail_rate(me._tail_for("lowres"))     # the hi-res line, if any, comes next
            except Exception:
                logger.debug("Could not count the clip's chunks", exc_info=True)
            return orig_single(eng, *a, **kw)

        def spatial_tiled_process(*a, **kw):
            try:
                if not me._placed:
                    # no log line seen: the chunk after the last one
                    start = me._index * me._stride
                    me._place(start, min(start + me._chunk_len, me._frames or start + me._chunk_len))
                me._placed = False
            except Exception:
                logger.debug("Could not place the chunk", exc_info=True)
            out = orig_tiled(*a, **kw)
            try:
                me._finish()
            except Exception:
                logger.debug("Could not finish the chunk", exc_info=True)
            return out

        class _ChunkLines(logging.Handler):
            """Reads the engine's chunk lines; never lets an error out."""
            def emit(self, record):
                try:
                    msg = record.getMessage()
                    m = _RUN_CHUNK.search(msg)
                    if m:
                        me._place(int(m.group(1)), int(m.group(2)))
                        return
                    m = _CACHED_CHUNK.search(msg)
                    if m:
                        me._place(int(m.group(1)), int(m.group(2)))
                        me._placed = False
                        me._finish()
                        return
                    m = _HIRES_RES.search(msg)
                    if m:
                        me._tail_key = f"{m.group(1)}x{m.group(2)}"
                        stats.tail_rate(me._tail_for(me._tail_key))
                        return
                    m = _HIRES_CHUNK.search(msg)
                    if m:
                        # this chunk starts: every frame before it is rendered
                        first, last = int(m.group(3)), int(m.group(4))
                        total = max(me._frames, last)
                        stats.phase_event("render", first, total)
                        stats.frames_event(total, me._done_frames, first, last)
                        return
                    if _HIRES_DONE.search(msg):
                        total = max(me._frames, 1)
                        stats.phase_event("render", total, total)
                except Exception:
                    pass

        orig_encode = getattr(engine_module, "encode_frames_to_mp4", None)

        def encode_frames_to_mp4(*a, **kw):
            try:
                stats.phase_event("encode")
            except Exception:
                pass
            ok = orig_encode(*a, **kw)
            try:
                if ok:
                    me._tail_done()
            except Exception:
                logger.debug("Could not time the clip's render", exc_info=True)
            return ok

        E.spatial_tiled_process = spatial_tiled_process
        E.InpaintingEngine.process_single_video = process_single_video
        if orig_encode is not None:
            E.encode_frames_to_mp4 = encode_frames_to_mp4
        self._saved_encode = orig_encode
        # The engine logs to "inpainting.v2" (and a child that propagates to it).
        eng_log = getattr(engine_module, "logger", None)
        if isinstance(eng_log, logging.Logger):
            self._handler = _ChunkLines(logging.INFO)
            self._handler_on = [eng_log]
            hk = getattr(engine_module, "housekeeping_log", None)
            if isinstance(hk, logging.Logger) and not hk.propagate:
                self._handler_on.append(hk)
            for lg in self._handler_on:
                lg.addHandler(self._handler)

    def _place(self, start: int, end: int) -> None:
        """The chunk over the loop's frames start..end is starting."""
        self._placed = True
        self._index = start // self._stride + 1
        self._end = end
        self._frames = max(self._frames, end)
        if self._counted:
            self._total = max(self._total, self._index)
        self._range = (self._frames - end, self._frames - start) if self._reverse else (start, end)
        self._stats.chunk_event("inpainting", self._index, self._total, False)
        self._stats.frames_event(self._shown_frames(), self._done_frames, *self._range)

    def _finish(self) -> None:
        """That chunk is inpainted: every frame up to its end is done."""
        if self._total and self._index >= self._total:
            self._tail_t0 = time.monotonic()      # the blend + encode start here
        self._done_frames = max(self._done_frames, self._end)
        self._stats.chunk_event("inpainting", self._index, self._total, True)
        self._stats.frames_event(self._shown_frames(), self._done_frames, *self._range)

    def _shown_frames(self) -> int:
        """The clip's frames for the "n / total frames inpainted" line: 0 (no
        line) when they could not be counted -- the count grown from the chunks
        so far would read as done after every chunk (bug sweep A2)."""
        return self._frames if self._counted else 0

    _TAILS_OWNER = "inpainting_eta"

    def _tail_for(self, key: str) -> float:
        if self._tails is None:
            try:
                from studio import settings_store
                self._tails = dict(settings_store.load(self._TAILS_OWNER).get("tail_seconds_per_frame", {}))
            except Exception:
                self._tails = {}
        try:
            return max(0.0, float(self._tails.get(key, 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _tail_done(self) -> None:
        """The clip is encoded: its blend + encode per frame, half into what was kept for its size."""
        if self._tail_t0 is None or self._frames <= 0:
            return
        took = (time.monotonic() - self._tail_t0) / self._frames
        self._tail_t0 = None
        old = self._tail_for(self._tail_key)
        new = took if old <= 0 else 0.5 * old + 0.5 * took
        self._tails[self._tail_key] = round(new, 5)
        from studio import settings_store
        settings_store.save(self._TAILS_OWNER, {"tail_seconds_per_frame": self._tails})
        self._stats.tail_rate(new)

    @staticmethod
    def _count_frames(path: str, process_length: Any) -> int:
        frames = 0
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
        except Exception:
            frames = 0
        if frames <= 0:
            frames = InpaintingChunks._probe_frames(path)  # some containers cv2 can't count
        try:
            pl = int(process_length)
            if pl > 0:
                frames = min(frames, pl) if frames else pl
        except (TypeError, ValueError):
            pass
        return frames

    @staticmethod
    def _probe_frames(path: str) -> int:
        """ffprobe's frame count for the first video stream: its nb_frames, else
        duration x frame rate. 0 when neither is there."""
        import json
        import subprocess
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=nb_frames,duration,r_frame_rate:format=duration", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            info = json.loads(out or "{}")
            stream = (info.get("streams") or [{}])[0]
            n = int(stream.get("nb_frames") or 0)
            if n > 0:
                return n
            num, _, den = str(stream.get("r_frame_rate") or "0/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            duration = float(stream.get("duration") or (info.get("format") or {}).get("duration") or 0)
            return int(round(duration * fps)) if fps > 0 and duration > 0 else 0
        except Exception:
            return 0

    def restore(self) -> None:
        if self._handler is not None:
            for lg in self._handler_on:
                lg.removeHandler(self._handler)
            self._handler = None
        if self._saved is not None:
            self._E.spatial_tiled_process, self._E.InpaintingEngine.process_single_video = self._saved
            if getattr(self, "_saved_encode", None) is not None:
                self._E.encode_frames_to_mp4 = self._saved_encode


class DepthCrafterTimeline:
    """For one DepthCrafter run: wraps DepthCrafterDemo's per-clip call (a clip
    starts; it is saved) and its inference call (the clip's frames, steps, window,
    overlap and size -- a segment is one window of its own frames) and tells
    JobStats, for DcEta. ``restore()`` puts them back; a renamed method just
    means no ETA, never a stopped job."""

    def __init__(self, logic_module, stats: JobStats):
        import inspect
        self._cls = getattr(logic_module, "DepthCrafterDemo", None)
        infer = getattr(self._cls, "_internal_infer", None)
        perform = getattr(self._cls, "_perform_inference", None)
        self._saved = None
        if infer is None or perform is None:
            logger.debug("DepthCrafter ETA: nothing to hook, skipped")
            return
        self._saved = (infer, perform)
        sig = inspect.signature(perform)

        def _internal_infer(demo, *a, **kw):
            stats.dc_event("clip")
            try:
                return infer(demo, *a, **kw)
            finally:
                stats.dc_event("done")

        def _perform_inference(demo, *a, **kw):
            try:
                b = sig.bind(demo, *a, **kw).arguments
                frames = int(b["actual_frames_to_process"].shape[0])
                window, overlap = int(b["pipe_call_window_size"]), int(b["pipe_call_overlap"])
                if b.get("segment_job_info"):
                    window, overlap = frames, 0         # _perform_inference runs a segment whole
                stats.dc_event("plan", {"frames": frames, "steps": int(b["num_denoising_steps"]),
                                        "window": window, "overlap": overlap,
                                        "key": f"{int(b['actual_processed_width'])}x{int(b['actual_processed_height'])}"})
            except Exception:
                logger.debug("DepthCrafter ETA: could not read the clip's plan", exc_info=True)
            return perform(demo, *a, **kw)

        self._cls._internal_infer = _internal_infer
        self._cls._perform_inference = _perform_inference

    def restore(self) -> None:
        if self._saved is not None:
            self._cls._internal_infer, self._cls._perform_inference = self._saved
            self._saved = None


class _NvMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class VramMonitor(QObject):
    """GPU memory in use (the whole device, every process), from NVML."""

    changed = Signal()
    INTERVAL_MS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._used = 0.0
        self._total = 0.0
        self._coarse = False
        self._handle = None
        self._nvml = None
        self._ok = self._init()
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self.poll)
        if self._ok:
            self.poll()
            self._timer.start()

    def _init(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            nv = ctypes.WinDLL("nvml.dll")
            if nv.nvmlInit_v2() != 0:
                return False
            handle = ctypes.c_void_p()
            if nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                return False
            self._nvml, self._handle = nv, handle
            return True
        except Exception:
            logger.debug("NVML not available; no VRAM readout", exc_info=True)
            return False

    @Slot()
    def poll(self) -> None:
        if not self._ok:
            return
        mem = _NvMemory()
        if self._nvml.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem)) != 0:
            return
        self._publish(mem.used / 2 ** 30, mem.total / 2 ** 30)

    def _publish(self, used: float, total: float) -> None:
        """Only a visible change -- the readout shows one decimal -- and while
        a job runs (coarse, bound to Theme.calm) only a 0.5 GB one: a job's
        memory moves all the time, and each change redraws the window on the
        GPU the job is using (audit 2026-09-28 O7)."""
        moved = (abs(used - self._used) >= 0.5 if self._coarse      # 0.5 GB since the last shown
                 else round(used, 1) != round(self._used, 1))
        if moved or round(total, 1) != round(self._total, 1):
            self._used, self._total = used, total
            self.changed.emit()

    def _set_coarse(self, on: bool) -> None:
        self._coarse = bool(on)

    available = Property(bool, lambda self: self._ok, constant=True)
    coarse = Property(bool, lambda self: self._coarse, _set_coarse)
    used = Property(float, lambda self: self._used, notify=changed)
    total = Property(float, lambda self: self._total, notify=changed)

    def shutdown(self) -> None:
        self._timer.stop()
        if self._ok:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
