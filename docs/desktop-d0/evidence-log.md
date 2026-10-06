# D0 桌面工程证据日志

路线图：`docs/desktop-client-roadmap.md`（v1.1）。本文件是审计轨迹，不替代测试。
每条记录包含：时间 / 里程碑 / 任务 / 假设 / 变更 / 测试 / 结果 / 证据 / 剩余风险 / 下一步。

---

## D0-ENV — 固定实验环境与输入（roadmap §7.1）

- 时间：2026-10-03
- 里程碑：D0
- 任务：固定源码 / build、Python 与确切依赖；准备独立稳定测试数据目录
- 假设：D0 结论必须绑定到可复现的具体版本，而不是"当前环境"
- 变更：新增本目录 `environment.md`（实测记录）；未迁移任何用户旧数据
- 测试：静态采集（`venv/bin/python -V`、`pip list`、`uname`、`sw_vers`、`tesseract --version`）
- 结果：PASS（记录见 `environment.md`）
- 证据：
  - Python 3.11.3（venv `/Users/xueyang/Dev/FolioThread/venv`）
  - Streamlit 1.64.0 / Starlette 1.6.0 / uvicorn 0.53.0 / pytest 8.4.2
  - macOS 27.0.1（build 26A434），arm64（Apple Silicon）
  - Tesseract 5.5.0（`/opt/homebrew/bin/tesseract`），已装语言 `chi_sim`、`eng`、`osd`、`snum`
  - LibreOffice：未安装 → 保持 NOT_RUN
- 剩余风险：
  - 系统 `python3`（3.11.3 framework）安装的是 Streamlit 1.62.0，与 venv 1.64.0 不同；D0 一律以 venv 为准，冻结构建必须固定 1.64.0。
  - 宿主 Tesseract 缺 `ara` 语言包；Arabic 切片需先解决语言包（`benchmark/tessdata_fast/ara.traineddata` 可用），否则 Arabic 项只能记 NOT YET TESTED，不能算通过。
  - 宿主存在 2026-10-02 遗留的 13 个 Streamlit 监听进程（8501、8525–8545 等，来自旧启动器路径），本次未清理用户进程；D0 使用 OS 随机端口，不受其影响，但它本身是旧路径退出回收不可靠的旁证。
- 下一步：D0-2 内部服务入口与身份握手

---

## D0-2a — 内部 `--serve` 服务模式 + 实例身份握手

- 时间：2026-10-03
- 里程碑：D0（roadmap §3.1、§6.1、§7.2）
- 任务：实现冻结可用的内部服务模式、OS 随机端口、父子进程专用通道身份核验
- 假设：Streamlit 1.64.0 的 Starlette/uvicorn 路径支持 `server.port = 0` 并回读实际端口（roadmap §3.1.2）
- 变更：
  - 新增 `folith/desktop/protocol.py`：单行 JSON 控制协议、每实例 `instance_id` + `startup_token`、`verify_ready_message()` 严格校验
  - 新增 `folith/desktop/service.py`：`--serve` 内部模式；stdout 独占为控制通道（Streamlit 输出改道 stderr）；从 stdin 读取 `hello`；绑定 0 端口并回读；上报真实 `service_pid` / `port` / 版本 / build
  - 新增 `folith/desktop/launcher.py`：启动顺序、身份核验、进程树与端口归属证据、认证停止与回收
  - 新增 `folith/desktop/process.py`：跨平台 PID / 父子关系 / 端口监听者查询（僵尸进程识别）
  - 新增 `folith/desktop/paths.py`：桌面数据 / 日志目录、版本与 build id
  - `gui.py`：新增 `--serve`（内部）与 `--desktop`（随机端口 + 握手 + 原生窗口）；旧源码路径保持不变
- 测试：
  - `venv/bin/python -m pytest tests/desktop_handshake_test.py -q` → **10 passed in 8.17s**
  - 手工探针 `/tmp/d0_probe.py`（真实启动 + 停止）
- 结果：PASS（源码路径）
- 证据（探针输出，2026-10-03）：
  - `port: 54066`（OS 随机分配，非 8501）
  - `port_owner_pids: [25333]`，`service_pid: 25333` → `port_ownership_verified: true`
  - `pipe_identity_verified: true`、`process_tree_verified: true`
  - 停止：`ack: true`、`child_gone: true`、`service_gone: true`、`port_released: true`、`residual: []`
- 剩余风险：
  - 依赖 Streamlit 私有 bootstrap 面（`_fix_sys_path` / `_fix_sys_argv` / `load_config_options` / `_install_config_watchers` / `_set_up_signal_handler` / `_run_server_loop` / `Server`）。已加启动前探针：缺失即报 `hook-unavailable` 并退出，不会静默降级。
  - 尚未在冻结构建中验证（D0-3）。
- 下一步：冻结资源收集与 PyInstaller 构建

### D0-2b — 路线图假设修正（需回写路线图）

