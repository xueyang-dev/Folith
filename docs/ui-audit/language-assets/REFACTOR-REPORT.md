# 「术语与翻译记忆」模块重构报告

> 页面：术语与翻译记忆（入口 `app_view == "library"`，侧栏「术语与翻译记忆」）
> 副标题：维护项目语言资产，并审核 Agent 发现的候选内容

---

## 0. 先更正一个前提

需求里提到「主要 React / TSX 组件」。**Folith 里没有 React/TSX。**
仓库里不存在 `.tsx` / `.ts` 应用代码，整个 UI 是 **Streamlit**（Python）：
`app.py`（~12.8k 行 Python + 内联 CSS）、`core.py`、`gui.py` 和 `folith/` 包。

所以本次重构的落点是 Streamlit 的容器/组件层与 CSS，而不是 React 组件树。
下面所有「组件」指的是 Streamlit 渲染函数。

### 0.1 现状检查（修改前确认）

| 检查项 | 实际实现 | 本次处理 |
|---|---|---|
| 页面入口 / 路由 | 侧栏把 `app_view` 设为 `library`，主入口调用 `_render_language_assets_workspace(saved_jobs)`；Tab 状态由 `?view=library&tab=terms\|tm\|review` 保持 | 保留现有应用路由，补齐深链恢复和 Tab/选中项 URL 同步 |
| 术语来源 | `core.list_jobs()` 返回的任务状态：`glossary` → `glossary_frozen` → 历史 `auto_terms`；另投影 `core.list_projects()` 中已持久化的项目 `glossary` | 纯视图模型聚合，项目条目不再被误标成文档级；同一术语的任务 occurrence 不重复计数 |
| 翻译记忆来源 | `core.load_tm(project_id)`，只展示 `reviewed=true` 的 `target / reviewed / updated_at` 记录 | 不补造来源文档、语言对、使用次数或 fuzzy match |
| 候选来源 | 任务状态 `knowledge_candidates`，通过 `knowledge.candidate_context` 得到候选类型、译法、置信度、occurrences、上下文和冲突 | Compact Review Inbox 只投影真实字段；“新术语”由候选与当前任务术语表的实际关系派生 |
| 审核动作 | 既有 `core.review_knowledge_candidate(job_id, candidate_id, decision)` 支持 `project_term / task_only / rejected` | 保留原 API；项目术语路径在任务确认后显式调用 `core.promote_job_to_project` 的 Memory gate |
| 后端能力边界 | 项目记忆已存在并可持久化；没有独立全局术语库存储；TM 没有 fuzzy/source/language-pair 字段 | 项目记忆进入术语库；全局入口和 TM 不可证明的筛选能力保持 disabled/TODO |
| schema | JSON 状态与项目文件已有字段足够完成本次 IA 重构 | 无数据库 schema 变更、无破坏性既有 API 变更 |

---

## 1. Before / After 信息架构变化

### Before —— 一个超长页面

```
术语与翻译记忆
├─ 大段说明卡片（术语库是什么 / 翻译记忆是什么 / 作用域说明）
├─ 术语库（大卡片，一条一张）
├─ 翻译记忆（大卡片）
├─ 待审核候选（318 条，一条一张大卡片：
│    完整原文 + 完整译文 + 解释 + 三个等宽大按钮）
└─ 作用域说明
```

问题：没有一级分类；说明文字占据首屏；318 条候选每条都是一张大卡片，
滚到底几乎不可能；三个并列按钮（加入项目术语 / 仅此次采用 / 拒绝）
把「接受」和「存到哪」两个不同层级的决策揉在一起。

### After —— 三 Tab 语言资产工作区

```
术语与翻译记忆                      维护项目语言资产，并审核 Agent 发现的候选内容
┌──────────────────────────────────────────────────────────────────┐
│ 术语 72 │ 翻译记忆 0 │ 待审核 318 │ 冲突 0        ← 摘要条（真实数字）│
├──────────────────────────────────────────────────────────────────┤
│ [术语库]  [翻译记忆]  [待审核 318]              ← 一级 Tab，URL 可保持 │
└──────────────────────────────────────────────────────────────────┘
        ↓ 主区 65%                        ↓ Inspector 35%（sticky）
```

