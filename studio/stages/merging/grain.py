"""grain.py -- the Grain Match Preview window's logic, hand-ported.

v1 builds the window inside ``MergingGUI.open_grain_preview`` (merging_gui.py
~5727-6675) and keeps its logic in closures there, so it cannot be generated.
What it RUNS is generated (preview_engine.py): the clip lookup
(``_find_dual_eye_grain_source``), the one-frame blend (``_render_grain_frame``),
the sigma solver (``_grain_match_stats``), the coverage metric and the
sidecar write -- the same methods v1's batch auto-match calls. This file is the
closures, rule for rule; each method names the one it came from. While both
exist, a change to v1's popup must be made here too.

It runs on the Merging preview's worker thread (preview.py), like the rest of
the preview; the suggestion scan has its own thread and reader, as in v1.
The window's pictures go to QML as PIL images; everything else as ``state()``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, Optional

from .mask_side import mask_side_of

logger = logging.getLogger("merging.v2.grain")

#: v1 _do_render: the crop panes always display at about this many pixels.
CROP_DISP = 280
#: v1's crop zoom choices.
ZOOMS = ("1x", "1.5x", "2x")

NO_CLIP = ("No clip with a usable fill mask.\n\n"
           "Load a clip in the previewer (pick one in Upcoming Jobs), or — for dual-eye "
           "clips — make sure the Mask folder contains the matching "
           "_splatted2D grid for the inpainted file.")


#: Decoded frames kept per reader while the window is open: the frame on
#: screen, plus a few to step back to.
FRAME_CACHE = 4
#: The full view goes to QML at most this big (it is fitted to the window).
FULL_MAX = (1920, 1080)


class _Frames:
    """A reader that keeps its last few decoded frames. A slider move changes
    only the grain, never the frame, yet v1's _render_grain_frame decodes it
    again (twice: before and after) -- a random seek per reader per render,
    the slowest step on real footage."""

    def __init__(self, reader):
        self._reader = reader
        self._cache: Dict[int, Any] = {}

    def get_batch(self, indices):
        if len(indices) != 1:
            return self._reader.get_batch(indices)
        i = int(indices[0])
        hit = self._cache.pop(i, None)
        if hit is None:
            hit = _Decoded(self._reader.get_batch([i]).asnumpy())
            while len(self._cache) >= FRAME_CACHE:
                self._cache.pop(next(iter(self._cache)))
        self._cache[i] = hit                     # most recent last
        return hit

    def __len__(self):
        return len(self._reader)

    def __getattr__(self, name):
        return getattr(self._reader, name)


class _Decoded:
    def __init__(self, arr):
        self._arr = arr

    def asnumpy(self):
        return self._arr


def _device():
    """Live rendering runs on the GPU whenever there is one (user, 2026-09-26),
    whatever Merging's own GPU switch says -- that one is for the batch."""
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _on(res, device):
    """v1's _render_grain_frame hands its tensors back on the CPU; the
    measuring and the crop pick then ran there. Move them to `device`."""
    import torch
    return {k: (v.to(device) if isinstance(v, torch.Tensor) else v) for k, v in res.items()}


