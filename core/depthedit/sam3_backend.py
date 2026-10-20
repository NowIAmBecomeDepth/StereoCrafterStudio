"""sam3_backend.py -- Meta's SAM 3, vendored, driven for the Depth Map Editor.

Where SAM 3 comes from
----------------------
third_party/sam3 holds the inference half of facebookresearch/sam3, byte-for-
byte, so a later upstream release can be re-synced by diffing. It is vendored
rather than installed because upstream's package pins numpy<2 while this
suite runs numpy 2.x; the code itself works on numpy 2 (checked), only the
pin is wrong for us. Its real dependencies -- timm, iopath, scipy,
pycocotools -- are in pyproject.toml. Everything Studio-side lives here.

Which model
-----------
SAM 3 (``facebook/sam3``, ``sam3.pt``) is the model this editor uses and what
Download Weights fetches. SAM 3.1 (``facebook/sam3.1``) is wired in but OFF
(``SAM31_ENABLED``), because on this suite's Windows stack it was measured
worse on every count that matters here (2026-09-22, RTX 5060 Ti 16 GB, real
footage, text prompts):

  * the same masks -- mean IoU against SAM 3 of 0.99 ("person") and 0.97
    ("hair") on a 35-frame clip;
  * about 5x slower to track (72 s vs 15 s) and a 22 GB peak on a 16 GB
    card, so Windows spills VRAM into system RAM;
  * clicks and boxes broken upstream: a click-only object returns no mask
    and is dropped after its frame (3.1 builds click results from a per-
    frame cache that only a text prompt or a full propagation fills), and a
    text prompt plus a clicked object trips an internal assertion.

Its advantage -- Object Multiplex, ~7x faster at 128 objects -- needs
FlashAttention 3 on datacenter GPUs and many objects; this editor tracks a
handful. Re-test before switching it on: run the editor's click, box, Find
and Track on a real clip and compare against SAM 3.

What this module does that upstream's quick-start does not
----------------------------------------------------------
  * SAM 3: builds ``Sam3VideoPredictor`` directly. ``build_sam3_video_predictor``
    returns the multi-GPU wrapper, which starts an NCCL process group, and
    NCCL does not exist on Windows. With one GPU nothing in the single-GPU
    class touches torch.distributed (every collective is behind
    ``world_size > 1``).
  * SAM 3.1: builds with ``use_fa3=False``. Upstream defaults to FlashAttention
    3, which has no Windows build. No Hydra is needed -- the builder uses the
    wrapper that skips it.
  * Lets attention fall back. With FA3 off, upstream's decoder runs SDPA
    under ``sdpa_kernel(SDPBackend.FLASH_ATTENTION)`` -- flash ONLY -- and the
    Windows PyTorch wheels ship no flash kernels (measured on torch 2.13
    cu130: "No available kernel" for every dtype, while cuDNN and
    memory-efficient attention both run). So every tracking step failed.
    ``_allow_sdpa_fallbacks`` rebinds the decoder module's ``sdpa_kernel`` so
    flash stays first choice and the others follow.
  * SAM 3.1: starts sessions itself (``_start_multiplex_session``). The shared
    ``start_session`` always passes ``offload_state_to_cpu``, which the 3.1
    model's ``init_state`` does not accept -- upstream's 3.1 path raises a
    TypeError on its first session. Worked around here rather than in
    third_party so the vendored copy stays byte-identical.
  * SAM 3.1's predictor ENTERS a bf16 autocast context in its constructor and
    leaves it open. Autocast is per-thread, so the model is built, used and
    unloaded on the editor's one worker thread, and ``unload`` closes it.
  * Loads the checkpoint from weights/SAM3/, beside the suite's other weights,
    instead of letting the builder fetch it into the HF cache on first use.
    The repos are gated, so the fetch is a deliberate button with a readable
    failure, not a surprise inside "Load model".
  * Supplies ``pkg_resources`` if setuptools has dropped it. Upstream's
    model_builder imports it at module level to find its BPE vocabulary;
    this module always passes that path explicitly, so a stand-in that
    answers resource_filename() is all the import needs.

One session at a time
---------------------
A text prompt RESETS the SAM 3 session -- it is a concept search over the
whole clip -- so a session can only ever hold one layer's objects. The editor
therefore gives the session to one layer at a time and, when another layer is
selected, resets and replays that layer's prompts (``replay``). The frames
stay loaded across resets; only switching between the RGB and depth videos
costs a reload.
"""

from __future__ import annotations

