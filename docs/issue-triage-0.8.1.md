# Folith Issue 整理与 v0.8.1 Milestone 记录

> 更新于 2026-11-01（第二轮：按 comments 复核“是否已在旧版本修复”）。
> 里程碑为顺序/范围描述，不代表日期承诺。

## 一、v0.8.1 定位

**v0.8.1 · Intelligent QA Triage & Autonomous Quality Resolution** —— 0.8.0 之后第一个功能版本：把质量流程升级为「检测 → 诊断 → 决策 → 修复 → 验证 → 必要时人工升级」的可审计链路，降低 HIR 但不隐藏/自动放行真实问题（规划来源 #270）。先落地 0.8.0 试用发现的稳定化修复，再做 QA 分诊改造。

Milestone: https://github.com/xueyang-dev/Folith/milestone/20

## 二、第二轮复核：确认真已随 v0.8.0 修复并已关闭（4 项）

| # | 优先级 | 证据（已核实在 v0.8.0 代码/标签内） |
|---|---|---|
| #257 | P2 | PR #260：打开任务改为 `no_parse` + 共享后台 `outline_loader`，不再同步解析全文大纲 |
| #229 | P1 | PR #245：`core.py` 已 import `folith.script_leak`，`check_translation_batch` 发 `kind=script_leak` |
| #230 | P2 | `folith/document_understanding.py` 已写入「Ignore show-through」提示词规则 |
| #223 | P1 | PR #262：`request_control.call_cancellable` 以 0.25s 轮询取消探测，vision 请求走该通道 |

## 三、复核后**仍保留在 v0.8.1** 的重点（易被误判为“已修”）

- **#226（P0）未经 0.8.0 修复**：注释里写的 fix `86e3b18` 只在备份分支 `backup/issue-232-terminology-eeb0d48`；v0.8.0 的 `_render_segments` 只 `segment_list.blockSignals`（视图），**没有** `model().blockSignals` 也没有单次 `layoutChanged`，27 GB 内存问题的真正修复尚未合入。必须留 0.8.1。
- **#220（P1）部分修复**：per-model thinking、table pages、overlapping bands、resumed read 已随 0.8.0；但 body 仍有 12 个未勾选项（A3/B1/B5/C1/C2/C3/D2/D3/E1/E2/F1/F2）。
- **#233（P2）部分修复**：PR #246 已合入修好 manual exclusion；但 forward split/insert 仍未更新 `paragraph_ranges`，且 choke point/property test 未做。
- **#102 / #90**：代码（PR #209、#262）已合入，但明确要求原生 macOS/Windows + 人工验收，未完成。
- **#222 / #231**：未修复（Continue 不重读连接；vision 请求仍不进 `model_call` 用量）。
- **#258**：修复在未合并 PR #261。**#276 / #277 / #219 / #270**：未修复 / 规划 / 阻塞。

## 四、v0.8.1 当前收录（14 项）

| # | 优先级 | 标题 |
|---|---|---|
| #17 | P2 | [Experimental] Review and accept non-Chinese UI locales (en/es/ru) |
| #90 | P1 | [Review] Comprehensive upgrade of the review workflow (tracking) |
| #102 | P3 | [Review][P3] Polish review layout, status wording and accessibility on native clients |
| #191 | P1 | [Outline] Smart document outline: structure-aware navigation (tracking) |
| #219 | P2 | Support and validate vertical Japanese scanned books: column order, ruby and two-page spreads |
| #220 | P1 | Thinking-off trial: switch scope, vision failures, copy-through QA gap and related bugs |
| #222 | P1 | Changing the connection / API key while a task is paused does not take effect on Continue |
| #226 | P0 | Opening a translated task froze the UI and ballooned memory to 27 GB (full synchronous segment render) |
| #231 | P2 | Usage trace omits vision requests and loses failed attempts, so a task's cost cannot be computed |
| #233 | P2 | Segment structure edits desync the positional lists (spans, marks, paragraph ranges); add one choke point, invariants and a property test |
| #258 | P2 | Warn when an OCR-only or translation-only model is assigned to the vision or translation role |
| #270 | P1 | [Review] Intelligent QA Triage & Autonomous Quality Resolution (v0.8.1 planning) |
| #276 | P2 | [Bug][Windows] CAT 工作台中快捷键 Ctrl+Alt+P 无法切换/展开 PDF 原页面板 |
| #277 | P3 | [Review] 审校队列术语卡展示字段不全：缺少冻结译法、旧译法及受影响段数 |

## 五、其余 open issue 分布

| 里程碑 | open | 说明 |
|---|---:|---|
| Deferred / Budget | 1 | 预算受限推迟（#25） |
| Distribution / Release Acceptance (Unscheduled) | 1 | 分发/发布验收（#161） |
| Research / Frozen | 5 | 冻结研究 P4 |
| v0.10.0 · Native PPTX Round Trip | 7 | 未来版本 |
| v0.11.0 · PPTX Layout Adaptation & Workflows | 10 | 未来版本 |
| v0.8.0 · Document Fidelity, CAT & Languages | 3 | 已发布；余下均为规划/路线图追踪器，非本版实现项 |
| v0.9.0 · Professional CAT Migration & Interoperability | 12 | 未来版本 |
| v1.0.1 · Android Discovery & Platform Strategy | 1 | 未来版本 |

