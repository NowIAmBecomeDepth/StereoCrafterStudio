"""fast_depth.py -- moved to core/splatting/depth_gpu.py (2026-09-29): the
render uses it too now. Kept so its old imports still work.
"""

from core.splatting.depth_gpu import *  # noqa: F401,F403
from core.splatting.depth_gpu import (  # noqa: F401
    gpu_blur, gpu_dilate, gpu_dilate_left, normalize_and_gamma_depth_gpu, process_depth_batch_gpu,
)
