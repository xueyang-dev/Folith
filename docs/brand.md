# Folith · 译页 品牌与视觉基准

本文以用户提供的品牌标准图 `docs/assets/folith-brand-standard.png` 为文字与版式参考，
**图标几何以 2026-10-16 修正版为唯一标准**（见文末「图标几何修正」）。仓库里只有这一个
Folith logo：向量源是 `docs/logo-exploration/folith-mark-final.svg`，其余标记、lockup、
App 图标都从它派生；旧几何的文件已全部删除，不再保留备份。

## 0. 正式品牌

| 语言 | 品牌名 | 定位 |
| --- | --- | --- |
| English | `Folith` | `Agentic Translation Workspace` |
| 中文 | `译页` | `智能体翻译工作台` |

组合字标统一写 `Folith · 译页`——拉丁与中文之间留一个**细空格**：中拉丁紧贴时
`h` 的下伸与 `译` 的横画会撞在一起，细空格同时提示这是两个词、不是拼写错误。
（用户可见的组合字标是一张位图 lockup，见 §1；这里的写法约束的是**文字版**字标：
窗口标题、设置页品牌行、README 与打包描述。）
正文可根据语言写 `Folith` 或 `译页`。
不要把品牌扩展成 `Folith AI`、`译页AI` 或普通的 AI Translator。

## 1. Logo

logo 继续使用两页、文/A 字形和白色指针组成的 icon mark：

| 元素 | 含义 |
| --- | --- |
| 两页叠放的圆角页面（后页钴蓝、前页横向渐变） | 长文档及其结构 |
| 页面上的 **文** 和 **A** | 中文 ↔ 英文的本地化工作 |
| 覆盖两页的白色指针光标 | Agent 在文档上执行工作 |

不重绘、不换色、不加阴影，也不重新设计图形。需要图标时从下面的资产矩阵里取，**不要**从旧提交或其他地方复制。

### 品牌标准图

![Folith · 译页 品牌标准图](assets/folith-brand-standard.png)

### 资产矩阵

| 版本 | 文件 | 来源 | 用在哪里 |
| --- | --- | --- | --- |
| 品牌标准图 | `docs/assets/folith-brand-standard.png` | 用户提供 | 本规范的完整参考（文字与版式；其中图标为旧几何，以下文件为准） |
| **标准向量源** | `docs/logo-exploration/folith-mark-final.svg` | 2026-10-16 修正版 | 唯一真源 |
| 标准向量（随包） | `folith-mark.svg` | 逐字节等于标准向量源 | 侧栏 / 页头标记（蓝色主题色）、向量复用 |
| 翠绿 / 石板主题色向量 | `folith-mark-green.svg`、`folith-mark-slate.svg` | 标准向量源仅换两条渐变色标 | 对应主题色的侧栏标记 |
| 图标高清母版 | `folith-mark-hires.png` | 标准向量源渲染（1254×1254，透明底） | App 图标母版、位图回退 |
| 页面图标 | `folith-mark.png` | `scripts/render_brand_assets.py` 从母版缩放（256×256） | 浏览器标签页、安装入口 |
| App 图标 | `folith-app-icon-white.png` | `scripts/build_app_icon.py` 从母版合成 | Qt 应用 / 关于 / 欢迎、Windows `.ico`、macOS `.icns`；1024px 透明画布、824px 纯白连续曲线底板、品牌标宽约 70% |
| 透明横向组合 | `folith-lockup-transparent.png` | 用户提供的字标 + 标准图标 | 日间主题侧栏；lockup 母版 |
| 夜间透明横向组合 | `folith-lockup-transparent-dark.png` | 母版仅调整文字颜色（`scripts/extract_brand_png.py`） | 夜间主题侧栏 |
| 白底横向组合 | `folith-lockup.png` | 母版叠在白底上（`scripts/extract_brand_png.py`） | README 首屏 |

lockup 的字标是用户提供的位图，**不要**重绘或换比例；只允许替换图标部分。
改动标准向量源后，依次重新渲染母版、重跑上面三个脚本，并运行
`python -m pytest tests/brand_assets_test.py -q`。

## 2. 色板

