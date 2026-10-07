# Folith / 译页 纸面模式与「外观与语言」改造（2026-10-02）

结论：`passed`（纸面主题与设置页重做完成；三套主题分别截图核对）

本轮针对两个问题：**纸面模式仍残留大量饱和品牌蓝**，以及**「外观与语言」仍像传统
Streamlit 表单**（卡片套卡片、信息密度低、预览无意义）。修法不是换几个色值，而是
把主题从「日间样式 + 覆盖」改成三套平级的语义 token，再把设置页重做成偏好页。

## 1. 根因

1. `folith/ui/themes.py` 的 `apply_theme()` 用一条 HSL 启发式把样式表里**每一个
   `#hex`** 归一成 `var(--tp-<role>)`，然后只为 `dark` / `paper` 追加一份调色板。
2. 纸面调色板本身是蓝的：`primary:#2251af`、`primary-soft:#e7ebef`、
   `border:#b0c0d6`、`tint-active:#e7ebef`。全表 113 处 `var(--tp-primary)`、
   34 处 `var(--tp-primary-soft)` 因此全部回到钴蓝：单选选中点、主按钮、链接、
   tab 下划线、输入框焦点边，以及侧栏当前项的冷蓝灰底。
3. **`rgba()` 字面量不经过归一化**（正则只匹配 `#hex`），
   `rgba(18,103,232,.14)` 这类蓝色焦点光圈（14 处）在纸面下原样保留。
4. 侧栏背景 `#f8faff` 被启发式判成 `primary-soft`，当时只能靠
   `apply_theme()` 末尾一条 `!important` 兜住 —— 这就是"纸面不断打补丁"的来源。
5. 原生 Streamlit 控件的强调色来自启动参数 `--theme.primaryColor #004cfd`，
   旧样式表没有覆盖单选外环、复选框、开关、滑块，因此它们永远跟随钴蓝。
6. `folith/ui/workspace/performance_view.py` 把状态色写死成 `#2563eb` 等字面量，
   直接进入内联 HTML，同样绕过主题层。

## 2. 架构

```
组件选择器  ->  --tp-<role>（历史角色名）  ->  --<semantic token>  ->  主题取值
```

- `folith/ui/theme_tokens.py`（新）：`TOKENS` 三套主题、`ROLE_ALIASES` 历史名别名、
  `root_css()`、以及设置页 / 主题选择器 / 实时预览 / 原生控件 / 侧栏工具面板的组件层。
- `folith/ui/themes.py`：`normalize_legacy()`（字面色归一，兼容层）→ `:root` token
  → 组件层 → RTL。`:root` 追加在样式表之后，靠级联生效，**不再需要 `!important` 修补**。
- `folith/ui/styles.py`：只做两件事——把绕过归一化的半透明字面色改成 token
  （`--focus-halo` / `--surface-glass` / `--overlay-veil` / `--glass-*` / `--shadow-*`），
  以及把单选指示器、侧栏当前项、输入焦点等指向 token。
- 日间主题仍是字面色基线（不动即不回归），夜间 / 纸面完全由 token 驱动。

`tests/ui_preferences_test.py` 新增回归断言：纸面归一化后的组件样式表里
**不允许再出现饱和品牌蓝**（logo 品牌锚点除外），并断言三套主题 token key 完全一致。

## 3. 纸面取值

| token | 值 | 用途 |
| --- | --- | --- |
| `--bg-page` | `#F2EADB` | 暖灰米白页面底 |
| `--bg-sidebar` | `#ECE3D2` | 比页面略深，层级自然形成 |
| `--bg-surface` | `#FBF7EE` | 纸张面，非纯白 |
| `--bg-surface-sunken` / `--bg-surface-raised` | `#E9DFCB` / `#FDFAF3` | 下沉 / 浮起 |
| `--bg-hover` / `--bg-selected` | `#EAE2D1` / `#E1DCCE` | 悬停 / 当前项 |
| `--text-primary` | `#2D2923` | 暖墨色，非纯黑 |
| `--text-secondary` / `--text-muted` | `#5F594E` / `#6F6858` | 均满足 4.5:1 |
| `--border-subtle` / `--border-strong` | `rgba(70,60,45,.15)` / `rgba(70,60,45,.28)` | 克制分隔 |
| `--accent` | `#4C5A66` | 墨蓝灰：与品牌同色族、不同饱和度 |
| `--accent-hover` / `--accent-active` | `#3E4B56` / `#333E48` | 只略微加深 |
| `--accent-soft` | `#E1DCCE` | 选中面 |
| `--text-on-accent` | `#FAF7EF` | 主按钮暖白文字 |
| `--focus-ring` / `--focus-halo` | `rgba(76,90,102,.42)` / `rgba(76,90,102,.18)` | 键盘焦点可见 |
| `--success` / `--warning` / `--danger` | `#2F6B4F` / `#8A5A17` / `#A63A31` | 低饱和状态色 |

