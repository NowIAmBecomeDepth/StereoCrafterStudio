"""Console capture: progress bars written to stdout/stderr reach the log model.

    python -m unittest studio.tests.test_console
"""

from __future__ import annotations

import io
import logging
import unittest

from PySide6.QtCore import QCoreApplication
from tqdm import tqdm

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.log_model import LogHub, LogModel, ModelHandler, StreamTee, parse_bar

from studio.tests._qt import app as _qt_app
_app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)


def rows(model: LogModel) -> list[str]:
    QCoreApplication.processEvents()
    return [model.data(model.index(i), LogModel.TextRole) for i in range(model.rowCount())]


class StreamTeeTests(unittest.TestCase):
    def setUp(self):
        self.model = LogModel()
        self.real = io.StringIO()
        self.tee = StreamTee(self.model, self.real)

    def test_plain_lines_become_rows_and_reach_the_real_stream(self):
        self.tee.write("one\ntwo")
        self.assertEqual(rows(self.model), ["one"])      # "two" waits for its newline
        self.tee.write(" halves\n")
        self.assertEqual(rows(self.model), ["one", "two halves"])
        self.assertEqual(self.real.getvalue(), "one\ntwo halves\n")

    def test_a_tqdm_bar_is_one_row_redrawn_in_place(self):
        for _ in tqdm(range(15), file=self.tee, mininterval=0, ascii=True):
            pass
        got = rows(self.model)
        self.assertEqual(len(got), 1, got)
        self.assertIn("15/15", got[0])

    def test_consecutive_bars_each_keep_a_row(self):
        for _ in range(3):
            for _ in tqdm(range(4), file=self.tee, mininterval=0, ascii=True):
                pass
        got = rows(self.model)
        self.assertEqual(len(got), 3, got)
        self.assertTrue(all("4/4" in r for r in got), got)

    def test_bar_moves_below_log_lines_that_arrive_mid_bar(self):
        self.tee.write("\r 10%|#   |")
        rows(self.model)
        self.model.post("Saved chunk checkpoint", logging.INFO)
        self.tee.write("\r 50%|##  |")
        self.assertEqual(rows(self.model), ["Saved chunk checkpoint", " 50%|##  |"])
        self.tee.write("\r100%|####|\n")
        self.tee.write("next\n")
        self.assertEqual(rows(self.model), ["Saved chunk checkpoint", "100%|####|", "next"])

    def test_unencodable_bar_does_not_raise(self):
        # The launcher's log file is cp1252; draw_progress_bar writes '█'.
        # This raised mid Hi-Res finalize and failed the clip (2026-09-25).
        cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        tee = StreamTee(self.model, cp1252)
        tee.write("\rProgress: |██--| 50.0% \n")
        self.assertEqual(rows(self.model), ["Progress: |██--| 50.0%"])

    def test_no_real_stream_under_pythonw(self):
        tee = StreamTee(self.model, None)
        tee.write("\r 5%|\n")
        tee.flush()
        self.assertEqual(rows(self.model), [" 5%|"])