- 现象：只调用 `bootstrap._install_config_watchers({... "server_port": 0})` 时，`server.port` **仍是默认 8501**，服务与旧进程同时监听 8501（macOS `SO_REUSEADDR`），"OS 随机端口"实际未生效。
- 原因：`streamlit run` 的 CLI 会先调用 `bootstrap.load_config_options(flag_options=...)`；只注册 watcher 不会应用本次 flag（仅在配置文件变化时才应用）。
- 修正：`--serve` 显式调用 `bootstrap.load_config_options(flag_options=...)` 后再注册 watcher。
- 结论：roadmap §3.1.2 的"支持绑定端口 0 后回读实际端口"成立，但**必须同时说明必须先显式加载 flag_options**，否则会静默落回 8501 并可能与其他实例共用端口。
- 复现证据：`venv/bin/python` 内联脚本，`_install_config_watchers({"server_port": 0})` 后 `config.get_option("server.port") == 8501`、`where_defined == <default>`。

### D0-2c — 停止回收的两个真实缺陷（已修复）

1. **僵尸进程被当成存活**：直接子进程退出但未 `wait()` 回收时，`os.kill(pid, 0)` 仍成功，`launcher_child_gone` 误判为 False，导致测试断言失败。
   - 修复：`stop()` 以 `Popen.wait()` 作为直接子进程的权威判据（`_reap_child()`）；`pid_alive()` 增加 POSIX 僵尸识别（`/proc/<pid>/stat` 或 `ps -o stat=`）。
2. **测试内 monkeypatch 污染停止阶段的端口证据**：属于测试隔离问题，已在断言前 `monkeypatch.undo()`。

- 复现命令：`venv/bin/python -m pytest tests/desktop_handshake_test.py -q -k stale_port`（修复前 `launcher_child_gone=False`）
- 修复后：`10 passed`，`residual_pids == []`

---

## D0-2d — Launcher 异常退出的清理（roadmap §3.1.4）

- 时间：2026-10-03
- 测试：`gui.py --desktop --no-browser` 真实启动后，对 launcher 发送 `SIGTERM`（模拟崩溃 / 强制结束）
- 结果：PASS
- 证据：
  - launcher：GONE；service（pid 25923）：GONE；端口 54385：已释放（无 LISTEN）
  - 机制：服务在 stdin EOF 时自行 `server.stop()`，不会比父进程活得更久
- 剩余风险：Windows 上 `stdin` 管道 EOF 行为未验证（D0-4 平台证据另列）。

---

## D0-2e — 无关 localhost 服务不被误认（roadmap §6.1）

- 测试：`test_launcher_ignores_unrelated_localhost_service`（同机起一个无关 HTTP 服务器，端口随机）
- 结果：PASS
- 证据：启动实例端口与 decoy 端口不同；decoy 未收到任何请求（`seen_paths == []`）；`port_ownership_verified is True`
- 说明：身份来自"本 launcher 通过管道写入的 startup token"，HTTP 200 不参与就绪判定。

---

## D0-3 — 冻结构建（PyInstaller，macOS arm64）

- 时间：2026-10-03
- 里程碑：D0（roadmap §5、§6.1、§7.3、§15）
- 任务：单可执行文件双角色（默认桌面启动器 / `--serve` 内部服务）、最小资源收集、macOS `.app`
- 变更：
  - 新增 `packaging/folith-desktop.spec`（PyInstaller onedir + `BUNDLE` → `Folith.app`）
  - 新增 `scripts/build_desktop.sh`（生成 build record → 构建 → 报告体积）
  - 新增 `scripts/write_desktop_build_info.py`（版本 / commit / 依赖 / 平台 / 打包后端记录）
  - 新增 `folith/desktop/paths.py` 的 build record 读取；版本解析优先使用构建记录
  - 新增 `tests/desktop_frozen_test.py`（冻结产物门禁，默认按环境变量开启）
  - `gui.py`：冻结构建默认走桌面模式；新增 `--legacy` 保留旧源码路径
- 构建记录（`build/desktop/desktop_build_info.json`）：
  - app_version 0.5.0；build_id `git:1260a82`；commit `1260a826…`；worktree_dirty true
  - python 3.11.3 / streamlit 1.64.0 / pywebview 6.2.1 / pyinstaller 6.22.3
  - target macos arm64；packaging_backend pyinstaller
- 产物体积：`dist/desktop/Folith.app` = **308 MB**（onedir；含 Streamlit 前端静态资源与 PyObjC/WebKit）
- 测试与结果：
  - `FOLITH_DESKTOP_FROZEN_APP=… venv/bin/python -m pytest tests/desktop_frozen_test.py -q` → **2 passed, 1 skipped**
  - 加 `FOLITH_D0_FROZEN_GUI=1` → **3 passed in 5.65s**
