# Qt 桌面端界面语言（i18n 第 8 轮，B4）

日期：2026-10-06 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

状态：**完成**。405 条界面文案在五种语言下全部译出（`--strict` 门禁退出码 0），
语言设置、RTL、覆盖率工具链齐备；唯一保留的边界是**核心生成的异常/细节文本**
（开放集合，见文末"边界"），它不影响界面自身的本地化。

这一轮的目标是把"桌面端只有中文"变成"桌面端可以讲五种语言"，并把覆盖率的真值做成
可复跑的检查，而不是靠人肉清点。

## 方案

沿用 Web 端那份目录约定：**中文原文就是键**（`{"理论框架": "Theoretical framework"}`）。
Qt 端特有的措辞放在 `folith/resources/locales/qt-<code>.json`，查找顺序：

1. Qt 目录 `qt-<code>.json`（桌面端自己的文案）；
2. Web 目录 `<code>.json`（两端一字不差的直接复用）；
3. 中文原文（**永不失败**，最坏情况是露出中文，而不是空白或半截）。

- 语言在**启动时**确定：`FOLITH_UI_LANGUAGE` 环境变量优先（脚本化验收与多语言截图要用），
  其次是设置里保存的 `appearance/language`。切换需要重建窗口，所以设置页明确写"重启生效"。
- 阿拉伯语等 RTL 语种会整体镜像：`QApplication.setLayoutDirection(RightToLeft)`。
- 没有引 `.ts`/`lupdate`：仓库已有 1500 条文案的 JSON 目录，另起一套只会分叉。

## 工具链（可复跑，不靠人肉）

| 工具 | 作用 |
| -- | -- |
| `scripts/qt_i18n_instrument.py --check/--write` | 用 AST 定位"UI 出口"（控件构造、`setText`/`setWindowTitle`/`addTab`/`addItem`、应用自己的 `_notice`/`_failure`/`_confirm`/`_busy`/`_field`/`_panel`/`nav_button`、prompts 的对话框…），把中文字面量与 f-string 改写成 `i18n.tr(...)`。**按行列字节区间精确替换**，注释与日志不受影响；已是 `tr()` 的不会二次包装（可重复运行） |
| `scripts/qt_i18n_instrument.py --catalogs` | 生成/刷新 5 份目录：`qt-zh-CN.json` 为原文映射，其余语言保留已有译文、自动复用 Web 目录里同键的文案，并报告缺口 |
| `scripts/qt_i18n_coverage.py [--strict] [--leftovers]` | 覆盖率报告（缺译 / 与原文相同 / 语言自称豁免），`--strict` 可当门禁；`--leftovers` 列出**还没走 `tr()`** 的中文字面量 |

改写过程的两个坑都留下了教训：`col_offset` 是 **UTF-8 字节**偏移（第一版按字符切，
把 `app.py` 切坏了）；`ast.unparse` 会丢注释，所以最终按区间替换而不是重排语法树。

## 覆盖结果

```text
Qt 界面文案：7 个文件
OK ar      405/405  缺 0  与原文相同 0
OK en      405/405  缺 0  与原文相同 0
OK es      405/405  缺 0  与原文相同 0
OK ru      405/405  缺 0  与原文相同 0
OK zh-CN   405/405  缺 0  与原文相同 0
```

405 条包含三类：源码里的静态文案、`{v0}` 模板、以及**核心产出的运行时词汇**
（任务状态、段落状态、目标语言，含 `已冻结交付 v{v0}` 这类模板）。后者用
`i18n.tr_dynamic()` 在显示处翻译：先精确匹配，再用模板正则匹配整串。

四份非中文目录由 4 个并行子代理各自独立完成（`qt-en/es/ru/ar.json`，各 340 条），
每个代理都用同一套校验：键集与中文目录逐字节一致、占位符与换行数一致、值里没有残留中文。
之后由我补了 3 批增量（21 + 43 + 10 条）并把术语统一。

## 状态覆盖（截图）

| 情形 | 证据 |
| -- | -- |
| 首页 · 中文（基准） | [home-zh-CN-light.png](home-zh-CN-light.png) · [home-zh-CN-dark.png](home-zh-CN-dark.png) |
| 首页 · English | [home-en-light.png](home-en-light.png) |
| 首页 · Español | [home-es-light.png](home-es-light.png) |
| 首页 · Русский | [home-ru-light.png](home-ru-light.png) |
| 首页 · العربية（**整体 RTL 镜像**） | [home-ar-light.png](home-ar-light.png) |
| 确认框（四语 + 中文） | [confirm-en-light.png](confirm-en-light.png) · [confirm-ar-light.png](confirm-ar-light.png) · [confirm-ru-dark.png](confirm-ru-dark.png) |
| 工作台（阿拉伯语，RTL：工具栏/分段筛选/状态芯片） | [workbench-ar.png](workbench-ar.png) |
| 交付页（阿拉伯语，RTL） | [delivery-ar.png](delivery-ar.png) |
| 工作台（俄语 / 中文） | [workbench-ru.png](workbench-ru.png) · [workbench-zh-CN.png](workbench-zh-CN.png) |
| 设置 · 外观（含"界面语言"一行） | [settings-en.png](settings-en.png) · [settings-ar.png](settings-ar.png) |
| 整改前（没有语言设置的外观页） | [before-settings-appearance-light.png](before-settings-appearance-light.png) |

