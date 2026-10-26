# Folith End-to-End Translation Performance Audit

审计日期：2026-10-26（UTC+3）。诊断与离线实验；未修改生产默认值、用户任务、配置、译文或 TM，未发起真实 API 调用。

## 1. Executive Summary

**真实长任务主要在等待多种模型调用，不是 Python 的 QA 或落盘。不能把“翻译一本书一小时”简单归因于正文模型速度。** 找到了当前用户所用 `dots3-note-prev` 的完整真实请求记录：任务 `e7a302d59653e282`，921 个单元、160,006 原文字符，自动流水线 **8,591.790 s（143.20 分钟）**。其中前置准备约 **17.18 分钟**，翻译及修复/知识反馈约 **35.99 分钟**，自动标注约 **90.03 分钟（62.87%）**。另一次交付运行 **138.711 s**；不能将中间用户等待算作计算时间。

重要版本边界：本次 HEAD 为 **`c8d6ac1707e0bada4eaf63ff9e9292c0c845178c`**（10 月 19 日），上述实际运行记录为 **`git:cfe2f4f` / 0.7.0**（10 月 22 日）。后者已有异步知识反馈；当前 HEAD 的反馈仍在提交线程同步执行。两种实现必须分开评价。构建身份自述不覆盖未提交改动，因此不能保证历史执行文件与该提交逐字一致；已用该提交的实际代码与请求重叠证据相互核对。

最值得先验证的三项优化：

1. **把有效批次策略与入口配置统一，再做自然段批次质量实验。** 当前桌面显式传批次大小，使核心的自然段默认值未生效。真实任务 161 批平均只发送 993 字符，4,800 字符上限没有用满；同结构自然段静态计划为 108 批（2,000 字符）或 58 批（4,000 字符）。这证明可减少计划请求数，尚不证明速度或质量收益。
2. **消除自动标注的有序补队空槽。** 它已两路并发，不能再提“从串行改并行”作为新优化。按序等结果、持久提交后再补对应槽位造成可观测空闲；保持提交顺序，分离完成收集和有界补队即可先做局部实验。
3. **验证模型角色的推理模式与重复输入成本。** 全任务 421 次逻辑调用；提供商返回 2,087,088 completion tokens，同时最终译文只有 54,698 字符，记录的 reasoning 内容达 5,728,600 字符。不能把计费 completion tokens/s 当成有用正文 tokens/s，也不能未经验证就关闭推理。先做获授权的小样本模式/提示词实验。

没有发现支持“用 asyncio 重写就能解决一小时”的证据。当前正文 worker 实际峰值为 4、平均活跃约 3.72；全局请求峰值为 8，包含知识反馈，不能把二者混称为正文并发。章节并行规划器未接入生产；直接接入会改变跨章节状态可见性。

## 2. Current Architecture and Actual Execution Path

### 2.1 审计范围与证据

开始时工作区已有 17 个 tracked 文件修改及多项 untracked 内容，包括 `core.py`、Qt、source scope 等。本次仅新增本报告、脱敏数字证据、离线审计脚本及其测试；保留原有改动。`git diff -- core.py` 表明现有差异集中在范围选择/重解析接入，`translate_stage` 和本次主要计时路径没有未提交差异。本地实验仍标为“当前工作区”，不冒充干净 HEAD 实验。

证据索引：

| 标识 | 文件/来源 | 适用范围 |
|---|---|---|
| E1 | [real-jobs.json](translation-bottleneck-evidence/real-jobs.json) | 只读扫描 3 个真实任务后的 allowlist 数字；含 run、批次、请求、构建身份与阶段边界 |
| E2 | [mock-matrix.json](translation-bottleneck-evidence/mock-matrix.json) | 本次实际 `translate_stage` 的固定文本离线 A/B/C 实验 |
| E3 | [local-scaling.json](translation-bottleneck-evidence/local-scaling.json) | 当前运行时 100/1,000/5,000 单元，零延迟 provider，包含 CPU、阶段、扫描计数及持久化细分 |
| E4 | [10 月 8 日历史脱敏快照](../performance-real-tasks-2026-10-08/evidence.json)及其 [审计方法](../performance-real-tasks-2026-10-08/README.md) | 较早真实任务；不代表当前 HEAD 或当前安装版本 |
| E5 | [新增复现脚本](../../scripts/translation_bottleneck_audit.py) | 复用 `translation_efficiency_benchmark.DeterministicProvider`；不连接网络 |

真实原始记录来自本机桌面数据目录下 `<job_id>/state.json`、`.performance/runs/<run_id>/{summary.json,requests.jsonl,events.jsonl}`、runtime events；也盘点了仓库 `outputs`。新报告不复制文件名、原文、译文、URL、凭证或完整配置。`performance.json` 可能已被后来的交付 run 覆盖，所以按 **state.performance.run_id** 关联嵌入式 profiler，而不是盲取最新文件。较老任务没有 requests.jsonl，其“0 条可读请求”表示缺测，不表示零 API 使用。

### 2.2 入口与并发的真实传递

`folith/application/task_setup.py::STRATEGY_PRESETS` → Qt setup/adapter 的策略与任务状态 → `application/settings.py::resolve_effective_settings` → `JobConfig.from_frontend` → `to_pipeline_kwargs` → `core.run_job_pipeline`（exclusive-job wrapper）→ `_run_job_pipeline_impl` → `translate_stage` → Executor。

`settings.py` 恢复任务时优先保留任务已保存的任务级决策；连接配置另行解析。`JobConfig` 总会输出 `translation_concurrency`、`batch_size` 和 `max_batch_chars`，并非只传 UI 改过的字段。

