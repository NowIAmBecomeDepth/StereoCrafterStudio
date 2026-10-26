"""Video Depth Anything: DepthCrafter's VDA tab (user, 2026-10-25).

settings.py   -- defaults, coercion and the per-run snapshot (no Qt, no torch)
runner.py     -- the batch: each clip through the model, normalised, written where Splatting reads it
controller.py -- the QObject QML talks to; runs the runner on a worker thread

The model itself is core/vda (and the vendored video_depth_anything/).
"""
