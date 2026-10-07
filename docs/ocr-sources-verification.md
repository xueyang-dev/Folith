# 三路 OCR 来源实测记录

2026-10-03。本文只记录**实际跑过**的检查与结果，包括请求消耗与没有做的部分。
配置与架构见[三路 OCR 来源：配置与架构](ocr-sources.md)。

## 1. 这次验证要发现什么失败

| 检查 | 如果实现有问题，会看到什么 |
| --- | --- |
| 自动化契约测试 | 来源选择静默回退、缓存跨模型/跨连接混用、失败页被当成成功页写入、凭据进入快照或报告、Tesseract 旧缓存失效 |
| 本地真实识别（Tesseract / Paddle） | 适配器没读到真实图像、任务级模型选择没有传到 worker（结果仍是官方权重）、语言范围限制没生效 |
| 线上真实请求（OpenCode Go） | 图像没有送进请求、响应结构校验/截断检测失效、错误分类错误（把认证错误当可重试）、用量或失败状态丢失 |
| 下游接入 | 无坐标结果在页边过滤中被误删、原页定位报“找不到”、分段/翻译/导出拿不到文字 |
| 请求预算 | 跑成整本书、重复付费请求 |

## 2. 自动化测试（固定响应，不依赖真实密钥或收费接口）

OCR 相关测试集合（含既有回归）共 **228 项通过、4 项跳过**：

```sh
venv/bin/python -m pytest tests/ocr_online_backend_test.py tests/ocr_sources_test.py \
  tests/ocr_sources_downstream_test.py tests/ocr_online_credentials_test.py \
  tests/ocr_subsystem_test.py tests/ocr_backend_boundary_test.py tests/pdf_ocr_test.py \
  tests/ocr_page_cache_test.py tests/ocr_ingestion_contract_test.py \
  tests/ocr_language_rtl_test.py tests/ocr_tsv_quote_test.py tests/ocr_arabic_smoke_test.py \
  tests/ocr_finetune_dataset_test.py tests/ocr_vision_pilot_test.py \
  tests/pdf_preview_test.py tests/pdf_preview_ui_test.py -q
```

全量回归（`venv/bin/python -m pytest tests -q`）：**1942 通过、7 失败、10 跳过**。
这 7 项失败与本次改动无关，已验证：

- 去掉本次新增的 4 个测试文件后重跑全量回归，**同样的 7 项**失败（1896 通过）；
- `tests/smoke_test.py` 单独运行时 47 项全通过，两项失败是既有的跨测试全局状态泄漏；
- `tests/brand_regression_test.py`、`tests/red_team_acceptance_test.py` 报出的文件全部是
  既有文件（`benchmark/ocr-vision-pilot/*.json`、`docs/desktop-*`、`folith/qt/dialogs.py`）；
- `tests/desktop_handshake_test.py` 的 3 项失败位于工作区中未跟踪的
  `folith/desktop/`（`is_descendant(pid, 1)` 返回 `None`），与 OCR 无关。

覆盖的关键行为：

- **线上后端**（26 项）：成功转写只产生文字证据（`geometry_known=false`、置信度
  `unknown`）；空响应/非 JSON/拒绝转写/缺少 `break_after`/行号不连续/全空 → 页面
  失败；`finish_reason="length"` 判为截断失败；429 重试并累加用量；401/400 不重试；
  503 重试到上限；超时记录“用量未知”；取消后不再发新请求；凭据不出现在 `repr`、
  描述、运行时信息或请求记录里；缓存身份区分目标/模型/输出上限/推理模式。
- **来源与缓存**（26 项）：`auto` 永不选择线上；显式线上缺少配置时明确报错；
  **Tesseract 旧的缓存 key 字节级不变**；不同 Paddle 模型/不同线上配置互不复用页面；
  任务级模型选择以**参数**形式到达 worker（含资源目录与 `allow_download`）；
  书籍模型缺资源或语言超出范围时状态明确且不可用；**坐标未知的重复行不会被页边
  过滤删掉**；无坐标页能在原页视图中定位（返回页面、没有矩形）；失败页不落盘。
- **下游链路**（3 项）：线上 OCR 结果（固定响应）经原文校对（离线替身）→ 分段 →
  翻译（离线替身）→ DOCX 导出；任务快照中没有密钥；恢复复用缓存页（0 次新请求）；
  重扫改用不同线上模型会使旧译文失效；任务界面提供三来源、Paddle 模型选择与线上
  连接设置。