| 调用方式/预设 | configured concurrency | 实际 worker 上限（有足够待处理批次时） | 附加质量/费用差异 |
|---|---:|---:|---|
| 核心调用省略并发、无保存值/环境覆盖 | 1 | 1 | 核心 fallback；不等于桌面默认 |
| 快速 | 6 | 6 | 6 项/4,800 字符；不开全文理解、术语提取、独立审校，关闭知识反馈 |
| 标准 | 4 | 4 | 4 项/2,400 字符；理解、术语、知识反馈；不开独立审校 |
| 学术增强（UI“精细”） | 4 | 4 | 4 项/2,400 字符；独立审校、严格术语治理 |
| 恢复已保存任务 | 保存值 | `min(clamp(value,1,16), remaining_batches)` | 不应偷偷更改批次拆分 |

`translate_stage` 的解析顺序是显式参数 → pipeline_config → `FOLIOTHREAD_TRANSLATION_CONCURRENCY` → 核心默认。只剩两个批次时 4 会降为 2，E4 中验证任务确有此记录。不能以快速/标准 wall time 对比宣称“并发加速”：它们同时改变质量流程和批次。

核心新任务默认 `DEFAULT_BATCH_STRATEGY="paragraph"`；但 `_run_job_pipeline_impl` 在调用者显式提供 `batch_size` 或 `max_batch_chars` 时选择 `sentence`。桌面通过 JobConfig 总会提供二者，因此普通桌面新任务仍走 sentence。这是**入口策略不一致**，不是“配置并发 4 被默默改为 1”。直接调用 `translate_stage` 且无策略也保留 sentence 兼容行为。已有 saved batch_strategy 的任务继续沿用保存值。

### 2.3 执行与依赖

```mermaid
flowchart LR
  I[读取 / 解析 / 清洗 / 分句] --> U[全文理解 / 术语准备]
  U --> G[有界批次 worker: 上下文 + 生成]
  G --> Q[确定性 QA + 必要修复]
  Q --> R[可选独立审校 / 候选复验]
  R --> C[按批次顺序提交]
  C --> K[知识反馈与合并]
  K --> S[journal / 快照 / TM]
  S --> F[定向终检]
  F --> A[可选自动标注]
  A --> D[交付 / 导出 / 渲染]
```

图是依赖图，不是互斥时间分区。多个 worker 的 G/Q/R 会重叠；HEAD 的 K 在主提交路径同步执行，10 月 22 日部署版 K 为独立池、按序合并。其源码 `cfe2f4f:core.py` 的 `_knowledge_pool`、`_submit_knowledge`、`_drain_knowledge` 与 E1 中正文 4 + 反馈 4 的峰值相符。

正文并发路径提交约 `2×concurrency` 的窗口，按 `futures[bi].result()` 顺序消费；开始等待当前批次之前再提交下一个。任务执行与提交顺序不同。`previous_target` 在 worker 执行时从已提交 pairs 读取，知识候选/实体也从共享状态取快照；锁保证读取安全，却不保证不同并发/时序下读到相同前驱状态。E2 中并发 2/4/6/8 相对串行均有 **59/60 个上下文不同**。

`folith/section_scheduler.py` 无生产导入，仅测试/benchmark 使用。它约束同一 section 每波一个批次，但没有定义全局译名/术语合并语义，也不保证跨章节独立；仅接入 planner 不能自动解决一致性问题。

`review_overlap` 默认为 False，且只有审校开启、effective concurrency≤1 才启用。`generated` 允许下一批使用尚未审校的译文，`stable` 在下一批前排空提交。已有 [语义等价性测试与裁定](../reviewer-overlap-semantics.md)明确 generated 不是串行 committed-target 语义的透明优化；本次重跑相关测试通过。不能为了“隐藏审校”直接推广 generated。

## 3. Performance Baseline

### 3.1 Dots 真实任务：时间线与可观测范围

主 run：`run_20261022_151924_733d4f21`。独立导出 run：`run_20261022_190629_028d8c3b`。时间线为 UTC+3：

| 区间 | 墙钟时间 | 证据与含义 |
|---|---:|---|
| 18:19:24.345 → 18:36:34.969 | 1,030.624 s | 前置准备；初始读取/结构处理约 1–2 s，其后理解/画像与术语请求；术语边界约 18:35:50 |
| translation_plan → translation_review_complete（19:12:34.336） | 2,159.371 s | 翻译及嵌入的 QA、修复、知识反馈、提交、尾部收敛；profiler 容器 2,158.502 s，边界差约 0.87 s |
| translation_review_complete → annotation_complete（20:42:36.079） | 5,401.745 s | 标注及转场；annotation worker 区间约 5,401 s |
| annotation_complete → automatic_pipeline_complete | 0.051 s | 自动流程尾部 |
| 主 run 合计 | **8,591.790 s** | 边界闭合仅毫秒舍入差 |
| 另一次交付 run，22:06:29 → 22:08:47 | **138.711 s** | 0 模型调用，进程 CPU 163.069 s；独立交付/渲染工作，不能并入首次 run 或计算人为等待 |

### 3.2 阶段计时与模型工作量

`Σ` 为累计工作量，`U` 为已测区间并集。以下列不是可加总的互斥桶。tokens 来自对应真实请求 usage；失败调用通常缺 usage。CPU 未有专属 thread span 时写“未知”，不以进程 CPU 分摊伪造。

