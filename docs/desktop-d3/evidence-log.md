# D3 桌面工程证据日志（Public Distribution）

路线图：`docs/desktop-client-roadmap.md` v1.1。D0/D1/D2 见 `docs/desktop-d0/`、`desktop-d1/`、`desktop-d2/`。
本文件是审计轨迹，不替代测试。**D3 的签名 / 公证 / 时间戳与真实下载验收在本环境为 BLOCKED（无 Developer ID、无干净 Mac、无 Windows 主机）**；此处只做不依赖证书的本地发布工程。

## D3 要求（roadmap line 170 / line 57 / §5 两平台 D3 行）

| 要求 | 本环境状态 |
| --- | --- |
| 签名 / 公证 / 时间戳（macOS：Developer ID、Hardened Runtime、notarytool、staple） | **BLOCKED**（无 Developer ID；D0 实测 0 个签名身份） |
| Windows 签署应用与安装器并加时间戳 | **BLOCKED**（无 Windows 主机） |
| 真实下载验证（浏览器下载的签名 / 公证产物在干净 Mac 验收） | **BLOCKED**（无干净 Mac；且产物未签名） |
| 发布说明（含各平台实际语言与外部安装要求逐项披露） | **本轮完成**（见 D3-2，由发布清单生成 + 漂移检测） |
| 可复现构建记录 | **本轮完成**（见 D3-1） |
| 发行依赖版本清单 / 资源清单 | **本轮完成**（见 D3-1） |
| 支持系统声明 | **本轮完成**（manifest 的 `supported_systems` + 发布说明第 2 节，并显式记录 Windows BLOCKED） |
| 正式平台 CI | **本轮完成定义**（见 D3-3：macOS job 与本地已验证命令一致；未在远端真实运行） |

---

## D3-1 — 发布物校验与发布清单（已实现并验证）

- 时间：2026-10-03
- 里程碑：D3（roadmap line 57「一份发行依赖版本清单、资源清单、验收用例与发布说明」；line 170「可复现构建记录、支持系统声明」）
- 变更：新增 `scripts/release_checks.py`，对**用户实际拿到的产物**做校验并写出 `build/desktop/release-manifest.json`：
  - 产物身份：DMG 字节数、sha256、`hdiutil verify` 退出码；
  - 应用标识：`CFBundleName/Identifier`、**自带图标**（拒绝 PyInstaller 占位图）、最低系统版本；
  - 架构：`lipo -archs` 必须为 `arm64`（首批只承诺 Apple Silicon）；
  - 随包运行时：Python / Streamlit / pywebview / PyInstaller 版本（取自随包构建记录）；
  - **发行依赖与资源清单**：包内实际存在的 `*.dist-info`（15 个发行版）+ 仓库固定的 `requirements*.txt` 内容；
  - OCR：语言包集合（ara/chi_sim/eng）、`configs/tsv` 存在、**许可与数据版本记录随包**，且随包 traineddata 的 sha256 与固定记录**逐一比对**；
  - **开发环境泄漏**：扫描包内 365 个 Mach-O 二进制的 `otool -L`，不得依赖 `/opt/homebrew`、`/usr/local/opt`、venv 或仓库路径；文本资源不得含这些路径；
  - 凭据泄漏：不得包含 `provider_config.json` / `.env` / `credentials.json`，不得出现形如密钥的字符串；
  - 签名状态：`codesign -dv` 与 `spctl -a` 的结果**如实记录**，未签名 / 未公证记为 **BLOCKED（非 PASS）**。
- 实测（`--mount`：直接校验**挂载 DMG 内**的应用，而不是构建目录）：

