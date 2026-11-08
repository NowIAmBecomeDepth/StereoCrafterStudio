"""vae_trt.py -- DVD's Wan VAE (encode before the depth, decode after it) on NVIDIA TensorRT (user, 2026-11-07:
"can we try putting the vae on tensor" / "get the other chunks in too like we did for dc").

The VAE is ~83% of a DVD window (896x480, 81 frames: encode 8.9 s, the transformer 5.0, decode 15.4), so it is
what TensorRT speeds up; the transformer stays PyTorch (TensorRT there would save ~3%).

The Wan VAE runs chunk by chunk -- encode: 1 frame, then 4 at a time; decode: 1 latent frame at a time --
carrying a cache list from chunk to chunk. Its state differs for the first chunk (every slot None), the
second (one-frame tensors; the decoder's upsample slots 'Rep') and every later one (fixed-size tensors).
So each of encode and decode has three engines -- "first", "second", "steady" -- each exported from the
VAE's own eager code in that state, the cache's tensors as its inputs and outputs: EVERY chunk runs on
TensorRT, as DepthCrafter's every chunk does. The engines are fixed-size, one set per clip size (DVD's
run size), built the first time a size is met (about 10 minutes on a 5060 Ti) and kept in trt_engines/.
The window length doesn't matter: the chunks are the same at any length.

In the export (both found the hard way, 2026-11-07):
  * RMS_norm in fp32: F.normalize's sum of squares over 384 channels overflows fp16 in ONNX / TensorRT
    (eager PyTorch adds it up in fp32) -- the encoder's latents came out 75-90% wrong;
  * the 2x 'nearest-exact' upsample as 'nearest' (no ONNX op for it; the same pixels at 2x).
The rest is fp16, the build strongly typed so the fp32 norms stay fp32.

Measured (one 81-frame window of a 1920x1038 clip at 896x480, RTX 5060 Ti): encode 2.18x, decode 1.65x,
a window ~1.6x. The depth is not bit-identical: ~1.4 10-bit codes from PyTorch's on average, 4.7% of
pixels 4 codes or more (DepthCrafter's UNet engine: ~1.7). Hence a box, off by default.

Anything that fails -- TensorRT missing, an export, a build, an engine that doesn't take what it's given,
a run -- is said once in the log and the VAE runs the normal way for the rest of the job.
"""

from __future__ import annotations

import copy
import logging
import time
import types
from typing import Dict, List, Optional, Tuple

#: Bumped whenever the export changes: old engines are then not reused.
EXPORT_VERSION = 1
STAGES = ("first", "second", "steady")


def stage(i: int) -> str:
    """The engine a chunk runs on: its index's."""
    return STAGES[min(i, 2)]


def kinds(cache) -> List[Optional[str]]:
    """The cache's state: "T" for a tensor, else what the slot holds (None or 'Rep')."""
    import torch
    return ["T" if torch.is_tensor(c) else c for c in cache]


