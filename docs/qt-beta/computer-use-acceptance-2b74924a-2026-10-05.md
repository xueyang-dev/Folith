# 新候选 computer use 验收回传（2026-10-05）

本轮对象为 SHA-256 `2b74924a43c936f8a103e7e25d2694e3ebf58a72a9b1b26508fdb27eb59dacce` 的 DMG。实测一致，**未沿用旧候选的通过结果**。本轮未通过：发生两次 AX 原生崩溃，Dock 退出两次未生效；部分步骤受阻未验证，不声明 `PRIVATE_BETA_READY`。没有修改应用代码或构建产物；仅生成验收夹具、证据与本报告。

## 候选与机器

- 安装包：`dist/qt-desktop/Folith-Qt-0.5.0-macos-arm64.dmg`。
- 实际安装并启动的位置：`/Applications/Folith.app`。
- 安装后随包构建记录：0.5.0 / `git:1260a82` / `2026-10-05T10:27:27+00:00`。
- 机器：macOS 27.0.1（26A434）/ Apple M4 / arm64；有仓库和 Python 开发环境，不是干净机器。
- UI 操作使用 `mcp__cua_repl`；候选身份校验、测试文档准备和 `.ips` 读取使用命令行。

## 已执行的步骤

### A.1′ 安装与启动：PARTIAL

1. 最初访达连接报 ScreenCaptureKit `-3811`；用户按请求手动打开“下载”窗口后恢复连接。这是工具连接问题，不作为 Folith 缺陷。
2. 通过访达双击 DMG，确认出现 `Folith 0.5.0` 安装卷窗口，包含 `Folith`、`Applications` 和 `请先阅读.txt`。
3. 多次调用 computer use 的拖拽操作，仅改变了选中项，未复制应用。**真实拖拽安装未验证通过**。
4. 改用访达选中候选 → `⌘C` → 打开 `Applications` → `⌘V`。系统显示旧项目替换确认；选择“替换”，观察到复制进度。之后读取安装构建记录确认它已更新为本轮候选。
5. 在应用程序目录列表中双击 `Folith`，首页正常打开，显示原数据目录及 **92 个任务**。
6. 本机未出现首次安全放行提示。安装应用没有 `com.apple.quarantine` 属性，不能据此验证带 quarantine 的首次右键“打开”路径。只读检查显示 Gatekeeper `assessments enabled`；未更改全局安全设置。

### A.7″ 系统面板：FAIL（导入与取消通过；后续流程被原生崩溃阻断）

1. 准备非敏感、三段英文的真实小型文件 `fixtures/验收 样本.pdf` 与 `fixtures/验收 样本.docx`。
2. 点击“导入文档…”，辅助功能树明确显示系统 `open-panel`。通过“前往文件夹”选择中文且含空格名称的 PDF，点击 Open。
3. 应用显示“已导入 1 个任务：验收 样本.pdf。现在开始翻译吗？”，选择“稍后”。首页任务数 **92 → 93**，PDF 导入与任务创建通过。
4. 随后继续操作首页导入入口时 computer use 返回 `noWindowsAvailable`。系统记录证实候选已经发生原生 `SIGSEGV`，见下一节。
5. 重开候选后，首页可看到该 PDF 任务，任务总数仍为 **93**。
6. 用户恢复可见窗口后，工具重新打开原生导入面板并点击 Cancel。首页任务数仍为 **93**，取消不建任务通过。
7. 再通过原生面板选择 `验收 样本.docx`，点击 Open。应用显示“已导入 1 个任务：验收 样本.docx。现在开始翻译吗？”。点击“开始翻译”后发生第二次原生崩溃。重开后首页任务总数 **94**，DOCX 任务为三段、状态“处理中断”。DOCX 导入与任务保留通过。
8. 冻结交付保存到中文空格目录、覆盖确认与取消尚未完成。原生崩溃阻断工作台路径；不能据此单独判定文件保存面板有缺陷，也不能将未测步骤记为 PASS。

### B.1 中文输入法：NOT_RUN

本轮尚未进入已有译文的编辑器。系统面板期间的 AX 原生崩溃阻断了后续工作台验收；候选窗、组合输入、选中替换、撤销重做、粘贴、切段草稿及提交均未验证。没有证据将输入法本身判为产品 FAIL。

