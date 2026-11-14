"""The kde-uebersicht daemon (entry point: kde_uebersicht.py).

QtWebEngine renders the widgets, LayerShellQt (in Desktop.qml) puts the window
on the desktop layer, QSystemTrayIcon (StatusNotifierItem on Plasma) is the menu.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from pathlib import Path

from PyQt6.QtCore import (
    QBuffer,
    QEvent,
    QFile,
    QFileSystemWatcher,
    QIODevice,
    QObject,
    QProcess,
    QProcessEnvironment,
    QRect,
    QStandardPaths,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QActionGroup, QDesktopServices, QIcon, QRegion
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtQml import QQmlApplicationEngine, QQmlComponent
from PyQt6.QtWebChannel import QWebChannel  # noqa: F401  (loads :/qtwebchannel/qwebchannel.js)
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler
from PyQt6.QtWebEngineQuick import QtWebEngineQuick
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from ubs_core import (
    ZOOM_CHOICES,
    Widgets,
    changed_paths,
    control_socket_path,
    esbuild_argv,
    find_esbuild,
    load_state,
    parse_command,
    parse_zoom_mode,
    save_state,
    scale_rects,
    scan_tree,
    send_command,
    webkit_zoom_level,
    widget_label,
    zoom_key,
)

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
APP_ID = "kde-uebersicht"
SCHEME = b"ubersicht"
APP_URL = "ubersicht://app/"
RUN_TIMEOUT_MS = 120_000
BASE_FONT_PT = 10.0  # Plasma's default UI font size; "system" zoom follows the ratio to it
TRAY_ICON = "widget-alternatives"
NO_INPUT = QRect(-10, -10, 1, 1)  # an empty QRegion would mean "no mask", i.e. all clicks land here

# Emulates WebKit's window.webkit.messageHandlers.ubersicht (postMessage returning
# a promise, as in Übersicht and gnome-uebersicht) on top of QWebChannel.
# Also lets the daemon evaluate JS in the page (the `evaluate` signal).
BRIDGE_JS = r"""
(function () {
  const pending = new Map();
  let seq = 0;
  const ready = new Promise((resolve) => {
    new QWebChannel(qt.webChannelTransport, (ch) => {
      const b = ch.objects.ubs;
      b.reply.connect((id, json) => {
        const done = pending.get(id);
        if (done) { pending.delete(id); done(JSON.parse(json)); }
      });
      b.evaluate.connect(async (id, code) => {
        let value;
        try { value = await (new Function("return (async () => {" + code + "})()"))(); }
        catch (e) { value = "ERROR " + e; }
        if (id) b.request(0, JSON.stringify({ op: "evaluated", id, value: String(value) }));
      });
      resolve(b);
    });
  });
  window.webkit = { messageHandlers: { ubersicht: { postMessage: (msg) => ready.then((b) =>
    new Promise((res) => { const id = ++seq; pending.set(id, res); b.request(id, JSON.stringify(msg)); })) } } };
})();
"""
RUNTIME_TAG = '<script src="/__ubs/runtime.js"></script>'
BRIDGE_TAGS = '<script src="/__ubs/qwebchannel.js"></script><script src="/__ubs/bridge.js"></script>'
DEFAULT_DUMP_JS = (
    "return JSON.stringify({text: document.body.innerText,"
    " html: document.getElementById('ubs-root').innerHTML.slice(0, 600),"
    " ls: localStorage.length})"
)


def log(*a):
    print("[ubs]", *a, file=sys.stderr, flush=True)


class AlreadyRunning(RuntimeError):
    pass


# ---------------------------------------------------------------- page plumbing
class Bridge(QObject):
    """The object the page sees as `ubs` over QWebChannel."""

    reply = pyqtSignal(int, str)  # (request id, JSON)
    evaluate = pyqtSignal(int, str)  # (id, async function body); id 0 = no result wanted

    def __init__(self, handler):
        super().__init__()
        self.handler = handler

    @pyqtSlot(int, str)
    def request(self, rid, payload):
        try:
            msg = json.loads(payload)
        except ValueError as e:
            self.reply.emit(rid, json.dumps({"error": f"bad message: {e}"}))
            return
        self.handler(msg, lambda obj: self.reply.emit(rid, json.dumps(obj)))


class SchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, serve, parent):
        super().__init__(parent)
        self.serve = serve

    def requestStarted(self, job):  # noqa: N802 (Qt override)
        found = self.serve(job.requestUrl().path() or "/")
        if found is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        body, mime = found
        buf = QBuffer(job)  # owned by the job, freed with it
        buf.setData(body)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode(), buf)


# ---------------------------------------------------------------- daemon
class Daemon(QObject):
    def __init__(self, args, app: QApplication):
        super().__init__()
        self.args = args
        self.app = app
        self.widgets = Widgets(Path(args.widgets).expanduser())
        self.widgets.root.mkdir(parents=True, exist_ok=True)
        self.esbuild = find_esbuild(args.esbuild)
        if not self.esbuild:
            log("WARNING: esbuild not found — JSX widgets will not compile")
        state = load_state()
        self.hidden = bool(state.get("desktop_hidden", False))
        self.hidden_widgets: set[str] = set(state.get("hidden_widgets", []))
        self.zoom_mode = parse_zoom_mode(state.get("zoom"))
        self.implicit_zoom = 1.0
        self.inspector = None
        self.menu = None
        self.dump_id = 0

        self.server = self._listen()
        self.app.aboutToQuit.connect(self._shutdown)
        self.app.installEventFilter(self)  # ApplicationFontChange → "system" zoom

        self.bridge = Bridge(self._on_message)
        self._build_window()
        self._build_tray()

        self.snapshot = scan_tree(self.widgets.root)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._on_fs_change)
        self.watcher.fileChanged.connect(self._on_fs_change)
        self.reload_timer = QTimer(self, singleShot=True, interval=300)
        self.reload_timer.timeout.connect(self._flush_reload)
        self._watch_tree()
        self._announce()

    # ------------------------------------------------------------ window
    def _build_window(self):
        data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation))
        self.engine = QQmlApplicationEngine(self)
        self.engine.warnings.connect(lambda ws: [log("qml:", w.toString()) for w in ws])
        ctx = self.engine.rootContext()
        ctx.setContextProperty("ubsDataDir", str(data / APP_ID / "webengine"))  # localStorage lives here
        ctx.setContextProperty("ubsCacheDir", str(cache / APP_ID))
        ctx.setContextProperty("ubsBridge", self.bridge)
        ctx.setContextProperty("ubsDebug", self.args.debug)
        qml = "DebugWindow.qml" if self.args.debug else "Desktop.qml"
        self.engine.load(QUrl.fromLocalFile(str(HERE / qml)))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError(f"cannot load {qml} (see the qml: messages above)")
        self.win = roots[0]
        self.view = self.win.findChild(QObject, "view")
        # the page is loaded below, after the ubersicht:// handler is in place
        self.scheme_handler = SchemeHandler(self._serve, self)
        self.view.property("profile").installUrlSchemeHandler(SCHEME, self.scheme_handler)
        if not self.args.debug:
            self.win.setMask(QRegion(NO_INPUT))
        self.view.setProperty("url", QUrl(APP_URL))
        self.win.show()

    def _serve(self, path: str):
        """Bytes and MIME type for a ubersicht:// path, or None."""
        if path in ("/", "/index.html"):
            html = (RUNTIME_DIR / "index.html").read_text()
            return html.replace(RUNTIME_TAG, BRIDGE_TAGS + RUNTIME_TAG).encode(), "text/html"
        if path == "/__ubs/bridge.js":
            return BRIDGE_JS.encode(), "text/javascript"
        if path == "/__ubs/qwebchannel.js":
            f = QFile(":/qtwebchannel/qwebchannel.js")
            if not f.open(QIODevice.OpenModeFlag.ReadOnly):
                log("cannot read qwebchannel.js from Qt resources")
                return None
            return bytes(f.readAll()), "text/javascript"
        if path.startswith("/__ubs/"):
            f = (RUNTIME_DIR / path[len("/__ubs/") :]).resolve()
            if RUNTIME_DIR.resolve() not in f.parents:
                return None
        else:
            f = self.widgets.path(path.lstrip("/"))
        if not f or not f.is_file():
            return None
        return f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream"

    def js(self, code: str):
        self.bridge.evaluate.emit(0, code)

    def _set_region(self, rects):
        if self.args.debug:
            return
        region = QRegion()
        for x, y, w, h in rects:
            region = region.united(QRect(x, y, w, h))
        self.win.setMask(region if not region.isEmpty() else QRegion(NO_INPUT))

    def _show_inspector(self):
        if self.inspector is None:
            comp = QQmlComponent(self.engine, QUrl.fromLocalFile(str(HERE / "Inspector.qml")))
            self.inspector = comp.createWithInitialProperties({"target": self.view})
            if self.inspector is None:
                log("inspector:", "; ".join(e.toString() for e in comp.errors()))
                return
        self.inspector.show()
        self.inspector.requestActivate()

    # ------------------------------------------------------------ zoom
    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        if obj is self.app and event.type() == QEvent.Type.ApplicationFontChange:
            self._apply_zoom()
        return False

    def _apply_zoom(self):
        text_scale = (self.app.font().pointSizeF() or BASE_FONT_PT) / BASE_FONT_PT
        level = webkit_zoom_level(self.zoom_mode, text_scale, self.implicit_zoom)
        current = self.view.property("zoomFactor") or 1.0
        if abs(level - current) > 0.005:
            log(f"zoom: mode={self.zoom_mode} text={text_scale:.2f} implicit={self.implicit_zoom:.3f} → {level:.3f}")
            self.view.setProperty("zoomFactor", level)  # the page resizes and resends its region

    def _set_zoom_mode(self, value):
        self.zoom_mode = parse_zoom_mode(value)
        state = load_state()
        state["zoom"] = self.zoom_mode
        save_state(state)
        self._apply_zoom()
        self._announce()

    # ------------------------------------------------------------ files
    def _watch_tree(self):
        root = self.widgets.root
        dirs, files = {str(root)}, set()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
            dirs.update(os.path.join(dirpath, d) for d in dirnames)
            files.update(os.path.join(dirpath, f) for f in filenames if not f.startswith("."))
        wanted = dirs | files
        watched = set(self.watcher.directories()) | set(self.watcher.files())
        if stale := watched - wanted:
            self.watcher.removePaths(sorted(stale))
        if new := wanted - watched:
            self.watcher.addPaths(sorted(new))

    def _on_fs_change(self, _path):
        self.reload_timer.start()  # debounce: editors write in several steps

    def _flush_reload(self):
        after = scan_tree(self.widgets.root)
        changed, self.snapshot = changed_paths(self.snapshot, after), after
        self._watch_tree()  # files saved by rename are new inodes: watch them again
        if not changed:
            return
        ids = set(self.widgets.ids())
        # only widget files changed → reload just those; a helper file changed → reload all
        only = sorted(changed & ids) if changed <= ids else None
        log("reloading:", only or "all widgets")
        self.js(f"window.__ubs && window.__ubs.reload({json.dumps(only)})")
        self._announce()

    # ------------------------------------------------------------ status, tray
    def status(self) -> dict:
        widgets = [{"id": i, "hidden": i in self.hidden_widgets} for i in self.widgets.ids()]
        return {"widgets": widgets, "desktopHidden": self.hidden, "zoom": self.zoom_mode}

    def _announce(self):
        self._update_menu(self.status())

    def _build_tray(self):
        icon = QIcon.fromTheme(TRAY_ICON, QIcon.fromTheme("preferences-desktop"))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Übersicht widgets")
        # left click toggles all widgets; right click opens the menu
        self.tray.activated.connect(
            lambda reason: (
                reason == QSystemTrayIcon.ActivationReason.Trigger and self._later("show" if self.hidden else "hide")
            )
        )
        self.tray.show()

    def _later(self, line: str):
        # The command rebuilds the menu, which must not happen inside its own signal handler.
        QTimer.singleShot(0, lambda: self.handle_line(line))

    def _update_menu(self, status: dict):
        menu = QMenu()
        act = menu.addAction("Widgets on desktop")
        act.setCheckable(True)
        act.setChecked(not status["desktopHidden"])
        act.toggled.connect(lambda on: self._later("show" if on else "hide"))
        menu.addSeparator()

        if not status["widgets"]:
            menu.addAction("No widgets").setEnabled(False)
        for w in status["widgets"]:
            a = menu.addAction(widget_label(w["id"]))
            a.setCheckable(True)
            a.setChecked(not w["hidden"])
            a.toggled.connect(lambda on, wid=w["id"]: self._later(f"widget-{'show' if on else 'hide'} {wid}"))
        menu.addSeparator()

        current = zoom_key(status["zoom"])
        labels = dict(ZOOM_CHOICES)
        sub = menu.addMenu(f"Widget size: {labels.get(current, current)}")
        group = QActionGroup(sub)
        for key, label in ZOOM_CHOICES:
            a = sub.addAction("Follow system font size" if key == "system" else label)
            a.setCheckable(True)
            a.setChecked(key == current)
            group.addAction(a)
            a.triggered.connect(lambda _on, k=key: self._later(f"zoom {k}"))
        menu.addSeparator()

        for label, cmd in (
            ("Refresh widgets", "refresh"),
            ("Reload widgets", "reload"),
            ("Open widgets folder", "open-folder"),
            (None, None),
            ("Restart daemon", "restart"),
            ("Web inspector (debug)", "inspect"),
            ("Quit", "quit"),
        ):
            if label is None:
                menu.addSeparator()
                continue
            menu.addAction(label).triggered.connect(lambda _on, c=cmd: self._later(c))

        old, self.menu = self.menu, menu
        self.tray.setContextMenu(menu)
        if old is not None:
            old.deleteLater()

    # ------------------------------------------------------------ commands
    def handle_line(self, line: str) -> dict:
        """One control command, from the tray menu or the control socket."""
        cmd, arg = parse_command(line)
        if cmd not in ("ping", "status"):
            log("command:", cmd, arg)
        if cmd == "zoom" and arg:
            self._set_zoom_mode(arg)
        elif cmd in ("widget-hide", "widget-show") and arg:
            if arg not in self.widgets.ids():
                return {"error": f"no such widget: {arg}"}
            if cmd == "widget-hide":
                self.hidden_widgets.add(arg)
            else:
                self.hidden_widgets.discard(arg)
            state = load_state()
            state["hidden_widgets"] = sorted(self.hidden_widgets)
            save_state(state)
            # sync([]) mounts newly visible widgets and drops hidden ones, leaving the rest alone
            self.js("window.__ubs && window.__ubs.reload([])")
            self._announce()
        elif cmd == "reload":
            self.js("window.__ubs && window.__ubs.reload(null)")
        elif cmd == "refresh":
            self.js("window.__ubs && window.__ubs.refreshAll()")
        elif cmd in ("hide", "show"):
            self.hidden = cmd == "hide"
            state = load_state()
            state["desktop_hidden"] = self.hidden
            save_state(state)
            self.js(f"window.__ubs && window.__ubs.setHidden({json.dumps(self.hidden)})")
            self._announce()
        elif cmd == "inspect":
            self._show_inspector()
        elif cmd == "open-folder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.widgets.root)))
        elif cmd == "restart":
            QTimer.singleShot(0, self._restart)
        elif cmd == "quit":
            QTimer.singleShot(0, self.app.quit)
        elif cmd not in ("ping", "status"):
            return {"error": f"unknown command {cmd!r}"}
        return {"ok": True, **self.status()}

    def _restart(self):
        """Re-exec the daemon (picks up daemon, QML and runtime changes)."""
        log("restarting")
        self._shutdown()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    # ------------------------------------------------------------ control socket
    def _listen(self) -> QLocalServer:
        path = control_socket_path()
        if path.exists():
            try:
                send_command(path, "ping", timeout=1.0)
            except (TimeoutError, ConnectionRefusedError, FileNotFoundError):
                path.unlink(missing_ok=True)  # left behind by a crashed instance
            else:
                raise AlreadyRunning(f"another instance is listening on {path}")
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(str(path)):
            raise RuntimeError(f"cannot listen on {path}: {server.errorString()}")
        server.newConnection.connect(self._on_connection)
        return server

    def _on_connection(self):
        while (sock := self.server.nextPendingConnection()) is not None:
            buf = bytearray()

            def on_ready(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                if b"\n" not in buf:
                    return
                line = buf.split(b"\n", 1)[0].decode("utf-8", "replace")
                sock.write((json.dumps(self.handle_line(line)) + "\n").encode())
                sock.disconnectFromServer()

            sock.readyRead.connect(on_ready)
            sock.disconnected.connect(sock.deleteLater)

    def _shutdown(self):
        if self.server.isListening():
            self.server.close()  # also removes the socket file

    # ------------------------------------------------------------ page messages
    def _on_message(self, msg: dict, reply):
        op = msg.get("op")
        if op == "run":
            # Like Übersicht: run through a shell, with the widgets directory as cwd.
            self._spawn(["/bin/bash", "-c", msg.get("cmd", "")], self.widgets.root, reply)
        elif op == "compile":
            self._compile(msg.get("id", ""), reply)
        elif op == "list":
            reply({"ids": self.widgets.visible(self.hidden_widgets), "hidden": self.hidden})
        elif op == "region":
            alloc = (self.win.width(), self.win.height())
            viewport = msg.get("viewport")
            if viewport and viewport[0] > 0 and alloc[0] > 0:
                self.implicit_zoom = alloc[0] / (viewport[0] * (self.view.property("zoomFactor") or 1.0))
                self._apply_zoom()
            self._set_region(scale_rects(msg.get("rects") or [], viewport, alloc))
            reply({"ok": True})
        elif op == "evaluated":
            if msg.get("id") == self.dump_id:
                print(msg.get("value"), flush=True)
                self.app.quit()
        else:
            reply({"error": f"unknown operation {op!r}"})

    def _spawn(self, argv, cwd, done):
        proc = QProcess(self)
        proc.setWorkingDirectory(str(cwd))
        env = QProcessEnvironment.systemEnvironment()
        local_bin = str(Path.home() / ".local" / "bin")
        path = env.value("PATH", "/usr/local/bin:/usr/bin:/bin")
        if local_bin not in path.split(":"):
            env.insert("PATH", f"{local_bin}:{path}")
        proc.setProcessEnvironment(env)
        timer = QTimer(proc, singleShot=True, interval=RUN_TIMEOUT_MS)
        timed_out = []

        def kill():
            timed_out.append(True)
            proc.kill()

        timer.timeout.connect(kill)

        def finished(code, status):
            timer.stop()
            out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            err = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
            if status != QProcess.ExitStatus.NormalExit:
                code = 137 if timed_out else 128  # killed (timeout) / crashed
            proc.deleteLater()
            done({"status": code, "stdout": out, "stderr": err})

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                timer.stop()
                proc.deleteLater()
                done({"status": 127, "stdout": "", "stderr": proc.errorString()})

        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        proc.start(argv[0], argv[1:])
        timer.start()
        proc.closeWriteChannel()

    def _compile(self, wid, done):
        p = self.widgets.path(wid)
        if not p:
            done({"error": f"no such widget: {wid}"})
            return
        if not self.esbuild:
            done({"error": "esbuild not found — run `npm install` in the kde-uebersicht repository"})
            return

        def fin(r):
            if r["status"] == 0:
                done({"code": r["stdout"]})
            else:
                done({"error": r["stderr"] or r["stdout"] or f"esbuild exit {r['status']}"})

        self._spawn(esbuild_argv(self.esbuild, p), p.parent, fin)

    def dump_after(self, seconds: float, js_file: str | None):
        """(testing) evaluate JS in the page after N seconds, print the result and quit."""
        body = Path(js_file).read_text() if js_file else DEFAULT_DUMP_JS
        self.dump_id = 1
        QTimer.singleShot(int(seconds * 1000), lambda: self.bridge.evaluate.emit(self.dump_id, body))


def register_scheme():
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    flags = QWebEngineUrlScheme.Flag
    scheme.setFlags(flags.SecureScheme | flags.LocalAccessAllowed | flags.CorsEnabled | flags.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)


def run(args) -> int:
    register_scheme()  # before the application object exists
    QtWebEngineQuick.initialize()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)  # Wayland app_id
    app.setQuitOnLastWindowClosed(False)  # closing the inspector must not quit
    try:
        daemon = Daemon(args, app)
    except AlreadyRunning as e:
        print(f"{APP_ID}: {e}", file=sys.stderr)
        return 1
    log("widgets from", daemon.widgets.root)
    if args.dump_after:
        daemon.dump_after(args.dump_after, args.dump_js)
    return app.exec()
