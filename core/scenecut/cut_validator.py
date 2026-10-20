"""
Cut Validation
--------------

AI validation for detected scene cuts. A detector proposes boundaries; this
module decides which of them are *real* cuts and which are artifacts (camera
flashes, lightning, strobes, fast motion) that should be merged away.

Uses DINOv3 only: CLS cosine for global continuity, dense patch matching for
spatial layout, and temporal novelty at the boundary. Flash detection (luma/V)
and optional SSCD remain as photometric-invariant cues.

Structure:

    ValidatorConfig   every tunable constant, in one place
    CutValidator      feature extraction (video I/O, DINOv3 + optional SSCD)
    decide()          pure decision cascade: features -> (keep, reason)

`decide()` deliberately touches no video, no model and no global state, so it
can be unit-tested and benchmarked against labelled data without a GPU.

From enoky's SceneCutGUI (MIT), unchanged but for where the checkpoints live:
the DINOv3 folder is resolved from the repo root as weights/SceneCut/DINOv3
rather than from the working directory, so validation works the same whether
the tool was launched standalone or from inside Studio.
"""

from __future__ import annotations

import logging
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModel

logger = logging.getLogger("CutValidator")

#: Where the validation checkpoints live: the DINOv3 model folder, with
#: sscd_disc_large.torchscript.pt beside it inside the same folder. Resolved
#: from this file rather than the working directory.
DEFAULT_DINOV3_DIR = str(
    Path(__file__).resolve().parent.parent.parent / "weights" / "SceneCut" / "DINOv3"
)

#: Name of the optional SSCD descriptor inside the checkpoint bundle.
SSCD_FILENAME = "sscd_disc_large.torchscript.pt"


def _find_sscd(model_dir: str) -> str:
    """Where SSCD is, given where DINOv3 is.

    Upstream looked only inside the DINOv3 folder, but the checkpoint bundle
    ships SSCD as a sibling of it -- it is not a DINOv3 file, it is a second
    model. Checking the folder first and then its parent accepts either layout,
    so validation does not quietly fall back to DINOv3-only because of where the
    archive happened to put one file. The last candidate is returned when
    neither exists, so the caller still reports a sensible missing path.
    """
    candidates = [
        os.path.join(model_dir, SSCD_FILENAME),
        os.path.join(os.path.dirname(os.path.abspath(model_dir)), SSCD_FILENAME),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]

# ANSI color helpers for console output
KEEP_FG, MERG_FG, BOLD, RESET = "\033[94m", "\033[92m", "\033[1m", "\033[0m"


# ------------------------------------------------------------------ #
# Tunable constants
# ------------------------------------------------------------------ #
@dataclass
class ValidatorConfig:
    """Thresholds and knobs for DINOv3-native cut validation."""

    # --- User-facing knob (GUI "Flash Sensitivity") ---
    # Smaller -> more sensitive -> merges more flash cuts.
    flash_luma_delta: float = 30.0

    # --- General ---
    default_base_threshold: float = 0.88
    max_large_window: int = 24

    # --- Dense patch features ---
    # Mean-pool patch tokens onto this grid before caching (bounds RAM).
    dense_grid: tuple = (7, 7)
    # Blended same-shot score: s = w_cls * s_cls + w_dense * s_dense
    w_cls: float = 0.55
    w_dense: float = 0.45
    # Novelty blends CLS and dense adjacent discontinuity
    w_novelty_cls: float = 0.6
    w_novelty_dense: float = 0.4

    # --- SSCD stable-frame sampling ---
    sscd_k: int = 5
    sscd_min_k: int = 3

    # --- Adjacent-frame discontinuity ---
    adj_strong_cut: float = 0.68
    hard_keep_novelty: float = 0.32       # novelty >= this
    hard_keep_adj_dense: float = 0.72     # s_adj_dense <= this

    # --- Flash detection window (frames relative to cut_frame) ---
    flash_scan: int = 12
    flash_center: int = 4
    flash_base_gap: int = 5
    flash_min_dur: int = 1
    flash_max_dur: int = 8

    # --- Pixel cue (HSV H,S histogram intersection, 0..1) ---
    pixel_hist_strong: float = 0.14

    # --- Flash event scoring ---
    flash_amp_floor: float = 10.0
    flash_amp_scale: float = 0.6
    flash_spike_z: float = 2.8
    flash_drift_floor: float = 12.0
    flash_drift_amp_ratio: float = 0.60
    flash_conf_w_amp: float = 0.52
    flash_conf_w_z: float = 0.15
    flash_conf_w_dur: float = 0.18
    flash_conf_w_return: float = 0.15
    flash_amp_score_span: float = 1.8
    flash_z_score_offset: float = 3.0
    flash_z_score_span: float = 3.5
    flash_off_boundary_penalty: float = 0.5
    flash_is_flash_conf: float = 0.55
    flash_afterglow_frac: float = 0.40
    flash_stable_tol_floor: float = 12.0
    flash_stable_tol_amp_frac: float = 0.35
    flash_stable_tol_relax: float = 1.6

    # --- Adaptive base threshold (Otsu over observed blended scores) ---
    auto_thr_min_samples: int = 10
    auto_thr_flat_std: float = 0.01
    auto_thr_flat_bump: float = 0.01
    auto_thr_flat_lo: float = 0.86
    auto_thr_flat_hi: float = 0.93
    auto_thr_lo: float = 0.80
    auto_thr_hi: float = 0.94
    auto_thr_pct_lo: int = 75
    auto_thr_pct_hi: int = 90

    # --- SSCD required-similarity curve ---
    sscd_req_at_max_sens: float = 0.78
    sscd_req_sens_slope: float = 0.0015
    sscd_req_motion_w: float = 0.05
    sscd_req_floor: float = 0.62
    sscd_req_ceil: float = 0.84
    sscd_sens_lo: float = 15.0
    sscd_sens_hi: float = 80.0

    # --- Window / similarity blending ---
    sim_small_weight: float = 0.65
    sim_large_weight: float = 0.35

    # --- Ambiguous band around base_thr (SSCD tie-break) ---
    ambig_near_thr: float = 0.10
    sscd_cut_margin: float = 0.10

    # --- Flash-path decision ---
    flash_conf_min: float = 0.55
    flash_conf_min_sscd: float = 0.45
    flash_same_shot_off: float = 0.04     # s_blend >= base_thr - this => same shot
    flash_dense_same: float = 0.78
    flash_sscd_same: float = 0.72
    flash_strong_novelty: float = 0.28
    flash_strong_adj: float = 0.70
    flash_strong_adj_dense: float = 0.74

    # --- Hard merge (non-flash continuity) ---
    merge_pixel_min: float = 0.14
    merge_adj_min: float = 0.78           # refuse hard-merge if s_adj too low

    # --- Short-scene guard ---
    guard_flash_conf_min: float = 0.40
    guard_flash_conf_min_sscd: float = 0.35
    guard_flash_like_conf: float = 0.35
    guard_same_shot_off: float = 0.05

    # --- Tiny-flash post-pass ---
    tiny_guard_seconds: float = 0.12
    tiny_ultra_short_seconds: float = 0.04
    tiny_flash_conf: float = 0.35
    tiny_score_flash_w: float = 1.2

    # --- Embedding / decode ---
    batch_size: int = 48
    sscd_input: int = 288
    sscd_frame_cache_max: int = 128