import gc
import importlib.util
import logging
import os
import sys
import threading
import types
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_DIR = _REPO_ROOT / "third_party" / "sam3"
WEIGHTS_DIR = _REPO_ROOT / "weights" / "SAM3"
BPE_PATH = VENDOR_DIR / "sam3" / "assets" / "bpe_simple_vocab_16e6.txt.gz"

#: version -> (display name, Hugging Face repo, checkpoint file). Preference
#: order: the first one present is the one loaded.
MODELS: Dict[str, Tuple[str, str, str]] = {
    "sam3": ("SAM 3", "facebook/sam3", "sam3.pt"),
    "sam3.1": ("SAM 3.1", "facebook/sam3.1", "sam3.1_multiplex.pt"),
}
#: What Download Weights fetches.
DEFAULT_VERSION = "sam3"
#: SAM 3.1 is never loaded while this is False, even if its checkpoint is on
#: disk. See "Which model" above for why it is off.
SAM31_ENABLED = False
HF_PAGE = f"https://huggingface.co/{MODELS[DEFAULT_VERSION][1]}"

#: import name -> the pyproject package that provides it. The last four are
#: already in the suite for other reasons and listed so a broken environment
#: says what is missing rather than failing somewhere inside the model.
REQUIRED_MODULES: Dict[str, str] = {
    "timm": "timm",
    "iopath": "iopath",
    "scipy": "scipy",
    "pycocotools": "pycocotools",
    "ftfy": "ftfy",
    "regex": "regex",
    "psutil": "psutil",
    "einops": "einops",
}


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def missing_packages() -> List[str]:
    """pyproject package names whose modules cannot be found."""
    return [pkg for mod, pkg in REQUIRED_MODULES.items() if importlib.util.find_spec(mod) is None]


def vendored_present() -> bool:
    return (VENDOR_DIR / "sam3" / "model_builder.py").is_file() and BPE_PATH.is_file()


def checkpoint_path(version: str) -> Path:
    return WEIGHTS_DIR / MODELS[version][2]


def _present(version: str) -> bool:
    try:
        path = checkpoint_path(version)
        return path.is_file() and path.stat().st_size > 100_000_000
    except OSError:
        return False


def active_version() -> Optional[str]:
    """The checkpoint that will be loaded: the first enabled one on disk, or None."""
    return next((v for v in MODELS if (v != "sam3.1" or SAM31_ENABLED) and _present(v)), None)


def default_present() -> bool:
    """Whether the checkpoint new installs download is here."""
    return _present(DEFAULT_VERSION)


def weights_present() -> bool:
    return active_version() is not None


def model_label(version: Optional[str] = None) -> str:
    return MODELS[version or active_version() or DEFAULT_VERSION][0]


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


class WeightsAccessError(RuntimeError):
    """The account cannot download the gated checkpoint (yet)."""


def download_weights(progress: Optional[Callable[[str], None]] = None,
                     version: str = DEFAULT_VERSION) -> Path:
    """Fetch a checkpoint from Hugging Face into weights/SAM3.

    Needs a logged-in `hf` account that has been granted access on the model
    page. A 403 is turned into WeightsAccessError with what to do about it.
    """
    label, repo, filename = MODELS[version]
    page = f"https://huggingface.co/{repo}"
    from huggingface_hub import hf_hub_download

    try:
        from huggingface_hub.errors import GatedRepoError
    except Exception:  # older hub
        from huggingface_hub.utils import GatedRepoError  # type: ignore

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"Downloading {filename} (about 3.4 GB) from Hugging Face...")
    try:
        path = hf_hub_download(repo_id=repo, filename=filename, local_dir=str(WEIGHTS_DIR))
    except GatedRepoError as e:
        raise WeightsAccessError(
            f"Your Hugging Face account does not have access to {label} yet.\n\n"
            f"1. Open {page} and request access (Meta usually approves quickly).\n"
            "2. Make sure this machine is logged in: run `hf auth login`.\n"
            "3. Press Download Weights again."
        ) from e
    return Path(path)


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def _ensure_pkg_resources() -> None:
    try:
        import pkg_resources  # noqa: F401

        return
    except Exception:
        pass
    shim = types.ModuleType("pkg_resources")

    def resource_filename(package: str, resource: str) -> str:
        spec = importlib.util.find_spec(package)
        base = os.path.dirname(spec.origin) if spec and spec.origin else str(VENDOR_DIR / package)
        return os.path.join(base, resource)

    shim.resource_filename = resource_filename  # type: ignore[attr-defined]
    sys.modules["pkg_resources"] = shim


