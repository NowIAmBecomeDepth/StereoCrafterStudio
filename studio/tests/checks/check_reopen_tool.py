"""Studio reopens where it was left, the Extra tab's tool included (user, 2026-10-11:
"sometimes when I reopen Studio it opens right into Scene Cut even though I wasn't using
it" -- closed in the Depth Map Editor, it came back on the Extra tab's first tool). Real
app, offscreen, run twice on one temp config: the first run goes to the Depth Map Editor
and quits; the second opens on the Depth Map Editor. Then the same for Scene Cut, and
for a pipeline stage. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_reopen_tool.py
"""
import os, sys, tempfile, hashlib, time, subprocess, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]

if len(sys.argv) > 2:                       # one run: <config dir> <stage> <tool> (-1: just look)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
    sys.path.insert(0, str(REPO)); os.chdir(REPO)
    cfg, stage_to, tool_to = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    from studio.legacy import install; install()
    import studio.settings_store as ss; ss.CONFIG_DIR = cfg
    import core.ui.job_handoff as jh; jh._RECORD_PATH = str(cfg.parent / "last_jobs.json")
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtGui import QGuiApplication
    from studio import app as A

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)

    def tree(it, out):
        for c in it.childItems():
            out.append(c); tree(c, out)
        return out
    qapp = QGuiApplication(sys.argv)
    engine, objs = A.build(qapp)
    win = engine.rootObjects()[0]
    win.show(); pump(1.0)
    stage = next(i for i in tree(win.contentItem(), []) if i.objectName() == "stage")
    extra = next(i for i in tree(win.contentItem(), []) if i.metaObject().className().startswith("ExtraPage"))
    print("OPENED", json.dumps({"stage": stage.property("current"), "tool": extra.property("tool")}), flush=True)
    if stage_to >= 0:
        objs["Shell"].openStage(stage_to, tool_to); pump(0.8)
    win.close(); pump(0.3)
    objs["Shell"].save()                    # what the quit does (app.main's _shutdown)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
    _fixtures.exit_now(0)

rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def run(stage, tool):
    # each run keeps its temp folders: exit_now removes a check's own after it ends, and this
    # settings folder is what the next run has to read
    env = {**os.environ, "STUDIO_CHECKS_KEEP_TEMP": "1"}
    out = subprocess.run([sys.executable, __file__, str(tmp / "config"), str(stage), str(tool)],
                         capture_output=True, text=True, timeout=180, env=env).stdout
    line = next((l for l in out.splitlines() if l.startswith("OPENED ")), "OPENED {}")
    return json.loads(line[7:])

run(4, 1)                                    # closed in the Depth Map Editor
got = run(-1, -1)
check("closed in the Depth Map Editor: reopens there", got == {"stage": 4, "tool": 1}, str(got))
run(4, 0)                                    # ... in Scene Cut
got = run(-1, -1)
check("closed in Scene Cut: reopens there", got == {"stage": 4, "tool": 0}, str(got))
run(1, -1)                                   # ... on Splatting
got = run(-1, -1)
check("closed on Splatting: reopens there", got.get("stage") == 1, str(got))

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
