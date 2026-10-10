# 译前准备 · 「翻译策略」→「翻译设置」：三层信息架构

日期：2026-10-09 · 界面：桌面端（PySide6 / cocoa，**真实窗口 + 离屏各一套**）· 主题：浅色 / 夜间 / 纸面

## 起因

试用反馈（原文）：这一块所有控件**同等视觉重量**；分段控件被拉满整行；术语没有解释
（"批次策略"）；"预设"与"参数"分不清；"已自定义"说不清是从哪来的；次要信息常驻；
宽屏没被利用；夜间面不像 macOS。

诊断：问题不在间距与字号，而在**信息架构** —— 「翻译模式」（一个决定）和「执行参数」
（四个旋钮）被排成了同一层，用户要先读懂全部才能知道该点哪里。

## 定案：三层

| 层 | 内容 | 语义 |
|---|---|---|
| ① 翻译模式 | 三个横排选项：快速 / 标准 / 精细（图标 + 标题 + 一行摘要） | **主决定**：选哪套模板 |
| ② 内容处理 | 文本切分方式（按句/按段）、自动识别术语、使用翻译记忆 | 每次翻译都相关的**内容决策**，一行一项，说明在左、控件在右 |
| ③ 高级设置 | 折叠头「高级设置 · 审校与执行参数」→ 译后自动审校、审定并锁定术语、并发请求数、请求调度 | **执行参数**，默认收起 |

折叠头是 `QPushButton`（可 Tab、空格开合、`accessibleName`），不是只有箭头能点。
模式选项是 `QPushButton(checkable)` + `QButtonGroup(exclusive)`：得到按钮级的悬停/选中/
方向键行为，选中态读作"抬起的卡片 + accent 描边"，而不是实心蓝块（macOS 分段控件的语义）。

## 文案（**显示名可以变，取值一个都没动**）

| 旧 | 新 | 备注 |
|---|---|---|
| 翻译策略 | 翻译设置 | 卡片标题 |
| 策略预设 | 翻译模式 | 区块标题 |
| 深度研究 | **精细** | `STRATEGY_PRESET_LABELS["学术增强"]`；内部键仍是 `学术增强` |
| 翻译单元粒度 | 文本切分方式 | 帮助：决定翻译单元的大小 |
| 按自然句（推荐）/ 按自然段 | 按句 / 按段 | 取值仍是 `sentence` / `paragraph` |
| 自动抽取并注入术语 | 自动识别术语 | 帮助：提取关键术语，保持译名一致 |
| 复用翻译记忆 | 使用翻译记忆 | 帮助：参考已保存的历史译文 |
| 完成后自动审核 | 译后自动审校 | 帮助：完成初译后进行额外检查 |
| 审核并冻结候选术语 | 审定并锁定术语 | 帮助：审查候选术语，锁定已确认的译名 |
| 批次策略 | 请求调度 | 帮助：同时向模型发出的批次请求数…（并发数） |
| 保守 / 均衡 / 快速 | 稳妥 / 均衡 / **高吞吐** | `BATCH_PROFILE_LABELS`；取值仍是 `保守/均衡/快速` |
| …：选择翻译策略与需要产出的文件… | …：**确认翻译设置**与需要产出的文件… | 面板副标题；`翻译策略` 已不在这一屏出现 |

新增的一行摘要（`STRATEGY_PRESET_SUMMARIES`）：快速=优先处理效率、标准=质量与效率兼顾、
精细=更充分的质量检查 —— **按真实预设配置写**（精细多出审校与术语锁定两步），
配置本身一个字没改。

## 共享真源（Web 与桌面端一份）

- `folith/application/task_setup.py`：`STRATEGY_PRESET_LABELS`（学术增强→精细）、
  新增 `STRATEGY_PRESET_SUMMARIES`、新增 `BATCH_PROFILE_LABELS`（稳妥/均衡/高吞吐）。
- `RESEARCH_STRATEGY_PRESETS` 保留**全部历史标签**（`深度研究`、`研究与报告`、`研究级`
  以及新增 `精细`）：按标签调用研究产物闸门的旧代码不会因此少掉一整块产物。
- `folith/qt/adapter.py#setup_choices()`：策略预设多吐 `description`、批次档多吐 `label`
  （Web 端卡片仍用自己的长文案）；`stemming_unit_options` → 按句/按段。
- 内部键、序列化键名、`apply_task_setup` 命令、`resolve_effective_settings` 全部未动。

## 「已自定义」由**实际生效的配置**推出

- `_MANAGED_STRATEGY_KEYS` 只比对面板能改的 8 个字段 → 预设里若有面板没暴露的新键，
  不会让指示常年亮着。
- 改回原值（不经过任何预设点击）→ 指示自己消失，恢复入口也隐藏。
- 状态行三态：`当前使用「标准」默认配置` / `标准模式 · 已自定义`（+ `恢复「标准」默认`）/
  `当前配置不对应任何预设；可直接调整下面的选项。`（无恢复入口 —— 恢复不到"哪一套"）。
