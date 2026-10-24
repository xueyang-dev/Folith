# Folith v0.7.2（Milestone 5）开发基线记录

日期：2026-10-24。对应方案：Milestone 5 开发指导方案（`milestone-5-development-guide-2026-10-24.md`，随里程碑计划单独维护，未纳入本仓库；下称"方案"）。
本文是方案 §7 要求的"第一份交付"：选定 commit、四条写入调用链、现有 UID/版本/锁/恢复能力、#93 操作矩阵、#49/#83 的可用材料与缺项，以及最小失败夹具的落点。

状态标记：**【已实现】**＝本轮代码已落地并有测试；**【待做】**＝尚未开始；**【缺材料】**＝本机无法完成，需要外部输入。

## 0. 结论速览

- 选定基线：`upstream/` @ `2603643`（分支 `codex/stability-data-integrity`，项目版本 0.7.1）。未跟踪的 `docs/android-development-plan.md` 与 `promo/` 保留不动。
- 四条写入链在基线上**全部直写正文**，没有任何一条有"候选"概念；`#29` 的缺口不是一处，而是一类。
- `#29` 已按方案 §3.3 收敛为"生成 → 持久化候选 → 采用（唯一事务）"，`#94` 的基础保护（稳定 UID、基线/版本校验、锁、幂等、原子保存）随第一次可用提交一起进入。**【已实现】**
- `#93` 的"确认问题存在 / 确认已解决 / 保留当前译文"已分成三个不同动作（不再共用一个含混按钮）。**【已实现】**
- `#92` 的审校建议与批量接入、`#94` 的跨入口与故障注入覆盖仍为**【待做】**。
- `#49` 缺真实 provider 连接与原 301–400 页 PDF；`#83` 缺原生 Windows/macOS 复现环境。两者目前只能给出**代码级路径结论**，见 §6。

## 1. 选定提交与环境

| 项 | 实测值 |
| --- | --- |
| 工作仓库 | `D:/03_Development/Folith/upstream` |
| commit | `2603643`（`fix(qt): estimate PDF size without rebuilding document layout`） |
| 分支 | `codex/stability-data-integrity` |
| 项目版本 | `pyproject.toml` → `0.7.1`（`name = "foliothread"`） |
| 运行环境 | `upstream/.venv/Scripts/python.exe`，Python 3.13.15，pytest 8.4.2（无 pytest-timeout；单测需自行控制用例粒度） |
| 必须保留的未跟踪内容 | `docs/android-development-plan.md`、`promo/` |
| 方案参照的远端 | `626e68b2900c103afa5ef779d3fbaebb26f8538c`；本地基线与该提交在下列四条链上的结构一致（本地多出 PDF 尺寸相关提交） |

`core.py` 是门面：拆分出去的 `folith/engine/*` 通过 `folith/engine/_link.py`（`_c`）**晚绑定**回 `core`，所以 monkeypatch `core.X` 对拆分模块仍然生效——候选事务也遵守这一点。

## 2. 四条写入调用链（基线 `2603643`）

| # | 入口 | UI → 窗口 | adapter | 核心写函数 | 持久化点 | 基线行为 | 本轮状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 定点重译 | `views.py:2822` `retranslate_button` → `window.py:352` `retranslate_segment` | `adapter.retranslate_segment:1087` | `core.retranslate_segments` → `delivery._retranslate_segments_locked` | `core.save_job_state` | 一次模型调用直接写 `target`、重写 `initial_target`、清人工接受标记、关闭旧 findings | **【已实现】**改为生成候选；采用才写正文 |
| 2 | 按要求重译 | 工作台共用输入 `views._submit` → `window.suggest_translation:3061` | `adapter.suggest_translation:1449` | `core.call_llm` 返回**字符串**，塞进编辑框；用户点保存才 `adapter.submit_edit:902` → `core.save_translation_edit:9213` | `core.save_job_state` | 建议没有身份、基线、来源；"采纳"与"手打一遍"在状态里不可区分 | **【已实现】**改为落候选并复用同一采用事务 |
| 3 | 采用审校建议 | `review_panel.applySuggestionRequested` → `window.apply_suggestion:2770` | `adapter.apply_suggested_translation:1722` | 顺序调用三个公开写入口：`core.decide_translation_review_finding` → `adapter.submit_edit` → `adapter.resolve_findings:1698` | 三次独立保存 | 一个逻辑动作拼接三个事务，中途失败会留下"决定已记、正文未改"或反向状态 | **【待做】**#92 |
| 4 | 批量修订（查找替换） | `window.py:2244` 后台任务 | `adapter.replace_in_targets:1033` | 逐段 `submit_edit` → `core.save_translation_edit` | 每段一次保存 | N 段 N 个事务；部分成功无整组语义，界面只报"改了几段" | **【待做】**#92 |

