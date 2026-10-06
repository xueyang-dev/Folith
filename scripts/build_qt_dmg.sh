#!/usr/bin/env bash
# Build the macOS DMG for the Folith Qt desktop beta.
#
# Layout: `Folith.app` plus an `Applications` symlink, so a tester can drag the
# app into /Applications.  The artifact is **unsigned** (no Developer ID in this
# environment), which is acceptable only for an explicitly disclosed invited
# private beta — never described as a public release.
#
# Usage: scripts/build_qt_dmg.sh [--skip-verify]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist/qt-desktop"
APP="$DIST/Folith.app"
BUILD_INFO="$ROOT/build/qt-desktop/desktop_build_info.json"
SKIP_VERIFY=0
[[ "${1:-}" == "--skip-verify" ]] && SKIP_VERIFY=1

if [[ ! -d "$APP" ]]; then
  echo "[error] $APP not found; run scripts/build_qt_desktop.sh first" >&2
  exit 1
fi

read_record() {
  /usr/bin/python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" \
    "$BUILD_INFO" "$1" 2>/dev/null || echo ""
}
VERSION="$(read_record app_version)"; VERSION="${VERSION:-0.0.0}"
BUILD_ID="$(read_record build_id)"; BUILD_ID="${BUILD_ID:-unknown}"
ARCH="$(uname -m)"
NAME="Folith-Qt-${VERSION}-macos-${ARCH}.dmg"
TARGET="$DIST/$NAME"
VOLUME="Folith ${VERSION}"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/folith-qt-dmg-XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "[1/5] staging $APP"
ditto "$APP" "$STAGE/Folith.app"
ln -s /Applications "$STAGE/Applications"
# A short, honest note inside the image: the candidate is unsigned and the
# system must be told to allow this specific app once.
cat > "$STAGE/请先阅读.txt" <<'TXT'
Folith 桌面端（Qt）— 受邀 Private Beta 候选

安装：
  1. 把 Folith.app 拖到右侧的「应用程序」文件夹。
  2. 首次打开：在「应用程序」里右键点 Folith → 「打开」→ 再点「打开」。
     （未签名候选，macOS 会要求你为这一个应用明确放行一次；不要全局关闭
       Gatekeeper。）
  3. 首次启动后在「设置」里填写模型连接，点「测试连接」确认可用。

数据位置：~/Library/Application Support/Folith/data
日志位置：~/Library/Logs/Folith
卸载：把 Folith.app 拖到废纸篓即可；用户数据不会被删除。

已知限制：未签名、未公证；未在 macOS 13/14 上验收（本候选要求 macOS 15 或更高）。
TXT

echo "[2/5] creating $NAME"
rm -f "$TARGET"
hdiutil create -volname "$VOLUME" -srcfolder "$STAGE" -ov -format UDZO \
  -fs HFS+ "$TARGET" >/dev/null

echo "[3/5] verifying image"
if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  hdiutil verify "$TARGET" >/dev/null
fi

echo "[4/5] recording"
SIZE_BYTES="$(stat -f%z "$TARGET")"
SHA="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
/usr/bin/python3 - "$TARGET" "$SIZE_BYTES" "$SHA" "$VERSION" "$BUILD_ID" "$ARCH" \
  "$BUILD_INFO" <<'PY' > "$ROOT/build/qt-desktop/dmg_record.json"
import json, sys, time
target, size, sha, version, build_id, arch, build_info = sys.argv[1:8]
record = json.load(open(build_info))
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
    "shell": "qt-widgets",
    "signed": False,
    "notarized": False,
    "minimum_system_version": record.get("minimum_system_version", ""),
    "python_version": record.get("python_version", ""),
    "pyside6_version": record.get("pyside6_version", ""),
    "qt_version": record.get("qt_version", ""),
    "pyinstaller_version": record.get("pyinstaller_version", ""),
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}, ensure_ascii=False, indent=2, sort_keys=True))
PY

echo "[5/5] confirming the packaged app matches the built candidate"
MOUNT_POINT="$(mktemp -d "${TMPDIR:-/tmp}/folith-qt-mnt-XXXXXX")"
hdiutil attach "$TARGET" -mountpoint "$MOUNT_POINT" -nobrowse -quiet
PLIST_MIN="$(/usr/bin/python3 -c "
import plistlib
with open('$MOUNT_POINT/Folith.app/Contents/Info.plist','rb') as handle:
    print(plistlib.load(handle).get('LSMinimumSystemVersion',''))")"
BUNDLE_ID="$(/usr/bin/python3 -c "
import plistlib
with open('$MOUNT_POINT/Folith.app/Contents/Info.plist','rb') as handle:
    print(plistlib.load(handle).get('CFBundleIdentifier',''))")"
hdiutil detach "$MOUNT_POINT" -quiet
rmdir "$MOUNT_POINT" 2>/dev/null || true
echo "packaged app: bundle=$BUNDLE_ID LSMinimumSystemVersion=$PLIST_MIN"
[[ "$BUNDLE_ID" == "dev.folith.qt" ]] || { echo "FATAL: wrong bundle in the DMG" >&2; exit 2; }

echo
echo "=== dmg ==="
cat "$ROOT/build/qt-desktop/dmg_record.json"
echo "size: $(du -h "$TARGET" | cut -f1)"
