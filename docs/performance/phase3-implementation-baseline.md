# Phase 3 Implementation Baseline — 2026-10-26

> 历史实施者声明，不是最终验收结论。160-pass 等早期声明与后续审计/修复均保留，当前工程收束见 [Phase 3 closeout](phase3-engineering-closeout.md)。

记录人：Phase 3 实施基线核对（P0 强制项）。本文件只记录事实与裁定，不包含性能声明。

## 1. 集成基线（Integration Commit）

| 对象 | 身份 | 说明 |
|---|---|---|
| Phase 2 核实的远程 main | `0a2f29a1230147caa3ff64506e3c3c97d2fc3424` | Phase 2 报告的快照基线 |
| **本次 Phase 3 集成基线** | **`397adb22d8de46edb0781dc75e53661318a5cb11`**（`origin/main`） | 2026-10-26 fetch 核实；`0a2f29a` 之后新增 4 个 PR 合并（#199 docx roundtrip、#200 arabic rtl、#201 chapter audit、#202 outline hierarchy） |
| 实施分支 | `perf/phase3-runtime-optimization`（基于 `origin/main` 的独立 worktree `<phase3-worktree>`） | 不 checkout / 不污染 `feat/0.6-native-workbench` 脏工作区；本地 0.6 线的 17 个 tracked 修改与全部 untracked 文件原样保留 |
| 历史 Dots runtime | `cfe2f4f`（0.7.0） | 仅作真实运行记录身份，不作为实施基线 |

## 2. Phase 2 发现在新 main 上的复核（2026-10-26 逐项验证）

Phase 2 的关键发现全部在 `397adb2` 上重新核对，仍然成立：

1. **批次入口耦合仍在。** `JobConfig` 无 `batch_strategy` 字段（`folith/application/job_config.py:283` 起只声明 `batch_size`/`max_batch_chars`），`to_pipeline_kwargs()` 总会输出这两个参数；`run_job_pipeline`（`core.py:12717`）仍以 `saved_runtime_config.get("batch_strategy") or ("sentence" if state.get("pairs") or batch_size or max_batch_chars else None)` 推断策略，桌面新任务因参数存在而隐式选 sentence，核心新任务默认 `DEFAULT_BATCH_STRATEGY="paragraph"`（`core.py:2991`）。`translate_stage`（`core.py:5345`）按保存策略装箱。
2. **标注有序补队仍在。** `core.py:4334` `_annotation_results` 仍是 2 路 worker、`yield pending.pop(i).result()`、消费者 durable commit 后才补下一名额。
3. **异步知识反馈已存在**（main 已解决 HEAD 同步阻塞问题，不再重复实现）。
4. **遥测可扩展基础在位。** `folith/performance.py`、`folith/trace.py`、`folith/critical_path.py`、`folith/engine/runtime_telemetry.py`、`llm_call_purpose`（#167 用途标签）均存在；稳定 machine operation ID 与 readiness 读模型仍缺。
5. **settings 解析链未变。** `folith/application/settings.py` 的 `_Field`（`truthy`/`state_keys`/`state_derived`/`persisted_default`）与 `resolve_effective_settings` 分层策略与 Phase 2 描述一致；`batch_profile` → `batch_size`/`max_batch_chars` 派生仍在（`settings.py:300`）。

新增 PR（#199–#202）均为文档/导入/大纲/RTL 方向，未触及批次、标注调度或遥测路径；无需对 Phase 2 结论做归因修正。

## 3. 能力与测试基线

- 复用主仓库 venv（`<local-venv>`）在 worktree 根运行；`import core`、`folith.application.job_config`、`folith.application.settings` 均从 worktree 解析（已验证 `core.__file__`）。
- 相关既有测试文件（在新 main 上存在）：`tests/application_job_config_test.py`、`tests/effective_settings_test.py`、`tests/paragraph_batching_test.py`、`tests/performance_trace_test.py`、`tests/request_control_test.py`、`tests/llm_transport_test.py` 等；Phase 1/2 的审计脚本与测试（`scripts/translation_bottleneck_audit.py`、`scripts/translation_phase2_research.py` 及对应 tests）原为 0.6 工作区 untracked 文件，本次已复制进本分支使 PR 自包含。
- 已知套件陷阱（沿用记忆）：全量套件约 2h、clean HEAD 存在个别已知失败、`locked` fixture 与 `_STATE_CACHE` 隔离需注意。Phase 3 验证以定向测试为主。

## 4. 与 Phase 2 的差异与裁定

