# SDLXLIFF 兼容性矩阵 —— Issue #145 Gate A 调查报告

状态：**Gate A 调查完成（受限样本集）；未经 Trados Studio 客户端验证。**
日期：2026-10-26 · Issue：#145 · 里程碑：v0.9.0 · 审计脚本：`scripts/audit_sdlxliff.py`

---

## 0. 结论速览

SDLXLIFF 是 **XLIFF 1.2 方言**：根元素为 `xliff version="1.2"`，叠加命名空间
`http://sdl.com/FileTypes/SdlXliff/1.0`（根元素另带 `sdl:version="1.0"` 属性）。
段级模型、确认状态、注释、TM 溯源与锁定全部通过该命名空间表达，
**没有任何观测到的结构要求修改 XLIFF 1.2 核心语义**——这与 #145 的架构约束
（方言层叠加在通用 XLIFF 1.2 适配器之上）一致。

三分类矩阵（依据 = 样本文件 + 出现次数；「推断」= 无样本支持、来自公开资料的合理假设）：

| 分类 | 数量 | 代表 |
|---|---|---|
| **P = 必须原样保全、不可编辑** | 24 种结构 | 段溯源 `sdl:value`、注释对（`cmt-def` + `x-sdl-comment` mrk）、结构标签 `sdl:st`、参考文件 `sdl:ref-files`、`internal-file`（原始文件 base64） |
| **M = 可与 Folith 状态双向映射** | 2 种结构 | `sdl:seg/@conf` → Folith 审校状态；`sdl:seg/@locked` → 段保护 |
| **R = 无法证明安全，导入时必须显式拒绝** | 4 类情形 | 未见过的 `sdl:` 元素/属性、`sdl:version ≠ 1.0`（推断）、多 `<file>` 文档（无样本）、非 SDLXLIFF 的 `.sdlxliff` 伪文件（有样本） |

**支持边界声明**：以上结论来自 4 份 RWS 官方测试样本（Trados Studio 2019 时代导出）
+ 4 份社区参考文件，**未经真实 Trados Studio 打开验证**。0.9.0 若实现 Gate B，
支持声明必须降级为「经样本集验证的受限 round trip，未经 Trados Studio 回读验证」，
不得宣称完整 SDLXLIFF 支持。

---

## 1. 样本来源与授权

真实来源样本（已入库 `tests/fixtures/sdlxliff/`，均来自 Trados 厂商 RWS 的
Apache-2.0 官方仓库，允许再分发）：

