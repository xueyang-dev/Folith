# XLIFF Contract v1：1.2 / 2.0 安全往返

对应 issue [#144](https://github.com/xueyang-dev/Folith/issues/144)。实现模块
`folith/xliff_roundtrip.py`，测试 `tests/xliff_contract_test.py`。

XLIFF 1.2（Trados/memoQ 通用交换格式）与 2.0 的结构差异大，两者使用**各自独立的
解析器**；身份、状态与 inline 标签语义统一遵循本文声明 TU Contract v1 语义（与
`docs/docx-contract-v1.md` 的最小双语模型同源）。内部真值仍是 `pairs`；XLIFF
只是适配器，不是存储格式。

## 身份（Instance ID）

```
<file original|id> :: <trans-unit id | unit id> [# <seg mid | segment id>]
```

- 1.2：`<trans-unit>` 带 `seg-source` 时按 `<seg mid>` 逐段对位（无 mid 按文档
  顺序 `s1, s2, …`），否则整个 TU 一个对位项。
- 2.0：`<unit>` 的每个 `<segment>` 一个对位项（无 id 按顺序 `s1, s2, …`）。
- 写回永不改变 id；导出文件重新导入时 Instance ID 全部复现。
- 多个 `<file>` 的 `original` 重复时按出现顺序加 `#2` 后缀消歧。

## 分段与源文权威

- TU 边界即分段边界：导入后任务强制 paragraph 分段模式（格式锁优先于用户偏好
  与环境变量），XLIFF 任务豁免 LLM 源文清理——清理改写源文会破坏占位符与 TU
  对位。
- 空源文单元（无可翻译内容）不进入任务；写回时原样保留。
- 段对与翻译项按**位置顺序**对位（与 DOCX 适配器相同的已知简化）；文件的
  Instance ID 在写回时不受影响。

## inline 标签（编号占位符 + 负载保全）

- 工作台/引擎看到的是编号占位符：开 `⟦n⟧`、闭 `⟦/n⟧`，编号按文档顺序分配。
- 容器（1.2 `<g>`、2.0 `<pc>`）：开闭之间的文本嵌在元素内，译文可整体移动。
- 原生代码（1.2 `bpt/ept/ph/x/it`，2.0 `ph/cp/sc/ec/sm/em`）：负载（属性与
  子元素）在提取时序列化保存，写回时深拷贝回填；`bpt/ept` 按 `rid` 配对，
  无 rid 按文档顺序；`sc/ec`、`sm/em` 按嵌套顺序配对。
- 译文调序合法；**确定性 Tag QA**（不用 LLM）要求：每个源文占位符在译文中
  恰好出现一次、无未知编号、容器嵌套不交叉。违规显式拒绝并指明单元。
- 源文本出现 `⟦` 字符时无法保证安全映射，显式拒绝。

## 状态映射（有界）

| XLIFF | Folith | 写回（仅用户显式审校确认时） |
| --- | --- | --- |
| 1.2 `approved="yes"` / `target@state∈{final, signed-off}` | `reviewed` | — |
| 1.2 其他 | 未审校 | `trans-unit@approved="yes"` |
| 2.0 `segment@state∈{reviewed, final}` | `reviewed` | — |
| 2.0 其他 | 未审校 | `segment@state="reviewed"` |

写回只提升、不降级：`final` 不会被改成 `reviewed`。

## locked 段

1.2 `translate="no"`（file/trans-unit 级）、2.0 继承的 `translate="no"`
（file/group/unit/segment 级）→ 导入为 locked 段并计数；写回**永不触碰**
（任务里对 locked 段的任何修改都被忽略）。

## Preserve + report（原样携带）

- 未知命名空间的**内联**节点：映射为占位符原样携带，出现在 manifest 的
  `preserved` 列表。
- 1.2 `header`、`note`、`context-group`、`alt-trans`；2.0 `notes`、
  `skeleton`、`original-data`：写回原样保留。
- 空源文单元、locked 段同样计数上报。

## 显式拒绝（原因 + 位置）

无法证明安全的结构导入即拒绝（报告 `status=failed` 与含行号的错误信息），
绝不产生部分写入：

- XML 解析失败（损坏节点）；
- 根元素不是 XLIFF 1.2/2.0 命名空间，或 `version` 属性与命名空间不符；
- `<trans-unit>`/`<unit>` 缺 `id`、缺 `<source>`、`<body>`/`<segment>` 缺失；
- 容器级未知命名空间节点（`<xliff>`/`<file>`/`<body>`/TU 子元素）；
- XLIFF 命名空间内无法映射的节点（如 target 中无法对位到 seg 的内容）；
- `bpt/ept`、`sc/ec`、`sm/em` 配对失衡；源文含 `⟦`。

## 写回（surgical round-trip）

- 以原始字节解析出的 XML 树为基底，只允许两类白名单变化：
  1. 被修改单元的 `<target>` 子树（文本、占位符位置、新建 target/mrk 元素）；
  2. 审校提升的状态属性（上表）。
- 未修改的单元**不重写**（与文件现有译文相同即跳过），no-op 往返的结构 diff
  为空。
- `deliver_xliff` 交付键产出 `original_format.xliff` + `original_format_report.json`
  （含 modified/untouched/locked/promoted 计数、supported/preserved 列表）。
  Tag QA 或锚点失败会阻断交付，不产出半成品。

## 结构完整性验证（确定性）

`structural_diff(source, output)` 做确定性 XML 树对比（无 LLM），差异条目带
`inside_target` 白名单标记；`verify_roundtrip_xliff` 汇总 `ok / whitelisted /
unexpected`。测试以此断言 no-op 往返 diff 为空、修改往返仅白名单差异。

## 已知边界（后续 Issue）

- SDLXLIFF（`sdl:*`）、MQXLIFF 方言：不在本契约内。
- 段对与翻译项按位置对位：任务内重排/删段后写回按源文件锚点拒绝（单元丢失
  报错，不静默错位）。
- 1000 TU 的引擎耗时已在测试锁定（<10s）；真实桌面客户端的 UI 不冻结验收
  （C1–C6）需在 Windows + macOS 双平台人工执行。
- 独立校验（CAT 工具打开 / 开源校验器 + 抽检）在端到端验收 Issue 执行并记录。
