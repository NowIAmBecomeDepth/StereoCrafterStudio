"""Batch video processing module.

Handles batch video splatting workflow including multi-resolution output,
sidecar integration, auto-convergence, and move-to-finished functionality.
"""

import gc
import glob
import logging
import os
import queue
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

# torch before decord: not used here by name, but loading decord's native
# library first crashes the process on import (Windows) -- keep it.
import torch  # noqa: F401
from decord import VideoReader, cpu

from core.common.cli_utils import print_job_header
from core.common.file_organizer import move_files_to_finished
from core.common.video_io import (
    FFmpegDepthPipeReader,
    _infer_depth_bit_depth,
    get_video_stream_info,
    read_video_frames,
)
from core.common.gpu_utils import release_cuda_memory
from core.common import keyframes
from .depth_processing import (
    compute_global_depth_stats,
    load_pre_rendered_depth,
)
from .render_processor import RenderProcessor, resolve_subtitle_mask

logger = logging.getLogger(__name__)


@dataclass
class ProcessingTask:
    """Represents a single video processing task configuration.

    Attributes:
        name: Task name (e.g., "Full-Resolution", "Low-Resolution")
        output_subdir: Output subdirectory name
        set_pre_res: Whether to set preprocessing resolution
        target_width: Target output width (-1 for original)
        target_height: Target output height (-1 for original)
        batch_size: Frames per batch
        is_low_res: Whether this is a low-resolution task
    """

    name: str
    output_subdir: str
    set_pre_res: bool
    target_width: int
    target_height: int
    batch_size: int
    is_low_res: bool


@dataclass
class ProcessingSettings:
    """Processing settings for batch operations.

    Attributes:
        input_source_clips: Path to source clips folder or file
        input_depth_maps: Path to depth maps folder or file
        output_splatted: Output directory for splatted videos
        max_disp: Maximum disparity percentage
        process_length: Number of frames to process (-1 for all)
        enable_full_resolution: Whether to output full resolution
        full_res_batch_size: Batch size for full resolution
        enable_low_resolution: Whether to output low resolution
        low_res_width: Width for low resolution output
        low_res_height: Height for low resolution output
        low_res_batch_size: Batch size for low resolution
        dual_output: Whether to output both left and right eyes
        zero_disparity_anchor: Convergence plane value (0.0-1.0)
        enable_global_norm: Whether to enable global normalization
        match_depth_res: Whether to match depth resolution to video
        move_to_finished: Whether to move processed files to finished folder
        output_crf: Quality CRF value (legacy)
        output_crf_full: CRF for full resolution
        output_crf_low: CRF for low resolution
        depth_gamma: Gamma correction for depth
        depth_dilate_size_x: Horizontal dilation size
        depth_dilate_size_y: Vertical dilation size
        depth_blur_size_x: Horizontal blur size
        depth_blur_size_y: Vertical blur size
        depth_dilate_left: Left eye dilation
        depth_blur_left: Left eye blur
        auto_convergence_mode: Auto-convergence mode ("Off", "Average", "Peak", "Hybrid")
        enable_sidecar_gamma: Whether sidecar controls gamma
        enable_sidecar_blur_dilate: Whether sidecar controls blur/dilate
        single_finished_source_folder: Optional finished folder for single-file mode
        single_finished_depth_folder: Optional finished folder for single-file mode
    """

    input_source_clips: str
    input_depth_maps: str
    output_splatted: str
    max_disp: float = 20.0
    process_length: int = -1
    enable_full_resolution: bool = True
    full_res_batch_size: int = 10
    enable_low_resolution: bool = False
    low_res_width: int = 1920
    low_res_height: int = 1080
    low_res_batch_size: int = 50
    # Auto Size (Studio, 2026-10-11): clip path -> (w, h) for a clip whose Low Res
    # pass runs at its own shape's size, or None for low_res_width/height. Unset: all
    # clips at low_res_width/height, as before.
    low_res_size_for: Optional[Any] = None
    dual_output: bool = False
    dual_eye: bool = False
    dual_eye_bias: float = 0.5
    flip_horizontal: bool = False
    strict_ffmpeg_decode: bool = False

    output_name_suffix: str = ""
    zero_disparity_anchor: float = 0.5
    enable_global_norm: bool = False
    match_depth_res: bool = True
    move_to_finished: bool = True
    output_crf: int = 23
    output_crf_full: int = 23
    output_crf_low: int = 23
    depth_gamma: float = 1.0
    depth_boost_thresh: float = 0.85
    depth_boost_amount: float = 0.0
    depth_boost_curve: float = 2.0
    depth_boost_knee: float = 0.0
    depth_boost_prefilter_radius: int = 0
    depth_boost_prefilter_eps: float = 1e-3
    depth_pop_amount: float = 0.0
    # Rain (core.splatting.rain): the GUI's, a clip's sidecar wins
    rain_mode: str = "off"
    rain_any_direction: bool = False
    rain_depth: float = 0.5
    rain_amount: float = 1.0
    rain_layers: int = 3
    rain_strands: bool = True
    rain_lights: bool = True
    rain_pop: float = 0.0
    rain_preset: str = "downpour"
    rain_guard: float = 0.0
    rain_particle: str = "rain"
    rain_placement: str = "front"
    rain_grow: int = 0
    rain_reflections: bool = False
    rain_spread: float = 1.0
    rain_spacing: float = 1.0
    rain_conv: float = 0.0
    submask_grow: float = 0.0           # the Detect Text mask's own grow / soften (px)
    submask_soften: float = 0.0
    submask_clean: float = 0.0          # Text Clean: the text taken out of the depth (px)
    submask_conv: float = 0.0           # Text Conv: the text's own Convergence (offset; 0 = the screen)
    depth_dilate_size_x: float = 0.0
    depth_dilate_size_y: float = 0.0
    depth_blur_size_x: float = 0.0
    depth_blur_size_y: float = 0.0
    depth_dilate_left: float = 0.0
    depth_blur_left: float = 0.0
    depth_blur_left_mix: float = 0.5
    auto_convergence_mode: str = "Off"
    enable_sidecar_gamma: bool = True
    enable_sidecar_blur_dilate: bool = True
    single_finished_source_folder: Optional[str] = None
    single_finished_depth_folder: Optional[str] = None
    # Process Selected (Studio, 2026-09-30): only these source clips, in the
    # batch's own order; the From/To range doesn't apply. None: every clip.
    only_videos: Optional[List[str]] = None
    # NEW FIELDS for deeper orchestration
    multi_map: bool = False
    selected_depth_map: str = ""
    color_tags_mode: str = "Auto"
    codec: str = "Auto"
    encoding_encoder: str = "Auto"
    encoding_quality: str = "Auto"
    encoding_tune: str = "Auto"
    bit_depth: str = "Auto"
    pix_fmt: str = "Auto"
    encoding_nvenc_lookahead_enabled: bool = False
    encoding_nvenc_lookahead: int = 16
    encoding_nvenc_spatial_aq: bool = False
    encoding_nvenc_temporal_aq: bool = False
    encoding_nvenc_aq_strength: int = 8
    dnxhr_fullres_split: bool = False
    dnxhr_profile: str = "HQX"
    # MP4 or MKV for a non-DNxHR output (Encoding Settings' Container; user, 2026-10-25)
    output_container: str = "MP4"
    is_test_mode: bool = False
    test_target_frame_idx: Optional[int] = None
    skip_lowres_preproc: bool = False
    sidecar_ext: str = ".fssidecar"
    sidecar_folder: str = ""
    track_dp_total_true_on_render: bool = False
    mask_mode: str = "SC"
    # M2S warp backend and mask tuning -- see core.splatting.m2s_backends.
    splat_backend: str = "SC"
    hybrid_sensitivity: float = 50.0
    hybrid_taper: bool = False
    m2s_fix_strength: float = 1.0

    # Special FX > Frame Break. Picked up automatically from config["frame_break_bars"]
    # / config["frame_break_thresh"] by from_config (Splatting GUI already writes
    # both into its config dict). Per-clip sidecar values still take precedence --
    # see _get_video_specific_settings -- these are only the GUI-level fallback.
    frame_break_bars: float = 0.0
    frame_break_thresh: float = 1.0
    frame_break_erode_left: float = 0.0
    frame_break_erode_right: float = 0.0
    frame_break_erode_global: float = 0.0
    frame_break_edge_blur: float = 0.0

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: dict, **overrides) -> "ProcessingSettings":
        """Build a ProcessingSettings from a flat config dict.

        Keys in *config* are matched to dataclass field names.  Numeric
        fields are coerced automatically.  Any explicit *overrides* are
        applied last so the caller can inject values that don't live in
        the config dict (e.g. ``sidecar_folder``, ``is_test_mode``).

        Unknown keys in *config* are silently ignored.
        """
        import dataclasses as _dc

        field_names = {f.name for f in _dc.fields(cls)}
        field_types = {f.name: f.type for f in _dc.fields(cls)}

        # --- Key aliases (config key -> dataclass field) ---------------
        _ALIASES = {
            "convergence_point": "zero_disparity_anchor",
            "batch_size": "full_res_batch_size",
            "pre_res_width": "low_res_width",
            "pre_res_height": "low_res_height",
            "enable_full_resolution": "enable_full_resolution",
            "enable_low_resolution": "enable_low_resolution",
        }

        kwargs: dict = {}
        for key, value in config.items():
            field = _ALIASES.get(key, key)
            if field not in field_names:
                continue
            # Light type coercion
            target = field_types.get(field, str)
            fraction = False
            try:
                if target is float:
                    value = float(value)
                elif target is int and not isinstance(value, bool):
                    # "1e3" is 1000; "2.5" is refused plainly -- left as text it
                    # failed later as "'<=' not supported between 'str' and
                    # 'int'" (Studio audit 2026-09-28, minor)
                    number = float(value)
                    fraction = not number.is_integer()
                    value = value if fraction else int(number)
                elif target is bool and not isinstance(value, bool):
                    value = str(value).lower() in ("true", "1", "yes")
            except (ValueError, TypeError):
                pass
            if fraction:
                raise ValueError(f"{key} must be a whole number, not {value!r}.")
            kwargs[field] = value

        kwargs.update(overrides)
        return cls(**kwargs)


