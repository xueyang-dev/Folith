# F2 取证：旧式阻断项让四处界面各说各话（macOS，2026-11-04）

**结论（一句话）**：**复现**。给一条**旧式**阻断项（没有 `requires_human_confirmation`、也没有
`review_event_id`）后：审校页说「当前译文已完成审校 / 翻译审校已完成」、检查浮层 `has_work=False`
（它的视图模型里根本没有 findings 字段）、页头按运行时状态说「已完成」、交付页却拦
「必须处理的问题 1 个」且 `can_deliver=False`。根因是四处读了**四个不同来源**（审校收据、流水线健康视图、运行时状态、交付预检），
其中只有交付页把「收据里的阻断项」和「旧式阻断项」合在一起数。

复现（纯数据层，不建窗口，约 3 秒）：

```bash
./venv/bin/python -m pytest tests/ui_overhaul_f2_status_test.py -q -s
```

实测输出（2026-11-04 16:40，工作区 `53348ae0` + 他人未提交改动）：

```json
{"review.readiness.status": "current",
 "review.readiness.label": "当前译文已完成审校",
 "review.queue_counts.blocking": 0,
 "review.delivery.title": "翻译审校已完成",
 "review.primary_action": "前往交付",
 "followup.has_work": false,
 "followup.keys": ["annotation","busy","entities","extraction","failures","fragments",
                   "has_work","profile","recovery","rescan","scan_pages","understanding"],
 "delivery.blocking_count": 1,
 "delivery.issues": ["必须处理的问题 1 个"],
 "delivery.can_deliver": false,
 "legacy.unresolved_blocking": 1}
header: done 已完成
```

## 1. 四处界面各自读什么（数据来源表）

| 界面 | 读的函数 | 读的字段 | 文件:行号 |
| --- | --- | --- | --- |
| 审校页「已完成」 | `workbench_view.review_workbench_view(state)` → `readiness` + `_coverage_copy` / `_delivery_copy` | `translation_review_readiness().status == "current"`（只数**收据里**的阻断项）；`blocking` 只来自队列项 | `workbench_view.py:899-957`、`READINESS_COPY:48-55`、`_coverage_copy:58-82`、`_delivery_copy:791-816` |
| 检查浮层「没问题」 | `adapter.followup_view(job_id, state)` → `FollowupPanel.issue_count()` | 只有 `failures / fragments / scan_pages / understanding / annotation / entities / profile / recovery`；**没有任何 findings 字段**，`has_work` 也不含阻断项 | `adapter.py:2283-2330`（`has_work` 在 2326-2329）、`views.py:4971-4987` |
| 页头「已完成」 | `views._render_runtime` → `views.run_status_view(runtime, …)` | 只读运行时 `runtime["status"] == "completed"`，压根不看 findings（基线 HEAD 代码） | 定义 `views.py:2026-2050`；调用 `HEAD views.py:5908`（当前工作区 `:5898`） |
| 交付页「必须处理的问题 1 个」 | `adapter.delivery_preview(job_id, state=…)` | **同时数两种**：`translation_review_readiness().blocking_finding_ids`（收据）+ `delivery.unresolved_blocking(state)`（旧式），取 `max()` | `adapter.py:4336-4437`；合并计数 `:4395-4398`；issues `:4404-4410`；`can_deliver` `:4426-4432` |

旧式阻断项为什么只在交付页被数到：

* 审校页的阻断计数要求发现项同时满足 `requires_human_confirmation` 且
  `review_event_id` 与本段当前收据一致（`translation_evidence.py:423-436`，其中
  `:429`、`:433` 是这两条）。旧式项两者都没有 → 既不算阻断、也不进队列
  （队列过滤见 `workbench_view.py:196-208`）。
* 交付门禁走 `delivery.unresolved_blocking`（`delivery.py:357-358`），只看
  `severity == "blocking"` 且未解决 → 数出 1。
