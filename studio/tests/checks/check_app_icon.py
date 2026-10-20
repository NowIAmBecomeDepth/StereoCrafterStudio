"""The app icon (user, 2026-10-08): Navy Blue matte by default, picked in the theme
popout's App Icon section (fifteen colourways, matte or glossy, and Classic); the
window, the title bar and the job notifications follow it, and it is saved in
studio.json. Offscreen, config and last_jobs.json redirected.

    python studio/tests/checks/check_app_icon.py [SHOT.png]
"""
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
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio.shell import ICONS_DIR

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


pump(0.8)
shell = objs["Shell"]
notifier = objs.get("JobNotifier")
icons = shell.property("appIcons")
check("31 icons: fifteen colourways matte and glossy, and Classic", len(icons) == 31
      and sum(i["finish"] == "matte" for i in icons) == 15 and sum(i["finish"] == "glossy" for i in icons) == 15
      and icons[-1]["id"] == "classic", str(len(icons)))
check("...each with its .ico and thumbnail", all((ICONS_DIR / (i["id"] + ".ico")).exists()
                                                  and (ICONS_DIR / (i["id"] + ".png")).exists() for i in icons))
check("Navy Blue matte by default", shell.property("appIcon") == "navy_blue_matte", shell.property("appIcon"))
key0 = qapp.windowIcon().cacheKey()
check("...on the window", not qapp.windowIcon().isNull())
title_img = [c for c in walk(win.contentItem(), []) if c.metaObject().className() == "QQuickImage"
             and str(c.property("source").toString() if hasattr(c.property("source"), "toString") else c.property("source")).endswith(".ico")]
src = lambda: str(title_img[0].property("source").toString()) if title_img else ""  # noqa: E731
check("...in the title bar", bool(title_img) and src().endswith("navy_blue_matte.ico"), src())
check("...and on the notifications", notifier is not None and str(notifier._icon_path).endswith("navy_blue_matte.ico"),
      str(getattr(notifier, "_icon_path", None)))

tp = named("themePicker")[0]
QMetaObject.invokeMethod(tp, "open"); pump(0.8)
cells = lambda: [c for c in walk(tp, []) if c.objectName() == "appIconCell"] or \
    [c for c in tp.findChildren(QObject) if c.objectName() == "appIconCell"]  # noqa: E731
popup_items = [o for o in tp.findChildren(QObject) if o.objectName() == "themePopup"]
allc = [c for c in walk(popup_items[0].property("contentItem"), []) if c.objectName() == "appIconCell"] if popup_items else []
check("the theme popout shows them: the fifteen matte ones and Classic", len(allc) == 16, str(len(allc)))
cur = [c for c in allc if c.property("current")]
check("...the current one marked", len(cur) == 1 and cur[0].property("modelData")["id"] == "navy_blue_matte")

plum = [c for c in allc if c.property("modelData")["id"] == "plum_matte"][0]
shell.setProperty("appIcon", "plum_matte"); pump(0.4)
check("picking one (Plum): it is the app icon", shell.property("appIcon") == "plum_matte")
check("...the window's icon changes", qapp.windowIcon().cacheKey() != key0)
check("...the title bar's", src().endswith("plum_matte.ico"), src())
check("...the notifications'", str(notifier._icon_path).endswith("plum_matte.ico"))
saved = json.loads((tmp / "config" / "studio.json").read_text(encoding="utf-8"))
check("...and it is saved", saved.get("app_icon") == "plum_matte", str(saved.get("app_icon")))

fin = [o for o in popup_items[0].findChildren(QObject) if o.objectName() == "iconFinish"] or \
      [c for c in walk(popup_items[0].property("contentItem"), []) if c.objectName() == "iconFinish"]
QMetaObject.invokeMethod(fin[0], "activated", Q_ARG(int, 1)); pump(0.5)
check("Glossy: the same colours, the other finish", shell.property("appIcon") == "plum_glossy", shell.property("appIcon"))
allc = [c for c in walk(popup_items[0].property("contentItem"), []) if c.objectName() == "appIconCell"]
check("...and the grid shows the glossy set", len(allc) == 16 and all(c.property("modelData")["finish"] in ("glossy", "")
                                                                       for c in allc))
if len(sys.argv) > 1:
    win.grabWindow().save(sys.argv[1])
    print("   saved", sys.argv[1])
shell.setProperty("appIcon", "classic"); pump(0.3)
check("Classic, the icon Studio had before", shell.property("appIcon") == "classic" and src().endswith("classic.ico"))
shell.setProperty("appIcon", "no_such_icon"); pump(0.2)
check("an unknown one is ignored", shell.property("appIcon") == "classic")
# bug sweep A1: from Classic, Glossy then a glossy icon then Matte -- the switch and grid follow
QMetaObject.invokeMethod(fin[0], "activated", Q_ARG(int, 1)); pump(0.3)
check("Classic + Glossy: the glossy set shows, the icon stays Classic",
      shell.property("appIcon") == "classic" and fin[0].property("currentIndex") == 1)
shell.setProperty("appIcon", "plum_glossy"); pump(0.3)
QMetaObject.invokeMethod(fin[0], "activated", Q_ARG(int, 0)); pump(0.5)
allc = [c for c in walk(popup_items[0].property("contentItem"), []) if c.objectName() == "appIconCell"]
check("...then a glossy icon and Matte: the matte icon, the switch on Matte, the matte set",
      shell.property("appIcon") == "plum_matte" and fin[0].property("currentIndex") == 0
      and all(c.property("modelData")["finish"] in ("matte", "") for c in allc),
      f"{shell.property('appIcon')} index {fin[0].property('currentIndex')}")
QMetaObject.invokeMethod(tp, "close"); pump(0.4)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
