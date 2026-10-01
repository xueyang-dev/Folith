# Performance Phase 6 — Evidence Appendix

> 主报告：`docs/translation-critical-path-attribution.md`
> 本文件只放**原始测量表**，供复核；结论与判据都在主报告里。
>
> ⚠️ 读任何 wall 列之前先读主报告 §3：本机每个 FS syscall 被拦截放大 ~100×，
> 同一逻辑工作量的 wall 在单一 campaign 内漂移 62×（CPU 恒定）。**只有次数与 CPU 可迁移。**

---

## A. Stage baseline（同一 revision / fixture / 机器，每档独立进程）

```
[baseline_n100]    segments=100    batches=28    wall=1.1343s    cpu=0.2839s
[baseline_n1000]   segments=1000   batches=280   wall=10.1343s   cpu=2.7446s
[baseline_n5000]   segments=5000   batches=1400  wall=162.5691s  cpu=19.3585s
[baseline_n10000]  segments=10000  batches=2800  wall=268.8541s  cpu=49.7842s
```

| stage | n=100 wall/cpu | n=1,000 wall/cpu | n=5,000 wall/cpu | n=10,000 wall/cpu |
|---|---|---|---|---|
| setup | 0.112 / 0.027 | 1.076 / 0.290 | 11.576 / 2.578 | 29.073 / 7.653 |
| translation_generation | 0.097 / 0.021 | 0.915 / 0.197 | 24.847 / 1.072 | 19.942 / 2.184 |
| translation_postprocess | 0.000 / 0.000 | 0.004 / 0.004 | 0.020 / 0.020 | 0.039 / 0.039 |
| deterministic_qa_and_repair | 0.097 / 0.026 | 1.009 / 0.253 | 15.381 / 1.317 | 15.348 / 2.584 |
| semantic_review | 0.114 / 0.038 | 1.318 / 0.603 | 20.957 / 6.982 | 40.378 / 22.489 |
| knowledge_feedback | 0.102 / 0.027 | 1.024 / 0.265 | 15.755 / 1.448 | 20.244 / 2.953 |
| state_commit | 0.207 / 0.054 | 1.982 / 0.508 | 34.516 / 2.759 | 41.700 / 5.630 |
| **commit_tail_residual** | 0.369 / 0.072 | 2.788 / 0.616 | 39.464 / 3.148 | **102.043 / 6.185** |
| **TOTAL** | 1.134 / 0.284 | 10.134 / 2.745 | 162.569 / 19.358 | 268.854 / 49.784 |

---

## B. Per-phase CPU 全量增长表（baseline）

`k = log(CPU_10000 / CPU_1000) / log(10)`。**k ≈ 1.0 = 健康（每批常量）；
k > 1.4 = 每批成本随已提交段数增长 → 累计 O(n²)-类。**

| phase | n=100 | n=1,000 | n=5,000 | n=10,000 | **k** |
|---|---:|---:|---:|---:|---:|
| `review_input_previous_context` | 0.0010 | 0.0515 | 1.3055 | 5.0913 | **2.00** |
| `review_input_state_copy` | 0.0001 | 0.0011 | 0.0180 | 0.0641 | **1.76** |
| `review_input_evidence_index` | 0.0002 | 0.0032 | 0.0559 | 0.2160 | **1.83** |
| `review_input_packet_build` | 0.0069 | 0.2236 | 3.4594 | 11.4870 | **1.71** |
| `reviewer_prompt_assemble` | 0.0008 | 0.0145 | 0.0370 | 0.0732 | 0.70 |
| `reviewer_transport` | 0.0006 | 0.0055 | 0.0306 | 0.0613 | 1.05 |
| `reviewer_parse` | 0.0003 | 0.0025 | 0.0147 | 0.0312 | 1.09 |
| `reviewer_findings_normalize` | 0.0001 | 0.0010 | 0.0053 | 0.0105 | 1.02 |
| `commit_timing_record` | 0.0005 | 0.0048 | 0.0262 | 0.0525 | 1.03 |
| `commit_log_append` | 0.0007 | 0.0085 | 0.0840 | 0.2548 | **1.48** |
| `commit_recheck_deterministic` | 0.0040 | 0.0384 | 0.2075 | 0.4097 | 1.03 |
| `commit_conflict_detection` | 0.0001 | 0.0005 | 0.0029 | 0.0057 | 1.01 |
| `commit_findings_merge` | 0.0000 | 0.0002 | 0.0010 | 0.0021 | 1.03 |
| `commit_knowledge_feedback`（容器） | 0.0227 | 0.2233 | 1.1878 | 2.3810 | 1.03 |
| `commit_knowledge_llm` | 0.0011 | 0.0111 | 0.0730 | 0.1793 | 1.21 |
| `commit_tm_eligibility` | 0.0003 | 0.0031 | 0.0165 | 0.0324 | 1.03 |
| `commit_state_commit`（容器） | 0.0319 | 0.2920 | 1.6223 | 3.3883 | 1.06 |
| `journal_serialize` | 0.0028 | 0.0283 | 0.2081 | 0.5737 | **1.31** |
| `journal_append` | 0.0205 | 0.1979 | 1.0296 | 2.0110 | 1.01 |
| `journal_fsync` | 0.0022 | 0.0086 | 0.0383 | 0.0733 | 0.93 |
| `audit_event_append` | 0.1193 | 1.1593 | 6.0621 | 11.9526 | 1.01 |
| `audit_event_fsync` | 0.0045 | 0.0443 | 0.2253 | 0.4574 | 1.01 |