| 维度 | Before | After |
|---|---|---|
| 一级结构 | 无（单页堆叠） | 3 个 Tab：术语库 / 翻译记忆 / 待审核 |
| 侧栏入口文案 | 术语与记忆 | **术语与翻译记忆**（与页面标题一致） |
| 首屏 | 大段说明卡片 | 摘要条（4 个真实数字） |
| 列表形态 | 大卡片 | 密集表格行 |
| 候选行高 | ~200px+ | **56–76px**（两行） |
| 候选默认信息 | 全文 + 解释 + 3 按钮 | `☐ source → target` / `出现 N 次 · 置信度 X.XX` / `[接受] [⋯]` |
| 详情 | 无处可去 | 右侧 sticky Inspector |
| 决策模型 | 3 个并列按钮 | 两阶段：`[接受][拒绝]` → `保存到` |
| 批量 | 无 | 多选 + 上下文操作栏 |
| 分组 | 无 | 按来源文档分组，可折叠 |
| URL | 无状态 | `?tab=terms\|tm\|review` |

---

## 2. 修改文件列表

| 文件 | 类型 | 说明 |
|---|---|---|
| `folith/language_assets.py` | **新增** 635 行 | 纯只读视图模型层，不 import Streamlit、不写盘、不发明字段 |
| `app.py` | 修改 | 新增 CSS 段 + `Language Assets Workspace` 段（约 L3903–4953）；删除两个旧渲染函数；新增 `_la_jump_to_segment` 深链 |
| `core.py` | 修改（**纯增量**） | 新增 `update_glossary_entry` / `delete_glossary_entry` / `add_glossary_entry` 与 `_glossary_mutation_note` |
| `tests/language_assets_workspace_test.py` | **新增** 874 行 / **39 条** | 视图模型 + 三 Tab + Inspector + 决策 + 批量 + 空/错态 + 契约 + 出现位置深链（3 条） |
| `tests/context_knowledge_ui_test.py` | 修改 | 两条用例改为新 IA（`la-summary`、`la_quick_*`、`待审核 N 条`），侧栏标签改「术语与翻译记忆」 |
| `tests/project_memory_test.py` | 修改 | `test_ui_library_shows_per_project_memory` 先切到「翻译记忆」Tab 再读 metric |
| `docs/project-memory.md` | 修改 | 两处页面名引用同步为「术语与翻译记忆」 |
| `docs/ui-audit/language-assets/*.png` | **新增** 10 张 | 实际运行截图 |
| `docs/ui-audit/language-assets/REFACTOR-REPORT.md` | **新增** | 本报告 |

> 注：`git diff --stat` 显示的 `app.py` / `core.py` 变更量包含了本次会话之前就存在的
> 未提交改动，不能用来衡量本次重构规模；上表按实际改动内容列出。

---

## 3. 新组件结构

### 3.1 视图模型层 `folith/language_assets.py`

纯函数，可脱离 Streamlit 单测：

```
标签          kind_label / status_label / behavior_label / scope_label
投影          build_term_rows + build_project_term_rows → group_terms
              build_tm_rows
              build_candidate_rows
              language_asset_summary
定位          section_index / segment_reference   （"Chapter 5 · #2"，1-based）
过滤          filter_terms / filter_tm / filter_candidates
文案          confidence_text / usage_text        （"出现 N 次" / "观察到 N 次" / "出现次数未知"）
分组          group_candidates_by_document / document_options / domain_options /
              target_language_options
常量          TERM_STATUS_LABELS / SCOPE_FILTERS / CONFIDENCE_FILTERS / KIND_FILTERS
              HIGH_CONFIDENCE_THRESHOLD = 0.70 / TERM_TABS / VALID_TABS
```

行 id 约定：`term::{source}::{preferred}`、`tm::{index}`、`cand::{job_id}::{candidate_id}`。

### 3.2 `app.py` 工作区（约 70 个函数，按职责分组）

```
入口          _render_language_assets_workspace(saved_jobs)

状态/闪回     _la_flash  _la_render_flash  _la_select  _la_selected_row_id
URL 同步      _la_sync_tab  _la_publish_query
多选          _la_selection  _la_checkbox_key  _la_toggle_selection
              _la_clear_selection  _la_forget_row
键/分片       _la_row_slug  _la_key_fragment  _la_generation
决策执行      _la_apply_decision  _la_apply_bulk  _la_execute_pending   ← 两阶段
分页          _la_group_limit  _la_bump_group_limit  _la_apply_limit
小部件        _la_option_value  _la_option_labels  _la_guard_option
              _la_status_chip  _la_summary_html  _la_empty
行渲染        _la_row_container  _la_term_row  _la_tm_row  _la_candidate_row
Inspector     _la_inspector_hint  _la_kv  _la_term_inspector
              _la_tm_inspector  _la_candidate_inspector  _la_render_inspector
Tab           _la_terms_tab  _la_tm_tab  _la_review_tab
弹窗          _la_new_term_dialog（@st.dialog("新建术语")）
```

