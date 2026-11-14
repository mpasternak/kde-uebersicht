#!/usr/bin/python3
"""plasma-uebersicht: Übersicht-style desktop widgets for KDE Plasma (Wayland).

A transparent Qt Quick window on the desktop layer (LayerShellQt, layer BOTTOM:
above the wallpaper and desktop icons, below all windows) with a QtWebEngine
view that runs the same widget runtime as the GNOME extension. A system tray
icon offers the same menu as the GNOME top bar indicator.

Control from the terminal (talks to the running daemon over a Unix socket):
  uebersicht_plasma.py --send reload | refresh | hide | show | inspect | quit
                              | widget-hide <id> | widget-show <id>
                              | zoom system|<factor> | status | restart | open-folder
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "extension"))  # ubs_core, shared with the GNOME daemon

from ubs_core import control_socket_path, default_widgets_dir, send_command  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--widgets", default=str(default_widgets_dir()), help="widgets directory")
    ap.add_argument("--esbuild", default=None, help="path to the esbuild binary")
    ap.add_argument("--debug", action="store_true", help="ordinary window, no click-through")
    ap.add_argument("--send", metavar="COMMAND", help="send a command to the running daemon and exit")
    ap.add_argument("--dump-after", type=float, default=0, help="(testing) print page text after N s, exit")
    ap.add_argument("--dump-js", default=None, help="(testing) async JS function body whose result is printed")
    args = ap.parse_args()

    if args.send:
        try:
            print(send_command(control_socket_path(), args.send), end="")
        except (ConnectionRefusedError, FileNotFoundError):
            sys.exit(f"plasma-uebersicht is not running (no listener on {control_socket_path()})")
        return

    from plasma_daemon import run  # Qt is imported only here, so --send stays fast

    sys.exit(run(args))


if __name__ == "__main__":
    main()
