"""Batch-level incremental source-cleanup checkpoint contracts.

The previous checkpoint rewrote one growing ``results`` payload after every
successful batch (O(n²) bytes).  These tests pin the replacement: one file per
completed batch, a constant-size manifest, and unchanged fingerprint / ordered
merge / failed-batch fallback / resume semantics.
"""
from __future__ import annotations

import json
import re

import core
from folith import source_cleanup


def _response_for(kwargs, user):
    """Same shape the real protocol expects: one row per requested item."""
    schema = (kwargs.get("response_format") or {}).get("json_schema") or {}
    expected = int(((schema.get("schema") or {}).get("properties") or {})
                   .get("segments", {}).get("maxItems") or 0)
    indices = [int(match) for match in re.findall(r"(?m)^(\d+)\. ", user)]
    rows = []
    for local in range(expected):
        global_index = indices[local] if local < len(indices) else local
        rows.append({"index": local, "text": f"item {global_index} complete.",
                     "break_after": True})
    return json.dumps({"segments": rows})


def _llm_factory(calls, fail_on=()):
    def llm(_provider, _api_key, _model, _system, user, **kwargs):
        calls.append(user)
        if len(calls) in fail_on:
            raise RuntimeError("HTTP 429")
        return _response_for(kwargs, user)
    return llm


def test_each_successful_batch_writes_only_its_own_file(tmp_path):
    calls = []
    paragraphs = [f"item {index}." for index in range(5)]
    _cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model", call_llm_fn=_llm_factory(calls),
        max_batch_items=1, confidence_by_index=[0] * len(paragraphs),
        parallelism=2, checkpoint_dir=tmp_path)

    assert warnings == []
    assert len(calls) == 5
    root = tmp_path / "source_cleanup_checkpoint"
    batch_files = sorted(path.name for path in (root / "batches").glob("batch-*.json"))
    assert batch_files == [f"batch-{index:05d}.json" for index in range(5)]

    stats = metadata["checkpoint_stats"]
    assert stats["batch_writes"] == 5
    # manifest is a constant-size run record, not one entry per batch
    assert stats["manifest_writes"] == 3  # start + mid-run + final
    assert stats["batch_bytes_written"] > 0

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_batches"] == 5
    assert manifest["status"] == "completed"
    assert "results" not in manifest


def test_resume_reads_sharded_batches_and_keeps_order(tmp_path):
    paragraphs = [f"item {index}." for index in range(4)]
    first_calls = []
    core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model",
        call_llm_fn=_llm_factory(first_calls), max_batch_items=2,
        confidence_by_index=[0] * len(paragraphs), parallelism=1,
        checkpoint_dir=tmp_path)
    assert len(first_calls) == 2  # 4 items / 2 per batch

    resumed_calls = []
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model",
        call_llm_fn=_llm_factory(resumed_calls), max_batch_items=2,
        confidence_by_index=[0] * len(paragraphs), parallelism=1,
        checkpoint_dir=tmp_path)
    assert resumed_calls == []  # every batch was already durable
    assert warnings == []
    assert metadata["resumed_batches"] == 2
    assert len(cleaned) == 4


def test_legacy_single_file_checkpoint_is_still_honoured(tmp_path):
    """An in-flight job from before the change must not re-send paid batches."""
    paragraphs = [f"item {index}." for index in range(3)]
    # Build the fingerprint the same way the module does, by running once and
    # then converting the sharded output into the legacy shape.
    core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model", call_llm_fn=_llm_factory([]),
        max_batch_items=1, confidence_by_index=[0] * 3, parallelism=1,
        checkpoint_dir=tmp_path)
    root = tmp_path / "source_cleanup_checkpoint"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    results = {}
    for path in sorted((root / "batches").glob("batch-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        results[str(payload["batch_index"])] = payload["result"]
    (tmp_path / "source_cleanup_checkpoint.json").write_text(json.dumps({
        "version": source_cleanup.CHECKPOINT_VERSION,
        "fingerprint": manifest["fingerprint"],
        "batch_count": 3,
        "selected_indices": [0, 1, 2],
        "status": "running",
        "results": results,
    }), encoding="utf-8")
    # Remove the sharded files so only the legacy path can satisfy the resume.
    for path in (root / "batches").glob("batch-*.json"):
        path.unlink()

    resumed_calls = []
    _cleaned, metadata, _warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model",
        call_llm_fn=_llm_factory(resumed_calls), max_batch_items=1,
        confidence_by_index=[0] * 3, parallelism=1, checkpoint_dir=tmp_path)
    assert resumed_calls == []
    assert metadata["resumed_batches"] == 3


def test_failed_batch_falls_back_and_is_not_checkpointed(tmp_path):
    paragraphs = [f"item {index}." for index in range(2)]
    calls = []
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model",
        call_llm_fn=_llm_factory(calls, fail_on=(1,)), max_batch_items=1,
        confidence_by_index=[0, 0], parallelism=1, max_retries=0,
        checkpoint_dir=tmp_path)
    assert cleaned[0] == "item 0."  # source text preserved on failure
    assert len(warnings) == 1
    root = tmp_path / "source_cleanup_checkpoint"
    batch_files = sorted(path.name for path in (root / "batches").glob("batch-*.json"))
    # only the successful batch is durable; the failed one must be retried
    assert batch_files == ["batch-00001.json"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["failed_batches"] == 1
