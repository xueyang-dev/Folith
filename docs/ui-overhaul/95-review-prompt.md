# 审查 Prompt：Folith UI 重做（给 Reviewer / Leader）

把下面整段**原样**交给审查者（另一个 agent 或人）。它自带上下文，不依赖前面的对话。

---

## 0. 你的身份与任务

你是 Folith（译页）桌面端的**独立审查者**，不是实现者的合作者。任务：审查分支
`feat/ui-overhaul` 上「按 2026-11-04 UI 审查报告重做界面」这项工作，判断它**是否真的做完了、
做得对不对、有没有把东西弄坏**。

姿态要求：

- **不要相信实现者的自述**。`docs/ui-overhaul/90-acceptance-report.md` 里的每个数字都要你自己
  跑一遍或从代码里读出来；对不上就以你测到的为准，并指出报告哪里夸大/失实。
- 实现者很可能有**自我确认偏差**（它的证据是它自己写的脚本产出的）。凡是能用第二种方法
  验证的，就用第二种方法（源码阅读 vs 跑脚本 vs 看截图）。
- 报告里主动写了两个「让步」（F6 网格行距未启用、长进程段错误），这两处**不是**免责条款：
  要判断这个取舍是否合理、有没有掩盖更严重的问题。
- 找不到问题时，明确说「未发现问题」，不要为了凑数编造；报告问题必须给 `文件:行号` + 复现方式。

## 1. 背景（只读这些，别读整个仓库）

仓库 `/Users/xueyang/Dev/FolioThread`，分支 `feat/ui-overhaul`，提交：

- `1ed740d6` feat(qt): 按 2026-11-04 UI 审查报告重做界面（F1–F20）
- `8991d64b` docs(ui): 记录 UI 重做的验收结果与已知遗留

必读：

1. `docs/ui-overhaul/01-master-plan.md` —— 执行契约（F1–F20 的验收口径、文件所有权）
2. `docs/ui-overhaul/90-acceptance-report.md` —— 实现者的自述结论（**待你证伪**）
3. `docs/ui-overhaul/00-current-architecture.md` 第 5 节 H1–H41 —— 这份代码库的硬约束
4. `docs/ui-overhaul/12-glossary.md` —— F16 的词表

上游需求原文（审查报告）在 `tmp/plan/plan.txt`（docx 提取文本；`tmp/` 可能不含在提交里，
若缺失就只看 `01-master-plan.md` 里转抄的 F 表）。

环境：`./venv/bin/python`（PySide6 6.11.2 / Qt 6.11.2），macOS。

## 2. 铁律

- **测试一律走 `./venv/bin/python scripts/run_tests.py …`**（文件锁串行）。直接 `python -m pytest`
  并发跑 Qt 测试会因共用 `QSettings` 域而段错误——实现者称这是段错误的真因，你要**独立判断
  这个解释是否成立**（见 §5 的段错误课题）。
- **不要改 `folith/qt/pdf_panel.py`**：它含用户未提交的本地改动，实现者全程未触碰。你若无意
  改到它，视为污染交付。
- 审查默认**只读**。只有当你确信有一个**明确 bug**（不是风格偏好）时才可改，并且必须单独一个
  提交、并在报告里说明。不要顺手重构。
- 不要 `git commit --amend`、不要 rebase、不要动分支。
- 不要清空 `tmp/` 里的证据目录（截图与脚本是审查材料）。

## 3. 先做这五件事（10 分钟内应有初步结论）

```bash
cd /Users/xueyang/Dev/FolioThread
git log --oneline -3 && git status --short          # 提交与工作区是否干净
git diff --stat 8991d64b~2..HEAD | tail -5          # 改动规模
./venv/bin/python scripts/run_tests.py tests/qt_design_tokens_test.py tests/qt_design_system_test.py tests/qt_client_style_test.py -q
./venv/bin/python scripts/check_ui_locales.py       # F16 门禁
./venv/bin/python scripts/ui_overhaul_measure_widths.py --detail | head -40   # F1 数字
```

