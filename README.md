# Folith · 译页

<p align="center">
  <img src="folith/resources/brand/folith-lockup.png" alt="Folith·译页 — Agentic Translation Workspace — 智能体翻译工作台" width="840">
</p>

<p align="center">
  <strong>面向长文档与专业本地化的智能体翻译工作台 (Agentic Translation Workspace)</strong>
</p>

<p align="center">
  <a href="https://github.com/xueyang-dev/Folith/releases"><img src="https://img.shields.io/badge/candidate-v0.5.0-orange.svg" alt="Candidate v0.5.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
</p>

---

**Folith · 译页** 是一套专为复杂、长篇幅文献与技术文档设计的本地化智能体工作台。不同于通用对话界面的简单改写，Folith 将**文档结构保真提取**、**全书上下文建模**、**术语与翻译记忆（TM）闭环**、**智能体双语翻译**、**深度人工审校**与**可审计交付资产**整合在一条可中断、可恢复的工程化流水线中。

无论是数百页的学术专著、技术白皮书、行业报告还是产品本地化资料，Folith 都能保持全篇概念与用词的高度一致，完整保留译者决策证据链，保障高水准成果交付。

---

## 📸 界面预览 (Showcase)

### 1. 多项目管理中心 (Project Center)
集中管理不同领域的本地化项目，各项目独立沉淀术语规范与翻译记忆；支持按状态快速检索、任务归档与资产汇总。

<p align="center">
  <img src="docs/assets/01-projects-preview.png" alt="Folith 多项目管理中心" width="1000">
</p>

### 2. 新建任务向导与上下文配置 (New Task Wizard)
结构化四步创建向导（文档与画像 → 翻译策略 → 交付内容 → 确认运行），支持 PDF / DOCX / EPUB / TXT / Markdown 拖拽上传、目标语言选择、项目上下文关联与智能画像识别。

<p align="center">
  <img src="docs/assets/02-new-task-preview.png" alt="Folith 新建任务向导" width="1000">
</p>

### 3. 智能体翻译工作台 (Translation Workbench)
双语段落平行对齐，顶部横幅清晰展示进度、术语数与阻断状态；右侧实时提供段落事实、Agent 智能改写、术语核验与快捷编辑动作。

<p align="center">
  <img src="docs/assets/03-workbench-preview.png" alt="Folith 智能体翻译工作台" width="1000">
</p>

### 4. 智能体审校与质检工作区 (Review & Quality Gate)
结构化审校队列与问题分类（术语冲突、句式偏离、格式异常），即时展现上下文解释与建议译文，支持采纳、修正与决策归档。

<p align="center">
  <img src="docs/assets/04-review-preview.png" alt="Folith 人工深度审校工作台" width="1000">
</p>

### 5. 交付门禁与成果导出 (Delivery & Quality Verification)
在正式导出前对翻译完整性、独立审校、合规状态与结构保真进行全景门禁核验；阻断项未解决前严格防护，支持生成双语对照与交付清单。

<p align="center">
  <img src="docs/assets/05-delivery-preview.png" alt="Folith 交付门禁与成果导出" width="1000">
</p>

### 6. 任务流水线与历史看板 (Task History & Resume)
统一展示所有执行中的翻译任务与历史归档，提供状态流转标签、进度读数与快捷恢复动作；意外中断的任务可秒级接续断点。

<p align="center">
  <img src="docs/assets/06-history-preview.png" alt="Folith 任务流水线与历史看板" width="1000">
</p>

### 7. 语言资产库：术语与翻译记忆 (Language Assets & Terminology)
跨任务沉淀高价值语料，支持术语锁定、作用域隔离、推荐译法与多格式（XLSX / TBX / TMX）导入导出，形成越用越准的资产飞轮。

<p align="center">
  <img src="docs/assets/07-language-assets.png" alt="Folith 语言资产与术语管理" width="1000">
</p>

---

## ⚡ 核心特性 (Key Features)

- **📚 长文档结构感知与保真提取**：针对 PDF 与 DOCX 进行深度结构解析，支持跨页排版恢复、断词修复、页眉页脚与无意义噪音频段排除，支持本地 Tesseract OCR 离线识别。
- **🧠 动态上下文与跨章节一致性**：引入滑动上下文窗口与章节摘要索引，翻译当前段落时智能检索前后文与专有名词，杜绝“断章取义”与术语前后矛盾。
- **💎 语言资产闭环 (TM & Terminology)**：内置精确匹配与归一化翻译记忆复用；支持项目术语抽取、锁定与精准注入，大幅削减大模型 Token 开销与无关幻觉。
- **🛡️ 状态解耦与人工责任闭环**：保存译文与审校确认严格分离，段落经人工编辑后自动触发重新审校校验；任务状态实时落盘，遭遇浏览器刷新或意外中断可秒级恢复。
- **📦 标准化交付与过程证据包**：支持纯译文 DOCX、双语对照 DOCX、重点标注版、[保留内嵌图片的流式译文 EPUB](docs/epub-export.md)、术语表、TMX 记忆库、结构化 JSONL 数据集以及 `delivery_manifest.json` 交付清单。
- **🎓 专用研究与报告工作流**：支持将翻译过程、案例对比、理论支持与反思复盘导出为结构化分析与实践报告，满足翻译硕士 (MTI) 等学术与专业评估需求。

