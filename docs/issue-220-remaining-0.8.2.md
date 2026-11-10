# #220 剩余项（0.8.2）：D2 / C1 / F1 / F2

本文件记录 `#220`（thinking-off 试用）剩余四项的落地口径与**明确未做**的部分。
A3、C2、C3、D3、E1、E2、B1 已在 #300 / #303 合入，B5 需要真实视觉模型复现，
本轮不做（见文末）。

## D2 · 语言无关的「部分残留」检测

**规则**：部分残留 = 译文里仍留有**源文有、目标语言不写**的书写系统。判据只有
两条事实，且都来自仓库既有机制：

1. **这个字符属于哪种书写系统** — 由字符自己的 Unicode 名称决定
   （`HIRAGANA LETTER A` → kana，`CJK UNIFIED IDEOGRAPH-6F22` → han，
   `ARMENIAN CAPITAL LETTER AYB` → armenian）。因此没有第二张「脚本 → 码点区间」
   表：从未列过的语言/文字（亚美尼亚、格鲁吉亚、孟加拉、泰米尔……）同样被识别。
2. **目标语言使用哪些书写系统** — 目标语言先经 `folith.ocr_language`（仓库唯一的
   语言注册表：标签、别名、语言代码都在里面）解析成注册表键，再由
   `folith/script_leak.py` 的一张 `语言键 → 书写系统` 表回答。给语言加一行注册表
   条目、加一行脚本表即可；`tests/script_leak_test.py` 会对「注册表里有、脚本表里
   没有」直接失败，不会静默漏检。

判定发生在源文写作系统 ∩ 目标不允许的写作系统上，所以**与语言对无关**：
日文源里的假名留在中文译文里、中文源里的汉字留在英文译文里、西里尔字母留在英文
译文里，都是同一个检查。输出沿用既有 finding 结构（`category` =
`translation_completion`，`kind` = `script_leak`，`severity` = `actionable`），
**不自动改译文**；真正改文本的是下一节的修复候选流程。

### 可证明的安全（不把正常保留判成残留）

按顺序应用，全部是"只在证明得了时才报"的方向：

1. **拉丁字母在任何目标语言里都允许**：人名、品牌、缩写、URL 不翻译，哪怕目标是
   中文、日文或阿拉伯文。
2. **源文里没有的书写系统不算残留**：只在源文出现过的脚本上做判定。
3. **目标语言未知就不检查**：`allowed_scripts()` 返回 `None` 时静默跳过（与既有
   "证明不了的语言不猜" 一致）；注册表里知道、但脚本表没有对应行同样返回 `None`
   （宁可漏检，也不猜错）。
4. **引号 / 括号内的内容是"有意保留的原文"**：只统计引号外的字母；整段残留都在
   引号内时不报。这是**刻意接受的漏报**——误报的代价是自动重译一段本来就对的
   英文/中文，比漏一条更贵。
5. 只有**明确成对**的引号/括号才参与判定（`「」『』«»“”‘’《》〈〉【】()（）[]〔〕`），
   且双引号只在成对出现时才算引号；撇号（`don't`）从不当作引号，避免一个撇号
   把整段后续文字都吞进"引号内"。

### 已知边界（不假装覆盖）

- **拉丁字母语言之间**（德语留在英语译文里）脚本层无法判断：两者都用 latin。
  这类需要语言识别，不在本项范围。
- **专名/标题**保留：引号规则只覆盖"引号里的原文"。冻结术语表里 `behavior=preserve`
  的保留项由既有 triage 保留策略（`folith/finding_triage.py` 的
  `RETENTION_POLICY`）标记为 `verified_safe`，这是"标记为保留"的既有入口。
  *若要在这条 finding 生成时就直接静默*，需要把 `check_translation_batch` 的
  glossary 参数传进 `leaked_scripts`（`core.py` 约 3 行）；本轮按边界要求未改
  `core.py`，留作后续。

### 决策：copy-through 类 finding 是否走已有 repair pass

**结论：接（保持现状，不新增机制）。** 现状是 `actionable` 已经进入确定性修复候选
流程，读代码确认这条路径是安全的：

- 触发条件是 `folith/review_gate.py` 使用的同一批 findings 中
  `severity ∈ {blocking, actionable}`，且每批不超过 8 条（`core.py` 的 `fixable`
  长度判断）——不会无限重译；
- 修复不直接写正式译文：`folith/repair.py` 先建 **shadow 候选**（`create_overlay`，
  `candidate_hash`/`input_hash` 绑定输入与候选），只有 `evaluate_overlay` 判定
  `accepted` 才 `promoted_targets` 生效；任一哈希不符、shadow 上仍有严重 finding、
  盲审失败或与盲审身份不符，都记为 `rejected` 并保留正式译文；
- 只对 `not p["from_tm"]` 的段落生效：TM 精确命中（`from_tm=True`，已审校）不会被
  覆盖；生成阶段批内没有人工译文（人工编辑发生在生成之后），被替换的只可能是刚
  生成的候选或 `tm_suggested` 建议；
