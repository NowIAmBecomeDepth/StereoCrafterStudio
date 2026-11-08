"""DVD (Depth Map's DVD tab; user, 2026-11-07): the parts that need no card.

* sizes: the short side at Input Size, both sides multiples of 16; windows 4n+1, up to 81;
* the windows as upstream's get_window_index lays them, the last pulled back to end with the clip;
* the stitch: each window fitted to the last over their shared frames (scale held to 0.7..1.5) and
  cross-faded in; every frame comes out once, in order -- a window at a time gives what the whole clip
  at once (upstream's generate_depth_sliced) gives;
* the LoRA merged into the DiT's weights as they load (peft's merge, without peft);
* the map's place: DVD's tag, a DepthCrafter or VDA map moved into its own folder, DVD's own replaced;
  Splatting's list tells a DVD map from a VDA one;
* settings coerce (the overlap kept under the window), and the Models catalog row.

    python -m unittest studio.tests.test_dvd
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
import numpy as np  # noqa: E402
import torch  # noqa: E402

from core.dvd import model as M  # noqa: E402
from core.vda import output as OUT  # noqa: E402
from studio.stages.dvd import settings as S  # noqa: E402


def _clip(path: str, comment: str = "") -> None:
    tags = ["-metadata", f"comment={comment}"] if comment else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x32:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", *tags, path], check=True)


def _upstream_sliced(windows, results, scale_only=False):
    """test_single_video.py's generate_depth_sliced, the alignment half, on numpy (T, H, W) windows."""
    out, prev_end = None, None
    for i, (t, (start, end)) in enumerate(zip(results, windows)):
        t = t[None, ..., None]                    # upstream's (B, T, H, W, C)
        if i == 0:
            out, prev_end = t, end
            continue
        ov = prev_end - start
        ref, cur = out[:, -ov:], t[:, :ov]
        a00, a01, a11 = np.sum(cur * cur), np.sum(cur), np.sum(np.ones_like(ref))
        b0, b1 = np.sum(cur * ref), np.sum(ref)
        det = a00 * a11 - a01 * a01
        scale, shift = ((a11 * b0 - a01 * b1) / det, (-a01 * b0 + a00 * b1) / det) if det != 0 else (1.0, 0.0)
        scale = np.clip(scale, 0.7, 1.5)
        al = t * scale + shift
        al[al < 0] = 0
        alpha = np.linspace(0, 1, ov, dtype=np.float32).reshape(1, ov, 1, 1, 1)
        out = np.concatenate([out[:, :-ov], (1 - alpha) * ref + alpha * al[:, :ov], al[:, ov:]], axis=1)
        prev_end = end
    return out[0, ..., 0]


class SizesTest(unittest.TestCase):
    def test_input_snaps_up_to_16(self):
        self.assertEqual(M.snap_input(480), 480)
        self.assertEqual(M.snap_input(481), 496)
        self.assertEqual(M.snap_input(10), 256)
        self.assertEqual(M.snap_input("junk"), M.DEFAULT_INPUT)

    def test_run_size_short_side_at_input_size_multiples_of_16(self):
        self.assertEqual(M.infer_size(800, 1920, 480), (480, 1152))     # measured on a 2.4:1 clip
        self.assertEqual(M.infer_size(1080, 1920, 480), (480, 864))     # 853.3 -> 864
        h, w = M.infer_size(1920, 1080, 480)                             # portrait: the width is the short side
        self.assertEqual((h % 16, w % 16, w), (0, 0, 480))

    def test_width_as_billys_gui(self):
        # Early Downsample: his read_video_early_downsample -- 1920x800 at Width 640 -> 640x272
        self.assertEqual(M.infer_size(800, 1920, 480, 640, True), (272, 640))
        self.assertEqual(M.infer_size(1080, 1920, 480, 640, True), (368, 640))     # 360 -> 368
        # off: his resize_for_training_scale -- max(480 / short, 640 / W): wide clips as before...
        self.assertEqual(M.infer_size(800, 1920, 480, 640), (480, 1152))
        self.assertEqual(M.infer_size(1080, 1920, 480, 640), (480, 864))
        # ...a clip narrower than 4:3 made 640 wide
        self.assertEqual(M.infer_size(1080, 1080, 480, 640), (640, 640))
        self.assertEqual(M.infer_size(1920, 1080, 480, 640), (1152, 640))
        self.assertEqual(M.snap_width(600), 608)
        self.assertEqual(M.snap_width(10), 256)

    def test_window_9_to_81_as_billys_gui(self):
        self.assertEqual(M.snap_window(81), 81)
        self.assertEqual(M.snap_window(100), 81)
        self.assertEqual(M.snap_window(30), 30)
        self.assertEqual(M.snap_window(3), 9)