# ------------------------------------------------------------------ #
# Pure helpers
# ------------------------------------------------------------------ #
def sscd_required_sim(motion: float, cfg: ValidatorConfig) -> float:
    """Dynamic SSCD threshold driven by the Flash Sensitivity knob."""
    sens = float(max(cfg.sscd_sens_lo, min(cfg.sscd_sens_hi, cfg.flash_luma_delta)))
    base = cfg.sscd_req_at_max_sens - cfg.sscd_req_sens_slope * (cfg.sscd_sens_hi - sens)
    base = base - cfg.sscd_req_motion_w * float(max(0.0, min(1.0, motion)))
    return float(max(cfg.sscd_req_floor, min(cfg.sscd_req_ceil, base)))


def otsu_threshold(scores, bins: int = 128, fallback: float = 0.88) -> float:
    scores = np.clip(np.asarray(scores).astype(np.float32), 0.0, 1.0)
    hist, bin_edges = np.histogram(scores, bins=bins, range=(0.0, 1.0))
    hist = hist.astype(np.float32)

    weight1 = np.cumsum(hist)
    weight2 = np.cumsum(hist[::-1])[::-1]

    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    mean1 = np.cumsum(hist * bin_centers) / np.maximum(weight1, 1e-6)
    mean2 = (np.cumsum((hist * bin_centers)[::-1]) / np.maximum(weight2[::-1], 1e-6))[::-1]

    between = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2
    if between.size == 0:
        return float(fallback)

    k = int(np.nanargmax(between))
    return float(bin_centers[k])


def auto_threshold(cut_scores, cfg: ValidatorConfig) -> float:
    """Pick a same-shot similarity threshold from the observed score spread."""
    if len(cut_scores) < cfg.auto_thr_min_samples:
        return float(cfg.default_base_threshold)

    s = np.array(cut_scores, dtype=np.float32)
    if np.std(s) < cfg.auto_thr_flat_std:
        return float(np.clip(float(np.mean(s)) + cfg.auto_thr_flat_bump,
                             cfg.auto_thr_flat_lo, cfg.auto_thr_flat_hi))

    t_otsu = otsu_threshold(s, fallback=cfg.default_base_threshold)
    p_lo = float(np.percentile(s, cfg.auto_thr_pct_lo))
    p_hi = float(np.percentile(s, cfg.auto_thr_pct_hi))

    thr = max(p_lo, min(t_otsu, p_hi))
    return float(np.clip(thr, cfg.auto_thr_lo, cfg.auto_thr_hi))


def longest_consecutive_run(idxs) -> list:
    """Return the indices belonging to the longest consecutive run."""
    if not idxs:
        return []
    idxs = sorted(set(int(x) for x in idxs))
    best = [idxs[0]]
    cur = [idxs[0]]
    for x in idxs[1:]:
        if x == cur[-1] + 1:
            cur.append(x)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [x]
    if len(cur) > len(best):
        best = cur
    return best


def mad(values, center) -> float:
    arr = np.asarray(values, dtype=np.float32)
    return float(np.median(np.abs(arr - float(center))))


def median_composite(frames_rgb_list):
    if not frames_rgb_list:
        return None
    stack = np.stack(frames_rgb_list, axis=0).astype(np.float32)
    comp = np.median(stack, axis=0)
    return np.clip(comp, 0, 255).astype(np.uint8)


def hist_intersection(h1, h2) -> float:
    if h1 is None or h2 is None:
        return float("nan")
    return float(np.minimum(h1, h2).sum())


def luma_from_rgb(rgb_uint8) -> float:
    r = rgb_uint8[:, :, 0].astype(np.float32)
    g = rgb_uint8[:, :, 1].astype(np.float32)
    b = rgb_uint8[:, :, 2].astype(np.float32)
    return float((0.2126 * r + 0.7152 * g + 0.0722 * b).mean())


def hsv_hist_and_vhi_from_rgb(rgb_uint8):
    """Return (hist_flat, v_hi_p995) for the pixel cue and flash signal."""
    hsv = cv2.cvtColor(rgb_uint8, cv2.COLOR_RGB2HSV)

    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256]).astype(np.float32)
    s = float(hist.sum())
    if s > 1e-6:
        hist /= s

    v = hsv[:, :, 2].astype(np.float32)
    return hist.reshape(-1), float(np.percentile(v, 99.5))


def blend_score(s_cls: float, s_dense: Optional[float], cfg: ValidatorConfig) -> float:
    """Weighted CLS + dense same-shot score."""
    if s_dense is None:
        return float(s_cls)
    w_sum = float(cfg.w_cls + cfg.w_dense)
    if w_sum <= 1e-9:
        return float(s_cls)
    return float((cfg.w_cls * s_cls + cfg.w_dense * float(s_dense)) / w_sum)


def dense_mean_max_sim(a: torch.Tensor, b: torch.Tensor) -> float:
    """Bidirectional mean-max cosine between two patch sets [P,D] (already L2-normed)."""
    if a is None or b is None or a.numel() == 0 or b.numel() == 0:
        return float("nan")
    a = a.float()
    b = b.float()
    if a.dim() == 1:
        a = a.unsqueeze(0)
    if b.dim() == 1:
        b = b.unsqueeze(0)
    sim = a @ b.T  # [Pa, Pb]
    fwd = float(sim.max(dim=1).values.mean().item())
    bwd = float(sim.max(dim=0).values.mean().item())
    return 0.5 * (fwd + bwd)


# ------------------------------------------------------------------ #
# The decision cascade -- pure
# ------------------------------------------------------------------ #
def decide(feats: dict, base_thr: float, cfg: ValidatorConfig) -> tuple[bool, str]:
    """Decide whether a candidate cut is real.

    Returns (keep, reason). Pure: reads only `feats`, `base_thr` and `cfg`.

    Order:
      1  flash  -> merge on same-shot evidence, else keep on strong discontinuity
      2  hard keep on high novelty + low dense adjacent similarity
      3  hard merge on blended score above threshold (+ pixel / adj guards)
      4  SSCD tie-break in the ambiguous band
      5  default keep
    """
    s_cls = float(feats.get("s_cls", feats.get("s_comb_raw", 0.0)))
    s_dense = feats.get("s_dense")
    s_blend = float(feats.get("s_blend", blend_score(s_cls, s_dense, cfg)))
    s_sscd = feats.get("s_sscd")
    s_adj = feats.get("s_adj")
    s_adj_dense = feats.get("s_adj_dense")
    novelty = float(feats.get("novelty", 0.0))
    motion = float(feats.get("motion", 0.0))
    pixel_sim = feats.get("pixel_sim")

    flash = feats.get("flash") or {}
    is_flash = bool(flash.get("is_flash", False))
    flash_conf = float(flash.get("conf", 0.0) or 0.0)

    # 1) Flash path
    if is_flash:
        min_flash_conf = cfg.flash_conf_min_sscd if s_sscd is not None else cfg.flash_conf_min
        if flash_conf < min_flash_conf:
            return True, "keep_flash_low_conf"

        same_shot = False
        if s_blend >= (float(base_thr) - cfg.flash_same_shot_off):
            same_shot = True
        if s_dense is not None and float(s_dense) >= cfg.flash_dense_same:
            same_shot = True
        if s_sscd is not None and float(s_sscd) >= cfg.flash_sscd_same:
            same_shot = True

        strong_cut = (
            novelty >= cfg.flash_strong_novelty
            and (
                (s_adj is not None and float(s_adj) < cfg.flash_strong_adj)
                or (s_adj_dense is not None and float(s_adj_dense) < cfg.flash_strong_adj_dense)
            )
        )

        if strong_cut and not same_shot:
            return True, "keep_flash_strong_cut"
        if same_shot:
            return False, "merge_flash_default"
        return False, "merge_flash_default"

    # 2) Hard keep: clear temporal + dense discontinuity (may override soft continuity)
    if (
        novelty >= cfg.hard_keep_novelty
        and s_adj_dense is not None
        and float(s_adj_dense) <= cfg.hard_keep_adj_dense
    ):
        return True, "keep_hard_discontinuity"

    # Also keep on strong CLS adjacent cut when dense is unavailable
    if s_adj_dense is None and s_adj is not None and float(s_adj) < cfg.adj_strong_cut:
        return True, "keep_adj_strong_cut"

    # 3) Hard merge: blended continuity above adaptive threshold
    pix_ok = pixel_sim is None or float(pixel_sim) >= cfg.merge_pixel_min
    adj_ok = s_adj is None or float(s_adj) >= cfg.merge_adj_min
    if s_blend >= float(base_thr) and pix_ok and adj_ok:
        return False, "merge_same_shot"

    # 4) SSCD tie-break in the ambiguous band near base_thr
    if s_sscd is not None and abs(s_blend - float(base_thr)) <= cfg.ambig_near_thr:
        req = sscd_required_sim(motion, cfg)
        if float(s_sscd) >= req:
            return False, "merge_same_shot_sscd"
        if float(s_sscd) <= (req - cfg.sscd_cut_margin):
            return True, "keep_cut_sscd"

    # 5) Default: trust the detector
    return True, "default_keep"


