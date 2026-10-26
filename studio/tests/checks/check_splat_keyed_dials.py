"""Splatting's dials follow the keyframes (user, 2026-10-11: "the splat previewer doesn't
have live keyframe previews but the depth map editor does"), real app, offscreen, over a
TEMP copy of the small fixture. Configs and last_jobs.json redirected.

  * a static key at frame 5 (Disparity 30), a dynamic at 20 (60): between them the
    Disparity dial shows the ramped value the picture uses, on the keys their own --
    while the setting itself (the clip's base value) is left alone;
  * playing: the dial follows frame by frame, cached frames too;
  * turned on a keyframe: that keyframe takes the value (and the clip's sidecar), the
    frames between follow; turned off a keyframe: nothing changes, said once.
"""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp()); FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, Q_RETURN_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.resize(1700, 1000)
win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


sp = objs["Splatting"]; pv = sp.preview


def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.05)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and not pv._render_timer.isActive():
            pump(0.4)
            with pv._cv:
                if not pv._jobs:
                    return


def find(it, pred):
    for c in it.childItems():
        if pred(c):
            return c
        r = find(c, pred)
        if r is not None:
            return r


pump(0.6)
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("SplattingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual", "max_disp": 30,
             "auto_save_sidecar": True, "update_slider_from_sidecar": True, "auto_convergence_mode": "Off"}.items():
    sp._set(k, v)
pump(1.0)
pv.load(); settle()
check("preview loaded", pv.loaded)
dial = find(page, lambda c: c.property("key") == "max_disp" and c.metaObject().indexOfProperty("fillFrom") >= 0)
check("the Disparity dial is there", dial is not None)
shown = lambda: float(dial.property("value"))  # noqa: E731

check("no keyframes: the dial shows the setting", not pv.property("keyed") and abs(shown() - 30) < 1e-6, shown())
pv.seek(5); settle(); pv.addKeyframe("static"); settle()
sp._set("max_disp", 60); pump(0.3)
pv.seek(20); settle(); pv.addKeyframe("dynamic"); settle()
pv.seek(12); settle()
want = 30 + 30 * (12 - 5) / 15
check("between the keys: the dial shows the ramped value", abs(shown() - want) < 0.51, f"{shown():.2f} vs {want:.2f}")
check("...the value the picture uses there", abs(pv.property("keyed")["max_disp"] - want) < 1e-6)
check("...and the setting is left as it was", abs(float(sp.settings.value("max_disp")) - 60) < 1e-6,
      sp.settings.value("max_disp"))
check("...not on a key", not pv.property("onKey"))
pv.seek(5); settle()
check("on the first key: its own value", abs(shown() - 30) < 1e-6 and pv.property("onKey"), shown())

# playing: the dial follows, cached frames too (a second loop plays from the cache)
sp._set("loop_playback", True); pump(0.2)
pv.seek(0); settle()
seen = set()
pv.togglePlay()
end = time.time() + 25
while time.time() < end and len(seen) < 12:
    pump(0.02)
    seen.add(round(shown(), 1))
pv.pause(); settle()
check("playing: the dial follows the keyframes", len(seen) >= 6, f"{len(seen)} values: {sorted(seen)[:8]}...")

# turned on a keyframe: it takes the value
pv.seek(20); settle()
QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", "max_disp"), Q_ARG("QVariant", 50.0))
settle()
check("turned on a keyframe: the dial stays where it was turned", abs(shown() - 50) < 1e-6, shown())
kf = {k["frame"]: k for k in (json.loads(next((FIX / "depth").glob("*.fssidecar")).read_text(encoding="utf-8"))
                             .get("keyframe_track") or [])}
check("...that keyframe has it, in the sidecar too", abs(kf.get(20, {}).get("values", {}).get("max_disp_var", -1) - 50) < 1e-6,
      str(kf.get(20, {}).get("values", {}).get("max_disp_var")))