---

## 🔄 工作流程 (Architecture)

<p align="center">
  <a href="docs/assets/folith-workflow.html">
    <img src="docs/assets/folith-workflow.svg" alt="Folith · 译页 长文档本地化与资产工作流程" width="1000">
  </a>
</p>

| 阶段 | 核心任务与工程能力 |
| :--- | :--- |
| **1. 文档解析与上下文** | 版面分析、段落提取、噪音频段排除、离线 OCR（无网络隐私泄露）、轻量结构化纠偏与上下文切片 |
| **2. 术语与资产准备** | 自动化术语候选抽取、置信度评分、人工锁定/冻结、多格式术语导入导出 (XLSX / TBX) |
| **3. 智能体翻译** | 动态上下文组装、相关术语精准注入、断点缓存保存、顺序翻译（默认并发 1），可选审校重叠流水线默认关闭 |
| **4. 人工审校与质检** | 规则质检（漏译、占位符、标点、术语一致性）、问题定位导航、编辑-重审联动闭环 |
| **5. 交付与资产沉淀** | 交付门禁校验、多格式成果物导出、已确认译文入库 (TMX)、全生命周期清单落盘 |

---

## 🚀 快速上手 (Quick Start)

界面默认使用简体中文。English、Español、Русский、العربية 为**实验功能**，四语各已补齐 1668/1668 条界面文案；尚未完成独立语言审阅及完整阿拉伯语 RTL 验收。中文 README 为当前维护版本，多语言界面不代表文档或模型输出已本地化。详见 [外观与多语言边界](docs/i18n/README.md)。

### 系统要求
- Python 3.10 或更高版本
- macOS / Windows / Linux
- （可选）[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)（如需离线识别扫描件 PDF）
- 扫描页主流程使用视觉模型理解原文，本机 Tesseract 提供原文位置坐标。Paddle 实验环境、模型和训练产物已清理，项目不再提供 `paddle` 安装依赖组；[历史微调报告](docs/paddle-arabic-finetuning.md)仅供参考。
- （可选）线上 OCR API：任务里可显式选择 OpenAI 兼容的图像转写接口（独立于翻译模型配置，可引用已有凭据）。默认关闭，只在明确选择时使用；配置与边界见[三路 OCR 来源](docs/ocr-sources.md)，实测与请求消耗见[实测记录](docs/ocr-sources-verification.md)。

### 0.5.0 Qt 受邀 Beta

本轮桌面候选为 **macOS Apple Silicon / macOS 15.7.5+** 的 Qt DMG；尚未正式发布。
中文为主界面，其他语言为实验功能。源码开发入口：

```bash
python -m pip install -e .
python -m pip install PySide6
python -m folith.qt
```

Pip 的 `folith` 命令仍启动保留一个版本的旧界面。Qt Beta 范围、验收条件与已知限制见
[发布计划](docs/release-plan.md)和[人工验收清单](docs/qt-beta/manual-acceptance.md)。

### 方式 1：通过 Pip 安装发布包（推荐）

```bash
# 当前公开发布版 v0.4.0：从 Releases 下载后安装
python -m pip install foliothread-0.4.0-py3-none-any.whl
foliothread  # v0.4.0 compatibility command

# 0.5.0 候选版从源码构建（见下方方式 2）；正式发布后使用：
# python -m pip install foliothread-0.5.0-py3-none-any.whl
# folith
```

### 方式 2：从源码运行与开发

```bash
# 克隆仓库
git clone https://github.com/xueyang-dev/Folith.git
cd Folith

# 安装依赖
python -m pip install -e .

# 启动工作台
folith
```

代码模块使用独立的 `folith` 包；发行包名为 `foliothread`。0.5.0 移除旧 TransPraxis 包和启动命令，升级后请使用 `folith` 或 `foliothread`。已有任务和配置继续保存在 `outputs/`。

仓库根目录提供一键启动脚本：
- **Windows**: 双击运行 `start.bat`
- **macOS**: 双击运行 `start.command` 或在终端运行 `./start.sh`
- **Linux**: 运行 `./start.sh`

> **参数说明**：
> - 仅启动后台服务不打开浏览器：`folith --no-browser`
> - 局域网协同模式：`folith --lan`
> - 原生窗口包装：安装可选依赖 `python -m pip install ".[desktop]"` 后，`folith` 会用 pywebview 窗口显示同一个 Streamlit 界面（不是下方的 Qt 客户端）。