```text
venv/bin/python tmp/qt-beta/shots-i18n.py zh-CN
venv/bin/python tmp/qt-beta/shots-i18n.py en
venv/bin/python tmp/qt-beta/shots-i18n.py ar
```

长语言的排版问题也顺手修了：按钮不会自己省略文字，英文/俄文比中文长 1.5–2 倍，
248px 的侧栏会把「Импортировать документ…」裁成半句。现在 `style_module.fit_button()`
按**主题正文字号**量宽度（控件未 polish 时 `fontMetrics()` 偏窄，第一版因此没生效），
超出就省略显示并把完整文字放进悬停提示。

## 验证

用**当前 core** 的验证树 `/tmp/qt-verify`，逐文件独立进程：

```text
qt_client_window_test        71 passed
qt_i18n_test                  8 passed
qt_prompts_test               6 passed
qt_settings_dialog_ui_test    8 passed
qt_home_cards_test           10 passed
qt_delivery_ui_test          13 passed
qt_delivery_content_test      3 passed
qt_client_style_test          1 failed / 7 passed   ← 品牌资源按 sys.executable 找路径（验证树自身问题）
scripts/qt_i18n_coverage.py --strict  → 退出码 0
```

本 worktree 内同批用例：`qt_i18n_test` 8、`qt_prompts_test` 6、`qt_settings_dialog_ui_test` 8、
`qt_home_cards_test` 10、`qt_delivery_ui_test` 13、`qt_client_style_test` 8，全绿。

两个**由本轮暴露、并已修掉**的真实缺陷：

1. `test_settings_dialog_keeps_untouched_preferences`（本轮新增）抓到：`open_settings`
   从来没有把当前主题/语言偏好带进对话框，用户"进去看一眼再保存"就会把主题静默重置成
   「跟随系统」。现在两条偏好都随对话框往返。
2. 界面语言是**用户设置**：新增的语言用例把 `appearance/language=en` 写进了真实的
   `QSettings("Folith","QtDesktop")`，同进程后续 15 条中文断言全挂（菜单变成 File/Tasks/Help）。
   根因不是"用例写错"，而是 `client` fixture 没有隔离用户设置——现在 fixture 会把语言键
   临时摘掉、跑完还原，并把 `i18n` 固定成基准语言。

## 第二轮：把缺口关掉（本轮）

1. **拼接句 28 处 → 全部改成"整句模板"**：`"共 " + n + " 个任务"` 这类用 `+` 拼的句子
   现在是一整条 `i18n.tr("共 {v0} 个任务", …)`；含条件分支的句子也改成"两个完整子句
   各自成句再拼接"，避免分片拼接在多语言里语序错乱。剩余的 `+` 只是把**已经翻译好的
   子句**接起来，不再有裸中文字面量。
2. **核心/适配层产出的有限词汇也进目录**：`EngineConfig.connection_problem()` 的三条
   固定措辞、核心给出的进度单位（批次）、运行状态标题（正在运行/处理中断…）等，
   都加进目录并在**显示处**过 `i18n.tr_dynamic()`；不再只在提示框里露中文。
3. **补齐工具漏掉的 UI 出口**：`style.CheckBox`/`RadioButton`（自绘控件）、
   `SegmentedControl` 的标签元组、以及"先赋值给变量再 setText"的芯片文案。
   阿拉伯语工作台现在工具栏、分段筛选与四个状态芯片都是阿拉伯文。

## 未验证 / 边界

- **未做独立语言审阅**：四份目录由 AI 生成并按术语表统一，但没有母语者逐条复核；
  设置页与 CHANGELOG 都标注"实验功能"。
- **核心生成的异常/细节文本仍是中文**：`adapter`/`core` 抛出的错误消息、`_failure` 的
  `detail`（异常堆栈）等是开放集合；本轮不修改核心，所以没有为它们建目录。固定措辞
  （如模型连接问题）已单独覆盖。
- `--leftovers` 仍会报出 200 多处中文字面量，其中绝大多数是**非界面文案**
  （日志、异常、QSS 注释、docstring），不能一律包 `tr()`；需要人分辨。
- RTL 已看首页/工作台/交付页/确认框/设置；项目对话框与诊断对话框未在 RTL 下逐页核对。
- 长文案在 200% 缩放与最小窗口下的换行未验收（B6 范围）。
- 语言切换仍是"重启生效"，没有做运行时重建界面。
