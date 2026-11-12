# Folith TU Contract v1：翻译单元与 inline 标签互操作语义契约

状态：2026-10-26 建立契约与确定性校验器实现（Issue #143，实现见
`folith/interop/`，测试见 `tests/tu_contract_v1_test.py`，fixture 见
`tests/fixtures/tu_contract/`）。本契约是 XLIFF / SDLXLIFF / MQXLIFF
适配器 Issue 与 v0.9.0 端到端验收的前置地基。

**版本 v1.1**：身份作用域与摘要规范形式落地（`CONTRACT_VERSION=2`、
`LEDGER_VERSION=3`）；`locked` 的落点**本版未改**（仍是 v1 的
`{"excluded": True}`，只读行裁定不在本版）。真实语料实测见
`.loop/reports/P3-2.md` 与 `.loop/reports/P3-1.md`。

## 定位与承诺

> Stable identity, verbatim preservation, or explicit rejection.

从 Trados / memoQ 迁移的职业译员携带的是共享同一副骨架的 XLIFF 系双语
文件：translation unit（源文 + 译文 + 状态 + 元数据）与 inline tags。本契约
定义**格式无关**的 TU 互操作语义，作为所有 CAT 双语格式适配器的共同地基，
避免每条导入/导出路径各自发明段落身份与标签语义，杜绝 round-trip 静默损坏。

- 依赖方向只能是 **适配器 → 契约**：`folith/interop/` 不得 import 任何具体
  格式解析器，也不得 import `core`；适配器负责格式解析并把结果投影成契约
  形状。
- 段落真值只有一套：任务 state 的 `pairs`（`source` / `target` / 审校与
  保护字段）+ `pair_signature`（`folith/translation_candidates.py`）+ 人工
  保护标记（#92/#94 候选事务契约：批量替换默认跳过受保护段落）。契约层
  描述外部 TU 如何映射到它，**禁止第二套段落真值**。
- 结构完整性校验全部是确定性代码（可由文件对比或测试判定），不依赖 LLM
  自评。

## 1. 段落身份（Segment identity）

**实例身份不含源文哈希**（#143 裁定）：身份只由外部 TU id 决定，源文摘要只作为
**独立的变化证据**，不参与身份、不拼进 key。

```
external_id     = file_scope "/" pct(unit_id) [ "/" pct(seg_id) ]
identity        = "tu:" + external_id                 # 第 1 个实例
instance n ≥ 2  = "tu:" + external_id + "#" + n       # 源文变化后的显式新实例
source_digest   = sha256(canonical_source_text(source_text, tags))  # 变化证据，与身份分开存放
```

- **身份作用域由契约规定，适配器不得自选拼法**：`external_id` 由
  `file_scope` + 单元 id（+ 可选分段 id）组成。每一段都用同一条
  `pct()` 转义（`[A-Za-z0-9._~-]` 之外一律 `%XX`，见
  `folith/interop/tu_contract.py` 的 `pct()`），因此拼接**单射**：
  `i:a/b%2Fc` 与 `i:a/b/c` 不会撞车，含 `/`、`%`、空格、中文的 id 也能原样
  往返（`compose_external_id` / `split_external_id`）。
- **`file_scope` 三级取值（固定顺序，取第一个可用来源）**：

  | 顺序 | 取值 | 适用 | 规则名 |
  | --- | --- | --- | --- |
  | 1 | `"i:" + pct(<file>@id)` | memoQ 的 `mq:id`；稳定、与路径无关 | `id` |
  | 2 | `"o:" + pct(<file>@original)` | SDLXLIFF 只有 `original`；整串参与，**不做 basename** | `original` |
  | 3 | `"p:" + <file> 的 1 基位置` | 两者都缺 | `position` |

  第 3 级回退必须**可见**：适配器必须把 `FILE_SCOPE_FALLBACK` 警告写进
  `ImportReport.warnings`（`file_scope_warnings()` 生成），不得静默。
- **`seg_id` 只在单元真的有 ≥2 个 `<mrk mtype="seg">` 时才拼**，用**单元内
  序号** `seg1`…`segN`，不用 `<mrk@mid>`：真实 SDLXLIFF 的 `mid` 是文件级
  计数器（同一单元的段不从 1 开始），文件里插一段会让后面所有段换身份。
  判据看 `<mrk>` 数量，不看 `<seg-source>` 是否存在（真实样本里有空
  `<seg-source>`）。单 `<mrk>` / 无分段 = 两段式身份。
