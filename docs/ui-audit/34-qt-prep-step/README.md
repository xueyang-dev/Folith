# 34 — Qt 客户端「译前准备」面板

## 问题（用户实测反馈）

按原设计，导入文档后的下一屏应当是**译前准备**（选翻译风格与产出物），但 Qt 客户端从导入
直接跳到工作台 —— 这一步被漏掉了。用户截图对比后确认：图一（导入）→ 图二（工作台），中间
的准备步骤不存在。

用户给定的对齐口径（经追问确认）：

| 维度 | 决定 |
|---|---|
| 形态 | **工作台内的「准备」面板**（不是独立向导窗口） |
| 内容 | **和之前的 Streamlit 版保持一致** |
| 范围 | **一次性全量对齐** |
| 导入行为 | **导入后直接进准备面板**（取消「现在开始翻译吗？」确认框） |

## 实现

```
folith/application/task_setup.py   (新) 前后端中立的真源：策略预设 / 批次档 / 交付方案 /
                                        产物清单 / 理论框架依赖，全部从 app.py 私有常量下沉
folith/application/commands.py          新增 apply_task_setup（唯一写回入口）
folith/qt/adapter.py                    task_setup_view / apply_task_setup / setup_choices
folith/qt/setup_panel.py           (新) TaskSetupPanel —— 面板本体，零新增 QSS
folith/qt/views.py                      工作台挂载面板（推进 translation_layout）+ 三态显隐
folith/qt/window.py                     载入选配 → 载入面板；连接「开始翻译」
folith/qt/style.py                      SegmentedControl.set_options()（控件能力，不改 QSS）
tests/qt_task_setup_test.py        (新) 15 个用例
```

### 关键约束：面板必须把选择**写回任务 state**

`resolve_effective_settings`（`folith/application/settings.py:372`）让**任务已保存的字段**
压过 `explicit` 层 —— 注释原文是「A global preference change therefore cannot silently
rewrite an existing task」。所以：

- 面板只改自己的临时值 → 任务会**忽略**它（用户选了产物却拿到导入时的默认值）；
- 唯一正确路径是共享命令 `apply_task_setup`，它对着**空 state** 解析出完整配置再整包写入。

另一处坑：`core.normalize_delivery_config(config, *, enable_report, enable_annotate)`
会用**顶层**开关覆盖 bundle 里的同名字段 → `apply_task_setup` 必须把
`enable_report` / `enable_annotate` 提到顶层，否则勾了「翻译实践报告」也不会落库
（`enable_annotate` 还要在未勾双语对照时强制为 False）。

## 证据

| 文件 | 说明 |
|---|---|
| `01-prep-panel-light.png` | 浅色主题。`译前准备` 标题 + 输入与上下文 / 翻译策略 两卡；预设停在 `标准`；`批次策略` 默认 `保守`。**这张也是 `pageCanvas` 修复后的对照**（修复前滚动视口在此主题下画成黑底） |
| `02-prep-panel-dark.png` | 夜间主题（中性 graphite） |
| `03-prep-panel-paper.png` | 纸面主题 |
| `04-prep-research-light.png` | 研究产物分支：勾「翻译实践报告」后 `研究产物` 区块展开、`理论框架` 下拉出现；`交付方案` 自动从 `标准交付` 跳到 `自定义` |
| `05-delivery-tab-light.png` | 工作台「交付」标签页对照：未开始任务优雅降级（状态「待处理」、三个操作按钮禁用、`尚未生成冻结交付` 空状态） |

取证方式：**真实 cocoa 平台**（非 offscreen）构建 `MainWindow`，走
`window.open_external_files()`（与 Finder 双击同一条路径）导入夹具，再 `grab()`。
断言输出：

```
task_started: False | setup visible: True | grid visible: False | header start hidden: True
research visible: True | theory visible: True
```

即：未开始 → 只显示准备面板，段落网格与头部运行控件都不出现。

## 本轮修掉的两个缺陷

1. **浅色主题下滚动视口画成黑底**（标题黑字压黑底）。根因：`QScrollArea` 的 viewport
   按约定是透明的，而面板 widget 自己没有背景规则 → 缝隙露出黑底。
   修法：`self.setObjectName("pageCanvas")` 复用既有
   `QWidget#pageCanvas { background: bg-page }` 规则，**不动 QSS**。
2. **异步导入回调重复载入任务**，把编辑器内容清空 ——
   `tests/qt_client_window_test.py::test_import_then_editor_keeps_text_across_background_refresh`
   由红转绿。根因：`import_paths` 的 `done` 回调是**异步**的，无条件调 `open_job` 会在
   测试已铺好 state 之后再开一次任务，命中 `_set_editor_text("", "")`。
   修法：`if self._current_job_id != results[0].job_id:` 守卫。

## 验证结果

| 项目 | 结果 |
|---|---|
| `tests/qt_task_setup_test.py` | 15 passed |
| `tests/qt_client_window_test.py` | **71 passed**（分块跑；见下） |
| `tests/qt_home_cards_test.py` / `qt_client_style_test.py` | pass |
| `check_ui_locales.py` | 0 errors |
| QSS 指纹 | light `c3b8fa9900246d85` / paper `c18f15e21272c328` / dark `4c31f48927321f38` —— 与基线一致，**零漂移** |
| 「夜间色不得泄进 light/paper」 | 命中数 0 |
| pyflakes | 相对 HEAD **增量为 0**（新文件那条未用 import 已清） |

## 未决 / 待办

- **`tests/qt_client_adapter_test.py` 有 1 条抖动失败**：
  `test_bad_translation_protocol_is_rejected_without_partial_writes`
  挂在最后一条断言 `needle in detail`（拿到的是通用文案
  「当前进度已安全保存，可以继续处理。」而非拒绝原因码）。
  证据表明是**抖动而非回归**：整文件跑时失败的是 `[""]-empty_item_or_unsupported_item`，
  单独跑时失败的换成 `[]-wrong_item_count` —— **失败参数在两次运行间互换**；且前两条断言
  （`p2_done is False`、无半截译文）都通过，核心不变量成立。
  上一轮全量回归该文件是 `57 passed`。**尚未定案**，需要在 shell 恢复后单独二分
  （怀疑 `status` 与失败原因非原子写入，读侧可能看到「failed 但 detail 还是默认值」）。
- **Qt 端多语言目录未补齐**：`qt_i18n_coverage.py` 报 `397/435`，缺 38 条，其中
  **35 条来自本轮新增的 `setup_panel.py`**（其余 3 条既存）。
  该脚本**未接进任何发布闸门**，缺条目按设计回退中文（`i18n.py` 明说「缺条目不是错误」）。
  补齐属于独立的一趟翻译（`scripts/qt_i18n_instrument.py --catalogs` +
  `scripts/translate_ui_catalogs.py`），本轮按「临近发布期收缩范围」未自行造译文。
