# Folith 0.8.0 发布说明与验收矩阵

状态：**候选，未发布**。当前候选为 `release/0.8.0` @ `ae20a3db`（2026-10-31 冻结，之前的候选 `5be4fbae`、`76e2f269`、`52279567` 作废），tag 在发布时补入。
本页不沿用 0.7.2 的通过结论。

## 版本变化

见 [CHANGELOG](../CHANGELOG.md) 的 0.8.0 条目：审校页改版与概念级术语决定、两级术语管理、文档结构与大纲、翻译范围排除、非 PDF 来源的原文定位、原排版译文转正、21 种翻译语言与法语 / 德语界面、阿拉伯语 RTL、应用内更新与稳定性修复。DOCX 原格式往返（#26）新增可选交付项「原格式译文」，默认关闭，尚未做原生人工验收。

## 发布前核对表

| 项目 | 状态 |
| --- | --- |
| #235、#237、#238、#239、#240 合入 main | 已合入（2026-10-28） |
| 冻结提交上的 CI | 通过。`release/0.8.0` @ `ae20a3db`（与 main `cd576ae5` 文件树完全相同），[Release Validation run 41221050877](https://github.com/xueyang-dev/Folith/actions/runs/41221050877)：macOS / Windows 候选构建（含冻结主流程、DMG、安装包与发布检查）与 Linux Python 3.10 / 3.11 / 3.12、Windows 3.11 完整回归全部通过。macOS 候选构建首轮在「候选文档与隔离的 Qt 回归」一步卡住，跑满 60 分钟被取消，重跑通过；本机把怀疑的 `qt_open_without_outline_wait_test.py` 连跑 30 次均通过，未能复现 |
| main 上的 Main Full Regression | **未全绿**：自 #264 起 main 上每次推送的该工作流都因 Linux 任务跑满 60 分钟而被取消，每次未报告的是同一批 8 个测试文件（并行池里最后的 `qt_pdf_tip_test`、`translation_bottleneck_audit_test` 与要等池结束才串行执行的 6 个独占文件）。这些文件本机（macOS）都在几十秒内通过，只在 Linux CI 上间歇卡住，原因未定位。#272 给回归运行器加了单进程超时与卡死时的线程栈输出，下次发生会变成带日志的失败。冻结候选自己的 Release Validation 是全绿的，不受此影响 |
| 候选后新增改动的人工验收 | 待人工：审校队列分组与术语卡（#102）、本地网关与推理强度、取消进行中的请求、界面卡死记录、打开任务不等大纲（#260）、跳过零尺寸图片框后的 DOCX 交付（#271）。本机 offscreen 测试不能代替原生窗口验收。已知未解决：作者本机（macOS 27.0.1）上有两类界面线程原生崩溃——11,471 段 PDF 任务打开后约 3 秒、以及交付后刷新工作台时（崩溃栈落在 `qa_panel.py` 的 `rule_list.clear()`），离线复现不稳定，v0.7.2 与本版在同一脚本下各 4 次均未崩，未能判断是否为 0.8.0 引入 |
| 真实长文档交付验收（PDF / DOCX / EPUB / 旧任务升级 / 中断恢复） | 待人工 |
| Windows x64 安装版与便携版原生验收 | 待人工 |
| macOS arm64 DMG 原生验收 | 待人工 |
| #90 审校端到端（人工译文保护、问题处理、术语修改、交付门禁） | 待人工 |
| #242（DOCX 原格式译文桌面入口）、#250（0.8.0 验收记录）合入 main | 已合入（2026-10-28） |
| #188 / #189 验收与 Issue 更新 | 已关闭（2026-10-28），证据见 [0.8.0 验收记录](qt-beta/acceptance-0.8.0-2026-10-28.md) |
| #191 大纲真实窗口验收（macOS / Windows，2000+ 段文档在翻译进行中导航） | 待人工；offscreen 脚本 `scripts/acceptance/outline_191_offscreen.py` 已通过，不能代替 |
| DOCX 原格式译文（#26）原生 macOS / Windows 验收 | 待人工 |
| 资产 SHA-256 | 来自 run 41221050877 的构建记录（未签名，build id `git:ae20a3d`）：<br>`Folith-Qt-0.8.0-macos-arm64.dmg` `e43f852ca341e720560f7941d172565489ef04a5d376e6989010a55affea6f52`（161,568,240 字节）<br>`Folith-Setup-0.8.0-windows-x64.exe` `8233015eeba6fef6eb35275fbd5042beda42878f5a90a901f1d00b74f142c500`<br>`Folith-Qt-0.8.0-windows-x64.zip` `89484fc89813cce4daf896f5fb06cb8b1fc42b4cd5b09c0736428a9b868a36c3`<br>发布时须用 Release 页上传的文件重新核对 |

## 已知限制

- 保留原版式的 PDF 输出（仅 PDF 来源）在扫描版上只能尽力还原：译文盖在原页像素之上，底色取自局部采样，文字后面是照片类背景时无法精确还原；视觉模型给出的文字框常比实际文字行窄，质量很差、但自带文字层又通过质量门禁的扫描件不会走视觉理解。渲染器自带样本只覆盖了一页干净的扫描件，退化扫描和照片背景没有覆盖（见 `docs/original-layout-pdf-renderer-report.md`）。
- 保留原版式的 PDF 输出在有文字层的 PDF 上，页数、书签与插图保持，但验收报告观察到在部分 PDF 上段落可能被合并、标题位置偏移（离线桩译文、未用真实模型复测，我方未能复现），不要把它当作对任意 PDF 的“原样排版”承诺。
- macOS 安装包的最低系统为 15.8.1（0.7.x 为 15.7.5）：随包 Tesseract 及其依赖在 CI 构建环境升级后要求 15.8.1，实测闭包与 Info.plist 一致。macOS 15.7.x 的用户需要先升级系统。
- macOS 包未签名、未公证；Windows 包未签名。首次放行、quarantine、安装升级须在最终候选上人工复核。
- 自动化（CI、offscreen、mock 平台）不能代替真实 Cocoa / Windows 原生窗口验收。
- 随包 OCR 基线为 Tesseract eng / chi_sim / ara；不随包 PaddleOCR 或 LibreOffice。
- Linux 不提供桌面安装包；Windows 10、Windows ARM64、Intel Mac 无原生验收结论。
- 新增翻译文案需要独立的语言审校。
- 三个依赖时序的测试在 CI 中各出现过一次偶发失败、重跑通过：`qt_notifications_test.py::test_card_uses_same_retry_and_stall_clock_as_workbench`（Windows）、`review_scheduler_test.py::test_overlap_with_simulated_latency`（Windows）、`qt_motion_test.py::test_reference_overflow_scrolls_without_compressing_term_rows`（Linux 3.10）。不涉及本版改动，待后续去掉对墙钟与布局时序的依赖。
