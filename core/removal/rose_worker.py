"""rose_worker.py -- ROSE object removal, run INSIDE ROSE's own environment.

Studio never imports this: core.removal.erase starts it as a separate process
with weights/ROSE/.venv's Python (ROSE pins diffusers 0.31; Studio runs a
newer one). It imports nothing from Studio. One job file in, one cleaned
.npy per chunk out, progress on stdout:

    python rose_worker.py <job.json>

    job = {"rose_dir": ..., "embeds": ".../rose_empty_prompt.npy",
           "size": [h, w], "steps": 50, "seed": 42, "sage": true, "fast_ops": true, "tensorrt": true,
           "chunks": [{"frames": "c0_frames.npy", "masks": "c0_masks.npy", "out": "c0_clean.npy"}, ...]}

frames: (F, h, w, 3) uint8; masks: (F, h, w) uint8, nonzero = remove; F is
16n+1. out: (F, h, w, 3) uint8.

Sized for a 16 GB card, and for a small download (core.removal.rose_env):
  * the UMT5 text encoder never loads -- ROSE runs on an empty prompt, whose
    embedding ships with Studio (rose_empty_prompt.npy);
  * the CLIP image encoder never loads -- with no reference image the
    pipeline zeroes its output, so a stand-in returns those zeros;
  * model CPU offload: the transformer and the VAE take turns on the GPU;
  * SageAttention, and Triton kernels for its norms and RoPE (use_sage, use_fast_ops);
  * TensorRT for the transformer where ROSE's Python has it (rose_trt.py) -- one engine for every size,
    built the first time (minutes).

stdout lines: "SAGE on|off", "FASTOPS on|off", "TRT on|off|building", "CHUNK k K", "STEP s S", "DONE k",
"ERROR text".
"""

import json
import os
import sys
import time

import numpy as np


def say(*parts):
    print(*parts, flush=True)


def use_sage(wanted):
    """ROSE's self-attention through SageAttention (the same 2.2.0 Studio runs, built for this
    environment's torch 2.8 / cu128) when it is installed here; everything else, and everything
    after a failure, stays on ROSE's own attention (SDPA without flash-attn). Prints "SAGE on|off"."""
    import torch
    from rose.models import wan_transformer3d as wt
    try:
        from sageattention import sageattn
    except Exception as e:                       # a build that's there but broken (a DLL, Triton): ROSE's own
        if not isinstance(e, ImportError):
            print(f"SageAttention didn't load ({e}); ROSE's own attention.", file=sys.stderr, flush=True)
        sageattn = None
    if not wanted or sageattn is None:
        say("SAGE", "off")
        return
    plain = wt.attention
    state = {"ok": True}

    def attention(q, k, v, *a, **kw):           # q, k, v: (B, L, heads, head_dim)
        if (state["ok"] and not kw.get("causal") and q.shape[1] == k.shape[1] and q.shape[1] >= 512
                and q.shape[-1] in (64, 96, 128)):
            try:
                half = q.dtype if q.dtype in (torch.float16, torch.bfloat16) else torch.float16
                out = sageattn(q.to(half), k.to(half), v.to(half), tensor_layout="NHD", is_causal=False)
                return out.to(q.dtype).contiguous()
            except torch.OutOfMemoryError:
                torch.cuda.empty_cache()         # this call only: ROSE's own attention, Sage kept for the next
            except Exception as e:
                state["ok"] = False
                print(f"SageAttention failed ({e}); ROSE's own attention from here on.", file=sys.stderr, flush=True)
        return plain(q, k, v, *a, **kw)

    wt.attention = attention
    say("SAGE", "on")


