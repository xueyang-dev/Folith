# A3 冻结版加压验证（macOS，0.8.2）

本文件是 issue #326 的**发布门槛证据**：冻结版每轮 3 个用例、连续 30 轮、0 次 -11。

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

## 附：`≥700 轮` 源码模式复现

见下方表格（单独一条记录，跑完补）。源码模式崩溃率显著低于冻结版（指南 §2），因此这一项与上面的冻结版门槛**互补而非替代**。
