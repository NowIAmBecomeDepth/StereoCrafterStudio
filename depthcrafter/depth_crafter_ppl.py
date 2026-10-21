import inspect
import os
import sys
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Union

import logging

import numpy as np
import torch

from diffusers.pipelines.stable_video_diffusion.pipeline_stable_video_diffusion import (
    _resize_with_antialiasing,
    StableVideoDiffusionPipelineOutput,
    StableVideoDiffusionPipeline,
    retrieve_timesteps,
)
from diffusers.utils.torch_utils import randn_tensor, is_compiled_module

# "depthcrafter" is one of the console's stage-routed prefixes, so anything
# logged here lands on the DepthCrafter tab rather than Raw / All.
_logger = logging.getLogger(__name__)


# CUDA kernels for reflect-padding (used inside _resize_with_antialiasing) index with
# 32-bit integers, so any tensor handed to them must have fewer than 2**31 elements.
# A 1:32 shot at 1024x576 is ~3.9e9 elements and trips this immediately.
_INT32_INDEX_LIMIT = 2**31 - 1


def _max_frames_for_32bit(per_frame_elements: int, safety: float = 0.4) -> int:
    """Largest frame count whose [n, c, h, w] tensor stays inside 32-bit index math.

    The reflect-pad inside the antialiased resize inflates the tensor before the
    kernel runs, so we keep a healthy margin rather than sitting on the exact limit.
    """
    per_frame_elements = max(int(per_frame_elements), 1)
    return max(1, int((_INT32_INDEX_LIMIT / per_frame_elements) * safety))


def _format_hms(seconds: float) -> str:
    """Compact h/m/s for console lines. Self-contained so this module keeps no
    dependency on depthcrafter.utils."""
    if seconds is None or seconds != seconds or seconds < 0:  # None / NaN / negative
        return "--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class _ThroughputMeter:
    """Frames-per-second accounting for one phase of a run.

    Silent by design. It prints nothing and draws nothing: the per-phase and
    live readouts it used to emit buried the console under four lines a job
    plus a counter refreshing twice a second, and everything worth keeping is
    folded into the one end-of-job summary the logic layer writes instead
    (see _format_throughput_breakdown). This just measures and hands back
    frames/seconds/fps.

    `frames_done` is accepted as a float so the denoising loop can credit
    partial windows (see the note at its call site) instead of jumping in
    `stride`-sized steps every 30-odd seconds.
    """

    def __init__(
        self,
        label: str,
        total_frames: int,
        enabled: bool = True,
        stream=None,
        min_interval: float = 0.5,
        window_seconds: float = 15.0,
    ):
        self.label = label
        self.total_frames = max(0, int(total_frames))
        self.stream = stream if stream is not None else sys.stderr
        self.min_interval = float(min_interval)
        self.window_seconds = float(window_seconds)
        # Kept so the constructor signature and the throughput_report flag
        # still mean something to callers; nothing is drawn either way.
        self.enabled = bool(enabled)

        self.start_time = time.perf_counter()
        self.frames_done = 0.0
        self._samples = deque()  # (timestamp, frames_done)
        self._samples.append((self.start_time, 0.0))

    # -- numbers ---------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.start_time

    @property
    def average_fps(self) -> float:
        elapsed = self.elapsed
        return (self.frames_done / elapsed) if elapsed > 0 else 0.0

    @property
    def rolling_fps(self) -> float:
        if len(self._samples) < 2:
            return self.average_fps
        t0, f0 = self._samples[0]
        t1, f1 = self._samples[-1]
        span = t1 - t0
        if span <= 0:
            return self.average_fps
        return (f1 - f0) / span

    @property
    def eta_seconds(self) -> Optional[float]:
        if not self.total_frames:
            return None
        avg = self.average_fps
        if avg <= 0:
            return None
        return max(0.0, (self.total_frames - self.frames_done) / avg)

    # -- accounting ------------------------------------------------------

    def update(self, frames_done: float, force: bool = False, extra: str = "") -> None:
        """Set the absolute frame count completed so far.

        `force` and `extra` are vestigial -- they controlled the live redraw --
        and are kept so the call sites in the denoise loop read unchanged.
        """
        self.frames_done = max(0.0, float(frames_done))

        now = time.perf_counter()
        self._samples.append((now, self.frames_done))
        while len(self._samples) > 2 and (now - self._samples[0][0]) > self.window_seconds:
            self._samples.popleft()

    def close(self, frames_done: Optional[float] = None) -> Dict:
        """Return this phase's stats. Prints nothing; see the class docstring."""
        if frames_done is not None:
            self.frames_done = max(0.0, float(frames_done))
        elapsed = self.elapsed
        fps = (self.frames_done / elapsed) if elapsed > 0 else 0.0

        return {
            "frames": int(round(self.frames_done)),
            "seconds": round(elapsed, 3),
            "fps": round(fps, 4),
        }


