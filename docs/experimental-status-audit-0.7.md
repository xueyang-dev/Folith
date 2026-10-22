# 0.7 实验/冻结/历史项状态审计（#19–#23）

日期：2026-10-19。基线：`main` @ `815133c`。
范围：仅审计并订正文档，不改动运行时行为、默认值或数据格式。

本文件把 milestone 1（0.7）中五个「收敛/冻结/历史」类 issue 逐条对照代码与测试，
给出可复核的证据（`文件:行号`）和本次订正。任何一条如果证据不足，明确标为未满足，
不代为宣称完成。

## 结论一览

| Issue | 主题 | 判定 | 依据 |
| --- | --- | --- | --- |
| #19 | reviewer-overlap 边界 | 已满足（文档+测试+默认锁定） | `core.py:4398`、`docs/reviewer-overlap-pipeline.md:31-54` |
| #20 | 风险选择性 review 策略 | **未收敛**：`risk_selective` 会跳过非 TM 的干净批；需保留决定 | `folith/review_gate.py:119-136`、`tests/review_gate_test.py:253-272`、`docs/review-efficiency-pass.md:56` |
| #21 | Dynamic Context v2 审计 | 已满足（ADR+冻结测试+无 UI 暴露） | `docs/dynamic-context-v2-adr.md:3`、`tests/context_mode_freeze_test.py` |
| #22 | Paddle 微调历史核查 | 已满足（横幅+依赖组已移除），补一处内联标记 | `docs/paddle-arabic-finetuning.md:3-4`、`pyproject.toml` 无 `paddle` extra |
| #23 | 并发文档口径统一 | 需订正文档（本次已改） | `core.py:2563`、`job_config.py:80-83`、`task_setup.py:62-83` |

---

## #19 — reviewer-overlap 边界

**边界结论**：审校重叠**不是**一个纯粹的吞吐开关，它是「使用 provisional generated 语境
的新语义模式」，因此只允许 opt-in，生产默认关闭。

证据：

* 默认关闭：`core.py:4398` `DEFAULT_REVIEW_OVERLAP = False`；策略默认 `generated`
  （`core.py:4400`）。
* opt-in 表面（显式参数 / `pipeline_config` / 环境变量，三级解析，缺省 False）：
  `core.review_overlap_enabled()`（`core.py:4403-4422`）。
* 语义等价性已证伪、Strategy A 与 B 的准确边界、生产决定与默认值锁定：
  `docs/reviewer-overlap-pipeline.md:20-54`；逐批证据
  `docs/reviewer-overlap-semantics.md`、`docs/reviewer-overlap-semantics-evidence.md`。
* 回归锁定：`tests/reviewer_overlap_semantic_equivalence_test.py`
  （含生产默认禁用断言，`docs/reviewer-overlap-pipeline.md:42` 具名引用）。

验收对照：opt-in 表面显式且默认关闭 → 满足；语义契约与有界漂移已文档化 → 满足；
默认值与 A/B 边界有回归锁定 → 满足。**无残留项。**

## #20 — 风险选择性 review 策略

**结论（修正）**：`risk_selective` **不是**「只跳过可证安全批次」。任何没有确定性风险信号的
批次——包括全新的非 TM 译文——都会被记为 `skipped_low_risk`
（reason = `clean_deterministic`），不送审。`all_from_tm` 只是唯一「按构造无假阴性」的
子集，不是唯一的跳过子集。因此 #20 尚不能宣称收敛，需要一份明确的保留决定，承认已测出的
漏检风险。

证据：

* 门禁返回干净跳过：`folith/review_gate.py:113-136`
  ——锁定 TM 特例后，遍历风险规则，全部未命中即返回 `(False, "clean_deterministic")`；
  `all_from_tm` 在 `review_gate.py:169` 计算，只是 `:119-123` 的提前返回。
* 集成测试明确验证非 TM 干净批被跳过：`tests/review_gate_test.py:253-272`
  ——4 个新译文批次全部 `skipped_low_risk`，`review_calls == 0`。
* 已测出的漏检风险：`docs/review-efficiency-pass.md:56` 记录「selective review,
  deterministic gate only」在真实语料中 **would miss every serious finding**；
  §K（`:299-317`）说明 `all_from_tm` 是唯一按构造无假阴性的跳过。
