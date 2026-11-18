"""A move lets go of the clips other stages' previews have open (a tester, 2026-11-17).

After "Rerun in", Splatting showed the clip again -- its source and depth map open -- so DVD's Move to
Finished said "still in use" (Windows won't move an open file) and reruns ended up as "name (2)" or failed.
Now a stage's move first has every OTHER stage's preview let go (GpuJobs.let_go_of_files), and puts them
back after. Here a stand-in preview holds the clip open for real; the move must still go through.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path


class _Preview:
    """Splatting's preview as GpuJobs sees it: holds `path` open while loaded."""

    def __init__(self, path):
        self.path, self.fh, self.restored = path, open(path, "rb"), 0

    def property(self, name):
        return self.fh is not None if name == "loaded" else None

    def _worker_idle(self):
        return True

    def release_for_job(self, then):
        self.fh.close()
        self.fh = None
        then()

    def restore_after_job(self):
        self.restored += 1


class MoveLetsGoTest(unittest.TestCase):
    def setUp(self):
        from studio.tests._qt import app
        self.app = app()
        self.tmp = Path(tempfile.mkdtemp(prefix="sc_letgo_"))
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.clip = self.src / "LC_V2-0338.mp4"
        self.clip.write_bytes(b"x" * 2048)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_move_to_finished_with_the_clip_open_elsewhere(self):
        from PySide6.QtCore import QObject
        from studio.clip_queue import ClipQueue
        from studio.gpu_jobs import GpuJobs

        jobs = GpuJobs()
        owner = QObject()                     # DVD's controller
        splat = QObject()                     # Splatting's, its preview showing the clip
        splat.preview = _Preview(str(self.clip))
        jobs.register("dvd", "DVD", owner)
        jobs.register("splatting", "Splatting", splat)
        q = ClipQueue("depthcrafter", lambda: {}, owner)
        q._start = lambda: None
        q._paths, q._depths = [str(self.clip)], [""]
        got = []
        q.movedToFinished.connect(lambda moved, left: got.append((moved, list(left))))
        q.moveToFinished(str(self.clip))
        end = time.monotonic() + 20
        while not got and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)
        if splat.preview.fh is not None:
            splat.preview.fh.close()
        self.assertEqual(got, [(1, [])], "the clip open in Splatting's preview stayed put")
        self.assertTrue((self.src / "finished" / self.clip.name).is_file())
        self.assertEqual(splat.preview.restored, 1, "Splatting's preview comes back after the move")

    def test_rerun_in_splatting_with_the_depth_map_open_in_merging(self):
        # the tester's second report: Inpainting's Rerun in Splatting -- "LC_V2-0343_depth.mp4 -- couldn't be
        # moved" -- with the depth map open in another stage's preview (Merging's Depth Map preview reads it)
        from PySide6.QtCore import QObject, Property, Signal
        from studio import send_back
        from studio.clip_queue import ClipQueue
        from studio.gpu_jobs import GpuJobs

        fin = self.src / "finished"
        fin.mkdir()
        depth = fin / "LC_V2-0343_depth.mp4"
        depth.write_bytes(b"d" * 2048)
        jobs = GpuJobs()
        owner = QObject()                     # Inpainting's controller
        merging = QObject()
        merging.preview = _Preview(str(depth))

        class Target(QObject):                # Splatting's controller, idle
            took = []

            @Property(bool, constant=True)
            def busy(self):
                return False

            def _values(self):
                return {}

            def takeBack(self, plan):
                Target.took.append(plan)
        target = Target()
        jobs.register("inpainting", "Inpainting", owner)
        jobs.register("merging", "Merging", merging)
        q = ClipQueue("inpainting", lambda: {}, owner)
        q._start = lambda: None
        plan = {"target": "splatting", "moves": [[str(depth), str(self.src)]], "aside": []}
        real_plan, send_back.plan = send_back.plan, lambda *a, **k: dict(plan)
        got = []
        q.sentBack.connect(lambda t, ok, title, text: got.append((ok, text)))
        try:
            q.sendBack("splatting", ["x_splatted2.mp4"], target)
            end = time.monotonic() + 20
            while not got and time.monotonic() < end:
                self.app.processEvents()
                time.sleep(0.02)
        finally:
            send_back.plan = real_plan
            if merging.preview.fh is not None:
                merging.preview.fh.close()
        self.assertEqual(got, [(True, "")], "the depth map open in Merging's preview stayed put")
        self.assertTrue((self.src / depth.name).is_file())
        self.assertEqual(merging.preview.restored, 1)

    def test_own_preview_and_idle_ones_are_left_alone(self):
        from PySide6.QtCore import QObject
        from studio.gpu_jobs import GpuJobs

        jobs = GpuJobs()
        owner = QObject()
        owner.preview = _Preview(str(self.clip))      # the moving stage's own: its queue releases it
        idle = QObject()
        idle.preview = _Preview(str(self.clip))
        idle.preview.fh.close()
        idle.preview.fh = None                       # nothing loaded
        jobs.register("splatting", "Splatting", owner)
        jobs.register("merging", "Merging", idle)
        self.assertEqual(jobs.let_go_of_files(owner), [])
        self.assertEqual(jobs.let_go_of_files(owner.preview), [])
        owner.preview.fh.close()


if __name__ == "__main__":
    unittest.main()
