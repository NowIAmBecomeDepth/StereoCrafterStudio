"""Shared by the Splatting and Merging preview checks: the wheel ZOOMS the live
preview (never scrolls it), a drag pans a zoomed-in picture, and a double-click goes
back to the whole frame (or 50% closer from there) (user, 2026-09-27)."""
from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest


def run(check, pump, settle, win, page, st, walk):
    def named(n):
        items = [c for c in walk(page) if c.objectName() == n and c.isVisible()]
        return items[0] if items else None

    img = named("previewImage")
    flick = img.parentItem().parentItem().parentItem()          # Image -> shown -> contentItem -> Flickable

    def wheel(n, at):
        for _ in range(n):
            QCoreApplication.sendEvent(win, QWheelEvent(QPointF(at), QPointF(at), QPoint(0, 0), QPoint(0, 120),
                                                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
            pump(0.05)
        pump(0.4)
        settle()

    size = lambda: float(str(st.value("preview_size")).rstrip("%"))
    start = size()
    c = img.mapToScene(QPointF(img.width() / 2, img.height() / 2))
    QTest.mouseMove(win, QPoint(int(c.x()), int(c.y()))); pump(0.1)
    wheel(8, c)
    zoomed = size()
    check("the wheel zooms the preview in (about 12% a notch)", zoomed > start * 1.5, f"{start} -> {zoomed}")
    y0 = flick.property("contentY")
    c = img.mapToScene(QPointF(img.width() / 2, img.height() / 2))
    wheel(1, c)
    check("...zoomed in, the wheel still zooms -- it never scrolls the picture",
          size() > zoomed and flick.property("interactive") is True, f"{zoomed} -> {size()}, contentY {y0} -> {flick.property('contentY')}")
    # a drag pans
    # a drag pans (whichever way the picture is bigger than the view)
    x0, y0 = flick.property("contentX"), flick.property("contentY")
    inside = lambda fx, fy: img.mapToScene(QPointF(img.width() * fx, img.height() * fy))
    ctr = flick.mapToScene(QPointF(flick.width() / 2, flick.height() / 2))
    a = QPointF(ctr.x() + 60, ctr.y() + 60)
    b = QPointF(ctr.x() - 60, ctr.y() - 60)
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(a.x()), int(a.y())))
    for t in range(1, 13):
        QTest.mouseMove(win, QPoint(int(a.x() + (b.x() - a.x()) * t / 12), int(a.y() + (b.y() - a.y()) * t / 12))); pump(0.02)
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(b.x()), int(b.y()))); pump(0.8)
    moved = abs(flick.property("contentX") - x0) + abs(flick.property("contentY") - y0)
    check("...a drag moves the zoomed-in picture", moved > 20,
          f"contentX {x0:.0f} -> {flick.property('contentX'):.0f}, contentY {y0:.0f} -> {flick.property('contentY'):.0f}")
    # a double-click (in place of the Fit button, user 2026-09-27): zoomed in ->
    # the whole frame; at the whole frame -> 50% closer. Neither one flips.
    card = page.findChild(QObject, "flipCard")
    up0 = card.property("backUp")

    def dclick():
        p = flick.mapToScene(QPointF(flick.width() / 2, flick.height() / 2))
        at = QPoint(int(p.x()), int(p.y()))
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at); pump(0.03)
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at); pump(0.8); settle()

    check("...no Fit button any more", named("previewFitButton") is None)
    dclick()
    fit = size()
    check("...a double-click zoomed in: the whole frame back in view, no flip",
          img.width() <= flick.width() + 1 and img.height() <= flick.height() + 1 and card.property("backUp") == up0
          and not card.property("busy"),
          f"picture {img.width():.0f}x{img.height():.0f} in {flick.width():.0f}x{flick.height():.0f}, size {fit}")
    dclick()
    check("...a double-click at the whole frame zooms 50% closer, no flip",
          abs(size() - round(fit * 1.5)) <= 1 and card.property("backUp") == up0, f"{fit} -> {size()}")
    dclick()
    check("...and another goes back to the whole frame", size() == fit, f"{size()} vs {fit}")
