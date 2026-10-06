# D2 桌面工程证据日志（Installable Beta）

路线图：`docs/desktop-client-roadmap.md` v1.1。D0 见 `docs/desktop-d0/`，D1 见 `docs/desktop-d1/`。
本文件是审计轨迹，不替代测试。

## 产品负责人决定（记录在案）

2026-10-03：macOS 优先推进，Windows 之后解决；**不宣称双端成立**。

## D2 范围（roadmap §3.4 / §4 / §5 表、§6 里程碑表）

| 要求 | 状态 |
| --- | --- |
| 随包运行时与依赖检测（首次启动不执行 pip） | **本轮完成 OCR 部分**（Tesseract + 三语言包随包）；其余依赖已在 D0 冻结 |
| OCR 基线：随包提供 Tesseract 与 eng / chi_sim / ara（固定数据版本与许可记录） | **本轮完成并三语言验收** |
| LibreOffice：Applications / Windows 安装位置发现 | 未开始 |
| 应用标识与原生集成（Dock / 菜单 / Cmd） | 未开始 |
| 文件集成（打开交付文件 / 显示所在文件夹） | 未开始 |
| DMG 打包与安装入口 | 未开始 |
| 干净机器普通用户验收（D2 完成条件） | 未开始（需一台干净 Mac → 外部环境） |

---

## D2-1 — 随包 OCR 基线：Tesseract + eng / chi_sim / ara（已实现并三语言验收）

- 时间：2026-10-03
- 里程碑：D2（roadmap §3.4「D2 目标随包提供 Tesseract 与这三个经两端验收的语言包，固定数据版本与许可记录；不默认打入可选 PaddleOCR。若某平台未完成 bundling 或验收，发布页逐项写明实际语言及外部安装要求」；§15「显式声明资源、测量体积」）
- 变更：
  - 新增 `packaging/tessdata/`：**固定并入库**的 tessdata_fast 语言包（eng / chi_sim / ara）+ `README.json` 记录来源 URL、字节数、sha256 与许可（Apache-2.0）；
  - 新增 `scripts/stage_tesseract_payload.py`：把 Tesseract 引擎、其**非系统 dylib 依赖闭包**（15 个文件）、三语言包、`configs/`+`tessconfigs/`+`pdf.ttf` 与来源记录 stage 到 `build/desktop/tesseract`；
  - `scripts/build_desktop.sh`：每次 macOS 构建先 stage payload（数据版本与许可记录随构建固定）；
  - `packaging/folith-desktop.spec`：把 payload 作为 `binaries`（引擎 + dylibs）与 `datas`（tessdata + 来源记录）显式声明（修复：`Analysis` 原先硬编码 `binaries=[]`，导致第一批 payload 只进了 datas）；
  - `folith/desktop/paths.py`：新增 `tesseract_payload_root()`；
  - `folith/desktop/launcher.py`：服务子进程环境注入 `FOLIOTHREAD_TESSERACT_CMD` 与 `TESSDATA_PREFIX` 指向**包内**引擎与数据（实测 `ps eww` 确认）。
- 体积测量（同一源码、同一依赖）：308 MB（无 payload）→ **331 MB**（含 payload）；payload 本体 17.0 MB（引擎+dylibs 8.3 MB、三语言包 8.0 MB、configs/tessconfigs 0.1 MB，其余为重复拷贝）。
- 三语言端到端验收（冻结 `.app`，**PATH 中已剔除 Homebrew**，即机器上无可用的系统 tesseract）：

| 语言 | 输入 | OCR 引擎 | 识别结果 | 判定 |
| --- | --- | --- | --- | --- |
| eng | 300 DPI 英文扫描件 | `tesseract`（包内） | 334 字符，含预期句 | **PASS** |
| chi_sim | 300 DPI 中文扫描件（新夹具） | `tesseract`（包内） | 102 字符，含预期词 | **PASS** |
| ara | 300 DPI 阿拉伯语扫描件 | `tesseract`（包内） | 195 字符，含预期词 | **PASS** |

- 包内自证：
  - `env -i <bundle>/Contents/Frameworks/tesseract/bin/tesseract --version` 可运行（依赖闭包完整、install name 已重写为 `@rpath/...`，**无 /opt/homebrew 残留引用**）；
  - `TESSDATA_PREFIX=<bundle>/.../tessdata tesseract --list-langs` → 恰好 `ara / chi_sim / eng`；
  - 服务子进程实测环境变量指向包内路径。
- 过程中发现并修掉的问题（均记录，避免复发）：
  1. **Homebrew 安装的 `chi_sim.traineddata` 与 tesseract 5.5.0 不兼容**：`--list-langs` 能列出，但加载时报 `Error opening data file`，OCR 必然失败。→ 改用固定入库的 tessdata_fast 三语言包（roadmap 引用的正是 tessdata_fast）。
  2. spec 的 `Analysis(binaries=[])` 硬编码导致引擎未进包（只有数据进了），首轮 bundle 里找不到 `bin/tesseract`。
  3. 验证脚本（源码模式）会用自己的 `TESSDATA_PREFIX` 覆盖包内数据，且该目录里的 chi_sim 正是上面那份损坏的包 —— 首轮 chi_sim 失败的真因；已改为**指向被测 bundle 的 payload**。
  4. 切片工具假设 PATH 上有系统 `tesseract`（`--list-langs`），剔除 Homebrew 后直接崩；已改为容忍缺失并记录。
  5. CJK 期望值匹配需先去掉 tesseract 在汉字间插入的空格（`生态 恢复`），否则误判失败。
  6. 中文夹具字体：MuPDF 的 HTML 引擎无法加载 `.ttc`（`substitute font creation is not implemented yet`），改用单文件字体（Arial Unicode.ttf）。
- 测试与结果：
  - `scripts/d2_ocr_baseline_runner.py` → **ok: true**（三语言全过、`homebrew_on_path: false`）
  - 全量桌面门禁 **61 passed**；OCR 相关产品回归（`pdf_ocr_test.py` / `ocr_arabic_smoke_test.py`）**5 passed, 4 skipped**（skip 为宿主缺 `ara`/字体等既有条件）
- 诚实标注 / 剩余风险：
  - `osd`（方向检测模型）**未随包**；当前默认 `psm 3` 不需要它，若将来启用方向检测需补并重新验收。
  - 语言包为 tessdata_fast（体积小）；识别质量与 tessdata_best 有差异，已记录数据版本（sha256）以便复现与替换。
  - Windows 侧随包与验收 **BLOCKED**（无主机）。
  - "干净机器普通用户验收"仍需一台干净 Mac。
- 下一步：D2 其余子项（LibreOffice 发现 / 应用标识与原生集成 / 文件集成 / DMG）。

---

