// The web view that renders all widgets. Shared by Desktop.qml and DebugWindow.qml.
import QtQuick
import QtWebEngine
import QtWebChannel

WebEngineView {
    id: view
    objectName: "view"
    anchors.fill: parent
    backgroundColor: "transparent"
    // assigned once everything is constructed: a binding would run before the
    // prototype is complete and get a null profile (crashes QtWebEngine 6.10)
    Component.onCompleted: view.profile = profileProto.instance()

    // persistent profile: widgets' localStorage survives restarts
    WebEngineProfilePrototype {
        id: profileProto
        storageName: "plasma-uebersicht"
        persistentStoragePath: ubsDataDir
        cachePath: ubsCacheDir
    }
    settings.javascriptCanAccessClipboard: true
    settings.localContentCanAccessRemoteUrls: true
    settings.showScrollBars: false

    webChannel: WebChannel {
        id: channel
        Component.onCompleted: channel.registerObject("ubs", ubsBridge)
    }

    // Links clicked in widgets open in the default browser, not on the desktop.
    onNavigationRequested: (request) => {
        const url = request.url.toString();
        if (!url.startsWith("ubersicht:") && !url.startsWith("about:")) {
            Qt.openUrlExternally(request.url);
            request.reject();
        }
    }
    onNewWindowRequested: (request) => Qt.openUrlExternally(request.requestedUrl)

    onJavaScriptConsoleMessage: (level, message, line, source) =>
        console.log(`[widget] ${message}` + (source ? ` (${source}:${line})` : ""))
    onRenderProcessTerminated: (status, code) => {
        console.warn(`[ubs] web process terminated (status ${status}, code ${code}) — reloading`);
        view.reload();
    }
    onContextMenuRequested: (request) => { request.accepted = !ubsDebug; }
}
