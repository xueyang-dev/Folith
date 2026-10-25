# #61 — 为 DOCX、EPUB、TXT 和 Markdown 源查看原位置

Issue: [#61](https://github.com/xueyang-dev/Folith/issues/61)（P2），里程碑
`v0.7.4 · Source Context & Translation Scope`。

## 基线（实现前）

`main` 在 `d949f34`：

- 「原位置」（检查器按钮，⌘/Ctrl+Alt+P 展开左栏）只对 PDF 任务可用：
  `WorkbenchView._load_pdf` 以 `adapter.pdf_available` 判断，其他格式直接
  `setVisible(False)`，没有任何解释。
- 流式源（DOCX / EPUB / TXT / Markdown）导入时源文件整体保存在任务的
  `source.bin`；EPUB 导出已有来源段落关联（`epub_source_paragraphs`），
  但工作台没有任何片段到源的导航。

## 设计决定

- **解释而非隐藏**：任务从可导入格式建立时，「查看原位置」按钮始终出现在
  操作栏；既没有 PDF 原页、也读不出上下文时按钮禁用，tooltip 说明边界
  （"原位置目前支持 PDF 原文，以及 DOCX、EPUB、TXT、Markdown 源文件的
  原文上下文……"）。
- **流式源给上下文而不是原样渲染**：回应 issue 的开放问题——采用带结构的
  纯文本，不保留字体 / 图片 / 分页。导入范围说明本来就不承诺原格式保真
  （#26），原格式写回不在此承诺。
- **定位与 PDF 同一条规矩**：复用 `pdf_preview` 的候选短语策略（整句优先、
  逐级缩短、从上次位置向后找再回卷），找不到时如实说"没能定位"，不猜。
  现有 `epub_source_paragraphs` 关联只覆盖未经编辑的旧任务，文本搜索对
  编辑过的任务同样成立，所以第一版统一走文本匹配。
- **同一入口、同一快捷键**：左栏 "source" 位由 PDF 显示原页、流式源显示
  上下文面板；Ctrl/⌘+Alt+P 与检查器按钮不变。

## 新增

`folith/source_context.py`（只读，无状态层写入）：

- `has_streaming_source` / `source_kind`：按任务 `filename` 后缀 + `source.bin`
  判断可用性。
- `load_blocks(job_id)`：解析源文件为顺序块（按 `source.bin` mtime 缓存，
  LRU 8 个任务）：
  - DOCX（python-docx）：按 body 顺序取段落；内置标题样式记层级并维护
    标题路径；表格按行以「单元格 · 单元格」文本呈现；
  - EPUB：`container.xml` → OPF → spine 顺序，每章用 `html.parser` 抽块级
    文本（p / h1–h6 / li / blockquote），章节名取章内第一个标题，退回文件名；
  - TXT / Markdown：块 = 非空行，带行号；Markdown 另记 `#` 标题路径并跳过
    代码块（导入也不翻译代码块）。
- `locate_segment(job_id, text, start_hint)`：返回
  `{kind, found, title, blocks, hint}`——匹配块前后各 2 个相邻块，匹配块带
  `highlight`；`title` 是章节名（EPUB）、标题路径（DOCX / Markdown）或
  行号（TXT / Markdown）。

`folith/qt/source_panel.py`：`SourcePanel`，镜像 `PdfPanel` 的结构——标题行 +
收起、「定位当前段」、位置说明（章节 / 标题路径 / 行号）、`QTextBrowser`
富文本展示，匹配块用与原页同一种暖黄高亮并滚动到它。解析整本书以秒计，
`load()` 在后台线程预热（缓存共享），完成前的定位先如实说"正在读取"。

适配器：`source_context_available` / `source_document_blocks` /
`source_format_known` / `segment_source_context`。

`WorkbenchView`：`_load_pdf` 同时判断原页与上下文可用性；`show_source_location`
按任务路由到 `pdf_panel.locate` 或 `source_panel.locate`；左栏切换、选中跟随、
定位去抖沿用原机制（`_source_widget()` 决定落到哪个面板）。

## 范围外

- 原始样式渲染、原格式保真（#26 / #28）。
- 只读源大纲与导入前的结构预览（#27）；本 issue 只做编辑时的片段-源导航。
- 图片、音视频在上下文中的呈现：不出现，也不声称出现。

## 验证

`tests/source_context_test.py`（7 项）：四种格式解析、标题路径 / 章节 / 行号、
表格文本化、代码块跳过、相邻段落、未命中与坏源文件如实说明。

`tests/qt_source_panel_test.py`（6 项）：适配器可用性与定位、面板定位高亮、
空任务、未匹配、工作台 DOCX 任务展开上下文面板、无源文件任务按钮禁用并解释。

`tests/qt_pdf_panel_test.py`：原「只有 PDF 任务才有入口」的断言按 #61 的新
契约更新为「PDF 走原页、DOCX 走上下文，入口共用」。

Windows（offscreen）上述 26 项全部通过；`qt_workbench_projection` /
`qt_client_window` / `qt_review_panel` / `qt_workbench_poll` /
`qt_segment_assist` / `qt_empty_states` / `epub_export` 回归通过。
