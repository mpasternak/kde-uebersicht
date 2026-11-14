#!/usr/bin/env bash
# Install kde-uebersicht: runtime bundle, .desktop entry and systemd user service.
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$PWD/app"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
WIDGETS="${UBERSICHT_WIDGETS_DIR:-$DATA/uebersicht/widgets}"

missing=()
/usr/bin/python3 -c "import PyQt6.QtWebEngineQuick, PyQt6.QtWebChannel" 2>/dev/null \
  || missing+=(python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel)
for m in qml6-module-qtwebengine qml6-module-qtwebchannel qml6-module-org-kde-layershell qml6-module-qtquick-window; do
  dpkg-query -W -f='${Status}' "$m" 2>/dev/null | grep -q "install ok installed" || missing+=("$m")
done
if ((${#missing[@]})); then
  echo "Missing packages — install them first:" >&2
  echo "  sudo apt install ${missing[*]}" >&2
  exit 1
fi

npm install --no-audit --no-fund
npm run build
mkdir -p "$UNITS" "$DATA/applications" "$WIDGETS"
sed "s|@APP_DIR@|$APP_DIR|g" kde-uebersicht.service.in > "$UNITS/kde-uebersicht.service"
sed "s|@APP_DIR@|$APP_DIR|g" kde-uebersicht.desktop.in > "$DATA/applications/kde-uebersicht.desktop"
[ -e "$WIDGETS/hello.jsx" ] || cp examples/hello.jsx "$WIDGETS/"

systemctl --user daemon-reload
systemctl --user enable kde-uebersicht.service
systemctl --user restart kde-uebersicht.service

echo
echo "Installed kde-uebersicht (service: systemctl --user status kde-uebersicht)"
echo "Widgets: $WIDGETS"
echo "Control: $APP_DIR/kde_uebersicht.py --send reload|refresh|hide|show|status|..."
