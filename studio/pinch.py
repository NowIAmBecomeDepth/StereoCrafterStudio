"""A trackpad's pinch (user, 2026-10-31): over a preview that zooms, it zooms that preview;
anywhere else in Studio's window it steps the UI Scale, as Ctrl+= / Ctrl+- do.

How Windows delivers it: Qt 6's Windows plugin has no touchpad gestures (no DirectManipulation, no
QNativeGestureEvent -- those come on macOS and Wayland only), so a precision touchpad's pinch reaches a
Qt app the way Windows sends it to any program that doesn't ask for gestures: as the mouse wheel with
Ctrl held, in small steps (a few to some tens of the wheel's 120 a notch). A mouse's Ctrl + wheel is
the same event and does the same.

The previews take it themselves (a WheelHandler for Ctrl beside their wheel's one) and each marks its
area with an Item named "pinchZoomArea" whose `on` says whether it zooms now. This filter runs before
Qt Quick hands the event out: over such an area it lets the event go to the preview; anywhere else it
takes it, so a page's list or scroll area never scrolls under a pinch, and the UI Scale moves a step
for each notch's worth gathered (a pinch's small steps add up)."""
import time

from PySide6.QtCore import QEvent, QMetaObject, QObject, QPointF, Qt, Q_ARG
from PySide6.QtQuick import QQuickItem

#: the wheel's units for one UI Scale step (one notch of a mouse wheel)
NOTCH = 120
#: a pause this long between two of its events starts a new pinch (what was gathered is dropped).
#: Timed by the events' own timestamps: a UI Scale step relays the window out, and the events
#: queued meanwhile arrive late together -- by the clock they'd look like a new pinch.
GAP_MS = 600
#: one step of the UI Scale, in percent (Main.qml stepUiScale)
STEP_PCT = 5


class PinchRouter(QObject):
    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._acc = 0
        self._last = 0          # ms
        win.installEventFilter(self)

    def over_preview(self, pos: QPointF) -> bool:
        """A preview that zooms now is under `pos` (window coordinates), not covered by a popup."""
        overlay = self._win.property("overlay")
        if isinstance(overlay, QQuickItem):
            for c in overlay.childItems():
                if c.isVisible() and c.contains(c.mapFromScene(pos)):
                    return False
        for area in self._win.findChildren(QQuickItem, "pinchZoomArea"):
            if not area.isVisible() or not area.property("on"):
                continue
            if not area.contains(area.mapFromScene(pos)):
                continue
            # a part scrolled out of a clipping parent (a page's scroll area) isn't there
            p, shown = area.parentItem(), True
            while p is not None:
                if p.clip() and not p.contains(p.mapFromScene(pos)):
                    shown = False
                    break
                p = p.parentItem()
            if shown:
                return True
        return False

    def eventFilter(self, obj, ev):
        if obj is not self._win or ev.type() != QEvent.Type.Wheel:
            return False
        if ev.modifiers() != Qt.KeyboardModifier.ControlModifier:
            return False
        if self.over_preview(ev.position()):
            return False
        dy = ev.angleDelta().y()
        now = ev.timestamp() or int(time.monotonic() * 1000)
        # a new pinch, or the fingers turned back: start from nothing
        if abs(now - self._last) > GAP_MS or (dy * self._acc) < 0:
            self._acc = 0
        self._last = now
        self._acc += dy
        while abs(self._acc) >= NOTCH:
            sign = 1 if self._acc > 0 else -1
            self._acc -= sign * NOTCH
            QMetaObject.invokeMethod(self._win, "stepUiScale", Q_ARG("QVariant", sign * STEP_PCT))
        ev.accept()
        return True
