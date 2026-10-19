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

## 文档

- 简体中文 `README.md` 是持续维护的版本；`README.en.md`、`README.ru.md`、`README.es.md` 跟随它翻译。改了中文 README，请同步其他三份，`tests/readme_sync_test.py` 会检查结构（标题、图片、链接）是否一致。
- README 截图由 `scripts/qt_showcase_screenshots.py` 用合成的演示数据生成，不要使用真实用户文件。

## 提交与 PR

- 一个 PR 做一件事，说明改了什么、怎么验证的、没验证什么。
- 品牌名统一用 Folith · 译页；旧名称只允许出现在兼容用途的位置，由 `tests/brand_regression_test.py` 检查。

反馈邮箱：xydev@outlook.sa
