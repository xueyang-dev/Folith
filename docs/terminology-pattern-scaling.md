# Terminology Pattern Scaling — Performance Phase 8

> 唯一主任务：消除「术语规模超过 pattern cache 容量后出现的 regex compilation cliff」，
> 同时保持 terminology matching 结果**逐例等价**。
> 明确不做：Project Memory retrieval 优化（仅观察，见 §13）。
>
> 引用约定：`## §N` 是**本文档**的章节号；行文里的 `（用户 §M）` 指用户 brief 的编号。
> 两者在多数地方同号，不同的地方会显式标注。

---

## Final verdict

**PASS**

`terminology matching` 已经从「pattern cache 容量决定性能、一旦 glossary 超容量就反复
regex compile」变成「**每个 glossary revision 每个 unique pattern 只编译一次**」的稳定模型：

| 判据（用户 §13） | 结果 |
| --- | --- |
| 差分等价（§6） | **100% EQUIVALENT**（20,000 例 × 16 个语义维度，0 mismatch） |
| 端到端语义门禁（§7） | **8/8 fixture EQUIVALENT**，捕获产物逐字节相同 |
| steady-state 编译数 | **0**（扫描期编译数与 G / 批数 / 4096 边界全部无关） |
| 编译数上界 | `= unique patterns per revision`（G=20000 → 18,000，恰好一次） |
| G=10000 悬崖 | **消失**（`compiles/pattern` 从 3.00 降到 1.00，扫描期 0） |
| 内存 | 线性 O(patterns)，实测 **≈625 B / pattern**（§10），修订数与 pattern 总数双重有界 |

一个保留项：§12 的 4096 LRU 仍然留在代码里，但**主 translation / review 路径已不依赖它**，
它现在是文档化的 fallback。这就是 verdict 用 PASS 而不是 PASS WITH FOLLOW-UP 的原因——
用户 §13 的四条判据全部满足，且没有任何一项靠"把容量调大"达成。

---

## Root Cause

**为什么 4096 之后会发生 cache thrash。**

`_term_pattern(term)` 用 `_TERM_PATTERN_CACHE`（`OrderedDict`，`_TERM_PATTERN_CACHE_MAX =
4096`）做记忆化。这是「**按 term 键控 + 固定容量**」的缓存，而真实工作集是**整张术语表**：

```
select_glossary_for_segments / check_glossary_compliance / _relevant_glossary /
_review_glossary_scope / stale_segments_for_glossary
        都是「外层遍历 batch（或 segment、或 pair），内层遍历整张 glossary」
```

内层对同一个 term 是连续查询（一次 miss + (S-1) 次 hit），所以**单次调用**只会产生
`G` 次编译、不会 thrash。thrash 发生在**调用之间**：起始位置每轮都从 `entries[0]` 重新开始，
而 LRU 里只剩上一轮的尾部 4096 条，于是

```
unique_patterns > 4096   =>   每轮扫描的命中率为 0   =>   每轮重编译全部 pattern
                          =>   编译数 = patterns × 批数
```

这就是实测到的 `compiles / unique_pattern` 比值：

| unique patterns | `re._compiler.compile` 调用数 / unique pattern |
| --- | --- |
| 90 / 450 / 900 / 3686 / **4096** | **1.00** |
| **4097** / 4200 / 4500 / 5000 / 6000 | **3.00**（= 批数） |

`batches = 3` 的测试台上是 3.00；在真实文档里它就是 `n / batch_size`，即
**Θ(G × n / S)**，与文档长度成正比——所以这不是"常数变大"，是 scaling 项变号。

边界精扫（§2.2）给出了决定性证据：`G=4096 → 1.00`，`G=4097 → 3.00`，
**多一个 term，CPU 从 0.606 s 跳到 1.686 s（+178%）**。容量是开关，不是平滑曲线。

> 注：`re` 模块自带的 512 条缓存只是把 cliff 从 512 挪到了 4096（Phase 5 的成果），
> 它是同一个反模式的下一次出现，不是解。

---

## §1 Baseline（当前 revision 重新测量，不复用 Phase 7 数字）

命令：

```bash
./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
    --glossary-sizes 100,500,1000,4096,5000,10000,20000 \
    --cjk-fraction 0.10 --repeat 3 --variant current \
    --json tmp/phase8/micro_before_mix.json
```

夹具：确定性伪词术语表（6 种 ASCII 形状 + 3 种 CJK 形状，全部互不相同），
`cjk_fraction=0.10` 贴近真实学术术语表。工作负载：`3 批 × 6 段 × 全表`，
与生产代码同形（`any(...)` 短路）。

| G | unique terms | unique patterns | `term_matches` calls | cache hits | cache misses | `re.compile` | **`sre_compiles`** | /pass | CPU s | wall s | peak RSS MB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 100 | 90 | 1,800 | 1,530 | 90 | 90 | 90 | 30 | 0.013 | 0.013 | 22.7 |
| 500 | 500 | 450 | 9,000 | 7,650 | 450 | 450 | 450 | 150 | 0.068 | 0.068 | 22.2 |
| 1,000 | 1,000 | 900 | 18,000 | 15,300 | 900 | 900 | 900 | 300 | 0.133 | 0.133 | 23.0 |
| 4,096 | 4,096 | 3,686 | 73,728 | 62,662 | 3,686 | 3,686 | **3,686** | 1,228 | 0.559 | 0.559 | 33.1 |
| 5,000 | 5,000 | 4,500 | 90,000 | 67,500 | 13,500 | 13,500 | **13,500** | 4,500 | 1.850 | 1.851 | 46.4 |
| 10,000 | 10,000 | 9,000 | 180,000 | 135,000 | 27,000 | 27,000 | **27,000** | 9,000 | 3.691 | 3.693 | 53.5 |
| 20,000 | 20,000 | 18,000 | 360,000 | 270,000 | 54,000 | 54,000 | **54,000** | 18,000 | 7.668 | 7.679 | 69.7 |

**列出处（每列都必须能追到具名产物，否则不可复算）**：

| 列 | 来源 |
| --- | --- |
| calls / hits / misses / `re.compile` / `sre_compiles` / `/pass` | `tmp/phase8/rss_<G>.json`（与 `micro_before_mix.json` 逐格相同——**计数是确定性的**） |
| CPU s / wall s | `tmp/phase8/rss_<G>.json`（**repeat=1、每档独立进程**，避免多档累积与后台负载漂移） |
| peak RSS MB | 同左 |

