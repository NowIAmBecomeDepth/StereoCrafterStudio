"""Studio's unit tests.

Every test runs against a temp settings folder and a temp job record, set
here before any test module imports Studio (user's rule: tests never touch
the user's data). test_inpainting_v2's batch once recorded its temp output in
the user's real last_jobs.json (found 2026-10-05) -- it, like most unit
tests, never redirected them itself. A test that sets its own still wins.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="studio-tests-")
os.environ.setdefault("STEREOCRAFTER_TEST_CONFIG_DIR", os.path.join(_TMP, "config"))
os.environ.setdefault("STEREOCRAFTER_TEST_RECORD_PATH", os.path.join(_TMP, "last_jobs.json"))

#: The retired Prototype's sources (splatting_gui.py and the rest) and the generators that build Studio's
#: engines from them (studio/tools/gen_*.py): on the developer's PC only -- they don't ship (release,
#: 2026-10-17). The tests that compare Studio with them skip without them.
from pathlib import Path as _Path

_REPO = _Path(__file__).resolve().parents[2]
PROTOTYPE = (_REPO / "splatting_gui.py").is_file() and (_REPO / "studio" / "tools" / "gen_merging_engine.py").is_file()
NO_PROTOTYPE = "compares Studio with the Prototype's sources, which don't ship"
