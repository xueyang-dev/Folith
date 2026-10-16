# Folith·译页 上线前命名检查清单

> 检查日期：2026-10-16。下表"当前状态"来自当天对 PyPI、GitHub API、whois / RDAP 的查询，
> 只说明"此刻没人注册"，不等于商标可用；商标部分请以代理机构的正式检索为准。

## 0. 一页总览

| # | 项目 | 当前状态 | 阻塞上线？ | 动作 |
| --- | --- | --- | --- | --- |
| 1 | 商标 `Folith`（第 9 / 42 / 41 类） | 未检索 | **是** | 委托代理检索并申请 |
| 2 | 商标 `译页` | 未检索；描述性强，驳回风险高 | 否（可先用不注册） | 一起检索，做好被驳回的准备 |
| 3 | 域名 `folith.dev` | 未注册 | **是**（bundle id 基于它） | 立即注册 |
| 4 | 域名 `folith.app` | 未注册 | 否 | 建议注册，作官网 |
| 5 | 域名 `folith.com` | **已被占用**（2019 年注册，IONOS，跳转到一个同名 Twitch 频道） | 否 | 可选：询价收购 |
| 6 | 域名 `folith.cn` / `.ai` / `.io` | 未注册 | 否 | 面向国内用户则注册 `.cn` |
| 7 | PyPI `folith` | 未注册 | **是** | 用 `folith` 发首个公开版 |
| 8 | PyPI `foliothread` | 未注册（**从未发布过**） | 否 | 不发布；只在本地兼容 |
| 9 | GitHub 组织 `folith` | 未注册 | 否 | 建议创建并迁移仓库 |
| 10 | macOS bundle id | `dev.folith.qt`、`dev.folith.desktop` 两个并存 | **是** | 首个公开版前定稿 |
| 11 | 代码里的 `foliothread` / `FOLIOTHREAD_*` | 约 37 个环境变量 + 发行包名 | 发行包名是；环境变量否 | 见第 6 节 |

"阻塞上线"指：公开以后再改，成本会明显上升，或者会让别人先占走。

## 1. 商标

> 以下是检查清单，不是法律意见。

**要查、要申请的标**

| 标 | 类型 | 说明 |
| --- | --- | --- |
| `Folith` | 文字 | 核心，必须申请 |
| `译页` | 文字 | "翻译 + 页面"偏描述性，商标局可能以缺乏显著性驳回 |
| `Folith·译页` | 组合文字 | `译页` 单独过不了时，组合标更容易注册 |
| 两页 + 文/A + 指针 | 图形 | "文/A"和指针都是品类通用元素，显著性弱；建议等 logo 定稿后再申请 |

**类别（尼斯分类）**

| 类 | 覆盖 | 优先级 |
| --- | --- | --- |
| 第 9 类 | 可下载的计算机软件、应用程序 | 必须 |
| 第 42 类 | 软件即服务（SaaS）、软件设计开发、在线平台 | 必须（以后做云端或订阅就要用到） |
| 第 41 类 | 翻译服务、笔译 / 口译 | 建议（产品的业务就是翻译） |

**地区**：中国（国家知识产权局）是第一优先；要做海外，再加美国（USPTO）和欧盟（EUIPO），
或者用马德里体系一次指定多国。

**自查入口**（正式检索交给代理）：

- 中国：国家知识产权局商标网上查询
- 美国：USPTO Trademark Search（tmsearch.uspto.gov）
- 欧盟及多国：TMview（tmdn.org/tmview）、WIPO Global Brand Database（branddb.wipo.int）

**自查要点**：除了 `Folith` 本身，还要查近似拼写（`Folio`、`Folit`、`Folyth`），
重点看第 9 / 42 类里有没有翻译或文档软件。

## 2. 域名

- [ ] **注册 `folith.dev`**：bundle id `dev.folith.*` 是它的反向域名。不持有这个域名就用它当
      bundle id，本身不违规，但被别人注册后会很难看，也没法用它做 Apple 账号 / 公证的关联。
