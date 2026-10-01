# 阿拉伯语与任意语言互译及 RTL 修复

2026-10-01。本次实现移除了新建任务目标语言的固定列表限制，修复工作台及 DOCX、PDF、EPUB 的 RTL 排版，并补充阿拉伯语问号分句。这里的“任意语言”表示应用接受任意目标语言名称或语言代码，使用同一条翻译流程；各语言的真实翻译质量取决于所选模型，不能由本轮模拟响应测试证明。

## 实现结果

- 新建任务保留常用语言选项，并允许直接输入其他语言名称或语言代码；自定义值在页面重新运行后保留。Chromium 中已实际输入并保留 `हिन्दी`。
- 原文和译文独立确定方向：优先使用已声明的 RTL 语言或语言代码，包含明确脚本的代码按脚本方向处理；无法识别语言名称时，依据 Unicode 强方向字符的占比决定。该判定只用于排版，不改写译文或翻译记忆的语言身份，也不会拒绝不在常用列表中的语言。
- 工作台原文、译文编辑器、上下文预览、术语及翻译决策的片段分别设置方向；原文编辑、拆分和合并弹窗的各类文本框也分别使用原文、译文方向。中文导航及原文／译文列顺序保留。
- DOCX 单语、双语、带标注双语及清洗后原文导出设置段落 `w:bidi`，RTL 段落使用 `w:jc="start"` 沿书写起始边对齐；复杂文字字体指定为 Arial。交付包将任务语言传给各导出函数，英文开头或全部为保留词的阿拉伯语段落也不会丢失 RTL 方向。
- PDF 明确指定各段落方向并使用起始边对齐，移除覆盖段落方向的 `unicode-bidi: plaintext`。英文前缀的阿拉伯语样本现在从右侧起排。
- EPUB 正文、目录及封面设置方向，spine 设置对应翻页方向；保留原文的图注使用原文方向，避免继承目标语言方向。`Arabic` 和现代标准阿拉伯语的常见名称可映射为 `ar` 元数据。
- sentence 分句识别阿拉伯语问号 `؟`，规则版本更新为 5。已锁定分段的任务保留原有单元；验收样本明确使用锁定的 paragraph 模式，避免与旧任务迁移混淆。

Word 的段落方向会影响段落对齐及缩进，因此没有用单纯右对齐替代 RTL。相关定义见 [Microsoft 的 BiDi 文档](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.bidi?view=openxml-3.0.1)。本次还用 left、right、start、end 四种方式生成对照文件，实际渲染确认 RTL 的起始边对齐。

## 验证结果

98 项针对性测试通过，覆盖新增 RTL 测试、现有工作台编辑与单元操作、分段、EPUB、空译交付及交付快照。随后对验收脚本新增的方向断言单独执行核心检查。未运行整个仓库测试集。

新增语言测试包含法语、德语、西班牙语、日语、韩语、印地语、孟加拉语、泰语、土耳其语、斯瓦希里语、希腊语、希伯来语、波斯语和乌尔都语，各自与阿拉伯语双向测试请求和响应，共 28 个组合。中、英、俄与阿拉伯语的原始 48 组样本也重新执行了读取、保存、编辑、搜索及导出检查。所有模型响应均为测试替身，不代表模型译文质量。

视觉及实际浏览器检查：

| 检查 | 结果 |
| --- | --- |
| 英→阿工作台 | 8 个译文编辑器实测 RTL、右对齐；英文原文保持 LTR |
| 阿→英工作台 | 8 个阿拉伯语原文实测 RTL、右对齐；英文编辑器保持 LTR |
| 自定义目标语言 | 浏览器中输入印地语后保留；AppTest 验证重新运行仍保留 |
| Word 单语与双语 | 英→阿样本经 LibreOffice 渲染并逐页查看，RTL 起排及换行后的短行靠右 |
| PDF | 英→阿样本逐页查看，英文前缀段落靠右；另有 PDF 几何位置回归测试 |
| EPUB | 导出 XHTML 的 Chromium 预览实测 RTL；包结构测试验证 RTL 翻页元数据 |

修复后的截图、机器结果及导出复现材料保存在 `exports/arabic-rtl-fixed-20261001/`，之前的 `exports/arabic-acceptance-20261001/` 保留为修复前证据。

## 仍需验证的范围

真实模型的翻译及独立语义审校、Microsoft Word、实际 EPUB 阅读器、多页长文、扫描件 OCR、学术报告导出和所有语言组合尚未完整验证。本机 LibreOffice 的中文表头和标题缺字仍是字体环境限制，未把它当作本次 RTL 修复已解决的项目。

此前记录的阿拉伯语／俄语源语残留启发式覆盖边界没有在本次修改中扩展。确定性检查没有 findings 仍不代表翻译质量合格；不得将此结果代替语义审校。

重复运行：

```bash
python3 -m pytest -q tests/arabic_rtl_test.py tests/translation_workbench_ui_test.py tests/translation_workbench_test.py tests/segmentation_test.py tests/epub_export_test.py tests/missing_translation_delivery_test.py tests/delivery_snapshot_test.py
python3 scripts/arabic_acceptance_audit.py --out /tmp/folith-arabic-acceptance
```

验收脚本现在会对 DOCX 的 RTL 段落、起始边对齐、EPUB 页面方向和问号分句作断言；不再仅记录这些观测值。浏览器、Word 的渲染检查仍需单独执行。