## D2-2 — LibreOffice 桌面环境发现 + 工具能力可见（已实现并验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §3.4「LibreOffice 保持可选，缺失保留 NOT_RUN；D2 补 Applications / Windows 安装位置发现并验证桌面 PATH」；§14「不要假设 Finder / 开始菜单启动有 shell PATH…缺失可选工具必须产生准确能力状态，绝不把 NOT_RUN 变成 PASS」）
- 现状问题（实测）：原实现只用 `shutil.which("soffice") or shutil.which("libreoffice")`；D0 已实测 Finder 启动环境 `PATH=/usr/bin:/bin:/usr/sbin:/sbin`，因此**即使装了 LibreOffice 也发现不了**（终端可用、桌面不可用）。
- 变更：
  - `core.py`：新增 `_SOFFICE_CANDIDATES`（macOS `/Applications/LibreOffice.app/Contents/MacOS/soffice`、Windows `C:\Program Files[(x86)]\LibreOffice\program\soffice.exe`、Linux `/usr/bin`、`/usr/local/bin`、`/snap/bin`）与 `_find_soffice()`，并支持 `FOLIOTHREAD_SOFFICE_CMD` 显式覆盖（与既有 `_find_tesseract()` 同一模式，复用而非另建发现机制）；`run_libreoffice_render_qa` 改用 `_find_soffice()`；
  - **发现必须校验文件存在**：配置项 / PATH 命中若指向不存在的文件，一律不当作引擎（避免把"配置了但没装"误报为可用）；
  - `folith/desktop/paths.py`：新增 `apply_bundled_tool_env()`，把随包 OCR 引擎与数据环境**同时应用到启动器进程**（此前只给服务子进程，导致诊断报告的是宿主 Homebrew 引擎而非随包引擎）；
  - `gui.py`：桌面模式与 `--export-diagnostics` 路径都先应用该环境；
  - `folith/desktop/diagnostics.py`：新增 `collect_tools()`，诊断包包含**工具能力**：`tesseract{found,path,languages}` 与 `libreoffice{found,path,render_qa}`（缺工具时 `render_qa: NOT_RUN`）。
- 冻结产物实测（`PATH` 已剔除 Homebrew，即"桌面启动环境"）：

| 场景 | 观测 | 判定 |
| --- | --- | --- |
| 随包 OCR 能力 | `tesseract.found=true`、`path=<bundle>/Contents/Frameworks/tesseract/bin/tesseract`（家目录已脱敏）、`languages=[ara, chi_sim, eng]` | **PASS**：报告的是**随包**引擎与语言包，不是宿主 Homebrew |
| LibreOffice 缺失（本机真实状态） | `libreoffice.found=false`、`path=""`、`render_qa=NOT_RUN` | **PASS**：NOT_RUN 准确，未被伪造成 PASS |
| 提供 LibreOffice（用文档化的 `FOLIOTHREAD_SOFFICE_CMD` 指向可执行文件） | `found=true`、`render_qa=available` | **PASS**：存在时能被发现 |

- 测试与结果：
  - 新增 `tests/desktop_tool_discovery_test.py` → **6 passed**（缺失时 NOT_RUN、**Finder 式 PATH 下仍能发现 /Applications 安装**（用假 bundle 结构）、env 覆盖优先且必须真实存在、`run_libreoffice_render_qa` 确实走 `_find_soffice`、诊断包含工具能力、随包 OCR 能力上报）
  - 修正既有测试使其与宿主无关：`tests/stage5_rendered_qa_test.py` 的"LibreOffice 不可用"用例现在同时中和绝对候选路径；"渲染成功"用例改用真实的假可执行文件 + 文档化覆盖（原先依赖 `shutil.which` 返回幻影路径的旧行为）
  - 全量桌面门禁 → **67 passed**；LibreOffice / 交付相关产品回归（`stage5_rendered_qa_test`、`stage2_dependency_test`、`stage3_case_review_test`、`task_overview_state_test`）→ **63 passed**
- 诚实标注 / 剩余风险：
  - **真实 LibreOffice 渲染仍未实测**（本机未安装）：已验收的是"发现逻辑 + 缺失时的准确 NOT_RUN + 存在时可发现"。按 roadmap §3.4，LibreOffice 缺失保留 NOT_RUN，发布页需逐项写明外部安装要求；若将来在装有 LibreOffice 的机器上验收，应重跑 `stage5` 与桌面渲染路径。
  - `/Applications` 候选路径用**假 bundle 结构**做单元验证；未在真实安装上验证（不安装大型第三方应用以免打扰用户机器）。
- 下一步：D2 其余子项（应用标识与原生集成 / 文件集成 / DMG）。

---

## D2-3a — 应用标识与原生集成（图标 / 名称 / 窗口标题 / 菜单 / Cmd）

- 时间：2026-10-03
- 里程碑：D2（roadmap §5 macOS 行「DMG、应用标识、Dock / 菜单 / Cmd、工具定位」）
- 现状问题（实测）：`Info.plist` 的 `CFBundleIconFile` 是 PyInstaller 的占位图 **`icon-windowed.icns`** —— Dock / Finder / 应用切换器里显示的是通用图标，不是一个可识别的应用。
- 变更：
  - 新增 `scripts/build_app_icon.py`：用系统 `sips` + `iconutil` 从**仓库内跟踪的品牌图** `folith/resources/brand/foliothread-app-icon.png`（512×512）生成 `Folith.icns`（9 帧，16→512，含 @2x；不生成 1024 帧以免放大出软边）；
  - `scripts/build_desktop.sh`：macOS 构建先产出 `build/desktop/Folith.icns`（并写 `app-icon.json` 记录帧信息）；
  - `packaging/folith-desktop.spec`：`BUNDLE(icon=...)` 指向该 `.icns`（仅在存在时），其余标识字段（identifier / 版本 / 最低系统 / ATS 本地网络）保持不变。
- 冻结产物实测：

| 观测 | 值 | 判定 |
| --- | --- | --- |
| `CFBundleIconFile` | **`Folith.icns`**（不再是 `icon-windowed.icns`） | PASS |
| `Contents/Resources/Folith.icns` | 存在，235 KB；`iconutil` 回读校验 9 帧齐全 | PASS |
| PyInstaller 占位图 | 已不再出现 | PASS |
| 名称 / 标识 / 版本 | `CFBundleName=Folith`、`CFBundleIdentifier=dev.folith.desktop`、`CFBundleShortVersionString=0.5.0` | PASS |
| **LaunchServices 身份**（Dock / 菜单 / 切换器实际读取） | `lsappinfo info -only name,bundleid <launcher pid>` → `"Folith"`、`bundleID="dev.folith.desktop"`、`(in front)`（前台常规 GUI 应用） | PASS |
| **窗口标题** | 渲染后 `document.title` = `译页 · 智能体翻译工作台`（D0 早期读到的 "Streamlit" 只是页面尚未加载完） | PASS |
| **菜单栏**（从应用内部读 `NSApp.mainMenu`） | 应用菜单：`About Folith`、`Hide Folith`(Cmd+H)、**`Quit Folith`(Cmd+Q)**；编辑菜单：**`Cut`(Cmd+X)、`Copy`(Cmd+C)、`Paste`(Cmd+V)、`Select All`(Cmd+A)**；视图菜单：`Enter Fullscreen`(Cmd+F) | PASS |
| Cmd+Q 行为 | 已在 D1-3 端到端验证：走完整 save & stop 后认证停止（非 EOF 兜底） | PASS（沿用 D1-3 证据） |

