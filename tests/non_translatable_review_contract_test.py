"""A segment the system proves is not translatable must not dead-end the workflow.

Regression for the macOS frozen-app acceptance blocker: the source-quality gate
quarantined six units of the real two-page ``arxiv-attention-2p.pdf`` run, and
that quarantine could neither be translated, nor reviewed (``第 … 段原文尚未核对``),
nor delivered.  The deterministic disposition below closes that loop without
letting genuinely unverified prose bypass review.
"""
import json
from pathlib import Path

import core
from folith import delivery
from folith.translation_evidence import translation_review_readiness

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "arxiv_attention_2p_source_quality.json")

# Display numbers 33, 36, 58, 59, 71, 82 of the real acceptance run are these
# zero-based segment indexes.
REAL_FLAGGED = [32, 35, 57, 58, 70, 81]
REAL_STRUCTURAL = [57, 58, 70, 81]   # arXiv margin stamp + three heading ordinals
REAL_ADMITTED = [32, 35]             # "mechanism." / "entirely." line-wrap tails

CLEAN_REVIEW = lambda *args, **kwargs: json.dumps(  # noqa: E731 - tiny stub
    {"findings": [], "evidence_requests": []})


def _block(block_id, block_type, text, bbox):
    return {
        "block_id": block_id,
        "type": block_type,
        "text": text,
        "source_anchor": {"source_spans": [{"block_id": block_id, "bbox": bbox}]},
    }


