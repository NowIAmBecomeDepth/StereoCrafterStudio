"""The notification bell (user, 2026-10-05): between Settings and the VRAM
pill, it keeps the session's toasts newest first, counts the unread ones on
the bell, reads them all when opened, and Clear All empties it. Offscreen,
config and last_jobs.json redirected.

    python studio/tests/checks/check_notification_bell.py [SHOT.png]
"""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QPointF, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlProperty
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 900); win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out


def named(n):
    return [c for c in walk(win.contentItem(), []) if c.objectName() == n]


pump(1.0)
theme = engine.singletonInstance("Studio", "Theme")
bell = named("notificationBell")[0]
gear = named("settingsButton")[0]
vram = named("vramPill")
x = lambda it: it.mapToScene(QPointF(0, 0)).x()        # noqa: E731
check("the bell sits right of Settings" + (" and left of the VRAM pill" if vram and vram[0].isVisible() else ""),
      x(bell) > x(gear) and (not vram or not vram[0].isVisible() or x(bell) + bell.width() <= x(vram[0])),
      f"gear {x(gear):.0f}, bell {x(bell):.0f}" + (f", vram {x(vram[0]):.0f}" if vram else ""))
tp = named("themePicker")[0]
check("the theme picker sits between the bell and the VRAM pill (user, 2026-10-08)",
      x(tp) >= x(bell) + bell.width() and (not vram or not vram[0].isVisible() or x(tp) + tp.width() <= x(vram[0])),
      f"bell ends {x(bell) + bell.width():.0f}, picker {x(tp):.0f}-{x(tp) + tp.width():.0f}")
n0 = bell.property("count")
QMetaObject.invokeMethod(theme, "toast", Q_ARG("QVariant", "info"), Q_ARG("QVariant", "Folders set"), Q_ARG("QVariant", "Input and output point at Chapter 1."))
QMetaObject.invokeMethod(theme, "toast", Q_ARG("QVariant", "success"), Q_ARG("QVariant", "Splatting finished"), Q_ARG("QVariant", "20 clips in 1h 02m 13s."))
QMetaObject.invokeMethod(theme, "toast", Q_ARG("QVariant", "warning"), Q_ARG("QVariant", "VRAM low"), Q_ARG("QVariant", "Close the preview to free memory."))
pump(0.4)
badge = named("bellBadge")[0]
check("each toast is kept, and the bell counts the unread ones",
      bell.property("count") >= n0 + 3 and bell.property("unread") >= 3 and badge.isVisible(),
      f"count {bell.property('count')}, unread {bell.property('unread')}")
from PySide6.QtCore import QObject
popup = [o for o in bell.findChildren(QObject) if o.objectName() == "bellPopup"][0]
QMetaObject.invokeMethod(popup, "open"); pump(0.8)
lst = [o for o in bell.findChildren(QObject) if o.objectName() == "bellList"]
check("opening it shows them, newest first, and reads them all",
      popup.property("opened") is True and bell.property("unread") == 0 and not badge.isVisible(), str(bell.property("unread")))
titles = []
if lst:
    for c in walk(lst[0], []):
        if c.metaObject().className().startswith("QQuickText") and c.property("text") in ("Folders set", "Splatting finished", "VRAM low"):
            titles.append((c.mapToScene(QPointF(0, 0)).y(), c.property("text")))
check("...newest at the top", [t for _y, t in sorted(titles)][:3] == ["VRAM low", "Splatting finished", "Folders set"], str(sorted(titles)))
QMetaObject.invokeMethod(theme, "toast", Q_ARG("QVariant", "info"), Q_ARG("QVariant", "While open"), Q_ARG("QVariant", ""))
pump(0.2)
check("a toast arriving while it is open is not counted unread", bell.property("unread") == 0)
if len(sys.argv) > 1:
    for c in walk(win.contentItem(), []) + walk(QQmlProperty(popup, "contentItem").read(), []):
        try:
            if QQmlProperty(c, "layer.enabled").read():
                QQmlProperty(c, "layer.enabled").write(False)
        except Exception:
            pass
    pump(0.3)
    win.grabWindow().copy(700, 0, 700, 520).save(sys.argv[1])
    print("   saved", sys.argv[1])
clear = [o for o in bell.findChildren(QObject) if o.objectName() == "bellClear"][0]
QMetaObject.invokeMethod(clear, "clicked"); pump(0.3)
check("Clear All empties it", bell.property("count") == 0)
QMetaObject.invokeMethod(popup, "close"); pump(0.4)
check("closed, nothing left counted as a modal", theme.property("modalOpen") is False)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
