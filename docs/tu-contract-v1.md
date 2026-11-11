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

**实例身份不含源文哈希**（#143 裁定）：身份只由外部 TU id 决定，源文摘要只作为
**独立的变化证据**，不参与身份、不拼进 key。

```
identity        = "tu:" + external_id                 # 第 1 个实例
instance n ≥ 2  = "tu:" + external_id + "#" + n       # 源文变化后的显式新实例
source_digest   = sha256(source_text)                 # 变化证据，与身份分开存放
```

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
- 身份账本（`IdentityLedger`，版本 2）：`external_id → {identity, source_digest,
  instance, confirmed_state, history}`；同一实例重复导入命中身份与摘要 →
  `stable=True`（"本次导入证明了身份未变"），`mark_confirmed()` 只记当前实例。
  版本 1 的旧口径账本（身份里含源文哈希）**显式拒绝**：必须重新导入以建立新
  账本，不得沿用旧身份。
- **身份映射必须单射**：同一批导入里重复的外部 TU id 是身份层缺陷（损坏的
  CAT 文件、跨 `<file>` 复用 id、按 `id` 而非 `file+id` 去重都会造成），
  契约按 `DUPLICATE_EXTERNAL_ID` **单元级拒绝**该单元——绝不允许多个 TU
  共用同一身份（注入时后写覆盖前写，段落内容会静默丢失），也绝不把
  "文件内重复 id"谎报成 `source_changed`。首次出现的单元照常导入，账本保留
  首次出现的实例，重复单元不进 `identities`。
- 无外部 id 的单元由适配器**显式赋号并报告**；若仍未赋号，契约按
  `IDENTITY_MISSING` **单元级拒绝**（`unit_ref` 用 1 基位置引用 `#1`、`#2`…），
  其余单元继续导入——单个坏单元不得中断整份文件。
- 再指派**不是拒绝**：单元照常导入，但报告必须让用户看见"这些段被视为新段落，
  需要重新定位/重译/重审"。

可测试判定：同一 fixture 导入两次 → 身份列表与身份账本 JSON 逐字节相同
（`ledger_first.to_json() == ledger_second.to_json()`），且身份里不含摘要；
只改译文再导入 → 身份不变、`stable=True`、账本 JSON 不变；篡改任一单元源文
再导入 → 该单元得到 `tu:<id>#2` 新身份并出现在 `reassignments`（新旧身份与
新旧摘要齐全、`previous_confirmed_state=True` 而 `confirmed_state=False`），
其余单元身份不变；把源文改回原文 → 又是新实例（`#3`），旧身份不复用；重复
外部 id → `DUPLICATE_EXTERNAL_ID` 单元级拒绝且只产出一个身份；空外部 id →
`IDENTITY_MISSING` 单元级拒绝且其余单元照常导入。

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
  编号标签必须存在于源文（多余标签 = 错误）。同编号标签必须 **`kind` /
  `code_name` / `payload` 三者同时**与源文一致——只比负载会让"把 open 改声明
  为 self_closing"或"open/close 互换"这类改写绕过整对闭合校验，导出即写出
  未闭合/倒置的标签。允许整对省略格式标签（目标语言词序调整），不允许半对：
  半对（含方向改写）会被成对校验或同编号比对捕获。"标签个数相等"不是校验
  目标，配对合法性才是。
- 标签校验不通过的单元按 `TAG_STRUCTURE_INVALID` 显式拒绝（单元级），
  不得截断半句入库（沿用 TMX `_tmx_seg_text` "宁可不导入，不导入半句"）。

可测试判定：嵌套/自闭合合法序列零错误；乱序关闭、未闭合、多余开启、译文
多余标签、负载不一致、**同编号标签的 `kind` 不一致、`kind` 互换、配对语义名
被改写**各产出精确错误；一处交叉嵌套只报一条最具体的错误（不把同一处结构
缺陷拆成"嵌套错乱 + 外层未闭合"两条）。

## 3. 状态映射

双向映射表（`STATUS_MAP` / `map_cat_status` / `map_folith_status`）。别名
归一后才查表：**大小写、首尾空白、连字符与下划线等价**（XLIFF 1.2/2.0 的
`state` 取值用连字符，如 `signed-off` / `needs-translation`，与表内下划线
写法同义）。**表外状态一律显式拒绝**（`StatusMappingError` →
`UNMAPPABLE_STATUS` 拒绝路径），绝不猜近似语义。