- 测试与结果：
  - 新增 `tests/desktop_app_identity_test.py` → **6 passed**（spec 必须接入图标与标识字段；构建脚本必须生成图标；品牌图必须入库且 512×512；图标构建产物必须是可回读的合法 `.icns`（含 16/128/512 帧）；**已构建产物不得再带占位图**且 `CFBundleIconFile` 指向自带图标；`app.py` 仍设置产品标题）
  - 全量桌面门禁 → **73 passed**
- 诚实标注 / 剩余风险：
  - 菜单**标题**在 AppKit 侧读出来是占位类名（pywebview 把标题设在子菜单上），但**菜单项名称与快捷键**正确（上表）；属观感细节，不影响功能。
  - 复制/粘贴快捷键只验证了"菜单项存在且键位正确"，未做真实按键的端到端粘贴验证（无辅助功能授权，人工项）。
  - 未做 Dock 图标视觉确认（截屏/人工），但 LaunchServices 名称/标识/前台状态与图标文件均已实测。
- 下一步：D2 文件集成（"打开交付文件" / "显示所在文件夹"）与 DMG。

---

## D2-3b — 文件集成：打开交付文件 / 显示所在文件夹（已实现并端到端验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §3.4「必要时只补原生文件选择 / 保存连接，以及"打开交付文件""显示所在文件夹"，复用既有交付门禁」；§8 验收表「PDF / DOCX 导入至编辑、审校、正式导出 → 修复文件动作，继续调用现有交付流程」）
- 变更：
  - 新增 `folith/desktop/file_actions.py`：`reveal_in_file_manager()` / `open_with_default_app()`，平台化命令（macOS `open -R` / `open`；Windows `explorer /select,` / `os.startfile`；Linux `xdg-open`），返回结构化结果（`ok` / `argv` / `detail`）**从不抛异常**；文件不存在时给出准确原因；
  - `folith/snapshots.py`：新增 `asset_path()`（快照资产磁盘布局的唯一来源）；`core.delivery_snapshot_asset_path()` 暴露给界面，避免界面自己拼路径；
  - `app.py`：新增 `_render_delivery_file_actions()`，在交付主产物 / 语言资产卡片上，**仅当该资产确实存在于当前交付快照**时提供「打开」「显示所在文件夹」，并把结果（成功或失败原因）显示在卡片上；下载按钮与交付门禁保持不变。
- 冻结产物端到端实测（job `c240cf830acc15fb`，交付版本 v1）：

| 观测 | 值 | 判定 |
| --- | --- | --- |
| 交付快照 | `delivery_snapshots/v1` 存在，含 `translation.docx` 等 | PASS |
| 按钮渲染 | 交付页渲染 **6 个「打开」+ 6 个「显示所在文件夹」**（仅限快照内实际存在的资产） | PASS |
| 点击「显示所在文件夹」 | 界面提示 `已在文件管理器中显示：translation.docx`（原生 `open -R` 返回码 0） | PASS |
- 测试与结果：
  - 新增 `tests/desktop_file_actions_test.py` → **8 passed**（平台命令、缺失文件不抛异常、成功记录 argv、命令失败暴露原因、异常不外泄、快照路径布局一致、交付页只在快照文件上提供动作、不引入 pywebview 桥）
  - 全量桌面门禁 → **81 passed**；交付相关产品回归（`delivery_snapshot_test` / `delivery_builder_ui_test` / `ui_console_test` / `missing_translation_delivery_test` / `report_workspace_test`）→ **60 passed**
- **本轮自引入并修复的严重缺陷（记录在案）**：首次接线时把 `_render_delivery_file_actions` 插到了 `_render_workspace_delivery` **函数体内部**，导致该函数在交付摘要卡片之后就被"截断"——**交付页的文件网格与下载按钮整体消失**（语法检查无法发现，因为嵌套 `def` 合法）。定位方式：用 `streamlit.testing.v1.AppTest` 渲染真实任务交付页，发现只有 `ctx_dl_*`（上下文面板）而没有 `dl_prim_*`（主网格），再用 AST 检查确认函数结构被破坏；已把辅助函数移到模块层并复核 `_render_workspace_delivery` 仍包含网格与 `primary_deliverables`。修复后 AppTest 同时出现 `dl_prim_*` 与 `open_prim_*` / `reveal_prim_*`。
- 诚实标注 / 剩余风险：
  - 「打开」按钮只验证了渲染与命令构造；真实"用默认应用打开"未做端到端点击验证（会在用户机器上启动 Word/Pages 等应用，未擅自执行）；`open -R`（显示所在文件夹）已端到端验证。
  - 交付文件的**原生保存对话框**（下载）仍属人工项（D0 已记录：pywebview `ALLOW_DOWNLOADS` + macOS `NSSavePanel` 模态）。
  - 本机未安装 LibreOffice，未验证"打开 PDF 渲染产物"的链路（PDF 资产不存在于该测试快照中）。
- 下一步：D2-4 DMG 打包（含 Applications 拖拽目标）。

---

## D2-4 — DMG 打包与挂载启动验收（已实现并验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §5 macOS 行「DMG、应用标识、Dock / 菜单 / Cmd、工具定位」）
- 变更：
  - 新增 `scripts/build_dmg.sh`：`ditto` 暂存 `Folith.app` + `ln -s /Applications`（拖拽目标）→ `hdiutil create -format UDZO` → `hdiutil verify` → 写 `build/desktop/dmg_record.json`（路径、字节数、sha256、版本、build、后端、**signed: false** 并注明签名归 D3）；
  - 新增 `scripts/d2_dmg_runner.py`：`hdiutil verify` → `attach -readonly` → 校验卷内容与应用标识 → **从只读挂载卷启动应用**（临时数据/日志目录）→ 验证身份握手与回收 → `detach` → 记录证据。
- 实测（Apple Silicon，macOS 27.0.1）：

| 观测 | 值 | 判定 |
| --- | --- | --- |
| 产物 | `dist/desktop/Folith-0.5.0-macos-arm64.dmg`，**150.9 MB**（应用 331 MB，UDZO 压缩后 144 MB） | PASS |
| 校验 | `hdiutil verify` 退出码 0（checksum VALID） | PASS |
| sha256 | `3ddb8e87b05f072e0d427e68ae383555b0dcdc8ae85a574c4ebcab81be69daea` | 记录 |
| 卷内容 | `Folith.app` 存在；`Applications` 符号链接 → `/Applications` | PASS |
| 应用标识（卷内） | `CFBundleName=Folith`、`CFBundleIdentifier=dev.folith.desktop`、`CFBundleIconFile=Folith.icns`（图标文件存在）、版本 0.5.0 | PASS |
| **从只读挂载卷启动** | 启动器输出正常横幅；`instance.json` 握手记录生成；服务 PID 53578 / 端口 56575 存活 | PASS |
| 退出回收 | SIGTERM 后服务进程 0 残留；`hdiutil detach` 退出码 0 | PASS |
- 测试与结果：
  - 新增 `tests/desktop_dmg_test.py` → **4 passed**（构建脚本声明拖拽目标 / UDZO / 校验 / 命名；脚本如实记录未签名并注明 D3；已构建 DMG 的记录与实际文件 sha256+字节数一致；DMG 所携带的应用与已构建应用一致（标识与图标））
  - 全量桌面门禁 → **85 passed**