色值取自 icon mark；日间界面主色 `#2B5FD1` 就是 `folith/ui/theme_tokens.py` 里 `light` 的
`accent`（夜间是 `#82ACFF`）。`tests/brand_assets_test.py` 守的是**资产**
（几何、尺寸、位图是否存在、README 引用），**不校验色值**——色板目前没有测试兜底。

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| Navy 深海军蓝 | `#000D2D` | 字标、App 图标底、最深色 |
| Cobalt 钴蓝 | `#004CFD` | 图标后页（只属于 logo） |
| Azure 蔚蓝 | `#0088FD` | 图标渐变中段 |
| Cyan 青 | `#00E8FE` | 图标渐变亮端与强调点 |
| Ink 正文墨色 | `#0B1F3B` | 标题与当前项 |
| Sub 次要文字 | `#3E495D` | 副标题与说明文字 |

**日间界面主色**（2026-10-16 色彩审校，`docs/ui-audit/40-design-review-2026-10-16` 第十一节）：

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| UI Blue 界面蓝 | `#2B5FD1` | 主按钮、当前选中、焦点环、链接 |
| hover / active | `#2352B8` / `#1D469D` | 同色相更深阶 |

界面蓝与钴蓝同色相（OKLCH h≈263°），彩度从 0.267 降到 0.185：钴蓝接近屏幕能显示的
最高彩度，放在白底的小按钮和描边上会"发颤"。语义色统一到同一明度（OKLCH L 0.55）：
成功 `#25855B`、警告 `#9D621E`、危险 `#BD423A`、信息 `#7761B0`。

### 2.1 外观、主题色与语义 token

桌面端的外观是 **日间 / 夜间 / 跟随系统**，主题色是 **蓝色 / 翠绿 / 石板**。日间与夜间是
**平级的 token 集合**，不是"日间样式 + 覆盖"；主题色只覆盖 `ACCENT_TOKENS` 里的强调色：

**纸面与翠绿整套主题已于 2026-11-04 从桌面端废弃**：设置里早已选不到，旧偏好继续迁移成
日间加石板 / 翠绿主题色。纸面的色值保留在下文，供以后的 Android 端取用。

```
组件选择器  ->  --tp-<role>（历史角色名）  ->  --<semantic token>  ->  主题取值
```

- 取值集中在 `folith/ui/theme_tokens.py` 的 `TOKENS`：两套主题的 key 完全相同，
  `ROLE_ALIASES` 把历史 `--tp-*` 名绑到语义 token 上，组件 CSS 无需一次性改写。
- `folith/ui/theme_tokens.py` 的 `root_css(theme)` 生成 `:root` 块（语义 token + `--tp-*` 别名），
  消费方把它追加在历史样式表之后，靠级联生效，不需要 `!important` 去修补某个主题。
- 桌面端（Qt）没有"历史字面色按主题自动归一"这一层：`folith/qt/theme.py` 的 `tokens()`
  只在换主题色时重算 `accent-soft / accent-border / focus-ring / focus-halo` 等派生项，
  所以**任何半透明色都必须直接取 token**，不要手写 `rgba()` 字面量
  （`QColor("rgba(...)")` 在 Qt 里解析失败会静默变黑，必须走 `folith.qt.theme.qcolor()`）。
  焦点光圈、玻璃面这类半透明色的 token 是 `--focus-halo` / `--surface-glass` / `--overlay-veil`。

**品牌蓝只属于 logo。** 日间主题的交互色是界面蓝 `#2B5FD1`，夜间是浅蓝 `#82ACFF`；
石板主题色沿用纸面的**墨蓝灰** `#4C5A66`。它们都不允许把 logo 的饱和钴蓝复制到按钮、
单选、导航当前项或状态点上。

#### 纸面（Paper）取值：已从桌面端废弃，留给 Android

当前 `folith/ui/theme_tokens.py` 只保留 `light` / `dark` 两套 token（纸面整套已删）；
下表数值取自退役前的提交 `7d565d80`，只作 Android 端的取色参考，桌面端不加载。