class WindowsTest(unittest.TestCase):
    def test_upstream_layout(self):
        self.assertEqual(M.windows(48, 25, 9), [(0, 25), (16, 41), (23, 48)])
        self.assertEqual(M.windows(120, 41, 9), [(0, 41), (32, 73), (64, 105), (79, 120)])
        self.assertEqual(M.windows(30, 81, 9), [(0, 30)])

    def test_every_frame_covered_each_window_starts_in_the_last(self):
        for total, win, ov in ((200, 81, 9), (82, 81, 9), (500, 41, 11), (90, 25, 24)):
            ws = M.windows(total, win, ov)
            self.assertEqual(ws[0][0], 0)
            self.assertEqual(ws[-1][1], total)
            for (s0, e0), (s1, e1) in zip(ws, ws[1:]):
                self.assertTrue(s0 < s1 < e0 <= e1, (total, win, ov, ws))


class StitchTest(unittest.TestCase):
    def _run(self, total, win, ov, seed=0):
        g = torch.Generator().manual_seed(seed)
        ws = M.windows(total, win, ov)
        # each window: the same scene at its own scale and shift, plus noise -- what joins them must undo
        base = torch.rand(total, 4, 6, generator=g)
        res = []
        for i, (s, e) in enumerate(ws):
            res.append(base[s:e] * (1.0 + 0.1 * i) + 0.05 * i + 0.01 * torch.rand(e - s, 4, 6, generator=g))
        got = []
        st = M.Stitcher(lambda d: got.append(d.clone()))
        for (s, e), r in zip(ws, res):
            st.add(s, e, r)
        st.finish()
        return ws, res, torch.stack(got)

    def test_each_frame_out_once_in_order(self):
        for total, win, ov in ((48, 25, 9), (200, 81, 9), (30, 81, 9), (121, 41, 11)):
            _, _, out = self._run(total, win, ov)
            self.assertEqual(out.shape[0], total)

    def test_matches_upstream_whole_clip_alignment(self):
        for total, win, ov in ((48, 25, 9), (200, 81, 9), (121, 41, 11)):
            ws, res, out = self._run(total, win, ov, seed=total)
            ref = _upstream_sliced(ws, [r.double().numpy() for r in res])
            np.testing.assert_allclose(out.double().numpy(), ref, rtol=1e-4, atol=1e-5)

    def test_scale_held_to_upstreams_clip(self):
        cur = torch.linspace(0, 1, 50)
        self.assertEqual(M.fit(cur, cur * 10)[0], M.SCALE_CLIP[1])
        self.assertEqual(M.fit(cur, cur * 0.1)[0], M.SCALE_CLIP[0])
        self.assertEqual(M.fit(torch.zeros(10), torch.ones(10)), (1.0, 0.0))   # flat: no fit

    def test_run_clip_stops_and_reports_windows(self):
        seen = []
        m = type("Fake", (), {})()
        orig = M.run_window
        M.run_window = lambda _m, f: f[:, 0]
        try:
            n = M.run_clip(m, lambda a, b: torch.rand(b - a, 3, 4, 4), 48, 25, 9, lambda d: None,
                           stopped=lambda: len(seen) >= 1, on_window=lambda i, e: seen.append((i, e)))
        finally:
            M.run_window = orig
        self.assertEqual(seen, [(0, 25)])
        self.assertEqual(n, 0)       # stopped before the stitch could hand any frame on


class LoraMergeTest(unittest.TestCase):
    def test_base_plus_b_at_a(self):
        g = torch.Generator().manual_seed(1)
        w = torch.randn(6, 4, generator=g).to(torch.bfloat16)
        a = torch.randn(3, 4, generator=g).to(torch.bfloat16)
        b = torch.randn(6, 3, generator=g).to(torch.bfloat16)
        bias = torch.randn(6, generator=g).to(torch.bfloat16)
        state = {"pipe.dit.blocks.0.self_attn.q.base_layer.weight": w,
                 "pipe.dit.blocks.0.self_attn.q.base_layer.bias": bias,
                 "pipe.dit.blocks.0.self_attn.q.lora_A.default.weight": a,
                 "pipe.dit.blocks.0.self_attn.q.lora_B.default.weight": b,
                 "pipe.dit.blocks.0.norm3.weight": bias,
                 "pipe.vae.model.conv1.weight": w}
        out = M._merged_dit_state(state, "cpu")
        self.assertEqual(sorted(out), ["blocks.0.norm3.weight", "blocks.0.self_attn.q.bias", "blocks.0.self_attn.q.weight"])
        want = (w.float() + b.float() @ a.float()).to(torch.bfloat16)
        self.assertTrue(torch.equal(out["blocks.0.self_attn.q.weight"], want))
        self.assertEqual(out["blocks.0.self_attn.q.weight"].dtype, torch.bfloat16)


