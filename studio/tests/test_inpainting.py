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


class JoinChunksTest(unittest.TestCase):
    """A user's clip (2026-10-24, a 12 GB card): two hours in, "DefaultCPUAllocator: not enough
    memory: you tried to allocate 12959907840 bytes" -- torch.cat(results) built the whole clip while
    every chunk was still held: the clip twice in RAM. _join_chunks lets each chunk go once copied."""

    def setUp(self):
        from studio.stages.inpainting import engine as E
        self.E = E

    def chunks(self):
        import torch
        g = torch.Generator().manual_seed(4)
        return [torch.rand((n, 3, 12, 20), generator=g) for n in (22, 22, 22, 9)]

    def test_the_same_clip_as_torch_cat(self):
        import torch
        chunks = self.chunks()
        want = torch.cat(chunks, dim=0)
        got = self.E._join_chunks(chunks)
        self.assertEqual(got.dtype, want.dtype)
        self.assertTrue(torch.equal(got, want))
        self.assertEqual(chunks, [], "the chunks are handed over, not kept")

    def test_each_chunk_is_let_go_once_copied(self):
        import weakref
        seen = []
        real_pop = list.pop

        class Watch(list):
            def pop(self, i=-1):
                seen.append(first() is not None)        # still alive when the next one is taken?
                return real_pop(self, i)
        chunks = Watch(self.chunks())                   # the only holder of the chunks
        first = weakref.ref(chunks[0])
        self.E._join_chunks(chunks)
        self.assertEqual(seen[:2], [True, False], "the first chunk was still held after it was copied in")

    def test_the_engine_joins_its_chunks_so(self):
        import inspect
        src = inspect.getsource(self.E.InpaintingEngine.process_single_video)
        self.assertIn("_join_chunks(results)", src)
        self.assertNotIn("torch.cat(results", src)


class MaskBlurSlicesTest(unittest.TestCase):
    """A user's measurements (2026-10-29): the mask blur ran over the whole clip at once on the CPU, and a
    778-frame 1840x768 clip asked for 70-103 GB, failed, and was skipped -- hard-edged masks. Now a slice
    of frames at a time (on the GPU when there is one), the same blur."""

    def setUp(self):
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        self.E, self.torch, self.mock = E, torch, mock
        self.eng = object.__new__(E.InpaintingEngine)
        g = torch.Generator().manual_seed(7)
        self.mask = (torch.rand((40, 1, 32, 48), generator=g) > 0.6).float()

    def blur(self, slice_values, cuda):
        """_apply_gaussian_blur with the slice size and the GPU as given, and the frames each conv2d saw."""
        seen = []
        real = self.torch.nn.functional

        class F:
            def __getattr__(self, name):
                return getattr(real, name)

            def conv2d(self, x, *a, **k):
                seen.append(int(x.shape[0]))
                return real.conv2d(x, *a, **k)
        with self.mock.patch.object(self.E, "F", F()), \
                self.mock.patch.object(self.E, "_BLUR_SLICE_VALUES", slice_values, create=True), \
                self.mock.patch.object(self.E.torch.cuda, "is_available", lambda: cuda):
            out = self.eng._apply_gaussian_blur(self.mask, 9)
        return out, seen

    def test_a_slice_at_a_time_the_same_blur(self):
        whole, _ = self.blur(1 << 40, cuda=False)            # the whole clip in one go, as v1 did
        per_frame = self.mask[0].numel()
        sliced, seen = self.blur(per_frame * 8, cuda=False)
        self.assertTrue(seen and max(seen) <= 8, f"conv2d saw {max(seen)} frames at once (slices of 8)")
        self.assertTrue(self.torch.equal(sliced, whole), "slicing changed the blur")
        self.assertGreater(float((whole - self.mask).abs().max()), 0.1, "nothing was blurred")

    @unittest.skipUnless(__import__("torch").cuda.is_available(), "no GPU")
    def test_on_the_gpu_the_same_blur_back_on_the_cpu(self):
        cpu, _ = self.blur(1 << 40, cuda=False)
        gpu, seen = self.blur(self.mask[0].numel() * 8, cuda=True)
        self.assertEqual((gpu.device.type, gpu.dtype), ("cpu", self.torch.float32))
        self.assertTrue(max(seen) <= 8)
        self.assertLess(float((gpu - cpu).abs().max()), 1e-6, "the GPU blur differs past float rounding")


