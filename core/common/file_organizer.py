import os
import shutil
import threading
import time
import queue
import logging
from typing import List, Tuple, Optional, Callable


def _looks_identical(path_a: str, path_b: str, probe_bytes: int = 1 << 20) -> bool:
    """Cheap sameness test for two large media files.

    Same size, same modified time, same first megabyte, same last megabyte. Hashing a multi-gigabyte
    render to answer this would cost more than the move it guards, and this is
    enough to tell "the same clip was already archived" from "a different clip
    happens to share a name" -- which is the distinction that matters, because
    the wrong answer used to mean deleting the file that was just produced.
    """
    try:
        st_a, st_b = os.stat(path_a), os.stat(path_b)
        size_a = st_a.st_size
        if size_a != st_b.st_size:
            return False
        # The same file -- moved, or copied with copy2 -- keeps its modified time;
        # a re-render gets a new one. Without this a DNxHR re-render that differs
        # only mid-clip (fixed-size frames, the moov at the end) passed the probe:
        # Resume skipped it and the move deleted it (bug sweep 2026-10-09 A11).
        # 2 s: FAT / exFAT drives keep times to 2 s.
        if abs(st_a.st_mtime - st_b.st_mtime) > 2.0:
            return False
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            if fa.read(probe_bytes) != fb.read(probe_bytes):
                return False
            if size_a > probe_bytes:
                fa.seek(-probe_bytes, os.SEEK_END)
                fb.seek(-probe_bytes, os.SEEK_END)
                if fa.read(probe_bytes) != fb.read(probe_bytes):
                    return False
        return True
    except OSError:
        # Unreadable means "cannot prove they match", which must not license a delete.
        return False


def split_already_finished(
    paths: List[str], finished_dir: str, logger: Optional[logging.Logger] = None
) -> Tuple[List[str], List[str]]:
    """Resume's test: (to process, skipped). A clip is skipped only when
    ``finished_dir`` holds THE SAME FILE under its name (_looks_identical) --
    a namesake with other content is a re-render (a clip re-splatted after
    its old splat was archived: audit 2026-09-28 #8, the user's Chapter 5),
    which the name alone used to skip without a word. Skipped names are logged."""
    remaining, skipped = [], []
    for path in paths:
        archived = os.path.join(finished_dir, os.path.basename(path))
        if os.path.isfile(archived) and _looks_identical(path, archived):
            skipped.append(os.path.basename(path))
        else:
            if os.path.isfile(archived) and logger:
                logger.info(f"Resume: {os.path.basename(path)} differs from the copy in finished/ -- "
                            f"a re-render, so it is processed again.")
            remaining.append(path)
    if skipped and logger:
        shown = ", ".join(skipped[:10]) + (f" (+{len(skipped) - 10} more)" if len(skipped) > 10 else "")
        logger.info(f"Resume: skipped {len(skipped)} clip(s) already in finished/: {shown}")
    return remaining, skipped


def _unique_destination(dest_path: str) -> str:
    """A destination that does not overwrite anything: name (2).ext, (3), ..."""
    stem, ext = os.path.splitext(dest_path)
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{stem} ({int(time.time())}){ext}"


