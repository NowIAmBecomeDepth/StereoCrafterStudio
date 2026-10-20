"""Merging's Merge All SBS to 1 Video (user, 2026-10-11), in the real app, offscreen,
on a made-up project: the button left of View Output Folder; its dialog lists the
two scenes with no merged output, each needing its own stage; Send to Splatting
loads that scene there (source + depth map + the chapter's splat folder) and opens
Splatting; Merge Anyway writes the whole movie -- every scene in order, black for
the missing ones, at their length -- with Merging busy meanwhile; with every scene
merged the dialog offers Merge instead; Encoding Options has the Merge All tab.
Temp folders; configs and last_jobs.json redirected.

    python studio/tests/checks/check_merge_all.py
"""
import os, sys, tempfile, hashlib, time, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from studio import app as A
from studio import merge_all as M

EW, EH = 256, 144
DNX = ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"]
def clip(path, frames, codec=("-c:v", "libx264", "-pix_fmt", "yuv420p"), colour=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = f"color={colour}:s={EW}x{EH}:r=24" if colour else f"testsrc2=s={EW}x{EH}:r=24"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                    "-frames:v", str(frames)] + list(codec) + [path], check=True)

P = tmp / "Movie"
src, dep, spl, mer = (str(P / n) for n in ("M Source Clips", "M DepthMaps", "M Splats", "M Merged"))
os.makedirs(str(P / "M Inpainted" / "Chapter 2 - Two"), exist_ok=True)
for ch, stem, n in (("Chapter 1 - One", "shot00000100", 5), ("Chapter 1 - One", "shot00000300", 4),
                    ("Chapter 2 - Two", "shot00000600", 3), ("Chapter 2 - Two", "shot00000900", 4)):
    clip(os.path.join(src, ch, "finished", "finished", stem + ".mp4"), n)
for ch, stem, n in (("Chapter 1 - One", "shot00000100", 5), ("Chapter 2 - Two", "shot00000900", 4)):
    for eye, c in (("L", "red"), ("R", "blue")):
        clip(os.path.join(mer, ch, f"{stem}_{EW}_merged_full_sbs_{eye}.mov"), n, DNX, c)
clip(os.path.join(dep, "Chapter 1 - One", "shot00000300_depth.mp4"), 4)     # 300: needs Splatting
os.makedirs(os.path.join(spl, "Chapter 1 - One", "lowres"), exist_ok=True)  # 600: needs DepthCrafter

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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def _tree(it, out):
    for c in it.childItems():
        out.append(c); _tree(c, out)
    return out
def items(name):
    # the visual tree from the window's root (popups sit in its overlay; a Repeater's
    # rows are not QObject children of the window, so findChildren misses them)
    root = win.contentItem().parentItem() or win.contentItem()
    return [o for o in _tree(root, []) if o.objectName() == name]
def shown(name):
    return [i for i in items(name) if i.isVisible()]
def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint()); pump(0.3)
toasts = []
for name in ("Merging", "Splatting", "DepthCrafter"):
    objs[name].toast.connect(lambda k, t, m: toasts.append((k, t, m)))
errors = []
objs["Merging"].message.connect(lambda k, t, m: errors.append((k, t, m)))

shell, mg, sp = objs["Shell"], objs["Merging"], objs["Splatting"]
pump(0.6)
shell.setProperty("projectFolder", P.as_posix()); pump(0.4)
out_dir = (tmp / "out").as_posix()
mg.settings.setProperty("mergeall_output_folder", out_dir)
for k, v in (("mergeall_codec", "H.264"), ("mergeall_encoder", "Force CPU"), ("mergeall_quality", "Fastest"),
             ("mergeall_crf", "30")):
    mg.settings.setProperty(k, v)
shell.openStage(3, -1); pump(0.6)

btn = shown("mgMergeAll")
check("Merging shows Merge All SBS to 1 Video", len(btn) == 1)
view = next((i for i in win.findChildren(QQuickItem) if i.property("text") == "View Output Folder" and i.isVisible()), None)
def placed(width):
    """At the window's `width`: Merge All left of View Output Folder on its line, clear of the
    centred buttons, and inside the bar -- edges mapped into the scene (the page is scaled)."""
    win.setWidth(width); pump(0.6)
    def span(it):
        a, z = it.mapToScene(QPointF(0, 0)), it.mapToScene(QPointF(it.width(), it.height()))
        return a.x(), z.x(), a.y()
    mid, ab = shown("abButtons")[0], shown("actionBar")[0]
    (b0, b1, by), (v0, v1, vy), (m0, m1, my), (_a0, a1, _ay) = span(btn[0]), span(view), span(mid), span(ab)
    clear = m1 <= b0 + 1 or abs(my - by) > 1             # left of it, or on another line
    ok = abs(by - vy) < 1 and b1 <= v0 + 1 and clear and v1 <= a1 + 1
    return ok, f"centre {m0:.0f}-{m1:.0f}, Merge All {b0:.0f}-{b1:.0f}, View {v0:.0f}-{v1:.0f}, bar ends {a1:.0f}"
