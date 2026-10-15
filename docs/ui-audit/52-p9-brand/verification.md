# P9 白底应用图标（2026-10-15）

实现完成；专项通过，全量回归和新 DMG 验证进行中。人工系统界面验收尚未完成。沿用 `feat/0.6-native-workbench`，只本地提交，不 push。

## 实现与可复现资产

- 高清品牌标保留不变；1024×1024 透明画布上合成 824×824 白底（四周 100px），标记等比例居中，最大边约为底板 70%，底板下轻微投影。
- 轮廓为指数 4 超椭圆的连续曲线近似，不声称与 Apple 私有系统掩膜精确相同；仅掩膜超采样，品牌标缩小一次，不改色。
- 生成并跟踪 `folith/resources/brand/folith-app-icon-white.png`；正常构建从同一母版和合成函数生成 `.icns`，不改写受版本控制的 PNG。需要重生成 PNG 时显式传 `--png-out`。
- 补齐 `icon_512x512@2x.png` 1024px 帧；Qt 应用 / 关于 / 欢迎页使用同一白底 PNG。旧的用户原始深底素材继续保留。
- 构建记录的 source 现在保留真实母版路径，避免指向已经删除的临时 master。

## 验证

验证失败将阻止本批交付：底板误成透明或方形、1024px 帧遗漏、原标记拉伸、Qt PNG 与构建合成分叉、实际包仍使用旧图标。专项 `desktop_app_identity_test.py`、`brand_assets_test.py`、`brand_regression_test.py`、`qt_client_style_test.py`：31 项通过。含 iconutil 实际往返解包和 1024px 透明角 / 白色底板检查，生成图与跟踪 PNG 按解码像素比较。

[小尺寸浅深底预览](small-sizes.png) · [本机 Chrome / Safari 资产对照](native-assets-comparison.png)。后者仅作同尺寸资产对照，Chrome / Safari 标识属于各自权利人；这不是实际 Dock 截图。Qt 关于页与欢迎页的中文 / 阿拉伯语、浅色 / 深色截图已记录为 `about-*` / `welcome-*`，已查看实际图标与浅深背景。首次加载并生成 64px 图标的本机样本：旧资产 10.008 ms，新资产 6.043 ms，见 [icon-load.json](icon-load.json)；各一次且有缓存 / 初始化影响，只用于排除明显加载停顿，不据此声称新图标更快。

全量回归与 DMG 结果待补齐。图标变化不涉及任务、模型策略与动效；不以此宣称 P4 的严格帧指标通过。

## 人工待签收与边界

- 新包安装后在 Dock、启动台 / 应用列表、Finder 的应用程序目录、⌘Tab 中确认白底、大小、32px 清晰度及系统是否额外套灰底。资产几何正确不代表系统合成效果已验证。
- 深色 / 着色专用 `.icon` / Assets.car 本轮按指南不做，仍留作后续。
- 未挂载 AX / Computer Use；人工键鼠、原生图标缓存与主题切换体验待发布负责人签收。