def encode_video_latents_upcast(vae, encode, video, chunk_size, progress_cb):
    """``encode`` (the pipeline's encode_vae_video), in the VAE's own fp16.

    SVD's VAE config asks for force_upcast, and the encode used to run in fp32
    for it: 19.9 s of a 260 s job (160 frames at 768x448). In fp16 it takes
    10.7 s, the depth moves 0.1 of a 10-bit code on average, and accuracy
    against a scene of known depth is unchanged (.claude/dc-speed/REPORT.md,
    2026-10-11). force_upcast is there for an fp16 overflow, so latents that
    aren't all finite are encoded again upcast to fp32, the old way -- and the
    VAE is cast back afterwards even when that encode raises (audit 2026-09-28
    #25: an out-of-memory encode on one clip left the VAE in fp32 for every
    later clip)."""
    latents = encode(video, chunk_size=chunk_size, target_dtype=vae.dtype, progress_cb=progress_cb)
    needs_upcasting = vae.dtype == torch.float16 and vae.config.force_upcast
    if not needs_upcasting or bool(torch.isfinite(latents).all()):
        return latents
    _logger.warning("The fp16 VAE encode overflowed; encoding this clip again in fp32.")
    del latents
    vae.to(dtype=torch.float32)
    try:
        return encode(video, chunk_size=chunk_size, target_dtype=vae.dtype, progress_cb=progress_cb)
    finally:
        vae.to(dtype=torch.float16)


