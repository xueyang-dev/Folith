# Project：任务、术语、风格规则、人工决定与已审校记忆的长期容器

本文件说明 Folith 的 Project 层——蓝图 §3.2 定义的 Project / Job 边界，
以及"跨任务复用"具体是怎么发生的。

- 领域模块：`folith/project.py`
- 界面入口：侧栏「工作区 → 项目」（`/projects`）与 `/projects/:projectId`
- 测试：`tests/project_lifecycle_test.py`、`tests/project_task_navigation_test.py`、
  `tests/project_memory_test.py`
- 蓝图依据：[开发蓝图](foliothread-agentic-native-blueprint.md) §3.2、§4.2

## TM 当前边界与后续方向（2026-09-14）

当前项目 TM 仍是 source 字符串索引的 reviewed 记录，支持 exact / normalized 查询和基础 TMX 导入导出。项目隔离与知识治理已落地，不等于 fuzzy、context、concordance 检索已完成。

TM V2 排在 DOCX Contract v1 闭环之后，详见[蓝图](foliothread-agentic-native-blueprint.md#7-当前进度与开发路线)。它需要先正确表达语言对、同源多译、来源与候选差异，修正 TMX 的语言方向与 inline 内容处理；候选召回、应用和新位置的审校批准必须分开。不得直接让 fuzzy 命中继承历史 reviewed 状态，也不新增第二份项目记忆真值。

## 两个实体，两个词，两套导航状态

| | Project | Translation Task |
| --- | --- | --- |
| 是什么 | 长期存在、跨任务复用的容器 | 一次具体文档翻译执行 |
| 保存什么 | 术语、风格规则、人工决定审计、项目级设置 | 文档、段落、译文、审校与交付状态 |
| 数量关系 | 一个 Project 包含多个 Tasks | 一个 Task 属于一个 Project（默认归系统工作区） |
| 导航状态 | `active_project_id` + `active_project_tab` | `active_job_id` + `workspace_section` |
| 持久字段 | `project.json` | `state["project_id"]` |

## 身份：不可变 UUID，名称只是标签

每个项目——包括系统工作区——都有一个**真实持久化 UUID**：

```text
outputs/projects/<project_uuid>/project.json
```

- ID 由 `uuid.uuid4()` 现场生成，**不由名称派生**。改名不换 ID，项目记忆目录、
  任务归属和任何链接都不受影响；
- Display name 可以改、可以是中文、可以重复出现在文案里，但它**不是主键**，
  也**不出现在磁盘路径或路由里**；
- 名称仍然要求唯一：名称是人工入口（创建、按名称归档、picker 选项），重名会让
  "选哪个项目"变成猜谜。校验在 `core.validate_project_name`。

## 系统工作区「未分类」

「未分类」不是一个虚拟常量，而是磁盘上一个**真实项目**：

```json
{
  "project_id": "<固定 UUID>",
  "is_system": true,
  "name": "未分类"
}
```

- UUID 由 `uuid.uuid5(NAMESPACE, "unclassified")` 确定性派生：同一个常量永远得到
  同一个 UUID，因此"还没落盘"与"已经落盘"是同一条记录，界面与测试都不需要先写文件
  才知道它的 ID；
- 它承载所有**没有归属**的任务：迁移前没有 `project_id` 字段的旧任务、显式
  `project_id: null` 的任务，以及历史数据里写成字符串 `"default"` 的任务；
- `is_system=true`，因此**不允许重命名、归档或删除**。理由是具体的：这些任务需要
  一个容器，删掉容器等于让它们无处可去；
- 它拥有自己的记忆空间，可以正常积累锁定术语与已审校记忆，也可以被提升。

**字符串 `"default"` 只是历史别名。** 任何入口（导航、导出、删除、归档）先经
`canonical_project_id` 归一到系统项目 UUID：

```python
core.normalize_project_id("default") == core.system_project_id()   # True
```

`projects/default/` 这类旧目录会在变更路径上被搬到 UUID 目录（`migrate_legacy_layout`），
旧记录里 `project_id: "default"` 在读取时被改写为 UUID 并置 `is_system`，因此**读一次
旧记录就等于完成迁移**，不需要单独的迁移脚本，也不会出现"旧记录打开还是 default"的
分叉。这条收敛点修掉的正是 `ValueError: 项目不存在：default`。

## 侧栏：Project 只占一个分组（标题即管理入口 + 上下文 selector）

层级是 **Workspace ▸ Project ▸ Task ▸ Run**：

```text
Logo
＋ 新建任务

项目 ›                                    ← 分组标题**本身**就是 Project Center 入口
                                          （12px/500 标题字型、与「工作区」同一条基线；
                                           chevron 紧跟标题；纯导航）
📁 示例项目   ▾         ← context selector：state / switch action
                        （点击展开轻量下拉面板；为后续 Task 提供术语 / TM / 风格规则）
                        中性面 + 1px 细边 + 46px/11px 圆角；权重低于「新建任务」CTA
  ┌ ✓ 未分类任务  Inbox 20 / 示例项目 3 / ＋ 新建项目 ┐
  └ compact row：38px 单行、无边框、行间 2px、名称省略、计数靠右、列表内滚动 ┘
                        footer「＋ 新建项目」是透明 action row，hover 才出浅底

当前任务                ← task flow，不是 project / global navigation
01 文档与画像 / 02 翻译策略 / 03 交付内容 / 04 确认运行

工作区                  ← 只有跨项目的**资料**（AI Engine 不在这里）
历史任务
术语与翻译记忆

─────────────────────   ← 分隔线：navigation / runtime status
○ AI引擎        管理    ← status label（左，不可点）+ 唯一 action（右，文字按钮）
deepseek-v4-flash-0731  ← secondary text：当前模型
尚未验证连接             ← tertiary/status text：连接状态
```

- **Project Context 与 Project Management 同属 Project，因此在侧栏是同一个分组**：
  同一个分组标题、同一个容器、上下相邻，中间不插分隔线。此前"顶部上下文 + 底部
  「工作区」里的项目中心"会被读成两套系统 —— 这正是要消除的割裂感；
- 管理入口**收敛到分组标题本身**：Project Center 就是"所有项目"这一层，做成标题下面
  一个独立行会让同一件事在同一分组里出现两次，还让它去和 selector 争视觉重量。因此
  侧栏里**没有**独立的「项目中心」行；
- 组内是**主次**而不是并列：selector 是主控件（46px、中性面 + 1px 细边、11px 圆角、
  12.5px/600，focus ring 只留一条 2px outline），标题是标题字型（17px 行盒、透明底、
  12px/500，与「工作区」同一条基线），权重明显更低，"能点"只体现在 hover、下划线与
  chevron 上。selector 自己也低于「新建任务」CTA（CTA 是实心主色）；
- Inbox 行只有一个焦点：名字 13 → 计数 11.5 → `Inbox` 9.5px 且颜色更淡；
- 标题的当前页标记只在 `/projects` 列表页出现（文字升 ink + chevron 上色）。一旦打开
  某个项目详情，当前态由 selector 承担（它显示项目名），同组不会同时亮两个"你在这儿"；
- 「工作区」只放跨项目的**资料**：历史任务 / 术语与翻译记忆。它**不是**
  Project 的一部分，Project Center 因此不在这里——否则会被读成"和历史任务同类的第三个
  资料库"；
- **AI Engine 不是「工作区」的第四行导航**，而是贴底的 **runtime status module**
  （`provider_status`）：`AI引擎` 是 status label（不可点），`管理` 是唯一 action，
  模型名是 secondary text，连接状态是 tertiary/status text。整块**不做** clickable
  card，也不套用 `.st-key-library_nav` 的导航行语法（48px 行高 / hover 面 / primary
  选中态）——否则它会被读成"第三个资料库"那一类东西；
- **侧栏里指向 Model Center 的入口只能有一个**（`manage_provider`）。侧栏曾经同时有
  「⚙ 设置」行与「管理」，两者都进 `app_view == "settings"`，是纯重复导航。当前产品
  **没有**独立的 General Settings 信息架构，所以「设置」行已删除。注意
  `app_view == "settings"` 这个 **route 与页面本身保留**：主工作区多处就地入口仍在
  复用它（新建任务第 4 步的「前往设置 / 检查设置 / 测试连接」、翻译与审校流程的
  「前往 AI 设置」），删掉的只是侧栏这一层重复的 surface。只有当 language /
  appearance / storage / privacy / shortcuts 这类**应用级**配置真的存在时，才恢复
  「设置」，届时 `Settings → /settings` 与 `AI Engine 管理 → /model-center` 语义分离
  （详见 `docs/sidebar-project-group-ia.md §9`）；
- ⚠️ **不要给「管理」加 `help=`**：tooltip 会把 `button` 包进 `stTooltipHoverTarget`，
  而 `.st-key-provider_status .stButton > button` 用的是**直接子选择器**，会静默落空、
  退回 Streamlit 默认描边按钮。要加 tooltip 必须先把那条 CSS 改成 `.stButton button`
  后代写法（`tests/sidebar_ai_engine_footer_test.py` 钉住了这条约束）；
- 上下文 selector 是一个 compact 控件：点击在它下方展开**轻量下拉面板**（不是大型
  Modal）。面板只负责 context switching：每行是 **compact row**（单行、名称长则
  ellipsis、计数靠右且不参与收缩、整行可点），当前上下文用浅色 selected 面 + check
  表示；系统工作区「未分类任务」只带一个极轻的 `Inbox` 标签（不再堆「系统工作区」
  +「N 个未归入项目的任务」那几层说明）；切换后新建任务的归属同步更新（**已有任务不会
  移动**——这句说明在 selector 的 tooltip 里，不在面板里常驻占位），并显示 toast；
  选中后面板立即收起；
- 项目较多时列表在**区域内滚动**（`max-height` + `overflow-y:auto`），「＋ 新建项目」
  在滚动区之外，不会被项目数量推走；搜索框只在项目达到阈值（≥5）时才出现，
  不无条件占掉侧栏一整行；**面板与行都不能横向溢出**：`min-width:0` +
  `max-width:100%` + `overflow-x:hidden` 是硬约束；
- 项目重命名、归档、删除等管理动作不在面板内完成——面板里**没有**「管理所有项目」，
  因为管理入口只有一个（分组标题）；
- 分组标题进入 `/projects`（管理页）。它是**纯导航**：不打开切换面板、不弹 Modal、
  也**不改变当前 Project Context**（路由由 `projects_route` 表达，上下文保留）；

- 正文的 [更改] / [选择项目] 展开的是**同一份**切换列表（就近锚点）；两处锚点同一时刻
  只展开一个；
- 没有选择具体项目时，selector 显示「未选择项目 ▾」——同一个 compact 控件的中性态，
  不是第二行说明，也不是一块虚线空卡片。任务落到系统工作区「未分类任务」，那是真实
  容器，不是一条空状态文案。

## 新建任务页：只读的项目上下文（不再是第二个选择器）

```text
项目上下文
📁 示例项目
继承项目术语、翻译记忆和规则     [更改]

项目上下文
📥 未分类任务
任务保存到系统工作区，不继承项目上下文   [选择项目]
```

- 这里**不**再提供「所属项目」下拉框：Project Context 只有一个来源（侧栏 switcher），
  两个并列的选择器迟早会给出互相矛盾的答案（侧栏说 A、任务说 B）；
- `[更改]` / `[选择项目]` 打开的正是那一个 switcher；在「新建任务」流程里切换或新建
  项目不会把用户拽去项目详情页；
- Task 归属（`task_project_id`）是上下文的**投影**：每次运行都会对齐，不存在
  "侧栏已切到 B、任务还挂在 A"的中间态。

## `/projects`：项目管理页

首屏就是管理页，没有隐藏的创建表单：

- **页面 header**：`项目` + `管理任务、术语、风格规则与项目记忆`，右侧同一 header
  container 内提供 `新建项目`，避免 CTA 漂浮在页面边缘；
- **系统工作区**：「未分类」使用 compact pinned workspace card，只显示 inbox icon、
  `系统` badge、简短说明、任务 / 术语 / 记忆数字和 `打开`；项目首页不显示 UUID、创建/精确
  更新时间或持久化实现说明；
- **我的项目**：普通项目使用响应式两列卡片网格（窄屏一列），每张卡片给出 folder icon、
  display name、description、任务数、术语与规则、人工决定与已审校记忆数、相对更新时间；
  **整张卡片可点**进入 `/projects/:projectId`，右侧 overflow menu 继续提供打开项目 /
  重命名 / 编辑 / 归档 / 删除；
- **统一 toolbar**：搜索、排序与状态筛选在同一行；状态筛选提供「活动项目 / 已归档 /
  全部」，不再单独使用归档 checkbox；
- **新建项目**：modal，第一版支持 name 与 description；创建成功后**直接进入项目详情**；
- JSON 导入是 advanced / migration 动作，收在「更多 → 导入项目」里。

## `/projects/:projectId`：四个一级 tab

| Tab | 内容 |
| --- | --- |
| **概览** | 基本信息（真实项目 ID、类型、状态、创建/更新时间、描述）、记忆统计（任务 / 锁定术语 / 风格规则 / 已审校记忆）、最近 5 个任务 |
| **任务** | 只列出**属于本项目**的任务，支持搜索与状态筛选，并提供"把其它任务移入本项目"的批量动作 |
| **项目记忆** | 锁定术语、风格规则、人工决定、已审校记忆、待决冲突，以及导出 |
| **设置** | 项目名称与描述（编辑 / 重命名）、归档与恢复、危险操作（永久删除） |

任何 Project 操作成功后，侧栏「当前项目」、项目列表与新建任务的 picker 都与磁盘
保持同步，并给出一条 toast 反馈（成功 / 失败 / 提示三种语气）。

## 生命周期：归档可恢复，删除是永久的

| 动作 | 可逆 | 效果 |
| --- | --- | --- |
| **归档** | ✅ 可恢复 | `status=archived`：离开活动列表与新建任务的选择器；**任务、项目记忆、已审校记忆全部保留** |
| **恢复** | — | `status=active`，重新出现在活动列表与选择器里 |
| **删除** | ❌ 不可逆（但有备份） | 移除项目目录（含其翻译记忆文件），删除前自动写备份 |

界面上归档 / 恢复 / 删除都在 modal 里完成（需要输入或破坏性的动作不该挤在列表页）：

1. **系统工作区永远不可归档或删除**，也不可重命名；
2. **仍含任务的项目禁止删除**：弹窗明确提示"请先把这些任务移到其它项目，或删除
   任务"，并提供前往「任务」tab 的入口。第一版刻意不提供"删项目时顺手挪任务"的
   一键动作——那是最容易误伤的操作；
3. **没有任务的项目仍需二次确认**：必须逐字输入项目名称，按钮在此之前始终禁用；
4. **删除前自动写备份**到 `outputs/projects/_deleted/<id>-<时间>.json`。备份就是
   `import_project_memory` 直接能读的格式，因此误删在实践中仍然可恢复；删除成功后
   界面把备份文件名告诉用户。

`core.delete_project(move_jobs_to=...)` 仍保留给程序化调用（先批量移走再删），
但界面不暴露它。

## 结构

```text
outputs/
  projects/<project_uuid>/project.json             ← 项目记忆（跨任务）
  projects/<project_uuid>/translation_memory.json  ← 命名项目的已审校译对
  translation_memory.json                          ← 系统工作区的已审校译对（历史路径）
  <job_id>/state.json                              ← 任务
```

**列出项目是纯读取，不创建任何文件。** 系统工作区由变更路径（创建项目、归档任务、
提升记忆）通过 `ensure_system_project()` 落盘；只读展示使用 `system_project_view()`
在内存中给出。这条不变量很重要：此前的实现会在"列出项目"时顺手创建默认项目，于是
任何只读访问——界面渲染、脚本、eval、测试——都会在 `outputs/` 下写文件。

任务属于哪个项目由该任务 state 中的 `project_id` 决定，是**唯一真值**；项目的任务
列表由扫描任务派生，不另存 `job_ids`。`list_project_jobs(system_id)` 与
`list_unassigned_jobs()` 是同一份派生结果。

## 翻译记忆按项目隔离

翻译记忆是"已审校译文的受控记忆"，蓝图 §3.2 把它列在 Project 之下。Folith
的记录形状是 `{"<目标语言>␟<原文>": {target, target_lang, reviewed}}`——键是
「目标语言 + 原文」的**作用域键**（见下一节）。同一个 source 在不同项目里译法不同
时，一份文件无法同时表示两种译法。这就是为什么按**项目分文件**，而不是在单文件里
加一个 `project_id` 字段：

| 项目 | 记忆路径 |
| --- | --- |
| 系统工作区「未分类」 | `outputs/translation_memory.json`（沿用历史全局路径） |
| 命名项目 | `outputs/projects/<project_uuid>/translation_memory.json` |

**没有迁移步骤。** 系统工作区继续使用历史上的全局文件，因此既有任务的数据与行为
完全不变；命名项目各自从空白记忆开始。`load_tm(None)` 仍读写系统工作区的记忆，
`tm_path("default")` 与 `tm_path(system_id)` 是同一个文件。

新建项目从空白记忆开始是隔离的代价。如果希望把既有积累带过去，项目详情的
「项目记忆」tab 提供一个**显式**动作「把「未分类」的已审校记忆并入本项目」：
只增不改（不覆盖本项目已有的译法），幂等，且只在"本项目为空且系统工作区有内容"
时出现。

「术语与翻译记忆」页按项目展示记忆条数，并可选择查看/清空某一个项目的记忆。

## 翻译记忆按目标语言隔离

**一条翻译记忆的身份是「目标语言 + 原文」，不是「原文」。** 同一段原文在
简体中文与 Français 任务里的正确译文不同；只拿原文当键，一种语言的已审校译文
就会串进另一种语言的任务，跳过模型调用、被标成已审校、并通过交付检查——译文是
另一种语言，而且完全静默。

作用域键因此是结构性的（`folith/translation_memory.py`，核心层与展示层共用
同一份格式，避免两边各写一份而漂移）：

- `tm_scope_key(language, source)` 生成键；语言为空时返回空键；
- `tm_lookup(tm, source, index, target_lang=…)` **必须**给出目标语言：语言未知
  或不同 → 不命中。这是正确性约束，不是可选过滤；
- `tm_index(tm, target_lang)` 只收录语言可证明相同的条目；没有目标语言时返回
  空索引——作用域不可证明就不建立任何可命中路径（fail closed）；
- 写入侧同样收紧：流水线晋升、检查点恢复、人工晋升、语义审校、失效删除、
  术语陈旧、项目迁移、TMX 导入、冲突合并。

**旧条目的处理是保守的。** 历史版本写下的、没有目标语言标注的条目**保留在文件里**
（不删用户数据），但**永不自动命中**，也不会被猜成某种语言。理由与"不做语义模糊
匹配"相同：翻译记忆是错误放大器。可用 `tm_legacy_keys` 把它们单独列出来，
「术语与翻译记忆」页会提示"未标注目标语言的旧记忆不会被自动复用"。

TMX 导入同理：条目必须带目标语言才入库，取调用方显式给出的 `target_lang`，
调用方没给时退回目标 `<tuv xml:lang>`。**刻意不做** BCP-47 → 显示名的映射
（`zh-CN` → `简体中文`）：猜测式映射会让导入"看起来成功、实际永不命中"，
比显式失败更糟。

### 导出载荷里语言是**显式字段**，不是键的副产品

项目记忆导出（`foliothread-project-memory` v1）的 `translation_memory` 是
`{作用域键: {target, target_lang, reviewed}}`。`target_lang` **必须**随记录一起写。

理由：只靠键前缀传语言，等于把语言身份寄托在一个内部约定上——任何一次键重写
（规范化、去重、第三方工具处理）都会**静默**抹掉它，而载荷是跨机器交换的文件，
读方无从得知我们内部的分隔符约定。记录里声明了语言，语言才是载荷的一部分。

导入侧按 `tm_record_language` 解析，顺序是**记录字段优先、键前缀兜底**：

| 载荷形态 | 导入结果 |
| --- | --- |
| 作用域键 + `target_lang` 字段（当前导出） | 语言 = 字段值（权威） |
| 作用域键、无字段（修复前的导出把键原样写进了载荷） | 语言 = 键前缀 |
| 纯原文键、无字段（更早的载荷） | 语言未知 → 保留条目但**永不自动命中** |

字段与键前缀冲突时以**字段**为准。三种形态都不会产生跨语言命中；语言不可证明时
一律走 fail-closed 分支。

## 什么能进入项目记忆

项目记忆只接受**已被人工确认**的内容。这是 Memory gate（蓝图 §4.2）的落点：

| 内容 | 是否进入 | 判定依据 |
| --- | --- | --- |
| 锁定术语（`status="locked"`） | ✅ | 复用 `models.GlossaryEntry` 的规范形态 |
| 候选 / 暂定术语 | ❌ | 从未经人工确认 |
| 已确认风格规则 | ✅ | 复用 Translation Core 的 `confirmed_style_rules` |
| 待定风格规则（`proposed`） | ❌ | 同上 |
| 人工决定（`actor_type="human"` 且有 actor） | ✅ | 审计用 |
| 模型自身的记录 | ❌ | 模型不能为自己的输出背书 |
| 未审校译文 | ❌ | 只进入既有 TM 存储，不进项目记忆 |

**不变式：一个 source 至多一条锁定条目。** `models.entry_id` 把 target 也算进
ID，所以"人工修正首选译名"会产生新 ID；项目记忆按 **source** 归并并就地替换，
避免同一术语留下两条互相冲突的锁定要求。

## 跨任务复用是怎么发生的

1. 在任务里完成术语确认（锁定 / 冻结）或风格确认；
2. 在工作区「术语」页点击 **提升到项目记忆** → 只提升已确认内容；
3. 之后新建任务时选择该项目 → 管线在**任务开始前**注入项目记忆：
   - 项目锁定术语并入任务的 `user_glossary`（状态强制为 `locked`，因此不会
     卡在严格术语治理门禁，且术语合规检查会强制使用项目首选译名）；
   - 已确认风格规则并入 `style_rules`。

注入时机很关键：**只在任务尚未开始工作时注入**。进行中的任务不会因为项目记忆
变化而改变术语（否则会触发术语依赖失效，把已完成的译文作废）。

任务自带术语优先于项目记忆：同一个 source 同时来自两处时保留任务自带的，
项目条目被记为未注入，因此不会产生重复要求。

每次注入都在任务状态里留下审计记录：

```json
"project_memory": {
  "project_id": "...", "glossary_version": 1, "glossary_hash": "...",
  "injected_entry_ids": ["t-..."], "injected_at": "..."
}
```

界面在术语页显示"本任务已注入项目记忆：锁定术语 N 条 · 项目术语版本 v1"。

## 刻意不做的事

- **项目文件里不存翻译记忆的副本。** 翻译记忆拥有独立存储（按项目一份），
  `project.json` 只记录术语、风格与人工决定审计，不复制译对——避免"两份 TM
  真值"。项目视图的项目记忆条数由 TM 存储读出。
- **不引入数据库。** 沿用本地 JSON（蓝图 §3.2 允许先映射到本地文件）。
- **不自动提升。** 候选术语不会因为"翻译完成"而进入项目记忆；必须点击提升。
- **不静默共享翻译记忆。** 跨项目的记忆带入只能由人显式发起。

## 已验证的行为

三套测试随 `pytest` 进入 CI，合计 103 项：
`tests/project_lifecycle_test.py` 守核心模型，`tests/project_task_navigation_test.py`
守信息架构与界面交互，`tests/project_memory_test.py` 守记忆边界、注入与可移植性。

### `project_lifecycle_test.py`：身份、系统工作区与生命周期

每一个项目都有不可变 UUID；「未分类」是真实项目而不是虚拟常量；归档可恢复、删除受保护。

| 测试 | 通过 |
| --- | --- |
| `test_system_project_has_a_real_persisted_uuid` | ✅ |
| `test_all_unassigned_tasks_resolve_to_the_system_project` | ✅ |
| `test_legacy_default_layout_is_migrated_without_data_loss` | ✅ |
| `test_system_project_cannot_be_renamed_archived_or_deleted` | ✅ |
| `test_system_project_owns_its_own_memory_space` | ✅ |
| `test_project_id_is_a_uuid_and_never_derived_from_the_name` | ✅ |
| `test_legacy_name_derived_slugs_still_load` | ✅ |
| `test_reserved_and_duplicate_names_are_rejected` | ✅ |
| `test_project_description_round_trips` | ✅ |
| `test_reading_projects_never_writes_to_disk` | ✅ |
| `test_require_project_reports_a_readable_error` | ✅ |
| `test_archive_is_recoverable_and_keeps_jobs_and_memory` | ✅ |
| `test_delete_refuses_while_the_project_still_has_jobs` | ✅ |
| `test_delete_requires_the_exact_name_then_backs_up_and_removes` | ✅ |
| `test_moving_jobs_out_then_deleting_is_supported_programmatically` | ✅ |
| `test_assign_by_name_and_by_id_hit_the_same_project` | ✅ |
| `test_assign_to_an_unknown_name_does_not_create_a_ghost_project` | ✅ |
| `test_project_for_job_always_returns_a_real_container` | ✅ |

### `project_task_navigation_test.py`：信息架构与交互模型

侧栏「项目」分组（selector 与它下面的管理入口）；`/projects` 与 `/projects/:projectId` 的四个 tab；卡片点击与 overflow menu；modal 校验；toast 反馈与状态同步。

| 测试 | 通过 |
| --- | --- |
| `test_task_without_a_project_belongs_to_the_system_workspace` | ✅ |
| `test_legacy_task_without_project_field_still_belongs_to_the_system_workspace` | ✅ |
| `test_legacy_default_project_id_never_raises_missing_project` | ✅ |
| `test_promoting_confirmed_terms_lands_in_the_task_container` | ✅ |
| `test_sidebar_splits_context_from_project_management` | ✅ |
| `test_sidebar_current_project_defaults_to_the_system_workspace` | ✅ |
| `test_sidebar_current_project_follows_the_open_task` | ✅ |
| `test_sidebar_switcher_changes_the_context_project` | ✅ |
| `test_project_nav_entry_returns_to_the_project_list` | ✅ |
| `test_project_page_shows_system_workspace_and_active_projects` | ✅ |
| `test_project_cards_are_clickable_and_have_an_overflow_menu` | ✅ |
| `test_system_workspace_card_has_no_lifecycle_menu` | ✅ |
| `test_project_search_filters_by_name_and_description` | ✅ |
| `test_archived_projects_leave_the_main_list_and_can_be_restored` | ✅ |
| `test_archived_projects_are_not_offered_to_new_tasks` | ✅ |
| `test_project_import_is_not_first_screen_content` | ✅ |
| `test_new_project_modal_creates_and_enters_the_project` | ✅ |
| `test_new_project_modal_validation_keeps_the_form_open` | ✅ |
| `test_inline_task_creator_uses_the_same_name_rules` | ✅ |
| `test_project_detail_has_four_primary_tabs` | ✅ |
| `test_overview_shows_basic_info_recent_jobs_and_memory_stats` | ✅ |
| `test_overview_empty_state_when_the_project_has_no_jobs` | ✅ |
| `test_tasks_tab_only_lists_tasks_of_this_project` | ✅ |
| `test_system_workspace_tasks_tab_lists_unclassified_tasks` | ✅ |
| `test_memory_tab_shows_confirmed_knowledge_only` | ✅ |
| `test_settings_tab_blocks_delete_until_jobs_are_gone` | ✅ |
| `test_settings_tab_system_workspace_explains_why_actions_are_absent` | ✅ |
| `test_rename_keeps_the_project_id_and_updates_the_list` | ✅ |
| `test_edit_updates_name_and_description` | ✅ |
| `test_archive_from_the_menu_keeps_jobs_and_memory` | ✅ |
| `test_delete_flow_requires_confirmation_and_a_job_free_project` | ✅ |
| `test_delete_flow_blocks_projects_with_jobs` | ✅ |
| `test_opening_the_app_from_a_deleted_project_link_falls_back` | ✅ |
| `test_new_task_picker_first_option_is_the_system_workspace` | ✅ |
| `test_new_task_can_select_an_existing_project` | ✅ |
| `test_new_task_without_a_project_lands_in_the_system_workspace` | ✅ |
| `test_opening_a_task_does_not_open_its_project` | ✅ |
| `test_opening_a_project_does_not_open_a_task` | ✅ |
| `test_task_and_project_do_not_share_vocabulary` | ✅ |
| `test_existing_tasks_and_projects_survive_the_new_ia` | ✅ |

### `project_memory_test.py`：记忆边界、注入与可移植性

只有已人工确认的内容能进入项目记忆；注入有时机与优先级；导出/导入是只增不改的资产迁移。

| 测试 | 通过 |
| --- | --- |
| `test_project_ids_are_uuid_and_never_derived_from_the_name` | ✅ |
| `test_creating_chinese_named_project_does_not_touch_default` | ✅ |
| `test_legacy_job_without_project_falls_back_to_default` | ✅ |
| `test_job_assignment_resolves_by_name_or_id` | ✅ |
| `test_only_confirmed_knowledge_enters_project_memory` | ✅ |
| `test_promotion_is_idempotent_and_versioned` | ✅ |
| `test_project_memory_is_injected_into_a_new_job` | ✅ |
| `test_task_owned_terms_win_over_project_memory` | ✅ |
| `test_started_job_is_not_retro_injected` | ✅ |
| `test_ui_projects_surface_lists_and_opens_a_project` | ✅ |
| `test_ui_promotion_moves_confirmed_terms_into_project_memory` | ✅ |
| `test_tm_paths_are_isolated_and_default_keeps_legacy_path` | ✅ |
| `test_project_memories_do_not_leak_between_projects` | ✅ |
| `test_same_source_can_differ_between_projects` | ✅ |
| `test_adopting_default_memory_is_explicit_and_merge_only` | ✅ |
| `test_tm_writeback_lands_in_the_job_project` | ✅ |
| `test_ui_library_shows_per_project_memory` | ✅ |
| `test_ui_project_can_adopt_system_memory` | ✅ |
| `test_batch_move_jobs_between_projects` | ✅ |
| `test_batch_move_does_not_move_terminology_or_overwrite_memory` | ✅ |
| `test_running_job_cannot_be_moved` | ✅ |
| `test_ui_can_move_jobs_into_a_project` | ✅ |
| `test_project_memory_round_trips_between_machines` | ✅ |
| `test_import_is_merge_only_and_idempotent` | ✅ |
| `test_import_rejects_malformed_or_tampered_payloads` | ✅ |
| `test_import_can_rename_to_avoid_touching_an_existing_project` | ✅ |
| `test_ui_project_exports_and_imports_memory` | ✅ |
| `test_tm_normalization_is_equivalence_not_similarity` | ✅ |
| `test_tm_lookup_prefers_exact_and_reports_normalized_hits` | ✅ |
| `test_pipeline_reuses_typography_variant_and_records_match_kind` | ✅ |
| `test_archive_hides_project_but_keeps_everything` | ✅ |
| `test_default_project_cannot_be_archived_or_deleted` | ✅ |
| `test_delete_requires_exact_name_confirmation` | ✅ |
| `test_delete_refuses_while_jobs_are_assigned` | ✅ |
| `test_delete_writes_a_recoverable_backup` | ✅ |
| `test_ui_project_archive_restore_and_guarded_delete` | ✅ |
| `test_new_task_selector_only_offers_active_projects` | ✅ |
| `test_import_records_addressable_conflicts_without_overwriting` | ✅ |
| `test_conflicts_survive_reload_and_do_not_duplicate` | ✅ |
| `test_adopting_incoming_replaces_glossary_and_translation_memory` | ✅ |
| `test_keeping_local_removes_conflict_without_changing_anything` | ✅ |
| `test_bulk_resolution_defaults_to_keeping_local` | ✅ |
| `test_resolving_an_unknown_conflict_is_rejected` | ✅ |
| `test_ui_offers_per_conflict_and_bulk_decisions` | ✅ |
| `test_reading_projects_never_writes_to_disk` | ✅ |

## 项目归档与删除（实现细节）

生命周期的产品语义见上文「生命周期」。这里补充实现层的保护：

- `set_archived(project, archived)` 拒绝系统工作区（`is_system_project`），
  并且只改 `status` / `archived_at`，不触碰 `glossary`、`style_rules`、
  `human_decisions` 或 TM 文件；
- `set_metadata` 拒绝系统工作区；正常项目改名只改 `name`，`project_id` 不变；
- `delete_project` 的四重保护（系统工作区、逐字名称、仍有任务时拒绝、删除前备份）
  在 `core.py` 里逐条实现并有对应测试；
- TM 只保留一份权威文件：迁移时**只在权威文件不存在时**搬
  `projects/<id>/translation_memory.json`，绝不合并两份——那会造成"同一实体两份
  TM 真值"。

## 翻译记忆的匹配范围

匹配**只吸收排版差异，不做语义近似**。`tm_normalize` 处理的是同一段文字在不同
来源下的写法差异：换行、制表与连续空格；不间断空格（U+00A0）与全角空格
（U+3000）；弯引号与直引号；en/em dash 与连字符；软连字符；省略号；NFKC 兼容
等价（如全角字母数字）。大小写**不**折叠——首字母大小写不同的段落是不同的段落。

匹配**必须限定目标语言**（见「翻译记忆按目标语言隔离」）：没有目标语言上下文时
`tm_lookup` 一律不命中，`tm_index` 返回空索引。也就是说，作用域不可证明时，
记忆的复用路径根本不存在——而不是"先匹配上再说"。

命中方式会被记录在段落上（`tm_match: "exact" | "normalized"`），因此"这条为什么
被复用"是可审计的。

**刻意不做语义模糊匹配**（编辑距离、词序、同义替换）。理由写在代码注释里，也值得
在这里重复一次：翻译记忆是错误放大器，一次错配会复制到整篇文档。项目里已经有一处
同类教训——`load_tm` 加载即消毒，就是为了防止被污染的记忆继续命中。语义近似需要
独立的置信度设计（谁判定、谁复核、误用后如何撤回），在那之前宁可不做。

## 已知缺口

1. **没有定时/自动的项目记忆同步。** 导出/导入是人工动作；多机协作目前靠人工
   传递 JSON。
2. **冲突只能看到，不能一键采纳。** 导入报告会列出术语与译文冲突，但采纳导入方的
   版本仍需要人工在术语页修改。
3. **删除的备份不会自动清理。** `outputs/projects/_deleted/` 只增不减，需要用户
   自己管理（也可以把整个目录当成归档区）。
4. **`scripts/` 与 `eval/` 里的既有工具仍作用于系统工作区的记忆。** 这对遗留脚本
   是合理的默认行为（它们历史上操作的就是全局记忆文件）；如需指定项目，它们需要
   新增参数。
5. **归档项目不出现在新建任务的 picker 里，但已归档项目下的任务仍可打开。** 这是
   有意的：归档表达的是"这个容器暂时不再新增任务"，不是"冻结里面的工作"。
