"""Mesh Warp's triangle list is built with numpy (bug sweep 2026-10-09, A11):
the Python loop took ~0.3 s at the preview's default density, on every Mesh
Warp render (316 -> 102 ms a render at 1080p). The list the GPU gets is the
loop's, element for element.

    python -m unittest studio.tests.test_mesh_indices
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


def loop_indices(dx, dy):
    out = []
    for j in range(dy - 1):
        for i in range(dx - 1):
            p0 = j * dx + i
            p1 = p0 + 1
            p2 = (j + 1) * dx + i
            p3 = p2 + 1
            out.extend([p0, p2, p1])
            out.extend([p1, p2, p3])
    return np.array(out, dtype=np.int32)


class IndicesTest(unittest.TestCase):
    def test_the_gpu_gets_the_loops_list(self):
        from core.common import mesh_warp as M
        for dx, dy in ((7, 5), (2, 2), (33, 17)):
            try:
                e = M.FusionStereoReplicator(64, 48, density_x=dx, density_y=dy)
            except Exception as err:                       # no OpenGL here
                self.skipTest(f"no GL context: {err}")
            try:
                got = np.frombuffer(e.ibo.read(), dtype=np.int32)
            finally:
                e.release()
            self.assertTrue(np.array_equal(got, loop_indices(dx, dy)), (dx, dy))


if __name__ == "__main__":
    unittest.main()
