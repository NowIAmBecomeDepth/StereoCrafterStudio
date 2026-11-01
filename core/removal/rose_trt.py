"""rose_trt.py -- TensorRT for ROSE's transformer (user, 2026-10-31), run INSIDE ROSE's own environment,
beside rose_worker.py (which imports it; Studio never does).

Erase's work sizes follow the clip's shape, its windows are any 4k+1 from 17 to 81 frames, Fill Edges runs
narrow strips. So the export is ROSE's forward written with every shape taken from the inputs (no padding,
one grid for the batch -- what ROSE's pipeline gives), and an engine takes a range of sizes: one per
size group (BUCKETS: 480p / 720p, wide / tall / square), built the first time a clip needs it. A group,
not one engine for all: TensorRT plans an engine's working memory for the largest size it takes, whatever
it's given -- one for up to 1280x1280 held 7.9 GB at every size and spilled a 16 GB card (2026-10-31).
In the export:
  * RoPE: its cos / sin are inputs, made outside from ROSE's own fp64 table (ONNX has no complex
    numbers); inside, the rotation is fp32 maths;
  * the timestep's sinusoid is an input too: ROSE makes it in fp64, which TensorRT doesn't have;
  * the text context comes in already padded to text_len;
  * attention is plain SDPA in the export: TensorRT fuses its own.
The build is strongly typed: every op keeps the type ROSE gives it (the norms in fp32, the rest fp16).
An engine whose weights and working memory don't fit beside the pipeline isn't used (PyTorch, said once).

A step at 49 frames 768x432 (RTX 5060 Ti, 2026-10-31): PyTorch with Sage + the fast ops 3.60 s, a
fixed-size engine 2.93 s; per step the engine is closer to ROSE's own maths than Sage is.

The transformer's weights stay off the GPU while the engine runs: its forward is replaced on the
instance, after model CPU offload, so offload's hook never moves it in. A call the engine can't take
(another shape of input, a size outside the profile) goes through the hook to PyTorch; a build or run
that fails puts every call back on PyTorch, said once.

stdout: "TRT on|off <why>", "TRT building" (before an export + build: minutes, once a GPU / TensorRT).
"""

import contextlib
import importlib
import math
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F

#: bump when the export changes: an engine of another version is never loaded
EXPORT_VERSION = 2
#: size groups: latent (height, width) at most (px / 8), and the size each is tuned for. Erase's work
#: sizes are about 480p / 576p / 720p in the clip's shape (erase.SIZES); frames 17-81 -> 5-21 latents,
#: batch 2 (guidance) or 1. A size in none of them runs in PyTorch.
BUCKETS = {
    "480w": ((64, 120), (56, 100)),      # up to 512x960: 480p wide (16:9 448x800, 2.39:1 384x912)
    "480t": ((120, 64), (100, 56)),
    "720w": ((96, 192), (90, 160)),      # up to 768x1536: 576p / 720p wide (2.39:1 at 720p 624x1488)
    "720t": ((192, 96), (160, 90)),
    "720s": ((128, 144), (104, 128)),    # up to 1024x1152: 4:3 (832x1104 at 720p) / square to 720p
}
FRAMES = (1, 21, 21)
BATCH = (1, 2, 2)
WORKSPACE = 8 << 30          # the build's workspace cap, on cards below SMALL_CARD only
SMALL_CARD = 14 << 30        # 8 and 12 GB cards
#: room kept free beside an engine's weights and working memory (the VAE, latents)
SPARE = 2 << 30


def say(*parts):
    print(*parts, flush=True)


def unavailable_reason() -> str:
    if not torch.cuda.is_available():
        return "no NVIDIA GPU"
    try:
        import tensorrt  # noqa: F401
    except Exception as e:
        return f"TensorRT isn't in ROSE's Python ({type(e).__name__})"
    return ""


def engine_dir() -> Path:
    d = os.environ.get("STEREOCRAFTER_ROSE_TRT")
    return Path(d) if d else Path(__file__).resolve().parents[2] / "trt_engines" / "rose"


def engine_path(bucket: str) -> Path:
    import tensorrt as trt
    gpu = "".join(c for c in torch.cuda.get_device_name(0).replace("NVIDIA ", "").replace("GeForce ", "")
                  if c.isalnum())
    return engine_dir() / f"rose_v{EXPORT_VERSION}_{bucket}_{gpu}_trt{trt.__version__}.engine"


