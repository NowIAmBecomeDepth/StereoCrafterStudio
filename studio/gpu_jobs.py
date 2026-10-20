"""gpu_jobs.py -- one job at a time, and the GPU handed over between stages.

v1's shell (unified_gui/shell.py ``request_job``): before a stage starts a
render it claims the job. The claim is refused while another stage is
rendering; granted, every OTHER stage still holding GPU state is asked to
release it first -- a stage never has to know what the others hold. The
stages that claim are v1's: DepthCrafter, Splatting, Inpainting (V1 and V2
are one stage) and Merging -- and Scene Cut and the Depth Map Editor, which
v1 never had claim (they ran beside a batch: audit 2026-09-28 #15).

A stage takes part through duck-typed methods on its controller:

* ``is_busy()`` (or a ``busy`` property) -- rendering now;
* ``holds_gpu()`` -- has weights / CUDA state resident while idle;
* ``release_gpu()`` -- drop them (v1 release_gpu; the previews let go of
  their clip and engine, as they do before their own stage's batch). A
  release that happens later, on the stage's own worker, returns a
  ``threading.Event`` set once it has really happened;
* ``restore_gpu()`` (optional) -- put a preview back once the job that
  evicted it has finished (v2's previews reload the clip they showed).

The claim ends by itself: a stage is running while its controller is busy,
so there is no release call to forget (v1 needed ``release_job`` on every
exit path, and a missed one blocked every other stage).

The job waits for the hand-over: ``claim`` runs on the UI thread and only
ASKS the others to let go; the previews do it on their workers, behind
whatever render is in flight. So the claimant's job thread calls
``wait_for_releases()`` before it loads anything -- until then the memory
it is about to fill is still someone else's (audit 2026-09-28 #14).
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("studio.gpu_jobs")


def _busy(ctl) -> bool:
    try:
        if hasattr(ctl, "is_busy"):
            return bool(ctl.is_busy())
        return bool(getattr(ctl, "busy", False))
    except Exception:
        return False


#: When each stage last claimed the GPU for a job (time.monotonic()): the
#: finish notification says how long the job took (user, 2026-10-05;
#: outcome.verdict).
_claimed_at: dict = {}


def job_seconds(stage_id: str, forget: bool = True):
    """Seconds since `stage_id` claimed the GPU for its job, or None; by
    default forgotten, so one job's time is never reported twice."""
    t = _claimed_at.pop(stage_id, None) if forget else _claimed_at.get(stage_id)
    return None if t is None else max(0.0, time.monotonic() - t)


def release_cuda_memory() -> None:
    """v1 core.common.gpu_utils.release_cuda_memory, tolerant of no CUDA."""
    gc.collect()
    try:
        from core.common.gpu_utils import release_cuda_memory as release
        release()
    except Exception:
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            logger.debug("Could not empty the CUDA cache", exc_info=True)


def wait_for_gpu(controller) -> None:
    """A stage's job thread, before it loads anything: wait until the stages
    its claim asked to let go really have (GpuJobs.wait_for_releases)."""
    jobs = getattr(controller, "jobs", None)
    if jobs is not None:
        jobs.wait_for_releases()


