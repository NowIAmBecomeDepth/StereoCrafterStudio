"""m2s_backends.py -- the optional M2S mask and warp backends.

StereoCrafter's own splatting (referred to here as SC) warps the left eye with
its final disparity map and reports the holes that opens up. M2S is a second
implementation, vendored under ``dependency_R/m2s_backend``, which can do the
same job differently:

  * **Hybrid mask** -- keeps the SC warp and the SC mask, then THINS that mask
    by strength. It does not add anything, which the name rather suggests it
    might: ``sensitivity`` (0-100) is a threshold on the SC mask itself, where
    100 behaves like plain SC and lower values keep only the strong, white
    occlusion holes and drop the faint grey haze. The taper then restores a
    few pixels of the original grey around whatever survived, so the cut does
    not end on a hard step.

    It ignores the disparity map. The vendored helper still accepts one for
    API compatibility, and one is passed, but Hybrid no longer derives any
    coverage from it.
  * **Dual mask** -- Hybrid at full resolution, SC at low resolution.
  * **M2S warp** -- builds the warped image AND its mask from the raw depth
    map rather than from SC's disparity, so the RGB output changes, not just
    the mask.
  * **M2S+Fix** -- runs the SC warp first and hands both results to the M2S
    fixer, which uses them to close tears the plain M2S warp leaves behind.
    ``fix_strength`` (0.50-2.00) scales that correction.

Everything here is OPTIONAL. ``dependency_R`` is a separate tree that may not
be present, so the import is guarded and every entry point falls back to SC
behaviour when a backend is missing or raises. A render must never die because
an alternative backend was unavailable -- it should quietly produce the SC
result, which is what the app did before any of this existed.

The M2S occlusion mask built from disparity is NOT here: that one was already
ported into :mod:`core.splatting.m2s_mask`, and is byte-for-byte the same
algorithm as the vendored ``build_m2s_occlusion_mask_lt_from_disparity``.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import torch

logger = logging.getLogger(__name__)

#: Mask backends the UI can ask for, in the order they are offered.
MASK_MODES = ("SC", "M2S", "Hybrid", "Dual")

#: Warp backends the UI can ask for.
SPLAT_BACKENDS = ("SC", "M2S", "M2S+Fix")

_IMPORT_ERROR: Optional[BaseException] = None

try:
    from dependency_R.m2s_backend import (
        build_m2s_occlusion_mask_hybrid_from_sc_and_disparity as _hybrid_mask,
        build_m2s_warp_and_occlusion_from_depth as _m2s_warp,
        build_m2s_warp_and_occlusion_fixed_from_depth as _m2s_warp_fixed,
    )
except Exception as exc:  # noqa: BLE001 -- any failure means "not available"
    _IMPORT_ERROR = exc
    _hybrid_mask = None
    _m2s_warp = None
    _m2s_warp_fixed = None
    logger.info(
        "M2S backends unavailable; Hybrid/Dual masks and the M2S warp will fall "
        "back to SC. Reason: %s", exc
    )


def backend_available() -> bool:
    """Whether the vendored M2S backend imported."""
    return _hybrid_mask is not None


def unavailable_reason() -> str:
    """Why it did not import, for a status line. Empty when it did."""
    return "" if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


def resolve_mask_mode(mask_mode: str, is_low_res_task: bool = False) -> str:
    """Normalise a UI mask selection to what this pass should actually run.

    Returns one of "SC", "M2S" or "HYBRID". Dual is not a backend of its own:
    it means Hybrid at full resolution and SC at low resolution, so it is
    resolved here rather than being carried any further down.
    """
    value = str(mask_mode or "SC").strip().upper()
    if value == "DUAL":
        return "SC" if bool(is_low_res_task) else "HYBRID"
    if value not in {"SC", "M2S", "HYBRID"}:
        return "SC"
    return value


def resolve_splat_backend(splat_backend: str) -> Tuple[str, bool]:
    """Normalise a UI warp selection to ``(backend, fix_enabled)``.

    "M2S+Fix" is the M2S warp with the tear fixer switched on, not a third
    backend, so it is split here into the pair the caller needs.
    """
    value = str(splat_backend or "SC").strip().upper()
    if value in {"M2S+FIX", "M2S + FIX"}:
        return "M2S", True
    if value != "M2S":
        return "SC", False
    return "M2S", False


def apply_hybrid_mask(
    sc_mask: torch.Tensor,
    disp_map: torch.Tensor,
    sensitivity: float = 50.0,
    taper: bool = False,
) -> torch.Tensor:
    """Thin an SC mask by strength. Returns the SC mask unchanged on failure.

    `sensitivity` is a threshold, not an amount to add: 100 keeps the SC mask
    as it is, and lower values drop the faint grey parts of it. A mask that
    is already binary therefore comes back untouched at every setting.

    `disp_map` is passed for API compatibility only -- the vendored helper
    takes one and ignores it.

    The SC mask is the fallback rather than an exception: a slightly wrong
    mask still renders, while a dead render loses the whole job.
    """
    if _hybrid_mask is None or sc_mask is None or disp_map is None:
        return sc_mask
    try:
        return _hybrid_mask(
            sc_mask_tensor=sc_mask,
            disp_map_tensor=disp_map,
            output_dtype=sc_mask.dtype,
            hybrid_sensitivity=float(sensitivity),
            hybrid_taper_enabled=bool(taper),
        )
    except Exception:
        logger.warning("Hybrid mask failed; using the SC mask.", exc_info=True)
        return sc_mask


def m2s_warp(
    source_tensor: torch.Tensor,
    depth_tensor: torch.Tensor,
    convergence_point: float,
    max_disp_pixels: float,
    fix_enabled: bool = False,
    fix_strength: float = 1.0,
    sc_right: Optional[torch.Tensor] = None,
    sc_mask: Optional[torch.Tensor] = None,
) -> Optional[Tuple[torch.Tensor, torch.Tensor]]:
    """Warp with M2S, returning ``(right_eye, occlusion)``, or None to use SC.

    None rather than raising, and None rather than a half-result: the caller
    already has a working SC path, and the only useful thing to say when a
    backend will not run is "use yours".

    ``fix_enabled`` needs the SC result to work from, so a caller that cannot
    supply ``sc_right``/``sc_mask`` gets the unfixed warp instead of nothing.
    """
    if _m2s_warp is None or source_tensor is None or depth_tensor is None:
        return None
    if convergence_point is None or max_disp_pixels is None:
        return None

    if fix_enabled and _m2s_warp_fixed is not None and sc_right is not None and sc_mask is not None:
        try:
            return _m2s_warp_fixed(
                source_tensor,
                depth_tensor,
                convergence_point=float(convergence_point),
                max_disp_pixels=float(max_disp_pixels),
                sc_right_tensor=sc_right,
                sc_mask_tensor=sc_mask,
                output_dtype=source_tensor.dtype,
                fix_strength=float(fix_strength),
            )
        except Exception:
            # Fall through to the unfixed warp rather than all the way to SC:
            # the fixer is a refinement of a result that may itself be fine.
            logger.warning("M2S tear fix failed; using the unfixed M2S warp.", exc_info=True)

    try:
        return _m2s_warp(
            source_tensor,
            depth_tensor,
            convergence_point=float(convergence_point),
            max_disp_pixels=float(max_disp_pixels),
            output_dtype=source_tensor.dtype,
        )
    except Exception:
        logger.warning("M2S warp failed; using the SC warp.", exc_info=True)
        return None
