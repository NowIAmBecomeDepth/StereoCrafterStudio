"""Video Depth Anything (DepthCrafter's VDA tab; user, 2026-10-25): the parts that need no card.

* sizes: a multiple of 14 to run at, a multiple of 16 wide to write (FFmpeg 8's swscale ticks);
* the depth range: the whole clip's, or iw3's EMA with its lookahead; codes 64..940 as DepthCrafter's;
* iw3's sign-safe window fit: a negative scale falls back to one that keeps near near;
* the map's place: a DepthCrafter map of the clip moves into DepthCrafter/ (user: "have it make a
  folder to place the dc map in"), VDA's own earlier map is replaced;
* settings coerce, and the Models catalog rows.

    python -m unittest studio.tests.test_vda
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.vda import model as M  # noqa: E402
from core.vda import normalize as N  # noqa: E402
from core.vda import output as OUT  # noqa: E402
from studio.stages.vda import settings as S  # noqa: E402


def _clip(path: str, comment: str = "") -> None:
    tags = ["-metadata", f"comment={comment}"] if comment else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x32:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", *tags, path], check=True)


class SizesTest(unittest.TestCase):
    def test_input_snaps_up_to_14(self):
        self.assertEqual(M.snap_input(512), 518)
        self.assertEqual(M.snap_input(518), 518)
        self.assertEqual(M.snap_input(392), 392)
        self.assertEqual(M.snap_input("x"), M.DEFAULT_INPUT)
        self.assertEqual(M.snap_input(10), 196)

    def test_run_size_keeps_the_shape_in_multiples_of_14(self):
        h, w = M.infer_size(800, 1920, 518)
        self.assertEqual((h % 14, w % 14), (0, 0))
        self.assertEqual(h, 518)
        self.assertAlmostEqual(w / h, 1920 / 800, delta=0.05)
        h, w = M.infer_size(1920, 1080, 518)           # portrait: the short side is the width
        self.assertEqual(w, 518)

    def test_very_wide_is_cut_to_four_to_one(self):
        h, w = M.infer_size(100, 1000, 392)
        self.assertLessEqual(w, 4 * h)

    def test_map_width_is_never_2_to_8_past_16(self):
        for width in range(196, 2000, 14):              # every width VDA can run at
            h, w = M.output_size(518, width)
            self.assertEqual(w % 16, 0, width)
            self.assertEqual(h % 2, 0)


class RangeTest(unittest.TestCase):
    def test_whole_clip_is_one_range(self):
        self.assertEqual(N.whole_clip([1, 0.5, 2], [3, 4, 2.5]), [(0.5, 4)] * 3)

    def test_ema_matches_iw3(self):
        # iw3's EMAMinMaxScaler, by hand: buffer 2, decay 0.5 over 4 frames
        lows, highs = [0, 1, 2, 3], [10, 11, 12, 13]
        got = N.ema(lows, highs, decay=0.5, buffer=2)
        # frame 0: window 0..1 -> (0, 11); frame 1: (1, 12) eased -> (0.5, 11.5); frame 2: (2, 13) -> (1.25, 12.25)
        # frame 3: no full lookahead left -> as it stands
        self.assertEqual(got, [(0, 11), (0.5, 11.5), (1.25, 12.25), (1.25, 12.25)])

    def test_ema_shorter_than_its_buffer_takes_the_whole_clip(self):
        self.assertEqual(N.ema([1, 2], [5, 6], buffer=30), [(1, 6), (1, 6)])

    def test_codes_as_depthcrafters(self):
        k, off = N.scale(2.0, 6.0)
        self.assertAlmostEqual(2.0 * k + off, 64)
        self.assertAlmostEqual(6.0 * k + off, 940)
        self.assertEqual(N.scale(3.0, 3.0), (0.0, 502.0))     # a flat frame: mid grey, not a division by 0


class SignSafeTest(unittest.TestCase):
    def test_a_negative_fit_keeps_the_sign(self):
        import torch
        fit = M._sign_safe_fit(lambda p, t, m=None, s=False: (-2.0, 1.0))
        p = torch.tensor([1.0, 2.0, 3.0])
        scale, shift = fit(p, p * 2 + 1)
        self.assertGreater(scale, 0)
        self.assertAlmostEqual(scale, 2.0, places=4)
        self.assertAlmostEqual(shift, 1.0, places=4)

    def test_a_good_fit_is_left_alone(self):
        fit = M._sign_safe_fit(lambda p, t, m=None, s=False: (0.5, 3.0))
        self.assertEqual(fit(None, None), (0.5, 3.0))

    def test_numpy_from_the_offline_loop(self):
        import numpy as np
        fit = M._sign_safe_fit(lambda p, t, m=None, s=False: (-1.0, 0.0))
        p = np.array([1.0, 2.0, 3.0], np.float32)
        scale, _shift = fit(p, p * 3, np.ones(3, bool))
        self.assertAlmostEqual(scale, 3.0, places=4)


class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vda-place-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_depthcrafter_map_moves_into_its_folder(self):
        final = OUT.map_path(self.tmp, "clipA")
        _clip(final)                                        # DepthCrafter's: no tag
        part = final + ".part"
        _clip(os.path.join(self.tmp, "new.mp4"), OUT.TAG)
        os.replace(os.path.join(self.tmp, "new.mp4"), part)
        moved = OUT.publish(part, final)
        self.assertEqual(os.path.normcase(moved), os.path.normcase(os.path.join(self.tmp, "DepthCrafter", "clipA_depth.mp4")))
        self.assertTrue(os.path.isfile(moved))
        self.assertTrue(OUT.is_vda_map(final))
        self.assertFalse(os.path.exists(part))

    def test_vdas_own_map_is_replaced_not_kept(self):
        final = OUT.map_path(self.tmp, "clipA")
        _clip(final, OUT.TAG)
        self.assertIsNone(OUT.set_aside(final))
        self.assertTrue(os.path.isfile(final))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "DepthCrafter")))

    def test_a_second_depthcrafter_map_doesnt_overwrite_the_first(self):
        final = OUT.map_path(self.tmp, "clipA")
        _clip(final)
        first = OUT.set_aside(final)
        _clip(final)
        second = OUT.set_aside(final)
        self.assertNotEqual(os.path.normcase(first), os.path.normcase(second))
        self.assertTrue(os.path.isfile(first) and os.path.isfile(second))

    def test_nothing_there(self):
        self.assertIsNone(OUT.set_aside(OUT.map_path(self.tmp, "none")))


class SettingsTest(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(S.coerce("input_size", "512"), 518)
        self.assertEqual(S.coerce("model_size", "Huge"), S.DEFAULTS["model_size"])
        self.assertEqual(S.coerce("run_mode", "Offline"), "Offline")
        self.assertEqual(S.coerce("ema_decay", 5), 0.999)
        self.assertEqual(S.coerce("ema_buffer", 0), 1)
        self.assertIs(S.coerce("use_fp16", "false"), False)

    def test_own_folders_under_depthcrafters_keys(self):
        # the clip list, Send to Splatting and chapters read them as DepthCrafter's
        self.assertIn("input_dir_or_file_var", S.DEFAULTS)
        self.assertIn("output_dir", S.DEFAULTS)

    def test_resume_as_depthcrafters(self):
        opts = S.snapshot({})
        self.assertTrue(S.moves_finished(opts, single=False, test=False))
        self.assertFalse(S.moves_finished(opts, single=True, test=False))
        self.assertFalse(S.moves_finished(opts, single=False, test=True))


class CatalogTest(unittest.TestCase):
    def test_a_row_per_size_into_the_vda_folder(self):
        from studio import model_catalog as C
        for size, key in (("Small", "vda_s"), ("Base", "vda_b"), ("Large", "vda_l")):
            part = C.PARTS[key]
            self.assertEqual(part.folder, M.FOLDER)
            self.assertEqual([f for f, _n in part.files], [M.checkpoint_name(size)])
            self.assertIn(key, C.MODELS_BY_KEY)
            self.assertFalse(C.MODELS_BY_KEY[key].gated)


if __name__ == "__main__":
    unittest.main()
