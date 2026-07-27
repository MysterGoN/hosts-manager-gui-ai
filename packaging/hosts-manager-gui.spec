# -*- mode: python ; coding: utf-8 -*-

import sys
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
hidden_imports = collect_submodules("truststore")
excluded_imports = []
if sys.platform != "darwin":
    hidden_imports = [name for name in hidden_imports if name != "truststore._macos"]
    excluded_imports.append("truststore._macos")
if sys.platform != "win32":
    hidden_imports = [name for name in hidden_imports if name != "truststore._windows"]
    excluded_imports.append("truststore._windows")

extra_binaries = []
if sys.platform.startswith("linux"):
    system_library_dir = Path("/usr/lib") / sysconfig.get_config_var("MULTIARCH")
    for library_name in (
        "libEGL.so.1",
        "libGL.so.1",
        "libGLX.so.0",
        "libGLdispatch.so.0",
        "libXi.so.6",
        "libxcb.so.1",
    ):
        library_path = system_library_dir / library_name
        if library_path.exists():
            extra_binaries.append((str(library_path), "."))

analysis = Analysis(
    [str(project_root / "src" / "hmg" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=extra_binaries,
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_imports,
    noarchive=False,
    optimize=0,
)

# The GTK theme plugin is optional and otherwise pulls the whole GTK runtime into
# the one-file Linux build. The TIFF plugin is also unused by this application and
# depends on libtiff.so.5 in the amd64 wheel. Qt's xcb platform plugin remains.
excluded_binary_suffixes = (
    "PySide6/Qt/plugins/imageformats/libqtiff.so",
    "PySide6/Qt/plugins/platformthemes/libqgtk3.so",
)
analysis.binaries = [
    entry
    for entry in analysis.binaries
    if not entry[0].replace("\\", "/").endswith(excluded_binary_suffixes)
]

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="hosts-manager-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
