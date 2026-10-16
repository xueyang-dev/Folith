#!/usr/bin/env bash
# Build the Folith Qt desktop bundle (PySide6 / Qt Widgets) for macOS.
#
# Separate output directories keep the Qt candidate and the retired Streamlit
# baseline distinguishable:
#   build/qt-desktop/   intermediates, build record, OCR payload, min-OS report
#   dist/qt-desktop/    Folith.app
#
# The declared minimum macOS version is measured, not guessed: the native
# closure (interpreter + PySide6/Qt + bundled OCR) is scanned before bundling and
# the artifact is re-scanned afterwards.
#
# Usage:
#   scripts/build_qt_desktop.sh [--clean] [--no-ocr]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${FOLITH_BUILD_PYTHON:-$ROOT/venv/bin/python}"
OUT_DIR="${FOLITH_QT_OUT_DIR:-$ROOT/build/qt-desktop}"
DIST_DIR="${FOLITH_QT_DIST_DIR:-$ROOT/dist/qt-desktop}"
BUILD_INFO="$OUT_DIR/desktop_build_info.json"
WITH_OCR=1
for arg in "$@"; do
  case "$arg" in
    --clean)
      rm -rf "$OUT_DIR/work" "$OUT_DIR/Folith" "$DIST_DIR"
      ;;
    --no-ocr) WITH_OCR=0 ;;
  esac
done

mkdir -p "$OUT_DIR"

case "$(uname -s)" in
  Darwin) PLATFORM="macos" ;;
  Linux) PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *) PLATFORM="unknown" ;;
esac
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"

echo "=== build record (shell=qt) ==="
"$PYTHON" "$ROOT/scripts/write_desktop_build_info.py" \
  --out "$BUILD_INFO" --backend pyinstaller --platform "$PLATFORM" --arch "$ARCH" \
  --shell qt

if [[ "$PLATFORM" == "macos" ]]; then
  # Bundled OCR payload: the eng/chi_sim/ara Beta baseline.
  if [[ "$WITH_OCR" == "1" ]]; then
    "$PYTHON" "$ROOT/scripts/stage_tesseract_payload.py" --force \
      --out "$OUT_DIR/tesseract" > "$OUT_DIR/tesseract-payload.json"
  else
    # 打包会带上目录里现成的 OCR 载荷：上一次构建留下的也算，所以要先删掉。
    rm -rf "$OUT_DIR/tesseract" "$OUT_DIR/tesseract-payload.json"
  fi
  # App identity for Dock / Finder / app switcher.
  "$PYTHON" "$ROOT/scripts/build_app_icon.py" --out "$OUT_DIR/Folith.icns" \
    > "$OUT_DIR/app-icon.json"
fi

echo "=== measure the native closure (declared support boundary) ==="
SCAN_ARGS=(--qt)
if [[ -d "$OUT_DIR/tesseract" ]]; then
  SCAN_ARGS+=(--scan "$OUT_DIR/tesseract")
fi
"$PYTHON" "$ROOT/scripts/qt_native_min_os.py" \
  "${SCAN_ARGS[@]}" --out "$OUT_DIR/native_min_os_inputs.json" >/dev/null
MIN_OS="$("$PYTHON" -c "
import json,sys
data=json.load(open('$OUT_DIR/native_min_os_inputs.json'))
print(data.get('max_minos') or '0.0')")"
echo "declared LSMinimumSystemVersion=$MIN_OS"

# Record the measured support boundary in the build record, so diagnostics, the
# DMG record and the release notes all quote the same measured fact.
"$PYTHON" - "$BUILD_INFO" "$MIN_OS" <<'PY'
import json, sys
path, min_os = sys.argv[1], sys.argv[2]
record = json.load(open(path))
record["minimum_system_version"] = min_os
record["minimum_system_version_source"] = (
    "measured: scripts/qt_native_min_os.py (Mach-O LC_BUILD_VERSION minos)")
with open(path, "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

echo "=== PyInstaller ==="
# Keep PyInstaller's binary cache inside the build tree: the default
# (~/Library/Application Support/pyinstaller) is outside the project, so a clean
# build would depend on and write to a per-user location.
export PYINSTALLER_CONFIG_DIR="$OUT_DIR/pyinstaller-cache"
FOLITH_BUILD_INFO="$BUILD_INFO" \
FOLITH_QT_OUT_DIR="$OUT_DIR" \
FOLITH_QT_MIN_OS="$MIN_OS" \
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --distpath "$DIST_DIR" \
  --workpath "$OUT_DIR/work" \
  "$ROOT/packaging/folith-qt.spec"

echo "=== verify the artifact against the declared minimum ==="
APP="$DIST_DIR/Folith.app"
if [[ -d "$APP" ]]; then
  set +e
  # Verify both claims about the artifact: the declared minimum OS and that
  # every bundled native file can actually run on Apple Silicon.
  "$PYTHON" "$ROOT/scripts/qt_native_min_os.py" --bundle "$APP" \
    --expect "$MIN_OS" --require-arm64 \
    --out "$OUT_DIR/native_min_os_bundle.json"
  VERIFY_RC=$?
  set -e
  if [[ "$VERIFY_RC" != "0" ]]; then
    echo "FATAL: the bundle contains a native component newer than the declared" \
         "LSMinimumSystemVersion=$MIN_OS (see native_min_os_bundle.json)" >&2
    exit "$VERIFY_RC"
  fi
  PLIST_MIN="$("$PYTHON" -c "
import plistlib,sys
with open('$APP/Contents/Info.plist','rb') as handle:
    print(plistlib.load(handle).get('LSMinimumSystemVersion',''))")"
  echo "Info.plist LSMinimumSystemVersion=$PLIST_MIN"
  [[ "$PLIST_MIN" == "$MIN_OS" ]] || {
    echo "FATAL: Info.plist declares $PLIST_MIN but the measured boundary is $MIN_OS" >&2
    exit 2
  }
fi

# Only now drop the duplicate COLLECT directory.  This is cosmetic, so it must
# never decide the build result — and it runs *after* verification so a hiccup
# here cannot skip the gate.  (macOS can re-create a .DS_Store inside the tree
# while it is being removed, which makes `rm -rf` report "Directory not empty".)
if [[ "$PLATFORM" == "macos" && -d "$DIST_DIR/Folith" && -d "$DIST_DIR/Folith.app" ]]; then
  rm -rf "$DIST_DIR/Folith" 2>/dev/null || true
  [[ -d "$DIST_DIR/Folith" ]] && rm -rf "$DIST_DIR/Folith" 2>/dev/null || true
  if [[ -d "$DIST_DIR/Folith" ]]; then
    echo "note: duplicate COLLECT directory left at $DIST_DIR/Folith (harmless)"
  fi
fi

echo
echo "=== artifact ==="
du -sh "$DIST_DIR"/* 2>/dev/null || true
echo
echo "=== build record ==="
cat "$BUILD_INFO"
