"""weights.py -- Inpainting V2's weights: which files it needs, whether they're here, and
Download V2 Weights (user, 2026-10-18: "an easy download button like SAM 3 and ROSE have").

V2 reads only part of the Wan 2.1 VACE repo -- its text encoder, VAE, tokenizer and the two small
configs (engine.load_models: subfolder tokenizer / text_encoder / vae); the transformer comes from
StereoCrafter2. So the download is those files, not the repos: 46.6 GB, where the whole VACE repo
alone is 76 GB.

Since 2026-10-20 the parts and the downloader are Studio-wide (studio.model_catalog, behind
Settings > Studio > Models, and the models folder setting); this names V2's parts for its page.

No Qt and no torch here: the page asks before the engine is imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ... import model_catalog as C
from ...model_catalog import Part, Stopped, free_space, gb  # noqa: F401  (V2's page and tests use them)

#: Wan 2.1 VACE: only what V2 loads from it (its 59 GB transformer is StereoCrafter2's job)
BASE = C.WAN_VACE
#: StereoCrafter2: the fine-tuned VACE transformer V2 runs (its licence and notice come along)
MODEL = C.STEREOCRAFTER2
#: StereoCrafter2 pre-quantized to FP8, for the FP8 resident offload mode only (~20 GB VRAM)
FP8 = C.STEREOCRAFTER2_FP8

REQUIRED = (BASE, MODEL)


def missing(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> List[Tuple[Part, str, int]]:
    """The files not on disk yet: (part, path, bytes)."""
    return C.missing(parts, root)


def installed(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> bool:
    return C.installed(parts, root)


def to_download(parts: Sequence[Part] = REQUIRED, root: Optional[Path] = None) -> int:
    """Bytes still to come for these parts."""
    return C.to_download(parts, root)


def download(parts: Sequence[Part], progress: Callable[[float, str], None], stop, **kw) -> None:
    """Fetch every missing file of `parts` into the models folder (model_catalog.download)."""
    C.download(parts, progress, stop, **kw)