pv.seek(12); settle()
want = 30 + 20 * (12 - 5) / 15
check("...and the frames between follow it", abs(pv.property("keyed")["max_disp"] - want) < 1e-6 and abs(shown() - want) < 0.51,
      f"{shown():.2f} vs {want:.2f}")

# turned in one drag on a key (user, 2026-10-25: Pop Out Boost on a dynamic key "jumps back to the initial
# position and begins to slide towards where i want it very slowly"): every notch is the newest -- the
# dial never goes back to an older value, and is there when the hand stops
pv.seek(20); settle()
start, seen = shown(), []
for k in range(1, 31):
    QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", "max_disp"), Q_ARG("QVariant", start + 20.0 * k / 30))
    pump(0.016)
    seen.append(shown())
back = [(a, b) for a, b in zip(seen, seen[1:]) if b < a - 1e-6]
check("a drag on a keyframe: the dial never goes back to an older value", not back, f"{back[:4]} in {[round(v, 1) for v in seen]}")
pump(0.3)
check("...and it is where the hand stopped, at once", abs(shown() - (start + 20.0)) < 1e-6, shown())
settle()
kf = {k["frame"]: k for k in (json.loads(next((FIX / "depth").glob("*.fssidecar")).read_text(encoding="utf-8"))
                             .get("keyframe_track") or [])}
check("...the keyframe saved with the last value", abs(kf.get(20, {}).get("values", {}).get("max_disp_var", -1) - (start + 20.0)) < 1e-6
      and abs(shown() - (start + 20.0)) < 1e-6, str(kf.get(20, {}).get("values", {}).get("max_disp_var")))
QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", "max_disp"), Q_ARG("QVariant", start)); settle()
pv.seek(12); settle()                                  # between the keys again

# off a keyframe (user, 2026-10-25: "i cant change any settings on the keyframed clip now" -- turned off a
# key the dial went back, a key one frame off looked frozen): a DYNAMIC keyframe is added there, holding
# what the dials showed at that frame with the turn in it, as video editors do
before = dict(pv.property("keyed"))
QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", "max_disp"), Q_ARG("QVariant", 10.0)); settle()
k12 = next((k for k in pv.property("keyframes") if k["frame"] == 12), None)
check("turned off a keyframe: a dynamic keyframe added there, the dial where it was turned",
      k12 is not None and k12["kind"] == "dynamic" and pv.property("onKey") and abs(shown() - 10.0) < 1e-6,
      f"{[(k['frame'], k['kind']) for k in pv.property('keyframes')]} dial {shown()}")
after = pv.property("keyed")
check("...the rest as the dials showed them there", all(abs(after[k] - v) < 1e-6 for k, v in before.items() if k != "max_disp"),
      {k: (before[k], after[k]) for k in before if k != "max_disp" and abs(after[k] - before[k]) >= 1e-6})
kf = {k["frame"]: k for k in (json.loads(next((FIX / "depth").glob("*.fssidecar")).read_text(encoding="utf-8"))
                             .get("keyframe_track") or [])}
check("...and in the sidecar", kf.get(12, {}).get("type") == "dynamic"
      and abs(kf.get(12, {}).get("values", {}).get("max_disp_var", -1) - 10.0) < 1e-6, str(kf.get(12)))
pv.seek(12); settle(); pv.clearKeyframe(); settle()
check("...Clear Current takes it off again", all(k["frame"] != 12 for k in pv.property("keyframes")) and not pv.property("onKey"))

# bug sweep 2026-10-14c: the dial itself turned (not keyEdit) wrote the clip's base value as well
pv.seek(20); settle()
def _items(it):
    for c in it.childItems():
        yield c
        yield from _items(c)
knob = next(c for c in _items(dial) if "Dial" in c.metaObject().className() and c is not dial
            and c.metaObject().indexOfProperty("inputMode") >= 0)
