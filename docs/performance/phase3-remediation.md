# Phase 3 验收问题修复与 C2/C4 裁定

> 当前 P0 状态：BR01/BR02 已修复并经独立 Agent 在本机工程范围复验通过，见 [P0 修复记录](phase3-p0-fixes.md) 与 [独立复验](phase3-p0-reacceptance/report.md)。下文保留历史验证事实。

> 后续独立边界复核发现两个 HIGH，F04 需重开、失败退出保证仍有缺口。以下实施验证为历史记录，当前判断以 [三边界复核](phase3-boundary-review.md) 为准。

日期：2026-10-26。工作区：`/Users/xueyang/Dev/Folith-phase3`；分支 `perf/phase3-runtime-optimization`；HEAD 仍为 `397adb22d8de46edb0781dc75e53661318a5cb11`，本轮修复尚未提交。

这是实施者的修复与验证记录，不是重新出具的独立验收。原 [独立审计](phase3-independent-acceptance.md) 和 [原始 findings](phase3-acceptance-findings.json) 保留为修复前证据；当前逐项状态见 [修复状态 JSON](phase3-remediation-status.json)。没有修改无关本地工作，没有创建或合并 PR。

## 1. Readiness / Telemetry（先修）

- **F01**：draft readiness 复用 `delivery.unrecovered_scan_pages` 和 `translation_target.validate_translation_pairs`，同时检查已知源段落是否缺少 pair。扫描缺页和 JSON/Markdown 等不合法目标正文不再报 ready。已有源质量隔离记录继续遵守既有 scope 语义。
- **F02**：复用 `delivery.unresolved_blocking`，不再把已解决、已被替代的历史 finding 或过期 `has_blocking` 缓存当成当前 blocker。
- **F03**：已选报告需同时满足生成完成与 `delivery.report_ready` 的最终报告状态。报告被最终策略拦截时，enhanced readiness 为 false。
- **F07**：真实修复、批后知识反馈、翻译、文档理解、术语和独立审校调用设置明确 operation ID。路由 role 与 operation 保持独立；修复仍可走 translator 但记录为 repair。线程本地作用域及异常恢复不变。
- **F10**：里程碑写入前检查对应当前 readiness，记录权威 translation version 和 review/增强产物选择策略。同一版本/策略的续跑不覆盖首次时间；版本改变后可记录新时间。权威版本 **0** 原样保留，不再混同为 1。历史时间不用于判断当前是否 ready。真实 pipeline 中 annotation partial 不会产生 enhanced ready。

人工交付批准、快照和渲染门禁保持独立，未把 enhanced readiness 当成人工批准。

相关代码：[`readiness.py`](../../folith/readiness.py)、[`core.py`](../../core.py)、[`translation_evidence.py`](../../folith/translation_evidence.py)。

## 2. Annotation Staging（随后修）

- **F04**：使用结构化 SHA-256 身份绑定实际 eligible 段落、有序局部映射、存在的段落 ID、精确 source/target、语言/model、provider、现有 `effective_request_route` 解析出的 endpoint/reasoning、实际 system/user prompt、生成温度及暂存/输出 schema 版本。prompt 组装与真实请求共用同一函数，不维护另一份模板。仅摘要写入元数据，不写入 API key、连接配置或原始 prompt。术语表仍在后续确定性过滤阶段处理，不因无关 glossary 更新重发 raw 请求。只更新未参与本批请求的段落，不改变其请求身份。
- 请求使用段落快照，消费者在修改 annotation 真值前重验当前请求身份；改变后的 target 不会提交旧结果。任务仍受既有 worker/save 所有权机制保护，没有增加重叠流水线或另一套并发状态。
- **F05**：只有 `success` / `empty` 候选可保存和重放。失败记录、旧 schema、损坏 JSON 或不合法的规范化条目不会被当成成功候选。真实 state-save 失败后的第一次恢复即可重新请求失败批次。
- **F06**：暂存写入返回失败时抛出明确错误，停止继续补队；不会把没有落盘确认的结果交给消费者。已发出的至多两个请求可能完成，但不再扩大补队窗口。
- **F09**：临时文件关闭前 flush/fsync；替换复用项目已有跨平台 `replace_file`，读取复用 `read_shared_text`。写入失败的 owned temp 在 finally 清理；重启后在工作线程启动前清理遗留 temp；成功收尾清理整个派生暂存目录中的文件。Windows sharing violation 的重试仅重试替换，不重发模型请求。

