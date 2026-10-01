# Folith·译页：Agentic Translation Workspace 开发蓝图

本文定义 Folith 的产品方向、整体架构和开发顺序。2026-09-14 更新：近期先完成 UI 全面打磨并正式发布；发布后转向 CAT 内核，首先完成 [DOCX Contract v1](docx-contract-v1.md) 的端到端闭环。

本文的进度以当前本地工作树为依据，包括尚未提交的实现；不等同于 GitHub main 或已发布版本。本次文档更新进行了相关代码静态核对，没有重新运行产品测试或真实 DOCX 保真验收。历史 Phase 文档保留为实现记录；后续开发优先级以本文 §7 为准。

它不是一次重写计划，也不是把“Agent”包装成一个新的聊天窗口。它的目标是把现有的长文档翻译能力重新组织成一个更明确的产品：

> Folith·译页是一个由 Agent 执行本地化工作、由人类控制语言质量和交付责任的 Agentic Translation Workspace。

Folith 的核心对象不再是“翻译实践报告”，而是一个可恢复、可审查、可交付的本地化工作台项目。

## 1. 产品收敛

### 1.1 面向谁

首要用户不是需要写 MTI 报告的人，而是需要把较长、结构复杂、术语密集的内容稳定地翻译成可交付资产的人：

- 独立译者和小型翻译团队；
- 研究人员、产品团队和内容团队；
- 需要本地运行、保留人工控制权的 AI 翻译使用者；
- 需要处理 PDF / DOCX，并要求术语、风格和审校可追溯的人。

MTI 学生可以继续使用 Folith，但这是一个具体用户群，不应继续决定产品的首页、信息架构和运行时边界。

### 1.2 一句话价值

**让 Agent 处理长文档翻译中的重复工作，让人类只在真正需要判断的地方介入，并始终知道最终交付依据是什么。**

### 1.3 产品不是什么

Folith 不应成为：

- 通用聊天式写作工具；
- 自动替用户做最终语言判断的黑箱翻译器；
- 论文生成器或文献管理器；
- 包含所有格式、平台和 Agent 协议的本地化平台；
- 将每个内部状态都暴露给用户的工作流引擎。

## 2. 核心工作模型

Folith 的主流程应从当前的“功能列表”收敛为一条 Studio Loop：

```text
Import
  ↓
Understand
  ↓
Prepare
  ↓
Translate
  ↓
Review
  ↓
Deliver
  ↺ 任何源文档、术语或人工决策变化都会回到受影响步骤
```

每一步都回答三个问题：

1. Agent 可以执行什么？
2. 人类必须决定什么？
3. 系统要保留什么证据，才能解释当前交付状态？

| 阶段 | Agent 负责 | 人类负责 | 最小产物 |
| --- | --- | --- | --- |
| Import | 解析文档、识别结构和异常 | 确认输入范围和输出语言 | `source_snapshot` |
| Understand | 建立章节、实体、风格和上下文摘要 | 修正关键文档事实 | `document_model` |
| Prepare | 提取术语候选、发现重复表达、建议风格规则 | 锁定、拒绝或修改术语和规则 | `project_memory` |
| Translate | 按受控上下文分批生成译文 | 处理风险段落和策略选择 | `translation_truth` |
| Review | 做确定性检查和独立语义审校 | 接受、修改、驳回或暂缓发现 | `review_findings` |
| Deliver | 生成资产、报告和交付清单 | 批准最终版本并承担交付责任 | `delivery_snapshot` |

这里的 Agent 是一组受边界约束的工作角色，不是一个可以自行循环和修改所有状态的总控 Agent。

## 3. 目标架构

### 3.1 四层，而不是两套产品 runtime

