# 0.5.0 Qt Beta 发布计划

更新：2026-10-11。v0.4.0 保持原 tag 与资产。本轮冻结 0.5.0 受邀 Beta 候选，不创建公开 Release。

## 冻结范围

- 分发主产物：Qt Widgets 原生客户端的 macOS arm64 DMG，最低 macOS 15.0；每次构建复核原生闭包。Windows、Linux、Intel Mac 不在本轮桌面支持声明内。
- 中文主界面；English、Español、Русский、العربية 为实验功能。目录完整性必须通过，但不代表语言语义、字体及 RTL 真机验收通过。
- PDF / DOCX / EPUB / TXT / Markdown 导入；文本层解析、扫描页视觉理解、显式本机 OCR；扫描件视觉连接在实际需要时校验。随包 Tesseract 基线 eng / chi_sim / ara，缺语言包明确失败。
- 准备 → 翻译 → 人工审校 → 交付；包括术语治理、项目隔离的 TM、段落编辑及结构修复、失败恢复、原页对照、交付门禁与不可变快照。
- 保留语言资产与项目记忆；学术模式默认关闭，显式开启或读取既有学术任务时可用。已有任务恢复使用其已保存策略。
- 保留现有原版式 PDF、DOCX、EPUB 和语言资产导出；原版式 PDF 仅适用于 PDF 来源，不宣称 DOCX 原格式写回。
- 日间、夜间、纸面与翠绿主题；冻结现有设计，不再扩展本轮 UI 范围。
- 旧 Streamlit / pywebview 路径保留一个版本。源码与 Pip 的 `folith` / `foliothread` 命令仍为旧启动器；Qt 源码入口为 `python -m folith.qt`，需另装 PySide6。DMG 是本轮 Qt Beta 入口，不能把 Pip 安装说明写成 Qt 安装说明。

EPUB 按主阅读顺序提取无 DRM 正文；TXT 支持 UTF-8、带 BOM 的 UTF-16 与 GB18030；Markdown 提取可读正文、列表及表格文字，代码块、图片与音视频明确列为未翻译。新格式使用现有翻译与重建导出流程，不提供原文件格式写回。

## 本轮不增加

DOCX 原格式写回、TM 模糊匹配、文档跨版本增量同步、新的知识反馈并发或性能策略。VLM 不宣称普遍更准、更快或更便宜；并发标注不宣称已取得真实 API 的端到端收益。

## 发布工程顺序

1. 保留当前已完成的 Qt 设计和修复；只提交源码、测试、文档与品牌资源。开发工具状态、用户任务、源文档、凭据及构建产物不入库。
2. 修复回归红项；用项目声明的 pytest 8 环境逐文件跑测试，避免多文件 Qt / Streamlit 对象污染。检查两套 UI 目录、重复定义与发布卫生。
3. 冻结本地源码提交。从该提交构建 wheel / sdist 与 Qt `.app` / DMG；核对归档内容和源码、产物身份、原生架构及最低系统版本。
4. 由构建记录生成 manifest、发行说明和人工验收文档；执行冻结应用的受控 HTTP 主流程、OCR、恢复、退出、干净 HOME 与 DMG 安装探针；生成同一候选的验收包。
5. 在最终 DMG 上收齐 7 条强制人工项后，才可声明 `PRIVATE_BETA_READY`。本轮不代替真实模型、中文输入法、系统面板、运行中睡眠恢复、quarantine 首启及干净 Mac 验收。
6. 公开分发另需 Developer ID、硬化运行时、签名、公证、staple 与真实下载验证；公开 push / tag / Release 留待后续授权。

## 候选身份与证据

冻结源码提交是候选身份的起点。构建后生成的验收文档引用该提交；其刷新不会改变应用源码，也不因此重造第二个候选。实际身份以 `build/qt-desktop/desktop_build_info.json`、`release-manifest.json` 和 `dmg_record.json` 为准。

历史测试数字只证明各自快照。新记录见 [Beta 收口记录](qt-beta/freeze-0.5.0.md)，现有人工项见 [验收矩阵](qt-beta/acceptance-matrix.md)。历史仓库路径清理只涉及本轮树，不重写 git 历史。
