#!/usr/bin/env bash
# Build the Folith desktop bundle for the current platform (D0 architecture spike).
#
# Roadmap §3.1/§7.3: frozen build with an explicit internal `--serve` mode and a
# minimal, declared resource set.  The build record written here is what the
# running app reports in diagnostics and in the instance handshake.
#
# Usage:
#   scripts/build_desktop.sh [--clean]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${FOLITH_BUILD_PYTHON:-$ROOT/venv/bin/python}"
OUT_DIR="${FOLITH_OUT_DIR:-$ROOT/build/desktop}"
DIST_DIR="${FOLITH_DIST_DIR:-$ROOT/dist/desktop}"
BUILD_INFO="$OUT_DIR/desktop_build_info.json"

# Release engineering / upgrade verification may pin the version, the build id
# and the output directory (FOLITH_APP_VERSION / FOLITH_BUILD_ID / FOLITH_DIST_DIR).
BUILD_INFO_ARGS=()
[[ -n "${FOLITH_APP_VERSION:-}" ]] && BUILD_INFO_ARGS+=(--app-version "$FOLITH_APP_VERSION")
[[ -n "${FOLITH_BUILD_ID:-}" ]] && BUILD_INFO_ARGS+=(--build-id "$FOLITH_BUILD_ID")

if [[ "${1:-}" == "--clean" ]]; then
  rm -rf "$ROOT/build/desktop/work" "$ROOT/build/desktop/Folith" "$DIST_DIR"
fi

mkdir -p "$OUT_DIR"

case "$(uname -s)" in
  Darwin) PLATFORM="macos" ;;
  Linux) PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *) PLATFORM="unknown" ;;
esac
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"

"$PYTHON" "$ROOT/scripts/write_desktop_build_info.py" \
  --out "$BUILD_INFO" --backend pyinstaller --platform "$PLATFORM" --arch "$ARCH" \
  ${BUILD_INFO_ARGS[@]+"${BUILD_INFO_ARGS[@]}"}

# Bundled OCR payload (roadmap §3.4 D2).  Staged every build so the data version
# and licence record stay in the build record.
if [[ "$PLATFORM" == "macos" ]]; then
  "$PYTHON" "$ROOT/scripts/stage_tesseract_payload.py" --force \
    --out "$OUT_DIR/tesseract" > "$OUT_DIR/tesseract-payload.json"
  # App identity: build the .icns from the tracked brand artwork.
  "$PYTHON" "$ROOT/scripts/build_app_icon.py" --out "$OUT_DIR/Folith.icns" \
    > "$OUT_DIR/app-icon.json"
fi

FOLITH_BUILD_INFO="$BUILD_INFO" "$PYTHON" -m PyInstaller \
  --noconfirm \
  --distpath "$DIST_DIR" \
  --workpath "$OUT_DIR/work" \
  "$ROOT/packaging/folith-desktop.spec"

echo
echo "=== build record ==="
cat "$BUILD_INFO"
echo
# On macOS the BUNDLE target already contains everything; the bare COLLECT
# directory is a pure duplicate (verified self-contained in D2-4), so drop it
# instead of leaving 300+ MB of confusion next to the .app.
if [[ "$PLATFORM" == "macos" && -d "$DIST_DIR/Folith" && -d "$DIST_DIR/Folith.app" ]]; then
  rm -rf "$DIST_DIR/Folith"
fi

echo "=== artifact size ==="
du -sh "$DIST_DIR"/* 2>/dev/null || true
