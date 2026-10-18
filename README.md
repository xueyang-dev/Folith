# Folith · 译页

<p align="center">
  <img src="folith/resources/brand/folith-lockup.png" alt="Folith·译页 — Agentic Translation Workspace — 智能体翻译工作台" width="840">
</p>

<p align="center">
  <strong>面向长文档与专业本地化的智能体翻译工作台 (Agentic Translation Workspace)</strong>
</p>

<p align="center">
  <a href="https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0"><img src="https://img.shields.io/badge/release-v0.6.0-blue.svg" alt="Release v0.6.0"></a>
  <img src="https://img.shields.io/badge/macOS-15.7.5%2B%20·%20Apple%20Silicon-lightgrey.svg" alt="macOS 15.7.5+ Apple Silicon">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
</p>

---

**Folith · 译页** 是一个面向书籍、白皮书、学术专著等长文档的桌面翻译工作台。它把一份文档当作一项完整的工作来做：先通读全文、建立画像和术语表，再带着上下文分批翻译；独立审校逐段检查，人工改过的段落会重新审校；最后在交付门禁通过后，冻结成可追溯的交付包。

一次请求翻一段很容易，难的是第九章和第二章用词一致、改过的地方有记录、交出去的文件能说清楚来源。Folith 就是为这件事做的。

---

## 📸 界面预览

### 任务库
所有任务按项目分组，卡片直接显示原文首页缩略图、段数、语言和当前阶段；可以按状态筛选、全文搜索，中断的任务一键继续。

<p align="center">
  <img src="docs/assets/qt-01-tasks.png" alt="Folith 任务库" width="1000">
</p>

### 项目：术语库与翻译记忆
术语库和翻译记忆按项目保存。确认的术语在新任务中自动使用；通过审校的句对进入本项目的翻译记忆，不会混进无关项目。支持 XLSX / TBX / TMX 导入导出。

<p align="center">
  <img src="docs/assets/qt-02-projects.png" alt="Folith 项目术语库" width="1000">
</p>

### 翻译工作台
原文与译文逐段对照，顶部步骤条显示「准备 → 翻译 → 审校 → 交付」进度。右侧是当前段落的操作：审校、点评、按要求重译、提问、加入术语库、批注。

<p align="center">
  <img src="docs/assets/qt-03-workbench.png" alt="Folith 翻译工作台" width="1000">
</p>

### 交付门禁
还有未审校段落或必须处理的问题时，交付被拦下并说明原因；也可以先导出当前译文副本。

<p align="center">
  <img src="docs/assets/qt-04-delivery-gate.png" alt="Folith 交付门禁" width="1000">
</p>

### 冻结交付
审校完成后冻结为不可变的交付版本，带交付清单；也可以从已完成的译文补生成 PDF、EPUB、TMX、TBX、JSONL，不必重新翻译。

<p align="center">
  <img src="docs/assets/qt-05-delivery-ready.png" alt="Folith 冻结交付" width="1000">
</p>

> 截图由 `scripts/qt_showcase_screenshots.py` 用合成的演示文档生成，不含真实用户文件。

---

## ⚡ 核心特性

- **📚 长文档导入**：PDF / DOCX / EPUB（无 DRM）/ TXT / Markdown。扫描页交给视觉模型阅读，本机 Tesseract 只负责给出每一行的位置。
- **🧠 全书上下文**：翻译前先做文档画像与术语抽取；每一批段落都带着前后文发送，跨页的代词、缩写和术语能对上。
- **💎 项目级语言资产**：术语锁定后注入每一次请求；翻译记忆按项目复用已审校的句对，减少重复调用。
- **🔍 独立审校与跨段检查**：审校模型对照原文逐段检查，能看到相邻段的译文；按句分组成批，同一句不会被拆开。问题按类别和严重程度分级，只有阻断性问题会拦住交付。
- **🛡️ 人工责任闭环**：人工修改如实记录，改过的段落在采信前重新审校；任务状态随时落盘，中断后从最后完成的批次继续。
- **📦 可追溯的交付**：纯译文 DOCX（保留原文图片）、双语对照 DOCX、EPUB、PDF、术语表、TMX、JSONL，以及 `delivery_manifest.json` 交付清单；冻结后的版本不可修改。
- **🎓 学术模式**（默认关闭）：把翻译过程、案例对比与反思导出为实践报告，适合翻译硕士（MTI）等评估场景。

