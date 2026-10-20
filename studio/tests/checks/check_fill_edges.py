"""Edge Fill (Extra > Edge Fill; user, 2026-10-14 -- Merging's Fill Edges dialog until then;
core/removal/edge_fill.py), in the real app, offscreen, on made-up clips. Merging's Fill Edges
button opens the page on Merging's folders; the page looks: a grid of stills, each clip's right
eye with its strip measured (remembered: not measured again) and marked; Steps and Resolution
on the left; right-click a tile to fill just it or play its files; the size slider; Select All;
Steps refuses 0; Fill runs as Edge Fill's own job -- busy, its status, the console's Extra tab
with a bar per stretch -- with ROSE stood in for, rewrites the right eye's files and leaves the
left eye's; the grid looks again after. Merging's Upcoming Jobs right-click fills one clip.
Temp folders; configs and last_jobs.json redirected.

    python studio/tests/checks/check_fill_edges.py
"""
import os, sys, tempfile, hashlib, time, subprocess, json, shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from studio import app as A
from core.removal import erase as E, edge_fill as F, rose_env
import core.common.recycle_bin as RB

W, H, N, STRIP = 320, 144, 10, 18
def encode(path, frames, args):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                          f"{frames[0].shape[1]}x{H}", "-r", "24", "-i", "-", *args, path], stdin=subprocess.PIPE)
    p.stdin.write(b"".join(np.ascontiguousarray(f).tobytes() for f in frames)); p.stdin.close(); assert p.wait() == 0
def picture(i):
    x = np.linspace(0, 1, W, dtype=np.float32)[None, :, None]
    img = np.broadcast_to(np.concatenate([60 + 120 * x, 90 + 0 * x, 140 - 40 * x], axis=2), (H, W, 3)).copy()
    img[:, 40 + 5 * i: 52 + 5 * i] += 40
    return np.clip(img, 0, 255).astype(np.uint8)

OUT, MASKS = str(tmp / "Merged"), str(tmp / "Splats" / "hires")
eyes = [picture(i) for i in range(N)]
right = [e.copy() for e in eyes]
for r in right:
    r[:, :STRIP] = (20, 10, 40)
