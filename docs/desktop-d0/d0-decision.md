# D0 — Desktop Architecture Spike：决策记录

日期：2026-10-03 · 依据：`docs/desktop-client-roadmap.md` v1.1 · 工作树 HEAD `1260a82`（未提交改动）
证据：`docs/desktop-d0/evidence-log.md`、`docs/desktop-d0/environment.md`、`/tmp/d0-*/evidence.json`（本轮运行产物）

---

## A. Architecture decision

> 说明：macOS 的 GO 由全部**可自动化**证据支撑；D0 仍有一个强制项未闭环——
> 真实操作系统级中文 IME / 光标 / 字体验证需要人工（本会话无辅助功能授权，
> `System Events` 返回 AppleEvent `-1712`）。按 roadmap §25，"强制项仍为
> NOT YET TESTED 即不算完成"，因此 macOS D0 记为 **GO（待人工确认 IME 一项）**。

| 平台 | 决策 | 依据 |
| --- | --- | --- |
| macOS arm64 | **GO — macOS / PyInstaller** | Option A（PyInstaller）在同一垂直切片上通过：`.app` 冷启动、身份握手、真实任务链、交付下载、重开恢复、无残留进程。未出现需要切到 py2app 的 PyObjC / 资源 / bundle 阻断。 |
| Windows x64 | **BLOCKED — 缺设备** | 本环境只有 macOS 主机，无法执行 roadmap §4 的 Windows 切片与实机验收。这是外部环境缺失，不是已证实的技术不兼容。 |

结论：**单端 GO（macOS / PyInstaller）+ 单端 BLOCKED（Windows / 缺设备）**。
按 roadmap §6.2，**双端 D0 均通过前不进入大规模 D1 / D2**；macOS 侧可以继续 D1 的本地工程（生命周期与数据安全），但不得宣称双端成立。

## B. Vertical slice（实际证明的真实用户路径）

在**冻结 `.app`** 上，通过真实 UI（浏览器驱动 + 原生 WKWebView 双通道），零外网模型调用，完整走通：

```
启动（随机端口 + 身份握手）→ 配置 AI 引擎（本机兼容端点）
→ 导入真实 DOCX / 文本层 PDF / 扫描 PDF / Arabic 扫描件
→ 运行小型任务（3 / 12 / 54 段）
→ 工作台人工修改 → 提交编辑（落盘 human_edited=true）
→ 独立审校（编辑后失效段落全部重新审校至 0 待复审）
→ 正式交付门禁通过 → 创建交付版本 v1（delivery_status=final）
→ UI 下载交付文件（下载副本与快照副本都包含人工编辑）
→ 正常退出（服务/端口/进程全部回收）
→ 重开（新进程）→ 任务、编辑、交付均可读
```

五个切片全部 PASS（DOCX、PDF 文本层、扫描 OCR eng、Arabic 扫描 OCR ara、目标语言 العربية），详见证据日志 D0-4a。

同一冻结产物上另有专项验证（同样零外网模型调用）：

- **混合中英阿（CN/EN/AR）编辑与导出**：三脚本同段的源文正确分段，混合人工编辑进入交付 DOCX（外部 `textutil` 可读），Arabic 段落带 `w:bidi`、文本未被反转，快照 manifest 5 个资产哈希全部一致（D0-4i）；
- **多句切换与提交**：第 0/2/4 段分别编辑并提交、切走再切回逐字一致、磁盘 `human_edited=[0,2,4]`，**重启服务后复读一致**（D0-4j）；
- **fragment 轮询 / 长文本（961 字符含中英阿）/ 重开读回**：D0-4c / D0-4d 既有证据；
- **源码 CLI 启动路径保持不变**：`python gui.py --legacy` 仍走解释器路径（HTTP 200 + `-m streamlit run app.py`），并有 4 项分派回归测试（D0-9）。

## C. Platform evidence

