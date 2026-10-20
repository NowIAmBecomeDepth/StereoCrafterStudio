"""Settings in place of the menu bar (user, 2026-09-27), in the real app, offscreen:
no menu strip; a gear left of the VRAM pill opens Settings; Appearance has a
two-tone swatch per palette that switches the theme; the Studio, GPU and
stage sections are the old menus as rows (a checkable item a switch that
flips the setting, any other a button that closes Settings first); the
stage section follows the stage showing. Round sliders (DialField) draw a
grey track on dark palettes. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPointF, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def named(name, root=None):
    return [c for c in walk(root or win.contentItem(), []) if c.objectName() == name and c.isVisible()]

pump(0.8)
shell = objs["Shell"]
theme = engine.singletonInstance("Studio", "Theme")
stage = named("stage")[0]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.6)

check("no menu strip any more", not [c for c in walk(win.contentItem(), []) if c.objectName() == "menuStrip"])
gear = named("settingsButton")
pill = win.findChild(QObject, "vramPill")
# in window pixels (the page is zoomed): the gear's right edge and centre, the pill's left edge and centre
g = gear[0].mapToItem(win.contentItem(), QPointF(gear[0].width(), gear[0].height() / 2)) if gear else None
p = pill.mapToItem(win.contentItem(), QPointF(0, pill.height() / 2)) if pill is not None and pill.isVisible() else None
check("a gear at the top right, left of the VRAM pill", bool(gear) and (p is None or
      (g.x() <= p.x() and abs(g.y() - p.y()) < 2)), f"gear right/centre {g} pill left/centre {p}")

# DepthCrafter's page is the narrowest: fitted to it, the strip must still clear the gear
tabs = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("StageTabBar")][0]
for i, label in ((0, "DepthCrafter"), (3, "Merging")):
    show(i)
    QMetaObject.invokeMethod(win, "fitToContent"); pump(0.5)
    t_right = tabs.mapToItem(win.contentItem(), QPointF(tabs.width(), 0)).x()
    bsw = [c for c in walk(win.contentItem(), []) if c.objectName() == "beginnerSwitch"][0]
    g_left = bsw.mapToItem(win.contentItem(), QPointF(0, 0)).x()
    check(f"{label}, fitted ({win.width()} px): the stage tabs end before the Beginner switch", t_right <= g_left,
          f"tabs end {t_right:.0f}, switch starts {g_left:.0f}")
# Beginner Mode: a switch left of the gear, not a Settings row
bsw = [c for c in walk(win.contentItem(), []) if c.objectName() == "beginnerSwitch"][0]
b_right = bsw.mapToItem(win.contentItem(), QPointF(bsw.width(), bsw.height() / 2))
g_mid = gear[0].mapToItem(win.contentItem(), QPointF(0, gear[0].height() / 2))
check("Beginner switch just left of the gear, level with it", b_right.x() <= g_mid.x() and abs(b_right.y() - g_mid.y()) < 2,
      f"switch right/centre {b_right} gear left/centre {g_mid}")
B = objs["Beginner"]
tog = [c for c in walk(bsw, []) if c.objectName() == "beginnerToggle"][0]
tog.setProperty("checked", True); QMetaObject.invokeMethod(tog, "toggled"); pump(0.6)
on = B.property("on")
tog.setProperty("checked", False); QMetaObject.invokeMethod(tog, "toggled"); pump(0.6)
check("...it turns Beginner Mode on and off", on is True and B.property("on") is False and tog.property("checked") is False)
# Settings > Studio > Hide Beginner Mode Switch (2026-10-11): gone from the bar, back while the mode is on
S_ = objs["Shell"]
S_.setProperty("hideBeginnerSwitch", True); pump(0.3)
hidden = not bsw.isVisible()
B.setEnabled(True); pump(0.6)
back_while_on = bsw.isVisible()
B.setEnabled(False); pump(0.6)
hidden_again = not bsw.isVisible()
S_.setProperty("hideBeginnerSwitch", False); pump(0.3)
check("Hide Beginner Mode Switch hides it, except while the mode is on",
      hidden and back_while_on and hidden_again and bsw.isVisible(), f"{hidden} {back_while_on} {hidden_again}")
dlg = win.findChild(QObject, "settingsDialog")
show(2)
QMetaObject.invokeMethod(gear[0], "clicked"); pump(0.6)
check("the gear opens Settings", dlg.property("opened") is True)
nav = [c.property("modelData") for c in named("settingsNav")]
# the stage showing first, then Studio (user, 2026-10-10)
check("sections: the stage showing, Studio, Appearance, Keyboard Shortcuts, Tips, GPU", nav == ["Inpainting", "Studio", "Appearance", "Keyboard Shortcuts", "Tips", "GPU"], str(nav))

# the look moved to a header button left of Beginner Mode (user, 2026-09-27):
# theme, glass, UI scale, outlines -- no longer in Settings > Appearance
dlg.setProperty("current", 2); pump(0.3)        # Appearance
_app = [c for c in walk(win.contentItem(), []) if c.objectName() == "settingsAppearance"][0]
_in_app = {c.objectName() for c in walk(_app, [])}
check("Settings > Appearance: no themes, glass, scale or outlines any more",
      not ({"paletteSwatch", "glassSwitch", "glassOpacityKnob", "uiScaleKnob", "outlinesSwitch"} & _in_app)
      and "tipsSwitch" in _in_app, str(sorted(n for n in _in_app if n)))
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)

# a long page shows it scrolls (user, 2026-10-11): the bar stays faintly in sight, the
# bottom edge fades with a "More below" button, the top fades once scrolled
show(1)
_h_was = win.height()
win.setHeight(int(win.property("minimumHeight"))); pump(0.5)
QMetaObject.invokeMethod(dlg, "open"); pump(0.6)
dlg.setProperty("current", 0); pump(0.6)                     # Splatting: the longest page
_flick = [c for c in walk(win.contentItem(), []) if c.objectName() == "settingsMoreBelow"][0].parentItem()
_fl = [c for c in _flick.childItems() if c.metaObject().className().startswith("QQuickFlickable")][0]
_byname = lambda n: [c for c in walk(win.contentItem(), []) if c.objectName() == n][0]
_more = _byname("settingsMoreBelow")
_fade_b, _fade_t = _byname("settingsFadeBottom"), _byname("settingsFadeTop")
_bar = [c for c in walk(_fl, []) if c.objectName() == "settingsScrollBar"][0]
check("a long page: More below and the bottom fade show, the top fade doesn't",
      _fl.property("contentHeight") > _fl.height() and _more.isVisible() and _fade_b.isVisible() and not _fade_t.isVisible(),
      f"content {_fl.property('contentHeight'):.0f} in {_fl.height():.0f}")
_handle = _bar.property("contentItem")
check("...and the scroll bar stays in sight while still", _handle.property("opacity") > 0.3, _handle.property("opacity"))
_y0 = _fl.property("contentY")
_c = _more.mapToScene(_more.boundingRect().center())
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_c.x()), int(_c.y()))); pump(0.6)
check("More below scrolls a page down, and the top fades in", _fl.property("contentY") > _y0 + 50 and _fade_t.isVisible(),
      f"{_y0:.0f} -> {_fl.property('contentY'):.0f}")
_fl.setProperty("contentY", _fl.property("contentHeight") - _fl.height()); pump(0.5)
check("at the end: More below and the bottom fade go", not _more.isVisible() and not _fade_b.isVisible())
if len(sys.argv) > 1:
    _fl.setProperty("contentY", 0); pump(0.5)
    win.grabWindow().save(sys.argv[1])
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)
win.setHeight(_h_was); pump(0.4)
show(2)                                                     # back on Inpainting, as the checks below expect
tp = [c for c in walk(win.contentItem(), []) if c.objectName() == "themePicker"][0]
tb = [c for c in walk(tp, []) if c.objectName() == "themeButton"][0]
# between the bell and the VRAM pill (user, 2026-10-08; it was left of Beginner Mode)
t_l = tb.mapToItem(win.contentItem(), QPointF(0, tb.height() / 2))
bell = [c for c in walk(win.contentItem(), []) if c.objectName() == "notificationBell"][0]
b_r = bell.mapToItem(win.contentItem(), QPointF(bell.width(), bell.height() / 2))
check("a theme button just right of the bell, level with it", t_l.x() >= b_r.x() and abs(t_l.y() - b_r.y()) < 2,
      f"button left/centre {t_l} bell right/centre {b_r}")
QMetaObject.invokeMethod(tp, "open"); pump(0.7)
check("...it opens the palettes, glass, scale and outlines", tp.property("opened") is True and all(
      named(n) for n in ("paletteSwatch", "glassSwitch", "glassOpacityKnob", "uiScaleKnob", "outlinesSwitch")))
check("...counted as a dialog while open (the page ignores clicks)", theme.property("modalOpen") is True)
# a swatch per palette, the current one ringed; a click switches
sw = named("paletteSwatch")
pals = shell.property("palettes")
check("Appearance: a two-tone swatch per palette", len(sw) == len(pals), f"{len(sw)} / {len(pals)}")
names = [x["name"] for x in pals]
target = names.index("Nord") if "Nord" in names else 1
QMetaObject.invokeMethod([s for s in sw if s.property("paletteIndex") == target][0], "pick"); pump(0.3)
check("...a click switches the theme", shell.property("paletteIndex") == target, str(shell.property("paletteIndex")))
check("...the current palette is the one marked", [s.property("paletteIndex") for s in sw if s.property("current")] == [target])
def group(name):
    g = named(name)
    return [c.property("pal")["name"] for c in walk(g[0], []) if c.objectName() == "paletteSwatch"] if g else []
dark_names, light_names = group("darkPalettes"), group("lightPalettes")
check("...grouped: the dark palettes, then the light", dark_names and light_names
      and all(x["isDark"] for x in pals if x["name"] in dark_names)
      and not any(x["isDark"] for x in pals if x["name"] in light_names)
      and len(dark_names) + len(light_names) == len(pals), f"dark {dark_names} light {light_names}")
dr = [x for x in pals if x["name"] == "The Nile"]
check("...The Nile among the dark ones: a deep red ground, a white accent", "The Nile" in dark_names and bool(dr)
      and dr[0]["accent"].lower() == "#ffffff" and QColor(dr[0]["bg"]).red() > QColor(dr[0]["bg"]).green() * 2,
      str(dr[0] if dr else None))
# on a white accent a switched-on switch's knob takes the dark ink (it vanished, white on white)
shell.setProperty("paletteIndex", names.index("The Nile")); pump(1.2)
sws = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("Toggle") and c.isVisible()
       and c.property("checked")]
knobs = [k for t in sws for k in walk(t, []) if k.metaObject().className().startswith("QQuickRectangle")
         and k.property("width") >= 20 and k.property("height") == 20]
check("white accent: an on switch's knob is dark, not white on white", bool(knobs)
      and all(QColor(k.property("color")).lightness() < 80 for k in knobs),
      str([QColor(k.property("color")).name() for k in knobs][:3]))
shell.setProperty("paletteIndex", target); pump(1.2)
check("...the button shows the palette's name", any(c.property("text") == names[target] for c in walk(tb, [])),
      str([c.property("text") for c in walk(tb, []) if c.property("text")]))
QMetaObject.invokeMethod(tp, "close"); pump(0.5)
check("...closed: nothing counted any more", theme.property("modalOpen") is False)

# the stage's own menus, as rows
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", 0); pump(0.4)       # the stage: first
rows = named("settingsItem")
texts = [r.property("text") for r in rows]
check("Inpainting section: its File / Options / Help items", "Use SageAttention" in texts and "Encoding Settings" in texts
      and "Load Settings" in texts and "About" in texts, str(texts))
byname = {r.property("text"): r for r in rows}
ghosts = [g.property("text") for g in walk(byname["Reset to Default"], []) if g.objectName() == "settingsGhost" and g.isVisible()]
check("...an action with nothing to open is a ghost button with its own name (no 'Run')",
      byname["Reset to Default"].property("ghost") is True and ghosts == ["Reset to Default"]
      and byname["Load Settings"].property("ghost") is False, str(ghosts))
check("...with a tooltip where the name alone is not enough", "finished" in byname["Restore Finished"].property("tip")
      and byname["About"].property("tip") == "", byname["Restore Finished"].property("tip"))
ip_st = objs["Inpainting"].property("settings")
sage = [r for r in rows if r.property("text") == "Use SageAttention"][0]
before = bool(ip_st.value("use_sageattention")) if hasattr(ip_st, "value") else None
QMetaObject.invokeMethod(sage, "flipped", Q_ARG(bool, not before)); pump(0.3)
after = bool(ip_st.value("use_sageattention"))
check("...a checkable item is a switch that flips the setting", after == (not before) and sage.property("on") == after,
      f"{before} -> {after}, switch {sage.property('on')}")
QMetaObject.invokeMethod(sage, "flipped", Q_ARG(bool, before)); pump(0.2)
# the manual (user, 2026-10-04): a stage's section links to its own part of it,
# Studio's to the contents
_mb = named("settingsManualButton")
check("...and a link to Inpainting's section of the manual at the top", len(_mb) == 1
      and _mb[0].property("text") == "Open Inpainting in the Manual"
      and win.manualAt(2) == "part2.html#inpainting", [b.property("text") for b in _mb])
dlg.setProperty("current", 1); pump(0.3)        # Studio
_mb = named("settingsManualButton")
check("Studio's section links to the whole manual", len(_mb) == 1 and _mb[0].property("text") == "Open the Manual",
      [b.property("text") for b in _mb])
_x = [c for c in walk(dlg.property("contentItem"), []) if c.metaObject().className().startswith("IconButton") and c.isVisible()]
_bx = _mb[0].mapToScene(QPointF(_mb[0].width(), 0)).x() if _mb else 0
_xx = min((c.mapToScene(QPointF(0, 0)).x() for c in _x), default=1e9)
check("...its button clear of the close button (user, 2026-10-05: it sat right under the X)",
      _bx + 8 <= _xx, f"button ends {_bx:.0f}, close starts {_xx:.0f}")
dlg.setProperty("current", 4); pump(0.3)        # GPU
check("...GPU's has none", not named("settingsManualButton"))
check("every stage's link goes to a section that is in the manual",
      all(("id=\"" + win.manualAt(i).split("#")[1] + "\"") in (REPO / "docs" / "manual" / win.manualAt(i).split("#")[0]).read_text(encoding="utf-8")
          for i in range(4)), [win.manualAt(i) for i in range(5)])
dlg.setProperty("current", 0); pump(0.3)        # the stage
enc = [r for r in rows if r.property("text") == "Encoding Settings"][0]
QMetaObject.invokeMethod(enc, "run"); pump(0.6)
check("...any other a button that closes Settings, then acts", dlg.property("opened") is False)
enc_dlg = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("EncodingDialog")]
check("...(Encoding Settings opened)", any(d.property("visible") for d in enc_dlg) or any(
      o.property("opened") for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")))
for o in win.findChildren(QObject):
    if o.metaObject().className().startswith("EncodingDialog"):
        QMetaObject.invokeMethod(o, "close")
pump(0.3)

# follows the stage showing
show(3)
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
nav = [c.property("modelData") for c in named("settingsNav")]
check("on Merging the first section is Merging", nav[:1] == ["Merging"], str(nav))
dlg.setProperty("current", 1); pump(0.3)        # Studio
texts = [r.property("text") for r in named("settingsItem")]
check("Studio: Save All Settings, Open Console, Exit -- Beginner Mode moved out, Open Studio v1 gone (2026-09-28)",
      all(t in texts for t in ("Save All Settings", "Check for Updates", "Open Console", "Exit")) and "Use Beginner Mode" not in texts
      and "Open Studio v1" not in texts, str(texts))
# ghost buttons inside the pane's clip, their text in line with the labels
# (user, 2026-09-30: they were cut off on the left)
studio_rows = [r for r in named("settingsItem") if r.isVisible()]
flick = studio_rows[0].parentItem()
while flick is not None and not flick.metaObject().className().startswith("QQuickFlickable"):
    flick = flick.parentItem()
from PySide6.QtCore import QPointF
def fx(item, x=0.0):
    return item.mapToItem(flick, QPointF(x, 0)).x()
gh = [g for r in studio_rows for g in walk(r, []) if g.objectName() == "settingsGhost" and g.isVisible()]
byname_studio = {r.property("text"): r for r in studio_rows}
label = [c for c in walk(byname_studio["Set Project Folder"], [])
         if c.metaObject().className().startswith("QQuickText") and c.property("text") == "Set Project Folder"][0]
lefts = [round(fx(g) + (g.property("scale") - 1) * -g.width() / 2, 1) for g in gh]
def glyphs_x(g):   # the caption's first glyph: the Text inside, less its own left bearing (none: plain Text)
    t = [c for c in walk(g, []) if c.metaObject().className().startswith("QQuickText") and c.property("text")]
    return fx(t[0]) if t else None
text_x = [round(glyphs_x(g), 1) for g in gh]
check("Studio's ghost buttons sit wholly inside the pane (nothing cut off on the left)",
      len(gh) >= 3 and min(lefts) >= 0 and all(fx(g) - g.width() * 0.015 >= 0 for g in gh), f"{lefts}")
check("...and their text lines up with the labels", all(abs(t - fx(label)) <= 2 for t in text_x),
      f"text {text_x} label {round(fx(label), 1)}")
check("the window is titled StereoCrafter Studio, no v2", win.title() == "StereoCrafter Studio", win.title())
QMetaObject.invokeMethod(dlg, "close"); pump(0.3)

app_texts = [c.property("text") for c in walk(win.contentItem(), []) if isinstance(c.property("text"), str)]
check("no Custom Title Bar switch any more; the custom title bar is always used",
      "Custom Title Bar" not in app_texts and shell.property("customTitleBarAtStart") is True)
# UI Scale as a knob (user, 2026-09-27): 75-100 in 5s, applied once the turning stops
QMetaObject.invokeMethod(tp, "open"); pump(0.6)
sk = [c for c in walk(win.contentItem(), []) if c.objectName() == "uiScaleKnob"][0]
was_scale = shell.property("uiScale")
check("UI Scale is a knob, 75-100% in 5s", (sk.property("from"), sk.property("to"), sk.property("step")) == (75, 100, 5))
QMetaObject.invokeMethod(sk, "write", Q_ARG("QVariant", 85)); QMetaObject.invokeMethod(sk, "moved", Q_ARG("double", 85.0))
pump(0.1)
early = shell.property("uiScale")
pump(0.7)
check("...applied once it stops turning, not on every notch", early == was_scale and shell.property("uiScale") == "0.85",
      f"{was_scale} -> at once {early}, then {shell.property('uiScale')}")
QMetaObject.invokeMethod(sk, "reset"); pump(0.8)
check("...double-click / reset: Auto", shell.property("uiScale") == "auto", shell.property("uiScale"))
shell.setProperty("uiScale", was_scale); pump(0.4)
QMetaObject.invokeMethod(tp, "close"); pump(0.3)

# tips (user, 2026-09-27): every stage its own, and one switch in Settings
tickers = {}
for i, nm in enumerate(["DepthCrafter", "Splatting", "Inpainting", "Merging", "Extra"]):
    pg = stage.property("pages").property(i).toQObject()
    for c in walk(pg, []):
        if c.objectName() == "tipTicker":
            tickers.setdefault(nm, []).append(c)
def tip_list(t):
    v = t.property("tips")
    return list(v.toVariant()) if hasattr(v, "toVariant") else list(v)
own = {nm: set(tip_list(ts[0])) for nm, ts in tickers.items() if ts and tip_list(ts[0])}
check("tips on DepthCrafter, Splatting, Inpainting, Merging and Scene Cut", {"DepthCrafter", "Splatting", "Inpainting",
      "Merging", "Extra"} <= set(own), str(sorted(own)))
shared = [(a_, b_) for a_ in own for b_ in own if a_ < b_ and own[a_] & own[b_]]
check("...each stage its own: no tip shows on two stages", not shared, str(shared))
check("...Show Tips on by default", shell.property("showTips") is True)
dlg.setProperty("current", 2); QMetaObject.invokeMethod(dlg, "open"); pump(0.4)   # Appearance
tsw = [c for c in walk(win.contentItem(), []) if c.objectName() == "tipsSwitch"][0]
QMetaObject.invokeMethod(tsw, "flipped", Q_ARG(bool, False)); pump(0.3)
check("...switched off in Settings: no stage shows them", shell.property("showTips") is False
      and not any(t.property("visible") for ts in tickers.values() for t in ts))
import json as _json
check("...and it is remembered", _json.loads((tmp / "config" / "studio.json").read_text()).get("show_tips") is False)
QMetaObject.invokeMethod(tsw, "flipped", Q_ARG(bool, True)); pump(0.3)
QMetaObject.invokeMethod(dlg, "close"); pump(0.3)
check("...back on: they return", any(t.property("visible") for ts in tickers.values() for t in ts))

# round sliders: grey on dark palettes
names = [x["name"] for x in pals]
shell.setProperty("paletteIndex", names.index("True Black")); pump(1.2)
check("dark palette: the dial track is grey, not the near-black trough",
      QColor(theme.property("dialTrack")).lightness() > QColor(theme.property("trough")).lightness() + 25,
      f"{QColor(theme.property('dialTrack')).name(QColor.HexArgb)} vs {QColor(theme.property('trough')).name()}")
dials = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("Dial_") and c.isVisible()]
check("Merging's dials (DialField) are there to draw it", len(dials) >= 3, str(len(dials)))

# VRAM Status: a click on the VRAM pill, not a Settings row (user, 2026-09-27)
_texts = [str(c.property("text")) for c in walk(win.contentItem(), []) if c.property("text") is not None]
_gpu = [c for c in win.findChildren(QObject) if c.property("title") == "GPU"]
_items = [str(k.property("text")) for g in _gpu for k in g.findChildren(QObject) if k.property("text") is not None]
check("Settings > GPU no longer lists Show VRAM Status", "Show VRAM Status" not in _items and "Release GPU Memory" in _items, str(_items))
QMetaObject.invokeMethod(pill, "showStatus"); pump(0.6)
_dlg = [c for c in win.findChildren(QObject) if c.metaObject().className().startswith("PromptDialog") and c.property("visible")]
check("...a click on the pill shows the VRAM Status window", bool(_dlg) and "VRAM" in str(_dlg[0].property("title") or ""),
      str([d.property("title") for d in _dlg]))
for d in _dlg: d.setProperty("visible", False)
pump(0.4)

# audit 2026-09-28 (minor): Save All Settings saved DepthCrafter + Inpainting
# only; Release GPU Memory left Splatting's preview on the GPU.
def _menu_item(title, text):
    for g in win.findChildren(QObject):
        if g.property("title") == title:
            for k in g.findChildren(QObject):
                if k.property("text") == text and k.metaObject().indexOfSignal("triggered()") >= 0:
                    return k
_cfg = tmp / "config"
_before = sorted(f.name for f in _cfg.glob("*.json"))
for f in _cfg.glob("*.json"):
    f.unlink()
QMetaObject.invokeMethod(_menu_item("Studio", "Save All Settings"), "triggered"); pump(0.6)
_after = sorted(f.name for f in _cfg.glob("*.json"))
_owners = {"depthcrafter", "splatting", "inpainting", "inpainting_v2", "merging", "scenecut", "studio"}
check("Save All Settings writes every stage's settings", _owners <= {n[:-5] for n in _after},
      f"before {_before}, written {_after}")
_sp = objs["Splatting"]
_sp_released = []
_sp.preview.release_for_job = lambda then, _o=_sp.preview.release_for_job: (_sp_released.append(1), _o(then))
QMetaObject.invokeMethod(_menu_item("GPU", "Release GPU Memory"), "triggered"); pump(0.6)
check("Release GPU Memory lets go of Splatting's preview too", bool(_sp_released))
# 3D glasses in the title bar, right of the name (user, 2026-09-28). The bar is
# the custom caption, off offscreen: shown here just to measure it.
from PySide6.QtCore import QPointF
_tb = [c for c in walk(win.contentItem(), []) if c.objectName() == "titleBar"][0]
_tb.setProperty("visible", True); _tb.setProperty("height", 32); pump(0.3)
_g = named("titleGlasses")
_name = [c for c in walk(_tb, []) if c.metaObject().className().startswith("QQuickText") and c.property("text") == win.title()]
if _g and _name:
    _gx = _g[0].mapToItem(_tb, QPointF(0, 0)); _nx = _name[0].mapToItem(_tb, QPointF(_name[0].width(), 0))
check("3D glasses in the title bar, just right of the name, inside the bar, in the caption's colour",
      bool(_g) and bool(_name) and 0 <= _gx.x() - _nx.x() <= 16 and _gx.y() >= 0
      and _gx.y() + _g[0].height() <= 32 and _g[0].property("color") == theme.property("fg"),
      f"{len(_g)} icon(s), {len(_name)} name(s)")
_tb.setProperty("visible", False)
# bug sweep 2026-10-14c: Ctrl+, opened on whatever section was shown last, not the stage
from PySide6.QtTest import QTest as _QT
dlg.setProperty("current", 4); QMetaObject.invokeMethod(dlg, "open"); pump(0.4)
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)
win.requestActivate(); pump(0.2)
_QT.keyClick(win, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier); pump(0.5)
check("Ctrl+, opens Settings on the stage again, not the last section", dlg.property("opened")
      and dlg.property("current") == 0, f"{dlg.property('opened')} / {dlg.property('current')}")
QMetaObject.invokeMethod(dlg, "close"); pump(0.3)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
