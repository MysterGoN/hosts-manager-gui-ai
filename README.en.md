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

## URL sources

The URL Sources window can store multiple HTTP/HTTPS addresses and define their
application order. Standard hosts format (`IP domain`), CSV/TSV, JSON, and
`domain IP` format are supported.

- Load New only adds source data.
- Synchronize also removes relationships that disappeared from a source.
- Replace Entirely builds the list only from enabled sources.

Source-list changes are applied with Save or one of the loading actions. Cancel
closes the window without changing the configured sources.
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
