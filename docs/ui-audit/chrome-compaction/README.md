# Workspace chrome compaction

验证任务：OECD The Space Economy at a Glance 2026（239 单元），右侧 Inspector 保持打开。
测试 viewport：1280×800（MacBook）、1440×800、1600×800，截图像素比 2。

| DOM 测量（CSS px，三个宽度一致） | 修改前 | 修改后 |
| --- | ---: | ---: |
| Header 区域，含翻译/术语/交付导航 | 98 | 62 |
| Header 起点至 segment table 表头（含所有顶部工具栏） | 260 | 123 |
| 首段距 viewport 顶部 | 305 | 160 |
| 段落滚动区高度 | 390 | 573 |
| 完整可见段落 | 5 | 6 |
| 含部分可见段落 | 6 | 7 |
| 新 pager 高度 | — | 34 |

返回入口改为「← 项目」，与标题同行；有所属项目时返回 Project Detail，否则进入 Project Center。
标题为顶部视觉主信息。第二行使用统一中性色显示「239 / 239 已译 · 0 / 15 术语确认 · 保存于 15:07」。
非阻断建议使用「⚑ 4 条建议」文字操作，继续打开原有问题面板。真正 blocking issue 保留告警样式。
发现筛选与段落导航合并为一行，保留过滤、上下段与右栏切换。
分页为「1–40 / 239    ‹ 上一组  下一组 ›」，沿用原有 40 单元窗口计算与导航回调。

采用已有 bounded editor 的固定底栏，而非叠加在正文上的 floating sticky：底栏始终容易访问，独立滚动区止于底栏之前，保留 2px 间隔；只在存在多于 40 个可见单元时出现。

修改代码：`app.py`、`folith/ui/styles.py`、`folith/ui/workspace/cat_grid.py`。
未修改 segmentation、翻译保存逻辑、分页后端或右侧审校实现。工作树原有其他改动均保留。

## 验证

- `python3 -m pytest tests/translation_workbench_ui_test.py tests/segment_navigation_test.py tests/project_task_navigation_test.py -q`：100 passed，5 条既有 SWIG deprecation warnings。
- 空结果筛选分支修正后重跑 `tests/translation_workbench_ui_test.py`：2 passed。
- 三种宽度 DOM 测量：标题和 metadata 均不溢出，metadata 单行；pager 34px，未覆盖正文。
- 截图与几何证据：`before-*.png`、`after-*.png`、`before.json`、`after.json`。
- 原截图脚本含依赖旧检查器文字的过时断言；本轮独立采集跳过该断言，并对实际 pager / title / metadata 的 DOM 几何进行测量。没有更改公共截图脚本。

验证范围为本地 Chromium、上述三个桌面 viewport 和真实任务；未覆盖移动端或所有任务生命周期状态。

实际点击验证通过：下一组、上一组往返；滚到当前窗口第 40 单元时末段完整可见且不被 pager 覆盖；「⚑ 4 条建议」打开原有右侧问题面板；「← 项目」返回所属项目详情。截图见 `interaction-bottom.png` 和 `interaction-suggestions.png`。

保留现有窗口算法：从 1–40 点击下一组，选中第 41 单元后窗口显示 21–60（围绕选中段居中），返回恢复 1–40；本轮未改成不重叠分页。