```text
┌────────────────────────────────────────────┐
│ Studio UI / CLI                            │
│ 项目、任务、审校、交付                     │
├────────────────────────────────────────────┤
│ Orchestration                             │
│ bounded jobs、context packet、human gates │
├────────────────────────────────────────────┤
│ Translation Core                           │
│ document、memory、translation、review     │
│ decisions、delivery、recovery             │
├────────────────────────────────────────────┤
│ Adapters                                   │
│ PDF/DOCX、provider、renderer、exporter    │
└────────────────────────────────────────────┘
```

#### Translation Core：产品真值

Translation Core 是 Folith 的唯一核心。它应拥有以下稳定概念：

- `Document`：源文档版本、结构、可翻译单元及原文件定位；当前实现仍以段落为主，目标模型随 DOCX 闭环落地；
- `Project`：跨任务的术语、风格和人工确认记忆；
- `TranslationJob`：一次具体的源文档到目标语言工作；
- `TranslationTruth`：每个翻译单元当前的译文、来源和状态；编辑/TM 单元与 LLM 的段落、章节上下文窗口分开；
- `ReviewFinding`：针对某个版本译文的可操作发现；
- `HumanDecision`：用户对发现、术语或交付的明确决定；
- `DeliverySnapshot`：绑定到特定源文档、译文和审校状态的交付版本。

现有 `folith.translation_core` 已经是正确的起点。下一步不是再造一个 `agent_core`，而是把现有 `models.py`、`checkpoint.py`、`translation_protocol.py`、`delivery.py` 和 `translation_core/` 的职责整理到这组概念下。

#### Orchestration：受限的 Agent 工作编排

编排层只负责：

- 为一个明确的任务构造 context packet；
- 调用一个指定角色的 provider；
- 校验结果是否符合该角色的输出契约；
- 写入现有真值或生成待人工决定的候选；
- 在需要人类判断时停止。

编排层不拥有第二份译文、不拥有第二份术语表，也不直接决定某个结果是否“已批准”。

推荐的最小角色集合：

```text
Context Builder → Translator → Reviewer → Repair Planner → Delivery Builder
```

其中 `Repair Planner` 只能提出候选修订。它不能自动把语义修订写回已确认译文；自动应用只允许用于确定性、可证明安全的机械修复。

#### Adapters：变化快，不能污染核心

适配器负责外部变化：

- PDF / DOCX 的读取和渲染；
- provider 和模型调用；
- DOCX / PDF / TMX / JSONL 输出；
- 本地文件持久化。

适配器可以失败、替换或暂时不支持某种格式，但不应改变核心状态语义。

### 3.2 Project 与 Job 的边界

这是 Folith 从“单次脚本”成为工作台的关键边界：

```text
Project
  ├─ glossary / confirmed terminology
  ├─ style profile
  ├─ reviewed translation memory
  ├─ human decisions / audit history
  └─ jobs/
       ├─ source snapshot
       ├─ translation truth
       ├─ review findings
       └─ delivery snapshots
```

- Project Memory 保存跨文档可复用的、已经被人类确认的知识。
- Job 保存当前文档的局部上下文、译文和审校结果。
- 生成的术语候选、模型解释和一次性 context 不应直接进入 Project Memory。
- TM 是已审校译文的受控记忆，不应被泛化成所有模型输出的缓存。

当前版本可以继续把这些结构映射到本地 `state.json`，不需要立即引入数据库。先稳定概念和依赖方向，再在真实规模证明文件存储不足时改变存储层。

## 4. Agentic Native 的具体含义

“Agentic Native”不等于让 Agent 拥有更多权限，而是让每项翻译工作都以 Agent 可执行的、边界明确的工作单元表达。

### 4.1 工作单元契约

每个 Agent job 至少包含：

```json
{
  "job_type": "translate_batch",
  "input_refs": ["source:17", "memory:confirmed", "style:default"],
  "context": {"bounded": true},
  "output_schema": "translation_batch.v1",
  "human_gate": "review_before_promotion",
  "writes": ["job.translation_candidates"],
  "evidence": ["model_call", "deterministic_checks"]
}
```

关键规则：

