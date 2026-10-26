# 独立 Agent：P0 Endpoint / Annotation Fail-Stop 复验

日期：2026-10-26。目标：`/Users/xueyang/Dev/Folith-phase3`，分支 `perf/phase3-runtime-optimization`，HEAD `397adb22d8de46edb0781dc75e53661318a5cb11` 加当前未提交改动。

**结论：PASS（限本报告明确执行的 P0 工程契约与 macOS 无网络验证范围）。** 未复现 BR01、BR02；没有发现新的可证实 P0 缺陷。这个结论不等于整个 Phase 3 完成、真实 provider/Windows 生产验收或主机断电持久性证明。

我未修改生产代码或历史探针，没有创建/合并 PR。以下结论来自独立读取当前实现、实际执行原探针以及另写的正向验收；未把实施 Agent 的通过声明当作证据。

## 执行与结果

1. 原样运行历史 `docs/performance/phase3-boundary-review/boundary_observations.py`：**23 passed /8 failed**。八个失败恰好全部是原来“确认旧缺陷仍存在”的 observation：endpoint collision 一项、stage-paused 三项、product-paused 三项、失败后继续发 HTTP 一项。它们转为失败是预期变化，不能算 31 项全部通过，也不能直接解释为八个新回归。其余 23 项 prompt/请求身份、存储故障恢复、正常 failed 状态与 Gemini SDK 边界检查仍通过。原始输出见 [original-probes.txt.gz](original-probes.txt.gz)。
2. 自行编写正向验收 [independent_acceptance_test.py](independent_acceptance_test.py)：**13 passed，5 warnings，3.18 秒**。警告为已有 SWIG deprecation；无跳过、无当前正向失败。输出见 [independent-results.txt](independent-results.txt)。

命令：

```sh
cd /Users/xueyang/Dev/Folith-phase3
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q docs/performance/phase3-boundary-review/boundary_observations.py
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q docs/performance/phase3-p0-reacceptance/independent_acceptance_test.py
```

独立测试首轮有一个 fixture 错误：没有把 `ANNOT_BATCH_SIZE` 设为 1，两个段落合并成一个 batch，不能进入设计的 peer 等待场景。已修正夹具后才采用上述结果；未为此改生产代码。

## Endpoint 身份

核实当前 `resolve_openai_base_url` 在应用层解析官方 OpenAI 的 `OPENAI_BASE_URL` /默认地址，SDK 构造获取同一个结果并显式传入，连接池 identity 同样包含该结果。标注执行在 executor 启动前确定 endpoint，绑定到各 worker 与消费者的当前上下文，避免 hash 之后由 SDK 再单独选择地址。pipeline 的显式 `translator_base_url` 也被传到 annotation。

独立执行：

- 同线程、环境变量 A 的等价 URL（带 `/chat/completions` 与基础 `/v1`）复用同一 client；切换为 B 构造不同 client，最终 HTTP 到 B。
- 显式 URL 覆盖环境 URL，并形成独立连接池身份。
- OpenAI 默认环境路径和自定义 relay 均验证：相同 endpoint 的未确认成功候选可恢复复用；endpoint 改变使 fingerprint 改变并重新请求新 endpoint。
- 在请求 fingerprint 计算后故意切换环境变量：当前执行的 fingerprint、暂存 fingerprint、提交前 fingerprint 和最终 HTTP 仍使用执行开始时确定的 endpoint；退出后上下文恢复。
- `p2_done=True` 的后台恢复任务，pipeline 显式 OpenAI 地址 A 与 `OPENAI_BASE_URL=B` 冲突：两个实际 annotation HTTP 均到 A，保存的 translator config 与请求一致。

请求捕获保留真实 `annotate_batch → call_llm → SDK → HTTP 序列化`，只在最后使用 `httpx.MockTransport`。没有真实 provider egress。连接池是否复用由实际 client 构造数量验证，不仅检查字符串或 helper 返回值。

