# Optional PaddleOCR 与 OCR 子系统工程报告

2026-10-03。结论：**PASS WITH KNOWN LIMITATIONS**。三项工程门禁完成；
Paddle 是可选本地后端，Tesseract 保持默认。这本古书的识别质量没有因换用
PP-OCRv5 而整体改善，不能将本次集成描述成古书 OCR 质量问题已经解决。

后续[此书微调实验](paddle-arabic-finetuning.md)核对出部分 GT 裁切边界不一致。
修正参考范围及两处明确转写后，原版引擎的十片段平均 CER 为 Tesseract **18.71%**、
Paddle **24.03%**，排序不变。下文旧数值保留为集成时的历史测量；最新质量与
微调后的独立留出测试见微调报告。

## A. Existing OCR Architecture Found

修改前已追踪实际调用链并建立[证据图](ocr-subsystem.md)：
`app` 配置/快速画像 → `core.run_job_pipeline` / `rescan_pdf_source` →
`extract_document_paragraphs_with_report` → PDF 文本层质量判断 → PyMuPDF 渲染 →
Tesseract TSV → RTL 词/段落重建 → 页面缓存 → 原文清理 → 分段 → 翻译 → 导出。

渲染为 RGB，标准/高质量/历史扫描为 220/300/350 DPI；没有额外图像预处理。
Tesseract 默认 PSM 3，没有显式 OEM。现存坏阿拉伯语文本层被错误直接使用的问题
**已经修复**，本次没有重新实现它。
证据：[文本层质量判断](../folith/text_layer_quality.py)、
[入口契约测试](../tests/ocr_ingestion_contract_test.py)、
[真实页面入口报告](../benchmark/paddle-intake-result.json)：触发原因为
`text_layer_quality_rejected`。

## B. Architecture Changed

只保留一条生产流程。共享渲染 → `resolve_backend` → `recognize_page` →
规范证据 → 共享页面后处理/清理/分段/翻译/导出。
Tesseract 实现提取到独立模块；Paddle 的运行时、模型及进程隔离集中在适配器。
没有云 OCR、VLM、双引擎识别、投票或按质量自动切换。

证据：[路由与 TesseractAdapter](../folith/ocr_backends.py)、
[`OCRBackend` 接口](../folith/ocr_evidence.py)、
[`core._ocr_pdf_text_with_warnings`](../core.py)、
`test_paddle_runs_shared_segmentation_translation_export_and_resume`
（[测试文件](../tests/ocr_subsystem_test.py)）。

## C. New OCR Contract

`OCRPageResult`、`OCRBlock`、`OCRRuntimeInfo` 使用项目现有 dataclass/dict 风格，
页面报告作为文档级汇总。保留页号、原始/规范文本、行、可用词框、几何、语言/
文字系统/方向、模型/版本/设备、预处理、DPI、计时、警告和错误。
像素矩形按 `72 / DPI` 映射到 PDF 坐标；Paddle 原始四边形也保留。
Paddle 原生 0–1 分数与 Tesseract 0–100 词均值分别标明 `confidence_kind`；
没有校准或强制换算。旧清理接口对 Paddle 收到未知置信度。

证据：[`OCRPageResult.to_record` / `OCRBlock.to_contract_dict`](../folith/ocr_evidence.py)、
`test_native_paddle_confidence_is_not_tesseract_confidence`
（[测试](../tests/ocr_subsystem_test.py)）。

## D. Tesseract Preservation

Gate A：修改前、抽取后各有 116 项相关测试通过。
识别调用、PSM、字面制表符解析、RTL 词序、段落拼接、88 分清理阈值保留。
真实页索引 20/100/169 的 TSV 金样验证文本、段落边界、置信度和纵向几何。
10 个标注片段重新测得 CER **21.6714%**、WER **54.1175%**，与历史最佳配置一致。

新增横向框和行/词证据；修正了原来将像素位置与 PDF 点高度比较的页边判断。
这是坐标契约修复，不是识别算法改写。
证据：[原 Tesseract 实现](../folith/ocr_tesseract.py)、
[金样](../tests/fixtures/ocr_tesseract_preservation.json)、
`test_real_tesseract_goldens_preserve_text_boundaries_and_confidence`
（[测试](../tests/ocr_subsystem_test.py)）。

## E. PaddleOCR Integration

