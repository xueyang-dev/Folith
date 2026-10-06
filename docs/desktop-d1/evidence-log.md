# D1 桌面工程证据日志（Lifecycle & Data Safety）

路线图：`docs/desktop-client-roadmap.md`（v1.1）。D0 的证据与决策见 `docs/desktop-d0/`。
本文件是审计轨迹，不替代测试。

## 产品负责人决定（记录在案）

2026-10-03：用户明确指示 **先在这台 macOS 机器上推进 macOS 侧开发，Windows 之后另行解决**。
据此：macOS D1 工程继续；**不宣称双端成立**（roadmap §6.2 仍然有效），Windows 的 D0 切片保持 BLOCKED。

## D1 范围（roadmap §3.2 / §3.3 / §3.5、§6 里程碑表）

| 要求 | 状态 |
| --- | --- |
| 稳定数据目录（§3.2） | D0 已完成（macOS 默认位置，导入 core 前设置） |
| 应用单实例 + 窗口唤起（§3.1.4） | **本轮完成**（见下） |
| save & stop：未失焦编辑、运行任务停止调度 + 有界等待（§3.3） | 未开始（D1 主项） |
| 崩溃恢复（复用 interrupted / stalled / 检查点） | 未开始（D0 已证 journal 未损坏） |
| 旧数据目录接入（§3.2） | 未开始 |
| 构建 / 运行时诊断（§3.5） | 未开始（build record 已具备） |
| 应用级退出（Cmd+Q / Dock，D0 发现） | 未开始 |

---

## D1-1 — 应用级单实例锁与窗口唤起

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.1.4「D1 增加应用级系统锁与窗口唤起；不能把任务锁当应用锁」）
- 假设：应用锁与任务锁必须分离；崩溃后锁必须可恢复；第二个实例不得启动第二个服务
- 变更：
  - 新增 `folith/desktop/single_instance.py`：`AppInstanceLock`（复用 `folith/job_lock` 的 OS 文件锁）+ `InstanceRecord`（instance_id / launcher_pid / service_pid / port / url / 版本 / build）+ `activate_existing_window()`（macOS 用 pyobjc `NSRunningApplication` 激活，失败回退 `open` bundle；Windows 预留 best-effort 分支）
  - `folith/desktop/paths.py`：新增 `state_dir` / `instance_lock` / `instance_record`（**位于 data_dir 之外**：机器本地进程状态，不是用户项目数据）
  - `gui.py`：桌面启动先取应用锁；取不到则唤起已运行窗口并退出码 **10**；启动成功后发布记录；所有退出路径释放锁
  - `gui.py`：启动前若发现**上一个实例的服务仍存活**（崩溃/强杀后的退出窗口），有界等待其退出（25s），绝不杀死旧进程；仍存活则退出码 **11** 并提示稍后重试
  - `gui.py`：`sys.stdout/stderr` 行缓冲，终端用户立即看到横幅与失败原因
- 设计要点（证据驱动）：
  - **OS 锁是权威**：进程死亡时内核自动释放，因此崩溃不会留下不可恢复的锁；
  - **记录只是提示**：`live_record()` 校验 launcher_pid 是否存活，死 PID 绝不当作"正在运行"；
  - **不误杀**：只等待、不终止非本次持有的进程（roadmap §3.1.4）。
- 测试与结果：

| 测试 | 结果 |
| --- | --- |
| `tests/desktop_single_instance_test.py`（8 项：互斥、释放后可重用、记录往返、死 PID 不视为运行、publish 需持锁、state_dir 在用户数据之外、等待旧服务且不杀它、旧服务存活时拒绝启动并释放锁） | **8 passed** |
| 真实两次启动实验（冻结 `.app`，同一数据目录） | **PASS**：B 退出码 10，打印已运行实例 / 端口 / 启动时间 / 窗口唤起方式，服务数仍为 1 |
| 窗口唤起（Finder 启动的实例） | **PASS**：`窗口唤起：macos-activated`（pyobjc 激活已运行应用） |
| 窗口唤起（终端启动的实例） | **PASS（回退路径）**：`窗口唤起：macos-open-bundle` |
| 崩溃竞态（`kill -9` launcher 后立即重启） | **PASS**：B 打印「上一个实例的服务进程仍在退出，等待其释放数据目录…」，25 次采样 **最大并发服务数 = 1** |
| 正常退出 / SIGTERM 后锁释放 | **PASS**：SIGTERM 路径记录文件残留但 OS 锁已释放；重启可正常获取并重新发布记录（服务数 1） |
| 全量桌面门禁（单实例 8 + 冻结门禁 3 + 握手 14 + gui 启动器 6） | **31 passed** |

- 证据（原始输出）：
  - B（第二次启动，冻结产物）：
    `[提示] 译页已经在运行，本次不再启动第二个服务。` / `已运行实例：250e4e4d7f3c…` / `端口：58725` / `窗口唤起：macos-open-bundle` / `exit=10`
  - 竞态：`kill -9` 后 B 输出 `[提示] 上一个实例的服务进程仍在退出，等待其释放数据目录…`，随后正常启动（`服务 PID：14694 端口：58993`）；`max simultaneous services = 1`
  - 记录文件内容示例：`{"instance_id": "f9e910196878469e8d99a1920f0c7b52", "launcher_pid": 14633, "service_pid": 14640, "port": 58993, ...}`
- 剩余风险 / 后续：
  - 记录文件在 `SIGTERM` / Cmd+Q 路径会残留（finally 未执行），但锁已释放、下次启动会覆盖——属预期设计；D1 的"应用级退出"任务会让正常退出路径也走完清理。
  - Windows 的窗口唤起分支为 best-effort 且**未在 Windows 实测**（无主机），标记 NOT YET TESTED。
- 下一步：D1 主项 **save & stop**（未失焦编辑同步 + 运行任务停止调度与有界等待）。

---

## D1-2 — save & stop：实现前审计结论（下一单元的输入）

已核对以下事实，作为实现依据（只读审计，未改代码）：

1. **pywebview 关闭事件是同步的**：`webview/window.py:163` 用 `Event(self, True)` 创建 `events.closing`（`should_lock=True`），`platforms/cocoa.py:1315` 在 `shouldClose` 里同步调用，返回 `True` 表示**取消关闭**。
   → 设计约束：`closing` 处理器必须立刻返回，不能在其中调用 `evaluate_js`（需 GUI 线程处理 JS，会自锁）。
   → 采用方案：`closing` 处理器返回 `True`（取消本次关闭）并在**工作线程**里执行"同步未失焦编辑 → 提交 → 停止服务"，结束时由该线程调用 `window.destroy()`；第二次 `closing` 事件看到"已完成"再返回 `False` 放行。
2. **草稿文件是既有真值，不另建状态**：`core.save_translation_drafts()`（`core.py:10355`）写入 `<job>/translation_drafts.json`，结构 `{version, job_id, updated_at, drafts:[{segment_id, text, baseline, updated_at}]}`；无草稿时**删除文件**；`core.load_translation_drafts()`（`core.py:10321`）读不到/损坏一律返回空。
   → 退出流程按 `segment_id` 读取该文件（只读），并复用既有"提交编辑"路径，不新建草稿真值。
3. **DOM 映射可行**：编辑器容器 key 为 `cat_editor_{job}_{index}`，输入框 widget key 为 `translation_editor_{segment_id}`（可能带 `#generation` 后缀），行容器 `cat_row_{job}_{index}`，提交按钮 key `cat_save_btn_{job}_{index}`。
   → 由 `segment_id` 定位输入框，再向上找 `cat_row` 内的保存按钮点击，即可提交该草稿；提交后再次读草稿文件确认已清空，才允许退出。
4. **运行任务状态可读**：`<job>/runtime_state.json`（`status` / `stage` / `operation`）已在 D0 退出矩阵中验证可用于判断"是否正在运行"。
   → 有运行任务时：先请求既有取消语义（UI 的 `runtime_cancel_{job}`），有界等待 worker 停止并释放锁，再停止服务；超时则明确提供"继续等待 / 强制退出"。

下一单元（D1-2a）：实现 `folith/desktop/exit_flow.py`（读取待提交草稿 / 判断运行中任务 / 通过真实 UI 提交 / 提交后校验）并接入 `closing`，用真实 WKWebView 自动化脚本取得端到端证据。

---

## D1-2a — save & stop：未失焦编辑在关窗时落盘（已实现并端到端验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「有未保存的编辑：先同步 WebView 中尚未失焦的 textarea…取得实际落盘确认后才退出，失败不能显示已保存」）
- 变更：
  - 新增 `folith/desktop/exit_flow.py`：只读既有 `<job>/translation_drafts.json` 与 `<job>/runtime_state.json`；用真实 UI 按钮提交草稿并**再次读文件确认**；worker 锁状态用同一把 OS 锁（`folith.job_lock`）判定，不看文件是否存在
  - `gui.py`：`open_native_window(..., on_closing=...)` 注册 `window.events.closing`；处理器返回 `False`（取消关闭）并起工作线程执行"冲刷 → 提交 → 校验"，确认落盘后才 `destroy()`；若仍有未提交草稿或 worker 未停止，**取消退出**并提示（不谎报已保存）
  - 新增 `scripts/desktop_d1_exit_probe.py`（真实窗口内制造"未失焦编辑"再触发关闭）与 `scripts/d1_exit_runner.py`（编排 + 磁盘校验）
  - 新增 `tests/desktop_exit_flow_test.py`（11 项，含与 `core.RUNTIME_ACTIVE_STATUSES` 的一致性断言）
