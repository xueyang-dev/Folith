# Terminology Pattern Scaling — Performance Phase 8 · Evidence

主报告：`docs/terminology-pattern-scaling.md`。本文件**只放原始命令与原始输出**，
便于逐条复算；口径与陷阱清单在 §A。

所有命令的 CWD 都是仓库根，解释器一律 `./venv/bin/python`。
夹具与计时口径见主报告 §1 / §12。

---

## §A 口径与陷阱（复算前先读）

| # | 事项 | 说明 |
| ---: | --- | --- |
| 1 | **三层编译计数器** | `pattern_calls/hits/misses`（项目 LRU）→ `re_compile_calls`（`re.compile` 入口，恒等于 misses）→ `sre_compiles`（`re._compiler.compile`，两层缓存都未命中的**真实编译**）。cliff 只看 `sre_compiles`。 |
| 2 | **必须 `re.purge()`** | `re` 自带三级缓存（`_cache` / `_compiler._cache` / `_parser._cache`，各 512）。同进程连测多档时只清项目 LRU 会被上一档污染：G=1000 档会**假报 450 而不是 900**。`measure_cell` 每轮都做 `reset_term_pattern_cache()` + `reset_compiled_glossary_cache()` + `re.purge()`。 |
| 3 | **夹具 CJK 占比** | `cjk_fraction=0.10`（默认）复现真实学术术语表；要精确压 4096 边界必须 `cjk_fraction=0`，此时 `unique patterns == G`。 |
| 4 | **CJK 形状不得截断** | `_cjk_source` 用整个 `_pseudo_word`（低位数在字符串前面，截断会把基数砍到 20 撞重复）。 |
| 5 | **tracemalloc 在 G=20000 会顶爆进程** | SIGTERM / exit 137。compiled 侧全档一次跑必须 `--no-tracemalloc` 或逐档分进程。 |
| 6 | **`ps` 在沙箱不可用** | `operation not permitted` → 用 `pkill -f <script>`。 |
| 7 | **回归期间不要并行跑浏览器/Streamlit 重活** | AppTest 会报 `script run timed out after 60(s)`，是环境并发不是回归。 |
| 8 | **多文件共用同一个 `TMPDIR` 会从第 4 个文件起 setup ERROR** | 每个测试文件必须 `TMPDIR=$(mktemp -d)`（`scripts/run_regression.py` 已自动这么做）。 |
| 9 | **跑全量回归会就地重写真实 `outputs/*/state.json`** | mtime 变、语义不变。证明"没有 state mutation"必须比**语义哈希**（`tmp/phase8/outputs_snapshot.py`），逐字节比对会把无害重写误报成 mutation。 |
| 10 | **门禁捕获点必须在语义边界** | `translation_setup_equivalence_gate` 原来只捕 `terminology.term_matches`；Phase 8 主路径走 `CompiledGlossary.matches` → digest 会反映"哪个实现回答"。已把两个入口写进同一张 keyed map。 |

---

## §B §1 Baseline — 当前 revision 重测（before）

主报告 §1 的表格**逐列锚定到下面这些产物**；本文给出原始输出。

### §B.1 分档独立进程（repeat=1）—— 主报告 §1 表格的来源

```bash
$ for G in 100 500 1000 4096 5000 10000 20000; do
    ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
        --glossary-sizes $G --cjk-fraction 0.10 --repeat 1 --variant current \
        --json tmp/phase8/rss_${G}.json > tmp/phase8/rss_${G}.log 2>&1
  done
```

```
      G     pat  term_calls      hits    miss     sre  /pat   cpu_s  wall_s  RSS_MB
    100      90        1800      1530      90      90   1.0   0.013   0.013    22.7
    500     450        9000      7650     450     450   1.0   0.068   0.068    22.2
   1000     900       18000     15300     900     900   1.0   0.133   0.133    23.0
   4096    3686       73728     62662    3686    3686   1.0   0.559   0.559    33.1
   5000    4500       90000     67500   13500   13500   3.0   1.850   1.851    46.4
  10000    9000      180000    135000   27000   27000   3.0   3.691   3.693    53.5
  20000   18000      360000    270000   54000   54000   3.0   7.668   7.679    69.7
```

### §B.2 一次性同进程全档（repeat=3）—— `re.purge()` 陷阱的现场

```bash
$ ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
      --glossary-sizes 100,500,1000,4096,5000,10000,20000 \
      --cjk-fraction 0.10 --repeat 3 --variant current \
      --json tmp/phase8/micro_before_mix.json
```

