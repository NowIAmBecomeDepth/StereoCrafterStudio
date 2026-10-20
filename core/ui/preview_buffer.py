import logging
from typing import Optional, Dict, Any
from PIL import Image

logger = logging.getLogger(__name__)


class PreviewFrameBuffer:
    """
    Buffer for caching processed preview frames to enable fast playback.

    Stores processed frames in memory to avoid re-processing each frame
    during playback loop. The buffer is invalidated when processing
    parameters change.
    """

    def __init__(self, max_frames: int = 500, max_memory_mb: int = 2048):
        """
        Initialize the preview frame buffer.

        Args:
            max_frames: Maximum number of frames to cache
            max_memory_mb: Maximum memory to use for caching (approximate)
        """
        self._main_buffer: Dict[int, Image.Image] = {}
        self._display_buffer: Dict[int, Image.Image] = {}  # Pre-scaled display images
        self._params_signature: Optional[str] = None
        self._video_path: Optional[str] = None
        self._preview_size: Optional[str] = None  # Track preview size to invalidate display buffer
        self._max_frames = max_frames
        self._max_memory_mb = max_memory_mb
        self._bytes = 0          # what _main_buffer's frames hold (approx.: w * h * bands)

    def _generate_signature_from_params(self, params: Dict[str, Any], video_path: str) -> str:
        """
        Generate a signature hash from processing parameters.

        Args:
            params: Dictionary of processing parameters
            video_path: Path to current video (used to invalidate on video change)

        Returns:
            String signature that changes when parameters change
        """
        preview_size = params.get("preview_size", "100%")
        key_parts = [
            video_path or "",
            # Splatting GUI parameters
            str(params.get("max_disp", "")),
            str(params.get("convergence_point", "")),
            str(params.get("depth_gamma", "")),
            str(params.get("depth_dilate_size_x", "")),
            str(params.get("depth_dilate_size_y", "")),
            str(params.get("depth_blur_size_x", "")),
            str(params.get("depth_blur_size_y", "")),
            str(params.get("depth_dilate_left", "")),
            str(params.get("depth_blur_left", "")),
            str(params.get("depth_blur_left_mix", "")),
            str(params.get("left_border_pct", "")),
            str(params.get("right_border_pct", "")),
            str(params.get("preview_source", "")),
            preview_size,
            str(params.get("enable_global_norm", "")),
            str(params.get("strict_ffmpeg_decode", "")),
            str(params.get("flip_horizontal", False)),
            # Depth Boost and mask mode both change the rendered
            # frame, so leaving them out meant moving those sliders served a
            # stale cached frame until the user scrubbed to a different index.
            str(params.get("boost_thresh", "")),
            str(params.get("boost_amount", "")),
            str(params.get("boost_curve", "")),
            str(params.get("mask_mode", "")),
            # The warp backend and its settings, and Dual Eye: the same stale-frame bug
            # (bug sweep 2026-10-09) -- changing them re-showed a frame already drawn
            str(params.get("splat_backend", "")),
            # Merging: Highlight Mask and the put-back draw on the frame too (bug sweep
            # 2026-10-09, A8: the frame on screen, and every one visited, stayed as drawn)
            str(params.get("mask_highlight", "")),
            str(params.get("putback_sig", "")),
            str(params.get("m2s_fix_strength", "")),
            str(params.get("hybrid_sensitivity", "")),
            str(params.get("hybrid_taper", "")),
            str(params.get("dual_eye", "")),
            str(params.get("dual_eye_bias", "")),
            # These three were added alongside boost_thresh/amount/curve above
            # but never folded in here, so Boost Knee and the Edge Clean/Edge
            # Sens prefilter (radius/eps) silently served a stale cached frame
            # -- the same class of bug as the block below.
            str(params.get("boost_knee", "")),
            str(params.get("boost_prefilter_radius", "")),
            str(params.get("boost_prefilter_eps", "")),
            str(params.get("pop_amount", "")),
            str(params.get("submask_grow", "")),
            str(params.get("submask_soften", "")),
            str(params.get("submask_clean", "")),
            str(params.get("submask_conv", "")),
            # Preview Brightness and Frame Break (bars/threshold + the Edge
            # erode/dilate/blur fine-tuning) all change the displayed image but
            # were absent here entirely: changing any of them left the CURRENT
            # frame's cache entry looking unchanged to check_and_update_buffer,
            # so it kept serving the pre-change image for that frame specifically
            # -- while scrubbing to any frame not yet cached forced a fresh
            # render (via get_params_callback) that correctly picked up the new
            # value, which is what made the bug look frame-specific.
            str(params.get("preview_brightness", "")),
            str(params.get("frame_break_bars", "")),
            str(params.get("frame_break_thresh", "")),
            str(params.get("frame_break_erode_left", "")),
            str(params.get("frame_break_erode_right", "")),
            str(params.get("frame_break_erode_global", "")),
            str(params.get("frame_break_edge_blur", "")),
            # Rain (user, 2026-10-07): the same stale-frame bug -- turning Rain off left a frame
            # already drawn with its rain, and Amount "snapped back" to the value a frame was
            # first drawn with as soon as the slider was let go
            str(params.get("rain_mode", "")),
            str(params.get("rain_any_direction", "")),
            str(params.get("rain_depth", "")),
            str(params.get("rain_amount", "")),
            str(params.get("rain_layers", "")),
            str(params.get("rain_strands", "")),
            str(params.get("rain_lights", "")),
            str(params.get("rain_pop", "")),
            str(params.get("rain_preset", "")),
            str(params.get("rain_guard", "")),
            str(params.get("rain_particle", "")),
            str(params.get("rain_placement", "")),
            str(params.get("rain_grow", "")),
            str(params.get("rain_reflections", "")),
            str(params.get("rain_spread", "")),
            str(params.get("rain_spacing", "")),
            str(params.get("rain_conv", "")),
            # Digest of edits that are read off the GUI object rather than
            # passed through params: Localized Dilate regions. Without this,
            # changing a region offset is a cache hit.
            str(params.get("_edit_sig", "")),
            # Merging GUI parameters
            str(params.get("mask_binarize_threshold", "")),
            str(params.get("mask_dilate_kernel_size", "")),
            str(params.get("mask_blur_kernel_size", "")),
            str(params.get("shadow_shift", "")),
            str(params.get("shadow_decay_gamma", "")),
            str(params.get("shadow_start_opacity", "")),
            str(params.get("shadow_opacity_decay", "")),
            str(params.get("shadow_min_opacity", "")),
            str(params.get("add_borders", "")),
            str(params.get("enable_color_transfer", "")),
            str(params.get("output_format", "")),
            str(params.get("sbs_cross_eye", False)),
        ]
        return "|".join(key_parts)

    def check_and_update_buffer(self, params: Dict[str, Any], video_path: str) -> bool:
        """
        Check if buffer needs to be cleared due to parameter changes.

        Args:
            params: Current processing parameters
            video_path: Path to current video

        Returns:
            True if buffer was cleared, False otherwise
        """
        current_sig = self._generate_signature_from_params(params, video_path)
        preview_size = params.get("preview_size", "100%")

        if (
            self._params_signature != current_sig
            or self._video_path != video_path
            or self._preview_size != preview_size
        ):
            self.clear()
            self._params_signature = current_sig
            self._video_path = video_path
            self._preview_size = preview_size
            logger.debug("Preview buffer cleared due to parameter or video change")
            return True
        return False

    def get_cached_frame(self, frame_idx: int) -> Optional[Image.Image]:
        """Get a cached processed frame."""
        return self._main_buffer.get(frame_idx)

    @staticmethod
    def _nbytes(frame) -> int:
        try:
            return int(frame.width) * int(frame.height) * len(frame.getbands())
        except Exception:
            return 0

    def cache_frame(self, frame_idx: int, frame: Image.Image) -> None:
        """Cache a processed frame. Capped by count AND by max_memory_mb, which
        was stored and never read: 500 full frames at 4K SBS held gigabytes for
        the session (bug sweep 2026-10-09, A6 / A8). The lowest frame goes first."""
        old = self._main_buffer.pop(frame_idx, None)
        if old is not None:
            self._bytes -= self._nbytes(old)
        cap = int(self._max_memory_mb) * 2 ** 20
        size = self._nbytes(frame)
        while self._main_buffer and (len(self._main_buffer) >= self._max_frames or self._bytes + size > cap):
            oldest_idx = min(self._main_buffer.keys())
            self._bytes -= self._nbytes(self._main_buffer.pop(oldest_idx))
            # Also remove from display buffer
            if oldest_idx in self._display_buffer:
                del self._display_buffer[oldest_idx]
        self._main_buffer[frame_idx] = frame
        self._bytes += size

    def get_cached_display_frame(self, frame_idx: int) -> Optional[Image.Image]:
        """Get a cached display-ready (scaled) frame."""
        return self._display_buffer.get(frame_idx)

    def cache_display_frame(self, frame_idx: int, display_frame: Image.Image) -> None:
        """Cache a display-ready (scaled) frame."""
        if len(self._display_buffer) >= self._max_frames:
            oldest_idx = min(self._display_buffer.keys())
            del self._display_buffer[oldest_idx]
        self._display_buffer[frame_idx] = display_frame

    def clear(self) -> None:
        """Clear all cached frames."""
        self._main_buffer.clear()
        self._display_buffer.clear()
        self._bytes = 0
        logger.debug("Preview frame buffers cleared")

    def get_stats(self) -> Dict[str, int]:
        """Get buffer statistics for debugging."""
        return {"main_buffer_size": len(self._main_buffer), "display_buffer_size": len(self._display_buffer)}