def _saved_or(sidecar_data: dict, key: str, fallback):
    """The sidecar's value when it saved one, else the GUI's."""
    v = sidecar_data.get(key)
    return fallback if v is None else v


def boost_kwargs_for(vid_settings: dict) -> dict:
    """The Depth Boost settings a clip hands the render: when its own boost
    is on, or a keyframe turns it on somewhere (Studio audit 2026-09-28: only
    the clip's own amount counted, so a boost keyframed up from 0 rendered on
    the flat 1.0 peak anchor and the default threshold / curve -- the preview
    had the clip's peak). Empty when no boost is asked for anywhere."""
    amount = float(vid_settings.get("boost_amount", 0.0) or 0.0)
    pop = float(vid_settings.get("pop_amount", 0.0) or 0.0)
    keyed = False
    track = vid_settings.get("keyframe_track")
    if track is not None and not track.is_empty():
        keyed = any(float((values or {}).get("boost_amount_var", 0.0) or 0.0) > 0.0
                    or float((values or {}).get("pop_amount_var", 0.0) or 0.0) > 0.0
                    for _f, _kind, values in track.items_sorted())
    if amount <= 0.0 and pop <= 0.0 and not keyed:
        return {}
    # Pop-Out rides along: it needs the same clip peak anchor (user, 2026-10-01)
    return {
        "pop_amount": pop,
        "boost_thresh": float(vid_settings.get("boost_thresh", 0.85)),
        "boost_amount": amount,
        "boost_curve": float(vid_settings.get("boost_curve", 2.0)),
        "boost_knee": float(vid_settings.get("boost_knee", 0.0)),
        # Edge Clean is off in Studio (user, 2026-10-11): no difference in testing, and a
        # radius saved in an older sidecar would run with nothing on the page showing it
        "boost_prefilter_radius": 0,
        "boost_prefilter_eps": float(vid_settings.get("boost_prefilter_eps", 1e-3)),
        "depth_peak_raw": float(vid_settings.get("depth_max_raw", 1.0)),
    }


@dataclass
class BatchSetupResult:
    """Result of batch processing setup.

    Attributes:
        input_videos: List of input video paths
        is_single_file_mode: Whether running in single-file mode
        finished_source_folder: Optional finished folder for source
        finished_depth_folder: Optional finished folder for depth
        error: Optional error message
    """

    input_videos: List[str] = field(default_factory=list)
    is_single_file_mode: bool = False
    finished_source_folder: Optional[str] = None
    finished_depth_folder: Optional[str] = None
    error: Optional[str] = None


#: What to change when a render runs out of graphics memory (release check 2026-10-17: the batch said only
#: "1 failed")
OOM_HINT = ("A render ran out of graphics memory. Lower Batch (Process Resolution), or close other programs "
            "using the graphics card, then run it again.")


