# Folith 桌面客户端：成果交接（Streamlit + pywebview → PySide6 / Qt Widgets）

> **2026-10-17：这是 2026-10-03 的交接文档，Qt Spike 之后的现状见[Qt Beta 状态](qt-beta/status.md)。** 下文 §6 的 Qt Spike 计划已完成并得到 `GO`（[S8](qt-beta/s8-decision.md)）；Streamlit + pywebview 桌面壳已退役；§7 里的构建与旧界面命令是当时的写法，以[发布计划](release-plan.md)和 `scripts/qt_beta_chain.sh` 为准。

日期：2026-10-03 · 仓库：`<workspace>`
背景：产品方向调整为 **Python + PySide6（Qt Widgets）**，Windows / macOS 共用界面与业务核心、分别打包。
本文件收尾既有 Streamlit + pywebview 桌面工程（D0–D3），保存成果、证据、已知问题与未验证范围，
并给出 Qt Spike 的复用范围与计划。**旧界面保留为对照**（`python gui.py --legacy`）。

---

## 1. 当前成果（产物清单）

### 1.1 可分发产物

| 产物 | 事实 |
| --- | --- |
| `dist/desktop/Folith.app` | 332 MB，PyInstaller **onedir + BUNDLE**；`built_at 2026-10-03T15:30:53+00:00`，`build_id git:1260a82`，`commit 1260a826819cce3df23e165af36f8563f84d3625` |
| `dist/desktop/Folith-0.5.0-macos-arm64.dmg` | **152,085,559 字节**（UDZO），sha256 `ba4c302bb4810e53…`；**未签名、未公证**（ad-hoc），`hdiutil verify` 退出码 0 |
| `build/desktop/release-manifest.json` | 发布校验清单，`ok: true`，`blocking_failures: []`（含资源完整性、OCR 溯源、无开发路径、无凭据样文本、镜像==已构建应用） |
| `build/desktop/dmg_record.json`、`build/desktop/desktop_build_info.json` | 镜像与构建记录（版本/commit/依赖版本） |
| `docs/desktop-d3/release-notes-0.5.0.md` | 由清单自动生成的发布说明（含"实际验收环境""干净机器验收未完成"的如实披露） |

### 1.2 代码资产（本仓库）

| 位置 | 规模 / 内容 |
| --- | --- |
| `core.py` | **14,699 行**：业务核心（解析、分段、翻译编排、审校、交付快照、TM/术语、运行状态、锁、迁移） |
| `app.py` | **13,886 行**：Streamlit 界面（107 个 `_render_*`，7 个 workspace section：translation / terms / review / qa / cases / report / delivery） |
| `gui.py` | 545 行：入口与分派（桌面启动 / `--serve` / `--legacy` / CLI：诊断导出、旧数据接入、自动化脚本） |
| `folith/desktop/` | **11 个模块**：`paths` `launcher` `service` `protocol` `process` `single_instance` `exit_flow` `legacy_import` `file_actions` `diagnostics` |
| `folith/ui/`、`folith/ui/workspace/` | 各 10 个模块：界面视图模型（与 Streamlit 解耦的部分，Qt 可复用） |
| `folith/translation_core/`、`folith/storage/` | 6 + 3 个模块：翻译核心与状态日志/迁移 |
| `packaging/folith-desktop.spec` | PyInstaller 规格（含 OCR 载荷、图标、资源、构建信息） |
| `scripts/` | **97 个文件**（4 个 shell）：构建、发布校验、发布说明、OCR 载荷、图标、端到端 sweep 与各验证器 |
| `tests/` | **19 个 `desktop_*`/GUI 测试文件** + 既有业务回归（本轮桌面门禁 **167 passed**） |

### 1.3 证据文档

