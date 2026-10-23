# Folith 0.7.1 发布说明与验收矩阵

> 状态：**2026-10-22 发布**，作为 0.7.0 的替代版本——`v0.7.0` 从未创建 tag 或 Release，
> 其内容并入本版。原生验收（4,000–5,000 段真实长文档、Windows 实机安装/升级/卸载、双平台
> 负载回归）仍未全部完成；发布是**知情决定**，未通过项在下方验收矩阵里逐条标出，不因为
> 发布了就改成"已过"。

## 版本范围

0.7.1 承接 `release: Folith 0.7.0 with Windows x64 packaging`（`306d183`，2026-10-21）
之后合入 `main` 的全部用户可见变更，并把原计划的 `v0.7.2 · Performance & Responsiveness`
整体并入 —— 里程碑 `v0.7.1 · Stability, Data Integrity & Performance` 现在同时承担稳定性与
性能两条线，#54 #55 #44 #23 都在其中。

| 主题 | 内容 | Issue / PR |
| --- | --- | --- |
| 工作台空转 CPU | 无变化轮询不再整表重绘 | #54 / #58 |
| 知识反馈串行化 | 模型调用移出串行提交关键路径 | #55 / #59 |
| Journal 平方增长 | 知识候选按增量写入，不再每批重写全量 | #56 / #60 |
| 打开大任务卡顿 | 加载遮罩 + 分段计时（已合入）→ 分片填充 + 画像自愈（#51） | #44 / #51 |
| 并发口径 | 补测 1/2/3/4/6/8，三层数值对齐，提示文案改写 | #23 / #70 |
| Windows 运行时 | `runtime_state.json` 并发读取不再误判、Explorer 退出码、卸载器只删自己的文件 | #37 |
| Windows VM 实测 | 游离窗口、准备页项目列表、项目任务行菜单、运行记录措辞、导入守卫 | #50 |
| 分段空白与导出契约 | 切分记录原始空白、`characters_exact` 实测、DOCX 页眉页脚往返 | #53 / #57 |
| 名称冲突 | 抽屉与审校页共用「统一为…/其他/保持原样」选择控件 | #63 / #66 |

> #53 原属 `v0.8.0 · Document Fidelity & CAT Workflow`，#63 原先没有里程碑；两者都已随 #57 / #66
> 进入 `main`，因此会随 0.7.1 一起发布。这需要同步更新 v0.8.0 的计划，否则同一件事会被记两次。

> `CHANGELOG.md` 的 `[Unreleased]` 段假定 #70（#23 并发口径）会合入；#51 已在 `cbb6978` 进入
> `main`，而本文件与 CHANGELOG 本身随 #71 合入。若 #70 被推迟，对应条目要一起撤下。

## 验收矩阵

2026-10-23 Windows 后续验收：PR #68 候选 `2603643` 已在 Windows 11 x64 本机完成
干净源码构建、冻结/安装版 DOCX/PDF 全流程、三语言自带 OCR、实际 0.7.0 → 0.7.1
升级、运行中静默安装/卸载拒绝、用户文件保留，以及 4,000 段生成样例的冻结包强制结束/
恢复/全量审校/最终交付。真实 3,283 段 EPUB 副本的导入、打开、滚动、重开和空闲 CPU
也有原生计时；1/2/3/4/6 档各跑完 801 批次。详见
[完整记录与限制](stability-milestone-4/windows-native-20261023.md)。
以下表格保留发布时的历史判断；新证据针对该候选，不自动代表已发布安装包。
真实模型长文导出、IME、睡眠唤醒、交互式安装器 Retry/Cancel、干净机器及原 VM
仍未完成验收；不据此关闭 #9 或整个 milestone。

依据里程碑门槛逐条核对。**"已过"只指有可复核证据；offscreen / mock 一律不算原生验收。**

