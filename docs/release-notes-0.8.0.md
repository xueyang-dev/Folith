# Folith 0.8.0 发布说明与验收矩阵

状态：**候选，未发布**。候选冻结于 2026-10-28，tag 在发布时补入。
本页不沿用 0.7.2 的通过结论。

## 版本变化

见 [CHANGELOG](../CHANGELOG.md) 的 0.8.0 条目：审校页改版与概念级术语决定、两级术语管理、文档结构与大纲、翻译范围排除、非 PDF 来源的原文定位、原排版译文转正、21 种翻译语言与法语 / 德语界面、阿拉伯语 RTL、应用内更新与稳定性修复。DOCX 原格式往返（#26）新增可选交付项「原格式译文」，默认关闭，尚未做原生人工验收。

## 发布前核对表

| 项目 | 状态 |
| --- | --- |
| #235、#237、#238、#239、#240 合入 main | 已合入（2026-10-28） |
| 冻结提交上的 CI | 通过。`release/0.8.0` @ `5be4fbae`（与 main `6d25613d` 文件树完全相同），[Release Validation run 40760850276](https://github.com/xueyang-dev/Folith/actions/runs/40760850276)：macOS / Windows 候选构建、冻结主流程与发布检查首轮即通过；Linux Python 3.10 的 `qt_motion_test.py::test_reference_overflow_scrolls_without_compressing_term_rows` 首轮失败（滚动条最大值为 0），重跑通过。main `6d25613d` 的完整回归首轮 Windows `review_scheduler_test.py::test_overlap_with_simulated_latency` 失败（墙钟比较），重跑通过 |
| 真实长文档交付验收（PDF / DOCX / EPUB / 旧任务升级 / 中断恢复） | 待人工 |
| Windows x64 安装版与便携版原生验收 | 待人工 |
| macOS arm64 DMG 原生验收 | 待人工 |
| #90 审校端到端（人工译文保护、问题处理、术语修改、交付门禁） | 待人工 |
| #242（DOCX 原格式译文桌面入口）、#250（0.8.0 验收记录）合入 main | 已合入（2026-10-28） |
| #188 / #189 验收与 Issue 更新 | 已关闭（2026-10-28），证据见 [0.8.0 验收记录](qt-beta/acceptance-0.8.0-2026-10-28.md) |
| #191 大纲真实窗口验收（macOS / Windows，2000+ 段文档在翻译进行中导航） | 待人工；offscreen 脚本 `scripts/acceptance/outline_191_offscreen.py` 已通过，不能代替 |
| DOCX 原格式译文（#26）原生 macOS / Windows 验收 | 待人工 |
| 资产 SHA-256 | 来自上述 run 的构建记录（未签名）：<br>`Folith-Qt-0.8.0-macos-arm64.dmg` `804db6255c903a83afcd2f045e3afb454d2feb04111a46de3bce535bde2dbc6c`<br>`Folith-Setup-0.8.0-windows-x64.exe` `892db6f24426eb741f4a362b0b37c3f3c1fcb361f7f2ad4d50d9f96d5f664100`<br>`Folith-Qt-0.8.0-windows-x64.zip` `76c1114be90bb148723cf2ad13d9daf85d5a047f1e1e772cc813ed1cab5253c6`<br>发布时须用 Release 页上传的文件重新核对 |

## 已知限制

- macOS 包未签名、未公证；Windows 包未签名。首次放行、quarantine、安装升级须在最终候选上人工复核。
- 自动化（CI、offscreen、mock 平台）不能代替真实 Cocoa / Windows 原生窗口验收。
- 随包 OCR 基线为 Tesseract eng / chi_sim / ara；不随包 PaddleOCR 或 LibreOffice。
- Linux 不提供桌面安装包；Windows 10、Windows ARM64、Intel Mac 无原生验收结论。
- 新增翻译文案需要独立的语言审校。
- 三个依赖时序的测试在 CI 中各出现过一次偶发失败、重跑通过：`qt_notifications_test.py::test_card_uses_same_retry_and_stall_clock_as_workbench`（Windows）、`review_scheduler_test.py::test_overlap_with_simulated_latency`（Windows）、`qt_motion_test.py::test_reference_overflow_scrolls_without_compressing_term_rows`（Linux 3.10）。不涉及本版改动，待后续去掉对墙钟与布局时序的依赖。
