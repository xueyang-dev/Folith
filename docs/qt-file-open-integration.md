# macOS 文件打开集成（Finder / Dock / `open -a` / 命令行）

日期：2026-10-06 ｜ 分支：`dsh/UIDev`（基于客户端基线 `8a0ab90`）

## 问题

在本轮之前，客户端**没有任何 macOS 文件打开集成**：代码里没有 `QFileOpenEvent`
处理，`packaging/folith-qt.spec` 里也没有 `CFBundleDocumentTypes`。后果是三种"用户以为
已经能用了"的路径全部**静默失败**：

| 用户操作 | 之前 | 现在 |
| --- | --- | --- |
| Finder 里双击 PDF/DOCX（Folith 不是默认应用时用「打开方式」） | 应用被拉到前台，什么都不发生 | 文档导入为新任务，落到任务列表 |
| 把文件拖到 Dock 上的 Folith 图标 | 同上 | 同上 |
| `open -a Folith 论文.pdf` | 同上 | 同上 |
| `Folith 论文.pdf`（可执行文件直接带参数） | 参数被忽略 | 同上 |
| 交给它一个不支持的类型（如 `.xlsx`） | 无任何反馈 | 明确说明不支持，并列出支持的格式 |

"打开了但没反应"是这类集成里最糟的失败模式：用户无法判断是软件坏了、文件坏了，还是
自己点错了。所以不支持的格式也必须有反馈。

## 实现

| 位置 | 作用 |
| --- | --- |
| `folith/qt/window.py` `FolithApplication` | 应用对象接收 `QFileOpenEvent`（macOS 把"用本应用打开文件"投递给**应用**，不是窗口），取出本地路径后发 `file_opened` 信号。事件被消费（返回 `True`），否则 Qt 的默认处理会忽略它。 |
| `folith/qt/window.py` `file_open_path()` | 从事件取路径：先 `file()`，取不到再退到 `url().toLocalFile()`。单独抽出来是为了可测——`FolithApplication` 是全局单例，一个进程里造不出第二个。 |
| `folith/qt/window.py` `MainWindow.open_external_files()` | 唯一的外部入口：按 `adapter.SUPPORTED_SUFFIXES`（共享应用层的 `SOURCE_SUFFIXES` 别名）分类，支持的先回任务列表再走正常导入，不支持的如实说明。 |
| `folith/qt/app.py` `ExternalFileQueue` | 启动期间的时序缓冲。`QFileOpenEvent` 可能在主窗口建好**之前**到达，argv 更是一定早于窗口；没有缓冲就会丢路径。`attach()` 时一次性冲刷。 |
| `folith/qt/app.py` `--help` 位置参数 | `Folith 论文.pdf` 这种用法进同一个队列。 |
| `packaging/folith-qt.spec` `CFBundleDocumentTypes` | 让 Finder /「打开方式」把 PDF / DOCX / DOC / TXT / MD 交给本应用。`LSHandlerRank = Alternate`：只做**候选**处理程序，不抢系统默认——PDF/DOCX 的默认应用应该是用户的阅读器/编辑器。 |

## 验证

```bash
venv/bin/python -m pytest tests/qt_macos_file_open_test.py -q     # 14 passed
```

覆盖的内容（每条都能在无头环境独立判定）：

- `build_application()` 建出来的应用确实是 `FolithApplication`，且带 `file_opened`；
- 真 `QFileOpenEvent` 走一遍：路径取对、事件返回 `True`、信号带着路径发出；
- 只给 `url()` 的事件变体、以及什么都不带的事件（返回空串，不崩）；
- `ExternalFileQueue`：窗口就绪前排队不丢、空路径不入队、`attach()` 冲刷、就绪后立即交付；
- `MainWindow.open_external_files()`：支持的类型真的走导入并回到任务列表；契约里**每一种**
  后缀都被接受；不支持的类型不导入但有说明；混合批次只导入支持的那些；空输入是空操作；
- 命令行位置参数解析；
- `packaging/folith-qt.spec` 的 `CFBundleDocumentTypes` 与 `adapter.SUPPORTED_SUFFIXES`
  **不漂移**，且每一项都是 `LSHandlerRank=Alternate`。

## 边界（如实记录）

- **应用已经在运行时不会转发**：Qt 客户端用单实例锁（`folith.desktop.single_instance`），
  第二个实例被拒绝后只做窗口激活，**没有**把文件转发给已运行实例的通道。所以"Folith 开着，
  再双击另一个文档"目前不会导入。要做需要 IPC，属独立范围，本轮没做。
- **冻结 `.app` 上的真实双击未验证**：本轮改动在源码层完成并通过测试，但候选 `.app`
  是上一轮构建的（不含本改动）。`CFBundleDocumentTypes` 的实际生效需要重新构建 + 在
  Finder 里真实点击一次，属人工验证项。
- **UTI 归属是声明式**：`.doc` 用 `com.microsoft.word.doc`、`.md` 用
  `net.daringfireball.markdown`；`.txt` 由 `public.plain-text` 覆盖。测试钉的是"声明覆盖了
  契约里的每一种后缀"，不是"每种 UTI 在现代 macOS 上都解析成同一个后缀"。
