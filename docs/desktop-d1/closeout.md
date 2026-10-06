# D1 里程碑收尾审计（Lifecycle & Data Safety）

日期：2026-10-03 · 路线图：`docs/desktop-client-roadmap.md` v1.1 · 证据：`docs/desktop-d1/evidence-log.md`、`docs/desktop-d0/`

审计口径（prompt §25）：逐条对照 roadmap 要求与 D1 完成条件，显式标注
`PASS` / `FAIL` / `BLOCKED` / `NOT APPLICABLE` / `NOT YET TESTED`。
**强制项仍为 NOT YET TESTED 即不算完成**。

产品负责人决定（2026-10-03）：macOS 优先，Windows 之后解决；不宣称双端成立。

---

## A. 逐条要求

| # | 要求（roadmap 出处） | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | 桌面入口在导入 core 前设置绝对数据目录（§3.2） | **PASS** | D0 服务子进程 `FOLITH_OUTPUT_DIR` 实测；D0-4g 中文+空格路径 |
| 2 | 数据/日志目录为各平台标准位置（§3.2） | **PASS（macOS）** | D0-4e/4g：`~/Library/Application Support/Folith/data`、`~/Library/Logs/Folith`；Windows **BLOCKED** |
| 3 | 临时文件 / 可删缓存与任务资产分开，清缓存不得删除源文件 / 检查点 / 交付快照（§3.2） | **NOT APPLICABLE（有证据）+ 约束已加回归保护** | D1-9：`grep` 全仓无任何清缓存功能（无 `清理缓存/清除缓存/清空缓存/删除缓存/clear_cache`）；共享 OCR 缓存位于 `<data>/ocr_profile_cache/<sha16>`（**独立顶层子树**，不在任何任务目录内），任务目录内可删的只有 `.performance` 与锁文件；全仓仅 16 处破坏性调用，**没有一处按名触及** `source.bin` / `state.json` / `source_cleanup_checkpoint` / `delivery_snapshots`（唯一例外是 journal 自身的轮转）。新增 `tests/desktop_data_safety_test.py` 把该约束固化为回归测试 |
| 4 | 接入旧数据目录：选择、停旧实例、复制并保留原目录、含全部任务资产、核对可读后才切换、失败不覆盖、不扫描磁盘、不建通用迁移框架（§3.2） | **PASS** | D1-4：原目录 25 文件逐文件 sha256 不变；23 文件资产集合一致；凭据不随迁；试运行不落盘（D1-12 升级为**两棵树逐文件 no-op 证明**，含目标已存在时）；失败时目标逐文件不变；持锁拒绝 |
| 5 | 升级只更新应用、保留数据（§3.2） | **NOT APPLICABLE（D2）** | 安装器 / 升级属 D2（§4/§5 表）；D1 不建迁移体系 |
| 6 | 退出：无任务、无未保存编辑 → 直接退出并回收服务（§3.3） | **PASS** | D0 退出矩阵（idle）+ D1 各实验的 `launch stop` |
| 7 | 退出：未失焦编辑 → 先同步 WebView textarea，保存后退出；失败不得显示"已保存"（§3.3） | **PASS** | D1-2a：草稿 0 → 关窗冲刷 → 提交 → `safe_to_exit`；失败路径单元测试拒绝谎报 |
| 8 | 退出：运行任务 → 停止安排新操作、有界等待、复用检查点 / journal、worker 释放锁后再提交并停服务（§3.3） | **PASS** | D1-2b：`cancel_clicked/left_active_status/worker_lock_released` 全 true；8 段已落盘；`delivery_status=draft` |
| 8b | 退出：**暂停中的任务**（roadmap §6.1「D1 再扩展暂停…矩阵」） | **PASS** | D1-7：暂停请求被接受（`paused`）→ 真实关窗走 `cancel-task` 并释放 worker 锁 → 8 段落盘、`delivery_status=draft`、0 残留 → 重开后 8/8→12/12 完成；零丢失、未伪报完成 |
| 9 | 退出：超时 → 明确提供"继续等待 / 强制退出"；强制退出只承诺已落盘结果可恢复（§3.3） | **PASS（对话框本体人工）** | D1-2b 单元测试两条分支；**D1-15 端到端**：外部持锁制造真实超时 → 第一次关窗 `safe_to_exit=false / forced=false / action=incomplete`（含 `force-prompt`，**拒绝谎报安全**）且应用保持打开；释放锁并记录 worker 停止后第二次关窗 `safe_to_exit=true / saved-and-stopped`，0 残留、12 段零丢失。原生对话框点击仍需人工 |
| 8c | 桌面退出码契约与失败可见性（§3.1「失败显示原因和日志入口」） | **PASS** | D1-8：冻结产物实测 自动化脚本加载失败→**8**、`--serve` 被直接调用→**3**（有界失败、0 残留服务/监听）；单元测试覆盖 **3/4/8/0** 四条退出码 |
| 10 | 崩溃 / 强制结束 / 断电 → 复用 interrupted / stalled 识别与检查点恢复，不把未完成任务标成完成（§3.3） | **PASS** | 本轮崩溃实验：`kill -9` 服务中途 → 重开显示"运行已中断，进度已保存" + 提供恢复控件 → 恢复后 `4/4 → 12/12`；`delivery_status` 始终 draft |
| 11 | 睡眠不保证计算继续；唤醒后识别断连 / 任务状态，提供继续或重试（§3.3） | **PASS（状态识别）/ NOT YET TESTED（真实睡眠）** | 服务意外退出即报错并给出日志入口（D0-4e、本轮）；真实机器睡眠/唤醒需人工且会打断用户机器，未执行 |
| 12 | 应用级系统锁 + 窗口唤起；不能把任务锁当应用锁（§3.1.4） | **PASS** | D1-1：独立锁路径（`state_dir/.instance.lock`）；第二次启动 exit 10 + 唤起；崩溃竞态最大并发服务数 = 1 |
| 13 | 诊断：app/build/schema/运行时/OS/架构/打包后端/数据与日志目录（§3.5） | **PASS** | D1-5 冻结导出实测 |
| 14 | 诊断：`job_state=unversioned`，runtime/journal/drafts 从既有常量或数据读取；不新建状态格式（§3.5） | **PASS** | D1-5：`observed` 从真实文件读出；与 `core` 常量一致性断言 |
| 15 | 诊断导出：隐去用户名 / 项目名，排除 API Key、startup token、provider 配置、环境变量全文、文档内容；不打包整个数据目录或未筛选日志；不建遥测（§3.5） | **PASS** | D1-5：预埋凭据/文档内容 0 泄漏；错误摘要过滤+脱敏；无遥测代码 |
| 16 | 应用级退出（Cmd+Q / Dock）走同一 save & stop（D0 发现） | **PASS** | D1-3：真实 `quit app` → 退出流程步骤齐全 → 认证停止，EOF 兜底消失 |
| 17 | **D1 完成条件**：未失焦编辑保存 | **PASS** | D1-2a、D1-3（两条路径都验证） |
| 18 | **D1 完成条件**：已落盘恢复 | **PASS** | D0 重开、D1 崩溃恢复、D1-4 接入后重开 |
| 19 | **D1 完成条件**：锁 / 进程清理通过 | **PASS** | D1-1（锁释放/竞态）、D1-2b（worker 锁释放）、D0 退出矩阵（无残留） |
| 20 | D1 产物：可恢复的开发构建（§6 表） | **PASS（macOS）/ BLOCKED（Windows）** | 冻结 `.app` 308 MB，PyInstaller 6.22.3；Windows 无主机 |

