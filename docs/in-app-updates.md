# Folith 应用内更新

跟踪：[Issue #161](https://github.com/xueyang-dev/Folith/issues/161)。首次装入本功能仍需手动安装；之后由客户端完成下载和安装。首版采用全量更新，不后台强制下载/安装。

## 客户端

关于 → 检查更新 → 阅读更新说明 → 下载更新（进度、取消、失败重试）→ 重启并更新。Windows 的状态由主窗口持有，关闭设置页再打开不会丢失下载。关闭应用时下载可能中断，下次重新下载。

安装必须通过现有正常退出路径：先落盘编辑，再停止活跃任务、等待写入操作。取消退出、保存失败、只能强制退出时都不安装，下载包保留在缓存供本次会话重试。任务/术语库/TM/配置目录不参与覆盖。源码运行、未配置签名公钥的 Windows 构建保留手动下载入口。

Windows 仅支持已安装的 frozen x64 构建。NSIS 从已登记的每用户目录升级，无需管理员。助手从系统 PowerShell 启动并隐藏窗口，等待主进程退出；校验 SHA-256、检查空间，备份程序，调用 NSIS `/S /D=原路径`，检查退出码及安装后 EXE ProductVersion，再打开新版并传入原有效数据/日志路径。数据或日志在程序目录内时拒绝自动安装。

安装退出失败、版本不符会恢复原程序并打开它。`日志目录/update.log` 和 `update-result.json` 记录结果。备份为安装目录的同级 `<Folith>.update-backup`；新版成功构建并显示窗口后，后台确认结果并删除该备份。进程启动成功不等于应用健康，未收到窗口启动确认时保留备份，阻止下一次覆盖它。

断电/系统重启、安装助手被终止、新版无法启动时不承诺自动恢复：退出全部 Folith，保留失败程序目录作为诊断，把同级 `.update-backup` 还原为原安装目录，再启动。不要在尚未检查时删除备份。普通安装失败的恢复无需用户操作。对应用内发生的用户数据格式迁移，本更新器不执行逆迁移。

## Windows 发布

需要 Python 的 cryptography、packaging 和既有打包依赖。发行签名私钥不得入库，也不能放入发行包。

首次在受控发布机执行（目标私钥文件必须不存在）：

```powershell
python scripts/create_update_manifest.py --generate-key C:\ReleaseSecrets\folith-update.pem
```

打印的是 base64 公钥；把它固定配置为发布构建的环境变量 `FOLITH_UPDATE_PUBLIC_KEY`。私钥在 Windows 上还需用文件 ACL 限制为发布账户可读（Unix 的 chmod 不能替代 Windows ACL）。该公钥在 PyInstaller 构建时写入包内，运行时不接受环境变量替换信任根。CI 用受保护 secret 保存私钥；不得输出到日志。没有公钥的开发构建不启用安装入口。

```powershell
$env:FOLITH_UPDATE_PUBLIC_KEY = '<上一步的公钥>'
powershell -ExecutionPolicy Bypass -File scripts/build_qt_windows.ps1 -Python <python.exe>
python scripts/create_update_manifest.py --installer dist/installer/Folith-Setup-0.8.0-windows-x64.exe --version 0.8.0 --private-key C:\ReleaseSecrets\folith-update.pem --out dist/installer
```

示例版本需要替换为 pyproject.toml 与发行标签的真实版本。向同一 `vX.Y.Z` Release 上传安装 EXE、`folith-update-windows-x64.json` 和 `.json.sig`，并编写 Release body 供客户端显示。客户端只接受官方仓库相同标签下的 Windows x64 EXE、匹配版本/平台/架构、Ed25519 签名和完整长度/摘要。签名验证失败不自动执行下载文件。GitHub 的 assets 缺少任何一项时不会自动安装。已发布公钥必须保持稳定；换钥需经旧信任链发布迁移版本。

更新签名验证下载的来源与完整性；Windows Authenticode 代码签名是另一条发布流程，用于系统发行者识别。此改动未配置或购买代码签名证书。

## macOS 发布与原生桥接

使用官方 Sparkle 2 发行包，维护者需固定所用版本和校验值，不随应用运行下载框架。`FolithUpdater.m` 在构建机使用 Sparkle 的头文件编译，`ctypes` 只调用小型 C 接口，不增加 PyObjC。Sparkle 使用原生更新窗口提供说明、下载进度、取消和安装。共享 Qt 关于页调用同一检查入口。

构建前配置：

```sh
export FOLITH_SPARKLE_FRAMEWORK=/path/to/pinned-sparkle/Sparkle.framework
export FOLITH_SPARKLE_PUBLIC_KEY='<Sparkle generate_keys 生成的 EdDSA 公钥>'
export FOLITH_SPARKLE_FEED='https://github.com/xueyang-dev/Folith/releases/latest/download/appcast.xml'
bash scripts/build_qt_desktop.sh
```

构建脚本嵌入 framework（包括其 XPC/安装助手）、编译桥接、写 Info.plist，再执行现有 native-min-OS/arm64 检查。关闭自动检查和自动下载安装，开启解压前签名验证。CFBundleVersion 改为发布版本，不能沿用 git SHA 作为更新比较值。没有 Sparkle 的开发构建保留手动下载入口。

Sparkle 要重启时，delegate 延迟安装并发出 Qt 请求，等待用户确认和现有正常退出路径完成，才调用保存的 installHandler。取消后下一次检查会再次提供该重启机会。

真正发布前必须按 [Sparkle 分发文档](https://sparkle-project.org/documentation/) 完成签名和更新资产准备。已提供以下工具，依次对整个 `.app` 的原生文件及嵌套 bundle 签名、验证，提交公证并 staple，验证 Gatekeeper，压缩更新包并调用 Sparkle `generate_appcast`（由 `generate_keys` 建立 Keychain 密钥）：

```sh
python scripts/prepare_macos_update.py --app dist/qt-desktop/Folith.app \
  --identity 'Developer ID Application: <name> (<team>)' \
  --notary-profile folith-notary --sparkle-bin /path/to/pinned-sparkle/bin \
  --out dist/updates/0.8.0
```

输出目录必须是新目录，当前 macOS 发布目标为 arm64。工具不上传或发布。上传更新 archive 和 appcast 前，核对 appcast enclosure 指向正确 Release 标签的 archive，版本与 CFBundleVersion 一致，并含签名与系统支持边界。其他架构需要扩展打包验收后再声明支持。

此改动不把现有未签名 DMG 脚本冒充为已签名发布链；Apple 证书、Keychain、公证账户和 appcast 密钥必须在 macOS 发布环境配置。Windows 私钥 PEM 与 Sparkle Keychain 管理由各自工具使用，不混用格式。

## 验证边界

共享 Qt CI 固定使用 Sparkle 2.10.0，下载 SHA-256 为 `c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c`（官方 Release asset digest）。macOS runner 实际编译并加载桥接库；该检查不启动升级，不替代签名发行包验收。

- 自动化：发布工具与客户端协议、签名/版本/平台/架构、截断/损坏/超长下载、取消/重试、Qt 状态与退出边界。
- Windows 原生助手：用临时编译的原生 EXE 驱动真实 PowerShell，覆盖空格路径、成功升级/重启、安装失败恢复、版本不符恢复和损坏包拒绝，不修改真实安装或注册表。
- 生产 NSIS 脚本联调：用临时应用载荷编译实际安装器逻辑，只隔离注册表/快捷方式名称，经过真实助手执行静默升级和重启，验证用户数据保留，并清理临时注册。
- 尚需发行验收：两版真实 Folith NSIS 安装包从 UI 下载→任务停止→升级→重新打开；网络中断、空间不足、杀助手/断电恢复；真实 macOS 上桥接编译、Cocoa/Sparkle 事件协调、签名/公证/appcast 和升级。

以上实机发行验收完成前，Issue 保持打开，不宣称自动更新已在用户发行版启用。

2026-10-25 本机 Windows 结果：窗口/设置/打包相关回归 174 passed、6 skipped；更新、平台打包契约、语言目录和 CI 分类的定向检查 47 passed、1 skipped（真实 macOS 编译检查在本机跳过）；新增真实 Qt 退出边界与正常退出取消下载用例 4 passed。之后恢复目录保护修正的更新/Windows 助手检查 26 passed。测试集合有重叠，不将这些数字累加为独立用例数。窗口回归存在既有 PySide signal disconnect 和 QMouseEvent 弃用警告。上述结果未替代两版完整 Folith 发行包的 UI 升级验收。