class LogHubTests(unittest.TestCase):
    """The console window's tabs (v1 console_window): each stage's lines on its
    own tab, everything on All (user, 2026-09-30)."""

    def setUp(self):
        self.hub = LogHub()
        self.handler = ModelHandler(self.hub)

    def log(self, name, text, level=logging.INFO):
        rec = logging.LogRecord(name, level, __file__, 1, text, None, None)
        self.handler.emit(rec)

    def tab(self, tab_id):
        return rows(self.hub.tab(tab_id))

    def test_the_tabs_in_order(self):
        self.assertEqual([t["id"] for t in self.hub.property("tabs")],
                         ["depthcrafter", "splatting", "inpainting", "merging", "extra", "all"])

    def test_a_stage_logger_lands_on_its_tab_and_on_all(self):
        self.log("depthcrafter.v2.runner", "dc line", logging.WARNING)
        self.log("studio.stages.splatting.engine", "splat line", logging.WARNING)
        self.log("inpainting.v2.wan", "v2 line", logging.WARNING)
        self.log("studio.stages.inpainting_v2.engine", "v2 engine line", logging.ERROR)
        self.log("merging.v2.preview", "merge line")
        self.log("scenecut.v2", "cut line")
        self.log("DepthEditor", "edit line")
        self.assertEqual(self.tab("depthcrafter"), ["dc line"])
        self.assertEqual(self.tab("splatting"), ["splat line"])
        self.assertEqual(self.tab("inpainting"), ["v2 line", "v2 engine line"])
        self.assertEqual(self.tab("merging"), ["merge line"])
        self.assertEqual(self.tab("extra"), ["cut line", "edit line"])
        self.assertEqual(len(self.tab("all")), 7)

    def test_shared_code_follows_the_running_job_or_only_all(self):
        self.log("core.common.video_io", "idle line")
        self.hub.set_active("scenecut")
        self.log("core.common.video_io", "during scene cut")
        self.hub.set_active("inpainting")
        self.log("core.common.video_io", "during inpainting", logging.WARNING)
        self.hub.set_active(None)
        self.assertEqual(self.tab("extra"), ["during scene cut"])
        self.assertEqual(self.tab("inpainting"), ["during inpainting"])
        self.assertEqual(self.tab("all"), ["idle line", "during scene cut", "during inpainting"])

    def test_housekeeping_below_warning_is_all_only(self):
        self.log("inpainting.v2.housekeeping", "moved to finished")
        self.log("inpainting.v2.housekeeping", "could not move", logging.WARNING)
        self.assertEqual(self.tab("inpainting"), ["could not move"])
        self.assertEqual(self.tab("all"), ["moved to finished", "could not move"])

    def test_printed_lines_and_bars_go_to_the_running_job(self):
        tee = StreamTee(self.hub, io.StringIO())
        self.hub.set_active("depthcrafter")
        tee.write("\r 10%|#   |")
        tee.write("\r100%|####|\n")
        tee.write("printed\n")
        self.assertEqual(self.tab("depthcrafter"), ["100%|####|", "printed"])
        self.assertEqual(self.tab("all"), ["100%|####|", "printed"])
        self.assertEqual(self.tab("splatting"), [])

    def test_splatting_and_inpainting_tabs_show_only_warnings_and_what_is_printed(self):
        # v1's console_log_level (user, 2026-09-30): the banner and the
        # Hi-Res / Low-Res bars on the tab, the rest of the log on All
        tee = StreamTee(self.hub, io.StringIO())
        self.hub.set_active("splatting")
        self.log("core.splatting.batch_processing", "chatty info")
        self.log("studio.stages.splatting.engine", "engine info")
        tee.write("\n===== [3/20] clip.mp4 =====\n")
        tee.write("\rHi-Res |#---| 25.0% \rHi-Res |####| 100.0% Complete\n")
        self.log("core.splatting.batch_processing", "a problem", logging.WARNING)
        self.assertEqual(self.tab("splatting"),
                         ["===== [3/20] clip.mp4 =====", "Hi-Res |####| 100.0% Complete", "a problem"])
        self.assertIn("chatty info", self.tab("all"))
        self.assertIn("engine info", self.tab("all"))
        self.hub.set_active("merging")
        self.log("core.common.video_io", "merging info")
        self.assertEqual(self.tab("merging"), ["merging info"])        # INFO tabs as v1

    def test_depthcrafter_tab_keeps_only_the_bars_and_the_total_times(self):
        # user, 2026-10-04: the ffprobe / processing INFO lines go to All; the
        # window bars (printed), the banner and "[Source complete] ... in" stay
        tee = StreamTee(self.hub, io.StringIO())
        self.hub.set_active("depthcrafter")
        tee.write("\n===== [1/69] powers2.mkv =====\n")
        self.log("core.common.video_io", "Re-running ffprobe with -count_frames")
        self.log("studio.stages.depthcrafter.runner", "Processing powers2")
        tee.write("\rWindow 1/2: 100%|####| 15/15\n")
        self.log("studio.stages.depthcrafter.runner", "[Full video] powers2 - 128 frames in 0:03:50")
        self.log("studio.stages.depthcrafter.runner", "[Source complete] powers2 - 128 frames across 1 job in 0:03:50.3")
        self.log("studio.stages.depthcrafter.runner", "--- Re-Merge for: x.json finished in 12.0s. ---")
        self.log("studio.stages.depthcrafter.runner", "out of memory", logging.WARNING)
        self.assertEqual(self.tab("depthcrafter"),
                         ["===== [1/69] powers2.mkv =====", "Window 1/2: 100%|####| 15/15",
                          "[Source complete] powers2 - 128 frames across 1 job in 0:03:50.3",
                          "--- Re-Merge for: x.json finished in 12.0s. ---", "out of memory"])
        self.assertIn("Processing powers2", self.tab("all"))
        self.assertIn("Re-running ffprobe with -count_frames", self.tab("all"))

    def test_the_debug_option_opens_a_quiet_tab(self):
        self.hub.set_tab_debug("inpainting", True)
        self.log("inpainting.v2.wan", "detail")
        self.hub.set_tab_debug("inpainting", False)
        self.log("inpainting.v2.wan", "hidden")
        self.assertEqual(self.tab("inpainting"), ["detail"])

    def test_clear_one_tab_leaves_the_others(self):
        self.log("merging.v2", "merge line")
        self.assertEqual(self.tab("merging"), ["merge line"])     # delivered, then cleared
        self.hub.tab("merging").clear()
        self.assertEqual(self.tab("merging"), [])
        self.assertEqual(self.tab("all"), ["merge line"])