| 规范状态 | 别名（示例） | → Folith pair 字段 | 说明 |
| --- | --- | --- | --- |
| `confirmed` | final, reviewed, signed_off（`signed-off`）, approved | `target_provenance="human_edit"`, `human_edited=True`, `reviewed=True`, `review_status="reviewed"`, `from_tm=False` | CAT 已确认 = 人工作者且人工定稿：进库即受人工保护（批量替换跳过） |
| `translated` | draft, pre_translated, translation_complete, needs_review_translation, needs_review_adaptation, needs_review_l10n | `target_provenance="generated"`, `human_edited=False`, `reviewed=False`, `review_status="not_reviewed"`, `from_tm=False` | 已翻未确认 = 可用工作译文，仍须走本应用审校门禁 |
| `untranslated` | new, empty, not_translated, initial, needs_translation（`needs-translation`） | 同 `translated`，**且 `target=""`** | 待生成。`target=""` 是**破坏性字段**：导入时必须**整体覆盖**单元字段，不得与旧译文 merge（否则旧译文残留、单元会被重新判成 `translated`） |
| `locked` | read_only, protected | `{"excluded": True}` | 适配器不得生成译文：单元不进 `pairs`，登记进既有排除账本 `excluded_segments`；排除不计为已翻译 |

反向映射（导出方向）：`excluded → locked`；`reviewed → confirmed`；有译文
未审 → `translated`；空译文 → `untranslated`。人工保护标记不改变状态词：
CAT 状态描述定稿程度，保护标记描述"谁动的"，两者正交。

字段取值严格来自现有 pairs 契约的既有取值域（`target_provenance ∈
{generated, human_edit, reviewed, tm_approved, candidate_adopted}`），
不发明新值。

可测试判定：映射表每个规范状态、每个别名、大小写变体全分支覆盖；**连字符
写法（`signed-off` / `needs-translation` / `initial` / `needs-review-*`）与
下划线写法等价**；表外状态（含空串、`needs-review`、`rejected`）必抛
`StatusMappingError`；反向映射四分支全覆盖，且 `untranslated` 的闭环不靠
测试手工补 `target`。

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
| 单元级 | `DUPLICATE_EXTERNAL_ID` | 同一文件内重复的外部 TU id（身份映射不再单射；拒绝重复出现的那个单元） |

`ImportReport` 汇总接受清单、拒绝（含范围/码/单元/原因）、身份再指派与
保全清单；`file_rejected` 为真时调用方**不得**产出任何可交付结果——这一点
由契约强制：`add_rejection` 收到文件级拒绝即**清空 `accepted`** 并记一条
`warnings`，`to_json` 也不会输出部分结果。拒绝码与范围在
`ImportReport.add_rejection` 处集中校验，写错即抛错。

可测试判定：`synthetic-unknown-namespace` 与 `synthetic-track-changes`
fixture 产出指定拒绝码的单元级拒绝；文件级拒绝码登记到 `file` 范围即令
`file_rejected` 为真**且 `accepted == []`**；未知拒绝码被拒绝登记；
重复/缺失外部 id 各产出对应单元级拒绝（见第 1 节）。

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

## 失败 / 回滚行为

契约层为新增纯库 + 文档 + fixture，无写入路径，不触碰任务状态；回滚 =
移除 `folith/interop/`、`docs/tu-contract-v1.md`、
`tests/fixtures/tu_contract/` 与测试文件，不影响任何现有功能。

## v1 已知限制（不阻塞本契约，但下游不得误读）

以下各项本版本**没有**定义或没有覆盖，按"不猜语义"原则留给后续 issue /
裁定，适配器不得据本节自行发明口径：

- **`locked` 的落点**：第 3 节的正向映射给出 pair 字段 `{"excluded": True}`，
  而规范落点是排除账本 `excluded_segments`（另一种记录形状，本契约未定义
  其类型/键名）；二者如何统一、锁定段落是否"工作台可见只读"，待用户裁定。
- **`source_digest` 的输入口径**（本轮裁定只确定"摘要不做身份"，没有改摘要
  怎么算）：摘要吃的是带占位符的 `source_text`，因此纯标签重编号会被判成源文
  变化 → 产生一个新实例。是否去占位符/归一空白仍待后续裁定；若改口径，只影响
  "是否判成变化"，不影响身份规则本身。
- **标签配对的扩展语义**：按 `code_name` 栈式配对，尚无 `rid` 配对键、
  无法表达 XLIFF 2.0 `<pc>`，"允许整对省略"与"写回必须禁止整对省略"的
  严格度也不可配（属 v1.1 扩展）。
- **没有字节级往返保证**：no-op 验收比较树投影等价（见第 6 节末）。

## Out of Scope

- 任何具体格式适配器（XLIFF / SDLXLIFF / MQXLIFF 各有独立 Issue）。
- 翻译核心、TM、审校真值的任何改动。
- 项目包（SDLPPX / MQOUT 等）解包。
