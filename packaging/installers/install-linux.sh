#!/usr/bin/env bash

set -euo pipefail

release_base_url="${HMG_RELEASE_BASE_URL:-https://github.com/MysterGoN/hosts-manager-gui/releases/latest/download}"
install_dir="${HMG_INSTALL_DIR:-${HOME}/.local/bin}"
applications_dir="${HMG_APPLICATIONS_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/applications}"
archive_name="hosts-manager-gui-linux.tar.gz"
binary_name="hosts-manager-gui"

if [[ "${HMG_SKIP_PLATFORM_CHECK:-0}" != "1" ]]; then
    [[ "$(uname -s)" == "Linux" ]] || { echo "This installer supports Linux only." >&2; exit 1; }
    case "$(uname -m)" in
        x86_64 | amd64) ;;
        *) echo "The published Linux build supports x86_64 only." >&2; exit 1 ;;
    esac
fi

for command_name in curl grep install mktemp sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "Required command is missing: $command_name" >&2
        exit 1
    }
done

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

curl --fail --location --silent --show-error \
    "$release_base_url/$archive_name" -o "$temporary_dir/$archive_name"
curl --fail --location --silent --show-error \
    "$release_base_url/SHA256SUMS" -o "$temporary_dir/SHA256SUMS"

(
    cd "$temporary_dir"
    checksum_line="$(grep " $archive_name\$" SHA256SUMS)" || {
        echo "Checksum for $archive_name is missing." >&2
        exit 1
    }
    printf '%s\n' "$checksum_line" | sha256sum --check -
    tar -xzf "$archive_name"
)

mkdir -p "$install_dir" "$applications_dir"
install -m 755 "$temporary_dir/$binary_name" "$install_dir/$binary_name"

cat >"$applications_dir/hosts-manager-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Hosts Manager GUI
Comment=Manage entries in the system hosts file
Exec="$install_dir/$binary_name"
TryExec="$install_dir/$binary_name"
Terminal=false
Categories=System;Utility;
EOF
chmod 644 "$applications_dir/hosts-manager-gui.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

echo "Hosts Manager GUI installed to $install_dir/$binary_name"
if ! command -v pkexec >/dev/null 2>&1; then
    echo "Warning: pkexec is required when saving /etc/hosts. Install your distribution's PolicyKit package."
fi

if [[ "${HMG_NO_LAUNCH:-0}" != "1" ]]; then
    ("$install_dir/$binary_name" >/dev/null 2>&1 &)
fi
