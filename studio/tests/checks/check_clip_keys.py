"""Up / Down between clips in Splatting's and Merging's previews (a tester, 2026-11-02: the two were
swapped). Upcoming Jobs lists the clips top to bottom, so Up goes to the clip above (the previous
one) and Down to the clip below (the next) -- v1 had Up = next.

Real app offscreen; the previews' "nav" job is recorded, not run (nothing loaded). Each page's Up and
Down shortcuts are fired as a key press fires them. Configs and last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio.stages.splatting.preview import SplattingPreview
from studio.stages.merging.preview import MergingPreview

navs = []
for cls in (SplattingPreview, MergingPreview):
    orig = cls._submit
    def rec_submit(self, kind, *a, _orig=orig, _cls=cls.__name__, **k):
        if kind == "nav":
            navs.append((_cls, a[0] if a else None))
            return None
        return _orig(self, kind, *a, **k)
    cls._submit = rec_submit

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
pump(1.0)

def shortcuts(seq):
    return [o for o in win.findChildren(QObject)
            if o.metaObject().className().startswith("QQuickShortcut") and str(o.property("sequence")) == seq]
for seq, want in (("Up", -1), ("Down", 1)):
    found = shortcuts(seq)
    check(f"{seq}: a shortcut on Splatting's and on Merging's page", len(found) == 2, str(len(found)))
    for s in found:
        navs.clear()
        QMetaObject.invokeMethod(s, "activated"); pump(0.2)
        who = navs[0][0] if navs else "?"
        check(f"...{seq} on {who}: {'the previous clip (the one above)' if want < 0 else 'the next clip (the one below)'}",
              [n[1] for n in navs] == [want], str(navs))

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("last_jobs.json untouched", h0 == h1)
print("FAILS", len(fails))
sys.stdout.flush()
os._exit(1 if fails else 0)