```
variant=current segments_per_batch=6 batches=3 repeat=3 cjk_fraction=0.1
            G       uniq        pat      calls       hits       miss re.compile        sre   sre/pass      cpu_s     wall_s     RSS_MB
          100        100         90       1800       1530         90         90         90         30      0.013      0.013       23.8
          500        500        450       9000       7650        450        450        450        150      0.067      0.067       24.6
         1000       1000        900      18000      15300        900        900        900        300      0.135      0.135       28.2
         4096       4096       3686      73728      62662       3686       3686       3686       1228      0.555      0.555       44.3
         5000       5000       4500      90000      67500      13500      13500      13500       4500      1.896      1.896       51.1
        10000      10000       9000     180000     135000      27000      27000      27000       9000      3.815      3.829       60.4
        20000      20000      18000     360000     270000      54000      54000      54000      18000      7.670      7.677       76.6
```

**与 §B.1 逐格对比**：计数列**完全相同**（1800/1530/90 … 360000/270000/54000），
CPU 与 RSS 有小幅出入（RSS 的差来自同进程多档累积，§B.1 的每档独立进程更干净）。

> **这两组数据的差异本身就是要记录的结论**：
> `sre_compiles` / `cache misses` / `term_matches calls` 是**确定性的**，复算必须逐格相同；
> CPU / wall / peak RSS 是**沙箱负载相关的**，同形运行间有 1–3%（RSS 可达 10%+）漂移。
> 报告里凡涉及"等价/判据"的结论只能建立在计数列上。发现这一点是因为最初报告表里
> 的 CPU/RSS 转抄自更早一次已被覆盖的运行 —— 所以现在每一列都写明出处。

### §B.3 边界精扫（`cjk_fraction=0`，`patterns == G`）

```bash
$ ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
      --glossary-sizes 3000,3800,4096,4097,4200,4500,5000,6000 \
      --cjk-fraction 0.0 --repeat 3 --variant current \
      --json tmp/phase8/micro_before_boundary.json
```

```
  G=  3000 pat=  3000 sre=  3000 /pat=  1.0 cpu=0.443 rss=35.2MB cache=3000/4096
  G=  3800 pat=  3800 sre=  3800 /pat=  1.0 cpu=0.560 rss=43.0MB cache=3800/4096
  G=  4096 pat=  4096 sre=  4096 /pat=  1.0 cpu=0.606 rss=45.2MB cache=4096/4096
  G=  4097 pat=  4097 sre= 12291 /pat=  3.0 cpu=1.686 rss=53.4MB cache=4096/4096
  G=  4200 pat=  4200 sre= 12600 /pat=  3.0 cpu=1.731 rss=54.5MB cache=4096/4096
  G=  4500 pat=  4500 sre= 13500 /pat=  3.0 cpu=1.854 rss=55.7MB cache=4096/4096
  G=  5000 pat=  5000 sre= 15000 /pat=  3.0 cpu=2.070 rss=55.7MB cache=4096/4096
  G=  6000 pat=  6000 sre= 18000 /pat=  3.0 cpu=2.483 rss=57.3MB cache=4096/4096
```

`G=4096` 时 cache size 恰好 4096/4096 且仍是 1.00；`G=4097` 立刻 3.00。
**一个 term 之差 CPU +178%（0.606 → 1.686）—— 容量是开关，不是平滑曲线。**

---

## §C §1 After — compiled 侧

```bash
$ ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
      --glossary-sizes 100,500,1000,4096,5000,10000,20000 \
      --variant compiled --no-tracemalloc --json tmp/phase8/micro_after.json
```

```
G=   100 pat=    90 calls=0 pcalls=0 sre=    90 scan_sre=0 build_sre=    90 /pat=1.0 cpu=0.001 rss=23.9 build_cpu=0.011 retained= 123KiB
G=   500 pat=   450 calls=0 pcalls=0 sre=   450 scan_sre=0 build_sre=   450 /pat=1.0 cpu=0.005 rss=25.0 build_cpu=0.058 retained= 617KiB
G=  1000 pat=   900 calls=0 pcalls=0 sre=   900 scan_sre=0 build_sre=   900 /pat=1.0 cpu=0.010 rss=26.4 build_cpu=0.115 retained=1226KiB
G=  4096 pat=  3686 calls=0 pcalls=0 sre=  3686 scan_sre=0 build_sre=  3686 /pat=1.0 cpu=0.042 rss=36.6 build_cpu=0.479 retained=5029KiB
G=  5000 pat=  4500 calls=0 pcalls=0 sre=  4500 scan_sre=0 build_sre=  4500 /pat=1.0 cpu=0.051 rss=42.2 build_cpu=0.580 retained=6139KiB
G= 10000 pat=  9000 calls=0 pcalls=0 sre=  9000 scan_sre=0 build_sre=  9000 /pat=1.0 cpu=0.107 rss=55.8 build_cpu=1.159 retained=12290KiB
G= 20000 pat= 18000 calls=0 pcalls=0 sre= 18000 scan_sre=0 build_sre= 18000 /pat=1.0 cpu=0.263 rss=89.8 build_cpu=2.866 retained=24460KiB
```

