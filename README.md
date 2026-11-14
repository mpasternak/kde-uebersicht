# gnome-uebersicht

[Übersicht](https://tracesof.net/uebersicht/)-compatible desktop widgets for
GNOME Shell and KDE Plasma on Wayland. Widgets are web components (JSX + CSS) fed by shell
commands, drawn on the desktop above the wallpaper and below all windows.

Existing Übersicht `.jsx` widgets run unchanged as long as their commands work
on Linux (no `osascript`, no macOS paths).

## How it works

```
GNOME Shell extension ──launches──▶ daemon (Python, GTK3 + WebKitGTK)
   │  pins its window to the desktop:     │  transparent full-screen web view
   │  DESKTOP type, all workspaces,       │  runs widget commands (bash)
   │  primary monitor, above DING icons   │  compiles JSX with esbuild
   │                                      │  click-through outside widgets
   └─ top bar menu ──stdin commands──────▶│  (input region = widget boxes)
                                          ▼
                              JS runtime (React 18 + emotion)
                              implements the Übersicht widget API
```

- **extension/extension.js** — thin GNOME Shell extension. Starts the daemon as
  a trusted Wayland client (`Meta.WaylandClient`), turns its window into a
  desktop-layer window and adds a top bar menu (show/hide all widgets or each
  one separately, refresh, web inspector). Restarts the daemon if it dies.
- **extension/uebersicht_daemon.py** — WebKitGTK window, `ubersicht://` scheme
  serving the runtime and widget assets, JS bridge for `run`/compile, file
  watcher with hot reload.
- **extension/ubs_core.py** — the daemon's pure-Python parts (widget discovery,
  state, esbuild invocation), importable without GTK.
- **runtime/runtime.jsx** + **runtime/core.js** — the widget runtime (DOM layer +
  DOM-free state machine), bundled into `extension/runtime/runtime.js`.

Only changes to `extension.js` require logging out (Wayland cannot restart
GNOME Shell in place). Daemon/runtime changes: top bar menu → *Restart daemon*.
Widget changes: picked up automatically.

## Install

Requirements: GNOME Shell 48–50, `python3-gi`, `gir1.2-webkit2-4.1`, Node.js/npm
(build time only, plus the `esbuild` binary at runtime).

From a checkout (development, the extension is symlinked to the repo):

```sh
./install.sh
# log out and back in (Wayland), then:
gnome-extensions enable uebersicht@local
```

From a [release](https://github.com/mpasternak/gnome-uebersicht/releases) zip
(needs `esbuild` on `PATH`, e.g. `npm install -g esbuild`):

```sh
gnome-extensions install gnome-uebersicht.zip
# log out and back in, then:
gnome-extensions enable uebersicht@local
```

Publishing on extensions.gnome.org is tracked in
[#1](https://github.com/mpasternak/gnome-uebersicht/issues/1).

Widgets live in `~/.local/share/uebersicht/widgets`
(override with `UBERSICHT_WIDGETS_DIR`).

## KDE Plasma

`kde/` is a native Plasma backend: the same widgets, runtime and state, with Qt
instead of GTK and no Shell extension.

```
systemd user service ──▶ kde/uebersicht_plasma.py (PyQt6)
                            │  Desktop.qml: Qt Quick window on the desktop layer
                            │    (LayerShellQt, layer BOTTOM: above the wallpaper
                            │    and desktop icons, below all windows, all desktops)
                            │  QtWebEngine view, QWebChannel bridge emulating
                            │    WebKit's messageHandlers → runtime.js unchanged
                            │  click-through outside widgets (window input mask)
                            │  tray icon (StatusNotifierItem) with the menu
                            └─ control socket: uebersicht_plasma.py --send <cmd>
```

Requirements: Plasma 6 on Wayland, Qt ≥ 6.9 and

```sh
sudo apt install python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel \
  qml6-module-qtwebengine qml6-module-qtwebchannel qml6-module-org-kde-layershell
```

plus Node.js/npm for the runtime bundle. Then:

```sh
kde/install.sh     # builds the runtime, installs and starts the systemd user service
```

The service (`plasma-uebersicht.service`) starts with the Plasma session and is
skipped in other desktops. The tray icon menu matches the GNOME top bar menu
(left click shows/hides all widgets); *Widget size → Follow system font size*
scales widgets with Plasma's general font (10 pt = 100%). From a terminal:

```sh
kde/uebersicht_plasma.py --send status             # JSON: widgets, hidden, zoom
kde/uebersicht_plasma.py --send "widget-hide hello.jsx"
kde/uebersicht_plasma.py --send restart            # after daemon/QML/runtime changes
```

Debugging: `journalctl --user -u plasma-uebersicht -f`, tray menu → *Web
inspector*, or `kde/uebersicht_plasma.py --debug` for an ordinary window.

## Widget API

Same as Übersicht:

```jsx
import { css, run } from "uebersicht";

export const command = "date +%H:%M";        // or (dispatch) => { ... }
export const refreshFrequency = 1000;        // ms, or false to run once
export const className = `top: 20px; left: 20px; color: white;`;

export const initialState = { output: "" };  // optional
export const updateState = (event, prev) => // optional
  event.type === "UB/COMMAND_RAN" ? { ...prev, output: event.output } : prev;
export const init = (dispatch) => {};        // optional

export const render = ({ output, error }, dispatch) => <h1>{output}</h1>;
```

Supported:

- `command` (string, run with `bash -c` in the widgets dir, or a function receiving `dispatch`)
- `refreshFrequency`, `render`, `className` (string or object), `initialState`, `updateState`, `init`
- `uebersicht` module: `run`, `css`, `styled`, `React` (+ `cx`, `injectGlobal`, `keyframes`)
- relative imports and `node_modules` next to the widget (bundled by esbuild)
- `localStorage` (persistent), links open in the default browser
- `*.jsx` files and `*.widget/index.jsx` folders; `node_modules`, `lib`, `src` and dot/underscore files are skipped

Widget size follows GNOME's display scale and text scaling factor (Settings →
Accessibility → Large Text) live; the top bar menu → *Widget size* can pin it
to a fixed zoom instead (100–200%).

Not supported (yet): legacy CoffeeScript widgets, per-widget screen selection,
multiple monitors (widgets render on the primary monitor).

## Development

```sh
npm ci
npm run lint && npm run build && npm test     # JS: eslint, bundle, node --test
python3 -m unittest discover -s tests         # Python: daemon core
uvx ruff check extension kde tests            # Python lint
pre-commit install                            # optional git hooks
```

CI runs all of the above on every push and builds the extension zip; pushing a
`v*` tag publishes it as a GitHub release.

## Relationship to Übersicht

This is an independent reimplementation of the Übersicht widget API for GNOME;
it shares no code with [Übersicht](https://github.com/felixhageloh/uebersicht)
by Felix Hageloh, and is not affiliated with or endorsed by it. All credit for
the widget model goes to the original project.

## License

MIT

## Debugging

- Top bar menu → *Web inspector (debug)* opens WebKit's inspector.
- Daemon output and `console.log` from widgets go to the journal:
  `journalctl --user -f -o cat | grep uebersicht`
- Run the daemon by hand in a normal window:
  `./extension/uebersicht_daemon.py --debug`
