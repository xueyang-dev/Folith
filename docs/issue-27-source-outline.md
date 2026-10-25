# #27 — AI 准备开始前的只读原文结构

Issue: [#27](https://github.com/xueyang-dev/Folith/issues/27)（P1），里程碑
`v0.7.4 · Source Context & Translation Scope`。

## 基线（实现前）

`main` 在 `691e079`（PR #158 合并后）：

- 「准备」页右栏只有：语言选择、翻译模式、项目 / 术语 / 高级设置、交付内容；
  左栏 `DocumentOverview` 是第一页缩略图 + 页数 / 图片数 / 无文字层页提示
  （`folith/qt/doc_overview.py`、`folith/qt/doc_facts.py`）。
- 本地解析能力已存在但结果被丢弃：`extract_text_source`（EPUB / MD / TXT，
  `folith/text_import.py`）、core 的 DOCX 段落提取、`pdf_ingestion` 的版面
  段落重建，都没有任何界面在准备阶段把它们展示出来。
- 用户在点「开始翻译」之前**看不到原文写了什么**——issue 说的"不能让用户
  盲目确认导入"。

## 设计决定

- **只解析、不识别**：结构来自文字层与文档自带的章节信息（PDF 目录
  `get_toc`、DOCX 标题样式、EPUB spine 章节、Markdown `#` 标题、TXT 段落
  边界）。扫描页没有文字层就如实标「未识别」并解释视觉模型路径——本地
  绝不做 OCR 冒充已读出，也不调任何模型。
- **与 core 提取同口径**：`paragraphs` 与
  `core.extract_document_paragraphs_with_report` 在文字层路径出自同一批
  提取函数、逐字相同（测试钉死）。这让 #28 的翻译范围与 #61 的原位置定位
  可以直接消费这份结构与锚点，不需要每个界面再写一套源解析（issue 的
  scope ownership 要求）。
- **复用缩略图的后台与缓存机制**：结构解析走
  `folith.qt.thumbnails.PreviewLoader`（后台线程 + 按
  `(job_id, mtime, size)` 缓存）。同一份源文件重复打开直接命中缓存；
  源文件变了缓存自然失效，不会拿旧结构冒充新文件。
- **与左栏分工**：`DocumentOverview` 说"这份文档长什么样"（页数 / 图片 /
  注意事项），新区块说"里面写了什么"（章节 / 页 / 段落分组 + 原文预览）。
  区块放在语言选择之后、翻译模式之前——先看内容，再做决定。
- **收起时也有答案**：`DisclosureSection` 的常驻摘要一行说出
  "N 章 · M 段 · 本地读取，未调用模型"；扫描件则直说读不出文字。
- **开始后停用**：任务开始后区块禁用并指向工作台（原文对照在 #61 的
  上下文 / 原页面板），不再请求结构——这里是准备阶段的只读视图。

## 新增

`folith/source_outline.py`（模型无关，只读）：

- `parse_source_outline(filename, data)` →
  `{format, nodes, paragraphs, pages, chapters?, textless_pages, scanned,
  partial, encrypted?, total_paragraphs, total_chars, fingerprint}`；
  坏文件 / 未知后缀返回空结构，不抛错。
- `nodes`：`{id, kind(chapter|page|group), title, level, page_start, page_end,
  para_start, para_count, chars, sample, scanned, paragraphs}`——
  - PDF：有目录按一级目录章节（目录前的前置页单列）；没有目录按页分组
    （超过 400 个节点自动并页）；每页文字层检查与 `doc_facts` 同口径。
  - DOCX：Heading 1–6 / Title 样式分章；段落过滤与 core 完全一致。
  - EPUB：spine 章节分章（`extract_text_source(per_chapter=True)`）。
  - Markdown：`#` 标题分章（`_html_paragraphs` 新增标题登记）。
  - TXT：无结构时按连续段落分组（20 段一组，段落边界是真实结构）。
- `outline_fingerprint(outline)`：结构指纹——同一解析复算必相同，结构变化
  必不同；判断"这还是不是那份结构"的依据（位置编号 n0003 不跨解析稳定）。

`folith/qt/source_outline_panel.py`：`SourceOutlineSection`（「原文结构」
折叠区块）——结构列表（**只读**：可选中预览，无勾选位、不可编辑）+
原文预览（4000 字符封顶并说明）+ 状态行；扫描节点预览明确写
"【未识别】……开始翻译后由视觉模型逐页识别"。纯函数 `node_item_text` /
`node_preview_text` / `outline_summary` 与界面共用同一套措辞。

`folith/text_import.py`（两处增量，默认行为不变）：
`_html_paragraphs(markup, headings=None)` 登记标题块位置与层级；
`extract_text_source(..., per_chapter=False)` 可按 EPUB 章节分组返回。

`folith/qt/setup_panel.py`：`TaskSetupPanel` 在语言选择与翻译模式之间挂载
`SourceOutlineSection`，`load()` 时随视图请求结构（未开始的任务才请求）。

文案：5 个 locale（zh-CN / en / es / ru / ar）各 +21 条，
`scripts/qt_i18n_coverage.py` 全绿（缺 0、与原文相同 0）。

## 范围外

- 翻译范围选择 / 排除 / 预估扣减（#28，PR #160 的另一条线：它从提取后的
  IR 区块投影结构；本 PR 不与其耦合）。
- 工作台的片段到原文导航（#61，已合并）。
- MinerU / Docling、跨格式统一 Document IR、第二个用户可见状态存储
  （issue 明确排除）。
- PDF 页面图像预览（已有 `pdf_panel`，任务开始后可用）。

## 验证

`tests/source_outline_test.py`（15 项）：五种格式的结构识别（TXT 分组恰好
覆盖全部段落、Markdown 标题边界、EPUB spine 章节、DOCX 标题分章、PDF
目录 / 无目录 / 前置页）、**对齐契约**（DOCX / EPUB / PDF 文字层的
`paragraphs` 与 core 提取逐字相同）、扫描页如实（部分无文字层 / 整本扫描 /
加密 / 损坏文件都不装懂）、指纹稳定性、未知后缀与统一收尾。

`tests/qt_source_outline_panel_test.py`（7 项）：无需 API 的结构列表与原文
预览；只读（列表没有勾选位；浏览前后 `state.json` **逐字节不变**）；同一
源文件第二次请求同步命中缓存（缓存条目未被重算替换）；源文件缺失如实说明
不停在"读取中"；任务开始后禁用并指向工作台；`TaskSetupPanel` 集成
（区块在语言选择之后、翻译模式之前，标题「原文结构」）。

回归：`text_import_test`、`qt_task_setup_test`、`qt_doc_overview_test`、
`source_context_test`、`qt_source_panel_test` 共 76 项通过；
`scripts/qt_i18n_coverage.py` 五种语言全绿。全部在 macOS offscreen Qt 平台
运行。

## 诚实限制

- 上述 Qt 验证全部是 macOS offscreen 平台；没有在原生 macOS / Windows
  桌面上做人工走查（含真窗口、主题、RTL 阿拉伯语界面的实际渲染）。
- 「不产生模型计费」以代码路径论证 + 状态文件逐字节不变佐证：解析链路
  （`parse_source_outline` → `text_import` / `pdf_ingestion` / python-docx /
  PyMuPDF 文字层与目录）不含任何网络调用；没有也无法在这里展示真实
  账单。
- 超长 PDF 的逐页文字层检查只看前 2000 页（与 `doc_facts` 同口径），
  `partial` 如实标记。
