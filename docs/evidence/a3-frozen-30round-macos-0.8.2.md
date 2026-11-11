# A3 加压验收记录（macOS，0.8.2）

本文件是 issue #326 的**加压验收证据**，含两条独立门槛：

1. **冻结版**：每轮 3 个用例、连续 30 轮、0 次 -11（下方第一节）
2. **源码模式已知复现用例**：≥700 轮 0 崩溃（下方第二节）

- 验证日期：2026-11-11
- 机器：用户 Mac（macOS 27.0.1 / Apple M4 / 16 GB / 10 核），arm64
- 构建来源：`fix/0.8.2-326-a3-batch4` @ `85074541`（含 #331 / #333 / #334 / #338 全部 A3 修复），`worktree_dirty=false`
- 产物：`Folith.app`（438 MB），二进制 SHA-256 `6eccbf48…23c30d8`
- 依赖：PySide6 6.11.2 / pyinstaller 6.22.3
- 跑法：`scripts/qt_ocr_baseline_runner.py --frozen-app <Folith.app>/Contents/MacOS/Folith --out <dir>`（每轮 3 个用例：`pdf_scanned` / `pdf_chinese_scanned` / `pdf_arabic_scanned` + 1 个缺语言包负例）
- 加压：脚本化的 CPU 忙循环（`run30.sh`），与 0.8.1 发布验收同口径

## 结果：30/30 轮，0 次 -11

| 指标 | 值 |
|---|---|
| 轮次 | 30 / 30（连续，无跳过、无重试） |
| 正例 exit 分布 | `{0: 90}`（3 用例 × 30 轮） |
| 正例引擎 | `eng` / `chi_sim` / `ara`，期望串 90/90 命中（CJK 去空白匹配） |
| 负例 exit 分布 | `{1: 30}` —— 30 轮全部仍按预期失败 |
| `runner_rc` | 30/30 = 0 |
| `ocr-baseline.json` | `ok` 30/30 = true |
| 失败目录 | 空 |
| 系统崩溃报告增益 | **0**（`Folith*.ips` 前后计数均为 0） |
| 每轮耗时 | ~154 s（总 76.9 分钟） |

按历史 18%/轮的崩溃率，30 轮全过概率约 0.3%（见 `docs/segfault-bisect-guide-0.8.1.md`）。

## 对照组：0.8.1 旧冻结版 5 轮内复现 -11

同机、同协议、同时段，用用户现有的 0.8.1 冻结版（`build_id=git:b38f1a2b`，只读执行）跑 5 轮：

- 第 5 轮 `pdf_chinese_scanned` **exit -11**（23.5 s 处）
- 崩溃报告：`Folith-2026-11-11-023450.ips`，`app_version=0.8.1`，`EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS at 0x0`
- 主线程原生栈命中指南 §2 第一类签名：`PySide::SignalManager::retrieveMetaObject` ← `Sbk_QTimer_Init` ← `type_call` ← `PySideQSlotObject::impl` ← `QObject::event`

结论：**0/30 不是探针被弄钝**——同一装置能在旧版上检出崩溃。对照组 5 轮只能证明"装置有效"，不足以估计 0.8.1 的真实崩溃率。

## 边界（不能当全部验收）

- 这是 **offscreen + 脚本化 selftest + CPU 加压**口径，不是人工交互验收；不含真实文档 / 真实 provider / 8 小时长跑 / **Windows**。
- `#326` 的另一条 CI 门槛（`macOS Qt candidate` 连续 2 次全绿）不在本记录内，需在 PR 合入 main 后确认。
- 验证期间机器上另有 Qt 测试负载（loadavg 14–68），两组在同一负载条件下运行，对照仍成立。

## 源码模式 ≥700 轮复现（issue 验收第二条）

- 代码版本：`85074541`（**与上面 30 轮冻结版同一提交**）
- 每轮 = 3 个已知复现文件：`tests/qt_concept_dialog_test.py`(3) + `tests/qt_layout_item_wrapper_test.py`(8) + `tests/qt_dialog_lifecycle_test.py`(4)，每轮 `15 passed`
- **700/700 轮完成**，轮号 1–700 无缺口无重复；`exit` 分布 **`{0: 700}`**（无 -11/-6、无超时、无 pytest 失败）
- 崩溃报告：`Folith*.ips` 运行前 1 → 运行后 1（**0 增益**；那份是上一节对照组 0.8.1 在 02:34 产生的，早于本轮 02:56 开始）
- 单轮 p50 12.8 s / max 17.5 s，总墙钟 51.8 min；`crash/`、`failed/` 现场目录均为空
- **执行协议（如实披露）**：单线程顺序需 ~96 min（超预算），正式 700 轮用 **3 个独立 worker 并发**（各跑 1/3 轮号，每轮仍是全新独立 pytest 进程）；并发只增加 CPU 争用（对"更容易崩"是更严苛条件），不减少轮次、不改判据。正式跑前有两段被中止的尝试（顺序 82 轮、双并发 38 轮，为压缩墙钟改并发而中止，**不是因为崩溃**），两段全部 0 崩溃并原样归档
- 700 这个数字取自 issue #326 验收原文（`docs/segfault-bisect-guide-0.8.1.md` 只规定冻结版 30 轮）

**与冻结版的互补关系**：冻结版证明**打包产物**不再崩（且对照组 0.8.1 同机同协议第 5 轮复现 -11）；源码 700 轮证明**修复机制长跑不退化**（源码模式崩溃率本就显著低于冻结版）。issue 两条都要求，不可互替。

逐轮记录（本地）：`/tmp/a3-700r/rounds-w{1,2,3}.jsonl`、`manifest.json`、`summary.json`。
