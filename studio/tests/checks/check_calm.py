"""While a stage runs a job the window must not redraw every frame: Qt Quick
draws on the GPU the job uses, and the busy indicators' continuous animation
cost Inpainting 8% (bench, 2026-09-26). Theme.calm follows "a stage is busy";
calm, the spinner / indeterminate bar tick a few times a second and the
progress sheen stops. Counts real frames, offscreen: busy + calm stays under
15/s; the same indicators uncalmed redraw far more. Audit 2026-09-28 O7: a
button's hover spring, a toast's countdown, the console's shadow and the VRAM
pill also redrew during jobs -- each is checked calm, and (the springs and the
toast) uncalmed, so the check can see the difference. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, qInstallMessageHandler
from PySide6.QtTest import QTest
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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.002)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
frames = [0]
win.frameSwapped.connect(lambda: frames.__setitem__(0, frames[0] + 1))
def rate(sec=2.0):
    pump(0.4); f0 = frames[0]; pump(sec); return (frames[0] - f0) / sec

pump(6)          # the startup animations run ~5 s
theme = engine.singletonInstance("Studio", "Theme")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(2)
ip = objs["Inpainting"]
objs["Shell"].setProperty("showTips", False); pump(0.5)     # the tip typing is idle's own motion
idle = rate()
check("idle: calm off, next to no redraws", theme.property("calm") is False and idle < 5, f"{idle:.1f}/s")

# a job "running" (the controller's busy flag, no engine)
ip._busy = True; ip._status = "Processing video 1 of 1"; ip.stateChanged.emit(); ip.infoChanged.emit()
pump(1.0)
check("busy: Theme.calm on", theme.property("calm") is True)
# no spinner / indeterminate bar any more (user, 2026-09-27): only the progress bar,
# which moves when the job reports progress -- a job leaves the window still
ab = find(win.contentItem(), lambda c: c.objectName() == "actionBar")
def _all(it, out):
    for c in it.childItems(): out.append(c); _all(c, out)
    return out
movers = [c.metaObject().className() for c in _all(ab, []) if c.isVisible()
          and c.metaObject().className().startswith(("Spinner", "IndeterminateBar"))]
check("busy: no spinner or indeterminate bar in the action bar", not movers, str(movers))
calm = rate(4.0)
check("busy + calm: the window stays still (under 1 redraw a second)", calm < 1, f"{calm:.2f}/s")
# a progress update while busy: calm, it lands in one frame or two (every
# transition is instant, Theme.dur 0); uncalmed its easing draws many more
def update_frames(n):
    ip._done = n; ip._total = 10; ip.stateChanged.emit(); ip.infoChanged.emit()
    f0 = frames[0]; pump(1.2); return frames[0] - f0
pump(0.5)
calm_frames = update_frames(3)
check("busy + calm: a progress update is one or two redraws", calm_frames <= 3, f"{calm_frames} frames")

# ---- O7: what else used to animate through a job ----------------------------------
def all_items(it, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        out.append(c); all_items(c, out)
    return out
def hover_frames(btn):
    """Frames drawn in the 1.5 s after the pointer lands on `btn` (and leaves)."""
    QTest.mouseMove(win, QPoint(3, 3)); pump(1.5)
    c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
    f0 = frames[0]
    QTest.mouseMove(win, QPoint(int(c.x()), int(c.y()))); pump(1.5)
    return frames[0] - f0
buttons = [b for b in all_items(win.contentItem()) if b.metaObject().className().startswith("AccentButton")
           and b.isVisible() and b.width() > 20 and b.property("enabled")]
btn = buttons[0] if buttons else None
calm_hover = hover_frames(btn) if btn is not None else -1
check("busy + calm: hovering a button is a redraw or two, not a spring ringing",
      btn is not None and calm_hover <= 3, f"{calm_hover} frames")
def toast_rate(sec=3.0):
    QTest.mouseMove(win, QPoint(3, 3)); pump(0.3)
    QMetaObject.invokeMethod(theme, "toast", Q_ARG("QVariant", "info"), Q_ARG("QVariant", "A clip finished"),
                             Q_ARG("QVariant", "calm check"))
    pump(0.6); f0 = frames[0]; pump(sec); return (frames[0] - f0) / sec
calm_toast = toast_rate()
check("busy + calm: a toast's countdown ticks a few times a second", calm_toast <= 3, f"{calm_toast:.1f}/s")
pump(9.5)                                        # let it go
# the console is a window of its own now (2026-09-30); its lines still in no layer
from PySide6.QtCore import QObject
console_win = win.findChild(QObject, "consoleWindow")
console = find(console_win.contentItem(), lambda c: c.objectName() == "consoleBody") if console_win else None
from PySide6.QtQml import QQmlProperty
def layer_on(item):
    return bool(QQmlProperty.read(item, "layer.enabled"))
def layered_ancestor(item):
    p = item.parentItem() if item is not None else None
    while p is not None:
        if layer_on(p):
            return p.objectName() or p.metaObject().className()
        p = p.parentItem()
    return None
log_list = find(console, lambda c: c.metaObject().className().startswith("QQuickListView")) if console else None
check("the console's lines are not inside its shadow's layer (a log line no longer re-renders the blur)",
      console is not None and log_list is not None and not layer_on(console)
      and layered_ancestor(log_list) is None, str(layered_ancestor(log_list)))
vram = objs["Vram"]
check("busy: the VRAM pill republishes in coarse steps", vram.property("coarse") is True)
published = []
vram.changed.connect(lambda: published.append(1))
vram._used, vram._total = 8.0, 16.0
vram._publish(8.2, 16.0); vram._publish(8.4, 16.0)
small = len(published)
vram._publish(8.6, 16.0)
check("...0.2 GB moves are not shown, 0.6 GB is", small == 0 and len(published) == 1, f"{small} then {len(published)}")

theme.setProperty("calm", False); pump(0.5)
wild_hover = hover_frames(btn) if btn is not None else -1
check("...uncalmed the same hover springs over many frames", wild_hover > 3 * max(calm_hover, 1),
      f"{wild_hover} vs {calm_hover} frames")
wild_toast = toast_rate()
check("...uncalmed a toast redraws every frame of its countdown", wild_toast > 4 * max(calm_toast, 0.5),
      f"{wild_toast:.1f}/s vs {calm_toast:.1f}/s")
pump(9.5)
wild_frames = update_frames(6)
check("...uncalmed the same update eases over many (the check can see the difference)",
      wild_frames > 3 * max(calm_frames, 1), f"{wild_frames} vs {calm_frames} frames")
ip._busy = False; ip.stateChanged.emit(); pump(0.3)
check("finished: calm off again", theme.property("calm") is False)
check("finished: the VRAM pill back to 0.1 GB steps", vram.property("coarse") is False)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
