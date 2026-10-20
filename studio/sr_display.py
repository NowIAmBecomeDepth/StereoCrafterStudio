"""sr_display.py -- Glasses-Free 3D: the Splatting / Merging preview woven for an SR display.

Leia's Simulated Reality (SR) platform drives the glasses-free 3D monitors
(Samsung's Odyssey 3D among them). With one connected, the previews' Front /
Back boxes offer "Glasses-Free 3D" (user, 2026-10-11): the picture area alone
goes 3D, at full resolution -- the full left eye and the full right eye, never
a half-width side-by-side -- while the rest of the window stays the 2D UI.

How
---
* The runtime, not the SDK: SR's installed DLLs export the calls SR Loom makes
  (its CreateLegacyWeaver path, SR-Loom src/SRWeaver.cpp), so ctypes reaches
  them with no headers and no new dependency. ``PredictingDX11Weaver`` samples
  one side-by-side texture (2W x H: each eye whole) into the bound target.
* A native child window sits over the visible part of the picture, with its
  own Direct3D 11 swap chain; only it is woven. It is click-through (its
  WM_NCHITTEST says HTTRANSPARENT, so the page under it gets the mouse), and
  popups, dialogs and toasts that reach over it get holes cut in it
  (SetWindowRgn) so they show on top. QML (GlassesFreeView.qml) says where.
* The engines render the view as their Side-by-Side and tag the frame
  (``info["sr3d"]``); the glue shows its left eye as the page's picture and
  hands the pair here (``offer_frame``).
* One thread does every SR and Direct3D call, so the UI never waits on the
  runtime or on a present.

The camera (measured 2026-10-11 against Windows' own camera-use record for
SREyeTracker.exe): it runs while a context that had a weaver exists --
destroying the weaver alone leaves it on; deleting the context turns it off --
and a context with only the lens hint leaves it off. So, by state:

* live (shown, no job running): context + weaver, re-woven every refresh to
  follow the viewer -- camera on;
* frozen (shown while a job runs -- nothing may redraw then): a lens-only
  context; the last woven frame stays on screen -- camera off;
* hidden: no context at all -- lens and camera off (after a second's grace, so
  turning the preview card over does not cycle them).

Keep Eye Tracking On (Shell.glassesKeepTracking; user, 2026-10-17: "so it doesn't
have to find me after every fullscreen or clip change"): while a Glasses-Free 3D
preview is wanted (a page or Full Preview showing that source), hidden and frozen
are "held" instead -- the context and weaver kept, nothing drawn, the camera on
(SR Loom's keep-alive does the same). A different window (Full Preview) gets a new
weaver on the same context, so the camera never stops. Nothing wanted any more,
the box unticked or Studio closing: closed as above.

The lens is the panel's, not the window's: on while the view shows.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PySide6.QtCore import (Property, QAbstractNativeEventFilter, QCoreApplication, QObject, QRect, QRectF,
                            Qt, QTimer, Signal, Slot)
from PySide6.QtGui import QGuiApplication, QSurface, QWindow

logger = logging.getLogger("studio.sr_display")

#: The Preview Source's name (both stages; the engines' generators use it too).
SOURCE = "Glasses-Free 3D"
#: Shell's Front / Back preferences per stage, and their defaults (shell.py)
SIDES = {"splatting": (("splatFlipFront", "Dubois Anaglyph"), ("splatFlipBack", "Splat Result")),
         "merging": (("mergeFlipFront", "Dubois Anaglyph"), ("mergeFlipBack", "Blended Image"))}
#: Hidden this long before the session goes (lens and camera off).
GRACE_S = 1.0

_vp = ctypes.c_void_p
_HUB: Optional["GlassesFree3D"] = None


def runtime_dir() -> Optional[str]:
    """The SR platform's bin folder, or None (no SR runtime: nothing to offer)."""
    if sys.platform != "win32":
        return None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.isfile(os.path.join(d, "SimulatedRealityCore.dll")):
            return d
    d = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "LeiaSR", "Platform", "bin")
    return d if os.path.isfile(os.path.join(d, "SimulatedRealityCore.dll")) else None


def offer_frame(stage: str, image) -> None:
    """A Glasses-Free 3D frame from a stage's preview worker: the side-by-side
    as an H x 2W x 3 uint8 array (kept, never written to), or a PIL image.
    Cheap when there is no SR display: nothing is converted."""
    hub = _HUB
    # (kept while the display is still being looked for: a saved choice's first frame)
    if hub is None or hub._renderer is None or image is None or (hub._probed and not hub._available):
        return
    try:
        arr = image if isinstance(image, np.ndarray) else np.asarray(
            image if image.mode == "RGB" else image.convert("RGB"))
    except Exception:
        logger.warning("Glasses-Free 3D: frame not readable", exc_info=True)
        return
    hub._renderer.set_pair(stage, arr)
    hub._sig_pair.emit(stage)


# ---- the SR runtime ----------------------------------------------------------------

def _fn(dll, name, res, *args):
    f = getattr(dll, name)
    f.restype = res
    f.argtypes = list(args)
    return f