- 关键技术事实（实测/代码核对）：
  1. `window.events.closing` 是 `should_lock=True`（同步、GUI 线程），处理器返回**恰好 `False`** 表示取消关闭（`event.py` 的 `false_values` 判定 + `cocoa.py:1310 should_close`）；
  2. **`window.destroy()` 绕过关闭委托**（`cocoa.py:758` 直接 `NSWindow.close`），因此退出流程挂在真实关闭路径上；自动化探针通过 `window.events.closing.set()` 走同一条语义（`closing_cancelled: true`）；
  3. 草稿真值仍是既有文件，退出流程不写任何 job 状态。
- 端到端结果（冻结 `.app`，真实窗口，job `50eaaaeb1b7f1654`）：

| 阶段 | 观测 |
| --- | --- |
| 编辑前 | `target = "D1 基线编辑 BASELINE"`，草稿 0 |
| 未失焦输入 | `focused: true`，27 字符；**草稿仍为 0**（值只在 WebView 里） |
| 触发关闭 | `closing_cancelled: true` → 退出流程接管 |
| 冲刷 | `flush.flushed: true`，草稿变为 1（`seg-50eaaaeb1b7f1654-0000`） |
| 提交 | `committed_segments: [seg-…-0000]`，`remaining: 0`，`confirmed: true`，无失败 |
| 判定 | `safe_to_exit: true`，`action: saved-and-stopped` |
| 磁盘 | `target = "D1 未失焦编辑 SAVE-AND-STOP 2026"`（未失焦内容已落盘）、`human_edited: [0]`、草稿文件已删除、worker 锁空闲、服务进程 0 |
| 退出 | 认证停止成功（`acknowledged=true`），启动器输出 `退出前检查：提交草稿 1 处；剩余草稿 0 处` |

- 测试与结果：
  - `tests/desktop_exit_flow_test.py` → **11 passed**
  - 全量桌面门禁（退出流程 11 + 单实例 8 + 冻结 3 + 握手 14 + gui 启动器 6）→ **42 passed**
  - 产品回归子集（`ui_console_test` / `translation_save_flow_ui_test` / `delivery_snapshot_test` / `persistence_phase_a_test`）→ **64 passed**
- 诚实标注（未完成 / 未验证）：
  - **"继续等待 / 强制退出"选择对话框未实现**：当前超时后是"取消退出 + 提示重试"（不谎报成功）；原生对话框属下一单元。
  - **真实运行中任务的取消路径只做了单元验证**（模拟 running 状态 → 点击 `runtime_cancel` → 等待离开 active + worker 锁释放）；尚未用真实长任务在窗口内验证。
  - **系统关闭按钮（红点 / Cmd+W）→ 委托** 由代码核对确认（`cocoa.py:91 windowShouldClose_` → `should_close` → `events.closing`）；本会话无辅助功能授权（AppleEvent `-1712`），无法脚本化点按钮，故探针调用同一语义的 `events.closing.set()`。
  - 本单元的 setup 切片 8/9 步：`freeze-delivery` 被**既有交付门禁**拦下（页面显示「交付被 Translation Target Invariant 阻止」），原因是夹具基线编辑文本 `D1 基线编辑 BASELINE` 含拉丁残留触发确定性检查——**门禁按设计工作、未被绕过**；D1 本单元不依赖交付，故不影响结论。
- 下一步：D1-2b —— 运行中任务的真实窗口验证 + "继续等待 / 强制退出"选择；随后 D1-3 应用级退出（Cmd+Q）。

---

## D1-2b — save & stop：运行中任务在关窗时停止调度并回收（已实现并端到端验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「有运行任务：停止安排新操作，有界等待当前操作，复用检查点与 journal；worker 停止并释放锁后再提交编辑和停止服务」+「等待超过上限：明确提供继续等待或强制退出」）
- 变更（`folith/desktop/exit_flow.py`、`gui.py`）：
  1. **任务发现不再只看 URL**：新增 `active_job_ids(data_dir)`；窗口停在首页（或别的任务）时，退出流程从数据目录扫描处于 active 状态的任务，并用产品自身路由 `navigate_to_job()` 切到该任务，使取消控件存在——这是第一轮实验暴露的真实缺口（首轮 `cancel` 为空，因为窗口 URL 里没有 `?task=`）。
  2. **超时选择**：有界等待后若仍不安全（草稿未提交 / 任务仍在停止 / worker 仍持锁），弹出原生确认对话框提供「强制退出 / 返回继续等待」（`confirm_force_exit`，可用 `FOLITH_EXIT_FORCE_PROMPT=0` 在自动化中关闭，避免模态阻塞）；强制退出在报告里显式标记 `forced`，并且只承诺已落盘内容可恢复。
  3. **退出流程逐步记录 + 结果落盘**：`run_exit_flow(..., on_step=...)` 写入 `exit flow step <name>`，结束后写 `<log_dir>/exit-flow.json`（诊断与证据都需要，且进程被切断时仍可取证）。
- 端到端结果（冻结 `.app`，真实窗口，job `684deda1300eecf4`）：

| 阶段 | 观测 |
| --- | --- |
| 任务中途 | `runtime_status: running`、`stage: translation`、journal 已落盘 4 段 |
| 关闭触发 | 探针调用真实关闭路径 → 退出流程接管 |
| 流程步骤 | `locate-job → navigate-to-job → flush-editor → cancel-task → commit-drafts → final-check`（全部执行） |
| 取消 | `cancel_clicked: true`（scoped 控件 `runtime_cancel_<job>`）、`left_active_status: true`、`worker_lock_released: true`、最终 `status: cancelled` |
| 停止后 | 8 段译文已保存进 `state.json`、`delivery_status: draft`（**未伪完成**）、worker 锁空闲、服务进程 0、应用退出码 0 |
| 启动器输出 | `退出前检查：提交草稿 0 处；剩余草稿 0 处；取消运行任务：是；worker 锁释放：是；任务来自数据目录扫描（窗口未停留在该任务）` |

- 过程中发现并修掉的**工具链/架构约束**（不是产品缺陷，但会误导后续验证）：
  1. **自动化回调返回即结束应用**：`webview.start(func)` 里 `func` 返回后应用随即退出（实测三次一致），因此探针必须**活到退出流程结束**——`desktop_d1_running_probe.py` 现在等待 `<log_dir>/exit-flow.json` 出现再返回；否则流程会在 `cancel-task` 中途被切断（这正是首轮 `exit-flow.json` 缺失、`cancel` 为空的原因）。
  2. 运行任务期间 `runtime_status` 可能是 `waiting_external`（等待外部模型响应），也在 `ACTIVE_STATUSES` 内，退出流程必须按"仍在运行"处理。
- 测试与结果：
  - `tests/desktop_exit_flow_test.py` → **14 passed**（新增：数据目录任务发现、窗口未显示任务时的取消、超时选择的两条分支）
  - 全量桌面门禁 → **45 passed**
  - 产品回归子集（`ui_console_test` / `persistence_phase_a_test` / `release_e2e_acceptance_test`）→ **29 passed**
  - `scripts/d1_running_exit_runner.py` → **OK: true**（可重复）
- 诚实标注：
  - **原生"继续等待 / 强制退出"对话框本身未被自动化点击验证**（模态需要人工）；两条分支由单元测试覆盖（注入 confirm 回调），自动化运行中该对话框被显式关闭。
  - 暂停（paused）状态、模型请求进行中强制退出的重复计费语义仍未单独实测（`paused` 属 ACTIVE_STATUSES，逻辑同运行中）。
- 下一步：D1-3 应用级退出（Cmd+Q / Dock 走完整 save & stop，而不是靠 stdin EOF 兜底）。

---

## D1-3 — 应用级退出：Cmd+Q / Dock 走同一套 save & stop（已验证）

- 时间：2026-10-03
- 里程碑：D1（D0 发现：`quit app` 绕过启动器认证停止流程，只靠 stdin EOF 兜底；roadmap §3.3 要求正常退出走"保存并停止"）
- 关键发现（代码核对，改变了原判断）：**pywebview 的 macOS AppDelegate 已实现 `applicationShouldTerminate_`**（`platforms/cocoa.py:66`），它调用 `BrowserView.should_close()` → `window.events.closing.set()`（`cocoa.py:1310`）。也就是说 Cmd+Q / Dock 退出与关闭按钮**走同一条 `closing` 通道**；D0 之所以看到 EOF 兜底，是因为当时**还没有注册任何 closing 处理器**（无处理器时 `set()` 返回 False → 直接放行终止）。D1-2 注册处理器后，退出事件自然被接管。
- 变更：
  - 新增 `scripts/desktop_d1_quit_probe.py`：真实 LaunchServices 启动下制造"未失焦编辑"并驻留等待外部退出事件（探针返回会结束应用，因此必须活到退出完成）；
  - `scripts/d1_exit_runner.py` 增加 `--trigger quit`：`open --env ... <bundle> --args --automation-script ...`（Finder 等价启动 + 临时数据目录）→ 等 sentinel → `osascript -e 'quit app "Folith"'`（真实 Apple Event）→ 校验；
  - 新增回归测试：`open_native_window` 必须把 `on_closing` 注册到 `events.closing`，并同时断言 `ALLOW_DOWNLOADS` 被开启。
- 端到端结果（冻结 `.app`，真实窗口，job `1783755ef5d3272a`，真实 quit 事件）：

