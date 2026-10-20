"""v2 at a given window width: elided/overflowing text, items past the page
edge, and the height the page needs. Offscreen, config + handoff redirected."""
import os, sys, tempfile, hashlib
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QPointF, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.setProperty("minimumWidth", 400)
win.show(); QTest.qWait(300)

def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
stage = find(win.contentItem(), lambda c: c.property("pages") is not None and c.property("current") is not None)

def eff_visible(it, stop):
    p = it
    while p is not None and p is not stop:
        if not p.isVisible() or p.opacity() < 0.02: return False
        p = p.parentItem()
    return True

def text_of(it):
    t = it.property("text")
    return (t or "")[:40]

def ab_overlaps(page):
    ab = None
    stack = [page]
    while stack:
        it = stack.pop()
        for ch in it.childItems():
            if ch.objectName() == "actionBar": ab = ch
            stack.append(ch)
    if ab is None: return []
    boxes = []
    def walk(it):
        for ch in it.childItems():
            cls = ch.metaObject().className()
            if eff_visible(ch, page) and ch.width() > 0 and cls.startswith(("AccentButton", "Later", "SpCheck", "SpBox", "TextBox", "Caption", "AnimCheck")):
                tl = ch.mapToItem(page, QPointF(0, 0)); boxes.append((cls[:10] + ":" + str(ch.property("text"))[:18], tl.x(), tl.y(), ch.width(), ch.height()))
            walk(ch)
    walk(ab)
    bad = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a[1] < b[1] + b[3] - 0.5 and b[1] < a[1] + a[3] - 0.5 and a[2] < b[2] + b[4] - 0.5 and b[2] < a[2] + a[4] - 0.5:
                bad.append(f"abOverlap {a[0]} x {b[0]}")
    return bad

def audit(page):
    probs = []
    pw = page.width()
    def walk(it):
        for c in it.childItems():
            if not eff_visible(c, page) or c.width() <= 0 or c.height() <= 0:
                continue
            cls = c.metaObject().className()
            r = c.mapToItem(page, QPointF(c.width(), 0)).x()
            if cls.startswith("QQuickText") and not cls.startswith("QQuickTextInput") and not cls.startswith("QQuickTextEdit"):
                if c.property("truncated"):
                    probs.append(f"elided   {text_of(c)!r}")
                elif c.property("contentWidth") > c.width() + 1:
                    probs.append(f"spills   {text_of(c)!r} ({c.property('contentWidth'):.0f}>{c.width():.0f})")
            if r > pw - 12 + 0.5 and not cls.startswith("QQuickItem"):
                probs.append(f"pastEdge {cls[:22]} {text_of(c)!r} right={r:.0f} page={pw:.0f}")
            sec = c.parentItem()
            while sec is not None and sec is not page and not sec.metaObject().className().startswith(("Section", "FoldSection", "ClipInfo")):
                sec = sec.parentItem()
            if sec is not None and sec is not page:
                sr = sec.mapToItem(page, QPointF(sec.width(), 0)).x()
                if r > sr + 0.5:
                    probs.append(f"outOfSec {cls[:22]} {text_of(c)!r} right={r:.0f} section={sr:.0f}")
            if cls.startswith(("TextBox", "Dropdown")) and c.width() < 56:
                probs.append(f"narrow   {cls[:22]} w={c.width():.0f}")
            if c.property("implicitWidth") and cls.startswith(("AccentButton", "AnimCheck", "Dropdown", "Toggle")) and c.width() + 0.5 < c.property("implicitWidth"):
                probs.append(f"squeezed {cls[:22]} {text_of(c)!r} {c.width():.0f}<{c.property('implicitWidth'):.0f}")
            walk(c)
    walk(page)
    return probs

names = {0: "DepthCrafter", 1: "Splatting", 2: "Inpainting"}
widths = [int(a) for a in sys.argv[1:]] or [790, 900, 1000, 1220]
objs["Shell"].setProperty("uiScale", "1.0"); QTest.qWait(300)
for idx in ([int(x) for x in os.environ.get("PAGES","0,2").split(",")]):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); QTest.qWait(700)
    page = stage.property("pages").property(idx).toQObject()
    ctl = objs["DepthCrafter"] if idx == 0 else objs["Inpainting"]
    print("page visible", page.isVisible(), "opacity", page.opacity(), "x", page.x(), "children", len(page.childItems()))
    if idx == 1 and os.environ.get("OPEN"):
        st = page.property("st")
        for k in ("output_settings_shown", "dev_tools_shown", "dual_eye", "custom_clip_range"):
            st.setProperty(k, True)
        st.setProperty("mask_mode", "Hybrid"); st.setProperty("auto_convergence_mode", "Average")
        for k in ("popOpen", "breakOpen", "keyOpen"):
            page.setProperty(k, True)
        QTest.qWait(900)
    if idx == 0 and os.environ.get("SEG"):
        st = page.property("st"); print("st", type(st))
        st.setProperty("process_as_segments_var", True); QTest.qWait(900)
    for w in widths:
        win.setProperty("width", w); QTest.qWait(400)
        probs = audit(page) + ab_overlaps(page)
        need = win.property("minimumHeight")
        print(f"{names[idx]:12s} w={w}  minHeight={need}  problems={len(probs)}")
        if os.environ.get('DUMP') and w == widths[0]:
            def dump(it, d=0):
                for ch in it.childItems():
                    cn = ch.metaObject().className()
                    if cn.startswith(("FieldRow", "CheckField", "Section")) and eff_visible(ch, page):
                        x0 = ch.mapToItem(page, QPointF(0, 0)).x()
                        print(f"        {cn[:10]:10s} x={x0:4.0f} w={ch.width():4.0f} {(ch.property('label') or ch.property('text') or ch.property('title') or '')[:34]!r}")
                    dump(ch, d+1)
            dump(page)
        for p in sorted(set(probs))[:int(os.environ.get('N','25'))]:
            print("     ", p)
print("last_jobs unchanged:", h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None))
os._exit(0)