- **`DUPLICATE_FILE_SCOPE`（文件级警告，不是拒绝）**：同一导入批次里出现
  两个解析到同一 `file_scope` 的 `<file>` 时，适配器必须在导入前产出一条
  文件级警告，`reason` 含完整 `file_scope` 与位置，由用户选择只导入其中一份。
  典型真实案例：`mt.mqxliff` 与 `pe.mqxliff` 是同一份文档的 MT / PE 阶段
  导出，`file@original`、`file@id`、51 个单元 id 与 51 条源文全部相同；按
  `file_scope/unit_id` 组身份会得到 51 个 `DUPLICATE_EXTERNAL_ID`——逐单元
  报错不可操作，文件级警告才是可行动的。该警告**不放行**重复身份：用户若
  仍同时导入两份，重复单元照旧按单元级拒绝处理。
- **`DUPLICATE_EXTERNAL_ID` 的 `reason` 必须带 `file_scope`**：让用户看到
  "两份文件都自称同一作用域"，而不是只看到一个裸单元 id。首次出现的单元
  照常导入，账本保留首次出现的实例，重复单元不进 `identities`。
- **可复现**：同一文件重复导入（external_id 不变），身份逐字节相同；**只改译文
  不改身份**（摘要只吃源文，target 根本不进摘要，也不进身份）。
- **摘要只是变化证据**：`source_digest` 单独出现在身份记录、账本与再指派报告里，
  用来判断"源文是否变过"；它既不参与身份计算，也不出现在身份字符串里。
- **源文变化 = 显式新实例 + 报告**：同一 external_id 的摘要与当前实例不符时，
  分配**新实例身份**（`tu:<id>#2`、`#3`…），并记入导入报告 `reassignments`：
  `reason="source_changed"`、`previous_identity` / `identity`、
  `previous_digest` / `digest`、`previous_confirmed_state` / `confirmed_state`。
  禁止让旧身份"漂移"成新内容，也禁止按内容相似度猜测"还是同一段"。
- **旧确认状态不自动继承**：新实例的 `confirmed_state` 一律从 `False` 开始
  （`reviewed`/审校状态不复制、不自动置真）；旧实例及其确认状态进账本
  `history` 留档供报告与回溯，但**旧身份永不复用**——哪怕源文后来改回原文，
  也会分配下一个新实例（`#3`），而不是悄悄回到 `tu:<id>`。
- **摘要必须由适配器显式传入**：`reconcile_identities()` 不再从
  `source_text` 兜底重算；缺 `source_digest` 直接报错（"禁止猜语义"）。
  适配器必须用 `canonical_digest()` 算好后传入，见 §2.1。
- 身份账本（`IdentityLedger`，版本 3）：
  `external_id → {identity, source_digest, instance, confirmed_state,
  file_scope_rule, history}`；同一实例重复导入命中身份与摘要 →
  `stable=True`（"本次导入证明了身份未变"），`mark_confirmed()` 只记当前实例。
  版本 1（身份里含源文哈希）与版本 2（键空间不含 `file_scope`）的旧账本
  **显式拒绝**：必须重新导入以建立新账本，不得沿用旧身份。
- 无外部 id 的单元由适配器**显式赋号并报告**；若仍未赋号，契约按
  `IDENTITY_MISSING` **单元级拒绝**（`unit_ref` 用 1 基位置引用 `#1`、`#2`…），
  其余单元继续导入——单个坏单元不得中断整份文件。
- 再指派**不是拒绝**：单元照常导入，但报告必须让用户看见"这些段被视为新段落，
  需要重新定位/重译/重审"。
- **已知限制（SDL 跨机器）**：`<file>@original` 是机器相关的绝对路径
  （`C:\\Users\\…\\Studio 2019\\…`）。换机、换项目目录会让 SDLXLIFF 的
  `file_scope` 变化，从而整份文件换身份。memoQ 的 `mq:id` 不受影响。
  SDLXLIFF 的跨机器身份连续性是 v1.1 **不保证**的性质，契约只能如实记录，
  不能靠规则修掉。