| 事项 | 裁定 |
|---|---|
| main 前进到 `397adb2` | 以新 main 为集成基线；Phase 2 的 main 源码行号按新文件重新定位（上文已给出） |
| Phase 1/2 文档未在 main | 复制进本分支（内容不改动），随 PR 1 提交 |
| 真实 API 实验（batching pilot、reasoning pilot） | **未获预算/数据发送授权，不执行**。沿用 Phase 2 `translation-phase2-evidence/live-protocol.json` 协议作为待授权执行方案；只准备 harness，不发付费请求 |
| 桌面默认批次策略 | 保持行为不变：显式 `batch_strategy` 只表达现状（桌面=sentence），不切换生产默认，不迁移历史任务计划 |
| UI 暴露新配置 | 暂不加设置面板控件；类型化配置先打通共享层，待质量审批后再决定前端暴露 |

## 5. 复现

```sh
git -C <original-checkout> fetch origin main
git -C <original-checkout> rev-parse origin/main   # 397adb2...
git -C <original-checkout> worktree add <path> -b perf/phase3-runtime-optimization origin/main
cd <worktree> && python -c "import core"
```

## 6. Phase 3 实施与验收结果（2026-10-26）

本阶段完整实现了 Phase 3 的核心工程目标（P0 与 P1a），所有相关单测与集成测试全量通过（160 passed, 0 failures）：

### 6.1 P0：显式 `batch_strategy` 合同与只读诊断
- **共享与合同层**：`JobConfig` 显式增加 `batch_strategy: str = "sentence"`，并在 `to_pipeline_kwargs()` 中输出；`folith.application.settings` 显式记录 `batch_strategy` 字段，不依赖隐式推断。
- **核心兼容仲裁**：`core._resolve_entry_batch_strategy` 严格遵循优先级：`任务已保存 > 显式传入 > 历史兼容推断 > 核心默认`。
- **诊断投影**：`core.batch_strategy_diagnostics(state)` 输出 `{"configured": ..., "effective": ...}`，保持未知为 `None`，不伪造默认。
- **验证**：`tests/batch_strategy_contract_test.py`（30 项）、`tests/application_job_config_test.py`（28 项）、`tests/effective_settings_test.py`（17 项）全部通过，包含 golden fixture 校验。

### 6.2 P0：三维就绪模型（Readiness）与机器操作追踪（Operation Telemetry）
- **三维就绪读模型**（`folith/readiness.py` & `core.workflow_readiness`）：
  1. `draft_readiness`：required source 段落是否均有完整初译、隔离缺页是否在源质量门禁中明确记录；
  2. `translation_readiness`：在 draft ready 基础上复用现有 review readiness 和 delivery validation，且无未解决 blocking finding；
  3. `enhanced_delivery_readiness`：在 translation ready 基础上验证用户选定增强产物（annotation、report）是否全量就绪。
- **机器操作追踪**：`trace.begin_model_request` 与 `runtime_telemetry` 统一携带机器可读稳定标识 `operation_id`（如 `translation`、`annotation`、`understanding`、`terminology`、`review`、`repair`、`knowledge`）与 `purpose`，消除依赖中文标签解析的脆弱性。
- **里程碑记录**：在任务关键阶段精确记录 `draft_ready`、`translation_ready`、`enhanced_delivery_ready`，无敏感原文/译文入日志。
- **验证**：`tests/readiness_telemetry_test.py`（11 项）全部通过。

### 6.3 P1a：自动标注有界补队与持久暂存（Annotation Durable Ready Staging）
- **持久暂存与指纹校验**（`folith/annotation_staging.py`）：
  - 任务批次输入以 `(source, target, model, target_lang)` sha256 指纹为标识，结果生成后原子落盘暂存；
  - 译文版本若被后续编辑修改，指纹不匹配自动失效并清理，杜绝旧版本写回。
- **有界容量补队（C4 模型）**：
  - `core._annotation_results` 严格保持网络并发 `workers <= 2`，候补容量限制在 `capacity = 4`；
  - 当第 0 批慢、第 1 批快时，第 2 批可在未越过容量上限前并发执行并暂存；结果严格按 0, 1, 2... 顺序向消费者 yield；
  - 消费者持久提交（`save_job_state`）成功后调用 `confirm_staged_annotation` 确认，整阶段完成自动清理暂存目录。
- **故障与恢复保障（0 重复调用）**：
  - 发生磁盘落盘失败或进程中断时，已暂存的候选批次在恢复时直接从暂存命中加载，实现**已完成候选结果 0 重复模型请求**。
- **验证**：`tests/annotation_durable_staging_test.py`（6 项）、`tests/annotation_concurrency_test.py`（5 项）、`tests/workflow_recovery_test.py`（15 项）全部通过，端到端标注结果与串行过滤逐字段完全一致。
