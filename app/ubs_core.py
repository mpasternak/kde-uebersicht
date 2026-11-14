"""Pure-Python core of the kde-uebersicht daemon (no Qt imports),
so it can be unit-tested anywhere."""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP_DIRS = {"node_modules", "lib", "src", ".git"}
ESBUILD_FLAGS_FILE = HERE / "esbuild-flags.json"


def _xdg(var: str, fallback: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else Path.home() / fallback


def default_widgets_dir() -> Path:
    env = os.environ.get("UBERSICHT_WIDGETS_DIR")
    if env:
        return Path(env).expanduser()
    return _xdg("XDG_DATA_HOME", ".local/share") / "uebersicht" / "widgets"


def state_file() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "kde-uebersicht" / "state.json"


def load_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or state_file()).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict, path: Path | None = None) -> None:
    f = path or state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(f)


def find_esbuild(explicit: str | None = None) -> str | None:
    cands = [explicit, os.environ.get("UBERSICHT_ESBUILD")]
    # app/ lives in the repo — node_modules is in the repo root
    for base in (HERE, HERE.parent):
        cands.append(str(base / "node_modules" / ".bin" / "esbuild"))
    cands.append(shutil.which("esbuild"))
    for c in cands:
        if c and os.access(c, os.X_OK):
            return c
    return None


def esbuild_argv(esbuild: str, widget: Path) -> list[str]:
    flags = json.loads(ESBUILD_FLAGS_FILE.read_text())
    return [esbuild, str(widget), *flags]


class Widgets:
    """Widget discovery in a directory (following Übersicht conventions):
    `*.jsx` files anywhere, plus `index.js(x)` inside `*.widget` folders;
    dot/underscore entries and node_modules/lib/src folders are skipped."""

    def __init__(self, root: Path):
        self.root = root

    def ids(self) -> list[str]:
        out = []
        if not self.root.is_dir():
            return out
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=True):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            rel = Path(dirpath).relative_to(self.root)
            in_widget_dir = any(p.endswith(".widget") for p in rel.parts)
            for f in sorted(filenames):
                if f.startswith((".", "_")):
                    continue
                if f.endswith(".jsx") or (in_widget_dir and f in ("index.js", "index.jsx")):
                    out.append(str(rel / f) if str(rel) != "." else f)
        return sorted(set(out))

    def path(self, wid: str) -> Path | None:
        """Resolve a widget id or asset path; None if missing or outside the root."""
        p = (self.root / wid).resolve()
        try:
            p.relative_to(self.root.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None

    def visible(self, hidden: set[str]) -> list[str]:
        return [i for i in self.ids() if i not in hidden]


def scale_rects(rects, viewport, window_size) -> list[tuple[int, int, int, int]]:
    """Map rectangles from CSS pixels to window (logical) pixels.

    The page may be zoomed (e.g. 'Widget size: 125%' makes CSS pixels 25%
    larger), so the input region must be scaled by window/viewport.
    Rounds outwards so a widget's clickable area is never smaller than drawn."""
    vw, vh = viewport if viewport else (0, 0)
    ww, wh = window_size
    sx = ww / vw if vw and ww else 1.0
    sy = wh / vh if vh and wh else 1.0
    out = []
    for x, y, w, h in rects:
        x0, y0 = math.floor(x * sx), math.floor(y * sy)
        x1, y1 = math.ceil((x + w) * sx), math.ceil((y + h) * sy)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


ZOOM_SYSTEM = "system"
ZOOM_PRESETS = (1.0, 1.1, 1.25, 1.5, 2.0)


def parse_zoom_mode(value) -> str | float:
    """'system' (follow the desktop font size) or a fixed factor such as 1.25."""
    if value == ZOOM_SYSTEM or value is None:
        return ZOOM_SYSTEM
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ZOOM_SYSTEM
    return f if 0.25 <= f <= 5 else ZOOM_SYSTEM


def webkit_zoom_level(mode, text_scale: float, implicit: float) -> float:
    """Zoom factor to give the web view so widgets end up at the wanted size.

    `implicit` is the zoom the web engine already applies on its own (measured
    from the viewport); the result compensates for it."""
    target = text_scale if mode == ZOOM_SYSTEM else float(mode)
    if not target or target <= 0:
        target = 1.0
    return target / (implicit if implicit and implicit > 0 else 1.0)


# --- tray menu and control socket ----------------------------------------------
ZOOM_CHOICES = (
    (ZOOM_SYSTEM, "Follow system text size"),
    ("1", "100%"),
    ("1.1", "110%"),
    ("1.25", "125%"),
    ("1.5", "150%"),
    ("2", "200%"),
)


def zoom_key(mode) -> str:
    """Menu key for a zoom mode: 'system', or the factor without trailing zeros (1.0 → '1')."""
    return ZOOM_SYSTEM if mode == ZOOM_SYSTEM else f"{float(mode):g}"


def widget_label(wid: str) -> str:
    """Human-readable widget name: 'clock.widget/index.jsx' → 'clock.widget', 'hello.jsx' → 'hello'."""
    for suffix in ("/index.jsx", "/index.js", ".jsx"):
        if wid.endswith(suffix):
            return wid[: -len(suffix)]
    return wid


def control_socket_path() -> Path:
    """Unix socket the daemon listens on for control commands."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "kde-uebersicht.sock"
    return Path("/tmp") / f"kde-uebersicht-{os.getuid()}.sock"


def parse_command(line: str) -> tuple[str, str]:
    """Split a control line ('widget-hide clock.jsx') into (command, argument)."""
    cmd, _, arg = line.strip().partition(" ")
    return cmd, arg.strip()


def send_command(path: Path, line: str, timeout: float = 5.0) -> str:
    """Send one control line to the running daemon and return its (JSON) reply."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(line.strip().encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := s.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "replace")


def scan_tree(root: Path) -> dict[str, tuple[float, int]]:
    """(mtime, size) of every file the widgets may depend on, keyed by path relative to root.

    Used to find out what changed after a file watcher event (QFileSystemWatcher
    only says *that* a directory changed, and editors often save by renaming)."""
    out: dict[str, tuple[float, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            full = Path(dirpath) / f
            try:
                st = full.stat()
            except OSError:
                continue  # vanished between listing and stat
            out[str(full.relative_to(root))] = (st.st_mtime, st.st_size)
    return out


def changed_paths(before: dict, after: dict) -> set[str]:
    """Paths added, removed or modified between two scan_tree() snapshots."""
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
