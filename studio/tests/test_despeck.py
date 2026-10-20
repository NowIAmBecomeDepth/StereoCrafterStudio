"""Despeck (user, 2026-10-12; core.depthedit.despeck): DepthCrafter's bright
specks and glows just inside LEFT edges lowered to the object's own depth, in
the Depth Map Editor's Despeck layers. What it does to a map (synthetic frames:
a speck, a soft glow, a right-edge bump it must leave, a narrow object it must
not carve); a Despeck layer keeps it to its mask, Opacity is how far; Highlight
Specks' figures (what it would take, and what a layer covers).

    python -m unittest studio.tests.test_despeck
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import edit_ops as E  # noqa: E402
from core.depthedit.despeck import despeck  # noqa: E402

W, H = 640, 160
BG, OBJ = 0.10, 0.50


def _scene(left_bump=0.0, right_bump=0.0, soft=False, x0=200, x1=440):
    """A background, an object from x0 to x1, and a bump just inside its left
    or right edge: a hard speck (4 px) or a soft glow (falling over 8 px)."""
    d = np.full((H, W), BG, np.float32)
    d[40:120, x0:x1] = OBJ
    x = np.arange(W, dtype=np.float32)
    r = x1 - 1
    if soft:
        ramp_l = np.clip(1.0 - (x - x0) / 8.0, 0, 1) * (x >= x0)
        ramp_r = np.clip(1.0 - (r - x) / 8.0, 0, 1) * (x <= r)
    else:
        ramp_l = ((x >= x0) & (x < x0 + 4)).astype(np.float32)
        ramp_r = ((x > r - 4) & (x <= r)).astype(np.float32)
    d[60:80] += left_bump * ramp_l + right_bump * ramp_r
    return d


class CoreTests(unittest.TestCase):
    def test_a_hard_left_speck_goes(self):
        d = _scene(left_bump=0.2)
        out = despeck(d, 1.0, device="cpu")
        self.assertAlmostEqual(float(d[70, 201]), OBJ + 0.2, places=5)
        self.assertLess(float(out[70, 200:204].max()), OBJ + 0.02)

    def test_a_soft_left_glow_goes(self):
        d = _scene(left_bump=0.15, soft=True)
        out = despeck(d, 1.0, device="cpu")
        self.assertLess(float(out[70, 200:210].max()), OBJ + 0.02)

    def test_a_wide_glow_fades_out_with_no_step(self):
        """A glow wider than the reach (user, 2026-10-12: "how can we blend these spots
        better"): the result climbs no more steeply along the row than the map did -- cut
        off at the reach, it left a hard step there."""
        d = np.full((H, W), BG, np.float32)
        d[40:120, 200:440] = OBJ
        x = np.arange(W, dtype=np.float32)
        d[40:120] += (0.12 * np.clip(1.0 - (x - 200) / 40.0, 0, 1) * (x >= 200))[None, :]
        out = despeck(d, 1.0, device="cpu")
        row = out[80, 202:300]
        self.assertLess(float(np.abs(np.diff(row)).max()), float(np.abs(np.diff(d[80, 202:300])).max()) + 0.004)
        self.assertLess(float(out[80, 200:204].max()), OBJ + 0.12 - 0.05)              # still lowered

    def test_no_dip_where_the_outline_falls_away(self):
        """A bump at an object's top-left corner, its top edge falling away to the right
        (user, 2026-10-12: "still darkening too much" -- V1's ridge): wherever it is lowered,
        not below the surface. Capped at the depth to its right on its own row, it sank
        about 0.05 into a dip; rebuilt from the surface around it, under 0.03."""
        import cv2
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        m = ((xx >= 200) & (xx < 440) & (yy < 130) & (yy >= 40 + 0.35 * (xx - 200))).astype(np.float32)
        surf = cv2.GaussianBlur(BG + (0.6 - BG) * m, (0, 0), 1.5)
        d = surf.copy()
        bump = (xx >= 201) & (xx < 207) & (yy >= 42) & (yy < 52)
        d[bump] += 0.15
        out = despeck(d, 1.0, device="cpu")
        self.assertGreater(float((out - surf)[m > 0.5].min()), -0.03 * 3)               # nowhere deep on the object
        self.assertGreater(float((out - surf)[bump & (m > 0.5)].min()), -0.03)           # no dip at the bump
        self.assertLess(float((out - d)[bump].mean()), -0.02)                             # and it did come down

    def test_a_right_edge_bump_is_left_alone(self):
        d = _scene(right_bump=0.2)
        out = despeck(d, 1.0, device="cpu")
        self.assertLess(float(np.abs(out[:, 300:] - d[:, 300:]).max()), 1e-6)

    def test_the_rest_of_the_map_is_untouched(self):
        d = _scene(left_bump=0.2)
        out = despeck(d, 1.0, device="cpu")
        far = np.ones_like(d, bool)
        far[50:90, 190:230] = False                     # the speck and its feather
        self.assertLess(float(np.abs(out - d)[far].max()), 1e-6)

    def test_it_only_ever_lowers(self):
        d = _scene(left_bump=0.2, right_bump=0.1, soft=True)
        self.assertTrue(bool((despeck(d, 1.0, device="cpu") <= d + 1e-7).all()))

    def test_a_narrow_object_is_not_capped_to_the_background(self):
        """A head 10 px wide: the interior samples past it are background, so
        it keeps its depth (an early version carved its left half away)."""
        d = np.full((H, W), BG, np.float32)
        d[40:120, 300:310] = OBJ
        out = despeck(d, 1.0, device="cpu")
        self.assertGreater(float(out[40:120, 300:310].min()), OBJ - 0.02)

    def test_amount(self):
        d = _scene(left_bump=0.2)
        self.assertIs(despeck(d, 0.0), d)
        full = d - despeck(d, 1.0, device="cpu")
        half = d - despeck(d, 0.5, device="cpu")
        np.testing.assert_allclose(half, full * 0.5, atol=1e-6)

    def test_a_batch_is_each_frame(self):
        a, b = _scene(left_bump=0.2), _scene(left_bump=0.1, soft=True)
        out = despeck(np.stack([a, b]), 1.0, device="cpu")
        np.testing.assert_allclose(out[1], despeck(b, 1.0, device="cpu"), atol=1e-6)

    @unittest.skipUnless(__import__("torch").cuda.is_available(), "needs CUDA")
    def test_the_gpu_agrees(self):
        d = _scene(left_bump=0.2, soft=True)
        np.testing.assert_allclose(despeck(d, 1.0, device="cuda"), despeck(d, 1.0, device="cpu"), atol=1e-5)


def _two_objects():
    """Two objects with a left-edge speck each: one at x 100, one at x 400."""
    d = _scene(left_bump=0.2, x0=100, x1=250)
    e = _scene(left_bump=0.2, x0=400, x1=550)
    return np.maximum(d, e)


def _mask(x0, x1):
    m = np.zeros((H, W), bool)
    m[40:120, x0:x1] = True
    return m


def _despeck_layer(**kw):
    """An Alter layer with Despeck on."""
    return E.LayerSettings(**{"mode": "Alter", "despeck_on": True, "edge_snap": 0, "feather": 0.0, **kw})


class LayerTests(unittest.TestCase):
    """Alter's Despeck: the speck finder kept to the layer's mask."""

    def test_an_alter_layer_has_it_off(self):
        v = _two_objects()
        s = E.LayerSettings(edge_snap=0, feather=0.0)
        self.assertFalse(s.despeck_on)
        np.testing.assert_allclose(E.apply_layer(v, _mask(100, 250), s), v)
        self.assertFalse(E.layer_settings_from_dict({"mode": "Alter"}).despeck_on)   # nor does an old project

    def test_only_in_alter(self):
        """The switch is Alter's: set, it stays with the layer through Add or Erase and back
        (user, 2026-10-12: as a mode of its own it went off at the switch back to Alter)."""
        v = _two_objects()
        s = _despeck_layer()
        self.assertTrue(E.has_despeck(s))
        self.assertFalse(E.has_despeck(_despeck_layer(mode="Add")))
        self.assertFalse(E.has_despeck(_despeck_layer(mode="Erase")))
        s.mode = "Add"; s.mode = "Alter"
        self.assertLess(float(E.apply_layer(v, _mask(100, 250), s)[70, 100:104].max()), OBJ + 0.02)

    def test_inside_the_mask_the_speck_goes_outside_it_stays(self):
        v = _two_objects()
        out = E.apply_layer(v, _mask(100, 250), _despeck_layer())
        self.assertLess(float(out[70, 100:104].max()), OBJ + 0.02)           # covered
        self.assertAlmostEqual(float(out[70, 401]), OBJ + 0.2, places=5)     # not covered
        self.assertLess(float(np.abs(out - v)[:, 300:].max()), 1e-6)

    def test_a_mask_that_stops_just_short_of_the_edge_still_takes_it(self):
        v = _two_objects()
        out = E.apply_layer(v, _mask(101, 250), _despeck_layer())         # a pixel short
        self.assertLess(float(out[70, 100:104].max()), OBJ + 0.02)

    def test_strength_is_how_far(self):
        v = _two_objects()
        full = v - E.apply_layer(v, _mask(100, 250), _despeck_layer())
        half = v - E.apply_layer(v, _mask(100, 250), _despeck_layer(despeck_strength=0.5))
        np.testing.assert_allclose(half, full * 0.5, atol=1e-6)
        np.testing.assert_allclose(E.apply_layer(v, _mask(100, 250), _despeck_layer(despeck_strength=0.0)), v)

    def test_the_switch(self):
        v = _two_objects()
        np.testing.assert_allclose(E.apply_layer(v, _mask(100, 250), _despeck_layer(despeck_on=False)), v)
        took, covered = E.speck_highlight(v, [(_despeck_layer(despeck_on=False), _mask(100, 250))], 0)
        self.assertGreater(float(took[70, 101]), 0.15)                       # still found ...
        self.assertFalse(bool(covered.any()))                                 # ... and shown uncovered

    def test_a_layer_saved_in_the_despeck_mode_opens_as_alter_with_it_on(self):
        s = E.layer_settings_from_dict({"mode": "Despeck"})
        self.assertEqual((s.mode, s.despeck_on, s.despeck_strength), ("Alter", True, 1.0))

    def test_with_the_rest_of_alter_it_goes_first(self):
        """Brightness on top of Despeck: the speck comes down to the object, then the
        whole object (speck spot and all) moves together."""
        v = _two_objects()
        out = E.apply_layer(v, _mask(100, 250), _despeck_layer(brightness=0.1))
        self.assertAlmostEqual(float(out[70, 180]), OBJ + 0.1, places=4)
        self.assertLess(abs(float(out[70, 100:104].max()) - (OBJ + 0.1)), 0.02)

    def test_the_frame_range(self):
        v = _two_objects()
        s = _despeck_layer(start_frame=5, end_frame=9)
        out = E.apply_all(v, E.GlobalSettings(), [(s, _mask(100, 250))], 2)
        np.testing.assert_allclose(out, v)

    def test_highlight_figures(self):
        v = _two_objects()
        layers = [(_despeck_layer(), _mask(100, 250)), (E.LayerSettings(), _mask(400, 550))]
        took, covered = E.speck_highlight(v, layers, 0)
        self.assertGreater(float(took[70, 101]), 0.15)                       # both specks found
        self.assertGreater(float(took[70, 401]), 0.15)
        self.assertTrue(bool(covered[70, 101]))                               # only the Despeck layer covers
        self.assertFalse(bool(covered[70, 401]))
        self.assertLess(float(took[:30].max()), 1e-6)                         # nothing where there are no specks


def _particle(core=0.75, body=0.4):
    """A small round particle (radius 12) at `body` with a dot (radius 5) at `core` in it,
    on a background of BG; and its mask."""
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx - 300, yy - 80)
    d = np.full((H, W), BG, np.float32)
    d[r <= 12] = body
    d[r <= 5] = core
    return d, r <= 12


class LevelEdgeTests(unittest.TestCase):
    """Level to edge (user, 2026-10-12): "the particle should match the level of grey its outer
    edges are when it's a clear dot of white inside it. Particles that are actually supposed
    to be near will be white throughout."""""

    def _layer(self, **kw):
        return E.LayerSettings(**{"level_edge": True, "edge_snap": 0, "feather": 0.0, **kw})

    def test_a_white_dot_comes_down_to_its_edge_grey(self):
        d, m = _particle()
        out = E.apply_layer(d, m, self._layer())
        self.assertAlmostEqual(float(out[m].max()), 0.4, places=4)
        np.testing.assert_allclose(out[~m], d[~m])

    def test_white_throughout_stays(self):
        d, m = _particle(core=0.75, body=0.75)
        np.testing.assert_allclose(E.apply_layer(d, m, self._layer()), d)

    def test_nothing_darker_moves(self):
        d, m = _particle(core=0.2, body=0.4)                 # a dark dot: left as it is
        np.testing.assert_allclose(E.apply_layer(d, m, self._layer()), d)

    def test_strength_and_off(self):
        d, m = _particle()
        half = E.apply_layer(d, m, self._layer(despeck_strength=0.5))
        self.assertAlmostEqual(float(half[m].max()), 0.4 + 0.35 / 2, places=4)
        np.testing.assert_allclose(E.apply_layer(d, m, self._layer(level_edge=False)), d)
        self.assertFalse(E.LayerSettings().level_edge)
        self.assertFalse(E.has_level_edge(self._layer(mode="Add")))

    def test_a_few_background_pixels_on_the_ring_dont_move_it(self):
        d, m = _particle()
        loose = m.copy(); loose[80, 312:315] = True              # a nub of mask out on the background
        self.assertAlmostEqual(E.edge_level(d, loose), 0.4, places=4)

    def test_highlight_shows_it_found_and_covered(self):
        d, m = _particle()
        took, covered = E.speck_highlight(d, [(self._layer(), m)], 0)
        self.assertGreater(float(took[80, 300]), 0.3)
        self.assertTrue(bool(covered[80, 300]))
        self.assertFalse(bool(covered[80, 310]))                  # the grey body: nothing taken


class GlobalTests(unittest.TestCase):
    """The Global Despeck (user, 2026-10-12): the whole frame, no layer."""

    def test_off_by_default(self):
        v = _two_objects()
        g = E.GlobalSettings()
        self.assertFalse(g.despeck_on)
        self.assertTrue(g.is_identity())
        self.assertIs(E.apply_global(v, g), v)
        self.assertFalse(E.global_settings_from_dict({}).despeck_on)             # an old project

    def test_on_every_speck_in_the_frame_goes(self):
        v = _two_objects()
        out = E.apply_all(v, E.GlobalSettings(despeck_on=True), [], 0)
        self.assertLess(float(out[70, 100:104].max()), OBJ + 0.02)
        self.assertLess(float(out[70, 400:404].max()), OBJ + 0.02)
        np.testing.assert_allclose(out, despeck(v, 1.0), atol=1e-6)

    def test_strength_and_first_before_invert(self):
        v = _two_objects()
        half = E.apply_global(v, E.GlobalSettings(despeck_on=True, despeck_strength=0.5))
        np.testing.assert_allclose(v - half, (v - despeck(v, 1.0)) * 0.5, atol=1e-6)
        inv = E.apply_global(v, E.GlobalSettings(despeck_on=True, invert=True))
        np.testing.assert_allclose(inv, 1.0 - despeck(v, 1.0), atol=1e-6)        # found on the map as it is

    def test_threshold_judges_each_spot_whole(self):
        """Threshold (user, 2026-10-12): a spot is taken when its highest point stands that far above
        its object, all of it -- not pixel by pixel, where a fringe and gentle shading look alike."""
        big = _scene(left_bump=0.2)                              # stands 0.2 above
        small = _scene(left_bump=0.03, soft=True)                # 0.03, a gentle glow
        self.assertLess(float(despeck(big, 1.0, device="cpu", threshold=0.1)[70, 200:204].max()), OBJ + 0.02)
        took0 = small - despeck(small, 1.0, device="cpu")
        self.assertGreater(float(took0.max()), 0.01)                                  # taken at 0
        np.testing.assert_allclose(despeck(small, 1.0, device="cpu", threshold=0.1), small)   # left at 10%

    def test_reach_and_edge(self):
        x = np.arange(W, dtype=np.float32)
        d = np.full((H, W), BG, np.float32)
        d[40:120, 200:440] = OBJ
        d[40:120] += (0.12 * np.clip(1.0 - (x - 200) / 40.0, 0, 1) * (x >= 200))[None, :]    # a wide glow
        near = float((d - despeck(d, 1.0, device="cpu", reach=0.005))[80, 200:260].sum())
        far = float((d - despeck(d, 1.0, device="cpu", reach=0.05))[80, 200:260].sum())
        self.assertGreater(far, near * 1.5)                                          # Reach: further in
        # Edge: a step inside an object (one part over another, 0.2) with a bright rim
        e = np.full((H, W), BG, np.float32)
        e[40:120, 100:440] = 0.4
        e[40:120, 250:440] = 0.6
        e[60:80, 250:254] += 0.15
        self.assertLess(float(despeck(e, 1.0, device="cpu")[70, 250:254].max()), 0.65)         # 4%: an edge
        np.testing.assert_allclose(despeck(e, 1.0, device="cpu", step=0.4)[:, 240:300], e[:, 240:300])   # 40%: not

    def test_the_global_dials_reach_it(self):
        v = _two_objects()
        g = E.GlobalSettings(despeck_on=True, despeck_threshold=0.05, despeck_reach=0.03, despeck_step=0.06)
        np.testing.assert_allclose(E.apply_global(v, g), despeck(v, 1.0, threshold=0.05, reach=0.03, step=0.06), atol=1e-6)
        self.assertEqual((E.GlobalSettings().despeck_threshold, E.GlobalSettings().despeck_reach,
                          E.GlobalSettings().despeck_step), (0.0, 0.02, 0.04))     # as it was

    def test_the_zone_keeps_it_inside_its_lines(self):
        """Left / Right / Top / Bottom (user, 2026-10-12): outside the zone the map is left alone,
        eased in across each line."""
        v = _two_objects()                                        # specks at x 100 and 400 of 640
        g = E.GlobalSettings(despeck_on=True, despeck_left=0.5)
        out = E.apply_global(v, g)
        self.assertLess(float(out[70, 400:404].max()), OBJ + 0.02)              # inside: down
        np.testing.assert_allclose(out[:, :300], v[:, :300])                  # outside: as it was
        self.assertIsNone(E.GlobalSettings().despeck_zone())                    # whole frame by default
        w = E.zone_weight((0.0, 0.25, 1.0, 0.75), (H, W))
        self.assertEqual((float(w[0, 0]), float(w[H // 2, 0]), float(w[H - 1, 0])), (0.0, 1.0, 0.0))
        col = w[:, 0]
        self.assertLess(float(np.abs(np.diff(col)).max()), 0.5)                 # eased, not cut
        took, covered = E.speck_highlight(v, [], 0, whole=True, global_args=dict(g.despeck_args(), zone=g.despeck_zone()))
        self.assertTrue(bool(covered[70, 401]) and not bool(covered[70, 101]))     # outside: still to cover

    def test_highlight_shows_them_all_covered(self):
        took, covered = E.speck_highlight(_two_objects(), [], 0, whole=True)
        self.assertGreater(float(took[70, 401]), 0.15)
        self.assertTrue(bool(covered.all()))


if __name__ == "__main__":
    unittest.main()
