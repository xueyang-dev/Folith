# A2 进程隔离 spike（#325）: 把翻译 worker 移出 GUI 进程

- Issue：#325（A2），父追踪 #327；与 #324（A1）共享同一份增量事件格式
- 分支：`spike/0.8.2-325-process-isolation`（从 `origin/main` `8109b9c2` 开，**不合入 main**）
- 原型：`scripts/spike/`（`harness.py` / `worker.py` / `workload.py` / `stall_probe.py` / `measure_startup.py`）
- 环境：macOS 14（Apple silicon）、CPython 3.11.3、仓库 `venv`、PySide6 6.11、冻结版样本 `dist/qt-desktop/Folith.app`（0.8.1，仅用于启动开销测量）
- **本文件是 spike 结论，不是设计定案。** 原型故意只测它要回答的问题；所有"这是合成负载"的地方都写明了。

---

## 0. 结论先行

| 选项 | 结论 |
|---|---|
| **0.8.2 实施完整 A2（worker 常驻子进程 + 全生命周期）** | **不建议**（推迟） |
| 0.8.2 实施"会话级 worker 进程"（应用启动时起一个长驻进程，pipeline 例外时用） | 可选，前提是 A1 P2/P3 先落地、且 A0 真机数据证明 GIL 类卡顿仍超门槛 |
| **A2 不做** | 也不建议：失败隔离/内存隔离是独立于卡顿的真实收益，8 小时长跑与崩溃门槛（#327）迟早需要它 |

**一句话理由**：A1 砍掉的是 GUI 主线程的 O(书长) 工作；A2 只能再买"worker 线程 GIL 争用"这一块。原型 3 次重复实测：在 **post-A1 形状**的每批负载下，GIL 争用把 GUI 脉冲 p99 从 27 ms 抬到 **≈74 ms**（每次 0–1 次 >100 ms，单次 max 83–145 ms）；只有当单批存在 **>100 ms 的持 GIL C 调用**时，进程隔离才把 40–69 次 slow stall 降到 **0**（3 次一致）。而这套收益的价格是每任务/每会话 worker 冷启动 **≈6–13 s**（frozen 实测 12.7 s 的完整路径、2.0 s 的早分支下限）、一整套双进程生命周期与打包复杂度。**先用 A0 真机数据确认"剩下的 p99 来自哪里"，再决定是否付这个价。**

---

## 1. 原型是什么、不是什么

**是**：同一份 CPU 形状负载（`workload.BatchEngine`：每批一次整份 state 的 `json.loads`、QA 正则、行/字典构建、批记录 `json.dumps`，外加 20 ms 模拟网络等待）分别以
- `inprocess`：GUI 进程内的 `threading.Thread`（当前产品架构 `core.start_job_worker`，`core.py:9687/9765`）；
- `subprocess`：`subprocess.Popen` 起一个**新解释器**跑 `scripts/spike/worker.py`，通过**与产品同构的持久文件**（命令文件 / progress JSONL / status / terminal）控制与观测；

两种模式下，主线程都跑 Qt 事件循环 + A0 同口径的 25 ms 脉冲探针（`stall_probe.PulseProbe`：`interval_ms`、`>=100ms` slow、`>=500ms` severe、`blocked_ms`）。生命周期统一为：**启动 → 翻译 → 暂停 → 继续 → 退出（`interrupted`）→ 续跑 → completed**；`--crash` 额外验证"kill -9 后恢复"。

**不是**：
- 不是真实 `core.run_job_pipeline`（没有真实 provider、没有 docx/PDF、没有真的 findings/审稿）；
- 探针是 A0 `jank_monitor`（PR #330，未在 main）的**同口径复刻**，不是 A0 已验证的采样器；
- 原型 worker 只 import `workload`，**没有** import `core`（见 §4 启动开销：真实 worker 必须 import core，代价完全不同）。

复现：