- [ ] 注册 `folith.app`，作官网和下载页（`.app` / `.dev` 强制 HTTPS，正合适）。
- [ ] 面向国内用户：注册 `folith.cn`（需要实名认证）。官网如果托管在大陆，还要办 ICP 备案。
- [ ] 可选：`folith.ai`（贵，只作防御）。
- [ ] 可选：通过注册商或中介询价 `folith.com`。现持有人只把它跳转到一个约 180 人关注的
      Twitch 频道，有机会买到，但不必为此推迟上线。
- [ ] 所有域名打开自动续费，注册邮箱用长期可用的地址。

## 3. PyPI

`folith` 和 `foliothread` 在 PyPI 上**都没有注册**，而且 `foliothread` 从来没有公开发布过。
所以迁移不涉及任何 PyPI 上的现有用户，可以直接改名。

- [ ] 把 `[project].name` 从 `foliothread` 改成 `folith`（见第 6 节第一阶段）。
- [ ] 先发到 TestPyPI，跑一遍 `scripts/release_smoke.py`。
- [ ] 正式发布 `folith 0.5.x / 0.6.0` 来占名。PyPI 不能"预留"名字，只发占位包违反 PEP 541，
      所以要发一个能用的真版本。
- [ ] 用 GitHub Actions 的 Trusted Publishing 发布，不在本机保存 PyPI token。
- [ ] **不**发布 `foliothread`：没人从 PyPI 装过它，发出去反而多出一个要长期维护的名字。

## 4. GitHub 与社交账号

- [ ] 创建 GitHub 组织 `folith`（当前未注册），把 `xueyang-dev/Folith` 转移过去。
      GitHub 会自动把旧地址重定向到新地址，但下面这些要手动改：
  - `pyproject.toml` 里的 `[project.urls]`
  - README 里的 releases 链接和徽章
  - 本地 `git remote set-url origin git@github.com:folith/Folith.git`
- [ ] 不迁移也行，但要在首个公开版前定下来：公开以后，链接会被到处引用。
- [ ] 以下没有检查，按目标用户选：X、小红书、微信公众号、知乎、少数派、Product Hunt。
      原则是同一个名字到处一致，`folith` 被占就统一用 `folithapp`。

## 5. App 内部标识（公开后基本改不了）

| 标识 | 当前值 | 位置 | 建议 |
| --- | --- | --- | --- |
| bundle id（Qt） | `dev.folith.qt` | `packaging/folith-qt.spec:183` | 定稿。保留也可以，但 `qt` 是实现细节；要换就在首个公开版前一次换掉 |
| bundle id（旧 pywebview） | `dev.folith.desktop` | `packaging/folith-desktop.spec:170` | 如果不再发布，就退役，避免两个 id 并存 |
| 数据目录 | `~/Library/Application Support/Folith` | `folith/desktop/paths.py:22` | 已经是新名字，不动 |
| QSettings | `QSettings("Folith", "QtDesktop")`，没设置 organization domain | `folith/qt/window.py:133`、`3284` | 低优先级：macOS 会据此生成 `com.folith.*` 的偏好文件，而 `com` 这个域名不是你的。真要改，需要先写一次性迁移，把旧的 plist 读出来 |

bundle id 改了以后，macOS 会当成另一个 App：文件访问、通知等权限要重新授予，公证记录也要重新生成。
数据目录和 QSettings 不受 bundle id 影响。

## 6. `foliothread` → `folith` 迁移方案

### 现状盘点

| 类别 | 内容 | 用户能看到吗 | 处理 |
| --- | --- | --- | --- |
| 发行包名 | `pyproject.toml:6` 中的 `name = "foliothread"` | 能（`pip install`） | **第一阶段改** |
| 版本查询 | `folith/desktop/paths.py:142` 中的 `version("foliothread")`；`scripts/release_smoke.py:152` | 间接 | 第一阶段改 |
| console 入口 | `foliothread = "gui:main"` | 能 | 保留一到两个版本，打印弃用提示 |
| 环境变量 | 约 37 个 `FOLIOTHREAD_*`，只有少数已有 `FOLITH_*` 优先读取（如 `OUTPUT_DIR`、`REVIEW_OVERLAP`、`PERFORMANCE_TRACE`） | 能（高级用户、文档） | 第二阶段改 |
| 持久化格式 | `PROJECT_SCHEMA = "foliothread-project-v1"`、`MEMORY_FORMAT`、`SYSTEM_PROJECT_NAMESPACE` UUID、`"provenance": "foliothread_state"`、`foliothread_source_cleanup` | 不能 | **永远不改** |
| 旧品牌素材 | `folith/resources/brand/foliothread-*` | 不能 | 第三阶段处理 |
| 文档 / 目录名 | `docs/foliothread-agentic-native-blueprint.md`、本地目录 `FolioThread/` | 开发者 | 第三阶段，可选 |

