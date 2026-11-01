"""Inpainting V2's "Rerun in Splatting" (user, 2026-10-31: send a clip back to the stage before, a group of
ticked clips too, Resume on or off), in the real app offscreen -- see _send_back_inpaint.py
for what it covers. Files only: no job runs. Configs and last_jobs.json redirected.
"""
from _send_back_rig import *
import _send_back_inpaint

_send_back_inpaint.run(globals(), v2=True)
finish()
