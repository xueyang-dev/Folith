"""Reject stale full-snapshot saves without losing already committed edits."""
import copy
import json
import shutil
import threading

import pytest

import core
from folith.storage import job_repository as repo


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield tmp_path
    repo.invalidate_cache()


def _seed(job="revision", target='["raw model response"]'):
    state = core.new_job_state("source.txt")
    state.update(p1_done=True, p2_done=True, paras=["A real sentence."],
                 pairs=[{"source": "A real sentence.", "target": target}],
                 delivery_config=core.normalize_delivery_config({
                     key: key == "deliver_jsonl" for key in core.DELIVERY_CONFIG_DEFAULTS}))
    core.save_job_state(job, state)
    return state


def test_export_of_a_stale_snapshot_cannot_restore_a_rejected_translation():
    _seed()
    old = core.load_job_state("revision")
    core.save_translation_edit("revision", 0, "一句完整的话。")
    current = core.job_state_path("revision").read_bytes()
    with pytest.raises(RuntimeError, match="任务状态已更新"):
        core.build_delivery_assets("revision", old)
    assert core.job_state_path("revision").read_bytes() == current
    assert core.load_job_state("revision")["pairs"][0]["target"] == "一句完整的话。"


@pytest.mark.parametrize("operation", ["plan", "assets", "report"])
def test_qt_export_reads_inside_the_transaction_after_a_concurrent_edit(
        isolated_jobs, monkeypatch, operation):
    from folith.qt import adapter
    _seed()
    core.mutate_job_state("revision", lambda state: state.update(p3_md="# Old report"))
    load = adapter.load_state
    calls = []

    def load_then_edit(job_id):
        state = load(job_id)
        if not calls:
            calls.append(job_id)
            core.save_translation_edit(job_id, 0, "一句完整的话。")
            core.mutate_job_state(job_id, lambda latest: latest.update(p3_md="# New report"))
        return state

    monkeypatch.setattr(adapter, "load_state", load_then_edit)
    destination = isolated_jobs / "exported"
    if operation == "plan":
        result = adapter.export_plan("revision", destination)
        assert result["names"] and not destination.exists()
    elif operation == "assets":
        written = adapter.export_current_assets("revision", destination)
        jsonl = next(path for path in written if path.suffix == ".jsonl")
        assert json.loads(jsonl.read_text().splitlines()[0])["target"] == "一句完整的话。"
    else:
        from docx import Document
        target = isolated_jobs / "report.docx"
        adapter.export_report("revision", "docx", str(target))
        assert any("New report" in paragraph.text for paragraph in Document(target).paragraphs)
    assert core.load_job_state("revision")["pairs"][0]["target"] == "一句完整的话。"


@pytest.mark.parametrize("operation", ["report_docx_bytes", "run_libreoffice_render_qa",
                                       "save_compliance_record", "generate_report_qa"])
def test_stale_artifact_operations_fail_before_writing(operation):
    _seed(target="一句完整的话。")
    old = core.load_job_state("revision")
    core.mutate_job_state("revision", lambda state: state.update(theory="New theory"))
    before = {p.name: p.read_bytes() for p in core.job_dir("revision").glob("*.json")}
    kwargs = {"save_file": True} if operation == "generate_report_qa" else {}
    with pytest.raises(RuntimeError, match="任务状态已更新"):
        getattr(core, operation)("revision", old, **kwargs)
    after = {p.name: p.read_bytes() for p in core.job_dir("revision").glob("*.json")}
    assert after == before
    assert core.load_job_state("revision")["theory"] == "New theory"


def test_two_saves_of_the_same_revision_cannot_overwrite_each_other():
    state = _seed(target="一句完整的话。")
    barrier = threading.Barrier(2)
    results = []

    def save(value):
        candidate = copy.deepcopy(state)
        candidate["theory"] = value
        barrier.wait(timeout=5)
        try:
            core.save_job_state("revision", candidate)
            results.append(("saved", value))
        except RuntimeError as exc:
            results.append((str(exc), value))

    threads = [threading.Thread(target=save, args=(value,)) for value in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    saved = [value for status, value in results if status == "saved"]
    assert len(saved) == 1
    assert any("任务状态已更新" in status for status, _ in results)
    assert core.load_job_state("revision")["theory"] == saved[0]


def test_failed_replace_does_not_consume_the_callers_revision(monkeypatch):
    state = _seed(target="一句完整的话。")
    initial_revision = state["snapshot_revision"]
    real_save = repo.save_job_state_atomic

    def fail(*args, **kwargs):
        raise OSError("controlled replacement failure")

    monkeypatch.setattr(repo, "save_job_state_atomic", fail)
    with pytest.raises(OSError, match="replacement failure"):
        core.save_job_state("revision", state)
    assert state["snapshot_revision"] == initial_revision
    monkeypatch.setattr(repo, "save_job_state_atomic", real_save)
    core.save_job_state("revision", state)
    assert state["snapshot_revision"] == initial_revision + 1


def test_legacy_snapshot_without_revision_loads_and_saves(isolated_jobs):
    path = isolated_jobs / "legacy" / "state.json"
    path.parent.mkdir()
    path.write_text(json.dumps(core.new_job_state("old.txt")), encoding="utf-8")
    state = core.load_job_state("legacy")
    assert state["snapshot_revision"] == 0
    core.save_job_state("legacy", state)
    assert json.loads(path.read_text())["snapshot_revision"] == 1


def test_saving_a_deleted_loaded_task_does_not_recreate_it():
    state = _seed()
    path = core.job_dir("revision")
    shutil.rmtree(path)
    with pytest.raises(RuntimeError, match="任务已被删除"):
        core.save_job_state("revision", state)
    assert not path.exists()