超线性项合计（n = 10,000，CPU）：11.487 + 5.091 + 0.216 + 0.064 + 0.255 + 0.574
= **17.687s / 49.784s = 35.5%**。

---

## C. n = 10,000 相位明细（wall / cpu / wait / calls / items / chars）

### review 相位

| phase | wall | cpu | wait | calls | items |
|---|---:|---:|---:|---:|---:|
| review_input_previous_context | 5.107 | 5.091 | 0.016 | 2,800 | 13,998,600 |
| review_input_state_copy | 0.064 | 0.064 | 0.000 | 2,800 | 13,998,600 |
| repair_shadow_state_copy | 0.000 | 0.000 | 0.000 | 0 | 0 |
| review_input_evidence_index | 0.216 | 0.216 | 0.000 | 2,800 | 14,008,600 |
| review_input_packet_build | 11.540 | 11.487 | 0.053 | 2,800 | 0（未埋点） |
| reviewer_prompt_assemble | 0.074 | 0.073 | 0.001 | 2,800 | 10,000 |
| reviewer_transport | 0.061 | 0.061 | 0.000 | 2,800 | chars 114,800 |
| reviewer_parse | 0.031 | 0.031 | 0.000 | 2,800 | – |
| reviewer_findings_normalize | 0.011 | 0.011 | 0.000 | 5,600 | – |
| reviewer_evidence_request | 0.000 | 0.000 | 0.000 | 0 | – |
| reviewer_suggestion_overlay | 0.000 | 0.000 | 0.000 | 0 | – |
| reviewer_blind_validation | 0.000 | 0.000 | 0.000 | 0 | – |
| reviewer_postprocess | 0.000 | 0.000 | 0.000 | 0 | **derived residual** |

`semantic_review` stage 总量 40.378 / 22.489 / 17.889 → 埋点小计 17.104 wall / 17.034 cpu；
**derived residual = 23.274 wall / 5.455 cpu / 17.819 wait**。

### commit 相位

| phase | wall | cpu | wait | calls | items |
|---|---:|---:|---:|---:|---:|
| commit_timing_record | 0.053 | 0.052 | 0.000 | 2,800 | 0 |
| commit_log_append | 0.256 | 0.255 | 0.001 | 2,800 | 3,921,400 |
| commit_recheck_deterministic | 0.411 | 0.410 | 0.001 | 2,800 | 10,000 |
| commit_conflict_detection | 0.006 | 0.006 | 0.000 | 2,800 | 0 |
| commit_findings_merge | 0.003 | 0.002 | 0.000 | 2,800 | 0 |
| commit_knowledge_feedback | 15.389 | 2.381 | 13.008 | 2,800 | 0 |
| commit_knowledge_llm | 0.180 | 0.179 | 0.000 | 2,800 | 10,000 |
| commit_tm_eligibility | 0.032 | 0.032 | 0.000 | 2,800 | 10,000 |
| commit_state_commit | 26.425 | 3.388 | 23.037 | 2,800 | 0 |
| commit_tm_promotion_flush | 0.000 | 0.000 | 0.000 | 0 | 0 |
| commit_telemetry | 0.000 | 0.000 | 0.000 | 1 | 0 |