> **注意：计时/内存列会随沙箱负载漂移（实测同形运行间 1–3%），计数列不会。**
> 复算时若 CPU 有小幅出入属预期；`sre_compiles`、`cache misses`、`term_matches calls`
> 必须逐格相同——**那才是本轮的判据**。`rss_<G>.json` 是 repeat=1 的分档产物；
> `micro_before_mix.json` 是 repeat=3 的一次性产物，两者计数一致、计时略有差异。

**cliff 稳定复现**：`compiles / unique_pattern` 在 4096 及以下恒为 1.00，在 4097 及以上
恒为 3.00（= 批数）。`re_compile_calls == cache_misses` 恒成立，说明调用的门槛就是我们
这层 LRU；`sre_compiles ≤ re_compile_calls` 的差是被 `re` 自身 512 条缓存吸掉的部分。

### 1.1 边界精扫（`cjk_fraction=0`，`patterns == G`）

| G | unique patterns | `sre_compiles` | /pattern | CPU s |
| ---: | ---: | ---: | ---: | ---: |
| 3,000 | 3,000 | 3,000 | 1.00 | 0.443 |
| 3,800 | 3,800 | 3,800 | 1.00 | 0.560 |
| **4,096** | **4,096** | **4,096** | **1.00** | **0.606** |
| **4,097** | **4,097** | **12,291** | **3.00** | **1.686** |
| 4,200 | 4,200 | 12,600 | 3.00 | 1.731 |
| 4,500 | 4,500 | 13,500 | 3.00 | 1.854 |
| 5,000 | 5,000 | 15,000 | 3.00 | 2.070 |
| 6,000 | 6,000 | 18,000 | 3.00 | 2.483 |

`G=4096` 处 cache size 恰好等于 max（4096/4096）且仍是 1.00；`G=4097` 立刻变 3.00。
一个 term 之差 CPU +178%。**这就是"容量开关"的确证。**

### 1.2 测量陷阱（写下来避免下一个人再踩）

同进程连续测多档时，**必须同时清两个缓存**：`terminology.reset_term_pattern_cache()` 与
`re.purge()`。只清前者会得到被上一档污染的数字——同进程跑过 G=500 之后，G=1000 的前
450 个 pattern 已在 `re` 模块缓存里，编译数会**假报 450 而不是 900**（首轮实测就是这个
数字，纠正后才自洽）。`re.purge()` 会清 `re._cache` / `re._compiler._cache` /
`re._parser._cache` 三级。

---

## §2 为什么「把 4096 调大」不是解

调大容量只是把同一个反模式的下一个边界往后挪：只要 `unique patterns` 超过新容量，
命中率仍然是 0，编译数仍然是 `patterns × 批数`。证据就是 §1.1 那条边界——它在 512
（`re` 模块）和 4096（本项目）两处各出现过一次，形状完全相同。

所以本轮的目标不是"更大的容量"，而是**换工作单元**：

```
旧：  term_matches 调用次数  决定编译次数
新：  每个 glossary revision 每个 unique pattern 编译一次
```

`4096 → 16384/65536` 只作为**对照实验**存在（§1.1 的边界精扫），不作为 production 方案。

---

## §3 `CompiledGlossary` —— 语义等价

`term_matches` 的语义**一字未改**（`terminology.py:term_matches` 保持原实现与 `_CJK_RE`
分支），新增的 `CompiledGlossary.matches()` 是它的查表实现：

| 输入 | `term_matches` | `CompiledGlossary.matches` |
| --- | --- | --- |
| 空 term / 空 text / None | `False` | `False`（同判断） |
| term 含 CJK | `term in text` | `term in self.plain` → `term in text` |
| 非 CJK 且在表内 | `_term_pattern(term).search(text)` | `self.patterns[term].search(text)` |
| **非 CJK 但不在表内** | `_term_pattern(term).search(text)` | **回落 `term_matches(term, text)`** |

最后一行是**语义安全网**：被匹配的字符串不一定是 source（例如
`check_glossary_compliance` 会匹配 `preferred`、`translation_planner` 会匹配 `expected`），
表外字符串一律走通用实现，因此不可能出现"静默变 False"的假阴性。

匹配模式由**术语原文的纯函数**决定，这一点是整套设计的前提：`re.escape(term)` + 固定
前后视断言，没有第二处状态。

### 3.1 工作单元与复用点

已编译视图现在覆盖这些**放大形状**的调用点（外层遍历 batch/segment/pair × 内层遍历全表）：

| 调用点 | 形状 | 频率 |
| --- | --- | --- |
| `terminology.select_glossary_for_segments` | batch × G × S | 每批（setup 热路径） |
| `terminology.check_glossary_compliance` | segment × G | 每段 |
| `terminology.detect_glossary_conflicts` | G × pairs | 每文档 |
| `terminology.stale_segments_for_glossary` | pairs × G | 每次术语表变更 |
| `translation_core.review_packet._relevant_glossary` | batch × G × S | 每批 |
| `translation_evidence._review_glossary_scope` | batch × G × S | 每批 |

未改动的调用点分两类：

| 调用点 | 形状 | 为什么不动 |
| --- | --- | --- |
| `translation_planner._terminology_findings` | 单次调用，最多 G 次编译 | 不是放大器 |
| `find_occurrences`（抽取路径，`terminology.py:320`） | 单次调用，最多 G 次编译 | 不是放大器 |
| `eval/metrics/terminology.py:24-42` | **`G × pairs`（同形放大器）** | 离线评估路径，不在 production 的 critical path 上 |
| `eval/term_audit.py:80-88` | **`G × segments`（同形放大器）** | 同上 |
| `eval/sampling/blind_review.py:89-95` | **`G × n`（同形放大器，且 `term_count` 被重复调用）** | 同上 |

**注意：这是一处有意留下的欠账，必须如实记录**：那三处 `eval/*` 与
`stale_segments_for_glossary` 是**同一种形状**，所以它们**会重新引入同一条 cliff**
（`patterns > 4096` 时每轮重编译全表）。本轮不动它们的理由是 §9 的"不要顺手重构整个
terminology architecture"——它们跑在离线评估里，不在翻译/审校主链路。
**若将来把 eval 跑在"大术语表 + 长文档"上，它们需要同样的改动（把 matcher 提到循环外）。**

**没有顺手动 terminology 架构**（§9 的要求）——本轮只动主链路的放大形状调用点。

---

## §4 Cache identity

