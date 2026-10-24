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

### 1.3 报告里四条假设的结论

| 假设 | 结论 | 依据 |
| --- | --- | --- |
| 1. 定位偏向当前页、丢弃其它持久化位置 | **不成立** | `adapter.segment_pdf_location`：当前页有该段位置才保持，否则取该段首个有效位置，只在完全没有持久化位置时文本搜索；`test_a_cross_page_segment_keeps_the_page_it_is_looking_at` 钉住 |
| 2. 延迟渲染永不恢复 | **未复现** | 每条结束过渡的路径都调 `_resume_source_render()`；`test_an_interrupted_column_transition_does_not_leave_the_render_deferred`、`test_a_render_asked_for_during_a_transition_is_flushed_when_it_settles` 钉住 |
| 3. 筛选后行号被当源段号 | **不成立** | 筛选只改可见集合，`row.index` 始终是源段号 |
| 4. 页码框空白/黑块 | **成立，已修** | 原生 `QSpinBox` 从未被仓库 QSS 覆盖（`setup_panel.py` 的同一条约定），深色主题下退回系统外观 |
| 5. 再点同一段没反应 | **成立，已修** | Qt 不为已是当前的单元格发 `currentCellChanged`，第二次点击根本没进跟随逻辑；已挂 `cellClicked`（`_row_reclicked`） |

修复在 PR `codex/m5-pdf-navigation-fixes`：一个控件、一个信号、13 个回归
（`tests/pdf_navigation_anchor_test.py` 13 例 + `tests/qt_pdf_panel_test.py` 页码框行为）。

### 1.4 原生 Windows 验证（2026-10-24，等价 49 页任务）

等价件：`case-49-pages.pdf`（49 页英文，第 5 页标题 "2.2 Accident Sequence"，
每 7 页一个 "8.5 Sensor calibration" 小节，302 段），经产品自身的导入链路
（`extract_document_paragraphs_with_report`，ocr_mode=off）切分。
在**真实 Windows 窗口**（非 offscreen，2250×1425，深色主题）里用真实鼠标点击跑完整序列，
每步截图与 JSON 日志存于仓库外 `.qa-artifacts/m5-83-native/`：

| 步骤 | 结果 |
| --- | --- |
| 打开原页面板 | 第 1 段 → 页 1，提示"第 1 段：已高亮"，1 个高亮矩形 |
| 搜索 `8.5` | 可见行 = 源段 37,38,39,40,41,42,43,49,80,…（**未重编号**），当前段 37 → 页 7 |
| 点击第 3 行（源段 39） | 页 7，提示"第 40 段：已高亮"，1 个矩形 |
| 在页码框输入 `1` 回车 | 页 1，框内显示 "1"，矩形清空（该段不在第 1 页——如实，不谎报高亮） |
| **再点同一行** | **页回到 7、高亮恢复**（修复前这里毫无反应） |
| 点"定位当前段" | 停在页 7，代次不变（没有多余重渲染） |
| 清除筛选 | 行恢复 0…11，仍是第 40 段 / 页 7 |

### 1.5 仍未验证（关闭 #83 所需）

**macOS 原生客户端**：同一组序列（浏览 → 搜 `8.5` → 选命中行 → 再点同一段 →
"定位当前段" → 清除筛选；面板切换与快速连选），本机没有 macOS 设备，无法代跑。
Windows 侧已完成（§1.4）。

## 2. #49 扫描 PDF 文档画像：真实请求已跑通，根因已定位并修复

### 2.1 真实请求记录（2026-10-24）

等价件 `case-301-400.pdf`：100 页英文叙事散文，**95 页无文本层（纯扫描图）+ 5 页有文本层**，
与原案例"100 页、部分无文本层"同构；由脚本生成（`fitz` 栅格化 + 印刷页码），源码提交见 PR。
连接来自用户客户端自己的配置（OpenCode Go），密钥只从本地存储读取，未写入任何输出。

