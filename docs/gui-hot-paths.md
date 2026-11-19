# GUI 线程热路径的标记约定（#505）

Qt 的界面线程每帧都要跑完绘制、事件过滤与轮询回调。有件事在代码里看不出来：
**一个普通的函数调用可能恰好跑在这条线程上**——打开任务时同步构建增量模型（88 ms，
P2-20）、在 `paintEvent` 里读状态文件、在轮询回调里解析 JSON，都会直接变成卡顿。
类型检查、单元测试和代码评审都不会报它。

所以这里用**名字**把「跑在 GUI 线程热路径上」标出来，并由
`tests/gui_hot_path_rules_test.py` 检查这些函数里不许出现阻塞操作。

## 约定

以下前缀的函数视为热路径（每帧 / 每次轮询都会执行）：

| 前缀 | 典型来源 |
| --- | --- |
| `paintEvent` | `QWidget.paintEvent` |
| `showEvent` | `QWidget.showEvent` |
| `eventFilter` | `QObject.eventFilter` |
| `_paint*` | 绘制辅助函数（`_paint_row`、`_paint_segment_chunk`…） |
| `_poll*` | 定时器/轮询回调（`_poll_workbench`、`_poll_workbench_delta`…） |
| `apply_delta` / `_apply_delta` / `_apply_row_delta` | 增量应用 |
| `_queue_segment_paint` | 段落重绘排队 |

新增热路径函数时用这些前缀；**不要把耗时工作塞进它们**。

## 不许做的事（检查会红）

在热路径函数体内**直接**调：

- 文件读写：`open()`、`Path.read_text/read_bytes/write_text/write_bytes`、`os.startfile`；
- JSON / TOML 解析：`json.load(s)`、`tomllib.load(s)`；
- 任务状态 / journal：`load_job_state*`、`load_runtime_state`、`read_mutations*`、`replay_from`；
- 子进程与网络：`subprocess.*`、`socket.*`、`urllib.*`、`requests.*`、`httpx.*`；
- `time.sleep()`（`time.perf_counter()` 这类计时是允许的）。

正确做法：把读取交给后台（`TaskRunner` / 工作线程 / 已有的增量 feed），回到 GUI 线程
只做「取已经就绪的数据 + 画」。

## 已知边界

检查只看**直接调用**，不看间接调用（`self._load_everything()` 里读文件不会被这条规则发现）。
刻意如此：间接检查要建调用图，误报会让这条门变成噪声。它挡的是"顺手在这里读一下"，
不是"故意绕三层再读"。

## 豁免

确属例外时，在 `tests/gui_hot_path_rules_test.py` 的 `ALLOWED` 里按
`(文件, 函数名): 理由` 登记——加一条就是一次评审记录。**目前没有豁免条目**
（2026-11-19：71 个热路径函数，0 处直调阻塞）。