### IO 相位

| phase | wall | cpu | wait | calls |
|---|---:|---:|---:|---:|
| journal_serialize | 0.576 | 0.574 | 0.002 | 2,800 |
| journal_append | 24.833 | 2.011 | 22.822 | 2,800 |
| journal_fsync | 0.148 | 0.073 | 0.075 | 2,800 |
| audit_event_append | 116.685 | 11.953 | 104.733 | 16,803 |
| audit_event_fsync | 0.754 | 0.457 | 0.297 | 16,803 |

### counters

```
audit_event_fsync_count=16803   knowledge_observe_batch_calls=2800
reviewer_calls_formal=2800      state_snapshot_taken=3
telemetry_event_appends=0       telemetry_fsyncs=0     telemetry_state_writes=0
```

---

## D. n = 5,000 / 1,400 批 clean reviewer 相位明细（semantic_review 拆解口径）

| phase | wall | cpu | wait | calls | items |
|---|---:|---:|---:|---:|---:|
| review_input_previous_context | 1.305 | 1.306 | 0.003 | 1,400 | 3,499,300 |
| review_input_state_copy | 0.018 | 0.018 | 0.000 | 1,400 | 3,499,300 |
| review_input_evidence_index | 0.056 | 0.056 | 0.000 | 1,400 | 3,504,300 |
| review_input_packet_build | 3.470 | 3.459 | 0.011 | 1,400 | – |
| reviewer_prompt_assemble | 0.037 | 0.037 | 0.000 | 1,400 | 5,000 |
| reviewer_transport | 0.031 | 0.031 | 0.000 | 1,400 | chars 57,400 |
| reviewer_parse | 0.015 | 0.015 | 0.000 | 1,400 | – |
| reviewer_findings_normalize | 0.005 | 0.005 | 0.000 | 2,800 | – |
| **小计** | **4.941** | **4.927** | – | | |
| `semantic_review` stage | 20.957 | 6.982 | 13.976 | | |
| **derived residual** | **16.016** | 2.055 | 13.961 | | |

---

## E. §4 controlled cases

### E1. 原始 sweep（n = 1,000 / 280 批，`suggesting` 模式）

| case | wall | cpu | semantic_review wall/cpu | reviewer_transport wall/cpu/wait | calls |
|---|---:|---:|---|---|---:|
| latency_0ms | 11.3905 | 3.3313 | 1.851 / 1.018 | 0.032 / 0.032 / 0.000 | 1,120 |
| latency_10ms | 26.8476 | 3.2302 | 17.545 / 1.023 | 15.829 / 0.071 / **15.758** | 1,120 |
| latency_50ms | 105.5782 | 3.2405 | (见下) | – | 1,120 |
| latency_100ms | 301.4374 | 3.3904 | 121.398 / 1.251 | 119.606 / 0.134 / **119.472** | 1,120 |
| context_heavy | 174.7625 | **59.3089** | 36.855 / 35.773 | 0.041 / 0.041 / 0.000 | 1,120 |

context_heavy（G=1000 / entity=1000 / candidates=1000）关键项：

| phase | wall | cpu | calls | items |
|---|---:|---:|---:|---:|
| review_input_packet_build | **28.675** | **28.564** | 560 | – |
| reviewer_suggestion_overlay | 14.798 | 14.745 | 280 | – |
| commit_recheck_deterministic | 6.279 | 6.252 | 280 | 281,000 |
| commit_conflict_detection | 2.764 | 2.751 | 280 | 1,000,000 |
| commit_knowledge_feedback | 6.503 | 0.452 | 280 | – |

### E2. 干净 replication（n = 400 / 112 批，独立进程、无并行干扰）

