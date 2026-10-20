"""Run every Studio v2 check in this folder and summarise.

    ./.venv/Scripts/python.exe studio/tests/checks/run_all.py
    ./.venv/Scripts/python.exe studio/tests/checks/run_all.py splat_editors fit
    ./.venv/Scripts/python.exe studio/tests/checks/run_all.py -j1
    ./.venv/Scripts/python.exe studio/tests/checks/run_all.py --onscreen titlebar

An argument filters by substring. With no filter (or the filter "unit") the
unit tests run too -- studio/tests/test_*.py, with the generator sync
tests -- as one more job; --no-unit leaves them out.

Every check_*.py here runs offscreen
(QT_QPA_PLATFORM=offscreen), in its own process, several at a time; the ones
in SOLO run one at a time afterwards because they time something, and the
ones in GPU_HEAVY (SAM 3, ROSE, Erase) one at a time because two of them
share a 16 GB card badly. The onscreen/ checks put a real window on the
screen and only run with --onscreen.

The Splatting checks load torch and use the GPU. Don't run them while a real
GPU job is going.

A check that can't run here (no SAM 3 weights, no ROSE, the GPU hidden) exits
with SKIP_CODE and is listed as SKIP, not PASS.

Exit code is the number of checks that failed.
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

#: Timing-sensitive: playback pacing ("never faster than the clip"), a real
#: SINGLE render, focus-driven key presses. Run alone so a neighbour can't
#: slow them.
SOLO = {"check_splat_preview_3b.py", "check_splat_single.py",
        # key presses go to the focus window, which a busy machine can race
        "check_splat_detect_text.py",
        # counts redraws while a job runs: the parallel checks' GPU use moves
        # the VRAM pill's number, a redraw that is real but not the window's own
        "check_calm.py"}
#: The big GPU users (bug sweep 2026-10-09, A12): in the 3-wide pool SAM 3 ran
#: twice side by side (check_sam3_vram also times two runs against each
#: other), beside ROSE's ~5 GB and a real erase, on one card -- an OOM or a
#: timing miss that isn't a regression. They run one at a time, after the pool.
GPU_HEAVY = {"check_sam3_directions.py", "check_sam3_vram.py", "check_erase_rose.py",
             "check_depthedit_erase.py"}
SOLO |= GPU_HEAVY
#: A check's exit code for "couldn't run here" (_fixtures.SKIP_CODE).
SKIP_CODE = 3

DEFAULT_JOBS = 3
#: The unit suite's name in the summary.
UNIT = "unit tests (studio/tests)"
TIMEOUT_S = 900


def scripts(patterns, onscreen):
    names = sorted(n for n in os.listdir(HERE) if n.startswith("check_") and n.endswith(".py"))
    if onscreen:
        names += sorted("onscreen/" + n for n in os.listdir(os.path.join(HERE, "onscreen"))
                        if n.startswith("check_") and n.endswith(".py"))
    if not patterns:
        return names
    return [n for n in names if any(p.lower() in n.lower() for p in patterns)]


def main(argv):
    args = list(argv[1:])
    jobs, onscreen, unit = DEFAULT_JOBS, False, True
    for arg in list(args):
        if arg == "--no-unit":
            unit = False
            args.remove(arg)
            continue
        if arg.startswith("-j"):
            try:
                jobs = max(1, int(arg[2:]))
            except ValueError:
                pass
            args.remove(arg)
        elif arg == "--onscreen":
            onscreen = True
            args.remove(arg)
    names = scripts(args, onscreen)
    # the unit suite: with no filter, or when asked for by name (audit
    # 2026-09-28: run_all never ran it, nor the generator sync tests in it)
    if unit and (not args or any(a.lower() == "unit" for a in args)):
        names.append(UNIT)
    if not names:
        print("nothing matched")
        return 0

    def run_one(name):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if name == UNIT:
            started = time.monotonic()
            repo = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
            try:
                result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "studio/tests",
                                         "-p", "test_*.py", "-t", "."], cwd=repo, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", env=env, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                result = subprocess.CompletedProcess(e.cmd, -1, "", f"timed out after {TIMEOUT_S}s")
            return name, result, time.monotonic() - started
        if not name.startswith("onscreen/"):
            env["QT_QPA_PLATFORM"] = "offscreen"
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-u", os.path.join(HERE, name)],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", env=env, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            result = subprocess.CompletedProcess(e.cmd, -1, e.stdout or "", f"timed out after {TIMEOUT_S}s")
        return name, result, time.monotonic() - started

    failed, skipped = [], []

    def report(name, result, took):
        ok = result.returncode == 0
        if result.returncode == SKIP_CODE and name != UNIT:
            # couldn't run here: not a pass (bug sweep A12: a missing SAM 3 or ROSE read green)
            why = next((ln.strip() for ln in (result.stdout or "").splitlines() if ln.startswith("SKIP")), "")
            print(f"  [SKIP] {name:40} {took:6.1f}s   {why[4:].strip(' :-')}", flush=True)
            skipped.append(name)
            return
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:40} {took:6.1f}s"
              + ("" if ok else f"   (exit code {result.returncode})"), flush=True)
        if not ok:
            failed.append((name, result))

    # Build the fixtures once, here: checks running side by side would each
    # find the cache missing and build it over one another.
    sys.path.insert(0, HERE)
    import _fixtures
    for kind in _fixtures.PREBUILT:       # every shared kind: "merge" too (audit #30)
        _fixtures.path(kind)

    parallel = [n for n in names if n not in SOLO and not n.startswith("onscreen/")]
    solo = [n for n in names if n not in parallel]
    print(f"running {len(names)} checks with {sys.executable}")
    print(f"  {len(parallel)} in parallel ({jobs} at a time), {len(solo)} on their own\n", flush=True)
    started_all = time.monotonic()
    if parallel:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for item in pool.map(run_one, parallel):
                report(*item)
    for name in solo:
        report(*run_one(name))

    print(f"\n  {time.monotonic() - started_all:.1f}s total")
    note = f" ({len(skipped)} skipped: couldn't run here)" if skipped else ""
    if not failed:
        print(f"all {len(names) - len(skipped)} that ran passed{note}" if skipped else f"all {len(names)} passed")
        return 0
    # Only the failures' output; a passing check is a screen of OK lines.
    for name, result in failed:
        print(f"--- {name} " + "-" * max(0, 60 - len(name)))
        out = result.stdout if isinstance(result.stdout, str) else ""
        if name == UNIT:        # unittest reports on stderr
            out = result.stderr if isinstance(result.stderr, str) else ""
            for line in out.splitlines():
                if line.startswith(("FAIL:", "ERROR:")) or line.startswith("Ran ") or "FAILED" in line:
                    print("   ", line.strip())
            continue
        for line in out.splitlines():
            if "FAIL" in line or "unchanged: False" in line or "OVERFLOW" in line:
                print("   ", line.strip())
        err = result.stderr if isinstance(result.stderr, str) else ""
        for line in err.strip().splitlines()[-4:]:
            print("    !", line)
    print(f"\n{len(failed)} of {len(names)} failed{note}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
