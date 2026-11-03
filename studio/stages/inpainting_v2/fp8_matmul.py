"""fp8_matmul.py -- Inpainting V2's FP8 Math switch (user, 2026-10-31): the transformer's matrix
multiplies in FP8 under MMGP streaming.

MMGP streams the transformer as quanto int8 weights and multiplies in bf16. With this on, each of
those multiplies runs as FP8 instead: the int8 weight (already on the GPU) and the activations are
scaled into FP8 E4M3, one scale each, and torch._scaled_mm multiplies them (RTX 40 / 50 cards:
twice bf16's rate). Measured on a 16 GB RTX 5060 Ti, 81 frames at 1408x768, 2 tiles, 5 steps: a
step 32.5 -> 26.8 s, the clip about 1.15x faster; the result 39.9 dB from the normal run's (37.8 dB
inside the holes), no difference to see.

How: MMGP keeps each layer's own bound forward, so a class patch never reaches it -- but its
QLinearQuantoRouter calls torch.nn.functional.linear(x, self.qweight) at call time. So this wraps
F.linear once, process-wide, and acts only while V2 has it on, only on a quanto int8 weight with a
bf16 CUDA input (only V2 uses quanto in Studio's process); everything else goes straight through.
Off by default; set at each Start (controller._start_now). Not with Torch Compile (untested together).

No torch import until it's switched on.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_enabled = False
_orig_linear = None
#: FP8 multiplies done since the process started (checks read it)
calls = 0


def supported() -> bool:
    """FP8 matmuls need compute capability 8.9 or newer (RTX 40 / 50)."""
    try:
        import torch
        if not torch.cuda.is_available() or not hasattr(torch, "_scaled_mm"):
            return False
        return torch.cuda.get_device_capability() >= (8, 9)
    except Exception:
        return False


def enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> bool:
    """Switch it for the next run; returns whether it's on (off where the card can't)."""
    global _enabled
    on = bool(on)
    if on and not supported():
        logger.info("FP8 Math needs an RTX 40 or 50 series card: running the normal way.")
        on = False
    if on:
        _install()
    if on != _enabled:
        logger.info("FP8 Math %s for Inpainting V2.", "on" if on else "off")
    _enabled = on
    return on


def _is_int8(w) -> bool:
    qtype = getattr(w, "qtype", None)
    return qtype is not None and getattr(qtype, "name", "") == "qint8" and hasattr(w, "dequantize")


def _to_fp8(t):
    import torch
    s = t.abs().amax().float().clamp_(min=1e-12).div_(448.0)        # E4M3's largest value
    return (t * (1.0 / s).to(t.dtype)).to(torch.float8_e4m3fn), s


def fp8_linear(x, w, bias=None):
    """x [..., K] bf16, w [N, K] bf16 -> [..., N] bf16; None for shapes the FP8 kernel won't take."""
    import torch
    k = x.shape[-1]
    x2 = x.reshape(-1, k)
    m, n = x2.shape[0], w.shape[0]
    if m < 16 or m % 16 or k % 16 or n % 16:
        return None
    x8, sx = _to_fp8(x2)
    w8, sw = _to_fp8(w)
    y = torch._scaled_mm(x8, w8.t(), scale_a=sx, scale_b=sw, out_dtype=x.dtype)
    if bias is not None:
        y = y + bias.to(x.dtype)
    return y.reshape(*x.shape[:-1], n)


def _install() -> None:
    global _orig_linear
    with _lock:
        if _orig_linear is not None:
            return
        import torch
        import torch.nn.functional as F
        orig = _orig_linear = F.linear

        def linear(input, weight, bias=None):
            global calls
            if (_enabled and _is_int8(weight) and input.is_cuda and input.dtype == torch.bfloat16
                    and weight.dim() == 2):
                y = fp8_linear(input, weight.dequantize().to(torch.bfloat16), bias)
                if y is not None:
                    calls += 1
                    return y
            return orig(input, weight, bias)
        linear.__wrapped__ = orig
        torch.nn.functional.linear = linear


def uninstall() -> None:
    """F.linear back as it was (tests)."""
    global _orig_linear, _enabled
    with _lock:
        if _orig_linear is None:
            return
        import torch
        torch.nn.functional.linear = _orig_linear
        _orig_linear = None
        _enabled = False
