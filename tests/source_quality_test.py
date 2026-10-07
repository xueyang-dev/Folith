"""Source-admission gate tests for OCR fragments."""

import json
from pathlib import Path

import core


def test_source_quality_gate_quarantines_the_reported_ocr_run():
    report = core.audit_source_quality([
        "The Midnight Library",
        "20",
        "kati",
        "stud with ber",
        "fl - oe",
        "Also by Matt Haig",
    ])

    assert report["status"] == "blocked"
    assert report["flagged_count"] == 4
    assert [item["segment_index"] for item in report["flags"]] == [1, 2, 3, 4]
    assert report["clusters"] == [[1, 2, 3, 4]]
    assert any(
        any("异常空格连字符" in reason for reason in item["reasons"])
        for item in report["flags"]
    )


def test_source_quality_gate_does_not_block_normal_titles_or_identifiers():
    report = core.audit_source_quality([
        "The Midnight Library",
        "Also by Matt Haig",
        "@matthaig1",
        "matthaig.com",
        "A sentence ends here.",
    ])

    assert report["status"] == "passed"
    assert report["flagged_count"] == 0


def test_translation_gate_does_not_submit_quarantined_fragments(tmp_path: Path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    calls = []
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        calls.append((system, user))
        if "学术翻译专家" in system:
            return json.dumps(["这是完整译文。"])
        if "翻译流知识抽取器" in system:
            return "[]"
        raise AssertionError(f"unexpected model call: {system[:80]}")

    core.call_llm = llm
    try:
        state = core.new_job_state("gate.pdf")
        state["paras"] = ["kati", "stud with ber", "A real sentence."]
        result = core.translate_stage(
            state, "source-quality-gate", [], "DeepSeek", "key", "model",
            "简体中文", "", enable_review=False, use_tm=False,
        )

        assert result["source_quality_gate"]["flagged_count"] == 2
        assert [pair["target_provenance"] for pair in result["pairs"]] == [
            "source_review_required", "source_review_required", "generated",
        ]
        assert result["pairs"][0]["target"] == ""
        assert result["pairs"][1]["target"] == ""
        submitted = "\n".join(user for system, user in calls if "学术翻译专家" in system)
        assert "kati" not in submitted
        assert "stud with ber" not in submitted
        gate_findings = [
            item for item in result["findings"]
            if item.get("type") == "source_quality_gate"
        ]
        assert {item["segment_index"] for item in gate_findings} == {0, 1}
        assert all(item["severity"] == "blocking" for item in gate_findings)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_delivery_gate_rechecks_legacy_runs_before_export():
    state = core.new_job_state("legacy.pdf")
    state.update(
        paras=["kati"],
        pairs=[{"source": "kati", "target": "浮冰"}],
        p2_done=True,
    )

    report = core.validate_delivery_translation_state(state)

    assert report["source_quality_gate"]["flagged_count"] == 1
    assert any(
        item.get("type") == "source_quality_gate"
        and item.get("severity") == "blocking"
        for item in report["blocking_findings"]
    )


def test_source_quality_gate_is_scoped_to_pdf_jobs():
    state = core.new_job_state("notes.docx")
    state.update(paras=["kati"], pairs=[{"source": "kati", "target": "kati"}], p2_done=True)

    report = core.validate_delivery_translation_state(state)

    assert report["source_quality_gate"]["status"] == "disabled"
    assert not report["source_quality_findings"]


# Short, lowercase, unpunctuated blocks that the OCR heuristics would quarantine.
# A VLM transcribed them from the page image, so they are semantic content, not
# rough OCR fragments.
_VLM_SEMANTIC_BLOCKS = ["kati", "stud with ber", "5p", "A real sentence."]


def test_source_quality_gate_is_disabled_for_vlm_and_mixed_semantic_sources():
    for semantic_source in ("vlm", "mixed"):
        state = core.new_job_state("scan.pdf")
        state["paras"] = list(_VLM_SEMANTIC_BLOCKS)
        state["extraction_report"] = {
            "semantic_source": semantic_source,
            "document_ir": {"blocks": [], "translation_block_ids": []},
        }

        assert core._source_quality_gate_enabled(state) is False
        report = core._audit_source_quality_for_state(state)
        assert report["status"] == "disabled"
        assert report["reason"] == "vlm_semantic_source"
        assert report["flags"] == []
        assert report["flagged_count"] == 0


def test_source_quality_gate_still_blocks_ocr_pdf_without_vlm_provenance():
    # Same blocks, same PDF filename: without VLM provenance the OCR gate must
    # keep firing (fail closed for legacy/OCR states).
    state = core.new_job_state("scan.pdf")
    state["paras"] = list(_VLM_SEMANTIC_BLOCKS)

    assert core._source_quality_gate_enabled(state) is True
    report = core._audit_source_quality_for_state(state)
    assert report["status"] == "blocked"
    assert {item["segment_index"] for item in report["flags"]} >= {0, 1, 2}


def test_vlm_semantic_blocks_are_translated_not_quarantined(tmp_path: Path):
    """A VLM PDF must not hold its own trusted blocks back for source review."""
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        if "翻译流知识抽取器" in system:
            return "[]"
        return json.dumps(["已翻译。"], ensure_ascii=False)

    core.call_llm = llm
    try:
        state = core.new_job_state("scan.pdf")
        state["paras"] = list(_VLM_SEMANTIC_BLOCKS)
        state["extraction_report"] = {"semantic_source": "vlm"}
        result = core.translate_stage(
            state, "vlm-source-quality-gate", [], "DeepSeek", "key", "model",
            "简体中文", "", enable_review=False, use_tm=False,
        )

        assert result["source_quality_gate"]["status"] == "disabled"
        assert result["source_quality_gate"]["flags"] == []
        assert [pair["target_provenance"] for pair in result["pairs"]] == [
            "generated", "generated", "generated", "generated",
        ]
        assert not [
            item for item in result["findings"]
            if item.get("type") == "source_quality_gate"
        ]
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_source_quality_gate_quarantines_p16_illustration_garbage():
    paragraphs = [
        "Nineteen Years Later",
        "eth Se 5th",
        "ie oy a oy ipennaa. aan in.",
        "Teepe om",
        "Ort eae” —",
        ":",
        "na",
        "TS",
        "Mn",
        "5p",
        "Otte.)",
        "2a",
        "rae",
        "ve",
        "The Man at the Door",
    ]
    report = core.audit_source_quality(paragraphs)

    assert report["status"] == "blocked"
    flagged_indices = [item["segment_index"] for item in report["flags"]]
    assert 0 not in flagged_indices
    assert 14 not in flagged_indices
    for i in [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13]:
        assert i in flagged_indices
    assert len(report["clusters"]) == 1
    assert report["clusters"][0] == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13]


