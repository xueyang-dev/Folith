# Folith 桌面版发布说明（0.5.0 · macOS Beta）

> 本文件由 `scripts/release_notes.py` 从发布清单自动生成，请勿手工编辑。
> 事实来源：`build/desktop/release-manifest.json`、`build/desktop/dmg_record.json`。

## 1. 版本与产物

| 项 | 值 |
| --- | --- |
| 应用版本 | `0.5.0` |
| build id | `git:1260a82` |
| 源码 commit | `1260a826819cce3df23e165af36f8563f84d3625` |
| 工作区状态 | 有未提交改动（开发构建） |
| 打包后端 | `pyinstaller` |
| 产物 | `Folith-0.5.0-macos-arm64.dmg`（152.1 MB） |
| sha256 | `ba4c302bb4810e53fbe78652f1a57948260e0d9b6278ef8f313755940a6ce0e4` |
| 格式 | `UDZO`，`hdiutil verify` 退出码 0 |

## 2. 支持系统

| 平台 | 状态 |
| --- | --- |
| macOS | **声明最低 13.0**，arm64（Apple Silicon） |
| Windows | BLOCKED - no Windows host in this environment (D2/D3 Windows work deferred) |

- **实际验收环境**：`macOS-27.0.1-arm64-arm-64bit`（arm64，Python 3.11.3）。
- `13.0` 是**声明的**最低版本，本版**未在 13.x 实机验证**；只把上述实际验收环境列为已验证系统。
- 首批只承诺 Apple Silicon；Intel Mac / Windows ARM64 不在本版承诺内。

## 3. 安装

1. 打开 `Folith-0.5.0-macos-arm64.dmg`，把 `Folith.app` 拖入 `Applications`；
2. 首次启动若出现 Gatekeeper 提示：本产物**未签名、未公证**（见第 6 节），需在"系统设置 → 隐私与安全性"中允许，或右键"打开"；
3. 用户数据位于 `~/Library/Application Support/Folith/data`，日志位于 `~/Library/Logs/Folith`；删除应用不会删除数据。

## 4. 随包能力（不需要额外安装）

| 能力 | 实际随包内容 |
| --- | --- |
| 运行时 | Python 3.11.3、Streamlit 1.64.0、pywebview 6.2.1（PyInstaller 6.22.3） |
| OCR 引擎 | Tesseract（随包，无需 Homebrew 或系统安装） |
| OCR 语言包 | `ara`, `chi_sim`, `eng` —— 数据来源与版本记录随包（许可 `Apache-2.0`），并逐文件校验 sha256 |
| OCR 输出配置 | `configs/`（含 `tsv`）随包 |

**语言披露（roadmap 要求逐项写明）**：本版**只**验证并随包上述三种语言；其他语言即使 Tesseract 理论上支持，也**不在本版承诺内**。缺少所选语言包时应用会明确报错，不会静默改用 `eng`。

## 5. 外部依赖（可选 / 必需）

| 依赖 | 状态 |
| --- | --- |
| 模型服务（翻译/画像） | **必需**，由用户自行配置 API Key（本地 JSON 保存，不进入交付物、项目转移与诊断包） |
| LibreOffice | **可选**。仅用于交付渲染 QA；未安装时该步骤保持 `NOT_RUN`（不会被伪报为通过）。本版已支持在 `/Applications` 等标准安装位置发现它 |

## 6. 已知限制与未验证项（如实披露）

| 项 | 状态 |
| --- | --- |
| 代码签名 | **未签名**（`adhoc=true`，无 Developer ID） |
| 公证 / staple | **未公证**（`spctl` 判定：rejected，退出码 3） |
| 真实下载后的 Gatekeeper 体验 | 未验证（无干净 Mac 与签名产物） |
| **干净机器验收（D2 完成条件）** | **未完成**：本版只在本机验证（见第 2 节实际验收环境）。验收手册与免 Python 的自动冒烟脚本已就绪：`docs/desktop-d2/clean-machine-acceptance.md` |
| 真实操作系统级中文输入法 | 未验证（无辅助功能授权） |
| 原生保存对话框 / 强制退出对话框点击 | 未验证（人工项） |
| 机器睡眠唤醒 | 未验证（人工项；断连与中断状态识别已实现并验证） |
| 真实 LibreOffice 渲染 | 未验证（本机未安装 LibreOffice） |
| Windows 版本 | **未提供**（无 Windows 主机，D2/D3 后续解决） |

自动检查中记为 BLOCKED 的项：`signed_with_developer_id`, `notarized_and_accepted`。

## 7. 发行依赖与资源清单

包内实际收集到的发行版（15 个）：

| 发行版 | 版本 |
| --- | --- |
| MarkupSafe | 0.3 |
| altair | 3.0 |
| attrs | .1.0 |
| click | 5.0 |
| cryptography | .0.1 |
| httpcore2 | 13.0 |
| httpx2 | 13.0 |
| itsdangerous | 2.0 |
| jsonschema | 26.0 |
| numpy | 4.6 |
| pydantic | 13.5 |
| setuptools | .5.0 |
| streamlit | 64.0 |
| typing_extensions | 16.0 |
| websockets | .1.1 |

仓库固定依赖清单：

| 文件 | 内容 |
| --- | --- |
| `requirements-desktop-build.txt` | 2 pinned entries |
| `requirements-desktop.txt` | 1 pinned entries |
| `requirements.txt` | 11 pinned entries |

## 8. 校验方式

```bash
shasum -a 256 Folith-0.5.0-macos-arm64.dmg   # 期望 ba4c302bb4810e53fbe78652f1a57948260e0d9b6278ef8f313755940a6ce0e4
hdiutil verify Folith-0.5.0-macos-arm64.dmg
```

发布前自动校验（18 项通过、2 项 BLOCKED）：

```bash
venv/bin/python scripts/release_checks.py --mount
```
