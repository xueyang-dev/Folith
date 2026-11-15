"""P3-5 UI/adapter lock gates on mixed locked and editable segments."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import core
from folith.qt import adapter, i18n


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    i18n.set_language("zh-CN")
    state = core.new_job_state("locked.txt")
    state.update(p1_done=True, p2_done=True,
                 paras=["Lydia first", "Lydia second"],
                 pairs=[{"source": "Lydia first", "target": "莉迪娅 first", "readonly": True},
                        {"source": "Lydia second", "target": "莉迪娅 second"}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪娅", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    core.save_job_state("p35", state)
    return "p35"


def test_xliff_whole_job_start_resume_and_retry_are_blocked(job, monkeypatch):
    state = adapter.load_state(job)
    state["xliff"] = {"version": 1, "dialect": "xliff12"}
    core.save_job_state(job, state)
    calls = []
    for name in ("start_job", "continue_job", "retry_job"):
        monkeypatch.setattr(adapter._commands, name,
                            lambda *args, _name=name, **kwargs: calls.append(_name) or True)
    config = adapter.EngineConfig(provider="自定义中转站", model="test", api_key="key", base_url="http://localhost:1234")
    for call in (lambda: adapter.start_job(job, "locked.xlf", None, config),
                 lambda: adapter.continue_job(job, config),
                 lambda: adapter.retry_job(job)):
        with pytest.raises(adapter.AdapterError, match="双语文件"):
            call()
    assert calls == []
    del state["xliff"]
    core.save_job_state(job, state)
    assert adapter.continue_job(job, config)
    assert adapter.retry_job(job)
    assert calls == ["continue_job", "retry_job"]


def test_editor_displays_locked_pair_and_rejects_edit(job, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from folith.qt.views import WorkbenchView

    app = QApplication.instance() or QApplication([])
    view = WorkbenchView()
    state = adapter.load_state(job)
    # A draft from before the lock must not hide the imported target.
    uid = core.segment_uid(state["pairs"][0]) or "0"
    core.save_translation_drafts(job, {uid: {"text": "old draft", "baseline": "莉迪娅 first"}})
    rows = adapter.segment_rows(job, state)
    view.apply(job, state, rows, adapter.runtime_view(job, state), adapter.delivery_snapshots(job))
    view.select_segment(0)
    app.processEvents()
    assert view.target_edit.toPlainText() == "莉迪娅 first"
    assert view.target_edit.isReadOnly()
    assert not view.submit_button.isEnabled()
    assert not view.retranslate_button.isEnabled()
    with pytest.raises(adapter.AdapterError, match="锁定"):
        adapter.submit_edit(job, 0, "changed")
    with pytest.raises(adapter.AdapterError, match="锁定"):
        adapter.save_draft(job, uid, "changed")
    view.select_segment(1)
    app.processEvents()
    assert not view.target_edit.isReadOnly()
    view.target_edit.setPlainText("draft on editable segment")
    with monkeypatch.context() as patch:
        def unexpected_load(_job_id):
            raise AssertionError("GUI autosave must reuse the displayed state")
        patch.setattr(adapter, "load_state", unexpected_load)
        assert view.flush_draft()
    adapter.submit_edit(job, 1, "changed")
    assert [p["target"] for p in adapter.load_state(job)["pairs"]] == ["莉迪娅 first", "changed"]
    view.close()


def test_autosave_reuses_displayed_state_and_checks_lock(job, monkeypatch):
    state = adapter.load_state(job)
    locked_uid = core.segment_uid(state["pairs"][0]) or "0"
    editable_uid = core.segment_uid(state["pairs"][1]) or "1"
    def unexpected_load(_job_id):
        raise AssertionError("autosave must not load the whole job on the GUI thread")
    monkeypatch.setattr(adapter, "load_state", unexpected_load)
    with pytest.raises(adapter.AdapterError, match="锁定"):
        adapter.save_draft(job, locked_uid, "bad", state=state)
    adapter.save_draft(job, editable_uid, "draft", state=state)
    drafts = core.load_translation_drafts(job)
    assert locked_uid not in drafts
    assert drafts[editable_uid]["text"] == "draft"


def test_batch_replace_skips_locked_even_with_override(job):
    result = adapter.replace_in_targets(job, "莉迪娅", "莉迪亚", allow_protected=True)
    assert result["changed"] == 1
    assert [p["target"] for p in adapter.load_state(job)["pairs"]] == ["莉迪娅 first", "莉迪亚 second"]


def test_mixed_entity_repair_skips_locked_and_keeps_the_conflict(job):
    preview = adapter.entity_repair_preview(job, "Lydia", "莉迪亚")
    assert [item["index"] for item in preview] == [0, 1]
    with pytest.raises(adapter.AdapterError, match="预览"):
        adapter.apply_entity_repair(job, "Lydia", "莉迪亚", preview[1:])
    state, changed, skipped = adapter.apply_entity_repair(job, "Lydia", "莉迪亚", preview)
    assert changed == [1]
    assert skipped == 1
    assert [p["target"] for p in state["pairs"]] == ["莉迪娅 first", "莉迪亚 second"]
    assert core.entity_consistency_queue(state)


def test_retranslation_skips_locked_and_allows_unlocked(job, monkeypatch):
    config = adapter.EngineConfig(provider="自定义中转站", model="test", api_key="key", base_url="http://localhost:1234")
    calls = []
    def regenerate(job_id, indexes, *args, **kwargs):
        calls.extend(indexes)
        return core.load_job_state(job_id), indexes
    monkeypatch.setattr(core, "retranslate_segments", regenerate)
    with pytest.raises(adapter.AdapterError, match="锁定"):
        adapter.retranslate_segment(job, 0, config)
    adapter.retranslate_segment(job, 1, config)
    assert calls == [1]
    assert [p["target"] for p in adapter.load_state(job)["pairs"]] == ["莉迪娅 first", "莉迪娅 second"]
