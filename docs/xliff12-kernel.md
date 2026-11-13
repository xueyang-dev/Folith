# Folith XLIFF 1.2 内核 v1：定位单元、骨架保全写回、身份账本

状态：2026-11-13 建立（P3-3，Issue #143 系列）。实现见
`folith/interop/xml_span.py`、`folith/interop/xliff12.py`、
`folith/interop/xliff12_export.py`、`folith/interop/xliff12_ledger.py`；确定性
用例见 `tests/xliff12_kernel_test.py`（构造样本）与
`tests/xliff12_real_corpus_test.py`（9 个真实样本）。实测数字与边界见
`.loop/reports/P3-3.md`。

本内核是 **P3-4（SDLXLIFF/MQXLIFF 只读配置 + 原样写回）** 的前置：它把 XLIFF
1.2 文件读成契约 v1.1 的单元与只读行，并能只改译文文本节点地写回。它**不**做
项目 UI 集成、不写 `pairs` 真值、不 import `core`。

## 1. 依赖方向与分层

```
适配器（P3-4） → 格式内核（本文件） → 契约层（docs/tu-contract-v1.md）
```

- 契约层 `folith/interop/tu_contract.py` / `structural_diff.py` 仍是**格式无关**
  的：不 import 任何格式解析器、不 import `core`；
- 本内核只依赖标准库 + 契约层：`xml_span` 是字节级 XML 扫描器，`xliff12*` 是
  XLIFF 1.2 语义；
- 段落真值仍然只有一套（任务 state 的 `pairs`）。内核产出的是**投影**：`pairs`
  行、排除清单与报告，导出按外部 id 读回这些行。

## 2. 定位单元

### 2.1 结构判据

- 单元 = 任意深度的 `<trans-unit>`（`<group>` 可嵌套）；
- 源文/译文 = 单元的**直接子节点** `<source>` / `<target>`（沿用契约
  `scan_trans_units` 的裁定：`<mq:historical-unit>`、`<seg-source>` 里的
  `<source>` 不是当前段落）；
- 一个单元没有 `<source>` → 单元级拒绝 `UNPARSEABLE_NODE`；没有 `<id>` →
  `IDENTITY_MISSING`；同一文件重复 `@id` → `DUPLICATE_EXTERNAL_ID`（解析层
  拒绝，不进 `pairs`）。

### 2.2 身份与摘要

完全按契约 v1.1，不另立拼法：

- `external_id = file_scope "/" pct(unit_id) [ "/" pct(seg_id) ]`；
- `file_scope`：`<file>@id`（任意命名空间，memoQ 的 `mq:id`）> `<file>@original`
  > 位置回退 `p:<n>`（位置回退产 `FILE_SCOPE_FALLBACK` 警告，同批次重复作用域产
  一条 `DUPLICATE_FILE_SCOPE` 警告）；
- `seg_id` 只在§5 的"真正分段"（目标侧 ≥2 个 `<mrk mtype="seg">`）时出现；
- `source_digest = canonical_digest(纯文本 + 编号占位符)`：inline 标签的
  `tag_id` 由内核按扁平顺序赋 `t1`…`tn`，**不沿用文件里的 `@id`/`@rid`**，因此
  CAT 工具改写标签编号不会改变摘要（契约 P3-2 的 115→0）；
- 状态词经 `map_cat_status` 映射；表外状态是单元级拒绝 `UNMAPPABLE_STATUS`。
  `@state` > 外来命名空间的 `@status`（`mq:status`）> `@approved`。单段单元的
  状态可以来自它唯一的子段声明（SDLXLIFF 的 `sdl:seg@conf`）。

## 3. 锁定段 = 只读行（用户裁定）

### 3.1 两条信号分开建模

| 信号 | 判据 | 落点 |
| --- | --- | --- |
| **锁定** | `<trans-unit>` 上任意命名空间里本地名 `locked` 的属性值 ∈ {`locked`,`true`,`yes`,`1`,`on`}；或状态词落在契约锁定词表（`read_only`/`protected`/`locked`）；或（单段单元）其子段声明的 `locked` | `pairs` 里的**只读行** `readonly=True` |
| **`translate="no"`** | `@translate`（可从 `<group>` 继承）为 `no` | **排除**：不进 `pairs`，登记进 `KernelImport.excluded`，`reason` 区分 `translate_no_placeholder`（纯空白源文 + 无译文，Word 换行占位）与 `translate_no`；写回入口也拒绝（原因 `translate_no`，与 `locked` 分开） |

