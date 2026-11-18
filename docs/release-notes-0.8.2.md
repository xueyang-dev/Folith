# Folith 0.8.2 发布说明

状态：**正式发布，2026-11-18。** 用户 2026-11-18 完成 Windows 界面目视签核与 macOS 冻结包 XLIFF 亲测后授权发布（「可以正式release了」）。发布提交与 Release Validation run 记录在 [0.8.2 验收对账表](release/0.8.2-acceptance-ledger.md)。

本版按 0.8.2 快速发布方案（用户 2026-11-15 裁定）执行：门槛从原计划 §13 的完整验收清单收缩为 9 条，其余整体移到 0.8.3 并在下方如实列出。门槛状态与证据见 [0.8.2 验收对账表](release/0.8.2-acceptance-ledger.md)。

一句话：这一版把**双语文件（XLIFF 1.2 / SDLXLIFF / MQXLIFF）接进了工作台和交付面板**，把**引擎跑进独立子进程**这条架构路径打通（默认仍走进程内），并修掉了一批打开任务、保存失败、任务操作失败时卡界面或沉默的问题。

---

## 这一版新增的功能

### 1. 双语文件：导入、编辑、导出（XLIFF 1.2）

工作台现在可以直接导入 `.xlf` / `.xliff`（通用 XLIFF 1.2），以及 `.sdlxliff` / `.mqxliff`（Trados / memoQ 的方言）。导入后：

- 段落、原文、译文、审校状态、锁定状态都来自文件本身，可以像普通任务一样在工作台里查看。
- **锁定段是只读行**：看得见、改不了。批量替换、定点重译、候选采用、实体统一、段落结构编辑都会跳过或拒绝锁定段，并在界面上说明跳过了多少段。
- 交付面板新增两个入口：**「原样导出双语文件…」**（任何方言都可用，把用户选的原文件逐字节写回）和 **「导出双语文件并写入译文…」**（只对通用 1.2 出现，把当前译文写回 `<target>`）。
- 写回时会先核对段落身份、原文和锁定信息，任何一项对不上就整批拒绝，不会写出半份文件；被修改的单元如果带着 `approved` / `state` / `status` 这类外部状态属性，写回会**明确拒绝并说明原因**——否则下游 CAT 会把没审过的新译文当成已批准。
- 1,000 单元的导入加写回导出：macOS 真实窗口实测最长界面停顿 **396 ms**（同机 offscreen 311–323 ms），门槛是 500 ms。这是**空闲机器**上的数字；机器上有别的重活时本机也量到过 459 ms，所以这一条是按「空闲机器上不超过 500 ms」说的。Windows 没有量过 1,000 单元。

**方言只读**：SDLXLIFF 与 MQXLIFF 只有「原样导出」，界面上不出现写回译文的入口。这是设计约束，不是缺陷：这两类文件里的状态、确认与锁定由 Trados / memoQ 自己维护，Folith 不去伪造。

证据：[P3-5 导入导出证据](evidence/0.8.2-p3-5-import-export.md)、[真实窗口走查（含截图）](evidence/0.8.2-g5-ui-walkthrough.md)、[未知命名空间口径](evidence/0.8.2-g4-unknown-namespace.md)、[内核证据](evidence/0.8.2-g4-xliff12-kernel.md)、[方言只读证明](evidence/0.8.2-g5-dialect-readonly.md)、[独立校验](evidence/0.8.2-p3-6-xliff-validation.md)。

### 2. 引擎子进程（默认关闭）

翻译流水线可以跑进每个任务一个的独立子进程（`folith.desktop.engine_supervisor` + `engine_host`）：界面进程只负责窗口与文件，起步（`Popen` + 一行 stdin）立即返回，冷启动的约 1 秒 `import core` 发生在界面已经可用之后；取消、退出通过文件里的请求标志与 stdin EOF 生效，退出时子进程跟着收尾。

**默认仍是进程内**：不设环境变量时走原来的路径，行为逐字节不变。要用子进程运行，设 `FOLITH_ENGINE_PROCESS=1` 启动客户端。这一版的验收只到「CI 上的子进程生命周期用例 + 冒烟清单里的退出检查」，没有把默认值改掉，也没有在真实机器上做长跑对照。

证据：`tests/engine_supervisor_*_test.py`、`tests/engine_cold_start_request_test.py` 等接收测试；E-0 的对象清单、E-1 的冷启动测量记录在 `.loop/reports/`。

### 3. 值得单独说的修复