class GrainMatch:
    """One open Grain Match Preview window (v1 open_grain_preview's state)."""

    def __init__(self, eng, on_scan_done):
        self.eng = eng
        self._on_scan_done = on_scan_done     # called from the scan thread
        self.open = False

    # ---- open (v1: the top of open_grain_preview) ------------------------------

    def start(self, pick: Optional[int] = None) -> Dict[str, Any]:
        """Returns {} when open, {"refused": text}, or {"choose": [names]}
        when several dual-eye clips qualify (v1 _choose_from_list)."""
        from decord import VideoReader, cpu
        eng = self.eng
        pv = eng.previewer
        src = None
        if pv.video_list:
            idx = pv.current_video_index
            if 0 <= idx < len(pv.video_list):
                cand = pv.video_list[idx]
                if cand.get("inpainted") and cand.get("splatted"):
                    src = cand
        if src is None:
            eng.grain_pick, eng.grain_choices = pick, None
            try:
                src = eng._find_dual_eye_grain_source()
            finally:
                choices, eng.grain_pick, eng.grain_choices = eng.grain_choices, None, None
            if src is None and choices and pick is None:
                return {"choose": choices}
        if src is None:
            return {"refused": NO_CLIP}
        self.src = src
        self.inpainted_path = src.get("inpainted")
        self.splatted_path = src.get("splatted")
        self.is_dual_eye_sbs = bool(src.get("is_dual_eye_sbs", False))
        sidecar = src.get("sidecar", {}) or {}
        try:
            inp_reader = VideoReader(self.inpainted_path, ctx=cpu(0))
            splat_reader = VideoReader(self.splatted_path, ctx=cpu(0))
        except Exception as e:
            return {"alert": f"Could not open clip files:\n{e}"}
        orig_reader = None
        self.orig_path = src.get("original")
        if self.orig_path and os.path.exists(self.orig_path):
            try:
                orig_reader = VideoReader(self.orig_path, ctx=cpu(0))
            except Exception:
                orig_reader = None
        self.n_frames = min(len(inp_reader), len(splat_reader))
        if self.n_frames < 1:
            return {"alert": "Clip appears to have no frames."}
        # v1: a brightness saved under the old +/-0.25 range is clamped to the
        # slider's +/-0.05, or the thumb would pin while the render used it.
        try:
            b = float(eng.fill_brightness_var.get())
            if b < -0.05 or b > 0.05:
                logger.info(f"[grain] Brightness {b:+.3f} is outside the new +/-0.05 range; clamping.")
                eng.fill_brightness_var.set(max(-0.05, min(0.05, b)))
        except Exception:
            pass
        self.ctx = {
            "inp_reader": _Frames(inp_reader), "splat_reader": _Frames(splat_reader),
            "orig_reader": _Frames(orig_reader) if orig_reader is not None else None,
            "is_sbs_input": src.get("is_sbs_input", False), "is_quad_input": src.get("is_quad_input", False),
            "is_dual_eye_sbs": self.is_dual_eye_sbs, "mask_side": mask_side_of(src),
            "flip_horizontal": sidecar.get("flip_horizontal", False), "eye": "R",
        }
        self.eye = "R"
        self.crop_zoom = "1.5x"
        self.frame = 0
        self._user_moved = False
        self.crop_override = None
        self.crop_rect = None
        self.grain_gain = None
        self.sigma_master = self.sigma_fill = self.sigma_fill_raw = None
        # v1: seeded at the nominal 0.5 gain; the first render measures it.
        self.sigma_target = round(float(eng.grain_amount_var.get()) * 0.5, 4)
        self.sigma_text = "grain σ — master: …   fill: …"
        self.titles = self._titles()
        self.automatch_status = ""
        self.fill_status = ""
        self.suggestions = None          # None = scanning, [] = none found
        self.images: Dict[str, Any] = {}
        self._before = None          # (key, ungrained render, auto crop centre)
        self.open = True
        self._closed = threading.Event()
        threading.Thread(target=self._scan, args=(self.eye,), name="grain-scan", daemon=True).start()
        return {}

    def _titles(self):
        m_title = "Source (master)" if self.is_dual_eye_sbs else "Left Eye (master)"
        eye_word = "Eye" if self.is_dual_eye_sbs else "Right Eye"
        return [m_title, f"{eye_word} — blend", f"{eye_word} — blend + grain"]

    # ---- suggested frames (v1 _scan_suggestions / _poll_suggestions) ---------------

    def _scan(self, eye):
        """Own thread, own reader (decord has no locking), a snapshot of the eye."""
        top4 = []
        try:
            from decord import VideoReader, cpu
            scan_reader = VideoReader(self.splatted_path, ctx=cpu(0))
            stride = max(1, self.n_frames // 36)
            probes = list(range(0, self.n_frames, stride))[:36]
            scores = []
            for fi in probes:
                if self._closed.is_set():
                    return
                try:
                    f_np = scan_reader.get_batch([fi]).asnumpy()[0]
                    scores.append((self.eng._grain_mask_coverage(f_np, dict(self.ctx, eye=eye)), fi))
                except Exception:
                    continue
            scores.sort(reverse=True)
            top4 = scores[:4]
        except Exception as e:
            logger.warning(f"[grain] Frame suggestion scan failed: {e}")
        if not self._closed.is_set():
            self._on_scan_done(top4)

    def scan_done(self, top4) -> None:
        """v1 _poll_suggestions, on the worker: the buttons, and the best frame."""
        if not self.open:
            return
        self.suggestions = [{"frame": int(fi), "coverage": float(cov)} for cov, fi in top4]
        # not over a frame the user picked meanwhile (audit 2026-09-28, minor)
        if top4 and not getattr(self, "_user_moved", False):
            self.frame = int(top4[0][1])

    # ---- ops (QML) --------------------------------------------------------------------

    def seek(self, frame):
        self.frame = max(0, min(self.n_frames - 1, int(frame)))
        self._user_moved = True

    def set_zoom(self, zoom):
        if zoom in ZOOMS:
            self.crop_zoom = zoom

    def set_eye(self, eye):
        """v1 _pick_eye (dual-eye clips only)."""
        self.eye = "L" if eye in ("L", "Left") else "R"

    def click(self, x, y):
        """v1 _on_canvas_click: the crop centres here (source pixels)."""
        self.crop_override = (float(y), float(x))

    def auto_crop(self):
        """v1 _on_canvas_rclick: back to automatic fill-boundary targeting."""
        self.crop_override = None

    def sigma(self, value):
        """v1 _on_sigma_target: the sigma slider -> stored Amount, via the gain."""
        g = self.grain_gain or 0.5
        try:
            tgt = float(value)
        except (TypeError, ValueError):
            return
        self.sigma_target = tgt
        amt = max(0.0, min(0.12, tgt / max(float(g), 1e-4)))
        if abs(amt - float(self.eng.grain_amount_var.get())) > 1e-6:
            self.eng.grain_amount_var.set(round(amt, 5))

    def auto_match(self):
        """v1 _auto_match: the Amount whose fill sigma matches the master's."""
        m, raw, g = self.sigma_master, self.sigma_fill_raw, self.grain_gain
        if m is None or raw is None or g is None:
            self.automatch_status = "Render a frame first — nothing measured yet."
            return
        target_sq = m * m - raw * raw
        if target_sq <= 0:
            if raw > m * 1.10:
                self.sigma_target = 0.0
                self.eng.grain_amount_var.set(0.0)
                self.automatch_status = f"Fill σ {raw:.4f} already exceeds master {m:.4f} — Amount 0."
            else:
                self.automatch_status = f"Already matched within noise (master {m:.4f}, fill {raw:.4f})."
            return
        target = target_sq ** 0.5
        self.sigma(round(target, 4))
        amount = float(self.eng.grain_amount_var.get())
        self.automatch_status = f"Target added σ {target:.4f} → Amount {amount:.4f} (gain {float(g):.2f})"
        logger.info(f"[grain auto-match] master σ={m:.4f} fill_raw σ={raw:.4f} "
                    f"target_added={target:.4f} gain={float(g):.3f} -> Amount={amount:.4f}")

    def reset_fill(self):
        """v1 _reset_fill_defaults: the three fill corrections to APP_DEFAULTS, saved now."""
        d = self.eng.APP_DEFAULTS
        self.eng.fill_red_reduce_var.set(max(0.0, min(1.0, float(d["fill_red_reduce"]))))
        self.eng.fill_brightness_var.set(max(-0.05, min(0.05, float(d["fill_brightness"]))))
        self.eng.fill_contrast_var.set(max(0.5, min(1.5, float(d["fill_contrast"]))))
        if self.eng._save_grain_prefs():
            self.fill_status = (f"Reset to defaults and saved  —  red {d['fill_red_reduce']:.2f}, "
                                f"brightness {d['fill_brightness']:+.3f}, contrast {d['fill_contrast']:.3f}")
        else:
            self.fill_status = "Reset to defaults, but the config file could not be written."

    def save_sidecar(self):
        """v1 _save_sidecar. Returns (kind, text) for the window's message."""
        eng = self.eng
        keys = {
            "grain_enable": bool(eng.grain_enable_var.get()),
            "grain_amount": float(eng.grain_amount_var.get()),
            "grain_size": float(eng.grain_size_var.get()),
            "grain_luma_falloff": float(eng.grain_luma_falloff_var.get()),
            "grain_chroma": float(eng.grain_chroma_var.get()),
            "grain_seed": (eng.get_current_settings() or {}).get("grain_seed", -1),
            "fill_red_reduce": float(eng.fill_red_reduce_var.get()),
            "fill_brightness": float(eng.fill_brightness_var.get()),
            "fill_contrast": float(eng.fill_contrast_var.get()),
        }
        core = self.src.get("core_name")
        if not core:
            base = os.path.basename(self.inpainted_path)
            m = re.search(r"_inpainted_(dual_)?sbsF?\.(mp4|mov|mkv)$", base) or re.search(
                r"_inpainted_right_eyeF?\.(mp4|mov|mkv)$", base)
            core_w = base[: -len(m.group(0))] if m else os.path.splitext(base)[0]
            core = core_w[: core_w.rfind("_")] if "_" in core_w else core_w
        try:
            sc_path, resolved = eng._write_grain_sidecar(self.inpainted_path, core, self.orig_path, keys)
        except Exception as e:
            return "error", f"Failed to write sidecar:\n{e}"
        if resolved:
            return "info", f"Grain settings saved to:\n{sc_path}"
        return "warning", (f"Wrote {os.path.basename(sc_path)} at:\n{sc_path}\n\n"
                           "but the merge's sidecar reader did not resolve it. This usually "
                           "means the clip's core name differs from the file name — the grain "
                           "will still apply from the global sliders.")

    def release_files(self):
        """Before a batch: let go of the clip's three files, or a batch that reaches this
        clip can't move them into finished/ (Windows keeps open files where they are --
        bug sweep 2026-10-09, A8). The window stays open; its next render reopens them."""
        ctx = getattr(self, "ctx", None)
        if not ctx:
            return
        for key in ("inp_reader", "splat_reader", "orig_reader"):
            ctx[key] = None
        self._files_released = True
        import gc
        gc.collect()                                  # decord closes a file when its reader goes

    def _reopen_files(self):
        """After release_files: the readers again, from the same paths."""
        from decord import VideoReader, cpu
        self.ctx["inp_reader"] = _Frames(VideoReader(self.inpainted_path, ctx=cpu(0)))
        self.ctx["splat_reader"] = _Frames(VideoReader(self.splatted_path, ctx=cpu(0)))
        orig = None
        if self.orig_path and os.path.exists(self.orig_path):
            try:
                orig = _Frames(VideoReader(self.orig_path, ctx=cpu(0)))
            except Exception:
                orig = None
        self.ctx["orig_reader"] = orig
        self._files_released = False

    def close(self):
        """v1 _close: stop the scan, persist what was tuned."""
        self.open = False
        if hasattr(self, "_closed"):
            self._closed.set()
        self.eng._save_grain_prefs()
        self.ctx = None
        self._before = None          # frees its GPU tensors
        self.images = {}

    # ---- render (v1 _do_render) ---------------------------------------------------------

    def render(self) -> None:
        import torch
        import torch.nn.functional as F
        from PIL import Image
        from .preview_engine import _gauss_blur_nchw

        from .engine import GRAIN_PARAM_KEYS

        eng = self.eng
        fi = int(self.frame)
        params = eng.get_current_settings()
        if not params:
            return
        device = _device()
        params = dict(params, use_gpu=device == "cuda")
        if getattr(self, "_files_released", False):
            self._reopen_files()
        ctx = dict(self.ctx, eye=self.eye)
        res_after = eng._render_grain_frame(ctx, fi, params, grained=True)
        if not res_after:
            return
        res_after = _on(res_after, device)
        # The ungrained render and the auto crop do not depend on the grain /
        # fill settings: a grain slider reuses them.
        key = (fi, self.eye, tuple(sorted((k, repr(v)) for k, v in params.items() if k not in GRAIN_PARAM_KEYS)))
        if self._before is None or self._before[0] != key:
            res_before = eng._render_grain_frame(ctx, fi, params, grained=False)
            if not res_before:
                return
            res_before = _on(res_before, device)
            mblur = _gauss_blur_nchw(res_before["mask"].unsqueeze(0), 20.0)[0, 0]
            score = mblur * (1.0 - mblur)
            flat_idx = int(torch.argmax(score).item())
            self._before = (key, res_before, (flat_idx // score.shape[1], flat_idx % score.shape[1]))
        res_before = self._before[1]
        blended = res_after["blended"]

        hh, ww = blended.shape[1], blended.shape[2]
        if self.crop_override:
            cy, cx = self.crop_override
            cy = max(0, min(hh - 1, int(cy)))
            cx = max(0, min(ww - 1, int(cx)))
        else:
            cy, cx = self._before[2]
        cz = self._zoom()
        cs = max(80, int(round(CROP_DISP / cz)) // 2 * 2)
        y0 = max(0, min(hh - cs, cy - cs // 2))
        x0 = max(0, min(ww - cs, cx - cs // 2))
        crop_after = blended[:, y0: y0 + cs, x0: x0 + cs]
        crop_before = res_before["blended"][:, y0: y0 + cs, x0: x0 + cs]

        st = eng._grain_match_stats(
            res_before, res_after, grain_size=float(eng.grain_size_var.get()),
            luma_falloff=float(eng.grain_luma_falloff_var.get()), chroma=float(eng.grain_chroma_var.get()))
        master_warn = ""
        self.titles = self._titles()
        if res_after.get("master_fallback"):
            master_warn = "   ⚠ master = other eye (no source video) — target reads LOW"
            self.titles[0] = "Other eye (no source) ⚠"
        if self.is_dual_eye_sbs:
            ew = "Left Eye" if self.eye == "L" else "Right Eye"
            self.titles[1] = f"{ew} — blend"
            self.titles[2] = f"{ew} — blend + grain"
        if not st["ok"] and st["master_sigma"] is None:
            self.sigma_master = self.sigma_fill = self.sigma_fill_raw = self.grain_gain = None
            self.sigma_text = f"grain σ — {st['reason']}" + master_warn
        else:
            self.sigma_master = st["master_sigma"]
            self.sigma_fill = st["fill_sigma"]
            self.sigma_fill_raw = st["fill_sigma_raw"]
            self.grain_gain = st["gain"]
            cur_amt = float(eng.grain_amount_var.get())
            # v1: reflect the stored Amount back into the sigma slider (no trace).
            self.sigma_target = round(cur_amt * self.grain_gain, 4)
            delta = self.sigma_fill - self.sigma_master
            self.sigma_text = (
                f"grain σ — master: {self.sigma_master:.4f}   fill: {self.sigma_fill:.4f}"
                f"   (Δ {delta:+.4f})   │   ungrained fill: {self.sigma_fill_raw:.4f}"
                f"   Amount {cur_amt:.4f} × gain {self.grain_gain:.2f}" + master_warn)

        self.crop_rect = (x0, y0, cs)
        master_full = res_after["original_left"]
        if master_full is not None:
            if master_full.shape[1] != hh or master_full.shape[2] != ww:
                master_full = F.interpolate(master_full.unsqueeze(0), size=(hh, ww), mode="bilinear",
                                            align_corners=False)[0].clamp(0, 1)
            crop_master = master_full[:, y0: y0 + cs, x0: x0 + cs]
        else:
            crop_master = torch.full((3, cs, cs), 0.18, device=blended.device)

        def pil(t, zoom=1.0):
            arr = (t.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).astype("uint8")
            im = Image.fromarray(arr)
            if zoom > 1.001:
                # NEAREST so magnified grain stays pixel-honest (v1 _to_photo).
                im = im.resize((int(im.width * zoom), int(im.height * zoom)), Image.NEAREST)
            return im

        # The full view goes at source size; QML fits it to the window (v1
        # resized it to the holder with LANCZOS, and redrew on a resize).
        # At source size only up to FULL_MAX: QML fits it to the window anyway,
        # and a 4K frame to PIL and QImage every render is slow. The state
        # keeps the source size, which is what clicks map to.
        full = blended
        shrink = min(1.0, FULL_MAX[0] / ww, FULL_MAX[1] / hh)
        if shrink < 1.0:
            full = F.interpolate(blended.unsqueeze(0), size=(max(1, round(hh * shrink)), max(1, round(ww * shrink))),
                                 mode="area")[0]
        self.images = {"full": pil(full), "master": pil(crop_master, cz),
                       "before": pil(crop_before, cz), "after": pil(crop_after, cz)}
        self.image_size = (ww, hh)

    def _zoom(self) -> float:
        try:
            return float(self.crop_zoom.rstrip("x"))
        except ValueError:
            return 1.5

    # ---- what QML shows -----------------------------------------------------------------

    def state(self) -> Dict[str, Any]:
        eng = self.eng
        x0, y0, cs = self.crop_rect or (0, 0, 0)
        w, h = getattr(self, "image_size", (0, 0))
        return {
            "title": "Grain Match Preview — " + os.path.basename(self.inpainted_path or ""),
            "frame": int(self.frame), "frames": int(self.n_frames),
            "dualEye": self.is_dual_eye_sbs, "eye": self.eye, "cropZoom": self.crop_zoom,
            "cropRect": [x0, y0, cs], "imageWidth": w, "imageHeight": h,
            "titles": list(self.titles), "sigmaText": self.sigma_text,
            "sigmaTarget": float(self.sigma_target),
            "sigmaDefault": round(float(eng.APP_DEFAULTS["grain_amount"]) * 0.5, 4),
            "automatchStatus": self.automatch_status, "fillStatus": self.fill_status,
            "suggestions": self.suggestions if self.suggestions is not None else [],
            "scanning": self.suggestions is None,
        }
