# #90 / #102 · 审校状态文案的结构化与本地化

## 背景

#90 是审校流程的跟踪 issue，实现子 issue 只剩 #102（版式、状态措辞、可访问性）。
#102 的实现（PR #209）已并入 `main`，但它自己记录的边界里还有一条代码缺口：

> 核心层仍有少量拼接式状态句（跳过原因明细、交付/导航标题）以中文拼好再交给界面，
> 这一轮没有做结构化重构。

于是在非中文界面里，审校页会漏出中文，最典型的是「按策略跳过」的覆盖说明，以及
「跳过」这类合成审校项的摘要/建议。这份改动只补这条**代码**缺口；#90/#102 仍要求
的原生 macOS/Windows 实机验收不在本改动范围内，也没有被它宣称完成。

## 改了什么

1. `folith/workbench_view.py`
   - 新增 `coverage_copy_parts(readiness)`：把「整段/部分跳过」的覆盖结论拆成结构化
     部件（`kind`、`skipped`、`reviewed`、`total`、`reasons[{reason, count}]`），
     并在 `review_workbench_view()` 里以 `"coverage"` 返回。
   - `_review_task_item()` 增加 `skip_reason`（原始原因码），供界面重排摘要。
   - **没有改动**任何现有中文串：旧的 `_coverage_copy` / `summary` 等原样保留，
     供 Streamlit 时代与测试沿用，默认中文逐字不变。
2. `folith/qt/review_panel.py`
   - `_coverage_texts()`、`_localized_reason_breakdown()`、`_localized_task_copy()`
     用显式占位符模板，把上述结构化部件按当前语言重排；发现项正文（模型产出）
     保持原语言，不翻译。
3. `folith/resources/locales/qt-*.json`（7 种语言）
   - 补齐覆盖标题/说明、跳过原因规范标签与合成项建议的翻译；`{v0}` 等占位符保留。
4. `scripts/qt_i18n_instrument.py`
   - 把审校跳过原因标签加入动态词表，避免覆盖率把动态标签当缺译漏掉。

## 证据

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q \
  tests/qt_review_layout_a11y_test.py tests/qt_review_panel_test.py \
  tests/qt_i18n_test.py tests/phase3_review_coverage_test.py
# 96 passed

venv/bin/python scripts/qt_i18n_coverage.py --strict
# 7 种语言 1701/1701，缺 0，与原文相同 0
```

新增回归在 `tests/qt_review_layout_a11y_test.py`：

- 7 种语言 × {整段跳过, 部分跳过}：标题、说明、队列表项、详情正文都不出现中文；
- 英/德：`stale` 合成项详情里的建议已本地化；
- 中文：覆盖标题/说明与核心投影 `readiness.label/detail` **逐字相同**（默认不回退）。

## 边界

- 这里只是实现与自动化证据，**不是** #102/#90 要求的人工/电脑操作原生验收。
  `docs/ui-audit/13-workspace-review/README.md` 里的 8 步脚本仍需在真实 macOS 与
  Windows 构建上执行。
- `workbench_view` 的 `delivery` / `nav` 标题同样由核心拼中文，但当前 Qt 审校页
  不消费这两个字段（只有 Streamlit 时代的 `workspace_view`/`task_overview` 测试用到），
  因此不在这条用户可见缺口的修复范围内，留待对应界面真正接入时一并结构化。
