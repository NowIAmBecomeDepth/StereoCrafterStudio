"""Film grain over the window (user, 2026-09-28), in the real app offscreen:
each theme's grain from dependency/grain_by_theme.json (a theme with none
saved gets grain.DEFAULTS; a theme saved at 0 gets none); the theme popout's
Film Grain switch turns it off; the grain is drawn over the window but never
over the Splatting preview's picture; one grain per screen pixel; nothing
animates. Configs and last_jobs.json redirected; the fixture is a TEMP copy."""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, QPointF, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import grain as G

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def named(n, root=None):
    return find(root or win.contentItem(), lambda c: c.objectName() == n)
pump(0.8)

names = [p["name"] for p in shell.property("palettes")]
saved = G.load()
layer = named("grainLayer")
img_ = lambda: named("grainImage", layer)      # the pieces are rebuilt as holes move
check("the grain layer and its tile are in the window", layer is not None and img_() is not None)

# ---- per theme: the saved grain, the defaults where nothing is saved, none at 0
def src():
    i = img_()
    return str(i.property("source").toString()) if i is not None and layer.property("visible") else ""
rows = []
for i, n in enumerate(names):
    shell.setProperty("paletteIndex", i); pump(0.15)
    want = G.source(saved.get(n, G.DEFAULTS))
    rows.append((n, src() == want and bool(layer.property("visible")) == (want != ""), src(), want))
bad = [r for r in rows if not r[1]]
check(f"every theme draws its own saved grain ({len(saved)} saved, the rest the defaults)", not bad, str(bad[:3]))
zero = [n for n, v in saved.items() if not v["enabled"] or v["amount"] <= 0]
if zero:
    shell.setProperty("paletteIndex", names.index(zero[0])); pump(0.2)
    check(f"a theme saved at no grain ({zero[0]}) draws none", not layer.property("visible"))
unsaved = [n for n in names if n not in saved]
if unsaved:
    shell.setProperty("paletteIndex", names.index(unsaved[0])); pump(0.2)
    check(f"a theme with nothing saved ({unsaved[0]}) gets the tuner's defaults", src() == G.source(G.DEFAULTS), src())

# a theme with grain, for the rest
gi = next(i for i, n in enumerate(names) if G.source(saved.get(n, G.DEFAULTS)))
shell.setProperty("paletteIndex", gi); pump(0.6)
dpr = win.screen().devicePixelRatio()
check("one grain per screen pixel: the tile drawn at 1/dpr",
      abs(img_().property("scale") - 1 / dpr) < 1e-6 and abs(img_().width() - win.width() * dpr) < 2, f"dpr {dpr}")
check("never takes the mouse", layer.property("enabled") is False)

# ---- the Splatting preview stays clean
sp = objs["Splatting"]; pv = sp.preview
stage = named("stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "auto_save_sidecar": False}.items():
    sp._set(k, v)
pv.load()
t0 = time.time()
while not pv.property("loaded") and time.time() - t0 < 60: pump(0.1)
pump(1.5)
holes = layer.property("holes")
holes = holes.toVariant() if hasattr(holes, "toVariant") else holes
pic = named("previewImage")
check("the preview's picture is cut out of the grain", pv.property("loaded") and len(holes) == 1, str(holes))
if holes and pic is not None:
    h = holes[0]
    p0 = pic.mapToScene(QPointF(0, 0)); p1 = pic.mapToScene(QPointF(pic.width(), pic.height()))
    check("...the hole is the picture, inside the preview's frame",
          abs(h["x"] - max(p0.x(), 0)) <= 2 and abs(h["w"] - (min(p1.x(), win.width()) - max(p0.x(), 0))) <= 3,
          f"hole {h} picture {p0.x():.0f},{p0.y():.0f}-{p1.x():.0f},{p1.y():.0f}")
    # the picture's pixels with grain on == with grain off; the window's ground differs
    shot_on = win.grabWindow()
    if os.environ.get("GRAIN_SHOT"): shot_on.save(os.environ["GRAIN_SHOT"])
    shell.setProperty("filmGrain", False); pump(0.5)
    check("the Film Grain switch turns it off", not layer.property("visible"))
    shot_off = win.grabWindow()
    shell.setProperty("filmGrain", True); pump(0.5)
    def diff(x0, y0, x1, y1):
        n = 0
        for y in range(int(y0), int(y1), 3):
            for x in range(int(x0), int(x1), 3):
                a, b = shot_on.pixelColor(int(x * dpr), int(y * dpr)), shot_off.pixelColor(int(x * dpr), int(y * dpr))
                n += abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
        return n
    inside = diff(h["x"] + 4, h["y"] + 4, h["x"] + h["w"] - 4, h["y"] + h["h"] - 4)
    outside = diff(8, win.height() - 60, 200, win.height() - 30)
    check("...the picture is untouched by the grain, the window around it is not", inside == 0 and outside > 0,
          f"inside {inside}, outside {outside}")

# ---- the switch in the theme popout
tp = named("themePicker")
QMetaObject.invokeMethod(tp, "open"); pump(0.8)
sw = named("grainSwitch")
check("the theme popout has a Film Grain switch", sw is not None and sw.isVisible())
tg = find(sw, lambda c: c.objectName() == "settingsSwitch") if sw is not None else None
if tg is not None:
    c = tg.mapToScene(QPointF(tg.width() / 2, tg.height() / 2))
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPoint, Qt
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.5)
    check("...clicking it turns the grain off, and it is remembered", shell.property("filmGrain") is False
          and not layer.property("visible")
          and json.loads((tmp / "config" / "studio.json").read_text()).get("film_grain") is False)
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.5)
    check("...and on again", shell.property("filmGrain") is True and layer.property("visible"))
QMetaObject.invokeMethod(tp, "close"); pump(0.4)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
pv.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