| 观测 | 值 |
| --- | --- |
| 启动方式 | `open --env … dist/desktop/Folith.app --args --automation-script …`（LaunchServices，Finder 等价） |
| 触发 | `osascript -e 'quit app "Folith"'` → Apple Event `kAEQuitApplication`（`quit` 返回码 1 = 本次退出被拦截，属预期） |
| 未失焦编辑 | `typed: {ok: true, focused: true, value_length: 27}`，sentinel 已写出 |
| 退出流程 | 启动器日志逐条记录：`exit flow start → locate-job → flush-editor → commit-drafts → final-check`（10:45:36–10:45:37） |
| 结果 | `action: saved-and-stopped`、`safe_to_exit: true`、`commit.committed_segments: [seg-…-0000]`、`confirmed: true` |
| 磁盘 | `target = "D1 未失焦编辑 SAVE-AND-STOP 2026"`、`human_edited: [0]`、草稿文件已删除、worker 锁空闲 |
| 退出 | `launch stop`（10:45:38，认证停止）；**服务日志不再出现 `control channel closed; stopping service`**（EOF 兜底），仅正常 `Stopping...`；无残留进程 |
- 对比证据（同一动作、修复前后）：D0 阶段 `quit app` → 无 `launch stop` 记录 + 服务日志出现 EOF 兜底；本轮 → `launch stop` + 完整退出流程步骤 + 无 EOF 兜底。
- 测试与结果：
  - `tests/desktop_exit_flow_test.py` → **15 passed**（新增 closing 注册/下载开关回归）
  - 全量桌面门禁 → **46 passed**
- 诚实标注：
  - 菜单 Cmd+Q 与 Dock 退出共用同一 Apple Event 路径，本次用 `quit app` 覆盖；键盘快捷键本身仍需人工按键确认（无辅助功能授权）。
  - 退出被拦截后由应用自行退出，用户会看到约 1–2 秒的保存延迟；这是有意的（先落盘再退出）。
- 下一步：D1-4 旧数据目录接入（roadmap §3.2）或 D1-5 诊断（§3.5）。

---

## D1-5 — 构建 / 运行时诊断与脱敏导出（已实现并验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.5）
- 变更：
  - 新增 `folith/desktop/diagnostics.py`：
    - `collect()` 汇总 §3.5 全部字段：app version / build id / commit / built_at / packaging backend / frozen、Python / Streamlit / pywebview / PyInstaller 版本、OS / OS version / architecture、用户数据目录与日志目录、任务数、数据 schema 版本；
    - **schema 版本如实记录**：`job_state=unversioned`；`runtime` / `translation_drafts` / `journal` 分别给出 `declared`（能廉价导入的常量）与 `observed`（**从实际持久化文件读出的值**）并注明来源；不为诊断新建状态格式；
    - `redact_path()` / `_redact_text()`：家目录 → `~`（含日志行中间的路径）、用户名 → `<user>`、`api_key/token/secret/password` 键值 → `<redacted>`；
    - `write_diagnostics()` 在桌面启动时写 `<log_dir>/diagnostics.json`；`export_bundle()` 生成可随工单提交的脱敏诊断包；
  - `gui.py`：启动横幅后写诊断文件；新增 `--export-diagnostics PATH`（不启动服务/窗口，导出后退出）。
- 冻结产物实测（`dist/desktop/Folith.app`）：
  - `Folith --export-diagnostics /tmp/d1-diag/export.json` → 退出码 0，内容含：
    `version 0.5.0`、`build_id git:1260a82`、`commit 1260a826…`、`packaging_backend pyinstaller`、`frozen true`、`target macos/arm64`、
    `python 3.11.3`、`streamlit 1.64.0`、`pywebview 6.2.1`、`pyinstaller 6.22.3`、`Darwin / macOS-27.0.1-arm64-arm-64bit / arm64`、
    `job_state unversioned`、`runtime.observed "2"`、`journal declared 1 / observed 1`、`translation_drafts.observed 1`
  - **泄漏检查**：在数据目录预埋 `provider_config.json`（`api_key: sk-SHOULD-NOT-LEAK-999`）与文档内容 → 导出包中 **0 次**出现该凭据、`provider_config`、文档文本；`Path.home()` 与用户名均不出现。
  - 日志错误摘要：预埋 `ERROR … api_key=sk-LOGLEAK-777` → 导出为 `ERROR boom at ~/Dev/… api_key=<redacted>`（家目录折叠、凭据脱敏、普通日志行被过滤）。
  - 启动写入：`--no-browser` 启动后 `<log_dir>/diagnostics.json` 存在，横幅打印 `诊断：<path>`。
- 测试与结果：
  - `tests/desktop_diagnostics_test.py` → **7 passed**（字段完整性、`job_state=unversioned`、**observed 与 `core.RUNTIME_SCHEMA_VERSION` / `TRANSLATION_DRAFTS_VERSION` / `JOURNAL_SCHEMA_VERSION` 一致性**、错误摘要过滤与脱敏、**预埋凭据/文档内容不得出现在导出包**、禁止读取 `provider_config.json` / `.onboarded` 的守卫）
  - 全量桌面门禁 → **53 passed**；产品回归子集（`ui_console_test` / `release_e2e_acceptance_test`）→ **12 passed**
- 诚实标注 / 剩余风险：
  - 日志行是应用写下的文本，可能包含用户自选路径的内部片段（如项目目录名）；导出只保证家目录 / 用户名与凭据被移除，提交前用户仍应过目。
  - 暂无 UI 诊断页（roadmap 允许"本地诊断页**或日志**"）；`--export-diagnostics` + 启动诊断文件已满足 D1，诊断页可作 D2 打磨。
  - `runtime` / `translation_drafts` 的 `declared` 为 null：这两个常量在重型 `core` 中，桌面侧不为读常量拖慢启动；一致性由测试对 `core` 常量断言（observed == 常量）。
- 下一步：D1-4 旧数据目录接入（§3.2）。

---

## D1-4 — 接入旧数据目录（已实现并端到端验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2「D1 提供"接入旧数据目录"：用户选择现有 `outputs/`，停止旧实例后复制并保留原目录；包含源文件、state、journal、编辑草稿与任务资产，不能只复制 `state.json`。核对可读后才切换，失败不覆盖；不扫描磁盘或建立通用迁移框架」）
- 变更：
  - 新增 `folith/desktop/legacy_import.py`：
    - `plan_import()`：列出来源任务、与目标冲突的任务、被排除项、**仍在运行（worker 锁未释放）** 的任务；拒绝来源=目标、互相包含、来源不存在等不安全路径；
    - `import_legacy_data()`：**先复制到目标父目录下的 staging → 逐任务校验可读（state.json / journal / drafts 解析 + 文件数/字节数与来源一致）→ 校验通过才 move 到位**；任何失败都会删除 staging 并**保持目标目录原样**；已存在的任务**永不覆盖**，只跳过并报告；
    - **凭据不随迁**（`provider_config.json`）与应用级状态（`.instance.lock` / `instance.json`）显式排除并报告；
    - `verify_import()`：从最终位置再读一遍被接入的任务。
  - `gui.py`：新增 `--import-legacy-data SOURCE` 与 `--import-dry-run`（不启动服务/窗口）；失败返回退出码 12。
- 冻结产物端到端实测（job `4055e7aef65f5cf9`，真实旧目录 = 浏览器切片产出的真实任务）：

| 观测 | 值 |
| --- | --- |
| 试运行 | `试运行：可接入 1 个任务，跳过 0 个已存在任务`；**目标目录未被创建**（`destination_exists: false`） |
| 正式接入 | 退出码 0，输出 `已接入 1 个任务…未复制凭据 1 项` |
| **原目录未改动** | 接入前后各 25 个文件，**逐文件 sha256 完全一致**（`legacy_tree_unchanged: true`） |
| **资产完整** | 来源任务 23 个文件 vs 接入后 23 个文件，**文件集合完全一致**（`asset_set_identical: true`）；含 `state.json`、`source.bin`、`runtime_events.jsonl`、`.performance/**` 等任务资产 |
| **凭据未随迁** | 预埋 `provider_config.json`（`api_key: sk-LEGACY-MUST-NOT-TRAVEL`）→ `credential_copied: false` |
| **接入后可读** | `adopted_job_readable: true` |
| **真实 UI 重开** | 在接入后的数据目录启动冻结应用 → 浏览器重开校验：编辑器显示 `导入校验编辑 IMPORT-CHECK`（`editor_contains_edit: true`） |
- 测试与结果：
  - `tests/desktop_legacy_import_test.py` → **8 passed**（整树复制且原目录字节不变、凭据不复制、**已存在任务永不覆盖**、worker 锁被持有时拒绝、**校验失败时目标目录逐文件不变且无 staging 残留**、试运行不落盘、拒绝不安全路径、接入后可校验）
  - 全量桌面门禁 → **61 passed**；产品回归子集（`ui_console_test` / `persistence_phase_a_test` / `delivery_snapshot_test`）→ **38 passed**
- 过程中的一个测试缺陷（已修）：测试里的"持锁子进程"没有保存 `job_lock.acquire()` 的返回值，CPython 立即回收文件对象 → flock 被释放，导致"拒绝接入"用例误判。改为保存 stream 后即通过（产品侧逻辑本身正确）。
- 诚实标注 / 剩余风险：
  - 目前只有 CLI 入口（`--import-legacy-data`）；roadmap 允许 D1 先提供能力，图形化"选择旧目录"向导可作 D2 打磨。
  - 不复制 `provider_config.json` 是有意为之（凭据跨目录/跨机不随迁，roadmap §3.4）；用户需在新位置重新配置 API Key，接入报告会明确列出。
  - "停止旧实例"通过 worker 锁检测（`.worker.lock` 被持有即拒绝）；若旧实例已退出但残留锁文件，锁会自然释放，接入照常进行。
