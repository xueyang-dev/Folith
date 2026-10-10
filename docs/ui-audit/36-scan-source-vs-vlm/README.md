# 扫描件：把表层词汇对齐到 VLM 迁移后的真实架构

日期：2026-10-09 · 界面：桌面端（PySide6 / cocoa，真实窗口抓图）· 主题：浅色 / 夜间 / 纸面

## 起因

试用时用户问：**"我之前的一个转向是把 OCR 摘掉然后转向 VLM 直接翻译，怎么这个 OCR 选项还在？"**

## 诊断：OCR 没有被"摘掉"这件事本身不准确

准确的说法是 **"被降级为显式遗留选项，但 UI 用的是迁移前一天的词汇"**，所以看起来像没生效。

VLM 迁移是真的，且已落地：

- `docs/vlm-migration.md` + `docs/vlm-migration-report.md`（2026-10-05）
- 提交 `3e8486a feat(document): add the extraction quality gate, DocumentIR and VLM path`（10-07）

### 关键机制：`ocr_backend` 的取值 `online` **就是** VLM 路径

```python
# core.py:2304-2306
if (not use_text_layer or visual_indices) and (understanding_provider is not None
                          or ocr_backend is None
                          or _ocr_backends.normalize_backend(ocr_backend) == "online"):
    from folith.document_understanding import VLMDocumentProvider, ...
```

而默认值就是 `online`（`adapter.py:101` 的 `EngineConfig.ocr_backend = "online"`，
`app.py:865` 同）。**所以面板里那个「识别引擎 = 联网识别（默认）」实际是 VLM 结构化视觉
理解**，不是线上 OCR 转写。

标签的时间线解释了为什么会这样：`联网识别（默认）` 是 **10-06** 的本地化提交
`ecc46cc` 引入的，VLM 路径 **10-07** 才加 —— 取值 `online` 的含义变了、名字没跟上。

### 哪些旋钮真的失效

| 字段 | 视觉理解（默认，`online`） | 本机 OCR（`tesseract` / `paddle`） |
|---|---|---|
| 识别语言 | 只喂"本机定位证据"那个**可选** pass（`core.py:2326`） | 语义识别语言 |
| 何时识别 | 仍然有效（是否信任文本层） | 同 |
| 识别精度 | **失效** —— VLM 固定 `dpi=150`（`core.py:2336`） | 有效（`ocr_dpi`，`core.py:2384`） |

`识别精度`（220/300/350 DPI）在视觉理解路径上是个**"转了没反应"的旋钮**，比缺一个选项
更糟 —— 用户会以为它在起作用。

### 遗留本机 OCR 不能直接拿掉

迁移报告 B / F 节：*runtime removals, not deletion of legacy implementations*，且
`KEEP TEMPORARILY: local legacy source cleanup for explicitly selected local engines and
saved tasks, pending broader release gates`。所以 Tesseract / Paddle 必须**仍然可达**，
只能标明是本机路径。

## 定案（用户三选一：改名收敛）

| 文件 | 改动 |
|---|---|
| `folith/qt/adapter.py` | 新增共享真源 `OCR_BACKEND_CHOICES`（`setup_choices()` 改用它）；取值不变，仍是 `online/tesseract/paddle` |
| `folith/qt/dialogs.py` | 标签页与卡片标题 `扫描件 OCR` → **`扫描件识别`**；字段 `识别引擎` → **`提取来源`** 并移到第一位；改用共享真源（删掉手抄的第二份副本）；NoticeStrip 改为"本机 OCR 才需要语言包" |
| `folith/qt/setup_panel.py` | 区块名 `扫描件 OCR` → `扫描件识别`；`识别引擎` → 提取来源（跨两列、排第一，标注两者互斥）；**`识别精度` 只在本机 OCR 时出现**；`识别语言` 的两套提示语按来源切换；底部说明按来源切换 |

**`ocr_backend` 的取值语义（`online` == VLM）没有动** —— 那是 core 的触发条件。

## 顺带修掉的一个既有缺陷：设置页的黑色视口

看这一页时发现设置对话框的滚动页面在**浅色主题下露出大片纯黑**。

`_scroll_page()` 里 `area.viewport().setAutoFillBackground(False)`（viewport 按约定透明），
而页面自己没有背景规则 → 内容比视口短时，剩下的那片没人画。

**这是 HEAD 就有的缺陷**，不是本轮引入的（`git show HEAD:folith/qt/dialogs.py` 里
`pageCanvas` 出现 0 次）。它一直没被发现，是因为「扫描件识别」是唯一**内容短到会露出来**
的一栏 —— 其余标签页都比视口高。

修法与我上一轮给译前面板用的是同一条既有规则：`inner.setObjectName("pageCanvas")`
（值与 `QDialog` 同为 `bg-page`，所以接得上），**零新增 QSS**。

## 证据

真实窗口（cocoa）+ 真实 QSS，数据目录 `/tmp/folith-qa-scan`，PDF 夹具用 fitz 现生成
（走真的 `is_pdf` 路径，不是手工造的视图）：

```
is_pdf: True
source entries: ['视觉理解（VLM，默认）', '本机 OCR · Tesseract', '本机 OCR · PaddleOCR']
selected source: '视觉理解（VLM，默认）' | data: 'online'
识别精度 hidden on VLM: True
识别精度 hidden on local: False
settings tab title: '扫描件识别'
```

截图：`01`–`03` 面板（VLM 默认，三主题）、`04` 面板切到本机 OCR（`识别精度` 出现）、
`05`/`06` 设置页同名标签页（黑区已消，字段顺序 = 提取来源 / 何时识别 / 识别语言）。

## 验证

- `tests/qt_task_setup_test.py` → **24 passed**（+4：默认标签即视觉理解、旧文案不再出现且
  新标签只有一份副本、`识别精度` 仅本机出现、非 PDF 不显示整块）
- `tests/qt_settings_dialog_ui_test.py` → **10 passed**（+2：标签来自共享真源、每个标签页
  自带底色）
- `tests/qt_client_window_test.py` → **71 passed**（分块逐进程）
- `qt_home_cards_test` / `qt_client_style_test` / `effective_settings_test` /
  `ui_preferences_test` → exit 0
- QSS 指纹 `light=c3b8fa9900246d85 / paper=c18f15e21272c328 / dark=4c31f48927321f38`
  → 逐字节命中基线，夜间色泄漏命中 0
- `pyflakes` → 相对 HEAD 增量 0

## 未决

- **多语言译文**：改名让 `folith/resources/locales/qt-*.json` 里的 `扫描件 OCR` 与
  `识别引擎` 成为孤儿，新键（`扫描件识别` / `提取来源` / 新提示语）在 en/es/ru/ar 下
  **回退中文**。这是 i18n 机制的设计（缺条目回退），但不该在发布前留着 —— 见任务 #29。
  **没有**手工编造译文。
- Streamlit 侧 `core.ocr_source_options()` 里 `online` 的描述仍写作「OpenAI 兼容图像转写
  接口」，同样名不副实；本轮未动（用户报的是桌面端）。
