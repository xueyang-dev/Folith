"""Concordance bilingual corpus search engine.

CAT-standard bilingual concordance search:
- Searches keyword in source or target direction across project TM and task pairs;
- Returns result model with source/target snippets, highlight spans, provenance/origin,
  review status, updated timestamp, and relevance score;
- Sorts by relevance or updated time;
- Pure local, zero model/LLM/network calls;
- Fast and bounded on 50,000+ items.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from folith.textual import normalize_language
from folith.translation_memory import tm_record_language, tm_unscope_key


@dataclass
class ConcordanceItem:
    """One bilingual concordance match record."""
    source: str
    target: str
    source_highlights: List[Tuple[int, int]] = field(default_factory=list)
    target_highlights: List[Tuple[int, int]] = field(default_factory=list)
    origin: str = ""
    origin_type: str = ""  # "tm_import" | "tm_reviewed" | "task_pair"
    reviewed: bool = False
    review_status: str = "unreviewed"
    updated_at: str = ""
    job_id: str = ""
    segment_index: int = -1
    task_name: str = ""
    direction: str = "source"
    score: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "source_highlights": list(self.source_highlights),
            "target_highlights": list(self.target_highlights),
            "origin": self.origin,
            "origin_type": self.origin_type,
            "reviewed": self.reviewed,
            "review_status": self.review_status,
            "updated_at": self.updated_at,
            "job_id": self.job_id,
            "segment_index": self.segment_index,
            "task_name": self.task_name,
            "direction": self.direction,
            "score": round(self.score, 4),
        }


def find_match_spans(text: str, query: str, *, case_sensitive: bool = False) -> List[Tuple[int, int]]:
    """Return non-overlapping (start, end) index spans of query in text."""
    if not text or not query:
        return []
    flags = 0 if case_sensitive else re.IGNORECASE
    escaped = re.escape(query)
    try:
        return [(m.start(), m.end()) for m in re.finditer(escaped, text, flags=flags)]
    except re.error:
        return []


def calculate_relevance(
    text: str,
    query: str,
    spans: Sequence[Tuple[int, int]],
    *,
    reviewed: bool = False,
    is_tm: bool = False,
    case_sensitive: bool = False,
) -> float:
    """Calculate relevance score for ranking."""
    if not spans:
        return 0.0
    text_clean = text.strip()
    query_clean = query.strip()
    if not case_sensitive:
        text_cmp = text_clean.casefold()
        query_cmp = query_clean.casefold()
    else:
        text_cmp = text_clean
        query_cmp = query_clean

    # Exact full match
    if text_cmp == query_cmp:
        score = 100.0
    # Starts with (prefix match)
    elif text_cmp.startswith(query_cmp):
        score = 80.0 + (len(query_cmp) / max(len(text_cmp), 1)) * 10.0
    # Whole-word / token boundary match
    elif any(
        (start == 0 or not text[start - 1].isalnum()) and
        (end == len(text) or not text[end].isalnum())
        for start, end in spans
    ):
        score = 70.0 + (len(query_clean) / max(len(text_clean), 1)) * 15.0
    else:
        # Substring match
        score = 50.0 + (len(query_clean) / max(len(text_clean), 1)) * 20.0

    # Match count bonus (capped at 5 matches)
    match_count = min(len(spans), 5)
    score += (match_count - 1) * 2.0

    # Early appearance bonus: matches near beginning get slight boost
    if spans:
        first_start = spans[0][0]
        pos_ratio = max(0.0, 1.0 - (first_start / max(len(text), 1)))
        score += pos_ratio * 3.0

    # Reviewed state bonus
    if reviewed:
        score += 5.0

    # TM authoritative record bonus
    if is_tm:
        score += 2.0

    return score


def format_highlighted_html(text: str, spans: Sequence[Tuple[int, int]]) -> str:
    """Format text as HTML with highlight spans wrapped in a styled mark tag."""
    import html
    if not text:
        return ""
    if not spans:
        return html.escape(text)

    # Sort and merge overlapping spans
    sorted_spans = sorted(spans, key=lambda s: s[0])
    merged: List[Tuple[int, int]] = []
    for start, end in sorted_spans:
        if not merged:
            merged.append((start, end))
        else:
            prev_s, prev_e = merged[-1]
            if start <= prev_e:
                merged[-1] = (prev_s, max(prev_e, end))
            else:
                merged.append((start, end))

    parts: List[str] = []
    last = 0
    for start, end in merged:
        if start > last:
            parts.append(html.escape(text[last:start]))
        matched_str = text[start:end]
        parts.append(
            f"<span style='background-color: rgba(245, 158, 11, 0.32); "
            f"color: inherit; font-weight: 600; border-radius: 2px; padding: 0 2px;'>"
            f"{html.escape(matched_str)}</span>"
        )
        last = end
    if last < len(text):
        parts.append(html.escape(text[last:]))
    return "".join(parts)


def search_concordance(
    query: str,
    *,
    direction: str = "source",  # "source" | "target"
    project_id: Optional[str] = None,
    state: Optional[Mapping[str, Any]] = None,
    tm: Optional[Mapping[str, Any]] = None,
    extra_jobs: Optional[Sequence[Mapping[str, Any]]] = None,
    target_lang: Optional[str] = None,
    sort_by: str = "relevance",  # "relevance" | "time"
    case_sensitive: bool = False,
    limit: int = 500,
) -> List[ConcordanceItem]:
    """Search bilingual concordance across project TM and task pairs.

    Zero model/LLM/network dependency. Fully local and deterministic.
    """
    q = str(query or "").strip()
    if not q:
        return []

    dir_key = "target" if str(direction or "").lower() == "target" else "source"

    # Resolve context from state if available
    current_job_id = ""
    if isinstance(state, Mapping):
        current_job_id = str(state.get("job_id") or "")
        if not project_id:
            try:
                from folith.engine.tm_store import tm_project_id
                project_id = tm_project_id(state)
            except Exception:
                project_id = str(state.get("project_id") or "")
        if not target_lang:
            try:
                from folith.engine.tm_store import state_target_lang
                target_lang = state_target_lang(state)
            except Exception:
                target_lang = str(state.get("target_lang") or "")

    # Lazy-load TM if not provided
    if tm is None:
        try:
            import core
            tm = core.load_tm(project_id)
        except Exception:
            tm = {}

    normalized_tgt = normalize_language(target_lang) if target_lang else ""

    results: List[ConcordanceItem] = []

    # 1. Search Project TM
    for key, record in (tm or {}).items():
        if not isinstance(record, Mapping):
            continue
        if normalized_tgt:
            rec_lang = tm_record_language(key, record)
            if rec_lang and rec_lang != normalized_tgt:
                continue

        _, src = tm_unscope_key(key)
        tgt = str(record.get("target") or "")
        if not src and not tgt:
            continue

        searched_text = src if dir_key == "source" else tgt
        spans = find_match_spans(searched_text, q, case_sensitive=case_sensitive)
        if not spans:
            continue

        is_import = record.get("source") == "tmx_import" or record.get("origin") == "tmx_import"
        origin = "项目 TM (TMX 导入)" if is_import else "项目 TM (审校入库)"
        origin_type = "tm_import" if is_import else "tm_reviewed"
        reviewed = bool(record.get("reviewed", True))
        review_status = "reviewed" if reviewed else "unreviewed"
        updated_at = str(record.get("updated_at") or record.get("timestamp") or "")

        score = calculate_relevance(
            searched_text, q, spans,
            reviewed=reviewed, is_tm=True, case_sensitive=case_sensitive,
        )

        results.append(ConcordanceItem(
            source=src,
            target=tgt,
            source_highlights=spans if dir_key == "source" else [],
            target_highlights=spans if dir_key == "target" else [],
            origin=origin,
            origin_type=origin_type,
            reviewed=reviewed,
            review_status=review_status,
            updated_at=updated_at,
            job_id="",
            segment_index=-1,
            task_name="",
            direction=dir_key,
            score=score,
        ))

    # 2. Search Current Task Pairs
    if isinstance(state, Mapping) and "pairs" in state:
        task_title = str(
            state.get("task_name") or state.get("filename")
            or state.get("title") or "当前任务"
        )
        for idx, pair in enumerate(state.get("pairs") or []):
            if not isinstance(pair, Mapping):
                continue
            src = str(pair.get("source") or "")
            tgt = str(pair.get("target") or "")
            if not src and not tgt:
                continue

            searched_text = src if dir_key == "source" else tgt
            spans = find_match_spans(searched_text, q, case_sensitive=case_sensitive)
            if not spans:
                continue

            reviewed = bool(pair.get("reviewed"))
            review_status = str(pair.get("review_status") or ("reviewed" if reviewed else "unreviewed"))
            updated_at = str(pair.get("updated_at") or pair.get("timestamp") or state.get("updated_at") or "")

            score = calculate_relevance(
                searched_text, q, spans,
                reviewed=reviewed, is_tm=False, case_sensitive=case_sensitive,
            )

            results.append(ConcordanceItem(
                source=src,
                target=tgt,
                source_highlights=spans if dir_key == "source" else [],
                target_highlights=spans if dir_key == "target" else [],
                origin=f"{task_title} #{idx + 1}",
                origin_type="task_pair",
                reviewed=reviewed,
                review_status=review_status,
                updated_at=updated_at,
                job_id=current_job_id,
                segment_index=idx,
                task_name=task_title,
                direction=dir_key,
                score=score,
            ))

    # 3. Search Extra Jobs in Project
    for job in extra_jobs or []:
        job_state = job.get("state") if isinstance(job, Mapping) and "state" in job else job
        if not isinstance(job_state, Mapping):
            continue
        extra_job_id = str(job.get("job_id") if isinstance(job, Mapping) else job_state.get("job_id") or "")
        if extra_job_id and extra_job_id == current_job_id:
            continue

        extra_task_title = str(
            job_state.get("task_name") or job_state.get("filename")
            or job_state.get("title") or extra_job_id or "项目任务"
        )
        for idx, pair in enumerate(job_state.get("pairs") or []):
            if not isinstance(pair, Mapping):
                continue
            src = str(pair.get("source") or "")
            tgt = str(pair.get("target") or "")
            if not src and not tgt:
                continue

            searched_text = src if dir_key == "source" else tgt
            spans = find_match_spans(searched_text, q, case_sensitive=case_sensitive)
            if not spans:
                continue

            reviewed = bool(pair.get("reviewed"))
            review_status = str(pair.get("review_status") or ("reviewed" if reviewed else "unreviewed"))
            updated_at = str(pair.get("updated_at") or pair.get("timestamp") or job_state.get("updated_at") or "")

            score = calculate_relevance(
                searched_text, q, spans,
                reviewed=reviewed, is_tm=False, case_sensitive=case_sensitive,
            )

            results.append(ConcordanceItem(
                source=src,
                target=tgt,
                source_highlights=spans if dir_key == "source" else [],
                target_highlights=spans if dir_key == "target" else [],
                origin=f"{extra_task_title} #{idx + 1}",
                origin_type="task_pair",
                reviewed=reviewed,
                review_status=review_status,
                updated_at=updated_at,
                job_id=extra_job_id,
                segment_index=idx,
                task_name=extra_task_title,
                direction=dir_key,
                score=score,
            ))

    # Sort results
    if sort_by == "time":
        results.sort(key=lambda item: (item.updated_at, item.score), reverse=True)
    else:
        results.sort(key=lambda item: (item.score, item.updated_at), reverse=True)

    return results[:limit]