| case | wall | cpu | setup wall/cpu | semantic_review wall/cpu | commit_tail wall/cpu |
|---|---:|---:|---|---|---|
| latency_0ms | 4.0609 | 1.1349 | 0.401 / 0.098 | 0.570 / 0.286 | 1.144 / 0.252 |
| latency_10ms | 47.9641 | 1.1010 | 5.389 / 0.097 | 6.884 / 0.289 | 26.146 / 0.236 |
| latency_50ms | 103.8647 | 1.1706 | 0.383 / 0.096 | 25.970 / 0.327 | 45.334 / 0.240 |
| latency_100ms | 166.1909 | 1.2211 | 3.988 / 0.098 | 48.571 / 0.355 | 71.416 / 0.241 |
| context_heavy | 101.0900 | 21.8356 | 4.357 / 1.513 | 13.235 / 12.832 | 50.240 / 1.003 |

`reviewer_transport`（448 calls/case）：

| case | wall | cpu | wait | wall/call |
|---|---:|---:|---:|---:|
| 0ms | 0.011 | 0.011 | 0.000 | 0.025 ms |
| 10ms | 6.354 | 0.032 | 6.321 | 14.2 ms |
| 50ms | 25.402 | 0.045 | 25.357 | 56.7 ms |
| 100ms | 47.996 | 0.052 | 47.944 | **107.1 ms** |
| context_heavy | 0.015 | 0.015 | 0.000 | 0.033 ms |

**→ wait/call = 注入延迟 + ≈7 ms，四点完美线性；`semantic_review` CPU 在 0.286–0.355s 之间不动。**

### E3. 沙箱 wall 不可复现性（同表内直接可见）

同逻辑工作量（112 批 `commit_tail_residual`）：wall **1.144 / 26.146 / 45.334 / 71.416 s**，
CPU **0.252 / 0.236 / 0.240 / 0.241 s**。→ **wall 漂移 62×，CPU 漂移 7%。**

---

## F. 逐批增量（§7 的核心证据）

### F1. n = 3,000 / 840 批（每批成本，ms/batch，按十分位）

| stage | D1 | D5 | D10 | k(wall) | k(cpu) |
|---|---:|---:|---:|---:|---:|
| setup | 3.34 | 4.42 | 64.67 | 1.01 | 0.30 |
| translation_generation | 2.97 | 3.42 | 122.63 | 1.27 | 0.07 |
| deterministic_qa_and_repair | 3.47 | 3.97 | 9.56 | 0.35 | 0.06 |
| semantic_review | 5.36 | 7.18 | 8.77 | 0.17 | 0.23 |
| state_commit | 6.74 | 8.15 | 8.23 | 0.07 | 0.07 |
| commit_tail_residual | 8.65 | 10.56 | 251.72 | 1.15 | 0.24 |

> D3 与 D10 出现宽幅尖峰（如 `audit_event_append` D3 = 261.43 ms/batch）——同一 campaign 内
> 的 FS 抖动，见 §E3。**不要读 wall 的十分位。**

| phase | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 | k(wall) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `review_input_previous_context` | 0.07 | 0.20 | 0.32 | 0.40 | 0.51 | 0.55 | 0.72 | 0.86 | 0.97 | **1.09** | **0.95** |
| `review_input_evidence_index` | 0.02 | 0.02 | 0.02 | 0.02 | 0.03 | 0.02 | 0.03 | 0.03 | 0.03 | 0.03 | 0.19 |
| `review_input_state_copy` | 0.00 | 0.00 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.65 |

**items（逐批，D1→D10）**

| phase | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 | 性质 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `review_input_previous_context` | 150 | 450 | 750 | 1,050 | 1,350 | 1,650 | 1,950 | 2,250 | 2,550 | 2,850 | 声明窗口 |
| `review_input_state_copy` | 150 | 450 | 750 | 1,050 | 1,350 | 1,650 | 1,950 | 2,250 | 2,550 | 2,850 | 实测 |
| `review_input_evidence_index` | 153 | 453 | 753 | 1,053 | 1,353 | 1,653 | 1,953 | 2,253 | 2,553 | 2,853 | 实测 |
| `commit_log_append` | 42 | 126 | 210 | 294 | 378 | 462 | 546 | 630 | 714 | 798 | 实测 |

**spy 归因（n = 3,000）**

