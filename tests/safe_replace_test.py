"""Tests for the protected-span replacement fix in folith/consistency.py.

Covers:
- Bug cases from the defect description (CJK substring replacement)
- Suffix variant: target contains the variant as a prefix
- Prefix variant: variant contains the target as a prefix (safe already, stays safe)
- Multiple occurrences in one segment: mixed protected and unprotected
- Arabic and Latin (ASCII) forms: existing boundary behaviour unchanged
- Concept revision caller: protected forms from entity registry
- Count equals actual changes (改 N 段)
- Idempotence
- Known limit: unregistered longer word (documented as expected current behaviour)
- Entry-point integration: glossary_ops.preview_entity_repair and apply_entity_repair
- Entry-point integration: term_revision with known_forms from state
- Entry-point integration: concept_revision with collect_known_forms
"""
from __future__ import annotations

import pytest

import core
from folith import consistency
from folith import concept_revision as cr
from folith import concept_cards as cc
from folith import term_revision as tr
from folith.entity_registry import EntityRegistry
from folith.engine import glossary_ops


# ---------------------------------------------------------------------------
# safe_replace — unit tests
# ---------------------------------------------------------------------------

class TestSafeReplace:
    """Unit tests for consistency.safe_replace."""

    def test_no_replace_forms_returns_text_unchanged(self):
        assert consistency.safe_replace("耶鲁大学", [], "耶鲁大学") == "耶鲁大学"

    def test_basic_cjk_replacement(self):
        result = consistency.safe_replace("他在耶鲁读书", ["耶鲁"], "耶鲁大学", ["耶鲁大学"])
        assert result == "他在耶鲁大学读书"

    # --- Bug case 1: target contains variant as a prefix ---
    def test_target_contains_variant_as_prefix_no_corruption(self):
        """耶鲁 → 耶鲁大学: segment already has 耶鲁大学, must not become 耶鲁大学大学."""
        text = "他在耶鲁大学读书"
        result = consistency.safe_replace(text, ["耶鲁"], "耶鲁大学", ["耶鲁大学"])
        assert result == text, "already-correct form must not be corrupted"

    # --- Bug case 2: another registered entity contains the variant ---
    def test_registered_longer_entity_protected(self):
        """艾米莉 → 艾米丽: segment has both 艾米莉 and 艾米莉娜, only 艾米莉 is replaced."""
        text = "艾米莉和艾米莉娜见面"
        result = consistency.safe_replace(text, ["艾米莉"], "艾米丽", ["艾米丽", "艾米莉娜"])
        assert result == "艾米丽和艾米莉娜见面"

    # --- Bug case 3: known limit — unregistered longer form ---
    def test_known_limit_unregistered_longer_word(self):
        """纽约城 → 纽约市: 纽约城市规划 is unregistered and cannot be detected.

        KNOWN LIMIT: this replacement is incorrect but cannot be fixed without
        registering 纽约城市规划 as a protected form.
        """
        text = "纽约城市规划"
        result = consistency.safe_replace(text, ["纽约城"], "纽约市", ["纽约市"])
        # The bug remains for unregistered longer words: document as expected
        assert result == "纽约市市规划", (
            "KNOWN LIMIT: unregistered 纽约城市规划 cannot be detected; "
            "纽约城 matches and corrupts the string")

    # --- Idempotence ---
    def test_idempotence_no_double_replacement(self):
        """Applying the replacement twice should give the same result."""
        text = "他在耶鲁读书，也在耶鲁图书馆看书"
        once = consistency.safe_replace(text, ["耶鲁"], "耶鲁大学", ["耶鲁大学"])
        twice = consistency.safe_replace(once, ["耶鲁"], "耶鲁大学", ["耶鲁大学"])
        assert once == twice, "replacement must be idempotent"

    # --- Mixed protected and unprotected in one segment ---
    def test_multiple_occurrences_mixed_protected_unprotected(self):
        """Segment has both a standalone variant and a protected form in one line."""
        text = "艾米莉来了，艾米莉娜也来了，艾米莉走了"
        result = consistency.safe_replace(text, ["艾米莉"], "艾米丽", ["艾米丽", "艾米莉娜"])
        assert result == "艾米丽来了，艾米莉娜也来了，艾米丽走了"

    # --- Suffix variant: variant is a suffix of a longer replace form ---
    def test_longer_replace_form_matches_before_shorter_one(self):
        """When both 耶鲁大 and 耶鲁 are in replace_forms, 耶鲁大 (longer) wins."""
        text = "他在耶鲁大学学习"  # has 耶鲁大 as substring of 耶鲁大学
        result = consistency.safe_replace(text, ["耶鲁大", "耶鲁"], "耶鲁大学", ["耶鲁大学"])
        assert result == text  # 耶鲁大学 is protected; no match escapes it

    # --- ASCII: word-boundary behaviour unchanged ---
    def test_ascii_word_boundaries_unchanged(self):
        """ASCII terms must not match inside longer words (existing behaviour)."""
        text = "term and terminal"
        result = consistency.safe_replace(text, ["term"], "word", ["word"])
        assert result == "word and terminal", "ASCII word boundary: 'terminal' must not be touched"

    def test_ascii_word_boundaries_no_match_inside_word(self):
        """'cat' in 'concatenate' must not match."""
        text = "concatenate the cat"
        result = consistency.safe_replace(text, ["cat"], "feline", ["feline"])
        assert result == "concatenate the feline"

    # --- Arabic: no word boundaries, but no regression ---
    def test_arabic_no_regression(self):
        """Arabic forms use no ASCII boundaries; a standalone form is replaced."""
        text = "قرأ الطالب الكتاب"
        result = consistency.safe_replace(text, ["الكتاب"], "الرواية", ["الرواية"])
        assert result == "قرأ الطالب الرواية"

    # --- Case-insensitive ASCII ---
    def test_ascii_case_insensitive(self):
        """ASCII replacement is case-insensitive (matches variant regardless of case)."""
        text = "He went to Yale for a year"
        result = consistency.safe_replace(text, ["yale"], "Harvard", ["Harvard"])
        assert result == "He went to Harvard for a year"

    # --- Prefix variant: variant is longer than target (target is a prefix of variant) ---
    def test_prefix_variant_no_corruption(self):
        """Replacing 艾米莉娜 (longer) → 艾米丽: 艾米丽 (target, shorter) stays intact."""
        text = "艾米莉娜来了，艾米丽已在此"
        result = consistency.safe_replace(text, ["艾米莉娜"], "艾米丽", ["艾米丽"])
        assert result == "艾米丽来了，艾米丽已在此"


