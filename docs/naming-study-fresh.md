# 命名研究（全新一轮，2026-10-16）

> 本轮只依据三类材料：运行界面截图（`docs/ui-audit/39-*`、`40-design-review-2026-10-16/`、`54-reading-mode-mockup/` 等）、功能代码与界面文案（`folith/qt/`、`folith/resources/locales/qt-zh-CN.json`）、`docs/qt-beta/release-notes.md`。没有读任何品牌文档，也没有用现在的占位名做文字游戏。
>
> **这不是商标检索。** 下面的可用性检查只覆盖 PyPI、GitHub 用户名、`.app`/`.com` 域名注册状态和网页搜索里同类软件的撞名情况。正式定名前，需要在中国商标网（第 9 类软件、第 42 类 SaaS）和 USPTO/EUIPO 做检索，`.cn` 域名和 App Store 名称也要再查一遍。

---

## 1. 这个产品是什么

**它是什么。** 一个跑在 Mac 上的长文档翻译工作台。导入 PDF、Word、EPUB、TXT 或 Markdown（扫描件由视觉模型读成正文），然后按四步走：**准备 → 翻译 → 审校 → 交付**。界面是逐段的原文/译文对照表，右侧是这一段的操作：审校、点评、重译、提问、加入术语库、留批注。还有通读模式，可以像看书一样读双语对照，左边的色条标出哪些段落待审校、哪些人工改过。

**给谁用。** 要对译文负责的人：职业译者、翻译硕士（有“实践报告 / 案例 / 终稿 QA”的学术模式）、要把整本书或整份手册译出来交给别人的编辑和研究者。他们自带模型 API Key；任务、术语、翻译记忆都“保存在这台 Mac 上”。

**和普通 AI 翻译有什么不同。** 欢迎页的一句话最能说明它：*“AI 先译、先审，你来决定什么可以交付。”* 具体体现在：

- **人来拍板，并且有记录。** 每段都有“待审校 / 已审校 / 人工修改 / 需重审”状态；改了原文、合并或拆分段落、统一译名之后，受影响的段落会自动退回待审校。
- **交付即定稿。** “冻结交付”会生成一个不可修改的版本快照（v1、v2……），交付门禁不通过就不能冻结；之后工作版本再改动，界面会提示“已偏离冻结交付”。
- **长文一致性。** 术语治理（严格模式下要先冻结术语表才能开始翻译）、按项目隔离的翻译记忆、文档画像。
- **原页对照。** 能在 PDF 原页里定位并高亮某一段，扫描件可以回看原页核对。
- **事先说清成本。** 快速、标准、精细三种模式开始前就显示大约多少次调用、多少 token、要多久。

一句话：普通 AI 翻译交给你一段**结果**；这个产品交给你一个**可以签字交出去的版本**。

---

## 2. 命名方向

| # | 方向 | 为什么贴合 |
|---|------|-----------|
| A | **校样与付印**（出版行业的行话） | 产品就是一条从 AI 初稿到人工审定、再到冻结交付的出版流程，“校样”“定稿”这类词恰好对应它最核心的那一步。 |
| B | **放行与拍板**（人来决定） | 最大的差异点是“你来决定什么可以交付”，名字可以直接说这个动作。 |
| C | **渡**（翻译就是把整本书运到对岸） | 产品面对的是整份长文档，而不是一句话；“渡”说的是完整、可靠地把一份东西运过去。 |
| D | **字斟句酌**（逐段、一致） | 逐段工作、术语统一、反复重审，体现的是一种慢而准的工作态度。 |
| E | **年轻口语化** | 用年轻创作者熟悉的口语（过稿、小样）降低专业工具的距离感，代价是专业用户的信任感会弱一些（见各条风险）。 |

---

## 3. 候选与检查

### 检查方法

- PyPI：`https://pypi.org/pypi/<名>/json`，404 = 可用。
- GitHub 用户/组织名：用已登录的 `gh api users/<名>` 查询（匿名接口很快就被限流了）。“占用”= 已有账号。
- 域名：`https://rdap.org/domain/<名>.app` 和 `.com`，404 = 未注册；溢价域名和保留域名没有核实。
- 撞名：在网页搜索同类软件（翻译、写作、阅读、校对工具）。
- 单个英文常用词的四项几乎全被占用，所以另外查了可以实际使用的变体（如 `xxxapp`、`xxxhq`），列在“变体”一栏。
- 检查日期 2026-10-16。✅ = 可用，❌ = 已占用。

