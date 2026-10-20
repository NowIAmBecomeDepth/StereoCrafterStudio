"""Capture of a Studio window, rendered by Qt itself (win.grabWindow -- our
own window's pixels, never the screen's; bug sweep 2026-10-09, A12: this took
ImageGrab screen pixels, against the screenshot rule). The glass isn't in a
grabWindow picture; grab_glass.py is the one that needs the screen, and it
asks first. argv[1] = on|off (custom title bar), argv[2] = output png."""
import sys, os, tempfile, shutil, ctypes
from ctypes import wintypes
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _fixtures
mode, out = sys.argv[1], sys.argv[2]
stage_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 2
tmp = Path(tempfile.mkdtemp())
shutil.copytree(Path(__file__).resolve().parents[4] / "config", tmp / "c")
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "c"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "l.json")
import studio.shell as S
_p = S.StudioShell._pref
S.StudioShell._pref = lambda self, k, d: (mode == "on") if k == "custom_titlebar" else _p(self, k, d)
# the setting is ignored now (always custom): the comparison overrides the start-up flag itself
from PySide6.QtCore import Property as _Prop
S.StudioShell.customTitleBarAtStart = _Prop(bool, lambda self: mode == "on", constant=True)
from PySide6.QtCore import Qt, QRect, QUrl, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if ("qml" in m.lower() or ".qml" in m) else None)
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
QTest.qWait(800)
def _find(it, n):
    for ch in it.childItems():
        if ch.objectName() == n: return ch
        r = _find(ch, n)
        if r is not None: return r
_stage = _find(win.contentItem(), "stage")
QMetaObject.invokeMethod(_stage, "show", Q_ARG("QVariant", stage_idx), Q_ARG("QVariant", True))
QTest.qWait(1500)
if os.environ.get("OPEN_ALL"):
    _pg = _stage.property("pages").property(stage_idx).toQObject()
    _st = _pg.property("st")
    for k in ("output_settings_shown", "dev_tools_shown", "dual_eye", "custom_clip_range"):
        _st.setProperty(k, True)
    _st.setProperty("mask_mode", "Hybrid"); _st.setProperty("auto_convergence_mode", "Average")
    for k in ("popOpen", "breakOpen", "keyOpen"):
        _pg.setProperty(k, True)
    QTest.qWait(1200)
if os.environ.get("FIXTURE"):
    _fx = _fixtures.copy("small", tmp / "fx")
    _sp = o["Splatting"]
    for _k, _v in {"input_source_clips": str(_fx / "src").replace("\\", "/"),
                   "input_depth_maps": str(_fx / "depth").replace("\\", "/"),
                   "output_splatted": str(_fx / "out").replace("\\", "/"), "multi_map": False,
                   "auto_save_sidecar": False, "preview_size": "100%"}.items():
        _sp._set(_k, _v)
    _sp.preview.load()
    import traceback as _tb
    import time as _time
    from PySide6.QtCore import QCoreApplication as _QCA, QEventLoop as _QEL
    for _i in range(250):
        _t_end = _time.time() + 0.1
        while _time.time() < _t_end:
            _QCA.processEvents(_QEL.AllEvents, 10); _time.sleep(0.005)
        if _i % 30 == 29:
            for _tid, _fr in sys._current_frames().items():
                _st = _tb.extract_stack(_fr)
                if any("preview.py" in f.filename for f in _st):
                    print(f"t={_i/10:.0f}s worker at", " <- ".join(f"{f.name}:{f.lineno}" for f in _st[::-1][:4]), flush=True)
        if _sp.preview.property("loaded") and not _sp.preview.property("loading") and _sp.preview.property("imageSerial") > 0:
            break
    QTest.qWait(800)
    if not _sp.preview.property("loaded"):
        import faulthandler; faulthandler.dump_traceback(all_threads=True)
    print("preview loaded", _sp.preview.property("loaded"), "serial", _sp.preview.property("imageSerial"))
if os.environ.get("MERGE_FIXTURE"):
    # Merging (stage 3) on the synthetic `merge` clips, preview loaded.
    import time as _time
    from PySide6.QtCore import QCoreApplication as _QCA, QEventLoop as _QEL
    _fx = _fixtures.copy("merge", tmp / "mfx")
    _mg = o["Merging"]
    for _k, _v in {"inpainted_folder": str(_fx / "inpainted"), "original_folder": str(_fx / "orig"),
                   "mask_folder": str(_fx / "mask"), "preview_size": "100%"}.items():
        _mg._set(_k, _v.replace("\\", "/"))
    _mg.preview.load()
    _t_end = _time.time() + 30
    while _time.time() < _t_end and not (_mg.preview.property("loaded") and not _mg.preview.property("loading")):
        _QCA.processEvents(_QEL.AllEvents, 10); _time.sleep(0.005)
    print("merge preview loaded", _mg.preview.property("loaded"))
_pos = [int(v) for v in os.environ.get("POS", "40,40").split(",")]
win.setPosition(_pos[0], _pos[1]); QTest.qWait(1200)
if os.environ.get("CHAPTER"):
    _pg = _stage.property("pages").property(stage_idx).toQObject()
    _ctl = o[["DepthCrafter", "Splatting", "Inpainting"][stage_idx]]
    _chs = _ctl.property("chapters")
    print("chapters", len(_chs), "hasProject", _ctl.property("hasProject"))
    if _chs and _ctl.property("chapterNum") < 0:
        _ctl.pickChapter(_chs[0]["num"])
    if os.environ.get("CHAPTER") == "open":
        _pg.setProperty("foldersOpen", True)
    QTest.qWait(900)
    print("chapterMode", _pg.property("chapterMode"), "folded", _pg.property("pathsFolded"))
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
if os.environ.get("CLICK"):
    from PySide6.QtCore import QPoint
    cx, cy = [int(v) for v in os.environ["CLICK"].split(",")]
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(cx, cy)); QTest.qWait(900)
img = win.grabWindow()          # the scene Qt rendered: our window only
img.save(out)
print("QML warnings:", len(warns)); [print("   ", w[:220]) for w in warns[:15]]
print("zoom", win.property("zoom"), "screenH", win.property("screenHeight"))
print(f"size {win.width()}x{win.height()} min {win.property('minimumWidth')}x{win.property('minimumHeight')}")
print(f"mode={mode} captured {img.width()}x{img.height()} glass={o['Shell'].property('glass')} palette={o['Shell'].property('paletteIndex')}", flush=True)
u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
_fixtures.exit_now(0)       # and the copied config goes with the temp folder
