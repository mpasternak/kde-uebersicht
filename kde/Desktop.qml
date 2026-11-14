// Full-screen transparent window on the desktop layer (wlr-layer-shell via
// LayerShellQt): above the wallpaper and desktop icons, below all windows,
// on every virtual desktop and activity. Clicks outside widgets pass through
// (the daemon sets the input mask).
import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

Window {
    title: "plasma-uebersicht"
    color: "transparent"
    visible: false // shown by the daemon once everything is wired up

    LayerShell.Window.scope: "plasma-uebersicht"
    LayerShell.Window.layer: LayerShell.Window.LayerBottom
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
                               | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    // -1: cover the whole screen, under panels too (same coordinates as on GNOME/macOS)
    LayerShell.Window.exclusionZone: -1
    // widgets may contain text fields: focus only when clicked
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityOnDemand

    UbsView {}
}
