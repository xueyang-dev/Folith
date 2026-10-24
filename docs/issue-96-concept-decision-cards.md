# Issue #96 — 概念级术语决策卡片、继承与上下文例外

> Issue: xueyang-dev/Folith#96（v0.7.3 · Concept-Based Review Decisions）
> 基线 commit: `595747e`（origin/main，#92/#93/#94 已合入）
> 新模块: `folith/concept_cards.py`；状态字段: `state["concept_decisions"]`
> 依赖: #95（已合入 #112）、#93（`cfa03cf`）、#92（`db11e8e`）✓

## 1. 前置审计（实现前的真实行为）

| 位置 | 观察 |
|---|---|
| `folith/consistency.py` `findings()` (L82) | entity_conflict 按**单条 registry 记录**产出：`observed_targets` 只有本记录观察到的译法。同一概念被拆成多条记录（#95 之前很常见）时会产出**多张互相矛盾的卡**。 |
| `folith/qt/name_conflict.py` (#63) | 交互模式：每种译法一个单选项 + 「其他」+ 「保持原样」+ 逐段预览 + 确认。**卡的概念是"一个 source_form"**，不是概念。 |
| `folith/translation_decisions.py` | 决策轨迹是中性的证据记录（taxonomy），没有"偏好译名决策"的持久化与继承。 |
| `folith/engine/candidates.py` (#92) | 候选写入契约：`review_suggestion` 候选携带 finding_ids，采纳走 uid-first + baseline 门禁。写路径不由本 issue 新增。 |
| 概念身份 | 现状不存在。#95 提供了变体键（`casefold_key`/`punct_key`）与聚类。 |

## 2. 概念卡数据模型（最小数据流）

```
#95 聚类（surface 变体）      registry observations（A/B/C/D 译法 + 段号）
        │                              │
        ▼                              ▼
   concept_cards.build_concept_cards(records, pairs, decisions)
        │  一张卡 = 一个概念 = 一组 surface form + 各译法出现频次/段落/证据
        ▼
   卡片列表（resolved 卡带 inherited decision）
        │ 人工确认（复用 #63 的选项-预览-确认交互）
        ▼
   record_concept_decision → state["concept_decisions"]
        │                                   │
        ▼                                   ▼
   后续 finding 查 decision_for_form()   EntityRegistry.lock（译名继承给翻译侧）
   （同概念 → 继承，不再出矛盾卡）        （复用既有 registry 真相，不另建）
```

**概念身份（card_id 的稳定性）**：card_id = sorted(member casefold_keys) 的
sha1。**继承不依赖 card_id 精确命中**：`decision_for_keys()` 会扫描已存卡片、
按 member_keys 与探测键（casefold/punct）的交集匹配——概念后来长出新变体时，
老决策仍然生效，不会出现第二张矛盾卡。

## 3. 决策持久化与例外

`state["concept_decisions"]`：

```json
{
  "version": 1,
  "disabled": false,
  "cards": {
    "<card_id>": {
      "member_keys": ["kafka", "kafka’s project"],
      "preferred_target": "卡夫卡",
      "keep_variants": false,
      "exceptions": [{"scope": "segment:12", "target": "卡夫卡的项目", "note": "书名回指"}],
      "decided_at": "...", "note": "..."
    }
  },
  "log": [{"at": "...", "card_id": "...", "action": "decide|exception|keep", ...}]
}
```

- **统一决策**（`preferred_target`）：文档级；`decision_for_form(form, segment_id,
  section_id)` 命中 `segment:<id>` / `section:<id>` 例外时返回例外译名。
- **保持原样**（`keep_variants: true`）：文学文字游戏、多义、各自符合语境——
  该概念不再产出 entity_conflict，映射关系保留。
- **拆分/独立实体**：复用 #95 的 `manual_splits` 语义（`split_concept_card`
  包装），拆分后两张卡各自映射，决策不串。
- **决策日志**：每次人工动作记入 `log`（动作、时间、卡片、译名），供验收
  度量"每 100 个发现的人工决策/点击数"。

## 4. 继承与 finding 集成

`consistency.findings(records, pairs, decisions=None)`（可选参数，向后兼容）：

- 记录所属概念已有文档级决策 → finding 附 `concept_card_id` 与
  `inherited_preferred`（UI 显示"沿用已决定译名"而不是新矛盾卡）；
- 变体集合 ⊆ 决策译名 ∪ 已接受变体 → **不再产出** finding（无矛盾则无卡）；
- `keep_variants` 概念 → 不产出 finding。

`record_concept_decision` 同时把译名写回 registry（`EntityRegistry.lock`），
翻译/审校注入侧复用既有 hint 路径，不建立平行真相。

## 5. 防护栏（沿 #95）

- 建议永远 suggestion-only；唯一写路径是显式人工决策。
- 零默认模型调用；不新增 per-batch LLM 调用。
- `disabled=true` 回滚：卡片与 finding 继承关闭，已存决策与 registry 证据保留。
- 字符串相似度永不建立概念身份；只复用 #95 封闭规则表的键与簇。

## 6. 验收对应

- 「一个核心决策处理同一概念变体、重启后持久、被后续发现继承」→
  `record_concept_decision` + state roundtrip + `decision_for_keys` 交集继承。
- 「不同概念与上下文例外不错误继承；拆分/合并后映射一致」→ 例外 scope 匹配 +
  split 后 `decision_for_form` 互不串。
- 「记录每 100 个发现的人工决策/点击数与误合并率」→ `log` 字段 + #95 的
  负例率测试沿用（本 PR 度量入口在数据层）。
- 「与改动前基线对比」→ 基线 = 多记录同名概念产出 N 张矛盾卡；测试固定此
  对照（旧行为 N 卡，新行为 1 卡 + 继承）。
