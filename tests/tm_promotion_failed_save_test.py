"""#109: a failed job-state save after a review decision must not leave the
human-reviewed promotion behind in the project translation memory."""
import pytest

import core
from folith.storage import job_repository as repo
from folith.translation_core import fingerprint, normalize_finding

JOB = "tm-promotion-failed-save"
SOURCE = "The committee approved the annual budget."
TARGET = "委员会批准了年度预算。"


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield tmp_path
    repo.invalidate_cache()


def _seed():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding.update({"type": "review", "segment_index": 0,
                    "review_event_id": "review-a", "reason": "omission"})
    state = core.new_job_state("tm-source.docx")
    state.update(
        p1_done=True, p2_done=True, paras=[SOURCE], target_lang="简体中文",
        use_tm=True, translation_core_review_required=True,
        pairs=[{"source": SOURCE, "target": TARGET, "reviewed": False}],
        findings=[finding], human_actions=[],
        review_evidence=[{
            "phase": "formal_review", "review_event_id": "review-a",
            "segment_ids": [0], "decision": "findings",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": [0]},
            "translation_core": {
                "final_consumed_input_fingerprint": current,
                "review_truth": [{"segment_id": 0, "source": SOURCE,
                                  "target": TARGET}],
            },
        }])
    core.save_job_state(JOB, state)
    return core.load_job_state(JOB), finding["finding_id"]


def _tm_bytes(state):
    path = core.tm_path(core.tm_project_id(state))
    return path.read_bytes() if path.exists() else None


def _has_entry(state):
    raw = _tm_bytes(state)
    return raw is not None and TARGET in raw.decode("utf-8")


def _fail_save(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("任务状态已更新")
    monkeypatch.setattr(core, "save_job_state", boom)


def test_control_dismiss_with_a_real_save_promotes_into_the_tm():
    state, finding_id = _seed()
    assert not _has_entry(state)
    core.decide_translation_review_finding(
        JOB, finding_id, "dismiss", "alice", actor_type="human")
    assert _has_entry(state)


def test_failed_save_after_dismiss_leaves_the_tm_untouched(monkeypatch):
    state, finding_id = _seed()
    before = _tm_bytes(state)
    _fail_save(monkeypatch)
    with pytest.raises(RuntimeError):
        core.decide_translation_review_finding(
            JOB, finding_id, "dismiss", "alice", actor_type="human")
    assert _tm_bytes(state) == before
    assert not _has_entry(state)


def test_failed_save_after_mark_findings_resolved_leaves_the_tm_untouched(monkeypatch):
    state, finding_id = _seed()
    before = _tm_bytes(state)
    _fail_save(monkeypatch)
    with pytest.raises(RuntimeError):
        core.mark_findings_resolved(JOB, [finding_id], "preserved")
    assert _tm_bytes(state) == before
    assert not _has_entry(state)