```bash
cd .loop/worktrees/spike-325
# 名义负载（post-A1 形状：10k 段 state，无大 C 调用）
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/spike/harness.py \
    --mode inprocess  --data-dir /tmp/s-in  --segments 10000 --batches 120 --latency 0.02
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/spike/harness.py \
    --mode subprocess --data-dir /tmp/s-sub --segments 10000 --batches 120 --latency 0.02
# 重 GIL 负载（每批一次约 146 ms 的 C 解析，模拟大 state / 重文档处理的上界）
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/spike/harness.py \
    --mode inprocess  --data-dir /tmp/h-in  --segments 10000 --batches 40 --parse-mb 160
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/spike/harness.py \
    --mode subprocess --data-dir /tmp/h-sub --segments 10000 --batches 40 --parse-mb 160
# 崩溃隔离
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/spike/harness.py \
    --mode subprocess --data-dir /tmp/crash --segments 10000 --batches 60 --crash
# 启动开销
venv/bin/python scripts/spike/measure_startup.py --repeats 5 \
    --frozen-app dist/qt-desktop/Folith.app/Contents/MacOS/Folith
```

---

## 2. 问题 1：进程边界（子进程负责什么 / GUI 保留什么 / 谁持锁）

**代码事实（现状）**：worker 是同一进程的 daemon 线程（`core.py:9765`），在启动时以非阻塞方式拿到 `.worker.lock`（`core.py:9693`，`folith/job_lock.py:7` 的 `flock`/`msvcrt.locking`）并持有到清理；外部保存（人工编辑/候选采用）在 `core.save_job_state` 里尝试同一把锁，拿不到且任务活跃就明确拒绝（`core.py:10646-10675`）。状态事务另有跨进程 `.state.lock`（`folith/engine/locks.py:60/93/124`，`folith/store_lock.py`）；运行状态合并写有跨进程 `.runtime-write.lock`（`core.py:8679`）。

**建议边界**：

| 子进程（worker） | GUI 进程 |
|---|---|
| 翻译生成 / 确定性 QA / 审校 / 知识反馈 / 上下文构建（`core.run_job_pipeline`） | 读模型与投影（A1 的 `DeltaBatch`、行模型、慢投影） |
| 写 `state.json` / `state_journal.jsonl` / TM / knowledge cache | 读取与渲染；人工编辑（经核心事务） |
| 写 `runtime_state.json`（heartbeat/进度）、读取消/暂停/退出标志 | 写取消/暂停/继续/退出标志到 `runtime_state.json`；读进度 |
| **持有 `.worker.lock`（该任务执行权）** | **不持 worker 锁**；保存时照旧被拒（语义不变） |

**锁的迁移是关键**：`.worker.lock` 必须由**子进程**在开工前获取并全程持有（跨进程 `flock` 天然支持，崩溃由 OS 释放——这比现在"线程崩溃锁随进程消失"更干净）。GUI 在子进程退出/崩溃后不再有锁，外部保存会成功后由 `snapshot_revision` CAS 与 `.state.lock` 保证不丢编辑；这正是 #92/#94 已有的语义，无需新锁。

**结论**：边界可行，锁模型不需要发明新原语；唯一必须改的语义是"执行权从线程移到子进程"，并把 `start_job_worker` 的"先拿锁再起线程"改成"先起子进程，由子进程拿锁并写 `runtime_state.worker.owner_pid`"。

---

## 3. 问题 2：通信、命令与终态契约

### 3.1 增量事件：复用 A1 的 `DeltaBatch`，且不需要新 IPC