# ---------------------------------------------------------------------------
# change_snippets — unit tests
# ---------------------------------------------------------------------------

class TestChangeSnippets:
    """Tests for consistency.change_snippets."""

    def _record(self, source, preferred, variants):
        """Build a minimal entity record."""
        return {
            "source_form": source,
            "preferred_target": preferred,
            "observations": [{"segment_id": 0, "target": v} for v in variants],
            "occurrences": [0, 1],
        }

    def _pair(self, source, target):
        return {"source": source, "target": target}

    def test_returns_per_occurrence_snippets(self):
        record = self._record("Yale", "耶鲁大学", ["耶鲁"])
        pairs = [
            self._pair("Yale", "他去耶鲁读书"),
            self._pair("Yale", "耶鲁大学欢迎他"),
        ]
        snippets = consistency.change_snippets(record, pairs, "耶鲁大学")
        # Segment 0 has standalone 耶鲁 → replaced
        # Segment 1 has 耶鲁大学 (protected) → no snippet
        assert len(snippets) == 1
        assert snippets[0]["segment_index"] == 0
        assert "耶鲁" in snippets[0]["old_snippet"]
        assert "耶鲁大学" in snippets[0]["new_snippet"]

    def test_snippets_have_context(self):
        record = self._record("Ella", "艾米丽", ["艾米莉"])
        pairs = [self._pair("Ella", "艾米莉和艾米莉娜见面")]
        snippets = consistency.change_snippets(record, pairs, "艾米丽",
                                               known_forms=["艾米莉娜"])
        # Only the standalone 艾米莉 (at position 0) produces a snippet.
        # 艾米莉娜 is protected and must not produce its own snippet.
        assert len(snippets) == 1, "protected 艾米莉娜 must not generate a snippet"
        assert snippets[0]["segment_index"] == 0
        # The old_snippet contains the original 艾米莉 at the start
        assert snippets[0]["old_snippet"].startswith("艾米莉")
        # The new_snippet has 艾米丽 instead of 艾米莉 at the same position
        assert snippets[0]["new_snippet"].startswith("艾米丽")


# ---------------------------------------------------------------------------
# preview — integration: count equals actual changes
# ---------------------------------------------------------------------------

