# Review Context Scaling — evidence appendix (Phase 7)

本文件是 [`translation-review-context-scaling.md`](translation-review-context-scaling.md) 的原始证据。
所有数字都是**实测**，且标注了统计口径（min-of-N / CPU vs wall / 确定性计数）。

## E0. 复现命令

```bash
# before（在改动之前捕获；本文件的 before 一律指这一份）
venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase7/equiv_before.json
venv/bin/python scripts/translation_critical_path_audit.py --baseline --sizes 1000,5000,10000 \
    --json tmp/phase7/breakdown_before.json          # -> tmp/phase7/before.log

# after
venv/bin/python scripts/translation_critical_path_audit.py --baseline --sizes 1000,5000,10000 \
    --json tmp/phase7/breakdown_after.json           # -> tmp/phase7/after.log
venv/bin/python scripts/phase7_memory_scope_scaling.py \
    --glossary-sizes 100,1000,10000 --source-sizes 4,16,64,256 \
    --repeat 3 --extrapolate 10000 --batch-size 4 \
    --json tmp/phase7/memory_scope_after.json        # -> tmp/phase7/memory_scope.log

# 等价
venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase7/equiv_before.json
TMPDIR=$(mktemp -d) venv/bin/python tmp/phase7/scope_equiv.py

# 回归
venv/bin/python scripts/run_regression.py --tsv tmp/phase7/regression_after.tsv
```

**注意**：`before.log` 里的埋点与 `after.log` 完全相同（`REVIEW_PACKET_PHASES` 13 项 + `objects` 指标
是在 before 基线**之前**就加好的），所以 before/after 的相位名与口径可逐行对照。

---

## E1. §1 复核：n=10000 的完整 review / commit / IO 相位表

### before（`tmp/phase7/before.log`，`[baseline_n10000]`，2800 批，run total wall 119.7348 s / CPU 49.4034 s）

```
  review phases
    phase                                 wall_s     cpu_s    wait_s   calls        items      objects      chars
    review_input_previous_context          4.974     4.954     0.019    2800     13998600            0          0
    review_input_state_copy                0.067     0.066     0.000    2800     13998600            0          0
    repair_shadow_state_copy               0.000     0.000     0.000       0            0            0          0
    review_input_evidence_index            0.211     0.210     0.001    2800     14008600            0          0
    review_input_packet_build             11.622    11.568     0.054    2800            0            0          0
    reviewer_prompt_assemble               0.071     0.071     0.000    2800        10000            0          0
    review_input_pair_prefix               7.018     6.987     0.031    2800     13998600     13998600          0
    review_input_project_memory            0.171     0.170     0.001    2800            0            0          0
    review_input_memory_normalize          0.007     0.007     0.000    2800            0            0          0
    review_input_memory_hash               0.047     0.047     0.000    2800            0            0          0
    review_input_memory_scan               0.003     0.003     0.000    2800            0            0          0
    review_input_memory_project            0.045     0.045     0.000    2800            0            0          0
    review_input_review_packet_core        3.811     3.793     0.018    2800        10000            0          0
    review_input_bounded                   3.595     3.579     0.016    2800        28000            0          0
    review_input_truth                     0.008     0.008     0.000    2800            0            0          0
    review_input_glossary_resolve          0.013     0.013     0.000    2800            0            0          0
    review_input_relevant_glossary         0.007     0.007     0.000    2800            0            0          0
    review_input_memory_deepcopy           0.030     0.030     0.000    2800            0         5600          0
    review_input_fingerprint               0.071     0.071     0.000    2800            0            0          0
    reviewer_transport                     0.065     0.064     0.000    2800            0            0     114800
    reviewer_parse                         0.033     0.033     0.000    2800            0            0          0
    reviewer_findings_normalize            0.010     0.010     0.000    5600            0            0          0
                                          31.879     <- 该列是"同表各行相加"，含嵌套重复，不可当 bucket 总量
```

### after（`tmp/phase7/after.log`，同一 fixture）