# ------------------------------------------------------------------ #
# Feature extraction
# ------------------------------------------------------------------ #
class CutValidator:
    """Embeds frames around candidate cuts with DINOv3 and scores each boundary."""

    def __init__(self, model_dir: str = "",
                 device=None, config: Optional[ValidatorConfig] = None,
                 enable_sscd: bool = True, sscd_model_path: Optional[str] = None,
                 log=None):
        self.cfg = config or ValidatorConfig()
        self._log = log or logger

        # Empty means "wherever this install keeps them".
        model_dir = str(model_dir or DEFAULT_DINOV3_DIR)
        if not os.path.isdir(model_dir):
            raise FileNotFoundError(f"DINOv3 model directory not found: {model_dir}")

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device == "cpu":
            raise RuntimeError("AI Validation requires a CUDA-enabled GPU and PyTorch.")

        self._log.debug("Loading DINOv3 model on device: %s", self.device)
        self.processor = AutoImageProcessor.from_pretrained(model_dir, local_files_only=True)
        self.model = AutoModel.from_pretrained(model_dir, local_files_only=True).to(self.device).eval()
        self._num_register_tokens = int(getattr(self.model.config, "num_register_tokens", 0) or 0)
        self._patch_size = int(getattr(self.model.config, "patch_size", 16) or 16)

        # Caches keyed by frame_idx
        self.cache = {}          # frame_idx -> normalized CLS embedding (device tensor)
        self.patch_cache = {}    # frame_idx -> fp16 CPU patch grid [G*G, D]
        self.luma_cache = {}
        self.vhi_cache = {}
        self.hist_cache = {}

        self.batch_size = int(self.cfg.batch_size)
        self.total_video_frames = 0

        _mean = getattr(self.processor, "image_mean", None) or [0.485, 0.456, 0.406]
        _std = getattr(self.processor, "image_std", None) or [0.229, 0.224, 0.225]
        self._img_mean = torch.tensor(_mean, device=self.device).view(1, 3, 1, 1)
        self._img_std = torch.tensor(_std, device=self.device).view(1, 3, 1, 1)
        self._img_rescale = float(getattr(self.processor, "rescale_factor", 1.0 / 255.0))

        # ---- Optional SSCD (photometric-invariant descriptor) ----
        self.sscd_model = None
        self.sscd_input = int(self.cfg.sscd_input)
        self._sscd_video_path = None
        self._sscd_cap = None
        self._sscd_frame_cache = OrderedDict()
        self._sscd_frame_cache_max = int(self.cfg.sscd_frame_cache_max)
        self._sscd_mean = torch.tensor([0.485, 0.456, 0.406], device=self.device).view(1, 3, 1, 1)
        self._sscd_std = torch.tensor([0.229, 0.224, 0.225], device=self.device).view(1, 3, 1, 1)
        if enable_sscd:
            try:
                if sscd_model_path is None:
                    sscd_model_path = _find_sscd(model_dir)
                self._init_sscd(sscd_model_path)
                self._log.info("SSCD enabled (torchscript: %s).", sscd_model_path)
            except Exception as e:
                self.sscd_model = None
                self._log.warning("SSCD unavailable (continuing with DINOv3-only): %s", e)

    # --- small helpers ---

    def _target_size(self):
        size = getattr(self.processor, "size", None) or {}
        if isinstance(size, dict):
            h = int(size.get("height") or size.get("shortest_edge") or 224)
            w = int(size.get("width") or size.get("shortest_edge") or 224)
        else:
            h = w = 224
        return (w, h)  # OpenCV uses (width, height)

    def _flash_signal(self, frame_idx: int):
        """Single scalar flash signal: max(mean_luma, V_p99.5)."""
        l = self.luma_cache.get(frame_idx)
        v = self.vhi_cache.get(frame_idx)
        if l is None and v is None:
            return None
        if l is None:
            return float(v)
        if v is None:
            return float(l)
        return float(max(float(l), float(v)))

    def _pool_patches(self, patch_tokens: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
        """Mean-pool [B, P, D] patch tokens onto dense_grid; return [B, G*G, D] L2-normed."""
        b, p, d = patch_tokens.shape
        grid_h, grid_w = int(self.cfg.dense_grid[0]), int(self.cfg.dense_grid[1])
        # Reconstruct spatial map when possible; otherwise adaptive avg-pool on a square.
        side = int(round(math.sqrt(p)))
        if side * side == p:
            spatial = patch_tokens.transpose(1, 2).reshape(b, d, side, side)
        elif gh > 0 and gw > 0 and gh * gw == p:
            spatial = patch_tokens.transpose(1, 2).reshape(b, d, gh, gw)
        else:
            # Fallback: treat as 1-D and interpolate via adaptive pool on a square pad
            side = max(1, int(math.ceil(math.sqrt(p))))
            pad_n = side * side - p
            if pad_n > 0:
                patch_tokens = torch.cat(
                    [patch_tokens, patch_tokens.new_zeros(b, pad_n, d)], dim=1)
            spatial = patch_tokens.transpose(1, 2).reshape(b, d, side, side)

        pooled = torch.nn.functional.adaptive_avg_pool2d(spatial, (grid_h, grid_w))
        flat = pooled.flatten(2).transpose(1, 2).contiguous()  # [B, G*G, D]
        return torch.nn.functional.normalize(flat, dim=-1)

    def _embed_batch(self, pixel_values):
        """Return (cls [B,D], patches [B,G*G,D]) both L2-normalized on device."""
        with torch.inference_mode():
            outputs = self.model(pixel_values=pixel_values)
            hs = outputs.last_hidden_state  # [B, 1+R+P, D]
            cls = hs[:, 0]
            pooled = getattr(outputs, "pooler_output", None)
            if pooled is not None:
                cls = pooled
            cls = torch.nn.functional.normalize(cls, dim=-1)

            patches = hs[:, 1 + self._num_register_tokens :, :]
            _, _, h, w = pixel_values.shape
            gh = max(1, h // self._patch_size)
            gw = max(1, w // self._patch_size)
            dense = self._pool_patches(patches, gh, gw)
            return cls, dense

    # --- SSCD helpers ---

    def _init_sscd(self, model_path: str):
        p = str(model_path or "")
        if not p:
            raise ValueError("SSCD model_path is empty.")
        if not os.path.isabs(p):
            p = os.path.normpath(p)
        if not os.path.exists(p):
            alt = os.path.join("./weights", os.path.basename(p))
            if os.path.exists(alt):
                p = alt
            else:
                raise FileNotFoundError(f"SSCD TorchScript file not found: {model_path}")

        self._log.info("Loading SSCD TorchScript model: %s", p)
        m = torch.jit.load(p, map_location=self.device)
        try:
            m = m.to(self.device)
        except Exception:
            pass
        m.eval()
        self.sscd_model = m

    def _sscd_get_cap(self, video_path: str):
        if (self._sscd_cap is None) or (self._sscd_video_path != video_path):
            try:
                if self._sscd_cap is not None:
                    self._sscd_cap.release()
            except Exception:
                pass
            self._sscd_video_path = video_path
            self._sscd_cap = cv2.VideoCapture(video_path)
            if not self._sscd_cap.isOpened():
                raise RuntimeError(f"Could not open video for SSCD decode: {video_path}")
        return self._sscd_cap

    def _sscd_read_frame_rgb(self, video_path: str, frame_idx: int):
        if frame_idx in self._sscd_frame_cache:
            rgb = self._sscd_frame_cache.pop(frame_idx)
            self._sscd_frame_cache[frame_idx] = rgb
            return rgb

        cap = self._sscd_get_cap(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        frame = cv2.resize(frame, (self.sscd_input, self.sscd_input), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        self._sscd_frame_cache[frame_idx] = rgb
        if len(self._sscd_frame_cache) > self._sscd_frame_cache_max:
            self._sscd_frame_cache.popitem(last=False)
        return rgb

    def _sscd_embed_rgb(self, rgb_uint8):
        if rgb_uint8 is None:
            return None
        x = torch.from_numpy(rgb_uint8).to(self.device).float() / 255.0
        x = x.permute(2, 0, 1).unsqueeze(0).contiguous()
        x = (x - self._sscd_mean) / self._sscd_std
        with torch.inference_mode():
            out = self.sscd_model(x)
        if isinstance(out, (tuple, list)):
            out = out[0]
        if isinstance(out, dict):
            out = out.get("embeddings") or out.get("embedding") or next(iter(out.values()))
        if not torch.is_tensor(out):
            return None
        if out.dim() == 2:
            out = out[0]
        return torch.nn.functional.normalize(out.flatten(), dim=0)

    def _select_stable_indices_near_cut(self, cut_frame: int, total_frames: int, side: str, flash_event: dict):
        cfg = self.cfg
        spike = set(int(x) for x in (flash_event.get("spike_frames") or []))
        baseline = flash_event.get("baseline")
        amp = float(flash_event.get("amp", 0.0) or 0.0)

        if side == "pre":
            cands = [cut_frame - k for k in range(1, 1 + 12)]
        else:
            cands = [cut_frame + k for k in range(0, 12)]
        cands = [int(max(0, min(total_frames - 1, i))) for i in cands]

        if baseline is None:
            chosen = []
            for i in cands:
                if i in spike:
                    continue
                chosen.append(i)
                if len(chosen) >= cfg.sscd_k:
                    break
            return chosen

        def gather(tol):
            out = []
            for i in cands:
                if i in spike:
                    continue
                v = self._flash_signal(i)
                if v is None:
                    continue
                if abs(float(v) - float(baseline)) <= tol:
                    out.append(i)
                    if len(out) >= cfg.sscd_k:
                        break
            return out

        tol = float(max(cfg.flash_stable_tol_floor, cfg.flash_stable_tol_amp_frac * abs(amp)))
        chosen = gather(tol)
        if len(chosen) < cfg.sscd_min_k:
            chosen = gather(tol * cfg.flash_stable_tol_relax)
        return chosen

    def _sscd_stable_similarity(self, video_path: str, cut_frame: int, total_frames: int, flash_event: dict):
        if (self.sscd_model is None) or (not video_path):
            return None

        pre_ids = self._select_stable_indices_near_cut(cut_frame, total_frames, "pre", flash_event)
        post_ids = self._select_stable_indices_near_cut(cut_frame, total_frames, "post", flash_event)
        if len(pre_ids) < self.cfg.sscd_min_k or len(post_ids) < self.cfg.sscd_min_k:
            return None

        pre_frames = [f for f in (self._sscd_read_frame_rgb(video_path, i) for i in pre_ids) if f is not None]
        post_frames = [f for f in (self._sscd_read_frame_rgb(video_path, i) for i in post_ids) if f is not None]
        if len(pre_frames) < self.cfg.sscd_min_k or len(post_frames) < self.cfg.sscd_min_k:
            return None

        e1 = self._sscd_embed_rgb(median_composite(pre_frames))
        e2 = self._sscd_embed_rgb(median_composite(post_frames))
        if e1 is None or e2 is None:
            return None
        sim = float((e1 * e2).sum().item())
        return None if math.isnan(sim) else sim

    # --- Embedding ---

    def embed_frames(self, video_path: str, indices_to_embed, abort_flag=None, progress_cb=None):
        indices = sorted(set(int(i) for i in indices_to_embed))
        if not indices:
            return

        self._log.info("Embedding %d unique frames via seek-based decode...", len(indices))

        target_size = self._target_size()
        batch_imgs, batch_ids = [], []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video for embedding: {video_path}")

        runs = []
        run_start = indices[0]
        current_run = [indices[0]]
        for i in range(1, len(indices)):
            if indices[i] == current_run[-1] + 1:
                current_run.append(indices[i])
            else:
                runs.append((run_start, current_run))
                run_start = indices[i]
                current_run = [indices[i]]
        runs.append((run_start, current_run))

        total_to_decode = len(indices)
        total_skipped = (indices[-1] - indices[0] + 1) - total_to_decode if len(indices) > 1 else 0
        self._log.info(
            "Seek-based decode: %d runs covering %d frames (skipping ~%d intermediate frames)",
            len(runs), total_to_decode, total_skipped,
        )

        def flush_batch():
            nonlocal batch_imgs, batch_ids
            if not batch_imgs:
                return
            stacked = np.stack(batch_imgs, axis=0)
            t = torch.from_numpy(stacked).to(self.device, non_blocking=True)
            t = t.permute(0, 3, 1, 2).float() * self._img_rescale
            t = (t - self._img_mean) / self._img_std
            cls_emb, dense_emb = self._embed_batch(t)
            for j, fid in enumerate(batch_ids):
                self.cache[fid] = cls_emb[j]
                # Store dense patches as fp16 on CPU to bound RAM
                self.patch_cache[fid] = dense_emb[j].detach().to("cpu", dtype=torch.float16)
            batch_imgs, batch_ids = [], []

        wanted_i = 0
        for run_start_frame, run_frames in runs:
            if abort_flag is not None and abort_flag.is_set():
                raise InterruptedError

            cap.set(cv2.CAP_PROP_POS_FRAMES, run_start_frame)

            for expected_frame in run_frames:
                if abort_flag is not None and abort_flag.is_set():
                    raise InterruptedError

                ret, frame = cap.read()
                if not ret or frame is None:
                    self._log.debug("Failed to read frame %d, skipping.", expected_frame)
                    continue

                frame = cv2.resize(frame, target_size)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                hist, v_hi = hsv_hist_and_vhi_from_rgb(rgb)
                self.luma_cache[expected_frame] = luma_from_rgb(rgb)
                self.vhi_cache[expected_frame] = v_hi
                self.hist_cache[expected_frame] = hist

                batch_imgs.append(rgb)
                batch_ids.append(expected_frame)
                wanted_i += 1

                if len(batch_imgs) >= self.batch_size:
                    flush_batch()

                if progress_cb and total_to_decode > 0 and wanted_i % 25 == 0:
                    progress_cb(min(1.0, wanted_i / total_to_decode),
                                f"Embedding frames ({wanted_i}/{total_to_decode})")

        flush_batch()
        cap.release()
        self._log.info("Frame embedding complete (%d CLS, %d dense cached).",
                       len(self.cache), len(self.patch_cache))

    # --- Similarity helpers ---

    def _get_patches(self, frame_idx: int) -> Optional[torch.Tensor]:
        p = self.patch_cache.get(frame_idx)
        if p is None:
            return None
        return p.float()

    def _pairwise_median_similarity(self, pre_vecs, post_vecs) -> float:
        pre = torch.stack(pre_vecs, dim=0)
        post = torch.stack(post_vecs, dim=0)
        sim = pre @ post.T
        return float(sim.flatten().median().item())

    def _pairwise_dense_similarity(self, pre_ids, post_ids) -> Optional[float]:
        """Median of bidirectional mean-max dense sims across pre×post frame pairs."""
        scores = []
        for i in pre_ids:
            a = self._get_patches(i)
            if a is None:
                continue
            for j in post_ids:
                b = self._get_patches(j)
                if b is None:
                    continue
                s = dense_mean_max_sim(a, b)
                if not math.isnan(s):
                    scores.append(s)
        if not scores:
            return None
        return float(np.median(np.asarray(scores, dtype=np.float32)))

    def _avg_adjacent_similarity(self, vecs) -> float:
        if len(vecs) < 2:
            return 1.0
        mat = torch.stack(vecs, dim=0)
        sims = (mat[:-1] * mat[1:]).sum(dim=1)
        return float(sims.mean().item())

    def _window_indices(self, cut_frame: int, window: int, gap: int, total_frames: int):
        pre_end = max(0, cut_frame - gap)
        pre_start = max(0, pre_end - window)
        post_start = min(total_frames, cut_frame + gap)
        post_end = min(total_frames, post_start + window)
        return list(range(pre_start, pre_end)), list(range(post_start, post_end))

    def window_geometry(self, window: int):
        gap = max(2, int(max(3, window // 2)))
        w_small = max(2, int(window))
        w_large = int(min(max(w_small * 3, w_small + 2), self.cfg.max_large_window))
        return gap, w_small, w_large

    # --- Flash detection ---

    def _detect_flash_event(self, cut_frame: int, total_frames: int) -> dict:
        cfg = self.cfg

        def clamp(i: int) -> int:
            return int(max(0, min(total_frames - 1, i)))

        def empty(drift=None, bpre=None, bpost=None, base=None):
            return {
                "is_flash": False, "conf": 0.0, "kind": None, "amp": 0.0, "dur": 0,
                "drift": drift, "baseline_pre": bpre, "baseline_post": bpost,
                "baseline": base, "spike_frames": [],
            }

        center_ids = [clamp(cut_frame + k) for k in range(-cfg.flash_center, cfg.flash_center + 1)]
        pre_base_ids = [clamp(cut_frame + k) for k in range(-cfg.flash_scan, -cfg.flash_base_gap)]
        post_base_ids = [clamp(cut_frame + k) for k in range(cfg.flash_base_gap, cfg.flash_scan + 1)]

        base_vals_pre = [v for v in (self._flash_signal(i) for i in pre_base_ids) if v is not None]
        base_vals_post = [v for v in (self._flash_signal(i) for i in post_base_ids) if v is not None]

        if len(base_vals_pre) + len(base_vals_post) < 6:
            return empty()

        baseline_pre = float(np.median(base_vals_pre)) if base_vals_pre else float(np.median(base_vals_post))
        baseline_post = float(np.median(base_vals_post)) if base_vals_post else float(np.median(base_vals_pre))
        baseline = float(np.median([baseline_pre, baseline_post]))
        drift = float(abs(baseline_pre - baseline_post))

        scale = float(max(1.0, 1.4826 * mad(base_vals_pre + base_vals_post, baseline)))

        center_vals = [(i, v) for (i, v) in ((i, self._flash_signal(i)) for i in center_ids) if v is not None]
        if not center_vals:
            return empty(drift, baseline_pre, baseline_post, baseline)

        pos = [(i, float(v) - baseline) for (i, v) in center_vals]
        neg = [(i, baseline - float(v)) for (i, v) in center_vals]
        kind = "bright" if max(d for _, d in pos) >= max(d for _, d in neg) else "dark"
        deltas = pos if kind == "bright" else neg
        amp = float(max(d for _, d in deltas))

        amp_thr = float(max(cfg.flash_amp_floor, cfg.flash_amp_scale * cfg.flash_luma_delta))
        z = float(amp / scale)

        spike = [i for (i, d) in deltas if d >= amp_thr and (d / scale) >= cfg.flash_spike_z]
        spike_run = longest_consecutive_run(spike)
        dur = int(len(spike_run))
        boundary_hit = any(abs(i - cut_frame) <= 1 for i in spike_run)

        return_ok = bool(drift <= max(cfg.flash_drift_floor, cfg.flash_drift_amp_ratio * amp)) if amp > 1e-3 else False

        amp_score = float(np.clip((amp - amp_thr) / max(1e-6, cfg.flash_amp_score_span * amp_thr), 0.0, 1.0))
        z_score = float(np.clip((z - cfg.flash_z_score_offset) / cfg.flash_z_score_span, 0.0, 1.0))

        if dur <= 0:
            dur_score = 0.0
        elif dur <= 3:
            dur_score = 1.0
        elif dur <= 6:
            dur_score = 0.85
        elif dur <= cfg.flash_max_dur:
            dur_score = 0.70
        else:
            dur_score = 0.0

        if amp <= 1e-3:
            return_score = 0.0
        else:
            ratio = drift / amp
            if ratio <= 0.30:
                return_score = 1.0
            elif ratio <= 0.60:
                return_score = 0.7
            elif ratio <= 1.00:
                return_score = 0.25
            else:
                return_score = 0.0

        conf = float(np.clip(
            cfg.flash_conf_w_amp * amp_score + cfg.flash_conf_w_z * z_score
            + cfg.flash_conf_w_dur * dur_score + cfg.flash_conf_w_return * return_score,
            0.0, 1.0))
        if not boundary_hit:
            conf *= cfg.flash_off_boundary_penalty

        is_flash = bool(conf >= cfg.flash_is_flash_conf and boundary_hit
                        and (cfg.flash_min_dur <= dur <= cfg.flash_max_dur))

        spike_frames = sorted(set(clamp(i + k) for i in spike_run for k in (-1, 0, 1)))

        return {
            "is_flash": is_flash, "conf": conf, "kind": kind if is_flash else None,
            "amp": amp, "dur": dur, "drift": drift,
            "baseline_pre": baseline_pre, "baseline_post": baseline_post, "baseline": baseline,
            "spike_frames": spike_frames, "z": z, "amp_thr": amp_thr,
            "boundary_hit": boundary_hit, "return_ok": return_ok,
        }

    def _stable_indices(self, indices, flash_event):
        if not indices:
            return []
        if not flash_event or not flash_event.get("is_flash", False):
            return list(indices)

        baseline = flash_event.get("baseline")
        amp = float(flash_event.get("amp") or 0.0)
        kind = flash_event.get("kind")
        spike_frames = set(int(i) for i in flash_event.get("spike_frames") or [])
        frac = self.cfg.flash_afterglow_frac

        kept = []
        for i in indices:
            if i in spike_frames:
                continue
            sig = self._flash_signal(i)
            if sig is None or baseline is None or amp <= 1e-6:
                kept.append(i)
                continue
            if kind == "bright" and float(sig) > float(baseline) + frac * amp:
                continue
            if kind == "dark" and float(sig) < float(baseline) - frac * amp:
                continue
            kept.append(i)
        return kept

    def _flash_side_decision(self, cut_frame: int, total_frames: int, flash_event: dict):
        if not flash_event or not flash_event.get("is_flash", False):
            return None
        spike = [int(i) for i in (flash_event.get("spike_frames") or [])]
        if not spike:
            return None

        pre_count = sum(1 for i in spike if i < cut_frame)
        post_count = sum(1 for i in spike if i >= cut_frame)
        if pre_count > post_count:
            return "pre"
        if post_count > pre_count:
            return "post"

        flash_vecs = [self.cache[i] for i in spike if i in self.cache]
        if not flash_vecs:
            return None

        pre_ids = self._select_stable_indices_near_cut(cut_frame, total_frames, "pre", flash_event)
        post_ids = self._select_stable_indices_near_cut(cut_frame, total_frames, "post", flash_event)
        pre_vecs = [self.cache[i] for i in pre_ids if i in self.cache]
        post_vecs = [self.cache[i] for i in post_ids if i in self.cache]

        if not pre_vecs and not post_vecs:
            return None
        if pre_vecs and not post_vecs:
            return "pre"
        if post_vecs and not pre_vecs:
            return "post"

        pre_sim = self._pairwise_median_similarity(flash_vecs, pre_vecs)
        post_sim = self._pairwise_median_similarity(flash_vecs, post_vecs)
        return "pre" if pre_sim >= post_sim else "post"

    def adjust_cut_for_flash(self, cut_frame: int, total_frames: int, flash_event: dict,
                             cur_start_f: int, nxt_end_f: int) -> int:
        side = self._flash_side_decision(cut_frame, total_frames, flash_event)
        if side is None:
            return int(cut_frame)
        spike = [int(i) for i in (flash_event.get("spike_frames") or [])]
        if not spike:
            return int(cut_frame)

        new_cut = int(max(spike) + 1) if side == "pre" else int(min(spike))

        min_cut = int(cur_start_f) + 1
        max_cut = int(nxt_end_f) - 1
        if min_cut > max_cut:
            return int(cut_frame)
        return int(max(min_cut, min(max_cut, new_cut)))

    # --- Feature extraction ---

    def extract_features(self, cut_frame: int, window: int, total_frames: int,
                         video_path: str = None, base_thr: float = None) -> Optional[dict]:
        cfg = self.cfg
        gap, w_small, w_large = self.window_geometry(window)

        pre_s, post_s = self._window_indices(cut_frame, w_small, gap, total_frames)
        pre_l, post_l = self._window_indices(cut_frame, w_large, gap, total_frames)

        def vecs_for(indices):
            return [self.cache[i] for i in indices if i in self.cache]

        pre_vecs_s, post_vecs_s = vecs_for(pre_s), vecs_for(post_s)
        pre_vecs_l, post_vecs_l = vecs_for(pre_l), vecs_for(post_l)

        if len(pre_vecs_s) < 2 or len(post_vecs_s) < 2:
            return None

        s_small = self._pairwise_median_similarity(pre_vecs_s, post_vecs_s)
        s_large = None
        if len(pre_vecs_l) >= 2 and len(post_vecs_l) >= 2:
            s_large = self._pairwise_median_similarity(pre_vecs_l, post_vecs_l)

        s_cls = float(s_small) if s_large is None else float(
            cfg.sim_small_weight * s_small + cfg.sim_large_weight * s_large)

        s_dense_small = self._pairwise_dense_similarity(
            [i for i in pre_s if i in self.patch_cache],
            [i for i in post_s if i in self.patch_cache],
        )
        s_dense_large = None
        if len(pre_l) >= 2 and len(post_l) >= 2:
            s_dense_large = self._pairwise_dense_similarity(
                [i for i in pre_l if i in self.patch_cache],
                [i for i in post_l if i in self.patch_cache],
            )
        if s_dense_small is None:
            s_dense = s_dense_large
        elif s_dense_large is None:
            s_dense = s_dense_small
        else:
            s_dense = float(
                cfg.sim_small_weight * s_dense_small + cfg.sim_large_weight * s_dense_large)

        pre_adj, post_adj = cut_frame - 1, cut_frame
        s_adj = None
        s_adj_dense = None
        if pre_adj in self.cache and post_adj in self.cache:
            s_adj = float((self.cache[pre_adj] * self.cache[post_adj]).sum().item())
        if pre_adj in self.patch_cache and post_adj in self.patch_cache:
            s_adj_dense = dense_mean_max_sim(
                self._get_patches(pre_adj), self._get_patches(post_adj))
            if s_adj_dense == s_adj_dense:  # not NaN
                s_adj_dense = float(s_adj_dense)
            else:
                s_adj_dense = None

        # Temporal novelty at the boundary
        nov_cls = (1.0 - float(s_adj)) if s_adj is not None else 0.0
        nov_dense = (1.0 - float(s_adj_dense)) if s_adj_dense is not None else nov_cls
        if s_adj_dense is None:
            novelty = float(nov_cls)
        else:
            novelty = float(
                cfg.w_novelty_cls * nov_cls + cfg.w_novelty_dense * nov_dense)

        pre_cont = self._avg_adjacent_similarity(pre_vecs_s)
        post_cont = self._avg_adjacent_similarity(post_vecs_s)
        cont = float(min(pre_cont, post_cont))
        motion = float(max(0.0, 1.0 - cont))

        flash_event = self._detect_flash_event(cut_frame, total_frames)

        # Flash-excluded ("stable") CLS / dense similarities
        s_cls_stable = s_cls
        s_dense_stable = s_dense
        used_stable = False
        if flash_event.get("is_flash", False):
            used_stable = True
            pre_s_st = self._stable_indices(pre_s, flash_event)
            post_s_st = self._stable_indices(post_s, flash_event)
            pre_vecs_st = vecs_for(pre_s_st)
            post_vecs_st = vecs_for(post_s_st)
            if len(pre_vecs_st) >= 2 and len(post_vecs_st) >= 2:
                s_small_st = self._pairwise_median_similarity(pre_vecs_st, post_vecs_st)
                pre_l_st = self._stable_indices(pre_l, flash_event)
                post_l_st = self._stable_indices(post_l, flash_event)
                pre_vecs_l_st = vecs_for(pre_l_st)
                post_vecs_l_st = vecs_for(post_l_st)
                if len(pre_vecs_l_st) >= 2 and len(post_vecs_l_st) >= 2:
                    s_large_st = self._pairwise_median_similarity(pre_vecs_l_st, post_vecs_l_st)
                    s_cls_stable = float(
                        cfg.sim_small_weight * s_small_st + cfg.sim_large_weight * s_large_st)
                else:
                    s_cls_stable = float(s_small_st)

                s_dense_stable = self._pairwise_dense_similarity(
                    [i for i in pre_s_st if i in self.patch_cache],
                    [i for i in post_s_st if i in self.patch_cache],
                )

        s_blend = blend_score(s_cls_stable if used_stable else s_cls,
                              s_dense_stable if used_stable else s_dense, cfg)

        s_sscd = None
        if (video_path is not None) and (self.sscd_model is not None) and flash_event.get("is_flash", False):
            try:
                s_sscd = self._sscd_stable_similarity(video_path, cut_frame, total_frames, flash_event)
            except Exception as e:
                self._log.debug("SSCD similarity failed at cut@%d: %s", cut_frame, e)
                s_sscd = None

        def get_hist(cands):
            for i in cands:
                if 0 <= i < total_frames and i in self.hist_cache:
                    return self.hist_cache[i]
            return None

        pre_hist = get_hist([cut_frame - 4, cut_frame - 5, cut_frame - 3, cut_frame - 6])
        post_hist = get_hist([cut_frame + 4, cut_frame + 5, cut_frame + 3, cut_frame + 6])
        pixel_sim = hist_intersection(pre_hist, post_hist)
        if pixel_sim != pixel_sim:
            pixel_sim = None

        feats = {
            "cut_frame": int(cut_frame),
            "s_cls": float(s_cls),
            "s_dense": None if s_dense is None else float(s_dense),
            "s_cls_stable": float(s_cls_stable),
            "s_dense_stable": None if s_dense_stable is None else float(s_dense_stable),
            "s_blend": float(s_blend),
            # Aliases used by guards / logging that historically expected these names
            "s_comb_raw": float(s_cls),
            "s_comb_stable": float(s_cls_stable),
            "s_sscd": None if s_sscd is None else float(s_sscd),
            "s_adj": None if s_adj is None else float(s_adj),
            "s_adj_dense": None if s_adj_dense is None else float(s_adj_dense),
            "novelty": float(novelty),
            "pre_cont": float(pre_cont),
            "post_cont": float(post_cont),
            "cont": float(cont),
            "motion": float(motion),
            "pixel_sim": None if pixel_sim is None else float(pixel_sim),
            "used_stable": bool(used_stable),
            "flash": flash_event,
            "gap": int(gap),
            "w_small": int(w_small),
            "w_large": int(w_large),
        }

        if base_thr is not None:
            self._maybe_fill_sscd(feats, float(base_thr), video_path, total_frames)
        return feats

    def _maybe_fill_sscd(self, feats: dict, base_thr: float, video_path, total_frames: int):
        """Lazily compute SSCD when flash or ambiguous band needs it."""
        if feats.get("s_sscd") is not None:
            return
        if video_path is None or self.sscd_model is None:
            return

        is_flash = bool((feats.get("flash") or {}).get("is_flash", False))
        s_blend = float(feats.get("s_blend", 0.0))
        near = abs(s_blend - base_thr) <= self.cfg.ambig_near_thr
        if not (is_flash or near):
            return

        try:
            s = self._sscd_stable_similarity(
                video_path, int(feats["cut_frame"]), total_frames, feats.get("flash") or {})
        except Exception as e:
            self._log.debug("SSCD similarity failed at cut@%d: %s", feats["cut_frame"], e)
            s = None
        feats["s_sscd"] = None if s is None else float(s)

    def validate_cut(self, scene_index: int, cut_frame: int, window: int, total_frames: int,
                     base_thr: float, video_path: str = None, return_feats: bool = False):
        """True if this is a real cut (KEEP), False if it should be merged."""
        feats = self.extract_features(cut_frame, window, total_frames,
                                      video_path=video_path, base_thr=base_thr)
        if feats is None:
            return (True, None) if return_feats else True

        decision_keep, reason = decide(feats, float(base_thr), self.cfg)
        feats["decision_keep"] = bool(decision_keep)
        feats["reason"] = reason

        flash = feats.get("flash") or {}
        s_sscd, s_adj, pixel_sim = feats.get("s_sscd"), feats.get("s_adj"), feats.get("pixel_sim")
        drift = flash.get("drift")
        self._log.debug(
            "Scene #%d cut@%d | s_blend=%.3f s_cls=%.3f s_dense=%s s_sscd=%s s_adj=%s "
            "s_adj_d=%s nov=%.3f cont=%.3f pixel=%s flash=%s(%.2f) amp=%.1f dur=%d drift=%s "
            "-> %s (%s)",
            scene_index, cut_frame, feats["s_blend"], feats["s_cls"],
            "%.3f" % feats["s_dense"] if feats.get("s_dense") is not None else "n/a",
            "%.3f" % s_sscd if s_sscd is not None else "n/a",
            "%.3f" % s_adj if s_adj is not None else "n/a",
            "%.3f" % feats["s_adj_dense"] if feats.get("s_adj_dense") is not None else "n/a",
            feats["novelty"], feats["cont"],
            "%.3f" % pixel_sim if pixel_sim is not None else "n/a",
            "Y" if flash.get("is_flash") else "N", float(flash.get("conf", 0.0) or 0.0),
            float(flash.get("amp", 0.0) or 0.0), int(flash.get("dur", 0) or 0),
            "%.1f" % float(drift) if drift is not None else "n/a",
            f"{BOLD}{KEEP_FG}KEEP{RESET}" if decision_keep else f"{BOLD}{MERG_FG}MERGE{RESET}",
            reason,
        )
        return (decision_keep, feats) if return_feats else decision_keep

    # --- Top level ---

    def filter_scenes(self, video_path: str, scenes: list, window: int, short_guard_frames: int,
                      total_frames: int = 0, abort_flag=None, progress_cb=None) -> list:
        """Validate every boundary in `scenes`, returning the surviving scenes."""
        cfg = self.cfg
        if len(scenes) < 2:
            return scenes

        self.total_video_frames = int(total_frames or 0)
        if self.total_video_frames <= 0:
            cap = cv2.VideoCapture(video_path)
            self.total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
        if self.total_video_frames <= 0:
            self._log.warning("Could not determine frame count for AI validation; skipping.")
            return scenes

        cut_frames = [int(end_tc.get_frames()) for _, end_tc in scenes[:-1]]
        w = int(window)
        gap, w_small, w_large = self.window_geometry(w)

        all_indices = set()
        for cut in cut_frames:
            for k in range(-cfg.flash_scan, cfg.flash_scan + 1):
                all_indices.add(max(0, min(self.total_video_frames - 1, cut + k)))
            pre_s, post_s = self._window_indices(cut, w_small, gap, self.total_video_frames)
            all_indices.update(pre_s); all_indices.update(post_s)
            pre_l, post_l = self._window_indices(cut, w_large, gap, self.total_video_frames)
            all_indices.update(pre_l); all_indices.update(post_l)

        self.embed_frames(video_path, sorted(all_indices), abort_flag=abort_flag, progress_cb=progress_cb)

        # Pass 1: collect blended scores for adaptive threshold.
        cut_scores = []
        for i, cut in enumerate(cut_frames, start=1):
            feats = self.extract_features(cut, w, self.total_video_frames)
            if feats is None:
                continue
            cut_scores.append(float(feats["s_blend"]))
            if progress_cb and i % 10 == 0:
                progress_cb(min(1.0, i / max(1, len(cut_frames))),
                            f"Analyzing cut scores ({i}/{len(cut_frames)})")

        base_thr = auto_threshold(cut_scores, cfg)
        self._log.info(
            "AI validation adaptive same-shot threshold: base_thr=%.3f (window=%d, gap=%d, large=%d)",
            base_thr, w, gap, w_large,
        )

        # Pass 2: decide each boundary.
        validated_scenes = []
        decisions = []
        cur_start, cur_end = scenes[0]

        for i in range(1, len(scenes)):
            nxt_start, nxt_end = scenes[i]
            cut_frame_num = int(nxt_start.get_frames())
            cur_len = int(cur_end.get_frames() - cur_start.get_frames())
            nxt_len = int(nxt_end.get_frames() - nxt_start.get_frames())

            if abort_flag is not None and abort_flag.is_set():
                raise InterruptedError

            keep_cut, feats = self.validate_cut(
                scene_index=i, cut_frame=cut_frame_num, window=w,
                total_frames=self.total_video_frames, base_thr=base_thr,
                video_path=video_path, return_feats=True,
            )

            flash = (feats or {}).get("flash") or {}
            is_flash = bool(flash.get("is_flash", False))
            flash_conf = float(flash.get("conf", 0.0) or 0.0)
            s_sscd = (feats or {}).get("s_sscd")
            s_stable = float((feats or {}).get("s_blend", (feats or {}).get("s_comb_stable", 0.0)) or 0.0)
            spike_frames = flash.get("spike_frames") or []
            boundary_hit = bool(flash.get("boundary_hit", False))

            min_flash_conf = cfg.guard_flash_conf_min_sscd if s_sscd is not None else cfg.guard_flash_conf_min

            guard_applied = None
            if cur_len <= short_guard_frames or nxt_len <= short_guard_frames:
                flash_like = bool(spike_frames) and boundary_hit and (flash_conf >= cfg.guard_flash_like_conf)
                same_shot = float(s_stable) >= max(0.0, float(base_thr) - cfg.guard_same_shot_off)
                if flash_like and same_shot:
                    keep_cut = False
                    guard_applied = "short_guard_merge_flash"
                elif not (is_flash and flash_conf >= min_flash_conf):
                    keep_cut = True
                    guard_applied = "short_guard_keep"

            if feats is not None:
                rec = {k: v for k, v in feats.items() if k != "flash"}
                rec["flash_is_flash"] = is_flash
                rec["flash_conf"] = flash_conf
                rec["flash_amp"] = float(flash.get("amp", 0.0) or 0.0)
                rec["flash_dur"] = int(flash.get("dur", 0) or 0)
                rec["base_thr"] = float(base_thr)
                rec["guard_applied"] = guard_applied
                rec["final_keep"] = bool(keep_cut)
                decisions.append(rec)

            if keep_cut:
                if is_flash and flash_conf >= min_flash_conf:
                    new_cut = self.adjust_cut_for_flash(
                        cut_frame=cut_frame_num, total_frames=self.total_video_frames,
                        flash_event=flash, cur_start_f=int(cur_start.get_frames()),
                        nxt_end_f=int(nxt_end.get_frames()),
                    )
                    if new_cut != cut_frame_num:
                        tc = cur_start.__class__
                        cur_end = tc(int(new_cut), cur_start.fps)
                        nxt_start = tc(int(new_cut), cur_start.fps)

                validated_scenes.append((cur_start, cur_end))
                cur_start, cur_end = nxt_start, nxt_end
            else:
                cur_end = nxt_end

            if progress_cb:
                progress_cb(min(1.0, i / max(1, len(scenes) - 1)),
                            f"Validating cuts ({i}/{len(scenes) - 1})")

        validated_scenes.append((cur_start, cur_end))
        validated_scenes = self._merge_tiny_flash_clips(
            validated_scenes, video_path, w, base_thr, short_guard_frames)

        self.last_decisions = decisions
        self.last_base_thr = float(base_thr)
        self._release_sscd()
        return validated_scenes

    def _merge_tiny_flash_clips(self, validated_scenes, video_path, w, base_thr, short_guard_frames):
        cfg = self.cfg
        try:
            if not short_guard_frames or len(validated_scenes) < 3:
                return validated_scenes

            fps_local = float(validated_scenes[0][0].fps) if validated_scenes else 0.0
            frames = [[int(st.get_frames()), int(et.get_frames())] for st, et in validated_scenes]
            boundary_cache = {}

            def boundary_info(cut_frame: int):
                if cut_frame in boundary_cache:
                    return boundary_cache[cut_frame]
                feats_b = self.extract_features(cut_frame, w, self.total_video_frames, video_path=video_path)
                if feats_b is None:
                    info = {"flash_like": False, "score": 0.0}
                else:
                    flash_b = feats_b.get("flash") or {}
                    conf_b = float(flash_b.get("conf", 0.0) or 0.0)
                    spike_b = flash_b.get("spike_frames") or []
                    hit_b = bool(flash_b.get("boundary_hit", False))
                    flash_like_b = bool(spike_b) and hit_b and (conf_b >= cfg.tiny_flash_conf)
                    s_stable_b = float(feats_b.get("s_blend", feats_b.get("s_comb_stable", 0.0)) or 0.0)
                    same_shot_b = s_stable_b >= max(0.0, float(base_thr) - cfg.guard_same_shot_off)
                    score_b = (conf_b * cfg.tiny_score_flash_w) + (s_stable_b - float(base_thr) if same_shot_b else 0.0)
                    info = {"flash_like": flash_like_b, "score": score_b}
                boundary_cache[cut_frame] = info
                return info

            ultra_short = max(1, int(round(fps_local * cfg.tiny_ultra_short_seconds))) if fps_local > 0 else 1
            guard_len = max(2, int(round(fps_local * cfg.tiny_guard_seconds))) if fps_local > 0 else 2

            i = 1
            merged_any = False
            while i < len(frames) - 1:
                seg_len = int(frames[i][1] - frames[i][0])
                if seg_len <= guard_len:
                    info_pre = boundary_info(frames[i][0])
                    info_post = boundary_info(frames[i][1])
                    if (seg_len <= ultra_short) or info_pre["flash_like"] or info_post["flash_like"]:
                        if info_pre["score"] >= info_post["score"]:
                            frames[i - 1][1] = frames[i][1]
                        else:
                            frames[i + 1][0] = frames[i][0]
                        del frames[i]
                        merged_any = True
                        continue
                i += 1

            if merged_any:
                tc = validated_scenes[0][0].__class__
                validated_scenes = [(tc(st, fps_local), tc(et, fps_local)) for st, et in frames if et > st]
        except Exception as e:
            self._log.warning("Tiny flash merge post-pass failed: %s", e)
        return validated_scenes

    def _release_sscd(self):
        try:
            if self._sscd_cap is not None:
                self._sscd_cap.release()
        except Exception:
            pass
        self._sscd_cap = None
        self._sscd_video_path = None
        try:
            if self._sscd_frame_cache is not None:
                self._sscd_frame_cache.clear()
        except Exception:
            pass

    def reset_caches(self):
        """Drop per-video state so one validator can serve multiple runs."""
        self.cache.clear()
        self.patch_cache.clear()
        self.luma_cache.clear()
        self.vhi_cache.clear()
        self.hist_cache.clear()
        self._release_sscd()
