# Folith 学术研究方向评估报告

日期：2026-10-05
依据：全仓库源码、docs/（61 篇 .md，另有子目录与资源）、tests/（163 文件 / 2,065 个 test 函数）、scripts/（107 个）、
benchmark/、eval/、.regression-logs/、CHANGELOG.md，以及对 OpenAlex / arXiv / ACL Anthology 的
相关工作检索。

---

## 0. 结论速览

| 排名 | 方向 | 一句话定位 | 新颖度 | 现有地基 | 工程量 | 风险 |
|---|---|---|---|---|---|---|
| **Top 1** | **增量重算语义（Incremental Recomputation）** —— 长文档 LLM 流水线在源文/上游产物变化后的**最小可信重算集**及其 benchmark | 「文档被改了一段，最少要重新调用几次模型、且交付物仍与全量重算等价」 | **高**（检索未发现 benchmark 或形式化处理，属真实空白） | ~60%（指纹、失效传播、journal 重放、等价门禁都已存在） | 中高 | 中 |
| **Top 2** | **验证经济学（Verification Economics）** —— 固定预算下的语义审校分流，用**注入缺陷回收率**而非译文质量评分来度量 | 「确定层只能抓结构性缺陷（6/18）；这 6.6% 命中率的随机审校该怎么花预算」 | 中高（部分开放；与 selective annotation / learning-to-defer 必须切割） | ~75%（注入缺陷 harness、live yield、风险门、分层抽样都在） | 中低 | 中 |
| **Top 3** | **保序推测执行（Equivalence-Preserving Speculation）** —— 用 read-set 校验把「生成/审校重叠」从 30–46% 提速但语义不等价，变成**可证等价且仍提速** | 「重叠执行不是透明优化：4/8 段分叉并跨 3 批传播；能否加上校验与有界回滚后既等价又快」 | 中（管线化本身饱和；「语义校验器 × 生成器 + 输出排序契约 + 成本模型」这一窄组合开放） | ~65%（scheduler、对抗性等价 harness、critical path 归因都在） | 中 | 高（Scepsy / TomasuLLM 逼近） |

**明确的 Top 1 推荐：增量重算语义 + 增量 benchmark。** 理由见 §5.1 与 §6。

### 三条贯穿性设计原则（本报告所有推荐都遵守）

1. **因变量必须是"构造上可验证"的量，不能是"译文好不好"。**
   成本（请求数/token/wall）、字节级等价、注入缺陷回收率、失效逃逸数、崩溃后模型调用数——
   这些都能离线复现；"译文质量"不能，而且仓库自己已经**刻意**不提供这类指标：
   `eval/metrics/reference.py` 直接 `raise NotImplementedError`，`eval/metrics/aggregate.py`
   明确不合成 `quality_score`，docs 写明「不用 LLM 自己给自己打总分」。
2. **OCR 只做出现在 source span 定位链路里。** 事实是：`document_ir.SourceSpan` 已经用
   `grounding_quality ∈ {exact, strong, approximate, page_only, unresolved}` 把"有几何/无几何"
   做成了**拒绝制**（`document_ir.py:36-55`），`spatial_index.align_blocks` 在歧义时主动弃权
   （`spatial_index.py:81-91`）。这是"锚定"而非"识别"，正好符合约束。
3. **优先复用仓库已有的"方法论资产"，论文的边际成本才低。**
   仓库已经具备：确定性 replay provider（离线、输出稳定）、字节级等价门禁、
   差分等价门（20,000 例 × 16 维）、对抗性测试替身（专门用来推翻"盲替身下的 100% PASS"）、
   真实已完工 workload fixture（239 段 / 61 批）、107 个测量脚本。这三条在这个仓库里
   意味着"做受控实验"的成本远低于一般项目。

### 0.1 证据可信度告警（立项前必须先行处理）

本次审计过程中实测到三条会直接影响研究可信度的事实，**任何方向在动工前都要先过这一关**：

1. **工作树与 HEAD 有 251 个路径不一致**（`git status --porcelain | wc -l = 251`），
   且审计期间 `core.py`（15682→15637 行）与 `folith/knowledge.py`（609→481 行）在会话中被改动过。
   本报告的行号以当前工作树为准，写代码前需重新核对。
2. **存在"文档领先于代码"的真实案例**：`docs/knowledge-feedback-efficiency-pass.md` 描述的
   事件驱动触发器（`should_run_knowledge_feedback` / `batch_knowledge_signals` /
   `named_source_forms` / `skip_reasons`）在 `docs/` 中出现 2 次，但在当前
   `folith/knowledge.py` 与 `core.py` 中**出现 0 次**；仅存于 `tmp/kf-efficiency/backup/`。
   当前实际生效的是纯节奏式 `feedback_due`。
   ⇒ 该文档 §A 的 −42.6% 调用降幅**无法从当前工作树复现**。
3. **因此**：本报告推荐的三个方向，其工程量与可行性都按**代码**而非文档估算；
   Top 1 / Top 2 的第一步都应当是一次"文档主张 ↔ 代码 ↔ 可复现数字"的三方对账，
   并把对账结果写成测试（仓库已有这种做法：`scripts/phase8_terminology_differential_gate.py`
   的 20,000 例差分等价门就是范本）。

