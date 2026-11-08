"""DVD: Depth Map's DVD tab (user, 2026-11-07), built as the VDA tab is.

settings.py   -- defaults, coercion and the per-run snapshot (no Qt, no torch)
runner.py     -- the batch: each clip through the model a window at a time, normalised, written where
                 Splatting reads it
controller.py -- the QObject QML talks to; runs the runner on a worker thread

The model itself is core/dvd (and the vendored dvd_depth/).
"""
