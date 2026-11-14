# kde-uebersicht

[![CI](https://github.com/mpasternak/kde-uebersicht/actions/workflows/ci.yml/badge.svg)](https://github.com/mpasternak/kde-uebersicht/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Übersicht](https://tracesof.net/uebersicht/)-compatible desktop widgets for
KDE Plasma on Wayland. Widgets are web components (JSX + CSS) fed by shell
commands, drawn on the desktop above the wallpaper and below all windows.

Existing Übersicht `.jsx` widgets run unchanged as long as their commands work
on Linux (no `osascript`, no macOS paths).

The GNOME counterpart is [gnome-uebersicht](https://github.com/mpasternak/gnome-uebersicht);
both share the widget runtime and the widget API, so the same widgets folder
works on either desktop.

## How it works

```
systemd user service ──▶ app/kde_uebersicht.py (PyQt6)
                            │  Desktop.qml: transparent Qt Quick window on the
                            │    desktop layer (LayerShellQt, layer BOTTOM: above
                            │    wallpaper and desktop icons, below all windows,
                            │    on every virtual desktop)
                            │  QtWebEngine view serving ubersicht:// (runtime +
                            │    widget assets), widget commands run with bash,
                            │    JSX compiled with esbuild, hot reload
                            │  click-through outside widgets (window input mask
                            │    = widget boxes)
                            │  tray icon (StatusNotifierItem) with the menu
                            └─ control socket: kde_uebersicht.py --send <command>
                                          │
                                          ▼
                              JS runtime (React 18 + emotion)
                              implements the Übersicht widget API
```

- **app/kde_uebersicht.py** — entry point and `--send` client.
- **app/daemon.py** — the daemon: web view, `ubersicht://` scheme, QWebChannel
  bridge (`run` / compile / input region), file watcher, tray menu, control socket.
- **app/\*.qml** — the desktop-layer window, the web view, debug and inspector windows.
- **app/ubs_core.py** — pure-Python parts (widget discovery, state, esbuild
  invocation, zoom maths), importable without Qt.
- **runtime/runtime.jsx** + **runtime/core.js** — the widget runtime (DOM layer +
  DOM-free state machine), bundled into `app/runtime/runtime.js`.

## Install

Requirements: Plasma 6 on Wayland, Qt ≥ 6.9 (e.g. Ubuntu 26.04, Kubuntu), Node.js/npm.

```sh
sudo apt install python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel \
  qml6-module-qtwebengine qml6-module-qtwebchannel qml6-module-org-kde-layershell
git clone https://github.com/mpasternak/kde-uebersicht
cd kde-uebersicht
./install.sh
```

`install.sh` builds the runtime and installs and starts `kde-uebersicht.service`
(a systemd user service started with the Plasma session, skipped in other
desktops) plus a `.desktop` entry.

Widgets live in `~/.local/share/uebersicht/widgets`
(override with `UBERSICHT_WIDGETS_DIR`). Changes are picked up automatically.

## Using it

The tray icon menu: show/hide all widgets, show/hide each widget, widget size,
refresh, reload, open the widgets folder, restart the daemon, web inspector.
Left click on the icon shows/hides all widgets.

From a terminal:

```sh
app/kde_uebersicht.py --send status                 # JSON: widgets, hidden, zoom
app/kde_uebersicht.py --send "widget-hide hello.jsx"
app/kde_uebersicht.py --send "zoom 1.25"            # or: zoom system
app/kde_uebersicht.py --send refresh | reload | hide | show | inspect | restart | quit
```

Widget size follows Plasma's display scale. *Widget size → Follow system font
size* also scales widgets with the general font (10 pt = 100%), live; a fixed
zoom (100–200%) can be chosen instead.

After changing the daemon, QML or runtime: `--send restart` (or the tray menu).

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

Not supported (yet): legacy CoffeeScript widgets, per-widget screen selection,
multiple monitors (widgets render on the primary screen).

## Development

```sh
npm ci
npm run lint && npm run build && npm test     # JS: eslint, bundle, node --test
python3 -m unittest discover -s tests         # Python: daemon core
uvx ruff check app tests                      # Python lint
pre-commit install                            # optional git hooks
```

## Debugging

- Logs, including `console.log` from widgets: `journalctl --user -u kde-uebersicht -f`
- Tray menu → *Web inspector (debug)* opens Chromium DevTools for the widgets.
- Run in an ordinary window: `systemctl --user stop kde-uebersicht && app/kde_uebersicht.py --debug`

## Relationship to Übersicht

This is an independent reimplementation of the Übersicht widget API for KDE
Plasma; it shares no code with [Übersicht](https://github.com/felixhageloh/uebersicht)
by Felix Hageloh, and is not affiliated with or endorsed by it. All credit for
the widget model goes to the original project.

## License

MIT