| 里程碑 | 文件 | 条目数 |
| --- | --- | --- |
| D0 架构 Go/No-Go | `docs/desktop-d0/{d0-decision.md,evidence-log.md,environment.md,README.md}` | 15 条证据（D0-2a…D0-9） |
| D1 生命周期与数据安全 | `docs/desktop-d1/{evidence-log.md,closeout.md}` | **24 条**（D1-1…D1-24） |
| D2 可安装 Beta | `docs/desktop-d2/{evidence-log.md,closeout.md,clean-machine-acceptance.md}` | **16 条**（D2-1…D2-16） |
| D3 公开发布（本地部分） | `docs/desktop-d3/{evidence-log.md,release-notes-0.5.0.md}` | 4 条（D3-1…D3-4） |

---

## 2. 验证证据汇总

### 2.1 端到端 sweep（`scripts/desktop_e2e_sweep.py`，**26 个用例**）

命令：`venv/bin/python scripts/desktop_e2e_sweep.py --out-dir build/desktop/e2e-sweep`

| 组 | 用例 |
| --- | --- |
| 退出矩阵 | exit-matrix / edit-exit（未失焦编辑）/ quit-exit（⌘Q）/ running-exit / paused-exit / **exit-timeout** / crash-recovery |
| 并发形状 | **two-tasks-exit / three-tasks-exit / paused-plus-running-exit / cancelling-at-exit** |
| 编辑与导入 | multi-segment-edit / legacy-import / **dry-run-noop** / **unsafe-import** |
| 删除语义 | **history-delete / history-delete-running / history-delete-paused / project-delete** |
| 规模与能力 | **many-jobs（50 任务）** / ocr-baseline / file-actions |
| 平台与升级 | ui-language / first-launch / dmg-launch / upgrade |

最近一次全量：**24/25 通过**，唯一失败 `file-actions` 归因为**环境性浏览器抖动**，加固后复验通过（等效 **25/25**）；
新增 `project-delete` 后为 26 用例（该用例单独复验通过）。

### 2.2 关键结论（每条都有对应证据编号）

- **D0 决策**：`GO — macOS / PyInstaller`（`PROCEED TO D1 WITH DOCUMENTED NON-BLOCKING RISKS`）；Windows 侧 **BLOCKED**（无主机），**不宣称双端成立**。
- **垂直切片**：冻结 `.app` 上 DOCX / 文本层 PDF / 扫描 OCR(eng) / Arabic OCR(ara) / 混合中英阿 / 多句编辑 / 交付下载 / 退出 / 重开恢复。
- **身份握手**：`instance_id + startup_token` 经父子管道；就绪 = token + 服务 PID 存活 + 进程树归属 + 端口归属（**不以 HTTP 200 为身份**）。
- **退出语义**：空闲 / 未失焦编辑 / ⌘Q / 运行中 / 暂停 / 超时 / 2–3 个并发任务 / 暂停+运行 / `cancelling` 瞬态 —— 均**停止所有活跃任务**且不谎报安全。
- **数据安全**：试运行零写入（两棵树逐文件 no-op）、不安全来源拒绝（6 类）、导入不覆盖且原目录字节不变、缓存与任务资产分离、任务删除需逐字确认且运行/暂停态拒删、项目删除写可恢复备份。
- **诊断**：版本取自随包运行时（与包内 metadata 一致）、`job_state=unversioned` 且 schema 版本取自常量、脱敏含**裸凭据**形态、无家目录/环境变量全文。
- **OCR 基线**：随包 Tesseract + `eng/chi_sim/ara` 固定版本与许可记录；缺包**明确报错、不静默回退 eng**；LibreOffice 缺失保持 `NOT_RUN`。

### 2.3 本轮发现并修复的真实缺陷（4 个产品缺陷）

| # | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- |
| 1 | 画像 OCR 进度回调在 worker 线程调用 Streamlit `status.update` 抛异常 → 被当作"取消"，0 页 | 用户拿不到 OCR 结果 | `app.py::_safe_progress_reporter` |
| 2 | 诊断的 `runtime`/`translation_drafts` **declared 版本写死 None**，未取常量 | 与 roadmap §3.5 不符 | `diagnostics._core_schema_declared()` |
| 3 | 诊断脱敏只处理带键名写法 → **裸 API Key 进包** | 凭据泄漏 | `diagnostics._SECRET_PATTERNS`（先具体后通用） |
| 4 | 退出流程只停止"窗口里那个"任务 → 其余任务被随服务强杀，**却报 safe_to_exit=true** | 数据/语义风险 | `exit_flow` 遍历取消所有活跃任务 + 安全判定覆盖全部 |

