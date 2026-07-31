#!/usr/bin/env bash

set -euo pipefail

applications_dir="${HMG_APPLICATIONS_DIR:-${HOME}/Applications}"
app_dir="$applications_dir/Hosts Manager GUI.app"

if [[ "$app_dir" != */"Hosts Manager GUI.app" ]]; then
    echo "Refusing to remove unexpected path: $app_dir" >&2
    exit 1
fi

rm -rf -- "$app_dir"
echo "Hosts Manager GUI application files removed."
echo "Settings, local state, logs, backups, and the system hosts file were not changed."