### macOS（Apple Silicon，macOS 27.0.1 arm64）
- 冻结构建：PyInstaller 6.22.3 onedir + `BUNDLE` → `Folith.app`，308 MB
- 冷启动：`--serve` 就绪到可服务 ≤ 数秒（探测：服务 ready 后 1.4s 内完成一次完整交互探测）
- 身份握手：`pipe_identity_verified` / `port_ownership_verified` / `process_tree_verified` 全为 true
- 垂直切片：5/5 PASS；原生窗口：渲染 / rerun / 长文本提交 / RTL 均通过
- 退出：正常退出与运行中退出都无残留进程、端口释放

### Windows
- **未执行**（无 Windows 主机）。不做任何 Windows 结论；WebView2、pythonnet、开始菜单启动、普通账户安装全部保持 NOT YET TESTED。

## D. Packaging decision

| 平台 | 实测 packager | 说明 |
| --- | --- | --- |
| macOS arm64 | **PyInstaller 6.22.3（onedir + .app）** | Option A 通过，不需要 Option B（py2app）。onedir 下 `service_pid == Popen.pid`（无 bootloader 子进程分叉），资源位于 `Contents/Resources`，`app.py` 作为数据文件由 Streamlit 按路径执行。 |
| Windows x64 | 未测（roadmap 建议 PyInstaller 目录原型 + Inno Setup） | BLOCKED |

构建记录（随包 `desktop_build_info.json`）：app_version 0.5.0、build_id `git:1260a82`、python 3.11.3、streamlit 1.64.0、pywebview 6.2.1、pyinstaller 6.22.3、target macos arm64。

决策记录覆盖项（roadmap §5）：PyObjC 收集 ✅（无需手工补丁，cocoa 后端 + pyobjc 框架正常）、WebKit ✅、app bundle ✅、resource loading ✅（`app.py` 与 `folith/resources` 从 `Contents/Resources` 正常加载）、cold start ✅（窗口→首屏 2.65 s / 5.42 s 两次实测）、Finder launch ✅、重复构建 ✅（4 次同尺寸 308 MB）、signing / hardened runtime / notarization **BLOCKED（本机 0 个签名身份，产物为 ad-hoc 签名）**。

## E. WebView behaviour

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| WebSocket / rerun | PASS | 真实窗口内 `websocket_connected=true`、点击控件后 DOM 变化 |
| fragment | PASS | 运行中观测 12s：侧栏锚点标记存活（无整页 rerun）+ 可见文本自行变化；源码 `app.py:3261` `@st.fragment(run_every="2s")` 隔离轮询 |
| 长文本编辑 | PASS | WKWebView 内 961 字符（含中英阿混排）→ 保存 → 磁盘 `human_edited=true`、长度 961 |
| 中文 IME / 组合输入 | **部分验证（D0 唯一未闭环的强制项）** | 模拟 `compositionstart/update/end` 后 value 不丢；真实操作系统级 IME / 光标移动 / 字体需人工。自动化被系统拒绝（`System Events` AppleEvent `-1712`，无辅助功能授权） |
| RTL / Arabic | PASS（布局与编辑） | 目标语言 العربية 时编辑器 `direction: rtl`；Arabic 扫描件 OCR（ara）→ 编辑 → 交付 → 重开可读 |
| 上传 | PASS（DOM 文件输入） | 浏览器侧 `setInputFiles` 走真实 uploader；**原生 OS 打开对话框需人工** |
| 下载 / 保存 | **产品缺陷已修复 + 需人工确认** | pywebview `ALLOW_DOWNLOADS` 默认 False → 原生窗口下载被静默取消；已开启，改走系统保存面板；模态面板的确认动作无法自动化 |
| 交付文件可外部打开 | PASS | macOS 识别为 `org.openxmlformats.wordprocessingml.document`；系统自带 `textutil` 读出人工编辑文本；快照 manifest 五个资产 sha256/size 全部一致 |
| 启动 / 关闭 | PASS | 关窗（`window.destroy`）走认证停止；Finder 启动、终端启动、运行中关闭都回收服务与端口 |
| 普通桌面启动（Finder） | PASS | `open Folith.app`：握手/端口归属/进程树全部核验通过，数据目录为 macOS 默认位置，最小 PATH 下 OCR 仍成功；完整切片 job `ee827ef50d9d7c0b` 通过 |
| 桌面环境工具发现 | Tesseract PASS / LibreOffice 准确 NOT_RUN | 最小 PATH 下 `_find_tesseract()` 仍命中绝对路径；LibreOffice 发现仅查 PATH → 证实 roadmap D2 项 |
| 应用退出（Cmd+Q / Dock） | **缺陷，D1 处理** | `quit app` 绕过认证停止，靠 stdin EOF 兜底回收（无残留）；D1 需实现应用级退出序列 |
| 服务意外死亡提示 | 已修复 | 原先静默 exit 0；现打印原因 + 日志路径并返回 exit 9（含回归测试） |
| 脱离仓库 / venv 运行 | PASS | 冻结 `.app` 复制到仓库外运行，身份握手 + 完整切片通过 |
| 中文与空格路径 | PASS | 数据目录 `/tmp/d0 中文 路径/切片 输出`、源文件 `中文 文件名 测试文档.docx`、中文编辑；交付 DOCX 合法且含编辑，重开可读 |
| Applications 位置启动 | PASS | `~/Applications/Folith.app` 经 `open` 启动并完成 OCR 切片（job `ab634f020071db92`） |
| 缺 pywebview 不回落浏览器 | PASS | 硬失败退出码 3 + 可操作提示，并回收已启动的服务 |