class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kinds_by_tag(self):
        kinds = [("dvd", OUT.DVD_TAG), ("vda", OUT.TAG), ("dc", "")]
        if hasattr(OUT, "BLEND_TAG"):                    # Blend: the user's own copy, not released
            kinds.append(("blend", OUT.BLEND_TAG))
        for kind, tag in kinds:
            p = os.path.join(self.tmp, f"{kind}.mp4")
            _clip(p, tag)
            self.assertEqual(OUT.kind_of(p), kind)

    def test_a_dvd_map_moves_a_depthcrafter_and_a_vda_map_into_their_folders(self):
        final = OUT.map_path(self.tmp, "clipA")
        _clip(final)                                     # DepthCrafter's
        part = os.path.join(self.tmp, "new.mp4")
        _clip(part, OUT.DVD_TAG)
        moved = OUT.publish(part, final, new_kind="dvd")
        self.assertEqual(moved, os.path.join(self.tmp, "DepthCrafter", "clipA_depth.mp4"))
        self.assertEqual(OUT.kind_of(final), "dvd")
        _clip(part, OUT.TAG)                             # then VDA over DVD's: DVD's is kept in DVD/
        moved = OUT.publish(part, final, new_kind="vda")
        self.assertEqual(moved, os.path.join(self.tmp, "DVD", "clipA_depth.mp4"))
        _clip(part, OUT.DVD_TAG)                         # DVD over VDA's: VDA's into VDA/
        self.assertEqual(OUT.publish(part, final, new_kind="dvd"), os.path.join(self.tmp, "VDA", "clipA_depth.mp4"))
        _clip(part, OUT.DVD_TAG)                         # DVD over its own: replaced
        self.assertIsNone(OUT.publish(part, final, new_kind="dvd"))

    def test_splattings_list_tells_dvd_from_vda(self):
        from studio.clip_queue import _map_kinds
        paths = []
        for name, tag in (("a", OUT.DVD_TAG), ("b", OUT.TAG), ("c", "")):
            p = os.path.join(self.tmp, f"{name}_depth.mp4")
            _clip(p, tag)
            paths.append(p)
        paths.append(os.path.join(self.tmp, "missing_depth.mp4"))
        self.assertEqual(_map_kinds(paths, {}), ["dvd", "vda", "dc", ""])


