# 翻译审视工作区（翻译页交互契约）

本文记录翻译工作台当前的信息架构与交互约定。它不是实现说明，而是**约束**：
以后再改这一页时，先确认没有违反下面的约定。旧版 Agent 快捷改写说明已由下文的
Translation Decision Layer 取代。

> **Qt 桌面端（2026-10-11）**：同一约定。当前行在网格里展开，原文整段换行，译文格就是编辑框（只有这一个，跟着当前行走，见 `folith/qt/segment_editor.py`）；改过才出现「● 未保存 · 保存」，⌘S 保存、⌘↩ 保存并进入下一段。右侧是**操作栏**：作用于表格里选中的行（审校、重译、复制原文、恢复原译、排除、段落结构、查看原位置），下面是段落事实与检查项，不重复原文与译文。选多行时只留批量审校。


## 为什么会有这一版

改版前，同一段译文在屏幕上出现两次——中央网格的"当前译文"列（只读）和右栏的
编辑器（可写 + 「保存修改」）。用户看到两处相同内容时的第一个问题是
"我到底应该在哪里改？"。同时页面在 900px 高的窗口里把正文推到了折叠线以下：
Agent 发现、元信息、工具栏各自占一行，正文一行都看不见。

三条结论驱动了这次收敛：

1. **一个内容只能有一个可写位置**。展示可以多处，编辑只能一处。
2. **辅助栏回答"现在什么状态、系统发现了什么、我能做什么"**，不重复正文。
3. **Agent 的存在感来自主动说明，不来自按钮数量**。系统要先说出它发现了什么。

## 布局契约

```text
┌──────────┬─────────────────────────────────────┬──────────────────────┐
│ 项目导航  │ 顶栏：书名 + 交付判断 + 进度          │ 翻译审视 Inspector    │
│          ├─────────────────────────────────────┤                      │
│ 概览      │ 翻译 · 四维进度（Trans/Term/Rev/Iss） │ 段落 #N   ← →        │
│ 翻译      │ 搜索 · 筛选 · 更多                    │ 段落事实              │
│ 术语      │ QA 发现（一行）+ 定位 #N              │ QA 问题（如有）       │
│ 审校      │ ─────────────────────────────────── │ 翻译痕迹（0–3）       │
│ 交付      │ 段 │ 原文        │ 译文（可直接编辑） │ 术语 / 上下文         │
│          │ 1  │ …           │ [text_area]  保存  │ 询问 Agent…           │
│          │ 2  │ …           │ [text_area]  保存  │                       │
└──────────┴─────────────────────────────────────┴──────────────────────┘
```

| 区域 | 负责 | 明确不负责 |
| --- | --- | --- |
| 中央网格 | 阅读原文、编辑译文、选段、行内保存 | 不放 AI 动作、不放术语详情、不常驻保存按钮 |
| 右栏 Inspector | 段落事实、QA 问题、翻译痕迹、术语与上下文 | **不重复原文与译文、不放编辑器** |
| Agent issue bar | 全文级观察的最高优先级一条 + 计数 + 展开 | 不做成卡片，不铺开多条，不占超过 52px |
| 顶栏 | 这是哪个文档 + 能不能交付 | 不堆版本号/变更/覆盖率，**不放 Translation 进度条** |

### 布局预算（1536×960 实测）

正文起点必须留在首屏上半部分。当前实测：

| 元素 | 高度 |
| --- | --- |
| 顶栏（含"任务列表/主页"） | 底部落在 121px |
| 翻译标题 + 四维进度 | ~60px |
| 搜索/筛选工具栏 | ~56px |
| Agent issue bar | **44px**（硬上限 52px） |
| 正文起点 | **359px**（占 960px 视口的 37%） |

首屏可见正文 3 行。改动这一页时请保持这个量级：任何新元素如果超过约 60px，
先问它能不能并进已有的行。

## 交互约定

### 中央网格是唯一编辑区

- 每行译文列是 `st.text_area`，包在 `st.form(enter_to_submit=False)` 里。
  **打字不触发 rerun**，只有点「保存」或按 ⌘/Ctrl+Enter 才提交。这是为了长文档：
 40 行的可视窗口上每敲一个字都重跑整页是不可接受的。
