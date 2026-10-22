# 长文档性能加固（第二轮）：验证与验收报告

基线：`main` @ `c0cd112 perf(workspace): stabilize CAT rendering and state reads`。
日期：2026-09-25。本文档为**最终验收报告**，只记录实际测量到的数字。

---

## Final verdict

**PASS WITH FOLLOW-UP**

- 全量回归：89 个测试文件全部跑完、**1164 个 pytest 用例通过**、有效失败 1 个，
  该唯一失败**已用 baseline 背靠背 A/B 证明与本轮无关**（baseline 52.57s/53.31s 失败
  vs current 53.78s/53.21s 失败，差值 ≤1.2s；详见「Regression」）。
- 本轮 10 项改动中 **7 项 KEEP、2 项 EXPERIMENTAL（默认关闭）、0 项 REVERT**（详见「去留审计」）。
- 译文真值、审校权威层级、术语治理语义、CAT 断句契约、`state.json` 单一权威真值**全部未改变**（详见「未改变的边界」）。
- 唯一需要下一阶段决策的遗留项：`state.json` 的平方级写入放大（5000 段时占整轮 49%）。

---

## Before / After

| Bottleneck | Before | After | Improvement | Evidence |
| --- | ---: | ---: | ---: | --- |
| LLM 每请求重建客户端 | 48 次构造 / 48 条 TCP | 1 次构造 / 1 条 TCP | **−97.9% 连接**，wall 5.50× | §LLM transport（本地 HTTP server，48 次调用） |
| 并发 worker 的客户端 | 每线程各自重建 | 4 线程 → 恰好 4 个客户端 | p95 123.8ms → 35.9ms | §LLM transport（4 线程） |
| 运行期遥测全量写盘 | 1600 submits → 1600 次全量写 / 1428 fsync | 345 次写 / 4 fsync | **写 −78.4%，fsync −99.7%** | §Runtime telemetry（8 worker） |
| OCR 检查点写入量（120 页） | 9.958 MB | 0.171 MB | **−98.3%** | §OCR cache |
| OCR 检查点写入量（20 页） | 0.291 MB | 0.029 MB | **−89.9%** | §OCR cache |
| 快速画像的 3 页被重复 OCR | 正式跑再 OCR 前/中/末页 | 复用 3 页，只补差集 | **重复 OCR 0 页** | §OCR cache |
| 原文纠错检查点 | 每批重写全部结果 | 批级文件 + manifest | 写量 −98% 量级 | §OCR cache / §PDF ingestion |
| 限流退避 | worker 各自 `sleep(15)` / `sleep(10)` / 15×`sleep(1)` | 单一 provider/model 闸门 | 4 worker 429 → **0/11 次扎堆** | §Translation throughput |
| 翻译阶段无细粒度证据 | 只有 PDF 侧有 profiler | 8 个子阶段进同一 profiler | 产出 §Persistence 结论 | §Persistence |
| `state_commit`（5000 段） | 无数据 | 199.6s / 整轮 49% / 8.96 GB / 655× | **未修复（已量化）** | §Persistence |
| `tm_commit`（5000 段） | 无数据 | 42.1s / 整轮 10.3% / 949 MB / 625× | **未修复（已量化）** | §Persistence |
| 审校时延暴露在主线上 | 串行 = 生成 + 审校 | 流水线可隐藏 49.3–49.5% | 投影，未实施 | §Translation throughput |

---

## PDF ingestion

同一台机器、同一个脚本、背靠背各跑一次（先 baseline 后 current），避免"拿不同条件的两份历史数据比快慢"。

| fixture | mode | baseline `c0cd112` | current | Δ |
| --- | --- | ---: | ---: | ---: |
| text_pdf | serial | 0.214s | 0.328s | +53% |
| text_pdf | optimized | 0.210s | 0.319s | +52% |
| scanned_20p | serial | 10.153s | 10.167s | +0.1% |
| scanned_20p | optimized | 7.023s | 6.990s | −0.5% |
| scanned_120p | serial | 61.906s | 61.324s | −0.9% |
| scanned_120p | optimized | 39.631s | 39.868s | +0.6% |

**OCR / rasterize 阶段逐项对比**（`performance.json` 内 stage 数据）：

| fixture | mode | baseline rasterize | current rasterize | baseline ocr | current ocr |
| --- | --- | ---: | ---: | ---: | ---: |
| scanned_20p | serial | 9.407s | 9.319s | 9.428s | 9.342s |
| scanned_20p | optimized | 6.704s | 6.608s | 6.730s | 6.635s |
| scanned_120p | serial | 58.875s | 57.961s | 58.897s | 57.984s |
| scanned_120p | optimized | 39.271s | 39.439s | 39.298s | 39.471s |

