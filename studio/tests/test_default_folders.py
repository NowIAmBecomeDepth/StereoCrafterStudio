"""The stages' folders before a project is chosen (release check 2026-10-17; studio/default_folders.py): under
the user's Videos folder, not "./" inside Studio's own folder, where a first-time user's outputs landed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import default_folders as D  # noqa: E402
from studio.stages.depthcrafter import settings as DC  # noqa: E402
from studio.stages.inpainting import settings as IN  # noqa: E402
from studio.stages.inpainting_v2 import settings as V2  # noqa: E402
from studio.stages.merging import settings as MG  # noqa: E402
from studio.stages.splatting import settings as SP  # noqa: E402

TABLES = {"DepthCrafter": DC.DEFAULTS, "Splatting": SP.DEFAULTS, "Inpainting": IN.DEFAULTS,
          "Inpainting reset": IN.RESET_VALUES, "Inpainting V2": V2.DEFAULTS, "Merging": MG.DEFAULTS,
          "Merging reset": MG.RESET_VALUES}


class DefaultFoldersTest(unittest.TestCase):
    def test_no_default_is_inside_studios_folder(self):
        inside = {f"{t}.{k}": v for t, table in TABLES.items() for k, v in table.items()
                  if isinstance(v, str) and (v.startswith("./") or v.startswith(".\\"))}
        self.assertEqual(inside, {}, "a default folder still points into Studio's own folder")

    def test_they_are_under_videos(self):
        base = str(D.BASE).replace("\\", "/")
        self.assertTrue(base.endswith("/StereoCrafter Studio"), base)
        for key in ("input_source_clips", "input_depth_maps", "output_splatted"):
            self.assertEqual(SP.DEFAULTS[key], f"{base}/{key}")
        self.assertEqual(MG.DEFAULTS["mask_folder"], f"{base}/output_splatted/hires")
        self.assertEqual(DC.DEFAULTS["output_dir"], f"{base}/output_depthmaps")

    def test_localize_leaves_everything_else(self):
        out = D.localize({"a": "./final_videos", "b": 3, "c": "D:/Movies", "d": ""}, base=Path("E:/V/StereoCrafter Studio"))
        self.assertEqual(out, {"a": "E:/V/StereoCrafter Studio/final_videos", "b": 3, "c": "D:/Movies", "d": ""})

    def test_a_missing_saved_folder_falls_back_there(self):
        got = SP.restore_folders({"output_splatted": "Z:/gone/away"})
        self.assertEqual(got["output_splatted"], SP.DEFAULTS["output_splatted"])


if __name__ == "__main__":
    unittest.main()
