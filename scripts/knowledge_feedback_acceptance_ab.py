#!/usr/bin/env python3
"""Production acceptance A/B for the knowledge-feedback cadence policy.

Answers one question with live-model translation evidence:

    does interval-4 (signal + checkpoint) feedback produce translation quality
    equivalent to mandatory per-batch feedback?

Both arms run the **real** ``core.translate_stage`` over the committed workload
(239 real segments of *OECD The Space Economy at a Glance 2026*) against the
live provider.  Everything except the feedback cadence is identical: same
document, provider, model, translation prompt, glossary, batching, concurrency,
starting project state and review policy.  No caches are involved — the exact
translation cache is off and a fresh job state is built per arm, so the two arms
cannot inherit each other's work.

Quality is compared four ways:

1. deterministic — terminology compliance, empty/omitted targets, number and
   date survival, cross-batch rendering consistency;
2. entity continuity — for every proper name the document actually repeats,
   whether both arms render it consistently;
3. review — the production reviewer is run over both arms' output with the same
   packet and the same model, and findings are compared by severity;
4. the translations themselves, batch by batch.

Secrets come from the application provider config and are never printed.

Run
---
    python3 scripts/knowledge_feedback_acceptance_ab.py --phase translate --out tmp/kf-acceptance
    python3 scripts/knowledge_feedback_acceptance_ab.py --phase review    --out tmp/kf-acceptance
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import entity_registry as _entities  # noqa: E402
from folith import terminology as _terminology  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith import usage as _usage  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"
DEFAULT_CONFIG = Path.home() / "Library/Application Support/Folith/data/provider_config.json"

ARMS = (
    ("A_mandatory", 1),
    ("B_interval4", None),
    # Repeatability control: identical configuration to A.  Translation samples
    # at temperature 0.3, so this arm measures the noise floor every aggregate
    # quality metric must be read against.
    ("A2_mandatory_repeat", 1),
)


def load_provider(explicit: Optional[str]) -> Dict[str, str]:
    path = Path(explicit) if explicit else DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(f"no live provider config at {path}; pass --provider-config")
    data = json.loads(path.read_text(encoding="utf-8"))
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not provider or not api_key:
        raise SystemExit("provider config is missing provider/api_key")
    return {"provider": provider, "model": model, "api_key": api_key}


# ---------------------------------------------------------------------------
# Shared instrumentation
# ---------------------------------------------------------------------------


class CallMeter:
    """Counts live provider calls and characters, without ever logging the key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self.calls: List[Dict[str, Any]] = []
        self._counter = 0

    def set_role(self, role: str) -> None:
        self._local.role = role

    @staticmethod
    def classify(system_prompt: str) -> str:
        """Same markers the deterministic benchmark harness classifies with."""
        text = system_prompt or ""
        if "翻译流知识抽取器" in text:
            return "auxiliary"
        if "独立的翻译审校专家" in text:
            return "review"
        if "你是一位专业翻译人员" in text:
            return "translation"
        if "问题清单" in text:
            return "repair"
        return "unknown"

    def wrap(self, inner):
        def call(provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
            started = time.perf_counter()
            result = inner(provider, api_key, model, system_prompt, user_prompt,
                           temperature=temperature, **kwargs)
            elapsed = time.perf_counter() - started
            with self._lock:
                self._counter += 1
                self.calls.append({
                    "index": self._counter,
                    "role": self.classify(system_prompt),
                    "system_chars": len(system_prompt or ""),
                    "user_chars": len(user_prompt or ""),
                    "completion_chars": len(result or ""),
                    "latency_s": round(elapsed, 4),
                })
            return result
        return call


# ---------------------------------------------------------------------------
# One arm
# ---------------------------------------------------------------------------


def build_state(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """Fresh project state: no inherited knowledge, no inherited progress."""
    state = core.new_job_state(fixture.get("filename") or "acceptance.docx")
    state["paras"] = list(fixture["segments"])
    state["target_lang"] = fixture.get("target_lang") or "简体中文"
    state["style_rules"] = fixture.get("style_rules") or ""
    state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
    state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
    state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
    state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
    # Deliberately empty: the fixture ships the *final* registry of the original
    # run, and pre-seeding it would hand both arms knowledge they must earn.
    state["entity_registry"] = []
    state["knowledge_candidates"] = []
    state["translation_continuity"] = []
    state["pairs"] = []
    return state


def run_arm(label: str, interval: Optional[int], fixture: Dict[str, Any],
            provider: Dict[str, str], *, output_dir: Path, concurrency: int,
            limit: Optional[int]) -> Dict[str, Any]:
    job_id = f"ab-{label}"
    core.OUTPUT_DIR = output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    state = build_state(fixture)
    if limit:
        state["paras"] = state["paras"][:limit]
    core.save_job_state(job_id, state)

    meter = CallMeter()
    meter.set_role("translation")
    old_call, old_output = core.call_llm, core.OUTPUT_DIR
    core.call_llm = meter.wrap(core.call_llm)
    core.OUTPUT_DIR = output_dir
    started = time.perf_counter()
    error = None
    try:
        result = core.translate_stage(
            state, job_id, list(fixture.get("glossary") or []),
            provider["provider"], provider["api_key"], provider["model"],
            state["target_lang"], state["style_rules"],
            enable_review=False, use_tm=False, use_translation_cache=False,
            document_profile=state["document_profile"],
            translation_concurrency=concurrency,
            knowledge_feedback_interval=interval,
        )
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        error = f"{type(exc).__name__}: {exc}"
        result = state
    finally:
        wall = time.perf_counter() - started
        core.call_llm, core.OUTPUT_DIR = old_call, old_output

    pairs = (result or {}).get("pairs") or []
    payload = {
        "label": label,
        "interval_override": interval,
        "wall_s": round(wall, 2),
        "error": error,
        "pairs": [
            {"source": p.get("source"), "target": p.get("target"),
             "review_status": p.get("review_status")}
            for p in pairs if isinstance(p, dict)
        ],
        "knowledge_policy": (result or {}).get("knowledge_feedback_policy") or {},
        "knowledge_candidates": (result or {}).get("knowledge_candidates") or [],
        "entity_registry": (result or {}).get("entity_registry") or [],
        "knowledge_events": (result or {}).get("knowledge_events") or [],
        "findings": [f for f in ((result or {}).get("findings") or [])
                     if isinstance(f, dict)],
        "llm_usage": (result or {}).get("llm_usage") or {},
        "calls": meter.calls,
        "state_digest": {
            "target_digest": hashlib.sha256(json.dumps(
                [p.get("target") for p in pairs], ensure_ascii=False,
                sort_keys=True).encode("utf-8")).hexdigest()[:16],
            "candidate_count": len((result or {}).get("knowledge_candidates") or []),
            "entity_count": len((result or {}).get("entity_registry") or []),
        },
    }
    return payload


# ---------------------------------------------------------------------------
# Quality evaluation
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|bn|billion|million|m\b|km|kg)?",
                       re.IGNORECASE)


