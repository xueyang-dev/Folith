# 长文档持久化性能（第五轮）：把"环境口径"从猜测变成测量

基线：第四轮之后的**工作树**（未改一行生产代码）。日期：2026-09-25。

> 与第四轮报告的关系：第四轮在「下一轮最值得做的一项」里点名**事件日志的追加次数**，
> 但加了一个硬门槛——**"必须先拿到真实 provider / 真实磁盘的复测数据再动"**，
> 理由是"调用次数主导"的阶段会被本环境的 IO 代理严重放大。
> 本轮做的就是这件事：**先把口径量准，再决定动不动。**
> 结论是**不动**，并且量出了真正的头号成本。

---

## Final verdict

**PASS（本轮的正确结论是"不优化"）**

- **事件日志：NOT JUSTIFIED BY DATA。** 生产口径下它只占整轮的 **4.78%**（5000 段：
  0.526s / 11.016s），不是第三轮测出的 63.51%。第四轮设的门槛是对的——
  在没有真实口径之前动它，会去优化一个不存在的问题。
- **真正的头号成本换人了：`state.json` 的 JSON 序列化，3.41–3.62s，占整轮 ~30%。**
  它是**纯 CPU、按字节数增长**的成本，因此**在生产环境里同样成立**，不是环境产物。
- **载荷分解**：`state.json` 10.49MB 里 **`review_evidence` 占 57.4%**，
  而其中 **`dependency_truth` + `review_truth` 合计 4.31MB = 全文件的 41.1%**，
  内容是**已审校段落原文/译文的整份拷贝**（`pairs`/`paras` 里已经有一份）。
- **消费方审计**：这两个字段在全仓库只被**一处**读取，且只做**相等比较**
  （`reconcile_runtime_review_truth`）——不做展示、不进交付物。
- **本轮不改任何生产代码。** 唯一有意义的杠杆是上面那 41.1% 的**表示形式**
  （文本 → 摘要），那是**持久化 schema 的改动**，且被用例逐字段断言，
  应当独立一轮做，而不是塞进测量轮。

---

## 方法：把沙箱 IO 代理费和真实系统调用成本分开

本环境的 Python 进程会被注入一个 `sitecustomize` shim（通过 `PYTHONPATH`
指向 `.../cli/vendor/shim/sitecustomize.py`），它把所有文件操作代理给宿主 IO broker，
**每次操作收一笔与大小无关的固定费用**。这正是第三轮把事件日志算成"整轮 63.5%"的原因。

关键性质：**这个 shim 只通过 `PYTHONPATH` 生效。** 清掉它，同一个脚本就在
**本机真实的本地文件系统**上跑——对"磁盘那部分"来说这就是生产口径。

```bash
PYTHONPATH="$PYTHONPATH" venv/bin/python tmp/phase5/io_toll.py --label shimmed
env -u PYTHONPATH        venv/bin/python tmp/phase5/io_toll.py --label raw
```

两次运行使用**同一解释器、同一目录、同一份代码**，差别只有 shim。
（已验证：`env -u PYTHONPATH` 下 `importlib.util.find_spec('sitecustomize')` 为 `None`。）

### 单次操作成本（中位数，µs）

| 操作 | 带 shim | 去掉 shim（真实） | 代理费倍数 |
| --- | ---: | ---: | ---: |
| `json.dumps(一个事件)` | 1.25 | 1.25 | **1×**（纯 Python，不经 IO） |
| `stat()` | 1.63 | 1.58 | **1×**（不经代理） |
| `mkdir(exist_ok=True)`（目录已存在） | 6,660.96 | **2.96** | **2,251×** |
| `open("a")+write+close` | 6,765.19 | 22.54 | 300× |
| `open+write+flush+close` | 6,703.90 | 21.81 | 307× |
| `open+write+flush+fsync+close` | 6,647.19 | **34.10** | 195× |
| **`checkpoint.append_event`（完整）** | **13,411.83** | **44.60** | **301×** |

两条只有在这个测量里才看得见的结论：