| 语义 token | 值 | 说明 |
| --- | --- | --- |
| `--bg-page` | `#F2EADB` | 暖灰米白页面底，不过黄 |
| `--bg-sidebar` | `#ECE3D2` | 比页面略深，层级自然形成 |
| `--bg-surface` | `#FBF7EE` | 纸张面，不用纯白 |
| `--text-primary` | `#2D2923` | 暖墨色，非纯黑 |
| `--text-secondary` | `#5F594E` | 副标题与说明 |
| `--text-muted` | `#6F6858` | 仍满足 4.5:1（页面底与面底都够） |
| `--accent` | `#4C5A66` | 墨蓝灰：与品牌同色族、不同饱和度 |
| `--accent-hover` / `--accent-active` | `#3E4B56` / `#333E48` | 只略微加深 |
| `--accent-soft` | `#E1DCCE` | 当前项与选中面 |
| `--text-on-accent` | `#FAF7EF` | 主按钮文字，暖白 |
| `--border-subtle` | `rgba(70,60,45,.15)` | 克制的分隔 |
| `--focus-ring` / `--focus-halo` | `rgba(76,90,102,.42)` / `rgba(76,90,102,.18)` | 键盘焦点可见 |
| `--success` / `--warning` / `--danger` | `#2F6B4F` / `#8A5A17` / `#A63A31` | 低饱和状态色 |

纸面**不是**「日间 + 米黄背景」：正文、面、边、强调、状态色全部重新取值，
所以遮住 logo 也应当能感到这是一套独立的阅读主题。

#### 夜间（Night）取值（2026-10-08 改为中性 graphite）

夜间**不是**冷色日间主题的再着色：macOS 的窗口 chrome（标题栏、菜单、sheet）由
AppKit 用自己的一套中性石墨灰绘制，深蓝黑的内容区因此看起来像"Mac 窗口里嵌了一张
网页"。夜间主题改成与 macOS 深色窗口同一亮度带的中性灰（`#1C1C1E` 即深色
`systemGray6`），标题栏与内容区才像同一种材质。

| 语义 token | 值 | 说明 |
| --- | --- | --- |
| `--bg-page` | `#1C1C1E` | 窗口 / 画布 |
| `--bg-sidebar` | `#202022` | 比窗口高一档，靠亮度而非色相区分 |
| `--bg-surface` | `#242426` | 卡片 / 输入框 |
| `--bg-surface-subtle` / `--bg-surface-sunken` / `--bg-surface-raised` | `#212123` / `#1A1A1C` / `#2A2A2D` | 表头 / 下沉井 / 浮起面（菜单、按钮悬停） |
| `--bg-hover` | `rgba(255,255,255,.05)` | 悬停是一场极弱的白光 |
| `--bg-selected` | `rgba(130,172,255,.18)` | 选中 = accent 的低透明度渍，不是"另一套深蓝" |
| `--text-primary/secondary/muted` | `rgba(255,255,255,.92)` / `rgba(235,235,245,.62)` / `(…,.38)` | 半透明文字，与原生 labelColor 同族 |
| `--border-faint … --border-strong` | `rgba(255,255,255,.040/.055/.065/.085/.14)` | 边界是**极弱反光**，不再是蓝灰实体线 |
| `--accent` | `#82ACFF` | 只用于主行动 / 选中 / 焦点 / 进度 / 链接 |
| `--success/warning/danger-soft` | `rgba(…,.14)` | 状态芯片是一层色渍，不是彩色面板 |

两条结构性约定（`folith/qt/style.py`）：

1. **层级靠面**：`bg-page → bg-surface → bg-surface-raised` 单调递增；边框退到
   几乎不可见，因此"页面底 + 卡片面"就是层级本身，而不是层层套框。
2. **半透明分隔**：分隔线/悬停/选中都写成 `rgba()`，叠在哪种面上都能正确合成。
   ⚠️ **`QColor("rgba(...)")` 在 Qt 里解析失败并静默变成黑色**，Qt 侧任何
   token→QColor 的地方都必须走 `folith.qt.theme.qcolor()`（风格层再导出为
   `style.qcolor`）。

夜间还额外做了两处**只作用于夜间**的结构简化（`build_qss` 末尾的
`_night_surface_rules()` 与 `style.night_mode()`）：任务表格 / 列表不再是"页面里的
一张卡片"（透明底 + 无边框 + 无圆角），以及按下态不再变蓝。日间的样式表
**逐字节不变**（`sha256(build_qss('light'))` 作为回归基线）。

### 2.2 界面里还没进 token 的字面色（F20 待收）

