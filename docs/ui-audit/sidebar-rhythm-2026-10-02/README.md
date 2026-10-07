# Sidebar alignment / affordance / rhythm — 2026-10-02

本轮只整理 Project Switcher、项目标题和上方交互行。Utility Bar 的渲染、样式和交互均未修改；保留现有 flex / auto margin 贴底布局。没有增加左侧装饰条。

## Root Cause

主题共享层把 Sidebar 的所有 secondary 按钮设为 `background: transparent !important; border-color: transparent !important`，覆盖了选择器原本的 surface 和边框。选择器因此看起来像裸文字。行内箭头与文字一起参与居中，空状态/打开状态标记的 Streamlit 包装层也贡献了间距；标题 margin 与外层 gap 叠加，造成不稳定的分组距离。

修正共享层的选择器例外；选择器用 folder / folder_open 表示项目，名称沿文字方向起始端对齐，独立 chevron 位于末端。长名称由 CSS ellipsis 截断，可访问名称保留全文。

## Spacing System

| Token / rule | Value |
| --- | --- |
| gap xs / sm / md / lg | 6 / 10 / 16 / 24px |
| New Task / navigation height | 48px |
| Project Switcher height | 46px |
| Interactive row radius | 11px |
| Interactive row inner inset | 12px |
| Icon size / icon-text gap | 18 / 10px |
| Project heading height / type | 24px / 13px, 600 |
| Logo → New Task / New Task → project group | 24px |
| Project heading → switcher | 10px |
| Project group → navigation | 24px |
| Navigation → navigation | 6px |

保留现有 Sidebar 外部 inset 24px（本轮不改侧栏宽度和底栏）。New Task、选择器和两行导航都是 width:100%、border-box，在 1280×720 窗口中实测宽187px、LTR 起点 x=24；RTL 起点 x=1069。隐藏状态标记的整个包装层不再参与布局。

## Project Switcher

- Default：`--tp-surface` + 1px `--tp-line`，空状态使用 `--tp-sub`，项目名称使用 `--tp-ink`。
- Hover：`--tp-tint-hover` + `--tp-border-strong`，几何尺寸不变。
- Open：`--tp-surface-sunken` + `--tp-border-strong`，chevron 旋转180°，几何尺寸不变。
- Focus：保留语义色 focus outline；无强阴影或装饰条。
- 项目标题仍是实际 Project Center 入口，保留 chevron，增加文字 hover 提示。
- 补齐四种非中文语言的选择器 tooltip，列表中的系统“未分类任务”使用既有翻译。用户项目名称保持原样。

## Navigation

History 和 Terminology 的 normal / selected 均为48px、相同 padding、radius、图标尺寸与边框宽度。selected 仅换颜色；hover/focus 仍有交互反馈。

实测关闭选择器时两行 y 分别为282.14和336.14，切换 History/Terminology 后位置与尺寸一致。选择器 y=212.14、h=46。底部四个 Utility 控件 y=668、h=36，各状态不变。

项目列表保留现有 inline 展开机制：打开时下方导航整体下移206px，关闭后恢复；这属于列表展开，不是选中导航造成的尺寸变化。底栏仍稳定贴底。

## Theme Check / locale check

在独立临时 OUTPUT_DIR 中检查，未修改实际用户数据和偏好。三种主题的 default / hover / open 都使用现有 semantic tokens：Light 使用中性浅色；Dark 的边界为可见深灰；Paper 使用暖中性色。

| Check | Evidence |
| --- | --- |
| Light / Chinese / History / no project | [01](01-light-history-empty.png) |
| Light / Chinese / open | [02](02-light-switcher-open.png) |
| Light / Chinese / project selected | [03](03-light-project-selected.png) |
| Light / Chinese / Terminology / project selected | [04](04-light-terminology-selected.png) |
| Dark / English / History / no project | [05](05-dark-en-history-empty.png) |
| Dark / English / open / long project | [06](06-dark-en-switcher-open.png) |
| Dark / English / long project selected | [07](07-dark-en-long-project-selected.png) |
| Paper / Spanish / Terminology / no project | [08](08-paper-es-terminology-empty.png) |
| Paper / Spanish / open | [09](09-paper-es-switcher-open.png) |
| Light / Russian / History | [10](10-light-ru-history-empty.png) |
| Dark / Arabic RTL / History | [11](11-dark-ar-history-empty.png) |
| Dark / Arabic RTL / open | [12](12-dark-ar-switcher-open.png) |
| Light / Chinese / Terminology / no project | [13](13-light-terminology-empty.png) |

DOM 尺寸及颜色记录：[measurements.json](measurements.json)。阿拉伯语沿 RTL 起始端对齐、图标在右、箭头在左；其余语言左对齐。长名称不改变行高。

## Verification

- 44个既有 Project Switcher / Context 测试通过；更新其中旧视觉尺寸断言以匹配本轮规格。
- 8个既有语言目录 / theme palette / 翻译生成契约检查通过。
- 翻译改动后重新运行系统 Inbox 行与菜单开关的两项行为检查，均通过。
- 四种非中文目录的两个新增 tooltip key 均可正常查询。
- 浏览器实际检查1280×720桌面窗口；未进行所有 theme × locale × state 的完整笛卡尔组合，也未覆盖手机窗口和大量项目列表。应用其他区域中的既有翻译缺口不在本次 Sidebar 整理范围内。