- **打开任务不再整份读状态**：打开路径上「这个任务是不是 PDF / 有没有流式原文」这类判断原来会整份读取 `source.bin`（一本几十 MB 的书就整份读进内存），现在只读文件头与索引，打开阶段的主线程停顿明显变短（P2-13、P2-14）。
- **保存失败后的刷新移出界面线程**：保存失败会触发「以磁盘为准」的整份状态读回，原来在界面线程做；现在改到后台（P2-11）。
- **运行状态下写入超时不再挂住界面**：运行状态文件的锁被占用时，后台定时写入会在有界等待后放弃这一次并记录，界面操作路径则如实报「请稍后重试」，不再把界面挂在锁上（E-6u）。
- **任务操作失败会说话**：开始/继续/重试/取消这类按钮失败时，界面给出可读原因，不再静默无反应（E-6v）。
- **删除任务/项目不再长时间阻塞界面**：删除路径的等待预算与取消语义按实测收紧（M-1、#411）。
- **翻译第一窗口的目标段数修正**：开始翻译后第一屏的段落数不再少于声明值（#408）。
- **Windows「减少动态」**：读取系统动画设置（#415）。真机效果见门槛 9 与 Windows 冒烟清单。
- **Windows 上打开大任务的原生崩溃修好了**：两个根因（帧环把 Qt 对象拖到错误的线程析构、跨线程取帧打坏主线程的帧）各自一个修复（#443、#446）。代价是**界面卡顿记录默认不再包含其他线程的栈**，诊断时用 `FOLITH_JANK_OTHER_THREADS=1` 打开。详见「已知问题」第 5 条与门槛 8 的跑法。

---

## 发布门槛（9 条）当前状态

| # | 门槛 | 状态 | 说明 |
| --- | --- | --- | --- |
| 1 | 冻结代码 | **进行中** | 从 2026-11-16 起 main 只接受发布阻塞修复与发布文档；P2 性能修复例外 |
| 2 | CI 全绿 + 1 次手动 Release Validation（含 macOS / Windows 冻结候选） | **待发布当天** | 合入发布说明与版本号后，在发布提交上触发一次（步骤见 `.loop/reports/release-day.md`） |
| 3 | macOS 冻结包冒烟（安装、翻译到交付、XLIFF 导入导出、重开任务还在、5 轮加压） | **已做** | 见 [macOS 冻结包冒烟证据](evidence/0.8.2-macos-frozen-smoke.md)；**XLIFF 子项：用户在冻结包界面亲测通过（2026-11-18）**——导入、编辑、写回译文导出都做过；自动化套件同时覆盖 |
| 4 | Windows 冻结包冒烟（同第 3 条、3 轮加压、退出无残留进程、界面无明显错位） | **通过**（界面目视签核：用户 2026-11-18） | 冻结包里通用 XLIFF 1.2 界面内编辑后写回 161/161 段、Strict 与 Transitional 两种 schema 均通过；memoQ 与 SDLXLIFF 原样导出与原件逐字节一致；步骤见 [Windows 冒烟清单](release/0.8.2-windows-smoke-checklist.md)；发现一处界面瑕疵见已知问题第 7 条 |
| 5 | 已知问题与发布说明如实 | **本文件即交付** | 性能数字已填（第 7、8 条，`e5f5941d`） |
| 6 | 版本号、标签、发布 | **版本号 PR 已就绪**（#433） | 发布当天与说明一起合；打标签与公开发布等用户点头 |
| 7 | G2 流畅度 −70% | **已测，未达** | R >100 ms −30.8%、R >500 ms 不可判定、I >100 ms +2.0%、I >500 ms +17.6%，见「性能」 |
| 8 | E-7 进程内 / 子进程分阶段对照 | **已完成** | R >100 ms 进程内比子进程高 9.1%，R >500 ms 两臂 0；全线程排行见「性能」 |
| 9 | Windows 流畅度两臂对照 | **已测，未达** | Windows 真机（`e5f5941d`，每臂 3 轮取中位）：R >100 ms **−35.0%**（改善但未到 −70%）、R >500 ms 不可判定、I >100 ms **+22.3%**、I >500 ms **+42.8%**；12/12 轮 0 崩溃。条件与明细见「性能」 |

用户已裁定：第 7–9 条要求的是**测完并如实报告**，不要求达到 70%。达到了写达到，没达到写没达到，不调参凑数，也不因此推迟发布。

---

## 性能