**结论**：OCR 路径**没有回归**（±1% 以内，方向随机）。唯一可复现的差异在 `llm_cleanup` 阶段：

| fixture | baseline `llm_cleanup` | current `llm_cleanup` | Δ |
| --- | ---: | ---: | ---: |
| text_pdf（1 批） | 0.064s | 0.147s | +83ms |
| scanned_20p serial（10 批） | 0.518s | 0.589s | +71ms |
| scanned_120p serial（60 批） | 2.757s | 3.058s | +301ms |

这是分片检查点的**固定成本 + 每批一次 syscall**：约 +70ms 固定 + 约 +5ms/批。成因是沙箱把文件 IO 代理给宿主（`scripts/run_regression.py` 实测 5–12 倍放大），一次多余的 `stat`/`mkdir` 就要数毫秒。**在 optimized 路径（生产路径，`llm_cleanup` 100% bypass）成本为 0**；在 120 页 LLM 纠错路径上为 +0.5%。已顺手把每页/每批一次的冗余 `mkdir` 改为一次性创建。

**质量不变量（baseline 与 current 均为 true）**：

| fixture | extracted_order_and_text_equal | cleanup_order_and_text_equal | segment_order_and_text_equal | page_order | duplicate_output | duplicate_segment |
| --- | --- | --- | --- | --- | ---: | ---: |
| text_pdf | true | true | true | true / true | 0 / 0 | 0 / 0 |
| scanned_20p | true | true | true | true / true | 0 / 0 | 0 / 0 |
| scanned_120p | true | true | true | true / true | 0 / 0 | 0 / 0 |

（每列 `serial / optimized` 各一份。）OCR 页**无丢失、无重复、顺序正确**，且文本摘要与 baseline 完全一致。

> **non-comparable 标记**：仓库里 2026-09-21 的历史 baseline（20 页 serial 16.77s / optimized 9.80s；120 页 serial 103.09s / optimized 55.12s）与今天的环境**不可直接比较**，因此本文只用今天背靠背重跑的 `c0cd112` 作为对照。

---

## OCR cache

**写入量**（`scripts/ocr_checkpoint_volume.py`，同一批页记录分别按旧格式与新格式序列化）：

| pages | legacy | new | reduction | files |
| ---: | ---: | ---: | ---: | ---: |
| 20 | 0.291 MB | 0.029 MB | **89.9%** | 20 |
| 120 | 9.958 MB | 0.171 MB | **98.3%** | 120 |

**快速画像 → 正式流水线复用**（真实目录结构，两种布局各测一次）：

| fixture | layout | preview OCR'd | full run reused | full run OCR'd | order_complete | duplicates | failed |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| 20p | shared | 3 | 3 | 17 | True | False | 0 |
| 20p | split | 3 | 3 | 17 | True | False | 0 |
| 120p | shared | 3 | 3 | 117 | True | False | 0 |
| 120p | split | 3 | 3 | 117 | True | False | 0 |

**cache identity**：`sha256(源文件) + page_index + backend + model + backend_version + language + DPI + preprocessing_version`，取前 12 位进文件名。身份不一致时 manifest 整体失效，**不会**跨 DPI/后端/版本误用。快速画像写 `outputs/ocr_profile_cache/<sha16>/ocr_cache/`（内容寻址，正式跑**只读**它，从不写它），因此不会污染 `list_jobs()`。

旧 `ocr_checkpoint.json`（v1）仅在身份**恰好等于**旧代码唯一可能产出的组合（`tesseract` / `tesseract-lstm` / 220 DPI / preprocessing v1）时才被采纳。

---

## LLM transport

`scripts/llm_transport_benchmark.py` 起一个**真实本地 HTTP server**，统计 accept 到的 TCP 连接数，两种模式走同一条代码路径（`FOLIOTHREAD_LLM_CLIENT_POOL` 开关）：

**48 次调用 / 单线程 / 服务端 20ms 延迟**

| mode | client 构造 | 复用 | TCP 连接 | wall | p50 | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| per-request（旧） | 48 | 0 | 48 | 1.579s | 31.9ms | 34.0ms |
| pooled（新） | 1 | 47 | 1 | 1.358s | 28.6ms | 32.5ms |

wall 比 **1.163×**，TCP 连接 **−97.9%**。

**48 次调用 / 4 线程 / 服务端 20ms 延迟**

| mode | client 构造 | 复用 | TCP 连接 | wall | p50 | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| per-request（旧） | 48 | 0 | 48 | 0.524s | 34.4ms | **123.8ms** |
| pooled（新） | **4** | 44 | **4** | 0.400s | 32.6ms | **35.9ms** |

