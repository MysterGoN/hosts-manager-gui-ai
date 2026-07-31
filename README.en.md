# Hosts Manager GUI

English | [Русский](README.md)

> [!WARNING]
> This project, including its source code and documentation, was written entirely
> with AI and has not undergone an independent security audit. Carefully review
> the code, path settings, and backup behavior before use. The application modifies
> the system `hosts` file with administrator privileges — use it at your own risk.

A desktop application for managing a dedicated HMG block in the system `hosts`
file. The interface is built with PySide6/Qt and uses a dark QSS theme.

## Running the application

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
- whether file logging is enabled in development mode.

When the data directory changes, the application offers to copy existing files.
The exact system paths are displayed in the Settings window and determined with
`platformdirs`. For portable or automated runs, the initial directories can be
overridden with `HMG_CONFIG_DIR`, `HMG_DATA_DIR`, and `HMG_LOG_DIR`.

In development mode, logs are written only to stdout by default. In the packaged
application, the console is disabled and JSON logs are written to the rotating
`hmg.log` file.

## Import

The Import window accepts TXT `domain IP` pairs, CSV/TSV with `domain` and
`ip`/`ips` columns, and JSON arrays of objects. Data can be pasted, selected with
the file dialog, or dropped onto the input area. Before applying an import, the
window shows the number of recognized domains and domain/IP relationships.
TXT/CSV/TSV errors include a line number, while JSON syntax errors include a line
and column.

## URL sources

The Sources window can store multiple HTTP/HTTPS addresses, enable them, and
define their application order. Source management is separate from data loading:
use Load from URL in the main window to run an operation. Standard hosts format
(`IP domain`), CSV/TSV, JSON, and `domain IP` format are supported.

- Load New only adds source data.
- Synchronize also removes relationships that disappeared from a source.
- Replace Entirely builds the list only from enabled sources.

Sources are loaded sequentially, and the operation can be canceled between
sources. After loading, the application shows a separate result or error for
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
renamed, or moved, and new domains from imports and URL sources are placed there.

A group switch temporarily excludes all its domains from the resulting `hosts`
file without changing the individual record switches. Groups and disabled records
do not create comments in `hosts`: the managed block contains only enabled domains
from enabled groups.

The main list supports domain/IP search, filters by state, group, and source, and
collapsible groups. Selecting one or more rows enables contextual actions for
editing, moving, bulk enabling/disabling, and deleting records. The footer status
shows how many domains are saved in local state but not yet applied to the system
`hosts` file. Full domain and origin values are available through tooltips and
context-menu copying.

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
automatically generated notes and attaches `hosts-manager-gui-linux.tar.gz`,
`hosts-manager-gui-windows.zip`, `hosts-manager-gui-macos.tar.gz`, and the
`SHA256SUMS` checksum file. A manual workflow run creates Actions artifacts only
and does not publish a Release without a tag.

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
