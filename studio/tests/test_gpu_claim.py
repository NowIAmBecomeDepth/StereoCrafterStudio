"""GpuJobs.claim when a stage's holds_gpu() or release_gpu() raises (bug sweep
2026-10-09, A2): it was a log line only -- read as "holds nothing", or left out
of the restore, and the job started with the memory still held.

    python -m unittest studio.tests.test_gpu_claim
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from PySide6.QtCore import QCoreApplication  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication(sys.argv)


class Stage:
    def __init__(self, holds=True, holds_raises=False, release_raises=False):
        self._holds, self._hr, self._rr = holds, holds_raises, release_raises
        self.released = self.restored = 0

    def is_busy(self):
        return False

    def holds_gpu(self):
        if self._hr:
            raise RuntimeError("engine mid-teardown")
        return self._holds

    def release_gpu(self):
        self.released += 1
        if self._rr:
            raise RuntimeError("CUDA error: an illegal memory access")

    def restore_gpu(self):
        self.restored += 1


class ClaimTests(unittest.TestCase):
    def jobs(self, *stages):
        from studio.gpu_jobs import GpuJobs
        j = GpuJobs()
        self.warned = []
        j.warned.connect(lambda t, m: self.warned.append(m))
        j.register("claimant", "Claimant", Stage(holds=False))
        for i, s in enumerate(stages):
            j.register(f"s{i}", f"Stage {i}", s)
        return j

    def test_a_raising_holds_gpu_is_still_released(self):
        s = Stage(holds_raises=True)
        self.assertTrue(self.jobs(s).claim("claimant"))
        self.assertEqual(s.released, 1)

    def test_a_failed_release_warns_and_is_restored_later(self):
        s = Stage(release_raises=True)
        j = self.jobs(s)
        self.assertTrue(j.claim("claimant"))
        self.assertEqual(len(self.warned), 1)
        self.assertIn("Stage 0", self.warned[0])
        self.assertIn("illegal memory access", self.warned[0])
        self.assertIn(s, j._restore)

    def test_a_clean_release_says_nothing(self):
        self.assertTrue(self.jobs(Stage()).claim("claimant"))
        self.assertEqual(self.warned, [])


if __name__ == "__main__":
    unittest.main()
