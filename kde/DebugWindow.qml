// --debug: an ordinary decorated window instead of the desktop layer.
import QtQuick
import QtQuick.Window

Window {
    title: "plasma-uebersicht (debug)"
    width: 1280
    height: 800
    color: "#202020"
    visible: false

    UbsView {}
}