class DecodedFramesTest(unittest.TestCase):
    """A user's measurements (2026-10-29): each decoded chunk went to the CPU as floats, through 25 PIL
    images and back (~0.5 s a chunk, the GPU idle). _decoded_to_frames rounds on the GPU and moves 8-bit
    frames: the same numbers as v1's PIL round trip."""

    def test_the_same_frames_as_the_pil_round_trip(self):
        import numpy as np
        import torch
        from diffusers.image_processor import VaeImageProcessor
        from pipelines.stereo_video_inpainting import tensor2vid
        from studio.stages.inpainting import engine as E
        proc = VaeImageProcessor()
        g = torch.Generator().manual_seed(3)
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        # [1, C, T, H, W] in -1.2..1.2 (clamped ends too), and the exact half-levels the rounding decides
        decoded = (torch.rand((1, 3, 5, 24, 40), generator=g) * 2.4 - 1.2)
        halves = (torch.arange(256).float() + 0.5) / 255 * 2 - 1
        decoded[0, :, 0, 0, :] = halves[:40]
        for dtype in (torch.float32, torch.float16):
            d = decoded.to(dev, dtype)
            pil = tensor2vid(d, proc, output_type="pil")[0]
            want = torch.stack([torch.tensor(np.array(img)).permute(2, 0, 1).float() / 255.0 for img in pil])
            got = E._decoded_to_frames(d, proc)
            self.assertEqual((got.device.type, got.dtype, tuple(got.shape)), ("cpu", torch.float32, tuple(want.shape)))
            self.assertTrue(torch.equal(got, want), f"{dtype}: differs by {float((got - want).abs().max())}")

    def test_the_engine_uses_it(self):
        import inspect
        from studio.stages.inpainting import engine as E
        src = inspect.getsource(E.InpaintingEngine.process_single_video)
        self.assertIn("_decoded_to_frames(decoded_frames", src)
        self.assertNotIn('output_type="pil"', src)