> 这既是风险也是机会：仓库里已经有大量"被测量过但未被形式化"的机制，
> 而论文需要的正是把它们从散文式报告变成可执行的契约与断言。

---

## 1. 项目真实能力盘点（只列已验证项）

### 1.1 已被**量化**的机制（有实测数字，不是估算）

| 机制 | 实测结论 | 证据位置 |
|---|---|---|
| 持久化写放大 | 5,000 段：快照 1250→3 次；写放大 655.1×→2.33×；累计写入 8958MB→24.46MB；`state_commit` 165.56s→0.138s | `docs/persistence-performance-round7-journal.md`；`folith/storage/state_journal.py` |
| journal 崩溃恢复 | crash 后重放**零模型调用**；截断尾行丢弃、中间坏行停止；游标+`extend.base` 幂等 | `tests/state_journal_test.py:198,218,234,258,308,334,368,490,529` |
| 关键路径归因 | n=10,000：81.5% wall 是阻塞；`review_input_packet_build` k=1.71、`select_target_context` k=2.00（累计 O(n²)），超线性项占 35.5% CPU | `docs/translation-critical-path-attribution.md`、`-evidence.md` |
| 审校重叠流水线 | Balanced +30.5% / Review-Heavy +44.8% / Gen-Heavy +18.9% wall 降低；**但语义不等价**：8 段中 4 段分叉、跨 3 批、不自我愈合 → `review_overlap=false` 保持默认 | `docs/reviewer-overlap-pipeline.md`、`docs/reviewer-overlap-semantics.md` |
| 审校成本与收益 | 审校占输入 token **69.1%**（152,122/220,121）；61 批中 50 批（82%）无任何 finding；严重 finding 产出率 **6.6%**；第二次 live run 命中**不同批次**（3 批，仅 1 批重叠） | `docs/review-efficiency-pass.md` §A/§F |
| 确定层 vs 语义缺陷 | 注入 18 个缺陷 × 3 次：确定层只抓 6/18（33%）；terminology drift 0/9、entity swap 0/6、number change 0/3、negation flip 0/9、hallucination 0/9 全盲 | `docs/review-efficiency-pass.md` §G；`scripts/review_defect_recall.py` |
| 审校包瘦身 | −26.2% review 输入 token；review-only 类召回 31/36→32/36（McNemar p=1.000）；packet 与其 `input_fingerprint` 逐字节不变 | `docs/review-efficiency-pass.md` §H；`tests/review_packet_slimming_test.py` |
| 知识反馈降频 | 61→35 次调用（−42.6%）、token −41.7%，专名提示 16→16 零损失，四个端到端 run **目标摘要比特级相同** | `docs/knowledge-feedback-efficiency-pass.md` §A |
| 术语 pattern 悬崖 | G=4096→4097：编译数 1.00→3.00、CPU 0.606s→1.686s（+178%）；修为「每 revision 每 pattern 编译一次」后 cliff 消失 | `docs/terminology-pattern-scaling.md`；`scripts/phase8_terminology_differential_gate.py` |
| 并发代价 | 并发 2/3 提速 1.50×/1.53×，但 3/15、5/15 批看到与串行不同的 `previous_target` | `docs/translation-throughput.md` |
| OCR（仅作边界参考） | 单本 1882 阿拉伯语扫描，10 段人工转写：Tesseract best 21.67% / Paddle 26.34% CER → 修正口径 18.71% / 24.03% | `benchmark/README.md`、`docs/ocr-engineering-report.md` |

### 1.2 已实现但**未被量化**的关键契约

- **依赖指纹与失效传播**：review event / finding / human decision 的 `input_fingerprint`；
  `stale_segment_ids` + `freshness_status ∈ {current, partial_stale, stale}`；
  `dependency_change` 语义；`superseded_by_review_event_id`（`folith/translation_evidence.py:69-200`、
  `mark_runtime_review_stale`）。交付就绪态区分 `not_run/missing/failed/stale/current`
  （`translation_evidence.py:411-420`），**只有 current 放行**。
- **内容寻址的请求缓存**：`prompt_fingerprint = sha256(canonical{version, provider, model,
  temperature, response_format, system, user})`（`folith/translation_cache.py:52-70`）；双轴有界
  （4096 条 + 8,000,000 字符）、单条 200k 拒绝、`merge_from` 支持多写者。
  **整请求哈希 ⇒ 命中在语义上必然可靠，但任何上下文位移都必然 miss。**
- **append-only 状态日志**：唯一 mutation `translation_batch_committed`，一行一 fsync，
  重放是 `(snapshot, journal)` 的纯函数。
- **交付快照身份**：`translation_truth_hash`（只哈希 source/initial_target/target 三元组）、
  `state_identity`（刻意排除 `final_qa`/`dependency_impact`，避免审校确认制造假分叉）、
  逐资产 sha256 且读取时复核。
- **文档 IR 与锚定**：17 种 block 类型、5 级 grounding quality、8 种 grounding method；
  分段契约 `RULES_VERSION = 5` + `_gate_sentence_parts` 无损覆盖门（不满足则拒绝切分）；
  span 在 merge/split/undo 后仍可传播且**有测试**（`tests/document_understanding_test.py:148-167`）。