* 跳过 provenance 仍然可靠：跳过记为 `skipped_low_risk`（`review_gate.py:20-26,39`），
  不标记 reviewed、不进入 TM；`tests/review_gate_test.py:263-266` 断言这一点。
* 模式解析已文档化：`review_gate.py:54-77`（显式 > `pipeline_config` > `state`），
  未知值回退 `always`，生产默认 `always`（`:35`）。

**判定**：保留 `always` 为默认这一项已满足；但选择性模式的实验性跳过仍然存在且存在已测
漏检风险。本审计**不**将 #20 标为已收敛。若要关闭 #20，必须在 issue 中明确记录
「保留该实验、接受已测漏检风险」的决定与可接受跳过范围；否则保持 open。

**订正**：issue 正文引用了 `tests/review_defect_recall.py`，仓库里不存在该测试文件；
注入缺陷的确定性 vs 语义回收率由保证脚本 `scripts/review_defect_recall.py` 提供
（`docs/review-efficiency-pass.md:14,47`）。本审计记录该路径引用错误，脚本本身存在。

## #21 — Dynamic Context v2 审计

**结论**：已冻结，`fixed` 为生产且唯一默认，`dynamic_v2` 只能被显式精确值触达，无 UI 暴露，
实验资产保留。

证据：

* ADR 状态 Accepted — FROZEN：`docs/dynamic-context-v2-adr.md:3`；生产决定
  `context_mode = "fixed"` 与 fail-safe 语义：同文件 `16-30`；完整证据
  `docs/dynamic-context-v2-report.md`、`docs/dynamic-context-v2-interface.md`、
  `docs/dynamic-context-v2-evidence/`。
* 解析器：`folith/dynamic_context.py:1610 resolve_context_mode()`（`argument > state > 生产默认`，
  只有精确 `"dynamic_v2"` 可达实验）；实验常量 `dynamic_context.py:55`。
* 冻结回归：`tests/context_mode_freeze_test.py` 覆盖生产默认与实验状态显式、
  未知/畸形模式 fail-safe 到 `fixed`、`fixed` 不调用动态选择器、
  `fixed` 下有无实验代码提示词逐字节一致、`fixed` 不写实验状态键、
  恢复保留已记录模式、实验资产保留。
* 无 UI 暴露：`folith/application`、`folith/qt` 中无 `dynamic_v2` 引用（全仓 grep 为空）。

**无残留项**；无需订正文档（现有 ADR/report 未含过期待办措辞）。

## #22 — Paddle 阿拉伯语微调历史核查

**结论**：微调实验已结束，依赖组、本地环境、模型与实验产物均已清理；历史结论保留为记录，
本地产物标注为不可用。

证据：

* 结束横幅：`docs/paddle-arabic-finetuning.md:3-4`（实验已停止、产物已删除、
  `paddle` 安装依赖组已移除）；`docs/ocr-subsystem.md:3-4`、`docs/ocr-sources.md:3` 同口径。
* 依赖组已移除：`pyproject.toml` 中不存在 `paddle` extra（grep 为空）；
  `docs/ocr-subsystem.md:125-127` 明确 `pip install '.[paddle]'` 不再可用。
* `FOLITH_OCR_MODEL_DIR` 保留的公开用途仅指向**官方**模型资源位置
  （`docs/ocr-subsystem.md:159-160`），非本书微调。
* 本地产物路径（`benchmark/paddle-models/alam-al-din-finetuned/`）在
  `docs/paddle-arabic-finetuning.md:111-119` 仍以「自包含模型目录」语气出现。

**订正**：在 `docs/paddle-arabic-finetuning.md` 该模型目录段与 OCR 引擎选择段补齐内联
「本地产物已不可用 / Paddle 手动选择已不再是当前产品路径」标记，使读者即使不读文件头
横幅也能得到正确结论。

## #23 — 同步并发文档口径统一

**结论**：历史实验结论（吞吐提升与连续性上下文代价）对当前实现**仍然成立**，但文档把
「核心回退值 = 1」误写成「系统默认并发 = 1」。需要区分三层数值，并说明预设对连续性代价
的取舍是已知且有意为之的。

2026-10-22 增补：[五档、每档 801 批的当前审计](stability-milestone-4/concurrency-audit.md)
记录了新的吞吐/上下文数据与限制；下方旧的 3/15、5/15 仅为历史样本。
Qt 提示已去掉“并发不改变译文质量”的无依据承诺，改为说明速度与连续性的取舍。

