# #150 导入校验向导（UI 层，0.8.2）

范围：issue #150 的 **向导 UI 那部分**——选文件 → 语言对预检与预期条数 → 导入
（工作线程 + 进度）→ 展示可导出的报告；一个入口覆盖 TMX / TBX / XLSX / CSV；
失败可重试；导入沿用引擎层原子性。引擎层（分类计数、元数据丢失清单、语言对
判定、报告落盘）由 #332 交付，本 PR 只消费它，不重写任何导入规则。

## 1. 入口

文件菜单新增一项 **「导入资产并校验…」**（`folith/qt/window.py`），位于
「接入旧数据目录…」之后。它与既有的两条导入路径互不影响：

| 入口 | 作用 |
| --- | --- |
| 文件 → 导入文档（已有） | 导入文档并建任务 |
| 译前准备 → 添加术语库（已有，`window.add_termbase`） | 把术语/TMX 挂到**当前任务** |
| **文件 → 导入资产并校验…（本 PR）** | 把资产导入**项目记忆/项目术语库**并给出校验报告 |

关闭向导后，若产生了报告，只调用 `refresh_projects()`（后台读取项目列表），
**不碰**工作台的增量刷新路径（A1 P2 正在改那里）。

## 2. 四步流程与状态机

`ImportWizardController`（纯 Python，Qt-free，可单测）：

```
file ──choose_file──▶ preflight ──begin_import──▶ importing ──finish_import──▶ report
                         ▲   │                          │
                         │   └──run_preflight 抛错──┐    │ 导入抛错
                         │                          ▼    ▼
                         └────────retry───────── error ◀─┘
```

| 步骤 | 界面 | 说明 |
| --- | --- | --- |
| 1 选文件 | `QFileDialog`（原生，两个平台同一实现），过滤 `*.tmx *.tbx *.xlsx *.csv` | 未选文件时「下一步」禁用 |
| 2 预检 | 文件类型 / 预计条目数 / 语言对状态 / 警告 / 目标项目 / 目标语言 | TMX 走只读预览（数 `tu`、读 header 与 tuv 语言，**不写库**）；TBX/XLSX/CSV 复用引擎 `parse_termbase*(report=True)`；术语库没有项目时**阻断**并提示 |
| 3 导入 | 不确定进度条 + 「正在导入…」 | `workers.TaskRunner` 单线程池，主线程不阻塞；导入期间关闭/上一步/下一步禁用 |
| 4 报告 | 分类计数、写入结果、语言对、元数据丢失清单、警告、恢复说明、报告文件路径 | 「导出报告…」把 JSON+MD 另存到用户选择的目录 |

**失败语义**：错误页展示原因（`AdapterError` 包装的 `ImportReportError.message`
或引擎的 `zero_import_detail`），「重试」**回到预检步**（重新预检后用户再点
导入），「重新选择文件」回到第 1 步。失败**不产生半成品**：

- TMX：引擎在写库前抛错，TM 不变；异常携带 `.report`（分类计数可见）；
- 术语库：逐条 `core.set_project_term` 写入，「已写入 N 条 + 恢复说明」如实展示；
  语言对/冲突/不合格的条目**不写入**，冲突不覆盖已审校条目。

## 3. 报告字段（引擎层 schema，向导只读）

`folith.engine.migration_report` 的 `folith-import-report-v1`：`counts`（固定 9 类
计数）、`metadata_loss.categories`、`language_pair`、`warnings`、`recovery`、
`persistence`。向导额外展示 `store`（写入结果：written/skipped）——它来自适配层，
不写进报告 schema（报告仍是引擎的那一份，可归档为 Gate 1 证据）。

导出复用 `migration_report.write_report(report, directory=...)`：用户选目录后得到
一对时间戳命名的 JSON+MD，内容与项目目录里的那份一致。

## 4. 适配层（`folith/qt/adapter.py` 新增函数，未改既有函数）