| 检查 | 结果 |
| --- | --- |
| 阻塞项（16 项） | **全部 PASS**，`ok: true`，`blocking_failures: []` |
| 应用 | `Folith / dev.folith.desktop`、图标 `Folith.icns`、`lipo -archs = arm64`、最低 macOS 13.0 |
| 构建记录 | `0.5.0 / git:1260a82`（commit `1260a82…`，worktree_dirty true 如实记录） |
| 随包运行时 | Python 3.11.3 / Streamlit 1.64.0 / pywebview 6.2.1 / PyInstaller 6.22.3 |
| 发行依赖清单 | 包内 15 个 dist-info + 仓库 3 个 `requirements*.txt` 全部记入 manifest |
| OCR | `["ara","chi_sim","eng"]`、`configs/tsv` 存在、许可 `Apache-2.0`、**sha256 与固定记录 0 处不符** |
| 二进制扫描 | 365 个二进制，**0 处** Homebrew / venv / 仓库路径依赖 |
| 文本扫描 / 密钥扫描 / 凭据文件 | 均 0 命中 |
| DMG 校验 | `hdiutil verify` 退出码 0 |
| 签名 | `adhoc=true`、`developer_id=false` → **BLOCKED（如实记录，不伪造通过）** |
| Gatekeeper | `spctl` 退出码 3 → **rejected**（未签名产物的真实状态，如实记录） |
| 支持系统声明 | macOS 13.0+ / arm64；Windows 显式标记 `BLOCKED - no Windows host` |
- 测试与结果：新增 `tests/desktop_release_checks_test.py` → **6 passed**（随包记录不含开发路径、许可记录可随包、spec 同时携带许可记录并读取构建记录、**篡改语言包必须被发现**、manifest 诚实性（未签名/未公证必须为 None 而非 True，且支持系统含 Windows BLOCKED）、manifest 与磁盘产物 sha256/字节数一致）；全量桌面门禁 → **97 passed**。
- **本轮发现并修复的两个真实缺陷（记录在案）**：
  1. **默认构建被破坏**：上一轮为升级验证加入的 `"${BUILD_INFO_ARGS[@]}"` 在 `set -u` 下、数组为空时直接报 `unbound variable` 并中止 —— 即"无覆盖参数的普通构建"会失败（带覆盖参数的构建反而正常，因此上一轮升级验证没暴露它）。已改为 `${BUILD_INFO_ARGS[@]+"${BUILD_INFO_ARGS[@]}"}`，并分别验证**默认构建**与**覆盖构建**两条路径。
  2. **许可 / 数据版本记录没有随包**：`tessdata_provenance.json` 只 stage 未 ship，导致发布物缺少 roadmap 要求的固定数据版本与许可记录；同时随包的 `tesseract_payload.json` 里带着 `/opt/homebrew/...` 开发路径。已让 spec 携带许可记录，并把随包记录中的来源路径改为**来源标签**（`homebrew:tesseract`、`tessdata_fast:chi_sim.traineddata`），开发路径只留在构建日志里。
- 诚实标注 / 剩余风险：
  - 签名、公证、Hardened Runtime、staple、真实下载与 Gatekeeper 放行体验 **全部未验证**（无证书）；manifest 中明确记为 BLOCKED，不构成发布通过。
  - 包内 15 个 dist-info 只是 PyInstaller 实际收集到的元数据，不是完整依赖闭包；完整清单以仓库固定 requirements 与构建记录为准（manifest 两者都记）。
  - 未做"可复现构建"的逐位复现对比（两次构建的字节级一致性），仅记录了同一源码/固定依赖下的构建记录。
- 下一步：发布说明草稿（逐项披露随包语言与外部安装要求）+ 平台 CI（macOS 构建与校验 job）；签名相关保持 BLOCKED。

---

## D3-2 — 发布说明（由发布清单生成，含逐项语言与外部依赖披露）

- 时间：2026-10-03
- 里程碑：D3（roadmap line 57「发布说明」；line 117「发布页逐项写明实际语言及外部安装要求，缺包明确报错，不宣称所有 OCR 语言开箱即用」；line 170「支持系统声明」）
- 变更：新增 `scripts/release_notes.py`，**从实测事实生成**发布说明（`build/desktop/release-manifest.json` + `dmg_record.json`），输出 `docs/desktop-d3/release-notes-<version>.md`；提供 `--check` 用于漂移检测（CI 与测试使用）。
- 生成内容（`docs/desktop-d3/release-notes-0.5.0.md`，4.4 KB）：
  1. 版本与产物（版本 / build id / commit / 工作区是否 dirty / 后端 / DMG 名 / 字节数 / **sha256** / `hdiutil verify` 退出码）；
  2. 支持系统（macOS 13.0+ arm64；**Windows 显式标注未提供**）；
  3. 安装步骤与数据位置（含"未签名需在系统设置中允许"的如实提示）；
  4. 随包能力（运行时版本、随包 Tesseract、**随包语言包 `ara`/`chi_sim`/`eng`** 与数据许可、`configs/`）；
  5. **语言披露**：只承诺已验证并随包的三种语言，其他语言不在承诺内，缺包明确报错、**不静默改用 `eng`**；
  6. 外部依赖（模型服务必需、LibreOffice 可选且缺失时 `NOT_RUN`）；
  7. 已知限制与未验证项（未签名 / 未公证 / Gatekeeper 判定原文 / 真实下载体验 / 真实 IME / 保存对话框 / 睡眠唤醒 / 真实 LibreOffice 渲染 / Windows 未提供），并列出自动检查中记为 BLOCKED 的项；
  8. 发行依赖与资源清单（包内 15 个发行版 + 仓库 3 个固定 requirements）；
  9. 校验命令。
