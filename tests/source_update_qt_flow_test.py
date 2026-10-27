"""Source revision through the desktop adapter and real task state."""
import pytest
import core
from tests.source_update_helpers import source_args
from folith.qt import adapter


def test_preview_apply_translate_and_deliver_text(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "source-update-desktop"
    state = core.new_job_state("original.txt")
    state.update(p1_done=True, p2_done=True, target_lang="简体中文",
        segmentation_mode="paragraph",
        paras=["This paragraph is unchanged.", "This paragraph is the original version."],
        pairs=[{"source": "This paragraph is unchanged.", "target": "不变的译文。",
                "reviewed": True, "segment_uid": "s1"},
               {"source": "This paragraph is the original version.", "target": "旧译文。",
                "reviewed": False, "segment_uid": "s2"}])
    state["delivery_config"] = core.normalize_delivery_config({"deliver_plain_docx": True})
    core.save_source(job, b"old source")
    core.save_job_state(job, state)
    path = tmp_path / "revised.txt"
    path.write_text("This paragraph is unchanged.\n\nThis paragraph is the revised version.", encoding="utf-8")
    payload = adapter.prepare_source_update(job, str(path))
    assert not payload["plan"].has_unresolved_suspected
    assert core.load_source(job) == b"old source"
    adapter.save_draft(job, "s2", "未提交的草稿", "旧译文。")
    with pytest.raises(ValueError, match="草稿"):
        adapter.execute_source_update(job, payload)
    assert core.load_source(job) == b"old source"
    adapter.clear_draft(job, "s2")
    updated = adapter.execute_source_update(job, payload)
    assert updated["source_update_pending"]
    assert updated["source_paras"] == payload["context"]["paragraphs"]
    assert updated["pairs"][0]["target"] == "不变的译文。"

    calls = []
    from folith import delivery
    def fake_generate(request, *args, **kwargs):
        calls.append(request["segment_index"])
        return {"candidate_target": "新译文。", "checks": {}}
    monkeypatch.setattr(delivery, "_generate_candidate_text", fake_generate)
    monkeypatch.setattr(adapter.EngineConfig, "connection_problem", lambda self: "")
    result = adapter.translate_source_update(job, adapter.EngineConfig())
    assert calls == [1]
    assert result["changed"] == 1
    finished = core.load_job_state(job)
    assert finished["p2_done"] and not finished["source_update_pending"]
    assert finished["pairs"][1]["target"] == "新译文。"
    assets = core.build_delivery_assets(job, finished, target_lang="简体中文")
    assert assets["translation.docx"]


def test_interrupted_update_is_recovered_on_open(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "source-update-recover"
    state = {"paras": ["A complete source paragraph."], "pairs": [{
        "source": "A complete source paragraph.", "target": "人工译文", "segment_uid": "s1"}]}
    core.save_source(job, b"original")
    core.save_job_state(job, state)
    plan = core.create_source_update_plan(state, ["A complete source paragraph.", "A new paragraph."], job_id=job)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        core.apply_source_update_plan(job, plan, simulate_crash_at="after_snapshot", **source_args(job, plan))
    recovered = adapter.load_state(job)
    assert not recovered.get("source_update_transaction")
    assert recovered["pairs"][0]["target"] == "人工译文"
    assert core.load_source(job) == b"original"


def test_docx_revision_can_roundtrip_after_incremental_update(tmp_path, monkeypatch):
    from pathlib import Path
    from folith.docx_roundtrip import extract_roundtrip_units, build_roundtrip_docx
    from folith.source_update import Classification, Resolution
    from folith import delivery
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "source_update"
    before = (fixture / "sample_v1.docx").read_bytes()
    after = (fixture / "sample_v2.docx").read_bytes()
    units, _ = extract_roundtrip_units(before)
    job = "docx-update-desktop"
    state = core.new_job_state("original.docx")
    state.update(p1_done=True, p2_done=True, target_lang="简体中文", segmentation_mode="paragraph",
        paras=[u.source_plain for u in units],
        segment_source_anchors=[u.source_anchor for u in units],
        pairs=[{"source": u.source_plain, "target": f"旧译文{i}", "reviewed": i != 1,
                "segment_uid": f"u{i}"} for i, u in enumerate(units)])
    core.save_source(job, before)
    core.save_job_state(job, state)
    path = tmp_path / "revision.docx"
    path.write_bytes(after)
    payload = adapter.prepare_source_update(job, str(path))
    for entry in payload["plan"].entries:
        if entry.classification == Classification.SUSPECTED_CHANGED:
            payload["plan"].resolve(entry.entry_id, Resolution.CONFIRM_REUSE)
    adapter.execute_source_update(job, payload)
    monkeypatch.setattr(adapter.EngineConfig, "connection_problem", lambda self: "")
    monkeypatch.setattr(delivery, "_generate_candidate_text", lambda *_a, **_k:
                        {"candidate_target": "新的译文。", "checks": {}})
    result = adapter.translate_source_update(job, adapter.EngineConfig())
    assert result["changed"] == 2
    finished = core.load_job_state(job)
    assert finished["p2_done"]
    output, report = build_roundtrip_docx(finished, after)
    assert report["status"] == "success"
    from docx import Document
    assert len(Document(output).paragraphs) >= 1
    exported_units, _ = extract_roundtrip_units(output.getvalue())
    assert [unit.source_plain for unit in exported_units] == [p["target"] for p in finished["pairs"]]