| 阶段 | Wall/Σ/U（s） | 本地 CPU | API Σ（s）/调用 | 输入 / 输出 tokens | 重试/错误与关键路径解释 |
|---|---|---|---|---|---|
| Document ingestion | classify 0.160、layout 0.411、segment U 0.022；完整边界约 1–2 | 未独立记录 | 该段无可见模型请求 | 不适用 | 无此段错误证据；部分读取未细分 |
| Document understanding + 术语准备 | 前置合计 1,030.624，含上述 ingestion；理解与术语的精准互斥 CPU/请求角色细分不足 | 未知 | 2,009.253 / 22 | 49,030 / 74,563 | 3 个 auxiliary 失败均在此；并行累计 API 可大于前置墙钟 |
| Translation generation | Σ7,356.358，U1,986.893；容器2,158.502 | 未知；含 setup | **7,353.945 / 164** | **306,299 / 556,993** | 161 成功批 + 3 失败逻辑调用/外层重试；P50 36.386、P95 101.426、max253.919 |
| Deterministic QA | Σ0.435、U0.435 | 未独立记录 | 0 / 0 | 0 / 0 | 本地检查很小，保留 |
| Translation repair | Σ376.407、U376.403 | 未知 | **376.275 / 11** | 13,738 / 19,072 | 11 次均成功；7%左右计划批触发，按 batch span 关联；并非生成重试 |
| Semantic review | 配置关闭、无 items | 不适用 | 0 / 0 | 0 / 0 | 不能把2,158s的空容器当审校时间；其他精细任务需另外测 |
| Knowledge feedback | merge/背压 span Σ173.082；异步提取另算 | 未独立记录 | **5,308.995 / 131** | 89,637 / 525,980 | 与正文重叠；不能把173s当全部反馈成本；131次是批次关联 auxiliary 请求 |
| State persistence | state_commit Σ0.628；runtime IO累计6.823 | 未独立记录 | 0 / 0 | 0 / 0 | runtime IO含3.611s状态写+3.211s事件追加；可能与其他工作交叠，不等于全部序列化成本 |
| 自动标注（额外模型加工） | 窗口约5,401.7；worker Σ9,493.555 | 未独立记录 | **9,492.223 / 93** | 132,211 / 910,480 | P50 102.739、P95 156.129、max337.865；0逻辑失败，HTTP尝试仍可能重试 |
| Export/render | 独立run138.711 | 进程163.069 | 0 / 0 | 不适用 | 高CPU的独立交付步骤；包含嵌套span，不相加 |

主 run 总进程 CPU **170.375 s / 8,591.790 s（约2%）**，不能视为每阶段独占 CPU。421 次逻辑请求完整配对，累计请求区间 **24,540.758 s**，并集 **8,584.914 s（主 run 的99.92%）**。这意味着几乎一直有请求在途；**不等于本地总开销仅6.875s**：CPU、排队和阻塞可发生在其他请求在途时。

阶段级queue/wait：只有下节的batch enqueue/worker/ready/commit以及部分provider gate有可关联观测；理解、修复、反馈提取、渲染的专属排队/锁等待未完整记录，均为**未知**，不以profiler默认0代替实测。Dots反馈131次未见逻辑失败；其余3个auxiliary失败在前置准备。阶段的独占关键路径增量需要反事实实验，不能由各阶段Σ/U直接相减得出。

### 3.3 并发、队列与 HOL

| 指标 | Dots 正文/翻译阶段 | 标注 |
|---|---:|---:|
| configured / effective worker cap | 4 / 4 | 受独立代码上限限制为2 |
| 配对 worker 峰值 / 平均 active | 4 / **3.715** | 2 / **1.758** |
| worker 观测窗口 | 2,084.034 s | 5,401.065 s |
| worker capacity idle（窗口×cap−Σworker） | **593.054 worker-s（7.11%）** | **1,308.575 worker-s（12.11%）** |
| queued→worker_start：mean / P95 / max | **41.118 /114.469 /166.240 s** | 0.000737 /0.000118 /0.063062 s |
| result_ready→commit_start：mean / P95 / max | **25.156 /137.205 /184.772 s** | **13.608 /66.481 /255.345 s** |
| 真实逻辑请求在途峰值 | 正文生成4；整翻译容器所有角色8 | 标注worker2 |
| 整翻译容器所有角色平均逻辑在途 | **6.041**（含反馈；区间2,158.502s） | 不能用线程数代替请求均值 |

正文纯生成请求区间 Σ7,353.972、U1,986.876、首末窗口2,084.021s，平均 **3.529** 个逻辑请求。排队是预提交窗口内的真实执行器等待，不应加到 API latency；ready→commit 同时受按序等待和提交背压影响，不能全部归为某一个锁。

这些是 **client logical call** 的实测并发，包含SDK内部重试/读响应；并非提供商正在执行 decode 的数量。HTTP body 完成时间、服务端执行开始不可观测，无法给出精确“服务器实际同时推理数”。

### 3.4 API 使用效率与吞吐

Dots 的 161 个已提交正文 packet：当前正文平均 **993.329**、P50 **1,009**、P95 **1,411**、min **184**、max **1,787** 字符；141批有6个待翻单元，20批少于6个。其4,800字符上限基本不是约束，单元数与语义边界更早触顶。正文输入159,926字符（部分源单元本地复用/跳过），并非完整160,006字符。

正文 prompt 总计694,302字符，平均4,312.43字符/批；源文净字符仅占23.03%。组件：固定指令97,888（平均608/批），文档上下文169,074，相邻上下文201,111，术语27,340，实体20,055，style9,338，其余3,008；`source` 类166,488包含编号等包装。**非净原文77%不等于77%可删冗余**。本次未用源文/上下文额外指纹做重复性判断，也没有证明哪一段语义内容可安全删除。

| 指标 | 纯正文生成口径 | 翻译容器口径 | 首次完整任务口径 |
|---|---:|---:|---:|
| 有用原文字符/s | 159,926 /1,986.876 = **80.49**（generation请求并集） | 160,006 /2,158.502 = **74.13** | 160,006 /8,591.790 = **18.62** |
| 成功正文批次/min | 161 /1,986.876×60 = **4.86** | **4.48** | **1.12** |
| 逻辑API calls/万原文字符 | **10.25**（164，含失败重试） | **19.12**（306，含修复/反馈） | **26.31**（421） |
| 输入tokens/原文字符 | **1.915**（306,299 /159,926） | 其余角色另列，不把状态字符估算当exact | **3.693**（590,915 /160,006） |
| useful output tokens/s | **不可精确测**；raw译文响应60,835字符，按/4仅代理约7.65 token/s | 最终译文54,698字符，约/4/容器=6.34估算token/s | 约/4/主run=1.59估算token/s |