### 候选总表

| # | 名称 | 方向 | 风格 | 含义 / 为什么合适 | 主要风险 |
|---|------|------|------|------------------|---------|
| 1 | **小样** | A+E | 年轻×专业 | 出版业“小样”就是付印前的校样，正好是“AI 先出稿、你来改”的那一份；口语里“小样儿”又俏皮亲切。一个词同时有行业感和年轻感。 | **“小样”在电商里最常见的意思是化妆品试用装**，搜索被淹没；“小样儿”带轻微调侃，对律所、出版社这类客户显得不够正式。 |
| 2 | **Galley** | A | 稳重 | 英文 galley proof = 校样，和“小样”同义，适合做小样的英文名。 | 已有一个开源 Mac Markdown 阅读器叫 Galley（Product Hunt 上有），同属“阅读/文本”类，中等撞名；还有一家餐饮软件公司 Galley Solutions。 |
| 3 | **过稿** | B+E | 年轻 | 创作者圈子说的“过稿”就是稿件通过审核；产品的核心正是“你决定哪一稿能过”。短、口语，传播性强。 | 是行话里的俚语，年长的专业译者和机构采购可能觉得轻飘；名字容易被理解成“投稿平台”。没找到同名软件。 |
| 4 | **Passage** | B+C | 稳重 | 双关：一段文字（passage）+ 通过/渡过（passage）。正好对应“逐段”和“放行”两层意思。 | Mac 上已有“Passage App”（多人共读）和“Passages for Readers”（摘录日记），同属阅读类，中等撞名；单词太常见，很难搜到。 |
| 5 | **译渡** | C | 稳重 | “渡”是把整本书完整运到对岸；词是生造的，好搜。 | 拼音 yidu 撞上医疗 AI 公司“医渡科技 Yidu Tech”（港股），英文环境容易混淆；“渡”略带宗教色彩（普渡）。 |
| 6 | **Ferry** | C | 稳重偏亲切 | 摆渡，跟“译渡”意象一致；读音轻快，比 Passage 年轻。 | 常用词，搜索难度大；没找到翻译类同名软件（只有票务、支付类 App）。 |
| 7 | **译定** | B | 稳重，带一点俏皮 | 字面是“译文已定”，对应冻结交付；读音同“一定”，天然带“可靠、有把握”的意思，口号也好写（“译定，再交”）。生造词，好搜、可注册。 | 和已有 App“译准翻译官”结构相近（译准 / 译定），有轻度混淆；本身画面感不强，logo 要靠“印章 / 定”字来撑。 |
| 8 | **校本** | A | 稳重 | “校过的本子”，学术出版的感觉，适合翻译硕士和学术用户。 | **大陆教育界“校本”通常指“学校本位”（校本课程）**，会被误读成 xiào běn；拼音 jiaoben 又等于“脚本”。不推荐。 |
| 9 | **定稿** | A+B | 稳重 | 最直白地说出“最后一稿由你定”。 | 通用词，无法注册商标，搜索毫无区分度；只适合做功能名，不适合做产品名。 |
| 10 | **推敲** | D | 稳重、文气 | 贾岛“推敲”的典故，逐字斟酌，文化认同度高。 | 已有 iOS App 叫“推敲”，PyPI 上 `tuiqiao` 是一个中文拼写纠错工具，同类撞名，**高风险**。 |
| 11 | **信译** | D | 稳重 | 取“信达雅”的“信”，忠实可信。 | 拼音 xinyi 和“信义”“心怡”这类常用词、人名同音，`.app`/`.com` 都已被注册；没找到同名翻译软件。 |
| 12 | **段段** | D+E | 年轻 | 逐段工作，叠字可爱。 | 有“断断续续”的联想，显得不稳定；`.app` 已被注册；专业可信度低。 |
| 13 | **对对** | D+E | 年轻 | “对照”加上“对的”，叠字亲切。 | 已有校对类 App“对对错别字”，**同类撞名**；不推荐。 |
| 14 | **句读** | D | 文气 | 古籍断句，体现逐段处理。 | 已有阅读 App“句读 — 发现文字之美”（App Store），**同类撞名**；不推荐。 |
| 15 | **译印** | A | 稳重 | “印”既是付印，也是盖章确认，对应冻结交付。 | 读音 yìyìn 拗口，口头传播差；没找到同名软件。 |
| 16 | **慢译** | D | 文艺 | 和“秒翻”反着来，强调认真。 | 会被理解成“慢”，和产品已经做的效率工作（预估、快速模式）冲突；不推荐。 |
| 17 | **Stet** | B | 专业 | 校对符号“保留原样”，人来定夺。 | 已有一个开源、本地优先的 AI 文档编辑器叫 Stet，理念几乎一样，还有一款 Mac 听写 App 叫 Stet，**高风险**。 |
| 18 | **Signoff** | B | 专业 | 签字确认，对应“冻结交付”。 | 和电子签名、审批类 SaaS（SignOff、ApproveForMe）撞名，容易被当成签名工具。 |
| 19 | **Colophon** | A | 专业 | 书末版本记录，对应不可变快照。 | Obsidian 上有写作插件 Colophon Writer，还有写作平台 Colofon，中等撞名；中文用户难读难记。 |
| 20 | **Concord** | D | 稳重 | 一致、和谐，又让人联想到语料索引工具 concordance。 | 语料学已有同名工具 Concord，还有合同管理 SaaS 也叫 Concord；四项全部被占。 |
| 21 | **Imprimatur** | A | 很正式 | 拉丁文“准予付印”。 | 已有一家语言服务公司叫 Imprimatur，**同行撞名**；太长、太生僻。 |
| 22 | **Nib** | E | 年轻 | 笔尖，短而可爱。 | App Store 上已有“Nib — AI Notes & Translation”，还有 Mac 本地化工具 nibTranslate，**同类撞名**。 |
| 23 | **Marginalia** | D | 文艺 | 页边批注，对应逐段批注和审校。 | 至少 4 款阅读/批注 App 同名，**高风险**。 |
| 24 | **FairCopy** | A | 专业 | 誊清稿 = 改定后的干净版本。 | 已有数字人文领域的桌面编辑器 FairCopy，**同类撞名**。 |
| 25 | **Rendition** | C | 稳重 | rendition = 译本、演绎。 | 英文里会让人联想到“非常规引渡”（extraordinary rendition），负面；四项全部被占。 |

