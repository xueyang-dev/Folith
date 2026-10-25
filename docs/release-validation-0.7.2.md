# v0.7.2 发布验证记录

记录日期：2026-10-24。候选分支：`release/0.7.2`，起点 `ba9272365091fbc0c2b1bd0a243a660500e6375f`。
原始开发分支与未提交改动未动；复用位于该起点的干净独立 worktree。

## main CI 与锁竞争调查

- [82a5ae9 原运行](https://github.com/xueyang-dev/Folith/actions/runs/40109039722)：
  Linux 三版本、macOS shared Qt 与 package 成功，Windows 仅
  `test_concurrent_project_tm_saves_preserve_both_writers` 失败。
  实际栈为 `folith/job_lock.py:20 stream.flush()` 的 PermissionError，
  并非背景描述的 `msvcrt.locking(..., LK_LOCK)` 十次重试耗尽。
- 2026-10-24 对该运行只重跑失败任务一次（attempt 2）；[attempt 2 全绿](https://github.com/xueyang-dev/Folith/actions/runs/40109039722/attempts/2)，Windows release blockers 9 passed、有效失败文件 0。
- [ba92723 / #110 合并后的运行](https://github.com/xueyang-dev/Folith/actions/runs/40111562258)：全绿；不支持 #110 引入稳定回归的判断。

代码中的首次创建竞争：两个 writer 都可在 `path.stat().st_size == 0` 时决定写入 NUL；
若一个 writer 已 flush 并锁住第一个字节，另一个的延迟 flush 写入该字节会被 Windows
强制字节锁拒绝。这是实际并发输入可达的问题，发生于 #110 之前，和 TM 晋升调用次序无关。
Windows 允许锁住 EOF 之外的字节，无需先写入锁文件；依据
[Python msvcrt 文档](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking) 与
[Microsoft 字节范围锁说明](https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files)。
候选移除锁前 NUL 初始化，直接锁定第一个字节，保留现有阻塞/非阻塞语义与重试界限。
新增空文件锁释放测试及原生 Windows 强制字节锁争用测试；macOS 本地结果 13 passed、
1 skipped（Windows 专用测试未运行），Windows 候选完整回归中，新增锁专项 2 passed、release blockers 9 passed、TM 失败保存 3 passed。没有扩大超时。

## #130 与 #110 对比

[#110](https://github.com/xueyang-dev/Folith/pull/110) 已合并，
[#130](https://github.com/xueyang-dev/Folith/pull/130) 已经负责人批准关闭，未合并；两者均把两条审校决定入口
的 TM 晋升推迟到任务状态保存成功之后。

差异：#110 用 `_write_tm_promotions` 并保留 TM 写异常的传播；#130 使用
`_apply_staged_tm_promotions` 并吞掉 `OSError`，是额外可观察行为，不只是命名不同。
#110 的三个测试覆盖 dismiss/legacy preserved 的失败保存与成功晋升；
#130 的三个测试以预置 TM 文件做逐字节比较、legacy human_fixed 的失败保存，
并验证成功晋升不丢已有条目（失败注入分别为 RuntimeError 与 OSError）。
#130 没有针对它新增的“TM 写失败后吞异常”行为的测试。

#130 当时 Fast Checks 的 Linux 完整回归及 package 失败均由三份证据 JSON 的本机绝对路径
触发（旧品牌检查与 release preflight）；已在 main 的 #131 清理，与 TM 顺序修复无关。
其 Release Validation 候选任务均被跳过，同名 gate 显示成功不能当作完整发布验收。
将 #130 的三个新增测试单独取出，在本候选的 #110 实现上执行：3 passed（10 deselected）；
没有引入 #130 的产品代码。

建议：关闭重复 #130；若要补预置条目/human_fixed 测试或改变 TM 异常策略，从当前 main
单独提交最小补丁并验证。直接合并会带回重复修复；rebase 后只应留下有意保留的差异。
2026-10-24 负责人批准后，已附说明关闭 #130；其额外异常处理未合并。

## 版本引用核对

参照 0.7.1 发布提交 `4167946`：pyproject、两处 User-Agent、smoke 断言和构建示例
同步到 0.7.2；macOS manifest/生成说明改从构建版本取值，避免继续报 0.7.1。
发布收口将 README 正式下载切换到 0.7.2；旧验收、历史 changelog 与构建记录保留原版本。
`docs/release-plan.md` 顶部链接本版状态，原 0.6 历史计划保留。

#95/#96/#97/#98 已核实属于 v0.7.3；不列作本版 milestone 成果，
但对应合并代码在候选中，范围边界见发布说明。

## Release Validation

发布准备 PR [#132](https://github.com/xueyang-dev/Folith/pull/132) 已合并。

| 来源 | 提交 | 结果与用途 |
| --- | --- | --- |
| [发布分支](https://github.com/xueyang-dev/Folith/actions/runs/40112954412) | `78b2281` | 全绿：Linux 三版本、Windows 全套、shared Qt、package、双平台冻结流程/OCR/打包、DMG 与安装往返 |
| [PR 完整验证](https://github.com/xueyang-dev/Folith/actions/runs/40112957063) | PR 临时合并提交 `2247f97` | 全绿；其 Git 文件树与 `78b2281` 完全一致 |
| [tag 发布验证](https://github.com/xueyang-dev/Folith/actions/runs/40120278404) | `0a0ef65` / `v0.7.2` | 正式资产来源；与已验证分支文件树相同，保留 tag 构建身份与自动验证报告 |

`v0.7.2` 指向合并提交 `0a0ef6503032560b145ea9198203292144ec5ac2`。
正式 Release 的 macOS DMG、Windows 安装器/便携 ZIP 均取自该 tag 的成功运行，
不混用不同提交的包；`SHA256SUMS.txt` 覆盖三个正式下载文件。
[Release](https://github.com/xueyang-dev/Folith/releases/tag/v0.7.2) 保留未签名/未公证及人工验收未完成的说明。

产物 `folith-qt-macos` / `folith-qt-windows` 内含构建身份、冻结流程/OCR 报告、
DMG/安装往返记录。CI runner 的原生 API 测试与冻结自动探针不等于人工原生硬件验收；
人工清单见 [发布说明](release-notes-0.7.2.md)。

## 本地验证

版本 smoke 与 Qt 发布清单/说明测试：50 passed、2 skipped（无本地冻结包）。
锁、release blockers 与 TM 失败保存测试：13 passed、1 skipped（Windows 强制字节锁）。
本地环境为 macOS / Python 3.11 / PySide6 6.11.2；这些测试不算人工 Cocoa 验收。
