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
- **测试替身与确定性审计**：CI 与自动化验收遵循项目原则，不调用付费外部在线模型，使用标准对齐基准（`tests/fixtures/arabic_acceptance.json`）对 48 组核心组合（中/英/俄 ↔ 阿拉伯语，8 个测试场景）及 38 组拓展语言组合进行端到端输入提取、协议参数透传、Unicode 连字变音符保留、双向写入与重读取审计。
- **显式支持边界**：自动化门禁证明协议传输、占位符保留、文本清洗、RTL 段落结构与字符无损坏；真实翻译质量取决于所接入的模型供应商，不可用确定性测试代替母语人工审校。公共文档保持 `experimental` 标注。

### 2.2 复杂表格与各界面长文本排版
- **Qt 工作台独立列排版**：`folith/qt/segment_editor.py` 的 `cell_direction` 现支持传入列语言；`WorkbenchRowDelegate` 保存 `source_language` 与 `target_language`，原文列与译文列独立应用书写方向。对于纯英文前缀、代码或数字单元格，若列属于阿拉伯语，亦能保证 RTL 正确排版与起始边对齐。
- **全屏 RTL 镜像与长文本折行**：阿拉伯语界面下 `QApplication.setLayoutDirection(RightToLeft)` 全局生效，导航、任务概览、状态芯片、设置页、交付卡片及工作台完整自适应；长文本按钮通过 `fit_button` 按字号测量自适应折行。

### 2.3 双向混排文本（Mixed-Direction Text）
逐项验证并断言通过以下混排模式：
- **括号与嵌套说明**：`OpenAI API: مهلة الطلب 30 ثانية (انظر القسم 2).` —— 括号位置稳定，数字与英文字符串不打乱两端阿拉伯语语义流。
- **阿拉伯-印度数字与西方数字**：`١٢٣٤` 与 `1,234`、`95.5%` —— 目标语言为阿拉伯语时保留原样；目标语言为非阿拉伯语脚本时按项目规范（`_numerals.normalize_targets`）映射为标准数字。
- **URL、邮箱与具名占位符**：`{user_name}`、`editor@example.com`、`https://example.com/report` —— 格式符及网址无转义破坏、无反向倒置。
- **阿拉伯语变音符号（Tashkeel）**：`قَرَأَ الطَّالِبُ الْكِتَابَ، ثُمَّ كَتَبَ مُلَخَّصًا قَصِيرًا.` —— 字符组合标记完整存留，无拆散与丢失。
- **标注高亮 Run**：带术语与重点标注的双语表格中，复杂文本字体（`w:cs="Arial"`）及 Run 级 `<w:rtl w:val="1"/>` 保持完整。

### 2.4 Microsoft Word 与真实 EPUB 结构标准
- **OpenXML BiDi & RTL 规范**：
  - 在 `folith/engine/document_export.py` 中，段落级应用 `<w:bidi w:val="1"/>` 与 `<w:jc w:val="start"/>`。
  - **Run 级属性增强**：在 `_apply_run_fonts` 中对阿拉伯语及强 RTL 字符的 Text Run 显式生成 `<w:rPr><w:rtl w:val="1"/></w:rPr>`，符合 ECMA-376 / ISO 29500 规范，保证 Microsoft Word 与 LibreOffice 打开时均能直接识别复杂文本双向流。
- **EPUB 3 规范与导航本地化**：
  - 各章节 XHTML 设置 `dir="rtl"` 与 `xml:lang="ar"`。
  - `content.opf` 的 `<spine>` 声明 `page-progression-direction="rtl"`。
  - `nav.xhtml` 目录标题与封面地标在 RTL 下自动本地化为 `جدول المحتويات`（目录）与 `الغلاف`（封面）。

### 2.5 多页长文与扫描 OCR 链路
- **OCR 语言注册与调度**：`folith/ocr_language.py` 严格区分 `ara` 引擎代码与 RTL 方向，支持 `ar+en` 组合识别；未安装阿拉伯语字包时显式报错拦截，绝不发生静默退回 Latin 或字符乱序。
- **RTL TSV 行块还原**：多页扫描件中的 TSV 框坐标按从右向左逻辑流重构段落，保留阿拉伯语问号 `؟` 分句判定规则。

### 2.6 学术报告导出（Academic Report DOCX）
- **BiDi 引用段落支持**：`folith/report_template.py` 中的 `_insert_content_after` 在插入报告案例时增加 `text_direction` 判定；当案例译文或引用包含阿拉伯语时，自动对生成的 DOCX 段落调用 `_apply_paragraph_direction`，赋予 `w:bidi`、`w:jc="start"` 及复杂脚本字体支持。