if btn and view:
    w0 = win.width()
    for w in (w0, 1280, 1600):
        ok, why = placed(w)
        check(f"...left of View Output Folder, clear of the other buttons, at {w} wide", ok, why)
    win.setWidth(w0); pump(0.4)

dlg = next(o for o in win.findChildren(QObject) if o.metaObject().className().startswith("MergeAllDialog"))
click(btn[0])
check("the dialog opens and looks", until(lambda: dlg.property("phase") != "scanning", 30), dlg.property("phase"))
check("...two scenes have no merged output", dlg.property("phase") == "missing" and len(dlg.property("missing") or []) == 2,
      str(dlg.property("phase")))
miss = dlg.property("missing") or []
check("...each with the stage it needs", [(m["stem"], m["stage"]) for m in miss] ==
      [("shot00000300", "splatting"), ("shot00000600", "depthcrafter")], str([(m["stem"], m["stage"]) for m in miss]))
rows = shown("mergeAllSend")
check("...a Send button per scene, named for its stage", [r.property("text") for r in rows] ==
      ["Send to Splatting", "Send to DepthCrafter"], str([r.property("text") for r in rows]))
check("...Merge Anyway offered, Merge not", bool(shown("mergeAllAnyway")) and not shown("mergeAllGo"))

if rows:
    click(rows[0])
    pump(0.5)
    check("Send to Splatting: the dialog closes", not dlg.property("visible"))
    check("...Splatting reads that scene and its depth map",
          str(sp.settings.property("input_source_clips")).replace("\\", "/").endswith("shot00000300.mp4")
          and str(sp.settings.property("input_depth_maps")).replace("\\", "/").endswith("shot00000300_depth.mp4"),
          (sp.settings.property("input_source_clips"), sp.settings.property("input_depth_maps")))
    check("...into the chapter's splat folder", "M Splats" in str(sp.settings.property("output_splatted")),
          str(sp.settings.property("output_splatted")))
    stage = next(i for i in win.findChildren(QQuickItem) if i.objectName() == "stage")
    check("...and Splatting is showing", stage.property("current") == 1, stage.property("current"))

shell.openStage(3, -1); pump(0.5)
click(shown("mgMergeAll")[0])
until(lambda: dlg.property("phase") == "missing", 30)
click(shown("mergeAllAnyway")[0])
check("Merge Anyway: Merging is busy", until(lambda: mg.property("busy"), 10))
check("...and says it is merging all", mg.property("mergingAll") and "Merge All" in mg.property("status"), mg.property("status"))
check("...and finishes", until(lambda: not mg.property("busy"), 120), mg.property("status"))
outs = [os.path.join(out_dir, f) for f in os.listdir(out_dir)] if os.path.isdir(out_dir) else []
check("one movie written, nothing else left there", [os.path.basename(p) for p in outs] == ["Movie - 3D Full SBS.mkv"], str(outs))
if outs:
    i = M.probe(outs[0])
    check("...every scene, black for the missing ones at their length", (i["w"], i["h"], i["frames"]) == (2 * EW, EH, 5 + 4 + 3 + 4),
          str(i))
check("...a success toast names it", any(k == "success" and "Movie - 3D Full SBS.mkv" in m for k, _t, m in toasts),
      str(toasts[-1:]))
check("...no error", not errors, str(errors))

# every scene merged now: the dialog asks to merge
for stem, n in (("shot00000300", 4), ("shot00000600", 3)):
    ch = "Chapter 1 - One" if stem.endswith("300") else "Chapter 2 - Two"
    for eye, c in (("L", "red"), ("R", "blue")):
        clip(os.path.join(mer, ch, f"{stem}_{EW}_merged_full_sbs_{eye}.mov"), n, DNX, c)
click(shown("mgMergeAll")[0])
check("all merged: the dialog offers Merge", until(lambda: dlg.property("phase") == "ready", 30)
      and bool(shown("mergeAllGo")) and not shown("mergeAllAnyway"), dlg.property("phase"))
check("...and says where it goes", "Movie - 3D Full SBS.mkv" in (shown("mergeAllReady")[0].property("text") if shown("mergeAllReady") else ""))
dlg.close(); pump(0.4)

# Encoding Options: the Merge All tab
enc = next(o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")
           and o.property("mergeAll"))
enc.open(); pump(0.5)
tabs = shown("encTabs")
check("Encoding Options has Clips | Merge All tabs", len(tabs) == 1)
enc.setProperty("tab", 1); pump(0.4)
check("...Merge All shows its section: layout, codec, the movie's sound, the folder",
      bool(shown("encMergeAll")) and bool(shown("encMergeAudio")) and bool(shown("encMergeOut")))
check("...and fits the window", enc.property("height") <= win.height(), f"{enc.property('height')} / {win.height()}")
enc.close(); pump(0.3)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