QMetaObject.invokeMethod(knob, "forceActiveFocus"); pump(0.1)
knob.setProperty("value", 45.0); settle()
check("the keyed dial turned by hand: its keyframe takes it", abs(pv.property("keyed")["max_disp"] - 45) < 1e-6,
      str(pv.property("keyed").get("max_disp")))
check("...and the clip's own setting is left alone", abs(float(sp.settings.value("max_disp")) - 60) < 1e-6,
      sp.settings.value("max_disp"))

# the picture moves with a keyed dial in the anaglyph too (user, 2026-10-25: "the live preview still does
# not update when i move any dials" -- the preview read Disparity / Convergence / Gamma off the dials' base
# values, over this frame's keyframed ones)
import hashlib as _hl
def _pic():
    img = pv.provider._images.get("frame")
    return None if img is None else _hl.md5(bytes(img.constBits())[:img.sizeInBytes()]).hexdigest()
sp._set("preview_source", "Dubois Anaglyph"); settle()
pv.seek(20); settle()
for _key, _val in (("max_disp", 20.0), ("zero_disparity_anchor", 0.3), ("depth_gamma", 1.6)):
    _p0 = _pic()
    QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", _key), Q_ARG("QVariant", _val)); settle()
    check(f"Dubois Anaglyph, on a keyframe: turning {_key} redraws the picture with it",
          _p0 is not None and _pic() != _p0 and abs(pv.property("keyed")[_key] - _val) < 1e-6)
sp._set("preview_source", "Splat Result"); settle()

# EVERY keyframed setting stays live on a keyframe (user, 2026-10-25: "make sure any controls that keyframes
# can attach to stay live after keyframes exist on a clip" -- Pop-Out stayed frozen): turned on a key, the
# next redraw draws with it -- the settings handed to the picture, and the depth pre-processing's own --
# and that keyframe saves it.
eng = pv._engine
_seen = {"params": None, "depth": None, "draws": 0}
_cb = eng.previewer.processing_callback
def _spy_cb(frames, params, _cb=_cb):
    _seen["params"], _seen["draws"] = dict(params), _seen["draws"] + 1
    return _cb(frames, params)
eng.previewer.processing_callback = _spy_cb
_pdb = eng._process_depth_batch
def _spy_depth(**kw):
    _seen["depth"] = {k: v for k, v in kw.items() if k != "batch_depth_numpy_raw"}
    return _pdb(**kw)
eng._process_depth_batch = _spy_depth
_TRY = {"boost_amount": 0.3, "boost_thresh": 0.7, "boost_curve": 3.0, "boost_knee": 0.2, "boost_prefilter_radius": 4.0,
        "boost_prefilter_eps": 0.02, "depth_dilate_size_x": 3.0, "depth_dilate_size_y": 2.0, "depth_blur_size_x": 7.0,
        "depth_blur_size_y": 3.0, "depth_dilate_left": 2.0, "depth_blur_left": 3.0, "depth_blur_left_mix": 0.8,
        "depth_gamma": 1.3, "max_disp": 25.0, "pop_amount": 6.0, "zero_disparity_anchor": 0.55, "border_width": 2.0,
        "border_bias": 0.3, "frame_break_thresh": 0.6, "fb_erode_left": 2.0, "fb_erode_right": 3.0,
        "fb_erode_global": 1.0, "fb_edge_blur": 4.0}
