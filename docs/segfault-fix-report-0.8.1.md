# 0.8.1 冻结版随机段错误：根因、修复与验证报告

- 分支：`integration/0.8.1-ui`（worktree `<集成工作树>`），基线 HEAD `e561de7d`，
  修复提交 `534b4c37`（本报告与它的回归测试一起提交）。
- 机器：macOS 27.0.1 / arm64 / 10 核，PySide6 6.11.2（随包 Qt 6.11.2），Python 3.11.3。
- 结论：**根因是 `folith/qt/pdf_panel.py` 的 `PdfPanel.__init__` 与 32149301 新增的 `_place_tools`
  两条代码相交**——构造时"先填后挂"给条目留下 Python `QWidgetItem` 包装器，窄宽度折行时把
  `zoom_box` / `locate_button` 从 `_bar` 搬到 `_tool_bar`，Qt 删掉旧条目，包装器立即失效；
  之后任意新对象落到同一地址，PySide 把它当成那个失效包装器，进程在**不相关的行**崩溃。
  修复：构造时先挂子布局再填控件（提交 `534b4c37`），并补回归测试。

## 1. 崩溃画像（与本机复现）

冻结 App（`17d6e53e` 旧包）在本机 CPU 加压下复现，故障线程原生栈：

```
SignalManager::retrieveMetaObject(_object*)   libpyside6.abi3.6.11.dylib
Sbk_QObject_Init(_object*, _object*, _object*) QtCore.abi3.so
wrap_init ← _PyObject_MakeTpCall ← _PyEval_EvalFrameDefault ...   Python
```

`~/Library/Logs/DiagnosticReports/Folith-2026-11-09-181919.ips`：`EXC_BAD_ACCESS / SIGSEGV`，
`KERN_INVALID_ADDRESS at 0x0`。这正是"新建 QObject 时 BindingManager 命中了旧地址上的失效
包装器"的受害者栈——与指南 §2 给出的第一份栈同类。

本机基线（`17d6e53e` 冻结包，`frozen_rate.sh`，8 轮 × 2 倍 CPU 加压）：

| 轮 | 3 个扫描用例 + 缺语言包用例的 exit_code |
|---|---|
| 1 | 0, 0, 0, 1 |
| 2 | **-11**, 0, 0, 1 |
| 3 | 0, 0, 0, 1 |
| 4 | 0, **-11**, **-11**, 1 |
| 5 | 0, 0, 0, 1 |
| 6 | 0, 0, **-11**, 1 |
| 7 | 0, 0, 0, 1 |
| 8 | 0, 0, 0, 1 |

**3 / 8 轮出现 -11，4 / 24 个扫描用例崩溃（用例级约 17%，轮级约 37%）**。指南里
"12 轮 0 崩溃不能证明干净"的教训在这里同样适用：本机复现率足够高，30 轮全绿才有统计意义。

## 2. 根因

### 2.1 机制（两步相交才成雷）

第一步：`outer.addLayout(inner)` 时，若 `outer` 已挂在控件上而 `inner` 已填过条目，PySide 会在
reparent 每个条目时创建 Python `QWidgetItem` 包装器并登记；反过来（先 `addLayout` 再填）不创建。
本机实验（PySide6 6.11.2）：

```
先填后挂：outer=QVBoxLayout(host) 已挂控件，inner 先 addWidget 两个 → outer.addLayout(inner)
          之后 gc 里 QWidgetItem 包装器 +2
先挂后填：同结构但 inner 为空时 addLayout，再 addWidget → +0
```

第二步：这些包装器登记的 C++ `QWidgetItem` 后来被 Qt 删除（布局搬移/清理/控件重挂），包装器就
变成"失效包装器"。PySide 的 binding manager 仍按地址登记它；之后在**同一地址**分配的新对象
（QObject、QTimer、QTextDocument……）会被解析成这个旧包装器 → 崩溃在完全不相关的行。

### 2.2 具体代码（32149301 引入的相交点）

`folith/qt/pdf_panel.py`（32149301 新增 `_bar` / `_tool_bar` / `_place_tools`，修复前：