另修：2 处**发布披露缺口**（"只列实际验收的系统"、干净机器验收状态）与 2 处**脚手架记账缺陷**。

---

## 3. 已知问题（未闭环）

| 项 | 状态 / 原因 |
| --- | --- |
| **干净机器验收**（D2 完成条件） | **未完成**：本机非干净环境。已备好手册与免 Python 脚本：`docs/desktop-d2/clean-machine-acceptance.md` + `scripts/clean_machine_acceptance.sh`（本机 19/19 PASS） |
| **签名 / 公证 / 真实下载验收** | **未完成**：无 Developer ID；产物 ad-hoc；`spctl` 判定 rejected（如实披露） |
| **Windows 全部线路** | **未提供**：无 Windows 主机；产品负责人决定后续解决 |
| 真实 OS 级中文 IME / 光标 | **人工**：无辅助功能授权（AppleEvent `-1712`） |
| 原生保存对话框 / 强制退出对话框点击 | **人工** |
| 机器睡眠唤醒 | **人工**（状态识别已实现并验证） |
| 真实 LibreOffice 渲染 | 本机未安装 → `NOT_RUN`（未伪报） |
| 项目删除"仍含任务时拒绝"的**端到端**证据 | **未完成**（D1-24）：探针等待 mover 面板超时；core 单测 + UI 代码核查已覆盖 |
| PaddlePaddle 在**源码模式**下 `libpaddle.so` 段错误 | 已记录、未修；Paddle **未随包**，桌面用户不受影响 |
| `osd`/`snum` 模型未随包 | 仅影响方向/数字识别，不影响 `eng/chi_sim/ara` |
| 数百/上千任务规模、3 个以上并发任务的部分组合 | 未逐一取证（50 任务与 2–3 并发已验证） |

---

## 4. 未验证范围（明确边界）

- **平台**：仅 macOS 27.0.1 arm64 实机验证；Intel Mac、Windows、Windows ARM64 未验证。
- **签名链路**：Developer ID 签名、硬化运行时、公证、staple、真实下载后 Gatekeeper 体验 —— 全部未验证。
- **人工交互**：真实 IME 组合输入、光标/字体细节、原生保存面板、强制退出确认框点击、睡眠/唤醒、真实 ⌘C/⌘V。
- **外部依赖**：真实模型服务的付费调用（本轮全部用本机 stub，零外网调用）、真实 LibreOffice 渲染。
- **异常路径**：磁盘满/权限拒绝、网络卷、符号链接/硬链接来源、并发写者竞争。

---

## 5. 交接给 Qt 的资产（复用范围）

### 5.1 直接复用（不重写）

| 资产 | 位置 | 说明 |
| --- | --- | --- |
| **业务核心** | `core.py` | 解析/分段/翻译编排/审校/交付快照/TM/术语/运行状态/锁/迁移 —— Qt 界面只调用它 |
| **数据格式与目录** | `<data>/<job>/{state.json,runtime_state.json,source.bin,delivery_snapshots/vN/**,…}` | **格式不变**；`job_state=unversioned`、`runtime=2`、`journal=1`、`translation_drafts=1` |
| **锁语义** | `folith/job_lock.py`（`flock`） | worker 锁与应用锁；Qt 版沿用 |
| **状态日志/迁移** | `folith/storage/` | `JOURNAL_SCHEMA_VERSION` 等 |
| **旧数据接入** | `folith/desktop/legacy_import.py` | 停旧实例 → 复制不覆盖 → 校验 → 切换；试运行零写入 |
| **工具发现** | `core._find_tesseract()` / `core._find_soffice()` + `FOLIOTHREAD_*` 覆盖 | 不依赖 shell PATH |
| **诊断** | `folith/desktop/diagnostics.py` | 版本/平台/工具/schema/脱敏（含裸凭据） |
| **文件动作** | `folith/desktop/file_actions.py` | 在文件管理器显示 / 用默认应用打开 |
| **视图模型** | `folith/ui/`、`folith/ui/workspace/` | 与 Streamlit 解耦的展示逻辑（状态文案、交付门禁文案、运行状态呈现） |
| **退出语义** | `folith/desktop/exit_flow.py` 的**判定逻辑** | "什么才算可以安全退出"（草稿/worker 锁/活跃状态）+ 逐任务取消顺序 |
| **打包资产** | `packaging/folith-desktop.spec`、`scripts/stage_tesseract_payload.py`、`scripts/build_app_icon.py`、`packaging/tessdata/**` | OCR 载荷、图标、资源清单可平移 |