- **术语治理**：candidate/provisional/locked/rejected 生命周期、`translate`/`preserve` 行为、
  scope（`global`/`document`/`section:`/`segment:`）、compliance 检查、`stale_segments_for_glossary`。
- **TM**：exact + normalized 两级，**必须显式给目标语言**，语言未知即不命中（发布阻断级约束）；
  **无 fuzzy、无 context 判别、无 concordance**。
- **可观测性**：`folith/trace.py` 分层 span（wall + CPU + items + token 字段），
  metadata 做 PII 有界化（长文本 → char_count+hash+40 字符预览），但事件是**采样**的
  （DETAILED / 顶层 / error / operation 含 batch / ≥5ms），且 **无成本字段**；
  `folith/usage.py` 是 exact-vs-estimated（`ceil(chars/4)`）双账本。

### 1.3 明确**未实现**的能力（实验设计中不能当成已有）

DOCX 原 package surgical round-trip、TM V2（fuzzy/context/concordance）、
**跨版本文档对应与增量翻译（Source Update, 路线图 v0.7.0）**、完整 CAT QA Profile、
PPTX/XLSX/HTML/XML 适配、XLIFF。grep `source_update|cross_version|surgical|concordance|xliff`
在 `folith/` 下均为空。

**注意**：§1.2 的"失效传播"是**给定受影响集合之后**的传播；从"文档被改了"到"受影响集合是什么"，
仓库里**完全没有**。这正是 Top 1 的缺口，也正是它值钱的原因。

---

## 2. 八个机制的"研究潜力"筛查

| 机制 | 现有实现（已验证） | 是否可量化 | 作为**独立论文主线**的潜力 | 结论 |
|---|---|---|---|---|
| **context selection** | `compile_context_packet`（`folith/context.py:716`）：**纯位置式滑动窗口 + 预生成 section digest，无检索/BM25/embedding/相关性打分**；previous_source/next_source 各限 8 条、previous accepted targets 限 8、去重（含与 accepted source 交叉去重）；预算 `max(5200, min(12000, max_chars×3))` = 7200 字符；超预算时**按固定顺序从后往前丢**：next-source → prev-target → prev-source → entity hints，当前批永不截断 | 成本可量化；质量不可量化（无 reference 指标） | **低**。EMNLP 2025 Findings 已做 context-strategy ablation；且因变量会落到"译文质量"，违反约束 | **不作为主线**，降级为 Top1/Top2 的**消融维度**（同时它是 Top1 的关键事实：实测**每次翻译提示 86% 是重复上下文，只有 13% 是待译原文**） |
| **cache / reuse** | `prompt_fingerprint` 整请求哈希 + 双轴有界 LRU + 多写者 merge + 统计入 state | 命中率/节省可量化 | **低**。KV/语义缓存 2023–2026 已饱和（Prompt Cache、CacheGen、CacheBlend、RAGCache、GPTCache） | **不作为主线**，作为 Top1 的**基线之一** |
| **incremental execution** | 只有"给定受影响集合后的失效传播"；**无** edit→affected-set、**无**跨版本对应、**无**重算引擎 | 完全可量化（且可做到字节级） | **最高**。检索bottom line：**没有 LLM 流水线增量重算的 benchmark，也没有形式化处理** | **Top 1** |
| **TM / 术语** | TM exact+normalized；术语生命周期/scope/compliance/stale；revision 键控 pattern 编译 | 违规数、复用率、成本可量化 | **低**（术语约束生成已被 AIDAterm@ACL2026 Industry 占位，WMT 有常设 terminology track）。仅"历史批准不等于新位置获批"这一**复用安全性**是可量化的开放窄缝 | **不作为主线**，术语/ TM 作为 Top1 的**依赖节点**与 Top2 的**信号源** |
| **multimodal document processing** | Document IR v1、10 角色版式分类器、无损切分门、span 传播、VLM 结构化页面理解（已默认用于新扫描任务，但自评"production acceptance remains open"） | 结构保真目前**无任何量化指标** | **中低**。任何以 OCR/VLM 转写准确率为主线的做法直接违反约束；且其 benchmark 是单本书 10 段、助手转写 | **只保留 anchoring 用途**；结构保真可作为 Top1 的**等价性维度** |
| **review / evaluation** | 7 信号确定门 + `skipped_low_risk` provenance；blocking/actionable/informational；注入缺陷 harness；分层盲抽样（40 随机 + 4×10 风险层）；就绪门 | 完全可量化（回收率/精度/成本） | **高**。检索结论：**部分开放**——"把审校分流表述为交付物上的每单位代价缺陷回收，并用客观 catch/precision-recall 评估"未被发表 | **Top 2** |
| **state / versioning** | journal（typed mutation + 游标幂等 + 崩溃语义）、不可变版本化快照 + 逐资产 sha、迁移带失效、`translation_truth_hash`/`state_identity` | 完全可量化 | **中**。作为工程已饱和（Temporal/DBOS 式 durable execution）；且 2026 的 *Resume Means Resume* 已把 checkpoint/interrupt/resume 契约形式化并机器校验 | **合并进 Top 1**（作为"重放边界 + 写放大"的成本维度） |
| **telemetry** | trace 分层 span（wall/CPU/items/token，无 cost）、资源采样 1.5s、usage 双账本、critical-path 归因（k 指数）、`.regression-logs` 只记测试 FAIL | 完全可量化 | **低**（单独成文）；但它是上面三条的**使能设施** | **作为方法论基础设施**，不单独成文 |

