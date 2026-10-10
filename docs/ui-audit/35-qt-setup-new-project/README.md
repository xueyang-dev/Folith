# 译前准备：归入项目选择器 + 新建项目

日期：2026-10-09 · 界面：桌面端（PySide6 / cocoa，真实窗口抓图）· 主题：浅色 / 夜间 / 纸面

## 起因

试用时报了两件事，其中一件顺带挖出一个**静默失效**：

1. **译前阶段无法新建项目。** Web 端的译前步骤是从项目切换面板底部的「新建项目」
   进入的（`app.py:_render_project_switcher_body`，第 12513 行）；桌面端没有那个面板，
   「归入项目」又是一个平铺下拉，于是译前阶段根本没有建项目的路径 —— 只能先去首页
   建好再回来。这是移植时丢掉的能力，不是设计取舍。
2. **顺手发现的真缺陷：「归入项目」选什么都写不回去。** 面板读的是
   `project.get("id")`，而 `adapter.project_options()` 返回的键是 `project_id`
   （`adapter.py:1530`）。于是每个条目的 data 都是 `None`：

   - `findData(<真实 id>)` 永不命中 → 回填时落到第 0 项；
   - `payload()["project_id"]` 恒为 `None` → `apply_task_setup` 收到 `None` 就
     **不动** `state["project_id"]`。

   控件在、样式在、能点开，但功能不在 —— 只有断言取值才发现。

## 改动

| 文件 | 改动 |
|---|---|
| `folith/qt/setup_panel.py` | 新增 `createProjectRequested` 信号 + `_build_project_row()`（选择器 + `新建项目…` 按钮）+ `add_project(project_id, name)`；修正项目条目的取值键并让「未分类」排第一 |
| `folith/qt/views.py` | 新增 `setupCreateProjectRequested` 信号并转发；新增 `add_setup_project()` |
| `folith/qt/window.py` | 新增 `create_project_from_setup()`：自动化守卫 + 名称对话框 + `adapter.create_project` + 填回面板 + 刷新首页 |
| `tests/qt_task_setup_test.py` | 新增 5 例（选择器真的选中 / 未分类排第一 / 按钮只发意图 / 载荷随行 / window 端守卫与选中） |

**零新增 QSS**：新建按钮用既有的 `#subtleAction`（次要动作样式），三套主题指纹不变。

### 两个刻意的设计决定

- **面板不自己建项目。** 弹框、写盘、刷新首页的项目列表都是 window 的职责（它持有
  对话框与其它页面）。面板只发意图，再由 window 调 `add_project()` 把结果填回来 ——
  与 `startRequested` / `setupStartRequested` 同一条分层。
- **归属不在这里落盘。** 它随「开始翻译」经 `apply_task_setup` 写进任务状态，与面板上
  其它选择走同一条路径。所以 `create_project_from_setup` 只**选中**，不调
  `assign_jobs_to_project` —— 后者是给"已存在的任务改归属"用的，而准备阶段的任务
  归属尚未生效。

### 为什么「未分类」必须排第一

`_set_combo_data()` 找不到条目时会落到第 0 项。若第 0 项不是未分类，一个归属项目
**已归档**的任务就会被静默改归到磁盘顺序上的第一个项目：用户什么都没改，任务却换了
项目。未分类经 `core.task_project_id_for_new_job()` 归一成 `None`（"没有长期归属"），
所以这个落点是安全的。`adapter.project_options()` 的顺序来自
`core.list_active_project_options()`，只在系统工作区**不在**磁盘上时才把它插到最前，
因此面板自己保证这一点（`sorted(..., key=lambda item: not item.get("is_system"))`）。

## 证据

真实窗口（cocoa）抓取，数据目录 `/tmp/folith-qa-project`，夹具
`sample-monograph.docx`（`tmp/`，未入库）：

```
entries: ['未分类', 'The Sensorium', '译前准备手册']
text0 == 未分类: True
selected: '未分类' | data: '71011d57-f167-5b46-809b-8458f3235a6a'   # 修复前这里会是 None
payload.project_id: '71011d57-...'
after create -> selected: '新建的译前项目' | data: '35b1d6e3-...'
created on disk: True
```

截图：

- `01-selector-light.png` / `02-selector-dark.png` / `03-selector-paper.png`
  —— 「归入项目」右侧的 `新建项目…`，三套主题。
- `04-after-create-light.png` / `05-after-create-dark.png` / `06-after-create-paper.png`
  —— 建完之后选择器落在「新建的译前项目」上。

## 反证（控制用例）

只证明"新键能选中"不够 —— 可能是别的原因让它生效。用**旧键形态**构造目录再跑一次：

```
旧键形态 -> currentData = None
旧键形态 -> payload.project_id = None
反证成立：旧键 `id` 让选择器与写回同时失效
新键形态 -> currentData = '94261628-3608-408d-8bde-e29edead38cd'
正向成立：新键 `project_id` 选中并可写回
```

（探针：`tmp/control_project_key.py`，未入库。）

## 验证

- `tests/qt_task_setup_test.py` → **20 passed**（15 旧 + 5 新）
- `tests/qt_client_window_test.py` → **71 passed**（分块逐进程跑）
- `tests/qt_client_adapter_test.py` → **57 passed**（顺带印证既存那条失败属抖动）
- `qt_home_cards_test` / `qt_client_style_test` → exit 0
- QSS 指纹 `light=c3b8fa9900246d85 / paper=c18f15e21272c328 / dark=4c31f48927321f38`
  → 逐字节命中基线，夜间色泄漏命中 0
- `pyflakes` → 相对 HEAD 增量 0

## 未决

- `识别精度` 在默认提取路径上**失效**（见下一节）——与本次改动无关，但要单独定案。
- 新建项目目前只有名称（`adapter.create_project(name)`），描述未暴露：与首页入口、项目
  管理对话框保持一致，`core.create_project` 本身支持 `description`。
