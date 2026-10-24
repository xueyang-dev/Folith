# Folith 0.7.2 发布说明与验收矩阵

准备日期：2026-10-24。**发布候选，尚未发布。** 最新正式版本仍为
[v0.7.1](https://github.com/xueyang-dev/Folith/releases/tag/v0.7.1)。
本页记录 v0.7.2 的范围与验收边界，不沿用 0.7.1 的通过结论。

## 版本变化

- 重译先生成候选，展示当前译文、候选与差异，明确采用后才替换（#29，PR #111）。
- 审校建议和批量替换使用统一候选事务；批量默认保护人工译文，逐项报告跳过或冲突（#92，PR #113）。
- 审校决定与文本改写分离，确认问题存在不会将它自动标为已解决（#93，PR #111）。
- 采用时校验段落身份与译文版本；撤销和修改正确失效审校证据、TM 与交付资格；恢复、并发和失败保存保持一致（#94，PR #114）。
- 审校决定先保存任务状态，再晋升项目 TM，避免失败决定被其他任务提前复用（#109，PR #110）。
- 画像区分周期额度用尽与瞬时限流，失败可重试且警告可见（#49，PR #117；补充证据 #121）。
- 搜索筛选后再点同一段可以重新定位原页；页码输入框跟随主题（#83，PR #115/#116；macOS 证据 #129）。
- 工作台在外观、主题色和真实系统明暗变化后立即更新文字颜色，保留未保存编辑（#119，PR #120）。

详细变更见 [CHANGELOG](../CHANGELOG.md)。
[Milestone 5](https://github.com/xueyang-dev/Folith/milestone/5) 的八个 issue
#29/#92/#93/#94/#49/#83/#109/#119 均已关闭；PR #120 是该 milestone 的另一个已关闭条目。
Milestone 关闭记录不能代替最终 0.7.2 包的验收。

### 范围说明

#95/#96/#97/#98 的 GitHub milestone 均为 v0.7.3；相关 PR #112/#118/#122/#123
虽已合入当前 main，本次不把它们列作 v0.7.2 的已验收功能或 milestone 成果。
候选代码包含这些已合并改动，完整回归会覆盖它们；若要从候选移除，需要另行决定，
不能通过省略发布说明声称代码不存在。

## 验收矩阵

| 项目 | 结果与边界 |
| --- | --- |
| 候选、决定分离、批量保护、修订一致性 | 已合并 PR #111/#113/#114，含保存失败、过期候选、幂等、撤销及恢复回归；见 [候选契约记录](milestone-5-candidate-contract-notes-2026-10-24.md)。非最终冻结包人工验收 |
| 决定保存失败不提前晋升 TM | PR #110 已合并；`tm_promotion_failed_save_test.py` 覆盖两条失败保存路径与成功晋升。发布候选的完整回归另记 |
| #49 真实 provider 请求 | 等价 100 页扫描 PDF（95 页无文本层）走产品 OCR，真实请求覆盖额度用尽与退避成功；同书 401–416 页再验证两类 429 和成功画像。见 [真实请求记录 §2/§2.2](milestone-5-verification-gaps-2026-10-24.md) |
| #83 Windows 原生窗口 | issue 评论记录等价 49 页任务、真实鼠标完整定位序列；PR #116 修复页码框与重复点击。非 0.7.2 冻结包复验 |
| #83 macOS 原生窗口 | Apple Silicon、真实 Cocoa 窗口、PySide6 6.11.2；等价 49 页产品导入任务，筛选、重复选择、面板重开和快速连选通过；[证据与驱动](ui-audit/issue-83-macos/README.md)。交互为 QTest 合成事件，非 OS 级点击 |
| #119 Windows/macOS 外观 | PR #120 已合并；Windows 原生证据在 PR，macOS 真实 Cocoa 与实际 OS 明暗切换通过，未保存编辑保留；[macOS 证据](ui-audit/workbench-theme-119/macos/)。程序驱动设置路径，非人工点选 |
| origin/main 完整回归 | ba92723 的运行及历史 Windows 失败调查见 [发布验证记录](release-validation-0.7.2.md)；尚不据此宣称候选可发布 |
| Release Validation | 由 release/0.7.2 推送触发：Linux Python 3.10/3.11/3.12、Windows 全套、双平台候选构建、冻结主流程、OCR、macOS DMG 和 Windows 安装往返；结果见 [发布验证记录](release-validation-0.7.2.md) |
| 0.7.2 原生硬件与安装升级 | 本轮尚未完成；0.7.1 的 Windows 11 x64 验收仅作操作参考，不能计作本版通过 |

## 支持范围与已知限制

桌面候选目标为 Windows x64（安装版和便携版）与 macOS arm64 DMG。
macOS 最低系统版本须以本次构建的原生依赖闭包与 manifest 实测为准；Linux CI
通过不代表提供 Linux 桌面安装包。Windows 10、Windows ARM64、Intel Mac 和独立
干净机器均未获得本版原生验收结论。

- #49 未取得原始 `THE_ROAD_TO_MECCA_(Muhammad_Asad)_301-400.pdf` 或当时失败日志。
  验收使用等价扫描 PDF 和同书 401–416 页（后者全有文本层）；不能倒推出原任务准确根因，
  也不代表所有扫描件通过。
- #83 的 macOS 验证虽是真实 Cocoa 窗口，但由 QTest 合成鼠标/键盘事件驱动，
  并非 OS 级点击；mock macOS、offscreen 与 CI 窗口测试不能代替人工原生验收。
- macOS 包未签名、未公证；Windows 包未签名。首次放行、实际下载 quarantine、
  安装升级及系统集成必须在最终候选上复核，不能由 CI 全绿推定完成。
- 随包 OCR 基线为 Tesseract eng/chi_sim/ara；其他语言需自行添加语言包。
  不随包 PaddleOCR 或 LibreOffice；真实模型连接需自行配置，质量与审校覆盖由任务决定。
- #130 尚未合并；核心保存顺序已由 #110 修复，但 #130 的 TM 写失败处理行为不同，
  不能声称整个 PR 已被逐行覆盖。评估见 [发布验证记录](release-validation-0.7.2.md)。

## 安装与升级候选

候选产物从对应 Release Validation 运行下载；需先确认运行成功并核对构建身份：
`Folith-Setup-0.7.2-windows-x64.exe`、`Folith-Qt-0.7.2-windows-x64.zip` 与
`Folith-Qt-0.7.2-macos-arm64.dmg`；尚无 v0.7.2 Release 下载页。
升级前备份任务、项目资产与设置，并关闭运行中的 Folith。

## 仍需人工验收

以下均为**待做**，记录候选源码提交、构建身份、系统版本、操作、截图及结果。
参考 [0.7.1 原生验收](stability-milestone-4/windows-native-20261023.md) 和
[收口记录](stability-milestone-4/windows-closeout-20261023.md)，新记录不得复制旧通过状态。

- Windows：在实际 Windows 11 x64 上安装最终候选、由真实 0.7.1 升级到 0.7.2、
  启动和重开任务、便携版主流程；运行中安装/卸载保护与交互 Retry/Cancel、卸载后用户文件保留。
- Windows：原生文件面板的中文/空格路径、真实中文 IME、睡眠唤醒与未保存草稿；
  外观切换、实际系统明暗变化、搜索后原页定位与重复点击。
- macOS：真实下载 quarantine 首次放行、挂载 DMG、拖入 Applications、Finder/Dock 启动与退出；
  0.7.1 升级与数据保留、原生文件面板、真实中文 IME、睡眠唤醒及干净 Mac。
- macOS：在最终冻结包以人工键鼠重复 #83 定位序列、#119 外观与真实 OS 切换；
  保留编辑、光标、撤销与高亮，不能只跑源码 QTest。
- 两平台：DOCX/PDF 导入→翻译→候选比较/采用/丢弃→审校决定→交付→退出重开；
  过期候选拒绝、批量保护人工译文、长任务中断恢复、真实模型连接与原有任务工作副本导出。

## 发布前批准

候选验证与人工记录齐备后，由负责人决定合并发布 PR、打并推送 `v0.7.2` tag、
创建 GitHub Release 并上传匹配产物。PR #130 的关闭、合并或改写也需负责人决定。