### 3.3 `core.py` 增量能力

```python
update_glossary_entry(job_id, entry_id, *, preferred, domain, status, note, actor)
    → (state, ok, message)
delete_glossary_entry(job_id, entry_id, actor)
    → (state, ok, message)
add_glossary_entry(job_id, source, target, *, domain, scope, status, actor)
    → (state, ok, message, entry_id)      # 注意是 4 元组
```

三个函数都**故意不走** `save_glossary_draft`——那会把 job 的 `stage` 打回
`TERMS_PREPARED`。它们改为直接改 `state["glossary"]` 后调用 `freeze_glossary()`，
因此：新版本号照常生成、`GLOSSARY_FROZEN` 不变、`_apply_glossary_staleness`
的失效标记照常生效。既有 freeze 不变式没有被绕过。

---

## 4. 被删除 / 废弃的旧组件

**已删除**（`app.py` 中确认 0 处运行时引用）：

- `_render_terminology_version` —— 旧「术语库」大卡片渲染
- `_render_knowledge_library` —— 旧「待审核候选」大卡片渲染

**刻意保留、未改动**（Phase 15 要求不破坏既有功能）：

- 任务工作区的术语表面：`_render_workspace_terms`、`_render_workspace_terms_context`、
  `_translation_terms_for_pair`、`_segment_term_hits`、`_glossary_dataframe`、
  `_df_to_entries`、`_humanize_glossary_editor`、`_glossary_status_chips`
- 术语准备与审核面板（`app.py` L12531 起的「刷新/重启后自动恢复」段）
- 翻译记忆写入链路（独立审校通过后自动落盘）与「翻译记忆维护」清空流程
  （后者搬到了「翻译记忆」Tab 的 expander 里，行为不变）

---

## 5. 完全实现的能力

| 需求 | 状态 |
|---|---|
| 页面名/副标题、去掉说明卡片、摘要条 | ✅ 4 个真实数字：术语 72 / 翻译记忆 0 / 待审核 318 / 冲突 0 |
| 三 Tab，不整页重载 | ✅ `st.segmented_control` |
| URL 状态 `?tab=` | ✅ 带 `la_published_tab` 防回写覆盖 |
| 术语库密集表 + 工具栏 | ✅ 搜索 / 作用域 / 分类 / 状态 / 目标语言 / + 新建术语；目标语言来自任务持久化字段 |
| 术语行字段 | ✅ 术语 / 推荐译法 / 分类 / 作用域 / 使用次数 / 状态 / 操作 |
| 右侧 Inspector（非新页面） | ✅ sticky，术语 / TM / 候选三套；主区 68% / Inspector 32%（实测 1440px：list 643px、inspector 285px） |
| 窄屏 Inspector 降级 | ✅ ≤1100px 断点后 Inspector 从右侧栏改为在列表下方**整宽堆叠**（实测 960px：两者均 left=268 / width=660，Inspector top=4768） |
| 术语 Inspector 字段 | ✅ 原术语、推荐译法、分类、语言方向、作用域、状态、行为、使用次数、来源任务、来源/证据、出现任务；编辑 / 删除 / 提升为全局术语 |
| 新建术语 | ✅ `@st.dialog` 弹窗 → `core.add_glossary_entry`，重名有明确拒绝文案 |
| 翻译记忆 Tab | ✅ 段对列表（原文/译文/状态/操作）+ Inspector |
| 术语 vs 翻译记忆区分 | ✅ 术语 = source term → target term；TM = source segment → target segment，两套行模型与两套 Inspector |
| 紧凑审核收件箱 | ✅ 两行 56–76px，默认只显示 `☐ source → target` + `出现 N 次 · 置信度 X.XX` + `[接受] [⋯]` |
| 多选 + 键盘可用 | ✅ `st.checkbox` 原生可聚焦；选中行有高亮 surface（CSS `:has()`） |
| 批量上下文操作栏 | ✅ 已选择 N 项 / 接受并加入项目术语 / 仅此次采用 / 拒绝 / 取消选择；不在每行重复按钮 |
| 选择全部高置信度 | ✅ 只勾选，不自动接受 |
| 两阶段决策 | ✅ `[接受][拒绝]` → `保存到`：`● 项目术语库 / ○ 不保存，仅此次采用`，主按钮 `[接受并保存]`，选「不保存」变 `[仅此次采用]` |
| Quick Accept 默认显式 | ✅ 列表 `接受` 按钮 help + Inspector 底部 caption 均写明「默认保存到：项目术语库」，并给出 `更改默认`（disabled + 原因） |
| 冲突拦截 | ✅ 与现有项目术语冲突时拒绝接受并给出冲突说明 |
| 来源文档分组 + 折叠 | ✅ `st.expander`，搜索/筛选时自动平铺 |
| 分组内分页 | ✅ 每组 40 条 + 「显示更多（还剩 N 条）」 |
| 空状态 | ✅ 三套文案，均为平静的提示而非空表格 |
| Loading / 错误 | ✅ 动作只锁当前行（`处理中…`），批量锁批量栏；API 异常显示错误态 + 重试按钮，不白屏 |
| 审核后平滑移出 | ✅ 成功写入后候选从队列消失，无需整页刷新 |