测量日期 2026-11-17，**测量所用提交 `e5f5941d`**（就是这一版的冻结候选，含 P2-21 / P2-21b 修复）。
基线包 `build/0.8.2-baseline-8109b9c2`（`74a421b5`，只移植了测量脚本与延迟分布文件，产品代码不动）。
口径：`metrics.gui_jank_process`，100 ms / 500 ms 两档，变化 =（候选 − 基线）/ 基线，**负数表示改善**。

### 门槛 7（G2 目标 −70%）：**未达成**

macOS 真实窗口、动画开启，产品默认配置（不设 `FOLITH_JANK_OTHER_THREADS`），S1 只测 R（1,000 段、窗口 120:720、每轮 600 s）、S3 只测 I（10,000 段、60 s），两臂各 3 轮交替。

| 阶段 | 档位 | 基线 | 候选 | 变化 | 目标 −70% |
| --- | --- | ---: | ---: | ---: | --- |
| R（滚动/选择/输入） | >100 ms 次/分 | 3.90 | 2.70 | **−30.8%（改善）** | 未达到 |
| R | >500 ms 次/分 | 0.00 | 0.00 | **不可判定**（两臂都是 0） | — |
| I（界面操作） | >100 ms 次/分 | 87.52 | 89.29 | **+2.0%（变差）** | 未达到 |
| I | >500 ms 次/分 | 19.41 | 22.82 | **+17.6%（变差）** | 未达到 |

四格「各降 ≥70%」**全部未达成**：R 的 >100 ms 有所改善但不到目标，I 的两档都变差。
按用户 2026-11-15 的裁定照常发布，数字如实记录——**没有调参、没有改窗口、没有挑轮次**。

### 门槛 8（E-7 进程内 / 子进程对照）

同机同时段、同一候选、S1 只测 R、两臂各 3 轮交替。这一条**打开了** `FOLITH_JANK_OTHER_THREADS=1`（需要全线程栈排行；产品默认不开）。

| 档位 | 子进程（`FOLITH_ENGINE_PROCESS=1`） | 进程内（`=0`） | 差异 |
| --- | ---: | ---: | ---: |
| R >100 ms 次/分 | 2.20 | 2.40 | 进程内 **+9.1%** |
| R >500 ms 次/分 | 0.00 | 0.00 | 不可判定 |

全线程栈排行（三轮合计 `total_ms` 前 5）：进程内是引擎自己的线程在跑（`core._runtime_heartbeat_loop`、`trace._run_sampling`，最长 450 ms）；子进程是监督者读输出（`engine_supervisor._drain_output`，最长 240 ms）。也就是说**子进程化把最长的那几段停顿从引擎线程换到了监督者的读输出上**，R 阶段的两档没有变差到可判定的程度。

### 稳定性

第 7、8 条期间 macOS 共 **18 轮零崩溃**（S1 6 轮 + S3 6 轮 + E-7 6 轮），全部 `ok=true`、产物完整。

### Windows

**门槛 9（Windows 方块对照）**：2026-11-18 在 Windows 真机跑，候选 `e5f5941d`、基线 `74a421b5`，产品默认配置（不设 `FOLITH_JANK_OTHER_THREADS`），每臂 3 轮取中位，单位次/分钟。

| 阶段 | 档位 | 0.8.1 | 候选 | 变化 | 目标 −70% |
| --- | --- | ---: | ---: | ---: | --- |
| R（滚动/选择/输入） | >100 ms | 12.00 | 7.80 | **−35.0%（改善）** | 未达到 |
| R | >500 ms | 0.00 | 0.00 | 不可判定 | — |
| I（界面操作） | >100 ms | 52.56 | 64.29 | **+22.3%（变差）** | 未达到 |
| I | >500 ms | 10.03 | 14.32 | **+42.8%（变差）** | 未达到 |

**结论：门槛 9 也未达 −70%**——R 的 >100 ms 改善了 35%，I 的两档都明显变差。

测量条件照实记录：Windows 11 家庭版、Ryzen 9 7845HX、32 GB、主屏 100%、「平衡」电源方案；**开测时 Windows Update 待重启**，微信剩 1 个进程、OneDrive 暂停状态未核实。12/12 正式轮，0 次崩溃、0 次超时中断；S1 的 `--verify` 6/6 `ok`；S3 按 runbook 预期 `ok=false`，但 `valid_round` / `I_valid` 都为 true；两臂 `--aggregate` 都是 `exit=0`。

### 两个平台共同的性能问题：打开任务变慢了

