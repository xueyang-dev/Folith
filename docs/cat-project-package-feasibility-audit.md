# CAT 项目包可行性审计 —— Issue #147 调查报告

状态：**可行性审计完成；真实项目包样本未获得，总体结论 = 待样本。**
日期：2026-10-27 · Issue：#147 · 里程碑：v0.9.0 · 性质：只读调查，零产品代码变更

---

## 0. 结论速览

SDLPPX / SDLRPX（Trados）与 MQOUT / MQBACK（memoQ）都是 **ZIP 容器**，
内部混合了「公开可解析的 XML / 标准格式」与「专有闭源格式」。逐条目判定后：

- **双语文件条目**（包内 `.sdlxliff`、`.mqxlz`/MQXLIFF）：0.9.0 既有适配器
  （#145 SDLXLIFF Gate A、#146 MQXLIFF Gate A、#144 XLIFF safe round-trip）
  已覆盖，**这是项目包里唯一可安全处理的部分**。
- **TM / 术语条目**：memoQ 包内的 TMX 导出与 CSV 术语库可直接走 Folith 既有
  `import_tmx` / `parse_termbase_csv`；Trados 包内的 SDLTM 是 SQLite
  （可读但 schema 未经样本验证），SDLTB 是 Access/Jet 数据库（跨平台读取
  依赖第三方逆向实现，法律与工程风险高）。
- **项目设置条目**（`.sdlproj` / memoQ 内部项目状态）：XML 本身可解析，
  但语义绑定 Trados Studio 工作流（任务、统计、报告、回包状态机），
  且 memoQ 侧为专有二进制。
- **回包**：理论上可以「只替换双语条目、原样保全其余条目」并按观察到的
  约定重写 `.sdlproj`，但 **Trados Studio / memoQ 对回包的真实校验行为
  完全未经样本验证**，风险清单见 §5。

> **总体建议：待样本，且 v0.9.0 不建议对项目包做任何支持承诺。**
> 替代路径明确且已被 memoQ 官方文档背书（§4.4）：请客户改发独立双语文件，
> 或让用户解包后直接取用包内 `.sdlxliff` / `.mqxlz` / `.xlf` 双语文件。
> 样本到位后按 §6 的补充审计协议重跑本审计，再决定是否开实现 Issue。

**证据等级标注约定**（本审计无真实样本，全部结论按下述分级）：

- 【文档】= 厂商公开文档（memoQ 官方文档、RWS 文档中心）；
- 【代码】= 与 Folith 无关的第三方开源实现（4 个独立项目，commit 已固定，
  见 §1.2），仅引用其**对格式的行为**，不复制其代码；
- 【推断】= 由上述证据推理，无直接观察支撑；
- **样本内实证 = 无**。任何条目的「真实包里确实如此」均标注**未验证**。

---

## 1. 样本获取过程与结果（全部尝试均记录）

### 1.1 搜索记录（2026-10-27，方法可复现）

| 尝试 | 方法 | 结果 |
|---|---|---|
| GitHub 代码搜索 `extension:sdlppx` / `extension:sdlrpx` / `extension:mqout` / `extension:mqback` | GitHub code search API | **0 个文件**（4 项全部 total_count=0） |
| GitHub 代码搜索字符串 `sdlppx` / `mqout` / `mqback` in:file | GitHub code search API | `sdlppx` 331 个命中（全部是实现代码/文档，无包文件）；`mqback` 命中全部为 IBM MQ 消息队列误报 |
| GitHub 仓库搜索 sdlppx / mqout / mqback / trados package | GitHub repo search API | 命中 4 个格式处理项目（见 §1.2），**无一含样本包** |
| 参考仓库全树遍历 | `git/trees?recursive=1` | OpenXLIFF / SDLPPXPackager / Supervertaler / STraSAK / Open TLC 树内均无 `.sdlppx`/`.sdlrpx`/`.mqout`/`.mqback` 文件 |
| Web 搜索样本下载 | 多组关键词 | 无公开、可再分发的真实项目包样本；memoQ 官方文档明确包由 Handoff/Delivery 向导生成，无样例下载 |

