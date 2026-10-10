# Qt 桌面客户端 · macOS 首页视觉构图精修 — 2026-10-09

上一轮（`28-qt-macos-real-window-qa`）与再上一轮（`27-qt-macos-dark-graphite`）解决的是
**配色**。本轮的判断是：颜色已经中性了，但界面仍然是「深灰的 Web Dashboard」——
四张巨卡 + 一整条 12px 灰字说明 + 表格从窗口 36% 处才开始，底栏两条文本还叠在一起。
所以本轮**不动配色**，只动**构图**。

> 一句话结论：任务列表从「窗口下半部分的一件事」变成「首页的主体」。

## 改了什么（4 个文件 + 1 个测试）

| 文件 | 改动 |
|---|---|
| `folith/qt/views.py` | 侧栏 248→232；页头「标题 + 副标题」同行、动作收进一行；`KpiCard` → 内联 `StatStrip`；表格列宽/对齐重排；脚注与表格合并成一块（`listFooter`）；侧栏底部引擎块；卡片改两行并重排列数；`_set_view_mode` 补列数重算 |
| `folith/qt/style.py` | 新增 `ElideLabel` / `StatStrip` / `StatusBar`；对应 QSS；删除 `kpiCard` / `kpiValue` / `kpiLabel`；底栏 `_sync_permanent` 去重 |
| `folith/qt/window.py` | 底栏改用 `StatusBar`；删掉底栏重复的模型状态；登记常驻路径标签 |
| `folith/qt/theme.py` | `CONTROL` 行高 40→36、表头 34→30 |
| `tests/qt_delivery_defaults_test.py` | 引擎状态用例改写（`engineChip` → 状态点 + 状态词 + 模型串），并**新增**「有阻塞时入口文案必须是"配置"而不是"更改"」 |

## 逐项对照（数值见 `measurements.json`）

| 关注点 | before | after |
|---|---|---|
| 侧栏宽度 | 247 | 231（在 220–240 区间内，靠实际可读性收敛） |
| KPI | 四张 69px 巨卡，主区顶部占 70px | 一条 17px 内联统计条；**四个数字与含义一个不少** |
| 任务区起点 | y=276 | y=140（**+147px 可视高度，+35%**） |
| 表头区占比 | 36% | 18% |
| 行高 / 表头 | 40 / 34 | 36 / 30 |
| 卡片 | 一张一行的 99px 大卡 | 65px、两列、首屏 14 张 |
| 底栏 | 两条文本同起点 x=2 **逐字穿插** | 布局定位，`873 < 879` 不重叠；重复文本自动收起 |

### 状态不再说三遍

同一件「模型没配好」原先出现在①顶部横幅②左下角 AI Engine 卡③底栏。现在分工是：
**横幅只说阻塞（带配置入口）→ 侧栏底部块说当前引擎是什么 → 底栏只说刚刚发生了什么**。
阻塞错误一条没少，配置入口在横幅、侧栏、菜单三处都可达。

## Phase 5：统一标题栏的裁决

按「先拿证据再决定」跑完 A/B/C 三个变体（`tmp/unified_toolbar_probe.py`，产物在 `toolbar/`）：

- **A（现状）**：标题栏写窗口身份「Folith — 学术翻译工作台」，无控件，AppKit 自绘的中性石墨灰与内容区**没有接缝**——就是说，这一档本来就不存在「不连续」要去修。
- **B（`setUnifiedTitleAndToolBarOnMac(True)` + 真 `QToolBar`）**：标题退化成「任务库」（品牌与窗口身份都丢了），两个图标被挤到标题栏最右并且垂直贴边；而页头仍然必须留标题行 → 反而多一份标题。
- **C（回退）**：标题复原；全屏 1470x923 → 回普通 1180x733，traffic light / 拖拽 / 缩放均未受影响。

→ **结论：不采用。** 保留真实原生标题栏，转而把内容区页头做成一行 40px 的工具栏
（标题 + 副标题 + 视图分段 + 项目筛选 + 两个图标动作），这才是「让顶部工具区自然融入」。

## 顺带修掉的两个真问题

1. **底栏把同一句话印两遍**：`enforce_output_dir()` 的提示自带完整路径，而右侧常驻属性
   就是同一个路径。现在 `StatusBar._sync_permanent()` 在消息**包含**常驻文本时把它收起，
   消息清掉自动回来 —— 右半区墨量从 19 862 px 掉到 14 617 px（差值就是那条重复路径）。
2. **卡片列数是陈旧的**：`_card_columns()` 只在 `set_rows` 时算过一次，那时窗口还没被布局到
   最终尺寸，于是 1180 宽的窗口算出 1 列、没人再问第二次。切到卡片视图时补一次重算。

## 复现

```bash
cd <repo>
DATA="$HOME/Library/Application Support/Folith/data"

# 真实 cocoa 窗口（含原生标题栏），同一夹具同一几何
FOLITH_DESKTOP_DATA_DIR="$DATA" venv/bin/python tmp/real_window_qa.py <out> system

# 离屏确定性渲染（浅色/纸面/夜间三套主题，带夹具行，不碰真实数据）
QT_QPA_PLATFORM=offscreen venv/bin/python tmp/theme_shot.py <out> light

# 几何 / 底栏 / 卡片密度 / 统一标题栏 四支探针
FOLITH_DESKTOP_DATA_DIR="$DATA" venv/bin/python tmp/layout_probe.py
FOLITH_DESKTOP_DATA_DIR="$DATA" venv/bin/python tmp/statusbar_probe.py
FOLITH_DESKTOP_DATA_DIR="$DATA" venv/bin/python tmp/card_probe.py
FOLITH_DESKTOP_DATA_DIR="$DATA" venv/bin/python tmp/unified_toolbar_probe.py
```

## 自动化结果

本轮相关 15 个测试文件、逐文件独立进程、每文件独立 `TMPDIR`：

```
qt_client_style 8 · qt_delivery_defaults 8 · qt_delivery_ui 13 · qt_home_cards 10
qt_i18n 8 · qt_prompts 6 · qt_settings_dialog_ui 8 · qt_task_defaults 12
qt_client_adapter 57 · sidebar_geometry_contract 17 · sidebar_ai_engine_footer 16
sidebar_project_switcher 21 · ui_preferences 24 · history_library 38
qt_client_window 71 · app_boot_test 通过
```

`qt_client_window_test` 的 warnings 从 620 降到 80：`setTextAlignment(int)` 是已弃用重载，
改成直接传 `AlignmentFlag` 后不再逐次报警。

⚠️ 浅色/纸面的 QSS 哈希本轮**有意**变动（`KpiCard` 换成了 `StatStrip`，这几条规则主题无关）。
真正要守的不变量是「夜间规则不许泄进浅色/纸面」——已复核通过。

## 还没做的（如实列出）

- **工作台页头卡**内部仍有一条约 50px 的空带（"审校全部段落"与 chips 行之间），属于
  「空白分配失衡」，但不在本轮首页范围内，未动。
- **卡片视图的 "打开任务 →"** 现在与 meta 同行；它仍是每张卡都显示的文字，若日后要更干净，
  可以改成选中/悬停时才出现——本轮按「不改变功能逻辑」保留常显。
- 未在 100%/1.5x 缩放下逐张复拍（本轮为 dpr 2.0 Retina + 956x640 / 940x640 窄窗几何复核）。
- 全量回归未重跑（本轮按受影响面聚焦复验）。
