"""The synthetic clips the Splatting checks run on -- never the user's clips.

v1's preview code auto-saves sidecars (and depth_max_raw) next to whatever
it loads, so a check pointed at real footage would write into it. These are
built from ffmpeg's test sources instead:

* ``small``: clipA / clipB, 640x360, 24 fps, 2 s (48 frames), H.264; the
  depth is a bright disc drifting side to side, so a warp has edges and
  therefore disocclusion holes (Hole Shift and Vertical Warp Fix need some).
* ``hd``: one 1920x1080 clip with a 10-bit HEVC depth map -- the format the
  fast decode / GPU depth parity checks exist for.
* ``merge``: what Merging reads, for two 320x180 clips (48 frames): the
  inpainted right eye, the splat grid and the original. mA is the dual-splat
  layout ([mask|warp] + the original beside it), mB the quad one
  ([left|depth] over [mask|warp]). The mask is a drifting disc, so the fill
  moves and a mask slider visibly changes the blend.

Built once into ``%TEMP%/studio_check_fixtures/<kind>`` and reused;
``copy()`` hands each check its own copy under its temp dir, because checks
write sidecars and mask videos into the fixture.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

CACHE = Path(tempfile.gettempdir()) / "studio_check_fixtures"

_SMALL_DEPTH = ("nullsrc=size=640x360:rate=24:duration=2,"
                "geq=lum='clip(255*(1-hypot(X-W/2-40*sin(T*3),Y-H/2)/(W/2)),0,255)':cb=128:cr=128")
_HD_DEPTH = ("nullsrc=size=1920x1080:rate=24:duration=2,format=gray16le,"
             "geq=lum='clip(65535*(1-hypot(X-W/2-100*sin(T*3),Y-H/2)/(W/2)),0,65535)'")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def _build_small(root: Path) -> None:
    for c in ("clipA", "clipB"):
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "src" / f"{c}.mp4"))
        _ffmpeg("-f", "lavfi", "-i", _SMALL_DEPTH,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "depth" / f"{c}_depth.mp4"))


def _build_hd(root: Path) -> None:
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "src" / "hd.mp4"))
    _ffmpeg("-f", "lavfi", "-i", _HD_DEPTH, "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error", str(root / "depth" / "hd_depth.mp4"))


_M = "size=320x180:rate=24:duration=2"
_M_MASK = f"nullsrc={_M},geq=lum='255*lt(hypot(X-W/2-50*sin(T*3),Y-H/2),45)':cb=128:cr=128"


def _build_merge(root: Path) -> None:
    for sub in ("inpainted", "mask", "orig"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    enc = ("-c:v", "libx264", "-pix_fmt", "yuv420p")
    for c in ("mA", "mB"):
        _ffmpeg("-f", "lavfi", "-i", f"testsrc2={_M}", *enc, str(root / "orig" / f"{c}.mp4"))
        _ffmpeg("-f", "lavfi", "-i", f"testsrc2={_M},hue=h=150:s=2", *enc,
                str(root / "inpainted" / f"{c}_320_inpainted_right_eye.mp4"))
    left, warp, mask = f"testsrc2={_M}", f"testsrc2={_M},hue=h=60", _M_MASK
    _ffmpeg("-f", "lavfi", "-i", mask, "-f", "lavfi", "-i", warp,
            "-filter_complex", "[0][1]hstack", *enc, str(root / "mask" / "mA_320_splatted2.mp4"))
    _ffmpeg("-f", "lavfi", "-i", left, "-f", "lavfi", "-i", mask, "-f", "lavfi", "-i", mask,
            "-f", "lavfi", "-i", warp,
            "-filter_complex", "[0][1]hstack[t];[2][3]hstack[b];[t][b]vstack", *enc,
            str(root / "mask" / "mB_320_splatted4.mp4"))


def _square_clip(path: Path, frames: int, w: int, h: int, two: bool) -> None:
    """SAM 3's made-up clips: a square (and a disc) moving over a fixed noise
    background. Built once and cached -- check_sam3_vram wrote its 300-frame
    1920x1038 clip (~100 MB) into a new temp folder every run and left it
    (bug sweep 2026-10-09, A12)."""
    import cv2
    import numpy as np
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (w, h))
    rng = np.random.default_rng(0)
    bg = (rng.random((h, w, 3)) * 60 + 40).astype(np.uint8)
    for f in range(frames):
        img = bg.copy()
        if two:
            x = 200 + int(1200 * f / max(1, frames - 1))
            cv2.rectangle(img, (x, 300), (x + 240, 560), (40, 200, 240), -1)                 # one object
            cv2.circle(img, (1700 - x // 2, 760), 120, (230, 80, 60), -1)                   # the other
        else:
            x = 60 + int(400 * f / (frames - 1))
            cv2.rectangle(img, (x, 120), (x + 120, 240), (40, 200, 240), -1)
        vw.write(img)
    vw.release()


def _build_sam3_square(root: Path) -> None:
    _square_clip(root / "src" / "square.mp4", 60, 640, 360, two=False)


def _build_sam3_two(root: Path) -> None:
    _square_clip(root / "src" / "two_objects.mp4", 300, 1920, 1038, two=True)


_BUILDERS = {"small": _build_small, "hd": _build_hd, "merge": _build_merge,
             "sam3_square": _build_sam3_square, "sam3_two": _build_sam3_two}
#: Built up front by run_all (checks side by side would build them over one
#: another); the SAM 3 clips are built by their own checks, which run alone.
PREBUILT = ("small", "hd", "merge")


def path(kind: str = "small") -> Path:
    """The cached, pristine fixture (built on first use). Read only: use copy().

    Built in a private folder and renamed into place (audit 2026-09-28 #30):
    two checks building the same kind at once used to rmtree and refill one
    folder under each other. Now each builds its own; the first rename wins
    and the other's copy is thrown away. A folder without ``.complete`` (an
    interrupted build) is replaced."""
    root = CACHE / kind
    done = root / ".complete"
    if done.exists():
        return root
    CACHE.mkdir(parents=True, exist_ok=True)
    build = CACHE / f".{kind}.building-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:6]}"
    for sub in ("src", "depth", "out"):
        (build / sub).mkdir(parents=True, exist_ok=True)
    try:
        _BUILDERS[kind](build)
        (build / ".complete").write_text("ok")
        if root.exists() and not done.exists():
            shutil.rmtree(root, ignore_errors=True)      # an interrupted build
        try:
            os.replace(build, root)
        except OSError:
            if not done.exists():                        # not another builder's: a real failure
                raise
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return root


def copy(kind: str, dest: Path) -> Path:
    """A private copy of the fixture at ``dest`` (a check's temp dir)."""
    shutil.copytree(path(kind), dest, ignore=shutil.ignore_patterns(".complete"))
    return dest


def fake_busy(ctl, on: bool) -> None:
    """Make a stage controller look busy (or idle) to everything that watches
    it, as a real job does: its `_busy` flag AND its stateChanged signal (the
    GPU hand-over, the window's calm, the page). The one seam for it, rather
    than each check poking the private flag (Studio audit 2026-09-28,
    tooling). A check that wants only the flag -- a partial state on purpose --
    still sets it itself."""
    ctl._busy = bool(on)
    ctl.stateChanged.emit()


def _own_temp_dirs() -> list:
    """The check's own temp folders: tmp* folders directly in the temp dir
    that the running check's globals point at (its `tmp = mkdtemp()`), or
    below them. Never the shared fixture cache, never anything else."""
    import __main__
    base = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    found = set()
    for value in list(vars(__main__).values()):
        if not isinstance(value, (str, Path)) or not str(value):
            continue
        try:
            path = os.path.normcase(os.path.abspath(str(value)))
        except (TypeError, ValueError):
            continue
        rel = os.path.relpath(path, base) if path.startswith(base + os.sep) else None
        if not rel:
            continue
        top = rel.split(os.sep)[0]
        if top.startswith("tmp") and os.path.isdir(os.path.join(base, top)):
            found.add(os.path.join(base, top))
    return sorted(found)


def remove_after_exit(folders) -> None:
    """Delete `folders` once this process has gone: a small detached Python
    waits for our PID, then removes them -- files the check still holds open
    (decord, ffmpeg readers) are free by then, and the check is not slowed.
    Checks left 3 GB of copied clips in %TEMP% before this (Studio audit
    2026-09-28, tooling)."""
    import subprocess
    import sys
    folders = [str(f) for f in folders if f]
    if not folders:
        return
    script = (
        "import os, sys, time, shutil\n"
        "pid = int(sys.argv[1])\n"
        "import ctypes\n"
        "k = ctypes.windll.kernel32 if os.name == 'nt' else None\n"
        "h = k.OpenProcess(0x00100000, False, pid) if k else None\n"
        "if h:\n"
        "    k.WaitForSingleObject(h, 120000)\n"
        "    k.CloseHandle(h)\n"
        "for f in sys.argv[2:]:\n"
        "    for _ in range(10):\n"
        "        shutil.rmtree(f, ignore_errors=True)\n"
        "        if not os.path.exists(f):\n"
        "            break\n"
        "        time.sleep(0.5)\n"
    )
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable, "-c", script, str(os.getpid())] + folders,
                         creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except Exception:
        pass


