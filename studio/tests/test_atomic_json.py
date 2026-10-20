"""Sidecars and the job handoff on disk (audit 2026-09-28, minor).

* A sidecar was written in place (truncate, then write): a crash or a failed
  dump in between left an empty / broken sidecar -- the clip's settings gone.
* last_jobs.json was read as {} on ANY failure -- a moment's lock by an
  antivirus or a sync tool included -- and the next record then wrote that
  {} plus one stage back, wiping every other stage's handoff.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.ui.job_handoff as jh  # noqa: E402
from core.common.sidecar_manager import SidecarConfigManager  # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


class SidecarWriteTests(_Tmp):
    def test_a_failed_save_leaves_the_old_sidecar_whole(self):
        path = self.dir / "clip.fssidecar"
        path.write_text(json.dumps({"convergence_plane": 0.4, "max_disparity": 30}))
        before = path.read_bytes()

        class Unserialisable:
            pass
        ok = SidecarConfigManager().save_sidecar_data(str(path), {"convergence_plane": 0.5, "bad": Unserialisable()})
        self.assertFalse(ok)
        self.assertEqual(path.read_bytes(), before, "a failed save truncated the sidecar")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["clip.fssidecar"], "a temp file was left")

    def test_a_save_lands_whole(self):
        path = self.dir / "clip.fssidecar"
        self.assertTrue(SidecarConfigManager().save_sidecar_data(str(path), {"convergence_plane": 0.5}))
        self.assertEqual(json.loads(path.read_text())["convergence_plane"], 0.5)


class HandoffTests(_Tmp):
    def setUp(self):
        super().setUp()
        self._saved = jh._RECORD_PATH
        jh._RECORD_PATH = str(self.dir / "last_jobs.json")
        self.addCleanup(setattr, jh, "_RECORD_PATH", self._saved)
        Path(jh._RECORD_PATH).write_text(json.dumps({
            "depthcrafter": {"_stage": "depthcrafter", "depth_out": "D:/depth"},
            "splatting": {"_stage": "splatting", "splat_out": "D:/splat"}}))

    def test_a_locked_file_is_not_wiped(self):
        before = Path(jh._RECORD_PATH).read_bytes()
        real_open = open

        def locked(path, *a, **kw):
            if str(path) == jh._RECORD_PATH and (not a or "r" in a[0]):
                raise PermissionError(13, "locked by another process")
            return real_open(path, *a, **kw)
        jh.open = locked
        self.addCleanup(lambda: delattr(jh, "open") if "open" in vars(jh) else None)
        import core.common.atomic_json as AJ
        AJ.open = locked
        self.addCleanup(lambda: delattr(AJ, "open") if "open" in vars(AJ) else None)
        self.assertFalse(jh.record_job("inpainting", {"inpainted_out": "D:/inp"}))
        self.assertEqual(Path(jh._RECORD_PATH).read_bytes(), before, "the other stages' handoffs were wiped")

    def test_a_corrupt_file_is_kept_aside_not_lost(self):
        Path(jh._RECORD_PATH).write_text("{ not json")
        self.assertTrue(jh.record_job("inpainting", {"inpainted_out": "D:/inp"}))
        self.assertEqual(Path(jh._RECORD_PATH + ".corrupt").read_text(), "{ not json")
        self.assertIn("inpainting", json.loads(Path(jh._RECORD_PATH).read_text()))

    def test_a_good_file_keeps_every_stage(self):
        self.assertTrue(jh.record_job("inpainting", {"inpainted_out": "D:/inp"}))
        self.assertEqual(sorted(json.loads(Path(jh._RECORD_PATH).read_text())),
                         ["depthcrafter", "inpainting", "splatting"])


if __name__ == "__main__":
    unittest.main()