def bucket_for(h: int, w: int):
    """The smallest size group taking latents h x w, or None."""
    fits = [(mh * mw, k) for k, ((mh, mw), _) in BUCKETS.items() if h <= mh and w <= mw]
    return min(fits)[1] if fits else None


# ---- the export ----------------------------------------------------------------------------------------

_ROPE = {}           # the export's cos / sin while it traces


def _rope_inputs(x, grid_sizes, freqs):
    """rope_apply as real maths on the cos / sin passed in: x [B, L, n, 2c] fp16 -> fp32, as rope_apply."""
    cos, sin = _ROPE["cos"], _ROPE["sin"]                 # [L, 1, c] fp32
    xr = x.float().unflatten(-1, (-1, 2))
    a, b = xr[..., 0], xr[..., 1]
    return torch.stack([a * cos - b * sin, a * sin + b * cos], dim=-1).flatten(3)


def _sdpa(q, k, v, *a, **kw):
    out = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2))
    return out.transpose(1, 2).contiguous()


def step_forward(model, x, y, sin_t, context, clip_fea):
    """WanTransformer3DModel.forward for one grid, no padding, no TeaCache, i2v -- every shape from the
    inputs. x, y: [B, C, F, H, W]; sin_t: [B, freq_dim] fp32 (sinusoidal_embedding_1d(t).float());
    context: [B, text_len, text_dim]; clip_fea: [B, 257, 1280]. RoPE reads _ROPE (or ROSE's own when
    the caller leaves rope_apply alone)."""
    from rose.models import wan_transformer3d as wt
    dtype = x.dtype
    x = model.patch_embedding(torch.cat([x, y], dim=1))         # [B, dim, f, h, w]
    b, _, f, h, w = x.shape
    x = x.flatten(2).transpose(1, 2)
    grid = torch.tensor([[int(f), int(h), int(w)]] * int(b), dtype=torch.long)      # read by ROSE's rope only
    seq_lens = torch.full((int(b),), int(f) * int(h) * int(w), dtype=torch.long)
    with torch.amp.autocast("cuda", dtype=torch.float32):
        e = model.time_embedding(sin_t)
        e0 = model.time_projection(e).unflatten(1, (6, model.dim))
        e0 = e0.to(dtype)
        e = e.to(dtype)
    context = model.text_embedding(context)
    context = torch.concat([model.img_emb(clip_fea), context], dim=1)
    for block in model.blocks:
        x = block(x, e=e0, seq_lens=seq_lens, grid_sizes=grid, freqs=model.freqs, context=context,
                  context_lens=None, dtype=dtype)
    x = model.head(x, e)
    p0, p1, p2 = model.patch_size
    c = model.out_dim
    x = x.reshape(b, f, h, w, p0, p1, p2, c)
    x = torch.einsum("bfhwpqrc->bcfphqwr", x)
    return x.reshape(b, c, f * p0, h * p1, w * p2)


class _Export(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, x, y, sin_t, context, clip_fea, cos, sin):
        _ROPE["cos"], _ROPE["sin"] = cos, sin
        return step_forward(self.model, x, y, sin_t, context, clip_fea)


@contextlib.contextmanager
def _export_ops(model):
    """rope_apply on the cos / sin inputs, attention as SDPA, the norms ROSE's own (rose_worker's Triton
    ones can't be traced: _rose_forward is where it keeps ROSE's); ROSE's complex128 table out of the model
    (passed to every block, unread here -- ONNX can't hold it)."""
    from rose.models import wan_transformer3d as wt
    keep = (wt.rope_apply, wt.attention, model.freqs, wt.WanLayerNorm.forward, wt.WanRMSNorm.forward)
    wt.rope_apply, wt.attention = _rope_inputs, _sdpa
    for cls in (wt.WanLayerNorm, wt.WanRMSNorm):
        cls.forward = getattr(cls, "_rose_forward", cls.forward)
    model.freqs = torch.zeros(1, device="cuda")
    try:
        yield
    finally:
        wt.rope_apply, wt.attention, model.freqs, wt.WanLayerNorm.forward, wt.WanRMSNorm.forward = keep
        _ROPE.clear()


