#!/usr/bin/env bash

set -euo pipefail

release_base_url="${HMG_RELEASE_BASE_URL:-https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download}"
applications_dir="${HMG_APPLICATIONS_DIR:-${HOME}/Applications}"
app_dir="$applications_dir/Hosts Manager GUI.app"
executable_dir="$app_dir/Contents/MacOS"
archive_name="hosts-manager-gui-macos.tar.gz"
binary_name="hosts-manager-gui"

if [[ "${HMG_SKIP_PLATFORM_CHECK:-0}" != "1" ]]; then
    [[ "$(uname -s)" == "Darwin" ]] || { echo "This installer supports macOS only." >&2; exit 1; }
    case "$(uname -m)" in
        arm64 | aarch64) ;;
        *) echo "The published macOS build supports Apple Silicon only." >&2; exit 1 ;;
    esac
fi

for command_name in cp curl grep install mktemp shasum sleep tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "Required command is missing: $command_name" >&2
        exit 1
    }
done

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

archive_path="${HMG_ARCHIVE_PATH:-}"
checksums_path="${HMG_CHECKSUMS_PATH:-}"
if [[ -n "$archive_path" || -n "$checksums_path" ]]; then
    [[ -f "$archive_path" && -f "$checksums_path" ]] || {
        echo "Both HMG_ARCHIVE_PATH and HMG_CHECKSUMS_PATH must reference files." >&2
        exit 1
    }
    cp -- "$archive_path" "$temporary_dir/$archive_name"
    cp -- "$checksums_path" "$temporary_dir/SHA256SUMS"
else
    curl --fail --location --silent --show-error \
        "$release_base_url/$archive_name" -o "$temporary_dir/$archive_name"
    curl --fail --location --silent --show-error \
        "$release_base_url/SHA256SUMS" -o "$temporary_dir/SHA256SUMS"
fi

(
    cd "$temporary_dir"
    checksum_line="$(grep " $archive_name\$" SHA256SUMS)" || {
        echo "Checksum for $archive_name is missing." >&2
        exit 1
    }
    printf '%s\n' "$checksum_line" | shasum -a 256 --check
    tar -xzf "$archive_name"
)

wait_pid="${HMG_WAIT_PID:-}"
if [[ "$wait_pid" =~ ^[0-9]+$ ]] && [[ "$wait_pid" != "0" ]] && [[ "$wait_pid" != "$$" ]]; then
    for _attempt in {1..600}; do
        kill -0 "$wait_pid" >/dev/null 2>&1 || break
        sleep 0.2
    done
    if kill -0 "$wait_pid" >/dev/null 2>&1; then
        echo "Timed out waiting for the running application to close." >&2
        exit 1
    fi
fi

mkdir -p "$executable_dir"
install -m 755 "$temporary_dir/$binary_name" "$executable_dir/$binary_name"

cat >"$app_dir/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>hosts-manager-gui</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.mystergon.hosts-manager-gui</string>
    <key>CFBundleName</key>
    <string>Hosts Manager GUI</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

echo "Hosts Manager GUI installed to $app_dir"
if [[ "${HMG_NO_LAUNCH:-0}" != "1" ]]; then
    open "$app_dir"
fi
