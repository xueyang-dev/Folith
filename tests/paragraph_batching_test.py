"""按自然段组批：整段送去翻译、按句返回；批次更大，每批固定开销被更多原文分摊。"""
from __future__ import annotations

import core
from folith import context as _context


def ranges(sizes):
    rows, start = [], 0
    for index, size in enumerate(sizes):
        rows.append({"paragraph_index": index, "start": start, "end": start + size})
        start += size
    return rows


def test_whole_paragraphs_are_packed_up_to_the_char_budget():
    segments = ["a" * 300] * 3 + ["b" * 300] * 3 + ["c" * 300] * 3
    batches = core.make_paragraph_batches(segments, ranges([3, 3, 3]), max_chars=2000)
    # 两段 1800 字符装得下，第三段会超出 → 另起一批；段落从不被拆开。
    assert [len(b) for b in batches] == [6, 3]


def test_an_oversized_paragraph_is_split_inside_and_packing_continues():
    segments = ["x" * 900] * 3 + ["y" * 100]
    batches = core.make_paragraph_batches(segments, ranges([3, 1]), max_chars=2000)
    assert [len(b) for b in batches] == [2, 2]
    assert sum(batches, []) == segments


def test_item_cap_and_semantic_unit_boundaries_are_respected():
    segments = ["s"] * 30
    assert [len(b) for b in core.make_paragraph_batches(
        segments, ranges([10, 10, 10]), max_items=20)] == [20, 10]
    units = [{"start_segment": 0, "end_segment": 4}, {"start_segment": 5, "end_segment": 29}]
    batches = core.make_paragraph_batches(segments, ranges([10, 10, 10]),
                                          max_items=20, semantic_units=units)
    assert [len(b) for b in batches] == [5, 15, 10]


def test_segments_outside_any_range_stand_alone():
    labels = core._paragraph_labels(4, [{"paragraph_index": 0, "start": 0, "end": 2}])
    assert labels == [0, 0, "solo-2", "solo-3"]


def test_grouped_batch_renders_paragraph_headers_and_keeps_numbering():
    packet = _context.compile_context_packet(
        None, None, None, "", [], [], [], ["One.", "Two.", "Three."])
    plain, _ = _context.render_context_packet_with_stats(packet)
    packet["current_paragraphs"] = [0, 0, 1]
    grouped, _ = _context.render_context_packet_with_stats(packet)
    assert "【第" not in plain
    assert grouped.endswith("【第 1 段】\n1. One.\n2. Two.\n【第 2 段】\n3. Three.")
    # 单句重试时标签与句数对不上，退回原格式。
    single = dict(packet, current_batch=["One."])
    assert "【第" not in _context.render_context_packet_with_stats(single)[0]


def test_strategy_resolution(monkeypatch):
    monkeypatch.delenv("FOLIOTHREAD_BATCH_STRATEGY", raising=False)
    assert core._batch_strategy({}) == "sentence"
    assert core._batch_strategy({}, default=core.DEFAULT_BATCH_STRATEGY) == "paragraph"
    monkeypatch.setenv("FOLIOTHREAD_BATCH_STRATEGY", "sentence")
    assert core._batch_strategy({}) == "sentence"
    assert core._batch_strategy({"pipeline_config": {"batch_strategy": "paragraph"}}) == "paragraph"
    assert core._batch_strategy({"pipeline_config": {"batch_strategy": "sentence"}}) == "sentence"
