#!/usr/bin/env bash
# Beta 候选的完整验证链（20 步）：构建 → 门禁 → 文档一致性 → 用例 → 端到端探针。
#
# 用法：bash scripts/qt_beta_chain.sh [输出目录] [--probes-only]   （默认 tmp/qt-beta/chain）
# 退出码：全部通过为 0；任何一步非 0 则打印该步并返回 1。
# `--probes-only` 跳过构建/门禁/用例（步骤 1–11），只对**磁盘上现有的候选**重跑端到端探针
# （步骤 12–20）。用途：只改了测试/文档/开发脚本、没动 shipped 代码时，不必重建候选就能
# 复核"链的端到端部分仍然全绿"（重建会换 sha、让验收包与文档身份全部过期）。
OUT="tmp/qt-beta/chain"
PROBES_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --probes-only) PROBES_ONLY=1 ;;
    *) OUT="$arg" ;;
  esac
done
LOG="$OUT/chain.log"
# 同一个输出目录**反复跑必须也能过**：上一轮留下的 `交付 结果 中文/` 会让 selftest 的
# `copy_delivery_assets` 以"拒绝覆盖同名文件"失败，整条链因此变红（R62 实际踩到：
# 前 10 步全绿、6 个冻结产物探针全红，原因只是输出目录没清）。这里只清本脚本自己
# 产生的子目录；危险的 OUT（空、根目录、仓库根）直接拒绝，不做任何删除。
if [ -z "$OUT" ] || [ "$OUT" = "/" ] || [ "$OUT" = "." ] || [ "$OUT" = "$PWD" ]; then
  echo "拒绝清理不安全的输出目录：'$OUT'（请给一个 tmp/ 下的子目录）" >&2
  exit 2
fi
for stale in dmg ocr two crash real hung cleanhome; do
  rm -rf "$OUT/$stale"
done
rm -f "$OUT/delivery-content.json" "$OUT/diag.json"
mkdir -p "$OUT"
: > "$LOG"
FAILED=0
run() {
  echo "### $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  local rc=$?
  echo "### rc=$rc $*" >> "$LOG"
  if [ "$rc" -ne 0 ]; then echo "rc=$rc  $*" >&2; FAILED=1; fi
}
# 先构建，再跑用例：其中包含"产物与源码一致"的断言，顺序颠倒会误报。
if [ "$PROBES_ONLY" -eq 0 ]; then
run bash scripts/build_qt_desktop.sh
run bash scripts/build_qt_dmg.sh
run venv/bin/python scripts/qt_release_checks.py --write
run venv/bin/python scripts/qt_release_checks.py --write-notes
run venv/bin/python scripts/qt_release_checks.py --check-notes
run venv/bin/python scripts/qt_artifact_check.py --out build/qt-desktop/artifact-check.json
run venv/bin/python scripts/qt_acceptance_kit.py --write
run venv/bin/python scripts/qt_acceptance_kit.py --check
run venv/bin/python scripts/qt_docs_audit.py --json build/qt-desktop/docs-audit.json
run venv/bin/python scripts/qt_duplicate_defs.py
run venv/bin/python scripts/run_regression.py \
  -k qt_acceptance_kit,qt_delivery_content,qt_client_style,red_team_acceptance,qt_extraction_error,qt_client_adapter,qt_client_window,qt_release_plumbing \
  --tsv "$OUT/regression.tsv"
fi
DMG=$(ls dist/qt-desktop/*.dmg | head -1)
run venv/bin/python scripts/qt_dmg_probe.py --dmg "$DMG" --out $OUT/dmg
run venv/bin/python scripts/qt_ocr_baseline_runner.py --out $OUT/ocr \
  --frozen-app "$PWD/dist/qt-desktop/Folith.app/Contents/MacOS/Folith"
# 两个任务同时运行 + 取消其中一个（另一个必须继续并跑完）
run venv/bin/python scripts/qt_lifecycle_probe.py --scenario two-tasks-cancel \
  --data-dir $OUT/two/data --out $OUT/two/report.json \
  --stub-delay 0.6 --timeout 420
run venv/bin/python scripts/qt_crash_recovery_probe.py \
  --data-dir $OUT/crash/data --out $OUT/crash/report.json --paragraphs 150
run venv/bin/python scripts/qt_desktop_probe.py --frozen-app "$PWD/dist/qt-desktop/Folith.app/Contents/MacOS/Folith" \
  --data-dir "$OUT/real/data" --out "$OUT/real/report.json" --fixture both \
  --screenshot "$OUT/real/frozen-workbench.png"
# 模型请求挂住时（睡眠/唤醒、网络黑洞）：界面可用、可取消、退出有上界且走显式强制路径。
run venv/bin/python scripts/qt_hung_provider_probe.py --out $OUT/hung
# 干净用户首跑：在全新的 HOME 下跑完整流程（验收清单 A 步可机器验证的部分）。
run venv/bin/python scripts/qt_clean_home_probe.py \
  --app "$PWD/dist/qt-desktop/Folith.app/Contents/MacOS/Folith" \
  --out $OUT/cleanhome
# 交付文件内容校验：把随包产物写出的交付文件读回来，与任务状态交叉核对。
JOB=$(venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["job_ids"][0])' "$OUT/real/report.json")
run venv/bin/python scripts/qt_delivery_content_probe.py \
  --delivery-dir "$OUT/real/交付 结果 中文" \
  --state "$OUT/real/data/$JOB/state.json" \
  --out $OUT/delivery-content.json
run env -i HOME="$HOME" PATH=/usr/bin:/bin TMPDIR="$TMPDIR" \
  dist/qt-desktop/Folith.app/Contents/MacOS/Folith --diagnostics-report \
  --data-dir /tmp/qt-diag-final --result $OUT/diag.json
echo "CHAIN61 DONE" >> "$LOG"

if [ "$FAILED" -ne 0 ]; then
  echo "验证链有步骤失败，详见 $LOG" >&2
  exit 1
fi
if [ "$PROBES_ONLY" -eq 1 ]; then
  echo "端到端探针全部通过（跳过构建/门禁/用例，共 9 步），日志：$LOG"
else
  echo "验证链全部通过（20 步），日志：$LOG"
fi
