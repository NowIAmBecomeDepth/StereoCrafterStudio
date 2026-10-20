import QtQuick
import Studio

// v1's "Change Inputs" (ctk_theme._collapse_imported_inputs): with a chapter
// picked the stage's path rows are put away, and this button on the heading
// brings them back -- to check them or pick folders by hand -- without
// leaving the chapter. Picking a chapter folds them away again.
//
// On Manual paths too (user, 2026-09-30): there it puts the fields away to
// save room, and Studio remembers it (Shell.foldersHidden).
//
// The page owns the state: bind `open` to it and flip it in `onClicked`.
// `paths` is [{ label, value }], shown in full on hover. `manual`: no chapter.
AccentButton {
    id: btn
    objectName: "changeFolders"
    property bool open: false
    property bool manual: false
    property var paths: []

    text: open ? "Hide Folders" : "Change Folders"
    kind: "outline"

    HoverTip {
        tip: (btn.manual
              ? (btn.open ? "Put the folder fields away to save room. The paths stay as they are; "
                            + "Change Folders shows them again."
                          : "Show the folder fields again, to check them or pick different folders.")
              : btn.open ? "Put the folder fields away again; the chapter's paths stay as they are."
                         : "Show the folder fields again, to check them or pick different folders by hand. "
                           + "Editing a path switches the stage back to Manual paths.")
             + "\n\n" + btn.paths.map(p => p.label + ":\n  " + (p.value || "(not set)")).join("\n")
    }
}