暂存 schema 升为 2。旧候选身份不完整，恢复时会失效并重新生成；未引入不安全的旧记录迁移。更换 API key 本身不改变语义身份，已验证可继续复用候选。

**保证边界**：成功写入、可读取且请求身份匹配的成功候选，在已验证的进程终止/恢复窗口内不重发；authoritative cursor 避免重复消费已提交批次。远端已完成但客户端未收到、响应尚未暂存、写入失败、候选损坏/失效或 SDK retry 不属于 exactly-once 保证。新增 file fsync 不会把既有 snapshot→confirmation 协议升级为完整主机断电保证；目录同步和 authoritative snapshot 的断电持久性仍未验证。

相关代码：[`annotation_staging.py`](../../folith/annotation_staging.py)、[`core.py`](../../core.py)。

## 3. C2/C4 裁定与回退

| 模式 | 当前用途 | 活动请求 / 未提交任务上限 |
|---|---|---|
| `c2` | **新任务默认**；既有未配置任务也使用它 | 2 / 2（仅一个 worker 时更小） |
| `c4` | 显式 opt-in 的实验候选 | 2 / 4 |
| `ordered` | 明确回退到原有按序等待、提交后补队的算法 | 2 / 2 |

内部 runtime 参数为 `annotation_scheduler`，合法值仅 `c2`、`c4`、`ordered`。它在 pipeline_config 持久化；续跑沿用已保存选择，显式本次参数可覆盖保存值，以便实际回退。它属于 `INTERNAL_CORE_OPTIONS`，不增加共享页面/UI 分支，也没有为此新增用户配置面板。

在现有 worker 启动/恢复调用的参数字典中明确设置即可：

```python
pipeline_kwargs["annotation_scheduler"] = "ordered"  # 回退补队算法
# 或设置为 "c2"，回到默认候选窗口；显式 "c4" 才启用四项窗口。
```

`start_job_worker` / `resume_job` 继续透传现有参数；直接 `run_job_pipeline(..., annotation_scheduler="ordered")` 同样可用。保存为 C4 的任务若需要回退，须显式选择 C2/ordered，不能只省略参数。回退不改变请求身份、不丢弃有效候选、不重置 durable cursor。已验证 C4 在保存失败后切换到 C2/ordered，8 个成功批次仍各只请求一次。

`ordered` 回退补队算法时**保留暂存和提交确认保护**；持久化失败仍会停止，不提供静默绕过写入错误的模式。

**上线裁定**：本机无网络工程故障验证支持 C4 作为显式实验选项；**不批准替换生产默认，不声称跨平台全量生产验收通过**。真实 Windows 文件共享/终止行为、真实 provider 竞争及质量/计费尚未验收。默认继续 C2，待这些证据齐备后再讨论默认推广。

## 4. 验证结果

运行环境：macOS 27.0.1 arm64，CPython 3.11.3，使用现有 `/Users/xueyang/Dev/FolioThread/venv/bin/python`。导入 target checkout。新增测试与回放禁止 provider transport，未进行真实 API 实验。

**分组去重：604 passed，4 skipped，0 当前失败。** 其中 57 项为本轮新增修复回归，547 项为既有相关回归；共有 608 个唯一收集项，见 [测试选择 JSON](phase3-remediation-evidence/test-selection.json)。没有把重复执行相同测试计入通过数。

主要执行记录：

- 第一组 readiness/telemetry 验证：65 passed。
- 暂存/恢复/调度/合同分组：135 passed（含当时的 51 项新增测试）；现有部分 84 项，与下组不重叠。
- 扩展 25 文件回归：463 passed，4 skipped，68.82 秒。覆盖 batch/settings、request/transport、journal/persistence、delivery、reasoning/trace、Qt、translation core/evidence 及 atomic-file 分支。
- 最新修复回归及原 readiness：68 passed，2.80 秒（57 项新增 +11 项原 readiness）。额外回归覆盖版本 0→1、真实 partial pipeline、SIGKILL、C4→C2/ordered 恢复和模拟 Windows 替换重试。