---

## 5.2 高密度视觉的落地细节（含两个**静默** CSS 陷阱）

Phase 12 要求「紧凑行 / 克制的分隔线 / 浅边框 / hover 动作 / 不要巨型按钮」。
这些靠 CSS 实现，而 Streamlit 的 DOM 有两处会让规则**静默失效**——不报错、不警告，
样式就是不生效。两者都已实测定位并修好，记录在此以便后续不再踩。

### 陷阱 A：`.stButton > button` 对带 `help=` 的按钮永远不命中

Streamlit 1.63 给带 `help=` 的按钮插了三层包装：

```
div.st-emotion-cache-…  →  span[data-testid="stTooltipIcon"]
  →  span[data-testid="stTooltipHoverTarget"]  →  <button>
```

`.stButton` 仍是祖先但**不是直接父节点**，所以 `>` 断链。

实测证据（行内 `接受` 按钮的 computed style）：

| | 期望 | 实际（修前） |
|---|---|---|
| `border-width` | `0px` | `1px` |
| `font-size` | `13px` | `16px` |
| `background` | `transparent` | `rgb(255,255,255)` |

**修法**：所有按钮规则改成后代选择器 `.stButton button`。同一问题也存在于
`.stPopover > button`（popover 按钮不在 `.stPopover` 直接子层）。

> 这个坑代码库里**早就记录过**——`app.py` L558–568「Tooltip 包装层归一化」的注释写着
> *"所有 `.stButton > button` 规则对带 help 的按钮静默失效（实测 10 个可见按钮里只有 5 个命中）"*。
> 但那里只归一化了宽度，没有让选择器重新命中。本次把语言资产段的规则全部改成后代形式。

### 陷阱 B：`[class*="st-key-la_row_"]:last-child` 会把**所有**行分隔线清零

Streamlit 把每个元素包进 `div[data-testid="stLayoutWrapper"]`，
于是**每个行容器都是它父节点的 `:last-child`**。
用 `:last-child` 去收尾 → 实测 `border-bottom > 0` 的计数是 **0 / 72**。

**修法**：从列表容器那一层挑真正的最后一行：

```css
[class*="st-key-la_list"] > [data-testid="stLayoutWrapper"]:last-child
  [class*="st-key-la_row_"] { border-bottom:0; }
```

修后实测 **71 / 72** 行有 1px 分隔线（最后一行没有）。

### 尺寸收敛

| 元素 | 全局 `.stButton` 尺寸 | 收敛后 |
|---|---|---|
| 候选 / 术语 / TM 行高 | — | **57px**（需求 56–76px；修前只有 42px，`padding` 由 `1px 8px` → `8px`） |
| 批量操作条按钮 | 44px | 32px |
| 选择全部高置信度 | 44px | 32px |
| 显示更多 / 查看全部出现位置 | 44px | 32px + 虚线边框 |
| 定位到工作台 | 44px | 28px 文字按钮 |
| popover 菜单项 | 44px | 32px 左对齐无边框 |

`提升为全局术语` 这类缺失能力用 `disabled` + 显式灰化，让它看起来是「不可用」
而不是「能点但没反应」。

---