#: A check's exit code for "couldn't run here": run_all lists it as SKIP, not PASS.
SKIP_CODE = 3


def skip(reason: str) -> None:
    """End a check that can't run on this machine (no SAM 3 weights, no ROSE,
    the GPU hidden) as a SKIP -- exit 0 read as a pass, and the summary was all
    green with those paths never run (bug sweep 2026-10-09, A12)."""
    print(f"SKIP: {reason}", flush=True)
    exit_now(SKIP_CODE)


def exit_now(code: int) -> None:
    """End a check at once with ``code``, as os._exit is meant to.

    os._exit still runs the DLLs' process-detach on Windows, and with torch,
    decord and an offscreen Qt scene loaded that teardown can itself crash
    (an access violation after every assertion had passed -- exit 139 for a
    green check). Studio's own quit path exits cleanly; this is only the
    checks skipping teardown. TerminateProcess skips it entirely."""
    import os
    import sys
    if os.environ.get("STUDIO_CHECKS_KEEP_TEMP") != "1":   # set it to look at what a check wrote
        remove_after_exit(_own_temp_dirs())
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        # HANDLE-sized, or the pseudo-handle -1 is truncated to a 32-bit int
        # and the call fails (falling through to the os._exit that crashes).
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess(k32.GetCurrentProcess(), int(code))
    os._exit(int(code))
