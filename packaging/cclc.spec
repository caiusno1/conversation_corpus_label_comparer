# PyInstaller spec — builds a self-contained one-folder distribution of the app.
#
# One-folder (COLLECT) mode is used deliberately: the Qt shared libraries stay as
# separate, replaceable files in the output folder, which keeps the bundle
# compliant with PySide6/Qt's LGPLv3 terms (see README "Licensing"). Build with:
#
#     pyinstaller packaging/cclc.spec
#
# The result is dist/<APP_NAME>/ containing <APP_NAME>.exe and all dependencies
# (a private Python interpreter + Qt), which the Inno Setup script then packages
# into an installer.

import glob
import os
import sys

APP_NAME = "ELAN Corpus Label Comparer"

# SPECPATH is the directory containing this spec file (packaging/).
repo_root = os.path.dirname(SPECPATH)
src_dir = os.path.join(repo_root, "src")
entry = os.path.join(SPECPATH, "run_cclc.py")

# Make the source package importable for collect_submodules during analysis.
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

# Ship the example .eaf files alongside the app.
datas = [(p, "samples") for p in glob.glob(os.path.join(repo_root, "samples", "*.eaf"))]

hiddenimports = collect_submodules("cclc")

# Trim large Qt add-ons and dev/test libraries the app never uses. If a Qt
# plugin turns out to be required at runtime, remove the matching entry here.
excludes = [
    "tkinter",
    "pytest",
    "matplotlib",
    "numpy",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSensors",
    "PySide6.QtBluetooth",
]

icon_path = os.path.join(SPECPATH, "cclc.ico")
icon = icon_path if os.path.exists(icon_path) else None

a = Analysis(
    [entry],
    pathex=[src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
