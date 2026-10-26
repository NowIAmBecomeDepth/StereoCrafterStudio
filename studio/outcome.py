"""How a run ended, told the same way on every stage.

Each stage's run counts its clips (done / failed / skipped) and may name a
fatal error; the controller turns that into one toast and decides whether
the next stage is offered this run's output (the job handoff). Before this,
a run that produced nothing -- an empty folder, missing weights, every clip
failing -- toasted green and handed its (empty) output on (audit
2026-09-28 #3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


@dataclass
class Outcome:
    done: int = 0
    failed: int = 0
    skipped: int = 0
    error: str = ""     # what ended the run early (bad input, model load, a crash)
    note: str = ""      # why nothing was processed, when that is not an error
    halted: bool = False    # ended early by Stop After Clip: the clips done are whole
    hint: str = ""      # what to change when it failed, e.g. out of graphics memory (said after the error)

    @classmethod
    def from_dict(cls, d) -> "Outcome":
        d = d or {}
        return cls(**{k: d[k] for k in ("done", "failed", "skipped", "error", "note", "halted", "hint") if k in d})

    def summary(self) -> str:
        parts = [f"{self.done} done"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        return ", ".join(parts)


class Verdict(NamedTuple):
    kind: str           # the toast's kind: success / warning / error
    title: str
    text: str
    handoff: bool       # record the job handoff (something was produced)


#: Told of every finished run, (kind, title, text), from any thread: the
#: Windows notification while the window is minimized (studio.desktop_notify;
#: user, 2026-10-05). app.build adds the listener.
FINISH_LISTENERS: list = []


def announce(kind: str, title: str, text: str) -> None:
    """A run has finished: tell the listeners (verdict() calls it; Scene Cut,
    whose finish is its own message, too)."""
    for listener in list(FINISH_LISTENERS):
        try:
            listener(kind, title, text)
        except Exception:
            pass


#: verdict's stage names -> the GpuJobs id their job claimed the GPU under.
_JOB_IDS = {"DepthCrafter": "depthcrafter", "Splatting": "splatting", "Inpainting": "inpainting",
            "Inpainting V2": "inpainting", "Merging": "merging", "Scene Cut": "scenecut",
            "Video Depth Anything": "depthcrafter_vda"}


def verdict(stage: str, outcome: Outcome, stopped: bool = False, seconds=None) -> Verdict:
    """The toast and the handoff decision for a finished run of ``stage``,
    with how long the job took (user, 2026-10-05): `seconds`, or the time
    since the stage claimed the GPU for it (gpu_jobs.job_seconds)."""
    v = _verdict(stage, outcome, stopped)
    if seconds is None:
        try:
            from studio.gpu_jobs import job_seconds
            seconds = job_seconds(_JOB_IDS.get(stage, ""))
        except Exception:
            seconds = None
    if seconds is not None:
        from core.common.cli_utils import format_duration
        took = ("Took " if v.kind == "success" else "Ran for ") + format_duration(seconds) + "."
        v = v._replace(text=(v.text.rstrip() + "\n" + took) if v.text.strip() else took)
    announce(v.kind, v.title, v.text)
    return v


def _hint(o: Outcome) -> str:
    """The outcome's hint as its own paragraph, or nothing."""
    return f"\n\n{o.hint.strip()}\n" if o.hint and o.hint.strip() else ""


def is_out_of_memory(e: BaseException) -> bool:
    """torch's OutOfMemoryError, or an error saying it ran out of (CUDA) memory."""
    return type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower()


def _verdict(stage: str, outcome: Outcome, stopped: bool = False) -> Verdict:
    o = outcome
    summary = o.summary()
    # A failure then a Stop is a failure: "stopped" dropped the error (bug sweep A1,
    # DepthCrafter and Inpainting V1; Merging and V2 already passed stopped and not error)
    if stopped and not o.error:
        return Verdict("warning", f"{stage} stopped", summary, False)
    if o.error:
        err = o.error.rstrip()
        if not err.endswith((".", "!", "?")):
            err += "."
        counted = f"\n{summary}." if (o.done or o.failed or o.skipped) else ""
        return Verdict("error", f"{stage} failed", f"{err}{counted}{_hint(o)} See the console.", o.done > 0)
    if o.failed:
        if o.done:
            ended = "stopped after the clip" if o.halted else "finished"
            return Verdict("warning", f"{stage} {ended} with errors", f"{summary}.{_hint(o)} See the console.", True)
        return Verdict("error", f"{stage} failed", f"{summary}.{_hint(o)} See the console.", False)
    if not o.done:
        return Verdict("warning", f"{stage}: nothing processed",
                       o.note or (summary if o.skipped else "No clips were processed."), False)
    if o.halted:        # Stop After Clip: every clip it did is complete, so it is handed on
        return Verdict("success", f"{stage} stopped after the clip", summary, True)
    return Verdict("success", f"{stage} finished", summary, True)