```
  review phases
    phase                                 wall_s     cpu_s    wait_s   calls        items      objects      chars
    review_input_previous_context          4.185     4.185     0.000    2800     13998600            0          0
    review_input_state_copy                0.052     0.052     0.000    2800     13998600            0          0
    repair_shadow_state_copy               0.000     0.000     0.000       0            0            0          0
    review_input_evidence_index            0.195     0.195     0.000    2800     14008600            0          0
    review_input_packet_build              3.267     3.267     0.000    2800            0            0          0
    reviewer_prompt_assemble               0.055     0.055     0.000    2800        10000            0          0
    review_input_pair_prefix               0.008     0.008     0.000    2800         5598        15598          0
    review_input_project_memory            0.080     0.080     0.000    2800            0            0          0
    review_input_memory_normalize          0.004     0.004     0.000    2800            0            0          0
    review_input_memory_hash               0.028     0.028     0.000    2800            0            0          0
    review_input_memory_scan               0.003     0.003     0.000    2800            0            0          0
    review_input_memory_project            0.016     0.016     0.000    2800            0            0          0
    review_input_review_packet_core        3.090     3.090     0.000    2800        10000            0          0
    review_input_bounded                   2.932     2.933     0.000    2800        28000            0          0
    review_input_truth                     0.007     0.007     0.000    2800            0            0          0
    review_input_glossary_resolve          0.001     0.001     0.000    2800            0            0          0
    review_input_relevant_glossary         0.001     0.001     0.000    2800            0            0          0
    review_input_memory_deepcopy           0.023     0.023     0.000    2800            0         5600          0
    review_input_fingerprint               0.056     0.056     0.000    2800            0            0          0
    reviewer_transport                     0.043     0.043     0.000    2800            0            0     114800
    reviewer_parse                         0.018     0.018     0.000    2800            0            0          0
    reviewer_findings_normalize            0.008     0.008     0.000    5600            0            0          0
                                          14.073     <- 同上：含嵌套重复
```

**读法**：
- `packet_build` 的子相位是**嵌套**的：`pair_prefix` 与 `review_packet_core` 与 `project_memory`
  是它的直接子项（before 6.987 + 3.793 + 0.170 = 10.95 ≈ 11.568；after 0.008 + 3.090 + 0.080 = 3.178 ≈ 3.267）。
  `bounded` ⊂ `review_packet_core`，`memory_*` ⊂ `project_memory`。**不可跨层相加**，
  表尾那行 `31.879` / `14.073` 就是"全部相加"的产物，只用来对照，不当总量。
- after 的 `glossary_resolve` / `relevant_glossary` 变成 0.001 s 且 items 归零，是因为派生被移到了
  `_review_glossary_scope`（计入 `project_memory` 一行）——**工作是搬家 + 减半，不是消失**。

### before（`[scale_n1000]`，G=1000 上下文重场景，280 批）

```
    review_input_packet_build             14.316    14.247     0.069     280            0            0          0
    review_input_pair_prefix               0.138     0.137     0.001     280       139860      139860          0
    review_input_project_memory            9.006     8.960     0.045     280       280000            0          0
    review_input_memory_normalize          0.882     0.877     0.005     280       280000            0          0
    review_input_memory_hash               2.516     2.498     0.018     280       280000            0          0
    review_input_memory_scan               1.624     1.617     0.007     280      1000000            0          0
    review_input_memory_project            3.935     3.919     0.016     280       280000            0          0
    review_input_review_packet_core        5.148     5.126     0.022     280         1000            0          0
    review_input_bounded                   0.118     0.117     0.001     280         2800            0          0
    review_input_glossary_resolve          3.355     3.341     0.014     280       280000            0          0
    review_input_relevant_glossary         1.612     1.605     0.007     280      1000000            0          0
```

重复识别：`normalize` 0.877（`project_memory` 内）+ 含在 `glossary_resolve` 3.341 里的同一份；
`hash` 2.498 + 同上；`G×S` 扫描 1.617（items 1,000,000）+ 1.605（items 1,000,000）。
合计重复 ≈ (0.877+2.498+1.617) + (3.341 中的 normalize+hash ≈ 1.4) + 1.605 ≈ **8.0 s / 14.247 s ≈ 56%**。
另有 `project_memory_from_entries` 默认路径先建全量 `terminology_refs` 再被 locked 子集覆盖（≈0.55 s 白做）。

