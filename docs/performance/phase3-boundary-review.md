# Phase 3 三个边界的独立复核

> 当前 P0 状态：BR01/BR02 已修复并经独立 Agent 在本机工程范围复验通过，见 [P0 修复记录](phase3-p0-fixes.md) 与 [独立复验](phase3-p0-reacceptance/report.md)。下文保留历史验证事实。

日期：2026-10-26。目标工作区 `/Users/xueyang/Dev/Folith-phase3`，分支 `perf/phase3-runtime-optimization`，HEAD `397adb22d8de46edb0781dc75e53661318a5cb11` 加现有未提交改动。

**结论：复现两个 HIGH 问题。F04 的请求身份验收需要重开；F06/F08 关于失败后退出及回退的保证只能算部分成立。暂不批准 Annotation 子系统生产验收。**

本轮为只读诊断：没有修改生产代码、提交或合并 PR。新增的只是独立探针和本报告。前一轮的 604 项回归通过是真实记录，但不能证明下面这些新增场景正确。

## 1. 身份是否与真实请求一致？——一般路径一致，但存在已复现缺口

检查没有停在 `annotate_batch` 替身：保留真实 `annotate_batch → call_llm → _call_llm_transport → OpenAI SDK → HTTP 序列化`，仅把最末 HTTP transport 换成 `httpx.MockTransport`。它在内存里捕获最终 URL 与 JSON body，不会向 provider 发出请求。

四个 OpenAI-compatible 组合：DeepSeek 默认；自定义 relay 的 GPT-5/high/thinking_disabled；OpenAI GPT-5 默认；自定义 relay 的不识别 reasoning 模型。分别对照：

1. 请求前 fingerprint 与参与计算的 context；
2. 暂存函数接收到的 fingerprint；
3. 已写入 JSON 的 fingerprint；
4. durable state commit 前重新计算的 fingerprint；
5. 最终 HTTP URL、messages、model、有效 reasoning、thinking 和实际发送的 temperature。

这四组的指纹一致，精确 prompt、模型和生效参数与最终请求一致。OpenAI GPT-5 会省略 temperature，所以身份不是 HTTP body 的逐字节摘要：它还包含任务映射、schema 等提交语义，并保留了一个未发送的固定 temperature 字段。此处没有复现错误复用，仅意味着“三处 hash 相等”不能单独证明完整依赖覆盖。

Gemini 另在 SDK `generate_content` 边界检查实际 `GenerateContentConfig` 的 system instruction、contents、temperature，以及请求前/暂存/提交前身份一致。没有将这个 SDK 级检查冒充 Gemini HTTP 序列化或真实 provider 验收。

### BR01 — HIGH：OpenAI SDK 的环境 endpoint 不在新指纹中

- **位置**：`core.py:4391`；实际缺口在 `folith/engine/llm_provider.py:139`；SDK 构造路径为 `core.py:2267`。
- **复现**：`test_observation_sdk_environment_endpoint_is_missing_from_fingerprint`。
  1. `OPENAI_BASE_URL=https://synthetic-a.invalid/v1`，OpenAI/gpt-4o，生成但不确认一个候选；最终 HTTP URL 确认为 A。
  2. 改为 B，使用同一任务和同一段落恢复；它直接复用 A 的候选，没有产生新请求。
  3. 在新任务强制生成；最终 HTTP URL 确认为 B，但 fingerprint 与 A 相同。