def _stage_one_state(tmp_path, monkeypatch, job_id, *, paras, blocks,
                     source_paras=None, source_paragraphs=None,
                     paragraph_ranges=None, spans=None, filename="sample.pdf"):
    """Build the pre-translation state stage one leaves behind."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state(filename)
    state.update(
        paras=list(paras),
        source_paras=list(source_paras if source_paras is not None else paras),
        source_paragraphs=list(source_paragraphs
                               if source_paragraphs is not None else paras),
        segmentation={
            "mode": "sentence", "locked": True,
            "paragraph_ranges": paragraph_ranges or [
                {"paragraph_index": index, "start": index, "end": index + 1}
                for index in range(len(paras))
            ],
        },
        segment_source_spans=spans if spans is not None else [
            [{"block_id": block["block_id"]}] for block in blocks
        ],
        extraction_report={
            "semantic_source": "native_pdf",
            "document_ir": {"blocks": blocks},
        },
        pairs=[], findings=[], review_evidence=[], human_actions=[],
        excluded_segments=[], exclusion_seq=0, source_quality_auto_exclusions=[],
        p1_done=True, p2_done=False, target_lang="简体中文", use_tm=False,
        translation_core_review_required=False,
    )
    core.save_job_state(job_id, state)
    return state


def _pairs_from(paras, *, empty_indexes=()):
    return [
        {
            "source": text,
            "target": "" if index in empty_indexes else f"译文{index}。",
            "initial_target": "" if index in empty_indexes else f"译文{index}。",
            "reviewed": False,
            "review_status": "not_reviewed",
        }
        for index, text in enumerate(paras)
    ]


# --------------------------------------------------------------------------
# Test 1 — equation-only + empty target: excluded, no review, delivery allowed
# --------------------------------------------------------------------------
def test_equation_only_segment_is_excluded_and_delivery_succeeds(tmp_path, monkeypatch):
    job_id = "non-translatable-equation"
    blocks = [
        _block("b0", "equation", "E = mc^2", [0.20, 0.20, 0.60, 0.30]),
        _block("b1", "paragraph", "A complete introductory sentence.",
               [0.176, 0.40, 0.826, 0.50]),
    ]
    state = _stage_one_state(
        tmp_path, monkeypatch, job_id,
        paras=["E = mc^2", "A complete introductory sentence."], blocks=blocks)

    # The gate does flag it; the IR block type proves it is not body prose.
    raw = core._audit_source_quality_for_state(state, apply_admissions=False)
    assert [flag["segment_index"] for flag in raw["flags"]] == [0]

    records = core._auto_exclude_non_translatable_segments(job_id, state)
    assert [record["source"] for record in records] == ["E = mc^2"]
    assert records[0]["excluded_by"] == "system:source_quality"
    assert "ir_block_type:equation" in records[0]["reason"]
    # Canonical exclusion: out of the working list, not marked translated.
    assert state["paras"] == ["A complete introductory sentence."]
    assert state["excluded_segments"][0]["excluded_id"] == records[0]["excluded_id"]
    assert core._audit_source_quality_for_state(state)["status"] == "passed"
    assert core.excluded_segments_summary(state)["count"] == 1

    # Finish the translation contract for what is left, then review and deliver.
    state["pairs"] = _pairs_from(state["paras"])
    state.update(p2_done=True, translation_core_review_required=True)
    core.save_job_state(job_id, state)

    refreshed, result = core.review_translation_segments(
        job_id, [0], "p", "k", "m", "简体中文", call_llm_fn=CLEAN_REVIEW)
    assert result == {"reviewed_segment_ids": [0], "failed_segment_ids": []}
    ready = translation_review_readiness(refreshed)
    assert ready["ready"] and ready["expected_segment_ids"] == [0]

    approved, ok, errors = delivery.approve_delivery(refreshed)
    assert ok and not errors, errors
    assert approved["delivery_status"] == "final"
    # The excluded unit never becomes a review obligation or a delivery notice.
    assert 0 not in ready["missing_segment_ids"]


# --------------------------------------------------------------------------
# Test 2 — ordinary prose with an empty target stays blocked
# --------------------------------------------------------------------------
def test_ordinary_prose_with_empty_target_is_not_auto_excluded(tmp_path, monkeypatch):
    job_id = "non-translatable-prose"
    blocks = [
        _block("b0", "paragraph", "A complete introductory sentence.",
               [0.176, 0.20, 0.826, 0.30]),
        _block("b1", "paragraph", "A complete introductory sentence. kati",
               [0.176, 0.30, 0.826, 0.40]),
    ]
    state = _stage_one_state(
        tmp_path, monkeypatch, job_id,
        paras=["A complete introductory sentence.", "kati"], blocks=blocks,
        source_paras=["A complete introductory sentence.", "kati"],
        source_paragraphs=["A complete introductory sentence. kati"],
        paragraph_ranges=[{"paragraph_index": 0, "start": 0, "end": 2}])

    raw = core._audit_source_quality_for_state(state, apply_admissions=False)
    assert 1 in [flag["segment_index"] for flag in raw["flags"]]

    assert core._auto_exclude_non_translatable_segments(job_id, state) == []
    assert state["excluded_segments"] == []
    assert core._audit_source_quality_for_state(state)["status"] == "blocked"

    # The quarantined prose unit keeps today's contract: source-only, no model
    # call, no silent "translated" state, and review still refuses it.
    state["pairs"] = _pairs_from(state["paras"], empty_indexes={1})
    state.update(p2_done=True, translation_core_review_required=True)
    core.save_job_state(job_id, state)

    try:
        core.review_translation_segments(
            job_id, [0, 1], "p", "k", "m", "简体中文", call_llm_fn=CLEAN_REVIEW)
        raise AssertionError("unverified prose must not be reviewed as translated")
    except ValueError as exc:
        assert "原文尚未核对" in str(exc)

    report = core.validate_delivery_translation_state(state)
    assert [issue["segment_index"] for issue in report["issues"]
            if issue["code"] == "empty_target"] == [1]
    assert not translation_review_readiness(state)["ready"]
    assert delivery.compute_delivery_status(state) == "review_required"
    _, ok, errors = delivery.approve_delivery(state)
    assert not ok and errors
    # Not excluded, not translated, not reviewed.
    assert state["pairs"][1]["target"] == ""
    assert state["pairs"][1]["reviewed"] is False
    assert not state["pairs"][1].get("excluded")


# --------------------------------------------------------------------------
# Test 3 — unverified equation-only source does not trigger 原文尚未核对
# --------------------------------------------------------------------------
def test_unverified_equation_only_source_does_not_trigger_source_check_refusal(
        tmp_path, monkeypatch):
    job_id = "non-translatable-refusal"
    blocks = [
        _block("b0", "paragraph", "A complete introductory sentence.",
               [0.176, 0.20, 0.826, 0.30]),
        _block("b1", "equation", "x = y + z", [0.30, 0.50, 0.70, 0.60]),
        _block("b2", "paragraph", "Another complete sentence follows.",
               [0.176, 0.70, 0.826, 0.80]),
    ]
    state = _stage_one_state(
        tmp_path, monkeypatch, job_id,
        paras=["A complete introductory sentence.", "x = y + z",
               "Another complete sentence follows."],
        blocks=blocks)
    core._auto_exclude_non_translatable_segments(job_id, state)
    assert state["paras"] == ["A complete introductory sentence.",
                              "Another complete sentence follows."]

    state["pairs"] = _pairs_from(state["paras"])
    state.update(p2_done=True, translation_core_review_required=True)
    core.save_job_state(job_id, state)

    # Whole-book review used to abort before the first model call.
    refreshed, result = core.review_translation_segments(
        job_id, [0, 1], "p", "k", "m", "简体中文", call_llm_fn=CLEAN_REVIEW)
    assert result == {"reviewed_segment_ids": [0, 1], "failed_segment_ids": []}
    assert translation_review_readiness(refreshed)["ready"]


# --------------------------------------------------------------------------
# Test 4 — excluded segment is not written to TM nor counted as reviewed
# --------------------------------------------------------------------------
def test_excluded_segment_is_not_inserted_into_tm_or_counted_as_reviewed(
        tmp_path, monkeypatch):
    job_id = "non-translatable-tm"
    blocks = [
        _block("b0", "equation", "E = mc^2", [0.20, 0.20, 0.60, 0.30]),
        _block("b1", "paragraph", "A complete introductory sentence.",
               [0.176, 0.40, 0.826, 0.50]),
    ]
    state = _stage_one_state(
        tmp_path, monkeypatch, job_id,
        paras=["E = mc^2", "A complete introductory sentence."], blocks=blocks)
    state["use_tm"] = True
    core._auto_exclude_non_translatable_segments(job_id, state)

    state["pairs"] = _pairs_from(state["paras"])
    state.update(p2_done=True, translation_core_review_required=True)
    core.save_job_state(job_id, state)
    refreshed, _ = core.review_translation_segments(
        job_id, [0], "p", "k", "m", "简体中文", call_llm_fn=CLEAN_REVIEW)

    tm = core.load_tm(core.tm_project_id(refreshed))
    assert not core.tm_lookup(tm, "E = mc^2", target_lang="简体中文")[0]
    assert refreshed["review_stats"]["reviewed_segments"] == 1
    manifest = core.excluded_segments_manifest(refreshed)
    assert [record["source"] for record in manifest["records"]] == ["E = mc^2"]
    assert all(not record.get("reviewed") for record in manifest["records"])
    assert refreshed["excluded_segments"][0]["excluded_by"] == "system:source_quality"


# --------------------------------------------------------------------------
# Test 5 — the real six-segment acceptance state no longer dead-ends
# --------------------------------------------------------------------------
def test_real_six_segment_acceptance_state_no_longer_dead_ends(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    job_id = "arxiv-attention-2p-regression"
    state = _stage_one_state(
        tmp_path, monkeypatch, job_id,
        paras=fixture["paras"],
        blocks=fixture["extraction_report"]["document_ir"]["blocks"],
        source_paras=fixture["source_paras"],
        source_paragraphs=fixture["source_paragraphs"],
        paragraph_ranges=fixture["segmentation"]["paragraph_ranges"],
        spans=fixture["segment_source_spans"],
        filename=fixture["filename"])
    state["segmentation"] = dict(fixture["segmentation"])
    state["target_lang"] = fixture["target_lang"]
    core.save_job_state(job_id, state)

    # Reproduction: exactly the six units the frozen app reported.
    raw = core._audit_source_quality_for_state(state, apply_admissions=False)
    assert sorted(flag["segment_index"] for flag in raw["flags"]) == REAL_FLAGGED

    outcomes = core._source_quality_dispositions(state, raw)
    assert sorted(index for index, outcome in outcomes.items()
                  if outcome["disposition"] == "non_translatable_structural") \
        == REAL_STRUCTURAL
    assert sorted(index for index, outcome in outcomes.items()
                  if outcome["disposition"] == "integrated_admission") \
        == REAL_ADMITTED

    records = core._auto_exclude_non_translatable_segments(job_id, state)
    assert sorted(record["index"] for record in records) == REAL_STRUCTURAL
    assert len(state["paras"]) == len(fixture["paras"]) - len(REAL_STRUCTURAL)
    assert core._audit_source_quality_for_state(state)["status"] == "passed"
    assert core._audit_source_quality_for_state(state)["flags"] == []
    # The two line-wrap tails stay in the working set as ordinary source text.
    assert "mechanism." in state["paras"]
    assert "entirely." in state["paras"]
    # The exclusion ledger names the real evidence and the system actor.
    assert {record["source"] for record in state["excluded_segments"]} == {
        "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", "1", "2", "3"}
    assert {record["excluded_by"] for record in state["excluded_segments"]} == {
        "system:source_quality"}
    # Paragraph ranges keep pointing at the right units after the removals.
    ranges = state["segmentation"]["paragraph_ranges"]
    assert ranges[-1]["end"] == len(state["paras"])
    assert all(row["start"] <= row["end"] for row in ranges)

    # Whole-book review of everything that remains must now run to completion.
    state["pairs"] = _pairs_from(state["paras"])
    state.update(p2_done=True, translation_core_review_required=True)
    core.save_job_state(job_id, state)
    refreshed, result = core.review_translation_segments(
        job_id, list(range(len(state["pairs"]))), "p", "k", "m", "简体中文",
        call_llm_fn=CLEAN_REVIEW)
    assert result["failed_segment_ids"] == []
    ready = translation_review_readiness(refreshed)
    # Every remaining unit is current for the review gate; none is missing
    # because of a source-quality flag, which is what used to abort the run.
    assert ready["status"] == "current" and ready["ready"]
    assert len(ready["expected_segment_ids"]) == len(state["pairs"])
    assert ready["missing_segment_ids"] == []
    _, ok, errors = delivery.approve_delivery(refreshed)
    assert ok and not errors, errors
