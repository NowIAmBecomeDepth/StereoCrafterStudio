"""Settings > Appearance > Glass Opacity in the real app, offscreen: a knob
(30-90%, Auto by double-click) beside Frosted Glass; picking one reaches the theme at once (Theme.groundKeep, the tint
the ground keeps over the glass) and is saved in studio.json; Auto goes back
to the palette's default (70% dark, 50% light); a stale saved value snaps to
the nearest choice. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, Q_RETURN_ARG, qInstallMessageHandler
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

pump(0.6)
shell = objs["Shell"]
theme = engine.singletonInstance("Studio", "Theme")
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
dlg = [c for c in walk(win.contentItem(), []) if c.objectName() == "themePicker"][0]
QMetaObject.invokeMethod(dlg, "open"); pump(0.6)
knobs = [c for c in walk(win.contentItem(), []) if c.objectName() == "glassOpacityKnob"]
check("the header's theme button: Glass Opacity is a knob beside Frosted Glass, under the themes", len(knobs) == 1)
kb = knobs[0]
sw = [c for c in walk(win.contentItem(), []) if c.objectName() == "glassSwitch"][0]
dk = [c for c in walk(win.contentItem(), []) if c.objectName() == "darkPalettes"][0]
from PySide6.QtCore import QPointF as _P
ky, sy, ty = (x.mapToItem(win.contentItem(), _P(0, 0)).y() for x in (kb, sw, dk))
check("...level with the switch, the themes above", abs(ky - sy) < 40 and ky > ty, f"knob {ky:.0f} switch {sy:.0f} themes {ty:.0f}")
check("...30-90% in 10s, at Auto's value while Auto", (kb.property("from"), kb.property("to"), kb.property("step")) == (30, 90, 10)
      and shell.property("glassOpacity") == "auto" and kb.property("value") == round(theme.property("defaultKeep") * 100),
      f"{kb.property('from')}-{kb.property('to')} value {kb.property('value')}")
QMetaObject.invokeMethod(kb, "write", Q_ARG("QVariant", 30)); QMetaObject.invokeMethod(kb, "moved", Q_ARG("double", 30.0)); pump(0.2)
check("...turned to 30%: the setting is 0.3", shell.property("glassOpacity") == "0.3" and kb.property("value") == 30,
      shell.property("glassOpacity"))
QMetaObject.invokeMethod(kb, "reset"); pump(0.2)
check("...double-click / reset: back to Auto", shell.property("glassOpacity") == "auto", shell.property("glassOpacity"))
shell.setProperty("glassOpacity", "0.5"); pump(0.2)
check("...a value set elsewhere shows on the knob", kb.property("value") == 50, str(kb.property("value")))
shell.setProperty("glassOpacity", "auto"); pump(0.2)
QMetaObject.invokeMethod(dlg, "close"); pump(0.3)

shell.setProperty("glassOpacity", "auto"); pump(0.2)
dark = theme.property("isDark")
check("Auto = the palette's default", abs(theme.property("groundKeep") - (0.7 if dark else 0.5)) < 1e-6,
      f"dark={dark} keep={theme.property('groundKeep')}")
shell.setProperty("glassOpacity", "0.3"); pump(0.2)
check("30% reaches the theme at once", abs(theme.property("groundKeep") - 0.3) < 1e-6, str(theme.property("groundKeep")))
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("...and is saved", saved.get("glass_opacity") == "0.3", str(saved.get("glass_opacity")))
shell.setProperty("glassOpacity", "0.83"); pump(0.2)
check("a stale value snaps to the nearest choice", shell.property("glassOpacity") == "0.8", shell.property("glassOpacity"))
shell.setProperty("glassOpacity", "auto"); pump(0.2)
check("back to Auto", shell.property("glassOpacity") == "auto"
      and abs(theme.property("groundKeep") - (0.7 if dark else 0.5)) < 1e-6)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
# True Black (user, 2026-09-27): black ground and sections, and the glass still works on it
from PySide6.QtGui import QColor as _QC
_names = [p["name"] for p in shell.property("palettes")]
_was = shell.property("paletteIndex"); _glass = theme.property("glass")
shell.setProperty("paletteIndex", _names.index("True Black")); theme.setProperty("glass", 0.0); pump(1.5)
_g, _p = _QC(theme.property("ground")), _QC(theme.property("panel"))
check("True Black: the window and the sections pure black", _g.rgb() & 0xFFFFFF == 0 and _p.rgb() & 0xFFFFFF == 0
      and _g.alpha() == 255, f"ground {_g.name(_QC.HexArgb)} panel {_p.name(_QC.HexArgb)}")
theme.setProperty("glass", 1.0); pump(1.5)
check("...and glass still shows through it", _QC(theme.property("ground")).alpha() < 255
      and _QC(theme.property("panel")).alpha() < 255, _QC(theme.property("ground")).name(_QC.HexArgb))
theme.setProperty("glass", _glass); shell.setProperty("paletteIndex", _was); pump(0.8)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
