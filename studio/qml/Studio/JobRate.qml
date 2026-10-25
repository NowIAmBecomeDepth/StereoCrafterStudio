import QtQuick
import Studio

// Beside the chunk ring: the last diffusion step's time, large, and its unit
// under it (sec/it; it/sec when a step takes under a second), then the iteration counter. Changes once per step.
Column {
    id: rate
    property string stage: ""
    property string owner: ""         // which of the stage's pages (JobStats.owner): "" = any
    readonly property bool mine: typeof JobStats !== "undefined" && JobStats.stage === stage
        && (owner === "" || JobStats.owner === owner)
    readonly property real value: mine ? JobStats.rate : 0
    spacing: -2

    Text {
        objectName: "jobRateText"
        text: rate.value > 0 ? (rate.value >= 1 ? rate.value.toFixed(2) : (1 / rate.value).toFixed(2)) : "—"
        color: rate.value > 0 ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: 18
        font.weight: Font.DemiBold
        font.features: { "tnum": 1 }
    }
    Text {
        objectName: "jobRateUnit"
        text: rate.value > 0 && rate.value < 1 ? "it/sec" : "sec/it"
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: 10
    }
    // the iteration counter: the step bar's own n / m
    Text {
        objectName: "jobIterText"
        topPadding: 4
        text: rate.mine && JobStats.steps > 0 ? "Inference Step " + JobStats.step + " / " + JobStats.steps : "Inference Step —"
        color: rate.mine && JobStats.steps > 0 ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: 11
        font.features: { "tnum": 1 }
    }
    // Inpainting: the frames the running chunk covers (JobStats.rangeFrom/To)
    Text {
        objectName: "jobRangeText"
        visible: rate.mine && JobStats.rangeTo > 0
        text: visible ? "Frames " + JobStats.rangeFrom + "–" + JobStats.rangeTo : ""
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: 11
        font.features: { "tnum": 1 }
    }
}