- **保存操作按需浮现**：正常状态不渲染保存按钮，内容变化后才出现
  `● 未保存　保存`。20 段同屏如果每行都挂一个"保存"，就是 20 个重复按钮。
- **行内只报异常**：`● 未保存 / 需处理 / 已修改 / 待审校`。没有异常就什么都不显示
  ——"已翻译"是正常状态，由 Inspector 的"段落事实"负责，不该每行宣布一次。
- **"需处理"只对应 `severity == "blocking"`**。actionable 级发现（如"译文偏短"）
  是建议：把它也标红，82 段文档实测会出现 26 个红字，等于把建议喊成错误。
- "有未保存改动"来自会话内的基线比较（`cat_baseline_{editor_key}`），
  而不是"`st.session_state` 里存在 key"——后者在 widget 交互后就会写入，
  会把"没改过"误报成"改过"。
- 保存后丢弃草稿、重建基线，行内状态回到无异常。
- 译文框无边框、随内容增高（`field-sizing:content`）。同时必须显式定宽：
  `textarea` 会保留首次渲染的固有宽度，列变窄后文字横向溢出，看起来像"没对齐"。
- 段号刻意做小（18px，约原来的 72%）：它是辅助定位，不该比译文更抢眼；
  当前段落的识别主要交给 3px 蓝色指示条 + 极淡蓝底。

### 段落跳转：唯一入口（曾经的 blocker）

**症状**：点击问题里的 `#2` → 正文变白 → 一直加载。

**根因不是滚动**，是跳转链路没有收敛。每个入口各写各的
（`st.session_state[...] = ...` + `st.rerun()`），而锚点用 `st.pills` 实现——
pills 的选中值在 rerun 之后**依然保留**，同一个值被反复返回、反复触发
`st.rerun()`，形成 rerun loop。

四条约定（`tests/segment_navigation_test.py` 逐条守住）：

1. **唯一入口 `_navigate_to_segment(job_id, state, index, *, reveal=True)`**。
   Agent 锚点、Inspector 的"定位"、上一段/下一段、网格选段全部走它。
   它只设置状态：
   ```python
   selected_segment_id = segment_id
   pending_scroll_segment_id = index
   ```
2. **跳转挂在 `on_click` 回调上，不在脚本里手动 `st.rerun()`**。
   回调在 rerun **之前**执行，scroll intent 因此能在渲染前就位；
   `if button(): ...; st.rerun()` 会让 widget 状态反复重放。
3. **scroll intent 消费一次就清空**。`_consume_pending_scroll()` 取出即 pop，
   绝不在 session state 里残留 `pending_scroll_segment_id`——否则之后每次 rerun
   都会再跳一次。
4. **锚点必须是按钮，不能用 `st.pills`**。pills 的选中值会在 rerun 后重放，
   这正是 loop 的来源。

滚动本身：行容器上挂 `data-segment="{index}"`（标记 span，`height:0`），
在网格渲染完成后用 `st.html(unsafe_allow_javascript=True)` 注入一次性脚本。

**不要用裸 `scrollIntoView({block:'center'})`**：标记 span 是 `height:0` 的，
浏览器按它自己的盒子算居中，实测目标行中心落在 718px、视口中心 480px——偏 240px；
而且滚完还会因为长段落上方的布局再稳定一次而继续漂。要显式算差值：

```javascript
var row = marker.closest('[class*="st-key-cat_row_"]') || marker;
var rect = row.getBoundingClientRect();
var delta = (rect.top + rect.height / 2) - window.innerHeight / 2;
document.querySelector('section[data-testid="stMain"]')
        .scrollBy({top: delta, behavior: 'smooth'});
```

滚动容器是 `section[data-testid="stMain"]`，不是 `window`
（`documentElement.scrollHeight == clientHeight`，页面本身不滚）。
300ms 后再纠一次以吸收布局稳定带来的漂移。实测 `rowCentre=480 / vhCentre=480`。

**不自动 focus textarea**——用户是去"看"那一段，不是去"编辑"它，
页面不该突然跳进编辑态。

### 目标被筛选挡住时：定位优先

渲染集合由 status 筛选 + 关键词搜索共同决定。如果目标不在集合里，
`querySelector` 根本找不到它——那不是"滚动失败"，是目标不存在。

`_reveal_segment()` 因此**先放宽筛选再渲染**：