def move_single_file(
    src_path: str, dest_folder: str, logger: Optional[logging.Logger] = None, max_retries: int = 1, retry_delay: float = 0.5
) -> Tuple[bool, Optional[str]]:
    """
    Moves a single file to a 'finished' subfolder within dest_folder.

    Args:
        src_path: Source file path
        dest_folder: Destination folder (finished subfolder will be created inside)
        logger: Optional logger for output
        max_retries: Number of attempts to try before failing
        retry_delay: Delay between attempts

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    if not os.path.exists(src_path):
        return True, None  # File doesn't exist, consider it moved

    for attempt in range(max_retries):
        try:
            finished_dir = os.path.join(dest_folder, "finished")
            os.makedirs(finished_dir, exist_ok=True)

            dest_path = os.path.join(finished_dir, os.path.basename(src_path))

            if os.path.exists(dest_path):
                # A file of this name is already archived. Deleting the source
                # on that basis alone destroyed the newer file and kept the
                # stale one -- and reported success while doing it. Only drop
                # the source when it really is the same file; otherwise archive
                # it beside the old one under a free name.
                if _looks_identical(src_path, dest_path):
                    os.remove(src_path)
                    if logger:
                        logger.info(
                            f"Removed source file (identical copy already in finished): "
                            f"{os.path.basename(src_path)}"
                        )
                else:
                    unique_path = _unique_destination(dest_path)
                    shutil.move(src_path, unique_path)
                    if logger:
                        logger.warning(
                            f"{os.path.basename(src_path)} differs from the copy already in "
                            f"finished; archived it as {os.path.basename(unique_path)} instead "
                            f"of overwriting or deleting either."
                        )
            else:
                shutil.move(src_path, finished_dir)
                if logger:
                    logger.info(f"Moved {os.path.basename(src_path)} to {finished_dir}")

            return True, None
        except PermissionError as e:
            error_msg = f"File in use: {e}"
            if attempt < max_retries - 1:
                if logger:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries}: {error_msg}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            if logger:
                logger.error(f"Failed to move {os.path.basename(src_path)} after {max_retries} attempts: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = str(e)
            if attempt < max_retries - 1:
                if logger:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries}: {error_msg}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            if logger:
                logger.error(f"Failed to move {os.path.basename(src_path)} after {max_retries} attempts: {error_msg}")
            return False, error_msg
    return False, "Unknown error"


def move_files_to_finished(
    files_to_move: List[Tuple[str, str]],
    logger: Optional[logging.Logger] = None,
    wait_before_move: float = 0.0,
    close_handles_callback: Optional[Callable[[], None]] = None,
    max_retries: int = 5,
    retry_delay: float = 0.5,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """
    Synchronously moves files to 'finished' subfolders.

    Args:
        files_to_move: List of (source_path, destination_folder) tuples
        logger: Optional logger for output
        wait_before_move: Seconds to wait before moving (helps with file handle release)
        close_handles_callback: Optional callback to release file handles before moving
        max_retries: Number of attempts to try before failing
        retry_delay: Delay between attempts

    Returns:
        Tuple of (moved_count, failed_count, list of (filename, error_message) for failures)
    """
    moved_count = 0
    failed_files: List[Tuple[str, str]] = []

    # Close handles and wait if callback provided
    if close_handles_callback:
        close_handles_callback()
    if wait_before_move > 0:
        time.sleep(wait_before_move)

    for src_path, dest_folder in files_to_move:
        if not os.path.exists(src_path):
            moved_count += 1
            continue

        success, error_msg = move_single_file(src_path, dest_folder, logger, max_retries, retry_delay)

        if success:
            moved_count += 1
        else:
            failed_files.append((os.path.basename(src_path), error_msg))

    return moved_count, len(failed_files), failed_files


class FileOrganizerWorker:
    """
    Background worker that moves files to finished folders with retry logic.
    Uses a queue for async processing with automatic retries.
    """

    def __init__(self, logger: Optional[logging.Logger] = None, max_retries: int = 3, retry_delay: float = 1.0):
        self.logger = logger
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._stats = {"moved": 0, "failed": 0, "retries": 0}

    def start(self):
        """Start the background worker thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        if self.logger:
            self.logger.debug("FileOrganizerWorker started")

    def stop(self, wait: bool = True, timeout: float = 5.0):
        """Stop the background worker."""
        self._stop_event.set()

        if wait and self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

        if self.logger:
            self.logger.debug(
                f"FileOrganizerWorker stopped. Stats: moved={self._stats['moved']}, "
                f"failed={self._stats['failed']}, retries={self._stats['retries']}"
            )

    def put(self, src_path: str, dest_folder: str, priority: int = 0):
        """
        Add a file to the move queue.

        Args:
            src_path: Source file path
            dest_folder: Destination folder (finished subfolder will be created inside)
            priority: Higher priority items are processed first (default 0)
        """
        self._queue.put((priority, src_path, dest_folder))

    def put_batch(self, files: List[Tuple[str, str]], priority: int = 0):
        """Add multiple files to the queue."""
        for src_path, dest_folder in files:
            self.put(src_path, dest_folder, priority)

    def wait_until_empty(self, timeout: float = 30.0):
        """Wait until every file put has been dealt with -- including the one
        being moved (Studio audit 2026-09-28: an empty queue was taken for
        done while the last move was still running)."""
        start_time = time.time()
        while self._queue.unfinished_tasks:
            if time.time() - start_time > timeout:
                if self.logger:
                    self.logger.warning("Timeout waiting for file organizer queue to empty")
                return
            time.sleep(0.1)

    def get_stats(self) -> dict:
        """Get current statistics."""
        return self._stats.copy()

    def _worker_loop(self):
        """Main worker loop that processes the queue."""
        while not self._stop_event.is_set():
            try:
                # Get item with timeout so we can check stop_event
                priority, src_path, dest_folder = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # A file already taken is moved even if stop() came meanwhile
            # (Studio audit 2026-09-28: it was dropped, never moved nor marked
            # done); the loop ends after it.

            # Try to move the file with retries
            success = False
            for attempt in range(self.max_retries):
                result, error_msg = move_single_file(src_path, dest_folder, logger=self.logger, max_retries=1)

                if result:
                    success = True
                    self._stats["moved"] += 1
                    if self.logger:
                        self.logger.debug(f"Moved to finished: {os.path.basename(src_path)}")
                    break
                else:
                    self._stats["retries"] += 1
                    if attempt < self.max_retries - 1:
                        if self.logger:
                            self.logger.debug(
                                f"Retry {attempt + 1}/{self.max_retries} for {os.path.basename(src_path)}: {error_msg}"
                            )
                        time.sleep(self.retry_delay)

            if not success:
                self._stats["failed"] += 1
                if self.logger:
                    self.logger.error(f"Failed to move after {self.max_retries} attempts: {os.path.basename(src_path)}")

            self._queue.task_done()