纯生成指标分母是活跃区间并集，另有首末生成窗口2,084.021s（含修复/间隙）；同一指标跨任务比较必须固定口径。`/4` 是粗略估算，中文尤其不能当 tokenizer 结果。提供商 completion tokens 包含未单列的推理成本，不能当 useful tokens。

主 run usage：输入590,915、输出2,087,088、已报告 cached86,528 tokens，415/421次返回输入输出 usage；失败请求缺测，缓存字段也非全覆盖，reasoning_tokens全部缺失。每万原文字符已报告输入约36,930、输出130,438 tokens。没有 relay 的实际价格与账单，**美元成本未知**；公式为 `(uncached_input×P_in + cached_input×P_cache + completion×P_out)/1e6 ÷source_chars×1e4`，缺失usage/缓存项与推理计费口径须由账单补齐，不能填0或用官方价格代替中转价。

## 4. Critical Path Analysis

长任务主关键路径是 **前置理解/术语 → 翻译/反馈尾部 → 自动标注尾部**。生成Σ7,354、知识APIΣ5,309、标注Σ9,492不是串行相加的24,155s：真实总耗时为8,592s。

已证实的等待机制：

- 正文 `futures[bi].result()` 加顺序提交：后续批次就绪后最长仍等184.8s；这是保序设计的可见成本，不代表可以无条件乱序提交。
- 标注 `_annotation_results` 只预提交2个，`yield pending.pop(i).result()`，消费者保存以后才补下一个。较慢的低编号请求会让已空出的另一个槽位等它；E1有255.3s的ready→commit长尾。
- 当前 HEAD 的 `_commit_batch` 同步 `_knowledge.observe_batch`，可挡住后续结果消费/窗口补充；10月17日真实任务 `4a50d882c7ff7b9f` 最后翻译容器1,691.184s、反馈Σ1,315.072s（77.76%）符合该机制。该run有多次translation_plan（7批→227批），E1按plan epoch分开统计batch ID，不把跨窗口同名批次覆盖；不能把整个run请求数直接分摊给最后容器。
- Dots部署版已异步反馈，131次API累计5,309s被大幅重叠，只有173s记录在merge/背压span内；不再是“串行反馈占主路径96%”的旧结论。

标注在固定请求服务时间、仍为2路且保持同一批次内容的理想下界是 `max(Σworker/2, max_worker)`≈4,746.8s；当前窗口5,401.1s，**最多约654s（10.9min）**的空槽空间。这个上界含调度、尾部和本地成本，不是承诺可全部回收；真实provider并发竞争可改变单次延迟。

同样，worker平均接近cap不证明服务端饱和；8路逻辑请求可能争用同一relay模型队列。没有服务端指标，不给“API队列占X%”的假拆分。

## 5. API vs Framework Overhead

### 5.1 可分离与不可分离

| 概念 | 当前证据 | 结论 |
|---|---|---|
| API Request Latency | logical begin/end、usage、错误、batch/role | 可测；含建连、SDK retry、网络、服务端与完整响应读取 |
| Server-side Queuing | 无服务端排队/执行开始指标 | 不可分离；长尾不自动等于排队 |
| Prefill / Decode | 非streaming `chat.completions.create`；TTFT记录为None | TTFT、prefill/decode各自吞吐不可测 |
| Network / Transport | HTTP attempt start与response headers；连接池代码可审计 | header间隔混合网络与服务端；没有DNS/TLS/body分段，不能当纯RTT |
| Client Scheduling / Retries | batch排队/就绪/提交；HTTP attempt与外层retry | 可测部分客户端等待；SDK sleep不全有独立span |

Dots421个逻辑调用对应 **443次HTTP attempt**；415条header均为200，6个逻辑失败（3 timeout、3 connection error），3条正文外层retry。多出的22次HTTP尝试说明仅看外层retry会低估重发；有28次attempt没有header完成记录，不得为其补造状态码。**没有可见429证据**，不能宣称429是瓶颈，也不能证明relay没有内部限流。RPM/TPM合同及队列不可知。

### 5.2 已存在的合理设计

- `folith/llm_transport.py::LLMClientRegistry.acquire` 按线程与连接身份复用SDK/HTTP client，包含连接池；executor退出后清理死线程client。默认开启。不是每段重建TCP/TLS。
- `request_control.py::ProviderRequestController` 已有共享gate、指数backoff+jitter、取消探测；默认min_interval=0，不是强制串行。当前key以provider/model为域，**不是RPM/TPM额度控制器**，也不是全局active请求上限；同一relay的多角色仍可能竞争。
- `translate_batch` 有解析成功后才写入的exact请求结果缓存；TM限定目标语言且依审批状态晋升，避免直接把未审译文当已批准。知识提取也有缓存与本地触发策略。冷任务无需期望exact-cache大量命中；Dots正文cache hit为0。
- JSON协议支持容错解析和长度校验。格式失败最多3次整批尝试，再降级逐单元（单项也最多3次）；可能放大到 `3+3×items` 个logical调用，SDK还可重试。这个路径真实可达，但Dots161个已记录正文prompt均最终成功，未见协议失败证据，不能把本次3个transport重试说成JSON失败。

### 5.3 Reasoning与角色路由

`core._call_llm_transport` 只有显式 `thinking_disabled` 才发 `extra_body={thinking:{type:disabled}}`；默认是provider_default。translator/auxiliary有mode映射，reviewer保持provider_default。`reasoning_effort_options` 对未知Dots模型返回空，不会自行强加OpenAI reasoning_effort。review/auxiliary未另配时回退同一provider/model。