| # | 门槛 | 状态 | 证据 / 缺口 |
| --- | --- | --- | --- |
| 1 | 4,000+ 片段运行中 UI 保持响应 | 部分 | 4,200 段 offscreen：空转主线程 CPU 9.4% → 1.0%，`_paint_row` 每 5 秒 ~21,000 次 → 0，每批提交 CPU 0.27 → 0.16 s（#58）。**缺**：真实窗口、真实 provider 下的 CPU 采样 |
| 2 | 导入/打开有及时加载反馈 | 部分 | 遮罩与分段计时已合入（`b0f53e7`）；#51 增加"可见行优先 + 约 4 ms 时间片补齐"。**缺**：原 VM 上 2–3 s 冻结未复现，真机手感未确认 |
| 3 | 等待模型时 CPU 明显降低、提交后刷新有界 | 部分 | 同上第 1 条；交付预检最多每分钟一次。**缺**：真实负载曲线 |
| 4 | 知识反馈移出串行关键路径、commit-wait 不再主导 | 部分 | 809 个既有用例通过（#59）。**缺**：`batch_timing` 真实运行数据，commit-wait 占比未实测 |
| 5 | 并发结论与预设、UI 文案、文档一致，且各档有实测 | 已过 | #70：1/2/3/4/6/8 六档 + 200 ms 延迟对照；4 与 6 同档；原文案"不改变译文质量"已改写 |
| 6 | 3,000–5,000 段真实长文档运行/暂停/中断/恢复/导出，Journal 完整重放 | **未执行** | 需要真实 provider 与真实长文档；这是 0.7.1 的核心承诺，不能由 offscreen 替代 |
| 7 | 工作台与任务卡及时显示 Journal 中的翻译进度 | **未执行** | #52 仍 open（提交后的译文在重开任务前不可见） |
| 8 | Windows 实机安装、升级、运行中安装/卸载、用户文件保护 | 部分 | #37 新增静默安装 → 升级 → 卸载的 CI 往返检查；卸载器只删 `Folith.exe` / `_internal` / `Uninstall.exe`。**缺**：运行中安装/卸载的人工验证 |
| 9 | Windows 打包 CI 完成（#42）、#9 支持范围核对 | 已过（CI） | #37/#50 修复后 Windows x64 Qt candidate 通过（12m55s）；#9 的支持范围表述未复核 |
| 10 | #13/#24/#38 状态与生命周期回归、旧任务数据兼容 | **未执行** | 需要旧任务数据与长时运行 |

结论：**5 条门槛里只有 2 条可判"已过"，其余为"部分"或"未执行"。** 按里程碑自己的管理
规则（修复代码完成不等于验收完成），这批内容原本不满足发布条件；**本次发布是知情决定**：
以 0.7.1 替代从未发布的 0.7.0，并把上面 8 条"部分/未执行"如实留在发布说明里，不因为发了版
就把它们改判为已过。

## 发布时要处理的既有问题（均已在本次处理）

### 1. `v0.7.0` 没有标签，也没有 GitHub Release → 已由 0.7.1 取代

`CHANGELOG.md` 有 `## [0.7.0] - 2026-10-21`，4 个 README（`README.md` / `.en` / `.es` / `.ru`）
都链到 `https://github.com/xueyang-dev/Folith/releases/tag/v0.7.0`，但：

```
$ gh release list --repo xueyang-dev/Folith
Folith v0.6.0   Latest   v0.6.0   about 3 days ago
Folith v0.4.0                    v0.4.0   about 1 month ago

$ git ls-remote --tags origin v0.7.0
(空)
```

也就是说 0.7.0 从未真正发布，README 里的下载链接此前是 404。**决定：跳过 0.7.0**，直接以
0.7.1 作为首个包含 Windows x64 桌面客户端的发布版本。具体做法：

* 4 个 README 的徽章、下载链接与产物文件名全部从 `0.7.0` 改为 `0.7.1`；
* `pyproject.toml` 版本、`User-Agent`、打包脚本示例一并改为 `0.7.1`；
* `CHANGELOG.md` 的 `[0.7.0]` 段保留标题但标注"未发布；内容并入 0.7.1"，其产物文件名
  改为实际存在的 `0.7.1`。

不再补发 0.7.0：它的产物从未构建/上传，而 0.7.1 的验收范围覆盖它。

### 2. 发布门禁中已定位的三个不稳定来源

第一个是**真实竞态**。`pytest (Python 3.11)` 在 PR #51 上的失败不是这个 PR 引入的，而是产品里的一个窄窗口：

```
test_full_slice_import_translate_edit_review_deliver_reopen
  → adapter.submit_edit → core.save_translation_edit → save_job_state
  → RuntimeError: 任务正在后台处理，请等待完成后保存；草稿可以保留。
```

任务已经报 `completed`，但 worker 尚未释放 `.worker.lock`；此时用户保存编辑会被拒绝。
PR #71 的 Windows 回归再次命中这个窗口，导致安装包任务跳过。发布分支现已合入 #78：
终态下等待 worker 最多 2 秒释放锁，活动任务仍立即拒绝保存。该改动有锁持有/释放回归，
本地保存锁与完整导入→翻译→编辑→审校→交付→重开流程共 22 项通过；当前提交的 Windows CI 仍需通过。

第二个是**测试自身依赖调度时序**。`Shared Qt (windows-latest)` 在 docs-only 的 PR #71 上失败：

