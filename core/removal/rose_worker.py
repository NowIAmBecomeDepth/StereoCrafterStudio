"""rose_worker.py -- ROSE object removal, run INSIDE ROSE's own environment.

Studio never imports this: core.removal.erase starts it as a separate process
with weights/ROSE/.venv's Python (ROSE pins diffusers 0.31; Studio runs a
newer one). It imports nothing from Studio. One job file in, one cleaned
.npy per chunk out, progress on stdout:

    python rose_worker.py <job.json>

    job = {"rose_dir": ..., "embeds": ".../rose_empty_prompt.npy",
           "size": [h, w], "steps": 50, "seed": 42,
           "chunks": [{"frames": "c0_frames.npy", "masks": "c0_masks.npy", "out": "c0_clean.npy"}, ...]}

frames: (F, h, w, 3) uint8; masks: (F, h, w) uint8, nonzero = remove; F is
16n+1. out: (F, h, w, 3) uint8.

Sized for a 16 GB card, and for a small download (core.removal.rose_env):
  * the UMT5 text encoder never loads -- ROSE runs on an empty prompt, whose
    embedding ships with Studio (rose_empty_prompt.npy);
  * the CLIP image encoder never loads -- with no reference image the
    pipeline zeroes its output, so a stand-in returns those zeros;
  * model CPU offload: the transformer and the VAE take turns on the GPU.

stdout lines: "CHUNK k K", "STEP s S", "DONE k", "ERROR text".
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
        say("DONE", k + 1, f"{time.time() - t0:.0f}s", f"peak {torch.cuda.max_memory_allocated() / 2**30:.1f}GB")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:  # the parent shows this line; the traceback goes to stderr
        import traceback
        traceback.print_exc()
        say("ERROR", f"{e.__class__.__name__}: {e}".replace("\n", " "))
        sys.exit(1)
