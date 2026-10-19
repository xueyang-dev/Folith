# 《علم الدين》PP-OCRv5 阿拉伯语微调实验

> 2026-10-17：此实验已停止，本地训练源码、环境、模型和实验产物已删除，
> 项目也已移除 `paddle` 安装依赖组。下文仅保留历史方法和结果，所链接的本地产物已不可用。

2026-10-03。**已训练并导出可运行模型，书内留出页面上有明显改善；同类其他古书未验证。**

## 实际结果

以下是完整页面经过现有 OCR 适配器后，在未参加训练的第 **6、221 页**的人工
标注片段上计算的结果。共 20 行、1263 个规范字符、229 个词，按字符/词数加权汇总。
没有 LLM 清理或翻译介入。三者使用同一份 350-DPI RGB 页面与同一修正后的参考范围。

| 模型 | CER | WER | 字符删除次数 |
|---|---:|---:|---:|
| 原版 Paddle Arabic | 28.03% | 64.63% | 318 |
| 此书微调 Paddle | **9.74%** | **41.92%** | **55** |
| Tesseract best / PSM 6 | 23.91% | 56.77% | 190 |

微调相对原版的字符错误率下降 **65.25%**。两页分别为：

| PDF 页 | 原版 Paddle CER | 微调 CER | Tesseract best CER |
|---|---:|---:|---:|
| 6 | 33.47% | 10.45% | 31.88% |
| 221 | 19.92% | 8.68% | 12.03% |

另有两页验证集，用于选择 checkpoint：原版 Paddle CER 28.69%，微调 9.49%。
测试集不参与梯度更新或 checkpoint 选择。

在相同的人工检查行图像上直接运行识别器，测试 CER 为 49.16% → 10.52%。
这与完整页面的 28.03% → 9.74% 是**不同输入裁切**的两个实验，不能交叉比较。
静态导出模型与动态 checkpoint 在全部 38 个验证/测试行上的文字输出逐行一致。

证据：[原生逐页与逐行结果](../benchmark/paddle-finetune/native-evaluation.json)、
[训练过程与 checkpoint 选择](../benchmark/paddle-finetune/run-01.json)。

## 如何训练

- 基础权重是官方可训练的 `arabic_PP-OCRv5_mobile_rec_pretrained.pdparams`，
  没有把推理权重当作训练 checkpoint，也没有从随机参数开始。
- 官方训练源码固定在 PaddleOCR `v3.7.0`，commit
  `b03f46425e8ff4442b268ce449e3eef758146cd4`；实际模型参数全部与官方配置对应。
- 保留字符字典与官方 Arabic CTC 解码器。人工标签保持逻辑 Unicode；训练目标通过
  官方解码器的逆变换生成视觉 CTC 顺序，并检查往返一致，没有改写应用的 RTL 逻辑。
- 冻结图像 backbone、未使用的 NRTR 分支及 BatchNorm 统计，只更新 CTC 序列编码器
  与分类器，共 **658469** 个参数。固定 backbone 特征预计算，降低本机 CPU 训练成本。
- 学习率 `5e-5`，Adam，梯度累积 4，梯度范数限制 5，每训练行加入一次轻微对比度/
  模糊增强。增强样本继承原页分组，没有跨组复制。
- 训练 20 epoch，按验证 CER 选中第 **2** epoch。继续训练后验证误差变大，保留较早
  checkpoint；没有因为训练损失下降而选择最后一轮。
- CPU 训练含特征准备约 **37.94 秒**，进程峰值 RAM **1344.63 MiB**。
  不含源码/权重下载、导出与原生页面推理。没有使用 GPU 或付费计算资源。

