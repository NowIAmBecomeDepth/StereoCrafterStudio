"""Colour tags land in the file, not just on the ffmpeg command line.

FFmpeg 7.1+ takes an encode's primaries and transfer from the frames and
ignores -color_primaries / -color_trc; on the ffmpeg Studio uses, every
encode came out "unknown" for both (found 2026-10-09 fixing bug sweep #17).
core.common.color_tags sets them on the frames with setparams. Real ffmpeg,
CPU encoders, tiny clips in a temp folder.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_color_tags -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from core.common.color_tags import with_color_tags  # noqa: E402

W, H, N = 64, 48, 4


def probe(path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_space,color_transfer,color_primaries,color_range", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


class HelperTests(unittest.TestCase):
    def test_adds_a_vf_after_the_inputs(self):
        cmd = with_color_tags(["ffmpeg", "-i", "-", "-c:v", "libx264", "-color_primaries", "bt709",
                               "-color_trc", "bt709", "out.mp4"])
        self.assertEqual(cmd[3:5], ["-vf", "setparams=color_primaries=bt709:color_trc=bt709"])

    def test_appends_to_an_existing_chain(self):
        cmd = with_color_tags(["ffmpeg", "-i", "-", "-vf", "scale=out_range=tv", "-color_trc", "smpte2084", "o.mp4"])
        self.assertEqual(cmd[cmd.index("-vf") + 1], "scale=out_range=tv,setparams=color_trc=smpte2084")

    def test_output_arguments_alone(self):
        self.assertEqual(with_color_tags(["-color_primaries", "bt2020"])[:2],
                         ["-vf", "setparams=color_primaries=bt2020"])

    def test_left_alone(self):
        for cmd in (["ffmpeg", "-i", "a.mp4", "-c:v", "copy", "-color_trc", "bt709", "o.mp4"],      # a stream copy
                    ["ffmpeg", "-i", "a.mp4", "-c", "copy", "-color_trc", "bt709", "o.mp4"],
                    ["ffmpeg", "-i", "a.mp4", "-filter_complex", "[0:v]null[v]", "-color_trc", "bt709", "o.mp4"],
                    ["ffmpeg", "-i", "a.mp4", "-color_trc", "made-up", "o.mp4"],                   # not a setparams name
                    ["ffmpeg", "-color_trc", "bt709", "-i", "a.mp4", "o.mp4"]):                    # an input option
            self.assertEqual(with_color_tags(cmd), cmd)


class EncoderTests(unittest.TestCase):
    """Each encoder Studio writes clips with: what ffprobe reads back."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        # the CPU encoders, for this test only (hiding CUDA process-wide took
        # NVENC from every test run after this one)
        from unittest import mock
        from core.common import video_io as V
        p = mock.patch.object(V, "nvenc_available", lambda *a, **k: False)
        p.start()
        self.addCleanup(p.stop)

    def feed(self, proc, frame_bytes):
        _out, err = proc.communicate(frame_bytes * N, timeout=60)
        self.assertEqual(proc.returncode, 0, err)

    def test_the_pipe_encoder_sdr(self):
        from core.common import video_io as V
        out = self.dir / "sbs.mp4"
        self.feed(V.start_ffmpeg_pipe_process(W, H, str(out), 12.0), bytes(W * H * 6))
        self.assertEqual(probe(out), {"color_range": "tv", "color_space": "bt709",
                                      "color_transfer": "bt709", "color_primaries": "bt709"})

    def test_the_pipe_encoder_keeps_a_pq_source(self):
        from core.common import video_io as V
        out = self.dir / "sbs_pq.mp4"
        info = {"color_primaries": "bt2020", "transfer_characteristics": "smpte2084", "color_space": "bt2020nc",
                "color_range": "tv", "pix_fmt": "yuv420p10le", "codec_name": "hevc", "color_tags_mode": "BT.2020 PQ"}
        self.feed(V.start_ffmpeg_pipe_process(W, H, str(out), 12.0, video_stream_info=info), bytes(W * H * 6))
        p = probe(out)
        self.assertEqual((p.get("color_primaries"), p.get("color_transfer")), ("bt2020", "smpte2084"), p)

    def test_the_dnxhr_pipe(self):
        from core.common import video_io as V
        out = self.dir / "sbs.mov"       # DNxHR refuses frames as small as W x H
        self.feed(V.start_ffmpeg_pipe_process_dnxhr(256, 144, str(out), 12.0), bytes(256 * 144 * 6))
        p = probe(out)
        self.assertEqual((p.get("color_primaries"), p.get("color_transfer")), ("bt709", "bt709"), p)

    def test_depthcrafter_save_video(self):
        import numpy as np
        from depthcrafter.utils import save_video
        out = self.dir / "a_depth.mp4"
        save_video(np.full((N, H, W), 0.5, np.float32), str(out), fps=12, output_format="main10_mp4", use_nvenc=False)
        p = probe(out)
        self.assertEqual((p.get("color_primaries"), p.get("color_transfer")), ("bt709", "bt709"), p)

    def test_erase_writer_keeps_the_source_tags(self):
        from core.removal import erase as E
        out = self.dir / "erased.mp4"
        info = {"w": W, "h": H, "rate": "12", "audio": False,
                "color": {"color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709",
                          "color_range": "tv"}}
        self.feed(E._writer(str(out), "unused.mp4", info), bytes(W * H * 6))
        p = probe(out)
        self.assertEqual((p.get("color_primaries"), p.get("color_transfer")), ("bt709", "bt709"), p)


if __name__ == "__main__":
    unittest.main()