class _Runtime:
    """The SR DLLs' exports this needs. C exports where the runtime has them
    (the context is its singleton: ``deleteSRContext`` clears it); the weaver
    is the C++ class, ``this`` first (x64)."""

    def __init__(self, folder: str):
        # Kept for the process: the runtime loads its weaving library later.
        self._dll_dir = os.add_dll_directory(folder)
        core = ctypes.WinDLL(os.path.join(folder, "SimulatedRealityCore.dll"))
        disp = ctypes.WinDLL(os.path.join(folder, "SimulatedRealityDisplays.dll"))
        dx = ctypes.WinDLL(os.path.join(folder, "SimulatedRealityDirectX.dll"))
        self.new_context = _fn(core, "newSRContextLensPreference", _vp, ctypes.c_bool)
        self.init_context = _fn(core, "initializeSRContext", None, _vp)
        self.delete_context = _fn(core, "deleteSRContext", None, _vp)
        self.create_screen = _fn(disp, "createScreen", _vp, _vp)
        self.res_w = _fn(disp, "getResolutionWidth", ctypes.c_int, _vp)
        self.res_h = _fn(disp, "getResolutionHeight", ctypes.c_int, _vp)
        self.size_w = _fn(disp, "getPhysicalSizeWidth", ctypes.c_float, _vp)     # cm
        self.size_h = _fn(disp, "getPhysicalSizeHeight", ctypes.c_float, _vp)
        self.create_hint = _fn(disp, "createSwitchableLensHint", _vp, _vp)
        self.lens_on = _fn(disp, "lensEnableHint", None, _vp)
        self.weaver_new = _fn(dx, "??0PredictingDX11Weaver@SR@@QEAA@AEAVSRContext@1@PEAUID3D11Device@@"
                                  "PEAUID3D11DeviceContext@@IIPEAUHWND__@@@Z",
                              _vp, _vp, _vp, _vp, _vp, ctypes.c_uint, ctypes.c_uint, _vp)
        self.weaver_del = _fn(dx, "??1PredictingDX11Weaver@SR@@QEAA@XZ", None, _vp)
        self.weaver_input = _fn(dx, "?setInputFrameBuffer@DX11WeaverBase@SR@@QEAAXPEAUID3D11ShaderResourceView@@@Z",
                                None, _vp, _vp)
        self.weave = _fn(dx, "?weave@DX11WeaverBase@SR@@QEAAXII@Z", None, _vp, ctypes.c_uint, ctypes.c_uint)
        self.weaver_srgb = _fn(dx, "?setShaderSRGBConversion@DX11WeaverBase@SR@@QEAAX_N0@Z",
                               None, _vp, ctypes.c_bool, ctypes.c_bool)
        self.weaver_late = _fn(dx, "?enableLateLatching@DX11WeaverBase@SR@@QEAAX_N@Z", None, _vp, ctypes.c_bool)


# ---- Direct3D 11 through COM vtables ---------------------------------------------------

def _com(obj, index, res, *argt):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(_vp))).contents
    return ctypes.WINFUNCTYPE(res, _vp, *argt)(vtable[index])


def _release(obj) -> None:
    if obj:
        _com(obj, 2, ctypes.c_ulong)(obj)


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls(u.fields[0], u.fields[1], u.fields[2])
        g.d[:] = u.bytes[8:]
        return g


class _MODE(ctypes.Structure):
    _fields_ = [("w", ctypes.c_uint), ("h", ctypes.c_uint), ("rate_n", ctypes.c_uint), ("rate_d", ctypes.c_uint),
                ("fmt", ctypes.c_uint), ("scanline", ctypes.c_uint), ("scaling", ctypes.c_uint)]


class _SWAP_DESC(ctypes.Structure):
    _fields_ = [("mode", _MODE), ("count", ctypes.c_uint), ("quality", ctypes.c_uint), ("usage", ctypes.c_uint),
                ("buffers", ctypes.c_uint), ("hwnd", _vp), ("windowed", ctypes.c_int), ("effect", ctypes.c_uint),
                ("flags", ctypes.c_uint)]


class _TEX_DESC(ctypes.Structure):
    _fields_ = [("w", ctypes.c_uint), ("h", ctypes.c_uint), ("mips", ctypes.c_uint), ("array", ctypes.c_uint),
                ("fmt", ctypes.c_uint), ("count", ctypes.c_uint), ("quality", ctypes.c_uint), ("usage", ctypes.c_uint),
                ("bind", ctypes.c_uint), ("cpu", ctypes.c_uint), ("misc", ctypes.c_uint)]


class _RTV_DESC(ctypes.Structure):
    _fields_ = [("fmt", ctypes.c_uint), ("dim", ctypes.c_uint), ("mip", ctypes.c_uint), ("pad", ctypes.c_uint * 2)]


class _VIEWPORT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("w", ctypes.c_float), ("h", ctypes.c_float),
                ("zmin", ctypes.c_float), ("zmax", ctypes.c_float)]


class _OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * 32), ("rect", wintypes.RECT), ("attached", wintypes.BOOL),
                ("rotation", ctypes.c_uint), ("monitor", _vp)]


_RGBA8 = 28
_RGBA8_SRGB = 29
_OCCLUDED = 0x087A0001
_IID_TEX2D = _GUID.of("6f15aaf2-d208-4e89-9ab4-489535d34f9c")
_IID_FACTORY1 = _GUID.of("770aae78-f26f-4dba-a829-253c83d1b387")