| 挡住目标的东西 | 处理 |
| --- | --- |
| status 筛选（待审/已审校） | 切回「全部」 |
| 「有审校问题」勾选 | 取消勾选 |
| 搜索关键词 | 清空搜索 |

搜索词**不恢复**：恢复会让目标在下一次 rerun 又消失，用户刚跳过去就看见它没了。
改动一律通过 `_render_nav_notice()` 明确告知（`↪ 已清除搜索以显示第 15 段`）——
静默改掉用户的筛选状态比多一行提示糟糕得多。

### 问题抽屉，不是浮层

「查看全部」把**右栏 Inspector** 切到 Issues 模式（`issues_panel_open`），
不再弹中央 popover：

- 浮层会盖住正在工作的正文，而问题列表和"跳到正文处理问题"是同一件事的两半，
  把它盖在工作台上自相矛盾。
- 面板顶部是分级计数（`0 必须处理 · 8 建议检查 · 0 参考`），下面逐条列出问题，
  每条带 `#N` 锚点和命中段列表。
- 点锚点 → 关闭抽屉 + 选中该段 + 一次性滚动 + 回到该段的 Inspector。
- 之后可以自然演进成"发现 → 定位 → Agent 建议 → 应用候选"。

**问题条与 scroll intent 在任何 early return 之前渲染。** 早先它们放在
「没有符合筛选条件的段落」之后，于是筛选命中 0 段时 issue bar 也一起消失——
而那恰恰是最需要它的时候（"有问题但当前筛选看不到"正是要点「查看全部」的场景）。

### 右栏翻译审视呈现决策证据，不生成候选译文

翻译生成和翻译决策审视是两种不同工作：首轮机器翻译负责给出译文；Inspector
帮助译者看见值得确认的信息变化、语义取舍和表达转换。Decision 是中性观察，
不等价于 QA 错误，也不自动给出替代译文或「应用修改」入口。

- 主区标题为「翻译审视」，本段 findings 称为「翻译痕迹」。
- 无值得注意的变化时显示轻量空状态：「本段未发现需要特别审视的翻译决策」。
- 有痕迹时最多呈现 3 条紧凑行；每条说明 Source 与 Target 的变化，证据可展开。
- 点击 source/target evidence 临时定位并高亮相应文本，不改内容、不触发重译。
- QA 问题保持独立分区。若确定性的 QA 已覆盖相同遗漏，Decision 检测会去重。
- 高兴趣段落可由译者显式请求一次结构化深审；选段本身不发起模型调用。
  底部「询问 Agent…」是自由问答入口，不提供预置改写动作或自动写回。

### 段落事实必须诚实

| 事实 | 来源 | 缺失时的写法 |
| --- | --- | --- |
| 状态 | 工作台投影 `segment_status` | — |
| 人工修改 | `human_actions` 计数，退化为 `human_edited` | 0 次时**整行不渲染** |
| 术语 | 本段原文命中的项目术语数 | 0 条时**整行不渲染** |
| 审校 | 审校是否启用 + 本段是否有发现 | 未启用时整行不渲染 |
| AI 置信度 | 审校发现里的 confidence | 没有审校时**整行不渲染**（不编造、不显示 `—`） |
| 章节 | `sections` 的段号区间 | 找不到时不渲染 |

渲染成轻量 definition list 而不是 2×3 方格：方格像 dashboard，且空字段会占掉
可观面积。空字段消失后翻译审视自然上移。

### 进度分维度

`translation_planner.workspace_progress()` 返回四个维度 + 一句判断。
单一"82 / 82 已翻译"会被读成"完成了"，而交付门禁可能同时说"暂不满足交付条件"。

- 维度标签是 `翻译 / 术语已确认 / 审校 / 发现`。**"术语已确认"不能说成"术语"**：
  它指"38 个术语里确认了几个"，与 Inspector 里"当前段命中 5 条术语"不是一回事，
  写成同一个词会让人以为是覆盖率。
- 各维度给 `done / total`；不适用时写 `不适用` 而不是 `0 / 0`。
- 四个维度只在正文上方出现一次。**顶栏不再放 Translation 进度条**——那会在视觉上
  继续宣称"翻译完成度是主要完成指标"。
