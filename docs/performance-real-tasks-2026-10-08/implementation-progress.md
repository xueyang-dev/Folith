# 第一轮优化实施记录

日期：2026-10-08。当前工作区实现，尚未部署；没有运行真实 API A/B，没有改动真实任务目录。历史实测见 [审计报告](README.md)，后续路线见 [优化路径](optimization-path.md)。

## 已实现

- `core.py`：自动标注使用最多 2 个 worker，沿用已有 `translation_concurrency` 的更低上限。保持原批次、输入与提示词。请求可以乱序完成，主线程仍按原批次顺序合并、保存和执行全局术语覆盖/去重。只有保存成功后补充下一个任务；取消或保存失败停止补充，已发出的请求仍可能完成。
- 恢复沿用 `annotation_batches` 和 `annotations_done_offset`，失败批次重新处理，成功且已保存的批次跳过。提前计算 offset，消除逐批重复求和。
- `folith/trace.py`：每个运行目录新增 `requests.jsonl`，保存逻辑请求关联 ID、job/run、角色、批次、UTC/单调时钟起止、逻辑在途数量、模型调用状态/错误/usage；usage 缺失明确标记。外层翻译重试和标注限流重试写入同一流。缓冲每 25 个事件刷新，模型错误立即刷新，正常结束关闭流；无逐事件 fsync。日志异常不覆盖业务异常。
- `core.py`：正文翻译、相关修复/审校及标注的现有 provider gate 增加等待 span。主提交路径的辅助调用关联当前批次。
- `folith/performance.py`：逐项记录单调时钟边界，支持后续计算区间并集。
- `folith/usage.py`：调用角色通过 ContextVar 传递，在异常时也恢复。修正已有翻译并发测试直接覆盖全局函数/输出目录造成的跨测试泄漏。

模型、审校/修复规则、知识反馈触发、标注范围与提示词、翻译批大小及交付门禁均保留。

## 验证与可复现结果

106 项相关回归通过，覆盖现有传输、翻译、质量、性能、恢复保护，以及新增乱序完成、结果等价、取消、保存失败、失败批次续跑、异常请求收尾和角色恢复。`git diff --check` 通过。

```sh
python3 -m pytest -q tests/annotation_concurrency_test.py tests/request_trace_persistence_test.py tests/performance_trace_test.py tests/workflow_recovery_test.py tests/annotation_quality_test.py tests/request_control_test.py tests/translation_instrumentation_test.py tests/translation_concurrency_test.py tests/translation_runtime_test.py tests/llm_transport_test.py
python3 docs/performance-real-tasks-2026-10-08/annotation-replay.py
```

固定响应回放使用 65 个批次，每个模拟请求等待 20ms，真实执行临时目录中的顺序保存和全局清理。串行 1.701772s，并发 2 为 0.859767s，墙钟减少 49.48%；两组均 65 次调用，最终 annotations、批次状态、完成标志、断点位置一致。原始结果：[annotation-replay-result.json](annotation-replay-result.json)。这是**合成负载的本机实测**；不是真实 API 或端到端翻译收益，不含语言模型随机性验证。单次回放不用于稳定 P95 或生产速度承诺。

## 当前边界与下一步

第一批遥测尚未全部完成：SDK 内部 HTTP 尝试/退避、完整队列与 ready→commit 等待、每次运行的构建/配置快照、交付里程碑以及全阶段上下文组件重发量仍需要补齐。provider gate span 是组合等待，不区分 pacing 和 cooldown。TTFT 未记录；逻辑在途数量不得称为实际 HTTP 并发。日志为缓冲写入，进程突然终止可能丢失尾部事件；存储故障无法保证写入。当前未测遥测开销是否满足 1% 目标，也未取得同版本真实 API 基线。

接下来先完成这些遥测与隔离的短文档成对 API 对照，再验证 Sensorium 长文档标注及端到端收益。真实请求波动、429、Tokens 和人工质量抽查尚未验证。部署依据仍是同版本真实对照。

知识反馈暂不并发：当前 `core.py` 主提交逻辑以 accepted_for_knowledge 和已知名称集合决定是否抽取，输入还包括已提交的 pairs；提前抽取尚不能证明触发、输入及后续上下文完全相同。本轮保持原调用集合和可见性，继续用细分计时确定可移出的等待。批大小 4/8 与上下文成本实验留待遥测及基线完善后执行。


## 第二轮：请求与批次时序补全

本轮继续在本地工作区实施，尚未部署，也没有修改业务任务目录。新增：

