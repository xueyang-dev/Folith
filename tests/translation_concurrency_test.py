import json
import time
from pathlib import Path

import core


def test_translate_stage_concurrent_execution(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    paragraphs = [f"Paragraph number {i} with some content." for i in range(12)]
    job_id = "test-concurrent-job-01"
    state = core.new_job_state("doc.txt")
    state["paras"] = paragraphs
    core.save_job_state(job_id, state)

    call_order = []

    def mock_llm(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        # Simulate small delay
        time.sleep(0.02)
        # Find which items were sent in user_prompt
        # Numbered format: "1. Paragraph number X..."
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = []
        for line in lines:
            parts = line.split(". ", 1)
            text = parts[1] if len(parts) > 1 else line
            results.append(f"译文: {text}")
        call_order.append(len(results))
        return json.dumps(results)

    monkeypatch.setattr(core, "call_llm", mock_llm)

    result = core.translate_stage(
        state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang="简体中文", style_rules="", enable_review=False,
        use_tm=False, batch_size=2, translation_concurrency=3,
    )

    assert result["batch_plan"]["concurrency"] == 3
    assert len(result["pairs"]) == 12
    # Verify strict document order
    for i, pair in enumerate(result["pairs"]):
        assert pair["source"] == paragraphs[i]
        assert pair["target"] == f"译文: {paragraphs[i]}"

    # Verify checkpoint events
    events = core._checkpoint.read_events(core.job_dir(job_id))
    state_commit_events = [e for e in events if e.get("phase") == "state_commit_done"]
    assert len(state_commit_events) == 6
    for idx, e in enumerate(state_commit_events):
        assert e["batch"] == idx


def test_translate_stage_serial_execution(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    paragraphs = [f"Item {i}" for i in range(6)]
    job_id = "test-serial-job-01"
    state = core.new_job_state("doc.txt")
    state["paras"] = paragraphs
    core.save_job_state(job_id, state)

    def mock_llm(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = [f"T: {line.split('. ', 1)[1]}" for line in lines]
        return json.dumps(results)

    monkeypatch.setattr(core, "call_llm", mock_llm)

    result = core.translate_stage(
        state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang="简体中文", style_rules="", enable_review=False,
        use_tm=False, batch_size=2, translation_concurrency=1,
    )

    assert result["batch_plan"]["concurrency"] == 1
    assert len(result["pairs"]) == 6
    for i, pair in enumerate(result["pairs"]):
        assert pair["source"] == paragraphs[i]
        assert pair["target"] == f"T: {paragraphs[i]}"


def test_paragraph_segmentation_mode_preserves_full_paragraphs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    raw_paragraphs = [
        "First sentence. Second sentence in same paragraph.",
        "Another sentence here. And one more.",
    ]
    job_id = "test-para-seg-01"

    monkeypatch.setattr(
        core, "extract_document_paragraphs_with_report",
        lambda *args, **kwargs: (raw_paragraphs, [], {})
    )

    def mock_llm(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = [f"译: {line.split('. ', 1)[1]}" for line in lines]
        return json.dumps(results)

    monkeypatch.setattr(core, "call_llm", mock_llm)

    state = core.run_job_pipeline(
        job_id, "test.pdf", b"%PDF-1.4",
        provider="DeepSeek", api_key="k", model="m",
        target_lang="简体中文", auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], enable_review=False,
        enable_annotate=False, use_tm=False, enable_understanding=False,
        enable_source_cleanup=False,
        segmentation_mode="paragraph",
        translation_concurrency=2,
    )

    # In paragraph mode, each raw paragraph is kept intact without splitting sentences
    assert state["pipeline_config"]["segmentation_mode"] == "paragraph"
    assert state["pipeline_config"]["translation_concurrency"] == 2
    assert state["segmentation"]["mode"] == "paragraph"
    assert state["paras"] == raw_paragraphs
    assert len(state["pairs"]) == 2
    assert state["pairs"][0]["source"] == raw_paragraphs[0]
