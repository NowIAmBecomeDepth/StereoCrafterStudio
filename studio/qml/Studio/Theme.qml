pragma Singleton
import QtQuick

// One palette and one type system for the whole window.
//
// Palettes are Studio's own (core/ui/theme_manager.THEMES, through Shell),
// and every colour eases to the next palette instead of snapping. Type
// follows v1's core/ui/app_fonts.py role for role: Bahnschrift SemiBold caps
// for headings, Poppins for body (v1: Oxanium; the user's pick for v2,
// 2026-09-26), Cal Sans caps for buttons, Bahnschrift caps
// for tabs.
Item {
    id: theme

    readonly property bool hasShell: typeof Shell !== "undefined" && Shell !== null
    readonly property var palettes: hasShell ? Shell.palettes : [fallback]
    readonly property var fallback: ({
        name: "Graphite", isDark: true, bg: "#2b2b2b", fg: "#ffffff", surface: "#3d3e40",
        field: "#3c3c3c", trough: "#28292b", accent: "#6ea8fe", accentInk: "#6ea8fe", accentFg: "#101010",
        outline: "#6ea8fe"
    })
    property int index: hasShell ? Shell.paletteIndex : 0
    function setPalette(i) { if (hasShell) Shell.paletteIndex = i; else index = i }
    readonly property var p: palettes[Math.max(0, Math.min(index, palettes.length - 1))]
    readonly property string name: p.name
    readonly property bool isDark: p.isDark

    // ---- motion -----------------------------------------------------------
    // Every animation's duration goes through here. Normal speed only: the
    // View > Motion menu (slow x5 / off) is gone (user, 2026-09-26).
    // While a job runs (calm) every transition is instant: a progress update
    // is then one redraw, not the ~20 a second its easing drew (the rolling
    // numbers, the sliding status line, the bar easing on). With the card full
    // each redraw makes Windows swap the window's video memory against the
    // job's: DepthCrafter ran 28% slower with the window up than minimised
    // (2026-09-27).
    function dur(ms) { return calm ? 0 : ms }
    // True while any stage runs a job (Main binds it). Qt Quick draws on the
    // GPU the job runs on, and Windows time-slices drawing against CUDA: the
    // busy indicators' continuous animation (a redraw every monitor refresh)
    // cost Inpainting 8% (2.82 -> 3.06 s/it, measured; v1's Tk draws on the
    // CPU). Calm, they tick a few times a second instead: 2.85 with none.
    property bool calm: false
    // The job running is Splatting's or Merging's (Main binds it): not heavy on
    // the GPU, so what the user moves by hand -- the clip lists' scroll glide --
    // may still animate (user, 2026-10-05). Everything else stays on dur().
    property bool lightJob: false
    function durLight(ms) { return calm && !lightJob ? 0 : ms }
    // Modal dialogs open (Settings, prompts, Encoding, text pages). Pointer
    // handlers on the page under them -- the previews' click-to-flip, their
    // wheel zoom, the clip lists' rows -- still received clicks THROUGH a
    // modal popup (user, 2026-09-27: picking a theme also flipped the
    // preview); they check modalOpen. Each modal popup counts itself in.
    property int modals: 0
    readonly property bool modalOpen: modals > 0
    // The toast stack (ToastHost sets it): Glasses-Free 3D cuts holes for toasts over the preview
    property Item toastHost: null
    readonly property int calmTickMs: 125

    // ---- type (v1 app_fonts roles) -----------------------------------------
    readonly property var fonts: hasShell ? Shell.fonts : ({})
    readonly property string headingFont: fonts.heading || "Segoe UI"
    readonly property string bodyFont: fonts.body || "Segoe UI"
    readonly property string buttonFont: fonts.button || "Segoe UI"
    readonly property string tabFont: fonts.tab || "Segoe UI"
    readonly property string iconFont: fonts.icons || "Segoe MDL2 Assets"
    readonly property string monoFont: fonts.mono || "Consolas"
    readonly property int headingSize: 15
    readonly property int bodySize: 13
    readonly property int smallSize: 11
    readonly property int buttonSize: 13
    readonly property int tabSize: 13

    // ---- density ------------------------------------------------------------
    // Pages never scroll: every stage has to fit its settings in the window.
    // These are the numbers that budget is made of.
    readonly property int rowHeight: 28       // one setting (v1's entry height)
    readonly property int rowGap: 4           // between settings
    readonly property int sectionPad: 10      // inside a section, top/bottom
    readonly property int gap: 10             // between sections

    function headingText(t) { return String(t).toUpperCase() }
    function tabText(t) { return String(t).toUpperCase() }
    // Caps with a double gap between words: in caps, and in a face as tight
    // as Cal Sans, one space let "BROWSE FOLDER" read as one word (v1).
    function buttonText(t) { return String(t).toUpperCase().split(" ").join("  ") }

    // ---- colour -----------------------------------------------------------
    readonly property int fadeMs: dur(520)

    property color bg: p.bg
    property color fg: p.fg
    property color surface: p.surface
    property color field: p.field
    property color trough: p.trough
    property color accent: p.accent
    // the clip lists' row bars (Upcoming Jobs): fill and empty track -- True Black's own
    property color listFill: p.listFill || p.accent
    property color listTrack: p.listTrack || p.trough
    property color accentInk: p.accentInk
    property color accentFg: p.accentFg
    // View > Show Section Outlines (v1): a 1px line round every section in
    // the palette's outline colour; off, none at all.
    readonly property bool outlines: hasShell ? Shell.sectionOutlines : true
    property color outline: p.outline || p.accent
    // One SCREEN pixel, in page units (user, 2026-09-27: thinner). 1 logical
    // px was 1.5 physical at 150% Windows scaling -- drawn as a soft 2 px
    // line. Main sets `hairline` from the screen's pixel ratio and the UI
    // Scale zoom; 1 until it does.
    property real hairline: 1
    readonly property real outlineWidth: outlines ? hairline : 0
    property color success: isDark ? "#3fb950" : "#1f883d"
    property color warning: isDark ? "#e3b341" : "#9a6700"
    property color danger: isDark ? "#f85149" : "#cf222e"

    Behavior on bg { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on fg { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on surface { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on field { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on trough { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on accent { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on accentInk { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on accentFg { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on outline { ColorAnimation { duration: theme.fadeMs; easing.type: Easing.InOutQuad } }
    Behavior on success { ColorAnimation { duration: theme.fadeMs } }
    Behavior on warning { ColorAnimation { duration: theme.fadeMs } }
    Behavior on danger { ColorAnimation { duration: theme.fadeMs } }

    // Light palettes over glass (user, 2026-10-01): muted / faint text was the
    // ink at 66% / 40% alpha, so below ~60% opacity the bright backdrop showed
    // through the letters and washed them out (grey letters with a thin light
    // rim). It is opaque ink mixed with the section colour instead -- the same
    // tone when solid -- and on light palettes darker the more backdrop shows
    // (seeThrough); disabled controls fade less with it (dim).
    readonly property real seeThrough: isDark ? 0 : glass * (1 - groundKeep)
    readonly property color fgMuted: mix(surface, fg, 0.66 + 0.30 * seeThrough)
    readonly property color fgFaint: mix(surface, fg, 0.40 + 0.50 * seeThrough)
    // a disabled control's opacity `a`, raised on light glass
    function dim(a) { return a + (1 - a) * 0.8 * seeThrough }
    readonly property color border: alpha(fg, 0.10)
    readonly property color borderStrong: alpha(fg, 0.24)
    readonly property color hover: alpha(fg, 0.06)
    readonly property color knob: isDark ? "#f2f2f2" : "#ffffff"
    // the accent is light (white): what sits on it takes the dark ink (accentFg)
    readonly property bool accentLight: Qt.colorEqual(p.accentFg, "#101010")
    // the unfilled arc of a round slider (DialField): grey on dark palettes,
    // where the trough is near black (user, 2026-09-27)
    readonly property color dialTrack: isDark ? alpha(fg, 0.2) : trough

    // ---- glass ------------------------------------------------------------
    // 0 = solid, 1 = over the Mica/Acrylic backdrop. Animated, so the tint
    // thins out and the material shows through gradually. Sections keep most
    // of their tint so text on them stays readable.
    property real glass: 0
    Behavior on glass { NumberAnimation { duration: theme.dur(520); easing.type: Easing.InOutQuad } }
    // How much of its tint the ground keeps over the glass: View > Glass
    // Opacity (Main binds it; -1 = the palette's default). Dark defaults to
    // 70%: over the system backdrop 75% matched v1's chroma-keyed glass and
    // the user settled on 60%; the accent acrylic (platform_win, which stays
    // frosted when inactive) is brighter, and 70% over it measures what 60%
    // did. Light defaults to 50%.
    property real glassKeep: -1
    readonly property real defaultKeep: isDark ? 0.7 : 0.5
    readonly property real groundKeep: glassKeep >= 0 ? glassKeep : defaultKeep
    readonly property color ground: alpha(bg, 1 - glass * (1 - groundKeep))
    readonly property color panel: alpha(surface, 1 - glass * 0.38)
    // Under text that sits on the bare ground -- the caption and tab strip,
    // tab bars, a stage's foot, the status bar (user, 2026-10-01): text over
    // glass thinner than a section's ~62% comes out with a light rim and
    // ragged edges (tested on the real acrylic). This tops the ground up to a
    // section's strength there; nothing when the ground is that solid already.
    readonly property real groundAlpha: 1 - glass * (1 - groundKeep)
    readonly property real bedAlpha: Math.max(0, (0.62 - groundAlpha) / Math.max(0.01, 1 - groundAlpha))
    readonly property color bed: alpha(bg, bedAlpha)

    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
    function mix(a, b, t) {
        return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, a.a + (b.a - a.a) * t)
    }

    // ---- film grain (grain.py; user, 2026-09-28) ------------------------------
    // This theme's grain as the tuner saved it, the theme popout's switch, and
    // the previews kept clean (GrainHole registers itself here).
    readonly property var grain: hasShell ? (Shell.grainTable[name] || Shell.grainDefault) : ({})
    readonly property string grainSource: {
        if (!hasShell || !Shell.filmGrain || !grain.enabled || !(grain.amount > 0)) return ""
        return "image://grain/" + Number(grain.amount).toFixed(1) + "_" + Number(grain.light).toFixed(0)
               + "_" + Number(grain.size).toFixed(1) + "_1"
    }
    property var grainHoles: []

    // ---- toasts -----------------------------------------------------------
    signal toastRequested(string kind, string title, string message)
    function toast(kind, title, message) { toastRequested(kind, title, message) }

    // Segoe Fluent Icons code points.
    readonly property var icon: ({
        play: "", pause: "", stop: "", settings: "", check: "",
        close: "", add: "", remove: "", del: "", folder: "", refresh: "",
        download: "", search: "", down: "", right: "", left: "", up: "",
        info: "", warning: "", error: "", save: "", edit: "",
        view: "", more: "", menu: "", sync: "", copy: "",
        filter: "", layers: "", video: "", openFolder: "", file: "",
        console: "", help: "", merge: "", picture: "", undo: "", redo: "", toStart: "", toEnd: "",
        fullscreen: "\uE740",
        bell: "\uEA8F", project: "", importIcon: ""
    })
}
