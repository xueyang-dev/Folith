# Folith macOS 客户端：进度核对与待处理问题汇总

核对日期：2026-10-03，快照约 21:11（Asia/Riyadh，UTC+3）。
依据：[Desktop Roadmap v1.1](desktop-client-roadmap.md)、当前工作树、实际冻结产物及已有验证记录。

产品负责人已授权 **macOS 优先，Windows 后续解决**，并允许开发 Agent 继续 Loop Engineering。Windows 缺少测试环境不作为本轮问题。本文件供后续统一收口使用，不要求立即暂停当前循环。

本轮只创建本汇总文档，没有修改实现、路线图、CI 或开发 Agent 的证据记录，没有运行构建、启动桌面应用、重跑整套测试或向其他 Agent 发送消息。已有通过记录与本轮独立确认的代码事实分别说明。

## 1. 当前进度

**总体方向保持健康，已有 macOS 内部可安装候选；强制人工验收、干净机器验收和正式分发尚未闭环。** 新增测试覆盖不能自动消除下列已确认的问题。

| 阶段 | 当前已有内容 | 收口状态 |
| --- | --- | --- |
| D0 Architecture Spike | frozen Streamlit、随机端口 / 实例握手、WKWebView、任务 / 编辑 / 交付切片、PyInstaller `.app` 有验证记录 | 实际 IME、原生文件对话框等仍未完整验收；见 MAC-05 |
| D1 Lifecycle & Data Safety | 稳定目录、单实例、编辑保存、运行 / 暂停退出、崩溃恢复、旧数据接入、诊断；新增多任务退出验证 | 已有实质实现，但首页空闲退出缺陷仍在；见 MAC-01 |
| D2 Installable Beta | DMG、随包运行时、`eng / chi_sim / ara` OCR、文件集成、升级保留数据 | 干净 Mac 普通用户验收未完成；最低系统声明另有缺口，见 MAC-02 |
| D3 Public Distribution | 发布清单、发布说明、产物校验和 macOS CI 定义 | CI 接线有缺陷；签名、公证、真实下载验收仍受外部条件阻塞 |

### 相对上一轮审查的新增进展

- 完整 sweep 从 **17 项扩展到 21 项**。[full3 记录](../build/desktop/e2e-sweep-full3/sweep.json) 为 `ok=true`、21/21 用例 `ok=true`、`failures=[]`；生成时间为 2026-10-03 20:01:02（UTC+3）。本轮读取了记录，未独立重跑。
- D1-16 修复“退出只停止当前窗口任务”的实际问题；D1-17 / D1-18 扩展到三个运行任务、暂停 + 运行、取消中退出。对应 JSON 的 `ok` 均为 true。
- D1-19 验证 50 个历史任务；D1-20 验证删除指定任务不牵连邻近任务与交付快照；D1-21 / D1-22 验证运行中与暂停中的任务拒绝删除。这些属于已有生命周期 / 数据安全边界的验证。
- D3-4 补充实际验收环境和“干净机器验收未完成”的发布披露；但“声明 macOS 13”仍未解决 MAC-02 的组件最低版本不一致。

证据入口：[D1 日志](desktop-d1/evidence-log.md)（D1-16…D1-22）、[D2 日志](desktop-d2/evidence-log.md)（D2-15）、[D3 日志](desktop-d3/evidence-log.md)（D3-4）。D1-19…D1-22 是后续专项记录，不包含在上述 21 项 sweep 中。

记录注意：full3 的原始摘要仍保留 `infrastructure_failures=["two-tasks-exit"]`；D2-15 已解释这是成功重试留下的文本被误分类，并记录脚手架分类修正。这里不将它算作产品失败，也不把原始摘要描述为“基础设施字段全部为零”。

### 本次读取的产物身份

| 项 | 值 |
| --- | --- |
| app version / build id | `0.5.0 / git:1260a82` |
| app built_at | `2026-10-03T15:30:53+00:00`（UTC；当地 18:30:53） |
| 构建状态 | `worktree_dirty=true`，开发构建 |
| 平台 / 后端 | macOS arm64 / PyInstaller 6.22.3 |
| 随包运行时 | Python 3.11.3 / Streamlit 1.64.0 / pywebview 6.2.1 |

这些是本次快照，不承诺后续 Loop 中的产物仍相同。批量修复前应重新核对当前源码、产物和证据。

## 2. 待处理问题索引

| ID | 问题 | 当前状态 | 所属边界 |
| --- | --- | --- | --- |
| MAC-01 | 首页无任务时正常关闭被拒绝 | OPEN；原分支仍在 | D1 正常退出 |
| MAC-02 | 声明 macOS 13，但随包 OCR 组件要求 macOS 15 | OPEN；实际产物已核对 | D2 兼容 / D3 支持声明 |
| MAC-03 | CI 的解释器、桌面依赖与发布说明步骤未接通 | OPEN；配置静态确认，远端未运行 | D2 / D3 构建链 |
| MAC-04 | 发布依赖清单截断版本号，且把范围声明称为 pinned | OPEN；脚本与生成清单均可见 | D1 诊断 / D3 发行记录 |
| MAC-05 | D0 GO 与强制人工验收未完成的口径混用 | OPEN；需要验收与记录收口 | D0 门禁 |