- 证据（冻结产物真实运行）：
  - `--serve` 握手：`frozen: true`、`pipe_identity_verified: true`、`port_ownership_verified: true`、`process_tree_verified: true`
  - 端口 55269/56297 等 OS 随机端口；`service_pid` 为唯一监听者；停止后 `port_released: true`、`residual: []`
  - 无握手直接运行 `--serve` → 退出码 3 + `handshake failed`（不会静默提供服务）
  - 原生 WKWebView（冻结）：`app_content_rendered: true`、`page_errors: []`、`websocket_connected: true`，DOM 文本为真实译页界面（新建任务 / 项目 / 翻译策略 / AI 模型 …）
- 修复的真实缺陷：
  1. **冻结后 app.py 首行即 `ModuleNotFoundError: No module named 'streamlit.runtime.scriptrunner.magic_funcs'`**。原因：Streamlit 在运行时把该 import 注入 app 脚本 AST，静态分析看不到。修复：spec 中显式 `collect_submodules("streamlit.runtime.scriptrunner")` 与 `scriptrunner_utils`。
  2. 冻结包内 `importlib.metadata.version("foliothread")` 会读到仓库 venv 里过期的 editable `foliothread 0.4.0`。修复：不收集该 metadata，改由构建记录提供版本；源码运行优先读 `pyproject.toml`。
- 平台事实：onedir 下 `service_pid == Popen.pid`（无 bootloader 子进程分叉）；onefile 未测。
- 剩余风险：
  - `window_title` 仍是 `Streamlit`，尚未设置原生窗口标题（非 D0 阻断项）。
  - 308 MB 体积偏大，D2 需按实际需要裁剪（当前未收集整个开发环境，但 PyObjC/Streamlit 静态资源占主要体积）。
  - 未做签名 / 公证（D3；本机无凭据）。
- 下一步：D0-4 冻结原生垂直切片（PDF/DOCX 导入、可控模型响应、CAT 编辑提交、正式交付、重开恢复）

---

## D0-4a — 冻结原生垂直切片（DOCX / PDF 文本层 / 扫描 OCR / Arabic）

- 时间：2026-10-03
- 里程碑：D0（roadmap §6.1 最小真实流程）
- 任务：在冻结构建上按真实 UI 走完 导入 → 小型任务 → 工作台编辑 → 提交 → 审校 → 正式交付 → 下载 → 退出 → 重开
- 变更（全部在验证/打包边界，未改业务核心）：
  - `scripts/d0_fixtures.py`：确定性输入（DOCX / 文本层 PDF / 扫描 PDF / Arabic DOCX / Arabic 扫描件）+ manifest（bytes、sha256、text_chars）
  - `scripts/d0_fake_llm_server.py`：本机 OpenAI 兼容端点，复用 `tests/offline_provider.py` 的确定性分发；未识别提示词**显式 500 报错**，不静默返回空
  - `scripts/desktop_d0_slice.js`、`scripts/desktop_d0_reopen.js`：真实浏览器驱动 UI（不注入 session_state / state.json，使用产品自身的 `?task=&section=` 路由）
  - `scripts/d0_slice_runner.py`：编排 stub → 服务（冻结或源码）→ UI 切片 → 磁盘校验 → 关闭 → 重开校验
  - `scripts/d0_exit_matrix.py`：运行中退出矩阵
- 测试与结果（全部 **PASS**）：

| 切片 | 产物 | job | 段数 | 编辑落盘 | 交付 | 下载含编辑 | 快照含编辑 | 重开可读 | 退出回收 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DOCX | 冻结 `.app` | a8d7a3c625e82db1 | 54 | ✅ | final v1 | ✅ | ✅ | ✅ | ✅ |
| PDF 文本层 | 冻结 `.app` | b5de6be30dc0677e | 12 | ✅ | final v1 | ✅ | ✅ | ✅ | ✅ |
| PDF 扫描件（OCR eng） | 冻结 `.app` | e1563f875259bfdb | 3 | ✅ | final v1 | ✅ | ✅ | ✅ | ✅ |
| Arabic 扫描件（OCR ara） | 冻结 `.app` | 9bb1fb66c4916e75 | 3 | ✅ | final v1 | ✅ | ✅ | ✅ | ✅ |
| DOCX → 目标语言 العربية | 冻结 `.app` | aba31e90684e7b3d | 54 | ✅ | final v1 | ✅ | ✅ | ✅ | ✅ |

- 证据要点：
  - 每一步都有截图（`/tmp/d0-frozen-*/screenshots/`），交付文件同时校验**下载副本**与 `delivery_snapshots/v1/translation.docx`（两份都包含人工编辑文本）
  - 关键告警原文：`第 1 段译文已保存；上一次审校已过期，需要重新审校。`、`✓ 交付版本 v1 已创建`
  - 可控模型调用：stub 记录请求种类（probe / style_profile / cleanup / offline 翻译/审校/画像），全程零外网模型调用
  - OCR 真实执行：扫描件 `text_chars=0`（无文本层）→ `ocr_backend=tesseract`、`ocr_language=eng`/`ara`、`ocr_text_direction=rtl`，识别出与生成文本一致的段落
  - 重开：重启服务后 UI 编辑器仍显示已提交的编辑（含 Arabic 文本），交付页可见 v1
