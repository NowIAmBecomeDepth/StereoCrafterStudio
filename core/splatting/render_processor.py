"""Main splatting render processor.

Implements the core video splatting algorithm, handling the processing loop,
GPU computation (forward warping), and FFmpeg encoding.
"""

import math
import os
import time
import logging
import queue
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from decord import VideoReader

from core.common.video_io import (
    as_uint8,
    start_ffmpeg_pipe_process,
    start_ffmpeg_pipe_process_dnxhr,
    start_ffmpeg_pipe_process_mask,
    resolve_frame_break_mask,
)
from core.common.gpu_utils import release_cuda_memory
from core.common.partial_output import discard, part_path, publish, reap
from core.common.cli_utils import draw_progress_bar
from .forward_warp import ForwardWarpStereo
from .depth_processing import process_depth_batch, normalize_and_gamma_depth
from .depth_gpu import normalize_and_gamma_depth_gpu
from core.common import frame_break
from core.common import dilate_regions as _dilreg
from core.common.keyframes import KeyframeTrack

logger = logging.getLogger(__name__)


#: Keyframe variable name -> the render_video parameter it overrides.
#:
#: The keys are the GUI variable names a keyframe snapshot is stored under, so
#: this is the render-side twin of splatting_gui's KEYFRAME_SETTINGS_VAR_MAP
#: (which maps the same snapshot onto the PREVIEW's settings dict). Both are
#: driven by the one interpolation in core.common.keyframes, so the preview and
#: the export cannot disagree about a frame's values -- only about which
#: parameter name they land on, which is what these two tables reconcile.
#:
#: border_width_var and border_bias_var are deliberately absent. A keyframe
#: captures them, and the preview applies them through Border Mode, but this
#: render never draws borders at all: they are scanned in border_scanning.py
#: and exported for Fusion (fusion_export.py), not burned into the splat. There
#: is nothing here for them to drive.
_KF_RENDER_VARS = {
    "max_disp_var": "max_disp",
    "zero_disparity_anchor_var": "zero_disparity_anchor_val",
    "depth_gamma_var": "depth_gamma",
    "depth_dilate_size_x_var": "depth_dilate_size_x",
    "depth_dilate_size_y_var": "depth_dilate_size_y",
    "depth_blur_size_x_var": "depth_blur_size_x",
    "depth_blur_size_y_var": "depth_blur_size_y",
    "depth_dilate_left_var": "depth_dilate_left",
    "depth_blur_left_var": "depth_blur_left",
    "depth_blur_left_mix_var": "depth_blur_left_mix",
    "boost_thresh_var": "boost_thresh",
    "boost_amount_var": "boost_amount",
    "boost_curve_var": "boost_curve",
    "boost_knee_var": "boost_knee",
    "boost_prefilter_radius_var": "boost_prefilter_radius",
    "boost_prefilter_eps_var": "boost_prefilter_eps",
    "pop_amount_var": "pop_amount",
    "frame_break_thresh_var": "frame_break_thresh",
    "fb_erode_left_var": "frame_break_erode_left",
    "fb_erode_right_var": "frame_break_erode_right",
    "fb_erode_global_var": "frame_break_erode_global",
    "fb_edge_blur_var": "frame_break_edge_blur",
}

#: Parameters the pipeline wants as ints, whatever the keyframe stored.
_KF_INT_PARAMS = ("boost_prefilter_radius",)


def plan_batches(total_frames, batch_size, track=None, test_target_frame_idx=None):
    """The frame indices for each batch, in order.

    Without keyframes this is the plain `range(0, total, batch_size)` split and
    nothing changes. With them, a batch may only cover frames that share the
    SAME interpolated values, because every one of those values reaches the
    pipeline as a scalar for the whole batch -- gamma at
    normalize_and_gamma_depth, the dilate/blur sizes at process_depth_batch,
    disparity and the boost at execute_forward_warp. A batch spanning a change
    would silently render the first frame's settings over all of them.

    So a run is cut wherever the values change, and capped at batch_size. The
    cost lands exactly where the animation is: static keyframes hold one
    snapshot for a long stretch and cost nothing, a dynamic ramp changes every
    frame and renders at batch size 1 for the span of the ramp, and past the
    last keyframe full batches resume -- which is why that tail is emitted
    without asking the track anything.
    """
    if test_target_frame_idx is not None:
        return [[int(test_target_frame_idx)]]

    def _even(start, stop):
        return [list(range(i, min(i + batch_size, stop))) for i in range(start, stop, batch_size)]

    if track is None or track.is_empty():
        return _even(0, total_frames)

    last_kf = max(track.sorted_frames())
    plan, run, run_values = [], [], None
    for f in range(0, min(last_kf + 1, total_frames)):
        values = track.values_at(f, hold_first=False)   # before the first key: the clip's own settings
        if run and (values != run_values or len(run) >= batch_size):
            plan.append(run)
            run = []
        if not run:
            run_values = values
        run.append(f)
    if run:
        plan.append(run)
    # Past the last keyframe every frame holds that snapshot, so there is
    # nothing left to cut on.
    plan.extend(_even(min(last_kf + 1, total_frames), total_frames))
    return plan


def effective_settings(base, track, frame):
    """`base` with this frame's keyframed values applied over it.

    Returns the dict unchanged (not a copy) when there is nothing to apply,
    which is the common case: most clips carry no track at all.
    """
    if track is None:
        return base
    # before the first keyframe the clip's own settings (user, 2026-10-30), as the preview shows them
    values = track.values_at(frame, hold_first=False)
    if not values:
        return base
    eff = dict(base)
    for var, param in _KF_RENDER_VARS.items():
        if var in values:
            try:
                eff[param] = float(values[var])
            except (TypeError, ValueError):
                continue
    for param in _KF_INT_PARAMS:
        if param in eff:
            try:
                eff[param] = int(round(float(eff[param])))
            except (TypeError, ValueError):
                pass
    return eff


def resolve_subtitle_mask(source_video_path: str) -> Optional[str]:
    """Return the <stem>_submask.mp4 sitting next to a source clip, if any.

    Convention shared with subtitle_mask_gui.py, which saves its rendered
    mask as <clip>_submask.mp4 alongside the source. GUI call sites can do:

        sub_mask = resolve_subtitle_mask(source_path)
        processor.render_video(..., subtitle_mask_path=sub_mask)
    """
    candidate = os.path.splitext(source_video_path)[0] + "_submask.mp4"
    return candidate if os.path.isfile(candidate) else None


# The encoder feed's writer thread (audit O1), shared with Merging (O3).
from core.common.pipe_writer import PipeWriter as _PipeWriter  # noqa: E402


def _grid_frames_gpu(panels: Dict[str, torch.Tensor], layout: str):
    """The encoder's frames, built on the GPU (audit O1): each yielded array
    is one [H, W, 3] uint16 BGR grid, downloaded once, byte-identical to the
    CPU path it replaces (np.clip -> grid -> clip * 65535.0 -> astype(uint16)
    -> RGB2BGR): float32 arithmetic and truncation are the same on the device.

    panels: [B, C, H, W] float tensors -- left, right, occlusion, depth, and
    left_mask for dual eye. layout: "4panel" [left|depth / occlusion|right],
    "2panel" [occlusion|right], "dual_eye" [left_mask|left / occlusion|right].
    """
    if layout not in ("4panel", "2panel", "dual_eye"):
        raise ValueError(f"unknown grid layout {layout!r}")
    n = next(iter(panels.values())).shape[0]
    for j in range(n):              # a frame at a time: ~100 MB of float grid at 4-panel 1080p, not a batch's

        def rgb(name):
            t = panels[name][j].clamp(0.0, 1.0)
            return t.expand(3, -1, -1) if t.shape[0] == 1 else t

        if layout == "4panel":
            grid = torch.cat([torch.cat([rgb("left"), rgb("depth")], dim=2),
                              torch.cat([rgb("occlusion"), rgb("right")], dim=2)], dim=1)
        elif layout == "2panel":
            grid = torch.cat([rgb("occlusion"), rgb("right")], dim=2)
        else:
            grid = torch.cat([torch.cat([rgb("left_mask"), rgb("left")], dim=2),
                              torch.cat([rgb("occlusion"), rgb("right")], dim=2)], dim=1)
        # BGR = the channels reversed; then [H, W, C] for the pipe
        frame = (grid.flip(0) * 65535.0).to(torch.uint16).permute(1, 2, 0).contiguous()
        yield frame.cpu().numpy()