def _adapter_for(hwnd: int):
    """The DXGI adapter driving the monitor `hwnd` is on (two-GPU machines: the
    weave must run on the SR display's), or None for the default one."""
    try:
        monitor = ctypes.windll.user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
        factory = _vp()
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(_IID_FACTORY1), ctypes.byref(factory)) != 0:
            return None
        try:
            i = 0
            while True:
                adapter = _vp()
                if _com(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(_vp))(factory, i, ctypes.byref(adapter)) != 0:
                    return None
                j = 0
                while True:
                    output = _vp()
                    if _com(adapter, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(_vp))(adapter, j, ctypes.byref(output)) != 0:
                        break
                    desc = _OUTPUT_DESC()
                    _com(output, 7, ctypes.c_long, ctypes.POINTER(_OUTPUT_DESC))(output, ctypes.byref(desc))
                    _release(output)
                    if desc.monitor == monitor:
                        return adapter
                    j += 1
                _release(adapter)
                i += 1
        finally:
            _release(factory)
    except Exception:
        logger.warning("Glasses-Free 3D: adapter lookup failed (default adapter)", exc_info=True)
        return None


class _Surface:
    """The child window's device, swap chain and input texture. Render thread only."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.w = self.h = 0
        self.rtv = None
        self.tex = self.srv = None
        self.tex_size = (0, 0)
        self._rgba = None
        rect = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        self.w, self.h = max(1, rect.right), max(1, rect.bottom)
        desc = _SWAP_DESC()
        desc.mode.w, desc.mode.h, desc.mode.fmt = self.w, self.h, _RGBA8
        desc.count, desc.usage, desc.buffers = 1, 0x20, 2          # RENDER_TARGET_OUTPUT
        desc.hwnd, desc.windowed, desc.effect = hwnd, 1, 4         # FLIP_DISCARD
        adapter = _adapter_for(hwnd)
        self.sc, self.dev, self.ctx = _vp(), _vp(), _vp()
        hr = ctypes.windll.d3d11.D3D11CreateDeviceAndSwapChain(
            adapter, 0 if adapter else 1, None, 0, None, 0, 7, ctypes.byref(desc),
            ctypes.byref(self.sc), ctypes.byref(self.dev), None, ctypes.byref(self.ctx))
        _release(adapter)
        if hr != 0:
            raise OSError(f"D3D11CreateDeviceAndSwapChain failed: 0x{hr & 0xFFFFFFFF:08X}")
        self._make_rtv()

    def _make_rtv(self) -> None:
        bb = _vp()
        _com(self.sc, 9, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(_GUID), ctypes.POINTER(_vp))(
            self.sc, 0, _IID_TEX2D, ctypes.byref(bb))
        rtv = _vp()
        # an sRGB view of the UNORM buffer: the weave blends in linear light, as SR Loom's
        desc = _RTV_DESC(_RGBA8_SRGB, 4, 0)
        make = _com(self.dev, 9, ctypes.c_long, _vp, _vp, ctypes.POINTER(_vp))
        if make(self.dev, bb, ctypes.byref(desc), ctypes.byref(rtv)) != 0:
            make(self.dev, bb, None, ctypes.byref(rtv))
        _release(bb)
        self.rtv = rtv

    def sync_size(self) -> bool:
        """Follow the window's client size; True when it changed."""
        rect = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(wintypes.HWND(self.hwnd), ctypes.byref(rect))
        w, h = max(1, rect.right), max(1, rect.bottom)
        if (w, h) == (self.w, self.h):
            return False
        _com(self.ctx, 33, None, ctypes.c_uint, _vp, _vp)(self.ctx, 0, None, None)
        _release(self.rtv)
        self.rtv = None
        _com(self.ctx, 111, None)(self.ctx)                          # Flush
        hr = _com(self.sc, 13, ctypes.c_long, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                  ctypes.c_uint)(self.sc, 0, w, h, 0, 0)
        if hr != 0:
            # the buffers stayed at the old size: not drawn as if they'd changed (sweep 2026-10-14c)
            raise OSError(f"ResizeBuffers failed (0x{hr & 0xFFFFFFFF:08X})")
        self.w, self.h = w, h
        self._make_rtv()
        return True

    def upload(self, sbs_rgb: np.ndarray) -> bool:
        """The side-by-side (H x 2W x 3) into the input texture; True when the
        texture (and so its view) is new."""
        h, w2 = sbs_rgb.shape[:2]
        if self._rgba is None or self._rgba.shape[:2] != (h, w2):
            self._rgba = np.full((h, w2, 4), 255, np.uint8)
        try:
            # OpenCV's SIMD copy, the GIL let go: 1 ms for a 1080p pair where numpy's
            # strided fill took 14 (59 at 4K) on the thread that weaves
            import cv2
            cv2.cvtColor(np.ascontiguousarray(sbs_rgb), cv2.COLOR_RGB2RGBA, dst=self._rgba)
        except Exception:
            self._rgba[..., :3] = sbs_rgb
        fresh = False
        if self.tex_size != (w2, h):
            _release(self.srv)
            _release(self.tex)
            self.srv = self.tex = None
            desc = _TEX_DESC(w2, h, 1, 1, _RGBA8_SRGB, 1, 0, 0, 0x8, 0, 0)
            tex, srv = _vp(), _vp()
            if _com(self.dev, 5, ctypes.c_long, ctypes.POINTER(_TEX_DESC), _vp, ctypes.POINTER(_vp))(
                    self.dev, ctypes.byref(desc), None, ctypes.byref(tex)) != 0:
                raise OSError("CreateTexture2D failed")
            if _com(self.dev, 7, ctypes.c_long, _vp, _vp, ctypes.POINTER(_vp))(
                    self.dev, tex, None, ctypes.byref(srv)) != 0:
                _release(tex)                # no NULL view handed to the weaver (bug sweep 2026-10-14c)
                raise OSError("CreateShaderResourceView failed")
            self.tex, self.srv, self.tex_size = tex, srv, (w2, h)
            fresh = True
        _com(self.ctx, 48, None, _vp, ctypes.c_uint, _vp, _vp, ctypes.c_uint, ctypes.c_uint)(
            self.ctx, self.tex, 0, None, self._rgba.ctypes.data, w2 * 4, 0)
        return fresh

    def bind(self) -> None:
        rtvs = (_vp * 1)(self.rtv)
        _com(self.ctx, 33, None, ctypes.c_uint, ctypes.POINTER(_vp), _vp)(self.ctx, 1, rtvs, None)
        vp = _VIEWPORT(0, 0, self.w, self.h, 0, 1)
        _com(self.ctx, 44, None, ctypes.c_uint, ctypes.POINTER(_VIEWPORT))(self.ctx, 1, ctypes.byref(vp))
        black = (ctypes.c_float * 4)(0, 0, 0, 1)
        _com(self.ctx, 50, None, _vp, ctypes.POINTER(ctypes.c_float * 4))(self.ctx, self.rtv, ctypes.byref(black))

    def present(self) -> int:
        return _com(self.sc, 8, ctypes.c_long, ctypes.c_uint, ctypes.c_uint)(self.sc, 1, 0) & 0xFFFFFFFF

    def close(self) -> None:
        for name in ("srv", "tex", "rtv"):
            _release(getattr(self, name))
            setattr(self, name, None)
        if self.ctx:
            _com(self.ctx, 110, None)(self.ctx)                      # ClearState
        _release(self.sc)
        self.sc = None
        if self.ctx:
            _com(self.ctx, 111, None)(self.ctx)                      # Flush: D3D11 destroys lazily
        for name in ("ctx", "dev"):
            _release(getattr(self, name))
            setattr(self, name, None)


