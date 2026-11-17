# Folith 0.8.2 发布说明

状态：**发布候选，未打标签、未上传安装包。** **发布日期：待定（P2-21 修复合入后）。** 用户 2026-11-17 裁定：P2-21 修好之前不发布；打标签和公开发布由用户在验收完成后决定。

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
| 3 | macOS 冻结包冒烟（安装、翻译到交付、XLIFF 导入导出、重开任务还在、5 轮加压） | **已做** | 见 [macOS 冻结包冒烟证据](evidence/0.8.2-macos-frozen-smoke.md)；**XLIFF 子项待用户在冻结包界面补核**（冻结包没有 CLI 入口，包内人工往返未做，自动化套件覆盖） |
| 4 | Windows 冻结包冒烟（同第 3 条、3 轮加压、退出无残留进程、界面无明显错位） | **待用户** | 步骤见 [Windows 冒烟清单](release/0.8.2-windows-smoke-checklist.md) |
| 5 | 已知问题与发布说明如实 | **本文件即交付** | 性能数字已填（第 7、8 条，`e5f5941d`） |
| 6 | 版本号、标签、发布 | **版本号 PR 已就绪**（#433） | 发布当天与说明一起合；打标签与公开发布等用户点头 |
| 7 | G2 流畅度 −70% | **已测，未达** | R >100 ms −30.8%、R >500 ms 不可判定、I >100 ms +2.0%、I >500 ms +17.6%，见「性能」 |
| 8 | E-7 进程内 / 子进程分阶段对照 | **已完成** | R >100 ms 进程内比子进程高 9.1%，R >500 ms 两臂 0；全线程排行见「性能」 |
| 9 | Windows 流畅度两臂对照 | **待用户** | Windows 真机；这一版没有 Windows 流畅度数字 |

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

Windows 的第 9 条（流畅度）由用户在真机跑，**这一版没有 Windows 流畅度数字**。Windows 上唯一有数字的是 P2-21 / P2-21b 的崩溃率：修复前 CI 的 S3 每轮约 2/10 ~ 7/20 概率整轮崩溃；修复后产品默认配置 **20 轮 0 崩溃、收尾退出码全 0**（run `44076100998`，`e5f5941d`），阳性对照（打开跨线程取帧）仍有 6/20 复现，说明因果成立。

证据：[G2 / E-7 最终测量](evidence/0.8.2-g2-final.md)（数字原件在 `.loop/reports/numbers-for-release-notes.md` 与 `.loop/real-run-g7/`）。

---

## 已知问题

