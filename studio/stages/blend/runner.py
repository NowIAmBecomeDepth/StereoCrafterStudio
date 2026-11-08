"""runner.py -- the Blend tab's batch: each clip's DepthCrafter and VDA maps blended into the map Splatting reads
(core.depthblend). Nothing here imports Qt; torch only through core.depthblend, when a batch starts.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List, Optional

from studio.outcome import Outcome, is_out_of_memory
from studio.stages.vda.runner import Hooks, _Ticker

logger = logging.getLogger("blend")

OOM_HINT = "It ran out of graphics memory. Pick a smaller Output Size, then run it again."


class BlendRunner:
    def __init__(self, opts: Dict, hooks: Hooks, stop_event: threading.Event,
                 stop_now: Optional[threading.Event] = None, stats_cache: Optional[Dict] = None,
                 per_clip: Optional[Dict[str, Dict]] = None):
        self.opts = opts
        #: clips' own settings (Own Settings; user, 2026-11-07), over `opts` for those clips
        self.per_clip = per_clip or {}
        self.hooks = hooks
        self.stop = stop_event
        self.stop_now = stop_now or threading.Event()
        self.outcome = Outcome()
        self.made: List[Dict] = []
        #: the measured sums by clip (the controller's, shared with the preview): a clip measured for the
        #: preview isn't measured again
        self.stats_cache = stats_cache if stats_cache is not None else {}

    def run_batch(self, pairs: List[Dict[str, str]]) -> bool:
        from core import depthblend as B
        total = len(pairs)
        if not total:
            self.outcome.note = f"No clip there has both a DepthCrafter and a {self.opts.get('other') or 'VDA'} map."
            return True
        for i, pair in enumerate(pairs):
            if self.stop.is_set():
                self.outcome.halted = True
                break
            self.hooks.progress(i, total)
            self.hooks.status(f"Blending {i + 1} of {total}: {pair['clip']}")
            self.hooks.clip(pair["clip"], "", "")
            t0 = time.monotonic()
            ticks: Dict[str, _Ticker] = {}

            def tick(phase, done, frames, _clip=pair["clip"]):
                t = ticks.get(phase)
                if t is None or t.total != frames:
                    t = ticks[phase] = _Ticker(self.hooks, frames, phase, _clip)
                t(done)
            try:
                key = stats_key(pair)
                own = self.per_clip.get(pair["clip"])
                if own:
                    from studio.stages.blend import settings as S
                    opts = S.opts_for(self.opts, own)
                    logger.info("%s: its own settings (%s).", pair["clip"], opts["mode"])
                else:
                    opts = self.opts
                res = B.blend_clip(pair, opts, self.stop_now, tick, self.stats_cache.get(key))
                self.outcome.done += 1
                self.made.append({**pair, **res})
                logger.info("%s: blended in %.1f s into %s%s%s.", pair["clip"], time.monotonic() - t0, pair["out"],
                            f"; the map it replaced is in {os.path.dirname(res['set_aside'])}" if res["set_aside"] else "",
                            f" ({res['note']})" if res["note"] else "")
            except InterruptedError:
                logger.info("Stopped during %s; its blend was not written.", pair["clip"])
                break
            except Exception as e:
                logger.exception("Blending %s failed", pair["clip"])
                self.outcome.failed += 1
                if is_out_of_memory(e):
                    self.outcome.hint = OOM_HINT
        self.hooks.progress(self.outcome.done + self.outcome.failed, total)
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        return True


def stats_key(pair: Dict[str, str]):
    """What a clip's measured sums depend on: its two maps as they are on disk."""
    def stamp(p):
        try:
            st = os.stat(p)
            return (os.path.normcase(os.path.abspath(p)), st.st_mtime, st.st_size)
        except OSError:
            return (p, 0, 0)
    return stamp(pair["dc"]), stamp(pair["other"])
