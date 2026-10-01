"""LLM OCR source cleanup contract and pipeline checkpoint tests."""

import json
from pathlib import Path

import fitz

import core


def test_cleanup_repairs_ocr_and_reflows_without_changing_item_order():
    calls = []

    def llm(provider, api_key, model, system, user, **kwargs):
        calls.append((system, user, kwargs))
        return json.dumps({
            "segments": [
                {"index": 0, "text": "The: \"Midnight", "break_after": False},
                {"index": 1, "text": "Library —", "break_after": False},
                {"index": 2, "text": "Infinite lives.", "break_after": True},
                {"index": 3, "text": "Nora Seed finds herself.", "break_after": True},
            ]
        })

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["The: “Midnacht", "Library —", "Infinite", "Nora Seed finds herself."],
        "DeepSeek", "key", "model", call_llm_fn=llm,
    )

    assert warnings == []
    assert cleaned == ['The: "Midnight Library — Infinite lives.',
                       "Nora Seed finds herself."]
    assert metadata["status"] == "completed"
    assert metadata["input_count"] == 4
    assert metadata["output_count"] == 2
    assert metadata["changed_segments"][0]["after"] == 'The: "Midnight'
    assert metadata["paragraph_merges"][0]["input_indices"] == [0, 1, 2]
    assert calls and calls[0][2]["response_format"]["json_schema"]["name"] == \
        "foliothread_source_cleanup"


def test_cleanup_failed_batch_keeps_original_source():
    def llm(*args, **kwargs):
        return "not json"

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["The: “Midnacht", "Library —"],
        "DeepSeek", "key", "model", call_llm_fn=llm,
    )

    assert cleaned == ["The: “Midnacht", "Library —"]
    assert metadata["status"] == "partial"
    assert metadata["failed_batches"] == 1
    assert len(warnings) == 1 and "已保留提取文本" in warnings[0]


def test_cleanup_can_join_across_batch_boundary():
    calls = {"count": 0}

    def llm(provider, api_key, model, system, user, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps({"segments": [
                {"index": 0, "text": "A sentence", "break_after": False},
                {"index": 1, "text": "continues", "break_after": False},
            ]})
        return json.dumps({"segments": [
            {"index": 0, "text": "here.", "break_after": True},
        ]})

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["A sentence", "continues", "here."],
        "DeepSeek", "key", "model", call_llm_fn=llm,
        max_batch_items=2,
    )

    assert warnings == []
    assert cleaned == ["A sentence continues here."]
    assert metadata["paragraph_merges"][0]["input_indices"] == [0, 1, 2]


