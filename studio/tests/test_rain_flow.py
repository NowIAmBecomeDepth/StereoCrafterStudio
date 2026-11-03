"""Rain's Flow setting (user, 2026-11-02): DIS or SEA-RAFT for Moving -- the setting, where it is saved,
and that the GPU extractor's flow follows it. No model and no footage: SEA-RAFT is a stand-in here."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.splatting.rain as R


class SettingTest(unittest.TestCase):
    def test_dis_unless_raft(self):
        self.assertEqual(R.RainSettings.from_values(mode="moving").flow, "dis")
        self.assertEqual(R.RainSettings.from_values(mode="moving", flow="raft").flow, "raft")
        self.assertEqual(R.RainSettings.from_values(mode="moving", flow="RAFT").flow, "raft")
        self.assertEqual(R.RainSettings.from_values(mode="moving", flow="farneback").flow, "dis")
        self.assertEqual(R.RainSettings.from_values(mode="moving", flow=None).flow, "dis")
        self.assertEqual(R.RainSettings.from_values(mode="moving", flow="raft_plain").flow, "raft_plain")

    def test_no_relight_only_for_its_own_choice(self):
        """SEA-RAFT, no Relight (user, 2026-11-02): SEA-RAFT, without Relight and the texture test."""
        s = {f: R.RainSettings.from_values(mode="moving", flow=f) for f in R.FLOWS}
        self.assertEqual([R.uses_raft(s[f]) for f in R.FLOWS], [False, True, True])
        self.assertEqual([R.lit_guards(s[f]) for f in R.FLOWS], [True, True, False])

    def test_no_relight_skips_them_in_the_extractor(self):
        """The CPU extractor on a lit frame: Relight and the texture test ran for DIS and not for no Relight."""
        rng = np.random.default_rng(2)
        base = (rng.random((96, 160, 3)) * 120 + 40).astype(np.uint8)
        win = np.stack([base] * 7); win[3] = np.clip(base.astype(np.int32) * 3 // 2, 0, 255).astype(np.uint8)
        got = {}
        for f in ("dis", "raft_plain"):
            ext = R.RainExtractor(R.RainSettings.from_values(mode="static", flow=f), 160, device="cpu")
            ext.extract_lengths(win, [3])
            got[f] = getattr(ext, "last_texture", None)
        self.assertIsNotNone(got["dis"])
        self.assertIsNone(got["raft_plain"])

    def test_saved_with_the_clip_and_sent_to_the_render(self):
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertIn("rain_flow", SidecarConfigManager.SIDECAR_KEY_MAP)
        self.assertIn("rain_flow", SidecarConfigManager.KEEP_GUI_WHEN_MISSING)
        from core.splatting.batch_processing import ProcessingSettings
        self.assertEqual(ProcessingSettings.__dataclass_fields__["rain_flow"].default, "dis")
        from studio.stages.splatting import settings as S
        self.assertEqual(S.DEFAULTS["rain_flow"], "dis")

    def test_the_preview_redraws_on_it(self):
        from core.ui import preview_buffer
        src = Path(preview_buffer.__file__).read_text(encoding="utf-8")
        self.assertIn('params.get("rain_flow"', src)


class FlowSwitchTest(unittest.TestCase):
    """rain_gpu._flows: SEA-RAFT with flow raft and its checkpoint there, else DIS."""

    def setUp(self):
        try:
            import torch  # noqa: F401
            from core.splatting import rain_gpu as G
        except Exception as e:                     # no torch: nothing to test here
            self.skipTest(str(e))
        self.G = G
        self._ready, self._flow = G.raft_ready, None
        import core.removal.flow_fill as FF
        self.FF = FF
        self._Flow = FF._Flow
        G.release_flow()

    def tearDown(self):
        self.G.raft_ready = self._ready
        self.FF._Flow = self._Flow
        self.G.release_flow()

    def _ext(self, flow):
        class Ext:
            pass
        e = Ext()
        e.s = R.RainSettings.from_values(mode="moving", flow=flow)
        e.ops = self.G.Ops("cpu")
        return e

    def _greys(self):
        rng = np.random.default_rng(3)
        base = (rng.random((60, 90)) * 255).astype(np.uint8)
        return [np.roll(base, k, axis=1) for k in range(3)]

    def test_raft_stand_in_gives_dis_shaped_flows_in_half_size_px(self):
        import torch
        calls = []

        class Stand:
            def __init__(self):
                pass

            def __call__(self, a, b):
                calls.append(tuple(a.shape))
                f = torch.zeros(a.shape[0], 2, a.shape[2], a.shape[3])
                f[:, 0], f[:, 1] = 1.0, 0.5           # px at the network's size
                return f

            def release(self):
                pass
        self.FF._Flow = Stand
        self.G.raft_ready = lambda: True
        self.G._flows(self._ext("raft_plain"), self._greys(), 1, [0, 2])    # no Relight: SEA-RAFT too
        self.assertEqual(len(calls), 1)
        calls.clear()
        out = self.G._flows(self._ext("raft"), self._greys(), 1, [0, 2])
        self.assertEqual(calls, [(4, 3, 30, 45)])              # one batch: 2 pairs each way, at RAFT_SCALE
        self.assertEqual(set(out), {0, 2})
        fwd, back = out[2]
        self.assertEqual(fwd.shape, (60, 90, 2))
        self.assertAlmostEqual(float(np.median(fwd[..., 0])), 2.0, places=4)   # back to the greys' px
        self.assertAlmostEqual(float(np.median(fwd[..., 1])), 1.0, places=4)
        self.assertIsNotNone(self.G._RAFT)
        self.G.release_flow()
        self.assertIsNone(self.G._RAFT)

    def test_dis_when_asked_or_when_the_checkpoint_is_missing(self):
        def boom(*a, **k):
            raise AssertionError("SEA-RAFT used")
        self.FF._Flow = boom
        self.G.raft_ready = lambda: True
        out = self.G._flows(self._ext("dis"), self._greys(), 1, [0, 2])
        self.assertEqual(out[0][0].shape, (60, 90, 2))
        self.G.raft_ready = lambda: False
        out = self.G._flows(self._ext("raft"), self._greys(), 1, [0, 2])
        self.assertEqual(out[2][0].shape, (60, 90, 2))


class PairOnceTest(unittest.TestCase):
    """A batch works each frame pair's flow out once (user, 2026-11-02: "work out the speedup"): frame t's
    flow to k is k's flow back to t. DIS's flows -- and so what is found -- are the same as before."""

    def setUp(self):
        try:
            from core.splatting import rain_gpu as G
        except Exception as e:
            self.skipTest(str(e))
        self.G = G
        self._pf = G._pair_flows

    def tearDown(self):
        self.G._pair_flows = self._pf

    def test_each_pair_once_across_the_centres(self):
        G = self.G
        asked = []

        def counting(ext, greys, t, ks):
            asked.extend(frozenset((t, k)) for k in ks)
            return self._pf(ext, greys, t, ks)
        G._pair_flows = counting

        class Ext:
            pass
        e = Ext(); e.s = R.RainSettings.from_values(mode="moving"); e.ops = G.Ops("cpu")
        rng = np.random.default_rng(5)
        greys = [(rng.random((40, 64)) * 255).astype(np.uint8) for _ in range(10)]
        e._pairs = {}
        got = {t: G._flows(e, greys, t, [k for k in range(max(0, t - 3), min(10, t + 4)) if k != t])
               for t in range(3, 7)}
        self.assertEqual(len(asked), len(set(asked)), "a pair worked out twice")
        e._pairs = None
        for t, flows in got.items():                       # the same flows as one centre alone
            alone = self._pf(e, greys, t, list(flows))
            for k, (f_tk, f_kt) in flows.items():
                self.assertTrue(np.array_equal(f_tk, alone[k][0]) and np.array_equal(f_kt, alone[k][1]), (t, k))
        # centres 3..6, 3 each side: 6 + 5 + 4 + 3 = 18 pairs worked out, not 4 x 6 = 24
        self.assertEqual(len(set(asked)), 18)


if __name__ == "__main__":
    unittest.main()