---

## E2. §1/§4：`pair_prefix` 的三档 n（确定性指标）

| n | 批数 | before items | before objects | after items | after objects | before CPU | after CPU |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | 280 | 139,860 | 139,860 | 558 | 1,558 | 0.0790 | 0.0010 |
| 5000 | 1400 | 3,499,300 | 3,499,300 | 2,798 | 7,798 | 1.3030 | 0.0040 |
| 10000 | 2800 | 13,998,600 | 13,998,600 | 5,598 | 15,598 | 6.9870 | 0.0080 |

`items` 的增长指数（`t ~ n^k`，用相邻两档反推）：

```
before:  log(3499300/139860)/log(5)  = 2.0002     (1000 -> 5000)
         log(13998600/3499300)/log(2) = 2.0001     (5000 -> 10000)     => k = 2.000
after :  log(2798/558)/log(5)        = 1.0004     (1000 -> 5000)
         log(15598/2798)/log(2)       = 2.4789     (5000 -> 10000)     => 见下
```

after 的每批均值：558/280 = **1.99**，2,798/1,400 = **2.00**，15,598/2,800 = **5.57**。
第三档的 5.57 是**该 fixture 的依赖集略宽**（`_dependency_segment_ids` 命中更多 committed 索引），
不是随 n 增长。用固定 `batch_len=4` 直接驱动 `core._runtime_review_context` 复核：

| n | offset=0 | offset=4 | offset=40 | offset=400 | offset=4000 | offset=n-4 |
| --- | --- | --- | --- | --- | --- | --- |
| 1000 | touched=0 | 2 | 2 | 2 | — | 2 |
| 10000 | touched=0 | 2 | 2 | 2 | 2 | 2 |

即修复后的每批对象数是**与 n 无关的小常数**（上界 = `|batch| + |有界依赖集|`），
上表的 5.57 只是 fixture 差异，不构成 O(n) 项。**结论按总 items/objects 与 objects 量级差陈述，不按该常数陈述。**

---

## E3. §7 G×S 矩阵（`tmp/phase7/memory_scope.log`，min-of-3，同进程 A/B）

### 整个 review batch：pre-Phase-7 vs Phase 7

```
       G     S       G*S |  legacy cpu |  phase7 cpu |  speedup |    norm    hash  term checks
     100     4       400 |    2.658 ms |    1.333 ms |    1.99x |    2->1    2->1     800->400
     100    16      1600 |    5.413 ms |    2.706 ms |    2.00x |    2->1    2->1   3200->1600
     100    64      6400 |   16.409 ms |    8.235 ms |    1.99x |    2->1    2->1  12800->6400
     100   256     25600 |   61.693 ms |   31.087 ms |    1.98x |    2->1    2->1  51200->25600
    1000     4      4000 |   26.745 ms |   13.341 ms |    2.00x |    2->1    2->1   8000->4000
    1000    16     16000 |   52.960 ms |   26.390 ms |    2.01x |    2->1    2->1  32000->16000
    1000    64     64000 |  157.860 ms |   79.240 ms |    1.99x |    2->1    2->1 128000->64000
    1000   256    256000 |  582.508 ms |  292.120 ms |    1.99x |    2->1    2->1 512000->256000
   10000     4     40000 | 3027.294 ms | 1554.359 ms |    1.95x |    2->1    2->1  80000->40000
   10000    16    160000 | 3357.908 ms | 1640.021 ms |    2.05x |    2->1    2->1 320000->160000
   10000    64    640000 | 4440.422 ms | 2187.566 ms |    2.03x |    2->1    2->1 1280000->640000
   10000   256   2560000 | 8701.799 ms | 4378.040 ms |    1.99x |    2->1    2->1 5120000->2560000
```

`norm` / `hash` / `term checks` 三列是**确定性计数**（包装 `models.normalize_glossary`、
`models.glossary_hash`、`terminology.term_matches` 计数），不受沙箱负载影响 —— 这是本轮
"同一份不可变数据被派生两次"的**直接证据**，比 CPU 更难反驳。