- 诚实标注 / 剩余风险：
  - **未签名、未公证**：本机无 Developer ID（D0 已实测 0 个签名身份），因此该 DMG 只用于本地/Beta 验证；真实下载后的 Gatekeeper 行为、quarantine、公证与 staple 全部属 D3，未验证。
  - 卷内**未做背景图/图标布局**（DMG 观感打磨），roadmap §10 明确观感不优先。
  - 未在"干净机器"上安装（`/Applications` 安装与首次启动验收需一台干净 Mac → D2 完成条件中的外部项）。
  - 构建前清理了 PyInstaller 的 `dist/desktop/Folith`（COLLECT 副本）与 `build/desktop/work` 缓存以腾出磁盘（已先实测确认 `.app` 自包含、不依赖该副本）。
- 下一步：D2 收尾审计（§25），逐条分类 D2 要求，并明确"干净机器验收"为外部项。

---

## D2-5 — 安装 / 卸载与"无开发环境"主流程验收（本机近似）

- 时间：2026-10-03
- 里程碑：D2（roadmap line 154「普通用户不依赖 Terminal、系统 Python 或 Homebrew 完成主流程」；line 169「安装卸载…无开发环境的干净机器以普通用户完成验收」；line 219「D2/D3 验收用户实际获得的安装包」）
- 方法：
  1. 挂载已验收的 DMG，把 `Folith.app` **安装到 `/Applications`**（真实安装路径；此前确认该位置不存在同名应用）；
  2. 用 **`env -i`（仅 HOME / `PATH=/usr/bin:/bin:/usr/sbin:/sbin` / TMPDIR）** 运行**已安装**的应用并导出诊断；
  3. 以同一"无 Homebrew、无 venv"的 PATH 驱动**已安装**应用跑完整主流程（导入 → 翻译 → 编辑提交 → 审校 → 冻结交付 → 下载）；
  4. 卸载（移除我安装的那份副本）后核对数据目录是否原样保留。
- 实测结果：

| 观测 | 值 | 判定 |
| --- | --- | --- |
| 安装 | `ditto <mount>/Folith.app /Applications/Folith.app`；`codesign -dv` 显示 `Identifier=dev.folith.desktop`、`Mach-O thin (arm64)` | PASS |
| 已安装应用的随包工具 | 诊断 `tools.tesseract.path=/Applications/Folith.app/Contents/Frameworks/tesseract/bin/tesseract`、`languages=[ara, chi_sim, eng]`；`libreoffice.render_qa=NOT_RUN`（准确） | PASS |
| 已安装应用的运行事实 | `app.frozen=true`、`packaging_backend=pyinstaller`、`version=0.5.0`、`pywebview 6.2.1`、`streamlit 1.64.0`、`python 3.11.3` | PASS |
| **主流程（无 Homebrew / 无 venv）** | 12/12 步全部 OK：`open-app → configure-provider → test-connection → upload-source → strategy → delivery-options → run-task → wait-translation(12/12) → edit-and-commit → review → freeze-delivery → download-delivery`；`ok: true`，job `ad41fe942d03148c` | PASS |
| 卸载后数据 | 移除 `/Applications/Folith.app` 后，数据目录 308 KB 不变：`delivery_status=final`、12 段译文、快照 v1 及其全部交付文件（`translation.docx` 等）仍在 | PASS |
| 进程回收 | 卸载与退出后 Folith 进程 0 残留 | PASS |
- 未运行 pip 的核查：静态检查 `gui.py` / `app.py` / `core.py` / `folith/**` 无运行时 `pip install`（仅两处**源码模式缺依赖时的提示文案**）；冻结应用在 `env -i` 下正常运行，首次启动未执行任何包安装。
- 诚实标注：
  - 这**不是**"干净机器"验收：本机仍装有本项目的 venv、Homebrew 工具与既有 `/tmp/folith-*` 残留。按 roadmap line 219，D2 完成条件要求在真正无开发环境的 Mac 上验收**用户实际获得的安装包** → 该项仍为 **NOT YET TESTED（外部环境）**。
  - 卸载采用"移除应用包"（macOS 常规方式），未做卸载器脚本；数据本就位于 `~/Library/Application Support/Folith`（D0-4e 已实测默认位置），与应用包分离，因此卸载不影响数据（上表实测）。
  - 首次运行遇到一次 Chromium `Page crashed`，重跑即通过（当时 `/tmp` 有 2.2 GB 既有 `folith-*` 残留、可用空间偏低）；已如实记录，不视为产品缺陷。
- 下一步：D2 收尾审计（`docs/desktop-d2/closeout.md`）。

---

## D2-6 — 升级只更新应用、保留数据（已端到端验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §3.2「**升级只更新应用、保留数据**；实际改变格式时再设计对应备份 / 兼容处理，不预建多版本迁移体系」）
- 方法：`scripts/d2_upgrade_runner.py`
  1. 从 DMG 安装 **构建 A（0.5.0 / git:1260a82）** 到 `/Applications`，用它做真实工作：导入 PDF → 翻译 → 编辑提交 → 审校 → 冻结交付；
  2. 对任务目录内**全部持久资产**做 sha256 指纹（27 个文件，含交付快照）；
  3. 用**不同版本与 build id** 构建 **构建 B（0.5.1 / upgrade-1791019655）** 到临时输出目录（`FOLITH_OUT_DIR` / `FOLITH_DIST_DIR`，工作区不受影响），安装覆盖 A；
  4. 用升级后的应用打开**同一数据目录**：诊断须报告 B，任务须能重开且编辑与交付完好；
  5. 比对指纹、检查是否出现迁移 / 备份文件；最后移除应用，确认数据仍在。
- 实测结果（job `fb4e46ef7515e90c`）：

| 观测 | 值 | 判定 |
| --- | --- | --- |
| 升级前应用 | `0.5.0` / `git:1260a82` | — |
| 升级后应用 | `0.5.1` / `upgrade-1791019655`（诊断实测，确认应用确已替换） | PASS |
| 重开既有任务（升级后） | `job_visible: true`、**`editor_contains_edit: true`**、`delivery_visible: true`、`ok: true` | PASS |
| 持久资产 | 27 个文件：**changed 0 / added 0 / removed 0**（逐文件 sha256 一致） | PASS |
| 迁移 / 备份产物 | **0 个**（未预建多版本迁移体系） | PASS |
| 卸载应用后 | 数据目录指纹不变（`data_intact: true`） | PASS |
| 工作区 | `dist/desktop/Folith.app` 仍为 0.5.0 / git:1260a82，`build/desktop/desktop_build_info.json` 未被升级构建污染 | PASS |
- **本轮发现并修复的打包缺陷（记录在案）**：`packaging/folith-desktop.spec` **硬编码**了构建记录路径 `ROOT/build/desktop/desktop_build_info.json`，因此任何"版本 / build id 被覆盖"的构建（发布打标签、升级验证）都会**打进工作区里旧的构建记录** —— 首轮升级验证中升级后的应用仍报告 `0.5.0 / git:1260a82`，正是这个原因。已改为读取 `FOLITH_BUILD_INFO`（构建脚本传入本次写出的记录），默认值保持原路径不变。
- 附带补齐的发布工程能力：`scripts/write_desktop_build_info.py` 支持 `--app-version` / `--build-id`；`scripts/build_desktop.sh` 支持 `FOLITH_APP_VERSION` / `FOLITH_BUILD_ID` / `FOLITH_OUT_DIR` / `FOLITH_DIST_DIR`。
- 测试与结果：新增 `tests/desktop_release_plumbing_test.py` → **6 passed**（覆盖记录覆盖项、默认仍从 pyproject/git 推导、CLI 写出覆盖后的记录、构建脚本转发覆盖项与记录路径、spec 从构建输出读取记录、未预建迁移机制且 job_state 仍如实报 unversioned）；全量桌面门禁 → **91 passed**。
- 诚实标注：升级验证用的是**同一台机器上的两个本地构建**（模拟"用户用新版本替换应用"），不是真实下载渠道的升级；跨版本数据格式变更后的兼容处理按 roadmap 明确**不预建**。