Gate B：本地 `PaddleOCR.predict`，没有调用托管服务。
验证了实际安装的 3.7.0 API 与模型注册表：阿拉伯语注册代码 `ar` 对应
`PP-OCRv5_server_det` + `arabic_PP-OCRv5_mobile_rec`；英文对应
`en_PP-OCRv5_mobile_rec`。构造器传入明确模型名和本地目录。
检测最大边 960，CPU 两线程，关闭文档旋转/去扭曲/行旋转，保留全部识别分数。

一会话一个 spawned 原生进程、一个模型实例、一个 OCR worker。
加载/下载和推理均可取消；进程崩溃成为页面错误，下一页可重建进程。
Python 异常按运行时、下载、模型加载、设备、图片、推理、解析、内存与超时分类；
普通 UI 收到简短错误，完整技术异常进入日志。

证据：[`PaddleOCRBackend.load` / `_load_local` / `_paddle_worker`](../folith/ocr_paddle.py)、
`test_native_worker_exit_is_isolated_and_next_page_restarts`、
`test_cancel_during_model_initialization_closes_worker`
（[测试](../tests/ocr_subsystem_test.py)）。

## F. RTL / Arabic Handling

Tesseract 保留自己的 TSV 重建。Paddle 仅按行几何排序，不使用 Tesseract 的词序逻辑，
不反转 Unicode 字符、数字或 Latin 片段。
纯阿拉伯语、西文数字、阿拉伯数字、英文姓名、标点、短标题和同行片段均有回归测试。

实测也揭示限制：清晰混排页 CER **13.43%**，西文 `123` 遗漏、英文姓名
`Ali Smith` 损坏/错序、冒号遗漏。纯阿拉伯语行、阿拉伯数字及行序正常。
适配器保留模型输出供审阅，没有用字符串补丁掩盖模型错误；UI 对这些限制有提示。
历史样本的“整行/整词反转”启发式计数为 0，**不能证明混排片段完全正确**。

证据：[行排序](../folith/ocr_order.py)、
[混排图像](../benchmark/paddle-smoke/arabic-mixed.png)、
[真实 OCR 输出](../benchmark/paddle-smoke/arabic-mixed-ocr.txt)、
[实测结果](../benchmark/paddle-smoke/results-offline.json)。

## G. Dependency & Cross-platform Design

Base 依赖不包含 Paddle。`pyproject.toml` 的可选 `paddle` extra 精确固定
PaddleOCR **3.7.0**、PaddleX **3.7.2**、PaddlePaddle CPU **3.3.1**。
当前 Python 下限 3.10 不变；此可选运行时接受 3.10–3.13。
PyPI 发布 CPU wheels 覆盖 macOS arm64、Windows amd64、Linux x86_64。
实际运行验证在 macOS Apple Silicon / Python 3.11.3；Windows/Linux 原生运行未验证。

CPU 为本次支持基线；不支持此 extra 下的 GPU，也不要求 CUDA。
独立验证环境成功安装 `.[paddle]`，`pip check` 无依赖冲突，原有基础环境未升级。
现有冻结桌面包仍采用 Tesseract；没有交付 Paddle 冻结包或 GUI 组件安装器。
冻结入口加入了 spawn 的 `freeze_support`，最终可选组件打包仍需独立验收。

