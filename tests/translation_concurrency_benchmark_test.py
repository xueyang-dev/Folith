"""The concurrency audit must compare the same batches despite worker ordering."""
from scripts.translation_concurrency_benchmark import _context_divergence, _timing_summary


def test_context_comparison_matches_source_batches_instead_of_completion_order():
    baseline = [
        {"current_batch": ["a"], "previous_target": []},
        {"current_batch": ["b"], "previous_target": ["translated a"]},
        {"current_batch": ["c"], "previous_target": ["translated b"]},
    ]
    assert _context_divergence(baseline, list(reversed(baseline))) == {
        "diverged_batches": 0, "missing_batches": 0, "total_batches": 3}
    changed = [dict(baseline[2], previous_target=[]), baseline[0]]
    assert _context_divergence(baseline, changed) == {
        "diverged_batches": 1, "missing_batches": 1, "total_batches": 3}


def test_commit_wait_is_measured_after_result_ready_not_worker_start():
    events = [
        {"stage": "translation", "batch_id": 1, "phase": phase, "at": at}
        for phase, at in (("queued", 0), ("worker_started", 2),
                          ("result_ready", 12), ("commit_started", 15),
                          ("commit_finished", 16))]
    result = _timing_summary(list(reversed(events)))
    assert result["prepare"]["sum_s"] == 10
    assert result["commit_wait"]["sum_s"] == 3
    assert result["commit"]["sum_s"] == 1