关键三列：

- `calls=0` **且** `pcalls=0`：扫描**完全没有触碰** `_TERM_PATTERN_CACHE`（连读都没读）。
- `scan_sre_compiles=0` **全档**：steady-state 编译数为 0，与 G、批数、4096 边界全部无关。
- `build_sre_compiles` 恰好等于 `unique patterns`（90/450/900/3686/4500/9000/18000），
  `compiles_per_unique_pattern` 全档 1.00。

---

## §D §7 差分语义门禁（`old_result == new_result`）

```bash
$ ./venv/bin/python scripts/phase8_terminology_differential_gate.py \
      --cases 20000 --glossary-size 5000 --seed 20260926 \
      --json tmp/phase8/diff_gate.json
```

```
seed=20260926 cases=20000 glossary_terms=5000
  positive_matches=11036 negative_matches=8964
  dimensions:
    absent                          1347
    ascii_word                      1544
    case                            1933
    cjk                             1141
    empty                            576
    fallback                         926
    hyphen                           993
    mixed                            931
    multiword                        992
    plural_like                     1556
    punctuation                     1167
    regex_meta                      1166
    substring                       1509
    unicode                         1585
    whitespace                      1103
    word_boundary                   1531
  compiled: patterns=4987 plain=14
  mismatches=0
RESULT: EQUIVALENT
```

维度是**驱动式**生成（`_WEIGHTED` 权重表 + 每维度专属生成器），不是事后打标签；
`empty` 占 576 例证明空值也真被覆盖。

---

## §E §8 端到端语义门禁（neutralized A/B）

```bash
$ ./venv/bin/python tmp/phase8/gate_neutralised.py --out tmp/phase8/gate_before.json
$ ./venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase8/gate_after.json
$ ./venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase8/gate_before.json
```

before 侧（`compiled_glossary` → legacy 委托对象）：

```
baseline written: tmp/phase8/gate_before.json
  plain_40: prompts=30 term_matches=0 wall=0.311s
  glossary_locked_40: prompts=20 term_matches=520 wall=0.335s
  glossary_wide_40: prompts=30 term_matches=16000 wall=0.638s
  sections_entity_60: prompts=80 term_matches=720 wall=0.665s
  knowledge_candidates_40: prompts=30 term_matches=480 wall=0.328s
  tm_hits_40: prompts=20 term_matches=0 wall=0.353s
  long_chain_120: prompts=160 term_matches=2797 wall=1.29s
  single_batch_8: prompts=3 term_matches=0 wall=0.08s
```

after 侧与比对：

```
=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK          glossary_wide_40: OK
  knowledge_candidates_40: OK     long_chain_120: OK
  plain_40: OK                    sections_entity_60: OK
  single_batch_8: OK              tm_hits_40: OK
```

两侧的 `term_matches` **计数逐格相同**（0 / 520 / 16000 / 720 / 480 / 0 / 2797 / 0）——
证明判定结果相同，而不只是"某个函数被调用了"。

---

## §F §12 translation workload 同进程 A/B

```bash
$ ./venv/bin/python tmp/phase8/workload_ab.py \
      --segments 100,1000 --glossary-sizes 1000,10000 \
      --repeat 2 --json tmp/phase8/workload_ab.json
```

```
  n=100    G=1000   legacy   cpu=   2.015s wall=   2.600s misses=     1000 build=0x0
  n=100    G=1000   compiled cpu=   2.005s wall=   2.663s misses=        0 build=1x1000
  n=100    G=10000  legacy   cpu= 396.045s wall= 397.235s misses=  2850000 build=0x0
  n=100    G=10000  compiled cpu=  21.554s wall=  22.229s misses=        0 build=1x10000
  n=1000   G=1000   legacy   cpu=  18.991s wall=  24.316s misses=     1000 build=0x0
  n=1000   G=1000   compiled cpu=  19.938s wall=  25.700s misses=        0 build=1x1000
```

`n=1000 / G=10000` 的 **legacy 侧按 §12 主动中止**（外推 ~58–66 分钟；实测跑到 9m46s
只完成 3/4 cell）。compiled 侧单独跑：

```bash
$ ./venv/bin/python tmp/phase8/workload_ab.py \
      --segments 300 --glossary-sizes 10000 --variants compiled \
      --repeat 1 --json tmp/phase8/workload_ab_compiled_n300.json

$ ./venv/bin/python tmp/phase8/workload_ab.py \
      --segments 1000 --glossary-sizes 10000 --variants compiled \
      --repeat 1 --json tmp/phase8/workload_ab_compiled_n1000_g10000.json
```

