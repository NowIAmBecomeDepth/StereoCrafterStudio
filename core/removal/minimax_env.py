"""minimax_env.py -- MiniMax-Remover, Edge Fill's second inpainter (user, 2026-11-03: "Yes, add it").

MiniMax-Remover (Zi et al., NeurIPS 2025, arXiv 2505.24873) removes masked parts of a video with a
1.1B Wan-based transformer in 6 steps. Benched against ROSE on 8 shots' hidden left edges
(prototypes/minimax_remover/bench_shots.py, 2026-11-03): its seam closer to the real picture on 8 of 8,
less flicker on 7, about 2.6x quicker (38 s against ROSE's 99 s at 50 steps for 49 frames).

It runs in Studio's own environment (diffusers' AutoencoderKLWan), as a child process
(minimax_worker.py) so its ~6 GB of the card goes when it ends. Everything it needs comes from
Settings > Studio > Models (studio/model_catalog.py, "minimax_remover") into one folder:

    MiniMax-Remover/
      vae/, transformer/, scheduler/        the weights (Hugging Face zibojia/minimax-remover; CC-BY-NC-4.0)
      pipeline_minimax_remover.py           the authors' two code files (github zibojia/MiniMax-Remover,
      transformer_minimax_remover.py        pinned) -- downloaded, not shipped: the repository states no licence
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core import weights_paths

FOLDER = "MiniMax-Remover"
#: what it can't run without
NEEDED = ("transformer/config.json", "transformer/diffusion_pytorch_model.safetensors",
          "vae/config.json", "vae/diffusion_pytorch_model.safetensors", "scheduler/scheduler_config.json",
          "pipeline_minimax_remover.py", "transformer_minimax_remover.py")
#: its denoising steps: 6 scored better than 12 on the bench (and the paper's own); 12 is offered too
#: (user, 2026-11-03), the authors' own example
STEPS = 6
STEP_CHOICES = (6, 12)
#: px its pipeline grows the mask by at its working size (the authors' example; the bench's)
DILATE = 6
WORKER = Path(__file__).resolve().parent / "minimax_worker.py"
LABEL = "MiniMax-Remover"


def model_dir() -> Path:
    return weights_paths.find(FOLDER, marker=NEEDED[1])


def available() -> bool:
    """Its weights and code are in the models folder."""
    try:
        d = model_dir()
        return all((d / f).is_file() for f in NEEDED)
    except OSError:
        return False


def python() -> str:
    """Studio's own Python: MiniMax-Remover needs nothing it doesn't have."""
    return sys.executable


def command(job_path: str):
    return [python(), "-u", str(WORKER), str(job_path)]


def missing_message() -> str:
    return ("MiniMax-Remover isn't downloaded: Settings > Studio > Models > MiniMax-Remover "
            f"({os.path.join(str(weights_paths.root()), FOLDER)}).")