三层数值（均可复核）：

| 层 | 位置 | 值 | 含义 |
| --- | --- | ---: | --- |
| core 回退值 | `core.py:2503` | 1 | 调用方**未提供**并发时的串行、连续性精确路径 |
| 应用共享契约默认 | `folith/application/job_config.py:80-83` | 4 | 两个前端共用的默认，契约总是显式下发该值 |
| 策略预设 | `folith/application/task_setup.py:62-83` | 快速 6 / 标准 4 / 学术增强 4 | 新建任务的模板值，高级设置可覆盖 |

历史测量（并发=1 基线）：

* 并发 2/3 提速 1.50× / 1.53×，译文真值一致，但 3/15、5/15 个批次看到的
  `previous_target`（连续性上下文）与串行不同：`docs/translation-throughput.md:11-14`；
  复现脚本 `scripts/translation_concurrency_benchmark.py`
  （`docs/performance-hardening-round2.md:424-425`）。
* `docs/performance-hardening-round2.md:184-190` 同结论，并写「`DEFAULT_TRANSLATION_CONCURRENCY`
  保持 1」——该句是**回退值**，不是预设值。
* 预设改用更高并发是后续的显式产品决定（P7 模式梯度）：
  `docs/qt-beta/acceptance-feedback-2026-10-14.md:777`（快速 = 均衡批次 + 并发 6 + 关闭每批知识反馈）。

**订正**：在 `docs/translation-throughput.md` 与 `docs/performance-hardening-round2.md`
的并发结论处加统一口径说明，区分「回退值 1 / 契约默认 4 / 预设 6-4-4」，并明确预设对
连续性上下文的影响是已知取舍。

### 2026-10-22 补测（关闭本条的最后一块缺口）

原状态是「4 / 6 档未测，结论靠 1/2/3 外推」。现已在宿主环境补测
`--concurrency 1,2,3,4,6,8 --review --repeat 3`（120 段 / 6 section / 50 ms 假 LLM），
并加跑一组 200 ms 延迟对照：

| 并发 | 50 ms 延迟墙钟 | 提速 | 200 ms 延迟墙钟 | 提速 | `previous_target` 分歧 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 8.04 s | 1.00× | 16.18 s | 1.00× | 0/30 |
| 2 | 4.27 s | 1.88× | 7.53 s | 2.15× | 1/30 |
| 3 | 4.01 s | 2.00× | — | — | 3/30 |
| 4 | 3.95 s | 2.03× | 4.39 s | 3.69× | 5/30 |
| 6 | 4.00 s | 2.01× | — | — | 5/30 |
| 8 | 4.05 s | 1.98× | 3.01 s | 5.38× | 8/30 |

因此本条可以关闭，依据改成实测而非外推：4 与 6 实际同档（差值在样本极差内），
预设之间真正的差别不在并发数；50 ms 下的平台是测量装置下限（固定簿记 + 串行写盘），
延迟提高后并发继续线性收益；连续性代价随档位单调上升，控件提示「不改变译文质量」
不成立，已改。

同时修掉基准脚本的一个口径错误：它把阶段 span 当成本报告，导致各阶段读数恒等于整轮
长度（"生成 4.184 s / 审校 4.184 s"即由此而来）。现在用 `interval_union_s`；
审校可重叠比例按正确口径重算为 40–44%。

---

## 本次订正的文档

1. 本文件。
2. `docs/translation-throughput.md`：并发结论加三层数值与预设取舍说明。
3. `docs/performance-hardening-round2.md`：`DEFAULT_TRANSLATION_CONCURRENCY 保持 1` 处
   加同口径脚注。
4. `docs/paddle-arabic-finetuning.md`：本地模型目录与引擎选择段加「已不可用」内联标记。
5. `docs/ocr-subsystem.md`：Paddle 手动选择说明段加「已不再是当前产品路径」内联标记。

## 未纳入本次改动（需实现或需外部验证）

* **#20**：选择性模式会跳过非 TM 的干净批，存在已测漏检风险；需明确保留决定，保持 open。
* **#18** 自适应 OCR DPI keep-or-drop：结论为 DROP，已在独立 PR 中移除。
* **#15 / #16 / #17**：需要语料重跑、真实模型/Word/EPUB、独立语言人工签核或
  非 macOS 平台验收，本环境无法诚实满足，保持 open。
