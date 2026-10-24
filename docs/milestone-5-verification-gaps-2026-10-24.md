# Milestone 5 · 未完成验证记录

日期：2026-10-24。对应方案：Milestone 5 开发指导方案（`milestone-5-development-guide-2026-10-24.md`）。
同一里程碑的另外两份记录随前面的 PR 落地：`milestone-5-baseline-record-2026-10-24.md`（#29/#93）、
`milestone-5-candidate-contract-notes-2026-10-24.md`（#92/#94）。

本文件只记录**还没做完的验证**和它缺什么。#29 / #93 / #92 / #94 的代码与证据在各自的 PR 里；
#49 与 #83 无法在本机闭环，缺项写在下面，不勾选即未完成（方案 §6 最后一条）。

## 1. #83 原页定位：代码侧已证实，原生侧未验收

### 1.1 定位链现在怎么走

| 环节 | 位置 | 行为 |
| --- | --- | --- |
| 选段映射 | `qt/views.py:5727` `_row_selected(position)` → `self._rows[position].index`；`qt/views.py:5632` `set_segment(row.index)` | 行位置只在网格内部使用，写入面板的始终是**源段号**；筛选只改可见集合，不重编号（`qt/views.py:4785` `_visible_rows`） |
| 锚点解析 | `pdf_preview.py:206` `get_segment_source_locations` | 读 `pairs[i].source_spans`，回退 `state.segment_source_spans`；`page_id` 不在 `document_ir.pages` 里的 span 直接丢弃 |
| 取哪个位置 | `qt/adapter.py:1697` `segment_pdf_location` | 当前页确有该段位置 → 保持该页；否则 `pdf_preview.py:243` `get_cached_segment_location` 取该段**首个有效持久化位置** |
| 文本搜索兜底 | `pdf_preview.py:98` `find_segment_page_and_rects` | 仅在没有持久化位置时使用，从当前页开始搜 |
| 页级锚点 | 同上 | 有锚点但没有可用 bbox → 返回该页、`rects=[]`，提示"仅定位页面，没有行级坐标，不画高亮" |
| 渲染代次 | `qt/pdf_panel.py:217` `_rendered` | `generation != self._generation` 的结果直接丢弃，旧页不会覆盖新页 |

据此，"把过滤后的行号当源段号"这条假设**不成立**：`row.index` 在筛选前后都是源段号，
`tests/pdf_navigation_anchor_test.py::test_filtered_rows_still_locate_by_source_segment`
用真实 `WorkbenchView` 走了一遍筛选 → 选中第 2 行（源段 3）→ 定位，页落在这段自己的锚点上。

### 1.2 新增回归（可离屏，已通过）

`tests/pdf_navigation_anchor_test.py`，10 例：

| 用例 | 钉住的性质 |
| --- | --- |
| `test_anchor_beats_text_search_when_the_text_also_exists_elsewhere` | 同一句话在第 1、3 页都有，锚点在第 3 页 → 定位必须到第 3 页（纯搜索会到第 1 页） |
| `test_a_legacy_task_without_anchors_falls_back_to_search` | 没有锚点的旧任务仍能靠文本搜索定位 |
| `test_a_cross_page_segment_keeps_the_page_it_is_looking_at` | 跨页段落：看哪页停哪页；都不是 → 回该段首个有效位置，不赖在当前页 |
| `test_a_page_only_anchor_says_it_has_no_line_level_highlight` | 页级锚点如实说明不画高亮 |
| `test_an_anchor_pointing_at_a_missing_page_is_not_a_location` | `page_id` 悬空的锚点不被当成位置 |
| `test_a_broken_box_keeps_the_page_but_never_invents_a_rect` | bbox 越界/反向 → 页码仍有效，但不编矩形、不谎称已高亮 |
| `test_missing_source_and_out_of_range_report_instead_of_guessing` | 空原文、越界段号各自给出说明 |
| `test_filtered_rows_still_locate_by_source_segment` | 筛选后按源段号定位；清除筛选后仍停在这一段 |
| `test_locate_can_run_without_a_new_selection` | "定位当前段"能主动执行，不依赖再次触发选择 |
| `test_an_older_render_cannot_overwrite_a_newer_location` | 旧代次渲染结果被丢弃，页不被翻回去 |