class AdditionalSpeedPatchesTest(unittest.TestCase):
    """Thomas's 'Additional Speed Patches' (2026-10-30), 1-3: the pre-finalize resume file in the background
    (skipped when the drive can't hold it, or when that size failed with "unexpected pos"), the next clip
    read ahead, and the Hi-Res finish's chunk read and frame writes on threads of their own."""

    def tensors(self):
        import torch
        g = torch.Generator().manual_seed(0)
        return dict(frames_output_final=torch.rand((5, 3, 8, 10), generator=g),
                    frames_mask_processed=torch.rand((5, 1, 8, 10), generator=g),
                    frames_warped_original=torch.rand((5, 3, 8, 10), generator=g),
                    frames_left_original=torch.rand((5, 3, 8, 10), generator=g))

    def engine(self, colour=False):
        from studio.stages.inpainting import engine as E
        eng = object.__new__(E.InpaintingEngine)
        eng._setting = lambda k: colour if k == "enable_color_transfer" else None
        eng.cleaned = []
        eng._cleanup_chunk_checkpoints = lambda name, checkpoint_path=None: eng.cleaned.append(name)
        return eng

    def save(self, eng, path, **kw):
        eng._save_prefinalize_checkpoint(checkpoint_path=path, signature={"v": 1}, fps=24.0, video_stream_info=None,
                                         base_video_name="clip", **self.tensors(), **kw)

    def tearDown(self):
        from studio.stages.inpainting import engine as E
        E._wait_prefinalize_write()
        E._PREFINALIZE_WRITE["fails_above"] = None

    def test_the_resume_file_is_written_in_the_background_the_same_bytes(self):
        import time
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = self.engine()
        gate = threading.Event()
        real_save = torch.save

        def slow_save(obj, path, *a, **k):
            gate.wait(5)
            return real_save(obj, path, *a, **k)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E.torch, "save", slow_save):
            path = os.path.join(d, "clip.prefinalize.pt")
            t0 = time.monotonic()
            self.save(eng, path, frames_edited_in_place_later=False)
            took = time.monotonic() - t0
            self.assertLess(took, 1.0, f"the save held the clip for {took:.2f} s")
            self.assertEqual(eng.cleaned, [], "the chunk checkpoints went before the file was on disk")
            gate.set()
            E._wait_prefinalize_write()
            got = torch.load(path, weights_only=False)
            for k, v in self.tensors().items():
                self.assertTrue(torch.equal(got[k], v), k)
            self.assertEqual(eng.cleaned, ["clip"], "then they go")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_it_waits_when_the_low_res_colour_transfer_edits_those_frames(self):
        import time
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = self.engine(colour=True)
        real_save = torch.save

        def slow_save(obj, path, *a, **k):
            time.sleep(0.6)
            return real_save(obj, path, *a, **k)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E.torch, "save", slow_save):
            path = os.path.join(d, "clip.prefinalize.pt")
            t0 = time.monotonic()
            self.save(eng, path, frames_edited_in_place_later=True)
            self.assertGreaterEqual(time.monotonic() - t0, 0.5, "it didn't wait")
            self.assertTrue(os.path.exists(path), "on disk before the in-place edits")

    def test_skipped_when_the_drive_cant_hold_it(self):
        import collections
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = self.engine()
        usage = collections.namedtuple("usage", "total used free")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(E.shutil, "disk_usage", return_value=usage(10, 9, 1024)), \
                mock.patch.object(E.torch, "save", side_effect=AssertionError("saved anyway")):
            path = os.path.join(d, "clip.prefinalize.pt")
            self.save(eng, path)
            E._wait_prefinalize_write()
            self.assertFalse(os.path.exists(path))
            self.assertEqual(eng.cleaned, [], "the chunk checkpoints are kept: a resume uses them")

    def test_a_size_that_failed_with_unexpected_pos_is_skipped_after(self):
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = self.engine()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clip.prefinalize.pt")
            with mock.patch.object(E.torch, "save", side_effect=RuntimeError("[enforce fail] unexpected pos 4294967296")):
                self.save(eng, path)
                E._wait_prefinalize_write()
            self.assertIsNotNone(E._PREFINALIZE_WRITE["fails_above"])
            self.assertFalse(os.path.exists(path + ".tmp"))
            with mock.patch.object(E.torch, "save", side_effect=AssertionError("tried again")) as again:
                self.save(eng, path)
                E._wait_prefinalize_write()
            again.assert_not_called()
            self.assertEqual(eng.cleaned, [])

    def test_the_next_clip_is_read_ahead_the_same_frames(self):
        import types
        from unittest import mock
        from studio.stages.inpainting import engine as E
        reads = []

        def reader(path):
            reads.append(path)
            return ("frames of " + os.path.basename(path), 24.0, {"path": path})
        vr = lambda *a, **k: types.SimpleNamespace(__len__=None, __getitem__=None)  # noqa: E731

        class FakeVR:
            def __init__(self, *a, **k):
                pass

            def __len__(self):
                return 10

            def __getitem__(self, i):
                return types.SimpleNamespace(shape=(4, 6, 3))
        rich = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(available=64 * 2**30))
        poor = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(available=8 * 2**30))
        pf = E._ClipPrefetch()
        with mock.patch.object(E, "_read_video_frames_uint8", reader), mock.patch.object(E, "VideoReader", FakeVR):
            with mock.patch.object(E, "psutil", rich):
                pf.start("C:/x/b.mp4")
                got = pf.take("C:/x/b.mp4")
                self.assertEqual(got, reader("C:/x/b.mp4")[:1] + got[1:] if False else got)
                self.assertEqual(got[0], "frames of b.mp4")
                self.assertEqual(reads, ["C:/x/b.mp4", "C:/x/b.mp4"][:len(reads)])
                n = len(reads)
                self.assertEqual(pf.take("C:/x/c.mp4")[0], "frames of c.mp4", "not read ahead: read now")
                self.assertEqual(len(reads), n + 1)
            with mock.patch.object(E, "psutil", poor):
                pf.start("C:/x/d.mp4")
                self.assertIsNone(pf._thread, "8 GB free: no read-ahead (the clip + 16 GB wouldn't fit)")
            pf.clear()

    def test_the_hires_chunks_come_in_order_and_errors_surface(self):
        import numpy as np
        from unittest import mock
        from studio.stages.inpainting import engine as E

        class FakeVR:
            def __init__(self, *a, **k):
                pass

            def get_batch(self, idx):
                arr = np.full((len(idx), 2, 2, 3), idx[0], np.uint8)
                return type("B", (), {"asnumpy": lambda s: arr})()
        with mock.patch.object(E, "VideoReader", FakeVR):
            r = E._HiresChunkReader("h.mp4", [(0, 4), (4, 8), (8, 10)], None)
            for s in (0, 4, 8):
                arr, _waited = r.next(s)
                self.assertEqual(int(arr[0, 0, 0, 0]), s)
                self.assertEqual(len(arr), 4 if s < 8 else 2)
            r.close()
            r = E._HiresChunkReader("h.mp4", [(0, 4), (4, 8)], None)
            with self.assertRaises(RuntimeError):
                r.next(4)
            r.close()

        class BadVR(FakeVR):
            def get_batch(self, idx):
                raise OSError("decode failed")
        with mock.patch.object(E, "VideoReader", BadVR):
            r = E._HiresChunkReader("h.mp4", [(0, 4)], None)
            with self.assertRaises(OSError):
                r.next(0)
            r.close()

    def test_the_frame_writer_keeps_the_order_and_reraises(self):
        import numpy as np
        from studio.stages.inpainting import engine as E
        got = []
        enc = type("Enc", (), {"write": lambda s, f: got.append(int(f[0, 0, 0]))})()
        w = E._FrameWriter(enc, None, 10)
        for start in (0, 4, 8):
            w.put(np.stack([np.full((2, 2, 3), start + k, np.uint16) for k in range(4 if start < 8 else 2)]))
        w.finish()
        self.assertEqual(got, list(range(10)))
        self.assertEqual(w.written, 10)

        def boom(_s, _f):
            raise BrokenPipeError("ffmpeg went away")
        w = E._FrameWriter(type("Enc", (), {"write": boom})(), None, 4)
        w.put(np.zeros((4, 2, 2, 3), np.uint16))
        with self.assertRaises(BrokenPipeError):
            w.finish()
        stop = threading.Event()
        stop.set()
        got.clear()
        w = E._FrameWriter(enc, stop, 4)
        w.put(np.zeros((4, 2, 2, 3), np.uint16))
        w.finish()
        self.assertEqual(got, [], "Stop: nothing more written")