```python
self._bar = QHBoxLayout()                                  # 已填 4 个翻页控件
...
self._tool_bar = QHBoxLayout(); self._tool_bar.addStretch(1)
self._wrapped = True
self._place_tools(False)                                   # 把 zoom/locate 也填进 _bar（共 6 项）
layout = QVBoxLayout(self)                                 # 外层布局挂在 PdfPanel 上
layout.addLayout(head)                                     # 先填后挂 → 2 个包装器
layout.addLayout(self._bar)                                # 先填后挂 → 6 个包装器
```

```python
def _place_tools(self, wrapped: bool) -> None:
    ...
    if wrapped:
        self._bar.takeAt(self._bar.count() - 1)            # 行尾弹簧
        for index, widget in enumerate(self._tools):
            self._tool_bar.insertWidget(index, widget)     # 把 zoom/locate 从 _bar 搬走
    else:
        for widget in self._tools:
            self._bar.addWidget(widget)
        self._bar.addStretch(1)
```

`insertWidget`/`addWidget` 把控件从兄弟布局搬走时，Qt 删除它们在原布局里的 `QWidgetItem`；
而这两个条目在构造时已经"先填后挂"过，有 Python 包装器 → 每次状态切换产生 **2 个失效包装器**。

### 2.3 为什么 `baseline` 每个进程必中

`WorkbenchView` 构造时必建 `PdfPanel`（views.py:3578），左栏固定宽 400
（`components.ClippedWidget(self.left_stack, 400)`）；应用完整 QSS 后
`_one_row_width() = 469 > 400`，所以 `window.show()` 的首次布局就把 `_place_tools` 置为
`wrapped=True`。**不需要用户点「原页」**：每个 OCR baseline 用例进程都会产生这 2 个失效包装器，
一轮 3 个用例约 6 个。之后翻译/审校/冻结交付阶段的大量分配只是"踩雷"的时机。

### 2.4 证据

| 证据 | 结果 |
|---|---|
| 机制实验 | 先填后挂 +2 包装器；先挂后填 +0 |
| 指针差分（修复前） | `PdfPanel()` 后 8 个 `QWidgetItem` 包装器；强制一次 `_place_tools(True)` 后 **2 个包装器指针不在任何活布局条目里** |
| 运行时探针（修复前，合作树） | `pdf_panel.py:130/131/132` 三个 `addLayout` 站点，实测 `+6`、`+2`、`+0` 包装器 |
| 真实 MainWindow 全流程扫描（只读插件） | 全流程 63 个"先填后挂"站点被执行；**只有 `pdf_panel.py:131` 留下 stale=2**，导入→翻译→审校→冻结交付结束后仍然只有这 2 个 |
| 冻结 App 字节码 A/B | 旧包 `PdfPanel.__init__` 里存在 `_place_tools` 调用（源码行 276）；新包不存在（`_place_tools` 行 284）→ 验收用的就是修复后的代码 |
| 32149301 归属 | `_bar`/`_tool_bar`/`_place_tools` 全部为该提交新增；`head`/外层 layout 是历史代码。"先填后挂"是历史习惯，"搬控件"是这次新增的一半——两者相交才成雷，正好解释 `2d41a64e` 0/12 而 `32149301` 起崩 |

同时解释了指南里的两条既有事实：11c7ff42 修掉 `delivery_panel.py` 的 6 处先填后挂后仍然崩
（那是**另一批**库存，不是本雷的引信）；`DeliveryWorkspace` 单独反复 `set_model` 不崩
（雷在窗口启动时构造的 `PdfPanel`，不在交付页自身）。

## 3. 修复

提交 `534b4c37`，`folith/qt/pdf_panel.py`：`PdfPanel.__init__` 改为**先建外层布局、把
`head` / `_bar` / `_tool_bar` 挂上去，再往里放控件**；工具默认放在 `_bar`（`_wrapped = False`），
`_place_tools` 的折行行为保持不变。搬移逻辑本身不需要改：没有包装器时，Qt 删除旧条目是安全的。

```python
layout = QVBoxLayout(self)
layout.setContentsMargins(0, 8, 0, 0)
head = QHBoxLayout(); layout.addLayout(head); ...            # 先挂后填
self._bar = QHBoxLayout(); layout.addLayout(self._bar); ...  # 先挂后填
self._tool_bar = QHBoxLayout(); layout.addLayout(self._tool_bar)
self._tool_bar.addStretch(1)
for widget in self._tools: self._bar.addWidget(widget)
self._bar.addStretch(1)
self._wrapped = False
```