- `workspace_progress()["verdict"]` 只是 `folith/task_overview.py` 的转发：
  交付结论由 `derive_task_overview_state(task, facts)` 单点推导，顶栏 / 侧栏 /
  Hero / pipeline / Inspector 必须显示同一份 canonical 状态与同一套颜色。
  词汇固定为 `DRAFT / TRANSLATING / NEEDS_ATTENTION /
  READY_FOR_DELIVERY_PREP / PREPARING_DELIVERY / DELIVERY_READY / DELIVERED`；
  "可以准备交付"不等于 "Ready for delivery"（后者只属于 `DELIVERY_READY`）。

### 侧栏状态语言

侧栏顶部挂一枚 canonical 状态 chip（与顶栏 pill、Hero 同文案同色），下面每个导航项
只描述**那一页**的状态，不再冒充整个任务的状态。

| canonical tone | 颜色 | 含义 |
| --- | --- | --- |
| `green` | 绿 | 已完成 / 可以正式交付 |
| `blue` | 蓝 | 正在进行（翻译、准备交付） |
| `amber` | 琥珀 | 建议或需要注意，但**不阻断**交付 |
| `red` | 红 | **存在 blocking 问题 / 交付门禁未通过** |
| `gray` | 灰 | 尚未开始 / pending / skipped / 未启用 |

导航项自己的 tone 仍是 `done` / `pending` / `stale` / `attention` / `muted`，
由 `SURFACE_TONES` 从 canonical tone 映射出来；两套状态语言不允许各写各的颜色。

徽标文案必须自解释：`待处理 1 / 待确认 1 / 待完成 1`，不用含糊的 `1 项`。

### Agent 发现的措辞与锚点

`translation_planner.plan_findings()` 的每条发现都带 0-based `segments`。
UI 里显示 1-based（`第 N 段` / `#N`）。发现的排序是
severity → 最小段号 → id，保证同样的任务每次渲染顺序一致。

## 样式约定

### Surface hierarchy（这一页最容易做错的地方）

**减少 Border hierarchy ≠ 消灭 Surface hierarchy。** 这一页来回走过两次极端：
先是"框套框"（整行卡片 → 原文卡片 → 译文卡片），后来变成"所有文字直接铺在
页面背景上"——后者读起来像双栏 PDF 阅读器，不像翻译工作台：用户会阅读，
但感知不到"我正在操作哪个 segment"。

目标层级（**只有一层容器**，不是嵌套三层）：

```text
页面背景        --tp-canvas        #f6f7f9
工作区 surface  .tp-cat-grid       #ffffff + 1px hairline + radius 10 + shadow-sm
Segment row     .is-active         #eef4ff  /  默认 #ffffff
                                    行与行之间 1px --tp-hairline
可编辑译文      textAreaRoot       #f7f9fc + 1px #e4e7ec（hover 加深边框）
focus / active  →                  白底 + 蓝色 focus ring
```

具体约定：

- **每个段落对是一个视觉工作单元**。行有 surface、有分隔线、hover 变色。
  但**不要**再给原文和译文各自套卡片——一个 Segment 只有一层容器。
- **原文是纯阅读态，译文必须明显可编辑**。译文平时就有淡灰蓝底 + 极淡描边
  （editable affordance），hover 描边加深，focus 才出现蓝环。不要回到"大白框"，
  但也不能让译文看起来像普通文本。
- **Active 段落靠整行 tint + 3px 左侧蓝条**，不能只靠段号。段号只是辅助定位
  （18px，比译文弱）。
- 分隔线只在必要处出现（栏间、表头、行间、事实条目之间）。

### 两个试过但不可靠的"自动长高"方案

译文框高度最终用**显式 112px（5 行）+ 框内滚动**。下面两条路都验证失败，
不要再试：

| 方案 | 结果 |
| --- | --- |
| `field-sizing:content` | 在这个 Streamlit 版本的包装层里不生效：长段落 `scrollHeight=375px`，元素高度仍是 46px |
| `height:auto` + `overflow:hidden` | Streamlit 自己的 auto-resize 会覆盖，把高度钉在 86px，剩余 289px 内容仍被藏起来 |

112px 而不是 132px 是因为后者会让首屏少掉一整段。框内滚动条本身也是一种
"这里可以编辑 / 还有内容"的 affordance。

### 其余约定