class RenderProcessor:
    """Handles the core splatting render loop for a single video task."""

    def __init__(self, stop_event: threading.Event, progress_queue: queue.Queue):
        """Initialize render processor.

        Args:
            stop_event: Event to signal stop/cancellation
            progress_queue: Queue for sending progress updates to GUI
        """
        self.stop_event = stop_event
        self.progress_queue = progress_queue
        self._color_encode_flags = {}

    def _read_ffmpeg_output(self, pipe, log_level):
        """Helper method to read FFmpeg's output without blocking."""
        try:
            # Use iter to read line by line
            for line in iter(pipe.readline, b""):  # Read bytes until an empty byte string
                if line:
                    # Decode bytes to string for logging, ignoring potential decoding errors
                    msg = line.decode("utf-8", errors="ignore").strip()
                    logger.log(log_level, f"FFmpeg: {msg}")
        except Exception as e:
            logger.error(f"Error reading FFmpeg pipe: {e}")
        finally:
            if pipe:
                pipe.close()

    def render_video(
        self,
        input_video_reader: VideoReader,
        depth_map_reader: VideoReader,
        total_frames_to_process: int,
        processed_fps: float,
        output_video_path_base: str,
        target_output_height: int,
        target_output_width: int,
        max_disp: float,
        batch_size: int,
        dual_output: bool,
        zero_disparity_anchor_val: float,
        video_stream_info: Optional[dict],
        input_bias: float,
        assume_raw_input: bool,
        global_depth_min: float,
        global_depth_max: float,
        depth_stream_info: Optional[dict],
        user_output_crf: Optional[int] = None,
        is_low_res_task: bool = False,
        depth_gamma: float = 1.0,
        depth_dilate_size_x: float = 0.0,
        depth_dilate_size_y: float = 0.0,
        depth_blur_size_x: float = 0.0,
        depth_blur_size_y: float = 0.0,
        depth_dilate_left: float = 0.0,
        depth_blur_left: float = 0.0,
        depth_blur_left_mix: float = 0.5,
        flip_horizontal: bool = False,
        skip_lowres_preproc: bool = False,
        color_tags_mode: str = "Auto",
        encoding_options: Optional[dict] = None,
        dnxhr_fullres_split: bool = False,
        dnxhr_profile: str = "HQX",  # short key; resolved via the SQ/HQ/HQX/444 map
        output_container: str = "MP4",  # MP4 or MKV for the non-DNxHR outputs (user, 2026-10-25)
        is_test_mode: bool = False,
        test_target_frame_idx: Optional[int] = None,
        mask_mode: str = "SC",
        splat_backend: str = "SC",
        hybrid_sensitivity: float = 50.0,
        hybrid_taper: bool = False,
        m2s_fix_strength: float = 1.0,
        dual_eye: bool = False,
        dual_eye_bias: float = 0.5,
        keyframe_track: Optional[KeyframeTrack] = None,
        boost_thresh: float = 0.85,
        boost_amount: float = 0.0,
        boost_curve: float = 2.0,
        boost_knee: float = 0.0,
        boost_prefilter_radius: int = 0,
        boost_prefilter_eps: float = 1e-3,
        pop_amount: float = 0.0,
        depth_peak_raw: float = 1.0,
        subtitle_mask_path: Optional[str] = None,
        subtitle_mask_strength: float = 1.0,
        subtitle_mask_grow: float = 0.0,
        subtitle_mask_soften: float = 0.0,
        subtitle_mask_clean: float = 0.0,
        subtitle_mask_conv: float = 0.0,
        dilate_regions: Optional[dict] = None,
        rain: Optional[dict] = None,
        frame_break_bars: float = 0.0,
        frame_break_thresh: float = 1.0,
        frame_break_erode_left: float = 0.0,
        frame_break_erode_right: float = 0.0,
        frame_break_erode_global: float = 0.0,
        frame_break_edge_blur: float = 0.0,
    ) -> bool:
        """Core splatting render loop.

        Args:
            input_video_reader: Reader for source video
            depth_map_reader: Reader for depth map
            total_frames_to_process: Number of frames to process
            processed_fps: Output video FPS
            output_video_path_base: Base path for output video
            target_output_height: Target height
            target_output_width: Target width
            max_disp: Max disparity percentage
            batch_size: Frames per batch
            dual_output: Whether to output left/right eyes
            zero_disparity_anchor_val: Convergence anchor (0-1)
            video_stream_info: Metadata for source video
            input_bias: Depth input bias
            assume_raw_input: Whether to skip normalization
            global_depth_min: Global min depth used for normalization
            global_max_depth: Global max depth used for normalization
            depth_stream_info: Metadata for depth map
            user_output_crf: FFmpeg CRF value
            is_low_res_task: Whether this is a low-res pass
            depth_gamma: Gamma correction for depth
            depth_dilate_size_x: X dilation for depth
            depth_dilate_size_y: Y dilation for depth
            depth_blur_size_x: X blur for depth
            depth_blur_size_y: Y blur for depth
            depth_dilate_left: Left-eye dilation
            depth_blur_left: Left-eye blur
            depth_blur_left_mix: Mix factor for left-eye blur
            skip_lowres_preproc: Whether to skip preprocessing for low-res
            color_tags_mode: FFmpeg color tagging mode
            is_test_mode: Whether in diagnostic test mode
            test_target_frame_idx: Specific frame for diagnostic test
            subtitle_mask_path: Optional grayscale mask video (white = burned-in
                subtitle pixels). Masked pixels are pinned to the convergence
                anchor (zero disparity / screen depth) to prevent subtitle
                ghosting. Frame-aligned with the source clip.
            subtitle_mask_strength: 0..1 scale for the pin (1.0 = fully at
                screen depth).
            frame_break_bars: Special FX > Frame Break letterbox bar fraction
                (0 = off, 1 = bars meet in the centre). When > 0, this method
                ALSO writes a lossless, sidecar-adjacent hard-cutout mask
                video (<output_stem>_framebreak.mkv, see
                core.common.video_io.resolve_frame_break_mask) recording which
                pixels in the bar rows should show through vs. go black. It
                does NOT burn bars into this stage's own splatted output --
                by design, Frame Break is composited later, in Merging, after
                inpainting and grain match.
            frame_break_thresh: Depth (white = nearest) a pixel must meet or
                exceed to protrude through the bars; only meaningful while
                frame_break_bars > 0.

        Returns:
            True if completed successfully, False otherwise
        """
        # Localized Dilate regions, parsed once per clip.
        _dilate_regions = _dilreg.DilateRegions.from_dict(dilate_regions)
        if not _dilate_regions.is_empty():
            logger.info(
                f"[LOCAL DILATE] {len(_dilate_regions.keyframes)} region keyframe(s) active."
            )
        else:
            _dilate_regions = None

        logger.debug("==> Initializing ForwardWarpStereo module")
        stereo_projector = ForwardWarpStereo(occlu_map=True).cuda()

        height, width = target_output_height, target_output_width
        if not is_test_mode:
            os.makedirs(os.path.dirname(output_video_path_base), exist_ok=True)

        # Determine output grid dimensions and final path
        # dual_eye: 4-panel [Lmask|Lwarp][Rmask|Rwarp], same footprint as _splatted4
        if dual_eye:
            grid_height, grid_width = height * 2, width * 2
        else:
            grid_height, grid_width = (height, width * 2) if dual_output else (height * 2, width * 2)
        flip_suffix = "F" if flip_horizontal else ""
        if dual_eye:
            suffix = f"_splatted2D{flip_suffix}"
        else:
            suffix = f"_splatted2{flip_suffix}" if dual_output else f"_splatted4{flip_suffix}"
        res_suffix = f"_{width}"
        _container_ext = ".mkv" if str(output_container or "").strip().upper() == "MKV" else ".mp4"
        final_output_video_path = os.path.normpath(
            f"{os.path.splitext(output_video_path_base)[0]}{res_suffix}{suffix}{_container_ext}"
        )
        logger.info(f"==> Target Output Path: {final_output_video_path}")

        task_name = "LowRes" if is_low_res_task else "HiRes"
        self._log_color_metadata(video_stream_info, task_name)

        ffmpeg_process = None
        mask_process = None
        splat_process = None
        frame_break_mask_process = None
        # Every file this render writes goes under <final>.part until the
        # render has finished cleanly (core/common/partial_output): a Stop or
        # a failure keeps the previous output and never leaves a truncated
        # one under its name for Inpainting to pick up (audit #5).
        parts = {}              # final path -> its .part, for the primary output(s)
        frame_break_part = None
        frame_break_mask_path = None
        use_dnxhr_split = (
            bool(dnxhr_fullres_split)
            and (bool(dual_output) or bool(dual_eye))
            and (not is_low_res_task)
            and (not is_test_mode)
        )

        if not is_test_mode:
            encode_stream_info = self._get_encode_stream_info(video_stream_info, color_tags_mode)
            # the warp and mask this render runs, in the grid's comment (2026-10-22): M2SVid's
            # inpainting needs an M2S warp and can tell an SC splat by it
            from core.splatting.m2s_backends import grid_comment as _grid_comment
            grid_comment = _grid_comment(splat_backend, mask_mode, is_low_res_task)

            if use_dnxhr_split:
                base_dir = os.path.dirname(output_video_path_base)
                stem = os.path.splitext(os.path.basename(output_video_path_base))[0]
                prefix = f"{stem}{res_suffix}"

                # Combined DNxHR output: a single grid file mirroring the non-10-bit
                # layout for this mode (_splatted2 for dual, _splatted2D for dual-eye,
                # including any flip suffix) so the downstream stage reads it through
                # its existing path with no special handling. Both panels must share
                # one codec (a file has one codec), so the mask travels as DNxHR too
                # -- it compresses to almost nothing since it's near-flat.
                splat_output_path = os.path.join(base_dir, f"{prefix}{suffix}.mov")

                parts[splat_output_path] = part_path(splat_output_path)
                splat_process = start_ffmpeg_pipe_process_dnxhr(
                    content_width=grid_width,
                    content_height=grid_height,
                    final_output_mov_path=parts[splat_output_path],
                    fps=processed_fps,
                    dnxhr_profile=dnxhr_profile,
                    comment=grid_comment,
                )
                if splat_process is None:
                    logger.error("Failed to start DNxHR pipe for combined splat output. Aborting splatting task.")
                    # This return sits above the try/finally that frees the
                    # projector, so without these two lines a failed pipe launch
                    # leaves a ForwardWarpStereo resident on the card.
                    del stereo_projector
                    release_cuda_memory()
                    return False

                # Keep the split-writer branch below working with a single pipe by
                # pointing both handles at it; _write_split_to_ffmpeg now hstacks.
                mask_process = None
                ffmpeg_process = splat_process
            else:
                parts[final_output_video_path] = part_path(final_output_video_path)
                ffmpeg_process = start_ffmpeg_pipe_process(
                    content_width=grid_width,
                    content_height=grid_height,
                    final_output_mp4_path=parts[final_output_video_path],
                    fps=processed_fps,
                    video_stream_info=encode_stream_info,
                    user_output_crf=user_output_crf,
                    output_format_str="splatted_grid",
                    debug_label=task_name,
                    encoding_options=encoding_options,
                    comment=grid_comment,
                )
                if ffmpeg_process is None:
                    logger.error("Failed to start FFmpeg pipe. Aborting splatting task.")
                    del stereo_projector
                    release_cuda_memory()
                    return False

            # --- NEW: Start threads to read FFmpeg output to prevent deadlock ---
            if ffmpeg_process:
                stdout_thread = threading.Thread(
                    target=self._read_ffmpeg_output, args=(ffmpeg_process.stdout, logging.DEBUG), daemon=True
                )
                stderr_thread = threading.Thread(
                    target=self._read_ffmpeg_output, args=(ffmpeg_process.stderr, logging.INFO), daemon=True
                )
                stdout_thread.start()
                stderr_thread.start()

            self._compare_encoding_flags(ffmpeg_process, task_name)

            # --- Frame Break: lossless protrusion mask (sidecar-adjacent) ---
            # Bars themselves are NOT drawn here -- only a hard 0/255 mask
            # recording which bar-row pixels should show through, written next
            # to this task's splatted output for the merging stage to apply
            # later (after inpainting and grain match). No-op file, no-op
            # cost, unless this clip's sidecar actually has bars > 0.
            if frame_break_bars > 0.0:
                _fb_primary_output_path = splat_output_path if use_dnxhr_split else final_output_video_path
                frame_break_mask_path = resolve_frame_break_mask(_fb_primary_output_path)
                frame_break_part = part_path(frame_break_mask_path)
                frame_break_mask_process = start_ffmpeg_pipe_process_mask(
                    content_width=width,
                    content_height=height,
                    output_path=frame_break_part,
                    fps=processed_fps,
                )
                if frame_break_mask_process is None:
                    logger.warning(
                        "[FRAME BREAK] Failed to start the lossless mask pipe; "
                        "continuing this render WITHOUT a Frame Break mask "
                        "(Merging will render this clip with no bars)."
                    )
                else:
                    logger.info(f"[FRAME BREAK] Writing protrusion mask to: {frame_break_mask_path}")
                    _fb_stdout_thread = threading.Thread(
                        target=self._read_ffmpeg_output,
                        args=(frame_break_mask_process.stdout, logging.DEBUG),
                        daemon=True,
                    )
                    _fb_stderr_thread = threading.Thread(
                        target=self._read_ffmpeg_output,
                        args=(frame_break_mask_process.stderr, logging.INFO),
                        daemon=True,
                    )
                    _fb_stdout_thread.start()
                    _fb_stderr_thread.start()

        max_expected_raw_value = self._get_max_expected_raw_depth(depth_stream_info)
        logger.debug(
            f"[DEPTH] Max expected raw value: {max_expected_raw_value}, assume_raw_input: {assume_raw_input}, global_depth_min: {global_depth_min:.2f}, global_depth_max: {global_depth_max:.2f}"
        )

        tv_disp_comp = 1.0
        if assume_raw_input and depth_stream_info and max_expected_raw_value > 256.0:
            if str(depth_stream_info.get("color_range", "")).lower() == "tv":
                from core.splatting.depth_processing import DEPTH_VIS_TV10_WHITE_NORM, DEPTH_VIS_TV10_BLACK_NORM

                tv_disp_comp = 1.0 / (DEPTH_VIS_TV10_WHITE_NORM - DEPTH_VIS_TV10_BLACK_NORM)
                logger.debug(f"[DEPTH] TV range compensation enabled: {tv_disp_comp:.3f}")

        # Ensure numeric values are actually integers before using them in ranges
        try:
            total_frames_to_process = int(total_frames_to_process)
        except (ValueError, TypeError):
            total_frames_to_process = 0

        try:
            batch_size = int(batch_size)
        except (ValueError, TypeError):
            batch_size = 1

        if test_target_frame_idx is not None:
            try:
                test_target_frame_idx = int(test_target_frame_idx)
            except (ValueError, TypeError):
                test_target_frame_idx = None

        frame_count = 0
        encoding_successful = True

        _fetcher = None     # the read-ahead threads (_fetch_ahead), stopped in the finally below
        try:
            # The values a keyframe animates all reach the pipeline as scalars
            # for a whole batch, so the batches themselves have to respect where
            # those values change. See plan_batches.
            batch_plan = plan_batches(
                total_frames_to_process, batch_size, keyframe_track, test_target_frame_idx
            )
            #: This clip's un-keyframed values, which effective_settings layers
            #: each batch's snapshot over. Built once; never mutated.
            kf_base = {
                "max_disp": max_disp,
                "zero_disparity_anchor_val": zero_disparity_anchor_val,
                "depth_gamma": depth_gamma,
                "depth_dilate_size_x": depth_dilate_size_x,
                "depth_dilate_size_y": depth_dilate_size_y,
                "depth_blur_size_x": depth_blur_size_x,
                "depth_blur_size_y": depth_blur_size_y,
                "depth_dilate_left": depth_dilate_left,
                "depth_blur_left": depth_blur_left,
                "depth_blur_left_mix": depth_blur_left_mix,
                "boost_thresh": boost_thresh,
                "boost_amount": boost_amount,
                "boost_curve": boost_curve,
                "boost_knee": boost_knee,
                "boost_prefilter_radius": boost_prefilter_radius,
                "boost_prefilter_eps": boost_prefilter_eps,
                "pop_amount": pop_amount,
                "frame_break_thresh": frame_break_thresh,
                "frame_break_erode_left": frame_break_erode_left,
                "frame_break_erode_right": frame_break_erode_right,
                "frame_break_erode_global": frame_break_erode_global,
                "frame_break_edge_blur": frame_break_edge_blur,
            }

            # Subtitle protection mask: grayscale video where white = burned-in
            # subtitle text. Those pixels get pinned to the convergence anchor
            # (zero disparity) so text sits at the screen plane in both eyes
            # instead of being warped with the scene depth (subtitle ghosting).
            sub_mask_reader = None
            sub_mask_len = 0
            if subtitle_mask_path:
                try:
                    if os.path.isfile(subtitle_mask_path):
                        sub_mask_reader = VideoReader(subtitle_mask_path)
                        sub_mask_len = len(sub_mask_reader)
                        logger.info(
                            f"[SUBMASK] Loaded subtitle mask: {subtitle_mask_path} "
                            f"({sub_mask_len} frames, strength={subtitle_mask_strength:.2f}, "
                            f"grow={subtitle_mask_grow:g}, soften={subtitle_mask_soften:g}, "
                            f"clean={subtitle_mask_clean:g}, conv={subtitle_mask_conv:+g})"
                        )
                        if sub_mask_len < total_frames_to_process:
                            logger.warning(
                                f"[SUBMASK] Mask has {sub_mask_len} frames, clip has "
                                f"{total_frames_to_process}; frames past the mask stay unpinned."
                            )
                    else:
                        logger.warning(f"[SUBMASK] Mask not found, ignoring: {subtitle_mask_path}")
                except Exception as e:
                    logger.warning(f"[SUBMASK] Failed to open subtitle mask ({e}); continuing without it.")
                    sub_mask_reader = None

            # Peak-anchored Depth Boost: convert the sidecar's RAW clip depth
            # max into the warp (post-gamma) domain using the pipeline's own
            # normalize_and_gamma_depth on a scalar — exact by construction,
            # no gamma-convention assumptions. 1.0 = legacy flat anchor.
            # Peak-anchored Depth Boost, as a function rather than a value:
            # it is derived from boost_amount, the convergence anchor and
            # gamma, and a keyframe track can animate all three. Computed once
            # below for the clip, and again per batch whenever keyframes move
            # any of its inputs.
            def _peak_warp(amount, anchor, gamma, quiet=True):
                if not (amount > 0 and 0.0 < depth_peak_raw < 0.999):
                    return 1.0
                try:
                    _pk = normalize_and_gamma_depth(
                        batch_depth_numpy_raw=np.full((1, 1, 1, 1), depth_peak_raw * max_expected_raw_value, dtype=np.float32),
                        assume_raw_input=assume_raw_input,
                        global_depth_max=global_depth_max,
                        global_depth_min=global_depth_min,
                        max_expected_raw_value=max_expected_raw_value,
                        zero_disparity_anchor_val=anchor,
                        depth_gamma=gamma,
                        debug_task_name="RenderPeak",
                    )
                    peak = float(np.asarray(_pk).reshape(-1)[0])
                    if not quiet:
                        logger.info(
                            f"[BOOST] peak anchor: raw={depth_peak_raw:.3f} -> warp-domain={peak:.3f}"
                        )
                    return peak
                except Exception as e:
                    logger.warning(f"[BOOST] peak conversion failed ({e}); using 1.0 (flat anchor).")
                    return 1.0

            # the clip's peak anchors Boost and Pop-Out alike
            depth_peak_warp = _peak_warp(
                boost_amount + pop_amount, zero_disparity_anchor_val, depth_gamma, quiet=False
            )
            if keyframe_track is not None and not keyframe_track.is_empty():
                _n_static, _n_dynamic = keyframe_track.count_by_type()
                _split = len(batch_plan)
                _even = -(-total_frames_to_process // max(batch_size, 1))
                logger.info(
                    f"[KEYFRAMES] {_n_static} static + {_n_dynamic} dynamic on "
                    f"{total_frames_to_process} frames -> {_split} batches "
                    f"(vs {_even} unkeyframed); animating "
                    f"{sorted(set(_KF_RENDER_VARS[v] for _f, _k, _vals in keyframe_track.items_sorted() for v in _vals if v in _KF_RENDER_VARS))}"
                )

            pipe_writer = None      # O1: ffmpeg_process's writer thread, made on the first batch
            from core.splatting.rain import RainSettings
            _rain_settings = RainSettings.from_values(**rain) if rain else None
            _rain_ext = None
            _rain_frames = None     # Rain / Sparks: rain.WindowFrames, made with the first batch (#18)
            # Web (personal/web: the user's own, not released -- rain.web_module()): the strands
            # found once for the clip, before the first batch
            _web = None
            _web_mod = None
            if _rain_settings is not None and _rain_settings.on and _rain_settings.particle == "web":
                from core.splatting.rain import web_module
                _web_mod = web_module()
            if _web_mod is not None:
                build_track = _web_mod.build_track
                _first = input_video_reader.get_batch([0]).asnumpy()[0]
                _kind = "orb" if _rain_settings.preset == "orb" else "strands"
                logger.info(f"[{task_name}] Web ({'moving' if _kind == 'orb' else 'strands'}): finding it (once for this clip)...")
                from core.common.video_io import Uint8Frames   # its detection reads 8-bit pictures
                _web = build_track(_kind, Uint8Frames(input_video_reader), depth_map_reader, (_first.shape[1], _first.shape[0]),
                                   flip=flip_horizontal)
                logger.info(f"[{task_name}] Web: {_web.describe()}; depth {_rain_settings.depth:.2f}, "
                            f"amount {_rain_settings.amount:.2f}"
                            f"{f', grow {_rain_settings.grow:+d} px' if _rain_settings.grow else ''}"
                            f"{f', pop +{_rain_settings.pop:.1f}' if _rain_settings.pop > 0 else ''}"
                            f"{f', convergence {_rain_settings.conv:+.2f}' if _rain_settings.conv else ''}")
            # The next batch read while this one warps (user, 2026-10-30: the GPU sat idle ~2/3 of each
            # clip waiting on reads): its frames, its depth at the video's size, normalized -- everything
            # step 1-2 does, on one thread so the readers still read in order. Not with Rain / Sparks or
            # Web: they read their own windows from the same reader.
            def _fetch_video(batch_indices, batch_video_numpy=None):
                if batch_video_numpy is None:
                    batch_video_numpy = input_video_reader.get_batch(batch_indices).asnumpy()
                if flip_horizontal:
                    batch_video_numpy = np.flip(batch_video_numpy, axis=2).copy()
                return batch_video_numpy

            def _fetch_depth(batch_indices, eff, video_shape):
                """The depth read, resized to the video's size and normalized; video_shape() gives the
                frames' shape (it waits for the video's read when that runs on the other thread)."""
                batch_depth_numpy_raw = depth_map_reader.get_batch(batch_indices).asnumpy()
                if flip_horizontal:
                    batch_depth_numpy_raw = np.flip(batch_depth_numpy_raw, axis=2).copy()

                # --- NEW: Aspect Ratio Parity ---
                # Immediate resize to ensure all following steps (normalization, dilation, blur)
                # occur at the correct target aspect ratio, matching the GUI previewer.
                video_h, video_w = video_shape()[1:3]
                depth_h, depth_w = batch_depth_numpy_raw.shape[1], batch_depth_numpy_raw.shape[2]

                if depth_h != video_h or depth_w != video_w:
                    logger.debug(
                        f"Resizing depth from {depth_w}x{depth_h} to match video {video_w}x{video_h} for aspect-ratio parity."
                    )
                    interp = cv2.INTER_AREA if (video_w < depth_w and video_h < depth_h) else cv2.INTER_LINEAR
                    # Correctly account for potential channel dimension (e.g., RGB depth maps)
                    output_shape = (batch_depth_numpy_raw.shape[0], video_h, video_w)
                    if batch_depth_numpy_raw.ndim == 4:
                        output_shape += (batch_depth_numpy_raw.shape[3],)

                    resized_depth = np.empty(output_shape, dtype=batch_depth_numpy_raw.dtype)
                    for idx in range(batch_depth_numpy_raw.shape[0]):
                        res = cv2.resize(batch_depth_numpy_raw[idx], (video_w, video_h), interpolation=interp)
                        if batch_depth_numpy_raw.ndim == 4 and res.ndim == 2:
                            res = res[..., np.newaxis]
                        resized_depth[idx] = res
                    batch_depth_numpy_raw = resized_depth

                # 2. Normalize and apply gamma (BEFORE dilation/blur), on the GPU when it's there (the same
                # steps in torch; the CPU's took ~6 s a 3-clip batch at 2580x1080, user 2026-10-30)
                batch_depth_normalized = normalize_and_gamma_depth_gpu(
                    batch_depth_numpy_raw=batch_depth_numpy_raw,
                    assume_raw_input=assume_raw_input,
                    global_depth_max=global_depth_max,
                    global_depth_min=global_depth_min,
                    max_expected_raw_value=max_expected_raw_value,
                    zero_disparity_anchor_val=eff["zero_disparity_anchor_val"],
                    depth_gamma=eff["depth_gamma"],
                    debug_task_name="Render",
                )
                return batch_depth_numpy_raw, batch_depth_normalized

            def _fetch_batch(batch_indices, eff, batch_video_numpy=None):
                """Steps 1-2 here and now (Rain / Sparks, Web, a test frame)."""
                video = _fetch_video(batch_indices, batch_video_numpy)
                return (video, *_fetch_depth(batch_indices, eff, lambda: video.shape))

            _read_ahead = not (_rain_settings is not None and _rain_settings.on) and not (
                is_test_mode and test_target_frame_idx is not None)
            if _read_ahead:
                # one thread per reader: each still reads in order, and the source's read and the
                # depth's read + prep run side by side
                _fetcher = (ThreadPoolExecutor(1, thread_name_prefix="splat-read-video"),
                            ThreadPoolExecutor(1, thread_name_prefix="splat-read-depth"))
            _fetched = {}       # batch_no -> (the video's Future, the depth's Future)

            def _fetch_ahead(batch_no):
                """Queue batch `batch_no`'s reads (and its depth prep) behind the ones in flight."""
                if batch_no < len(batch_plan) and batch_no not in _fetched and batch_plan[batch_no]:
                    idx = batch_plan[batch_no]
                    video = _fetcher[0].submit(_fetch_video, idx)
                    depth = _fetcher[1].submit(_fetch_depth, idx, effective_settings(kf_base, keyframe_track, idx[0]),
                                               lambda: video.result().shape)
                    _fetched[batch_no] = (video, depth)

            for batch_no, batch_indices in enumerate(batch_plan):
                # Every way out of this loop other than running out of frames
                # means the output is short. Say so: encoding_successful starts
                # True, ffmpeg exits 0 on a cleanly closed stdin no matter how
                # few frames it got, and the final verification only checks the
                # file exists and is non-empty -- which a truncated file passes.
                # Returning True here is what let a stopped render be treated as
                # a finished one and moved the source clip into finished/.
                if self.stop_event.is_set():
                    logger.info("==> Render stopped by user; this clip's output is incomplete.")
                    encoding_successful = False
                    break
                if ffmpeg_process is not None and ffmpeg_process.poll() is not None:
                    logger.error("==> FFmpeg exited mid-render; this clip's output is incomplete.")
                    encoding_successful = False
                    break
                if use_dnxhr_split and splat_process is not None and splat_process.poll() is not None:
                    logger.error("==> Splat pipe exited mid-render; this clip's output is incomplete.")
                    encoding_successful = False
                    break

                # Frame Break's mask pipe is auxiliary output: if it dies mid-
                # render, that must never take down the primary splat/deliverable.
                # Stop writing to it and keep going.
                if frame_break_mask_process is not None and frame_break_mask_process.poll() is not None:
                    logger.warning(
                        "[FRAME BREAK] Mask pipe exited unexpectedly mid-render; "
                        "the primary render continues, but this clip's mask will "
                        "be short/missing and Merging will skip bars past this point."
                    )
                    frame_break_mask_process = None

                if not batch_indices:
                    continue

                # plan_batches has already guaranteed every frame in this batch
                # shares one set of keyframed values, so the first frame speaks
                # for all of them.
                eff = effective_settings(kf_base, keyframe_track, batch_indices[0])
                eff_peak_warp = (
                    depth_peak_warp
                    if eff is kf_base
                    else _peak_warp(
                        eff["boost_amount"] + eff["pop_amount"],
                        eff["zero_disparity_anchor_val"],
                        eff["depth_gamma"],
                    )
                )

                # 1-2. Fetch frames, then the depth resized, normalized and gamma'd (_fetch_batch)
                _win_raw = _centres = None
                if _fetcher is not None:
                    _fetch_ahead(batch_no)
                    _fetch_ahead(batch_no + 1)       # the next batch reads while this one warps
                    _video, _depth = _fetched.pop(batch_no)
                    batch_video_numpy = _video.result()
                    batch_depth_numpy_raw, batch_depth_normalized = _depth.result()
                elif _rain_settings is not None and _rain_settings.on and _web_mod is None:
                    # the batch sliced out of its Rain / Sparks window: one forward read (#18)
                    if _rain_frames is None:
                        from core.splatting.rain import WindowFrames, radius_of
                        _rain_frames = WindowFrames(input_video_reader, radius_of(_rain_settings))
                    _win_raw, _centres = _rain_frames.read(batch_indices)
                    batch_video_numpy = _win_raw[_centres]
                    batch_video_numpy, batch_depth_numpy_raw, batch_depth_normalized = _fetch_batch(
                        batch_indices, eff, batch_video_numpy)
                else:
                    batch_video_numpy, batch_depth_numpy_raw, batch_depth_normalized = _fetch_batch(batch_indices, eff)

                # 2.5. The subtitle mask for this batch, read once: Text Clean
                # uses it here, the pin (3.6) after Dilate / Blur. Text Clean
                # (user, 2026-10-02): DepthCrafter's own copy of the text is
                # taken out of the depth BEFORE Dilate / Blur can spread it past
                # the mask -- the depth under the mask, grown by the Clean
                # distance (source px), is filled from the background around it.
                sub_valid, sub_m = [], None
                if sub_mask_reader is not None:
                    sub_valid = [bi for bi, gi in enumerate(batch_indices) if gi < sub_mask_len]
                    if sub_valid:
                        # Decord always returns 8-bit [b, H, W, 3]; mask is gray -> take ch 0
                        sub_np = sub_mask_reader.get_batch([batch_indices[bi] for bi in sub_valid]).asnumpy()
                        # Splatting's own Text Grow / Text Soften (user, 2026-10-02),
                        # in source pixels: on the mask before it is resized
                        from core.splatting.subtitle_mask_adjust import adjust_mask, clean_depth
                        sub_m = adjust_mask(sub_np[..., 0], subtitle_mask_grow, subtitle_mask_soften)
                        sub_m = sub_m.astype(np.float32) / 255.0
                        if flip_horizontal:
                            sub_m = np.flip(sub_m, axis=2).copy()
                        sub_src_w = sub_m.shape[2]
                        dh, dw = batch_depth_normalized.shape[1], batch_depth_normalized.shape[2]
                        if sub_m.shape[1] != dh or sub_m.shape[2] != dw:
                            sub_m = np.stack(
                                [cv2.resize(sub_m[k], (dw, dh), interpolation=cv2.INTER_LINEAR) for k in range(sub_m.shape[0])]
                            )
                        if subtitle_mask_clean > 0:
                            _clean_px = float(subtitle_mask_clean) * dw / max(sub_src_w, 1)
                            for k, bi in enumerate(sub_valid):
                                batch_depth_normalized[bi] = clean_depth(batch_depth_normalized[bi], sub_m[k], _clean_px)

                # 2.6. Web (personal/web), where Text Clean is: the strands' light and depth for
                # this batch, and DepthCrafter's strand bands taken out of the scene's depth before
                # Dilate / Blur can spread them -- the clean frame is warped with that
                _web_parts = None
                if _web is not None:
                    _web_parts = []
                    for _k in range(batch_depth_normalized.shape[0]):
                        _dk = batch_depth_normalized[_k]
                        _d2 = _dk[..., 0] if _dk.ndim == 3 else _dk
                        _lit, _zf, _scene = _web.frame(as_uint8(batch_video_numpy[_k]), _d2, grow=_rain_settings.grow,
                                                       index=int(batch_indices[_k]))
                        if _dk.ndim == 3:
                            batch_depth_normalized[_k][..., 0] = _scene
                        else:
                            batch_depth_normalized[_k] = _scene
                        _web_parts.append((_lit, _zf, _scene))

                # Convert back to "raw" format for process_depth_batch (which expects raw-like values)
                # Scale back to max_raw_value range so dilation/blur work correctly
                batch_depth_for_processing = batch_depth_normalized * max_expected_raw_value

                # Add channel dimension for process_depth_batch
                if batch_depth_for_processing.ndim == 3:
                    batch_depth_for_processing = batch_depth_for_processing[..., None]

                # 3. Process depth batch (dilation/blur)
                batch_depth_processed = process_depth_batch(
                    dilate_regions=_dilate_regions,
                    frame_indices=batch_indices,
                    batch_depth_numpy_raw=batch_depth_for_processing,
                    depth_gamma=1.0,  # Already applied above
                    depth_dilate_size_x=eff["depth_dilate_size_x"],
                    depth_dilate_size_y=eff["depth_dilate_size_y"],
                    depth_blur_size_x=eff["depth_blur_size_x"],
                    depth_blur_size_y=eff["depth_blur_size_y"],
                    max_raw_value=max_expected_raw_value,
                    depth_dilate_left=eff["depth_dilate_left"],
                    depth_blur_left=eff["depth_blur_left"],
                    depth_blur_left_mix=eff["depth_blur_left_mix"],
                    skip_preprocessing=skip_lowres_preproc and is_low_res_task,
                )

                # Normalize back to 0-1 after processing
                batch_depth_numpy_float = batch_depth_processed / max(max_expected_raw_value, 1.0)
                batch_depth_numpy_float = np.clip(batch_depth_numpy_float, 0.0, 1.0)

                # 3.6. Subtitle mask: pin masked pixels to the convergence anchor.
                # Same anchor-relative lerp as the divergence ramp: m=1 -> depth
                # == anchor -> zero disparity (screen plane, identical in both
                # eyes -- also correct in dual_eye mode since both half-shifts
                # derive from the same anchor-relative disparity); m=0 ->
                # untouched. Soft mask edges blend smoothly. Applied AFTER
                # depth dilation/blur and the ramp so neither bleeds warped
                # depth back into the protected text.
                if sub_m is not None:
                    valid = sub_valid
                    if valid:
                        m = sub_m          # read, adjusted and flipped at 2.5
                        dh, dw = batch_depth_numpy_float.shape[1], batch_depth_numpy_float.shape[2]
                        if m.shape[1] != dh or m.shape[2] != dw:
                            m = np.stack(
                                [
                                    cv2.resize(m[k], (dw, dh), interpolation=cv2.INTER_LINEAR)
                                    for k in range(m.shape[0])
                                ]
                            )
                        if subtitle_mask_strength < 1.0:
                            m = m * max(0.0, float(subtitle_mask_strength))
                        # This batch's anchor, not the clip's: a keyframed
                        # convergence point moves the screen plane the text
                        # is being pinned to. Text Conv (user, 2026-10-11) moves the
                        # text off it, as Rain's own Convergence does its drops: a
                        # shift is (depth - anchor), so + sets the text back, - out.
                        _anchor = float(np.clip(float(eff["zero_disparity_anchor_val"]) - float(subtitle_mask_conv), 0.0, 1.0))
                        for k, bi in enumerate(valid):
                            mk = m[k]
                            if batch_depth_numpy_float[bi].ndim == 3 and mk.ndim == 2:
                                mk = mk[..., None]
                            batch_depth_numpy_float[bi] = (
                                batch_depth_numpy_float[bi] * (1.0 - mk) + _anchor * mk
                            )

                # 3.7. Frame Break: write this batch's protrusion mask to the
                # auxiliary lossless pipe, if active. Pure side output -- never
                # touches batch_processed_frames or the splat itself. Uses the
                # SAME fully-processed depth (post gamma/dilate/blur, post
                # divergence ramp, post subtitle pin) that drives the warp, and
                # the SAME core.common.frame_break helpers the GUI preview uses,
                # so the baked mask (edge erode/dilate/blur included) matches
                # what was previewed. Outside the bar rows the mask is 255 (no
                # bar there); inside, it is the morphed protrusion.
                if frame_break_mask_process is not None:
                    try:
                        _fb_depth = batch_depth_numpy_float
                        if _fb_depth.ndim == 4 and _fb_depth.shape[-1] == 1:
                            _fb_depth = _fb_depth.squeeze(-1)
                        _fb_bar_px = int(round(float(frame_break_bars) * 0.5 * height))
                        _fb_bar_px = min(_fb_bar_px, height // 2 if height > 1 else 0)
                        for _fb_bi in range(_fb_depth.shape[0]):
                            _fb_frame_mask = np.full((height, width), 255, dtype=np.uint8)
                            if _fb_bar_px > 0:
                                _fb_mask = frame_break.build_protrusion_mask(
                                    _fb_depth[_fb_bi], float(eff["frame_break_thresh"]), height, width
                                )
                                _fb_mask = frame_break.morph_protrusion_mask(
                                    _fb_mask,
                                    float(eff["frame_break_erode_left"]),
                                    float(eff["frame_break_erode_right"]),
                                    float(eff["frame_break_erode_global"]),
                                    float(eff["frame_break_edge_blur"]),
                                )
                                for _fb_region in (slice(0, _fb_bar_px), slice(height - _fb_bar_px, height)):
                                    _fb_frame_mask[_fb_region] = _fb_mask[_fb_region]
                            frame_break_mask_process.stdin.write(_fb_frame_mask.tobytes())
                    except Exception as e:
                        logger.warning(
                            f"[FRAME BREAK] Mask write failed for this batch ({e}); "
                            f"disabling Frame Break for the rest of this clip."
                        )
                        # ended and waited for, so its .part is closed before the
                        # cleanup deletes it -- left running, the delete failed on
                        # Windows and a .part stayed behind (bug sweep A4)
                        reap(frame_break_mask_process)
                        frame_break_mask_process = None

                # 3.9 Rain (user, 2026-10-06; core.splatting.rain): the batch's rain separated
                # with its neighbours, and its own depth in front of this scene -- warped on
                # its own below, through the same controls, and added back
                rain_numpy = rain_depth_numpy = None
                if _web_parts is not None:
                    placed = _web_mod.placed
                    rain_numpy = np.stack([_l for _l, _z, _sc in _web_parts])
                    rain_depth_numpy = np.stack([placed(_z, _sc, _rain_settings.depth, _rain_settings.conv)
                                                 for _l, _z, _sc in _web_parts])
                elif _rain_settings is not None and _rain_settings.on:
                    from core.splatting.rain import RainExtractor, window_indices
                    if _rain_ext is None:
                        _rain_ext = RainExtractor(_rain_settings, batch_video_numpy.shape[2])
                        logger.info(f"[{task_name}] {'Sparks' if _rain_settings.particle == 'sparks' else 'Rain'}: "
                                    f"{_rain_settings.mode}"
                                    f"{', any direction' if _rain_settings.any_direction else ''}, "
                                    f"depth {_rain_settings.depth:.2f}, amount {_rain_settings.amount:.2f}, "
                                    f"{_rain_settings.layers} layer(s)"
                                    f"{', strands kept out' if _rain_settings.strands else ''}"
                                    f"{', lights kept out' if getattr(_rain_settings, 'lights', True) else ''}"
                                    f"{f', pop +{_rain_settings.pop:.1f}' if _rain_settings.pop > 0 else ''}"
                                    f", {_rain_settings.preset}"
                                    f"{f', guard {_rain_settings.guard:.2f}' if _rain_settings.guard > 0 else ''}"
                                    f"{', in place' if _rain_settings.placement == 'place' else ''}"
                                    f"{f', grow {_rain_settings.grow:+d} px' if _rain_settings.grow else ''}"
                                    f"{', reflections kept out' if _rain_settings.particle == 'sparks' and _rain_settings.reflections else ''}"
                                    f"{f', spread {_rain_settings.spread:.2f}' if _rain_settings.spread != 1.0 else ''}"
                                    f"{f', spacing {_rain_settings.spacing:.2f}' if _rain_settings.spacing != 1.0 else ''}"
                                    f"{f', convergence {_rain_settings.conv:+.2f}' if _rain_settings.conv else ''}")
                    if _win_raw is not None:             # read with the batch (step 1)
                        _win = _win_raw
                    else:
                        from core.splatting.rain import radius_of
                        _win_idx, _centres = window_indices(batch_indices, len(input_video_reader),
                                                            radius_of(_rain_settings))
                        _win = input_video_reader.get_batch(_win_idx).asnumpy()
                    if flip_horizontal:
                        _win = np.flip(_win, axis=2).copy()
                    _rd = batch_depth_numpy_float[..., 0] if batch_depth_numpy_float.ndim == 4 else batch_depth_numpy_float
                    _parts = _rain_ext.extract(as_uint8(_win), _centres)   # OpenCV detection: 8-bit frames
                    if _rain_settings.guard > 0:        # Foreground guard: the near part keeps its rain
                        from core.splatting.rain import apply_guard   # as the preview does (bug sweep A5)
                        _parts = [apply_guard(r, b, _rd[k], _rain_settings.guard, width / 1920.0)
                                  for k, (r, b) in enumerate(_parts)]
                    rain_numpy = np.stack([r for r, _b in _parts])
                    rain_depth_numpy = np.stack([_rain_ext.depth_of(b, _rd[k]) for k, (_r, b) in enumerate(_parts)])

                # 4. GPU Splatting. Encoding: the panels stay on the GPU, the grid
                # is built there and handed to the pipe's writer thread (O1).
                encoding_now = bool(ffmpeg_process) and not (is_test_mode and test_target_frame_idx is not None)
                batch_processed_frames = self._process_gpu_splatting(
                    as_tensors=encoding_now,
                    is_low_res_task=is_low_res_task,
                    stereo_projector=stereo_projector,
                    batch_video_numpy=batch_video_numpy,
                    batch_depth_numpy_float=batch_depth_numpy_float,
                    target_width=width,
                    target_height=height,
                    max_disp=eff["max_disp"],
                    zero_disparity_anchor_val=eff["zero_disparity_anchor_val"],
                    input_bias=input_bias,
                    tv_disp_comp=tv_disp_comp,
                    mask_mode=mask_mode,
                    splat_backend=splat_backend,
                    hybrid_sensitivity=hybrid_sensitivity,
                    hybrid_taper=hybrid_taper,
                    m2s_fix_strength=m2s_fix_strength,
                    dual_eye=dual_eye,
                    dual_eye_bias=dual_eye_bias,
                    boost_thresh=eff["boost_thresh"],
                    boost_amount=eff["boost_amount"],
                    boost_curve=eff["boost_curve"],
                    boost_knee=eff["boost_knee"],
                    boost_prefilter_radius=eff["boost_prefilter_radius"],
                    boost_prefilter_eps=eff["boost_prefilter_eps"],
                    pop_amount=eff["pop_amount"],
                    depth_peak=eff_peak_warp,
                    rain_numpy=rain_numpy,
                    rain_depth_numpy=rain_depth_numpy,
                    rain_amount=_rain_settings.amount if _rain_settings is not None else 1.0,
                    rain_pop=_rain_settings.pop if _rain_settings is not None else 0.0,
                    # steady Pop: the scene's, not the batch's nearest drop (bug sweep A4)
                    rain_pop_peak=_rain_ext.pop_peak() if _rain_ext is not None else None,
                )

                # 5. Handle results (diag tests or FFmpeg write)
                if is_test_mode and test_target_frame_idx is not None:
                    self._handle_diagnostic_capture(batch_processed_frames, dual_output, task_name)
                elif encoding_now:
                    # Dual-eye's 4-panel grid takes precedence (both the HEVC pipe
                    # and the combined DNxHR pipe are ffmpeg_process then); the
                    # DNxHR split writes the same [mask | splat] as dual output.
                    if dual_eye:
                        layout = "dual_eye"
                    elif (use_dnxhr_split and splat_process) or dual_output:
                        layout = "2panel"
                    else:
                        layout = "4panel"
                    if pipe_writer is None:
                        pipe_writer = _PipeWriter(ffmpeg_process)
                    for grid_frame in _grid_frames_gpu(batch_processed_frames, layout):
                        pipe_writer.put(grid_frame)

                frame_count += len(batch_indices)
                self.progress_queue.put(("processed", frame_count))
                if not is_test_mode:
                    draw_progress_bar(
                        frame_count, total_frames_to_process, suffix=f"{task_name} Batch {batch_no}"
                    )

                # Cleanup batch. No cache flush + gc.collect() per batch (Studio
                # audit 2026-09-28, PERF): the next batch needs the same blocks,
                # and emptying the cache made it allocate them again. The task's
                # end (below) still releases everything.
                del batch_video_numpy, batch_depth_numpy_raw, batch_depth_numpy_float, batch_processed_frames

        except Exception as e:
            logger.error(f"Render error: {e}", exc_info=True)
            encoding_successful = False
            if type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower():
                self.out_of_vram = True       # the batch says what to change (release check 2026-10-17)
        finally:
            # The read-ahead ended before anything closes: its thread reads the clip's readers
            for _pool in _fetcher or ():
                _pool.shutdown(wait=True, cancel_futures=True)
            # Guarantee every ffmpeg pipe is either reaped or killed, and that its
            # stdout/stderr are closed. A silent wait() timeout here (e.g. slow DNxHR
            # finalize) previously left ffmpeg.exe orphaned -- each orphan pinned an
            # NVENC session / VRAM, so after ~a dozen clips new encoders failed to
            # start and every write hit a broken pipe.
            def _shutdown_pipe(proc, label):
                if not proc:
                    return True
                if not encoding_successful:
                    # a stopped or failed render: its output is discarded, so the
                    # encoder is ended, not waited up to 180 s to finish a file
                    # nobody keeps (bug sweep 2026-10-09, A4)
                    reap(proc)
                    logger.info(f"{label} ended (the render did not finish).")
                    return False
                ok = True
                try:
                    if proc.stdin:
                        try:
                            proc.stdin.close()
                        except (BrokenPipeError, OSError):
                            pass  # ffmpeg already gone; that's what we're cleaning up
                    try:
                        proc.wait(timeout=180)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"{label} did not exit in 180s; terminating.")
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            logger.error(f"{label} ignored terminate; killing.")
                            proc.kill()
                            proc.wait(timeout=10)
                    rc = proc.returncode
                    logger.info(f"{label} finished with return code {rc}")
                    if rc != 0:
                        ok = False
                except Exception as e:
                    logger.warning(f"Error closing {label}: {e}")
                    ok = False
                finally:
                    for stream in (proc.stdout, proc.stderr):
                        if stream:
                            try:
                                stream.close()
                            except Exception:
                                pass
                return ok

            # every frame handed over is written before the pipe closes (a
            # stopped or failed render's are dropped: its output is discarded)
            _writer = locals().get("pipe_writer")
            if _writer is not None:
                try:
                    # bounded (bug sweep A11): a stalled encoder used to hang the
                    # render here, before _shutdown_pipe could end it
                    _writer.close(abandon=not encoding_successful,
                                  timeout=180 if encoding_successful else 30)
                except Exception as e:
                    logger.error(f"Writing to FFmpeg failed: {e}")
                    encoding_successful = False
                    if not _writer.done:
                        # the encoder stopped reading: end it, which frees the write
                        for _proc in (ffmpeg_process, splat_process, frame_break_mask_process):
                            try:
                                if _proc and _proc.poll() is None:
                                    _proc.kill()
                            except Exception:
                                pass
                        try:
                            _writer.close(abandon=True, timeout=10)
                        except Exception as e2:
                            logger.error(f"FFmpeg writer still stuck after the encoder was ended: {e2}")

            if not _shutdown_pipe(ffmpeg_process, "FFmpeg process"):
                encoding_successful = False

            if use_dnxhr_split and splat_process and splat_process is not ffmpeg_process:
                if not _shutdown_pipe(splat_process, "DNxHR splat pipe"):
                    encoding_successful = False

            # Frame Break's mask pipe is auxiliary: shut it down the same way,
            # but a failure here never fails the primary render -- Merging
            # simply won't find a usable mask and will skip bars for this clip.
            frame_break_ok = False
            if frame_break_mask_process is not None:
                frame_break_ok = _shutdown_pipe(frame_break_mask_process, "Frame Break mask pipe")

            # Every pipe is closed: publish the finished files whole, or drop
            # the parts and keep whatever was there before.
            if encoding_successful:
                for final_path, part in parts.items():
                    if not publish(part, final_path):
                        encoding_successful = False
            if not encoding_successful:
                for part in parts.values():
                    discard(part)
            if frame_break_part:
                if encoding_successful and frame_break_ok:
                    publish(frame_break_part, frame_break_mask_path)
                else:
                    discard(frame_break_part)

            del stereo_projector
            release_cuda_memory()

        # --- Final Verification ---
        if not is_test_mode and encoding_successful:
            # Combined DNxHR split mode writes a single [mask|splat] grid .mov
            # (splat_output_path) instead of the standard combined mp4. Verify the
            # file this mode actually produced.
            if use_dnxhr_split:
                expected_outputs = [splat_output_path]
            else:
                expected_outputs = [final_output_video_path]

            for out_path in expected_outputs:
                if os.path.exists(out_path):
                    file_size = os.path.getsize(out_path)
                    if file_size > 0:
                        logger.debug(
                            f"==> VERIFIED: Output file created successfully at {out_path} ({file_size / (1024 * 1024):.2f} MB)"
                        )
                    else:
                        logger.error(f"==> ERROR: Output file exists but is EMPTY (0 bytes) at {out_path}")
                        encoding_successful = False
                else:
                    logger.error(
                        f"==> ERROR: Output file was NOT FOUND at {out_path} despite FFmpeg returning 0."
                    )
                    encoding_successful = False

        return encoding_successful

    def _log_color_metadata(self, info: Optional[dict], task_name: str):
        if not info:
            return
        try:
            logger.info(
                f"[COLOR_META][{task_name}] input ffprobe: "
                f"pix_fmt={info.get('pix_fmt')}, range={info.get('color_range')}, "
                f"primaries={info.get('color_primaries')}, trc={info.get('transfer_characteristics')}, "
                f"matrix={info.get('color_space')}"
            )
        except Exception:
            pass

    def _get_encode_stream_info(self, source_info: Optional[dict], mode: str) -> dict:
        info = dict(source_info) if source_info else {}
        info["color_tags_mode"] = mode
        defaults = {
            "color_primaries": "bt709",
            "transfer_characteristics": "bt709",
            "color_space": "bt709",
            "color_range": "tv",
        }

        if mode == "Auto":
            for k, v in defaults.items():
                info.setdefault(k, v)
        elif mode in ("BT.709", "BT.709 L"):
            info.update(defaults)
        elif mode == "BT.709 F":
            info.update(defaults)
            info["color_range"] = "pc"
        elif mode in ("BT.2020", "BT.2020 PQ"):
            info.update(
                {
                    "color_primaries": "bt2020",
                    "transfer_characteristics": "smpte2084",
                    "color_space": "bt2020nc",
                    "color_range": "tv",
                }
            )
        elif mode == "BT.2020 HLG":
            info.update(
                {
                    "color_primaries": "bt2020",
                    "transfer_characteristics": "arib-std-b67",
                    "color_space": "bt2020nc",
                    "color_range": "tv",
                }
            )
        else:
            for k, v in defaults.items():
                info.setdefault(k, v)
        return info

    def _compare_encoding_flags(self, process: Any, task_name: str):
        try:
            flags = getattr(process, "sc_encode_flags", None)
            if not flags:
                return
            subset_keys = [
                "enc_codec",
                "enc_pix_fmt",
                "enc_profile",
                "enc_color_primaries",
                "enc_color_trc",
                "enc_colorspace",
                "quality_mode",
                "quality_value",
            ]
            subset = {k: flags.get(k) for k in subset_keys}
            self._color_encode_flags[task_name] = subset

            other_name = "HiRes" if task_name == "LowRes" else "LowRes"
            if other_name in self._color_encode_flags:
                other = self._color_encode_flags[other_name]
                diffs = {k: (other.get(k), subset.get(k)) for k in subset_keys if other.get(k) != subset.get(k)}
                if diffs:
                    logger.warning(f"[COLOR_META] Encoding flags differ ({other_name} vs {task_name}): {diffs}")
                else:
                    logger.debug(f"[COLOR_META] Encoding flags match between {other_name} and {task_name}.")
        except Exception:
            pass

    def _get_max_expected_raw_depth(self, info: Optional[dict]) -> float:
        pix_fmt = str(info.get("pix_fmt", "")).lower() if info else ""
        profile = str(info.get("profile", "")).lower() if info else ""

        logger.debug(f"[DEPTH] Detecting bit depth: pix_fmt='{pix_fmt}', profile='{profile}'")

        # 1. Check for High-Bit Formats
        #
        # Matched on the bit-count SUFFIX, not as a bare substring. Substring
        # matching misreads formats whose name happens to contain the digits:
        # "nv12" contains "12" (it is 8-bit, and the fallback comment below
        # already lists it as an expected 255.0 case), and "yuv410p" contains
        # "10". Reading an 8-bit nv12 depth map as 12-bit divides it by 4095
        # instead of 255, so the depth arrives ~16x too dark and the splat
        # produces almost no separation -- a silent, plausible-looking failure.
        #
        # FFmpeg spells the depth as a suffix on the plane token: yuv420p10le,
        # yuv444p12le, gray16le, p010le. Anchor on that instead. The digit run
        # is 2-3 wide because the semi-planar formats zero-pad it ("p010le" is
        # 10-bit, not 010-bit), and int() drops the leading zero for us.
        bit_suffix = re.search(r"(?:p|gray)(\d{2,3})(?:le|be)?$", pix_fmt)
        detected_bits = 0
        if bit_suffix:
            try:
                detected_bits = int(bit_suffix.group(1))
            except (TypeError, ValueError):
                detected_bits = 0

        if detected_bits >= 16 or "gray16" in pix_fmt:
            return 65535.0
        if detected_bits == 12:
            return 4095.0
        if detected_bits == 10 or "main10" in profile or "gray10" in pix_fmt:
            return 1023.0

        # 2. Check for Float
        if "float" in pix_fmt or "f32" in pix_fmt:
            return 1.0

        # 3. Default to 8-bit Range (255.0) for everything else (yuv420p, nv12, gray, etc.)
        # This is safer than 1.0 as it prevents "washed out/white" depth maps if detection is slightly off.
        if pix_fmt:
            logger.debug(f"[DEPTH] Defaulting to 8-bit (255.0) for pix_fmt='{pix_fmt}'")
            return 255.0

        return 255.0

    def _process_gpu_splatting(
        self,
        stereo_projector: ForwardWarpStereo,
        batch_video_numpy: np.ndarray,
        batch_depth_numpy_float: np.ndarray,
        target_width: int,
        target_height: int,
        max_disp: float,
        zero_disparity_anchor_val: float,
        input_bias: float,
        tv_disp_comp: float = 1.0,
        mask_mode: str = "SC",
        splat_backend: str = "SC",
        hybrid_sensitivity: float = 50.0,
        hybrid_taper: bool = False,
        m2s_fix_strength: float = 1.0,
        dual_eye: bool = False,
        dual_eye_bias: float = 0.5,
        boost_thresh: float = 0.85,
        boost_amount: float = 0.0,
        boost_curve: float = 2.0,
        boost_knee: float = 0.0,
        boost_prefilter_radius: int = 0,
        boost_prefilter_eps: float = 1e-3,
        depth_peak: float = 1.0,
        pop_amount: float = 0.0,
        is_low_res_task: bool = False,
        as_tensors: bool = False,
        rain_numpy: Optional[np.ndarray] = None,
        rain_depth_numpy: Optional[np.ndarray] = None,
        rain_amount: float = 1.0,
        rain_pop: float = 0.0,
        rain_pop_peak: Optional[float] = None,
    ) -> Union[List[dict], Dict[str, torch.Tensor]]:
        """Process GPU splatting on normalized depth maps.

        Args:
            batch_depth_numpy_float: Pre-normalized depth in range [0, 1]
            as_tensors: return the panels as [B, C, H, W] device tensors (for
                _grid_frames_gpu) instead of per-frame CPU float arrays (O1)
        """
        # CRITICAL: Ensure depth matches video resolution before GPU processing
        # batch_video_numpy: [B, H, W, 3]
        # batch_depth_numpy_float: [B, H', W'] - already normalized to [0, 1]

        video_h, video_w = batch_video_numpy.shape[1], batch_video_numpy.shape[2]

        # Handle depth shape - ensure it's [B, H, W]
        if batch_depth_numpy_float.ndim == 4:
            if batch_depth_numpy_float.shape[-1] == 1:
                batch_depth_numpy_float = batch_depth_numpy_float.squeeze(-1)
            elif batch_depth_numpy_float.shape[-1] == 3:
                batch_depth_numpy_float = batch_depth_numpy_float[..., 0]

        # Depth is already resized to video dimensions at the start of the render loop.

        # Move to GPU
        # uint8 up, converted on the device: a quarter of the bytes (O1)
        source_tensor = torch.from_numpy(batch_video_numpy).cuda().permute(0, 3, 1, 2).float() / 255.0
        depth_tensor = torch.from_numpy(batch_depth_numpy_float).unsqueeze(1).float().cuda()

        from core.splatting.forward_warp import execute_forward_warp

        # Rain (core.splatting.rain): the scene is warped WITHOUT its rain, the rain on its own at
        # its own depth -- through the same controls, so Disparity, Convergence, Boost and Pop-Out
        # move it as they move the scene -- and added. The left eye stays the source, rain and all.
        warp_source = source_tensor
        rain_tensor = rain_depth_tensor = None
        if rain_numpy is not None and rain_depth_numpy is not None:
            rain_tensor = torch.from_numpy(rain_numpy).cuda().permute(0, 3, 1, 2).float()
            rain_depth_tensor = torch.from_numpy(rain_depth_numpy).unsqueeze(1).float().cuda()
            warp_source = (source_tensor - rain_tensor).clamp(0.0, 1.0)

        def _warp_rain(eyes: int):
            """The rain warped as the scene is (plain splatting: no Edge Clean),
            plus its own Pop."""
            from core.splatting.rain import rain_pop_extra
            out = execute_forward_warp(
                extra_depth=rain_pop_extra(rain_depth_tensor, rain_pop, zero_disparity_anchor_val, max_disp,
                                           peak=rain_pop_peak),
                stereo_projector=stereo_projector, source_tensor=rain_tensor * float(rain_amount),
                depth_tensor=rain_depth_tensor, target_width=target_width, max_disp=max_disp,
                zero_disparity_anchor_val=zero_disparity_anchor_val, input_bias=input_bias,
                tv_disp_comp=tv_disp_comp, debug_task_name="Render", mask_mode="SC", splat_backend="SC",
                is_low_res_task=is_low_res_task, dual_eye=eyes == 2, dual_eye_bias=dual_eye_bias,
                boost_thresh=boost_thresh, boost_amount=boost_amount, boost_curve=boost_curve,
                boost_knee=boost_knee, pop_amount=pop_amount, depth_peak=depth_peak,
            )
            return (out[0], out[2]) if eyes == 2 else (out[0],)

        if dual_eye:
            left_eye_raw, left_mask, right_eye_raw, right_mask = execute_forward_warp(
                stereo_projector=stereo_projector,
                source_tensor=warp_source,
                depth_tensor=depth_tensor,
                target_width=target_width,
                max_disp=max_disp,
                zero_disparity_anchor_val=zero_disparity_anchor_val,
                input_bias=input_bias,
                tv_disp_comp=tv_disp_comp,
                debug_task_name="Render",
                mask_mode=mask_mode,
                splat_backend=splat_backend,
                hybrid_sensitivity=hybrid_sensitivity,
                hybrid_taper=hybrid_taper,
                m2s_fix_strength=m2s_fix_strength,
                dual_eye=True,
                dual_eye_bias=dual_eye_bias,
                is_low_res_task=is_low_res_task,
                boost_thresh=boost_thresh,
                boost_amount=boost_amount,
                boost_curve=boost_curve,
                boost_knee=boost_knee,
                boost_prefilter_radius=boost_prefilter_radius,
                boost_prefilter_eps=boost_prefilter_eps,
                pop_amount=pop_amount,
                depth_peak=depth_peak,
            )
            if rain_tensor is not None:
                rl, rr = _warp_rain(2)
                left_eye_raw = (left_eye_raw + rl).clamp(0.0, 1.0)
                right_eye_raw = (right_eye_raw + rr).clamp(0.0, 1.0)

            if as_tensors:
                return {"left": left_eye_raw, "left_mask": left_mask, "right": right_eye_raw, "occlusion": right_mask}
            left_cpu = left_eye_raw.cpu().numpy()
            left_mask_cpu = left_mask.cpu().numpy()
            right_cpu = right_eye_raw.cpu().numpy()
            right_mask_cpu = right_mask.cpu().numpy()

            results = []
            for j in range(len(batch_video_numpy)):
                results.append(
                    {
                        "left": np.clip(left_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                        "left_mask": np.clip(left_mask_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                        "right": np.clip(right_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                        "occlusion": np.clip(right_mask_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                    }
                )
            return results

        right_eye_raw, occlusion_mask = execute_forward_warp(
            stereo_projector=stereo_projector,
            source_tensor=warp_source,
            depth_tensor=depth_tensor,
            target_width=target_width,
            max_disp=max_disp,
            zero_disparity_anchor_val=zero_disparity_anchor_val,
            input_bias=input_bias,
            tv_disp_comp=tv_disp_comp,
            debug_task_name="Render",
            mask_mode=mask_mode,
            splat_backend=splat_backend,
            hybrid_sensitivity=hybrid_sensitivity,
            hybrid_taper=hybrid_taper,
            m2s_fix_strength=m2s_fix_strength,
            # Knee and the Pre-filter were passed on the dual-eye path only,
            # and neither path said which pass this is -- Mask Mode "Dual" ran
            # Hybrid on the low-res pass too (audit 2026-09-28 #17)
            is_low_res_task=is_low_res_task,
            boost_thresh=boost_thresh,
            boost_amount=boost_amount,
            boost_curve=boost_curve,
            boost_knee=boost_knee,
            boost_prefilter_radius=boost_prefilter_radius,
            boost_prefilter_eps=boost_prefilter_eps,
            pop_amount=pop_amount,
            depth_peak=depth_peak,
        )
        if rain_tensor is not None:
            right_eye_raw = (right_eye_raw + _warp_rain(1)[0]).clamp(0.0, 1.0)

        if as_tensors:
            return {"left": source_tensor, "right": right_eye_raw, "occlusion": occlusion_mask, "depth": depth_tensor}
        # CPU conversion
        left_cpu = source_tensor.cpu().numpy()
        right_cpu = right_eye_raw.cpu().numpy()
        occl_cpu = occlusion_mask.cpu().numpy()
        depth_cpu = depth_tensor.cpu().numpy()

        results = []
        for j in range(len(batch_video_numpy)):
            results.append(
                {
                    "left": np.clip(left_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                    "right": np.clip(right_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                    "occlusion": np.clip(occl_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                    "depth": np.clip(depth_cpu[j].transpose(1, 2, 0), 0.0, 1.0),
                }
            )
        return results

    def _handle_diagnostic_capture(self, batch_results: List[dict], dual_output: bool, task_name: str):
        # In test mode, we usually only have one frame
        if not batch_results:
            return
        res = batch_results[0]
        # For diagnostic captures, we always use the 4-panel grid so the depth map is available
        grid = self._construct_grid(res, dual_output=False)
        self.progress_queue.put(("diagnostic_capture", {"grid": grid, "task_name": task_name}))

    def _write_to_ffmpeg(self, process: Any, batch_results: List[dict], dual_output: bool):
        for res in batch_results:
            grid = self._construct_grid(res, dual_output)
            # Convert to 16-bit and BGR for FFmpeg
            grid_uint16 = (np.clip(grid, 0.0, 1.0) * 65535.0).astype(np.uint16)
            grid_bgr = cv2.cvtColor(grid_uint16, cv2.COLOR_RGB2BGR)
            process.stdin.write(grid_bgr.tobytes())

    def _write_split_to_ffmpeg(self, mask_process: Any, splat_process: Any, batch_results: List[dict]):
        """Write a combined [mask | splat] DNxHR grid to a single pipe.

        splat_process is the sole DNxHR pipe (mask_process is None in combined mode).
        The panel order [occlusion | right] exactly matches _construct_grid(dual_output=True)
        and the non-10-bit _splatted2 layout, so the merge stage's existing dual-input
        slicing reads it unchanged.
        """
        pipe = splat_process if splat_process is not None else mask_process
        for res in batch_results:
            occlusion = res["occlusion"]
            right = res["right"]

            if occlusion.ndim == 2 or (occlusion.ndim == 3 and occlusion.shape[-1] == 1):
                occlusion = np.stack([occlusion.squeeze()] * 3, axis=-1)

            grid = np.concatenate([occlusion, right], axis=1)  # [mask | splat]
            grid_u16 = (np.clip(grid, 0.0, 1.0) * 65535.0).astype(np.uint16)
            grid_bgr = cv2.cvtColor(grid_u16, cv2.COLOR_RGB2BGR)
            pipe.stdin.write(grid_bgr.tobytes())

    def _construct_grid(self, res: dict, dual_output: bool) -> np.ndarray:
        """Construct output grid for encoding.

        dual_output=True: [occlusion_mask | right_eye] (2-panel)
        dual_output=False: [left_eye | depth_vis]
                           [occlusion_mask | right_eye] (4-panel)

        Returns float32 array in range [0, 1]
        """
        # Convert uint8 back to float for grid assembly
        # Input frames are already float32 in range [0, 1]
        left = res["left"]
        right = res["right"]
        occlusion = res["occlusion"]
        depth = res["depth"]

        # Ensure all are 3-channel
        if occlusion.ndim == 2 or (occlusion.ndim == 3 and occlusion.shape[-1] == 1):
            occlusion = np.stack([occlusion.squeeze()] * 3, axis=-1)
        if depth.ndim == 2 or (depth.ndim == 3 and depth.shape[-1] == 1):
            depth = np.stack([depth.squeeze()] * 3, axis=-1)

        if dual_output:
            # 2-panel: occlusion on left, warped right eye on right
            return np.concatenate([occlusion, right], axis=1)
        else:
            # 4-panel: top row (left, depth), bottom row (occlusion, right)
            top_row = np.concatenate([left, depth], axis=1)
            bot_row = np.concatenate([occlusion, right], axis=1)
            return np.concatenate([top_row, bot_row], axis=0)

    def _construct_dual_eye_grid(self, res: dict) -> np.ndarray:
        """Construct the dual-eye 4-panel grid for the _splatted2D format.

        Layout:
            [ left_mask  | left_warped  ]
            [ right_mask | right_warped ]

        The inpainting stage reads top row as the left-eye (mask|warp) and
        bottom row as the right-eye (mask|warp), batches both through the UNet,
        and outputs an SBS of both inpainted eyes.

        Returns float32 array in range [0, 1].
        """
        left = res["left"]
        right = res["right"]
        left_mask = res["left_mask"]
        right_mask = res["occlusion"]  # right-eye mask stored under "occlusion"

        if left_mask.ndim == 2 or (left_mask.ndim == 3 and left_mask.shape[-1] == 1):
            left_mask = np.stack([left_mask.squeeze()] * 3, axis=-1)
        if right_mask.ndim == 2 or (right_mask.ndim == 3 and right_mask.shape[-1] == 1):
            right_mask = np.stack([right_mask.squeeze()] * 3, axis=-1)

        top_row = np.concatenate([left_mask, left], axis=1)
        bot_row = np.concatenate([right_mask, right], axis=1)
        return np.concatenate([top_row, bot_row], axis=0)

    def _write_dual_eye_to_ffmpeg(self, process: Any, batch_results: List[dict]):
        for res in batch_results:
            grid = self._construct_dual_eye_grid(res)
            grid_uint16 = (np.clip(grid, 0.0, 1.0) * 65535.0).astype(np.uint16)
            grid_bgr = cv2.cvtColor(grid_uint16, cv2.COLOR_RGB2BGR)
            process.stdin.write(grid_bgr.tobytes())