* 报告里写的 `adapter.py:4323` 是**旧行号**：HEAD 与当前工作区里这段合并计数的实际位置是
  `adapter.py:4395-4398`（`def delivery_preview` 在 `:4336`）。

## 2. 期望 vs 实际

| 界面 | 期望（任何一处都该是同一个数） | 实际（复现值） | 判定 |
| --- | --- | --- | --- |
| 审校页 | 有 1 个必须处理项 → 不说「已完成」，主按钮是「继续处理 1 个必须处理的问题」 | `readiness.status=current`、`queue_counts.blocking=0`、`delivery.title=翻译审校已完成`、`primary_action=前往交付` | ❌ |
| 检查浮层 | 角标显示 1，`has_work=True` | `has_work=False`；模型键里没有 `findings`/`blocking` | ❌ |
| 页头 | 不说「已完成」，说「运行已完成 · 还有 1 个问题必须处理」 | `run_status_view` 返回 `("done", "已完成")` | ❌（基线） |
| 交付页 | 拦 1 个 → `can_deliver=False`，问题数 1 | `blocking_count=1`、`issues=["必须处理的问题 1 个"]`、`can_deliver=False` | ✅（这一处是对的） |
| 首页任务行 | 状态标签应为「待审校」 | `core.task_status_label` 已经问 `unresolved_blocking` → 「待审校」（这一处也是对的） | ✅ |

补充：`test_legacy_blocking_should_be_visible_everywhere`（同文件）标了
`xfail(strict=False)`，断言的是"修好后四处同数"；修好后它 XPASS 不会让测试套变红。

## 3. 统一到一个函数：落点与签名

**不要把真值放在 `views.py` 里**（当前工作区已有 `WorkbenchView.blocking_items()`，`views.py:4842`，
但它只有工作台能调用，审校页/浮层/窗口/首页/项目页都拿不到）。真值是纯数据、先收在核心里：

```python
# folith/delivery.py —— 不依赖 Qt，仅读 state
def blocking_items(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """所有还没解决的「必须处理」项，唯一口径。

    = 当前审校收据里未处理的阻断项（translation_evidence.translation_review_readiness
      的 blocking_finding_ids，按 finding_id 映射回 state["findings"]）
      + 旧式未解决 blocking 发现项（unresolved_blocking）
    去重：finding_id 优先，缺失时用 (segment_index, fingerprint)。
    返回顺序 = 收据项在前，旧式项在后，便于「去处理第一个」。

    实现注意：``translation_evidence`` 在函数内延迟导入（delivery 与 core 之间已有导入环，
    见 ``delivery.py`` 其它函数的同款写法）。
    """
```

Qt 侧只留一个薄封装（复用既有名字，避免调用点漂移）：

```python
# folith/qt/adapter.py:1902 附近
def unresolved_blocking(job_id, state=None) -> list[dict]:
    from folith import delivery as _delivery
    return _delivery.blocking_items(state or load_state(job_id))   # 现有函数就地换实现
```

每个调用点该传什么：

| 调用点 | 改成 | 传什么 |
| --- | --- | --- |
| `adapter.delivery_preview`（`:4395-4398`） | `blocking = delivery.blocking_items(state)`，`blocking_count = len(blocking)`，删掉 `max()` 与单独的 `legacy_blocking` 分支 | 同一个 `state`（已经加载好的那份，不要重读） |
| `adapter.followup_view`（`:2283`） | 结果里加 `"blocking_items": …`、`"blocking_count": len(…)`，并让 `has_work` 把它算进去 | 同上 |
| `workbench_view.review_workbench_view`（`:899`） | 左侧加参 `blocking_items: Sequence[Mapping] \| None = None`，不传时内部调 `delivery.blocking_items(state)`；`progress["blocking"]`、`counts`、`_delivery_copy`、`_primary_action` 全用它 | 审校页：直接 `review_workbench_view(state)`（内部自取）；窗口/测试可显式传以复用同一份 |
| `views._render_runtime`（`:5898-5909`） | 现在的 `blocking = self.blocking_items()` 保留；`WorkbenchView.blocking_items()`（`:4842`）已经是 `adapter.unresolved_blocking(...)`，底层换成统一口径即可 | `self._state`（当前状态的同一个对象） |
| `views._render_delivery`（`:6097`） | `blocking = adapter.unresolved_blocking(self.job_id, self._state)` | 同上 |
| 首页任务行 / `core.task_status_label`（`folith/engine/delivery_state.py:658`） | 把 `_delivery.unresolved_blocking(state)` 换成 `blocking_items(state)`，收据里的阻断项也显示「待审校」 | `state` |
| `WorkbenchView._render_stage`（`:4857`） | 同上（`blocking=len(...)`） | `self._state` |