- 下一步：D1 里程碑收尾审计（roadmap §25），确认 D1 强制项是否全部有证据、有无遗留 NOT YET TESTED。

---

## D1-6 — 崩溃 / 强制结束恢复（已端到端验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「崩溃、强制结束或断电：复用现有 interrupted / stalled 识别与检查点恢复，不能把未完成的任务标成完成」；§8 失败表「强制结束后恢复 → 恢复不能继续」）
- 方法：`scripts/d1_crash_recovery_runner.py` —— 用受控端点（每次响应延迟 6s）跑真实任务，在翻译中途 **`kill -9` 服务进程**（模拟崩溃 / 断电，无保存机会），然后在同一数据目录重开服务，用浏览器打开该任务观察用户看到什么，并点击恢复控件确认能否继续。
- 结果：**PASS**（job `6ebe7b94fd5c5b99`，冻结 `.app`）

| 阶段 | 观测 |
| --- | --- |
| 崩溃前 | `runtime_status: waiting_external`、`stage: translation`、journal 已落盘 4 段、**worker 锁被持有** |
| 崩溃 | `kill -9` 服务 → `service_alive: false`；OS 自动释放 worker 锁（`worker_lock_held: false`） |
| 崩溃后（磁盘） | journal 仍 4 段、`delivery_status: draft`、runtime 状态仍是崩溃前的陈旧 `waiting_external` —— **没有被标成完成** |
| 重开 UI | `interrupted_text_present: true`（界面显示"运行已中断，进度已保存"一类状态）、**提供恢复控件**（`runtime_resume_*`，`resume_present: true`；非 stalled 的 retry 分支） |
| 点击恢复 | 进度 `4/4 → 12/12`，`resumed: true`；恢复后 12 段全部完成，`delivery_status` 仍为 `draft`（交付没有被伪完成） |
| 收尾 | 服务进程 0 残留 |

- 说明（过程修复）：首轮恢复失败（`runtime_status: failed`）是因为**测试脚本在崩溃阶段就终止了受控模型端点**，恢复后的 worker 无法调用模型——属验证脚本缺陷；让端点在恢复阶段继续存活后即通过。产品在模型不可用时如实报 `failed`、不伪完成，这一点本身也是正确行为。
- 测试与结果：`scripts/d1_crash_recovery_runner.py` → **ok: true**；全量桌面门禁 **61 passed**；产品回归子集 **38 passed**。
- 剩余：真实机器睡眠 / 唤醒需人工（会打断用户机器）；等价能力（断连识别、中断状态识别、恢复可用）已证明。
- 下一步：D1 收尾审计（见 `docs/desktop-d1/closeout.md`）。

---

## D1-7 — 暂停（paused）任务退出：**仍未验证**（本轮尝试 + 精确失败证据）

- 时间：2026-10-03
- 里程碑：D1（roadmap §6.1「D1 再扩展**暂停**、未提交输入、长请求和崩溃矩阵」；prompt §11 明确"不得在未证明的情况下把暂停当作 save & stop 的等价物"）
- 本轮新增：`scripts/desktop_d1_paused_probe.py`（导航到任务 → 必要时先恢复 → 点应用自己的「暂停任务」按钮 → 等 worker 真正暂停并释放锁 → 触发真实关窗路径 → 等退出流程报告）、`scripts/d1_paused_exit_runner.py`（前置造出"翻译中途"的任务 → 暂停 → 退出 → 重开并恢复），并扩展 `scripts/desktop_d1_resume.js` 以覆盖 `runtime_continue_*`（暂停）控件。
- 三次尝试的实际结果（均**未**达到验证目标，且失败都发生在**测试脚手架**一侧）：

| 尝试 | 现象 | 归因 |
| --- | --- | --- |
| 1 | 探针 `pause_click: missing`；任务 `stage=document_processing` | 探针**没有先把窗口路由到任务页**，DOM 里根本没有 `runtime_pause_*` 控件（已修：改用 `exit_flow.navigate_to_job`） |
| 2 | 任务 `status=failed`，`stage=document_processing`，`paras=12 / pairs=0` | 前置阶段在**文档处理中途**就停掉了服务，产品如实把任务标为 failed，探针找不到"活跃任务"（已修：等到 `stage=translation` 且 journal 有落盘后再停服务） |
| 3 | 任务 `status=failed`，`error=批次翻译失败（1 段）：Connection error.`；探针到场时任务已失败，UI 提供 `runtime_retry` | **受控模型端点的生命周期问题在脚手架侧**：runner 与各阶段共用一个 `<out>/stub-ready.json`，应用被配置到的端口与 runner 之后持有的 stub 可能不是同一个实例；等前置阶段结束后该端点已不在监听 → 连接失败。产品侧超时为 connect 15s / read 180s（`folith/llm_transport.py:OPENAI_TIMEOUT`），与 8s 的受控延迟无关 |

- 本轮**已获得的正面观察**（部分证据，不足以判定该行通过）：
  - 退出流程确实执行了完整步骤：`locate-job → navigate-to-job → flush-editor → commit-drafts → final-check`，且退出后 `services_after_exit = 0`（无残留服务）；
  - 任务处于失败/中断状态时，界面如实提供 `重试当前步骤`（未伪报成功）；`delivery_status` 保持 `draft`，未被标成已交付；
  - 探针能够通过 `exit_flow.navigate_to_job` 把窗口路由到任务页（该复用路径有效）。
- 下一步（明确的修复项，属脚手架而非产品）：
  1. 让每个阶段使用**独立的 out 目录**（或让 runner 只启动一个 stub 并把 URL 传给所有阶段），避免 `<out>/stub-ready.json` 被后续 stub 覆盖；
  2. 或给 `scripts/d0_slice_runner.py` 增加 `--stub-url`，使其复用外部端点而不是自建；
  3. 修复后重跑，判定标准：暂停确已生效（`status=paused` 且 worker 锁已释放）→ 关窗走完整退出流程 → 无残留 → 重开后暂停的进度**零丢失**且能继续跑到完成。
- 诚实标注：**该行仍为 NOT YET TESTED**，不得据本轮结果宣称"暂停等价于 save & stop"。

---

## D1-7（结论）— 暂停任务退出：**PASS**

- 时间：2026-10-03
- 命令：`venv/bin/python scripts/d1_paused_exit_runner.py --out /tmp/d1-p10 --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith` → **exit 0（ok: true）**
- 场景（真实用户路径）：真实任务在翻译中途 → 前置服务被停止（任务 `interrupted`，journal 4 段）→ 启动**冻结应用**并让探针用应用自己的 UI：路由到任务 → 点「继续处理」→ 点「暂停任务」→ 触发真实关窗 → 重开并继续。
- 实测时间线（job `…`，冻结 `.app`）：

| 阶段 | 观测 |
| --- | --- |
| 中断到达 | `runtime_status=interrupted`，journal 4 段，worker 锁未持有 |
| 点「继续处理」 | `waiting_external`，4 段已落盘，worker 锁**被持有** |
| 点「暂停任务」 | **`runtime_status=paused`**（`paused_observed=true`），当时 worker 锁仍持有（`worker_lock_held_at_pause=true`）——产品先停止安排新操作，锁在退出流程里才释放 |
| 关窗（真实路径） | 退出流程步骤：`locate-job → flush-editor → **cancel-task** → commit-drafts → final-check` |
| 退出后 | `runtime_status=cancelled`、**8 段已落盘**、`delivery_status=draft`（未伪报交付）、**worker 锁已释放**、**0 残留服务** |
| 重开后 | 界面提供 `继续处理`（`runtime_resume`）→ 进度 **8/8 → 12/12** → `runtime_status=completed` |

- 判定依据（与 roadmap §3.3 一致，而非"暂停等于 save & stop"的假设）：
  1. 暂停请求确实被产品接受并进入 `paused`；
  2. 退出时复用既有"停止安排新操作 + 有界等待 + 释放 worker 锁"路径（`cancel-task`），不绕过互斥；
  3. **零丢失**：暂停时 4 段 → 退出后 8 段（进行中的批次完成并落盘），未完成任务**未被标成完成**（`delivery_status` 始终 `draft`）；
  4. 重开后暂停的任务**可恢复且能继续跑完**（8/8 → 12/12 completed）。
- 过程修复（脚手架，均已加回归测试 `tests/desktop_harness_test.py` → 5 passed）：
  1. `start_stub_llm` 用 `subprocess.PIPE` 接住受控端点的 stdout/stderr 却从不读取 —— `BaseHTTPRequestHandler` 每请求写 stderr，缓冲填满后端点**阻塞**，在被测应用里表现为误导性的 `Connection error`；改为写文件（`<out>/stub-server.log`）；
  2. 重开阶段另起一个端点（换端口），而应用里存的是旧 URL → 恢复阶段必然连接失败；改为**全程只用一个端点**；
  3. 探针必须先 `exit_flow.navigate_to_job` 才有暂停控件；且要**先恢复再暂停**（服务是被停掉的，任务到场时是 `interrupted`）；
  4. 暂停时机：worker 在**安排下一批**时检查暂停，因此必须在还有剩余批次时发出请求（过早等到任务结束就会 `completed`）；
  5. runner 早退路径会漏掉端点（已在最外层 `finally` 统一回收）。
- 诚实标注：观察到退出时状态为 `cancelled`（而非保持 `paused`）——这是既有退出流程"停止活动任务"的语义；对用户的结果是**停止且可恢复、零丢失**。若产品负责人希望暂停中的任务在退出后仍显示为 `paused`，那是一个独立的产品行为变更，需另行决定。

