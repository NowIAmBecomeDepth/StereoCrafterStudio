"""last_jobs.json: two stages recording at once both keep their entry (bug
sweep 2026-10-09, A2: the read-merge-write had no lock, and the later write
dropped the other's stage). A slowed read stands in for the overlap.

    python -m unittest studio.tests.test_job_record_lock
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.ui.job_handoff as jh  # noqa: E402


class LockTests(unittest.TestCase):
    def test_overlapping_records_both_land(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = str(Path(d.name) / "last_jobs.json")
        from core.common import atomic_json
        real = atomic_json.read_json

        def slow(p):
            out = real(p)
            time.sleep(0.2)           # both read before either writes, without the lock
            return out
        with mock.patch.object(jh, "_RECORD_PATH", path), mock.patch.object(atomic_json, "read_json", slow):
            ts = [threading.Thread(target=jh.record_job, args=(s, {"output": f"E:/{s}"}))
                  for s in ("scenecut", "depthedit")]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), ["depthedit", "scenecut"])


if __name__ == "__main__":
    unittest.main()
