# Folith Qt 桌面端 — Beta 状态

单一进度清单（复用 `docs/qt-spike/evidence-log.md` 作为证据日志，不重复造第二份证据表）。
每轮只改本文件的"当前阶段 / 状态 / 证据 / 阻断 / 下一单元"。

- 更新日期：2026-10-05（Beta 收口 R62：AX 崩溃已复现 + 定性上游缺陷 + 删除无效开关）
- 阶段：**S1–S8 完成；进入 Beta 收口（等待人工验收）**
- **当前结论（§7）：`ENGINEERING_COMPLETE — REQUIRED_ACCEPTANCE_PENDING`**——人工强制项通过前**不得**声明 `PRIVATE_BETA_READY`

- 路线：Python + PySide6（Qt Widgets）；Windows 保留适配与实机验收要求，本轮不宣称支持
- 架构决策：**GO — Qt Widgets / macOS arm64**（见 [s8-decision.md](s8-decision.md)）
- 本机验收环境：Apple Silicon（arm64）/ macOS 27.0.1 / Python 3.11.3 / PySide6 6.11.2 / Qt 6.11.2
<!-- status-identity:begin -->
- 候选身份：`app_version=0.5.0`、`build_id=git:5ad40a0`、`worktree_dirty=false`、PyInstaller 6.22.3、built_at `2026-10-07T06:18:12+00:00`
<!-- status-identity:end -->
- **声明最低系统：macOS 15.0**（实测原生闭包上限；**不支持** 13/14，不声称支持）

### 最终报告摘要（截至 R57）