- **连接独立性**（3 项）：OCR 连接文件独立且权限 0600；翻译模型变更后任务保存的
  OCR 服务/模型/地址不变（引用翻译凭据时只更新密钥）；状态检查不发起识别请求。

## 3. 真实识别

脚本：`benchmark/run_ocr_sources_verification.py`。固定页面：PDF 第 6、121、221、301 页
（`page_index` 5、120、220、300），350 DPI，OCR 语言 `ara`。准确率复用
`benchmark/ocr-vision-pilot/dataset.json` 中**已固定的参考片段**（从不发给任何模型）：
本地来源按 pilot 的像素窗口从整页结果中截取，线上来源用同一窗口的区域裁图
（线上没有坐标，这是唯一同口径的比较方式）。

| 来源 | 实际后端与模型 | 4 片段 CER | WER | 本表对应请求 | 4 页耗时 | 完成/失败 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Tesseract 5.5.0 + `tessdata_best/ara`，PSM 3 | `tesseract-lstm` | 34.42% | 68.17% | 0（本地） | 20.0 s | 4 / 0 |
| Paddle PP-OCRv5 + `alam-al-din-finetuned` | `PP-OCRv5_server_det+arabic_PP-OCRv5_mobile_rec@alam-al-din-finetuned` | **9.01%** | 39.72% | 0（本地） | 51.9 s | 4 / 0 |
| 线上 OCR（区域裁图，2 页） | `deepseek-v4-flash-vision-exp`（服务返回同名） | **0.60%** | 3.17% | 2 次付费请求 | — | 2 / 0 |

“本表对应请求”只统计这张表用到的请求；完整的真实请求账目（含整页转写与推理探针）
见第 4 节与 `request-ledger.json`。

逐页片段 CER：

| PDF 页 | Tesseract | Paddle 微调 | 线上（裁图） |
| ---: | ---: | ---: | ---: |
| 6 | 58.60% | 10.45% | 未测（预算） |
| 121 | 14.81% | 8.02% | 0.31% |
| 221 | 25.05% | 8.68% | 未测（预算） |
| 301 | 13.62% | 7.25% | 0.87% |

说明：

- 这些窗口是 pilot 参考行的**并集框**，可能包含相邻行；因此上表适合**来源之间互比**
  （同一窗口），不能与 `docs/paddle-arabic-finetuning.md` 里修正范围后的数值直接比较。
  第 6 页窗口偏大，很可能把邻近行算进参考，Tesseract 该页 58.60% 主要来自窗口口径。
- Paddle 一行在本机真实加载了**微调权重**（模型身份字符串与证据里的 `model_id`
  都是 `alam-al-din-finetuned`），这证明任务级模型选择真的传到了 worker；若仍是官方
  权重，CER 会接近 24–28%。
- 线上片段 CER 只覆盖第 121、301 两页，因为每次裁图请求都要计入请求预算（见第 5 节）。

### 整页完整性与阅读顺序（PDF 第 121 页）

没有任何整页人工参考，所以这里检查的是“整页有没有漏、顺序对不对”：把 6 行固定参考
按模糊相似度（≥0.7）对齐到整页识别出的逻辑行。

| 来源 | 整页行数 | 参考行命中 | 顺序保持 | 整页字符数 |
| --- | ---: | ---: | --- | ---: |
| Tesseract | 21 | 0 / 6 | 是 | 962 |
| Paddle 微调 | 22 | 6 / 6 | 是 | 968 |
| 线上 OCR | 21 | 6 / 6 | 是 | 995 |

Tesseract 命中 0/6 是它在这本书上古阿拉伯语输出太差（相似度低于阈值），不是因为整页
文本为空——同一页它有 21 行、962 字符。线上与 Paddle 都是 6/6 且顺序一致，字符数接近
（995 / 968），没有出现整段缺失。

线上整页输出保留扫描页码单独成行（例如第 121 页首行 `١٣٣`）。因为该来源没有坐标，
实现不会把它当页边文字删除；已有原文校对阶段负责处理这类页码行。这是有意的取舍。

## 4. 请求消耗

- 4 个不同页面，全部在同一天、同一连接、同一模型下完成；没有跑整本书。
- 逐次请求与超预算说明见
  [`benchmark/ocr-sources-verification/request-ledger.json`](../benchmark/ocr-sources-verification/request-ledger.json)：
  **实际共 11 次识别请求**（首轮 7 次：4 整页 + 2 裁图 + 1 推理探针；补充字段后重跑
  4 次整页），比 8 次上限多 3 次。多出的原因不是多跑了页面，而是脚本每次重建页面
  子集 PDF，PyMuPDF 会写入新的文件 id 与时间戳，导致 OCR 源哈希变化、页面缓存未命中。
  已修复：子集 PDF 现在只构建一次并缓存在 `.audit/ocr-sources-verification/`，重复运行
  为纯缓存命中（实测第二次运行 `cached_pages=4`、0 次新识别）。
