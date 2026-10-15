# Folith·译页 品牌与视觉基准

本文以用户提供的品牌标准图 `docs/assets/folith-brand-standard.png` 为唯一视觉标准。
它定义组合字标、英文/中文副标题、icon、App icon、横向小版和深浅单色版。

在此基础上，正式素材单独提供三份（主横向组合、图标、App 图标），由界面与 README
直接使用，**不由渲染脚本生成**；脚本只补 kit 未覆盖的版位。

## 0. 正式品牌

| 语言 | 品牌名 | 定位 |
| --- | --- | --- |
| English | `Folith` | `Agentic Translation Workspace` |
| 中文 | `译页` | `智能体翻译工作台` |

组合字标统一写 `Folith·译页`；正文可根据语言写 `Folith` 或 `译页`。
不要把品牌扩展成 `Folith AI`、`译页AI` 或普通的 AI Translator。

## 1. Logo

logo 继续使用两页、文/A 字形和白色指针组成的 icon mark：

| 元素 | 含义 |
| --- | --- |
| 两页叠放的圆角页面（后页钴蓝、前页横向渐变） | 长文档及其结构 |
| 页面上的 **文** 和 **A** | 中文 ↔ 英文的本地化工作 |
| 覆盖两页的白色指针光标 | Agent 在文档上执行工作 |

本轮不重绘、不换色、不加阴影，也不重新设计图形。产品界面与 README 直接使用
用户提供的正式素材；向量派生只用于 kit 未覆盖的补充版位。原有 `foliothread-*`
资源作为历史/兼容素材保留，但不再由当前 UI 或 README 引用。

### 品牌标准图

![Folith·译页品牌标准图](assets/folith-brand-standard.png)

### 资产矩阵

| 版本 | 文件 | 来源 | 用在哪里 |
| --- | --- | --- | --- |
| 品牌标准图 | `docs/assets/folith-brand-standard.png` | 用户提供 | 本规范的完整参考 |
| 主横向组合 | `folith-lockup.png` | 用户提供 | README 首屏、透明版的原始素材 |
| 透明横向组合 | `folith-lockup-transparent.png` | 原图逐像素去白底 | 日间与纸面主题侧栏 |
| 夜间透明横向组合 | `folith-lockup-transparent-dark.png` | 同一原图，仅调整文字颜色 | 夜间主题侧栏 |
| 图标（透明底） | `folith-mark.png` | 用户提供 | 页面图标（浏览器标签页、安装入口） |
| 图标（透明底，高清母版） | `folith-mark-hires.png` | 用户提供（1254×1254） | 白底应用图标的原始母版（`scripts/build_app_icon.py` 保留原色，仅缩放居中） |
| App 图标（原始深底版） | `folith-app-icon.png` | 用户提供 | 保留的原始素材；Qt 当前应用入口改用下行白底版 |
| App 图标（P9 白底版） | `folith-app-icon-white.png` | 由高清母版合成 | Qt 应用 / 关于 / 欢迎与桌面 `.icns`；1024px 透明画布、824px 纯白连续曲线底板、品牌标宽约 70% |
| 深色底横向组合 | `folith-logo-dark.png` | SVG 派生 | 旧补充素材，侧栏不再使用 |
| 竖向组合 | `folith-logo-stacked.png` | SVG 派生 | 窄栏或正方形版位 |
| 彩色图标（向量） | `folith-mark.svg` | 向量源 | 派生渲染与单色底稿 |
| 单色图标（向量） | `folith-mark-mono.svg` | 向量源 | 单色、需跟随 `currentColor` 的场景 |
| 标签页图标（向量） | `folith-favicon.svg` | 向量源 | 需要矢量缩放时 |

标注「用户提供」的是正式品牌文件，禁止手工替换为不同比例或不同字标的图片，
也**不要**用渲染脚本覆盖它们；标注「SVG 派生」的位图在改完 SVG 源后重跑脚本即可。
旧的 `foliothread-source-lockup.png` 与 `foliothread-source-icon.png` 仍随包保留，
供历史数据或旧文档读取。

## 2. 色板

色值取自 icon mark；`folith/ui/theme_tokens.py` 的日间（`light`）token 与
`gui.py` 的 Streamlit 主题必须与这里一致，`tests/brand_assets_test.py` 会检查：

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| Navy 深海军蓝 | `#000D2D` | 字标、App 图标底、最深色 |
| Cobalt 钴蓝 | `#004CFD` | 图标后页、界面主色、主按钮与链接 |
| Azure 蔚蓝 | `#0088FD` | 图标渐变中段 |
| Cyan 青 | `#00E8FE` | 图标渐变亮端与强调点 |
| Ink 正文墨色 | `#0B1F3B` | 标题与当前项 |
| Sub 次要文字 | `#3E495D` | 副标题与说明文字 |

