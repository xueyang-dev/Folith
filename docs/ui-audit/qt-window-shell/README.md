# Qt 窗口外壳（UI/UX 第 7 轮）

日期：2026-10-06 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮做 A2：窗口外壳——菜单栏、状态栏与通知落点。菜单结构本身（macOS 菜单角色、
⌘O/⌘W/⌘R/⌘⇧R/⌘⇧H/⌘S、关于/设置/退出的角色）在前几轮已经是对的，所以这一轮修的是
**状态与可读性**。

## 差距（逐条有截图或探针输出）

| 现象 | 为什么算问题 | 证据 |
| -- | -- | -- |
| **菜单动作从不置灰** | 在任务列表页点「开始 / 继续翻译」「暂停」「取消任务」「重新载入」「保存并提交当前段落」「冻结并保存交付…」不会有任何反应——菜单看起来坏了一半。探针输出：改造前所有动作 `enabled=True` | [before-shell-menu-home-light.png](before-shell-menu-home-light.png) → [after-shell-menu-home-light.png](after-shell-menu-home-light.png) |
| **即使置灰，看起来仍是黑的** | 全局基础 QSS 给 `QWidget` 设了文字色，盖掉了菜单的禁用调色板。修完置灰后，同一份截图**字节完全相同**（sha256 一致）——置灰对用户不可见 | 两个场景改造后的菜单截图起初 sha 相同，加了 `QMenu::item:disabled` 才分开 |
| 状态栏把整条数据目录横着铺满 | 长路径（macOS 的 `Application Support/…`）会顶满状态栏，把右侧的模型状态挤到边缘 | [before-shell-home-light.png](before-shell-home-light.png) → [after-shell-home-light.png](after-shell-home-light.png) |
| 模型未配置与已配置是同一撮灰字 | 连接没配好是**需要用户动手**的状态，应该能一眼看出来 | 同上（改造后未配置为红色） |
| 数据目录路径无法选中复制 | 排错时经常要把路径整条复制给支持人员 | 改造后 `TextSelectableByMouse` + 悬停显示完整路径 |

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/window.py` | 新增 `_sync_menu_state()`：任务菜单的动作按"当前页 + 工作台按钮的真实状态"启用/禁用（不新写一套判断，直接复用 `start_button`/`pause_button`/`cancel_button`/`submit_button`/`deliver_button` 的可见性与可用性）；在 `show_home()`、`open_job()`、`refresh_workbench()` 三处同步。状态栏：数据目录中间省略 + 悬停完整值 + 可选中复制；模型状态用 `statusError`/`hint` 语义色并在切换时重新 polish |
| `folith/qt/style.py` | 新增 `QMenu::item:disabled { color: disabled-ink }`——否则置灰在视觉上不存在 |
| `tests/qt_client_window_test.py` | 新增 `test_task_menu_actions_follow_the_current_page`：首页上六个任务动作必须禁用；打开任务后「任务列表 / 重新载入 / 保存并提交」必须可用 |

## 状态覆盖

| 情形 | 证据 |
| -- | -- |
| 「任务」菜单 · 任务列表页（全部置灰） | [before-shell-menu-home-light.png](before-shell-menu-home-light.png) · [after-shell-menu-home-light.png](after-shell-menu-home-light.png) |
| 「任务」菜单 · 任务页（回到列表/重新载入/提交可用，其余置灰） | [after-shell-menu-workbench-light.png](after-shell-menu-workbench-light.png) |
| 状态栏 · 首页（长路径省略、模型红色） | [before-shell-home-light.png](before-shell-home-light.png) · [after-shell-home-light.png](after-shell-home-light.png) · [after-shell-home-dark.png](after-shell-home-dark.png) |
| 状态栏 · 任务页（临时消息「已完成」在左，永久信息在右） | [after-shell-workbench-light.png](after-shell-workbench-light.png) · [after-shell-workbench-dark.png](after-shell-workbench-dark.png) |

```text
venv/bin/python tmp/qt-beta/shots-shell.py home    "<数据目录>"
venv/bin/python tmp/qt-beta/shots-shell.py workbench "<数据目录>"
venv/bin/python tmp/qt-beta/shots-shell.py menu-home
venv/bin/python tmp/qt-beta/shots-shell.py menu-workbench
```

两张状态栏前后对比用的是**同一个真实目录**（`/tmp/folith-shell-长路径/Application Support/Folith/数据目录/home-data`，
fixture 的副本），所以省略与否的差别不是路径不同造成的；「整改前」由改动前的代码
在快照树 `/tmp/qt-verify` 里渲染，不是重画示意。

## 验证

```text
qt_client_window_test -k "menu"          3 passed
  ├─ test_task_menu_actions_follow_the_current_page   （本轮新增）
  ├─ test_macos_menu_roles_and_standard_shortcuts     （既有）
  └─ test_menu_shortcuts_actually_fire_and_are_not_ambiguous（既有）
```

既有用例这次真的抓到了我的错误：第一版我把「任务列表」的启用条件写反（在任务页被禁用），
`⌘⇧H 必须回到任务列表` 立刻失败。修好后三条全绿——这也说明这轮加的置灰没有把
快捷键语义改坏。

## 未验证 / 边界

- 未在真实 macOS 原生菜单栏下核对（截图来自窗口内的菜单栏渲染；菜单角色的归属由既有用例覆盖）；
- 菜单项没有图标——macOS 的人机指南本来就不在菜单里放图标，这里有意保持一致；
- 通知（`_notice`）的落点仍是"状态栏 8 秒 + 工作台状态标签"，本轮没有改成浮层/横幅；
  错误类通知的颜色区分留待与 A2 的通知条一起做；
- 未验证 200% 缩放下菜单与状态栏的重排（B6 范围）。
