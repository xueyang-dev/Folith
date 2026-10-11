# Folith 桌面端（Qt）— 受邀 Private Beta 发布说明

本文件由 `scripts/qt_release_checks.py --write-notes` 依据**本次构建**的清单与
DMG 记录生成；`--check-notes` 会在说明与当前产物不一致时失败（不做旧文本对比）。

## 产物

- 应用版本 / build id：`0.5.0` / `git:38e93d3`
- 构建时间（UTC）：`2026-10-11T05:27:28+00:00`
- 打包后端：`pyinstaller`；worktree dirty：`False`
- 目标平台 / 架构：`macos` / `arm64`
- 随包运行时：Python `3.11.3` / PySide6 `6.11.2` / Qt `6.11.2`
- DMG：`Folith-Qt-0.5.0-macos-arm64.dmg`
- DMG sha256：`5d2941853e81a230a8b624cdf03085916c3589433eff1ec81c044f150b0d9442`（199.5 MB）

## 支持的系统

- 最低 macOS：**15.0**（依据：measured: scripts/qt_native_min_os.py (Mach-O LC_BUILD_VERSION minos)）
- 包内原生组件实测上限：`15.0.0`（445 个原生文件）
- 架构：arm64×445；arm64 完整：True；通用（universal）二进制文件数：0（本候选为 **arm64-only**，不含 Intel 切片）
- 本轮实际验收系统：`macOS-27.0.1-arm64-arm-64bit`
- 未在 macOS 13 / 14 上验收：随包 Qt 6.11 与 Tesseract 均为 `minos 15.0`，因此本候选**不支持** macOS 13 / 14，也不声称支持。
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

## 已知限制

- 真实中文输入法、系统文件面板、睡眠/唤醒、真实模型服务与干净机器验收为**人工项**，尚未闭环；自动化与冻结产物证据不能替代。
- 高级报告、案例库与完整语言资产管理界面未迁移到 Qt；保留旧实现，Qt 中不提供无效入口。
- 数据目录 `~/Library/Application Support/Folith/data` 与旧壳共用；替换或删除应用不会删除用户数据。
- **macOS 26/27 上的外部辅助功能扫描（AI "Computer Use" / VoiceOver / 窗口管理器的AX 客户端）会触发 Qt 平台插件崩溃**（SIGSEGV/SIGBUS，栈顶 `libqcocoa` + `NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:`）：上游根因是 **QTBUG-149612**（macOS: synthesized table elements can delete their parent accessibility interface；影响 Qt 6.11.0–6.11.2，截至 2026-10-05 无修复版本），同样见于 calibre（PyQt6）、Qt Creator、Anki、FreeCAD。R62 已在本机复现 3/3 并逐帧核对，且实测**没有可用的应用侧开关**：`QT_ACCESSIBILITY=0` 在 Qt 6.11 上完全无效，`QAccessible.setActive(False)` 会被系统在 AX 客户端接入时重新激活。**请用人工键鼠完成验收，不要挂载 AX 代理**——人工键鼠操作不受此缺陷影响。
