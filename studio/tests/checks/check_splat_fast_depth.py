import sys, time, subprocess
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _fixtures
from studio.legacy import install; install()
# the CPU reference (process_depth_batch itself runs on the GPU since 2026-09-29)
from core.splatting.depth_processing import process_depth_batch_cpu as process_depth_batch
from studio.stages.splatting.fast_depth import process_depth_batch_gpu
path = sys.argv[1] if len(sys.argv) > 1 else str(_fixtures.path("hd") / "depth" / "hd_depth.mp4")
raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", path, "-frames:v", "1", "-vf", "extractplanes=y,format=gray16le",
                      "-f", "rawvideo", "pipe:1"], capture_output=True).stdout
d = np.frombuffer(raw, np.uint16).reshape(1080, 1920, 1).astype(np.float32)[None]  # (1,H,W,1)
d = d / 64.0  # MSB-aligned 10-bit -> 0..1023
cases = [dict(depth_dilate_size_x=3.5, depth_dilate_size_y=0.5, depth_blur_size_x=3, depth_blur_size_y=3, depth_dilate_left=4.0, depth_blur_left=5),
         dict(depth_dilate_size_x=0, depth_dilate_size_y=0, depth_blur_size_x=0, depth_blur_size_y=0, depth_dilate_left=0, depth_blur_left=0),
         dict(depth_dilate_size_x=7.25, depth_dilate_size_y=2.0, depth_blur_size_x=10, depth_blur_size_y=1, depth_dilate_left=1.5, depth_blur_left=0),
         dict(depth_dilate_size_x=34, depth_dilate_size_y=33, depth_blur_size_x=0, depth_blur_size_y=5, depth_dilate_left=0, depth_blur_left=9, depth_blur_left_mix=0.3),
         dict(depth_dilate_size_x=-3, depth_dilate_size_y=2.5, depth_blur_size_x=35, depth_blur_size_y=35, depth_dilate_left=20, depth_blur_left=20)]
worst = 0
for c in cases:
    kw = dict(batch_depth_numpy_raw=d, depth_gamma=1.0, max_raw_value=1023.0, **c)
    t = time.perf_counter(); a = process_depth_batch(**kw); tc = time.perf_counter() - t
    process_depth_batch_gpu(**kw)
    t = time.perf_counter(); b = process_depth_batch_gpu(**kw); tg = time.perf_counter() - t
    diff = np.abs(a - b); units = diff / 1023.0 * 65535  # in 16-bit steps
    worst = max(worst, units.max())
    print(f"cpu {tc*1000:6.1f} ms  gpu {tg*1000:6.1f} ms  max diff {units.max():.2f} 16-bit units  mean {units.mean():.4f}  {c}")
# One unit of the 16-bit intermediate is rounding; more is a real difference.
print("WORST", round(float(worst), 2))
sys.exit(0 if worst <= 1.01 else 1)