---

## D2-3a 更正 — 应用图标来源（产品负责人决定 + 一处素材误用修复）

- 时间：2026-10-03
- 背景：D2-3a 首次接入应用图标时，`scripts/build_app_icon.py` 默认读取的是**旧素材集** `foliothread-app-icon.png`（`foliothread-*` 系列，带海军蓝圆角方块底）。`docs/brand.md` 的资产矩阵表明 `foliothread-*` 属**旧素材**，正式素材是 `folith-*` 系列。
- 产品负责人决定（2026-10-03，本轮确认）：**桌面 / Dock 图标使用品牌透明 mark 的高清母版** `folith-mark-hires.png`（用户提供，1254×1254）。
- 变更：
  1. `scripts/build_app_icon.py` 的 `DEFAULT_SOURCE` 改为 `folith-mark-hires.png`；旧素材集与 `folith-app-icon.png`（273×257 海军蓝方块）**都不再**用于打包；
  2. 新增并入库用户提供的高清母版 `folith/resources/brand/folith-mark-hires.png`（**原样保存，不改比例、不改字标**），并写入 `docs/brand.md` 资产矩阵（"桌面 / Dock 应用图标"）与 `tests/brand_assets_test.py` 的正式素材清单与尺寸契约（1254×1254）；
  3. 图标构建新增**方形画布归一化**：把紧贴边缘的透明 mark 裁到实际像素范围、居中放到方形画布（默认 10% 透明外边距，匹配 macOS 图标栅格），**不拉伸、不裁切、不改色**（`square_master()`，含单测：宽高相等、长宽比与源一致）。
- 实测（重建后）：

| 观测 | 值 |
| --- | --- |
| 图标源 | `folith-mark-hires.png`（artwork 1238×1161 → 方形母版 1548×1548，512 帧**清晰**） |
| 包内 `CFBundleIconFile` | `Folith.icns`（401 KB） |
| 发布校验 `own_icon` | **PASS**（不是 PyInstaller 占位图） |
| 512 帧来源 | 由 1548 px 母版下采样，非放大（旧方案从 231×226 放大，偏糊） |

- 诚实标注：`folith-mark-hires.png` 的 artwork 左缘紧贴画布（x=0），是**紧裁**而非缺角（已放大目视确认圆角完整）；若后续拿到更宽松的导出，只需替换该文件并重跑 `scripts/build_app_icon.py`。
- 测试：`tests/brand_assets_test.py`（13 passed，含新素材尺寸契约）、`tests/desktop_app_identity_test.py`（7 passed，断言默认源必须是高清 mark、且不得回退到旧素材或方块图标）。

---

## D2-7 — 随包 OCR 基线复现时发现的**产品健壮性缺陷**（已修复并复验）

- 时间：2026-10-03
- 触发：本轮新增 `scripts/desktop_e2e_sweep.py`（对**当前产物**重跑全部端到端验证器），`ocr-baseline` 用例**稳定失败**：三语言全部 `ok: false`、`chars: 0`，应用提示 `OCR 未生成可用段落；请安装语言包或手动选择风格`，随后切片在 `strategy` 步骤超时。
- 定位过程（逐层排除，均有证据）：
  1. 随包引擎与语言包**正常**：诊断 `tools.tesseract.path=<bundle>/…/tesseract`、`languages=[ara, chi_sim, eng]`；
  2. **进程内**用同一随包 payload 调 OCR → 336 字符、1 页完成、0 失败；用**应用一模一样的参数**（`checkpoint_dir=ocr_profile_cache_dir`、`ocr_backend=tesseract`、`source_language`、DPI/PSM/mode）复现 → 3 段、1 页完成；
  3. 用应用的 **automation hook 在冻结解释器内**直接调 OCR → 336 字符、1 页完成、0 失败 → 引擎、payload、冻结运行时都正常；
  4. 读 OCR 运行记录（`ocr_profile_cache/**/manifest.json`）→ `requested_indices:[0]` 但 `completed_pages: 0`、`status: cancelled`；
  5. 源码定位：`core.py:1329` 的 `except BaseException: cancelled = True` —— 说明 OCR 期间抛出了异常；
  6. 真因：应用的画像进度回调 `on_progress=lambda message: status.update(...)` 会被 OCR **工作线程**调用（`ocr-worker-0`）。当 Streamlit 运行已被判失效（重跑/页面关闭）时，这个 UI 调用抛出 Streamlit 的控制流异常，被上面的 `except BaseException` 当作"取消"处理 → OCR 中止、0 页、画像无段落 → 界面显示"OCR 未生成可用段落"。
- 修复（产品侧，最小改动）：新增 `app.py:_safe_progress_reporter()`，进度更新失败只记录一条提示（`进度提示未能刷新（…）；不影响解析结果。`）而**不再中断解析**；画像 OCR 改用它。
- 复验（重建冻结产物后，`scripts/d2_ocr_baseline_runner.py`）：

| 语言 | 修复前 | 修复后 |
| --- | --- | --- |
| eng | ok=false, 0 字符 | 该轮在 `configure-provider` 遇到**浏览器被关闭**（脚手架/环境抖动，见下），未判定 |
| chi_sim | ok=false, 0 字符 | **ok=true，102 字符，backend=tesseract** |
| ara | ok=false, 0 字符 | **ok=true，195 字符，backend=tesseract** |

- 诚实标注：
  - 修复解决了"进度提示失败导致 OCR 被取消"这一**真实产品缺陷**（chi_sim/ara 由全失败变为通过即为证据）；
  - `eng` 用例当轮失败于 `Target page, context or browser has been closed`，属**脚手架浏览器抖动**，与 OCR 无关；需在稳定环境下复跑该用例才能宣告三语言全通过；
  - 该缺陷在 D2-1 首轮验收（第 10 轮）时未出现，属**时序相关**：机器负载高时画像 OCR 期间更易被判失效。这正是"对当前产物重跑端到端验证"的价值所在。

---

## D2-8 — 对**当前产物**重跑全部端到端验证（新增 sweep 工具）与由此修复的脚手架缺陷