- **预期**：实际 endpoint 改变必须改变候选身份；不能复用另一 endpoint 的结果。
- **实际**：`effective_request_route` 对官方 OpenAI 默认路由给出空 `base_url`；构造 SDK 时又省略该参数，SDK 自行读取 `OPENAI_BASE_URL`。同一空 route 对应两个真实 endpoint。请求前、暂存时、提交前的 hash 全相等，仍然是错误的语义身份。
- **可达性**：项目 `folith/model_connections.py:24` 本身就使用 `OPENAI_BASE_URL`，本机安装 SDK 也明确消费该环境变量。这不是杜撰 feature flag；实际 HTTP 序列化已复现差异。跨重启切换环境与测试中两次新 executor 的行为相同。
- **影响**：更换已选服务端后可能获得前一个服务端生成的 annotation，绕过请求身份失效。未证明权威译文丢失或真实账单损失。
- **最小修复方向**：在应用进入 SDK 前统一解析最终 endpoint，并让 fingerprint、client/pool identity 和真实 SDK 构造消费同一个解析结果，显式传入；不要让 SDK 的环境回退发生在 identity 计算之后。保留 credentials 不进入候选身份的边界。
- **证据**：真实 SDK / 最终 HTTP MockTransport 捕获；HIGH confidence。阻止关闭 F04。

## 2. 暂存写入失败能否始终退出和恢复？——否，暂停交互仍有问题

正确部分已独立验证：

- C2、C4、ordered 各注入 staging mkdir、JSON serialization、fsync、replace，以及 replace 后 temp unlink 也被拒绝的错误。
- 没有暂停叠加时，异常离开实际 stage，Executor 线程全部退出，没有继续补充超过两个已启动的请求；失败前的 `state.json` 字节保持不变。
- 单独写入失败时 owned temp 被移除；unlink 本身被拒绝时 temp 确实保留，不能宣称“文件总能清干净”。解除错误后，下一次 stage 启动会清扫这些 temp 并正常恢复。
- 使用实际 `start_job_worker → run_job_pipeline → annotate_stage`，三个模式都会发布 `failed`、释放 worker/运行锁，annotation cursor 不前进、不写 enhanced ready。切成 ordered 而不修复存储仍会失败；修复存储后通过 `resume_job` 恢复到 completed。

这些测试特意保留 runtime metadata 可写，因此没有证明整块磁盘持续 ENOSPC 时还能成功把“失败状态”写回同一磁盘。已发出的网络请求也不会因 `Future.cancel()` 被强制中断；真实 SDK 超时/外部 I/O 是另一边界。

### BR02 — HIGH：暂存失败无法终止暂停的 peer，失败发布与锁释放等待人工解除暂停

- **位置**：`core.py:4568` 的 Executor 上下文、`core.py:4634` 的 future cancel；相关暂停循环为 `core.py:366`。ordered 的同类清理在 `core.py:4593`。
- **复现**：`test_observation_product_failure_publication_waits_for_manual_pause_release`，对 C2/C4/ordered 全部执行；另有 generator 级 probe 和最终 HTTP 级 probe。
  1. 两个 annotation worker 已提交。
  2. 第 0 批进入模型请求；第 1 批在真正发请求前到达暂停检查点。
  3. 发出正常的 `request_job_pause`，第 1 批进入真实 pause loop。
  4. 第 0 批完成，但暂存失败。
  5. 主线程观察异常后取消 pending future，Executor 退出却仍等待第 1 个正在运行、处于 pause loop 的线程。
