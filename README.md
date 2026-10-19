# Folith · 译页

<p align="center">
  <img src="folith/resources/brand/folith-lockup.png" alt="Folith·译页 — Agentic Translation Workspace — 智能体翻译工作台" width="840">
</p>

<p align="center">
  <strong>面向长文档与专业本地化的翻译工作台</strong>
</p>

<p align="center">
  <a href="https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0"><img src="https://img.shields.io/badge/release-v0.6.0-blue.svg" alt="Release v0.6.0"></a>
  <img src="https://img.shields.io/badge/macOS-15.7.5%2B%20·%20Apple%20Silicon-lightgrey.svg" alt="macOS 15.7.5+ Apple Silicon">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
</p>

<p align="center">
  <strong>简体中文</strong> | <a href="README.en.md">English</a> | <a href="README.ru.md">Русский</a> | <a href="README.es.md">Español</a>
</p>

> 简体中文 README 是持续维护的版本；其他语言版本由它翻译而来，如有出入以中文为准。

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

## 🚀 安装

### macOS 桌面客户端（推荐）

从 [v0.6.0 Release](https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0) 下载 `Folith-Qt-0.6.0-macos-arm64.dmg`，拖进「应用程序」。

- 系统要求：macOS 15.7.5 或更高，Apple Silicon。
- 当前版本未签名、未公证：第一次打开时，在「应用程序」里右键点 Folith → 打开，放行一次即可。**不要**全局关闭 Gatekeeper。
- 安装包已内置 Tesseract（英文、简体中文、阿拉伯文），不需要另装任何依赖。
- 打开后在「设置」里添加模型连接，见下面的快速上手。

### Windows

共享的 Qt 界面已经适配 Windows，但**目前没有 Windows 安装包，也没有完成 Windows 验收**。只能从源码运行，未经测试，不建议用于正式工作。

### 从源码运行（开发者）

```bash
git clone https://github.com/xueyang-dev/Folith.git
cd Folith
python -m pip install -e .
python -m folith.qt
```

需要 Python 3.10+，PySide6 已包含在依赖里。可选：另装 [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)，用于给扫描页定位每一行的位置；没有 Tesseract 时，扫描页会完全交给视觉模型处理。

### Python 包（面向开发者）

PyPI 上有发行包 `foliothread`（代码模块名为 `folith`），但它**不是获取应用的推荐方式**。PyPI 上的 0.6.0 仍然带着旧的 Streamlit 界面，`folith` 命令启动的是旧界面，而不是桌面客户端。旧界面已从源码中移除，下一个版本起 `folith` 命令会直接启动桌面客户端。在此之前，请使用上面的 DMG 或源码运行。

---

## ⚡ 快速上手

1. **添加模型连接**：打开「设置」，在「模型」里选择服务商、填入 API Key 和模型名，点「测试连接」，通过后保存。
2. **导入文档**：在任务库点「导入文档…」，选择 PDF / DOCX / EPUB / TXT / Markdown。
3. **选择模式**：在准备页选「快速」「标准」或「精细」。每张卡片会显示这份文档大约需要的调用次数、输入 token 和耗时。
4. **翻译并审校**：点「开始翻译」。翻译完成后在工作台逐段审校，处理右侧「检查与修复」里列出的问题。
5. **冻结交付**：问题处理完、门禁通过后，点「冻结并保存交付…」，选择保存位置，得到译文、双语对照、术语表、翻译记忆和交付清单。

---

## ⚡ 核心特性

| 阶段 | 做什么 |
| :--- | :--- |
| **准备** | 提取段落，排除页眉页脚等噪声；扫描页由视觉模型阅读，本机 Tesseract 只负责给出每一行的位置；生成文档画像、术语候选，并带入项目术语与翻译记忆 |
| **翻译** | 按模式（快速 / 标准 / 精细）分批，每一批都带着前后文和锁定术语；进度随时落盘，可暂停、继续 |
| **审校** | 独立的审校模型对照原文逐段检查，能看到相邻段的译文；人工编辑、按要求重译、对某一段提问；改过的段落重新审校 |
| **交付** | 门禁核验完整性与阻断问题；冻结为不可变版本；导出译文、双语、术语、记忆与交付清单 |


<p align="center">
  <img src="docs/assets/folith-workflow-zh.svg" alt="Folith 工作流程：准备 → 翻译 → 审校 → 交付" width="1000">
</p>

- **📚 长文档导入**：PDF / DOCX / EPUB（无 DRM）/ TXT / Markdown。
- **🧠 全书上下文**：翻译前先做文档画像与术语抽取；跨页的代词、缩写和术语能对上。
- **💎 项目级语言资产**：术语锁定后注入每一次请求；翻译记忆按项目复用已审校的句对，减少重复调用。
- **🔍 独立审校与跨段检查**：审校由单独的模型完成，不是翻译模型给自己打分；按句分组成批，同一句不会被拆开。问题按类别和严重程度分级，只有阻断性问题会拦住交付。
- **🛡️ 人工责任闭环**：人工修改如实记录，改过的段落在采信前重新审校；任务状态随时落盘，中断后从最后完成的批次继续。
- **📦 可追溯的交付**：纯译文 DOCX、双语对照 DOCX、EPUB、PDF、术语表、TMX、JSONL，以及 `delivery_manifest.json` 交付清单；冻结后的版本不可修改。
- **🎓 学术模式**（默认关闭）：把翻译过程、案例对比与反思导出为实践报告，适合翻译硕士（MTI）等评估场景。

