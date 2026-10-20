"""Scene cut detection -- the model side of the Bonus tab's Scene Cut tool.

Ported from enoky's SceneCutGUI (https://github.com/enoky/SceneCutGUI, MIT),
which shipped as one 2,357-line script. Studio keeps its own split: the model
and ffmpeg work lives here with no tkinter in sight, and the window that drives
it is scenecut_gui.py at the repo root, the same shape as every other stage.

  detection    -- detector registry, model loading, inference, timecodes
  cut_validator -- the optional DINOv3/SSCD pass that filters false positives

The two neural detectors that are not on PyPI (AutoShot, OmniShotCut) are
vendored under third_party/; detection.py puts them on sys.path when it
imports them, exactly as upstream did.
"""