# ---- the render thread -------------------------------------------------------------------

class _Renderer(threading.Thread):
    """Every SR and Direct3D call, on one thread. The UI only sets wishes."""

    def __init__(self, folder: str, on_probed):
        super().__init__(name="glasses-free-3d", daemon=True)
        self._folder = folder
        self._on_probed = on_probed
        self._cv = threading.Condition()
        self._stop = False
        self._dirty = True
        self._probe_wanted = True
        # what the UI wants
        self._stage = ""
        self._hwnd = 0
        self._visible = False
        self._live = True
        self._hold = False                     # Keep Eye Tracking On, and a view wants it
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._pairs: Dict[str, Tuple[int, np.ndarray]] = {}
        self._serial = 0
        # what is
        self._rt: Optional[_Runtime] = None
        self._mode = "off"                     # off / live / lens / held (tracking kept, not drawn)
        self._ctx = None
        self._weaver = None
        self._weaver_mem = None
        self._surface: Optional[_Surface] = None
        # every woven window's surface, kept while its window lives: a new swap chain on a
        # window that had one is refused (E_ACCESSDENIED) until D3D11 gets round to destroying
        # the old -- seconds, at times (2026-10-11: Full Preview closed and reopened stayed 2D)
        self._surfaces: Dict[int, _Surface] = {}
        self._shown_key = None                 # (stage, pair serial, crop, texture view) last uploaded
        self._hidden_since: Optional[float] = None
        self._retry_at = 0.0

    # -- the UI's side (any thread) --
    def _poke(self, **kw) -> None:
        with self._cv:
            for k, v in kw.items():
                setattr(self, k, v)
            self._dirty = True             # (a wish made mid-present is not lost)
            self._cv.notify()

    def set_view(self, stage: str, hwnd: int, visible: bool, crop) -> None:
        self._poke(_stage=stage, _hwnd=int(hwnd), _visible=bool(visible), _crop=tuple(float(c) for c in crop))

    def set_live(self, live: bool) -> None:
        self._poke(_live=bool(live))

    def set_hold(self, hold: bool) -> None:
        self._poke(_hold=bool(hold))

    def set_pair(self, stage: str, arr: np.ndarray) -> None:
        with self._cv:
            self._serial += 1
            self._pairs[stage] = (self._serial, arr)
            self._dirty = True
            self._cv.notify()

    def drop_window(self) -> None:
        self._poke(_hwnd=0, _visible=False)

    def probe(self) -> None:
        self._poke(_probe_wanted=True)

    def stop(self) -> None:
        self._poke(_stop=True)

    # -- the thread --
    def run(self) -> None:
        try:
            self._rt = _Runtime(self._folder)
        except Exception:
            logger.warning("Glasses-Free 3D: the SR runtime could not be loaded", exc_info=True)
            self._on_probed(None)
            return
        while True:
            with self._cv:
                if not self._stop and not self._dirty and not self._drawing():
                    self._cv.wait(self._timeout())
                if self._stop:
                    break
                self._dirty = False
                stage, hwnd, visible, live, crop = self._stage, self._hwnd, self._visible, self._live, self._crop
                hold = self._hold
                pair = self._pairs.get(stage)
                probe = self._probe_wanted and self._mode == "off"
                if probe:
                    self._probe_wanted = False
            try:
                if probe:
                    try:
                        info = self._probe()
                    except Exception:
                        # a probe that raised left the display "unknown" for good: no fall-back,
                        # no retry (bug sweep 2026-10-14c) -- not offered until the screens change
                        logger.warning("Glasses-Free 3D: the SR display probe failed", exc_info=True)
                        info = None
                    self._on_probed(info)
                self._step(stage, hwnd, visible, live, crop, pair, hold)
            except Exception:
                logger.warning("Glasses-Free 3D: SR call failed; session closed", exc_info=True)
                self._teardown_sr()
                self._close_surface(self._surface)
                self._retry_at = time.monotonic() + 5.0
        self._teardown_sr()
        for surf in list(self._surfaces.values()):
            self._close_surface(surf)

    def _drawing(self) -> bool:
        return self._mode == "live" and self._visible and self._surface is not None and self._shown_key is not None

    def _timeout(self) -> Optional[float]:
        waits = []
        if self._hidden_since is not None and self._mode != "off":
            waits.append(max(0.0, GRACE_S - (time.monotonic() - self._hidden_since)))
        if self._retry_at:
            waits.append(max(0.0, self._retry_at - time.monotonic()))
        return min(waits) if waits else None

    def _probe(self):
        """(width, height, width cm, height cm) of the SR display, or None."""
        rt = self._rt
        ctx = rt.new_context(False)
        if not ctx:
            return None
        try:
            screen = rt.create_screen(ctx)
            if not screen:
                return None
            w, h = rt.res_w(screen), rt.res_h(screen)
            return (w, h, float(rt.size_w(screen)), float(rt.size_h(screen))) if w > 0 and h > 0 else None
        finally:
            rt.delete_context(ctx)

    def _step(self, stage, hwnd, visible, live, crop, pair, hold=False) -> None:
        now = time.monotonic()
        if self._retry_at and now < self._retry_at:
            return
        self._retry_at = 0.0
        # tracking that is kept: a session that has tracked, and Keep Eye Tracking On wanted
        keep = hold and self._mode in ("live", "held")
        if self._surface is not None and self._surface.hwnd != hwnd and not keep:
            self._teardown_sr()
            self._surface = None                 # (kept for its window's return)
        for h, surf in list(self._surfaces.items()):
            if h != hwnd and not ctypes.windll.user32.IsWindow(wintypes.HWND(h)):
                if surf is self._surface:
                    self._drop_weaver()          # held on a window now gone: the context stays
                self._close_surface(surf)        # its window is gone
        if visible and hwnd:
            self._hidden_since = None
            want = "live" if live else ("held" if keep else "lens")
        elif keep:
            self._hidden_since = None
            want = "held"
        else:
            if self._mode != "off" and self._hidden_since is None:
                self._hidden_since = now
            want = self._mode if (self._hidden_since is not None and now - self._hidden_since < GRACE_S) else "off"
        if want == "held" and self._mode == "live":
            self._mode = "held"                  # stop drawing; context, weaver and camera kept
            logger.info("Glasses-Free 3D: held (not drawn, eye tracking kept on)")
        elif want == "live" and (self._mode == "held" or (
                self._mode == "live" and (self._surface is None or self._surface.hwnd != hwnd))):
            self._resume_live(hwnd)              # the kept context: tracking never stopped
        elif want != self._mode:
            self._teardown_sr()
            if want == "live":
                self._start_live(hwnd)
            elif want == "lens":
                self._start_lens()
        if self._mode != "live" or not visible:
            return
        surf = self._surface
        surf.sync_size()
        if pair is not None:
            key = (stage, pair[0], crop)
            if key != self._shown_key or surf.srv is None:
                if surf.upload(_crop_pair(pair[1], crop)) or self._shown_key is None:
                    self._rt.weaver_input(self._weaver, surf.srv)
                self._shown_key = key
        if self._shown_key is None:
            return
        t0 = time.perf_counter()
        surf.bind()
        self._rt.weave(self._weaver, surf.w, surf.h)
        hr = surf.present()
        if hr & 0x80000000:
            # a lost device (a driver reset, the display unplugged): the session closed and tried
            # again, not presented to for good with the picture frozen (bug sweep 2026-10-14c)
            raise OSError(f"Present failed (0x{hr:08X})")
        if hr == _OCCLUDED:
            time.sleep(0.05)                     # hidden by Windows: no busy loop
        elif time.perf_counter() - t0 < 0.002:
            time.sleep(0.004)                    # a present that did not wait for the refresh

    def _start_live(self, hwnd: int) -> None:
        rt = self._rt
        if self._surface is None:
            self._surface = self._surfaces.get(hwnd) or _Surface(hwnd)
            self._surfaces[hwnd] = self._surface
        surf = self._surface
        ctx = rt.new_context(False)
        if not ctx:
            raise OSError("no SR context (is SR Service running?)")
        self._ctx = ctx
        self._new_weaver(hwnd)
        rt.lens_on(rt.create_hint(ctx))
        self._mode = "live"
        logger.info("Glasses-Free 3D: live (lens on, eye tracking on)")

    def _new_weaver(self, hwnd: int) -> None:
        """A weaver for `hwnd`'s surface on the context there is, and the context initialised
        after it (it registers its tracking first)."""
        rt, ctx, surf = self._rt, self._ctx, self._surface
        # (sizeof the class is not published: generously over-allocated)
        self._weaver_mem = ctypes.create_string_buffer(1 << 20)
        weaver = ctypes.cast(self._weaver_mem, _vp).value
        rt.weaver_new(weaver, ctx, surf.dev, surf.ctx, 2 * max(1, surf.w), max(1, surf.h), hwnd)
        self._weaver = weaver
        rt.weaver_srgb(weaver, False, False)     # sRGB views both ends do it
        rt.weaver_late(weaver, True)
        rt.init_context(ctx)                     # after the weaver: it registers its tracking first
        self._shown_key = None
        surf.tex_size = (0, 0)                   # the new weaver gets the input again

    def _resume_live(self, hwnd: int) -> None:
        """Held -> live (Keep Eye Tracking On): the same window draws again; another window
        gets a new weaver on the kept context -- the camera is not stopped."""
        if self._weaver is not None and self._surface is not None and self._surface.hwnd == hwnd:
            self._mode = "live"
            logger.info("Glasses-Free 3D: live again (eye tracking kept on)")
            return
        self._drop_weaver()
        self._surface = self._surfaces.get(hwnd) or _Surface(hwnd)
        self._surfaces[hwnd] = self._surface
        self._new_weaver(hwnd)
        self._mode = "live"
        logger.info("Glasses-Free 3D: live in another window (new weaver, eye tracking kept on)")

    def _start_lens(self) -> None:
        rt = self._rt
        ctx = rt.new_context(False)
        if not ctx:
            raise OSError("no SR context (is SR Service running?)")
        self._ctx = ctx
        rt.init_context(ctx)
        rt.lens_on(rt.create_hint(ctx))
        self._mode = "lens"
        logger.info("Glasses-Free 3D: frozen while a job runs (lens on, eye tracking off)")

    def _drop_weaver(self) -> None:
        """The weaver only: a context that had one keeps the camera on."""
        rt = self._rt
        self._shown_key = None
        if rt is not None and self._weaver is not None:
            try:
                if self._surface is not None:
                    _com(self._surface.ctx, 110, None)(self._surface.ctx)     # ClearState
                rt.weaver_del(self._weaver)
            except Exception:
                logger.warning("Glasses-Free 3D: weaver release failed", exc_info=True)
            self._weaver = None
            self._weaver_mem = None

    def _teardown_sr(self) -> None:
        rt, was = self._rt, self._mode
        self._mode = "off"
        self._shown_key = None
        if rt is None:
            return
        self._drop_weaver()
        if self._ctx is not None:
            try:
                rt.delete_context(self._ctx)     # the camera and the lens go with it
            except Exception:
                logger.warning("Glasses-Free 3D: context release failed", exc_info=True)
            self._ctx = None
            if was != "off":
                logger.info("Glasses-Free 3D: session closed (lens and eye tracking off)")

    def _close_surface(self, surf: Optional[_Surface]) -> None:
        if surf is None:
            return
        self._surfaces.pop(surf.hwnd, None)
        if surf is self._surface:
            self._surface = None
        try:
            surf.close()
        except Exception:
            logger.warning("Glasses-Free 3D: surface release failed", exc_info=True)


