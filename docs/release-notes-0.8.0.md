# Folith 0.8.0 发布说明与验收矩阵

状态：**候选，未发布**。tag、冻结提交与资产校验和在发布时补入。
本页不沿用 0.7.2 的通过结论。

## 版本变化

见 [CHANGELOG](../CHANGELOG.md) 的 0.8.0 条目：文档结构与大纲恢复/手动修正、两级术语流程、翻译范围排除、DOCX 外科式往返、阿拉伯语 RTL、审校工作流完善与稳定性修复。

## 发布前核对表

| 项目 | 状态 |
| --- | --- |
| #235、#237、#238、#239、#240 合入 main | 已合入（2026-10-28） |
| 冻结提交上的 CI | 待填：冻结提交 SHA 与 run 链接 |
| 真实长文档交付验收（PDF / DOCX / EPUB / 旧任务升级 / 中断恢复） | 待人工 |
| Windows x64 安装版与便携版原生验收 | 待人工 |
| macOS arm64 DMG 原生验收 | 待人工 |
| #90 审校端到端（人工译文保护、问题处理、术语修改、交付门禁） | 待人工 |
| #188 / #189 / #191 验收与 Issue 更新 | 待核对 |
| 资产 SHA-256 | 待填 |

## 已知限制

- macOS 包未签名、未公证；Windows 包未签名。首次放行、quarantine、安装升级须在最终候选上人工复核。
- 自动化（CI、offscreen、mock 平台）不能代替真实 Cocoa / Windows 原生窗口验收。
- 随包 OCR 基线为 Tesseract eng / chi_sim / ara；不随包 PaddleOCR 或 LibreOffice。
- Linux 不提供桌面安装包；Windows 10、Windows ARM64、Intel Mac 无原生验收结论。
- 新增翻译文案需要独立的语言审校。