- 测试与结果：新增 `tests/desktop_release_notes_test.py` → **4 passed**（必须含语言披露与"不静默改用 eng"、未签名/未公证必须如实呈现且不得出现"已签名/已公证"、**漂移检测**（改版本号后 `--check` 必须失败）、磁盘上的说明与清单重新渲染一致）。

## D3-3 — macOS 平台 CI（与本地已验证命令一致）

- 时间：2026-10-03
- 里程碑：D3（roadmap line 215「安装器、正式平台 CI 和 release 工程归 D2 / D3」；line 219「每个支持平台必须有对应记录，现有 Ubuntu / wheel gate 不替代桌面实机验收」）
- 变更：在既有 `.github/workflows/release-gate.yml`（原为 Ubuntu-only：pytest 矩阵 + wheel 冒烟）中新增 **`desktop-macos` job**（`runs-on: macos-14`，10 步）：检出 → Python 3.11 → 安装 `.[test]` 与 `requirements-desktop-build.txt` → `brew install tesseract`（stage 随包引擎需要一份可复制的引擎）→ `bash scripts/build_desktop.sh` → **桌面门禁 14 个测试文件**（含冻结应用握手 / 生命周期 / 诊断 / 导入 / 工具发现 / 应用标识 / 文件集成 / DMG / 发布校验与说明）→ `bash scripts/build_dmg.sh` → `scripts/release_checks.py --mount` → `scripts/release_notes.py --check` → 上传 DMG 与清单。
- 验证方式（本环境无法执行 GitHub Actions，故验证**定义与命令本身**）：
  - YAML 解析通过（`ruby -ryaml`）：jobs = `tests, package, desktop-macos`，runner `macos-14`，10 步；
  - **逐字执行 CI 中列出的门禁命令**：14 个测试文件在本地以 CI 相同的环境变量运行 → **101 passed**；
  - 新增 `tests/desktop_ci_definition_test.py` → **4 passed**（job 必须存在且在 macOS runner 上；必须运行 build/DMG/release checks/notes check 四条命令并上传 `*.dmg`；门禁列表中的每个测试文件都必须真实存在；必须安装用于 stage 随包 payload 的引擎并安装构建依赖）。
- 诚实标注 / 剩余风险：
  - CI **尚未在 GitHub 上真实运行过**（本环境不能触发远端 Actions，且未获授权推送）；因此它是"与本地已验证命令一致的定义"，不是"CI 已通过"的证据。
  - CI 里 Tesseract 引擎来自 `brew install tesseract`（与本机相同的来源方式）；语言包来自仓库固定文件，不受 runner 环境影响。
  - 签名 / 公证在 CI 中同样为 BLOCKED（无证书），`release_checks.py` 将其记为非阻塞项，不会让 CI 假通过。
- 下一步：D3 剩余项全部依赖外部条件（Developer ID、干净 Mac、Windows 主机）；本地可做的发布工程已到此为止。

---

## D3-1 追加 — 按已定图标重切产物并刷新清单 / 发布说明

- 时间：2026-10-03
- 触发：D2-3a 更正（应用图标改用 `folith-mark-hires.png` 高清透明 mark）后，产物必须重切，清单与发布说明必须随之刷新（否则发布物与记录不一致）。
- 执行：`bash scripts/build_desktop.sh` → `bash scripts/build_dmg.sh` → `scripts/release_checks.py --mount` → `scripts/release_notes.py`。
- 结果：`ok: true`、`blocking_failures: []`；`own_icon / ocr_provenance / no_development_paths_in_text` 均 PASS；产物 `Folith-0.5.0-macos-arm64.dmg` 152,050,004 字节（145 MB），新 sha256 已写入 `build/desktop/release-manifest.json` 与 `docs/desktop-d3/release-notes-0.5.0.md`（`release_notes.py --check` 非 stale）。
- 教训（记录）：**不要在针对该产物的验证脚本运行期间重建 `dist/desktop/Folith.app`**（本轮曾在暂停退出验证运行中重建，可能干扰该验证的后续阶段）；重切产物应在验证结束后进行。

---

## D3-1 追加 — 发现"DMG 与已构建应用不一致"的发布漂移，并加入阻断式校验

