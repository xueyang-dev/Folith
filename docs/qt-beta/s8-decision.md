# S8 — Qt 架构决策与迁移估算

- 日期：2026-10-03
- 范围：`docs/qt-beta-loop-engineering-prompt.md` §4 的 Spike 决策；证据见 [证据日志](../qt-spike/evidence-log.md)，状态见 [Beta 状态](status.md)。
- 候选身份：`app_version=0.5.0`、`build_id=git:1260a82`、`worktree_dirty=true`、macOS arm64、PyInstaller 6.22.3。
- 验收系统：macOS 27.0.1 / Apple Silicon（开发机）。声明最低系统：**macOS 15.0**（实测，见下）。

## 1. 决策

**GO — Qt Widgets / macOS arm64**（真实窗口与 frozen 主流程成立、核心/数据语义保持、强制输入与生命周期验证有证据）。

保留的强制人工项（不影响 GO，但不能写成"完整通过"）：

| 项 | 状态 |
| --- | --- |
| 真实中文输入法（候选词 / 组合输入 / 光标） | REQUIRED MANUAL |
| 系统文件面板（导入 / 保存的确认与取消） | REQUIRED MANUAL |
| 睡眠 / 唤醒 | REQUIRED MANUAL |
| 真实模型服务任务链 | REQUIRED MANUAL（需授权与凭据） |
| 干净 Mac 普通用户全流程 | REQUIRED MANUAL（候选已就绪） |

## 2. 复用范围（按实际调用链）

**直接复用（无 Streamlit 依赖）**

| 面 | 复用内容 |
| --- | --- |
| 业务核心 | `core.py` 全量：任务身份、状态格式、worker、运行时/事件、暂停/取消/重试、草稿、提交、审校、交付门禁、快照、TM/术语、OCR、诊断 |
| 数据与存储 | `folith/storage/{job_repository,state_journal}`、`folith/job_lock`、`folith/checkpoint`、`folith/snapshots` |
| 交付与资产 | `folith/delivery`、`folith/assets`、`folith/final_docx`、`folith/report_evidence`、`folith/translation_evidence` |
| 桌面基础设施 | `folith/desktop/{paths,exit_flow,file_actions,legacy_import,diagnostics,single_instance}`（`exit_flow` 只复用其无 WebView 的读取部分） |
| 品牌与本地化资源 | `folith/resources/**`（品牌图、locale 目录） |

**需要适配（Qt 侧薄层）**：`pipeline_kwargs`（从 Streamlit `session_state` 改为 `EngineConfig`）、任务列表读取模型（`list_job_summaries` + 快照状态）、进度呈现（`build_job_runtime_view`）、退出流程（WebView 点击提交 → 编辑器直接 flush 草稿）。

**不复用**：`app.py` 的 107 个渲染单元、`folith/ui/**`（12 个模块导入 Streamlit，合计约 591 处 `st.*`）、`folith/desktop/service.py`、`gui.py` 启动器、WebView 探针脚本。

**验证结论**：Qt 候选的启动与主流程**不需要安装或启动 Streamlit**；frozen 包内已确认不含 `streamlit` / `webview`（`build/qt-desktop/release-manifest.json` 的 `must_not_contain.found = []`）。

## 3. 关键行为证据（摘要）

| 能力 | 证据 | 结果 |
| --- | --- | --- |
| 导入 PDF / DOCX → 现有格式目录 | E3 / E4 | PASS |
| 小型任务翻译（真实 HTTP 传输的受控端点） | E3 / E4 / E5 | PASS |
| 多段工作台：编辑 → `human_edited` → 切走切回 / 重开一致 | E3 + 窗口级用例 | PASS |
| 审校 + 交付门禁 + 快照 | E3 / E4 / E5（含"部分重审不能通过门禁"） | PASS |
| 退出：空闲 / 运行中 / 两个活跃任务 | E7 + 窗口级用例 | PASS（锁全部释放） |
| 单实例 | E11（第二进程退出码 3） | PASS |
| 200 段 + 16,280 字符段 | E9（打开 31ms / 刷新 9ms / 编辑 17ms） | PASS |
| 旧数据目录接入 | E10（源目录未改动） | PASS |
| frozen 主流程 | E13（`--selftest`，`frozen=true`） | PASS |
| frozen OCR eng/chi_sim/ara（PATH 无 Homebrew） | E14 | PASS |
| DMG 挂载 → 复制安装 → 主流程 | E15 | PASS |
| 真实 IME / 系统面板 / 睡眠唤醒 / 真实服务 / 干净机器 | — | REQUIRED MANUAL |