A1 (#324, PR #337) 的 `DeltaBatch.as_payload()` 是纯 JSON，字段为 `schema/job_id/from_seq/seq/batch/batches/offset/segments[]/counters/delivery/findings_delta/progress/domains/origin/bytes`。跨进程有两条路：

1. **文件总线（推荐）**：子进程照旧写 `state_journal.jsonl`（已有、已有 fsync、已有 seq 幂等/断档语义），GUI 用 A1 的 `StateFeed` 读同一文件。**不需要新协议、不需要序列化协商**，且天然支持"子进程崩溃后 GUI 仍在、续跑从 cursor 开始"。
2. 管道/Socket：`as_payload()` 可直接当消息体，但需要重新发明背压、丢包恢复、断档判定——与 A1 已有的文件语义重复。**仅当未来需要亚秒级进度时才考虑**，且仍应以日志为权威。

### 3.2 命令（取消/暂停/继续/退出）

现状已经是**文件命令**：worker 每次检查点 `load_runtime_state(job_id)`，读 `pause_requested / cancel_requested / restart_requested / exit_requested`（`core.py:353/359/477`，暂停是协作式的：`_runtime_pause_checkpoint` 在批次边界等 `pause_requested` 清空）。`update_runtime_state` 用跨进程 `.runtime-write.lock`（`core.py:8679`）做 load→merge→replace，所以**GUI 与子进程同时写运行状态是现有实现已经支持的**。

进程隔离下这条链不需要改，只多两个约束：
- 子进程写 heartbeat/lease（`worker.owner_pid` / `lease_expires_at`），GUI/看门狗据此判断存活（现有 lease 恢复逻辑直接可用）；
- GUI **不得同步等待**子进程 I/O（否则又变成 GUI 卡顿）：用 `Popen.poll()` + `QTimer` 或 reader 线程（与 `folith/qt/ui_health.py:44-55` 的 watchdog 完全相同）。

### 3.3 终态契约（`docs/desktop-lifecycle-contract.md`）如何保持

| 契约要求 | 隔离后的实现 |
|---|---|
| 退出落点必须是 `interrupted` | 子进程收到 `exit_requested` 后在批次边界停下、写 `interrupted`、释放 `.worker.lock`、退出 0（现有 worker 终态分支原样保留） |
| 有界等待 + 强制退出 | GUI `Popen.wait(timeout=…)`（原型用 8 s）；超时 `kill()`；OS 释放 flock；GUI 按"无存活 worker 的活跃任务"规则补写 `interrupted`（契约 §2.5 已有） |
| 暂停中退出 | `cancel_requested` 唤醒暂停循环（现有 `_runtime_pause_checkpoint` 的 `_runtime_cancel_requested` 分支，`core.py:507`） |
| 强制退出只承诺已落盘结果 | 不变：journal/快照仍在，续跑重算未完成批次 |
| 崩溃/进程丢失 → `interrupted`/`stalled` | lease/heartbeat 已有；原型验证了"kill -9 → 由观察方按最后 cursor 写 `interrupted` → 续跑" |
| 最小化不停止 | GUI 进程不退出，子进程继续，天然满足 |

**原型实测（`--crash`）**：kill -9 发生在 seq 18；`progress.jsonl` 最后完整记录=18；观察方写 `interrupted{seq:18}`；续跑从 18 启动，最终 `completed`，**重复 seq 为空**，GUI 全程 0 slow stall。

---

## 4. 问题 3：打包（spawn vs 自 re-exec）与启动开销实测

### 4.1 冻结版事实

- 打包是 **onedir**：`packaging/folith-qt.spec:194 EXE(...) / 214 COLLECT(...)`，产物 `Folith.app` 内有 `Python.framework`、`PySide6`、`QtCore`。
- 入口 `qt_client.py` **没有** `multiprocessing.freeze_support()`。
- PyInstaller 6.22.3 自带 `pyi_rth_multiprocessing.py`：它把 `multiprocessing.freeze_support` 替换为内部实现，但**仍要求应用自己在 `__main__` 调用** `freeze_support()`，否则 spawn 出来的子进程会重新执行整个冻结应用（本例会撞单实例锁 / 弹"已在运行"提示）。
- 产品已有**自 re-exec** 的成熟先例：`folith/qt/ui_health.py:44-55` 用 `[sys.executable]`（frozen）或 `[sys.executable, "-m", "folith.qt"]`（源码）+ `--ui-watchdog` 参数起子进程，stdin/stdout 管道通信，Windows 用 `CREATE_NO_WINDOW`；`folith/qt/app.py:88-90` 在 **bootstrap/Qt/单实例之前**分流该参数。
- `folith/ocr_paddle.py:274` 已经在用 `multiprocessing.get_context("spawn")` + Pipe 跑 PaddleOCR 子进程——说明冻结版下 spawn 也能用（但 OCR 有独立的可选 runtime 安装，不能直接推断 `core` 也能低成本 spawn）。

### 4.2 两种机制对比

| | `multiprocessing` (spawn) | 自 re-exec（`Popen`/`QProcess` + `--worker` 参数） |
|---|---|---|
| 冻结版可用性 | 必须加 `freeze_support()`（当前没有），且 spawn 会重新引导整个 bundle | 与 watchdog 同款，**已在两平台冻结版验证** |
| 启动入口 | 重新引导应用（Qt/单实例全部再跑一遍，除非早分支） | 可精确控制：在 `prepare_environment` 之后、`build_application` 之前分流 |
| 控制/观测 | Pipe + `multiprocessing.Queue`（pickle） | stdin/stdout JSON 行，或文件总线；无 pickle 版本耦合 |
| Windows 隐藏控制台 | 需自行处理 | `folith/desktop/process.py::hidden_console_kwargs()` 已有 |
| 失败检测 | `Process.is_alive/exitcode` | `Popen.poll/returncode` + 信号 |
| 对 GUI 事件循环 | 需要线程或 `QSocketNotifier` 桥接 | 同；`QProcess` 可直接接 Qt 信号（额外依赖 Qt 在 GUI 侧，worker 侧仍可纯 Python） |

**推荐**：若实施，用**自 re-exec**（与 watchdog 同一模式），不用 `multiprocessing`。理由：冻结版路径已被验证、入口可早分支、无 `freeze_support` 隐患、无 pickle 版本风险。

### 4.3 启动开销实测（macOS 本机，中位数）

| 测量 | 中位数 | 说明 |
|---|---:|---|
| `python -c pass`（裸解释器） | 37–53 ms | 进程创建基线 |
| `python -c "import PySide6.QtCore"` | 221–313 ms | Qt 基础导入 |
| `python -c "import core"`（源码树） | **5.0–6.7 s** | `-X importtime`：`pandas` 1.5 s、`google.genai` 1.1 s、`openai` 1.0 s、`core` 自身 8 ms 之外全是依赖 |
| `multiprocessing` spawn 往返（空子进程） | 179–188 ms | 不含 import core |
| **冻结版早分支**（`--ui-watchdog`，立即退出） | **≈2.0 s**（3 次 1.93–2.13 s） | PyInstaller 引导 + Python 启动，不含 core/Qt |
| 源码同等早分支（`python -m folith.qt --ui-watchdog`） | ≈0.21 s | 只作对照：源码模式的引导成本几乎为零 |
| **冻结版完整路径**（`--ocr-report` / `--diagnostics-report`） | **12.7 s / 12.9 s** | 引导 + Qt 平台插件 + `import core` + CLI 逻辑 |

**推论（诚实版）**：

- 真实 worker 子进程必须 import `core` → 源码模式 **≈5–7 s**，冻结版落在 **2.0 s（仅引导）与 12.7 s（引导+Qt+core 全路径）之间**；若 worker 入口在 `build_application` 之前分流（不走 Qt），预计 **≈7–10 s**（未实测，因为产品代码里没有这个入口；**待 Windows/冻结版实测确认**）。
- 原型里 `spawn_to_first_delta ≈ 90–330 ms` **不代表产品**：原型子进程只 import `workload`。这个数字只能证明"子进程机制本身很便宜"，不能证明"worker 冷启动便宜"。
- 每次**任务**冷启动 5–10 s 会直接体现在"点开始 → 第一批译文"的等待上；每**会话**只起一次则可以摊掉（见建议）。
- 杀毒软件（Windows Defender/SmartScreen）只能定性：新进程首次执行 + 未签名变体可能额外数百 ms 到数秒；冻结版 onedir 的 DLL 加载也受实时扫描影响。**必须由用户 Windows 真机测**。

---

## 5. 问题 4：失败隔离

**现状**：worker 线程崩溃 = 整个 GUI 进程崩溃（`docs/segfault-bisect-guide-0.8.1.md` 记录过故障隔离完全缺失）。**A2 最大的独立收益在这里，而不是卡顿。**

原型验证的隔离行为：

| 事件 | GUI 表现（实测） | 状态恢复 |
|---|---|---|
| 子进程 `kill -9` | 0 slow/severe，GUI 继续响应（pulse 正常） | 观察方按 `progress.jsonl` 最后完整 seq 写 `interrupted`；续跑完成，无重复提交 |
| 子进程异常退出（非 0） | 同上；`Popen.poll()` 立即发现 | 同 lease 恢复；错误写 `runtime_state.error`，用户可见 |
| 子进程挂死（不响应命令） | GUI 不受影响；有界等待后 `kill()` | 强制退出后按"无存活 worker"补 `interrupted` |
| GUI 崩溃 | 子进程变孤儿（`Popen` 不连带杀死） | 必须在 GUI 启动时按 `worker.owner_pid` + lease 清理孤儿；原型未做（见 §10） |

**代价**：内存/CPU 隔离是双向的——GUI 少了 300 MB 工作集（重负载下 GUI RSS 52 MB vs 272 MB，见 §6），但**总内存不一定下降**（子进程独立副本 + 各自解释器，实测 52+317 vs 272+344 的瞬时峰值，量级相近）。

---

## 6. 实测数字（macOS 本机）

### 6.1 名义负载（post-A1 形状：10k 段 state ≈ 3 MB，每批 CPU ≈ 7 ms + 20 ms 模拟网络，120 批）

| 模式 | 运行次数 | slow (>100 ms) | severe (>500 ms) | interval p99（每次） | interval max（每次） | GUI RSS |
|---|---:|---:|---:|---|---|---:|
| idle（仅 GUI 6 s） | 2 | 0 | 0 | 27.0 / 27.2 ms | 28.0 / 29.5 ms | 53–70 MB |
| inprocess | 3 | 0, 0, 1 | 0 | **73.2 / 74.3 / 74.1 ms** | 86.9 / 144.7 / 83.2 ms | 74–75 MB |
| subprocess | 3 | 0, 0, 0 | 0 | **27.1 / 27.1 / 27.1 ms** | 33.5 / 29.4 / 37.4 ms | 54 MB |

**读法**：在 A1 之后的负载形状里，GIL 争用把 GUI 脉冲尾部从 27 ms 抬到 **≈74 ms**：p99 仍在 100 ms 门槛内，但余量从 73 ms 缩到 26 ms，且 3 次里出现 1 次 ~145 ms 的单次尖峰（跨过 slow 阈值）。A2 在这个形状下买到的是 **≈47 ms 的 p99 余量 + 去掉偶发尖峰**——不是数量级收益。换成"每会话起一次"的方案时这笔收益仍然成立（GIL 争用与进程何时创建无关），只是不再有每次任务 5–10 s 的冷启动。

### 6.2 重 GIL 负载（每批一次 ≈146 ms 的持 GIL C 解析，40 批；模拟大 state/重文档处理的上界）

| 模式 | 运行 | slow | severe | blocked p95 | interval max | GUI RSS | 子进程 RSS |
|---|---:|---:|---:|---:|---:|---:|---:|
| inprocess | #1 | **40** | 0 | 124.7 ms | 175.1 ms | 271.9 MB | 344.3 MB（GUI 自身瞬时） |
| inprocess | #2 | **65** | 40 | 957.1 ms | 1038.0 ms | 321.6 MB | 424.8 MB |
| inprocess | #3 | **69** | 40 | 944.1 ms | 1058.5 ms | 306.3 MB | 410.4 MB |
| subprocess | #1 | **0** | 0 | – | 35.7 ms | 54.1 MB | 401.2 MB |
| subprocess | #2 | **0** | 0 | – | 40.4 ms | 55.1 MB | 368.9 MB |

**读法**：只有当**单批**存在 >100 ms 的持 GIL 调用时，A2 的收益才是数量级的（40–69 → 0 次 slow，3 次一致）。后两次 inprocess 运行期间本机 swap 已用 4 GB、pageout 63 万次（同时还有别的冻结版在跑），绝对 `blocked_ms` 受内存压力放大，**只能当上界**；但同一 session 内交替测量，"子进程 0 slow / 进程内每批都 stall"这个方向是稳定的。真实 pipeline 是否存在这种批次，**必须用 A0 真机 `top_stacks` 判断**（不是本原型能回答的）。冻结版注意：`--selftest` 路径不启动 ui_health（A0 报告已声明），真机复测要走正常冻结客户端。

### 6.3 生命周期与崩溃

| 项 | 实测 |
|---|---|
| 暂停（协作式） | 命令后 ≤1 批停下，恢复后继续；两模式一致 |
| 退出（`exit` → `interrupted`） | 有界 8 s 内落 `interrupted`，随后续跑从 cursor 继续 |
| 续跑 | 从 `interrupted.seq` 续，最终 `completed`，**无重复 seq**（两模式） |
| 崩溃恢复（kill -9） | GUI 0 卡顿；观察方按最后完整 seq 写 `interrupted`；续跑 60/60 完成 |
| spawn→首条 delta（原型） | 90–330 ms（**仅 import workload**，见 §4.3 caveat） |

---

## 7. 问题 5：与 ui_watchdog、单实例、更新器的关系

- **单实例**：`AppInstanceLock` 在 `folith/qt/app.py:158` 获取。worker 子进程**绝不能**走单实例逻辑，也不能初始化 Qt/bundle 的 GUI 部分；现有 `--ui-watchdog` 的做法（`app.py:88` 在 bootstrap 之前分流）应原样复用为 `--worker`。若用 `multiprocessing` 而不是自 re-exec，spawn 子进程会重跑 `__main__` → 撞单实例锁并弹提示（**这就是必须 `freeze_support` 的原因**）。
- **ui_watchdog**：`folith/desktop/ui_watchdog.py` 只监视 GUI pid 的 pulse（`TIMEOUT_SECONDS=30`），是独立进程（`ui_health.py` 自 re-exec）。子进程化 worker 后它**仍然只盯 GUI**——这正是想要的行为：worker 卡住不应触发"UI 无响应"报告；worker 崩溃由 GUI 侧的 `Popen.poll()` 报告。两个子进程模型互不干扰，但**都不共享 worker 的 lease**：GUI 崩溃时 worker 会成孤儿，需要 GUI 启动时的孤儿清理（按 owner_pid + lease），见 §10。
- **更新器**：`folith/desktop/updates.py` 在退出后替换安装目录。Windows 上**运行中的 exe 不能被替换**：更新前必须保证 worker 子进程已退出（复用 `stop_job_for_exit` 的退出链 + `Popen.wait` 有界）；macOS 上重命名/替换运行的 bundle 通常可行但会让孤儿 worker 继续用旧代码写数据——同样要求先停 worker。**结论：更新器需要在"退出并替换"之前等待所有 worker 子进程结束，否则会出现"GUI 更新了、旧 worker 还在写 state"的版本撕裂。**

---

## 8. 方案对比：A2 与"只做 A1 增量化"

| | 只做 A1（P2/P3 完成） | + A2 完整进程隔离 |
|---|---|---|
| GUI 主线程 O(n) 工作 | 消除（读模型增量 + 慢投影分域） | 同 A1 |
| worker GIL 争用 | 保留 | 消除 |
| 失败隔离（worker 崩溃不带走 GUI） | 无 | **有** |
| 8 小时 RSS 增长/泄漏隔离 | 无（泄漏留在 GUI） | **有**（子进程退出即释放） |
| 每任务启动延迟 | 0 | **+5–10 s**（源码 5–7 s；冻结版待测，上限 12.7 s） |
| 打包/平台风险 | 低 | 中高（spawn/freeze_support、杀毒、Windows exe 更新） |
| 生命周期复杂度 | 低 | 高（双进程 + 孤儿清理 + 有界退出） |
| 对 #327 崩溃门槛的帮助 | 无（崩溃源是 A3 的过期包装器） | 有，但属于"绕过"而非"根治" |

**对 #327 根因判断的校正**：v0.8.2 的随机 SIGSEGV 来自 PySide 过期包装器（#326 A3），**A2 不能修它**，只能把后果限制在子进程（如果崩溃恰好在 worker 里；GUI 自身的包装器崩溃仍会带走进程）。因此"用 A2 解决 0.8.2 崩溃门槛"是不成立的；A2 的正当理由是**长跑稳定性与内存/失败隔离**，那属于 0.8.3 的稳定性专项。

---

## 9. 建议

**推迟（0.8.2 不实施 A2）。**

理由（按重要性）：

1. **收益上限在 A1 之前无法测准**：A1 P2/P3 尚未落地，GUI 侧仍有慢投影占主线程/线程池；等 A1 完成 + A0 真机会给出"剩余 p99 的 Top 栈"，才能回答"这些 stall 是否来自 worker 的 GIL"。
2. **名义负载下 A2 买到的是余量而不是量级**（p99 74 ms vs 27 ms，0–1 次 slow/run）；只有"单批 >100 ms 持 GIL"才出现数量级差异（40–69 → 0），而那是需要通过 A0 真机 `top_stacks` 确认的情形。
3. **价格不低**：worker 冷启动 5–10 s（冻结版上限实测 12.7 s）、双进程生命周期/孤儿清理/更新器排序、Windows 杀毒与 exe 替换风险；0.8.2 的时间应优先给 A3（崩溃根因）与 A1 P2/P3。
4. **A2 对 #327 的崩溃门槛没有直接帮助**（崩溃源是 A3）。

**若用户坚持在 0.8.2 内做**，建议只做最小可用版本（不叫 A2 全量）：
- **会话级 worker 进程**（应用启动后懒起一个长驻进程，生命周期 = 会话），避免每任务 5–10 s 冷启动；pipeline 请求走命令队列；
- 不做每任务 spawn、不做 `multiprocessing`（用 `--worker` 自 re-exec，复用 watchdog 的 `Popen` 模式）；
- 先落 A1 P2/P3，再加一个「子进程但仍在 GUI 会话内」的开关（`FOLIOTHREAD_WORKER_PROCESS=0/1`）做灰度；
- 明确孤儿清理、更新器等待、`interrupted` 终态三条验收，缺一不可。

**0.8.3 建议**：以本 spike 为起点做正式设计，前提是 A1 P2/P3 已合入、A0 有真机数据、A3 已消除崩溃源。

---

## 10. 未验证 / 原型没做的事（诚实清单）

1. **真实 pipeline**：原型没有跑 `core.run_job_pipeline`（无真实 provider/文档/审校）。数字只回答"GIL 争用形状"下两种架构的差异，不回答"真实每批耗时分布"。
2. **真实 worker 冷启动**：未测"早分支 + import core"的冻结版（产品里没有这个入口）；12.7 s 是完整 CLI 路径（含 Qt），是**上限**；2.0 s 是早分支（不含 core），是**下限**。区间内的确切值需要原型补一个 `--worker` 早分支入口（需改 `app.py`，本 spike 未做）。
3. **Windows 全未测**：启动开销、杀毒影响、`CREATE_NO_WINDOW`、exe 替换、防火墙（若走网络）都需要用户真机；本机只验证了 POSIX 的 `flock`/`Popen`。
4. **孤儿 worker**：GUI 被 kill 后子进程仍在跑（`Popen` 不级联）；清理策略（owner_pid + lease + 启动时扫描）未实现、未测。
5. **多任务并发**：原型只跑一个任务；真实 GUI 可能同时有多个任务 worker，进程数量/内存/句柄上限未评估。
6. **A2 与 A1 的事件格式联调**：原型用的是同字段名的 JSONL，**没有** import `folith/state_feed.py`（不在 main）；合入后应直接复用 `DeltaBatch`。
7. **QProcess**：原型用 `subprocess.Popen`；`QProcess` 的 Qt 信号化未实测（设计上等价，但 Windows 上 `QProcess` 的隐藏控制台/环境继承行为需真机确认）。
8. **真实 A0 数据**：`docs/performance/runtime-baseline-0.8.2.md` 的两平台数字仍是 pending；本 spike 不替代它。

---

## 11. 要用户裁定的点

1. **A2 是"推迟"还是"0.8.2 最小会话级版本"**（本文件建议推迟；若选最小版本，需要额外 8–12 人日并先完成 A1 P2/P3）。
2. 是否接受"worker 冷启动 5–10 s"作为任务开始延迟（当前是即时开始）；若不能接受，则只能走会话级常驻进程，但那会让"每个任务一个干净进程"的隔离收益打折。
3. 是否接受 A2 之后**更新器必须等待 worker 退出**（Windows 上否则无法替换 exe）。
4. Windows 真机测量由谁执行、何时排期（步骤见 §10/§4.3）。

---

## 12. 附：本 spike 的产物

```
scripts/spike/stall_probe.py      # A0 同口径 25ms 脉冲探针（spike-local）
scripts/spike/workload.py         # CPU 形状负载 + mock provider + 协作式生命周期
scripts/spike/worker.py           # 子进程入口（文件命令/JSONL 进度/terminal）
scripts/spike/harness.py          # 两种模式的统一生命周期 + 卡顿/RSS 报告
scripts/spike/measure_startup.py  # 解释器/Qt/core/spawn/冻结版启动开销
docs/performance/process-isolation-spike-0.8.2.md
```

产物全部在 spike 分支，**不改产品代码**（`folith/qt/*`、`core.py`、`folith/engine/*` 零改动）。原始报告 JSON 在 `/tmp/s-*`、`/tmp/h-*`、`/tmp/crash`、`/tmp/spike-startup*.json`（本地临时，不入库）。
