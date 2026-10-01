"""Read-only, deterministic findings and progress for the Folith agent panel.

The runtime and Translation Core remain authoritative; this module only reads
persisted job state and reports what it already says — terminology drift, lost
citation markers, placeholder leftovers, structural delivery problems,
translation coverage, and pending review.  It never writes state, never calls a
model, and never touches the network or the filesystem, so the UI can rebuild
the panel on every rerun without side effects.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from .terminology import compile_glossary, term_matches


SEVERITY_BLOCKING = "blocking"
SEVERITY_ACTIONABLE = "actionable"
SEVERITY_INFORMATIONAL = "informational"

_SEVERITY_RANK = {
    SEVERITY_BLOCKING: 0,
    SEVERITY_ACTIONABLE: 1,
    SEVERITY_INFORMATIONAL: 2,
}
# Findings without a concrete segment sort after every indexed finding.
_NO_SEGMENT = 1 << 30

_TITLE_LIMIT = 60
_DETAIL_LIMIT = 160
_COUNTING_LIMIT = 10 ** 6
_MAX_CITATION_FINDINGS = 6

_SHORT_TARGET_RATIO = 0.25
_SHORT_SOURCE_MIN_CHARS = 80

# Glossary statuses that make a term part of the deterministic terminology check.
_TERM_CHECK_STATUSES = frozenset({"locked", "frozen"})
# Glossary statuses that count as confirmed terminology progress.
_TERM_DONE_STATUSES = frozenset({"locked", "frozen", "confirmed"})

# Numeric brackets ([1], [1,2], [1-3]) and author-year forms ((Smith, 2019),
# （Smith，2019）).  Full-width punctuation is normalized before comparison.
_CITATION_RE = re.compile(
    r"\[\s*\d{1,4}(?:\s*[-,–—]\s*\d{1,4})*\s*\]"
    r"|[（(]\s*[A-Za-z][^（()）]{0,60}?[,，]\s*(?:19|20)\d{2}[a-z]?\s*[)）]"
)

_FULLWIDTH_PUNCTUATION = str.maketrans({
    "（": "(", "）": ")", "，": ",", "－": "-", "–": "-", "—": "-",
})

# (pattern, blocks_delivery).  Template leftovers are blocking; placeholder
# syntax that may be intentional stays actionable.
_PLACEHOLDER_RULES: Tuple[Tuple[re.Pattern, bool], ...] = (
    (re.compile(r"Lorem ipsum", re.IGNORECASE), True),
    (re.compile(r"\b(?:TODO|TBD|TRANSLATE)\b"), True),
    (re.compile(r"\bXXX+\b"), False),
    (re.compile(r"\{\{\s*[^{}\n]{1,40}?\s*\}\}|\{\s*[A-Za-z_][A-Za-z0-9_.\-]*\s*\}"), False),
    (re.compile(r"%(?:\d+\$)?[sd]"), False),
)


# ---------------- 基础工具 ----------------

def _as_mapping(state: Any) -> Mapping[str, Any]:
    return state if isinstance(state, Mapping) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _flag(value: Any) -> bool:
    """Truthiness that survives persisted "false"/"0" strings."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none"}
    return bool(value)