DNX = ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"]
encode(os.path.join(OUT, f"shot_{W}_merged_full_sbs_L.mov"), eyes, DNX)
encode(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov"), right, DNX)
mask = np.zeros((H, W, 3), np.uint8); mask[:, :STRIP] = 255
encode(os.path.join(MASKS, "finished", f"shot_{W}_splatted2.mkv"), [np.hstack([mask, p]) for p in eyes],
       ["-c:v", "ffv1", "-pix_fmt", "bgr0"])
# a second clip, for the single-clip fill from Upcoming Jobs: unticked before the first Fill
encode(os.path.join(OUT, f"take_{W}_merged_full_sbs_L.mov"), eyes, DNX)
encode(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov"), right, DNX)
encode(os.path.join(MASKS, f"take_{W}_splatted2.mkv"), [np.hstack([mask, p]) for p in eyes],
       ["-c:v", "ffv1", "-pix_fmt", "bgr0"])

jobs_seen = []
def fake_worker(job_path, n_chunks, steps, progress, stop, lo, hi, bars=False):
    """ROSE's stand-in: the frames back, the masked part magenta; a little slow, so busy shows.
    Its step bars as the real loop draws them (erase.step_bar; test_rose_stop runs that loop)."""
    job = json.load(open(job_path, encoding="utf-8")); jobs_seen.append(job)
    for s in range(steps + 1) if bars else ():
        progress(lo + (hi - lo) * s / steps, f"ROSE: window 1 of {n_chunks}, step {s} of {steps}")
        E.step_bar(1, n_chunks, s, steps, 0.12 if s else None)
    for c in job["chunks"]:
        fr = np.load(c["frames"]).copy(); fr[np.load(c["masks"]) > 0] = (250, 20, 200); np.save(c["out"], fr)
    time.sleep(1.0)
real_run_worker = E._run_worker                  # for the quit check at the end
E._run_worker = fake_worker
measured = []                                    # the grid's measuring (not a fill's), for "remembered"
_measure = F.measure
import threading
def counted_measure(path, stop=None):
    if threading.current_thread().name == "fill-edges-scan":
        measured.append(path)
    return _measure(path, stop)
F.measure = counted_measure
rose_env.ready = lambda: True
RB.send_to_recycle_bin = lambda p: False         # the originals into edge_fill_originals/

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
    root = win.contentItem().parentItem() or win.contentItem()
    return [o for o in _tree(root, []) if o.objectName() == name]
def shown(name):
    return [i for i in items(name) if i.isVisible()]
def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint()); pump(0.3)
def type_into(item, text):
    click(item)
    QTest.keyClick(win, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for ch in text:
        QTest.keyClick(win, Qt.Key(ord(ch)))
    QTest.keyClick(win, Qt.Key.Key_Return); pump(0.3)
toasts, errors = [], []
mg, shell, ef = objs["Merging"], objs["Shell"], objs["EdgeFill"]
for ctl in (mg, ef):
    ctl.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
    ctl.message.connect(lambda k, t, m: errors.append((k, t, m)))
rejected = []
ef.fieldRejected.connect(rejected.append)
from studio import outcome as _outcome
finishes = []
_outcome.FINISH_LISTENERS.append(lambda k, t, m: finishes.append((k, t)))
def picked():
    v = page.property("picked"); return list(v.toVariant() if hasattr(v, "toVariant") else v)
def strips():
    return [t.property("text") for t in shown("fillEdgesStrip")]

pump(0.6)
mg.settings.setProperty("output_folder", OUT)
mg.settings.setProperty("mask_folder", MASKS)
ef.settings.setProperty("merged_folder", str(tmp / "elsewhere"))      # Merging's button points it at Merging's
shell.openStage(3, -1); pump(0.6)

btn = shown("mgFillEdges")
check("Merging shows Fill Edges", len(btn) == 1)
merge_all = shown("mgMergeAll")
if btn and merge_all:
    a, b = btn[0].mapToScene(QPointF(0, 0)), merge_all[0].mapToScene(QPointF(0, 0))
    check("...beside Merge All, on its line", abs(a.y() - b.y()) < 1 and a.x() < b.x(), f"{a.x():.0f} vs {b.x():.0f}")

page = next(o for o in win.findChildren(QObject) if o.objectName() == "edgeFillPage")
extra = next(o for o in win.findChildren(QObject) if o.metaObject().className().startswith("ExtraPage"))
click(btn[0])
check("Fill Edges opens Extra > Edge Fill", until(lambda: page.isVisible() and extra.property("tool") == 2, 10)
      and shown("extraSwitch"), f"tool {extra.property('tool')}")
check("...on Merging's Output and Mask folders", os.path.normcase(str(ef.settings.property("merged_folder")).replace("/", "\\"))
      == os.path.normcase(OUT) and os.path.normcase(str(ef.settings.property("mask_folder")).replace("/", "\\")) == os.path.normcase(MASKS),
      f"{ef.settings.property('merged_folder')} | {ef.settings.property('mask_folder')}")
check("the page looks: the grid", until(lambda: page.property("phase") == "list", 30), page.property("phase"))
clips = page.property("clips"); clips = clips.toVariant() if hasattr(clips, "toVariant") else clips
check("...lists the merged clips, their masks found, ready", [(c["name"], c["state"]) for c in clips or []]
      == [(f"shot_{W}_merged_full_sbs", "ready"), (f"take_{W}_merged_full_sbs", "ready")], str(clips))
left, grid_sec = shown("edgeFillLeft"), shown("edgeFillClips")
steps_box, size_box = shown("fillEdgesSteps"), shown("fillEdgesSize")
check("...Steps and Resolution on the left, the grid on the right",
      bool(left and grid_sec and steps_box and size_box)
      and steps_box[0].mapToScene(QPointF(0, 0)).x() < grid_sec[0].mapToScene(QPointF(0, 0)).x()
      and size_box[0].mapToScene(QPointF(0, 0)).x() < grid_sec[0].mapToScene(QPointF(0, 0)).x())
tiles = shown("fillEdgesTile")
check("...as a grid: one tile per clip, side by side", len(tiles) == 2
      and abs(tiles[0].mapToScene(QPointF(0, 0)).y() - tiles[1].mapToScene(QPointF(0, 0)).y()) < 1, str(len(tiles)))
check("...each strip measured: 18 px, the right eye's left edge",
      until(lambda: strips().count("18 px strip  ·  R eye, left edge") == 2, 30), str(strips()))
meas = page.property("measured"); meas = meas.toVariant() if hasattr(meas, "toVariant") else meas
m0 = (meas or {}).get(f"shot_{W}_merged_full_sbs", {})
check("...the still is the right eye: the _R file, whole", m0.get("thumb", "").endswith("_R.mov") and m0.get("part") == "whole", str(m0))
thumbs = shown("fillEdgesThumb")
check("...the stills load", until(lambda: all(t.property("implicitWidth") > 2 for t in shown("fillEdgesThumb")), 20)
      and len(thumbs) == 2, str([t.property("implicitWidth") for t in thumbs]))
from studio.clip_thumbs import EdgeThumbProvider
from urllib.parse import quote
from PySide6.QtCore import QSize
still = EdgeThumbProvider().requestImage(f"3/whole/{quote(os.path.join(OUT, f'shot_{W}_merged_full_sbs_R.mov'))}", QSize(), QSize())
px = still.pixelColor(2, still.height() // 2)
check("...its strip shows dark at the still's left", still.width() > 100 and px.red() < 50 and px.green() < 40,
      f"{still.width()}x{still.height()} {px.red()},{px.green()},{px.blue()}")
check("...a line marks where each strip ends", len(shown("fillEdgesStripLine")) == 2
      and all(abs(l.property("x") - l.property("px") - (l.parentItem().width() - shown("fillEdgesThumb")[0].property("paintedWidth")) / 2) < 1
              for l in shown("fillEdgesStripLine")))
QTest.mouseClick(win, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                 tiles[1].mapToScene(QPointF(tiles[1].width() / 2, tiles[1].height() - 8)).toPoint()); pump(0.4)
tm = next(o for o in win.findChildren(QObject) if o.objectName() == "fillEdgesTileMenu")
check("...a right-click on a tile offers Fill Just This Clip", tm.property("visible") and tm.property("name") == f"take_{W}_merged_full_sbs",
      str(tm.property("name")))
plays = shown("fillEdgesPlay")
check("...and Play for each of its files, the eye with the bar first", [str(o.property("text")).split()[1] for o in plays]
      == [f"take_{W}_merged_full_sbs_R.mov", f"take_{W}_merged_full_sbs_L.mov"]
      and "the eye with the bar" in str(plays[0].property("text")), str([o.property("text") for o in plays]))
opened = []
import studio.shell as _sh
_sh.os.startfile = lambda p: opened.append(p)
if plays:
    click(plays[0])
check("...Play opens that file in the default player", [os.path.normcase(p) for p in opened]
      == [os.path.normcase(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov"))], str(opened))
# "Open ... in Explorer" (user, 2026-10-18): the merged clip and its hi-res splat; Explorer stood in for
revealed = []
import core.ui.folder_open as _FO
_FO.reveal_file = lambda p: (revealed.append(os.path.normcase(os.path.normpath(p))), (True, p))[1]
QTest.mouseClick(win, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                 tiles[1].mapToScene(QPointF(tiles[1].width() / 2, tiles[1].height() - 8)).toPoint()); pump(0.4)
explores = shown("fillEdgesExplore")
check("...and Open Merged Clip / Hi-Res Splat in Explorer",
      [str(o.property("text")) for o in explores] == ["Open Merged Clip in Explorer", "Open Hi-Res Splat in Explorer"],
      str([o.property("text") for o in explores]))
for k in range(len(explores)):       # a pick closes the menu: open it again, its items made anew
    if not tm.property("visible"):
        QTest.mouseClick(win, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                         tiles[1].mapToScene(QPointF(tiles[1].width() / 2, tiles[1].height() - 8)).toPoint()); pump(0.4)
    click(shown("fillEdgesExplore")[k])
check("...they select the merged clip (the eye with the bar) and its splat",
      len(revealed) >= 2 and revealed[0] == os.path.normcase(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov"))
      and os.path.basename(revealed[1]).startswith(f"take_{W}_splatted2"), str(revealed))
if tm.property("visible"):
    tm.close()
pump(0.3)

def right_of(it):
    return it.mapToScene(QPointF(it.width(), 0)).x()
size0 = win.size()
over = {}
for scale in ("1.0", "1.25"):
    shell.setProperty("uiScale", scale); pump(0.5)
    win.resize(1000, size0.height()); pump(0.8)
    sec = shown("edgeFillClips")[0]
    edge = right_of(sec) - 1
    things = shown("fillEdgesTile") + shown("edgeFillRefresh") + shown("fillEdgesPerRow") + shown("fillEdgesBigger") + shown("fillEdgesTileSize")
    over[scale] = [round(right_of(t) - edge) for t in things if right_of(t) > edge + 0.5]
    if len(things) < 6 or sec.width() > 760:      # the tiles and controls found, and the section narrow
        over[scale] = over[scale] or [f"nothing to measure: {len(things)} items, section {sec.width():.0f} px"]
shell.setProperty("uiScale", "auto"); win.resize(size0); pump(0.8)
check("a narrow window: the tiles, the toolbar and Refresh stay inside the Clips section (user, 2026-10-14)",
      not any(over.values()), str(over))
cols0 = int(str(shown("fillEdgesPerRow")[0].property("text")).split()[0]) if shown("fillEdgesPerRow") else 0
for _ in range(10):
    click(shown("fillEdgesBigger")[0])
cols1 = int(str(shown("fillEdgesPerRow")[0].property("text")).split()[0])
check("the + button makes the thumbnails bigger, fewer to a row", cols1 < cols0
      and tiles[0].width() > 300 and int(ef.settings.property("tile")) > 300,
      f"{cols0} -> {cols1} a row, tile {tiles[0].width():.0f} px, kept {ef.settings.property('tile')}")
for _ in range(15):
    click(shown("fillEdgesSmaller")[0])
cols2 = int(str(shown("fillEdgesPerRow")[0].property("text")).split()[0])
check("...the - button smaller, more to a row, down to 120", cols2 > cols0 and int(ef.settings.property("tile")) == 120,
      f"{cols2} a row, kept {ef.settings.property('tile')}")
ef.settings.setProperty("tile", 200); pump(0.3)
check("...the slider follows the setting", shown("fillEdgesTileSize")[0].property("value") == 200)
sl = shown("fillEdgesTileSize")[0]
a0, a1 = sl.mapToScene(QPointF(8, sl.height() / 2)).toPoint(), sl.mapToScene(QPointF(sl.width() - 4, sl.height() / 2)).toPoint()
QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a0); pump(0.1)
for k in range(1, 11):
    QTest.mouseMove(win, a0 + (a1 - a0) * (k / 10.0)); pump(0.03)
QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a1); pump(0.3)
check("...dragging it to the right end: the biggest, kept", int(ef.settings.property("tile")) == 420,
      str(ef.settings.property("tile")))
ef.settings.setProperty("tile", 200); pump(0.3)
check("...and after a drag it still follows the setting", sl.property("value") == 200, str(sl.property("value")))

tiles = shown("fillEdgesTile")
click(tiles[1])
check("a click on a tile unticks it", picked() == [f"shot_{W}_merged_full_sbs"], str(picked()))
sa = shown("fillEdgesSelectAll")
check("Select All offered while one is unticked", bool(sa) and sa[0].property("text") == "Select All",
      str([x.property("text") for x in sa]))
click(sa[0])
check("...ticks every clip", sorted(picked()) == [f"shot_{W}_merged_full_sbs", f"take_{W}_merged_full_sbs"]
      and sa[0].property("text") == "Deselect All", f"{picked()} {sa[0].property('text')}")
click(sa[0])
check("...then Deselect All unticks them all", picked() == [] and sa[0].property("text") == "Select All"
      and shown("fillEdgesGo") and not shown("fillEdgesGo")[0].property("enabled"), str(picked()))
click(tiles[0])
check("...and a tile ticks again on its own", picked() == [f"shot_{W}_merged_full_sbs"], str(picked()))
check("Fill offered for the ticked clip", bool(shown("fillEdgesGo")) and shown("fillEdgesGo")[0].property("enabled")
      and shown("fillEdgesGo")[0].property("text") == "Fill 1 Clip")
check("...no ROSE warning (ROSE is there)", not shown("fillEdgesNoRose"))
check("...Resolution offers 560, 784 and Native (tiled), 560 picked", [str(s).split()[0] for s in (ef.property("sizes") or [])]
      == ["560", "784", "Native"] and str(ef.settings.property("size")).startswith("560"), str(ef.settings.property("size")))
check("...Match Detail offered, on", bool(shown("fillEdgesDetail")) and ef.settings.property("detail") is True)
check("...the note on what it suits: simple backgrounds, not faces or text (user, 2026-10-18)",
      bool(shown("fillEdgesSuits")) and "simple backgrounds" in shown("fillEdgesSuits")[0].property("text"))
check("...Right Edge Too offered, off: the right eye's left edge only (user, 2026-10-18)",
      bool(shown("fillEdgesBothEdges")) and ef.settings.property("both_edges") is False)
check("...Steps shows the default, 6", shown("fillEdgesSteps") and shown("fillEdgesSteps")[0].property("text") == "6",
      shown("fillEdgesSteps")[0].property("text") if shown("fillEdgesSteps") else "")
if shown("fillEdgesSteps"):
    type_into(shown("fillEdgesSteps")[0], "9")
    check("typing 9 into Steps sets it", int(ef.settings.property("steps")) == 9, str(ef.settings.property("steps")))
    type_into(shown("fillEdgesSteps")[0], "0")
    check("...0 is refused, 9 kept", "steps" in rejected and int(ef.settings.property("steps")) == 9,
          f"{rejected} {ef.settings.property('steps')}")
ef.settings.setProperty("steps", "inf"); pump(0.3)
try:
    ef.save(); saved = True
except Exception as e:
    saved = repr(e)
check("Steps refuses 'inf' too, and settings still save (sweep 2026-10-14)",
      str(ef.settings.property("steps")) == "9" and saved is True, f"{ef.settings.property('steps')} {saved}")

l_id = F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_L.mov"))
r_id = F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov"))
still_before = shown("fillEdgesThumb")[0].property("source").toString()
time.sleep(1.1)                                    # the rewrite gets a later mtime (its URL's version)
if shown("fillEdgesGo"):
    click(shown("fillEdgesGo")[0])
check("Fill: Edge Fill is busy", until(lambda: ef.property("busy"), 10))
check("...the bar's status names the clip", until(lambda: f"shot_{W}_merged_full_sbs" in ef.property("status"), 10), ef.property("status"))
check("...and it finishes", until(lambda: not ef.property("busy"), 120), ef.property("status"))
check("...ROSE got the typed steps, for the ticked clip only", len(jobs_seen) == 1 and jobs_seen[0]["steps"] == 9,
      str([j["steps"] for j in jobs_seen]))
check("...the right eye's file rewritten, the left eye's left alone",
      F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov")) != r_id
      and F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_L.mov")) == l_id)
after = list(E._reader(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov"), W, H, "rgb24"))
check("...its strip is the fill", len(after) == N and np.abs(after[3][:, 2:STRIP].reshape(-1, 3).mean(0) - (250, 20, 200)).max() < 25,
      str(after[3][:, 2:STRIP].reshape(-1, 3).mean(0).round()) if after else "no frames")
check("...the original kept", os.path.isfile(os.path.join(OUT, F.ORIGINALS_DIR, f"shot_{W}_merged_full_sbs_R.mov")))
check("...a success toast names the edge", any(k == "success" and "R eye left edge" in m for k, _t, m in toasts), str(toasts[-1:]))
check("...no error", not errors, str(errors))
check("...and a finish notification, as the stages give (sweep 2026-10-14b)", ("success", "Edge Fill finished") in finishes,
      str(finishes))
pump(0.5)
con = objs["Logs"].tab("extra")
text = con.allText()
want = ["=== Edge Fill: 1 clip(s); 9 steps, size 560, grain on, detail on ===", "  Detail: ", f"[1/1] shot_{W}_merged_full_sbs",
        "  Strip: R eye left edge 26 px", "  ROSE: ", "  Wrote shot_", "  Filled in ", "=== Edge Fill done: 1 filled"]
check("the console's Extra tab tells the fill", all(w in text for w in want),
      str([w for w in want if w not in text]) + " | " + text[-1500:])
# (this check's own "[OK]" prints during the fill end a bar's line early: more than one row here)
last = {}
for r in [r for r in con._rows if r[4]]:
    last[r[4][0].strip()] = r[4]
check("...a bar for each stretch, drawn as bars, each ending full: reading, ROSE's window, blend + write",
      [k for k in last] == ["Reading edges:", "ROSE window 1/1:", "Blend + write:"]
      and all(abs(b[1] - 1.0) < 1e-6 for b in last.values()), str({k: round(b[1], 3) for k, b in last.items()}))
check("...ROSE's bar counts steps and s/it", "9/9 [0.12s/it]" in last.get("ROSE window 1/1:", ["", 0, ""])[2],
      str(last.get("ROSE window 1/1:")))

# the grid looks again after a fill
check("after the fill the grid looks again: the filled clip is done", until(lambda: [c["state"] for c in
      (lambda v: v.toVariant() if hasattr(v, "toVariant") else v)(page.property("clips")) or []] == ["done", "ready"], 30))
check("...its tile says Filled", until(lambda: any(t.startswith("Filled") for t in strips()), 30), str(strips()))
check("...and its still is asked for again: a new URL, not Qt's cached pre-fill picture (sweep 2026-10-14)",
      until(lambda: shown("fillEdgesThumb") and shown("fillEdgesThumb")[0].property("source").toString() != still_before, 10),
      still_before)
check("...not ticked", picked() == [], str(picked()))
# filled this run: green, as the clip lists show a finished row (user, 2026-10-18); the other tile not
from PySide6.QtGui import QColor as _QC
_tiles = shown("fillEdgesTile")
_green = [t.property("finished") for t in _tiles]
check("...its tile turns green, the unfilled one doesn't", _green == [True, False]
      and _QC(_tiles[0].property("color")).green() > _QC(_tiles[0].property("color")).red()
      and _QC(_tiles[0].property("color")) != _QC(_tiles[1].property("color")),
      f"{_green} {[_QC(t.property('color')).name(_QC.HexArgb) for t in _tiles]}")
# ...and a clip filled before this session (only its record says so) is green too (user, 2026-10-18)
ef._filled_now = []; ef.filledChanged.emit(); pump(0.3)
check("...a clip filled in an earlier run stays green", [t.property("finished") for t in shown("fillEdgesTile")] == [True, False])
check("...both strips remembered from the first look, not measured again", len(measured) == 2
      and until(lambda: [t.endswith("18 px strip  ·  R eye, left edge") for t in strips()] == [True, True], 10),
      f"{len(measured)} measures; " + str(strips()))
click(shown("edgeFillRefresh")[0])
check("Refresh looks again, still nothing measured again", until(lambda: page.property("phase") == "list", 20)
      and until(lambda: len(strips()) == 2 and all("18 px" in t for t in strips()), 10) and len(measured) == 2, str(len(measured)))

# Merging's Upcoming Jobs right-click Fill Edges: by the inpainted clip's name, on Merging's folders
n0 = len(toasts)
ef.fillClip(f"C:/x/other_{W}_inpainted_right_eye.mp4", OUT, MASKS); pump(0.2)
check("Fill Edges on a clip not merged yet: says so", toasts[n0:] and toasts[-1][1] == "Not merged yet", str(toasts[n0:]))
ef.fillClip(f"C:/x/shot_{W}_inpainted_right_eye.mp4", OUT, MASKS); pump(0.2)
check("...on one already filled: says so, no job", toasts[-1][1] == "Already filled" and not ef.property("busy"), str(toasts[-1:]))
t_id = F._file_id(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov"))
ef.fillClip(f"C:/x/take_{W}_inpainted_right_eye.mp4", OUT, MASKS)
check("...on a merged one: Edge Fill fills it", until(lambda: ef.property("busy"), 10))
n1 = len(toasts)
ef.useFolders(str(tmp / "x"), str(tmp / "y")); pump(0.2)
check("Merging's Fill Edges during a fill says the folders wait (sweep 2026-10-14)",
      any(t == "Edge Fill is busy" for _k, t, _m in toasts[n1:])
      and os.path.normcase(str(ef.settings.property("merged_folder")).replace("/", "\\")) == os.path.normcase(OUT),
      str(toasts[n1:]))
check("...and finishes", until(lambda: not ef.property("busy"), 120), ef.property("status"))
check("...that clip only, rewritten", len(jobs_seen) == 2
      and F._file_id(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov")) != t_id, str(len(jobs_seen)))
qs = [o for o in win.findChildren(QObject) if o.objectName() in ("jobQueue", "jobQueueCompact")]
acts = [a for q in qs for a in (q.property("rowActions").toVariant() if hasattr(q.property("rowActions"), "toVariant") else q.property("rowActions") or [])]
check("...offered in Merging's Upcoming Jobs right-click menu", any(a.get("id") == "filledges" for a in acts), str(acts))
pump(0.5)
n2 = len(toasts)
ef.fillClip(f"C:/x/take_{W}_inpainted_right_eye.mp4", "", ""); pump(0.2)
check("right-click Fill with Merging's folders empty says so, not Studio's own folder (sweep 2026-10-14)",
      toasts[n2:] and toasts[-1][1] == "No folders", str(toasts[n2:]))
# the busy fill above asked for Merging's folders after it: put the page back on OUT
ef.useFolders(OUT, MASKS); pump(0.2)
check("the page back on the merged folder", until(lambda: page.property("phase") == "list"
      and os.path.normcase(str((page.property("info").toVariant() if hasattr(page.property("info"), "toVariant") else page.property("info")).get("folder", "")).replace("/", "\\")) == os.path.normcase(OUT), 20))

# a slower, older look doesn't overwrite a newer one (sweep 2026-10-14)
SLOW = str(tmp / "SlowFolder"); os.makedirs(SLOW, exist_ok=True)
_find_groups = F.find_groups
def slow_find_groups(folder):
    if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(SLOW):
        time.sleep(2.5)
    return _find_groups(folder)
F.find_groups = slow_find_groups
ef.settings.setProperty("merged_folder", SLOW); ef.scan()
ef.settings.setProperty("merged_folder", OUT); ef.scan()
pump(4.0)
info = page.property("info"); info = info.toVariant() if hasattr(info, "toVariant") else info
check("a slower look of an earlier folder doesn't overwrite the newer one's grid",
      page.property("phase") == "list" and os.path.normcase(str(info.get("folder", "")).replace("/", "\\")) == os.path.normcase(OUT),
      str(info.get("folder")))
F.find_groups = _find_groups

# Fill works on the folder the grid was looked at in, not the field as typed since
OTHER = str(tmp / "Other"); os.makedirs(OTHER, exist_ok=True)
for n_ in (f"shot_{W}_merged_full_sbs_L.mov", f"shot_{W}_merged_full_sbs_R.mov"):
    shutil.copy(os.path.join(OUT, F.ORIGINALS_DIR, n_) if os.path.exists(os.path.join(OUT, F.ORIGINALS_DIR, n_)) else os.path.join(OUT, n_), os.path.join(OTHER, n_))
other_r = F._file_id(os.path.join(OTHER, f"shot_{W}_merged_full_sbs_R.mov"))
out_r = F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov"))
until(lambda: page.property("phase") == "list", 10)
ef.settings.setProperty("merged_folder", OTHER)
ef.fill([f"shot_{W}_merged_full_sbs"])
until(lambda: ef.property("busy"), 10); until(lambda: not ef.property("busy"), 120)
check("Fill fills the clip the grid shows, not a same-named one in a folder typed since (sweep 2026-10-14)",
      F._file_id(os.path.join(OTHER, f"shot_{W}_merged_full_sbs_R.mov")) == other_r
      and F._file_id(os.path.join(OUT, f"shot_{W}_merged_full_sbs_R.mov")) != out_r)
ef.settings.setProperty("merged_folder", OUT); pump(1.5)
until(lambda: page.property("phase") == "list", 20)

# no look during another stage's job: it waits, then looks (sweep 2026-10-14)
sc = objs["SceneCut"]
n_meas = len(measured)
sc._busy, sc._loading, sc._then = True, True, (lambda *a: None)
sc.stateChanged.emit(); pump(0.4)
click(shown("edgeFillRefresh")[0]); pump(1.0)
check("another stage's job running: Refresh waits instead of looking", page.property("phase") == "waiting"
      and bool(shown("edgeFillWaiting")) and len(measured) == n_meas, page.property("phase"))
check("...the waiting line names the stage, and Fill is off (sweep 2026-10-14b)",
      ("Scene Cut" in str(shown("edgeFillWaiting")[0].property("text")) and bool(shown("fillEdgesGo"))
       and not shown("fillEdgesGo")[0].property("enabled")) if shown("edgeFillWaiting") else False,
      str(shown("edgeFillWaiting")[0].property("text")) if shown("edgeFillWaiting") else "")
sc._busy, sc._loading, sc._then = False, False, None
sc.stateChanged.emit()
check("...and looks once that job ends", until(lambda: page.property("phase") == "list", 20), page.property("phase"))
check("...Fill back on when clips are ticked", bool(shown("fillEdgesGo"))
      and shown("fillEdgesGo")[0].property("enabled") == bool(picked()), str(picked()))
# a fill the GPU hand-over refuses leaves the grid's measuring alone (sweep 2026-10-14b)
jobs_obj = objs["GpuJobs"]
look_before = ef._look
jobs_obj.claim = lambda *a, **k: False
ef.fill([f"shot_{W}_merged_full_sbs"]); pump(0.3)
del jobs_obj.claim
check("a refused fill doesn't stop the grid's measuring", ef._look is look_before and not look_before.is_set()
      and not ef.property("busy"))

# Edge Fill's folders follow a project switch: Merging's Output and Mask folders (user, 2026-10-14)
PROJ = str(tmp / "Projects" / "Movie Z"); os.makedirs(PROJ, exist_ok=True)
ef.settings.setProperty("merged_folder", str(tmp / "stale_merged")); ef.settings.setProperty("mask_folder", str(tmp / "stale_masks"))
shell.setProperty("projectFolder", PROJ); pump(1.0)
def norm(p_):
    return os.path.normcase(os.path.abspath(str(p_ or "")))
mv = mg._values()
check("a project switch: Edge Fill's folders are Merging's Output and Mask folders",
      norm(ef.settings.property("merged_folder")) == norm(mv.get("output_folder"))
      and norm(ef.settings.property("mask_folder")) == norm(mv.get("mask_folder"))
      and "stale" not in str(ef.settings.property("merged_folder")),
      f"{ef.settings.property('merged_folder')} | {mv.get('output_folder')}")
ef.useFolders(OUT, MASKS); pump(1.0)

# the chapter dropdown, as the stages have (user, 2026-10-14)
for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
    for ch in ("Chapter 1 - One", "Chapter 2 - Two"):
        (tmp / "Projects" / "Movie C" / f"MC {st}" / ch).mkdir(parents=True, exist_ok=True)
for ch in ("Chapter 1 - One", "Chapter 2 - Two"):
    (tmp / "Projects" / "Movie C" / "MC Splats" / ch / "hires").mkdir(parents=True, exist_ok=True)
PC = (tmp / "Projects" / "Movie C").as_posix()
shell.setProperty("projectFolder", PC); pump(1.0)
chs = ef.property("chapters"); chs = chs.toVariant() if hasattr(chs, "toVariant") else chs
num = {c["name"].split(" - ")[0]: c["num"] for c in chs or []}
check("Edge Fill's chapter dropdown shows the project's chapters", bool(shown("edgeFillChapter"))
      and set(num) >= {"Chapter 1", "Chapter 2"}, str([c["name"] for c in chs or []]))
looks = []
ef.scanned.connect(lambda r: looks.append(dict(r.toVariant() if hasattr(r, "toVariant") else r)))
ef.pickChapter(num["Chapter 1"]); pump(0.5)
mf, kf = str(ef.settings.property("merged_folder")), str(ef.settings.property("mask_folder"))
check("...the grid looks again by itself on a chapter pick (user, 2026-10-14)",
      until(lambda: any("Chapter 1 - One" in str(r.get("error", "")) + str(r.get("folder", "")) for r in looks), 10),
      str(looks[-1:]))
check("...a chapter points Merged at its merged clips and Mask at its splats",
      mf.replace("\\", "/").endswith("MC Merged/Chapter 1 - One") and "MC Splats/Chapter 1 - One" in kf.replace("\\", "/")
      and ef.property("chapterNum") == num["Chapter 1"], f"{mf} | {kf}")
check("...said once", any(t == "Chapter selected" for _k, t, _m in toasts[-2:]), str(toasts[-2:]))
ef.settings.setProperty("merged_folder", OUT); pump(0.3)
check("...a folder edited by hand: Manual paths", ef.property("chapterNum") == -1, ef.property("chapterNum"))
ef.pickChapter(num["Chapter 2"]); pump(0.3)
shell.setProperty("projectFolder", PROJ); pump(1.0)
shell.setProperty("projectFolder", PC); pump(1.0)
check("...kept per project: back in Movie C, chapter 2 again, quietly",
      ef.property("chapterNum") == num["Chapter 2"] and "Chapter 2 - Two" in str(ef.settings.property("merged_folder")),
      f"{ef.property('chapterNum')} {ef.settings.property('merged_folder')}")
mg.pickChapter(num["Chapter 1"]); pump(0.3)
mv = mg._values()
ef.useFolders(str(mv.get("output_folder") or ""), str(mv.get("mask_folder") or "")); pump(0.3)
check("Merging's Fill Edges on Merging's chapter: the same chapter here", ef.property("chapterNum") == num["Chapter 1"],
      ef.property("chapterNum"))
mg.pickChapter(-1); mg.settings.setProperty("output_folder", OUT); mg.settings.setProperty("mask_folder", MASKS)
ef.useFolders(OUT, MASKS); pump(1.0)
check("...and on Merging's own folders: Manual paths", ef.property("chapterNum") == -1, ef.property("chapterNum"))

# a project switch during a fill: the project's chapter is applied when the fill ends -- the picker
# showed the new chapter while the folders stayed the old project's (sweep 2026-10-14b)
ef.pickChapter(num["Chapter 1"]); pump(0.3)
shell.setProperty("projectFolder", PROJ); pump(1.0)
ef.useFolders(OUT, MASKS); pump(1.0)
n3 = len(toasts)
def unfill():                                     # the record cleared: the clips fill again
    try:
        os.remove(os.path.join(OUT, F.RECORD))
    except OSError:
        pass
unfill()
ef.fillClip(f"C:/x/take_{W}_inpainted_right_eye.mp4", OUT, MASKS)
until(lambda: ef.property("busy"), 10)
shell.setProperty("projectFolder", PC); pump(0.5)
until(lambda: not ef.property("busy"), 120); pump(1.0)
mf = str(ef.settings.property("merged_folder")).replace("\\", "/")
check("a project switch during a fill: after it, the chapter AND its folders (not the old project's)",
      ef.property("chapterNum") == num["Chapter 1"] and mf.endswith("MC Merged/Chapter 1 - One"), f"{ef.property('chapterNum')} {mf}")
check("...restored quietly (no 'busy' or 'Chapter selected' toasts)",
      not [t for _k, t, _m in toasts[n3:] if t in ("Edge Fill is busy", "Chapter selected")], str(toasts[n3:]))
# Merging's Fill Edges pressed during a fill: its folders are used after it, even in a project
# this page has folders remembered for (the deferred follow dropped them)
unfill()
ef.fillClip(f"C:/x/take_{W}_inpainted_right_eye.mp4", OUT, MASKS)
until(lambda: ef.property("busy"), 10)
ef.useFolders(OUT, MASKS); pump(0.2)
until(lambda: not ef.property("busy"), 120); pump(0.8)
check("Merging's Fill Edges pressed during a fill: its folders after it",
      os.path.normcase(str(ef.settings.property("merged_folder")).replace("/", "\\")) == os.path.normcase(OUT)
      and ef.property("chapterNum") == -1, f"{ef.settings.property('merged_folder')} {ef.property('chapterNum')}")

check("Merging no longer has Fill Edges' settings", all(mg.settings.property(k) is None for k in
      ("edgefill_steps", "edgefill_size", "edgefill_grain", "edgefill_tile")))

# quit during a fill: shutdown waits for the job -- its ROSE process killed, its work folder
# removed -- before Studio ends (the process outlived Studio holding its VRAM; sweep 2026-10-14)
import psutil
from unittest import mock
WAIT_WORKER = tmp / "waiting_worker.py"
WAIT_WORKER.write_text("import os, sys, time\n"
                       "open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))\n"
                       "print('CHUNK', 1, 1, flush=True)\n"
                       "time.sleep(120)\n")
E._run_worker = real_run_worker
until(lambda: page.property("phase") == "list", 20)
r_before = F._file_id(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov"))
with mock.patch.object(rose_env, "python", return_value=Path(sys.executable)), \
        mock.patch.object(rose_env, "WORKER", WAIT_WORKER):
    ef.fill([f"take_{W}_merged_full_sbs"])
    pidf = lambda: [x for x in OUT_DIR_FILES() if x.endswith(".pid")]
    def OUT_DIR_FILES():
        found = []
        for d in os.listdir(OUT):
            if d.startswith(".edgefill_"):
                try:
                    found += [os.path.join(OUT, d, x) for x in os.listdir(os.path.join(OUT, d))]
                except OSError:
                    pass
        return found
    until(lambda: bool(pidf()), 60)
    def read_pid():
        try:
            return int(open(pidf()[0]).read())
        except (IndexError, ValueError, OSError):
            return -1
    until(lambda: read_pid() > 0, 10)
    pid = read_pid()
    t0 = time.time()
    ef.shutdown()
    took = time.time() - t0
    thread = ef._thread
    alive = thread is not None and thread.is_alive()
    try:
        worker_alive = pid > 0 and psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        worker_alive = False
    left = [d for d in os.listdir(OUT) if d.startswith(".edgefill_")]
check("quit during a fill: shutdown waits for the job (its thread ended)", pid > 0 and not alive, f"pid {pid}, {took:.1f}s")
check("...the ROSE worker is gone", not worker_alive, str(pid))
check("...its work folder removed, the clip as it was", not left
      and F._file_id(os.path.join(OUT, f"take_{W}_merged_full_sbs_R.mov")) == r_before, str(left))
pump(1.0)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