def test_pdf_pipeline_persists_cleaned_source_before_translation(tmp_path: Path,
                                                                 monkeypatch):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    calls = {"cleanup": 0, "translation": 0}

    def llm(provider, api_key, model, system, user, **kwargs):
        if "OCR 纠错" in system:
            calls["cleanup"] += 1
            return json.dumps({
                "segments": [
                    {"index": 0, "text": "The: \"Midnight", "break_after": False},
                    {"index": 1, "text": "Library.", "break_after": True},
                ]
            })
        calls["translation"] += 1
        return json.dumps(["午夜图书馆。"])

    monkeypatch.setattr(core, "extract_pdf_paragraphs",
                        lambda _bytes: ["The: “Midnacht", "Library."])
    document = fitz.open()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()
    core.call_llm = llm
    try:
        state = core.run_job_pipeline(
            "source-cleanup-pipeline", "scan.pdf", pdf_bytes,
            provider="DeepSeek", api_key="key", model="model",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], enable_review=False,
            enable_annotate=False, use_tm=False, enable_understanding=False,
            enable_source_cleanup=True,
        )
        assert calls["cleanup"] == 1
        assert state["source_cleanup_done"] is True
        assert state["source_paras"] == ["The: “Midnacht", "Library."]
        assert state["paras"] == ['The: "Midnight Library.']
        assert state["pairs"][0]["source"] == 'The: "Midnight Library.'
        performance = json.loads(
            (tmp_path / "source-cleanup-pipeline" / "performance.json")
            .read_text(encoding="utf-8")
        )
        assert set(("pdf_classify", "layout_recovery", "rasterize", "ocr",
                    "deterministic_cleanup", "llm_cleanup", "segment_build")) \
            <= set(performance["stages"])
        assert performance["stages"]["llm_cleanup"]["batch_count"] == 1
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_pdf_pipeline_translates_sentence_units_but_keeps_cleaned_paragraph(
        tmp_path: Path, monkeypatch):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model, system, user, **kwargs):
        if "OCR 纠错" in system:
            return json.dumps({
                "segments": [
                    {"index": 0,
                     "text": "The first sentence. The second sentence.",
                     "break_after": True},
                ]
            })
        return json.dumps(["第一句。", "第二句。"])

    monkeypatch.setattr(core, "extract_pdf_paragraphs",
                        lambda _bytes: ["The first sentence. The second sentence."])
    document = fitz.open()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()
    core.call_llm = llm
    try:
        state = core.run_job_pipeline(
            "sentence-unit-pipeline", "scan.pdf", pdf_bytes,
            provider="DeepSeek", api_key="key", model="model",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], enable_review=False,
            enable_annotate=False, use_tm=False, enable_understanding=False,
            enable_source_cleanup=True,
        )
        assert state["source_paragraphs"] == [
            "The first sentence. The second sentence."
        ]
        assert state["paras"] == ["The first sentence.", "The second sentence."]
        assert [pair["source"] for pair in state["pairs"]] == state["paras"]
        assert [pair["target"] for pair in state["pairs"]] == ["第一句。", "第二句。"]
        assert state["segmentation"]["input_paragraph_count"] == 1
        assert state["segmentation"]["output_segment_count"] == 2
        assert state["source_cleanup"]["paragraph_count"] == 1
        assert state["source_cleanup"]["segment_count"] == 2
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_legacy_partial_pdf_is_rebased_before_resume(tmp_path: Path, monkeypatch):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    monkeypatch.setattr(core, "extract_pdf_paragraphs",
                        lambda _bytes: ["The: “Midnacht", "Library."])

    def llm(provider, api_key, model, system, user, **kwargs):
        if "OCR 纠错" in system:
            return json.dumps({
                "segments": [
                    {"index": 0, "text": "The: \"Midnight", "break_after": False},
                    {"index": 1, "text": "Library.", "break_after": True},
                ]
            })
        return json.dumps(["新的译文。"])

    document = fitz.open()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()
    state = core.new_job_state("legacy.pdf")
    state.update(
        p1_done=True,
        p2_done=False,
        paras=["The: “Midnacht", "Library."],
        pairs=[{"source": "The: “Midnacht", "target": "旧译文"}],
    )
    core.save_job_state("legacy-source-cleanup", state)
    core.call_llm = llm
    try:
        result = core.run_job_pipeline(
            "legacy-source-cleanup", "legacy.pdf", pdf_bytes,
            provider="DeepSeek", api_key="key", model="model",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], enable_review=False,
            enable_annotate=False, use_tm=False, enable_understanding=False,
        )
        assert result["source_cleanup_done"] is True
        assert result["source_cleanup"]["previous_pair_count"] == 1
        assert result["pairs"][0]["source"] == 'The: "Midnight Library.'
        assert result["pairs"][0]["target"] == "新的译文。"
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_cleanup_waits_for_all_parallel_batches():
    import time

    def slow_llm(provider, api_key, model, system, user, **kwargs):
        time.sleep(0.05)
        # Parse user prompt to find number of items
        lines = [line for line in user.splitlines() if line and line[0].isdigit()]
        segments = [{"index": i, "text": f"Cleaned {i}", "break_after": True}
                    for i in range(len(lines))]
        return json.dumps({"segments": segments})

    # 10 paragraphs, 1 per batch => 10 batches
    paras = [f"Raw paragraph {i}" for i in range(10)]
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paras, "DeepSeek", "key", "model",
        call_llm_fn=slow_llm,
        max_batch_items=1,
        parallelism=3,
    )
    assert metadata["status"] == "completed"
    assert metadata["batch_count"] == 10
    assert metadata["failed_batches"] == 0
    assert len(cleaned) == 10
    assert all("Cleaned" in text for text in cleaned)


def test_cleanup_handles_none_or_missing_rows_safely(monkeypatch):
    from folith import source_cleanup

    # Even if result_map has missing batches or rows is None, it should not crash
    batches = [[(0, "Line 0"), (1, "Line 1")]]
    result_map = {0: {"status": "failed", "rows": None, "error": None}}

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["Line 0", "Line 1"], "DeepSeek", "key", "model",
        call_llm_fn=lambda *a, **k: "invalid",
    )
    assert cleaned == ["Line 0", "Line 1"]
    assert metadata["failed_batches"] == 1
    assert any("已保留提取文本" in w for w in warnings)