### MAC-01 — 首页无任务时正常关闭被拒绝（P1）

**触发**：首次启动停留在首页，没有选中任务，也没有活跃任务，然后点关窗或正常退出。

**证据**：[exit_flow.py](../folith/desktop/exit_flow.py) 的 `run_exit_flow()` 在 `if not job_id` 分支只设置 `action="nothing-to-save"` 后返回，未设置 `safe_to_exit`。[gui.py](../gui.py) 的 `on_closing()` 则用 `if not report.get("safe_to_exit")` 取消关闭。上次已用不写文件的纯逻辑调用确认；本次读取当前分支，仍保留此行为。

**影响**：空闲退出被误判为不安全，用户可能被迫强制结束。已有 [退出测试](../tests/desktop_exit_flow_test.py) 的无任务用例只断言 action / job_id，没有检查关闭回调所依赖的安全标志。

**后续处理**：无任务且无待处理编辑 / worker 时返回明确可退出结果；保留真正不安全场景的阻止关闭语义。

**验收**：

- 无任务退出报告满足关闭回调契约；测试不能只断言 `nothing-to-save`。
- frozen 应用首次启动首页与回到首页后，正常关窗 / Cmd+Q 都能退出，服务和监听端口回收。
- 有未提交编辑、运行中 / 暂停任务的退出保护仍有效。

### MAC-02 — 支持系统声明与随包 OCR 最低版本不一致（P1）

**证据**：[spec](../packaging/folith-desktop.spec) 与实际 `.app/Contents/Info.plist` 声明 `LSMinimumSystemVersion=13.0`；本轮读取包内 Tesseract 和 libtesseract 的 Mach-O，二者 `LC_BUILD_VERSION` 均为 `minos 15.0`。

实际核对路径：`dist/desktop/Folith.app/Contents/Frameworks/tesseract/bin/tesseract`、`tesseract/lib/libtesseract.5.dylib`。

**影响**：当前产物不足以承诺 macOS 13 / 14 上的随包 OCR 可用。具体低版本运行报错尚未实机验证；仅在说明中添加“未测试 13.x”不能解决组件与声明的冲突。

**后续处理**：核对必需原生组件的最低系统版本，再选择重建低版本兼容组件，或收紧应用 / 手册 / 发布说明的最低系统声明。不要只修改文案后就宣布兼容已通过。

**验收**：

- Info.plist、随包必需原生组件、清单和安装说明的支持边界一致。
- 在最终声明的最低 macOS 上验证启动与三语言 OCR。
- 更新 [干净机器手册](desktop-d2/clean-machine-acceptance.md) 与生成的发布说明，保留实际验收系统记录。

### MAC-03 — CI 构建链存在接线缺口（P1）

来源：[workflow](../.github/workflows/release-gate.yml)、[build_desktop.sh](../scripts/build_desktop.sh)、[build_dmg.sh](../scripts/build_dmg.sh)、[pyproject.toml](../pyproject.toml)、[release_notes.py](../scripts/release_notes.py)。

已确认的三个子问题：

1. **解释器不一致**：workflow 用 `setup-python` 安装依赖，构建脚本默认使用 `$ROOT/venv/bin/python`；workflow 未创建该 venv，也未设置 `FOLITH_BUILD_PYTHON`。干净 checkout 会先在解释器路径上失败。
2. **缺桌面依赖**：workflow 安装 `.[test]` 与打包工具；pywebview 位于可选 `desktop` extra，没有在这些步骤中安装。解决解释器问题后仍需补齐实际桌面构建依赖。
3. **新清单直接校验旧说明**：workflow 生成本次 DMG / manifest 后直接执行 `release_notes.py --check`，未生成对应本次构建的说明；该检查逐字比较，说明含 commit 与 DMG checksum，不能期待仓库旧说明匹配新的 CI 产物。这是本轮补录的同一构建链问题。

**影响**：本机存在 venv 的命令通过，不能证明干净 CI 能执行；“workflow 字符串存在”的测试也不能证明构建链接通。GitHub Actions 尚未真实运行，本项结论来自当前配置，不宣称已观察到远端失败。

**后续处理 / 验收**：

- 构建与 DMG 步骤使用 workflow 实际创建的同一 Python 环境，明确安装桌面依赖。
- 为本次产物生成并保存对应发布说明，保留 stale 检测的实际作用，不用跳过检查掩盖缺口。
- 在无仓库 venv 的环境完成构建链，并取得真实 macOS runner 记录；若其后暴露其他依赖缺失，按实际失败补齐。
- 构建、测试、DMG、manifest 与说明对应同一产物；unsigned 开发产物与正式分发条件分别记录。