四道关：**提交是否只含该含的文件**、**契约测试是否绿**、**文案门禁是否绿**、**F1 数字是否与报告一致**。

## 4. 逐条验收（按报告的顺序，每项都要给「证实 / 证伪 / 无法判断」）

对每个 F 编号，做三件事：读实现 → 用第二种方式复核 → 判断是否真解决了**上游报告提出的那个问题**
（不是"改了点东西"）。

重点怀疑清单（高价值，优先做）：

1. **F1**：实现者说最小宽度从 1174/1302/1374/1451 降到 874/1072/1123/1149。独立复跑，并
   **换一种口径**验证（例如强制 `QT_SCALE_FACTOR=2`、切到 en/ru 再量）。另外判断：`FlowRow`
   折行后，**窄窗口下顶栏是不是变得不可用**（按钮折成两行会不会撑高顶栏、把网格挤掉）——报告
   只给了宽度数字，没给窄窗口截图。
2. **F2**：四处（审校页 / 检查浮层 / 页头 / 交付页）是否真的同数。读
   `folith/workbench_view.py:review_workbench_view`、`folith/qt/adapter.py:followup_view`、
   `folith/qt/views.py` 的 `blocking_items()`/`_render_stage`，确认**没有第二处独立计数**残留。
   特别检查：`counts["blocking"] = max(队列计数, len(unresolved_blocking))` 里的 `max` 是否
   制造了新的不一致（队列比门禁多的时候会怎样？）。
3. **F4**：`folith/qt/status_voice.py` 是否真的成为唯一出口。全仓搜索是否还有别处在自己
   把状态映射成颜色（`ok/warn/danger/muted/info` 这套旧词汇以及 `STATUS_TONES` 的直接使用）。
   检查 `escalate()` 的规则是否有反例：什么情况下「任务未完成 + 运行完成」仍会显示成绿色？
4. **F6（实现者主动让步处）**：网格正文行距未启用。判断：①报告说「用户盯着看几个小时的地方」
   是网格，现在只改了展开行的编辑框，**这算不算没做完**？②`style.RowDelegate(reading_text=True)`
   的实现是否存在实现者没说的缺陷（读 `folith/qt/style.py` 的 `_paint_reading_text`，注意它用
   `_empty_index()` 替换索引来抑制重复绘字——这是不是肮脏 hack、会不会在高 DPI/选中态下出错）。
   ③把开关打开跑一次 `tests/qt_reading_grid_test.py` + 一次窗口测试，看是否真有稳定性问题。
5. **F7/F10**：对比度与按钮可点性。用 `tmp/overhaul-tokens/contrast_audit.py --check` 复算，
   并**自己从 token 重算**几个值（别只信脚本）。检查禁用态是否真的"无填充"，以及
   **深色主题下禁用态是否反而不可见**。
6. **F8/F9**：看 `tmp/ui-after/qt-01-tasks.png` 与 `qt-01-tasks-dark.png`。判断：①文件名两行
   折行后**扩展名被截断**（截图里是 `Need a Workspace.d...`）算不算没解决「相似文件分不清」；
   ②夜间压暗 0.85 是否过头（对比度、可读性）；③`set_glass_enabled(False)` 之后
   `CoverArt` 里是否有死代码/死分支。
7. **F11/F12/F13/F14**：交付页「成品感」是否真的成立（看 `qt-04-delivery-gate.png` /
   `qt-05-delivery-ready.png`）；F12 把「排除」藏进「更多」后，**`exclude_button` 成了没有父级
   布局的孤儿控件**（实现者给它 `setParent(self)` 并永久隐藏）——判断这是不是坏味道、有没有
   造成残留的内存/生命周期隐患；F13 的阈值 1500 是不是拍脑袋，会不会在 1440×900 的常见
   Mac 上把检查器直接藏掉（用户找不到逐段操作）。
