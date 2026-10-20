"""CLI and console utilities for StereoCrafter."""

import os
import sys
import logging

def draw_progress_bar(current, total, bar_length=50, prefix="Progress:", suffix="", keep_suffix=False):
    """
    Draws an ASCII progress bar in the console, overwriting the same line.
    Adds a newline only when 100% complete.
    """
    if total == 0:
        sys.stdout.write(f"\r{prefix} [Skipped (Total 0)] {suffix}")
        sys.stdout.flush()
        return

    percent = 100 * (current / float(total))
    filled_length = int(round(bar_length * current / float(total)))
    bar = "█" * filled_length + "-" * (bar_length - filled_length)

    # Format the suffix for completion
    actual_suffix = suffix
    if current == total and not keep_suffix:     # keep_suffix: a rate or a time stays at 100%
        actual_suffix = "Complete      "

    sys.stdout.write(f"\r{prefix} |{bar}| {percent:.1f}% {actual_suffix}")
    sys.stdout.flush()

    if current == total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def format_duration(seconds):
    """Human-readable elapsed time: "42.3s", "12m 05s", "1h 02m 13s".

    Shared so every stage reports job times the same way.
    """
    seconds = max(0.0, float(seconds))
    if seconds < 59.95:  # so 59.97 reads "1m 00s", not "60.0s"
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def set_logger_level(logger, level):
    """Set the logging level for a logger and all its handlers.

    Args:
        logger: The logger instance to modify
        level: The logging level to set (e.g., logging.INFO)
    """
    logger.setLevel(level)
    for h in logger.handlers:
        h.setLevel(level)


def print_job_header(index: int, total: int, video_path: str, width: int = 78) -> None:
    """Print a rule + clip name to the console, ahead of that clip's bars.

    Every stage prints it at the start of each clip (Splatting first; the
    rest since 2026-10-01), so a batch reads as one labelled group per clip.

    Each job emits two progress bars (HiRes and LowRes) straight to stdout,
    which run together into an undifferentiated wall. This breaks them into
    labelled groups so the finished count is readable at a glance.

    Deliberately a print rather than a logger call: the bars go to stdout,
    while the logger is routed to the GUI and the log file, so a logged
    header would never land between them.

    ASCII only — the console this lands in is Windows cmd, which is not
    reliably on a UTF-8 code page.
    """
    name = os.path.basename(video_path)
    label = f" [{index}/{total}] {name} "
    if len(label) > width - 6:
        label = f" [{index}/{total}] {name[: max(8, width - len(str(index)) - len(str(total)) - 16)]}... "
    pad = max(4, width - len(label))
    left = pad // 2
    print(f"\n{'=' * left}{label}{'=' * (pad - left)}", flush=True)