- 输入引用必须可追溯；
- 输出必须符合已知 schema；
- 写入范围必须提前声明；
- 需要判断的结果必须进入 human gate；
- context 只包含当前工作所需的有限内容；
- 失败可以重试该 job，但不能隐式重写已确认真值。

这足以支持 Agent 工作流，不需要现在引入通用 tool registry、复杂规划器或多 Agent 通信协议。

### 4.2 人类控制点

Folith 应把人类控制点集中在四处：

1. **Memory gate**：术语、实体和风格规则是否进入项目记忆；
2. **Translation gate**：候选译文是否成为当前译文；
3. **Review gate**：发现是否接受、修改、驳回或标记为风险接受；
4. **Delivery gate**：当前版本是否可以导出和交付。

不需要为每一个内部函数创建用户可见确认。确认点应该对应用户真正承担的语言或交付责任。

## 5. 学术能力的重新安置

Folith 的学术能力不应被粗暴删除，因为其中的 provenance、合规和渲染 QA 对某些用户有价值；但它们必须从核心产品降级为一个明确的下游扩展：

```text
Folith Core
  ├─ Translation Studio（默认）
  └─ Research / MTI Extension（可选）
       ├─ case provenance
       ├─ literature evidence
       ├─ thesis constraints
       ├─ academic report
       └─ final report QA
```

扩展只能读取 Translation Core 的正式产物：

- 源文档快照；
- 已确认译文；
- review findings；
- human decisions；
- delivery snapshot。

扩展不得重新实现翻译、术语、审校或交付。`academic_writer.py` 等模块可以先留在仓库中，但应迁移到清晰的 `research_extension/` 边界；在迁移完成前，至少通过入口和文档让普通翻译任务不再进入它们。

### 学术扩展的退出条件

当以下条件同时满足时，才考虑将扩展拆成单独 package 或仓库：

- 核心 API 已经可以独立安装和运行；
- 学术扩展不再 import UI 或翻译 runtime 的内部实现；
- 至少有一个完整的核心任务可以不加载学术模块；
- 学术扩展有独立用户和独立发布理由。

在这些条件达到前，拆仓库只会把重复维护提前发生。

## 6. 建议的仓库结构

短期不做全量 import rename。先按职责收敛目录：

```text
Folith/
├─ foliothread/                 # 未来公开 namespace；逐步迁移
│  ├─ core/                     # 稳定的领域模型和状态契约
│  ├─ orchestration/            # bounded Agent jobs
│  ├─ adapters/                 # 文档、provider、渲染和导出
│  ├─ studio/                   # UI/CLI application layer
│  └─ research/                 # 可选 MTI / academic extension
├─ folith/                 # v0.x 兼容实现，逐步缩小
├─ tests/
│  ├─ core/
│  ├─ orchestration/
│  ├─ adapters/
│  ├─ studio/
│  └─ research/
└─ docs/
```

但这个目标结构不应立即通过复制文件实现。迁移顺序应是：

1. 先定义并测试稳定的 core contract；
2. 把现有调用从 UI 和 academic 模块中抽出来；
3. 让旧 `folith` namespace 成为兼容入口；
4. 最后再移动文件和修改公开 import。

`foliothread/` 的新目录只有在至少一个真实调用已经不再依赖旧目录时才创建。否则只是重复代码树。

## 7. 当前进度与开发路线

### 7.1 当前本地基线