- 时间：2026-10-03
- 背景：D2-7 修复画像 OCR 健壮性后重建了 `dist/desktop/Folith.app`，但**没有重切 DMG**。于是磁盘上的交付物（DMG）里装的是**修复前**的应用，而发布清单/发布说明仍按旧镜像生成——这正是"发布物与记录不一致"的典型漂移，而且其它检查全部照常通过。
- 实测证据（对比包内构建记录）：

| 对象 | `built_at` |
| --- | --- |
| `dist/desktop/Folith.app`（当前构建，含 OCR 修复） | `2026-10-03T11:46:24+00:00` |
| **DMG 内**的 `Folith.app` | `2026-10-03T10:03:14+00:00`（旧） |

- 变更：
  1. `scripts/release_checks.py` 新增**阻断式**检查 `dmg_app_matches_built_app`：比较镜像内应用与已构建应用的构建记录（`built_at` + `build_id`），不一致即失败并提示 `rebuild the DMG (scripts/build_dmg.sh)`；比较逻辑抽为 `dmg_app_matches_built_app()` 便于单测；
  2. 重切镜像：`bash scripts/build_dmg.sh` → `Folith-0.5.0-macos-arm64.dmg`，152,082,974 字节，新 sha256 `48a92497be1ef72d…`；
  3. 重新生成发布清单与发布说明（`release_checks.py --mount` → `release_manifest.json`；`release_notes.py` → `release-notes-0.5.0.md`，`--check` 非 stale）。
- 复验（对**新镜像**）：

| 检查 | 结果 |
| --- | --- |
| `dmg_app_matches_built_app` | **PASS**（镜像内 `built_at=2026-10-03T11:46:24+00:00` = 已构建应用） |
| 其余阻断项（16 项） | 全部 PASS，`ok: true`，`blocking_failures: []`（含 `dmg_checksum`、`own_icon`、`ocr_provenance`、365 个二进制无开发路径、无密钥/凭据） |
| 挂载并启动新镜像（sweep `dmg-launch`） | **PASS**（9.4s） |
- 测试：`tests/desktop_release_checks_test.py` 新增"陈旧 DMG 必须被抓到"用例 → **7 passed**（含镜像与已构建应用一致时通过、不一致时失败且提示重切）。
- 诚实标注：该检查只比较构建记录，不比较二进制内容；如需更强保证应对镜像内应用做全量哈希（体积/时间成本更高，暂不引入）。

---

## D3-4 — 发布说明的"只列实际验收系统"与验收状态披露（§25 / roadmap line 11）

- 时间：2026-10-03
- 里程碑：D3（roadmap line 11「macOS 最低版本…**发布页只列实际验收的系统**」；line 219「D2/D3 验收用户实际获得的安装包」；prompt §25 文档准确性）
- 发现的两个披露缺口：
  1. 发布清单**没有记录"在哪里验收"**——`supported_systems` 只有声明的最低版本 `13.0`，而实际只在 macOS 27.0.1 (arm64) 上跑过；发布说明因此隐含"13.0 已验证"，与"只列实际验收的系统"不符；
  2. 发布说明**没有披露 D2 完成条件（干净机器验收）尚未完成**，读者无法从发布页看出这一状态。
- 修复：
  - `scripts/release_checks.py` 新增 `verified_environment`（`platform.platform()` / `machine` / `python`）写入发布清单——**验收发生在哪台机器**成为可追溯事实；
  - `scripts/release_notes.py` 第 2 节改为"**声明**最低 13.0 + **实际验收环境**（`macOS-27.0.1-arm64-arm-64bit`，arm64，Python 3.11.3）+ 明确说明 13.0 **未在 13.x 实机验证**、只把实际验收环境列为已验证系统"；
  - 第 6 节新增一行：**干净机器验收（D2 完成条件）——未完成**，并指向 `docs/desktop-d2/clean-machine-acceptance.md`；
  - 顺带修掉公证一行直接打印临时路径（`/var/folders/...`）的问题，改为 `spctl` 判定 + 退出码。
- 复验：`release_checks.py --mount` → 清单 `ok: true`；`release_notes.py` 重新生成 → `--check` 返回 `stale: false`（退出码 0）。
- 新增回归测试（`tests/desktop_release_notes_test.py` → **6 passed**）：必须出现"实际验收环境"、"声明的"+"未在 13.x 实机验证"、"干净机器验收…未完成"与手册路径；且**不得**出现 `/var/folders` 之类的原始临时路径。
- 诚实标注：`verified_environment` 记录的是**运行发布校验的那台机器**（本开发机）；干净机器验收完成后应重新生成清单与发布说明，届时第 2 节与第 6 节会自动反映新事实。