补充：`#94` 要求的"旧式旁路"在基线里还有一条——`apply_suggested_translation` 在"不要求重审校"的分支里直接 `resolve_findings`，即**写正文后自动替用户把问题标成已解决**。这条与 #93 的"采用 ≠ 质量通过"直接冲突，须在 #92/#93 里一并改掉。

## 3. 现有能力清单（只复用，不新建第二套）

| 能力 | 实现位置 | 语义要点 | 候选流程如何使用 |
| --- | --- | --- | --- |
| 段身份 | `engine/segments.py:129` `segment_uid`、`:145` `next_segment_uid`、`:152` `assign_missing_segment_uids` | `segment_uid` 只为界面/候选身份服务，数据契约仍按 index | 候选按 uid 绑定；index 只在源文匹配时作为回退 |
| 结构变更保身份 | `segments.py:587` `mutate_translation_segments`、`:972` `undo_translation_segment_structure` | 拆分/合并/插入/删除后 uid 跟随，后续 index 含义改变 | 采用时按 uid 解析落点，避免误写旧行号 |
| 文档级版本 | `engine/delivery_state.py:64` `_mark_translation_truth_changed` | 递增 `translation_truth.version`，记录 reason/actor/action | 采用成功只递增**一次**，作为"文本真值变了"的唯一凭据 |
| 审校证据失效 | `delivery_state.py:150` `_invalidate_translation_reviews` | 只撤销**存在 runtime review event** 的段的证据，不动其他段 | 采用后按被改段调用，历史快照保留 |
| 交付门禁重算 | `segments.py:546` `_recheck_delivery_invariants_for_segments` | 只重算受影响段的交付不变量与 final/approval 依赖 | 采用后调用，避免整书重扫 |
| 状态互斥 | `engine/locks.py:76` `job_state_mutation_lock`、`:108` `_serialized_job_mutation`、`:31` `_exclusive_job_execution` | 状态写入串行化；worker 任期内拒绝外部保存 | 采用在锁内重读权威状态；模型调用**不**持锁 |
| 原子保存 | `core.py:9139` `save_job_state`；`core.py:9098` `_check_snapshot_revision`；`storage/job_repository.py:352` `save_job_state_atomic` | 快照版本必须等于磁盘版本，否则拒绝；临时文件 + 原子替换；随后 `journal.rotate_if_applied` | 候选写入与正文写入在同一次快照里完成 |
| 恢复 | `storage/state_journal.py`；`_APPLIERS:515` 只有 `translation_batch_committed` 一种 mutation | 快照承载全量状态，journal 只记录翻译批次的增量；重放按 `journal_cursor` 判定 | **已核实**：候选不需要新的 mutation 类型——候选变更随快照落盘，不靠增量记录；#94 需用 journal 开/关重放测试锁定 |
| 撤销 | `segments.py:1061` `restore_translation_edit` | 撤销人工修改：恢复旧 target 并重新标记待审校，不恢复 reviewed/final | 采用后的撤销走同一条路径，保留采用记录（#94 待验） |
| 草稿 | `translation_drafts.json`（`segments.py:46` 起） | 草稿不进 state.json、不改审校/交付 | 采用前若编辑器有未提交内容（含已落盘草稿）必须拒绝 |

> 基线**没有**段级版本号。方案 §3.2 的"优先复用全局版本"在此适用：本轮用 `translation_truth.version` + 候选记录里的 `baseline.target`/`baseline.source` 双校验，不引入段级版本框架。

## 4. 候选契约落点（#29 已实现部分）

