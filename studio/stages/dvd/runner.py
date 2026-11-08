"""runner.py -- DVD's batch: each clip through the model, written where Splatting reads it.

VDA's runner with DVD's first pass (studio/stages/vda/runner.py has the rest: the encode pass, finished /
failed, the progress ticker). Two passes a clip, so a long clip needs no more memory than a short one:

1. depth: the frames are decoded at the size they run at (ffmpeg scales them), a window at a time -- only
   the window running is held, with the frames the next one shares -- and DVD's stitched depth for each
   frame, as it can no longer change, goes into a float16 file beside the output (.<clip>.dvd-raw.npy)
   with its lowest and highest value noted;
2. encode: VDA's -- each frame's range (the whole clip's, or iw3's EMA), 10-bit HEVC as DepthCrafter's
   maps, put in place whole; a map of another kind at that name moves into its kind's folder first. The map
   carries DVD's tag (core.vda.output.DVD_TAG).

The map is written at the size DVD ran at (both sides multiples of 16; Splatting scales it to its own).

Nothing here imports Qt. Torch and the model load only when a batch starts.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List, Optional

from studio.outcome import is_out_of_memory
from studio.stages.vda.runner import VIDEO_TYPES, Hooks, Stopped, VdaRunner, _clock, _reader, _Ticker  # noqa: F401

logger = logging.getLogger("dvd")

OOM_HINT = "It ran out of graphics memory. Pick a lower Input Size or a shorter Window, then run it again."


class _DvdTicker(_Ticker):
    LABELS = {"depth": "DVD depth", "encode": "DVD encode"}


class DvdRunner(VdaRunner):
    TAG_KIND = ("dvd", "DVD_TAG")

    def ticker(self, total: int, phase: str, name: str) -> _Ticker:
        return _DvdTicker(self.hooks, total, phase, name)

    # ---- the batch -------------------------------------------------------------------------

    def run_batch(self, specs: List[Dict[str, str]]) -> bool:
        from core.dvd import model as M

        videos = [s for s in specs if s.get("type") in VIDEO_TYPES]
        for s in specs:
            if s.get("type") not in VIDEO_TYPES:
                logger.warning("DVD takes videos only; skipping %s.", os.path.basename(s["path"]))
                self.outcome.skipped += 1
        total = len(videos)
        if not total:
            self.outcome.note = "No videos to process (image sequences are DepthCrafter's)."
            return True
        self.hooks.status("Loading DVD...")
        try:
            model = M.load()
        except M.ModelsMissing as e:
            self.outcome.error = str(e)
            logger.error("%s", e)
            return False
        except Exception as e:
            logger.exception("DVD could not load")
            self.outcome.error = f"DVD could not load: {e}"
            if is_out_of_memory(e):
                self.outcome.hint = OOM_HINT
            return False
        if self.opts.get("use_tensorrt"):          # the TensorRT box: the VAE on engines (user, 2026-11-07)
            from core.dvd import vae_trt
            why = vae_trt.unavailable_reason()
            if why:
                logger.warning("TensorRT isn't available (%s); DVD runs the normal way.", why)
            else:
                model.trt = vae_trt.VaeTensorRT(model, logger, status=self.hooks.status)
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
            if getattr(model, "trt", None) is not None:
                logger.info("%s", model.trt.summary())
            M.release(model)
            model = None
        return True

    # ---- one clip ----------------------------------------------------------------------------

    def process_clip(self, model, spec: Dict[str, str]) -> Optional[bool]:
        """True: the map is in place; False: the clip failed; None: stopped during it."""
        import numpy as np
        import torch
        from core.depthedit.depth_io import probe
        from core.dvd import model as M

        path, base = spec["path"], spec["basename"]
        o = self.opts
        t0 = time.monotonic()
        raw_path = None
        raw = None
        frames = None
        try:
            info = probe(path)
            n = info.num_frames
            if int(o.get("process_length", -1)) > 0:
                n = min(n, int(o["process_length"]))
            if n <= 0:
                raise ValueError("no frames")
            oh, ow = M.infer_size(info.height, info.width, int(o["input_size"]), int(o["width"]),
                                  bool(o["early_downsample"]))
            window, overlap = M.snap_window(o["window"]), int(o["overlap"])
            model.tile_area = M.choose_tile_area(model, oh, ow, window)        # 0: whole frames
            if model.tile_area:
                r, c, th, tw = M.tile_grid(oh // 8, ow // 8, model.tile_area)
                logger.info("%s: the VAE works in %d tiles of %dx%d (the card is short of room for whole frames).",
                            os.path.basename(path), r * c, tw * 8, th * 8)
            self.hooks.clip(os.path.basename(path), f"{info.width}x{info.height}", str(n))
            logger.info("%s: %d frames, %dx%d, run and mapped at %dx%d, windows of %d overlapping by %d.",
                        os.path.basename(path), n, info.width, info.height, ow, oh, window, overlap)
            out_dir = o["output_dir"]
            os.makedirs(out_dir, exist_ok=True)
            device = model.device

            # -- pass 1: depth -------------------------------------------------------------------
            raw_path = os.path.join(out_dir, f".{base}.dvd-raw.npy")
            raw = np.lib.format.open_memmap(raw_path, mode="w+", dtype=np.float16, shape=(n, oh, ow))
            lows: List[float] = []
            highs: List[float] = []
            tick = self.ticker(n, "depth", os.path.basename(path))

            def keep(d):
                k = len(lows)
                lows.append(float(d.amin()))
                highs.append(float(d.amax()))
                raw[k] = d.to(torch.float16).cpu().numpy()
                tick(k + 1)

            frames = _Frames(path, info, n, ow, oh, device, self.stop_now)
            got = M.run_clip(model, frames.get, n, window, overlap, keep, stopped=self.stop_now.is_set)
            frames.close()
            frames = None
            if self.stop_now.is_set():
                raise Stopped()
            if got == 0:
                raise ValueError("the model gave no frames")
            if got < n:
                logger.warning("%s: %d of %d frames could be read; the map has %d.", os.path.basename(path), got, n, got)

            # -- pass 2: encode (VDA's) ------------------------------------------------------------
            final, moved = self.encode(raw, lows, highs, got, (oh, ow), info, path, base, device)
            self.made.append({"source": path, "map": final, "set_aside": moved or ""})
            took = time.monotonic() - t0
            logger.info("%s: depth map saved to %s in %.1f s (%.2f fps)%s.", os.path.basename(path), final, took,
                        got / took if took > 0 else 0.0,
                        f"; the map it replaced is in {os.path.dirname(moved)}" if moved else "")
            return True
        except Stopped:
            logger.info("Stopped during %s; its map was not written.", os.path.basename(path))
            return None
        except Exception as e:
            logger.exception("DVD failed on %s", os.path.basename(path))
            if is_out_of_memory(e):
                self.outcome.hint = OOM_HINT
            return False
        finally:
            if frames is not None:
                frames.close()
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


class _Frames:
    """The clip's frames for DVD's windows, decoded once, in order, at the size DVD runs at: get(start, end)
    gives frames start..end-1 as (T, 3, H, W) uint8, pinned RAM (run_window takes them to the card). Windows only
    move forward, so frames before a window's start are dropped as it asks -- only one window's worth is ever
    held (on the CPU, as uint8; the window goes to the card when asked for)."""

    def __init__(self, path: str, info, n: int, width: int, height: int, device, stop_now: threading.Event):
        self.reader = _reader(path, info, n, width, height)
        self.n, self.device, self.stop_now = n, device, stop_now
        self.name = os.path.basename(path)
        self.first = 0              # the clip frame self.held[0] is
        self.held: list = []
        self.ended = False

    def _read_to(self, end: int) -> None:
        nxt = self.first + len(self.held)
        while nxt < end and not self.ended:
            if self.stop_now.is_set():
                return
            b = min(end, nxt + 8)
            try:
                arr = self.reader.get_batch(list(range(nxt, b))).asnumpy()
            except EOFError:
                logger.warning("%s ended at frame %d.", self.name, nxt)
                self.ended = True
                return
            if len(arr) == 0:
                self.ended = True
                return
            self.held.extend(arr)
            nxt += len(arr)

    def get(self, start: int, end: int):
        import numpy as np
        import torch

        if start > self.first:
            drop = min(start - self.first, len(self.held))
            del self.held[:drop]
            self.first += drop
        self._read_to(end)
        if self.stop_now.is_set():
            return None
        part = self.held[start - self.first: end - self.first]
        if not part:
            return None
        x = torch.from_numpy(np.stack(part))
        if self.device.type == "cuda":
            x = x.pin_memory()
        return x.permute(0, 3, 1, 2)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None