1. **`fsync` 在 shim 下完全不收费**：带 shim 时"flush"6,703.90µs 与
   "flush+fsync"6,647.19µs 之差是**负的**（纯噪声）。也就是说，
   **shim 根本没有建模生产环境里唯一真正重要的那笔成本**——落盘。
   去掉 shim 后，`fsync` 的真实代价是 **34.10 − 21.81 = 12.29µs**。
   （顺带记录：macOS 的 `fsync(2)` 只把数据推到设备缓存，不等物理落盘；
   这是既有行为，本轮不改。）
2. **`stat` 完全不经过代理**（1.63µs vs 1.58µs）。所以任何"先 `stat` 再决定"
   的写法在本环境里看起来是免费的——这会误导优化方向。

---

## 整轮成本：同一脚本，两种口径

`scripts/translation_persistence_audit.py --sizes 1000,5000 --batch-size 4`，
确定性假 LLM，`enable_review=True`，`use_tm=True`，concurrency=1。

| 5000 段 | 带 shim（第四轮当天） | 带 shim（本轮） | **去掉 shim（生产口径）** |
| --- | ---: | ---: | ---: |
| 整轮 wall | 54.09s | 172.01s | **11.23s / 11.77s** |
| `state_commit` | 6.44s | 12.59s | **4.64s / 4.85s** |
| `tm_commit` | 1.42s | 4.14s | **0.48s / 0.51s** |
| 写放大（`st_x` / `tm_x`） | 84.0× / 79.3× | 84.0× / 79.3× | **84.0× / 79.3×** |

**带 shim 的两个绝对值差了 3.2 倍**（54.09s vs 172.01s，同一份代码、同一台机器）。
这不是代码回归：`st_x`/`tm_x` 与全部不变量在两种口径下完全一致
（`tx=e4df2a30d8f773f6` / `a6383421880a8c41`）。结论很直接：

> **shim 的每次操作代理费在 5.3ms–24ms 之间随负载浮动，
> 因此带 shim 的绝对耗时不可用于排序**——它连自己和自己都不可复现。
> 排序只能用去掉 shim 的数字。

---

## 结论 1：事件日志 —— NOT JUSTIFIED BY DATA（不动）

| 指标（5000 段） | 第三轮（带 shim） | **本轮（生产口径）** |
| --- | ---: | ---: |
| `append_event` 调用次数 | 10,157 | **7,971**（6.38 次/批） |
| 每次平均 | 10.971ms | **0.066ms** |
| 累计 | 111.437s | **0.526s** |
| 占整轮 | **63.51%** | **4.78%** |
| 整轮 wall | 175.463s | **11.016s** |

- 调用次数从 8.13 次/批降到 **6.38 次/批**，这是**第四轮 TM 节奏改动**的副产品
  （`tm_promotion_pending` / `done` 从 2500 次降到 314 次），与事件日志本身无关。
- 每次成本 10.971ms → 0.066ms，**166×**，全部来自口径，不是代码。
- **它值不值得改？** 0.526s / 11.016s，而且这 11s 里用的是**零时延假 LLM**；
  真实 provider 下整轮是分钟级，事件日志会掉到 **<0.5%**。

**决定：不改。** 具体地：

- **不合并事件**：`generation_started` / `*_done` / `state_snapshot` 是
  `recovery_summary` 与 `reconcile_translation_memory` 的**恢复证据**，
  合并会改变崩溃语义。
- **不降 `fsync` 频率**：它现在只值 12.29µs/次 × 7971 ≈ **0.10s**，
  而它是唯一保证"事件已落盘"的东西。
- **连零风险的冗余 `mkdir` 也不改**：第四轮把 `append_event` 的每次 `mkdir`
  和 `save_tm` 的 `tm_prepare` 认定为"同一个 bug 的两个副本"——
  **形状判断是对的，量级判断错了**。在 `append_event` 里那笔 `mkdir`
  值 2.958µs/次 × 7971 ≈ **23.6ms = 整轮的 0.21%**。
  为零收益去动恢复证据路径，不符合"量不出收益的改动要主动撤回"。

