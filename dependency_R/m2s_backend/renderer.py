import cv2
import numpy as np
import torch
import torch.nn.functional as F

from .vendored_m2s.warping import scatter_image
from .gpu import build_m2s_outputs_torch


_DEF_DIFF_THRESHOLD = 0.090
_DEF_EDGE_DELTA_THRESHOLD = 0.075
_DEF_SEAM_SC_EDGE_THRESHOLD = 0.060
_DEF_SEAM_BRIGHTNESS_THRESHOLD = 0.035
_DEF_SEAM_VAR_THRESHOLD = 0.0010
_DEF_MAX_COMPONENT_AREA = 1024
_DEF_MAX_SEAM_COMPONENT_AREA = 1536


def _clamp_fix_strength(fix_strength: float | None) -> float:
    try:
        value = float(fix_strength if fix_strength is not None else 1.0)
    except Exception:
        value = 1.0
    return max(0.50, min(2.00, value))


def _validate_inputs(left_image_tensor: torch.Tensor | None, depth_tensor: torch.Tensor):
    if depth_tensor is None:
        raise ValueError("depth_tensor is required")
    if depth_tensor.ndim != 4 or int(depth_tensor.shape[1]) != 1:
        raise ValueError(f"depth_tensor must have shape (B, 1, H, W); got {tuple(depth_tensor.shape)}")
    if left_image_tensor is not None:
        if left_image_tensor.ndim != 4:
            raise ValueError(f"left_image_tensor must have shape (B, C, H, W); got {tuple(left_image_tensor.shape)}")
        if (
            int(left_image_tensor.shape[0]) != int(depth_tensor.shape[0])
            or int(left_image_tensor.shape[2]) != int(depth_tensor.shape[2])
            or int(left_image_tensor.shape[3]) != int(depth_tensor.shape[3])
        ):
            raise ValueError(
                f"left_image_tensor and depth_tensor spatial dims must match; got {tuple(left_image_tensor.shape)} and {tuple(depth_tensor.shape)}"
            )