可测试判定：同一 fixture 导入两次 → 身份列表与身份账本 JSON 逐字节相同
（`ledger_first.to_json() == ledger_second.to_json()`），且身份里不含摘要；
只改译文再导入 → 身份不变、`stable=True`、账本 JSON 不变；篡改任一单元源文
再导入 → 该单元得到 `tu:<id>#2` 新身份并出现在 `reassignments`（新旧身份与
新旧摘要齐全、`previous_confirmed_state=True` 而 `confirmed_state=False`），
其余单元身份不变；把源文改回原文 → 又是新实例（`#3`），旧身份不复用；重复
外部 id → `DUPLICATE_EXTERNAL_ID` 单元级拒绝且只产出一个身份（`reason` 含
`file_scope`）；空外部 id → `IDENTITY_MISSING` 单元级拒绝且其余单元照常导入；
`@id` 优先于 `@original`，两者皆缺 → `p:<位置>` + `FILE_SCOPE_FALLBACK`
警告；两个 `<file>` 解析到同一作用域 → 一条 `DUPLICATE_FILE_SCOPE` 文件级
警告且导入继续；`compose_external_id` / `split_external_id` 对含 `/`、`%`、
空格、中文的分量往返一致；同一 `<file>` 内真重复 id 仍走单元级拒绝。

**真实语料证据**：9 个样本（SDL 4 + memoQ 5）共 193 个 `trans-unit`，
**0/9** 个文件含多个 `<file>`；memoQ 5/5 有 `mq:id`，SDL 0/4（只有
`original`）；同一文件内重复单元 id 0 个。`mt.mqxliff` / `pe.mqxliff` 是
唯一的跨文件碰撞对（51 个单元 id 与 51 条源文全同），由文件级
`DUPLICATE_FILE_SCOPE` 警告处理。**多 `<file>` 场景没有真实样本**，只用
合成样本覆盖（依据 XLIFF 1.2 规范：`trans-unit@id` 的作用域是所在
`<file>`）。

## 2. Inline 标签语义

- **内部表示**：标签在契约文本中只以**编号占位符**出现（`U+E000 <tag_id>
  U+E001` 私有区定界，正常文本不可能出现，无需转义协议）；原始负载（源文件
  中的序列化字节/串）与占位符编号分离存放于 `InlineTag.payload`，导出时由
  适配器按编号取回原负载写回。**`tag_id` 是适配器自赋的不透明字符串**，不要求
  是整数：真实 SDLXLIFF 有 `ph1` / `pt5` 这类符号 id，memoQ 的 `bpt id=1` 与
  `ept id=1` 还会复用同一个文件标签 id。契约只要求同侧内唯一，`encode_placeholder`
  不做 `int()` 转换。**禁止伪造负载**：同编号标签的译文负载必须与源文逐字符
  一致。
- **成对/自闭合校验**（`validate_tag_sequence`）：占位符与标签定义一一对应；
  成对标签（open/close）必须同 `code_name`（适配器给出的配对键：memoQ 用
  `rid`，SDLXLIFF 用 `<g>@id`）在**同一侧内**闭合。XLIFF 1.2 的 `bpt`/`ept`
  是**序列模型而不是树模型**：真实 memoQ 会先连续开多个 `bpt`、再按 id 关
  `ept`，配对区间交叠但每个键恰好开一次、关一次——交叠不是缺陷。只有"关一
  个没开过的键"（同侧 `rid` 不自洽）与"开了没关"（id 不闭合）才算错误。
- **译文标签类型计数 ⊆ 源文**（`validate_target_tags`）：`tag_id` 与配对键
  都是**每侧独立**的计数器。真实 memoQ 源文 `<bpt id="1" rid="1">` 对应的
  译文是 `<bpt id="1" rid="2">`，`<ph id="3">` 的 `id` 在两侧也不对应。因此
  契约**不跨侧比较编号、配对键或 `payload`**（跨侧逐字符比较会把 12 个真实
  memoQ 译文判成"伪造负载/改写配对"）；跨侧只比较 `open` / `close` /
  `self_closing` 三种类型的**数量**：译文不得超过源文——允许整对省略或重排，
  不允许凭空多出。译文的配对自洽仍按各自一侧校验，半对必报错。**跨侧顺序
  不比较**：memoQ 会为目标语序重排格式跨度（`document.mqxliff` id=9 是真实
  证据），要求顺序一致同样会误判合法文件。
- 标签校验不通过的单元按 `TAG_STRUCTURE_INVALID` 显式拒绝（单元级），
  不得截断半句入库（沿用 TMX `_tmx_seg_text` "宁可不导入，不导入半句"）。

