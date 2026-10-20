"""View > Show Section Outlines (v1 show_section_outlines), in the real app,
offscreen: on by default; every section (Section, FoldSection, the Splatting
and Merging preview panels) draws a 1px line in the palette's outline colour
(v1 ctk_theme: ctk_outline, else section_border, else the bar); off, none at
all; saved in studio.json; follows a palette change. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, qInstallMessageHandler
from PySide6.QtGui import QColor
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from PySide6.QtQml import QQmlExpression
def qml(item, expr):
    """A property QML can read but Python cannot (the border pen)."""
    e = QQmlExpression(engine.contextForObject(item) or engine.rootContext(), item, expr)
    v, bad = e.evaluate()
    return v
from core.ui.theme_manager import THEMES

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
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out

pump(0.8)
shell = objs["Shell"]
theme = engine.singletonInstance("Studio", "Theme")
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
pages = [stage.property("pages").property(i).toQObject() for i in range(5)]
def cards():
    out = []
    for pg in pages:
        for c in walk(pg, []):
            n = c.metaObject().className()
            if n.startswith(("Section_", "FoldSection_")) or c.objectName() == "previewPanel":
                out.append(c)
    return out
def expected(name):
    p = THEMES[name]
    return QColor(p.get("ctk_outline") or p.get("section_border") or p.get("panel_bar"))

dlg = [c for c in walk(win.contentItem(), []) if c.objectName() == "themePicker"][0]
QMetaObject.invokeMethod(dlg, "open"); pump(0.6)
item = ([c for c in walk(win.contentItem(), []) if c.objectName() == "outlinesSwitch"] or [None])[0]
check("the theme button's Section Outlines switch exists, on by default", item is not None and item.property("on") is True
      and shell.property("sectionOutlines") is True)
QMetaObject.invokeMethod(dlg, "close"); pump(0.3)
cs = cards()
kinds = {c.metaObject().className().split("_")[0] if c.objectName() != "previewPanel" else "previewPanel" for c in cs}
check("sections, fold sections and both preview panels found", {"Section", "FoldSection", "previewPanel"} <= kinds
      and sum(1 for c in cs if c.objectName() == "previewPanel") == 2, str(kinds))
for name in ("True Black", shell.property("palettes")[0]["name"]):
    idx = [p["name"] for p in shell.property("palettes")].index(name)
    shell.setProperty("paletteIndex", idx); pump(1.2)          # colours ease over ~0.5 s
    want = expected(name)
    hw = theme.property("outlineWidth")
    bad = [c for c in cs if abs(qml(c, "border.width") - hw) > 1e-6 or QColor(qml(c, "border.color")).name() != want.name()]
    check(f"on ({name}): every card 1px in {want.name()}", not bad,
          f"{len(bad)} off, e.g. {QColor(qml(bad[0], 'border.color')).name() if bad else ''}")
QMetaObject.invokeMethod(item, "flipped", Q_ARG(bool, False)); pump(0.3)
check("switched off: the setting goes off, and the switch follows", shell.property("sectionOutlines") is False
      and item.property("on") is False)
check("off: no card draws an outline", all(qml(c, "border.width") == 0 for c in cs))
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("...and it is saved", saved.get("show_section_outlines") is False, str(saved.get("show_section_outlines")))
shell.setProperty("sectionOutlines", True); pump(0.3)
check("back on", all(qml(c, "border.width") > 0 for c in cs))
# one PHYSICAL pixel (user, 2026-09-27: thinner): 1 / (pixel ratio x UI Scale zoom)
dpr = win.devicePixelRatio()
for sc in ("0.75", "1.0"):
    shell.setProperty("uiScale", sc); pump(0.4)
    want_w = 1 / (dpr * float(sc))
    check(f"UI Scale {sc}: the outline is one screen pixel ({want_w:.3f} page units)",
          all(abs(qml(c, "border.width") - want_w) < 1e-3 for c in cs), f"{qml(cs[0], 'border.width')}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