def _import_sam3():
    """Put the vendored package on sys.path and import its builder module."""
    if not vendored_present():
        raise RuntimeError(f"SAM 3 source is missing from {VENDOR_DIR}.")
    missing = missing_packages()
    if missing:
        raise RuntimeError("SAM 3 needs packages that are not installed: " + ", ".join(missing))
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    _ensure_pkg_resources()
    import warnings

    with warnings.catch_warnings():
        # model_builder's `import pkg_resources` warns on every setuptools
        # that still has it; the warning is about upstream, not the user.
        warnings.simplefilter("ignore", UserWarning)
        import sam3.model_builder as builder
    _allow_sdpa_fallbacks()
    return builder


def _allow_sdpa_fallbacks() -> None:
    """Let SAM's forced-flash attention fall back to the backends Windows has.

    Rebinds the name inside sam3.model.decoder only, so nothing else in the
    process is affected and third_party stays byte-identical. Idempotent.
    """
    import sam3.model.decoder as decoder
    from torch.nn.attention import SDPBackend, sdpa_kernel

    if getattr(decoder.sdpa_kernel, "_studio_fallbacks", False):
        return
    fallbacks = [SDPBackend.FLASH_ATTENTION, SDPBackend.CUDNN_ATTENTION,
                 SDPBackend.EFFICIENT_ATTENTION, SDPBackend.MATH]

    def with_fallbacks(backends, *args, **kwargs):
        wanted = list(backends) if isinstance(backends, (list, tuple)) else [backends]
        return sdpa_kernel(wanted + [b for b in fallbacks if b not in wanted], *args, **kwargs)

    with_fallbacks._studio_fallbacks = True  # type: ignore[attr-defined]
    decoder.sdpa_kernel = with_fallbacks


def _flash_attention_3() -> bool:
    return importlib.util.find_spec("flash_attn_interface") is not None


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

#: What a prompt or a propagated frame hands back: object ids and one full-
#: resolution boolean mask per object.
FrameResult = Tuple[List[int], np.ndarray]


def _unpack(outputs: Optional[dict]) -> FrameResult:
    if not outputs:
        return [], np.zeros((0, 1, 1), bool)
    ids = [int(i) for i in np.asarray(outputs.get("out_obj_ids", [])).reshape(-1)]
    masks = np.asarray(outputs.get("out_binary_masks", np.zeros((0, 1, 1), bool))).astype(bool)
    return ids, masks


# ---- VRAM: SAM 3's per-frame mask cache, kept in system memory (2026-10-05) ----
#
# For every frame it tracks, SAM 3 keeps each object's mask at the VIDEO's full
# size, on the GPU, until the session is reset (inference_state
# ["cached_frame_outputs"]): ~2 MB an object a frame at 1920x1038, 1.5 GB an
# object over a 750-frame scene -- the user's "eats up my VRAM, and a second
# object slows it right down". It only reads one frame's back now and then
# (re-prompting a frame, removing an object). So the cache lives here instead:
# each mask bit-packed in system memory (~250 KB), unpacked onto the GPU when
# SAM 3 reads it. Swapped in by Sam3Engine.open; third_party stays untouched.

class _PackedFrame(dict):
    """One frame's {obj_id: mask}: stored packed on the CPU, read as GPU tensors."""

    def __init__(self, masks=None):
        super().__init__()
        for k, v in (masks or {}).items():
            self[k] = v

    def __setitem__(self, obj_id, mask) -> None:
        import torch
        if isinstance(mask, torch.Tensor):
            shape, device = tuple(mask.shape), mask.device
            bits = np.packbits(mask.detach().to("cpu").numpy().astype(bool).ravel())
            dict.__setitem__(self, obj_id, (bits, shape, device))
        else:
            dict.__setitem__(self, obj_id, mask)

    @staticmethod
    def _unpack(entry):
        if not isinstance(entry, tuple) or len(entry) != 3:
            return entry
        import torch
        bits, shape, device = entry
        n = int(np.prod(shape))
        arr = np.unpackbits(bits, count=n).astype(bool).reshape(shape)
        return torch.from_numpy(arr).to(device)

    def __getitem__(self, obj_id):
        return self._unpack(dict.__getitem__(self, obj_id))

    def get(self, obj_id, default=None):
        return self[obj_id] if dict.__contains__(self, obj_id) else default

    def items(self):
        return [(k, self[k]) for k in dict.keys(self)]

    def values(self):
        return [self[k] for k in dict.keys(self)]

    def copy(self):
        return {k: self[k] for k in dict.keys(self)}


