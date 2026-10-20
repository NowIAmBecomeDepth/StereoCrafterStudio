.pragma library

// Glasses-Free 3D (user, 2026-10-11; studio/sr_display.py): why the option is greyed out
// in `win`, or "" when it can be used there. `hub` is the Glasses3D context property.
// (Its properties and the window's screen are read here, so a binding calling this
// follows them: plugging the display in, or moving the window, re-evaluates it.)
function whyNot(hub, win) {
    if (!hub || !hub.available)
        return "Glasses-Free 3D needs a connected glasses-free 3D monitor on Leia's SR platform, "
             + "with its SR software installed and running."
    if (!win || !win.screen || win.screen.name !== hub.screenName)
        return "Glasses-Free 3D shows on the " + (hub.displayName || "3D monitor")
             + ". Move this window onto it to use it."
    return ""
}