## 6. 因后端限制**未实现**的能力（UI 已留位，未伪造）

这些是后端**确实没有数据源**的，UI 上一律 `disabled` + TODO 说明或直接隐藏，
没有任何 mock 数据：

| 能力 | 后端事实 | UI 处理 |
|---|---|---|
| **全局术语库** | `core.review_knowledge_candidate` docstring 明确：*"There is intentionally no global glossary store."* | Inspector 里 `全局术语库` checkbox **disabled**，caption 说明原因；`提升为全局术语` disabled + tooltip；`更改默认` disabled |
| **项目级术语库** | 已存在项目记忆：`folith/project.py` 持久化项目 `glossary`，`core.promote_job_to_project` 是现有 Memory gate | 语言资产页读取真实项目记录并与任务术语合并；接受并加入项目术语会先更新任务冻结版本，再通过 Memory gate 写入项目。项目独有条目可查看，但因当前没有项目文件 CRUD API，直接编辑保持 disabled |
| **模糊匹配百分比** | TM 没有相似度数据 | **不显示**任何 match % |
| **TM 来源文档** | TM 记录只有 `target` / `reviewed` / `updated_at` | 「翻译记忆」工具栏 `来源` selectbox **disabled** + tooltip |
| **TM 语言对** | 没有持久化语言对（语言方向只在 `pipeline_config.target_lang`） | `语言对` selectbox **disabled** + tooltip |
| **TM 使用次数** | 记录里没有计数 | 列表只显示「状态：已确认」，不编造次数 |
| **TM 直接编辑** | 条目由审校流程写入 | 行内 `编辑` disabled + 原因说明 |

> 说明：原报告此表里还列过「候选跳转到工作台 segment」，经核实该能力**后端是具备的**，
> 已实现并移入第 5 节（见 §5.1）。这里保留记录，避免读者误以为它仍缺失。

---

## 7. 测试结果

### 测试命令与结果（最终代码）

```bash
python3 -m pytest -q
# 741 passed, 5 warnings in 208.39s

python3 -m pytest -q \
  tests/language_assets_workspace_test.py \
  tests/context_knowledge_ui_test.py \
  tests/project_memory_test.py
# 88 passed, 5 warnings in 42.90s
```

全仓库回归最终结果：**741 passed, 0 failed, 0 errors**（exit 0）。5 条告警均来自
底层依赖的 `SwigPy*` 类型弃用提示，与本次改动无关。

`language_assets_workspace_test.py` 当前 39 条用例覆盖：

- **纯投影**：跨任务聚合与真实计数、项目/全局/文档作用域、目标语言筛选、
  TM 不发明来源文档与匹配率（断言 key 集合）、TM 丢掉未确认条目、
  候选位置真实且去重、「出现次数未知」而不是 0、已决策候选离开队列、
  冲突来自既有术语、过滤器都由真实字段支撑、摘要只统计真实来源
- **Tab / 搜索 / 筛选**（6 条）：三 Tab 渲染与切换无异常、摘要真实计数、
  术语搜索+作用域、候选搜索+置信度、按来源文档分组与筛选时自动平铺、
  120 条候选分批渲染（40 → 显示更多 → 80）
- **Inspector**（1 条）：术语 / TM / 候选三套都能打开
- **两阶段决策**（5 条）：Quick Accept 落项目术语、两阶段接受落项目术语并更新列表、
  仅此次采用不写术语库、Inspector 拒绝后移出队列、冲突时拒绝接受
- **批量**（3 条）：批量接受与批量拒绝生效、取消选择收起操作栏、
  选择全部高置信度**不会**自动接受
- **空/错态**（3 条）：三 Tab 空状态、API 抛异常显示错误态而非白屏、
  筛选无结果显示平静空状态
- **契约**（3 条）：既有审核 API 契约未变、三个 glossary 增量函数保持 freeze 不变式、
  术语 Inspector 的编辑/删除走 freeze 流程

### schema / API change

- **无数据库 schema 变更。**
- **无破坏性 API 变更。** `core.py` 只新增 3 个函数 + 1 个私有 helper，
  没有修改任何既有函数签名或返回结构。
- 既有 `core.review_knowledge_candidate`、项目术语写入、仅此次采用、拒绝、
  来源文档关系、项目作用域、既有翻译记忆全部保持原行为——
  `test_existing_review_api_contract_is_unchanged` 专门守这条。

---