这一版把打开路径上的一些读整份文件的操作改成了只读文件头与索引（P2-13/P2-14），但**「打开任务」这一步在两个平台上都比 0.8.1 慢**：macOS 的 I >500 ms 是 **+17.6%**，Windows 的 I >500 ms 是 **+42.8%**。这是 **0.8.3 的首要性能目标**，不要因为 R 阶段的改善把这一条读成「更流畅了」。

### Windows 稳定性（P2-21 / P2-21b）

修复前 CI 的 S3 每轮约 2/10 ~ 7/20 概率整轮崩溃；修复后产品默认配置 **20 轮 0 崩溃、收尾退出码全 0**（run `44076100998`，`e5f5941d`），阳性对照（打开跨线程取帧）仍有 6/20 复现，说明因果成立。

证据：[G2 / E-7 最终测量](evidence/0.8.2-g2-final.md)；数字原件在 `.loop/reports/numbers-for-release-notes.md`、`.loop/reports/numbers-windows-g9.md` 与 `.loop/real-run-g7/`。

---

## 已知问题

1. **翻译进行中不能保存人工编辑。** 任务在后台跑时，编辑框里写的内容可以保留为草稿，但提交保存会被拒绝并提示「任务正在后台处理，请等待完成后保存」。这样做是为了避免后台 worker 和人工同时改同一段导致互相覆盖。想改译文，请先暂停或等这一轮跑完。
2. **SDL / memoQ 文件只能原样导出。** `.sdlxliff`、`.mqxliff` 不会写回译文，界面上也没有这个入口；要更新译文，请回 Trados / memoQ 处理。通用 XLIFF 1.2 可以写回译文。
3. **Windows 的验证范围只有冒烟和流畅度。** 这一版没有做 30 轮加压、没有做 8 小时长跑、没有做休眠唤醒，也没有做 0.8.1 界面的 Windows 原生逐项验收。Windows 上「能装、能打开、能翻完一份小文档并交付、XLIFF 能进出、退出没有残留进程」以冒烟清单为准；性能以门槛 9 的双臂对照为准。
4. **G2 流畅度没有达到 −70%**（见「性能」）。不要读成「变快了」。
5. **Windows 上打开大任务的原生崩溃（P2-21）已修复。** main 上 20 轮复核：产品默认口径 **0/20 崩溃**，打开跨线程取帧的阳性对照仍 **6/20**（run `44076100998`）。 两个根因都修了——采样器不再把被检查线程的帧拖进循环垃圾（[#443](https://github.com/xueyang-dev/Folith/pull/443)），也不再默认跨线程取帧（[#446](https://github.com/xueyang-dev/Folith/pull/446)；`FOLITH_JANK_OTHER_THREADS=1` 才打开）。判别实验（Windows CI，三臂 × 20 轮）：旧默认 **7/20 崩溃**，只采主线程 **0/20** 且每轮 `exit=0`。**代价**：界面卡顿记录默认不再包含其他线程的栈，只看得到主线程自己的停顿；需要全线程栈排行时（诊断、第 8 条测量）设 `FOLITH_JANK_OTHER_THREADS=1`。`FOLITH_JANK_MONITOR=0` 仍然全关，但测量里不要用（runner 会把整轮记成失败）。
6. **子进程模式的退出偶发要再点一次。** 打开 `FOLITH_ENGINE_PROCESS=1` 后，Windows 上退出时偶尔会提示「停止任务失败」并留在应用里，或者在下次打开时把这条任务显示成「意外中断」。**任务与数据都不受影响**（进度已落盘、任务仍是可继续的 `interrupted`），再点一次退出即可。只影响这个默认关闭的开关；0.8.3 修（[#453](https://github.com/xueyang-dev/Folith/issues/453)）。
7. **Windows 首次进入交付页时，「冻结交付」提示在窗口左缘被裁切（[#520](https://github.com/xueyang-dev/Folith/issues/520)）。** 提示文字仍在、功能可用，只是贴边显示不完整；把窗口拉宽即可看清。不影响交付结果，0.8.3 修。
8. **启动慢（[#514](https://github.com/xueyang-dev/Folith/issues/514)）。** 任务多、已交付的书多时，从双击到窗口出现要十几秒。实测：macOS，21 个任务、609 MB 的数据目录，**约 16 s**；主要耗时是主页任务列表在**窗口出现之前**同步构建（`refresh_home → adapter.list_jobs()`）。任务少时不明显（Windows 新数据目录冷启动 6.3 s）。**0.8.1 也有，不是 0.8.2 引入的**：`refresh_home`、`list_jobs` 在 0.8.1 的代码里与现在逐字相同。0.8.3 修，目标是同一份数据 **< 3 s**。
9. **任务库与项目页切换卡顿（[#515](https://github.com/xueyang-dev/Folith/issues/515)）。** 数据多时切到「项目」页要等很久，期间界面会卡。实测同一份数据：项目概览**冷 33 s、热 16 s**，界面最长停顿约 **1.4 s**。**0.8.1 也有**：`refresh_projects` 与 `projects_overview` 在 0.8.1 的代码里与现在相同（同样是整份重算、同样的后台提交方式）。0.8.3 修，并加骨架过渡（[#516](https://github.com/xueyang-dev/Folith/issues/516)）。
10. **没有在 Trados 或 memoQ 里做过往返。** 方言样本与通用样本都是这样；原样导出的字节一致有测试与走查，但没有目标工具回读记录。
11. **macOS 安装包仍未签名、未公证。** 首次打开需要用户手动放行。
12. 离屏测试、mock 与单元测试不能代替真实窗口。
13. **数据多时界面偶尔无响应数秒。** 打开或刷新整本书时，后台会复制任务数据并回收内存，这期间界面线程等不到运行权：macOS 实测一次 **7.65 s** 无响应。**0.8.1 也有，不是这一版引入的**（`_isolated_copy` 的 pickle 往返与 0.8.1 逐字相同），数据不受影响；等它恢复即可，不必强制退出。0.8.3 修。

---

## 移到 0.8.3 的清单

按快速发布方案，下面这些**不在 0.8.2 的验收范围**，移到 0.8.3：

- 8 小时长跑与 S4 场景
- 五个操作的延迟预算（p95 与签字校准值）
- 连续 20 次
- 休眠唤醒
- 一本真实书的对照
- 30 轮加压（两个平台）
- 0.8.1 界面在 Windows 上的原生逐项验收
- TM / TB 导入向导的两个平台真窗口 + 一份真实 TMX
- 理解阶段的真实书耗时对照（#319）
- #220 的 B5（坏视觉回复的样子）
- **引擎子进程改为默认开启**（先在 0.8.3 做真实机器长跑对照，再决定默认值）
- G2 的 70% 目标本身（0.8.2 只要求测完如实报告）

---

# Folith 0.8.2 release notes (English)

Status: **released on 2026-11-18.** The user signed off the Windows visual check and tested the XLIFF round trip in the macOS frozen build on 2026-11-18, then authorized the release. The release commit and the Release Validation run are recorded in the [0.8.2 acceptance ledger](release/0.8.2-acceptance-ledger.md).

This release follows the 0.8.2 fast-path plan (user ruling, 2026-11-15): the gate list shrank from the full §13 acceptance matrix to nine gates; everything else moved to 0.8.3 and is listed below. Gate status and evidence: [0.8.2 acceptance ledger](release/0.8.2-acceptance-ledger.md).

In one line: this version brings **bilingual files (XLIFF 1.2 / SDLXLIFF / MQXLIFF) into the workbench and the delivery panel**, opens the **engine-in-a-child-process** architecture path (still off by default), and fixes a set of problems where opening a task, a failed save, or a failed job action would stall the interface or stay silent.

## New in this version

### 1. Bilingual files: import, edit, export (XLIFF 1.2)

The workbench imports `.xlf` / `.xliff` (generic XLIFF 1.2) plus `.sdlxliff` / `.mqxliff` (Trados / memoQ dialects). After import:

- Segments, source, target, review status and lock state come from the file itself.
- **Locked segments are read-only rows**: visible, not editable. Batch replace, retranslation, candidate adoption, entity unification and structural editing skip or refuse them, and the UI reports how many were skipped.
- The delivery panel gains **"Export the bilingual file unchanged…"** (every dialect; writes your chosen file back byte for byte) and **"Export the bilingual file with translations…"** (generic 1.2 only; writes the current targets back into `<target>`).
- Write-back first re-checks segment identity, source text and lock state and refuses the whole batch on any mismatch. If a changed unit carries an external `approved` / `state` / `status` attribute, write-back **refuses with a reason** instead of producing a file that downstream CAT tools would read as approved.
- A 1,000-unit import plus write-back export: the worst main-thread pause was **396 ms** in a real macOS window (311–323 ms offscreen on the same idle machine) against a 500 ms budget. That is an idle-machine number; with other heavy work running the same machine measured 459 ms. Windows was not measured at 1,000 units.

**Dialects are read-only.** SDLXLIFF and MQXLIFF are export-unchanged only, with no write-back entry in the UI. That is a design constraint: their status, confirmation and locking belong to Trados / memoQ, and Folith does not fake them.

### 2. Engine subprocess (off by default)

The translation pipeline can run in one child process per task: the GUI process only owns the window and files, start-up returns immediately, and cancel/quit travel through the request flags in files plus stdin EOF. Quitting stops the child.

**The default is still in-process**: without the environment variable the old path is unchanged byte for byte. Set `FOLITH_ENGINE_PROCESS=1` to run the pipeline in a child process. This version's acceptance covers CI lifecycle tests and the quit check in the smoke checklist; the default was not flipped and no long real-machine comparison was run.

### 3. Notable fixes

- Opening a task no longer reads the whole state (PDF / streaming-source checks read only headers and indexes).
- The refresh after a failed save moved off the GUI thread.
- A contended runtime-state lock no longer hangs the interface: periodic writes drop one tick after a bounded wait; user actions report "please retry".
- Failed start/continue/retry/cancel actions now say why.
- Deleting tasks/projects no longer blocks the interface for seconds.
- First-window target count after starting a translation is no longer short (#408).
- Windows "reduce motion" is read from the system setting (#415).
- The native crash when a large task opens on Windows is fixed: the frame cycle that destroyed Qt objects on a worker thread (#443) and the cross-thread frame walk that corrupted the GUI thread's frames (#446). The trade-off is that jank records no longer include other threads' stacks by default; set `FOLITH_JANK_OTHER_THREADS=1` when diagnosing. See known issue 5 and the gate 8 instructions.

## Nine gates: current status

| # | Gate | Status |
| --- | --- | --- |
| 1 | Code frozen | **in progress** (only release-blocking fixes and release docs) |
| 2 | CI green + one manual Release Validation with macOS/Windows frozen candidates | **release day** — triggered once on the release commit after the notes and version bump land (`.loop/reports/release-day.md`) |
| 3 | macOS frozen-app smoke | **done** — see the [macOS frozen-app smoke evidence](evidence/0.8.2-macos-frozen-smoke.md); the **XLIFF sub-item was verified by the user in the frozen build's UI on 2026-11-18** (import, edit and write-back export), with the automated suite covering it as well |
| 4 | Windows frozen-app smoke | **passed** (visual sign-off by the user on 2026-11-18) — in the frozen build, generic XLIFF 1.2 edited in the UI and written back for 161/161 segments, valid against both the Strict and Transitional schemas; memoQ and SDLXLIFF exported unchanged, byte-identical to the originals; one cosmetic defect is known issue 7 |
| 5 | Known issues and notes are honest | **this document**; the performance section now carries the gate 7 and 8 numbers |
| 6 | Version, tag, release | **version PR ready** (#433); merges with these notes on release day, tag/publish waits for the user |
| 7 | G2 −70% | **measured, not met** — R >100 ms -30.8%, R >500 ms not decidable, I >100 ms +2.0%, I >500 ms +17.6% |
| 8 | E-7 in-process vs subprocess comparison | **done** — R >100 ms is 9.1% higher in-process, R >500 ms zero on both arms |
| 9 | Windows fluency, both arms | **measured, not met** — real machine, `e5f5941d`, three rounds per arm (median): R >100 ms **-35.0%** (better, not the -70% target), R >500 ms not decidable, I >100 ms **+22.3%**, I >500 ms **+42.8%**; 12 of 12 rounds with zero crashes |

The user ruled that gates 7–9 require **measuring and reporting honestly**, not hitting 70%. Report what happened; do not tune numbers and do not slip the release over it.

## Performance

Measured 2026-11-17 on **commit `e5f5941d`** (this release's frozen candidate, P2-21 / P2-21b included).
Baseline package `build/0.8.2-baseline-8109b9c2` (`74a421b5`, measurement scripts and latency distribution only; product code untouched).
Metric: `metrics.gui_jank_process`, 100 ms / 500 ms bands. Change = (candidate - baseline) / baseline, so **negative is better**.

### Gate 7 (G2 target, -70%): **not met**

Real macOS window, animations on, product default configuration (no `FOLITH_JANK_OTHER_THREADS`), S1 measuring R only (1,000 segments, window 120:720, 600 s per round) and S3 measuring I only (10,000 segments, 60 s), three alternating rounds per arm.

| Phase | Band | Baseline | Candidate | Change | -70% target |
| --- | --- | ---: | ---: | ---: | --- |
| R (scroll/select/type) | >100 ms per min | 3.90 | 2.70 | **-30.8% (better)** | not met |
| R | >500 ms per min | 0.00 | 0.00 | **not decidable** (both arms 0) | — |
| I (UI work) | >100 ms per min | 87.52 | 89.29 | **+2.0% (worse)** | not met |
| I | >500 ms per min | 19.41 | 22.82 | **+17.6% (worse)** | not met |

All four cells fall short of -70%: R improved at >100 ms but not enough, and both I bands got worse. Released on schedule per the user's 2026-11-15 ruling, with the numbers recorded as measured — **no retuning, no window changes, no cherry-picked rounds**.

### Gate 8 (E-7 in-process vs subprocess)

Same machine, same window, same candidate, S1 measuring R only, three alternating rounds per arm. This one ran **with** `FOLITH_JANK_OTHER_THREADS=1` because it needs the all-thread stack ranking (the product default is off).

| Band | Subprocess (`FOLITH_ENGINE_PROCESS=1`) | In-process (`=0`) | Difference |
| --- | ---: | ---: | ---: |
| R >100 ms per min | 2.20 | 2.40 | in-process **+9.1%** |
| R >500 ms per min | 0.00 | 0.00 | not decidable |

All-thread ranking (top five `total_ms` over three rounds): in-process, the engine's own threads do the work (`core._runtime_heartbeat_loop`, `trace._run_sampling`, longest 450 ms); as a subprocess, the supervisor draining its output (`engine_supervisor._drain_output`, longest 240 ms). Subprocessing therefore moved the longest stalls from the engine thread to the supervisor's output drain, and did not degrade either R band measurably.

### Stability

18 rounds on macOS with zero crashes during gates 7 and 8 (6 S1 + 6 S3 + 6 E-7), every round `ok=true` with complete artifacts.

### Windows

**Gate 9 (Windows, both arms)** was measured on a real machine on 2026-11-18: candidate `e5f5941d`, baseline `74a421b5`, product default configuration (no `FOLITH_JANK_OTHER_THREADS`), three rounds per arm, median, per minute.

| Phase | Band | 0.8.1 | Candidate | Change | -70% target |
| --- | --- | ---: | ---: | ---: | --- |
| R (scroll/select/type) | >100 ms | 12.00 | 7.80 | **-35.0% (better)** | not met |
| R | >500 ms | 0.00 | 0.00 | not decidable | — |
| I (UI work) | >100 ms | 52.56 | 64.29 | **+22.3% (worse)** | not met |
| I | >500 ms | 10.03 | 14.32 | **+42.8% (worse)** | not met |

**Gate 9 did not reach -70% either**: R >100 ms improved by 35%, and both I bands got clearly worse.

Measurement conditions, recorded as they were: Windows 11 Home, Ryzen 9 7845HX, 32 GB, 100% display scaling, "Balanced" power plan; **a Windows Update restart was pending when the run started**, one WeChat process remained, and the OneDrive pause was not verified. 12 of 12 formal rounds, 0 crashes, 0 timeouts; S1 `--verify` 6/6 `ok`; S3 reported `ok=false` as the runbook predicts while `valid_round` / `I_valid` were true; both `--aggregate` runs exited 0.

### Both platforms share one regression: opening a task got slower

This release replaced some whole-file reads on the open path with header-and-index reads (P2-13/P2-14), yet **"opening a task" is slower than 0.8.1 on both platforms**: macOS I >500 ms is **+17.6%** and Windows I >500 ms is **+42.8%**. That is **the top performance goal for 0.8.3**; the R-phase improvement does not make this release "smoother" overall.

### Windows stability (P2-21 / P2-21b)

Before the fix, CI S3 rounds crashed about 2 in 10 to 7 in 20; after it, the product default is **0 crashes in 20 rounds with every round exiting 0** (run `44076100998`, `e5f5941d`), while the positive control with cross-thread frame capture still reproduced 6/20.

Evidence: [the final G2 / E-7 measurements](evidence/0.8.2-g2-final.md); raw numbers in `.loop/reports/numbers-for-release-notes.md`, `.loop/reports/numbers-windows-g9.md` and `.loop/real-run-g7/`.

---

## Known issues

1. **Edits cannot be saved while a translation is running.** The draft is kept, but submitting is refused with "the task is being processed in the background; save after it finishes". This prevents the worker and a human from writing the same segment concurrently.
2. **SDL / memoQ files are export-unchanged only.** No write-back, no UI entry for it. Generic XLIFF 1.2 does write translations back.
3. **Windows verification covers smoke and fluency only.** No 30-round stress, no 8-hour run, no sleep/wake, no native per-item acceptance of the 0.8.1 UI on Windows.
4. **G2 did not reach −70%.** Do not read it as "faster".
5. **The native crash when a large task opens on Windows (P2-21) is fixed.** The 20-round recheck on main: the product default crashed **0/20**, while the positive control with cross-thread frame capture still crashed **6/20** (run `44076100998`). Both root causes are fixed — the sampler no longer drags the inspected thread's frames into a reference cycle ([#443](https://github.com/xueyang-dev/Folith/pull/443)) and no longer walks other threads' frames by default ([#446](https://github.com/xueyang-dev/Folith/pull/446); `FOLITH_JANK_OTHER_THREADS=1` enables it). Discriminator run on Windows CI, three arms x 20 rounds: the old default crashed **7/20**, main-thread-only sampling was **0/20** with every round exiting 0. **The trade-off**: jank records no longer include other threads' stacks by default, so only the main thread's own stalls are visible; set `FOLITH_JANK_OTHER_THREADS=1` when you need the all-thread ranking (diagnosis, the gate 8 measurement). `FOLITH_JANK_MONITOR=0` still turns the sampler off entirely, but do not use it for measurements: the runner records the round as failed.
6. **Quitting in subprocess mode occasionally takes a second click.** With `FOLITH_ENGINE_PROCESS=1` on Windows, quitting can report "stopping the task failed" and keep the app open, or the next launch can show that task as an unexpected interruption. **The task and its data are unaffected** (progress is on disk and the task stays resumable as `interrupted`); quit once more. Only this opt-in switch is affected; fixed in 0.8.3 ([#453](https://github.com/xueyang-dev/Folith/issues/453)).
7. **On Windows, the "freeze delivery" hint is clipped at the window's left edge the first time the delivery page opens ([#520](https://github.com/xueyang-dev/Folith/issues/520)).** The text is there and the feature works; widening the window shows all of it. Delivery results are unaffected; fixed in 0.8.3.
8. **Slow startup ([#514](https://github.com/xueyang-dev/Folith/issues/514)).** With many tasks and delivered books, double-click to window takes more than ten seconds. Measured on macOS with 21 tasks and a 609 MB data directory: **about 16 s**, mostly the home task list being built **synchronously before the window appears** (`refresh_home -> adapter.list_jobs()`). With few tasks it is not noticeable (a fresh Windows data directory cold-started in 6.3 s). **0.8.1 has the same code path** — `refresh_home` and `list_jobs` are identical in the 0.8.1 tree — so this is not something 0.8.2 introduced. Fixed in 0.8.3 with a target of **under 3 s** on the same data set.
9. **Switching to the projects page stalls ([#515](https://github.com/xueyang-dev/Folith/issues/515)).** With a lot of data the projects page takes a long time and the interface freezes meanwhile. Measured on the same data: the overview is **33 s cold and 16 s warm**, with the longest interface pause around **1.4 s**. **0.8.1 behaves the same way** — `refresh_projects` and `projects_overview` are unchanged — because the overview is recomputed in full. Fixed in 0.8.3, together with a skeleton transition ([#516](https://github.com/xueyang-dev/Folith/issues/516)).
10. **No Trados / memoQ round trip** for dialects or generic files.
11. **The macOS build is still unsigned and not notarized.**
12. Offscreen tests, mocks and unit tests do not stand in for a real window.
13. **With a lot of data the interface occasionally stops responding for seconds.** Opening or refreshing a whole book copies the task data in the background and reclaims memory, and the GUI thread does not get scheduled meanwhile: one measured macOS stall was **7.65 s**. **0.8.1 behaves the same way** (the `_isolated_copy` pickle round trip is identical), and no data is at risk; wait for it to recover instead of force-quitting. Fixed in 0.8.3.

## Moved to 0.8.3

8-hour run and S4; the five-operation latency budget; 20 consecutive runs; sleep/wake; a real-book comparison; 30-round stress on both platforms; native Windows acceptance of the 0.8.1 UI; TM/TB import wizard on real windows with a real TMX; understanding-stage timing on a real book (#319); #220 item B5; **flipping the engine subprocess default on** (needs a real-machine long comparison first); the G2 −70% target itself.