---

## B. 结论

**macOS D1 达到完成条件**（第 17–19 项全部 PASS，§3.2/§3.3/§3.5/§3.1.4 的 D1 条款均有实测证据）。

未闭环项（全部为人工 / 外部环境，非工程缺口）：

| 项 | 分类 | 说明 |
| --- | --- | --- |
| 真实机器睡眠 / 唤醒 | NOT YET TESTED（人工） | 会打断用户机器；已证明的等价能力：断连识别 + 中断状态识别 + 恢复控件可用 |
| 原生"继续等待 / 强制退出"对话框点击 | NOT YET TESTED（人工） | 两条分支已由注入回调的单元测试覆盖 |
| 真实操作系统级中文 IME / 光标 / 字体 | NOT YET TESTED（人工，D0 遗留） | 无辅助功能授权（AppleEvent `-1712`） |
| 真实模型调用（授权、受控范围） | NOT YET TESTED（需授权） | 产生费用，需用户明确授权 |
| Windows D1 构建与验收 | BLOCKED（缺设备） | 产品负责人已决定 macOS 优先 |
| 签名 / 公证 | BLOCKED（无 Developer ID） | 阻塞 D3 |

## C. 回归状态（本轮结束）

| 门禁 | 结果 |
| --- | --- |
| `tests/desktop_legacy_import_test.py` + `desktop_diagnostics_test.py` + `desktop_exit_flow_test.py` + `desktop_single_instance_test.py` + `desktop_frozen_test.py` + `desktop_handshake_test.py` + `gui_launcher_test.py` | **61 passed**（含冻结产物与真实 WKWebView 探测） |
| 产品回归子集（`ui_console_test` / `persistence_phase_a_test` / `delivery_snapshot_test`） | **38 passed** |
| 端到端脚本 | `d1_exit_runner`（probe / quit 两种触发）、`d1_running_exit_runner`、`d1_crash_recovery_runner`、`d1_import_runner` 全部 `ok: true` |

## D. 下一步

D1 之后按 roadmap 顺序进入 **D2 — Installable Beta**。结合"macOS 优先"的决定，macOS D2 的可执行子项为：

1. 应用标识与原生集成（Dock / 菜单 / Cmd 快捷键）；
2. 随包运行时与 OCR 基线（eng / chi_sim / ara 语言包 + `configs/`，D0 已证明 `configs/` 必需）；
3. LibreOffice 的 `/Applications` 发现（§3.4 D2 项）；
4. 文件集成（打开交付文件 / 显示所在文件夹）；
5. DMG 打包与安装入口。

其中"**无开发环境的干净机器以普通用户完成验收**"（D2 完成条件）需要一台干净 Mac → 属外部环境，将在该子项完成时按 STOP B 明确记录。
