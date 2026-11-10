# TM 模糊匹配：评分、来源与候选排序（0.8.2 / #148）

Issue：[#148 \[TM\] Fuzzy matching: scoring, provenance and candidate ranking](https://github.com/xueyang-dev/Folium/issues/148)
（本文件是本仓库的实现文档，链接仅作追溯用途）。

## 1. 结论速览

- 模糊匹配是**同一条 TM 读取层上的加法**：新的 `tm_candidates()` 只返回候选
  （建议），`tm_lookup()` / `tm_index()` / `_tm_hit()` / `tm_eligible` 的行为
  一个字节都没变。没有第二套 TM 真值，没有模型调用。
- 相似度是确定性的 `difflib.SequenceMatcher`（标准库，零新依赖）：输入先过
  现有 `tm_normalize`，精确命中 = 100、规范化等价 = 100（`kind` 区分）、
  模糊 = 四舍五入到整数百分比且**封顶 99**。
- 默认阈值 **75%** 是量出来的（见 §5）：语料上"不相关段落"的最佳分数上界
  < 50，"改一个数字"的分数下界 88，"改一个专名"的下界 67（ZH→EN 76）。
- 50,000 条实测（真实仓库散文，Apple M4）：普通文档 TM 查询 p50 **45.7 ms** /
  p95 **114.5 ms**、索引构建 **361 ms**（见 §6）；样板段最坏情况 p95
  **163.9 ms**（预算截断时如实标注 `scan_truncated`）。
- 回滚是单参数、无数据迁移：`fuzzy=False`，或部署级
  `FOLITH_TM_FUZZY=0`。
- 截断是**可判定的**：返回的是带 `scan_truncated` / `reason` 的 `list` 子类，
  空结果也能区分"预算耗尽没找到"与"TM 里确实没有"。

## 2. 读取接口

```python
from folith.engine.tm_store import tm_candidate_index, tm_candidates

index = tm_candidate_index(tm, "简体中文")          # 一次运行建一次（O(n)）
items = tm_candidates(
    tm, source, "简体中文",
    previous_source=prev, next_source=nxt,           # #107 上下文
    threshold=None,                                  # 默认 75（可覆盖）
    limit=5,                                         # Top-K（可省）
    fuzzy=None,                                      # False = 回滚
    index=index,                                     # 可选复用
)
```

每一项包含：

| 字段 | 含义 |
| --- | --- |
| `key` | 作用域键（"目标语言 + 原文"），TM 的身份，可直接回查 `tm` |
| `source` | 条目原文（未规范化） |
| `target` | 条目译文 |
| `score` | 整数百分比；exact/normalized = 100，fuzzy ≤ 99 |
| `kind` | `exact` / `normalized` / `fuzzy` |
| `context_status` | #107 的 `match` / `conflict` / `unknown` |
| `context_conflict` | `context_status == "conflict"` 的便捷位 |
| `legacy_unscoped` | 恒为 `False`（#108：旧条目不参与，不可能出现在候选里） |
| `reviewed` | 记录是否审校过（进索引的前提，恒为 `True`） |
| `provenance` | 记录自带来源（如 `tmx_import`），没有则空串 |
| `target_lang` | 条目声明的目标语言（原样写法） |
| `scan_truncated` | 该条目所属检索是否"部分检索"（便捷位；**空结果**要看返回对象上的同名属性） |

排序键：分数降序 → 等价程度（exact > normalized > fuzzy）→ 原文 → 键。
全部是确定性比较：同一输入、同一 TM 永远得到同一顺序，与插入顺序无关。

失败即关闭：目标语言无法证明时返回 `[]`（与 `tm_index` 的 fail-closed 语义
一致），旧版无作用域条目既不参与也不会"顺带"返回。

### 返回的是 `TmCandidates`（list 子类）——空结果也带完备性状态

`tm_candidates` 返回 `list` 的子类，**列表行为完全不变**（`len` / 迭代 / 下标 /
切片 / `== []` / JSON 序列化都照旧），额外挂本次检索的状态：

| 属性 | 含义 | 取值 |
| --- | --- | --- |
| `scan_truncated` | 本次检索是否"部分检索"（受工作预算限制） | `bool` |
| `reason` | 完备性或 fail-closed 原因 | `complete` / `eval_budget_exhausted` / `scan_budget_exhausted` / `eval_budget_exhausted+scan_budget_exhausted` / `no_target_language` / `empty_source` |
| `evaluated` | 实际做了多少次昂贵的 `ratio()` 评估 | `int` |
| `scanned` | 进入阶段二候选池（廉价上界过线）的条数 | `int` |
| `window_size` | 长度窗口内条目总数（预算裁剪前） | `int` |

**空结果必须能读出状态**——这是最容易出问题的一处：

```python
items = tm_candidates(tm, source, "简体中文")
if not items and items.scan_truncated:
    # 预算用完了、什么都没找到：不等于"TM 里没有相似段"，UI 要如实说明
    ...
```

截断信息不依赖候选条目存在（旧实现只把 `scan_truncated` 挂在候选上，空结果
时会完全丢失，等于静默截断）。注意 `list(items)` / 切片 / JSON 得到的是普通
list，状态只在**返回值本身**上。

## 3. 为什么是"候选"而不是"命中"

`tm_lookup` 的命中会被**直接复用**为已审校译文。翻译记忆是错误放大器：一段
"相似"的原文配上一段别的译文，会把这个猜测复制到整篇文档。所以：

- 可证明等价（exact / normalized）走原有 `tm_lookup`，行为不变；
- 相似（fuzzy）只进候选列表，调用方可以预填/展示，但必须经过 #29/#92 的
  候选事务（人工确认或独立审校）才能写入；
- 模糊分数封顶 99：100 专用于"同一句话"，调用方不需要靠"99.7 舍成 100"
  去猜一条候选到底是不是等价的。

## 4. 算法与索引

1. **规范化**：`tm_normalize(source)`（NFKC、空白折叠、引号/破折号/省略号），
   排版差异不扣分。
2. **相似度**：`difflib.SequenceMatcher.ratio()`（Ratcliff/Obershelp 匹配块）。
   复用同一个 matcher、把查询固定为 `seq2`（字符索引只构建一次），逐候选
   `set_seq1`。
3. **长度窗口（无损）**：`ratio = 2M/(la+lb)` 且 `M ≤ min(la,lb)`，因此长度
   `Lc` 只有在 `[Lq·t/(2-t), Lq·(2-t)/t]` 内才可能达到阈值 `t`；窗口外直接
   跳过，不改变任何分数。边界用 `t-0.5`（四舍五入半格）放宽，避免剪掉
   "舍入后刚好过线"的候选。
4. **上界剪枝（无损）**：`real_quick_ratio()`（长度上界，O(1)）→
   `quick_ratio()`（字符重集上界，O(len)）；上界低于阈值/第 K 名分数的候选
   不算昂贵的 `ratio()`。
5. **Top-K 剪枝（无损，前提是没触发预算）**：给出 `limit` 时用"当前第 K 名
   分数"抬高剪枝线。**当 `scan_truncated == False` 时**，返回结果与"全量排序
   后取前 N 名"逐条相同（有测试锁定）；一旦预算截断，阶段一按"字符重集上界
   降序"淘汰，而上界排序 **不等于** 精确 `ratio` 排序，前 N 名可能与全量
   结果不同。
6. **工作预算（有损，但如实标注）**，两条独立路径：
   - `max_evals=400`：阶段一只保留字符重集上界最高的 400 条进阶段二；
   - `max_scan=6000`：长度窗口本身超过廉价扫描上限时，只扫窗口内"长度最接近
     查询"的 6000 条（`_tm_window_positions`），窗口尾部根本不看。
   任一触发都会在 `TmCandidates` 上置 `scan_truncated=True`，`reason` 写明
   是哪一路（可能两者同时）。深度样板段的 TM（整篇同一句式）里字符重集上界
   几乎等于 1，前几层剪枝不起作用，50k 穷举会把一次查询拖到数秒——这是
   需要预算兜底的场景。

**索引是快照**：`tm_candidate_index` 只在构建时读 `tm`；之后 `tm` 有增删、
审校状态或目标语言变化时必须重建索引，复用旧索引只会按旧快照返回（不会读出
错数据，但会漏掉新条目）。一次运行内 TM 不变时复用是安全的，也是 50k 规模
下的推荐用法。

两项**不做**：不做 LLM/embedding 检索；不为相似度建第二份 TM 真值。

### 为什么用标准库 difflib 而不是 rapidfuzz

- 本仓库虚拟环境（`venv`，Python 3.11.3）**没有安装** rapidfuzz；引入它等于
  为一条只读候选路径新增运行时依赖，而"零额外模型调用/零额外运行时依赖"
  是这个功能的价值边界。
- difflib 是纯 Python、确定性实现，跨平台与跨 Python 版本（仓库支持
  3.10/3.11/3.12）结果稳定、可复现，测试里可以把期望分数**推导**出来
  （`(len-k)/len`）而不是抄实现输出。
- 在 §6 的索引策略下，50k 条查询的 p95 已经在交互式量级内，没有必要为一个
  常数因子换依赖。如果将来需要更快，`rapidfuzz` 可以原地替换第 2 步的
  `ratio()`（接口不变），但必须先补依赖说明与基准。

## 5. 默认阈值 75% 的标定（量出来的，不是猜的）

### 方法

语料来自本仓库的真实自然语言文本（`docs/**/*.md` 与根目录 markdown，按句子
切分后去重，长度 ≥ 20 字符）：

- 英文 60,881 句（长度 p5/p50/p95 = 19 / 49 / 83 字符）；
- 中文 19,759 句（p5/p50/p95 = 16 / 44 / 85 字符）。

每类抽 400 条做查询，TM = 对应语料前 20,000 条（查询条目本身不在 TM 的
"自己的记录"里）；分数取 `tm_candidates(..., threshold=1)` 的最高分。四类
样本：**改一个数字**、**改一个专名**、**改掉约 15% 的文本（一句之差）**、
**不相关段落（同语料的其他句子，自身记录排除）**。

### 结果（`threshold=1`，最高分）

| 样本类 | 语对 | n | min | p5 | p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- |
| 改一个数字 | EN→ZH | 113 | 88 | 93 | 98 | 99 |
| 改一个数字 | ZH→EN | 129 | 92 | 95 | 98 | 99 |
| 改一个专名 | EN→ZH | 80 | 67 | 69 | 90 | 95 |
| 改一个专名 | ZH→EN | 400 | 76 | 87 | 94 | 98 |
| 一句之差（≈15% 文本） | EN→ZH | 82 | 58 | 60 | 72 | 81 |
| 一句之差（≈15% 文本） | ZH→EN | 314 | 50 | 52 | 58 | 65 |
| **不相关段落** | EN→ZH | 200 | — | — | **< 50** | — |
| **不相关段落** | ZH→EN | 200 | — | — | **< 50** | — |

（"不相关"一列是 0 个样本达到 50 分：同语料内不相关段落的最佳分数全部低于
50，即噪声带的上界 < 50。exact / normalized 均为 100。）

### 决策

默认阈值取 **75**：

- 高于实测噪声上界（< 50）留出 25 分余量，避免跨项目合并 TM（20 年 TMX）
  里相似的样板句互相污染；
- 完整保留"改一个数字"（下界 88）与绝大多数"改一个专名"（EN 侧 p5=69、
  ZH 侧 p5=87）；
- 与专业 CAT 的模糊区间下界（75）一致，UI 与译文回忆的心智模型不变。

可配置性与回退：`threshold=` 按查询覆盖；`FOLITH_TM_FUZZY_THRESHOLD`
部署级覆盖。语料实测显示 60–70 也能保持零噪声（同语料内），但那是牺牲
跨语料安全性换召回，因此**不是默认值**；调用方如果要召回短段/大改写的段落，
应该显式降低阈值并在 UI 上把低分候选标成"需大改"。

### 短段与长段的诚实分数

- 英文短句 `The gate is closed.` vs `The gate is locked.` = 89 分（过默认阈值）；
- 中文 7 字短句 `大门已经关闭。` vs `大门已经锁上。` = 71 分（**低于**默认
  阈值）。字符级相似度在短 CJK 段上天然偏低，这是真实信号，不为凑候选而
  抬高分数；需要短段召回的场景显式降阈值（测试里用 65）。
- 长段（> 200 字符，含 difflib autojunk 边界之外）改一个数字仍是 99 分。

## 6. 性能基线（≥ 50,000 条）

### 机器与实现

- Apple M4 / 16 GB / macOS 27.0.1（arm64）
- Python 3.11.3（`venv`），`difflib.SequenceMatcher`，`max_scan=6,000`、
  `max_evals=400`、`threshold=75`、`fuzzy=True`

### 实测 1：普通文档 TM（真实仓库散文，50,000 条）

- 语料：本仓库真实英文句子（`docs/**/*.md`、根目录 markdown、`tests/**/*.py`
  按句切分去重后取 50,000 条；长度 p5/p50/p95/max = 18 / 49 / 83 / 659 字符）。
- 查询：随机 200 条，各自改掉一个数字/一个词（"相似但不相同"）。
- 索引构建（50,000 条，一次性）：**361 ms**。

| 返回条数 | p50 | p95 | p99 | max |
| --- | --- | --- | --- | --- |
| `limit=None`（全量排序） | **45.7 ms** | **114.5 ms** | 139.0 ms | 154.2 ms |
| `limit=5`（Top-K） | 41.9 ms | 121.7 ms | 142.8 ms | 149.5 ms |

（Top-K 剪枝在这套语料上没有明显收益：普通散文里字符重集上界本来就低，
瓶颈是长度窗口内的廉价扫描，而不是昂贵的 `ratio()`。它真正起作用的是
样板段语料，见实测 2。）

### 实测 2：可复现的测试代理语料（`tests/tm_fuzzy_performance_test.py`）

- 语料：512 词表 + 对数正态长度分布（4–34 个词，最多约 250 字符），
  50,000 条；句子比实测 1 长得多，作为**保守上界**。
- 索引构建 **1,679–2,419 ms**（字符串更长，规范化更贵；仍然只做一次）。

| 语料 | 查询 | p50 | p95 | p99 | max |
| --- | --- | --- | --- | --- | --- |
| prose（普通） | `limit=None` | 206.7 ms | 303.3 ms | 343.0 ms | 350.6 ms |
| boilerplate（样板段） | `limit=5` | 133.7 ms | 163.9 ms | — | 163.9 ms |

### 接受上界与降级

- 接受上界（本功能的用途是"工作台里按需取候选"，不是翻译管线里的逐段
  查询）：**p95 ≤ 350 ms**。实测普通 TM 114.5 ms、保守长句代理 303.3 ms、
  样板段 163.9 ms；最坏情况有余量、普通情况有约 3 倍余量。
- 预算是性能旋钮而不是正确性旋钮：`FOLITH_TM_FUZZY_MAX_EVALS`（默认
  400）越小越快、越可能"部分检索"；同一个 corpus 上把预算调到 10 时 p95 降
  到 ≈ 10 ms，但候选集合可能不完整（`scan_truncated=True`）。
- **测试里的 `p95 < 1500 ms` / `p95 < 3000 ms` 是 CI 数量级护栏，不是性能
  验收**：它们只拦"慢了一个数量级"的回归（实测值是上面的 45.7–303.3 ms，
  护栏留了 5–10 倍余量），因为 CI 机器的绝对延迟与开发机不同，收紧成实测
  阈值只会变成随机红灯。性能验收以上表的实测数字为准，回归时重跑
  `tests/tm_fuzzy_performance_test.py -s` 对比。该文件单独跑约 45–125 s，
  适合放在 full-regression 而不是 PR fast checks。
- 超预算/超时时的降级就是 `fuzzy=False`（只给 exact/normalized）——与 issue
  的 "degrade to exact/normalized" 一致，任务不会失败。
- **不上数据库**：0.9.0 之前不引入 SQLite/向量库；索引是进程内只读快照。

## 7. 兼容性与回滚

- **旧格式 TM**：`translation_memory.json` 原样加载、原样保留在文件里
  （读取不写回）。旧版"原文为键、无语言标注"的条目加载无损，但按 #108 语义
  永不参与候选（`legacy_unscoped_count` 只用于展示计数）；`#107` 之前的
  "有作用域、无上下文字段"旧条目照常精确命中，上下文状态为 `unknown`。
- **既有行为不变**：`tm_lookup` / `tm_index` / `_tm_hit` / `_tm_eligible` 未改
  一行；`tests/tm_*` 全部保持绿色。
- **回滚**（无数据迁移）：
  - 单次调用：`tm_candidates(..., fuzzy=False)` → 只返回 exact/normalized；
  - 部署级：`FOLITH_TM_FUZZY=0` → 所有未显式传参的调用回到
    exact/normalized（0.8.1 状态）。
- **写入路径不变**：模糊候选不写 TM、不触发审校晋升；#109 "审校决策成功才
  入库"的契约不受影响。

## 8. 接线边界（本 issue 未改 `core.py`）

本 issue 只交付读取层与候选排序。翻译管线（`core.translate_stage`）仍然只用
`tm_lookup` 做 exact/normalized 复用——这是**故意的**：让模糊候选进入管线
需要与 Workbench 的候选事务（#151）一起设计"建议→确认→晋升"的路径，属于
另一个 issue 的所有权范围。消费方（工作台候选卡片）应调用

```python
items = tm_candidates(tm, segment_source, target_lang,
                      previous_source=prev, next_source=nxt, limit=5)
```

并按 `kind` / `score` / `context_conflict` / `scan_truncated` 展示与降级。

## 9. 测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/tm_fuzzy_matching_test.py` | 黄金集（100 / 规范化 / ~85 / 阈值边界 ±1 字符 / 明显不匹配）、排序与确定性、来源字段、#107 上下文冲突、#108 旧条目不出现、回滚与阈值单调性、fail-closed 语言、索引复用与 `tm_index` 一致性、长段/短段/数字/人名、旧格式加载无损、**空结果截断状态**（eval/scan 两条路径）、**候选查询不回写 TM**（字节 + `mtime_ns` 断言） |
| `tests/tm_fuzzy_tmx_import_test.py` | 导入 1,000 条 TMX（真实 `import_tmx`）后相似原文端到端返回带分数候选、`provenance=tmx_import`、现有 `tm_index`/`tm_lookup` 不受影响 |
| `tests/tm_fuzzy_performance_test.py` | 50,000 条延迟分位（脚本兼冒烟）、样板段最坏情况、长度窗口与 Top-K 剪枝无损性、预算截断如实标注 |

复现：

```bash
venv/bin/python -m pytest tests/tm_fuzzy_matching_test.py \
    tests/tm_fuzzy_tmx_import_test.py -q
venv/bin/python -m pytest tests/tm_fuzzy_performance_test.py -q -s   # 打印分位数
```

## 10. 已知限制 / 未验证部分

- 阈值标定用的是**本仓库自身**的中英文散文（同域语料），不是 20 年跨项目
  TMX；跨域语料需要按 §5 的方法重新标定（这是默认值的保守原因）。
- 性能数字来自 Apple M4 + Python 3.11；Windows/CI 机器会慢 2–3 倍，测试里
  的断言只拦数量级回归（p95 < 1,500 ms / 3,000 ms），不锁具体毫秒。
- 工作预算截断时结果是"部分检索"（`scan_truncated=True`，空结果也能读到）：
  分数仍然精确，但集合可能漏掉候选（`max_evals` 路径理论上也可能挤掉一条
  真实高分的候选）；这是有意的、被显式标注的取舍。
- `difflib.ratio` 是启发式（Ratcliff/Obershelp），不是严格编辑距离：极端
  构造的字符串（例如每隔几个字符就改动、且改动字符自身重复）可能给出比直觉
  更低的分。这在本 issue 允许的 "difflib/rapidfuzz 级别" 范围内，但黄金集里
  的期望分数一律是推导 + 独立参考实现双向校验的。
- 未接线 `core.py`（见 §8）：管线内的模糊复用由后续 issue 交付。