| 函数 | 作用 |
| --- | --- |
| `import_wizard_project_choices()` | 项目列表 + 语言方向（预填目标语言） |
| `import_wizard_preflight(path, project_id=, target_lang=)` | 格式识别、语言对、预计条数（只读） |
| `import_wizard_run(path, project_id=, target_lang=, preflight=)` | 调引擎执行导入，返回 `{"report","store"}` |
| `import_wizard_export_report(report, directory)` | 导出报告 |

TMX 预检的只读计数是本层唯一"额外解析"：它不写库、不给分类计数，导入后的
一切数字仍以引擎报告为准（文档与代码注释都写明这一点）。

## 5. i18n

新增 49 个用户可见文案键 + 1 个菜单动作键，7 个目录全部补齐（`qt-zh-CN.json`
为源串，其余 6 个按插入顺序追加、`indent=2`、不排序重写）：

- 状态词：一致 / 不符 / 部分不符 / 无法判定 / 文件未声明 / 未检查 /
  文件语言无法解析 / 目标语言无法解析 / 全部导入 / 部分导入 / 未导入任何条目 /
  语言对不符，已阻止 / 文件里没有可导入的单元 / 导入状态 / 失败 / 未完成
- 步骤与动作：导入与校验 / 第 1–4 步标题 / 上一步 / 下一步 / 开始导入 / 重试 /
  重新选择文件 / 关闭 / 选择文件… / 导出报告… / 导入资产并校验…
- 字段与提示：文件类型 / 预计条目数 / 语言对 / 警告 / 目标项目 / 目标语言 /
  分类计数 / 写入结果 / 元数据丢失 / 恢复说明 / 报告文件 / 报告已导出到 /
  导入失败：{v0} / 未知原因 / 未指定 / 无 / 未分类 / 例如：简体中文 /
  导入后给出 / 已写入 {v0} 条，跳过 {v1} 条。/ 术语库需要导入到具体项目… /
  选择要导入的文件（TMX / TBX / XLSX / CSV）。/ 术语库与翻译记忆过滤串 /
  选择要导入的文件 / 选择导出目录

`venv/bin/python scripts/check_ui_locales.py` → **0 missing / 0 unexpected / 0 errors**
（7 个目录）。

平台差异：文件对话框保持 Qt 原生（`QFileDialog`），不做 OS 分叉；没有新增
快捷键，因此不需要 `platform_ui` 的绑定与提示同源约束（菜单项文案走 i18n）。

## 6. 测试

`tests/qt_import_wizard_test.py`（offscreen，10 passed）：

- 状态机：四步顺序、文件不存在拒绝、预检失败→错误页→重试回预检、导入失败保留
  报告且可重试、无报告时拒绝导出；
- 控件：步骤切换、按钮可见/禁用（未选文件、术语库缺项目）、报告页含分类计数 /
  元数据丢失 / 警告、错误页含原因；
- 端到端（真实引擎）：1,000 条合成 TMX → 预检 1000 条 + 语言对 match → 导入
  → 报告计数与直接调用 `core.import_tmx(report=True)` **逐项一致**，导出 JSON+MD
  文件存在；
- 语言对不符：文件声明 zh-CN、目标英语 → 引擎拒绝且异常带报告，向导错误页
  展示 `skipped_language_mismatch=1`。

## 7. 未验证 / 已知限制

- **真机 UI 未跑**：本机只在 `QT_QPA_PLATFORM=offscreen` 下跑组件测试；macOS 原生
  文件对话框 / 标题栏行为没有在真实窗口里验收（沿用既有原生对话框约定）。
- 导入进度是**不确定态**：引擎的导入路径不提供逐条进度，界面给"正在导入…"的
  可见反馈而不是伪造百分比。要按条推进度需要引擎层回调（本 PR 不改引擎）。
- 报告里引擎的计数标签（`COUNT_LABELS`）是中英双语、不经过 Qt 目录翻译；要让
  它们跟随界面语言需要引擎层提供 key 而不是文案（边界外，只报不改）。
- 术语库导入是逐条 `core.set_project_term`，1,000 条约 1–2 秒（在 worker 线程，
  不阻塞界面）；TMX 走引擎的整文件写入，不受此影响。
- 端到端测试用合成 TMX；真实 20 年 TMX 的规模/编码覆盖仍属引擎层验收。
