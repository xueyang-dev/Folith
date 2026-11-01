# #276 — Windows：CAT 工作台 `Ctrl+Alt+P` 不展开原页面板

Issue: [#276](https://github.com/xueyang-dev/Folith/issues/276)（P2，`v0.8.1`）

## 现象

Windows 11 上，在 CAT 工作台把焦点放在段落网格/编辑框里按 `Ctrl+Alt+P`，
PDF 原页面板不展开、也不收起，界面毫无反应；右键操作栏的「查看原位置」按钮
却立即正常。`Ctrl+Shift+H` 等其它快捷键正常，说明窗口事件分发本身没坏。

## 根因

Windows 的 AltGr 键盘布局把 `Ctrl+Alt+<字母>` 解释为「输入一个字符」。Qt 在把按键
交给快捷键系统之前，先向**焦点控件**发送 `QEvent.ShortcutOverride` 询问它是否
要自己处理；聚焦的文本框（`QPlainTextEdit` / `QLineEdit`，例如译文编辑框、原文
浮层、搜索框、页码框）会对这种可打印的 `Ctrl+Alt+字母` 接受该事件并插入字符，
于是 `QAction` 上的窗口级快捷键永远收不到这个按键。

- 按钮能工作：`view_source_button.clicked` 直接调用 `show_source_location`，不经过
  键盘分发。
- macOS 不受影响：`⌘⌥P` 里的 Command 会抑制文本输入，`ShortcutOverride` 不被文本框
  接受，快捷键照常触发。
- 同一问题也影响 `Ctrl+Alt+O`（文档结构，#187）——它与 `Ctrl+Alt+P` 是同一种绑定。

`QKeySequence`（绑定）与 `platform_ui.shortcut_text`（提示）本来就同源，所以帮助
对话框里显示的 `Ctrl+Alt+P` 是对的，问题只在按键被文本框截走。

## 修复

`folith/qt/platform_ui.py` 新增 `install_shortcut_guard(widget, actions)`：在给定
控件上装一个事件过滤器，遇到与这些已绑定 `QAction` 相同的按键时，

1. 对 `QEvent.ShortcutOverride` 调用 `event.ignore()`，文本框不再把它当输入，
   Qt 先尝试快捷键分发；
2. 若 Windows 把 AltGr（右 Alt）上报成 `GroupSwitchModifier`，Qt 的
   `QKeySequence` 会把 Alt 丢掉（`Ctrl+Alt+P` 变成 `Ctrl+P`）、快捷键表匹配不到，
   于是守卫在随后的 `KeyPress` 上直接 `action.trigger()`，既触发动作也不插入
   AltGr 字符；
3. 其它按键原样交还控件。

传入的是工作台自己的 `QAction` 对象（`source_shortcut` / `outline_shortcut`），
绑定与提示仍来自 `platform_ui.shortcut(...)` 的同一条定义，没有在共享 UI 里
硬编码 Windows 行为。

`WorkbenchView` 在建立这两个 `QAction` 后，把守卫装到工作台里会拿到焦点的文本
控件：译文编辑框、原文浮层、段落网格、搜索框、提问框、PDF 页码框
（`views.py` 的 `_install_source_shortcut_guards`）。

## 测试

`tests/qt_shortcut_windows_test.py`（7 项，offline / offscreen）：

1. 用一个会按 AltGr 方式接受 `ShortcutOverride` 的文本框复现「快捷键被吞」；
2. 装上守卫后同一个按键触发 `QAction` 且不插入字符；
3. AltGr 被上报成 `GroupSwitchModifier` 时动作同样触发；
4. 守卫只认自己的动作，`Ctrl+Alt+Q`、`Ctrl+Alt+Shift+P` 不拦；
5. 空动作集合时是 no-op；
6. 工作台的六个文本控件都装了守卫；
7. 在网格上按 `Ctrl+Alt+P` 能真正开关原页左栏。

运行：

```
QT_QPA_PLATFORM=offscreen python -m pytest tests/qt_shortcut_windows_test.py -q
```

相关回归：`qt_platform_ui_test.py`、`qt_pdf_panel_test.py`、
`qt_source_panel_test.py` 及工作台动效/投影/内联编辑/术语高亮/大纲面板测试。

## 验收边界（未完成项）

- 上述测试用 offscreen Qt 模拟了 Windows 的 AltGr 抢占（真实的 Windows 平台插件
  行为无法在 macOS 上跑），**不能代替 Windows 真机验收**。
- 仍需在 Windows 11 原生客户端上人工确认：焦点在段落网格、译文编辑框、原文浮层、
  搜索框、PDF 页码框时，`Ctrl+Alt+P` 都能开关原页面板，`Ctrl+Alt+O` 都能开关
  文档结构；并且在这些控件里输入 `Ctrl+Alt+<其它字母>` 仍能正常产生 AltGr 字符。