- 修复的真实缺陷：
  1. **tessdata 只放 `*.traineddata` 会让 OCR 静默失败**：Tesseract 的 `tsv` 是 config 文件（`configs/tsv`），缺失时 `--psm N tsv` 报 `read_params_file: Can't open tsv`，应用只报"未生成带置信度的 OCR 文本"。修复：随包 tessdata 必须包含 `configs/`、`tessconfigs/`（D2 bundling 必须遵守）。
  2. **Arabic 扫描件必须走 shaping 渲染**：`insert_text` 写出逻辑序字形，Tesseract 忠实读成乱序文本；改用 `insert_htmlbox(direction: rtl)` 后识别正确。这是**测试夹具**问题，不是产品缺陷。
  3. 仓库既有 `_build_pdf(scanned=True)` 夹具为 108 DPI / JPEG q75，真实 OCR 拒绝；D0 夹具改为 300 DPI / q90。
- 剩余风险：见 D0-4c / D0-5。

## D0-4b — 原生窗口（WKWebView）行为

- 时间：2026-10-03
- 任务：真实窗口内的渲染、rerun、长文本、组合输入事件、RTL、下载
- 测试：`scripts/desktop_d0_webview_probe.py`（冻结 `.app` 桌面模式，真实 WKWebView）
- 结果：
  - 渲染：`app_content_rendered=true`、`page_errors=[]`、`websocket_connected=true`、`rerun_observed=true`
  - 重开：新进程会话中编辑器从服务端 hydrate 出上次提交的 961 字符长文本 → 原生窗口能读回已落盘成果
  - 长文本 + 提交：在 WKWebView 中写入 961 字符（含中英阿混排）→ `focusout` → 保存 → 出现"已保存"；磁盘 `state.json` 该段 `human_edited=True`、长度 961 → **真实窗口内的编辑提交链路成立**
  - 组合输入事件：`compositionstart/update/end` 后 value 保留（26 / 52 字符），未丢状态
  - RTL：目标语言 = العربية 时编辑器计算样式 `direction: rtl`、`text-align: right`；目标语言 = 简体中文 时为 `ltr`（符合产品语义）
- **真实缺陷（已修复）**：pywebview 6 的 `ALLOW_DOWNLOADS` 默认 `False`，原生窗口内的下载会被**静默取消**（无对话框、无文件）。修复：`gui.py` 在创建窗口前开启该设置，下载改走系统保存面板；已加回归测试。
### D0-4b-2 — Fragment 隔离轮询（定向观测）

- 测试：`scripts/d0_slice_runner.py --fragment-probe --stub-delay 6.0`（冻结 `.app`，任务运行中观测 12 秒）
- 结果：PASS
- 证据（`/tmp/d0-fragment4/slice.json`）：
  - `marker_survived: true` —— 在侧栏锚点节点上打的 JS 标记在 12 次采样中始终存在，说明期间**没有发生整页 rerun**；
  - `section_changed: true` —— 页面可见文本在无人交互的情况下自行变化（长度 255 → 248 → 254 → 248 波动，即运行中进度 HUD）；
  - 源码依据：`app.py:3261` `@st.fragment(run_every="2s")` 的 `_render_runtime_strip`（注释明确"Fragment 隔离轮询"）。
- 结论：fragment 轮询在真实 WKWebView + 冻结服务下工作，且更新被隔离在 fragment 子树内。

- 未验证（需人工）：
  - 真实操作系统级 IME 组合输入（脚本只能模拟 composition 事件）
  - 原生保存面板的实际确认与落盘（macOS 上 pywebview 走 `NSSavePanel`，模态对话框无法自动化）
- 剩余风险：原生窗口下载的端到端确认仍属人工验收项。

## D0-4c — 运行中退出矩阵

- 时间：2026-10-03
- 测试：`scripts/d0_exit_matrix.py`（冻结 `.app`；stub 人为延迟 6s/请求，使任务停在翻译中途）
- 结果：PASS（可回收、可恢复、不伪完成）
- 证据：
  - 停止前：journal 已落盘 4 段译文；`runtime_state.stage=translation`、`operation=translation_generation`、`status=running`、`正在翻译第 37–40 段`
  - 停止：`acknowledged=true`、`service_gone=true`、`port_released=true`、`residual_pids=[]`、`forced=true`
  - 停止后：journal 8 段译文保留、`delivery_status=draft`（未伪完成）
  - 重开：任务仍存在、journal 仍为 8 段、交付状态仍为 draft，再次关闭同样无残留
- 发现（D1 输入，不是 D0 阻断）：
  - 当前桌面停止路径只停止服务；运行中的 worker 会继续调度后续批次，因此进程需要 **强制终止** 才回收（`forced=true`）。
  - D1 必须按 roadmap §3.3 实现"停止安排新操作 + 有界等待当前操作 + 复用检查点/journal"，而不是依赖终止进程。D0 只要求证明回收成立，本轮已证明，且数据未损坏（journal 完整、状态非 final）。