「智能体」在这里的具体含义是：独立的审校角色、按你的指示重译某一段、就某一段向模型提问。它不会自作主张地改你的文档，所有改动都由你确认并留有记录。

---

## ⚙️ 模型与服务商

- **内置预设**：OpenAI、Gemini、DeepSeek、OpenRouter、SiliconFlow（硅基流动）、Moonshot / Kimi、Zhipu / GLM、Qwen / DashScope、OpenCode Go。
- **自定义网关**：任何兼容 OpenAI API 的网关或本地模型服务（vLLM / Ollama）。
- **多条连接**：翻译、审校、视觉可以各用一条已保存的连接；视觉连接只在任务含扫描页时才校验。
- **模型目录**：OpenAI 兼容服务商与 Gemini 可以自动获取可用模型；实际能否调用以「测试连接」为准。
- **思考强度**：支持推理模型（如 DeepSeek-R1、OpenAI o 系列），可调节或关闭思考。

---

## 🔒 隐私与数据

- **发给谁**：文档文本、扫描页图片会发送给**你自己配置的**模型服务商，由对方处理，适用对方的隐私条款。请先确认文档允许这样处理。
- **不上传其他内容**：应用本身没有遥测、统计或账号系统。除了模型服务商，唯一的网络请求是你点「检查更新」时读取 GitHub 上的最新版本信息，不会自动下载或安装。
- **数据在本机**：任务、术语、翻译记忆和设置保存在本机数据目录——macOS：`~/Library/Application Support/Folith/data`；Windows：`%LOCALAPPDATA%\Folith\data`。
- **API Key 怎么存**：保存在数据目录里的本地 JSON 配置文件中（权限 0600，仅当前用户可读），**不在系统钥匙串里**，也没有额外加密。请不要把数据目录同步到公共位置或分享给他人。

---

## 💰 费用

Folith 本身免费开源。翻译产生的模型调用费用由你和模型服务商结算，使用的是你自己的 API Key。准备页会按「快速 / 标准 / 精细」三种模式分别估算这份文档大约需要的调用次数、输入 token 与耗时，开始前可以对比；估算只是参考，实际用量取决于文档和所选模型。

---

## ⚠️ 已知限制

- DMG 仅支持 Apple Silicon 和 macOS 15.7.5 或更高；未签名、未公证。
- Windows 只有源码运行，没有安装包，未经验收。
- 不支持有 DRM 的 EPUB。
- 表格和文本框里的图片不会保留到译文 DOCX；双语对照 DOCX 不含图片。
- 不会写回原 DOCX 的版式，导出的是新生成的文件。
- 简体中文以外的界面语言属于实验功能。
- 扫描页由视觉模型识别，可能出错，请对照原页检查。
- 7 项人工验收（Finder 双击放行、系统文件面板、真实模型服务、中文输入法、Dock 退出、睡眠 / 唤醒、干净 Mac）尚在发布后继续验证，详见 [发布说明](docs/qt-beta/release-notes.md)。

---

## 🌐 界面语言

界面默认简体中文。English、Español、Русский、العربية 为实验功能，界面文案已齐全，但尚未完成独立语言审阅和完整的阿拉伯语 RTL 验收。详见 [外观与多语言边界](docs/i18n/README.md)。

---

## ❓ 常见问题

**第一次打开被系统拦住？** 应用未签名、未公证。在「应用程序」里右键点 Folith，选「打开」，在弹窗中再点一次「打开」。只需一次。不要全局关闭 Gatekeeper。

**「测试连接」失败？** 检查 API Key、模型名和接口地址是否正确。如果你开着系统代理或 VPN，代理可能返回 502 之类的错误，可以先关闭代理再试，或把服务商域名加入直连。自定义网关请确认兼容 OpenAI API。

**扫描页识别错了？** 识别由视觉模型完成，可能出错。先在工作台用原页对照核对；可以换一个更强的视觉模型，在「设置」里单独配置视觉连接后重新扫描。

**任务中断了怎么办？** 进度会随时落盘。在任务库里打开该任务，点「继续翻译」，会从最后完成的批次接着做，成功的批次不会重跑。

**怎么更新、卸载、清除数据？** 点「检查更新」会提示是否有新版本，新版本需自行到 Release 页下载。把 `Folith.app` 拖进废纸篓只卸载应用，任务和设置保留。要完全清除，退出应用后删除 `~/Library/Application Support/Folith`、`~/Library/Logs/Folith` 和 `~/Library/Preferences/com.folith.QtDesktop.plist`；删除前请先导出需要保留的译文与 TMX / TBX。

### 反馈

- 问题与建议：[GitHub Issues](https://github.com/xueyang-dev/Folith/issues)
- 邮件：xydev@outlook.sa（也可以在应用「设置」里找到入口）

---

## 🗺️ 路线图

- [x] **v0.4.0**：统一品牌、语言资产与术语中心、交付门禁、草稿实时容灾
- [x] **v0.6.0**：Qt 原生桌面客户端成为主入口；EPUB / TXT / Markdown 导入；扫描页以视觉模型为主；项目内的术语与翻译记忆；多条模型连接；跨段审校；译文 DOCX 保留原文图片；外观跟随系统
- [ ] **下一步**：签名与公证；Windows 安装包与验收；DOCX 原格式写回（保留原始排版与样式）；翻译记忆模糊匹配；文档跨版本增量翻译

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
