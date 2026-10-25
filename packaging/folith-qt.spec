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
import sys
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
MIN_OS = os.environ.get("FOLITH_QT_MIN_OS", "15.7.5")

# --------------------------------------------------------- Windows identity
# The macOS BUNDLE() section (icon + Info.plist) is a no-op on Windows, so a
# Windows .exe needs its own icon and version resource or Explorer/Task Manager
# show PyInstaller's placeholder and "unknown" version.  Both are optional:
# when their inputs are absent the build still succeeds, just without identity.

def _version_tuple(value: str) -> tuple:
    parts = []
    for token in str(value).split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


_WIN_ICON = OUT_DIR / "Folith.ico"
_WIN_ICON_ARG = str(_WIN_ICON) if _WIN_ICON.is_file() else None

_WIN_VERSION = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    _ver = _version_tuple(APP_VERSION)
    _WIN_VERSION = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_ver, prodvers=_ver, mask=0x3F, flags=0x0,
            OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("080404B0", [
                StringStruct("CompanyName", "Folith"),
                StringStruct("FileDescription", "Folith · 译页"),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("InternalName", "Folith"),
                StringStruct("OriginalFilename", "Folith.exe"),
                StringStruct("ProductName", "Folith"),
                StringStruct("ProductVersion", APP_VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0804, 0x04B0])]),
        ])

# ---------------------------------------------------------------- data files
datas = []
binaries = []
if BUILD_INFO.exists():
    datas.append((str(BUILD_INFO), "."))
# Brand assets, locale catalogs and other package resources.
datas += collect_data_files("folith", includes=["resources/**"])
datas += [(str(ROOT / "folith/desktop/install-update.ps1"), "folith/desktop")]
_update_key = os.environ.get("FOLITH_UPDATE_PUBLIC_KEY", "").strip()
if _update_key:
    import base64
    if len(base64.b64decode(_update_key, validate=True)) != 32:
        raise ValueError("FOLITH_UPDATE_PUBLIC_KEY must be a base64 Ed25519 public key")
    _update_key_path = OUT_DIR / "update-public-key.txt"
    _update_key_path.write_text(_update_key, encoding="ascii")
    datas.append((str(_update_key_path), "folith/resources"))

# ------------------------------------------------------ bundled OCR payload
# eng/chi_sim/ara plus the engine, staged by scripts/stage_tesseract_payload.py
# into one platform-neutral layout:
#   bin/             the engine (on Windows also its sibling DLL closure)
#   lib/             the macOS .dylib closure (absent on Windows)
#   share/tessdata/  the language packs and the output-mode configs
TESSERACT_DIR = OUT_DIR / "tesseract"
if TESSERACT_DIR.is_dir():
    _payload_bin = TESSERACT_DIR / "bin"
    if _payload_bin.is_dir():
        for _entry in sorted(_payload_bin.iterdir()):
            if _entry.is_file():
                binaries.append((str(_entry), "tesseract/bin"))
    _payload_lib = TESSERACT_DIR / "lib"
    if _payload_lib.is_dir():
        for _entry in sorted(_payload_lib.iterdir()):
            if _entry.is_file():
                binaries.append((str(_entry), "tesseract/lib"))
    _payload_tessdata = TESSERACT_DIR / "share" / "tessdata"
    if _payload_tessdata.is_dir():
        datas.append((str(_payload_tessdata), "tesseract/share/tessdata"))
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
    # PyMuPDF 2.x imports pymupdf.layout and loads its ONNX model on import.
    # Folith only uses the core PDF APIs, and the model resources are not
    # bundled, so drop the optional layout analyzer instead.
    "pymupdf.layout", "pymupdf4llm",
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
    icon=_WIN_ICON_ARG,
    version=_WIN_VERSION,
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

# 文档类型声明：没有它，Finder /「打开方式」不会把 PDF/DOCX 交给本应用，
# `QFileOpenEvent` 也就永远不会到达（双击只会拉起应用但不带文件）。
# `LSHandlerRank = Alternate`：只做候选处理程序，**不抢系统默认**——PDF/DOCX 的
# 默认应用应该是用户的阅读器/编辑器，不是本应用。
# 与 `adapter.SUPPORTED_SUFFIXES`（= 共享应用层的 `SOURCE_SUFFIXES`）逐项对应；
# `tests/qt_macos_file_open_test.py` 会核对两边不漂移。
# 只声明共享导入契约中已经实现的类型。
DOCUMENT_TYPES = [
    {"CFBundleTypeName": "PDF document",
     "CFBundleTypeRole": "Viewer",
     "LSHandlerRank": "Alternate",
     "LSItemContentTypes": ["com.adobe.pdf"]},
    {"CFBundleTypeName": "Word document",
     "CFBundleTypeRole": "Editor",
     "LSHandlerRank": "Alternate",
     "LSItemContentTypes": ["org.openxmlformats.wordprocessingml.document"]},
    {"CFBundleTypeName": "EPUB book", "CFBundleTypeRole": "Viewer",
     "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["epub"],
     "LSItemContentTypes": ["org.idpf.epub-container"]},
    {"CFBundleTypeName": "Plain text", "CFBundleTypeRole": "Viewer",
     "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["txt"],
     "LSItemContentTypes": ["public.plain-text"]},
    {"CFBundleTypeName": "Markdown document", "CFBundleTypeRole": "Viewer",
     "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": ["md", "markdown"],
     "LSItemContentTypes": ["net.daringfireball.markdown"]},
]

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
    "CFBundleDocumentTypes": DOCUMENT_TYPES,
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