键 = **对"参与匹配的字符串序列"的内容哈希**（`_sources_revision`，sha1 over
`"\x00".join(stripped strings)`）。

§4 点名禁止的三类 token，以及为什么最终**没有**用其中的 glossary hash：

| 候选 | 是否可用 | 理由 |
| --- | --- | --- |
| `id(glossary)` / 对象地址 / 列表身份 | 不可用 | glossary 会被就地更新而键不变 → 返回过期 pattern（静默语义错误） |
| `glossary_frozen.version` | 不可用 | 只在 `freeze_glossary` 时 +1，而 `state["glossary"]` 存在**绕过 re-freeze 的写入点**（Phase 7 §6.4 已记录） |
| `models.glossary_hash(entries)` | 可用但太贵 | 它是 O(G log G)（canonical JSON + 排序），每次查询都算一遍等于**把 regex 问题换成 hash 问题** |
| **被匹配字符串的内容修订号** | **采用** | 它就是 pattern 集合的**定义域**；O(Σ\|s\|)，实测 G=1,000 / 10,000 / 20,000 → 0.54 / 6.54 / 11.73 ms |

> 实测口径（`tmp/phase8/key_cost_probe.py`，真实 EN→ZH 形态 = 英文 source + 中文译名，
> 中位数，9 次取最小）：G=100 → 0.05 ms、1,000 → 0.54 ms、4,096 → 2.41 ms、
> 10,000 → 6.54 ms、20,000 → 11.73 ms。**这不是可以忽略的量级**，所以它必须是
> 「每批常数次」而不是「每次匹配一次」——`core.check_translation_batch` 已把 matcher
> 预派生一次并向 `check_glossary_compliance` 透传（§9）。

**被评估并否决的方案：键只取第一个字段（`source`）。** 它把键的字节数降到 1/3
（G=10,000 时 6.54 ms → 3.03 ms，省 3.5 ms/次），而且**是安全的**：已编译视图是
「字符串 → pattern」的纯函数；某字符串没进视图时 `matches()` 回落 `term_matches`；
CJK 串只进 `plain` 子串集合。因此最坏情况只是"少编译几个字符串"（退化到 legacy 路径），
**永远不会命中过期 pattern**。否决的理由不是"不安全"，而是：

- 安全论证依赖一条**未被强制的调用方不变式**（"查询的字符串必须来自调用方自己的 entries"）。
  今天 6 个调用点全部满足，但没有任何机制阻止未来的第 7 个调用点违反它；
- 收益只在 `G > 4096` 时兑现，而那正是本轮已经拿到 **3.00× → 1.00×**（编译数）与
  **18.4×**（工作负载）的场景。为 G=10,000 上约 9% 的收益去换一个有附加条件的缓存身份不划算；
- §4 要求 cache identity 基于 glossary revision。取全字段键后 `revision` **无条件等于**
  「这份编译视图的内容」，任何调用方都能直接依赖它，不需要读懂上面那段论证。

**同一张术语表在所有调用点得到同一个键**：默认纳入的字段是 `source` / `preferred` /
`target`（三者都会被 `term_matches` 匹配），所以 setup 路径与 review 路径复用同一份编译
产物，不会出现"两份各自编译一遍"。对中文为目标语的术语表，`preferred`/`target` 是 CJK
→ 进 `plain` 子串集合、**不产生额外正则**。

> 三个调用点（`review_packet.py:111`、`translation_evidence.py:526`、
> `terminology.stale_segments_for_glossary`）都先 `models.normalize_glossary` 再传进来，
> 而 `normalize_glossary_entry` 恒产出含 `source`/`preferred`/`target` 的条目
> （`preferred` 默认回落到 `target`）→ 各调用点的字段集合同构，键因此稳定共享。
> 这条由 `test_default_fields_compile_source_and_all_glossary_names` 守卫。

所以 §4 里"确认 hash 是否已经在 mutation 时产生 / 可复用"的答案分两半：

- **已经在产生**：`glossary_hash` 在 review batch 里每批都会算（Phase 7 记录 2.5 s / 280 批），
  `glossary_frozen.glossary_hash` 在冻结时算。
- **但不能直接当键**：它是**全条目**（含 target/behavior/status/evidence）的哈希，比
  pattern 的依赖面**更宽**——改一个无关的 `note` 就会让 pattern 缓存失效并重编译全表；
  而更窄的 `version` 又不健全。真正的可用投影是"被匹配字符串集合"，成本 O(Σ|s|) 远低于
  `glossary_hash`，因此直接派生它，不借用任何现成 token。

---

## §5 Mutation contract（用户 §5 逐条覆盖）

全部有测试守卫（`tests/terminology_compiled_glossary_test.py`）：

| 变更 | 契约 | 实现方式 | 测试 |
| --- | --- | --- | --- |
| **Add term** | 新 matcher 可见 | 字符串序列变化 → 新 revision → 重新编译 | `test_add_term_is_visible` |
| **Remove term** | 旧 pattern 不得继续命中 | 同上；且缓存不会返回旧对象 | `test_remove_term_drops_the_pattern` |
| **Edit source term** | 旧 pattern invalidated | `source` 进哈希 → 新 revision | `test_edit_source_invalidates_pattern` |
| **Edit target term** | pattern 不变可复用，metadata 必须更新 | 视图**不持有 entries**，metadata 永远来自调用方当次的那份条目；默认字段把 target 纳入匹配，因此 revision 也会变 | `test_edit_target_changes_revision_only_when_target_is_a_match_field` |
| **Freeze/lock/reject 状态变化** | eligibility 立即反映 | pattern 复用**不携带** eligibility：`select_glossary_for_segments` 用当次条目的 `status` 过滤 | `test_status_change_does_not_leak_through_pattern_reuse` |
| **Target language / project scope change** | 不得串用 compiled matcher | 键含全部被匹配字符串；不同 glossary 键不同 | `test_distinct_glossaries_do_not_share_patterns`、`test_scope_change_is_caller_side` |

另有两条**守卫**（防"日后有人把口径悄悄收窄"）：

| 守卫 | 钉住的事实 | 测试 |
| --- | --- | --- |
| 字段覆盖面 | `source` / `preferred` / `target` **都**进视图（否则译名会退回逐次回落，重新引入随调用次数增长的编译） | `test_default_fields_compile_source_and_all_glossary_names` |
| 命中即复用 | 同一份匹配内容返回**同一个对象**，不只是同一 revision | `test_cache_hit_avoids_rebuild`（`second_view is first_view`） |

