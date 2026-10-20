"""Start-up's environment check (release check 2026-10-17; studio/env_check.py): what a first-time user's PC
is missing for Studio -- no NVIDIA driver, one older than 580, no ffmpeg / ffprobe on PATH -- said before any
job, in plain words. The driver and PATH are stood in for: nothing here reads this PC."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import env_check as E  # noqa: E402

ALL_TOOLS = lambda tool: f"C:/ffmpeg/bin/{tool}.exe"   # noqa: E731


class EnvCheckTest(unittest.TestCase):
    def titles(self, driver, which=ALL_TOOLS):
        return [t for t, _text in E.problems(driver=lambda: driver, which=which)]

    def test_a_good_pc_has_nothing_to_say(self):
        self.assertEqual(self.titles("591.74"), [])

    def test_no_nvidia_driver(self):
        self.assertEqual(self.titles(None), ["No NVIDIA graphics card found"])

    def test_a_driver_older_than_580(self):
        found = E.problems(driver=lambda: "552.44", which=ALL_TOOLS)
        self.assertEqual([t for t, _ in found], ["NVIDIA driver too old"])
        self.assertIn("552.44", found[0][1])
        self.assertIn("580", found[0][1])
        self.assertEqual(self.titles("580.88"), [], "580 itself is new enough")

    def test_ffmpeg_or_ffprobe_missing(self):
        found = E.problems(driver=lambda: "591.74", which=lambda tool: None if tool == "ffprobe" else ALL_TOOLS(tool))
        self.assertEqual([t for t, _ in found], ["FFmpeg not found"])
        self.assertIn("ffprobe is not", found[0][1])
        both = E.problems(driver=lambda: "591.74", which=lambda tool: None)
        self.assertIn("ffmpeg and ffprobe are not", both[0][1])

    def test_everything_missing_says_each(self):
        self.assertEqual(self.titles(None, which=lambda tool: None), ["No NVIDIA graphics card found", "FFmpeg not found"])

    def test_driver_major(self):
        self.assertEqual(E.driver_major("581.57"), 581)
        self.assertIsNone(E.driver_major("n/a"))
        self.assertIsNone(E.driver_major(None))


if __name__ == "__main__":
    unittest.main()
