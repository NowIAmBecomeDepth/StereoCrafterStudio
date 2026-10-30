"""model_catalog.py -- every model Studio can download, whether it's here, and the downloader
(user, 2026-10-20: "an in-app way to get each missing weight", in Settings > Studio > Models).

Each model is a list of Parts: a repository at a pinned revision, the folder it goes in under the
models folder (core.weights_paths), and the files Studio loads from it with their sizes -- not the
whole repository. Stable Video Diffusion is the big case: DepthCrafter and Inpainting read only
its fp16 VAE and image encoder and a few configs (depthcrafter_logic, pipelines/
stereo_video_inpainting: variant="fp16", their own UNet passed in), 1.5 GB of its 18. A Part's
`extras` (licence, notice) come with it but aren't needed to run.

A part is installed in a folder when every file is there and isn't empty -- not by size, so a
copy from another revision still counts. It is looked for in the models folder, then in
<repo>/weights (weights_paths.roots()); a download goes to the models folder.

The download runs in a child process (huggingface_hub's own hf_hub_download, one file at a time;
urllib for the one file not on the Hub) so a Stop ends it at once; the next Download picks up the
part-files it left. Progress is the bytes on disk -- finished files plus the part-files -- read
every couple of seconds. Moved here from Inpainting V2's weights.py, which still names its parts.

A "package" row (TensorRT, user 2026-10-29: "download and install tensorrt from studio like we do
the weights") is Python packages, not weights: Install runs `uv pip install` into Studio's own
environment at the versions uv.lock pins for the extra (what `uv sync --extra tensorrt` would put
there; the updater keeps them once they're here). Nothing it installs is imported before it's used.

No Qt and no torch here.
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from core import weights_paths

on_change = weights_paths.on_change


@dataclass(frozen=True)
class Part:
    name: str                               # as the page says it
    repo: str
    revision: str                           # pinned: the sizes below are this revision's
    folder: str                             # under the models folder
    files: Tuple[Tuple[str, int], ...]      # (path in the repo, bytes): what Studio loads
    key: str = ""                           # model_dir(key)
    extras: Tuple[Tuple[str, int], ...] = ()    # downloaded with it (licence, notice), not needed to run
    alt_folders: Tuple[str, ...] = ()       # other names an older install used
    url: str = ""                           # a plain HTTPS file instead of the Hub (one file)
    sha256: str = ""                        # ...checked after the download

    @property
    def size(self) -> int:
        return sum(n for _p, n in self.files)


@dataclass(frozen=True)
class Model:
    """A row of the Models window: one or more parts, or a model got some other way (kind)."""
    key: str
    name: str
    used_by: str
    parts: Tuple[Part, ...] = ()
    gated: bool = False                     # the account must be granted access on the model page
    page: str = ""
    kind: str = "download"                  # "download" | "rose" (Set Up ROSE) | "bundled" | "manual" | "package"
    note: str = ""
    tip: str = ""                           # on hovering its name
    manual_file: str = ""                   # kind "manual": the file to put in the folder (under SceneCut/)
    extra: str = ""                         # kind "package": the pyproject extra it installs
    modules: Tuple[str, ...] = ()           # ...and the modules that must import for it to count as here
    installed_size: str = ""                # ...as the page says it


# ---- the parts ------------------------------------------------------------------------------

#: Stable Video Diffusion 1.1: only what DepthCrafter and Inpainting load (their own UNets replace its)
SVD = Part("Stable Video Diffusion 1.1 (VAE, image encoder)", "stabilityai/stable-video-diffusion-img2vid-xt-1-1",
           "043843887ccd51926e3efed36270444a838e7861", "stable-video-diffusion-img2vid-xt-1-1", (
               ("model_index.json", 496),
               ("feature_extractor/preprocessor_config.json", 518),
               ("scheduler/scheduler_config.json", 533),
               ("image_encoder/config.json", 685),
               ("image_encoder/model.fp16.safetensors", 1264217240),
               ("vae/config.json", 607),
               ("vae/diffusion_pytorch_model.fp16.safetensors", 195531910),
           ), key="svd", extras=(("LICENSE.md", 11852), ("README.md", 16069)),
           # the -xt base's VAE and image encoder are byte-identical (depthcrafter runner)
           alt_folders=("stable-video-diffusion-img2vid-xt",))

DEPTHCRAFTER = Part("DepthCrafter", "tencent/DepthCrafter", "f2207fa67e799d3cac41b0a051bb051401137a5b",
                    "DepthCrafter", (("config.json", 891), ("diffusion_pytorch_model.safetensors", 3049435868)),
                    key="depthcrafter", extras=(("LICENSE", 2115), ("NOTICE", 8167)))

STEREOCRAFTER = Part("StereoCrafter", "TencentARC/StereoCrafter", "512bbe01ceda2c76facc7d598f5d7be69ccad6bc",
                     "StereoCrafter", (("config.json", 984), ("diffusion_pytorch_model.safetensors", 3049441596)),
                     key="stereocrafter", extras=(("LICENSE", 1883), ("NOTICE", 7940)))

#: Wan 2.1 VACE: only what Inpainting V2 loads from it (its 59 GB transformer is StereoCrafter2's job)
WAN_VACE = Part("Wan 2.1 VACE (text encoder, VAE, tokenizer)", "Wan-AI/Wan2.1-VACE-14B-diffusers",
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
                ), key="wan_vace")

#: StereoCrafter2: the fine-tuned VACE transformer Inpainting V2 runs (its licence and notice come along)
STEREOCRAFTER2 = Part("StereoCrafter2", "TencentARC/StereoCrafter2", "7cc4b0f6d95b841cdee6da8a635a739cd23fa165",
                      "StereoCrafter2", (
                          ("config.json", 685),
                          ("diffusion_pytorch_model.safetensors", 34675325472),
                          ("LICENSE", 1845),
                          ("NOTICE", 13211),
                          ("README.md", 827),
                      ), key="stereocrafter2")

#: StereoCrafter2 pre-quantized to FP8, for Inpainting V2's FP8 resident mode only (~20 GB VRAM)
STEREOCRAFTER2_FP8 = Part("StereoCrafter2 FP8 (for FP8 resident)", "enoky/StereoCrafter2-FP8",
                          "436d5e3e399db51ce4083aea5009deba978c3d9e", "StereoCrafter2-FP8", (
                              ("config.json", 688),
                              ("diffusion_pytorch_model_fp8.pt", 17362409053),
                              ("LICENSE", 1845),
                              ("README.md", 3153),
                          ), key="stereocrafter2_fp8")

SAM3 = Part("SAM 3", "facebook/sam3", "3c879f39826c281e95690f02c7821c4de09afae7", "SAM3",
            (("sam3.pt", 3450062241),), key="sam3")

OMNISHOTCUT = Part("OmniShotCut", "uva-cv-lab/OmniShotCut", "7b59cc525168ea13d36dcf08db6885fd7edc2215", "SceneCut",
                   (("OmniShotCut_ckpt.pth", 164149963),), key="omnishotcut")

DINOV3 = Part("DINOv3 ViT-L/16", "facebook/dinov3-vitl16-pretrain-lvd1689m", "ea8dc2863c51be0a264bab82070e3e8836b02d51",
              "SceneCut/DINOv3", (("config.json", 745), ("model.safetensors", 1212559808),
                                  ("preprocessor_config.json", 585)),
              key="dinov3", extras=(("LICENSE.md", 7503),))

#: Meta's release of SSCD (facebookresearch/sscd-copy-detection) -- not on the Hub
SSCD = Part("SSCD", "", "", "SceneCut", (("sscd_disc_large.torchscript.pt", 178141104),), key="sscd",
            url="https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_large.torchscript.pt",
            sha256="16a196359f91e5fc4e774d5679244c8a661c182309ad4ede0c7e83f6a9861fa0")

#: Video Depth Anything (DepthCrafter's VDA tab; user, 2026-10-25): one checkpoint per size, all in one folder
VDA_S = Part("Video Depth Anything Small", "depth-anything/Video-Depth-Anything-Small",
             "256875362cff76724b920335dfb4b29dd611f66e", "VideoDepthAnything",
             (("video_depth_anything_vits.pth", 116440756),), key="vda_s")
VDA_B = Part("Video Depth Anything Base", "depth-anything/Video-Depth-Anything-Base",
             "7231d0c6e260f54f103eba5005d01f6efa4f43db", "VideoDepthAnything",
             (("video_depth_anything_vitb.pth", 458247082),), key="vda_b")
VDA_L = Part("Video Depth Anything Large", "depth-anything/Video-Depth-Anything-Large",
             "7aafbcb5c6af0bac741aad2b6471894fb4761afa", "VideoDepthAnything",
             (("video_depth_anything_vitl.pth", 1538392012),), key="vda_l")

PARTS: Dict[str, Part] = {p.key: p for p in (SVD, DEPTHCRAFTER, STEREOCRAFTER, WAN_VACE, STEREOCRAFTER2,
                                             STEREOCRAFTER2_FP8, SAM3, OMNISHOTCUT, DINOV3, SSCD,
                                             VDA_S, VDA_B, VDA_L)}

#: The rows of the Models window, in the stages' order.
MODELS: Tuple[Model, ...] = (
    Model("depthcrafter", "DepthCrafter", "DepthCrafter", (DEPTHCRAFTER,),
          page="https://huggingface.co/tencent/DepthCrafter"),
    Model("vda_s", "Video Depth Anything Small", "DepthCrafter (VDA)", (VDA_S,),
          page="https://huggingface.co/depth-anything/Video-Depth-Anything-Small", note="Apache-2.0."),
    Model("vda_b", "Video Depth Anything Base", "DepthCrafter (VDA)", (VDA_B,),
          page="https://huggingface.co/depth-anything/Video-Depth-Anything-Base",
          note="CC-BY-NC-4.0: non-commercial use only."),
    Model("vda_l", "Video Depth Anything Large", "DepthCrafter (VDA)", (VDA_L,),
          page="https://huggingface.co/depth-anything/Video-Depth-Anything-Large",
          note="CC-BY-NC-4.0: non-commercial use only."),
    Model("svd", "Stable Video Diffusion 1.1", "DepthCrafter, Inpainting", (SVD,), gated=True,
          page="https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt-1-1",
          tip="Studio uses only its VAE and image encoder: 1.5 GB of the repository's 18 GB. DepthCrafter and "
              "Inpainting bring their own UNet, so the rest is never loaded."),
    Model("stereocrafter", "StereoCrafter", "Inpainting", (STEREOCRAFTER,),
          page="https://huggingface.co/TencentARC/StereoCrafter"),
    Model("inpainting_v2", "Wan 2.1 VACE + StereoCrafter2", "Inpainting V2", (WAN_VACE, STEREOCRAFTER2),
          page="https://huggingface.co/TencentARC/StereoCrafter2",
          note="Only the files V2 loads -- not VACE's own 59 GB transformer."),
    Model("stereocrafter2_fp8", "StereoCrafter2 FP8", "Inpainting V2 (FP8 resident)", (STEREOCRAFTER2_FP8,),
          page="https://huggingface.co/enoky/StereoCrafter2-FP8"),
    Model("sam3", "SAM 3", "Depth Map Editor", (SAM3,), gated=True, page="https://huggingface.co/facebook/sam3"),
    Model("rose", "ROSE", "Erase from Clip, Edge Fill", kind="rose", page="https://github.com/Kunbyte-AI/ROSE",
          note="Builds its own environment: Set Up ROSE in the Depth Map Editor's Erase panel."),
    Model("omnishotcut", "OmniShotCut", "Scene Cut", (OMNISHOTCUT,), page="https://huggingface.co/uva-cv-lab/OmniShotCut"),
    Model("transnetv2", "TransNetV2", "Scene Cut", kind="bundled", page="https://github.com/soCzech/TransNetV2",
          note="Comes with Studio's packages (transnetv2-pytorch)."),
    Model("autoshot", "AutoShot", "Scene Cut", kind="manual", manual_file="ckpt_0_200_0.pth",
          page="https://drive.google.com/file/d/1aJ96eJE4DstJ_HBzAusuYLvfU01Jnc92/view?usp=sharing",
          note="Only on Google Drive: get ckpt_0_200_0.pth from the SceneCutGUI bundle and put it in SceneCut/."),
    Model("dinov3", "DINOv3", "Scene Cut (cut validation)", (DINOV3,), gated=True,
          page="https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m"),
    Model("sscd", "SSCD", "Scene Cut (cut validation)", (SSCD,),
          page="https://github.com/facebookresearch/sscd-copy-detection"),
    Model("tensorrt", "NVIDIA TensorRT", "DepthCrafter, Inpainting (their TensorRT boxes)", kind="package",
          extra="tensorrt", modules=("tensorrt", "onnx", "onnxscript"), installed_size="2.3 GB",
          page="https://developer.nvidia.com/tensorrt",
          note="Python packages, installed into Studio's own environment.",
          tip="Makes DepthCrafter and Inpainting about twice as fast when their TensorRT box is on. Each "
              "builds its engines the first time it meets a new size (kept in trt_engines)."),
)
MODELS_BY_KEY: Dict[str, Model] = {m.key: m for m in MODELS}


def gb(n: int) -> str:
    """Bytes as the page says them: decimal GB, as Hugging Face lists them (Windows' "GB" is GiB)."""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 or n == 0 else f"{n / 1e6:.0f} MB"


# ---- what's here ----------------------------------------------------------------------------

def _absent(folder: Path, files: Sequence[Tuple[str, int]]) -> List[Tuple[str, int]]:
    out = []
    for path, size in files:
        try:
            if (folder / path).stat().st_size > 0:
                continue
        except OSError:
            pass
        out.append((path, size))
    return out


def part_dir(part: Part, root: Optional[Path] = None) -> Path:
    """The folder that holds all of `part`: the first complete one in the models folder, then
    <repo>/weights (or only `root` when given); none complete: where it would be downloaded."""
    bases = [Path(root)] if root is not None else weights_paths.roots()
    for base in bases:
        for folder in (part.folder, *part.alt_folders):
            if not _absent(base / folder, part.files):
                return base / folder
    return (Path(root) if root is not None else weights_paths.root()) / part.folder


def model_dir(key: str) -> str:
    """The folder of the part named `key` (PARTS), as the engines load it."""
    return str(part_dir(PARTS[key]))


def fp8_dir_for(transformer_path: str) -> str:
    """Inpainting V2's FP8 transformer for the transformer at `transformer_path`: the catalog's
    when that is StereoCrafter2's own folder, else the folder beside it (its old rule)."""
    if os.path.normcase(os.path.abspath(transformer_path)) == os.path.normcase(os.path.abspath(model_dir("stereocrafter2"))):
        return model_dir("stereocrafter2_fp8")
    return transformer_path.rstrip("/\\") + "-FP8"


def label(part: Part) -> str:
    """The Models window's name for the row a part is in, without its bracket."""
    row = next((m for m in MODELS if part in m.parts), None)
    return (row.name if row is not None else part.name).split(" (")[0]


def missing_message(keys: Sequence[str], root: Optional[Path] = None) -> str:
    """"" when every part named is here; else what a stage tells the user before it loads."""
    lacking = list(dict.fromkeys(label(PARTS[k]) for k in keys if not installed([PARTS[k]], root)))
    if not lacking:
        return ""
    one = len(lacking) == 1
    where = Path(root) if root is not None else weights_paths.root()
    return (f"{' and '.join(lacking)} {'is' if one else 'are'} not in your models folder ({where}). Download "
            f"{'it' if one else 'them'} in Settings > Studio > Models, or point Studio at the folder that has "
            f"{'it' if one else 'them'} there.")


def missing(parts: Sequence[Part], root: Optional[Path] = None) -> List[Tuple[Part, str, int]]:
    """The files not on disk yet: (part, path, bytes)."""
    out = []
    for part in parts:
        folder = part_dir(part, root)
        out.extend((part, path, size) for path, size in _absent(folder, part.files))
    return out


def installed(parts: Sequence[Part], root: Optional[Path] = None) -> bool:
    return not missing(parts, root)


def to_download(parts: Sequence[Part], root: Optional[Path] = None) -> int:
    """Bytes still to come for these parts."""
    return sum(n for _p, _f, n in missing(parts, root))


def free_space(root: Optional[Path] = None) -> int:
    probe = Path(root) if root is not None else weights_paths.root()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(str(probe)).free


def _bundled_transnetv2() -> Optional[Path]:
    try:
        spec = importlib.util.find_spec("transnetv2_pytorch")
    except Exception:
        return None
    if spec is None or not spec.origin:
        return None
    f = Path(spec.origin).parent / "transnetv2-pytorch-weights.pth"
    return f if f.is_file() else None


def status(model: Model) -> Dict[str, object]:
    """A row of the Models window: state "installed" | "missing" | "partial", where it is, the size
    and what's left to download."""
    row: Dict[str, object] = {"key": model.key, "name": model.name, "usedBy": model.used_by, "gated": model.gated,
                              "page": model.page, "kind": model.kind, "note": model.note, "tip": model.tip,
                              "where": "",
                              "size": "", "left": "", "leftBytes": 0}
    if model.kind == "rose":
        from core.removal import rose_env
        row["state"] = "installed" if rose_env.ready() else "missing"
        row["where"] = str(rose_env.ROSE_DIR)
        row["size"] = f"{rose_env.DOWNLOAD_GB:.1f} GB"
        return row
    if model.kind == "package":
        row["state"] = "installed" if packages_ready(model) else "missing"
        row["where"] = _site_packages() if row["state"] == "installed" else ""
        row["size"] = model.installed_size
        return row
    if model.kind == "bundled":
        own = weights_paths.find("SceneCut", "transnetv2-pytorch-weights.pth")
        f = own if own.is_file() else _bundled_transnetv2()
        row["state"] = "installed" if f is not None else "missing"
        row["where"] = str(f) if f is not None else ""
        return row
    if model.kind == "manual":
        f = weights_paths.find("SceneCut", model.manual_file)
        row["state"] = "installed" if f.is_file() else "missing"
        row["where"] = str(f if f.is_file() else f.parent)
        return row
    todo = missing(model.parts)
    left = sum(n for _p, _f, n in todo)
    total = sum(p.size for p in model.parts)
    row["state"] = "installed" if not todo else ("partial" if left < total or _any_there(model) else "missing")
    row["where"] = "; ".join(dict.fromkeys(str(part_dir(p)) for p in model.parts))
    row["size"] = gb(total)
    row["left"] = gb(left)
    row["leftBytes"] = left
    return row


def _any_there(model: Model) -> bool:
    return any((part_dir(p) / f).is_file() for p in model.parts for f, _n in p.files)


# ---- the download ---------------------------------------------------------------------------

def _part_files_dir(folder: Path) -> Path:
    return folder / ".cache" / "huggingface" / "download"


def _on_disk(todo: Sequence[Tuple[Part, str, int]], dirs: Dict[str, Path]) -> int:
    """Bytes of these files on disk: the finished ones, plus every part-file in their folders."""
    done = 0
    for part, path, _size in todo:
        try:
            done += (dirs[part.key or part.folder] / path).stat().st_size
        except OSError:
            pass
    for folder in {dirs[part.key or part.folder] for part, _p, _s in todo}:
        for f in glob.glob(os.path.join(glob.escape(str(_part_files_dir(folder))), "**", "*.incomplete"),
                           recursive=True):
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

#: one file over HTTPS, resumed from its part-file with a Range request
_FETCH_URL = (
    "import os, sys, urllib.request\n"
    "url, dest, part = sys.argv[1:4]\n"
    "os.makedirs(os.path.dirname(part), exist_ok=True)\n"
    "have = os.path.getsize(part) if os.path.exists(part) else 0\n"
    "req = urllib.request.Request(url, headers={'Range': 'bytes=%d-' % have} if have else {})\n"
    "print('Downloading ' + url, flush=True)\n"
    "with urllib.request.urlopen(req, timeout=60) as r:\n"
    "    with open(part, 'ab' if have and r.status == 206 else 'wb') as f:\n"
    "        while True:\n"
    "            b = r.read(1 << 20)\n"
    "            if not b:\n"
    "                break\n"
    "            f.write(b)\n"
    "os.replace(part, dest)\n"
)


class Stopped(Exception):
    pass


def download(parts: Sequence[Part], progress: Callable[[float, str], None], stop: threading.Event,
             root: Optional[Path] = None, python: Optional[str] = None, poll_s: float = 2.0,
             procs: Optional[list] = None, wait: Optional[Callable[[], str]] = None) -> None:
    """Fetch every missing file of `parts` (and their extras) into the models folder (or `root`).
    progress(0..1, text) every `poll_s`. `wait()` returning a reason (a job running) holds each
    part back until it returns "". Raises Stopped on a Stop (the part-files stay for the next
    run), RuntimeError on a failure."""
    dirs = {p.key or p.folder: part_dir(p, root) for p in parts}
    todo = missing(parts, root)
    for part in parts:
        if any(p is part for p, _f, _n in todo):
            todo.extend((part, path, size) for path, size in _absent(dirs[part.key or part.folder], part.extras))
    total = sum(n for _p, _f, n in todo) or 1
    start_bytes = _on_disk(todo, dirs)
    began = time.monotonic()
    for part in parts:
        files = [path for p, path, _n in todo if p is part]
        if not files:
            continue
        while wait is not None:
            why = wait()
            if not why:
                break
            progress(min(1.0, _on_disk(todo, dirs) / total), f"Waiting: {why}")
            if stop.wait(poll_s):
                raise Stopped()
            began = time.monotonic()
            start_bytes = _on_disk(todo, dirs)
        folder = dirs[part.key or part.folder]
        folder.mkdir(parents=True, exist_ok=True)
        if part.url:
            dest = folder / files[0]
            args = [_FETCH_URL, part.url, str(dest), str(_part_files_dir(folder) / (files[0] + ".incomplete"))]
        else:
            args = [_FETCH, part.repo, part.revision, str(folder), *files]
        env = dict(os.environ, HF_HUB_DISABLE_PROGRESS_BARS="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([python or sys.executable, "-c", *args],
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
                done = _on_disk(todo, dirs)
                rate = max(0, done - start_bytes) / max(1e-6, time.monotonic() - began)
                left = f", about {_eta((total - done) / rate)} left" if rate > 1e6 else ""
                progress(min(1.0, done / total), f"{part.name}: {gb(done)} of {gb(total)}{left}")
        finally:
            if proc.poll() is None:
                proc.kill()
            reader.join(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
            if procs is not None and proc in procs:
                procs.remove(proc)
        if proc.returncode != 0:
            tail = "".join(lines[-6:]).strip()
            raise RuntimeError(f"Downloading {part.name} failed (exit {proc.returncode}).\n\n{_explain(tail)}")
        if part.url and part.sha256:
            _check_sha256(folder / files[0], part.sha256)
    still = missing(parts, root)
    if still:
        raise RuntimeError("Still missing after the download: " + ", ".join(f"{p.folder}/{f}" for p, f, _n in still))
    weights_paths.refresh()
    progress(1.0, "Downloaded.")


def _check_sha256(path: Path, want: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    if h.hexdigest() != want:
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError(f"{path.name} came down damaged (its checksum doesn't match). Download it again.")


def _explain(tail: str) -> str:
    """The child's last lines, with what to do about the usual Hugging Face refusals."""
    low = tail.lower()
    if "gatedrepo" in low or "403" in low or "awaiting a review" in low or "restricted" in low:
        return (tail + "\n\nThis model is gated: open its page, request access, and make sure you are signed "
                "in to Hugging Face (Settings > Studio > Models).")
    if "401" in low:
        return tail + "\n\nHugging Face wants a signed-in account for this one: sign in in Settings > Studio > Models."
    return tail


def _eta(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else (f"{m} min" if m else "a minute")


# ---- packages (kind "package") --------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]


def packages_ready(model: Model) -> bool:
    """Every module of `model` importable in Studio's environment (found, not imported)."""
    importlib.invalidate_caches()      # a folder listed before the install would hide what it put there
    try:
        return all(importlib.util.find_spec(m) is not None for m in model.modules)
    except (ImportError, ValueError):
        return False


def _site_packages() -> str:
    import sysconfig
    return sysconfig.get_paths().get("purelib", "")


def pins(extra: str, lock: Optional[Path] = None) -> List[str]:
    """The extra's packages at the versions uv.lock pins ("name==version"); bare names without a lock."""
    import tomllib
    try:
        with open(lock or _REPO / "uv.lock", "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        data = {}
    pkgs = data.get("package", [])
    versions = {p.get("name"): p.get("version") for p in pkgs}
    for p in pkgs:
        names = [d.get("name") for d in p.get("optional-dependencies", {}).get(extra, [])]
        if names and (p.get("source", {}).get("editable") or p.get("source", {}).get("virtual")):
            return [f"{n}=={versions[n]}" if versions.get(n) else n for n in names]
    if extra == "tensorrt":
        return ["tensorrt-cu13", "onnx", "onnxscript"]
    raise RuntimeError(f"No packages known for '{extra}'.")


def _uv() -> List[str]:
    """uv: on PATH, or where its installer puts it."""
    found = shutil.which("uv")
    if found is None:
        cand = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        found = str(cand) if cand.is_file() else None
    if found is None:
        raise RuntimeError("Installing needs uv, the tool Studio's installer and updater use, and it isn't on "
                           "this PC's PATH. Run the Studio installer again, or install uv from "
                           "https://docs.astral.sh/uv/ and try once more.")
    return [found]


def install_packages(model: Model, progress: Callable[[float, str], None], stop: threading.Event,
                     python: Optional[str] = None, poll_s: float = 2.0, procs: Optional[list] = None,
                     wait: Optional[Callable[[], str]] = None) -> None:
    """`uv pip install` the model's extra into Studio's environment (or `python`'s). progress(-1, text):
    uv says no sizes, so the bar has no fraction. `wait()` holds it back while a job runs. Raises
    Stopped on a Stop (uv's cache keeps what came down), RuntimeError on a failure."""
    while wait is not None:
        why = wait()
        if not why:
            break
        progress(-1.0, f"Waiting: {why}")
        if stop.wait(poll_s):
            raise Stopped()
    cmd = [*_uv(), "pip", "install", "--python", python or sys.executable, *pins(model.extra)]
    logger_line = " ".join(cmd)
    progress(-1.0, f"{model.name}: starting")
    env = dict(os.environ, NO_COLOR="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if procs is not None:
        procs.append(proc)
    lines: List[str] = []
    reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
    reader.start()
    began = time.monotonic()
    try:
        while proc.poll() is None:
            if stop.wait(poll_s):
                proc.kill()
                proc.wait()
                raise Stopped()
            said = next((s.strip() for s in reversed(lines) if s.strip()), "working")
            took = int(time.monotonic() - began)
            progress(-1.0, f"{model.name}: {said[:90]} ({took // 60}:{took % 60:02d})")
    finally:
        if proc.poll() is None:
            proc.kill()
        reader.join(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if procs is not None and proc in procs:
            procs.remove(proc)
    if proc.returncode != 0:
        tail = "".join(lines[-8:]).strip()
        raise RuntimeError(f"Installing {model.name} failed (exit {proc.returncode}).\n\n> {logger_line}\n{tail}")
    if not packages_ready(model):
        raise RuntimeError(f"uv finished, but {', '.join(model.modules)} still can't be found in "
                           f"{_site_packages()}.")
    progress(1.0, "Installed.")
