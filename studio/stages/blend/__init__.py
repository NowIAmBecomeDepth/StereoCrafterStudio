"""Depth Map's Blend tab (user, 2026-10-26): a clip's DepthCrafter and VDA maps blended into one.

settings.py   -- defaults and coercion (no Qt, no torch)
runner.py     -- the batch: each clip's maps through core.depthblend
controller.py -- the QObject QML talks to: the clips found, the preview, the job
"""