Dots记录translator与auxiliary均provider_default；确实返回大量reasoning_chars，说明存在可观察的推理内容，但不证明它可关闭、可免费删掉或关闭后质量等价。DeepSeek官方接口说明 `thinking.type` 支持enabled/disabled；当前实现的字段符合其文档，但**不等于自定义中转站或名为deepseek的其他端点一定接受/执行该开关**。[DeepSeek官方Thinking Mode文档](https://api-docs.deepseek.com/guides/thinking_mode/)

独立审校可能另有blind verification/evidence round。修复也可能触发blind审校，故“启用审校一次”不等于每批只多一个请求。应按实际角色与候选流程计数，不能按按钮数估计API成本。

### 5.4 本地算法与已修复项

复用 `translation_critical_path_audit.py`，同一合成任务构造、review开启、translation worker=1、零远程延迟，观察本地规模效应：

| 单元 / 批次 | wall / process CPU s | setup s | 本地semantic review s | state_commit s |
|---|---:|---:|---:|---:|
| 100 /28 | 0.0944 /0.0901 | 0.0051 | 0.0140 | 0.0085 |
| 1,000 /280 | 0.6387 /0.6123 | 0.0723 | 0.2927 | 0.0814 |
| 5,000 /1,400 | 5.9377 /5.7909 | 1.0021 | 3.2834 | 0.5150 |

真实可达的残余前缀工作：`core._runtime_review_context` 的历史上下文选择、`_translation_evidence_index` 的历史pair复制/安全视图，以及review packet构造。1,000→5,000单元，evidence-index累计遍历 **140,860→3,504,300** 项（约24.9倍）；本地时间 **0.011→0.265s**。previous-context阶段 **0.030→0.747s**；packet-build **0.113→1.405s**。这是固定批大小下存在O(N²)前缀处理的证据，但5,000单元整轮仍仅约6s，**不是已证实的分钟级远程任务主瓶颈**；20,000单元、真实大型术语表/大量findings尚未在本次运行测量，不外推精确秒数。

已有效的优化：正文 `recent_target_context(...,2)` 从尾部取上下文，不再为每批建立全文历史字典；`compiled_glossary` 按内容修订复用匹配器，QA一批只派生一次，不是每单元重复normalize全术语表；journal+阈值快照替代每批全量state/TM重写，批次退出强制flush；runtime telemetry有合并窗口，profiler阶段统一关闭后持久化。当前普通并发路径没有每批deepcopy整个state。实验性的review overlap snapshot仍复制历史pairs并浅拷贝state，不能说全仓库不存在前缀复制。

没有测得锁等待成为主要CPU/IO瓶颈；未加入逐锁探针。runtime遥测仍会在合并窗口前沿同步写入，但Dots累计已测IO约6.8s，不支持为了它重写持久化层。E3的纯本地测试中review前缀工作值得保留为P2规模问题，不能重复宣称已解决的全文落盘问题仍主导任务。

## 6. Bottleneck Ranking

排序针对有完整请求记录的Dots实际运行；HEAD特有问题另标。优先级是工程决策，不是未测收益评分。

| Bottleneck / Priority | Evidence | Root cause | Proposed change | Expected impact | Quality risk | Complexity |
|---|---|---|---|---|---|---|
| 自动标注模型等待与补队HOL / **P1** | 5,401.7s，主run62.87%；2路平均1.758；ready等待max255s | 已验证：全翻译后才开始；按序消费后补槽 | 完成收集与顺序提交解耦，保持有界队列；先固定2路 | 固定服务时间下空槽空间≤654s；实际收益待A/B | 低到中：必须保留target版本、取消、durability边界 | 中 |
| 正文小批、重复固定上下文 / **P1** | 161批均993字符；77%输入不是净原文；静态自然段108/58批 | 已验证：项数/边界先触顶，桌面入口选sentence | 明确保存batch_strategy；新任务选择经过盲评的策略，老任务保持原计划 | 请求数可下降；真实耗时/token收益待测，无倍速承诺 | 中：对齐、段内连贯性、截断与多项恢复 | 中 |
| 推理/多角色额外模型输出 / **P1实验** | 421calls、completion208.7万、reasoning_chars572.9万；多数为aux/annotation | 输出量已证实；服务端推理耗时比例及必要性未知 | 获授权小样本reasoning模式、schema精简与角色prompt试验 | 待测；不可用reasoning_chars换算秒数 | 中到高：知识质量与审校召回 | 低到中 |
| 同步知识反馈阻塞HEAD提交 / **P1，仅HEAD** | HEAD同步observe；10/17反馈1,315s/容器1,691s | 已验证设计；10/22版本已异步 | 先核对部署/分支差异，再评估已有异步实现；不重复造调度器 | HEAD有重叠空间，不能复用Dots173s当改造收益 | 中：知识注入滞后与重启恢复 | 中 |
| transport长尾与重发 / **P1观测、P2策略** | 正文max254s；标注max338s；443HTTP/421logical | 错误与重发已知；网络/服务端根因未分离 | 补SDK attempt结束/退避计时；按错误类型处理，保留质量修复 | 不承诺减少多少秒；可避免错误重试假归因 | 低到中：不能漏重试或丢有用结果 | 低到中 |
| review前缀扫描/packet构造 / **P2** | E3扫描约N²；5,000单元整轮5.94s | 已测O(N²)前缀加工，当前量级小 | profile命中后增量/限定证据视图，保持review语义 | 当前任务秒级候选；更大书需重测 | 中：证据完整性和审校真值 | 中 |
| state/runtime IO / **P3观察** | state0.628s、遥测IO6.823s | 现有journal/缓存有效 | 保留现状，出现具体IO异常再处理 | 对当前主run收益小 | 新改造风险高于已测收益 | 暂不实施 |

## 7. Benchmark Results and Limitations

### 7.1 Experiment A — Concurrency（本次离线测量）

固定240单元、39,970原文字符、简体中文、每批4项/2,400字符，关闭TM、exact cache与知识反馈，固定provider sleep20ms，无网络、无rate limit。使用生产 `translate_stage`；计时是本机单次样本，有预热/系统负载噪声，不能换算真实API容量。

| configured / effective | wall s | calls / in-flight peak | 平均active worker | idle capacity worker-s | mean queue / ready→commit s | 与串行previous_target不同批数 |
|---|---:|---:|---:|---:|---:|---:|
| 1 /1 | 1.6825 | 60 /1 | 0.94 | 0.127 | 0 /0 | 0/60 |
| 2 /2 | 0.8393 | 60 /2 | 1.99 | 0.025 | 0.0370 /0.0005 | 59/60 |
| 4 /4 | 0.4283 | 60 /4 | 3.92 | 0.063 | 0.0289 /0.0013 | 59/60 |
| 6 /6 | 0.2801 | 60 /6 | 5.93 | 0.048 | 0.0251 /0.0027 | 59/60 |
| 8 /8 | 0.2087 | 60 /8 | 7.50 | 0.140 | 0.0225 /0.0017 | 59/60 |

各组240/240顺序正确、无空译、reference逐项匹配、占位符全保留、协议fallback=0；0 API错误/429是Mock构造，不能推论生产稳定性。translator按固定reference回答，不读取连续性语义，故这些结构检查不能证明真实译名连续性不变。本次使用逐批上下文比较，没有用输出hash代替质量验证。

### 7.2 Experiment B — Batch Sizing（候选，不是生产建议）

固定自然段装箱、20项上限、不改变ID/顺序；字符上限只在离线进程patch生产装箱函数，原函数默认参数在定义时绑定，因此不能只改常量就声称已测新上限。保留相同文本/语言/provider。

| chars cap | Mock计划批次 | 原文均字符/批 | wall s | 结构质量 |
|---:|---:|---:|---:|---|
| 2,000 | 21 | 1,903.3 | 0.1681 | 240项、顺序/占位符/reference均通过，0fallback |
| 4,000 | 12 | 3,330.8 | 0.1068 | 同上 |
| 6,000 | 12 | 3,330.8 | 0.0977 | 同上；20项约束已触顶 |
| 8,000 | 12 | 3,330.8 | 0.0952 | 同上；与6k差异属单次计时噪声 |

在真实Dots结构上只运行 `make_paragraph_batches`、不生成译文：2k/4k/6k/8k分别 **108/58/57/57批**；平均 **1,481.5/2,758.7/2,807.1/2,807.1字符**。保留其真实semantic units/section digests与paragraph ranges，均仅1个singleton batch。当前161批不是大量极小chapter batch独自造成，而是6项上限及边界共同作用；不能忽略自然段不可拆与20项约束。

这些静态/Mock结果不衡量大批生成时的真实响应长度、截断率、推理时长或重试放大。下一步必须在同一真实模型上配预算做小样本2k/4k实验；没有依据直接用8k。

### 7.3 Experiment C — Translation Pipeline（本次离线测量）

同一文本、同一provider20ms、并发4、60个正文批次。Translation-only是benchmark内局部停用deterministic检查的消融，保留其他本地流程；不会成为产品设置。repair实验故意删除占位符，repair stub恢复固定reference。

| 组合 | wall s / CPU s | 调用拆分 | 观察 |
|---|---:|---|---|
| generation only消融 | 0.3879 /0.0796 | 60 generation | 无检查基线，不能作为生产提速方案 |
| +deterministic QA | 0.4052 /0.1486 | 60 generation | 本地增量很小；单次CPU噪声不作精确收益 |
| +repair（每批注入占位符错误） | 0.8246 /0.2609 | 60 generation +60 repair | repair触发60/60；恢复全部240项 |
| +independent review（无注错） | 0.7964 /0.2154 | 60 generation +60 review | 空finding reviewer，不测真实召回 |
| Full（QA+repair+review+feedback） | 1.6304 /0.4379 | 60 generation +60 repair +120 review +16 auxiliary | repair候选blind复验再加formal review；多一次验证是质量流程，不是重复bug |

所有组最终240/240 reference匹配、0空译/占位符错误/协议fallback。两种注错组的修复质量和其他组输入初译不同，不能简单相减推导生产repair成本；它们验证的是调用增量与路径。无实际术语表/实体歧义，本轮未宣称术语或实体质量得到验证。

### 7.4 Experiment D — Provider Isolation（待授权预算执行）

本次未读凭证、未用已配置连接发新请求。现有记录证明Dots连接曾可用，不证明现在仍有有效余额/额度；未做Dots vs DeepSeek或thinking开关真实A/B。相同输入、相同结构约束、同一质量规则的provider对照**待执行**，没有模拟“真实API结果”。

建议协议：先定每provider最大请求数/费用与样本ID，translation、repair、review、auxiliary分别固定路由；同一个角色比较provider_default/thinking_disabled，保留temperature/schema设置与原文/目标语言。先warming一次再交错测试，独立记录provider usage、实际wire开关、错误/限流、TTFT可用性、完整性和盲评，不能按模型名称推断功能。

### 7.5 质量门禁

| 质量维度 | 本次完成 | 上线前仍需 |
|---|---|---|
| 漏译/段落对齐/格式 | Mock240项、顺序、非空、占位符及reference逐项核对通过 | 真实输出逐ID核对；长段截断/跨项错位样本 |
| JSON结构成功率 | Mock全部无协议fallback；Dots记录最终161批成功 | 逐attempt区分JSON失败与transport错误，覆盖长批输出 |
| 修复触发率 | Dots11次/161计划批；注错实验60/60 | 同一输入下候选batch策略的修复率与复验成功率 |
| 独立审校发现率 | Dots关闭；Mock空finding，不能估计召回 | 同一审校器+固定缺陷样本的发现率/误报率 |
| 术语、实体一致性 | 已重跑现有配置/上下文/重叠语义测试 | 专名反复出现、别名、章节跨越的真实翻译对比 |
| 长距离连续性、文学质量 | 上下文差异已测，文本审美未测 | 固定样本盲评：同页段内、跨批、跨章、相隔数十页的专名/语气/指代 |

盲评建议：冻结一套可合法用于基准的文学/专业样本与术语决策，随机隐藏配置标签，由同一评审按完整性、术语/实体、叙事连续性、风格逐项标注；先处理显著错位/漏译，再比较可接受质量下的时间与成本。Mock reference一致和hash一致均不能替代此过程。

## 8. Recommended Optimizations

| 方向 | 判断与最小下一步 |
|---|---|
| Adaptive Batch Sizing | **先做固定候选A/B，暂不自适应**。首先解决配置实际策略并评估2k/4k；动态更改resume边界与重试成本更复杂，当前无需引入新框架 |
| Section-aware Parallel Translation | **不优先接入**。已有正文4路有效；同书章节仍共享专名/术语。先定义section内committed-target契约、全局冻结/合并点与跨章盲评；收益待测 |
| Asynchronous Request Scheduling | **无需因同步SDK直接重写asyncio**。线程已隐藏I/O；优先完成收集/补队与明确背压。HEAD反馈可参考部署已有实现，不重造 |
| Pipeline Overlap | **最有架构潜力**。对已完成必要QA/审校的批次提前标注，保留同一译文版本依赖；全局角色并发上限与正文优先级避免relay竞争 |
| Redundant Context Reduction | **值得做组件级消融**。上下文占比高，但不删质量必需状态；先识别无差异的格式/指令重复与无效注入，用同一质量门禁测试 |
| Prompt Token Optimization | **低成本实验**。稳定system前缀、紧凑标签与候选schema；不得把语义信息删减包装成“纯格式优化” |
| Request Deduplication | **已有exact cache与TM，保留**。Dots冷任务0hit不能证明失效；没有同输入同route重复请求证据，暂不加新的去重层 |
| Model Role Routing | **有潜力但本轮不换模型**。先分角色核对reasoning与输出约束；以后有预算再评估aux/annotation路由，不能用廉价模型降质量换数字 |
| Smarter Retry / Rate Limit | **保留现有gate/jitter，补观测后调整**。区分transport、JSON、质量repair与SDK重发；没有429证据，不增设猜测的TPM限额 |

如果只做一次架构级优化，选择 **有界、带译文版本依赖的阶段重叠调度**：完成必要QA/审校的批次即可进入标注，最终交付等待全部依赖收敛；采集完成与按序提交分开，所有角色共享可观测容量预算。原因是Dots90分钟标注完全挂在36分钟翻译之后，章节并行只覆盖较小的正文区间且改变连续性语义。

这个选择是架构方向，**不是本次已测加速承诺**。定向终检若改变译文，必须只重算受影响批次标注；annotation若依赖完整文档/术语最终状态，就必须等相应依赖。先写依赖/恢复验收样本，再用同一provider实测竞争；无这些保障不能宣称重叠质量不变。不要为此先引入通用任务框架或重写整个应用。

## 9. Quality and Regression Risks

1. 批次计划是断点/ID契约的一部分。新策略不能改变已有任务边界；保留精确ID、顺序、段内映射、未完成批次恢复及超长单项行为。
2. 增加并发改变previous_target、候选术语/实体何时可见；锁和最终排序不足以保证语义确定性。章节边界不是天然独立性证明。
3. 异步知识反馈与generated-review overlap都会改变知识新鲜度；已有历史实现需按当前契约验收，不能只cherry-pick性能部分。
4. 标注提前执行须跟踪实际target版本；修复/终检修改target后，旧标注不得进入最终交付。保持失败记录与partial结果恢复。
5. 不关闭确定性QA、必要修复、blind验证或独立审校来制造提速；生产预设已有质量差异，必须分别报告。
6. 新脚本采用pathlib、tempfile、标准线程/计时API，未添加OS专有生产行为。本轮运行环境macOS、Python3.11；**未在真实Windows执行离线矩阵，也未做Cocoa/Windows标题栏验收**，本次不涉及UI/native变更。
7. 运行状态与trace并非总一致：Dots worker completed但状态`annotations_done`仍False；本次仅以93个annotation请求/批次与milestone计时，不扩张解释为业务审批已完成。较旧日志无逐请求区间，不强行补齐。

## 10. P0–P3 Implementation Roadmap

| 优先级 | 行动 | 验收条件 |
|---|---|---|
| **P0（诊断基线）** | 保存本次脱敏证据；确认用户运行build与目标开发分支；运行时展示configured/effective batch strategy及各角色请求容量；区分usage估算和provider exact | 同一run可闭合时间线；无源码/密钥入日志；不把空容器/旧run当当前阶段 |
| **P1** | 先做标注有界完成收集/补队实验；新任务批次入口一致性及2k/4k质量A/B；获预算后逐角色reasoning小样本实验 | 固定输入/质量门禁/模型；记录wall、usage、HOL、失败、重试、术语/实体与盲评；收益超过噪声才推广 |
| **P1，HEAD条件项** | 对照已部署异步反馈与当前HEAD，确定是否移植已有实现 | 恢复、知识注入时序和语义差异显式验收；不声称该优化在Dots生产任务仍缺失 |
| **P2** | 通过版本化阶段依赖验证翻译→标注重叠；必要时优化review历史视图；补HTTP attempt结束/SDK退避与provider能力证据 | 不增加未获质量批准的并发；目标变更正确失效；20k/大术语表出现实测热点才细化本地优化 |
| **P3** | section planner接入、自适应batch、asyncio重写、新去重层、持久化重做 | 当前证据不足支持投入；新数据改变优先级再启动 |

### 可复现命令与实际验证

```sh
# 离线A/B/C；只有临时任务目录会被流水线写入
venv/bin/python scripts/translation_bottleneck_audit.py --out /tmp/folith-bottleneck-replay

# 追加真实日志只读收集；--job-dir可重复，不读取provider配置/密钥
venv/bin/python scripts/translation_bottleneck_audit.py --logs-only \
  --job-dir '<desktop-data>/<job-id>' --out /tmp/folith-log-audit

# 本地框架规模实验；零延迟stub，不是付费API
venv/bin/python scripts/translation_critical_path_audit.py --sizes 100,1000 --baseline \
  --json /tmp/folith-critical-current.json
venv/bin/python scripts/translation_critical_path_audit.py --sizes 5000 --baseline \
  --json /tmp/folith-critical-5000.json

# 对已存历史脱敏证据重新归因
venv/bin/python docs/performance-real-tasks-2026-10-08/audit.py \
  --evidence docs/performance-real-tasks-2026-10-08/evidence.json --out /tmp/folith-history-replay
```

新增脚本复用旧provider，但修正其prompt提取对自然段标题的兼容；否则老harness会把标题当原文续行，导致虚假的整批重试/逐项fallback。修复仅在新离线工具，不改变产品翻译行为。A/B/C每个配置本次取一个最终有效样本，开发中因探针/fixture适配失败重跑的样本不纳入结果，不充当统计重复。A/B/C源文固定39,970字符，不与真实160,006字符任务混算。

针对本次归因风险而运行的检查：区间并集/乱序输入算术测试防止把累计API误算成墙钟；allowlist测试防止输出文本/凭证；既有batch/config/settings/context/transport/request-control/performance/review-overlap测试验证审计依赖与当前语义。命令：

```sh
venv/bin/python -m pytest -q tests/translation_bottleneck_audit_test.py \
  tests/paragraph_batching_test.py tests/application_job_config_test.py \
  tests/effective_settings_test.py tests/context_target_selection_equivalence_test.py \
  tests/request_control_test.py tests/llm_transport_test.py \
  tests/performance_trace_test.py tests/reviewer_overlap_semantic_equivalence_test.py
```

结果：**119 passed**（5个既有依赖deprecation warnings）。未执行生产provider A/B、真实模型文学盲评、20k框架规模或Windows实机验证；无成本、TTFT、服务端排队或decode速度的伪精确结果。

### 当前工作区代码定位

行号对应本次审计开始后的当前工作区；历史build用`git show cfe2f4f:core.py`核对，不能套用当前行号。

| 证据主题 | 定位 |
|---|---|
| 入口、参数与batch_strategy选择 | `core.py:11369`、`core.py:11692`；`application/job_config.py:242`、`:560`；`application/settings.py:400` |
| 组批与段落约束 | `core.py:2831`、`:2909`、`:5147` |
| 并发解析、上下文读取、窗口/按序Future | `core.py:5261`、`:5434`、`:6693` |
| HEAD同步知识反馈 | `core.py:6299`；`folith/knowledge.py:265` |
| 标注补队和独立cap | `core.py:4177`、`:4284` |
| 请求重试、逐项fallback | `core.py:3419`、`:5590`；统一transport `core.py:2092`、`:2167` |
| 连接池、gate、reasoning角色映射 | `folith/llm_transport.py:125`；`folith/request_control.py:54`；`folith/engine/llm_provider.py:92` |
| 历史上下文与review evidence | `core.py:3033`、`:2969`；`folith/context.py:664`；`folith/translation_evidence.py:658` |
| 已有匹配缓存、持久化、profiler | `folith/terminology.py:233`；`core.py:2639`；`folith/performance.py:161`；`folith/critical_path.py`、`persistence_profiler.py`、`profiler.py` |
| request/HTTP/phase时间线与遥测 | `folith/trace.py:736`；`core.py:4158`；`folith/engine/runtime_telemetry.py:33` |

### 最终六问

**Q1：一本书接近一小时，主要时间在哪里？** 已有Dots实例正文循环约36分钟，另有17分钟前置、90分钟标注。主要是各角色远程请求；不能把自动标注或反馈算进正文模型速度。不能据一个任务概括所有书。

**Q2：API使用效率合理吗？** 有效并发、连接池、缓存、共享退避和持久化设计是合理的；小正文批、多角色重复输入和推理输出仍有明确优化候选。421次调用不等于421次正文翻译，需按角色/阶段评价。现在不能宣称最优。

**Q3：是否有明显架构/调度缺陷？** 有可追溯的批次入口策略差异、有序消费后补队HOL、HEAD同步反馈关键链与残余review前缀扫描。严重程度不同：前两者和远程调用相关，本地N²在本次5k实验是秒级；不能笼统说整个框架失效。

**Q4：不换模型、不降质量，前三项是什么？** 验证并统一新任务有效批次策略；保持质量/提交契约的标注有界补队；逐角色验证reasoning与prompt非语义重复开销。后两种模型请求优化都须在质量A/B通过后采用，不能预先保证等价。

**Q5：只能选一次架构优化？** 选带译文版本依赖与全局容量约束的阶段重叠调度，优先翻译→标注；它针对真实最长后置链，避免为章节并行先改变译文连续性契约。

**Q6：哪些已实测、哪些待生产验证？** 真实日志测得Dots时间线、请求角色/usage、逻辑并发、批次队列/HOL、额外标注与交付；本地测得并发上下文差异、候选装箱、调用增量、5k框架复杂度。真实批大小/推理开关加速与质量、server queue/TTFT/decode、价格成本、20k规模和跨平台接受度仍未验证。