- **翻译痕迹是轻量证据行**：小型语义图标、克制标签、展开证据与浅分隔线；
  不做大型卡片墙，也不把决策伪装成错误告警。
- **Inspector 是一个 panel surface**（白底 + hairline + shadow），内部：
  段落事实与相关术语各是一块连续的浅底 + 行分隔，**不给每个事实做小方格**
  （那是 dashboard 的做法）。
- 需要点击但次要的东西（Agent 锚点、行内保存、顶栏返回）默认无边框，
  hover 才浮出。

## 两个 Streamlit 陷阱（踩过，别再踩）

1. **带 `help=` 的按钮不再是 `.stButton` 的直接子元素**。Streamlit 会插入
   `<span data-testid="stTooltipHoverTarget">`，于是所有 `.stButton > button`
   规则对这类按钮静默失效（实测 10 个可见按钮只有 5 个命中）。样式表里已有
   归一化规则（把包装层拉成 100% 块级，见"Tooltip 包装层归一化"一节），
   新增 `.stButton > button` 规则时不需要再各自处理。
2. **`help` tooltip 点击后不会消失**。实测点击导航按钮后鼠标移到别处、
   等 5.5 秒，`stTooltipContent` 仍在 DOM 里并盖在侧栏上。
   所以侧栏不使用 `help`；必须传达的状态直接写进标签。

## 回归测试

| 文件 | 守住什么 |
| --- | --- |
| `tests/segment_navigation_test.py` | 跳转状态机：选中+pending_scroll 只消费一次、连续跳转收敛不 loop、尾部段落可达、被筛选挡住时先放宽再渲染、active 整行标记、问题抽屉替代浮层、刷新不残留 |
| `tests/agent_inspector_ui_test.py` | 中央网格是唯一编辑器；Inspector 事实与翻译审视；问题条含抽屉入口；四维进度；顶栏不重复标语；行内保存落盘与基线重建 |
| `tests/translation_decisions_test.py` | 简单句静默；遗漏/压缩、增补/强度、显化、文化处理、隐喻、歧义；QA 去重；结构化证据与缓存校验 |
| `tests/translation_planner_test.py` | 六类发现规则、严重度排序、limit 截断、畸形状态不抛异常、progress 各维度与 verdict（canonical 转发） |
| `tests/task_overview_state_test.py` | canonical 状态矩阵：生命周期 × tone、optional 阶段 skipped/blocked、blocking 与建议的区别、DELIVERY_READY 前置条件、只读性与畸形状态 |
| `tests/task_overview_ui_test.py` | 概览页验收：skipped 不画 ✓、未启用功能无强 CTA、blocking 时无交付 CTA、建议不写成 blocking、顶栏/侧栏/Hero 同色同文案 |
| `tests/translation_workbench_ui_test.py` | 选段、筛选、行内保存、长段落不被截断 |
| `tests/phase3_workbench_ui_test.py` | 审校动作与翻译页之间的跳转、编辑后的复审文案 |

## 尚未做（有意留白）

- 网格使用 40 行的可视窗口，靠搜索/筛选收敛；虚拟滚动没有做。
- 译文框的自动增高依赖 `field-sizing:content`（Chrome 123+）。
  旧浏览器回落到固定高度 + 框内滚动，功能不受影响。
- Decision 检测先使用当前段的窄范围确定性线索；更深的模型审视需要显式请求，
  不在选段或每次 Inspector 渲染时自动执行。
- 深审模型的实际输出质量仍需在配置 AI 的真实运行环境中做人工验收；本轮单测
  验证的是 schema/span 校验与缓存行为，不代表已评估模型判断准确率。

## 验收状态

| 范围 | 状态 |
| --- | --- |
| UI contract（信息架构、状态语义、布局预算） | **PASS** |
| Translation Decision Layer：确定性规则、去噪与证据交互 | **PASS — automated scenarios and Inspector coverage** |
| 深审模型判断质量 | **OPEN — requires configured-model review on a curated set** |

本轮覆盖真实易懂的决策类型和负例，但确定性检测刻意保持窄范围以控制噪声。
后续评测应使用包含长句、术语密集、跨段指代、文化信息和低质量源文的固定片段集，
人工判断每条 finding 是否有证据、是否中立、是否值得译者花时间查看。
