"""parallel.py -- Splatting's High VRAM Mode: several clips at once, each in a worker process of its own.

A user with 24 GB of VRAM ran four copies of Studio side by side, each splatting a share of the clips, to
use the card (2026-10-30); this does that inside one Studio. One Splatting job, one GPU claim: the batch's
clips (BatchProcessor.select_videos, in number order) are handed out one at a time to up to `jobs` workers,
each running the same BatchProcessor on its clip. Processes, not threads: two threads ran slower than one
(the GIL), two processes faster.

The workers are `python -m core.splatting.parallel` -- not multiprocessing's spawn, which would import
studio.app (all of Qt and every stage, 4 s) into each -- and talk to Studio over a private pipe
(multiprocessing.connection). The page reads the same progress queue a BatchProcessor fills: the batch's
total and tasks done, and the most recently started clip's passes, status and frames. Every worker's log
lines reach the console, the clip's name before them.

The GPU fills one job at a time: the first starts at once, and another only after a clip has finished,
when the card has room for one more clip like it (its peak, as that worker measured it, and a new
process's own CUDA and PyTorch) -- so a card too full for the number asked runs fewer, and says so.
Stop reaches every worker; Stop After Clip hands out no more clips.
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Room a job adds beyond its clip's own peak: its process's CUDA context and PyTorch, and a margin
NEW_JOB_OVERHEAD = int(1.0 * 2 ** 30)
#: How long a worker gets to start and connect
CONNECT_TIMEOUT_S = 120.0
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ParallelBatchProcessor:
    """BatchProcessor's interface (run_batch_process, stop_after_clip, result, validate_settings), its
    clips spread over `jobs` worker processes. Fewer than two clips, or one clip given as a file: the
    ordinary BatchProcessor, as it always ran."""

    def __init__(self, progress_queue: "queue.Queue", stop_event: threading.Event, jobs: int,
                 sidecar_manager: Optional[Any] = None):
        self.progress_queue = progress_queue
        self.stop_event = stop_event
        self.jobs = max(1, int(jobs))
        self.sidecar_manager = sidecar_manager
        self.stop_after_clip: Optional[threading.Event] = None
        self.result: Dict[str, Any] = {}

    def _serial(self):
        from core.splatting.batch_processing import BatchProcessor
        bp = BatchProcessor(self.progress_queue, self.stop_event, self.sidecar_manager)
        bp.stop_after_clip = self.stop_after_clip
        return bp

    def validate_settings(self, settings):
        return self._serial().validate_settings(settings)

    def run_batch_process(self, settings, from_index: int = 0, to_index: Optional[int] = None,
                          video_list: Optional[List[Dict]] = None) -> None:
        from core.splatting.batch_processing import BatchProcessor
        probe = BatchProcessor(queue.Queue(), self.stop_event, self.sidecar_manager)
        setup = probe.setup_batch_processing(settings)
        videos = ([] if setup.error or setup.is_single_file_mode
                  else probe.select_videos(setup, settings, from_index, to_index, video_list))
        tasks = probe.get_defined_tasks(settings)
        if len(videos) < 2 or self.jobs < 2 or not tasks:
            bp = self._serial()
            bp.run_batch_process(settings=settings, from_index=from_index, to_index=to_index, video_list=video_list)
            self.result = bp.result
            return
        _Run(self, settings, videos, len(tasks)).go()


class _Run:
    """One High VRAM batch: the workers, the clips handed out, the progress merged into one."""

    def __init__(self, owner: ParallelBatchProcessor, settings, videos: List[str], n_tasks: int):
        import pickle
        try:
            pickle.dumps(settings)
        except Exception as e:
            raise ValueError(f"High VRAM Mode can't hand these settings to its jobs ({e}).") from e
        self.o, self.settings, self.n_tasks = owner, settings, n_tasks
        self.videos = list(videos)
        self.pending = collections.deque(self.videos)
        self.jobs = min(owner.jobs, len(self.videos))
        self.events: "queue.Queue" = queue.Queue()
        self.conns: Dict[int, Any] = {}
        self.procs: Dict[int, subprocess.Popen] = {}
        self.running: Dict[int, str] = {}           # worker -> its clip
        self.active: set = set()                     # workers given clips
        self.asking: List[int] = []                  # workers waiting for their next clip
        self.gone: set = set()
        self.cur_tasks: Dict[int, int] = {}          # worker -> tasks done on its clip so far
        self.done_tasks = 0
        self.started = 0
        self.focus: Optional[int] = None             # the worker whose clip the page shows
        # each job's clip and its passes, for Upcoming Jobs: {name, frames [H, L], done [H, L], pass}
        self.jobstate: Dict[int, Dict[str, Any]] = {}
        self.peak = 0                                # the largest clip peak seen, bytes
        self.free = 0                                # the card's free memory at the last report
        self.said_room = False
        self.result = {"done": 0, "failed": 0, "skipped": 0, "error": "", "note": ""}
        self.stopping = False

    # ---- the workers -------------------------------------------------------------------------------------

    def _start_workers(self) -> None:
        from multiprocessing.connection import Listener
        key = secrets.token_bytes(32)
        self.listener = Listener(authkey=key)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for wid in range(self.jobs):
            self.procs[wid] = subprocess.Popen(
                [sys.executable, "-m", "core.splatting.parallel", str(self.listener.address), key.hex(), str(wid)],
                cwd=_REPO, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._drain_stderr, args=(wid,), daemon=True, name=f"splat-job{wid}-err").start()
        threading.Thread(target=self._accept, daemon=True, name="splat-jobs-accept").start()

    def _drain_stderr(self, wid: int) -> None:
        tail = collections.deque(maxlen=30)
        for raw in iter(self.procs[wid].stderr.readline, b""):
            tail.append(raw.decode("utf-8", "replace").rstrip())
        self.events.put((wid, ("exited", "\n".join(tail))))

    def _accept(self) -> None:
        for _ in range(self.jobs):
            try:
                conn = self.listener.accept()
            except Exception:
                return
            try:
                hello = conn.recv()
            except Exception:
                continue
            wid = int(hello[1])
            self.conns[wid] = conn
            conn.send(("settings", self.settings))
            threading.Thread(target=self._read, args=(wid, conn), daemon=True, name=f"splat-job{wid}").start()

    def _read(self, wid: int, conn) -> None:
        while True:
            try:
                msg = conn.recv()
            except Exception:
                self.events.put((wid, ("closed",)))
                return
            self.events.put((wid, msg))

    def _send(self, wid: int, msg) -> None:
        conn = self.conns.get(wid)
        if conn is None:
            return
        try:
            conn.send(msg)
        except Exception:
            pass

    # ---- the loop ----------------------------------------------------------------------------------------

    def go(self) -> None:
        q = self.o.progress_queue
        q.put(("total", len(self.videos) * self.n_tasks))
        logger.info(f"High VRAM Mode: {len(self.videos)} clips, up to {self.jobs} at once.")
        try:
            self._start_workers()
            began = time.monotonic()
            while len(self.gone) < self.jobs:
                try:
                    wid, msg = self.events.get(timeout=0.25)
                except queue.Empty:
                    wid = msg = None
                if not self.stopping and self.o.stop_event.is_set():
                    self.stopping = True
                    for w in list(self.conns):
                        self._send(w, ("stop",))
                if msg is not None:
                    self._on(wid, msg)
                self._dispatch()
                if not self.conns and time.monotonic() - began > CONNECT_TIMEOUT_S:
                    raise RuntimeError("High VRAM Mode: no worker started (see the console).")
        except Exception as e:
            logger.error(f"High VRAM Mode: {e}", exc_info=True)
            self.result["error"] = self.result["error"] or f"{e.__class__.__name__}: {e}"
        finally:
            self._end()
            self.o.result = dict(self.result)
            q.put(("result", dict(self.result)))
            q.put("finished")

    def _on(self, wid: int, msg) -> None:
        kind = msg[0]
        if kind == "ready":
            self._finished_clip(wid, msg[1])
            if wid not in self.asking:
                self.asking.append(wid)
        elif kind == "msg":
            self._progress(wid, msg[1])
        elif kind == "log":
            _level, name, text = msg[1], msg[2], msg[3]
            clip = self.running.get(wid)
            logging.getLogger(name).log(_level, f"[{os.path.basename(clip)}] {text}" if clip else text)
        elif kind in ("closed", "exited"):
            if wid in self.gone:
                return
            clip = self.running.pop(wid, None)
            if self.jobstate.pop(wid, None) is not None:
                self._say_jobs()
            if clip is not None and not self.stopping:
                self.result["failed"] += 1
                logger.error(f"High VRAM Mode: the job running {os.path.basename(clip)} ended without "
                             f"finishing it.\n{msg[1] if kind == 'exited' else ''}".rstrip())
            self.gone.add(wid)
            self.conns.pop(wid, None)
            self.active.discard(wid)
            if wid in self.asking:
                self.asking.remove(wid)

    def _finished_clip(self, wid: int, report: Dict[str, Any]) -> None:
        clip = self.running.pop(wid, None)
        self.done_tasks += self.cur_tasks.pop(wid, 0)
        res = report.get("result")
        if self.jobstate.pop(wid, None) is not None:
            if clip is not None and isinstance(res, dict) and int(res.get("done") or 0) > 0:
                self.o.progress_queue.put(("job_done", os.path.splitext(os.path.basename(clip))[0]))
            self._say_jobs()
        if clip is not None and isinstance(res, dict):
            for k in ("done", "failed", "skipped"):
                self.result[k] += int(res.get(k) or 0)
            if res.get("error") and not self.result["error"]:
                self.result["error"] = res["error"]
            if res.get("hint"):
                self.result["hint"] = res["hint"]
        if report.get("peak"):
            self.peak = max(self.peak, int(report["peak"]))
            self.free = int(report.get("free") or 0)
        self.o.progress_queue.put(("tasks_done", self.done_tasks))

    def _say_jobs(self) -> None:
        """Every running job's clip and passes to the page (Upcoming Jobs shows each one's bars)."""
        self.o.progress_queue.put(("jobs", [{"name": j["name"], "frames": list(j["frames"]), "done": list(j["done"])}
                                            for _w, j in sorted(self.jobstate.items())]))

    def _progress(self, wid: int, m) -> None:
        if m == "finished" or not isinstance(m, tuple) or not m:
            return
        kind = m[0]
        job = self.jobstate.get(wid)
        if job is not None and kind == "status":
            for i, name in enumerate(("Full-Resolution", "Low-Resolution")):
                if str(m[1]).startswith(f"Processing {name} for "):
                    if 0 <= job["pass"] != i:
                        job["done"][job["pass"]] = True
                    job["pass"], job["frames"][i] = i, 0
                    self._say_jobs()
        elif job is not None and kind == "processed" and job["pass"] >= 0:
            job["frames"][job["pass"]] = int(m[1])
            self._say_jobs()
        if kind == "tasks_done":
            self.cur_tasks[wid] = int(m[1])
            self.o.progress_queue.put(("tasks_done", self.done_tasks + sum(self.cur_tasks.values())))
        elif kind in ("status", "update_info", "processed") and wid == self.focus:
            self.o.progress_queue.put(m)
        # "total", "clip", "result" are the worker's own one-clip run's: the batch's are said here

    def _room_for_another(self) -> bool:
        if not self.active:
            return True
        if not self.peak:
            return False                 # wait for one clip to say what a clip takes
        return self.free - NEW_JOB_OVERHEAD >= self.peak

    def _dispatch(self) -> None:
        halted = self.o.stop_after_clip is not None and self.o.stop_after_clip.is_set()
        for wid in list(self.asking):
            if self.stopping or halted or not self.pending:
                if halted and self.pending and "halted" not in self.result:
                    self.result["halted"] = True
                    logger.info("==> Stopped after the clips running, as asked; the rest are left for the next run.")
                self._send(wid, ("done",))
                self.asking.remove(wid)
                continue
            if wid not in self.active:
                if len(self.active) >= self.jobs:
                    continue
                if not self._room_for_another():
                    if self.peak and not self.said_room:
                        self.said_room = True
                        logger.info(f"High VRAM Mode: room on the GPU for {len(self.active)} at once "
                                    f"(a clip takes {self.peak / 2 ** 30:.1f} GB, {self.free / 2 ** 30:.1f} GB free).")
                    continue
                if self.active:          # its share counted as used until the next report says otherwise
                    self.free -= self.peak + NEW_JOB_OVERHEAD
                self.active.add(wid)
            clip = self.pending.popleft()
            self.running[wid] = clip
            self.cur_tasks[wid] = 0
            self.started += 1
            self.focus = wid
            self.asking.remove(wid)
            self._send(wid, ("clip", clip))
            self.o.progress_queue.put(("clip", (self.started, len(self.videos))))
            self.jobstate[wid] = {"name": os.path.basename(clip), "frames": [0, 0], "done": [False, False], "pass": -1}
            self._say_jobs()

    def _end(self) -> None:
        for wid in list(self.conns):
            self._send(wid, ("stop",) if self.stopping else ("done",))
        deadline = time.monotonic() + 60
        for p in self.procs.values():
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
        for conn in list(self.conns.values()):
            try:
                conn.close()
            except Exception:
                pass
        try:
            self.listener.close()
        except Exception:
            pass


# ---- the worker: python -m core.splatting.parallel ADDRESS KEY ID ----------------------------------------

def _worker(address: str, key_hex: str, wid: int) -> None:
    from multiprocessing.connection import Client
    conn = Client(address, authkey=bytes.fromhex(key_hex))
    lock = threading.Lock()

    def send(msg) -> None:
        with lock:
            conn.send(msg)

    send(("hello", wid))
    settings = conn.recv()[1]
    sys.path.insert(0, _REPO)
    os.chdir(_REPO)
    from studio.legacy import install
    install()
    import torch  # noqa: F401  -- before decord (its DLLs)

    class _ToStudio(logging.Handler):
        def emit(self, record):
            try:
                text = record.getMessage()
                if text.startswith("Process Selected: "):     # each clip is one ticked clip here
                    return
                send(("log", record.levelno, record.name, text))
            except Exception:
                pass
    root = logging.getLogger()
    root.handlers[:] = [_ToStudio()]
    root.setLevel(logging.INFO)

    stop = threading.Event()
    orders: "queue.Queue" = queue.Queue()

    def listen():
        while True:
            try:
                msg = conn.recv()
            except Exception:
                stop.set()
                orders.put(("done",))
                return
            if msg[0] == "stop":
                stop.set()
            else:
                orders.put(msg)
    threading.Thread(target=listen, daemon=True, name="orders").start()

    class _Progress:
        def put(self, m):
            send(("msg", m))

    from core.common.sidecar_manager import SidecarConfigManager
    from core.splatting.batch_processing import BatchProcessor
    bp = BatchProcessor(_Progress(), stop, SidecarConfigManager())
    cuda = torch.cuda.is_available()
    report: Dict[str, Any] = {"result": None}
    while True:
        send(("ready", report))
        order = orders.get()
        if order[0] != "clip":
            break
        settings.only_videos = [order[1]]
        if cuda:
            torch.cuda.reset_peak_memory_stats()
        bp.run_batch_process(settings=settings, from_index=0, to_index=None, video_list=None)
        report = {"result": dict(bp.result)}
        if cuda:
            report["peak"] = int(torch.cuda.max_memory_reserved())
            report["free"] = int(torch.cuda.mem_get_info()[0])
    conn.close()


if __name__ == "__main__":
    _worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
