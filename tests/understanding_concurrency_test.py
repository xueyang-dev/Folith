"""#319 plan A: understanding-phase concurrency without changing output.

The issue's constraint is narrow: raise the digest worker count to 8 (bounded,
configurable) and run one synopsis-reduce layer in parallel, while
**byte-identical output** and the existing cancel/checkpoint/fallback semantics
stay untouched.

These tests use an injected mock provider (no network) to prove:

* same-layer calls really overlap, and the worker knob is bounded/configurable;
* a parallel reduce produces the same prompts, the same synopsis and the same
  persisted artifacts as the serial reference implementation (a verbatim copy of
  the pre-change algorithm lives in this file);
* a failing unit still falls back to the deterministic digest, with the same
  number of provider calls as the serial version (no retry amplification);
* cancellation propagates out of in-flight parallel calls and never leaves
  half-written artifacts;
* resume from a checkpoint skips completed units in the parallel path too.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import context  # noqa: E402


# ---------------------------------------------------------------- fixtures

def _paragraphs(count: int) -> list[str]:
    return [f"Section {index}: " + ("word " * 40).strip() for index in range(count)]


def _profile(count: int) -> dict:
    return {"sections": [
        {"section_id": f"s{index}", "start_segment": index,
         "end_segment": index, "topic": f"topic {index}"}
        for index in range(count)]}


def _units(count: int) -> list[dict]:
    return context.build_semantic_units(_paragraphs(count), _profile(count))


def _synopsis_system() -> str:
    return context._synopsis_system_prompt()


class MockProvider:
    """Deterministic mock ``call_llm`` with concurrency probing.

    ``enter``/``leave`` bracket each model call.  When ``expected_concurrent``
    calls are inside at the same time, the probe releases; that makes "did these
    calls really overlap?" a deterministic assertion instead of a timing race.
    """

    def __init__(self, *, expected_concurrent: int = 0, delay: float = 0.0,
                 fail_markers: tuple[str, ...] = ()):
        self.expected_concurrent = expected_concurrent
        self.delay = delay
        self.fail_markers = tuple(fail_markers)
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.inside = 0
        self.peak = 0
        self.released = threading.Event()
        self.calls: list[dict] = []
        self.cancel_event = threading.Event()
        self.cancel_after_calls: int | None = None
        self.layer_state = {
            "layer1_active": 0, "layer1_started": 0, "layer1_done": 0,
            "layer2_starts": [],
        }

    # -- probe ---------------------------------------------------------
    def _enter(self) -> None:
        with self.condition:
            self.inside += 1
            self.peak = max(self.peak, self.inside)
            if self.inside >= self.expected_concurrent:
                self.released.set()
                self.condition.notify_all()
        self.released.wait(timeout=5.0)

    def _leave(self) -> None:
        with self.lock:
            self.inside -= 1

    # -- provider ------------------------------------------------------
    def __call__(self, provider, key, model, system, user, temperature=0.1):
        with self.lock:
            call_index = len(self.calls)
        layer = 2 if "[中间概要" in user else 1
        with self.lock:
            if layer == 1:
                self.layer_state["layer1_active"] += 1
                self.layer_state["layer1_started"] += 1
            else:
                self.layer_state["layer2_starts"].append(
                    {**self.layer_state})
            self.calls.append({"index": call_index, "system": system,
                              "user": user, "layer": layer})
        self._enter()
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.cancel_event.is_set():
                raise RuntimeError("任务已请求取消")
            if self.cancel_after_calls is not None \
                    and call_index >= self.cancel_after_calls:
                self.cancel_event.set()
                raise RuntimeError("任务已请求取消")
            for marker in self.fail_markers:
                if marker in user:
                    raise ValueError(f"mock provider failure for {marker}")
            if "全书理解器" in system:
                return json.dumps({
                    "summary": "S-" + self._digest(user, 16),
                    "themes": ["theme"],
                })
            return json.dumps({
                "summary": "D-" + self._digest(user, 12),
                "key_entities": ["entity"],
                "key_terms": ["term"],
                "translation_notes": ["note"],
            })
        finally:
            with self.lock:
                if layer == 1:
                    self.layer_state["layer1_active"] -= 1
                    self.layer_state["layer1_done"] += 1
            self._leave()

    @staticmethod
    def _digest(user: str, length: int) -> str:
        return hashlib.sha256(user.encode("utf-8")).hexdigest()[:length]

    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------- digest pool

def test_digest_workers_run_one_layer_in_parallel(monkeypatch):
    monkeypatch.delenv(context.DIGEST_WORKERS_ENV, raising=False)
    units = _units(8)
    mock = MockProvider(expected_concurrent=8)

    digests, warnings = context.generate_section_digests(
        units, "Provider", "k", "m", "简体中文", call_llm=mock)

    assert len(digests) == 8 and not warnings
    assert mock.peak == 8, f"8 个互不依赖的单元应同时最多 8 路，实测 {mock.peak}"
    assert [digest["unit_id"] for digest in digests] == [unit["unit_id"] for unit in units]


def test_digest_worker_count_is_configurable_and_capped(monkeypatch):
    units = _units(9)

    monkeypatch.setenv(context.DIGEST_WORKERS_ENV, "6")
    mock = MockProvider(expected_concurrent=6)
    context.generate_section_digests(units, "p", "k", "m", "", call_llm=mock)
    assert mock.peak == 6

    monkeypatch.setenv(context.DIGEST_WORKERS_ENV, "999")
    mock = MockProvider(expected_concurrent=context.MAX_DIGEST_WORKERS)
    context.generate_section_digests(units, "p", "k", "m", "", call_llm=mock)
    assert mock.peak == context.MAX_DIGEST_WORKERS, "环境变量必须被硬上限截断"

    monkeypatch.setenv(context.DIGEST_WORKERS_ENV, "not-a-number")
    mock = MockProvider(expected_concurrent=context.DEFAULT_DIGEST_WORKERS)
    context.generate_section_digests(units, "p", "k", "m", "", call_llm=mock)
    assert mock.peak == context.DEFAULT_DIGEST_WORKERS

    # Explicit parameter wins over the environment.
    mock = MockProvider(expected_concurrent=2)
    context.generate_section_digests(units, "p", "k", "m", "", call_llm=mock,
                                     max_workers=2)
    assert mock.peak == 2

    assert context.digest_worker_count(1) == 1
    assert context.digest_worker_count(context.MAX_DIGEST_WORKERS + 5) \
        == context.MAX_DIGEST_WORKERS
    assert context.digest_worker_count(0) == 1


# ---------------------------------------------------------------- reduce layers

def _long_digests(count: int) -> list[dict]:
    return [{
        "unit_id": f"unit-{index:04d}", "kind": "section", "label": f"unit {index}",
        "start_segment": index, "end_segment": index,
        "summary": f"summary {index} " + ("x" * 700),
        "key_entities": ["entity"], "key_terms": ["term"],
        "open_threads": [], "translation_notes": ["note"], "status": "model",
    } for index in range(count)]


def test_synopsis_same_layer_parallel_next_layer_waits():
    # 8 digests of ~760 chars with a 1000-char chunk limit -> 8 chunks on the
    # first reduce level; the probe only releases once all 8 are in flight.
    mock = MockProvider(expected_concurrent=8)

    synopsis, warnings = context.generate_document_synopsis(
        _long_digests(8), "p", "k", "m", "简体中文", call_llm=mock,
        max_chunk_chars=1000)

    assert synopsis["summary"].startswith("S-") and not warnings
    layer1 = [call for call in mock.calls if call["layer"] == 1]
    assert len(layer1) >= 4, "首层块应并行调用"
    assert mock.peak >= 4, f"同层块应并发，实测峰值 {mock.peak}"
    assert mock.layer_state["layer2_starts"], "该输入应至少产生一层合并"
    for snapshot in mock.layer_state["layer2_starts"]:
        assert snapshot["layer1_active"] == 0, "下一层不得与上一层并发"
        assert snapshot["layer1_done"] == snapshot["layer1_started"], \
            "下一层必须等上一层全部完成"


# ---------------------------------------------------------------- equivalence

def _serial_reduce_reference(items, provider, api_key, model, target_lang,
                             call_llm, max_chars):
    """Verbatim copy of the pre-change serial algorithm (golden reference)."""
    chunk_limit = min(context.MAX_SYNOPSIS_CHARS, max(1000, int(max_chars or 12000)))
    chunks = context._text_chunks(items, chunk_limit)
    if not chunks:
        return {"summary": "", "status": "unavailable"}
    reduced = []
    for chunk in chunks:
        raw = context._parse_object(context._call(
            call_llm, provider, api_key, model, _synopsis_system(),
            f"目标语言：{target_lang}\n语义摘要块：\n{chunk}"))
        if raw is None:
            raise ValueError("模型未返回结构化 JSON")
        reduced.append(context._normalize_synopsis(raw))
    if len(reduced) == 1:
        return reduced[0]
    return _serial_reduce_reference(
        [context._synopsis_text(item, index) for index, item in enumerate(reduced)],
        provider, api_key, model, target_lang, call_llm,
        min(context.MAX_SYNOPSIS_CHARS, chunk_limit * 2))


@pytest.mark.parametrize("digest_count,max_chars", [(3, 1000), (8, 1000), (8, 700),
                                                    (17, 1200)])
def test_parallel_reduce_matches_serial_reference(digest_count, max_chars):
    digests = _long_digests(digest_count)
    items = [context._digest_text(digest) for digest in digests]

    serial_mock = MockProvider()
    serial = _serial_reduce_reference(
        items, "p", "k", "m", "简体中文", serial_mock, max_chars)
    parallel_mock = MockProvider()
    parallel = context._reduce_synopsis(
        items, "p", "k", "m", "简体中文", parallel_mock, max_chars,
        max_workers=context.MAX_DIGEST_WORKERS)

    assert parallel == serial
    assert sorted(call["user"] for call in parallel_mock.calls) \
        == sorted(call["user"] for call in serial_mock.calls), \
        "同层块集合与层间输入必须与串行版完全一致"


@pytest.mark.parametrize("digest_count", [2, 9])
def test_parallel_understanding_artifacts_are_byte_identical(digest_count, tmp_path):
    serial_dir = tmp_path / "serial"
    parallel_dir = tmp_path / "parallel"

    _, _, serial_synopsis, serial_warnings = context.build_document_understanding(
        _paragraphs(digest_count), _profile(digest_count), "p", "k", "m",
        "简体中文", call_llm=MockProvider(), max_workers=1,
        checkpoint_dir=serial_dir)
    _, _, parallel_synopsis, parallel_warnings = context.build_document_understanding(
        _paragraphs(digest_count), _profile(digest_count), "p", "k", "m",
        "简体中文", call_llm=MockProvider(),
        max_workers=context.MAX_DIGEST_WORKERS, checkpoint_dir=parallel_dir)

    assert serial_synopsis == parallel_synopsis
    assert serial_warnings == parallel_warnings
    for name in ("semantic_units.json", "section_digests.json",
                 "document_synopsis.json"):
        assert (serial_dir / name).read_bytes() == (parallel_dir / name).read_bytes(), \
            f"{name} 必须逐字节一致"


# ---------------------------------------------------------------- failure path

def test_failed_unit_falls_back_without_amplifying_retries():
    units = _units(6)
    marker = units[3]["unit_id"]

    serial_mock = MockProvider(fail_markers=(marker,))
    serial_digests, serial_warnings = context.generate_section_digests(
        units, "p", "k", "m", "简体中文", call_llm=serial_mock, max_workers=1)
    parallel_mock = MockProvider(fail_markers=(marker,))
    parallel_digests, parallel_warnings = context.generate_section_digests(
        units, "p", "k", "m", "简体中文", call_llm=parallel_mock,
        max_workers=context.MAX_DIGEST_WORKERS)

    assert serial_digests == parallel_digests
    assert sorted(serial_warnings) == sorted(parallel_warnings)
    assert serial_mock.call_count() == len(units)
    assert parallel_mock.call_count() == len(units), \
        "失败重试次数不得因并发而放大（每个单元仍只有一次调用）"
    assert parallel_digests[3]["status"] == "deterministic_fallback"
    assert any("语义摘要失败" in warning for warning in parallel_warnings)


# ---------------------------------------------------------------- cancel/resume

def test_cancel_propagates_from_in_flight_parallel_digest_calls(tmp_path):
    mock = MockProvider(expected_concurrent=2)
    mock.cancel_after_calls = 2

    with pytest.raises(RuntimeError, match="取消"):
        context.build_document_understanding(
            _paragraphs(8), _profile(8), "p", "k", "m", "简体中文",
            call_llm=mock, max_workers=8, checkpoint_dir=tmp_path)

    # Checkpoint files are the resume mechanism: they must always be valid JSON
    # and must never claim a finished synopsis after a cancellation.
    for name in ("semantic_units.json", "section_digests.json",
                 "document_synopsis.json"):
        path = tmp_path / name
        assert path.is_file(), f"取消后仍应有可续跑的 {name}"
        json.loads(path.read_text(encoding="utf-8"))
    synopsis = json.loads((tmp_path / "document_synopsis.json").read_text(encoding="utf-8"))
    assert synopsis.get("status") == "pending", "取消后不得落盘半成品概要"


def test_cancel_propagates_from_parallel_synopsis_reduce():
    mock = MockProvider()
    mock.cancel_after_calls = 2
    with pytest.raises(RuntimeError, match="取消"):
        context.generate_document_synopsis(
            _long_digests(8), "p", "k", "m", "简体中文", call_llm=mock,
            max_chunk_chars=1000)


def test_resume_from_checkpoint_skips_completed_units_in_parallel_path(tmp_path):
    paragraphs = _paragraphs(8)
    profile = _profile(8)
    first = MockProvider()
    units, digests, synopsis, _ = context.build_document_understanding(
        paragraphs, profile, "p", "k", "m", "简体中文",
        call_llm=first, max_workers=8, checkpoint_dir=tmp_path)
    assert len(digests) == 8

    unused = MockProvider()
    resumed_units, resumed_digests, resumed_synopsis, warnings = \
        context.build_document_understanding(
            paragraphs, profile, "p", "k", "m", "简体中文",
            call_llm=unused, max_workers=8, checkpoint_dir=tmp_path)
    assert unused.call_count() == 0, "检查点完整时不应再发模型请求"
    assert resumed_units == units
    assert resumed_digests == digests
    assert resumed_synopsis == synopsis
    assert not warnings
