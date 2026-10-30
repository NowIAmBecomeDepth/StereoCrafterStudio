"""TensorRT for Inpainting V1 (a user's patch, 2026-10-29): the UNet, the VAE encoder and the VAE decoder
as NVIDIA TensorRT FP16 engines. Optional, off by default: Inpainting's TensorRT box.

An engine is built the first time a shape is met (UNet ~3 min, VAE decoder ~2, encoder ~1) and kept in
trt_engines/ beside Studio, named for the network, the shape, the GPU and the TensorRT version, so a new
card or TensorRT builds fresh ones. Not bit-identical to PyTorch: about 0.2 of a level on average, at hole
edges (the user's measurements, 2026-10-29).

The pipeline calls these at its own call sites (StableVideoDiffusionInpaintingPipeline.tensorrt), not by
replacing unet.forward: diffusers puts model CPU offload back after every tile (maybe_free_model_hooks),
which restores the original forward. Whatever doesn't fit an engine -- a one-frame chunk, the odd
last frame of a decode, the fp32 encode after an fp16 overflow -- runs through PyTorch, and so does a
network that failed once (TensorRT missing, an export, build or run failing): it is said once in the log
and that network is PyTorch for the rest of the run."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import torch

log = logging.getLogger("inpainting.v2.tensorrt")  # a child of Inpainting's logger: reaches its log file

_REPO = Path(__file__).resolve().parents[1]
#: Where engines live; STEREOCRAFTER_TRT_ENGINES moves them (the tests' temp folder).
ENGINE_DIR = Path(os.environ.get("STEREOCRAFTER_TRT_ENGINES") or _REPO / "trt_engines")
WORKSPACE_BYTES = 8 << 30
_BUILD_LOCK = threading.Lock()
#: Below this much free GPU memory a run makes room (an 8 GB card, 2026-10-30): the Scratch shrinks to the
#: engine asking, and the UNet engine is unloaded for the decode, reloaded for the next chunk (~2 s).
LOW_VRAM = 2 << 30
SHRINK_SPARE = 512 << 20


def _hand_back_cache():
    """On a card short of memory, what PyTorch holds cached but unused (CLIP's encode, a tile's set-up:
    ~1.3 GB) goes back to the card before an engine runs: TensorRT can't use PyTorch's cache, and beside
    the UNet engine it left an 8 GB card with nothing free (2026-10-30)."""
    cached = torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
    if cached > SHRINK_SPARE // 2 and torch.cuda.mem_get_info()[0] < LOW_VRAM:
        torch.cuda.empty_cache()


def free_vram() -> int:
    """What the card has free, PyTorch's cached but unused memory counted in."""
    free, _ = torch.cuda.mem_get_info()
    return free + torch.cuda.memory_reserved() - torch.cuda.memory_allocated()


def unavailable_reason() -> str:
    """"" when TensorRT can run here, else why not (said in the log, and Inpainting runs the normal way)."""
    if not torch.cuda.is_available():
        return "no NVIDIA GPU"
    for module in ("tensorrt", "onnx", "onnxscript"):
        try:
            __import__(module)
        except Exception:
            return f"'{module}' is not installed (Settings > Studio > Models: NVIDIA TensorRT, Install)"
    return ""


def _gpu_tag() -> str:
    name = torch.cuda.get_device_name(0).replace("NVIDIA", "").replace("GeForce", "")
    return re.sub(r"[^A-Za-z0-9]+", "", name) or "gpu"


def engine_name(kind: str, width: int, height: int, n, channels: int, model: str = "svd") -> str:
    """e.g. svd_unet_984x448_f25_c8_RTX5060Ti_trt11.4.0.106.engine (n: frames for the UNet, batch for the
    VAE; a range such as "2-120" for an engine that takes any of them); model: svd (Inpainting) or dc."""
    tag = {"unet": "f", "vaedec": "n", "vaeenc": "n"}[kind]
    return f"{model}_{kind}_{width}x{height}_{tag}{n}_c{channels}_{_gpu_tag()}_trt{_trt_version()}.engine"


def _trt_version() -> str:
    import tensorrt as trt
    return trt.__version__


@contextlib.contextmanager
def _quiet():
    """The ONNX exporter prints emoji the Windows console can't encode (and a page of progress), and its
    optimiser logs every node it touches -- thousands of lines a build, 600 KB of a user's log (2026-10-30)."""
    loggers = [logging.getLogger(name) for name in ("onnxscript", "onnx_ir", "torch.onnx")]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.WARNING)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        for lg, level in zip(loggers, levels):
            lg.setLevel(level)