## 六、改动记录

- 第一轮：新建 milestone #20；18 个 issue 归入 v0.8.1；补 #219/#233/#257/#258 的 P2 标签；58/58 均有唯一 P0–P4 与里程碑。
- 第二轮：复核 comments + 核实 v0.8.0 代码后，#257/#229/#230/#223 移入 v0.8.0 并关闭（附证据评论）。当前 open 54，#v0.8.1 剩 14。

## 七、v0.8.1 开发优先级排序（工程管理逻辑）

### 排序原则（为什么不是简单按 P0–P4）
1. **约束/风险优先（TOC + risk-first）**：数据完整性/安全类 P0 是硬前提，无论 WSJF 多高都先做。
2. **WSJF = Cost of Delay ÷ Job Size**：同等风险下，先做「代价高、体量小」的快速胜利。
3. **关键路径/依赖（CPM）**：有下游依赖的前置项先做；大特性必须建立在稳定输入之上。
4. **验收前置（fail fast）**：对已发布能力的验收尽早跑，早暴露 0.8.0 遗留缺陷，而不是在其上继续堆功能。
5. **滚动波次 + 并行工程**：#270 的设计审计第 1 天并行启动（只读、无代码风险），编码/合并放到最后。

### 排序结果（1 = 最先）

| 排序 | Issue | 标签 | 类别 | 依赖/前置 | 体量 | 阶段 | 一句话理由 |
|---|---|---|---:|---|---|---|---|---|
| 1 | #226 | P0 | 安全/约束 | 无（它是别人的前置） | M | W1 | 27GB 冻结会丢工作并阻断一切大文档工作与 #270 大文档测试；真修复已在备份分支，成本低 |
| 2 | #258 | P2 | 可靠性使能 | 无，PR #261 就绪 | S | W1 | 快速胜利：防误配模型导致整任务失败，直接决定 #219/#270 视觉测试能否稳定跑（WSJF 最高） |
| 3 | #233 | P2 | 数据完整性 | 无 | M | W1 | split/insert 后 positional 列表静默错位→错锚定；剩余面小、风险高 |
| 4 | #222 | P1 | 核心工作流 | #226 之后 | M | W2 | 暂停后改连接在 Continue 不生效，中转/弱网用户高频踩 |
| 5 | #276 | P2 | 平台 bug | 无 | S | W2 | Windows Ctrl+Alt+P 小确定修复，有鼠标替代但可见度高（1 行级） |
| 6 | #90 | P1 | 验收门 | #102、#209/#262 已合 | M | W2 | 审校端到端验收，缺陷回灌队列；早跑降 #270 风险 |
| 7 | #191 | P1 | 验收门 | 大纲 #185–190 已合 | M | W2 | 大纲原生窗口验收，暴露结构缺陷早于 #270 |
| 8 | #102 | P3 | 验收门 | #90 同源 | M | W2 | 审校布局/无障碍原生验收 |
| 9 | #17 | P2 | 验收门 | 无 | M | W2 | 多语言界面验收（en/es/ru） |
| 10 | #220 | P1 | 偿债/omnibus | 先拆 12 个未勾选项 | L | W3 | 核心已随 0.8.0；剩余 12 项（A3/B1/B5/C1/C2/C3/D2/D3/E1/E2/F1/F2）体量大、歧义多，先拆再按各自严重度清 |
| 11 | #231 | P2 | 可观测/使能 | 无 | M | W3 | vision 用量/成本进 trace；是 #270 HIR/成本指标的使能项 |
| 12 | #277 | P3 | 打磨 | 无 | S | W3 | 术语卡字段补齐，有替代路径 |
| 13 | #219 | P2 | 阻塞能力 | VLM 配额 | L | W4 | 竖排日文扫描被 quota 阻塞无法排编码；#270 日语语料需要它，配额到位即插入 |
| 14 | #270 | P1 | 核心特性 | 依赖 1–13 的结论 | XL | W4 | 发布主题、体量最大、依赖前置修复与验收；设计第 1 天并行启动，编码/合并最后，按子 issue 分批 |

### 关键说明
- **#276（P2）排在 #220（P1）前、#231（P2）排在多个 P1 验收前**是刻意的：P0–P4 是「优先级类别」，执行序用 WSJF+依赖+风险，不做标签字典序。
- **#270 不是「最后才想」**：它的**设计/审计第 1 天并行启动**（产出子 issue、暴露架构风险），只是**编码/合并**排在稳定化之后——这是滚动波次的标准做法。
- **验收（#90/#191/#102/#17）放 W2 而不是收尾**：fail fast，避免 #270 建在未验证的审校/大纲能力上；但它们是「人力/原生门」，彼此顺序可互换，受谁有空跑真机约束。

### 依赖链速览
`#226 / #233 / #258 / #220 / #231 / #219` → 稳定、可信的输入与度量 → `#270`（QA 分诊）；`#90/#102/#191` 验证审校/大纲底座 → 降低 #270 风险；`#222 / #276 / #277` 独立、无下游。
