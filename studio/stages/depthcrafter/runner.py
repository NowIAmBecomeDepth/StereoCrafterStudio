"""runner.py -- DepthCrafter batch orchestration, ported out of the Tk GUI.

Source: ``depthcrafter_gui_seg.py`` (v1, GUI_VERSION 26-03-29.0). v1 keeps
this logic inside its Tk class, reading tk variables as it goes; here it reads
one frozen settings snapshot (``settings.snapshot``) and reports through a
:class:`Hooks` object instead of StringVars, message queues and messageboxes.
Nothing here imports Qt or Tk.

Each method is named after the v1 method it came from, and keeps its order of
operations and its log lines, so a fix to one can be carried to the other by
reading them side by side. While v1 is still in use, a behaviour fix in v1's
batch path needs making here too (and vice versa).

Two deliberate differences from v1, both fixes:

* v1's full-video "Overwrite?" prompt calls ``messagebox.askyesno`` from the
  worker thread, the threading bug its own ``_ask_yes_no_cancel`` exists to
  avoid. Here every prompt goes through ``hooks.ask``, which the controller
  marshals to the UI thread.
* v1 reads a few settings live off tk variables mid-run (output folder,
  merge options, segment flag). Here all of them come from the snapshot taken
  at Start, so editing a field during a run cannot change the run.

The model (torch, diffusers, DepthCrafterDemo) is imported only when a batch
actually starts, so the UI opens without loading any of it.
"""

from __future__ import annotations

import gc
import logging
import os
import random
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from studio.outcome import Outcome, is_out_of_memory

#: What to change when DepthCrafter runs out of graphics memory (release check 2026-10-17: a smaller card got
#: only "1 failed")
OOM_HINT = ("It ran out of graphics memory. Turn on Process as Segments (Low VRAM Mode), or lower Window Size "
            "or the Target Width / Height, then run it again.")

#: The models, as the installer or Settings > Studio > Models put them in the models folder (release check
#: 2026-10-17: DepthCrafter only ever asked the Hub for them, so the installer's ~10 GB sat unused and the first
#: job downloaded them again, the SVD base from another, gated repo). DepthCrafter's UNet is the Hub's own file;
#: of SVD it takes the VAE, image encoder, scheduler and feature extractor, which are byte-identical in -xt and
#: -xt-1-1 (measured: the UNet, the only part that differs, is DepthCrafter's). The Hub ids only when Hugging
#: Face's cache already holds them (2026-10-20: no more silent downloads -- the Models window says what's missing).
DEPTHCRAFTER_HUB = "tencent/DepthCrafter"
SVD_HUB = "stabilityai/stable-video-diffusion-img2vid-xt"


class ModelsMissing(Exception):
    """DepthCrafter's models are in neither the models folder nor Hugging Face's cache."""


def _in_hub_cache(repo_id: str, filename: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(repo_id, filename), str)
    except Exception:
        return False


def model_paths(repo: Optional[str] = None) -> Tuple[str, str]:
    """(DepthCrafter's UNet, the SVD base): their folders in the models folder (core.weights_paths, then
    <repo>/weights; only <repo>/weights when `repo` is given), else the Hugging Face ids when its cache has
    them. Neither: ModelsMissing, saying where to get them."""
    from pathlib import Path
    from studio import model_catalog as C
    root = Path(repo) / "weights" if repo else None
    found, lacking = [], []
    for part, hub, probe in ((C.DEPTHCRAFTER, DEPTHCRAFTER_HUB, "config.json"),
                             (C.SVD, SVD_HUB, "model_index.json")):
        if C.installed([part], root):
            found.append(str(C.part_dir(part, root)))
        elif _in_hub_cache(hub, probe):
            found.append(hub)
        else:
            found.append("")
            lacking.append(C.MODELS_BY_KEY[part.key].name.split(" (")[0])
    if lacking:
        where = str(root) if root is not None else str(C.weights_paths.root())
        raise ModelsMissing(f"{' and '.join(lacking)} {'is' if len(lacking) == 1 else 'are'} not in your models folder "
                            f"({where}). Download {'it' if len(lacking) == 1 else 'them'} in Settings > Studio > "
                            "Models, or point Studio at the folder that has them there.")
    return found[0], found[1]

_logger = logging.getLogger("depthcrafter.v2.runner")

VIDEO_EXTENSIONS = ["*.mp4", "*.avi", "*.mov", "*.mkv", "*.webm", "*.flv", "*.gif"]
IMAGE_EXTENSIONS = ["*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff", "*.exr"]

# v1: MOVE_ORIGINAL_TO_FINISHED_FOLDER_ON_COMPLETION
MOVE_ORIGINAL_TO_FINISHED_FOLDER_ON_COMPLETION = True


class InputError(Exception):
    """The input path cannot be turned into a list of sources."""


class Hooks:
    """What the runner reports and asks. Every method may be called from the
    worker thread; the controller's implementation marshals to the UI."""

    def status(self, text: str) -> None:
        pass

    def progress(self, done: int, total: int) -> None:
        pass

    def clip(self, filename: str, resolution: str, frames: str) -> None:
        pass

    def ask(self, title: str, message: str, allow_cancel: bool = True) -> Optional[bool]:
        """Yes -> True, No -> False, Cancel -> None. Blocks until answered."""
        return None


# ---------------------------------------------------------------------------
# Source discovery (v1: start_thread, _determine_input_mode_from_path,
# _is_image_sequence_folder)
# ---------------------------------------------------------------------------


def _ext_in(ext: str, patterns: List[str]) -> bool:
    return any(ext in p.replace("*", "") for p in patterns)


def is_image_sequence_folder(folder_path: str) -> bool:
    """Rudimentary check if a folder looks like an image sequence (v1)."""
    if not os.path.isdir(folder_path):
        return False
    image_files_count = video_files_count = sub_dirs_count = 0
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isdir(item_path):
            sub_dirs_count += 1
            continue
        ext = os.path.splitext(item)[1].lower()
        if _ext_in(ext, IMAGE_EXTENSIONS):
            image_files_count += 1
        elif _ext_in(ext, VIDEO_EXTENSIONS):
            video_files_count += 1
    return image_files_count > 5 and video_files_count == 0 and sub_dirs_count == 0


def determine_input_mode(path_str: str) -> Tuple[str, bool]:
    """(input_mode, is_single_source) for a path (v1 _determine_input_mode_from_path)."""
    if not path_str or not os.path.exists(path_str):
        _logger.warning(f"GUI Input: Path '{path_str}' is invalid or does not exist. Cannot determine input mode accurately.")
        return "batch_folder", False
    if os.path.isfile(path_str):
        ext = os.path.splitext(path_str)[1].lower()
        if _ext_in(ext, VIDEO_EXTENSIONS):
            return "single_video_file", True
        if _ext_in(ext, IMAGE_EXTENSIONS):
            return "single_image_file", True
        _logger.warning(f"GUI Input: Typed path '{path_str}' is a file of unknown type. Treating as non-single source (batch fallback).")
        return "batch_folder", False
    if os.path.isdir(path_str):
        if is_image_sequence_folder(path_str):
            return "image_sequence_folder", True
        return "batch_folder", False
    _logger.warning(f"GUI Input: Path '{path_str}' exists but is not a regular file or directory.")
    return "batch_folder", False


def discover_sources(input_path_str: str) -> Tuple[List[Dict[str, str]], bool]:
    """The source specs a Start would process, and whether it is single-source.

    Raises InputError with a user-facing message where v1 showed an error box.
    """
    if not input_path_str or not os.path.exists(input_path_str):
        raise InputError(f"Input path does not exist: {input_path_str}")
    mode, single = determine_input_mode(input_path_str)
    specs: List[Dict[str, str]] = []
    if single:
        if mode == "image_sequence_folder":
            basename = os.path.basename(input_path_str)
        else:
            basename = os.path.splitext(os.path.basename(input_path_str))[0]
        specs.append({"path": input_path_str, "type": mode, "basename": basename})
        return specs, True
    if mode != "batch_folder":
        raise InputError(f"Unexpected input mode '{mode}' for path '{input_path_str}'. Please report this.")
    try:
        for item_name in os.listdir(input_path_str):
            item_full_path = os.path.join(input_path_str, item_name)
            if os.path.isfile(item_full_path):
                ext = os.path.splitext(item_name)[1].lower()
                from core.common.submask_files import is_subtitle_mask
                if _ext_in(ext, VIDEO_EXTENSIONS) and not is_subtitle_mask(item_name):
                    specs.append({"path": item_full_path, "type": "video_file",
                                  "basename": os.path.splitext(item_name)[0]})
            elif os.path.isdir(item_full_path) and is_image_sequence_folder(item_full_path):
                specs.append({"path": item_full_path, "type": "image_sequence_folder", "basename": item_name})
    except NotADirectoryError:
        raise InputError(f"Input path is not a directory for batch processing: {input_path_str}")
    except OSError as e:
        raise InputError(f"Could not read directory contents for '{input_path_str}':\n{e}")
    # os.listdir's order is the file system's, not the name order Upcoming Jobs
    # shows (clip_queue.dc_clips): sorted the same way, the batch starts at the
    # top of the list instead of part-way down it
    specs.sort(key=lambda s: os.path.basename(s["path"]).lower())
    return specs, False