**4 线程恰好 4 个客户端** → 线程局部隔离成立，非线程安全客户端**没有被跨线程共享**。p95 从 123.8ms 降到 35.9ms，因为不再有连接建立风暴。

**零延迟（纯 localhost）对照**：48 构造 / 48 TCP / 0.175s → 1 构造 / 1 TCP / 0.032s，wall **5.50×**。

> **诚实说明**：localhost 没有 RTT，所以这里的收益被**低估**。真实中转站一次 TCP+TLS 握手是 1–3 个 RTT；把本测得的 5.50×（零延迟）当作上限、1.16×（20ms）当作下限，实际生产收益在两者之间且偏向上限。

**密钥安全**：cache key 只用 `sha256(api_key)[:16]`；`ClientIdentity.describe()` 不含密钥；benchmark 产物不落盘任何凭据。

**已验证的契约**（`tests/llm_transport_test.py` 9 例 + 既有用例）：

| 要求 | 证据 |
| --- | --- |
| 同 provider/model/base_url 复用 client | 上表（48 → 1） |
| 并发 worker 不共享非线程安全 client | 4 线程 → 4 客户端 |
| 不同 base_url 不串 client | `provider_exchange_modes_test`（归一化相同→复用；不同主机→新建且 base_url 正确） |
| 不同 proxy mode 不串 client | identity 含 `proxy_bypass`；`smoke_test` 断言 `_trust_env is False` |
| 凭据不出现在 key / log / 产物 | 指纹截断 16 位；`describe()` 无密钥 |
| client 正确关闭 | `close_all_clients()`；`smoke_test` 断言释放池后 `is_closed` 为真 |
| retry / cancellation 正常 | `request_control_test` 取消用例 + 全套回归 |

---

## Runtime telemetry

`scripts/runtime_telemetry_benchmark.py` 直接驱动真实 coalescer，两种模式：`flush_interval=0`（旧的"每次提交都落盘"形状）vs 默认 `0.35s`。

| requests | workers | mode | submits | flushes | state writes | fsyncs | wall |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 200 | 1 | uncoalesced | 400 | 400 | 400 | 400 | 23.533s |
| 200 | 1 | coalesced | 400 | 209 | 209 | 16 | 8.861s |
| 200 | 8 | uncoalesced | 400 | 400 | 400 | 390 | 23.230s |
| 200 | 8 | coalesced | 400 | 159 | 159 | 4 | 6.468s |
| 800 | 1 | uncoalesced | 1600 | 1600 | 1600 | 1600 | 86.400s |
| 800 | 1 | coalesced | 1600 | 830 | 830 | 50 | 32.823s |
| 800 | 8 | uncoalesced | 1600 | 1600 | 1600 | 1428 | 82.088s |
| 800 | 8 | coalesced | 1600 | **345** | **345** | **4** | **13.157s** |

| 场景 | state writes | 降幅 | fsyncs |
| --- | --- | ---: | --- |
| 200 req / 1 worker | 400 → 209 | 47.8% | 400 → 16 |
| 200 req / 8 workers | 400 → 159 | 60.2% | 390 → 4 |
| 800 req / 1 worker | 1600 → 830 | 48.1% | 1600 → 50 |
| 800 req / 8 workers | 1600 → 345 | **78.4%** | 1428 → **4** |

**结论**：并发下写入从「与调用数成正比」变成「与**时间窗口**成正比」——请求越多，降幅越大（60.2% → 78.4%）。串行时只能降约 48%，因为 `_active == 0` 必须立刻落盘把"等待模型"清掉（UI 正确性优先）。

**仍然同步立即落盘的事件**（不经合并窗口）：取消、失败（`force=True`）、worker lease、真实阶段边界、业务 checkpoint。合并窗口内若发现 `cancel_requested`，本次合并**主动放弃写入**，宁可丢几条遥测也不覆盖 `cancelling`。

---

## Translation throughput

**并发 benchmark**（60 段 / 3 section / 30ms 假 LLM / 开启审校）：

| concurrency | wall | speedup | target digest | source order | segment count | 连续性上下文分歧 |
| ---: | ---: | ---: | --- | --- | --- | ---: |
| 1（默认） | baseline | 1.000× | ✓ | ✓ | ✓ | 0/15 |
| 2 | 2.7917s | 1.499× | ✓ | ✓ | ✓ | **3/15** |
| 3 | 2.7329s | 1.531× | ✓ | ✓ | ✓ | **5/15** |

译文真值在各级都一致，但**连续性上下文会变**（并发批次预读的 `previous_target` 不同）。**`DEFAULT_TRANSLATION_CONCURRENCY` 保持 1。**

