# Qt 桌面端任务生命周期契约（退出 / 暂停 / 中断）

> 适用版本：0.7。修复 GitHub issue #24「退出/中断契约」时确立。
> 目标：任何"停止任务"的入口都必须给出**可预期、可恢复**的终态，退出永不静默丢数据。

## 1. 终态定义

| 终态 | 含义 | 重开后可做的动作 |
| --- | --- | --- |
| `completed` | 全部阶段完成 | 查看 / 交付 |
| `cancelled` | **用户主动取消** | 重试 / 重新开始 |
| `failed` | 步骤失败（有错误详情与技术日志） | 重试当前步骤 |
| `interrupted` | 被外部原因打断（退出、崩溃、进程丢失），未完成但已落盘 | **继续处理（resume）**、重试 |
| `idle_incomplete` | 从未开始或已停止但无中断标记 | 开始 / 继续 |

`interrupted` 是"退出"的唯一正确落点：它保留已提交的批次、释放写入锁，并在重开时提供 `resume`。

## 2. 退出（关闭窗口 / 应用退出）

1. 窗口进入退出流程时，对每个活跃任务调用退出停止（`request_job_exit`），而非用户取消：
   - 置 `exit_requested=True` + `cancel_requested=True`，状态进入 `cancelling`；
   - `cancel_requested` 用于唤醒**已暂停**的 worker，使其跳出等待循环。
2. worker 在协作取消边界停下后，按 `exit_requested` 发布 **`interrupted`**（不是 `cancelled`），`error=None`，清空 worker lease。
3. 退出前有界等待 worker 释放写入锁；超时后提供"继续等待 / 强制退出"。
4. **强制退出只承诺"已经落盘的结果可恢复"**；中断的当前请求可能重试并重复计费。
5. 若退出时没有存活 worker，但任务仍是活跃状态，直接把该任务发布为 `interrupted`，避免留下悬空的"正在运行"。

## 3. 暂停

- 暂停是**协作式**的：当前操作结束后进入 `paused`，等待同一 worker 继续。
- 暂停状态在退出时同样走 `request_job_exit`：任务变为 `interrupted`，重开后可 `resume`。
- 暂停**不**等于"保存后退出"，也不承诺常驻后台。

## 4. 最小化

- 最小化**不会**停止或暂停任务；任务在后台继续，UI 只在首次最小化时提示一次。
- 不承诺关窗后托盘常驻（见 `desktop-client-roadmap.md` §3.3）。

## 5. 崩溃 / 强制结束 / 断电

- 复用现有 lease / 心跳恢复：持有 lease 的进程消失后，读取状态会把任务纠正为 `interrupted` / `stalled`。
- 不得把未完成的任务标成 `completed`。

## 6. 实现锚点

- 核心：`core.py` 的 `request_job_exit()`、`_runtime_exit_requested()`、worker 终态分支、`_runtime_pause_checkpoint()`。
- 命令层：`folith/application/commands.py::stop_job_for_exit`。
- 适配层：`folith/qt/adapter.py::stop_job_and_wait`（改用退出语义，签名不变）。
- 窗口：`folith/qt/window.py` 的退出确认文案、暂停提示、`changeEvent` 最小化提示。

## 7. 验收

- 运行中退出：锁被释放，任务终态为 `interrupted`，重开可 `resume`。
- 暂停中退出：worker 被唤醒并释放锁，任务终态为 `interrupted`，重开可 `resume`。
- 没有存活 worker 的活跃任务：退出后直接为 `interrupted`，不残留 `running`。
- 最小化：任务继续运行，提示只出现一次。
