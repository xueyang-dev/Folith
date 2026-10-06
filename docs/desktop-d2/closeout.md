# D2 里程碑收尾审计（Installable Beta）

日期：2026-10-03 · 路线图：`docs/desktop-client-roadmap.md` v1.1 · 证据：`docs/desktop-d2/evidence-log.md`（D2-1…D2-10 + 安装/卸载验收 + 14 用例端到端 sweep）

审计口径（prompt §25）：逐条对照 roadmap 要求与 D2 完成条件，显式标注
`PASS` / `FAIL` / `BLOCKED` / `NOT APPLICABLE` / `NOT YET TESTED`。
**强制项仍为 NOT YET TESTED 即不算完成**。

产品负责人决定（2026-10-03）：macOS 优先，Windows 之后解决；**不宣称双端成立**（roadmap line 201 亦如此要求）。

---

## A. 共享 D2 要求（roadmap line 169 / §3.4 / §3.5）

| # | 要求 | 结论 | 证据 |
| --- | --- | --- | --- |
| 0 | 升级只更新应用、保留数据（§3.2） | **PASS** | D2-6：构建 A(0.5.0) 建数据 → 覆盖安装构建 B(0.5.1/upgrade-*) → 升级后应用报告 B、重开既有任务（编辑与交付完好）、27 个持久资产 sha256 全部不变、0 个迁移/备份产物、卸载后数据仍在 |
| 1 | 安装卸载 | **PASS（macOS）** | 从 DMG 复制到 `/Applications` 安装 → 运行 → 移除应用后**数据目录逐项不变**（308 KB、`delivery_status=final`、12 段、快照 v1 与其全部交付文件） |
| 2 | 随包运行时（首次启动不执行 pip） | **PASS** | 冻结应用在 `env -i`（仅 HOME/PATH/TMPDIR）下运行；静态核查无运行时 `pip install`（仅源码模式缺依赖时的提示文案）；`diagnostics.app.frozen=true`、`packaging_backend=pyinstaller` |
| 3 | 依赖检测 | **PASS** | 诊断 `tools` 报告随包 Tesseract 路径 + 语言列表、LibreOffice `found/NOT_RUN`；`runtime` 报告 Python/Streamlit/pywebview/PyInstaller 版本 |
| 4 | 文件集成 | **PASS** | 交付页「打开」「显示所在文件夹」仅对**当前交付快照内实际存在**的资产提供；冻结产物端到端点击「显示所在文件夹」→ `已在文件管理器中显示：translation.docx`；下载按钮路径同时验证（`download-delivery` 步骤 OK） |
| 5 | OCR 基线：随包 Tesseract + eng / chi_sim / ara，固定数据版本与许可记录 | **PASS** | 三语言在冻结产物中**无 Homebrew PATH**下全部识别成功（eng 334 / chi_sim 102 / ara 195 字符）；数据为入库固定的 tessdata_fast，`packaging/tessdata/README.json` 记录来源 URL、字节数、sha256、许可（Apache-2.0）；引擎依赖闭包随包（无 `/opt/homebrew` 残留引用） |
| 6 | 缺包明确报错，不静默改用 `eng`，不宣称全部语言开箱即用 | **PASS** | 仅装 eng 时：`ocr_language_status("chi_sim").ok=False` 且给出明确提示；实际 OCR 返回 0 字符 + 明确警告，报告仍记录请求语言 `chi_sim`（**未**回退 eng） |
| 7 | LibreOffice 可选、缺失保留 NOT_RUN；补 Applications / Windows 安装位置发现并验证桌面 PATH | **PASS（发现逻辑）/ NOT YET TESTED（真实渲染）** | 候选路径含 macOS `/Applications/LibreOffice.app/...` 与 Windows `C:\Program Files[(x86)]\...`；Finder 式 PATH 下仍能发现（假 bundle 单测）；本机缺失 → `found=false`、`render_qa=NOT_RUN`（准确，未伪造成 PASS）；真实 LibreOffice 渲染未实测（本机未安装） |
| 7a | 随包资源完整性（§15「先定位**具体**缺失资源」） | **PASS** | D2-9：仓库 `folith/resources/**` 与包内副本 **29/29**、无缺失/多余、逐文件 sha256 一致；新增**阻断式**发布校验 `bundled_resources_complete` |
| 7b | UI 语言切换（随包语言目录在运行时真正生效） | **PASS** | D2-9：冻结产物内切到 English 后出现 `New task`/`Task history` 且「新建任务」消失，切回中文恢复；证明包内 `en.json` 被真实加载 |
| 7c | 首次启动（数据目录不存在） | **PASS** | D2-10：数据/日志目录指向不存在的嵌套路径 → 应用自行创建两者、HTTP 200、UI 可用 |
| 7d | 偏好跨重启持久化（§3.2 数据目录） | **PASS** | D2-10：切换语言后 `ui_preferences.json` = `{theme: light, locale: en}`；**重启服务后界面自动回到 English** |
| 7e | 画像 OCR 健壮性（进度回调失败不得中断解析） | **PASS（缺陷已修）** | D2-7：端到端复跑发现 `status=cancelled`、0 页；根因是工作线程调用 Streamlit `status.update` 抛控制流异常被当作取消；新增 `_safe_progress_reporter` 后三语言 OCR 全通过 |

