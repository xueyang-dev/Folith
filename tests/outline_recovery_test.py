import json
import io
from types import SimpleNamespace
import core
import pytest
from folith import outline_recovery, source_outline


def _outline(paragraphs, provenance="page_group"):
    node = source_outline._node(0, kind="group", title="Full text", level=0,
                                paragraphs=paragraphs, provenance=provenance)
    return source_outline._finish({"nodes": [node], "paragraphs": paragraphs})


def test_numbering_recovery_is_explicit_and_survives_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    original = _outline(["Introduction", "Chapter 1 Beginning", "Some text.",
                         "1.1 Details", "More text.", "Chapter 2 Ending", "End."])
    recovered = outline_recovery.recover(original)
    assert [n["title"] for n in recovered["nodes"]][1:] == [
        "Chapter 1 Beginning", "1.1 Details", "Chapter 2 Ending"]
    assert [n["level"] for n in recovered["nodes"]][1:] == [1, 2, 1]
    assert all(n["provenance"] == "rule_numbering" for n in recovered["nodes"][1:])
    assert original["nodes"][0]["provenance"] == "page_group"
    outline_recovery.save("recovery0001", original, recovered)
    assert outline_recovery.load("recovery0001", original) == recovered
    assert outline_recovery.load("recovery0001", _outline(["new source"])) != recovered
    changed = _outline(["Introduction", "Chapter 1 Beginning", "Some text?",
                        "1.1 Details", "More text.", "Chapter 2 Ending", "End."])
    assert changed["fingerprint"] == original["fingerprint"]
    assert outline_recovery.load("recovery0001", changed) == changed
    outline_recovery.reject("recovery0001")
    assert outline_recovery.load("recovery0001", original) == original


def test_native_headings_are_never_replaced():
    original = _outline(["Chapter 1 Beginning", "Body"], "native_heading_style")
    assert outline_recovery.recover(original) == original


def test_rules_reject_repeated_headers_and_adjacent_numbered_list_items():
    original = _outline(["RUNNING HEADER", "1. First", "2. Second", "RUNNING HEADER", "body"])
    original["paragraph_roles"] = ["header", "body", "body", "header", "body"]
    assert not outline_recovery.candidates(original)


def test_unstyled_docx_recovers_its_own_numbered_titles():
    from docx import Document
    document = Document()
    for text in ("Chapter 1 Arrival", "The first section has body text.",
                 "Chapter 2 Departure", "The second section has body text."):
        document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    original = source_outline.parse_source_outline("book.docx", output.getvalue())
    recovered = outline_recovery.recover(original)
    assert [n["title"] for n in recovered["nodes"]] == ["Chapter 1 Arrival", "Chapter 2 Departure"]
    assert all(n["provenance"] == "rule_numbering" for n in recovered["nodes"])


def test_pdf_without_bookmarks_recovers_numbered_text_layer():
    import fitz
    document = fitz.open()
    for heading in ("Chapter 1 Arrival", "Chapter 2 Departure"):
        page = document.new_page()
        page.insert_text((72, 80), heading, fontsize=16)
        page.insert_text((72, 130),
                         "This chapter contains enough ordinary body text to provide a useful paragraph.",
                         fontsize=10)
    data = document.tobytes()
    document.close()
    original = source_outline.parse_source_outline("book.pdf", data)
    recovered = outline_recovery.recover(original)
    assert [n["title"] for n in recovered["nodes"]] == ["Chapter 1 Arrival", "Chapter 2 Departure"]
    assert all(n["provenance"] == "rule_numbering" for n in recovered["nodes"])


def test_ai_candidates_are_bounded_and_titles_come_only_from_source(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    original = _outline(["Chapter 1 Existing", "x" * 10000, "Chapter 2 Existing", "End"])
    prompt, rows = outline_recovery.ai_prompt(original)
    assert len(prompt) + len(outline_recovery.AI_SYSTEM) <= outline_recovery.MAX_AI_INPUT_CHARS
    answer = json.dumps([{"index": rows[0]["index"], "level": 1, "confidence": 0.8,
                          "title": "Invented"},
                         {"index": 9999, "level": 1, "confidence": 1}])
    result = outline_recovery.from_ai(original, rows, answer)
    heading = next(n for n in result["nodes"] if n["provenance"] == "ai_inferred")
    assert heading["title"] == "Chapter 1 Existing" and heading["confidence"] == 0.8
    with pytest.raises(ValueError):
        outline_recovery.from_ai(original, rows, '[{"index":9999,"level":1,"confidence":1}]')
    previous = outline_recovery.recover(original)
    outline_recovery.save("recovery-ai", original, result, previous=previous)
    outline_recovery.reject("recovery-ai", original)
    assert outline_recovery.load("recovery-ai", original) == previous
    assert outline_recovery.ai_rejected("recovery-ai", original)
    assert not outline_recovery.ai_rejected("recovery-ai", _outline(["different"]))


def test_ai_failure_keeps_previous_outline_and_records_attempt(tmp_path, monkeypatch):
    from folith import outline_anchor
    from folith.qt import adapter

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "recovery-failure"
    source = b"source bytes"
    original = _outline(["Chapter 1 First", "body", "Chapter 2 Second", "end"])
    core.save_job_state(job, core.new_job_state("book.txt"))
    core.save_source(job, source)
    outline_anchor.store_outline(job, original, data=source)
    previous = outline_recovery.recover(original)
    outline_recovery.save(job, original, previous)
    config = SimpleNamespace(connection_problem=lambda: "", provider="test", api_key="key",
                             model="test", translator_base_url=lambda: "")

    def fail(*args, **kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(core, "call_llm", fail)
    with pytest.raises(TimeoutError):
        adapter.analyze_outline_recovery(job, config, original["fingerprint"])
    assert outline_recovery.load(job, original) == previous
    record = core.load_job_state(job)["outline_recovery_analysis"]
    assert record["calls"] == 1
    assert record["input_characters"] <= record["max_input_characters"]


def test_native_outline_and_normal_load_never_call_model(tmp_path, monkeypatch):
    from folith import outline_anchor
    from folith.qt import adapter
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "recovery-native"
    original = _outline(["Chapter 1 First", "body"], "native_heading_style")
    core.save_job_state(job, core.new_job_state("book.txt"))
    core.save_source(job, b"source bytes")
    outline_anchor.store_outline(job, original, data=b"source bytes")
    def fail_if_called(*args, **kwargs):
        raise AssertionError("unexpected model call")
    monkeypatch.setattr(core, "call_llm", fail_if_called)
    assert adapter.task_outline(job)["outline"] == original
    config = SimpleNamespace(connection_problem=lambda: "", provider="test", api_key="key",
                             model="test", translator_base_url=lambda: "")
    with pytest.raises(adapter.AdapterError):
        adapter.analyze_outline_recovery(job, config, original["fingerprint"])