可测试判定：交叠跨度（先开 A、再开 B、先关 A、再关 B）合法且零错误；关闭
未开启、开启未闭合、占位符与定义不一一对应、译文类型计数超过源文（多出标签 /
整对改写成自闭合 / 自闭合改写成一对）各产出精确错误；真实语料 9 个文件、
193 个 `trans-unit`（167 个带译文）0 误判。**"交叠"不再是错误**：严格 LIFO
树检查是 v1 的误判根因，已删除。

## 2.1 源文摘要的规范形式

`source_digest` 吃**规范形式**，不是文件里的原始字符数据：

```
canonical_source_text(text, tags) =
    纯文本（逐字符，只把 CRLF/CR 归一成 LF）
  + 占位符 U+E000 {o|c|s} <十进制序号> U+E001

序号：按占位符在文本中首次出现的顺序 1,2,3…；
      成对标签（open/close，按 code_name 配对）共用同一个序号，
      self_closing 各自一个序号。
source_digest = sha256(utf-8(canonical_source_text))
```

字段名仍叫 `source_digest`，计算函数是 `canonical_digest(text, tags)`；v1 的
`source_digest(text)` 只保留为**旧口径 API**（复现反例/读旧数据），不得用于导入。

- **只归一换行**：`\r\n`、`\r` → `\n`（XML 1.0 §2.11 本来就规定解析后如此）。
  **不折叠连续空白、不 trim、不动制表符**。真实 `SamplePhotoPrinter` 46 个
  单元里有 30 个源文是纯空白，共 8 种逐字符形状；按 `" ".join(text.split())`
  折叠后会产生 **24 次撞车**（把"3 个空行"判成"没变"）。契约宁可多报一次
  变化，也不能少报。
- **不含 payload**：`<g>` 的 `code_name`、`<ph>` 的原始串、`mq:ch` 的 `\n`
  都不参与。真实 memoQ 37 个单元的"换行"只出现在 `mq:ch` 的 payload 里，
  排除 payload 后段文本换行为 0。
- **不含文件标签编号**：`@id` / `@rid` 不参与；跨文件重排、CAT 工具重新生成
  标签计数器都不改变摘要。
- **`xml:space` 不影响摘要**：摘要吃解析后的字符数据。memoQ 133/133 个
  `<source>` 带 `xml:space="preserve"`，SDL 0/60；若按属性分两套口径，同一
  内容会有两个摘要。`xml:space` 属于导出要原样写回的元数据（`PreservedFragment`）。
- **CDATA 与转义文本不区分**：解析后同为字符数据；但导出不得**新增** CDATA。
- **译文完全不进摘要**：target 不参与身份，也不参与源文摘要。
- **私有区必须拒绝**：源文纯文本里出现 `U+E000` / `U+E001` 时，规范形式不再
  单射，适配器必须按 `TAG_STRUCTURE_INVALID` 拒绝该单元（`canonical_source_text`
  会抛 `ValueError`）。
- **`reconcile_identities` 不再兜底**：缺 `source_digest` 直接报错，绝不从
  `source_text` 静默重算。v1 的 `source_digest(source_text)` 保留为**旧口径
  API**（复现反例与读旧数据），不得用于导入。

**真实语料实测（决定性数字）**：9 个样本、193 个单元、123 个含行内标签。
把整份文件重新序列化（命名空间前缀改写、自闭合写法变化）并把行内标签
`@id`/`@rid` 整体 ×7（保序单射、配对不变）后：

| 口径 | 摘要变化单元 | 含义 |
| --- | --- | --- |
| v1 逐字符（含文件标签号） | **115 / 193** | 115 次再指派、审校/确认状态清零 |
| v1.1 规范形式 | **0 / 193** | 身份与确认状态全部保留 |

115 而不是 123：`SamplePhotoPrinter` 的 8 个单元标签 id 是符号（`ph1`…），
扰动只改整数 id；真实重新生成会连符号一起改，实际暴露面是 123/123 个含标签
单元。该数字由 `tests/tu_contract_real_corpus_test.py` 钉住
（`test_real_corpus_legacy_digest_would_reassign_115_units` /
`test_real_corpus_canonical_digest_has_zero_changes`）。

可测试判定：标签 id / payload / 配对键改写后 `canonical_digest` 不变；
`\r\n` 与 `\n` 同摘要；连续空格、前后空白、制表、空行数任何一项变化都必须
改变摘要；私有区字符与不自洽标签序列抛错；真实语料往返 0 变化、带账本重导入
`reassignments == []` 且全部 `stable=True`。