class _PackedFrames(dict):
    """cached_frame_outputs: {frame: _PackedFrame}; a plain dict set in is packed."""

    def __setitem__(self, frame, masks) -> None:
        dict.__setitem__(self, frame, masks if isinstance(masks, _PackedFrame) else _PackedFrame(masks))


def _pack_mask_cache(state) -> None:
    """Swap a session's full-size mask cache for the packed, CPU-side one."""
    cache = state.get("cached_frame_outputs") if isinstance(state, dict) else None
    if cache is None or isinstance(cache, _PackedFrames):
        return
    packed = _PackedFrames()
    for frame, masks in cache.items():
        packed[frame] = masks
    state["cached_frame_outputs"] = packed


class _HostFrames:
    """A session's frames kept in system memory (float16, as SAM 3's own
    offloading loaders keep them), each sent to the GPU as float32 when it is
    read. Every reader in SAM 3's video path indexes the batch and moves the
    frame itself; this does the move for the ones that don't."""

    def __init__(self, frames, device) -> None:
        self._frames = frames
        self._device = device

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def shape(self):
        return self._frames.shape

    def __getitem__(self, idx):
        import torch
        if torch.is_tensor(idx):
            idx = idx.cpu()
        return self._frames[idx].to(self._device, dtype=torch.float32, non_blocking=True)

    def as_batch(self, frame_ids):
        """The frame(s) `frame_ids` on the GPU, seen as the whole clip's batch
        (an expanded view: no more memory): indexed as SAM 3 indexes it, the
        frame comes out in exactly the layout of upstream's GPU batch."""
        import torch
        one = self._frames[frame_ids.cpu()].to(self._device, dtype=torch.float32, non_blocking=True)
        return one.expand(len(self._frames), -1, -1, -1)


def _detector_reads_host_frames(detector) -> None:
    """The detector's image read, for frames kept in system memory.

    Given a batch that isn't a tensor, SAM 3 reads one frame and unsqueezes
    it; the values are the same, but the frame's batch stride is not (3, not
    the batch's), which picks another convolution kernel -- tiny numeric
    differences that tracking carries on (0.2% of mask pixels on the check's
    clip). Handed the frame as an expanded batch it takes upstream's own path,
    and its masks are bit-identical to upstream's."""
    if getattr(detector, "_studio_host_frames", False):
        return
    import torch
    real = detector._get_img_feats

    def feats(backbone_out, img_ids):
        frames = backbone_out.get("img_batch_all_stages")
        if isinstance(frames, _HostFrames) and "backbone_fpn" not in backbone_out:
            ids = torch.unique(img_ids) if img_ids.numel() > 1 else img_ids
            if ids.numel() == 1:
                backbone_out = {**backbone_out, "img_batch_all_stages": frames.as_batch(ids)}
        return real(backbone_out, img_ids)

    detector._get_img_feats = feats
    detector._studio_host_frames = True


#: The kept frames' precision in system memory.
HOST_FRAME_DTYPE = "float16"


#: SAM 3's tracking sped up (user, 2026-10-17: "anything to help speed up tracking from sam3?"; measured in
#: prototypes/sam3_bench: 369 -> 292 ms a frame, peak 4.3 -> 3.3 GB, every mask bit-identical). SAM 3 only --
#: 3.1 is another model. False builds the model exactly as upstream does.
TRACK_SPEEDUPS = True

#: The detector's outputs that SAM 3 reads back from a frame's detection buffer.
_DET_KEYS = ("pred_logits", "pred_boxes", "pred_boxes_xyxy", "pred_masks")


