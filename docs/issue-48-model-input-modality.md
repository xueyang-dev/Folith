# Issue #48 — 模型设置显示输入类型（文本 / 文本 + 视觉 / 未知）

> Issue: xueyang-dev/Folith#48（v0.7.4 · Source Context & Translation Scope，P2）
> 基线 commit: `82a5ae9`（origin/main，PR #131 之后）
> 关联验收门（milestone 8）：*Display provider/test-based model capability with
> an explicit unknown state; no hard-coded model lists or automatic paid probes.*

## 1. 前置事实（实现前）

- `core.fetch_provider_models` 已经从 `/models` 响应读取
  `input_modalities` / `architecture.input_modalities`（例如 OpenRouter），
  但只用来过滤非文本模型，**读完即弃**。
- 多数服务商（OpenAI、OpenCode Go、SiliconFlow、大多数中转站）的目录不声明
  输入模态；Gemini 原生目录也没有这个字段。
- 连接测试（`connection_status` / `settings_pages.ConnectionPanel`）只发文字
  请求；视觉连接的测试结果明确写「未验证图像输入」。

## 2. 数据模型：两个来源，都不许猜

| 来源 | 存储 | 失效方式 |
|---|---|---|
| 服务商目录声明（`fetch_provider_models` 的第 4 个返回值 `modalities`） | 随连接档案存进 `profile["model_modalities"]`（config 文件） | 重新拉取目录覆盖；换提供商即丢弃 |
| 「测试视觉」实测（用户点按钮才有的一次付费小图请求） | 客户端偏好（QSettings），键 = provider+model+base_url+密钥指纹（`connection_status.record_vision_probe`） | 任何一维配置变化 → 指纹变化 → 读不到 = 未知 |

两者都没有时**显式「未知」**。不查、不写任何内置模型清单；`test_provider_vision`
的判断只依据这一次请求的真实反应（接口拒图 = 不支持；说出方块颜色 = 支持；
答非所问 = 未验证通过，如实返回）。

## 3. 行为

- **连接编辑区（模型目录）**：每个模型显示 `名字 · 文本 / 文本 + 视觉 / 未知`；
  悬停说明证据来源（「服务商模型目录声明」/「『测试视觉』实测 时间」/「未知，
  可实测」）。列表显示带标注，但选中回写 `model_box` 的永远是纯模型名
  （`Qt.UserRole`），数据模型不混入显示文案。
- **「测试视觉」按钮**：位于「获取可用模型」旁，对列表中选中模型发一张程序
  生成的 16×16 纯红方块 PNG（`core._probe_image_png`，无素材依赖），问颜色。
  结果写入缓存并即时刷新列表与「视觉」用途提示。进行中按钮去抖。
- **「视觉」用途行**：所选模型被目录标注仅文本 → 警告色提示；实测未通过 →
  警告色 + 悬停给出失败原因；支持 → 确认色；未知 → 中性提示「可在编辑连接里
  实测」。警告不阻止保存（保留人工决定）。
- **探测请求路径**：`core.test_provider_vision` 复用与正式请求完全相同的
  OpenAI 兼容客户端获取（`_acquire_openai_compat_client`，从
  `_call_llm_transport` 提取——代理例外、连接池、跟踪钩子一致），非
  OpenAI 兼容服务商（Gemini）在发请求前拒绝并说明原因。

## 4. 红线与对应测试

| 验收要求 | 测试 |
|---|---|
| 显式未知态，声明之外缺席（不编造） | `tests/model_input_modality_test.py::test_fetched_modalities_keep_provider_declarations_verbatim`；`tests/qt_model_input_modality_test.py::test_catalog_labels_declared_and_unknown_states` |
| 无硬编码模型清单 | `test_vision_probe_has_no_hard_coded_model_lists`（源码级断言：函数体不含模型名/前缀清单） |
| 无自动付费探测 | 探测只挂在 `vision_probe_button.clicked`；`test_probe_button_updates_list_and_caches_result` 钉住「点一次按钮 = 一次调用」；连接测试（`connection_status.test`）仍只发文字请求 |
| 探测缓存按 连接+模型、配置变化即作废 | `test_vision_probe_cache_roundtrip_and_invalidation` |
| 已知仅文本被选为视觉连接时警告 | `test_vision_role_warns_for_declared_text_only`；实测未通过 `test_vision_role_hint_reads_probe_cache` |
| 未通过 ≠ 已知仅文本 | `test_probe_failure_keeps_unknown_out_of_the_way`（失败信息进缓存与状态行，不冒充目录结论） |

## 5. 验证记录与限制

- 新增 `tests/model_input_modality_test.py`（8 例）、`tests/qt_model_input_modality_test.py`
  （10 例）；更新 4 个受签名/呈现影响的既有文件
  （`provider_exchange_modes_test`、`qt_model_discovery_test`、
  `qt_trial_feedback_test` ×2 处）。相关回归 2026-10-25 本机通过：
  模型/连接/设置/引导/视觉连接/传输层/i18n/red-team 共 245 项（offscreen）。
- `scripts/check_ui_locales.py` 与 `scripts/qt_i18n_coverage.py` 通过
  （新文案 14 条，en/es/ru/ar/zh-CN 五目录一致）。
- **实测限制（如实记录）**：
  - 「测试视觉」对真实服务商的端到端行为未在本机验证（那是一次真实付费调用）；
    单测以伪造客户端钉住请求形状与判定逻辑。合并后建议在有余额的连接上人工
    点一次按钮补一条真实验证记录。
  - Qt 侧验证在 macOS offscreen 完成；未做原生 Windows / macOS 手动走查
    （本改动只用标准控件与既有跨平台模式，无平台分支）。
