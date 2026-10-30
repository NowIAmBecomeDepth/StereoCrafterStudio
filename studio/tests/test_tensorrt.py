"""Inpainting's TensorRT box (a user's patch, 2026-10-29): pipelines/trt_unet.py. These need no TensorRT --
the engines are stand-ins; a real build and run is measured by hand (.claude/HANDOFF.md, 2026-10-29).

What they hold: a shape an engine fits goes through it and any other through PyTorch; a network that
fails once is PyTorch for the rest of the run, said once; the pipeline asks for TensorRT at its own call
sites (diffusers' offload hooks would undo a replaced forward); TensorRT and PyTorch chunk checkpoints
never resume into each other; without TensorRT installed nothing changes."""

from __future__ import annotations

import inspect
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

import torch  # noqa: E402

from pipelines import trt_unet as T  # noqa: E402


def sample(frames=25):
    return torch.zeros(1, frames, 8, 4, 6, dtype=torch.float16)


class FakeUNet:
    def __init__(self):
        self.calls = []
        self.device = torch.device("cpu")

    def __call__(self, sample, t, **kw):
        self.calls.append(tuple(sample.shape))
        return (torch.full(sample.shape[:2] + (4,) + sample.shape[3:], 7.0),)


class FakeVae:
    def __init__(self):
        self.decodes, self.encodes = [], []

    def decode(self, z, num_frames=None, return_dict=True):
        self.decodes.append(tuple(z.shape))
        return types.SimpleNamespace(sample=torch.zeros(z.shape[0], 3, 8, 8))

    def encode(self, x, return_dict=True):
        self.encodes.append(tuple(x.shape))
        return types.SimpleNamespace(latent_dist=None)


def trt(offload="model"):
    pipe = types.SimpleNamespace(unet=FakeUNet(), vae=FakeVae(), image_encoder=None)
    return T.TensorRT(pipe, frames_chunk=25, use_sageattention=True, offload_type=offload), pipe