## 3. 状态映射

双向映射表（`STATUS_MAP` / `map_cat_status` / `map_folith_status`）。别名
归一后才查表：**大小写、首尾空白、连字符与下划线等价、CamelCase →
snake_case 拆分**（XLIFF 1.2/2.0 的 `state` 取值用连字符，如 `signed-off` /
`needs-translation`，与表内下划线写法同义；memoQ 写 `ManuallyConfirmed`、
Trados 写 `ApprovedSignOff`，不做 CamelCase 拆分就只剩
`manuallyconfirmed` / `approvedsignoff`，与下划线词表永不相等）。拆分只是
拼写归一：归一后的值仍须**逐字**命中规范名或别名。**表外状态一律显式拒绝**
（`StatusMappingError` → `UNMAPPABLE_STATUS` 拒绝路径），绝不猜近似语义，
也不设"未知值默认通过"的兜底。

| 规范状态 | 别名（示例） | → Folith pair 字段 | 说明 |
| --- | --- | --- | --- |
| `confirmed` | final, reviewed, signed_off（`signed-off`）, approved, manually_confirmed（`ManuallyConfirmed`）, proofread（`Proofread`）, reviewer1_confirmed（`Reviewer1Confirmed`）, approved_sign_off（`ApprovedSignOff`）, approved_translation（`ApprovedTranslation`） | `target_provenance="human_edit"`, `human_edited=True`, `reviewed=True`, `review_status="reviewed"`, `from_tm=False` | CAT 已确认 = 人工作者且人工定稿：进库即受人工保护（批量替换跳过） |
| `translated` | draft（`Draft`）, pre_translated（`PreTranslated`）, translation_complete, machine_translated（`MachineTranslated`）, partially_edited（`PartiallyEdited`）, needs_review_translation, needs_review_adaptation, needs_review_l10n | `target_provenance="generated"`, `human_edited=False`, `reviewed=False`, `review_status="not_reviewed"`, `from_tm=False` | 已翻未确认 = 可用工作译文，仍须走本应用审校门禁 |
| `untranslated` | new, empty, not_translated, not_started（`NotStarted`）, initial, needs_translation（`needs-translation`） | 同 `translated`，**且 `target=""`** | 待生成。`target=""` 是**破坏性字段**：导入时必须**整体覆盖**单元字段，不得与旧译文 merge（否则旧译文残留、单元会被重新判成 `translated`） |
| `locked` | read_only, protected | `{"excluded": True}` | 适配器不得生成译文：单元不进 `pairs`，登记进既有排除账本 `excluded_segments`；排除不计为已翻译 |

**真实语料实测**（`tests/fixtures/mqxliff/` 5 个文件、`tests/fixtures/sdlxliff/`
4 个文件）：memoQ 共 **363 个状态 token / 7 种取值**（`NotStarted` 160、
`ManuallyConfirmed` 91、`MachineTranslated` 72、`PreTranslated` 32、
`PartiallyEdited` 3、`Proofread` 3、`Reviewer1Confirmed` 2）；Trados 共
**8 个 token / 4 种取值**（`Translated` 3、`Draft` 2、`ApprovedSignOff` 2、
`ApprovedTranslation` 1）。全部可映射、**0 个表外值**；回归防线见
`tests/tu_contract_real_corpus_test.py`（逐文件 × 逐状态词参数化）。

memoQ 的**锁定是独立标志**（`mq:locked="locked"` + `translate="no"`），
不是状态词：语料里 9 个锁定容器各自另有独立的 `mq:status`，
`translation_status.mqxliff` 的 6 个锁定段覆盖 6 种不同状态。适配器必须把
`mq:locked` 当正交标志单独读取，**不得**把该属性当 `status` 喂给映射表
（`locked` 规范状态的最终落点见文末"v1.1 已知限制"）。

反向映射（导出方向）：`excluded → locked`；`reviewed → confirmed`；有译文
未审 → `translated`；空译文 → `untranslated`。人工保护标记不改变状态词：
CAT 状态描述定稿程度，保护标记描述"谁动的"，两者正交。

字段取值严格来自现有 pairs 契约的既有取值域（`target_provenance ∈
{generated, human_edit, reviewed, tm_approved, candidate_adopted}`），
不发明新值。

