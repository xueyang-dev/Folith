#!/bin/bash
# Clean-machine acceptance smoke check for the Folith macOS beta.
#
# Roadmap §6.1 / §25: D2's completion condition is "无开发环境的干净机器以普通用户完成验收".
# This script performs everything that can be automated on such a machine and
# writes a report the reviewer can attach as evidence.  It uses **only macOS
# built-ins** (no Python, no Homebrew), so it can run on a machine that has never
# seen a development environment.
#
# What it proves:
#   1. the DMG checksum and the app identity inside it;
#   2. the app launches and serves its UI without any system Python;
#   3. the data directory is created in the documented default location;
#   4. the bundled OCR engine ships eng/chi_sim/ara and works with no Homebrew;
#   5. diagnostics report the bundled runtime and the honest schema state;
#   6. quitting reclaims the service with no residual processes.
#
# What it cannot prove (do these by hand, see docs/desktop-d2/clean-machine-acceptance.md):
#   the full import → translate → review → deliver flow, the native save panel,
#   Gatekeeper's first-launch prompt, and real IME input.
#
# Usage:
#   scripts/clean_machine_acceptance.sh --dmg /path/Folith-0.5.0-macos-arm64.dmg \
#       [--expect-sha256 SHA256] [--install] [--data-dir DIR] [--log-dir DIR] [--out DIR]
set -u

DMG=""
EXPECT_SHA=""
INSTALL=0
DATA_DIR=""
LOG_DIR=""
OUT_DIR="$(pwd)/folith-clean-acceptance"
MOUNT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --expect-sha256) EXPECT_SHA="$2"; shift 2 ;;
    --install) INSTALL=1; shift ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$DMG" ]]; then
  echo "[error] --dmg is required" >&2
  exit 2
fi
if [[ ! -f "$DMG" ]]; then
  echo "[error] DMG not found: $DMG" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/clean-machine-report.txt"
LOG="$OUT_DIR/app-launch.log"
: > "$REPORT"

