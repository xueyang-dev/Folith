# 31 — Qt 桌面端发布前收尾（r5：release-finish pass）

本轮**不做整体重设计**，只修最后一批可观察缺陷。四个 Task 全部有 Before/After 证据；
没有为制造差异而追加改动。

## 修改的问题与根因

| Task | 问题 | 根因 | 修法 |
|---|---|---|---|
| 1 | 外观页三张主题预览卡渲染成 **30px** 高，色块占满整卡、主题名与色块叠印、第三张卡的主题名整行被裁 | 泛用 QSS `QPushButton { min-height: 30px }` 会**覆盖** `setFixedSize(120,78)` 设下的最小高度（Qt 的 QSS min/max 指内容盒）——与上一轮"控件 40px"是同一条物理，只是方向相反 | 尺寸写进 `style.THEME_CARD`（单一真值），QSS 用 `min/max-width/height`（减去 2×2px 边框）+ 代码 `setFixedSize` 三处一致；卡片 124×84，内边距/间隙/色块高度全取同一常量 |
| 1 | 主题卡键盘焦点**不可见** | `QPushButton:focus`（0 个 id）在 Qt 的优先级里压不过 `QPushButton#themeCard`（1 个 id） | 新增 `QPushButton#themeCard:focus`；实测焦点边框 `#303032 → #82acff`（accent）。静止态边框统一 2px，选中不再引起 1px 内容位移 |
| 2 | 两颗筛选复选框**互相压住 3px**（"只看人工修改"的末字被下一颗的方框盖住） | QSS 主题下，`QHBoxLayout` 按"样式内容的内缩框"给 QLabel/QCheckBox 分配槽位，比控件整框窄 **11px**（复选框 110→99、"筛选"标签 24→22），槽位定位用了窄值、控件却按整框画 | `Qt.WA_LayoutUsesWidgetRect` 挂在工具条的标签+两颗复选框上，实测 shortfall 11px → **0**。只挂这一条工具条：设置页的复选框在竖排布局里，改全局会动那里已校准的高度 |
| 2 | 控件过度紧贴 / 组边界不清 / 窄窗时搜索框被压到 164px | 统一 8px 间距把"状态分段"与"筛选开关"挤成一组；搜索框没有下限 | 组间额外 4px（合计 12px）、组内 8px；`search_edit.setMinimumWidth(200)`。工具条总高 39 → **32px**（更矮，未增加） |
| 3 | 启动期"数据目录已更正…"被写进工作台工具区的运行说明行，**长期留存** | `_notice` 同时写底栏与工作台说明行；底栏有 8s 过期，说明行没有 | `_notice(..., status_bar_only=True)`：与当前任务无关的启动告知只进底栏（8s 自动消失），真正要处理的错误仍走 `_failure`，没有隐藏 |
| 3+4 | 「数据目录（relative）已更正为 …」把内部英文枚举印给用户 | `enforce_output_dir` 的返回串直接上 UI | 用户语言「数据目录已更正为 …」；内部原因（relative / inside the install directory）只进启动日志 |
| 4 | 外观页说明直接写 Python 模块名 `folith.ui.theme_tokens` | 实现细节放进普通设置页 | 改为「三套主题共用同一份品牌配色，切换不会影响已导入的任务与译文」 |
| 4 | 关于对话框印数据目录 / 日志目录两行路径（不可复制、不可显示） | 与「诊断信息」重复且没有操作入口 | 移除两行路径，指向「帮助 → 诊断信息…」（那里有只读输入框可复制 + Finder 显示按钮）；版本/构建保留 |

## Before / After（同窗口帧，dpr=2，107 个真实任务）

| 证据 | 说明 |
|---|---|
| `before/crop-theme-cards.png` vs `after/crop-cards.png` | 主题卡 30px 裁切 → 124×84 完整；说明文字不再贴着色块 |
| `before/crop-cb.png` vs `after/crop-cb.png` | 复选框 gap −3px（互压）→ +8px |
| `before/crop-toolbar.png` vs `after/03-workbench__client.png` | 工具条 39 → 32px，筛选组有组间距 |
| `after/06-settings-appearance-LIGHT__client.png` | Light Mode 下三张卡与主题名全部可读（"夜间"卡的主题名此前几乎不可见——说明文字改用**当前**主题的文字色，只让色块用被预览主题的 token） |
| `after/03-workbench-longname__client.png` | 88 字符长文件名省略显示，长原文/长译文在表格内省略，不挤压进度与按钮 |
| `after/04-workbench-narrow__client.png` | 940×660：工具条 8 个控件全部可见、最小间距 8px、双栏不重叠 |
| `after/01-home-table__client.png` / `02-home-cards__client.png` | Home 表格 / 卡片（未改，作为回归基线） |
| `after/05-settings-model__client.png` | 模型设置 780×640 无滚动 |
| `after/10a-theme-card-focus__client.png` + measurements | 焦点/选中/悬停三态反馈 |

## 验证

- **回归**：12 个测试文件 **230 passed / 0 failed**（逐文件独立进程 + 独立 TMPDIR）+ `app_boot_test` 通过。日志 `/tmp/qa-run/r5-final/`。
- **主题不变量**：light / paper **零夜间规则泄漏**；新基线 light `c3b8fa9900246d85`、paper `c18f15e21272c328`、dark `4c31f48927321f38`（有意漂移：themeCard 规则块）。
- **键盘可达性**（offscreen 探针）：工作台 Tab 序 搜索框 → 分段 → 两颗复选框 → 审校所选段落 → …；外观页 Tab 序 色卡1 → 色卡2 → 色卡3 → 主题下拉 → 保存 → 取消。
- **语义探针**：启动后工作台说明行为空；底栏消息 8s 过期；关于对话框不含内部路径；外观页无模块名。

## 剩余问题（明确不修 / 环境限制）

1. **真窗口里焦点环拍不出来**：本沙箱无法把进程变成前台应用（`NSApp.activate` 返回 False），
   非活动窗口不渲染焦点态。规则本身已在 offscreen 下像素级验证（`#82acff`），
   真实前台窗口按同一条 QSS 渲染。
2. 顶部提示条「尚未配置可用的模型连接：尚未选择模型提供商。」两句都以"尚未"开头，
   读起来重复。改文案要同步 5 个语言文件（en/ru/es/ar/zh-CN），且现有措辞已满足
   "原因 + 解决方法"，本轮按"不为差异而改动"的约束**未动**。
3. 窄于 940px 的窗口宽度达不到：段落表最小 360 + 检查器 400 决定了主窗口下限，
   工具条在该下限处刚好放下（右缘 926 / 可用 926）。
4. 外观页"主题"下拉与三张色卡是**两个**入口（保存以下拉框为准，色卡只是快捷选择），
   这是既有设计，本轮不动。