## F. Process lifecycle

- **父进程（launcher）**：桌面入口，持有唯一写端控制管道；记录 `instance_id` / `startup_token` / `launcher_pid`。
- **服务进程（service）**：`--serve` 内部模式；stdout 独占为控制通道（Streamlit 输出改道 stderr）；OS 随机端口（实测 54066 / 55269 / 56297 等）；上报真实 `service_pid` / `port` / 版本 / build。
- **worker**：服务进程内的后台线程（roadmap §2 事实一致），不单独产生进程。
- **清理**：正常退出 → 认证 stop → 服务停止 → 无残留；运行中退出 → 有界等待后强制终止 → 无残留；launcher 被 `SIGTERM` → 服务检测 stdin EOF 自行退出（无孤儿）。
- **stale 识别**：身份来自本次启动经管道下发的 token；HTTP 200 不参与就绪判定；同机无关 HTTP 服务不会被误认（测试覆盖）。
- **身份握手**：token 不进命令行 / URL / 日志 / 诊断；端口归属由 `lsof`/`netstat` 校验"监听该端口的 PID == service_pid"。

## G. Known defects

| # | 缺陷 | 严重度 | 是否阻断 D1 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | pywebview `ALLOW_DOWNLOADS` 默认 False，原生窗口下载被静默取消 | 高（文件保存是 D0/D2 必验项） | 否 | 已修复（`gui.py`），已加回归测试；端到端保存面板确认待人工 |
| 2 | 运行中退出依赖强制终止（worker 继续调度后续批次） | 中（数据未损坏，但可能重复计费） | 否（D1 正是要解决） | 记录为 D1 输入 |
| 3 | 冻结后 `streamlit.runtime.scriptrunner.magic_funcs` 缺失导致 app.py 无法执行 | 高 | 否 | 已修复（spec hiddenimports） |
| 4 | 随包 tessdata 缺 `configs/` 时 OCR 静默失败 | 中（会误报"未识别到文本"） | 否 | 已记录为 D2 bundling 硬性要求 |
| 5 | 冻结包内 `importlib.metadata` 会读到仓库 venv 的过期版本（0.4.0） | 低（诊断准确性问题） | 否 | 已修复（构建记录优先） |
| 6 | 原生窗口标题仍为 `Streamlit` | 低（观感） | 否 | 未处理（roadmap §10 明确不优先做观感） |
| 8 | Cmd+Q / Dock 退出绕过认证停止（靠管道 EOF 兜底回收） | 中（退出语义与关窗不一致） | 否（D1 范围） | 记录为 D1 输入 |
| 9 | 服务意外退出原先无提示（静默 exit 0） | 中（用户无从得知） | 否 | 已修复（exit 9 + 原因 + 日志路径 + 回归测试） |
| 7 | 包体积 308 MB | 低（D2 需裁剪） | 否 | 记录，D2 处理 |
| 10 | 无 Developer ID：Hardened Runtime / 公证未验证 | 中（阻塞 D3，不阻塞 D1） | 否 | BLOCKED（外部凭据） |