关键设计取舍：**`CompiledGlossary` 只持有编译产物，不持有 entries**。这让"pattern 可以
复用、但状态/元数据必须当次重新读"这条契约变成结构性事实，而不是靠纪律维持。

---

## §6 语义等价 —— `100% EQUIVALENT`

命令：

```bash
./venv/bin/python scripts/phase8_terminology_differential_gate.py \
    --cases 20000 --glossary-size 5000 --json tmp/phase8/diff_gate.json
```

oracle = `scripts/phase8_terminology_differential_gate.py:legacy_term_matches`，即 Phase 8
之前的实现**逐字保留**（legacy copy 不随生产实现变化，是语义的权威定义）。

```
seed=20260926 cases=20000 glossary_terms=5000
  positive_matches=11036 negative_matches=8964
  dimensions: absent 1347 / ascii_word 1544 / case 1933 / cjk 1141 / empty 576 /
              fallback 926 / hyphen 993 / mixed 931 / multiword 992 /
              plural_like 1556 / punctuation 1167 / regex_meta 1166 /
              substring 1509 / unicode 1585 / whitespace 1103 / word_boundary 1531
  mismatches=0
RESULT: EQUIVALENT
```

§6 要求的每个语义面都有专属生成器（不是"事后打标签"）：ASCII 单词 / CJK / 含标点 /
连字符 / 大小写（upper/lower/title/swapcase）/ 词界（前后粘 `a z 9 _ 0`）/ 类复数邻词
（`s es ed ing`）/ 子串假阳性（`pre`+term+`post`）/ 正则元字符 / 空白归一（term 前后加
空白、文本内换成 tab/换行）/ Unicode（NFC↔NFD、`ﬁ`↔`fi`、`Ω`↔`Ω`、全角↔半角、全角字母）
/ 表外字符串回落 / 空值。

覆盖 20,000 例、11,036 正向 / 8,964 负向，**0 mismatch**。

> `séance`(NFC) vs `se\u0301ance`(NFD) 的用例确认了一件容易忽略的事：它们是不同的
> 字符串，因此是不同的 pattern、不同的 revision，两边实现都给出 `False`——等价性不受
> Unicode 归一形式影响，因为两边都不做归一。

---

## §7 端到端门禁（用户 §8）

`scripts/translation_setup_equivalence_gate.py` 跑 8 个真实 fixture，比较 selected
glossary entries / glossary packet / terminology refs / compliance findings / conflict
detection / frozen term behavior / final translator prompt / reviewer terminology
evidence / final target / findings digest。

**代码已经改了，所以 before 侧不能靠回滚工作树。** 做法是同进程 neutralized A/B
（`tmp/phase8/gate_neutralised.py`）：把 `terminology.compiled_glossary` 换成返回 legacy
委托对象的版本，它的 `.matches()` 直接调 `term_matches`，于是所有调用点退回旧路径，
而 fixture / provider / 捕获逻辑完全不变。

```
$ ./venv/bin/python tmp/phase8/gate_neutralised.py --out tmp/phase8/gate_before.json
$ ./venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase8/gate_after.json
$ ./venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase8/gate_before.json

=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK      glossary_wide_40: OK
  knowledge_candidates_40: OK long_chain_120: OK
  plain_40: OK                sections_entity_60: OK
  single_batch_8: OK          tm_hits_40: OK
```

每个 fixture 的 `term_matches` 计数也逐格相同（520 / 16000 / 720 / 480 / 2797），
证明**判定结果**相同，而不只是"某个函数被调用了"。

### 7.1 门禁自身的仪表盲区（本轮修正）

原来的捕获只挂在 `terminology.term_matches` 上。Phase 8 之后主路径走
`CompiledGlossary.matches`，于是 `term_match_digest` 会变成"只统计回落调用"——
**会让一个逐字节等价的改动看起来像回归**。这门禁的捕获点已提到**语义边界**：
两个入口都写进同一张 keyed map，因此 digest 与"哪个实现回答"无关。

这是本轮唯一的门禁改动，属修仪表而非放宽判据（`GATED_FIELDS` 与 `CALL_LOG_ONLY` 未动，
比对仍是逐字节 sha256）。

---

## §8 Compile Count

| G | Before（`sre_compiles`） | After（build，一次性） | After（扫描期） |
| ---: | ---: | ---: | ---: |
| 100 | 90 | 90 | **0** |
| 500 | 450 | 450 | **0** |
| 1,000 | 900 | 900 | **0** |
| 4,096 | 3,686 | 3,686 | **0** |
| 5,000 | 13,500 | 4,500 | **0** |
| 10,000 | 27,000 | 9,000 | **0** |
| 20,000 | 54,000 | 18,000 | **0** |

读法：

- **Before 列 = 整个测量块的编译次数**（`re._compiler.compile`，两层缓存都未命中），
  在 4097 以上严格等于 `unique_patterns × 批数`。
- **After 扫描期恒为 0**：`CompiledGlossary` 已经把全部 pattern 编译完，扫描只做 `search`。
  这一列与 G、批数、4096 边界**全部无关**——这就是 §13 "steady state regex compile count
  不再随 `segments × terms` 增长"的直接证据。
- After 的 build 列恒等于 `unique_patterns`（G=20000 → 18,000 = 恰好每个 unique pattern 一次）。
- G=20,000 时总量 54,000 → 18,000（3×，= 批数）；把批数换成真实文档的 `n/S`
  （n=10,000 / batch=4 → 2,500 批）就是 **54,000 → 18,000 而 Before 是 4,500 万**。

---

## §9 重复 `normalize_glossary` 审计

按 §9 逐调用链盘点，只处理**明确的重复纯函数工作**，不动架构：

| 位置 | 审计结果 | 处置 |
| --- | --- | --- |
| `check_translation_batch` 的逐段循环 | **重复**：`check_glossary_compliance` 每个 segment 都自己 `normalize_glossary(glossary)` → 一批 4 段就是 4 次 O(G) 归一化 + 4 轮全表扫描 | 新增**可选** kwargs `entries=` / `matcher=`，`core.check_translation_batch` 提到循环外预派生一次 |
| `select_glossary_for_segments` | 每批一次，批次间 glossary 不变但批次是独立调用 | 已编译视图按 revision 复用；normalize 保持每批一次（不跨批缓存 normalize：缺可证明的 revision token，见 §4） |
| `_review_glossary_scope`（review batch） | Phase 7 已把 normalize/hash/scan 去重为各一次 | 本轮只把扫描换到已编译视图，不重复 Phase 7 的工作 |
| `stale_segments_for_glossary` | 每次调用一次，但内层是 `pairs × G` | 仅换 matcher，不重构 |

