"""Persistence work that runs beside the GUI must not change what is written.

* ``job_repository.encode_state`` encodes the state in pieces off the main
  thread (the C JSON encoder never releases the GIL); its output must be the
  bytes ``json.dumps(state, **COMPACT_JSON)`` produces.
* ``epub.bind_source_references`` remembers inputs it could not bind, so a
  saved edit does not re-segment the whole document; any changed input must be
  evaluated again.
"""
from __future__ import annotations

import json
import random
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith import epub  # noqa: E402
from folith.storage import job_repository as repository  # noqa: E402


def _on_worker(fn, *args):
    result = {}

    def run():
        result["value"] = fn(*args)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    return result["value"]


def _value(rng: random.Random, depth: int):
    roll = rng.random()
    if depth > 3 or roll < 0.3:
        return rng.choice([0, -3, 1.5, float("nan"), float("inf"), True, False, None,
                           "", "中文「引号」", "emoji 😀", "a\"b\\c\n", " "])
    if roll < 0.65:
        # Long lists (sliced by the encoder) only near the top, so the data stays small.
        sizes = [0, 1, 3, 300, 700] if depth < 2 else [0, 1, 3]
        return [_value(rng, depth + 2 if len(sizes) > 3 else depth + 1)
                for _ in range(rng.choice(sizes))]
    keys = ["k", "键", 1, 2.5, True, None, "z" * 3]
    return {rng.choice(keys) if rng.random() < 0.3 else f"key{n}": _value(rng, depth + 1)
            for n in range(rng.choice([0, 1, 4, 9]))}


@pytest.mark.parametrize("seed", range(60))
def test_worker_encoding_matches_json_dumps(seed):
    rng = random.Random(seed)
    state = {f"top{n}": _value(rng, 0) for n in range(rng.randint(1, 12))}
    if seed % 5 == 0:
        state[7] = "non-string top-level key"
    assert _on_worker(repository.encode_state, state) == \
        json.dumps(state, **repository.COMPACT_JSON)


def test_main_thread_and_non_dict_states_use_the_plain_encoder():
    for value in ({"a": [1] * 1000}, [1, 2], "x"):
        assert repository.encode_state(value) == json.dumps(value, **repository.COMPACT_JSON)


def _unbindable_state():
    paragraphs = ["First paragraph. It has two sentences.", "Second one."]
    return {"source_paras": list(paragraphs), "segmentation": {"mode": "sentence"},
            "pairs": [{"source": "Something the segmenter never produced."}]}


def test_a_refused_binding_is_remembered_but_recomputed_when_inputs_change(monkeypatch):
    epub._UNBINDABLE.clear()
    calls = []
    real = epub.segmentation.segment_paragraphs

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(epub.segmentation, "segment_paragraphs", counting)
    state = _unbindable_state()
    assert epub.bind_source_references(state) is False
    assert epub.bind_source_references(state) is False
    assert len(calls) == 1, "same inputs must not re-segment the document"

    state["pairs"][0]["source"] = "First paragraph."
    epub.bind_source_references(state)
    assert len(calls) == 2, "a changed input must be evaluated again"
