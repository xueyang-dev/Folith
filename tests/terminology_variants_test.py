"""Issue #95：术语变体归一化与候选聚类防误合并。

失败用例先行（issue 验收要求的 failure-case tests first）。

运行方式（项目根目录）：python tests/terminology_variants_test.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import state_migration
from folith import terminology_variants as tv
from folith.entity_registry import EntityRegistry


# ---------------- 归一化键（保留原 surface form，不回写） ----------------

def test_casefold_key_normalizes_case_and_whitespace():
    assert tv.casefold_key("  World Health  Organization ") == "world health organization"
    assert tv.casefold_key("KAFKA") == tv.casefold_key("kafka")
    # NFKC：全角折叠为半角
    assert tv.casefold_key("ＷＨＯ") == "who"
    # 原始字符串不被修改（纯函数）
    original = "  KAFKA  "
    tv.casefold_key(original)
    assert original == "  KAFKA  "


def test_punct_key_folds_dash_and_quote_families():
    # 破折号族互相归一
    assert tv.punct_key("co‑operation") == tv.punct_key("co-operation")
    assert tv.punct_key("co—operation") == tv.punct_key("co-operation")
    # 引号族互相归一
    assert tv.punct_key("“Kafka”") == tv.punct_key('"Kafka"')
    assert tv.punct_key("‘quote’") == tv.punct_key("'quote'")
    # punct_key 必然蕴含 casefold_key 的归一（大小写/空白同样被折叠）
    assert tv.punct_key("KAFKA") == tv.punct_key("kafka")


def test_hyphen_elision_is_not_punctuation_tier():
    # "e-mail" vs "email"：连字符省略比标点变体更激进，必须落入建议层，
    # 不得与 exact/punctuation 层混同（"re-cover" vs "recover" 是不同概念）。
    assert tv.punct_key("e-mail") != tv.punct_key("email")


# ---------------- 候选聚类（永远是建议，不写回） ----------------

def _members(*forms):
    return [{"form": form, "origin": "glossary"} for form in forms]


def test_exact_tier_groups_case_and_space_variants():
    clusters = tv.build_candidate_clusters(
        _members("World Health Organization", "world health  organization"), {})
    assert len(clusters) == 1
    assert clusters[0]["tier"] == "exact"
    assert clusters[0]["confidence"] == "high"
    # 原始 surface form 逐字保留，证据可追溯
    forms = {m["form"] for m in clusters[0]["members"]}
    assert forms == {"World Health Organization", "world health  organization"}


def test_punctuation_tier_groups_dash_and_quote_variants():
    clusters = tv.build_candidate_clusters(_members("co‑operation", "co-operation"), {})
    assert len(clusters) == 1
    assert clusters[0]["tier"] == "punctuation"
    assert clusters[0]["confidence"] == "medium"


def test_morphological_tier_is_suggestion_only():
    # 复数
    clusters = tv.build_candidate_clusters(_members("algorithm", "algorithms"), {})
    assert len(clusters) == 1
    assert clusters[0]["tier"] == "morphological"
    assert clusters[0]["suggestion_only"] is True
    assert clusters[0]["confidence"] == "low"
    # 首字母缩略
    clusters = tv.build_candidate_clusters(
        _members("World Health Organization", "WHO"), {})
    assert len(clusters) == 1
    assert clusters[0]["tier"] == "morphological"
    assert clusters[0]["suggestion_only"] is True
    # 连字符省略
    clusters = tv.build_candidate_clusters(_members("e-mail", "email"), {})
    assert len(clusters) == 1
    assert clusters[0]["tier"] == "morphological"
    assert clusters[0]["suggestion_only"] is True


def test_negative_examples_never_cluster():
    # 字符串相似但概念不同：不得出现在任何簇中（相似度不建立语义同一）
    for a, b in [("Stark", "Start"), ("Kafka", "Karcag"),
                 ("adaptation", "adoption"), ("translate", "translatable")]:
        assert tv.build_candidate_clusters(_members(a, b), {}) == [], (a, b)


def test_cjk_substring_is_not_a_relationship():
    # CJK 无形态变化；子串重叠属于相似度，不得成簇
    assert tv.build_candidate_clusters(_members("术语", "术语表"), {}) == []
    assert tv.build_candidate_clusters(_members("生态系统", "系统"), {}) == []


def test_identical_forms_dedupe_no_cluster():
    # 完全相同的 surface form 只算一条证据，不足以成簇
    assert tv.build_candidate_clusters(_members("kafka", "kafka", "kafka"), {}) == []
    assert tv.build_candidate_clusters([{"form": "kafka", "origin": "glossary"}], {}) == []


def test_same_record_members_never_cluster():
    # 同一 registry 记录内部（source + alias）的形式差异不是"重复决策卡"
    members = [
        {"form": "Kafka", "record_id": "entity-0001", "origin": "entity_registry"},
        {"form": "KAFKA", "record_id": "entity-0001", "origin": "entity_registry"},
        {"form": "Kafka’s Project", "record_id": "entity-0001",
         "origin": "entity_registry"},
    ]
    assert tv.build_candidate_clusters(members, {}) == []


# ---------------- 人工决策（merge / split / 回滚开关） ----------------

def test_normalize_variant_decisions_roundtrip():
    raw = {
        "disabled": False,
        "manual_merges": [["Stark", "starks"]],
        "manual_splits": [["World Health Organization", "WHO"]],
    }
    decisions = tv.normalize_variant_decisions(raw)
    assert decisions["version"] == 1
    assert decisions["disabled"] is False
    # 决策键按归一化键存储，并对内排序保证确定性
    assert decisions["manual_merges"] == [["stark", "starks"]]
    assert decisions["manual_splits"] == [["who", "world health organization"]]
    # JSON roundtrip 后语义不变
    again = tv.normalize_variant_decisions(json.loads(json.dumps(decisions)))
    assert again == decisions
    # 垃圾输入 -> 安全默认
    safe = tv.normalize_variant_decisions("garbage")
    assert safe["manual_merges"] == [] and safe["manual_splits"] == []
    assert safe["disabled"] is False


def test_manual_merge_forces_cluster_with_manual_provenance():
    decisions = tv.normalize_variant_decisions({"manual_merges": [["Stark", "Starks"]]})
    clusters = tv.build_candidate_clusters(_members("Stark", "Starks"), decisions)
    assert len(clusters) == 1
    assert clusters[0]["decision"] == "manual"
    assert clusters[0]["suggestion_only"] is False


def test_manual_split_persists_and_suppresses_suggestions():
    decisions = tv.normalize_variant_decisions(
        {"manual_splits": [["World Health Organization", "WHO"]]})
    members = _members("World Health Organization", "WHO", "world health organization")
    clusters = tv.build_candidate_clusters(members, decisions)
    # WHO 不再与全称成簇；exact 层的两个全称变体仍然成簇
    assert len(clusters) == 1
    forms = {m["form"] for m in clusters[0]["members"]}
    assert forms == {"World Health Organization", "world health organization"}


def test_disabled_switch_restores_original_form_presentation():
    decisions = tv.normalize_variant_decisions({
        "disabled": True,
        "manual_merges": [["a", "b"]],
        "manual_splits": [["c", "d"]],
    })
    # 关闭聚类：返回空，但 decisions 本身不被清空（回滚不删证据/决策）
    assert tv.build_candidate_clusters(_members("kafka", "KAFKA"), decisions) == []
    assert decisions["manual_merges"] == [["a", "b"]]
    assert decisions["manual_splits"] == [["c", "d"]]


# ---------------- Entity Registry 集成（证据留原地） ----------------

def _registry():
    return EntityRegistry([
        {"source_form": "World Health Organization", "preferred_target": "世界卫生组织",
         "occurrences": [0, 3], "provenance": "reviewed"},
        {"source_form": "WHO", "preferred_target": "WHO", "occurrences": [7]},
        {"source_form": "Kafka", "preferred_target": "卡夫卡", "occurrences": [1, 2, 5]},
        {"source_form": "Kafka’s Project", "preferred_target": "卡夫卡的项目",
         "occurrences": [9]},
    ])


def test_registry_candidate_clusters_carry_traceable_evidence():
    clusters = _registry().candidate_variant_clusters()
    by_forms = {frozenset(m["form"] for m in c["members"]): c for c in clusters}
    who = by_forms[frozenset({"World Health Organization", "WHO"})]
    assert who["tier"] == "morphological"
    member = next(m for m in who["members"] if m["form"] == "World Health Organization")
    # 证据可追溯：record id + occurrences 来自既有 registry 字段
    assert member["record_id"]
    assert member["occurrences"] == [0, 3]


def test_registry_merge_variants_fuses_records_and_preserves_evidence():
    registry = _registry()
    decision = registry.merge_variants(["Kafka", "Kafka’s Project"], note="同一概念")
    # 决策可持久化（casefold 键）
    assert decision["merge"] == ["kafka", "kafka’s project"]
    # 记录融合：高 provenance 的记录吸收另一条，证据不丢
    kafka = next(r for r in registry.records if "kafka" in
                 {f.casefold() for f in [r["source_form"]] + r.get("aliases", [])})
    assert "Kafka’s Project" in kafka["aliases"] or \
        "Kafka’s Project".casefold() == kafka["source_form"].casefold()
    occ = set(kafka["occurrences"])
    assert {1, 2, 5, 9} <= occ
    merged_records = [r for r in registry.records
                      if r["source_form"].casefold() == "kafka’s project"]
    assert merged_records == []  # 已并入，不再出现重复决策卡


def test_registry_split_decision_persists_and_suppresses():
    registry = _registry()
    decision = registry.split_variants("Kafka", "Kafka’s Project", note="不同概念")
    assert decision["split"] == ["kafka", "kafka’s project"]
    clusters = registry.candidate_variant_clusters(
        tv.normalize_variant_decisions({"manual_splits": [decision["split"]]}))
    kafka_cluster = [c for c in clusters
                     if {"Kafka", "Kafka’s Project"} <=
                     {m["form"] for m in c["members"]}]
    assert kafka_cluster == []


# ---------------- 状态持久化（state migration 契约） ----------------

def test_state_migration_defaults_and_normalizes_variant_decisions():
    defaults = state_migration.migrate_state({})
    assert defaults["terminology_variant_decisions"] == \
        tv.normalize_variant_decisions(None)
    migrated = state_migration.migrate_state({
        "terminology_variant_decisions": {
            "manual_merges": [["A", "b"]], "manual_splits": "garbage",
        },
    })
    decisions = migrated["terminology_variant_decisions"]
    assert decisions["manual_merges"] == [["a", "b"]]
    assert decisions["manual_splits"] == []


# ---------------- 度量：耗时与误合并率（零模型调用） ----------------

def test_local_processing_time_budget():
    import random
    random.seed(95)
    words = ["kafka", "alpha", "gene", "who", "stark", "co-operation", "email"]
    members = []
    for i in range(10000):
        w = random.choice(words)
        form = w.upper() if i % 3 == 0 else (w + ("s" if i % 7 == 0 else ""))
        members.append({"form": form, "origin": "fixture"})
    start = time.perf_counter()
    clusters = tv.build_candidate_clusters(members, {})
    elapsed = time.perf_counter() - start
    assert clusters, "正常输入必须能产出候选簇"
    assert elapsed < 2.0, f"1 万 forms 聚类耗时 {elapsed:.3f}s，超出本地计算预算"
    print(f"  · 10000 forms 聚类耗时 {elapsed * 1000:.1f} ms，产出 {len(clusters)} 簇")


def test_false_merge_rate_zero_on_labeled_negatives():
    # 类别一：任何形式的簇都不允许出现（相似/拼写接近但概念无关）
    hard_negatives = [
        ("Stark", "Start"), ("Kafka", "Karcag"), ("adaptation", "adoption"),
        ("术语", "术语表"), ("system", "sytem"), ("translate", "translatable"),
        ("effect", "affect"),
    ]
    hits = sum(
        1 for a, b in hard_negatives
        if tv.build_candidate_clusters(_members(a, b), {}))
    rate = hits / len(hard_negatives)
    assert rate == 0.0, f"标注负例误合并率 {rate:.0%}，必须为 0"
    print(f"  · 硬负例误合并率 {rate:.0%}（{len(hard_negatives)} 例）")

    # 类别二：连字符省略是激进规则，只允许 suggestion-only 低置信建议，
    # 绝不自动合并（不产生 high/medium 簇，不带 manual 决策）。
    for a, b in [("recover", "re-cover"), ("email", "e-mail")]:
        clusters = tv.build_candidate_clusters(_members(a, b), {})
        assert len(clusters) == 1, (a, b)
        assert clusters[0]["tier"] == "morphological"
        assert clusters[0]["suggestion_only"] is True
        assert clusters[0]["confidence"] == "low"
        assert clusters[0]["decision"] is None
    print("  · 连字符省略仅产出 suggestion-only 建议（不自动合并）")


if __name__ == "__main__":
    test_casefold_key_normalizes_case_and_whitespace()
    test_punct_key_folds_dash_and_quote_families()
    test_hyphen_elision_is_not_punctuation_tier()
    test_exact_tier_groups_case_and_space_variants()
    test_punctuation_tier_groups_dash_and_quote_variants()
    test_morphological_tier_is_suggestion_only()
    test_negative_examples_never_cluster()
    test_cjk_substring_is_not_a_relationship()
    test_same_record_members_never_cluster()
    test_identical_forms_dedupe_no_cluster()
    test_normalize_variant_decisions_roundtrip()
    test_manual_merge_forces_cluster_with_manual_provenance()
    test_manual_split_persists_and_suppresses_suggestions()
    test_disabled_switch_restores_original_form_presentation()
    test_registry_candidate_clusters_carry_traceable_evidence()
    test_registry_merge_variants_fuses_records_and_preserves_evidence()
    test_registry_split_decision_persists_and_suppresses()
    test_state_migration_defaults_and_normalizes_variant_decisions()
    test_local_processing_time_budget()
    test_false_merge_rate_zero_on_labeled_negatives()
    print("\n全部通过 ✅")