def test_source_quality_gate_does_not_block_spaced_en_dash_sentences():
    paragraphs = [
        "And then – a runny nose.",
        "One man – Lech Wałęsa.",
        "And now, there it was – the lake.",
        "Yes, yes – my part of the deal.",
    ]
    report = core.audit_source_quality(paragraphs)
    assert report["status"] == "passed"
    assert report["flagged_count"] == 0


def test_source_quality_gate_does_not_block_rejoined_poem_lines():
    raw_lines = [
        "It was summer, or its end, and I heard then",
        "your footsteps, as you walked from east to west",
        "for the last time. And in the world, forgotten",
        "were kerchiefs, and books, and people.",
    ]
    cleaned = (
        "It was summer, or its end, and I heard then your footsteps, "
        "as you walked from east to west for the last time. "
        "And in the world, forgotten were kerchiefs, and books, and people."
    )
    segments = [
        "It was summer, or its end, and I heard then your footsteps, as you walked from east to west for the last time.",
        "And in the world, forgotten were kerchiefs, and books, and people.",
    ]
    state = core.new_job_state("poem.pdf")
    state.update(
        source_paras=raw_lines,
        source_paragraphs=[cleaned],
        paras=segments,
        source_cleanup={"output_input_indices": [list(range(len(raw_lines)))]},
        segmentation={"paragraph_ranges": [{"paragraph_index": 0, "start": 0, "end": 2}]},
    )
    report = core._audit_source_quality_for_state(state)
    assert report["status"] == "passed"
    assert report["flags"] == []


def test_source_quality_gate_does_not_block_copula_titles():
    paragraphs = [
        "Playtime is Over",
        "The War is Not Won",
        "All Hope is Gone",
    ]
    report = core.audit_source_quality(paragraphs)
    assert report["status"] == "passed"
    assert report["flagged_count"] == 0


def test_source_quality_gate_does_not_block_verse_continuation():
    stanza = [
        "Like rain in many places",
        "from many clouds,",
        "to be drunk by many mouths,",
        "to be breathed like air during the year and scattered like the bloom of spring.",
    ]
    report = core.audit_source_quality(stanza)
    assert report["status"] == "passed"
    assert report["flagged_count"] == 0


def test_source_quality_gate_does_not_block_single_word_wrapped_lines_in_sentence():
    raw_lines = [
        "The",
        "Air",
        "Force",
        "Commander",
        "accepted",
        "the",
        "wing",
        "commander’s recommendation and appointed me as the commander of Squadron 117.",
    ]
    cleaned = "The Air Force Commander accepted the wing commander’s recommendation and appointed me as the commander of Squadron 117."
    state = core.new_job_state("doc.pdf")
    state.update(
        source_paras=raw_lines,
        source_paragraphs=[cleaned],
        paras=[cleaned],
        source_cleanup={"output_input_indices": [list(range(len(raw_lines)))]},
        segmentation={"paragraph_ranges": [{"paragraph_index": 0, "start": 0, "end": 1}]},
    )
    report = core._audit_source_quality_for_state(state)
    assert report["status"] == "passed"
    assert report["flags"] == []


def test_source_quality_gate_does_not_block_model_designations():
    paragraphs = [
        "W-9",
        "U-2",
        "B-52",
        "AK-47",
        "MiG-21",
    ]
    report = core.audit_source_quality(paragraphs)
    assert report["status"] == "passed"
    assert report["flagged_count"] == 0