> **2026-10-19 口径订正**：这里的 1 是 **core 回退值**（调用方未提供并发时），不是产品默认。
> 前后端共享契约默认是 `folith/application/job_config.py:80-83` 的
> `DEFAULT_TRANSLATION_CONCURRENCY = 4`；新建任务预设为 快速 6 / 标准 4 / 学术增强 4
> （`folith/application/task_setup.py:62-83`）。预设采用更高并发是对上述
> `previous_target` 分歧的**已知取舍**。审计见 `docs/experimental-status-audit-0.7.md`。

**审校重叠投影**：

| concurrency | generation | review | 串行合计 | 投影流水线 | 可隐藏比例 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 4.1839s | 4.1840s | 8.3679s | 4.2243s | **49.5%** |
| 2 | 2.7914s | 2.7915s | 5.5829s | 2.8303s | 49.3% |
| 3 | 2.7326s | 2.7327s | 5.4653s | 2.7729s | 49.3% |

审校占翻译阶段约一半墙钟，且几乎全部可被隐藏（尾批只有 ~0.04s 无法重叠）。**本轮只做架构与投影，未实施。**

**section-aware 调度器**（默认关闭）：3 个 section / 15 批 / 并发 3 → 波次 `[[0,5,10],[1,6,11],[2,7,12],[3,8,13],[4,9,14]]`，同 section 内顺序严格保持。

**限流统一**：`folith/` 与 `core.py` 中已无任何硬编码限流 sleep（原 `sleep(15)` / `sleep(10)` / 15×`sleep(1)` 全部改走共享控制器）。剩下的两处 `time.sleep(0.1)` 是带 `check_cancel()` 的 worker 收尾轮询，不是退避。

**4 worker 同时 429 的实测重试时间线**（`base_backoff=0.2s`、`max=0.8s`、`jitter=0.25`）：

```
attempts=12  total_elapsed=8.683s
  #1  t= 0.000
  #2  t= 0.229  (+0.229s)
  #3  t= 0.645  (+0.416s)
  #4  t= 1.458  (+0.813s)
  #5  t= 2.334  (+0.876s)
  #6  t= 3.207  (+0.873s)
  #7  t= 4.063  (+0.856s)
  #8  t= 4.879  (+0.815s)
  #9  t= 5.851  (+0.972s)
  #10 t= 6.774  (+0.923s)
  #11 t= 7.722  (+0.949s)
  #12 t= 8.682  (+0.960s)
20ms 内相邻扎堆次数: 0/11
```

旧行为对照：4 个 worker 各自 `sleep(15)` → **≥15s，且 4 个请求同时重新发出**。现在是 8.68s 且**没有任何一次扎堆**。

---

## Persistence

`scripts/translation_persistence_audit.py`，`batch_size=4`，全程走真实 profiler 的 `state_commit` / `tm_commit` 阶段。

### `state.json`（每批一次全量原子重写）

| segments | batches | run | `state_commit` | 占整轮 | 每批均值 | 累计写入 | 最终 state | 放大 | 全量重写次数 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 25 | 5.207s | 1.424s | 27.3% | 57.2ms | 3.94 MB | 280 KB | 14.1× | 25 |
| 1000 | 250 | 58.92s | 19.27s | 32.7% | 77.3ms | 359.62 MB | 2.73 MB | 131.9× | 250 |
| 5000 | 1250 | 407.52s | 199.60s | **49.0%** | **159.9ms** | **8958.05 MB** | 13.67 MB | **655.2×** | 1250 |

### TM（每批一次全量重写）

| segments | run | `tm_commit` | 占整轮 | 每批均值 | 累计写入 | 最终 TM | 放大 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 5.207s | 0.655s | 12.6% | 26.2ms | 0.39 MB | 30 KB | 13.0× |
| 1000 | 58.92s | 7.146s | 12.1% | 28.6ms | 38.00 MB | 303 KB | 125.5× |
| 5000 | 407.52s | 42.119s | 10.3% | 33.7ms | 949.50 MB | 1.52 MB | 625.1× |

**关键读法**：每批 `state_commit` 从 57ms → 77ms → 160ms（批次 25 → 250 → 1250）。单批成本随批次**线性增长** ⇒ 累计成本是 **O(n²)**，所以 `state_commit` 占整轮比例从 27% 涨到 **49%**，放大从 14× 涨到 **655×**。

**本轮未改动 `state.json` 的单一权威真值契约**，只量化。下一阶段方案见「Remaining bottlenecks」。

---

## Regression

**第一轮全量回归（修复前）**：