| 轮次 | 模型 | OCR | 调用 | 结果 |
| --- | --- | --- | --- | --- |
| A | `deepseek-v4.1-flash`（连接配置的模型） | auto（文本层） | 1 次，0.8s，HTTP 429 `GoUsageLimitError` / `limitName: monthly` | 画像失败。**修复后**：1 次调用即停，警告"该连接的本月用量额度已用尽，等一会儿不会恢复；请更换连接或提升额度。" |
| B | `longcat-2.5-preview-free`（provider 默认模型） | force（全文 OCR） | 3 次共 4.8s：2 次瞬时 429（上游不可用）+ 1 次成功 | **画像成功**：领域"文学"，类型"游记/叙事散文"；抽样 3 个窗口 5420 字符；OCR 102 段 / 59.5s |

同一模型既有失败（额度）也有成功（免费额度），回答了 issue "another document or model
also fails" 之问：失败与文档无关，是**连接的按月额度用尽**。

### 2.2 同一本书的相邻分段（2026-10-24 补充）

用户提供 `THE_ROAD_TO_MECCA_(Muhammad_Asad)_(Z-Library)_401-416.pdf`——**与原案例同一本书、
同一 Z-Library 来源、同一导出工具**的紧接着的下一段（原案例是 301–400，这是 401–416）。
在合并 #117 修复后的 main（`29285f5`）上重跑同一脚本：

| 项 | 值 |
| --- | --- |
| 页数 | 16（**16/16 全有文本层**；原案例的 301–400 段是"部分无文本层"，这一段是全有） |
| 提取 | 53 段 / 3.1s（走产品自身链路 `extract_document_paragraphs_with_report`，`ocr_mode=auto`，文本层直接抽取，未走 OCR） |
| 抽样 | 3 个窗口，共 5865 字符 |
| 源码提交 | `29285f5`（main，含 #116/#117 修复） |
| 密钥 | 从用户客户端自己的 OpenCode Go 配置读取，未写入任何输出 |

| 轮次 | 模型 | 调用 | 结果 |
| --- | --- | --- | --- |
| C | `deepseek-v4.1-flash`（连接配置的模型，当月额度已耗尽） | 1 次，0.91s，HTTP 429 `GoUsageLimitError` / monthly | **画像失败**，`quota_exhausted`，1 次调用即停（不再浪费退避重试），警告"该连接的本月用量额度已用尽，等一会儿不会恢复；请更换连接或提升额度。" |
| D | `longcat-2.5-preview-free`（provider 默认模型） | 2 次共 9.9s：1 次 429 `server_error / Upstream request failed: Endpoint is unavailable`（瞬时，正确分类为 `rate_limited` 走退避）+ 1 次成功 6.68s（762 字符） | **画像成功**：领域"文学（旅行文学）"，子域"麦加朝觐与阿拉伯世界旅行见闻"，类型"回忆录/旅行文学"，语气"正式书面语，文学性与沉思性较强"，置信度 0.92；3 个 section（S1 0–3 抵达麦加，S2 26–29 远望阿拉法特平原，S3 52 结尾追问），带风格约束（保留 hajj / ihram / mutawwif / Arafat / Jabal ar-Rahma 音译等） |

**这组证据同时回答 issue 的三个 "Needed"**：

1. 日志行 / 模型 → C 组日志（上表）+ `run_real.py` 落盘的 `extraction.json` / `sample.json` / `profile.json`；
2. "whether another document or model also fails" → **换模型就成功**（同一份 PDF，两个模型，结果翻转），失败跟的是连接额度，不是文档；
3. 两类 429 现在能正确区分：连接的 `GoUsageLimitError`（周期额度用尽）走 `quota_exhausted` 即停，上游瞬时不可用走 `rate_limited` 退避后成功——修复前两者都被当限流盲目重试三次。