class DispatchTest(unittest.TestCase):
    def test_what_fits_runs_on_the_engine_the_rest_in_pytorch(self):
        rt, pipe = trt()
        ran = []

        def engine(*args):
            ran.append(tuple(args[0].shape))
            return torch.ones(1, 25, 4, 4, 6)
        with mock.patch.object(rt, "_engine", lambda kind, key, name, build: engine), \
                mock.patch.object(T, "engine_name", lambda *a: "x"):
            ehs, ids = torch.zeros(1, 1, 1024, dtype=torch.float16), torch.zeros(1, 3, dtype=torch.float16)
            out = rt.unet(sample(), torch.tensor(3.0), encoder_hidden_states=ehs, added_time_ids=ids,
                          return_dict=False)[0]
            self.assertEqual(float(out.mean()), 1.0)
            rt.unet(sample(13), torch.tensor(3.0), encoder_hidden_states=ehs, added_time_ids=ids,
                    return_dict=False)                                # the shorter last chunk
            rt.unet(sample().float(), torch.tensor(3.0), encoder_hidden_states=ehs, added_time_ids=ids,
                    return_dict=False)                                # not fp16
        self.assertEqual(ran, [(1, 25, 8, 4, 6)])
        self.assertEqual(pipe.unet.calls, [(1, 13, 8, 4, 6), (1, 25, 8, 4, 6)])
        self.assertEqual((rt.nets["unet"].trt_calls, rt.nets["unet"].torch_calls), (1, 2))
        self.assertIn("UNet steps 1 TensorRT / 2 PyTorch", rt.summary())

    def test_the_vae_odd_frame_and_fp32_go_to_pytorch(self):
        rt, pipe = trt()
        rt._checked_decoder = True
        with mock.patch.object(rt, "_engine", lambda kind, key, name, build: (lambda x: torch.zeros(2, 8, 4, 4))), \
                mock.patch.object(T, "engine_name", lambda *a: "x"):
            rt.vae_decode(torch.zeros(2, 4, 4, 4, dtype=torch.float16), num_frames=2)
            rt.vae_decode(torch.zeros(1, 4, 4, 4, dtype=torch.float16), num_frames=1)   # the 25th frame
            enc = rt.vae_encode(torch.zeros(5, 3, 32, 32, dtype=torch.float16))
            self.assertEqual(tuple(enc.latent_dist.mode().shape), (2, 4, 4, 4))
            rt.vae_encode(torch.zeros(5, 3, 32, 32))                                     # the fp32 retry
        self.assertEqual((pipe.vae.decodes, pipe.vae.encodes), ([(1, 4, 4, 4)], [(5, 3, 32, 32)]))

    def test_a_failure_turns_that_network_off_for_the_run(self):
        rt, pipe = trt()
        made = []

        class Broken:
            def __init__(self, path, scratch=None):
                made.append(path)

            def __call__(self, *a):
                raise RuntimeError("engine fell over")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(T, "ENGINE_DIR", Path(d)), \
                mock.patch.object(T, "Engine", Broken), mock.patch.object(T, "engine_name", lambda *a: "e.engine"):
            (Path(d) / "e.engine").write_bytes(b"built before")
            ehs, ids = torch.zeros(1, 1, 1024, dtype=torch.float16), torch.zeros(1, 3, dtype=torch.float16)
            with self.assertLogs(T.log, "WARNING") as logs:
                for _ in range(3):
                    rt.unet(sample(), torch.tensor(1.0), encoder_hidden_states=ehs, added_time_ids=ids,
                            return_dict=False)
        self.assertEqual(len(made), 1, "loaded once, not again after failing")
        self.assertEqual(len(logs.records), 1, "said once")
        self.assertIn("engine fell over", logs.output[0])
        self.assertEqual(len(pipe.unet.calls), 3, "every step still ran, in PyTorch")
        self.assertTrue(rt.nets["unet"].off)

    def test_an_engine_is_built_once_then_loaded(self):
        rt, _ = trt()
        builds, loads = [], []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(T, "ENGINE_DIR", Path(d)), \
                mock.patch.object(T, "Engine", lambda p, s=None: loads.append(p) or "ENGINE"):
            def build(name):
                builds.append(name)
                (Path(d) / name).write_bytes(b"engine")
            self.assertEqual(rt._engine("unet", (1,), "a.engine", build), "ENGINE")
            self.assertEqual(rt._engine("unet", (1,), "a.engine", build), "ENGINE")
            rt2, _ = trt()
            rt2._engine("unet", (1,), "a.engine", build)          # the next run: on disk already
        self.assertEqual(builds, ["a.engine"])
        self.assertEqual(len(loads), 2)

    def test_a_half_written_engine_never_looks_finished(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(T, "ENGINE_DIR", Path(d)), \
                mock.patch.object(T, "_serialize", side_effect=RuntimeError("build failed")):
            released = []
            with self.assertRaises(RuntimeError):
                T.build_engine("u.engine", lambda p: p.write_bytes(b"onnx"), release=lambda: released.append(1))
            self.assertEqual(released, [1], "the GPU copy freed even so")
            self.assertEqual([p.name for p in Path(d).iterdir()], [], "no engine, no .part, no ONNX left")

    def test_with_no_offload_the_pytorch_unet_waits_in_ram(self):
        rt, pipe = trt("none")
        moves = []
        pipe.unet.device = torch.device("cuda")
        pipe.unet.to = lambda dev: (moves.append(dev), setattr(pipe.unet, "device", torch.device(dev)))
        with mock.patch.object(torch.cuda, "empty_cache", lambda: None):
            rt._park_torch_unet()
            rt._unpark_torch_unet()
        self.assertEqual(moves, ["cpu", "cuda"])


@unittest.skipUnless(torch.cuda.is_available(), "needs the GPU")
class ScratchTest(unittest.TestCase):
    """One working buffer for every engine of a run (2026-10-29: a buffer per call, marked used on
    TensorRT's stream, left two copies of each engine's cached -- the decode spilled out of VRAM)."""

    def test_one_buffer_grown_to_the_largest(self):
        s = T.Scratch()
        a = s.get(1 << 20)
        self.assertEqual(s.get(1 << 10), a, "a smaller ask reuses it")
        s.get(4 << 20)
        self.assertEqual(s.buffer.numel(), 4 << 20)
        self.assertEqual(s.get(2 << 20), s.buffer.data_ptr())
        s.release()
        self.assertIsNone(s.buffer)


class NotInstalledTest(unittest.TestCase):
    def test_nothing_changes_without_tensorrt(self):
        pipe = types.SimpleNamespace(tensorrt=None)
        with mock.patch.dict(sys.modules, {"tensorrt": None}), mock.patch.object(torch.cuda, "is_available",
                                                                                 lambda: True):
            with self.assertLogs(T.log, "WARNING") as logs:
                self.assertIsNone(T.enable_tensorrt(pipe, 25, True, "model"))
        self.assertIsNone(pipe.tensorrt)
        self.assertIn("'tensorrt' is not installed", logs.output[0])
        T.release_tensorrt(pipe)                     # nothing to release: no error

    def test_not_with_sequential_offload(self):
        pipe = types.SimpleNamespace(tensorrt=None)
        with mock.patch.object(T, "unavailable_reason", lambda: ""), self.assertLogs(T.log, "WARNING"):
            self.assertIsNone(T.enable_tensorrt(pipe, 25, True, "sequential"))


class PipelineCallSitesTest(unittest.TestCase):
    """The pipeline asks for TensorRT where it calls the UNet and VAE: diffusers' maybe_free_model_hooks
    puts model offload back after every tile, restoring unet.forward, so a replaced forward would last
    one tile."""

    def stub(self, rt):
        vae = FakeVae()
        vae.config = types.SimpleNamespace(scaling_factor=1.0)
        vae.forward = lambda sample, num_frames=1: None
        return types.SimpleNamespace(vae=vae, tensorrt=rt)

    def test_decode_and_encode_go_through_tensorrt(self):
        from pipelines.stereo_video_inpainting import StableVideoDiffusionInpaintingPipeline as P
        calls = []
        rt = types.SimpleNamespace(
            vae_decode=lambda z, num_frames=None: calls.append(("dec", num_frames)) or types.SimpleNamespace(
                sample=torch.zeros(z.shape[0], 3, 8, 8)),
            vae_encode=lambda x: calls.append(("enc", x.shape[0])) or types.SimpleNamespace(
                latent_dist=types.SimpleNamespace(mode=lambda: torch.zeros(x.shape[0], 4, 1, 1))))
        s = self.stub(rt)
        P._decode_latents(s, torch.zeros(1, 5, 4, 1, 1), 5, decode_chunk_size=2)
        P._encode_vae_frames(s, torch.zeros(10, 3, 8, 8), "cpu", 1, False)
        self.assertEqual(calls, [("dec", 2), ("dec", 2), ("dec", 1), ("enc", 5), ("enc", 5)])
        self.assertEqual((s.vae.decodes, s.vae.encodes), ([], []))

    def test_without_tensorrt_the_vae_as_before(self):
        from pipelines.stereo_video_inpainting import StableVideoDiffusionInpaintingPipeline as P
        s = self.stub(None)
        P._decode_latents(s, torch.zeros(1, 3, 4, 1, 1), 3, decode_chunk_size=2)
        self.assertEqual(s.vae.decodes, [(2, 4, 1, 1), (1, 4, 1, 1)])
        self.assertIsNone(P.tensorrt)

    def test_the_denoise_asks_for_it(self):
        from pipelines.stereo_video_inpainting import StableVideoDiffusionInpaintingPipeline as P
        src = inspect.getsource(P.__call__)
        self.assertIn("denoise = self.tensorrt.unet if self.tensorrt is not None else self.unet", src)
        self.assertIn("noise_pred = denoise(", src)
        self.assertNotIn("self.unet(", src)


class DepthCrafterDispatchTest(unittest.TestCase):
    """DepthCrafter's engines take any length (2026-10-29): a short clip is one window as long as itself,
    a segment one as long as the segment -- a fixed-length engine would be a new build for nearly every
    clip. The UNet 2 frames to the window, the VAE 2 to 4; one frame and longer than the window: PyTorch."""

    def rt(self):
        pipe = types.SimpleNamespace(unet=FakeUNet(), vae=FakeVae(), image_encoder=None)
        return T.DepthCrafterTensorRT(pipe, frames_chunk=120, use_sageattention=True, offload_type="model"), pipe

    def test_one_engine_for_every_window_length(self):
        rt, pipe = self.rt()
        keys, names = [], []

        def engine(kind, key, name, build):
            keys.append(key)
            names.append(name)
            return lambda *a: torch.zeros(1, a[0].shape[1], 4, 4, 6)
        with mock.patch.object(rt, "_engine", engine), mock.patch.object(T, "_gpu_tag", lambda: "GPU"), \
                mock.patch.object(T, "_trt_version", lambda: "11"):
            ids = torch.zeros(1, 3, dtype=torch.float16)
            for f in (120, 90, 2, 1, 121):
                rt.unet(sample(f), torch.tensor(1.0), encoder_hidden_states=torch.zeros(1, f, 1024, dtype=torch.float16),
                        added_time_ids=ids, return_dict=False)
        self.assertEqual(len(set(keys)), 1, "the same engine for 120, 90 and 2 frames")
        self.assertEqual(names[0], "dc_unet_48x32_f2-120_c8_GPU_trt11.engine")
        self.assertEqual(pipe.unet.calls, [(1, 1, 8, 4, 6), (1, 121, 8, 4, 6)])

    def test_vae_only_leaves_the_unet_to_pytorch(self):
        """Thomas's DepthCrafter patch (2026-10-29): the VAE only, the depth as close to exact as normal."""
        pipe = types.SimpleNamespace(unet=FakeUNet(), vae=FakeVae(), image_encoder=None)
        rt = T.DepthCrafterTensorRT(pipe, 120, True, "model", trt_unet=False)
        asked = []
        with mock.patch.object(rt, "_engine", lambda kind, *a: asked.append(kind) or (lambda *x: torch.zeros(2, 8, 4, 4))), \
                mock.patch.object(T, "engine_name", lambda *a: "x"):
            rt.unet(sample(120), torch.tensor(1.0), encoder_hidden_states=torch.zeros(1, 120, 1024, dtype=torch.float16),
                    added_time_ids=torch.zeros(1, 3, dtype=torch.float16), return_dict=False)
            rt.vae_encode(torch.zeros(2, 3, 32, 32, dtype=torch.float16))
        self.assertEqual(asked, ["vaeenc"])
        self.assertEqual(pipe.unet.calls, [(1, 120, 8, 4, 6)])

    def test_the_vae_two_to_four_frames(self):
        rt, pipe = self.rt()
        rt._checked_decoder = True
        with mock.patch.object(rt, "_engine", lambda kind, key, name, build: (lambda x: torch.zeros(x.shape[0], 8, 4, 4))), \
                mock.patch.object(T, "engine_name", lambda *a: "x"):
            for n in (4, 3, 1):
                rt.vae_decode(torch.zeros(n, 4, 4, 4, dtype=torch.float16), num_frames=n)
                rt.vae_encode(torch.zeros(n, 3, 32, 32, dtype=torch.float16))
        self.assertEqual((pipe.vae.decodes, pipe.vae.encodes), ([(1, 4, 4, 4)], [(1, 3, 32, 32)]))
        self.assertEqual((rt.nets["vaedec"].trt_calls, rt.nets["vaeenc"].trt_calls), (2, 2))

    def test_its_lines_reach_the_stage_log(self):
        dc_log = mock.Mock()
        pipe = types.SimpleNamespace(tensorrt=None)
        with mock.patch.object(T, "unavailable_reason", lambda: ""):
            rt = T.enable_tensorrt(pipe, 120, True, "model", kind=T.DepthCrafterTensorRT, logger=dc_log,
                                   stage="DepthCrafter")
        self.assertIsInstance(rt, T.DepthCrafterTensorRT)
        T.release_tensorrt(pipe)
        self.assertEqual(dc_log.info.call_count, 2, "on, then the run's counts")
        self.assertIn("UNet steps", dc_log.info.call_args[0][0])


class ExportableTest(unittest.TestCase):
    """What the exporter traces: Sage as plain SDPA (it can't be exported), the cross-attention shortcut
    kept around plain SDPA, DepthCrafter's feed-forward slicing off -- all put back after."""

    def test_swapped_for_the_export_and_put_back(self):
        from diffusers.models.attention import FeedForward
        from diffusers.models.attention_processor import Attention, AttnProcessor2_0
        from depthcrafter.attention import SingleTokenCrossAttnProcessor
        from depthcrafter.unet import chunk_feed_forward

        class SageAttnProcessor2_0:
            pass
        self_attn, cross_attn = Attention(query_dim=8, heads=1, dim_head=8), Attention(query_dim=8, heads=1, dim_head=8)
        sage, shortcut = SageAttnProcessor2_0(), SingleTokenCrossAttnProcessor(SageAttnProcessor2_0())
        self_attn.set_processor(sage)
        cross_attn.set_processor(shortcut)
        model = torch.nn.ModuleList([self_attn, cross_attn, FeedForward(8)])
        self.assertEqual(chunk_feed_forward(model), 1)
        sliced = model[2].forward
        with T.exportable(model):
            self.assertIsInstance(self_attn.processor, AttnProcessor2_0)
            self.assertIsInstance(cross_attn.processor, SingleTokenCrossAttnProcessor)
            self.assertIsInstance(cross_attn.processor.inner, AttnProcessor2_0)
            self.assertNotIn("forward", model[2].__dict__, "the class's own forward while exporting")
        self.assertIs(self_attn.processor, sage)
        self.assertIs(cross_attn.processor, shortcut)
        self.assertIs(model[2].forward, sliced)


class DepthCrafterCallSitesTest(unittest.TestCase):
    def test_encode_decode_and_the_resume_header(self):
        from depthcrafter.depth_crafter_ppl import DepthCrafterPipeline as P
        calls = []
        rt = types.SimpleNamespace(
            vae_decode=lambda z, num_frames=None: calls.append(("dec", num_frames)) or types.SimpleNamespace(
                sample=torch.zeros(z.shape[0], 3, 8, 8)),
            vae_encode=lambda x: calls.append(("enc", x.shape[0])) or types.SimpleNamespace(
                latent_dist=types.SimpleNamespace(mode=lambda: torch.zeros(x.shape[0], 4, 1, 1))))
        s = types.SimpleNamespace(vae=FakeVae(), tensorrt=rt, LATENT_CHECKPOINT_FORMAT_VERSION=1)
        P._decode_chunk(s, torch.zeros(4, 4, 1, 1), True)
        P.encode_vae_video(s, torch.zeros(6, 3, 8, 8), chunk_size=4)
        self.assertEqual(calls, [("dec", 4), ("enc", 4), ("enc", 2)])
        self.assertEqual((s.vae.decodes, s.vae.encodes), ([], []))
        args = (10, 120, 100, 10, 1.0, 448, 768, 0.0)
        self.assertTrue(P._latent_checkpoint_header(s, *args)["tensorrt"])
        s.tensorrt = None
        self.assertNotIn("tensorrt", P._latent_checkpoint_header(s, *args), "a PyTorch run's header as it was")
        src = inspect.getsource(P.__call__)
        self.assertIn("denoise = self.tensorrt.unet if self.tensorrt is not None else self.unet", src)
        self.assertEqual(src.count("denoise("), 2, "the guided pass too")
        self.assertNotIn("self.unet(", src)


class DepthCrafterRunnerTest(unittest.TestCase):
    def test_the_box_turns_it_on_for_the_loaded_model(self):
        from studio.tests import test_depthcrafter_runner as TR
        R = TR.R
        seen = []

        class Run(TR.RunnerTestBase):
            def runTest(self):
                pass
        base = Run()
        base.setUp()
        try:
            base.clip("a.mp4")
            specs, _ = R.discover_sources(str(base.inp))
            with mock.patch.object(T, "enable_tensorrt", lambda *a, **k: seen.append((a, k))):
                R.DepthCrafterRunner(base.opts(use_tensorrt_var=True, window_size=120, cpu_offload="none"),
                                     TR.RecordingHooks(), threading.Event()).run_batch(specs, 42)
                base.clip("b.mp4")
                specs, _ = R.discover_sources(str(base.inp))
                R.DepthCrafterRunner(base.opts(), TR.RecordingHooks(), threading.Event()).run_batch(specs, 42)
                base.clip("c.mp4")
                specs, _ = R.discover_sources(str(base.inp))
                R.DepthCrafterRunner(base.opts(use_tensorrt_var=True, use_tensorrt_unet_var=True, window_size=120,
                                               cpu_offload="none"),
                                     TR.RecordingHooks(), threading.Event()).run_batch(specs, 42)
        finally:
            base.tearDown()
        self.assertEqual(len(seen), 2, "only with the box on")
        (pipe, window, sage, offload), kw = seen[0]
        self.assertEqual((window, sage, offload), (120, True, "none"))
        self.assertIs(kw["kind"], T.DepthCrafterTensorRT)
        self.assertEqual(kw["stage"], "DepthCrafter")
        self.assertIs(kw["trt_unet"], False, "TensorRT VAE alone: the UNet stays PyTorch (user, 2026-10-29)")
        self.assertIs(seen[1][1]["trt_unet"], True, "UNet Too on")

    def test_unet_box_off_by_default_and_under_the_vae_box(self):
        from studio.stages.depthcrafter import settings as S
        self.assertIs(S.DEFAULTS["use_tensorrt_unet_var"], False)
        qml = (REPO / "studio/qml/Studio/DepthCrafterPage.qml").read_text("utf8")
        self.assertIn('key: "use_tensorrt_unet_var"', qml)
        self.assertIn("shown: !!page.st.use_tensorrt_var", qml)


class ResumeSignatureTest(unittest.TestCase):
    def test_tensorrt_and_pytorch_chunks_never_resume_into_each_other(self):
        from studio.stages.inpainting import engine as E
        from studio.stages.inpainting import settings as S
        values = S.normalise({})
        eng = E.InpaintingEngine(S.run_settings(values), {"input": "", "output": "", "hires": ""}, values,
                                 types.SimpleNamespace(), threading.Event())
        args = dict(input_video_path="a.mp4", hires_video_path=None, is_dual_input=False, frames_chunk=25,
                    overlap=5, tile_num=2, num_inference_steps=10, original_input_blend_strength=0.1,
                    process_length=-1)
        plain = eng._build_prefinalize_signature(**args)
        eng.pipeline = types.SimpleNamespace(tensorrt=object())
        on = eng._build_prefinalize_signature(**args)
        self.assertNotIn("tensorrt", plain, "a PyTorch run's signature is as it was")
        self.assertEqual(on, {**plain, "tensorrt": True})

    def test_off_by_default(self):
        from studio.stages.inpainting import settings as S
        self.assertIs(S.DEFAULTS["use_tensorrt"], False)
        self.assertIs(S.normalise({})["use_tensorrt"], False)
        self.assertIn('key: "use_tensorrt"', (REPO / "studio/qml/Studio/InpaintingPage.qml").read_text("utf8"))


if __name__ == "__main__":
    unittest.main()