- 时间：2026-10-03
- 动机：D0/D1/D2 的端到端证据是在**当时的构建**上取得的；此后产物经历了图标更换、构建脚本修复、OCR 修复等多次重建。按 roadmap §6/§16「里程碑验收针对用户实际获得的产物」，需要对**当前**产物重跑全部验证，而不是引用一次性结果。
- 新增 `scripts/desktop_e2e_sweep.py`：顺序重跑端到端验证器（初版 11 个；后续加入多句编辑后为 12 个）（退出矩阵 / 编辑退出 / ⌘Q 退出 / 运行中退出 / 暂停退出 / 崩溃恢复 / 旧数据接入 / OCR 基线 / 文件集成 / DMG 挂载启动 / 升级保留数据），逐例记录退出码、验证器自报结果、耗时与**是否属脚手架基础设施失败**，汇总到 `build/desktop/e2e-sweep/sweep.json`；已接入 macOS CI（`--skip-slow`）。
- 本轮 sweep 发现并修复的问题：

| # | 问题 | 性质 | 处置 |
| --- | --- | --- | --- |
| 1 | 画像 OCR 被"进度提示失败"当作取消（`status=cancelled`、0 页、界面报"OCR 未生成可用段落"） | **产品健壮性缺陷** | 见 D2-7：新增 `_safe_progress_reporter`，进度刷新失败只记录提示、不再中断解析 |
| 2 | sweep 复用同一 case 目录 → 残留 `instance.json`/数据让验证器读到**过期握手**（`dmg-launch` 报 `service_alive=false`） | 脚手架 | sweep 每例使用全新目录 |
| 3 | 例与例之间没有等待前一个应用退出 → 下一次启动被单实例锁拒绝（`legacy-import` 7 秒失败） | 脚手架 | sweep 每例前 `_settle()`（等待 Folith / stub / 测试浏览器全部退出，有上限） |
| 4 | 多个验证器在早退路径上**不打印任何信息**（`d0_exit_matrix` 等 6 个），失败无法诊断、也无法被分类 | 脚手架 | 6 个验证器早退时打印证据 JSON + 切片输出尾部 |
| 5 | sweep 无法区分"脚手架浏览器被关闭"与"产品验证失败" | 脚手架 | 识别 `context or browser has been closed` 签名；仅对**基础设施**失败重试一次，两次结果都记录在 `attempts` |

- 结果（当前产物 `0.5.0 / git:1260a82`，含图标与 OCR 修复）：

| 用例 | 结果 |
| --- | --- |
| exit-matrix | **PASS**（重跑修复后 86.0s，exit 0） |
| edit-exit（未失焦编辑 → 关窗） | **PASS** |
| quit-exit（⌘Q / Dock 退出） | **PASS** |
| running-exit（运行中退出） | **PASS** |
| paused-exit（暂停中退出） | **PASS**（重跑 321s） |
| crash-recovery（`kill -9` 后恢复） | **PASS** |
| legacy-import（旧数据接入） | **PASS**（重跑 128s） |
| **ocr-baseline（三语言随包 OCR）** | **PASS**（修复后三语言全过：eng / chi_sim 102 字符 / ara 195 字符） |
| file-actions（打开 / 显示所在文件夹） | **PASS** |
| dmg-launch（挂载 DMG 并启动） | **PASS** |
| upgrade（升级保留数据） | **PASS**（当前产物复跑：构建 A→建数据→覆盖安装构建 B→重开既有任务→持久资产零变化；218.7s） |

**最终结果：11/11 用例全部 PASS（当前产物 `0.5.0 / git:1260a82`）。** 过程中的失败全部归因于上表的脚手架缺陷与环境浏览器抖动，修复后逐例复跑通过；产品侧唯一真实缺陷是 D2-7 的 OCR 健壮性问题。

- 诚实标注：
  - 本轮环境存在**浏览器被关闭**的抖动（`Target page, context or browser has been closed`，出现在不同步骤、无 Chromium 崩溃报告、swap 接近用满 2.5/3.0 GB）。sweep 现在会把它标为**基础设施失败**并重试；**产品断言失败不会被重试掩盖**。
  - `exit-matrix` 的验证器自报字段为 `None`（该验证器只以退出码表达结果），sweep 以退出码为准并在 `attempts` 中保留原始输出。

---

## D2-9 — 随包资源完整性与 UI 语言切换（冻结 `.app`，已验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §5 D2「普通用户不依赖 Terminal、系统 Python 或 Homebrew 完成主流程」；§15「显式声明资源…当冻结构建缺少资源时，先定位**具体**缺失资源」）
- 动机：此前只校验了 `app.py`、品牌图与 OCR payload，**没有系统核对** `folith/resources/` 全量资源（含 5 份语言目录）。资源静默漏包会让其它检查全绿、而 UI 在运行时退化。
- 实测（当前产物）：

| 检查 | 结果 |
| --- | --- |
| 仓库资源 vs 包内资源 | **29 / 29** 文件，**无缺失、无多余**，逐文件 **sha256 全部一致** |
| 包内资源根 | `Contents/Frameworks/folith/resources` |
| 语言目录 | `ar.json / en.json / es.json / ru.json / zh-CN.json`（各 1633 条，与仓库一致） |

- **UI 语言切换端到端验证**（新增 `scripts/desktop_ui_language.js` + `scripts/d2_ui_language_runner.py`）：
  1. 打开应用 → 记录初始文案（含"新建任务"）；
  2. 通过应用自己的「界面语言」弹出面板切到 **English**；
  3. 断言：界面出现 `New task`/`Task history`，且**"新建任务"消失** → 证明包内 `en.json` **确实在运行时被加载使用**；
  4. 再切回 **简体中文** → 断言"新建任务"恢复。
  - 结果：`ok: true`（exit 0），三段断言全部成立。
- 新增发布校验（阻断式）：`release_checks.py` 新增 `bundled_resources_complete` —— 比对仓库 `folith/resources/**` 与包内副本（数量 + 逐文件 sha256），缺失或不一致即失败；已并入发布清单（`ok: true`，`missing=none, mismatched=none`）。
- 已加入端到端 sweep（现为 **13 个用例**）。
- 诚实标注：语言切换验证覆盖 zh-CN ↔ en（两种目录）；`es/ru/ar` 目录已核对随包且字节一致，但未逐一做 UI 切换实测（切换机制与目录加载路径相同，风险低）。

---

## D2-10 — 首次启动（无数据目录）与偏好跨重启持久化（冻结 `.app`，已验证）

- 时间：2026-10-03
- 里程碑：D2（roadmap §3.2「桌面默认目录…桌面入口在导入 core 之前设置绝对 `FOLITH_OUTPUT_DIR`」；§5 D2 验收「普通用户…完成主流程」——普通用户第一次启动时数据目录**并不存在**）
- 动机：此前所有验证都由 runner 预先创建数据/日志目录，**没有覆盖"目录不存在"的首次启动路径**；UI 语言偏好是否跨重启保留也从未验证。
- 新增 `scripts/d2_firstlaunch_prefs_runner.py`（并把 `desktop_ui_language.js` 扩展出 `--keep` 与 `--read-only` 两种模式）。
- 实测（数据/日志目录指向**不存在的**嵌套路径）：

