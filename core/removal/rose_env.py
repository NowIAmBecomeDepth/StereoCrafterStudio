"""rose_env.py -- where ROSE lives, whether it is ready, and setting it up.

ROSE (Kunbyte-AI/ROSE, Apache-2.0: video object removal with its side
effects -- shadows, reflections, translucency) runs in its OWN environment,
as a separate process (user, 2026-10-05): it pins diffusers 0.31 and an
older PyTorch than Studio's, so it can't share Studio's packages.

    weights/ROSE/                 the ROSE repository at ROSE_COMMIT
      .venv/                      its Python 3.12 (PyTorch cu128)
      models/Wan2.1-Fun-1.3B-InP/ VAE + tokenizer (its base model)
      weights/transformer/        ROSE's fine-tuned transformer

The download is ~3.7 GB, not the base model's ~20: the 11 GB text encoder and
the 4.8 GB CLIP encoder are never loaded (rose_worker.py says why), so they
are never fetched. weights/ is git-ignored, like every model.

No Qt here; the editor's controller drives setup() on a worker thread.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

from core import weights_paths  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
#: ROSE/ in the models folder (Settings > Studio > Models, 2026-10-20), or in <repo>/weights when
#: it was set up there. Its .venv is tied to where it was built: moved by hand, Set Up ROSE again.
ROSE_DIR = weights_paths.find("ROSE", marker=os.path.join(".venv", "pyvenv.cfg"))
GIT_URL = "https://github.com/Kunbyte-AI/ROSE.git"
#: The commit Studio was tested with (2026-10-05).
ROSE_COMMIT = "6be41c5420bf331c6d491277d5a6feaf9b3a779a"
EMBEDS = Path(__file__).resolve().parent / "rose_empty_prompt.npy"
WORKER = Path(__file__).resolve().parent / "rose_worker.py"

BASE_REPO = "alibaba-pai/Wan2.1-Fun-1.3B-InP"
BASE_FILES = ["Wan2.1_VAE.pth", "config.json", "google/umt5-xxl/*"]
ROSE_REPO = "Kunbyte/ROSE"
ROSE_FILES = ["config.json", "diffusion_pytorch_model.safetensors"]

#: ROSE's own pins, plus a PyTorch that knows Blackwell cards (sm_120) and
#: the transformers 4.x its diffusers needs.
TORCH = ["torch==2.8.0", "torchvision==0.23.0"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
EXTRA = ["transformers>=4.46.2,<5", "huggingface_hub"]

DOWNLOAD_GB = 3.7


def python() -> Path:
    return ROSE_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _needed() -> List[Path]:
    base = ROSE_DIR / "models" / "Wan2.1-Fun-1.3B-InP"
    return [python(), ROSE_DIR / "rose" / "pipeline" / "__init__.py",
            ROSE_DIR / "configs" / "wan2.1" / "wan_civitai.yaml",
            base / "Wan2.1_VAE.pth", base / "google" / "umt5-xxl" / "tokenizer.json",
            ROSE_DIR / "weights" / "transformer" / "diffusion_pytorch_model.safetensors",
            ROSE_DIR / "weights" / "transformer" / "config.json", EMBEDS, WORKER]


def missing() -> List[str]:
    """What is not there yet, as paths relative to the repo; [] = ready."""
    out = []
    for p in _needed():
        if not p.exists():
            try:
                out.append(str(p.relative_to(REPO)))
            except ValueError:
                out.append(str(p))
    return out


def ready() -> bool:
    return not missing()


def _uv() -> Optional[str]:
    return shutil.which("uv")


def _run(cmd: List[str], log: Callable[[str], None], stop=None, cwd=None) -> None:
    """Run a setup step, its output to `log`; raises on failure or stop."""
    log("> " + " ".join(str(c) for c in cmd))
    proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
        if stop is not None and stop.is_set():
            proc.kill()
            raise RuntimeError("stopped")
    if proc.wait() != 0:
        raise RuntimeError(f"{os.path.basename(str(cmd[0]))} failed (exit {proc.returncode})")


#: Written once the clone is checked out at ROSE_COMMIT (bug sweep 2026-10-09
#: #12: the folder appears when a clone starts, so a stopped one counted as done).
CLONE_MARKER = ROSE_DIR / ".studio_clone_ok"


@weights_paths.on_change
def _models_moved() -> None:
    global ROSE_DIR, CLONE_MARKER
    ROSE_DIR = weights_paths.find("ROSE", marker=os.path.join(".venv", "pyvenv.cfg"))
    CLONE_MARKER = ROSE_DIR / ".studio_clone_ok"


def _head(git: str) -> Optional[str]:
    """The ROSE checkout's commit, or None (no repository, or a broken one)."""
    try:
        out = subprocess.run([git, "-C", str(ROSE_DIR), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _clone(git: str, log: Callable[[str], None], stop=None) -> None:
    """ROSE's repository at ROSE_COMMIT in ROSE_DIR. A clone that was cut
    short is redone beside it and merged in: the repository has models/ and
    weights/ folders of its own, where the downloads go, so nothing there is
    removed."""
    if CLONE_MARKER.is_file() and CLONE_MARKER.read_text(encoding="utf-8").strip() == ROSE_COMMIT:
        return
    if (ROSE_DIR / ".git").is_dir() and _head(git) == ROSE_COMMIT:     # set up before the marker
        CLONE_MARKER.write_text(ROSE_COMMIT, encoding="utf-8")
        return
    side = ROSE_DIR.with_name(ROSE_DIR.name + ".clone")
    shutil.rmtree(side, ignore_errors=True)
    ROSE_DIR.parent.mkdir(parents=True, exist_ok=True)
    _run([git, "clone", GIT_URL, str(side)], log, stop)
    _run([git, "-C", str(side), "checkout", "-q", ROSE_COMMIT], log, stop)
    ROSE_DIR.mkdir(parents=True, exist_ok=True)
    for entry in side.iterdir():
        dest = ROSE_DIR / entry.name
        if entry.is_dir():
            if entry.name == ".git":
                shutil.rmtree(dest, ignore_errors=True)    # a cut-short clone's history goes
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)
    shutil.rmtree(side, ignore_errors=True)
    CLONE_MARKER.write_text(ROSE_COMMIT, encoding="utf-8")


def setup(log: Callable[[str], None], stop=None) -> None:
    """Clone ROSE, build its environment and fetch its weights (~3.7 GB),
    skipping whatever is there already. Needs git and uv on PATH. Safe to run
    again after a Stop or a failure: each step carries on where it left off."""
    git, uv = shutil.which("git"), _uv()
    if git is None or uv is None:
        raise RuntimeError("Setting up ROSE needs " + " and ".join(n for n, p in (("git", git), ("uv", uv)) if p is None)
                           + " on PATH.")
    _clone(git, log, stop)
    if not python().exists():
        _run([uv, "venv", "--python", "3.12", str(ROSE_DIR / ".venv")], log, stop)
    # always: python.exe appears before its packages, so a stopped install
    # used to be skipped for good (bug sweep #12); uv returns at once when
    # everything is there already
    _run([uv, "pip", "install", "--python", str(python()), *TORCH, "--index-url", TORCH_INDEX], log, stop)
    _run([uv, "pip", "install", "--python", str(python()), "-r", str(ROSE_DIR / "requirements.txt"), *EXTRA],
         log, stop)
    fetch = (
        "from huggingface_hub import snapshot_download as d\n"
        f"d({ROSE_REPO!r}, local_dir='weights/transformer', allow_patterns={ROSE_FILES!r})\n"
        f"d({BASE_REPO!r}, local_dir='models/Wan2.1-Fun-1.3B-InP', allow_patterns={BASE_FILES!r})\n")
    _run([str(python()), "-c", fetch], log, stop, cwd=str(ROSE_DIR))
    still = missing()
    if still:
        raise RuntimeError("Still missing after setup: " + ", ".join(still))
    log("ROSE is ready.")


if __name__ == "__main__":     # python -m core.removal.rose_env [setup]
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["setup"]:
        setup(print)
    print("ready" if ready() else "missing: " + ", ".join(missing()))
