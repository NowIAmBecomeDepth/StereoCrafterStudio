"""A finished job's notification says how long it took (user, 2026-10-05):
outcome.verdict adds "Took ..." (or "Ran for ..." when it stopped or failed)
from the time the stage claimed the GPU for it, once.

    python -m unittest studio.tests.test_outcome_time
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import gpu_jobs  # noqa: E402
from studio.outcome import Outcome, verdict  # noqa: E402


class JobTimeTests(unittest.TestCase):
    def tearDown(self):
        gpu_jobs._claimed_at.clear()

    def test_the_time_since_the_claim(self):
        gpu_jobs._claimed_at["splatting"] = time.monotonic() - 3733
        v = verdict("Splatting", Outcome(done=20))
        self.assertEqual((v.kind, v.text), ("success", "20 done\nTook 1h 02m 13s."))

    def test_reported_once(self):
        gpu_jobs._claimed_at["depthcrafter"] = time.monotonic() - 65
        verdict("DepthCrafter", Outcome(done=1))
        self.assertEqual(verdict("DepthCrafter", Outcome(done=1)).text, "1 done")

    def test_stopped_or_failed_says_ran_for(self):
        self.assertTrue(verdict("Merging", Outcome(done=2), stopped=True, seconds=95).text.endswith("Ran for 1m 35s."))
        self.assertTrue(verdict("Inpainting V2", Outcome(error="boom"), seconds=5).text.endswith("Ran for 5.0s."))

    def test_no_claim_no_time(self):
        self.assertEqual(verdict("Splatting", Outcome(done=3)).text, "3 done")


class StopAfterFailureTests(unittest.TestCase):
    """bug sweep A1: a run that failed and was then stopped read "stopped" and
    dropped the error (DepthCrafter and Inpainting V1 pass the raw Stop)."""

    def test_the_error_wins(self):
        v = verdict("DepthCrafter", Outcome(done=1, error="CUDA out of memory"), stopped=True)
        self.assertEqual(v.kind, "error")
        self.assertIn("CUDA out of memory", v.text)

    def test_a_plain_stop_is_still_a_stop(self):
        self.assertEqual(verdict("DepthCrafter", Outcome(done=1), stopped=True).kind, "warning")


class HintTests(unittest.TestCase):
    """A failed run says what to change (release check 2026-10-17: out of graphics memory said only "1 failed")."""

    def test_a_failure_carries_its_hint(self):
        from studio.outcome import Outcome, _verdict
        v = _verdict("Splatting", Outcome(failed=1, hint="Lower Batch."))
        self.assertIn("Lower Batch.", v.text)
        v = _verdict("DepthCrafter", Outcome(error="It broke", hint="Turn on segments."))
        self.assertIn("Turn on segments.", v.text)
        v = _verdict("Splatting", Outcome(done=2, failed=1, hint="Lower Batch."))
        self.assertIn("Lower Batch.", v.text)

    def test_no_hint_no_change_and_a_success_never_shows_one(self):
        from studio.outcome import Outcome, _verdict
        self.assertEqual(_verdict("Splatting", Outcome(failed=1)).text, "0 done, 1 failed. See the console.")
        self.assertNotIn("Lower", _verdict("Splatting", Outcome(done=1, hint="Lower Batch.")).text)

    def test_from_dict_reads_it(self):
        from studio.outcome import Outcome
        self.assertEqual(Outcome.from_dict({"failed": 1, "hint": "x"}).hint, "x")

    def test_is_out_of_memory(self):
        from studio.outcome import is_out_of_memory
        self.assertTrue(is_out_of_memory(type("OutOfMemoryError", (RuntimeError,), {})("CUDA")))
        self.assertTrue(is_out_of_memory(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")))
        self.assertFalse(is_out_of_memory(ValueError("bad frame")))


if __name__ == "__main__":
    unittest.main()
