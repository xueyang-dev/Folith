# Folith TU Contract v1：翻译单元与 inline 标签互操作语义契约

状态：2026-10-26 建立契约与确定性校验器实现（Issue #143，实现见
`folith/interop/`，测试见 `tests/tu_contract_v1_test.py`，fixture 见
`tests/fixtures/tu_contract/`）。本契约是 XLIFF / SDLXLIFF / MQXLIFF
适配器 Issue 与 v0.9.0 端到端验收的前置地基。

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

外部 TU id ↔ Folith pair 的映射键定义为：

```
pair_key = "tu:" + external_id + ":" + sha256(source_text)[:16]
```

- **可复现**：同一文件重复导入，(external_id, 源文摘要) 不变，映射键逐字节
  相同。
- **不稳定即再指派**：外部 id 不变而源文摘要变化时，旧身份不可证明仍然
  成立——新映射键天然不同（嵌入了新摘要），该单元按**新身份**导入，并在
  导入报告的 `reassignments` 中记入旧键、新键与原因 `source_changed`。
  禁止静默沿用旧身份，也禁止按内容相似度猜测"还是同一段"。
- 身份账本（`IdentityLedger`：external_id → 源文摘要 + pair key）随导入
  更新；账本版本不识别时显式报错，不猜语义。
- 无外部 id 的单元必须由适配器**显式赋号并报告**；契约层对空 id 直接
  抛错（`IDENTITY_MISSING` 拒绝路径）。
- 不可证明稳定的再指派**不是拒绝**：单元照常导入，但报告必须让用户看见
  "这些段被视为新段落"。需要保留历史关联时由适配器在文件内自担。

可测试判定：同一 fixture 导入两次 → 身份账本 diff 为空；篡改任一单元源文
再导入 → 该单元出现在 `reassignments`，其余单元身份不变。

## 2. Inline 标签语义

- **内部表示**：标签在契约文本中只以**编号占位符**出现（`U+E000 <n> U+E001`
  私有区定界，正常文本不可能出现，无需转义协议）；原始负载（源文件中的
  序列化字节/串）与占位符编号分离存放于 `InlineTag.payload`，导出时由
  适配器按编号取回原负载写回。**禁止伪造负载**：同编号标签的译文负载必须
  与源文逐字符一致。
- **成对/自闭合校验**（`validate_tag_sequence`）：占位符与标签定义一一对应；
  成对标签（open/close）必须同 `code_name` 正确嵌套闭合；自闭合标签无配对
  语义。违反即错误清单（确定性输出）。
- **译文标签集合 ⊆ 源文标签集合**（`validate_target_tags`）：译文出现的每个
  编号标签必须存在于源文（多余标签 = 错误），且负载一致；允许整对省略
  格式标签（目标语言词序调整），不允许半对——半对会被成对校验捕获。
  "标签个数相等"不是校验目标，配对合法性才是。
- 标签校验不通过的单元按 `TAG_STRUCTURE_INVALID` 显式拒绝（单元级），
  不得截断半句入库（沿用 TMX `_tmx_seg_text` "宁可不导入，不导入半句"）。

可测试判定：嵌套/自闭合合法序列零错误；乱序关闭、未闭合、多余开启、译文
多余标签、负载不一致各产出精确错误。

## 3. 状态映射

双向映射表（`STATUS_MAP` / `map_cat_status` / `map_folith_status`）。别名
归一后才查表；**表外状态一律显式拒绝**（`StatusMappingError` →
`UNMAPPABLE_STATUS` 拒绝路径），绝不猜近似语义。

| 规范状态 | 别名（示例） | → Folith pair 字段 | 说明 |
| --- | --- | --- | --- |
| `confirmed` | final, reviewed, signed_off, approved | `target_provenance="human_edit"`, `human_edited=True`, `reviewed=True`, `review_status="reviewed"`, `from_tm=False` | CAT 已确认 = 人工作者且人工定稿：进库即受人工保护（批量替换跳过） |
| `translated` | draft, pre_translated, translation_complete | `target_provenance="generated"`, `human_edited=False`, `reviewed=False`, `review_status="not_reviewed"`, `from_tm=False` | 已翻未确认 = 可用工作译文，仍须走本应用审校门禁 |
| `untranslated` | new, empty, not_translated | 同 `translated`，且 `target=""` | 待生成 |
| `locked` | read_only, protected | `{"excluded": True}` | 适配器不得生成译文：单元不进 `pairs`，登记进既有排除账本 `excluded_segments`；排除不计为已翻译 |