class TestPreviewCountEqualsChanges:
    """The 改 N 段 count must equal the number of segments actually changed."""

    def _record(self, source, preferred, variants, segments):
        return {
            "source_form": source,
            "preferred_target": preferred,
            "observations": [{"segment_id": s, "target": v}
                              for s, v in zip(segments, variants)],
            "occurrences": list(segments),
        }

    def test_count_matches_for_already_correct_segment(self):
        """Segment already using the target must not be counted."""
        record = self._record("Yale", "耶鲁大学", ["耶鲁", "耶鲁大学"], [0, 1])
        pairs = [
            {"source": "Yale", "target": "他在耶鲁读书"},      # will change
            {"source": "Yale", "target": "他在耶鲁大学读书"},   # already correct
        ]
        changes = consistency.preview(record, pairs, "耶鲁大学")
        assert len(changes) == 1
        assert changes[0]["index"] == 0
        assert changes[0]["after"] == "他在耶鲁大学读书"

    def test_count_does_not_include_protected_only_segment(self):
        """A segment whose only matches are protected forms must not be counted."""
        record = self._record("Yale", "耶鲁大学", ["耶鲁"], [0])
        pairs = [{"source": "Yale", "target": "他在耶鲁大学读书"}]
        changes = consistency.preview(record, pairs, "耶鲁大学")
        assert changes == [], "protected-only segment must not appear in changes"


# ---------------------------------------------------------------------------
# Term revision: safe_replace with new_target as protected
# ---------------------------------------------------------------------------

class TestTermRevisionSafeReplace:
    """preview_term_revision must not corrupt segments already using the new target."""

    def _pairs(self):
        return [
            {"source": "Yale", "target": "耶鲁读书", "segment_uid": "t0",
             "target_provenance": "generated"},
            {"source": "Yale", "target": "他在耶鲁大学读书", "segment_uid": "t1",
             "target_provenance": "generated"},
            {"source": "Yale", "target": "耶鲁大学很好", "segment_uid": "t2",
             "target_provenance": "generated"},
        ]

    def test_target_containing_variant_not_corrupted(self):
        preview = tr.preview_term_revision(
            "Yale", "耶鲁", "耶鲁大学", self._pairs())
        rows_by_seg = {row["segment_index"]: row for row in preview["rows"]}
        # Segment 0: has standalone 耶鲁 → should be replaced
        assert 0 in rows_by_seg
        assert rows_by_seg[0]["after"] == "耶鲁大学读书"
        # Segment 1: 耶鲁大学 (target protected) — no standalone 耶鲁 outside it
        assert 1 not in rows_by_seg, "already-correct segment must not appear in rows"
        # Segment 2: 耶鲁大学很好 — already correct
        assert 2 not in rows_by_seg

    def test_idempotent_term_revision(self):
        """Applying the same term revision twice must not change the result."""
        pairs = [{"source": "Yale", "target": "他在耶鲁读书",
                  "segment_uid": "i0", "target_provenance": "generated"}]
        p1 = tr.preview_term_revision("Yale", "耶鲁", "耶鲁大学", pairs)
        assert p1["rows"]
        after_text = p1["rows"][0]["after"]
        pairs2 = [{"source": "Yale", "target": after_text,
                   "segment_uid": "i0", "target_provenance": "generated"}]
        p2 = tr.preview_term_revision("Yale", "耶鲁", "耶鲁大学", pairs2)
        assert p2["rows"] == [], "second application must be a no-op (idempotent)"


# ---------------------------------------------------------------------------
# Concept revision: protected forms from entity registry
# ---------------------------------------------------------------------------