`entries=` 不传时行为与历史**完全一致**（默认 None）。等价性验证：批次级调用与逐段
`entries=`/`matcher=` 预派生调用的输出逐条相同（含 4 条真实 finding 的夹具）。

> **没有**顺手做跨批 normalize 缓存：它需要 §4 结论里那个"可证明的内容修订号"，
> 否则就是拿正确性换速度。Phase 7 已把这条记为下一轮前置条件。

---

## §10 Memory Cost

| G | unique patterns | build CPU s | matcher retained（`sys.getsizeof` 上界） | build 期 RSS 增量（实测） | 实测 B/pattern | 进程 peak RSS MB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 90 | 0.011 | 123 KiB | 0.2 MB | 2,277 | 24.3 |
| 500 | 450 | 0.058 | 617 KiB | 0.5 MB | 1,139 | 23.8 |
| 1,000 | 900 | 0.118 | 1,226 KiB | 0.6 MB | 683 | 26.9 |
| 4,096 | 3,686 | 0.475 | 5,029 KiB | 3.0 MB | 852 | 44.2 |
| 5,000 | 4,500 | 0.579 | 6,139 KiB | 1.7 MB | 387 | 44.5 |
| 10,000 | 9,000 | 1.170 | 12,290 KiB | 4.9 MB | 570 | 58.6 |
| 20,000 | 18,000 | 2.445 | 24,460 KiB | **10.8 MB** | **615** | 111.9 |

出处：`tmp/phase8/micro_after_<G>.json`（每档独立进程、**tracemalloc 开启**、repeat=3）。

第二遍（**tracemalloc 关闭、逐档独立进程**，`tmp/phase8/micro_after.json`）——tracemalloc
自身会给每个分配加追踪开销，所以主表最后一列的 peak RSS 是**被抬高的**，下面这组更接近
生产：

| G | unique patterns | build CPU s | matcher retained | 进程 peak RSS MB |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 90 | 0.011 | 123 KiB | 23.9 |
| 500 | 450 | 0.058 | 617 KiB | 25.0 |
| 1,000 | 900 | 0.115 | 1,226 KiB | 26.4 |
| 4,096 | 3,686 | 0.479 | 5,029 KiB | 36.6 |
| 5,000 | 4,500 | 0.580 | 6,139 KiB | 42.2 |
| 10,000 | 9,000 | 1.159 | 12,290 KiB | 55.8 |
| 20,000 | 18,000 | 2.866 | 24,460 KiB | **89.8** |

两遍的 `retained` 列完全一致（这是纯结构量，与 tracemalloc 无关），build CPU 也在噪声内。

结论：

- **增长是线性的 O(patterns)**。最可信的斜率取自**最大区间**（G=1,000→20,000，
  RSS 增量 0.6→10.8 MB / 900→18,000 patterns）：**≈625 B / pattern**。
  单档比值在小 G 上被 `rss_delta` 的量化精度（~0.05 MB）主导，所以那几行的
  "B/pattern"（2,277 / 1,139）**不可当作每 pattern 成本**——它们主要是一次性的
  解释器 arena 增长。
- `matcher retained` 列用 `sys.getsizeof` 逐对象求和，**各档恒定 ≈1,400 B / pattern**，
  而实测摊薄后是 **387–852 B / pattern**，即**高估 1.6–3.6×**，因此它是严格**上界**。
  引用内存时以"build 期 RSS 增量"与上面的 625 B/pattern 为准。
- 与旧路径对比（**同为 tracemalloc 开启、可直接比较**）：G=20,000 时旧路径 peak 69.7 MB
  （夹具 + 4096 条 LRU 封顶），新路径 111.9 MB（夹具 + 18,000 条 pattern）。
  **新路径在 G > 4096 时确实更占内存**（tracemalloc 开启时 +42 MB；关闭时新路径为
  89.8 MB，即真实增量更小）。换来的是消掉 Θ(G × n/S) 的重编译。这笔交易在大术语表上
  是明显划算的，但必须如实记录：**这不是一个"零成本"的改动**。
- **不存在 `O(G × segments)` 或 `O(G × batches)` 的缓存**：视图只按 revision 存 pattern，
  与批数、段数、文档长度无关。
- 边界情形：一张 glossary 自身就超过 `_COMPILED_CACHE_MAX_PATTERNS`（60,000）时，
  内存下界就是 glossary 本身（G=100,000 → 约 60 MB 量级）。这是 O(G) 而非失控，
  但若未来出现这种规模，应先调 `run_regression` 之外的压测再决定是否分批编译。

---

## §11 Lifetime

`_COMPILED_CACHE`（`terminology.py`）：

- **有界**：最多 `8` 个 revision，且 pattern 总数不超过 `60,000`；超出按 LRU 淘汰。
- **键是内容修订号**，不是对象身份，所以"同一个 glossary 被反复取用"是命中，
  "换了 glossary"是重建，不存在"指针相同但内容变了"的窗口。
- **有明确的显式生命周期钩子**：`reset_compiled_glossary_cache()`（测试与 stage 收尾用），
  `compiled_glossary_cache_info()` 暴露 revisions / patterns / builds / hits / evictions。
- **不是 process-global 无界 dict**，也没有"因为生命周期难做就退回小 LRU"。
- 单张 glossary 本身超出 60,000 pattern 时仍会保留（淘汰循环保留最后一个 revision）：
  这是有意的——正确性优先于内存，且该情形下界就是 glossary 自身规模（O(G) 是 §10 的目标，
  不是违规）。

为什么用"进程级有界 revision 缓存"而不是"显式 stage owner"：owner 需要把对象穿透
~10 个调用点（含 Phase 7 的 review 路径），那正是 §9 禁止的架构重构；有界 revision
缓存以 O(1) 的调用方改动达成同样的"stage/revision 作用域"效果。

---

## §12 旧 LRU 的去留（用户 §14）

**保留，但已降级为文档化的 fallback。**

- 主 translation / review 路径（§3.1 六处）已全部改走 `CompiledGlossary`，
  **不再依赖 4096 LRU 保证性能**。
- `_term_pattern` + `_TERM_PATTERN_CACHE` 继续服务：ad-hoc / 单发调用、
  `term_matches` 的向后兼容路径、以及 `CompiledGlossary.matches` 的**表外字符串回落**。