下面这些颜色现在写死在控件里，换主题 / 换主题色时不会跟着变。按 F20 的口径应当收敛成
token（`folith/ui/theme_tokens.py` 一处取值，Qt 侧经 `folith.qt.theme.qcolor()` 取用）。
**本节只登记，不动代码**（由 Lead 与 `style-tokens` 实施）。

| 色值 | 用在哪 | 现状 | 应收敛到 |
| --- | --- | --- | --- |
| `#ffe08a` 背景 / `#202124` 文字 | PDF 原页与搜索命中高亮（`folith/qt/source_panel.py:23` 的 `_HIGHLIGHT_STYLE`） | 写死在 HTML 片段里，两套主题共用同一个值 | `--highlight-mark` / `--highlight-on-mark`，两套主题各一份（可由 `warning` 派生） |
| `#1f7a4d` / `#ffffff` | 译文差异片段「插入」（`folith/qt/segment_assist.py:602`） | 写死 | `--diff-insert-bg` / `--diff-insert-fg` |
| `#7a1f1f` / `#e8b4b4` | 译文差异片段「删除」（`folith/qt/segment_assist.py:604`） | 写死 | `--diff-delete-bg` / `--diff-delete-fg` |
| `#fdfcf8` / `#efe8d8` / `#cfc7b6` / `#dcd8cf` | 任务卡纸页封面与页影（`folith/qt/components.py:1141 / 1208 / 1352`） | 纸面主题退役后剩下的暖白，夜间在控件里另开 `dark` 分支 | 换成 light 主题 `surface` 的派生值（或显式 token），不要留在控件里 |
| `#ffffff` / `rgba(255,255,255,…)` / `#ff9f9a` / `#8fe6ad` | 深色玻璃封面上的文字与状态色（`folith/qt/style.py:149-150`） | 写死，只有夜间分支 | 由 `--bg-*` 与语义色派生 |
| `#ffffff` / `#1f2328` / `#3d434b` | 文档缩略图里画「一页纸」（`folith/qt/thumbnails.py:126 / 139`） | 写死，但**是刻意的**：缩略图画的是文档纸面，不是界面色 | 保留，代码注释里写明理由即可 |

## 3. 字体与副标题

桌面端**不随包任何字体文件**，一律使用系统字体栈（`folith/qt/theme.py` 的 `FONT_STACK`）：

```
SF Pro Text -> Segoe UI -> Helvetica Neue -> PingFang SC -> Hiragino Sans GB
-> Microsoft YaHei -> Geeza Pro -> Arial -> Noto Sans Arabic -> Noto Sans SC
```

- 拉丁：`SF Pro Text`（macOS 默认）→ `Segoe UI`（Windows）→ `Helvetica Neue` / `Arial`；
- 中文：`PingFang SC` → `Hiragino Sans GB` → `Microsoft YaHei` → `Noto Sans SC`；
- 阿拉伯语：`Geeza Pro` → `Noto Sans Arabic`；
- 等宽（日志 / 诊断 / 代码类）：`SF Mono` → `Menlo` → `Monaco` → `Courier New`；
- 字号只取 7 档：`11 / 12 / 13 / 14 / 17 / 22 / 28`（`theme.py` 的 `FONT_STEPS`），
  层级靠「大小 + 粗细 + 颜色」表达，不靠把一切加粗。

**阅读字体是设置项**（设置 · 阅读）：候选只列本机已安装的字族，默认「跟随界面字体」，
不写死任何平台的字体；阅读区的行距是规则不是开关——拉丁 ≈1.5、中文 ≈1.7。

**字标不是排版出来的字**：`Folith · 译页` 的组合字标是用户提供的**位图 lockup**
（`folith-lockup-transparent.png` / `folith-lockup-transparent-dark.png`，见 §1 资产矩阵），
所以字标没有"字体族 + 字重"可规定；侧栏 / 页头里与标记并排的文字走上面的系统字体栈。
历史上 Web 端（Streamlit）用过 Google Fonts 的 Manrope，那套界面已退役，
仓库里既没有 Manrope 字体文件、Qt 字体栈里也没有它，本文不再保留这个说法。

- 英文副标题：`Agentic Translation Workspace`；
- 中文副标题：`智能体翻译工作台`。

中文副标题保留现有字距规则；界面与品牌位图共处一张画面时，以系统字体栈的默认字距为准。

## 4. 资源生成与回归

