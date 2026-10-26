# Phase 3 P0：最终 Endpoint 身份与 Annotation Fail-Stop

日期：2026-10-26。工作区 `/Users/xueyang/Dev/Folith-phase3`；分支 `perf/phase3-runtime-optimization`；HEAD `397adb22d8de46edb0781dc75e53661318a5cb11` 加未提交修改。

**实施完成；独立 Agent 复验 PASS，限实际执行的本机 P0 工程契约。** [独立复验报告](phase3-p0-reacceptance/report.md) 与 [机器结果](phase3-p0-reacceptance/result.json) 确认 BR01/BR02 在所测范围关闭，没有发现新的确认缺陷。这个结论不等于完整 Phase 3 或跨平台生产验收。

## Endpoint：SDK 前完成解析

`resolve_openai_base_url` 现在统一解析官方 OpenAI 的环境 `OPENAI_BASE_URL` 与默认地址，SDK 构造和连接池 identity 显式使用同一个最终 URL，连接层不再独立依赖 SDK 回退。`model_connections.connection_base_url` 复用该解析，不再重复环境分支。

标注 executor 启动前固定 endpoint；请求指纹、worker 实际调用和消费者提交前复验使用同一执行上下文。即使指纹计算后环境变量改变，本次请求仍到固定 endpoint；下一次执行重新解析，endpoint 改变会失效旧候选。上下文在执行后恢复，避免污染后续调用。

pipeline 显式传入的 `translator_base_url` 通过 `annotate_stage(..., base_url=...)` 传给 `_annotation_results`，覆盖环境默认。已验证 `p2_done=True` 的恢复任务显式选 A、环境选 B，annotation 实际 HTTP 到 A。

覆盖：环境 A/B 切换、URL 归一化后的同线程 client 复用、不同 endpoint 的池隔离、自定义 relay 不回退到其他环境地址、未确认暂存候选恢复、指纹后环境改变，以及显式 pipeline/环境冲突。候选身份仍不包含 API key。

## Fail-Stop：先保存原始错误，停止未发请求

单次 annotation execution 使用共享 `AnnotationFailStop`，记录第一个 `AnnotationStagingError`。作用域绑定到 executor 调度线程和两个 worker，执行完毕恢复调用者上下文；不把故障存成污染下一次执行的全局状态。

检查点覆盖：

- 提交新 worker 与有序消费者继续前；
- 真实 pause loop 和取消检查；
- 真实 `ProviderRequestController.acquire` 的取消探针；
- `call_llm` 预发送检查；
- 实际 OpenAI-compatible HTTP attempt hook，包括 SDK retry。

失败后的暂停线程不需用户 Continue/Cancel 即退出；provider gate 等待线程不再发送。Executor join 后优先传播原始存储故障，避免次生 SDK 错误或并发取消覆盖它。`ordered` 继续是调度回退，但同样遵守 Fail-Stop，不关闭暂存保护。

真实暂存写入/序列化错误不再吞成 False：`AnnotationStagingError.storage_error` 与 `__cause__` 保留原始异常，errno/filename 保留。临时文件清理失败不覆盖更早的写入错误。旧 False mock/未确认写入也会明确失败。

后台 worker 将此类故障归为 `failed`，即使同时收到用户取消/退出；runtime error 包含 errno 与原始错误类型，暂停标志清除。已验证线程、heartbeat 和 `.worker.lock` 释放，cursor 不前进且不产生 enhanced ready；修复存储后新的 execution 可以恢复。

**边界**：通过最后发送检查、已进入远端执行的请求仍可能等待既有网络超时；本实现不承诺瞬间终止远端推理或免除其费用。HTTP retry 的阻断在真实 SDK + MockTransport 场景验证，不是所有 provider/SDK 组合的真实网络证明。文件系统永久拒绝清理时无法承诺即时清除 temp；原始故障仍保留，恢复存储后再清扫。

## 验证记录

实施侧分组去重 **707 passed，4 skipped**；[选择清单](phase3-p0-evidence/test-selection.json) 共 711 个唯一测试。未把重跑的子集再次累计。

- 定向：119 passed，7.62 秒。包括原相关回归与 16 项当时的新增 P0 用例。
- 扩展：587 passed，4 skipped，71.89 秒。与上述分组不重叠，覆盖 shared settings/Qt、连接配置、translation core、delivery、persistence/journal、request 与 telemetry 等。
- 随后增加显式 pipeline endpoint 用例并重跑相关子集：28 passed，4.05 秒；仅增加的 1 项计入总数。新增 P0 回归现共 17 项，位于 [annotation_fail_stop_test.py](../../tests/annotation_fail_stop_test.py)。
- `git diff --check` 通过。

4 项跳过要求真实 Windows 文件共享/ReplaceFileW/snapshot 语义。警告为已有 SWIG deprecation 与 Qt signal disconnect。运行环境 macOS arm64、CPython 3.11.3，所有请求为合成 fixture + MockTransport/SDK 替身，无真实付费 API。

独立 Agent 在生产代码固定后执行：

1. **原样重跑旧 31 项边界探针：23 passed /8 failed。** 8 项失败全部是断言旧 BR01/BR02 仍存在的历史 observation，修复后不应继续通过；历史探针没有被改写或跳过。
2. **自编正向验收：13 passed，3.18 秒。** 使用真实 annotate_batch、call_llm、SDK、pause loop、provider controller 和 MockTransport，覆盖三个模式的暂停/gate 六组交错、原始异常 cause/errno、失败状态、heartbeat/锁释放、存储修复后恢复、SDK retry 阻断及 endpoint/池/恢复契约。

独立报告给出范围明确的 PASS。它没有修改生产代码，也未把实施者通过声明作为证据。

## 复现

```sh
cd /Users/xueyang/Dev/Folith-phase3
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q tests/annotation_fail_stop_test.py
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q docs/performance/phase3-p0-reacceptance/independent_acceptance_test.py
```

完整分组选择见 JSON，独立历史探针原始输出见独立报告目录。历史 [边界报告](phase3-boundary-review.md) 与发现 JSON 保留，不改写过去的复现事实。

仍未验证：真实 Windows、所有 provider 的真实网络/重试/计费组合、主机断电和整卷持续 ENOSPC、全仓库套件、完整 Phase 3 质量/性能实验。默认仍 C2；没有将 C4 推广成默认。本轮没有 commit、创建 PR 或 merge，原有本地修改均保留。