def test_cleanup_drops_a_stray_symbol_line_instead_of_gluing_the_period():
    """文本层把句点吐成独立一行：不得产出 ``…in the. Midnight Library…``。

    模型在这里是**故意敌对**的：它把孤立句点粘回 ``the`` 之后并声明两行同段，
    正是修复前真实发生过的行为。确定性层必须让它无法把假句末交出去。
    """
    seen = []

    def llm(provider, api_key, model, system, user, **kwargs):
        seen.append(user)
        return json.dumps({"segments": [
            {"index": 0, "text": "When Nora Seed finds herself in the.",
             "break_after": False},
            {"index": 1, "text":
                "Midnight Library, she has a chance to make things right.",
             "break_after": True},
        ]})

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["ca When Nora Seed finds herself in the", ".",
         "_ Midnight Library, she has a chance to make things right."],
        "DeepSeek", "key", "model", call_llm_fn=llm, parallelism=1,
    )

    assert warnings == []
    assert cleaned == ["When Nora Seed finds herself in the Midnight Library, "
                       "she has a chance to make things right."]
    assert "the." not in cleaned[0]
    stray = [item for item in metadata["changed_segments"]
             if item.get("reason") == "stray_punctuation_fragment"]
    assert sorted(item["index"] for item in stray) == [0, 1]
    fragment = next(item for item in stray if item["index"] == 1)
    assert fragment["before"] == "." and fragment["after"] == ""
    assert [item for item in metadata["deterministic_merges"]
            if item.get("reason") == "stray_punctuation_fragment"] == [
        {"input_indices": [0, 1, 2], "reason": "stray_punctuation_fragment"}]
    # 噪声行没有可修复的正文：不送模型，模型也无权把它填回来
    assert metadata["llm_input_count"] == 2
    assert "1. ." not in seen[0]


def test_cleanup_keeps_symbol_lines_that_are_not_stray_fragments():
    """前一行已经结句时，孤立符号行是正常内容，必须原样保留。"""

    def llm(provider, api_key, model, system, user, **kwargs):
        return json.dumps({"segments": [
            {"index": 0, "text": "The chapter ends here.", "break_after": True},
            {"index": 1, "text": "©", "break_after": True},
            {"index": 2, "text": "Next sentence.", "break_after": True},
        ]})

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["The chapter ends here.", "©", "Next sentence."],
        "DeepSeek", "key", "model", call_llm_fn=llm, parallelism=1,
    )

    assert warnings == []
    assert cleaned == ["The chapter ends here.", "©", "Next sentence."]
    assert not [item for item in metadata["changed_segments"]
                if item.get("reason") == "stray_punctuation_fragment"]


def test_cleanup_does_not_treat_a_colon_after_a_quote_as_a_stray_fragment():
    """``Neil’: ‘You went…`` 里的冒号是真标点，删掉就是把原文改了。"""

    def llm(provider, api_key, model, system, user, **kwargs):
        return json.dumps({"segments": [
            {"index": 0, "text": "sophers in Bedford, if I’m honest, Neil’",
             "break_after": True},
            {"index": 1, "text": ":", "break_after": True},
            {"index": 2, "text": "‘You went to uni, had a year in London.",
             "break_after": True},
        ]})

    cleaned, metadata, _warnings = core.cleanup_source_paragraphs(
        ["sophers in Bedford, if I’m honest, Neil’", ":",
         "‘You went to uni, had a year in London."],
        "DeepSeek", "key", "model", call_llm_fn=llm, parallelism=1,
    )

    assert cleaned == ["sophers in Bedford, if I’m honest, Neil’", ":",
                       "‘You went to uni, had a year in London."]
    assert not [item for item in metadata["changed_segments"]
                if item.get("reason") == "stray_punctuation_fragment"]


def test_resume_stage1_with_existing_source_paras_without_file_bytes(tmp_path: Path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model, system, user, **kwargs):
        if "OCR 纠错" in system:
            return json.dumps({
                "segments": [
                    {"index": 0, "text": "Cleaned paragraph 1.", "break_after": True},
                    {"index": 1, "text": "Cleaned paragraph 2.", "break_after": True},
                ]
            })
        return "译文。"

    job_id = "resume-no-file-bytes"
    state = core.new_job_state("test.pdf")
    state.update({
        "p1_done": False,
        "source_paras": ["Raw para 1", "Raw para 2"],
        "extraction_report": {
            "version": 1,
            "format": "pdf",
            "extracted": {"pages": 12, "paragraphs": 2},
        },
    })
    core.save_job_state(job_id, state)
    core.call_llm = llm
    try:
        # file_bytes is None, and source.bin does not exist on disk
        result = core.run_job_pipeline(
            job_id, "test.pdf", None,
            provider="DeepSeek", api_key="key", model="model",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], enable_review=False,
            enable_annotate=False, use_tm=False, enable_understanding=False,
            enable_source_cleanup=True,
        )
        assert result["p1_done"] is True
        assert result["source_page_count"] == 12
        assert result["source_paras"] == ["Raw para 1", "Raw para 2"]
        assert len(result["paras"]) == 2
        assert result["paras"][0] == "Cleaned paragraph 1."
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