- **预期**：暂存失败优先于普通暂停；停止未发出的请求，解除内部等待，保留原始失败原因，收尾线程/锁并发布 failed。
- **实际**：pause loop 没有获知 annotation 失败。后台 worker 继续存活并保留运行锁，产品未发布错误；C2/C4/ordered 都不能在此时重启。用户“继续”后第 1 批反而进入模型调用，随后整个任务才报暂存失败。一个额外的实际 SDK→HTTP MockTransport probe 确认：第二个最终 HTTP 请求在第一个暂存失败之后才产生。
- **精确边界**：这不是永不可恢复的锁死。用户取消或解除暂停可以释放它；但代码没有内部失败唤醒机制，只要 pause 标志保持不变，就无法自行完成失败退出。测试观察受 0.3–0.35 秒时间窗限制，无界等待结论来自真实 pause loop 和 Executor shutdown 的控制流，而非把短暂延迟叫作永久死锁。
- **影响**：错误不可及时见，锁/worker 继续占用；解除暂停可增加失败之后的新请求和付费风险。没有执行付费请求，也没有把 MockTransport 调用计为实际账单。
- **最小修复方向**：把 staging failure 接到全体 annotation worker 可见的 fail-stop 信号；pause loop、provider gate 和发请求前检查都观察该信号，取消未发请求并唤醒暂停等待。优先复用现有 workflow failure/cancel 检查路径，保留存储异常及其 errno/reason；内部 fail-stop 不应被误归类成人工 cancelled。最后仍要等待或有界收尾已发请求，独立释放 heartbeat/worker lock，再发布原始 failed。
- **证据**：真实 pause loop、实际后台 worker API、真实运行状态与重启拒绝、最终 HTTP 请求捕获；HIGH confidence。F06 的正常错误路径成立，但更强的退出保证与 F08 回退验收不成立。

## 3. ordered 回退是否可靠？——是调度回退，不能修复暂存

确认：ordered 确实是原式“按序等待 → 消费者提交 → 补下一个名额”的算法；仍然调用同一个 request/staging/identity/ack 协议。因此：

- 它可以减少补队窗口并回退算法；
- 它不能修复 BR01 的身份问题；
- 它不能绕过磁盘/权限/序列化故障；
- 它同样受 BR02 的失败＋暂停交互影响。

不能向用户宣称“切 ordered 就恢复整个 annotation 子系统”。当前名称和前一轮修复报告已说明这一点，定义本身正确，不另制造第三条缺陷。

产品应遵守的安全失败契约：

1. 持久化失败后不自动降级为内存结果、不扩大窗口、不自动循环重试付费请求。
2. 通知所有 worker 停止新请求，失败优先解除暂停/provider-gate 等待；已在远端执行的请求明确受现有取消/超时边界约束。
3. 保留已持久前缀和可验证的成功候选；不推进未提交 cursor，不标 annotation/enhanced ready，不修改已冻结交付。
4. 发布明确的 failed 与存储操作原因，释放线程、heartbeat 和任务所有权。若同一磁盘使错误状态无法写入，应与“失败已持久化”区分，不能伪造成功。
5. 用户修复存储并显式恢复后才继续；候选重新验证。C2/ordered 是调度选择，不是绕过此协议的后门。

当前在没有暂停交互、runtime 可写的故障场景中满足正常失败/恢复路径；BR02 修复前不能声称始终满足以上契约。默认 C2 继续保守，但它也不是这两个 HIGH 的修复。

## 4. 证据与限制

```sh
cd /Users/xueyang/Dev/Folith-phase3
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q docs/performance/phase3-boundary-review/boundary_observations.py
```

最终结果：**31 passed，5 warnings，5.70 秒**。其中 8 个 observation 用例通过是确认缺陷存在，不是通过产品验收；其余用例确认正常语义契约及可恢复故障行为。文件：[独立探针](phase3-boundary-review/boundary_observations.py)、[发现 JSON](phase3-boundary-review/findings.json)。

HTTP fixture 最初违反了 SDK 对 httpx client 的类型要求，未产生 wire capture；改为真正的 Client 子类后才采用以上结果。全部网络调用使用 MockTransport 或 Gemini SDK 替身，没有真实 API 支出或用户文档。只将合成输入留在测试源码；完整 prompt/请求捕获仅在内存中使用。

环境：macOS 27.0.1 arm64 / Python 3.11.3。未运行真实 Windows、主机断电、真实 provider 流式/超时/计费、整块卷持续写满或挂死的内核 I/O。没有重复跑全套回归来掩盖边界问题。

当前处理建议：先修 BR01 和 BR02，再复核这三个边界。此前 F04 完全关闭与“写入失败总能退出”的表述应撤回；原回归通过数保留为历史事实。
