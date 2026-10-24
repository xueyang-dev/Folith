# Milestone 5 · 候选契约与完整性验证记录

日期：2026-10-24。对应方案：Milestone 5 开发指导方案（`milestone-5-development-guide-2026-10-24.md`）。
基线记录：[milestone-5-baseline-record-2026-10-24.md](./milestone-5-baseline-record-2026-10-24.md)。

本文件记录 #92 与 #94 的实现口径与证据，供评审对照；行为细节以测试为准。

## 1. 四条写入链的统一口径

基线上四条链各自为政（详见基线记录第 2 节）。现在的分工：

| 入口 | 生成 | 提交 | 来源标记 |
| --- | --- | --- | --- |
| 定点重译 | `delivery.retranslate_segments`（锁外调模型） | `adopt_translation_candidate` | `targeted_retranslate` |
| 按要求重译 | 同上，带 `instruction` | 同上 | `instructed_retranslate` |
| 采用审校建议 | `adapter.apply_suggested_translation` | 同上 | `review_suggestion`（带 `finding_ids`） |
| 批量查找替换 | `adapter.stage_batch_replacement` | `adopt_translation_candidates` | `batch_revision` |

共同约束：生成不改正文、不动发现项、不写人工决定、不升文本版本；提交是唯一写入口，
锁内重读、按 UID 定位、校验基线、幂等、每次采用只升一次版本、只留一条候选维度的审计记录。

## 2. 批量提交策略（#92 §3.4）

**逐项返回 + 单次原子快照。** 结果为 `committed` / `skipped`（含冲突码与原因）；
一条过期或受保护的段落不会阻塞其它段落。落盘仍然是一次 `save_job_state`，
所以磁盘上不会出现"前几段成功、后面的没写"的半批状态；保存失败时整批都不生效
（`test_a_failed_save_leaves_the_batch_unwritten`）。

批量默认**跳过人工改写/人工确认过的段落**并在结果里说明原因；显式选择"同时替换"
才覆盖，覆盖会记在候选的 `overrode_protection` 上。逐段采用仍可覆盖，但界面先说明代价。

## 3. 采用前后的状态契约（#94）

- **身份与过期**：UID 优先，索引仅在原文仍匹配时使用；原文、译文、审校/接受标记
  任一变化都判定过期；段落拆分/合并/删除/排除后旧候选不能落到新位置。
- **失效范围**：被改段落的审校证据失效、接受标记清空、TM 条目撤销，文档级 final /
  delivery approval 复位；未涉及的段落保留证据与 TM。审校事件的依赖集合仍按既有
  依赖图决定（审校时看过整本书的段落，会因为整本书的改动而失效——这是既有语义）。
- **撤销**：恢复为一次新的正文变更（`translation_restore`），保留原采用记录；
  `reviewed` / `review_status` / `from_tm` **不恢复**，TM 资格必须重新取得。
- **并发**：重复点击与同批重复 ID 只产生一次变更；同一段两个候选只有一个能采用
  （后生成的胜出，旧候选在生成时已标记过期）；worker 持锁时采用被拒绝；
  取锁失败不落任何状态。
- **中断**：保存失败不留下正文、决定或版本变更；journal 游标不落后于已保存的快照。
- **旧任务**：无候选字段时正常打开，首次写候选补足段落身份；没有基线依据的旧候选
  一律拒绝，不猜。

## 4. 证据

| 范围 | 结果 |
| --- | --- |
| `tests/review_batch_candidate_test.py`（新，10 例） | 通过 |
| `tests/revision_integrity_test.py`（新，14 例） | 通过 |
| 候选/审校/右栏/适配器/i18n/品牌/术语/交付/phase-3/恢复回归 | 297 passed |
| TM 失效、段落生命周期、并发锁、journal、交付快照、审校批次 | 168 passed |
| 界面文案覆盖率 | 1392/1392 × 5，`check_ui_locales.py` 0 错误 |

## 5. 仍未完成

- **#49**：缺真实 provider 连接与原 301–400 页 PDF，画像阶段未做真实验证；保持待验证。
- **#83**：缺 49 页任务与原生 Windows/macOS 客户端复现。代码层面已确认
  `segment_pdf_location` 在"持久化锚点不在当前页"时会使用该段第一个有效锚点，
  没有有效锚点才回退文本搜索；延迟渲染的代次问题尚无复现证据，因此未改。