class TestConceptRevisionSafeReplace:
    """preview_concept_revision must protect registered entity forms."""

    def _state_with_two_entities(self):
        """Kafka concept + 艾米莉娜 as a separate entity."""
        registry = EntityRegistry()
        registry.observe("Amelia", "艾米莉", segment_id=0)
        registry.observe("Amelia", "艾米丽", segment_id=1)
        registry.observe("Ameliana", "艾米莉娜", segment_id=2)
        decisions: dict = {}
        cards = cc.build_concept_cards(registry.to_list(), [
            {"source": "Amelia", "target": "艾米莉"},
            {"source": "Amelia", "target": "艾米丽"},
            {"source": "Ameliana", "target": "艾米莉娜"},
        ])
        amelia_card = next(c for c in cards
                           if any("amelia" == k for k in c["member_keys"])
                           and not any("ameliana" == k for k in c["member_keys"]))
        cc.record_concept_decision(decisions, card=amelia_card,
                                   preferred_target="艾米丽")
        amelia_card = next(
            c for c in cc.build_concept_cards(registry.to_list(), [
                {"source": "Amelia", "target": "艾米莉"},
                {"source": "Amelia", "target": "艾米丽"},
                {"source": "Ameliana", "target": "艾米莉娜"},
            ], decisions)
            if c["card_id"] == amelia_card["card_id"])
        return registry.to_list(), amelia_card, decisions

    def test_registered_longer_entity_protected_in_concept_revision(self):
        records, card, decisions = self._state_with_two_entities()
        pairs = [
            {"source": "Amelia", "target": "艾米莉和艾米莉娜见面"},
        ]
        preview = cr.preview_concept_revision(card, records, pairs, decisions)
        assert preview["rows"], "segment with standalone 艾米莉 must appear"
        assert preview["rows"][0]["after"] == "艾米丽和艾米莉娜见面"

    def test_concept_revision_count_matches_actual_changes(self):
        records, card, decisions = self._state_with_two_entities()
        pairs = [
            {"source": "Amelia", "target": "艾米莉和艾米莉娜见面"},   # changes
            {"source": "Amelia", "target": "艾米莉娜是另一人"},        # 艾米莉 inside 艾米莉娜: unchanged
        ]
        preview = cr.preview_concept_revision(card, records, pairs, decisions)
        changed_indexes = {row["segment_index"] for row in preview["rows"]}
        assert 0 in changed_indexes
        assert 1 not in changed_indexes, \
            "segment whose only match is inside a protected form must not be in rows"


# ---------------------------------------------------------------------------
# collect_known_forms helper
# ---------------------------------------------------------------------------

class TestCollectKnownForms:
    """consistency.collect_known_forms must collect from registry and glossary."""

    def test_collects_entity_preferred_and_observations(self):
        state = {
            "entity_registry": [
                {"source_form": "Emily", "preferred_target": "艾米丽",
                 "observations": [{"target": "艾米莉"}]},
                {"source_form": "Ameliana", "preferred_target": "艾米莉娜",
                 "observations": []},
            ],
            "glossary": [],
        }
        forms = consistency.collect_known_forms(state)
        assert "艾米丽" in forms
        assert "艾米莉" in forms
        assert "艾米莉娜" in forms

    def test_collects_glossary_preferred(self):
        state = {
            "entity_registry": [],
            "glossary": [{"source": "Yale", "preferred": "耶鲁大学", "target": ""}],
        }
        forms = consistency.collect_known_forms(state)
        assert "耶鲁大学" in forms

    def test_deduplicates(self):
        state = {
            "entity_registry": [
                {"source_form": "X", "preferred_target": "甲",
                 "observations": [{"target": "甲"}]},
            ],
            "glossary": [{"source": "X", "preferred": "甲"}],
        }
        forms = consistency.collect_known_forms(state)
        assert forms.count("甲") == 1

    def test_empty_state_returns_empty(self):
        assert consistency.collect_known_forms({}) == []
        assert consistency.collect_known_forms(None) == []


# ---------------------------------------------------------------------------
# glossary_ops entry-point: preview_entity_repair protects registered forms
# ---------------------------------------------------------------------------

@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _make_entity_job(job_id):
    """Job with two entities: 艾米莉/艾米丽 conflict AND registered 艾米莉娜."""
    registry = EntityRegistry()
    registry.observe("Emily", "艾米莉", segment_id=0)
    registry.observe("Emily", "艾米丽", segment_id=1)
    registry.observe("Ameliana", "艾米莉娜", segment_id=2)
    state = core.new_job_state("entity-repair.docx")
    pairs = [
        {"source": "Emily met Ameliana.",
         "target": "艾米莉和艾米莉娜见面",
         "target_provenance": "generated", "segment_uid": "e0"},
        {"source": "Emily smiled.",
         "target": "艾米丽笑了",
         "target_provenance": "generated", "segment_uid": "e1"},
        {"source": "Ameliana alone.",
         "target": "艾米莉娜独自一人",
         "target_provenance": "generated", "segment_uid": "e2"},
    ]
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [p["source"] for p in pairs],
        "pairs": pairs,
        "entity_registry": registry.to_list(),
        "findings": [],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