反向映射（导出方向）：`excluded → locked`；`reviewed → confirmed`；有译文
未审 → `translated`；空译文 → `untranslated`。人工保护标记不改变状态词：
CAT 状态描述定稿程度，保护标记描述"谁动的"，两者正交。

字段取值严格来自现有 pairs 契约的既有取值域（`target_provenance ∈
{generated, human_edit, reviewed, tm_approved, candidate_adopted}`），
不发明新值。

可测试判定：映射表每个规范状态、每个别名、大小写变体全分支覆盖；表外
状态（含空串）必抛 `StatusMappingError`；反向映射四分支全覆盖。

## 4. 元数据保全

- 适配器无法解释为契约语义的属性/节点，必须作为 `PreservedFragment`
  （`location` + 原样 `payload`）**逐字节携带**；`location` 是适配器自述的
  结构位置。契约层不解释其内容，只保证"进得来、出得去"。
- 导出时全部保全片段必须原样写回。no-op 往返验收（第 6 节）兜底验证：
  任何静默丢弃都会成为结构 diff 的一条差异。
- 禁止以"看起来是注释/上下文"为由改写、归一或丢弃保全内容。

可测试判定：`synthetic-notes` fixture 的注释与上下文组按原样出现在报告
`preserved` 清单；导出树缺失任一片段时 no-op 判定失败并给出精确路径。

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
| 单元级 | `IDENTITY_MISSING` | 无外部 id 且适配器未显式赋号 |

`ImportReport` 汇总接受清单、拒绝（含范围/码/单元/原因）、身份再指派与
保全清单；`file_rejected` 为真时调用方**不得**产出任何可交付结果。拒绝码
与范围在 `ImportReport.add_rejection` 处集中校验，写错即抛错。

可测试判定：`synthetic-unknown-namespace` 与 `synthetic-track-changes`
fixture 产出指定拒绝码的单元级拒绝；文件级拒绝码登记到 `file` 范围即令
`file_rejected` 为真；未知拒绝码被拒绝登记。

## 6. Round-trip 结构比较工具

适配器把"解析出的文件结构"投影成契约层的通用节点树
（`{"name", "attrs", "text", "children"}`；任何格式皆可投影）：

- `canonical_snapshot`：属性键排序等与内容无关的序列化差异在规范化时消除，
  不算差异；文本逐字符比较，不做空白归一。
- `diff_snapshots`：精确差异清单（路径 + `added/removed/changed` + 前后
  值）。路径 `/name:0/child:1@attr`、`.../node#text`，`:i` 为子节点位置
  （0 基，用冒号而非方括号：方括号会被 fnmatch 当成字符类，白名单将无法
  按字面匹配路径）——插入/删除会使后续位置移位，这正是 no-op 验收想要
  暴露的。
- `no_op_verdict`：no-op 模式（解析 → 原样导出 → 再解析）下，重导出树与
  源树的 diff 必须为空，或差异全部落入**显式白名单**（`{"path": glob,
  "kinds": [...]}`，由调用方逐条声明，契约层不内置任何默认豁免）；白名单外
  差异判失败，`unexplained` 即失败原因清单。

可测试判定：`synthetic-noop-roundtrip` 判空通过；`synthetic-tampered`
（属性改动 / 丢节点 / 文本变化）输出与 fixture 内黄金 diff 逐条一致；
白名单只豁免声明的路径与类型，路径不符或类型不符均判失败。

## 7. Fixture corpus

`tests/fixtures/tu_contract/`，带来源记录（`SOURCES.md` + 每个样本内嵌
`provenance`）。当前全部为仓库内合成样本（无需外部授权）；真实导出样本
须先取得授权并登记来源，无授权来源的样本不进入仓库。生成器是唯一权威
来源，手工编辑 JSON 会在重建时被覆盖。

## 失败 / 回滚行为

契约层为新增纯库 + 文档 + fixture，无写入路径，不触碰任务状态；回滚 =
移除 `folith/interop/`、`docs/tu-contract-v1.md`、
`tests/fixtures/tu_contract/` 与测试文件，不影响任何现有功能。

## Out of Scope

- 任何具体格式适配器（XLIFF / SDLXLIFF / MQXLIFF 各有独立 Issue）。
- 翻译核心、TM、审校真值的任何改动。
- 项目包（SDLPPX / MQOUT 等）解包。