可测试判定：映射表每个规范状态、每个别名、大小写变体全分支覆盖；**连字符
写法（`signed-off` / `needs-translation` / `initial` / `needs-review-*`）与
下划线写法等价**；**CamelCase 写法（`NotStarted` / `ManuallyConfirmed` /
`Reviewer1Confirmed` / `ApprovedSignOff`）与下划线写法等价**；表外状态（含
空串、`needs-review`、`rejected`、拆分后仍不在词表的 `TotallyUnknownStatus`）
必抛 `StatusMappingError`；真实语料（363 + 8 个 token）逐状态词参数化，100%
命中且映射结果在规范词表内；反向映射四分支全覆盖，且 `untranslated` 的闭环
不靠测试手工补 `target`。

## 4. 元数据保全

- 适配器无法解释为契约语义的属性/节点，必须作为 `PreservedFragment`
  （`location` + 原样 `payload`）**逐字节携带**；`location` 是适配器自述的
  结构位置。契约层不解释其内容，只保证"进得来、出得去"。
- 导出时全部保全片段必须原样写回。**这一条必须由确定性核对判定，不能只靠
  结构 diff**：适配器无法投影的节点在源树与导出树里通常都不存在，`diff` 两
  侧都看不见它——这类丢失恰好是结构 diff 唯一看不见的丢失。契约因此提供
  `verify_preserved(exported_tree, preserved)`：按 `payload` **逐字符精确
  匹配**导出树中的等价子树，找不到即判失败；匹配口径是"`payload` 等于导出树
  某节点子树按 `json.dumps(node, ensure_ascii=False, sort_keys=True)` 序列化
  的结果"，`location` 只用于报告、不参与匹配；空 `payload` 无法证明写回，
  显式报错。`no_op_verdict(..., preserved=...)` 把缺失片段计入
  `unexplained`/`missing_preserved`，**且不接受白名单豁免**（把"静默丢弃"
  白名单掉就违背本节）。
- 禁止以"看起来是注释/上下文"为由改写、归一或丢弃保全内容。

可测试判定：`synthetic-notes` fixture 的注释与上下文组按原样出现在报告
`preserved` 清单；导出树缺失任一片段（或片段内容被改写）时
`verify_preserved` 返回缺失清单，`no_op_verdict(..., preserved=...)` 判
失败并给出精确位置；**同一场景下不带 `preserved` 的结构 diff 仍为空**
（用测试固定这条边界，避免文档再次声称 diff 覆盖了它）。

## 5. 拒绝语义

无法证明安全往返的结构必须**显式拒绝并报告原因**，按影响范围分级：

| 级别 | 拒绝码 | 触发 |
| --- | --- | --- |
| 文件级（出现任一即整文件拒绝，不产出部分结果） | `UNPARSEABLE_FILE` | 文件整体不可解析 |
| 文件级 | `UNKNOWN_NAMESPACE` | 未知命名空间影响单元结构（适配器无法可靠切割单元） |
| 文件级 | `PENDING_TRACK_CHANGES_FILE` | 文件级未决修订容器 |
| 单元级（该单元跳过并报告，其余单元继续） | `UNPARSEABLE_NODE` | 单元内不可解析节点 |
| 单元级 | `UNKNOWN_NAMESPACE` | 单元内的未知命名空间节点 |
| 单元级 | `PENDING_TRACK_CHANGES` | 单元内未决修订（绝不代用户接受或拒绝修订） |
| 单元级 | `UNMAPPABLE_STATUS` | 状态不在映射表内 |
| 单元级 | `TAG_STRUCTURE_INVALID` | 标签配对/嵌套校验不通过 |
| 单元级 | `IDENTITY_MISSING` | 无外部 id 且适配器未显式赋号（`unit_ref` 用 1 基位置引用 `#n`） |
| 单元级 | `DUPLICATE_EXTERNAL_ID` | 同一 `file_scope` 内重复的外部 TU id（同一 `<file>`，或两个解析到同一 `<file>` 的文件；身份映射不再单射，拒绝重复出现的那个单元；`reason` 带 `file_scope`） |

`ImportReport` 汇总接受清单、拒绝（含范围/码/单元/原因）、身份再指派与
保全清单；`file_rejected` 为真时调用方**不得**产出任何可交付结果——这一点
由契约强制：`add_rejection` 收到文件级拒绝即**清空 `accepted`** 并记一条
`warnings`，`to_json` 也不会输出部分结果。拒绝码与范围在
`ImportReport.add_rejection` 处集中校验，写错即抛错。

