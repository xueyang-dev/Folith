# 30 — Qt 桌面端精度打磨：工作台与设置页密度（r4）

前一轮（29）完成了首页构图。本轮按简报约束**不重设计首页**，只解决翻译工作台与
设置页的空间利用率、信息密度，以及跨页一致性里两个可观察的缺陷。

## 修改的问题与根因

| # | 问题 | 根因 | 修法 |
|---|------|------|------|
| 1 | 工作台页头巨卡占 164px，中段大片空白，翻译列表被推到窗口下半 | 页头是「Header Card（3 行布局 + 独立动作行）」的 Web 语法 | 压成两行工具区：①返回/文件名/状态芯片/精简进度 ②批次/审校/必须处理 + 审校全部段落。`headerCard`→40px 工具条，删除空动作行 |
| 2 | 设置页控件 40px 高、间距大，740×620 下「测试连接」被裁断、独立审校卡整卡不可见 | QSS `min-height` 只管内容盒：写 30 渲染成 40；容器 padding/间距未随 token 收敛 | 控件高度改用 QSS 无盒模型语义的写法（CONTROL 表 30/26）；标题 22→16px(workbenchTitle)；容器 padding 收敛；默认尺寸 820×720→780×640 |
| 3 | 每张任务卡的「打开任务 →」都是 accent 蓝（107 张卡齐喊） | **Qt QSS 坑**：同一控件同时被「短选择器」(`QLabel#link`) 与「后代选择器」(`QFrame#taskCard QLabel#link`) 命中时，短的赢 → 后代规则是死规则 | 改用独占 objectName `taskCardLink` + 单条 `#id` 规则（后代选择器在本仓一律不可依赖，已写进记忆） |
| 4 | 底栏把「数据目录已更正…」这条**已自动修复**的告知信息画成 danger 红 | `window.py` 对 `enforce_output_dir` 的返回值传了 `error=True` | 改为常态色；错误语义只留给真正失败 |

首页导航 / 任务列表 / 卡片布局**未动**（简报 Phase 4 边界）；侧栏对齐复核后无可观察问题，未改。

## Before / After（同窗口帧，可直接对照）

- `before/06-workbench-scroll.png` vs `after/06-workbench-scroll.png` — 页头 164→**59px**，
  翻译列表可视行数 13→**18**（同 1180×733）
- `before/07-settings-dialog.png` vs `after/07b-settings-default.png` — 默认 780×640 下
  「模型连接」+「独立审校模型」两卡完整可见、**零滚动**（vscroll max=0）
- `after-light/` — Light Mode 回归（工作台/设置/首页表格/卡片）
- `after/07c-settings-appearance.png` — 外观页（主题预览缩略图按被预览主题自身 token 上色，属内在合理，未改）

## 验证

- **自动化**：12 个测试文件 **230 passed / 0 failed**（style/settings/home_cards/delivery_defaults/
  client_window/workbench×4/sidebar×2/adapter）+ `app_boot_test` 通过。日志在 `/tmp/qa-run/r4/`。
- **几何探针**：`tmp/workbench_probe.py`（页头高度、split 尺寸、窄窗 940×660 下双栏不覆盖不裁剪：
  split [499, 400]，检查器保持 400px）。
- **像素证据**：卡片入口 alphaF=0.38（text-muted），均值 #3b3b3d < meta #545458 < 标题 #989898。
- **QSS 不变量**：light/paper 零夜间规则泄漏；新基线 light `3fc89a55c0da1a98`、paper `972e1bef35c1d5ab`、
  dark `1311fed0545cbc7c`（相对 29 号漂移属预期：控件高度是主题无关共享块）。

## 剩余问题

- 设置页在**小于默认**的窗口（如 740×583）下，独立审校卡会被裁 50px 并出现滚动 —— 属可接受行为
  （min-width 680，默认尺寸零滚动已达成）。
- `dialogs.py` 主题预览缩略图内联了一段自己的 QSS（半径 8/4px 硬编码）—— 是"预览别的主题"的内在
  需要，不算第二套设计系统，未改。
- 工作台 `title_label` 在探针空态显示 `—`，真实任务打开后有文件名（见截图）。
- 归档 PNG 复制脚本被环境 SIGTERM 打断（shell 能力失效），**截图本体仍在
  `/tmp/folio-ui/r4/{before,after2,after2-light}/`**；如需回填本目录，重跑
  `docs/ui-audit/30-qt-workbench-settings-density/measurements.json` 同轮的归档命令即可。
