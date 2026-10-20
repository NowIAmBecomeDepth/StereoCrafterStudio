import sys, time, random, subprocess
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _fixtures
from studio.legacy import install; install()
from core.common.video_io import FFmpegRGBSingleFrameReader
from studio.stages.splatting import fast_decode as FD
fx = sys.argv[1] if len(sys.argv) > 1 else str(_fixtures.path("hd"))
dpath, spath = fx + "/depth/hd_depth.mp4", fx + "/src/hd.mp4"
W, H, FPS, N = 1920, 1080, 24.0, 48
def v1_depth(i):  # v1 _read_depth_frame_ffmpeg's _run
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{i/FPS:.6f}", "-i", dpath, "-an", "-sn", "-dn",
           "-frames:v", "1", "-vf", "extractplanes=y,format=gray16le", "-f", "rawvideo", "pipe:1"]
    return subprocess.run(cmd, capture_output=True).stdout
fails = 0
ds = FD.depth_stream(dpath, W, H, FPS, "extractplanes=y,format=gray16le")
order = list(range(N)) + random.Random(1).sample(range(N), 20) + [47, 3, 4, 5, 30]
t = time.perf_counter(); got = {i: ds.read(i) for i in range(N)}; seq = time.perf_counter() - t
for i in order:
    b = ds.read(i)
    if b != v1_depth(i):
        fails += 1; print("depth mismatch at", i, len(b or b""))
print(f"depth: {len(order)} reads checked, sequential {seq/N*1000:.1f} ms/frame")
t = time.perf_counter(); v1_depth(40); print(f"   v1 per-frame depth decode: {(time.perf_counter()-t)*1000:.0f} ms")
orig = FFmpegRGBSingleFrameReader(spath, W, H, FPS, N, "tv", "bt709")
rd = FD.StreamRGBReader(orig, lambda strict: FD.rgb_stream(spath, W, H, "tv", "bt709", strict=strict, total=N))
t = time.perf_counter(); [rd.get_batch([i]) for i in range(N)]; seq = time.perf_counter() - t
for i in [0, 1, 2, 17, 5, 47, 46, 20, 21, 22]:
    a = rd.get_batch([i]).asnumpy(); b = orig.get_batch([i]).asnumpy()
    if not np.array_equal(a, b):
        fails += 1; print("source mismatch at", i, np.abs(a.astype(int) - b).max())
print(f"source (strict): checked, sequential {seq/N*1000:.1f} ms/frame")
t = time.perf_counter(); orig.get_batch([40]); print(f"   v1 per-frame strict decode (frame 40): {(time.perf_counter()-t)*1000:.0f} ms")
# bug sweep 2026-10-09 (A6): a far jump decoded from frame 0 (select alone), a read past
# the end ran ffmpeg over the whole clip, and a slow read made the plain chain stick
cmd = rd._strict._build_cmd(40)
if "-ss" not in cmd or any("select" in c for c in cmd):
    fails += 1; print("a far jump does not seek:", cmd)
proc0 = rd._strict._proc
t = time.perf_counter()
past = rd._strict.read(N + 5)
if past is not None or rd._strict._proc is not proc0 or time.perf_counter() - t > 1.0:
    fails += 1; print("a read past the end started a decode")
_real = rd._strict.read
def _slow(idx, timeout=30.0):
    rd._strict.timed_out = True
    return None
rd._strict.read = _slow
a = rd.get_batch([12]).asnumpy(); b = orig.get_batch([12]).asnumpy()
rd._strict.read = _real
if rd._stream is not rd._strict or not np.array_equal(a, b):
    fails += 1; print("a timed-out read made the plain chain stick, or lost the frame")
print("far jump / past end / timeout: checked")
ds.close(); rd.close()
print("FAILS", fails)
sys.exit(1 if fails else 0)
