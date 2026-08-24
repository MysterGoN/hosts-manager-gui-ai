# Hosts Manager GUI

English | [Русский](README.md)

> [!WARNING]
> This project, including its source code and documentation, was written entirely
> with AI and has not undergone an independent security audit. Carefully review
> the code, path settings, and backup behavior before use. The application modifies
> the system `hosts` file with administrator privileges — use it at your own risk.

> [!WARNING]
> This repository is experimental. Pull requests are not currently reviewed or
> accepted. Bug reports and suggestions are welcome in
> [GitHub Issues](https://github.com/MysterGoN/hosts-manager-gui-ai/issues).

A desktop application for managing a dedicated HMG block in the system `hosts`
file. The interface is built with PySide6/Qt and uses a dark QSS theme.

## Installing a release

Release builds do not require Python, `uv`, or the project dependencies. The
installer scripts from the [latest GitHub Release](https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest)
download the correct archive, verify it against `SHA256SUMS`, install the
application, create a shortcut, and launch it. You can inspect each script in a
text editor before running it.

### Linux x86_64

`glibc 2.39` or newer is required. Run:

```bash
curl -fLO https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download/install-linux.sh
bash install-linux.sh
```

The application is installed in `~/.local/bin` and added to the application
menu. Writing `/etc/hosts` requires `pkexec` from PolicyKit.

To uninstall:

```bash
curl -fLO https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download/uninstall-linux.sh
bash uninstall-linux.sh
```

### Windows x86_64

Open PowerShell and run:

```powershell
$url = "https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download"
Invoke-WebRequest "$url/install-windows.ps1" -OutFile .\install-windows.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
```

The application is installed in the per-user programs directory and added to
the Start menu. SmartScreen may require More info → Run anyway on the first
launch of the unsigned file. Windows displays the standard UAC prompt only when
the application writes the system `hosts` file.

To uninstall:

```powershell
$url = "https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download"
Invoke-WebRequest "$url/uninstall-windows.ps1" -OutFile .\uninstall-windows.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall-windows.ps1
```

### macOS Apple Silicon

On a Mac with Apple Silicon (`arm64`), run the following in Terminal:

```bash
curl -fLO https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download/install-macos.sh
bash install-macos.sh
```

The script creates `~/Applications/Hosts Manager GUI.app`. Gatekeeper may block
the first launch of the unsigned application. If it does, use System Settings →
Privacy & Security → Open Anyway.

To uninstall:

```bash
curl -fLO https://github.com/MysterGoN/hosts-manager-gui-ai/releases/latest/download/uninstall-macos.sh
bash uninstall-macos.sh
```

In an installed build, use the Update button in the window header. The
application checks the latest GitHub Release only when this button is pressed,
shows the release notes, downloads and verifies the installer and application
archive, closes, and launches again after installation. You can run the installer
script again manually if in-app updating is unavailable.

The repository and published GitHub Release must be public for update checks and
installation. The application neither requests nor stores a GitHub token, so a
private repository returns HTTP 404.

When running from source, the button opens the Release page; update the source
tree and dependencies manually. Uninstallers remove the application and
shortcuts but intentionally preserve settings, local state, logs, backups, and
changes to the system `hosts` file. Remove those separately if needed after
checking the paths shown in Settings.

## Running from source

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```bash
make setup
make run
```

The application stores extended domain state separately and automatically saves
record, group, and source changes to local state. The system `hosts` file changes
only after manually reviewing the diff; the application requests administrator
privileges when necessary.

## Settings and directories

The main `settings.json` file is always stored in the standard user configuration
directory for the current platform. The Settings section allows you to select:

- the data directory (`state.json` and `sources.json`);
- the log directory;
- the logging level;
- the maximum size of a single file (`KB`, `MB`, `GB`);
- the number of rotated files;
- the log retention period (`min`, `h`, `d`);
- whether file logging is enabled in development mode;
- the privileged-session lifetime for repeated `hosts` writes (5 minutes by
  default, `0` requests elevation every time, maximum 60 minutes);
- a separate tracing mode that measures significant logical nodes.

When the data directory changes, the application offers to copy existing files.
The exact system paths are displayed in the Settings window and determined with
`platformdirs`. For portable or automated runs, the initial directories can be
overridden with `HMG_CONFIG_DIR`, `HMG_DATA_DIR`, and `HMG_LOG_DIR`.

In development mode, logs are written only to stdout by default. In the packaged
application, the console is disabled and JSON logs are written to the rotating
`hmg.log` file.
Tracing can also be forced with `HMG_TRACE=1`. It adds `trace_started` and
`trace_finished` events to the same JSON output, including the node name,
execution thread, status, and `duration_ms`. Normal mode does not emit these
events.

The application never stores an administrator login or password. When the
privileged-session lifetime is greater than zero, the system elevation prompt
starts a minimal elevated helper. It can only write the system `hosts` file, is
reachable through a one-time local channel, and exits when the timer expires or
the application closes. Changing the lifetime immediately closes the current
session.

## Import

The Import window automatically detects standard hosts format (`IP domain`), TXT
`domain IP` pairs, CSV/TSV with `domain` and `ip`/`ips` columns, and JSON arrays
of objects. A hosts line may contain multiple domains for one IP and an inline
comment. Data can be pasted, selected with the file dialog, or dropped onto the
input area. Before applying an import, choose its target group; the window shows
the detected format and the number of recognized domains and domain/IP
relationships. Every imported domain, including an existing one, is moved to the
selected group. TXT/CSV/TSV/hosts errors include a line number, while JSON syntax
errors include a line and column.

Domain names containing Unicode characters, including Cyrillic (`пример.рф`),
are accepted in manual entry, imports, and URL sources. They are shown as
readable Unicode in the interface and stored in canonical Punycode in local
state and the system `hosts` file. A Unicode name and its matching Punycode are
treated as the same domain, and search supports both forms.

## URL sources

The Sources window can store multiple HTTP/HTTPS addresses, enable them, and
define their application order. Source management is separate from data loading:
use Load from URL in the main window to run an operation. Standard hosts format
(`IP domain`), CSV/TSV, JSON, and `domain IP` format are supported.

- Load New only adds source data.
- Synchronize also removes relationships that disappeared from a source.
- Replace Entirely builds the list only from enabled sources.

Sources are loaded sequentially on a background worker, so a timeout or an
unavailable endpoint does not block the interface. The operation can be canceled
between sources; the current request first finishes with a response or timeout.
After loading, the application shows a separate result or error for
every URL and an overall change preview. Local state changes only after explicit
confirmation. A failed source is skipped without removing its previous
relationships; Replace Entirely is blocked if any source fails.

Source-list changes are applied with Save. Cancel closes the window without
changing the configured sources.
The origin of every domain/IP pair is tracked separately in `sources.json`.
Manual records and their Enabled state are preserved during synchronization.
Every URL-source action updates only the application's internal state. The system
`hosts` file is never changed automatically: inspect the diff with Preview and
write it separately with Save to hosts. URL sources are not synchronized
automatically at application startup.

## Groups

Domains can be visually divided into groups and moved individually or in a
multiple selection. `Default` is always the first group: it cannot be deleted,
renamed, or moved. A target group is selected for a manual import, while new
domains from URL sources are placed in `Default`.

A group switch temporarily excludes all its domains from the resulting `hosts`
file without changing the individual record switches. The managed block contains
only enabled domains from enabled groups. Every non-empty group is marked with a
`# Group: ...` comment, and adjacent groups are separated by a blank line. Within
each group, records are sorted first by the numeric IP value (IPv4 before IPv6),
then, for matching IPs, by domain labels from right to left: top-level domain,
second-level domain, and so on.

The main list supports domain/IP search, filters by state, group, and source, and
collapsible groups. A Unicode domain is shown in readable form, with its Punycode
available in the tooltip and context menu. Selecting one or more rows enables
contextual actions for editing, moving, bulk enabling/disabling, and deleting
records. The footer status shows how many domains are saved in local state but
not yet applied to the system `hosts` file. Full domain and origin values are
available through tooltips and context-menu copying.

Search starts after a short typing pause so the table is not rebuilt for every
intermediate character. In tracing mode, `search.filter_entries`,
`search.apply_visibility`, and `search.refresh_and_render_table` separate
filtering, visibility updates for existing rows, and a full Qt table rebuild.

The `hosts` preview identifies added, removed, modified, and service lines
separately. `Generated at` changes are excluded from user-facing statistics.
Long unchanged sections are collapsed, while Previous and Next buttons navigate
between changes.

Successful non-critical operations appear in the main window's status message
instead of requiring a modal dialog to be dismissed. Dangerous confirmations
use explicit actions such as Delete records or Close without applying rather
than ambiguous Yes/No buttons.

## Keyboard and accessibility

Primary keyboard shortcuts:

- `Ctrl+N` — add a record;
- `Ctrl+I` — open Import;
- `Ctrl+F` — focus Search;
- `Ctrl+P` — open Preview;
- `Ctrl+S` — review and save to `hosts`;
- `Ctrl+,` — open Settings;
- `F1` — open the built-in Help;
- `F5` — reload data from disk;
- `Delete` and `Escape` in the table — delete selected records or clear selection;
- `F7` / `Shift+F7` in the diff — navigate to the next or previous change.

Interactive controls have a prominent keyboard focus outline. State is not
represented by color alone: the diff uses `+`, `−`, `≈`, and `•` markers, while
checkboxes retain their native check mark. Compound table controls have
contextual accessible names for screen readers.

The main window's Help button explains the local state/`hosts` model, groups,
imports, URL sources, and the complete shortcut list. Help is bundled with the
application and works offline.

Run `make check-ui-scaling` to verify the main window and dialogs at 100%, 125%,
150%, and 200% scaling. The same check runs in the GitHub Actions build matrix on
Linux, Windows, and macOS.

## Checks

```bash
make check
```

This runs Ruff, mypy, a known-vulnerability audit with `uv audit`, and the test
suite. The same checks are also available as pre-commit hooks:

```bash
make install-hooks
make pre-commit
```

The installed `commit-msg` hook also validates commit-message formatting.

## Versioning and releases

The project is in active development and uses zero-major `0.x.y` versions.
Until the API is explicitly declared stable, automated releases never increment
the major version to `1.0.0`: every new `0.x.0` minor release may contain
backward-incompatible changes.

Commitizen derives the version and tag from Conventional Commits:

- `fix`, `perf`, and `refactor` increment patch: `0.1.0 → 0.1.1`;
- `feat` increments minor: `0.1.x → 0.2.0`;
- `!` after the type/scope or a `BREAKING CHANGE:` footer marks an incompatible
  change and, in zero-major mode, also increments minor instead of major;
- other types (`docs`, `test`, `build`, `ci`, `chore`) do not trigger a version
  increment by themselves.

Create a valid commit interactively and preview the next release without changing
files with:

```bash
make commit
make release-preview
```

Run a release only from a clean working tree:

```bash
make release
git push --follow-tags
```

`make release` runs the checks, updates the version in `pyproject.toml` and
`uv.lock` together, updates `CHANGELOG.md`, creates a release commit, and creates
an annotated `vX.Y.Z` tag. It does not push anything to the remote repository;
explicitly pushing the tag triggers the GitHub Actions build matrix. After all
three platform builds succeed, the workflow creates a GitHub Release with
automatically generated notes and attaches application archives, installers,
uninstallers, and the `SHA256SUMS` checksum file. A manual workflow run creates
Actions artifacts only and does not publish a Release without a tag.

## Building the application

PyInstaller creates a single executable for the current operating system:

```bash
make build-app
```

The result is written to `dist/hosts-manager-gui` (`.exe` on Windows).
PyInstaller does not support cross-compilation, so
`.github/workflows/build-app.yml` builds Linux, Windows, and macOS artifacts in
parallel on native GitHub runners. The workflow can be started manually or by
publishing a `v*` tag.

The builds are not currently signed with a developer certificate, so macOS
Gatekeeper and Windows SmartScreen may display warnings until code signing is
configured.

### Local builds with Docker

Docker Buildx can build a Linux one-file binary locally, independently of the
host operating system:

```bash
# Linux amd64
make build-linux-docker

# Linux arm64
make build-linux-arm64-docker

# Both architectures
make build-linux-docker-all
```

Artifacts are written to `dist/docker/linux-amd64` and
`dist/docker/linux-arm64`.

The amd64 artifact targets Debian 12 / glibc 2.36 or newer. ARM64 is built on
Debian 13 and targets glibc 2.41 or newer because current ARM64 PySide6 wheels
require at least glibc 2.39.

Docker uses the Linux kernel and cannot create native macOS or Windows
applications. Use `make build-app` on the corresponding operating system for
those targets. Wine-based Windows builds are intentionally not used because
PyInstaller does not officially support that cross-compilation path and the
result would not be considered reproducible.
