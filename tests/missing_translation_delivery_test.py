"""Missing translations remain visible and exportable without weakening parsing."""
import json
from io import BytesIO

from docx import Document

import core
from folith import assets, delivery, translation_target
from folith.translation_evidence import translation_review_readiness


def _merged_state(raw, cleaned):
    state = core.new_job_state("report.pdf")
    state.update(
        source_paras=raw, source_paragraphs=[cleaned], paras=[cleaned],
        source_cleanup={"output_input_indices": [list(range(len(raw)))]},
        segmentation={"paragraph_ranges": [{"paragraph_index": 0, "start": 0, "end": 1}]},
    )
    return state


def test_retained_directory_page_number_does_not_quarantine_title():
    for title, number in [("Conclusion: The Growth Challenge", "21"),
                          ("Middle East Airlines: Big Ambitions, Fierce Competition", "6")]:
        state = _merged_state([title, number], f"{title} {number}")
        assert core._audit_source_quality_for_state(state)["flags"] == []


def test_numeric_lineage_is_not_discarded_when_number_is_missing_or_changed():
    for cleaned in ["The chapter title", "The chapter title 2023", "The chapter title 3.2", "3"]:
        state = _merged_state(["The chapter title", "3"], cleaned)
        assert core._audit_source_quality_for_state(state)["flagged_count"] == 1
    state = _merged_state(["The chapter title", "kati", "3"], "The chapter title kati 3")
    flags = core._audit_source_quality_for_state(state)["source_lineage_flags"]
    assert any(flag["text"] == "kati" for flag in flags)


def test_rejoined_pdf_bullet_continuation_is_not_quarantined():
    for raw in [
        ["– There are very few domestic markets within the region, and whilst they",
         "are crucial for connectivity in some of the larger Middle East markets they",
         "account for just 20% of all capacity"],
        ["– The major domestic markets are in Saudi Arabia and Iran which account",
         "for 94% of all domestic seats - the Saudi Arabian domestic market is",
         "three times larger than Iran’s domestic market, despite the larger",
         "population in Iran"],
    ]:
        assert not core._audit_source_quality_for_state(
            _merged_state(raw, " ".join(raw)))["flags"]


def test_short_ocr_noise_and_unrelated_fragments_keep_lineage_flags():
    for tail in ["kati", "stud with ber", "Otte.)", "5p"]:
        raw = ["A complete introductory sentence.", tail]
        assert core._audit_source_quality_for_state(
            _merged_state(raw, " ".join(raw)))["flags"]
    raw = ["The report describes a very large regional market with", "many connecting passengers"]
    # A merge map alone cannot excuse a fragment absent from the final text.
    assert core._audit_source_quality_for_state(
        _merged_state(raw, "The report describes a very large regional market."))["flags"]


def test_lowercase_brand_year_heading_is_not_an_ocr_fragment():
    assert not core.audit_source_quality([
        "flynas 2024 Network", "flydubai 2024 network",
    ])["flags"]


def test_empty_model_target_remains_invalid_but_delivery_is_a_notice():
    pair = {"source": "A chapter title", "target": ""}
    assert translation_target.validate_translation_target(**pair)["blocking"]
    report = translation_target.validate_translation_pairs([pair], allow_empty=True)
    assert not report["blocking"]
    assert report["issues"][0]["severity"] == "actionable"
    assert not translation_review_readiness({
        "pairs": [pair], "translation_core_review_required": True,
    })["missing_segment_ids"]
    bad = {"source": "A chapter title", "target": '["raw model response"]'}
    assert translation_target.validate_translation_pairs([bad], allow_empty=True)["blocking"]


def test_existing_missing_source_gate_rows_export_with_empty_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("report.pdf")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False,
        paras=["kati", "A real sentence."],
        pairs=[{"source": "kati", "target": "", "target_provenance": "source_review_required"},
               {"source": "A real sentence.", "target": "一句完整的话。"}],
        findings=[{"type": "source_quality_gate", "category": "source_quality",
                   "segment_index": 0, "severity": "blocking", "reason": "原文待核对"},
                  {"type": "delivery_invariant", "invariant_code": "empty_target",
                   "segment_index": 0, "severity": "blocking", "reason": "正文段落译文不能为空"}],
        delivery_config=core.normalize_delivery_config({"deliver_plain_docx": True,
                                                        "deliver_bilingual_docx": True,
                                                        "deliver_jsonl": True}),
    )
    core.save_job_state("missing", state)
    report = core.validate_delivery_translation_state(state)
    assert not report["blocking"]
    assert report["source_quality_findings"][0]["severity"] == "actionable"
    assert not delivery.unresolved_blocking(state)
    bundle = core.build_delivery_assets("missing", state)
    table = Document(BytesIO(bundle["bilingual.docx"])).tables[0]
    assert table.rows[1].cells[0].text == "kati"
    assert table.rows[1].cells[1].text == ""
    assert table.rows[2].cells[1].text == "一句完整的话。"
    assert json.loads(assets.build_jsonl(state).splitlines()[0])["target"] == ""
    approved, ok, errors = core.approve_delivery("missing")
    assert ok, errors
    assert approved["latest_delivery_snapshot_version"] == 1


