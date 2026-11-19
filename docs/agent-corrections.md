# 纠正 → 约束的台账（#510）

同一类错误被指出两次之后，正确的回应不是「再写一条提示」，而是**让它变成机器会红的东西**：
测试、lint 规则、导入契约、棘轮基线。提示只能约束读到它的人；约束约束所有人，包括 agent。

这张表记录「哪条纠正已经变成约束」，以及它是哪种约束。**每次发布在发布记录里写一行**
（本版总数 + 相比上一版新增），让曲线可见：新增变少说明重复错误在消失。

## 台账

| 日期 | 纠正（人类/评审指出的问题） | 结果 | 约束在哪 |
| --- | --- | --- | --- |
| 2026-10 初 | 界面文案改成中文后，部分语言目录缺键，界面显示原始 key | **检查** | `tests/qt_i18n_test.py`、`tests/qt_trial_feedback_test.py` |
| 2026-10 | README 中文改版后其它语言页面结构漂了 | **检查** | `tests/readme_sync_test.py` |
| 2026-10 | 品牌名旧称还留在资源里 | **检查** | `tests/brand_regression_test.py` |
| 2026-11-11 | 撤回独立重构后没人管文件继续变大：六天净增约 1,800 行 | **检查（棘轮）** | `tests/line_budget_test.py`（#458） |
| 2026-11-11 | 新增 `QTimer.singleShot(msec, callable)` 不给上下文对象，接收者销毁后回调仍会跑 | **检查（棘轮）** | `scripts/check_qt_singleshot.py`、`docs/qt-singleshot-baseline.txt` |
| 2026-11-11 | 先填控件再 `addLayout` 会留下 stale `QWidgetItem` 包装器 | **检查（棘轮）** | `scripts/check_qt_layout_order.py`、`docs/qt-layout-order-baseline.txt` |
| 2026-11-12 | 工作台增量缓存的键用了状态对象 identity，脏数据会静默显示 | **检查（棘轮）** | `scripts/check_workbench_domain_cache.py` |
| 2026-11-18 | agent 产出 PR 被自动合并，人工评审层被绕开 | **文档 + 设置**（合并政策） | `CONTRIBUTING.md` §1.0 之前不自动合并、`AGENTS.md`；仓库 `allow_auto_merge=false` |
| 2026-11-19 | AGENTS.md 里四条能机械判定的规则只写在文档里 | **检查** | `tests/agents_rules_test.py`（#459） |
| 2026-11-19 | GUI 线程热路径里「顺手」做文件/JSON/状态读取，卡顿进不来评审视野 | **检查（约定 + 豁免名单）** | `tests/gui_hot_path_rules_test.py`、`docs/gui-hot-paths.md`（#505） |
| 2026-11-19 | 没有静态检查在拦「新写的 bug 类错误」（未定义名、闭包里用循环变量…） | **lint（冻结基线）** | `scripts/check_ruff_baseline.py`、`docs/ruff-baseline.txt`、`docs/ruff.md`（#460） |

## 计数

- **0.8.2（2026-11-18）**：本项记录从 0.8.3 开始，0.8.2 及之前没有计数（基线按 **0** 记）。
- **0.8.3**：累计 **11** 条，其中本版新增 **4** 条（#458 行数棘轮、#459 AGENTS 规则、#505 热路径、#460 ruff；合并政策是文档+设置，不计入"检查"数）。

> 计数规则：只有**会红的**约束才计入（测试、lint、棘轮脚本、CI 步骤）；纯文档不算。
> 每次发布时在发布记录里写这一行，并把新增条目补进上表。

## 维护

1. 被指出两次以上的同类问题 → 优先做成约束，而不是加提示；
2. 新约束进表时要写清「约束在哪」（文件路径可点开核对）；
3. 带基线的棘轮约束（行数、ruff、singleShot、layout order）只收紧、不放宽；放宽要在 PR 里写明理由。