def enc_chunks(x):
    """x (1, 3, T, H, W) as VideoVAE_.encode slices it: the first frame, then 4 at a time."""
    return [x[:, :, :1]] + [x[:, :, 1 + 4 * (i - 1):1 + 4 * i] for i in range(1, 1 + (x.shape[2] - 1) // 4)]


def unavailable_reason() -> str:
    """"" when TensorRT can run here, else why not."""
    from pipelines.trt_unet import unavailable_reason as why
    return why()


def engine_name(part: str, st: str, width: int, height: int) -> str:
    """e.g. dvd_vae1_dec_steady_896x480_RTX5060Ti_trt11.4.0.106.engine"""
    from pipelines.trt_unet import _gpu_tag, _trt_version
    return f"dvd_vae{EXPORT_VERSION}_{part}_{st}_{width}x{height}_{_gpu_tag()}_trt{_trt_version()}.engine"


# ---- the export ----------------------------------------------------------------------------------------

def _step_class():
    import torch
    import torch.nn as nn

    class Step(nn.Module):
        """One chunk with the cache in a given state: (x, *its tensor slots) -> (out, *its tensor slots after)."""

        def __init__(self, net, pre):
            super().__init__()
            self.net, self.pre = net, list(pre)

        def forward(self, x, *tensors):
            it = iter(tensors)
            cache = [next(it) if k == "T" else k for k in self.pre]
            out = self.net(x, feat_cache=cache, feat_idx=[0])
            return (out, *[c for c in cache if torch.is_tensor(c)])
    return Step


def exportable(net, pre, dtype):
    """A copy of the encoder or decoder in `dtype` as a Step, its norms fp32 and its upsample ONNX's."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from dvd_depth.wan_video_vae import RMS_norm

    def norm32(self, x):
        y = F.normalize(x.float(), dim=(1 if self.channel_first else -1)) * self.scale * self.gamma.float()
        if torch.is_tensor(self.bias):
            y = y + self.bias.float()
        return y.to(x.dtype)

    step = _step_class()(copy.deepcopy(net).to(dtype), pre).eval()
    for mod in step.modules():
        if isinstance(mod, RMS_norm):
            mod.forward = types.MethodType(norm32, mod)
        elif isinstance(mod, nn.Upsample) and mod.mode == "nearest-exact":
            assert tuple(float(s) for s in mod.scale_factor) == (2.0, 2.0), mod.scale_factor
            mod.mode = "nearest"                # the same pixels at 2x
    return step


def _serialize(onnx_path, workspace: int) -> bytes:
    """Strongly typed (the fp32 norms stay fp32 on every TensorRT), DepthCrafter's timing cache."""
    import tensorrt as trt
    from pipelines import trt_unet as T
    tlog = T._trt_logger()
    builder = trt.Builder(tlog)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, tlog)
    if not parser.parse_from_file(str(onnx_path)):
        raise RuntimeError("TensorRT could not read the ONNX file: "
                           + "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace)
    cache_path = T.ENGINE_DIR / f"timing_{T._gpu_tag()}_trt{trt.__version__}.cache"
    cache = config.create_timing_cache(cache_path.read_bytes() if cache_path.exists() else b"")
    config.set_timing_cache(cache, ignore_mismatch=False)
    blob = builder.build_serialized_network(network, config)
    if blob is None:
        raise RuntimeError("TensorRT could not build the engine (see the lines above)")
    try:
        cache_path.write_bytes(memoryview(config.get_timing_cache().serialize()))
    except OSError:
        pass
    return bytes(memoryview(blob))


def build(name: str, net, pre, x_shape, shapes, dtype):
    """The engine file `name`, exported (from zeros of the chunk's and the cache's shapes: the values don't
    matter to the graph) and built when it isn't there (as .part, then renamed)."""
    import os
    import shutil
    import tempfile

    import torch
    from pipelines import trt_unet as T
    path = T.ENGINE_DIR / name
    with T._BUILD_LOCK:
        if path.exists():
            return path
        T.ENGINE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="onnx_", dir=T.ENGINE_DIR)
        try:
            onnx_path = os.path.join(tmp, "model.onnx")
            # out of DVD's inference mode: the exporter traces
            with torch.inference_mode(False), torch.no_grad(), T._quiet():
                step = exportable(net, pre, dtype)
                x = torch.zeros(x_shape, device="cuda", dtype=dtype)
                tensors = [torch.zeros(s, device="cuda", dtype=dtype) for s in shapes]
                n_out = len(step(x, *tensors))
                torch.onnx.export(step, (x, *tensors), onnx_path,
                                  input_names=["x"] + [f"c{i}" for i in range(len(tensors))],
                                  output_names=["out"] + [f"n{i}" for i in range(n_out - 1)],
                                  opset_version=17, do_constant_folding=True, dynamo=False)
            del step, x, tensors
            torch.cuda.empty_cache()
            first = T._workspace_bytes()
            try:
                blob = _serialize(onnx_path, first)
            except RuntimeError as e:
                more = T._workspace_bytes(capped=False)
                if "could not build" not in str(e) or more <= first:
                    raise
                blob = _serialize(onnx_path, more)
            part = path.with_name(path.name + ".part")
            part.write_bytes(blob)
            os.replace(part, path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return path


# ---- running -------------------------------------------------------------------------------------------

class VaeTensorRT:
    """DVD's VAE on TensorRT for one job: engines per run size, loaded (or built) the first time a size is
    met. encode / decode return None when they can't (the caller then runs the normal way)."""

    def __init__(self, model, log: logging.Logger, status=None):
        import torch
        from pipelines import trt_unet as T
        self.m, self.log = model, log
        self.status = status or (lambda text: None)     # the page's status line (a build takes minutes)
        self.dtype = torch.float16
        self.scratch = T.Scratch()
        T._TRT_OUT[0] = log                     # TensorRT's own errors in DVD's log
        self.sets: Dict[Tuple[int, int], dict] = {}     # (h, w) -> {"enc"|"dec": {stage: (Engine, post)}}
        self.failed = ""
        self.runs = {"enc": 0, "dec": 0}

    def _fail(self, what: str, e: Exception) -> None:
        if not self.failed:
            self.failed = f"{what}: {e}"
            self.log.warning("TensorRT for DVD's VAE is off for the rest of this job (%s: %s); the VAE runs the "
                             "normal way.", what, e)
        self.release()

    # -- the engines for a size

    def _record(self, h: int, w: int):
        """Each stage's cache state and its inputs' shapes, and the state after it, from the VAE's
        own eager code on a 9-frame stand-in -- three encoder chunks, three decoder latent frames."""
        import torch
        vm = self.m.vae.model
        dev, dt = self.m.device, self.m.dtype
        out = {}
        xx = None
        with torch.inference_mode():
            x = torch.zeros(1, 3, 9, h, w, device=dev, dtype=dt)
            z = torch.zeros(1, vm.z_dim, 3, h // 8, w // 8, device=dev, dtype=dt)
            for part, net, chunks in (("enc", vm.encoder, enc_chunks(x)),
                                      ("dec", vm.decoder, None)):
                vm.clear_cache()
                if part == "dec":
                    xx = vm.conv2(z)
                    chunks = [xx[:, :, i:i + 1] for i in range(3)]
                cache = vm._enc_feat_map if part == "enc" else vm._feat_map
                rec = {}
                for i, c in enumerate(chunks):
                    pre = kinds(cache)
                    ex = (tuple(c.shape), [tuple(t.shape) for t in cache if torch.is_tensor(t)])
                    net(c, feat_cache=cache, feat_idx=[0])
                    rec[STAGES[i]] = (pre, ex, kinds(cache))
                out[part] = rec
                del chunks, cache
            vm.clear_cache()
            x = z = xx = None
        torch.cuda.empty_cache()
        return out

    def ready(self, h: int, w: int) -> bool:
        """The engines for a run size loaded -- built first if they aren't on disk. False: run the normal way."""
        if self.failed:
            return False
        if (h, w) in self.sets:
            return True
        try:
            import torch
            from pipelines import trt_unet as T
            # the cache's states from a tiny stand-in (they don't depend on the size); the full-size one only for a
            # build -- it held ~3 GB of the card for nothing when the engines were already there (2026-11-07)
            rec = self._record(32, 32)
            names = {(p, st): engine_name(p, st, w, h) for p in ("enc", "dec") for st in STAGES}
            missing = [n for n in names.values() if not (T.ENGINE_DIR / n).exists()]
            if missing:
                self.log.info("TensorRT: building DVD's VAE engines for %dx%d (%d of 6; about 10 minutes, once for "
                              "this size -- kept in trt_engines)...", w, h, len(missing))
                self.status(f"Building TensorRT engines for DVD at {w}x{h} (about 10 minutes, once for this size)...")
                t0 = time.monotonic()
                was_on = self.m.dit_off()       # the room TensorRT's build wants
                torch.cuda.empty_cache()
                try:
                    full = self._record(h, w)
                    vm = self.m.vae.model
                    for (p, st), name in names.items():
                        pre, (x_shape, shapes), _ = full[p][st]
                        build(name, vm.encoder if p == "enc" else vm.decoder, pre, x_shape, shapes, self.dtype)
                    del full
                finally:
                    if was_on:
                        self.m.dit_on()
                self.log.info("TensorRT: DVD's VAE engines for %dx%d built in %.0f s.", w, h, time.monotonic() - t0)
            engines = {"enc": {}, "dec": {}}
            for (p, st), name in names.items():
                pre, _, post = rec[p][st]
                eng = T.Engine(T.ENGINE_DIR / name, self.scratch)
                if len(eng.inputs) != 1 + pre.count("T") or len(eng.outputs) != 1 + post.count("T"):
                    raise RuntimeError(f"{name} takes {len(eng.inputs)} inputs / gives {len(eng.outputs)} outputs, "
                                       f"not {1 + pre.count('T')} / {1 + post.count('T')}")
                engines[p][st] = (eng, post)
            self.sets[(h, w)] = engines
            return True
        except Exception as e:
            self.log.debug("DVD VAE TensorRT", exc_info=True)
            self._fail(f"the engines for {w}x{h}", e)
            return False

    # -- encode / decode, as VideoVAE_'s, every chunk on its stage's engine

    def _chunks(self, part: str, net_chunks, cache, h: int, w: int):
        import torch
        engines = self.sets[(h, w)][part]
        outs = []
        for i, c in enumerate(net_chunks):
            eng, post = engines[stage(i)]
            # the cache stays in the engines' fp16 from chunk to chunk: converting it to DVD's bf16 and back
            # each chunk held the decoder's full-size caches 3-4 times over -- a 16 GB card full and spilling
            # (a window 18 s alone, ~25 s inside a job; 2026-11-07)
            r = list(eng.run(c, *[t for t in cache if torch.is_tensor(t)]).values())
            outs.append(r[0].to(c.dtype))
            it = iter(r[1:])
            cache[:] = [next(it) if k == "T" else k for k in post]
            del r, it
        self.runs[part] += 1
        return torch.cat(outs, 2)

    def encode(self, x, scale):
        """VideoVAE_.encode's latents, or None."""
        h, w = x.shape[-2:]
        if not self.ready(h, w):
            return None
        vm = self.m.vae.model
        try:
            vm.clear_cache()
            out = self._chunks("enc", enc_chunks(x), vm._enc_feat_map, h, w)
            vm.clear_cache()
        except Exception as e:
            vm.clear_cache()
            self._fail("an encode", e)
            return None
        mu, _ = vm.conv1(out).chunk(2, dim=1)
        s = [v.to(mu.dtype) for v in scale]
        return (mu - s[0].view(1, vm.z_dim, 1, 1, 1)) * s[1].view(1, vm.z_dim, 1, 1, 1)

    def decode(self, z, scale, h: int, w: int):
        """VideoVAE_.decode's frames at h x w, or None."""
        if not self.ready(h, w):
            return None
        vm = self.m.vae.model
        s = [v.to(z.dtype) for v in scale]
        try:
            vm.clear_cache()
            xx = vm.conv2(z / s[1].view(1, vm.z_dim, 1, 1, 1) + s[0].view(1, vm.z_dim, 1, 1, 1))
            out = self._chunks("dec", [xx[:, :, i:i + 1] for i in range(xx.shape[2])], vm._feat_map, h, w)
            vm.clear_cache()
            return out
        except Exception as e:
            vm.clear_cache()
            self._fail("a decode", e)
            return None

    def summary(self) -> str:
        if self.failed:
            return f"TensorRT for DVD's VAE was turned off ({self.failed})."
        return (f"TensorRT for DVD's VAE: {self.runs['enc']} encodes and {self.runs['dec']} decodes on its engines "
                f"(every chunk).")

    def release(self) -> None:
        self.sets = {}
        self.scratch.release()
