"""SAM 3's per-frame mask cache, kept packed in system memory (user,
2026-10-05: a 750-frame track ate the VRAM). The swapped-in cache must read
back exactly what SAM 3 put in, on the device it came from, for every way SAM 3
reads it -- [], get, items, values, copy, `in`, del, clear. No GPU or SAM 3
needed (CPU tensors stand in).

    python -m unittest studio.tests.test_sam3_mask_cache
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import sam3_backend as S  # noqa: E402


def mask(h=37, w=53, seed=0):
    g = torch.Generator().manual_seed(seed)
    return torch.rand((1, h, w), generator=g) > 0.5


class PackedCacheTests(unittest.TestCase):
    def setUp(self):
        self.state = {"cached_frame_outputs": {}}
        S._pack_mask_cache(self.state)
        self.cache = self.state["cached_frame_outputs"]

    def test_reads_back_exactly(self):
        a, b = mask(seed=1), mask(seed=2)
        self.cache[4] = {1: a, 2: b}                      # as _cache_frame_outputs sets it
        frame = self.cache[4]
        self.assertTrue(torch.equal(frame[1], a) and torch.equal(frame.get(2), b))
        self.assertEqual(frame[1].dtype, torch.bool)
        self.assertEqual(tuple(frame[1].shape), (1, 37, 53))
        self.assertEqual(frame[1].device, a.device)
        self.assertTrue(all(torch.equal(v, w) for v, w in zip(frame.values(), (a, b))))
        self.assertEqual([k for k, _v in frame.items()], [1, 2])
        copy = frame.copy()                                # _build_tracker_output
        self.assertIsInstance(copy, dict)
        self.assertTrue(torch.equal(copy[2], b))
        self.assertIsNone(frame.get(9))
        self.assertEqual(self.cache.get(99, {}), {})       # propagation_fetch on an untracked frame

    def test_stored_packed_not_as_tensors(self):
        a = mask(h=1038, w=1920, seed=3)
        self.cache[0] = {7: a}
        bits, shape, _dev = dict.__getitem__(self.cache[0], 7)
        self.assertEqual(shape, (1, 1038, 1920))
        self.assertLessEqual(bits.nbytes, a.numel() // 8 + 1)   # 1/8 of a bool tensor

    def test_removing_an_object_and_resetting(self):
        self.cache[1] = {1: mask(seed=4), 2: mask(seed=5)}
        self.cache[2] = {1: mask(seed=6)}
        for f in self.cache:                               # remove_object's loop
            fc = self.cache[f]
            if 1 in fc:
                del fc[1]
        self.assertEqual(list(dict.keys(self.cache[1])), [2])
        self.assertEqual(len(self.cache[2]), 0)
        self.cache.clear()                                 # reset_state
        self.assertEqual(len(self.cache), 0)
        self.assertIs(self.state["cached_frame_outputs"], self.cache)

    def test_swapping_twice_or_with_masks_already_there(self):
        state = {"cached_frame_outputs": {3: {1: mask(seed=7)}}}
        S._pack_mask_cache(state)
        S._pack_mask_cache(state)                          # idempotent
        self.assertIsInstance(state["cached_frame_outputs"], S._PackedFrames)
        self.assertTrue(torch.equal(state["cached_frame_outputs"][3][1], mask(seed=7)))
        S._pack_mask_cache({})                             # no cache: nothing to do
        S._pack_mask_cache(None)


class HostFramesTests(unittest.TestCase):
    """The session's frames kept in system memory (user, 2026-10-05: SAM 3
    copied the whole clip to the GPU whatever offload_video_to_cpu said)."""

    def frames(self, t=5):
        g = torch.Generator().manual_seed(1)
        # the cv2 loader's layout: (T, H, W, C) permuted, values 2x - 1 of 8-bit pixels
        return (torch.randint(0, 256, (t, 6, 7, 3), generator=g).float() * 2 - 1).permute(0, 3, 1, 2)

    def test_read_back_exactly_as_float32(self):
        f = self.frames()
        host = S._HostFrames(f.to(torch.float16), "cpu")
        self.assertEqual(len(host), 5)
        self.assertEqual(tuple(host.shape), (5, 3, 6, 7))
        self.assertTrue(torch.equal(host[2], f[2]), "float16 holds these whole numbers exactly")
        self.assertEqual(host[2].dtype, torch.float32)
        self.assertTrue(torch.equal(host[torch.tensor([1, 3])], f[[1, 3]]))

    def test_one_frame_as_the_whole_batch_in_upstreams_layout(self):
        f = self.frames()
        host = S._HostFrames(f.to(torch.float16), "cpu")
        ids = torch.tensor([3])
        virt = host.as_batch(ids)
        self.assertEqual(len(virt), 5)
        got, want = virt[ids], f[ids]
        self.assertTrue(torch.equal(got, want))
        self.assertEqual(got.stride(), want.stride(), "the stride that picks the convolution kernel")

    def test_the_detector_is_handed_the_frame_as_a_batch(self):
        f = self.frames()
        host = S._HostFrames(f.to(torch.float16), "cpu")
        seen = []

        class Detector:
            def _get_img_feats(self, backbone_out, img_ids):
                seen.append(backbone_out["img_batch_all_stages"])
                return "feats"
        det = Detector()
        S._detector_reads_host_frames(det)
        S._detector_reads_host_frames(det)              # once only
        self.assertEqual(det._get_img_feats({"img_batch_all_stages": host}, torch.tensor([2])), "feats")
        self.assertTrue(torch.is_tensor(seen[-1]) and len(seen[-1]) == 5)
        self.assertTrue(torch.equal(seen[-1][torch.tensor([2])], f[[2]]))
        plain = torch.zeros(5, 3, 6, 7)
        det._get_img_feats({"img_batch_all_stages": plain}, torch.tensor([2]))
        self.assertIs(seen[-1], plain, "a tensor batch (frames on the GPU) is passed through")
        det._get_img_feats({"img_batch_all_stages": host, "backbone_fpn": []}, torch.tensor([2]))
        self.assertIs(seen[-1], host, "features already there: nothing to read")

    def test_the_batch_copy_leaves_the_frames_in_system_memory(self):
        try:
            S._import_sam3()
            from sam3.model import sam3_video_inference as vi
            from sam3.model.data_misc import BatchedDatapoint
        except Exception as e:                              # SAM 3 not installed here
            self.skipTest(f"SAM 3 not importable: {e}")
        real = vi.copy_data_to_device
        f = self.frames()
        batch = BatchedDatapoint(img_batch=f, find_text_batch=["a"], find_inputs=[], find_targets=[], find_metadatas=[])
        with S._frames_stay_on_host():
            out = vi.copy_data_to_device(batch, torch.device("cpu"), non_blocking=True)
        self.assertIs(vi.copy_data_to_device, real, "put back afterwards")
        self.assertIsInstance(out.img_batch, S._HostFrames)
        self.assertEqual(out.img_batch._frames.dtype, torch.float16)
        self.assertTrue(torch.equal(out.img_batch[4], f[4]))
        self.assertIs(batch.img_batch, f, "the original batch is left as it was")
        self.assertEqual(out.find_text_batch, ["a"])


if __name__ == "__main__":
    unittest.main()