_TRT_LOGGER = None


def _trt_logger():
    """One for the process: TensorRT keeps the first it is given and warns about every other."""
    global _TRT_LOGGER
    if _TRT_LOGGER is None:
        import tensorrt as trt
        _TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
    return _TRT_LOGGER


def _torch_dtype(trt, dt):
    types = {trt.DataType.HALF: torch.float16, trt.DataType.FLOAT: torch.float32, trt.DataType.INT32: torch.int32,
             trt.DataType.INT64: torch.int64, trt.DataType.BOOL: torch.bool}
    if hasattr(trt.DataType, "BF16"):
        types[trt.DataType.BF16] = torch.bfloat16
    return types[dt]


def _workspace_bytes() -> int:
    """8 GB, or what the card has free less 1 GB (at least 1 GB): a workspace past the card's free memory
    spills into shared memory on Windows, or fails the build (a 12 GB card, 2026-10-30)."""
    free, _ = torch.cuda.mem_get_info()
    return int(min(WORKSPACE_BYTES, max(1 << 30, free - (1 << 30))))


def _serialize(onnx_path: Path, profile=None) -> bytes:
    """An ONNX file as a TensorRT FP16 engine: strongly typed where TensorRT has no FP16 flag (11), else
    the flag; a workspace of up to 8 GB (_workspace_bytes) and a timing cache shared by every build on this GPU. profile: {input name:
    (min shape, opt shape, max shape)} for the inputs whose size varies (DepthCrafter's window length)."""
    import tensorrt as trt
    tlog = _trt_logger()
    builder = trt.Builder(tlog)
    strongly_typed = not hasattr(trt.BuilderFlag, "FP16")
    flags = 1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED) if strongly_typed else 0
    network = builder.create_network(flags)
    parser = trt.OnnxParser(network, tlog)
    if not parser.parse_from_file(str(onnx_path)):
        raise RuntimeError("TensorRT could not read the ONNX file: "
                           + "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, _workspace_bytes())
    if not strongly_typed:
        config.set_flag(trt.BuilderFlag.FP16)
    if profile:
        p = builder.create_optimization_profile()
        for name, (lo, opt, hi) in profile.items():
            p.set_shape(name, lo, opt, hi)
        config.add_optimization_profile(p)
    cache_path = ENGINE_DIR / f"timing_{_gpu_tag()}_trt{trt.__version__}.cache"
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


def build_engine(name: str, export, release=None, profile=None) -> Path:
    """The engine file `name`, built when it isn't there: export(onnx_path) writes the ONNX, release()
    then frees what the export needed on the GPU before TensorRT takes its workspace. Written as .part,
    then renamed: a build cut short never leaves a file that looks finished."""
    path = ENGINE_DIR / name
    with _BUILD_LOCK:
        if path.exists():
            return path
        ENGINE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="onnx_", dir=ENGINE_DIR))
        try:
            onnx_path = tmp / "model.onnx"
            try:
                with _quiet():
                    export(onnx_path)
            finally:
                if release is not None:
                    release()
            torch.cuda.empty_cache()
            blob = _serialize(onnx_path, profile)
            part = path.with_name(path.name + ".part")
            part.write_bytes(blob)
            os.replace(part, path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return path


class Scratch:
    """The working memory of every engine of a run, one buffer grown to the largest they ask: only one
    runs at a time. A buffer per call from PyTorch's pool, marked as used on TensorRT's stream, couldn't
    be reused until the allocator caught up -- two copies of each engine's, ~10 GB on a 16 GB card, and
    the decode spilled into shared memory at a third of PyTorch's speed (2026-10-29)."""

    def __init__(self):
        self.buffer = None

    def get(self, size: int) -> int:
        """The address of at least `size` bytes. Freeing a smaller one is safe: the caller's stream has
        waited for every engine call before it, and only that stream reuses it."""
        held = 0 if self.buffer is None else self.buffer.numel()
        # A card short of memory gets the decoder's 2.8 GB back while the UNet asks 1.2: kept, the
        # denoise on an 8 GB card spilled into shared memory (2026-10-30). Room to spare: kept as it is.
        shrink = held - size > SHRINK_SPARE and free_vram() < LOW_VRAM
        if held < size or shrink:
            self.buffer = None
            # what PyTorch cached meanwhile goes back too: a chunk's odd last frame decodes in PyTorch,
            # and its 4 GB stayed cached beside the next chunk's engines (0.26 GB left on 10.5)
            torch.cuda.empty_cache()
            self.buffer = torch.empty(size, dtype=torch.uint8, device="cuda")
        return self.buffer.data_ptr()

    def release(self):
        """Handed back to the card, not kept in PyTorch's pool: there the next chunk's UNet took part of
        the decoder's freed block and the decoder then took a second whole one -- 2.8 GB twice over, a
        12 GB card full and spilling into shared memory (a user's 4070 SUPER, 2026-10-30)."""
        if self.buffer is not None:
            self.buffer = None
            torch.cuda.empty_cache()


class Engine:
    """One deserialised engine and its execution context, run on its own CUDA stream between the
    caller's: the inputs are waited for, the outputs are waited on before it returns -- so whatever the
    caller frees afterwards is only reused by work queued behind that wait, and nothing needs
    record_stream. Its working memory comes from a Scratch shared with the run's other engines."""

    def __init__(self, path: Path, scratch: Scratch | None = None):
        self.pool = scratch or Scratch()
        import tensorrt as trt
        self._trt = trt
        self.runtime = trt.Runtime(_trt_logger())
        self.engine = self.runtime.deserialize_cuda_engine(Path(path).read_bytes())
        if self.engine is None:
            raise RuntimeError(f"TensorRT could not load {Path(path).name}")
        strategy = getattr(trt, "ExecutionContextAllocationStrategy", None)
        self.scratch = 0
        if strategy is not None and hasattr(self.engine, "device_memory_size_v2"):
            self.context = self.engine.create_execution_context(strategy.USER_MANAGED)
            self.scratch = int(self.engine.device_memory_size_v2)
        else:
            self.context = self.engine.create_execution_context()
        if self.context is None:   # Thomas's Patch 5 (2026-10-30): said plainly; the caller falls back to PyTorch
            raise RuntimeError(f"no execution context for {Path(path).name} (out of GPU memory?)")
        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        self.inputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        self.outputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
        self.stream = torch.cuda.Stream()

    def __call__(self, *args: torch.Tensor) -> torch.Tensor:
        """The inputs in the engine's order; its first output."""
        trt, ctx, engine = self._trt, self.context, self.engine
        current = torch.cuda.current_stream()
        feeds = []
        for name, t in zip(self.inputs, args):
            t = t.to(device="cuda", dtype=_torch_dtype(trt, engine.get_tensor_dtype(name))).contiguous()
            ctx.set_input_shape(name, tuple(t.shape))
            ctx.set_tensor_address(name, t.data_ptr())
            feeds.append(t)
        outs = []
        for name in self.outputs:
            o = torch.empty(tuple(ctx.get_tensor_shape(name)), dtype=_torch_dtype(trt, engine.get_tensor_dtype(name)),
                            device="cuda")
            ctx.set_tensor_address(name, o.data_ptr())
            outs.append(o)
        if self.scratch:
            address = self.pool.get(self.scratch)
            if hasattr(ctx, "set_device_memory"):
                ctx.set_device_memory(address, self.scratch)
            else:
                ctx.device_memory = address
        self.stream.wait_stream(current)
        if not ctx.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("TensorRT could not run the engine")
        current.wait_stream(self.stream)
        return outs[0]


class _UNetExport(torch.nn.Module):
    """The UNet's own forward, not the offload hook's or anything set on the instance."""

    def __init__(self, unet):
        super().__init__()
        self.unet = unet

    def forward(self, sample, timestep, encoder_hidden_states, added_time_ids):
        return type(self.unet).forward(self.unet, sample, timestep, encoder_hidden_states, added_time_ids,
                                       return_dict=False)[0]


class _DecoderExport(torch.nn.Module):
    def __init__(self, vae, num_frames):
        super().__init__()
        self.decoder, self.num_frames = vae.decoder, num_frames

    def forward(self, z):
        indicator = torch.zeros(z.shape[0] // self.num_frames, self.num_frames, dtype=z.dtype, device=z.device)
        return self.decoder(z, num_frames=self.num_frames, image_only_indicator=indicator)


class _EncoderExport(torch.nn.Module):
    def __init__(self, vae):
        super().__init__()
        self.encoder, self.quant_conv = vae.encoder, vae.quant_conv

    def forward(self, x):
        return self.quant_conv(self.encoder(x))


def _export(module, args, names, onnx_path, dynamic_shapes=None):
    torch.onnx.export(module, args, str(onnx_path), input_names=names, output_names=["out"], dynamo=True,
                      external_data=True, optimize=True, verbose=False, dynamic_shapes=dynamic_shapes)


@contextlib.contextmanager
def exportable(unet):
    """The UNet as the exporter can trace it, put back as it was after: SageAttention's processors as
    plain SDPA (Sage can't be exported; TensorRT fuses its own attention), and DepthCrafter's feed-forward
    slicing (chunk_feed_forward: a per-instance forward splitting by the frame count) taken off -- the
    engine plans its own memory. The single-token cross-attention shortcut stays: it exports, and saves
    the engine that work too."""
    from diffusers.models.attention import FeedForward
    from diffusers.models.attention_processor import Attention, AttnProcessor2_0
    swapped, unsliced = [], []
    for module in unet.modules():
        if isinstance(module, Attention):
            proc = module.processor
            inner = getattr(proc, "inner", None) or getattr(proc, "processor", None)
            if "Sage" in type(proc).__name__:
                swapped.append((module, proc))
                module.set_processor(AttnProcessor2_0())
            elif inner is not None and "Sage" in type(inner).__name__:  # the shortcut around Sage: kept
                swapped.append((module, proc))
                module.set_processor(type(proc)(AttnProcessor2_0()))
        elif isinstance(module, FeedForward) and "forward" in module.__dict__:
            unsliced.append((module, module.__dict__.pop("forward")))
    try:
        yield unet
    finally:
        for module, proc in swapped:
            module.set_processor(proc)
        for module, fwd in unsliced:
            module.forward = fwd


class _Net:
    def __init__(self, label: str):
        self.label, self.engines, self.off, self.trt_calls, self.torch_calls = label, {}, False, 0, 0


class TensorRT:
    """What the pipeline calls instead of its UNet and VAE while Inpainting's TensorRT box is on."""

    def __init__(self, pipe, frames_chunk: int, use_sageattention: bool, offload_type: str, logger=None):
        self.pipe, self.frames_chunk = pipe, int(frames_chunk)
        self.log = logger or log  # the stage's own: DepthCrafter never configures Inpainting's
        self.use_sageattention, self.offload_type = bool(use_sageattention), offload_type
        self.nets = {"unet": _Net("UNet steps"), "vaeenc": _Net("VAE encodes"), "vaedec": _Net("VAE decodes")}
        self.scratch = Scratch()
        # The first decode's side-by-side with PyTorch is off (Thomas's Patch 5, 2026-10-30): both decodes held
        # at once spiked VRAM -- out of memory on a fuller card -- and the difference is measured already
        # (HANDOFF: the in-hole mean 0.38 of 255)
        self._checked_decoder = True
        self._said_unload = False

    # ---- the networks, through TensorRT where they fit --------------------------------------------------

    def _engine(self, kind, key, name, build):
        """The engine for this shape, built on first use; None once this network has failed."""
        net = self.nets[kind]
        if net.off:
            return None
        if key not in net.engines:
            try:
                path = ENGINE_DIR / name
                if not path.exists():
                    self.log.info(f"TensorRT: building the {kind_label(kind)} engine {name} "
                             "(once for this size; a few minutes, the job waits)...")
                    t0 = time.monotonic()
                    build(name)
                    self.log.info(f"TensorRT: {name} engine built in {time.monotonic() - t0:.0f} s.")
                net.engines[key] = Engine(path, self.scratch)
            except Exception as e:
                self._fail(kind, e)
                return None
        return net.engines[key]

    def _fail(self, kind, e):
        net = self.nets[kind]
        net.off = True
        net.engines.clear()
        self.log.warning(f"TensorRT: the {kind_label(kind)} failed ({type(e).__name__}: {e}); it runs in PyTorch "
                    "for the rest of this run.")

    def unet(self, sample, timestep, encoder_hidden_states=None, added_time_ids=None, return_dict=True, **kw):
        engine = None
        # Any chunk of 2 to Frames Chunk frames (2026-10-30): an engine for exactly Frames Chunk left a
        # clip's shorter last chunk in PyTorch -- half of a 41-frame clip at Frames Chunk 25
        if (not kw and sample.dim() == 5 and sample.shape[0] == 1 and 2 <= sample.shape[1] <= self.frames_chunk
                and sample.dtype == torch.float16 and encoder_hidden_states is not None
                and added_time_ids is not None and encoder_hidden_states.shape[0] == 1
                and tuple(added_time_ids.shape) == (1, 3)):
            _, f, c, h, w = sample.shape
            engine = self._engine("unet", (c, h, w, tuple(encoder_hidden_states.shape)),
                                  engine_name("unet", w * 8, h * 8, f"2-{self.frames_chunk}", c),
                                  lambda name: self._build_unet(name, sample, encoder_hidden_states, added_time_ids))
        if engine is not None:
            try:
                self._park_torch_unet()
                t = torch.as_tensor(timestep, dtype=torch.float32, device=sample.device).reshape(1)
                out = engine(sample, t, encoder_hidden_states, added_time_ids)
                self.nets["unet"].trt_calls += 1
                if not return_dict:
                    return (out,)
                from diffusers.models.unets.unet_spatio_temporal_condition import UNetSpatioTemporalConditionOutput
                return UNetSpatioTemporalConditionOutput(sample=out)
            except Exception as e:
                self._fail("unet", e)
        self.nets["unet"].torch_calls += 1
        self._torch_unet_room()
        self._unpark_torch_unet()
        return self.pipe.unet(sample, timestep, encoder_hidden_states=encoder_hidden_states,
                              added_time_ids=added_time_ids, return_dict=return_dict, **kw)

    def vae_decode(self, z, num_frames=None, return_dict=True):
        engine = None
        if num_frames == 2 and z.dim() == 4 and z.shape[0] == 2 and z.dtype == torch.float16:
            _, c, h, w = z.shape
            engine = self._engine("vaedec", (c, h, w), engine_name("vaedec", w * 8, h * 8, 2, c),
                                  lambda name: self._build_decoder(name, z))
        return self._decode_with(engine, z, num_frames, return_dict)

    def _torch_unet_room(self):
        """A step the engine can't take (one frame, not fp16) brings the PyTorch UNet onto the GPU:
        the engine's 2.8 GB goes first, loaded again by the next full chunk (~2 s). Both at once filled a
        12 GB card on a 41-frame clip's 21-frame chunk: 24.5 s a step instead of 0.6 (2026-10-30)."""
        self.nets["unet"].engines.clear()
        self.scratch.release()  # PyTorch needs the room (DC's UNet buffer is 7.7 GB)

    def idle(self):
        """Work that isn't TensorRT's next (the Hi-Res blend: ~7 GB of PyTorch at 2580x1080): its engines
        and working memory back to the card, loaded again by the next clip's first call (~2 s). Held
        through the blend, the UNet engine's 2.8 GB filled a 12 GB card (2026-10-30)."""
        for net in self.nets.values():
            net.engines.clear()
        self.scratch.release()

    def _room_for_decode(self, engine):
        """The UNet engine (2.8 GB) waits idle through the decode: on a card without room for both it is
        unloaded here and loaded again by the next chunk's first step (~2 s). An 8 GB card filled to the
        last MB and spilled into shared memory, chunk times doubling (2026-10-30)."""
        unet = self.nets["unet"]
        held = 0 if self.scratch.buffer is None else self.scratch.buffer.numel()
        if unet.engines and free_vram() + held < getattr(engine, "scratch", 0) + LOW_VRAM:
            unet.engines.clear()
            self.scratch.release()
            if not self._said_unload:
                self._said_unload = True
                self.log.info("TensorRT: not enough GPU memory for the UNet engine beside the decode; it is "
                              "unloaded for each decode and loaded again after (a few seconds a chunk).")

    def _decode_with(self, engine, z, num_frames, return_dict):
        if engine is not None:
            try:
                self._room_for_decode(engine)
                out = engine(z)
                self.nets["vaedec"].trt_calls += 1
                if not self._checked_decoder:
                    self._checked_decoder = True
                    self.scratch.release()  # the check is PyTorch: it needs the room
                    ref = self.pipe.vae.decode(z, num_frames=num_frames).sample
                    diff = (out.float() - ref.float()).abs() * 127.5  # -1..1 -> levels of 255
                    self.log.info(f"TensorRT: VAE decoder vs PyTorch on the first decode: mean {diff.mean().item():.3f}, "
                             f"max {diff.max().item():.2f} levels of 255.")
                if not return_dict:
                    return (out,)
                from diffusers.models.autoencoders.vae import DecoderOutput
                return DecoderOutput(sample=out)
            except Exception as e:
                self._fail("vaedec", e)
        self.nets["vaedec"].torch_calls += 1
        self.scratch.release()  # PyTorch needs the room (DC's UNet buffer is 7.7 GB)
        if num_frames is None:
            return self.pipe.vae.decode(z, return_dict=return_dict)
        return self.pipe.vae.decode(z, num_frames=num_frames, return_dict=return_dict)

    #: The pipeline encodes 5 frames at a time (_encode_vae_frames), a chunk's last call fewer: one engine
    #: takes 2 to 5. An engine per count built a new one mid-clip whenever a chunk ended short, beside
    #: the UNet's -- on a 12 GB card that build failed and the card spilled into shared memory (2026-10-30).
    ENC_FRAMES = 5

    def vae_encode(self, x, return_dict=True):
        engine = None
        if x.dim() == 4 and 2 <= x.shape[0] <= self.ENC_FRAMES and x.dtype == torch.float16:
            _, c, h, w = x.shape
            engine = self._engine("vaeenc", (c, h, w), engine_name("vaeenc", w, h, f"2-{self.ENC_FRAMES}", c),
                                  lambda name: self._build_encoder(name, x))
        return self._encode_with(engine, x, return_dict)

    def _encode_with(self, engine, x, return_dict):
        if engine is not None:
            try:
                # CLIP is done before the encode: off the GPU now, not at the first step, beside the
                # UNet engine still loaded from the last tile (8 GB card, 2026-10-30)
                if not self.nets["unet"].off and getattr(self, "trt_unet", True):
                    self._park_torch_unet()
                moments = engine(x)
                self.nets["vaeenc"].trt_calls += 1
                from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution
                posterior = DiagonalGaussianDistribution(moments)
                if not return_dict:
                    return (posterior,)
                from diffusers.models.modeling_outputs import AutoencoderKLOutput
                return AutoencoderKLOutput(latent_dist=posterior)
            except Exception as e:
                self._fail("vaeenc", e)
        self.nets["vaeenc"].torch_calls += 1
        self.scratch.release()  # PyTorch needs the room (DC's UNet buffer is 7.7 GB)
        return self.pipe.vae.encode(x, return_dict=return_dict)

    # ---- where the PyTorch UNet waits --------------------------------------------------------------------

    def _park_torch_unet(self):
        """While the engine denoises, the PyTorch UNet needn't be on the GPU beside it. With model offload
        it stays where the hooks leave it (in RAM), and CLIP goes there too, as the UNet's hook would have
        sent it; with no offload it waits in RAM until a chunk needs it."""
        pipe = self.pipe
        if self.offload_type == "model":
            enc = getattr(pipe, "image_encoder", None)
            if enc is not None and enc.device.type == "cuda":
                enc.to("cpu")
        elif self.offload_type == "none" and pipe.unet.device.type == "cuda":
            pipe.unet.to("cpu")
            torch.cuda.empty_cache()
        _hand_back_cache()

    def _unpark_torch_unet(self):
        if self.offload_type == "none" and self.pipe.unet.device.type != "cuda":
            self.pipe.unet.to("cuda")

    # ---- building ----------------------------------------------------------------------------------------

    def _on_gpu(self, module):
        """Moves module to the GPU for an export; returns how to put it back."""
        before = next(module.parameters()).device

        def back():
            module.to(before)
            torch.cuda.empty_cache()
        module.to("cuda")
        return back

    def _build_unet(self, name, sample, ehs, ids):
        from pipelines.stereo_video_inpainting import configure_attention_processors
        pipe = self.pipe
        configure_attention_processors(pipe, use_sageattention=False)  # SageAttention can't be exported
        self._on_gpu(pipe.unet)
        t = torch.ones(1, dtype=torch.float32, device="cuda")

        def to_ram():  # TensorRT's build wants the room; the engine denoises from now on (_park_torch_unet)
            pipe.unet.to("cpu")
            torch.cuda.empty_cache()
        frames = torch.export.Dim("frames", min=2, max=self.frames_chunk)
        top = (1, self.frames_chunk) + tuple(sample.shape[2:])
        try:
            build_engine(name, lambda p: _export(_UNetExport(pipe.unet).eval(),
                                                 (sample.cuda(), t, ehs.cuda(), ids.cuda()),
                                                 ["sample", "timestep", "encoder_hidden_states", "added_time_ids"], p,
                                                 dynamic_shapes=({1: frames}, None, None, None)),
                         release=to_ram,
                         profile={"sample": ((1, 2) + tuple(sample.shape[2:]), top, top)})
        finally:
            configure_attention_processors(pipe, use_sageattention=self.use_sageattention)

    def _build_decoder(self, name, z):
        back = self._on_gpu(self.pipe.vae)
        build_engine(name, lambda p: _export(_DecoderExport(self.pipe.vae, 2).eval(), (z.cuda(),), ["z"], p),
                     release=back)

    def _build_encoder(self, name, x):
        back = self._on_gpu(self.pipe.vae)
        n = torch.export.Dim("n", min=2, max=self.ENC_FRAMES)
        top = (self.ENC_FRAMES,) + tuple(x.shape[1:])
        build_engine(name, lambda p: _export(_EncoderExport(self.pipe.vae).eval(), (x.cuda(),), ["x"], p,
                                             dynamic_shapes=({0: n},)),
                     release=back, profile={"x": ((2,) + tuple(x.shape[1:]), top, top)})

    # ---- the end of a run --------------------------------------------------------------------------------

    def summary(self) -> str:
        return "TensorRT: " + "; ".join(f"{n.label} {n.trt_calls} TensorRT / {n.torch_calls} PyTorch"
                                        for n in self.nets.values()) + "."

    def release(self):
        for net in self.nets.values():
            net.engines.clear()
        self.scratch.release()


class _DecoderExportAnyLength(torch.nn.Module):
    """The temporal decoder over however many frames it is given (DepthCrafter decodes 4 at a time and the
    rest at the end)."""

    def __init__(self, vae):
        super().__init__()
        self.decoder = vae.decoder

    def forward(self, z):
        n = z.shape[0]
        indicator = torch.zeros(1, n, dtype=z.dtype, device=z.device)
        return self.decoder(z, num_frames=n, image_only_indicator=indicator)


class DepthCrafterTensorRT(TensorRT):
    """DepthCrafter's TensorRT (2026-10-29): its UNet takes one CLIP embedding per frame ([1, T, 1024]) and
    its window is as long as a short clip, or a segment, so its engines take any length -- the UNet 2 frames
    to the window, the VAE 2 to 4 frames -- built for the window and 4. One frame (a clip's odd last
    decode) and anything longer than the window run in PyTorch.

    2.0x at 768x448, window 120, 10 steps on a 16 GB 5060 Ti; the depth is ~1 10-bit code from exact SDPA
    on average (Sage: ~0.3-0.7), ~3.6% of pixels 4+ codes off, a steady ~0.5-code lift. GroupNorm and
    LayerNorm exported in fp32 were tried: 0.52 -> 0.49 codes, 8% slower -- not the cause, left out."""

    MODEL = "dc"
    MIN_FRAMES = 2
    VAE_FRAMES = 4

    def __init__(self, *args, trt_unet: bool = True, **kwargs):
        """trt_unet False: the VAE only (Thomas's DepthCrafter patch, 2026-10-29: the depth as close to
        exact as the normal path); the UNet stays PyTorch with SageAttention."""
        super().__init__(*args, **kwargs)
        self.trt_unet = bool(trt_unet)

    def unet(self, sample, timestep, encoder_hidden_states=None, added_time_ids=None, return_dict=True, **kw):
        engine = None
        if (self.trt_unet and not kw and sample.dim() == 5 and sample.shape[0] == 1
                and self.MIN_FRAMES <= sample.shape[1] <= self.frames_chunk and sample.dtype == torch.float16
                and encoder_hidden_states is not None and added_time_ids is not None
                and tuple(encoder_hidden_states.shape[:2]) == tuple(sample.shape[:2])
                and tuple(added_time_ids.shape) == (1, 3)):
            _, f, c, h, w = sample.shape
            top = self.frames_chunk
            engine = self._engine("unet", (c, h, w, top, encoder_hidden_states.shape[2]),
                                  engine_name("unet", w * 8, h * 8, f"{self.MIN_FRAMES}-{top}", c, self.MODEL),
                                  lambda name: self._build_unet(name, sample, encoder_hidden_states, added_time_ids))
        if engine is not None:
            try:
                self._park_torch_unet()
                t = torch.as_tensor(timestep, dtype=torch.float32, device=sample.device).reshape(1)
                out = engine(sample, t, encoder_hidden_states, added_time_ids)
                self.nets["unet"].trt_calls += 1
                if not return_dict:
                    return (out,)
                from diffusers.models.unets.unet_spatio_temporal_condition import UNetSpatioTemporalConditionOutput
                return UNetSpatioTemporalConditionOutput(sample=out)
            except Exception as e:
                self._fail("unet", e)
        self.nets["unet"].torch_calls += 1
        self._torch_unet_room()
        self._unpark_torch_unet()
        return self.pipe.unet(sample, timestep, encoder_hidden_states=encoder_hidden_states,
                              added_time_ids=added_time_ids, return_dict=return_dict, **kw)

    def vae_decode(self, z, num_frames=None, return_dict=True):
        engine = None
        if (z.dim() == 4 and self.MIN_FRAMES <= z.shape[0] <= self.VAE_FRAMES and num_frames == z.shape[0]
                and z.dtype == torch.float16):
            _, c, h, w = z.shape
            engine = self._engine("vaedec", (c, h, w),
                                  engine_name("vaedec", w * 8, h * 8, f"{self.MIN_FRAMES}-{self.VAE_FRAMES}", c,
                                              self.MODEL),
                                  lambda name: self._build_decoder(name, z))
        return self._decode_with(engine, z, num_frames, return_dict)

    def vae_encode(self, x, return_dict=True):
        engine = None
        if x.dim() == 4 and self.MIN_FRAMES <= x.shape[0] <= self.VAE_FRAMES and x.dtype == torch.float16:
            _, c, h, w = x.shape
            engine = self._engine("vaeenc", (c, h, w),
                                  engine_name("vaeenc", w, h, f"{self.MIN_FRAMES}-{self.VAE_FRAMES}", c, self.MODEL),
                                  lambda name: self._build_encoder(name, x))
        return self._encode_with(engine, x, return_dict)

    def _build_unet(self, name, sample, ehs, ids):
        pipe, top = self.pipe, self.frames_chunk
        _, f, c, h, w = sample.shape
        frames = torch.export.Dim("frames", min=self.MIN_FRAMES, max=top)
        self._on_gpu(pipe.unet)
        t = torch.ones(1, dtype=torch.float32, device="cuda")

        def to_ram():
            pipe.unet.to("cpu")
            torch.cuda.empty_cache()
        with exportable(pipe.unet):
            build_engine(
                name,
                lambda p: _export(_UNetExport(pipe.unet).eval(), (sample.cuda(), t, ehs.cuda(), ids.cuda()),
                                  ["sample", "timestep", "encoder_hidden_states", "added_time_ids"], p,
                                  dynamic_shapes=({1: frames}, None, {1: frames}, None)),
                release=to_ram,
                profile={"sample": ((1, self.MIN_FRAMES, c, h, w), (1, top, c, h, w), (1, top, c, h, w)),
                         "encoder_hidden_states": ((1, self.MIN_FRAMES, ehs.shape[2]), (1, top, ehs.shape[2]),
                                                   (1, top, ehs.shape[2]))})

    def _vae_profile(self, name, x):
        lo, hi = self.MIN_FRAMES, self.VAE_FRAMES
        return {name: ((lo,) + tuple(x.shape[1:]), (hi,) + tuple(x.shape[1:]), (hi,) + tuple(x.shape[1:]))}

    def _build_decoder(self, name, z):
        back = self._on_gpu(self.pipe.vae)
        n = torch.export.Dim("n", min=self.MIN_FRAMES, max=self.VAE_FRAMES)
        build_engine(name, lambda p: _export(_DecoderExportAnyLength(self.pipe.vae).eval(), (z.cuda(),), ["z"], p,
                                             dynamic_shapes=({0: n},)),
                     release=back, profile=self._vae_profile("z", z))

    def _build_encoder(self, name, x):
        back = self._on_gpu(self.pipe.vae)
        n = torch.export.Dim("n", min=self.MIN_FRAMES, max=self.VAE_FRAMES)
        build_engine(name, lambda p: _export(_EncoderExport(self.pipe.vae).eval(), (x.cuda(),), ["x"], p,
                                             dynamic_shapes=({0: n},)),
                     release=back, profile=self._vae_profile("x", x))


def kind_label(kind: str) -> str:
    return {"unet": "UNet", "vaeenc": "VAE encoder", "vaedec": "VAE decoder"}[kind]


def enable_tensorrt(pipe, frames_chunk: int, use_sageattention: bool, offload_type: str, kind=TensorRT,
                    logger=None, stage: str = "Inpainting", **options):
    """Turns TensorRT on for this pipeline (pipe.tensorrt); None, said in the log, when it can't be.
    kind: TensorRT (Inpainting) or DepthCrafterTensorRT; logger: the stage's, so the lines reach its log."""
    out = logger or log
    reason = unavailable_reason()
    if not reason and offload_type == "sequential":
        reason = "not with Sequential CPU offload"
    if reason:
        out.warning(f"TensorRT: {reason} -- {stage} runs the normal way.")
        return None
    pipe.tensorrt = kind(pipe, frames_chunk, use_sageattention, offload_type, logger=logger, **options)
    parts = "the VAE" if options.get("trt_unet") is False else "the UNet and VAE"
    out.info(f"TensorRT on for {parts} (engines in {ENGINE_DIR}).")
    return pipe.tensorrt


def release_tensorrt(pipe) -> None:
    """The run's TensorRT summary in the log, and its engines freed."""
    trt = getattr(pipe, "tensorrt", None)
    if trt is None:
        return
    trt.log.info(trt.summary())
    trt.release()
    pipe.tensorrt = None