| 指标 | 值 |
| --- | --- |
| 测试文件完成 / 总数 | 89 / 89 |
| pytest 用例通过 | 1125 |
| 有效失败文件 | 3 |
| 脚本式 smoke | 1（`app_boot_test.py`，exit=0） |
| 总耗时 | 38m25s |

失败文件：`provider_exchange_modes_test.py`、`smoke_test.py`、`red_team_acceptance_test.py`。

**逐一归因**：

| 文件 | 是否本轮造成 | 结论与处置 |
| --- | --- | --- |
| `provider_exchange_modes_test.py` | **是** | 用例断言"传 `base_url` 时必然新建客户端"。客户端池化后同一归一化地址必须复用（这正是收益来源）。已改为断言**真契约**：归一化相同的地址→复用；**不同主机→必须新建且 base_url 正确**（顺带补上了原先缺失的"不串 client"检查） |
| `smoke_test.py` | **是** | 用例断言"请求结束后绕过代理的 `http_client` 已关闭"——那是每请求建客户端时代的正确行为。池化后每请求关闭会摧毁 keep-alive。已改为断言**新生命周期**：请求结束后仍存活，`reset_llm_clients()` 释放池时才关闭 |
| `red_team_acceptance_test.py` | **否** | 该用例是墙钟性能门禁（400 段 × 2 模式，各须 < 30s）。回归中读到 quick=52.12s / quality=30.63s。**背靠背 A/B 复测**：baseline 42.09s / 40.71s，current 43.79s / 43.48s，**两边都通过**。原失败是环境尖峰，非本轮回归 |

**第二轮全量回归（修复后）**：

| 指标 | 值 |
| --- | --- |
| 测试文件完成 / 总数 | 89 / 89 |
| pytest 用例通过 | **1164** |
| 有效失败文件 | **1**（`red_team_acceptance_test.py`，已证明与本轮无关，见下） |
| 脚本式 smoke | 1（`app_boot_test.py`，exit=0） |
| 总耗时 | 51m31s |

> 第一轮 1125 与第二轮 1164 的差异来自计数方式：`run_regression.py` 只统计
> 以 `N passed` 开头的行，带失败的文件（`1 failed, 10 passed`）不计入通过数。
> 修复两个文件后，它们的 39 个用例重新进入统计。

**`red_team_acceptance_test.py` 的定性与证据链**：

该用例是墙钟性能门禁（400 段 × 2 模式，各须 < 30s）。三种条件下测得：

| 条件 | baseline `c0cd112` | current | 结论 |
| --- | ---: | ---: | --- |
| 单用例直接 pytest | 42.09s / 40.71s（通过） | 43.79s / 43.48s（通过） | 两边都通过 |
| 整文件经 `run_regression.py`（第 1 次） | — | 52.12s / 30.63s（失败） | — |
| **整文件经同一 runner，背靠背** | **52.57s / 53.31s（失败）** | **53.78s / 53.21s（失败）** | **两边都失败，差值 ≤1.2s** |

**结论：与本轮改动无关。** 判据有三条：

1. **baseline 同样失败**，且两个模式的耗时与本轮几乎一致（差值 ≤1.2s，在噪声内）。
2. 差异来自**运行上下文而非代码**：单用例直接跑约 20s/模式，跑完同文件另外 17 个用例后
   变成约 53s/模式（2.5×）。这是仓库自己的 `scripts/run_regression.py` 文档里写明的
   沙箱现象（文件 IO 被代理给宿主，实测放大 5–12 倍）。
3. 本轮**没有**修改 `red_team_acceptance_test.py`，也没有改动它覆盖的状态机路径。

**处置：不改门禁阈值。** 把阈值放宽只是让数字变绿，属于"为了让指标好看而减少工作"，
不是修复。该用例在真实（非沙箱代理 IO）环境下的通过性不在本轮范围内。

**用户原有 working-tree 改动**（`docs/history-task-list.md`、`folith/context.py`、`folith/segmentation.py`、`folith/source_quality.py`、`tests/segmentation_test.py`、`tests/source_cleanup_test.py`、`tests/source_quality_test.py`）：**未被覆盖、未被回退、未被改写**，与本轮改动在文件层面完全隔离。回归中这些文件对应的用例全部通过（`segmentation_test` 11 例、`source_cleanup_test` 12 例、`source_quality_test` 6 例）。

---

## Remaining bottlenecks