- **换模式**会把交付方案重置为标准交付（与 Web `_apply_preset` 一致）；
  **恢复默认**只动策略、不动交付（两条路径语义不同，都有用例钉住）。
- 准备阶段的后台轮询不重建设置面（`state_signature` 未变）→ 用户改完还没开始，不会被刷新抹掉。

## 样式：四组通用规则，零字面色

| objectName | 规则 |
|---|---|
| `#presetOption` | 静止 `bg-surface-sunken` + hairline；hover `bg-surface-subtle` + `border-strong`；`:checked` `bg-surface-raised` + `accent` 边；`:focus` accent 边 |
| `#presetTitle` / `#presetDesc` | 纯字号 + 字色（`text-primary` / `text-secondary`） |
| `#disclosureHeader` | 透明底；hover `bg-hover`；focus accent 边 |

`sha256(build_qss(theme))[:16]`：light `e36dd3a80d89ce48` · paper `33f8468ebbab4135` ·
dark `72862016013ec7c7`（基线已同步到 `qt-desktop-visual.md`）。
夜间专属 token 在 light / paper 的 QSS 里命中数 **0**（这次一行夜间结构规则都没加）。

## 几何取证

离屏（`tmp/prep_shot.py`，窗口 1180×820）：

| 主题 | 视口 | 面板 | 横向滚动 | 三个模式选项 | 其它控件 |
|---|---|---|---|---|---|
| dark | 1129×615 | 1129×1352 | max=0 | 362 / 363 / 362 × 55 | segMode 116×32 · 并发 76×30 · 调度 186×32 · 折叠头 1103×22 |
| light | 1129×615 | 1129×1352 | max=0 | 同上 | 同上 |
| paper | 1129×615 | 1129×1352 | max=0 | 同上 | 同上 |

窄窗（应用自身最小宽度：窗口 962 → 视口 911）：选项 290 / 289 / 290 × 55，横向滚动 max=0，
控件都没被压到 `minimumWidth`。

真实 cocoa 窗口（`tmp/prep_real.py`，DPR 2.0）：
`{'window': (1180, 810), 'viewport': (1129, 634), 'panel': (1129, 1331), 'hscroll_max': 0,
'note': '当前使用「标准」默认配置', 'restore_visible': False, 'advanced_body_visible': True,
'mode_titles': ['快速','标准','精细'], 'mode_checked': ['标准']}`

左基线（`tmp/align_probe.py`，面板宽 929）：翻译模式标签 / 状态行 / 内容处理标签 / 每一行 /
折叠头 / 折叠体内每一行**全部 x=13**（折叠头原先因左右各 4px 内边距参差 4px，已去掉）。

长文案（`tmp/long_label_probe.py`，西/俄/德语式长句）：
真实语言下三行说明都放得下（最长 237px / 可用 265px）；刻意的超长说明（286px）**以省略号收尾**
而不是被切掉，完整文案留在 tooltip。三套主题下横向滚动都是 0。

## 交互态像素（`drawControl` 显式 state，非肉眼）

| 主题 | 静止 | 悬停 | 选中 | 选中+悬停 | 焦点 |
|---|---|---|---|---|---|
| light（accent `#004cfd`） | 底 `#f2f4f7` | 边 `#c9d2df` 底 `#f9fafb` | 边 `#004cfd` 底 `#ffffff` | 与选中同 | 边 `#004cfd` 底 `#f2f4f7` |
| dark（accent `#82acff`） | 底 `#1a1a1c` | 边 `#58585a` 底 `#212123` | 边 `#82acff` 底 `#2a2a2d` | 与选中同 | 边 `#82acff` 底 `#1a1a1c` |
| paper（accent `#4c5a66`） | 底 `#e9dfcb` | 边 `#a9a395` 底 `#f6f0e2` | 边 `#4c5a66` 底 `#fdfaf3` | 与选中同 | 边 `#4c5a66` 底 `#e9dfcb` |

## 自动化验证

`tests/qt_task_setup_test.py` **40 passed**（本轮新增 16 条、另 3 条既有用例改到新 API）：