def _skip_dropped_detections(model) -> None:
    """A click/box layer's per-frame step runs the backbone only.

    Tracking objects made by clicks ("propagation_partial"), SAM 3 runs the
    whole detector on every frame for the backbone's features alone, and drops
    its detections (``_prepare_backbone_feats``: ``_ = ...``) -- a fifth of each
    frame. There the detector's encoder, decoder and mask heads are skipped and
    stand-in outputs with no detection are handed back; the backbone, and so the
    tracker's features, run as before. A text layer's own tracking finds new
    objects on every frame and keeps the full detector. SAM 3 buffers the next
    frame's detection ahead, backbone and all, so the stand-ins stay in its
    buffer while clicks are tracked (dropped, each frame's backbone ran twice)
    and go the moment a full detection reads it: a text pass never sees one."""
    import torch

    det = getattr(model, "detector", None)
    if det is None or not hasattr(model, "_prepare_backbone_feats") or hasattr(det, "_studio_skip"):
        return
    det._studio_skip = False
    shapes: dict = {}          # the real outputs' shapes, from the first real run
    stand_ins: set = set()     # storages of the stand-ins in SAM 3's buffer
    real_grounding = det.forward_grounding
    real_video_grounding = det.forward_video_grounding_multigpu
    real_prepare = model._prepare_backbone_feats

    def storages(buffer):
        return {e["pred_logits"][0].untyped_storage().data_ptr(): f for f, e in buffer.items() if "pred_logits" in e}

    def forward_grounding(backbone_out, find_input, find_target, geometric_prompt, **kw):
        if not det._studio_skip or not shapes:
            out = real_grounding(backbone_out=backbone_out, find_input=find_input, find_target=find_target,
                                 geometric_prompt=geometric_prompt, **kw)
            if not shapes:
                shapes.update({k: (tuple(out[k].shape), out[k].dtype, out[k].device) for k in _DET_KEYS})
            return out
        _prompt, _mask, backbone_out = det._encode_prompt(backbone_out, find_input, geometric_prompt)
        out = {"prev_encoder_out": {"backbone_out": backbone_out}}
        for k, (shape, dtype, device) in shapes.items():
            out[k] = torch.zeros(shape, dtype=dtype, device=device)
        out["pred_logits"].fill_(-1e4)                  # no detection anywhere
        stand_ins.add(out["pred_logits"].untyped_storage().data_ptr())
        return out

    def video_grounding(*a, **kw):
        buffer = kw.get("multigpu_buffer")
        if not det._studio_skip and stand_ins and buffer:
            for ptr, f in storages(buffer).items():   # a full detection: the stand-ins out of its way
                if ptr in stand_ins:
                    buffer.pop(f, None)
            stand_ins.clear()
        return real_video_grounding(*a, **kw)

    def prepare(inference_state, frame_idx, reverse):
        det._studio_skip = True
        try:
            return real_prepare(inference_state, frame_idx, reverse)
        finally:
            det._studio_skip = False
            buffer = inference_state.get("feature_cache", {}).get("multigpu_buffer") or {}
            stand_ins.intersection_update(storages(buffer))   # only those SAM 3 still holds

    det.forward_grounding = forward_grounding
    det.forward_video_grounding_multigpu = video_grounding
    model._prepare_backbone_feats = prepare


def _trunk_weights_bf16(model) -> None:
    """The image backbone's linear weights kept in bf16.

    SAM 3 runs under bf16 autocast, which casts every weight of the ViT to
    bf16 again on every frame (8% of the backbone's time); kept in bf16 they
    are the same values, and 0.8 GB less. The trunk keeps its own autocast so
    a call outside SAM 3's runs as it would have."""
    import torch

    trunk = getattr(getattr(getattr(model, "detector", None), "backbone", None), "vision_backbone", None)
    trunk = getattr(trunk, "trunk", None)
    if trunk is None or getattr(trunk, "_studio_bf16", False):
        return
    for m in trunk.modules():
        if isinstance(m, torch.nn.Linear):
            m.to(torch.bfloat16)
    real = trunk.forward

    def forward(*a, **kw):
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            return real(*a, **kw)

    trunk.forward = forward
    trunk._studio_bf16 = True


@contextmanager
def _frames_stay_on_host():
    """While a SAM 3 session starts: its frames stay in system memory.

    Upstream's init_state loads the clip with offload_video_to_cpu honoured,
    then copies the whole input batch to the GPU -- every frame, 1008x1008x3
    float32, 12 MB each (user, 2026-10-05: 2 GB of a 175-frame scene, ~9 GB
    of a 750-frame one). The copy here leaves the frames out of it."""
    import torch
    from sam3.model import sam3_video_inference as vi
    real = vi.copy_data_to_device

    def copy(data, device, *a, **kw):
        frames = getattr(data, "img_batch", None)
        if not torch.is_tensor(frames):
            return real(data, device, *a, **kw)
        data.img_batch = None
        try:
            out = real(data, device, *a, **kw)
        finally:
            data.img_batch = frames
        out.img_batch = _HostFrames(frames.to("cpu", getattr(torch, HOST_FRAME_DTYPE)), device)
        return out

    vi.copy_data_to_device = copy
    try:
        yield
    finally:
        vi.copy_data_to_device = real