| 能力 | 当前实现与边界 |
| --- | --- |
| 翻译工作流 | 文档上下文、批量翻译、恢复、审校、人工决策和交付快照已有实现；不代表语言质量已胜过其他 CAT 产品 |
| Translation Core | packet / finding / decision / memory 已接入运行时，见 [Phase 2 closure](translation-core-phase2-completion.md)；继续复用唯一真值和 freshness 语义 |
| Project / Studio | 项目生命周期、项目记忆、历史任务、语言资产和工作台视图已有本地实现，部分尚未提交；剩余 UX 修复服务于真实任务 |
| TM | `core.tm_lookup` 支持 exact / normalized，项目 TM 仍以 source 字符串索引；基础 TMX 导入导出已有，尚无完整 fuzzy / context / concordance 引擎 |
| TMX 边界 | 导入尚需按语言选择方向、正确处理 inline 内容；导出为 paragraph / plaintext。这些属性描述现状，不单独代表质量等级 |
| 文档模型与 DOCX | `paras` / `pairs` 和索引仍是主要组织方式；已有 DOCX 提取及生成，不具备本契约要求的原 package surgical round-trip |
| QA | 已有确定性检查、独立语义审校和交付门禁；尚非完整 CAT QA Profile 系统 |
| Source Update | 已有依赖失效能力，尚无完整跨文档版本对应、译文迁移与歧义复核流程 |
| 验证 | 已有离线场景门禁；不能据此声称原 DOCX 保真、专业译员效率或真实模型质量已验收 |

### 7.2 近期正式发布：先完成 UI 全面打磨

用户已于 2026-09-14 确定：近期 UI 全面打磨结束后发布正式版，并在 GitHub、小红书、X 发布与宣传。此发布先于 CAT 内核里程碑，不以 DOCX Contract v1 或 TM V2 完成为前提。v0.4.0 已于 2026-09-22 发布；当前独立代码包候选版本为 v0.5.0，具体发布日期待定，范围见 release-plan.md。

发布范围、验收与渠道准备见[近期正式版发布计划](release-plan.md)。发布材料只承诺已验证的当前能力，未来 CAT 计划单独标注。这里记录发布意图，本次文档更新不执行上传或对外发帖。

### 7.3 发布后的首个内核里程碑：DOCX Contract v1

目标：在明确支持范围内，专业译员能导入真实 DOCX、翻译与审校，并拿回保留原结构的目标文档。

按一个纵向闭环实施，不先建设通用文档框架：

1. 用正文、表格、格式、超链接、脚注、页眉页脚及混合结构 fixture 确定提取和写回范围。
2. 从原 package 提取带受保护标记的单元，同时建立版本内 Instance ID 与 source anchor。
3. 让现有翻译、人工编辑、确定性检查和审校流程消费这些单元；句级编辑不削弱段落/章节上下文。
4. 在原 package 的明确范围内写回；输出能力报告，列出保留但未翻译的内容。
5. 接入现有交付门禁与快照，提供确认下一段、复制原文和问题导航等基本键盘路径。

完成标准以 [DOCX Contract v1](docx-contract-v1.md) 为准。模型、adapter、tag QA 和最小工作台交互属于同一次验收，不能分别宣称完成后留下断开的主路径。

### 7.4 后续里程碑：TM Engine V2

目标：让历史资产可检索、可解释、可安全应用。

- 先处理语言对、同源多译、来源与审校信任，以及 TMX 语言方向和 inline 内容。
- 提供 exact / normalized / fuzzy 候选及可解释差异；context 用于区分候选，concordance 支持主动查用法。
- 检索与应用分开：历史批准不等于在新位置获批，fuzzy 候选不能自动继承 reviewed 状态。
- 自动复用条件必须覆盖语言、上下文歧义与 tag 兼容；匹配分数不是翻译质量或正确率。
- 将候选接入 Translator context 和工作台应用操作；评估真实资产上的误复用、检索延迟与人工工作量。

Fragment retrieval、复杂资源优先级和大规模索引优化在实际需求证明价值后再扩展，不作为首版的前置条件。

### 7.5 后续里程碑：Source Update 与 Project Analysis

目标：客户发来 V2 后，复用明确未变的内容，定位修改和歧义。

- 版本内身份、原文件定位、跨版本对应分开；对应关系允许移动、修改、拆分、合并及未匹配。
- 第一版先覆盖明确对应的迁移，修改与歧义进入复核；不得仅凭 UUID、文本相同或相似度继承批准。
- 沿用既有 stale propagation 使相关审校失效，但不把失效机制当作版本对齐算法。
- 使用真实分段与 TM 结果提供字数/字符数、重复和匹配分布，并明确语言计数口径。
- AI 成本与复核工作量先作为有依据的估计，区分实际消耗；没有校准数据时不承诺报价或工时准确性。

