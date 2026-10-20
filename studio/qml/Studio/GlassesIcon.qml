import QtQuick
import QtQuick.Shapes
import Studio

// A pair of 3D glasses (user, 2026-09-28, from their reference picture): the
// frame, both arms, and the two lens outlines cut out of the frame (odd-even
// fill), so whatever is behind shows through the rims -- the palette, glass,
// anything. Drawn in the reference's own units (the frame is 1263 wide) and
// scaled to `width`.
Item {
    id: icon
    property color color: Theme.fg
    implicitWidth: 30
    implicitHeight: Math.round(implicitWidth * 640 / 1263)

    Shape {
        width: 1263
        height: 640
        scale: icon.width / 1263
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true

        ShapePath {
            fillColor: icon.color
            strokeColor: "transparent"
            fillRule: ShapePath.OddEvenFill

            // the frame, the nose cut out of its bottom edge
            startX: 0; startY: 295
            PathLine { x: 1263; y: 295 }
            PathLine { x: 1205; y: 612 }
            PathQuad { x: 1175; y: 640; controlX: 1200; controlY: 640 }
            PathLine { x: 735; y: 640 }
            PathQuad { x: 718; y: 612; controlX: 722; controlY: 640 }
            PathQuad { x: 631; y: 522; controlX: 700; controlY: 522 }
            PathQuad { x: 545; y: 612; controlX: 562; controlY: 522 }
            PathQuad { x: 528; y: 640; controlX: 541; controlY: 640 }
            PathLine { x: 88; y: 640 }
            PathQuad { x: 58; y: 612; controlX: 63; controlY: 640 }
            PathLine { x: 0; y: 295 }

            // the left arm
            PathMove { x: 8; y: 290 }
            PathLine { x: 300; y: 12 }
            PathQuad { x: 330; y: 12; controlX: 315; controlY: -2 }
            PathLine { x: 395; y: 78 }
            PathQuad { x: 385; y: 116; controlX: 418; controlY: 108 }
            PathQuad { x: 322; y: 108; controlX: 352; controlY: 90 }
            PathLine { x: 228; y: 290 }
            PathLine { x: 8; y: 290 }

            // the right arm (the left one mirrored)
            PathMove { x: 1255; y: 290 }
            PathLine { x: 963; y: 12 }
            PathQuad { x: 933; y: 12; controlX: 948; controlY: -2 }
            PathLine { x: 868; y: 78 }
            PathQuad { x: 878; y: 116; controlX: 845; controlY: 108 }
            PathQuad { x: 941; y: 108; controlX: 911; controlY: 90 }
            PathLine { x: 1035; y: 290 }
            PathLine { x: 1255; y: 290 }

            // the lens rims: an outer and an inner pill each -- with the
            // frame, odd-even leaves just the ring between them clear
            PathRectangle { x: 83; y: 347; width: 463; height: 233; radius: 116.5 }
            PathRectangle { x: 131; y: 395; width: 367; height: 137; radius: 68.5 }
            PathRectangle { x: 718; y: 347; width: 463; height: 233; radius: 116.5 }
            PathRectangle { x: 766; y: 395; width: 367; height: 137; radius: 68.5 }
        }
    }
}
