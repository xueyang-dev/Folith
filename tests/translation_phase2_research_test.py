"""Contract checks for isolated research; these do not enable production paths."""
import importlib.util
import json
from pathlib import Path
import tempfile
import pytest

spec = importlib.util.spec_from_file_location("phase2", Path(__file__).resolve().parents[1]/"scripts/translation_phase2_research.py")
phase2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase2)


def test_straggler_replay_distinguishes_capacity_from_worker_count():
    service = [10, 1, 1, 1, 1, 1]
    baseline = phase2.queue_replay(service, capacity=2, ordered_refill=True)
    bounded_two = phase2.queue_replay(service, capacity=2)
    unbounded = phase2.queue_replay(service, capacity=6)
    bounded_four = phase2.queue_replay(service, capacity=4)
    assert baseline["wall_s"] == bounded_two["wall_s"] == 12
    assert unbounded["wall_s"] == 10
    assert bounded_four["wall_s"] == 11
    assert bounded_four["peak_outstanding"] <= 4
    assert unbounded["peak_ready"] > bounded_two["peak_ready"]


def test_queue_replay_includes_serial_durable_commit_cost():
    result = phase2.queue_replay([1, 1, 1], [2, 2, 2], capacity=3)
    assert result["wall_s"] == 7


def test_overlap_requires_capacity_and_keeps_committed_translation_chain():
    serial = phase2.overlap_model([.05]*8, [.08]*8, capacity=1, overlap=False)
    no_capacity = phase2.overlap_model([.05]*8, [.08]*8, capacity=1, overlap=True)
    spare = phase2.overlap_model([.05]*8, [.08]*8, capacity=2, overlap=True)
    assert abs(serial["wall_s"]-no_capacity["wall_s"]) < 1e-9
    assert spare["wall_s"] < serial["wall_s"]
    assert spare["peak_requests"] <= 2
    assert spare["committed_batches"] == spare["annotated_batches"] == 8


def test_staged_result_accepts_only_exact_target_and_glossary_versions():
    ready = dict(status="ready", inputs={"seg-0": 3, "seg-1": 5}, glossary_revision=2)
    versions = {"seg-0": 3, "seg-1": 5, "seg-9": 7}
    assert phase2.version_accept(ready, versions, glossary_revision=2, qa_ready=True)
    # An unrelated change must not invalidate this batch.
    versions["seg-9"] += 1
    assert phase2.version_accept(ready, versions, glossary_revision=2, qa_ready=True)
    versions["seg-1"] += 1
    assert not phase2.version_accept(ready, versions, glossary_revision=2, qa_ready=True)
    assert not phase2.version_accept(ready, {"seg-0": 3}, glossary_revision=2, qa_ready=True)
    assert not phase2.version_accept(ready, ready["inputs"], glossary_revision=3, qa_ready=True)
    assert not phase2.version_accept(ready, ready["inputs"], glossary_revision=2, qa_ready=False)
    assert not phase2.version_accept({**ready, "status": "failed"}, ready["inputs"], glossary_revision=2, qa_ready=True)


def test_crash_reloaded_stage_is_revalidated_and_commit_is_idempotent():
    staged = dict(status="ready", inputs={"seg-0": 1}, glossary_revision=4)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/"staged.json"
        path.write_text(json.dumps(staged))
        loaded = json.loads(path.read_text())
    accepted = set()
    identity = ("annotation", tuple(sorted(loaded["inputs"].items())), loaded["glossary_revision"])
    for _ in range(2):
        if phase2.version_accept(loaded, {"seg-0": 1}, glossary_revision=4, qa_ready=True):
            accepted.add(identity)
    assert len(accepted) == 1
    assert not phase2.version_accept(loaded, {"seg-0": 2}, glossary_revision=4, qa_ready=True)


@pytest.mark.parametrize("variant,cap", [("A", 2), ("C2", 2), ("C4", 4)])
@pytest.mark.parametrize("failure", ["disk", "cancel", "parse"])
def test_runtime_annotation_failure_resume_and_speculation_bounds(tmp_path, variant, cap, failure):
    import core
    row, _ = phase2.annotation_probe(core, tmp_path, variant, failure=failure)
    assert row["resume_complete"]
    if failure == "disk":
        assert row["durable_offset"] == 0
        assert row["calls"] <= cap
    elif failure == "cancel":
        assert row["durable_offset"] == 1
        assert row["calls"] <= cap
    else:
        assert row["resume_calls"] == 1