- 剩余风险：进程被强杀时，正在进行的模型请求可能重复计费（roadmap §3.3 已声明该语义）。

## D0-4g — 脱离仓库运行 / 中文与空格路径 / Applications 位置 / 缺 pywebview 硬失败

- 时间：2026-10-03
- 里程碑：D0（roadmap §6.1「普通桌面启动与开发启动一致…脱离仓库 / venv 运行」、§8「任意启动位置、普通账户、中文及空格路径」、§6.1「不靠浏览器回退」）
- 任务：把冻结 `.app` 复制到仓库外运行，并用中文 + 空格路径走完整切片
- 结果（全部 PASS）：
  1. **脱离仓库运行**：`cp -R dist/desktop/Folith.app /tmp/d0-apps/`，用 `/tmp/d0-apps/Folith.app/Contents/MacOS/Folith` 运行 → `frozen=true`、身份握手通过、完整切片通过（不依赖仓库、不依赖 venv）
  2. **中文与空格路径**：输出目录 `/tmp/d0 中文 路径/切片 输出`、源文件 `中文 文件名 测试文档.docx`、编辑文本 `中文路径与空格编辑 D0-PATH-2026`
     - job `f1ae122f951aaf6d`：任务运行、编辑提交、交付 final v1
     - 下载副本与 `delivery_snapshots/v1/translation.docx` 均含该中文编辑；交付文件是合法 Word 包（9 个 `word/` 条目，`word/document.xml` 内含编辑文本）
     - 重开：编辑器仍显示该中文编辑；退出无残留
  3. **Applications 位置启动**：`~/Applications/Folith.app`（仓库外）→ `open` 启动，握手 / 端口归属通过，数据目录为 macOS 默认位置；对该实例跑扫描件 OCR 切片 → job `ab634f020071db92` PASS（3 段、编辑提交、交付 final、下载与快照含编辑）；`quit app` 后无残留
  4. **缺 pywebview 硬失败（不靠浏览器回退）**：用 `PYTHONPATH` 屏蔽 `webview` 后运行 `gui.py --desktop` → 输出 `[错误] 未安装 pywebview，无法打开原生窗口…浏览器回退不算桌面验证通过`，退出码 **3**，并且回收本次启动的服务（`child_gone/service_gone/port_released` 均 true、`acknowledged=true`）
- 测试残留清理：`~/Applications/Folith.app` 测试副本与 `~/Library/Application Support/Folith/data` 下的 D0 测试任务 / stub 配置已删除（用户原有 `outputs/provider_config.json` 未触碰）。
- 剩余风险：无新增。`/Applications`（系统级）安装属 D2 安装器范围，本轮用 `~/Applications` 证明仓库外启动等价。

## D0-4h — 交付文件"可外部打开"与快照完整性

- 时间：2026-10-03
- 里程碑：D0（roadmap §6.1「交付文件可外部打开」、§5 快照 manifest）
- 任务：用**独立于本产品**的读取者验证交付 DOCX，并核对快照 manifest 的哈希
- 结果：PASS
- 证据（`/tmp/d0 中文 路径/切片 输出`，job `f1ae122f951aaf6d`）：
  - macOS 元数据识别（下载副本与快照副本一致）：
    - `kMDItemContentType = org.openxmlformats.wordprocessingml.document`
    - `kMDItemKind = Microsoft Word document (.docx)`
  - 外部读取者：macOS 自带 `textutil -convert txt -stdout translation.docx` 能读出人工编辑文本 `中文路径与空格编辑 D0-PATH-2026`（命中 1 次）→ 交付文件不是只有本产品能读的私有格式
  - 快照 manifest 交叉校验：`bilingual.docx`、`delivery_manifest.json`、`source_cleanup.json`、`terms.xlsx`、`translation.docx` 五个资产的 `sha256` 与 `size` **全部与磁盘实际文件一致**；`delivery_status=final`、`translation_truth_hash=20e8942fc6e386a1…`
- 剩余风险：未安装 Word/Pages，未做"用办公软件实际打开"的人工确认；已用系统级读取者与内容类型识别替代，人工确认仍属 D2 干净机器验收。

## D0-4f — 打包决策记录所需测量（冷启动 / 重复构建 / 签名条件）

- 时间：2026-10-03
- 里程碑：D0（roadmap §5「决策记录覆盖 … app bundle、resource loading、cold start、Finder launch、相同源码 / 固定依赖的重复构建，以及 signing / hardened runtime / notarization 可行性」）
- 冷启动（冻结 `.app`，Apple Silicon，macOS 27.0.1；方法：`/usr/bin/time -p` + `scripts/desktop_d0_coldstart.py`，从窗口创建到首屏可读文本）：

