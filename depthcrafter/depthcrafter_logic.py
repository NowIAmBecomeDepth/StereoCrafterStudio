import os
import gc
import numpy as np
import torch
import time
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module="diffusers.models.transformers.transformer_2d")
# ruff: noqa: E402
import logging  # Import standard logging

# Use WARNING or ERROR to silence model loading messages.
logging.getLogger("diffusers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

# Configure a logger for this module
_logger = logging.getLogger(__name__)

from diffusers.training_utils import set_seed
from depthcrafter.depth_crafter_ppl import DepthCrafterPipeline
from depthcrafter.unet import DiffusersUNetSpatioTemporalConditionModelDepthCrafter, chunk_feed_forward
from depthcrafter.attention import (
    SageAttnProcessor2_0,
    SAGE_ATTENTION_AVAILABLE,
    shortcut_single_token_cross_attention,
)

# --- MODIFIED IMPORTS from depthcrafter.utils ---
from depthcrafter.utils import (
    save_video,
    read_video_frames,
    save_depth_visual_as_mp4_util,
    save_depth_visual_as_png_sequence_util,
    save_depth_visual_as_exr_sequence_util,
    save_depth_visual_as_single_exr_util,
    read_image_sequence_as_frames,
    create_frames_from_single_image,
    format_duration,
    get_segment_output_folder_name,
    get_segment_npz_output_filename,
    get_full_video_output_filename,
    get_sidecar_json_filename,
    save_json_file,
)
# --- END MODIFIED IMPORTS ---

try:
    import OpenEXR  # noqa: F401
    import Imath  # noqa: F401

    OPENEXR_AVAILABLE_LOGIC = True
except ImportError:
    OPENEXR_AVAILABLE_LOGIC = False
    _logger.warning(
        "OpenEXR/Imath libraries not found. EXR features will be limited/unavailable. Context: depth_crafter_logic.py"
    )

warnings.filterwarnings("ignore", category=FutureWarning, module="diffusers.models.transformers.transformer_2d")

from depthcrafter.depth_stabilize import (
    stabilize_depth_affine,
    normalize_with_percentiles,
)

from typing import Optional, Tuple, Union

# --- Global Configuration Flags ---
# Windows builds of PyTorch ship without flash-attention kernels and leave the
# cuDNN SDPA backend runtime-disabled, so SDPA silently dispatches to the slow
# memory-efficient backend. cuDNN attention is ~2x faster on the shapes
# DepthCrafter uses and is numerically exact.
if torch.cuda.is_available():
    torch.backends.cuda.enable_cudnn_sdp(True)

# Order matters: this is the order the phases actually run in, so the breakdown
# reads chronologically. Keys absent from the stats dict are skipped, which is
# what makes a checkpoint-resumed job (no encode, no denoise) print correctly.
_THROUGHPUT_PHASE_ORDER = (
    ("encode_clip", "encode/clip"),
    ("encode_vae", "encode/vae"),
    ("denoise", "denoise"),
    ("decode", "decode"),
)


def _format_throughput_breakdown(phases: Optional[dict]) -> str:
    """Render the per-phase fps list, e.g. 'encode/clip 9.99, denoise 0.97 fps'."""
    if not phases:
        return ""
    parts = []
    for key, label in _THROUGHPUT_PHASE_ORDER:
        entry = phases.get(key)
        if isinstance(entry, dict) and entry.get("fps"):
            parts.append(f"{label} {entry['fps']:.2f}")
    if not parts:
        return ""
    return ", ".join(parts) + " fps"


class JobStopped(Exception):
    """Raised between denoising steps once `DepthCrafterDemo.stop_event` is
    set: Studio's Stop / Quit stop a clip now, not after its segment
    (audit 2026-09-28 #13)."""


class DepthCrafterDemo:
    #: A threading.Event; when set, the running inference ends at its next
    #: denoising step with JobStopped. None: runs to the end (the Prototype).
    stop_event = None

    def _stop_between_steps(self, pipe, step, timestep, callback_kwargs):
        if self.stop_event is not None and self.stop_event.is_set():
            raise JobStopped("stopped by the user")
        return callback_kwargs

    def __init__(
        self,
        unet_path: str,
        pre_train_path: str,
        cpu_offload: Union[str, None] = "model",
        use_cudnn_benchmark: bool = False,
        local_files_only: bool = False,
        use_sageattention: bool = False,
    ):
        torch.backends.cudnn.benchmark = use_cudnn_benchmark
        # Per-phase throughput from the most recent _perform_inference call.
        self._last_throughput_stats = {}
        try:
            unet = DiffusersUNetSpatioTemporalConditionModelDepthCrafter.from_pretrained(
                unet_path, low_cpu_mem_usage=True, torch_dtype=torch.float16, local_files_only=local_files_only
            )
            self.pipe = DepthCrafterPipeline.from_pretrained(
                pre_train_path, unet=unet, torch_dtype=torch.float16, variant="fp16", local_files_only=local_files_only
            )
            # for saving memory, we can offload the model to CPU, or even run the model sequentially to save more memory

            cpu_offload_lower = cpu_offload

            if cpu_offload_lower == "sequential":
                self.pipe.enable_sequential_cpu_offload()
                _logger.info("CPU Offload set to 'sequential'.")
            elif cpu_offload_lower == "model":
                self.pipe.enable_model_cpu_offload()
                _logger.info("CPU Offload set to 'model'.")
            else:
                # If the value is "none", "None", or any other unrecognized string/value
                # (or the legacy string "None"), we default to full CUDA.
                self.pipe.to("cuda")
                _logger.info(
                    f"CPU Offload set to '{cpu_offload}' (unrecognized/None option). Model loaded entirely on CUDA."
                )

            if use_sageattention:
                if SAGE_ATTENTION_AVAILABLE:
                    self.pipe.unet.set_attn_processor(SageAttnProcessor2_0())
                    _logger.info(
                        "SageAttention ENABLED for UNet spatial self-attention (SDPA fallback for short/cross attention)."
                    )
                else:
                    _logger.warning(
                        "SageAttention requested but the 'sageattention' package is not available. Using standard SDPA."
                    )
            else:
                _logger.info("Using standard PyTorch SDPA attention (SageAttention disabled).")

            # one CLIP token per frame: cross-attention without the attention (2026-10-11)
            _logger.debug(
                "Single-token cross-attention on %d UNet layers.",
                shortcut_single_token_cross_attention(self.pipe.unet),
            )

            # the temporal blocks' feed-forward was the run's VRAM peak (2026-10-01)
            _logger.debug("Feed-forward slicing on %d UNet layers.", chunk_feed_forward(self.pipe.unet))

            _logger.debug(
                "DepthCrafterPipeline initialized successfully."
            )  # This was already there, ensure it remains.
        except Exception as e:
            _logger.critical(f"CRITICAL: Failed to initialize DepthCrafterPipeline: {e}", exc_info=True)
            raise  # Re-raise after logging

    @staticmethod
    def get_latent_checkpoint_path(base_output_folder: str, original_video_basename: str) -> str:
        """Path of the denoised-latents checkpoint for a full-video job.

        Kept as a staticmethod so the GUI can test for an existing checkpoint
        before the job starts without duplicating the naming rule.
        """
        return os.path.join(base_output_folder, f"{original_video_basename}_latents.ckpt")

    def _setup_paths(
        self, base_output_folder: str, original_video_basename: str, segment_job_info: Optional[dict]
    ) -> Tuple[str, str, str]:
        actual_save_folder_for_output = base_output_folder
        output_filename_for_meta = ""

        if segment_job_info:
            segment_subfolder_name = get_segment_output_folder_name(original_video_basename)
            actual_save_folder_for_output = os.path.join(base_output_folder, segment_subfolder_name)
            output_filename_for_meta = get_segment_npz_output_filename(
                original_video_basename, segment_job_info["segment_id"], segment_job_info["total_segments"]
            )
        else:
            output_filename_for_meta = get_full_video_output_filename(original_video_basename)

        full_save_path = os.path.join(actual_save_folder_for_output, output_filename_for_meta)
        os.makedirs(actual_save_folder_for_output, exist_ok=True)
        return actual_save_folder_for_output, output_filename_for_meta, full_save_path

    def _initialize_job_metadata(
        self,
        guidance_scale: float,
        num_denoising_steps: int,
        user_target_height: int,
        user_target_width: int,
        seed_val: int,
        target_fps_for_read: float,
        segment_job_info: Optional[dict],
        output_filename_for_meta: str,
        pipe_call_window_size: int,
        pipe_call_overlap: int,
        original_video_basename: str,
    ) -> dict:
        job_specific_metadata = {
            "original_video_basename": original_video_basename,
            "guidance_scale": float(guidance_scale),
            "inference_steps": int(num_denoising_steps),
            "target_height_during_process": int(user_target_height),
            "target_width_during_process": int(user_target_width),
            "seed": int(seed_val),
            "target_fps_setting": float(target_fps_for_read),
            "status": "pending",
            "_individual_metadata_path": None,
        }

        if segment_job_info:
            job_specific_metadata.update(
                {
                    "segment_id": int(segment_job_info["segment_id"]),
                    "source_start_frame_raw_index": int(segment_job_info["start_frame_raw_index"]),
                    "source_num_frames_raw_for_segment": int(segment_job_info["num_frames_to_load_raw"]),
                    "output_segment_filename": output_filename_for_meta,
                    "output_segment_format": "npz",
                    "segment_definition_window_setting": int(pipe_call_window_size),
                    "segment_definition_overlap_setting": int(pipe_call_overlap),
                }
            )
        else:
            job_specific_metadata.update(
                {
                    "output_video_filename": output_filename_for_meta,
                    "pipeline_window_size_used_for_full_video_pass": int(pipe_call_window_size),
                    "pipeline_overlap_used_for_full_video_pass": int(pipe_call_overlap),
                }
            )
        return job_specific_metadata

    def _load_frames(
        self,
        video_path_or_job_info: Union[str, dict],
        frames_array_if_provided: Optional[np.ndarray],
        process_length_for_read: int,
        user_target_height: int,
        user_target_width: int,
        segment_job_info: Optional[dict],
        job_specific_metadata: dict,
    ) -> Tuple[Optional[np.ndarray], float, int, int]:
        actual_frames_to_process = None
        actual_fps_for_save = 30.0
        original_h_loaded, original_w_loaded = None, None

        if frames_array_if_provided is not None:
            actual_frames_to_process = frames_array_if_provided
            if segment_job_info and "original_video_fps" in segment_job_info:
                actual_fps_for_save = segment_job_info["original_video_fps"]
            elif "target_fps_setting" in job_specific_metadata:
                actual_fps_for_save = (
                    job_specific_metadata["target_fps_setting"]
                    if job_specific_metadata["target_fps_setting"] != -1
                    else 24.0
                )
            else:
                actual_fps_for_save = 24.0
            _logger.debug(
                f"Loaded {len(actual_frames_to_process)} frames from numpy array. Using FPS: {actual_fps_for_save:.2f}"
            )
            if actual_frames_to_process.ndim > 0 and len(actual_frames_to_process) > 0:
                original_h_loaded, original_w_loaded = actual_frames_to_process.shape[1:3]

        elif isinstance(video_path_or_job_info, str):
            video_path_for_read = video_path_or_job_info
            start_frame_idx = 0
            num_frames_to_load_for_seg = -1

            if segment_job_info:
                start_frame_idx = segment_job_info["start_frame_raw_index"]
                num_frames_to_load_for_seg = segment_job_info["num_frames_to_load_raw"]
                target_fps_for_video_read = segment_job_info.get("gui_fps_setting_at_definition", -1)
            else:
                target_fps_for_video_read = job_specific_metadata.get("target_fps_setting", -1)

            # --- ADDED: Check for cached ffprobe info and pass it to read_video_frames ---
            cached_ffprobe_info = None
            if segment_job_info and "video_stream_ffprobe_info" in segment_job_info:
                cached_ffprobe_info = segment_job_info["video_stream_ffprobe_info"]
                _logger.debug(f"Reusing cached ffprobe info for {os.path.basename(video_path_for_read)}.")
            # -----------------------------------------------------------------------------

            (
                loaded_frames,
                fps_from_read,
                original_h,
                original_w,
                processed_h,
                processed_w,
                video_stream_info,
                ffprobe_raw_stdout,
            ) = read_video_frames(
                video_path_for_read,
                process_length=process_length_for_read if not segment_job_info else -1,
                target_fps=target_fps_for_video_read,
                target_height=user_target_height,
                target_width=user_target_width,
                start_frame_index=start_frame_idx,
                num_frames_to_load=num_frames_to_load_for_seg,
                cached_ffprobe_info=cached_ffprobe_info,
            )
            actual_frames_to_process = loaded_frames
            actual_fps_for_save = fps_from_read
            job_specific_metadata["original_height_detected"] = original_h
            job_specific_metadata["original_width_detected"] = original_w
            job_specific_metadata["processed_height"] = processed_h  # This is the actual height Decord delivered
            job_specific_metadata["processed_width"] = processed_w  # This is the actual width Decord delivered
            job_specific_metadata["video_stream_metadata"] = video_stream_info  # Keep ffprobe info
            job_specific_metadata["ffprobe_raw_stdout"] = ffprobe_raw_stdout
            _logger.debug(
                f"Loaded {len(actual_frames_to_process) if actual_frames_to_process is not None else 0} frames from video '{video_path_for_read}'. Original FPS for save: {actual_fps_for_save:.2f}"
            )

        elif isinstance(video_path_or_job_info, dict):
            source_info = video_path_or_job_info
            source_type = source_info.get("type")
            source_path = source_info.get("path")

            if "target_fps_setting" in job_specific_metadata and job_specific_metadata["target_fps_setting"] != -1.0:
                effective_output_fps = job_specific_metadata["target_fps_setting"]
            else:
                effective_output_fps = 24.0

            actual_fps_for_save = effective_output_fps

            if source_type == "image_sequence_folder":
                start_idx_for_segment = 0
                num_img_to_load_for_segment = process_length_for_read

                if segment_job_info:
                    start_idx_for_segment = segment_job_info.get("start_frame_raw_index", 0)
                    num_img_to_load_for_segment = segment_job_info.get("num_frames_to_load_raw", -1)

                frames_this_segment, h, w = read_image_sequence_as_frames(
                    folder_path=source_path,
                    num_frames_to_load=num_img_to_load_for_segment,
                    target_height=user_target_height,
                    target_width=user_target_width,
                    start_index=start_idx_for_segment,
                )
                actual_frames_to_process = frames_this_segment
                original_h_loaded, original_w_loaded = h, w
                _logger.debug(
                    f"Frames Load: Attempting to load image sequence from '{source_path}'. Target FPS: {actual_fps_for_save:.3f}. Segment Start Idx: {start_idx_for_segment}, Num to Load: {num_img_to_load_for_segment}. Loaded: {len(actual_frames_to_process) if actual_frames_to_process is not None else 0} frames."
                )

            elif source_type == "single_image_file":
                num_frames_gen = (
                    segment_job_info["num_frames_to_load_raw"] if segment_job_info else int(round(effective_output_fps))
                )

                frames_this_segment, h, w = create_frames_from_single_image(
                    image_path=source_path,
                    num_frames_to_generate=num_frames_gen,
                    target_height=user_target_height,
                    target_width=user_target_width,
                )
                actual_frames_to_process = frames_this_segment
                original_h_loaded, original_w_loaded = h, w
                _logger.debug(
                    f"Frames Load: Loaded {len(actual_frames_to_process) if actual_frames_to_process is not None else 0} frames from single image '{source_path}' at {actual_fps_for_save:.1f} FPS (for 1s clip)."
                )
            else:
                job_specific_metadata["status"] = "failure_unknown_source_type_in_dict"
                _logger.error(f"Frames Load: Unknown source type '{source_type}' in input dictionary.")
                return None, 0.0, 0, 0  # four, as the caller unpacks (Studio audit 2026-09-28)

        else:
            job_specific_metadata["status"] = "failure_no_input_source"
            _logger.error("Cannot load frames: No video path or numpy array provided.")
            return None, 0.0, 0, 0  # four, as the caller unpacks (Studio audit 2026-09-28)

        if original_h_loaded is not None:
            job_specific_metadata["original_height_loaded"] = original_h_loaded
            job_specific_metadata["original_width_loaded"] = original_w_loaded
        # Image sequences and single images come back already resized: their
        # processed size is the frames' own (audit 2026-09-28 #24 -- it was
        # never recorded, so every such job ended in a KeyError).
        if actual_frames_to_process is not None and "processed_height" not in job_specific_metadata:
            job_specific_metadata["processed_height"] = int(actual_frames_to_process.shape[1])
            job_specific_metadata["processed_width"] = int(actual_frames_to_process.shape[2])

        return (
            actual_frames_to_process,
            actual_fps_for_save,
            job_specific_metadata.get("processed_height", 0),
            job_specific_metadata.get("processed_width", 0),
        )

    def _handle_no_frames_failure(
        self,
        job_specific_metadata: dict,
        full_save_path: str,
        infer_start_time: float,
        actual_fps_for_save: float,
        segment_job_info: Optional[dict],
        save_final_output_json_config_passed_in: bool,
    ) -> Tuple[None, dict]:
        video_basename_for_log = job_specific_metadata.get("original_video_basename", "unknown_video")
        _logger.warning(f"No frames to process for {video_basename_for_log}. Skipping.")

        job_specific_metadata["status"] = "failure_no_frames"
        job_specific_metadata["frames_in_output_video"] = 0
        job_specific_metadata["processed_at_fps"] = float(
            actual_fps_for_save if actual_fps_for_save is not None and actual_fps_for_save > 0 else 0
        )

        infer_duration_sec_noframes = time.perf_counter() - infer_start_time
        job_specific_metadata["internal_processing_duration_seconds"] = round(infer_duration_sec_noframes, 2)
        job_specific_metadata["internal_processing_duration_formatted"] = format_duration(infer_duration_sec_noframes)
        job_specific_metadata["processing_timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        should_save_failure_json = (segment_job_info is not None) or (
            not segment_job_info and save_final_output_json_config_passed_in
        )

        if should_save_failure_json and full_save_path:
            individual_metadata_json_path_noframes = get_sidecar_json_filename(full_save_path)
            if save_json_file(job_specific_metadata, individual_metadata_json_path_noframes):
                job_specific_metadata["_individual_metadata_path"] = os.path.abspath(
                    individual_metadata_json_path_noframes
                )
                _logger.info(f"Saved failure JSON (no frames): {individual_metadata_json_path_noframes}")
            else:
                job_specific_metadata["_individual_metadata_path"] = None
        else:
            job_specific_metadata["_individual_metadata_path"] = None
        return None, job_specific_metadata

    def _perform_inference(
        self,
        actual_frames_to_process: np.ndarray,
        guidance_scale: float,
        num_denoising_steps: int,
        pipe_call_window_size: int,
        pipe_call_overlap: int,
        segment_job_info: Optional[dict],
        actual_processed_height: int,
        actual_processed_width: int,  # <--- RENAMED
        latent_checkpoint_path: Optional[str] = None,
        latent_checkpoint_resume: bool = False,
        latent_checkpoint_extra: Optional[dict] = None,
    ) -> np.ndarray:
        current_pipe_window_for_call = pipe_call_window_size
        current_pipe_overlap_for_call = pipe_call_overlap
        if segment_job_info:
            current_pipe_window_for_call = actual_frames_to_process.shape[0]
            current_pipe_overlap_for_call = 0

        _logger.debug(
            f"Starting inference: Frames: {actual_frames_to_process.shape[0]}, Res: {actual_frames_to_process.shape[1]}x{actual_frames_to_process.shape[2]}, Scale: {guidance_scale}, Steps: {num_denoising_steps}, Win: {current_pipe_window_for_call}, Ovlp: {current_pipe_overlap_for_call}"
        )
        with torch.inference_mode():
            res = self.pipe(
                actual_frames_to_process,
                height=actual_processed_height,  # <--- USE RENAMED PARAM
                width=actual_processed_width,  # <--- USE RENAMED PARAM
                output_type="np",
                guidance_scale=guidance_scale,
                num_inference_steps=num_denoising_steps,
                window_size=current_pipe_window_for_call,
                overlap=current_pipe_overlap_for_call,
                latent_checkpoint_path=latent_checkpoint_path,
                latent_checkpoint_resume=latent_checkpoint_resume,
                latent_checkpoint_extra=latent_checkpoint_extra,
                callback_on_step_end=self._stop_between_steps if self.stop_event is not None else None,
            ).frames[0]

        # The pipeline stashes its per-phase timings on itself rather than widening
        # the return signature; lift them onto the demo so _internal_infer can fold
        # them into the job metadata.
        self._last_throughput_stats = dict(getattr(self.pipe, "last_throughput_stats", {}) or {})

        _logger.debug(f"Inference completed. Result shape: {res.shape}")

        if res.ndim == 4 and res.shape[-1] > 1:
            res = res.sum(-1) / res.shape[-1]
            _logger.debug(f"Inference result (RGB/RGBA) averaged to grayscale. Final shape: {res.shape}")
        return res

    def _save_segment_npz(self, res: np.ndarray, full_save_path: str, job_specific_metadata: dict) -> bool:
        try:
            # Uncompressed (audit 2026-09-28 O5): compressing a segment took ~3-11 s
            # of CPU while the GPU waited, for ~13 % less disk -- float depth
            # compresses poorly. np.load reads both, so older segments still merge.
            np.savez(full_save_path, frames=res)
            job_specific_metadata["npz_segment_path"] = os.path.abspath(full_save_path)
            _logger.debug(f"Successfully saved: {full_save_path}")
            return True
        except Exception as e_save_npz:
            _logger.error(f"Failed to save: {full_save_path}. Reason: NPZ segment save error: {e_save_npz}")
            job_specific_metadata["status"] = "failure_npz_save"
            return False

    def _save_intermediate_visual_for_segment(
        self,
        res_normalized_for_visual: np.ndarray,
        actual_save_folder_for_output: str,
        output_filename_for_meta: str,
        intermediate_visual_format_to_save: str,
        actual_fps_for_save: float,
        job_specific_metadata: dict,
        output_crf: Optional[int] = None,
        output_preset: Optional[str] = None,
    ):
        base_filename_no_ext_for_visual = os.path.splitext(os.path.basename(output_filename_for_meta))[0]

        visual_save_path_or_dir = None
        visual_save_error = None
        target_fps_for_visual_float = actual_fps_for_save if actual_fps_for_save > 0 else 23.976

        save_func = None
        save_args = []
        save_kwargs = {}

        if intermediate_visual_format_to_save == "mp4":
            mp4_path = os.path.join(actual_save_folder_for_output, f"{base_filename_no_ext_for_visual}_visual.mp4")
            save_func = save_depth_visual_as_mp4_util
            save_args = [res_normalized_for_visual, mp4_path, target_fps_for_visual_float]
            save_kwargs = {"output_format": "mp4", "crf": output_crf, "preset": output_preset}
        elif intermediate_visual_format_to_save == "main10_mp4":
            mp4_path = os.path.join(actual_save_folder_for_output, f"{base_filename_no_ext_for_visual}_visual.mp4")
            save_func = save_depth_visual_as_mp4_util
            save_args = [res_normalized_for_visual, mp4_path, target_fps_for_visual_float]
            save_kwargs = {"output_format": "main10_mp4", "crf": output_crf, "preset": output_preset}
        elif intermediate_visual_format_to_save == "png_sequence":
            save_func = save_depth_visual_as_png_sequence_util
            save_args = [res_normalized_for_visual, actual_save_folder_for_output, base_filename_no_ext_for_visual]
        elif intermediate_visual_format_to_save == "exr_sequence":
            if OPENEXR_AVAILABLE_LOGIC:
                save_func = save_depth_visual_as_exr_sequence_util
                save_args = [res_normalized_for_visual, actual_save_folder_for_output, base_filename_no_ext_for_visual]
            else:
                visual_save_error = "OpenEXR libraries not available in logic module."
        elif intermediate_visual_format_to_save == "exr":
            if OPENEXR_AVAILABLE_LOGIC:
                first_frame_to_save = res_normalized_for_visual[0] if len(res_normalized_for_visual) > 0 else None
                if first_frame_to_save is None:
                    visual_save_error = "No frame data for single EXR."
                else:
                    save_func = save_depth_visual_as_single_exr_util
                    save_args = [first_frame_to_save, actual_save_folder_for_output, base_filename_no_ext_for_visual]
            else:
                visual_save_error = "OpenEXR libraries not available in logic module."
        elif intermediate_visual_format_to_save == "none":
            pass
        else:
            visual_save_error = f"Unknown intermediate visual format: {intermediate_visual_format_to_save}"

        if save_func and not visual_save_error:
            visual_save_path_or_dir, visual_save_error = save_func(*save_args, **save_kwargs)

        if visual_save_path_or_dir:
            job_specific_metadata["intermediate_visual_path"] = os.path.abspath(visual_save_path_or_dir)
            job_specific_metadata["intermediate_visual_format_saved"] = intermediate_visual_format_to_save
            _logger.debug(f"Saved intermediate segment visual in {intermediate_visual_format_to_save}")

        if visual_save_error:
            job_specific_metadata["intermediate_visual_save_error"] = visual_save_error
            _logger.error(
                f"Error saving intermediate segment visual ({intermediate_visual_format_to_save}): {visual_save_error}"
            )

    def _save_full_video_output(
        self,
        res: np.ndarray,
        full_save_path: str,
        actual_fps_for_save: float,
        job_specific_metadata: dict,
        use_percentile_norm: bool = False,
        norm_low_percentile: float = 0.1,
        norm_high_percentile: float = 99.9,
        output_crf: Optional[int] = None,
        output_preset: Optional[str] = None,
        norm_auto_top: bool = False,
    ) -> bool:
        # Raw min/max lets one extreme pixel anywhere in the sequence set the
        # bounds for every frame. On a clip containing a single very near object
        # that costs real range everywhere else, so percentile bounds are
        # available as an alternative.
        if use_percentile_norm:
            res_normalized_for_mp4, norm_stats = normalize_with_percentiles(
                res, low_percentile=norm_low_percentile, high_percentile=norm_high_percentile,
                auto_top=norm_auto_top,     # the usual frames only (user, 2026-10-12)
            )
            job_specific_metadata["output_normalization"] = norm_stats
            _logger.debug(f"Full video percentile normalization: {norm_stats}")
        else:
            res_min_full, res_max_full = res.min(), res.max()
            if res_max_full != res_min_full:
                # in place after the one copy: each step was another full-clip
                # float32 temporary (bug sweep 2026-10-09, A3); the same values
                res_normalized_for_mp4 = res - res_min_full
                res_normalized_for_mp4 /= (res_max_full - res_min_full)
            else:
                res_normalized_for_mp4 = np.zeros_like(res)
            np.clip(res_normalized_for_mp4, 0, 1, out=res_normalized_for_mp4)
            job_specific_metadata["output_normalization"] = {
                "mode": "min_max",
                "low_value": float(res_min_full),
                "high_value": float(res_max_full),
            }

        try:
            save_video_fps_full = actual_fps_for_save
            if save_video_fps_full == -1.0:
                _logger.warning(
                    f"Logic Save Video: FPS value is still -1.0 at save point, falling back to 30.0. FPS: {save_video_fps_full}"
                )
                save_video_fps_full = 30.0
            elif save_video_fps_full <= 0:
                _logger.warning(
                    f"Logic Save Video: FPS value is zero or negative at save point, falling back to 30.0. FPS: {save_video_fps_full}"
                )
                save_video_fps_full = 30.0

            output_format_for_full_video = job_specific_metadata.get("preferred_output_format", "mp4")
            # save_video only implements the two video branches; its sequence
            # branch is commented out. Falling through would silently write an
            # MP4 under a sequence selection, so say so instead of pretending.
            if output_format_for_full_video not in ("mp4", "main10_mp4"):
                _logger.warning(
                    f"Output format '{output_format_for_full_video}' is not supported by the "
                    f"full-video save path (only 'mp4' and 'main10_mp4' are). Writing 'main10_mp4' "
                    f"instead. Use segment mode if you need sequence output."
                )
                output_format_for_full_video = "main10_mp4"

            save_video(
                res_normalized_for_mp4,
                full_save_path,
                fps=save_video_fps_full,
                crf=output_crf,
                preset=output_preset,
                output_format=output_format_for_full_video,
            )
            _logger.debug(f"Successfully saved: {full_save_path}")
            return True
        except Exception as e_save_mp4:
            _logger.error(f"Failed to save: {full_save_path}. Reason: Full video MP4 save error: {e_save_mp4}")
            job_specific_metadata["status"] = "failure_mp4_save"
            return False

    def _finalize_job_metadata_and_save_json(
        self,
        job_specific_metadata: dict,
        infer_start_time: float,
        actual_fps_for_save: float,
        frames_processed_count: int,
        saved_output_successfully: bool,
        full_save_path: Optional[str],
        segment_job_info: Optional[dict],
        save_final_output_json_config_passed_in: bool,
    ):
        if "internal_processing_duration_seconds" not in job_specific_metadata:
            infer_duration_sec = time.perf_counter() - infer_start_time
            job_specific_metadata["internal_processing_duration_seconds"] = round(infer_duration_sec, 2)
            job_specific_metadata["internal_processing_duration_formatted"] = format_duration(infer_duration_sec)

        job_specific_metadata["processed_at_fps"] = float(actual_fps_for_save)
        job_specific_metadata["frames_in_output_video"] = frames_processed_count

        # Throughput, i.e. how fast the job ran -- distinct from `processed_at_fps`,
        # which is the frame rate of the video itself. Covers the whole job (frame
        # load, inference, encode/save), so it is the number that predicts how long
        # the next run of the same shape will take.
        job_duration_sec = job_specific_metadata.get("internal_processing_duration_seconds") or 0.0
        if frames_processed_count > 0 and job_duration_sec > 0:
            job_specific_metadata["throughput_fps"] = round(frames_processed_count / job_duration_sec, 4)
        else:
            job_specific_metadata["throughput_fps"] = 0.0

        if saved_output_successfully and job_specific_metadata["status"] == "pending":
            job_specific_metadata["status"] = "success"
        elif job_specific_metadata["status"] == "pending":
            job_specific_metadata["status"] = "failure_at_finalize"

        if "processing_timestamp_utc" not in job_specific_metadata:
            job_specific_metadata["processing_timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        should_save_this_job_json = (segment_job_info is not None) or (
            not segment_job_info and save_final_output_json_config_passed_in
        )

        if should_save_this_job_json and full_save_path:
            individual_metadata_json_path = get_sidecar_json_filename(full_save_path)
            if save_json_file(job_specific_metadata, individual_metadata_json_path):
                job_specific_metadata["_individual_metadata_path"] = os.path.abspath(individual_metadata_json_path)
            else:
                if job_specific_metadata["status"] == "success":
                    job_specific_metadata["status"] = "failure_metadata_save"
                job_specific_metadata["_individual_metadata_path"] = None
        elif job_specific_metadata.get("_individual_metadata_path") is None:
            job_specific_metadata["_individual_metadata_path"] = None

    def _internal_infer(
        self,
        video_path_or_job_info_dict: Union[str, dict],
        frames_array_if_provided: Optional[np.ndarray],
        num_denoising_steps: int,
        guidance_scale: float,
        base_output_folder: str,
        user_target_height: int,
        user_target_width: int,
        seed_val: int,
        original_video_basename: str,
        process_length_for_read: int,
        gui_target_fps_for_job: float,
        pipe_call_window_size: int,
        pipe_call_overlap: int,
        segment_job_info: Optional[dict] = None,
        should_save_intermediate_visuals: bool = False,
        intermediate_visual_format_to_save: str = "none",
        save_final_output_json_config_passed_in: bool = False,
        latent_checkpoint_enabled: bool = False,
        latent_checkpoint_resume: bool = False,
        stabilize_depth: bool = False,
        stabilize_ref_index: Optional[int] = None,
        stabilize_smooth_window: int = 0,
        use_percentile_norm: bool = False,
        norm_low_percentile: float = 0.1,
        norm_high_percentile: float = 99.9,
        output_crf: Optional[int] = None,
        output_preset: Optional[str] = None,
        preferred_output_format: str = "mp4",
        norm_auto_top: bool = False,
    ) -> Tuple[Optional[str], dict]:

        infer_start_time = time.perf_counter()
        set_seed(seed_val)
        _logger.debug(
            f"Starting inference job for: {original_video_basename} (Seed: {seed_val}, Segment: {bool(segment_job_info)}, ID: {segment_job_info.get('segment_id', -1) if segment_job_info else -1})"
        )

        actual_save_folder_for_output, output_filename_for_meta, full_save_path = self._setup_paths(
            base_output_folder, original_video_basename, segment_job_info
        )

        job_specific_metadata = self._initialize_job_metadata(
            guidance_scale,
            num_denoising_steps,
            user_target_height,
            user_target_width,
            seed_val,
            gui_target_fps_for_job,
            segment_job_info,
            output_filename_for_meta,
            pipe_call_window_size,
            pipe_call_overlap,
            original_video_basename,
        )

        # The full-video save path reads this key. Nothing used to write it, so it
        # always fell through to the "mp4" default and the GUI's Output Format
        # selection was silently ignored for non-segment jobs. Set before
        # _load_frames so it is present no matter which branch that takes.
        job_specific_metadata["preferred_output_format"] = preferred_output_format

        actual_frames_to_process, actual_fps_for_save, actual_processed_h, actual_processed_w = self._load_frames(
            video_path_or_job_info=video_path_or_job_info_dict,
            frames_array_if_provided=frames_array_if_provided,
            process_length_for_read=process_length_for_read,
            user_target_height=user_target_height,
            user_target_width=user_target_width,
            segment_job_info=segment_job_info,
            job_specific_metadata=job_specific_metadata,
        )
        # Update job_specific_metadata with the *actual* processed H/W if not already set (e.g. from np.array input)
        job_specific_metadata["processed_height"] = actual_processed_h
        job_specific_metadata["processed_width"] = actual_processed_w

        if job_specific_metadata["status"] == "failure_no_input_source":
            self._finalize_job_metadata_and_save_json(
                job_specific_metadata,
                infer_start_time,
                0.0,
                0,
                False,
                full_save_path,
                segment_job_info,
                save_final_output_json_config_passed_in,
            )
            return None, job_specific_metadata

        if actual_frames_to_process is None or actual_frames_to_process.shape[0] == 0:
            return self._handle_no_frames_failure(
                job_specific_metadata,
                full_save_path,
                infer_start_time,
                actual_fps_for_save if actual_fps_for_save is not None else 0.0,
                segment_job_info,
                save_final_output_json_config_passed_in,
            )

        # Latent checkpointing is only worth it for full-video jobs. Segments are
        # short enough to simply re-run, and per-segment checkpoints would litter
        # the segment folder.
        latent_ckpt_path = None
        if latent_checkpoint_enabled and not segment_job_info:
            latent_ckpt_path = self.get_latent_checkpoint_path(base_output_folder, original_video_basename)

        inference_result = self._perform_inference(
            actual_frames_to_process,
            guidance_scale,
            num_denoising_steps,
            pipe_call_window_size,
            pipe_call_overlap,
            segment_job_info,
            actual_processed_h,
            actual_processed_w,
            latent_checkpoint_path=latent_ckpt_path,
            latent_checkpoint_resume=latent_checkpoint_resume,
            latent_checkpoint_extra={"seed": int(seed_val)},
        )

        throughput_phases = dict(getattr(self, "_last_throughput_stats", {}) or {})
        if throughput_phases:
            job_specific_metadata["throughput_phases"] = throughput_phases

        # --- Affine stabilization -------------------------------------------
        # Runs here, on the float result, before either save branch normalizes
        # or quantizes. DepthCrafter anchors each segment's scale to the
        # previous one and that chain only runs forward, so late-arriving near
        # content makes a static scene drift. The drift is a pure global affine,
        # so a per-frame gain and offset fit back onto a reference frame removes
        # it without touching relative depth.
        #
        # Note this is post-denoise, so it does NOT participate in the latent
        # checkpoint header -- stabilization settings can be changed and a
        # checkpointed job still resumes correctly.
        if stabilize_depth and inference_result is not None and inference_result.ndim == 3:
            try:
                inference_result, stabilize_stats = stabilize_depth_affine(
                    inference_result,
                    ref_index=stabilize_ref_index,
                    smooth_window=stabilize_smooth_window,
                    in_place=True,
                )
                job_specific_metadata["depth_stabilization"] = stabilize_stats
            except Exception as e_stab:
                # Stabilization is a correction, not a requirement. A failure
                # here must not cost the user an entire denoising run.
                _logger.error(f"Depth stabilization failed, saving unstabilized result instead: {e_stab}")
                job_specific_metadata["depth_stabilization"] = {"error": str(e_stab)}

        if inference_result is not None and inference_result.ndim >= 3:  # Should be (T, H, W)
            job_specific_metadata["processed_height"] = inference_result.shape[1]
            job_specific_metadata["processed_width"] = inference_result.shape[2]
        else:
            _logger.warning(
                f"Inference result was not valid for dimension extraction. Inference_result shape: {inference_result.shape if inference_result is not None else 'None'}"
            )
            job_specific_metadata["processed_height"] = "N/A"
            job_specific_metadata["processed_width"] = "N/A"

        saved_output_successfully = False
        if segment_job_info:
            saved_output_successfully = self._save_segment_npz(inference_result, full_save_path, job_specific_metadata)
            if (
                saved_output_successfully
                and should_save_intermediate_visuals
                and intermediate_visual_format_to_save != "none"
                and inference_result.size > 0
            ):
                res_min_seg, res_max_seg = inference_result.min(), inference_result.max()
                if res_max_seg != res_min_seg:
                    res_normalized_for_visual = (inference_result - res_min_seg) / (res_max_seg - res_min_seg)
                else:
                    res_normalized_for_visual = np.zeros_like(inference_result)
                res_normalized_for_visual = np.clip(res_normalized_for_visual, 0, 1)

                self._save_intermediate_visual_for_segment(
                    res_normalized_for_visual,
                    actual_save_folder_for_output,
                    output_filename_for_meta,
                    intermediate_visual_format_to_save,
                    actual_fps_for_save,
                    job_specific_metadata,
                    output_crf=output_crf,
                    output_preset=output_preset,
                )
        else:
            saved_output_successfully = self._save_full_video_output(
                inference_result,
                full_save_path,
                actual_fps_for_save,
                job_specific_metadata,
                use_percentile_norm=use_percentile_norm,
                norm_low_percentile=norm_low_percentile,
                norm_high_percentile=norm_high_percentile,
                output_crf=output_crf,
                output_preset=output_preset,
                norm_auto_top=norm_auto_top,
            )

        if latent_ckpt_path and saved_output_successfully and os.path.exists(latent_ckpt_path):
            try:
                os.remove(latent_ckpt_path)
                _logger.debug(f"Removed latent checkpoint after successful save: {latent_ckpt_path}")
            except Exception as e_ckpt_rm:
                _logger.warning(f"Could not remove latent checkpoint {latent_ckpt_path}: {e_ckpt_rm}")

        self._finalize_job_metadata_and_save_json(
            job_specific_metadata,
            infer_start_time,
            actual_fps_for_save,
            actual_frames_to_process.shape[0],
            saved_output_successfully,
            full_save_path,
            segment_job_info,
            save_final_output_json_config_passed_in,
        )

        # --- End-of-job throughput summary -------------------------------------
        # INFO level on purpose: this is the line the console run is for. It is the
        # whole-job rate (load + inference + save), so it is directly comparable
        # between segment jobs and full-video jobs of the same source.
        job_label = (
            f"Segment {segment_job_info.get('segment_id', -1) + 1}/{segment_job_info.get('total_segments', 0)}"
            if segment_job_info
            else "Full video"
        )
        frames_done = job_specific_metadata.get("frames_in_output_video") or 0
        overall_fps = job_specific_metadata.get("throughput_fps") or 0.0
        duration_txt = job_specific_metadata.get("internal_processing_duration_formatted", "?")

        if frames_done and overall_fps:
            summary = (
                f"[{job_label}] {original_video_basename} - {frames_done} frames in {duration_txt} "
                f"| {overall_fps:.2f} fps overall"
            )
            breakdown = _format_throughput_breakdown(throughput_phases)
            if breakdown:
                summary += f" ({breakdown})"
            pipeline_stats = (throughput_phases or {}).get("pipeline") or {}
            peak_vram = pipeline_stats.get("peak_vram_gb")
            if peak_vram:
                summary += f" | peak VRAM {peak_vram:.2f} GB"
            if pipeline_stats.get("resumed_from_checkpoint"):
                summary += " [resumed from latent checkpoint]"
            _logger.info(summary)
        else:
            _logger.info(
                f"[{job_label}] {original_video_basename} - finished in {duration_txt} "
                f"with status '{job_specific_metadata['status']}' (no throughput to report)"
            )

        _logger.debug(
            f"Inference job for {original_video_basename} finished. Status: {job_specific_metadata['status']}. Duration: {job_specific_metadata['internal_processing_duration_formatted']}. Output: {full_save_path if saved_output_successfully else 'N/A'}"
        )
        return full_save_path if saved_output_successfully else None, job_specific_metadata

    def run(
        self,
        video_path_or_frames_or_info: Union[str, np.ndarray, dict],
        num_denoising_steps: int,
        guidance_scale: float,
        base_output_folder: str,
        gui_window_size: int,
        gui_overlap: int,
        process_length_for_read_full_video: int,
        target_height: int,
        target_width: int,
        seed: int,
        original_video_basename_override: Optional[str] = None,
        segment_job_info_param: Optional[dict] = None,
        keep_intermediate_npz_config: bool = False,
        intermediate_segment_visual_format_config: str = "none",
        save_final_json_for_this_job_config: bool = False,
        latent_checkpoint_config: bool = False,
        latent_checkpoint_resume_config: bool = False,
        stabilize_depth_config: bool = False,
        stabilize_ref_index_config: Optional[int] = None,
        stabilize_smooth_window_config: int = 0,
        use_percentile_norm_config: bool = False,
        norm_low_percentile_config: float = 0.1,
        norm_high_percentile_config: float = 99.9,
        norm_auto_top_config: bool = False,
        output_crf_config: Optional[int] = None,
        output_preset_config: Optional[str] = None,
        output_format_config: str = "mp4",
    ):

        video_path_or_info_for_infer_load: Union[str, dict]
        frames_array_input = None
        original_basename_for_job: str

        current_job_spec: dict
        if segment_job_info_param:
            current_job_spec = segment_job_info_param
            video_path_or_info_for_infer_load = current_job_spec["video_path"]
            if current_job_spec["source_type"] != "video_file":
                video_path_or_info_for_infer_load = {
                    "type": current_job_spec["source_type"],
                    "path": current_job_spec["video_path"],
                    "gui_fps": current_job_spec["gui_fps_setting_at_definition"],
                }
            original_basename_for_job = current_job_spec["original_basename"]
        elif isinstance(video_path_or_frames_or_info, dict):
            current_job_spec = video_path_or_frames_or_info
            video_path_or_info_for_infer_load = current_job_spec["video_path"]
            if current_job_spec["source_type"] != "video_file":
                video_path_or_info_for_infer_load = {
                    "type": current_job_spec["source_type"],
                    "path": current_job_spec["video_path"],
                    "gui_fps": current_job_spec["gui_fps_setting_at_definition"],
                }
            original_basename_for_job = current_job_spec["original_basename"]
        elif isinstance(video_path_or_frames_or_info, str):
            current_job_spec = {}
            video_path_or_info_for_infer_load = video_path_or_frames_or_info
            original_basename_for_job = (
                original_video_basename_override
                if original_video_basename_override
                else os.path.splitext(os.path.basename(video_path_or_frames_or_info))[0]
            )
            _logger.warning(
                f"DepthCrafterDemo.run received a direct video path '{video_path_or_frames_or_info}' without a full job specification dictionary. Assuming default FPS of -1 (original). It is recommended to pass a job dictionary for full metadata tracking."
            )
        elif isinstance(video_path_or_frames_or_info, np.ndarray):
            current_job_spec = {}
            frames_array_input = video_path_or_frames_or_info
            video_path_or_info_for_infer_load = None
            if not original_video_basename_override:
                _logger.error(
                    "DepthCrafterDemo.run: original_video_basename_override is required for np.ndarray input."
                )
                raise ValueError("original_video_basename_override needed for np.ndarray input.")
            original_basename_for_job = original_video_basename_override
        else:
            _logger.error(
                f"DepthCrafterDemo.run: video_path_or_frames must be str or np.ndarray or dict, got {type(video_path_or_frames_or_info).__name__}."
            )
            raise ValueError("video_path_or_frames_or_info invalid.")

        gui_fps_setting_for_job = current_job_spec.get("gui_fps_setting_at_definition", -1.0)
        if gui_fps_setting_for_job == -1.0 and isinstance(video_path_or_info_for_infer_load, dict):
            gui_fps_setting_for_job = video_path_or_info_for_infer_load.get("gui_fps", -1.0)
        if gui_fps_setting_for_job == -1.0 and not frames_array_input and video_path_or_info_for_infer_load is None:
            _logger.warning(
                f"Run Logic: Target FPS setting missing in job spec for {original_basename_for_job} and frames/video path not directly available to infer. Defaulting to -1 (original)."
            )

        should_save_visuals_for_infer = False
        intermediate_visual_fmt_for_infer = "none"
        if segment_job_info_param and keep_intermediate_npz_config:
            should_save_visuals_for_infer = True
            intermediate_visual_fmt_for_infer = intermediate_segment_visual_format_config

        effective_process_length_for_infer = (
            current_job_spec.get("num_frames_to_load_raw")
            if segment_job_info_param
            else process_length_for_read_full_video
        )

        save_path, job_metadata_dict = self._internal_infer(
            video_path_or_job_info_dict=video_path_or_info_for_infer_load,
            frames_array_if_provided=frames_array_input,
            num_denoising_steps=num_denoising_steps,
            guidance_scale=guidance_scale,
            base_output_folder=base_output_folder,
            user_target_width=target_width,
            user_target_height=target_height,
            seed_val=seed,
            original_video_basename=original_basename_for_job,
            process_length_for_read=effective_process_length_for_infer,
            gui_target_fps_for_job=gui_fps_setting_for_job,
            pipe_call_window_size=gui_window_size,
            pipe_call_overlap=gui_overlap,
            segment_job_info=segment_job_info_param,
            should_save_intermediate_visuals=should_save_visuals_for_infer,
            intermediate_visual_format_to_save=intermediate_visual_fmt_for_infer,
            save_final_output_json_config_passed_in=save_final_json_for_this_job_config,
            latent_checkpoint_enabled=latent_checkpoint_config,
            latent_checkpoint_resume=latent_checkpoint_resume_config,
            stabilize_depth=stabilize_depth_config,
            stabilize_ref_index=stabilize_ref_index_config,
            stabilize_smooth_window=stabilize_smooth_window_config,
            use_percentile_norm=use_percentile_norm_config,
            norm_low_percentile=norm_low_percentile_config,
            norm_high_percentile=norm_high_percentile_config,
            norm_auto_top=norm_auto_top_config,
            output_crf=output_crf_config,
            output_preset=output_preset_config,
            preferred_output_format=output_format_config,
        )
        gc.collect()
        torch.cuda.empty_cache()
        return save_path, job_metadata_dict