品牌钴蓝只保留在 logo（`--brand-logo-blue`），不再作为交互色；日间主题的交互色仍是
Cobalt，夜间是 `#82ACFF`。详细 token 表见 [docs/brand.md](../../brand.md) §2.1。

## 4. 设置页信息结构

```
外观与语言
调整阅读环境与界面语言，设置会保存到本机。
────────────────────────────────
外观
显示模式          [日间模式][夜间模式][纸面模式✓]   ← 每个选项带该主题的配色预览
阅读预览          原文 / 译文 / 术语 live preview    ← 跟随所选模式即时变化
────────────────────────────────
语言
界面语言          [简体中文 ▾]
────────────────────────────────
                                   [✓ 应用更改]
```

- 取消卡片套卡片：分区标题 + 细分隔线 + 间距承担层级，只有预览本身是一个 surface。
- 设置项统一「左：名称 + 说明 / 右：控件」；窄窗口自动回落为上下排列。
- 阅读预览是真实内容（原文 / 译文 / 术语命中），不是说明卡；切换模式即时反映，
  保存后才应用到整个界面（`preferences.preview_theme()`）。
- 界面语言仍只影响文案，不改文档内容、翻译目标语言或模型输出语言。

## 5. 验证

渲染证据（本地 Streamlit，桌面 CSS 视口 1440×900，`deviceScaleFactor 2`）：

| # | 页面 | 证据 |
| -- | --- | --- |
| 1 | 外观与语言 · 日间 | [01-appearance-light.png](01-appearance-light.png) |
| 2 | 外观与语言 · 夜间 | [02-appearance-dark.png](02-appearance-dark.png) |
| 3 | 外观与语言 · 纸面 | [03-appearance-paper.png](03-appearance-paper.png) |
| 4 | 外观与语言 · 纸面 · 窄窗 760px | [04-appearance-paper-narrow.png](04-appearance-paper-narrow.png) |
| 5 | 外观与语言 · 纸面 · 阿拉伯语 RTL | [05-appearance-paper-rtl.png](05-appearance-paper-rtl.png) |
| 6 | 侧栏工具面板 · 显示模式 · 纸面 | [06-sidebar-theme-popover-paper.png](06-sidebar-theme-popover-paper.png) |
| 7 | 翻译工作台 · 纸面 | [07-workbench-paper.png](07-workbench-paper.png) |
| 8 | 翻译工作台 · 日间（回归对照） | [08-workbench-light.png](08-workbench-light.png) |
| 9 | 翻译工作台 · 夜间（回归对照） | [09-workbench-dark.png](09-workbench-dark.png) |

- **代码审计**：纸面归一化后的组件样式表内饱和品牌蓝命中数为 `0`
  （`tests/ui_preferences_test.py::test_no_saturated_brand_blue_survives_normalizing_the_component_sheet`）。
- **功能**：主题切换 / 保存 / 重启后保持、界面语言保存、预览不写盘、不影响目标语言与
  模型输出语言，均由 `tests/ui_preferences_test.py` 覆盖。
- **回归**：`python scripts/run_regression.py` 1548 passed；5 个失败文件与本轮无关
  （侧栏导航重做中的 `工作区` 分组、`_t()` 误套用户数据、`core.py` 中的服务商 URL），
  其中 `inbox_project_density_test` 的图标字体规则已顺手修正并通过。

## 6. 未覆盖 / 已知

- 阿拉伯语 RTL 仍属实验功能：版式已镜像（侧栏在右、标签右对齐、控件在左），
  但主题选项卡片内的色块仍按 LTR 顺序落在文字左侧，未做逐像素 RTL 验收。
- `gui.py` 仍以 `--theme.primaryColor #004cfd` 启动；已被覆盖的原生控件跟随 token，
  但 Streamlit 未来新增的、尚未覆盖的控件仍会回落到该值。
