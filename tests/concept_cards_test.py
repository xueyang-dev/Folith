"""Issue #96：概念级术语决策卡片、继承与上下文例外。

失败用例先行。运行方式（项目根目录）：python tests/concept_cards_test.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import consistency, state_migration
from folith import concept_cards as cc
from folith.entity_registry import EntityRegistry


# ---------------- fixture ----------------

PAIRS = [
    {"source": "Kafka was born in Prague.", "target": "卡夫卡出生在布拉格。"},
    {"source": "Kafka's project was unfinished.", "target": "Kafka 的项目没有完成。"},
    {"source": "He is the Franz Kafka of our time.", "target": "他是我们时代的弗朗茨·卡夫卡。"},
    {"source": "Kafka studied law.", "target": "卡夫卡学习法律。"},
]


def _registry():
    registry = EntityRegistry()
    registry.observe("Kafka", "卡夫卡", segment_id=0, provenance="reviewed")
    registry.observe("Kafka’s Project", "Kafka 的项目", segment_id=1,
                     provenance="generated_observation")
    registry.observe("Franz Kafka", "弗朗茨·卡夫卡", segment_id=2,
                     provenance="generated_observation")
    registry.observe("Kafka", "卡夫卡", segment_id=3)
    return registry


def _concept_registry():
    """同一概念的三种 surface form：人工先用 #95 的 merge 断言同一概念。

    （#95 防护栏：相似/包含字符串不会自动连成概念，必须人工合并。）
    """
    registry = _registry()
    registry.merge_variants(["Kafka", "Kafka’s Project", "Franz Kafka"],
                            note="同一概念")
    return registry


# ---------------- 卡片构建：一个概念一张卡 ----------------

def test_one_card_per_concept_combines_forms_and_translations():
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    kafka_cards = [c for c in cards
                   if any("kafka" in k for k in c["member_keys"])]
    # 同一概念的 Kafka / Kafka’s Project / Franz Kafka 是一张卡（#95 聚类相连）
    assert len(kafka_cards) == 1
    card = kafka_cards[0]
    keys = " ".join(card["member_keys"])
    assert "kafka" in keys
    # 各译法作为选项，带频次与段号
    targets = {o["target"] for o in card["options"]}
    assert "卡夫卡" in targets and "Kafka 的项目" in targets
    top = max(card["options"], key=lambda o: o["count"])
    assert top["target"] == "卡夫卡" and top["count"] == 2
    assert set(top["segment_indexes"]) == {0, 3}
    # 证据可追溯：带段号与原文/译文
    row = top["evidence"][0]
    assert {"segment_index", "source", "target"} <= set(row)


def test_card_with_decision_is_resolved():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡",
                               note="统一")
    rebuilt = cc.build_concept_cards(_concept_registry().to_list(), PAIRS, state)
    resolved = next(c for c in rebuilt
                    if c["card_id"] == card["card_id"])
    assert resolved["decision"] is not None
    assert resolved["decision"]["preferred_target"] == "卡夫卡"


# ---------------- 决策持久化与继承 ----------------

def test_decision_persists_and_inherits_across_surface_variants():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡")

    # JSON roundtrip（重启）
    state = cc.normalize_concept_decisions(json.loads(json.dumps(state)))

    # 继承：任一 surface 变体（含后来出现的新形式）都解析到同一决策
    for form in ("Kafka", "Kafka’s Project", "Franz Kafka", "Franz  Kafka"):
        hit = cc.decision_for_form(state, form)
        assert hit is not None, form
        assert hit["preferred_target"] == "卡夫卡"
        assert hit["exception"] is False


def test_new_variant_maps_to_same_card_no_duplicate_cards():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡")
    # 概念后来长出新变体：探测键与 member_keys 交集命中，不产生第二张卡
    hit = cc.decision_for_keys(state, ["franz kafka", "franz  kafka"])
    assert hit is not None and hit["preferred_target"] == "卡夫卡"


# ---------------- 上下文例外 / 多义 / 文字游戏 ----------------

def test_segment_exception_overrides_document_decision():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡",
                               exceptions=[{"scope": "segment:2",
                                            "target": "弗朗茨·卡夫卡",
                                            "note": "修辞"}])
    hit = cc.decision_for_form(state, "Kafka", segment_id=2)
    assert hit["preferred_target"] == "弗朗茨·卡夫卡"
    assert hit["exception"] is True
    # 其他段落仍走文档级决策
    assert cc.decision_for_form(state, "Kafka", segment_id=0)["preferred_target"] \
        == "卡夫卡"


def test_keep_variants_decision_for_wordplay_and_polysemy():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, keep_variants=True,
                               note="双关，各译法符合语境")
    hit = cc.decision_for_form(state, "Kafka")
    assert hit is not None and hit["keep_variants"] is True
    # keep 决策不再产出 finding
    findings = consistency.findings(_registry().to_list(), PAIRS, state)
    assert findings == []


# ---------------- 拆分 / 不同实体 ----------------

def test_split_creates_independent_mappings():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡")
    # 人工拆分：Kafka’s Project 是不同概念
    cc.split_concept_card(state, "Kafka", "Kafka’s Project", note="不同实体")
    whole = cc.decision_for_form(state, "Kafka")
    project = cc.decision_for_form(state, "Kafka’s Project")
    assert whole["preferred_target"] == "卡夫卡"
    assert project is None  # 拆出去后不再继承整个概念的决策


def test_distinct_entities_with_similar_names_do_not_share_card():
    # 相似字符串、不同概念：各自有竞争译法时，各自成卡，绝不合并成一张
    records = EntityRegistry()
    records.observe("Stark", "斯塔克", segment_id=0)
    records.observe("Stark", "施塔克", segment_id=1)
    records.observe("Start", "起点", segment_id=2)
    records.observe("Start", "起始", segment_id=3)
    pairs = [
        {"source": "Stark spoke.", "target": "斯塔克说话。"},
        {"source": "Stark spoke again.", "target": "施塔克说话。"},
        {"source": "From the start.", "target": "从起点开始。"},
        {"source": "The start line.", "target": "起始线。"},
    ]
    cards = cc.build_concept_cards(records.to_list(), pairs)
    assert len(cards) == 2
    key_sets = [set(c["member_keys"]) for c in cards]
    assert key_sets[0] & key_sets[1] == set()


# ---------------- finding 集成：继承而非矛盾卡 ----------------

def test_findings_attach_inherited_decision_and_skip_resolved():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡")

    # 基线对照：无决策时，多记录同名概念产出互相独立的 finding（旧行为）
    baseline = consistency.findings(_concept_registry().to_list(), PAIRS)
    assert len(baseline) >= 1

    inherited = consistency.findings(_concept_registry().to_list(), PAIRS, state)
    # 已决定概念：仍有漂移的 finding 带 concept_card_id + inherited_preferred
    assert inherited, "漂移存在时应产出带继承信息的 finding"
    for finding in inherited:
        assert finding.get("inherited_preferred") == "卡夫卡"
        assert finding.get("concept_card_id")


def test_findings_compatible_without_decisions():
    # decisions=None 时行为与历史完全一致
    legacy = consistency.findings(_registry().to_list(), PAIRS)
    assert legacy == consistency.findings(_registry().to_list(), PAIRS, None)


# ---------------- 状态持久化 ----------------

def test_normalize_and_state_migration():
    raw = {"cards": {"x": {"member_keys": ["A", "b"], "preferred_target": "译名",
                           "exceptions": [{"scope": "segment:3", "target": "例外"}]}},
           "log": "garbage"}
    decisions = cc.normalize_concept_decisions(raw)
    assert decisions["version"] == 1
    card = decisions["cards"]["x"]
    assert card["member_keys"] == ["a", "b"]
    assert card["preferred_target"] == "译名"
    assert decisions["log"] == []
    safe = cc.normalize_concept_decisions("garbage")
    assert safe["cards"] == {} and safe["disabled"] is False

    defaults = state_migration.migrate_state({})
    assert defaults["concept_decisions"] == cc.normalize_concept_decisions(None)
    migrated = state_migration.migrate_state({"concept_decisions": raw})
    assert migrated["concept_decisions"]["cards"]["x"]["member_keys"] == ["a", "b"]


def test_disabled_switch_keeps_decisions_and_registry_evidence():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡")
    state["disabled"] = True
    assert cc.build_concept_cards(_registry().to_list(), PAIRS, state) == []
    assert cc.decision_for_form(state, "Kafka") is None
    assert state["cards"], "回滚不删决策"


def test_decision_log_records_human_actions():
    state = {}
    cards = cc.build_concept_cards(_concept_registry().to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡",
                               note="统一")
    cc.split_concept_card(state, "Kafka", "Kafka’s Project")
    actions = [(entry["action"], entry.get("card_id")) for entry in state["log"]]
    assert actions[0] == ("decide", card["card_id"])
    assert actions[1][0] == "split"


def test_registry_write_through_locks_preferred():
    registry = _concept_registry()
    state = {}
    cards = cc.build_concept_cards(registry.to_list(), PAIRS)
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(state, card=card, preferred_target="卡夫卡",
                               registry=registry)
    kafka = next(r for r in registry.records
                 if r["source_form"].casefold() == "kafka")
    assert kafka["status"] == "locked"
    assert kafka["preferred_target"] == "卡夫卡"


if __name__ == "__main__":
    test_one_card_per_concept_combines_forms_and_translations()
    test_card_with_decision_is_resolved()
    test_decision_persists_and_inherits_across_surface_variants()
    test_new_variant_maps_to_same_card_no_duplicate_cards()
    test_segment_exception_overrides_document_decision()
    test_keep_variants_decision_for_wordplay_and_polysemy()
    test_split_creates_independent_mappings()
    test_distinct_entities_with_similar_names_do_not_share_card()
    test_findings_attach_inherited_decision_and_skip_resolved()
    test_findings_compatible_without_decisions()
    test_normalize_and_state_migration()
    test_disabled_switch_keeps_decisions_and_registry_evidence()
    test_decision_log_records_human_actions()
    test_registry_write_through_locks_preferred()
    print("\n全部通过 ✅")