这些用例在 `origin/main` 基线上同样通过，说明**第 3、5 条的产品代码无需改动**；
本次交付是证据，不是修复。第 2、4 条的"延迟渲染未解除 / transition 中断"没有复现证据，
按方案"没有证据不改"的要求原样保留。

### 1.3 仍未验证（关闭 #83 所需）

方案要求 Windows 和 macOS **原生客户端**完成同一组步骤，offscreen 测试不能替代：

1. 原 49 页任务：正常浏览 → 搜索 `8.5` → 选第 34 段 → 再次点击同一段 → "定位当前段" → 清除筛选；
2. 面板切换（原页 ⇄ 术语 ⇄ 译文）与快速连选（按住方向键扫过多段）；
3. 记录实际页索引、可见页码、高亮矩形与状态提示是否一致。

需要的东西：**那份 49 页任务**（或等价的可导入 PDF + 已切分状态），以及一台 **Windows 与一台 macOS** 机器上跑起的原生客户端。
本机只有 Windows + offscreen，macOS 侧没有设备。

## 2. #49 扫描 PDF 文档画像：没有真实请求，不能关闭

方案 §3 已明确：#51 合入的是**恢复能力**（非法 JSON / 缺字段的纠正请求、总计至多三次调用、警告持久化），
其说明写明未执行真实模型请求、未复现原 301–400 页案例。现有 `tests/document_profile_test.py`
覆盖的都是 mock 与故障注入，按方案 §6 最后一条"没有真实请求不关闭 #49"，保持待验证。

缺的材料（缺任一都无法闭环）：

1. **原案例的 301–400 页扫描 PDF**（或其等价的、有证据说明等价性的文档：文档类型、扫描比例、抽样长度、语言一致）；
2. **可用的 provider 连接**：provider 类型、model、base URL、有效凭据；
3. 该任务副本上的**原始警告与阶段日志**（`profile_warnings`、管线画像阶段的记录），用来对比验证前后的差异；
4. 允许的调用额度——画像阶段预计 1 次请求，加纠正重试总计不超过 3 次，不重译全书。

拿到之后的最小验证：在任务副本上只跑画像阶段，记录源码提交、样本范围、调用次数、耗时、实际 usage
（未知就单列）、脱敏后的错误与最终 profile 校验结果；样本缺失先查导入/OCR，请求失败先查连接/取消/限流，
返回格式问题查解析与有限纠正，结构无效查校验规则——按观测选最小修复，并补一条针对该失败的回归。

## 3. 方案 §6 清单现状

| 清单项 | 状态 |
| --- | --- |
| 四类入口在人工修改/接受/已审校译文上不自动覆盖 | 已通过（#92 保护语义 + 批量默认跳过） |
| 采用后来源、文本变更、人工决定与版本可追溯 | 已通过（#29/#93/#92/#94） |
| UID 错配、过期、并发、重复点击、worker 锁安全拒绝 | 已通过（#94 集成套件） |
| 失败/重启/journal replay/compaction/撤销后状态一致，旧任务可用 | 已通过（#94） |
| 只失效受影响证据，人工历史与交付快照保留，无错误 TM 提升 | 已通过（#94；并修掉无审校事件时不撤销 TM 的缺陷） |
| #49 真实有效画像证据 | **未完成**：缺 §2 的四项材料 |
| #83 Windows/macOS 原生验证 | **未完成**：代码侧已证实并加回归，缺 §1.3 的任务与两台原生机器 |
| 既有门禁不被新入口绕过 | 已通过（回归全绿，i18n 1392/1392 × 5） |
| 记录额外成本/延迟及未完成验证 | 即本文件 |