验收建议（F2 目标「四处同一份数据 + 有阻断时不出现『已完成』」）：让
`tests/ui_overhaul_f2_status_test.py::test_legacy_blocking_should_be_visible_everywhere` 从
xfail 变成普通断言，再加一条"页头 `run_status_view` 在 blocking>0 时不得返回 `done`"。

## 4. 边界与未复现的部分

* 本复现只覆盖**数据层**（Qt 不参与），所以 Windows/macOS 都一样；**没有**截四张图逐页目视
  （工作区里 Lead 的 WIP 已经在改页头/交付页，截图会拍到"修到一半"的状态）。
* **真实任务**能否自然走到这个状态**未验证**（按契约不在本机范围）：合成状态是手工构造的
  "有收据 + 旧式阻断项并存"。真实任务里旧式 finding 只在收据机制上线前产生/迁移而来，
  这一点需要真实任务库确认。
* 页头那处按 **HEAD 基线**记录（`HEAD:views.py:5841/5908`）；当前工作区已被 Lead 的未提交
  WIP 覆盖（`views.py:5903` 起有 `blocking = self.blocking_items()` 的补丁，`status_voice.py`
  新增未跟踪）。判断"是否已修好"要以最终提交后的代码为准，本文件只钉基线行为与数据口径。

## 5. 复核：修复后（2026-11-04 17:3x）

`test_all_four_surfaces_report_same_blocking`（正式断言）实测：

| 读数 | 修复前 | 修复后 |
| --- | --- | --- |
| 审校页 readiness 文案 | 当前译文已完成审校 | **还有 1 个必须处理的问题** |
| 审校页 queue blocking | 0 | **1** |
| 审校页交付摘要 | 翻译审校已完成 | **暂时无法交付** |
| 审校页主操作 | 前往交付 | **还有 1 个必须处理的问题** |
| 交付页 blocking_count / can_deliver | 1 / false | 1 / false |
| 检查浮层 `blocking` / `has_work` | 无此字段 / false | **1 / true** |
| 首页任务行 `task_status_label` | 待审校 | 待审校 |

→ 四处**数据**已同数。剩下的缺口在视图层：`FollowupPanel` 不读 `followup_view["blocking"]`
（`followup_panel.py` 全文没有 blocking），`issue_count()` 数的是固定 8 张卡
（`issue_cards():357-364`、`issue_count():366-367`），所以「检查与修复」角标仍会是 0、
抽屉里看不到这一项。`test_followup_panel_counts_blocking` 仍是 `xfail(strict=False)`。

落点（只改面板，`adapter`/`views` 不用再动）：

1. `FollowupPanel.apply`（`followup_panel.py:374`）：`self._blocking = int(view.get("blocking") or 0)`。
2. `issue_cards()`（`:357`）：加一张"必须处理的问题 · N"卡（动作发 `locateRequested`，跳第一段）
   或至少让 `issue_count()` 返回 `len(cards) + (1 if self._blocking else 0)`。
3. 抽屉不能出现"角标 1、里面空白"：`overview` 的「需要处理 · N」与 `empty` 的可见性都走同一个数
   （`apply()` 末尾 `:407-410`）。`views._render_followup`（`views.py:4971-4987`）不用改，它已经
   把 `issue_count()` 交给 `check_button.set_count`。