---

## 3. 候选方向逐个评估

> `A–H` 对应检索任务的方向编号。每个方向给出：研究问题 / 真实 novelty / 现有 foundation /
> 需补工程 / 实验怎么做 / 核心指标 / 风险 / 是否值得。

### A. Context selection 作为方法 → **否掉**
- **RQ**：token 预算下如何给上下文单元排序以最大化边际收益？
- **真实 novelty**：低。`Exploring Context Strategies in LLMs for Discourse-Aware MT`
  （EMNLP 2025 Findings）已系统消融 prev-sentence / summary / entities；`Lost in the Middle`、
  LLMLingua、Selective Context 已覆盖压缩与位置效应。唯一空白是"带成本模型的边际效用排序 + 逐单元归因"，
  但这要求一个**质量因变量**。
- **致命问题**：Folith 刻意没有 reference-based 指标（`eval/metrics/reference.py` 抛
  `NotImplementedError`），质量评估只能走向人工/主观 → 直接违反用户约束；且统计功效上，
  需要远大于 239 段的语料与大量人工标注。
- **结论**：**否掉**。保留为 Top1/Top2 中的消融维度（context 半径、glossary 注入、synopsis 开关）。

### B. Cache/reuse + incremental execution → **Top 1（拆分：缓存机制否掉，增量语义保留）**
- **RQ**：源文变化后，最小可信重算集是什么？如何在不重算全部的前提下**证明**交付物与全量重算等价？
- **真实 novelty**：**高**。检索的独立负面结论：*"没有任何 benchmark 度量文档编辑下 LLM 流水线的最小重算，
  也没有形式化处理"*。缓存机制本身饱和（Prompt Cache / CacheGen / CacheBlend / GPGCache），
  构建系统理论成熟（Build systems à la carte、self-adjusting computation、Enzyme IVM）——
  这些是**要引用而不是要重新推导**的祖先。
  最近邻 `Language Twin`（Applied Sciences 2026）：7 层版本化状态、scoped human-edit propagation、
  +39.2% prompt token 降幅，但**只有 17 段 / 3 个 bundle 的 pilot，无失效语义、无 TM、
  无 minimality 或写放大结果**。
- **现有 foundation**：§1.3 列出的"给定受影响集合后的传播" + `prompt_fingerprint` +
  journal 幂等重放 + `translation_truth_hash`/`state_identity` + 逐资产 sha +
  `spatial_index.align_blocks`（歧义弃权式对齐）+ 分段合约与无损门 +
  已量化的写放大/O(n²) 故事。
- **需补工程**（按依赖顺序）：① 文档编辑算子与版本差分（insert/delete/replace/reorder/split/merge）；
  ② **段级源文对应**（复用 `align_blocks` 的弃权思想：文本相似度 + 结构 + 邻接单调性，
  歧义一律降级为"必须重算"）；③ **影响半径模型**：哪些上下文元素**能**改变段 i 的输出
  （context 半径 0/1/2/批，glossary scope，section digest 变更范围）；
  ④ 重算引擎：计算受影响集合 → 复用其余 → 把下游 review/TM/交付标 stale；
  ⑤ **重算报告**（reused / recomputed / review-refreshed / ambiguous）作为审计产物。
- **实验怎么做**：
  - E0 **健全性**：确定性 replay provider 下，对「编辑算子 × 规模 n ∈ {100, 1k, 5k, 10k}」断言
    交付物与全量重算**比特级相同**（除预期改动）；报告逃逸数（目标 0）。
  - E1 **最小性/成本**：重算率（重算段/总段）、LLM 调用数、token、wall；
    基线 = recompute-all、当前整请求缓存、脏后缀传播、仅 TM 复用；
    消融 = context 半径、依赖粒度（batch/segment/window）、glossary scope。
  - E2 **传播半径实测**（本文最新的量）：在位置 p 制造 1 段编辑，测量发散**实际传播多远**，
    按 context 策略分层。这一量在审校重叠实验里已被观测到（跨 3 批），但从未被系统测量。
  - E3 live provider 在 239 段真实 workload 上复现成本/token；再用合成放大到 10k 段验证 scaling。
  - E4 方法学对照：重放确定性 provider（判等价）vs live（判成本），二者必须分开报告。
- **核心指标**：健全性逃逸数（0）、重算率、节省 token/调用比例、**误失效率 vs 漏失效率**、
  review 刷新成本、wall、写放大、传播半径分布。
- **风险**：(a) 对应关系判错会导致不健全 → 用弃权兜底，并把"歧义"计入重算（保守但健全）；
  (b) 模型非确定性混淆等价性 → 确定性 replay provider 已存在，可把语义主张与成本主张分离；
  (c) 被评审读成"把 build system 套到 LLM 上" → 用 benchmark + 传播半径 + 语义依赖
  （上下文是无界语义依赖，不是文件路径依赖）来正面回应。
- **值得投入吗**：**是，最高。** 且与产品自身 v0.7.0 Source Update 里程碑**同一份工程**，双重收益。