class BatchProcessor:
    """Handles batch video splatting workflow.

    Manages the processing queue, worker threads, and progress
    reporting for batch video conversion. Coordinates multiple
    processing tasks (Full/Low resolution) per video.
    """

    def __init__(self, progress_queue: queue.Queue, stop_event: threading.Event, sidecar_manager: Optional[Any] = None):
        """Initialize batch processor.

        Args:
            progress_queue: Queue for progress updates to GUI
            stop_event: Event for cancellation

            sidecar_manager: Optional sidecar manager for loading/saving settings
        """
        self.progress_queue = progress_queue
        self.stop_event = stop_event
        self.result: Dict[str, Any] = {}     # the last run's counts (run_batch_process)
        self._clip_ok = False
        # Stop After Clip: set while a clip renders, the batch ends before the
        # next one (the clip itself finishes whole). Optional; the Qt Studio sets it.
        self.stop_after_clip: Optional[threading.Event] = None

        self.sidecar_manager = sidecar_manager
        self.logger = logging.getLogger(__name__)

    def setup_batch_processing(self, settings: ProcessingSettings) -> BatchSetupResult:
        """Setup batch processing, validate inputs, and determine mode.

        Handles input path validation, mode determination (single file vs batch),
        and creates necessary 'finished' folders.

        Args:
            settings: Processing settings

        Returns:
            BatchSetupResult with setup information or error
        """
        input_source = settings.input_source_clips
        input_depth = settings.input_depth_maps
        output_dir = settings.output_splatted

        is_source_file = os.path.isfile(input_source)
        is_source_dir = os.path.isdir(input_source)
        is_depth_file = os.path.isfile(input_depth)
        is_depth_dir = os.path.isdir(input_depth)

        result = BatchSetupResult()

        if is_source_file and is_depth_file:
            # Single-file mode
            result.is_single_file_mode = True
            self.logger.debug("==> Running in single file mode. Files will not be moved to 'finished' folders.")
            result.input_videos.append(input_source)
            os.makedirs(output_dir, exist_ok=True)

        elif is_source_dir and is_depth_dir:
            # Batch (folder) mode
            self.logger.debug("==> Running in batch (folder) mode.")

            if settings.move_to_finished:
                result.finished_source_folder = os.path.join(input_source, "finished")
                result.finished_depth_folder = os.path.join(input_depth, "finished")
                os.makedirs(result.finished_source_folder, exist_ok=True)
                os.makedirs(result.finished_depth_folder, exist_ok=True)
                self.logger.debug("Finished folders enabled for batch mode.")
            else:
                self.logger.debug("Finished folders DISABLED by user setting. Files will remain in input folders.")

            os.makedirs(output_dir, exist_ok=True)

            # Collect video files
            video_extensions = ("*.mp4", "*.avi", "*.mov", "*.mkv")
            for ext in video_extensions:
                result.input_videos.extend(glob.glob(os.path.join(input_source, ext)))
            # Detect Text's <clip>_submask.mp4 sits beside the clips: not a clip
            from core.common.submask_files import is_subtitle_mask
            # in number order, clip_110 before clip_1009, as Upcoming Jobs lists them (user, 2026-10-29)
            from core.common.clip_names import natural_key
            result.input_videos = sorted((v for v in result.input_videos if not is_subtitle_mask(v)),
                                         key=lambda p: natural_key(os.path.basename(p)))

        else:
            result.error = (
                "==> Error: Input Source Clips and Input Depth Maps must both be "
                "either files or directories. Skipping processing."
            )
            self.logger.error(result.error)
            return result

        if not result.input_videos:
            result.error = f"No video files found in {input_source}"
            self.logger.error(result.error)

        return result

    def get_defined_tasks(self, settings: ProcessingSettings) -> List[ProcessingTask]:
        """Get list of processing tasks based on settings.

        Args:
            settings: Processing settings

        Returns:
            List of ProcessingTask objects
        """
        tasks = []

        if settings.enable_full_resolution:
            tasks.append(
                ProcessingTask(
                    name="Full-Resolution",
                    output_subdir="hires",
                    set_pre_res=False,
                    target_width=-1,
                    target_height=-1,
                    batch_size=settings.full_res_batch_size,
                    is_low_res=False,
                )
            )

        if settings.enable_low_resolution:
            tasks.append(
                ProcessingTask(
                    name="Low-Resolution",
                    output_subdir="lowres",
                    set_pre_res=True,
                    target_width=settings.low_res_width,
                    target_height=settings.low_res_height,
                    batch_size=settings.low_res_batch_size,
                    is_low_res=True,
                )
            )

        return tasks

    def select_videos(self, setup_result: "BatchSetupResult", settings: ProcessingSettings, from_index: int = 0,
                      to_index: Optional[int] = None, video_list: Optional[List[Dict]] = None) -> List[str]:
        """The batch's clips, in its order: the ticked ones (Process Selected), a range, or all of them.
        High VRAM Mode's parallel processor hands out the same list (core.splatting.parallel)."""
        input_videos = list(setup_result.input_videos)
        if setup_result.is_single_file_mode:
            return input_videos
        only = getattr(settings, "only_videos", None)
        if only:
            keep = {os.path.normcase(os.path.abspath(p)) for p in only}
            input_videos = [v for v in input_videos if os.path.normcase(os.path.abspath(v)) in keep]
            self.logger.info(f"Process Selected: {len(input_videos)} ticked clip(s).")
        elif video_list:
            total = len(video_list)
            start = max(0, min(total, from_index))
            end = max(start + 1, min(total, to_index or total))
            selected = video_list[start:end]
            input_videos = [e.get("source_video") for e in selected if e.get("source_video")]
        else:
            total = len(input_videos)
            start = max(0, min(total, from_index))
            end = max(start + 1, min(total, to_index or total))
            input_videos = input_videos[start:end]
        return input_videos

    def run_batch_process(
        self,
        settings: ProcessingSettings,
        from_index: int = 0,
        to_index: Optional[int] = None,
        video_list: Optional[List[Dict]] = None,
    ) -> None:
        """Run batch processing loop.

        Before its "finished" message it sends ("result", counts): the clips
        done / failed / skipped, plus the error that ended the run early or
        the note saying why nothing was processed -- so a run that produced
        nothing is not reported as a success."""
        result = self.result = {"done": 0, "failed": 0, "skipped": 0, "error": "", "note": ""}
        renderer = None
        try:
            # Setup
            setup_result = self.setup_batch_processing(settings)
            if setup_result.error:
                self.logger.error(setup_result.error)
                result["error"] = setup_result.error
                return

            is_single_file = setup_result.is_single_file_mode
            if not setup_result.input_videos:
                self.logger.error("No input videos found for processing.")
                result["note"] = "No input videos found for processing."
                return
            input_videos = self.select_videos(setup_result, settings, from_index, to_index, video_list)
            if not input_videos:
                self.logger.error("No input videos left to process.")
                result["note"] = "No input videos left to process."
                return

            tasks = self.get_defined_tasks(settings)
            if not tasks:
                self.logger.error("No processing tasks defined.")
                result["error"] = "No processing tasks defined."
                return

            total_tasks = len(input_videos) * len(tasks)
            self.progress_queue.put(("total", total_tasks))

            # Initialize RenderProcessor
            renderer = RenderProcessor(stop_event=self.stop_event, progress_queue=self.progress_queue)

            # Process each video
            task_counter = 0
            total_videos = len(input_videos)
            for vid_idx, vid_path in enumerate(input_videos, start=1):
                if self.stop_event.is_set():
                    break
                if vid_idx > 1 and self.stop_after_clip is not None and self.stop_after_clip.is_set():
                    self.logger.info("==> Stopped after the clip, as asked; the rest are left for the next run.")
                    result["halted"] = True
                    break

                print_job_header(vid_idx, total_videos, vid_path)

                # Which CLIP this is, which the progress bar cannot say: its
                # maximum is len(videos) * len(tasks), so "12/30" there means
                # tasks, and a clip producing a full-res and a low-res pass
                # advances it twice. The GUI shows this beside the filename.
                self.progress_queue.put(("clip", (vid_idx, total_videos)))

                tasks_processed = self._process_single_video_orchestration(
                    video_path=vid_path,
                    settings=settings,
                    renderer=renderer,
                    initial_task_counter=task_counter,
                    is_single_file_mode=is_single_file,
                )
                task_counter += tasks_processed
                if self._clip_ok:
                    result["done"] += 1
                elif not self.stop_event.is_set():     # a stopped clip is neither
                    result["failed"] += 1

        except Exception as e:
            self.logger.error(f"Batch processing error: {e}", exc_info=True)
            self.progress_queue.put(("status", f"Error: {e}"))
            result["error"] = f"{e.__class__.__name__}: {e}"
            if type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower():
                result["hint"] = OOM_HINT
        finally:
            if getattr(renderer, "out_of_vram", False):
                result["hint"] = OOM_HINT
            release_cuda_memory()
            self.progress_queue.put(("result", dict(result)))
            self.progress_queue.put("finished")

    def _process_single_video_orchestration(
        self,
        video_path: str,
        settings: ProcessingSettings,
        renderer: RenderProcessor,
        initial_task_counter: int,
        is_single_file_mode: bool,
    ) -> int:
        """Handles the full processing lifecycle for a single video.

        Leaves ``self._clip_ok``: every task of the clip rendered."""
        self._clip_ok = False
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        video_name_out = (
            f"{video_name}{getattr(settings, 'output_name_suffix', '')}"
            if getattr(settings, "output_name_suffix", "")
            else video_name
        )
        self.logger.info(f"==> Processing Video: {video_name}")
        self.progress_queue.put(("update_info", {"filename": video_name}))

        # 1. Resolve Settings (Sidecar + GUI)
        vid_settings = self._get_video_specific_settings(video_path, settings, is_single_file_mode)
        if vid_settings.get("error"):
            self.logger.error(f"Settings error for {video_name}: {vid_settings['error']}")
            n = len(self.get_defined_tasks(settings))
            self.progress_queue.put(("tasks_done", initial_task_counter + n))
            return n

        # 2. Convergence: the clip's sidecar anchor, else the global one. Auto-
        # convergence is a preview pre-pass (its result goes to the slider and
        # the sidecar); a batch never estimated it per clip -- the placeholder
        # that stood here returned the global anchor, i.e. this same value
        # (Studio audit 2026-09-28 tidy-up).
        conv_val = vid_settings["convergence_plane"]

        # 3. Tasks Loop
        tasks = self.get_defined_tasks(settings)
        # Auto Size: a clip of another shape than the one the page was set for runs
        # its Low Res pass at its own shape's size (Studio's low_res_size_for)
        size_for = getattr(settings, "low_res_size_for", None)
        if size_for is not None:
            try:
                own = size_for(video_path)
            except Exception:
                self.logger.debug(f"[Batch] No Auto Size for {video_name}", exc_info=True)
                own = None
            if own:
                tasks = [replace(t, target_width=int(own[0]), target_height=int(own[1])) if t.is_low_res else t
                         for t in tasks]
                self.logger.info(f"[Batch] {video_name}: Auto Size, Low Res {int(own[0])}x{int(own[1])} "
                                 f"for its shape.")
        all_tasks_successful = True
        processed_count = 0

        # Optimization: Group tasks by their source resolution requirements
        # Full-res and Low-res tasks currently require different decord resolutions.
        # But we can at least avoid re-reading metadata if we were clever.
        # For now, let's just make sure the loop is intact and adding logging.
        self.logger.debug(f"[Batch] Video {video_name} has {len(tasks)} tasks: {[t.name for t in tasks]}")

        for task in tasks:
            if self.stop_event.is_set():
                all_tasks_successful = False
                break

            self.progress_queue.put(("status", f"Processing {task.name} for {video_name}"))

            # Initialize Readers for this task/resolution
            # Note: This still opens readers per task because different resolutions require different Decord instances.
            # To fix 'loading twice' truly, we would need to load at original res and resize in renderer.
            readers = self._initialize_readers(video_path, vid_settings["actual_depth_map_path"], settings, task)
            if not readers:
                self.logger.error(f"[Batch] Failed to initialize readers for {task.name}")
                all_tasks_successful = False
                processed_count += 1
                self.progress_queue.put(("tasks_done", initial_task_counter + processed_count))
                continue

            # Enable Global Normalization: the clip's own depth range, scanned
            # once before its first pass. Nothing ever computed it -- the render
            # divided raw depth codes by the 0..1 defaults and clipped white
            # (audit 2026-09-28 #18).
            if vid_settings.get("enable_global_norm") and "global_min" not in vid_settings:
                self._scan_global_depth_range(vid_settings, readers, task)

            # Run Rendering
            encoding_options = {
                "codec": settings.codec,
                "encoder": settings.encoding_encoder,
                "quality": settings.encoding_quality,
                "tune": settings.encoding_tune,
                "bit_depth": settings.bit_depth,
                "pix_fmt": settings.pix_fmt,
                "nvenc_lookahead_enabled": settings.encoding_nvenc_lookahead_enabled,
                "nvenc_lookahead": settings.encoding_nvenc_lookahead,
                "nvenc_spatial_aq": settings.encoding_nvenc_spatial_aq,
                "nvenc_temporal_aq": settings.encoding_nvenc_temporal_aq,
                "nvenc_aq_strength": settings.encoding_nvenc_aq_strength,
            }
            # --- Depth Boost pass-through ---
            # Only forwarded when active so clips WITHOUT boost render fine even if
            # RenderProcessor hasn't been updated to accept these kwargs yet. A
            # boosted clip against an unpatched RenderProcessor fails loudly
            # (TypeError) rather than silently rendering without the boost.
            boost_kwargs = boost_kwargs_for(vid_settings)

            # --- Subtitle mask pass-through ---
            # Same version-safety pattern as Depth Boost: only forwarded when
            # the clip's sidecar enables it AND <clip>_submask.mp4 exists next
            # to the source, so an unpatched RenderProcessor keeps working
            # unless the feature is actually requested. Masked pixels are
            # pinned to the convergence anchor (screen depth) in the renderer.
            if vid_settings.get("submask_enabled"):
                _sub_mask = resolve_subtitle_mask(video_path)
                if _sub_mask:
                    boost_kwargs["subtitle_mask_path"] = _sub_mask
                    boost_kwargs["subtitle_mask_grow"] = float(vid_settings.get("submask_grow", 0.0) or 0.0)
                    boost_kwargs["subtitle_mask_soften"] = float(vid_settings.get("submask_soften", 0.0) or 0.0)
                    boost_kwargs["subtitle_mask_clean"] = float(vid_settings.get("submask_clean", 0.0) or 0.0)
                    boost_kwargs["subtitle_mask_conv"] = float(vid_settings.get("submask_conv", 0.0) or 0.0)
                    self.logger.info(f"[SUBMASK] Using subtitle mask for {video_name}: {_sub_mask}")
                else:
                    self.logger.warning(
                        f"[SUBMASK] Sidecar enables subtitle mask for {video_name} but no "
                        f"_submask.mp4 was found next to the source; rendering unpinned."
                    )

            _dreg = vid_settings.get("dilate_regions")
            if isinstance(_dreg, dict) and _dreg.get("keyframes"):
                boost_kwargs["dilate_regions"] = _dreg
                self.logger.info(
                    f"[LOCAL DILATE] {len(_dreg.get('keyframes', []))} region "
                    f"keyframe(s) for {video_name}."
                )

            # --- Rain pass-through (core.splatting.rain): only when on, as the others ---
            _rain = vid_settings.get("rain") or {}
            if str(_rain.get("mode", "off")).lower() in ("moving", "static"):
                boost_kwargs["rain"] = dict(_rain)
                self.logger.info(f"[RAIN] {_rain.get('mode')} for {video_name}: depth {_rain.get('depth')}, "
                                 f"amount {_rain.get('amount')}{', any direction' if _rain.get('any_direction') else ''}")

            # --- Frame Break pass-through ---
            # Same version-safety pattern as Depth Boost: only forwarded when
            # active, so an unpatched RenderProcessor keeps working unless the
            # feature is actually requested. When forwarded, this only makes
            # RenderProcessor write the sidecar-adjacent protrusion mask --
            # bars are composited later, in Merging, not into this splat output.
            _fb_bars = float(vid_settings.get("frame_break_bars", 0.0) or 0.0)
            if _fb_bars > 0.0:
                boost_kwargs["frame_break_bars"] = _fb_bars
                boost_kwargs["frame_break_thresh"] = float(vid_settings.get("frame_break_thresh", 1.0))
                boost_kwargs["frame_break_erode_left"] = float(vid_settings.get("frame_break_erode_left", 0.0))
                boost_kwargs["frame_break_erode_right"] = float(vid_settings.get("frame_break_erode_right", 0.0))
                boost_kwargs["frame_break_erode_global"] = float(vid_settings.get("frame_break_erode_global", 0.0))
                boost_kwargs["frame_break_edge_blur"] = float(vid_settings.get("frame_break_edge_blur", 0.0))
                self.logger.info(
                    f"[FRAME BREAK] bars={_fb_bars:.3f} thresh={vid_settings.get('frame_break_thresh', 1.0):.3f} "
                    f"for {video_name}; writing protrusion mask alongside this task's output."
                )

            success = renderer.render_video(
                input_video_reader=readers["source"],
                depth_map_reader=readers["depth"],
                total_frames_to_process=readers["total_frames"],
                processed_fps=readers["fps"],
                output_video_path_base=os.path.join(
                    settings.output_splatted, task.output_subdir, f"{video_name_out}.mp4"
                ),
                target_output_height=readers["target_h"],
                target_output_width=readers["target_w"],
                max_disp=vid_settings["max_disparity_percentage"],
                keyframe_track=vid_settings["keyframe_track"],
                batch_size=task.batch_size,
                dual_output=settings.dual_output,
                dual_eye=settings.dual_eye,
                dual_eye_bias=settings.dual_eye_bias,
                zero_disparity_anchor_val=conv_val,
                video_stream_info=readers["source_info"],
                input_bias=vid_settings["input_bias"],
                assume_raw_input=not vid_settings["enable_global_norm"],
                global_depth_min=vid_settings.get("global_min", 0.0),
                global_depth_max=vid_settings.get("global_max", 1.0),
                depth_stream_info=readers["depth_info"],
                user_output_crf=settings.output_crf_low if task.is_low_res else settings.output_crf_full,
                is_low_res_task=task.is_low_res,
                depth_gamma=vid_settings["depth_gamma"],
                depth_dilate_size_x=vid_settings["depth_dilate_size_x"],
                depth_dilate_size_y=vid_settings["depth_dilate_size_y"],
                depth_blur_size_x=vid_settings["depth_blur_size_x"],
                depth_blur_size_y=vid_settings["depth_blur_size_y"],
                depth_dilate_left=vid_settings["depth_dilate_left"],
                depth_blur_left=vid_settings["depth_blur_left"],
                depth_blur_left_mix=vid_settings["depth_blur_left_mix"],
                flip_horizontal=vid_settings["flip_horizontal"],
                skip_lowres_preproc=settings.skip_lowres_preproc,
                color_tags_mode=settings.color_tags_mode,
                encoding_options=encoding_options,
                dnxhr_fullres_split=settings.dnxhr_fullres_split,
                dnxhr_profile=settings.dnxhr_profile,
                output_container=settings.output_container,
                is_test_mode=settings.is_test_mode,
                test_target_frame_idx=settings.test_target_frame_idx,
                mask_mode=settings.mask_mode,
                splat_backend=settings.splat_backend,
                hybrid_sensitivity=settings.hybrid_sensitivity,
                hybrid_taper=settings.hybrid_taper,
                m2s_fix_strength=settings.m2s_fix_strength,
                **boost_kwargs,
            )

            if not success:
                self.logger.error(f"[Batch] Task {task.name} failed for {video_name}")
                all_tasks_successful = False

            processed_count += 1
            # "processed" (from RenderProcessor) is frames within this pass;
            # the batch's own count is tasks, told apart so the overall
            # progress stays in tasks (audit 2026-09-28 #20).
            self.progress_queue.put(("tasks_done", initial_task_counter + processed_count))

            # Release this task's video handles before the next task opens its own,
            # and before the move-to-finished step below. An open depth handle is
            # what blocks the move on Windows with WinError 32.
            self._release_readers(readers)
            readers = None

        if all_tasks_successful:
            self.logger.info(f"==> Successfully processed all tasks for {video_name}.")
        self._clip_ok = all_tasks_successful and not self.stop_event.is_set()

        # 4. Move to Finished (if enabled and all tasks for this video succeeded)
        #
        # A stopped batch never moves anything, even for a clip whose own tasks
        # happened to finish before the user hit Stop. Moving the source out from
        # under a run the user interrupted is the one mistake that cannot be
        # undone by re-running: the clip is no longer in the folder to re-render
        # from. RenderProcessor now reports a cancelled render as failed, so this
        # is the second line of defence rather than the only one.
        stopped = self.stop_event.is_set()
        if stopped and all_tasks_successful and settings.move_to_finished:
            self.logger.info(
                f"==> Batch was stopped; leaving {video_name} source files where they are."
            )
        should_move = all_tasks_successful and settings.move_to_finished and not stopped

        if should_move:
            self.logger.info(f"==> All tasks successful for {video_name}. Moving source files to finished.")

            files_to_move = []

            # Source video
            src_dir = os.path.dirname(video_path)
            if is_single_file_mode and settings.single_finished_source_folder:
                dest_source = settings.single_finished_source_folder
            else:
                dest_source = src_dir
            files_to_move.append((video_path, dest_source))
            # ...and its Detect Text mask goes with it, so a re-run from
            # finished/ still finds it beside the clip
            _sub_mask_path = resolve_subtitle_mask(video_path)
            if _sub_mask_path:
                files_to_move.append((_sub_mask_path, dest_source))

            # Depth map
            depth_path = vid_settings["actual_depth_map_path"]
            depth_dir = os.path.dirname(depth_path)
            if is_single_file_mode and settings.single_finished_depth_folder:
                dest_depth = settings.single_finished_depth_folder
            else:
                dest_depth = depth_dir
            files_to_move.append((depth_path, dest_depth))

            # Sidecars
            sidecar_patterns = [
                os.path.join(settings.sidecar_folder, f"{video_name}_depth{settings.sidecar_ext}"),
                os.path.join(src_dir, f"{video_name}{settings.sidecar_ext}"),
                os.path.join(depth_dir, f"{video_name}_depth{settings.sidecar_ext}"),
            ]

            for sc_path in sidecar_patterns:
                if os.path.exists(sc_path):
                    # For sidecars, move to source dest if it was in source dir, else depth dest
                    sc_dir = os.path.dirname(sc_path)
                    if sc_dir == src_dir:
                        files_to_move.append((sc_path, dest_source))
                    else:
                        files_to_move.append((sc_path, dest_depth))

            # Perform move
            moved, failed, failed_list = move_files_to_finished(
                files_to_move=files_to_move, logger=self.logger, wait_before_move=0.5
            )

            if failed > 0:
                self.logger.warning(f"Failed to move {failed} files for {video_name}: {failed_list}")
            else:
                self.logger.info(f"Successfully moved {moved} source/sidecar files to finished.")

        return len(tasks)

    def _get_video_specific_settings(
        self, video_path: str, settings: ProcessingSettings, is_single_file_mode: bool
    ) -> dict:
        """Resolve settings for a specific video, merging sidecar and GUI defaults."""
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        sidecar_path = os.path.join(settings.sidecar_folder, f"{video_name}_depth{settings.sidecar_ext}")

        sidecar_data = {}
        if self.sidecar_manager and os.path.exists(sidecar_path):
            # one that won't read fails the clip: load_sidecar_data would answer the
            # defaults, and the clip rendered with neither its own settings nor the
            # page's (bug sweep 2026-10-09 A11)
            err = self.sidecar_manager.read_error(sidecar_path)
            if err:
                return {"error": f"its sidecar {os.path.basename(sidecar_path)} can't be read ({err}). "
                                 f"Fix or remove it, then run the clip again."}
            sidecar_data = self.sidecar_manager.load_sidecar_data(sidecar_path) or {}

        # Resolve Depth Map Path
        actual_depth_path = self._resolve_depth_path(video_path, settings, sidecar_data, is_single_file_mode)
        if not actual_depth_path:
            return {"error": "Depth map not found"}

        # Merging Logic
        res = {
            "actual_depth_map_path": actual_depth_path,
            "convergence_plane": sidecar_data.get("convergence_plane", settings.zero_disparity_anchor),
            "max_disparity_percentage": sidecar_data.get("max_disparity", settings.max_disp),
            # Keyframes travel with the clip, like the dilate regions: a batch run covers many clips and each one
            # animates on its own timeline, so the track comes from THIS
            # clip's sidecar rather than from whatever the GUI has loaded.
            "keyframe_track": keyframes.KeyframeTrack.from_dict(
                sidecar_data.get(keyframes.SIDECAR_KEY)
            ),
            "input_bias": sidecar_data.get("input_bias", 0.0),
            "depth_gamma": sidecar_data.get("gamma", settings.depth_gamma)
            if settings.enable_sidecar_gamma
            else settings.depth_gamma,
            "boost_thresh": sidecar_data.get("boost_thresh", settings.depth_boost_thresh),
            "boost_amount": sidecar_data.get("boost_amount", settings.depth_boost_amount),
            "boost_curve": sidecar_data.get("boost_curve", settings.depth_boost_curve),
            "boost_knee": sidecar_data.get("boost_knee", settings.depth_boost_knee),
            "boost_prefilter_radius": 0,  # Edge Clean is off in Studio (2026-10-11), sidecars too
            "boost_prefilter_eps": sidecar_data.get(
                "boost_prefilter_eps", settings.depth_boost_prefilter_eps
            ),
            # Pop-Out is per clip (user, 2026-10-09): the clip's own sidecar value, else 0 --
            # never the page's, which is whatever clip the preview had open. Process Single
            # Clip renders what the page shows.
            "pop_amount": settings.depth_pop_amount if is_single_file_mode
            else float(sidecar_data.get("pop_out", 0.0) or 0.0),
            # Peak-anchored boost: RAW clip depth max, written by the splat GUI
            # (auto-saved from the GN clip scan). 1.0 = flat/legacy anchor.
            "depth_max_raw": float(sidecar_data.get("depth_max_raw", 1.0) or 1.0),
            # Localized Dilate regions travel verbatim from the sidecar.
            "dilate_regions": sidecar_data.get("dilate_regions"),
            # a sidecar that never saved Rain (None, or no key) leaves the GUI's setting standing
            "rain": {
                "mode": _saved_or(sidecar_data, "rain_mode", settings.rain_mode),
                "any_direction": _saved_or(sidecar_data, "rain_any_direction", settings.rain_any_direction),
                "depth": _saved_or(sidecar_data, "rain_depth", settings.rain_depth),
                "amount": _saved_or(sidecar_data, "rain_amount", settings.rain_amount),
                "layers": _saved_or(sidecar_data, "rain_layers", settings.rain_layers),
                "strands": _saved_or(sidecar_data, "rain_strands", settings.rain_strands),
                "lights": _saved_or(sidecar_data, "rain_lights", settings.rain_lights),
                "pop": _saved_or(sidecar_data, "rain_pop", settings.rain_pop),
                "preset": _saved_or(sidecar_data, "rain_preset", settings.rain_preset),
                "guard": _saved_or(sidecar_data, "rain_guard", settings.rain_guard),
                "particle": _saved_or(sidecar_data, "rain_particle", settings.rain_particle),
                "placement": _saved_or(sidecar_data, "rain_placement", settings.rain_placement),
                "grow": _saved_or(sidecar_data, "rain_grow", settings.rain_grow),
                "reflections": _saved_or(sidecar_data, "rain_reflections", settings.rain_reflections),
                "spread": _saved_or(sidecar_data, "rain_spread", settings.rain_spread),
                "spacing": _saved_or(sidecar_data, "rain_spacing", settings.rain_spacing),
                "conv": _saved_or(sidecar_data, "rain_conv", settings.rain_conv),
            },
            "depth_dilate_size_x": sidecar_data.get("depth_dilate_size_x", settings.depth_dilate_size_x)
            if settings.enable_sidecar_blur_dilate
            else settings.depth_dilate_size_x,
            "depth_dilate_size_y": sidecar_data.get("depth_dilate_size_y", settings.depth_dilate_size_y)
            if settings.enable_sidecar_blur_dilate
            else settings.depth_dilate_size_y,
            "depth_blur_size_x": sidecar_data.get("depth_blur_size_x", settings.depth_blur_size_x)
            if settings.enable_sidecar_blur_dilate
            else settings.depth_blur_size_x,
            "depth_blur_size_y": sidecar_data.get("depth_blur_size_y", settings.depth_blur_size_y)
            if settings.enable_sidecar_blur_dilate
            else settings.depth_blur_size_y,
            "depth_dilate_left": sidecar_data.get("depth_dilate_left", settings.depth_dilate_left),
            "depth_blur_left": sidecar_data.get("depth_blur_left", settings.depth_blur_left),
            "depth_blur_left_mix": sidecar_data.get("depth_blur_left_mix", settings.depth_blur_left_mix),
            "flip_horizontal": sidecar_data.get("flip_horizontal", settings.flip_horizontal),
            "anchor_source": "Sidecar" if "convergence_plane" in sidecar_data else "GUI",
            # Subtitle protection mask (written by the GUI's Subs editor).
            "submask_enabled": bool(sidecar_data.get("submask_enabled", False)),
            "submask_grow": float(_saved_or(sidecar_data, "submask_grow", settings.submask_grow) or 0.0),
            "submask_soften": float(_saved_or(sidecar_data, "submask_soften", settings.submask_soften) or 0.0),
            "submask_clean": float(_saved_or(sidecar_data, "submask_clean", settings.submask_clean) or 0.0),
            "submask_conv": float(_saved_or(sidecar_data, "submask_conv", settings.submask_conv) or 0.0),
            # Frame Break: per-clip, scene-specific, same "sidecar overrides
            # GUI" pattern as Depth Boost above.
            "frame_break_bars": sidecar_data.get("frame_break_bars", settings.frame_break_bars),
            "frame_break_thresh": sidecar_data.get("frame_break_thresh", settings.frame_break_thresh),
            "frame_break_erode_left": sidecar_data.get("frame_break_erode_left", settings.frame_break_erode_left),
            "frame_break_erode_right": sidecar_data.get("frame_break_erode_right", settings.frame_break_erode_right),
            "frame_break_erode_global": sidecar_data.get("frame_break_erode_global", settings.frame_break_erode_global),
            "frame_break_edge_blur": sidecar_data.get("frame_break_edge_blur", settings.frame_break_edge_blur),
            "enable_global_norm": settings.enable_global_norm and ("convergence_plane" not in sidecar_data),  # Policy
        }
        return res

    def _resolve_depth_path(
        self, video_path: str, settings: ProcessingSettings, sidecar_data: dict, is_single_file: bool
    ) -> Optional[str]:
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        if is_single_file:
            return settings.input_depth_maps if os.path.isfile(settings.input_depth_maps) else None

        base_folder = settings.input_depth_maps
        if settings.multi_map:
            # Sidecar check
            selected = sidecar_data.get("selected_depth_map") or settings.selected_depth_map
            if selected:
                candidate = os.path.join(base_folder, selected, f"{video_name}_depth.mp4")
                if os.path.exists(candidate):
                    return candidate
                candidate = os.path.join(base_folder, selected, f"{video_name}_depth.npz")
                if os.path.exists(candidate):
                    return candidate

        # Default check
        c_mp4 = os.path.join(base_folder, f"{video_name}_depth.mp4")
        if os.path.exists(c_mp4):
            return c_mp4
        c_npz = os.path.join(base_folder, f"{video_name}_depth.npz")
        if os.path.exists(c_npz):
            return c_npz
        return None

    def _initialize_readers(
        self, video_path: str, depth_path: str, settings: ProcessingSettings, task: ProcessingTask
    ) -> Optional[dict]:
        try:
            source, fps, orig_h, orig_w, target_h, target_w, info, total = read_video_frames(
                video_path,
                settings.process_length,
                set_pre_res=task.set_pre_res,
                pre_res_width=task.target_width,
                pre_res_height=task.target_height,
                strict_ffmpeg_decode=settings.strict_ffmpeg_decode,
                high_bit_source=True,      # a 10-bit source warped at 10 bits, not Decord's 8
            )

            # Depth reader setup
            depth_target_h, depth_target_w = (
                (orig_h, orig_w) if (task.is_low_res and not settings.skip_lowres_preproc) else (target_h, target_w)
            )
            depth_match = True if task.is_low_res else settings.match_depth_res

            try:
                d_reader, d_total, d_h, d_w, d_info = load_pre_rendered_depth(
                    depth_path,
                    process_length=settings.process_length,
                    target_height=depth_target_h,
                    target_width=depth_target_w,
                    match_resolution_to_target=depth_match,
                )
            except Exception as e:
                self.logger.error(f"load_pre_rendered_depth failed: {e}")
                raise

            # Handle case where frame counts might be strings or None
            try:
                total_val = int(total) if total not in (None, "", "N/A") else 0
                d_total_val = int(d_total) if d_total not in (None, "", "N/A") else 0
            except (ValueError, TypeError):
                total_val = 0
                d_total_val = 0

            if total_val != d_total_val and total_val > 0 and d_total_val > 0:
                self.logger.error(f"Frame count mismatch: source={total_val}, depth={d_total_val}")
                return None

            return {
                "source": source,
                "depth": d_reader,
                "fps": fps,
                "target_h": target_h,
                "target_w": target_w,
                "source_info": info,
                "depth_info": d_info,
                "total_frames": total,
                "orig_h": orig_h,
                "orig_w": orig_w,
            }
        except Exception as e:
            import traceback

            self.logger.error(f"Reader init error: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def _scan_global_depth_range(self, vid_settings: dict, readers: dict, task: ProcessingTask) -> None:
        """Fill vid_settings["global_min"/"global_max"] for this clip from its
        depth map (compute_depth_normalization: bit-depth aware, native size)."""
        depth_path = vid_settings["actual_depth_map_path"]
        info = get_video_stream_info(depth_path) or {}
        try:
            width, height = int(info.get("width")), int(info.get("height"))
        except (TypeError, ValueError):
            width = height = -1
        try:
            total = int(readers.get("total_frames") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:          # the 10-bit pipe reader reads exactly this many frames
            try:
                total = int(info.get("nb_frames") or 0) or len(VideoReader(depth_path, ctx=cpu(0)))
            except Exception:
                total = 0
        self.progress_queue.put(("status", f"Scanning the depth range of {os.path.basename(depth_path)}"))
        _raw, lo, hi, _peak = self.compute_depth_normalization(
            depth_path, True, total, max(1, int(task.batch_size or 1)), width, height)
        if hi <= lo:
            self.logger.warning(f"Global Normalization: no depth range found in {os.path.basename(depth_path)}; "
                                f"rendering it as raw depth instead.")
            vid_settings["enable_global_norm"] = False
            return
        vid_settings["global_min"], vid_settings["global_max"] = float(lo), float(hi)
        self.logger.info(f"Global Normalization: depth range {lo:.1f}..{hi:.1f} for {os.path.basename(depth_path)}")

    def _release_readers(self, readers: Optional[dict]) -> None:
        """Close and drop any open video handles produced by _initialize_readers.

        The depth entry is a live lazy reader (Decord VideoReader or
        FFmpegDepthPipeReader) that keeps an OS-level handle on the depth file.
        On Windows an open handle makes the move-to-finished step fail with
        WinError 32, so handles must be released before files are relocated.
        The source entry is decoded frame data rather than a reader, but it is
        dropped here too so a task's working set does not survive into the next.
        """
        if not readers:
            return

        for key in ("depth", "source"):
            obj = readers.pop(key, None)
            if obj is None:
                continue
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            del obj

        readers.clear()
        gc.collect()

    def validate_settings(self, settings: ProcessingSettings) -> Tuple[bool, str]:
        """Validate processing settings.

        Args:
            settings: Processing settings to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check max disparity
        if settings.max_disp <= 0:
            return False, "Max Disparity must be positive."

        # Check convergence anchor
        if not (0.0 <= settings.zero_disparity_anchor <= 1.0):
            return False, "Zero Disparity Anchor must be between 0.0 and 1.0."

        # Check batch sizes
        if settings.enable_full_resolution and settings.full_res_batch_size <= 0:
            return False, "Full Resolution Batch Size must be positive."

        if settings.enable_low_resolution:
            if settings.low_res_width <= 0 or settings.low_res_height <= 0:
                return False, "Low-Resolution Width and Height must be positive."
            if settings.low_res_batch_size <= 0:
                return False, "Low-Resolution Batch Size must be positive."

        # Check at least one resolution enabled
        if not (settings.enable_full_resolution or settings.enable_low_resolution):
            return False, "At least one resolution (Full or Low) must be enabled."

        # Check gamma
        if settings.depth_gamma <= 0:
            return False, "Depth Gamma must be positive."

        # Check dilate/blur values
        if (
            settings.depth_dilate_size_x < -10.0
            or settings.depth_dilate_size_x > 30.0
            or settings.depth_dilate_size_y < -10.0
            or settings.depth_dilate_size_y > 30.0
        ):
            return False, "Depth Dilate Sizes (X/Y) must be between -10 and 30."

        if settings.depth_blur_size_x < 0 or settings.depth_blur_size_y < 0:
            return False, "Depth Blur Sizes (X/Y) must be non-negative."

        # below 0 erodes the left edges (user, 2026-10-30: to take back a Dilate X there)
        if settings.depth_dilate_left < -10.0 or settings.depth_dilate_left > 20.0:
            return False, "Dilate Left must be between -10 and 20."

        if settings.depth_blur_left < 0 or settings.depth_blur_left > 20:
            return False, "Blur Left must be between 0 and 20."

        return True, ""

    def compute_depth_normalization(
        self,
        depth_map_path: str,
        enable_global_norm: bool,
        total_frames: int,
        batch_size: int,
        actual_width: int,
        actual_height: int,
    ) -> Tuple[bool, float, float, float]:
        """Compute depth normalization parameters.

        Performs global depth stats scan when global normalization is enabled,
        or determines scaling factor for raw input mode.

        Args:
            depth_map_path: Path to depth map video
            enable_global_norm: Whether global normalization is enabled
            total_frames: Total frames in depth video
            batch_size: Processing batch size
            actual_width: Actual depth width
            actual_height: Actual depth height

        Returns:
            Tuple of (assume_raw_mode, global_min, global_max, max_content_value)
        """
        assume_raw_mode = not enable_global_norm
        global_min, global_max = 0.0, 1.0
        max_content_value = 1.0

        # First scan: the max content value -- only without Global Normalisation. With it,
        # the second scan's max is the same number, and this was a whole extra decode of
        # the depth map per clip (bug sweep 2026-10-09, A4).
        if assume_raw_mode:
            raw_reader = None
            try:
                depth_info = get_video_stream_info(depth_map_path)
                bit_depth = _infer_depth_bit_depth(depth_info)
                pix_fmt = str((depth_info or {}).get("pix_fmt", ""))

                if bit_depth > 8:
                    raw_reader = FFmpegDepthPipeReader(
                        depth_map_path,
                        out_w=actual_width,
                        out_h=actual_height,
                        bit_depth=bit_depth,
                        num_frames=total_frames,
                        pix_fmt=pix_fmt,
                    )
                else:
                    raw_reader = VideoReader(depth_map_path, ctx=cpu(0), width=actual_width, height=actual_height)

                if len(raw_reader) > 0:
                    _, max_content_value = compute_global_depth_stats(
                        depth_map_reader=raw_reader, total_frames=total_frames, chunk_size=batch_size
                    )
                    self.logger.debug(f"Max content depth scanned: {max_content_value:.3f}.")
                else:
                    self.logger.error("RAW depth reader has no frames for content scan.")
            except Exception as e:
                self.logger.error(f"Failed to scan max content depth: {e}")
            finally:
                if raw_reader:
                    if hasattr(raw_reader, "close"):
                        try:
                            raw_reader.close()
                        except Exception:
                            pass
                    del raw_reader
                    gc.collect()

        # Second scan: Global normalization (if enabled)
        if not assume_raw_mode:
            self.logger.info("==> Global Depth Normalization selected. Starting global depth stats pre-pass.")

            raw_reader = None
            try:
                depth_info = get_video_stream_info(depth_map_path)
                bit_depth = _infer_depth_bit_depth(depth_info)
                pix_fmt = str((depth_info or {}).get("pix_fmt", ""))

                if bit_depth > 8:
                    raw_reader = FFmpegDepthPipeReader(
                        depth_map_path,
                        out_w=actual_width,
                        out_h=actual_height,
                        bit_depth=bit_depth,
                        num_frames=total_frames,
                        pix_fmt=pix_fmt,
                    )
                else:
                    raw_reader = VideoReader(depth_map_path, ctx=cpu(0), width=actual_width, height=actual_height)

                if len(raw_reader) > 0:
                    global_min, global_max = compute_global_depth_stats(
                        depth_map_reader=raw_reader, total_frames=total_frames, chunk_size=batch_size
                    )
                    max_content_value = global_max      # the first scan's number, without its decode
                    self.logger.debug("Successfully computed global stats from RAW reader.")
                else:
                    self.logger.error("RAW depth reader has no frames.")
            except Exception as e:
                self.logger.error(f"Failed to compute global stats: {e}")
                global_min, global_max = 0.0, 1.0
            finally:
                if raw_reader:
                    if hasattr(raw_reader, "close"):
                        try:
                            raw_reader.close()
                        except Exception:
                            pass
                    del raw_reader
                    gc.collect()
        else:
            self.logger.debug("==> No Normalization (Assume Raw 0-1 Input) selected. Skipping global stats pre-pass.")

            # Determine scaling factor for raw input mode
            if max_content_value <= 256.0 and max_content_value > 1.0:
                global_max = 255.0
            elif max_content_value > 256.0 and max_content_value <= 1024.0:
                global_max = 1023.0
            else:
                global_max = 1023.0
                self.logger.warning(f"Max content value is unusual ({max_content_value:.2f}). Using fallback 1023.0.")
            global_min = 0.0

        return assume_raw_mode, global_min, global_max, max_content_value

    def check_stop_requested(self) -> bool:
        """Check if processing stop has been requested.

        Returns:
            True if stop event is set
        """
        return self.stop_event.is_set()