class V1StyleTests(unittest.TestCase):
    """The console reads as v1's did (user, 2026-09-30): log records as
    'HH:MM:SS - LEVEL - message', printed lines as printed, and progress bars
    drawn as accent spans -- the model hands QML each bar's parts."""

    def setUp(self):
        self.model = LogModel()

    def role(self, name):
        roles = {bytes(v).decode(): k for k, v in self.model.roleNames().items()}
        QCoreApplication.processEvents()
        return [self.model.data(self.model.index(i), roles[name]) for i in range(self.model.rowCount())]

    def test_records_carry_v1s_prefix_printed_lines_none(self):
        ModelHandler(self.model).emit(logging.LogRecord("x", logging.WARNING, __file__, 1, "careful", None, None))
        StreamTee(self.model, io.StringIO()).write("printed\n")
        pre = self.role("prefix")
        self.assertRegex(pre[0], r"^\d\d:\d\d:\d\d - WARNING - $")
        self.assertEqual(pre[1], "")
        self.assertEqual(self.role("line"), ["careful", "printed"])
        self.assertTrue(self.model.allText().splitlines()[0].endswith(" - WARNING - careful"))

    def test_a_selection_copies_the_part_picked(self):
        # the console's drag selection (user, 2026-10-01): rows as shown,
        # the first from its start character, the last to its end one
        tee = StreamTee(self.model, io.StringIO())
        tee.write("alpha line\n")
        tee.write("\rEncoding clip.mov: |#####-----| 50.0% \n")
        tee.write("gamma line\n")
        ModelHandler(self.model).emit(logging.LogRecord("x", logging.INFO, __file__, 1, "delta", None, None))
        QCoreApplication.processEvents()
        m = self.model
        self.assertEqual(m.textRange(0, 2, 0, 5), "pha")
        self.assertEqual(m.textRange(0, 6, 2, 5), "line\nEncoding clip.mov: |#####-----| 50.0%\ngamma")
        self.assertEqual(m.textRange(2, 5, 0, 6), m.textRange(0, 6, 2, 5))          # dragged upward
        delta = m.textRange(3, 0, 3, 10**9)
        self.assertRegex(delta, r"^\d\d:\d\d:\d\d - INFO - delta$")                 # a record with its prefix
        self.assertEqual(m.textRange(1, 30, 1, 31), "Encoding clip.mov: |#####-----| 50.0%")  # a bar: whole
        self.assertEqual(m.textRange(0, 0, 99, 10**9).count("\n"), 3)              # past the end: clamped
        self.assertEqual(LogModel().textRange(0, 0, 0, 5), "")

    def test_bars_are_parsed_for_drawing(self):
        tee = StreamTee(self.model, io.StringIO())
        tee.write("\rEncoding clip.mov: |█████-----| 50.0% \n")
        for _ in tqdm(range(4), file=tee, mininterval=0, ascii=True, desc="denoise"):
            pass
        bars = self.role("bar")
        self.assertEqual(bars[0][0], "Encoding clip.mov:")
        self.assertAlmostEqual(bars[0][1], 0.5)
        self.assertEqual(bars[0][2].strip(), "50.0%")
        self.assertEqual(bars[1][0].split(":")[0], "denoise")
        self.assertAlmostEqual(bars[1][1], 1.0)          # 4/4, from tqdm's count
        self.assertIn("4/4", bars[1][2])

    def test_a_log_line_with_pipes_is_not_a_bar(self):
        ModelHandler(self.model).emit(logging.LogRecord("x", logging.INFO, __file__, 1, "a | b | c", None, None))
        self.assertEqual(self.role("bar"), [[]])
        self.assertIsNone(parse_bar("fps 1.2 | avg 1.1 | eta 5"))


if __name__ == "__main__":
    unittest.main()