### 5.2 需要替换（pywebview/Streamlit 专有）

| 资产 | 原因 |
| --- | --- |
| `app.py`（13.9k 行 Streamlit UI） | 换成 Qt Widgets；但**界面语义/文案/门禁规则**以旧界面为对照 |
| `folith/desktop/{launcher,service,protocol,process}.py`、`single_instance` 的服务部分 | Qt 单进程即可，可能不再需要"启动器 + 内部 `--serve` 子进程 + 管道握手" |
| `gui.py` 的桌面分派分支、`exit_flow` 中驱动 DOM 的辅助（`evaluate_js`/`_CANCEL_JS`） | WebView 专有；退出**判定**保留、**驱动方式**改为直接调用 Qt/核心 |
| `scripts/desktop_*.js` 探针 | 浏览器专有；Qt 用 `QTest`/直接调用替换 |

### 5.3 对照基线（必须保留）

- 旧界面仍可运行：`venv/bin/python gui.py --legacy`（源码模式），用于逐项比对**行为语义**（分段、审校门禁、交付快照、退出提示文案）。
- 旧产物仍在 `dist/desktop/`（`Folith.app` / DMG），可作为"用户已获得的安装包"基线。

---

## 6. Qt Architecture Spike（预算 5–8 轮）

### 6.1 目标与判定

- **目标**：证明"**PySide6 Qt Widgets + 现有业务核心 + 现有数据格式**"能在 macOS 上跑通真实用户路径，并给出 Windows 可行性判断。
- **必须先跑的验证流程**（每轮推进一段，最终完整走通）：

```text
导入真实 PDF/DOCX → 小型翻译任务 → 多段工作台 → 人工编辑并提交
→ 导出保存 → 正常退出 → 重开恢复
```

- **必须验证的项**：
  1. **真实中文 IME**（组合输入、光标、字体；macOS 输入源已具备）
  2. **中英阿混排**（含 RTL 光标与导出）
  3. **长文本**（长 textarea/富文本编辑，不丢字、不卡死）
  4. **界面响应**（翻译运行中 UI 不冻结；进度/日志可见）
  5. **未保存编辑处理**（关闭窗口时：保存/提示/不静默丢弃）
  6. **worker 回收**（退出后无线程/进程/锁残留）
  7. **frozen 应用启动**（PyInstaller 打包后能启动并跑通上述流程）
- **平台**：**先 macOS**；Windows 保留"实机验收"要求（不宣称双端成立）。
- **交付**：Go / No-Go、**实际复用范围**、阻断问题、**迁移估算**；随后再推进其余页面。

### 6.2 建议的轮次切分（每轮一个最小工程单元 + 证据）

