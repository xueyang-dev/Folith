"""The review index must answer exactly what the per-segment scans answered.

`translation_evidence.review_index` replaced an O(segments × events) scan that
froze the GUI for ~10 s when opening a book-length task.  The reference here is a
frozen copy of the module before the change (`tests/legacy/`).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from folith import translation_evidence as current  # noqa: E402
from folith import workbench_view  # noqa: E402
from legacy import translation_evidence_pre_index as legacy  # noqa: E402


def _event(rng: random.Random, n_segments: int, serial: int) -> object:
    roll = rng.random()
    if roll < 0.03:
        return "not-an-event"
    ids_pool = list(range(n_segments)) + [True, False, "7", None, 2.0]
    ids = rng.sample(ids_pool, k=rng.randint(0, min(6, len(ids_pool))))
    event = {"review_event_id": f"rev-{serial}"}
    if rng.random() < 0.8:
        event["segment_ids"] = ids
    else:
        event["completion_receipt"] = {"reviewed_segment_ids": ids,
                                       "status": rng.choice(["done", "failed",
                                                             "skipped_low_risk"])}
    event["review_scope"] = rng.choice(["current_translation", "historical", None])
    if rng.random() < 0.2:
        event["phase"] = "formal_review"
    if rng.random() < 0.2:
        event["stale_segment_ids"] = rng.sample(range(n_segments),
                                                k=rng.randint(0, min(3, n_segments)))
    if rng.random() < 0.15:
        event["freshness_status"] = "stale"
    if rng.random() < 0.1:
        event["decision"] = rng.choice(["failed", "skipped_low_risk", "passed"])
    return event


def _state(seed: int) -> dict:
    rng = random.Random(seed)
    n = rng.randint(1, 40)
    pairs = [{"source": f"s{i}", "target": "" if rng.random() < 0.1 else f"t{i}"}
             for i in range(n)]
    state = {
        "translation_core_review_required": rng.random() < 0.9,
        "pairs": pairs,
        "review_evidence": [_event(rng, n, k) for k in range(rng.randint(0, 60))],
        "source_quality_gate": {"flags": [{"segment_index": rng.randrange(n)}
                                          for _ in range(rng.randint(0, 3))]},
    }
    return state


def _assert_equivalent(state: dict) -> None:
    new_readiness = current.translation_review_readiness(state)
    old_readiness = legacy.translation_review_readiness(state)
    for key, value in old_readiness.items():
        assert new_readiness.get(key) == value
    index = current.review_index(state)
    probes = list(range(len(state.get("pairs") or []) + 2)) + [True, None, -1]
    for segment_id in probes:
        assert current.current_review_event(state, segment_id, index=index) is \
            legacy.current_review_event(state, segment_id)
        assert current.current_review_event(state, segment_id) is \
            legacy.current_review_event(state, segment_id)
        if isinstance(segment_id, int):
            new = workbench_view._historical_event(state, segment_id, index=index)
            old = workbench_view._historical_event(state, segment_id)
            # The same event object, or both "nothing found" (a fresh {} each call).
            assert new is old or (new == {} and old == {})


@pytest.mark.parametrize("seed", range(400))
def test_index_matches_the_per_segment_scans(seed):
    _assert_equivalent(_state(seed))


def test_workbench_view_old_fields_are_unchanged(monkeypatch):
    coverage_keys = {
        "skipped_segment_reasons", "skipped_reason_counts", "reviewed_count",
        "skipped_count", "coverage_complete", "coverage_status",
    }
    for seed in range(60):
        state = _state(seed)
        new = workbench_view.review_workbench_view(state)
        with monkeypatch.context() as patch:
            patch.setattr(workbench_view.translation_evidence,
                          "translation_review_readiness",
                          legacy.translation_review_readiness)
            patch.setattr(workbench_view.translation_evidence, "review_index",
                          lambda _state: None)
            old = workbench_view.review_workbench_view(state)
        if new["readiness"].get("skipped_count"):
            continue
        new["readiness"] = {
            key: value for key, value in new["readiness"].items()
            if key not in coverage_keys
        }
        assert new == old, seed


def _real_states():
    """Real tasks on this machine, if any (read-only; never in CI)."""
    root = Path(os.environ.get("FOLITH_REAL_TASKS_DIR") or
                Path.home() / "Library" / "Application Support" / "Folith" / "data")
    if not root.is_dir() or os.environ.get("CI"):
        return []
    found = []
    for path in sorted(root.glob("*/state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state.get("review_evidence"):
            found.append(state)
    return found


def test_real_tasks_on_this_machine_are_unchanged():
    states = _real_states()
    if not states:
        pytest.skip("no real tasks with review evidence on this machine")
    for state in states:
        _assert_equivalent(state)


def test_readiness_scales_linearly():
    """5 000 段、1 000 条审校记录：一次就绪计算在 50ms 内（旧实现约需数秒）。"""
    n = 5000
    events = []
    for k in range(1000):
        start = (k * 5) % n
        events.append({"review_event_id": f"r{k}", "review_scope": "current_translation",
                       "segment_ids": list(range(start, start + 5))})
    state = {"translation_core_review_required": True,
             "pairs": [{"source": "s", "target": "t"} for _ in range(n)],
             "review_evidence": events}
    started = time.perf_counter()
    result = current.translation_review_readiness(state)
    elapsed = time.perf_counter() - started
    assert result["status"] == "current"
    assert elapsed < 0.05, f"{elapsed * 1000:.0f} ms"