| 关注点 | 落点 | 契约 |
| --- | --- | --- |
| 记录模型 | `folith/translation_candidates.py`（纯逻辑，不 import `core`） | `translation_candidates` / `translation_candidate_seq`；状态 `pending/adopted/discarded/stale/failed`；来源 `targeted_retranslate`、`instructed_retranslate`、`review_suggestion`、`batch_revision` |
| 基线 | 同上 `BASELINE_FIELDS`、`baseline_conflict` | 逐项比对 source → target → reviewed/review_status/provenance → from_tm/excluded → 人工接受；顺序有含义：先报"源文变了"，再报"译文变了" |
| 生成入口 | `folith/engine/candidates.py:commit_candidates` | 生成侧只写候选；写入前捕获基线，基线已变则直接标 `stale` 并给出冲突码 |
| 采用入口 | `folith/engine/candidates.py:adopt_translation_candidate` | 唯一入口：锁内重读 → uid 匹配 → 基线校验 → 源文质量门禁 → 幂等（已采用直接返回原结果）→ 一次 truth 递增 → 失效传播 → 人工动作 `candidate_adopted`（key 为 `candidate:<id>`） |
| 丢弃入口 | `discard_translation_candidate` | 只改候选状态，**不动** finding，不解决任何问题 |
| 重译改造 | `folith/delivery.py`：`_retranslation_request`（调用前抓 uid/source/baseline/truth_version/上下文）、`_generate_candidate_text`（一次生成 + 一次修复）、`retranslate_segments` | 模型调用在锁外；生成阶段不写 `target`、不重写 `initial_target`、不关 findings、不再自动触发重审 |
| UI | `qt/adapter.py`（`retranslate_segment`、`segment_candidates`、`adopt_candidate`、`discard_candidate`）、`qt/segment_assist.CandidateBox`、`qt/views`、`qt/window` | 生成后只提示"已生成候选"；采用前检查编辑器未提交内容；采用成功才重载编辑器 |
| 测试 | `tests/translation_candidate_contract_test.py`（12 例） | 生成不改稿、失败不改稿、丢弃不解决 finding、采用幂等/可追溯、译文或源文过期、同文采用不增版本、结构变更后按 uid 跟随、未知 uid、源文门禁、重启后仍在、旧任务可打开 |

## 5. #93 操作矩阵（基线现状 → 目标）

| 用户动作 | 正文变化 | 决策含义 | 审校/历史表现 | 基线现状 |
| --- | --- | --- | --- | --- |
| 保留当前译文 | 无 | 记 `preserved`（映射 `dismiss`） | 显示保留依据，不制造"已修改" | 已有，语义基本正确 |
| 确认问题确实存在 | 无 | 问题被确认、仍待修订 | 保持待处理，不自动 resolved | **【已实现】**新增独立动作 `confirmed_open`：只写一条 `finding_confirmation` 人工记录，finding 保持 open、仍挡交付；Translation Core 发现项映射到既有 `request_revision` |
| 确认问题已解决 | 无 | `human_fixed`（映射 `accept_resolution`） | 仅证据新鲜且门禁允许时解决 | 已有；现与"确认存在"分成两个按钮，文案不再含混 |
| 采用建议 | 有，或同文无变化 | 明确接受某候选，变更与决定关联 | 按规则重新审校，不宣称质量通过 | 基线是"三次写调用 + 自动标已解决"；候选版本已改为同一采用事务，但"审校建议 → 候选"的接入属 #92 |
| 手工编辑 | 仅实际差异 | 记录人工作业（`human_edited`） | 旧证据失效，不自动接受 AI finding | 已有（`core.save_translation_edit`） |
| 取消/失败 | 无已提交变化 | 不产生成功决定 | 不显示已完成/已解决 | 基线部分入口缺显式反馈（生成失败时无候选、无记录） |

本轮已明确的边界：**采用候选 ≠ 审校通过**（采用后 `reviewed=False` / `review_status=not_reviewed`）、**丢弃候选 ≠ 处置 finding**（finding 原样保留，交付状态仍可能是 `review_required`）。

## 6. #49 / #83 的可用材料与缺项

### #49 扫描 PDF 文档画像