def effective_seed(seed_setting: int) -> int:
    """A negative seed means a fresh random one for this run (v1 start_thread)."""
    if seed_setting < 0:
        seed = random.randint(0, 2**32 - 1)
        _logger.debug(f"GUI: Seed was set to {seed_setting} (negative). Generating a new random seed for this run: {seed}")
        return seed
    _logger.debug(f"GUI: Using user-specified seed: {seed_setting}")
    return seed_setting


# ---------------------------------------------------------------------------


class DepthCrafterRunner:
    """One batch, re-merge or visual-generation run over a frozen snapshot."""

    def __init__(self, opts: Dict[str, Any], hooks: Hooks, stop_event: threading.Event,
                 move_originals: bool = MOVE_ORIGINAL_TO_FINISHED_FOLDER_ON_COMPLETION,
                 stop_now: Optional[threading.Event] = None):
        self.opts = opts
        self.hooks = hooks
        # stop_event: stop at the next safe point (between segments / clips).
        # stop_now: also cut the running inference off at its next denoising
        # step (audit #13); the controller sets both for Stop, only
        # stop_event for "Stop after this segment". Unset: stop_event is both.
        self.stop_event = stop_event
        self.stop_now = stop_now if stop_now is not None else stop_event
        # v1: effective_move_original_on_completion -- off for single sources.
        self.effective_move_original_on_completion = move_originals
        self._shown_resolution = "N/A"
        self._shown_frames = "N/A"
        # the last run_batch's sources: done / failed / skipped (audit #3)
        self.outcome = Outcome()

    # v1 _opt(): a setting captured for the current run.
    def _opt(self, key: str, default=None):
        return self.opts.get(key, default)

    @property
    def output_dir(self) -> str:
        return self.opts["output_dir"]

    # -- the batch (v1 start_processing) ------------------------------------

    #: VRAM left free for Windows and Studio's own window while DepthCrafter
    #: runs (user, 2026-10-01). Uncapped, PyTorch kept every block it ever
    #: used: the card filled (15.9 of 15.9 GB), the desktop and Studio's window
    #: crawled, and DepthCrafter itself ran slower. With the feed-forward
    #: sliced (depthcrafter.unet.chunk_feed_forward) window 120 at 768x448
    #: needs 9.3 GB live: capped 3 GB short of the card it ran in 597 s
    #: against 625 s uncapped -- on the user's 16 GB card. Other cards need
    #: other figures (user, 2026-10-02), so Studio ships with no cap: a PC
    #: opts in with "depthcrafter_vram_headroom_gb" in config/local.json
    #: (settings_store.local_option; git ignores config/).
    LOCAL_KEY = "depthcrafter_vram_headroom_gb"

    @classmethod
    def vram_headroom_gb(cls) -> float:
        """This PC's headroom in GB; 0: no cap (the default)."""
        from studio import settings_store
        try:
            return max(0.0, float(settings_store.local_option(cls.LOCAL_KEY, 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _cap_vram(cls, on: bool) -> bool:
        """Cap (or uncap) this process's CUDA memory. The cap is PyTorch's own
        limit: at it, the allocator frees its cached blocks and reuses them
        instead of asking Windows for more (which, on WDDM, spills into
        system memory rather than failing). True when a cap is now set."""
        try:
            import torch
            if not torch.cuda.is_available():
                return False
            if not on:
                torch.cuda.set_per_process_memory_fraction(1.0)
                return False
            headroom = cls.vram_headroom_gb()
            if headroom <= 0:                # not opted in: no cap
                return False
            total = torch.cuda.get_device_properties(0).total_memory
            cap = total - headroom * 2**30
            if cap < total * 0.5:            # a small card: no cap, as before
                return False
            torch.cuda.set_per_process_memory_fraction(cap / total)
            _logger.info(f"VRAM cap for DepthCrafter: {cap / 2**30:.1f} of {total / 2**30:.1f} GB "
                         f"({headroom:g} GB kept for the desktop; config/local.json).")
            return True
        except Exception as e:
            _logger.debug(f"VRAM cap not set: {e}")
            return False

    def run_batch(self, source_specs_to_process: List[Dict[str, str]], effective_seed_for_run: int) -> bool:
        """Process every source. True when it ran to the end (not cancelled)."""
        from depthcrafter.depthcrafter_logic import DepthCrafterDemo
        from depthcrafter.utils import define_video_segments, get_full_video_output_filename, get_segment_output_folder_name

        o = self.opts
        total = len(source_specs_to_process)
        user_size = (o["target_width"], o["target_height"])
        _logger.debug(f"Starting lazy batch processing for {total} sources...")
        self.hooks.status("Starting processing...")
        self.hooks.progress(0, total)

        all_videos_master_metadata: Dict[str, Any] = {}
        base_job_info_map: Dict[str, Any] = {}
        demo = None
        self.outcome = outcome = Outcome()
        # The model sets cudnn.benchmark for the whole process: put back when
        # the run ends, or every stage after ran with it (audit 2026-09-28).
        import torch
        cudnn_was = torch.backends.cudnn.benchmark
        try:
            if not o["use_local_models_only_var"]:
                _logger.info("Attempting to check model at Hugging Face Hub against local.")
            else:
                _logger.info("Attempting to load local model.")
            self.hooks.status("Loading DepthCrafter model...")
            unet_path, svd_path = model_paths()
            _logger.info("DepthCrafter model: %s; SVD base: %s", unet_path, svd_path)
            demo = DepthCrafterDemo(
                unet_path=unet_path,
                pre_train_path=svd_path,
                cpu_offload=o["cpu_offload"],
                use_cudnn_benchmark=o["use_cudnn_benchmark"],
                local_files_only=o["use_local_models_only_var"],
                use_sageattention=o["use_sageattention_var"],
            )
            demo.stop_event = self.stop_now  # Stop now ends the clip at its next step (#13)
            self._capped = self._cap_vram(True)
        except Exception as e:
            _logger.exception(f"CRITICAL: Failed to initialize DepthCrafterDemo: {e}")
            self.hooks.status("Error: Model initialization failed. See console.")
            self.hooks.clip("N/A", "N/A", "N/A")
            outcome.error = f"The DepthCrafter model could not be loaded ({e.__class__.__name__})."
            if isinstance(e, ModelsMissing):
                outcome.error = str(e)
            if is_out_of_memory(e):
                outcome.hint = OOM_HINT
            torch.backends.cudnn.benchmark = cudnn_was
            return False

        total_sources_processed = 0
        try:
            for source_idx, source_spec in enumerate(source_specs_to_process):
                if self.stop_event.is_set():
                    _logger.info("Processing cancelled by user.")
                    self.hooks.status("Cancelled.")
                    break

                current_video_path = source_spec["path"]
                original_basename = source_spec["basename"]
                # here, not at the top: core.common's package imports torch, and
                # Studio loads this module at start-up
                from core.common.cli_utils import print_job_header
                print_job_header(source_idx + 1, len(source_specs_to_process), current_video_path)
                current_gui_mode = source_spec["type"]
                # every later read of the size for this source goes through o
                o["target_width"], o["target_height"] = self._size_for_source(source_spec, user_size)

                gui_fps_setting = o["target_fps"]
                gui_len_setting = o["process_length"]
                gui_win_setting = o["window_size"]
                gui_ov_setting = o["overlap"]

                log_msg_base = f"Source {source_idx + 1}/{total}: {original_basename}"
                _logger.debug(f"--- Defining Jobs for {log_msg_base}...")
                self.hooks.status(f"Defining jobs for {source_idx + 1} of {total}")

                if current_gui_mode in ("single_video_file", "video_file"):
                    source_type_for_define = "video_file"
                elif current_gui_mode == "image_sequence_folder":
                    source_type_for_define = "image_sequence_folder"
                elif current_gui_mode == "single_image_file":
                    source_type_for_define = "single_image_file"
                else:
                    _logger.error(f"Lazy Job Definition: Unknown source_spec type '{current_gui_mode}' for basename '{original_basename}'. Skipping.")
                    outcome.failed += 1
                    total_sources_processed += 1
                    self.hooks.progress(total_sources_processed, total)
                    continue

                # A. metadata extraction / segment definition
                try:
                    all_potential_segments_for_video, base_job_info_initial = define_video_segments(
                        video_path_or_folder=current_video_path,
                        original_basename=original_basename,
                        gui_target_fps_setting=gui_fps_setting,
                        gui_process_length_overall=gui_len_setting,
                        gui_segment_output_window_frames=gui_win_setting,
                        gui_segment_output_overlap_frames=gui_ov_setting,
                        source_type=source_type_for_define,
                        gui_target_height_setting=o["target_height"],
                        gui_target_width_setting=o["target_width"],
                    )
                except Exception as e_metadata:
                    _logger.error(f"Skipping {original_basename}: File not found or metadata extraction failed. Error: {e_metadata.__class__.__name__}: {e_metadata}")
                    self.hooks.status(f"Error: Missing file or metadata fail for {original_basename}. Skipping.")
                    outcome.failed += 1
                    total_sources_processed += 1
                    self.hooks.progress(total_sources_processed, total)
                    continue

                if not base_job_info_initial:
                    _logger.info(f"Skipping {original_basename}: Issues in metadata extraction/segment definition.")
                    outcome.failed += 1
                    total_sources_processed += 1
                    self.hooks.progress(total_sources_processed, total)
                    continue

                base_job_info_map[current_video_path] = base_job_info_initial.copy()
                jobs_to_process_for_this_source: List[Dict[str, Any]] = []
                is_segment_processing = o["process_as_segments_var"]

                # B. the job list: segments (with resume) or the full video
                if is_segment_processing:
                    if not all_potential_segments_for_video:
                        reason_skip = ("Too short or invalid overlap/settings"
                                       if base_job_info_initial.get("original_video_raw_frame_count", 0) > 0
                                       else "Source issue or zero frames/duration")
                        _logger.info(f"Skipping {original_basename}: No segments defined by settings (Reason: {reason_skip}).")
                        outcome.failed += 1
                        total_sources_processed += 1
                        self.hooks.progress(total_sources_processed, total)
                        continue

                    segment_subfolder_path = os.path.join(self.output_dir, get_segment_output_folder_name(original_basename))
                    current_video_base_info_ref = base_job_info_map[current_video_path]
                    jobs_to_process_for_this_source, action_taken = self._get_segments_to_resume_or_overwrite(
                        current_video_path, original_basename, segment_subfolder_path,
                        all_potential_segments_for_video, current_video_base_info_ref,
                    )
                    _logger.debug(f"For source '{original_basename}': Action '{action_taken}', {len(jobs_to_process_for_this_source)} segments will be processed.")
                    if not jobs_to_process_for_this_source and not current_video_base_info_ref.get("pre_existing_successful_jobs"):
                        _logger.info(f"Skipping {original_basename}: Job definition/resume resulted in no segments to process.")
                        outcome.skipped += 1
                        total_sources_processed += 1
                        self.hooks.progress(total_sources_processed, total)
                        continue
                else:
                    full_out_check_path = os.path.join(self.output_dir, get_full_video_output_filename(original_basename, "mp4"))
                    proceed_full = True
                    if os.path.exists(full_out_check_path) and not self._opt("test_render", False):
                        answer = self.hooks.ask(
                            "Overwrite?",
                            f"An output file for '{original_basename}' might exist (e.g., MP4):\n{full_out_check_path}\n\nOverwrite if it exists?",
                            allow_cancel=False,
                        )
                        if not answer:
                            _logger.info(f"Skipping {original_basename} (full video processing, user chose not to overwrite).")
                            proceed_full = False
                    if proceed_full:
                        jobs_to_process_for_this_source.append({
                            **base_job_info_initial,
                            "is_segment": False,
                            "gui_desired_output_window_frames": gui_win_setting,
                            "gui_desired_output_overlap_frames": gui_ov_setting,
                        })
                    else:
                        outcome.skipped += 1
                        total_sources_processed += 1
                        self.hooks.progress(total_sources_processed, total)
                        continue

                # C. master metadata for this source
                total_expected_jobs_overall = len(all_potential_segments_for_video) if is_segment_processing else 1
                all_videos_master_metadata[current_video_path] = self._initialize_master_metadata_entry(
                    original_basename, base_job_info_initial, total_expected_jobs_overall)
                master_meta_for_this_vid = all_videos_master_metadata[current_video_path]
                master_meta_for_this_vid["global_processing_settings"]["seed_setting"] = effective_seed_for_run

                pre_existing = base_job_info_map[current_video_path].get("pre_existing_successful_jobs", [])
                if pre_existing:
                    _logger.debug(f"Loading {len(pre_existing)} pre-existing successful segment metadata entries for {original_basename} into current run's master data.")
                    master_meta_for_this_vid["jobs_info"].extend(pre_existing)
                    master_meta_for_this_vid["completed_successful_jobs"] += len(pre_existing)

                # D. run the jobs
                skipped_by_user = False
                total_jobs_for_source = len(jobs_to_process_for_this_source)
                for job_idx, job_info_to_run in enumerate(jobs_to_process_for_this_source):
                    if self.stop_event.is_set():
                        break
                    is_segment_job = job_info_to_run.get("is_segment", False)
                    # a segment is named; a whole video needs no "Full video (1/1)" (user, 2026-10-04)
                    log_msg_prefix = (
                        f"segment {job_info_to_run.get('segment_id', -1) + 1} of {job_info_to_run.get('total_segments', 0)}"
                        if is_segment_job else ""
                    )
                    self._update_gui_info_on_job_start(job_info_to_run, original_basename, log_msg_prefix)
                    _logger.info(f"Processing {original_basename}" + (f" - {log_msg_prefix}" if log_msg_prefix else ""))
                    # "Processing video 1 of 35, segment 2 of 4" (user, 2026-10-03: "Full video (1/1)" and
                    # the segment counted twice were noise). The segment's own number, not the job's: a
                    # resumed clip skips the segments already done.
                    status = f"Processing video {source_idx + 1} of {total}"
                    if is_segment_job:
                        status += (f", segment {job_info_to_run.get('segment_id', -1) + 1}"
                                   f" of {job_info_to_run.get('total_segments', 0)}")
                    self.hooks.status(status)

                    job_successful, current_job_specific_metadata = self._process_single_job(
                        demo, job_info_to_run, master_meta_for_this_vid)
                    if current_job_specific_metadata is None:
                        _logger.error(f"Error: _process_single_job for {original_basename} returned None metadata. Initializing to empty dict.")
                        current_job_specific_metadata = {}
                    if str(current_job_specific_metadata.get("status", "")).startswith("skipped_by_user"):
                        # Cancel at a prompt = skip this video: not a failed job,
                        # so no finalizing and no move to failed/ (audit, minor)
                        skipped_by_user = True
                        break
                    self._update_gui_info_on_job_finish(job_info_to_run, current_job_specific_metadata)

                    if is_segment_job and "segment_id" not in current_job_specific_metadata:
                        current_job_specific_metadata["segment_id"] = job_info_to_run.get("segment_id", -1)
                    current_job_specific_metadata.pop("_individual_metadata_path", None)
                    master_meta_for_this_vid["jobs_info"].append(current_job_specific_metadata)
                    if job_successful:
                        master_meta_for_this_vid["completed_successful_jobs"] += 1
                    else:
                        master_meta_for_this_vid["completed_failed_jobs"] += 1

                # E. finalize once every job is accounted for
                accounted = (master_meta_for_this_vid["completed_successful_jobs"]
                             + master_meta_for_this_vid["completed_failed_jobs"])
                if skipped_by_user:
                    _logger.info(f"Skipped {original_basename} as asked; it stays where it is.")
                    outcome.skipped += 1
                elif accounted >= master_meta_for_this_vid["total_expected_jobs"]:
                    # Every job ran to its end: finalize, even when "Stop after this
                    # segment / clip" came in during the last one -- what it waited
                    # for is complete (bug sweep 2026-10-09, A3: the merge, the move
                    # and the JSON were skipped). Only a job cut off by Stop holds it.
                    cut_short = self.stop_now.is_set() or any(
                        j.get("status") == "stopped_by_user" for j in master_meta_for_this_vid["jobs_info"])
                    if not cut_short:
                        if self._finalize_video_processing(current_video_path, original_basename,
                                                           master_meta_for_this_vid):
                            outcome.done += 1
                        else:
                            outcome.failed += 1
                    else:
                        _logger.info(f"Skipping finalization of {original_basename} due to user cancellation.")

                # F. one progress unit per source
                total_sources_processed += 1
                self.hooks.progress(total_sources_processed, total)

            if not self.stop_event.is_set():
                _logger.info("All processing sources complete!")
                self.hooks.status("Processing Finished.")
            else:
                self.hooks.status("Processing Cancelled.")
        finally:
            self._release_model(demo)
            torch.backends.cudnn.benchmark = cudnn_was
            if getattr(self, "_capped", False):
                self._capped = self._cap_vram(False)
            if getattr(self, "_out_of_vram", False):
                outcome.hint = OOM_HINT
        return not self.stop_event.is_set()

    def _size_for_source(self, source_spec, user_size):
        """Auto size (user, 2026-10-05): a clip the shape of the one loaded on
        the page runs at Target Width/Height (whatever the user made of the
        auto pick); a clip of another shape runs at its own shape's pick."""
        o = self.opts
        if not o.get("auto_resolution_var") or source_spec.get("type") not in ("single_video_file", "video_file"):
            return user_size
        from . import settings as S
        shape = S.ratio_class(S.video_ratio(source_spec["path"]))
        if shape is None or shape == o.get("auto_loaded_class"):
            return user_size
        w, h = (o.get("auto_sizes") or {}).get(shape) or S.default_auto_size(shape)
        _logger.info(f"{source_spec['basename']} is {shape}: Auto size {w}x{h} "
                     f"(Target Width/Height are for {o.get('auto_loaded_class') or 'the loaded clip'}).")
        return int(w), int(h)

    @staticmethod
    def _release_model(demo) -> None:
        """G. Cleanup: drop the pipeline and hand VRAM back (v1)."""
        if demo is not None:
            try:
                pipe = getattr(demo, "pipe", None)
                if pipe is not None:
                    for part in ("vae", "unet"):
                        if getattr(pipe, part, None) is not None:
                            delattr(pipe, part)
                    del demo.pipe
                _logger.debug("DepthCrafter model components released.")
            except Exception as e_cleanup:
                _logger.warning(f"Error during DepthCrafter model cleanup: {e_cleanup}")
        del demo
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                _logger.info("CUDA cache cleared.")
        except Exception:
            pass

    # -- one job (v1 _process_single_job) -------------------------------------

    def _run_demo(self, demo, **kwargs):
        """demo.run, once more without the VRAM cap if the cap was what it ran
        out of: settings bigger than the cap allows still run, as they did
        before it (just with the card full)."""
        try:
            return demo.run(**kwargs)
        except Exception as e:
            if type(e).__name__ != "OutOfMemoryError" or not getattr(self, "_capped", False):
                raise
            _logger.warning("Out of VRAM under the cap; lifting it and running this job again "
                            "(the desktop may be slow while it runs).")
        # Out of the except first (bug sweep 2026-10-09, A3): the exception's
        # traceback holds the failed run's frames -- its video and latents on the
        # GPU -- for as long as the handler runs, so a retry from inside it
        # started with that memory still taken and ran out again.
        self._capped = self._cap_vram(False)
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        return demo.run(**kwargs)

    def _process_single_job(self, demo, job_info, master_meta_for_this_vid):
        from depthcrafter.depthcrafter_logic import DepthCrafterDemo

        returned_job_specific_metadata: Dict[str, Any] = {}
        job_successful = False
        is_segment_job = job_info.get("is_segment", False)
        original_basename = job_info["original_basename"]

        snap = master_meta_for_this_vid["global_processing_settings"]
        process_length_for_run_param = snap["process_max_frames_setting"] if not is_segment_job else -1

        try:
            keep_npz_for_this_job_run = False
            if is_segment_job and self._opt("keep_intermediate_npz"):
                min_frames_thresh = self._opt("min_frames_to_keep_npz", 0)
                orig_vid_frame_count = job_info.get("original_video_raw_frame_count", 0)
                if min_frames_thresh <= 0 or orig_vid_frame_count >= min_frames_thresh:
                    keep_npz_for_this_job_run = True

            # Latent checkpoint: ask before reusing a previous denoise.
            latent_ckpt_enabled = bool(self._opt("latent_checkpoint")) and not is_segment_job
            latent_ckpt_resume = False
            if latent_ckpt_enabled:
                ckpt_path = DepthCrafterDemo.get_latent_checkpoint_path(self.output_dir, original_basename)
                if os.path.exists(ckpt_path):
                    try:
                        ckpt_mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(ckpt_path)))
                        ckpt_size_mb = os.path.getsize(ckpt_path) / (1024.0 * 1024.0)
                    except Exception:
                        ckpt_mtime, ckpt_size_mb = "unknown", 0.0
                    choice_ckpt = self.hooks.ask(
                        "Resume From Latent Checkpoint?",
                        f"A latent checkpoint was found for '{original_basename}'.\n"
                        f"Path: {ckpt_path}\n"
                        f"Written: {ckpt_mtime}  ({ckpt_size_mb:.0f} MB)\n\n"
                        f"Yes: resume from these latents. Encoding and denoising are skipped and the "
                        f"job goes straight to decode. A checkpoint made with different settings is "
                        f"rejected automatically and the job restarts instead.\n"
                        f"No: delete the checkpoint and run the whole job from scratch.\n"
                        f"Cancel: skip this video entirely.",
                    )
                    if choice_ckpt is None:
                        _logger.info(f"Skipping {original_basename} (user cancelled at latent checkpoint prompt).")
                        return False, {"status": "skipped_by_user_at_latent_checkpoint"}
                    elif choice_ckpt is True:
                        latent_ckpt_resume = True
                        _logger.info(f"Resuming {original_basename} from latent checkpoint: {ckpt_path}")
                    else:
                        try:
                            os.remove(ckpt_path)
                            _logger.info(f"Deleted latent checkpoint, starting fresh: {ckpt_path}")
                        except Exception as e_rm:
                            _logger.warning(f"Could not delete latent checkpoint {ckpt_path}: {e_rm}")

            saved_data_filepath, returned_job_specific_metadata = self._run_demo(
                demo,
                video_path_or_frames_or_info=job_info,
                num_denoising_steps=snap["inference_steps"],
                guidance_scale=snap["guidance_scale"],
                base_output_folder=self.output_dir,
                gui_window_size=snap["gui_window_size_setting"],
                gui_overlap=snap["gui_overlap_setting"],
                process_length_for_read_full_video=process_length_for_run_param,
                target_height=self.opts["target_height"],
                target_width=self.opts["target_width"],
                seed=snap["seed_setting"],
                original_video_basename_override=original_basename,
                segment_job_info_param=job_info if is_segment_job else None,
                keep_intermediate_npz_config=keep_npz_for_this_job_run,
                intermediate_segment_visual_format_config=self._opt("intermediate_segment_visual_format"),
                save_final_json_for_this_job_config=self._opt("save_final_output_json"),
                latent_checkpoint_config=latent_ckpt_enabled,
                latent_checkpoint_resume_config=latent_ckpt_resume,
                stabilize_depth_config=self._opt("stabilize_depth"),
                stabilize_ref_index_config=self._opt("stabilize_ref_index"),
                stabilize_smooth_window_config=self._opt("stabilize_smooth_window", 0),
                use_percentile_norm_config=self._opt("full_percentile_norm"),
                norm_low_percentile_config=self._opt("norm_low_percentile", 0.0),
                norm_high_percentile_config=self._opt("norm_high_percentile", 100.0),
                norm_auto_top_config=self._opt("norm_auto_top", False),
                output_crf_config=self._opt("output_crf"),
                output_preset_config=self._opt("output_preset"),
                output_format_config=self._opt("output_format"),
            )
            if not returned_job_specific_metadata:
                returned_job_specific_metadata = {"status": "failure_no_metadata_from_run"}
                _logger.warning(f"Warning: No job-specific metadata returned from run for {original_basename}.")
            if saved_data_filepath and returned_job_specific_metadata.get("status") == "success":
                job_successful = True
            else:
                prefix = (f" (segment {job_info.get('segment_id', -1) + 1} of {job_info.get('total_segments', 0)})"
                          if is_segment_job else "")
                _logger.info(f"  Job for {original_basename}{prefix} status: {returned_job_specific_metadata.get('status', 'unknown_status')}")
        except Exception as e:
            returned_job_specific_metadata = returned_job_specific_metadata or {}
            if type(e).__name__ == "JobStopped":
                # depthcrafter_logic.JobStopped -- Stop / Quit mid-inference
                # (audit #13): not an error, and nothing was saved for this job.
                returned_job_specific_metadata["status"] = "stopped_by_user"
                _logger.info(f"  Stopped {original_basename} part-way; nothing was saved for this job.")
                return False, returned_job_specific_metadata
            if is_out_of_memory(e):
                self._out_of_vram = True          # the batch's message says what to change
            returned_job_specific_metadata["status"] = "exception_in_gui_process_single_job"
            returned_job_specific_metadata["error_message"] = str(e)
            prefix = (f" (segment {job_info.get('segment_id', -1) + 1} of {job_info.get('total_segments', 0)})"
                      if is_segment_job else "")
            _logger.exception(f"  Exception during job for {original_basename}{prefix}: {e}")
            self.hooks.status(f"Error: {e.__class__.__name__} during {original_basename}")
        return job_successful, returned_job_specific_metadata

    # -- resume / overwrite (v1 _get_segments_to_resume_or_overwrite) ----------

    def _get_segments_to_resume_or_overwrite(self, vid_path, original_basename, segment_subfolder_path,
                                             all_potential_segments_from_define, base_job_info_for_video_ref):
        from depthcrafter.utils import load_json_file

        master_meta_path = os.path.join(segment_subfolder_path, f"{original_basename}_master_meta.json")
        base_job_info_for_video_ref["pre_existing_successful_jobs"] = []

        if os.path.exists(master_meta_path):
            choice = self.hooks.ask(
                "Resume or Overwrite Finalized Segments?",
                f"Master metadata found for '{original_basename}'. This video was previously processed/finalized.\n"
                f"Path: {master_meta_path}\n\n"
                f"Yes: re-process only FAILED segments and update the master metadata. "
                f"Existing successful segments are preserved in the new master metadata.\n"
                f"No: delete ALL existing segments and master metadata and start fresh.\n"
                f"Cancel: skip this video entirely.",
            )
            if choice is True:
                _logger.info(f"Attempting to re-process failed segments for {original_basename} based on existing master metadata.")
                master_data = load_json_file(master_meta_path)
                if not master_data or "jobs_info" not in master_data:
                    # bug sweep 2026-10-09 #9: this fell through to the delete below, so a
                    # "Yes" cost every segment. A master write cut short keeps the
                    # segments' own JSONs, so resume from those; ask before deleting.
                    _logger.warning(f"Could not read the master metadata for {original_basename}; "
                                    f"resuming from the segment files instead.")
                    self._set_aside(master_meta_path)
                    to_run, status, n_done = self._resume_from_segment_files(
                        original_basename, segment_subfolder_path, all_potential_segments_from_define,
                        base_job_info_for_video_ref)
                    if n_done:
                        return to_run, status
                    if self.hooks.ask(
                            "Delete the Segments?",
                            f"The master metadata for '{original_basename}' can't be read, and no segment "
                            f"has a record of finishing, so none can be reused.\nPath: {segment_subfolder_path}\n\n"
                            f"Yes: delete the segments and start fresh.\n"
                            f"No: skip this video and keep its files.",
                            allow_cancel=False) is not True:
                        _logger.info(f"Skipping {original_basename}: its segments are kept.")
                        return [], "skipped_finalized"
                    choice = False
                else:
                    failed_segment_jobs_to_run = []
                    successful_jobs_from_old_master = []
                    potential = {seg["segment_id"]: seg for seg in all_potential_segments_from_define}
                    for job_in_meta in master_data.get("jobs_info", []):
                        seg_id = job_in_meta.get("segment_id")
                        if job_in_meta.get("status") == "success":
                            successful_jobs_from_old_master.append(job_in_meta)
                        elif seg_id is not None and seg_id in potential:
                            failed_segment_jobs_to_run.append(potential[seg_id])
                            _logger.debug(f"  Queueing segment ID {seg_id} (status: {job_in_meta.get('status', 'unknown')}) for {original_basename} for re-processing.")
                        else:
                            _logger.warning(f"  Warning: Segment (ID: {seg_id}, Status: {job_in_meta.get('status')}) from master_meta for {original_basename} not re-queueable. It will be ignored.")
                    if not failed_segment_jobs_to_run:
                        _logger.info(f"No re-processable failed segments found in master_meta for {original_basename}. All existing successful segments will be preserved if merging.")
                        base_job_info_for_video_ref["pre_existing_successful_jobs"] = successful_jobs_from_old_master
                        return [], "skipped_no_failed_segments_in_master_for_reprocessing"
                    try:
                        backup = master_meta_path + f".backup_{time.strftime('%Y%m%d%H%M%S')}"
                        shutil.move(master_meta_path, backup)
                        _logger.debug(f"Backed up existing file {os.path.basename(master_meta_path)} to: {os.path.basename(backup)}")
                    except Exception as e:
                        _logger.warning(f"  Warning: Could not back up existing master metadata: {e}. It might be overwritten.")
                    base_job_info_for_video_ref["pre_existing_successful_jobs"] = successful_jobs_from_old_master
                    return failed_segment_jobs_to_run, "reprocessing_failed_from_master"
            if choice is False:
                _logger.info(f"User chose/defaulted to delete existing segment folder and start fresh for {original_basename}: {segment_subfolder_path}")
                self._rmtree(segment_subfolder_path)
                return all_potential_segments_from_define, "overwriting_finalized"
            _logger.info(f"Skipping {original_basename} (user chose to cancel on finalized segments).")
            return [], "skipped_finalized"

        if os.path.exists(segment_subfolder_path):
            choice_incomplete = self.hooks.ask(
                "Resume Incomplete Segments?",
                f"Incomplete segment data found for '{original_basename}' (no master metadata file).\n"
                f"Path: {segment_subfolder_path}\n\n"
                f"Yes: resume by processing only missing or failed segments. "
                f"Existing successful segments are preserved.\n"
                f"No: delete the existing incomplete segments and start fresh.\n"
                f"Cancel: skip this video entirely.",
            )
            if choice_incomplete is True:
                _logger.debug(f"Attempting to resume incomplete segments for {original_basename}.")
                return self._resume_from_segment_files(original_basename, segment_subfolder_path,
                                                       all_potential_segments_from_define,
                                                       base_job_info_for_video_ref)[:2]
            if choice_incomplete is False:
                _logger.info(f"User chose to delete existing incomplete segment folder and start fresh for {original_basename}: {segment_subfolder_path}")
                self._rmtree(segment_subfolder_path)
                return all_potential_segments_from_define, "overwriting_incomplete"
            _logger.info(f"Skipping {original_basename} (user chose to cancel on incomplete segments).")
            return [], "skipped_incomplete"

        return all_potential_segments_from_define, "fresh_processing"

    def _resume_from_segment_files(self, original_basename, segment_subfolder_path,
                                   all_potential_segments_from_define, base_job_info_for_video_ref):
        """Resume from the segments' own files: (segments to run, status, how
        many were already done). A segment counts as done when its NPZ and a
        JSON saying "success" are both there."""
        from depthcrafter.utils import get_segment_npz_output_filename, get_sidecar_json_filename, load_json_file

        segments_to_run = []
        num_already_complete = 0
        completed_meta = []
        for seg_job in all_potential_segments_from_define:
            seg_id, total_segs = seg_job["segment_id"], seg_job["total_segments"]
            npz_name = get_segment_npz_output_filename(original_basename, seg_id, total_segs)
            npz_path = os.path.join(segment_subfolder_path, npz_name)
            json_path = os.path.join(segment_subfolder_path, get_sidecar_json_filename(npz_name))
            done = False
            if os.path.exists(npz_path) and os.path.exists(json_path):
                segment_meta = load_json_file(json_path)
                if segment_meta and segment_meta.get("status") == "success":
                    done = True
                    num_already_complete += 1
                    completed_meta.append(segment_meta)
                else:
                    status_msg = segment_meta.get("status", "unknown") if segment_meta else "JSON missing/corrupt"
                    _logger.info(f"  Segment {seg_id + 1}/{total_segs} for {original_basename} found but not successful (status: {status_msg}). Will re-process.")
            else:
                _logger.debug(f"  Segment {seg_id + 1}/{total_segs} for {original_basename} (NPZ: {npz_name}) not found or JSON missing. Will process.")
            if not done:
                segments_to_run.append(seg_job)
        if num_already_complete > 0:
            _logger.info(f"Found {num_already_complete} successfully completed segments for {original_basename} that will be skipped during processing.")
        base_job_info_for_video_ref["pre_existing_successful_jobs"] = completed_meta
        if not segments_to_run and num_already_complete == len(all_potential_segments_from_define):
            _logger.warning(f"  All segments for {original_basename} appear complete from individual files, but master_meta was missing. Consider re-merging. Skipping processing.")
            return [], "skipped_all_segments_found_complete_no_master", num_already_complete
        if not segments_to_run:
            _logger.warning(f"  No segments to run for {original_basename}, but not all were found complete. Total defined: {len(all_potential_segments_from_define)}, Found complete: {num_already_complete}")
            return [], "skipped_no_segments_to_run_incomplete", num_already_complete
        return segments_to_run, "resuming_incomplete", num_already_complete

    @staticmethod
    def _set_aside(path: str) -> None:
        """Rename an unreadable file out of the way, keeping it."""
        try:
            shutil.move(path, path + f".damaged_{time.strftime('%Y%m%d%H%M%S')}")
        except OSError as e:
            _logger.warning(f"  Could not set aside {os.path.basename(path)}: {e}")

    @staticmethod
    def _rmtree(path: str) -> None:
        try:
            if os.path.exists(path):
                shutil.rmtree(path)
            _logger.debug(f"  Successfully deleted: {path}")
        except Exception as e:
            _logger.error(f"  Error deleting {path}: {e}. Processing may fail or overwrite.")

    # -- metadata (v1 _initialize_master_metadata_entry) ----------------------

    def _initialize_master_metadata_entry(self, original_basename, job_info_for_original_details,
                                          total_expected_jobs_for_this_video):
        o = self.opts
        entry = {
            "original_video_basename": original_basename,
            "original_video_details": {
                "raw_frame_count": job_info_for_original_details.get("original_video_raw_frame_count", 0),
                "original_fps": job_info_for_original_details.get("original_video_fps", 30.0),
            },
            "global_processing_settings": {
                "guidance_scale": o["guidance_scale"],
                "inference_steps": o["inference_steps"],
                "target_height_setting": o["target_height"],
                "target_width_setting": o["target_width"],
                "seed_setting": o["seed"],
                "target_fps_setting": o["target_fps"],
                "process_max_frames_setting": o["process_length"],
                "gui_window_size_setting": o["window_size"],
                "gui_overlap_setting": o["overlap"],
                "processed_as_segments": o["process_as_segments_var"],
            },
            "jobs_info": [],
            "overall_status": "pending",
            "total_expected_jobs": total_expected_jobs_for_this_video,
            "completed_successful_jobs": 0,
            "completed_failed_jobs": 0,
        }
        if o["process_as_segments_var"]:
            gps = entry["global_processing_settings"]
            gps["segment_definition_output_window_frames"] = job_info_for_original_details.get(
                "gui_desired_output_window_frames", o["window_size"])
            gps["segment_definition_output_overlap_frames"] = job_info_for_original_details.get(
                "gui_desired_output_overlap_frames", o["overlap"])
        return entry

    # -- finalisation (v1 _finalize_video_processing and helpers) -------------

    def _finalize_video_processing(self, current_video_path, original_basename, master_meta) -> bool:
        """True when the source's depth map is complete: every job succeeded
        and, for segments, the merge did too."""
        from depthcrafter.utils import format_duration, get_segment_output_folder_name

        if master_meta["completed_failed_jobs"] == 0:
            master_meta["overall_status"] = "all_success"
        elif master_meta["completed_successful_jobs"] > 0:
            master_meta["overall_status"] = "partial_success"
        else:
            master_meta["overall_status"] = "all_failed"
        _logger.debug(f"Finished processing for {original_basename}. Overall Status: {master_meta['overall_status']}.")

        # Whole-source throughput: summed job durations, not wall clock.
        try:
            total_frames, total_seconds, counted = 0, 0.0, 0
            for job_meta in master_meta.get("jobs_info") or []:
                if not isinstance(job_meta, dict) or job_meta.get("status") != "success":
                    continue
                frames = job_meta.get("frames_in_output_video") or 0
                seconds = job_meta.get("internal_processing_duration_seconds") or 0.0
                if frames > 0 and seconds > 0:
                    total_frames += int(frames)
                    total_seconds += float(seconds)
                    counted += 1
            if counted and total_seconds > 0:
                job_word = "job" if counted == 1 else "jobs"
                _logger.info(f"[Source complete] {original_basename} - {total_frames} frames across "
                             f"{counted} {job_word} in {format_duration(total_seconds)} "
                             f"| {total_frames / total_seconds:.2f} fps overall")
        except Exception as e_tp:
            _logger.debug(f"Could not compute source throughput summary for {original_basename}: {e_tp}")

        main_output_dir = self.output_dir
        was_segments = master_meta["global_processing_settings"]["processed_as_segments"]
        segment_subfolder_path = (os.path.join(main_output_dir, get_segment_output_folder_name(original_basename))
                                  if was_segments else None)
        master_meta_filepath = None
        merge_success, final_merged_path = False, "N/A (Merge not applicable or failed)"
        finalized = False
        try:
            master_meta_filepath, meta_saved = self._save_master_metadata_and_cleanup_segment_json(
                master_meta, original_basename, main_output_dir, was_segments, segment_subfolder_path)
            # Only a complete set is merged (audit #23): with a failed segment the
            # merge stitched across the hole into <clip>_depth.mp4 -- shorter
            # than the clip, under the final name. Resume fills it, then merges.
            if was_segments and meta_saved and master_meta["overall_status"] == "all_success":
                try:
                    merge_success, final_merged_path = self._handle_segment_merging(
                        master_meta_filepath, original_basename, main_output_dir)
                except Exception as e_merge:
                    _logger.error(f"Error during segment merging for {original_basename}: {e_merge}", exc_info=True)
                    self.hooks.status(f"Merge Failed: {e_merge.__class__.__name__}")
                    merge_success, final_merged_path = False, f"N/A (Merge failed due to {e_merge.__class__.__name__})"
            elif was_segments:
                _logger.info(f"Not merging {original_basename}: not every segment succeeded "
                             f"(status: {master_meta['overall_status']}). Its segments stay in "
                             f"{segment_subfolder_path or 'N/A'} -- Resume redoes the missing ones, then merges.")

            if self._opt("save_final_output_json"):
                self._save_final_output_sidecar_json(original_basename, final_merged_path, master_meta_filepath,
                                                     master_meta, was_segments, merge_success)
            # The segments are the only copy of the GPU work until a merge has
            # succeeded: a failed merge (MemoryError on a long clip, disk full,
            # an encoder failure) or a failed segment must leave them for
            # Re-Merge / Resume, never delete them (audit 2026-09-28, blocker:
            # with Keep NPZ off they were rmtree'd whatever the merge did).
            if was_segments and segment_subfolder_path:
                if merge_success and master_meta["overall_status"] == "all_success":
                    self._cleanup_segment_folder(segment_subfolder_path, original_basename, master_meta)
                else:
                    _logger.warning(f"Keeping the segments of {original_basename} in {segment_subfolder_path}: "
                                    f"{'the merge failed' if master_meta['overall_status'] == 'all_success' else 'not every segment succeeded'}"
                                    f" -- Re-Merge or Resume can use them.")
            finalized = True
        except Exception as e:
            _logger.exception(f"Error during finalization for {original_basename}: {e}")
            self.hooks.status(f"Finalization Error: {e.__class__.__name__} for {original_basename}")

        final_status = master_meta.get("overall_status", "all_failed")
        if self.effective_move_original_on_completion:
            target = ""
            if final_status == "all_success":
                target = "finished"
            elif final_status in ("partial_success", "all_failed"):
                target = "failed"
            else:
                _logger.warning(f"Move Original: Could not determine 'finished' or 'failed' status for '{original_basename}' (status: '{final_status}'). Original file will not be moved.")
            if target:
                self._move_original_source(current_video_path, original_basename, target)
        else:
            _logger.info(f"Skipped moving original source '{original_basename}' (single file/sequence mode).")
        return (finalized and final_status == "all_success"
                and (merge_success or not was_segments))

    def _save_master_metadata_and_cleanup_segment_json(self, master_meta_to_save, original_basename,
                                                       main_output_dir, was_segments, segment_subfolder_path):
        from depthcrafter.utils import get_segment_output_folder_name, get_sidecar_json_filename, save_json_file

        meta_saved = False
        if was_segments:
            if not segment_subfolder_path:
                segment_subfolder_path = os.path.join(main_output_dir, get_segment_output_folder_name(original_basename))
            os.makedirs(segment_subfolder_path, exist_ok=True)
            master_meta_filepath = os.path.join(segment_subfolder_path, f"{original_basename}_master_meta.json")
            if save_json_file(master_meta_to_save, master_meta_filepath):
                _logger.debug(f"Saved master metadata for {original_basename} to {master_meta_filepath}")
                meta_saved = True
            if meta_saved:
                _logger.debug(f"  Attempting to delete individual segment JSONs for {original_basename} (master created).")
                deleted = 0
                for job_data in master_meta_to_save.get("jobs_info", []):
                    npz_file = job_data.get("output_segment_filename")
                    if npz_file:
                        json_to_del = os.path.join(segment_subfolder_path, get_sidecar_json_filename(npz_file))
                        if os.path.exists(json_to_del):
                            try:
                                os.remove(json_to_del)
                                deleted += 1
                            except Exception as e:
                                _logger.error(f"ERROR deleting individual segment JSON {json_to_del}: {e}")
                _logger.debug(f"    Deleted {deleted} individual segment JSONs.")
            else:
                _logger.warning(f"  Skipping deletion of individual segment JSONs for {original_basename} (master_meta.json not saved).")
        else:
            master_meta_filepath = os.path.join(main_output_dir, f"{original_basename}_master_meta.json")
            _logger.debug(f"Skipping save of '{os.path.basename(master_meta_filepath)}' by _save_master_metadata for full video mode for {original_basename}.")
        return master_meta_filepath, meta_saved

    def merge_kwargs(self, master_meta_path: str, output_path: str, base_name: str) -> Dict[str, Any]:
        """merge_depth_segments arguments from the snapshot (v1 built these in
        two places, _handle_segment_merging and re_merge_from_gui)."""
        o = self.opts
        return {
            "master_meta_path": master_meta_path,
            "output_path_arg": output_path,
            "do_dithering": o["merge_dither_var"],
            "dither_strength_factor": o["merge_dither_strength_var"],
            "apply_gamma_correction": o["merge_gamma_correct_var"],
            "gamma_value": o["merge_gamma_value_var"],
            "use_percentile_norm": o["merge_percentile_norm_var"],
            "norm_low_percentile": o["merge_norm_low_perc_var"],
            "norm_high_percentile": o["merge_norm_high_perc_var"],
            "output_format": o["merge_output_format_var"],
            "merge_alignment_method": "linear_blend" if o["merge_alignment_method_var"] == "Linear Blend" else "shift_scale",
            "output_filename_override_base": base_name,
            "enable_dual_output_robust_norm": o["enable_dual_output_robust_norm"],
            "robust_norm_low_percentile": o["robust_norm_low_percentile"],
            "robust_norm_high_percentile": o["robust_norm_high_percentile"],
            "robust_norm_output_min": o["robust_norm_output_min"],
            "robust_norm_output_max": o["robust_norm_output_max"],
            "robust_output_suffix": o["robust_output_suffix"],
            "is_depth_far_black": o["is_depth_far_black"],
            # the Output CRF / Preset, as a clip done whole gets them (audit, minor)
            "output_crf": o.get("output_crf"),
            "output_preset": o.get("output_preset"),
        }

    def _handle_segment_merging(self, master_meta_filepath, original_basename, main_output_dir) -> Tuple[bool, str]:
        merge_depth_segments = _merge_module()
        if merge_depth_segments is None:
            _logger.warning(f"Segment merging for {original_basename} skipped: merge_depth_segments module not available.")
            return False, "N/A (Merge module not available - module missing)"
        merged_base_name = f"{original_basename}{self.opts['merge_output_suffix_var']}"
        try:
            primary_output_path = merge_depth_segments.merge_depth_segments(
                **self.merge_kwargs(master_meta_filepath, main_output_dir, merged_base_name))
            if primary_output_path is None:
                _logger.error(f"merge_depth_segments returned None for {original_basename}. Merge considered failed.")
                return False, "N/A (Merge module returned no path)"
            _logger.debug(f"Primary merge for {original_basename} successful. Output: {primary_output_path}")
            return True, primary_output_path
        except Exception as e:
            _logger.exception(f"Exception during merge_depth_segments call for {original_basename}: {e}")
            self.hooks.status(f"Merge Error: {e.__class__.__name__} for {original_basename}")
            return False, f"N/A (Merge failed due to {e.__class__.__name__})"

    def _save_final_output_sidecar_json(self, original_basename, final_merged_path, master_meta_filepath,
                                        master_meta, was_segments, merge_successful):
        from depthcrafter.utils import get_sidecar_json_filename, save_json_file

        o = self.opts
        json_path, json_content = None, {}
        if was_segments:
            if merge_successful and final_merged_path and not final_merged_path.startswith("N/A"):
                fmt = o["merge_output_format_var"]
                json_content = {
                    "source_video_basename": original_basename,
                    "processing_mode": "segmented_then_merged",
                    "final_output_path": os.path.abspath(final_merged_path),
                    "final_output_format_selected": fmt,
                    "master_metadata_path_source": os.path.abspath(master_meta_filepath) if master_meta_filepath else None,
                    "global_processing_settings_summary": master_meta.get("global_processing_settings"),
                    "merge_settings_summary": {
                        "output_format_selected": fmt,
                        "output_suffix": o["merge_output_suffix_var"],
                        "alignment_method": o["merge_alignment_method_var"],
                        "dithering": o["merge_dither_var"],
                        "dither_strength": o["merge_dither_strength_var"],
                        "gamma_correction": o["merge_gamma_correct_var"],
                        "gamma_value_if_applied": o["merge_gamma_value_var"] if o["merge_gamma_correct_var"] else 1.0,
                        "percentile_norm": o["merge_percentile_norm_var"],
                        "norm_low_perc": o["merge_norm_low_perc_var"],
                        "norm_high_perc": o["merge_norm_high_perc_var"],
                    },
                    "generation_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                json_path = self._sidecar_path_for(final_merged_path, original_basename, "merged", get_sidecar_json_filename)
            else:
                _logger.info(f"  Skipping final JSON for merged {original_basename} (merge not successful/path invalid).")
        else:
            if master_meta and master_meta.get("jobs_info"):
                job_info = master_meta["jobs_info"][0]
                rel = job_info.get("output_video_filename")
                if job_info.get("status") != "success":
                    # a failed save left no new map (or an older one): no sidecar
                    # pointing at it (bug sweep 2026-10-09)
                    _logger.info(f"  Skipping final JSON for full video {original_basename} "
                                 f"(job status: {job_info.get('status')}).")
                elif rel:
                    out_path = os.path.join(self.output_dir, rel)
                    json_content = {
                        "source_video_basename": original_basename,
                        "processing_mode": "full_video",
                        "final_output_path": os.path.abspath(out_path),
                        "final_output_format": os.path.splitext(rel)[1].lstrip("."),
                        "global_processing_settings": master_meta.get("global_processing_settings"),
                        "job_specific_details": job_info,
                        "generation_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    json_path = self._sidecar_path_for(out_path, original_basename, "full video", get_sidecar_json_filename)
                else:
                    _logger.warning(f"  Skipping final JSON for full video {original_basename} (output path/format missing from job_info).")
            else:
                _logger.warning(f"  Skipping final JSON for full video {original_basename} (master_meta or job_info missing).")

        if json_path and json_content:
            _logger.debug(f"    Attempting to save final output JSON to: {json_path}")
            if save_json_file(json_content, json_path):
                _logger.debug(f"  Successfully saved sidecar JSON for final output: {json_path}")
        elif o["save_final_output_json_var"]:
            mode = "merged" if was_segments else "full video"
            _logger.warning(f"  Final output JSON for {mode} '{original_basename}' not created (conditions not met, or save failed).")

    @staticmethod
    def _sidecar_path_for(path, original_basename, what, get_sidecar_json_filename):
        if os.path.isdir(path):
            return os.path.join(os.path.dirname(path.rstrip(os.sep)), f"{os.path.basename(path.rstrip(os.sep))}.json")
        if os.path.isfile(path):
            return get_sidecar_json_filename(path)
        _logger.warning(f"    Cannot determine final JSON path for {what} {original_basename} (output path: {path}).")
        return None

    def _cleanup_segment_folder(self, segment_subfolder_path, original_basename, master_meta):
        del_folder = False
        if not self._opt("keep_intermediate_npz"):
            _logger.debug(f"Deleting intermediate segment subfolder for {original_basename} (Keep NPZ unchecked)...")
            del_folder = True
        else:
            min_frames = self._opt("min_frames_to_keep_npz", 0)
            if min_frames > 0:
                orig_frames = master_meta.get("original_video_details", {}).get("raw_frame_count", 0)
                if orig_frames < min_frames:
                    _logger.info(f"  Video frames ({orig_frames}) < threshold ({min_frames}). Deleting segment folder for {original_basename} despite 'Keep NPZ'.")
                    del_folder = True
                else:
                    _logger.info(f"  Video frames ({orig_frames}) >= threshold ({min_frames}). Segment folder for {original_basename} will be kept.")
            else:
                _logger.debug(f"Keeping intermediate NPZ files for {original_basename} (Keep NPZ checked, no positive frame threshold).")
        if del_folder:
            if os.path.exists(segment_subfolder_path):
                try:
                    shutil.rmtree(segment_subfolder_path)
                    _logger.debug(f"Successfully deleted segment subfolder for {original_basename}.")
                except Exception as e:
                    _logger.error(f"  Error deleting segment subfolder {segment_subfolder_path}: {e}")
            else:
                _logger.warning(f"  Segment subfolder not found for deletion: {segment_subfolder_path}")
        else:
            _logger.debug(f"Keeping intermediate NPZ files and _master_meta.json in {segment_subfolder_path}")

    def _move_original_source(self, current_video_path, original_basename, target_subfolder):
        """Move a processed source into <input>/finished or <input>/failed (v1)."""
        _logger.debug(f"Moving original source '{original_basename}' to '{target_subfolder}' folder.")
        try:
            input_field = self.opts["input_dir_or_file_var"]
            if os.path.isdir(input_field):
                root = input_field
            elif os.path.isfile(input_field):
                root = os.path.dirname(input_field)
            else:
                _logger.warning(f"Move Original: The GUI input path '{input_field}' is invalid for determining the target folder root. Using dirname of processed item as fallback.")
                root = os.path.dirname(current_video_path)
                if not os.path.isdir(root):
                    _logger.error(f"ERROR moving original '{original_basename}': Cannot determine valid root for target folder.")
                    return
            destination_dir = os.path.join(root, target_subfolder)
            os.makedirs(destination_dir, exist_ok=True)
            dest_filename = os.path.basename(current_video_path)
            dest_path = os.path.join(destination_dir, dest_filename)
            if os.path.exists(current_video_path):
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(dest_filename)
                    new_dest_name = f"{base}{time.strftime('_%Y%m%d%H%M%S')}{ext}"
                    dest_path = os.path.join(destination_dir, new_dest_name)
                    _logger.info(f"Move Original: Destination already exists. Renaming '{dest_filename}' to '{new_dest_name}'.")
                shutil.move(current_video_path, dest_path)
                _logger.debug(f"Successfully moved original source '{dest_filename}' to '{target_subfolder}' folder.")
            else:
                _logger.warning(f"Move Original: Source path to move does not exist: {current_video_path}")
        except Exception as e:
            _logger.exception(f"ERROR moving original '{original_basename}': {e}")

    # -- Current Clip Info (v1 _update_gui_info_on_job_start / _finish) --------

    def _update_gui_info_on_job_start(self, job_info, original_basename, log_msg_prefix):
        o = self.opts
        th, tw = o["target_height"], o["target_width"]
        is_segment_job = job_info.get("is_segment", False)
        res = "N/A"
        if th > 0 and tw > 0:
            res = f"{tw}x{th}"
        else:
            oh, ow = job_info.get("original_height", "N/A"), job_info.get("original_width", "N/A")
            if oh != "N/A" and ow != "N/A":
                res = f"{ow}x{oh} (Original/Fallback)"
        total_frames = job_info.get("original_video_raw_frame_count", "N/A")
        frames = "N/A"
        if is_segment_job:
            raw = job_info.get("num_frames_to_load_raw", "N/A")
            if raw != "N/A":
                frames = f"{raw}"
            if total_frames != "N/A" and str(raw) != str(total_frames):
                frames += f" of {total_frames}"
        else:
            frames = f"{o['process_length']}" if o["process_length"] != -1 else f"{total_frames}"
        self._shown_resolution, self._shown_frames = res, frames
        # Current Clip Info: just the clip's name (user, 2026-10-04: "(Full video (1/1))"
        # wasted the room); the status line says which segment
        self.hooks.clip(original_basename, res, frames)
        self._shown_filename = original_basename

    def _update_gui_info_on_job_finish(self, job_info, meta):
        ph, pw = meta.get("processed_height", "N/A"), meta.get("processed_width", "N/A")
        if ph != "N/A" and pw != "N/A":
            res = f"{pw}x{ph}"
        else:
            res = self._shown_resolution + " (Failed to confirm)"
        processed = meta.get("frames_in_output_video", "N/A")
        total_frames = job_info.get("original_video_raw_frame_count", "N/A")
        if processed != "N/A":
            frames = f"{processed}"
            if total_frames != "N/A" and str(processed) != str(total_frames):
                frames += f" of {total_frames} total" if job_info.get("is_segment", False) else f" (of {total_frames} total)"
        else:
            frames = self._shown_frames + " (Failed to confirm)"
        self._shown_resolution, self._shown_frames = res, frames
        self.hooks.clip(getattr(self, "_shown_filename", "N/A"), res, frames)

    # -- Re-Merge (v1 _execute_re_merge) --------------------------------------

    def run_remerge(self, master_meta_path: str, output_path: str) -> Optional[str]:
        from depthcrafter.utils import format_duration

        self.stop_event.clear()
        self.hooks.progress(0, 1)
        start_time = time.perf_counter()
        primary_output_path = None
        base = os.path.splitext(os.path.basename(master_meta_path))[0].replace("_master_meta", "")
        base_name = f"{base}{self.opts['merge_output_suffix_var']}"
        try:
            module = _merge_module()
            if module is not None:
                primary_output_path = module.merge_depth_segments(**self.merge_kwargs(master_meta_path, output_path, base_name))
                if primary_output_path:
                    _logger.info(f"Re-Merge completed. Primary output saved to: {primary_output_path}")
                else:
                    _logger.warning("Re-Merge completed, but no primary output path was returned.")
            else:
                _logger.warning("Segment merging for N/A for re-merge action skipped: merge_depth_segments module not available.")
        except Exception as e:
            _logger.exception(f"ERROR during re-merge execution: {e}")
            self.hooks.status(f"Re-Merge Error: {e.__class__.__name__}")
            primary_output_path = None
        finally:
            _logger.info(f"--- Re-Merge for: {os.path.basename(master_meta_path)} finished in {format_duration(time.perf_counter() - start_time)}. ---")
            if primary_output_path:
                self.hooks.status(f"Re-Merge Finished. Output: {os.path.basename(primary_output_path)}")
            else:
                self.hooks.status("Re-Merge Finished (No primary output).")
            self.hooks.progress(1, 1)
        return primary_output_path

    # -- Generate Seg Visuals (v1 _execute_generate_segment_visuals) ----------

    def run_generate_visuals(self, master_path: str, vis_fmt: str) -> int:
        import numpy as np
        from depthcrafter import utils as u
        from .settings import openexr_available

        self.stop_event.clear()
        self.hooks.progress(0, 1)
        start_time = time.perf_counter()
        meta_data = u.load_json_file(master_path)
        if not meta_data:
            return 0
        jobs = [j for j in meta_data.get("jobs_info", [])
                if j.get("status") == "success" and j.get("output_segment_filename")]
        if not jobs:
            _logger.warning(f"No successful segments with output filenames found in {os.path.basename(master_path)} for visual generation.")
            return 0
        self.hooks.progress(0, len(jobs))
        seg_folder_path = os.path.dirname(master_path)
        updated_visual_paths = {}
        exr_ok = openexr_available()

        for i, job_meta in enumerate(jobs):
            if self.stop_event.is_set():
                _logger.info("Segment visual generation cancelled during processing.")
                break
            seg_id, npz_name = job_meta.get("segment_id"), job_meta.get("output_segment_filename")
            npz_path = os.path.join(seg_folder_path, npz_name)
            _logger.debug(f"  Visual Gen - Processing segment {seg_id + 1 if seg_id is not None else '?'}/{len(jobs)}: {npz_name} for {vis_fmt}")
            if not os.path.exists(npz_path):
                _logger.error(f"File not found: {npz_path}")
                continue
            try:
                with np.load(npz_path) as data:
                    if "frames" not in data.files:
                        _logger.error(f"Key 'frames' not found in NPZ: {npz_name}")
                        continue
                    raw_frames = data["frames"]
                if raw_frames.size == 0:
                    _logger.warning(f"    Visual Gen - WARNING: Segment {npz_name} is empty. Skipping.")
                    continue
                norm_frames = ((raw_frames - raw_frames.min()) / (raw_frames.max() - raw_frames.min())
                               if raw_frames.max() != raw_frames.min() else np.zeros_like(raw_frames))
                norm_frames = np.clip(norm_frames, 0, 1)
                base_name_no_ext = os.path.splitext(npz_name)[0]
                save_path, save_err = None, None
                fps = float(job_meta.get("processed_at_fps", meta_data.get("original_video_details", {}).get("original_fps", 30.0)))
                if fps <= 0:
                    fps = 30.0
                if vis_fmt in ("mp4", "main10_mp4"):
                    save_path, save_err = u.save_depth_visual_as_mp4_util(
                        norm_frames, os.path.join(seg_folder_path, f"{base_name_no_ext}_visual.mp4"), fps, output_format=vis_fmt)
                elif vis_fmt == "png_sequence":
                    save_path, save_err = u.save_depth_visual_as_png_sequence_util(norm_frames, seg_folder_path, base_name_no_ext)
                elif vis_fmt == "exr_sequence":
                    if exr_ok:
                        save_path, save_err = u.save_depth_visual_as_exr_sequence_util(norm_frames, seg_folder_path, base_name_no_ext)
                    else:
                        save_err = "OpenEXR module not available in GUI environment."
                elif vis_fmt == "exr":
                    if exr_ok:
                        first_frame = norm_frames[0] if len(norm_frames) > 0 else None
                        if first_frame is None:
                            save_err = "No frame data for single EXR."
                        else:
                            save_path, save_err = u.save_depth_visual_as_single_exr_util(first_frame, seg_folder_path, base_name_no_ext)
                    else:
                        save_err = "OpenEXR module not available in GUI environment."
                if save_path:
                    _logger.debug(f"    Visual Gen - Successfully saved visual: {save_path}")
                    if seg_id is not None:
                        updated_visual_paths[seg_id] = {"path": os.path.abspath(save_path), "format": vis_fmt}
                if save_err:
                    _logger.error(f"    Visual Gen - ERROR saving visual for {npz_name}: {save_err}, format requested: {vis_fmt}")
            except Exception as e:
                _logger.exception(f"    Visual Gen - ERROR processing segment {npz_name}: {e}")
            self.hooks.progress(i + 1, len(jobs))

        if updated_visual_paths:
            _logger.info("Visual Gen - Updating master metadata with new visual paths...")
            meta_update = u.load_json_file(master_path)
            if meta_update:
                updated = 0
                for job_entry in meta_update.get("jobs_info", []):
                    s_id = job_entry.get("segment_id")
                    if s_id in updated_visual_paths:
                        job_entry["intermediate_visual_path"] = updated_visual_paths[s_id]["path"]
                        job_entry["intermediate_visual_format_saved"] = updated_visual_paths[s_id]["format"]
                        updated += 1
                if updated > 0:
                    if u.save_json_file(meta_update, master_path, indent=4):
                        _logger.info(f"Visual Gen - Master metadata updated for {updated} segments.")
                else:
                    _logger.info("Visual Gen - No segments in master metadata needed visual path updates.")

        _logger.info(f"--- Segment Visual Generation for: {os.path.basename(master_path)} (Format: {vis_fmt}) finished in {u.format_duration(time.perf_counter() - start_time)}. ---")
        self.hooks.progress(len(jobs), len(jobs))
        self.hooks.status(f"Generated {len(updated_visual_paths)} segment visual(s).")
        return len(updated_visual_paths)


# ---------------------------------------------------------------------------
# File-menu operation (v1 _restore_input_files); no model, runs on the UI side
# ---------------------------------------------------------------------------


def restore_input_files(source_input_path: str, folder_type: str) -> Tuple[int, int, str]:
    """Move files from <input>/<finished|failed> back into <input>.

    Returns (restored, errors, message). Raises InputError where v1 showed an
    error box.
    """
    display = folder_type.capitalize()
    if not os.path.isdir(source_input_path):
        _logger.warning(f"Restore {display} operation skipped: Input Folder/File is not a directory: {source_input_path}")
        raise InputError(f"Restore '{display}' only applies when Input Folder/File is set to a directory (batch mode).")
    restored = errors = 0
    src_folder = os.path.join(source_input_path, folder_type)
    if os.path.isdir(src_folder):
        _logger.info(f"==> Restoring input files from: {src_folder}")
        for filename in os.listdir(src_folder):
            src_path = os.path.join(src_folder, filename)
            dest_path = os.path.join(source_input_path, filename)
            if not os.path.isfile(src_path):
                continue
            try:
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(filename)
                    new_name = f"{base}_restored_{time.strftime('%Y%m%d%H%M%S')}{ext}"
                    dest_path = os.path.join(source_input_path, new_name)
                    _logger.warning(f"Input file '{filename}' already exists in '{source_input_path}'. Restoring as '{new_name}'.")
                shutil.move(src_path, dest_path)
                restored += 1
                _logger.debug(f"Moved input file '{filename}' to '{source_input_path}'")
            except Exception as e:
                errors += 1
                _logger.error(f"Error moving input file '{filename}' from '{src_folder}': {e}", exc_info=True)
        try:
            if not os.listdir(src_folder):
                os.rmdir(src_folder)
                _logger.info(f"Removed empty folder: {src_folder}")
        except OSError as e:
            _logger.warning(f"Could not remove empty folder '{src_folder}': {e}")
    else:
        _logger.info(f"==> Input '{display}' folder not found: {src_folder}")
    if restored or errors:
        msg = f"Restored {restored} file(s) from '{display}'" + (f", {errors} could not be moved." if errors else ".")
    else:
        msg = f"No input files found in the '{display}' folder to restore."
    return restored, errors, msg


def _merge_module():
    try:
        from depthcrafter import merge_depth_segments
        return merge_depth_segments
    except ImportError as e:
        _logger.warning(f"Could not import 'merge_depth_segments'. Merging functionality will not be available. Error: {e}")
        return None