- `terminology.py` 的 `_TERM_PATTERN_CACHE` 注释已改写，明确写出"它是按 term 键控 + 固定
  容量，工作集是整张术语表，超容量即每轮重编译"这一事实，并指向本报告。
- 反向守卫测试 `test_legacy_lru_path_still_thrashes_above_capacity` 把容量压到 64、
  用 300 个 term 扫 3 轮，断言编译数 **恰为 900**（= 300 × 3）——把"这条 cliff 是真实的、
  以及它现在只存在于 fallback 路径"两件事同时钉住。

---

## §13 Project Memory 本轮只观察（用户 §15）

按要求只记录，不实现任何 Project Memory 索引 / pair revision / memory DB / schema
改动。Phase 8 之后 review batch 的术语派生相位（G=1000 ctx-heavy，280 批）：

| phase | Phase 7（扫描走 LRU） | Phase 8（扫描走 compiled） |
| --- | ---: | ---: |
| `review_input_memory_normalize` | 1.055 s | 1.013 s |
| `review_input_memory_hash` | 2.534 s | 2.853 s |
| `review_input_memory_compile` | —（无此相位） | **0.193 s**（新增，一次性 build） |
| `review_input_memory_scan` | 1.673 s | **1.720 s** |
| `review_input_memory_project` | 0.003 s | 0.004 s |
| `review_input_project_memory`（父） | 5.272 s | 5.796 s |
| `review_input_packet_build` | 5.462 s | 6.023 s |

命令与口径与 Phase 7 完全一致（`--segments 1000 --glossary-size 1000`，280 批，
`items=280000` / scan `items=1000000`）：

```bash
./venv/bin/python scripts/phase6_batch_scaling.py \
    --segments 1000 --glossary-size 1000 --json tmp/phase8/ctx_phase8.json
```

**这两列基本持平**（整轮 `wall=32.99s cpu=22.87s`），差异量级与本轮新增的
`memory_compile` 相位（0.193 s = 280 批 × 0.68 ms 的一次性 build）同阶。
这不是"优化没生效"，而是 G=1000 本来就**在 4096 以下、没有 cliff 可消**——
与 §12 工作负载在 G=1000 时 legacy/compiled 几乎无差（1.00× / 0.95×）是同一个事实
的两次观测。cliff 只在 `G > 4096` 存在。

`_scoped_project_memory` 的**剩余增长项**（Phase 7 已记录、本轮未动）：
`normalize` + `hash` 随 G 走（G=1000 时占该 bucket 约 65%），真正的跨批可省项；
前置条件是给 glossary 一个可证明的内容修订号。**留待 Phase 9 重新排序后再决定。**

---

## §14 A/B —— CPU / Wall（用户 §12）

两组独立证据。**都是同进程、同 fixture、同 provider，只切术语匹配实现**：
`legacy` = 把 `terminology.compiled_glossary` 换成 legacy 委托对象（`.matches()` 直接调
`term_matches`）；`compiled` = 真实代码。

### 14.1 Translation workload（复用真实 `translate_stage`，`tmp/phase8/workload_ab.py`）

| n | G | legacy CPU | compiled CPU | 提速 | legacy 编译尝试次数 | compiled |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 100 | 1,000 | 2.015 s | 2.005 s | 1.00× | 1,000 | build **1** × 1,000 |
| 100 | 10,000 | **396.045 s** | **21.554 s** | **18.4×** | 2,850,000 | build **1** × 10,000 |
| 1,000 | 1,000 | 18.991 s | 19.938 s | 0.95× | 1,000 | build **1** × 1,000 |
| 1,000 | 10,000 | **未测**（按 §12 主动中止；外推 ≈3,960 s ≈ 66 min） | **190.934 s** | ≈20.7×（外推比） | — | build **1** × 10,000 |

读法：

- **`G ≤ 4096` 两档几乎无差（1.00× / 0.95×）**——那一档没有 cliff 可消。这是一个干净的
  自然对照：收益只在超容量时兑现。`n=1000/G=1000` 行的轻微回退来自 §4 的键派生
  （G=1,000 时 0.54 ms/次 × 每批常数次），量级与新增的 `memory_compile` 相位同阶。
- **`G = 10,000` 兑现全部收益**：n=100 时 **18.4×**，而这一档的 legacy 是
  2,850,000 次编译尝试 → compiled 是 **1 次 build**。
- **`n = 1,000 / G = 10,000`**：legacy 的外推依据是"编译尝试次数随批数线性增长
  （n=100 时 2.85 M）"，即 n=1,000 → ≈28.5 M → CPU ≈ 396 s × 10 ≈ 3,960 s。
  **这是外推，不是实测**（任务里明确允许不为一个 synthetic wall number 等几十分钟）。
  compiled 侧是实测：**190.934 s**，且 `build = 1 × 10,000`——**1,000 段只 build 一次**，
  这就是"摊销"的直接证据。
- 所有 compiled 行的 `misses` **全为 0**：整个工作负载没有触碰过 term 级 LRU。

### 14.2 Micro benchmark（合成，直接数编译次数）

| G | Before `sre_compiles` | After 扫描期 | After build（一次性） | `compiles / unique_pattern` |
| ---: | ---: | ---: | ---: | --- |
| 100 | 90 | **0** | 90 | 1.00 → **1.00** |
| 500 | 450 | **0** | 450 | 1.00 → **1.00** |
| 1,000 | 900 | **0** | 900 | 1.00 → **1.00** |
| 4,096 | 3,686 | **0** | 3,686 | 1.00 → **1.00** |
| 5,000 | 13,500 | **0** | 4,500 | **3.00 → 1.00** |
| 10,000 | 27,000 | **0** | 9,000 | **3.00 → 1.00** |
| 20,000 | 54,000 | **0** | 18,000 | **3.00 → 1.00** |

**Before 侧的比值 3.00 是"批数"**，在真实文档里换成 `n / batch_size`——
所以 Before 是 `Θ(G × n/S)`，After 是 `O(G)`。这就是 cliff 的形状。

> 内存代价见 §10；本轮的收益不是免费的：G=20,000 时进程 peak RSS
> 69.7 MB → 89.8 MB（tracemalloc 关闭、分进程实测）。

---

## §15 Validation（用户 §16）

### 15.1 定向测试（逐文件独立进程 + 每文件私有 `TMPDIR`）