class DepthCrafterPipeline(StableVideoDiffusionPipeline):
    @torch.inference_mode()
    def encode_video(
        self,
        video: torch.Tensor,
        chunk_size: int = 14,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> torch.Tensor:
        """
        :param video: [b, c, h, w] in range [-1, 1], the b may contain multiple videos or frames
        :param chunk_size: the chunk size to encode video
        :param progress_cb: optional callable receiving the running count of frames encoded
        :return: image_embeddings in shape of [b, 1024]

        NOTE: the antialiased downscale to 224x224 is done *inside* the loop. Doing it
        on the whole clip at once (the upstream behaviour) overflows 32-bit CUDA
        indexing past roughly 1,200 frames at 1024x576, and allocates a full-resolution
        fp32 copy of the entire clip on the way there. Per-chunk resizing is
        mathematically identical -- the resize is purely spatial, with no cross-frame
        state -- and caps peak memory at chunk_size frames.
        """
        num_frames, c, h, w = video.shape
        resize_chunk = min(int(chunk_size), _max_frames_for_32bit(c * h * w))
        resize_chunk = max(1, resize_chunk)

        embeddings = []
        for i in range(0, num_frames, resize_chunk):
            video_224 = _resize_with_antialiasing(video[i : i + resize_chunk].float(), (224, 224))
            video_224 = (video_224 + 1.0) / 2.0  # [-1, 1] -> [0, 1]

            tmp = self.feature_extractor(
                images=video_224,
                do_normalize=True,
                do_center_crop=False,
                do_resize=False,
                do_rescale=False,
                return_tensors="pt",
            ).pixel_values.to(video.device, dtype=video.dtype)
            embeddings.append(self.image_encoder(tmp).image_embeds)  # [b, 1024]

            del video_224, tmp

            if progress_cb is not None:
                progress_cb(min(i + resize_chunk, num_frames))

        embeddings = torch.cat(embeddings, dim=0)  # [t, 1024]
        return embeddings

    @torch.inference_mode()
    def encode_vae_video(
        self,
        video: torch.Tensor,
        chunk_size: int = 14,
        target_dtype: Optional[torch.dtype] = None,
        progress_cb: Optional[Callable[[int], None]] = None,
    ):
        """
        :param video: [b, c, h, w] in range [-1, 1], the b may contain multiple videos or frames
        :param chunk_size: the chunk size to encode video
        :param target_dtype: dtype to cast each chunk to before the VAE sees it. Casting
            per chunk instead of casting the whole clip up front avoids materialising a
            second full-resolution copy of the video (fp32 upcasting doubles it again).
        :param progress_cb: optional callable receiving the running count of frames encoded
        :return: vae latents in shape of [b, c, h, w]
        """
        total_in = video.shape[0]
        video_latents = []
        for i in range(0, total_in, chunk_size):
            chunk = video[i : i + chunk_size]
            if target_dtype is not None and chunk.dtype != target_dtype:
                chunk = chunk.to(target_dtype)
            video_latents.append(self.vae.encode(chunk).latent_dist.mode())
            del chunk
            if progress_cb is not None:
                progress_cb(min(i + chunk_size, total_in))
        video_latents = torch.cat(video_latents, dim=0)
        return video_latents

    def _decode_chunk(self, chunk: torch.Tensor, accepts_num_frames: bool) -> torch.Tensor:
        """One decode chunk; out of memory, it is decoded in halves (and those in halves...)
        rather than ending the clip: 8 frames a chunk (2026-10-11) doubled the decode's peak
        against the 4 before, too much for a smaller card (bug sweep 2026-10-14c). The halves
        meet at one more seam than the whole chunk would have. On Windows a full card spills
        into system memory rather than raising, so this only catches a real out-of-memory;
        the default is back at 4 for that (2026-10-21)."""
        try:
            kw = {"num_frames": chunk.shape[0]} if accepts_num_frames else {}
            return self.vae.decode(chunk, **kw).sample
        except torch.OutOfMemoryError:
            if chunk.shape[0] <= 1:
                raise
            torch.cuda.empty_cache()
            half = chunk.shape[0] // 2
            _logger.warning("Out of GPU memory decoding %d frames at once; decoding them in halves.", chunk.shape[0])
            return torch.cat([self._decode_chunk(chunk[:half], accepts_num_frames),
                              self._decode_chunk(chunk[half:], accepts_num_frames)])

    @torch.inference_mode()
    def decode_latents_to_gray_cpu(
        self,
        latents: torch.Tensor,
        num_frames: int,
        decode_chunk_size: int = 14,
        out_dtype=np.float32,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> np.ndarray:
        """Decode latents straight to a [t, h, w] CPU array, one chunk at a time.

        Upstream `decode_latents` concatenates every decoded frame into a single GPU
        tensor, calls `.float()` on it (doubling it), and then `postprocess_video`
        allocates a third copy inside `denormalize`. For a 2,208-frame clip at
        768x448 that is 8.5 GiB per copy.

        This does the denormalize and the RGB->gray reduction inside the decode loop,
        on chunk-sized tensors, and writes each chunk into a preallocated CPU array.
        Peak GPU cost is one chunk. The grayscale reduction is the same mean that
        `_perform_inference` applies immediately afterwards, so the values are
        unchanged -- it just never builds the 3-channel version at full length.
        """
        latents = latents.flatten(0, 1)
        latents = 1 / self.vae.config.scaling_factor * latents

        forward_vae_fn = self.vae._orig_mod.forward if is_compiled_module(self.vae) else self.vae.forward
        accepts_num_frames = "num_frames" in set(inspect.signature(forward_vae_fn).parameters.keys())

        total = latents.shape[0]
        decode_chunk_size = max(1, int(decode_chunk_size))

        out = None
        write_idx = 0
        for i in range(0, total, decode_chunk_size):
            chunk = latents[i : i + decode_chunk_size]
            num_frames_in = chunk.shape[0]

            frame = self._decode_chunk(chunk, accepts_num_frames)  # [n, 3, h, w] in [-1, 1]

            # denormalize (what image_processor.denormalize does) then collapse to gray
            frame = (frame.float() * 0.5 + 0.5).clamp_(0.0, 1.0)
            frame = frame.mean(dim=1)  # [n, h, w]

            frame_np = frame.cpu().numpy()
            if out_dtype is not None and frame_np.dtype != out_dtype:
                frame_np = frame_np.astype(out_dtype)

            if out is None:
                out = np.empty((total, frame_np.shape[1], frame_np.shape[2]), dtype=frame_np.dtype)
            out[write_idx : write_idx + num_frames_in] = frame_np
            write_idx += num_frames_in

            del chunk, frame, frame_np

            if progress_cb is not None:
                progress_cb(write_idx)

        return out

    # ------------------------------------------------------------------
    # Latent checkpointing
    # ------------------------------------------------------------------

    LATENT_CHECKPOINT_FORMAT_VERSION = 1

    def _latent_checkpoint_header(
        self,
        num_frames: int,
        window_size: int,
        overlap: int,
        num_inference_steps: int,
        guidance_scale: float,
        height: int,
        width: int,
        noise_aug_strength: float,
        extra: Optional[Dict] = None,
    ) -> Dict:
        """Identity of a denoising run.

        Resuming from latents produced under different settings would silently
        produce a wrong depth map, so every parameter that changes `latents_all`
        goes in here and is compared exactly on load.
        """
        header = {
            "format_version": self.LATENT_CHECKPOINT_FORMAT_VERSION,
            "num_frames": int(num_frames),
            "window_size": int(window_size),
            "overlap": int(overlap),
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
            "height": int(height),
            "width": int(width),
            "noise_aug_strength": float(noise_aug_strength),
        }
        if extra:
            for k, v in extra.items():
                header[str(k)] = v
        return header

    @staticmethod
    def _peak_vram_gb() -> Optional[float]:
        """Highest VRAM this process reserved since the last reset, in GB.

        Reserved rather than allocated: that is what the card actually had
        taken away from it, and the gap between the two is what made the last
        window spill to shared system memory before empty_cache() was added
        between windows.
        """
        if not torch.cuda.is_available():
            return None
        try:
            return torch.cuda.max_memory_reserved() / (1024 ** 3)
        except Exception:
            return None

    def _save_latent_checkpoint(self, path: str, latents_all: torch.Tensor, header: Dict) -> bool:
        """Write latents + header atomically. Failure is logged, never fatal."""
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = path + ".tmp"
            torch.save({"header": header, "latents": latents_all.detach().to("cpu")}, tmp_path)
            os.replace(tmp_path, path)
            return True
        except Exception as exc:  # a failed checkpoint must not kill a finished denoise
            print(f"[DepthCrafter] Warning: could not write latent checkpoint to {path}: {exc}")
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except Exception:
                pass
            return False

    def _load_latent_checkpoint(self, path: str, expected_header: Dict, device) -> Optional[torch.Tensor]:
        """Return latents if the checkpoint matches expected_header exactly, else None."""
        if not path or not os.path.exists(path):
            return None
        try:
            try:
                payload = torch.load(path, map_location="cpu", weights_only=True)
            except TypeError:  # torch too old for weights_only
                payload = torch.load(path, map_location="cpu")
        except Exception as exc:
            print(f"[DepthCrafter] Latent checkpoint at {path} could not be read ({exc}); running from scratch.")
            return None

        if not isinstance(payload, dict) or "latents" not in payload or "header" not in payload:
            print(f"[DepthCrafter] Latent checkpoint at {path} is malformed; running from scratch.")
            return None

        found_header = payload["header"]
        mismatches = []
        for key, expected in expected_header.items():
            actual = found_header.get(key, "<missing>")
            if isinstance(expected, float):
                same = isinstance(actual, (int, float)) and abs(float(actual) - expected) < 1e-6
            else:
                same = actual == expected
            if not same:
                mismatches.append(f"{key}: checkpoint={actual!r} current={expected!r}")

        if mismatches:
            print(
                "[DepthCrafter] Latent checkpoint does not match current settings; running from scratch.\n  "
                + "\n  ".join(mismatches)
            )
            return None

        latents = payload["latents"]
        if not isinstance(latents, torch.Tensor):
            print(f"[DepthCrafter] Latent checkpoint at {path} holds no tensor; running from scratch.")
            return None

        print(f"[DepthCrafter] Resuming from latent checkpoint: {path}")
        return latents.to(device=device)

    @staticmethod
    def check_inputs(video, height, width):
        """
        :param video:
        :param height:
        :param width:
        :return:
        """
        if not isinstance(video, torch.Tensor) and not isinstance(video, np.ndarray):
            raise ValueError(f"Expected `video` to be a `torch.Tensor` or `VideoReader`, but got a {type(video)}")

        if height % 8 != 0 or width % 8 != 0:
            raise ValueError(f"`height` and `width` have to be divisible by 8 but are {height} and {width}.")

    @torch.no_grad()
    def __call__(
        self,
        video: Union[np.ndarray, torch.Tensor],
        height: int = 576,
        width: int = 1024,
        num_inference_steps: int = 25,
        guidance_scale: float = 1.0,
        window_size: Optional[int] = 110,
        noise_aug_strength: float = 0.02,
        decode_chunk_size: Optional[int] = 4,
        encode_chunk_size: Optional[int] = 4,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        latents: Optional[torch.FloatTensor] = None,
        output_type: Optional[str] = "pil",
        callback_on_step_end: Optional[Callable[[int, int, Dict], None]] = None,
        callback_on_step_end_tensor_inputs: List[str] = ["latents"],
        return_dict: bool = True,
        overlap: int = 25,
        track_time: bool = False,
        latent_checkpoint_path: Optional[str] = None,
        latent_checkpoint_resume: bool = False,
        latent_checkpoint_extra: Optional[Dict] = None,
        throughput_report: bool = True,
    ):
        """
        :param video: in shape [t, h, w, c] if np.ndarray or [t, c, h, w] if torch.Tensor, in range [0, 1]
        :param height:
        :param width:
        :param num_inference_steps:
        :param guidance_scale:
        :param window_size: sliding window processing size
        :param fps:
        :param motion_bucket_id:
        :param noise_aug_strength:
        :param decode_chunk_size: frames per VAE decode. The temporal decoder mixes the
            frames inside a chunk, so each chunk edge is a seam: 8 (upstream's default)
            flickered 7% less against a scene of known depth than 4 (2026-10-11), but
            at 768x448 its decode reserves 12.7 GB against 6.6 GB at 4, for the same
            speed (2026-10-21). On a 12 GB card WDDM spills that into system memory
            instead of failing, so _decode_chunk's halving never runs and the decode
            crawls: a user's 69-frame clip took 2:05 in Studio against 36 s before. 4.
        :param encode_chunk_size: frames per CLIP / VAE encode and noise chunk. Kept at
            4 apart from the decode: the noise is drawn per chunk, so changing it
            changes the random stream (and the depth) for no speed.
        :param generator:
        :param latents:
        :param output_type:
        :param callback_on_step_end:
        :param callback_on_step_end_tensor_inputs:
        :param return_dict:
        :return:
        """
        # Per-call throughput bookkeeping. Read back by DepthCrafterDemo after the
        # call returns (see `last_throughput_stats`), so the caller can fold these
        # into the job metadata without threading another return value through.
        self.last_throughput_stats = {}
        # Peak VRAM is reported per call, so the high-water mark has to start
        # from this call rather than from whatever an earlier job left behind.
        if torch.cuda.is_available():
            try:
                torch.cuda.reset_peak_memory_stats()
            except Exception:
                pass
        call_wall_start = time.perf_counter()

        # 0. Default height and width to unet
        height = height or self.unet.config.sample_size * self.vae_scale_factor
        width = width or self.unet.config.sample_size * self.vae_scale_factor
        num_frames = video.shape[0]
        decode_chunk_size = decode_chunk_size if decode_chunk_size is not None else 4
        encode_chunk_size = max(1, int(encode_chunk_size if encode_chunk_size is not None else 4))
        if num_frames <= window_size:
            window_size = num_frames
            overlap = 0
        stride = window_size - overlap

        # 1. Check inputs. Raise error if not correct
        self.check_inputs(video, height, width)

        # 2. Define call parameters
        batch_size = 1
        device = self._execution_device
        # here `guidance_scale` is defined analog to the guidance weight `w` of equation (2)
        # of the Imagen paper: https://arxiv.org/pdf/2205.11487.pdf . `guidance_scale = 1`
        # corresponds to doing no classifier free guidance.
        self._guidance_scale = guidance_scale

        needs_upcasting = self.vae.dtype == torch.float16 and self.vae.config.force_upcast

        # --- latent checkpoint -------------------------------------------------
        # Denoising is the expensive phase; decode only needs latents_all. So a
        # valid checkpoint lets us skip the CLIP encode, the VAE encode AND the
        # denoising loop, going straight to decode.
        checkpoint_header = self._latent_checkpoint_header(
            num_frames=num_frames,
            window_size=window_size,
            overlap=overlap,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            height=height,
            width=width,
            noise_aug_strength=noise_aug_strength,
            extra=latent_checkpoint_extra,
        )

        latents_all = None
        if latent_checkpoint_path and latent_checkpoint_resume:
            latents_all = self._load_latent_checkpoint(latent_checkpoint_path, checkpoint_header, device)
        resumed_from_checkpoint = latents_all is not None

        if latents_all is None:
            # 3. Encode input video
            if isinstance(video, np.ndarray):
                video = torch.from_numpy(video.transpose(0, 3, 1, 2))
            else:
                assert isinstance(video, torch.Tensor)
            _given = video
            video = video.to(device=device, dtype=self.dtype)
            # [0,1] -> [-1,1], in [t, c, h, w]. In place on the device copy: out of place
            # it was a second full-clip copy on the GPU (~4 GB fp16 for 2000+ frames at
            # 768x448; bug sweep 2026-10-09, A3). When .to() made no copy the caller's
            # tensor is left alone.
            if video.data_ptr() == _given.data_ptr():
                video = video * 2.0 - 1.0
            else:
                video.mul_(2.0).sub_(1.0)
            del _given

            if track_time:
                start_event = torch.cuda.Event(enable_timing=True)
                encode_event = torch.cuda.Event(enable_timing=True)
                denoise_event = torch.cuda.Event(enable_timing=True)
                decode_event = torch.cuda.Event(enable_timing=True)
                start_event.record()

            clip_meter = _ThroughputMeter("encode/clip", num_frames, enabled=throughput_report)
            video_embeddings = self.encode_video(
                video,
                chunk_size=encode_chunk_size,
                progress_cb=clip_meter.update,
            ).unsqueeze(0)  # [1, t, 1024]
            self.last_throughput_stats["encode_clip"] = clip_meter.close(num_frames)
            torch.cuda.empty_cache()
            # 4. Encode input image using VAE
            # Noise is added chunk-wise and in place. The upstream form
            #     noise = randn_tensor(video.shape, ...); video = video + noise_aug_strength * noise
            # allocates three extra full-resolution copies of the clip at peak, which is fine
            # for a 110-frame window and fatal for a multi-thousand-frame full-video pass.
            noise_chunk = encode_chunk_size
            for i in range(0, video.shape[0], noise_chunk):
                chunk = video[i : i + noise_chunk]
                chunk.add_(
                    randn_tensor(chunk.shape, generator=generator, device=device, dtype=video.dtype),
                    alpha=noise_aug_strength,
                )
                del chunk

            # pdb.set_trace()
            vae_meter = _ThroughputMeter("encode/vae", num_frames, enabled=throughput_report)
            video_latents = encode_video_latents_upcast(
                self.vae, self.encode_vae_video, video, encode_chunk_size, vae_meter.update
            ).unsqueeze(0)  # [1, t, c, h, w]
            self.last_throughput_stats["encode_vae"] = vae_meter.close(num_frames)

            # The full-resolution clip is dead weight from here on -- everything downstream
            # works in latent space. Dropping it frees ~2 bytes * t * c * h * w.
            del video
            torch.cuda.empty_cache()

            if track_time:
                encode_event.record()
                torch.cuda.synchronize()
                elapsed_time_ms = start_event.elapsed_time(encode_event)
                print(f"Elapsed time for encoding video: {elapsed_time_ms} ms")

            torch.cuda.empty_cache()

            # 5. Get Added Time IDs
            added_time_ids = self._get_add_time_ids(
                7, 127, noise_aug_strength, video_embeddings.dtype, batch_size, 1, False
            )  # [1 or 2, 3]
            added_time_ids = added_time_ids.to(device)

            # 6. Prepare timesteps
            timesteps, num_inference_steps = retrieve_timesteps(self.scheduler, num_inference_steps, device, None, None)
            num_warmup_steps = len(timesteps) - num_inference_steps * self.scheduler.order
            self._num_timesteps = len(timesteps)

            # 7. Prepare latent variables
            num_channels_latents = self.unet.config.in_channels
            latents_init = self.prepare_latents(
                batch_size,
                window_size,
                num_channels_latents,
                height,
                width,
                video_embeddings.dtype,
                device,
                generator,
                latents,
            )  # [1, t, c, h, w]
            latents_all = None

            idx_start = 0
            if overlap > 0:
                weights = torch.linspace(0, 1, overlap, device=device)
                weights = weights.view(1, overlap, 1, 1, 1)
            else:
                weights = None

            torch.cuda.empty_cache()

            # Denoise throughput. Every window costs the same to denoise, but only
            # `stride` of its frames are new once the first window is behind us, so
            # counting whole windows would overstate the rate by window_size/stride
            # (3.3x at window 100 / overlap 70). Credit *finished* frames instead:
            # frames covered by completed windows, plus the current window's new
            # frames prorated by how many of its denoising steps have run. That keeps
            # the number directly comparable to the encode and decode rates.
            _sched_starts = []
            if stride > 0:
                _s = 0
                while _s < num_frames - overlap:
                    _sched_starts.append(_s)
                    _s += stride
            total_windows = max(1, len(_sched_starts))
            denoise_meter = _ThroughputMeter("denoise", num_frames, enabled=throughput_report)
            window_idx = 0
            frames_covered = 0

            # inference strategy for long videos
            # two main strategies: 1. noise init from previous frame, 2. segments stitching
            while idx_start < num_frames - overlap:
                idx_end = min(idx_start + window_size, num_frames)
                window_idx += 1
                window_new_frames = max(0, idx_end - frames_covered)
                self.scheduler.set_timesteps(num_inference_steps, device=device)

                # 9. Denoising loop
                latents = latents_init[:, : idx_end - idx_start].clone()
                latents_init = torch.cat([latents_init[:, -overlap:], latents_init[:, :stride]], dim=1)

                video_latents_current = video_latents[:, idx_start:idx_end]
                video_embeddings_current = video_embeddings[:, idx_start:idx_end]

                with self.progress_bar(total=num_inference_steps) as progress_bar:
                    # Which window this is, and nothing else. The rolling
                    # fps/avg/eta postfix that used to ride along here is gone:
                    # the end-of-job summary carries those numbers once, and on
                    # the bar they refreshed twice a second for the whole run.
                    try:
                        progress_bar.set_description(f"Window {window_idx}/{total_windows}")
                    except Exception:
                        pass  # a bar that will not take a label still counts steps
                    steps_this_window = max(1, len(timesteps))

                    for i, t in enumerate(timesteps):
                        if latents_all is not None and i == 0:
                            latents[:, :overlap] = (
                                latents_all[:, -overlap:]
                                + latents[:, :overlap] / self.scheduler.init_noise_sigma * self.scheduler.sigmas[i]
                            )

                        latent_model_input = latents  # [1, t, c, h, w]
                        latent_model_input = self.scheduler.scale_model_input(latent_model_input, t)  # [1, t, c, h, w]
                        latent_model_input = torch.cat([latent_model_input, video_latents_current], dim=2)
                        noise_pred = self.unet(
                            latent_model_input,
                            t,
                            encoder_hidden_states=video_embeddings_current,
                            added_time_ids=added_time_ids,
                            return_dict=False,
                        )[0]
                        # perform guidance
                        if self.do_classifier_free_guidance:
                            latent_model_input = latents
                            latent_model_input = self.scheduler.scale_model_input(latent_model_input, t)
                            latent_model_input = torch.cat(
                                [latent_model_input, torch.zeros_like(latent_model_input)], dim=2
                            )
                            noise_pred_uncond = self.unet(
                                latent_model_input,
                                t,
                                encoder_hidden_states=torch.zeros_like(video_embeddings_current),
                                added_time_ids=added_time_ids,
                                return_dict=False,
                            )[0]

                            noise_pred = noise_pred_uncond + self.guidance_scale * (noise_pred - noise_pred_uncond)
                        latents = self.scheduler.step(noise_pred, t, latents).prev_sample

                        if callback_on_step_end is not None:
                            callback_kwargs = {}
                            for k in callback_on_step_end_tensor_inputs:
                                callback_kwargs[k] = locals()[k]
                            callback_outputs = callback_on_step_end(self, i, t, callback_kwargs)

                            latents = callback_outputs.pop("latents", latents)

                        if i == len(timesteps) - 1 or ((i + 1) > num_warmup_steps and (i + 1) % self.scheduler.order == 0):
                            progress_bar.update()

                        denoise_meter.update(frames_covered + window_new_frames * (i + 1) / steps_this_window)

                if latents_all is None:
                    latents_all = latents.clone()
                else:
                    assert weights is not None
                    # latents_all[:, -overlap:] = (
                    #     latents[:, :overlap] + latents_all[:, -overlap:]
                    # ) / 2.0
                    latents_all[:, -overlap:] = latents[:, :overlap] * weights + latents_all[:, -overlap:] * (1 - weights)
                    latents_all = torch.cat([latents_all, latents[:, overlap:]], dim=1)

                frames_covered = idx_end
                denoise_meter.update(frames_covered, force=True)

                # Hand the cached blocks back before the next window allocates.
                #
                # Denoising one window grows the caching allocator to that
                # window's peak and it never shrinks on its own -- measured at
                # 15.09 GB reserved against 3.13 GB actually live on a 15.93 GB
                # card. That would be harmless if the next window reused those
                # blocks, but the LAST window is short (105 frames against 120
                # here), so its tensors are a different shape and the cached
                # blocks are the wrong size for them.
                #
                # The allocator therefore asks the driver for more while the
                # device has nothing left, and on Windows WDDM that does not
                # fail -- it is quietly satisfied from shared system memory,
                # over PCIe. Hence reserved climbing to 16.44 GB on a 15.93 GB
                # card, num_alloc_retries staying at 0 (no cudaMalloc ever
                # failed), and 6.44 s/it becoming 23.55 s/it.
                #
                # empty_cache() only releases blocks nothing is using, so it
                # cannot disturb the offloaded weights or latents_all, and one
                # call per window is nothing against a window's own runtime.
                torch.cuda.empty_cache()

                idx_start += stride

            self.last_throughput_stats["denoise"] = denoise_meter.close(frames_covered)

            if latent_checkpoint_path:
                self._save_latent_checkpoint(latent_checkpoint_path, latents_all, checkpoint_header)

        if track_time and not resumed_from_checkpoint:
            denoise_event.record()
            torch.cuda.synchronize()
            elapsed_time_ms = encode_event.elapsed_time(denoise_event)
            print(f"Elapsed time for denoising video: {elapsed_time_ms} ms")

        if not output_type == "latent":
            # cast back to fp16 if needed
            if needs_upcasting:
                self.vae.to(dtype=torch.float16)

            if output_type == "np":
                # Streaming path: decode -> denormalize -> gray -> CPU, chunk by chunk.
                # Returns [1, t, h, w] so `.frames[0]` yields the [t, h, w] grayscale
                # array that _perform_inference expects after its own RGB averaging
                # (which it now correctly skips, since res.ndim == 3).
                decode_meter = _ThroughputMeter("decode", num_frames, enabled=throughput_report)
                gray = self.decode_latents_to_gray_cpu(
                    latents_all, num_frames, decode_chunk_size, progress_cb=decode_meter.update
                )
                self.last_throughput_stats["decode"] = decode_meter.close()
                del latents_all
                torch.cuda.empty_cache()

                if track_time and not resumed_from_checkpoint:
                    decode_event.record()
                    torch.cuda.synchronize()
                    elapsed_time_ms = denoise_event.elapsed_time(decode_event)
                    print(f"Elapsed time for decoding video: {elapsed_time_ms} ms")

                frames = gray[None]  # [1, t, h, w]
            else:
                frames = self.decode_latents(latents_all, num_frames, decode_chunk_size)

                if track_time and not resumed_from_checkpoint:
                    decode_event.record()
                    torch.cuda.synchronize()
                    elapsed_time_ms = denoise_event.elapsed_time(decode_event)
                    print(f"Elapsed time for decoding video: {elapsed_time_ms} ms")

                frames = self.video_processor.postprocess_video(video=frames, output_type=output_type)
        else:
            frames = latents_all

        self.maybe_free_model_hooks()

        # Wall-clock total for the whole pipeline call, so the caller can report a
        # single "this is what the run actually cost you" number alongside the
        # per-phase breakdown. Includes the phases above plus the odd cache flush
        # and dtype cast between them, which is why it is measured, not summed.
        call_elapsed = time.perf_counter() - call_wall_start
        peak_vram = self._peak_vram_gb()
        self.last_throughput_stats["pipeline"] = {
            "frames": int(num_frames),
            "seconds": round(call_elapsed, 3),
            "fps": round(num_frames / call_elapsed, 4) if call_elapsed > 0 else 0.0,
            "resumed_from_checkpoint": bool(resumed_from_checkpoint),
            "peak_vram_gb": round(peak_vram, 2) if peak_vram is not None else None,
        }

        if not return_dict:
            return frames

        return StableVideoDiffusionPipelineOutput(frames=frames)