---

## 附：新发现的独立问题 — 源码模式下 PaddlePaddle 段错误（与桌面产物无关，记录备查）

- 时间：2026-10-03
- 现象：本轮数次验证出现"页面/浏览器被关闭"与 `Connection error` 的偶发失败；`~/Library/Logs/DiagnosticReports/Python-*.ips` 显示多个 **源码模式** Python 进程 `Segmentation fault: 11`，崩溃线程栈为 `libpaddle.so: paddle::framework::EventCount::Park(...)`（`__psynch_cvwait`），即 **PaddlePaddle 内部线程**。
- 影响面核查：
  - 冻结桌面产物**不包含 Paddle**：`find dist/desktop/Folith.app -iname "*paddle*"` 为空，发布清单的 15 个发行版中无 paddle → **桌面用户不会遇到该类崩溃**（符合 roadmap §3.4「不默认打入可选 PaddleOCR」）；
  - `core.py` 仅在"Tesseract 不可用且模式为 auto"时走 `PaddleOCRBackend`（`core.py:759`）；源码模式下 venv 里装了 Paddle 时该路径会加载 `libpaddle`。
- 处置：按 §22 记录为独立问题，不在桌面里程碑内修复；对验证脚手架的建议是让源码模式服务也指向随包 OCR 引擎与数据（`folith/desktop/paths.py:apply_bundled_tool_env`，D2 已实现），从而避免走到 Paddle 回退路径。
- 未验证：该段错误是否只在解释器退出阶段发生（现有 `.ips` 不足以定论）；如需跟进应单独立项。

---

## D1-8 — 桌面退出码契约与"内部服务模式被直接调用"的行为（补测）

- 时间：2026-10-03
- 里程碑：D1/D0 边界（roadmap §3.1「失败显示原因和日志入口」；`--serve` 只启动 Streamlit、不递归建窗）
- 动机：桌面工程定义了退出码契约（0 正常 / 2 用法错误 / 3 缺 pywebview / 4 原生窗口失败 / 8 自动化脚本加载失败 / 9 服务意外退出 / 10 第二实例被拒 / 11 上一服务仍在退出 / 12 旧数据接入失败）。此前 **3 / 4 / 8 无任何测试**，而 `--serve` 被用户直接调用时的行为（是否挂死、是否泄漏监听/进程）也从未验证。
- 实测（冻结产物，真实进程）：

| 场景 | 命令 | 观测 | 判定 |
| --- | --- | --- | --- |
| 自动化脚本加载失败 | `Folith --automation-script /nonexistent-probe.py --automation-result …` | 退出码 **8**；输出 `[错误] 无法加载 D0 验证脚本：automation script not found: …`；**0 残留服务** | **PASS** |
| 内部服务模式被直接调用 | `Folith --serve --handshake-timeout 3 < /dev/null` | 退出码 **3**；输出 `[folith-desktop] handshake failed: control channel closed before the launcher handshake`；**0 残留服务、0 个 Folith 监听端口** | **PASS**（有界失败，不挂死、不泄漏） |

- 新增单元测试（`tests/gui_launcher_test.py`，共 **14 passed**）：用最小假服务覆盖退出码契约
  - 缺 pywebview → **3**；
  - 原生窗口创建失败 → **4**；
  - 自动化脚本加载失败 → **8**；
  - 一切正常 → **0**（并核对 `stop()` 回收证据被打印：`child_gone / service_pid_gone / port_released / acknowledged`）。
- 诚实标注：退出码 **2**（用法错误）由 argparse 与显式分支覆盖，未单独写用例；**9 / 10 / 11 / 12** 已有既有测试（`desktop_handshake`、`desktop_single_instance`、`desktop_legacy_import`、`gui_launcher`）与本轮 sweep 的端到端用例。`--serve` 直接调用返回 3（缺握手）而非 2，属实现现状，已如实记录。

---

## D1-9 — 缓存 / 任务资产分离的**证据化**审计（roadmap §3.2）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2「临时文件 / 可删缓存与任务资产分开，清缓存不能删除源文件、检查点或交付快照」）
- 背景：D1 首轮审计把该条标为 `NOT APPLICABLE`（"产品没有清缓存功能"），但当时是**基于印象**，没有留下证据。本轮把它变成可复核的证据，并把约束固化为回归测试。
- 证据：
  1. **不存在清缓存功能**：全仓 `grep -rn "清理缓存|清除缓存|清空缓存|删除缓存|clear_cache"`（`app.py` / `core.py` / `folith/**`）**零命中**；`_reset_*` 系列是"重置 AI 连接"，不删除数据；
  2. **缓存与资产物理分离**：共享 OCR 画像缓存位于 `<data>/ocr_profile_cache/<sha16>`（**独立顶层子树**，不在任何任务目录内）；任务目录内可删的只有 `.performance/`（性能采样）与 `.runtime-write.lock` / `.worker.lock`；任务资产为 `source.bin`、`state.json`、`source_cleanup_checkpoint`、`document_profile.json`、`delivery_snapshots/vN/**` 等；
  3. **破坏性调用面**：`rmtree(` / `.unlink(` / `os.remove(` 全仓共 **16 处**，逐处检查后**没有一处按名触及任务资产**；唯一涉及资产命名的是 `folith/storage/state_journal.py:474` 的 journal 轮转（退役已压缩的旧日志段，属设计行为，列入允许清单）。
- 新增回归测试 `tests/desktop_data_safety_test.py`（**3 passed**）：
  1. 共享缓存必须位于 `<output>/<OCR_PROFILE_CACHE_DIRNAME>/…` 这一独立子树（不可能被误当成某个任务目录）；
  2. 破坏性调用**不得**按名触及任务资产（仅允许 journal 轮转）；
  3. 若将来出现清缓存入口，该测试会**失败并要求补充安全论证**（避免"新功能静默绕过 §3.2"）。
- 诚实标注：本条今天仍是 `NOT APPLICABLE`（无功能可测），但**分离约束已有证据与回归保护**；未覆盖磁盘配额/权限等异常路径。

---

## D1-10 — 诊断的 schema 版本必须取自常量（§3.5）：发现缺口并修复

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.5「当前数据格式没有统一的 job-state schema 版本：应如实记录 `job_state=unversioned`，以及现有 `runtime=2`、`journal=1`、`translation_drafts=1`；**后续从对应常量读取**，不为诊断新建状态格式或迁移体系」）
- 背景：D1-5 已验证诊断包的字段与脱敏，但没有逐项核对"declared 是否真的取自常量"。本轮把这条从印象变成证据。
- **发现的缺口**：`folith/desktop/diagnostics.py` 中只有 `journal.declared` 读取了常量（`JOURNAL_SCHEMA_VERSION`）；`runtime.declared` 与 `translation_drafts.declared` 写死为 `None`，未读取 `core.RUNTIME_SCHEMA_VERSION`（"2"）与 `core.TRANSLATION_DRAFTS_VERSION`（1）——与 §3.5 的"从对应常量读取"不符（`observed` 仍在读文件，信息未丢失）。
- 修复：新增 `_core_schema_declared(attribute)`（惰性导入 `core`，失败如实报未知而非编造），`runtime` 与 `translation_drafts` 的 `declared` 改由它读取。
- 复验（**冻结产物**，`--export-diagnostics` 指向含真实任务文件的目录）：

```json
{
  "job_state": "unversioned",
  "runtime":            {"declared": "2", "observed": "2"},
  "journal":            {"declared": 1,   "observed": 1},
  "translation_drafts": {"declared": 1,   "observed": 1},
  "source": "observed from 1 job(s) in the data directory"
}
```

  → declared 全部来自常量，且与真实文件中的 observed 一致。
- 新增回归测试（`tests/desktop_diagnostics_test.py`，共 **9 passed**）：
  1. `declared` 必须等于 `core.RUNTIME_SCHEMA_VERSION` / `core.TRANSLATION_DRAFTS_VERSION` / `state_journal.JOURNAL_SCHEMA_VERSION`，且 `job_state == "unversioned"`（并断言常量本身为 `"2"` / `1`）；
  2. `observed` 必须从真实文件读出（`runtime_state.json.schema_version`、`translation_drafts.json.version`、journal 行的 `schema`）。
- 产物：因改动产品代码，已**重建应用并重切 DMG**（`dmg_app_matches_built_app` 阻断式校验要求如此）→ 新镜像 152,082,164 字节、sha256 `75ed97f2…`；发布清单 `ok: true`，发布说明已刷新且 `--check` 非 stale。

---

## D1-11 — §3.5「版本取自实际随包运行时」与"无任务数据"诊断路径（已取证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.5「**版本取自实际随包运行时**，构建记录必须能定位源码与依赖」；以及诊断在全新安装上的行为）
- 取证（冻结产物）：
  1. **streamlit 版本来自随包运行时**：诊断报告 `streamlit_version: 1.64.0`，与**包内自带**的 `Contents/Resources/streamlit-1.64.0.dist-info/METADATA` 中 `Version: 1.64.0` **一致**（不是抄构建记录）；
  2. **Python 版本来自随包解释器**：报告 `python_version: 3.11.3`；代码路径为 `sys.version.split()[0]`（冻结进程内），与包内 `Python.framework/Versions/3.11` 相符；
  3. **pywebview 版本来自随包包体**：启动器在冻结进程中 `import webview` 并读 `__version__`（包内存在 `Contents/Resources/webview`）→ `6.2.1`；
  4. **pyinstaller 版本**如实来自构建记录（运行时不可导入 PyInstaller，属构建期事实，非伪造）；
  5. 其余：`app.frozen=true`、`packaging_backend=pyinstaller`、`system.architecture=arm64`、`os_version=macOS-27.0.1-arm64`。
