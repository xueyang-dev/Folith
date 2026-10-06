# Qt 交付页：资产清单与空状态（UI/UX 第 2 轮）

日期：2026-10-05 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮只做 Qt 桌面端的**交付页**，对应上一轮留下的优先级 ①：交付页的资产清单
（文件图标 + 大小 + 分组）与空状态。没有改核心业务判断、没有改交付门禁、
没有新增导航入口。

## 参照与差距

参照物是仓库里的旧 Web 界面：

- 交付页设计参照 `docs/assets/delivery-preview.png`（交付判断 + 版本对比）；
- "空状态"表达参照首页 `docs/assets/01-projects-preview.png` / Qt 首页"还没有任务"。

整改前的实际界面（`before-delivery-light.png`）：

| 现象 | 为什么算问题 |
| -- | -- |
| 资产清单只有文件名，一行一个 | 看不出文件类型、大小、用途；6 个文件靠一块 180px 高的盒子硬撑 |
| "用默认应用打开 / 在 Finder 中显示" 与选中行没有绑定关系 | 没选中任何文件时按钮仍可点，点的是哪一条只能靠猜 |
| "已排除段落（0）" 下方是一整块空白列表 | 看起来像加载失败；空的状态没有句子可读 |
| 交付页不是滚动区 | 清单长一点时最后一行被切一半 |

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/icons.py` | 新增 `file` / `file-sheet` / `file-code` / `image` 四个文件类型图标（同一 24×24 描边网格，颜色仍取主题 token） |
| `folith/qt/style.py` | 新增 `EmptyState`（空状态：图标 + 标题 + 说明）与 `AssetRowDelegate`（资产行：类型图标 + 文件名 + 右对齐大小/缺失标记；分组标题行 30px、文件行 40px，选中行左侧强调条，发丝分隔线） |
| `folith/qt/views.py` | 交付页重做：按用途分组（译文与对照 / 术语表 / 证据与清理 / 交付记录 / 报告 / 图片 / 其它）、小节标题右侧汇总（`v1 · 6 个文件 · 80.8 KB · 冻结于 …`）、空状态、缺文件提示条、双击/Finder 入口与选中绑定；交付页套滚动区 |
| `folith/qt/adapter.py` | 新增 `snapshot_asset_path()`：优先用 `core.delivery_snapshot_asset_path`，旧 core 上退回 `folith.snapshots` 的同一份快照布局（否则会把"函数不存在"误报成"所有交付文件缺失"） |
| `tests/qt_delivery_ui_test.py` | 新增 13 条用例：分组顺序、组头计数与大小、逐个人类可读大小、未知后缀回退、缺失文件必须报出来、空清单/空排除段落的空状态、选中行跳过分组标题 |
| `tests/qt_client_window_test.py` | 两处随行为更新：资产清单现在按分组重排 → 比集合而非顺序；只把"文件行"当资产，并断言分组标题不可选中 |

分组与图标规则是数据映射（`views.DELIVERY_ASSET_KINDS` / `DELIVERY_SUFFIX_ICONS`），
不写死在渲染函数里，未知文件按后缀取图标并落到"其它文件"，不会消失。

## 状态覆盖（三套主题）

| 情形 | 证据 | 说明 |
| -- | -- | -- |
| 已冻结交付 v1，6 个文件 | [after-frozen-light.png](after-frozen-light.png) · [after-frozen-dark.png](after-frozen-dark.png) · [after-frozen-paper.png](after-frozen-paper.png) | 4 个分组、每行大小、组头计数与合计 |
| 选中一行 | [after-frozen-light-selected.png](after-frozen-light-selected.png) | 左侧强调条 + 浅色底；"打开/在 Finder 中显示"此时才可用 |
| 尚未冻结交付 | [after-empty-light.png](after-empty-light.png) · [after-empty-paper.png](after-empty-paper.png) | 空状态说明"冻结后这里会有什么"；两个按钮置灰 |
| 快照文件在磁盘上缺失 | [after-missing-light.png](after-missing-light.png) · [after-missing-dark.png](after-missing-dark.png) | 顶部提示条点名缺失文件，行内显示"文件缺失"（红色），不按正常大小伪装 |
| 没有被排除的段落 | 同上各图下半部分 | 用空状态替换整块空白列表 |
| 整改前对照 | [before-delivery-light.png](before-delivery-light.png) · [before-delivery-dark.png](before-delivery-dark.png) | 上游基线，仅供对比 |

截图由 `tmp/qt-beta/shots-delivery.py` 生成（真实窗口控件渲染，
`QT_QPA_PLATFORM=offscreen`，1400×900）：

```text
venv/bin/python tmp/qt-beta/shots-delivery.py frozen    # 冻结 + 空状态
venv/bin/python tmp/qt-beta/shots-delivery.py missing   # 缺文件（在 tmp 副本上删文件，不动源数据）
```

## 验证

本 worktree 的基线是 `1260a82`，而 Qt 客户端是主仓库里的未跟踪代码，依赖主仓库
尚未提交的 `core` 增量（`delivery_snapshot_asset_path`、`ocr_language_options` 等）。
因此本目录里跑测试会出现两类**与本次改动无关**的失败：旧 `core` 缺函数、
以及依赖真实模型管线的端到端用例。

为了不把"环境不匹配"当成"改坏了"，用两棵目录分别验证：

1. 本 worktree（UI 分支自身的用例）：

```text
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/qt_delivery_ui_test.py -q
13 passed
```

2. 用**当前 core** 组装一棵临时验证树（`/tmp/qt-verify`：主仓库 `core.py` + `folith/`
   + `tests/`，再覆盖本分支的 `folith/qt/*` 与两个测试文件），跑交付相关用例：

```text
QT_QPA_PLATFORM=offscreen python -m pytest tests/qt_client_window_test.py -q \
    -k "deliver or asset or excluded"
7 passed
```

其中包括交付格式声明、冻结后打开/Finder 定位、切换任务不串资产、排除/恢复、
"审校未就绪时按钮禁用并解释"这些既有回归用例——它们此前在本 worktree 里因旧
`core` 失败，在当前 `core` 下全绿，说明本轮改动没有破坏既有断言。

3. 同一棵验证树上的完整 Qt 套件（9 个 `tests/qt_*` 文件）：

```text
163 passed / 4 skipped / 9 failed（286s）
```

这 9 条失败与本次改动无关，证据是**同一棵树、只把 `folith/qt/` 换回主仓库版本**
（`/tmp/qt-verify-base`）后失败集合逐条相同：

```text
9 failed / 14 passed / 2 skipped（3.99s）—— 失败项与上表完全一致
```

失败原因是验证树缺 `docs/`、`packaging/`、`dist/` 与真实 OCR 包/真实任务目录
（6 条验收套件文档断言、1 条品牌资源路径、1 条 OCR 缺包、1 条真实任务目录），
不是界面行为。

## 分支快照说明（合并前先看）

本 worktree 从 `1260a82` 切出，而 Qt 桌面端在主仓库里是**未跟踪文件**，worktree
里原本一份都没有。本轮开工前按"桌面端"范围从主仓库工作区复制了一份快照：

- 客户端本体：`qt_client.py`、`folith/qt/`、`folith/desktop/`、`folith/ui/theme_tokens.py`、
  `folith/resources/brand/folith-mark-hires.png`、`qt_spike/`；
- 测试与脚本：`tests/qt_*.py`、`scripts/qt_*.py`、`scripts/build_qt_*.sh`、
  `scripts/desktop_*.py|js`、`scripts/d0_*.py`、`packaging/`、`requirements-desktop-build.txt`；
- 文档：`docs/qt-beta/`、`docs/qt-spike/`、`docs/desktop-*.md`、`docs/desktop-d0..d3/`。

**这些文件不是本轮写的**，是同一天从主仓库复制过来的；本轮实际改的是
`folith/qt/{views,style,icons,adapter}.py`、两个测试文件、`CHANGELOG.md`
和本目录。合并/对比时请以这 6 个路径为准，其余按"新增文件"处理。

## 未验证 / 边界

- 未跑真实模型调用与完整交付流程（隔离 fixture + 冻结快照，不触发付费模型）；
- 未在 Windows / Linux 上核对（本轮只在 macOS 上渲染）；
- 键盘顺序与 VoiceOver 朗读顺序未单独验收；分组标题经
  `ItemIsSelectable=False` 明确不可选中，方向键不会停在标题行，但没有屏幕阅读器证据；
- "报告 / 图片"两个分组只有映射规则与空状态，本机 fixture 里没有这类资产可看。