### C. Interleaving / pipelining with strict ordering → **Top 3**
- **RQ**：能否让生成与审校重叠（30–46% wall 收益），同时**保证**提交产物与严格串行执行等价？
- **真实 novelty**：**中**。管线化作为系统常识已饱和（Parrot@OSDI'24；2026 年 Scepsy、HeraSys、
  TOPAS、TomasuLLM）。仅"在显式输出排序契约下重叠语义校验器与生成器 + 成本模型 + 逐阶段 critical path 归因"
  这一窄组合是开放的。
- **现有 foundation（本方向最独特的资产）**：`folith/review_scheduler.py`（快照、
  `max_in_flight` 背压、有序提交屏障、`hidden_review_ratio`）；**对抗性等价 harness**
  ——项目自己发现"盲替身（永远返回空 findings 的 reviewer + 忽略上下文的 translator）"
  让"100% invariants PASS"毫无意义，于是换成 context-sensitive translator + adversarial reviewer，
  以**逐批 generation context**（而非最终 digest）比较；已实测 4/8 段分叉、跨 3 批、不自我愈合；
  以及 previous-target 契约 `reviewed/committed`（`folith/context.py:582-598`）。
- **需补工程**：① **read-set 捕获**（生成与审校各自真正读了哪些状态，带指纹）——
  review 侧已有 `input_fingerprint`，需扩到生成侧；② 提交时**校验**：read-set 指纹与
  当时已提交状态比对，不匹配则失效并**有界回滚**重算（保持背压与屏障）；
  ③ 回滚策略对比（仅重算失效单元 vs 重置屏障）；④ 把等价性写成**可执行测试矩阵**。
- **实验怎么做**：延迟档（gen/rev = 80/160、100/100、160/80）× 策略
  （sequential / Strategy A 无校验 / Strategy A + 校验 / Strategy B）× 装置
  （对抗性替身 vs 确定性替身）；规模扩展 n。
- **核心指标**：等价性违规数（0）、校验后**保留的加速比**、每千段额外调用/token、
  陈旧窗口长度、传播半径、`hidden_review_ratio`。
- **风险**：**最高**（Scepsy / TomasuLLM 逼近），且缺少形式保证就会退化为工程。缓解：
  定位为"**产物型**流水线的语义等价保序推测"，把保证做成机器可校验测试，
  并把"盲替身会给出假 PASS"作为方法学教训写进论文（这是领域内真实存在的评测缺陷）。
- **值得投入吗**：**是，但排第三**——方差最大，产品价值是二阶（已建成的 opt-in 功能）。

### D. Cache-aware / reuse-aware scheduling → **否掉**
- **RQ**：按共享前缀分组请求以最大化命中率。
- **真实 novelty**：**无**。Preble（2024）、SGLang/RadixAttention、CachedAttention、DualMap（2026）、
  TOPAS（2026）、*Not All Tokens Are Worth Caching*（2026）已把前缀感知放置、路由、驱逐、
  负载均衡的权衡发表透。唯一"可辩护的差异"是 workload 特征（一个大文档前缀 + 数千个严格有序短查询），
  但那是**表征差异，不是机制贡献**。
- **结论**：**否掉**。（Folith 也没有 serving 层可优化——它是本地单进程流水线。）

### E. Structured verification（确定性检查 vs 语义审校的责任边界）→ **并入 Top 2，不单独成文**
- **RQ**：哪些不变量可被确定性检查覆盖，哪些必须语义判断？两层联合的**边际**召回是多少？
- **真实 novelty**：**中**。组件饱和：格式约束解码、LLM-as-judge 可靠性、
  Neuro-Symbolic Verification（2026）、metamorphic testing × LLM 综述（2026）。
  开放部分就是"边界 + 实测分工"。
- **现有 foundation**：**非常强**。注入缺陷 harness（18 缺陷 × 3 次）、
  确定层 6/18（33%）且对 5 类语义缺陷**全盲**、审校 6.6% 批命中率、包瘦身消融 + McNemar、
  就绪门要求每段有 current review。
- **结论**：**并入 Top 2**。它的经验内核正是 Top 2 的动机，拆成两篇会互相抢内容。

### F. HITL review triage under budget → **Top 2**
- **RQ**：固定语义审校预算 B（调用/token/人时）下，应把哪些单元送审，以最大化
  **确定性层在构造上不可能发现**的缺陷的回收率？可达的 recall@B 前沿是什么？
- **真实 novelty**：**中高**。检索结论：*"把文档翻译的审校分流表述为预算约束下每单位投入的期望错误减少，
  融合确定性失败 + QE + self-consistency，并用客观 catch/precision-recall 评估"* ——这一表述未被发表。
  两个必须切割的邻居：`Selective Annotation`（ICLR'23）、`Learning to Defer in Congested Systems`（2024）。
  **差异化论证**：AL / QE / deferral 优化的是**训练标签效用**且**oracle 是确定的**；
  这里优化的是**可交付产物上的审校产出**，且 **oracle 本身是随机的**——Folith 已实测同一文档两次
  live run 命中不同批次（3 批，仅 1 批重叠）。"随机 oracle 下的分流"是真实差异点。