| 运行 | 进程总耗时 | 窗口 → 首屏 |
| --- | --- | --- |
| 1 | 3.92 s | 2.65 s |
| 2 | 6.95 s | 5.42 s |

  - 服务就绪（launcher 日志同秒）：`launch start` 与 `service ready` 同一秒（<1 s）
  - 说明：两次差异来自磁盘缓存/机器负载，非构建差异；未做多次统计平均
- 重复构建：同一源码 / 固定依赖共构建 4 次，产物均为 **308 MB**，资源清单一致（未出现随机缺资源）
- 签名 / 公证可行性（本机实测）：
  - `security find-identity -v -p codesigning` → **0 valid identities**
  - 产物为 **ad-hoc 签名**（`Signature=adhoc`、`TeamIdentifier=not set`、`Identifier=dev.folith.desktop`），本机 Finder 启动正常
  - 结论：**BLOCKED — 缺 Developer ID 凭据**（roadmap §5 允许将凭据等待记为外部条件，不据此判架构失败）；Hardened Runtime / notarytool / staple 全部 **NOT YET TESTED**
- 剩余风险：真实分发的 Gatekeeper 体验与公证结果必须在 D3 用实际下载产物验证。

## D0-4e — 普通桌面（Finder）启动与桌面环境的工具发现

- 时间：2026-10-03
- 里程碑：D0（roadmap §6.1「普通桌面启动与开发启动一致」、§14「不要假设 Finder / 开始菜单启动有 shell PATH」）
- 任务：用与 Finder 双击等价的方式启动 `.app`，核验身份握手、数据目录、工具发现、退出回收
- 命令与结果：
  - `open dist/desktop/Folith.app` → launcher pid 49792 / service pid 49794；launcher 日志记录 `pipe_identity_verified=true`、`port_ownership_verified=true`（端口 54000）、`process_tree_verified=true`
  - 数据目录解析为 macOS 默认位置：`/Users/xueyang/Library/Application Support/Folith/data`（roadmap §3.2 目标位置）
  - 退出：`osascript -e 'quit app "Folith"'` → 无残留进程、端口释放
- **桌面环境 PATH 实测**：干净环境启动（`env -i HOME=$HOME open …`）时服务进程 `PATH=/usr/bin:/bin:/usr/sbin:/sbin`（launchd 最小 PATH）。在完全相同的 PATH 下：
  - `core._find_tesseract()` → `/opt/homebrew/bin/tesseract`（应用已有绝对 GUI 路径探测，**不依赖 PATH**）→ OCR 发现 PASS
  - LibreOffice 发现仍**只查 PATH**（`which soffice` = None）→ 本机未安装，NOT_RUN 状态**准确**；已证实 roadmap §3.4 的 D2 项（补 Applications 安装位置发现）确有必要
- **端到端切片（Finder 启动实例）**：`scripts/d0_slice_runner.py --source-kind pdf_scanned --url http://127.0.0.1:54265 --data-dir "$HOME/Library/Application Support/Folith/data"` → **PASS**
  - job `ee827ef50d9d7c0b`：3 段、OCR 真实执行（`ocr_backend=tesseract`、`eng`）、编辑提交、交付 final v1、下载与快照均含编辑
  - 最小 PATH 下 OCR 仍成功 → 桌面启动环境的 OCR 能力与开发启动一致
- **新发现（D1 输入）**：`quit app`（Cmd+Q / Dock 退出，macOS 标准退出路径）**绕过启动器的认证停止流程**：launcher 被 AppleEvent 直接终止，服务端日志显示 `control channel closed; stopping service`（stdin EOF 兜底回收）。回收可靠、无残留，但"保存并停止"握手未执行。D1 必须处理应用级退出（`applicationShouldTerminate` / 退出前停止序列），否则 Cmd+Q 与关窗语义不一致。
- **新缺陷（已修复）**：服务进程被外部杀死时，启动器原先静默退出（exit 0、无提示）。修复：`gui.py` 检测到非自身发起的服务退出时打印原因 + 服务日志 / 启动日志路径，并返回独立退出码 9；新增回归测试 `test_launcher_reports_unexpected_service_death`。
- **本轮自引入回归（已修复并复测）**：为上述修复接线 `on_ready` 时，普通（无验证脚本）窗口启动会误走自动化分支并抛 `AttributeError: 'NoneType' object has no attribute 'run'`，窗口立即关闭退出（烟雾测试发现）。修复：`automation is None` 时只启动服务监视线程并返回。复测（冻结 `.app`，真实窗口）：窗口保持打开、服务存活；`kill -9` 服务后输出 `[错误] 服务进程意外退出` + 两条日志路径，窗口关闭、无残留进程。
- 复测门禁：`tests/desktop_frozen_test.py` + `tests/desktop_handshake_test.py` + `tests/gui_launcher_test.py` → **23 passed**（含原生窗口探测）。
- **UI 脚本化不可用（硬证据）**：`osascript -e 'tell application "System Events" to keystroke "x"'` 返回 AppleEvent 超时 `-1712`（无辅助功能授权）。因此**真实操作系统级 IME 组合输入**与**原生保存面板确认**无法自动化，保持"需人工"；本机已安装中文（SCIM ITABC）、日文与阿拉伯键盘输入源，人工验收条件具备。
- **测试残留清理（已执行）**：Finder 启动测试在真实默认位置产生了测试数据（`~/Library/Application Support/Folith/data/` 下的 stub `provider_config.json`、测试任务 `ee827ef50d9d7c0b`、`ocr_profile_cache`、`.onboarded`）。已删除这些**本轮测试残留**（证据保存在 `/tmp/d0-finder/evidence.json` 与本文件），保留产品设计目录本身；用户原有 `outputs/provider_config.json`（OpenCode Go）**未被触碰**（mtime 仍为 10-01 23:31）。

