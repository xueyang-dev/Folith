# 阿拉伯语 RTL 排版、导出与全链路验收（v0.8.0）

日期：2026-10-26。对应 Issue：[#16](https://github.com/xueyang-dev/Folith/issues/16)。
前置文档：[arabic-acceptance-2026-10-01.md](arabic-acceptance-2026-10-01.md)（初次验收记录）、[arabic-rtl-fixes-2026-10-01.md](arabic-rtl-fixes-2026-10-01.md)（首轮排版修复）。

---

## 1. 验收背景与范围

在 Folith v0.8.0（Document Fidelity, CAT & Languages）里程碑中，阿拉伯语（`ar`）不仅作为界面的实验性语言包，更涉及 CAT 工作台双向排版、DOCX/PDF/EPUB 导出交付、复杂表格、双向文本混排（BiDi）及扫描识别的完整链路。

本轮验收针对 `docs/arabic-rtl-fixes-2026-10-01.md` 遗留的“仍需验证的范围”逐项进行工程实现、断言覆盖、视觉截图与机器可读审计。

---

## 2. 核心验证与工程修复

针对遗留的 8 项边界，本轮完成了以下实现与验收：

### 2.1 真实模型翻译与独立语义审校（Explicit Boundary）
- **测试替身与确定性审计**：CI 与自动化验收遵循项目原则，不调用付费外部在线模型，使用标准对齐基准（`tests/fixtures/arabic_acceptance.json`）对 48 组核心组合（中/英/俄 ↔ 阿拉伯语，每个方向 8 个测试场景）及 40 组拓展语言组合（20 个可选伙伴语言 × 双向）进行端到端输入提取、协议参数透传、Unicode 连字变音符保留、双向写入与重读取审计。
- **显式支持边界**：自动化门禁证明协议传输、占位符保留、文本清洗、RTL 段落结构与字符无损坏；真实翻译质量取决于所接入的模型供应商，本轮没有真实模型调用，也没有独立母语审校记录。公共文档保持 `experimental` 标注。

### 2.2 复杂表格与各界面长文本排版
- **Qt 工作台独立列排版**：`folith/qt/segment_editor.py` 的 `cell_direction` 现支持传入列语言；`WorkbenchRowDelegate` 保存 `source_language` 与 `target_language`，原文列与译文列独立应用书写方向。对于纯英文前缀、代码或数字单元格，若列属于阿拉伯语，亦能保证 RTL 正确排版与起始边对齐。
- **全屏 RTL 镜像与长文本折行**：阿拉伯语界面下 `QApplication.setLayoutDirection(RightToLeft)` 全局生效，导航、任务概览、状态芯片、设置页、交付卡片及工作台完整自适应；长文本按钮通过 `fit_button` 按字号测量自适应折行。
- **PDF 长段落截断修复**：`translations_to_pdf` 原先把每段放进单行表格；MuPDF 不会跨页拆分表格行，因此单个高于页面的阿拉伯语段落会被截断（实测 6,221 字符只输出 4,140 个字符）。现改为全宽 `<p style="text-align:start">`，保留右对齐起始边，并可跨页拆分。验收用一个约 6,700 字符的阿拉伯语段落（90 个数字哨兵）确认跨 2 页且 90 个哨兵全部存在。
- **源文档表格边界**：段落提取链路对源 DOCX 中的表格文字不做翻译，并在导入报告中显式列出“表格文字不进入翻译”；双语对照表本身（`pairs_to_word` 生成的表格）是受支持并通过 RTL 验收的导出形态。两者不是同一能力，文档中不合并陈述。

### 2.3 双向混排文本（Mixed-Direction Text）
逐项验证并断言通过以下混排模式：
- **括号与嵌套说明**：`OpenAI API: مهلة الطلب 30 ثانية (انظر القسم 2).` —— 括号位置稳定，数字与英文字符串不打乱两端阿拉伯语语义流。
- **阿拉伯-印度数字与西方数字**：`١٢٣٤` 与 `1,234`、`95.5%` —— 目标语言为阿拉伯语时保留原样；目标语言为非阿拉伯语脚本时按项目规范（`_numerals.normalize_targets`）映射为标准数字。
- **URL、邮箱与具名占位符**：`{user_name}`、`editor@example.com`、`https://example.com/report` —— 格式符及网址无转义破坏、无反向倒置。
- **阿拉伯语变音符号（Tashkeel）**：`قَرَأَ الطَّالِبُ الْكِتَابَ، ثُمَّ كَتَبَ مُلَخَّصًا قَصِيرًا.` —— 字符组合标记完整存留，无拆散与丢失。
- **标注高亮 Run**：带术语与重点标注的双语表格中，复杂文本字体（`w:cs="Arial"`）及 Run 级 `<w:rtl w:val="1"/>` 保持完整。
- **导出侧边距**：混合方向段落导出 PDF 后，正文最右端达到页宽的 0.891（A4，左右各 54pt 边距），确认整行从右侧起始边开始排版，而不是只有阿拉伯语 run 局部右对齐。

### 2.4 Microsoft Word 与真实 EPUB 结构标准
- **OpenXML BiDi & RTL 规范**：
  - 在 `folith/engine/document_export.py` 中，段落级应用 `<w:bidi w:val="1"/>` 与 `<w:jc w:val="start"/>`。
  - **Run 级属性增强**：在 `_apply_run_fonts` 中对阿拉伯语及强 RTL 字符的 Text Run 显式生成 `<w:rPr><w:rtl w:val="1"/></w:rPr>`，符合 ECMA-376 / ISO 29500 规范。
- **真实 Microsoft Word 渲染（macOS）**：用 Word 16.114.1 打开 Folith 导出的单语译文 DOCX 与双语对照 DOCX，各自导出 PDF 后逐项核对：
  - 单语译文：8 个数字哨兵全部存在；首页阿拉伯语文字最右端达到页宽的 0.848（Letter 页面，右边界约 0.853）；标题、混排英文前缀、`95.5%` 与括号保持正确。
  - 双语对照：原文列与译文列独立方向，阿拉伯语单元格右端 0.865，8 个哨兵全部存在。
  - 证据：`docs/evidence/word-arabic-2026-10-26/`（DOCX、Word 导出的 PDF、首页 PNG、`evidence.json`）。
- **EPUB 3 规范与导航本地化**：
  - 各章节 XHTML 设置 `dir="rtl"` 与 `xml:lang="ar"`。
  - `content.opf` 的 `<spine>` 声明 `page-progression-direction="rtl"`。
  - `nav.xhtml` 目录标题与封面地标在 RTL 下自动本地化为 `جدول المحتويات`（目录）与 `الغلاف`（封面）。
  - **边界**：本轮只做了结构与字节级验收；本机没有可脚本化的真实 EPUB 阅读器（Apple Books 需要交互式辅助访问授权），因此“在真实阅读器中翻页、书脊方向与目录点击”仍是待人工确认项，不在本轮 PASS 范围内。

### 2.5 多页长文与扫描 OCR 链路
- **OCR 语言注册与调度**：`folith/ocr_language.py` 严格区分 `ara` 引擎代码与 RTL 方向，支持 `ar+eng` 组合识别；未安装阿拉伯语字包时显式报错拦截，绝不发生静默退回 Latin 或字符乱序。`ara+eng` 组合要求两个语言包同时存在；验收环境把 `ara`、`eng` 与 `configs/tsv` 放在同一个 `TESSDATA_PREFIX` 下。
- **真实 Tesseract 验收（Tesseract 5.5.0）**：用 Qt 渲染三行阿拉伯语样张（其中一行含 `OpenAI API: … 30 ثانية`），走产品自身的 `_ocr_page_with_tesseract`（`ara+eng`、默认 PSM 3、RTL）识别：
  - 得到 3 个文字块，按图像从上到下排列，平均置信度 88.9；
  - 数字 `30`、`9` 与全部阿拉伯语正文保留；
  - 混排拉丁片段被识别为 `OpenAl API`（大写 I 读成小写 l）——这是 Tesseract 的字符级精度边界，不是 RTL 顺序问题；
  - 证据：`docs/evidence/ocr-arabic-2026-10-26/`（样张 PNG、`evidence.json`）。
- **RTL TSV 行块还原**：多页扫描件中的 TSV 框坐标按从右向左逻辑流重构段落，保留阿拉伯语问号 `؟` 分句判定规则。

### 2.6 学术报告导出（Academic Report DOCX）
- **BiDi 引用段落支持**：`folith/report_template.py` 中的 `_insert_content_after` 在插入报告案例时增加 `text_direction` 判定；当案例译文或引用包含阿拉伯语时，自动对生成的 DOCX 段落调用 `_apply_paragraph_direction`，赋予 `w:bidi`、`w:jc="start"` 及复杂脚本字体支持。

### 2.7 全语言组合互译拓展（21 语种）
- 覆盖与阿拉伯语双向互译的全部 20 个可选伙伴语言（产品目录 `TARGET_LANGUAGES` 去掉阿拉伯语），每个语言正反各一次：
  - 中文与亚洲语系：简体中文、繁体中文、日语、韩语、越南语、土耳其语；
  - 欧洲语系：英语、法语、德语、西班牙语、俄语、意大利语、葡萄牙语、荷兰语、波兰语、乌克兰语；
  - RTL 语系互译：希伯来语、波斯语、乌尔都语、维吾尔语。
- 验收脚本直接从 `TARGET_LANGUAGES` 生成伙伴语言列表，并断言样本表与目录完全一致，避免以后新增可选语言却没有进入验收。
- 协议层另外覆盖印地语、孟加拉语、泰语、斯瓦希里语、希腊语等语言名（当前不在界面可选目录中），只验证翻译协议透传，不计入“可选语言”覆盖。
- 验证双向请求系统提示词参数、响应解析、分词断句与书写方向切换。

### 2.8 跨平台系统字体覆盖
- 在 `folith/qt/theme.py` 的 `FONT_STACK` 中收拢多平台预装阿拉伯语字体族：
  - macOS：`Geeza Pro`、`Arial`、`Noto Sans Arabic`；
  - Windows：`Segoe UI`、`Arial`、`Tahoma`；
  - Linux：`Noto Sans Arabic`、`DejaVu Sans`。
- **真实字形覆盖（非仅字体名）**：在验收机上用 `QRawFont.supportsCharacter` 检查 `العربيةقَرَأَ؟،١٢٣٤`，`FONT_STACK` 中实际安装且完整覆盖该样本的是 `Geeza Pro` 与 `Arial`；Qt 的字体回退保证连写与变音符正常显示。
- **边界**：Windows 与 Linux 只做了字体族名称级检查，未在对应系统上做实机字形渲染。

---

## 3. 验收证据与测试产物

### 3.1 导出的复现材料与机器可读结果
执行 `python3 scripts/arabic_acceptance_audit.py --out exports/arabic-acceptance-v0.8.0` 生成以下审计证据：

| 产物路径 | 说明 |
| :--- | :--- |
| `exports/arabic-acceptance-v0.8.0/audit-results.json` | 6 组核心方向 + 40 组拓展方向（20 个可选语言 × 双向），以及长文档、混排 PDF、真实字形覆盖的断言结果（均为 `PASS`） |
| `exports/arabic-acceptance-v0.8.0/long-paragraph-ar.pdf` | 单段约 6,700 字符阿拉伯语段落导出为 2 页，90 个数字哨兵全部存在 |
| `exports/arabic-acceptance-v0.8.0/mixed-direction-ar.pdf` | 混排段落 PDF，正文右端达到页宽 0.891 |
| `exports/arabic-acceptance-v0.8.0/*-translation.docx` | 单语阿拉伯语译文 Word 文档，段落 `w:bidi` 与 Run `w:rtl` 校验通过 |
| `exports/arabic-acceptance-v0.8.0/*-bilingual.docx` | 双语对照 Word 表格文档，原文列与译文列独立方向校验通过 |
| `exports/arabic-acceptance-v0.8.0/*-translation.pdf` | 页面自适应右对齐与 PyMuPDF 复杂文本渲染样例 |
| `exports/arabic-acceptance-v0.8.0/*-pdf-page-1.png` | 从导出的 PDF 渲染提取的高分辨率页面图像 |
| `exports/arabic-acceptance-v0.8.0/*.epub` | 符合 EPUB 3 标准的 RTL 翻页与目录结构电子书 |

审计的固定副本保存在 `docs/evidence/arabic-acceptance-2026-10-26.json`（可随仓库审阅）。

### 3.2 真实 Microsoft Word 渲染证据
| 产物路径 | 说明 |
| :--- | :--- |
| `docs/evidence/word-arabic-2026-10-26/evidence.json` | Word 16.114.1；单语/双语/英文对照三份 PDF 的页数、哨兵、右端比例、百分比观察值 |
| `docs/evidence/word-arabic-2026-10-26/word-translation-ar.pdf` | Word 打开 Folith 单语译文 DOCX 后导出的 PDF（右端 0.848） |
| `docs/evidence/word-arabic-2026-10-26/word-bilingual-ar.pdf` | Word 打开双语对照 DOCX 后导出的 PDF（阿拉伯语单元格右端 0.865） |
| `docs/evidence/word-arabic-2026-10-26/*-page1.png` | 上述 PDF 的首页渲染图，便于快速目视核对 |

### 3.3 真实 Tesseract OCR 证据
| 产物路径 | 说明 |
| :--- | :--- |
| `docs/evidence/ocr-arabic-2026-10-26/evidence.json` | Tesseract 5.5.0、`ara+eng`、PSM 3；3 个块按图像顺序排列，平均置信度 88.9，拉丁片段观察值 `OpenAl API` |
| `docs/evidence/ocr-arabic-2026-10-26/ocr-sample-ar.png` | Qt 渲染的阿拉伯语扫描样张（三行，含混排数字与拉丁片段） |

### 3.4 Qt 界面视觉截图
执行 `QT_QPA_PLATFORM=cocoa python3 scripts/generate_arabic_rtl_evidence.py`，在隔离的数据目录中启动产品自身的 `MainWindow`（侧栏、任务库、工作台、交付页均完整），生成以下截图：

| 证据截图 | 界面与验证内容 |
| :--- | :--- |
| [`docs/ui-audit/arabic-rtl-v0.8.0/01-workbench-ar-mixed-bidi.png`](../docs/ui-audit/arabic-rtl-v0.8.0/01-workbench-ar-mixed-bidi.png) | 真实主窗口工作台：阿拉伯语译文列右对齐、英文原文列左对齐、混排数字/括号/占位符稳定 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/02-home-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/02-home-ar.png) | 真实主窗口任务库：侧边导航、任务卡、状态芯片整体 RTL 镜像 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/03-setup-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/03-setup-ar.png) | 任务设置面板：语言选择、表单标题与控件自右向左对齐 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/04-delivery-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/04-delivery-ar.png) | 真实主窗口交付页：交付卡片、导出选项及状态芯片在 RTL 布局下的排列 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/05-settings-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/05-settings-ar.png) | 系统设置窗口：多页签标题自右向左排列，语言项标注 `العربية (experimental)` |