8. **F16**：7 个语言目录是否真的对齐（键集、占位符、是否有中文漏译）。用
   `scripts/qt_i18n_coverage.py` 复核，并**抽查 5 个改动过的中文原文字符串**在 en/ru/ar 下的
   译文是否真的对（不是机翻错位、不是串行）。
9. **F20**：`docs/brand.md` 改成「以实现为准」是否合理，还是把规范变成了实现的复述（规范应
   约束实现，不是反过来）。`folith/brand.py` 的 `WORDMARK` 加空格后，是否还有别处按旧字标
   做字符串比较（搜索 `Folith·译页` 与 `Folith · 译页`）。
10. **测试稳定性**：实现者把「单进程跑 `qt_client_window_test` 偶发段错误」归因为长进程累积，
    并主张「干净 HEAD 单进程 118 passed」。独立验：`git stash` 不允许，改用
    `git archive` 到临时目录跑一次干净 HEAD 的 118 条；再在当前分支单进程跑一次。若两边表现
    相同（都崩），实现者的归因可信；若当前分支更早/更频繁崩，说明**引入了回归**，必须找出
    是哪一个改动（二分：`git bisect` 或逐个 revert 嫌疑文件）。
11. **声称的既有失败**：`tests/qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`
    被称作「干净 HEAD 上同红、顺序相关」。独立复现这条论断；若不是，它就是本轮的回归。

## 5. 代码质量与边界（不经报告，直接读代码）

- **平台边界**（AGENTS.md）：共享视图里不得出现 `sys.platform == "darwin"` 之类的分叉；文案与
  快捷键必须走 `folith/qt/platform_ui.py`；新快捷键的绑定与提示必须同源。搜一遍本轮改动。
- **RTL / 长语言**：新增或改动的自绘控件（`DataTable`、`ModeCard`、`_TaskCard` 的两行标题、
  `FlowRow` 折行）在阿拉伯语与俄语下是否还成立。至少跑 `tests/qt_long_language_layout_test.py`、
  `tests/qt_arabic_rtl_test.py`，并**目视一张 ar 截图**（`scripts/qt_showcase_screenshots.py --lang ar`
  若支持；不支持就说明无法判断）。
- **死代码**：实现者自己列了三处死 QSS（`#presetOption`/`#modeTrack`/`#primaryCombo`）与
  `exclude_button` 孤儿化。判断这些是否应在合并前清掉，还是可以作为遗留。
- **硬编码**：本轮新增的中文字面量是否都进了 `qt-*.json`（`check_ui_locales.py` 应能发现，
  但它的 AST 覆盖可能不全——抽查 `followup_panel.py`、`views.py` 里新增的 `i18n.tr(...)`）。
- **测试改动**：本轮改了 8 个既有测试文件。逐个判断被改的断言是「快照」（可改）还是「契约」
  （不该动）。重点看 `tests/qt_client_window_test.py` 里被改成「更多按钮在场」的那条。

## 6. 输出格式

按严重程度排序，每条给：

```
[阻断|严重|一般|建议] <一句话结论>
  位置：文件:行号
  证据：复现命令 / 数值 / 截图路径
  期望 vs 实际：
  判断：是回归 / 是没做完 / 是取舍不当 / 是风格偏好
```

最后给三段：

1. **F1–F20 逐条裁决表**（证实 / 部分证实 / 证伪 / 无法判断 + 一句理由）。
2. **发布就绪判断**：这个分支能不能进 0.8.1？必须先修的阻断项有哪些（最多 5 条）。
3. **对实现者自述的纠正**：报告里哪些说法你实测不符、哪些结论它下得太早。

## 7. 时间盒

先给一份 20 行内的「第一小时结论」（只覆盖 §3 的四道关 + 你最先发现的 3 个问题），
再继续做 §4/§5。不要等全部做完才说话。