- OpenAI 兼容接口使用 SDK 默认 HTTP 客户端的公开 event hooks，保留原有连接池、代理模式、超时和重试。实际 HTTP **尝试开始**与**响应头到达**逐次关联逻辑请求；真实 SDK 加离线 HTTP transport 验证了 429 与连接失败重试，不将一次逻辑调用冒充一次 HTTP 尝试。Gemini 原生传输尚未补这些 hook。
- 翻译与标注记录 queued、worker_started、result_ready、commit_started、commit_finished/worker_failed；另存有效并发和批次数。等待按序提交可由同批事件重算。审校重叠模式的 worker result_ready 是生成准备完成，后续独立审校计入 ready→commit，不能称为纯提交排队。
- 每次运行保存去敏配置、应用版本与构建 ID。源代码运行的构建 ID只标识 Git HEAD，不包含当前未提交修改，事件明确注明这个限制；没有新增代码指纹。新增译文/审校、标注和自动步骤完成里程碑。交付资产与快照生成独立计时，人工审批等待没有实测值。
- 翻译外层每次调用（含失败）保存正文与上下文组件字符数，所有调用记录 system/user 字符数；字符统计不是精确 Tokens，也不证明失败连接发送了请求正文。修正原有“other”组件少计 system 字符数的统计错误，提示词内容保持相同。
- 逐阶段同时保存 container wall、item_wall_sum_s 与 interval_union_s。没有逐项区间时后两者为 null，保留原 active_wall_time_s 兼容字段但不将其作为区间并集。
- 翻译异常退出关闭并保存已创建阶段，日志保存失败不能替换业务错误。运行“完成”记录移至最后一次状态保存成功之后，避免最后保存失败仍宣称成功。
- 请求 ID 改为运行 ID 加锁内序号。CPU profile 中原 ID 日期格式化在 120 次调用中累计约 36ms；新 ID 保持可关联性，消除逐请求随机数、日期格式化及散列。

### 可复现证据

新增 [指标重算脚本](../../scripts/report_request_trace.py)：

```sh
python3 scripts/report_request_trace.py /path/to/run/requests.jsonl
python3 docs/performance-real-tasks-2026-10-08/request-telemetry-replay.py
```

[合成日志](synthetic-requests.jsonl)重算出 120 次逻辑调用、120 次 HTTP 尝试与 1,440 个输入 Tokens，并与预设回放数据一致。这些 Tokens 是合成 fixture 的预设 usage，**不是实际消费**。重放[汇总](synthetic-request-metrics.json)还包括批次等待、未闭合请求、usage 缺失与里程碑。它不以零填补缺失的 Tokens 或耗时。

开销回放每次包含 120 个模拟 20ms 请求，预热后按 off/standard/standard/off 顺序执行。ID 改动前实测墙钟增量 2.63%，改动后 1.10%；原始数据分别保存在 [改动前](request-telemetry-overhead-before-id-change.json)和[改动后](request-telemetry-overhead.json)。不同进程与机器负载会影响墙钟，不能据此推断稳定的生产增量。当前这项合成测试仍略高于 1% 工程目标，**没有宣称达标**。启动版本读取和逐事件记录是剩余成本，收尾约几毫秒；真实长请求的开销比例仍未验证。

已通过 163 项相关回归，新增覆盖 SDK 重试关联、乱序等待边界、区间并集、配置去敏、交付字节不变、失败阶段保存、最后保存失败的完成状态。没有测试真实模型输出、网络限流或生产墙钟收益。

### 尚未完成

实际 HTTP body 完成/连接失败的逐尝试结束仍缺失，HTTP hooks 到响应头的区间不是完整请求耗时，也不是 TTFT；不能据此报告完整 HTTP 并发。缺失响应头的尝试在报告中单列。SDK 内部退避尚未独立测量，两个尝试之间的间隔只能作为间隔观察，不能直接命名为 backoff。提供商未返回 usage 的失败尝试，其消耗仍未知。

配置快照为去敏选项，不能单独恢复完整输入、术语表和用户风格文本；真实 A/B 仍需同一版本、同一原始任务快照。知识反馈依赖、质量策略与生产默认批大小未改变。

桌面当前可用配置为 OpenCode Go / deepseek-v4.1-flash；已请求选择是否用于小规模隔离 API 对照，尚未执行付费请求。后续先做相同短文档标注批次的串行/并发 2 对照，再决定是否开展整篇长文档验证。
