"""The Depth Map Editor's Undo / Redo history (user, 2026-10-05:
core.depthedit.history). No Qt, no SAM 3.

    python -m unittest studio.tests.test_depthedit_history
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import history as H  # noqa: E402
from core.depthedit.project import Click, Project  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def project():
    p = Project(rgb_path="a.mp4", depth_path="a_depth.mp4")
    layer = p.new_layer(24, 32)
    m = np.zeros((24, 32), bool); m[4:9, 5:12] = True
    layer.masks.set(0, m)
    return p


def state(p, label="edit", selected=0):
    return H.State(label, p.snapshot(), selected, {})


class FingerprintTests(unittest.TestCase):
    def test_what_an_edit_changes_shows(self):
        p = project()
        fp = H.fingerprint(p)
        self.assertEqual(H.fingerprint(p.snapshot()), fp, "a copy is the same")
        p.layers[0].settings.brightness = 0.2
        self.assertNotEqual(H.fingerprint(p), fp)

    def test_each_kind_of_edit(self):
        edits = [
            lambda p: setattr(p.layers[0], "name", "Railing"),
            lambda p: setattr(p.layers[0], "enabled", False),
            lambda p: p.layers[0].clicks.append(Click(0, 1, 0.5, 0.5, 1)),
            lambda p: p.layers[0].masks.set(1, np.ones((24, 32), bool)),
            lambda p: p.layers[0].keys.set_keyframe(3, "static", {"brightness": 0.1}),
            lambda p: setattr(p.global_settings, "gamma", 1.4),
            lambda p: p.new_layer(24, 32),
            lambda p: p.layers.pop(),
        ]
        for edit in edits:
            p = project()
            fp = H.fingerprint(p)
            edit(p)
            self.assertNotEqual(H.fingerprint(p), fp, edit)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.h = H.History(clock=self.clock)

    def test_undo_then_redo(self):
        p0 = project()
        self.h.push(state(p0, "Push / pull"))
        p1 = project(); p1.layers[0].settings.brightness = 0.3
        back = self.h.undo(state(p1, ""))
        self.assertIs(back.project.layers[0].settings.brightness, p0.layers[0].settings.brightness)
        self.assertTrue(self.h.can_redo and not self.h.can_undo)
        self.assertEqual(self.h.redo_label, "Push / pull", "redo names the edit it puts back")
        fwd = self.h.redo(state(p0, ""))
        self.assertEqual(fwd.project.layers[0].settings.brightness, 0.3)
        self.assertEqual(self.h.undo_label, "Push / pull")

    def test_a_dial_dragged_is_one_step(self):
        p = project()
        for i in range(20):
            self.h.push(state(p, "Contrast"), coalesce="control.contrast")
            self.clock.t += 0.05
        self.assertEqual(len(self.h._undo), 1)
        self.clock.t += H.COALESCE_S + 0.5          # let go, then again later: a new step
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.assertEqual(len(self.h._undo), 2)

    def test_another_dial_is_another_step(self):
        p = project()
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.h.push(state(p, "Flatten"), coalesce="control.flatten")
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.assertEqual([s.label for s in self.h._undo], ["Contrast", "Flatten", "Contrast"])

    def test_undo_breaks_a_run(self):
        p = project()
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.h.undo(state(p, ""))
        self.h.push(state(p, "Contrast"), coalesce="control.contrast")
        self.assertEqual(len(self.h._undo), 1)
        self.assertFalse(self.h.can_redo, "a new edit drops what could be redone")

    def test_limit(self):
        h = H.History(limit=5, clock=self.clock)
        p = project()
        for i in range(12):
            h.push(state(p, f"e{i}"))
        self.assertEqual([s.label for s in h._undo], ["e7", "e8", "e9", "e10", "e11"])

    def test_the_restored_copy_is_its_own(self):
        p = project()
        self.h.push(state(p, "x"))
        back = self.h.undo(state(p, ""))
        restored = back.project.snapshot()        # as the controller restores it
        restored.layers[0].settings.brightness = 0.9
        restored.layers[0].clicks.append(Click(0, 1, 0.1, 0.1, 1))
        self.assertEqual(back.project.layers[0].settings.brightness, 0.0)
        self.assertEqual(back.project.layers[0].clicks, [])

    def test_clear(self):
        p = project()
        self.h.push(state(p, "x"))
        self.h.clear()
        self.assertFalse(self.h.can_undo or self.h.can_redo)
        self.assertEqual((self.h.undo_label, self.h.redo_label), ("", ""))


class BudgetTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): every re-track left the old masks alive in
    its Undo step -- hundreds of MB a re-track on a long clip, kept until
    Load Clip. Steps are capped by the memory only they hold."""

    def retracked(self, p):
        """a re-track: every frame's masks replaced (new arrays, 10 KB each)"""
        for f in range(10):
            p.layers[0].masks._frames[f] = np.full(10_000, f, np.uint8)

    def test_the_oldest_steps_go_past_the_budget(self):
        p = project()
        hist = H.History(clock=Clock(), budget=250_000)
        for k in range(8):
            before = state(p, f"Tracking {k}")
            self.retracked(p)
            hist.push(before, live=(p, {}))
        held = hist.held_bytes(live=(p, {}))
        self.assertLessEqual(held, 250_000)
        self.assertGreater(held, 150_000, "as many steps as fit are kept")
        self.assertEqual(hist.undo_label, "Tracking 7", "the newest step stays")
        self.assertLess(len(hist._undo), 8)

    def test_shared_and_live_arrays_cost_nothing(self):
        p = project()
        self.retracked(p)
        hist = H.History(clock=Clock(), budget=50_000)
        for k in range(20):                       # renames: every step shares the live masks
            hist.push(state(p, f"Rename {k}"), live=(p, {}))
            p.layers[0].name = f"n{k}"
        self.assertEqual(len(hist._undo), 20)
        self.assertEqual(hist.held_bytes(live=(p, {})), 0)

    def test_results_count_too(self):
        from core.depthedit import sam3_backend as sam
        p = project()
        hist = H.History(clock=Clock(), budget=100_000)
        for k in range(5):
            res = {(1, f): sam.pack_result(([1], np.ones((1, 200, 400), bool))) for f in range(3)}
            hist.push(H.State(f"t{k}", p.snapshot(), 0, res))
        self.assertLessEqual(hist.held_bytes(), 100_000)
        self.assertLess(len(hist._undo), 5)


class SaveOrderTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): the layer list was put in place before the
    masks were written; a save failing at the masks (a full disk) left the
    new layers beside the old masks, and they loaded tracked with none."""

    def test_a_failed_masks_write_leaves_the_last_save_whole(self):
        import os
        import tempfile
        from unittest import mock
        from core.depthedit.project import masks_path_for
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = os.path.join(d, "a.depthedit.json")
        p = project()
        p.save(path)
        before = (Path(path).read_bytes(), Path(masks_path_for(path)).read_bytes())
        p.new_layer(24, 32).masks.set(0, np.ones((24, 32), bool))
        with mock.patch("numpy.savez_compressed", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                p.save(path)
        self.assertEqual((Path(path).read_bytes(), Path(masks_path_for(path)).read_bytes()), before)
        self.assertEqual(sorted(os.listdir(d)), sorted([os.path.basename(path), os.path.basename(masks_path_for(path))]),
                         "no temp files left")
        loaded = Project.load(path, 24, 32)
        self.assertEqual(len(loaded.layers), 1)
        self.assertEqual(len(loaded.layers[0].masks), 1)


if __name__ == "__main__":
    unittest.main()