| call site | calls | wall | cpu | items |
|---|---:|---:|---:|---:|
| `cancel_check` @ commit_tail_residual | 1,678 | 35.799 | 1.177 | – |
| `load_runtime_state` @ commit_tail_residual | 1,678 | 35.788 | 1.167 | – |
| `cancel_check` @ translation_generation | 840 | 12.814 | 0.588 | – |
| `load_runtime_state` @ translation_generation | 840 | 12.810 | 0.584 | – |
| `select_target_context` @ setup | 840 | 0.445 | 0.441 | 1,259,580 |
| `batch_section_profile` @ setup | 1,680 | 0.018 | 0.018 | 201,600 |
| `digest_for_segment` @ setup | 1,680 | 0.018 | 0.017 | 201,600 |
| `compile_context_packet` @ setup | 840 | 0.009 | 0.009 | 3,000 |
| `recent_target_context` @ setup | 840 | 0.008 | 0.008 | 1,678 |
| `review_dependency_ids` @ setup | 840 | 0.006 | 0.006 | – |
| `glossary_block` @ setup | 840 | 0.002 | 0.002 | – |
| `provisional_hints` @ setup | 840 | 0.001 | 0.001 | – |
| `select_glossary` @ setup | 840 | 0.001 | 0.001 | – |

> `select_target_context` 在 **setup 阶段**也被调用（840 次，声明窗口 1,259,580 = Σoffset），
> 即与 `_runtime_review_context` **每批各扫一次**同一个已提交前缀。
> `recent_target_context` items 只有 1,678（≈2/批）——它是取满即停的 O(count) 版本，
> **不是**瓶颈；两者的差异见主报告 §7.2。

### F2. context-heavy n = 600 / G = 300 / entity = 300 / candidates = 300（168 批）

| stage | D1 | D5 | D10 | k(wall) | k(cpu) |
|---|---:|---:|---:|---:|---:|
| setup | 13.02 | 8.84 | 8.31 | −0.15 | −0.18 |
| semantic_review | 29.25 | 28.80 | 29.54 | 0.00 | 0.01 |
| commit_tail_residual | 11.41 | 12.38 | 39.51 | 0.42 | 0.78 |

| call site | calls | wall | cpu | items | items/批 |
|---|---:|---:|---:|---:|---:|
| `select_glossary` @ setup | 168 | 0.583 | 0.580 | 52,416 | ≈312（≈G） |
| `provisional_hints` @ setup | 168 | 0.095 | 0.094 | 50,400 | =300（candidates） |
| `select_target_context` @ setup | 168 | 0.019 | 0.019 | 50,316 | Σoffset |
| `commit_recheck_deterministic` | 168 | 1.018 | **1.003** | 51,000 | ≈304（≈G） |
| `commit_conflict_detection` | 168 | 0.468 | **0.467** | 180,000 | ≈1,071（≈G×batch） |
| `review_input_packet_build` | 168 | 2.805 | **2.793** | – | 16.6 ms/call |

**→ 第二条放大轴：每批 O(G) / O(G·|batch|) → 累计 O(n·G)。**

---

## G. Persistence（§6 判定依据）

| 指标 | n=100 | n=1,000 | n=5,000 | n=10,000 |
|---|---:|---:|---:|---:|
| batches | 28 | 280 | 1,400 | 2,800 |
| `save_calls` | 1 | 1 | 2 | 3 |
| `state_snapshot_taken` | 1 | 1 | 2 | **3** |
| `state_file_write` 字节 | 189,005 | 1,848,203 | 13,818,963 | **36,097,342** |
| `state_file_write` | 0.0056s | 0.0084s | 0.0200s | 0.0690s |
| `json_serialization` | 0.0011s | 0.0132s | 0.0866s | 0.2172s |
| `summary_write` | 0.0272s | 0.0302s | 0.0461s | 0.0628s |
| `atomic_replace` | 0.0123s | 0.0142s | 0.0312s | 0.0467s |
| `truth_marking` | 0.0038s | 0.0355s | 0.1895s | 0.3728s |
| `review_truth_reconcile` | 0.0003s | 0.0026s | 0.0182s | 0.0463s |
| `state_pre_reconcile` | 0.0030s | 0.0036s | 0.0034s | 0.0036s |
| persistence 合计 | ≈0.05s | ≈0.11s | ≈0.40s | **≈0.82s (0.30%)** |

