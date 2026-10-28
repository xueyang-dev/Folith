import copy
import pytest

import core
from folith import outline_corrections as corrections
from folith import source_outline


def outline_with_titles(first="One"):
    paragraphs = [first, "A", "Two", "B"]
    nodes = [source_outline._node(i, kind="chapter", title=title, level=1,
                                para_start=start, paragraphs=paragraphs[start:start + 2],
                                provenance="native_heading")
             for i, (title, start) in enumerate(((first, 0), ("Two", 2)))]
    return source_outline._finish({"nodes": nodes, "paragraphs": paragraphs})


def test_corrections_persist_without_mutating_source_or_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "correct00001"
    source = b"# First\n\nFirst body\n\n# Second\n\nSecond body\n"
    core.save_source(job, source)
    outline = outline_with_titles()
    original = copy.deepcopy(outline)
    first, second = (n["id"] for n in outline["nodes"])
    state = core.new_job_state("book.md")
    state.update(pairs=[{"source": "a", "target": "translated"}],
                 paras=["a"], excluded_segments=[{"source": "b"}])
    core.save_job_state(job, state)
    state_before = copy.deepcopy(core.load_job_state(job))
    corrections.save(job, outline, first, title="Renamed", level=2)
    corrections.save(job, outline, second, delete=True)
    effective, dropped = corrections.apply(job, outline)
    assert dropped == 0
    assert len(effective["nodes"]) == 1
    assert effective["nodes"][0]["title"] == "Renamed"
    assert effective["nodes"][0]["provenance"] == "manual"
    assert corrections.apply(job, outline)[0] == effective
    assert core.load_job_state(job) == state_before
    assert core.load_source(job) == source and outline == original
    corrections.clear(job)
    assert corrections.apply(job, outline) == (original, 0)


def test_changed_outline_never_matches_ambiguous_or_missing_title(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    old = outline_with_titles()
    corrections.save("correct00002", old, old["nodes"][0]["id"], title="New")
    changed = outline_with_titles("Other")
    result, dropped = corrections.apply("correct00002", changed)
    assert dropped == 1 and result == changed


def test_boundary_moves_recompute_navigation_and_never_touch_exclusion_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    outline = outline_with_titles()
    job = "correct-boundary"
    state = core.new_job_state("book.txt")
    state["scope_exclusion_plan"] = {"entries": [{"para_start": 2, "para_end": 4,
                                                   "anchor_first": "Two", "anchor_last": "B"}]}
    core.save_job_state(job, state)
    before = core.load_job_state(job)
    corrections.save(job, outline, "n0001", para_start=1)
    result, dropped = corrections.apply(job, outline)
    assert dropped == 0
    assert [(n["para_start"], n["para_count"]) for n in result["nodes"]] == [(0, 1), (1, 3)]
    assert core.load_job_state(job) == before
    with pytest.raises(ValueError):
        corrections.save(job, outline, "n0001", para_start=0)
    assert corrections.apply(job, outline)[0] == result


def test_duplicate_original_title_is_not_guessed_after_reparse(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    original = outline_with_titles()
    original["nodes"][1]["title"] = "One"
    original["fingerprint"] = source_outline.outline_fingerprint(original)
    corrections.save("duplicate-title", original, "n0000", title="Edited")
    changed = outline_with_titles()
    effective, dropped = corrections.apply("duplicate-title", changed)
    assert dropped == 1 and effective == changed


def test_reparse_rejects_same_title_with_different_first_paragraph(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    original = outline_with_titles()
    original["nodes"][0]["title"] = "Introduction"
    original["fingerprint"] = source_outline.outline_fingerprint(original)
    corrections.save("same-title-new-content", original, "n0000", title="Edited")
    changed = outline_with_titles("Different text")
    changed["nodes"][0]["title"] = "Introduction"
    changed["fingerprint"] = source_outline.outline_fingerprint(changed)
    effective, dropped = corrections.apply("same-title-new-content", changed)
    assert dropped == 1 and effective == changed


def test_reparse_retains_correction_when_title_and_first_paragraph_match(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    original = outline_with_titles()
    corrections.save("same-content-reparse", original, "n0000", title="Edited")
    changed = copy.deepcopy(original)
    changed["nodes"][1]["level"] = 2
    changed["nodes"][1]["title"] = "A changed heading"
    changed["fingerprint"] = source_outline.outline_fingerprint(changed)
    effective, dropped = corrections.apply("same-content-reparse", changed)
    assert dropped == 0 and effective["nodes"][0]["title"] == "Edited"


def test_saving_new_correction_preserves_unmatched_old_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "unmatched-preserved"
    old = outline_with_titles()
    corrections.save(job, old, "n0000", title="Old edit")
    changed = outline_with_titles("Different")
    corrections.save(job, changed, "n0001", title="Second edit")
    effective, dropped = corrections.apply(job, changed)
    assert dropped == 1
    assert [n["title"] for n in effective["nodes"]] == ["Different", "Second edit"]
    saved = corrections._read(job)
    assert len(saved["entries"]) == 2
    assert any(e.get("title") == "Old edit" and e.get("unmatched") for e in saved["entries"])
    corrections.save(job, changed, "n0000", title="New edit")
    effective, dropped = corrections.apply(job, changed)
    assert dropped == 1
    assert [n["title"] for n in effective["nodes"]] == ["New edit", "Second edit"]


def test_changed_boundary_text_is_not_applied_with_same_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "changed-boundary"
    original = outline_with_titles()
    corrections.save(job, original, "n0001", para_start=1)
    changed = copy.deepcopy(original)
    changed["paragraphs"][1] = "Replacement"
    assert changed["fingerprint"] == original["fingerprint"]
    effective, dropped = corrections.apply(job, changed)
    assert dropped == 1 and effective == changed
    corrections.save(job, changed, "n0000", title="Other edit")
    effective, dropped = corrections.apply(job, changed)
    assert dropped == 1
    assert effective["nodes"][0]["title"] == "Other edit"
    assert effective["nodes"][1]["para_start"] == 2


def test_duplicate_boundary_text_is_rejected_before_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    outline = outline_with_titles()
    outline["paragraphs"][3] = outline["paragraphs"][1]
    with pytest.raises(ValueError, match="must be unique"):
        corrections.save("ambiguous-boundary", outline, "n0001", para_start=1)
    assert not corrections.has_corrections("ambiguous-boundary")
