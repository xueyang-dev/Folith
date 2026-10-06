# 干净机器验收手册（macOS Beta）

路线图：`docs/desktop-client-roadmap.md` §6.1 / §25。D2 的完成条件是
**"无开发环境的干净机器以普通用户完成验收"**，D2/D3 必须验收**用户实际获得的安装包**
（roadmap line 219）。本机（开发机）已把可自动化的部分全部验证（见 `evidence-log.md` 的
D2-1…D2-12、17 用例 sweep），剩下这一步需要一台**干净 Mac**，因此写成可照做的手册。

---

## 0. 本手册的两部分

| 部分 | 谁来做 | 内容 |
| --- | --- | --- |
| A. 自动冒烟检查 | 一条命令 | 校验镜像、安装、启动、UI 可访问、数据目录、随包 OCR、诊断、退出回收（**不需要 Python / Homebrew**） |
| B. 人工主流程 | 你（普通用户视角） | 真实导入 → 翻译 → 编辑 → 审校 → 交付 → 保存/外部打开 → 退出 → 重开恢复 |

脚本只使用 macOS 自带命令（`hdiutil` / `shasum` / `defaults` / `curl` / `ditto`），
因此可以在从未装过开发环境的机器上运行。

---

## 1. 前置条件

- Apple Silicon Mac（首批只承诺 arm64）；macOS 13 或更高（`LSMinimumSystemVersion=13.0`）。
- 本仓库产出的 `Folith-<version>-macos-arm64.dmg` 与它的 **sha256**（在 `build/desktop/dmg_record.json`
  或 `build/desktop/release-manifest.json` 中）。
- 一个可用的模型服务 **API Key**（翻译/画像需要；应用不会自带密钥）。
- 可选：LibreOffice（仅影响交付的渲染 QA；未安装时该步骤会如实显示 `NOT_RUN`）。
- 机器上**没有**本项目的 venv / Homebrew / Python 也不影响——这正是要验证的点。

---

## 2. A 部分：自动冒烟检查

把 DMG 与脚本拷到干净机器（例如 `~/Downloads`），然后：

```bash
chmod +x clean_machine_acceptance.sh
./clean_machine_acceptance.sh \
  --dmg ~/Downloads/Folith-0.5.0-macos-arm64.dmg \
  --expect-sha256 <预期的 sha256> \
  --install \
  --out ~/Downloads/folith-acceptance
```

- `--install`：把应用装到 `/Applications`（验收要求"拖入 Applications"的等价动作）。不加则直接从挂载卷运行。
- `--data-dir` / `--log-dir`：**验收时不要传**——要验证的是**默认位置**（`~/Library/Application Support/Folith/data`、`~/Library/Logs/Folith`）。这两个参数只在需要隔离时使用。
- 结果写入 `--out` 目录：`clean-machine-report.txt`（逐项 PASS/FAIL）、`diagnostics.json`、`app-launch.log`。

脚本检查项（全部 PASS 才继续 B 部分）：

| 检查 | 通过标准 |
| --- | --- |
| 镜像校验 | sha256 与给定值一致；`hdiutil verify` 通过 |
| 卷内容 | 存在 `Folith.app` 与指向 `/Applications` 的 `Applications` 链接 |
| 应用标识 | `CFBundleName=Folith`、`CFBundleIdentifier=dev.folith.desktop`、图标文件存在 |
| 启动（无系统 Python） | 生成实例记录（含端口与服务 PID） |
| UI | `http://127.0.0.1:<port>` 返回 **HTTP 200** |
| 数据目录 | 默认位置被创建 |
| 随包 OCR | 包内 Tesseract 列出 **eng / chi_sim / ara**（无需 Homebrew） |
| 诊断 | `frozen=true`、`packaging_backend=pyinstaller`、`job_state=unversioned`、declared runtime schema = 2、**不含凭据样文本** |
| 退出 | 退出后**无残留** `Folith --serve` 进程 |

