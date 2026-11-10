"""CPU-shaped translation work for the A2 spike.

This is deliberately **not** the real pipeline.  It reproduces the *shape* of
the GIL-bound work a pipeline worker does per batch on the GUI process -- JSON
parse of the state, deterministic QA regexes, row/dict building, JSON
serialisation of the committed batch -- plus a mock provider latency
(``time.sleep``, which releases the GIL, like a network wait).  The point of the
spike is to measure how much GUI-thread smoothness that workload costs when it
runs in-process versus in a child process; a synthetic workload is enough for
that question and keeps the prototype honest about what it is not measuring.

Progress records deliberately use the field names of A1's ``DeltaBatch``
(``schema/seq/batch/offset/segments/domains/origin``, see
``folith/state_feed.py`` on PR #337).  The prototype does not import that
module because it is not on ``main`` yet; the contract is documented in
``docs/performance/process-isolation-spike-0.8.2.md``.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Iterable

QA_DOUBLE_SPACE = re.compile(r"  +")
QA_SPACE_BEFORE_PUNCT = re.compile(r"\s+[,.;:!?]")
QA_CJK_IN_LATIN = re.compile(r"[A-Za-z]+[\u4e00-\u9fff]|[\u4e00-\u9fff][A-Za-z]+")

BATCH_SIZE = 20
MOCK_LATENCY_S = 0.02


def build_corpus(segments: int, *, seed: int = 7) -> list[str]:
    """Deterministic book-shaped source text (same bytes on every run)."""
    words = ("the quick brown fox jumps over the lazy dog while translators "
             "review context and terminology across the whole document").split()
    corpus = []
    for index in range(segments):
        length = 8 + (index + seed) % 17
        text = " ".join(words[(index + offset) % len(words)] for offset in range(length))
        corpus.append(f"{text.capitalize()} — 段落 {index}.")
    return corpus


class BatchEngine:
    """Runs ``batches`` of the CPU-shaped workload, resumable from a cursor."""

    def __init__(self, *, segments: int = 2000, batches: int = 200,
                 batch_size: int = BATCH_SIZE,
                 latency_s: float = MOCK_LATENCY_S,
                 parse_mb: float = 0.0) -> None:
        self.segments = int(segments)
        self.batches = int(batches)
        self.batch_size = int(batch_size)
        self.latency_s = float(latency_s)
        self.parse_mb = float(parse_mb)
        # An optional large JSON payload parsed once per batch.  A multi-MB
        # ``json.loads`` is a C call that holds the GIL for its whole duration,
        # which is the shape of the real "large state / layout IR" parses the
        # worker performs; it is what a process boundary can actually remove
        # from the GUI process.
        self.heavy_blob = None
        if self.parse_mb > 0:
            filler = "x" * 1024
            items = max(1, int(self.parse_mb * 1024))
            self.heavy_blob = "[" + ",".join(f'"{filler}"' for _ in range(items)) + "]"
        self.corpus = build_corpus(self.segments)
        # One JSON blob shaped like a full state snapshot; each simulated batch
        # re-reads it, which is the O(document) parse the worker really pays.
        self.state_blob = json.dumps(
            {"pairs": [{"segment_uid": f"seg-{index:06d}", "source": text,
                        "target": f"译文 {index}", "reviewed": index % 5 == 0,
                        "review_status": "not_reviewed", "from_tm": False,
                        "target_provenance": "generated", "excluded": False}
                       for index, text in enumerate(self.corpus)]},
            ensure_ascii=False)

    # -- workload ---------------------------------------------------------
    def _parse_state(self) -> dict:
        return json.loads(self.state_blob)

    def _qa(self, texts: Iterable[str]) -> list[dict]:
        findings = []
        for text in texts:
            findings.extend({"kind": "double_space", "at": match.start()}
                            for match in QA_DOUBLE_SPACE.finditer(text))
            findings.extend({"kind": "space_before_punct", "at": match.start()}
                            for match in QA_SPACE_BEFORE_PUNCT.finditer(text))
            findings.extend({"kind": "cjk_latin_join", "at": match.start()}
                            for match in QA_CJK_IN_LATIN.finditer(text))
        return findings

    def run_batch(self, batch_index: int) -> dict[str, Any]:
        """One batch: mock latency + the CPU work, returning the delta payload."""
        if self.latency_s:
            time.sleep(self.latency_s)          # network wait: releases the GIL
        offset = (batch_index * self.batch_size) % max(1, self.segments - self.batch_size)
        if self.heavy_blob is not None:
            json.loads(self.heavy_blob)          # large C parse (holds the GIL)
        slice_pairs = self._parse_state()        # state parse (CPU, GIL)
        batch_texts = [pair["source"] for pair in slice_pairs["pairs"]
                       [offset:offset + self.batch_size]]
        findings = self._qa(batch_texts)          # QA regex (CPU, GIL)
        committed = [{"index": offset + position,
                      "segment_uid": f"seg-{offset + position:06d}",
                      "target": f"提交译文 {batch_index}-{position}",
                      "fields": ["target"]}
                     for position in range(min(self.batch_size, self.segments - offset))]
        record = {
            "schema": 1,
            "seq": int(batch_index) + 1,
            "batch": int(batch_index),
            "offset": offset,
            "segments": committed,
            "findings_delta": {"findings": len(findings)},
            "domains": ["rows", "counts"],
            "origin": "pipeline",
            "bytes": len(json.dumps(committed)) + 128,   # serialise the batch (CPU, GIL)
        }
        json.dumps(record)                            # journal record encode (CPU, GIL)
        return record

    # -- lifecycle --------------------------------------------------------
    def run(self, *, control: Callable[[], str], emit: Callable[[dict], None],
            status: Callable[[str, dict], None], resume_from: int = 0,
            crash_after: int | None = None) -> None:
        """Run batches under cooperative control, resumable from ``resume_from``.

        ``control()`` returns ``run`` / ``pause`` / ``exit``.  Pausing waits for
        ``run`` or ``exit`` without doing work, exactly like the product's
        cooperative pause checkpoint.  ``exit`` stops after the current batch
        (never mid-batch), which is what the desktop lifecycle contract calls
        ``interrupted``.
        """
        for batch_index in range(int(resume_from), self.batches):
            if crash_after is not None and batch_index >= crash_after:
                import os
                os._exit(9)                        # simulated hard crash
            while True:
                command = control()
                if command == "run":
                    break
                if command == "exit":
                    status("interrupted", {"committed_batches": batch_index,
                                           "seq": batch_index, "last_batch": batch_index - 1})
                    return
                time.sleep(0.02)                   # paused: wait for continue/exit
            status("running", {"batch": batch_index + 1, "total": self.batches})
            emit(self.run_batch(batch_index))
        status("completed", {"committed_batches": self.batches, "seq": self.batches})


def read_commands(path) -> str:
    """Read the spike's command file; a missing/unreadable file means "run"."""
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError):
        return "run"
    command = str((value or {}).get("command") or "run")
    return command if command in {"run", "pause", "exit"} else "run"


def write_json_atomic(path, payload: dict) -> None:
    import os
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