| 用例 | 钉住的不变量 |
|---|---|
| `test_strategy_presets_and_labels_are_the_shared_ones` | 内部键是 `快速/标准/学术增强`；显示名是 `精细`；历史标签仍在研究产物闸门里 |
| `test_every_preset_and_batch_profile_has_a_short_summary` | 每个模式与批次档都有非空摘要（三层的第一层不许留空白） |
| `test_the_translation_settings_panel_has_three_levels` | 三层结构（含折叠体默认隐藏） |
| `test_the_advanced_header_toggles_from_the_keyboard` | 空格开合折叠体 |
| `test_each_mode_loads_its_own_configuration` | 选模式 = 真把整套模板套上去（8 个字段） |
| `test_the_custom_indicator_follows_the_effective_configuration` | 指示随**生效配置**走；改回去自己消失 |
| `test_restoring_the_mode_keeps_the_delivery_choices` | 恢复默认不动交付 |
| `test_changing_the_mode_resets_delivery_like_the_web_wizard` | 换模式才重置交付（与 Web 一致） |
| `test_re_clicking_the_selected_mode_does_not_wipe_delivery` | 重复点选中的模式无副作用 |
| `test_clicking_the_row_toggles_the_switch_once` | 整行可点且只翻一次 |
| `test_the_selected_mode_is_drawn_with_theme_tokens` | 选中项有 accent 边、未选中项没有（三主题像素） |
| `test_the_batch_display_name_does_not_change_the_saved_value` | 「高吞吐」只是显示名，写回仍是 `快速` |
| `test_the_panel_never_scrolls_sideways_at_the_narrowest_window` | 最小宽度下不横向滚动、选项不被压扁 |
| `test_a_long_translated_description_is_elided_not_cut` | 长文案省略不硬裁，原文在 `fullText()`/tooltip |
| `test_the_card_keeps_one_left_baseline` | 卡片内一条左基线 |
| `test_the_old_wording_is_gone` | 旧文案（策略预设/翻译单元粒度/按自然句（推荐）…）不许回来 |
| `test_a_background_poll_does_not_discard_the_panels_edits` | 后台轮询不丢用户在准备面上的选择 |

相邻面：`app_boot_test.py`（Streamlit 冒烟，直接跑）✅ · `qt_i18n_test.py` 8 ·
`qt_client_style_test.py` 8 · `qt_delivery_ui_test.py` 13 · `qt_settings_dialog_ui_test.py` 10 ·
`effective_settings_test.py` 20 —— 全绿。

## 截图清单

| 文件 | 说明 |
|---|---|
| 01-prep-collapsed-dark / 02-prep-expanded-dark | 真实窗口 1180，折叠 / 展开 |
| 03-panel-custom-dark | 面板全图：`标准模式 · 已自定义` + 右侧 `恢复「标准」默认` |
| 04 / 15 / 16-modes-focus-{dark,light,paper} | 焦点落在选中项上（accent 边） |
| 05-panel-expanded-dark · 06-panel-collapsed-paper · 07-panel-expanded-light · 08-panel-custom-light | 三主题全图 |
| 09-narrow-expanded-dark-960 · 10-narrow-collapsed-light-960 | 最小宽度附近（962 窗口 / 911 视口） |
| 11/12/13-real-window-*-dark | 真实 cocoa 窗口（DPR 2.0）：折叠 / 自定义 / 折叠体底部 |
| 14-zoom-strategy-card-dark | 1.45× 放大，看三层之间的发丝线与左基线 |

生成夹具：`tmp/prep_shot.py`（离屏）、`tmp/prep_real.py`（真实窗口；本环境 `screencapture`
对所有窗口都瞎，一律用窗口自身 `grab()`）。

> ⚠️ **图的修订版**：这 16 张取自"最终修订的前一步" —— 代码与最终字节**只差面板副标题的措辞**
> （图上仍是「正在准备「x.pdf」：选择翻译策略与需要产出的文件…」，最终字节已改为
> 「…：确认翻译设置与需要产出的文件…」，见上一节文案表）。原因是重出这一版时沙箱 shell
> 在真实窗口取证那一趟被 `SIGTERM` 打断后**整体不可用**（连 `echo` 都返回 137，子 agent 的
> shell 同样不可用），最新一版离屏图已生成在 `/tmp/arch-wide`、`/tmp/arch-narrow` 但没能覆盖过来。
> 其余结构完全一致（三层、三个选项尺寸、折叠头、左基线、省略号行为都未变）。

## 遗留

1. **i18n 覆盖率**：面板新增/改名的 20 余条中文文案在 en / es / ru / ar 会回退中文
   （`scripts/qt_i18n_coverage.py`：393/453，缺 60）。这是**仓库既有约定**——中文原文即键，
   缺条目回退原文并由脚本如实报告；且 `qt_i18n_test.py` 要求五语目录**键集完全一致**，
   不能只补一门，故没有在本轮手写译文。
2. **目录标签不经过 `tr()`**：`adapter` 给的标签（快速/标准/精细、稳妥/均衡/高吞吐、
   交付方案、识别语言…）在 Qt 侧是原样显示的（`dialogs.py:413` 同）。即使 en 目录里已有
   `快速→Quick`、`标准→Standard`、`保守→Conservative`，界面也仍显示中文。要统一处理得
   连同 `delivery_seg` / `target_languages` 一起改，属于跨面板议题，本轮未动。
3. **设置对话框的同一组开关仍用旧词**：`dialogs.py` 里「自动抽取并注入术语 / 复用翻译记忆」
   与本轮的「自动识别术语 / 使用翻译记忆」并存。那是**另一个面**（设置对话框的"新建任务默认值"），
   按简报的范围纪律本轮未改，但同一个开关出现两个名字，建议下一步统一。