| 维度 | 结果 |
| --- | --- |
| 工程实现 | §6 要求的 A/B/C/D 主流程全部实现；本轮循环内修复 **40+ 处真实缺陷**（含数据损坏级：交付预览承诺门禁会拒绝的事、批次错位防护、列表可能指向错任务） |
| 机器可验证验收 | **PASS**：166 项用例、20 步验证链（`bash scripts/qt_beta_chain.sh`）、155 文件全量回归（154 通过；唯一失败为既有 brand 用例：命中他人未跟踪文件 + 本仓 Qt 客户端 `dialogs.py` 一处注释，见 E180）、文档一致性审计（用例数/候选身份/最低系统/结论字符串/**相对链接是否断**自动核对）、重复定义静态检查（全部模块） |
| 人工强制项 | **0/7 完成**（本轮 computer use 回传：C.2′ → **抓到真缺陷并已修复**；A.8′ 连接 PASS / 任务链被 AX 崩溃阻断；C.12 空闲 PASS / 运行中未验证；A.1′、A.7″、B.1、D.9 见下方明细）。详见 [computer-use-acceptance-2b74924a-2026-10-05.md](computer-use-acceptance-2b74924a-2026-10-05.md) |
| 公开分发 | **DEFERRED**：Developer ID / 硬化运行时 / 签名 / 公证 / staple / 真实下载——属另行授权范围，不阻塞受邀 Beta |
| 技术阻断 | **无**（不依赖人工项的部分已全部收口；剩余项均为"只有人能判定"） |
| 证据规模 | 证据日志 **202 条**（E1–E202）、66 个轮次小节（S1–S8 + Beta R2–R71） |

## 1. 入口与产物

| 项 | 值 |
| --- | --- |
| 源码入口 | `venv/bin/python qt_client.py`（或 `python -m folith.qt`） |
| 冻结应用 | `dist/qt-desktop/Folith.app`（375 MiB / 393 MB，bundle id `dev.folith.qt`） |
<!-- status-dmg:begin -->
| DMG | `dist/qt-desktop/Folith-Qt-0.5.0-macos-arm64.dmg`（159 MiB / 166 MB，sha256 `ce8bc23e…c0d4`） |
<!-- status-dmg:end -->
| 构建中间物 | `build/qt-desktop/`（build record、`native_min_os_*.json`、`release-manifest.json`、`dmg_record.json`） |
| 发行说明 | [release-notes.md](release-notes.md)（生成式，`--check-notes` 可检测过期） |
| 人工验收包 | [manual-acceptance.md](manual-acceptance.md)（详细步骤）｜[acceptance-quickstart.md](acceptance-quickstart.md)（**一页速览**，含回传模板）——两份的候选身份都由 `scripts/qt_acceptance_kit.py --write` 生成并进链校验 |
| **§6 覆盖矩阵** | [acceptance-matrix.md](acceptance-matrix.md)（逐条：状态 / 证据 / 验证方式） |
| **验收包（发给测试者）** | `dist/qt-beta-handoff/`（DMG + 生成式 `README-先读我.md` + 三份文档 + `manifest.json`）；`venv/bin/python scripts/qt_acceptance_bundle.py --write/--check`。**重建候选后必须重跑 --write** |
| 旧壳基线（保留对照） | `dist/desktop/`、`venv/bin/python gui.py --legacy` |
| 用户数据目录 | `~/Library/Application Support/Folith/data`（与旧壳同一位置，升级可见旧任务） |
| 日志目录 | `~/Library/Logs/Folith`（`qt-client.log`） |

产物身份检查（每轮必跑，缺失即失败）：

```bash
venv/bin/python scripts/qt_artifact_check.py --out build/qt-desktop/artifact-check.json
```

复现命令：

```bash
venv/bin/python -m pytest tests/qt_acceptance_kit_test.py tests/qt_delivery_content_test.py \
    tests/qt_extraction_error_test.py tests/qt_client_style_test.py \
    tests/qt_client_adapter_test.py tests/qt_client_window_test.py \
    tests/qt_release_plumbing_test.py tests/qt_spike_test.py -q       # 166 项

# 完整验证链（20 步：构建 → 门禁 → 文档一致性 → 用例 → 端到端探针）
bash scripts/qt_beta_chain.sh tmp/qt-beta/chain
bash scripts/qt_beta_chain.sh tmp/qt-beta/chain --probes-only   # 只跑端到端探针（9 步，不重建候选）
bash scripts/build_qt_desktop.sh            # .app（含实测最低系统复核）
bash scripts/build_qt_dmg.sh                # DMG + 镜像内身份复核
venv/bin/python scripts/qt_ocr_baseline_runner.py --out /tmp/qt-ocr \
    --frozen-app "$PWD/dist/qt-desktop/Folith.app/Contents/MacOS/Folith"
venv/bin/python scripts/qt_dmg_probe.py --dmg dist/qt-desktop/*.dmg --out /tmp/qt-dmg
venv/bin/python scripts/qt_release_checks.py --write && \
venv/bin/python scripts/qt_release_checks.py --write-notes && \
venv/bin/python scripts/qt_release_checks.py --check-notes
```

## 2. Beta 必需流程状态

| # | 流程 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | 导入 PDF/DOCX → 建任务（现有格式目录）；取消不产生半成品 | PASS | 适配层与窗口级用例；E3/E4；E22（面板取消不写任何东西） |
| 2 | 首次启动模型配置与连接状态、可理解失败、可重试；项目归属（**含已有任务改归属**）与项目筛选可用 | PASS | `EngineConfig` 往返 + 断连→重试 + 项目归属（含孤儿防护）+ 项目筛选 + **E51/E54** |
| 3 | 小型任务翻译（受控端点，真实 HTTP 传输）；进度/错误/恢复 | PASS | E3/E4/E5；`build_job_runtime_view` |
| 4 | 运行期间编辑、切换、取消；暂停/恢复按既有契约 | PASS | 暂停/恢复窗口用例；E8 |
| 5 | 多段工作台（CAT 网格 + 检查器）：原文/译文/状态/QA、编辑、提交、下一段、复制原文、子集审校、**重译本段、排除/恢复段落、搜索与状态筛选**；后台刷新/网格重建不打断输入 | PASS | E3；E24、E25；E35；E44；E45；**E57–E62（含三处真实缺陷修复：重译静默失效、网格重建空白、筛选静默不生效）** |
| 6 | 最小术语/TM 传入核心，不丢弃既有项目配置 | PASS | 术语/TM 传递用例 |
| 7 | 交付门禁 → **按用户声明的格式**生成；中文与空格路径；取消不写文件 | PASS | E3/E4/E5；E22；**E33/E34（声明即产出、已有任务选择优先）** |
| 8 | 真实 PDF + DOCX 全流程、重开读回、真实模型服务 | 部分 | 冻结产物全流程 PASS；取消后可恢复且未误标完成（用例）；**SIGKILL 后重开→恢复→交付 PASS（E30）**；**真实服务 = REQUIRED MANUAL** |
| 9 | 200 段 + 约 10k 字符长文本 | PASS | E9 |
| 10 | 旧数据目录接入（保留来源、不覆盖）；**真实历史任务可打开** | PASS | E10、E27（50/50 打开、交付资产可重建、源目录未改动） |
| 11 | 单实例 | PASS | E11 |
| 12 | 退出：新装首页 / 已有任务首页 / 未失焦编辑 / 运行中 / 暂停 / 两个活跃 / 取消中；⌘Q 同一规则；无残留进程与锁 | PASS | E7、E11b、E11c、E11d、E21、E23 + 窗口级用例 |
| 12b | 源文件写入原子性（并发读写不产生空源） | PASS | `save_source` 原子写 + 客户端空源拒绝；E29 |
| 12c | 受控端点并发可靠（不因 backlog 误报失败） | PASS | E29（backlog 5→256，10/10 整套通过） |
| 12d | 替换/升级应用不删数据 | PASS | E31（覆盖安装后任务数不变） |
| 12e | 全量回归（核心接口变更后） | PASS（2 项既有/已修失败已分别归因） | E32：1962 项通过；red-team 失败由本轮引入已修；brand 失败在他人未跟踪文件 |
| 12f | 交付格式选择真正生效（无无效入口） | PASS | E33/E34 |
| 12g | 界面无其他无效控件（静态复查） | PASS | R8 两次扫描零命中；E35 |
| 12h | 冻结产物自述诊断（版本/构建/dirty/支持边界/工具） | PASS | E37（含 LibreOffice NOT_RUN、无凭据值） |
| 12i | 超时强退分支：继续等待 / 强制退出，且强退只承诺已落盘结果 | PASS | E38 |
| 12j | 重复退出（⌘Q 连按 / Dock 退出同一事件）只执行一次 | PASS | E39 |
| 12k | 项目删除：确认、运行中保护、仍含任务拒删、删除前备份、系统工作区不可删 | PASS | E52/E55（**此前为 N/A**，本轮补上入口并验证保护） |
| 12l | 项目归档/重命名与统计 | PASS | E53 |
| 12m | 段落级操作（重译/排除/恢复）与筛选真的生效 | PASS | E57–E62 |
| 12n | 全量回归（改 core/delivery/job_repository 后各一次） | PASS（未引入新失败） | E63、E67：153 文件 152 通过，唯一失败为既有 brand 用例（他人文件） |
| 12o | 界面状态色调/按钮可见性跟随核心契约 | PASS | E64–E66（修掉"偏离显示成成功绿"、"停滞无取消按钮"、"死掉的继续按钮"） |
| 12p | 首页时间列与阻断项计数跟随核心定义 | PASS | E69、E70（修掉"更新时间永远是—"与"已解决阻断项仍被计数"） |
| 12q | 保存设置不重置未展示字段；草稿过期可识别/可放弃；显式动作后编辑器刷新 | PASS | E71–E75（三条"静默丢东西"路径） |
| 12r | macOS 菜单角色/⌘W；对话框不阻塞脚本化运行；显式动作立即刷新 | PASS | E76–E78 |
| 12s | 等待框留出路（在后台运行）、关窗不留残窗、自动化判定单一 | PASS | E79、E80 |
| 12t | 随包产物视觉确认；诊断页可读性；标签页左对齐 | PASS | E81、E82 |
| 12u | 交付文件内容校验（读回逐项核对，进链） | PASS | E83、E84 |
| 12v | 保存交付文件：完整写出、冲突先问、缺文件拒绝 | PASS | E85、E86 |
| 12w | 导出副本同规则；诊断包字段齐全且不弹原生面板 | PASS | E87–E89 |
| 12x | 人工验收清单与当前界面/候选一致（身份生成 + 进链校验） | PASS | E90–E93 |
| 12y | 坏输入（损坏/加密/空）的失败原因可理解 | PASS | E94–E96 |
| 12z | 运行期间提交人工修改：被核心拒绝（不覆盖）、原因明确；排除后编辑器跟着段落走 | PASS | E97、E98 |
| 12aa | 两个任务同时运行、取消隔离、导出副本内容与诚实性 | PASS | E99、E100 |
| 12ab | 保存并进入下一段 / 复制原文到译文；旧数据接入不覆盖；所有原生面板入口都有自动化守卫 | PASS | E101–E104 |
| 12ac | 损坏任务列出而非隐藏；打开时给出可理解失败 | PASS | E105、E106 |
| 12ad | 交付后偏离如实显示并可再交付 v2；失败与生命周期进日志（不落凭据） | PASS | E107–E109 |
| 12ae | 交付预览与真实门禁一致（审校未就绪时按钮禁用并说明原因） | PASS | E110、E111 |
| 12af | 干净用户首跑（全新 HOME，只创建预期目录、不依赖既有配置） | PASS | E113（已进链） |
| 12ag | 输入法结构性预检（不替代人工 C 步） | PASS | E115 |
| 12ah | 请求挂住时：界面可用、可取消、退出有上界且走显式强制路径 | PASS | E116（已进链） |
| 12ai | 提供商错误（401/402/404/429/500）映射为可执行中文，不泄露原始 JSON | PASS | E118、E119 |
| 12aj | 批次响应不合格整批拒绝（不错位、不写半截、可恢复） | PASS | E120、E121 |
| 12ak | 文档（状态/矩阵/验收清单/证据日志）与产物、实测、用例数一致 | PASS | E122、E123（审计进链且可证伪） |
| 12al | 首页批量操作（多选删除/改归属，一次确认、运行中任务仍受保护） | PASS | E124、E125 |
| 12am | OCR「何时识别」真的生效（off 不 OCR、force 强制 OCR），标签与核心取值对齐 | PASS | E126、E127 |
| 12an | OCR 语言选项来自核心注册表；界面标签不会泄漏成配置取值 | PASS | E128 |
| 12ao | 新任务默认值（学术报告/术语/TM/画像/清理/风格/切分）落到任务状态；已有任务不被覆盖 | PASS | E129 |
| 12ap | 独立审校模型：同提供商沿用正文地址、真的被调用 | PASS | E130、E131 |
| 12aq | 表驱动不变量：界面每个字段都以核心认识的键名到达（整类缺陷钉死） | PASS | E132 |
| 12ar | 静默失败清理（日志写失败退回 stderr；退出过滤器失败留痕）；交付预览 419 段 4–7ms | PASS | E133–E135 |
| 12as | 任务列表行标识以目录名为准（缓存串号也不会指向错任务）；复制目录按正常任务列出 | PASS | E136–E138 |
| 12at | 接入旧数据目录后行标识 = 目录名（含串号 summary 的真实场景） | PASS | E139 |
| 12au | 一页验收速览（最短操作步骤 + 回传模板）；三份文档身份生成与校验 | PASS | E140–E142 |
| 12av | 切换任务后交付面板显示当前任务的资产（节流键含任务标识） | PASS | E143 |
| 12aw | 跨任务状态不残留（搜索词清空、状态筛选保留、终态备忘重置） | PASS | E144 |
| 12ax | 进度显示跟随核心（翻译阶段用核心的批次进度，不再整段空） | PASS | E145 |
| 12ay | 过期缓存不能决定首页行字段（按状态重建）；审校阶段进度同样正常 | PASS | E146、E147 |
| 12az | 审校芯片与交付门禁同一结论（收据失效时显示"需重新审校"） | PASS | E148 |
| 12ba | 「必须处理」芯片与「只看必须处理」筛选同规则（且删掉同名死代码） | PASS | E149 |
| 12bb | 106 个模块无重复定义（后者静默胜出）；检查器可被证伪 | PASS | E150、E151 |
| 12bc | 验证链脚本化（20 步全绿，可复现）；最终报告摘要；文档数字受审计 | PASS | E152–E154 |
| 12bd | 交付页自足（已验清单 + 复核命令）；人工项条数与矩阵一致（7 强制 + 6 PARTIAL） | PASS | E155–E157 |
| 12be | macOS 27 AX 扫描崩溃（平台级）：界面结构变更收敛 + 用探针**稳定复现**并定性到上游 QTBUG-149612 | PASS（收敛，非根治；**无可用应用侧开关**） | E158–E162；**E163–E168** |
| 12bf | 删除无效的 AX 开关（`FOLITH_QT_DISABLE_ACCESSIBILITY`），并钉住「不得再提供该开关」 | PASS | E165、E166、E169 |
| 12bg | 验证链可重入：同一个输出目录连跑两次都 20/20（带护栏的清理，危险路径直接拒绝） | PASS | E170 |
| 12bh | 最终冻结候选上复现同一 AX 崩溃（如实披露，不是候选可修的问题） | PASS（复现即证据） | E171 |
| 12bi | 硬崩溃的编辑丢失上界：草稿跨进程可读回，最多丢自动保存窗口（1.5s）内的输入 | PASS | E172 |
| 12bj | AX 崩溃的后果：任务数据指纹逐项不变、无残留进程、同一数据目录可重开存活 | PASS | E173 |
| 12bk | 后果探针可证伪：没有崩溃时明确 FAIL（并修掉「菜单项被当成任务行」的匹配缺陷） | PASS | E174 |
| 12bl | §6.B Unicode 保真到交付文件：混排/RTL/组合字符/emoji 经交付门禁后码点级相等 | PASS | E175 |
| 12bm | 纯键盘可达性（修复）：表格获得焦点时当前行成为选中行，按钮可用且 Tab 可达 | PASS（修复前实测不可达） | E176 |
| 12bn | 键盘输入到达译文框、草稿落盘、Tab 可离开编辑器 | PASS | E177 |
| 12bo | 主窗口最小高度 902→621pt（小屏装得下），缩放/全屏往返后仍可用且检查器可滚动 | PASS（修复前 902pt） | E178 |
| 12bp | 产物内容核对：两个修复确实进了冻结候选（解 PYZ 字节码，非 mtime） | PASS | E179 |
| 12bq | Cmd 快捷键按真实按键触发正确动作且无歧义（不再只查"注册过"） | PASS | E181 |
| 12br | 首页双击任务行打开被双击的那一个任务 | PASS | E182 |
| 12bs | 可发给测试者的验收包（生成式 README + 逐文件 sha256），篡改/过期会被 --check 抓出 | PASS | E183 |
| 12bt | 文档相对链接进链校验（40 个链接零断链；检查可证伪；tmp 断链只告警；代码块内的示例不计） | PASS | E184–E186 |
| 12bu | 端到端探针可在不重建候选的前提下复核（`--probes-only`）：当前候选 9/9 全绿 | PASS | E187、E188 |
| 12bv | R64 布局改动后的冻结产物视觉复核（无破相；检查器单条主题化滚动条；取舍已记录） | PASS（目视，非像素门禁） | E189 |
| 12bw | **C.2′ 真缺陷：退出流程走完却不退出**（⌘Q/⌘W/菜单退出/Dock 退出四条入口；窗口一直开着、进程不退）——两个面都已修：① 嵌套 close 被外层已 ignore 的事件吞掉；② 模态对话框（设置）开着时把退出拖住 | PASS（修复前实测四条入口全不退出） | E191、E192、E198 |
| 12bx | 退出路径端到端探针：冻结候选上真实 ⌘Q 与 Apple Event quit 必须让进程退出且日志以 `qt client exited` 收尾 | PASS（修复前 FAIL rc=1 → 修复后 PASS） | E193 |
| 12by | 用户 computer use 回传处理：两次 AX 崩溃的新触发面（导入回首页、点「开始翻译」）+ 真实服务连接 PASS + 睡眠/唤醒空闲 PASS + 安装/导入/取消的实际结果 | 已记录（C.2′ 见 12bw） | E190、E194–E197 |
| 12ca | **UI/UX 对齐旧 Web 界面**（第一轮）：图标系统 33 个、字号/控件高度/圆角/间距成体系、QSS 重写（分段控件/提示条/KPI 卡/下划线标签/行委托）、首页重做（侧栏 + 页头 + KPI + 卡片表格 + 空状态）、工作台页头/工具栏/检查器分小节 | PASS（机器侧；观感由用户判断） | E199–E202 |
| 12bz | 链在本轮工作区状态下有两处红，**归因于他人并发编辑**（`core.py` 16:43、`document_understanding.py` 16:41 比 16:33 的构建更新 → 产物身份要求重建；OCR 缺包用例报错文件正是他人刚改的那个） | 外部条件（待其稳定后重建并重跑完整链） | E197 节末说明 |
| 13 | 冻结 `.app` + DMG；从 DMG 安装副本可跑主流程；LaunchServices 可启动 | PASS | E13/E15/E20 |
| 14 | 扫描件 OCR `eng/chi_sim/ara`（无 Homebrew） | PASS | E14 |
| 14b | 缺语言包**准确报错**、不静默改用英语 | PASS | E14b（否定用例：失败并点名 `deu`，未产出英文文本） |
| 14c | 原生闭包架构一致（arm64 完整） | PASS | E18（449/449 含 arm64；构建门禁） |
| 14d | 诊断包不含正文 / 凭据 | PASS | E19 |
| 14e | Cmd 快捷键无歧义、⌘Q 走统一退出规则 | PASS | E23 |
| 14f | 构建门禁（最低系统/架构/Info.plist）确实执行 | PASS | E26 |
| 14g | 419 段真实运行的界面响应 | PASS | E28（产品侧最大间隔 37.5ms） |
| 14i | 视觉：品牌 token 落地、首页重做、三套主题、工作台 CAT 布局、设置/诊断对话框、**项目对话框** | PASS（增量 1–4） | E40–E50、E56；主行动按钮/色卡均取自 token |
| 14j | 产物身份检查（缺失即失败） | PASS | `scripts/qt_artifact_check.py`；用例覆盖"不存在→失败" |
| 15 | 发行清单/说明与产物身份一致 | PASS | E17 |
| 16 | 真实中文 IME / 组合输入 | REQUIRED MANUAL | 见人工验收包 C |
| 17 | 系统文件面板（导入/保存的确认与取消） | REQUIRED MANUAL | 见人工验收包 D |
| 18 | 睡眠 / 唤醒 | REQUIRED MANUAL | 见人工验收包 E |
| 19 | 真实模型服务任务链 | REQUIRED MANUAL | 见人工验收包 F |
| 20 | 干净 Mac 普通用户全流程 | REQUIRED MANUAL | 见人工验收包 A |
| 21 | 签名 / 公证 / 公开发布 | DEFERRED（外部条件） | 仅阻塞公开分发 |

自动化/冻结证据与人工项分开记录：以上 PASS 均为**自动化或冻结产物**证据，不代表人工项已通过。

## 3. 原问题收口（MAC-01…05，见 `docs/desktop-macos-review-backlog.md`）

只在新证据出现时标 RESOLVED；Qt 已替换的旧壳问题标 LEGACY ONLY / SUPERSEDED 并注明新路径结果。

| ID | 原问题 | 本轮状态 | 新路径证据 |
| --- | --- | --- | --- |
| MAC-01 | 首页无任务时正常关闭被拒绝 | **RESOLVED（Qt 路径）**；旧壳分支保留 LEGACY ONLY | `exit_report` 对空闲任务返回 `safe_to_exit=true`；`test_idle_window_exits_without_a_prompt` 与 E15 退出均通过；旧 `folith/desktop/exit_flow.py` 的 WebView 分支未改，旧缺陷保留历史状态 |
| MAC-02 | 声明 macOS 13 与随包 OCR 最低版本不一致 | **RESOLVED（Qt 候选）** | 实测原生闭包上限 `minos 15.0`（449 个原生文件）；`Info.plist` / 清单 / 发布说明一致声明 **15.0**；明确不声称支持 13/14（s8-decision §4） |
| MAC-03 | CI 解释器 / 桌面依赖 / 说明步骤未接通 | **RESOLVED（本地接线）— 远端未运行** | 新增 `qt-macos` job：自建 venv、安装真实依赖、按"构建→DMG→清单→生成说明→校验说明"顺序；本机按同样命令全部通过。**不宣称远端 CI 已通过**（无远端授权） |
| MAC-04 | 发布清单版本号截断、把范围当 pinned | **RESOLVED（Qt 发行链）** | 新脚本按 `len("Version: ")` 提取；`tests/qt_release_plumbing_test.py` 用真实样本 `Version: 1.64.0` 断言不截断；发布说明明确区分"范围声明"与"实际解析并随包版本" |
| MAC-05 | D0 GO 与人工验收未完成的口径混用 | **RESOLVED（文档口径）** | [s8-decision.md](s8-decision.md) 明确 `GO — Qt Widgets / macOS arm64` 并单列 5 项 REQUIRED MANUAL；自动化/冻结证据与人工项在本文与证据日志中分开记录 |

## 4. 阻断与缺口

- **平台级风险（非本候选独有，且应用侧无解）**：macOS 26/27 上外部辅助功能扫描
  （AI "Computer Use" / VoiceOver / 窗口管理器 AX 客户端）遍历界面会让 Qt 平台插件崩溃
  （栈顶 `libqcocoa` + `NSAccessibilityAttributeAccessorInfo`）。上游根因 **QTBUG-149612**
  （synthesized table elements 会删掉父级 table 的 accessibility interface；影响 Qt
  6.11.0–6.11.2，截至 2026-10-05 无修复版本，Gerrit 765434 评审中），calibre/Qt Creator/
  Anki/FreeCAD 同样如此。**R62 已用 `scripts/qt_ax_scan_probe.py` 稳定复现（3/3，另加对照
  2/2）**，并实测**没有可用的应用侧开关**：`QT_ACCESSIBILITY=0` 在 Qt 6.11 完全无效、
  `QAccessible.setActive(False)` 会被系统在 AX 客户端接入时重新激活（每 100ms 重申也照崩）。
  因此 R61 的 `FOLITH_QT_DISABLE_ACCESSIBILITY=1` **已删除**——不能给测试者一个不存在的
  安全感。**验收请用人工键鼠、不挂 AX 代理**（人工路径不受影响）。2026-10-05 的人工验收
  因此未到达译文框（输入法项被阻断），详见
  [computer-use-acceptance-2026-10-05.md](computer-use-acceptance-2026-10-05.md)。
- 其余无技术阻断。剩余必需项依赖人工/外部条件（第 16–20 项）或外部凭据（签名/公证）。
- 远端 CI 未运行（无远端授权）：`qt-macos` job 已按同一解释器环境接线，本机命令可复现，**不宣称远端 CI 通过**。
- 未提供项目删除入口（只提供任务删除，含运行中保护与明确确认）；不为了补旧探针回到 Streamlit 调试。
- Windows / Intel Mac / Windows ARM：未验收，不宣称支持。
- LibreOffice 未随包：渲染 QA 保持 `NOT_RUN`，不以缺少它阻塞可用 DOCX 的 Beta。

## 5. 下一单元（本循环已收口，等待人工验收）

0. **先决条件（重要）**：本轮构建（16:33）之后，**另一个 session 仍在改** `core.py` / `folith/document_understanding.py` / `folith/ui/pdf_view.py`，所以产物身份检查会报"需要重建"，且 `test_real_scan_with_missing_pack_reports_the_pack` 当前是红的（报错就在他人刚改的文件里）。**等共享工作区稳定后**：重建候选 → 重跑完整 20 步链 → 重跑 `scripts/qt_exit_paths_probe.py` → 重生成验收包（`scripts/qt_acceptance_bundle.py --write`）。
1. **候选已就绪**：`dist/qt-desktop/Folith-Qt-0.5.0-macos-arm64.dmg`（身份见 §1；未签名未公证）。
   发给测试者的最小集合：[acceptance-quickstart.md](acceptance-quickstart.md)（一页速览 + 回传模板）
   ＋ [manual-acceptance.md](manual-acceptance.md)（详细步骤）＋ 本文件（状态与证据入口）。
2. **机器侧已无剩余工作**：166 用例、20 步验证链（`bash scripts/qt_beta_chain.sh`）、155 文件回归、
   文档一致性审计、重复定义检查——全部通过；终局核对见证据日志 R60。
3. **唯一剩余路径 = 7 条强制人工项**（A.1′、A.7″、A.8′、B.1、C.2′、C.12、D.9）。
   请用**人工键鼠**完成——不要挂 AX 代理/Computer Use：那是上游 QTBUG-149612 的触发条件，
   与本候选无关，且实测没有任何应用侧开关能挡住它。回传后：
   **需 7 条强制项全部通过后**才可把结论改为 `PRIVATE_BETA_READY`；有失败项则我按失败项
   定位修复后出新候选重验。
4. 公开分发（Developer ID / 签名 / 公证 / staple / 真实下载）等待外部条件与另行授权。
5. **R63 已把崩溃的后果说清楚**（可对测试者直说）：挂 AX 代理会崩进程，但**不会弄坏任务**——
   崩溃前后全部任务的指纹（行数/状态/人工修改/交付资产/原文与译文哈希/文件大小）逐项一致、
   无残留进程、同一数据目录可重开继续用（E173）；代价只有最后一次自动保存（1.5s）之后的输入（E172）。
   两个探针可随时复跑：`scripts/qt_ax_scan_probe.py`（复现崩溃）与
   `scripts/qt_ax_crash_aftermath_probe.py`（后果检查，含可证伪的负控 E174）。
6. **R64 修了两个 §6.B 的真实问题**（已重建候选，身份见 §1）：纯键盘用户此前**永远按不动**
   「打开任务」（灰按钮还会被 Tab 焦点链跳过）→ 现在表格获得焦点时当前行成为选中行；
   主窗口强制最小高度 **902 → 621pt**（检查器套滚动区），小屏（1440×900 可用约 875pt）装得下、
   控件仍可达。证据 E176–E179；剩下的是"手感/观感"人工项。
7. **R63 另补了 §6.B 的 Unicode 保真证据**（E175）：中英阿混排 + RTL 控制符 + NFD 组合重音 +
   ZWJ emoji/旗帜经"人工提交 → 无缓存重开 → 真实交付门禁 → 快照 → 导出"后，`translation.docx`
   与 `bilingual.docx` 里**码点级完全相等**（用例自带非空断言，证明夹具对归一化敏感）。