新增故障矩阵在 C2、C4、ordered 各运行正常、parse failure、worker exception、暂存失败、state save 失败、取消、confirmation 中断；记录 queued、活动请求和 commit 事件，检查有界容量、提交顺序和恢复游标。暂停前不补队，恢复后正常结束；成功候选在已验证窗口无重复模型调用。既有标注质量/串行对照测试继续逐字段检查全局过滤及结果等价。

4 项跳过均要求真实 Windows sharing / ReplaceFileW / snapshot 语义。macOS 上的模拟 Windows 重试通过，只证明分支逻辑，不冒充 Windows 实机验收。警告为已有 SWIG deprecation 与 Qt signal disconnect；没有将其当成新产品缺陷。

原 `phase3-independent-evidence/independent_audit_observations.py` 是修复前缺陷观察脚本，其 `test_defect_*` 刻意断言旧缺陷存在；修复后不作为当前通过标准，也没有删除以掩盖原始审计。当前正确行为由 `tests/phase3_acceptance_fixes_test.py` 验证。

## 5. 修复后性能回放

新增 runner：[phase3_scheduler_validation.py](../../scripts/phase3_scheduler_validation.py)。原基线 stage/scheduler 从集成 commit 的真实 AST 提取；其余共享未改过滤逻辑。provider 返回相同合成候选，实际执行临时目录的 state/staging IO；runtime 显示写入替换为测试替身。保留并比较正常、提交失败、取消后的输出字段和恢复成本。

本轮 48 个回放记录：36 个合成正常/磁盘失败/取消记录（每策略/故障 3 次），12 个缩放历史记录（4 策略各 3 次）。36 个合成场景最终 annotation、batch status、done/cursor 与基线逐字段相同。全部已观察网络并发 ≤2，C2/ordered 的 outstanding ≤2，C4 ≤4。原始时间线及数字见 [benchmark-results.json](phase3-remediation-evidence/benchmark-results.json.gz)。

| 策略 | 历史 93 批服务时间缩放 1/1000：墙钟均值 ± 样本 SD | 平均 worker 服务利用率 | 平均暂存写入时间 |
|---|---|---|---|
| 基线 | 5.6958 ±0.0089 s | 83.3% | 0 |
| 默认 C2 | 5.7365 ±0.0118 s | 82.7% | 0.0786 s |
| 显式 C4 | 5.0256 ±0.0150 s | 94.5% | 0.0904 s |
| ordered 回退 | 5.7620 ±0.0268 s | 82.4% | 0.0844 s |

显式 C4 在此固定服务时间模型下约比基线快 **11.8%**；默认 C2 没有调度提速承诺，新增保护有本地 IO 成本。缩放后持久化的相对开销被放大；这些数字不能外推为真实长文档生产速度。原始历史服务时间来源仍是既有脱敏研究数据，不是本轮重新采集的 provider trace。

8 批合成故障回放：首次 state save 失败后，基线总请求 10 次（2 重复），三个候选策略均 8 次（0 重复）；取消后基线 9 次（1 重复），候选均 8 次。失败请求和写入前丢失的响应不被纳入“已成功暂存零重复”。

## 6. 复现与后续 PR 边界

```sh
cd /Users/xueyang/Dev/Folith-phase3
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest -q tests/phase3_acceptance_fixes_test.py tests/readiness_telemetry_test.py
/Users/xueyang/Dev/FolioThread/venv/bin/python scripts/phase3_scheduler_validation.py
```

全部分组的去重文件列表在测试选择 JSON；重放会更新修复后 evidence，不覆盖修复前审计数据。`git diff --check` 通过。

建议审查顺序与本轮实施顺序一致：

1. Readiness、真实 operation 归因、受门禁约束的里程碑和对应测试。
2. 暂存请求身份、成功候选恢复、写入确认和临时文件生命周期。
3. 默认 C2、显式 C4、ordered 回退合同及故障/性能证据。

本轮尚未创建 commit/PR，原有未提交修改和新增文件都保留在原工作区。若拆 PR，需要连同相应新增模块和测试一起纳入，不能只提交 tracked diff。全套仓库测试、真实 Windows、主机断电、真实 provider A/B 和生产性能仍未验证；完整 Phase 3 仍未完成。