def _numbers(text: str) -> List[str]:
    out = []
    for match in NUMBER_RE.finditer(str(text or "")):
        token = re.sub(r"\s+", "", match.group(0)).lower()
        if any(ch.isdigit() for ch in token) and len(token) > 1:
            out.append(token)
    return out


def evaluate_arm(fixture: Dict[str, Any], arm: Dict[str, Any]) -> Dict[str, Any]:
    sources = [str(s) for s in fixture["segments"]]
    targets = [str(p.get("target") or "") for p in arm["pairs"]]
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"

    deterministic = core.check_translation_batch(
        sources, targets, glossary, target_lang)
    severity = collections.Counter(
        str(item.get("severity")) for item in deterministic
        if isinstance(item, dict))
    terminology_violations = sum(
        count for name, count in severity.items()
        if name in ("blocking", "actionable"))

    missing_numbers = 0
    number_total = 0
    for source, target in zip(sources, targets):
        for token in _numbers(source):
            number_total += 1
            if token not in re.sub(r"\s+", "", target).lower():
                missing_numbers += 1

    # Entity continuity: for every proper name the document really repeats,
    # how many distinct renderings did this arm produce where the name occurs?
    registry = _entities.EntityRegistry(arm.get("entity_registry") or [])
    entity_rows = []
    for record in registry.records:
        form = str(record.get("source_form") or "")
        occurrences = [int(i) for i in (record.get("occurrences") or [])
                       if isinstance(i, int)]
        if len(occurrences) < 2:
            continue
        renderings = set()
        for index in occurrences:
            if 0 <= index < len(targets) and targets[index].strip():
                renderings.add(targets[index].strip())
        entity_rows.append({
            "source_form": form,
            "occurrences": len(occurrences),
            "distinct_translated_segments": len(renderings),
            "preferred_target": record.get("preferred_target"),
        })

    # Cross-batch rendering consistency for repeated source terms that the arm
    # itself observed as knowledge candidates.
    repeated_term_rows = []
    for candidate in arm.get("knowledge_candidates") or []:
        source_form = str(candidate.get("source") or "")
        observed = [int(i) for i in (candidate.get("occurrences") or [])
                    if isinstance(i, int)]
        if len(observed) < 2:
            continue
        targets_seen = {targets[i].strip() for i in observed
                        if 0 <= i < len(targets) and targets[i].strip()}
        repeated_term_rows.append({
            "source": source_form,
            "occurrences": len(observed),
            "distinct_renderings": len(targets_seen),
        })

    return {
        "segments": len(targets),
        "empty_targets": sum(1 for t in targets if not t.strip()),
        "target_digest": arm["state_digest"]["target_digest"],
        "target_chars": sum(len(t) for t in targets),
        "terminology_violations": terminology_violations,
        "findings_by_severity": dict(sorted(severity.items())),
        "numbers_total": number_total,
        "numbers_missing_in_target": missing_numbers,
        "entity_rows": entity_rows,
        "entities_repeated": len(entity_rows),
        "repeated_terms": len(repeated_term_rows),
        "repeated_term_rows": repeated_term_rows,
        "multi_rendering_terms": sum(
            1 for row in repeated_term_rows if row["distinct_renderings"] > 1),
    }