> **后续进展（同日第三轮）**：本节的第 1 项 —— `state.json` 的平方级全量重写 —— **已在
> [长文档持久化性能（第三轮）](persistence-performance-phase3.md) 中修复**：
> 5000 段 `state_commit` 从 165.56s / 整轮 64.6% / 8.96 GB / 655× 降到
> **8.20s / 8.0% / 881 MB / 84×**，整轮 256.41s → 103.14s。
> 本节保留当时（修复前）的原始排序与数字，作为历史记录。
>
> 修复后重排（第三轮实测）：新的头号**持久化**成本是 `save_tm`
> （21.01s / 20.4% / 949.50 MB / **625×**，同一 O(n²) 形状，未修）。
> 另外第三轮把整轮拆到逐阶段后发现本环境里最大的是**事件日志**
> （5000 段 10157 次 `append_event`、63.5%），但它**主要是沙箱 IO 代理口径**
> （本环境每次文件操作固定收约 5.3ms），生产本地 SSD 上约 0.5–10s —— 详见第三轮报告
> 的「§15 阶段比例、事件日志与环境口径」。
>
> **本节第 3 项（审校占翻译阶段 49.5%）在第三轮未获新证据**：第三轮 fixture 用
> 零时延的确定性假 LLM，审校只占整轮 0.8%，**无法**用来确认或推翻该投影。
> 该结论仍以本轮（第二轮）的真实 provider 投影为准。

### 从 PDF 导入到翻译完成，当前最大的三个瓶颈

1. **`state.json` 的平方级全量重写（本轮最大发现，未修复）**
   5000 段：占整轮 **49%** 墙钟、累计写 **8.96 GB**、最终文件只有 13.67 MB（**655× 放大**）、1250 次全量重写。单批成本随批次线性增长（57→77→160ms），所以文档越长越糟。这是唯一一个**随文档长度恶化**的瓶颈。

2. **OCR 仍是 Tesseract CPU，且分辨率固定 220 DPI**
   120 页 rasterize+OCR 占 39.4s（optimized 路径的 **98.9%**）。本机 `core.ocr_backend_report()` 实测：`tesseract available=true device=cpu version=5.5.0`，`paddleocr available=false reason="No module named 'paddleocr'"`。加速后端目前只有**契约与回退路径**，没有可比的加速数据。注意：**CUDA 被检测到 ≠ GPU 被使用**，产物里记录的是真实执行设备。

3. **审校时延完全暴露在主线上**
   审校占翻译阶段约 **49.5%** 墙钟，且尾批只有 0.04s 不可重叠。这是纯结构性的可隐藏等待，不需要任何新模型或新并发。

（第 4 位是并发被连续性上下文卡住：并发 2/3 会让 3/15 与 5/15 个批次的上下文变化，所以默认并发只能停在 1。）

### 下一轮最值得做的一项优化

**`state.json` 的 `journal frequently + snapshot periodically`**，理由是三条独立证据同时指向它：

- **收益最大**：49% 的整轮墙钟，且是唯一随文档变长而恶化的项。削掉一半就等于给 5000 段文档省 100s。
- **风险最低**：不动 `state.json` 的单一权威真值地位——快照仍是唯一权威真值，journal 只是**可重放的增量**；`resume = snapshot + 后续 journal`。
- **可复用现有设施**：仓库已有 `events.jsonl`（追加写）与 checkpoint 基础设施，不需要新数据库、不需要 schema migration、不需要第二套任务真值。

具体形态（本轮只提设计，不实施）：

| 事件 | 持久化方式 |
| --- | --- |
| 批次完成 | 追加 durable delta 到 journal（O(1) 写入） |
| 每 N 批 / 有界时间间隔 | 压缩成一次全量快照 |
| 阶段边界 | **强制**全量快照 |
| 取消 | **强制**durable checkpoint |
| 失败 | **强制**durable checkpoint |
| resume | snapshot + 其后的 journal 重放 |

必须保住的语义：崩溃恢复、有序译文真值、人工审校真值、TM 提升、取消、`state.json` 作为权威快照。

**先做低风险的一半**：紧凑序列化（orjson/紧凑 JSON）+ 消除每次写入的重复派生归一化，用本轮 profiler 复测（同一 fixture 可直接对比 8.96 GB 这个基线）。若仍不够，再上 journal。

TM 不受 `state.json` 权威契约约束，可独立评估 SQLite/WAL + 按目标语言/原文建索引（625× 放大，949 MB 累计写入）。

---

## 去留审计（KEEP / REVERT / EXPERIMENTAL）

以下保留当轮实验结论。Section-aware 波次规划实验不等于当前产品的普通翻译并发；
当前回退值 1、共享默认 4、预设 6/4/4 及最新五档测量见
[2026-10-22 并发审计](stability-milestone-4/concurrency-audit.md)。
表中的“未实施/默认关”不应当作后续版本的产品开关状态。