实际代码：[数据准备](../benchmark/prepare_paddle_finetune.py)、
[训练](../benchmark/train_paddle_arabic.py)、
[原生验收](../benchmark/evaluate_paddle_finetune.py)。
上游依据：[微调说明](https://www.paddleocr.ai/v3.7.0/en/version2.x/ppocr/model_train/finetune.html)、
[阿拉伯语模型与训练权重](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html)。

## 数据与参考范围修正

现有 10 页人工转写得到 89 个独立行图像，按固定随机种子 `20261003` **整页划分**：

| 分组 | PDF 页码 | 使用的真实行数 |
|---|---|---:|
| 训练 | 21、51、101、170、181、212 | 41 |
| 验证 | 8、141 | 18 |
| 测试 | 6、221 | 20 |

训练页原有 51 行，10 行因记录的转写歧义而排除。增强副本不增加独立标注数量。
OCR 文字只辅助匹配图像行，训练标签来自已有人工转写；没有以 Tesseract 输出或
Internet Archive 的坏文本层充当真值。10 页的行图像 contact sheet 已逐页查看。
两处明确转写错误只在新数据集纠正：第 51 页 `ضفته` → `صفته`，第 212 页
`منع الخير` → `منبع الخير`，保留原标签及修正记录。

核对图像发现旧基准的部分 y 窗口与 GT 范围不一致：第 6、51 页漏收边界行，
第 141 页多收下一行。这会把裁切错误计入识别误差。
复用原完整 OCR 输出、修正参考范围后，10 个片段的算术平均为：

| 原版引擎 | 原报告 CER / WER | 修正参考范围与两处转写后的 CER / WER |
|---|---:|---:|
| Tesseract best | 21.67% / 54.12% | **18.71% / 52.46%** |
| Paddle Arabic | 26.34% / 60.33% | **24.03% / 58.74%** |

原始结果保留；原版 Tesseract 优于原版 Paddle 的结论仍成立。修正范围的收益
没有算进微调收益：本报告的原版/微调/对照均使用同一修正参考范围。
十片段平均值也不能直接与两页留出集的加权值比较。

证据：[数据与逐行标签](../benchmark/paddle-finetune/dataset-manifest.json)、
[旧基准范围修正](../benchmark/paddle-finetune/reference-scope-correction.json)、
[数据隔离/对齐测试](../tests/ocr_finetune_dataset_test.py)。

## 已完成的使用验证

微调模型经过现有 Folith 原生进程适配器，完整页面检测、行排序与坐标转换均可运行。
第 221 页进入生产 intake 后得到 21 个单元，确定性测试翻译 provider、Word 与
双语 Word 导出、原文行高亮均成功；再次命中 OCR 缓存约 **0.059 秒**，未加载模型。
这证明流程衔接，未评估真实翻译质量。

禁止 socket 联网后，已缓存的实验模型仍能完成推理。清晰 Arabic/Latin 混排 fixture
CER 从 13.43% 变为 10.45%，西文 `123` 恢复，英文姓名字符完整，但逻辑片段顺序
仍有错误：期望 `مرحبا Ali Smith`，输出 `Ali Smith مرحبا`。该问题没有被字符串补丁
掩盖。未修改的独立英文识别器仍为 CER/WER 0。

证据：[实际 intake/导出/缓存](../benchmark/paddle-finetune/intake/paddle-intake-result.json)、
[实际原文高亮](../benchmark/paddle-finetune/intake/paddle-intake-preview.png)、
[独立模型包的离线与混排结果](../benchmark/paddle-finetune/packaged-controls/results-offline.json)。

## 模型位置与使用

> 本节及以下命令为历史记录：`benchmark/paddle-models/alam-al-din-finetuned/` 等本地产物
> 已随实验清理而**不可用**，`paddle` 安装依赖组已移除，Folith 也不再提供 PaddleOCR 手动选择。

自包含模型目录为 `benchmark/paddle-models/alam-al-din-finetuned/`，内含新的 Arabic
推理权重/图/YAML、原有检测器和原有英文识别器的独立副本，外加 `model-card.json`。
原始权重与默认 Tesseract 选择保持原状。训练 checkpoint 在
`.audit/paddle-finetune/run-01/best.pdparams`，完整模型与依赖为本机文件，不提交二进制。

在安装了可选 Paddle runtime 的环境启动 Folith 时，将已有资源路径指向此目录：

```bash
FOLITH_OCR_MODEL_DIR="$PWD/benchmark/paddle-models/alam-al-din-finetuned" \
  .venv/paddle/bin/python -m streamlit run app.py
```

在 PDF OCR 设置中选择 PaddleOCR。引擎型号名称仍为 PP-OCRv5；证据中的模型资产
摘要记录实际微调权重，页面缓存会区分原始/微调模型。旧任务需要明确重新扫描
才能更新已保存的原文与翻译单元。

## 复现

准备独立的可选 runtime，避免升级基础应用环境。此处用本次验证的 `.venv/paddle`。

```bash
git clone --depth 1 --branch v3.7.0 \
  https://github.com/PaddlePaddle/PaddleOCR.git .venv/ocr-training-src
.venv/paddle/bin/python -m pip install -r benchmark/paddle-finetune/requirements-training.txt
mkdir -p .audit/paddle-finetune/pretrained
curl --fail --location \
  https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/arabic_PP-OCRv5_mobile_rec_pretrained.pdparams \
  --output .audit/paddle-finetune/pretrained/arabic_PP-OCRv5_mobile_rec_pretrained.pdparams
.venv/paddle/bin/python benchmark/prepare_paddle_finetune.py
```

核对 `.audit/paddle-finetune/dataset/review-page-*.png` 与 manifest 标签后，在 manifest
记录人工核对并将 `review_required` 改为 `false`，训练程序才会运行：

```bash
.venv/paddle/bin/python benchmark/train_paddle_arabic.py
.venv/paddle/bin/python .venv/ocr-training-src/tools/export_model.py \
  -c .audit/paddle-finetune/run-01/export.yaml
.venv/paddle/bin/python benchmark/evaluate_paddle_finetune.py \
  --adapted-root benchmark/paddle-models/alam-al-din-finetuned
FOLITH_OCR_MODEL_DIR=benchmark/paddle-models/alam-al-din-finetuned \
  .venv/paddle/bin/python benchmark/run_paddle_smoke.py --offline \
  --output-dir benchmark/paddle-finetune/controls
```

新导出权重应放入独立模型根目录的
`paddle/3.7.0/official_models/arabic_PP-OCRv5_mobile_rec/`；检测器/英文识别器保持原版。
重新实验用新的 output/evidence 目录，保留本次 checkpoint 与测试记录。

## 验收边界

这是**一本书的少样本适配**。只用了 6 页的 41 条可靠训练行，测试仅两页的片段；
没有证明全书 390 页都能达到 9.74%，更没有证明其他印刷体或其他古书能达到该数值。
原人工 GT 的历史字形/拼写不确定性仍适用。

若要形成同类古书模型，需要补充多本书、不同字体/纸张/退化情况的可靠行标注，
并按整本书留出测试；官方通用微调指南建议在字典不变时准备约 5000 条识别样本。
这个建议针对更可靠的领域适配，不能用增强副本替代真实覆盖。
训练未改检测器，原 CPU 检测内存成本仍适用；混排逻辑顺序也仍需独立解决。

本次新增 4 项数据隔离/对齐测试及关联回归合计 **89 项通过**，原生训练、静态导出、
留出页面、独立模型包的离线推理与完整 intake 均实跑。
见[验证记录](../benchmark/paddle-finetune/verification.json)及
[回归日志](../benchmark/paddle-finetune/regression.log)。
生产应用代码没有改动；本次没有重新运行上一阶段 138 文件全量
回归。此前完整回归的通过记录保留，本次重点验证训练及实际模型接入。
