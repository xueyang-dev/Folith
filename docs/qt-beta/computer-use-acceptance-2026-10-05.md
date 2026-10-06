# Computer use 优先四项验收记录（2026-10-05）

本次按 [acceptance-quickstart.md](acceptance-quickstart.md) 的顺序验收。**四项未全部完成，不能据此声明 `PRIVATE_BETA_READY`。**

## 候选与环境

- DMG：`dist/qt-desktop/Folith-Qt-0.5.0-macos-arm64.dmg`。
- 实测 SHA-256：`1257a3c2d5837d576e84c02a39cbbe6bd30218764fe9994113197e5a5e72d4f9`，与速览一致。
- 运行应用：`dist/qt-desktop/Folith.app`；应用诊断显示 0.5.0 / `git:1260a82`，随包 Python 3.11.3、PySide6 6.11.2、Qt 6.11.2。
- 本机：macOS 27.0.1（26A434）、Apple M4、arm64；有仓库和 Python 开发环境，**不是干净机器**。
- UI 操作使用 `mcp__cua_repl`；文件准备、产物校验和崩溃记录读取使用命令行。应用通过已支持的 `--data-dir` / `--log-dir` 参数启动隔离会话。
- 原用户数据首页显示 78 个任务、未配置模型。验收使用 `tmp/qt-beta/frozen-window/data` 的副本，放在 `tmp/qt-beta/computer-use-2026-10-05/data`；其中 DOCX 有 3 段译文和冻结交付 v1。该既有任务由受控端点生成，**不计入真实服务通过证据**。

## 四项结果

| 优先项 | 本次结果 | 实际证据与未完成部分 |
| --- | --- | --- |
| 1. 真实中文输入法 | NOT_RUN（进入编辑器被阻断） | 两次通过 computer use 点击“打开任务”均导致候选进程崩溃；未能进入译文框。候选窗口、选词、组合输入、撤销重做、粘贴、切段草稿及提交均未验证。不能将此记录成输入法自身 FAIL。 |
| 2. 系统文件面板 | PARTIAL | 原生 `open-panel` 选择 PDF 与 DOCX、创建任务、取消不建任务通过；Finder 双击现有冻结 `translation.docx` 后 WPS 显示中文正文通过。交付页访问被崩溃阻断，保存到中文空格目录、重复保存的取消/覆盖以及应用内“用默认应用打开”未验证。 |
| 3. 干净机器 | NOT_RUN | 当前机器不符合无 Python / Homebrew / 仓库副本的要求，没有可访问的另一台干净 Mac。DMG 拖拽安装、Gatekeeper 单应用放行、干净机器主流程和卸载保留数据未验证。未把隔离数据目录或此前干净 HOME 探针当作干净机器验收。 |
| 4. 真实模型服务 | NOT_RUN | 首页显示“尚未选择模型提供商”。已请求用户在应用中自行配置连接，不接收聊天中的 Key。本轮未使用真实凭据、未向模型服务发送文档。完整流程、重译、排除/恢复、退出重开均未验证。 |

### 系统面板已通过的操作

1. 准备独立、非敏感的小型测试文档：`fixtures/真实流程 样本.pdf`（实际文本层 PDF）与 `fixtures/真实流程 样本.docx`（实际 DOCX），均位于本次验收目录。
2. 点击“导入文档…”后，辅助功能树显示原生 `open-panel`，含系统边栏、Where、Cancel、Open。
3. 通过系统“前往文件夹”选择 PDF，点击 Open，再选“稍后”。首页任务数 **2 → 3**，状态栏显示“已创建 1 个新任务（真实流程 样本.pdf）”。
4. 同样选择 DOCX。首页任务数 **3 → 4**，状态栏显示“已创建 1 个新任务（真实流程 样本.docx）”。
5. 再次打开导入面板并点击 Cancel。首页仍为 **4 个任务**，状态栏显示“已取消导入，没有创建任何任务。”
6. Finder 双击副本中的既有冻结 `translation.docx`；默认应用 WPS Office 打开标题为 `translation.docx` 的文档，页面显示 3 段中文正文，含“人工修改：这是桌面端写入的译文。”。这是对既有冻结文件可打开性的验证，不代表本轮已通过交付保存。
7. 另外通过“帮助 → 诊断信息… → 保存诊断包…”的原生 `save-panel` 写出诊断 JSON，成功提示给出实际保存路径；这不替代冻结交付保存验收。

初次选择 `验收样本.pdf` 时，它与副本内既有 PDF 内容相同，发生正常去重，任务数保持 2。随后改用内容不同的上述测试文档验证新任务创建；未把去重误计为新建成功。

## 实际失败：打开任务时辅助功能访问触发原生崩溃

