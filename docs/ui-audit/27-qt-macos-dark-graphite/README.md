# Qt 桌面客户端 · macOS Dark Mode 视觉重构 — 2026-10-08

只换材质（color / surface / border / separator / hover / selection / focus /
native palette mapping），**不动**布局、信息架构、控件位置、尺寸、表格结构、
导航与任何业务逻辑。日间与纸面主题的样式表逐字节未变。

## Root Cause

夜间主题此前是**冷色日间主题的再着色**：页面 `#111820`、侧栏 `#17212c`、
卡片 `#1b2531`、边框 `#2c3a49 / #3b4b5d / #4e6075`、选中 `#203855` —— 一整条
蓝黑色阶。而 macOS 的窗口 chrome（标题栏 / 菜单 / sheet）由 AppKit 用中性石墨灰
绘制，两者色温、层级与"边界感"都不一致，于是看起来像"Mac 窗口里嵌了一张网页"。

放大这种割裂的是三条结构性习惯：

1. **Border 是蓝灰实体线**：卡片、表头、输入框、分段控件、表格整块都有可见描边；
2. **选中是一块高饱和深蓝** + 行首亮蓝竖线（Web Admin 表格的语言）；
3. **Accent 蓝被用于背景与边界**（`--accent-soft: #203855` 当选中面、
   `--accent-border: #4c709e` 当默认边框），于是"结构"和"交互"都发蓝。

量化（画布区域采样，见 `measurements.json`）：改造前 **99.8%** 的画布像素带蓝倾向
（主色 `#182030`）；改造后 **6.4%**（主色 `#181818` / `#202020`），剩下的蓝只来自
选中行、主按钮与状态文字。

## Theme Architecture

没有新增平行主题系统。仍然是一条链：

```
macOS 系统外观 (QStyleHints::colorScheme)
        ↓
folith/qt/theme.py      ← token 取值 + qcolor()（token→QColor，支持 rgba）
        ↓
folith/ui/theme_tokens.py  ← 三套主题的语义 token 唯一真值（本次只改 dark 取值）
        ↓
folith/qt/style.py      ← build_qss()/apply_theme()：QSS + QPalette + 原生外观
        ↓
folith/ui/styles.py（旧 Streamlit 壳）与 Qt 组件共用同一份 token
```

## Changed Files

| 文件 | 改动 |
| --- | --- |
| `folith/ui/theme_tokens.py` | `TOKENS['dark']` 整体换成中性 graphite 色阶（面 / 文字 / 分隔 / 状态）；日间与纸面取值一字未动 |
| `folith/qt/theme.py` | 新增 `qcolor()`：token→QColor，正确处理 `rgba(...)`（`QColor("rgba(...)")` 会静默变黑） |
| `folith/qt/icons.py` | 图标取色走 `qcolor()`，半透明 token 不再画成黑色 |
| `folith/qt/style.py` | `_night_surface_rules()`（夜间去盒子）、`night_mode()`、`apply_theme()` 补齐夜间调色板角色 + `_sync_native_appearance()`、委托取色改走 `qcolor()`、夜间去掉选中行左侧高亮竖线 |
| `folith/qt/views.py` / `dialogs.py` | 表格/项目行的 `setForeground(QColor(token))` 改走 `style.qcolor()` |
| `docs/brand.md` | 新增「夜间（Night）取值」小节，记录色阶、两条结构约定与浅色不变量的基线 |

## Major Visual Changes

- **Surface**：`#1C1C1E`（窗口）→ `#202022`（侧栏）→ `#242426`（卡片/输入）→
  `#2A2A2D`（菜单/按钮悬停），全部中性、`R=G`、`B` 最多 +2（与 `#1C1C1E` 同族）。
  层级由面本身承担，不再依赖描边。
- **Border / Separator**：全部改为半透明白
  `rgba(255,255,255,.040→.14)`——极弱反光而非蓝灰实体线；卡片描边实际不可见，
  结构由面差 + 间距表达。KPI 卡 = 略微浮起的面 + 几乎不可见的边 + 原有一层极轻投影。
- **Table**：整块不再是"页面里的深蓝卡片"。表格背景透明、无边框无圆角，落在页面底上；
  表头 = 略高一点的面（`#212123`）+ 一条底线；行 = 同面 + 每个单元格底部一条
  `rgba(255,255,255,.055)` 发丝线（由 `RowDelegate` 自绘）。
- **Selection**：`bg-selected` 改为 `rgba(130,172,255,.18)`（accent 低透明度渍），
  选中行文字保持 `text-primary`（不刷纯白粗体）；**删除了行首的高亮蓝竖线**
  （夜间；日间/纸面保持原状）。
- **Sidebar**：与主区不再色相不同——侧栏 `#202022` / 主画布 `#1C1C1E`，靠亮度分层；
  分隔线 `rgba(255,255,255,.055)`（原来是蓝灰 Divider）。
- **Titlebar transition**：窗口与画布同为 `#1C1C1E`（macOS 深色 `systemGray6` 亮度带），
  并通过 `QStyleHints::setColorScheme()` 让**原生标题栏跟随界面主题**（见下）。
- **States**：默认按钮 `#242426` → 悬停 `#2A2A2D`（不透明，保证在页面与卡片上都可见）
  → 按下 `#1A1A1C`（"按下去"而不是"变蓝"）；主按钮与危险按钮的
  `:hover/:pressed` 由各自的 id 规则接管，未受影响。禁用态用中性下沉面 + 低透明文字。

## Native Integration