snapshot 节奏：1/28、1/280、1/700、**1/933 批** —— 与文档长度解耦（journal 预算驱动），
Round 7（Phase B）基线为「5,000 段时 3 次快照、写放大 2.33×」。
本轮 n = 5,000 实测 2 次 / 13.82 MB，n = 10,000 实测 3 次 / 36.10 MB（≈3.0× 放大）。
**结论：NO REGRESSION。**

---

## H. 沙箱 IO 探针（用于 §3 的放大因子）

| 操作 | 本机实测 |
|---|---:|
| `Path.open("a")` + write + flush + close | ≈ 2.2–2.7 ms/op（高负载时 9–21 ms/op） |
| `mkdir(parents=True, exist_ok=True)` | ≈ 2.3–6.5 ms/op |
| `os.write` + `os.fsync`（fd 已开） | ≈ 24–31 µs/op |
| `os.stat` | ≈ 2.0 µs/op |
| `Path.is_dir()` | ≈ 2.5 µs/op |

→ 拦截发生在 **path-based open** 上，而不是 fd-based write/fsync。
这解释了为什么 `journal_fsync` 全轮只有 0.148s（0.06%）而 `journal_append` 有 24.833s。

---

## I. §9/§10 Profiler 开销原始输出

```
=== micro: one phase() entry ===
  iterations: 200000
  disabled  wall=0.0362s cpu=0.0360s (181 ns/call)
  enabled   wall=0.4469s cpu=0.4453s (2234 ns/call)

=== macro: real translate_stage, symmetric order [off on on off] ===
   #  profiler    wall_s     cpu_s  batches  files      bytes
   0       off   54.5048    4.6787      420      4    3964309
   1        on   54.4454    4.7143      420      4    3964296
   2        on   54.1304    4.6677      420      4    3964323
   3       off   94.3098    4.7114      420      4    3964310

  summary (mean of the alternating runs)
    wall: off=74.4073s on=54.2879s delta=-20.1194s (-27.04%)
    cpu : off=4.6951s on=4.6910s delta=-0.0041s (-0.086%)
    artifacts: off=4 files on=4 files
```

---

## J. §13 等价门禁原始输出

```
=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK      glossary_wide_40: OK
  knowledge_candidates_40: OK long_chain_120: OK
  plain_40: OK                sections_entity_60: OK
  single_batch_8: OK          tm_hits_40: OK
```

结构化 diff（`equiv_off.json` vs `equiv_on.json`）差异叶子 **8 个，全部是 `wall_s`**：

```
/glossary_locked_40/wall_s       0.415 -> 0.553
/glossary_wide_40/wall_s         1.07  -> 1.274
/knowledge_candidates_40/wall_s  0.645 -> 0.682
/long_chain_120/wall_s           2.305 -> 1.395
/plain_40/wall_s                 0.362 -> 0.547
/sections_entity_60/wall_s       1.011 -> 0.97
/single_batch_8/wall_s           0.202 -> 0.132
/tm_hits_40/wall_s               0.68  -> 0.536
```

（`long_chain_120` 的 profiler-on 反而更快 —— 再次印证 wall 只反映机器噪声。）

---

## K. 原始文件清单

```
tmp/phase6/attribution.log            40,628 B    baseline + latency/context 全量
tmp/phase6/attribution.json          157,798 B
tmp/phase6/batch_scaling_n3000.json  7,057,229 B
tmp/phase6/batch_scaling_ctx600.json 1,435,681 B
tmp/phase6/equiv_off.json            4,282,314 B
tmp/phase6/equiv_on.json             4,282,315 B
tmp/phase6/profiler_overhead.json
tmp/phase6/campaign_f.log            清晰 replication + 门禁 + 开销
tmp/phase6/validation_targeted.log   定向测试（首次，含 TMPDIR 伪影）
tmp/phase6/validation_targeted_isolated.log  定向测试（每文件独立 TMPDIR，干净）
tmp/phase6/regression.log            全量回归
tmp/phase6/regression.tsv            全量回归机器可读
tmp/phase6/recheck_agent_inspector.log  单文件复跑
tmp/phase6/outputs_check.log         outputs/ 条目核对
tmp/phase6/outputs_verify.log        outputs/ 文件集 + state.json 哈希核对
```