| 轮 | 单元 | 完成判据 |
| --- | --- | --- |
| S1 | 环境与骨架：PySide6 安装、最小窗口、`core` 复用边界审计（哪些函数可直接调用、哪些依赖 Streamlit 运行时） | 冻结/源码下窗口能启动；列出 core 的"可直接调用 / 需适配 / 不可用"清单 |
| S2 | 任务创建与导入：文件选择 → 解析 → 建任务（复用 `core`），显示任务列表 | 真实 PDF/DOCX 导入成功，数据落在**现有格式**目录 |
| S3 | 运行与进度：启动小型翻译任务（本机 stub），UI 不冻结，可取消/暂停 | 任务跑完；取消/暂停后 worker 锁释放 |
| S4 | 多段工作台与编辑：分段列表、编辑、提交（复用 `core` 的草稿/提交语义） | 编辑落盘 `human_edited`，切走再切回一致 |
| S5 | 审校与交付：审校门禁、交付快照、导出保存（原生保存面板） | 交付 v1 生成；文件可被 Word/Pages 打开 |
| S6 | 退出与恢复：未保存编辑处理、退出回收、重开恢复 | 退出后无残留；重开后编辑与交付仍在 |
| S7 | 打包与冻结验证：PyInstaller + Qt 插件打包（macOS），跑通完整流程 | 冻结应用能启动并完成 6.1 的完整流程 |
| S8 | 结论：Go/No-Go、复用范围、阻断问题、迁移估算（含页面清单与工作量） | 报告 + 证据 |

### 6.3 前置状态（已探明）

- `PySide6` **未安装**；`pip download PySide6` 实测可用（拿到 `pyside6-6.11.2-cp310-abi3-macosx_13_0_universal2.whl`）。
- 注意：PySide6 6.11 要求 **macOS 13.0+**（与现声明的最低版本一致），且为 **universal2**（Intel 亦可用）。
- 打包需处理 Qt 插件（`platforms/libqcocoa.dylib` 等）与 `qt.conf`，这是 S7 的主要风险点。

---

## 7. 复现命令（现有成果）

```bash
# 构建应用与镜像
bash scripts/build_desktop.sh
bash scripts/build_dmg.sh

# 发布校验与发布说明
venv/bin/python scripts/release_checks.py --mount --out build/desktop/release-manifest.json
venv/bin/python scripts/release_notes.py --check

# 端到端 sweep（26 用例；可 --only NAME / --skip-slow）
venv/bin/python scripts/desktop_e2e_sweep.py --out-dir build/desktop/e2e-sweep

# 桌面门禁（含冻结产物探测）
FOLITH_D0_FROZEN_GUI=1 \
FOLITH_DESKTOP_FROZEN_APP="$PWD/dist/desktop/Folith.app/Contents/MacOS/Folith" \
venv/bin/python -m pytest tests/gui_launcher_test.py tests/desktop_*.py tests/brand_assets_test.py -q

# 干净机器验收（在干净 Mac 上）
./scripts/clean_machine_acceptance.sh --dmg <dmg> --expect-sha256 <sha256> --install

# 旧界面（对照基线）
venv/bin/python gui.py --legacy
```

---

## 8. 不可违反的约束（Qt 版同样适用）

1. **业务语义不变**：分段、源/目标真值、人工编辑、审校决定、TM、术语、交付门禁、任务恢复、OCR 状态、`NOT_RUN` 语义、源文件与交付快照。
2. **不制造并行真值**：界面/启动器只读既有产物，不新建第二套状态。
3. **数据安全优先**：不得静默丢弃用户工作；破坏性操作必须显式确认；失败不得伪报成功。
4. **缺能力如实报错**：缺 OCR 包/缺 LibreOffice 必须明确，**不静默回退**、不把 `NOT_RUN` 说成 `PASS`。
5. **平台声明诚实**：macOS 先验收；Windows 未实机验收前不宣称支持。

---

## 9. 交接结论

- 既有 Streamlit + pywebview 桌面工程**已收尾**：D0/D1/D2 工程项与 D3 本地发布工程均有冻结产物证据，累计修复 4 个真实缺陷。
- 其**不可用部分**（签名/公证/干净机器/Windows/人工交互）已逐项标注，不阻塞 Qt 方向，但**Windows 实机验收**与**签名链路**在 Qt 版仍需重做。
- Qt Spike 的**最大复用面**是 `core.py` + 数据格式 + 锁/日志/快照/导入/诊断/工具发现；**最大工作量**在 `app.py` 对应的 107 个渲染单元 → Qt Widgets 的等价实现（见 S8 的迁移估算）。