---

## 3. B 部分：人工主流程（普通用户视角）

每一步记录"期望 / 实际"，全部完成且数据可恢复才算通过。

| # | 步骤 | 期望看到 |
| --- | --- | --- |
| 1 | 双击 DMG，把 `Folith.app` 拖入 Applications | 应用出现在 Applications |
| 2 | 首次启动 | 若出现 Gatekeeper 提示：**这是未签名产物**，在"系统设置 → 隐私与安全性"选择仍要打开，或右键"打开" |
| 3 | 窗口出现 | 标题为「译页 · 智能体翻译工作台」；侧栏可见「新建任务」等界面 |
| 4 | 配置 AI 引擎 | 用自己的 API Key；「测试连接」返回成功 |
| 5 | 导入一份真实 PDF 或 DOCX（中文/英文各一即可；**任意自有文档即可，无需样例文件**） | 文档解析出段落；扫描件会走 OCR |
| 6 | 运行小型任务（例如 10–20 段） | 进度推进；可中途暂停并「继续任务」 |
| 7 | 在工作台编辑一段并保存 | 出现"已保存"；切到别的段再切回，内容仍在 |
| 8 | 完成审校 | 编辑过的段落需重新审校至无待复审 |
| 9 | 创建交付版本 | 显示「✓ 交付版本 v1 已创建」；若有阻塞项，界面给出**具体原因**（不是空白） |
| 10 | 下载交付文件 | 出现 macOS **原生保存面板**；选择保存位置并确认 |
| 11 | 打开交付文件 | 双击下载的文件能被 Word/Pages 打开，且包含第 7 步的编辑 |
| 12 | 退出（⌘Q 或关窗） | 若有未提交编辑或运行中任务：先保存/停止再退出；超时会明确给出"继续等待 / 强制退出" |
| 13 | 重开 | 任务列表里有该任务；**编辑与交付版本仍在**（可从交付页重新下载） |
| 14 | 数据位置 | `~/Library/Application Support/Folith/data` 下有该任务；日志在 `~/Library/Logs/Folith` |
| 15 | 卸载 | 把 `Folith.app` 拖入废纸篓；**用户数据仍在**（重装后任务可读） |

---

## 4. 记录与回传

把以下内容作为验收证据回传（可直接贴到 D2 记录里）：

1. `clean-machine-report.txt`（A 部分逐项结果）；
2. `diagnostics.json`（应用自述的版本/平台/工具能力）；
3. B 部分第 3–14 步的"期望 / 实际"记录（截图或文字均可）；
4. 机器信息：`sw_vers`、`uname -m`、是否装过 Xcode/Homebrew/Python。

---

## 5. 已知限制（必须逐项如实披露）

| 项 | 状态 |
| --- | --- |
| 代码签名 / 公证 | **未签名、未公证**（无 Developer ID）→ 首次启动会出现 Gatekeeper 提示；真实下载后的体验属 D3 |
| OCR 语言 | 随包且已验证：**eng / chi_sim / ara**；其他语言不在本版承诺内（缺包会明确报错，不静默改用 eng） |
| LibreOffice | 可选；未安装时交付的渲染 QA 保持 `NOT_RUN`（不会被伪报为通过） |
| 真实操作系统级 IME / 光标 | 需人工（本手册第 7 步可覆盖中文输入） |
| Windows | **本版未提供**（无 Windows 主机；产品负责人已决定后续解决） |
| Intel Mac / Windows ARM64 | 不在首批承诺内 |

---

## 6. 判定

- **A 部分**：脚本 19 项全 PASS；
- **B 部分**：第 3–14 步全部符合期望，且第 13 步能读回编辑与交付；
- **无残留**：退出后无 `Folith` 进程、端口释放。

两者都满足后，把结果写入 `docs/desktop-d2/evidence-log.md` 的"D2 完成条件"一行，
D2 的 `NOT YET TESTED（外部环境）` 才可改为 **PASS**。