截图跟随系统深色主题；主题本身与 RTL 无关。

---

## 4. 复现与运行命令

```bash
# 1. 运行阿拉伯语与 RTL 自动化测试（74 项）
python3 -m pytest -q tests/arabic_rtl_test.py

# 2. 运行 OCR 与 RTL 调度测试（22 项）
python3 -m pytest -q tests/ocr_language_rtl_test.py

# 3. 运行本地完整性与机器可读审计（生成 exports/ 产物并更新证据 JSON）
python3 scripts/arabic_acceptance_audit.py --out exports/arabic-acceptance-v0.8.0

# 4. 生成 Word 验收输入，再用 Word 手工导出同目录下的 PDF，最后复核
python3 scripts/word_render_audit.py --dir docs/evidence/word-arabic-2026-10-26
#    在 Word 中打开该目录的 3 份 DOCX，另存为同名 PDF，然后再次运行上面的命令

# 5. 真实 Tesseract 验收（需要 ara + eng 两个语言包与 configs/tsv）
python3 scripts/ocr_arabic_acceptance.py --tessdata <含 ara/eng/configs 的目录>

# 6. 生成 Qt 界面截图证据（必须使用原生平台）
QT_QPA_PLATFORM=cocoa python3 scripts/generate_arabic_rtl_evidence.py
```

