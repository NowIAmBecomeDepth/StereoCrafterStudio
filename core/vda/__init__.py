"""Video Depth Anything for StereoCrafter (user, 2026-10-25: DepthCrafter's VDA tab).

model.py loads a size and runs a clip's frames through it; normalize.py turns its raw output into
0..1 depth for the whole clip; output.py writes the map where Splatting reads it. The model code
itself is vendored in video_depth_anything/ at the repo top.
"""