> 这一条是第四轮门槛的直接兑现：**如果当时动手，就会去优化一个占 4.78% 的东西，
> 而漏掉占 30% 的那个。**

---

## 结论 2：真正的头号成本是 `state.json` 的序列化

去掉 shim 后，5000 段整轮 ~11.2–11.8s，`save_job_state` 的子阶段（157 次保存）：

| 子阶段 | 本轮运行 1 | 本轮运行 2 | 占整轮 | 口径 |
| --- | ---: | ---: | ---: | --- |
| **`json_serialization`** | **3.4055s** | **3.6236s** | **~30%** | **纯 CPU → 生产同样成立** |
| `review_truth_reconcile` | 0.6714s | 0.7335s | ~6% | 纯 CPU（**O(n²) 扫描**，见下） |
| `state_file_write` | 0.4616s | 0.3972s | ~4% | 字节主导 |
| `truth_marking` | 0.0918s | 0.0908s | 0.8% | 纯 CPU |
| `atomic_replace` | 0.0495s | 0.0459s | 0.4% | 字节无关 |
| `summary_write` | 0.0302s | 0.0292s | 0.3% | — |
| `tm_json_serialization` | 0.3611s | 0.4341s | ~3.5% | 纯 CPU |
| `tm_file_write` | 0.0876s | 0.0534s | <1% | 字节主导 |
| `tm_atomic_replace` | 0.0204s | 0.0198s | 0.2% | 字节无关 |
| `append_event`（另测） | 0.526s | — | 4.8% | 见结论 1 |
| **持久化合计** | **≈5.70s** | **≈5.55s** | **≈50%** | |

两次运行在 ~7% 以内一致，`json_serialization` 稳定在 **~30%**。

**这是本轮最重要的发现**：第四轮基于带 shim 的数据说
"持久化已经不再是翻译阶段的前二瓶颈"——**换成生产口径，这句话不成立**。
持久化占整轮约一半，而其中最大的一项是与字节数成正比的纯 CPU 序列化。

---

## 载荷分解：`state.json` 里到底是什么

`state.json` @ 5000 段 = **10.49MB**，按顶层键：

| 键 | 字节 | 占比 | 元素数 |
| --- | ---: | ---: | ---: |
| **`review_evidence`** | **6,024,255** | **57.4%** | 1250 |
| `pairs` | 2,863,891 | 27.3% | 5000 |
| `paras` | 628,891 | 6.0% | 5000 |
| `context_packet_log` | 474,278 | 4.5% | 1250 |
| `human_actions` | 277,641 | 2.6% | 1250 |
| `glossary_injection_log` | 211,113 | 2.0% | 1250 |

再拆 `review_evidence` 内部（6.02MB）：

| 字段 | 字节 | 占 `review_evidence` | 占整个 `state.json` |
| --- | ---: | ---: | ---: |
| `translation_core`（整体） | 5,090,788 | 84.6% | 48.5% |
| ├─ **`dependency_truth`** | **3,080,666** | **51.2%** | **29.4%** |
| ├─ **`review_truth`** | **1,229,030** | **20.4%** | **11.7%** |
| └─ 指纹 / 哈希（3 个） | 273,750 | 4.6% | 2.6% |
| `completion_receipt` | 320,140 | 5.3% | 3.1% |
| `review_event_id` | 56,530 | 0.9% | 0.5% |
| `rounds` | 51,250 | 0.9% | 0.5% |
| `requests` | 2,500 | 0.0% | 0.0% |

**`dependency_truth` + `review_truth` = 4.31MB = 整个 `state.json` 的 41.1%。**

它们的形状是：

```json
{"segment_id": 12, "source": "……原文整段……", "target": "……译文整段……",
 "target_checked": true, "target_from_accepted": false, "source_from_paragraphs": false}
```

