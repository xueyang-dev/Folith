"""Section-aware scheduling invariants.

The scheduler exists to answer one question with evidence: *can independent
sections run in parallel without changing the continuity context a batch sees?*
These tests pin the ordering guarantees the answer depends on.

``section_of_segment`` is the per-segment section index list, so its length is
the total segment count.
"""
from __future__ import annotations

from folith import section_scheduler as sched


def test_concurrency_one_is_exactly_document_order():
    plan = sched.build_schedule([2, 2, 2, 2], [0] * 8, concurrency=1)
    assert plan.waves == ((0,), (1,), (2,), (3,))
    assert plan.to_dict()["waves"] == [[0], [1], [2], [3]]


def test_no_wave_contains_two_batches_from_the_same_section():
    sections = [0] * 6 + [1] * 6  # 6 batches of 2 segments: A A A B B B
    plan = sched.build_schedule([2] * 6, sections, concurrency=3,
                                section_ids=["A", "B"])
    by_index = {task.index: task for task in plan.tasks}
    for wave in plan.waves:
        wave_sections = [by_index[index].section_id for index in wave]
        assert len(wave_sections) == len(set(wave_sections)), wave
        assert len(wave) <= 3


def test_every_batch_appears_exactly_once():
    plan = sched.build_schedule([2, 2, 2, 2], [0, 0, 1, 1, 2, 2, 3, 3],
                                concurrency=4,
                                section_ids=["A", "B", "C", "D"])
    flat = [index for wave in plan.waves for index in wave]
    assert sorted(flat) == [0, 1, 2, 3]
    assert len(flat) == len(set(flat))


def test_in_section_order_is_preserved_across_waves():
    # Batch sections: A A B A  -> batch 3 is section A again and must follow 0/1.
    plan = sched.build_schedule([2, 2, 2], [0, 0, 1, 1, 0, 0], concurrency=3,
                                section_ids=["A", "B"])
    position = {}
    for wave_index, wave in enumerate(plan.waves):
        for index in wave:
            position[index] = wave_index
    a_batches = [task.index for task in plan.tasks if task.section_id == "A"]
    assert a_batches == [0, 2]
    for earlier, later in zip(a_batches, a_batches[1:]):
        assert position[earlier] <= position[later]


def test_section_ids_fall_back_to_index_names():
    plan = sched.build_schedule([2, 2], [0, 0, 0, 0], concurrency=2)
    assert {task.section_id for task in plan.tasks} == {"section-0"}


def test_scheduler_is_off_by_default(monkeypatch):
    monkeypatch.delenv(sched.SECTION_SCHEDULER_ENV, raising=False)
    assert sched.default_enabled() is False
    monkeypatch.setenv(sched.SECTION_SCHEDULER_ENV, "1")
    assert sched.default_enabled() is True


def test_serial_schedule_matches_document_order():
    plan = sched.serial_schedule([3, 3, 3], [0] * 9)
    assert plan.waves == ((0,), (1,), (2,))
    assert [task.offset for task in plan.tasks] == [0, 3, 6]


def test_plan_is_a_pure_function_of_its_inputs():
    sections = [0, 0, 0, 0, 1, 1, 1, 1]
    first = sched.build_schedule([2, 2, 2, 2], sections, concurrency=2,
                                 section_ids=["A", "B"])
    second = sched.build_schedule([2, 2, 2, 2], sections, concurrency=2,
                                  section_ids=["A", "B"])
    assert first == second
    assert first.waves == ((0, 2), (1, 3))