### 2.7 全语言组合互译拓展（21 语种）
- 覆盖与阿拉伯语双向互译的 21 种语言组合：
  - 欧洲语系：英语（`en`）、法语（`fr`）、德语（`de`）、西班牙语（`es`）、俄语（`ru`）、意大利语（`it`）、葡萄牙语（`pt`）、荷兰语（`nl`）、波兰语（`pl`）、乌克兰语（`uk`）；
  - 亚洲语系：简体中文（`zh`）、日语（`ja`）、韩语（`ko`）、印地语（`hi`）、孟加拉语（`bn`）、泰语（`th`）、越南语（`vi`）、土耳其语（`tr`）；
  - RTL 语系互译：希伯来语（`he`）、波斯语（`fa`）、乌尔都语（`ur`）、维吾尔语（`ug`）。
- 验证双向请求系统提示词参数、响应解析、分词断句与书写方向切换。

### 2.8 跨平台系统字体覆盖
- 在 `folith/qt/theme.py` 的 `FONT_STACK` 中收拢多平台预装阿拉伯语字体族：
  - macOS：`Geeza Pro`、`Arial`、`Noto Sans Arabic`；
  - Windows：`Segoe UI`、`Arial`、`Tahoma`；
  - Linux：`Noto Sans Arabic`、`DejaVu Sans`。
- 用户无需手动安装第三方字体即可正常连写与渲染。

---

## 3. 验收证据与测试产物

### 3.1 导出的复现材料与机器可读结果
执行 `python3 scripts/arabic_acceptance_audit.py --out exports/arabic-acceptance-v0.8.0` 生成以下审计证据：

| 产物路径 | 说明 |
| :--- | :--- |
| `exports/arabic-acceptance-v0.8.0/audit-results.json` | 包含 6 组核心方向 + 38 组拓展方向的所有机器断言结果（均为 `PASS`） |
| `exports/arabic-acceptance-v0.8.0/*-translation.docx` | 单语阿拉伯语译文 Word 文档，段落 `w:bidi` 与 Run `w:rtl` 校验通过 |
| `exports/arabic-acceptance-v0.8.0/*-bilingual.docx` | 双语对照 Word 表格文档，原文列与译文列独立方向校验通过 |
| `exports/arabic-acceptance-v0.8.0/*-translation.pdf` | 页面自适应右对齐与 PyMuPDF 复杂文本渲染样例 |
| `exports/arabic-acceptance-v0.8.0/*-pdf-page-1.png` | 从导出的 PDF 渲染提取的高分辨率页面图像 |
| `exports/arabic-acceptance-v0.8.0/*.epub` | 符合 EPUB 3 标准的 RTL 翻页与目录结构电子书 |

### 3.2 Qt 界面视觉截图
执行 `python3 scripts/generate_arabic_rtl_evidence.py` 生成以下 UI 审计截图：

| 证据截图 | 界面与验证内容 |
| :--- | :--- |
| [`docs/ui-audit/arabic-rtl-v0.8.0/01-workbench-ar-mixed-bidi.png`](../docs/ui-audit/arabic-rtl-v0.8.0/01-workbench-ar-mixed-bidi.png) | CAT 工作台：阿拉伯语译文列右对齐、英文原文列左对齐、混排数字/括号/占位符稳定 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/02-home-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/02-home-ar.png) | 项目首页：整体 RTL 镜像，侧边导航、项目列表与色点位置对齐 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/03-setup-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/03-setup-ar.png) | 任务设置面板：语言选择、表单标题与控件自右向左对齐 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/04-delivery-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/04-delivery-ar.png) | 交付面板：交付卡片、导出选项及状态芯片在 RTL 布局下的排列 |
| [`docs/ui-audit/arabic-rtl-v0.8.0/05-settings-ar.png`](../docs/ui-audit/arabic-rtl-v0.8.0/05-settings-ar.png) | 系统设置窗口：多页签标题、模型配置表单自右向左排版 |

---

## 4. 复现与运行命令

```bash
# 1. 运行核心阿拉伯语与 RTL 自动化测试（59 项针对性用例）
python3 -m pytest -v tests/arabic_rtl_test.py

# 2. 运行完整 OCR 与 RTL 调度测试
python3 -m pytest -v tests/ocr_language_rtl_test.py

# 3. 运行本地完整性与机器可读审计（生成 exports/ 产物）
python3 scripts/arabic_acceptance_audit.py --out exports/arabic-acceptance-v0.8.0

# 4. 生成 Qt 界面截图证据
python3 scripts/generate_arabic_rtl_evidence.py
```
