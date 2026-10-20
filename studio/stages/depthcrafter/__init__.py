"""DepthCrafter for Studio v2.

settings.py   -- defaults, coercion and the per-run snapshot (no Qt, no Tk)
runner.py     -- the batch orchestration ported out of depthcrafter_gui_seg.py
controller.py -- the QObject QML talks to; runs the runner on a worker thread
"""
