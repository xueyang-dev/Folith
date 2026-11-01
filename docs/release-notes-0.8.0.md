# Folith 0.8.0 发布说明与验收矩阵

状态：**已发布**（2026-11-01），源码 tag `v0.8.0`，发布基于候选 `release/0.8.0` @ `8e361cdf`（之前的候选 `5be4fbae`、`76e2f269`、`52279567`、`ae20a3db`、`829edae5` 作废）。发布时 macOS 原生验收和下表标「待人工」的项没有全部完成，已在各行与「已知限制」里如实写明。
本页不沿用 0.7.2 的通过结论。

## 版本变化

见 [CHANGELOG](../CHANGELOG.md) 的 0.8.0 条目：审校页改版与概念级术语决定、两级术语管理、文档结构与大纲、翻译范围排除、非 PDF 来源的原文定位、原排版译文转正、21 种翻译语言与法语 / 德语界面、阿拉伯语 RTL、应用内更新与稳定性修复。DOCX 原格式往返（#26）新增可选交付项「原格式译文」，默认关闭，尚未做原生人工验收。

## 发布前核对表

| 项目 | 状态 |
| --- | --- |
| #235、#237、#238、#239、#240 合入 main | 已合入（2026-10-28） |
| 冻结提交上的 CI | 通过。`release/0.8.0` @ `8e361cdf`（与 main `f54d7a70` 文件树完全相同），[Release Validation run 41286021237](https://github.com/xueyang-dev/Folith/actions/runs/41286021237)：macOS / Windows 候选构建（含冻结主流程、DMG、安装包与发布检查）与 Linux Python 3.10 / 3.11 / 3.12、Windows 3.11 完整回归、Main Full Regression 全部通过。此前候选 `829edae5` 的验证在 Linux 3.11 上失败：`qt_pdf_tip_test.py` 卡在模态的「新变化」窗口里被超时杀掉，已在 #275 修复（见更新日志）；更早的 `ae20a3db` 通过属于侥幸（定时器竞争）。`release/0.8.0` 之前一轮的 macOS 候选构建曾有一次在「候选文档与隔离的 Qt 回归」一步卡到 60 分钟被取消，重跑通过，本轮没有再出现 |
| main 上的 Main Full Regression | 本轮在候选上通过。自 #264 起 main 上该工作流一直为红，原因是 `qt_pdf_tip_test.py` 在较慢的 Linux 机器上卡在「新变化」模态窗口；#272 加了单进程超时与线程栈输出，用它定位到根因，#275 修复。#275 合并后 main 上的该工作流已转绿（`cd610bf6`、`f54d7a70`） |
| 候选后新增改动的人工验收 | 待人工：审校队列分组与术语卡（#102）、本地网关与推理强度、取消进行中的请求、界面卡死记录、打开任务不等大纲（#260）、跳过零尺寸图片框后的 DOCX 交付（#271）。本机 offscreen 测试不能代替原生窗口验收。已知未解决：作者本机（macOS 27.0.1）上有两类界面线程原生崩溃——11,471 段 PDF 任务打开后约 3 秒、以及交付后刷新工作台时（崩溃栈落在 `qa_panel.py` 的 `rule_list.clear()`），离线复现不稳定，v0.7.2 与本版在同一脚本下各 4 次均未崩，未能判断是否为 0.8.0 引入 |
| 真实长文档交付验收（PDF / DOCX / EPUB / 旧任务升级 / 中断恢复） | 待人工 |
| Windows x64 安装版与便携版原生验收 | 已执行（用的是候选 `ae20a3db` 的构建，安装版与便携版均复现了问题 1）。除下面两个问题外，验收员没有报告其他失败：1. `Ctrl+Alt+P`（查看原位置）在 CAT 工作台里没有响应，点按钮正常（#276）；2. 审校队列的术语卡没有显示冻结译法、旧译法、受影响段数（#277）。两项都不阻止发布，记为已知问题。该构建不是最终候选 `8e361cdf`：二者的应用代码只差 #275 里脚本化运行的一处保护，真实使用的行为相同，但最终构建本身没有在 Windows 真机上再验收一遍 |
| macOS arm64 DMG 原生验收 | 未在最终候选的 DMG 上完成。作者用源码运行（候选 `ae20a3db` 之后的 main）试用过，期间出现过两次界面线程原生崩溃，原因未定位（见上一行「候选后新增改动的人工验收」） |
| #90 审校端到端（人工译文保护、问题处理、术语修改、交付门禁） | 待人工 |
| #242（DOCX 原格式译文桌面入口）、#250（0.8.0 验收记录）合入 main | 已合入（2026-10-28） |
| #188 / #189 验收与 Issue 更新 | 已关闭（2026-10-28），证据见 [0.8.0 验收记录](qt-beta/acceptance-0.8.0-2026-10-28.md) |
| #191 大纲真实窗口验收（macOS / Windows，2000+ 段文档在翻译进行中导航） | 待人工；offscreen 脚本 `scripts/acceptance/outline_191_offscreen.py` 已通过，不能代替 |
| DOCX 原格式译文（#26）原生 macOS / Windows 验收 | 待人工 |
| 资产 SHA-256 | 来自 run 41286021237 的构建记录（未签名，build id `git:8e361cd`）：<br>`Folith-Qt-0.8.0-macos-arm64.dmg` `4a82768581baaea4ca6c1649b294ef123a762210729a009d05bcb833448aef07`（161,564,911 字节）<br>`Folith-Setup-0.8.0-windows-x64.exe` `60fa8223007ff6c15e0452523a1ad218ae3fc1655ae04c627eae1aff89c74aba`<br>`Folith-Qt-0.8.0-windows-x64.zip` `ee7fa08c137077bd2bb0faf6af2e82129052b09cc003ad8cfc5efffb324eded0`<br>发布时须用 Release 页上传的文件重新核对 |

## 已知限制

- Windows：CAT 工作台里 `Ctrl+Alt+P`（查看原位置）没有响应，用鼠标点右侧的「查看原位置」按钮可以正常展开 PDF 原页面板（[#276](https://github.com/xueyang-dev/Folith/issues/276)）。
- 审校队列里的术语卡：Windows 验收中展开一张概念决策类的术语卡，只看到术语、命中次数与原文 / 当前译文对照，没有看到冻结译法、旧译法、受影响段数（[#277](https://github.com/xueyang-dev/Folith/issues/277)）。这些字段在过期译法类术语卡的代码里有渲染，是否只是两类卡的展示不一致，没有确认。
- 作者的 macOS 27.0.1 机器上出现过两次界面线程原生崩溃（打开 11,471 段 PDF 任务约 3 秒后；冻结交付完成后刷新工作台时），没有定位到原因，v0.7.2 与本版在同一离线复现脚本下各 4 次均未崩，不能判断是否由 0.8.0 引入。遇到闪退请在 Issue 里附上诊断包（关于 → 诊断）。
- 在连接编辑器里新建或保存 DeepSeek 系连接时，「思考」默认是「服务默认」，思考保持开启；要得到更新日志里「关闭思考」的提速，需要手动选「关闭思考（DeepSeek 系）」。升级前创建、没有记录思考选项的旧连接仍默认关闭思考。
- 保留原版式的 PDF 输出（仅 PDF 来源）在扫描版上只能尽力还原：译文盖在原页像素之上，底色取自局部采样，文字后面是照片类背景时无法精确还原；视觉模型给出的文字框常比实际文字行窄，质量很差、但自带文字层又通过质量门禁的扫描件不会走视觉理解。渲染器自带样本只覆盖了一页干净的扫描件，退化扫描和照片背景没有覆盖（见 `docs/original-layout-pdf-renderer-report.md`）。
- 保留原版式的 PDF 输出在有文字层的 PDF 上，页数、书签与插图保持，但验收报告观察到在部分 PDF 上段落可能被合并、标题位置偏移（离线桩译文、未用真实模型复测，我方未能复现），不要把它当作对任意 PDF 的“原样排版”承诺。
- macOS 安装包的最低系统为 15.8.1（0.7.x 为 15.7.5）：随包 Tesseract 及其依赖在 CI 构建环境升级后要求 15.8.1，实测闭包与 Info.plist 一致。macOS 15.7.x 的用户需要先升级系统。
- macOS 包未签名、未公证；Windows 包未签名。首次放行、quarantine、安装升级须在最终候选上人工复核。
- 自动化（CI、offscreen、mock 平台）不能代替真实 Cocoa / Windows 原生窗口验收。
- 随包 OCR 基线为 Tesseract eng / chi_sim / ara；不随包 PaddleOCR 或 LibreOffice。
- Linux 不提供桌面安装包；Windows 10、Windows ARM64、Intel Mac 无原生验收结论。
- 新增翻译文案需要独立的语言审校。
- 三个依赖时序的测试在 CI 中各出现过一次偶发失败、重跑通过：`qt_notifications_test.py::test_card_uses_same_retry_and_stall_clock_as_workbench`（Windows）、`review_scheduler_test.py::test_overlap_with_simulated_latency`（Windows）、`qt_motion_test.py::test_reference_overflow_scrolls_without_compressing_term_rows`（Linux 3.10）。不涉及本版改动，待后续去掉对墙钟与布局时序的依赖。