def diff_arms(fixture: Dict[str, Any], a: Dict[str, Any],
              b: Dict[str, Any]) -> Dict[str, Any]:
    """Batch-by-batch translation difference summary."""
    at = [str(p.get("target") or "") for p in a["pairs"]]
    bt = [str(p.get("target") or "") for p in b["pairs"]]
    changed = [i for i, (x, y) in enumerate(zip(at, bt)) if x != y]
    return {
        "segments": len(at),
        "identical_segments": len(at) - len(changed),
        "changed_segments": len(changed),
        "changed_indexes": changed[:40],
    }


# ---------------------------------------------------------------------------
# Live review phase
# ---------------------------------------------------------------------------


class ReviewMeter(CallMeter):
    pass


def review_arm(fixture: Dict[str, Any], arm: Dict[str, Any],
               provider: Dict[str, str], *, workers: int,
               output_dir: Path) -> Dict[str, Any]:
    """Run the production reviewer over one arm's output, identically."""
    ref = arm["pairs"]
    sources = [str(s) for s in fixture["segments"]]
    targets = [str(p.get("target") or "") for p in ref]
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"
    style_rules = fixture.get("style_rules") or ""
    state = build_state(fixture)
    state["pairs"] = [
        {"source": sources[i], "target": targets[i], "reviewed": True,
         "review_status": "reviewed_clean", "target_provenance": "reviewed",
         "accepted_target": targets[i], "from_tm": False}
        for i in range(len(sources))
    ]

    batches = core.make_batches(
        list(fixture["segments"]), batch_size=core.BATCH_SIZE,
        max_chars=core.TRANSLATION_MAX_BATCH_CHARS,
        semantic_units=fixture.get("semantic_units") or None)
    offsets, cursor = [], 0
    for batch in batches:
        offsets.append(cursor)
        cursor += len(batch)

    meter = CallMeter()
    old_call = core.call_llm
    core.call_llm = meter.wrap(core.call_llm)
    rows: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def one(index: int) -> None:
        offset = offsets[index]
        batch = batches[index]
        seg_ids = [offset + j for j in range(len(batch))]
        src = [sources[i] for i in seg_ids]
        tgt = [targets[i] for i in seg_ids]
        section_profile = core._batch_section_profile(
            state.get("document_profile"), offset, len(batch))
        deterministic = core.check_translation_batch(
            src, tgt, glossary, target_lang, section_profile=section_profile)
        deterministic = core._globalize_batch_findings(deterministic, offset)
        selected, _ids = _terminology.select_glossary_for_segments(
            src, glossary, state.get("document_profile"), section_profile)
        glossary_text = _terminology.glossary_block(selected)
        review_context = core._runtime_review_context(
            state, offset, len(batch), glossary_text, style_rules, target_lang)
        batch_pairs = [
            {"source": src[j], "target": tgt[j], "initial_target": tgt[j],
             "target_provenance": "generated", "reviewed": False,
             "review_status": "not_reviewed", "from_tm": False}
            for j in range(len(batch))
        ]
        evidence_index = core._translation_evidence_index(
            state["paras"], state.get("pairs") or [], batch_pairs, glossary,
            state.get("document_profile"), state.get("document_synopsis"),
            state.get("section_digests"), [], blocked_source_indexes=set())
        packet = _evidence.build_runtime_review_packet(
            state, batch_pairs, seg_ids, glossary,
            deterministic_checks=deterministic, review_context=review_context)
        try:
            findings, failed, _trace = _evidence.review_translation_batch_with_evidence(
                src, tgt, glossary_text, style_rules, target_lang,
                provider["provider"], provider["api_key"], provider["model"],
                evidence_index, segment_ids=seg_ids,
                translation_core_packet=packet)
        except Exception as exc:  # noqa: BLE001
            findings, failed = [], True
            with lock:
                rows.append({"batch": index, "error": f"{type(exc).__name__}: {exc}"})
            return
        severities = collections.Counter(
            str(f.get("severity")) for f in (findings or [])
            if isinstance(f, dict))
        with lock:
            rows.append({
                "batch": index, "failed": bool(failed),
                "findings": len(findings or []),
                "severities": dict(severities),
                "detail": [
                    {"severity": f.get("severity"), "type": f.get("type"),
                     "summary": str(f.get("summary") or "")[:160],
                     "segment_id": f.get("segment_id")}
                    for f in (findings or []) if isinstance(f, dict)],
            })

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, range(len(batches))))
    finally:
        core.call_llm = old_call

    severity_totals: collections.Counter = collections.Counter()
    for row in rows:
        severity_totals.update(row.get("severities") or {})
    return {
        "label": arm["label"],
        "batches": len(batches),
        "review_calls": len(meter.calls),
        "findings_total": sum(row.get("findings", 0) for row in rows),
        "findings_by_severity": dict(sorted(severity_totals.items())),
        "failed_batches": sum(1 for row in rows if row.get("failed")),
        "rows": sorted(rows, key=lambda r: r.get("batch", 0)),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def phase_translate(args) -> int:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
    provider = load_provider(args.provider_config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    arms: Dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for label, interval in ARMS:
            if args.arms and label not in args.arms:
                continue
            print(f"[{label}] interval={interval} …", flush=True)
            arm = run_arm(label, interval, fixture, provider,
                          output_dir=Path(tmp) / label,
                          concurrency=args.concurrency, limit=None)
            print(f"[{label}] wall={arm['wall_s']}s calls={len(arm['calls'])} "
                  f"error={arm['error']}", flush=True)
            arms[label] = arm
            (out_dir / f"{label}.json").write_text(
                json.dumps(arm, ensure_ascii=False, indent=1), encoding="utf-8")

    report: Dict[str, Any] = {"fixture": str(args.fixture), "arms": {}}
    for label, arm in arms.items():
        report["arms"][label] = {
            "wall_s": arm["wall_s"],
            "calls": len(arm["calls"]),
            "calls_by_role": dict(collections.Counter(
                c["role"] for c in arm["calls"])),
            "llm_usage": arm["llm_usage"],
            "knowledge_policy": {
                key: arm["knowledge_policy"].get(key)
                for key in ("interval", "force_every_batch",
                            "observed_batches", "skipped_batches",
                            "signal_batches", "skip_reasons")},
            "state_digest": arm["state_digest"],
            "quality": evaluate_arm(fixture, arm),
        }
    if len(arms) == 2:
        report["diff"] = diff_arms(fixture, arms["A_mandatory"],
                                   arms["B_interval4"])
    path = out_dir / "translate.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(render_translate(report))
    print(f"\nwrote {path}")
    if any(arm["error"] for arm in arms.values()):
        for label, arm in arms.items():
            if arm["error"]:
                print(f"arm {label} error: {arm['error']}", file=sys.stderr)
        return 1
    return 0


def render_translate(report: Dict[str, Any]) -> str:
    lines = ["# Live translation A/B — mandatory vs interval-4 feedback\n"]
    lines.append("| Metric | A mandatory | B interval-4 |\n|---|---|---|")
    a = report["arms"].get("A_mandatory") or {}
    b = report["arms"].get("B_interval4") or {}
    if a and b:
        for key, label in (("wall_s", "wall time (s)"),
                           ("calls", "live provider calls")):
            lines.append(f"| {label} | {a[key]} | {b[key]} |")
        for role in ("translation", "auxiliary"):
            lines.append(f"| · {role} calls | "
                         f"{a['calls_by_role'].get(role, 0)} | "
                         f"{b['calls_by_role'].get(role, 0)} |")
        for label, path in (("input tokens", ("llm_usage", "prompt_tokens")),
                            ("output tokens", ("llm_usage", "completion_tokens"))):
            av = (a.get(path[0]) or {}).get(path[1], 0)
            bv = (b.get(path[0]) or {}).get(path[1], 0)
            lines.append(f"| {label} | {av} | {bv} |")
        lines.append("")
        lines.append("## Quality\n")
        lines.append("| Check | A mandatory | B interval-4 |\n|---|---|---|")
        qa, qb = a["quality"], b["quality"]
        for key, label in (
                ("segments", "segments"),
                ("empty_targets", "empty / omitted targets"),
                ("terminology_violations", "terminology violations"),
                ("numbers_missing_in_target", "numbers missing in target"),
                ("entities_repeated", "repeated proper names tracked"),
                ("multi_rendering_terms", "repeated terms with >1 rendering")):
            lines.append(f"| {label} | {qa[key]} | {qb[key]} |")
        lines.append(f"| target digest | `{qa['target_digest']}` | "
                     f"`{qb['target_digest']}` |")
        lines.append(f"| findings | {qa['findings_by_severity']} | "
                     f"{qb['findings_by_severity']} |")
        diff = report.get("diff") or {}
        lines.append("")
        lines.append(f"Segments with identical text across arms: "
                     f"**{diff.get('identical_segments')} / {diff.get('segments')}**"
                     f" (differing: {diff.get('changed_segments')}).")
    return "\n".join(lines)


def phase_review(args) -> int:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
    provider = load_provider(args.provider_config)
    out_dir = Path(args.out)
    result = {}
    for label, _interval in ARMS:
        path = out_dir / f"{label}.json"
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        arm = json.loads(path.read_text(encoding="utf-8"))
        print(f"[{label}] live review …", flush=True)
        result[label] = review_arm(fixture, arm, provider,
                                   workers=args.workers, output_dir=out_dir)
    path = out_dir / "review.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    lines = ["# Live review over both arms\n",
             "| Metric | A mandatory | B interval-4 |\n|---|---|---|"]
    for key in ("review_calls", "findings_total", "failed_batches"):
        lines.append(f"| {key} | {result['A_mandatory'][key]} | "
                     f"{result['B_interval4'][key]} |")
    lines.append(f"| findings by severity | "
                 f"{result['A_mandatory']['findings_by_severity']} | "
                 f"{result['B_interval4']['findings_by_severity']} |")
    text = "\n".join(lines)
    print(text)
    print(f"\nwrote {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--out", default="tmp/kf-acceptance")
    parser.add_argument("--phase", default="translate",
                        choices=("translate", "review"))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--arms", nargs="*", default=None)
    args = parser.parse_args()
    return phase_translate(args) if args.phase == "translate" else phase_review(args)


if __name__ == "__main__":
    raise SystemExit(main())
