"""DVD for StereoCrafter (user, 2026-11-07: Depth Map's DVD tab).

model.py loads the checkpoint, runs windows of frames through it and stitches them; the map is normalised
and written as Video Depth Anything's are (core.vda.normalize, core.vda.output). The model code itself is
vendored in dvd_depth/ at the repo top.
"""