两条信号**不共用字段**：`readonly` 只由锁定决定；`translate="no"` 永远不产生
只读行。真实语料里二者常常同时出现（memoQ 的锁定段也写 `translate="no"`），
此时**锁定优先** → 只读行，不是排除。

锁定判据是可配置的 `LockPolicy`（属性本地名、子段声明元素名、真值集合）：P3-4
的"SDLXLIFF/MQXLIFF 只读配置"替换策略即可，不需要改内核。

### 3.2 只读行形状（P3-1 §4.2 的裁定）

```python
{
  "source": "…", "target": "<文件里的原译文，逐字符>",
  "readonly": True, "readonly_reason": "locked",
  "target_provenance": "human_edit",   # 复用既有取值，不新增
  "human_edited": False, "reviewed": <按状态映射>, "review_status": …,
  "from_tm": False, "excluded": False,
  "external_segment": {"external_id", "file_scope", "unit_id", "seg_id",
                       "raw_status", "locked": True, "locked_attribute",
                       "translate": …},
}
```

- 只读行**在** `pairs` 里：占段落序号、参与上下文，不是第二套真值；
- `excluded=False`，`readonly=True` 表达"能不能改"；
- 文件里已有的译文逐字符保留（含"锁定 + NotStarted + 空译文"这一类，`target=""`）。

## 4. 写回：骨架保全的补丁导出

### 4.1 只做字节拼接，不做重序列化

`xml_span` 用 expat 只取**元素边界**（开始/结束事件、注释、CDATA、处理指令的
字节区间），文本由"内容区 − 子节点"得到。写回时把新文本编码成字节，**只替换
文本节点区间**；未落在补丁里的字节原样来自原文件。因此属性顺序、命名空间前缀、
注释位置、空元素写法、属性引号、行间空白、BOM 全部不变。

- 无待写补丁时 `XliffDocument.render()` 直接返回原字节（逐字节相同）；
- inline 标签（`bpt`/`ept`/`ph`/`x`/`g`/`mrk`/未知扩展元素）的字节作为
  **载荷**原样写回；新译文的占位符序列必须与文件逐个同序同编号，否则
  `TargetTagStructureError`（不猜"少一个标签"是不是合法译文）；
- 目标里含注释/处理指令时，文本未变 → 不动；文本要变 →
  `UnpatchableSideError`（不移动注释、不重构骨架）；
- 干净文本（无注释）里，某段原本没有任何字节（两个相邻标签之间）也能插入，按段
  边界定位、按文档顺序确定先后。

### 4.2 只读行的写回闸门

三个写回入口，全部**显式拒绝**并报出外部 id + 锁定证据（不是静默跳过）：

| 入口 | 行为 |
| --- | --- |
| `XliffDocument.set_target(id, text)` | 锁定行立即抛 `ReadonlySegmentError`（原因 `locked`）；`translate="no"` 行同样拒绝（原因 `translate_no`） |
| `export_patched(document, {id: text})` | 任何只读 id 出现即整批拒绝（即使新值等于原值） |
| `apply_plan(document, plan)` | `plan.refusals` 非空时整批拒绝 |

批量比对入口 `plan_export(document, pairs)` 是**计划器不是写入口**：只读行译文
与文件一致 → 记进 `unchanged`（原样保留，不产生补丁）；不一致 → 记一条
`ReadonlyRefusal`（`ok=False`）。只读判定以**文件**为准，不信任 pair 自报的
`readonly`：任一来源说只读且文本要变，就拒绝。

### 4.3 零差异证明（两条独立证据，缺一不可）

`verify_export(document, exported_bytes)` 同时给出：

1. **结构化对比**（`structural_diff.no_op_verdict`）：两棵树逐节点比属性/文本/
   子节点，白名单只豁免 `<target>` 子树里的文本值变化（`*target*#text`）；
   白名单外任何差异都是失败。文本投影在父节点的 `text` 上（文本节点不占子节点
   序号），否则插入文本会让后续兄弟位置移位、把"同位置另一个元素"报成 changed。
2. **字节骨架对比**（`compare_preservation`）：
   - `skeleton_segments`：`<target>` 内容之外的每个字节段必须逐段相同；
   - `markup_signature`：目标内容里每个 inline 标签/注释的字节必须逐段相同。