- **现有 foundation**：`folith/review_gate.py`（7 信号 + 稳定机器可读 reason + `skipped_low_risk` provenance）；
  注入缺陷 harness；live yield 脚本（`scripts/review_yield_live.py`）；
  分层盲抽样（`eval/sampling/blind_review.py`：40 随机 + 术语密集/修复触发/长难/依赖上下文各 10）；
  风险门实测**0% 分流率下漏掉 4/4 严重 finding**（这就是现成的负结果基线）。
- **需补工程**：① 把注入缺陷套件**语料化**（类别 × 位置 × 上下文条件可控，多文档多语言对）；
  ② 廉价送审前预测子：生成自一致性（k 采样）、跨温度/跨模型分歧、QE 分（CometKiwi 或 LLM-QE）、
  确定信号密度、段落新颖度/歧义特征、前序批 finding 率；③ 带**显式预算与 provenance** 的分流策略；
  ④ 重复运行下的 recall@B 与每检出缺陷成本（必须报置信区间，因为 oracle 随机）。
- **实验怎么做**：基线 = always / 仅确定门 / random@B / 分层均匀@B / TM 跳过；
  消融 = 单信号 + leave-one-out；受控 = 同语料同注入缺陷、3–5 次重复；
  敏感性 = B ∈ {5,10,25,50,100}%；二级验证集 = live run 自然产生的真实 finding 集合。
- **核心指标**：缺陷回收率（总体 + 分类，尤其 review-only 五类）、误跳过率（对 blocking 的假阴性）、
  审校调用/token 节省、**每检出缺陷成本**、run-to-run 方差 / oracle 一致率。
- **风险**：(a) 被 selective annotation / learning-to-defer 抢；(b) 注入缺陷的生态效度被质疑
  → 用真实 live finding 做二级验证；(c) **收益可能真的很有限**（审校只在 6.6% 的批次有产出），
  必须把交付物设计成"**刻画 + 前沿**"而不是"一个方法"——"没有廉价信号能预测审校产出"
  本身就是有价值的负结果，但一篇论文仍需正面贡献。
- **值得投入吗**：**是**——到论文的路径最短，harness 基本现成。

### G. Durable long-running agentic workflows → **否掉**
- **RQ**：LLM 流水线的崩溃一致重放、幂等、成本感知重放边界。
- **真实 novelty**：**低**。Temporal/DBOS 式 durable execution + event sourcing 是成熟工业模式；
  且 2026 的 **Resume Means Resume** 已把 checkpoint/interrupt/resume 语义做成机器校验的一致性契约。
- **结论**：**否掉作为主线**，把 journal/写放大作为 Top 1 的**成本维度**保留（它有实测 99.5% 写入降幅，
  但没有文献定义的比较基线，单独成文危险）。

### H. Terminology-constrained decoding / glossary injection → **否掉**
- **RQ**：术语约束注入与一致性。
- **真实 novelty**：**无**。**AIDAterm**（ACL 2026 Industry）已是"4 个顺序 agent
  （Analysis / Translation / Post-editing / **Review**）+ 每阶段 glossary 注入 + 99.4% 术语准确率 +
  击败 WMT25 Terminology 全部 20 个提交 + 原生 XLIFF 落地"。加上 WMT 常设 terminology track 与
  TermJudge 等 2026 指标工作，这个坑已经满了。
- **结论**：**否掉**。术语只作为 Top 1 的依赖节点与 Top 2 的缺陷类别来源。

### 其他一并否掉
- **OCR/VLM 转写准确率为主线**：违反约束；且 benchmark 是单本书 10 段、助手转写（自我披露"非阿拉伯语专家"），
  不足以支撑主张。
- **主观译文评价 / 访谈 / 翻译学分析 / MTI 报告式评价**：违反约束；仓库自己也禁止 LLM 自评，
  并规定"盲法人工成对评审"。
- **"Agentic 翻译工作台"作为架构主张**：AIDAterm + 2024–2026 agentic translation 浪潮已覆盖。
- **纯测量报告（pipeline 成本画像）**：无新颖性；降级为 Top 1 的 benchmark 贡献。
- **DOCX surgical round-trip 保真**：目前是设计文档，代码为 0；且更偏 DocAI 工程而非可辩护的科学问题。
  可作为 Top 1 的"结构不变量"维度，但不做主线。

---

## 4. Top 3 详细方案

### 4.1 Top 1 — 增量重算语义与 benchmark（Incremental Recomputation for LLM Document Pipelines）

**一句话**：把「文档改了一段，最少要重新调用几次模型、并且仍能证明交付物与全量重算等价」做成一个
有形式定义、有 benchmark、有策略前沿的问题。

- **研究问题（三个子问题）**
  - RQ1（契约）：在何种依赖模型下，"重算集 S 是健全的"可以被精确定义并机器校验？
  - RQ2（最小性）：对给定编辑算子，可达到的最小重算集是什么？现有粒度（整请求哈希 / 批 / 段 / 窗口）
    各损失多少？
  - RQ3（影响半径）：上下文扰动的**实际**传播半径是多少？它是否可被上界化，从而使"局部重算"既健全又非平凡？
