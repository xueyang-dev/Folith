# Folith / 译页：产品与架构边界

2026-09-14 更新。下文 Phase 1 / v0.4 / v0.5 条目是历史边界记录，不是当前待办顺序。当前本地进度与后续优先级以[开发蓝图](foliothread-agentic-native-blueprint.md#7-当前进度与开发路线)为准；近期先完成 UI 打磨并正式发布，再推进 CAT 内核。

## 当前新增约束：CAT 内核

- DOCX 原格式输出采用 [DOCX Contract v1](docx-contract-v1.md)：保留原 package，仅修改声明的可翻译范围；不得用重建文档冒充 surgical round-trip。该能力尚未实现。
- 版本内 Instance ID、source XML anchor、跨版本 correspondence 各自独立；不能把段落索引或 UUID 当成 Source Update 算法。
- 翻译单元可以跨 run，编辑/TM 粒度不限制 LLM 上下文粒度；格式和 inline 标记有结构语义。
- 保留但未翻译的内容必须进入用户可见报告；无法安全处理时明确拒绝，结构损坏不能由语义风险接受放行。
- 继续复用唯一译文真值、finding、human decision、恢复与交付快照，不另建状态库或通用 adapter 框架。
- TM 检索候选与译文应用分开；历史批准不自动延伸至新上下文。PDF 保留现有工作流，不成为其他文档格式的内部标准。

以下保留原阶段决策，便于解释现有实现；其中“Phase 1 不做”不禁止当前已批准的后续 CAT 工作。

## 1. 产品定位

Folith·译页的品牌定位是 **Agentic Translation Workspace / 智能体翻译工作台**。用户首先看到并使用的是一条可恢复的本地化主路径：

```text
文档解析 → 文档上下文 → 术语与翻译记忆 → 翻译 → 人工审校 → 交付
```

MTI、论文和翻译实践报告不再作为产品的默认解释，而是“研究与报告”专用能力：

- 只在用户选择研究资产或报告输出时进入；
- 继续复用翻译主路径产生的证据，不另造一份译文真值；
- 保留现有 MTI 合规、案例 provenance、文献证据和报告 QA 能力；
- 不占据普通本地化任务的首屏，也不限制 Folith 的使用对象。

## 2. v0.4 基础设施：继续保留

这些能力服务于所有长文档翻译任务，Phase 1 不重写、不迁移：

| 基础设施 | 当前实现 | Phase 1 决策 |
| --- | --- | --- |
| 文档输入与结构恢复 | `pdf_ingestion.py`、`document_profile.py` | 保留，作为长文档入口 |
| 可恢复任务与运行状态 | `core.py`、`checkpoint.py`、`state_migration.py` | 保留，继续支持中断恢复 |
| 上下文与语义单元 | `context.py`、`entity_registry.py`、`knowledge.py` | 保留，作为长文档连续性的基础 |
| 术语治理与翻译记忆 | `terminology.py`、`assets.py`、`checkpoint.py` | 保留，术语与 TM 仍是主路径资产 |
| 翻译、审校与证据 | `translation_protocol.py`、`translation_evidence.py`、`repair.py` | 保留，不改变真值和审校语义 |
| 交付、快照与页面 QA | `delivery.py`、`snapshots.py`、`rendered_qa.py`、`finalization.py` | 保留，作为可追溯交付基础 |
| Provider 与模型配置 | `core.py`、`model_roles.py`、`gui.py` | 保留，Phase 1 不改变调用方式 |

公共品牌已经改为 Folith / 译页，但 v0.4 的 Python import namespace `folith`、既有状态字段、内部 artifact ID、`folith:` 导出字段和 `FOLITH_*` 环境变量继续保留。这些是技术兼容边界，不是产品定位。

## 3. MTI / 论文能力：legacy / specialized

以下能力继续存在，但属于可选、专用的研究下游，而非 Folith 的默认产品中心：

| 专用能力 | 组成 | 进入条件 |
| --- | --- | --- |
| 研究模型与学术写作 | `academic_writer.py`、`academic_evidence.py`、`academic_validator.py`、`academic_quality.py` | 开启研究报告 |
| 案例与人工证据 | `case_analysis.py`、`case_presentation.py`、`case_provenance.py`、`human_evidence.py` | 需要案例分析或人工补充 |
| 文献与报告结构 | `literature_evidence.py`、`report_evidence.py`、`report_template.py` | 选择研究资料或报告模板 |
| MTI 规则与终稿 QA | `thesis_constraints.py`、`compliance.py`、`final_docx.py`、`rendered_qa.py` | 使用 MTI/报告型交付 |
| 专用 UI | “研究与报告（专用能力）”、案例、合规与报告工作区 | 用户主动进入专用工作流 |

`MTI_PRACTICE_REPORT_DEFAULT`、`translation_practice_report` 等内部 profile/schema 名称暂不改动。它们描述的是现有专用能力的技术契约，不应被误读为 Folith 的全局产品定位。

## 4. v0.5 演化方向

v0.5 在现有边界上演化，不在 Phase 1 提前实现：

| 方向 | 目标 | 复用 v0.4 的什么 | Phase 1 不做什么 |
| --- | --- | --- | --- |
| **Project Memory** | 保存跨文档、跨任务可复用的术语、实体、风格和人工决策 | 术语、TM、entity registry、任务状态 | 不把 TM 直接改造成通用记忆库 |
| **Agentic Context** | 为每个工作步骤提供有边界、可追溯的上下文包与证据 | `context.py`、evidence、document profile | 不加入自主循环或无界检索 |
| **Human Decision** | 将审校、批准、拒绝、覆盖和风险接受变成明确决策记录 | review findings、case review、finalization | 不让模型批准自己的输出 |
| **Delivery** | 将资产、验证事实、版本和交接统一为发布包 | `delivery.py`、snapshots、manifest、QA | 不新建第二套交付流水线 |

其中 Delivery 是 v0.4 已有能力的演化方向，不意味着 Phase 1 要重写交付；Project Memory、Agentic Context 和 Human Decision 也不要求现在修改现有状态 schema。

## 5. 命名与迁移规则

- 用户可见产品名、页面标题、启动器、README、发布说明和资源名使用 `Folith` / `译页`。
- 0.5.0 已将内部包从旧品牌迁移到 `folith/`，代码和资源引用同步迁移。
- 发行包名仍为 `foliothread`；0.5.0 主命令为 `folith`，`foliothread` 命令为兼容别名。旧品牌命令在 0.5.0 移除；明确保留的旧环境变量见品牌文档。
- 现有 `FOLITH_API_KEY`、`FOLITH_EVAL_API_KEY` 等环境变量保留，避免把品牌迁移误变成运行时迁移。
- 旧的论文全文、渲染页面、临时截图和旧 logo 不属于产品资产；它们不应进入仓库。`.codex_tmp/` 已加入忽略规则。

## 6. 读写边界：读取不得有写副作用

本地 `state.json` 既是任务真值又是运行期缓存，因此很容易写出"读一下顺手补个默认值"
的代码。**本项目把这条定为不变量：以 `load_` / `get_` / `list_` / `build_` /
`*_view` / `*_summary` 命名的函数不得创建或修改任何文件。**

违反它会造成两个真实后果：

- 跑一次测试、打开一个页面、执行一次 eval，都会在用户工作目录里留下与本次操作
  无关的文件；
- 只读消费者（脚本、报告、审计）会悄然改变被观察对象的状态，让"看到的结果"与
  "上次看到的结果"不再可比。

已修复的三处（都曾有实际副作用）：

| 位置 | 曾经的副作用 | 现在 |
| --- | --- | --- |
| `core.list_projects()` | 顺手创建默认项目，于是**渲染新建任务页**就会写 `outputs/projects/default/project.json` | 纯读取；默认项目由 `ensure_default_project()` 在变更路径创建，展示用内存视图 `default_project_view()` |
| `core.get_job_runtime_status()` | 推断状态时调用 `update_runtime_state`，于是**从未运行过**的任务被读过就多出 `runtime_state.json` | 推断只影响返回值，不落盘；真实状态由 worker 写入 |
| `core.list_jobs()` | 先 `_ensure_output_dir()`，于是"列出任务"会创建 `outputs/` | 目录不存在时返回空列表 |
| `core.project_for_job()` | 默认项目不存在时调用 `ensure_default_project()` 并落盘 | 纯读取，返回内存视图 |

**唯一保留的写路径（有意为之）**：当持久化状态仍是 `running` / `queued` 等活跃状态、
而对应进程已不存在时，`get_job_runtime_status()` 会把状态纠正为 `interrupted` /
`stalled` 并落盘。它只发生在**真正启动过 worker** 的任务上（从未运行的任务状态是
`idle`，走纯计算分支），目的是让运行状态、lease 与取消逻辑在各个界面看到一致结果，
而不是"看一眼就产生文件"。

不在此不变量范围内：`build_delivery_assets()` 这类**生成器**——它的职责就是产出
可下载的交付文件，写盘是它的功能而非副作用。

回归防线：

- `tests/runtime_state_test.py::test_reading_a_never_run_job_writes_nothing`
- `tests/project_memory_test.py::test_reading_projects_never_writes_to_disk`

两项都经过反向验证：把对应副作用改回去，测试会失败。

## 7. Phase 1 明确不做（历史范围）

- 不改翻译、审校、报告生成、快照、交付门禁或状态恢复的核心行为；
- 不改现有 MTI profile 的判断规则和 artifact schema；
- 不加入 agent loop、自动决策、Project Memory 数据库或新的检索层；
- 不通过 feature flag、迁移框架或兼容 wrapper 预留尚未发生的运行时分支。

## 8. 判定边界：什么算正文、什么算同一个任务、什么算同一条记忆

这一节记的是**判定类**边界。它们的共同风险不是崩溃，而是**静默地做出一个错误的
判断然后一路通过**：没有报错、门禁通过、交付成功，结果是错的。因此每一条都要求
"证明不了就不要做"，而不是"先猜一个再说"。

| 判定 | 曾经的判法 | 现在的判法 | 证明不了时 |
| --- | --- | --- | --- |
| 这一行是不是正文 | `[A-Za-z0-9\u4e00-\u9fff]` 枚举语言区间 | Unicode 类别：字母（`L*`）或数字（`N*`）即正文 | 纯标点/符号按装饰行原样保留（不调模型） |
| 这条记忆属于哪种目标语言 | 不判——键就是原文 | 键是「目标语言 + 原文」的作用域键 | 语言未知 → 不命中、不入库 |
| 这是不是同一个任务 | 文件内容哈希 | 文档身份 + 本地化上下文（项目 / 目标语言 / 源语言） | 旧任务上下文不可证明一致 → 新建独立任务 |
| 这个服务监听哪里 | 框架默认（`0.0.0.0`） | 显式 `127.0.0.1`；LAN 只在 `--lan` 时显式选择 | —— |

四条各自的理由：

1. **正文判定不能用语言区间枚举。** 按脚本列区间永远会漏掉下一种语言，而漏掉的
   后果不是报错，是**原样保留 + 标成已审校 + 通过交付检查**（译文就是原文）。
   "这一行是不是文字"本身就是 Unicode 类别问题，不是语言清单问题。核心层与
   检查点恢复共用同一个判定（`folith.textual.has_textual_content`），
   不允许各存一份正则。
2. **翻译记忆的身份是「目标语言 + 原文」。** 只用原文当键，一种语言的已审校译文
   会串进另一种语言的任务。没有目标语言上下文就不建立任何可命中路径（fail
   closed）；旧条目保留在文件里但不自动命中，也不被猜成某种语言。
3. **文档身份不等于任务身份。** 内容哈希可以回答"这是不是同一份文件"，不能回答
   "这是不是同一个活"——项目决定注入哪套记忆与术语，目标语言决定译文本身。
   内容哈希保留为去重手段，两者在概念与实现上都分开。
4. **网络绑定不依赖框架默认值。** 默认值可能随框架版本变化，而这里默认值一旦
   变宽就是把本地文档工作台暴露到局域网。默认必须显式写出。

回归防线：

- `tests/text_content_detection_test.py`（正文判定 + 记忆资格 + 检查点共用同一判定）
- `tests/project_memory_test.py`（记忆作用域与 fail-closed）
- `tests/task_identity_test.py`（任务身份、旧任务认领、状态不串）
- `tests/project_task_navigation_test.py::test_the_same_file_in_another_context_creates_a_new_task`
  （走完整界面路径，而不是只调核心函数）
- `tests/gui_launcher_test.py`（用真实 socket 探测默认回环 / `--lan` 可达）

全部经过反向验证：把旧判法放回去，对应用例会失败（见 `CHANGELOG.md` 同一批条目）。
