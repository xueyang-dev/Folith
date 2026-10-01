# 0.5.0 候选版本发布计划

更新：2026-10-01。v0.4.0 已于 2026-09-22 公开发布。当前候选版本为 0.5.0，正式发布日期待定；不得移动或覆盖 v0.4.0 的 tag 与资产。

## 候选版本范围

- 内部代码包迁移到 `folith`；主命令 `folith`，保留 `foliothread` 发行名和兼容命令。0.5.0 移除 `transpraxis` 命令，需在正式 release notes 告知升级用户。
- 本轮已经实现的性能、任务恢复、审校与 EPUB 交付改进，详见 CHANGELOG 的 Unreleased 区块。
- 日间、夜间、纸面模式；四种非中文界面为实验功能，缺译回退中文。未完成语言审阅与全页面 RTL 验收，不宣称完整本地化。
- DOCX 原格式保真及 Translation Quality Pipeline 是后续工作，不作为本次 0.5.0 的完成承诺。

## 发布顺序

1. 明确选取源码、测试、文档、品牌图和五种语言目录作为本地提交；运行数据、导出源文档及本地过程稿不进入发布单元。
2. 在冻结提交上运行 `python scripts/check_ui_locales.py --allow-incomplete` 与 `python scripts/run_regression.py --tsv <evidence-dir>/regression.tsv`。前者允许已记录的实验缺译，仍阻断新增缺译、资源缺失、额外键和非法占位符。
3. 从同一提交构建 wheel/sdist，运行 `python scripts/check_release_archives.py <dist-dir>` 及 `python scripts/release_preflight.py --dist <dist-dir>`；干净环境安装后从仓库外运行 `scripts/release_smoke.py`。
4. 核对验证记录及未验证范围。正式发布时将 Unreleased 内容移入 `[0.5.0] - <实际发布日期>`，更新发布徽章并重新验证与该提交对应的构建。
5. 经公开发布授权后 push，查看真实 GitHub Actions，创建新 v0.5.0 tag 和 Release；公开 v0.4.0 保持原样。

全量本地化严格检查使用 `python scripts/check_ui_locales.py`，当前尚未通过；实验基线通过不能写成全量翻译完成。

## 历史证据边界

此前的 1031、1509、1526 passed 均属于各自源码快照，不能证明本次候选提交。当前验证以本轮生成的独立记录为准。

真实收费模型、Windows/Linux 实机、原生 pywebview 窗口、全部语言语义与 RTL 页面视觉仍须分别验收。旧 git 历史中的本机路径未清除；历史重写需要单独授权。
