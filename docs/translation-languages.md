# Folith 翻译源语言与目标语言支持与验收规范

本文档记录计划用于 **v0.8.0** 的翻译源语言（Source Language）与目标语言（Target Language）目录扩展及组件验证。关联任务为 [#10](https://github.com/xueyang-dev/Folith/issues/10)；本次实现不代表新增语言对已通过真实模型的完整质量验收，#10 仍保持打开。

> [!NOTE]
> 界面交互语言（UI Locales，即软件操作界面中/英/法/德/西/俄/阿）由 [#11](https://github.com/xueyang-dev/Folith/issues/11) 跟踪；本文档专指**文档翻译的原文语言与译文目标语言**。

---

## 1. 语言目录审计与扩展范围

### 1.1 目录对齐与初选原则
1. **源/目标目录对齐**：首先审计此前已在原文语言目录提供、但目标语言下拉框缺失的 5 种语言（土耳其语、乌尔都语、波斯语、维吾尔语、希伯来语），全部补齐为可选目标语言。
2. **高频需求扩展**：结合主流翻译场景与 OCR 引擎支持，初选扩充 4 种重要语言：荷兰语（Nederlands）、波兰语（Polski）、乌克兰语（Українська）、越南语（Tiếng Việt）。
3. **两端统一**：原文语言目录与目标语言目录收敛为完全一致的 **21 种语言**。

### 1.2 完整支持清单（21 语）

| 语言自称 (Endonym) | 英文名 | ISO 639-1 / BCP-47 | Tesseract 代码 | 书写方向 | 类别与特性 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **简体中文** | Simplified Chinese | `zh-CN` | `chi_sim` | LTR | CJK 象形，字符级统计 |
| **繁体中文** | Traditional Chinese | `zh-TW` | `chi_tra` | LTR | CJK 象形，字符级统计 |
| **English** | English | `en` | `eng` | LTR | 基础拉丁，大小写分句 |
| **日本語** | Japanese | `ja` | `jpn` | LTR | CJK 假名/汉字混合 |
| **한국어** | Korean | `ko` | `kor` | LTR | 谚文，空格分词 |
| **Deutsch** | German | `de` | `deu` | LTR | 复合词、变音符号（Umlaut） |
| **Français** | French | `fr` | `fra` | LTR | 变音符、连字符、冠词守卫 |
| **Español** | Spanish | `es` | `spa` | LTR | 重音符、倒感叹号/倒问号 |
| **Italiano** | Italian | `it` | `ita` | LTR | 省略符号与重音 |
| **Português** | Portuguese | `pt` | `por` | LTR | 鼻化元音、波浪号 |
| **Русский** | Russian | `ru` | `rus` | LTR | 西里尔字母 |
| **Nederlands** | Dutch | `nl` | `nld` | LTR | 双元音、连字、首字母缩写保护 |
| **Polski** | Polish | `pl` | `pol` | LTR | 西斯拉夫变音符、尾音辅音丛 |
| **Українська** | Ukrainian | `uk` | `ukr` | LTR | 东斯拉夫西里尔字母（含 `і`, `ї`, `є`, `ґ`） |
| **Tiếng Việt** | Vietnamese | `vi` | `vie` | LTR | 复杂声调符号与角符、空格分音节 |
| **Türkçe** | Turkish | `tr` | `tur` | LTR | 突厥语族、带点与不带点 `i` / `I` / `ı` / `İ` |
| **العربية** | Arabic | `ar` | `ara` | RTL | 闪含语系、连写阿拉伯字母、阿拉伯问号 `؟` |
| **فارسی** | Persian | `fa` | `fas` | RTL | 波斯字母（含 `پ`, `چ`, `ژ`, `گ`）、东阿拉伯数字 |
| **עברית** | Hebrew | `he` | `heb` | RTL | 希伯来方块字母、无大小写分句 |
| **اردو** | Urdu | `ur` | `urd` | RTL | 波斯-阿拉伯字母、乌尔都句号 `۔` |
| **ئۇيغۇرچە** | Uyghur | `ug` | `uig` | RTL | 维吾尔阿拉伯文（全元音文字） |

---

## 2. 核心架构契约与实现细节

### 2.1 任务配置与持久化
- 原文语言 (`source_lang`) 与目标语言 (`target_lang`) 均持久化在任务的 `state.json` 与流水线快照中。
- `folith/source_languages.py` 严格进行别名匹配（`match(text)`），识别用户在交互或画像中输入的中文名（如“波兰语”）、英文名（如“Polish”）或 ISO 代码（如“pl”），规范化保存为语言自称（如 `"Polski"`），未收录语言绝不随意猜测。
- 本次组件测试验证源/目标语言字段的保存重读和 `task_context` 构造；没有据此声称所有语言方向的重新导入、任务身份及完整管线行为均已验收。

### 2.2 视觉模型（VLM）提示词与缓存身份
- 扫描件由多模态视觉模型（VLM）阅读，`VLMDocumentProvider` 会在提示词中附加针对该语言的文字学指示：
  ```text
  The document is written in <Language>. Transcribe it in <Language> with that language's
  own orthography and letter forms; do not normalise it into another language that shares the script.
  ```
  该提示要求模型区分共用文字的语言（如阿拉伯语、波斯语、乌尔都语、维吾尔语）。现有测试只验证提示词与缓存身份，不证明真实模型不会发生语言或正字法混淆。
- 语言提示是 `cache_identity` 的一部分，更换原文语言后自动使旧缓存失效，确保不复用错误语言的页面识别结果。

### 2.3 本机 Tesseract OCR 与语言包守卫
- 本机 Tesseract OCR 仅作为辅助选项（提取排版坐标或纯离线流水线）。
- `validate_language_packs` 严格校验所选语言对应的 Tesseract 语言包（如 `tur`、`fas`、`heb`、`urd`、`uig`、`nld`、`pol`、`ukr`、`vie`）。
- **缺少语言包时显式报错拦截**，明确告知用户缺失的语言包文件名并列出已安装项，**坚决不隐蔽降级为 `eng`**，杜绝错乱文本污染后续翻译。

### 2.4 分句（Segmentation）与标点适配
- 分句算法（`folith/segmentation.py`）针对非西欧文字与复杂脚本进行了规则增强：
  - 支持乌尔都语专用终结符 `۔`（U+06D4）与阿拉伯问号 `؟`（U+061F）。
  - 处理无大小写字母体系（Arabic, Hebrew 等）的句子边界，避免英文大小写假设导致的不分句。
  - 增强 `_token_before` 识别含非 ASCII 变音符的 Unicode 单词（如越南语 `tiên.`、波兰语、捷克语等），防止将复合字母误判为缩写首字母。

### 2.5 数字与双向文本（BiDi / RTL）
- `folith/numerals.py` 约定：东阿拉伯数字（٠١٢… / ۰۱۲…）在目标语言为阿拉伯语、波斯语、乌尔都语时予以保留；在目标语言为西欧或 CJK 语言时自动规范化为西式阿拉伯数字（0-9），且严格保护 URL、DOI 与行内术语。
- 双向文本判断（`folith/textual.py:text_direction`）：对于 5 种 RTL 语言以及对应 BCP-47 标签，自动赋予 `"rtl"` 方向。

### 2.6 交付产物导出（Exports）
- **DOCX 导出**：自动在段落属性中设置 `w:bidi`，RTL 段落应用靠右对齐，LTR 段落应用靠左对齐。
- **PDF 导出**：通过 PyMuPDF `Story` 根据目标语言设置表格单元格 `dir="rtl"` 或 `dir="ltr"`，文字自动应用对齐。
- **EPUB 导出**：在 `content.opf`、`nav.xhtml` 以及各章节 XHTML 中注入准确的 BCP-47 `xml:lang` 和 `page-progression-direction`。
- **TBX / TMX 资产**：`folith.textual.language_tag` 为所有 21 种语言生成合法的 BCP-47 标签（例如 `tr`、`fa`、`he`、`ur`、`ug`、`nl`、`pl`、`uk`、`vi`），确保导出的行业标准记忆库绝不出现 `"und"`。

---

## 3. 质量验证与支持声明区分

根据 [#10](https://github.com/xueyang-dev/Folith/issues/10) 验收标准，必须公开明确**“界面可选语言”与“经过端到端质量验证的语言对”**之间的边界：

### 3.1 界面可选（Selectable）
21 种语言均已加入客户端源/目标语言目录。目录、别名和语言标签可用；任务字段持久化及若干导出结构有组件测试。可选状态不等同于模型能力、字体覆盖或语言对质量已经验证。

### 3.2 组件及离线结构验证（不属于语言对质量验收）
`tests/language_expansion_e2e_test.py` 名称沿用既有命名，但其中没有执行完整翻译/独立审校管线，也没有调用真实模型。结合原有语言、OCR、准备页测试，可确认的范围为：
- [x] 21 种语言目录自称、代码、别名双向匹配 100% 通过；
- [x] 21 种语言 BCP-47 标签解析，以及英语/土耳其语标签的 TBX/TMX 导出结构样例；
- [x] 阿拉伯语、希伯来语、波斯语、乌尔都语、维吾尔语的 RTL 方向判定；
- [x] 乌尔都语、阿拉伯语、越南语、荷兰语、波兰语、乌克兰语的分句与标点支持；
- [x] 图像/VLM 读页语言提示词与缓存身份隔离；
- [x] 本机 Tesseract 缺失语言包拦截与报错提示；
- [x] 导出结构样例：阿拉伯语 PDF 字节生成、荷兰语 EPUB 语言/方向元数据。它们不构成所有新增目标语言的字体、搜索复制、原生阅读器或排版质量验收。

### 3.3 尚未完成的 #10 验收

本次没有新增通过真实模型质量验收的语言对。新增的九种目标语言（土耳其语、乌尔都语、波斯语、维吾尔语、希伯来语、荷兰语、波兰语、乌克兰语、越南语）仍需：

- [ ] 代表性文档的导入、画像、术语提取、翻译、独立审校及交付全流程验证，保留样本、模型配置、质量观察和失败案例。
- [ ] 真实扫描页识别、语言混淆检查及字体/分句/RTL 检查。
- [ ] 在实际 Word/EPUB 阅读器及适用 PDF 导出中验证新增语言，记录平台与字体限制。

#16/#17 的既有语言与 RTL 验收要求仍保留。目录实现及组件测试不能替代这些验收，也不自动关闭 #10。

### 3.4 已知局限与建议
- **模型支持差异**：不同大语言模型服务商（OpenAI, Gemini, DeepSeek, Qwen 等）对小语种的翻译质量存在差异。用户在开展大批量长文档翻译前，建议在「准备页」使用小样张进行试译。
- **随包字体与排版**：阿拉伯文、波斯文与乌尔都文在原生 PDF 渲染时依赖系统提供的 OpenType / TrueType 复杂字体；在缺少 Nastaliq 字体的系统上，乌尔都文将退回标准 Naskh 风格呈现。