| 文件 | 结果 |
| --- | --- |
| `terminology_compiled_glossary_test.py` | **18 passed** |
| `terminology_governance_test.py`（glossary governance） | **32 passed** |
| `terminology_match_cache_test.py` | **5 passed** |
| `project_context_hierarchy_test.py`（context） | **23 passed** |
| `context_target_selection_equivalence_test.py`（semantic equivalence） | **11 passed** |
| `translation_runtime_test.py` | **31 passed** |
| `runtime_state_test.py` | **16 passed** |
| `reviewer_overlap_semantic_equivalence_test.py`（reviewer/evidence） | **17 passed** |
| `review_scheduler_test.py` | **10 passed** |
| `tm_promotion_persistence_test.py`（TM） | **12 passed** |
| `legacy_case_recovery_test.py`（recovery） | **11 passed** |
| `project_memory_test.py` | **54 passed** |

另有两条门禁（§6 / §7）与两条基准（§1 / §12）全部按预期通过。

### 15.2 全量回归

```bash
$ ./venv/bin/python scripts/run_regression.py --tsv tmp/phase8/regression_after.tsv
```

```
测试文件总数        : 98
  pytest 风格       : 97
  脚本式 smoke      : 1
pytest cases 通过   : 1163
有效失败文件        : 3
总耗时              : 29m39s
失败文件：context_knowledge_ui_test.py, language_assets_workspace_test.py,
          project_task_navigation_test.py
```

（`tests/app_boot_test.py` 整应用冒烟 `exit=0`。）

### 15.3 那 3 个失败文件是不是我引入的 —— **不是**

三条互相独立的对照，失败集合**逐条相同**：

| 文件 | 全量回归 | 控制组（不带插件） | 中立化（Phase 8 关闭） |
| --- | --- | --- | --- |
| `context_knowledge_ui_test` | 1 failed | 1 failed（同一条） | 1 failed（同一条） |
| `language_assets_workspace_test` | 2 failed | 2 failed（同两条） | 2 failed（同两条） |
| `project_task_navigation_test` | 2 failed | 2 failed（同两条） | 2 failed（同两条） |

中立化做法：`tmp/phase8/neutralise_phase8.py` 作为 pytest 插件，在导入期把
`terminology.compiled_glossary` 换成 legacy 委托对象，于是所有调用点退回旧路径
（`-p neutralise_phase8`）。**按要求：失败集合逐条相同 ⇒ 既存失败。**

判定依据不是"看起来无关"，而是失败内容本身：

| 失败 | 症状 | 与 terminology 的关系 |
| --- | --- | --- |
| `context_knowledge_ui_test::test_context_and_knowledge_surfaces_rerun_without_widget_identity_errors` | `AssertionError`，"加入项目" 文案断言 | 无。文案属并行工作流的 UI 改动 |
| `language_assets_workspace_test::test_empty_states_for_all_three_tabs` | `assert '当前项目还没有术语' in page` | 无。同样是对 UI 文案/空状态的断言 |
| `language_assets_workspace_test::test_quick_accept_saves_to_project_termbase` | 同族空状态断言 | 无 |
| `project_task_navigation_test::test_overview_recent_tasks_are_capped_at_five` | `KeyError: 'overview_all_tasks_<uuid>'` | 无。widget key 由 `outputs/projects` 里的项目 UUID 派生 → 依赖 `outputs/` 状态 |
| `project_task_navigation_test::test_overview_view_all_switches_to_tasks_tab` | 同上 | 无 |

> 这 3 个文件都是 Streamlit `AppTest` UI 测试，依赖 `outputs/` 真实状态与 UI 文案。
> 工作区里同时存在并行工作流的未提交 UI 改动（§15.5），所以它们红着是**预期内**的；
> 真正需要担心的是"Phase 8 改了匹配结果导致它们红"——已被上面三组对照排除。

**环境观察（不是回归）**：同一文件在不同负载下耗时差异可达 13×
（`context_knowledge_ui_test` 4.75 s ↔ 62.85 s，`language_assets_workspace_test`
39.5 s ↔ 230.6 s）。这是沙箱的 AppTest 文件 IO 开销（记忆里记过 5–12×），
所以**不要用 AppTest 耗时判断回归**。

### 15.4 收尾检查（§16）

| 检查 | 结果 |
| --- | --- |
| `git diff --check`（全仓） | **干净**（无行尾空白 / 文件末尾多余空行） |
| 尾随空白（本轮改动 + 新增 9 个文件） | **全部 0 处** |
| `py_compile`（本轮改动 + 新增） | **全部通过** |
| brand regression | `brand_assets_test` **13 passed**、`brand_compatibility_test` **2 passed**、`brand_regression_test` **2 passed** |
| 整应用冒烟 | `app_boot_test` **exit=0** |
| **outputs integrity** | **INTACT** —— 任务与项目记录的语义哈希逐条相同；连字节数都未被重写（0/1） |

outputs 比对口径：`tmp/phase8/outputs_snapshot.py` 对每个 `outputs/*/state.json` 取
**语义哈希**（`json.dumps(sort_keys=True)` 的 sha256），而不是文件字节/mtime——
因为跑全量回归会就地重写真实 `state.json`（mtime 变、语义不变）。

### 15.5 本轮改动范围（不能靠 `git diff` 界定）

`git diff --stat` 会给出误导性的范围，因为**同一个工作区里叠着至少三条未提交工作流**：

| 工作区里的未提交内容 | 归属 | 与本轮的关系 |
| --- | --- | --- |
| `terminology.py` 的 `_term_pattern` / `_TERM_PATTERN_CACHE`（4096 LRU） | **Phase 5**（HEAD 里根本没有这两个符号——HEAD 的 `term_matches` 是每次直接 `re.compile`） | 本轮测的 "Before" 就是它 |
| `terminology.py` 的 `extract_auto_terms_v2` 一带 7 处 hunk | **并行工作流**（PDF 摄取 / 自动术语） | 与本轮无关 |
| `review_packet.py` / `translation_evidence.py` 里的 `review_input_truth` / `review_input_glossary_resolve` / `review_input_bounded` / `glossary_entries=` 等相位与参数 | **Phase 7** | 与本轮无关 |
| `app.py` / `folith/ui/styles.py` 等 UI 改动 | 并行工作流 | 与本轮无关 |

**本轮（Phase 8）实际改动的文件**：