- 可用：源码路径 `folith/document_profile.py:profile_document`、`folith/models.py` 的画像规范/校验、`profile_warnings`、`tests/document_profile_test.py`（mock 覆盖非法 JSON、缺字段、空样本、取消、网络失败、重试耗尽）。
- 可用：`docs/task-loading-profile-followup.md` 与 PR #51 的既有结论（三次调用上限、纠正请求、警告持久化）。
- **缺材料**：真实 provider 连接与额度；原案例 301–400 页 PDF（本机无该材料）；原任务的阶段日志与警告原文。
- 因此：本轮**不**修改画像实现，也不把 #49 标为关闭。等价案例需要满足"同文档类型、相近扫描比例、相近抽样长度、同语言、同连接与模型"，缺一即不能替代。

### #83 筛选后选段 → PDF 原页定位

- 可用：`qt/adapter.py:segment_pdf_location` 的代码路径已确认——**持久化位置存在但不在当前页时仍回退文本搜索**（方案 §1 已定点核对，本地一致）；`folith/pdf_preview.py` 负责锚点/渲染；`qt/pdf_panel.py` 负责 `locate`/`goto` 与延迟渲染。
- 可用：`tests/pdf_preview_test.py`、`tests/qt_pdf_panel_test.py`（offscreen 层）。
- **缺材料**：原 49 页任务文件；原生 Windows/macOS 客户端的人工复现（offscreen 测试不能替代页面/控件验收）。
- 因此：本轮只能做"分层记录点"与代码级判定，不宣称修复；原生证据缺一项即不勾选验收。

## 7. 最小失败夹具与当前结果

| 夹具 | 位置 | 断言的核心事实 |
| --- | --- | --- |
| 候选契约（12 例） | `tests/translation_candidate_contract_test.py` | 生成/失败/丢弃不改稿；采用幂等且只递增一次版本；过期拒绝；结构变更按 uid 跟随；重启后仍在；旧任务可用 |
| 重译与术语治理 | `tests/terminology_governance_test.py` | 重译产出候选而不写正文；候选可记录 postcheck 结果而不触碰状态 |
| 交付回滚 | `tests/delivery_snapshot_test.py` | 采用导致的失效只影响被改段 |
| 人工审校 | `tests/manual_review_execution_test.py`、`tests/phase3_review_runtime_compat_test.py` | 未采用的候选不触发审校 |
| 中断恢复 | `tests/workflow_recovery_test.py` | 致命中断发生在生成阶段时，正文保持原值 |
| Qt 右栏 | `tests/qt_segment_assist_test.py` | 建议生成只进候选卡片，不进编辑器 |
| Qt 工作台 | `tests/qt_client_window_test.py` | 生成后编辑器/草稿原样；采用才刷新；有未提交草稿时拒绝采用 |

测量（本机，Python 3.13.15，2026-10-24）：

| 命令范围 | 结果 |
| --- | --- |
| `translation_candidate_contract_test` + `red_team_acceptance_test` + `terminology_governance_test` | 61 passed（含候选契约 12 例） |
| `qt_review_panel_test.py` | 31 passed（含"确认问题存在"与"确认已解决"分离两例） |
| `qt_segment_assist_test.py` + `qt_inline_editor_test.py` | 21 passed |
| `qt_client_window_test.py`（整文件） | 108 passed，6m01s |
| `qt_client_adapter_test.py` + `qt_review_panel_test.py`（改动前基线） | 88 passed |

用例粒度提示：本机未装 `pytest-timeout`；`qt_client_window_test.py` 整文件约 6 分钟，排查单例时用 `-k` 更省时间。整文件跑完时，pytest 的临时目录清理会被本机批量删除守卫拦下并打印 `SystemExit`——那是环境守卫，不是用例失败，摘要行仍为 `108 passed`。

## 8. 未完成清单（按方案顺序）

1. `#93`：动作矩阵中的"确认问题存在 / 确认已解决 / 保留"三值已落地；剩余部分是"采用建议"与"手工编辑"的对外文案与历史投影一致性复核（采用路径已由候选事务承载，见 §4）。
2. `#92`：审校建议先落候选再展示 diff；批量入口统一 UID/版本/来源契约，并写明批量冲突策略（整组不提交 vs 逐项返回）与对应故障测试。
3. `#94`：过期拒绝、失效范围、幂等/并发、撤销、中断注入、旧任务、journal 开/关 的完整覆盖；本文 §3 的"复用结论"需要用测试固定下来。
4. `#49`、`#83`：等待真实材料与原生平台，在此之前保持待验证，不以 mock 或 offscreen 结果关闭。