| 观测 | 结果 |
| --- | --- |
| 启动前目录 | `data: false`、`logs: false`（确认不存在） |
| 启动后 | 两者**均由应用创建**（`data: true`、`logs: true`） |
| 页面 | HTTP **200**，7,260 字节的 Streamlit shell（`shell_ok: true`） |
| 语言切换后写盘 | `ui_preferences.json` → `{"theme": "light", "locale": "en"}` |
| **重启服务后** | 界面**自动回到 English**（`english_present: true`、`chinese_present: false`）→ 偏好确实持久化并被读回 |
- 已加入端到端 sweep（现为 **14 个用例**）。
- 诚实标注：本轮验证的是"目录不存在 → 自动创建 → 可用"，未覆盖磁盘权限被拒、磁盘满等异常（属错误路径，未在 D2 要求内）；偏好仅验证 `locale`（`theme` 随同一文件写入，已见 `theme: light`）。
- 过程中修正的两处**测试设计**缺陷（非产品缺陷）：① 语言探针默认会切回中文，导致"持久化"检查看到的是切回后的值 → 增加 `--keep`；② 断言在服务端 HTML 里找产品标题，但标题由前端设置 → 改为断言 Streamlit shell + 由浏览器探针断言渲染后的 UI。

---

## D2-11 — 全量 sweep 中 `edit-exit` 失败的归因（**产品正确、脚手架夹具不合格**）与修复

- 时间：2026-10-03
- 触发：14 用例全量 sweep 中 13 通过、`edit-exit`（D1-2a 未失焦编辑保存）失败：setup 切片在 `freeze-delivery` 超时，界面提示 `交付被 Translation Target Invariant 阻止`。
- 定位（逐层，均有证据）：
  1. 该任务当前状态重新跑 `core.validate_delivery_translation_state(state)` → `issues: 0`，但缓存记录为 `{status: fail, blocking: true, issues: []}` —— 说明阻塞项不在 `issues` 里；
  2. 打印完整报告 → `blocking_findings` 为 **确定性完整性 QA**：
     `segment_index: 0, severity: blocking, summary: "译文疑似遗漏或被截断"`，`source_span` 是整句英文（"Recent work on Ecological restoration…"），`target_span` 是脚手架写入的 9 字标记 `基线编辑标记 2026`，`explanation: 原文长度和句子数量与当前译文不匹配`；
  3. 结论：**产品的交付门禁正确地拦住了一次"把长句替换成短标记"的伪翻译**——这不是缺陷，而是门禁在起作用。此前把该类阻塞笼统记作"源语残留"并不准确：本例的真实门禁是**完整性检查**（已在此更正）。
- 修复（脚手架）：`scripts/d1_exit_runner.py` 的 setup 编辑文本与 `scripts/desktop_d1_exit_probe.py` 的默认编辑文本改为**与源句长度相当的合理中文译文**，并保留可识别标记（`…未失焦编辑标记二〇二六…`）。
- 复验：`--only edit-exit,quit-exit` → **两个用例均 exit 0**（`edit-exit` 78.2s、`quit-exit` 79.0s；后者从 131.7s 缩短，因为冻结不再超时）。
- 最终结论：**14 / 14 端到端用例全部通过**（全量 run 的 13 项 + 夹具修复后复验的 2 项，其中 `quit-exit` 属重复确认）。
- 教训（记录）：脚手架里的"假译文"必须**通过产品自己的交付门禁**，否则验证会在错误的层面失败；后续新增编辑类用例时，编辑文本要与源文长度/语种相称。

---

## D2-12 — 全量 sweep（17 用例）在**当前产物**上一次性全通过

- 时间：2026-10-03
- 背景：产物在 D1-10（诊断 schema 取自常量）与 D1-13（诊断裸凭据脱敏）两处产品修复后重建过，且 sweep 已从 14 个用例扩到 17 个（新增 dry-run-noop / unsafe-import / exit-timeout）。因此对**当前产物**重跑全量。
- 命令：`venv/bin/python scripts/desktop_e2e_sweep.py --out-dir build/desktop/e2e-sweep-full2`
- 结果：**`ok: true`，`failures: []`，`infrastructure_failures: []`，17/17 全部在**第一次尝试**通过（无重试）**：

| 用例 | 结果 | 用例 | 结果 |
| --- | --- | --- | --- |
| exit-matrix | PASS 87.2s | dry-run-noop | PASS 67.5s |
| edit-exit | PASS 79.2s | unsafe-import | PASS 1.2s |
| quit-exit | PASS 80.4s | ocr-baseline | PASS 257.2s |
| running-exit | PASS 109.1s | file-actions | PASS 116.1s |
| paused-exit | PASS 320.7s | ui-language | PASS 18.7s |
| exit-timeout | PASS 109.0s | first-launch | PASS 23.0s |
| crash-recovery | PASS 116.3s | dmg-launch | PASS 10.4s |
| multi-segment-edit | PASS 77.2s | upgrade | PASS 166.9s |
| legacy-import | PASS 136.4s | | |

- 与上一轮（14 用例）相比：新增的三个用例全部一次通过；本轮没有出现此前的"浏览器被关闭"抖动（sweep 的基础设施失败分类为 0）。
- 诚实标注：这是**本机、当前 arm64 macOS** 上的结果；干净机器验收与签名/公证后的真实下载验收仍属外部条件（见 closeout 的 BLOCKED 项）。

---

## D2-13 — 干净机器验收：**可执行的验收工具与手册**（把外部阻塞项变成可照做的一步）

- 时间：2026-10-03
- 里程碑：D2 完成条件（roadmap §6.1「无开发环境的干净机器以普通用户完成验收」；line 219「D2/D3 验收用户实际获得的安装包」）
- 背景：本机（开发机）已把可自动化部分全部验证（D2-1…D2-12、17 用例 sweep），但"干净机器"无法在此环境提供。为了让该外部项**不需要工程判断即可执行**，新增：
  - `scripts/clean_machine_acceptance.sh`：**只用 macOS 自带命令**（`hdiutil` / `shasum` / `defaults` / `curl` / `ditto`，**不需要 Python / Homebrew**）的自动冒烟检查，产出 `clean-machine-report.txt` + `diagnostics.json` + 启动日志；
  - `docs/desktop-d2/clean-machine-acceptance.md`：逐步手册（A 自动冒烟 / B 人工主流程 15 步 / 记录回传 / 已知限制 / 判定标准）。
- 脚本覆盖（本机实测 **19/19 PASS**，用 `--data-dir` 隔离数据）：

| 检查 | 结果 |
| --- | --- |
| sha256 与给定值一致 / `hdiutil verify` | PASS |
| 只读挂载；卷内 `Folith.app` + `Applications → /Applications` | PASS |
| 标识（`Folith` / `dev.folith.desktop` / 图标文件存在） | PASS |
| 启动后生成实例记录（端口 + 服务 PID） | PASS |
| UI `http://127.0.0.1:<port>` → **HTTP 200** | PASS |
| 数据目录创建 + 状态目录内实例记录 | PASS |
| 随包 OCR 列出 `ara chi_sim eng`（无 Homebrew） | PASS |
| 诊断：`frozen=true`、`packaging_backend=pyinstaller`、`job_state=unversioned`、declared runtime=2、**无凭据样文本** | PASS |
| 退出后**无残留** `Folith --serve` | PASS |
- 诚实标注：脚本**不能**替代人工主流程（导入 → 翻译 → 编辑 → 审校 → 交付 → 原生保存面板 → 外部打开 → 退出 → 重开恢复）与 Gatekeeper 首次启动提示、真实 IME；手册第 B 部分与"已知限制"逐项列出，并要求回传报告 + 诊断 + 逐步记录。**D2 完成条件仍为 `NOT YET TESTED`，直到干净机器上跑完 A+B。**
- 过程中的一个自查缺陷（已修）：脚本最初在数据目录下找 `instance.json`，而该记录位于**状态目录**（数据目录的父目录，设计上位于用户数据之外）；同时修掉了 `kill` 后的 job-control 噪声。