证据：[依赖定义](../pyproject.toml)、[实际 wheels](../benchmark/paddle-wheel-compatibility.json)、
[安装日志](../benchmark/paddle-install.log)、[资源路径](../folith/desktop/paths.py)、
[冻结入口](../gui.py)。
上游：[本地 OCR 文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)、
[PP-OCRv5 多语言模型](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html)、
[CPU wheels](https://pypi.org/project/paddlepaddle/3.3.1/#files)。

## H. Cache / Persistence / Recovery

共享页面缓存身份含文档、页、引擎、模型、包版本、模型权重/图/YAML 摘要、推理配置、
语言、DPI、预处理版本、PSM 与方向。摘要每适配器/会话读取一次，用来决定是否
重用昂贵 OCR；设置和诊断不计算摘要。两引擎、模型图更新、配置改变不会串缓存。

成功页原子保存，失败页不冒充成功页。保存的选择在恢复时优先；旧任务缺省
Tesseract。实际模型身份、失败列表与缓存引用在 `extraction_report.ocr`；
`source_paras` 在清理之前落盘。旧状态可读，无法证明模型身份的旧 OCR 缓存会重算。
Paddle 清理提示词使用独立 checkpoint 身份，Tesseract 原有清理指纹保留。
没有改变翻译记忆缓存。

证据：[`OCRPageIdentity` / `OCRPageCache`](../folith/ocr_cache.py)、
[`run_job_pipeline` / `rescan_pdf_source`](../core.py)、
[清理 checkpoint](../folith/source_cleanup.py)、
`test_cancel_resume_only_recognizes_missing_paddle_pages`、
`test_old_task_without_engine_resumes_as_tesseract`、
`test_model_graph_changes_invalidate_asset_identity`
（[测试](../tests/ocr_subsystem_test.py)）。

## I. UI & Observability

新建任务与重新扫描的既有 OCR 设置中增加引擎选择；默认仍为 Tesseract。
Auto 只在 Tesseract 可执行文件缺失时改用 Paddle，并保存原因。
显式 Paddle 缺失/不支持时直接显示可操作错误，不默默降级。
正常标签没有“更高质量”宣传；型号细节放在已有进度/诊断证据中。

沿用现有进度、页面计时和工作详情。另修复实测发现的性能桥接问题：第一次保存时的
OCR `running` 状态不再盖掉最终完成状态；最终性能文件保留实际引擎元数据和页面计时。
扫描原文预览使用本次缓存身份的 OCR 行框；清理改写后找不到对应短语则不定位。

证据：[UI](../app.py)、[原文定位](../folith/pdf_preview.py)、
[Profiler 桥接](../folith/performance.py)、[最终性能输出](../folith/trace.py)、
`test_profiler_bridge_updates_ocr_completion_and_keeps_native_spans`
（[测试](../tests/performance_trace_test.py)）。

## J. Tests

全量运行覆盖 **138 个测试文件**，初轮品牌检查失败后完成修复与关联复跑。
最终合并验证为 **1812 项 pytest 通过、4 项已有资源/环境依赖测试跳过、1 个脚本式
app 启动检查通过、0 个失败文件**。跳过项为冻结桌面探测 3 项、case portfolio 1 项。
4 项原生 Tesseract Arabic smoke 使用现有基准模型与字体补跑通过；
原生 Paddle CPU 实测单独执行。完整记录与各次复跑来源见
[验证清单](../benchmark/ocr-verification.json)。

最后关联回归覆盖文本原生 PDF 绕过 OCR、Tesseract 保留、可选依赖缺失、UI、RTL、
双引擎缓存、取消/恢复、原生进程退出、旧任务、清理 checkpoint、性能、翻译及导出。
原生 Arabic/English CPU 推理另行执行；禁止 socket 联网的离线验证也通过。

品牌检查异常来自临时第三方运行时及历史技术路径。运行时已放入既有忽略目录，
基准 JSON 使用可移植路径；兼容引用按既有逐文件/逐行白名单归类，并增加测试证明
普通旧品牌文案仍会失败。没有放宽品牌断言或降低无关业务测试要求。

证据：[全量记录](../benchmark/ocr-full-regression.tsv)、
[关联复跑](../benchmark/ocr-release-verification.tsv)、
[OCR 专项测试](../tests/ocr_subsystem_test.py)、
[品牌约束测试](../tests/brand_regression_test.py)。

## K. Historical Arabic Benchmark

390 页原书取 20 页，两个适配器读取完全相同的 350-DPI RGB 图像。
10 页有固定 GT 片段，另 10 页只用于性能及定性检查。GT 的古字形/拼写不确定性
保留在原来的[说明](../benchmark/ground_truth/gt_meta.json)中。
CER/WER 是 10 个片段的算术平均；不声称对全书具有代表性。

| 指标 | Tesseract best / PSM 6 | PP-OCRv5 Arabic |
|---|---:|---:|
| CER | 21.6714% | 26.3446% |
| WER | 54.1175% | 60.3346% |
| 缺行 / 多行 | 2 / 0 | 2 / 4 |
| 整行/整词反转启发式计数 | 0 | 0 |
| 页面失败 | 0 | 0 |

部分页面 Paddle 更好，整体更差；保留 Tesseract 默认有实测依据。
对照不包含 LLM 清理或翻译。纯英文 fixture 的 115 个规范字符、21 个词均正确。
证据：[基准程序](../benchmark/run_engine_comparison.py)、
[数值与逐页结果](../benchmark/engine-comparison/results.json)、
[原生 smoke 程序](../benchmark/run_paddle_smoke.py)。

## L. Performance

| 测量 | Tesseract | Paddle CPU |
|---|---:|---:|
| 第一页 | 2.525 s | 11.459 s |
| 后续 19 页平均 | 3.431 s | 12.913 s |
| 20 页识别累计 | 67.708 s | 256.812 s |
| 原生进程峰值 RAM | 158.64 MiB | 4431.38 MiB |

Paddle 新进程/已缓存模型加载为 **4.555 s**，与页面计时分开，未计首次下载。
CPU 单 worker，GPU/VRAM 未使用。内存每 20 ms 采样；Tesseract 统计排除了已经加载
但闲置的 Paddle 进程。测试机 16 GiB Apple Silicon，当时有其他回归工作，速度为
该环境下的观测值。不能直接承诺 390 页的完成时间。

初始未约束检测配置曾卡住并被系统终止；加入 960 最大边后 20 页稳定完成。
这项尺寸约束可能影响小字检测，未证明它适合所有文档。

最终原书第 21 页生产链路：**18.895 s**，20 原始段、20 单元、20 测试译文，Word
与双语 Word 导出成功，原页高亮成功。再次匹配缓存 **0.119 s**，模型加载 **0 s**。
该链路使用真实 Paddle OCR、既有确定性测试翻译 provider，关闭 LLM 清理，
没有远程请求或真实翻译质量评估。
证据：[生产链路程序](../benchmark/run_paddle_intake.py)、
[链路结果/性能文件](../benchmark/paddle-intake-result.json)、
[实际高亮](../benchmark/paddle-intake-preview.png)。

## M. Remaining Risks

- PP-OCRv5 对这本历史印刷体整体质量较差，混排数字/姓名存在已复现错误；本次没有
  通过后处理掩盖或宣称修好这些模型错误。
- 检测内存仍约 4.33 GiB；原生进程隔离保护主程序，但系统整体内存不足仍会导致
  工作进程终止。已完成页可恢复。
- 多栏、复杂表格、竖排及强旋转页面没有独立排版模型验收；当前排序仅按行几何。
- 未验证 Windows/Linux 原生推理、GPU 或 Paddle 冻结桌面组件；已验证 wheels、
  支持状态边界、CPU 和 spawn 隔离，并明确保留打包验收边界。
- Paddle 置信度未校准。清理开启时未知置信度会使更多段进入现有 LLM 清理；
  基准表仅计算 OCR 成本。
- LLM 改写后无法逐词保证精确原文定位；本次只提供有短语证据的行级高亮。

## N. Files Changed

本次修改限于以下文件；工作区已有的大量桌面/UI/历史基准改动保留。

- 新增：`folith/ocr_evidence.py`、`ocr_order.py`、`ocr_tesseract.py`、`ocr_paddle.py`。
- 修改：`folith/ocr_backends.py`、`core.py`、`app.py`、`folith/pdf_preview.py`、
  `folith/source_cleanup.py`、`folith/performance.py`、`folith/trace.py`、`gui.py`、
  `pyproject.toml`、`.gitignore`、`README.md`。
- 测试：新增 `tests/ocr_subsystem_test.py`、3 个真实 TSV fixture 和对应 JSON 金样；
  修改 `tests/ocr_backend_boundary_test.py`、`tests/performance_trace_test.py`、
  `tests/brand_regression_test.py`。
- 文档：`docs/ocr-subsystem.md`、本报告。
- 基准：新增三个运行程序、引擎对照/原生 smoke/生产链路结果、wheels 记录和验证日志。
  下载权重与验证环境被忽略；原始本地路径证据在 `.audit/` 保留，公开基准路径可移植。

## O. Final Verdict

**PASS WITH KNOWN LIMITATIONS**。

Gate A 保留验证通过；Gate B 可选 Paddle 通过统一契约并接入共享流程；Gate C
完成依赖/模型/缓存/恢复/故障隔离/性能/真实基准/回归与架构审计。
引擎分支集中于 OCR 路由、适配器和已有 OCR 配置 UI；分段、翻译、导出没有引擎分叉。
Tesseract 继续默认，显式选择不暗中降级。

此结论针对 OCR 子系统集成；不等同于古书质量问题解决，也不等同于所有平台的
Paddle 桌面发布包已经通过发布验收。
