"""Glasses-Free 3D's render thread (studio/sr_display.py), with no SR runtime and no Direct3D:
the runtime and the surface stood in for (bug sweep 2026-10-14c).

* a probe that raises still answers (no display), so a saved Glasses-Free 3D side falls back;
* a failed Present (a lost device) closes the session to try again, not presented to for good.
* Keep Eye Tracking On (user, 2026-10-17): hidden, frozen or in another window, the session is held --
  the context never deleted (the camera stays on); a new window gets only a new weaver.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import sr_display as SR  # noqa: E402


class _Rt:
    def weave(self, *a):
        pass

    def weaver_input(self, *a):
        pass


class _Surf:
    hwnd, w, h, srv = 7, 4, 4, object()

    def __init__(self, hr):
        self.hr = hr

    def sync_size(self):
        return False

    def upload(self, arr):
        return False

    def bind(self):
        pass

    def present(self):
        return self.hr


def _renderer(answers):
    r = SR._Renderer("nowhere", answers.append)
    r._teardown_sr = lambda: None
    r._close_surface = lambda s: None
    return r


class ProbeTest(unittest.TestCase):
    def test_a_probe_that_raises_still_answers(self):
        answers = []
        r = _renderer(answers)

        def boom():
            raise OSError("the SR service went away")

        def step(*a):
            r._stop = True
        r._probe, r._step = boom, step
        with mock.patch.object(SR, "_Runtime", lambda folder: _Rt()):
            r.run()
        self.assertEqual(answers, [None])


class PresentTest(unittest.TestCase):
    def _present(self, hr):
        r = _renderer([])
        r._rt, r._weaver, r._mode, r._surface, r._shown_key = _Rt(), object(), "live", _Surf(hr), ("x",)
        with mock.patch.object(SR.ctypes, "windll", create=True):
            r._step("merging", 7, True, True, (0, 0, 1, 1), None)

    def test_a_lost_device_raises_to_close_the_session(self):
        with self.assertRaises(OSError):
            self._present(0x887A0005)                    # DXGI_ERROR_DEVICE_REMOVED

    def test_ok_and_occluded_present_quietly(self):
        self._present(0)
        with mock.patch.object(SR.time, "sleep"):
            self._present(SR._OCCLUDED)


class _CallRt:
    """The SR runtime, recording what was called."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a):
            self.calls.append(name)
            return 0xC0 if name in ("new_context", "create_hint") else None
        return call

    def count(self, name):
        return self.calls.count(name)


class _HoldSurf(_Surf):
    def __init__(self, hwnd):
        super().__init__(0)
        self.hwnd, self.dev, self.ctx, self.tex_size = hwnd, object(), object(), (0, 0)

    def close(self):
        pass


