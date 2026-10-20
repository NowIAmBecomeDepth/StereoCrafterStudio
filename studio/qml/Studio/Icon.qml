import QtQuick
import Studio

// A Segoe Fluent Icons glyph. Glyphs are font outlines, so they stay sharp at
// any scale -- the animations below scale and rotate them freely.
//
// Centred by its ink (user, 2026-10-05: the ticks and i's in the round badges
// sat a little off): an icon font puts a glyph anywhere in its em box, so a
// Text centred by its box isn't centred by what shows. Shell.glyphOffset
// measures each glyph once, drawn large off screen; the nudge is that, at
// this size.
Text {
    id: icon
    property string glyph: ""
    property int size: 16
    text: glyph
    color: Theme.fg
    font.family: Theme.iconFont
    font.pixelSize: size
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    renderType: Text.QtRendering

    readonly property var _off: glyph !== "" && typeof Shell !== "undefined" && Shell.glyphOffset
                                ? Shell.glyphOffset(font.family, glyph) : [0, 0]
    // in whole screen pixels: a fraction of one is lost when the glyph is drawn
    readonly property real _dpr: Screen.devicePixelRatio || 1
    transform: Translate { x: Math.round(icon._off[0] * icon.size * icon._dpr) / icon._dpr
                           y: Math.round(icon._off[1] * icon.size * icon._dpr) / icon._dpr }
}