def describe_restore(restored: int, failed_files: List[Tuple[str, str]]) -> str:
    """What Restore Finished did, for the message after it: how many came
    back, and each file that did not -- with why, grouped (a file kept because
    a different one of its name is in the input folder is not an error the
    user has to fix, but they should know it is still in finished/)."""
    lines = [f"{restored} file{'s' if restored != 1 else ''} restored."]
    by_reason: dict = {}
    for name, reason in failed_files:
        by_reason.setdefault(reason, []).append(name)
    for reason, names in by_reason.items():
        shown = ", ".join(names[:8]) + (f" (+{len(names) - 8} more)" if len(names) > 8 else "")
        lines.append(f"{reason[:1].upper()}{reason[1:]}: {shown}")
    return "\n".join(lines)


def restore_finished_files(
    restore_dirs: List[Tuple[str, str]],
    logger: Optional[logging.Logger] = None,
    wait_before_move: float = 0.0,
    close_handles_callback: Optional[Callable[[], None]] = None,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """
    Moves files from 'finished' subfolders back to their original input directories.

    Args:
        restore_dirs: List of (input_folder, finished_folder) tuples
        logger: Optional logger for output
        wait_before_move: Seconds to wait before moving
        close_handles_callback: Optional callback to release file handles before moving

    Returns:
        Tuple of (restored_count, failed_count, list of (filename, error_message) for failures)
    """
    restored_count = 0
    failed_files: List[Tuple[str, str]] = []

    if close_handles_callback:
        close_handles_callback()
    if wait_before_move > 0:
        time.sleep(wait_before_move)

    for input_folder, finished_folder in restore_dirs:
        finished_path = os.path.join(input_folder, finished_folder)

        if not os.path.isdir(finished_path):
            if logger:
                logger.debug(f"Restore skipped: 'finished' folder not found at {finished_path}")
            continue

        try:
            files_to_restore = [f for f in os.listdir(finished_path) if os.path.isfile(os.path.join(finished_path, f))]
        except Exception as e:
            if logger:
                logger.error(f"Error listing finished folder {finished_path}: {e}")
            failed_files.append((finished_folder, str(e)))
            continue

        if not files_to_restore:
            if logger:
                logger.debug(f"Restore skipped: No files found in {finished_path}")
            continue

        for filename in files_to_restore:
            src_path = os.path.join(finished_path, filename)
            dest_path = os.path.join(input_folder, filename)

            # A file of this name is already back in the input folder. The
            # archived copy used to be deleted for good whatever either file
            # was -- after a re-render, that destroyed the only copy of the
            # old clip (audit 2026-09-28 #9). Now: the same file -> the
            # archived duplicate goes to the Recycle Bin; a different one ->
            # both stay, and the restore says why this one was not brought back.
            if os.path.exists(dest_path):
                if not _looks_identical(src_path, dest_path):
                    reason = "kept in finished: it differs from the file of this name in the input folder"
                    if logger:
                        logger.warning(f"Not restoring '{filename}': {reason}.")
                    failed_files.append((filename, reason))
                    continue
                from core.common import recycle_bin
                if recycle_bin.send_to_recycle_bin(src_path):
                    restored_count += 1
                    if logger:
                        logger.info(f"'{filename}' is already in the input folder; the duplicate in "
                                    f"finished went to the Recycle Bin.")
                else:
                    reason = "kept in finished: a duplicate of the input folder's copy, but it could not be recycled"
                    if logger:
                        logger.warning(f"Not restoring '{filename}': {reason}.")
                    failed_files.append((filename, reason))
                continue

            for attempt in range(5):
                try:
                    shutil.move(src_path, input_folder)
                    if logger:
                        logger.info(f"Restored '{filename}' from finished to {input_folder}")
                    restored_count += 1
                    break
                except Exception as e:
                    error_msg = str(e)
                    if attempt < 4:
                        if logger:
                            logger.warning(f"Error restoring '{filename}': {error_msg}. Retrying in 0.5s...")
                        time.sleep(0.5)
                        continue
                    if logger:
                        logger.error(f"Error restoring '{filename}' after 5 attempts: {error_msg}")
                    failed_files.append((filename, error_msg))
                    break

        # Remove empty finished folder
        try:
            if os.path.exists(finished_path) and not os.listdir(finished_path):
                os.rmdir(finished_path)
                if logger:
                    logger.debug(f"Removed empty finished folder: {finished_path}")
        except Exception as e:
            if logger:
                logger.warning(f"Could not remove empty finished folder '{finished_path}': {e}")

    return restored_count, len(failed_files), failed_files
