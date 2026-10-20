import os
import json
import logging
from typing import Optional, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)


class SidecarConfigManager:
    """Handles reading, writing, and merging of stereocrafter sidecar files."""

    # CENTRAL KEY MAP: {JSON_KEY: (Python_Type, Default_Value)}
    SIDECAR_KEY_MAP = {
        "convergence_plane": (float, 0.5),
        "max_disparity": (float, 20.0),
        "true_max": (float, None),
        "dp_total_max_true": (float, None),
        "dp_total_max_est": (float, None),
        "gamma": (float, 1.0),
        "boost_thresh": (float, 0.85),
        "boost_amount": (float, 0.0),
        "boost_curve": (float, 2.0),
        "pop_out": (float, 0.0),
        "input_bias": (float, 0.0),
        "depth_dilate_size_x": (float, 0.0),
        "depth_dilate_size_y": (float, 0.0),
        "depth_blur_size_x": (float, 0.0),
        "depth_blur_size_y": (float, 0.0),
        "depth_dilate_left": (float, 0.0),
        "depth_blur_left": (float, 0.0),
        "depth_blur_left_mix": (float, 0.5),
        "selected_depth_map": (str, ""),
        "left_border": (float, 0.0),
        "right_border": (float, 0.0),
        "border_mode": (str, None),
        "auto_border_L": (float, None),
        "auto_border_R": (float, None),
        "flip_horizontal": (bool, False),
        # Subtitle protection mask (editor: subtitle_mask_editor.py).
        # Masked pixels are pinned to the convergence anchor at render time.
        "submask_enabled": (bool, False),
        "submask_core": (int, 200),
        "submask_edge": (int, 165),
        "submask_refine": (bool, True),
        "submask_r": (int, 2),
        "submask_edge_mode": (str, "Hard"),
        "submask_color": (str, "Any"),
        "submask_invert": (bool, False),
        "submask_rect": (list, None),
        # Detect Text's selections (user, 2026-10-02): [{from, to, core, edge,
        # refine, r, edge_mode, color, invert, rect}]; the keys above hold the first
        "submask_segments": (list, None),
        # Splatting's own grow / soften for that mask (user, 2026-10-02), source px
        # None = this sidecar never saved them: the page's values stand (bug sweep
        # 2026-10-09: a sidecar from before them read as 0 and overrode the page).
        # Not pop_out: Pop-Out is per clip, like Boost, so a missing one is 0 (user)
        "submask_grow": (float, None),
        "submask_soften": (float, None),
        "submask_clean": (float, None),
        "submask_conv": (float, None),
        # Rain (user, 2026-10-06; core.splatting.rain): off | moving | static, and where /
        # how strongly the separated rain goes back. None = this sidecar never saved it: the
        # GUI's setting stands (a sidecar written before Rain existed read as "off" and
        # overrode Rain turned on in Splatting -- user, 2026-10-07, the rain missing in Merging)
        "rain_mode": (str, None),
        "rain_any_direction": (bool, None),
        "rain_depth": (float, None),
        "rain_amount": (float, None),
        "rain_layers": (int, None),
        "rain_strands": (bool, None),
        "rain_lights": (bool, None),
        "rain_pop": (float, None),
        "rain_preset": (str, None),
        "rain_guard": (float, None),
        "rain_particle": (str, None),
        "rain_placement": (str, None),
        "rain_grow": (int, None),
        "rain_reflections": (bool, None),
        "rain_spread": (float, None),
        "rain_spacing": (float, None),
        "rain_conv": (float, None),
    }

    #: Keys whose GUI value stays as it is when a sidecar doesn't have them
    #: (sync_to_gui would set the text "None" or False).
    KEEP_GUI_WHEN_MISSING = frozenset({"rain_mode", "rain_any_direction", "rain_depth", "rain_amount", "rain_layers",
                                       "rain_strands", "rain_lights", "rain_pop", "rain_preset", "rain_guard",
                                       "rain_particle", "rain_placement", "rain_grow", "rain_reflections",
                                       "rain_spread", "rain_spacing", "rain_conv",
                                       "submask_grow", "submask_soften", "submask_clean", "submask_conv"})

    def _get_defaults(self) -> dict:
        """Returns a dictionary populated with all default values."""
        defaults = {}
        for key, (_, default_val) in self.SIDECAR_KEY_MAP.items():
            defaults[key] = default_val
        return defaults

    def load_sidecar_data(self, file_path: str) -> dict:
        """Loads and validates sidecar data, returning a dictionary merged with defaults."""
        data = self._get_defaults()
        if not os.path.exists(file_path):
            return data

        try:
            from core.common.atomic_json import read_json
            sidecar_json = read_json(file_path)  # a moment's lock is retried, not taken as empty

            for key, (expected_type, _) in self.SIDECAR_KEY_MAP.items():
                if key in sidecar_json:
                    val = sidecar_json[key]
                    try:
                        if expected_type is int:
                            data[key] = int(val)
                        elif expected_type is float:
                            data[key] = float(val)
                        else:
                            data[key] = val
                    except (ValueError, TypeError):
                        pass

            # Preserve unknown keys for legacy migration
            for key, val in sidecar_json.items():
                if key not in data:
                    data[key] = val

        except Exception as e:
            logger.error(f"Failed to read/parse sidecar at {file_path}: {e}")

        return data

    @staticmethod
    def read_error(file_path: str) -> Optional[str]:
        """Why the sidecar at ``file_path`` can't be read, or None: it reads, or
        there is none. load_sidecar_data answers an unreadable one with the
        defaults, which callers can't tell from a real sidecar (bug sweep
        2026-10-09 A11)."""
        if not os.path.exists(file_path):
            return None
        try:
            from core.common.atomic_json import read_json
            data = read_json(file_path)
        except Exception as e:
            return str(e) or type(e).__name__
        return None if isinstance(data, dict) else "not a JSON object"

    def save_sidecar_data(self, file_path: str, data: dict) -> bool:
        """Saves a dictionary to the sidecar file."""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            # A sidecar that won't read is kept before it is written over: the data
            # being saved came from load_sidecar_data's defaults, and the autosave
            # used to replace the clip's real settings with them (bug sweep A11).
            # If even a copy can't be made (held open), nothing is written.
            err = self.read_error(file_path)
            if err:
                kept = file_path + ".unreadable"
                if os.path.exists(kept):
                    import time
                    kept = f"{file_path}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}"
                try:
                    import shutil
                    shutil.copy2(file_path, kept)
                except OSError as e:
                    logger.error(f"Not saving {os.path.basename(file_path)}: it can't be read ({err}) and "
                                 f"couldn't be kept aside ({e}).")
                    return False
                logger.warning(f"{os.path.basename(file_path)} couldn't be read ({err}); kept as "
                               f"{os.path.basename(kept)} before saving over it.")
            output_data = {}
            for key, (expected_type, _) in self.SIDECAR_KEY_MAP.items():
                if key in data:
                    v = data[key]
                    if v is None:
                        continue
                    output_data[key] = v

            # Include any extra keys not in the map
            for key, val in data.items():
                if key not in output_data and val is not None:
                    output_data[key] = val

            # whole or not at all (audit 2026-09-28, minor): written in place, a
            # failure mid-dump left the clip's sidecar truncated
            from core.common.atomic_json import write_json_atomic
            write_json_atomic(file_path, output_data, indent=4)
            return True
        except Exception as e:
            logger.error(f"Failed to save sidecar to {file_path}: {e}")
            return False

    def get_merged_config(self, sidecar_path: str, gui_config: dict, override_keys: List[str]) -> dict:
        """Merges sidecar data with GUI configuration, allowing specific keys to be overridden."""
        merged_config = self.load_sidecar_data(sidecar_path)
        for key in override_keys:
            if key in gui_config and key in self.SIDECAR_KEY_MAP:
                expected_type = self.SIDECAR_KEY_MAP[key][0]
                try:
                    val = gui_config[key]
                    if expected_type is float:
                        merged_config[key] = float(val)
                    elif expected_type is int:
                        merged_config[key] = int(val)
                    else:
                        merged_config[key] = val
                except (ValueError, TypeError):
                    pass
        return merged_config

    # --- NEW: GUI Integration Methods ---

    def resolve_sidecar_path(self, depth_map_path: str, sidecar_folder: str, extension: str = ".fssidecar") -> str:
        """Determines the full path for the sidecar file."""
        if not depth_map_path:
            return ""
        depth_map_basename = os.path.splitext(os.path.basename(depth_map_path))[0]
        return os.path.join(sidecar_folder, f"{depth_map_basename}{extension}")

    def sync_to_gui(
        self, sidecar_data: Dict[str, Any], tk_vars: Dict[str, Any], mapping: Optional[Dict[str, str]] = None
    ) -> None:
        """Updates tkinter variables from sidecar data using the key map or a custom mapping."""
        import tkinter as tk  # here, not at the top: only this needs Tk (Studio audit tidy-up)

        for key, val in sidecar_data.items():
            if val is None and key in self.KEEP_GUI_WHEN_MISSING:
                continue
            attr_name = mapping.get(key, f"{key}_var") if mapping else f"{key}_var"
            if attr_name in tk_vars:
                var = tk_vars[attr_name]
                try:
                    if isinstance(var, tk.BooleanVar):
                        var.set(bool(val))
                    elif isinstance(var, (tk.IntVar, tk.DoubleVar)):
                        var.set(float(val))
                    else:
                        var.set(str(val))
                except Exception:
                    pass

    def capture_from_gui(self, tk_vars: Dict[str, Any], mapping: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Captures values from tkinter variables into a data dictionary using a custom mapping."""
        data = {}
        for key in self.SIDECAR_KEY_MAP:
            attr_name = mapping.get(key, f"{key}_var") if mapping else f"{key}_var"
            if attr_name in tk_vars:
                var = tk_vars[attr_name]
                try:
                    data[key] = var.get()
                except Exception:
                    pass
        return data

    def calculate_borders_from_width_bias(self, width: float, bias: float) -> Tuple[float, float]:
        """Convert GUI Width/Bias to Left/Right border values."""
        if bias <= 0:
            left_b = width
            right_b = width * (1.0 + bias)
        else:
            right_b = width
            left_b = width * (1.0 - bias)
        return left_b, right_b

    def calculate_width_bias_from_borders(self, left_b: float, right_b: float) -> Tuple[float, float]:
        """Convert Left/Right border values back to GUI Width/Bias format."""
        w = max(left_b, right_b)
        if w > 0:
            if left_b > right_b:
                b = (right_b / left_b) - 1.0
            elif right_b > left_b:
                b = 1.0 - (left_b / right_b)
            else:
                b = 0.0
        else:
            b = 0.0
        return w, b


def find_sidecar_file(base_path: str) -> Optional[str]:
    """Looks for a sidecar JSON file next to the video file."""
    sidecar_path = f"{os.path.splitext(base_path)[0]}.fssidecar"
    if os.path.exists(sidecar_path):
        return sidecar_path
    json_path = f"{os.path.splitext(base_path)[0]}.json"
    if os.path.exists(json_path):
        return json_path
    return None


def find_sidecar_in_folder(folder: str, core_name: str) -> Optional[str]:
    """Looks for a sidecar file in a specific folder."""
    fssidecar_path = os.path.join(folder, f"{core_name}.fssidecar")
    if os.path.exists(fssidecar_path):
        return fssidecar_path
    json_path = os.path.join(folder, f"{core_name}.json")
    if os.path.exists(json_path):
        return json_path
    return None


def read_clip_sidecar(
    sidecar_manager: SidecarConfigManager, video_path: str, core_name: str, search_folders: Optional[List[str]] = None
) -> dict:
    """Reads the sidecar file for a clip, checking multiple locations."""
    if search_folders:
        for folder in search_folders:
            sidecar_path = find_sidecar_in_folder(folder, core_name)
            if sidecar_path:
                return sidecar_manager.load_sidecar_data(sidecar_path)

    sidecar_path = find_sidecar_file(video_path)
    if sidecar_path:
        return sidecar_manager.load_sidecar_data(sidecar_path)

    return sidecar_manager._get_defaults()


def find_video_by_core_name(folder: str, core_name: str) -> Optional[str]:
    """Scans a folder for a file matching the core_name with any common video extension."""
    video_extensions = ("*.mp4", "*.avi", "*.mov", "*.mkv", "*.webm")
    for ext in video_extensions:
        full_path = os.path.join(folder, f"{core_name}{ext[1:]}")
        if os.path.exists(full_path):
            return full_path
    return None