### `_scoped_project_memory` 单次调用子相位

```
       G     S        G*S         wall          cpu   norm   hash      terms   scan items
     100     4        400     1.328 ms     1.328 ms      1      1        400          400
     100    16       1600     2.679 ms     2.678 ms      1      1       1600         1600
     100    64       6400     8.046 ms     8.046 ms      1      1       6400         6400
     100   256      25600    30.193 ms    30.193 ms      1      1      25600        25600
    1000     4       4000    13.614 ms    13.604 ms      1      1       4000         4000
    1000    16      16000    26.576 ms    26.571 ms      1      1      16000        16000
    1000    64      64000    79.242 ms    79.193 ms      1      1      64000        64000
    1000   256     256000   291.790 ms   291.718 ms      1      1     256000       256000
   10000     4      40000  1361.047 ms  1358.603 ms      1      1      40000        40000
   10000    16     160000  1624.787 ms  1624.546 ms      1      1     160000       160000
   10000    64     640000  2173.081 ms  2172.651 ms      1      1     640000       640000
   10000   256    2560000  4362.068 ms  4360.668 ms      1      1    2560000      2560000
```

`scan items` 严格等于 `G*S` → "每条目 × 每 source"**得到实测确认**（不是假设）。
`wall ≈ cpu` → 无阻塞，纯本地 CPU。

子相位拆分（单次调用，ms）：

```
       G     S   normalize        hash        scan     project         sum
     100     4       0.185       0.641       0.492       0.004       1.322
    1000     4       2.004       6.851       4.779       0.006      13.640
   10000     4      57.354      73.484    1374.472       0.017    1505.327
   10000    64      21.056     106.523    2118.635       0.011    2246.225
   10000   256      21.362      74.539    4261.197       0.011    4357.109
```

### 累计外推（n=10000、batch=4、2500 批）

```
       G  per-call cpu    calls   cumulative cpu
     100      1.328 ms     2500      3320.000 ms
    1000     13.604 ms     2500     34010.000 ms
   10000   1358.603 ms     2500   3396507.500 ms
```

### LRU 悬崖的直接测量

```
source chars: 95
cache info: {'size': 4096, 'max': 4096}
re.compile('AuditTerm1234') :     0.23 us     <- 同一 pattern 重复编译（命中 re 模块自身缓存）
term_matches warm     :     1.13 us          <- G <= 4096，模式缓存命中
term_matches thrash   :   127.28 us          <- 循环 10000 个不同术语
ratio thrash/warm     :   112.2x
```

`term_matches thrash` 那一行是**冷编译**的真实代价（`re` 模块自带的 512 条缓存与
`_TERM_PATTERN_CACHE` 的 4096 条同时被冲掉），对应 G=10000 时 `memory_scan` 的 34 µs/次。

---

## E4. §13 门禁输出

```
$ venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase7/equiv_before.json
warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead.

=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK
  glossary_wide_40: OK
  knowledge_candidates_40: OK
  long_chain_120: OK
  plain_40: OK
  sections_entity_60: OK
  single_batch_8: OK
  tm_hits_40: OK
```

`equiv_before.json` 的每个 fixture 都含 `capture.review_packets`（共 90 个 packet）：

| fixture | prompts | term_matches | review_packets |
| --- | --- | --- | --- |
| plain_40 | 30 | 0 | 10 |
| glossary_locked_40 | 20 | 520 | 10 |
| glossary_wide_40 | 30 | 16000 | 10 |
| sections_entity_60 | 80 | 720 | 16 |
| knowledge_candidates_40 | 30 | 480 | 10 |
| tm_hits_40 | 20 | 0 | 10 |
| long_chain_120 | 160 | 2797 | 32 |
| single_batch_8 | 3 | 0 | 1 |

### 同进程 neutralized A/B（`tmp/phase7/scope_equiv.py`）

```
seed=11 segments=64 batches=8: OK
seed=12 segments=40 batches=5: OK
seed=13 segments=12 batches=12: OK
seed=14 segments=96 batches=3: OK
RESULT: EQUIVALENT
```