@contextlib.contextmanager
def _without_onnx_package():
    """torch's TorchScript exporter imports onnx only to add onnxscript functions (none here): without
    onnx installed, that step passes the file through."""
    try:
        import onnx  # noqa: F401
        yield
        return
    except ImportError:
        pass
    saved = []
    for name in ("torch.onnx._internal.torchscript_exporter.onnx_proto_utils", "torch.onnx._internal.onnx_proto_utils"):
        try:
            m = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(m, "_add_onnxscript_fn"):
            saved.append((m, m._add_onnxscript_fn))
            m._add_onnxscript_fn = lambda model_bytes, custom_opsets: model_bytes
    try:
        yield
    finally:
        for m, fn in saved:
            m._add_onnxscript_fn = fn


def export_onnx(model, args, onnx_path):
    """The transformer as ONNX (weights as external data beside it), its sizes dynamic."""
    names = ["x", "y", "sin_t", "context", "clip_fea", "cos", "sin"]
    video = {0: "b", 2: "f", 3: "h", 4: "w"}
    dyn = {"x": video, "y": video, "sin_t": {0: "b"}, "context": {0: "b"}, "clip_fea": {0: "b"},
           "cos": {0: "l"}, "sin": {0: "l"}, "out": video}
    with torch.no_grad(), _export_ops(model), _without_onnx_package():
        torch.onnx.export(_Export(model), tuple(args), str(onnx_path), input_names=names, output_names=["out"],
                          dynamic_axes=dyn, opset_version=17, do_constant_folding=True, dynamo=False)


