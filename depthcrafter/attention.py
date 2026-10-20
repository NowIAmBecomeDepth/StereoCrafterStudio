"""Attention processor that routes large spatial self-attention through SageAttention.

SageAttention's quantized kernel is only a win for long sequences: on the
shapes DepthCrafter produces (640x384, 110-frame window) it is ~4x faster than
SDPA for the 3840-token spatial self-attention, but *slower* for the 110-token
temporal attention and unsupported for head dims outside {64, 96, 128}. The
processor therefore dispatches per call and falls back to SDPA everywhere else.
"""

import logging
from typing import Optional

import torch
import torch.nn.functional as F

from diffusers.models.attention_processor import Attention

_logger = logging.getLogger(__name__)

try:
    from sageattention import sageattn

    SAGE_ATTENTION_AVAILABLE = True
except Exception as _e:  # a build that's there but broken (a DLL, Triton) too: PyTorch's SDPA
    if not isinstance(_e, ImportError):
        logging.getLogger(__name__).warning("SageAttention didn't load (%s); using PyTorch SDPA.", _e)
    sageattn = None
    SAGE_ATTENTION_AVAILABLE = False

# Below this sequence length the quantization overhead outweighs the kernel
# speedup (measured crossover on an RTX 5080 sits well under 512).
SAGE_MIN_SEQ_LEN = 512
_oom_said = False                 # the out-of-memory fallback's warning, once per run
_SAGE_HEAD_DIMS = (64, 96, 128)


def _attention(query, key, value, attention_mask):
    global sageattn
    if (
        sageattn is not None
        and attention_mask is None
        and query.is_cuda
        and query.dtype in (torch.float16, torch.bfloat16)
        and query.shape[-1] in _SAGE_HEAD_DIMS
        and query.shape[-2] >= SAGE_MIN_SEQ_LEN
        and query.shape[-2] == key.shape[-2]
    ):
        try:
            return sageattn(query, key, value, tensor_layout="HND", is_causal=False)
        except torch.OutOfMemoryError:
            # this call only: SDPA, Sage kept for the next (one OOM turned it off until Studio
            # restarted -- bug sweep 2026-10-14); said once, not per layer per step (sweep 2026-10-14b)
            global _oom_said
            if not _oom_said:
                _oom_said = True
                _logger.warning("SageAttention ran out of memory; PyTorch SDPA for the calls where it does.")
        except Exception as e:
            # An RTX 20 runs Sage through a Triton kernel compiled on the spot, and a GPU newer than
            # the package raises "Unsupported CUDA architecture": either way SDPA for the rest of the run.
            _logger.warning("SageAttention failed (%s); using PyTorch SDPA from here on.", e)
            sageattn = None
    return F.scaled_dot_product_attention(
        query, key, value, attn_mask=attention_mask, dropout_p=0.0, is_causal=False
    )


class SingleTokenCrossAttnProcessor:
    """Cross-attention onto a single key token, without the attention.

    DepthCrafter conditions every cross-attention on one CLIP embedding per
    frame (unet.py flattens it to [B*F, 1, 1024]; diffusers' temporal blocks
    reshape it to [B*H*W, 1, C]). With one key the softmax is exactly 1, so the
    output is to_out(to_v(context)) for every query: the query projection, the
    attention kernel and to_out over all tokens are wasted. This computes it
    once per row and broadcasts it. The same maths: the depth moved 0.09 of a
    10-bit code (GEMM rounding), denoise 5.5% faster (.claude/dc-speed/REPORT.md,
    2026-10-11). Anything else goes to the processor it wraps."""

    def __init__(self, inner):
        self.inner = inner

    def __call__(
        self,
        attn: Attention,
        hidden_states: torch.Tensor,
        encoder_hidden_states: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        temb: Optional[torch.Tensor] = None,
        *args,
        **kwargs,
    ) -> torch.Tensor:
        if (
            encoder_hidden_states is not None
            and encoder_hidden_states.ndim == 3
            and encoder_hidden_states.shape[1] == 1
            and attention_mask is None
            and hidden_states.ndim == 3
            and getattr(attn, "spatial_norm", None) is None
            and getattr(attn, "group_norm", None) is None
            and not getattr(attn, "norm_cross", False)
            and not attn.residual_connection
        ):
            value = attn.to_out[1](attn.to_out[0](attn.to_v(encoder_hidden_states)))
            return (value / attn.rescale_output_factor).expand(-1, hidden_states.shape[1], -1)
        return self.inner(attn, hidden_states, encoder_hidden_states, attention_mask, temb, *args, **kwargs)


