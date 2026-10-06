# -*- mode: python ; coding: utf-8 -*-
"""Folith Qt desktop bundle — PyInstaller spec (Qt Widgets candidate).

Differences from the retired Streamlit shell's spec (``folith-desktop.spec``),
which is deliberately kept as the old baseline:

* the entry point is ``qt_client.py`` (Qt Widgets), not ``gui.py``;
* **Streamlit and pywebview are excluded**, not collected: the Qt candidate must
  run without either, and the frozen bundle is where that claim is proven;
* output goes to its own directories (``dist/qt-desktop``, ``build/qt-desktop``);
* ``LSMinimumSystemVersion`` is not a guess: the build measures the native
  closure first (``scripts/qt_native_min_os.py``) and passes the result in
  ``FOLITH_QT_MIN_OS``; PySide6 6.11's Qt libraries are built with
  ``minos 15.0``, so claiming 13.0 would be false.
"""
import json
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent if (SPEC_DIR.parent / "qt_client.py").exists() else SPEC_DIR
if not (ROOT / "qt_client.py").exists():  # pragma: no cover - defensive
    raise SystemExit(f"cannot locate repository root from spec path {SPEC_DIR}")

OUT_DIR = Path(os.environ.get("FOLITH_QT_OUT_DIR", str(ROOT / "build" / "qt-desktop")))
BUILD_INFO = Path(os.environ.get(
    "FOLITH_BUILD_INFO", str(OUT_DIR / "desktop_build_info.json")))

APP_VERSION = "0.0.0"
BUILD_ID = "unknown"
if BUILD_INFO.exists():
    _record = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    APP_VERSION = _record.get("app_version", APP_VERSION)
    BUILD_ID = _record.get("build_id", BUILD_ID)

# The declared support boundary, measured before bundling (never guessed).
MIN_OS = os.environ.get("FOLITH_QT_MIN_OS", "15.0")

# ---------------------------------------------------------------- data files
datas = []
binaries = []
if BUILD_INFO.exists():
    datas.append((str(BUILD_INFO), "."))
# Brand assets, locale catalogs and other package resources.
datas += collect_data_files("folith", includes=["resources/**"])

# ------------------------------------------------------ bundled OCR payload
# Beta baseline eng/chi_sim/ara, staged by scripts/stage_tesseract_payload.py.
TESSERACT_DIR = OUT_DIR / "tesseract"
if TESSERACT_DIR.is_dir():
    binaries.append((str(TESSERACT_DIR / "bin" / "tesseract"), "tesseract/bin"))
    for _lib in sorted((TESSERACT_DIR / "lib").glob("*.dylib")):
        binaries.append((str(_lib), "tesseract/lib"))
    datas.append((str(TESSERACT_DIR / "share" / "tessdata"),
                  "tesseract/share/tessdata"))
    for _name in ("tesseract_payload.json", "tessdata_provenance.json"):
        _manifest = TESSERACT_DIR / _name
        if _manifest.exists():
            datas.append((str(_manifest), "tesseract"))

# ------------------------------------------------------------ hidden imports
hiddenimports = [
    "core",
    "folith.qt.app",
    "folith.qt.adapter",
    "folith.qt.window",
    "folith.qt.views",
    "folith.qt.dialogs",
    "folith.qt.workers",
    "folith.qt.bootstrap",
    "folith.qt.branding",
    # Loaded dynamically by the core's optional paths.
    "pymupdf",
    "fitz",
    "docx",
    "openpyxl",
    "xlsxwriter",
    "PIL",
    "defusedxml",
    "google.genai",
    "openai",
    "httpx",
]
hiddenimports += collect_submodules("folith")

# The Qt candidate must not drag the old shell's runtime in.  Excluding these
# both shrinks the bundle and makes "no Streamlit needed" a build-time fact.
excludes = [
    "streamlit", "webview", "pywebview", "clr_loader", "pythonnet",
    "tkinter", "matplotlib", "IPython", "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtBluetooth",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtRemoteObjects",
    "PySide6.QtPositioning", "PySide6.QtNfc", "PySide6.QtSpatialAudio",
    "pytest", "unittest", "pydoc_data", "test", "tests",
    "paddle", "paddleocr", "paddlex",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "qt_client.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Folith",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Folith",
)

info_plist = {
    "CFBundleName": "Folith",
    "CFBundleDisplayName": "Folith",
    "CFBundleExecutable": "Folith",
    "CFBundleIdentifier": "dev.folith.qt",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": APP_VERSION,
    "CFBundleVersion": BUILD_ID,
    # Measured from the bundled native closure; see the module docstring.
    "LSMinimumSystemVersion": MIN_OS,
    "LSApplicationCategoryType": "public.app-category.productivity",
    "NSHighResolutionCapable": True,
    "NSHumanReadableCopyright": "Folith · 译页",
    "NSRequiresAquaSystemAppearance": False,
}

_ICON = OUT_DIR / "Folith.icns"
_ICON_ARG = str(_ICON) if _ICON.is_file() else None

app = BUNDLE(
    coll,
    name="Folith.app",
    icon=_ICON_ARG,
    bundle_identifier="dev.folith.qt",
    info_plist=info_plist,
)