### 7.6 暂后工作

PPTX / XLSX / HTML/XML 按真实客户输入排序；XLIFF 可以成为后续互操作切片，但标准 XLIFF 支持不等于 SDLXLIFF、memoQ 双语文件或项目 package 完整兼容。Alignment、完整 QA Profile、高级键盘编辑、协作权限和企业工作流均不与首个 DOCX 闭环并行铺开。

品牌维护继续，但全仓库 namespace 迁移、研究扩展拆包和更多 AI 页面不再是前置里程碑。旧任务与现有 PDF 长文工作流继续维护；PDF 不作为其他格式的内部标准表示。

模型调用保留可追溯的输入、输出和实际使用配置；“还原历史运行”与“重新请求模型得到相同输出”分开。远程模型重跑一致性不是可保证的交付承诺。

## 8. 进度与质量指标

Folith 的进度不再使用“完成了多少模块”。现有工作流基线包含三个端到端场景；新的 DOCX 契约需增加专门验收，不能用旧场景替代：

| 场景 | 必须证明 |
| --- | --- |
| 20 页 DOCX | 导入、术语准备、翻译、审校、双语交付可完成 |
| 100 页 PDF | 中断恢复、章节上下文、受影响范围重建可用 |
| 术语密集文档 | 术语确认、TM 复用、独立审校和交付清单可信 |

这些是既有工作流门禁，不验证原 DOCX package 的保真写回。DOCX Contract v1 的计划验收见 [契约验收](docx-contract-v1.md#验收与-fixtures)，当前尚未实现。

这三个场景已可执行：`tests/scenario_gate_test.py` 离线运行全部三个场景，
外加蓝图 §4.2 的 Memory / Review / Delivery 控制点。源文档由
`scripts/make_scenario_fixtures.py` 确定性重建，仓库不提交二进制 fixture。
运行方式、断言清单和门禁发现过的缺陷记录见[场景验收门禁](scenario-gate.md)。

每个场景只记录：

- 是否完成；
- 人类必须介入的次数和位置；
- 未解决的 blocking findings；
- 交付资产是否与当前译文和源文档绑定；
- 失败后能否从最近安全点继续。

Agentic Native 的核心指标不是“自动化率越高越好”，而是：

> Agent 做了更多可复用的工作，同时没有扩大人类无法解释或撤回的修改范围。

## 9. 明确暂停的工作

在核心路线完成前，暂停：

- 新增更多 academic artifact 类型；
- 新增通用 Agent planner、tool registry 或多 Agent 协议；
- 将所有模型输出保存为长期记忆；
- 继续复制 Folith 的通用翻译能力；
- 为尚无真实场景的格式增加完整 adapter；
- 为每个内部状态增加一个新的 UI fixture；
- 仅为了改名而进行全仓库 import rename。

这些工作不是永久禁止，而是要等真实使用证明它们改变了下一步决策。

## 10. 最终目标状态

```text
Folith
  └─ Translation Studio
      ├─ Project Memory
      ├─ Document Understanding
      ├─ Agent Translation Jobs
      ├─ Human Review
      ├─ Evidence-aware Delivery
      └─ optional Research / MTI Extension

Folith
  └─ legacy state / compatibility source

localize-anything
  └─ 独立的软件项目本地化工程层
```

最终用户应该只需要理解一个产品承诺：

> Folith 帮你把长文档本地化完成，并让你始终知道哪些内容由 Agent 生成、哪些内容由人确认、哪些资产可以负责地交付。

这比继续把“通用翻译”和“学术写作”维持成两个平衡的一等支柱更清晰，也比把 Folith 变成一个泛化的 Agent 平台更可执行。