## H. Roadmap impact

需要回写 roadmap 的假设（均有实测证据）：

1. **§3.1.2 端口 0**：结论成立，但必须补一句"必须显式调用 `bootstrap.load_config_options(flag_options=...)`；只注册 config watcher 不会应用本次 flag，会静默落回 8501 并可能与其他实例共用端口"。
2. **§5 packager 决策**：macOS Option A（PyInstaller）在本切片上通过；onedir 下 `service_pid == Popen.pid`（roadmap 提醒的 bootloader 分叉在 onedir 未出现，onefile 未测）。
3. **§3.4 文件保存**：需补充"pywebview 必须显式开启 `ALLOW_DOWNLOADS`，且 macOS 保存为 `NSSavePanel` 模态对话框 → 该验收项无法脚本化，必须人工确认"。
4. **§3.4 OCR bundling**：需补充"随包 tessdata 必须同时携带 `configs/`（至少 `configs/tsv`）与 `tessconfigs/`，否则 OCR 报错信息具有误导性"。
5. **§6.1 真实模型调用**：需明确该步骤需要用户明确授权（费用），可与架构 GO 解耦。
6. **§14 工具发现**：Finder 启动环境实测为 `PATH=/usr/bin:/bin:/usr/sbin:/sbin`；Tesseract 因已有绝对路径探测不受影响，LibreOffice 仍只查 PATH → §3.4 的 D2 发现项确有必要。
7. **§3.1 / §3.3 应用退出**：macOS 标准退出（Cmd+Q / Dock）不会走启动器的认证停止流程，回收依赖管道 EOF；D1 的应用级退出必须显式实现。服务意外退出必须显示原因与日志入口（D0 已修复为 exit 9）。
8. **§2 表格**："暂停不能直接作为保存后退出"的判断在运行中退出实验中被再次证实（当前实现依赖强制终止）。

未发现需要推翻 roadmap 的假设。

## I. Recommendation

```text
PROCEED TO D1 WITH DOCUMENTED NON-BLOCKING RISKS
```

产品负责人决定（2026-10-03）：**先在当前 macOS 机器上推进 macOS 侧开发，Windows 之后另行解决**。
因此 macOS D1 工程继续推进，Windows 的 D0 切片保持 BLOCKED，且**不宣称双端成立**（roadmap §6.2）。
D1 的证据记录见 `docs/desktop-d1/evidence-log.md`。

理由：
- macOS 侧 D0 架构 Go/No-Go 有完整证据（冻结切片 + 身份握手 + 生命周期 + 重开恢复）；
- Windows 侧为外部环境 BLOCKED，按 roadmap §6.2 不宣称双端成立，但不阻塞 macOS 本地 D1 工程；
- 非阻断风险已逐项记录（下载面板人工确认、IME 人工确认、包体积、真实模型调用授权）。

进入 D1 的前置动作（建议顺序）：
1. 把本轮验证固化为可重复门禁（`tests/desktop_handshake_test.py`、`tests/desktop_frozen_test.py`、`scripts/d0_*`）；
2. D1 第一项：把"运行中退出"从强制终止升级为"停止调度 + 有界等待 + 检查点/journal"（roadmap §3.3）；
3. D1 第二项：应用级退出序列（Cmd+Q / Dock，§3.3）与桌面诊断（§3.5）、旧数据目录接入（§3.2）；
4. Windows 设备可用后立即补 D0 Windows 切片（PyInstaller 目录原型 + WebView2）。