def _crop_pair(sbs: np.ndarray, crop) -> np.ndarray:
    """The visible part of each eye of a side-by-side, side by side again."""
    h, w2 = sbs.shape[:2]
    w = w2 // 2
    x0, y0, x1, y1 = crop
    l, r = int(round(x0 * w)), int(round(x1 * w))
    t, b = int(round(y0 * h)), int(round(y1 * h))
    l, t = max(0, min(l, w - 1)), max(0, min(t, h - 1))
    r, b = max(l + 1, min(r, w)), max(t + 1, min(b, h))
    if (l, t, r, b) == (0, 0, w, h):
        return sbs[:, : 2 * w]
    return np.concatenate([sbs[t:b, l:r], sbs[t:b, w + l:w + r]], axis=1)


# ---- the child window --------------------------------------------------------------------

class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class _HitThrough(QAbstractNativeEventFilter):
    """The woven window takes no mouse: HTTRANSPARENT hands it to the page."""

    def __init__(self):
        super().__init__()
        self.hwnds: set = set()           # every woven window's (a window re-shown gets a new one)

    def nativeEventFilter(self, eventType, message):
        try:
            msg = _MSG.from_address(int(message))
            if msg.message == 0x0084 and msg.hwnd in self.hwnds:     # WM_NCHITTEST
                return True, -1                                      # HTTRANSPARENT
        except Exception:
            pass
        return False, 0