**警告码（不是拒绝，导入继续）**：

| 警告码 | 触发 | 生成函数 |
| --- | --- | --- |
| `FILE_SCOPE_FALLBACK` | `<file>` 既无 `@id` 也无 `@original`，按位置回退为 `p:<n>` | `file_scope_warnings()` |
| `DUPLICATE_FILE_SCOPE` | 同一批次 ≥2 个 `<file>` 解析到同一 `file_scope` | `file_scope_warnings()` |

两类警告都必须写进 `ImportReport.warnings`，不得静默。警告码集合
`WARNING_CODES` 与拒绝码集合 `FILE_SCOPE_CODES` / `UNIT_SCOPE_CODES`
**不相交**：`DUPLICATE_FILE_SCOPE` 不使 `file_rejected` 为真，重复身份仍由
单元级 `DUPLICATE_EXTERNAL_ID` 兜底。

可测试判定：`synthetic-unknown-namespace` 与 `synthetic-track-changes`
fixture 产出指定拒绝码的单元级拒绝；文件级拒绝码登记到 `file` 范围即令
`file_rejected` 为真**且 `accepted == []`**；未知拒绝码被拒绝登记；
重复/缺失外部 id 各产出对应单元级拒绝（见第 1 节）；位置回退与重复
`file_scope` 各产出一条对应警告且 `file_rejected` 为假。

## 6. Round-trip 结构比较工具

适配器把"解析出的文件结构"投影成契约层的通用节点树
（`{"name", "attrs", "text", "children"}`；任何格式皆可投影）：

- `canonical_snapshot`：属性键排序等与内容无关的序列化差异在规范化时消除，
  不算差异；文本逐字符比较，不做空白归一。**契约键只有 `name` / `attrs` /
  `text` / `children`；节点字典里的其他键（`tail` / `nsmap` / `cdata` /
  `line` …）不丢弃**，而是原样收进规范化结果的 `extra` 段参与比较（嵌套值
  按确定键序排列）。否则"只改这些键"的导出会被 no-op 闸门静默判过。
- `diff_snapshots`：精确差异清单（路径 + `added/removed/changed` + 前后
  值）。路径 `/name:0/child:1@attr`、`.../node#text`、`.../node!key`
  （`!key` 即非契约键），`:i` 为子节点位置（0 基，用冒号而非方括号：方括号
  会被 fnmatch 当成字符类，白名单将无法按字面匹配路径）——插入/删除会使
  后续位置移位，这正是 no-op 验收想要暴露的。
- `no_op_verdict`：no-op 模式（解析 → 原样导出 → 再解析）下，重导出树与
  源树的 diff 必须为空，或差异全部落入**显式白名单**（`{"path": glob,
  "kinds": [...]}`，由调用方逐条声明，契约层不内置任何默认豁免）；白名单外
  差异判失败，`unexplained` 即失败原因清单。传入 `preserved=` 时同时核对
  保全片段写回（见第 4 节）：缺失片段计入 `unexplained` 与
  `missing_preserved`，**不接受白名单豁免**。
- **边界**：本工具比较的是**树投影等价**，不是字节级 XML 往返（CDATA 与普通
  文本、注释/处理指令只能靠投影约定表达）。"逐字节保全"由 `PreservedFragment`
  的 `payload` 携带，不由此工具证明。

可测试判定：`synthetic-noop-roundtrip` 判空通过；`synthetic-tampered`
（属性改动 / 丢节点 / 文本变化）输出与 fixture 内黄金 diff 逐条一致；
白名单只豁免声明的路径与类型，路径不符或类型不符均判失败；**只改
`tail`/`nsmap`/`line` 等非契约键 → `ok=False`**；导出缺保全片段 →
`ok=False`（即便结构 diff 为空）。

## 7. Fixture corpus

`tests/fixtures/tu_contract/`，带来源记录（`SOURCES.md` + 每个样本内嵌
`provenance`）。当前全部为仓库内合成样本（无需外部授权）；真实导出样本
须先取得授权并登记来源，无授权来源的样本不进入仓库。生成器是唯一权威
来源，手工编辑 JSON 会在重建时被覆盖。

**fixture 必须随 PR 提交**：测试在 fixture 缺失时直接失败（不自动重建），并
有一条测试把生成器产物与提交物逐字节比对——否则"语料已提交"这条验收不可
判定，且测试会写入仓库目录。

## 8. 单元切割（`trans-unit` 的直接子节点）