## D0-4d — 平台与授权边界

- **Windows**：本环境无 Windows 主机，无法执行 roadmap §4 的 Windows D0（PyInstaller 目录原型、WebView2、开始菜单/普通桌面快捷方式启动、普通账户验收）。状态：**BLOCKED（缺设备）**，非技术不兼容。
- **真实模型调用**：roadmap §6.1 要求"再各做一次明确授权、范围受控的真实模型调用"。本机 `outputs/provider_config.json` 内存在真实凭据，但该调用会产生真实费用并要求用户明确授权，本轮**未执行**。状态：**NOT YET TESTED（需用户明确授权）**。D0 的架构结论不依赖该项（可控响应已走完整链路）。
- **签名 / 公证**：无 Developer ID 凭据，未验证（roadmap §5 已将其列为外部等待，阻塞 D3 而非 D0）。
- **LibreOffice**：本机未安装 → 保持 NOT_RUN（未虚报）。



---

## D0-4i — 混合中英阿（CN/EN/AR）编辑与导出（冻结 `.app`，已端到端验证）

- 时间：2026-10-03
- 里程碑：D0（roadmap §3.4「分别验证三语言真实扫描件，以及 Arabic 分段、Unicode、RTL、字体、光标、**混合中英阿编辑 / 导出**」；§6.1 必验项「含 Arabic / 混合中英阿、RTL 光标与字体」）
- 背景：此前 D0-4a 已覆盖 DOCX / PDF 文本层 / 扫描 OCR / Arabic DOCX / Arabic 扫描件，但没有**专门的混合中英阿**夹具，也没有"混合段落被人工编辑后导出"的证据。本轮补齐。
- 新增夹具：`scripts/d0_fixtures.py` → `docx_mixed`（`d0-mixed.docx`，5 段，混合段落在第 1 段以便默认编辑落到混合内容）：
  1. `混合段落：模型 v2 输出 «مرحبا بالعالم» 共 42 段，Mixed CN/EN/AR editing.`（三脚本同段）
  2. 纯中文段；3. 纯英文段；4. 纯 Arabic 段（带 `w:bidi`）；5. `恢复轨迹 استعادة 42% trajectory`（中+阿+英+数字）
- 命令：`venv/bin/python scripts/d0_slice_runner.py --source-kind docx_mixed --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith --review --target-lang العربية --edit-text "<混合译文>"` → **ok: true**（job `7686e0f1d33af987`）
- 实测（12/12 步全 OK：导入 → 翻译 5/5 → 编辑提交 → 审校 → 冻结交付 v1 → 下载）：

| 观测 | 结果 |
| --- | --- |
| 分段（Unicode） | 5 段；**第 1、5 段的源文同时含汉字 + Arabic + Latin**；纯 Arabic 段（第 4 段）独立成段，未被拆碎或吞并 |
| 混合人工编辑 | 编辑文本 `فقرة مختلطة: النموذج v2 ينتج «مرحبا بالعالم» بإيجابي…(mixed edit).`（Arabic + Latin `v2` + «» + 数字）提交成功，提示"第 1 段译文已保存" |
| 交付 | `delivery_status=final`，交付版本 v1；`translation.docx` / `bilingual.docx` 等 5 个资产齐备 |
| **导出内容（外部读取者）** | macOS `textutil` 从**下载副本**与**快照副本**的 `translation.docx` 中各读到混合编辑 1 次；`bilingual.docx` 保留混合源文（`混合段落` 1 次、`مرحبا بالعالم` 2 次 = 源文 + 编辑后译文） |
| **RTL / 方向** | 交付 DOCX 的 `word/document.xml` 中 Arabic 段落带 **`w:bidi`**（文档内 5 处）；Arabic 子串 `مرحبا بالعالم` 原样存在（未被反转或乱序） |
| 快照完整性 | `snapshot_manifest.json` 的 5 个资产逐一比对 `sha256` + 字节数 → **0 处不符** |
- 诚实标注：
  - 译文正文来自**受控模型响应**（stub），不是真实翻译质量证据；本轮验证的是**混合脚本的导入 / 分段 / 编辑 / 导出链路**，人工编辑部分是真实输入。
  - 真实操作系统级 **IME 组合输入与光标行为**仍是人工项（无辅助功能授权，D0 已记录）；本轮覆盖的是"混合脚本文本在真实 WebView + 真实导出管线中的往返"。
  - 交付 DOCX 中只有 1 个段落含 Arabic，因为其余 Arabic 源段被 stub 译成了中文；**源侧**的混合性由 `bilingual.docx` 与 `state.json` 证明。