### 可用性检查结果

| 名称 | 检查用名 | PyPI | GitHub | .app | .com | 可用变体（PyPI / GitHub / .app 都可用） |
|---|---|---|---|---|---|---|
| 小样 | xiaoyang | ✅ | ❌ | ❌ | ❌ | `xiaoyangapp`（.com ❌） |
| Galley | galley | ❌ | ❌ | ❌ | ❌ | `galleyhq`、`galleypress`（.com 均 ❌） |
| 过稿 | guogao | ✅ | ❌ | ✅ | ❌ | `guogaoapp`（四项全空） |
| Passage | passage | ❌ | ❌ | ❌ | ❌ | `passageapp`（.com ❌）；`getpassage`、`usepassage` 的 .app 已占 |
| 译渡 | yidu | ✅ | ❌ | ✅ | ❌ | `yiduapp`（四项全空） |
| Ferry | ferry | ❌ | ❌ | ❌ | ❌ | `ferryline`（.com ❌）；`ferryapp`（GitHub ❌） |
| 译定 | yiding | ✅ | ❌ | ✅ | ❌ | `yidingapp`（四项全空） |
| 校本 | jiaoben | ✅ | ❌ | ✅ | ❌ | `jiaobenapp`（四项全空） |
| 定稿 | dinggao | ✅ | ❌ | ✅ | ❌ | `dinggaoapp`（四项全空） |
| 推敲 | tuiqiao | ❌（中文纠错工具） | ❌ | ✅ | ❌ | `tuiqiaoapp`（四项全空） |
| 信译 | xinyi | ✅ | ❌ | ❌ | ❌ | `xinyiapp`（.com ❌） |
| 段段 | duanduan | ✅ | ❌ | ❌ | ❌ | `duanduanapp`（四项全空） |
| 对对 | duidui | ✅ | ❌ | ❌ | ❌ | `duiduiapp`（四项全空） |
| 句读 | judou | ✅ | ❌ | ✅ | ❌ | `judouapp`（.com ❌） |
| 译印 | yiyin | ✅ | ❌ | ✅ | ❌ | `yiyinapp`（四项全空） |
| 慢译 | manyi | ✅ | ❌ | ❌ | ❌ | `manyiapp`（.com ❌） |
| Stet | stet | ❌ | ❌ | ❌ | ❌ | `stetapp`、`stetdesk`（.com ❌） |
| Signoff | signoff | ✅ | ❌ | ❌ | ❌ | `signoffapp`（.com ❌） |
| Colophon | colophon | ❌ | ❌ | ❌ | ❌ | `colophonapp`（.com ❌） |
| Concord | concord | ❌ | ❌ | ❌ | ❌ | `concordant` 也只有 PyPI 可用 |
| Imprimatur | imprimatur | ❌ | ❌ | ❌ | ❌ | — |
| Nib | nib | ❌ | ❌ | ✅ | ❌ | `nibapp`（.com ❌） |
| Marginalia | marginalia | ❌ | ❌ | ❌ | ❌ | — |
| FairCopy | faircopy | ❌ | ❌ | ❌ | ❌ | `faircopyapp`（四项全空） |
| Rendition | rendition | ❌ | ❌ | ❌ | ❌ | — |