class Sam3Engine:
    """One SAM 3 video predictor and at most one open session.

    Not thread-safe: the editor calls it from a single worker thread only.
    """

    def __init__(self) -> None:
        self._predictor = None
        #: Which checkpoint is loaded ("sam3.1" / "sam3"), None when unloaded.
        self.version: Optional[str] = None
        self._session: Optional[str] = None
        #: (resource path, offload) the open session was started on.
        self._session_key: Optional[Tuple[str, bool]] = None
        #: uid of the layer whose prompts the session currently holds.
        self.owner: Optional[str] = None
        self.num_frames = 0
        self._cancel = threading.Event()
        #: Frames prompted since the last reset. See propagate().
        self._prompted: set = set()

    # -- model -----------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._predictor is not None

    def load(self) -> None:
        """Build the model. Call from the worker thread (see the module notes)."""
        if self._predictor is not None:
            return
        if not cuda_available():
            raise RuntimeError("SAM 3 needs an NVIDIA GPU with CUDA; none is available to PyTorch.")
        version = active_version()
        if version is None:
            raise RuntimeError(f"No SAM 3 weights in {WEIGHTS_DIR}. Use Download Weights first.")
        builder = _import_sam3()
        ckpt = checkpoint_path(version)
        logger.info("Loading %s from %s ...", model_label(version), ckpt)
        # Synchronous frame loading for both: the async loader starts a
        # thread per session that keeps decoding after a reset, and this
        # editor resets often.
        if version == "sam3.1":
            self._predictor = builder.build_sam3_multiplex_video_predictor(
                checkpoint_path=str(ckpt),
                bpe_path=str(BPE_PATH),
                use_fa3=_flash_attention_3(),
                async_loading_frames=False,
            )
        else:
            from sam3.model.sam3_video_predictor import Sam3VideoPredictor

            self._predictor = Sam3VideoPredictor(
                checkpoint_path=str(ckpt),
                bpe_path=str(BPE_PATH),
                async_loading_frames=False,
                video_loader_type="cv2",
            )
            if TRACK_SPEEDUPS:
                _skip_dropped_detections(self._predictor.model)
                _trunk_weights_bf16(self._predictor.model)
        self.version = version
        logger.info("%s loaded.", model_label(version))

    def unload(self) -> None:
        """Free the model. Call from the thread that loaded it."""
        self.close_session()
        predictor, self._predictor = self._predictor, None
        version, self.version = self.version, None
        if predictor is not None:
            if version == "sam3.1":
                # Its constructor entered a bf16 autocast it never leaves
                # (SAM 3's own shutdown() closes its equivalent). Left open,
                # autocast stays on in this thread after the model is gone.
                try:
                    predictor.bf16_context.__exit__(None, None, None)
                except Exception:
                    logger.debug("closing SAM 3.1's autocast raised", exc_info=True)
            try:
                predictor.shutdown()
            except Exception:
                logger.debug("SAM 3 shutdown raised", exc_info=True)
            del predictor
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # -- session ---------------------------------------------------------

    def open(self, resource_path: str, offload_frames: bool) -> int:
        """Load a video's frames into a session. Reuses one that matches."""
        key = (os.path.normcase(os.path.abspath(resource_path)), bool(offload_frames))
        if self._session is not None and self._session_key == key:
            return self.num_frames
        self.close_session()
        self.load()
        if self.version == "sam3.1":
            self._session = self._start_multiplex_session(resource_path, bool(offload_frames))
        else:
            # Offload frames also keeps the tracker's per-frame memory in system
            # memory (SAM 3's offload_state_to_cpu; 2026-10-05): it grows with
            # every frame tracked, and only the few frames the tracker attends
            # to go back to the GPU.
            with (_frames_stay_on_host() if offload_frames else nullcontext()):
                resp = self._predictor.handle_request(dict(
                    type="start_session", resource_path=resource_path,
                    offload_video_to_cpu=bool(offload_frames),
                    offload_state_to_cpu=bool(offload_frames),
                ))
            if offload_frames:
                _detector_reads_host_frames(self._predictor.model.detector)
            self._session = resp["session_id"]
        self._session_key = key
        self.owner = None
        state = self._predictor._all_inference_states[self._session]["state"]
        _pack_mask_cache(state)              # the full-size masks in system memory
        self.num_frames = int(state["num_frames"])
        return self.num_frames

    def _start_multiplex_session(self, resource_path: str, offload_frames: bool) -> str:
        """Sam3BasePredictor.start_session, minus the argument 3.1 rejects.

        Registers the session exactly as upstream does, so every other request
        (prompts, propagation, reset, close) goes through handle_request
        unchanged.
        """
        import time
        import uuid

        import torch

        with torch.inference_mode():
            state = self._predictor.model.init_state(
                resource_path=resource_path,
                offload_video_to_cpu=offload_frames,
                async_loading_frames=False,
                use_cv2=True,
            )
        session_id = str(uuid.uuid4())
        now = time.time()
        self._predictor._all_inference_states[session_id] = {
            "state": state, "session_id": session_id, "start_time": now, "last_use_time": now,
        }
        return session_id

    def close_session(self) -> None:
        if self._session is not None and self._predictor is not None:
            try:
                self._predictor.handle_request(dict(type="close_session", session_id=self._session))
            except Exception:
                logger.debug("close_session raised", exc_info=True)
        self._session = None
        self._session_key = None
        self.owner = None
        self._prompted.clear()

    def reset(self) -> None:
        self._require()
        self._predictor.handle_request(dict(type="reset_session", session_id=self._session))
        self._prompted.clear()

    # -- prompts ---------------------------------------------------------

    def add_text(self, frame: int, text: str) -> FrameResult:
        """Find every instance of a concept. Resets the session first (SAM 3 does)."""
        self._require()
        resp = self._predictor.handle_request(dict(
            type="add_prompt", session_id=self._session, frame_index=int(frame), text=text,
        ))
        self._prompted = {int(frame)}  # the text prompt reset everything before it
        return _unpack(resp.get("outputs"))

    def add_points(self, frame: int, obj_id: int, points: Sequence[Tuple[float, float]],
                   labels: Sequence[int]) -> FrameResult:
        """Every click on one object on one frame, relative 0..1 coordinates.

        Sent whole each time (clear_old_points): the tracker re-decodes from
        the full set, which is how an extra click refines rather than adds.
        """
        self._require()
        resp = self._predictor.handle_request(dict(
            type="add_prompt", session_id=self._session, frame_index=int(frame),
            points=[[float(x), float(y)] for x, y in points], point_labels=[int(v) for v in labels],
            obj_id=int(obj_id), clear_old_points=True, rel_coordinates=True,
        ))
        self._prompted.add(int(frame))
        return _unpack(resp.get("outputs"))

    def add_mask(self, frame: int, obj_id: int, mask: np.ndarray) -> FrameResult:
        """Make an exact mask a tracker object on `frame` (full video resolution)."""
        self._require()
        resp = self._predictor.handle_request(dict(
            type="add_mask", session_id=self._session, frame_index=int(frame), obj_id=int(obj_id),
            mask=mask,
        ))
        self._prompted.add(int(frame))
        return _unpack(resp.get("outputs"))

    def remove_object(self, obj_id: int) -> None:
        self._require()
        self._predictor.handle_request(dict(type="remove_object", session_id=self._session, obj_id=int(obj_id)))

    def replay(self, text: str, text_frame: int, clicks: Sequence) -> Dict[int, FrameResult]:
        """Reset and re-apply a layer's prompts. Returns each prompted frame's result.

        A text prompt and clicks never share a session as they are. Measured
        on SAM 3 (2026-09-22): ANY click after a text prompt -- a new object
        or just a remove-click refining a found one -- breaks tracking of
        every object, which then exist on the prompted frame only. Clicks on
        their own track fine, and so do objects given as masks. So when a
        layer has both, the text runs once to find its objects, the session
        is reset, each found object goes back in as its mask on the text
        frame, and the clicks apply on top. The cost: such a layer tracks
        the objects found on the text frame, where a text-only layer also
        picks up new instances that enter later.
        """
        self.reset()
        results: Dict[int, FrameResult] = {}
        if text:
            found = self.add_text(text_frame, text)
            results[int(text_frame)] = found
            if clicks:
                self.reset()
                for obj_id, mask in zip(*found):
                    results[int(text_frame)] = self.add_mask(text_frame, obj_id, mask)
        for (frame, obj_id), group in _group_clicks(clicks):
            # A box is its two corners as the object's FIRST points.
            group = sorted(group, key=lambda c: 0 if c.label in (2, 3) else 1)
            results[frame] = self.add_points(frame, obj_id, [(c.x, c.y) for c in group], [c.label for c in group])
        return results

    # -- tracking --------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def propagate(self, direction: str = "both", start_frame: Optional[int] = None) -> Iterator[Tuple[int, FrameResult]]:
        """Track every prompted object through the clip, both directions.

        ``direction`` "forward" / "backward" with ``start_frame``: only from
        that frame on, or back from it to the start (user, 2026-10-05: Track
        From Here, Track Backwards).

        Yields (frame, result) as SAM 3 produces them -- forwards from the
        first prompted frame, then backwards. cancel() stops it between
        frames.

        The start frame is passed explicitly. Left to itself, SAM 3.1 finds it
        from the frames a TEXT prompt ran on, and click-only prompts never
        mark theirs -- so a layer made purely of clicks and boxes raised "No
        prompts are received on any frames". SAM 3 finds the same frame
        either way.
        """
        self._require()
        self._cancel.clear()
        if direction not in ("both", "forward", "backward"):
            raise ValueError(f"no propagation direction {direction!r}")
        request = dict(type="propagate_in_video", session_id=self._session, propagation_direction=direction)
        if start_frame is not None:
            request["start_frame_index"] = int(start_frame)
        elif self._prompted:
            request["start_frame_index"] = min(self._prompted)
        stream = self._predictor.handle_stream_request(request)
        try:
            for resp in stream:
                if self._cancel.is_set():
                    break
                yield int(resp["frame_index"]), _unpack(resp.get("outputs"))
        finally:
            stream.close()
            # tracking's working memory stays reserved by PyTorch once it is
            # over -- GBs nothing else could use (user, 2026-10-05)
            from core.common import gpu_ops
            gpu_ops.vram_note(logger, "SAM 3 tracking", release=True)

    def _require(self) -> None:
        if self._predictor is None or self._session is None:
            raise RuntimeError("No SAM 3 session is open.")