结构化对比**不能**单独证明骨架保全：把文档交给 ElementTree 重序列化，结构可以
零差异，但属性顺序/前缀/注释/空元素写法会被改写——`skeleton_segments` 才抓得住
（测试 `test_naive_elementtree_reserialization_fails_skeleton_check`）。
`readonly_bytes_report` 另外逐个核对只读段的 `<target>` 元素字节未变。

## 5. 子段级接口（SDLXLIFF 形态）

SDLXLIFF 的锁定在**子段**上（`trans-unit/sdl:seg-defs/sdl:seg@locked="true"`，
`sdl:seg@id` 对应 `<mrk mid>`），不在 `trans-unit` 上。内核模型：

- 目标侧每个 `<mrk mtype="seg">` 是一个 `Subsegment`：`seg_id`、`mid`、
  自己的目标文本、锁定证据、状态（`conf`）；
- **真正分段**（≥2 个 `<mrk>`）→ 三段式 `external_id` 与按子段的行
  （`build_import(..., subsegments=True)`）；子段锁定 → 该子段行只读；
- 单 `<mrk>` 单元仍用两段式身份（契约 `segment_ids` 的裁定），但它的子段锁定
  反映到**单元行**上（整段就是这一个子段）；
- 多段单元只有**全部**子段锁定才让单元行只读；部分锁定时单元行不是只读行，并给
  一条可见警告 `PARTIAL_SUBSEGMENT_LOCK`（要求调用方按 `subsegments=True`
  逐段处理，否则会覆盖锁定子段）。

P3-3 只实现到接口与构造样本 + 真实 QuotesSample 的两条单段锁定；完整的 SDLXLIFF
读写配置属 P3-4。

## 6. 身份账本

`SegmentLedger`（`xliff12_ledger.py`）回答"这次导入相对上次，哪些段落是新的、
哪些消失了、哪些只是换了位置"：

- 键 = `external_id`；值 = `identity` / `source_digest` / `instance` / `ordinal` /
  `location` / `first_generation` / `last_generation` / `state`；
- 变化分类：`added`、`removed`、`moved`（文档序号或位置字符串变了）、
  `returned`（消失过又出现）、`source_changed`（摘要变了 → 实例递增，
  identity 变成 `tu:<id>#<n>`）、`stable`（摘要没变，位置可能变——位置变化另记
  `moved`）、`duplicates`（同一批里重复外部 id）；
- 实例规则**复用契约**（`identity_key` / `instance_identity`），交叉断言钉在测试
  里（同一批单元 `reconcile_identities` 与账本给出的 identity 必须一致）；
- 落盘 `dump()/load()`：JSON、键序固定、**无时间戳**，只有单调递增的
  `generation`。同一批文档按同一顺序推进必得逐字节相同的账本；`version` 不识别
  即拒绝（不"尽力而为"地读旧账本）。

## 7. 已知限制（不阻塞 P3-4，但下游不得误读）

1. **编码**：只接受 ASCII 兼容编码（UTF-8 / UTF-8-BOM / 单字节）。UTF-16 会显式
   拒绝（字节定界扫描要求定界符是单字节），不会静默转码。
2. **不创建/删除元素**：本内核只改已有 `<target>` 的文本节点；没有 `<target>` 的
   单元写回抛 `NotWritableError`（P3-4 若需要新建目标元素，必须另立契约）。
3. **文件内嵌注释的处理**：目标内容里的注释不参与逻辑文本；文本要变即拒绝
   （见 §4.1）。真实 9 个样本里目标内容 0 个注释，这条路径只有构造样本覆盖。
4. **`translate` 继承**：按 XLIFF 1.2 从 `<group>` 继承；锁定属性**不继承**
   （1.2 没有标准 `group@locked`，不发明继承语义）。
5. **状态词**：SDLXLIFF 的单元级状态在 `sdl:seg@conf`（子段声明）上；多段单元的
   单元行状态仍只有一个，逐段状态由 `subsegments=True` 的子段行给出。
6. **真实 CAT 工具往返**：验证的是"导出字节与原文件在骨架层面逐字节一致"，不是
   用 Trados/memoQ 打开再保存。工具会改写什么（标签 id、命名空间声明、行尾）没有
   在本节点实测。
7. **多 `<file>` 真实样本**：9 个样本里 0 个含多个 `<file>`；多文件行为只用构造
   样本与契约（`trans-unit@id` 的作用域是所在 `<file>`）覆盖。
8. **UTF-16 / 超大盘**：未做流式解析，9 个样本最大 3.5 MB 元素数 3560，性能未做
   大数据集测量。