- **`QStyleHints::setColorScheme()`**（Qt 6.8+，macOS 由 cocoa 插件落到 NSApp appearance）：
  显式选择"日间/夜间"时钉住原生外观，使标题栏、菜单、sheet 与内容区同一材质；
  "跟随系统"传 `Unknown` 把决定权还给系统。值相同不调用（该 API 会发
  `colorSchemeChanged`，而窗口接了这个信号，无脑写会形成回环）。
  实机探测（非 offscreen）：`platform=cocoa`，系统初始 `Dark`，
  `set Dark/Light` 生效、`set Unknown` 回到系统值。
- **QPalette**：原有 11 个角色改为经 `qcolor()` 写入（原先直接 `QColor(rgba)` 会变黑）；
  夜间额外补齐 `PlaceholderText / Link / LinkVisited / Mid / Midlight / Light / Dark /
  Shadow`，避免未被 QSS 覆盖的控件落回**浅色的平台调色板**。
- **未使用**：NSVisualEffectView / 全屏毛玻璃 / 自定义 titlebar——本轮明确不做，
  见「Remaining Imperfections」。

## Compatibility

- **日间 / 纸面**：`theme_tokens` 里取值未动；`build_qss('light')` 的 sha256 仍是
  `72fbdfa39eda1203…`（改动前基线），`paper` 仍是 `b9f388d2b924cf62…`；夜间专属的
  结构规则（透明表格、按下变深、去竖线）由 `style.night_mode()` / `build_qss` 里的
  主题判断隔离；补齐的 QPalette 角色只在夜间写入。**浅色模式输出与改动前逐字节一致。**
- **Windows**：共用同一份 token（有意为之，符合"Shared Folith identity + platform-native
  material"的长期方向），因此 Windows 夜间也从蓝黑变为中性 graphite；Windows 日间不受
  影响。本轮**未**为 Windows 单独取调色板（Fluent/accent 留待后续）。
- 附带影响：旧 Streamlit 壳的夜间主题同样变中性（同一份 token）——这是有意的，避免
  同产品两套夜间语言。

## Validation

- **截图**（`QT_QPA_PLATFORM=offscreen` 离屏渲染真实窗口）：
  `01` 任务库 · 选中行、`02` 行悬停、`03` 卡片视图、`04` 工作台、`05` 设置对话框、
  `06` 菜单、`07/07b/08` 日间对照、`00` 改造前、`10` before/after 并排。
  复现：`QT_QPA_PLATFORM=offscreen venv/bin/python tmp/theme_shot.py <out_dir> <theme>`
  （夹具在 `tmp/`，不入库）。
- **像素审计**：`measurements.json`（画布偏蓝像素 99.8% → 6.4%）。
- **状态渲染取样**（`QStyle.drawControl` + `CE_PushButton`，避开肉眼判断）：
  夜间默认按钮 rest `#242426` / hover `#2a2a2d` / pressed `#1a1a1c`；
  主按钮 `#82acff → #a1c0ff → #b6ceff`；危险主按钮 pressed `#ffaaa4`。
  日间默认按钮 rest `#ffffff` / hover `#f7f9fc` / pressed `#eef4ff`（与原实现一致）。
- **测试**（逐文件独立进程，`TMPDIR=$(mktemp -d)`）：
  `qt_client_style_test` 8 ✓、`qt_settings_dialog_ui_test` 8 ✓、`qt_home_cards_test` 10 ✓、
  `qt_prompts_test` 6 ✓、`qt_delivery_ui_test` 13 ✓、`qt_client_window_test` **71 ✓**（3m31s）、
  `sidebar_ai_engine_footer_test` 16 ✓、`sidebar_geometry_contract_test` 17 ✓、
  `history_library_test` 38 ✓、`ui_preferences_test` 24 ✓（含"三套主题 key 相同"与
  "非日间不残留饱和品牌蓝"）。
  既存失败（与本轮无关）：`brand_regression_test::test_legacy_brand_hits_are_allowlisted`
  ——命中全部来自并行工作流新建的 `docs/performance-real-tasks-2026-10-08/`，本轮改动的
  6 个文件一条命中都没有。

## Remaining Imperfections

1. **没有真正的 macOS material**。标题栏只是"色温对齐 + appearance 跟随"，
   没有 `NSVisualEffectView` 侧栏毛玻璃；那需要 pyobjc（当前 venv 无 AppKit）或自定义
   原生窗口，且必须真机截图验证，本轮按约束不做。
2. **Accent 仍是品牌浅蓝 `#82acff`**，不是系统 accent（macOS 深色 `#0A84FF`）。
   原因：token 必须是"主题名的纯函数"（`theme.tokens(name) == TOKENS[name]` 有回归断言），
   运行时读取系统 accent 会破坏这个不变量；如需系统 accent，应在 token 层增加
   `accent-source: system` 的显式机制，而不是在渲染层偷偷替换。
3. **分段控件的轨道**（`bg-surface-sunken` `#1A1A1C`）只比页面深 2 级，轨道轮廓很弱；
   选中段靠面差表达。若要更接近 macOS 的分段控件，需要一档更深的"轨道"token。
4. **表头文字**仍是 `text-muted`（`rgba(235,235,245,.38)`），在深色下偏暗；
   这是既有排版选择，本轮不动字体层级。
5. 选中行是 accent 18% 的渍，比 macOS Finder "窗口非活动时"的灰选中更醒目；
   这是按任务要求（accent 18–24%）取值的结果。
6. 离屏截图看不到真实标题栏，标题栏连续性只有"色值对齐 + cocoa 通路实测"两条证据，
   最终观感需要在真机上再确认一次。
