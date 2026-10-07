#!/usr/bin/env bash
# Build the macOS DMG for the Folith desktop beta (roadmap §5 D2: DMG).
#
# Layout: `Folith.app` plus an `Applications` symlink, so the user can drag the
# app into /Applications.  The artifact is unsigned (no Developer ID in this
# environment), so it is a local/beta artifact — Gatekeeper behaviour for a real
# download belongs to D3.
#
# Usage: scripts/build_dmg.sh [--skip-verify]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist/desktop"
APP="$DIST/Folith.app"
BUILD_INFO="$ROOT/build/desktop/desktop_build_info.json"
SKIP_VERIFY=0
[[ "${1:-}" == "--skip-verify" ]] && SKIP_VERIFY=1

if [[ ! -d "$APP" ]]; then
  echo "[error] $APP not found; run scripts/build_desktop.sh first" >&2
  exit 1
fi

VERSION="$(/usr/bin/python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['app_version'])" "$BUILD_INFO" 2>/dev/null || echo 0.0.0)"
BUILD_ID="$(/usr/bin/python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['build_id'])" "$BUILD_INFO" 2>/dev/null || echo unknown)"
ARCH="$(uname -m)"
NAME="Folith-${VERSION}-macos-${ARCH}.dmg"
TARGET="$DIST/$NAME"
VOLUME="Folith ${VERSION}"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/folith-dmg-XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "[1/4] staging $APP"
ditto "$APP" "$STAGE/Folith.app"
ln -s /Applications "$STAGE/Applications"

echo "[2/4] creating $NAME"
rm -f "$TARGET"
hdiutil create -volname "$VOLUME" -srcfolder "$STAGE" -ov -format UDZO \
  -fs HFS+ "$TARGET" >/dev/null

echo "[3/4] verifying image"
if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  hdiutil verify "$TARGET" >/dev/null
fi

echo "[4/4] recording"
SIZE_BYTES="$(stat -f%z "$TARGET")"
SHA="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
/usr/bin/python3 - "$TARGET" "$SIZE_BYTES" "$SHA" "$VERSION" "$BUILD_ID" "$ARCH" <<'PY' \
  > "$ROOT/build/desktop/dmg_record.json"
import json, sys, time
target, size, sha, version, build_id, arch = sys.argv[1:7]
print(json.dumps({
    "artifact": target,
    "bytes": int(size),
    "sha256": sha,
    "app_version": version,
    "build_id": build_id,
    "target_platform": "macos",
    "target_arch": arch,
    "packaging_backend": "hdiutil (UDZO)",
    "format": "UDZO",
    "signed": False,
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}, ensure_ascii=False, indent=2, sort_keys=True))
PY

echo
echo "=== dmg ==="
cat "$ROOT/build/desktop/dmg_record.json"
echo "size: $(du -h "$TARGET" | cut -f1)"