- **真实 novelty**：检索确认**无 benchmark、无形式化处理**；最近邻 Language Twin 只是 17 段 pilot、
  没有失效语义。与 build systems / IVM 的区别：LLM 阶段的依赖是**语义的、无界的、非显式的**
  （一个批次的输入包含"此前所有已提交译文的窗口"），因此经典脏标记传播在这里会
  ① 过度失效（上下文一改，下游整条链全脏）或 ② 不健全。这个张力就是论文的核心。
- **现有 foundation（可直接复用）**：`prompt_fingerprint`（整请求内容寻址，命中在语义上必然可靠）；
  已实测的**部分失效**行为：warm rerun 下 **60/61 批命中、仅 1 批重译**（`docs/translation-efficiency-pass.md` §5.2），
  说明"整请求哈希"在**同一版本内**确实给出免费的局部失效；
  `mark_runtime_review_stale` + `dependency_segment_ids` + `partial_stale` + supersession；
  交付就绪态 `not_run/missing/failed/stale/current`；journal 幂等重放（零模型调用，已测）；
  `translation_truth_hash` / `state_identity` / 逐资产 sha256；`align_blocks` 弃权式对齐；
  `RULES_VERSION=5` + 无损切分门；已量化的写放大与 O(n²) 归因。
  **缺的正是另一半**：跨版本文档对应（edit → affected set）与"在受影响集合之外复用"的引擎。
  ⚠️ 注意 `folith/section_scheduler.py` **并未接入运行时**（只有 tests 与一个 benchmark 引用它），
  不要把它当成已有的调度地基。
- **需补工程**：见 §3.B 的 ①–⑤。最小可用版本 = 编辑算子 + 段级对应（含弃权）+ 上下文半径 1 的重算引擎
  + 重算报告。
- **实验与指标**：见 §3.B 的 E0–E4 与指标表。**核心两数**：
  ① 健全性逃逸（必须 0）；② 在保证 0 逃逸的前提下，相对 recompute-all 的节省比例与**影响半径分布**。
- **落地节奏（6–12 个月）**
  1. 阶段 1（1–2 月）：定义依赖模型与"健全/最小"判据；把现有失效传播包装成可测试契约；
     建立 edit-op × size 的**差分基准**（确定性 provider 下断言比特级等价）。
  2. 阶段 2（2–4 月）：实现段级源文对应（弃权式）+ 上下文半径模型；跑 E0/E1/E2，出第一版前沿。
  3. 阶段 3（1–2 月）：live provider 成本复现 + 10k 段 scaling + 写放大对照。
  4. 阶段 4（1–2 月）：论文写作。**明确不做**：TM fuzzy 检索、DOCX round-trip、OCR 任何改进。
- **风险与缓解**：见 §3.B。额外一条：如果"最小性"上界被证明平凡（例如上下文字段实际使所有段互相依赖），
  **这本身是可发表的负面结论**——它把"长文档 LLM 流水线的局部性"变成一个被测量的性质。

### 4.2 Top 2 — 预算约束下的语义审校分流（Verification Economics）

- **研究问题**：在预算 B 下最大化"确定层构造上抓不到"的缺陷回收率；可达前沿与可预测性边界在哪？
- **真实 novelty / 差异化**：随机 oracle（同一文档两次 live run 命中不同批次）+ 交付物产出（非训练标签效用）
  + 确定层互补性约束（不能为了省钱牺牲结构性检查覆盖）。
- **现有 foundation**：§3.F。已有一个**现成的负结果**：仅确定门分流会漏掉 4/4 严重 finding，
  且对 5 类语义缺陷 0 召回。
- **需补工程**：§3.F 的 ①–④。最小可用版本 = 注入缺陷语料化 + 3 个廉价信号（自一致性、
  跨温度分歧、LLM-QE）+ recall@B 曲线。
- **实验与指标**：§3.F。**核心两数**：recall@B（尤其 review-only 五类）与每检出缺陷成本。
  必须报 run-to-run 方差，否则随机 oracle 会制造假结论。
- **风险**：见 §3.F。**若 6 个月内发现没有任何廉价信号优于"按段落长度/风险层均匀抽样"，
  应果断转向 Top 1，并把该负结果写成短文。**

### 4.3 Top 3 — 保序推测执行（Equivalence-Preserving Speculation）

- **研究问题**：能否把"生成/审校重叠"从 30–46% 提速但语义不等价，变成**可证等价且仍提速**？
- **现有 foundation**：§3.C（含项目自己发现的"盲替身假 PASS"教训与对抗性 harness）。
- **需补工程**：§3.C 的 ①–④。最小可用版本 = 生成侧 read-set 指纹 + 提交期校验 +
  "仅重算失效单元"回滚 + 可执行等价测试矩阵。
- **实验与指标**：§3.C。**核心两数**：等价违规（必须 0）与**校验后保留的加速比**。
- **风险**：**三者中最高**（Scepsy / TomasuLLM）。若无法给出比"事务内存套到 LLM 上"更强的贡献，
  应降级为 Top 1 的一个章节（"推测执行作为重算的一种主动形式"）。

---

## 5. 最终推荐

### 5.1 Top 3 排序

1. **增量重算语义 + 增量 benchmark（推荐 Top 1）**
2. **预算约束下的语义审校分流（Verification Economics）**
3. **保序推测执行（Equivalence-Preserving Speculation）**

### 5.2 为什么 Top 1 是增量重算