class SettingsTest(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(S.coerce("input_size", "500"), 512)
        self.assertEqual(S.coerce("window", 50), 50)
        self.assertEqual(S.coerce("width", "700"), 704)
        self.assertIs(S.coerce("early_downsample", "true"), True)
        self.assertEqual(S.WINDOWS, [9, 18, 27, 36, 45, 54, 63, 72, 81])
        self.assertIs(S.DEFAULTS["early_downsample"], False)
        self.assertEqual(S.coerce("overlap", -3), 0)
        self.assertEqual(S.coerce("normalise_mode", "nope"), "Whole clip")

    def test_overlap_kept_under_the_window(self):
        self.assertEqual(S.normalise({"window": 25, "overlap": 40})["overlap"], 24)

    def test_menus_hold_only_what_the_model_takes(self):
        self.assertTrue(all(v == M.snap_input(v) for v in S.INPUT_SIZES))
        self.assertTrue(all(v == M.snap_window(v) for v in S.WINDOWS))
        self.assertIn(M.DEFAULT_INPUT, S.INPUT_SIZES)
        self.assertIn(M.DEFAULT_WINDOW, S.WINDOWS)

    def test_own_folders_under_depthcrafters_keys(self):
        self.assertIn("input_dir_or_file_var", S.DEFAULTS)
        self.assertIn("output_dir", S.DEFAULTS)


class CatalogTest(unittest.TestCase):
    def test_one_row_into_the_dvd_folder(self):
        from studio import model_catalog as C
        row = next(m for m in C.MODELS if m.key == "dvd")
        part = row.parts[0]
        self.assertEqual(part.folder, M.FOLDER)
        self.assertEqual([f for f, _n in part.files], [M.CHECKPOINT])
        self.assertIn("non-commercial", row.note)



class TiledVaeTest(unittest.TestCase):
    """The VAE by tiles (core.dvd.model, tile_grid / _encode_tiled / _decode_tiled): a stand-in VAE that works
    pixel by pixel gives back exactly what it gives on whole frames, so only the real VAE's edges differ."""

    class _Vae:
        def encode(self, x, scale):           # (1, 3, T, H, W) -> (1, 16, T, H/8, W/8): 8x8 means
            return torch.nn.functional.avg_pool3d(x.float(), (1, 8, 8)).repeat(1, 6, 1, 1, 1)[:, :16]

        def decode(self, z, scale):           # (1, 16, t, h, w) -> (1, 3, t, 8h, 8w)
            return z[:, :3].repeat_interleave(8, dim=3).repeat_interleave(8, dim=4).clamp(-1, 1)

    def _model(self):
        from types import SimpleNamespace
        return SimpleNamespace(trt=None, parker=None, vae=SimpleNamespace(model=self._Vae()))

    def test_grid_covers_with_overlap_and_fits_area(self):
        for lh, lw, area in ((60, 112, 3600), (90, 168, 2400), (60, 112, 1600), (44, 80, 99999), (90, 160, 600)):
            r, c, th, tw = M.tile_grid(lh, lw, area)
            if r * c > 1:
                self.assertLessEqual(th * tw, area)
                self.assertEqual((th % 2, tw % 2), (0, 0))                # 16 px
            boxes = M.tiles(lh, lw, area)
            self.assertEqual(len(boxes), r * c)
            cover = torch.zeros(lh, lw)
            for h0, h1, w0, w1 in boxes:
                self.assertEqual((h1 - h0, w1 - w0), (th, tw))          # one size: one set of TensorRT engines
                cover[h0:h1, w0:w1] += 1
            self.assertTrue(bool((cover >= 1).all()), (lh, lw, area))
            for starts, size in ((sorted({b[0] for b in boxes}), th), (sorted({b[2] for b in boxes}), tw)):
                for s0, s1 in zip(starts, starts[1:]):
                    self.assertGreaterEqual(s0 + size - s1, M.TILE_OVERLAP)   # neighbours share the fade

    def test_whole_frames_fit_untiled(self):
        self.assertEqual(M.tile_grid(60, 112, 6720), (1, 1, 60, 112))

    def test_tiled_matches_whole_for_a_local_vae(self):
        m = self._model()
        x = torch.rand(1, 3, 5, 480, 896) * 2 - 1
        whole = m.vae.model.encode(x, None)
        tiled = M._encode_tiled(m, x, None, 1600)
        self.assertTrue(torch.allclose(tiled, whole, atol=1e-5))
        want = whole.clamp(-1, 1)
        want = m.vae.model.decode(want, None)[0].float().add(1).mul(0.5).clamp(0, 1).mean(dim=0)
        got = M._decode_tiled(m, whole, None, 1600)
        self.assertEqual(got.shape, (5, 480, 896))
        self.assertTrue(torch.allclose(got, want, atol=1e-5))

    def test_no_tiles_off_the_card(self):
        from types import SimpleNamespace
        self.assertEqual(M.choose_tile_area(SimpleNamespace(device=torch.device("cpu")), 720, 1344, 81), 0)


class ParkTest(unittest.TestCase):
    """Smaller cards: the transformer's weights wait in RAM while the VAE runs (core.dvd.model._Parked)."""

    def test_off_and_back_keeps_every_weight_and_buffer(self):
        net = torch.nn.Sequential(torch.nn.Linear(4, 3), torch.nn.BatchNorm1d(3))
        before = {k: v.clone() for k, v in net.state_dict().items()}
        p = M._Parked(net, torch.device("cpu"))
        self.assertTrue(p.off())
        self.assertFalse(p.off())                          # already off: says so, does nothing
        for key, t in p._tensors():
            self.assertEqual(t.data_ptr(), p.cpu[key].data_ptr())     # the RAM copy itself, no new copy
        p.on()
        self.assertTrue(p.on_card)
        for k, v in net.state_dict().items():
            self.assertTrue(torch.equal(v, before[k]), k)
        self.assertEqual(net(torch.ones(2, 4)).shape, (2, 3))

    def test_small_cards_park(self):
        self.assertEqual(M.SMALL_CARD, 14 << 30)          # 8 and 12 GB cards; 16 GB only when it's short of room


class TokenSliceTest(unittest.TestCase):
    """The transformer's FFN and RoPE by token slices (core.dvd.model._by_tokens): the same as all at once."""

    def test_slices_match_whole(self):
        net = torch.nn.Sequential(torch.nn.Linear(8, 20), torch.nn.GELU(approximate="tanh"), torch.nn.Linear(20, 8))
        x = torch.randn(1, 37, 8)
        per = torch.randn(37, 1, 3)
        old = M.TOKEN_SLICE
        M.TOKEN_SLICE = 5                                  # 37 tokens: 7 whole slices and a short one
        try:
            with torch.no_grad():
                got = M._by_tokens(lambda t, p: net(t) + p.sum(dim=(1, 2)).view(1, -1, 1), x, per)
        finally:
            M.TOKEN_SLICE = old
        with torch.no_grad():
            want = torch.cat([net(x[:, i:i + 1]) + per[i].sum() for i in range(37)], dim=1)
        self.assertEqual(got.shape, (1, 37, 8))
        self.assertTrue(torch.allclose(got, want, atol=1e-6))


class VaeTensorRTTest(unittest.TestCase):
    """core.dvd.vae_trt's pure parts (the engines themselves need a GPU and a build: a real run proves them)."""

    def test_every_chunk_has_an_engine(self):
        from core.dvd import vae_trt as V
        self.assertEqual([V.stage(i) for i in range(5)], ["first", "second", "steady", "steady", "steady"])

    def test_encode_chunks_as_the_vae_slices_them(self):
        from core.dvd import vae_trt as V
        x = torch.arange(21).float().view(1, 1, 21, 1, 1)
        chunks = V.enc_chunks(x)
        self.assertEqual([c.shape[2] for c in chunks], [1, 4, 4, 4, 4, 4])
        self.assertEqual(torch.cat(chunks, 2).flatten().tolist(), list(range(21)))

    def test_cache_kinds(self):
        from core.dvd import vae_trt as V
        self.assertEqual(V.kinds([None, "Rep", torch.zeros(1)]), [None, "Rep", "T"])

    def test_off_by_default(self):
        self.assertIs(S.DEFAULTS["use_tensorrt"], False)
        self.assertIs(S.coerce("use_tensorrt", "true"), True)

    def test_export_copy_keeps_norms_finite_in_fp16(self):
        # F.normalize's sum of squares over 384 channels of ~200 overflows fp16 when added up in fp16 (ONNX /
        # TensorRT): the export's norms run in fp32. Here: a tiny encoder, its export copy's RMS_norm on big values.
        from core.dvd import vae_trt as V
        from dvd_depth.wan_video_vae import RMS_norm
        step = V.exportable(torch.nn.Sequential(RMS_norm(384, images=False)), [], torch.float16)
        norm = next(m for m in step.modules() if isinstance(m, RMS_norm))
        x = torch.full((1, 384, 1, 2, 2), 200.0, dtype=torch.float16)
        self.assertGreater(float((x.float() ** 2).sum(1).max()), 65504)      # the sum alone overflows fp16
        y = norm(x)
        self.assertEqual(y.dtype, torch.float16)
        self.assertTrue(torch.isfinite(y).all())
        self.assertTrue(torch.allclose(y.float(), torch.ones_like(y.float()), rtol=1e-3))   # x / |x| * sqrt(384)

    def test_export_copy_upsamples_as_onnx_can(self):
        from core.dvd import vae_trt as V
        from dvd_depth.wan_video_vae import Decoder3d
        dec = Decoder3d(dim=8, z_dim=4, dim_mult=[1, 2], num_res_blocks=1, temperal_upsample=[False])
        modes = {m.mode for m in V.exportable(dec, [], torch.float16).modules() if isinstance(m, torch.nn.Upsample)}
        self.assertTrue(modes and "nearest-exact" not in modes, modes)
        x = torch.randn(2, 3, 5, 7)
        F = torch.nn.functional
        self.assertTrue(torch.equal(F.interpolate(x, scale_factor=2.0, mode="nearest"),
                                    F.interpolate(x, scale_factor=2.0, mode="nearest-exact")))


if __name__ == "__main__":
    unittest.main()
