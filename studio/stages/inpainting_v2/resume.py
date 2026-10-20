"""resume.py -- Inpainting V2's per-chunk checkpoints (user, 2026-10-18).

A V2 clip takes minutes per 81-frame chunk; a Stop or a crash used to throw the
finished chunks away with the clip's partial output. Now each chunk's denoised
latents are kept as it finishes (``<output>/_inpaint_resume``, beside V1's own
checkpoints), with the random state right after its denoise. The next run of the
clip goes through the same loop: a chunk with a checkpoint skips its denoise --
the latents are decoded as they were the first time, so the overlap frames the
next chunk is conditioned on and the frames written are the same -- and the
random state is put back, so the chunks still to run draw the noise an unbroken
run would have. Only the decode is repeated: seconds a chunk, not minutes.

A checkpoint is used only by a run with the same signature (the clip itself by
size and time, every setting that changes the latents, the model); a stale one
is deleted when the clip runs again. A finished clip deletes its own.

No Qt here: the engine's run_inpaint calls it on the runner's worker thread.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Optional

import torch

logger = logging.getLogger(__name__)

FORMAT = 1
RESUME_DIR = "_inpaint_resume"          # V1 keeps its own checkpoints there too (".chunk_", ".prefinalize")
_TAG = ".v2chunk_"
_NAME_RE = re.compile(re.escape(_TAG) + r"(\d{6})_(\d{4})\.pt$")


def _identity(path: str) -> dict:
    """The clip as a checkpoint knows it: where it is, its size and modified time."""
    path = os.path.abspath(path)
    try:
        st = os.stat(path)
        return {"path": os.path.normcase(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {"path": os.path.normcase(path), "size": None, "mtime_ns": None}


def signature(input_path: str, *, frames_chunk: int, frames_overlap: int, tile_num: int, tile_overlap: int,
              inference_steps: int, seed, prompt: str, is_dual_input: bool, total_frames: int, model: dict) -> dict:
    """Everything that changes a chunk's latents: a checkpoint made under anything else is stale."""
    return {
        "format": FORMAT,
        "input": _identity(input_path),
        "frames_chunk": int(frames_chunk),
        "frames_overlap": int(frames_overlap),
        "tile_num": int(tile_num),
        "tile_overlap": int(tile_overlap),
        "inference_steps": int(inference_steps),
        "seed": None if seed is None else int(seed),
        "prompt": str(prompt or ""),
        "is_dual_input": bool(is_dual_input),
        "total_frames": int(total_frames),
        "model": {k: str(v) for k, v in sorted((model or {}).items())},
    }


def _rng_state() -> dict:
    state = {"cpu": torch.get_rng_state()}
    # only once CUDA is in use: asking first would start a context (and take VRAM) for nothing --
    # with none, nothing was drawn on it to keep
    if torch.cuda.is_available() and torch.cuda.is_initialized():
        try:
            state["cuda"] = torch.cuda.get_rng_state()
        except Exception:
            pass
    return state


def _set_rng_state(state: dict) -> None:
    if state.get("cpu") is not None:
        torch.set_rng_state(state["cpu"])
    if state.get("cuda") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state(state["cuda"])


class ChunkCheckpoints:
    """One clip's chunk checkpoints, keyed by each chunk's (first frame, length)."""

    def __init__(self, save_dir: str, video_name: str, sig: dict):
        self.dir = os.path.join(save_dir, RESUME_DIR)
        self.stem = video_name
        self.sig = sig
        self.restored = 0                    # chunks this run took from a checkpoint
        self.saved = 0
        self._drop_stale()

    def path(self, start: int, length: int) -> str:
        return os.path.join(self.dir, f"{self.stem}{_TAG}{int(start):06d}_{int(length):04d}.pt")

    def _mine(self):
        # "<stem>.v2chunk_*": another clip whose name starts with this one's has another letter there
        return [p for p in glob.glob(os.path.join(glob.escape(self.dir), glob.escape(self.stem) + _TAG + "*.pt"))
                if _NAME_RE.search(os.path.basename(p))]

    def _read(self, path: str) -> Optional[dict]:
        try:
            got = torch.load(path, map_location="cpu", weights_only=False)
        except Exception as e:
            logger.warning("Chunk checkpoint unreadable, ignored: %s (%s)", path, e)
            return None
        return got if isinstance(got, dict) else None

    def _drop_stale(self) -> None:
        """Checkpoints of this clip made under other settings (or of another file by the name): gone."""
        stale = []
        for p in self._mine():
            got = self._read(p)
            if got is None or got.get("signature") != self.sig:
                stale.append(p)
        for p in stale:
            try:
                os.remove(p)
            except OSError:
                pass
        if stale:
            logger.info("Deleted %d chunk checkpoint(s) of %s made under other settings (or of another clip).",
                        len(stale), self.stem)

    def load(self, start: int, length: int, device) -> Optional[torch.Tensor]:
        """This chunk's latents on `device` with the random state put back as it was after its denoise,
        or None (no checkpoint, or not this run's)."""
        path = self.path(start, length)
        if not os.path.exists(path):
            return None
        got = self._read(path)
        if (got is None or got.get("signature") != self.sig or got.get("start") != int(start)
                or got.get("length") != int(length) or not isinstance(got.get("latents"), torch.Tensor)):
            return None
        _set_rng_state(got.get("rng") or {})
        self.restored += 1
        logger.info("Chunk [%d:%d] from its checkpoint: no denoise, only its decode.", start, start + length)
        return got["latents"].to(device)

    def save(self, start: int, length: int, latents: torch.Tensor) -> bool:
        """Keep this chunk's latents and the random state after its denoise. Never fatal."""
        path = self.path(start, length)
        tmp = path + ".tmp"
        try:
            os.makedirs(self.dir, exist_ok=True)
            torch.save({"signature": self.sig, "start": int(start), "length": int(length),
                        "latents": latents.detach().to("cpu"), "rng": _rng_state()}, tmp)
            os.replace(tmp, path)
            self.saved += 1
            return True
        except Exception as e:               # a full disk loses the resume, never the clip
            logger.warning("Could not keep chunk checkpoint %s: %s", path, e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def clear(self) -> None:
        """The clip finished: its checkpoints (and the folder, once empty) go."""
        for p in self._mine():
            try:
                os.remove(p)
            except OSError:
                logger.debug("Could not delete chunk checkpoint %s", p, exc_info=True)
        try:
            os.rmdir(self.dir)               # only when empty: V1's checkpoints may share it
        except OSError:
            pass
        if self.restored:
            logger.info("Resumed %d chunk(s) of %s from their checkpoints.", self.restored, self.stem)