与原案例的严格差异，如实记录：这一段是 401–416 而非 301–400，且 16 页全有文本层
（原报告说 "some without a text layer"——那段无文本层的页在这份 PDF 的前段，本段没有覆盖到）。
不过关键归因不依赖这一点：**同一本书 + 同一连接 + 两个模型 + 结果翻转**已经把失败原因钉在
连接额度上，与文档形态无关；OCR 路径本身由 §2.1 的等价扫描件覆盖。

### 2.3 根因与修复

原代码把"额度用尽"当成"限流"：同样 429，走退避重试三次，最后告诉用户"请稍后重试"——
按月额度等多久都不会恢复，这句话是假的；三次重试只是把同一份额度再撞三遍。
另外画像失败也把 `profile_done` 写成 `True`，任务从此永久跳过阶段 1.2，连接修好也补不回来；
而且这条警告只写在 `state["profile_warnings"]`，收尾页只读提取报告的警告，界面上看不见。

修复（PR `codex/m5-profiling-quota`）：

1. `provider_error_status` 先于 429 判定"额度用尽"（`GoUsageLimitError` / usage limit /
   `insufficient_quota` / quota exceeded），按 metadata 的 `limitName` 说明"本月/本周期"，
   给可操作的提示；普通限流维持原状。
2. 画像遇 `quota_exhausted` 立即停止（1 次调用），遇限流仍退避重试。
3. 画像失败不再写 `profile_done=True`——阶段 1.2 的入口条件就是 `not profile_done`，
   重跑任务会再画像一次。
4. 收尾页与注意力统计把 `state["profile_warnings"]` 纳入扫描，画像失败在界面上可见、
   带原因（`task_attention.state_facts`、`adapter.followup_view`）。

回归：`tests/provider_quota_errors_test.py`（5 例，含真实错误形状）+ `tests/qt_followup_test.py`
（画像失败可见、补上后消失）。

### 2.4 关闭结论

issue 自列的 "Needed" 三项（画像阶段日志、所用模型、其它模型是否也失败）已由 §2.1 与 §2.2
覆盖，且 §2.2 用的是**与原案例同一本书的相邻分段**，不是合成的等价件。失败原因（连接当月
额度耗尽被当限流）已在真实文档、真实连接、真实请求下复现并修复；同一文档换可用模型画像
立即成功。**按 §6 的判定标准，#49 可以关闭**；若之后仍能拿到 301–400 那一段原件，
在其任务副本上重跑阶段 1.2 与本记录对表即可，属复核而非补证。

## 3. 方案 §6 清单现状

| 清单项 | 状态 |
| --- | --- |
| 四类入口在人工修改/接受/已审校译文上不自动覆盖 | 已通过（#92 保护语义 + 批量默认跳过） |
| 采用后来源、文本变更、人工决定与版本可追溯 | 已通过（#29/#93/#92/#94） |
| UID 错配、过期、并发、重复点击、worker 锁安全拒绝 | 已通过（#94 集成套件） |
| 失败/重启/journal replay/compaction/撤销后状态一致，旧任务可用 | 已通过（#94） |
| 只失效受影响证据，人工历史与交付快照保留，无错误 TM 提升 | 已通过（#94；并修掉无审校事件时不撤销 TM 的缺陷） |
| #49 真实有效画像证据 | **已取证并闭环**（§2.1 合成等价件 + §2.2 同一本书真实分段，根因=按月额度用尽被当限流，修复已合并 PR `codex/m5-profiling-quota`；换模型结果翻转直接回答 issue 之问） |
| #83 Windows/macOS 原生验证 | **Windows 已完成**（§1.4，等价 49 页任务 + 截图/日志）；两个真实缺陷已修（页码框、重复点击）；**macOS 未完成**，无设备 |
| 既有门禁不被新入口绕过 | 已通过（回归全绿，i18n 1393/1393 × 5） |
| 记录额外成本/延迟及未完成验证 | 即本文件 |
