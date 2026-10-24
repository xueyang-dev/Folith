# Issue #95 — 术语变体归一化与候选聚类防误合并

> Issue: xueyang-dev/Folith#95（v0.7.3 · Concept-Based Review Decisions）
> 审计基线 commit: `2040b745118b3e5e0ec9c5f62bd72c2fa7c7e26d`（origin/main, 2026-10-24）
> 状态字段: `state["terminology_variant_decisions"]`；实现模块: `folith/terminology_variants.py`

本文档是 issue 要求的「先记录最小数据流与防误合并护栏」，按 issue 原文顺序组织。

## 1. 前置审计（改动前的真实行为）

记录于实现之前，逐条核对过代码：

| 位置 | 观察 |
|---|---|
| `folith/entity_registry.py` `normalize_registry()` (L122) | 记录合并只按 `_key(source_form)`（空白折叠 + casefold）**精确相等**；不同 key 一律各自成记录。没有 Unicode/标点/连字符归一化。 |
| `folith/entity_registry.py` `EntityRegistry._find()` (L149) | 查找时 aliases 参与匹配，但没有任何机制把 "Kafka—Project" / "Kafka's" / 全角变体归并到同一条记录。 |
| `folith/entity_registry.py` `consistency_findings()` (L325) | 只报告**单条记录内**多个译法的冲突；跨记录的变体（同名概念被拆成多条记录）完全不可见。 |
| `folith/terminology.py` `extract_auto_terms_v2()` (L491 起) | 自动术语候选按 `src.casefold()` 精确合并；同样没有变体层。 |
| `folith/terminology.py` `term_matches()` / `CompiledGlossary` | 匹配是精确 term + 词边界（CJK 子串），大小写不敏感，但 no 归一化键。 |
| LLM 调用 | 上述两条路径均为本地纯计算，**现状零模型调用**。本 issue 保持这一点。 |

**结论：当前不存在任何候选聚类。** 本 issue 新增的是「建议层」（suggestion-only），
不改变上述任何既有写入语义；`normalize_registry` 的精确 casefold 合并行为保持不变。

## 2. 最小数据流

```
证据源（只读）                    本地纯计算（零模型调用）                 持久化决策（人工）
┌──────────────────────┐   ┌──────────────────────────────────┐   ┌─────────────────────────┐
│ entity_registry 记录  │   │ terminology_variants             │   │ state[                  │
│  source_form/aliases/│──▶│  .build_candidate_clusters(      │──▶│  "terminology_variant_  │
│  occurrences         │   │     members, decisions)          │   │   decisions"]           │
│ glossary sources     │   │  → 候选簇（confidence 分层）      │   │  manual_merges /        │
│ （现状即有，不复写）  │   │  → 证据可追溯到原 surface form    │   │  manual_splits /        │
└──────────────────────┘   └──────────────────────────────────┘   │  disabled               │
                                                                  └─────────────────────────┘
```

- **只读复用既有真相**：聚类输入来自 entity registry（含 aliases）与 glossary 的既有
  字段，不复制、不另存一份"术语真相"。
- **候选簇永远是建议**：任何 confidence 层级都不会自动写入 registry/glossary；
  唯一写路径是显式的人工决策 API（merge/split）。
- **决策持久化在独立 decisions 字段**：`manual_merges`（人工断言同一概念）、
  `manual_splits`（人工断言不同概念，永久抑制再次建议）、`disabled`（回滚开关）。
  证据始终留在 entity_registry 原地；decisions 只引用归一化键。
- **归一化键保留原形**：成员携带原始 surface form 与 `occurrences`/record id，
  关闭聚类（`disabled=true`）后按原形展示，不丢任何证据或人工拆分决策。

## 3. 归一化与分层（防误合并护栏）

| 层级 | 归一化内容 | confidence | 是否自动成簇 |
|---|---|---|---|
| `exact` | NFKC + casefold + 空白折叠 | high | 是（候选簇） |
| `punctuation` | 破折号族（- ‐ – — ―）/引号族（' ' " "）/边缘标点归一 | medium | 是（候选簇） |
| `morphological` | 规则形态学：复数（-s/-es/-ies）、-ed/-ing、首字母缩略（initialism）、连字符省略 | low, `suggestion_only=true` | 是，但标记为建议 |
| （不存在） | 字符串相似度 | — | **永不**成簇 |

护栏（逐条对应 issue 验收）：

1. **相似度永不建立语义同一**：模块内不出现 edit-distance / difflib；唯一允许的
   模糊关系来自封闭的形态规则表。CJK 形态学规则不适用（CJK 无词形变化），
   CJK 之间只走 exact/punctuation 两层。
2. **负例不自动合并**：相似但不同概念的字符串（如 "Stark" vs "Start"、
   "recover" vs "re-cover"——后者属连字符省略建议层，仍需人工确认）不产生
   high/medium 簇；morphological 簇带 `suggestion_only` 且人工 split 后永久抑制。
3. **手动拆分持久化**：`manual_splits` 记录归一化键对，重建聚类时无条件遵守。
4. **可回滚**：`disabled=true` 时 `build_candidate_clusters` 返回空、UI/调用方
   按原形展示；decisions 与 registry 证据原样保留。
5. **零默认模型调用**：模块只 import `re`/`unicodedata`/标准库；无任何网络或
   LLM 依赖，也不新增 per-batch LLM 调用。

## 4. 验收度量

- `tests/terminology_variants_test.py`：失败用例先行，覆盖归一化分组、
  负例、CJK 例外、manual split/merge 持久化与回滚开关。
- 处理耗时：测试内置 1 万 surface forms 的构建计时（本地纯计算，毫秒级预算），
  作为与改动前基线（该路径不存在，基线成本为 0）的对比记录。
- 误合并率：以测试内固定标注负例集统计（建议层建议数中命中负例的比例须为 0，
  因为负例按设计不进入任何建议簇）；后续 #96 决策卡上线后用人工决策日志
  （每 100 个发现的点击数）继续度量。