修复后探针复测（合作树，`PdfPanel()` + `show()` + 强制折行往返 + resize 320/1000 往返）：

- 构造新增包装器 **0**；
- `_place_tools` 往返（18 次搬控件）新增包装器 **0**、失效包装器 **0**。

## 4. 回归测试

`tests/qt_layout_item_wrapper_test.py::test_pdf_panel_builds_and_rewraps_without_item_wrappers`：

1. 构造 `PdfPanel()` 前后不得新增 `QWidgetItem` 包装器（修复前会 +8，这条会红）；
2. 强制 `_place_tools(True/False)` 往返与 320/1000 resize 往返后仍不得新增。

同类已有测试（`test_delivery_workspace_builds_and_rebuilds_without_item_wrappers` 等）覆盖交付页与
其它重建路径；本文件 7 条用例全绿。

## 5. 验证结果

### 5.1 修复后冻结版 30 轮（本机、CPU 加压 ×2）

同一台机器、同一加压脚本（`frozen_rate.sh`，10 核 × 2 忙循环），修复后冻结 App
（字节码已核对含 `534b4c37`，见 §5.3）跑 30 轮：

```
RESULT fix_pdf (534b4c37): 0 crashes in 30 runs
```

- 30 轮 × 4 个用例 = **120 次 App 启动，0 个 -11**；每轮 exit_code 都是 `0, 0, 0, 1`
  （第 4 个是缺语言包用例的预期 1）。
- 对照：同一脚本、同一机器上旧包（`17d6e53e`）8 轮里 3 轮出现 -11（4/24 个扫描用例，
  用例级约 17%、轮级约 37%）。若真实崩溃率仍是旧包水平，30 轮全过的概率
  ≈ 0.625³⁰ ≈ 8×10⁻⁷。
- 完整输出：`/tmp/frozen_fix_pdf.txt`（30 行 run + RESULT 行）。

### 5.2 测试

| 命令 | 结果 |
|---|---|
| `pytest tests/qt_layout_item_wrapper_test.py` | 7 passed |
| `pytest tests/qt_pdf_panel_test.py` | 13 passed |
| `pytest tests/qt_delivery_workspace_test.py tests/qt_review_panel_test.py` | 66 passed |
| `scripts/run_tests.py tests -q`（全量） | **3 failed / 5493 passed / 34 skipped**（18:02），3 个失败都与本次改动无关，见下 |

全量套件 3 个失败的解释（均已单独复核）：

| 失败 | 性质 |
|---|---|
| `qt_layout_item_wrapper_test::test_project_overview_rebuild_creates_no_item_wrappers`<br>`qt_layout_item_wrapper_test::test_unsorted_terms_rebuild_creates_no_item_wrappers` | 指南已列的**顺序相关**失败（`docs/segfault-bisect-guide-0.8.1.md` §6 提到 main 上就有这两条）；该文件单独跑 **7 passed**。与本次改动无关。 |
| `qt_acceptance_kit_test::test_quickstart_is_a_usable_one_page_guide` | **本机 build 目录残留**：`build/qt-desktop/dmg_record.json` 是 11-07 为 `17d6e53e` 候选打的本地 DMG 记录，而 `docs/qt-beta/acceptance-quickstart.md` 里是另一个候选的 sha；这个记录是未跟踪的构建产物，CI 干净 checkout 上没有它，该断言会被 `if record.is_file()` 跳过。把记录临时移走后该用例 **1 passed**。 |

另：本轮新增的 `docs/segfault-*.md` 与 `scripts/qt_stale_wrapper_probe.py` 里的本机 worktree 绝对路径
会命中 `brand_regression_test` 的旧品牌规则；按该测试**既有的逐行 allowlist 机制**（"由维护者把它归入
真实兼容边界"，与 `docs/desktop-d0/environment.md` 的 `FolioThread/venv` 同类）登记为证据路径，
没有改动任何断言；`tests/brand_regression_test.py` 重跑 **3 passed**。

### 5.3 已核对的构建

