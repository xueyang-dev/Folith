# -*- mode: python ; coding: utf-8 -*-
"""Folith desktop bundle — PyInstaller spec (roadmap §3.1, §4, §5, §7.3).

Design notes:
* One executable, two roles: no arguments starts the desktop launcher; `--serve`
  starts the internal Streamlit service child.  The frozen build never uses the
  interpreter-based `python -m streamlit` path.
* `app.py` is shipped as a *data file* because Streamlit executes the main
  script by path; `hiddenimports=["app"]` makes PyInstaller analyse that file's
  imports so its dependencies are collected without importing it.
* Only the resources the product actually needs are collected (Streamlit static
  assets + metadata, `folith/resources`, pywebview JS assets, the build record).
  Missing resources are added one at a time from observed failures rather than
  by collecting the whole environment.
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent if (SPEC_DIR.parent / "gui.py").exists() else SPEC_DIR
if not (ROOT / "gui.py").exists():  # pragma: no cover - defensive
    raise SystemExit(f"cannot locate repository root from spec path {SPEC_DIR}")

# The build script passes the record it just wrote (FOLITH_BUILD_INFO); the
# workspace default keeps plain `pyinstaller` invocations working.  Reading the
# record from the build output directory matters for release/upgrade builds that
# override the version and build id.
BUILD_INFO = Path(os.environ.get(
    "FOLITH_BUILD_INFO", str(ROOT / "build" / "desktop" / "desktop_build_info.json")))
APP_VERSION = "0.0.0"
BUILD_ID = "unknown"
if BUILD_INFO.exists():
    import json

    _record = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    APP_VERSION = _record.get("app_version", APP_VERSION)
    BUILD_ID = _record.get("build_id", BUILD_ID)

# ---------------------------------------------------------------- data files
datas = [
    (str(ROOT / "app.py"), "."),
]
binaries = []
if BUILD_INFO.exists():
    datas.append((str(BUILD_INFO), "."))
# UI locale catalogs, brand assets and other package resources.
datas += collect_data_files("folith", includes=["resources/**"])
# Streamlit's compiled frontend and its metadata (importlib.metadata lookups).
datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")
# pywebview JS assets used by the cocoa backend (the bundled hook only covers
# the Windows runtime).
_webview_data = collect_data_files("webview")
datas += [
    (src, dest) for src, dest in _webview_data
    if not src.lower().endswith(".dll") and not src.lower().endswith(".jar")
]
# Our own package metadata is intentionally NOT collected: the repository venv
# carries a stale editable `foliothread 0.4.0` dist-info, so the frozen bundle
# reports its version from the generated build record instead.

# ------------------------------------------------------ bundled OCR payload
# Roadmap §3.4 (D2): ship Tesseract + the eng/chi_sim/ara baseline so OCR works
# without Homebrew/PATH tooling.  Staged by scripts/stage_tesseract_payload.py.
TESSERACT_DIR = ROOT / "build" / "desktop" / "tesseract"
if TESSERACT_DIR.is_dir():
    binaries += [(str(TESSERACT_DIR / "bin" / "tesseract"), "tesseract/bin")]
    for _lib in sorted((TESSERACT_DIR / "lib").glob("*.dylib")):
        binaries.append((str(_lib), "tesseract/lib"))
    datas.append((str(TESSERACT_DIR / "share" / "tessdata"), "tesseract/share/tessdata"))
    _payload_manifest = TESSERACT_DIR / "tesseract_payload.json"
    if _payload_manifest.exists():
        datas.append((str(_payload_manifest), "tesseract"))
    # The licence / data-version record must ship with the artifact (roadmap §3.4).
    _provenance = TESSERACT_DIR / "tessdata_provenance.json"
    if _provenance.exists():
        datas.append((str(_provenance), "tesseract"))

# ------------------------------------------------------------ hidden imports
hiddenimports = [
    "app",  # analyse app.py's imports without executing it
    "core",
    "streamlit.web.server.server",
    "streamlit.web.server.starlette.starlette_server",
    "streamlit.web.bootstrap",
    "webview.platforms.cocoa",
    # uvicorn loads protocol/loop/lifespan implementations by dotted string.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # anyio picks its backend dynamically.
    "anyio._backends._asyncio",
]
hiddenimports += collect_submodules("folith")
hiddenimports += collect_submodules("websockets")
# Streamlit injects `import streamlit.runtime.scriptrunner.magic_funcs` into the
# app script's AST at runtime, so static analysis cannot see it.  Observed in the
# first frozen run as ModuleNotFoundError on the app script's first line.
hiddenimports += collect_submodules("streamlit.runtime.scriptrunner")
hiddenimports += collect_submodules("streamlit.runtime.scriptrunner_utils")
hiddenimports += collect_submodules("objc")
hiddenimports += collect_submodules("PyObjCTools")
hiddenimports += ["webview.platforms.cocoa", "AppKit", "Foundation", "WebKit",
                  "UniformTypeIdentifiers", "Quartz"]

excludes = [
    "tkinter", "matplotlib", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest", "unittest", "pydoc_data", "test", "tests",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,  # includes the bundled OCR payload when staged
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
    "CFBundleIdentifier": "dev.folith.desktop",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": APP_VERSION,
    "CFBundleVersion": BUILD_ID,
    "LSMinimumSystemVersion": "13.0",
    "LSApplicationCategoryType": "public.app-category.productivity",
    "NSHighResolutionCapable": True,
    # The app talks to its own Streamlit service on 127.0.0.1 only.
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSHumanReadableCopyright": "Folith · 译页",
}

# App identity (roadmap §5 D2): without an icon macOS shows PyInstaller's generic
# placeholder in the Dock, Finder and the app switcher.
_ICON = ROOT / "build" / "desktop" / "Folith.icns"
_ICON_ARG = str(_ICON) if _ICON.is_file() else None

app = BUNDLE(
    coll,
    name="Folith.app",
    icon=_ICON_ARG,
    bundle_identifier="dev.folith.desktop",
    info_plist=info_plist,
)
