"""Forward warp module for stereo video generation.

Provides the ForwardWarpStereo PyTorch module for warping images
based on disparity maps for 3D video generation.
"""

import logging
from typing import Optional, Tuple, Union

import torch
import torch.nn as nn

from . import m2s_backends

logger = logging.getLogger(__name__)


def fill_edge_occlusions(
    right_video_tensor: torch.Tensor, occlusion_mask_tensor: torch.Tensor, outline_thickness: int = 2
) -> torch.Tensor:
    """
    Fills horizontal edge occlusions at the FRAME boundaries (both left and right sides) in the right-eye video
    by drawing an outline of the nearest visible pixels to aid inpainting, rather than extending the image edge inwards.

    Args:
        right_video_tensor (torch.Tensor): The forward-warped right-eye video tensor [B, C, H, W], values in [0, 1].
        occlusion_mask_tensor (torch.Tensor): The corresponding occlusion mask tensor [B, 1, H, W], where 1 indicates occlusion.
        outline_thickness (int): The number of pixels to draw the outline for inside the occlusion gap.
    Returns:
        torch.Tensor: The modified right-eye video tensor.
    """
    B, C, H, W = right_video_tensor.shape

    # Ensure outline_thickness is within valid bounds
    outline_thickness = min(W // 2, outline_thickness)

    if outline_thickness <= 0:
        return right_video_tensor

    modified_right_video_tensor = right_video_tensor.clone()

    is_occluded = occlusion_mask_tensor[:, 0, :, :] > 0.5  # [B, H, W]
    visible = ~is_occluded

    # --- LEFT EDGE ---
    # Find the nearest visible pixel from the left for each row
    first_visible_idx = visible.long().argmax(dim=2, keepdim=True)  # [B, H, 1]
    first_visible_idx_expanded = first_visible_idx.unsqueeze(1).expand(-1, C, -1, -1)  # [B, C, H, 1]

    # Gather colors of the first visible pixels
    left_colors = right_video_tensor.gather(3, first_visible_idx_expanded)  # [B, C, H, 1]
    left_colors_expanded = left_colors.expand(-1, -1, -1, outline_thickness)  # [B, C, H, thickness]

    # Only overwrite the left frame edge IF it is occluded
    left_occluded = is_occluded[:, :, :outline_thickness].unsqueeze(1).expand(-1, C, -1, -1)  # [B, C, H, thickness]

    modified_right_video_tensor[:, :, :, :outline_thickness] = torch.where(
        left_occluded, left_colors_expanded, modified_right_video_tensor[:, :, :, :outline_thickness]
    )

    # --- RIGHT EDGE ---
    # Find the nearest visible pixel from the right for each row
    last_visible_idx_rev = visible.flip(dims=[2]).long().argmax(dim=2, keepdim=True)  # [B, H, 1]
    last_visible_idx = W - 1 - last_visible_idx_rev
    last_visible_idx_expanded = last_visible_idx.unsqueeze(1).expand(-1, C, -1, -1)  # [B, C, H, 1]

    # Gather colors of the last visible pixels
    right_colors = right_video_tensor.gather(3, last_visible_idx_expanded)  # [B, C, H, 1]
    right_colors_expanded = right_colors.expand(-1, -1, -1, outline_thickness)  # [B, C, H, thickness]

    # Only overwrite the right frame edge IF it is occluded
    right_occluded = is_occluded[:, :, -outline_thickness:].unsqueeze(1).expand(-1, C, -1, -1)  # [B, C, H, thickness]

    modified_right_video_tensor[:, :, :, -outline_thickness:] = torch.where(
        right_occluded, right_colors_expanded, modified_right_video_tensor[:, :, :, -outline_thickness:]
    )

    return modified_right_video_tensor


try:
    from Forward_Warp import forward_warp

    logger.info("CUDA Forward Warp is available.")
except Exception:
    from dependency.forward_warp_pytorch import forward_warp

    logger.info("Forward Warp Pytorch is active.")

from .m2s_mask import build_m2s_occlusion_mask_from_disparity
# apply_guided_prefilter: Boost Pre-filter. Never imported before, so the
# NameError was swallowed below and the filter never ran (audit 2026-09-28 #16).
from .depth_boost import (apply_depth_boost, apply_guided_prefilter, boost_is_active, pop_out_extra,
                          pop_out_is_active)


#: The widest disparity span (px) the occlusion weights take as is: 1.414**240
#: is ~1e36, so even hundreds of such pixels summed on one target stay in
#: float32 (max ~3.4e38).
_WEIGHT_SPAN = 240.0


class ForwardWarpStereo(nn.Module):
    """
    PyTorch module for forward warping an image based on a disparity map.

    This module performs forward warping where each pixel in the input
    image is shifted according to the disparity map to create a warped
    output suitable for stereoscopic 3D video generation.

    Args:
        eps: Small epsilon value for numerical stability (default: 1e-6)
        occlu_map: Whether to return occlusion map alongside warped image
                   (default: False)

    Example:
        >>> import torch
        >>> from core.splatting.forward_warp import ForwardWarpStereo
        >>>
        >>> # Create module
        >>> fws = ForwardWarpStereo(eps=1e-6, occlu_map=False)
        >>>
        >>> # Forward pass
        >>> image = torch.randn(1, 3, 512, 1024)  # [B, C, H, W]
        >>> disparity = torch.randn(1, 1, 512, 512)  # [B, 1, H, W]
        >>> warped = fws(image, disparity)
    """

    def __init__(self, eps: float = 1e-6, occlu_map: bool = False):
        """Initialize the forward warp stereo module.

        Args:
            eps: Small value to prevent division by zero
            occlu_map: Whether to return occlusion map
        """
        super().__init__()
        self.eps = eps
        self.occlu_map = occlu_map
        self.fw = forward_warp()

    def forward(self, im: torch.Tensor, disp: torch.Tensor) -> Union[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        """Perform forward warping of image based on disparity.

        Args:
            im: Input image tensor of shape [B, C, H, W]
            disp: Disparity map tensor of shape [B, 1, H, W]
                  Positive values shift pixels right (right eye view)

        Returns:
            If occlu_map is False:
                Warped image tensor of shape [B, C, H, W]
            If occlu_map is True:
                Tuple of (warped_image, occlusion_map)
                - warped_image: [B, C, H, W]
                - occlusion_map: [B, 1, H, W] showing areas without source pixels
        """
        im = im.contiguous()
        disp = disp.contiguous()

        # Create weights map from disparity: where pixels land on one target, the
        # nearest wins, by 1.414 per px of disparity above the batch's farthest.
        # Past ~255 px of span that overflowed float32 (1.414**256 = inf) and the
        # foreground came out NaN -- a 3840-wide render at Disparity 67, less with
        # a TV-range map, Boost or Pop-Out (bug sweep 2026-10-09 #15). A wider span
        # is compressed into _WEIGHT_SPAN: the same order, a little softer; up to
        # it, the weights are exactly as before. No .item(): no GPU sync.
        lo = disp.amin()
        weights_map = disp - lo
        span = disp.amax() - lo
        weights_map = weights_map * torch.clamp(_WEIGHT_SPAN / span.clamp(min=1e-6), max=1.0)
        weights_map = (1.414) ** weights_map

        # Create flow field from disparity (negative for right eye shift)
        flow = -disp.squeeze(1)
        dummy_flow = torch.zeros_like(flow, requires_grad=False)
        flow = torch.stack((flow, dummy_flow), dim=-1)

        # Perform forward warp
        res_accum = self.fw(im * weights_map, flow)
        mask = self.fw(weights_map, flow)
        mask.clamp_(min=self.eps)
        res = res_accum / mask

        if not self.occlu_map:
            return res
        else:
            # Compute occlusion map
            ones = torch.ones_like(disp, requires_grad=False)
            occlu_map = self.fw(ones, flow)
            occlu_map.clamp_(0.0, 1.0)
            occlu_map = 1.0 - occlu_map
            return res, occlu_map


def execute_forward_warp(
    stereo_projector: ForwardWarpStereo,
    source_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    target_width: int,
    max_disp: float,
    zero_disparity_anchor_val: float,
    input_bias: float = 0.0,
    tv_disp_comp: float = 1.0,
    edge_outline_thickness: int = 2,
    debug_task_name: str = "Render",
    mask_mode: str = "SC",
    splat_backend: str = "SC",
    hybrid_sensitivity: float = 50.0,
    hybrid_taper: bool = False,
    m2s_fix_strength: float = 1.0,
    is_low_res_task: bool = False,
    dual_eye: bool = False,
    dual_eye_bias: float = 0.5,
    boost_thresh: float = 0.85,
    boost_amount: float = 0.0,
    boost_curve: float = 2.0,
    boost_knee: float = 0.0,
    boost_prefilter_radius: int = 0,
    boost_prefilter_eps: float = 1e-3,
    depth_peak: float = 1.0,
    pop_amount: float = 0.0,
    disp_probe: Optional[dict] = None,
    extra_depth: Optional[torch.Tensor] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Executes the shared forward stereo projection logic.

    Args:
        stereo_projector: The ForwardWarpStereo module instance
        source_tensor: Batch of source images [B, C, H, W] in [0, 1] range, on GPU
        depth_tensor: Batch of depth maps [B, 1, H, W] in [0, 1] range, on GPU
        target_width: Width of the target image (used for % disparity calculation)
        max_disp: Maximum disparity percentage
        zero_disparity_anchor_val: The convergence anchor value [0, 1]
        input_bias: Depth map float bias adjustment
        tv_disp_comp: TV-range disparity compensation multiplier
        debug_task_name: Task name for conditional logic (e.g., "Preview", "Render")
        mask_mode: Mask generation mode - "SC" for StereoCrafter original, "M2S" for
            M2S-style occupancy mask, "Hybrid" for the SC mask widened using the
            disparity map, "Dual" for Hybrid at full res and SC at low res.
        splat_backend: Which implementation warps the image - "SC", "M2S", or
            "M2S+Fix" (M2S with its tear fixer, which needs the SC result first).
        hybrid_sensitivity: 0-100, how eagerly Hybrid widens the SC mask.
        hybrid_taper: soften the edge Hybrid adds instead of leaving it hard.
        m2s_fix_strength: 0.50-2.00, scales the M2S tear fix.
        is_low_res_task: whether this is the low-resolution pass. Only "Dual"
            reads it, to use SC there and Hybrid at full resolution.
        dual_eye: If True, warp BOTH eyes from the source (each carries a share of the
                  total disparity) instead of keeping the source as a pristine left eye.
                  Halves per-eye occlusion holes. Total L<->R separation is unchanged.
        dual_eye_bias: Fraction of the total disparity assigned to the RIGHT eye in dual
                  mode, in [0, 1]. 0.5 = symmetric (-half / +half). Lower values keep the
                  left eye closer to the original (e.g. 0.66 -> left -1/3, right +2/3).
        boost_thresh: Depth Boost threshold — normalized depth where the boost ramps in.
        boost_amount: Depth Boost strength in Max Disparity slider units. 0.0 = off.
                  Lets near-camera pixels exceed the nominal max disparity ("extra
                  front convergence"). Applied AFTER the [0,1] clip and input_bias,
                  so boosted depth intentionally exceeds 1.0 from this point on.
        boost_curve: Depth Boost ramp exponent between thresh and full white.
        boost_knee: Smoothstep gate width at the bottom of the boost ramp, in
                  w units. 0.0 = off (legacy). Only useful for boost_curve
                  below 1.0, where the ramp slope diverges at the threshold.
        pop_amount: Pop-Out, in Max Disparity slider units. 0.0 = off. Extra
                  pop in front of the screen only: zero at and behind the
                  convergence plane, rising as u**2 to the full amount at the
                  clip's nearest depth (depth_peak). See depth_boost.pop_out_extra.
        extra_depth: added to the depth after Boost / Pop-Out (past the [0, 1]
                  clip, like them): the rain's own Pop (core.splatting.rain).
        boost_prefilter_radius: "Edge Clean". Radius, in pixels, of an
                  edge-aware guided filter (the source image as the guide).
                  Since 2026-10-01 it shapes the EXTRA pop (Boost + Pop-Out)
                  rather than the depth: the extra snaps to the picture's own
                  edges, so a boosted object gets one even push up to its real
                  outline instead of its soft depth edge being stretched. The
                  user saw no difference from the old depth pre-filter --
                  DepthCrafter's maps are smooth already, and smoothing them
                  cannot sharpen a soft outline. 0 = off. Historical notes:

                  The boost multiplies depth GRADIENTS by up to ~4x near white
                  (see depth_boost.py), and that amplified gradient is what the
                  Stretch->Hole gap test (removed 2026-10-09) saw. On small bright subjects such as
                  sparks, quantization noise in the depth map is therefore
                  amplified into spurious holes whose boundary crawls between
                  frames -- the "squiggly edge" failure. Filtering first snaps
                  the depth contour onto real image edges, so the boost acts on
                  the subject outline instead of on noise.

                  Placed before the boost and not earlier on purpose: unboosted
                  depth is not amplified, so filtering the whole pipeline would
                  soften geometry for no benefit.
        boost_prefilter_eps: Guided filter edge sensitivity. Smaller preserves
                  more edges but smooths less.
        depth_peak: Clip depth maximum in the SAME (post-gamma) domain as
                  depth_tensor. When < 1.0, the boost threshold/ramp anchor
                  to this peak instead of pure white: thresh means 'fraction
                  of the clip's own range' and the nearest pixel in the clip
                  receives full ramp weight. 1.0 (default) = legacy behavior.
        disp_probe: Optional dict filled with the FINAL per-eye disparity in
            pixels under key "right" (and "left" for dual-eye). Callers that
            need to reason about what the splat will actually do -- e.g. Hole
            Shift -- must use this rather than recomputing it,
            because the value here is post depth_peak clamping and post
            dual-eye splitting, both of which change the gaps materially.
                  Typical useful range 1.5 - 4.0 (lower = more aggressive).

    Returns:
        Single-eye mode (dual_eye=False):
            Tuple of (right_eye_tensor, occlusion_mask)
        Dual-eye mode (dual_eye=True):
            Tuple of (left_eye_tensor, left_mask, right_eye_tensor, right_mask)
    """
    depth_tensor = torch.clip(depth_tensor, 0.0, 1.0)

    if input_bias != 0:
        depth_tensor = torch.clip(depth_tensor + input_bias, 0.0, 1.0)

    # --- Depth Boost ("extra front convergence") ---
    # Must come AFTER both clips above: the boost pushes depth beyond 1.0 by
    # design, and any later clamp would silently flatten it. In dual-eye mode
    # the boosted disparity is split by dual_eye_bias below, so the extra pop
    # stays L/R consistent through the single inpaint pass.
    _boost_on = boost_is_active(boost_amount, max_disp)
    _pop_on = pop_out_is_active(pop_amount, max_disp)
    _edge_clean = bool(boost_prefilter_radius) and int(boost_prefilter_radius) > 0
    _base = depth_tensor
    if _boost_on:
        # (unchanged maths; Edge Clean now acts on the extra pop below)
        _peak = min(max(float(depth_peak), 1e-3), 1.0)
        if _peak < 0.999:
            # Peak-anchored boost: evaluate the ramp in the clip's own
            # depth range (d / peak, clamped), then add only the DELTA to
            # the real depth. Base disparity is untouched; thresh/curve
            # semantics become relative to the clip's actual peak, and
            # the clip's nearest pixel gets full boost weight.
            _scaled = torch.clamp(depth_tensor / _peak, max=1.0)
            depth_tensor = depth_tensor + (
                apply_depth_boost(
                    _scaled,
                    boost_thresh=boost_thresh,
                    boost_amount=boost_amount,
                    boost_curve=boost_curve,
                    max_disp=max_disp,
                    boost_knee=boost_knee,
                )
                - _scaled
            )
        else:
            depth_tensor = apply_depth_boost(
                depth_tensor,
                boost_thresh=boost_thresh,
                boost_amount=boost_amount,
                boost_curve=boost_curve,
                max_disp=max_disp,
                boost_knee=boost_knee,
            )

    # Pop-Out, and Edge Clean on the whole extra pop. With neither, the depth is
    # exactly what the boost alone made (a render with neither is unchanged).
    if _pop_on or (_edge_clean and (_boost_on or _pop_on)):
        _extra = depth_tensor - _base
        if _pop_on:
            _extra = _extra + pop_out_extra(
                _base, pop_amount, zero_disparity_anchor_val, max_disp,
                depth_peak=min(max(float(depth_peak), 1e-3), 1.0),
            )
        if _edge_clean:
            try:
                _extra = apply_guided_prefilter(
                    _extra, source_tensor, radius=int(boost_prefilter_radius), eps=float(boost_prefilter_eps),
                ).clamp(min=0.0)
            except Exception as _e_pf:
                # A filter failure must not cost the frame: the unfiltered pop.
                logger.warning(f"[{debug_task_name}] Edge Clean skipped: {_e_pf}")
        depth_tensor = _base + _extra

    if extra_depth is not None:
        depth_tensor = depth_tensor + extra_depth

    disp_map = (depth_tensor - zero_disparity_anchor_val) * 2.0
    actual_max_disp_pixels = (max_disp / 20.0 / 100.0) * target_width * tv_disp_comp
    disp_map = disp_map * actual_max_disp_pixels

    # The FINAL per-eye disparity in pixels -- the same array handed to the
    # projector -- for callers that reason about what the splat does.
    if disp_probe is not None:
        try:
            disp_probe["right"] = disp_map.detach()
        except Exception:
            pass

    # Which backends this pass is actually running. Resolved once, here,
    # rather than inside each branch: "Dual" is not a backend, it is a choice
    # between two, and "M2S+Fix" is the M2S warp with its fixer switched on.
    resolved_mask_mode = m2s_backends.resolve_mask_mode(mask_mode, is_low_res_task)
    resolved_splat, m2s_fix_enabled = m2s_backends.resolve_splat_backend(splat_backend)

    if not dual_eye:
        with torch.no_grad():
            # M2S without its fixer needs no SC warp: run it first, and warp
            # with SC only if it declines (Studio audit 2026-09-28, PERF: the
            # SC warp ran every batch and was thrown away).
            warped = None
            if resolved_splat == "M2S" and not m2s_fix_enabled:
                warped = m2s_backends.m2s_warp(
                    source_tensor,
                    depth_tensor,
                    convergence_point=zero_disparity_anchor_val,
                    max_disp_pixels=actual_max_disp_pixels,
                    fix_enabled=False,
                )
            if warped is not None:
                right_eye_raw, occlusion_mask = warped
            else:
                right_eye_raw, occlusion_mask = stereo_projector(source_tensor, disp_map)

            if resolved_splat == "M2S" and m2s_fix_enabled:
                # The SC result above is not wasted when the fixer is on: that
                # is exactly what it works from. Handing it over also means a
                # backend that declines leaves us with a finished render.
                warped = m2s_backends.m2s_warp(
                    source_tensor,
                    depth_tensor,
                    convergence_point=zero_disparity_anchor_val,
                    max_disp_pixels=actual_max_disp_pixels,
                    fix_enabled=m2s_fix_enabled,
                    fix_strength=m2s_fix_strength,
                    sc_right=right_eye_raw,
                    sc_mask=occlusion_mask,
                )
                if warped is not None:
                    right_eye_raw, occlusion_mask = warped

            if resolved_mask_mode == "M2S":
                occlusion_mask = build_m2s_occlusion_mask_from_disparity(disp_map, output_dtype=occlusion_mask.dtype)
            elif resolved_mask_mode == "HYBRID":
                occlusion_mask = m2s_backends.apply_hybrid_mask(
                    occlusion_mask, disp_map, hybrid_sensitivity, hybrid_taper
                )

            if debug_task_name != "Preview":
                right_eye_raw = fill_edge_occlusions(
                    right_eye_raw, occlusion_mask, outline_thickness=edge_outline_thickness
                )

        return right_eye_raw, occlusion_mask

    # --- Dual-eye half-shift path ---
    # Split the total disparity between the two eyes. The module's internal
    # flow = -disp shifts in the "right eye" direction, so we feed +share for the
    # right eye and the negated share for the left eye to shift the opposite way.
    right_frac = float(max(0.0, min(1.0, dual_eye_bias)))
    left_frac = 1.0 - right_frac

    right_disp = disp_map * right_frac
    left_disp = disp_map * (-left_frac)

    # M2S per eye (Studio audit 2026-09-28: dual-eye always warped with SC,
    # whatever the backend). M2S warps the right way only, so the left eye is
    # warped mirrored: flip the frame and depth, warp with the left eye's share,
    # flip back -- the mirror of SC's negated-disparity shift.
    def _m2s_eye(frac, left, sc_img=None, sc_mask=None, fix=False):
        f = (lambda t: torch.flip(t, dims=[-1])) if left else (lambda t: t)
        out = m2s_backends.m2s_warp(
            f(source_tensor),
            f(depth_tensor),
            convergence_point=zero_disparity_anchor_val,
            max_disp_pixels=actual_max_disp_pixels * frac,
            fix_enabled=fix,
            fix_strength=m2s_fix_strength,
            sc_right=f(sc_img) if sc_img is not None else None,
            sc_mask=f(sc_mask) if sc_mask is not None else None,
        )
        return None if out is None else (f(out[0]), f(out[1]))

    with torch.no_grad():
        use_m2s = resolved_splat == "M2S"
        warped_r = _m2s_eye(right_frac, False) if use_m2s and not m2s_fix_enabled else None
        warped_l = _m2s_eye(left_frac, True) if use_m2s and not m2s_fix_enabled else None
        # SC where M2S is not asked for, declined, or its fixer needs SC to work from
        right_eye_raw, right_mask = warped_r if warped_r is not None else stereo_projector(source_tensor, right_disp)
        left_eye_raw, left_mask = warped_l if warped_l is not None else stereo_projector(source_tensor, left_disp)
        if use_m2s and m2s_fix_enabled:
            fixed = _m2s_eye(right_frac, False, right_eye_raw, right_mask, fix=True)
            if fixed is not None:
                right_eye_raw, right_mask = fixed
            fixed = _m2s_eye(left_frac, True, left_eye_raw, left_mask, fix=True)
            if fixed is not None:
                left_eye_raw, left_mask = fixed

        if resolved_mask_mode == "M2S":
            right_mask = build_m2s_occlusion_mask_from_disparity(right_disp, output_dtype=right_mask.dtype)
            left_mask = build_m2s_occlusion_mask_from_disparity(left_disp, output_dtype=left_mask.dtype)
        elif resolved_mask_mode == "HYBRID":
            # Each eye is widened against its OWN share of the disparity, the
            # same way the M2S branch above does it.
            right_mask = m2s_backends.apply_hybrid_mask(
                right_mask, right_disp, hybrid_sensitivity, hybrid_taper
            )
            left_mask = m2s_backends.apply_hybrid_mask(
                left_mask, left_disp, hybrid_sensitivity, hybrid_taper
            )

        if disp_probe is not None:
            try:
                disp_probe["right"] = right_disp.detach()
                disp_probe["left"] = left_disp.detach()
            except Exception:
                pass

        if debug_task_name != "Preview":
            right_eye_raw = fill_edge_occlusions(
                right_eye_raw, right_mask, outline_thickness=edge_outline_thickness
            )
            left_eye_raw = fill_edge_occlusions(
                left_eye_raw, left_mask, outline_thickness=edge_outline_thickness
            )

    return left_eye_raw, left_mask, right_eye_raw, right_mask