## B. macOS D2（roadmap line 154）

| # | 要求 | 结论 | 证据 |
| --- | --- | --- | --- |
| 8 | DMG | **PASS** | `Folith-0.5.0-macos-arm64.dmg`，**152,082,974 字节**（应用 332 MB，UDZO），`hdiutil verify` 通过，sha256 `48a92497…` 记录在 `build/desktop/dmg_record.json`；含 `Applications` 拖拽目标；**重切后**新增阻断式校验 `dmg_app_matches_built_app`（镜像内应用必须等于已构建应用，防止改了应用却没重切镜像） |
| 9 | 应用标识 | **PASS** | `CFBundleName=Folith`、`CFBundleIdentifier=dev.folith.desktop`、`CFBundleIconFile=Folith.icns`（自带图标，非 PyInstaller 占位图）；LaunchServices 实测 `"Folith" / dev.folith.desktop / (in front)`；渲染后窗口标题为产品名 |
| 10 | Dock / 菜单 / Cmd | **PASS（菜单与键位）/ NOT YET TESTED（真实按键）** | 从应用内读 `NSApp.mainMenu`：`About Folith`、`Quit Folith`(⌘Q)、`Cut/Copy/Paste/Select All`(⌘X/C/V/A)、`Enter Fullscreen`(⌘F)；⌘Q 行为沿用 D1-3 端到端证据（走完整 save & stop） |
| 11 | 工具定位 | **PASS** | 见 #3/#7：随包 OCR 与 LibreOffice 发现均在"桌面启动环境"（无 Homebrew PATH）实测 |
| 12 | 验收：普通用户不依赖 Terminal、系统 Python 或 Homebrew 完成主流程 | **PASS（本机近似）** | 从 DMG 安装到 `/Applications` 的应用，在 `PATH=~/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin`（无 Homebrew、无 venv）下完成**全流程 12/12 步**：导入 PDF → 翻译 12/12 → 编辑并提交 → 审校 → 冻结交付 v1 → 下载交付文件（`ok: true`，job `ad41fe942d03148c`） |

## C. D2 完成条件（roadmap line 169）

| 要求 | 结论 |
| --- | --- |
| 产物：Apple Silicon DMG | **PASS** |
| 产物：Windows x64 安装器 | **BLOCKED**（无 Windows 主机；产品负责人已决定 Windows 后续解决；PyInstaller 不能交叉编译） |
| 完成条件：**无开发环境的干净机器以普通用户完成验收** | **NOT YET TESTED（外部环境）** —— 已备好可照做的验收手册与**免 Python**的自动冒烟脚本：`docs/desktop-d2/clean-machine-acceptance.md` + `scripts/clean_machine_acceptance.sh`（本机实测 19/19 PASS） —— 本机已用"从 DMG 安装 + 无 Homebrew/venv 环境 + 全流程"作近似验证，但这不是一台**干净的 Mac**（系统里仍存在本项目的 venv、Homebrew 工具、以及既有 `/tmp/folith-*` 残留）。按 roadmap line 219，D2/D3 必须验收**用户实际获得的安装包**，因此该条在真正干净机器上完成前**不能宣告通过** |