def profile_shapes(model, bucket, ctx_len, clip_len):
    """{input: (min, opt, max)} for a size group."""
    cin = model.patch_embedding.in_channels
    cx = model.out_dim
    (mh, mw), (oh, ow) = BUCKETS[bucket]
    ranges = {"b": BATCH, "f": FRAMES, "h": (2, oh, mh), "w": (2, ow, mw)}

    def shp(i):
        b, f, h, w = (ranges[k][i] for k in "bfhw")
        tokens = f * (h // model.patch_size[1]) * (w // model.patch_size[2])
        return {"x": (b, cx, f, h, w), "y": (b, cin - cx, f, h, w), "sin_t": (b, model.freq_dim),
                "context": (b, ctx_len, model.text_dim), "clip_fea": (b, clip_len, 1280),
                "cos": (tokens, 1, model.d // 2), "sin": (tokens, 1, model.d // 2)}
    lo, opt, hi = shp(0), shp(1), shp(2)
    return {k: (lo[k], opt[k], hi[k]) for k in lo}


# ---- TensorRT --------------------------------------------------------------------------------------------

class _Log:
    def __init__(self):
        import tensorrt as trt

        class L(trt.ILogger):
            def __init__(self):
                trt.ILogger.__init__(self)

            def log(self, severity, msg):
                if severity <= trt.ILogger.Severity.ERROR:
                    print(f"TensorRT: {msg}", file=sys.stderr, flush=True)
        self.logger = L()


_LOG = None


def _logger():
    global _LOG
    if _LOG is None:
        _LOG = _Log()
    return _LOG.logger


def build(onnx_path: Path, profile: dict, out: Path):
    """A strongly typed engine with one dynamic profile, written as .part then renamed."""
    import tensorrt as trt
    builder = trt.Builder(_logger())
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, _logger())
    if not parser.parse_from_file(str(onnx_path)):
        raise RuntimeError("TensorRT could not read the ONNX file: "
                           + "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    free, total = torch.cuda.mem_get_info()
    room = int(max(1 << 30, free - (1 << 30)))
    # capped on 8 and 12 GB cards only; 16, 24 and 32 GB cards get what they have free (user, 2026-10-31)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, min(room, WORKSPACE) if total < SMALL_CARD else room)
    p = builder.create_optimization_profile()
    for name, (lo, opt, hi) in profile.items():
        p.set_shape(name, lo, opt, hi)
    config.add_optimization_profile(p)
    blob = builder.build_serialized_network(network, config)
    if blob is None:
        raise RuntimeError("TensorRT could not build the engine")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    part.write_bytes(memoryview(blob))
    os.replace(part, out)


class Engine:
    """The engine, run on its own stream between the caller's; working memory sized for each call's shapes."""

    def __init__(self, path: Path):
        import tensorrt as trt
        self.trt = trt
        self.runtime = trt.Runtime(_logger())
        self.engine = self.runtime.deserialize_cuda_engine(path.read_bytes())
        if self.engine is None:
            raise RuntimeError(f"TensorRT could not load {path.name}")
        self.context = self.engine.create_execution_context(trt.ExecutionContextAllocationStrategy.USER_MANAGED)
        if self.context is None:
            raise RuntimeError(f"no execution context for {path.name} (out of GPU memory?)")
        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        self.inputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        self.outputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
        self.lo = {n: tuple(self.engine.get_tensor_profile_shape(n, 0)[0]) for n in self.inputs}
        self.hi = {n: tuple(self.engine.get_tensor_profile_shape(n, 0)[2]) for n in self.inputs}
        self.stream = torch.cuda.Stream()
        self.scratch = None
        #: working memory: planned for the group's largest size, whatever the call's (TensorRT 10)
        self.memory = int(self.engine.device_memory_size_v2)

    def fits(self, feeds: dict) -> bool:
        return all(len(feeds[n].shape) == len(self.lo[n])
                   and all(a <= s <= b for a, s, b in zip(self.lo[n], feeds[n].shape, self.hi[n])) for n in self.inputs)

    def _dtype(self, name):
        trt = self.trt
        return {trt.float16: torch.float16, trt.float32: torch.float32, trt.bfloat16: torch.bfloat16,
                trt.int32: torch.int32, trt.int64: torch.int64}[self.engine.get_tensor_dtype(name)]

    def __call__(self, feeds: dict) -> torch.Tensor:
        ctx = self.context
        keep = []
        for n in self.inputs:
            t = feeds[n].to(device="cuda", dtype=self._dtype(n)).contiguous()
            ctx.set_input_shape(n, tuple(t.shape))
            ctx.set_tensor_address(n, t.data_ptr())
            keep.append(t)
        outs = []
        for n in self.outputs:
            o = torch.empty(tuple(ctx.get_tensor_shape(n)), dtype=self._dtype(n), device="cuda")
            ctx.set_tensor_address(n, o.data_ptr())
            outs.append(o)
        need = int(ctx.update_device_memory_size_for_shapes())
        if self.scratch is None or self.scratch.numel() < need:
            self.scratch = None
            self.scratch = torch.empty(max(need, 1), dtype=torch.uint8, device="cuda")
        ctx.set_device_memory(self.scratch.data_ptr(), self.scratch.numel())
        current = torch.cuda.current_stream()
        self.stream.wait_stream(current)
        if not ctx.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("TensorRT could not run the engine")
        current.wait_stream(self.stream)
        return outs[0]

    def release(self):
        self.scratch = None


# ---- in the pipeline -------------------------------------------------------------------------------------

class RoseTensorRT:
    """The pipeline's transformer calls through the engine; PyTorch (through offload's hook) otherwise."""

    def __init__(self, transformer):
        from rose.models import wan_transformer3d as wt
        self.wt = wt
        self.model = transformer
        self.torch_forward = transformer.forward          # offload's hook around ROSE's forward
        self.engines = {}                                  # bucket -> Engine, or None: PyTorch for it
        self.failed = ""
        self.tables = {}
        self.calls = [0, 0]                                # [TensorRT, PyTorch]
        transformer.forward = self.forward

    def _say_failed(self, what, e):
        self.failed = f"{what}: {type(e).__name__}: {e}"
        self.release()
        self.engines.clear()
        torch.cuda.empty_cache()
        print(f"TensorRT {what} failed ({type(e).__name__}: {e}); ROSE goes on in PyTorch.", file=sys.stderr, flush=True)
        say("TRT", "off", what, "failed")

    def _rope(self, f, h, w):
        """cos / sin of rope_apply's own complex128 multipliers for this grid: fp32 [f*h*w, 1, c]."""
        key = (f, h, w)
        if key not in self.tables:
            self.tables.clear()
            freqs = self.model.freqs.to("cuda")
            c = self.model.d // 2
            fs = freqs.split([c - 2 * (c // 3), c // 3, c // 3], dim=1)
            fi = torch.cat([fs[0][:f].view(f, 1, 1, -1).expand(f, h, w, -1),
                            fs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
                            fs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1)], dim=-1).reshape(f * h * w, 1, c)
            self.tables[key] = (fi.real.float().contiguous(), fi.imag.float().contiguous())
        return self.tables[key]

    def feeds(self, x, t, context, seq_len, clip_fea=None, y=None, **kw):
        """The engine's inputs for this call, or None when it isn't one the export covers."""
        m = self.model
        if kw.get("cond_flag", True) is not True or kw.get("return_intermediate") or m.teacache is not None \
                or clip_fea is None or y is None:
            return None
        xs = torch.stack(list(x)) if isinstance(x, (list, tuple)) else x
        ys = torch.stack(list(y)) if isinstance(y, (list, tuple)) else y
        if xs.dim() != 5 or ys.shape[0] != xs.shape[0] or ys.shape[2:] != xs.shape[2:] or not xs.is_cuda \
                or xs.dtype != torch.float16:
            return None
        p = m.patch_size
        b, _, f, h, w = xs.shape
        if f % p[0] or h % p[1] or w % p[2]:
            return None
        gf, gh, gw = f // p[0], h // p[1], w // p[2]
        if gf * gh * gw != seq_len:                         # ROSE would pad: not in the export
            return None
        ctx = torch.stack([torch.cat([u, u.new_zeros(m.text_len - u.size(0), u.size(1))]) for u in context])
        sin_t = self.wt.sinusoidal_embedding_1d(m.freq_dim, t).float()
        cos, sin = self._rope(gf, gh, gw)
        return {"x": xs, "y": ys, "sin_t": sin_t, "context": ctx, "clip_fea": clip_fea, "cos": cos, "sin": sin}

    def _build(self, feeds, bucket):
        """Export at this call's shapes (the weights on the GPU for it), then build -- minutes, once."""
        path = engine_path(bucket)
        say("TRT", "building", bucket)
        t0 = time.time()
        tmp = path.parent / f"onnx_{os.getpid()}"
        tmp.mkdir(parents=True, exist_ok=True)
        m = self.model
        try:
            m.to("cuda")
            export_onnx(m, [feeds[k] for k in ("x", "y", "sin_t", "context", "clip_fea", "cos", "sin")],
                        tmp / "rose.onnx")
            m.to("cpu")
            torch.cuda.empty_cache()
            build(tmp / "rose.onnx", profile_shapes(m, bucket, feeds["context"].shape[1], feeds["clip_fea"].shape[1]),
                  path)
        finally:
            m.to("cpu")
            torch.cuda.empty_cache()
            for f in tmp.iterdir():
                with contextlib.suppress(OSError):
                    f.unlink()
            with contextlib.suppress(OSError):
                tmp.rmdir()
        print(f"TensorRT: ROSE's engine built in {time.time() - t0:.0f} s ({path.name}).", file=sys.stderr, flush=True)

    def forward(self, *a, **kw):
        if not self.failed and not a:
            try:
                feeds = self.feeds(**kw)
            except Exception:
                feeds = None
            bucket = bucket_for(feeds["x"].shape[3], feeds["x"].shape[4]) if feeds is not None else None
            if bucket is not None:
                try:
                    engine = self._engine(bucket, feeds)
                    if engine is not None and engine.fits(feeds):
                        with torch.no_grad():
                            out = engine(feeds)
                        self.calls[0] += 1
                        return out
                except Exception as e:
                    self._say_failed("engine", e)
        self.calls[1] += 1
        return self.torch_forward(*a, **kw)

    def _engine(self, bucket, feeds):
        """The size group's engine (built the first time), or None when it doesn't fit beside the pipeline
        -- PyTorch for that group, said once."""
        if bucket in self.engines:
            return self.engines[bucket]
        # one group's engine on the GPU at a time (a job runs one size; Erase's refine pass another)
        self.engines = {k: v for k, v in self.engines.items() if v is None}
        torch.cuda.empty_cache()
        path = engine_path(bucket)
        if not path.exists():
            self._build(feeds, bucket)
        engine = Engine(path)
        free, _ = torch.cuda.mem_get_info()
        if engine.memory + SPARE > free:
            print(f"TensorRT: ROSE's {bucket} engine needs {(engine.memory + SPARE) / 2**30:.1f} GB with room for "
                  f"the rest, {free / 2**30:.1f} GB free -- this size runs in PyTorch.", file=sys.stderr, flush=True)
            say("TRT", "off", bucket, "no room")
            del engine
            torch.cuda.empty_cache()
            self.engines[bucket] = None
            return None
        say("TRT", "on", bucket)
        self.engines[bucket] = engine
        return engine

    def release(self):
        for e in self.engines.values():
            if e is not None:
                e.release()


def enable(transformer):
    """TensorRT for the transformer, or None (said why). Call after enable_model_cpu_offload."""
    why = unavailable_reason()
    if why:
        say("TRT", "off", why)
        return None
    return RoseTensorRT(transformer)