class TestGlossaryOpsEntryPoint:
    """Production entry path: glossary_ops.preview_entity_repair must protect
    registered longer forms even when they contain the variant as a prefix."""

    def test_preview_protects_registered_ameliana(self, job_env):
        """艾米莉→艾米丽: segment 0 has both; 艾米莉娜 is registered → protected."""
        state = _make_entity_job("job-entity-1")
        changes = glossary_ops.preview_entity_repair(state, "Emily", "艾米丽")
        by_seg = {c["index"]: c for c in changes}
        # Segment 0: standalone 艾米莉 → 艾米丽, but 艾米莉娜 must NOT change
        assert 0 in by_seg, "segment with standalone 艾米莉 must appear in preview"
        assert by_seg[0]["after"] == "艾米丽和艾米莉娜见面", \
            "艾米莉娜 must be preserved; only standalone 艾米莉 is replaced"
        # Segment 1: already 艾米丽 → no change
        assert 1 not in by_seg, "already-correct segment must not appear"
        # Segment 2: only 艾米莉娜 (protected) → no change
        assert 2 not in by_seg, "protected-only segment must not appear"

    def test_apply_writes_exactly_what_preview_promised(self, job_env):
        """apply_entity_repair writes the exact before/after pairs from the preview.

        The apply path enforces this in two ways:
        1. apply_entity_repair calls preview_entity_repair again inside the
           @_serialized_job_mutation lock and raises ValueError if the current
           result differs from the passed preview (concurrent-edit guard).
        2. It then writes change["after"] for each change["index"] verbatim
           (see glossary_ops.py apply_entity_repair: pair.update(target=change["after"])).
        This test verifies the end-to-end invariant: written text == preview["after"].
        """
        state = _make_entity_job("job-entity-2")
        # Capture original targets before apply
        original_targets = [p["target"] for p in state["pairs"]]
        preview = glossary_ops.preview_entity_repair(state, "Emily", "艾米丽")
        assert preview, "preview must have at least one change"
        core.apply_entity_repair("job-entity-2", "Emily", "艾米丽", preview)
        state2 = core.load_job_state("job-entity-2")
        # Segments in preview: written text must match the promised after
        preview_map = {c["index"]: c["after"] for c in preview}
        for idx, expected_after in preview_map.items():
            assert state2["pairs"][idx]["target"] == expected_after, \
                f"segment {idx}: apply wrote {state2['pairs'][idx]['target']!r} " \
                f"but preview promised {expected_after!r}"
        # Segments NOT in preview: original text preserved
        for idx, orig in enumerate(original_targets):
            if idx not in preview_map:
                assert state2["pairs"][idx]["target"] == orig, \
                    f"segment {idx} was not in preview but was modified"


# ---------------------------------------------------------------------------
# term_revision entry-point: known_forms from state reaches safe_replace
# ---------------------------------------------------------------------------

class TestTermRevisionEntryPoint:
    """preview_term_revision with known_forms must protect registered entities."""

    def test_known_forms_protect_ameliana_in_term_revision(self):
        """When a glossary/entity registry has 艾米莉娜, term_revision must protect it."""
        pairs = [
            {"source": "Emily met Ameliana.", "target": "艾米莉和艾米莉娜见面",
             "target_provenance": "generated", "segment_uid": "t0"},
        ]
        # Without known_forms: old behaviour (not under test here but documents baseline)
        # With known_forms: 艾米莉娜 is protected
        preview = tr.preview_term_revision(
            "Emily", "艾米莉", "艾米丽", pairs,
            known_forms=["艾米丽", "艾米莉娜"])
        rows = {row["segment_index"]: row for row in preview["rows"]}
        assert 0 in rows, "segment with standalone 艾米莉 must appear"
        assert rows[0]["after"] == "艾米丽和艾米莉娜见面", \
            "艾米莉娜 must be preserved by known_forms protection"

    def test_term_revision_count_matches_changes(self):
        """rows count == segments actually changed, same logic as safe_replace."""
        pairs = [
            {"source": "Emily.", "target": "艾米莉和艾米莉娜见面",
             "target_provenance": "generated", "segment_uid": "u0"},
            {"source": "Emily.", "target": "艾米莉娜独自一人",
             "target_provenance": "generated", "segment_uid": "u1"},
        ]
        preview = tr.preview_term_revision(
            "Emily", "艾米莉", "艾米丽", pairs,
            known_forms=["艾米丽", "艾米莉娜"])
        # Segment 1 has only 艾米莉娜 (protected): must not appear in rows
        row_segs = {r["segment_index"] for r in preview["rows"]}
        assert 0 in row_segs
        assert 1 not in row_segs, \
            "segment whose only 艾米莉 is inside protected 艾米莉娜 must not be in rows"