| 改动 | 结论 | 理由 |
| --- | --- | --- |
| LLM client registry | **KEEP** | 48→1 次构造、48→1 条 TCP、p95 123.8→35.9ms；4 线程恰好 4 客户端。收益随网络 RTT 增大，localhost 反而低估。复杂度小（单模块、密钥安全身份、显式 `close_all_clients()`、env 逃生舱） |
| Telemetry coalescing | **KEEP** | 8 worker 下 1600→345 次全量写（−78.4%）、1428→4 次 fsync、遥测墙钟 82.1→13.2s；请求越多降幅越大。业务真值写入仍同步 |
| Page-level OCR cache | **KEEP** | 120 页写量 −98.3%、20 页 −89.9%；旧检查点身份门控兼容；页序/去重/失败页语义经 15 例测试与真实 PDF 不变量验证 |
| Quick Profile cache reuse | **KEEP** | 直接消掉每任务 3 页重复 OCR（重复 OCR 0 页），shared/split 两种布局都验证；正式跑只读内容寻址缓存，不污染任务列表 |
| Cleanup sharded checkpoint | **KEEP** | 写量 −98% 量级；成本 +0.30s/120 页（仅 LLM 纠错路径，占 0.5%），生产 optimized 路径为 0；旧格式只读兼容、resume/有序合并/失败批回退语义不变 |
| Shared rate controller | **KEEP** | 消除 3 处不一致的硬编码退避；4 worker 429 → 0/11 次扎堆、8.68s（旧 ≥15s 且 4 路同发） |
| Translation instrumentation | **KEEP** | 它是本轮最大发现（persistence 49%/655×）的唯一来源；已验证不改变译文；代价在零延迟假 LLM 基准上是 +4–7%（**最坏情况**，真实 LLM 时延下占比更小） |
| Persistence benchmark tooling | **KEEP** | 产出 §Persistence 全部数据，可复现、离线、无 API 费用 |
| `StageHandle.finish(persist=False)` | **KEEP** | 把翻译阶段 8 次全量产物序列化降为 1 次（**按构造可数**）。诚实说明：在 400 段上**未量出墙钟差异**，属冗余 IO 消除；如需最小 diff 可回退 |
| 每页/每批一次性 `mkdir` | **KEEP** | 去掉每页/每批一次多余 syscall。诚实说明：**未量出独立墙钟差异**；如需最小 diff 可回退 |
| Adaptive OCR DPI | **EXPERIMENTAL（默认关）** | 它会**改变 OCR 输出**，因此在有盲评质量等价证据之前不允许默认开启。220 DPI 基线保持不变，决策全部记入性能元数据 |
| Section-aware concurrency | **EXPERIMENTAL（默认关）** | 波次规划保持 section 内顺序，但**尚无质量等价证据**。`DEFAULT_TRANSLATION_CONCURRENCY` 保持 1 |
| 审校重叠流水线 | **未实施（仅投影）** | 49.3–49.5% 的潜在收益已量化，但落地会触及有序提交与崩溃恢复语义，需独立一轮 |

**REVERT：0 项。** 没有任何改动被证明无效到需要撤回。

---

## 未改变的边界（架构边界审计）

| 边界 | 结论 | 核对方式 |
| --- | --- | --- |
| 读取路径不得有写副作用 | **未违反** | 对新模块中所有 `load_/get_/list_/build_/*_view/*_summary/*_stats/*_report` 做源码扫描，无 `write_text/mkdir/save/atomic/open(` 命中 |
| `state.json` 单一权威真值 | **未改变** | 没有新增状态库；OCR 缓存与纠错检查点是**可重算的缓存/断点**，不是真值来源；本轮只量化未重构 |
| 审校权威层级 / finding 语义 | **未改变** | 插桩只读时间戳；`reviewed` / `review_status` 计算路径未动 |
| 术语治理语义 | **未改变** | `terminology.py` 只改限流退避；候选/冻结门禁与 `extract_auto_terms_v2` 返回语义未动，`terminology_governance_test.py` 32 例通过 |
| CAT 断句契约 | **未改变** | `segment_source_paragraphs` 未动 |
| 默认参数 | **未改变** | `BATCH_SIZE=4`、`TRANSLATION_MAX_BATCH_CHARS=2400`、`DEFAULT_TRANSLATION_CONCURRENCY=1` 全部原值 |
| 新开关默认值 | 安全默认 | LLM 客户端池默认**开**（纯性能，含逃生舱）；section 调度器默认**关**；自适应 DPI 默认**关** |

`git diff --check` 干净（含全部新增文件的行尾空白检查）；所有改动文件 `py_compile` 通过。

---

## 复现