_keyed = [v for vs in eng.KEYFRAME_VAR_GROUPS.values() for v in vs]
_into = {var: k for k, var in eng.KEYFRAME_SETTINGS_VAR_MAP.items()}
check("every keyframed setting has a value to try", sorted(v[:-4] for v in _keyed) == sorted(_TRY), str(_keyed))
pv.seek(20); settle()
_stale = []
for _var in _keyed:
    _key, _val = _var[:-4], _TRY.get(_var[:-4], 1.0)
    _n = _seen["draws"]
    QMetaObject.invokeMethod(page, "keyEdit", Q_ARG("QVariant", _key), Q_ARG("QVariant", _val)); settle()
    _par, _dep = _seen["params"] or {}, _seen["depth"] or {}
    if _key in ("border_width", "border_bias"):        # Manual: they become the two borders' widths
        w = _val if _key == "border_width" else _TRY["border_width"]
        b = _val if _key == "border_bias" else None
        ok = _seen["draws"] > _n and ("left_border_pct" in _par)
        if b is not None:
            want = (w * (1.0 - b), w) if b > 0 else (w, w * (1.0 + b))
            ok = ok and abs(_par["left_border_pct"] - want[0]) < 1e-6 and abs(_par["right_border_pct"] - want[1]) < 1e-6
    else:
        _sk = _into.get(_var, _key)
        ok = _seen["draws"] > _n and abs(float(_par.get(_sk, -999)) - _val) < 1e-6
        if _key in ("depth_blur_left_mix",):         # the pre-processing reads it too
            ok = ok and abs(float(_dep.get("depth_blur_left_mix", -999)) - _val) < 1e-6
    if not ok:
        _stale.append(f"{_key}: drew {_par.get(_into.get(_var, _key))} / depth {_dep.get(_key)}, wanted {_val}")
check("on a keyframe, every keyframed dial turned redraws the preview with it", not _stale, "; ".join(_stale))
_vals = dict((pv._kf_track.frame_at(20) or {}).get("values") or {})
check("...and the keyframe holds every one of them", all(abs(float(_vals.get(v, -999)) - _TRY[v[:-4]]) < 1e-6 for v in _keyed),
      str({v: _vals.get(v) for v in _keyed if abs(float(_vals.get(v, -999)) - _TRY[v[:-4]]) >= 1e-6}))
eng.previewer.processing_callback = _cb
del eng._process_depth_batch

# Full Preview's floating dials (user, 2026-10-14): Convergence, Disparity and Gamma under the
# frame slider -- the page's own dials, keyframes and all
from PySide6.QtGui import QWindow
fp = next(o for o in win.findChildren(QWindow) if o.metaObject().className().startswith("FullPreviewWindow")
          and o.property("stage") == "splatting")
QMetaObject.invokeMethod(fp, "openFull", Q_ARG("QVariant", win.screen())); pump(0.6)
bar = next(o for o in _items(fp.contentItem()) if o.objectName() == "fullPreviewDials")
scrub_bar = next(o for o in _items(fp.contentItem()) if o.objectName() == "fullPreviewScrub")
fdials = [o for o in _items(bar) if o.metaObject().indexOfProperty("fillFrom") >= 0]
check("Full Preview: Convergence, Disparity and Gamma float under the frame slider",
      [d.property("key") for d in fdials] == ["zero_disparity_anchor", "max_disp", "depth_gamma"]
      and bar.isVisible() and scrub_bar.isVisible()
      and scrub_bar.mapToScene(scrub_bar.boundingRect().bottomLeft()).y() <= bar.mapToScene(bar.boundingRect().topLeft()).y(),
      str([d.property("key") for d in fdials]))
pv.seek(12); settle()
check("...the keyed Disparity shows the keyframes' value there too",
      abs(float(fdials[1].property("value")) - pv.property("keyed")["max_disp"]) < 1e-6, fdials[1].property("value"))
pv.seek(20); settle()                    # on a keyframe: it takes the turn
fknob = next(c for c in _items(fdials[0]) if c.metaObject().indexOfProperty("inputMode") >= 0)
QMetaObject.invokeMethod(fknob, "forceActiveFocus"); pump(0.1)
fknob.setProperty("value", 0.62); settle()
check("...turning Convergence there on a keyframe sets it", abs(pv.property("keyed")["zero_disparity_anchor"] - 0.62) < 1e-6,
      pv.property("keyed").get("zero_disparity_anchor"))
from PySide6.QtTest import QTest
QTest.keyClick(fp, Qt.Key.Key_Escape); pump(0.4)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