每个 batch 断言 5 件事：
1. `build_review_packet` 收到预派生参数 vs 自行派生 → packet 全等（含 `input_fingerprint`）；
2. `blind=True` + `candidate_targets` 的覆盖路径 → packet 全等；
3. `_review_glossary_scope().memory` 与**逐字重写的历史实现** `historical_scoped_project_memory` 全等；
4. `scope.relevant_glossary` 与 `translation_core.review_packet._relevant_glossary(entries, truth)` 全等；
5. packet 的 `glossary.entries` 就是第 4 条那份；`terminology_refs` 恰为其 `locked` 子序列。

夹具含混合 status（locked / draft / candidate / confirmed / provisional / deprecated），
且**一半条目带与 batch 相交的 `occurrences` 但 status 非 locked** —— 正是
"packet glossary 收 all-status、terminology_refs 只收 locked"这条 split 的判定点。

---

## E5. §5 previous-target 复核

| 检查 | 结果 |
| --- | --- |
| 审校侧是否走 `select_target_context` | 是（`core.py:2807`），`limit=2` 原样保留 |
| 是否仍物化完整前缀 | **否**（Phase 5 已改；`context.py:591` 反向扫描，`_clean` 只对入选项执行） |
| 是否走 `full prefix → candidates → select last N` | 否 |
| previous-target contract | `state["pairs"]`（committed）→ caller 过滤 `{human_accepted, reviewed, tm_approved}` |
| 生成侧是否分开 | 是（`core.py:5106` 用 `recent_target_context`） |
| 新增守卫 | `test_review_context_keeps_level_priority_contract`、`test_review_context_filters_provisional_and_generated_levels` |
| 该文件全部用例 | 11 passed |
| 残留成本 | n=10000：4.185 s CPU（10.0%）；每批 Θ(offset)，累计 Θ(n²)，**语义必需** |

---

## E6. 全量回归

```
$ venv/bin/python scripts/run_regression.py --tsv tmp/phase7/regression_after.tsv
...
测试文件总数        : 97
  pytest 风格       : 96
  脚本式 smoke      : 1
pytest cases 通过   : 1281
有效失败文件        : 0
总耗时              : 29m42s
TSV 写入 tmp/phase7/regression_after.tsv
exit 0
```

- 97 个文件（96 个 pytest + 1 个脚本式冒烟 `tests/app_boot_test.py`），**1281 个用例通过，0 有效失败**。
- `.regression-logs/` 里只有一份 `agent_inspector_ui_test.log`，mtime 是 **10:35**（本轮回归之前），
  且本轮该文件是 `9 passed` —— 它是**上一轮**留下的 FAIL 日志，不属于本次。按约定"只有 FAIL 才落日志"，
  本次 0 个新日志 = 0 失败；判断时必须用 mtime 对齐本次窗口，不能只看目录非空。

### `outputs/` 的实际影响（修正既往 caveat）

既往记录写"跑全量回归会重写真实 `outputs/*/state.json`（mtime 变、语义不变）"。
本轮**实测不成立**（与 Phase 6 收尾的观察一致）：

```
files under outputs/ modified inside the regression window: 2
   17:11:01  outputs/runtimecancel001/runtime_events.jsonl
   17:11:03  outputs/threadrecover01/runtime_events.jsonl

state.json modified in window: 0
```

即：**`outputs/*/state.json` 一个都没被重写**；被追加的只有两个 runtime-cancel/recover 夹具的
`runtime_events.jsonl`（它们本来就写 runtime 事件）。所以该 caveat 应改为
**条件性表述**：*视 job 是否落在 `_flush_state_snapshot` 的落盘路径上而定，不一定发生。*
判断方法：把 `outputs/` 下的 mtime 与回归窗口对齐，而不是预设它一定会变。

复现该检查（不需要事后补备份）：

```python
from pathlib import Path          # 与回归窗口对齐，只看窗口内被改的文件
import datetime
lo = datetime.datetime(2026, 9, 26, 17, 11, 0).timestamp()
hi = datetime.datetime(2026, 9, 26, 17, 45, 0).timestamp()
print([str(f) for f in Path("outputs").rglob("*")
       if f.is_file() and lo <= f.stat().st_mtime <= hi])
```

---