派生位图由脚本生成：只修改标准向量源（并重新渲染母版），然后重跑并检查。

```bash
python scripts/render_brand_assets.py          # folith-mark.png（页面图标）
python scripts/render_brand_assets.py --check
python scripts/extract_brand_png.py            # folith-lockup.png、夜间 lockup
python scripts/build_app_icon.py --png-out folith/resources/brand/folith-app-icon-white.png --out build/qt-desktop/Folith.icns
python -m pytest tests/brand_assets_test.py -q
```

脚本只依赖 Pillow（`extract_brand_png.py` 另需 NumPy），只写自己的产物。
素材被替换或删除时，`tests/brand_assets_test.py` 会立刻失败。

## 5. 工作台应用

侧栏、页面图标、启动器和导出署名使用同一个标准标记：侧栏品牌位使用透明 lockup（日间 / 夜间各一份）或 `folith-mark*.svg`，
页面图标 = `folith-mark.png`；Qt App 图标 = `folith-app-icon-white.png`，由 `scripts/build_app_icon.py` 从高清母版合成。组合字标统一为
`Folith · 译页`。README 首屏展示 `folith-lockup.png`。页面仍保持既有侧栏、任务步骤、
内容面、圆角、间距和颜色，不借 rebrand 之机改动导航或交互流程。

## 6. 兼容边界

`folith` 是当前 Python 代码包和主 console 入口；PyPI 发行包名仍为
`foliothread`，并保留同名 console 入口。

已有 `FOLIOTHREAD_*` 环境变量、项目 schema、内存格式和历史资源名属于
兼容性边界，不能仅为品牌整洁而删除或重命名。它们不改变产品表层品牌。

## 7. 独立代码包（2026-10-01）

代码包、资源路径、线程名称和新导出的 TBX/TMX 扩展字段统一使用 `folith`。
评测凭据变量为 `FOLITH_EVAL_API_KEY`。旧 TransPraxis 包和启动命令已移除，
不提供旧包导入别名。已有 `outputs/` 任务、项目和凭据继续从原位置读取，
无需搬移数据。CHANGELOG 与已有导出日志中的旧名称是历史记录。

### 侧栏透明 PNG（2026-10-01）

日间使用 `folith-lockup-transparent.png`，夜间使用 `folith-lockup-transparent-dark.png`。两者从正式原图逐像素去白底，保留图标内白色文字与箭头；夜间版只调整文字颜色，尺寸与 alpha 轮廓相同。不再使用 SVG 派生图或主题放大。

重建命令：`python3 scripts/extract_brand_png.py`（维护环境需 Pillow、NumPy、SciPy）。原图保留，不覆盖。


## P9 白底应用图标（2026-10-15）

重生成：`venv/bin/python scripts/build_app_icon.py --out build/qt-desktop/Folith.icns --png-out folith/resources/brand/folith-app-icon-white.png`。

外形为指数 4 的超椭圆，是连续曲率底板的近似，不声称是 Apple 系统掩膜的精确复制。纯白底板下有轻微投影；只对轮廓超采样，品牌标按原比例缩小一次，不改色。传统 `.icns` 含 16–1024px 的完整帧，外角透明。浅色 / 深色均使用白底版；独立深色 / 着色资产仍留到有完整 Xcode / Icon Composer 后另做。系统是否额外套底座须在实际安装后人工确认，不能从资产图推断。


## 图标几何修正（2026-10-16）

原图标的两页是"斜切"（竖边 + 倾斜的上下边），两页大小不同、错位不对称，放在白色方形底板上显得偏右下：中心对称度 91%，外接框比画布中心低 1.5%。修正版保留原来的颜色、渐变、文/A 和指针，只改几何：

- 两页同样大小，保留原来的斜切角度，沿对角线等距错开，外轮廓中心对称（100%）。
- 两页前后穿插的分界线下移，浅色页占可见面积 57%（原图 55%），抵消深色更重的视觉分量。
- 面积与原图相同，视觉重心居中并上提 1.5%。

矢量源 `docs/logo-exploration/folith-mark-final.svg`，并随包为 `folith/resources/brand/folith-mark.svg`。探索过程中的旧几何复刻、对称变体与对照图已删除，只保留这一份；`docs/logo-exploration/README.md` 记录来源。