class HoldTest(unittest.TestCase):
    """The render thread's states with Keep Eye Tracking On (sr_display._Renderer._step)."""

    def setUp(self):
        self.r = SR._Renderer("nowhere", lambda info: None)
        self.rt = self.r._rt = _CallRt()
        for p in (mock.patch.object(SR, "_Surface", _HoldSurf),
                  mock.patch.object(SR, "_com", lambda *a: (lambda *b: None)),
                  mock.patch.object(SR.ctypes, "windll", create=True),
                  mock.patch.object(SR.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        SR.ctypes.windll.user32.IsWindow.return_value = True

    def step(self, hwnd, visible, live=True, hold=True):
        self.r._step("splatting", hwnd, visible, live, (0, 0, 1, 1), None, hold)

    def test_hidden_is_held_not_closed(self):
        self.step(7, True)
        self.assertEqual(self.r._mode, "live")
        self.step(7, False)                     # a clip change, the card turned over
        self.assertEqual(self.r._mode, "held")
        self.assertEqual(self.rt.count("delete_context"), 0)
        self.assertEqual(self.rt.count("weaver_del"), 0)
        self.step(7, True)                      # back: the same weaver, nothing made again
        self.assertEqual(self.r._mode, "live")
        self.assertEqual((self.rt.count("new_context"), self.rt.count("weaver_new")), (1, 1))

    def test_a_job_holds_instead_of_lens_only(self):
        self.step(7, True)
        self.step(7, True, live=False)          # Theme.calm: frozen
        self.assertEqual(self.r._mode, "held")
        self.assertEqual(self.rt.count("delete_context"), 0)

    def test_another_window_gets_a_new_weaver_on_the_kept_context(self):
        self.step(7, True)
        self.step(0, False)                     # the page's view let go (Full Preview opening)
        self.step(9, True)                      # Full Preview's window
        self.assertEqual(self.r._mode, "live")
        self.assertEqual(self.r._surface.hwnd, 9)
        self.assertEqual(self.rt.count("new_context"), 1)
        self.assertEqual(self.rt.count("delete_context"), 0)
        self.assertEqual((self.rt.count("weaver_del"), self.rt.count("weaver_new")), (1, 2))
        self.assertEqual(self.rt.count("init_context"), 2)     # after each weaver
        self.step(7, True)                      # and back to the page: no restart either
        self.assertEqual((self.rt.count("new_context"), self.rt.count("delete_context")), (1, 0))

    def test_a_held_windows_end_keeps_the_context(self):
        self.step(9, True)
        self.step(0, False)
        SR.ctypes.windll.user32.IsWindow.return_value = False     # Full Preview closed
        self.step(0, False)
        self.assertEqual(self.r._mode, "held")
        self.assertIsNone(self.r._weaver)
        self.assertEqual((self.rt.count("weaver_del"), self.rt.count("delete_context")), (1, 0))
        SR.ctypes.windll.user32.IsWindow.return_value = True
        self.step(7, True)
        self.assertEqual(self.r._mode, "live")
        self.assertEqual((self.rt.count("new_context"), self.rt.count("weaver_new")), (1, 2))

    def test_without_hold_it_closes_as_before(self):
        self.step(7, True, hold=False)
        self.step(7, True, live=False, hold=False)
        self.assertEqual(self.r._mode, "lens")              # frozen: lens only, camera off
        self.assertEqual(self.rt.count("delete_context"), 1)
        self.step(7, True, hold=False)
        self.step(9, True, hold=False)                      # another window: a new session
        self.assertEqual(self.rt.count("new_context"), 4)

    def test_hold_let_go_closes_after_the_grace(self):
        self.step(7, True)
        self.step(7, False)
        self.assertEqual(self.r._mode, "held")
        self.step(7, False, hold=False)                     # unticked, or no view wants it
        self.assertEqual(self.r._mode, "held")              # (the second's grace)
        self.r._hidden_since -= SR.GRACE_S + 0.1
        self.step(7, False, hold=False)
        self.assertEqual(self.r._mode, "off")
        self.assertEqual(self.rt.count("delete_context"), 1)

    def test_hold_never_starts_tracking_on_its_own(self):
        self.step(7, False)
        self.assertEqual(self.r._mode, "off")
        self.step(7, True, live=False)                      # first shown during a job: lens only
        self.assertEqual(self.r._mode, "lens")


class HubHoldTest(unittest.TestCase):
    """GlassesFree3D: held only with the box ticked and a view wanting the source."""

    def test_hold_follows_the_box_and_the_views(self):
        from PySide6.QtCore import Property, QObject, Signal

        class Shell(QObject):
            changed = Signal()
            keep = False
            glassesKeepTracking = Property(bool, lambda self: self.keep, notify=changed)

        shell = Shell()
        hub = SR.GlassesFree3D.__new__(SR.GlassesFree3D)
        QObject.__init__(hub)
        hub._shell, hub._wanting, hub._keep = shell, set(), False
        hub._renderer = mock.Mock()
        shell.changed.connect(hub._read_keep)
        held = lambda: hub._renderer.set_hold.call_args[0][0] if hub._renderer.set_hold.called else False
        hub.want("splatting#a", True)
        self.assertFalse(held())
        shell.keep = True
        shell.changed.emit()
        self.assertTrue(held())
        hub.want("splatting#a", False)
        self.assertFalse(held())
        hub.want("merging#b", True)
        self.assertTrue(held())
        shell.keep = False
        shell.changed.emit()
        self.assertFalse(held())


if __name__ == "__main__":
    unittest.main()
