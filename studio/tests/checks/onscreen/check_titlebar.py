"""Custom caption on a REAL window (native platform, off-screen): client covers
the window, hit-tests, max-button hover/click, maximised fit. No screenshots."""
import os, sys, tempfile, hashlib, ctypes
from ctypes import wintypes
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]; sys.path.insert(0, str(REPO))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest()
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
import studio.platform_win as PW
if os.environ.get("NO_TME"): PW._TME_LEAVE = 0  # off-screen: the real cursor is elsewhere
if os.environ.get("TRACE"):
    _om = PW.WindowFrame._on_message
    def _trace(self, msg):
        before = self._hover
        r = _om(self, msg)
        if msg.message in (0xA0, 0xA1, 0xA2, 0x200, 0x2A2) or before != self._hover:
            print(f"    msg {msg.message:#06x} wp={msg.wParam} hover {before}->{self._hover} ret={r}")
        return r
    PW.WindowFrame._on_message = _trace

u = ctypes.windll.user32
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
frame = objs["WinFrame"]
QTest.qWait(500)
win.setPosition(-3000, -3000); QTest.qWait(300)
hwnd = int(win.winId())
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}")
    if not ok: fails.append(label)
def find(it, name):
    for c in it.childItems():
        if c.objectName() == name: return c
        r = find(c, name)
        if r is not None: return r

wr, cr = wintypes.RECT(), wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(wr)); u.GetClientRect(hwnd, ctypes.byref(cr))
dpr = win.devicePixelRatio()
print(f"dpr {dpr}  window {wr.right-wr.left}x{wr.bottom-wr.top}  client {cr.right}x{cr.bottom}  qt {win.width()}x{win.height()}")
check("client area is the whole window", (cr.right, cr.bottom) == (wr.right - wr.left, wr.bottom - wr.top))
check("Qt size matches the client", abs(win.width() * dpr - cr.right) <= 1 and abs(win.height() * dpr - cr.bottom) <= 1)
style = u.GetWindowLongW(hwnd, -16)
check("has caption + thick frame styles (shadow, snap)", (style & 0x00C00000) == 0x00C00000 and style & 0x00040000)

tb = find(win.contentItem(), "titleBar")
def ht(item_or_pt, fx=0.5, fy=0.5):
    if isinstance(item_or_pt, tuple):
        x, y = item_or_pt
    else:
        p = item_or_pt.mapToScene(QPointF(item_or_pt.width() * fx, item_or_pt.height() * fy)); x, y = p.x(), p.y()
    pt = wintypes.POINT(int(x * dpr), int(y * dpr)); u.ClientToScreen(hwnd, ctypes.byref(pt))
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    return u.SendMessageW(hwnd, 0x0084, 0, lp)
maxb, minb, closeb = find(tb, "captionMax"), find(tb, "captionMin"), find(tb, "captionClose")
check("title bar drags (HTCAPTION)", ht((win.width() / 2, 16)) == 2)
check("maximise button is HTMAXBUTTON (snap layouts)", ht(maxb) == 9)
check("minimise is client (QML handles it)", ht(minb) == 1)
check("close is client", ht(closeb) == 1)
check("page is client", ht((win.width() / 2, win.height() / 2)) == 1)
check("left edge resizes", ht((2, win.height() / 2)) == 10)
check("right edge resizes", ht((win.width() - 2, win.height() / 2)) == 11)
check("bottom edge resizes", ht((win.width() / 2, win.height() - 2)) == 15)
check("top edge resizes", ht((win.width() / 2, 2)) == 12)
check("bottom-right corner", ht((win.width() - 2, win.height() - 2)) == 17)

# max button hover/press/click through the non-client messages
p = maxb.mapToScene(QPointF(maxb.width() / 2, maxb.height() / 2))
pt = wintypes.POINT(int(p.x() * dpr), int(p.y() * dpr)); u.ClientToScreen(hwnd, ctypes.byref(pt))
lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
# Delivered straight to the frame: posted, Windows' own leave tracking fires at
# once because the real cursor is not over this off-screen window.
m = PW._MSG(); m.hwnd = hwnd; m.message = 0x00A0; m.wParam = 9; m.lParam = lp
frame._on_message(m)   # read before the leave it arms is processed
print("    frame.maxHovered", frame.property("maxHovered"), "button.hovered", maxb.property("hovered"), "nativeFrame", tb.property("nativeFrame"), "pressed", maxb.property("pressed"))
check("hovering it lights it", frame.property("maxHovered") is True and maxb.property("hovered") is True)
u.PostMessageW(hwnd, 0x00A1, 9, lp); QTest.qWait(50)
check("pressing it", frame.property("maxPressed") is True)
u.PostMessageW(hwnd, 0x00A2, 9, lp); QTest.qWait(700)
check("clicking maximises", "Maximized" in str(win.property("visibility")), f"visibility={win.property('visibility')}")

# maximised: client exactly on the monitor's work area
class MI(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
mi = MI(); mi.cbSize = ctypes.sizeof(MI)
u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
cr2 = wintypes.RECT(); u.GetClientRect(hwnd, ctypes.byref(cr2))
tl = wintypes.POINT(0, 0); u.ClientToScreen(hwnd, ctypes.byref(tl))
work = mi.rcWork
got = (tl.x, tl.y, tl.x + cr2.right, tl.y + cr2.bottom)
check("maximised client == work area (nothing off-screen)", got == (work.left, work.top, work.right, work.bottom), f"{got} vs {(work.left, work.top, work.right, work.bottom)}")
check("maximised: top edge is the title bar, not a resize edge", ht((win.width() / 2, 2)) == 2)
check("glyph shows Restore", maxb.property("glyph") == "\ue923")
u.PostMessageW(hwnd, 0x00A1, 9, lp); u.PostMessageW(hwnd, 0x00A2, 9, lp); QTest.qWait(700)
check("clicking again restores", "Windowed" in str(win.property("visibility")), f"visibility={win.property('visibility')}")
u.PostMessageW(hwnd, 0x0200, 0, 0); QTest.qWait(50)
check("moving into the client clears hover", frame.property("maxHovered") is False)
win.setPosition(-3000, -3000)
_lj = h0 == hashlib.sha256(rec.read_bytes()).hexdigest(); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _fixtures
_fixtures.exit_now(0 if not fails and _lj else 1)
