# SDLXLIFF 测试样本（Issue #145 Gate A）

真实 Trados 导出文件，来自 Trados 厂商 RWS 的 Apache-2.0 官方仓库（允许再分发，
保留原始许可证条款）。仅作只读结构审计与将来的 Gate B round-trip 测试基线，
不得当作 Folith 已支持 SDLXLIFF 的依据。

| 文件 | 来源（Apache-2.0） | 入库 blob SHA-256 | 上游原件 SHA-256 |
|---|---|---|---|
| `QuotesSample.docx.sdlxliff` | [`RWS/Sdl-Community/XLIFF Manager for Trados Studio/XLIFF.Manager.UnitTests/TestFiles/Sdlxliff/QuotesSample.docx.sdlxliff`](https://github.com/RWS/Sdl-Community/blob/eb7e401f3dddf605e1357d3a718d04f487fd7ad2/XLIFF%20Manager%20for%20Trados%20Studio/XLIFF.Manager.UnitTests/TestFiles/Sdlxliff/QuotesSample.docx.sdlxliff) | `95a9514d5f5b7e7349639621dedc8bc3a63c75866e6721000fefbeed1800870b` | `95a9514d5f5b7e7349639621dedc8bc3a63c75866e6721000fefbeed1800870b` |
| `TTSExample.docx.sdlxliff` | [`RWS/Sdl-Community/SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/TTSExample.docx.sdlxliff`](https://github.com/RWS/Sdl-Community/blob/eb7e401f3dddf605e1357d3a718d04f487fd7ad2/SDLTM%20Import%20Plus/SDLTMImportUnitTests/TestingFiles/TTSExample.docx.sdlxliff) | `ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710` | `ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710` |
| `quickplace.docx.sdlxliff` | [`RWS/Sdl-Community/SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/#1 - quickplace.docx.sdlxliff`](https://github.com/RWS/Sdl-Community/blob/eb7e401f3dddf605e1357d3a718d04f487fd7ad2/SDLTM%20Import%20Plus/SDLTMImportUnitTests/TestingFiles/%231%20-%20quickplace.docx.sdlxliff) | `85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b` | `85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b` |
| `SamplePhotoPrinter.doc.sdlxliff` | [`RWS/trados-studio-api-samples/LanguagePlatform/TMAutomation/SamplePhotoPrinter.doc.sdlxliff`](https://github.com/RWS/trados-studio-api-samples/blob/4cf88a2c9acdbafed0702aa6bd5df5012c30b60c/LanguagePlatform/TMAutomation/SamplePhotoPrinter.doc.sdlxliff) | `b27a14cb95e058d3c1007c6ced037f656fe190f0ebc4c6b509e48db488f57dd3` | `d9fc637cdb18d6577ceb3908f3da42286d7135b7473f31a55ee04cf905bcb1ef` |

表格中的哈希由 `tests/fixture_provenance_test.py` 守护：入库 blob 的 SHA-256
必须等于"入库 blob SHA-256"列；"上游原件 SHA-256"列给出上游固定 commit 的原始
字节指纹。前三个文件上游与入库逐字节相同。

结构结论与证据见 `docs/sdlxliff-gate-a-compatibility-matrix.md`；
全量审计输出可用 `python scripts/audit_sdlxliff.py` 复现。

## 上游许可与入库差异

- RWS/Sdl-Community 的[上游许可文件](https://github.com/RWS/Sdl-Community/blob/eb7e401f3dddf605e1357d3a718d04f487fd7ad2/LICENSE)保存在 [`RWS-Sdl-Community.txt`](licenses/RWS-Sdl-Community.txt)。
- RWS/trados-studio-api-samples 的[上游许可文件](https://github.com/RWS/trados-studio-api-samples/blob/4cf88a2c9acdbafed0702aa6bd5df5012c30b60c/LICENSE)保存在 [`RWS-trados-studio-api-samples.txt`](licenses/RWS-trados-studio-api-samples.txt)。

两个上游路径的适用目录均只有上述 LICENSE。样本导出配置与逐文件 Studio 版本待核实。

### 换行口径（2026-11-12 复核）

`SamplePhotoPrinter.doc.sdlxliff` 的上游原件是 **CRLF** 行尾（42996 B，298 个
回车），入库 blob 是 **LF**（42698 B，0 个回车）。两者**只差换行**：把入库 blob
的 298 个 LF 程序化还原为 CRLF 后，SHA-256 为
`d9fc637cdb18d6577ceb3908f3da42286d7135b7473f31a55ee04cf905bcb1ef`，与上游原件
逐字节一致，解码文本相同。该结论已用上表链接的固定 commit
`4cf88a2c9acdbafed0702aa6bd5df5012c30b60c` 取回的上游文件复核，不是推断。

其余三个样本的上游原件本身就是 LF，与入库 blob 逐字节相同。

复核方式（不联网的回归看门）：`python -m pytest tests/fixture_provenance_test.py -q`。