也就是说：**已审校段落的原文与译文被整份拷贝进了审校证据，每批一份。**
而 `pairs`（2.86MB）和 `paras`（0.63MB）里已经存着同样的文本。

> 一个先被证伪的假设：我原以为 `rounds`（每轮 LLM 的完整轨迹，含证据工具调用的
> 全部返回载荷）是元凶——**实测只占 0.9%**，因为确定性假 LLM 不发起证据请求。
> 真实 provider 下 `requests` 可能显著更大，但**这需要真实口径的复测**，
> 不能拿假 LLM 的数字去推测。

---

## 消费方审计：那 41.1% 到底有没有人读

| 字段 | 读取者 | 用途 |
| --- | --- | --- |
| `dependency_truth` | `translation_evidence.reconcile_runtime_review_truth`（**唯一**） | 取 `source` / `target` 做 **`!=` 相等比较** |
| `review_truth` | 同上（`truths.extend(...)` 后同一个循环） | 同上 |
| `dependency_truth` / `review_truth` 的 `source` / `target` | **没有别的读取者** | 不进 UI、不进交付物 |
| `segment_id` / `target_checked` / `target_from_accepted` / `source_from_paragraphs` | 同上的循环 | 决定"比哪一边" |
| `requests` | `app.py:1778`、`app.py:4305`、`delivery.py:250` | UI 展示 + 交付物 |
| `rounds` | **全仓库无读取者**（含测试） | — |

关键代码（`folith/translation_evidence.py`）：

```python
current_source = str(...)                      # 当前的 pairs/paras 值
current_target = str(...)
source_changed = current_source != str(truth.get("source") or "")
target_changed = truth.get("target_checked", True) \
    and current_target != str(truth.get("target") or "")
```

**只做相等判断。** 这意味着"审校时看到的是什么"只需要一个**摘要**即可判定，
不需要保存全文——这正是下一轮的杠杆所在。

---

## 现在最大的瓶颈（生产口径重新排序）

**口径标签是这一节的重点**：没有标签的排序会把环境产物当成生产结论。

| # | 瓶颈 | 实测（5000 段） | 占整轮 | 口径 | 性质 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | **`state.json` 序列化** | 3.41–3.62s | **~30%** | **CPU / 字节主导 → 可信** | 载荷 41.1% 是重复文本 |
| 2 | `review_truth_reconcile` | 0.67–0.73s | ~6% | CPU → 可信 | **O(n²)**：每次保存全量扫 `review_evidence` |
| 3 | 事件日志 `append_event` | 0.526s | 4.8% | 调用次数主导 → **已证伪，不动** | 见结论 1 |
| 4 | `state_file_write` | 0.40–0.46s | ~4% | 字节主导 → 可信 | 会随 #1 的载荷缩减同比例下降 |
| 5 | `tm_json_serialization` | 0.36–0.43s | ~3.5% | CPU → 可信 | — |
| 6 | `truth_marking` | 0.09s | 0.8% | CPU → 可信 | — |
| 7 | `summary_write` + 两次 `atomic_replace` | 0.10s | ~0.9% | 字节无关 | 已被节奏压到边界 |

**#2 的 O(n²) 形状值得单独说明**：`save_job_state` 每次都调
`reconcile_runtime_review_truth`，它遍历**全部** `review_evidence`
（157 次保存 × 累计 99,218 次遍历）。5000 段 0.67s；10000 段会约 4 倍。
它与第三/四轮修掉的两个瓶颈是**同一个形状**（每次全量重扫）。

---

## 下一轮最值得做的一项

**一个：`review_truth` / `dependency_truth` 里那份原文/译文的完整拷贝
（4.31MB = `state.json` 的 41.1%）。**

理由：

1. 它是**唯一一处**能同时降低 #1（序列化 3.41s）和 #4（写字节 881MB）的载荷来源，
   而这两项合计占整轮约 34%。
2. **消费方审计已经做完**：这两个字段的 `source`/`target` 只被用于**相等比较**。
   把"当时的文本"换成"当时的文本摘要"，语义上是等价的判定。
