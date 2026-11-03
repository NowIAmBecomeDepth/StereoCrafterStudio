"""minimax_worker.py -- MiniMax-Remover for Edge Fill, run as a child of Studio (minimax_env.py).

Studio starts it with its own Python, so the ~6 GB it takes of the card goes when it ends. It imports
nothing from Studio. The same job file and stdout lines as rose_worker.py:

    python minimax_worker.py <job.json>

    job = {"inpainter": "minimax", "model_dir": ".../MiniMax-Remover", "size": [h, w], "steps": 6,
           "seed": 42, "dilate": 6,
           "chunks": [{"frames": "c0_frames.npy", "masks": "c0_masks.npy", "out": "c0_clean.npy"}, ...]}

frames: (F, h, w, 3) uint8; masks: (F, h, w) uint8, nonzero = paint; F is 4n+1, h and w multiples of
16. out: (F, h, w, 3) uint8 -- the whole frame as MiniMax-Remover gives it back (Edge Fill takes the
strip and the band it fades over). stdout lines: "SAGE on|off", "CHUNK k K", "STEP s S", "DONE k ...",
"ERROR text".

Its transformer's attention runs through SageAttention where Studio has it (user, 2026-11-03: "lets try sage
attention"; prototypes/minimax_remover/speed.py, a 49-frame 384x784 window: the transformer 24.0 s -> 11.7 s,
the window 39.8 s -> 27.5 s, the strip 0.2 levels from SDPA's on average, its error against the real picture
9.66 -> 9.65). Its VAE keeps PyTorch's own attention.
"""

import json
import sys
import time
import traceback

import numpy as np


def say(*parts):
    print(*parts, flush=True)


def use_sage(module):
    """The transformer module's attention (its AttnProcessor2_0 calls F.scaled_dot_product_attention) through
    SageAttention; anything it can't take, and everything after a failure, through PyTorch's own. Prints
    "SAGE on|off"."""
    import torch.nn.functional as Fn
    try:
        from sageattention import sageattn
    except Exception as e:                       # not installed, or a broken build (a DLL, Triton)
        if not isinstance(e, ImportError):
            print(f"SageAttention didn't load ({e}); PyTorch's own attention.", file=sys.stderr, flush=True)
        say("SAGE", "off")
        return
    plain = Fn.scaled_dot_product_attention
    state = {"ok": True}

    class Functional:
        """torch.nn.functional for that module only, its attention Sage's"""
        def __getattr__(self, name):
            return getattr(Fn, name)

        @staticmethod
        def scaled_dot_product_attention(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False, **kw):
            if state["ok"] and attn_mask is None and not is_causal and q.shape[-1] in (64, 96, 128):
                import torch
                try:
                    return sageattn(q.half(), k.half(), v.half(), tensor_layout="HND", is_causal=False).to(q.dtype)
                except torch.OutOfMemoryError:
                    torch.cuda.empty_cache()     # this call only: PyTorch's own, Sage kept for the next
                except Exception as e:
                    state["ok"] = False
                    print(f"SageAttention failed ({e}); PyTorch's own attention from here on.",
                          file=sys.stderr, flush=True)
            return plain(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, **kw)

    module.F = Functional()
    say("SAGE", "on")


def main(job_path):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    sys.path.insert(0, job["model_dir"])        # the authors' two code files, beside the weights
    import torch
    from diffusers.models import AutoencoderKLWan
    from diffusers.schedulers import UniPCMultistepScheduler
    import transformer_minimax_remover
    from pipeline_minimax_remover import Minimax_Remover_Pipeline
    from transformer_minimax_remover import Transformer3DModel
    if job.get("sage", True):
        use_sage(transformer_minimax_remover)

    d = job["model_dir"]
    pipe = Minimax_Remover_Pipeline(
        transformer=Transformer3DModel.from_pretrained(f"{d}/transformer", torch_dtype=torch.float16),
        vae=AutoencoderKLWan.from_pretrained(f"{d}/vae", torch_dtype=torch.float16),
        scheduler=UniPCMultistepScheduler.from_pretrained(f"{d}/scheduler")).to("cuda:0")
    pipe.set_progress_bar_config(disable=True)
    steps = int(job.get("steps", 6))
    count = {"s": 0}
    step = pipe.scheduler.step

    def counted(*a, **k):                        # each denoising step, as ROSE's worker says it
        out = step(*a, **k)
        count["s"] += 1
        say("STEP", count["s"], steps)
        return out
    pipe.scheduler.step = counted

    chunks = job["chunks"]
    for k, c in enumerate(chunks, 1):
        say("CHUNK", k, len(chunks))
        t0 = time.time()
        frames = np.load(c["frames"])
        masks = np.load(c["masks"]) > 0
        n, h, w = masks.shape
        count["s"] = 0
        torch.cuda.reset_peak_memory_stats()
        with torch.no_grad():
            video = pipe(images=torch.from_numpy(frames).float() / 127.5 - 1.0,
                         masks=torch.from_numpy(masks.astype(np.float32))[..., None],
                         num_frames=n, height=h, width=w, num_inference_steps=steps,
                         generator=torch.Generator(device="cuda:0").manual_seed(int(job.get("seed", 42))),
                         iterations=int(job.get("dilate", 6))).frames[0]
        np.save(c["out"], (np.clip(video, 0.0, 1.0) * 255.0).round().astype(np.uint8))
        say("DONE", k, f"{time.time() - t0:.0f}s peak {torch.cuda.max_memory_allocated() / 2 ** 30:.1f}GB")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:                       # the runner shows the first line; the trace goes to stderr
        traceback.print_exc()
        say("ERROR", f"MiniMax-Remover failed: {type(e).__name__}: {e}".replace("\n", " "))
        sys.exit(1)
