# MQXLIFF 兼容性矩阵 —— Issue #146 Gate A 调查报告

状态:**Gate A 调查完成(受限样本集);未经 memoQ 客户端回读验证;对照实验被 #144 前置阻塞。**
日期:2026-10-26 · Issue:#146 · 里程碑:v0.9.0 · 审计脚本:`scripts/audit_mqxliff.py`

---

## 0. 结论速览

MQXLIFF 是 **XLIFF 1.2 方言**:根元素为 `<xliff version="1.2">`,叠加命名空间
URI 为**字面量 `MQXliff`** 的 `mq:` 扩展(不是 URL;大小写混合,样本 6/6 一致)。
与 SDLXLIFF(#145)最大的差异:mq: 扩展不引入独立的段级元素树——
**每个 `<trans-unit>` 本身就是一段**,确认状态、锁定、段身份、TM 溯源全部以
`mq:` 属性直接挂在 trans-unit 上,不用 `seg-source`/`mrk` 分段
(6/6 样本,共 145 个 trans-unit,零 seg-source、零 mrk)。
没有任何观测到的结构要求修改 XLIFF 1.2 核心语义——与 #146 的架构约束
(方言层叠加在通用 XLIFF 1.2 适配器之上、与 #143 TU Contract 对齐)一致。

**结论(三选一):按 #146 验收标准,当前可判定的结论为
「样本充分,但判定标准路径是否覆盖的对照实验不可行——前置 #144(XLIFF 1.2/2.0
safe round trip)尚未合入,Folith 现无 XLIFF 适配器可承载实验」。**
即:实现门保持关闭,方言层不开工;缺口不是样本而是实验基座。
对照实验协议已备好(§5),#144 合入后即可执行,届时再在
「标准路径覆盖 / 需方言层」之间做最终判定。

三分类矩阵(依据 = 样本文件 + 出现次数;「推断」= 无样本支持、来自公开资料的合理假设):

| 分类 | 数量 | 代表 |
|---|---|---|
| **P = 必须原样保全、不可编辑** | 14 种结构 | 文档身份 `mq:id`/`mq:projectid`、`mq:docinformation`、历史版本 `mq:minorversions`/`mq:historical-unit`、TM 溯源 `mq:insertedmatch`/`mq:commitinfos`、QA 警告 `mq:warnings40`、内嵌 XML `mq:details` |
| **M = 可与 Folith 状态双向映射** | 2 组结构 | `mq:status`(7 种观测值)→ Folith 审校状态;`mq:locked` + `translate="no"` → 段保护 |
| **R = 无法证明安全,导入时必须显式拒绝** | 3 类情形 | 观测集合之外的 `mq:` 元素/属性、非 `MQXliff` URI 的冒充文件(推断)、`mq:` 状态值域外的取值(推断) |

**支持边界声明**:独立成文 `docs/memoq-support-boundary.md`(按 #146 验收
标准不与本文合并表述),结论降级为「经 6 份真实样本验证的受限 round trip
(如 Gate B 实施),未经 memoQ 回读验证」。

---

## 1. 样本来源与授权

真实来源样本(已入库 `tests/fixtures/mqxliff/`,均来自 MIT 许可的公开仓库,
允许再分发):

| 文件 | memoQ 版本 | 来源 | SHA-256(前 16 位) | 内容特征 |
|---|---|---|---|---|
| `document.mqxliff` | 7.8.124 | [ShunSakurai/check_forbidden](https://github.com/ShunSakurai/check_forbidden) → `Test/document.mqxliff` | `384533689d1bce18` | 全部 16 段 ManuallyConfirmed;TM 溯源 `mq:insertedmatch`(matchrate=101 的 CM)、QA 警告 `mq:warnings40`(3 个 errorwarning)、历史版本 19 个 `mq:historical-unit`、CDATA 内嵌 `mq:details`、bpt/ept/ph 内联标签 |
| `locked.mqxliff` | 10.3.12 | [bb-io/OpenAI](https://github.com/bb-io/OpenAI) → `Tests.OpenAI/Input/locked.mqxliff` | `5b3de4d08266a8b3` | **锁定段**(`mq:locked="locked"` + `translate="no"`)、审读态 Proofread + `mq:segmentproofread`、空目标段(NotStarted) |
| `mt.mqxliff` | 11.6.8 | [ikuehirata/MT-quality-review](https://github.com/ikuehirata/MT-quality-review) → `sample_and_materials/mt.mqxliff` | `9c228773a9cf10d2` | **MT 工作流**:MachineTranslated ×36 + PreTranslated ×15;注释双表示(23 组 note + mq:comment,11.6 版为 116/186 组);245 个 ph 标签 |
| `pe.mqxliff` | 11.6.8 | 同上 → `sample_and_materials/pe.mqxliff` | `12b338dc75b80831` | **同一文档后编辑后导出**:51 段全部 ManuallyConfirmed;与 mt.mqxliff 51 段段序对应 |
| `translation_status.mqxliff` | 11.6.8 | [ikuehirata/mqxliff-viewer](https://github.com/ikuehirata/mqxliff-viewer) → `sample/translation_status_sample.xlsx_jpn.mqxliff` | `381f331e8c000a8b` | **单文件覆盖 6 种状态**各 2 段:NotStarted/PartiallyEdited/PreTranslated/ManuallyConfirmed/Reviewer1Confirmed/Proofread;11 个 historical-unit |

仅审计、未入库的参考样本(许可证不适配,存仓库外
`verification/issue-146-mqxliff-gatea/audit-only-samples/`):

| 文件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| `test_bitext_en_es.mqxliff` | [luismond/tm2tb](https://github.com/luismond/tm2tb) → `tests/data/test_bitext_en_es.mqxliff` | GPL-3.0 | memoQ 10.4.11 导出,交叉验证 docinformation/commitinfos/historical-unit 结构跨版本一致 |

审计原始输出(全量 JSON,本次审计存于仓库外
`verification/issue-146-mqxliff-gatea/audit-all.json`),可用
`python scripts/audit_mqxliff.py <files...>` 完整复现。

样本局限:**无 memoQ 导出的「标准 XLIFF」对照样本**(对照实验所需,见 §5);
无 Reviewer2/Reviewer3 多轮审阅状态样本;无 split/join 实际发生后的样本
(`mq:nosplitjoins` 仅见 filter 原因);无 .mqxlz 项目内样本;样本 5/6 出自
en→ja 的分隔文本/文档转换器,过滤器多样性不足。上述缺口均记录,不猜测。

---

## 2. 观察到的结构事实清单

以下全部为样本内直接观察到的事实(证据 = 文件名 + 审计计数)。

### 2.1 根与文件层

- 根元素 `<xliff version="1.2">`,声明 `xmlns:mq="MQXliff"`(6/6 一致)。
  **URI 是字面量 `MQXliff`,非 URL**——按 URL 模式探测会漏检,必须按字符串
  等值(建议大小写不敏感)匹配。
- `<file>` 带 `mq:id`(文档 GUID)与 `mq:projectid`(项目 GUID,6/6),
  这是 memoQ 文档身份的双主键;`datatype` 为 memoQ 过滤器全名
  (如 `x-MemoQ.DocConverters.MuLiDelimited.MuLiDelimitedConverter`、
  `x-docx`、`x-plaintext`);`original` 属性保留源机文件名。
- `header` 内:`tool`(tool-id="MQ"、tool-name="MemoQ"、tool-company="Kilgray")
  + 3 组 `mq:` 结构:`mq:export-path`(导出机绝对路径)、
  `mq:docinformation`(文档元数据:isabstract、hashistory、docname、
  majorversion/minorversion、nosplitjoin、langindepgroupid、multidocgroupid、
  origfile、numberofwords、filterconfigname 等)、其下挂
  `mq:versioninfos`/`mq:minorversioninfo`/`mq:details`(7.8 样本)或
  `mq:tagdefinition`、`mq:doccomments`、`mq:lqamodel`、
  `mq:externalpreviewinfo`(10.3+ 样本)。
- **BOM 不一致**:7.8/11.6 导出带 UTF-8 BOM(4/6),10.3/10.4 不带(2/6)。
  解析必须容忍 BOM 有无。

### 2.2 段级模型(与 SDLXLIFF 的根本差异)

- **一个 trans-unit = 一段**:无 `seg-source`、无 `mrk`、无 `sdl:seg-defs`
  类似物(6/6,145 个 trans-unit)。分段信息由 `mq:firstlabel`/`mq:lastlabel`、
  `mq:autosplitjoinstate`、`mq:nosplitjoin`/`mq:nosplitjoins` 表达。
- trans-unit 上的 `mq:` 属性全集(按样本观测):
  - **状态与角色**:`mq:status`(值域见 §2.4)、`mq:lastmodifrole`、
    `mq:segmentproofread`(presence-as-value);
  - **身份与版本**:`mq:segmentguid`、`mq:minorversionstart`/`mq:minorversionend`;
  - **保护**:`mq:locked`(presence-as-value),并**同时写标准
    `translate="no"`**(locked.mqxliff 实测);
  - **排版约束**:`mq:maxlengthchars`、`mq:multipleexactmatch`;
  - **TM 匹配溯源**:`mq:percent`、`mq:translatorcommitmatchrate`、
    `mq:translatorcommittimestamp`、`mq:translatorcommitusername`。
- **`mq:segmentguid` 不是跨导出稳定身份**:mt/pe 是同一文档的两次导出,
  51 段中 50 段 guid 一致,**TU 14 源文本未变但 guid 已变**
  (mt `fab01491-…` → pe `745faf58-…`)。round-trip 身份主键不得单独依赖
  segmentguid(对 #146 验收标准「导出重新导入身份稳定」是关键约束)。
- trans-unit 下的 `mq:` 子元素:`mq:minorversions`(内含 0–n 个
  `mq:historical-unit`,即段历史快照,其内部结构 = 当前段的镜像)、
  `mq:startingws`/`mq:endingws`(段首尾空白,`xml:space="preserve"`)、
  `mq:commitinfos`(TM 提交历史,内含 `mq:commitinfo`)、
  `mq:nosplitjoins`(split/join 禁止标记,reason 取值 filter)、
  `mq:originalsource @edited`、`mq:comments`/`mq:comment`、
  `mq:insertedmatch`、`mq:warnings40`(7.8 时代,元素名**带版本号 40**)。
- **内联标签用标准 XLIFF `bpt`/`ept`/`ph`**,非 mq: 扩展(样本实测
  document 42/42/4、mt 24/24/245、pe 45/45/492 个);标签格式元数据在
  `mq:tagdefinition`/`mq:tagdefelement`/`mq:tagdefattribute`(挂
  `mq:docinformation` 下,document 样本 40 个 tagdefelement、93 个
  tagdefattribute)。
- 上下文:`context-group` 的 `context-type` 为 `x-mmq-context` 或
  `x-mmq-structural-context`(值为 GUID 或结构路径)。

### 2.3 注释模型(双表示)

memoQ 同时写两份注释(translation_status 23 组、mt 116 组、pe 186 组):

1. 标准 XLIFF `<note>`(纯文本);
2. `mq:comments/mq:comment`(富元数据:`id`、`creatoruser`、`time`、
   `deleted`、`category`、`appliesto`、`origin`)。

两者内容对应但载体独立,round trip 必须成对保全,不得只留一份
(只留 `<note>` 会丢元数据,只留 `mq:comment` 会破坏标准 XLIFF 互读)。

### 2.4 状态值全集(样本内)

`NotStarted`、`PartiallyEdited`、`MachineTranslated`、`PreTranslated`、
`ManuallyConfirmed`、`Reviewer1Confirmed`、`Proofread`(7 种,全部来自
translation_status/mt/pe/locked/document 样本)。
`Reviewer2Confirmed`/`Reviewer3Confirmed` 推断存在(多轮审阅工作流),
**无样本**;交付级状态(如 Delivered)未观测,不猜测。

### 2.5 命名空间与序列化陷阱(对 round trip 最重要的事实)

- **默认命名空间重复声明**:memoQ 在大量 `mq:` 容器元素上重复声明
  `xmlns="MQXliff"`(document 35 处、mt 116 处、pe 186 处、
  translation_status 23 处)。任何按前缀字符串解析的实现都会把
  `<mq:commitinfos xmlns="MQXliff">` 误判;标准命名空间感知解析器
  (ElementTree/lxml)则正确解析为 `{MQXliff}commitinfos`。
  **序列化方向更危险**:按 ElementTree 重新序列化不会再现这些重复声明,
  结构等价但字节不同——Gate B 的结构 diff 白名单必须预先收录此项。
- **presence-as-value 布尔惯例**:`mq:locked="locked"`、
  `mq:segmentproofread="segmentproofread"`——值 = 本地名,**不是
  "true"/"false"**。按 `value == "true"` 判断会全部漏检。
- **CDATA 内嵌 XML**:`mq:details` 文本为 `<?xml …>` 序列化片段
  (CDATA 包裹的不透明载荷),必须字节级保全,不得解析改写。
- **带版本的元素名**:7.8 用 `mq:warnings40`,10.3+ 样本未见该元素
  ——memoQ 元素名可随版本演化,Gate B 拒绝路径必须覆盖「未知编号的
  `mq:warnings*`」。

### 2.6 伪文件反例(推断,无样本)

本批样本无「扩展名 .mqxliff 但无 mq: 命名空间」的反例(与 #145 的
small-with-notes 不同)。memoQ 也支持导出普通 XLIFF,因此扩展名同样
不可作格式判据,必须按根命名空间探测。此项记为**推断**,待对照实验补样本。

---

## 3. 兼容性矩阵

### P 类:必须原样保全、导出写回,不可编辑

| # | 结构 | 证据 | 保全方式 |
|---|---|---|---|
| P1 | `<file>` 的 `mq:id`、`mq:projectid`(文档/项目 GUID) | 6/6 | 原样携带(memoQ 回导身份键) |
| P2 | `mq:docinformation` 全树(含 `@origfile`、`@multidocgroupid` 等) | 6/6 | 原样携带 |
| P3 | `mq:export-path` | 6/6 | 原样携带 |
| P4 | `mq:versioninfos`/`mq:minorversioninfo`(文档版本史) | 6/6 | 原样携带 |
| P5 | `mq:minorversions`/`mq:historical-unit`(段历史快照) | document/translation_status/mt/pe/test_bitext | 原样携带;**不得因译文修改而改写历史** |
| P6 | `mq:commitinfos`/`mq:commitinfo`(TM 提交史) | 6/6 | 原样携带 |
| P7 | `mq:insertedmatch`(匹配来源/匹配率,含 `@source` TM 名、matchrate=101 CM) | document ×13、translation_status ×2 | 原样携带(只读展示) |
| P8 | `mq:percent`、`mq:translatorcommit*`(trans-unit 匹配溯源属性) | document/pe/translation_status | 原样携带(只读展示) |
| P9 | `mq:tagdefinition`/`mq:tagdefelement`/`mq:tagdefattribute` | 6/6 | 原样携带 |
| P10 | `mq:startingws`/`mq:endingws`、`mq:originalsource @edited` | 6/6 | 原样携带(空白语义载体) |
| P11 | `mq:nosplitjoins`/`mq:nosplitjoin`(含 `@reason`) | 6/6 | 原样携带 |
| P12 | `mq:details`(CDATA 内嵌 XML) | document(7.8) | 字节级保全,不解析 |
| P13 | `mq:warnings40`/`mq:errorwarning`(QA 警告) | document(7.8) | 原样携带;未知编号的 `warnings*` 走拒绝路径 |
| P14 | `mq:lqamodel`、`mq:doccomments`、`mq:externalpreviewinfo`/`mq:previewid*`、`context-group[x-mmq-*]` | 10.3/11.6 样本 | 原样携带 |

### M 类:可与 Folith 状态双向映射

| # | MQXLIFF | → Folith 导入 | → MQXLIFF 导出 | 证据 |
|---|---|---|---|---|
| M1 | `mq:status="NotStarted"` | 未开始(空段) | 未编辑 → `NotStarted` | translation_status ×2、locked ×2 |
| M2 | `mq:status="PartiallyEdited"` | 编辑中(非空未确认) | 编辑中 → `PartiallyEdited` | translation_status ×2 |
| M3 | `mq:status="MachineTranslated"` / `"PreTranslated"` | 机器翻译/预翻译候选,待编辑 | Folith 无此二态,导入后归入编辑中并记录来源;导出时原值回写,不降级改写 | mt ×36/×15 |
| M4 | `mq:status="ManuallyConfirmed"` | **已确认** | 已确认 → `ManuallyConfirmed` | document ×16、pe ×51、translation_status ×2 |
| M5 | `mq:status="Reviewer1Confirmed"` | 已确认 + 审阅通过(Folith 单一已审态,两者导入后等价) | 已审 → 保留原值 `Reviewer1Confirmed` 回写,不降级为 ManuallyConfirmed | translation_status ×2 |
| M6 | `mq:status="Proofread"` + `mq:segmentproofread` | **已审 + 保护** | 已审 → `Proofread` + `segmentproofread` 回写 | locked ×1、translation_status ×2 |
| M7 | `mq:locked="locked"`(伴随 `translate="no"`) | 段保护(锁定) | 保持锁定;锁定段的 trans-unit 同步写回 `translate="no"` | locked ×1、document ×1 |
| M8 | `Reviewer2/3Confirmed`(推断)、其他未知状态值 | 拒绝导入该值并报明(不猜测映射) | —— | 无样本,**推断**;Gate B 若遇到需先补样本 |

映射约束(遵循 #143 TU Contract 与 #146 架构约束):Folith 侧不新增第二套
真值系统;ManuallyConfirmed / Reviewer1Confirmed / Proofread 的「已确认/已审」
程度差异在 Folith 内收敛为统一的确认与审校两态,导出时按原文件值回写,
**不把审阅态降级改写为确认态**;MachineTranslated/PreTranslated 的来源信息
以只读溯源展示,不进入 Folith 真值。

### R 类:无法证明安全,导入时显式拒绝(说明原因,绝不静默丢弃)

| # | 情形 | 依据 |
|---|---|---|
| R1 | 观测集合(§2.2/§3 全表)之外的 `mq:` 元素或属性(含未知编号 `mq:warnings*`) | TU Contract:结构未知即无法保证携带语义正确,拒绝并报明未识别节点名 |
| R2 | 根命名空间 URI ≠ 字面量 `MQXliff`(大小写不敏感比较),但扩展名为 `.mqxliff` | 推断(样本仅见 MQXliff);不拒绝会走错适配器,应路由至通用 XLIFF 1.2 适配器(#144)并在报告中说明 |
| R3 | `mq:status` 出现 §2.4 值域之外的取值 | 推断:拒绝并报明取值,等待补样本后再扩展映射 |

---

## 4. 对 #146 实现门与 Gate 6 第三类试验的含义

- **实现门保持关闭**:本调查未发现任何「必须方言层才能表达」的结构——
  状态在 trans-unit 属性上、锁定走标准 `translate="no"`、内联标签走标准
  bpt/ept/ph。若 §5 对照实验证明 memoQ 对标准 XLIFF 的 round trip 也被
  memoQ 自身接受,#146 的合理结论就是「以标准 XLIFF 支持 memoQ 交换,
  MQXLIFF 方言层不实施」,仅在 XLIFF 适配器内保留本矩阵的 M 类映射与
  P 类保全清单作为 `mq:` 未知属性透传规则。
- 若对照实验证明 memoQ 拒绝回导标准 XLIFF(如丢失 `mq:docinformation`
  导致上下文/预览断裂),则按既定范围实施受限 MQXLIFF 方言层:
  叠加于 #144 XLIFF 1.2 适配器之上,按 §3 P 类保全、M 类映射、R 类拒绝。
- **Gate 6 第三类试验(memoQ 双语交换)在对照实验完成前不得标记完成**。

## 5. 对照实验协议(#144 合入后执行,当前记录为缺口)

1. 取材:同一源文档在 memoQ 中(a)导出 MQXLIFF,(b)导出标准 XLIFF;
   若无法获得 memoQ 环境,以样本库的 MQXLIFF + 手工剥离 `mq:` 层的
   等价标准 XLIFF 替代,并显式记录替代方案的局限。
2. 两路分别走 Folith XLIFF round trip(#144 适配器):no-op 结构 diff、
   修改译文 diff。
3. 回读验证:两路导出文件均在 memoQ 中重新导入,验证段状态、预览、
   上下文、锁定段表现;记录 memoQ 版本。
4. 判定:两路行为等价且 memoQ 接受 → 结论「标准 XLIFF 覆盖」;
   标准路丟失必要信息 → 结论「需方言层」,范围即本矩阵 §3。
5. 全程产物(样本、diff、memoQ 截图或导出对照)入
   `verification/issue-146-对照实验/`。

## 6. 测试与证据

- `tests/mqxliff_fixture_audit_test.py`:对 5 份入库样本做只读结构断言
  (根命名空间、一元一段模型、状态值域、presence-as-value 锁定、
  translate="no" 同步、注释双表示、历史快照、mt/pe 段身份配对与
  TU 14 guid 变更),作为 Gate B 之前 fixtures 的看门测试;
  审计脚本 `scripts/audit_mqxliff.py` 可复现全量 JSON。
