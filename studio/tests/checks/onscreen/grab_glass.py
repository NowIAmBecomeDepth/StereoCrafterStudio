"""Window-only capture of a v2 window over OUR OWN test pattern (no user content
behind the glass). argv[1] = on|off (custom title bar), argv[2] = output png.
Env: INACTIVE=1 hands the focus to a separate process first (the system
backdrop went flat there, the accent acrylic must not); STEPS=1 resizes,
minimises, maximises and changes the opacity, reporting FLAT/frosted after each."""
import sys, os, tempfile, shutil, ctypes
from ctypes import wintypes
from pathlib import Path
# It reads SCREEN pixels (ImageGrab: the glass is DWM's, not in a grabWindow
# picture) -- against the standing rule unless the user said yes this time
# (bug sweep 2026-10-09, A12; memory screenshot-restraint): it won't run without
# STUDIO_ALLOW_SCREEN_GRAB=1.
if os.environ.get("STUDIO_ALLOW_SCREEN_GRAB") != "1":
    print("grab_glass reads screen pixels. Run it only with the user's OK, as "
          "STUDIO_ALLOW_SCREEN_GRAB=1 python ...grab_glass.py on|off out.png")
    sys.exit(2)
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _fixtures
mode, out = sys.argv[1], sys.argv[2]
tmp = Path(tempfile.mkdtemp())
shutil.copytree(Path(__file__).resolve().parents[4] / "config", tmp / "c")
if os.environ.get("BEGINNER"):   # capture with Studio > Use Beginner Mode on
    import json as _json
    _sj = tmp / "c" / "studio.json"; _d = _json.loads(_sj.read_text()); _d["beginner_mode"] = True
    _sj.write_text(_json.dumps(_d))
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "c"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "l.json")
import studio.shell as S
_p = S.StudioShell._pref
S.StudioShell._pref = lambda self, k, d: (mode == "on") if k == "custom_titlebar" else _p(self, k, d)
# the setting is ignored now (always custom): the comparison overrides the start-up flag itself
from PySide6.QtCore import Property as _Prop
S.StudioShell.customTitleBarAtStart = _Prop(bool, lambda self: mode == "on", constant=True)
from PySide6.QtCore import Qt, QRect, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from studio import app as A
QQuickStyle.setStyle("Basic")
QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QGuiApplication(sys.argv)

# The test pattern: bright diagonal stripes + big text, so blur is obvious.
pat = QQuickView()
pat.setFlags(Qt.Window | Qt.FramelessWindowHint)
qml = tmp / "pat.qml"
qml.write_text("""import QtQuick
Rectangle { color: "#ffffff"
  Repeater { model: 40; Rectangle { x: index * 60 - 400; y: -200; width: 26; height: 3000; rotation: 30
      color: ["#ff2d55", "#ffcc00", "#34c759", "#0a84ff", "#bf5af2"][index % 5] } }
  Text { anchors.centerIn: parent; text: "GLASS TEST"; font.pixelSize: 140; font.bold: true; color: "#111" } }""")
pat.setSource(QUrl.fromLocalFile(str(qml)))
pat.setResizeMode(QQuickView.SizeRootObjectToView)

e, o = A.build(app)
win = e.rootObjects()[0]
QTest.qWait(1500)
geo = win.frameGeometry()
pat.setGeometry(QRect(geo.x() - 60, geo.y() - 60, geo.width() + 120, geo.height() + 120))
pat.show(); QTest.qWait(300)
pat_h = int(pat.winId())
ctypes.windll.user32.SetWindowPos(pat_h, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_TOP
QTest.qWait(200)
u = ctypes.windll.user32
hwnd = int(win.winId())
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
u.SetForegroundWindow(hwnd)
QTest.qWait(1200)
print("ours is foreground:", u.GetForegroundWindow() == hwnd, "| active per Qt:", win.isActive(), flush=True)
if os.environ.get("INACTIVE"):   # ANOTHER PROCESS takes focus: the glass must stay frosted
    import subprocess
    code = ("import tkinter as t; r=t.Tk(); r.geometry('60x60+%d+%d'); r.attributes('-topmost',1); r.update(); "
            "print(int(r.wm_frame(), 16), flush=True); r.after(8000, r.destroy); r.mainloop()") % (geo.x() + 20, geo.y() + geo.height() + 10)
    other = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    oh = int(other.stdout.readline()); QTest.qWait(300)
    u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    u.keybd_event(0x12, 0, 0, 0); ok = u.SetForegroundWindow(oh); u.keybd_event(0x12, 0, 2, 0)   # Alt: lifts the foreground lock
    print("handed focus:", ok, hex(oh), flush=True); QTest.qWait(300)
    u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010); QTest.qWait(1500)
    print("the other process has the focus:", u.GetForegroundWindow() == oh, "| ours active per Qt:", win.isActive(), flush=True)
r = wintypes.RECT()
ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))  # extended frame bounds
from PIL import ImageGrab
if os.environ.get("STEPS"):   # everyday actions, one capture after each: "flat" = the glass is gone
    import numpy as np
    def shot(tag):
        QTest.qWait(900)
        rr = wintypes.RECT(); ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rr), ctypes.sizeof(rr))
        im = ImageGrab.grab(bbox=(rr.left, rr.top, rr.right, rr.bottom), all_screens=True)
        a = np.asarray(im.convert("L")).astype(float)[-90:-50, 60:-60]
        print(f"{tag:12s} std={a.std():6.1f} mean={a.mean():6.1f} {'FLAT' if a.std() < 4 else 'frosted'}", flush=True)
        im.save(out.replace(".png", f"_{tag}.png"))
    sh = o["Shell"]
    shot("start")
    win.setWidth(win.width() - 200); shot("narrower")
    win.setWidth(win.width() + 400); shot("wider")
    win.showMinimized(); QTest.qWait(700); win.showNormal(); u.SetForegroundWindow(hwnd); shot("restored")
    win.showMaximized(); shot("maximized"); win.showNormal(); shot("unmax")
    for v in ("0.3", "0.6", "auto"): sh.setProperty("glassOpacity", v); shot("op" + v)
    _fixtures.exit_now(0)
img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
img.save(out)
print(f"mode={mode} captured {img.size} glass={o['Shell'].property('glass')} palette={o['Shell'].property('paletteIndex')}", flush=True)
u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
_fixtures.exit_now(0)       # and the copied config goes with the temp folder
