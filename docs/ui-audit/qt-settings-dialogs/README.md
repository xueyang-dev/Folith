# Qt 设置 / 诊断对话框（UI/UX 第 3 轮）

日期：2026-10-05 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮做上一轮排定的优先级 ②：设置（`EngineDialog`）与诊断（`DiagnosticsDialog`）
对话框。参照物是仓库里的旧 Web 界面设置页
（`docs/ui-audit/02-settings/020-settings-default-same-reviewer.png`）与旧界面预览
（`docs/assets/02-new-task-preview.png`）。没有改核心业务判断、没有改配置字段集合、
没有改保存/校验路径。

## 差距（整改前）

| 现象 | 为什么算问题 |
| -- | -- |
| 表单标签右对齐成一条窄列，值列起点随标签长度跳动 | 旧界面是"标签在上、控件在下"；右对齐标签在中文里既不好读也不好扫 |
| **复选框选中只是一个蓝方块，没有对勾** | 对话框用的是裸 `QCheckBox`，而仓库已有自绘对勾的 `style.CheckBox`（QSS 画不出勾） |
| 复选框文字区被刷上一道灰条 | 全局基础规则给每个 `QWidget` 都刷页面底色，容器型控件因此露底 |
| 「审校模型」四个字段常驻，未启用时只是变灰 | 灰输入框既不能用又占半张卡片，用户还要先猜为什么不能填 |
| API Key 只能盲打 | 旧界面输入框右侧有眼睛按钮 |
| 外观页三张色卡长得像按钮，点了没反应 | 它是预览不是控件；"像按钮却不是按钮"是最典型的假交互 |
| OCR 识别引擎显示 `online` / `tesseract` / `paddle` | 同一张表单其它项都是中文，只有它把核心取值暴露给用户 |
| 连通性测试结果与普通说明同为灰色 | 成功/失败没有颜色区分 |
| 设置页最长一栏把对话框顶到 914px 高 | 在 900px 高的屏幕上保存/取消会被挤出屏幕，且没有滚动 |
| 诊断概览的标签右对齐、JSON 标题是普通正文 | 事实表读起来像散落的文字 |

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/style.py` | 基础规则**不再给每个 QWidget 刷背景**（只设文字与字号），背景交给顶层窗口与显式 `objectName` 的面；新增 `field_label`（标签在上）、`SecretField`（输入框 + 显示/隐藏眼睛按钮，含焦点态外框）、`RadioButton`（自绘内层圆点）、`themeCard` 样式；修复 `QCheckBox`/`QRadioButton` 的透明与 16px indicator |
| `folith/qt/dialogs.py` | `_section`（右对齐 QFormLayout）→ `_panel` + `_field`（标签在上）；每个标签页套滚动区；服务商/模型并排；API Key 用 `SecretField`；审校方式改单选（同一模型 / 单独配置），未启用时整块收起；外观色卡可点选并与下拉框同步；OCR 引擎显示中文标签；连通性结果带成功/失败颜色；按钮补图标；诊断概览标签左对齐、小节标题用 `SectionLabel` |
| `tests/qt_settings_dialog_ui_test.py` | 新增 8 条用例：标签在上、所有复选框都是自绘对勾控件、审校字段按模式显隐且 `_collect()` 语义不变、色卡可点选且颜色仍来自 token、OCR 中文标签但取值仍是核心值、密钥默认隐藏且可切换、诊断标签左对齐与路径可复制、诊断按钮有图标 |
| `tests/qt_client_window_test.py` | 未改断言（现有"保存不得重置未展示字段""创建项目/设置对话框不阻塞脚本运行""退出时关闭模态设置框"等用例原样通过） |

## 状态覆盖（截图）

| 情形 | 证据 |
| -- | -- |
| 模型连接（日间/夜间/纸面） | [after-settings-connection-light.png](after-settings-connection-light.png) · [after-settings-connection-dark.png](after-settings-connection-dark.png) · [after-settings-connection-paper.png](after-settings-connection-paper.png) |
| 独立审校模型（选"单独配置"才展开） | [after-settings-connection-separate-light.png](after-settings-connection-separate-light.png) · [after-settings-connection-separate-dark.png](after-settings-connection-separate-dark.png) |
| 任务与交付（复选框有对勾、两列网格） | [after-settings-task-light.png](after-settings-task-light.png) · [after-settings-task-dark.png](after-settings-task-dark.png) |
| 扫描件 OCR（引擎显示中文标签） | [after-settings-ocr-light.png](after-settings-ocr-light.png) |
| 外观（三张色卡可点选，选中描边） | [after-settings-appearance-light.png](after-settings-appearance-light.png) · [after-settings-appearance-dark.png](after-settings-appearance-dark.png) |
| 诊断信息（标签左对齐、按钮带图标） | [after-diagnostics-light.png](after-diagnostics-light.png) · [after-diagnostics-dark.png](after-diagnostics-dark.png) |
| 整改前对照 | [before-settings-connection-light.png](before-settings-connection-light.png) · [before-settings-appearance-light.png](before-settings-appearance-light.png) · [before-diagnostics-light.png](before-diagnostics-light.png) |

截图由 `tmp/qt-beta/shots-dialogs.py` 生成（真实窗口控件渲染）：

```text
venv/bin/python tmp/qt-beta/shots-dialogs.py settings      # 四个标签页 × 三主题 + 独立审校
venv/bin/python tmp/qt-beta/shots-dialogs.py diagnostics   # 诊断 × 三主题
```

## 全局基础 QSS 的连带修复

把"每个 QWidget 都刷页面底色"改成"只有顶层窗口与显式面刷底色"会影响所有页面，
所以本轮逐页复核了首页、工作台（翻译 / 交付）三套主要界面（浅色 + 夜间）：

```text
venv/bin/python tmp/qt-beta/shots-pages.py   # page-home-* / page-translation-* / page-delivery-*
```

结论：页面底色、侧栏、卡片、表格行、页头与检查器外观不变；只有原先"容器控件被刷上
灰条"的位置（复选框文字区、表单字段外壳）变成应有的透明。交付页清单与首页任务表在
本轮改动后重新截图核对，未发现回归。

## 验证

1. 本 worktree（UI 分支自身用例）：

```text
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest \
    tests/qt_settings_dialog_ui_test.py tests/qt_delivery_ui_test.py tests/qt_client_style_test.py -q
