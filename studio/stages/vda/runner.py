"""runner.py -- Video Depth Anything's batch: each clip through the model, written where Splatting reads it.

Two passes a clip, so a long clip needs no more memory than a short one:

1. depth: the frames go through the model on the card (decoded by ffmpeg, resized and normalised on
   the card) and each frame's raw depth, scaled to the map's size there too, goes into a float16
   file beside the output (.<clip>.vda-raw.npy) with its lowest and highest value noted;
2. encode: core.vda.normalize turns those into each frame's range (the whole clip's, or iw3's EMA),
   and the frames go to 10-bit HEVC as DepthCrafter's do (tv range, 0..1 on codes 64..940) --
   lossless, through the Depth Map Editor's DepthWriter -- under a .part name, put in place whole
   (core.vda.output.publish, which moves a DepthCrafter map of the clip into DepthCrafter/ first).

The map's width is a multiple of 16 (core.vda.model.output_size): FFmpeg 8's swscale tick bug.

Nothing here imports Qt. Torch and the model load only when a batch starts.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from studio.outcome import Outcome, is_out_of_memory

logger = logging.getLogger("vda")

OOM_HINT = ("It ran out of graphics memory. Pick a smaller Model or a lower Input Size, or use Online mode, "
            "then run it again.")

#: Frames decoded and sent to the card at a time, and encoded at a time.
READ_BATCH = 8
ENCODE_BATCH = 16

VIDEO_TYPES = ("video_file", "single_video_file")


class Hooks:
    """What the runner reports. Called on the worker thread."""

    def status(self, text: str) -> None:
        pass

    def progress(self, done: int, total: int) -> None:
        """Clips: `done` of `total` finished."""

    def clip(self, filename: str, resolution: str, frames: str) -> None:
        pass

    def frames(self, done: int, total: int, phase: str) -> None:
        """The clip: `done` of `total` frames through `phase` ("depth" or "encode")."""


class Stopped(Exception):
    """Stop (now) during a clip."""


class VdaRunner:
    def __init__(self, opts: Dict[str, Any], hooks: Hooks, stop_event: threading.Event,
                 move_originals: bool = True, stop_now: Optional[threading.Event] = None):
        self.opts = opts
        self.hooks = hooks
        self.stop = stop_event
        self.stop_now = stop_now or threading.Event()
        self.move_originals = move_originals
        self.outcome = Outcome()
        #: what each finished clip made: {source, map, set_aside} (set_aside: where the DepthCrafter map went)
        self.made: List[Dict[str, str]] = []

    # ---- the batch -------------------------------------------------------------------------

    def run_batch(self, specs: List[Dict[str, str]]) -> bool:
        from core.vda import model as M

        videos = [s for s in specs if s.get("type") in VIDEO_TYPES]
        for s in specs:
            if s.get("type") not in VIDEO_TYPES:
                logger.warning("Video Depth Anything takes videos only; skipping %s.", os.path.basename(s["path"]))
                self.outcome.skipped += 1
        total = len(videos)
        if not total:
            self.outcome.note = "No videos to process (image sequences are DepthCrafter's)."
            return True
        o = self.opts
        self.hooks.status(f"Loading Video Depth Anything {o['model_size']}...")
        try:
            model = M.load(o["model_size"], "offline" if o["run_mode"] == "Offline" else "online")
        except M.ModelsMissing as e:
            self.outcome.error = str(e)
            logger.error("%s", e)
            return False
        except Exception as e:
            logger.exception("Video Depth Anything could not load")
            self.outcome.error = f"Video Depth Anything could not load: {e}"
            if is_out_of_memory(e):
                self.outcome.hint = OOM_HINT
            return False
        try:
            for i, spec in enumerate(videos):
                if self.stop.is_set():
                    self.outcome.halted = True
                    break
                self.hooks.progress(i, total)
                self.hooks.status(f"Processing {i + 1} of {total}: {os.path.basename(spec['path'])}")
                ok = self.process_clip(model, spec)
                if ok is None:              # stopped mid-clip: the source stays where it is
                    break
                if ok:
                    self.outcome.done += 1
                else:
                    self.outcome.failed += 1
                if self.move_originals:
                    self._move_original(spec["path"], "finished" if ok else "failed")
            self.hooks.progress(self.outcome.done + self.outcome.failed, total)
        finally:
            M.release(model)
            model = None
        return True

    # ---- one clip ----------------------------------------------------------------------------

    def process_clip(self, model, spec: Dict[str, str]) -> Optional[bool]:
        """True: the map is in place; False: the clip failed; None: stopped during it."""
        import numpy as np
        import torch
        from core.depthedit.depth_io import probe
        from core.vda import model as M

        path, base = spec["path"], spec["basename"]
        o = self.opts
        t0 = time.monotonic()
        raw_path = part = None
        raw = None
        writer = None
        try:
            info = probe(path)
            n = info.num_frames
            if int(o.get("process_length", -1)) > 0:
                n = min(n, int(o["process_length"]))
            if n <= 0:
                raise ValueError("no frames")
            size = M.infer_size(info.height, info.width, int(o["input_size"]))
            oh, ow = M.output_size(*size)
            self.hooks.clip(os.path.basename(path), f"{info.width}x{info.height}", str(n))
            logger.info("%s: %d frames, %dx%d, run at %dx%d, map %dx%d (%s, %s).", os.path.basename(path), n,
                        info.width, info.height, size[1], size[0], ow, oh, o["model_size"], o["run_mode"])
            out_dir = o["output_dir"]
            os.makedirs(out_dir, exist_ok=True)
            device = next(model.parameters()).device
            use_amp = bool(o.get("use_fp16", True)) and device.type == "cuda"

            # -- pass 1: depth -------------------------------------------------------------------
            raw_path = os.path.join(out_dir, f".{base}.vda-raw.npy")
            raw = np.lib.format.open_memmap(raw_path, mode="w+", dtype=np.float16, shape=(n, oh, ow))
            lows: List[float] = []
            highs: List[float] = []
            tick = _Ticker(self.hooks, n, "depth", os.path.basename(path))

            def keep(d):
                d = d.reshape(1, 1, *d.shape[-2:]).float()
                if tuple(d.shape[-2:]) != (oh, ow):
                    d = torch.nn.functional.interpolate(d, size=(oh, ow), mode="bilinear", align_corners=False)
                d = d[0, 0]
                k = len(lows)
                lows.append(float(d.amin()))
                highs.append(float(d.amax()))
                raw[k] = d.to(torch.float16).cpu().numpy()
                tick(k + 1)

            if o["run_mode"] == "Offline":
                frames = _read_all(path, info, n, size, self.stop_now)
                if frames is None:
                    raise Stopped()
                depths = M.run_offline(model, frames, int(o["input_size"]), use_amp=use_amp)
                del frames
                for d in depths:
                    keep(torch.from_numpy(np.ascontiguousarray(d)).to(device))
            else:
                M.run_online(model, _batches(path, info, n, device, self.stop_now), size, keep, use_amp=use_amp,
                             stopped=self.stop_now.is_set)
            if self.stop_now.is_set():
                raise Stopped()
            got = len(lows)
            if got == 0:
                raise ValueError("the model gave no frames")
            if got < n:
                logger.warning("%s: %d of %d frames could be read; the map has %d.", os.path.basename(path), got, n, got)

            # -- pass 2: encode ----------------------------------------------------------------
            from core.common.partial_output import discard, part_path  # noqa: F401
            from core.depthedit.depth_io import ClipInfo, DepthWriter
            from core.vda import normalize as N
            from core.vda import output as OUT

            final = OUT.map_path(out_dir, base, o.get("output_suffix") or "_depth")
            part = part_path(final)
            ranges = N.ranges(o["normalise_mode"], lows, highs, float(o["ema_decay"]), int(o["ema_buffer"]))
            writer = DepthWriter(part, ClipInfo(path=final, width=ow, height=oh, num_frames=got, fps=info.fps,
                                                pix_fmt="yuv420p10le", full_range=False),
                                 metadata={"comment": OUT.TAG})
            tick = _Ticker(self.hooks, got, "encode", os.path.basename(path))
            for a in range(0, got, ENCODE_BATCH):
                if self.stop_now.is_set():
                    raise Stopped()
                b = min(got, a + ENCODE_BATCH)
                block = torch.from_numpy(np.asarray(raw[a:b])).to(device).float()
                ks = torch.tensor([N.scale(*ranges[i])[0] for i in range(a, b)], device=device).view(-1, 1, 1)
                offs = torch.tensor([N.scale(*ranges[i])[1] for i in range(a, b)], device=device).view(-1, 1, 1)
                codes = (block * ks + offs).round_().clamp_(0, 1023).to(torch.int32).cpu().numpy().astype(np.uint16)
                for c in codes:
                    writer.write(c)
                tick(b)
            writer.close()
            writer = None
            moved = OUT.publish(part, final)
            part = None
            self.made.append({"source": path, "map": final, "set_aside": moved or ""})
            took = time.monotonic() - t0
            logger.info("%s: depth map saved to %s in %.1f s (%.1f fps)%s.", os.path.basename(path), final, took,
                        got / took if took > 0 else 0.0,
                        f"; the DepthCrafter map it replaced is in {os.path.dirname(moved)}" if moved else "")
            return True
        except Stopped:
            logger.info("Stopped during %s; its map was not written.", os.path.basename(path))
            return None
        except Exception as e:
            logger.exception("Video Depth Anything failed on %s", os.path.basename(path))
            if is_out_of_memory(e):
                self.outcome.hint = OOM_HINT
            return False
        finally:
            if writer is not None:
                writer.abort()
            if part:
                from core.common.partial_output import discard
                discard(part)
            if raw is not None:
                del raw
            if raw_path and os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except OSError:
                    logger.warning("Could not remove %s", raw_path)
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # ---- finished / failed (DepthCrafter's _move_original_source) -----------------------------

    def _move_original(self, current: str, target_subfolder: str) -> None:
        try:
            field = self.opts["input_dir_or_file_var"]
            root = field if os.path.isdir(field) else os.path.dirname(field if os.path.isfile(field) else current)
            dest_dir = os.path.join(root, target_subfolder)
            os.makedirs(dest_dir, exist_ok=True)
            name = os.path.basename(current)
            dest = os.path.join(dest_dir, name)
            if os.path.exists(dest):
                b, ext = os.path.splitext(name)
                dest = os.path.join(dest_dir, f"{b}{time.strftime('_%Y%m%d%H%M%S')}{ext}")
            if os.path.exists(current):
                shutil.move(current, dest)
        except Exception:
            logger.exception("Could not move %s to %s", current, target_subfolder)


class _Ticker:
    """Frame progress, at most twice a second (nothing redraws continuously during a job): to the hooks (the
    page's bar), and as a progress row in the console's Depth Map tab (user, 2026-10-25: "add vda job progress
    to the depth map tab in the console") -- a '\r' bar on stderr, which the console shows live in the running
    job's tab (log_model.StreamTee) and keeps as a line when the pass ends."""

    LABELS = {"depth": "VDA depth", "encode": "VDA encode"}

    def __init__(self, hooks: Hooks, total: int, phase: str, name: str = ""):
        self.hooks, self.total, self.phase, self.name = hooks, total, phase, name
        self.last = 0.0
        self.t0 = time.monotonic()
        hooks.frames(0, total, phase)
        self._bar(0)

    def __call__(self, done: int) -> None:
        now = time.monotonic()
        if done >= self.total or now - self.last >= 0.5:
            self.last = now
            self.hooks.frames(done, self.total, self.phase)
            self._bar(done)

    def bar_text(self, done: int) -> str:
        took = time.monotonic() - self.t0
        fps = done / took if took > 0 and done else 0.0
        pct = 100 * done // self.total if self.total else 0
        left = f", {_clock((self.total - done) / fps)} left" if fps > 0 and done < self.total else ""
        speed = f", {fps:.1f} fps" if fps > 0 else ""
        clip = f" {self.name}" if self.name else ""
        # cli_utils.draw_progress_bar's form, which the console draws as a bar (log_model.parse_bar)
        fill = 20 * done // self.total if self.total else 0
        bar = "█" * fill + "-" * (20 - fill)
        return f"{self.LABELS.get(self.phase, self.phase)}{clip} |{bar}| {pct}% {done}/{self.total} frames{speed}{left}"

    def _bar(self, done: int) -> None:
        try:
            sys.stderr.write("\r" + self.bar_text(done) + ("\n" if done >= self.total else ""))
            sys.stderr.flush()
        except Exception:
            pass                # no console (a bare pythonw): the page's bar has it


def _clock(seconds: float) -> str:
    s = int(round(max(0.0, seconds)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _reader(path: str, info, n: int, width: int, height: int):
    from core.common.video_io import FFmpegRGBPipeReader
    return FFmpegRGBPipeReader(path, width, height, info.fps_float, n, in_range="pc" if info.full_range else "tv")


def _batches(path: str, info, n: int, device, stop_now: threading.Event):
    """The clip's frames as (B, 3, H, W) uint8 on `device`, decoded at their own size."""
    import torch

    reader = _reader(path, info, n, info.width, info.height)
    try:
        for a in range(0, n, READ_BATCH):
            if stop_now.is_set():
                return
            try:
                arr = reader.get_batch(list(range(a, min(n, a + READ_BATCH)))).asnumpy()
            except EOFError:
                logger.warning("%s ended at frame %d.", os.path.basename(path), a)
                return
            if len(arr) == 0:
                return
            yield torch.from_numpy(arr).to(device, non_blocking=True).permute(0, 3, 1, 2)
    finally:
        reader.close()


def _read_all(path: str, info, n: int, size, stop_now: threading.Event):
    """Offline: the whole clip in memory, decoded straight at the size it runs at. None: stopped."""
    import numpy as np

    h, w = size
    reader = _reader(path, info, n, w, h)
    out = []
    try:
        for a in range(0, n, READ_BATCH):
            if stop_now.is_set():
                return None
            try:
                out.append(reader.get_batch(list(range(a, min(n, a + READ_BATCH)))).asnumpy())
            except EOFError:
                break
    finally:
        reader.close()
    return np.concatenate(out) if out else np.empty((0, h, w, 3), np.uint8)
