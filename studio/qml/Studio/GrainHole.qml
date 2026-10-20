import QtQuick
import Studio

// Keeps its parent's area clean of the film grain (grain.py): put one inside
// a preview's picture. Main's grain layer cuts a hole wherever a visible one
// sits, clipped to `clipTo` (the preview's frame) so a zoomed-in picture never
// clears the grain past it.
Item {
    id: hole
    property Item clipTo: null
    anchors.fill: parent
    Component.onCompleted: Theme.grainHoles = Theme.grainHoles.concat([hole])
    Component.onDestruction: Theme.grainHoles = Theme.grainHoles.filter(h => h !== hole)
}