def shortcut_single_token_cross_attention(model: torch.nn.Module) -> int:
    """Wrap the processor of every cross-attention (``attn2``) in ``model`` in
    SingleTokenCrossAttnProcessor. Call it after set_attn_processor, which
    would replace the wrappers. Returns how many were wrapped."""
    wrapped = 0
    for name, module in model.named_modules():
        if isinstance(module, Attention) and name.endswith("attn2"):
            if not isinstance(module.processor, SingleTokenCrossAttnProcessor):
                module.set_processor(SingleTokenCrossAttnProcessor(module.processor))
                wrapped += 1
    return wrapped


class SageAttnProcessor2_0:
    """Drop-in replacement for diffusers' AttnProcessor2_0 with SageAttention dispatch."""

    def __init__(self):
        if not SAGE_ATTENTION_AVAILABLE:
            raise ImportError("SageAttnProcessor2_0 requires the 'sageattention' package.")

    def __call__(
        self,
        attn: Attention,
        hidden_states: torch.Tensor,
        encoder_hidden_states: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        temb: Optional[torch.Tensor] = None,
        *args,
        **kwargs,
    ) -> torch.Tensor:
        residual = hidden_states
        if attn.spatial_norm is not None:
            hidden_states = attn.spatial_norm(hidden_states, temb)

        input_ndim = hidden_states.ndim

        if input_ndim == 4:
            batch_size, channel, height, width = hidden_states.shape
            hidden_states = hidden_states.view(batch_size, channel, height * width).transpose(1, 2)

        batch_size, sequence_length, _ = (
            hidden_states.shape if encoder_hidden_states is None else encoder_hidden_states.shape
        )

        if attention_mask is not None:
            attention_mask = attn.prepare_attention_mask(attention_mask, sequence_length, batch_size)
            attention_mask = attention_mask.view(batch_size, attn.heads, -1, attention_mask.shape[-1])

        if attn.group_norm is not None:
            hidden_states = attn.group_norm(hidden_states.transpose(1, 2)).transpose(1, 2)

        query = attn.to_q(hidden_states)

        if encoder_hidden_states is None:
            encoder_hidden_states = hidden_states
        elif attn.norm_cross:
            encoder_hidden_states = attn.norm_encoder_hidden_states(encoder_hidden_states)

        key = attn.to_k(encoder_hidden_states)
        value = attn.to_v(encoder_hidden_states)

        inner_dim = key.shape[-1]
        head_dim = inner_dim // attn.heads

        query = query.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        key = key.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        value = value.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)

        if attn.norm_q is not None:
            query = attn.norm_q(query)
        if attn.norm_k is not None:
            key = attn.norm_k(key)

        hidden_states = _attention(query, key, value, attention_mask)

        hidden_states = hidden_states.transpose(1, 2).reshape(batch_size, -1, attn.heads * head_dim)
        hidden_states = hidden_states.to(query.dtype)

        # linear proj
        hidden_states = attn.to_out[0](hidden_states)
        # dropout
        hidden_states = attn.to_out[1](hidden_states)

        if input_ndim == 4:
            hidden_states = hidden_states.transpose(-1, -2).reshape(batch_size, channel, height, width)

        if attn.residual_connection:
            hidden_states = hidden_states + residual

        hidden_states = hidden_states / attn.rescale_output_factor

        return hidden_states