## 4. 最低系统版本：修正 S1 的假设（MAC-02）

S1 记录认为"PySide6 6.11 要求 macOS 13.0+"。**实测不成立**：

- `pyside6-6.11.2-cp310-abi3-macosx_13_0_universal2.whl` 的 `macosx_13_0` 是 **wheel 平台标签**，不是包内 Mach-O 的最低系统；
- 实测 `QtCore/QtGui/QtWidgets` 的 `LC_BUILD_VERSION minos = 15.0`（`libqcocoa.dylib` 为 13.0）；
- 随包 Tesseract 亦为 `minos 15.0`（与 MAC-02 原判断一致）。

因此 Qt 候选的 `LSMinimumSystemVersion = 15.0`，由构建流程**实测**后写入并在打包后复核（`scripts/qt_native_min_os.py`，449 个原生文件上限 15.0）。**不声称支持 macOS 13 / 14。**

## 5. 迁移估算（剩余旧界面 → Qt）

实测基础：`app.py` 14,123 行、431 个顶层函数，其中 `_render*`/`_sidebar*`/`_workspace*` 形态 **134 个单元 / 7,556 行**（与路线图的"107 个渲染单元"同一量级）。已完成的 Qt 客户端共 **3,836 行**（adapter 1,083 / views 708 / window 843 / dialogs 373 / workers 110 / bootstrap+app+branding+selftest 709）。

已覆盖（Beta 必需流程）：任务库与导入、模型配置、任务运行与进度、多段工作台与编辑提交、审校、交付门禁与快照保存、退出与恢复、单实例、诊断、旧数据接入。

未迁移（保留旧实现，Qt 中不提供无效入口）：

| 组 | 单元数 | 行数 | 迁移工作量估计 |
| --- | --- | --- | --- |
| 高级实践报告与学术工作台（大纲、章节、质量复核、证据、文献） | ~30 | ~2,300 | 大：需要 3–5 个新页面 + 长任务状态机呈现，约 8–12 轮 |
| 案例库与案例终审 | ~12 | ~1,200 | 中：列表 + 详情 + 决策流，约 4–6 轮 |
| 语言资产管理（术语/TM/风格库全功能） | ~20 | ~1,600 | 中：表格编辑 + 冲突处理，约 4–6 轮 |
| 项目中心 / 项目设置 / 归档与备份 | ~23 | ~1,227 | 中：Qt 已有项目选择与新建，缺设置/归档/备份，约 3–5 轮 |
| 交付复核队列与 QA 面板 | ~9 | ~499 | 小到中：约 2–3 轮 |
| 历史页高级筛选与批量操作 | ~2 | ~205 | 小：约 1–2 轮 |
| 侧栏工具区、主题与本地化切换 | ~3 | ~287 | 小：约 1–2 轮 |
| 其余（向导、预览、原页对照等） | ~35 | ~2,049 | 中到大：原页对照与 PDF 预览需要新控件，约 6–10 轮 |

**总量估计：约 29–47 轮**（每轮一个可验收单元 + 证据），对应"完整 107 单元等价界面"。本估算只用于决策，不作为本轮承诺；Private Beta 的必需流程不依赖这些剩余单元。

## 6. 阻断与风险

- 无技术阻断。
- 风险 1：**未签名分发**。DMG 为未签名未公证候选，仅限受邀测试；首次打开需右键放行。公开分发需 Developer ID / hardened runtime / 公证 / staple 与真实下载验收（外部条件）。
- 风险 2：**最低系统 15.0**。若产品必须支持 macOS 13/14，需要换用更低 minos 的 Qt 与 Tesseract 构建（重建原生闭包），而不是改文案。
- 风险 3：**bundle 体积 375 MB（DMG 159 MB）**。Qt 运行时占主要部分；如需缩减需按模块裁剪（Qt Qml/Quick/VirtualKeyboard 等当前由 PyInstaller 钩子带入）。
- 风险 4：真实 IME 与系统面板尚未人工验收；若 IME 出现控件级缺陷，先定位到控件使用/焦点/模型刷新，不堆补丁、不改路线。
- 风险 5：远端 CI 未运行（无远端授权）。工作流已按同一解释器环境接线，本机命令可复现；不宣称远端 CI 已通过。

## 7. 下一步

1. 按 §6 收口 Beta 剩余工程项（诊断/发行记录已就绪，见 `release-notes.md`）。
2. 交付人工验收包：DMG + 逐步清单 + 待回传记录（一次请求）。
3. 人工项与干净机器验收通过后，再按外部条件推进签名/公证与公开发布。