### A.8′ 真实服务：FAIL（连接通过；翻译小任务未跑通）

最初首页显示“尚未选择模型提供商”。工具打开“设置 → 模型连接”，请求用户只在应用内填写 Key、测试连接并保存。之后首页显示 **OpenCode Go / deepseek-v4.1-flash → 简体中文**，第二次崩溃重开后仍保持该配置。没有读取、输出或截图凭据。

睡眠唤醒后，工具通过 `⌘,` 打开已配置的设置，点击“测试连接”。界面先显示“正在测试…”，然后返回 **“连接正常：响应「OK」· 耗时 3.1s”**。真实连接测试通过；结果文字单独保存为不含 Key 的证据，未截图设置窗口。结束时应用停留在这个成功结果。

工具通过 DOCX 导入后的“开始翻译”启动非敏感三段小任务。该操作随即触发 AX 原生崩溃，重开显示“处理中断”；只读任务摘要为 `idle_incomplete`、三段。尚未确认服务请求是否成功返回，不能将这个失败归因于模型服务本身。PDF 完整流程、翻译完成、审校、重译、排除、冻结、保存、重开交付均未验证。

### C.2′ Dock 退出：FAIL（用户点击，工具检查）

1. computer use 绑定 `com.apple.dock` 和 `/System/Library/CoreServices/Dock.app` 均返回 `timeoutReached`。用户按请求打开 Folith 的 Dock 右键菜单后，工具仍无法读取或点击它。
2. 用户随后明确回复“已点击退出”。当时没有正在运行的模型任务，应用打开着“设置”对话框。
3. `2026-10-05 15:56:37 +0300` 的只读进程检查仍显示 PID **64970**；computer use 仍可读取“设置”对话框，没有退出或等待提示。再等 45 秒后，进程仍为 **64970**。因此记录设置对话框打开时 Dock 退出未生效。菜单点击由用户执行，未录得该次点击的菜单截图。
4. 工具随后点击设置“取消”，下一次读取返回 ScreenCaptureKit `-3812`。重连及重置工具会话后仍报同一错误，进程仍存活，未发现新 `.ips`。这是主窗口读取阻断，不能据此认定又发生了原生崩溃。
5. 第二次崩溃重开后，应用在主窗口，没有设置对话框；用户再次明确回复“主窗口状态已点击退出”。**16:05:32 +0300** 的进程检查显示 PID **65686**，AX 树仍为主窗口（94 个任务、DOCX 为“处理中断”），未见退出或等待对话框。保存窗口截图后再等 45 秒，**16:06:56 +0300** 仍为同一安装路径的 PID **65686**。该条件下退出也未生效。未用 `⌘Q`、应用菜单退出或进程信号替代。

### C.12 睡眠/唤醒：PASS（空闲首页可用性；运行中恢复未验证）

工具按手册尝试 `⌃⌥⏏`，但键名 `XF86Eject` 返回 `keyNotFound`，未触发睡眠。用户随后手动合盖并回复“已唤醒”。系统日志确认 **16:12:38 +0300 Clamshell Sleep → 16:15:56 +0300 Wake，共 198 秒**。

唤醒后仍为 PID **65686**，computer use 可读取首页并前置窗口、点击“刷新”、打开原生导入面板再取消，任务数保持 **94**，模型配置仍在。空闲首页唤醒后继续可用通过。因真实任务启动即 AX 崩溃，未验证运行中进度恢复、运行中取消或退出；不将这个有限通过结果扩大为手册全部睡眠情景已通过。

## 本轮原生崩溃（两次）

- 时间：`2026-10-05 15:41:43 +0300`（Asia/Riyadh）。
- PID：64815。
- 实际进程路径：`/Applications/Folith.app/Contents/MacOS/Folith`。
- 异常：`EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS`。
- 栈顶：`libqcocoa.dylib +553984`，随后是 AppKit 的 `NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:` 与 HIServices 的 `__AXCopyAttributeValueForHierarchy`。
- 本轮可确认的触发范围：**macOS 27.0.1，本候选与 computer use 的 AX 扫描共同运行，PDF 导入返回首页的交互期间**。没有据此推断普通人工键鼠操作同样失败。
- 当前速览已披露 AX 扫描的已知限制。本次记录是新候选的实际崩溃证据，不是引用旧候选的 `.ips`。