class GpuJobs(QObject):
    #: A claim was refused (v1's warning): Main shows it.
    refused = Signal(str, str)
    #: a claim went ahead, but a stage couldn't let go of the GPU (bug sweep A2)
    warned = Signal(str, str)

    #: How often a finished job is looked for, to restore the previews it evicted.
    POLL_MS = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        # (stage id, display name, controller); stage ids repeat for V1/V2.
        self._stages: List[Tuple[str, str, Any]] = []
        self._restore: List[Any] = []          # controllers whose preview was evicted
        self._claimant: Optional[Any] = None
        self._pending: List[Tuple[str, Any]] = []   # (stage name, Event) releases still under way
        self._pending_lock = threading.Lock()
        self._seen_busy = False
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._check_finished)

    def register(self, stage_id: str, name: str, controller) -> None:
        self._stages.append((stage_id, name, controller))
        controller.jobs = self

    def running(self) -> Optional[Tuple[str, str, Any]]:
        for entry in self._stages:
            if _busy(entry[2]):
                return entry
        return None

    def claim(self, stage_id: str, controller=None) -> bool:
        """v1 request_job. Call once a start has passed its own validation,
        right before the job begins. False: refused (the warning is shown)."""
        for sid, name, ctl in self._stages:
            if sid != stage_id and _busy(ctl):
                self.refused.emit("Another stage is running",
                                  f"{name} is currently processing. Wait for it to finish "
                                  f"before starting another stage.")
                return False
        released, stuck = [], []
        for sid, name, ctl in self._stages:
            if sid == stage_id or not hasattr(ctl, "release_gpu"):
                continue
            try:
                holds = bool(ctl.holds_gpu()) if hasattr(ctl, "holds_gpu") else False
            except Exception:
                # unknown: asked to let go anyway, rather than read as holding nothing (bug sweep A2)
                logger.exception("holds_gpu failed for %s; releasing it anyway", name)
                holds = True
            if not holds:
                continue
            logger.info("Releasing GPU held by %s for %s", name, stage_id)
            try:
                done = ctl.release_gpu()
                released.append(name)
                if done is not None and hasattr(done, "wait"):
                    with self._pending_lock:
                        self._pending.append((name, done))
                if hasattr(ctl, "restore_gpu") and ctl not in self._restore:
                    self._restore.append(ctl)
            except Exception as e:
                # bug sweep A2: this was a log line only -- the stage kept its memory while the
                # claimant loaded, and nothing restored it. It is restored after the job like the
                # rest, and the user is told which stage may still hold memory.
                logger.exception("release_gpu failed for %s", name)
                stuck.append(f"{name}: {e}")
                if hasattr(ctl, "restore_gpu") and ctl not in self._restore:
                    self._restore.append(ctl)
        if released:
            release_cuda_memory()
        if stuck:
            self.warned.emit("GPU not fully released",
                             "Couldn't free the graphics memory held by " + "; ".join(stuck)
                             + ". The job starts anyway; if it runs out of memory, use Release GPU "
                               "Memory and start it again.")
        if self._restore:
            self._claimant = controller
            self._seen_busy = False
            self._poll.start()
        _claimed_at[stage_id] = time.monotonic()
        return True

    def wait_for_releases(self, timeout: float = 60.0) -> None:
        """From the claimant's JOB thread, before it loads anything: block
        until every release the claim asked for has really happened, then
        empty the CUDA cache of what they freed. Never on the UI thread --
        the previews finish their release there, so waiting on it would
        stall them; there it only logs and returns."""
        with self._pending_lock:
            pending, self._pending = self._pending, []
        if not pending:
            return
        if threading.current_thread() is threading.main_thread():
            logger.warning("wait_for_releases called on the UI thread; not waiting")
            return
        deadline = time.monotonic() + timeout
        for name, done in pending:
            if not done.wait(max(0.0, deadline - time.monotonic())):
                logger.warning("%s had not let go of the GPU after %.0f s; starting anyway.", name, timeout)
        release_cuda_memory()
        logger.info("GPU handed over (%s released).", ", ".join(n for n, _ in pending))

    def _check_finished(self) -> None:
        """The job that evicted the previews has ended: put them back (v2's
        previews come back after their own stage's batch the same way)."""
        claimant = self._claimant
        busy = claimant is not None and _busy(claimant)
        if busy:
            self._seen_busy = True
            return
        if claimant is not None and not self._seen_busy:
            # One grace tick: some starts go busy a moment after the claim.
            self._seen_busy = True
            return
        if self.running() is not None:
            return                             # another job took over; wait for it
        self._poll.stop()
        restore, self._restore, self._claimant = self._restore, [], None
        for ctl in restore:
            try:
                ctl.restore_gpu()
            except Exception:
                logger.exception("restore_gpu failed")
