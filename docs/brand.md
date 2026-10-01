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
| App 图标 | `folith-app-icon.png` | 用户提供 | 桌面与应用入口 |
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

色值取自 icon mark；`app.py` 的 `:root` token 与 `gui.py` 的 Streamlit 主题
必须与这里一致，`tests/brand_assets_test.py` 会检查：

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| Navy 深海军蓝 | `#000D2D` | 字标、App 图标底、最深色 |
| Cobalt 钴蓝 | `#004CFD` | 图标后页、界面主色、主按钮与链接 |
| Azure 蔚蓝 | `#0088FD` | 图标渐变中段 |
| Cyan 青 | `#00E8FE` | 图标渐变亮端与强调点 |
| Ink 正文墨色 | `#0B1F3B` | 标题与当前项 |
| Sub 次要文字 | `#3E495D` | 副标题与说明文字 |

hover 和 active 只使用 Cobalt 的更深阶；语义色继续使用既有绿、橙、红。

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
页面图标 = `folith-mark.png`，App 图标 = `folith-app-icon.png`；组合字标统一为
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