3. 它不是"调用次数"类问题，**不依赖环境口径**——省下的字节在生产上一样省。

**但必须按独立一轮做，原因是它改的是持久化表示**：

- 被用例逐字段断言（`tests/translation_runtime_test.py:628` 断言
  `dependency_truth == [{...}]`），改表示就要改契约用例——必须说清楚
  "改的是表示，不是判定"。
- 需要**向后兼容**：旧 `state.json` 里存的是文本，新代码必须两种都能读
  （否则已有任务无法续跑）。
- 需要新的崩溃/续跑等价性用例：**"摘要方案与文本方案给出完全相同的 stale 判定"**
  必须是显式断言，而不是"看起来一样"。

**设计草案（未实施）**：`truth` 里保留 `segment_id` + 三个布尔标志，
把 `source`/`target` 换成 `source_digest`/`target_digest`（例如 sha256 前 16 字节）；
比较处改成"对当前值取同样的摘要再比"。旧格式（含文本）在读入时按原逻辑比较，
写入时升级为新格式。

**为什么不是另外两个**：

- **`review_truth_reconcile` 的 O(n²) 扫描（#2, 6%）**：要消除它必须有一个
  "哪些 evidence 变了"的变更信号。第三轮已经论证过：**由调用方维护的 dirty 域
  是脆弱契约**（漏标一次就静默丢数据）。要么不做，要么用**自校验**的方式做
  （例如给每个 entry 存一个内容摘要，扫描时先比摘要）——但那正是上一项的副产品，
  应该在同一轮里一起设计。
- **事件日志（#3）**：见结论 1，已证伪。

---

## 去留审计（KEEP / REVERT / EXPERIMENTAL）

| 改动 | 结论 | 依据 |
| --- | --- | --- |
| 第四轮的 TM 写节奏（`save_tm` 搭快照窗口） | **KEEP（本轮再次确认）** | 生产口径下 `tm_commit` 仅 0.48s；且它把事件日志调用从 8.13/批降到 6.38/批 |
| 第四轮的紧凑序列化 / 编码一次 / 目录缓存 | **KEEP** | `tm_prepare` 生产口径 0.003ms/次；紧凑编码仍是载荷侧唯一免费的一招 |
| **改事件日志的 `append_event`（含去掉冗余 `mkdir`）** | **REVERT（未实施）** | 生产口径只值 23.6ms = 整轮 **0.21%**；为零收益动恢复证据路径不划算 |
| **合并事件 / 降低 `fsync` 频率** | **REVERT（未实施）** | `fsync` 只值 0.10s；它是"事件已落盘"的唯一保证 |
| **提高快照节奏上限（少存几次）** | **REVERT（未实施）** | 会放宽崩溃后重算窗口（第三轮定死 32 段硬上限），换不到 30% 那一项 |
| **`review_truth`/`dependency_truth` 文本 → 摘要** | **EXPERIMENTAL（下一轮）** | 审计支持（只做相等比较），但改持久化表示，需要独立一轮 + 兼容用例 |

---

## 未改变的边界（架构边界审计）

| 边界 | 状态 | 证据 |
| --- | --- | --- |
| 本轮**未改任何生产代码** | **未改变** | 唯一被改的仓库文件是 `tests/brand_regression_test.py` 的文档 allowlist；其余 `.py` 改动全部来自第一至四轮。本轮新增文件都在 `tmp/phase5/*`（已被 `.gitignore` 忽略） |
| `state.json` = 唯一权威完整快照 | **未改变** | 未触碰写入语义 |
| `tm.json` = 派生物化缓存 | **未改变** | 第四轮已写清；本轮未动 |
| `events.jsonl` = 恢复证据 | **未改变** | 本轮只测量，未改 |
| 译文真值 / 审校权威 | **未改变** | 两种口径下 `translation_digest` 与 `st_x`/`tm_x` 完全一致 |
| 快照节奏与崩溃窗口 | **未改变** | `snaps=157`、`crash=32` 在两次 raw 运行中相同 |

---

## Regression