### 1.2 结构证据来源（第三方开源实现，commit 已固定）

| 项目 | 许可证 | commit | 与本项目包的关系 |
|---|---|---|---|
| [maxprograms-com/OpenXLIFF](https://github.com/maxprograms-com/OpenXLIFF) | EPL-1.0 | `eff6ea9`（2026-10-23） | **读 SDLPPX → XLIFF、XLIFF → 写 SDLRPX** 的完整实现（`converters/sdlppx/`） |
| [briacp/SDLPPXPackager](https://github.com/briacp/SDLPPXPackager) | GPL-3.0 | `359063e`（2023-02-14） | 就地改写 SDLPPX 为回包、并转换包内 SDLTM/SDLTB 的实现 |
| [Supervertaler/Supervertaler-Workbench](https://github.com/Supervertaler/Supervertaler-Workbench) | MIT | `7b76d21`（2026-10-10） | Python 实现的 SDLPPX 解析 / `.sdlproj` 回包改写（`modules/sdlppx_handler.py`） |
| [KlinsBt/open-translation-client](https://github.com/KlinsBt/open-translation-client) | （仓库无 LICENSE 文件，仅作行为参考） | `c85df168`（2026-08-25） | 把 SDLPPX/RPX、MQOUT/MQBACK 统一按「ZIP 容器 + 只替换双语成员」处理的实现（`docs/CONSTRAINED_FORMAT_WORKFLOWS.md`） |

三个**许可证兼容、作者互不相关**的实现（OpenXLIFF / SDLPPXPackager /
Supervertaler）对 SDLPPX 结构的处理方式互相印证，可作为「强第三方证据」；
但对它们彼此之间回包兼容性的声明，均**未验证**。

### 1.3 样本缺口结论

**未获得任何真实来源的 SDLPPX / SDLRPX / MQOUT / MQBACK 文件。**
按 Issue #147 的 Failure/Rollback 规则，总体结论记为**待样本**；
本文所有结构描述均为【文档】/【代码】/【推断】级证据，
每一条都不构成「文件内观察到的事实」。

---

## 2. 包结构清单（逐条目，含证据等级）

### 2.1 SDLPPX（Trados 项目包）

【代码】三方一致 + 【推断】补充。条目清单：

| 条目 | 角色 | 格式 | 证据 | 备注 |
|---|---|---|---|---|
| `<名称>-<时间戳>.sdlproj` | 项目设置 | XML（根元素 `ProjectPackage`，属性 `PackageType="ProjectPackage"`） | 【代码】OpenXLIFF `getPackageLanguages`、SDLPPXPackager、Supervertaler 三方一致 | 包根目录（【代码】Supervertaler `glob('*.sdlproj')` 只扫根）；OpenXLIFF 观察到文件名含 `yyyyMMdd-H'h'mm'm'ss's'` 时间戳后缀的命名约定 |
| `<源语言代码>/*.sdlxliff` | 源语参考 | XLIFF 1.2 + `sdl:` 命名空间 | 【代码】OpenXLIFF 按语言码父目录分流 | 与 #145 审计过的 SDLXLIFF 同构 |
| `<目标语言代码>/*.sdlxliff` | **双语工作文件（译者实际翻译的对象）** | 同上 | 【代码】三方一致（OpenXLIFF 仅转换该目录、Supervertaler 仅加载该目录） | 语言码目录大小写与分隔符（`-`/`_`）不统一，三方实现都做了归一化兜底【代码】 |
| `*.sdltm`（任意深度 ≤10） | 翻译记忆 | **SQLite 数据库** | 【代码】SDLPPXPackager `jdbc:sqlite:` 直连 | schema 专有、未见公开规范；包内是否必含【文档】可选 |
| `*.sdltb`（任意深度） | 术语库 | **Microsoft Access/Jet 数据库** | 【代码】SDLPPXPackager 用 Jackcess（纯 Java Jet 逆向实现）打开 | 专有二进制格式，无公开规范 |
| `Reports/`、`File Types/` 等 | 报告 / 过滤器设置 | XML / 其他 | 【代码】Supervertaler 在回包中显式排除 | 内容细节**未验证** |

`.sdlproj` 内观察到的元素【代码】（三方交叉）：`LanguageDirections/LanguageDirection`
（`SourceLanguageCode`/`TargetLanguageCode`）、`Tasks`（`AutomaticTask`、
`ManualTask@PercentComplete/Status/CompletedAt/TaskFile@Completed`）、
`Reports`、`TermbaseConfiguration`、`ConfirmationStatistics`（`Draft`/`Translated`
等，含 `Words/Characters/Segments/Placeables/Tags` 计数）、`PackageCreatedAt`、
`PackageCreatedBy`、`LanguageFile@LanguageCode > FileVersions > FileVersion@FileTimeStamp`。

**没有观察到任何 manifest / 哈希 / 签名条目**【代码·否定性证据】：三个实现
重建 ZIP 时均未复算任何校验值。但「Studio 不校验」本身**未验证**（§5）。

### 2.2 SDLRPX（Trados 回包）

【代码】三方一致：结构与 SDLPPX 相同的 ZIP，差异仅在 `.sdlproj`：

1. 根元素 `PackageType="ReturnPackage"`（SDLPPXPackager / OpenXLIFF /
   Supervertaler 都只改这一个属性起点）；
2. OpenXLIFF 另外移除 `TermbaseConfiguration`、移除 `AutomaticTask`、清空
   `Reports`、仅保留目标语言 `LanguageFile` 并刷新 `FileVersion@FileTimeStamp`、
   按约定重命名 `.sdlproj`；
3. Supervertaler 额外把 `ConfirmationStatistics` 的 Draft 计数挪到 Translated、
   把 `ManualTask` 置 `Status="Completed" PercentComplete="100"`、剥掉
   `.sdlproj` 文件名中的目标语言后缀。

两个实现回包改写深度不同——即**回包最小必要变更集没有公认答案**，
这正是没有公开规范的直接后果。

### 2.3 MQOUT / MQBACK（memoQ 交接 / 交付包）

【文档】memoQ 官方文档（handoff wizard，docs.memoq.com 11.6）。条目清单：

| 条目 | 角色 | 格式 | 证据 | 备注 |
|---|---|---|---|---|
| 双语文档 | 工作文件 | `.mqxlz`（ZIP 内 ZIP，内含 MQXLIFF）/ `.xlf` | 【文档】官方 fallback 指引明示解包后可直接导入 | 与 #146 审计过的 MQXLIFF 同构【推断】 |
| 本地 TM | 记忆库 | **TMX 导出** | 【文档】「a TMX export of all local translation memories」 | 公开标准 |
| 本地术语库 | 术语 | **CSV 导出** | 【文档】「a CSV export of all local term bases」 | 公开格式 |
| LiveDocs 语料 | 参考语料 | **专有二进制** + XLIFF 双份 | 【文档】「binary internal format, and also in the form of XLIFF files」 | 二进制部分无公开规范 |
| 在线资源引用 | 服务器资源 | 引用（非副本） | 【文档】 | 依赖 memoQ TMS 连接 |
| 预览文件 / 最终导出骨架 | 预览 / 回导出 | 未公开 | 【文档】提及存在 | 细节**未验证** |

MQOUT 与 MQBACK 的内部差异**未验证**（官方文档只描述工作流语义：
MQOUT = 交接包、MQBACK = 交付包、允许部分交付）。
**未发现任何处理 MQOUT/MQBACK 的开源实现**（§1.1），其 ZIP 清单、
内部项目状态、校验机制全部**未验证**。

---

## 3. 逐条目可处理性判定（对照 Folith 0.9.0 既有能力）

| 条目 | 可处理性 | 依据 |
|---|---|---|
| 包内 `.sdlxliff` | **可处理**（受 #145 Gate A 边界约束） | SDLXLIFF 适配器结论已建立：XLIFF 1.2 + `sdl:` 方言，24 P / 2 M / 4 R |
| 包内 `.mqxlz` 内的 MQXLIFF | **可处理**（受 #146 Gate A 边界约束） | MQXLIFF 兼容矩阵已建立；`.mqxlz` 本身是 ZIP 套 ZIP，解包属普通 ZIP 处理【推断】 |
| MQOUT 内 TMX | **可处理** | Folith 已有 `import_tmx`（`folith/engine/glossary_checks.py`，文档字符串即点名 Trados/memoQ 导出） |
| MQOUT 内 CSV 术语库 | **可处理**（列语义需对齐） | Folith 已有 `parse_termbase_csv`（同文件，Source/Target 列约定） |
| `.sdlproj` | **半可处理**：XML 解析无障碍；工作流语义（任务/统计/回包状态）不建议建模 | §2.1、§2.2；语义绑定 Studio |
| SDLTM（SQLite） | **待样本**：可读性高（SQLite 公开格式），但 schema 未经样本验证，导出语义（字段、TU 结构、字段过滤）未知 | §2.1 |
| SDLTB（Jet） | **不建议处理**：无跨平台公开实现路径之外的选择，依赖第三方逆向库，见 §4 | §2.1 |
| LiveDocs 二进制语料 | **不可处理** | 专有二进制，无公开规范【文档·否定性】 |
| `.sdlproj` 之外的 Studio 专有条目（预览、过滤器等） | **不可处理**，原样保全 | 无公开规范【推断】 |

结论与 #147 预期一致：**项目包不是「解压重打包就兼容」**——可安全处理的
只有包内的标准/已审计双语条目与标准资源导出（TMX/CSV）。

---

## 4. 许可与法律边界

1. **分析边界**：仅分析文件本身的可见结构（ZIP 条目表、XML 文本、SQLite
   schema），不反编译任何二进制、不绕过任何技术保护措施、不依赖未授权的
   专有软件组件（不链接 SDL / memoQ 的 DLL 或 SDK 之外要求授权的部件）。
2. **专有格式事实**：SDLPPX/RPX、MQOUT/MQBACK 无任何公开格式规范【文档·否定性】；
   本文全部结构知识来自厂商文档与第三方开源实现的公开源码。
3. **高风险依赖点**：SDLTB（Jet）的读取在业界依赖 Jackcess 之类**对专有格式
   的逆向实现**；即便其许可证干净（Jackcess 为 Apache-2.0），依赖逆向实现
   处理客户数据与 Folith 的边界不符，建议永不依赖。
4. **无加密证据**：所有引用实现都把包当普通明文 ZIP 处理【代码】，未观察到
   加密条目；但**未验证**真实包是否可能出现受保护条目——若出现，按本节
   边界直接跳过并记录，不得尝试解密。
5. **样本入库规则**（未来样本到位时执行）：仅入库来源与授权明确允许再分发
   的样本；涉及第三方客户内容时只记录结构与 SHA-256 清单，样本本身存仓库外
   `verification/issue-147-package-audit/`（对齐 #145/#146 惯例）。

---

## 5. 回包语义与风险清单（RPX / MQBACK）

回包 = 客户「交回项目包」时，Folith 在保全包内其他条目的前提下替换双语文件
条目。可行性受以下风险约束（全部**未验证**，因无样本）：

| # | 风险 | 现有证据 |
|---|---|---|
| R1 | **最小必要变更集不明**：两个独立实现对 `.sdlproj` 的回包改写深度不同（§2.2），改少了可能被 Studio 拒收，改多了可能在某些项目类型上出错 | 【代码】OpenXLIFF vs Supervertaler 差异 |
| R2 | **Studio / memoQ 端校验行为未知**：未观察到 manifest/哈希【代码·否定性】，但不排除 Studio 按 GUID、任务 GUID 或包内计数做一致性校验 | 无样本 |
| R3 | **统计一致性**：`.sdlproj` 的 `ConfirmationStatistics` 与包内双语文件实际段状态可能被交叉核对（Supervertaler 专门搬计数，暗示存在这种失败模式【推断】） | 【代码】+【推断】 |
| R4 | **命名与版本兼容**：`.sdlproj` 文件名时间戳约定【代码】、多语言方向包（一个包多个 `LanguageDirection`，OpenXLIFF 代码显式处理【代码】）、Studio 版本间 schema 演进——均无样本覆盖 | 【代码】+ 未验证 |
| R5 | **MQBACK 完全不可评估**：无开源实现、无内部结构文档，memoQ 更新主项目的机制（工作流状态迁移）只有官方文档的行为级描述 | 【文档】 |
| R6 | **一次往返、全程无法自测**：与 XLIFF 系适配器不同，项目包 round-trip 没有 Studio/memoQ 客户端就无法做回读验收；#145 已确立「未经客户端回读验证不得宣称支持」的先例 | 本仓库先例 |

**回包路线的工程含义**：即便将来 go，实现也必须采用「读原包 → 全条目原样
复制 → 仅替换双语条目 + 按观察集重写 `.sdlproj`」的保守策略（【代码】三方
实际做法一致），并显式拒绝任何 `R` 级未知条目变更。

---

## 6. go / no-go 建议与补充审计协议

### 6.1 建议：**待样本**；对 v0.9.0 按 no-go 对待

- 依据：§1.3 样本缺口（结论只能到「待样本」）+ §5 回包风险（即便结构清晰，
  R2/R6 无法在无客户端条件下闭合）。
- **不产生任何支持声明**：文档、UI、发行说明不得出现「支持 SDLPPX / MQOUT」
  或类似表述；本审计本身不是能力承诺。
- v0.9.0 迁移闭环不依赖项目包（Issue #147 明示），此结论不阻塞任何 Gate。

### 6.2 替代工作流（客户发来项目包时）

1. **首选**：请 PM 改发独立双语文件（SDLXLIFF / MQXLIFF / XLIFF）——
   这正是 0.9.0 迁移闭环的主路径（#144/#145/#146）。
2. **用户自助**：项目包是 ZIP【代码/文档】，用户可自行解包取出双语文件。
   memoQ 侧该路径有**官方背书**【文档】：官方文档明确建议「如果交付包无法
   更新项目，解包并导入其中的 `.xlf` / `.mqxlz`」（注意官方同时声明该路径
   不更新工作流状态）。
3. 包内 TMX / CSV 术语资源可用 Folith 既有导入功能单独吸收（§3）。

### 6.3 样本到位后的补充审计协议（触发条件：获得 ≥1 份真实 SDLPPX/SDLRPX、≥1 份真实 MQOUT/MQBACK）

1. 记录来源与授权（§4.5 规则），计算 SHA-256；
2. 产出全量 ZIP 条目清单 + 角色标注 + 哈希清单（对齐 #145 的
   `scripts/audit_sdlxliff.py` 规格，新增 `scripts/audit_cat_package.py`）；
3. 逐条目复核 §2、§3 判定，把【代码】/【文档】升级或降级为样本内实证，
   重点验证：`.sdlproj` 实际 schema 与元素全集（§2.1）、是否存在校验条目
   （R2）、MQOUT/MQBACK 内部结构（R5）；
4. 若双语条目与 `.sdlproj` 全部可解释且无未知保护机制 → 可开**只读导入**
   的实现 Issue（回包仍单独评审）；否则维持 no-go 并记录原因。

---

## 7. 验收对照（Issue #147 Acceptance Criteria）

- [x] 审计文档合入 `docs/`：结构清单（§2）、逐条目可处理性判定（§3）、
      许可边界（§4）、回包风险（§5）、go/no-go 建议与依据（§6）。
- [x] 结论可追溯性：因无样本，本文采用【文档】/【代码】/【推断】三级标注，
      全部来源（含搜索方法、参考仓库 commit、官方文档 URL）在 §1 可复现；
      **所有「真实包内确实如此」的断言均不存在，样本缺口显式标注（§1.3）**。
- [x] 无任何支持声明；no-go/待样本结论下，替代工作流（双语文件交换 +
      官方背书的解包导入路径）已明确写出（§6.2）。
- [ ] （未来）真实样本补充审计：按 §6.3 协议，样本到位后另行执行。
