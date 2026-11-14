// Chromium DevTools for the widgets view (tray menu → Web inspector).
import QtQuick
import QtQuick.Window
import QtWebEngine

Window {
    property var target
    title: "plasma-uebersicht — inspector"
    width: 1100
    height: 750
    visible: true

    WebEngineView {
        anchors.fill: parent
        inspectedView: target
    }
}
