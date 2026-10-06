# D0 桌面架构 Spike — 复现指南

本目录是 roadmap `docs/desktop-client-roadmap.md` 的 D0 证据与决策记录。

| 文件 | 内容 |
| --- | --- |
| `environment.md` | 固定实验环境（OS / Python / 依赖 / 外部工具实测值） |
| `evidence-log.md` | 逐任务审计轨迹（假设 → 变更 → 测试 → 证据 → 风险） |
| `d0-decision.md` | D0 决策记录（A–I 节，Go / No-Go / BLOCKED） |

## 本轮新增的实现边界

| 路径 | 作用 |
| --- | --- |
| `folith/desktop/protocol.py` | 实例身份与父子控制通道协议（单行 JSON、startup token、严格校验） |
| `folith/desktop/service.py` | 内部 `--serve` 模式：随机端口、回读实际端口、上报真实 PID |
| `folith/desktop/launcher.py` | 启动顺序、身份核验、进程/端口证据、认证停止与回收 |
| `folith/desktop/process.py` | 跨平台 PID / 父子关系 / 端口监听者查询（含僵尸识别） |
| `folith/desktop/paths.py` | 桌面数据/日志目录、版本与 build 记录 |
| `packaging/folith-desktop.spec` | PyInstaller onedir + `Folith.app`，显式资源清单 |
| `scripts/build_desktop.sh` | 生成 build record → 构建 → 报告体积 |
| `scripts/write_desktop_build_info.py` | 构建记录（版本 / commit / 依赖 / 平台 / 打包后端） |
| `gui.py` | `--serve`（内部）/ `--desktop`（源码桌面模式）/ 冻结默认桌面模式 / `--legacy` |

## 复现命令

```bash
# 1) 源码级身份握手与生命周期门禁
venv/bin/python -m pytest tests/desktop_handshake_test.py -q

# 2) 冻结构建（macOS arm64）
bash scripts/build_desktop.sh

# 3) 冻结产物门禁（含原生窗口探测需 GUI 会话）
FOLITH_DESKTOP_FROZEN_APP="$PWD/dist/desktop/Folith.app/Contents/MacOS/Folith" \
  venv/bin/python -m pytest tests/desktop_frozen_test.py -q
FOLITH_DESKTOP_FROZEN_APP="$PWD/dist/desktop/Folith.app/Contents/MacOS/Folith" \
FOLITH_D0_FROZEN_GUI=1 venv/bin/python -m pytest tests/desktop_frozen_test.py -q

# 4) 冻结原生垂直切片（可控模型响应，零外网模型调用）
venv/bin/python scripts/d0_slice_runner.py --source-kind docx --out /tmp/d0-slice --review \
  --frozen-app "$PWD/dist/desktop/Folith.app/Contents/MacOS/Folith"
# 其它输入：pdf_text / pdf_scanned / docx_arabic / pdf_arabic_scanned
#   --ocr-language "العربية" --target-lang "العربية" --edit-text "…"

# 5) 运行中退出矩阵
venv/bin/python scripts/d0_exit_matrix.py --out /tmp/d0-exit \
  --frozen-app "$PWD/dist/desktop/Folith.app/Contents/MacOS/Folith"

# 6) 原生窗口行为探测（真实 WKWebView）
FOLITH_DESKTOP_DATA_DIR=/tmp/d0-slice/data FOLITH_DESKTOP_LOG_DIR=/tmp/d0-slice/logs \
FOLITH_D0_WEBVIEW_JOB=<job_id> FOLITH_D0_WEBVIEW_EXPECT="<已提交文本>" \
  dist/desktop/Folith.app/Contents/MacOS/Folith \
  --automation-script scripts/desktop_d0_webview_probe.py \
  --automation-result /tmp/d0-webview.json
```

浏览器驱动需要 `node` + `playwright-core` + Chrome；可用
`FOLIO_PLAYWRIGHT_CORE` / `FOLIO_CHROME` 覆盖自动探测。

## 需要人工完成的验收项（脚本无法替代）

- 真实操作系统级中文 IME 组合输入（光标、候选词、丢字检查）
- 原生窗口下载时系统保存面板的确认，以及"打开交付文件 / 显示所在文件夹"
- Windows 端全部 D0 项目（当前 BLOCKED：无 Windows 主机）
- 明确授权后的真实模型调用（范围受控、会产生费用）