| 文件 | 大小 | 来源 | SHA-256（前 16 位） | 内容特征 |
|---|---|---|---|---|
| `QuotesSample.docx.sdlxliff` | 35 KB | [RWS/Sdl-Community](https://github.com/RWS/Sdl-Community) → XLIFF Manager for Trados Studio 单元测试 | `95a9514d5f5b7e73` | 状态全覆盖（Draft/Translated/ApprovedTranslation/ApprovedSignOff）、MT 溯源、locked、注释对、`sdl:doc-info` |
| `TTSExample.docx.sdlxliff` | 17 KB | 同上 → SDLTM Import Plus 单元测试 | `ab3fcbcb32a91ad8` | **seg-defs 存在但无任何 `conf` 属性**（状态未知场景）；无 TM 溯源 |
| `quickplace.docx.sdlxliff` | 39 KB | 同上 → SDLTM Import Plus 单元测试 | `85e67619be7594ff` | `sdl:rep` 重复段哈希、`origin="source"` |
| `SamplePhotoPrinter.doc.sdlxliff` | 43 KB | [RWS/trados-studio-api-samples](https://github.com/RWS/trados-studio-api-samples)（Apache-2.0） | `d9fc637cdb18d657` | 46 个 trans-unit、80 个 `sdl:st` 结构标签、TM 匹配（`percent`/`text-match`） |

仅审计、未入库的参考样本（许可证不适配或为人工构造，存
`verification/issue-145-sdlxliff-gatea/audit-only-samples/`）：

| 文件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| `piazza.sdlxliff` | [matecat/xliff-parser](https://github.com/matecat/xliff-parser)（LGPL-3.0） | LGPL-3.0 | locked 段 + 多语言混合证据 |
| `small-with-notes.sdlxliff` | [matecat/MateCat](https://github.com/matecat/MateCat)（LGPL-3.0） | LGPL-3.0 | **反例：文件名带 .sdlxliff 但完全没有 sdl 命名空间** |
| `minimal.sdlxliff`、`with-namespaces.sdlxliff` | [Jirawatpyk/QA-Localization-tool](https://github.com/Jirawatpyk/QA-Localization-tool) | 无 | 人工构造 fixture，仅作结构参考，**不算真实来源证据** |

审计原始输出（全量 JSON，本次审计存于仓库外
`verification/issue-145-sdlxliff-gatea/audit-all.json`），可用
`python scripts/audit_sdlxliff.py <files...>` 完整复现。
样本局限：无 Trados Studio 2021/2022/2024 导出样本；无 Rejected\* 状态样本；
无 SDLPPX 项目包内样本；样本均来自厂商测试套件，不代表生产文件的全部变体。

---

## 2. 观察到的结构事实清单

以下全部为样本内直接观察到的事实（证据 = 文件名 + 审计计数）。

### 2.1 根与文件层

- 根元素 `<xliff version="1.2" sdl:version="1.0">`，声明 `xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"`（全部 4 份真实样本一致）。
- `<file>` 的 `datatype` 为 `x-sdlfilterframework2`（QuotesSample/TTSExample/quickplace/SamplePhotoPrinter 均是）；人工 fixture 用 `x-sdlxliff`。`original` 属性保留**源机绝对路径**（如 `C:\Users\<user>\Studio 2019\TTSGerman\en-us\TTSExample.docx`）。
- `header` 内除 XLIFF 1.2 标准的 `reference/internal-file`（**原始 DOCX/DOC 以 base64 内嵌**）外，还有 7 组 `sdl:` 结构：`sdl:ref-files`、`sdl:file-info`（`sdl:value key=...` 键值对，8–190 个/文件）、`sdl:sniff-info/sdl:detected-source-lang`、`sdl:filetype-info/sdl:filetype-id`、`sdl:fmt-defs`、`sdl:cxt-defs`、`sdl:node-defs`、`sdl:tag-defs`。
- QuotesSample 在 `xliff` 根下还有 `sdl:doc-info`（含 `cmt-defs`，见 2.3）。**注意**：该文件的 `doc-info` 子树用**默认命名空间**声明（`<doc-info xmlns="http://sdl.com/FileTypes/SdlXliff/1.0">`），不用 `sdl:` 前缀——按前缀字符串解析会漏检，必须按 URI 解析（QuotesSample 实测）。
- 真实样本均为单 `<file>` 文档（4/4）；多 `<file>` 未见样本。

### 2.2 段级模型与 TM/MT 溯源

- 分段：`trans-unit` 下 `seg-source`/`target` 内 `<mrk mtype="seg" mid="N">`；`sdl:seg-defs` 直接挂在 `trans-unit` 下，内含 `sdl:seg id="N"`，`id` 与 `mid` 对应（全部样本一致）。
- `sdl:seg` 观测到的属性全集：`id`、`conf`（确认状态）、`origin`（取值：`source`/`tm`/`mt`/`interactive`）、`origin-system`（如 `"SDL Machine Translation"`）、`percent`（TM 匹配率）、`text-match`（`SourceAndTarget`）、`locked`（`"true"`）。
- `sdl:seg` 子元素：`sdl:value key=...`（TM/MT 溯源：`SegmentIdentityHash`、`tuid`、`tuguid`、`created_by`、`created_on`、`last_modified_by`、`modified_on`，QuotesSample 中 56 个）、`sdl:prev-origin`（前次来源记录，含嵌套 `sdl:value`）、`sdl:rep id="..."`（重复段指纹，quickplace 5 个）、`sdl:cmt`（段级注释文本，with-namespaces 1 个）。
- `trans-unit`/`group` 级还有 `sdl:cxts/sdl:cxt`（格式上下文引用，指向 `cxt-defs`）、`sdl:tag-defs/sdl:tag/sdl:bpt|ept|ph`（标签格式元数据）、`sdl:bpt-props`/`sdl:ept-props`。
- **正文内结构标签** `sdl:st name="..."`（SamplePhotoPrinter 80 个）：文本内容为**源格式的 XML 片段序列化**（如 `<paragraph style="Note Body" font="Times New Roman" italic="on">`），是 Trados 还原版式所需，不是可翻译文本。
- **状态缺省场景**：TTSExample 的 4 个 `sdl:seg` 全部只有 `id`，无 `conf`/`origin`——真实文件可以完全没有状态属性。

### 2.3 注释模型（两部分成对出现）

1. 载荷在根级：`sdl:doc-info/cmt-defs/cmt-def @id → Comments/Comment @severity @user @date @version`（QuotesSample：2 个 cmt-def，severity 取值 Low/Medium）；
2. 锚点在正文：`<mrk mtype="x-sdl-comment" sdl:cid="{cmt-def 的 id}">`（QuotesSample：2 个）。

两部分缺一即失义，round trip 必须成对保全。

### 2.4 状态值全集（样本内）

`Draft`、`Translated`、`ApprovedTranslation`、`ApprovedSignOff`（QuotesSample + 人工 fixture）。
`RejectedTranslation`/`RejectedSignOff`/`RejectedInternal` 在样本中**未出现**（推断：社区资料记载其存在，语义为拒绝流程）。

### 2.5 伪文件反例

`small-with-notes.sdlxliff`（MateCat 测试资源）不含任何 sdl 命名空间，实为普通 XLIFF 1.2。
**扩展名不可作为格式判据**，必须按根元素命名空间探测。

---

## 3. 兼容性矩阵

### P 类：必须原样保全、导出写回，不可编辑

| # | 结构 | 证据 | 保全方式 |
|---|---|---|---|
| P1 | `header/reference/internal-file`（原始文件 base64） | 全部 4 份真实样本 | 字节级原样携带 |
| P2 | `sdl:ref-files` | 4/4 | 原样携带 |
| P3 | `sdl:file-info` + 全部 `sdl:value` 键值 | 4/4 | 原样携带 |
| P4 | `sdl:sniff-info`、`sdl:detected-source-lang` | 4/4 | 原样携带 |
| P5 | `sdl:filetype-info`/`sdl:filetype-id` | 4/4 | 原样携带 |
| P6 | `sdl:fmt-defs`、`sdl:cxt-defs`、`sdl:node-defs`、`sdl:tag-defs` 及其子树 | 4/4 | 原样携带 |
| P7 | `sdl:doc-info`（含 cmt-defs 载荷） | QuotesSample | 原样携带；注意默认命名空间变体 |
| P8 | 注释锚点 `mrk mtype="x-sdl-comment" @sdl:cid` | QuotesSample | 保全锚点位置与 cid |
| P9 | `sdl:seg/sdl:value`（SegmentIdentityHash、tuid、tuguid、created_by、created_on、last_modified_by、modified_on） | QuotesSample/quickplace/piazza | 原样携带；丢失即破坏 Trados TM/perfect match 关联 |
| P10 | `sdl:prev-origin` 子树 | QuotesSample | 原样携带 |
| P11 | `sdl:rep` | quickplace | 原样携带 |
| P12 | `sdl:st` 结构标签 | SamplePhotoPrinter | 原样携带（不可当作译文文本） |
| P13 | `sdl:cxt`、`sdl:tag`、`sdl:bpt-props`、`sdl:ept-props` 上下文/格式元数据 | 4/4 | 原样携带 |
| P14 | `sdl:seg/@origin`、`@origin-system`、`@percent`、`@text-match`（TM 匹配溯源） | QuotesSample、SamplePhotoPrinter | 原样携带（只读展示，不可编辑） |
| P15 | `trans-unit/@original` 源机路径、`@datatype` | 4/4 | 原样携带 |

### M 类：可与 Folith 状态双向映射

| # | SDLXLIFF | → Folith 导入 | → SDLXLIFF 导出 | 证据 |
|---|---|---|---|---|
| M1 | `sdl:seg/@conf="Draft"` | 未确认（草稿） | 段未确认 → `Draft` | QuotesSample ×2 |
| M2 | `@conf="Translated"` | 已翻译、待审 | 已翻译未审 → `Translated` | QuotesSample ×2 |
| M3 | `@conf="ApprovedTranslation"` | **已审 + 保护** | 已审 → `ApprovedTranslation` | QuotesSample ×1 |
| M4 | `@conf="ApprovedSignOff"` | **已审 + 保护**（最高级；Folith 只有单一已审态，两者导入后等价） | 已审 → 保留原值 `ApprovedSignOff`（导出时若原文件为 SignOff 流程，不降级写回） | QuotesSample ×2 |
| M5 | `@conf` 缺失（TTSExample 场景） | 视为未确认，**不得臆造状态** | 未确认段 → 写回时省略 `conf`（保持与原文件一致的缺省） | TTSExample ×4 |
| M6 | `sdl:seg/@locked="true"` | 段保护（锁定） | 保持锁定 | QuotesSample ×2、piazza ×1 |
| M7 | `RejectedTranslation`/`RejectedSignOff` | （推断，无样本）拒绝态 → 不映射为已审；导入保全、导出原样写回，Folith 内不可达 | 同左 | 无样本，**推断**，Gate B 若遇到需先补样本 |

映射约束（遵循 #145 架构约束第 3 条）：Folith 侧不新增第二套真值系统；
ApprovedTranslation 与 ApprovedSignOff 在 Folith 内同为「已审+保护」，
导出时按原文件值回写，**不把 SignOff 降级改写为 Translation**。

### R 类：无法证明安全，导入时显式拒绝（说明原因，绝不静默丢弃）

| # | 情形 | 依据 |
|---|---|---|
| R1 | 观测集合（§2.2/§3 全表）之外的 `sdl:` 元素或属性 | TU Contract：不可解析即原样携带——但对**结构未知**的方言节点，无法保证携带语义正确，故拒绝并报明未识别节点名 |
| R2 | 根 `sdl:version` ≠ `"1.0"` | 推断（样本仅见 1.0）；无样本证明兼容，拒绝并提示版本 |
| R3 | 单文档多 `<file>` 且各 file 的 `sdl:filetype-info` 不一致 | 无样本；无法证明安全，首版拒绝 |
| R4 | 扩展名 `.sdlxliff` 但无 sdl 命名空间 | small-with-notes 实测存在；不拒绝会走错适配器，应路由至通用 XLIFF 1.2 适配器（#144），并在报告中说明 |

---

## 4. Gate B 实施范围建议（待 #143/#144 落地后启动）

- **前置**：TU Contract（#143）与 XLIFF 1.2 通用适配器（#144）合入；方言层只做叠加，不 fork 解析核心。
- 导入：按 §3 P 类清单原样携带；M 类映射；R 类显式拒绝（错误信息含未识别节点/版本号）。
- 编辑：仅允许改 `target` 内 `mrk mtype="seg"` 内容；写回时 M 类状态按映射回写，P 类逐字节不变。
- 验收（对齐 #145 AC）：每个真实样本 no-op round trip 结构 diff 为空；修改译文 round trip 仅白名单变更（target 内容 + 显式映射的 conf）；导出文件重新导入 Folith 身份稳定、确认状态复现。
- **真实验证缺口**：需 Trados Studio 环境做「打开 + 段状态读取」验证；在此之前，支持声明一律降级为「未经 Trados 验证」。

## 5. 测试与证据

- `tests/sdlxliff_fixture_audit_test.py`：对 4 份入库样本做只读结构断言（命名空间、seg-defs、conf 值域、locked、注释对、internal-file 存在性），作为 Gate B 之前 fixtures 的看门测试；审计脚本 `scripts/audit_sdlxliff.py` 可复现全量 JSON。