| 文件 | 改动 |
| --- | --- |
| `folith/terminology.py` | `term_matches` **之后**新增 `_sources_revision` / `CompiledGlossary` / `compile_glossary` / `_COMPILED_CACHE` / `compiled_glossary` / `compiled_glossary_cache_info` / `reset_compiled_glossary_cache`；改写 `_TERM_PATTERN_CACHE` 注释；4 个调用点换 matcher（`select_glossary_for_segments`、`stale_segments_for_glossary`、`check_glossary_compliance`、`detect_glossary_conflicts`） |
| `folith/critical_path.py` | 在 `REVIEW_PACKET_PHASES` 里插入 `"review_input_memory_compile"` |
| `folith/translation_evidence.py` | `_review_glossary_scope` 内新增该相位 + 扫描换 `matcher.matches` |
| `folith/translation_core/review_packet.py` | `_relevant_glossary` 换 matcher |
| `core.py` | `check_glossary_compliance` 增加可选 `entries=` / `matcher=` 透传；`check_translation_batch` 循环外预派生一次 |
| **新增** | `scripts/phase8_terminology_pattern_scaling.py`、`scripts/phase8_terminology_differential_gate.py`、`tests/terminology_compiled_glossary_test.py`、`docs/terminology-pattern-scaling.md`(+`-evidence.md`)、`tmp/phase8/*` |

`scripts/translation_setup_equivalence_gate.py` 的改动只有 §7.1 那处**捕获点修正**。

> 因此**不能**用 `git add -A` 或按文件批量提交：那样会把 Phase 5 / Phase 7 / 并行
> 工作流的改动一起卷进本轮提交。提交范围需要逐条与用户商定（记忆里的仓库纪律）。

---

## §16 Remaining Critical Path（用户 §17 · 重新排名）

口径：§13 的 ctx-heavy 复测（`--segments 1000 --glossary-size 1000`，280 批，
整轮 `cpu=22.87 s`）。这是**合成** profile（G=1000 在 4096 以下，因此本轮改动在这一档
本来就无收益可拿），但它给出了各剩余的**可加性**项：

| 排名 | 项 | 实测 | 占整轮 CPU | 形状 | 是否值得下一步动 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | **Project Memory 检索派生**（`normalize_glossary` + `glossary_hash` + 全表扫描） | **5.796 s** | **25.3%** | `批数 × G log G` | **唯一**的 `O(G × n/S)` 剩余项 |
| 1a | └ `review_input_memory_hash` | 2.853 s | 12.5% | `批数 × G log G` | 是（同一件事的 49%） |
| 1b | └ `review_input_memory_scan` | 1.720 s | 7.5% | `批数 × G × S` | 需按需剪枝才有下界 |
| 1c | └ `review_input_memory_normalize` | 1.013 s | 4.4% | `批数 × G` | 是（同一 memo 可覆盖） |
| 2 | 真实 provider 延迟 | 未测（合成 profile 用 fake provider） | — | 网络 | 只在真实运行时才是主项，不是"计算优化" |
| 3 | `review_input_previous_context`（上一轮上下文扫描） | **0.061 s** | **0.27%** | `批数 × 上下文窗口` | **不要动**：Phase 7 之后它是噪声 |
| 4 | OCR / PDF 摄取 | 不在本 profile 内 | — | 独立管线 | 不要动：与本 critical path 无交集 |
| 5 | `eval/*` 的三处**同形放大器**（§3.1） | 未测（离线路径） | — | `G × n` | **已知欠账**：把 eval 跑在大术语表 + 长文档上会重现同一条 cliff；不在 production 路径 → 低优先，但别忘 |

排名依据是**形状**而不是绝对秒数：只有第 1 项是 `O(G × n/S)` 的缩放项（文档越长、
术语表越大就越贵）；第 3 项虽然也在同一 profile 里，但已经小到 0.27%，继续投入是负收益。

### 下一项最佳优化（只选一项）

**Project Memory 检索派生：把 `normalize_glossary` + `glossary_hash` 做成按 glossary
revision 键控的派生视图**（与本轮 `CompiledGlossary` 同形）。

- **收益**：在上面的 profile 里覆盖 **25.3%** 的整轮 CPU。其中同一次 memo 能覆盖的是
  `normalize` + `hash` = 1.013 + 2.853 = **3.866 s**（17%）；剩下的 `scan` 1.720 s
  需要另做按需剪枝才动得了。两者都随批数线性增长——与刚修掉的 cliff 是同一类缩放项。
- **前置条件（Phase 7 点名的那条）现在有了进展但**不**是直接可用**：
  `_sources_revision` 只覆盖**被匹配字符串**（`source`/`preferred`/`target`），而
  `glossary_hash` 覆盖**全部条目字段**（含 `behavior`/`status`/`evidence`/`occurrences`）。
  两者依赖面**不同宽**，所以**不能**拿本轮的键去 memo `glossary_hash`——那会返回过期哈希。
  需要的是一个**全条目**的内容修订号（同一套 `sha1` 手法，只是投影换成全字段）。
- **为什么本轮不做**：§15 明确 "Project Memory 本轮只观察"，且 §9 禁止顺手扩大重构范围。
- **做法建议**：在**同一处**（`_review_glossary_scope`）把 `(normalize, hash)` 收进一个
  全条目 revision 键控的 memo，仍以 `state` 生命周期为界、有界淘汰，不改 state schema。

### 对用户原话问题的直接回答

> **Folith 的 terminology matching 是否已经从"pattern cache 容量决定性能、一旦 glossary
> 超容量就反复 regex compile"变成"每个 glossary revision 每个 unique pattern 只编译一次"
> 的稳定模型？**

**YES。**

三条互相独立的证据：

1. **编译数与容量、批数、G 全部解耦**：扫描期 `sre_compiles` 在
   `G ∈ {100, 500, 1,000, 4,096, 5,000, 10,000, 20,000}` **全档恒为 0**，
   而 build 恰好等于 `unique patterns`（90 / 450 / 900 / 3,686 / 4,500 / 9,000 / 18,000），
   `compiles / unique_pattern` **全档 1.00**。
2. **cliff 消失**：`G=4097` 的 `3.00×` 跳到 `1.00×`（§1.1 的"一个 term 之差 +178%"）
   在 after 侧不再存在——不是被推后，是那条曲线没有了。
3. **稳定模型是靠"换工作单元"而不是"换容量"得到的**：容量仍是 4096，`_TERM_PATTERN_CACHE_MAX`
   一行未改（§1.1 的边界精扫用的还是同一个值）。旧 LRU 现在只是 fallback（§12）。

因此满足用户设的收口条件，Phase 8 可以宣布完成。
