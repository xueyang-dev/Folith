# Folith 外观与多语言支持

## 当前状态（v0.8.0 / 2026-10）

外观与语言页面已接入侧栏及 Qt 桌面端偏好设置。日间、夜间、暖黄色纸面三种显示模式可切换。设置保存在本机（Qt 桌面端通过 `QSettings` 持久化，Web 端通过 `outputs/ui_preferences.json`），重启后恢复。

已有简体中文默认界面，支持 **English**、**Español**、**Русский**、**العربية**、**Français** 和 **Deutsch** 六门界面语言。

### 界面语言与目录范围

| 语言代码 | 语言名称 | 状态 | Web 目录条数 | Qt 桌面目录条数 | 备注 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `zh-CN` | 简体中文 | 默认/基准 | 1,674 | 2,190 | 基准语言，唯一源目录 |
| `en` | English | 实验功能 (experimental) | 1,674 | 2,193 | 见 [#17](https://github.com/xueyang-dev/Folith/issues/17) 语言审校验收 |
| `es` | Español | 实验功能 (experimental) | 1,674 | 2,194 | 见 [#17](https://github.com/xueyang-dev/Folith/issues/17) 语言审校验收 |
| `ru` | Русский | 实验功能 (experimental) | 1,674 | 2,191 | 见 [#17](https://github.com/xueyang-dev/Folith/issues/17) 语言审校验收 |
| `ar` | العربية | 实验功能 (experimental) | 1,674 | 2,191 | RTL 布局支持，见 [#16](https://github.com/xueyang-dev/Folith/issues/16) RTL 验收 |
| `fr` | Français | 实验功能 (experimental) | 1,674 | 2,194 | 包含变音符与标点空格规范，新增于 [#11](https://github.com/xueyang-dev/Folith/issues/11) |
| `de` | Deutsch | 实验功能 (experimental) | 1,674 | 2,194 | 复合词长文本压力测试，新增于 [#11](https://github.com/xueyang-dev/Folith/issues/11) |

> [!NOTE]
> 界面目录完整（100% 覆盖）不等同于母语审阅完成。除简体中文外的所有界面语言当前均明确标记为 **experimental（实验功能）**。
> 本次新增德语与法语目录已通过语法、占位符、标点及 Qt 长语言布局用例检验，但仍需社区及母语审校者进一步校对与反馈。
> 与其他 Issue 的明确分界：
> - 翻译源/目标语言拓展属于 [#10](https://github.com/xueyang-dev/Folith/issues/10)；
> - 阿拉伯语 RTL 验收属于 [#16](https://github.com/xueyang-dev/Folith/issues/16)；
> - 英语、西班牙语、俄语母语审阅属于 [#17](https://github.com/xueyang-dev/Folith/issues/17)；
> - 法语、德语及新增界面语言框架属于 [#11](https://github.com/xueyang-dev/Folith/issues/11)。

---

## 架构与边界

- **Qt 客户端国际化**（`folith/qt/i18n.py`）：
  - 启动时读取持久化偏好或 `FOLITH_UI_LANGUAGE` 环境变量。
  - 单句查询 `i18n.tr("源文本", **kwargs)`，支持 `{v0}`、`{name}` 等具名格式化占位符。
  - 单复数处理 `i18n.tr_n("源文本", count, **kwargs)`，对于支持单数的语言读取 `<源文本>#one` 键，未配置时回退基准翻译。
  - 阿拉伯语 RTL 判定 `i18n.is_rtl()`，驱动 Qt 窗口及控件方向镜像。
  - 语言列表 `i18n.available_languages()` 与标准化 `i18n.normalize(code)`。
- **Web 端国际化**（`folith/ui/i18n.py`、`folith/ui/preferences.py`）：
  - 独立本机设置读写与设置页。显示偏好不会写入任务、模型配置或译文。
  - 动态格式化 `localized_formatter()`，静态 HTML 片段 `html_literal()`。
- **目录资源**（`folith/resources/locales/`）：
  - Web 界面目录：`zh-CN.json`（源目录）、`en.json`、`es.json`、`ru.json`、`ar.json`、`fr.json`、`de.json`。
  - Qt 界面目录：`qt-zh-CN.json`（源目录）、`qt-en.json`、`qt-es.json`、`qt-ru.json`、`qt-ar.json`、`qt-fr.json`、`qt-de.json`。
  - 目录随 wheel 和安装包统一打包。

### 目录与界面范围约束

范围仅包括显式 UI 操作文案、按钮、菜单、对话框、提示、静态 HTML 片段、导航、设置以及工作台界面。

**明确排除在界面目录之外的内容：**
- 用户文档原文与译文、段落内容、项目名称、任务标题与文件路径；
- 用户的专有术语库、翻译记忆（TMX）、风格规则及人工修改记录；
- 模型与后端返回的动态堆栈、底层调试日志、原始异常文本；
- 导出文件（DOCX / EPUB / PDF）的正文内容。

---

## 贡献与校对指南（Contribution & Proofreading Guide）

欢迎社区贡献者对已有语言进行校对、提出润色建议，或贡献新的界面语言。

### 1. 目录文件结构

- 目标语言文件必须放置在 `folith/resources/locales/`：
  - Web 目录：`<locale>.json`（例如 `fr.json`、`de.json`）
  - Qt 目录：`qt-<locale>.json`（例如 `qt-fr.json`、`qt-de.json`）
- 键（Key）必须与 `zh-CN.json` 及 `qt-zh-CN.json` **完全一致**。
- 单数形式可在 Qt 目录中以 `<源文案>#one` 为键添加对应单数表达。
- 语言自称（如 `"简体中文"`、`"繁體中文"`、`"日本語"`）在所有语言中保持原样，不得翻译为外语。

### 2. 占位符与格式要求

- **占位符必须保持一致**：源文案中的 `{v0}`、`{count}`、`{name}` 等占位符在目标文案中必须完全相同，不可遗漏、拼错或更改为其他格式。
- **首尾空白严格匹配**：若源文案开头或结尾含有换行（`\n`）或空格（` `），译文必须严格保留相同的首尾空白字符。
- **避免直接拼接**：界面文案应为完整语义句式，不可随意拆分；拼接会导致不同语系的语序冲突。
- **严禁意外 HTML**：非富文本标签不得引入 `<script>`、`<iframe>`、`<img>` 或 `<style>` 标签。

### 3. 本地验证门禁

提交 PR 之前，请在项目根目录下运行以下命令确保通过严格门禁：

```bash
# 1. 验证 Web 目录完整性、占位符、首尾空白与标签安全性
python3 scripts/check_ui_locales.py

# 2. 验证 Qt 界面目录文案覆盖率（--strict 要求 100% 覆盖且无残留中文）
python3 scripts/qt_i18n_coverage.py --strict

# 3. 运行 Qt 国际化单元测试与单复数校验
python3 -m pytest tests/qt_i18n_test.py

# 4. 运行长语言布局测试（针对英/西/俄/法/德控件折行与内容截断测试，需要 PySide6 环境）
python3 -m pytest tests/qt_long_language_layout_test.py
```

---

## 验证记录

- **Web 目录校验**（`scripts/check_ui_locales.py`）：
  - `en`、`es`、`ru`、`ar`、`fr`、`de` 六门语言全部通过：0 missing / 0 unexpected keys，占位符与首尾空白 100% 匹配，0 错误。
- **Qt 覆盖率校验**（`scripts/qt_i18n_coverage.py --strict`）：
  - 37 个 Qt 界面文件覆盖的 1,513 条直接文案及全部 2,190 条目录条目在 `de` 和 `fr` 中均为 100% 覆盖，0 缺译，与原文相同 0 条（除语言自称外）。
- **单元与回归测试**（`tests/qt_i18n_test.py`、`tests/qt_long_language_layout_test.py` 等）：
  - `qt_i18n_test.py` 10 个测试用例全部通过（语言标准化、基准回退、Qt 目录加载、坏占位符容错、RTL 判定、法语/德语单复数形式）。
  - `qt_long_language_layout_test.py` 40 个测试用例全部通过（德语、法语及长语言下交付按钮折行、空状态高度、工作台右栏宽度、项目标签不截断、状态芯片尺寸均符合要求）。