`folith` 命令与 `start.*` 脚本启动的是旧 Streamlit 启动器（`gui.py`），0.5.0 起保留一个版本；Pip 安装说明不适用于 Qt 桌面客户端。

### 方式 3：macOS 桌面客户端（Qt，0.5.0 受邀 Beta）

0.5.0 Beta 的桌面入口是 Qt Widgets 原生客户端，以 `Folith-Qt-0.5.0-macos-arm64.dmg` 分发：未签名、未公证，仅限受邀测试；最低 macOS 15.7.5，仅 Apple Silicon。支持 PDF / DOCX / EPUB / TXT / Markdown 导入，覆盖准备 → 翻译 → 审校 → 交付，语言资产与学术专业模式也已提供。支持范围与已知限制以 [Qt 桌面端发布说明](docs/qt-beta/release-notes.md) 为准。

```bash
# 从源码运行同一客户端（需另装 PySide6）
python -m pip install PySide6
python -m folith.qt          # 或 python qt_client.py
```

| 入口 | 启动的界面 | 以哪份说明为准 |
| :--- | :--- | :--- |
| `Folith-Qt-0.5.0-macos-arm64.dmg`、`python -m folith.qt`、`qt_client.py` | Qt 桌面客户端 | [Qt 桌面端发布说明](docs/qt-beta/release-notes.md) |
| `folith` 命令、`start.sh` / `start.command` / `start.bat` | 旧 Streamlit 启动器（保留一个版本） | [CHANGELOG](CHANGELOG.md) |

旧的 Streamlit + pywebview 桌面壳（`Folith-0.5.0-macos-arm64.dmg`）已退役，[其发布记录](docs/desktop-d3/release-notes-0.5.0.md)只作历史基线。

---

## ⚙️ 模型与服务商支持 (Providers)

Folith 采用模块化模型接入架构，在系统设置中支持开箱即用配置：

- **主流服务商**：OpenAI、Gemini、DeepSeek、OpenRouter、SiliconFlow (硅基流动)、Moonshot / Kimi、Zhipu / GLM、Qwen / DashScope、OpenCode Go
- **自定义网关**：支持任何遵循 OpenAI API 规范的第三方兼容网关或本地模型服务 (vLLM / Ollama)
- **深度思考控制**：支持带思维链的推理模型（如 DeepSeek-R1、OpenAI o-series），可自定义推理强度

---

## 🗺️ 路线图 (Roadmap)

- [x] **v0.4.0**：统一 Folith 品牌体系、交互界面重构、语言资产与术语中心、交付门禁系统与草稿实时容灾
- [ ] **v0.5.0（候选）**：独立 Folith 代码包、性能与恢复改进、EPUB 交付和实验性外观/语言设置
- [ ] **后续版本**：DOCX 原格式保真写回（基于 XML 模板的原位段落替换，保留原始排版、样式与内嵌图表）
- [ ] **v0.6.0**：TM V2 引擎（支持多语料库检索与模糊匹配评分、增强型 TMX 分段索引）
- [ ] **v0.7.0**：文档跨版本差分更新与增量翻译同步

---

## 📖 详细文档 (Documentation)

- [智能体交互面板契约 (Agent Inspector)](docs/agent-inspector-workspace.md)
- [历史任务与状态迁移](docs/history-task-list.md)
- [项目与长期记忆模型](docs/project-memory.md)
- [DOCX Contract v1 规范](docs/docx-contract-v1.md)
- [架构设计与能力边界](docs/architecture-boundaries.md)
- [变更日志 (Changelog)](CHANGELOG.md)

---

## 📄 开源许可证 (License)

本项目基于 [MIT License](LICENSE) 开源。

### 自动获取模型目录

AI 引擎设置页对 OpenAI 兼容服务商和 Gemini 显示“获取可用模型”。打开设置时自动读取服务商目录，浏览器会话内缓存一小时；刷新失败保留上次列表，修改密钥或地址后重新获取。OpenCode Go、OpenRouter 的公开目录不需要密钥；真实调用仍需有效的 Go 密钥。

Gemini 使用原生目录并分页读取，SiliconFlow 只读取 chat 模型；OpenAI 排除音频、图像及仅支持 Responses 的系列，OpenRouter 按文本输入/输出能力筛选。独立审校连接也可自动获取和手动刷新。服务商预设支持填写可选 API 地址，以使用官方提供的区域端点；切换审校服务商会清空旧连接字段。目录中的模型仍受账户权限限制，请用“测试连接”确认实际调用。

OpenCode Go 当前自动列出已接入 Chat Completions 的模型系列，包括 `deepseek-v4.1-flash`。其目录没有调用协议元数据，Responses/Messages 模型暂不在自动选择范围内。请求以 Folith 标识客户端并带稳定会话 ID；使用范围以[Go 官方说明](https://opencode.ai/v2/docs/console/go#where-can-i-use-it)为准。
