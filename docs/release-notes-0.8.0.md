# Folith 0.8.0 发布说明与验收矩阵

状态：**候选，未发布**。候选于 2026-10-31 重新冻结在 main 当前提交（之前的候选 `5be4fbae`、`76e2f269` 作废），tag 在发布时补入。冻结提交与验证 run 见下表，待 Release Validation 通过后填写。
本页不沿用 0.7.2 的通过结论。

## 版本变化

见 [CHANGELOG](../CHANGELOG.md) 的 0.8.0 条目：审校页改版与概念级术语决定、两级术语管理、文档结构与大纲、翻译范围排除、非 PDF 来源的原文定位、原排版译文转正、21 种翻译语言与法语 / 德语界面、阿拉伯语 RTL、应用内更新与稳定性修复。DOCX 原格式往返（#26）新增可选交付项「原格式译文」，默认关闭，尚未做原生人工验收。

## 发布前核对表

| 项目 | 状态 |
| --- | --- |
| #235、#237、#238、#239、#240 合入 main | 已合入（2026-10-28） |
| 冻结提交上的 CI | **待填**：`release/0.8.0` 重新冻结后的提交与 Release Validation run。此前候选 `76e2f269`（run 40815562563 通过）之后又合入 #259、#260、#262、#263、#266，它们没有被该 run 覆盖，该候选作废 |
| 重新冻结前 main 的完整回归 | HEAD `fc865d0c` 的 Windows 3.11 任务唯一失败是已知偶发的 `qt_notifications_test.py::test_card_uses_same_retry_and_stall_clock_as_workbench`，Python 3.10 / 3.12 任务被取消未跑完；macOS 与打包任务通过。需要以重跑或冻结后的 run 为准 |
| 候选后新增改动的人工验收 | 待人工：审校队列分组与术语卡（#102）、本地网关与推理强度、取消进行中的请求、界面卡死记录、打开任务不等大纲（#260）。本机 offscreen 测试 441 通过，不能代替原生窗口验收 |
| 真实长文档交付验收（PDF / DOCX / EPUB / 旧任务升级 / 中断恢复） | 待人工 |
| Windows x64 安装版与便携版原生验收 | 待人工 |
| macOS arm64 DMG 原生验收 | 待人工 |
| #90 审校端到端（人工译文保护、问题处理、术语修改、交付门禁） | 待人工 |
| #242（DOCX 原格式译文桌面入口）、#250（0.8.0 验收记录）合入 main | 已合入（2026-10-28） |
| #188 / #189 验收与 Issue 更新 | 已关闭（2026-10-28），证据见 [0.8.0 验收记录](qt-beta/acceptance-0.8.0-2026-10-28.md) |
| #191 大纲真实窗口验收（macOS / Windows，2000+ 段文档在翻译进行中导航） | 待人工；offscreen 脚本 `scripts/acceptance/outline_191_offscreen.py` 已通过，不能代替 |
| DOCX 原格式译文（#26）原生 macOS / Windows 验收 | 待人工 |
| 资产 SHA-256 | 旧候选的校验值，重新冻结后须以新 run 的构建替换：来自旧 run 的构建记录（未签名）：<br>`Folith-Qt-0.8.0-macos-arm64.dmg` `804db6255c903a83afcd2f045e3afb454d2feb04111a46de3bce535bde2dbc6c`<br>`Folith-Setup-0.8.0-windows-x64.exe` `892db6f24426eb741f4a362b0b37c3f3c1fcb361f7f2ad4d50d9f96d5f664100`<br>`Folith-Qt-0.8.0-windows-x64.zip` `76c1114be90bb148723cf2ad13d9daf85d5a047f1e1e772cc813ed1cab5253c6`<br>发布时须用 Release 页上传的文件重新核对 |

## 已知限制

- macOS 安装包的最低系统为 15.8.1（0.7.x 为 15.7.5）：随包 Tesseract 及其依赖在 CI 构建环境升级后要求 15.8.1，实测闭包与 Info.plist 一致。macOS 15.7.x 的用户需要先升级系统。
- macOS 包未签名、未公证；Windows 包未签名。首次放行、quarantine、安装升级须在最终候选上人工复核。
- 自动化（CI、offscreen、mock 平台）不能代替真实 Cocoa / Windows 原生窗口验收。
- 随包 OCR 基线为 Tesseract eng / chi_sim / ara；不随包 PaddleOCR 或 LibreOffice。
- Linux 不提供桌面安装包；Windows 10、Windows ARM64、Intel Mac 无原生验收结论。
- 新增翻译文案需要独立的语言审校。
- 三个依赖时序的测试在 CI 中各出现过一次偶发失败、重跑通过：`qt_notifications_test.py::test_card_uses_same_retry_and_stall_clock_as_workbench`（Windows）、`review_scheduler_test.py::test_overlap_with_simulated_latency`（Windows）、`qt_motion_test.py::test_reference_overflow_scrolls_without_compressing_term_rows`（Linux 3.10）。不涉及本版改动，待后续去掉对墙钟与布局时序的依赖。