---

## L. §14 全量回归

`venv/bin/python scripts/run_regression.py --tsv tmp/phase6/regression.tsv`
（逐文件独立进程 + 每文件私有 scratch + `--basetemp` 指向不存在路径）

```
测试文件总数        : 97
  pytest 风格       : 96
  脚本式 smoke      : 1
pytest cases 通过   : 1270
有效失败文件        : 0
总耗时              : 38m14s
```

过程中只有 1 个文件报红：`tests/agent_inspector_ui_test.py`（首个文件）
`1 failed, 8 passed`，失败原因是

```
RuntimeError: AppTest script run timed out after 40(s)
  tests/agent_inspector_ui_test.py::test_center_grid_is_the_only_translation_editor
```

—— 是**超时**而非断言失败，且发生在回归的第 1 个文件（紧跟前面重活之后的负载余波）。
**单独复跑**（`TMPDIR=$(mktemp -d)` + `--basetemp` 指向不存在路径）：
**9 passed in 39.19s / exit 0** → **环境负载假失败**，非代码回归。
（同一文件在回归负载下耗时 83s，本机 AppTest 在负载下慢 2 倍以上，40s 超时被触发。）

本机已复现过的同类 signature：`AppTest script run timed out after N(s)`。

### 关键文件逐项（全部 ok）

| 文件 | 结果 |
|---|---|
| `app_boot_test.py`（脚本式 smoke） | exit=0 |
| `checkpoint_event_append_test.py`（本轮新增） | 6 passed |
| `state_journal_test.py` | 16 passed |
| `persistence_phase_a_test.py` | 17 passed |
| `context_target_selection_equivalence_test.py` | 9 passed |
| `terminology_governance_test.py` | 32 passed |
| `terminology_match_cache_test.py` | 5 passed |
| `tm_promotion_persistence_test.py` | 12 passed |
| `translation_runtime_test.py` | 31 passed |
| `translation_instrumentation_test.py` | 4 passed |
| `translation_segment_lifecycle_test.py` | 22 passed |
| `translation_core_test.py` / `translation_core_phase2_test.py` | 25 / 10 passed |
| `translation_planner_test.py` | 32 passed |
| `translation_concurrency_test.py` | 3 passed |
| `reviewer_overlap_semantic_equivalence_test.py` | 17 passed |
| `source_cleanup_checkpoint_test.py` | 4 passed |
| **品牌三连**：`brand_assets_test.py` / `brand_compatibility_test.py` / `brand_regression_test.py` | 13 / 2 / 2 passed |
| `ui_console_test.py`（并发易假失败者） | 11 passed |
| `project_task_navigation_test.py`（曾红） | 86 passed |

---

## M. `outputs/` 回归影响核对（收尾，回归后补测）

回归前已备份：`cp -R outputs tmp/phase6/outputs_backup_pre_regression`。
回归结束后逐条目 + 逐字节比对（`tmp/phase6/outputs_check.log` / `outputs_verify.log`）：

```
=== 文件清单差异（排除 .tmp / .log）===
IDENTICAL FILE SET

=== state.json 内容哈希（outputs vs 备份）===
SAME  ./454cd4ce83969042/state.json  e588f4dfbc9b
SAME  ./586f32a79a54e8f4/state.json  2d3c799009f7
SAME  ./dbg-job/state.json           9f2c34a3eb51
```

* 目录条目：`outputs/` 与备份**完全一致**（13 个任务目录 + `projects` + `provider_config.json`）。
* 全部 **3 份 `state.json` sha256 相同** → 比「mtime 变、语义不变」更强的结论：**内容根本未变**。

> 与既往记录（"跑全量回归会重写真实 `outputs/*/state.json`，mtime 变、语义不变"）相比，
> 本轮连 mtime 都未触发重写——这 3 个 job 在回归中不处于会被 `_flush_state_snapshot` 落盘的路径上。
> **无论如何，`outputs/` 未被污染，备份可按需保留或删除。**
