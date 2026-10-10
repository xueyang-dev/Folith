# Qt 桌面客户端 · 真实 macOS 窗口视觉 QA — 2026-10-08

上一轮（`27-qt-macos-dark-graphite`）把夜间主题从「冷色日间主题的再着色」改成中性
graphite。本轮**不做主题重构**，只做两件事：

1. **在真实 cocoa 窗口里**（不是 offscreen）逐项复核视觉；
2. 对复核中确认存在的两个问题做**最小微调**。

改动范围：`folith/qt/style.py` 一个文件、两个主题门控（`theme == "dark"`）的块。
`before/` 与 `after/` 是**同一份夹具、同一次几何**下采集的（见 `geometry.json`），
两组的窗口位置尺寸逐字段相同，可直接叠图比对。

## 复现

```bash
# before：把 style.py 里下面两处本轮新增内容摘掉（保留上一轮成果）
#   ① _night_surface_rules() 末尾的「选中 / 悬停」QSS 块
#   ② RowDelegate 的 __init__ / _remember_hover_row / _set_hover_row / eventFilter
#      与 paint() 里的整行铺色
# after：工作区当前状态（本轮微调已在其中）

cd <repo>
FOLITH_DESKTOP_DATA_DIR="$HOME/Library/Application Support/Folith/data" \
  venv/bin/python tmp/real_window_qa.py <out_dir> system
```

产物：`01-home-table-selected` … `08-main-window-inactive`（整窗，含原生标题栏）
+ `*__client.png`（Qt 自绘区，供像素量取）+ `facts-system.json` + `geometry.json`。

量取工具（都在 `tmp/`，未入库）：

```bash
venv/bin/python tmp/qa_scan.py <png> v <x> <y0> <y1>      # 沿扫描线打颜色游程（ΔRGB>2）
venv/bin/python tmp/qa_scan.py <png> h <y> <x0> <x1>
venv/bin/python tmp/qa_crop.py <png> <out_dir> name:l,t,w,h   # 最近邻放大裁切
```

## 夹具的两个硬坑（都踩过）

1. `screencapture -l <CGWindowID>` 前**必须**对 NSWindow 调 `orderFrontRegardless`，
   否则它会**安静地返回缓存缩略图**（1180×828 的窗只给 272×310）且不报错。
   只有 `-o`（不含阴影）+ `-l` 才与 `frameGeometry` 成精确的 2× 关系 →
   每条截图都拿 `frame.w*dpr, frame.h*dpr` 校验，不符就重试。
2. 本环境**无法把进程变成前台应用**（`NSApp.activate` /
   `NSRunningApplication.activateWithOptions:` 都返回 False，`osascript` 卡在自动化授权）
   → ① 标题栏只能按**非活动**窗口渲染；② **真实 mouse-move 到不了窗口**，
   `QTest.mouseMove(widget, pos)` 在这个条件下**完全不生效**（实测 `hover_row` 停在 -1、
   `grab()` 逐字节不变）。必须用
   `QApplication.sendEvent(viewport, QMouseEvent(MouseMove, …))` 走
   `QAbstractItemView::viewportEvent`。
   另外 `viewport()` 的坐标原点**已经在表头之下**，算 y 时不要再加 `horizontalHeader().height()`。

## 复核结论（5 个重点）

| # | 重点 | 结论 |
|---|------|------|
| A | Titlebar continuity | 非活动 `#28282b` / 活动 `#373739`，分别与原生 TextEdit（`#272729`）和裸 Qt 活动窗（`#373738`）吻合 → **AppKit 自绘，非 Folith 引入** → 不改 |
| B | Surface hierarchy | page `#1c1c1e` → card `#242426`（Δ8）→ 输入/下沉 `#1a1a1c` → 浮起 `#2a2a2d`，可读 → 不改 |
| C | Borders | 全部 1 logical px（2 device px）反光线：卡片 `#2c2c2e`、表头底线 `#333335`、行发丝线 `#28282a`，与相邻面最大 Δ7 → 不过亮 → 不改 |
| D | Table selection | **有问题** → 已修（见下） |
| E | Segmented control | track `#1a1a1c` + 描边 `#28282a`，选中面 `#242426` + 描边 `#333335`，未选中文字 `#9c9ca3` → 双重信号可读 → 不改 |

## 本轮两处微调

### D. 选中行被 Qt 叠了三层

token 写 `rgba(130,172,255,.18)`，真实窗口渲出来是 **`#495b82`（等效 alpha ≈ .44）**——
Qt 对 `::item:selected` 的背景叠加了三层：QSS 规则 + 基样式 + `selection-background-color`。
选中行因此变成一块饱和蓝，比主行动按钮还抢眼（`crops/selected-row--before.png`）。

只要留 `selection-background-color` 那一层（`::item:selected { background: transparent; }`），
就渲成 token 精确的 **`#2e3546`**（逐通道误差 ≤1）。

> 代价检查：`AssetRowDelegate`（资产列表）不调 `super().paint()`、自己铺一次，
> 本来就只有一层，实测**不受这条影响**，未动它。

### D′. 悬停只有一格亮

`::item:hover` 是**格**级选择器。实测（`02-home-row-hover.png` 与 `01` 的差异包围盒）：

| | 差异包围盒（device px） | 覆盖范围 | 悬停处颜色 | 行内其余 |
|---|---|---|---|---|
| before | `(1126,948)-(1705,1027)` | 580×80 = **恰好一列** × 一行 | `#313133` | `#1c1c1e` |
| after | `(544,948)-(2289,1027)` | 1745×80 = **整个视口宽** × 一行 | `#282829` | `#282829` |

即 before 是「鼠标停在状态列时，只有那一格亮起一块方块」（`crops/hover-row--before.png`），
after 是整行均匀（`crops/hover-row--after.png`）。做法：夜间把格级 hover 置 `transparent`，
改由 `RowDelegate` 按行铺（`_hover_row` 由 view 的 `entered` 信号 +
viewport `Leave` 事件过滤器维护）。浅色 / 纸面不参与（`night_mode()` 门控）。

## 刻意没做

不引入 `NSVisualEffectView` / PyObjC / 侧栏毛玻璃 / 全屏 transparency / 系统 accent /
阴影 / 渐变 / Windows 配色改动 / Light Mode 改动；标题栏保持 Qt 默认（非 Frameless）。
全部改动都在 `theme == "dark"` 门控内，`light` / `paper` 的 QSS 逐字节未变
（`sha256(build_qss)[:16]`：light `72fbdfa39eda1203`、paper `b9f388d2b924cf62`）。

## 一个「看见但按约束不动」的观察

首页工具条右侧是 `[表格 | 卡片]` + 独立标签「项目」+ `[全部项目]` 两个分段控件。
在整窗截图（`after/01-home-table-selected.png`）里，「项目」夹在两个胶囊中间，
读起来像**一个四段控件的破损中段**。这是信息架构 / 排版问题，不属于材质范围，
本轮约束明确禁止改 layout / typography，故**只记录不改**。
（放大裁切 `crops/segmented--after.png` 里两个控件各自独立是清楚的。）