观察范围限定为 **macOS 27.0.1 上，本候选与 computer use / 辅助功能访问共同运行时**。本次没有证据断言普通鼠标操作或其他 macOS 版本同样崩溃。

复现操作：

1. 启动候选的隔离会话：

   ```sh
   dist/qt-desktop/Folith.app/Contents/MacOS/Folith \
     --data-dir /Users/xueyang/Dev/FolioThread/tmp/qt-beta/computer-use-2026-10-05/data \
     --log-dir /Users/xueyang/Dev/FolioThread/tmp/qt-beta/computer-use-2026-10-05/logs
   ```

2. 用 computer use 绑定该候选应用。首页可读取，可看到 `selftest.docx` 为“已冻结交付 v1”、3 段。
3. 按 Tab 选择该任务，点击“打开任务”。
4. computer use 报 `App quit`；应用日志最后操作为 `open job 0ce17b89faa98948`，进程退出。

两次记录（Asia/Riyadh，UTC+03:00）：

| 时间 | PID | 退出码 | 系统异常 |
| --- | --- | --- | --- |
| 2026-10-05 05:58:58 | 27304 | 139 | `EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS at 0x11` |
| 2026-10-05 06:00:56 | 27374 | 138 | `EXC_BAD_ACCESS / SIGBUS` |

两份崩溃栈均包含候选随包的 `libqcocoa.dylib`、AppKit 的 `NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:` 和 HIServices 的 `__AXCopyAttributeValueForHierarchy`。第一份顶层帧在 `libqcocoa.dylib`；第二份先出现 `vtable for QPaintEngine`，随后进入同一辅助功能调用链。**这是定位线索，具体失效对象和修复方案尚未查明。**

收尾时关闭 WPS 验收文档后，computer use 的界面捕获连续报 ScreenCaptureKit `-3812`（参数无效），重置 REPL 后仍未恢复，影响 Folith 和 Finder 的读取。该工具连接问题另行记录，不能当作 Folith 崩溃。最终对已核实身份、处于首页且无运行任务的隔离验收进程发送 SIGTERM 完成清理；**这不算正常 UI 退出通过**。所有报告中的 12 个本地链接均已确认目标存在。

## 证据

本次证据目录：[tmp/qt-beta/computer-use-2026-10-05/evidence](../../tmp/qt-beta/computer-use-2026-10-05/evidence)。

- [第一份原生崩溃报告](../../tmp/qt-beta/computer-use-2026-10-05/evidence/01-open-task-crash.ips)。
- [第二份原生崩溃报告](../../tmp/qt-beta/computer-use-2026-10-05/evidence/01b-open-task-crash.ips)。
- [崩溃摘要](../../tmp/qt-beta/computer-use-2026-10-05/evidence/crash-summary.json)。
- [PDF 原生选择面板](../../tmp/qt-beta/computer-use-2026-10-05/evidence/02-pdf-native-panel.png)。
- [DOCX 原生选择面板](../../tmp/qt-beta/computer-use-2026-10-05/evidence/03-docx-native-panel.png)。
- [导入后 4 个任务](../../tmp/qt-beta/computer-use-2026-10-05/evidence/04-imported-four-tasks.png)。
- [取消后仍为 4 个任务](../../tmp/qt-beta/computer-use-2026-10-05/evidence/05-cancel-still-four-tasks.png)。
- [WPS 实际打开交付 DOCX](../../tmp/qt-beta/computer-use-2026-10-05/evidence/06-docx-default-app-wps.png)。
- [应用原生导出的诊断包](../../tmp/qt-beta/computer-use-2026-10-05/evidence/folith-diagnostics-computer-use.json)。
- [应用日志](../../tmp/qt-beta/computer-use-2026-10-05/logs/qt-client.log)。

## 回传

```text
候选 sha256：1257a3c2d5837d576e84c02a39cbbe6bd30218764fe9994113197e5a5e72d4f9（实测一致）
验收机器：macOS 27.0.1 / Apple M4 / 非干净机器
1 中文输入法：NOT_RUN；打开任务时辅助功能访问触发原生崩溃，尚未到达译文框。
2 系统面板：PARTIAL；PDF/DOCX 原生导入与取消 PASS；既有冻结 DOCX 在 WPS 打开 PASS；交付保存与覆盖确认未验证。
3 干净机器：NOT_RUN；没有符合条件的可访问 Mac。
4 真实服务：NOT_RUN；没有已配置的可用模型连接，未使用凭据或发起真实服务请求。
5 睡眠唤醒：NOT_RUN。
6 生命周期与交互：PARTIAL；首页、帮助菜单及诊断导出可用；工作台打开发生两次原生崩溃。其他生命周期项未完整验收。
其他问题：见本记录及两份 .ips 崩溃报告。未修改实现，未提升发布就绪结论。
```