持久化格式里的字符串是写进用户数据里的"格式标识"，不是品牌。改了旧项目就打不开；
留着对用户没有任何可见影响。

### 第一阶段：发行包改名（首个公开版前，一个 PR）

1. `pyproject.toml`：把 `name` 改成 `"folith"`。`[project.scripts]` 里的 `foliothread` 入口
   改成一个小包装函数：先往 stderr 打印一行"`foliothread` 命令将在 0.7 移除，请改用 `folith`"，
   再调用 `gui:main`。
2. `folith/desktop/paths.py:142`：依次试 `version("folith")` 和 `version("foliothread")`，
   这样本机还没重装的旧 editable 安装也能拿到版本号。
3. `scripts/release_smoke.py`：wheel 文件名、`distribution("folith")`、对 console 入口的断言都要跟着改。
4. `packaging/*.spec`、`scripts/write_desktop_build_info.py`：更新里面提到"过期 `foliothread`
   dist-info"的注释和过滤逻辑。
5. 测试：`tests/brand_regression_test.py:143-146` 现在会断言 `version("foliothread")` **存在**，
   `tests/brand_compatibility_test.py` 也锁着旧名。要有意识地改成新的边界：发行包名是
   `folith`，`foliothread` 只剩 console 别名。
6. 本机重装：
   ```bash
   venv/bin/pip uninstall -y foliothread && venv/bin/pip install -e .
   ```
7. 更新 `docs/brand.md` 第 6 节（兼容边界）。

**验收**：`pip install folith` 后，`folith` 和 `foliothread` 两个命令都能启动；
`.app` 里的版本号正确；`release_smoke.py` 通过。

### 第二阶段：环境变量（可以和第一阶段同版本，也可以下一版）

1. 新增一个读环境变量的小工具，例如 `folith/env.py`：`get(name)` 先读 `FOLITH_<name>`，
   再读 `FOLIOTHREAD_<name>`；读到旧名时，每个进程只记一次弃用日志。
2. 把 `core.py`、`folith/**` 里所有 `os.environ.get("FOLIOTHREAD_…")` 都换成它，
   包括已经手写了 `FOLITH_… or FOLIOTHREAD_…` 的那几处。
3. `folith/application/settings.py:73-80` 的环境变量列表改为列新名。
4. README 和 docs 里只写 `FOLITH_*`，旧名统一放在一张"兼容旧名"表里。
5. 测试：每个旧变量单独设置时依然生效；新旧同时设置时新名胜出。

旧名读取的成本很低，可以长期保留，到 1.0 再决定删不删。

### 第三阶段：清理（不阻塞上线）

- 旧品牌素材 `foliothread-*`：先 `git grep` 确认 UI 和 README 都不再引用，然后挪到
  `resources/brand/legacy/`，或者直接删掉（[docs/brand.md](brand.md) 说它们"作为历史保留"，删之前要确认）。
- 文档文件名：`docs/foliothread-*.md` 改名，并更新引用（`scripts/make_scenario_fixtures.py:625` 里引用了一个）。
- 本地目录 `FolioThread/` 改名的话，同时更新 `scripts/release_checks.py:40` 里的 `"/FolioThread/venv"`。
- 0.7：移除 `foliothread` console 别名。

## 7. 名字本身（顺手定下来）

- [ ] 定读音（建议 /ˈfoʊ.lɪθ/，"FOH-lith"），写进 README 和官网的 About。
- [ ] 一句话解释名字的由来，例如"folio（书页）+ lith（石，意为可靠、可存档）"，按真实意图写。
- [ ] 副标题考虑把 "Agentic Translation Workspace" 换成说差异点的说法（长文档、术语一致、可审计交付）。