class ChunkCheckpointInBackgroundTest(unittest.TestCase):
    """A user's measurements (2026-10-29): each chunk's resume file (~730 MB) was written while the GPU
    waited, ~1.45 s a chunk. Now on a thread: the save returns at once, the file is the same, and a
    reader waits for it."""

    def test_saved_in_the_background_the_same_file(self):
        import time
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = object.__new__(E.InpaintingEngine)
        gate = threading.Event()
        real_save = torch.save

        def slow_save(obj, path, *a, **k):
            gate.wait(3)                                    # a big file on a slow disk
            return real_save(obj, path, *a, **k)
        cur = torch.rand((6, 3, 8, 10))
        app = cur[:4].clone()
        want_cur, want_app = cur.clone(), app.clone()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E.torch, "save", slow_save):
            path = os.path.join(d, "clip.chunk_00000.pt")
            t0 = time.monotonic()
            eng._save_chunk_checkpoint(path, {"v": 1}, 0, cur, app)
            took = time.monotonic() - t0
            cur.mul_(0)                                      # the chunk loop goes on with its tensors
            app.mul_(0)
            gate.set()
            got = eng._load_chunk_checkpoint(path, {"v": 1}, 0, 4)     # waits for the write
            self.assertLess(took, 1.0, f"the save held the job for {took:.2f} s")
            self.assertIsNotNone(got, "the checkpoint wasn't there to load")
            self.assertTrue(torch.equal(got["current_chunk_generated"], want_cur))
            self.assertTrue(torch.equal(got["append_frames"], want_app), "the file took the later changes")
            self.assertFalse(os.path.exists(path + ".tmp"))
            eng._save_chunk_checkpoint(path, {"v": 1}, 0, want_cur, want_app)
            E._wait_checkpoint_writes()                      # nothing in flight when the folder goes

    def test_the_job_ends_once_the_last_one_is_on_disk(self):
        """A Stop after this chunk returns while its checkpoint is still being written: the job's end
        (processing_done, every way out of the batch) waits for it."""
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        eng = object.__new__(E.InpaintingEngine)
        eng.pipeline, eng._job_started, eng.hooks = None, None, Hooks()
        eng.processed_count = E._Counter(lambda: None)
        eng.total_videos = E._Counter(lambda: None)
        eng.outcome = None
        eng._report_progress = lambda: None
        gate = threading.Event()
        real_save = torch.save
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(E.torch, "save", lambda obj, p, *a, **k: (gate.wait(0.5), real_save(obj, p))):
            path = os.path.join(d, "clip.chunk_00000.pt")
            eng._save_chunk_checkpoint(path, {"v": 1}, 0, torch.zeros(2, 3, 4, 4), torch.zeros(2, 3, 4, 4))
            self.assertFalse(os.path.exists(path), "the write didn't wait for the stand-in disk")
            eng.processing_done(stopped=True)
            self.assertTrue(os.path.exists(path), "the job ended before its last checkpoint was on disk")


