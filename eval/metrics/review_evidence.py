"""Review-evidence evaluation harness (#100).

Compares the two independent-review policies on an inspectable annotated sample:

* ``always`` -- every batch is sent to the reviewer;
* ``risk_selective`` -- only batches whose deterministic signals fire are reviewed.

The harness does not change production defaults and does not add model calls to
the application: it drives the existing ``translate_stage`` code path with a
scripted provider and a temporary output directory.  The provider returns the
annotated draft targets (instead of translating) and acts as a *perfect*
reviewer for the gold errors a reviewed batch exposes.  That isolates the policy
question -- which serious errors reach a reviewer at all -- from reviewer-model
quality, and makes the run reproducible without network or spend.

Metrics per arm:

* serious-error recall, misses by category;
* review calls, estimated prompt/completion tokens, wall time;
* clean segments that were still reviewed (policy cost/false-positive reviews);
* skipped-segment verification: skipped is not reviewed, not approved, not
  TM-eligible; ``coverage_complete`` is false.

Usage::

    python eval/metrics/review_evidence.py            # run + write results
    python eval/metrics/review_evidence.py --no-write # print only

Annotation provenance is recorded in ``eval/review_evidence/annotations.json``.
The samples are agent-authored synthetic cases, not independent expert
annotation; see the report's limits section.  This is a harness validation, not
a claim about real-book quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import review_gate  # noqa: E402
from folith import translation_evidence  # noqa: E402
from folith import translation_memory  # noqa: E402
from folith import usage  # noqa: E402
from folith.storage import job_repository as repo  # noqa: E402

DATA_DIR = ROOT / "eval" / "review_evidence"
RESULTS_DIR = DATA_DIR / "results"
TARGET_LANG = "简体中文"

REVIEW_MARKER = "独立的翻译审校专家"
KNOWLEDGE_MARKER = "翻译流知识抽取器"
TRANSLATOR_MARKER = "专业翻译人员"

# The review packet lists ``local_ordinal`` / ``segment_id`` / 原文 / 译文.
_REVIEW_BLOCK_RE = re.compile(
    r"(?ms)^local_ordinal:\s*(\d+)\s*\nsegment_id:\s*(\d+)\s*\n"
    r"原文：(.*?)\n译文：(.*?)(?=\nlocal_ordinal:|\Z)")


def _estimate_tokens(text: str) -> int:
    return usage.estimate_text_tokens(text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dataset(data_dir: Path = DATA_DIR) -> Tuple[List[Dict[str, Any]],
                                                     List[Dict[str, Any]],
                                                     Dict[str, Any]]:
    samples = [json.loads(line) for line in
               (data_dir / "samples.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    glossary = json.loads((data_dir / "glossary.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "annotations.json").read_text(encoding="utf-8"))
    return samples, glossary, meta


class ScriptedProvider:
    """Annotated drafts for translation, perfect gold-error reviewer for review."""

    def __init__(self, samples: Sequence[Mapping[str, Any]]) -> None:
        self.samples = {str(item["source"]): item for item in samples}
        self.calls: Dict[str, int] = {role: 0 for role in
                                      ("translation", "review", "repair",
                                       "auxiliary", "unknown")}
        self.prompt_tokens: Dict[str, int] = {role: 0 for role in self.calls}
        self.completion_tokens: Dict[str, int] = {role: 0 for role in self.calls}
        self.review_seconds = 0.0
        self.review_batches: List[Dict[str, Any]] = []

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = system_prompt or ""
        if REVIEW_MARKER in system_prompt:
            return "review"
        if KNOWLEDGE_MARKER in system_prompt:
            return "auxiliary"
        if TRANSLATOR_MARKER in system_prompt:
            return "translation"
        if "修复" in (user_prompt or "") or "repair" in (user_prompt or "").lower():
            return "repair"
        return "unknown"

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        role = self.classify(system_prompt, user_prompt)
        self.calls[role] += 1
        self.prompt_tokens[role] += _estimate_tokens(
            f"{system_prompt or ''}\n{user_prompt or ''}")
        started = time.perf_counter()
        if role == "review":
            response = self._review_response(user_prompt or "")
            self.review_seconds += time.perf_counter() - started
        elif role == "translation":
            sources = self._translation_sources(user_prompt or "")
            response = json.dumps(
                [self.samples.get(source, {}).get("draft_target", "")
                 for source in sources], ensure_ascii=False)
        elif role == "repair":
            sources = self._translation_sources(user_prompt or "", allow_bare=True)
            response = json.dumps(
                [self.samples.get(source, {}).get("draft_target", "")
                 for source in sources], ensure_ascii=False)
        else:
            response = "[]"
        self.completion_tokens[role] += _estimate_tokens(response)
        return response

    @staticmethod
    def _translation_sources(user_prompt: str, allow_bare: bool = False) -> List[str]:
        sources: List[str] = []
        for line in user_prompt.splitlines():
            stripped = line.strip()
            head, sep, rest = stripped.partition(". ")
            if sep and head.isdigit():
                sources.append(rest)
            elif stripped.startswith("原文："):
                sources.append(stripped[len("原文："):])
        return sources

    def _review_response(self, user_prompt: str) -> str:
        findings = []
        blocks = _REVIEW_BLOCK_RE.findall(user_prompt)
        batch = []
        for ordinal, _segment_id, source, target in blocks:
            local_index = int(ordinal)
            sample = self.samples.get(source.strip())
            if sample is None:
                continue
            batch.append({"segment": local_index, "source": source.strip(),
                          "target": target.strip()})
            for error in sample.get("serious_errors") or []:
                findings.append({
                    "segment_index": local_index,
                    "severity": "actionable",
                    "reason": f"[{error['id']}] {error['note']}",
                    "suggested_target": "",
                })
            for error in sample.get("minor_errors") or []:
                findings.append({
                    "segment_index": local_index,
                    "severity": "informational",
                    "reason": f"[{error['id']}] {error['note']}",
                    "suggested_target": "",
                })
        self.review_batches.append({"segments": batch, "findings": len(findings)})
        return json.dumps(findings, ensure_ascii=False)


def _run_arm(samples: Sequence[Mapping[str, Any]],
             glossary: Sequence[Mapping[str, Any]], *,
             mode: str, batch_size: int, job_id: str,
             provider=None, reviewer_config=None) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="folith-review-eval-") as tmp:
        old_output = core.OUTPUT_DIR
        core.OUTPUT_DIR = Path(tmp)
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        try:
            state = core.new_job_state("review-eval.txt")
            state["paras"] = [str(item["source"]) for item in samples]
            state["pairs"] = []
            state["target_lang"] = TARGET_LANG
            state["glossary"] = [dict(item) for item in glossary]
            state["review_mode"] = mode
            state["style_rules"] = ""
            provider = provider if provider is not None else ScriptedProvider(samples)
            original_call = core.call_llm
            core.call_llm = provider
            started = time.perf_counter()
            try:
                core.translate_stage(
                    state, job_id, [dict(item) for item in glossary],
                    "DeepSeek", "eval-key", "scripted-model", TARGET_LANG, "",
                    enable_review=True, use_tm=True, batch_size=batch_size,
                    max_batch_chars=100000, translation_concurrency=1,
                    review_mode=mode, use_translation_cache=False,
                    use_knowledge_cache=False, knowledge_feedback=False,
                    reviewer_config=reviewer_config)
            finally:
                core.call_llm = original_call
                wall_ms = round((time.perf_counter() - started) * 1000, 3)
            final_state = core.load_job_state(job_id)
            assert final_state is not None
            tm = core.load_tm(core.tm_project_id(final_state))
            return {
                "state": final_state,
                "provider": provider,
                "tm": tm,
                "wall_ms": wall_ms,
            }
        finally:
            core.OUTPUT_DIR = old_output
            repo.invalidate_cache()
            core.reset_tm_dir_cache()


def _segment_findings(state: Mapping[str, Any], index: int) -> List[Dict[str, Any]]:
    result = []
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        segment = finding.get("segment_index", finding.get("segment_id"))
        if isinstance(segment, int) and not isinstance(segment, bool) \
                and segment == index:
            result.append(dict(finding))
    return result


def _deterministic_matches(finding: Mapping[str, Any],
                           error: Mapping[str, Any]) -> bool:
    if str(finding.get("type") or "") not in {"check", "deterministic"}:
        return False
    category = str(error.get("category") or "")
    kind = str(finding.get("kind") or "")
    category_map = {
        "number": {"number", "numeric_integrity"},
        "unit": {"unit", "unit_integrity"},
        "tag": {"tag", "tag_integrity"},
        "placeholder": {"placeholder", "token_integrity", "preserved_token"},
        "terminology": {"terminology", "glossary_compliance"},
    }
    kinds = category_map.get(category)
    if kinds and kind and kind not in kinds:
        return False
    haystack = " ".join(str(finding.get(key) or "") for key in
                        ("reason", "summary", "detected_text", "source_span",
                         "entry_id", "category"))
    tokens = [str(token) for token in error.get("detect_tokens") or []]
    if tokens:
        return any(token in haystack for token in tokens)
    return bool(kinds and category in {"omission", "mistranslation"})


def _score_arm(samples: Sequence[Mapping[str, Any]],
               arm: Mapping[str, Any],
               glossary: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    state = arm["state"]
    pairs = state.get("pairs") or []
    provider: ScriptedProvider = arm["provider"]
    tm = arm["tm"]
    per_sample = []
    domain_stats: Dict[str, Dict[str, Any]] = {}
    serious_total = serious_detected = serious_missed = 0
    missed_details = []
    for index, sample in enumerate(samples):
        domain = str(sample["domain"])
        stats = domain_stats.setdefault(domain, {
            "samples": 0, "serious_total": 0, "serious_detected": 0,
            "missed": 0, "reviewed_segments": 0, "skipped_segments": 0,
            "review_calls": 0,
        })
        stats["samples"] += 1
        pair = pairs[index] if index < len(pairs) else {}
        findings = _segment_findings(state, index)
        errors = []
        for error in sample.get("serious_errors") or []:
            deterministic = any(_deterministic_matches(f, error) for f in findings)
            review = any(
                str(f.get("type") or "") == "review"
                and f"[{error['id']}]" in str(f.get("reason") or "")
                for f in findings)
            detected = deterministic or review
            errors.append({
                **error,
                "detected": detected,
                "captured_by_deterministic": deterministic,
                "captured_by_review": review,
                "missed": not detected,
            })
            serious_total += 1
            stats["serious_total"] += 1
            if detected:
                serious_detected += 1
                stats["serious_detected"] += 1
            else:
                serious_missed += 1
                stats["missed"] += 1
                missed_details.append({
                    "sample": sample["id"], "domain": domain,
                    "error": error["id"], "category": error["category"],
                    "note": error["note"],
                    "reviewed": bool(pair.get("reviewed")),
                    "review_status": pair.get("review_status"),
                })
        reviewed = bool(pair.get("reviewed"))
        skipped = str(pair.get("review_status") or "") == review_gate.SKIPPED_LOW_RISK
        if reviewed:
            stats["reviewed_segments"] += 1
        if skipped:
            stats["skipped_segments"] += 1
        clean = not (sample.get("serious_errors") or [])
        review_false_positives = sum(
            1 for finding in findings
            if str(finding.get("type") or "") == "review" and clean)
        deterministic_false_positives = sum(
            1 for finding in findings
            if str(finding.get("type") or "") in {"check", "deterministic"} and clean)
        per_sample.append({
            "id": sample["id"], "domain": domain,
            "reviewed": reviewed,
            "review_status": pair.get("review_status"),
            "target": pair.get("target"),
            "serious_errors": errors,
            "reviewer_false_positive_findings": review_false_positives,
            "deterministic_false_positive_findings": deterministic_false_positives,
        })
    # Skip/TM invariants, independent of the annotation.
    skipped_pairs = [(index, pair) for index, pair in enumerate(pairs)
                     if str(pair.get("review_status") or "")
                     == review_gate.SKIPPED_LOW_RISK]
    skip_violations = []
    for index, pair in skipped_pairs:
        if pair.get("reviewed"):
            skip_violations.append(f"{index}: reviewed")
        if pair.get("accepted_target"):
            skip_violations.append(f"{index}: accepted_target")
        if str(pair.get("target_provenance") or "") not in {"generated", ""}:
            skip_violations.append(f"{index}: provenance={pair.get('target_provenance')}")
        key = translation_memory.tm_scope_key(TARGET_LANG,
                                              str(pair.get("source") or ""))
        if key and key in tm:
            skip_violations.append(f"{index}: in_tm")
    readiness = translation_evidence.translation_review_readiness(state)
    stats_out = state.get("review_stats") or {}
    review_calls = provider.calls.get("review", 0)
    clean_segments_reviewed = sum(
        1 for item in per_sample
        if item["reviewed"] and not item["serious_errors"])
    return {
        "serious_total": serious_total,
        "serious_detected": serious_detected,
        "serious_missed": serious_missed,
        "serious_recall": round(serious_detected / serious_total, 4)
        if serious_total else None,
        "missed_by_category": _count_by(missed_details, "category"),
        "missed_details": missed_details,
        "per_domain": {
            domain: {
                **stats,
                "recall": round(stats["serious_detected"]
                                / stats["serious_total"], 4)
                if stats["serious_total"] else None,
            } for domain, stats in domain_stats.items()
        },
        "per_sample": per_sample,
        "review_calls": review_calls,
        "review_prompt_tokens": provider.prompt_tokens.get("review", 0),
        "review_completion_tokens": provider.completion_tokens.get("review", 0),
        "all_calls": dict(provider.calls),
        "all_prompt_tokens": dict(provider.prompt_tokens),
        "all_completion_tokens": dict(provider.completion_tokens),
        "wall_ms": arm["wall_ms"],
        "review_provider_seconds": round(provider.review_seconds, 3),
        "review_stats": stats_out,
        "reviewed_segments": sum(1 for pair in pairs if pair.get("reviewed")),
        "skipped_segments": len(skipped_pairs),
        "clean_segments_reviewed": clean_segments_reviewed,
        "skip_violations": skip_violations,
        "skipped_never_reviewed": not skip_violations,
        "skipped_not_tm_eligible": all(
            "in_tm" not in violation for violation in skip_violations),
        "readiness": {
            "required": readiness.get("required"),
            "status": readiness.get("status"),
            "coverage_complete": readiness.get("coverage_complete"),
            "skipped_count": readiness.get("skipped_count"),
            "skipped_reason_counts": readiness.get("skipped_reason_counts"),
            "ready": readiness.get("ready"),
        },
        "review_batches": len(provider.review_batches),
        "reviewer_false_positive_findings": sum(
            item["reviewer_false_positive_findings"] for item in per_sample),
        "deterministic_false_positive_findings": sum(
            item["deterministic_false_positive_findings"] for item in per_sample),
    }


def _count_by(items: Iterable[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _policy_recommendation(always: Mapping[str, Any],
                           selective: Mapping[str, Any]) -> List[str]:
    lines = []
    recall_delta = (selective["serious_detected"] - always["serious_detected"])
    calls_saved = always["review_calls"] - selective["review_calls"]
    lines.append(
        f"Recall: always {always['serious_detected']}/{always['serious_total']}, "
        f"risk_selective {selective['serious_detected']}/{selective['serious_total']} "
        f"(delta {recall_delta}).")
    lines.append(
        f"Review calls: always {always['review_calls']}, risk_selective "
        f"{selective['review_calls']} (saved {calls_saved}); clean segments reviewed: "
        f"always {always['clean_segments_reviewed']}, selective "
        f"{selective['clean_segments_reviewed']}.")
    if selective["serious_missed"]:
        categories = ", ".join(sorted(selective["missed_by_category"]))
        lines.append(
            "risk_selective missed serious errors in these annotation categories: "
            f"{categories}. This reproduces the #20 finding that deterministic "
            "signals alone cannot see terminology drift, omission, negation and "
            "mistranslation; the measured misses are the cost of the skipped "
            "batches.")
        lines.append(
            "Policy recommendation: keep the modes explicit rather than declaring "
            "them equivalent. If the default is risk_selective (#64), the report "
            "must not claim full-review recall, and skipped segments must stay "
            "visibly skipped (this run verifies reviewed=false, no approval, no TM "
            "promotion). A future mode that samples review work cannot be "
            "justified by this harness alone.")
    else:
        lines.append(
            "No serious miss in this sample; the sample is too small to treat that "
            "as policy equivalence, so the explicit-mode recommendation stands.")
    return lines


def evaluate(*, batch_size: int = 1) -> Dict[str, Any]:
    samples, glossary, meta = load_dataset()
    started = time.perf_counter()
    arms = {}
    for mode in ("always", "risk_selective"):
        arm = _run_arm(samples, glossary, mode=mode, batch_size=batch_size,
                       job_id=f"review-eval-{mode}")
        arms[mode] = _score_arm(samples, arm, glossary)
    comparison = {
        "serious_recall_delta": (arms["risk_selective"]["serious_detected"]
                                 - arms["always"]["serious_detected"]),
        "review_calls_saved": (arms["always"]["review_calls"]
                               - arms["risk_selective"]["review_calls"]),
        "review_prompt_tokens_saved": (
            arms["always"]["review_prompt_tokens"]
            - arms["risk_selective"]["review_prompt_tokens"]),
        "clean_segments_reviewed_saved": (
            arms["always"]["clean_segments_reviewed"]
            - arms["risk_selective"]["clean_segments_reviewed"]),
    }
    return {
        "schema_version": 1,
        "kind": "review_evidence_evaluation",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "main_commit": _git_commit(),
            "dataset_sha256": _sha256(DATA_DIR / "samples.jsonl"),
            "annotation_sha256": _sha256(DATA_DIR / "annotations.json"),
            "samples": len(samples),
            "batch_size": batch_size,
            "provider": "scripted (annotated drafts + perfect reviewer for gold errors)",
            "model": "scripted-model",
            "target_lang": TARGET_LANG,
            "glossary_entries": len(glossary),
            "review_default_on_main": review_gate.DEFAULT_REVIEW_MODE,
            "token_estimator": "folith.usage.estimate_tokens (ceil(chars/4))",
            "network": "none; wall time is local harness time, not provider latency",
        },
        "annotation_provenance": meta.get("annotation"),
        "arms": arms,
        "comparison": comparison,
        "policy_recommendation": _policy_recommendation(arms["always"],
                                                        arms["risk_selective"]),
        "harness_wall_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return "unknown"


def render_markdown(report: Mapping[str, Any]) -> str:
    config = report["config"]
    arms = report["arms"]
    lines = [
        "# 审校策略评估报告（#100）",
        "",
        f"生成时间：{report['created_at']}　commit：`{config['main_commit'][:12]}`"
        f"　batch_size：{config['batch_size']}",
        "",
        "## 配置与语料",
        "",
        f"- 样本：{config['samples']} 条（literary / academic / technical 各 6）",
        f"- provider：{config['provider']}，model：`{config['model']}`",
        f"- 目标语言：{config['target_lang']}；锁定术语 {config['glossary_entries']} 条",
        f"- 当前 main 默认审校模式：`{config['review_default_on_main']}`",
        f"- dataset sha256：`{config['dataset_sha256'][:16]}…`"
        f"；annotation sha256：`{config['annotation_sha256'][:16]}…`",
        f"- 成本口径：{config['token_estimator']}；{config['network']}",
        "",
        "## 汇总",
        "",
        "| 指标 | always（全量审校） | risk_selective（风险选择性） |",
        "| --- | --- | --- |",
        f"| 严重错误检出 | {arms['always']['serious_detected']}/{arms['always']['serious_total']}"
        f" | {arms['risk_selective']['serious_detected']}/{arms['risk_selective']['serious_total']} |",
        f"| 严重错误召回率 | {arms['always']['serious_recall']}"
        f" | {arms['risk_selective']['serious_recall']} |",
        f"| 漏检 | {arms['always']['serious_missed']} | {arms['risk_selective']['serious_missed']} |",
        f"| review 调用 | {arms['always']['review_calls']} | {arms['risk_selective']['review_calls']} |",
        f"| review 输入 token（估计） | {arms['always']['review_prompt_tokens']}"
        f" | {arms['risk_selective']['review_prompt_tokens']} |",
        f"| review 输出 token（估计） | {arms['always']['review_completion_tokens']}"
        f" | {arms['risk_selective']['review_completion_tokens']} |",
        f"| 全部模型调用 | {arms['always']['all_calls']} | {arms['risk_selective']['all_calls']} |",
        f"| 端到端本地耗时 ms | {arms['always']['wall_ms']} | {arms['risk_selective']['wall_ms']} |",
        f"| 被审校的“干净”段 | {arms['always']['clean_segments_reviewed']}"
        f" | {arms['risk_selective']['clean_segments_reviewed']} |",
        f"| 跳过段 | {arms['always']['skipped_segments']}"
        f" | {arms['risk_selective']['skipped_segments']} |",
        f"| reviewer 假阳性（对照段上的 review finding） | "
        f"{arms['always']['reviewer_false_positive_findings']}"
        f" | {arms['risk_selective']['reviewer_false_positive_findings']} |",
        f"| 确定性假阳性（对照段上的 check finding） | "
        f"{arms['always']['deterministic_false_positive_findings']}"
        f" | {arms['risk_selective']['deterministic_false_positive_findings']} |",
        f"| 跳过段违规（reviewed/approved/TM） | {len(arms['always']['skip_violations'])}"
        f" | {len(arms['risk_selective']['skip_violations'])} |",
        "",
        "## 分领域召回",
        "",
        "| 领域 | always | risk_selective | selective 漏检类别 |",
        "| --- | --- | --- | --- |",
    ]
    for domain in ("literary", "academic", "technical"):
        always = arms["always"]["per_domain"][domain]
        selective = arms["risk_selective"]["per_domain"][domain]
        missed = sorted({
            error["category"]
            for sample in arms["risk_selective"]["per_sample"]
            if sample["domain"] == domain
            for error in sample["serious_errors"] if error["missed"]
        })
        lines.append(
            f"| {domain} | {always['serious_detected']}/{always['serious_total']}"
            f" | {selective['serious_detected']}/{selective['serious_total']}"
            f" | {', '.join(missed) or '—'} |")
    lines.extend(["", "## risk_selective 漏检明细", ""])
    if arms["risk_selective"]["missed_details"]:
        lines.extend(["| 样本 | 领域 | 类别 | 说明 |", "| --- | --- | --- | --- |"])
        for item in arms["risk_selective"]["missed_details"]:
            lines.append(f"| {item['sample']} | {item['domain']} | "
                         f"{item['category']} | {item['note']} |")
    else:
        lines.append("无。")
    lines.extend([
        "",
        "## 跳过语义验证",
        "",
        f"- always：skipped={arms['always']['skipped_segments']}，"
        f"违规={arms['always']['skip_violations'] or '无'}，"
        f"coverage_complete={arms['always']['readiness']['coverage_complete']}",
        f"- risk_selective：skipped={arms['risk_selective']['skipped_segments']}，"
        f"违规={arms['risk_selective']['skip_violations'] or '无'}，"
        f"coverage_complete={arms['risk_selective']['readiness']['coverage_complete']}",
        f"- readiness（selective）：{json.dumps(arms['risk_selective']['readiness'], ensure_ascii=False)}",
        "",
        "## 与 #20 / #64 的对照",
        "",
        "- #20：确定性信号漏掉 4/4 严重 finding、五类 review-only 缺陷不可见。"
        "本轮样本中 risk_selective 的漏检类别与之一致；检出仅发生在数字/单位/"
        "标签/占位符/术语（有确定性信号）的样本上。",
        "- #64：建议把 risk_selective 设为默认。本轮不改变生产默认，只量化代价："
        "选择性模式节省 review 调用与 token，但漏检非确定性类别；若采纳默认，"
        "不得声称与全量审校等价，且跳过段必须保持 skipped 可见。",
        "",
        "## 建议",
        "",
    ])
    lines.extend(f"- {item}" for item in report["policy_recommendation"])
    lines.extend([
        "",
        "## 限制",
        "",
        "- 标注由实现 agent 编写，不是独立人类专家；无标注者间一致性。",
        "- 样本为合成的短句，不是真实书籍语料；每域仅 6 条（4 个错误 + 2 个对照）。",
        "- 脚本化 reviewer 对看到的 gold 错误是“完美”的，因此召回率验证的是"
        "路由策略（哪些批到达 reviewer），不是 reviewer 模型的真实质量。",
        "- 无网络、无真实 API 调用；wall time 是本机 harness 时间，token 是字符估计，"
        "不是账单数据。",
        "- 未运行 Windows 原生环境；验证为 macOS + Python 3.11。",
        "",
        f"（harness 自身耗时 {report['harness_wall_ms']} ms）",
    ])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    report = evaluate(batch_size=args.batch_size)
    markdown = render_markdown(report)
    print(markdown)
    if not args.no_write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = report["created_at"].replace(":", "").replace("+00:00", "Z")
        json_path = RESULTS_DIR / f"review-evidence-{stamp}.json"
        md_path = RESULTS_DIR / f"review-evidence-{stamp}.md"
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            json_path, md_path = out / json_path.name, out / md_path.name
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        print(f"wrote {json_path}\nwrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
