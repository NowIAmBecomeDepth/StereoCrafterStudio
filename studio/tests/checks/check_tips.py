"""Every tip in one place (user, 2026-10-11): the tip faders above the progress bars and
Settings > Tips read the same list (studio/qml/Studio/tips.js). Real app, offscreen:
each stage's fader has its stage's tips, the new ones included (Pop-Out before Pop Out
Boost, resolution presets, Rain, Auto Crop, Merge All, ...); Settings > Tips sits after
Keyboard Shortcuts and shows every tip under its group's heading, the Depth Map Editor's
and the everywhere ones too. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_tips.py
"""
import os, sys, tempfile, hashlib, time, re
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

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
def tree(it, out):
    for c in it.childItems():
        out.append(c); tree(c, out)
    return out
def as_list(v):
    v = v.toVariant() if hasattr(v, "toVariant") else v
    return list(v or [])

pump(0.8)
root = win.contentItem().parentItem() or win.contentItem()
tickers = [i for i in tree(root, []) if i.metaObject().className().startswith("TipTicker")]
by_first = {}
for t in tickers:
    tips = as_list(t.property("tips"))
    if tips:
        by_first[tips[0]] = tips
def fader(word):
    """The fader whose tips mention `word` (each stage's list is its own)."""
    return next((tips for tips in by_first.values() if any(word in x for x in tips)), [])

for stage, old, new in (
        ("Splatting", "Dual Eye shares the shift", "Try Pop-Out first"),
        ("Splatting", "Dual Eye shares the shift", "Custom Resolution Presets"),
        ("Splatting", "Dual Eye shares the shift", "Keep lights out"),
        ("Scene Cut", "OmniShotCut", "Auto Crop"),
        ("Merging", "Raise Blur Kernel", "Merge All SBS to 1 Video"),
        ("DepthCrafter", "Process as Segments", "Auto Size for the Loaded Clip"),
        ("Inpainting", "Keep Frames Chunk at 25", "Guidance Scale 1.0")):
    tips = fader(old)
    check(f"{stage}'s fader has its old tips and the new: {new}", bool(tips) and any(new in x for x in tips), f"{len(tips)} tips")
check("no fader mixes two stages' tips", not (set(fader("OmniShotCut")) & set(fader("Dual Eye shares the shift"))))

dlg = next(o for o in win.findChildren(QObject) if o.objectName() == "settingsDialog")
dlg.setProperty("current", dlg.property("tipsAt")); dlg.setProperty("visible", True); pump(0.8)
nav = [i.property("modelData") for i in tree(root, []) if i.objectName() == "settingsNav"]
check("Settings: Tips right after Keyboard Shortcuts", "Tips" in nav and nav.index("Tips") == nav.index("Keyboard Shortcuts") + 1, str(nav))
sec = next(i for i in tree(root, []) if i.objectName() == "settingsTips")
check("...its page shows", sec.isVisible())
rows = [i for i in tree(sec, []) if i.objectName() == "tipRow"]
texts = [next(c for c in tree(r, []) if c.metaObject().className().startswith("QQuickText")).property("text") for r in rows]
# tips.js read as text: every quoted tip in GROUPS
src = (REPO / "studio/qml/Studio/tips.js").read_text(encoding="utf-8")
want = re.findall(r'^\s+"((?:[^"\\]|\\.)+)",?\s*$', src, re.M)
check("...every tip in Studio, each once", sorted(texts) == sorted(want) and len(set(texts)) == len(texts),
      f"{len(texts)} shown, {len(want)} in tips.js")
for fader_tips, name in ((fader("Dual Eye shares the shift"), "Splatting"), (fader("OmniShotCut"), "Scene Cut")):
    check(f"...{name}'s fader tips all among them", fader_tips and set(fader_tips) <= set(texts))
heads = [i.property("text") for i in tree(sec, []) if i.property("text") in ("Everywhere", "Scene Cut", "DepthCrafter", "Splatting", "Inpainting", "Merging", "Depth Map Editor")]
check("...under the headings, in pipeline order", heads == ["Everywhere", "Scene Cut", "DepthCrafter", "Splatting",
                                                           "Inpainting", "Merging", "Depth Map Editor"], str(heads))
dlg.setProperty("visible", False); pump(0.3)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