真实 memoQ 会在 `trans-unit` 里嵌 `mq:historical-unit`（历史版本）与
`mq:insertedmatch`（TM/CM 匹配溯源），二者各自带自己的 `<source>`。契约因此
把**当前段落**钉死为 `trans-unit` 的**直接子节点** `<source>` / `<target>`：

- **禁止**用 `.//source`（或等价的平铺扫描）统计当前段落。实测平铺会把历史
  版本当当前段落：`pe.mqxliff` 306 vs 51 个 `trans-unit`、`mt.mqxliff`
  167 vs 51、`document.mqxliff` 35 vs 16、`translation_status.mqxliff`
  25 vs 12。多算的单元会带着旧译文/旧状态进入工作台。
- 嵌套 `<source>` **不是静默丢弃**：`scan_trans_units(root)` 返回
  `TransUnitScan`（`units` + `nested_sources` 按容器名分类计数 +
  `nested_summary()` 报告文本）。适配器必须把 `nested_summary()` 写进
  `ImportReport.warnings`（或等价报告字段），让"跳过了 N 条历史版本"可见。
- 单元容器 = 任意深度的 `trans-unit`（`group` 可嵌套）；源文/译文取直接子
  节点中的第一个同名元素；没有 `<source>` 的单元进入 `units_without_source`，
  由适配器显式拒绝，不得静默跳过。

可测试判定：9 个真实样本的直接子节点计数逐一钉死（16/3/51/51/12/5/46/4/5，
嵌套合计 19/0/116/255/13/0/0/0/0）；平铺计数等于直接计数 + 嵌套计数，且
4 个 memoQ 文件平铺数大于单元数；合成树里的 `historical-unit` /
`insertedmatch` 各计 1 且报告文本同时列出两个容器名。

## 失败 / 回滚行为

契约层为新增纯库 + 文档 + fixture，无写入路径，不触碰任务状态；回滚 =
移除 `folith/interop/`、`docs/tu-contract-v1.md`、
`tests/fixtures/tu_contract/` 与测试文件，不影响任何现有功能。

## v1.1 已知限制（不阻塞本契约，但下游不得误读）

以下各项本版本**没有**定义或没有覆盖，按"不猜语义"原则留给后续 issue /
裁定，适配器不得据本节自行发明口径：

- **`locked` 的落点**：第 3 节的正向映射仍是 v1 的 `{"excluded": True}`
  （排除账本），本版**没有**改成只读行。二者如何统一、锁定段落是否"工作台
  可见只读"，待用户裁定（P3-1 建议只读行，但不在本版）。
- **多 `<file>` 场景没有真实语料**：9 个真实样本里 0 个含多个 `<file>`。
  `external_id = file_scope/unit_id` 的单射性只用**合成样本**覆盖，依据是
  XLIFF 1.2 规范（`trans-unit@id` 的作用域是所在 `<file>`），不是仓库样本。
- **SDLXLIFF 跨机器身份不保证**：`file@original` 是机器相关的绝对路径；
  换机/换目录会让 `file_scope` 变化，整份文件换身份。memoQ 的 `mq:id` 不受
  影响。这是如实记录的限制，不是可修的 bug。
- **规范形式不是段落语义的完备编码**：它只保留纯文本与标签类型序列，
  不同 payload / 不同 `code_name` / 不同文件标签 id 但同类型序列的单元会得到
  同一摘要。这是**刻意选择**（摘要证明"段落内容没变"，不证明"标签负载没变"）；
  标签负载的往返由 `payload` 原样写回与结构 diff 负责。
- **标签配对的扩展语义**：配对键由适配器通过 `code_name` 提供（memoQ 的
  `rid`、SDLXLIFF 的 `<g>@id`），契约层不解析 `payload` 里的格式属性，因此
  无法独立重建 `rid`；`payload` 不跨侧比较（每侧按自己的负载原样写回）。
  无法表达 XLIFF 2.0 `<pc>`/`<sc>`/`<ec>`，"允许整对省略"与"写回必须禁止
  整对省略"的严格度也不可配（属后续版本扩展）。
- **没有字节级往返保证**：no-op 验收比较树投影等价（见第 6 节末）。

## Out of Scope

- 任何具体格式适配器（XLIFF / SDLXLIFF / MQXLIFF 各有独立 Issue）。
- 翻译核心、TM、审校真值的任何改动。
- 项目包（SDLPPX / MQOUT 等）解包。