- 每次 overlay 进 `repair_overlays_to_record` 审计，`reviewed` 不会被标成已审，
  仍需过审校门禁。

因此不需要第二套机制，也不需要把 copy-through 排除在修复之外；本项只是把这条
边界和它的安全前提写下来，并用 `tests/script_leak_test.py` 锁住
「finding 仍是 actionable、仍带 `kind=script_leak`」。

## C1 · 视觉 502 与并发默认

**现象**（#220 C1 实测）：思考开启时，读一页在约 **110 s** 后返回 HTTP 502，而
客户端超时是 **180 s**——网关先放弃，客户端还在等。并发越高，多路读页请求同时排
在网关后面，越容易一起撞上限（首批 5 页里 4 页失败、任务 5 分钟无进展）。

**改动**（`folith/engine/ocr_config.py`，默认值与提示，语义不变）：

| 项 | 值 | 说明 |
| --- | --- | --- |
| `VISION_PAGE_WORKERS_DEFAULT` | **3** | 官方端点 |
| `VISION_PAGE_WORKERS_RELAY_DEFAULT` | **2** | 自定义 relay / 网关（`vision_connection_is_relay`：未登记服务商，或已登记但 base_url 被改到别处） |
| `VISION_PAGE_WORKERS_MAX` | 8 | 上限不变 |
| 覆盖顺序 | 显式参数 > 现有的页并发环境变量（见 `folith/document_understanding.page_worker_count()`）> 按连接的默认值 | 原有语义不变；显式值仍夹紧在 1..8 |
| `VISION_GATEWAY_TIMEOUT_HINT` | — | "网关超时早于客户端超时"的诊断提示，含 502 / 110 s / 180 s / 降并发建议 |

`folith/document_understanding.page_worker_count()` 现在按 provider / base_url
分档（读页入口把连接的 `provider` / `base_url` 传进去）；不知道连接时用保守档。
**客户端超时（180 s）与重试默认值（2 次）没有改**，`tests/vision_concurrency_test.py`
同时锁住"新默认值可覆盖"和"旧超时/重试值不变"。

## F1 · `test_a_finished_run_leaves_the_header`

**已绿，无需修改。** 在 `origin/main`（`8109b9c2`）上跑：

```
pytest tests/qt_design_review_p0_test.py::test_a_finished_run_leaves_the_header -q
-> 1 passed
```

问题单里记录的英文断言（`'Done'` vs `'Current progress has been saved; you can
continue.'`）在当前树里已不存在；该测试的现断言（"已完成" + 进度条隐藏）由
`3ed0ab50`（fix(qt): stop the workbench saying the wrong thing (design review P0)）
引入，正是为这条反馈加的，后续 i18n 提交把文案统一到中文。当前树上两者一致。

## F2 · 系统 `python3` 下的导入失败

**做法：在 `tests/conftest.py` 里干净地跳过**（不是只写"必须用 venv"）。

- `tests/optional_deps.py` 用 AST 判断一个测试模块是否在**模块级无条件**导入了
  `PySide6` / `typst` 依赖的模块（`PySide6`、`folith.qt*`、`qt_client`；
  `typst`、`folith.pdfrender*`）；
- 只有这种模块被 `collect_ignore`：系统 `python3` 下从 **35 个 collection error**
  变成 **0 error**，其余 3859 条测试照常收集运行；跳过条数在 pytest header 里显示
  一行（"跳过 N 个缺少可选依赖的测试模块"）；
- 模块**自己已经处理缺失**时不跳过：`pytest.importorskip("PySide6")`（且位于受保护
  import 之前）会让 pytest 正常报 skipped；`try: import … except ImportError` 与
  `if TYPE_CHECKING:` 同理；只在函数内 import Qt 的模块照常收集并运行其非 Qt 测试。
- `venv` 下两个依赖都在，`collect_ignore` 为空——CI 与开发者行为完全不变
  （`venv` 收集数 5573 → 5578，新增的正是本文件相关的 5 条测试）。

日常仍建议用 `venv/bin/python` 跑全量；系统 `python3` 现在至少不会因为 35 个模块
而整轮失败。`tests/optional_deps_test.py` 通过注入"已安装依赖集合"来验证判定规则，
两个解释器下都能跑。

## B5 · 未做（需要真实视觉模型）

B5 是"坏回复长什么样"的复现研究（表格页 JSON 断裂、混入外语 token、`Page number: N`
幻觉、8,192 token 截断），需要真实网关 + 真实表格页重跑并保留原始回复；本轮没有
可用额度/样本，因此不做，避免用编造样例写测试。开始条件：一次真实运行的
`document_understanding/failures.jsonl` + 失败页的原始回复片段（B3 已按 4,000 字符
落盘），以及 #228 的建议（容忍坏表格、保留页面）。