def _translate_frame(frame: np.ndarray, shift_px: float, border_value: float = 0.0) -> np.ndarray:
    h, w = frame.shape[:2]
    matrix = np.array([[1.0, 0.0, float(shift_px)], [0.0, 1.0, 0.0]], dtype=np.float32)
    return cv2.warpAffine(
        frame,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def _translate_mask(mask: np.ndarray, shift_px: float) -> np.ndarray:
    h, w = mask.shape[:2]
    matrix = np.array([[1.0, 0.0, float(shift_px)], [0.0, 1.0, 0.0]], dtype=np.float32)
    return cv2.warpAffine(
        mask,
        matrix,
        (w, h),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def _scatter_signed_disparity(
    input_frame: np.ndarray,
    signed_disparity: np.ndarray,
):
    h, w = input_frame.shape[:2]
    disparity_map = signed_disparity.astype(np.float32, copy=False)

    d0 = np.floor(disparity_map).astype(np.int32)
    frac = np.clip(disparity_map - d0.astype(np.float32), 0.0, 1.0)
    weight_second = frac
    weight_first = 1.0 - frac

    x_coords, _ = np.meshgrid(np.arange(w), np.arange(h))
    reproj_x_coords = x_coords - d0
    reproj_x_coords_plus1 = x_coords - (d0 + 1)

    reproj_img = np.zeros_like(input_frame, dtype=np.float32)
    reproj_img_weight = np.zeros_like(input_frame, dtype=np.float32)

    valid_mask = (reproj_x_coords >= 0) & (reproj_x_coords < w)
    valid_y, valid_x = np.where(valid_mask)
    reproj_valid_x_coords = reproj_x_coords[valid_y, valid_x]

    valid_mask1 = (reproj_x_coords_plus1 >= 0) & (reproj_x_coords_plus1 < w)
    valid_y1, valid_x1 = np.where(valid_mask1)
    reproj_valid_x_coords_plus1 = reproj_x_coords_plus1[valid_y1, valid_x1]

    reproj_img[valid_y, reproj_valid_x_coords] += (
        input_frame[valid_y, valid_x] * weight_first[valid_y, valid_x][:, None]
    )
    reproj_img_weight[valid_y, reproj_valid_x_coords] += weight_first[valid_y, valid_x][:, None]

    reproj_img[valid_y1, reproj_valid_x_coords_plus1] += (
        input_frame[valid_y1, valid_x1] * weight_second[valid_y1, valid_x1][:, None]
    )
    reproj_img_weight[valid_y1, reproj_valid_x_coords_plus1] += weight_second[valid_y1, valid_x1][:, None]

    filled_pixel_mask = (reproj_img_weight[..., 0] != 0)
    reproj_nonzero = reproj_img_weight != 0
    reproj_img[reproj_nonzero] /= reproj_img_weight[reproj_nonzero]

    hole_mask = np.zeros((h, w), dtype=np.uint8)
    hole_mask[~filled_pixel_mask] = 255
    return reproj_img.astype(input_frame.dtype, copy=False), hole_mask


def _build_m2s_numpy_outputs_inwarp(
    left_frames: np.ndarray | None,
    depth_frames: np.ndarray,
    convergence_point: float,
    max_disp_pixels: float,
):
    if max_disp_pixels is None:
        raise ValueError("max_disp_pixels is required")

    scale_factor = float(max_disp_pixels) * 2.0
    convergence_point = float(convergence_point)

    warped_frames = []
    hole_masks = []

    for batch_index in range(depth_frames.shape[0]):
        # no ceiling: depth past 1 is Boost / Pop-Out / Particles in front (bug sweep 2026-10-09)
        depth_frame = np.maximum(depth_frames[batch_index, 0], 0.0).astype(np.float32, copy=False)
        signed_disparity = (depth_frame - convergence_point).astype(np.float32, copy=False) * scale_factor

        if left_frames is None:
            input_frame = np.zeros((depth_frame.shape[0], depth_frame.shape[1], 3), dtype=np.float32)
            _, hole_mask = _scatter_signed_disparity(input_frame, signed_disparity)
        else:
            input_frame = left_frames[batch_index].astype(np.float32, copy=False)
            warped_frame, hole_mask = _scatter_signed_disparity(input_frame, signed_disparity)
            warped_frames.append(np.clip(warped_frame, 0.0, 1.0).astype(np.float32, copy=False))

        hole_masks.append((hole_mask > 0).astype(np.float32, copy=False))

    warped_np = None if left_frames is None else np.stack(warped_frames, axis=0)
    mask_np = np.stack(hole_masks, axis=0)[:, None, :, :]
    return warped_np, mask_np


def _build_m2s_numpy_outputs(
    left_frames: np.ndarray | None,
    depth_frames: np.ndarray,
    convergence_point: float,
    max_disp_pixels: float,
):
    if max_disp_pixels is None:
        raise ValueError("max_disp_pixels is required")

    scale_factor = float(max_disp_pixels) * 2.0
    shift_px = float(convergence_point) * scale_factor

    warped_frames = []
    hole_masks = []

    for batch_index in range(depth_frames.shape[0]):
        # no ceiling: depth past 1 is Boost / Pop-Out / Particles in front (bug sweep 2026-10-09)
        depth_frame = np.maximum(depth_frames[batch_index, 0], 0.0).astype(np.float32, copy=False)
        if left_frames is None:
            dummy_input = np.zeros((depth_frame.shape[0], depth_frame.shape[1], 3), dtype=np.float32)
            warped_frame, hole_mask, _ = scatter_image(
                dummy_input,
                depth_frame,
                direction=-1,
                scale_factor=scale_factor,
                reproject_depth=False,
            )
        else:
            left_frame = left_frames[batch_index].astype(np.float32, copy=False)
            warped_frame, hole_mask, _ = scatter_image(
                left_frame,
                depth_frame,
                direction=-1,
                scale_factor=scale_factor,
                reproject_depth=False,
            )
            warped_frame = _translate_frame(warped_frame.astype(np.float32, copy=False), shift_px, border_value=0.0)
            warped_frames.append(np.clip(warped_frame, 0.0, 1.0).astype(np.float32, copy=False))

        shifted_mask = _translate_mask(hole_mask.astype(np.uint8, copy=False), shift_px)
        hole_masks.append((shifted_mask > 0).astype(np.float32, copy=False))

    warped_np = None if left_frames is None else np.stack(warped_frames, axis=0)
    mask_np = np.stack(hole_masks, axis=0)[:, None, :, :]
    return warped_np, mask_np


def _thin_label(stats_row, max_area: int, max_short_side: int, max_fill_ratio: float) -> bool:
    area = int(stats_row[cv2.CC_STAT_AREA])
    if area <= 0 or area > int(max_area):
        return False
    width = int(stats_row[cv2.CC_STAT_WIDTH])
    height = int(stats_row[cv2.CC_STAT_HEIGHT])
    fill_ratio = area / float(max(1, width * height))
    short_side = min(width, height)
    aspect_ratio = max(width, height) / float(max(1, short_side))
    return short_side <= int(max_short_side) or fill_ratio <= float(max_fill_ratio) or aspect_ratio >= 3.0


def _keep_thin_components(candidate_u8: np.ndarray, max_area: int, max_short_side: int, max_fill_ratio: float) -> np.ndarray:
    """The thin components of candidate_u8, as 1. One lookup over the label
    image (StereoCrafter Studio, 2026-09-29): the loop below (kept for the
    test) made a full-frame pass per component -- most of M2S+Fix's time."""
    if not np.any(candidate_u8):
        return np.zeros_like(candidate_u8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(candidate_u8, connectivity=8)
    lut = np.zeros(num_labels, dtype=candidate_u8.dtype)
    for label in range(1, num_labels):
        if _thin_label(stats[label], max_area, max_short_side, max_fill_ratio):
            lut[label] = 1
    return lut[labels]


def _keep_thin_components_loop(candidate_u8: np.ndarray, max_area: int, max_short_side: int, max_fill_ratio: float) -> np.ndarray:
    if not np.any(candidate_u8):
        return np.zeros_like(candidate_u8)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(candidate_u8, connectivity=8)
    keep = np.zeros_like(candidate_u8)
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0 or area > int(max_area):
            continue
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        bbox_area = max(1, width * height)
        fill_ratio = area / float(bbox_area)
        short_side = min(width, height)
        long_side = max(width, height)
        aspect_ratio = long_side / float(max(1, short_side))
        if short_side <= int(max_short_side) or fill_ratio <= float(max_fill_ratio) or aspect_ratio >= 3.0:
            keep[labels == label] = 1
    return keep


def _detect_m2s_seam_pixels(
    m2s_frame: np.ndarray,
    m2s_mask: np.ndarray,
    sc_frame: np.ndarray,
    sc_mask: np.ndarray,
    fix_strength: float = 1.0,
) -> np.ndarray:
    fix_strength = _clamp_fix_strength(fix_strength)
    sensitivity_scale = max(0.45, 1.0 / fix_strength)
    kernel_radius = int(max(0, round((fix_strength - 1.0) * 2.0)))
    band_kernel = max(3, 5 + (kernel_radius * 2))
    grow_iterations = 1 if fix_strength <= 1.15 else (2 if fix_strength <= 1.60 else 3)
    max_area = int(round(_DEF_MAX_SEAM_COMPONENT_AREA * max(1.0, 0.85 + (0.45 * fix_strength))))
    max_short_side = 3 if fix_strength <= 1.10 else (4 if fix_strength <= 1.60 else 5)
    max_fill_ratio = max(0.35, 0.55 - (0.10 * max(0.0, fix_strength - 1.0)))

    m2s_mask_bool = m2s_mask > 0.5
    sc_mask_bool = sc_mask > 0.5
    filled = ~(m2s_mask_bool | sc_mask_bool)
    if not np.any(filled):
        return np.zeros(m2s_mask.shape, dtype=np.uint8)

    m2s_rgb = np.clip(m2s_frame, 0.0, 1.0).astype(np.float32, copy=False)
    sc_rgb = np.clip(sc_frame, 0.0, 1.0).astype(np.float32, copy=False)
    m2s_gray = cv2.cvtColor(m2s_rgb, cv2.COLOR_RGB2GRAY)
    sc_gray = cv2.cvtColor(sc_rgb, cv2.COLOR_RGB2GRAY)

    diff_map = np.mean(np.abs(m2s_rgb - sc_rgb), axis=2).astype(np.float32, copy=False)
    edge_m2s = cv2.Laplacian(m2s_gray, cv2.CV_32F, ksize=3)
    edge_sc = cv2.Laplacian(sc_gray, cv2.CV_32F, ksize=3)
    abs_edge_m2s = np.abs(edge_m2s)
    abs_edge_sc = np.abs(edge_sc)
    edge_delta = abs_edge_m2s - abs_edge_sc
    brightness_delta = m2s_gray - sc_gray

    sc_boundary_band = cv2.dilate(
        (abs_edge_sc >= (_DEF_SEAM_SC_EDGE_THRESHOLD * max(0.55, sensitivity_scale))).astype(np.uint8),
        np.ones((band_kernel, band_kernel), np.uint8),
        iterations=1,
    ) > 0

    m2s_var = cv2.blur(m2s_gray * m2s_gray, (3, 3)) - cv2.blur(m2s_gray, (3, 3)) ** 2
    sc_var = cv2.blur(sc_gray * sc_gray, (3, 3)) - cv2.blur(sc_gray, (3, 3)) ** 2
    var_delta = m2s_var - sc_var

    candidate = (
        filled
        & sc_boundary_band
        & (diff_map >= ((_DEF_DIFF_THRESHOLD * 0.85) * sensitivity_scale))
        & (edge_delta >= ((_DEF_EDGE_DELTA_THRESHOLD * 0.70) * sensitivity_scale))
        & (brightness_delta >= (_DEF_SEAM_BRIGHTNESS_THRESHOLD * max(0.60, sensitivity_scale)))
        & (var_delta >= (_DEF_SEAM_VAR_THRESHOLD * max(0.60, sensitivity_scale)))
    )
    if not np.any(candidate):
        return np.zeros(m2s_mask.shape, dtype=np.uint8)

    keep = _keep_thin_components(candidate.astype(np.uint8), max_area, max_short_side, max_fill_ratio)
    if not np.any(keep):
        return keep

    keep = cv2.dilate(
        keep,
        np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8),
        iterations=grow_iterations,
    )
    keep = np.where(filled, keep, 0).astype(np.uint8, copy=False)
    return keep


def _apply_m2s_fix(
    m2s_warped_np: np.ndarray,
    m2s_mask_np: np.ndarray,
    sc_warped_np: np.ndarray,
    sc_mask_np: np.ndarray,
    fix_strength: float = 1.0,
):
    fixed_warped = m2s_warped_np.copy()
    fixed_mask = m2s_mask_np.copy()

    for batch_index in range(m2s_warped_np.shape[0]):
        m2s_frame = m2s_warped_np[batch_index]
        m2s_mask = m2s_mask_np[batch_index, 0]
        sc_frame = sc_warped_np[batch_index]
        sc_mask = sc_mask_np[batch_index, 0]

        seam_pixels = _detect_m2s_seam_pixels(
            m2s_frame,
            m2s_mask,
            sc_frame,
            sc_mask,
            fix_strength=fix_strength,
        )
        if not np.any(seam_pixels):
            continue

        seam_bool = seam_pixels.astype(bool)
        fixed_warped[batch_index][seam_bool] = sc_frame[seam_bool]

    return fixed_warped, fixed_mask


def _apply_m2s_fix_to_variant(
    left_np: np.ndarray,
    depth_np: np.ndarray,
    convergence_point: float,
    max_disp_pixels: float,
    sc_warped_np: np.ndarray,
    sc_mask_np: np.ndarray,
    output_builder,
    fix_strength: float = 1.0,
):
    m2s_warped_np, m2s_mask_np = output_builder(
        left_frames=left_np,
        depth_frames=depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
    )
    return _apply_m2s_fix(
        m2s_warped_np,
        m2s_mask_np,
        sc_warped_np,
        sc_mask_np,
        fix_strength=fix_strength,
    )


def build_m2s_occlusion_mask_lt_from_disparity(
    disp_map_tensor: torch.Tensor,
    output_dtype=None,
    eps: float = 1e-6,
) -> torch.Tensor:
    """Fast torch-only M2S-like hole mask from StereoCrafter's final disparity tensor.

    This keeps the existing SC RGB warp untouched and derives an occupancy mask from
    the same final disparity map already used for splatting:

        destination_x = x - disparity

    Compared with the full M2S mask helpers, this avoids the CPU/NumPy round-trip
    and is intended as a faster comparison backend for SC warp + sparse M2S-like mask.
    """
    if disp_map_tensor is None:
        raise ValueError("disp_map_tensor is required")
    if disp_map_tensor.ndim != 4 or int(disp_map_tensor.shape[1]) != 1:
        raise ValueError(
            f"disp_map_tensor must have shape (B, 1, H, W); got {tuple(disp_map_tensor.shape)}"
        )

    if output_dtype is None:
        output_dtype = disp_map_tensor.dtype

    disp = disp_map_tensor[:, 0].to(dtype=torch.float32)
    batch, height, width = disp.shape
    device = disp.device
    base_x = torch.arange(width, device=device, dtype=torch.float32).view(1, width)

    hole_masks = []
    for batch_index in range(batch):
        disp_frame = disp[batch_index]
        dest_x = base_x - disp_frame

        x0 = torch.floor(dest_x)
        x1 = x0 + 1.0
        w1 = dest_x - x0
        w0 = 1.0 - w1

        valid0 = (x0 >= 0.0) & (x0 < float(width))
        valid1 = (x1 >= 0.0) & (x1 < float(width))

        x0_idx = x0.clamp(0, width - 1).to(torch.int64)
        x1_idx = x1.clamp(0, width - 1).to(torch.int64)

        occupancy = torch.zeros((height, width), device=device, dtype=torch.float32)
        occupancy.scatter_add_(1, x0_idx, w0 * valid0.to(torch.float32))
        occupancy.scatter_add_(1, x1_idx, w1 * valid1.to(torch.float32))

        hole_masks.append((occupancy <= float(eps)).to(dtype=torch.float32))

    mask = torch.stack(hole_masks, dim=0).unsqueeze(1)
    return mask.to(dtype=output_dtype).clamp_(0.0, 1.0)

def _merge_sc_frame_edge_mask_into_hybrid(
    hybrid_mask_tensor: torch.Tensor,
    sc_mask_tensor: torch.Tensor,
    disp_map_tensor: torch.Tensor | None = None,
    output_dtype=None,
    edge_band_px: int = 3,
    edge_threshold: float = 0.001,
) -> torch.Tensor:
    """Merge only the exact SC mask pixels at the extreme left/right frame edge.

    This is intentionally much narrower than a normal SC-mask union:
      - fixed 1-3 px style edge band, default 3 px
      - no dilation, blur, pooling, or adaptive max-disparity widening
      - preserves the original SC mask values only where SC already has edge pixels

    The goal is to catch tiny crawling black edge specks without thickening Hybrid
    mask interiors or turning Hybrid back into a broad SC mask.
    disp_map_tensor is accepted for API compatibility but intentionally unused.
    """
    if hybrid_mask_tensor is None or sc_mask_tensor is None:
        return hybrid_mask_tensor
    if hybrid_mask_tensor.ndim != 4 or sc_mask_tensor.ndim != 4:
        return hybrid_mask_tensor
    if int(hybrid_mask_tensor.shape[1]) != 1 or int(sc_mask_tensor.shape[1]) != 1:
        return hybrid_mask_tensor
    if tuple(hybrid_mask_tensor.shape) != tuple(sc_mask_tensor.shape):
        return hybrid_mask_tensor

    width = int(hybrid_mask_tensor.shape[-1])
    if width <= 0:
        return hybrid_mask_tensor

    band_px = max(1, min(int(edge_band_px), width))
    desired_dtype = output_dtype if output_dtype is not None else hybrid_mask_tensor.dtype
    device = hybrid_mask_tensor.device

    hybrid_float = torch.clamp(hybrid_mask_tensor.to(dtype=torch.float32), 0.0, 1.0)
    sc_float = torch.clamp(sc_mask_tensor.to(device=device, dtype=torch.float32), 0.0, 1.0)

    allowed = torch.zeros_like(hybrid_float)
    allowed[..., :, :band_px] = 1.0
    allowed[..., :, width - band_px :] = 1.0

    sc_edge = torch.where(
        (allowed > 0.0) & (sc_float > float(edge_threshold)),
        sc_float,
        torch.zeros_like(sc_float),
    )

    return torch.maximum(hybrid_float, sc_edge).to(dtype=desired_dtype).clamp_(0.0, 1.0)

def _threshold_sc_mask_by_hybrid_sensitivity(
    sc_mask_tensor: torch.Tensor,
    output_dtype=None,
    hybrid_sensitivity: float = 50.0,
    min_threshold: float = 0.001,
    max_threshold: float = 0.85,
) -> torch.Tensor:
    """Return SC mask pixels above a sensitivity-controlled strength threshold.

    Sensitivity is intentionally user-facing and simple:
      - 100 = effectively full SC mask (all normal non-zero SC mask values)
      -   0 = only the strongest/whitest SC mask pixels

    This avoids basing Hybrid coverage on M2S/LT occupancy lines that can flicker
    in/out frame-to-frame while still letting the user suppress faint SC mask haze
    from small gradual depth shifts.
    """
    if output_dtype is None:
        output_dtype = sc_mask_tensor.dtype

    try:
        sensitivity = float(hybrid_sensitivity)
    except Exception:
        sensitivity = 50.0
    if not np.isfinite(sensitivity):
        sensitivity = 50.0
    sensitivity = max(0.0, min(100.0, sensitivity))

    sc_float = torch.clamp(sc_mask_tensor.to(dtype=torch.float32), 0.0, 1.0)

    # At max sensitivity, do not quantize/threshold the SC mask at all. This keeps
    # the result as close to pure SC as possible, including soft gray SC values.
    if sensitivity >= 99.5:
        return sc_float.to(dtype=output_dtype).clamp_(0.0, 1.0)

    threshold = float(max_threshold) - ((float(max_threshold) - float(min_threshold)) * (sensitivity / 100.0))
    threshold = max(float(min_threshold), min(float(max_threshold), threshold))

    hybrid = torch.where(sc_float >= threshold, sc_float, torch.zeros_like(sc_float))
    return hybrid.to(dtype=output_dtype).clamp_(0.0, 1.0)


def _apply_sc_mask_taper_to_hybrid(
    hybrid_mask_tensor: torch.Tensor,
    sc_mask_tensor: torch.Tensor,
    output_dtype=None,
    taper_enabled: bool = False,
    taper_radius_px: int = 3,
    min_taper_value: float = 0.001,
) -> torch.Tensor:
    """Add a small seed-limited SC feather around already-selected Hybrid areas.

    This is intentionally not a global lower threshold. The strong/sensitivity
    threshold first decides which SC mask regions are allowed. If taper is enabled,
    only pixels inside a tiny neighborhood of those already-allowed pixels can come
    back in, and they come back with their original SC mask gray value. This avoids
    pulling in disconnected faint cheek/hill haze while softening abrupt threshold
    cutoffs around real occlusion layers.
    """
    if hybrid_mask_tensor is None or sc_mask_tensor is None:
        return hybrid_mask_tensor
    if hybrid_mask_tensor.ndim != 4 or sc_mask_tensor.ndim != 4:
        return hybrid_mask_tensor
    if int(hybrid_mask_tensor.shape[1]) != 1 or int(sc_mask_tensor.shape[1]) != 1:
        return hybrid_mask_tensor
    if tuple(hybrid_mask_tensor.shape) != tuple(sc_mask_tensor.shape):
        return hybrid_mask_tensor
    if not bool(taper_enabled):
        desired_dtype = output_dtype if output_dtype is not None else hybrid_mask_tensor.dtype
        return hybrid_mask_tensor.to(dtype=desired_dtype).clamp_(0.0, 1.0)

    desired_dtype = output_dtype if output_dtype is not None else hybrid_mask_tensor.dtype
    hybrid_float = torch.clamp(hybrid_mask_tensor.to(dtype=torch.float32), 0.0, 1.0)
    sc_float = torch.clamp(sc_mask_tensor.to(device=hybrid_mask_tensor.device, dtype=torch.float32), 0.0, 1.0)

    radius = max(0, int(taper_radius_px))
    if radius <= 0:
        return hybrid_float.to(dtype=desired_dtype).clamp_(0.0, 1.0)

    # Seed only from pixels already accepted by the sensitivity threshold.
    seed = (hybrid_float > float(min_taper_value)).to(dtype=torch.float32)
    if not torch.any(seed > 0.0):
        return hybrid_float.to(dtype=desired_dtype).clamp_(0.0, 1.0)

    kernel = (radius * 2) + 1
    support = F.max_pool2d(seed, kernel_size=kernel, stride=1, padding=radius)

    # Preserve SC's original gray/soft values in the narrow support band only.
    taper = torch.where(
        (support > 0.5) & (sc_float > float(min_taper_value)),
        sc_float,
        torch.zeros_like(sc_float),
    )
    return torch.maximum(hybrid_float, taper).to(dtype=desired_dtype).clamp_(0.0, 1.0)


def build_m2s_occlusion_mask_hybrid_from_sc_and_disparity(
    sc_mask_tensor: torch.Tensor,
    disp_map_tensor: torch.Tensor | None = None,
    output_dtype=None,
    dilate_radius: int = 4,
    eps: float = 1e-6,
    hybrid_sensitivity: float = 50.0,
    edge_band_px: int = 3,
    hybrid_taper_enabled: bool = False,
    hybrid_taper_radius_px: int = 3,
) -> torch.Tensor:
    """SC-strength Hybrid mask with a user-controlled sensitivity threshold.

    This replaces the previous LT/M2S-occupancy-supported Hybrid logic. The old
    approach only kept SC mask around M2S-like hole lines, which could miss visible
    SC warp disturbances and could flicker as those occupancy lines appeared or
    disappeared. This version is driven directly by SC mask strength:

      - higher sensitivity keeps more SC mask, with 100 behaving like SC
      - lower sensitivity keeps only strong/white SC occlusion holes

    Optional taper keeps the sensitivity threshold as the seed, then restores a
    tiny radius of original SC gray values around only those selected regions.
    This softens abrupt threshold cutoffs without allowing disconnected faint SC
    mask haze elsewhere.

    The 1-3 px frame-edge SC safety strip is still restored separately so tiny
    edge specks remain covered even when sensitivity is low.

    disp_map_tensor, dilate_radius, and eps are accepted for API compatibility;
    Hybrid no longer derives its interior coverage from disparity/LT occupancy.
    """
    if sc_mask_tensor is None:
        raise ValueError("sc_mask_tensor is required")
    if sc_mask_tensor.ndim != 4 or int(sc_mask_tensor.shape[1]) != 1:
        raise ValueError(f"sc_mask_tensor must have shape (B, 1, H, W); got {tuple(sc_mask_tensor.shape)}")
    if disp_map_tensor is not None:
        if disp_map_tensor.ndim != 4 or int(disp_map_tensor.shape[1]) != 1:
            raise ValueError(f"disp_map_tensor must have shape (B, 1, H, W); got {tuple(disp_map_tensor.shape)}")
        if (
            int(sc_mask_tensor.shape[0]) != int(disp_map_tensor.shape[0])
            or int(sc_mask_tensor.shape[2]) != int(disp_map_tensor.shape[2])
            or int(sc_mask_tensor.shape[3]) != int(disp_map_tensor.shape[3])
        ):
            raise ValueError(
                f"sc_mask_tensor and disp_map_tensor spatial dims must match; got {tuple(sc_mask_tensor.shape)} and {tuple(disp_map_tensor.shape)}"
            )
    if output_dtype is None:
        output_dtype = sc_mask_tensor.dtype

    hybrid = _threshold_sc_mask_by_hybrid_sensitivity(
        sc_mask_tensor=sc_mask_tensor,
        output_dtype=torch.float32,
        hybrid_sensitivity=hybrid_sensitivity,
    )
    hybrid = _apply_sc_mask_taper_to_hybrid(
        hybrid_mask_tensor=hybrid,
        sc_mask_tensor=sc_mask_tensor,
        output_dtype=torch.float32,
        taper_enabled=hybrid_taper_enabled,
        taper_radius_px=hybrid_taper_radius_px,
    )
    hybrid = _merge_sc_frame_edge_mask_into_hybrid(
        hybrid_mask_tensor=hybrid,
        sc_mask_tensor=sc_mask_tensor,
        disp_map_tensor=disp_map_tensor,
        output_dtype=torch.float32,
        edge_band_px=edge_band_px,
    )
    return hybrid.to(dtype=output_dtype).clamp_(0.0, 1.0)

def build_m2s_occlusion_mask_from_depth(
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    output_dtype=None,
) -> torch.Tensor:
    _validate_inputs(None, depth_tensor)
    if output_dtype is None:
        output_dtype = depth_tensor.dtype

    device = depth_tensor.device
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()
    _, mask_np = _build_m2s_numpy_outputs(
        left_frames=None,
        depth_frames=depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
    )
    return torch.from_numpy(mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)


def build_m2s_occlusion_mask_inwarp_from_depth(
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    output_dtype=None,
) -> torch.Tensor:
    _validate_inputs(None, depth_tensor)
    if output_dtype is None:
        output_dtype = depth_tensor.dtype

    device = depth_tensor.device
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()
    _, mask_np = _build_m2s_numpy_outputs_inwarp(
        left_frames=None,
        depth_frames=depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
    )
    return torch.from_numpy(mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)


def build_m2s_occlusion_mask_aligned_to_sc_from_depth(
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    sc_mask_tensor: torch.Tensor,
    output_dtype=None,
) -> torch.Tensor:
    """Compatibility wrapper: for SC warp + Mask=M2S, use the in-warp M2S mask geometry.

    The previous SC-union behavior over-expanded the mask and pulled in the boxy
    straight edge strip. This wrapper keeps the old public name used by the GUI,
    but returns the same mask geometry as the M2S+InWarp backend so it aligns
    more closely with the SC-visible splat without unioning SC holes into it.
    sc_mask_tensor is accepted for API compatibility but intentionally unused.
    """
    _validate_inputs(None, depth_tensor)
    if sc_mask_tensor is None:
        raise ValueError("sc_mask_tensor is required for SC-aligned M2S mask")
    if sc_mask_tensor.ndim != 4 or int(sc_mask_tensor.shape[1]) != 1:
        raise ValueError(f"sc_mask_tensor must have shape (B, 1, H, W); got {tuple(sc_mask_tensor.shape)}")
    if (
        int(sc_mask_tensor.shape[0]) != int(depth_tensor.shape[0])
        or int(sc_mask_tensor.shape[2]) != int(depth_tensor.shape[2])
        or int(sc_mask_tensor.shape[3]) != int(depth_tensor.shape[3])
    ):
        raise ValueError(
            f"sc_mask_tensor and depth_tensor spatial dims must match; got {tuple(sc_mask_tensor.shape)} and {tuple(depth_tensor.shape)}"
        )
    if output_dtype is None:
        output_dtype = sc_mask_tensor.dtype

    return build_m2s_occlusion_mask_inwarp_from_depth(
        depth_tensor,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
        output_dtype=output_dtype,
    )


def build_m2s_warp_and_occlusion_from_depth(
    left_image_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    output_dtype=None,
):
    _validate_inputs(left_image_tensor, depth_tensor)
    if output_dtype is None:
        output_dtype = left_image_tensor.dtype

    device = left_image_tensor.device
    if device.type == "cuda":
        # the same warp on the GPU, bit for bit (gpu.py; StereoCrafter Studio 2026-09-29)
        warped, mask = build_m2s_outputs_torch(left_image_tensor, depth_tensor.to(device),
                                               float(convergence_point), float(max_disp_pixels))
        return warped.to(dtype=output_dtype).clamp_(0.0, 1.0), mask.to(dtype=output_dtype).clamp_(0.0, 1.0)
    left_np = left_image_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()

    warped_np, mask_np = _build_m2s_numpy_outputs(
        left_frames=left_np,
        depth_frames=depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
    )

    warped_tensor = torch.from_numpy(warped_np).permute(0, 3, 1, 2).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    mask_tensor = torch.from_numpy(mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    return warped_tensor, mask_tensor


def _gpu_m2s_numpy_builder(left_tensor: torch.Tensor, depth_tensor: torch.Tensor):
    """An output_builder for _apply_m2s_fix_to_variant that warps on the GPU
    and hands numpy back (the fixer itself is OpenCV)."""
    def build(left_frames, depth_frames, convergence_point, max_disp_pixels):
        warped, mask = build_m2s_outputs_torch(left_tensor, depth_tensor.to(left_tensor.device),
                                               convergence_point, max_disp_pixels)
        return warped.permute(0, 2, 3, 1).cpu().numpy(), mask.cpu().numpy()
    return build


def build_m2s_warp_and_occlusion_inwarp_from_depth(
    left_image_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    output_dtype=None,
):
    _validate_inputs(left_image_tensor, depth_tensor)
    if output_dtype is None:
        output_dtype = left_image_tensor.dtype

    device = left_image_tensor.device
    left_np = left_image_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()

    warped_np, mask_np = _build_m2s_numpy_outputs_inwarp(
        left_frames=left_np,
        depth_frames=depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
    )

    warped_tensor = torch.from_numpy(warped_np).permute(0, 3, 1, 2).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    mask_tensor = torch.from_numpy(mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    return warped_tensor, mask_tensor

def build_m2s_warp_and_occlusion_inwarp_fixed_from_depth(
    left_image_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    sc_right_tensor: torch.Tensor,
    sc_mask_tensor: torch.Tensor,
    output_dtype=None,
    fix_strength: float = 1.0,
):
    _validate_inputs(left_image_tensor, depth_tensor)
    if output_dtype is None:
        output_dtype = left_image_tensor.dtype
    if sc_right_tensor is None or sc_mask_tensor is None:
        raise ValueError("sc_right_tensor and sc_mask_tensor are required for M2S+InWarp+Fix")

    device = left_image_tensor.device
    left_np = left_image_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()
    sc_warped_np = sc_right_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    sc_mask_np = sc_mask_tensor.detach().to(dtype=torch.float32).cpu().numpy()

    fixed_warped_np, fixed_mask_np = _apply_m2s_fix_to_variant(
        left_np,
        depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
        sc_warped_np=sc_warped_np,
        sc_mask_np=sc_mask_np,
        output_builder=_build_m2s_numpy_outputs_inwarp,
        fix_strength=fix_strength,
    )

    warped_tensor = torch.from_numpy(fixed_warped_np).permute(0, 3, 1, 2).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    mask_tensor = torch.from_numpy(fixed_mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    return warped_tensor, mask_tensor



def build_m2s_warp_and_occlusion_fixed_from_depth(
    left_image_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    sc_right_tensor: torch.Tensor,
    sc_mask_tensor: torch.Tensor,
    output_dtype=None,
    fix_strength: float = 1.0,
):
    _validate_inputs(left_image_tensor, depth_tensor)
    if output_dtype is None:
        output_dtype = left_image_tensor.dtype
    if sc_right_tensor is None or sc_mask_tensor is None:
        raise ValueError("sc_right_tensor and sc_mask_tensor are required for M2S+Fix")

    device = left_image_tensor.device
    left_np = left_image_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    depth_np = depth_tensor.detach().to(dtype=torch.float32).cpu().numpy()
    sc_warped_np = sc_right_tensor.detach().to(dtype=torch.float32).permute(0, 2, 3, 1).cpu().numpy()
    sc_mask_np = sc_mask_tensor.detach().to(dtype=torch.float32).cpu().numpy()

    fixed_warped_np, fixed_mask_np = _apply_m2s_fix_to_variant(
        left_np,
        depth_np,
        convergence_point=float(convergence_point),
        max_disp_pixels=float(max_disp_pixels),
        sc_warped_np=sc_warped_np,
        sc_mask_np=sc_mask_np,
        output_builder=(_gpu_m2s_numpy_builder(left_image_tensor, depth_tensor) if device.type == "cuda"
                        else _build_m2s_numpy_outputs),
        fix_strength=fix_strength,
    )

    warped_tensor = torch.from_numpy(fixed_warped_np).permute(0, 3, 1, 2).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    mask_tensor = torch.from_numpy(fixed_mask_np).to(device=device, dtype=output_dtype).clamp_(0.0, 1.0)
    return warped_tensor, mask_tensor