## 8. 实际 UI 状态（截图见 `docs/ui-audit/language-assets/`）

真实数据：20 个 job → 术语 **72** / 翻译记忆 **0** / 待审核 **318** / 冲突 **0**；
最大来源文档 215 条，其次 97 条、6 条。2026-09-14 在本地 Streamlit 运行态复核了
三 Tab、URL 深链、项目/文档作用域、目标语言筛选、候选 Inspector，以及从出现位置
定位回翻译工作台；页面没有依赖 mock 数据。

| 截图 | 内容 |
|---|---|
| `la-review.png` | 待审核 Tab：摘要条 + 三个 Tab + 工具栏（搜索 / 候选类型 / 来源文档 / 置信度 / 列表）+ 筛选 chips（全部 / 高置信度 / 有冲突 / 新术语）+ 按来源文档分组的 215 条；每行 `☐ drone → 无人机` + `出现 86 次 · 置信度 0.50` + `[接受] [⋯]` |
| `la-review-bulk.png` | 勾选 2 条后出现上下文操作栏：`已选择 2 项 / [接受并加入项目术语] [仅此次采用] [拒绝] [取消选择]`；选中行有高亮 surface，Inspector 保持钉住 |
| `la-review-inspector.png` | 候选 Inspector：建议译法 无人机 / 置信度 0.50 / 出现次数 86 / 类型 / 来源文档 / 首次出现 #10 + 上下文（原文段落）+ 出现位置 |
| `la-review-save-target.png` | 两阶段第二阶段：`保存到 ● 项目术语库 ○ 不保存，仅此次采用`、`☐ 全局术语库`（disabled + 原因）、主按钮 `接受并保存`、`取消`、Quick Accept 默认说明、`更改默认`（disabled）；出现位置 `#2 #3 #6 #8 #10 #11` + `查看全部出现位置（12）` |
| `la-terms.png` | 术语库 Tab：密集表，表头 术语 / 推荐译法 / 分类 / 作用域 / 使用次数 / 状态 / 操作，状态 chip（暂定 / 候选），`显示 72 / 72 条术语`；目标语言筛选已在最终运行态复核 |
| `la-terms-inspector.png` | 术语 Inspector：推荐译法 体积感知 / 分类 / 语言方向 →简体中文 / 作用域 本文档 / 状态 暂定 / 行为 翻译 / 使用次数 25 / 来源任务 2 个任务 / 来源证据（模型知识：自动抽取，未经人工核实）/ 出现任务 |
| `la-tm.png` | 翻译记忆 Tab：搜索 + `来源`（disabled）+ `语言对`（disabled）+ 项目选择 + 说明 caption；`未分类 = 0` 的真实 metric；空状态「完成并确认翻译后，翻译记忆会出现在这里。」 |
| `la-narrow.png` | 960px 窄屏：主列表整宽（不再被挤到 68%），Inspector 移到列表下方整宽堆叠 |
| `la-review-positions.png` | 候选 Inspector 的「出现位置」区：每个位置一行 `Chapter N · #M` + `[定位到工作台]`，底部 `查看全部出现位置（86）` |
| `la-jump-workbench.png` | 点击 `[定位到工作台]` 后的工作台：已切到该任务的 translation 区，目标段落被滚动到视口居中（目标下标 `1` → 视口中心命中 `[data-segment="1"]`，该段正文确实在讲无人机，与 `drone → 无人机` 候选一致） |

**验收标准**：打开页面第一屏就是 `术语 72 │ 翻译记忆 0 │ 待审核 318 │ 冲突 0`
和三个 Tab，不再有说明卡片和几百张大卡片；待审核列表以 ~60px 两行密集行呈现，
勾选即出现批量操作栏，点行即出右侧 Inspector。可以在几秒内理解
「管理已有术语 / 查看翻译记忆 / 高效审核 Agent 新发现的候选」。

**行高与分隔线的实测值**（用于确认「紧凑但可读」而不是「挤成一团」）：

| 指标 | 实测 |
|---|---|
| 候选行高 | 57px（需求区间 56–76px） |
| 有分隔线的行 | 71 / 72（最后一行无分隔线） |
| 主列表 : Inspector 宽度 | 1440px 下 643px : 285px ≈ 69% : 31%（需求 65–72% / 28–35%） |
| 窄屏（≤1100px） | 列表与 Inspector 均整宽（660px），Inspector 移到列表下方堆叠 |
