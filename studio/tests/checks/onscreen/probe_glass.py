"""Window styles and backdrop state with glass on, custom frame on (real window, off-screen)."""
import os, sys, tempfile, ctypes
from ctypes import wintypes
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "c"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "l.json")
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
u = ctypes.windll.user32; dwm = ctypes.windll.dwmapi
a = QGuiApplication(sys.argv)
e, o = A.build(a); w = e.rootObjects()[0]; QTest.qWait(400)
w.setPosition(-3000, -3000); QTest.qWait(200)
h = int(w.winId())
def dump(tag):
    st = u.GetWindowLongW(h, -16) & 0xFFFFFFFF; ex = u.GetWindowLongW(h, -20) & 0xFFFFFFFF
    v = ctypes.c_int(0); dwm.DwmGetWindowAttribute(h, 38, ctypes.byref(v), 4)
    print(f"{tag}: style={st:#010x} ex={ex:#010x} layered={bool(ex & 0x80000)} sysmenu={bool(st & 0x80000)} "
          f"popup={bool(st & 0x80000000)} backdrop={v.value} alphaFmt={w.format().hasAlpha()}")
dump("start")
print("DwmEnableBlurBehindWindow hr:", getattr(o["WinFrame"], "blur_behind_hr", None))
print("apply acrylic:", o["Backdrop"].apply("acrylic")); QTest.qWait(200)
dump("acrylic")
w.showMaximized(); QTest.qWait(500); dump("maximised"); w.showNormal(); QTest.qWait(500); w.setPosition(-3000, -3000); dump("restored")
th = e.rootObjects()[0]
o["Backdrop"].apply("none"); QTest.qWait(200); dump("glass off")
o["Backdrop"].apply("acrylic"); QTest.qWait(200); dump("glass on again")
os._exit(0)