1. **唯一被检索确认为"真空"的方向**：没有 benchmark，没有形式化处理。其余候选都有直接占位论文。
2. **地基最厚且是"别人没有"的那种厚**：内容寻址指纹、依赖级失效传播、纯 IO 幂等重放、
   逐资产哈希、弃权式对齐、无损切分门——这些拼起来正好就是"增量流水线"的骨架，
   缺的只有"编辑 → 受影响集合"这一段。
3. **因变量天然客观**：字节级等价 + 重算率 + token/调用 + 写放大。不需要任何译文质量判断，
   完美符合约束；确定性 replay provider 让语义主张可离线复现。
4. **与产品路线图同轨**：v0.7.0 Source Update 就是这件事。工程投入双重收益，不存在"论文做完代码丢掉"。
5. **风险可控且有下行保护**：即使"最小性"上界平凡，得到的也是对长文档 LLM 流水线局部性的
   首个系统测量（传播半径），仍是有效贡献。

### 5.3 立刻要做、且能反过来加强论文的两件基建

1. **补齐相关工作脊柱**。现状：`docs/literature-evidence-spine.md` 是内部审计、无外部引用；
   `folith/legacy_literature.py:45-145` 的 23 条是某个示范论文的**主题**文献（无人机/感官研究），
   不是本项目的相关工作。做 Top 1 前必须建立 document-level MT / LLM systems /
   incremental computation 三条引用线。
2. **把增量基准打包成可复现资产**。仓库已有确定性 provider、真实 239 段 workload、
   等价门禁与 2,065 个测试——只需把 `benchmark/` 从"OCR 引擎对比"扩展出一个
   `benchmark/incremental/`（edit-op 矩阵 + 期望等价断言 + 成本表），论文的 artifact 评价
   与工程回归就是同一件事。

### 5.4 检索局限（需要人工复核）

本次相关工作检索时 `web_search` 不可用（HTTP 402），仅通过 OpenAlex API / arXiv API /
ACL Anthology 的关键词元数据匹配完成；DBLP 被反爬、Semantic Scholar 429。
**同义但措辞不同的论文可能被漏掉**，建议在动工前对 Top 1（incremental）与
已否掉的 G（durability）各做一次人工交叉复核。Top 3 的占位论文
（Scepsy、TomasuLLM）已直接读到摘要，风险判断可信。

---

## 附录 A：否掉清单（一页速查）

| 方向 | 否掉理由 |
|---|---|
| Context selection 作为方法 | EMNLP 2025 Findings 已做消融；因变量被迫落到主观译文质量 |
| Cache-aware / prefix-cache 调度 | Preble / TOPAS / DualMap / SGLang 已饱和；本地单进程无 serving 层 |
| Terminology-constrained decoding | AIDAterm（ACL 2026 Industry）+ WMT 常设 track 已占位 |
| Durable execution / checkpoint-resume | 工业成熟 + *Resume Means Resume*（2026）已形式化 |
| Structured verification 单独成文 | 组件饱和（Neuro-Symbolic Verification 2026、metamorphic testing 综述 2026）；并入 Top 2 |
| OCR / VLM 转写准确率 | 违反约束；benchmark 规模与转写质量不足以支撑主张 |
| 主观译文评价 / 访谈 / 翻译学分析 | 违反约束；仓库自身禁止 LLM 自评 |
| "Agentic 翻译工作台"架构主张 | AIDAterm 与 agentic translation 浪潮已覆盖 |
| 纯 pipeline 成本测量报告 | 无新颖性；降级为 Top 1 的 benchmark |
| DOCX surgical round-trip 保真 | 目前代码为 0；偏工程，科学问题不清晰 |

## 附录 B：主要证据索引

- 审校经济学：`docs/review-efficiency-pass.md`（§A/§F/§G/§H/§I/§J）、
  `scripts/review_yield_live.py`、`scripts/review_defect_recall.py`、`folith/review_gate.py`
- 重叠流水线与语义裁定：`docs/reviewer-overlap-pipeline.md`、
  `docs/reviewer-overlap-semantics.md`、`docs/reviewer-overlap-semantics-evidence.md`、
  `folith/review_scheduler.py`、`tests/reviewer_overlap_semantic_equivalence_test.py`
- 失效传播与就绪门：`folith/translation_evidence.py`（`mark_runtime_review_stale`、
  `translation_review_readiness`）、`docs/translation-core-phase2-completion.md`
- 持久化与 journal：`folith/storage/state_journal.py`、`folith/snapshots.py`、
  `tests/state_journal_test.py`、`docs/persistence-performance-round7-journal.md`
- 上下文与缓存：`folith/context.py:716`（`compile_context_packet`）、
  `folith/translation_cache.py:52`（`prompt_fingerprint`）
- 关键路径与遥测：`folith/trace.py`、`folith/critical_path.py`、`folith/usage.py`、
  `docs/translation-critical-path-attribution.md`
- 文档 IR 与锚定：`folith/document_ir.py`、`folith/spatial_index.py`、`folith/segmentation.py`、
  `folith/document_understanding.py`、`folith/pdf_preview.py:206`
- 基准与评测：`benchmark/translation-efficiency/workload.json`、`eval/run_ab.py`、
  `eval/metrics/*`、`eval/sampling/blind_review.py`