**本轮没有改任何生产代码，因此不需要新的全量回归。**
当前有效的证据仍是第四轮在本轮同一棵工作树上跑完的那一次：

| 项目 | 结果 |
| --- | ---: |
| 测试文件总数 | **91** |
| pytest 用例通过 | **1210** |
| **有效失败文件** | **0**（`exit=0`） |
| 记录 | `tmp/phase3/regression-tm.tsv` / `tmp/phase3/regression-tm.log` |

本轮新增的文件只在 `tmp/phase5/`（测量脚本与原始输出），不进入仓库、不被任何测试收集。

唯一被改动的仓库文件是 `tests/brand_regression_test.py`：本报告在「复现」一节逐字引用了
保留的旧前缀环境变量，触发品牌扫描，因此按第一至四轮同样的做法把本报告加进
`LEGACY_LINE_ALLOWLIST`（只放行 `FOLIOTHREAD_` 前缀行）。改完后单独复跑该用例：

| 检查 | 结果 |
| --- | --- |
| `_legacy_hits()`（全仓库扫描） | **0** 条未归类命中 |
| `pytest tests/brand_regression_test.py` | **2 passed** |

这是**文档表面**的改动，不涉及任何生产代码路径，因此不影响上面的 1210 用例结论。

---

## 复现

```bash
# 1) 单次操作成本：带 shim 与去掉 shim（真实本地文件系统）
PYTHONPATH="$PYTHONPATH" venv/bin/python tmp/phase5/io_toll.py \
    --label shimmed --out tmp/phase5/io-toll-shimmed.json
env -u PYTHONPATH        venv/bin/python tmp/phase5/io_toll.py \
    --label raw     --out tmp/phase5/io-toll-raw.json

# 2) 整轮：同一脚本两种口径
env -u PYTHONPATH venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase5/raw  --sizes 1000,5000 --batch-size 4
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase5/shimmed --sizes 1000,5000 --batch-size 4

# 3) 事件日志调用次数与累计耗时（去掉 shim）
env -u PYTHONPATH venv/bin/python tmp/phase3/event_log_cost.py 1000,5000

# 4) 载荷分解
env -u PYTHONPATH venv/bin/python tmp/phase5/state_composition.py 5000
env -u PYTHONPATH venv/bin/python tmp/phase5/evidence_composition.py 5000

# 5) 报告的品牌扫描（新增文档必须仍然干净）
venv/bin/python -c "
import sys; sys.path.insert(0,'tests'); sys.path.insert(0,'.')
from brand_regression_test import _legacy_hits
print('unclassified legacy hits:', len(_legacy_hits()))"
```

环境变量：本轮**未新增**任何环境变量。第三轮引入的快照节奏开关
（`FOLIOTHREAD_STATE_SNAPSHOT_EVERY` / `FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S`）
本轮未改，只被用来把两次 raw 运行放在同一节奏上。

---

## 本轮新增 / 修改文件

**新增（全部在 `tmp/phase5/`，不进仓库）**

- `tmp/phase5/io_toll.py` — 带 shim / 去掉 shim 的单次操作成本基准。
- `tmp/phase5/state_composition.py` — `state.json` 顶层键的载荷分解。
- `tmp/phase5/evidence_composition.py` — `review_evidence` 的字段级分解。
- `tmp/phase5/io-toll-{shimmed,raw}.json`、`raw*.log`、`shimmed.log`、
  `event-log-cost-round3-shimmed.json` — 原始测量输出。
- `docs/persistence-performance-round5-io-caliber.md` — 本文档。

**修改**

- `tests/brand_regression_test.py` — 本报告加进 `LEGACY_LINE_ALLOWLIST`
  （只放行 `FOLIOTHREAD_` 前缀行），理由与第一至四轮相同：报告逐字引用了兼容边界里
  保留的旧前缀环境变量。改后已单独复跑（2 passed），全仓库未归类命中为 0。

**未修改**：无生产代码、无既有测试语义、无既有文档的语义改动。