- `scripts/build_qt_desktop.sh --clean` 在合作树构建，build record：`build_id git:e561de7d`、
  `worktree_dirty true`（= `e561de7d` + 未提交的 `534b4c37` 内容）；PyInstaller 的 `PYTHONPATH`
  首个条目是 `<集成工作树>`，构建日志引用合作树文件。
- 字节码 A/B（见 §2.4）确认新包里的 `PdfPanel.__init__` 不再调用 `_place_tools`。

## 6. 没验证的部分 / 限制

1. **CI 没跑**：按指南要求，没有推送、没有合并 PR #313、没有打 tag；`macOS Qt candidate`
   连续 2 次全绿需要在用户同意推送后由 CI 完成。
2. **历史技术债仍在**：全仓仍有 61 处"先填后挂"（审计 `docs/segfault-audit-static.md` §5），
   32149301 之前的代码就有；它们目前"只制造包装器、不制造失效包装器"，不解释本崩溃，但任何
   将来在这些控件上做"搬移/清理条目"的改动都会把库存变成雷。最高危：`terms_panel.py:254`、
   `followup_panel.py:108`（卡片会重建）、`style_cards.py:190`、`setup_panel.py:229`、
   `review_panel.py:593/594`。
3. **第二份崩溃栈（`~QListWidgetItemWrapper ← QListWidget.clear()`）没有单独定论**：
   `activity_panel.set_rows` / `review_panel` 的 `QListWidget` 用法没有 Python 跨 `clear()`
   持有条目的证据，且 32149301 未改这些代码。若 30 轮后再出现该栈，建议单独立项审计。
4. **AX 修补不是原因**：与本修复无关；`macos_accessibility.py` 未改动。
5. **只在本机复现**：修复前后都用同一台机器、同一加压脚本；跨机器（CI runner）未验证。
6. **全量套件在本机不是严格全绿**：3 个失败已逐个归因（2 个是指南已列的顺序相关失败、
   1 个是本机过期 DMG 记录），但"全绿"最终要等干净 checkout / CI 复跑确认；本机
   `tests/fixtures/docx_contract/` 下的 9 个受控夹具会被 `docx_contract` 用例重写，
   跑完套件需 `git checkout -- tests/fixtures/docx_contract/` 还原（本次已还原）。

## 7. 复现与验收命令

```bash
# 基线（旧包）加压：3/8 轮出现 -11
/tmp/frozen_rate.sh <集成工作树> 8 base17d6e53e 2

# 构建修复后的冻结版（约 1 分钟，PyInstaller 缓存命中时）
cd <集成工作树>
FOLITH_BUILD_PYTHON=<项目虚拟环境>/bin/python \
  bash scripts/build_qt_desktop.sh --clean

# 30 轮验收（每轮 3 个扫描用例 + 缺语言包用例；CPU 加压 2×核数忙循环）
/tmp/frozen_rate.sh <集成工作树> 30 fix_pdf 2

# 包装器探针（合作树；pdf 场景应 0 泄漏）
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/qt_stale_wrapper_probe.py \
  --scenario pdf --in-process --repeat 1 --json /tmp/probe_pdf_fixed.json
```

## 8. 后续（需要用户决定）

1. 推送 `534b4c37`（修复 + 回归测试）与 `de2b8e74`（还原 CI 实验）到 `integration/0.8.1-ui`
   （PR #313），跑 `macOS Qt candidate` 连续 2 次。
2. 合入 main 前确认：临时 CI 实验提交 `e561de7d` 已被 `de2b8e74` 还原（本次已做），
   不要再把它带进 main。
3. `scripts/qt_ocr_baseline_runner.py` 的本地调试补丁（两处 `LOCAL DEBUG ONLY`）已还原（本次已做）。
4. 更新 `docs/release-notes-0.8.1.md`（CI run id、资产 SHA-256、崩溃状态）。
5. 可选：给 `_place_tools` 的弹簧 `takeAt` 加不变式防御（审计建议）；把"先挂后填"作为
   Qt 布局构建的通用规则写进 `AGENTS.md`/评审清单，逐步清掉 61 处历史债。
6. 可选：`docs/segfault-audit-static.md`、`docs/segfault-audit-harness.md` 与
   `scripts/qt_stale_wrapper_probe.py` 是本次诊断的证据与工具，是否随 PR 提交由用户决定
   （当前只在 worktree 里，未跟踪/未提交）。