## D. 未闭环项（全部为外部环境 / 人工）

| 项 | 分类 | 说明 |
| --- | --- | --- |
| 干净 Mac 普通用户验收 | NOT YET TESTED（外部） | 需一台无开发环境的 Mac；已备好 DMG（150.9 MB）与可复现的验收脚本 |
| Windows D2（WebView2/pythonnet、按用户安装、入口、卸载、Ctrl、缩放、文件集成） | BLOCKED（缺设备） | 产品负责人决定 macOS 优先 |
| 签名 / 公证 / staple | BLOCKED（无 Developer ID） | 阻塞 D3；DMG 记录中 `signed: false` |
| 真实 LibreOffice 渲染 | NOT YET TESTED（缺工具） | 发现逻辑与 NOT_RUN 已验收 |
| 真实 OS 级中文 IME / 原生保存对话框 / 强制退出对话框点击 / 机器睡眠唤醒 / 真实 ⌘C⌘V 按键 | NOT YET TESTED（人工） | 无辅助功能授权（AppleEvent `-1712`），D0/D1 已记录 |
| 真实"用默认应用打开交付文件" | NOT YET TESTED（人工） | 会在用户机器上启动 Word/Pages；「显示所在文件夹」已端到端验证 |

## D2b. 里程碑级复跑（对**当前产物**）

`scripts/desktop_e2e_sweep.py` 顺序重跑 **17 个**端到端验证器（退出矩阵 / 编辑退出 / ⌘Q 退出 / 运行中退出 / 暂停退出 / **退出超时** / 崩溃恢复 / 多句编辑 / 旧数据接入 / **试运行零写入** / **不安全来源拒绝** / OCR 基线 / 文件集成 / UI 语言 / 首次启动与偏好 / DMG 挂载启动 / 升级保留数据），逐例记录退出码、自验结果、耗时与基础设施失败分类（详见 D2-8）。这是"证据针对当前产物"的保证，也是发现 D2-7 产品缺陷的手段。

**结果：17 / 17 全部通过（D2-12，且全部在第一次尝试通过、无基础设施失败）。** 早期 14 用例轮次的结果：14 / 14 通过。 过程中 `edit-exit` 曾失败，归因为**脚手架夹具不合格**（把长句替换成短标记，被产品的确定性完整性 QA 正确拦下，见 D2-11），修正夹具后复验通过。

## E. 回归状态（D2 收尾）

| 门禁 | 结果 |
| --- | --- |
| 全量桌面门禁（含冻结产物与真实 WKWebView 探测、脚手架契约测试） | **148 passed** |
| 交付 / 文件集成相关产品回归（`delivery_snapshot_test`、`delivery_builder_ui_test`、`ui_console_test`、`missing_translation_delivery_test`、`report_workspace_test`） | **60 passed** |
| 端到端验收与持久化（`release_e2e_acceptance_test`、`persistence_phase_a_test`、`desktop_dmg_test`） | **22 passed** |
| OCR 产品回归（`pdf_ocr_test`、`ocr_arabic_smoke_test`） | **5 passed, 4 skipped**（skip 为宿主既有条件） |
| 脚本级端到端 | `d2_ocr_baseline_runner`（三语言）、`d2_file_actions_runner`、`d2_dmg_runner`、D1 四个 runner 全部 `ok: true` |

## F. 结论

**macOS D2 的工程内容已全部实现并验证**（#1–#12 全部 PASS，除标注为外部/人工的细项）；
但按 roadmap 的 D2 完成条件，**里程碑仍差一项外部验收**：一台干净 Mac 上的普通用户验收。
Windows D2 为 BLOCKED（产品负责人决定）。因此本轮**不宣告 D2 完成**，而是进入：

- 可继续推进的 macOS 侧工作：D3 的**本地**部分（release 工程、产物校验脚本、发布说明草稿）——但 D3 的签名 / 公证 / 分发受 Developer ID 缺失阻塞；
- 外部等待项：干净 Mac 验收、Windows 主机、Developer ID。

按 prompt §20，此处达到 **STOP B（缺外部环境）** 与 **STOP E（下一里程碑被门禁阻塞）** 的交界：
本地可做的 D2 工程已穷尽，D3 的发布动作需要证书与干净机器。