def use_fast_ops(wanted):
    """ROSE's norms and RoPE without their fp32 / fp64 detours (user, 2026-10-31; a 768x432 step 4.7 s,
    these ~1.1 s of it), the maths otherwise unchanged:
      * WanLayerNorm: fp16 straight into layer_norm (it computes in fp32 inside) instead of an fp32 copy
        first, back to the input's type as ROSE's does -- the same values, bit for bit (under the
        pipeline's fp16 autocast layer_norm makes the fp32 copy itself and returns fp32: the .to keeps
        ROSE's fp16 result);
      * WanRMSNorm: one Triton kernel (fp32 inside, rounded to fp16 where ROSE rounds) -- ~1 value in
        20,000 a step lands one fp16 step away (another summing order);
      * rope_apply: one Triton kernel on cos / sin of ROSE's own fp64 table, in fp32 (ROSE multiplies in
        complex128) -- ~1 value in 14,000 one fp16 step away.
    Each takes only what it was checked for (CUDA fp16, the batch's clips one grid, no padding) and hands
    anything else to ROSE's own; a Triton failure puts all of it back on ROSE's own, said once.
    Prints "FASTOPS on|off"."""
    global tl                      # Triton reads a kernel's names from the module's globals
    import torch
    import torch.nn.functional as F
    from rose.models import wan_transformer3d as wt
    try:
        if not wanted:
            raise ImportError("not asked for")
        import triton
        import triton.language as tl
    except Exception as e:
        if not isinstance(e, ImportError):
            print(f"Triton didn't load ({e}); ROSE's own norms and RoPE.", file=sys.stderr, flush=True)
        say("FASTOPS", "off")
        return

    state = {"ok": True}

    def failed(what, e):
        state["ok"] = False
        print(f"The fast {what} failed ({type(e).__name__}: {e}); ROSE's own norms and RoPE from here on.",
              file=sys.stderr, flush=True)

    # ---- layer norm: no fp32 copy --------------------------------------------------------------------
    plain_ln = wt.WanLayerNorm.forward

    def layer_norm(self, x):
        if x.dtype == torch.float16 and x.is_cuda and (self.weight is None or self.weight.dtype == x.dtype):
            return F.layer_norm(x, self.normalized_shape, self.weight, self.bias, self.eps).to(x.dtype)
        return plain_ln(self, x)

    # ---- RMS norm ------------------------------------------------------------------------------------
    @triton.jit
    def _rms(X, W, Y, N, eps, BLOCK: tl.constexpr):
        row = tl.program_id(0).to(tl.int64)
        cols = tl.arange(0, BLOCK)
        m = cols < N
        xv = tl.load(X + row * N + cols, mask=m, other=0.0).to(tl.float32)
        r = 1.0 / tl.sqrt(tl.sum(xv * xv, axis=0) / N + eps)
        nv = (xv * r).to(tl.float16).to(tl.float32)            # _norm(x.float()).type_as(x) ...
        wv = tl.load(W + cols, mask=m, other=0.0).to(tl.float32)
        tl.store(Y + row * N + cols, (nv * wv).to(tl.float16), mask=m)   # ... * self.weight

    plain_rms = wt.WanRMSNorm.forward

    def rms_norm(self, x):
        n = x.shape[-1]
        if (state["ok"] and x.is_cuda and x.dtype == torch.float16 and self.weight.dtype == torch.float16
                and self.weight.numel() == n and n <= 8192):
            try:
                x2 = x.reshape(-1, n).contiguous()
                y = torch.empty_like(x2)
                if x2.shape[0]:
                    _rms[(x2.shape[0],)](x2, self.weight.contiguous(), y, n, float(self.eps),
                                         BLOCK=triton.next_power_of_2(n))
                return y.view(x.shape)
            except Exception as e:
                failed("RMS norm", e)
        return plain_rms(self, x)

    # ---- RoPE ----------------------------------------------------------------------------------------
    @triton.jit
    def _rope(X, COS, SIN, Y, NHEAD, C, LEN, BLOCK_C: tl.constexpr):
        row = tl.program_id(0).to(tl.int64)          # (b * LEN + l) * NHEAD + head
        pos = (row // NHEAD) % LEN
        cols = tl.arange(0, BLOCK_C)
        m = cols < C
        base = row * C * 2
        a = tl.load(X + base + cols * 2, mask=m, other=0.0).to(tl.float32)
        b = tl.load(X + base + cols * 2 + 1, mask=m, other=0.0).to(tl.float32)
        cs = tl.load(COS + pos * C + cols, mask=m, other=0.0)
        sn = tl.load(SIN + pos * C + cols, mask=m, other=0.0)
        tl.store(Y + base + cols * 2, (a * cs - b * sn).to(tl.float16), mask=m)
        tl.store(Y + base + cols * 2 + 1, (a * sn + b * cs).to(tl.float16), mask=m)

    plain_rope = wt.rope_apply
    table = {}

    def rope_table(grid, freqs, c):
        """cos / sin of rope_apply's own complex128 multipliers for one grid, as fp32 [f*h*w, c]."""
        key = (grid, freqs.data_ptr(), tuple(freqs.shape), c)
        if key not in table:
            table.clear()
            f, h, w = grid
            fs = freqs.split([c - 2 * (c // 3), c // 3, c // 3], dim=1)
            fi = torch.cat([fs[0][:f].view(f, 1, 1, -1).expand(f, h, w, -1),
                            fs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
                            fs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1)], dim=-1).reshape(f * h * w, -1)
            table[key] = (fi.real.float().contiguous(), fi.imag.float().contiguous())
        return table[key]

    def rope_apply(x, grid_sizes, freqs):
        # the fp16 result is what the attention casts rope_apply's fp32 one to
        if state["ok"] and x.is_cuda and x.dtype == torch.float16 and freqs.dtype == torch.complex128 \
                and freqs.is_cuda and x.dim() == 4 and x.size(3) % 2 == 0:
            grids = {tuple(g) for g in grid_sizes.tolist()}
            if len(grids) == 1:
                grid = grids.pop()
                if grid[0] * grid[1] * grid[2] == x.size(1):
                    try:
                        n, c = x.size(2), x.size(3) // 2
                        cos, sin = rope_table(grid, freqs, c)
                        xc = x.contiguous()
                        y = torch.empty_like(xc)
                        _rope[(x.size(0) * x.size(1) * n,)](xc, cos, sin, y, n, c, x.size(1),
                                                             BLOCK_C=triton.next_power_of_2(c))
                        return y
                    except Exception as e:
                        failed("RoPE", e)
        return plain_rope(x, grid_sizes, freqs)

    wt.WanLayerNorm._rose_forward, wt.WanRMSNorm._rose_forward = plain_ln, plain_rms   # rose_trt's export
    wt.WanLayerNorm.forward = layer_norm
    wt.WanRMSNorm.forward = rms_norm
    wt.rope_apply = rope_apply
    say("FASTOPS", "on")


def main(job_path):
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    rose_dir = job["rose_dir"]
    sys.path.insert(0, rose_dir)
    os.chdir(rose_dir)

    import warnings
    warnings.filterwarnings("ignore")
    import torch
    from diffusers import FlowMatchEulerDiscreteScheduler
    from omegaconf import OmegaConf
    from transformers import AutoTokenizer

    from rose.models import AutoencoderKLWan, WanTransformer3DModel
    from rose.pipeline import WanFunInpaintPipeline

    use_sage(job.get("sage", True))
    use_fast_ops(job.get("fast_ops", True))

    dtype = torch.float16
    base = os.path.join("models", "Wan2.1-Fun-1.3B-InP")
    config = OmegaConf.load(os.path.join("configs", "wan2.1", "wan_civitai.yaml"))

    class Embeds(list):
        """T5 embeddings as the pipeline keeps them (a tensor per prompt),
        with the .shape it sizes the batch by when no prompt string is given."""

        @property
        def shape(self):
            return (len(self),)

    class NoTextEncoder(torch.nn.Module):
        """UMT5's place: the pipeline reads .dtype; offload needs a parameter."""
        dtype = torch.float16

        def __init__(self):
            super().__init__()
            self._placeholder = torch.nn.Parameter(torch.zeros(1, dtype=torch.float16), requires_grad=False)

        def forward(self, *a, **k):
            raise RuntimeError("the text encoder was called although the embeddings were given")

    class ZeroClip(torch.nn.Module):
        """CLIP's place: with no reference image the pipeline zeroes CLIP's
        output (1 x 257 x 1280), so only its shape and dtype matter."""
        dtype = torch.float16

        def __init__(self):
            super().__init__()
            self._placeholder = torch.nn.Parameter(torch.zeros(1, dtype=torch.float16), requires_grad=False)

        def forward(self, images):
            return torch.zeros(len(images), 257, 1280, dtype=torch.float16, device=self._placeholder.device)

    def kwargs_for(cls, kwargs):
        import inspect
        ok = set(inspect.signature(cls.__init__).parameters) - {"self", "cls"}
        return {k: v for k, v in kwargs.items() if k in ok}

    tokenizer = AutoTokenizer.from_pretrained(os.path.join(base, config["text_encoder_kwargs"]["tokenizer_subpath"]))
    vae = AutoencoderKLWan.from_pretrained(
        os.path.join(base, config["vae_kwargs"]["vae_subpath"]),
        additional_kwargs=OmegaConf.to_container(config["vae_kwargs"]))
    transformer = WanTransformer3DModel.from_pretrained(
        os.path.join("weights", "transformer"),
        transformer_additional_kwargs=OmegaConf.to_container(config["transformer_additional_kwargs"]),
        # straight into fp16 on an empty model: without it 1.56 B random fp32 weights are made
        # first and overwritten (12.6 s of a 13 s load, 2026-10-13)
        low_cpu_mem_usage=True, torch_dtype=dtype)
    scheduler = FlowMatchEulerDiscreteScheduler(
        **kwargs_for(FlowMatchEulerDiscreteScheduler, OmegaConf.to_container(config["scheduler_kwargs"])))
    pipe = WanFunInpaintPipeline(vae=vae, text_encoder=NoTextEncoder(), tokenizer=tokenizer,
                                 transformer=transformer, scheduler=scheduler, clip_image_encoder=ZeroClip())
    pipe.to(dtype=dtype)
    pipe.enable_model_cpu_offload()
    # TensorRT for the transformer (rose_trt.py beside this file) where ROSE's Python has it: after the
    # offload, so the engine's calls never move the transformer's weights onto the GPU
    trt = None
    if job.get("tensorrt", True):
        try:
            import rose_trt
            trt = rose_trt.enable(transformer)
        except Exception as e:
            print(f"TensorRT didn't load ({type(e).__name__}: {e}); ROSE runs in PyTorch.", file=sys.stderr, flush=True)
            say("TRT", "off", "didn't load")
    else:
        say("TRT", "off", "not asked for")
    embeds = Embeds([torch.from_numpy(np.load(job["embeds"]).astype(np.float16)).to("cuda")])

    h, w = (int(v) for v in job["size"])
    steps = int(job.get("steps", 50))
    chunks = job["chunks"]

    def on_step(_pipe, i, _t, kw):
        say("STEP", i + 1, steps)
        return kw

    for k, chunk in enumerate(chunks):
        say("CHUNK", k + 1, len(chunks))
        frames = np.load(chunk["frames"])
        masks = np.load(chunk["masks"])
        n = frames.shape[0]
        video = torch.from_numpy(frames).permute(3, 0, 1, 2).unsqueeze(0).float() / 255.0      # 1,3,F,H,W
        mask = torch.from_numpy((masks > 0).astype(np.float32))[None, None]                      # 1,1,F,H,W
        t0 = time.time()
        with torch.no_grad():
            out = pipe(prompt=None, prompt_embeds=embeds, negative_prompt_embeds=embeds,
                       video=video, mask_video=mask, num_frames=n, height=h, width=w,
                       num_inference_steps=steps, callback_on_step_end=on_step,
                       guidance_scale=float(job.get("guidance", 6.0)),
                       generator=torch.Generator("cuda").manual_seed(int(job.get("seed", 42)))).videos
        clean = (out[0].permute(1, 2, 3, 0).float().clamp(0, 1).numpy() * 255 + 0.5).astype(np.uint8)
        np.save(chunk["out"] + ".partial.npy", clean)
        os.replace(chunk["out"] + ".partial.npy", chunk["out"])
        extra = [f"TensorRT {trt.calls[0]}/{sum(trt.calls)} steps"] if trt is not None else []
        say("DONE", k + 1, f"{time.time() - t0:.0f}s", f"peak {torch.cuda.max_memory_allocated() / 2**30:.1f}GB", *extra)
        if trt is not None:
            trt.calls[:] = [0, 0]


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:  # the parent shows this line; the traceback goes to stderr
        import traceback
        traceback.print_exc()
        say("ERROR", f"{e.__class__.__name__}: {e}".replace("\n", " "))
        sys.exit(1)
