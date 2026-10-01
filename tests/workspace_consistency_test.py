"""Interaction and truthful evidence checks for the consistent workspace UI."""
from pathlib import Path

import core
from folith.ui.workspace.presentation import term_rows, review_evidence


def test_term_counts_match_current_source_with_word_boundaries():
    state = {"glossary": [{"id": "ai", "source": "AI", "preferred": "人工智能", "status": "provisional"}],
             "pairs": [{"source": "AI systems", "target": "人工智能系统"},
                       {"source": "MAIN systems", "target": "主体系统"},
                       {"source": "AI tools", "target": "智能工具"}]}
    row = term_rows(state)[0]
    assert row["occurrences"] == [0, 2]
    assert row["matched"] == 1
    assert row["exception"] == ""


def test_review_evidence_distinguishes_recorded_variants_from_current_translation():
    state = {"pairs": [{"source": "Air Mobility", "target": "当前译名"}],
             "entity_registry": [{"source_form": "Air Mobility", "observations": [
                 {"segment_id": 0, "target": "检查时译名"}]}]}
    evidence = review_evidence(state, {"detected_text": "Air Mobility", "segment_id": 0})
    assert evidence == [{"index": 0, "source": "Air Mobility", "target": "检查时译名", "recorded": True}]
    assert review_evidence(state, {"segment_id": 0})[0]["target"] == "当前译名"


def test_select_and_confirm_term_changes_only_selected_term(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "consistencyterms"
    state = core.new_job_state("terms.docx")
    state.update(p1_done=True, p2_done=True, paras=["Germany"],
                 pairs=[{"source": "Germany", "target": "德国"}],
                 glossary=[{"id": "de", "source": "Germany", "preferred": "德国", "status": "provisional"},
                           {"id": "fr", "source": "France", "preferred": "法国", "status": "provisional"}])
    core.save_job_state(job_id, state)
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    for key, value in dict(active_job_id=job_id, app_view="workspace", workspace_mode=True, workspace_section="terms").items():
        at.session_state[key] = value
    at.run()
    assert not at.exception
    assert any("选择一个术语" in e.value for e in at.markdown)
    next(b for b in at.button if b.key == f"workspace_term_select_{job_id}_0").click().run()
    assert not at.exception
    assert any("1 / 1 段包含首选译名" in e.value for e in at.markdown)
    next(b for b in at.button if b.label == "确认译名").click().run()
    assert not at.exception
    updated = core.load_job_state(job_id)
    assert [e["status"] for e in updated["glossary"]] == ["locked", "provisional"]
    assert updated["pairs"][0]["target"] == "德国"
