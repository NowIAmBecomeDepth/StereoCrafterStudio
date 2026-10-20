"""test_renders.py -- where a stage's Test Render writes (user, 2026-10-15: "a right click option on the
clips in the list of each stage to create a test render that creates a new folder in the output folder of
the job, so the test clip outputs automatically in there so it doesn't overwrite the original output").

A Test Render is the stage's "This Clip Now" with three differences: its output goes into
<output folder>/Test Renders, it moves nothing to finished/, and it isn't handed on as the stage's job
(the next stage keeps reading the real output). A second test of a clip replaces the first.
"""

from __future__ import annotations

import os

FOLDER = "Test Renders"


def folder(output: str) -> str:
    """<output>/Test Renders, made if it isn't there. An output that's a file: beside it."""
    base = str(output or "").strip()
    if os.path.isfile(base):
        base = os.path.dirname(base)
    path = os.path.join(base, FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def is_test_folder(path: str) -> bool:
    return os.path.basename(os.path.normpath(str(path or ""))) == FOLDER
