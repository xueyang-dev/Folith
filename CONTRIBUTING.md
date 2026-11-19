# 参与贡献

感谢你愿意改进 Folith · 译页。简体中文是项目的主要工作语言，Issue 和 PR 用中文或英文都可以。

## 先开 Issue

- 报问题：到 [Issues](https://github.com/xueyang-dev/Folith/issues) 说明系统版本、Folith 版本、复现步骤；**不要**贴 API Key，也不要上传涉及版权或保密的文档，截图请先遮掉敏感内容。
- 较大的改动（新功能、界面调整、数据格式变化）请先开 Issue 说一下思路，避免白做。

## 本地开发

```bash
git clone https://github.com/xueyang-dev/Folith.git
cd Folith
python -m venv venv && source venv/bin/activate
python -m pip install -e ".[test]"
python -m folith.qt
```

需要 Python 3.10+。扫描页的行位置识别需要另装 Tesseract（可选）。

## 运行测试

测试用无界面的 Qt 平台运行；涉及 Qt 的测试请**逐个文件**运行，避免多个文件共享的 Qt 对象互相污染：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/brand_regression_test.py
```

提交前至少跑你改动涉及的测试文件，以及 `tests/brand_regression_test.py`、`tests/readme_sync_test.py`。

## 代码约定

项目约定写在 [AGENTS.md](AGENTS.md)，要点：

- 项目页、翻译工作台、编辑器、组件和主题在 macOS 与 Windows 之间共享，不按系统分叉整个视图。
- 涉及系统名称、快捷键、窗口行为、文件路径的改动，两个平台都要考虑：标签和快捷键走 `folith/qt/platform_ui.py`，Cocoa 窗口集成只放在 `folith/qt/macwindow.py`。
- 界面文案用 `i18n.tr(...)`，并同步 `folith/resources/locales/` 里的目录。
- 用户的本地改动不要覆盖；只提交你自己改的文件。
- 四个巨型模块（`core.py`、`folith/qt/views.py`、`folith/qt/adapter.py`、`folith/qt/window.py`）有行数上限，`tests/line_budget_test.py` 会在 PR 里检查：**新逻辑放新模块**，确实无法拆分时才在同一个 PR 里抬上限并写明理由。

## 文档

- 英文 `README.md` 是默认展示页；简体中文 `README.zh-CN.md` 是持续维护的编辑基准，英文、俄文和西班牙文页面跟随它翻译。`README.en.md` 保留为旧链接入口。改了中文 README，请同步其他三份，`tests/readme_sync_test.py` 会检查结构（标题、图片、链接）是否一致。
- README 截图由 `scripts/qt_showcase_screenshots.py` 用合成的演示数据生成，不要使用真实用户文件。

## 提交与 PR

- 一个 PR 做一件事，说明改了什么、怎么验证的、没验证什么。
- 品牌名统一用 Folith · 译页；旧名称只允许出现在兼容用途的位置，由 `tests/brand_regression_test.py` 检查。

### 1.0 之前不自动合并

**1.0 之前，任何 PR 都不自动合并（仓库的 auto-merge 保持关闭）。** CI 全绿是必要条件，不是合并许可。

为什么：把机器能查的和人才能判断的分开。CI 已经能查编译、单元测试、回归、打包和平台边界；留给人的是最贵、也最没法自动化的两件事：

1. **原生验收**：macOS 的 Cocoa 标题栏与窗口行为、Windows 的原生窗口与安装器、冻结版在真实窗口里的流畅度。mock 或 offscreen 通过不算这一层通过。
2. **合并决定**：这个改动现在该不该进 main。

具体口径：

- **产品代码 PR**：由开 PR 的人（人或自动化）写好改动说明与证据，CI 全绿后**交给评审者核对 diff 与证据并决定是否合并**；写 PR 的一侧不自己合。
- **纯文档、纯测试、纯工作流的 PR**：CI 全绿后可以自己合并，但要在 PR 里记录合并提交，便于回溯。
- **合并由人（或明确受托的评审者）执行**：脚本、CI 和 agent 都不能把「CI 绿了」当成「可以合了」。
- 需要「先合后测」的改动（例如只影响冻结版行为的修复），把验证写在 PR 里说明由谁在什么机器上补。

反馈邮箱：xydev@outlook.sa
