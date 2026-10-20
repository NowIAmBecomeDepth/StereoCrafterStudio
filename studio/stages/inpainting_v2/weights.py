"""weights.py -- Inpainting V2's weights: which files it needs, whether they're here, and
Download V2 Weights (user, 2026-10-18: "an easy download button like SAM 3 and ROSE have").

V2 reads only part of the Wan 2.1 VACE repo -- its text encoder, VAE, tokenizer and the two small
configs (engine.load_models: subfolder tokenizer / text_encoder / vae); the transformer comes from
StereoCrafter2. So the download is the files below, not the repos: 46.6 GB, where the whole
VACE repo alone is 76 GB. Each file is listed with its size, from the repo at the pinned
revision -- the page's numbers are these, and a download fetches exactly that revision.

The download runs in a child process (huggingface_hub's own hf_hub_download, one file at a
time) so a Stop can end it at once; the next Download picks up the part-files it left. Progress
is the bytes on disk -- finished files plus the part-files -- read every couple of seconds.

No Qt and no torch here: the page asks before the engine is imported.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ...legacy import REPO

WEIGHTS = REPO / "weights"


@dataclass(frozen=True)
class Part:
    name: str                               # as the page says it
    repo: str
    revision: str                           # pinned: the sizes below are this revision's
    folder: str                             # under weights/ (engine.PRE_TRAINED_PATH, TRANSFORMER_PATH, + "-FP8")
    files: Tuple[Tuple[str, int], ...]      # (path in the repo, bytes)

    @property
    def size(self) -> int:
        return sum(n for _p, n in self.files)


#: Wan 2.1 VACE: only what V2 loads from it (its 59 GB transformer is StereoCrafter2's job)
BASE = Part("Wan 2.1 VACE (text encoder, VAE, tokenizer)", "Wan-AI/Wan2.1-VACE-14B-diffusers",
            "db79b90c60bbb45ceec9e41b9d5a4df934538ac4", "Wan2.1-VACE-14B-diffusers", (
                ("model_index.json", 408),
                ("scheduler/scheduler_config.json", 751),
                ("text_encoder/config.json", 850),
                ("text_encoder/model.safetensors.index.json", 22476),
                ("text_encoder/model-00001-of-00003.safetensors", 4935812536),
                ("text_encoder/model-00002-of-00003.safetensors", 4983103192),
                ("text_encoder/model-00003-of-00003.safetensors", 1442935480),
                ("tokenizer/special_tokens_map.json", 7079),
                ("tokenizer/spiece.model", 4548313),
                ("tokenizer/tokenizer.json", 16837459),
                ("tokenizer/tokenizer_config.json", 61786),
                ("vae/config.json", 724),
                ("vae/diffusion_pytorch_model.safetensors", 507591892),
            ))

#: StereoCrafter2: the fine-tuned VACE transformer V2 runs (its licence and notice come along)
MODEL = Part("StereoCrafter2", "TencentARC/StereoCrafter2", "7cc4b0f6d95b841cdee6da8a635a739cd23fa165",
             "StereoCrafter2", (
                 ("config.json", 685),
                 ("diffusion_pytorch_model.safetensors", 34675325472),
                 ("LICENSE", 1845),
                 ("NOTICE", 13211),
                 ("README.md", 827),
             ))

#: StereoCrafter2 pre-quantized to FP8, for the FP8 resident offload mode only (~20 GB VRAM)
FP8 = Part("StereoCrafter2 FP8 (for FP8 resident)", "enoky/StereoCrafter2-FP8",
           "436d5e3e399db51ce4083aea5009deba978c3d9e", "StereoCrafter2-FP8", (
               ("config.json", 688),
               ("diffusion_pytorch_model_fp8.pt", 17362409053),
               ("LICENSE", 1845),
               ("README.md", 3153),
           ))

REQUIRED = (BASE, MODEL)


def gb(n: int) -> str:
    """Bytes as the page says them: decimal GB, as Hugging Face lists them (Windows' "GB" is GiB)."""
    return f"{n / 1e9:.1f} GB"


def missing(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> List[Tuple[Part, str, int]]:
    """The files not on disk yet: (part, path, bytes). A file is there when it exists and isn't
    empty -- not by its size, so a copy from another revision (an older clone) still counts."""
    base = Path(root) if root is not None else WEIGHTS
    out = []
    for part in parts:
        for path, size in part.files:
            f = base / part.folder / path
            try:
                if f.stat().st_size > 0:
                    continue
            except OSError:
                pass
            out.append((part, path, size))
    return out


def installed(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> bool:
    return not missing(parts, root)


def to_download(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> int:
    """Bytes still to come for these parts."""
    return sum(n for _p, _f, n in missing(parts, root))


def free_space(root: Optional[Path] = None) -> int:
    base = Path(root) if root is not None else WEIGHTS
    probe = base
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(str(probe)).free


def _on_disk(base: Path, todo: Sequence[Tuple[Part, str, int]]) -> int:
    """Bytes of these files on disk: the finished ones, plus every part-file in their folders."""
    done = 0
    for part, path, _size in todo:
        try:
            done += (base / part.folder / path).stat().st_size
        except OSError:
            pass
    for folder in {part.folder for part, _p, _s in todo}:
        for f in glob.glob(os.path.join(glob.escape(str(base / folder)), ".cache", "huggingface", "download",
                                        "**", "*.incomplete"), recursive=True):
            try:
                done += os.path.getsize(f)
            except OSError:
                pass
    return done


_FETCH = (
    "import sys\n"
    "from huggingface_hub import hf_hub_download\n"
    "repo, rev, folder, *files = sys.argv[1:]\n"
    "for f in files:\n"
    "    print('Downloading ' + f, flush=True)\n"
    "    hf_hub_download(repo, f, revision=rev, local_dir=folder)\n"
)


class Stopped(Exception):
    pass


def download(parts: Sequence[Part], progress: Callable[[float, str], None], stop: threading.Event,
             root: Optional[Path] = None, python: Optional[str] = None, poll_s: float = 2.0,
             procs: Optional[list] = None) -> None:
    """Fetch every missing file of `parts` into weights/. progress(0..1, text) every `poll_s`.
    Raises Stopped on a Stop (the part-files stay for the next run), RuntimeError on a failure."""
    base = Path(root) if root is not None else WEIGHTS
    todo = missing(parts, base)
    total = sum(n for _p, _f, n in todo) or 1
    start_bytes = _on_disk(base, todo)
    began = time.monotonic()
    for part in parts:
        files = [path for p, path, _n in todo if p is part]
        if not files:
            continue
        folder = base / part.folder
        folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, HF_HUB_DISABLE_PROGRESS_BARS="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([python or sys.executable, "-c", _FETCH, part.repo, part.revision, str(folder), *files],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if procs is not None:
            procs.append(proc)
        lines: List[str] = []
        reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
        reader.start()
        try:
            while proc.poll() is None:
                if stop.wait(poll_s):
                    proc.kill()
                    proc.wait()
                    raise Stopped()
                done = _on_disk(base, todo)
                rate = max(0, done - start_bytes) / max(1e-6, time.monotonic() - began)
                left = f", about {_eta((total - done) / rate)} left" if rate > 1e6 else ""
                progress(min(1.0, done / total), f"{part.name}: {gb(done)} of {gb(total)}{left}")
        finally:
            if proc.poll() is None:
                proc.kill()
            reader.join(timeout=5)
            if procs is not None and proc in procs:
                procs.remove(proc)
        if proc.returncode != 0:
            tail = "".join(lines[-6:]).strip()
            raise RuntimeError(f"Downloading {part.name} failed (exit {proc.returncode}).\n\n{tail}")
    still = missing(parts, base)
    if still:
        raise RuntimeError("Still missing after the download: " + ", ".join(f"{p.folder}/{f}" for p, f, _n in still))
    progress(1.0, "Downloaded.")


def _eta(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else (f"{m} min" if m else "a minute")