**几点观察：**

- 拼音名的 `.com` 和 GitHub 用户名几乎都被个人用户占着（拼音和人名重合），但 **`.app` 域名多数还空着**：`yiding.app`、`yidu.app`、`guogao.app` 都能注册。对一个 Mac 应用来说，`.app` 已经够用。
- 英文单词名都要加后缀才能用，而且搜索区分度差。如果主要做中文市场，英文名可以退一步，只作为 bundle 名或域名使用。
- 因同类撞名应直接淘汰的：Stet、句读、对对、推敲、Nib、Marginalia、FairCopy、Imprimatur。

---

## 4. 年轻化方向对专业可信度的影响

- **过稿、小样**的年轻感来自行业行话，而不是撒娇式的叠字，所以对专业用户的伤害相对小。在出版和创作圈，这两个词本来就是内行话。真正的风险在于：“小样”会被搜成化妆品，“过稿”会被理解成投稿平台。
- **段段、对对**这类叠字最年轻，但会直接削弱“我可以把交付交给它”的信任感。何况这个产品的卖点就是冻结、门禁和可追溯，这类名字和产品气质冲突，不建议。
- 折中做法：**正式名用稳重的，年轻感放在口号和界面语气里**。比如正式名叫“译定”，口号写“AI 先译，你说了算”。

---

## 5. 三个首选组合与推荐

### 组合一（推荐）：**译定**（中文） + **Yiding**（拼音字标，域名 `yiding.app`）

- **理由：** 最准确地说出了产品和普通 AI 翻译不同的地方：最后一步是“定”，而且由你来定。读音同“一定”，天然带“可靠”的意思，对专业用户有说服力；口号也能写得活泼（“译定，再交付”“这一版，译定了”）。词是生造的，搜索干净，`yiding.app`、PyPI `yiding` 都可用。logo 可以用印章或“定”字，正好对应界面里的“冻结交付”。
- **代价：** 和“译准翻译官”结构相近；拼音字标对海外用户不够友好。日后如果出海，再按第 6 节的方法造一个英文名，这次不必强求。

### 组合二（年轻×专业兼顾）：**小样**（中文） + **Galley**（英文，`galleyhq` / `galleypress`）

- **理由：** 中英文是同一个出版行话（校样 / galley proof），内行一看就懂，年轻用户又觉得“小样儿”亲切。这是候选里唯一同时满足“年轻化”和“行业感”的组合，最有记忆点。
- **代价：** “小样”在中文搜索里会被化妆品试用装淹没，需要长期用“小样翻译 / 小样 App”这样的组合词来做品牌搜索；Galley 有一个开源 Markdown 阅读器同名；面对出版社、律所这类机构客户时显得不够正式。

### 组合三（稳重、有意境）：**译渡**（中文） + **Lingdu**（英文造词，`lingdu.app`，见第 6 节）

- **理由：** 把“整份长文档完整运到对岸”这个画面讲得最好，logo 也好画（一条船或一道弧线）。气质稳重，又不像“定稿”那样死板。英文名不再硬套 ferry，而是参照 Trados、memoQ 这类行业工具名的造法：前半截 *ling-* 让英文用户一眼认出是语言工具，后半截 *-du* 就是“渡”。
- **代价：** “渡”略带宗教意味；不能直接用拼音 Yidu（撞上医渡科技 Yidu Tech）；Lingdu 在中文里可能被读成“领读”。
- （初稿这里配的是 Ferry，已撤下：ferry 在英文里只是渡船，“翻译即摆渡”是中文的联想，英文用户看不出它和翻译有什么关系。）