PASS=0
FAIL=0
pass() { echo "PASS  $1" | tee -a "$REPORT"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $1" | tee -a "$REPORT"; FAIL=$((FAIL + 1)); }
info() { echo "      $1" | tee -a "$REPORT"; }

cleanup() {
  if [[ -n "$MOUNT" && -d "$MOUNT" ]]; then
    hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Folith clean-machine acceptance" | tee -a "$REPORT"
echo "generated: $(date '+%Y-%m-%d %H:%M:%S %z')" | tee -a "$REPORT"
info "host: $(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null) ($(uname -m))"
info "dmg: $DMG"
info "python on PATH: $(command -v python3 || echo none)"
info "brew on PATH: $(command -v brew || echo none)"
echo | tee -a "$REPORT"

# --- 1. checksum and image --------------------------------------------------
ACTUAL_SHA="$(shasum -a 256 "$DMG" | awk '{print $1}')"
info "sha256: $ACTUAL_SHA"
if [[ -n "$EXPECT_SHA" ]]; then
  if [[ "$ACTUAL_SHA" == "$EXPECT_SHA" ]]; then
    pass "dmg checksum matches the expected value"
  else
    fail "dmg checksum mismatch (expected $EXPECT_SHA)"
  fi
else
  info "no --expect-sha256 given; record the sha256 above"
fi
if hdiutil verify "$DMG" >/dev/null 2>&1; then
  pass "hdiutil verify"
else
  fail "hdiutil verify"
fi

# --- 2. mount and inspect the bundle ---------------------------------------
MOUNT="$(mktemp -d "${TMPDIR:-/tmp}/folith-acceptance-XXXXXX")"
if hdiutil attach -nobrowse -readonly -mountpoint "$MOUNT" "$DMG" >/dev/null 2>&1; then
  pass "DMG mounts read-only"
else
  fail "DMG failed to mount"
  echo "report: $REPORT"
  exit 1
fi
APP="$MOUNT/Folith.app"
if [[ -d "$APP" ]]; then pass "Folith.app present"; else fail "Folith.app missing"; fi
if [[ -L "$MOUNT/Applications" && "$(readlink "$MOUNT/Applications")" == "/Applications" ]]; then
  pass "Applications drag target present"
else
  fail "Applications drag target missing"
fi
PLIST="$APP/Contents/Info.plist"
BUNDLE_NAME="$(defaults read "$PLIST" CFBundleName 2>/dev/null || true)"
BUNDLE_ID="$(defaults read "$PLIST" CFBundleIdentifier 2>/dev/null || true)"
ICON="$(defaults read "$PLIST" CFBundleIconFile 2>/dev/null || true)"
info "CFBundleName=$BUNDLE_NAME CFBundleIdentifier=$BUNDLE_ID CFBundleIconFile=$ICON"
if [[ "$BUNDLE_NAME" == "Folith" && "$BUNDLE_ID" == "dev.folith.desktop" ]]; then
  pass "bundle identity"
else
  fail "bundle identity unexpected"
fi
if [[ -n "$ICON" && -f "$APP/Contents/Resources/$ICON" ]]; then
  pass "app icon ships with the bundle"
else
  fail "app icon missing"
fi

# --- 3. optional install to /Applications ----------------------------------
RUN_APP="$APP"
if [[ "$INSTALL" -eq 1 ]]; then
  if [[ -d /Applications/Folith.app ]]; then
    fail "/Applications/Folith.app already exists (remove it first)"
  else
    if ditto "$APP" /Applications/Folith.app; then
      pass "installed to /Applications"
      RUN_APP="/Applications/Folith.app"
    else
      fail "install to /Applications failed"
    fi
  fi
fi

# --- 4. launch without any system Python -----------------------------------
ENV_PREFIX=()
[[ -n "$DATA_DIR" ]] && ENV_PREFIX+=("FOLITH_DESKTOP_DATA_DIR=$DATA_DIR")
[[ -n "$LOG_DIR" ]] && ENV_PREFIX+=("FOLITH_DESKTOP_LOG_DIR=$LOG_DIR")
if [[ ${#ENV_PREFIX[@]} -eq 0 ]]; then
  info "using the documented default data/log locations"
  DATA_ROOT="$HOME/Library/Application Support/Folith/data"
else
  info "using an isolated data directory (not the default-location acceptance)"
  DATA_ROOT="${DATA_DIR:-$HOME/Library/Application Support/Folith/data}"
fi
# The instance record lives in the *state* directory, i.e. the parent of the data
# directory (the app lock and record deliberately sit outside user data).
STATE_DIR="$(dirname "$DATA_ROOT")"

env "${ENV_PREFIX[@]}" "$RUN_APP/Contents/MacOS/Folith" --no-browser >"$LOG" 2>&1 &
LAUNCHER_PID=$!
RECORD="$STATE_DIR/instance.json"
for _ in $(seq 1 120); do
  [[ -f "$RECORD" ]] && break
  sleep 1
done
if [[ -f "$RECORD" ]]; then
  pass "launcher published an instance record"
  PORT="$(grep -o '"port": *[0-9]*' "$RECORD" | grep -o '[0-9]*' | head -1)"
  SERVICE_PID="$(grep -o '"service_pid": *[0-9]*' "$RECORD" | grep -o '[0-9]*' | head -1)"
  info "port=$PORT service_pid=$SERVICE_PID"
  HTTP="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT" || echo 000)"
  if [[ "$HTTP" == "200" ]]; then
    pass "UI served over loopback (HTTP 200)"
  else
    fail "UI did not answer (HTTP $HTTP)"
  fi
  if [[ -d "$DATA_ROOT" ]]; then pass "data directory present: $DATA_ROOT"; else fail "data directory missing"; fi
  if [[ -f "$RECORD" ]]; then pass "instance record in the state directory: $RECORD"; fi
else
  fail "no instance record appeared"
  info "launch log tail: $(tail -3 "$LOG" | tr '\n' ' ')"
fi

# --- 5. bundled OCR engine --------------------------------------------------
TESSDATA="$APP/Contents/Frameworks/tesseract/share/tessdata"
TESSERACT="$APP/Contents/Frameworks/tesseract/bin/tesseract"
if [[ -x "$TESSERACT" ]]; then
  LANGS="$(TESSDATA_PREFIX="$TESSDATA" "$TESSERACT" --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
  info "bundled OCR languages: $LANGS"
  MISSING=""
  for language in eng chi_sim ara; do
    case " $LANGS " in *" $language "*) ;; *) MISSING="$MISSING $language" ;; esac
  done
  if [[ -z "$MISSING" ]]; then
    pass "bundled OCR ships eng, chi_sim and ara"
  else
    fail "bundled OCR missing:$MISSING"
  fi
else
  fail "bundled tesseract binary missing"
fi

# --- 6. diagnostics ---------------------------------------------------------
DIAG="$OUT_DIR/diagnostics.json"
env "${ENV_PREFIX[@]}" "$RUN_APP/Contents/MacOS/Folith" --export-diagnostics "$DIAG" >/dev/null 2>&1
if [[ -f "$DIAG" ]]; then
  pass "diagnostics export"
  grep -q '"frozen": *true' "$DIAG" && pass "diagnostics report frozen=true" || fail "frozen flag missing"
  grep -q '"packaging_backend": *"pyinstaller"' "$DIAG" && pass "diagnostics report the packaging backend" || fail "packaging backend missing"
  grep -q '"job_state": *"unversioned"' "$DIAG" && pass "diagnostics report job_state=unversioned" || fail "schema state missing"
  grep -q '"runtime": *{' "$DIAG" && grep -q '"declared": *"2"' "$DIAG" \
    && pass "diagnostics report the declared runtime schema (2)" || fail "declared runtime schema missing"
  if grep -q 'api_key' "$DIAG" || grep -q 'sk-' "$DIAG"; then
    fail "diagnostics appear to contain credential-looking text"
  else
    pass "diagnostics contain no credential-looking text"
  fi
else
  fail "diagnostics export failed"
fi

# --- 7. quit and reclaim ----------------------------------------------------
kill "$LAUNCHER_PID" >/dev/null 2>&1 || true
wait "$LAUNCHER_PID" 2>/dev/null || true
for _ in $(seq 1 60); do
  pgrep -f "Folith --serve" >/dev/null 2>&1 || break
  sleep 1
done
if pgrep -f "Folith --serve" >/dev/null 2>&1; then
  fail "a Folith service process survived the quit"
else
  pass "quit reclaimed the service (no residual processes)"
fi

echo | tee -a "$REPORT"
echo "summary: $PASS passed, $FAIL failed" | tee -a "$REPORT"
echo "report: $REPORT" | tee -a "$REPORT"
if [[ "$INSTALL" -eq 1 ]]; then
  echo "note: /Applications/Folith.app is installed; drag it to the Trash to uninstall (user data stays in $DATA_ROOT)" | tee -a "$REPORT"
fi
[[ "$FAIL" -eq 0 ]]