```
  n=300    G=10000  compiled cpu=  54.000s wall=  56.636s misses=0 build=1x10000  batches=84  pairs=300
  n=1000   G=10000  compiled cpu= 190.934s wall= 199.926s misses=0 build=1x10000  batches=280 pairs=1000
```

`misses`（= 旧路径尝试编译的次数）在 compiled 侧**全为 0**，`build` 恒为 `1 × G`——
**1,000 段 / 280 批只 build 一次**。两次运行的 `built_pat` 都是 10,000，
证明 build 次数与批数、段数**无关**。

> 为什么 G<4096 时几乎无差（1.00× / 0.95×）：那一档**没有 cliff 可消**。
> 这是一个干净的自然对照——收益只在超容量时兑现。

---

## §G §4 缓存键派生成本（被否决方案的数字来源）

```bash
$ ./venv/bin/python tmp/phase8/key_cost_probe.py
```

```
 fixture |       G |         key |     bytes |   min ms |   med ms
  en->zh |     100 |     content |      7474 |    0.048 |    0.052
  en->zh |     100 | source-only |      2824 |    0.014 |    0.014
  en->zh |    1000 |     content |     75181 |    0.479 |    0.540
  en->zh |    1000 | source-only |     28359 |    0.141 |    0.146
  en->zh |    4096 |     content |    306304 |    2.099 |    2.408
  en->zh |    4096 | source-only |    116022 |    0.624 |    0.833
  en->zh |   10000 |     content |    757688 |    5.686 |    6.540
  en->zh |   10000 | source-only |    286108 |    2.320 |    3.025
  en->zh |   20000 |     content |    1501362 |   11.479 |   11.729
  en->zh |   20000 | source-only |     566960 |    3.674 |    4.112
```

`content` = 全字段键（**最终采用**）；`source-only` = 只取首字段（**被否决**，见主报告 §4）。
`sparse` 夹具（preferred/target 为空）另见脚本输出——那种形态下两种键几乎同价，
说明 3.5 ms 的差距完全来自非空译名的字节量。

---

## §H §13 ctx-heavy 相位复测

```bash
$ ./venv/bin/python scripts/phase6_batch_scaling.py \
      --segments 1000 --glossary-size 1000 --json tmp/phase8/ctx_phase8.json
```

```
[scale_n1000] segments=1000 batches=280 wall=32.9871s cpu=22.871s pairs=1000
  review_input_packet_build              6.023     5.937     0.087     280            0
  review_input_project_memory            5.796     5.713     0.083     280       280000
  review_input_memory_normalize          1.013     1.000     0.013     280       280000
  review_input_memory_hash               2.853     2.806     0.047     280       280000
  review_input_memory_compile            0.193     0.190     0.003     280       280000
  review_input_memory_scan               1.720     1.701     0.019     280      1000000
  review_input_memory_project            0.004     0.004     0.000     280       280000
  review_input_pair_prefix               0.001     0.001     0.000     280          558
  review_input_previous_context          0.061     0.061     0.001     280       139860
```

`memory_compile` 是**新增相位**：`items=280000`（= 280 批 × G=1000），
`materialized` 记的是 `matcher.pattern_count`。它把"一次性 build"从 `memory_scan` 里
显式拆出来，避免摊销被藏起来。

---

## §I §16 outputs integrity

```bash
$ ./venv/bin/python tmp/phase8/outputs_snapshot.py --out tmp/phase8/outputs_before.json
$ ./venv/bin/python scripts/run_regression.py --tsv tmp/phase8/regression_after.tsv
$ ./venv/bin/python tmp/phase8/outputs_snapshot.py --compare tmp/phase8/outputs_before.json
```

见主报告 §15 —— 结论以**语义哈希逐条相同**为准，不以其 mtime / 字节数为准
（回归会就地重写 `state.json`，那是无害重写）。

---

## §J 测试与门禁清单（§16）

```bash
# 逐文件独立进程 + 每文件私有 TMPDIR
$ for f in terminology_compiled_glossary_test terminology_governance_test \
           terminology_match_cache_test project_context_hierarchy_test \
           context_target_selection_equivalence_test translation_runtime_test \
           runtime_state_test reviewer_overlap_semantic_equivalence_test \
           review_scheduler_test tm_promotion_persistence_test \
           legacy_case_recovery_test project_memory_test; do
    T=$(mktemp -d); TMPDIR=$T ./venv/bin/python -m pytest "tests/$f.py" -q 2>&1 | tail -3
  done

$ ./venv/bin/python scripts/run_regression.py                 # 全量
$ git diff --check                                            # 空白
$ ./venv/bin/python -m py_compile <changed files>             # 语法
```

结果见主报告 §15。**注意**：`tests/app_boot_test.py` 是脚本式冒烟（`def main()`、无用例），
必须 `./venv/bin/python tests/app_boot_test.py` 跑，pytest 退出码 5 是预期的。