### 我的推荐

**选组合一：译定。** 理由有三：

1. 这个产品的差异点不在“翻得快”，而在“由人确认、冻结、可追溯”。三个组合里，只有“译定”把这一点放进了名字本身。
2. 它在搜索、PyPI 和 `.app` 域名上都是干净的，撞名风险最低。
3. 读音同“一定”，可以在口号和界面语气里加入年轻感，不用拿正式名去冒专业信任的风险。

如果你更想要一个第一眼就有个性、在社交媒体上更容易被记住的名字，那就选**小样 / Galley**，但要接受搜索上的长期成本。

**正式定名前还要做：** 中国商标网第 9、42 类近似检索（“译定”“译准”的近似判定尤其要看）；核实 `.cn` 域名、App Store 和 Mac App Store 的同名应用；用 5–10 位目标用户（译者、翻译硕士）做一次读音和联想测试。

---

## 6. 给“译渡”配英文名（第二轮）

第一轮把 Ferry 配给译渡，问题在于：这是随手拿了个词典里的单词，意象只在中文里成立。这一轮先看翻译行业工具名是怎么起的，再按同样的方法造词。

### 行业工具名的造法

| 名字 | 怎么来的 | 可借鉴的地方 |
|---|---|---|
| Trados | **TRA**nslation & **DO**cumentation **S**oftware 的缩合 | 用功能词的片段拼成新词，读起来像专有名词 |
| memoQ | memo（翻译记忆）+ Q | 核心概念加一个有辨识度的字母 |
| Wordfast / Smartcat / Matecat | 两个短词合成，cat 同时指 CAT（计算机辅助翻译） | 一半是行业内的暗号 |
| Transifex / Lokalise / Crowdin | 行业词根加一个改写过的后缀 | 词根让人认出品类，后缀让名字可以注册 |

共同点：**一眼看得出是语言或翻译工具，但又不是现成的单词**；两三个音节，像专有名词；可以注册。

### 造词候选

思路是“语言或翻译词根 + du（渡）”，让英文用户认出品类，中文用户听出“渡”。

| 候选 | 构成 | PyPI | GitHub | .app | .com | 撞名检查 | 结论 |
|---|---|---|---|---|---|---|---|
| **Lingdu** | *ling*(ua) 语言 + *du* 渡 | ✅ | ❌ | ✅ | ❌ | 没搜到同名产品；邻近的有 Lingui、Lingue、Lingko（都是本地化或翻译工具，拼法不同） | **首选**。读作 LING-doo，英文顺口，像一个品牌名；`lingdu.app` 可注册。中文里可能被读成“领读”，影响不大 |
| **Duvia** | *du* 渡 + *via*（经由、取道） | ✅ | ❌ | ✅ | ❌ | 没搜到同名软件 | 备选。干净好注册，但 via 和翻译的关系比较弱，像药品或出行品牌 |
| Duvers | *du* 渡 + *vers*（法语“朝向”；也接近 version，即译本） | ✅ | ❌ | ✅ | ❌ | 和 AI 配音、本地化平台 **Dubverse** 只差一个字母 | 不推荐 |
| Tradu / Traduo | 罗曼语“翻译”词根 *tradu-*，结尾正好是 du | ✅ | ❌ / ✅ | ✅ | ❌ | 已有“Tradu — Document Translator”、iOS“Traduo”翻译 App，还有 Tradugo、TradooIT；这个词根已经很拥挤 | 淘汰 |
| Transdo | *trans* + *do*（渡） | ✅ | ✅ | ✅ | ❌ | 已有 iOS 翻译 App“TransDO” | 淘汰 |
| Duverse | *du* + *verse* | ✅ | ❌ | ✅ | ❌ | 和 **Dubverse** 几乎同形 | 淘汰 |
| Duling | *du* + *ling*(ua) | ✅ | ❌ | ✅ | ❌ | 读音接近 Duolingo | 淘汰 |
| Yidu | 拼音 | ✅ | ❌ | ✅ | ❌ | **医渡科技 Yidu Tech**（港股 2158，AI 软件，有 YiduCore 品牌），同在软件类 | 淘汰 |
| Yido | 拼音变体 | — | — | — | — | 英文里接近对犹太人的蔑称 “Yid / Yiddo” | 淘汰 |
| Duport | *du* + *port*（渡口 = 港口） | ✅ | ❌ | ✅ | ❌ | 和 DuPont（杜邦）只差一个字母 | 淘汰 |
| Vadu | 拉丁语 *vadum*（浅滩、渡口）；罗马尼亚语 vad = 渡口 | ✅ | ❌ | ❌ | ❌ | 罗马尼亚地名，太生僻 | 淘汰 |