def _same_window(a, b) -> bool:
    """The same window object (PySide's wrappers for one need not be one Python object).
    Never by winId(): on a closed window that makes a native window again -- under a parent
    that is gone, for a child -- and Qt then believes it has one (2026-10-11: Full Preview
    reopened stayed 2D, its woven window's handle dead)."""
    try:
        import shiboken6
        return (a is not None and b is not None
                and shiboken6.getCppPointer(a)[0] == shiboken6.getCppPointer(b)[0])
    except Exception:
        return False


def _native_ok(child, window) -> bool:
    """`child` has a live native window inside `window`'s current one (`window` is shown)."""
    if QGuiApplication.platformName() != "windows":
        return True                     # (offscreen: no Win32 windows to ask about)
    try:
        u32 = ctypes.windll.user32
        u32.GetParent.restype = wintypes.HWND
        h = int(child.winId())
        return bool(u32.IsWindow(wintypes.HWND(h))) and int(u32.GetParent(wintypes.HWND(h)) or 0) == int(window.winId())
    except Exception:
        return False


# ---- what QML sees -----------------------------------------------------------------------

class GlassesFree3D(QObject):
    """Context property ``Glasses3D``.

    ``available`` -- an SR display is connected (the option is offered only then).
    ``place(stage, window, rect, crop, holes)`` -- show the view: `rect` the
    visible picture in window coordinates, `crop` that part of the image as
    fractions, `holes` the popups over it (window rects).
    ``release(stage)`` -- the stage's view is gone.
    ``calm`` -- a job runs (Theme.calm): frozen, no redraws, no tracking.
    ``want(key, on)`` -- a view (page or Full Preview) wants Glasses-Free 3D or not: with
    Shell.glassesKeepTracking on, tracking is held while any does."""

    availableChanged = Signal()
    calmChanged = Signal()
    _sig_probed = Signal(object)
    _sig_pair = Signal(str)

    def __init__(self, shell=None, parent=None):
        super().__init__(parent)
        global _HUB
        _HUB = self
        self._shell = shell
        self._available = False
        self._probed = False
        self._info = None
        self._screen = None              # the SR display's QScreen name (a name: screens come and go)
        self._display_name = ""
        self._calm = False
        self._renderer: Optional[_Renderer] = None
        self._child: Optional[QWindow] = None
        self._filter: Optional[_HitThrough] = None
        self._children: Dict[int, QWindow] = {}     # a woven window per parent window
        self._hwnd = 0                                # the native handle last placed
        self._owner = ""
        self._shown = False
        self._geo: Optional[QRect] = None
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._holes: Optional[tuple] = None   # the holes cut in the shown window (None: not known)
        self._wanting: set = set()            # the views that want Glasses-Free 3D now
        self._keep = False                    # Keep Eye Tracking On (Shell.glassesKeepTracking)
        self._sig_probed.connect(self._on_probed)
        self._sig_pair.connect(self._on_pair)
        app = QCoreApplication.instance()
        folder = runtime_dir()
        # the offscreen checks never touch the SR service (STUDIO_SR_CHECKS=1 lets one)
        if (isinstance(app, QGuiApplication) and app.platformName() == "offscreen"
                and os.environ.get("STUDIO_SR_CHECKS") != "1"):
            folder = None
        if folder is None:
            self._probed = True
            QTimer.singleShot(0, self._fallback)
        else:
            self._renderer = _Renderer(folder, self._sig_probed.emit)
            # after start-up: loading the runtime is not the window's business
            QTimer.singleShot(1500, self._renderer.start)
            if isinstance(app, QGuiApplication):
                app.screenAdded.connect(self._screens_changed)
                app.screenRemoved.connect(self._screens_changed)
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        if shell is not None and hasattr(shell, "changed"):
            shell.changed.connect(self._read_keep)
        self._read_keep()

    # -- properties --
    available = Property(bool, lambda self: self._available, notify=availableChanged)
    sourceName = Property(str, lambda self: SOURCE, constant=True)
    #: the SR display's QScreen name and model ("Odyssey G90XF"), "" without one -- QML
    #: greys the option out on any other screen (user, 2026-10-11)
    screenName = Property(str, lambda self: self._screen or "", notify=availableChanged)
    displayName = Property(str, lambda self: self._display_name, notify=availableChanged)

    def _get_calm(self) -> bool:
        return self._calm

    def _set_calm(self, on: bool) -> None:
        on = bool(on)
        if on == self._calm:
            return
        self._calm = on
        if self._renderer is not None:
            self._renderer.set_live(not on)
        self.calmChanged.emit()

    calm = Property(bool, _get_calm, _set_calm, notify=calmChanged)

    # -- Keep Eye Tracking On --
    def _read_keep(self) -> None:
        try:
            keep = bool(self._shell.property("glassesKeepTracking")) if self._shell is not None else False
        except Exception:
            keep = False
        if keep != self._keep:
            self._keep = keep
            self._push_hold()

    @Slot(str, bool)
    def want(self, key: str, on: bool) -> None:
        (self._wanting.add if on else self._wanting.discard)(key)
        self._push_hold()

    def _push_hold(self) -> None:
        if self._renderer is not None:
            self._renderer.set_hold(self._keep and bool(self._wanting))

    # -- availability --
    def _on_probed(self, info) -> None:
        self._probed = True
        self._info = info
        if info:
            logger.info("Glasses-Free 3D: SR display %dx%d, %.1f x %.1f cm", *info)
        self._match()

    def _screens_changed(self, *_):
        self._match()
        if self._renderer is not None and self._renderer.is_alive():
            self._renderer.probe()

    def _match(self) -> None:
        screen = None
        if self._info:
            w, h, cw, ch = self._info
            for s in QGuiApplication.screens():
                dpr = s.devicePixelRatio() or 1.0
                g = s.geometry()
                if (round(g.width() * dpr), round(g.height() * dpr)) != (w, h):
                    continue
                mm = s.physicalSize()
                if mm.width() > 0 and abs(mm.width() - cw * 10) > 0.08 * cw * 10:
                    continue
                screen = s
                break
        was = (self._screen, self._display_name)
        self._screen = screen.name() if screen is not None else None
        self._display_name = (screen.model() or screen.name()) if screen is not None else ""
        on = screen is not None
        if on != self._available:
            self._available = on
            logger.info("Glasses-Free 3D: %s", f"offered (on {screen.name()})" if on else "not offered")
            if not on:
                self._hide()
            self.availableChanged.emit()
        elif was != (self._screen, self._display_name):
            self.availableChanged.emit()
        self._fallback()

    def _fallback(self) -> None:
        """Known to be unavailable: a Front / Back side holding it takes its default."""
        if self._available or not self._probed or self._shell is None:
            return
        for sides in SIDES.values():
            for prop, default in sides:
                try:
                    if str(self._shell.property(prop)) == SOURCE:
                        self._shell.setProperty(prop, default)
                except Exception:
                    logger.warning("Glasses-Free 3D: could not reset %s", prop, exc_info=True)

    # -- the view --
    def _ensure_child(self, window) -> QWindow:
        """The woven window in `window`: one per window (the page's, a Full Preview's),
        made once and kept -- never deleted: dropping one whose parent was a QML Window
        took that Window down with it (found 2026-10-11 with check_glasses_free)."""
        import shiboken6
        key = shiboken6.getCppPointer(window)[0]
        child = self._children.get(key)
        if child is not None and child is self._child:
            return child
        if self._child is not None:            # another window's: hidden, its surface let go
            self._child.hide()
            self._shown = False
            if self._renderer is not None:
                self._renderer.drop_window()
        if child is None:
            child = QWindow()
            child.setSurfaceType(QSurface.SurfaceType.Direct3DSurface)
            child.setFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
            child.setParent(window)
            child.create()
            self._children[key] = child
        if self._filter is None:
            self._filter = _HitThrough()
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        self._child = child
        # not known: this window may still have holes from when it was shown before (2026-10-11: a tip's
        # hole stayed in the page's after Full Preview closed -- a 2D patch in the picture)
        self._holes = None
        return child

    def _drop_child(self) -> None:
        """At shutdown: every woven window hidden (not deleted: see _ensure_child)."""
        if self._renderer is not None:
            self._renderer.drop_window()
        for child in self._children.values():
            try:
                child.hide()
            except RuntimeError:
                pass                           # its window already gone
        if self._filter is not None:
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None
        self._child = None
        self._shown = False

    @Slot(str, QObject, QRectF, "QVariantList", "QVariantList")
    def place(self, stage: str, window, rect: QRectF, crop, holes) -> None:
        if not self._available or window is None or not self._on_sr_screen(window):
            self.release(stage)
            return
        geo = QRect(round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height()))
        if geo.width() < 8 or geo.height() < 8:
            self.release(stage)
            return
        crop = tuple(float(c) for c in (crop or (0, 0, 1, 1)))
        child = self._ensure_child(window)
        if not _native_ok(child, window):
            # its native window went with its parent's (a Full Preview closed): made again
            child.hide()
            child.destroy()
            child.setParent(window)
            child.create()
            self._shown = False
            self._holes = None
        moved = (stage, geo, crop) != (self._owner, self._geo, self._crop)
        if self._calm and (moved or not self._shown):
            # frozen: nothing can be re-woven -- the page's own (2D) picture shows
            self._hide()
            return
        if moved or not self._shown:
            self._owner, self._geo, self._crop = stage, geo, crop
            child.setGeometry(geo)
            if not self._shown:
                child.show()
                self._shown = True
            hwnd = int(child.winId())
            self._hwnd = hwnd
            self._filter.hwnds.add(hwnd)           # click-through (a re-shown window: a new handle)
            if self._renderer is not None:
                self._renderer.set_view(stage, hwnd, True, crop)
        self._set_holes(window, holes or [])

    def _on_sr_screen(self, window) -> bool:
        s = window.screen()
        return s is not None and self._screen is not None and s.name() == self._screen

    @Slot(str)
    def release(self, stage: str) -> None:
        if stage == self._owner or not self._owner:
            self._hide()

    @Slot(str, QObject)
    def releaseFrom(self, stage: str, window) -> None:
        """`stage`'s view in `window` is gone: hidden only when it is that view
        that is shown (the page letting go must not hide its Full Preview's)."""
        if self._child is not None and window is not None and not _same_window(self._child.parent(), window):
            return
        self.release(stage)

    def _hide(self) -> None:
        if self._child is not None and self._shown:
            self._child.hide()
        self._shown = False
        self._geo = None
        if self._renderer is not None:
            # (the handle last placed: winId() on a closed window's child would make a dead one)
            self._renderer.set_view(self._owner, self._hwnd, False, self._crop)

    def _on_pair(self, stage: str) -> None:
        # a new picture while frozen can't be woven: show the page's 2D one
        if self._calm and self._shown and stage == self._owner:
            self._hide()

    def _set_holes(self, window, holes) -> None:
        dpr = window.devicePixelRatio() or 1.0
        g = self._geo
        local = []
        for h in holes:
            try:
                x, y, w, hh = (float(v) for v in (h["x"], h["y"], h["width"], h["height"])) if isinstance(h, dict) \
                    else (float(h.x()), float(h.y()), float(h.width()), float(h.height()))
            except Exception:
                continue
            r = (round((x - g.x()) * dpr), round((y - g.y()) * dpr), round((x - g.x() + w) * dpr),
                 round((y - g.y() + hh) * dpr))
            if r[2] > 0 and r[3] > 0 and r[0] < round(g.width() * dpr) and r[1] < round(g.height() * dpr):
                local.append(r)
        local = tuple(local)
        if local == self._holes:
            return
        self._holes = local
        hwnd = wintypes.HWND(int(self._child.winId()))
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        if not local:
            user32.SetWindowRgn(hwnd, None, True)
            return
        region = gdi32.CreateRectRgn(0, 0, round(g.width() * dpr) + 1, round(g.height() * dpr) + 1)
        for r in local:
            cut = gdi32.CreateRectRgn(*r)
            gdi32.CombineRgn(region, region, cut, 4)                 # RGN_DIFF
            gdi32.DeleteObject(cut)
        user32.SetWindowRgn(hwnd, region, True)                      # (Windows owns it now)

    @Slot()
    def shutdown(self) -> None:
        self._hide()
        if self._renderer is not None and self._renderer.is_alive():
            self._renderer.stop()
            self._renderer.join(3.0)
        self._drop_child()