def _group_clicks(clicks: Sequence) -> List[Tuple[Tuple[int, int], list]]:
    """Clicks grouped by (frame, object) in the order each group first appeared."""
    groups: Dict[Tuple[int, int], list] = {}
    for c in clicks:
        groups.setdefault((int(c.frame), int(c.obj_id)), []).append(c)
    return list(groups.items())


def union_mask(result: FrameResult, height: int, width: int) -> Optional[np.ndarray]:
    """All objects of a result as one mask, resized to (height, width)."""
    import cv2

    ids, masks = result
    if not ids or masks.size == 0:
        return None
    merged = masks.reshape(-1, masks.shape[-2], masks.shape[-1]).any(axis=0)
    if merged.shape != (height, width):
        # Area-average then threshold: nearest-neighbour would alias a thin
        # object's edge into a staircase at DepthCrafter's lower resolution.
        merged = cv2.resize(merged.astype(np.float32), (width, height), interpolation=cv2.INTER_AREA) >= 0.5
    return merged if merged.any() else None


class PackedMasks:
    """A result's masks, 1 bit a pixel (np.packbits along the width): what the
    editor keeps for every tracked frame (Studio audit 2026-09-28, PERF: as
    bool arrays, 2 MB an object a 1080p frame)."""

    __slots__ = ("bits", "shape")

    def __init__(self, masks: np.ndarray):
        m = np.asarray(masks, bool)
        m = m.reshape(-1, m.shape[-2], m.shape[-1]) if m.size else np.zeros((0, 1, 1), bool)
        self.shape = m.shape
        self.bits = np.packbits(m, axis=-1)

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    def at(self, index: int, py: int, px: int) -> bool:
        return bool((self.bits[index, py, px >> 3] >> (7 - (px & 7))) & 1)


def pack_result(result: FrameResult):
    """(ids, PackedMasks): a result to keep. object_at reads either."""
    ids, masks = result
    return list(ids), PackedMasks(masks)


def result_nbytes(result) -> int:
    masks = result[1]
    return int(masks.bits.nbytes if isinstance(masks, PackedMasks) else np.asarray(masks).nbytes)


def object_at(result: FrameResult, x: float, y: float) -> Optional[int]:
    """Which object's mask covers relative point (x, y) on this result, if any."""
    ids, masks = result
    if not ids or masks.size == 0:
        return None
    if isinstance(masks, PackedMasks):
        h, w = masks.shape[-2:]
        px, py = min(int(x * w), w - 1), min(int(y * h), h - 1)
        for i, obj_id in enumerate(ids):
            if masks.at(i, py, px):
                return obj_id
        return None
    masks = masks.reshape(-1, masks.shape[-2], masks.shape[-1])
    h, w = masks.shape[-2:]
    px, py = min(int(x * w), w - 1), min(int(y * h), h - 1)
    for obj_id, m in zip(ids, masks):
        if m[py, px]:
            return obj_id
    return None