（检查日期 2026-10-16，方法同第 3 节；同样不是商标检索。）

### 结论

**译渡 / Lingdu** 是这条路线上最站得住的组合：中文名表达意象，英文名表达品类，两者通过“渡 / du”这个音连起来，造法和 Trados、memoQ 一致。

同样的批评也适用于第一轮的 **Galley**、**Passage**：它们也是直接拿来的英文单词。如果要用组合二（小样），英文名也应该按本节的方法重新造一个。

---

## 附：撞名检查用到的来源

- Stet（本地优先 AI 文档编辑器 / Mac 听写 App）：[Wikipedia: Stet (software)](https://en.wikipedia.org/wiki/Stet_(software))、[Product Hunt: Stet](https://www.producthunt.com/products/stet-a-smart-dictation-for-rest-of-us)、[github.com/filu123/stet](https://github.com/filu123/stet)
- 句读：[App Store「句读 — 发现文字之美」](https://apps.apple.com/cn/app/id1073431872)
- 推敲：[App Store「推敲」](https://apps.apple.com/py/app/%E6%8E%A8%E6%95%B2/id1628306308)；PyPI `tuiqiao`（中文拼写纠错工具）
- 对对：[App Store「对对错别字」](https://apps.apple.com/cn/app/id6741154739)
- Nib：[Nib — AI Notes & Translation](https://mwm.ai/apps/nib-ai-notes-translation/6787264555)、[nibTranslate](https://www.iwascoding.com/nibTranslate)
- Marginalia：[Marginalia: Annotate Organize](https://apps.apple.com/app/id6758641325)、[Marginalist](https://apps.apple.com/ee/app/marginalist/id6753768853)
- FairCopy：[FairCopy: A Word Processor for the Digital Humanities](https://omekas.library.uvic.ca/s/dhsi2021_conf_exhibit/item/5762)
- Imprimatur / Imprima：[Imprima Smart Translation](https://www.imprima.com/ai-due-diligence/smart-translation)
- Colophon：[Colophon Writer（Obsidian）](https://community.obsidian.md/plugins/colophon-writer)、[Colofon](https://www.producthunt.com/products/colofon)
- Galley：[Product Hunt: Galley（Markdown 阅读器）](https://www.producthunt.com/p/galley-3/galley-2)
- Passage：[Passage App](https://apps.apple.com/us/app/-/id6755797928)、[Passages for Readers](https://apps.apple.com/app/id1092871281)
- Concord：[Concord（CNR 术语索引工具）](https://iris.cnr.it/handle/20.500.14243/80757)
- Signoff：[SignOff 替代品（签名类）](https://resources.signnow.com/info-alternatives/best-digital-signature-platforms-similar-to-signoff-for-operational-documents)、[ApproveForMe](https://alternativeto.net/software/approveforme/about/)
- 译准翻译官（与“译定”相近）：[App Store](https://apps.apple.com/us/app/id6469807868)
- 小样、过稿、译渡、译定、校本、定稿、信译、段段、译印、慢译、Ferry、Rendition：在翻译、写作、阅读类软件中没有搜到同名产品（不等于不存在）。
- 第二轮（译渡英文名）：Trados 名称来源见 [JoSTrans: Long term memories: Trados and TM turn 20](https://www.jostrans.org/article/download/7241/6501?inline=1)；[Tradu — Document Translator](https://apkcombo.com/ru/tradu/com.zonait.translatenow/)、[Traduo Offline Translating App](https://appgoblin.info/apps/6762088103)、[Tradugo](https://alternativeto.net/software/tradugo/about)；[TransDO（App Store）](https://apps.apple.com/mx/app/transdo/id6746675000)；[Dubverse](https://www.getapp.ca/software/2073306/dubverse)；[医渡科技 Yidu Tech](https://www.moomooapp.com/stock/02158-HK/company)。Lingdu、Duvia 没有搜到同名产品（不等于不存在）。