```
tests/runtime_state_test.py::test_worker_heartbeat_renews_lease_while_pipeline_waits
AssertionError: assert '2026-10-22T12:10:31+00:00' != '2026-10-22T12:10:31+00:00'
```

用例把心跳间隔设成 0.1 s 后 `time.sleep(1.1)`，假设这段时间内心跳线程一定跑过一次。负载高的
runner 上线程被饿住，`last_heartbeat_at` 仍是同一秒，断言就失败。这不是产品缺陷（租约几秒才过期），
而是用例把"固定睡多久"当成"条件已成立"。发布分支已合入 #70：改为有界轮询，
条件成立才继续，超时仍失败，原有心跳与租约断言不变（#73）。

第三个是**CI 找不到已装好的 NSIS**，它拦下的正是 docs-only 的 PR #71：

```
Locate the installer compiler
  → throw "makensis not found after installing NSIS"
```

但日志里 `C:\Program Files (x86)\NSIS\makensis.exe` 是存在的（4 KB 启动器 + 468 KB 主程序），
`choco install nsis` 只是没把它放进本次会话的 `PATH`，而原脚本的回退搜索**只搜 chocolatey 库**，
从不看标准安装目录。这一项已在 `main` 上由 #66 修掉（`fc477b1`：显式搜索
`Program Files (x86)\NSIS` 与 `Program Files\NSIS`，并区分 `Get-Item` 的 `FullName` 与 `Get-Command`
的 `Source`）。**修复方式是补搜索路径，不是重试**——同类失败再次出现时按这个方法处理。

## GitHub Release 正文草稿

> 与 `CHANGELOG.md` 的 `[0.7.1]` 段一致，可直接粘贴。

**Folith 0.7.1 — 长文档不再拖慢界面**

> 0.7.0 从未单独发布，本版是首个包含 Windows x64 桌面客户端的发布。之前若收藏过
> `releases/tag/v0.7.0`，那个地址不存在，请使用本页。

- 工作台空转不再整表重绘：4,200 段 fixture 上空转主线程 CPU 从 9.4% 降到 1.0%。
- 打开大任务先给加载反馈，再分片填充表格；切换任务会丢弃未完成的填充。
- 知识反馈的模型调用不再串在提交路径上；知识候选改为增量写入 journal，日志不再随批次平方增长。
- 文档画像输出不合规时先纠正再重试（最多三次），告警落盘。
- 并发请求数的提示改为如实描述：并发越高越快，但跨批上下文衔接会变弱，边界措辞可能不同。
- Windows：任务运行中不再被读成空闲/已完成；「在资源管理器中显示」不再误报错误；卸载器只删除自己安装的文件。
- 名称冲突（同一实体多种写法）在抽屉与审校页用同一个控件一次选定统一写法。
- 句子切分保留原始空白，解释型重建逐字还原；DOCX 页眉页脚按契约翻译并在往返后保持。

产物：`Folith-Setup-0.7.1-windows-x64.exe`（安装版）、`Folith-Qt-0.7.1-windows-x64.zip`（便携版）、
`Folith-Qt-0.7.1-macos-arm64.dmg`（macOS，未签名/未公证）、`SHA256SUMS.txt`。

## 发布操作清单

仓库**没有** tag 触发的发布工作流（`.github/workflows` 里没有 `tags:` 触发器），因此发布是
人工步骤：产物从 CI 的 `folith-qt-windows` / `folith-qt-macos` artifact 取，再手工附到 Release。

- [x] 决定 `v0.7.0` 处理方式：跳过，由 0.7.1 取代（见上一节）。
- [ ] 验收矩阵第 6、7、10 条（真实长文档 / Windows 实机 / 旧数据兼容）——**本次发布时未完成，
      以知情决定放行**，不得勾选。
- [x] 合并 #51 与 #70，`main` 上 `pytest (3.10/3.11/3.12)`、`Shared Qt`、两个平台 candidate 全绿。
- [x] 4 个 README（非 5 个）的版本徽章、下载链接与产物文件名改到 0.7.1。
- [x] `CHANGELOG.md`：`## [Unreleased]` → `## [0.7.1] - 2026-10-22`；`[0.7.0]` 段标注未发布。
- [ ] 打 `v0.7.1` 标签并创建 GitHub Release，附 `Folith-Setup-0.7.1-windows-x64.exe`、
      `Folith-Qt-0.7.1-windows-x64.zip`、`Folith-Qt-0.7.1-macos-arm64.dmg` 与 `SHA256SUMS.txt`。
- [ ] 关闭里程碑 `v0.7.1 · Stability, Data Integrity & Performance`。