第二次记录为 `Folith-2026-10-05-160236.ips`：实际崩溃时间 **16:02:16.9857 +0300**，PID **64970**，同一安装进程路径，`EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS at 0x10`。发生在 DOCX 导入确认后点击“开始翻译”并读取界面期间。栈顶仍为 `libqcocoa.dylib +553984`，随后为同一 AppKit/HIServices AX 层级读取链。重开首页正常，两个新导入任务均保留，模型配置仍可见。

## 回传模板

没有实际执行的项目使用 `NOT_RUN`，以免将未验证行为误报为产品故障。系统面板与真实服务的 FAIL 表示本轮要求的完整路径没有通过；已通过的子步骤和未执行步骤分别说明，未据此制造独立的面板或服务端缺陷。

```text
候选 sha256：2b74924a43c936f8a103e7e25d2694e3ebf58a72a9b1b26508fdb27eb59dacce（实测一致）
验收机器：macOS 27.0.1 / Apple M4 / 非干净机器
1 中文输入法：NOT_RUN；本轮 AX 原生崩溃阻断工作台验收，七项输入行为均未验证。
2 系统面板：FAIL（本轮完整路径未通过）；PDF/DOCX 原生导入 PASS（92→93→94）；取消不建任务 PASS（仍为 93）。后续操作发生 AX 原生崩溃，交付保存和覆盖步骤未验证。
3 干净机器：NOT_RUN；当前机器有开发环境和仓库，没有可访问的干净 Mac。
4 真实服务：FAIL（小任务未跑通）；OpenCode Go / deepseek-v4.1-flash 配置保存并保留，真实连接测试 PASS（响应 OK，3.1s）。小 DOCX 点击“开始翻译”即 AX 原生崩溃，重开为“处理中断”；翻译任务的服务响应未验证，未附 Key。
5 睡眠唤醒：PASS（空闲首页）；系统确认合盖睡眠 198 秒，唤醒后同一进程可刷新、打开并取消原生面板。运行中任务恢复未验证。
6 生命周期与交互：FAIL；用户分别在设置窗口和主窗口点击 Dock“退出”，每次超过 45 秒对应进程仍存活（PID 64970 / 65686），未见退出提示。访达复制安装并双击启动 PASS；真实拖拽、首次安全放行未验证。
其他问题（含原生崩溃请附 .ips）：本轮两次 AX 扫描期间 SIGSEGV，见 Folith-2026-10-05-154202.ips 与 Folith-2026-10-05-160236.ips。Gatekeeper 保持开启。
```

## 证据

- [安装后的首页](../../tmp/qt-beta/computer-use-2b74924a/evidence/01-installed-home.png)。
- [PDF 原生导入面板](../../tmp/qt-beta/computer-use-2b74924a/evidence/02-pdf-native-panel.png)。
- [主窗口 Dock 退出后仍存在](../../tmp/qt-beta/computer-use-2b74924a/evidence/03-main-window-after-dock-quit.png)。
- [睡眠唤醒后首页](../../tmp/qt-beta/computer-use-2b74924a/evidence/04-home-after-sleep-wake.png)。
- [系统睡眠唤醒记录](../../tmp/qt-beta/computer-use-2b74924a/evidence/sleep-wake.log)。
- [不含 Key 的真实连接结果](../../tmp/qt-beta/computer-use-2b74924a/evidence/connection-result.txt)。
- [本轮原生崩溃 .ips](../../tmp/qt-beta/computer-use-2b74924a/evidence/Folith-2026-10-05-154202.ips)。
- [第二次原生崩溃 .ips](../../tmp/qt-beta/computer-use-2b74924a/evidence/Folith-2026-10-05-160236.ips)。
- [崩溃摘要](../../tmp/qt-beta/computer-use-2b74924a/evidence/crash-summary.json)。
- [第二次崩溃摘要](../../tmp/qt-beta/computer-use-2b74924a/evidence/crash-summary-160236.json)。
- [本轮观察记录](../../tmp/qt-beta/computer-use-2b74924a/observations.json)。