def _clip(text: Any, limit: int) -> str:
    value = _text(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _segment_list(indexes: Sequence[int], *, cap: int = 5) -> str:
    """1-based segment list for human-facing copy."""
    labels = [str(int(index) + 1) for index in list(indexes)[:cap]]
    text = "、".join(labels)
    if len(indexes) > cap:
        text += f" 等 {len(indexes)} 段"
    return text


def _finding(*, identifier: str, kind: str, severity: str, title: str,
             detail: str, segments: Sequence[int], action: str) -> Dict[str, Any]:
    clean: List[int] = sorted({
        int(index) for index in segments
        if isinstance(index, int) and not isinstance(index, bool)
    })
    return {
        "id": str(identifier),
        "kind": kind,
        "severity": severity,
        "title": _clip(title, _TITLE_LIMIT),
        "detail": _clip(detail, _DETAIL_LIMIT),
        "segments": clean,
        "action": action,
    }


def _sort_key(finding: Mapping[str, Any]) -> Tuple[int, int, str]:
    segments = finding.get("segments") or []
    first = min(segments) if segments else _NO_SEGMENT
    return (_SEVERITY_RANK.get(str(finding.get("severity")), 3), first,
            str(finding.get("id") or ""))


def _safe_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 12
    return max(0, value)


def _pair_reviewed(pair: Mapping[str, Any]) -> bool:
    return _flag(pair.get("reviewed")) or _flag(pair.get("human_edited"))


# ---------------- 段落读取 ----------------

def _segment_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Normalize pairs into index/source/target/pair rows without mutating state.

    ``pairs`` wins when present; otherwise ``paras`` becomes the segment list so
    a document that has not produced pairs yet still yields progress numbers.
    Pair sources fall back to the parallel paragraph list.
    """
    raw_pairs = state.get("pairs")
    raw_paras = state.get("paras")
    paras: Sequence[Any] = raw_paras if isinstance(raw_paras, (list, tuple)) else []
    if isinstance(raw_pairs, (list, tuple)) and raw_pairs:
        rows: Sequence[Any] = list(raw_pairs)
    else:
        rows = list(paras)
    records: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        pair = row if isinstance(row, Mapping) else {}
        source = _text(pair.get("source"))
        if not source and index < len(paras):
            source = _text(paras[index])
        records.append({
            "index": index,
            "source": source,
            "target": _text(pair.get("target")),
            "pair": pair,
        })
    return records


# ---------------- 术语 ----------------

def _term_entries(state: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Locked/frozen glossary terms plus auto-term overrides, keyed by source."""
    glossary = state.get("glossary")
    raw_auto = state.get("auto_terms")
    auto_terms: Mapping[Any, Any] = raw_auto if isinstance(raw_auto, Mapping) else {}
    entries: Dict[str, Dict[str, str]] = {}
    if isinstance(glossary, (list, tuple)):
        for raw in glossary:
            if not isinstance(raw, Mapping):
                continue
            source = _text(raw.get("source"))
            if not source:
                continue
            status = _text(raw.get("status")).lower()
            in_auto = source in auto_terms
            if status not in _TERM_CHECK_STATUSES and not in_auto:
                continue
            expected = _text(raw.get("preferred")) or _text(raw.get("target"))
            if not expected and in_auto:
                expected = _text(auto_terms.get(source))
            if source in entries:
                continue
            entries[source] = {
                "source": source,
                "expected": expected,
                "status": status or "candidate",
            }
    for key, value in auto_terms.items():
        source = _text(key)
        if not source or source in entries:
            continue
        entries[source] = {
            "source": source,
            "expected": _text(value),
            "status": "auto",
        }
    return list(entries.values())


def _terminology_findings(state: Mapping[str, Any],
                          records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    entries = _term_entries(state)
    compiled = compile_glossary(entry["source"] for entry in entries)
    for entry in entries:
        term, expected, status = entry["source"], entry["expected"], entry["status"]
        if not expected:
            continue
        pattern = compiled.pattern(term)
        matches = pattern.search if pattern is not None else lambda text: term in text
        hits = [record for record in records
                if record["target"] and matches(record["source"])]
        if not hits:
            continue
        missing = [int(record["index"]) for record in hits
                   if not term_matches(expected, record["target"])]
        if not missing:
            continue
        translated_indexes = [int(record["index"]) for record in hits]
        if len(missing) == len(hits):
            # Mirror case: the term was translated, but never with its preferred name.
            findings.append(_finding(
                identifier=f"terminology_unused:{term}",
                kind="terminology",
                severity=SEVERITY_ACTIONABLE,
                title=f"「{term}」未使用首选译名",
                detail=(f"术语表将「{term}」锁定为「{expected}」，但 "
                        f"{len(hits)} 处含该词的译文均未使用该译名，请确认术语是否真正生效。"),
                segments=translated_indexes,
                action="检查术语"))
            continue
        if len(hits) < 2:
            continue
        findings.append(_finding(
            identifier=f"terminology:{term}",
            kind="terminology",
            severity=(SEVERITY_BLOCKING if status == "frozen"
                      else SEVERITY_ACTIONABLE),
            title=f"「{term}」在 {len(hits)} 处译法不一致",
            detail=(f"术语表将「{term}」锁定为「{expected}」，"
                    f"但第 {_segment_list(missing)} 段未使用该译名（共 {len(hits)} 处命中）。"),
            segments=missing,
            action="统一译法"))
    return findings


# ---------------- 引用标注 ----------------

def _normalize_marker(marker: str) -> str:
    return re.sub(r"\s+", "", _text(marker).translate(_FULLWIDTH_PUNCTUATION).lower())


def _missing_markers(source_text: str, target_text: str) -> List[str]:
    """Marker multiset difference that keeps the source's original spelling."""
    expected = _CITATION_RE.findall(source_text or "")
    if not expected:
        return []
    available = Counter(_normalize_marker(marker)
                        for marker in _CITATION_RE.findall(target_text or ""))
    missing: List[str] = []
    for marker in expected:
        key = _normalize_marker(marker)
        if available.get(key, 0) > 0:
            available[key] -= 1
            continue
        if marker not in missing:
            missing.append(marker)
    return missing


def _citation_findings(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for record in records:
        if not record["target"]:
            continue
        missing = _missing_markers(str(record["source"]), str(record["target"]))
        if not missing:
            continue
        shown = "、".join(missing[:3])
        findings.append(_finding(
            identifier=f"citation:{record['index']}",
            kind="citation",
            severity=SEVERITY_ACTIONABLE,
            title=f"第 {int(record['index']) + 1} 段缺少引用标注 {shown}",
            detail=f"原文含引用标注 {shown}，译文中未保留，请核对是否漏译了引用。",
            segments=[int(record["index"])],
            action="补回引用标注"))
        if len(findings) >= _MAX_CITATION_FINDINGS:
            break
    return findings


# ---------------- 占位符 ----------------

def _placeholder_findings(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for record in records:
        target = str(record["target"])
        if not target:
            continue
        hard: List[str] = []
        soft: List[str] = []
        for pattern, blocks_delivery in _PLACEHOLDER_RULES:
            for match in pattern.finditer(target):
                token = match.group(0)
                bucket = hard if blocks_delivery else soft
                if token not in bucket:
                    bucket.append(token)
        if not hard and not soft:
            continue
        tokens = hard + [token for token in soft if token not in hard]
        shown = "、".join(tokens[:3])
        tail = "交付前必须替换为实际内容。" if hard else "请确认是否为有意保留的占位符。"
        findings.append(_finding(
            identifier=f"placeholder:{record['index']}",
            kind="placeholder",
            severity=SEVERITY_BLOCKING if hard else SEVERITY_ACTIONABLE,
            title=f"第 {int(record['index']) + 1} 段译文残留占位符 {shown}",
            detail=f"目标文本仍包含未处理的占位符或模板内容：{shown}；{tail}",
            segments=[int(record["index"])],
            action="清理占位符"))
    return findings


# ---------------- 结构 ----------------

def _structure_findings(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    validation = state.get("delivery_validation")
    issues = validation.get("issues") if isinstance(validation, Mapping) else None
    if not isinstance(issues, (list, tuple)):
        return []
    findings: List[Dict[str, Any]] = []
    for position, issue in enumerate(issues):
        if not isinstance(issue, Mapping):
            continue
        if _text(issue.get("code")) != "transport_wrapper":
            continue
        index = issue.get("segment_index")
        if isinstance(index, bool) or not isinstance(index, int):
            index = None
        message = _text(issue.get("message")) or "译文仍是未解析的结构化包装文本"
        findings.append(_finding(
            identifier=(f"structure:{index}" if index is not None
                        else f"structure:unindexed-{position}"),
            kind="structure",
            severity=SEVERITY_BLOCKING,
            title=(f"第 {index + 1} 段译文结构异常" if index is not None
                   else "译文结构异常"),
            detail=f"交付校验发现译文结构问题：{message}",
            segments=[] if index is None else [index],
            action="重译该段"))
    return findings


# ---------------- 覆盖 ----------------

def _coverage_findings(state: Mapping[str, Any],
                       records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    translated = [record for record in records if record["target"]]
    if not translated:
        return []
    short: List[int] = []
    for record in records:
        target = str(record["target"])
        source = str(record["source"])
        if not target or len(source) < _SHORT_SOURCE_MIN_CHARS:
            continue
        if len(target) < len(source) * _SHORT_TARGET_RATIO:
            short.append(int(record["index"]))
    if short:
        return [_finding(
            identifier="coverage:short",
            kind="coverage",
            severity=SEVERITY_ACTIONABLE,
            title=f"{len(short)} 段译文长度明显偏短",
            detail=(f"第 {_segment_list(short)} 段译文不足原文长度的 25%"
                    f"（原文至少 {_SHORT_SOURCE_MIN_CHARS} 字符），可能漏译。"),
            segments=short,
            action="复核译文")]
    if not _flag(state.get("p2_done")):
        return []
    empty = [int(record["index"]) for record in records
             if not record["target"] and str(record["source"]).strip()]
    if not empty:
        return []
    return [_finding(
        identifier="coverage:empty",
        kind="coverage",
        severity=SEVERITY_INFORMATIONAL,
        title=f"{len(empty)} 段译文为空",
        detail=f"任务已标记翻译完成，但第 {_segment_list(empty)} 段仍没有译文，请确认。",
        segments=empty,
        action="补全译文")]


# ---------------- 审校 ----------------

def _review_findings(state: Mapping[str, Any],
                     records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if not _flag(state.get("translation_core_review_required")):
        return []
    pending = [int(record["index"]) for record in records
               if record["target"] and not _pair_reviewed(record["pair"])]
    if not pending:
        return []
    return [_finding(
        identifier="review:pending",
        kind="review",
        severity=SEVERITY_INFORMATIONAL,
        title=f"{len(pending)} 段译文尚未审校",
        detail=f"第 {_segment_list(pending)} 段译文尚未经过人工审校，交付前建议完成。",
        segments=pending,
        action="前往审校")]


# ---------------- 公开 API ----------------

def plan_findings(state: Any, *, job_id: str = "", limit: int = 12) -> List[Dict[str, Any]]:
    """Return ordered document findings for the workspace agent panel.

    Findings are document-scoped and deterministic, so ``job_id`` is accepted
    only for call-site symmetry and never mixed into finding ids.  The function
    is total: a ``None``, empty, or malformed state yields ``[]``.
    """
    try:
        mapping = _as_mapping(state)
        records = _segment_records(mapping)
        findings: List[Dict[str, Any]] = []
        collectors: Sequence[Callable[[], List[Dict[str, Any]]]] = (
            lambda: _terminology_findings(mapping, records),
            lambda: _citation_findings(records),
            lambda: _placeholder_findings(records),
            lambda: _structure_findings(mapping),
            lambda: _coverage_findings(mapping, records),
            lambda: _review_findings(mapping, records),
        )
        for collect in collectors:
            try:
                findings.extend(collect())
            except Exception:
                continue
        deduped: Dict[str, Dict[str, Any]] = {}
        for finding in findings:
            deduped.setdefault(str(finding.get("id")), finding)
        ordered = sorted(deduped.values(), key=_sort_key)
        return ordered[:_safe_limit(limit)]
    except Exception:
        return []


def _glossary_entries(state: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    glossary = state.get("glossary")
    if not isinstance(glossary, (list, tuple)):
        return []
    return [entry for entry in glossary
            if isinstance(entry, Mapping) and _text(entry.get("source"))]


def _auto_term_count(state: Mapping[str, Any]) -> int:
    auto_terms = state.get("auto_terms")
    if not isinstance(auto_terms, Mapping):
        return 0
    return len([key for key in auto_terms if _text(key)])


def _progress(state: Mapping[str, Any], *, job_id: str = "", findings=None) -> Dict[str, Any]:
    records = _segment_records(state)
    total = len(records)
    done = sum(1 for record in records if record["target"])
    translation_complete = total > 0 and done >= total
    translation = {
        "done": done,
        "total": total,
        "label": f"{done} / {total}",
        "complete": translation_complete,
    }

    glossary_entries = _glossary_entries(state)
    term_total = len(glossary_entries) or _auto_term_count(state)
    term_done = min(
        sum(1 for entry in glossary_entries
            if _text(entry.get("status")).lower() in _TERM_DONE_STATUSES),
        term_total) if term_total else 0
    terminology_applicable = bool(
        term_total or _flag(state.get("quality_mode"))
        or _flag(state.get("glossary_frozen")))
    if not terminology_applicable:
        terminology = {"done": 0, "total": 0, "label": "不适用",
                       "complete": True, "applicable": False}
    else:
        terminology_complete = bool(
            _flag(state.get("glossary_frozen")) or _flag(state.get("quality_bypass"))
            or (term_total > 0 and term_done >= term_total))
        terminology = {"done": term_done, "total": term_total,
                       "label": f"{term_done} / {term_total}",
                       "complete": terminology_complete, "applicable": True}

    translated = [record for record in records if record["target"]]
    review_applicable = _flag(state.get("translation_core_review_required"))
    if not review_applicable:
        review = {"done": 0, "total": 0, "label": "不适用",
                  "complete": True, "applicable": False}
    else:
        review_total = len(translated)
        review_done = sum(1 for record in translated
                          if _pair_reviewed(record["pair"]))
        review = {"done": review_done, "total": review_total,
                  "label": f"{review_done} / {review_total}",
                  "complete": review_done >= review_total, "applicable": True}

    if findings is None:
        findings = plan_findings(state, job_id=job_id, limit=_COUNTING_LIMIT)
    blocking = sum(1 for finding in findings
                   if finding.get("severity") == SEVERITY_BLOCKING)
    actionable = sum(1 for finding in findings
                     if finding.get("severity") == SEVERITY_ACTIONABLE)
    informational = sum(1 for finding in findings
                        if finding.get("severity") == SEVERITY_INFORMATIONAL)
    count = len(findings)
    if count == 0:
        issues_label = "未发现问题"
    elif blocking:
        issues_label = f"{count} 个问题，其中 {blocking} 项阻断交付"
    else:
        issues_label = f"{count} 个问题"
    issues = {"count": count, "blocking": blocking, "actionable": actionable,
              "informational": informational, "label": issues_label}

    # The delivery verdict is owned by ``task_overview.derive_task_overview_state``
    # so the header, sidebar, hero and pipeline can never disagree again.  The
    # counts above are handed over as facts to avoid a second document scan.
    from . import task_overview as _task_overview

    overview = _task_overview.derive_task_overview_state(
        state,
        facts={"job_id": job_id,
               "issue_counts": {"blocking": blocking, "actionable": actionable,
                                "informational": informational}},
    )
    verdict = {"tone": overview["tone"], "label": overview["label"],
               "detail": overview["detail"], "lifecycle": overview["lifecycle"],
               "reason": overview["reason"]}

    return {
        "translation": translation,
        "terminology": terminology,
        "review": review,
        "issues": issues,
        "verdict": verdict,
    }


def _fallback_progress() -> Dict[str, Any]:
    return {
        "translation": {"done": 0, "total": 0, "label": "0 / 0", "complete": False},
        "terminology": {"done": 0, "total": 0, "label": "不适用",
                        "complete": True, "applicable": False},
        "review": {"done": 0, "total": 0, "label": "不适用",
                   "complete": True, "applicable": False},
        "issues": {"count": 0, "blocking": 0, "actionable": 0, "informational": 0,
                   "label": "未发现问题"},
        "verdict": {"tone": "gray", "label": "尚未开始",
                    "detail": "当前任务还没有可交付的段落译文。",
                    "lifecycle": "draft", "reason": "draft"},
    }


def workspace_progress(state: Any, *, job_id: str = "", findings=None) -> Dict[str, Any]:
    """Return the multi-dimensional progress model behind the agent header.

    The single "82 / 82 已翻译" number becomes translation, terminology, review,
    issues, and an honest delivery verdict.  This function is total and never
    raises; malformed states degrade to a valid, non-ready progress model.
    """
    try:
        return _progress(_as_mapping(state), job_id=job_id, findings=findings)
    except Exception:
        return _fallback_progress()
