# Qt 应用内对话框收敛（UI/UX 第 6 轮）

日期：2026-10-05/06 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮做 A3：把"应用自己的提示与确认"从系统对话框换成产品自己的对话框。
全仓库清点结果是 **16 处真实调用点**（`window.py` ×8、`dialogs.py` ×6、`app.py` ×1，
另外 `views.py`/`workers.py`/`bootstrap.py`/`selftest.py`/`qt_client.py` 一处都没有）。
清点由子代理逐行读取产出，含每处的标题/按钮原文、自动化守卫与测试旁路。

## 差距（逐条有截图）

| 现象 | 为什么算问题 | 证据 |
| -- | -- | -- |
| 确认框是系统 `QMessageBox`：一个大蓝问号 + 标准按钮 | 与三套主题割裂；"永久删除"在系统按钮里和"取消"长得一样，没有危险语义 | [before-confirm-danger-light.png](before-confirm-danger-light.png) → [after-confirm-danger-light.png](after-confirm-danger-light.png) |
| 标题只在窗口栏，正文里没有标题 | 截图/录屏里看不出这是哪个动作；对话框被窗口栏遮住时更明显 | 同上 |
| 详情（异常堆栈、诊断 JSON）只能靠系统"显示详情" | 展开方式、字体、配色都不受控 | [before-notice-detail-light.png](before-notice-detail-light.png) → [after-notice-detail-light.png](after-notice-detail-light.png) |
| 名称输入是 `QInputDialog` | 与设置页"标签在上"的表单排版不一致 | [before-text-input-light.png](before-text-input-light.png) → [after-text-input-light.png](after-text-input-light.png) |
| 等待框是 `QProgressDialog` | 系统进度条在深色主题下是一块系统灰；且"在后台运行"按钮走系统按钮样式 | [before-busy-light.png](before-busy-light.png) → [after-busy-light.png](after-busy-light.png) |
| **两处自动化缺口**：`create_project`（`QInputDialog`）与 `show_tools` | 没有任何守卫：脚本化运行会弹出没人点的模态框而挂住——这是"机器可复现"的缺陷，不是审美 | 代码事实 + 新增用例 |

文件/文件夹选择面板（`QFileDialog` ×3）**按设计保留系统原生**：macOS 用户对它有稳定期待，
自绘文件浏览器只会更差。本轮换掉的是应用自己的提示、确认、输入与等待。

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/prompts.py`（新增） | 四个对话框：`ConfirmDialog`（主按钮按语义取实心 accent / 实心危险色）、`NoticeDialog`（单按钮 + 详情默认收起可展开）、`TextInputDialog`（标签在上、回车即确认）、`BusyDialog`（模态、不确定进度、唯一出路「在后台运行」＝只隐藏不取消）。统一挂在同一张 `Card` 上，颜色只走 token |
| `folith/qt/window.py` | `_failure` / `_critical` / `_confirm` / `_busy` 全部改走 `prompts`（对外签名与自动化行为不变）；`show_about` / `show_tools` 改用 `NoticeDialog`；`create_project` 改用 `TextInputDialog` **并补上自动化守卫**；`show_tools` 同样补守卫 |
| `folith/qt/dialogs.py` | `EngineDialog` 的"配置不完整"确认与"保存失败"提示、`DiagnosticsDialog` 的导出结果提示、`ProjectDialog._ask_name` 全部改走 `prompts`；不再引用 `QMessageBox` / `QInputDialog` |
| `folith/qt/app.py` | 重复启动的提示框改用 `NoticeDialog` |
| `folith/qt/style.py` | 新增 `QPushButton#dangerPrimary`（实心危险色主按钮）与 `QTextBrowser#dialogDetail`（等宽只读详情面板），并在 `EmptyState` 上下加弹性空间 |
| `tests/qt_prompts_test.py`（新增） | 6 条用例：确认框返回真实选择、危险语义用实心危险按钮、详情默认收起可展开、文本输入返回值、等待框模态且只有一条出路、四个框都落在 `Card` 上 |
| `tests/qt_client_window_test.py` | `test_dialogs_never_block_a_scripted_run` 增加两处原先漏网的守卫；新增 `test_main_window_confirm_uses_the_apps_own_dialog` 断言 `_confirm` 走 `prompts.ConfirmDialog.ask`（含标题与按钮文字透传） |

## 状态覆盖

| 情形 | 证据 |
| -- | -- |
| 危险确认（删除任务） | [after-confirm-danger-light.png](after-confirm-danger-light.png) · [after-confirm-danger-dark.png](after-confirm-danger-dark.png) |
| 失败通知 + 展开详情 | [after-notice-detail-light.png](after-notice-detail-light.png) · [after-notice-detail-dark.png](after-notice-detail-dark.png) |
| 名称输入 | [after-text-input-light.png](after-text-input-light.png) · [after-text-input-dark.png](after-text-input-dark.png) |
| 等待（唯一出路） | [after-busy-light.png](after-busy-light.png) · [after-busy-dark.png](after-busy-dark.png) |
| 整改前对照（同一文案、同一主题样式） | [before-confirm-danger-light.png](before-confirm-danger-light.png) · [before-notice-detail-light.png](before-notice-detail-light.png) · [before-text-input-light.png](before-text-input-light.png) · [before-busy-light.png](before-busy-light.png) |

```text
venv/bin/python tmp/qt-beta/shots-prompts.py after    # prompts.*
venv/bin/python tmp/qt-beta/shots-prompts.py before   # 旧 QMessageBox / QInputDialog / QProgressDialog
```

「整改前」不是示意图：脚本直接构造旧的系统控件（同一文案、同一套主题样式），
就是改造前用户看到的那一屏。

## 验证

```text
qt_prompts_test               6 passed
qt_settings_dialog_ui_test    8 passed
qt_client_window_test         7 passed（-k "dialog or busy or about or tools or confirm or modal or exit_closes"）
```

本 worktree 的 `core.py` 比主仓库工作区旧，管线类用例（6 条）在本目录必然失败，
与界面无关；界面与对话框用例都在本目录跑通。

用**当前 core** 组装的验证树 `/tmp/qt-verify`（A1+A3 代码，7 个 Qt 测试文件同进程跑）：

```text
170 passed / 1 failed（482s）
```

唯一失败是 `test_brand_lockup_uses_the_documented_night_variant`——它按 `sys.executable`
的位置找品牌资源，在临时验证树里必然找不到；同一棵树把 `folith/qt/` 换回主仓库版本后
**同样失败**（前几轮已记录）。其中 `qt_client_window_test.py` 68 条、`qt_client_adapter_test.py`
57 条全绿，包含本轮新增/扩展的两条对话框用例。

## 未验证 / 边界

- 未在真实模态栈上核对 Escape/回车/焦点顺序（用例只覆盖按钮点击与默认按钮）；
- 系统文件面板仍是 `QFileDialog`（有意保留），因此"导入文档"的观感依旧是系统面板；
- 原生 macOS 外观（浅色/深色系统切换）下 `QDialog` 的边框与阴影未单独核对；
- 对话框在 200% 缩放与超长消息下的重排未验收（B6 范围）。
