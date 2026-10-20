"""Keyframes for a Depth Map Editor layer (user, 2026-10-05; core.depthedit.keys):
static keys hold, dynamic keys ramp in from the key before, no keys leave the
settings alone, a change on a key updates it, and the keys save, load and
snapshot with the project.

    python -m unittest studio.tests.test_depthedit_keys
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common.keyframes import KeyframeTrack  # noqa: E402
from core.depthedit import keys as K  # noqa: E402
from core.depthedit.edit_ops import LayerSettings  # noqa: E402
from core.depthedit.project import Project  # noqa: E402


def snap(**kw):
    s = LayerSettings()
    for k, v in kw.items():
        K._set(s, tuple(k.split(".")), v)
    return K.capture(s)


class SettingsAtTests(unittest.TestCase):
    def test_no_keys_is_the_settings_themselves(self):
        s = LayerSettings(brightness=0.2)
        self.assertIs(K.settings_at(s, KeyframeTrack(), 10), s)
        self.assertIs(K.settings_at(s, None, 10), s)

    def test_static_holds_dynamic_ramps(self):
        tr = KeyframeTrack()
        tr.set_keyframe(10, "static", snap(brightness=0.0, add_depth=0.4))
        tr.set_keyframe(20, "dynamic", snap(brightness=0.4, add_depth=0.8))
        tr.set_keyframe(30, "static", snap(brightness=-0.2, add_depth=0.8))
        base = LayerSettings(brightness=0.9, mode="Add")
        at = lambda f: K.settings_at(base, tr, f)   # noqa: E731
        self.assertAlmostEqual(at(0).brightness, 0.0)             # before the first: the first holds
        self.assertAlmostEqual(at(15).brightness, 0.2)            # halfway into the dynamic key
        self.assertAlmostEqual(at(15).add_depth, 0.6)
        self.assertAlmostEqual(at(25).brightness, 0.4)            # the static key ahead doesn't ramp
        self.assertAlmostEqual(at(30).brightness, -0.2)
        self.assertEqual(at(15).mode, "Add")                      # choices aren't keyed
        self.assertAlmostEqual(base.brightness, 0.9)              # the layer's own settings untouched

    def test_gradient_ends_and_whole_pixel_snap(self):
        tr = KeyframeTrack()
        tr.set_keyframe(0, "static", snap(**{"gradient.near": 0.9, "edge_snap": 2}))
        tr.set_keyframe(10, "dynamic", snap(**{"gradient.near": 0.5, "edge_snap": 6}))
        s = K.settings_at(LayerSettings(), tr, 5)
        self.assertAlmostEqual(s.gradient.near, 0.7)
        self.assertEqual(s.edge_snap, 4)
        self.assertIsInstance(s.edge_snap, int)

    def test_a_change_on_a_key_updates_that_key(self):
        tr = KeyframeTrack()
        tr.set_keyframe(5, "dynamic", snap(brightness=0.1))
        self.assertTrue(K.update_key_value(tr, 5, ("brightness",), 0.3))
        self.assertEqual(tr.frame_at(5)["type"], "dynamic")
        self.assertAlmostEqual(tr.frame_at(5)["values"]["brightness"], 0.3)
        self.assertFalse(K.update_key_value(tr, 6, ("brightness",), 0.5))      # not on a key
        self.assertFalse(K.update_key_value(tr, 5, ("mode",), "Add"))          # not keyed


class ProjectTests(unittest.TestCase):
    def test_keys_save_load_and_snapshot(self):
        p = Project(rgb_path="a.mp4", depth_path="a_depth.mp4")
        layer = p.new_layer(24, 32)
        layer.keys.set_keyframe(3, "static", snap(brightness=0.1))
        layer.keys.set_keyframe(9, "dynamic", snap(brightness=0.5))
        snap_p = p.snapshot()
        layer.keys.clear_frame(9)
        self.assertEqual(snap_p.layers[0].keys.sorted_frames(), [3, 9])           # the snapshot keeps its own
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "a_depthedit.json")
            snap_p.save(path)
            q = Project.load(path, 24, 32)
        got = q.layers[0].keys
        self.assertEqual([(f, k) for f, k, _v in got.items_sorted()], [(3, "static"), (9, "dynamic")])
        self.assertAlmostEqual(K.settings_at(q.layers[0].settings, got, 6).brightness, 0.3)

    def test_a_layer_without_keys_saves_no_keyframes(self):
        p = Project()
        p.new_layer(24, 32)
        self.assertNotIn("keyframes", p.layers[0].to_dict())


if __name__ == "__main__":
    unittest.main()