---

## 🚀 安装

### macOS 桌面客户端（推荐）

从 [v0.6.0 Release](https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0) 下载 `Folith-Qt-0.6.0-macos-arm64.dmg`，拖进「应用程序」。

- 系统要求：macOS 15.7.5 或更高，Apple Silicon。
- 当前版本未签名、未公证：第一次打开时，在「应用程序」里右键点 Folith → 打开，放行一次即可。**不要**全局关闭 Gatekeeper。
- 打开后在「设置 → 模型」里填入模型服务商的 API Key。

支持范围与已知限制见 [桌面端发布说明](docs/qt-beta/release-notes.md)。

### 从源码运行

```bash
git clone https://github.com/xueyang-dev/Folith.git
cd Folith
python -m pip install -e .
python -m pip install PySide6
python -m folith.qt
```

需要 Python 3.10+。离线识别扫描件时另装 [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)；没有 Tesseract 时，扫描页会完全交给视觉模型处理。

### Python 包

```bash
python -m pip install foliothread-0.6.0-py3-none-any.whl   # 从 Release 下载
```

代码模块名为 `folith`，发行包名沿用 `foliothread`。Pip 安装的 `folith` 命令启动的是旧的 Streamlit 界面（保留一个版本，之后移除），不是上面的桌面客户端。

---

## ⚙️ 模型与服务商

- **内置预设**：OpenAI、Gemini、DeepSeek、OpenRouter、SiliconFlow（硅基流动）、Moonshot / Kimi、Zhipu / GLM、Qwen / DashScope、OpenCode Go。
- **自定义网关**：任何兼容 OpenAI API 的网关或本地模型服务（vLLM / Ollama）。
- **多条连接**：翻译、审校、视觉可以各用一条已保存的连接；视觉连接只在任务含扫描页时才校验。
- **模型目录**：OpenAI 兼容服务商与 Gemini 可以自动获取可用模型；实际能否调用以「测试连接」为准。
- **思考强度**：支持推理模型（如 DeepSeek-R1、OpenAI o 系列），可调节或关闭思考。

---

## 🌐 界面语言

界面默认简体中文。English、Español、Русский、العربية 为实验功能，界面文案已齐全，但尚未完成独立语言审阅和完整的阿拉伯语 RTL 验收。详见 [外观与多语言边界](docs/i18n/README.md)。

---

## 🔄 工作流程

| 阶段 | 做什么 |
| :--- | :--- |
| **1. 准备** | 提取段落、排除页眉页脚等噪声；扫描页由视觉模型阅读；文档画像、术语候选、项目术语与记忆 |
| **2. 翻译** | 按模式（快速 / 标准 / 精细）分批，带前后文与锁定术语；进度随时落盘，可暂停、继续 |
| **3. 审校** | 独立审校逐段检查，跨段对照相邻译文；人工编辑、重译、提问；改过的段落重新审校 |
| **4. 交付** | 门禁核验完整性与阻断问题；冻结为不可变版本；导出译文、双语、术语、记忆与交付清单 |

<p align="center">
  <a href="docs/assets/folith-workflow.html">
    <img src="docs/assets/folith-workflow.svg" alt="Folith · 译页 长文档本地化与资产工作流程" width="1000">
  </a>
</p>

---

## 🗺️ 路线图

- [x] **v0.4.0**：统一品牌、语言资产与术语中心、交付门禁、草稿实时容灾
- [x] **v0.6.0**：Qt 原生桌面客户端成为主入口；EPUB / TXT / Markdown 导入；扫描页以视觉模型为主；项目内的术语与翻译记忆；多条模型连接；跨段审校；译文 DOCX 保留原文图片；外观跟随系统
- [ ] **下一步**：签名与公证；DOCX 原格式写回（保留原始排版与样式）；翻译记忆模糊匹配；文档跨版本增量翻译

---

## 📖 文档

- [桌面端发布说明](docs/qt-beta/release-notes.md)
- [发布计划](docs/release-plan.md)
- [项目与长期记忆](docs/project-memory.md)
- [译文 EPUB / DOCX 导出](docs/epub-export.md)
- [OCR 与视觉阅读](docs/ocr-subsystem.md)
- [架构与能力边界](docs/architecture-boundaries.md)
- [变更日志](CHANGELOG.md)

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。