8 + 13 + 8 passed
```

2. 用**当前 core** 组装的验证树 `/tmp/qt-verify`（主仓库 `core.py` + `folith/` + `tests/`，
   覆盖本分支的 `folith/qt/*` 与两个测试文件）：

```text
tests/qt_client_style_test.py tests/qt_client_adapter_test.py \
tests/qt_client_window_test.py tests/qt_delivery_ui_test.py
145 passed / 1 failed（468s）
```

唯一失败是 `test_brand_lockup_uses_the_documented_night_variant`：它按
`sys.executable` 的位置找品牌资源，在临时验证树里必然找不到；同一棵树把
`folith/qt/` 换回主仓库版本后**同样失败**，属验证树自身的路径问题。

3. 本轮顺带修掉两个只在旧 core 上暴露的崩溃点（`folith/qt/adapter.py`）：
   `ocr_language_choices()` 在核心没有 OCR 语言注册表时返回"自动判断"而不是抛
   `AttributeError`；`normalize_ocr_mode()` 在核心没有归一函数时仍认得
   `always/never` 这些历史值。这是让客户端在"核心少一个导出"时不至于连设置页都打不开，
   不改变新 core 上的行为。

## 未验证 / 边界

- 未发起真实模型调用：连通性测试、模型目录刷新只走了"按钮/状态/样式"的代码路径，
  真实网络结果与超时体验由验收环境负责；
- 保存流程未跑真实写入（现有回归覆盖"未展示字段不得被重置"与"配置不完整仍要保存"，
  本轮未新增写入路径）；
- 未在 Windows / Linux 上核对；未做 200% 缩放下设置对话框的重排验收；
- 键盘 Tab 顺序未单独验证（各字段仍是标准控件，顺序按创建顺序，与整改前一致）。
