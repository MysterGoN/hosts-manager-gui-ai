#!/usr/bin/env bash

set -euo pipefail

install_dir="${HMG_INSTALL_DIR:-${HOME}/.local/bin}"
applications_dir="${HMG_APPLICATIONS_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/applications}"
binary_path="$install_dir/hosts-manager-gui"
desktop_path="$applications_dir/hosts-manager-gui.desktop"

rm -f -- "$binary_path" "$desktop_path"
if command -v update-desktop-database >/dev/null 2>&1 && [[ -d "$applications_dir" ]]; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

echo "Hosts Manager GUI application files removed."
echo "Settings, local state, logs, backups, and the system hosts file were not changed."