**BR01 在这些边界已关闭。** 身份还包含 mapping/schema 等提交语义，因此不要求 hash 就是 HTTP body 的逐字节 hash；重要的是所有会选择真实 endpoint 的输入在 SDK 前已经解析并一致使用。

## Fail-Stop

核实一个 annotation 执行共享 first-storage-error latch，作用域在 executor 与 worker 上；pause/cancel probe、provider gate probe、call_llm 的预发送检查以及每次 HTTP attempt hook 都检查该状态。Executor join 后优先重新抛出原始存储失败，再恢复调用者上下文，避免把下一次恢复污染为旧故障。

独立正向场景均使用真实 `annotate_batch`，没有用返回固定数组的 annotation 替身绕过 provider-gate 异常路径：

- C2、C4、ordered × pause/gate 共六组后台 worker 测试。
- pause 场景调用真实 `request_job_pause`、真实 pause loop；gate 场景让第二个 worker 在真实 `ProviderRequestController.acquire` 的 cooldown 中等待。
- 第一批真实 SDK 请求返回后，由真实暂存写入路径注入 `PermissionError(EACCES)`。
- 不需用户 Continue/Cancel，后台 worker 自动发布 `failed`；等待中的 peer 没有请求到达 HTTP transport。所有六组最终只有第一批的一个 HTTP 请求。
- 抛出的 `AnnotationStagingError.storage_error` 和 `__cause__` 保留同一个原始异常对象，runtime error 保留 errno，未推进未提交 annotation cursor 或宣称 done。
- worker owner 字段清空；实际 `.worker.lock` 可重新取得；无遗留 `ft-annot` 线程或本次新增 heartbeat 线程。
- 修复暂存故障并以 `resume_job` 启动新 execution：没有旧 failure latch 污染，可正常完成。总请求数为失败前一个已完成请求加恢复时两个请求，符合“未成功暂存的响应可能重发”的保证边界。

另有并发用户取消测试：暂存失败时同时设置 cancel_requested，最终仍发布 `failed` 并保留 EACCES，而不是归类为用户 cancelled。

### SDK retry 也受阻断

另写两批实际 SDK 请求场景：两批都已到达 HTTP MockTransport，第一批响应成功但暂存 ENOSPC；第二批在原始远端请求位置返回 500，诱发 SDK retry。

结果：实际 HTTP hook 至少运行三次，但 MockTransport 只收到最初两次请求；失败后那次 retry 在 hook 被阻断，没有再次到达 transport。任务仍以原始 ENOSPC 为 failed，不被次生 SDK connection/retry 错误覆盖。Executor 退出且没有 annotation worker 残留。

**BR02 在这些边界已关闭。** 原有“暂停＋失败需人工解除”与“Continue 后再发一批”的行为未再出现。

## 回退与保证边界

`ordered` 仍是调度回退，不是绕过新暂存协议。上面的 ordered 两种等待场景通过同一个安全失败契约，而非关闭暂存后继续执行。默认 C2 没有被本次 P0 验证改成 C4；本报告不裁定 C4 默认推广。

已在远端执行的请求不会瞬间停止。MockTransport 测试证明后续 transport dispatch 被阻断，不证明操作系统能立刻中止真实网络等待、provider 推理或已计费请求；现有 timeout 仍是边界。

## 未验证

- Windows 实机的文件 sharing、ReplaceFileW、线程退出与本地 UI。
- 真实 provider 质量、延迟、账单、真实网络黑洞或所有 SDK/provider retry 组合。
- 主机断电、目录 fsync、整卷持久 ENOSPC 时失败状态能否写回同一磁盘。
- 完整仓库测试、其他 Phase 3 项目以及任意不受支持的内部状态修改。

环境：macOS arm64 / CPython 3.11.3，现有 `/Users/xueyang/Dev/FolioThread/venv/bin/python`。所有数据为合成 fixture；请求捕获仅在内存中使用。生产修改仍需正常代码审查，本次报告不执行 merge。