### MAC-04 — 发布清单版本号截断与“固定依赖”表述不准确（P2）

**证据**：[release_checks.py](../scripts/release_checks.py) 的 `_bundled_distributions()` 对 `Version: ` 使用 `line[11:]`，而前缀长度是 9；实际包内 `Version: 1.64.0` 被截成 `64.0`。本轮 [manifest](../build/desktop/release-manifest.json) 中的 Streamlit 条目仍为 `64.0`，其他发行版同样受影响。

另一个记录问题：[requirements.txt](../requirements.txt) 与 [requirements-desktop.txt](../requirements-desktop.txt) 主要是版本范围，却在生成说明中称为 `pinned entries` / “固定依赖清单”。固定的打包工具版本不等于全部运行时已经锁定。

**影响**：发行清单不能准确用于排查 / 复现，且容易误读构建可复现程度。随包 build record 的 Streamlit 字段为正确的 `1.64.0`，需校正的是发行依赖清单及其表述。

**后续处理 / 验收**：

- 按实际 metadata 前缀提取完整版本，使用真实 `1.64.0` 等样本检查，不以生成文件与自身重新生成相等代替正确性验证。
- 重生 manifest 与发布说明，逐项对照包内 metadata，完整保留版本字符串。
- 区分“依赖范围声明”“实际解析版本”“已锁定版本”；未完成锁定时如实披露，不宣称完整环境已可复现。

### MAC-05 — D0 工程方向通过与强制人工验收待完成需要区分

**证据**：[D0 决策](desktop-d0/d0-decision.md) 同时包含 `GO — macOS / PyInstaller`、`GO（待人工确认 IME 一项）` 和带已披露风险继续工程的建议；同一记录还将真实 OS 级 IME、原生上传 / 保存面板列为未完成。D1 / D2 的人工项也沿用该状态。

**影响**：读者可能把 packager / 自动化切片的通过，理解为 D0 全部强制项已验收。窗口中的程序化 DOM 赋值、模拟 composition、浏览器 `setInputFiles` 不足以完成真实输入法与系统文件面板验收。

**后续处理**：统一决策与里程碑口径，分别记录“自动化 / packager 证据已通过”“人工强制项待完成”“已授权继续 macOS 本地工程”。用户已允许当前 Loop 继续，这不是撤销授权或要求立即停工。

**验收**：在最终选定 frozen 产物上完成并记录：

- 真实中文输入法候选、组合输入与光标操作；长文本和中英阿混排在轮询 / rerun 中稳定提交、重开读回。
- 系统打开面板导入 PDF / DOCX；系统保存面板确认 / 取消、中文和空格路径，保存副本包含人工修改。
- 默认应用打开交付文件，以及实际关窗 / Cmd+Q 和需要时的强制退出选择。
- D0 决策、各阶段 closeout 与发布说明一致；没有证据的项目保留未验证，不用测试数量代替验收结果。

## 3. 外部验收与发布前条件

以下是已知待验证 / 外部条件，不与代码缺陷混在一起，也不默认要求开发新功能：

| 项目 | 状态与收口方式 |
| --- | --- |
| 干净 Mac 普通用户全流程 | D2 未完成；按现有手册，用实际 DMG 完成自动冒烟 + 人工主流程，验证无 Python / Homebrew / Terminal 依赖 |
| Developer ID、Hardened Runtime、签名、公证 / staple、真实下载 | D3 未完成；等待外部条件后对真正发布产物验证，不把 ad-hoc 构建当正式分发完成 |
| 真实模型调用 | 已有本机可控响应证据；真实服务验证按用户授权和受控范围执行，不宣称已完成 |
| 睡眠 / 唤醒与真实快捷键 | 人工未验证；断连恢复和菜单键位证据不能替代全部真实操作 |
| LibreOffice 实际渲染 | 发现逻辑与缺失 NOT_RUN 有记录，真实渲染未验证；保持可选定位，不默认要求 bundling |
| Windows / Intel Mac / Windows ARM | Windows 经授权延期；其他架构不在首批承诺内，不阻塞本轮 macOS 问题修复 |

## 4. 后续批量收口顺序

1. 复核当前工作树，处理 MAC-01 正常退出契约与 MAC-02 系统兼容边界。
2. 处理 MAC-03 构建链、MAC-04 版本记录；仅运行能验证相应失败的检查。
3. 重建对应候选产物、重切 DMG、更新清单与说明，再对受影响路径取证；已有通过结果未受影响时不无条件重复整套循环。
4. 完成 MAC-05 原生人工验收及干净机器测试，之后按外部条件收口签名 / 公证与实际下载。
5. 每项记录修复位置、对应构建、验证证据和仍未验证的范围，再将 OPEN 改为 RESOLVED。当前阶段无需重写核心 / UI、迁移 Tauri / Electron 或增加 D4 功能。

本文件仅汇总截至上述快照的事实；生产代码仍由开发 Agent 在后续获授权的批量修复中处理。
