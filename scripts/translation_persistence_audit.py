#!/usr/bin/env python3
"""Quantify translation-stage persistence cost before touching it.

The brief asks for measured evidence, not an opinion, about two writes that
happen once per translation batch:

* ``state_commit`` -- :func:`core.save_job_state` atomically rewrites the whole
  ``state.json`` (the single authoritative ground truth);
* ``tm_commit`` -- :func:`core.save_tm` rewrites the whole project translation
  memory JSON.

Both are O(document) writes executed O(batch) times, so the interesting numbers
are per-batch wall time and *cumulative* bytes written versus the final file
size (the write-amplification factor).  This script produces those numbers with
the profiler stages added in the previous round, using a deterministic fake LLM
so no API budget is spent and the run is reproducible.

It also decomposes a single ``save_job_state`` into the eleven sub-phases the
persistence brief names (``folith.persistence_profiler``), so the report
can say *which* part of a save is expensive instead of treating it as one
bucket.  Finally it records stable invariants -- serialization count, cumulative
bytes, traversal counts, amplification -- so a later comparison does not rest on
wall-clock alone.

Nothing here changes behaviour.  It only measures.

Run:

    python3 scripts/translation_persistence_audit.py \
        --out tmp/translation-persistence-audit \
        --sizes 100,1000,5000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402

# The harness must run unchanged against a pre-Phase-A revision (an A/B baseline
# checked out with ``git worktree``), where the fine-grained profiler and the
# cadence helper do not exist yet.  Missing them degrades to "no sub-phase
# table, once-per-batch snapshots" instead of an ImportError.
try:  # pragma: no cover - exercised by the A/B harness, not by unit tests
    from folith import persistence_profiler  # noqa: E402
except ImportError:  # pragma: no cover
    persistence_profiler = None

# The state journal arrived with Phase B; a revision older than that must still
# be measurable, and then the journal simply does not exist to be counted.
try:  # pragma: no cover - exercised by the A/B harness, not by unit tests
    from folith.storage import state_journal  # noqa: E402
except ImportError:  # pragma: no cover
    state_journal = None
from folith.performance import PipelineProfiler  # noqa: E402


REVIEW_SYSTEM_MARKER = "独立的翻译审校专家"


def _fake_llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
    """Deterministic provider stub.

    The review protocol expects a JSON object with ``findings`` /
    ``evidence_requests``; everything else is a numbered translation request and
    gets an equal-length array back.

    Targets are written in the target language on purpose: a target that still
    contains the source text fails deterministic QA and is never promoted into
    translation memory, which would silently skip the ``tm_commit`` path this
    audit exists to measure.
    """
    if REVIEW_SYSTEM_MARKER in (system_prompt or ""):
        return json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    results = []
    for line in lines:
        ordinal = line.split(".", 1)[0].strip()
        results.append(f"第{ordinal}段记录了手稿在同年冬季之前经由北方各省的档案南运。")
    return json.dumps(results, ensure_ascii=False)


def _build_segments(count: int) -> list:
    """Synthetic prose that survives sentence segmentation as one unit each."""
    return [
        f"Segment {index} records that the manuscript travelled north through "
        f"the provincial archive before the winter of the same year."
        for index in range(count)
    ]


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _translation_digest(pairs: list) -> str:
    """Digest the persisted translation truth fields, in document order."""
    return _digest([
        {key: pair.get(key) for key in (
            "source", "initial_target", "target", "accepted_target",
            "reviewed", "review_status", "target_provenance")}
        for pair in pairs
    ])


def _invariants(state: dict, *, segments: list, batch_size: int) -> dict:
    """Stable correctness signals that must not move because of a perf change."""
    pairs = state.get("pairs") or []
    findings = state.get("findings") or []
    tm_file = core.tm_path(core.tm_project_id(state))
    tm_entries = 0
    tm_digest = _digest({})
    if tm_file.is_file():
        try:
            tm = json.loads(tm_file.read_text(encoding="utf-8"))
            tm_entries = len(tm)
            tm_digest = _digest({key: tm[key] for key in sorted(tm)})
        except Exception:
            pass

    # Resume invariant: re-plan the batches against the committed pairs.  When
    # the run is complete there must be nothing left to recompute.
    paras = state.get("paras") or []
    batches = core.make_batches(
        paras, batch_size=batch_size,
        max_chars=core.TRANSLATION_MAX_BATCH_CHARS,
        semantic_units=state.get("semantic_units") or None)
    cum_end, start_batch = 0, 0
    for bi, batch in enumerate(batches):
        cum_end += len(batch)
        if cum_end > len(pairs):
            start_batch = bi
            break
    else:
        start_batch = len(batches)

    source_order_ok = all(
        (pairs[i].get("source") or "") == segments[i] for i in range(len(pairs))
    )
    return {
        "pair_count": len(pairs),
        "segment_count": len(segments),
        "all_segments_committed": len(pairs) == len(segments),
        "source_order_preserved": bool(source_order_ok),
        "translation_digest": _translation_digest(pairs),
        "findings_count": len(findings),
        "findings_digest": _digest(findings),
        "review_evidence_count": len(state.get("review_evidence") or []),
        "review_truth_version": (state.get("translation_truth") or {}).get("version"),
        "reviewed_count": sum(1 for pair in pairs if pair.get("reviewed")),
        "reviewed_clean_count": sum(
            1 for pair in pairs if pair.get("review_status") == "reviewed_clean"),
        "tm_entry_count": tm_entries,
        "tm_digest": tm_digest,
        "resume_batches_remaining": len(batches) - start_batch,
        "resume_recompute_segments": max(0, len(segments) - len(pairs)),
        "delivery_status": state.get("delivery_status"),
    }


class _WriteMeter:
    """Wrap the two persistence entry points and account bytes actually written.

    Byte counts come from ``stat`` on the real artifact after each write, so the
    number reflects what hit the disk rather than a re-serialized guess.
    """

    def __init__(self) -> None:
        self.state_writes = 0
        self.state_bytes = 0
        self.state_wall_s = 0.0
        self.tm_writes = 0
        self.tm_bytes = 0
        self.tm_wall_s = 0.0
        self.journal_appends = 0
        self.journal_bytes = 0
        self.journal_wall_s = 0.0
        self._original_state = None
        self._original_tm = None
        self._original_append = None

    def __enter__(self) -> "_WriteMeter":
        self._original_state = core.save_job_state
        self._original_tm = core.save_tm
        meter = self

        if state_journal is not None:
            self._original_append = state_journal.append_mutation

            def counting_append(job_id, record, output_dir=None):
                begin = time.perf_counter()
                result = meter._original_append(job_id, record,
                                                output_dir=output_dir)
                meter.journal_wall_s += time.perf_counter() - begin
                meter.journal_appends += 1
                meter.journal_bytes += int(result[1])
                return result

            state_journal.append_mutation = counting_append

        def counting_state(job_id, state):
            begin = time.perf_counter()
            meter._original_state(job_id, state)
            meter.state_wall_s += time.perf_counter() - begin
            meter.state_writes += 1
            try:
                meter.state_bytes += (core.job_dir(job_id) / "state.json").stat().st_size
            except OSError:
                pass

        def counting_tm(tm, project_id=None):
            begin = time.perf_counter()
            meter._original_tm(tm, project_id)
            meter.tm_wall_s += time.perf_counter() - begin
            meter.tm_writes += 1
            try:
                meter.tm_bytes += core.tm_path(project_id).stat().st_size
            except OSError:
                pass

        core.save_job_state = counting_state
        core.save_tm = counting_tm
        return self

    def __exit__(self, *_exc) -> bool:
        core.save_job_state = self._original_state
        core.save_tm = self._original_tm
        if self._original_append is not None and state_journal is not None:
            state_journal.append_mutation = self._original_append
        return False


def _measure(segments: list, *, batch_size: int, workdir: Path,
             profile: bool = True) -> dict:
    job_id = f"persist-{len(segments)}"
    core.OUTPUT_DIR = workdir
    (workdir / job_id).mkdir(parents=True, exist_ok=True)

    state = core.new_job_state("bench.txt")
    state["paras"] = segments
    core.save_job_state(job_id, state)

    profiler = PipelineProfiler(workdir / job_id / "performance.json",
                                run_id=job_id)
    core.call_llm = _fake_llm

    if profile and persistence_profiler is not None:
        persistence_profiler.reset()
        persistence_profiler.enable()

    meter = _WriteMeter()
    begin = time.perf_counter()
    # ``profiler=`` arrived with the translation instrumentation; a revision
    # older than that must still be measurable for an A/B baseline.
    stage_kwargs = {}
    try:
        import inspect

        if "profiler" in inspect.signature(core.translate_stage).parameters:
            stage_kwargs["profiler"] = profiler
    except (TypeError, ValueError):  # pragma: no cover - defensive
        stage_kwargs["profiler"] = profiler
    with meter:
        result = core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=True, use_tm=True, batch_size=batch_size,
            translation_concurrency=1, **stage_kwargs)
    wall = time.perf_counter() - begin

    subphases = (persistence_profiler.snapshot()
                 if (profile and persistence_profiler is not None) else {})
    if persistence_profiler is not None:
        persistence_profiler.disable()

    snapshot = profiler.snapshot()
    stages = snapshot["stages"]

    def stage(name: str) -> dict:
        record = stages.get(name) or {}
        per_item = (record.get("per_item") or {}).get("wall_time_s") or {}
        return {
            "status": record.get("status"),
            "wall_time_s": round(float(record.get("wall_time_s") or 0.0), 4),
            "batch_count": int(record.get("completed_batch_count") or 0),
            "per_batch_avg_s": round(float(per_item.get("avg") or 0.0), 6),
            "per_batch_max_s": round(float(per_item.get("max") or 0.0), 6),
        }

    state_commit = stage("state_commit")
    tm_commit = stage("tm_commit")

    pairs = result.get("pairs") or []
    batches = len(pairs) // batch_size + (1 if len(pairs) % batch_size else 0)
    tm_promoted = sum(
        1 for pair in pairs
        if pair.get("review_status") == "reviewed_clean" and not pair.get("from_tm")
    )

    final_state_bytes = (workdir / job_id / "state.json").stat().st_size
    tm_file = core.tm_path(core.tm_project_id(state))
    final_tm_bytes = tm_file.stat().st_size if tm_file.is_file() else 0

    def amplification(total: int, final: int) -> float:
        return round(total / final, 2) if final else 0.0

    # Re-read through the authoritative loader: the invariants must describe
    # what a resuming process would actually see, not the in-memory object.
    persisted = core.load_job_state(job_id) or state

    cadence_fn = getattr(core, "state_snapshot_cadence", None)
    window_fn = getattr(core, "state_snapshot_crash_window", None)
    if cadence_fn is None:  # pre-Phase-A baseline: one snapshot per batch
        cadence = {"every_batches": 1, "max_interval_s": 0.0}
        crash_window = batch_size
    else:
        cadence = cadence_fn(total_batches=batches)
        crash_window = window_fn(batch_size=batch_size, total_batches=batches)

    return {
        "segment_count": len(segments),
        "batch_size": batch_size,
        "batch_count": batches,
        "wall_time_s": round(wall, 4),
        "tm_promoted_segments": tm_promoted,
        "snapshot_cadence": cadence,
        "snapshot_crash_window_segments": crash_window,
        "state_commit": {
            **state_commit,
            "save_calls": meter.state_writes,
            "bytes_written": meter.state_bytes,
            "final_file_bytes": final_state_bytes,
            "write_amplification": amplification(meter.state_bytes, final_state_bytes),
            "total_wall_s": round(meter.state_wall_s, 4),
            "share_of_run": round(meter.state_wall_s / wall, 4) if wall else 0.0,
        },
        "tm_commit": {
            **tm_commit,
            "save_calls": meter.tm_writes,
            "bytes_written": meter.tm_bytes,
            "final_file_bytes": final_tm_bytes,
            "write_amplification": amplification(meter.tm_bytes, final_tm_bytes),
            "total_wall_s": round(meter.tm_wall_s, 4),
            "share_of_run": round(meter.tm_wall_s / wall, 4) if wall else 0.0,
        },
        "state_journal": {
            "enabled": bool(state_journal is not None),
            "appends": meter.journal_appends,
            "bytes_written": meter.journal_bytes,
            "total_wall_s": round(meter.journal_wall_s, 4),
            "share_of_run": round(meter.journal_wall_s / wall, 4) if wall else 0.0,
            "final_file_bytes": (
                state_journal.size(job_id) if state_journal is not None else 0),
        },
        # The number that actually answers "how much did this run write":
        # every byte of snapshot, memory and journal, over the artifact a user
        # ends up with.
        "total_durable": {
            "bytes_written": meter.state_bytes + meter.tm_bytes
            + meter.journal_bytes,
            "write_amplification": amplification(
                meter.state_bytes + meter.tm_bytes + meter.journal_bytes,
                final_state_bytes),
        },
        "subphases": subphases,
        "stages": {
            name: {
                "status": record.get("status"),
                "wall_time_s": round(float(record.get("wall_time_s") or 0.0), 4),
                "batch_count": int(record.get("completed_batch_count") or 0),
                "share_of_run": round(
                    float(record.get("wall_time_s") or 0.0) / wall, 4) if wall else 0.0,
            }
            for name, record in stages.items()
        },
        "invariants": _invariants(persisted, segments=segments,
                                  batch_size=batch_size),
    }


def _phase_table(results: list) -> list:
    """Flatten the per-size sub-phase snapshots into report rows."""
    rows = []
    for item in results:
        phases = (item.get("subphases") or {}).get("phases") or {}
        save_calls = (item.get("subphases") or {}).get("save_calls") or 0
        if save_calls:
            rows.append({
                "segments": item["segment_count"], "phase": "__save_calls__",
                "call_count": save_calls, "total_s": 0.0, "avg_ms": 0.0,
                "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0,
                "MB_serialized": 0.0, "MB_written": 0.0, "items_traversed": 0,
            })
        for name, record in phases.items():
            if not record.get("call_count"):
                continue
            rows.append({
                "segments": item["segment_count"],
                "phase": name,
                "call_count": record["call_count"],
                "total_s": record["total_wall_s"],
                "avg_ms": round(record["avg_s"] * 1000, 4),
                "p50_ms": round(record["p50_s"] * 1000, 4),
                "p95_ms": round(record["p95_s"] * 1000, 4),
                "max_ms": round(record["max_s"] * 1000, 4),
                "MB_serialized": round(record["bytes_serialized"] / 1e6, 3),
                "MB_written": round(record["bytes_written"] / 1e6, 3),
                "items_traversed": record["items_traversed"],
            })
    return rows


def _print_phase_table(results: list) -> None:
    for item in results:
        phases = (item.get("subphases") or {}).get("phases") or {}
        if not phases:
            continue
        print(f"\n--- save_job_state sub-phases @ {item['segment_count']} segments "
              f"({item['subphases'].get('save_calls')} saves) ---")
        header = (f"{'phase':>28} {'n':>6} {'total_s':>9} {'avg_ms':>9} "
                  f"{'p50_ms':>9} {'p95_ms':>9} {'MBser':>8} {'MBwr':>8} {'items':>10}")
        print(header)
        print("-" * len(header))
        for name, record in phases.items():
            if not record.get("call_count"):
                continue
            print(f"{name:>28} {record['call_count']:>6} "
                  f"{record['total_wall_s']:>9.4f} "
                  f"{record['avg_s'] * 1000:>9.4f} "
                  f"{record['p50_s'] * 1000:>9.4f} "
                  f"{record['p95_s'] * 1000:>9.4f} "
                  f"{record['bytes_serialized'] / 1e6:>8.3f} "
                  f"{record['bytes_written'] / 1e6:>8.3f} "
                  f"{record['items_traversed']:>10}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/translation-persistence-audit")
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--no-profile-phases", action="store_true",
                        help="skip the fine-grained save sub-phase profiler")
    args = parser.parse_args()

    sizes = [int(value) for value in str(args.sizes).split(",") if value.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    original_output = core.OUTPUT_DIR
    results = []
    try:
        for size in sizes:
            with tempfile.TemporaryDirectory(prefix="persist-audit-") as tmp:
                results.append(_measure(_build_segments(size),
                                        batch_size=args.batch_size,
                                        workdir=Path(tmp),
                                        profile=not args.no_profile_phases))
    finally:
        core.OUTPUT_DIR = original_output

    rows = []
    for item in results:
        rows.append({
            "segments": item["segment_count"],
            "batches": item["batch_count"],
            "run_s": item["wall_time_s"],
            "snapshots": item["state_commit"]["save_calls"],
            "crash_window_segments": item["snapshot_crash_window_segments"],
            "state_commit_s": item["state_commit"]["total_wall_s"],
            "state_per_batch_ms": round(
                item["state_commit"]["per_batch_avg_s"] * 1000, 3),
            "state_MB_written": round(
                item["state_commit"]["bytes_written"] / 1e6, 2),
            "state_final_MB": round(
                item["state_commit"]["final_file_bytes"] / 1e6, 3),
            "state_amplification": item["state_commit"]["write_amplification"],
            "tm_commit_s": item["tm_commit"]["total_wall_s"],
            "tm_per_batch_ms": round(
                item["tm_commit"]["per_batch_avg_s"] * 1000, 3),
            "tm_promoted_segments": item["tm_promoted_segments"],
            "tm_MB_written": round(item["tm_commit"]["bytes_written"] / 1e6, 2),
            "tm_final_MB": round(item["tm_commit"]["final_file_bytes"] / 1e6, 3),
            "tm_amplification": item["tm_commit"]["write_amplification"],
            "journal_appends": item["state_journal"]["appends"],
            "journal_MB_written": round(
                item["state_journal"]["bytes_written"] / 1e6, 2),
            "journal_enabled": item["state_journal"]["enabled"],
            "total_MB_written": round(
                item["total_durable"]["bytes_written"] / 1e6, 2),
            "total_amplification": item["total_durable"]["write_amplification"],
        })

    unmeasured = [row["segments"] for row in rows
                  if row["tm_commit_s"] <= 0.0 and row["tm_promoted_segments"] > 0]
    report = {
        "fixture": {
            "sizes": sizes,
            "batch_size": args.batch_size,
            "concurrency": 1,
            "review": True,
            "fake_llm": "deterministic",
            "note": ("state.json is the single authoritative ground truth; this "
                     "audit quantifies its cost and does not change the contract."),
        },
        "results": results,
        "summary_table": rows,
        "phase_table": _phase_table(results),
        "invariants": {item["segment_count"]: item["invariants"]
                       for item in results},
        "tm_commit_measured": not unmeasured,
        "interpretation": (
            "Write amplification = cumulative bytes written / final artifact "
            "size. It scales with batch count, so it grows linearly with "
            "document length for a fixed batch size."),
    }
    (out_dir / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    header = (f"{'segments':>9} {'batches':>8} {'run_s':>8} {'snaps':>7} "
              f"{'crash':>6} {'state_s':>8} {'st/batch_ms':>11} {'st_MB':>8} "
              f"{'st_x':>6} {'tm_s':>7} {'tm_MB':>7} {'tm_x':>6} "
              f"{'jrn_MB':>7} {'tot_MB':>7} {'tot_x':>6}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['segments']:>9} {row['batches']:>8} {row['run_s']:>8.2f} "
              f"{row['snapshots']:>7} {row['crash_window_segments']:>6} "
              f"{row['state_commit_s']:>8.2f} {row['state_per_batch_ms']:>11.2f} "
              f"{row['state_MB_written']:>8.2f} {row['state_amplification']:>6.1f} "
              f"{row['tm_commit_s']:>7.2f} {row['tm_MB_written']:>7.2f} "
              f"{row['tm_amplification']:>6.1f} "
              f"{row['journal_MB_written']:>7.2f} "
              f"{row['total_MB_written']:>7.2f} "
              f"{row['total_amplification']:>6.1f}")
    if unmeasured:
        print(f"\nWARNING: tm_commit not observed for sizes {unmeasured} "
              f"despite TM-eligible segments; the audit is incomplete.")
    _print_phase_table(results)
    print("\n--- invariants ---")
    for size, inv in report["invariants"].items():
        print(f"{size:>6} segments: pairs={inv['pair_count']} "
              f"order_ok={inv['source_order_preserved']} "
              f"complete={inv['all_segments_committed']} "
              f"reviewed={inv['reviewed_count']} "
              f"resume_remaining={inv['resume_batches_remaining']} "
              f"tm={inv['tm_entry_count']} "
              f"tx={inv['translation_digest']}")
    print(f"\nwrote {out_dir / 'audit.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
