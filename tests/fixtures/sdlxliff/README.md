# SDLXLIFF 测试样本（Issue #145 Gate A）

真实 Trados 导出文件，来自 Trados 厂商 RWS 的 Apache-2.0 官方仓库（允许再分发，
保留原始许可证条款）。仅作只读结构审计与将来的 Gate B round-trip 测试基线，
不得当作 Folith 已支持 SDLXLIFF 的依据。

| 文件 | 来源（Apache-2.0） | SHA-256 |
|---|---|---|
| `QuotesSample.docx.sdlxliff` | RWS/Sdl-Community → `XLIFF Manager for Trados Studio/XLIFF.Manager.UnitTests/TestFiles/Sdlxliff/QuotesSample.docx.sdlxliff` | `95a9514d5f5b7e7349639621dedc8bc3a63c75866e6721000fefbeed1800870b` |
| `TTSExample.docx.sdlxliff` | RWS/Sdl-Community → `SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/TTSExample.docx.sdlxliff` | `ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710` |
| `quickplace.docx.sdlxliff` | RWS/Sdl-Community → `SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/#1 - quickplace.docx.sdlxliff` | `85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b` |
| `SamplePhotoPrinter.doc.sdlxliff` | RWS/trados-studio-api-samples → `LanguagePlatform/TMAutomation/SamplePhotoPrinter.doc.sdlxliff` | `d9fc637cdb18d6577ceb3908f3da42286d7135b7473f31a55ee04cf905bcb1ef` |

结构结论与证据见 `docs/sdlxliff-gate-a-compatibility-matrix.md`；
全量审计输出可用 `python scripts/audit_sdlxliff.py` 复现。