- 可对账的 token 用量（服务实际返回）：

| 用途 | prompt | completion | reasoning | total |
| --- | ---: | ---: | ---: | ---: |
| 4 页整页转写（重跑那一轮） | 4,588 | 3,202 | 未返回细项 | **7,790** |
| 第 121 页区域裁图 | 851 | 212 | — | 1,063 |
| 第 301 页区域裁图 | 851 | 219 | — | 1,070 |
| 第 121 页推理探针（服务默认思考） | 876 | 755 | 542 | 1,631 |
| 可对账小计 | | | | **11,554** |

  首轮整页的用量未能保留（被重跑覆盖），所以总额只能给出下界。接口不返回金额，
  **费用未知**，不做估算。
- 关于输出截断与推理参数（依据 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)，
  图像输入格式依据 [DeepSeek 视觉文档](https://api-docs.deepseek.com/guides/vision/)）：
  服务默认启用思考；本实现默认不发送任何推理参数，只有显式选择“关闭思考”时才发送
  `extra_body={"thinking": {"type": "disabled"}}`。真实探针显示服务默认思考也能在
  `max_tokens=8192` 下正常结束（`finish_reason=stop`，542 个 reasoning token），
  同一裁图的片段 CER 为 3.09%，而关闭思考时为 0.31%——**单个样本，不能据此下结论**。
  截断本身由 `finish_reason=="length"` 判定并把该页标为失败（自动化测试覆盖）。

## 5. 哪些环节调用了真实外部服务

| 环节 | 是否调用真实外部服务 |
| --- | --- |
| Tesseract / Paddle 本地识别 | 否（本机进程） |
| 线上 OCR 请求（OpenCode Go 视觉接口） | **是**，共 11 次，见上 |
| 原文校对（LLM cleanup）真实模型 | **否**。真实验证脚本只跑到 OCR 原文；下游链路由自动化测试用确定性替身覆盖 |
| 分段 / 翻译 / DOCX 导出 | **否**。使用 `tests/offline_provider.py` 的确定性替身，不调用任何外部翻译服务 |
| 页面缓存、原页定位、识别报告 | 否（本地文件与 PyMuPDF） |

## 6. 已知限制与未验证

1. 线上片段只实测 2 页（预算限制），整页只做了完整性与顺序检查，没有整页人工参考。
2. 线上整页依赖服务把图像缩到约 1300×1300 像素；密排小字是否有系统性漏字未验证。
3. 请求预算被超出 3 次（原因与修复见第 4 节）；后续复跑为缓存命中，不再产生请求。
4. `deepseek-v4-flash-vision-exp` 已被上游标记为退役别名（由最新 Flash 模型承接）；
   实现按服务返回的 `model` 字段记录实际模型，别名的长期可用性不由本项目保证。
5. 线上缓存目前只由自动化测试证明“同配置复用、不同配置不复用”；真实多轮缓存复跑
   因为第 4 节的子集重建问题没有做成（修复已合入，但为避免继续消耗请求未再实测付费链路）。
6. 没有验证：自动候选融合、按质量切换来源、专用 OCR 厂商适配器、整本书线上识别的
   成本与准确率、其他书籍上的 Paddle 微调模型。

## 7. 证据文件

- `benchmark/ocr-sources-verification/results-tesseract.json`
- `benchmark/ocr-sources-verification/results-paddle.json`
- `benchmark/ocr-sources-verification/results-online.json`
- `benchmark/ocr-sources-verification/summary.json`
- `benchmark/ocr-sources-verification/request-ledger.json`
- 复现：`TESSDATA_PREFIX=$PWD/benchmark/tessdata_best venv/bin/python benchmark/run_ocr_sources_verification.py --source tesseract`；
  `TMPDIR=$PWD/.audit/tmp .venv/paddle/bin/python benchmark/run_ocr_sources_verification.py --source paddle`；
  `venv/bin/python benchmark/run_ocr_sources_verification.py --source online --reasoning-mode thinking_disabled --crop-pages 120,300 --probe-reasoning-page 120`；
  `venv/bin/python benchmark/run_ocr_sources_verification.py --source summarize`。