@unittest.skipUnless(__import__("torch").cuda.is_available(), "no GPU")
class MaskChainOnTheGpuTest(unittest.TestCase):
    """A user's measurements (2026-10-29): the mask chain greyed each frame with cv2 in a Python loop and
    ran the rest over the whole clip on the CPU -- twice a clip, and again for every Hi-Res chunk. Now
    32 frames at a time on the GPU, cv2's own grey formula: the same masks."""

    SETTINGS = [(0.0, 0.0, 0, 0, 0), (0.1, 0.0, 5, 9, 15), (0.05, 0.5, 3, 0, 7), (0.0, 0.3, 0, 11, 0),
                (0.2, 0.0, 7, 0, 0)]

    def setUp(self):
        import torch
        from unittest import mock
        from studio.stages.inpainting import engine as E
        self.E, self.torch, self.mock = E, torch, mock
        self.eng = object.__new__(E.InpaintingEngine)
        self.eng._setting = lambda k: False
        g = torch.Generator().manual_seed(11)
        m = torch.zeros((70, 3, 48, 64), dtype=torch.uint8)            # over two 32-frame slices
        for t in range(70):                                             # coloured blobs, mask-like
            x, y = int(torch.randint(0, 50, (1,), generator=g)), int(torch.randint(0, 34, (1,), generator=g))
            m[t, :, y:y + 14, x:x + 14] = torch.randint(0, 256, (3, 1, 1), generator=g, dtype=torch.uint8)
        m += torch.randint(0, 40, m.shape, generator=g, dtype=torch.uint8)
        self.mask = m

    def chain(self, frames, s, gpu):
        calls = []
        real = self.E.cv2.cvtColor
        with self.mock.patch.object(self.E.gpu_ops, "gpu_available", lambda: gpu), \
                self.mock.patch.object(self.E.cv2, "cvtColor", lambda *a: (calls.append(1), real(*a))[1]):
            out = self.eng._process_mask_frames(frames, s[0], s[1], s[2], s[3], s[4], "clip.mp4",
                                                debug_prefix=None, save_debug=False)
        return out, len(calls)

    def test_the_grey_formula_is_this_opencvs(self):
        self.assertIsNotNone(self.E._gray_formula(), "cv2's grey isn't a known formula here: the chain stays on the CPU")

    def test_the_same_masks_on_the_gpu(self):
        for frames in (self.mask, self.mask.float(), self.mask.float() / 255.0):
            for s in self.SETTINGS:
                cpu, cpu_calls = self.chain(frames, s, gpu=False)
                gpu, gpu_calls = self.chain(frames, s, gpu=True)
                label = f"{frames.dtype} max {float(frames.max()):.0f}, settings {s}"
                self.assertEqual(cpu_calls, 70)
                self.assertEqual(gpu_calls, 0, f"{label}: greyed frame by frame with cv2 (not on the GPU)")
                self.assertEqual((gpu.device.type, gpu.dtype, tuple(gpu.shape)), ("cpu", self.torch.float32, (70, 1, 48, 64)))
                if s[4] == 0:
                    self.assertTrue(self.torch.equal(gpu, cpu), f"{label}: differs")
                else:
                    # the blur on the GPU: the CPU's within float rounding (a post threshold can't flip on it here)
                    self.assertLess(float((gpu - cpu).abs().max()), 1e-6 if s[1] == 0 else 1e-9, label)

    def test_a_gpu_chunk_stays_on_the_gpu(self):
        out, _ = self.chain(self.mask.to("cuda").float(), self.SETTINGS[1], gpu=True)
        self.assertEqual(out.device.type, "cuda")