```bash
# LLM 客户端复用（本地 HTTP server，统计真实 TCP 连接；无 API 费用）
python3 scripts/llm_transport_benchmark.py --out tmp/llm-transport-benchmark \
    --calls 48 --threads 1,4 --latency 0.02

# 运行期遥测合并（1/8 worker × 200/800 请求）
python3 scripts/runtime_telemetry_benchmark.py --out tmp/runtime-telemetry-benchmark \
    --volumes 200,800 --workers 1,8

# 翻译并发 + 连续性上下文 + 审校重叠投影（离线）
python3 scripts/translation_concurrency_benchmark.py \
    --out tmp/translation-concurrency-benchmark \
    --segments 60 --sections 3 --latency 0.03 --review

# 长文档持久化成本（100/1000/5000 段）
python3 scripts/translation_persistence_audit.py \
    --out tmp/translation-persistence-audit --sizes 100,1000,5000

# OCR 检查点写量 + 快速画像复用（需要本机 Tesseract）
python3 scripts/ocr_checkpoint_volume.py --out tmp/ocr-checkpoint-volume --pages 20,120

# PDF ingestion（text / scanned 20p / scanned 120p）
python3 scripts/pdf_ingestion_benchmark.py --out tmp/pdf-ingestion-benchmark

# 全量回归
python3 scripts/run_regression.py --tsv .regression-logs/final.tsv
```

`docs/pdf-ingestion-performance-audit.md`、`docs/translation-throughput.md`、`CHANGELOG.md` 已回填本轮结论。

---

## 本轮新增 / 修改文件

**新增**

| 文件 | 作用 |
| --- | --- |
| `folith/llm_transport.py` | LLM 客户端注册表（密钥安全身份 + 生命周期） |
| `folith/ocr_cache.py` | 页级 OCR 持久缓存 + 旧检查点身份门控兼容 |
| `folith/request_control.py` | provider/model 作用域请求闸门 |
| `folith/ocr_backends.py` | OCR 后端边界与诚实运行时元数据 |
| `folith/section_scheduler.py` | section-aware 波次规划（默认关） |
| `scripts/llm_transport_benchmark.py` | 客户端复用 / TCP 复用 / 时延分布 |
| `scripts/runtime_telemetry_benchmark.py` | 遥测合并前后写量与 fsync |
| `scripts/translation_concurrency_benchmark.py` | 并发 + 连续性 + 审校重叠 |
| `scripts/translation_persistence_audit.py` | `state_commit` / `tm_commit` 写量审计 |
| `scripts/ocr_checkpoint_volume.py` | OCR 检查点写量与快速画像复用 |
| `tests/llm_transport_test.py` | 9 例 |
| `tests/ocr_page_cache_test.py` | 15 例 |
| `tests/ocr_backend_boundary_test.py` | 10 例 |
| `tests/request_control_test.py` | 10 例 |
| `tests/source_cleanup_checkpoint_test.py` | 4 例 |
| `tests/translation_instrumentation_test.py` | 4 例 |
| `tests/section_scheduler_test.py` | 8 例 |
| `docs/performance-hardening-round2.md` | 本报告 |

**修改**

| 文件 | 改动 |
| --- | --- |
| `core.py` | 客户端注册表接入（含 `reset_llm_clients()`、逃生舱释放）、遥测合并、页级 OCR 缓存、共享 profile 缓存、自适应 DPI、后端解析、翻译 8 阶段插桩（阶段收尾只写一次产物）、限流统一 |
| `folith/source_cleanup.py` | 批级增量检查点；`_RequestGate` 继承统一控制器；批目录一次性创建 |
| `folith/ocr_cache.py` | 页目录一次性创建 |
| `folith/performance.py` | `StageHandle.finish(persist=False)`（向后兼容） |
| `folith/document_profile.py` | 限流退避改走共享控制器（原 `time.sleep(10)`）；`cancel_check` |
| `folith/terminology.py` | 限流退避改走共享控制器（原 15×`sleep(1)` 循环） |
| `app.py` | 快速画像的 OCR 写入共享 profile 缓存，供正式流水线复用 |
| `tests/provider_exchange_modes_test.py` | 改为断言 base_url 真契约（复用 + 不串 client） |
| `tests/smoke_test.py` | 改为断言池化后的客户端生命周期 |
| `tests/pdf_pipeline_performance_test.py` | 检查点用例改断言分片布局 |
| `tests/brand_regression_test.py` | 新增性能模块与报告加入旧品牌行级 allowlist（仅 `FOLIOTHREAD_` 前缀） |
| `docs/pdf-ingestion-performance-audit.md` | 回填检查点/后端边界/翻译插桩结论 |
| `docs/translation-throughput.md` | 回填并发、审校重叠、限流、持久化结论 |
| `CHANGELOG.md` | 新增「未发布」条目 |