## E7. 口径与陷阱（本轮踩到的）

1. **wall 不可跨运行比较**：同一批日志里与改动无关的相位一起掉了 15–21%（沙箱负载漂移）。
   凡涉及 `open`/`fsync` 或高负载的项，只读"次数"与 CPU；跨运行比 **min**，不比 mean。
2. **相位是嵌套的**：`pair_prefix` / `review_packet_core` / `project_memory` 是
   `packet_build` 的直接子项；`bounded` ⊂ `review_packet_core`；`memory_*` ⊂ `project_memory`。
   把同表所有行相加会重复计数（before 那张表相加是 31.88 s，而真实 bucket 只有 ~17 s）。
3. **`sys.getallocatedblocks` 在这里没有信息量**：临时对象被立即释放、freelist 复用，净增量恒为 ~1–2。
   §7 要求的 "allocations" 用**确定性派生计数**（normalize/hash/term_matches 调用次数）+ profiler 的
   `objects` 指标代替。这一点在脚本注释里写明了，避免下一个人再被误导。
4. **`before` 基线是在加完埋点之后捕获的**，所以 before/after 的相位名与 `objects` 列逐行可比。
5. **`items` 只在同相位内可比**：不同相位记的是不同东西（`pair_prefix` 记前缀长度、
   `review_input_truth` 记 segment 数），跨相位相加无意义。
6. 手工跑单文件测试必须 `TMPDIR=$(mktemp -d)`：沙箱 shim 让已存在的 pytest basetemp 在 setup 就报错
   （症状：exit 1 且**无 "N passed" 汇总行**）。

---

## E8. §4.4 十分位斜率复测（Phase 6 报告指定的最后一项前置验证）

Phase 6 报告原文要求：「用 `scripts/phase6_batch_scaling.py` 复测十分位斜率 `k` 是否从 1.7/2.0
降到 ≈1.0（**必须看斜率，不能只看总量**）」。E2 是三档总量反推，本节是**逐批斜率直测**。

### E8.1 命令

```bash
./venv/bin/python scripts/phase6_batch_scaling.py --segments 1000 --glossary-size 1000 \
    --json tmp/phase7/batch_scaling_ctx_after.json > tmp/phase7/batch_scaling_ctx_after.log 2>&1
# exit 0, 3m08s
```

对照侧：`tmp/phase7/before.log` 的第 3 节 `########## 3) breakdown BEFORE (G=1000 ctx) ##########`
（`[scale_n1000]`，同一 fixture、同一脚本、只差实现）。

### E8.2 `review_input_pair_prefix` 的原样输出

before（`tmp/phase7/before.log:268-280`）：

```
  per-batch phase cost by decile (ms/batch) -- superlinear only
    phase                              D 1 D 2 D 3 D 4 D 5 D 6 D 7 D 8 D 9 D10   k(wall)
    review_input_previous_context       0.05  0.08  0.11  0.15  0.19  0.22  0.26  0.28  0.32  0.35     0.69
    review_input_pair_prefix            0.01  0.04  0.06  0.08  0.09  0.12  1.34  1.43  1.51  0.26     1.06
    review_input_state_copy             0.00  0.00  0.00  0.00  0.00  0.00  0.00  0.01  0.01  0.01     0.36

  phase items scanned by decile -- evidence of rescanning
    phase                              D 1 D 2 D 3 D 4 D 5 D 6 D 7 D 8 D 9 D10
    review_input_previous_context         50   150   250   350   450   550   650   750   850   950
    review_input_state_copy               50   150   250   350   450   550   650   750   850   950
    review_input_evidence_index         1053  1153  1253  1353  1453  1553  1653  1753  1853  1953
    review_input_pair_prefix              50   150   250   350   450   550   650   750   850   950
    commit_log_append                     14    42    70    98   126   154   182   210   238   266
```

after（`tmp/phase7/batch_scaling_ctx_after.log:15-26`）：