- 下一步：D0 必验项已全部有证据；剩余为人工/外部项。

---

## D0-4j — 多句切换与提交（冻结 `.app`，已端到端验证）

- 时间：2026-10-03
- 里程碑：D0（roadmap §6.1 必验项「中文 IME / 组合输入、**长 textarea、多句切换与提交**」→ 通过证据要求"在实际轮询 / rerun 中连续输入、粘贴、失焦和提交；与预期文字逐项比对，**无丢字、丢状态**"）
- 背景：此前 D0-4d 已覆盖**单段** 961 字符长文本 + 提交 + 重开读回；**多句之间来回切换并分别提交**没有专门证据，本轮补齐。
- 新增：
  - `scripts/desktop_d0_multiseg.js`：在真实 CAT 工作台里依次编辑多段，用**应用自己的保存按钮**提交，每段提交后**切走再切回**读回文本；`--verify-only` 模式用于跨进程复读；
  - `scripts/d0_multiseg_runner.py`：导入 → 翻译完成 → 编辑并提交第 0/2/4 段 → **重启服务**后再复读三段；已加入 `scripts/desktop_e2e_sweep.py`。
- 命令：`venv/bin/python scripts/d0_multiseg_runner.py --out /tmp/d0-multiseg --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith` → **exit 0（ok: true）**
- 实测：

| 观测 | 结果 |
| --- | --- |
| 三段提交（0 / 2 / 4） | 每段都出现"已保存"确认（`saved_alert: true`），保存按钮在草稿落盘后才可用 |
| 切走再切回 | 三段各自读回文本与写入**逐字一致**（`ok: true`） |
| 磁盘状态 | `state.json` 三段 `target` 与写入一致，`human_edited: [0, 2, 4]` |
| **重启服务后复读** | 三段全部读回一致（`verify_ok: true`）→ 不是前端缓存，而是真实落盘 |
| 无丢字 / 丢状态 | 切换、提交、重启三个环节均无丢失 |
- 测试：`tests/desktop_harness_test.py` 新增该验证的接线契约（必须检查磁盘 `human_edited`、必须重启后复读、必须纳入 sweep）。
- 诚实标注：真实操作系统级 **IME 组合输入与光标行为**仍是人工项（无辅助功能授权）；本轮覆盖的是"多段文本在真实 WebView + 真实保存链路 + 进程重启"中的往返与不丢失。

---

## D0-9 — 源码 CLI 启动路径保持不变（roadmap §3.1「源码 CLI 保留解释器启动路径」）

- 时间：2026-10-03
- 里程碑：D0（roadmap §3.1：冻结构建用 `--serve` 只启动 Streamlit、不递归建窗；**源码 CLI 保留解释器启动路径**）
- 动机：桌面工程对 `gui.py` 改动很大（单实例锁、`--desktop`/`--serve`/诊断/旧数据接入/退出流程）。源码用户（`python gui.py`）走的是同一条分派路径，一旦被误导向桌面分支就会破坏既有启动方式——这是回归风险，而此前**没有任何测试覆盖该分派**。
- 实测（源码模式，真实启动，临时数据目录以免污染仓库 `outputs/`）：
  - 命令：`FOLITH_OUTPUT_DIR=/tmp/legacy-check/data venv/bin/python gui.py --legacy --no-browser --port 0`
  - 结果：`http://127.0.0.1:54903` 返回 **HTTP 200**，页面含产品标题；子进程 argv 为
    `python -m streamlit run <repo>/app.py --server.headless true --server.port 0 --server.address 127.0.0.1 --theme.primaryColor #004cfd …` → 仍是**解释器启动路径**，未被桌面分支接管
  - 停止：源码路径的 `finally` 会在子进程仍存活时 `terminate()` 并等待（代码复核 + 假进程单测覆盖）
- 新增回归测试（`tests/gui_launcher_test.py`，共 **10 passed**）：
  1. `--legacy` **绝不**进入桌面路径（`run_desktop` 不被调用），且必须派生 Streamlit 服务；
  2. 冻结模式下**不带** `--legacy` → 走桌面路径，且**不再派生第二个** Streamlit 服务；
  3. 源码模式下显式 `--desktop` → 走桌面路径；
  4. 源码模式无参数 → 保持源码路径（派生服务、正常返回）。
- 诚实标注：真实操作系统级窗口/浏览器打开行为未在单测中触发（用假进程替代）；本轮验证的是**分派语义**与真实源码启动可用性。
