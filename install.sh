#!/usr/bin/env bash
# Install SimpleStudy for the current user.
#
#   ./install.sh            install/refresh the package, units and autostart
#   ./install.sh --uninstall  remove what this script installed
#
# The Python package is installed into the user site with pip, so the console
# scripts land in ~/.local/bin. The daemon is enabled as a systemd --user unit.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now simplestudy-daemon.service 2>/dev/null || true
    rm -f "$UNIT_DIR/simplestudy-daemon.service"
    rm -f "$AUTOSTART_DIR/simplestudy.desktop"
    systemctl --user daemon-reload || true
    python3 -m pip uninstall -y simplestudy >/dev/null 2>&1 || true
    echo "SimpleStudy uninstalled."
    exit 0
fi

echo "==> Installing Python package"
python3 -m pip install --user --upgrade "$SRC"

mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR" "$BIN_DIR"

echo "==> Installing systemd user unit"
install -m 0644 "$SRC/packaging/simplestudy-daemon.service" "$UNIT_DIR/simplestudy-daemon.service"

echo "==> Installing desktop autostart entry"
install -m 0644 "$SRC/packaging/simplestudy.desktop" "$AUTOSTART_DIR/simplestudy.desktop"

echo "==> Enabling daemon"
systemctl --user daemon-reload
systemctl --user enable --now simplestudy-daemon.service

echo
echo "Done. Check status with:"
echo "    systemctl --user status simplestudy-daemon.service"
echo "    simplestudy-ctl status"
echo
echo "If ~/.local/bin is not on your PATH, add it."