- **全新安装（数据目录不存在）**：`--export-diagnostics` 指向不存在的 `<tmp>/missing/data` → 退出码 0、诊断包正常导出，且如实呈现：

```json
"data_schema": {"job_state": "unversioned",
                "runtime": {"declared": "2", "observed": null},
                "journal": {"declared": 1, "observed": null},
                "translation_drafts": {"declared": 1, "observed": null},
                "source": "observed from 0 job(s) in the data directory"}
```

  → declared（来自常量）仍可知，observed 一律 `null`，**不编造**任何数据；`locations.job_count = 0`。
- 新增回归测试（`tests/desktop_diagnostics_test.py`，共 **11 passed**）：全新安装时 declared 有值 / observed 为 null / `0 job(s)`；包内 streamlit 元数据版本与目录名一致（防"报告值与随包实际版本脱节"）。
- 诚实标注：`pyinstaller_version` 属构建期事实（来自随包构建记录），非运行时探测；本机为 arm64 macOS，其它架构/系统未验证。

---

## D1-12 — `--import-dry-run` 的"零写入"从"目标未创建"升级为**逐文件 no-op 证明**

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2「核对可读后才切换，**失败不覆盖**」；D1-4 只证明了"目标目录未被创建"，而**用户已有数据**才是常见情形）
- 新增 `scripts/d1_dryrun_noop_runner.py`：真实旧数据目录（含凭据）→ 目标目录**预先存在**且含用户自己的任务、`provider_config.json`（自有 API Key）与 `.onboarded` → 对**两棵树**做完整指纹（目录项 + 逐文件 sha256）→ 运行 `Folith --import-dry-run --import-legacy-data …` → 重新指纹。
- 实测（冻结产物，`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 退出码 / 输出 | **0**；输出含 `试运行：可接入 1 个任务，跳过 0 个已存在任务`、`旧数据目录（未改动）`、`未复制（凭据 / 应用状态）：provider_config.json` |
| **旧数据目录** | 指纹差异 **added/removed/changed 全为空** |
| **已存在的目标数据目录** | 指纹差异 **added/removed/changed 全为空**（用户任务、`.onboarded` 均未动） |
| 用户自己的凭据 | `provider_config.json` 中 `api_key` 仍为 `sk-USER-OWN-KEY`（`user_key_untouched: true`） |
- 已加入端到端 sweep（现为 **15 个用例**）+ 接线契约测试。
- 诚实标注：验证的是"同机、同一文件系统、无并发写者"下的 no-op；未覆盖磁盘满/权限拒绝等异常路径。

---

## D1-13 — 诊断导出：**裸 API Key 泄漏（已修）**与对抗性日志取证

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.5「导出时隐去路径中的用户名 / 项目名，**排除 API Key**、startup token、provider 配置、环境变量全文和文档内容」）
- 方法：构造**对抗性日志**（含带前缀的 key、**裸 key**、startup token、文档正文、PATH 全文、家目录路径、正常错误行），用冻结产物 `--export-diagnostics` 导出并逐项检查是否出现在包里。
- **发现真实缺陷**：`folith/desktop/diagnostics.py` 的脱敏只处理 `api_key/token/... : value` 这类**带键名**的写法；日志里**不带前缀**的凭据（上游错误回显、SDK 报错）会**原样进入诊断包**。实测首轮：

```
launcher.log: ERROR upstream returned 401 for key sk-BARE-BBB2222222222   ← 泄漏
```

- 修复：新增 `_SECRET_PATTERNS` 高置信度凭据形态（`sk-…`、`AIza…`、`ghp_…`、`xox?-…`、`Bearer …`），并**先应用具体形态、再应用通用 `键名: 值` 规则**（否则通用规则只吃掉 "Bearer" 一词而把 token 留下）。刻意**不**匹配普通哈希，避免诊断失去可读性。
- 复验（重建后的冻结产物，同一份对抗性日志）：

| 检查 | 修复前 | 修复后 |
| --- | --- | --- |
| 带前缀 key `api_key=sk-PREFIXED-AAA111` | 已脱敏 | 已脱敏 |
| **裸 key** `key sk-BARE-BBB2222222222` | **泄漏** | **`<redacted>`** |
| `startup_token=abc123def456` | 已脱敏 | 已脱敏 |
| `Authorization: Bearer eyJ…` | 部分泄漏（只吃掉 Bearer） | `<redacted>` |
| 家目录 / `/opt/homebrew` | 已隐去 | 已隐去 |
| 64 位哈希（诊断可读性） | 保留 | 保留 |

- 新增测试：`tests/desktop_diagnostics_test.py` → **12 passed**（裸凭据 5 种形态必须脱敏；**普通哈希必须保留**）。
- **残余风险（如实记录）**：对抗性日志里的**文档正文**行仍会被收录（诊断按"错误标记行"筛选，无法通用判定哪段文本是文档内容）。核查结论：**产品自身不记录文档正文/提示词**（`app.py`/`core.py`/`gui.py`/`folith/desktop/*` 中无记录 prompt 或正文的日志调用），因此该风险仅存在于"第三方库把正文写进日志"的情形；若将来出现此类日志，需要按具体来源治理，而不是在导出侧做模糊过滤。
- 产物：产品代码变更 → 重建应用并**重切 DMG**；发布清单 `ok: true`（`dmg_app_matches_built_app`、`no_secret_like_strings` 均 PASS），发布说明已刷新且 `--check` 非 stale。

---

## D1-14 — 旧数据接入：**不安全来源**在冻结产物中被拒绝且零写入

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2「用户选择现有 `outputs/`，停止旧实例后复制并保留原目录…**核对可读后才切换，失败不覆盖**；不扫描磁盘」）
- 背景：单测覆盖了路径规则，但"冻结产物真的会拒绝、且拒绝时一个字节都不写"此前没有端到端证据。
- 新增 `scripts/d1_unsafe_import_runner.py`：目标目录**预先存在**（含用户自己的任务与 `provider_config.json`），对每种不安全来源运行 `Folith --import-legacy-data <source>`，逐例比对**目标树指纹**（目录项 + 逐文件 sha256）。
- 实测（冻结产物，`ok: true`，7/7）：

| 来源 | 退出码 | 界面原因 | 目标目录 |
| --- | --- | --- | --- |
| 不存在的路径 | **12** | `旧数据目录不存在或不是目录：…` | **逐项不变** |
| 普通文件 | **12** | 同上 | **逐项不变** |
| 与目标相同 | **12** | `旧数据目录与当前数据目录相同，无需接入` | **逐项不变** |
| 目标的父目录 | **12** | `旧数据目录与当前数据目录不能互相包含` | **逐项不变** |
| 目标的子目录 | **12** | 同上 | **逐项不变** |
| 文件系统根 `/` | **12** | 同上（根包含目标 → 拒绝） | **逐项不变** |
| 空目录（无任务） | 0 | 无任务可接入（如实记录，不视为"不安全"） | **逐项不变** |

- 已加入端到端 sweep（现为 **16 个用例**）+ 接线契约测试（6 类不安全来源必须在用例集合里，且必须比对目标树）。
- 诚实标注：覆盖的是"同机、无并发写者"下的拒绝与零写入；符号链接/硬链接、网络卷、权限拒绝等未覆盖。

---

## D1-15 — 退出超时分支：**不谎报安全**、保持打开、解除阻塞后正常停止（冻结 `.app`，已验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「等待超过上限：明确提供继续等待或强制退出；**强制退出只承诺已经落盘的结果可恢复**」）
- 背景：该分支此前只有单测覆盖（`confirm_force_exit` 的两条路径），没有端到端证据。本轮用"外部持锁 = 卡住的 worker"制造**真实的超时**：
  - 新增 `scripts/desktop_d1_timeout_probe.py`（在冻结应用内运行）：用**同一个** `folith.job_lock` 抓住该任务的 `.worker.lock`（与卡住的 worker 等价），触发第一次关窗；随后释放锁、记录 worker 已停止、触发第二次关窗；
  - 新增 `scripts/d1_exit_timeout_runner.py`：先跑完整切片造出真实任务（12 段已落盘），把运行状态设为 `running`（**夹具**，用于进入超时分支），再以 `FOLITH_EXIT_FORCE_PROMPT=0` 运行带探针的应用。
- 实测（`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 探针持锁 | `lock_acquired: true`、`lock_seen_by_flow: true`（退出流程读到锁被持有） |
| **第一次关窗（锁被持有）** | `safe_to_exit: false`、`forced: false`、`action: incomplete`；步骤含 **`force-prompt`**；`after.worker_lock_held: true` —— **拒绝谎报安全**，且（自动化开关下）不强制退出 |
| 第二次关窗（锁已释放 + worker 已停止） | `safe_to_exit: true`、`action: saved-and-stopped` → 应用正常退出 |
| 进程回收 | **0 残留服务** |
| 数据 | 关窗前 12 段已落盘 → 退出后仍 12 段、`delivery_status=final`（**零丢失**） |
- 过程修正（均为**夹具/断言**问题，非产品缺陷）：① setup 切片遇到环境性的"浏览器被关闭"抖动 → 加入与 sweep 相同的**有界重试**；② 夹具把运行状态写成 `running` 会被产品的 interrupted 识别归一化（`active_status: interrupted`）→ 因此**没有** `cancel` 子报告（没有活动任务可取消），断言改为"锁被持有"这一诚实原因；③ 第二次关窗前必须记录"worker 已停止"，否则状态永远不是非活动、流程永远无法安全。
- 已加入端到端 sweep（现为 **17 个用例**）+ 接线契约测试。
- 诚实标注：**强制退出（点击"确定"）本身仍需人工**（原生确认对话框，D0/D1 已记为人工项）；本轮证明的是"超时不被谎报为安全 + 可继续等待 + 解除阻塞后能正常停止"。

---

## D1-16 — **真实缺陷**：两个任务同时运行时，退出只停止"窗口里那个"（已修复）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「运行中任务退出：走产品既有取消语义」；§3.1.4「不能把任务锁当应用锁」；prompt §11「正常关闭不得只是杀掉进程、指望持久化已经完成」）
- 动机：产品**没有**"已有任务在运行时禁止再开一个"的保护（`app.py` 只在界面提示"任务正在后台处理…期间可浏览其他任务"），因此"两个任务同时活跃"是用户可达状态；而退出流程只取 `actives[0]`。这是 D1 之前从未验证过的路径。
- **缺陷证据（修复前，冻结产物）**：
  - 造出两个不同来源的任务，二者同时处于活跃状态（`waiting_external`）；
  - 退出报告 `active_jobs_on_disk` 列出**两个**任务，但只取消了窗口里的那个：`other_cancels: null`；
  - 退出后状态：一个 `cancelled`，另一个**仍是 `running`**（被随服务一起强杀——正是 D0 记录、D1 要消除的缺陷）；
  - 更严重：此时仍报告 **`safe_to_exit: true` / `action: saved-and-stopped`** —— **在还有任务运行时谎报安全退出**。
- 修复（`folith/desktop/exit_flow.py`，复用既有抽象，不新建状态）：
  1. 提交主任务草稿后，枚举 `active_job_ids(data_dir)` 中**其余**活跃任务：逐个 `navigate_to_job` → `cancel_running_task`，结果记入 `report["other_cancels"]`，步骤 `cancel-other-tasks`；
  2. `safe_to_exit` 现在要求**所有**任务的快照都满足"无未提交草稿 / 无 worker 锁 / 非运行中"；任一不满足即 `incomplete`，并在强制退出提示里列出"另有 N 个任务仍在运行：<job…>"；
  3. `cancel_running_task` 增加**有界重试**（`CANCEL_CLICK_WAIT = 15s`）：取消控件位于运行时条带内、异步渲染，首次点击可能早于控件出现（修复前实测 `cancel-control-not-found`）。
- **修复后复验（冻结产物，`ok: true`）**：

| 观测 | 结果 |
| --- | --- |
| 退出前两任务是否都活跃 | 是（`ACTIVE_STATUSES` = cancelling/paused/pausing/queued/resume_requested/running/starting/waiting_external） |
| `other_cancels` | 另一任务 `cancel_clicked: true`（`via: scoped`）、`worker_lock_released: true`、状态 → `cancelled` |
| 退出后状态 | **两个任务均为 `cancelled`**（无遗留 `running`） |
| 退出结论 | `safe_to_exit: true` / `saved-and-stopped`（**这次是真的安全**） |
| 残留进程 | 0 |
| 数据 | 两任务 `state.json` 均可解析：12 段/8 段已提交译文 与 54 段（提前取消，0 段提交）——无损坏 |

- 新增单元测试（`tests/desktop_exit_flow_test.py` → **17 passed**）：① 两个活跃任务都必须被取消且结论为 safe；② 另一个任务停不下来时**必须**报 `safe_to_exit: false` 并在提示里说明"另有 N 个任务仍在运行"。
- 已加入端到端 sweep（现为 **18 个用例**）+ 接线契约测试。
- 产物：产品代码变更 → 重建应用并**重切 DMG**（152,085,559 字节 / sha256 `ba4c302b…`）；发布清单 `ok: true`，发布说明已刷新且 `--check` 非 stale。
- 诚实标注：验证覆盖"同一服务内两个活跃任务"；三个及以上、以及"一个任务在跑 + 另一个停在暂停态"的组合未单独跑（代码按同一循环处理，但未逐一取证）。

---

## D1-17 — 并发形状扩展：**三个同时运行**与**暂停 + 运行混合**的退出（均已验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3；D1-16 明确遗留的两个组合：三个及以上任务、暂停态与运行态混合）
- 方法：把 `scripts/d1_two_tasks_runner.py` 泛化为 `--scenario {two-running,three-running,paused-plus-running}`（任务数与 stub 延迟随场景缩放，保证退出时确实"多个任务同时活跃"）；暂停阶段由 `scripts/desktop_d1_two_tasks_probe.py` 在**真实 UI** 中点击 `.st-key-runtime_pause_<job>` 完成，并等到状态真正变为 `paused`。
- **场景 A：三个任务同时运行**（冻结产物，`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 退出前活跃任务 | **3 个全部活跃** |
| `other_cancels` | 恰好覆盖"活跃集合 − 主任务"（2 个），且两者 `cancel_clicked: true` |
| 退出后状态 | **三个均 `cancelled`** |
| 结论 / 残留 | `safe_to_exit: true`、`saved-and-stopped`；0 残留 |
| 数据 | 三者 `state.json` 均可解析（12 段/4 段已提交、54 段、5 段），无损坏 |

- **场景 B：暂停 + 运行混合**（冻结产物，`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 暂停是否真的生效 | `paused_ok: 1`；暂停后状态：一个 `waiting_external`、一个 `paused` |
| 退出前活跃任务 | 两个都在活跃集合（`paused` 属产品定义的活跃状态） |
| `other_cancels` | 覆盖另一个任务且 `cancel_clicked: true` |
| 退出后状态 | **两个均 `cancelled`**（暂停的任务也被正确停止） |
| 结论 / 残留 | `safe_to_exit: true`、`saved-and-stopped`；0 残留 |

- 断言口径（本轮修正的**脚手架**问题，非产品缺陷）：① 早期断言要求"所有任务都在运行"，但先启动的任务可能已自然完成 → 改为"退出时**至少两个**任务活跃"，并断言 `other_cancels` **恰好等于**"活跃集合 − 主任务"；② stub 延迟改为随任务数缩放，避免后启动的任务比先启动的先完成。
- 已加入端到端 sweep（现为 **20 个用例**）+ 接线契约测试。
- 诚实标注：覆盖 2/3 个任务与"一暂停一运行"；更多任务、以及"暂停 + 暂停"未单独跑（同一循环处理，但未逐一取证）。

---

## D1-18 — 瞬态退出：任务处于 `cancelling`（模型请求仍在飞行）时关闭窗口

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.3「网络 / 模型请求仍在进行」的退出处理；prompt §11 明列该情形）
- 方法：`--scenario cancelling-at-exit` —— 启动一个真实任务后，由探针在**真实 UI** 点击该任务的「取消」（不是退出流程的取消），等到产品真的进入 `cancelling` 瞬态，**随即**触发关窗。
- 实测（冻结产物，`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 是否真的进入瞬态 | `cancelling_ok: 1`；触发关窗时状态为 **`cancelling`** |
| 退出流程步骤 | `locate-job → flush-editor → **cancel-task** → commit-drafts → final-check`（对进行中的取消**继续等待**，而不是直接强杀） |
| 退出结论 | `safe_to_exit: true`、`saved-and-stopped` |
| 退出后状态 | 任务为 **`cancelled`**（不再是活跃状态） |
| 残留进程 | **0** |
| 数据 | `state.json` 可解析；`source.bin` 仍在（该任务在启动后数秒即被取消，故 0 段落属正常，无丢失） |
- 断言口径修正（脚手架）：早期断言要求"段落数 > 0"，但"刚启动即取消"的任务本来就可能还没完成解析；改为"`state.json` 可解析 **且源文件仍在**"，这才是该情形下的持久性证据。
- 已加入端到端 sweep（现为 **21 个用例**）+ 接线契约测试。
- 诚实标注：验证的是"取消请求已发出、模型请求仍在飞行"这一瞬态；`queued` / `starting` 等其他瞬态的退出未单独取证（同一等待逻辑处理）。

---

## D1-19 — 长期使用形态：**50 个任务**的数据目录仍可用且计数诚实

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2：数据目录是**长期累积**的用户数据；此前所有验证都只有 1–3 个任务）
- 方法：用冻结产物跑出一个**真实任务**（含解析、翻译、编辑、交付），把该任务目录复制成 **50 个**任务（任务身份即目录名；`state.json` 不含 job id，复制安全），然后启动应用并用浏览器探针验证"历史列表渲染数量 / 能否打开任务 / 诊断计数 / 退出回收"，同时记录耗时。
- 实测（冻结产物，`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 数据目录中的任务数 | **50** |
| 历史视图渲染出的任务条目 | **50**（未截断、未分页丢失） |
| 打开一个任务后 CAT 工作台 | **正常渲染** |
| 诊断 `job_count` | **50**（如实计数） |
| 退出后残留服务 | **0** |
| 复制 50 个任务目录耗时 | 1.0s 级（`copy_seconds` 记录在证据里） |

- 性能观测（按 prompt §17 记录口径）：
  - 硬件/系统：Apple Silicon（arm64）、16 GB、macOS 27.0.1；构建类型：PyInstaller onedir 冻结 `.app`（非源码模式）；
  - 样本：数据目录 50 个任务（每个含源文件、state.json、交付快照）；
  - 测量方法：Playwright 驱动真实窗口，`domcontentloaded → 列表出现 → 打开任务并等到 CAT 行渲染` 的墙钟时间；
  - 结果：加载 1,977 ms；列表渲染 3,414 ms；打开任务 1,054 ms；**合计 6,446 ms**（可接受，未见卡死）。
- 已加入端到端 sweep（现为 **22 个用例**）+ 接线契约测试。
- 诚实标注：50 个任务是本机可快速构造的量级；数百/上千任务、以及"大量任务 + 同时运行多个任务"的组合未验证；耗时数据只代表上述机器与构建类型。

---

## D1-20 — 任务「移除」：破坏性操作必须**逐字确认**，且不牵连其他任务（冻结产物已验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2 数据安全；prompt §22「不得静默丢弃用户工作」）
- 背景：历史页的「移除」是产品**唯一的删除入口**（也是唯一能触达"孤儿任务"的入口，见 `app.py` 中该段的注释）。此前只有源码模式 AppTest 覆盖"输入名称后按钮可用 → 点击后任务被删"，**没有**在冻结产物里验证"输错不删 / 取消不删 / 不牵连邻居"。
- 方法：用冻结产物造**两个**真实任务（各含交付快照），浏览器探针驱动真实弹窗，并在**每一步**用文件系统核对目标任务是否还在：
  1. 打开弹窗；2. 输入**错误**名称；3. 点「取消」关闭；4. 重新打开并输入**精确**名称；5. 确认移除。
- 实测（`ok: true`）：

| 步骤 | 观测 |
| --- | --- |
| 打开弹窗 | 任务仍在；确认按钮 **disabled** |
| 输入错误名称（已回车提交） | 任务**仍在**；确认按钮**仍 disabled** |
| 点「取消」关闭 | 任务**仍在**（未删除） |
| 输入精确名称（`D0 Text Layer`） | 确认按钮 **enabled**（读回输入值一致） |
| 点「永久移除」 | 目标任务**被删除**（目录消失） |
| **邻居任务** | **完好**；其交付快照 **7 个文件前后一致** |
| 残留进程 | **0** |
- 过程中修正的三处**探针**缺陷（非产品问题）：① Streamlit 把 key class 同时加在容器与按钮上，后代选择器会匹配到同一按钮两次 → 用 `.first()`；② 普通任务的关闭控件是「取消」(`history_delete_cancel`)，`history_delete_close` 只在"任务正在运行"时出现；③ 文本输入需**回车/blur 提交**，仅 `fill()` 不会触发 Streamlit 重跑（这也是"输入精确名称后按钮仍 disabled"的原因）。
- 已加入端到端 sweep（现为 **23 个用例**）+ 接线契约测试。
- 诚实标注：覆盖"两个任务、删除其中一个"；未覆盖"任务正在运行时点移除"（代码路径为只显示「关闭」并说明原因，已有单测）与"孤儿任务"场景的真实删除。

---

## D1-21 — 任务**正在运行时**不可移除（冻结产物已验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2 数据安全：worker 正在往任务目录写状态时删除会留下半写状态）
- 方法：这次必须让任务在**探针所连接的那个服务**里真正运行——先启动应用，再对同一个实例启动任务（`--stop-after-start` + 慢 stub），然后立刻在历史页点「移除」。**先停服务再点移除是无效的**：worker 已死，产品的 interrupted 识别会把状态归一化，此时允许移除是正确的（本轮第一次尝试即因此失败，属脚手架设计错误）。
- 实测（`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 点击移除时的运行状态 | **`waiting_external`（真正在运行）** |
| 弹窗内容 | 出现**拒绝文案**（"这个任务正在运行，无法移除…"） |
| 确认入口 | **没有**名称输入框、**没有**确认按钮（不存在"点了没反应"的按钮） |
| 可用的控件 | 只有「关闭」；点击后正常关闭弹窗 |
| 任务目录 | **始终存在**（运行中、关闭弹窗后均未被删除） |
| 退出后残留 | **0** |
- 已加入端到端 sweep（现为 **24 个用例**：`history-delete` 与 `history-delete-running`）+ 接线契约测试。
- 诚实标注：覆盖"运行中（waiting_external）不可移除"；`paused` 状态下的移除语义（产品视 `paused` 为活跃状态，按同一分支处理）未单独取证。

---

## D1-22 — 任务处于 **paused** 时同样不可移除（冻结产物已验证，两阶段取证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2 数据安全；D1-21 明确遗留的"paused 状态的移除语义"）
- 方法：探针先在**真实 UI** 里把任务暂停（点击 `runtime_pause_<job>`，等到界面出现 `runtime_continue_<job>`＝已暂停），**然后交回控制权**；runner 在此时读取磁盘上的 `runtime_state.json`（确认确实是 `paused`），再放行探针去做移除尝试。这个两阶段设计是必要的：第一版把状态读取放在探针**结束之后**，那时状态已经变了（实测读到 `waiting_external`），证据不足以支撑"暂停态不可移除"的结论。
- 实测（`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 暂停是否真的生效 | 探针在 UI 上确认进入暂停（`paused: true`，18.5s） |
| **尝试移除时的磁盘状态** | **`paused`**（runner 在放行前读取） |
| 弹窗 | 出现**拒绝文案**（"这个任务正在运行，无法移除…"） |
| 确认入口 | **没有**名称输入框、**没有**确认按钮（不存在点了没反应的按钮） |
| 可用控件 | 只有「关闭」；点击后正常关闭 |
| 任务目录 | **始终存在** |
| 退出后残留 | **0** |
- 已加入端到端 sweep（现为 **25 个用例**：`history-delete` / `history-delete-running` / `history-delete-paused`）+ 接线契约测试。
- 诚实标注：产品把 `paused` 视为活跃状态，因此与运行中同属"不可移除"分支；本条的结论仅覆盖"暂停态不可移除"，不代表暂停态的其他语义（如唤醒后继续）在本轮被重新验证（见 D1-7）。

---

## D1-23 — 项目级删除：逐字确认 + **删除前写可恢复备份**（冻结产物已验证）

- 时间：2026-10-03
- 里程碑：D1（roadmap §3.2 数据安全；D1-22 遗留的"项目级删除"）
- 背景：`core.delete_project` 的规则已有单测（含任务时拒绝、名称不匹配拒绝、删除前写备份且备份可再导入），但**弹窗 UI 与"备份真的落到磁盘"**在冻结产物中没有证据；同时产品对"项目下仍有任务"会**拒绝**（UI 不提供级联删除入口，`cascade=True` 只被测试调用）。
- 方法：探针在真实 UI 里创建空项目（「新建项目」popover → `project_new_blank` → 名称 → `project_form_create`），回到项目 hub 按**名称**定位该项目行并取出 project_id，打开卡片菜单 → 删除 → 校验弹窗；runner 在磁盘上核对备份文件与"项目已不存在"。
- 实测（`ok: true`）：

| 观测 | 结果 |
| --- | --- |
| 创建 | 真实 UI 创建成功（project_id `bbf43606-…`，hub 中 1 行） |
| 弹窗打开时 | 确认按钮 **disabled** |
| 输入**错误**名称 | 确认按钮**仍 disabled** |
| 输入**精确**名称（`验收用空项目`） | 确认按钮 **enabled**（读回输入值一致） |
| 点击「永久删除」 | 项目行**消失** |
| **可恢复备份** | 磁盘上出现 `projects/_deleted/bbf43606-…-20261003T185646Z.json`，内容含项目名 |
| 项目是否仍在 | **否**（存储中已无该项目名） |
| 残留进程 | **0** |
- 过程修正的**探针**问题（非产品问题）：① 创建项目后应用会**进入该项目详情**，hub 的项目行随之消失 → 探针需回到 `?view=projects` 并按名称匹配行；② 环境性的"浏览器被关闭"抖动 → runner 增加一次重试，且第二次用**不同的项目名**避免与失败残留冲突。
- 已加入端到端 sweep（现为 **26 个用例**）+ 接线契约测试。
- 诚实标注：本条覆盖"空项目的删除"；"项目下仍有任务时拒绝删除"目前只有 core 单测覆盖（UI 分支已代码核查：错误提示 + 两个按钮、无确认输入），未在冻结产物中端到端取证。

---

## D1-24 — 项目删除的"仍含任务时拒绝"分支：**端到端未完成**（如实收尾）

- 时间：2026-10-03
- 状态：**未完成（本轮按产品方向调整收尾，不再继续调试该探针）**
- 已做：`scripts/d1_project_delete_runner.py --mode with-task` 与 `scripts/desktop_d1_project_delete.js --mode with-task` 已实现（先造真实任务 → 真实 UI 创建项目 → 展开「把任务移入本项目」→ 多选任务 → 移入 → 回到 hub → 卡片菜单删除 → 期望"拒绝 + 无确认输入 + 有「前往『任务』标签处理」"）。
- 实测结果：探针在等待 `st-key-project_move_panel_<pid>` 时**超时 180s**（`url_project_id` 已正确取到，说明项目创建成功、URL 带 `?project=<pid>`）。最可能原因：项目详情页的 mover 面板在该上下文未渲染/需要不同的展开入口。
- 当前可信的覆盖程度：
  - **core 层**：`tests/project_lifecycle_test.py::test_delete_project_refuses_while_it_still_has_jobs`（含任务时拒绝、且项目仍在）→ 已通过；
  - **UI 分支**：`app.py::_project_delete_modal` 的代码路径已核查（`jobs` 非空 → `st.error` + 「前往『任务』标签处理」/「取消」，**不渲染**名称输入与确认按钮）；
  - **端到端**：**未取证**（本条）。
- 交接提示：若后续仍需要该证据，优先检查项目详情页 mover 面板的渲染条件（是否只在特定 tab/视图下渲染），或改用任务侧「移到项目」入口（`_open_task_move_modal`）。