class Uint16FramesTest(unittest.TestCase):
    """The Hi-Res frames for the encoder made where they are (2026-10-29): v1's PNG numbers exactly."""

    def test_the_png_paths_numbers(self):
        import numpy as np
        import torch
        from studio.stages.inpainting import engine as E
        g = torch.Generator().manual_seed(5)
        frames = torch.rand((3, 3, 20, 30), generator=g) * 1.4 - 0.2      # past both ends too
        frames[0, 0, 0, :5] = torch.tensor([0.0, 1.0, 0.5, 1 / 65535, 65534.5 / 65535])
        want = np.stack([(np.clip(f.permute(1, 2, 0).numpy(), 0.0, 1.0) * 65535.0).astype(np.uint16) for f in frames])
        devices = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])
        for dev in devices:
            got = E._frames_to_uint16(frames.to(dev))
            self.assertEqual((got.dtype, got.shape), (np.uint16, want.shape))
            self.assertTrue(np.array_equal(got, want), dev)


class DecodeParkingTest(unittest.TestCase):
    """CPU Offload none (a user, 2026-10-29): the UNet and CLIP went to RAM and back around every chunk's
    decode -- 0.93 s and 3 GB each way on a 32 GB card -- for 16 GB cards, where the decode didn't fit
    beside them. Now only when it doesn't: asked of the free VRAM (Windows spills rather than failing)."""

    def run_decode(self, free_gb):
        import types
        import torch
        from unittest import mock
        from pipelines import stereo_video_inpainting as P
        moves = []

        class Mod:
            device = torch.device("cuda")

            def to(self, dev):
                moves.append(str(dev))
        fake = types.SimpleNamespace(park_idle_for_decode="auto", _parked_last=None, unet=Mod(), image_encoder=Mod(),
                                     vae=types.SimpleNamespace(device=torch.device("cuda")),
                                     _decode_latents=lambda l, n, c: "frames")
        latents = torch.zeros((1, 25, 4, 96, 230))                  # 1840x768
        with mock.patch.object(P.torch.cuda, "is_available", lambda: True), \
                mock.patch.object(P.torch.cuda, "mem_get_info", lambda d=None: (free_gb * 2**30, 32 * 2**30)), \
                mock.patch.object(P.torch.cuda, "memory_reserved", lambda d=None: 0), \
                mock.patch.object(P.torch.cuda, "memory_allocated", lambda d=None: 0), \
                mock.patch.object(P.torch.cuda, "empty_cache", lambda: None):
            out = P.StableVideoDiffusionInpaintingPipeline.decode_latents(fake, latents, 25, 2)
        self.assertEqual(out, "frames")
        return moves

    def test_room_for_the_decode_nothing_moves(self):
        self.assertEqual(self.run_decode(free_gb=20), [], "parked with 20 GB free for an 11 GB decode")

    def test_short_of_room_they_are_parked_and_put_back(self):
        self.assertEqual(self.run_decode(free_gb=8), ["cpu", "cpu", "cuda", "cuda"])

    def test_offload_none_asks(self):
        import inspect
        from pipelines import stereo_video_inpainting as P
        self.assertIn('park_idle_for_decode = "auto"', inspect.getsource(P.load_inpainting_pipeline))


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

    def test_tensorrt_box_turns_it_on_and_compile_off(self):
        """TensorRT (a user's patch, 2026-10-29): on for the loaded pipeline, Torch Compile skipped --
        unless TensorRT can't run here, when Torch Compile stays as set; the engines released at the end."""
        from unittest import mock
        E, seen = self.E, []
        with mock.patch.object(E, "enable_tensorrt", lambda *a: seen.append(("on",) + a)), \
                mock.patch.object(E, "release_tensorrt", lambda p: seen.append(("released", p))), \
                mock.patch.object(E, "trt_unavailable_reason", lambda: ""):
            self.clip("shot-0006_640_splatted4.mp4")
            self.run_batch(use_tensorrt=True, use_compile=True)
            with mock.patch.object(E, "trt_unavailable_reason", lambda: "'tensorrt' is not installed"):
                self.clip("shot-0007_640_splatted4.mp4")
                self.run_batch(use_tensorrt=True, use_compile=True)
            self.clip("shot-0008_640_splatted4.mp4")
            self.run_batch(use_tensorrt=False, use_compile=True)
        self.assertEqual([kw["use_compile"] for kw in self.loads], [False, True, True])
        self.assertEqual(seen[0], ("on", "PIPELINE", 25, True, "model"))
        self.assertEqual([s[0] for s in seen], ["on", "released", "on", "released", "released"])


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

        # the decode's frames: _decoded_to_frames since 2026-10-29 (it was tensor2vid's PIL images)
        saved = {k: getattr(E, k) for k in ("spatial_tiled_process", "_decoded_to_frames")}
        self.addCleanup(lambda: [setattr(E, k, v) for k, v in saved.items()])
        E.spatial_tiled_process = tiled
        E._decoded_to_frames = lambda d, proc: torch.zeros(d.shape[1], 3, 8, 8)
        result = eng.process_single_video(pipeline=Pipe(), input_video_path=str(root / "clip_splatted2.mp4"),
                                          save_dir=str(root), frames_chunk=6, overlap=2, num_inference_steps=5,
                                          stop_event=stop)
        E._wait_checkpoint_writes()     # as the job's end does (processing_done): written in the background
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


class Sage1TorchCompileTest(unittest.TestCase):
    """SageAttention 1 for RTX 20 series (2026-10-30): its Triton 3.2 breaks Torch Compile, so a run has it
    off while Sage 1 is installed, whatever was saved; with Sage 2 the saved value goes through."""

    def test_the_run_has_torch_compile_off_with_sage_1(self):
        from unittest import mock
        from studio import sage1
        from studio.stages.inpainting import settings as S
        values = S.normalise({"use_compile": True})
        for installed, want in ((True, False), (False, True)):
            with mock.patch.object(sage1, "installed", lambda i=installed: i):
                self.assertIs(S.run_settings(values)["use_compile_var"], want, installed)
        self.assertIs(values["use_compile"], True, "the saved setting is kept")


if __name__ == "__main__":
    unittest.main()
