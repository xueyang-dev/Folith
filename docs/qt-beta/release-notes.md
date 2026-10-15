# Folith 桌面端（Qt）— 受邀 Private Beta 发布说明

本文件由 `scripts/qt_release_checks.py --write-notes` 依据**本次构建**的清单与
DMG 记录生成；`--check-notes` 会在说明与当前产物不一致时失败（不做旧文本对比）。

## 入口

- 本说明只描述 DMG 内的 `Folith.app`：Qt Widgets 原生客户端（bundle id `dev.folith.qt`），由 `packaging/folith-qt.spec` 从 `qt_client.py` 冻结。这是 0.5.0 Beta 唯一的桌面入口。
- 源码等价入口：`python -m folith.qt`（或 `python qt_client.py`），需另装 PySide6。
- 源码与 Pip 安装的 `folith` 命令（含兼容别名）以及 `start.sh` / `start.command` / `start.bat` 仍启动旧 Streamlit 启动器（`gui.py`，保留一个版本），**不是**本客户端，Pip 安装说明也不能当作本客户端的安装说明。
- 旧 Streamlit + pywebview 桌面壳（`Folith-<版本>-macos-arm64.dmg`）已退役，`docs/desktop-d3/` 下的说明只作历史基线。

## 本候选范围

- 导入：PDF / DOCX / EPUB（无 DRM）/ TXT / Markdown；新格式共用任务、翻译、审校、恢复与交付流程，不提供原文件格式写回。
- 流程：准备 → 翻译 → 人工审校 → 交付，含术语治理、项目隔离的翻译记忆、段落编辑与结构修复、失败恢复、原页对照、交付门禁与不可变快照。
- 语言资产、项目记忆，以及学术专业模式（QA、案例、实践报告）已在本客户端提供；学术模式默认关闭，显式开启或打开既有学术任务时可用。
- 工作台动效验证切片：可见目标段落保持滚动位置；检查与修复侧栏可中断开合，支持减少动态效果与 RTL，Esc 收起回到编辑框。段落和 AI 操作保持固定，参考内容在下方滚动。动效尚未推广到导航、原页、浮窗及进度条。

## 产物

- 应用版本 / build id：`0.5.0` / `git:5725c3f`
- 构建时间（UTC）：`2026-10-15T04:28:25+00:00`
- 打包后端：`pyinstaller`；worktree dirty：`True`
- 目标平台 / 架构：`macos` / `arm64`
- 随包运行时：Python `3.11.3` / PySide6 `6.11.2` / Qt `6.11.2`
- DMG：`Folith-Qt-0.5.0-macos-arm64.dmg`
- DMG sha256：`c7411c1fe4fddc675a789a5a1ef624c194fcb5b056f83b0464e395f95550389c`（199.8 MB）

## 支持的系统

- 最低 macOS：**15.0.0**（依据：measured: scripts/qt_native_min_os.py (Mach-O LC_BUILD_VERSION minos)）
- 包内原生组件实测上限：`15.0.0`（445 个原生文件）
- 架构：arm64×445；arm64 完整：True；通用（universal）二进制文件数：0（本候选为 **arm64-only**，不含 Intel 切片）
- 本轮实际验收系统：`macOS-27.0.1-arm64-arm-64bit`
- 本候选原生依赖闭包要求 macOS 15.0.0；未在更低版本上验收，不声称支持更低版本。
- Intel Mac / Windows：本轮不在范围，未验收即不宣称支持。

## 随包能力与外部依赖

- OCR 引擎：Tesseract（随包）；语言：ara, chi_sim, eng
- 缺少语言包时明确报错，不会静默改用英语。
- PaddleOCR / LibreOffice：**不随包**；缺失时如实记录 `NOT_RUN`，不宣称渲染通过。
- 已确认**不包含**旧壳运行时：streamlit、webview、pywebview（实际命中：无）
- 包外绝对路径动态库引用：无
- 随包发行版共 9 项，版本取自包内 `METADATA`（完整字符串，不截断）；逐项见 `release-manifest.json` 的 `distributions`。
- 依赖表述：`requirements*.txt` 记录的是**版本范围**；本文件与清单记录的是本次构建**实际解析并随包**的版本（`distributions` 与 `runtime`）。未完成依赖锁定时不宣称完整环境可复现。

## 分发状态（如实披露）

- 签名：否；公证：否
- 适用范围：**受邀 Private Beta**。首次打开需在「应用程序」中右键 → 打开，为本应用明确放行一次；**不要**全局关闭 Gatekeeper。
- 公开分发（Developer ID / hardened runtime / 签名 / 公证 / staple / 真实下载验收）尚未完成，本候选不得当作公开版本发布。

## 卸载

- 把 `Folith.app` 拖到废纸篓只移除应用本身；任务、术语、翻译记忆和设置都会保留，重新安装后原样可见。
- 如需完全清除，先退出应用，再删除：`~/Library/Application Support/Folith`（任务与语言资产，与旧壳共用）、`~/Library/Logs/Folith`（日志）、`~/Library/Preferences/com.folith.QtDesktop.plist`（界面设置）。删除前请先导出需要保留的译文与 TMX/TBX。

## 已知限制

- 真实中文输入法、系统文件面板、睡眠/唤醒、真实模型服务与干净机器验收为**人工项**，尚未闭环；自动化与冻结产物证据不能替代。
- 有意不迁移到本客户端：性能面板、Paddle 任务级模型选择、线上 OCR 独立来源、风格微调滑杆（风格由智能画像与风格卡片决定）；Agent 检查器已并入工作台右侧操作栏。
- 数据目录 `~/Library/Application Support/Folith/data` 与旧壳共用；替换或删除应用不会删除用户数据。
- **macOS 26/27 上的外部辅助功能扫描（AI "Computer Use" / VoiceOver / 窗口管理器的AX 客户端）会触发 Qt 平台插件崩溃**（SIGSEGV/SIGBUS，栈顶 `libqcocoa` + `NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:`）：上游根因是 **QTBUG-149612**（macOS: synthesized table elements can delete their parent accessibility interface；影响 Qt 6.11.0–6.11.2，截至 2026-10-05 无修复版本），同样见于 calibre（PyQt6）、Qt Creator、Anki、FreeCAD。R62 已在本机复现 3/3 并逐帧核对，且实测**没有可用的应用侧开关**：`QT_ACCESSIBILITY=0` 在 Qt 6.11 上完全无效，`QAccessible.setActive(False)` 会被系统在 AX 客户端接入时重新激活。**请用人工键鼠完成验收，不要挂载 AX 代理**——人工键鼠操作不受此缺陷影响。