```
  per-batch phase cost by decile (ms/batch) -- superlinear only
    phase                              D 1 D 2 D 3 D 4 D 5 D 6 D 7 D 8 D 9 D10   k(wall)
    review_input_previous_context       0.04  0.08  0.10  0.15  0.18  0.21  0.28  0.26  0.32  0.34     0.69
    review_input_state_copy             0.00  0.00  0.00  0.00  0.00  0.01  0.01  0.00  0.01  0.01     0.34

  phase items scanned by decile -- evidence of rescanning
    phase                              D 1 D 2 D 3 D 4 D 5 D 6 D 7 D 8 D 9 D10
    review_input_previous_context         50   150   250   350   450   550   650   750   850   950
    review_input_state_copy               50   150   250   350   450   550   650   750   850   950
    review_input_evidence_index         1053  1153  1253  1353  1453  1553  1653  1753  1853  1953
    review_input_pair_prefix               2     2     2     2     2     2     2     2     2     2
    commit_log_append                     14    42    70    98   126   154   182   210   238   266
  ...
  phase totals (whole run)
    review_input_pair_prefix               0.001     0.001     0.000     280          558
```

读法：

- `pair_prefix` 的 **items 行从 50→950（线性）变成 2→2（常数）**，并且它**整行从
  「superlinear only」表里消失**（低于报表阈值）。before 那行的 `k(wall)=1.06` 是这张表内的
  斜率；after 无处可测，因为逐批值完全相同。
- **同一张表里的 `previous_context` / `state_copy` / `evidence_index` 三行，items 逐格一致**
  （before `0.05…0.35` / after `0.04…0.34`，k 都是 0.69）——说明这不是"整体变小"，
  而是**只有被改的那一行动了**。`previous_context` 的 Θ(offset) 是 §5 有意保留的。
- before 那行的 ms 列有 D7–D9 的 1.3–1.5 ms 尖峰后 D10 掉回 0.26，属 wall 抖动（沙箱）；
  **只有 items 列是确定性的**，结论以 items 列为准。

### E8.3 ctx-heavy（G=1000）whole-run 相位对照

| phase | before cpu_s | after cpu_s | Δ |
| --- | --- | --- | --- |
| `review_input_pair_prefix` | 0.137 | 0.001 | −99.3% |
| `review_input_project_memory` | 8.960 | 5.272 | −41.2% |
| ··`memory_normalize` | 0.877 | 1.055 | ≈0 |
| ··`memory_hash` | 2.498 | 2.534 | ≈0 |
| ··`memory_scan` | 1.617 | 1.673 | ≈0 |
| ··`memory_project` | 3.919 | 0.003 | −99.9% |
| `review_input_review_packet_core` | 5.126 | 0.151 | −97.1% |
| ··`glossary_resolve` | 3.341 | 0.001 | −100% |
| ··`relevant_glossary` | 1.605 | 0.000 | −100% |
| ··`bounded` | 0.117 | 0.120 | ≈0 |
| `review_input_packet_build`（父） | 14.247 | 5.462 | **−61.7%** |
| `review_input_previous_context` | 0.056 | 0.055 | ≈0 |
| run total | cpu 40.950 / wall 49.215 | cpu 25.549 / wall 123.885 | wall 被 FS 停顿抬高，不可用 |

归因闭合：`14.247 − 5.462 = 8.785`，被消掉的重复派生 `1.605 + 3.341 + 3.919 + 0.137 = 9.002`
（`relevant_glossary` + `glossary_resolve` + `memory_project` 内二次 `normalize`+`hash` + `pair_prefix`）
→ 差 2.4%，在沙箱噪声内。`memory_normalize/hash/scan` 三行不变是关键旁证。

### E8.4 与 Phase 6 报告预期数字的对齐

Phase 6 报告说的是「从 1.7/2.0 降到 ≈1.0」。实测：

| 场景 | 指标 | before | after |
| --- | --- | --- | --- |
| n=10000（E2，三档反推） | `pair_prefix.items` 增长指数 | 2.000 | 每批常数（1.99 / 2.00 / 5.57，第三档为 fixture 依赖集差异） |
| n=1000 ctx（E8.2，十分位直测） | D1→D10 的 items | 50→950（k=1.06） | 2→2（k=0） |

即：**「文档级 Θ(n²) 项被消掉」在三档总量与逐批斜率两条独立路径上都成立。**