1. **翻译进行中不能保存人工编辑。** 任务在后台跑时，编辑框里写的内容可以保留为草稿，但提交保存会被拒绝并提示「任务正在后台处理，请等待完成后保存」。这样做是为了避免后台 worker 和人工同时改同一段导致互相覆盖。想改译文，请先暂停或等这一轮跑完。
2. **SDL / memoQ 文件只能原样导出。** `.sdlxliff`、`.mqxliff` 不会写回译文，界面上也没有这个入口；要更新译文，请回 Trados / memoQ 处理。通用 XLIFF 1.2 可以写回译文。
3. **Windows 的验证范围只有冒烟和流畅度。** 这一版没有做 30 轮加压、没有做 8 小时长跑、没有做休眠唤醒，也没有做 0.8.1 界面的 Windows 原生逐项验收。Windows 上「能装、能打开、能翻完一份小文档并交付、XLIFF 能进出、退出没有残留进程」以冒烟清单为准；性能以门槛 9 的双臂对照为准。
4. **G2 流畅度没有达到 −70%**（见「性能」）。不要读成「变快了」。
5. **Windows 上打开大任务的原生崩溃（P2-21）已修复。** main 上 20 轮复核：产品默认口径 **0/20 崩溃**，打开跨线程取帧的阳性对照仍 **6/20**（run `44076100998`）。 两个根因都修了——采样器不再把被检查线程的帧拖进循环垃圾（[#443](https://github.com/xueyang-dev/Folith/pull/443)），也不再默认跨线程取帧（[#446](https://github.com/xueyang-dev/Folith/pull/446)；`FOLITH_JANK_OTHER_THREADS=1` 才打开）。判别实验（Windows CI，三臂 × 20 轮）：旧默认 **7/20 崩溃**，只采主线程 **0/20** 且每轮 `exit=0`。**代价**：界面卡顿记录默认不再包含其他线程的栈，只看得到主线程自己的停顿；需要全线程栈排行时（诊断、第 8 条测量）设 `FOLITH_JANK_OTHER_THREADS=1`。`FOLITH_JANK_MONITOR=0` 仍然全关，但测量里不要用（runner 会把整轮记成失败）。
6. **子进程模式的退出偶发要再点一次。** 打开 `FOLITH_ENGINE_PROCESS=1` 后，Windows 上退出时偶尔会提示「停止任务失败」并留在应用里，或者在下次打开时把这条任务显示成「意外中断」。**任务与数据都不受影响**（进度已落盘、任务仍是可继续的 `interrupted`），再点一次退出即可。只影响这个默认关闭的开关；0.8.3 修。
7. **没有在 Trados 或 memoQ 里做过往返。** 方言样本与通用样本都是这样；原样导出的字节一致有测试与走查，但没有目标工具回读记录。
8. **macOS 安装包仍未签名、未公证。** 首次打开需要用户手动放行。
9. 离屏测试、mock 与单元测试不能代替真实窗口。

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

Status: **release candidate, no tag and no uploaded installers.** Tagging and publishing happen after the user signs off.

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
| 3 | macOS frozen-app smoke | **done** — see the [macOS frozen-app smoke evidence](evidence/0.8.2-macos-frozen-smoke.md); the **XLIFF sub-item waits for the user to recheck it in the frozen build's UI** (no CLI entry, so the in-app round trip was not run; the automated suite covers it) |
| 4 | Windows frozen-app smoke | **pending user** — see the [Windows smoke checklist](release/0.8.2-windows-smoke-checklist.md) |
| 5 | Known issues and notes are honest | **this document**; the performance section now carries the gate 7 and 8 numbers |
| 6 | Version, tag, release | **version PR ready** (#433); merges with these notes on release day, tag/publish waits for the user |
| 7 | G2 −70% | **measured, not met** — R >100 ms -30.8%, R >500 ms not decidable, I >100 ms +2.0%, I >500 ms +17.6% |
| 8 | E-7 in-process vs subprocess comparison | **done** — R >100 ms is 9.1% higher in-process, R >500 ms zero on both arms |
| 9 | Windows fluency, both arms | **pending user** — this release carries no Windows fluency numbers |

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

Gate 9 (Windows fluency) runs on the user's machine, so **this release carries no Windows fluency numbers**. The one Windows number that exists is the P2-21 / P2-21b crash rate: before the fix, CI S3 rounds crashed about 2 in 10 to 7 in 20; after it, the product default is **0 crashes in 20 rounds with every round exiting 0** (run `44076100998`, `e5f5941d`), while the positive control with cross-thread frame capture still reproduced 6/20.

Evidence: [the final G2 / E-7 measurements](evidence/0.8.2-g2-final.md) (raw numbers in `.loop/reports/numbers-for-release-notes.md` and `.loop/real-run-g7/`).

---

## Known issues

1. **Edits cannot be saved while a translation is running.** The draft is kept, but submitting is refused with "the task is being processed in the background; save after it finishes". This prevents the worker and a human from writing the same segment concurrently.
2. **SDL / memoQ files are export-unchanged only.** No write-back, no UI entry for it. Generic XLIFF 1.2 does write translations back.
3. **Windows verification covers smoke and fluency only.** No 30-round stress, no 8-hour run, no sleep/wake, no native per-item acceptance of the 0.8.1 UI on Windows.
4. **G2 did not reach −70%.** Do not read it as "faster".
5. **The native crash when a large task opens on Windows (P2-21) is fixed.** The 20-round recheck on main: the product default crashed **0/20**, while the positive control with cross-thread frame capture still crashed **6/20** (run `44076100998`). Both root causes are fixed — the sampler no longer drags the inspected thread's frames into a reference cycle ([#443](https://github.com/xueyang-dev/Folith/pull/443)) and no longer walks other threads' frames by default ([#446](https://github.com/xueyang-dev/Folith/pull/446); `FOLITH_JANK_OTHER_THREADS=1` enables it). Discriminator run on Windows CI, three arms x 20 rounds: the old default crashed **7/20**, main-thread-only sampling was **0/20** with every round exiting 0. **The trade-off**: jank records no longer include other threads' stacks by default, so only the main thread's own stalls are visible; set `FOLITH_JANK_OTHER_THREADS=1` when you need the all-thread ranking (diagnosis, the gate 8 measurement). `FOLITH_JANK_MONITOR=0` still turns the sampler off entirely, but do not use it for measurements: the runner records the round as failed.
6. **Quitting in subprocess mode occasionally takes a second click.** With `FOLITH_ENGINE_PROCESS=1` on Windows, quitting can report "stopping the task failed" and keep the app open, or the next launch can show that task as an unexpected interruption. **The task and its data are unaffected** (progress is on disk and the task stays resumable as `interrupted`); quit once more. Only this opt-in switch is affected; fixed in 0.8.3.
7. **No Trados / memoQ round trip** for dialects or generic files.
8. **The macOS build is still unsigned and not notarized.**
9. Offscreen tests, mocks and unit tests do not stand in for a real window.

## Moved to 0.8.3

8-hour run and S4; the five-operation latency budget; 20 consecutive runs; sleep/wake; a real-book comparison; 30-round stress on both platforms; native Windows acceptance of the 0.8.1 UI; TM/TB import wizard on real windows with a real TMX; understanding-stage timing on a real book (#319); #220 item B5; **flipping the engine subprocess default on** (needs a real-machine long comparison first); the G2 −70% target itself.
