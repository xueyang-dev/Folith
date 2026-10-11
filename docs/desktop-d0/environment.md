# D0 固定实验环境（实测）

采集时间：2026-10-03（本地工作树，未提交状态；HEAD `1260a82`）
采集命令见下方"证据命令"。本文件只记录实测值，不记录推测。

## 平台

| 项 | 实测值 |
| --- | --- |
| OS | macOS 27.0.1（BuildVersion 26A434） |
| 架构 | arm64（Apple Silicon，T8132） |
| 内核 | Darwin 27.0.0 |
| 主机名 | MacBook.local |

## 运行时与依赖（D0 基准环境：仓库内 venv）

| 项 | 实测值 |
| --- | --- |
| 解释器 | `<workspace>/venv/bin/python` |
| Python | 3.11.3 |
| Streamlit | 1.64.0 |
| Starlette | 1.6.0 |
| uvicorn | 0.53.0 |
| PyMuPDF | 1.28.2 |
| python-docx | 1.2.0 |
| pandas | 3.0.4 |
| pytest | 8.4.2 |
| Pillow | 12.3.0 |

注意：系统 `python3`（`/Library/Frameworks/Python.framework/Versions/3.11`）另装有
Streamlit 1.62.0，与 venv 不同。D0 全部结论以 venv 1.64.0 为准，冻结构建必须固定该版本。

## 外部工具

| 工具 | 状态 | 证据 |
| --- | --- | --- |
| Tesseract | 已安装 5.5.0 | `/opt/homebrew/bin/tesseract`，leptonica-1.85.0 |
| Tesseract 语言 | `chi_sim`、`eng`、`osd`、`snum` | `tesseract --list-langs`（`/opt/homebrew/share/tessdata/`） |
| `ara` 语言包 | **宿主缺失** | 同上；仓库内 `benchmark/tessdata_fast/ara.traineddata` 可作 D0 备用 |
| LibreOffice | 未安装 | `/Applications/LibreOffice.app/.../soffice` 不存在，`which soffice` 无输出 → 保持 NOT_RUN |
| pywebview | 未安装 | `venv/bin/python -m pip list` 无 pywebview |
| PyInstaller | 未安装（网络可下载 6.22.3） | `pip download --no-deps pyinstaller` 成功 |

## 环境观察（影响 D0 证据解读，非本次工作造成）

宿主存在 2026-10-02 遗留的 13 个 Python/Streamlit 监听进程，占用 8501、8525–8545 等端口，
来自旧启动器路径（`python -m streamlit run app.py --server.port 85xx`）。
本次未清理这些用户进程。D0 桌面模式使用 OS 随机端口，不受其影响；但该现象本身是
"旧路径关闭窗口后服务未回收"的旁证，与 roadmap §2 的判断一致。

## 证据命令

```bash
venv/bin/python -V
venv/bin/python -m pip list
uname -a; sw_vers; arch
tesseract --version; tesseract --list-langs
ls /Applications/LibreOffice.app/Contents/MacOS/soffice; which soffice
lsof -nP -iTCP -sTCP:LISTEN
```

## D0 测试数据目录约定

桌面模式默认：

- 数据：`FOLITH_DESKTOP_DATA_DIR`（默认 macOS `~/Library/Application Support/Folith/data`）
- 日志：`FOLITH_DESKTOP_LOG_DIR`（默认 macOS `~/Library/Logs/Folith`）
- D0 试验使用临时目录，不接入用户旧数据（旧数据接入属 D1，roadmap §3.2）