hover 和 active 只使用 Cobalt 的更深阶；语义色继续使用既有绿、橙、红。

### 2.1 三套主题与语义 token（2026-10-02）

界面有 **日间 / 夜间 / 纸面** 三套主题。三者是**平级的 token 集合**，不是
"日间样式 + 覆盖"：

```
组件选择器  ->  --tp-<role>（历史角色名）  ->  --<semantic token>  ->  主题取值
```

- 取值集中在 `folith/ui/theme_tokens.py` 的 `TOKENS`：三套主题的 key 完全相同，
  `ROLE_ALIASES` 把历史 `--tp-*` 名绑到语义 token 上，组件 CSS 无需一次性改写。
- `folith/ui/themes.py` 生成 `:root` 并追加在样式表之后，因此靠级联生效，
  不需要 `!important` 去修补某个主题。
- 历史样式表里写死的字面色（`#hex`）在夜间/纸面下由 `normalize_legacy()` 归一到
  角色引用；**`rgba()` 字面量不会被归一**，所以焦点光圈、玻璃面这类半透明色必须
  直接写成 token（见 `--focus-halo` / `--surface-glass` / `--overlay-veil`）。

**品牌蓝只属于 logo。** 日间主题的交互色就是 Cobalt；纸面主题的交互色是**墨蓝灰**
`#4C5A66`，夜间是浅蓝 `#82ACFF`。三者都不允许把 logo 的饱和钴蓝复制到按钮、
单选、导航当前项或状态点上。

#### 纸面（Paper）取值

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
`tests/ui_preferences_test.py` 会断言纸面归一化后的组件样式表里不再出现饱和品牌蓝。

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
一张卡片"（透明底 + 无边框 + 无圆角），以及按下态不再变蓝。日间与纸面的样式表
**逐字节不变**（`sha256(build_qss('light'))` 作为回归基线）。

## 3. 字体与副标题

- 主字体：Manrope `400/500/600/700/800`，字标使用 `800`；
- 中文回退：`PingFang SC` → `Hiragino Sans GB` → `Microsoft YaHei` → `Noto Sans SC`；
- 英文副标题：`Agentic Translation Workspace`；
- 中文副标题：`智能体翻译工作台`。

界面和品牌资产使用同一字体栈，中文副标题保留现有字距规则。

## 4. 资源生成与回归

补充变体由脚本生成：只修改 SVG 源，然后重跑并检查。

```bash
$EDITOR folith/resources/brand/folith-mark.svg
python scripts/render_brand_assets.py
python scripts/render_brand_assets.py --check
python -m pytest tests/brand_assets_test.py -q
```

脚本只写自己的产物（`folith-logo-dark.png`、`folith-logo-stacked.png`，外加可选的
单色预览），**不碰**用户提供的正式素材，也不覆盖旧的 `foliothread-source-*.png`。
正式素材被替换或删除时，`tests/brand_assets_test.py` 会立刻失败。

## 5. 工作台应用

侧栏、页面图标、启动器和导出署名使用正式品牌素材：侧栏品牌位使用 `folith-lockup.png` 的透明 PNG 派生版，
页面图标 = `folith-mark.png`；Qt App 图标 = `folith-app-icon-white.png`，由 `scripts/build_app_icon.py` 从高清标记合成；原始深底 App 素材仍保留。组合字标统一为
`Folith·译页`。README 首屏展示同一份主 lockup。页面仍保持既有侧栏、任务步骤、
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

日间与纸面使用 `folith-lockup-transparent.png`，夜间使用 `folith-lockup-transparent-dark.png`。两者从正式原图逐像素去白底，保留图标内白色文字与箭头；夜间版只调整文字颜色，尺寸与 alpha 轮廓相同。不再使用 SVG 派生图或主题放大。

重建命令：`python3 scripts/extract_brand_png.py`（维护环境需 Pillow、NumPy、SciPy）。原图保留，不覆盖。


## P9 白底应用图标（2026-10-15）

重生成：`venv/bin/python scripts/build_app_icon.py --out build/qt-desktop/Folith.icns --png-out folith/resources/brand/folith-app-icon-white.png`。

外形为指数 4 的超椭圆，是连续曲率底板的近似，不声称是 Apple 系统掩膜的精确复制。纯白底板下有轻微投影；只对轮廓超采样，品牌标按原比例缩小一次，不改色。传统 `.icns` 含 16–1024px 的完整帧，外角透明。浅色 / 深色均使用白底版；独立深色 / 着色资产仍留到有完整 Xcode / Icon Composer 后另做。系统是否额外套底座须在实际安装后人工确认，不能从资产图推断。