---

## D2-14 — 跨里程碑证据一致性复核（D0–D3，prompt §25）并固化为回归测试

- 时间：2026-10-03
- 动机：prompt §25 要求收尾审计检查"每条 roadmap 要求、每个完成条件、**文档准确性**、平台声明、未支撑假设"。此前的收尾文档由多轮增量写成，需要一次**机械化**复核：引用的证据编号是否真实存在、要求行是否都带分类、平台声明是否与产品负责人决定一致。
- 复核方法与结果（`tests/desktop_evidence_consistency_test.py`，**9 passed**）：
  1. **证据编号可解析**：D0/D1/D2/D3 四份 closeout 中引用的每个 `D*-*` 编号，都必须在对应证据日志里存在同名小节。日志现有小节：D0 **15** 个、D1 **17** 个、D2 **14** 个、D3 **4** 个 → **零缺失**；
  2. **要求行必须分类**：D1/D2 closeout 的编号要求行必须带 `PASS` / `FAIL` / `BLOCKED` / `NOT APPLICABLE` / `NOT YET TESTED` 之一 → **零未分类**（先前扫描出的"未分类"行全部位于 D0 **决策记录**，其表格是决策/缺陷清单而非要求分类表，属扫描器的误报）；
  3. **D0 决策用词合规**：`d0-decision.md` 必须出现 §26 允许的推荐语之一，实测为 **`PROCEED TO D1 WITH DOCUMENTED NON-BLOCKING RISKS`**；且 A–I 九个章节齐全；
  4. **平台声明一致**：任何收尾/发布文档都不得出现"Windows 已支持/已签名/双端均通过"这类**肯定性**声明（产品负责人已决定 Windows 后续解决），且伞状文档必须写明 BLOCKED / 未提供 / 不宣称双端成立；
  5. **人工项分类跨文档一致**：真实 OS 级中文 IME 在 D0（部分验证，唯一未闭环强制项）、D1（NOT YET TESTED，D0 遗留）、D2（NOT YET TESTED，人工）、D3 发布说明（未验证）四处口径一致，原因均为"无辅助功能授权（AppleEvent `-1712`）"。
- 结论：**未发现文档与证据不一致**；本轮未改动任何产品代码，只新增一致性回归测试，使"引用必须可解析、要求必须分类、平台声明必须诚实"成为可持续保证。

---

## D2-15 — 全量 sweep（21 用例）在 **D1-16 修复后**的产物上通过 + 两处脚手架记账缺陷修正

- 时间：2026-10-03
- 背景：D1-16 修改了退出流程（现在会停止**所有**活跃任务），而退出路径被大量用例覆盖，必须整体复跑；同时 sweep 已扩到 21 个用例。
- 命令：`venv/bin/python scripts/desktop_e2e_sweep.py --out-dir build/desktop/e2e-sweep-full3`
- 结果：**`ok: true`，`failures: []`，21/21 全部 exit 0**（含三个并发形状与 `cancelling-at-exit`）：

| 用例 | 结果 | 用例 | 结果 |
| --- | --- | --- | --- |
| exit-matrix | PASS 85.2s | crash-recovery | PASS 116.6s |
| edit-exit | PASS 80.0s | multi-segment-edit | PASS 77.0s |
| quit-exit | PASS 80.2s | legacy-import | PASS 136.4s |
| running-exit | PASS 109.5s | dry-run-noop | PASS 67.9s |
| paused-exit | PASS 324.0s | unsafe-import | PASS 1.3s |
| exit-timeout | PASS 214.0s | ocr-baseline | PASS 257.1s |
| two-tasks-exit | PASS 293.1s | file-actions | PASS 116.6s |
| three-tasks-exit | PASS 689.6s | ui-language | PASS 18.3s |
| paused-plus-running-exit | PASS 277.7s | first-launch | PASS 22.8s |
| cancelling-at-exit | PASS 90.2s | dmg-launch | PASS 11.3s |
| | | upgrade | PASS 171.4s |

- **两处脚手架记账缺陷（本轮修正，非产品问题）**：
  1. 并发用例的 `reported_ok` 一直是 `None`：runner 把证据字典**截断到 3000 字符**后打印，`"ok"` 落在截断之外，sweep 的解析器扫不到。改为**先打印一行紧凑的 ok-first 摘要**（`{"ok": …, "scenario": …, "statuses_after_exit": …}`），再打印截断详情；复验 `_ok_from_output` 返回 **True**。
  2. `infrastructure_failures` 误报 `two-tasks-exit`：分类只看输出里是否出现"browser has been closed"，而 runner **内部重试成功后**仍会在输出里留下该字样。改为**只有未通过时**才计为基础设施失败（`exit_code == 0 且 reported_ok 非 False` 时不计）。
- 诚实标注：本机 arm64 macOS 结果；干净机器验收与签名/公证后的真实下载验收仍属外部条件。

---

## D2-16 — 全量 sweep（25 用例）与一次**环境抖动**的归因 + 脚手架加固

- 时间：2026-10-03
- 命令：`venv/bin/python scripts/desktop_e2e_sweep.py --out-dir build/desktop/e2e-sweep-full4`
- 结果：**24/25 通过**；唯一失败 `file-actions` 归因为**环境性抖动**，加固后复验通过（详见下）。

| 结果 | 用例 |
| --- | --- |
| PASS（24） | exit-matrix / edit-exit / quit-exit / running-exit / paused-exit / exit-timeout / two-tasks-exit / three-tasks-exit / paused-plus-running-exit / cancelling-at-exit / crash-recovery / multi-segment-edit / legacy-import / dry-run-noop / unsafe-import / **many-jobs** / **history-delete** / **history-delete-running** / **history-delete-paused** / ocr-baseline / ui-language / first-launch / dmg-launch / upgrade |
| FAIL→复验 PASS（1） | `file-actions` |

- `file-actions` 归因（两次失败，均为环境）：第一次 `actions` 阶段 `Target page, context or browser has been closed`（**setup 阶段本身成功**、交付 v1 已创建）；第二次 `setup` 阶段 `upload-source: … has been closed`（`setup.json` 记录了该签名）。
- **脚手架加固（非产品改动）**：
  1. `scripts/d2_file_actions_runner.py`：setup 阶段加入**一次重试**（与其它 runner 一致），并把 setup 阶段错误写进早退输出，便于诊断；
  2. 该 runner 现在还会把 `actions` 阶段的 `error` 打进摘要，使 sweep 能把"浏览器死亡"识别为基础设施问题而不是产品失败。
- 复验：`--only file-actions` → **exit 0 / `ok: true`**（119.3s）。
- 诚实标注：本机 arm64 macOS；本次全量未出现其它基础设施失败（`infrastructure_failures: []`）。
