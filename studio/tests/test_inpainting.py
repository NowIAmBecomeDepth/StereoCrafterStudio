"""Tests for v2 Inpainting (V1 SVD): the generated engine and the settings.

The model is never loaded: ``load_inpainting_pipeline`` and
``process_single_video`` (the per-clip torch work) are replaced with fakes,
and everything else -- v1's own run_batch_process, sidecar overrides, clip-ID
filter, resume skip, finished/ moves -- runs for real in a temp folder.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

from studio.stages.inpainting import settings as S  # noqa: E402
try:                    # the Prototype's generators don't ship (release, 2026-10-17)
    from studio.tools import gen_inpainting_engine as gen  # noqa: E402
except ImportError:
    gen = None


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    def test_engine_matches_v1(self):
        """engine.py is exactly what the generator makes from today's v1.

        Fails when inpainting_gui.py's engine has changed since engine.py was
        generated: re-run studio.tools.gen_inpainting_engine."""
        fresh = gen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, gen.TARGET.read_bytes(),
                         "engine.py is stale; re-run: python -m studio.tools.gen_inpainting_engine")


class RetiredSettingsTest(unittest.TestCase):
    """Post-Inpainting Blend left Studio (user, 2026-10-11: Merging blends the
    fill in, with more control). A config, preset or older Studio that saved it
    on must not run it unseen; the hi-res finalize itself is untouched."""

    def test_a_saved_blend_is_held_off(self):
        for saved in (True, "true", "1"):
            values = S.normalise({"enable_post_inpainting_blend": saved})
            self.assertIs(values["enable_post_inpainting_blend"], False)
            self.assertIs(S.run_settings(values)["enable_post_inpainting_blend"], False)

    def test_the_hi_res_blend_folder_still_runs(self):
        values = S.normalise({"hires_blend_folder": "X:/hires"})
        self.assertEqual(values["hires_blend_folder"], "X:/hires")
        self.assertEqual(S.run_settings(values)["hires_blend_folder_var"], "X:/hires")


class CrossAttentionShortcutTest(unittest.TestCase):
    """2026-10-11: V1's UNet sees one CLIP embedding, so its cross-attention
    skips the attention (depthcrafter.attention), with Sage or SDPA. A stand-in
    UNet of real diffusers Attention layers on the CPU."""

    def unet(self):
        import torch
        from diffusers.models.attention_processor import Attention

        class Unet(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.attn1 = Attention(query_dim=64, heads=4, dim_head=16)
                self.attn2 = Attention(query_dim=64, cross_attention_dim=32, heads=4, dim_head=16)

            def set_attn_processor(self, processor):
                for m in (self.attn1, self.attn2):
                    m.set_processor(processor)
        return Unet()

    def test_the_cross_attention_is_wrapped_either_way(self):
        from pipelines.stereo_video_inpainting import configure_attention_processors
        from depthcrafter.attention import SingleTokenCrossAttnProcessor
        for sage in (False, True):
            pipe = types.SimpleNamespace(unet=self.unet())
            configure_attention_processors(pipe, use_sageattention=sage)
            self.assertIsInstance(pipe.unet.attn2.processor, SingleTokenCrossAttnProcessor, f"sage={sage}")
            self.assertNotIsInstance(pipe.unet.attn1.processor, SingleTokenCrossAttnProcessor)


class Hooks:
    def __init__(self):
        self.statuses, self.progress_calls, self.clips, self.messages, self.finish_calls = [], [], [], [], []
        self.outcome = None

    def status(self, text):
        self.statuses.append(text)

    def progress(self, done, total):
        self.progress_calls.append((done, total))

    def clip(self, *values):
        self.clips.append(values)

    def message(self, kind, title, message):
        self.messages.append((kind, title, message))

    def finished(self, stopped, outcome=None):
        self.finish_calls.append(stopped)
        self.outcome = outcome


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting import engine as E
        cls.E = E

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lo, self.hi, self.out = root / "lowres", root / "hires", root / "out"
        for d in (self.lo, self.hi, self.out):
            d.mkdir()
        self.calls = []
        self.stop = threading.Event()
        E = self.E
        self._saved = (E.load_inpainting_pipeline, E.InpaintingEngine.process_single_video)
        self.loads = []
        E.load_inpainting_pipeline = lambda **kw: self.loads.append(kw) or "PIPELINE"
        # the models are taken as there: a clean checkout has none (2026-10-20: the run checks first)
        from studio import model_catalog
        self._saved_missing = model_catalog.missing_message
        model_catalog.missing_message = lambda keys, root=None: ""
        test = self

        def fake_process(self, **kw):
            test.calls.append(kw)
            name = os.path.basename(kw["input_video_path"])
            hires = test.hi / name
            if test.stop_after is not None and len(test.calls) >= test.stop_after:
                test.stop.set()
            return True, (str(hires) if hires.exists() else None)

        E.InpaintingEngine.process_single_video = fake_process
        self.stop_after = None

    def tearDown(self):
        self.E.load_inpainting_pipeline, self.E.InpaintingEngine.process_single_video = self._saved
        from studio import model_catalog
        model_catalog.missing_message = self._saved_missing
        self.tmp.cleanup()

    def clip(self, name, hires=True):
        (self.lo / name).write_bytes(b"lo")
        if hires:
            (self.hi / name).write_bytes(b"hi")

    def run_batch(self, **over):
        values = S.normalise({"input_folder": str(self.lo), "output_folder": str(self.out),
                              "hires_blend_folder": str(self.hi), **over})
        kwargs, err = S.validate(values)
        self.assertEqual(err, "")
        hooks = Hooks()
        eng = self.E.InpaintingEngine(S.run_settings(values),
                                      {"input": values["input_folder"], "output": values["output_folder"],
                                       "hires": values["hires_blend_folder"]},
                                      values, hooks, self.stop)
        eng.run_batch_process(values["input_folder"], values["output_folder"], **kwargs)
        return hooks, eng

    def test_batch_with_sidecar_override_and_finished_moves(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        (self.lo / "shot-0007_640_splatted4.spsidecar").write_text(json.dumps({"frame_overlap": 7, "input_bias": 0.25}))
        hooks, eng = self.run_batch(frame_overlap="5", original_input_blend_strength="0.0")

        self.assertEqual([os.path.basename(c["input_video_path"]) for c in self.calls],
                         ["shot-0006_640_splatted4.mp4", "shot-0007_640_splatted4.mp4"])
        self.assertEqual((self.calls[0]["overlap"], self.calls[0]["original_input_blend_strength"]), (5, 0.0))
        self.assertEqual((self.calls[1]["overlap"], self.calls[1]["original_input_blend_strength"]), (7, 0.25))
        self.assertEqual(self.calls[0]["pipeline"], "PIPELINE")
        # Lo-res clips, the sidecar and the hi-res matches all moved to finished/.
        self.assertEqual(sorted(p.name for p in (self.lo / "finished").iterdir()),
                         ["shot-0006_640_splatted4.mp4", "shot-0007_640_splatted4.mp4", "shot-0007_640_splatted4.spsidecar"])
        self.assertEqual(sorted(p.name for p in (self.hi / "finished").iterdir()),
                         ["shot-0006_640_splatted4.mp4", "shot-0007_640_splatted4.mp4"])
        self.assertEqual(hooks.progress_calls[-1], (2, 2))
        self.assertEqual(hooks.finish_calls, [False])
        self.assertTrue(hooks.statuses[-1].startswith("Processing completed"))
        self.assertIsNone(eng.pipeline)  # released in processing_done

    def test_use_compile_reaches_the_pipeline_off_by_default(self):
        # Torch Compile: torch.compile for long unattended runs (2026-09-30).
        self.clip("shot-0006_640_splatted4.mp4")
        self.run_batch()
        self.assertIs(self.loads[-1]["use_compile"], False)
        self.clip("shot-0007_640_splatted4.mp4")
        self.run_batch(use_compile=True)
        self.assertIs(self.loads[-1]["use_compile"], True)
        self.assertIs(S.DEFAULTS["use_compile"], False)
        self.assertIs(S.RESET_VALUES["use_compile"], False)

    def test_guidance_scale_one_by_default_and_reaches_the_engine(self):
        # Settings > Inpainting > Guidance Scale (2026-10-09): 1.0 skips the unconditional pass.
        self.assertEqual(S.DEFAULTS["guidance_scale"], "1.0")
        self.assertEqual(S.RESET_VALUES["guidance_scale"], "1.0")
        self.clip("shot-0006_640_splatted4.mp4")
        _, eng = self.run_batch(guidance_scale="1.01")
        self.assertEqual(float(eng._setting("guidance_scale_var")), 1.01)

    def test_guidance_scale_out_of_range_is_refused_at_start(self):
        for bad in ("0.9", "10.5", "x"):
            kwargs, err = S.validate(S.normalise({"guidance_scale": bad}))
            self.assertIsNone(kwargs, bad)
            self.assertIn("Guidance Scale", err)
        self.assertEqual(S.validate(S.normalise({"guidance_scale": "3"}))[1], "")

    def test_guidance_of_one_turns_the_second_pass_off(self):
        # was `return self.guidance_scale`: 1.0 counted as on for the embeddings
        # and off in the loop, so the batches didn't match
        from pipelines.stereo_video_inpainting import StableVideoDiffusionInpaintingPipeline as P
        cfg = P.do_classifier_free_guidance.fget
        self.assertFalse(cfg(types.SimpleNamespace(guidance_scale=1.0)))
        self.assertTrue(cfg(types.SimpleNamespace(guidance_scale=1.01)))

    def test_resume_expects_what_each_chunk_saved(self):
        # bug sweep 2026-10-09 (A7): the appended frames of a clip's chunks add up
        # to the clip; resume expected the last one `overlap` short and re-ran it
        from studio.stages.inpainting import engine as E
        for total in range(1, 140):
            for chunk in (5, 16, 23, 25):
                for overlap in range(0, chunk // 2 + 1):
                    stride, got = max(1, chunk - overlap), 0
                    for i in range(0, total, stride):
                        actual = min(i + chunk, total) - i
                        if i > 0 and overlap > 0 and actual <= overlap:
                            break
                        got += E._chunk_append_length(i, stride, overlap, actual, total)
                    self.assertEqual(got, total, (total, chunk, overlap))
        # the case that was wrong: 60 frames, chunks of 25 with 5 overlap -> 0, 20, 40 (the last, 20 frames)
        self.assertEqual(E._chunk_append_length(40, 20, 5, 20, 60), 20)

    def test_offload_none_parks_unet_and_clip_for_the_decode(self):
        # 2026-10-09: with them on the GPU the 1408x768 decode filled 16 GB and crawled
        from pipelines.stereo_video_inpainting import StableVideoDiffusionInpaintingPipeline as P
        import torch
        moves = []

        class Mod:
            def __init__(self, name):
                self.name, self.device = name, torch.device("cuda", 0)

            def to(self, dev):
                moves.append((self.name, str(dev)))
                self.device = torch.device(dev)

        fake = types.SimpleNamespace(unet=Mod("unet"), image_encoder=Mod("clip"), park_idle_for_decode=True)
        fake._decode_latents = lambda *a: (moves.append(("decode", "")), "FRAMES")[1]
        self.assertEqual(P.decode_latents(fake, None, 2, 2), "FRAMES")
        self.assertEqual(moves, [("unet", "cpu"), ("clip", "cpu"), ("decode", ""),
                                 ("unet", "cuda:0"), ("clip", "cuda:0")])
        moves.clear()
        fake.park_idle_for_decode = False
        P.decode_latents(fake, None, 2, 2)
        self.assertEqual(moves, [("decode", "")])

    # Single Clip ID left the page (user, 2026-10-11: Inpaint This Clip Now does
    # one clip); an ID saved by an older Studio no longer filters the run
    def test_a_saved_single_clip_id_is_ignored(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        self.run_batch(single_clip_id="7")
        self.assertEqual([os.path.basename(c["input_video_path"]) for c in self.calls],
                         ["shot-0006_640_splatted4.mp4", "shot-0007_640_splatted4.mp4"])

    def test_resume_skips_finished(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        (self.lo / "finished").mkdir()
        (self.lo / "finished" / "shot-0006_640_splatted4.mp4").write_bytes(b"lo")   # the very same file
        self.run_batch()
        self.assertEqual([os.path.basename(c["input_video_path"]) for c in self.calls], ["shot-0007_640_splatted4.mp4"])

    # audit 2026-09-28 #8 (the user's Chapter 5): a clip re-splatted after its
    # old splat went to finished/ shares its name, not its content -- run it
    def test_resume_runs_a_re_splatted_clip(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        (self.lo / "finished").mkdir()
        (self.lo / "finished" / "shot-0006_640_splatted4.mp4").write_bytes(b"the old splat")
        (self.lo / "finished" / "shot-0007_640_splatted4.mp4").write_bytes(b"lo")
        hooks, eng = self.run_batch()
        self.assertEqual([os.path.basename(c["input_video_path"]) for c in self.calls], ["shot-0006_640_splatted4.mp4"])
        self.assertEqual(eng.outcome.skipped, 1)

    # audit 2026-09-28 O8: the SVD pipeline (10-30 s, several GB of VRAM) was
    # loaded before the engine looked for work, so a Start that found nothing
    # to do paid for it anyway. It loads once there is a clip to inpaint.
    def test_the_model_loads_only_when_there_is_work(self):
        self.run_batch()                                     # an empty folder
        self.assertEqual(self.loads, [], "loaded for an empty folder")
        self.clip("shot-0006_640_splatted4.mp4")
        (self.lo / "finished").mkdir()
        (self.lo / "finished" / "shot-0006_640_splatted4.mp4").write_bytes(b"lo")
        (self.lo / "shot-0006_640_splatted4.mp4").write_bytes(b"lo")
        self.run_batch()                                     # everything already done
        self.assertEqual(self.loads, [], "loaded when every clip was already processed")
        self.clip("shot-0007_640_splatted4.mp4")
        self.run_batch()                                     # one clip to do
        self.assertEqual(len(self.loads), 1)
        self.assertEqual([os.path.basename(c["input_video_path"]) for c in self.calls], ["shot-0007_640_splatted4.mp4"])

    def test_a_missing_model_is_said_and_nothing_loads(self):
        # 2026-10-20: the run names what's missing and where to get it, not a loader's traceback
        from studio import model_catalog
        model_catalog.missing_message = lambda keys, root=None: "StereoCrafter is not in your models folder."
        self.clip("shot-0006_640_splatted4.mp4")
        _hooks, eng = self.run_batch()
        self.assertEqual(self.loads, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(eng.outcome.error, "StereoCrafter is not in your models folder.")

    # audit 2026-09-28 (minor): an Overlap not below the Chunk makes no
    # progress (the step clamps to one frame and the loop quits after the
    # first chunk). A sidecar asking for one is ignored for the page's value.
    def test_a_sidecar_overlap_not_below_the_chunk_is_ignored(self):
        self.clip("shot-0006_640_splatted4.mp4")
        (self.lo / "shot-0006_640_splatted4.spsidecar").write_text(json.dumps({"frame_overlap": 30}))
        self.run_batch(frames_chunk="23", frame_overlap="3")
        self.assertEqual(self.calls[0]["overlap"], 3)

    # bug sweep 2026-10-09 #10: past half the chunk the output drifts from its masks
    def test_a_sidecar_overlap_over_half_the_chunk_is_ignored(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        (self.lo / "shot-0006_640_splatted4.spsidecar").write_text(json.dumps({"frame_overlap": 12}))
        (self.lo / "shot-0007_640_splatted4.spsidecar").write_text(json.dumps({"frame_overlap": 11}))
        self.run_batch(frames_chunk="23", frame_overlap="3")
        self.assertEqual([c["overlap"] for c in self.calls], [3, 11])

    def test_no_videos_reports_and_finishes(self):
        hooks, _ = self.run_batch()
        self.assertEqual(self.calls, [])
        # audit #3: said once, by the finish toast (the outcome's note), not
        # by an info box and then a green "finished"
        self.assertEqual(hooks.messages, [])
        self.assertIn("No .mp4/.mov files", hooks.outcome.note)
        self.assertEqual((hooks.outcome.done, hooks.outcome.failed), (0, 0))
        self.assertEqual(hooks.finish_calls, [False])

    def test_stop_ends_after_current_clip(self):
        self.clip("shot-0006_640_splatted4.mp4")
        self.clip("shot-0007_640_splatted4.mp4")
        self.stop_after = 1
        hooks, _ = self.run_batch()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(hooks.finish_calls, [True])
        self.assertTrue(hooks.statuses[-1].startswith("Processing stopped"))

    def test_settings_reach_engine_under_v1_names(self):
        values = S.normalise({"frames_chunk": "21", "enable_color_transfer": False, "debug_mode": True})
        rs = S.run_settings(values)
        eng = self.E.InpaintingEngine(rs, {"input": "", "output": "", "hires": ""}, values, Hooks(), self.stop)
        self.assertEqual(eng._setting("frames_chunk_var"), "21")
        self.assertIs(eng._setting("enable_color_transfer"), False)
        self.assertIs(eng._setting("debug_mode_var"), True)
        self.assertNotIn("codec", rs)
        self.assertEqual(eng.app_config["codec"], S.DEFAULTS["codec"])


class ResumeTests(unittest.TestCase):
    """The per-clip resume path (audit 2026-09-28 #6 / #7), torch work stubbed:
    a resume checkpoint is keyed on the inpaint direction and the hi-res
    source, and a Reverse-mode resume lines its blend mask up with its frames."""

    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting import engine as E
        cls.E = E

    class _Done(Exception):
        pass

    def run_clip(self, direction, checkpoint):
        import torch
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        values = S.normalise({"input_folder": str(root), "output_folder": str(root), "hires_blend_folder": str(root)})
        eng = self.E.InpaintingEngine(S.run_settings(values), {"input": str(root), "output": str(root),
                                                               "hires": str(root)}, values, Hooks(), threading.Event())
        seen = {}
        hires = root / "clip_hires.mp4"
        hires.write_bytes(b"hi")
        eng._setup_video_info_and_hires = lambda *a, **k: (str(root / "out.mp4"), {
            "base_video_name": "clip", "video_name_for_output": "clip", "hires_video_path": str(hires)})

        def load(path, signature):
            seen["signature"] = signature
            if checkpoint is None:      # only the signature is wanted: stop here
                raise self._Done()
            return checkpoint
        eng._load_prefinalize_checkpoint = load
        n = 6
        # the blend mask as _prepare_video_inputs builds it: always forward order
        forward_mask = torch.arange(n, dtype=torch.float32).view(n, 1, 1, 1).expand(n, 1, 4, 4).clone()
        eng._prepare_video_inputs = lambda **kw: (None, None, None, n, 4, 4, {}, 24.0, None, forward_mask)

        def finalize(**kw):
            seen["finalize"] = kw
            raise self._Done()
        eng._finalize_output_frames = finalize
        try:
            eng.process_single_video(pipeline=None, input_video_path=str(root / "clip_splatted2.mp4"),
                                     save_dir=str(root), frames_chunk=4, overlap=1, inpaint_direction=direction)
        except self._Done:
            pass
        return seen

    def reverse_checkpoint(self, n=6):
        import torch
        # what a Reverse run saves: everything still in reversed time order
        rev = torch.arange(n - 1, -1, -1, dtype=torch.float32).view(n, 1, 1, 1)
        return {"frames_output_final": rev.expand(n, 3, 4, 4).clone(), "frames_mask_processed": rev.expand(n, 1, 4, 4).clone(),
                "frames_warped_original": rev.expand(n, 3, 4, 4).clone(), "frames_left_original": None,
                "video_stream_info": {}, "fps": 24.0}

    def test_signature_carries_direction_and_hires_source(self):
        fwd = self.run_clip("Forward", None)["signature"]
        rev = self.run_clip("Reverse", None)["signature"]
        strip = lambda sig: {k: v for k, v in sig.items() if k not in ("input", "hires")}  # noqa: E731
        self.assertNotEqual(strip(fwd), strip(rev), "a Forward checkpoint would pass for a Reverse run")
        self.assertEqual(rev.get("inpaint_direction"), "Reverse")
        self.assertTrue(str(fwd.get("hires", {}).get("path", "")).endswith("clip_hires.mp4"), fwd.get("hires"))

    def test_reverse_resume_mask_lines_up_with_frames(self):
        kw = self.run_clip("Reverse", self.reverse_checkpoint())["finalize"]
        frames = kw["inpainted_frames"] if "inpainted_frames" in kw else next(
            v for k, v in kw.items() if k != "mask_frames" and hasattr(v, "shape") and v.shape[1] == 3)
        mask = kw["mask_frames"]
        f_idx = [int(frames[i, 0, 0, 0]) for i in range(frames.shape[0])]
        m_idx = [int(mask[i, 0, 0, 0]) for i in range(mask.shape[0])]
        self.assertEqual(f_idx, list(range(6)), "frames not back in forward order")
        self.assertEqual(m_idx, f_idx, "the blend mask is time-reversed against the frames")


class StopTests(unittest.TestCase):
    """Stop now vs Stop after this chunk (audit 2026-09-28 #13, user
    2026-09-29), through process_single_video's real chunk loop -- the model
    (spatial_tiled_process, decode, tensor2vid) stood in for, five denoising
    steps a chunk. Stop now ends the chunk at its next step; the safe stop
    lets it finish and keeps its checkpoint for the next run."""

    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting import engine as E
        cls.E = E

    def run_clip(self, press):
        """press(eng, chunk, step): called at each stood-in denoising step."""
        import torch
        from PIL import Image
        E = self.E
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "clip_splatted2.mp4").write_bytes(b"x")
        values = S.normalise({"input_folder": str(root), "output_folder": str(root), "hires_blend_folder": ""})
        stop = threading.Event()
        eng = E.InpaintingEngine(S.run_settings(values), {"input": str(root), "output": str(root), "hires": ""},
                                 values, Hooks(), stop)
        eng.stop_now = threading.Event()
        eng._setup_video_info_and_hires = lambda *a, **k: (str(root / "out.mp4"), {
            "base_video_name": "clip", "video_name_for_output": "clip", "hires_video_path": None})
        n = 12
        frames = torch.rand(n, 3, 8, 8)
        mask = torch.zeros(n, 1, 8, 8)
        eng._prepare_video_inputs = lambda **kw: (frames, mask, None, n, 8, 8, {}, 24.0, frames.clone(), mask.clone())
        eng._finalize_output_frames = lambda **kw: (_ for _ in ()).throw(AssertionError("finalized a stopped clip"))
        steps = []   # (chunk, step) the stood-in model ran

        def tiled(cond_frames=None, callback_on_step_end=None, num_inference_steps=5, **kw):
            chunk = len({c for c, _ in steps})
            for step in range(num_inference_steps):
                steps.append((chunk, step))
                press(eng, chunk, step)
                if callback_on_step_end is not None:
                    callback_on_step_end(None, step, 0, {})
            return torch.zeros(cond_frames.shape[0], 4, 1, 1)

        class Pipe:
            vae = types.SimpleNamespace(to=lambda **kw: None)
            image_processor = None

            @staticmethod
            def decode_latents(latents, num_frames, decode_chunk_size):
                return latents

        saved = {k: getattr(E, k) for k in ("spatial_tiled_process", "tensor2vid")}
        self.addCleanup(lambda: [setattr(E, k, v) for k, v in saved.items()])
        E.spatial_tiled_process = tiled
        E.tensor2vid = lambda d, proc, output_type="pil": [[Image.new("RGB", (8, 8))] * d.shape[1]]
        result = eng.process_single_video(pipeline=Pipe(), input_video_path=str(root / "clip_splatted2.mp4"),
                                          save_dir=str(root), frames_chunk=6, overlap=2, num_inference_steps=5,
                                          stop_event=stop)
        chunks = sorted(p.name for p in (root / "_inpaint_resume").glob("*.chunk_*.pt"))
        return result, steps, chunks

    def test_stop_now_ends_the_chunk_at_its_next_step(self):
        def press(eng, chunk, step):
            if (chunk, step) == (1, 1):     # Stop now during chunk 2's second step
                eng.stop_now.set()
                eng.stop_event.set()
        result, steps, chunks = self.run_clip(press)
        self.assertEqual(result, (False, None))
        self.assertEqual([s for s in steps if s[0] == 1], [(1, 0), (1, 1)], "the chunk ran on after Stop now")
        self.assertEqual(len(chunks), 1, "chunk 1's checkpoint should be kept for the next run")

    def test_stop_after_this_chunk_finishes_it_and_keeps_it(self):
        def press(eng, chunk, step):
            if (chunk, step) == (1, 1):     # the safe stop during chunk 2
                eng.stop_event.set()
        result, steps, chunks = self.run_clip(press)
        self.assertEqual(result, (False, None))
        self.assertEqual([s for s in steps if s[0] == 1], [(1, i) for i in range(5)], "the chunk was cut off")
        self.assertEqual(len({c for c, _ in steps}), 2, "a third chunk was started")
        self.assertEqual(len(chunks), 2, "both finished chunks' checkpoints should be kept")


class RestoreFinishedTests(unittest.TestCase):
    """audit 2026-09-28 (minor): with one clip picked as the input, Restore
    Finished looked in <clip.mp4>/finished and found nothing; an empty Hi-Res
    folder was read as the working folder."""

    def test_a_single_clip_input_restores_from_its_folder(self):
        from core.common.file_organizer import restore_finished_files
        from studio.stages.inpainting.controller import restore_dirs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "finished").mkdir()
            (root / "finished" / "clip_splatted2.mp4").write_bytes(b"x")
            dirs = restore_dirs(str(root / "clip_splatted2.mp4"), "")
            self.assertEqual(dirs, [(str(root), "finished")])
            restored, errors, _failed = restore_finished_files(restore_dirs=dirs)
            self.assertEqual((restored, errors), (1, 0))
            self.assertTrue((root / "clip_splatted2.mp4").is_file())

    def test_a_hires_folder_is_added_once(self):
        from studio.stages.inpainting.controller import restore_dirs
        self.assertEqual(restore_dirs("A:/low", "A:/hi"), [("A:/low", "finished"), ("A:/hi", "finished")])
        self.assertEqual(restore_dirs("A:/low", "A:/low/"), [("A:/low", "finished")])


class StopBeforeLoadTests(unittest.TestCase):
    """audit 2026-09-28 (minor): Stop pressed while the job waited for the GPU
    or scanned its clips was only seen after the whole SVD pipeline had
    loaded (tens of seconds, several GB of VRAM) -- for nothing."""

    def test_a_stop_before_the_load_skips_it(self):
        from studio.stages.inpainting import engine as E
        loads = []
        real = E.load_inpainting_pipeline
        E.load_inpainting_pipeline = lambda **kw: loads.append(kw)
        self.addCleanup(setattr, E, "load_inpainting_pipeline", real)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "clip_splatted2.mp4").write_bytes(b"x")
            values = S.normalise({"input_folder": str(root), "output_folder": str(root), "hires_blend_folder": ""})
            hooks, stop = Hooks(), threading.Event()
            eng = E.InpaintingEngine(S.run_settings(values), {"input": str(root), "output": str(root), "hires": ""},
                                     values, hooks, stop)
            stop.set()                                   # pressed while it waited / scanned
            eng.run_batch_process(str(root), str(root), 5, 1, "none", 16, 4, 0.0, 23, -1, None, "left")
            for _ in range(50):
                if hooks.finish_calls:
                    break
                time.sleep(0.02)
        self.assertEqual(loads, [], "the pipeline was loaded after Stop")
        self.assertEqual(hooks.finish_calls, [True])



class LoadSettingsTests(unittest.TestCase):
    """bug sweep 2026-10-14c: Load Settings turned DNxHR back on past the one-time reset (the page's
    own 'reviewed' was used, not the file's), and a file's Post-Inpainting Blend was dropped silently."""

    def _load(self, data):
        from studio.tests._qt import app as _qt_app
        _app = _qt_app()
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        from studio.tests.test_splatting import _Shell
        from studio.stages.inpainting.controller import InpaintingController
        with tempfile.TemporaryDirectory() as d:
            saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
            ss.CONFIG_DIR, jh._RECORD_PATH = Path(d) / "config", str(Path(d) / "last_jobs.json")
            try:
                ctl = InpaintingController(_Shell())
                toasts = []
                ctl.toast.connect(lambda *a: toasts.append(a))
                f = Path(d) / "old.json"
                f.write_text(json.dumps(data), encoding="utf-8")
                ctl.loadSettingsFrom(str(f))
                return ctl.settings.value("dnxhr_fullres_split"), toasts
            finally:
                ss.CONFIG_DIR, jh._RECORD_PATH = saved

    def test_an_old_file_does_not_turn_dnxhr_back_on(self):
        dnx, _ = self._load({"dnxhr_fullres_split": True})
        self.assertIs(dnx, False)

    def test_a_reviewed_file_keeps_its_dnxhr(self):
        dnx, _ = self._load({"dnxhr_fullres_split": True, "inpaint_dnxhr_reviewed": True})
        self.assertIs(dnx, True)

    def test_a_files_retired_blend_is_said(self):
        _, toasts = self._load({"enable_post_inpainting_blend": True})
        self.assertIn("Post-Inpainting Blend is retired", toasts[-1][2], toasts)

class SiblingReleaseTests(unittest.TestCase):
    """audit 2026-09-28 (minor): V1's Start released V2's resident engine
    before checking its own settings -- a refused start still unloaded V2."""

    def test_a_refused_start_leaves_v2_loaded(self):
        from PySide6.QtCore import QCoreApplication
        from studio.tests._qt import app as _qt_app
        _app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        from studio.tests.test_splatting import _Shell
        from studio.stages.inpainting.controller import InpaintingController
        with tempfile.TemporaryDirectory() as d:
            saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
            ss.CONFIG_DIR, jh._RECORD_PATH = Path(d) / "config", str(Path(d) / "last_jobs.json")
            try:
                ctl = InpaintingController(_Shell())
                released = []

                class _V2:
                    def is_busy(self):
                        return False

                    def release_for_sibling(self):
                        released.append(1)
                        return True
                ctl.sibling = _V2()
                ctl._map.insert("input_folder", str(Path(d) / "missing"))
                ctl._map.insert("output_folder", str(Path(d) / "missing"))
                toasts = []
                ctl.toast.connect(lambda *a: toasts.append(a))
                ctl.start()
                self.assertTrue(toasts and toasts[-1][1] == "Cannot start", toasts)
                self.assertEqual(released, [], "V2 was unloaded for a start that never happened")
                ctl._poll.stop() if hasattr(ctl, "_poll") else None
            finally:
                ss.CONFIG_DIR, jh._RECORD_PATH = saved


    def test_a_start_the_claim_refuses_leaves_v2_loaded(self):
        # bug sweep 2026-10-09 (A7): another stage running -- the claim says no --
        # but V2's 14B engine had already been released
        from studio.tests._qt import app as _qt_app
        _app = _qt_app()
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        from studio.tests.test_splatting import _Shell
        from studio.stages.inpainting.controller import InpaintingController
        with tempfile.TemporaryDirectory() as d:
            saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
            ss.CONFIG_DIR, jh._RECORD_PATH = Path(d) / "config", str(Path(d) / "last_jobs.json")
            try:
                ctl = InpaintingController(_Shell())
                released, claims = [], []

                class _V2:
                    def is_busy(self):
                        return False

                    def release_for_sibling(self):
                        released.append(1)
                        return True

                class _Jobs:
                    answer = False

                    def claim(self, stage, c):
                        claims.append(stage)
                        return _Jobs.answer
                ctl.sibling, ctl.jobs = _V2(), _Jobs()
                (Path(d) / "in").mkdir()
                (Path(d) / "out").mkdir()
                ctl._map.insert("input_folder", str(Path(d) / "in"))
                ctl._map.insert("output_folder", str(Path(d) / "out"))
                ctl.start()
                self.assertEqual(claims, ["inpainting"])
                self.assertEqual(released, [], "V2 was unloaded for a start the claim refused")
                # ...and a granted claim does release it (the run's thread stubbed: nothing loads)
                from unittest import mock
                import studio.stages.inpainting.controller as IC
                _Jobs.answer = True
                with mock.patch.object(IC.threading, "Thread") as thread:
                    ctl.start()
                self.assertEqual(released, [1], "V2 kept for a start that went ahead")
                thread.return_value.start.assert_called_once()
                ctl._busy = False
                ctl._poll.stop() if hasattr(ctl, "_poll") else None
            finally:
                ss.CONFIG_DIR, jh._RECORD_PATH = saved


class SettingsTests(unittest.TestCase):
    def test_validate_matches_v1_rules(self):
        ok, err = S.validate(S.normalise({}))
        self.assertEqual(err, "")
        self.assertIsNone(ok["single_clip_id"])
        for bad in ({"num_inference_steps": "0"}, {"process_length": "0"}, {"original_input_blend_strength": "1.5"},
                    {"frames_chunk": "x"}):
            self.assertIsNone(S.validate(S.normalise(bad))[0], bad)

    def test_overlap_must_be_at_most_half_the_chunk(self):
        # bug sweep 2026-10-09 #10: 12 of 23 made a 100-frame clip 106 frames, cut back to 100
        for chunk, overlap in (("23", "23"), ("23", "40"), ("23", "12"), ("24", "13")):
            ok, err = S.validate(S.normalise({"frames_chunk": chunk, "frame_overlap": overlap}))
            self.assertIsNone(ok, (chunk, overlap))
            self.assertIn("Overlap", err)
        for chunk, overlap in (("23", "11"), ("24", "12"), ("25", "10")):
            self.assertEqual(S.validate(S.normalise({"frames_chunk": chunk, "frame_overlap": overlap}))[1], "")

    def test_seed_fallbacks(self):
        seeded = S.seed_from_v1({"mask_initial_threshold": "0.4", "mask_blur_kernel_size": "9",
                                 "debug_mode_enabled": True, "window_x": 5})
        self.assertEqual(seeded["inpaint_mask_initial_threshold"], "0.4")
        self.assertEqual(seeded["inpaint_mask_post_threshold"], "0.4")
        self.assertEqual(seeded["inpaint_mask_blur_kernel_size"], "9")
        self.assertIs(seeded["debug_mode"], True)
        self.assertNotIn("window_x", seeded)

    def test_real_v1_config_round_trips(self):
        path = REPO / "config_inpaint.json"
        if not path.exists():
            self.skipTest("no v1 config on this machine")
        v1 = json.loads(path.read_text(encoding="utf-8-sig"))
        values = S.normalise(S.seed_from_v1(v1))
        for key in S.DEFAULTS:
            # Enable DNxHR did nothing in Inpainting before 2026-10-11: set off once (settings.normalise)
            if key in v1 and key != "dnxhr_fullres_split":
                self.assertEqual(str(values[key]), str(S.coerce(key, v1[key])), key)
        self.assertFalse(values["dnxhr_fullres_split"])
        self.assertEqual(S.validate(values)[1], "")


if __name__ == "__main__":
    unittest.main()