---

## 5. 已知边界与未完成项

1. **真实模型翻译质量与独立母语审校**：本轮全部是确定性替身验收，没有真实供应商调用，也没有母语审校签字；状态保持 `experimental`。
2. **Word 渲染平台**：只验证了 macOS Word 16.114.1；LibreOffice、WPS、Word for Windows 未在本轮实机验证。
3. **真实 EPUB 阅读器**：EPUB 结构、`dir`、书脊方向与本地化目录已验证；真实阅读器翻页/目录点击未自动化，需人工确认。
4. **源文档复杂表格**：段落提取链路不翻译源 DOCX 表格文字、页眉页脚、文本框、脚注与图片内文字，并在导入报告中显式列出；双语对照表导出不受影响。
5. **OCR 字符精度**：Tesseract 5.5.0 在阿拉伯语行内可能把拉丁片段读错一个字符（实测 `OpenAI API` → `OpenAl API`）；RTL 顺序与阿拉伯语正文不受影响。`ara+eng` 需要同时安装两个语言包及 `configs/tsv`。
6. **界面文案完整性**：任务库分组名等个别字符串仍会显示中文（例如 `未分类`），属于 i18n 文案缺口，不影响 RTL 排版与方向判断；本轮未扩大范围修改。
7. **PDF 文本提取**：PyMuPDF 输出的是阿拉伯语呈现形式（presentation forms），不能直接按逻辑字符数比较；长文完整性用数字哨兵断言，而不是字符长度。
