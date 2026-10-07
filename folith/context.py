"""Long-document understanding and translation context compilation.

The module keeps five kinds of context separate:
document meaning, local section meaning, terminology, source neighbors, and
accepted target continuity.  It is deliberately JSON-shaped so the result can
be persisted beside a job and reused after a restart.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from .workflow_errors import raise_if_fatal


DEFAULT_UNIT_CHARS = 12000
DEFAULT_DIGEST_WORKERS = 4
MAX_SYNOPSIS_CHARS = 12000
# The context packet is an input budget, separate from the source batch budget.
# Keeping it bounded prevents a large synopsis/profile from silently multiplying
# prompt tokens for every translation batch.
DEFAULT_CONTEXT_BUDGET_CHARS = 7200
TARGET_CONTEXT_LEVELS = (
    "human_accepted",
    "reviewed",
    "tm_approved",
    "generated",
)


def _provider_failure_detail(error, fallback):
    """Use readable provider copy while preserving local parser diagnostics."""
    try:
        import core
        status = core.provider_error_status(error)
        if status["status"] != "unknown":
            return status["message"]
    except Exception:  # pragma: no cover - defensive import fallback
        pass
    return fallback


def _clean(value: Any, limit: int = 1200) -> str:
    text = "" if value is None else str(value).strip()
    return text[:limit].rstrip()


def _string_list(value: Any, limit: int = 12, item_limit: int = 160) -> List[str]:
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]", value)
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        item = _clean(item, item_limit)
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _dedupe_strings(values: Sequence[Any], *, exclude: Optional[Sequence[str]] = None,
                    limit: int = 12) -> List[str]:
    """Keep context order while removing repeated source snippets.

    Repeated neighboring paragraphs are common after PDF extraction and can
    otherwise be sent twice (once as previous context and once as next context).
    Exact matching is intentional: semantic deduplication would risk removing
    a genuinely repeated sentence that is useful for continuity.
    """
    seen = {str(item).strip() for item in (exclude or []) if str(item).strip()}
    result: List[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= max(0, int(limit or 0)):
            break
    return result


def _parse_object(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.DOTALL)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.DOTALL).strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", candidate):
        try:
            value, _ = decoder.raw_decode(candidate[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _call(call_llm: Callable, provider: str, api_key: str, model: str,
          system_prompt: str, user_prompt: str) -> Any:
    try:
        return call_llm(provider, api_key, model, system_prompt, user_prompt,
                        temperature=0.1)
    except TypeError:
        # Small test/dry-run providers sometimes expose the five-argument form.
        return call_llm(provider, api_key, model, system_prompt, user_prompt)


def _chunk_range(paragraphs: Sequence[str], start: int, end: int,
                 max_chars: int, unit_prefix: str, label: str,
                 kind: str) -> List[Dict[str, Any]]:
    units = []
    chunk_start = start
    chars = 0
    for index in range(start, end + 1):
        size = len(paragraphs[index])
        if index > chunk_start and chars + size > max_chars:
            units.append(_make_unit(paragraphs, chunk_start, index - 1,
                                    unit_prefix, len(units), label, kind))
            chunk_start, chars = index, 0
        chars += size
    if chunk_start <= end:
        units.append(_make_unit(paragraphs, chunk_start, end,
                                unit_prefix, len(units), label, kind))
    return units


def _make_unit(paragraphs: Sequence[str], start: int, end: int,
               prefix: str, number: int, label: str, kind: str) -> Dict[str, Any]:
    return {
        "unit_id": f"{prefix}-{number + 1}",
        "kind": kind,
        "label": _clean(label, 240),
        "start_segment": start,
        "end_segment": end,
        "source": "\n".join(paragraphs[start:end + 1]),
    }


def _section_sort_key(section: Dict[str, Any]) -> Tuple[int, int]:
    """Sort malformed profile ranges after valid ranges without raising."""
    try:
        start = int(section.get("start_segment"))
        end = int(section.get("end_segment"))
    except (TypeError, ValueError):
        return (10**12, 10**12)
    return start, end


def build_semantic_units(
    paragraphs: Sequence[str],
    document_profile: Optional[Dict[str, Any]] = None,
    max_chars: int = DEFAULT_UNIT_CHARS,
) -> List[Dict[str, Any]]:
    """Build section/cluster units from deterministic paragraph ranges.

    Profile ranges are preferred.  Gaps and documents without reliable section
    boundaries become contiguous semantic clusters, so every source paragraph
    belongs to exactly one unit.
    """
    paragraphs = [str(p or "") for p in paragraphs]
    if not paragraphs:
        return []
    max_chars = max(200, int(max_chars or DEFAULT_UNIT_CHARS))
    ranges: List[Tuple[int, int, str, str, str]] = []
    used = set()
    sections = sorted(
        (s for s in (document_profile or {}).get("sections") or []
         if isinstance(s, dict)),
        key=_section_sort_key,
    )
    for section in sections:
        try:
            start = max(0, int(section.get("start_segment")))
            end = min(len(paragraphs) - 1, int(section.get("end_segment")))
        except (TypeError, ValueError):
            continue
        if start > end or any(i in used for i in range(start, end + 1)):
            continue
        used.update(range(start, end + 1))
        sid = _clean(section.get("section_id"), 120) or f"section-{start + 1}"
        label = _clean(section.get("topic"), 240) or sid
        ranges.append((start, end, f"section-{sid}", label, "section"))

    # Fill uncovered ranges with clusters.  This also handles sparse LLM
    # section output without silently dropping source text.
    gap_start = None
    for index in range(len(paragraphs) + 1):
        covered = index < len(paragraphs) and index in used
        if index < len(paragraphs) and not covered and gap_start is None:
            gap_start = index
        if gap_start is not None and (index == len(paragraphs) or covered):
            ranges.append((gap_start, index - 1, "cluster", "", "semantic_cluster"))
            gap_start = None
    ranges.sort(key=lambda item: item[0])

    units: List[Dict[str, Any]] = []
    for start, end, prefix, label, kind in ranges:
        chunks = _chunk_range(paragraphs, start, end, max_chars, prefix, label, kind)
        for chunk in chunks:
            chunk["unit_id"] = f"unit-{len(units) + 1:04d}"
            chunk["source_range_id"] = prefix
            units.append(chunk)
    return units


def _digest_system_prompt() -> str:
    return (
        "你是长文翻译的文档理解器。只依据给出的语义单元原文，输出一个合法 JSON 对象。"
        "不要补写原文没有的事实；不确定的字段留空数组或空字符串。"
        "字段：summary（单元主旨）、key_entities（人物/地点/机构/作品）、"
        "key_terms（重要概念）、open_threads（需要后文确认的指代或叙事线索）、"
        "translation_notes（对语气、指代、术语连续性的翻译提示）。"
    )


def _normalize_digest(unit: Dict[str, Any], raw: Optional[Dict[str, Any]],
                     status: str = "model") -> Dict[str, Any]:
    raw = raw or {}
    return {
        "unit_id": unit["unit_id"],
        "kind": unit["kind"],
        "label": unit.get("label", ""),
        "start_segment": unit["start_segment"],
        "end_segment": unit["end_segment"],
        "summary": _clean(raw.get("summary") or raw.get("digest"), 1600),
        "key_entities": _string_list(raw.get("key_entities") or raw.get("entities")),
        "key_terms": _string_list(raw.get("key_terms") or raw.get("terms")),
        "open_threads": _string_list(raw.get("open_threads") or raw.get("threads")),
        "translation_notes": _string_list(raw.get("translation_notes") or raw.get("notes")),
        "status": status,
    }


def _fallback_digest(unit: Dict[str, Any]) -> Dict[str, Any]:
    source = unit.get("source", "")
    first = re.split(r"(?<=[.!?。！？])\s+", source.strip(), maxsplit=1)[0]
    digest = _normalize_digest(unit, {"summary": first or source[:600]},
                               status="deterministic_fallback")
    digest["warning"] = "模型未返回结构化语义摘要；仅使用单元首句作为临时上下文。"
    return digest


def generate_section_digests(
    units: Sequence[Dict[str, Any]],
    provider: str,
    api_key: str,
    model: str,
    target_lang: str = "",
    call_llm: Optional[Callable] = None,
    max_workers: int = DEFAULT_DIGEST_WORKERS,
    existing_digests: Optional[Sequence[Dict[str, Any]]] = None,
    on_progress: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Generate independent unit digests; results retain source order."""
    if not units:
        return [], ["语义摘要跳过：没有可用语义单元"]
    if call_llm is None:
        import core
        call_llm = core.call_llm
    system_prompt = _digest_system_prompt()

    def work(unit: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        user_prompt = (
            f"目标语言：{target_lang}\n"
            f"语义单元：{unit['unit_id']}（段落 {unit['start_segment']}-"
            f"{unit['end_segment']}）\n原文：\n{unit['source']}"
        )
        try:
            raw = _parse_object(_call(call_llm, provider, api_key, model,
                                      system_prompt, user_prompt))
            if raw is None:
                return _fallback_digest(unit), f"{unit['unit_id']}：返回不是结构化 JSON"
            return _normalize_digest(unit, raw), None
        except Exception as exc:  # provider failures must not corrupt the job
            raise_if_fatal(exc)
            if "任务已请求取消" in str(exc) or "请求取消" in str(exc):
                raise RuntimeError("任务已请求取消") from exc
            detail = _provider_failure_detail(
                exc, str(exc)[:160] or "模型请求失败")
            return _fallback_digest(unit), f"{unit['unit_id']}：语义摘要失败（{detail}）"

    existing_by_id = {
        str(item.get("unit_id")): dict(item)
        for item in existing_digests or []
        if isinstance(item, dict) and item.get("unit_id")
    }
    results: List[Optional[Dict[str, Any]]] = []
    for unit in units:
        saved = existing_by_id.get(str(unit["unit_id"]))
        if saved and saved.get("status") != "deterministic_fallback" \
                and saved.get("start_segment") == unit.get("start_segment") \
                and saved.get("end_segment") == unit.get("end_segment") \
                and saved.get("source") in (None, unit.get("source")):
            results.append(saved)
        else:
            results.append(None)
    warnings: List[str] = []
    pending = [(index, unit) for index, unit in enumerate(units)
               if results[index] is None]
    if pending:
        workers = max(1, min(int(max_workers or 1), len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, unit): index for index, unit in pending}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    digest, warning = future.result()
                except Exception as exc:  # defensive around custom executors/providers
                    raise_if_fatal(exc)
                    if "任务已请求取消" in str(exc) or "请求取消" in str(exc):
                        raise RuntimeError("任务已请求取消") from exc
                    digest = _fallback_digest(units[index])
                    detail = _provider_failure_detail(
                        exc, str(exc)[:160] or "模型请求失败")
                    warning = f"{units[index]['unit_id']}：语义摘要失败（{detail}）"
                results[index] = digest
                if warning:
                    warnings.append(warning)
                if on_progress is not None:
                    on_progress([item for item in results if item is not None])
    return [item for item in results if item is not None], warnings


def _synopsis_system_prompt() -> str:
    return (
        "你是长文翻译的全书理解器。根据多个语义单元摘要，输出一个合法 JSON 对象。"
        "只综合摘要中已有的信息，不臆造情节或事实。字段：summary（全书概要）、"
        "document_arc（论证/叙事发展）、themes（主题）、entities（跨单元实体）、"
        "terms（全书关键概念）、translation_notes（全书翻译连续性提示）。"
    )


def _normalize_synopsis(raw: Dict[str, Any], status: str = "model") -> Dict[str, Any]:
    return {
        "summary": _clean(raw.get("summary") or raw.get("synopsis"), 2400),
        "document_arc": _clean(raw.get("document_arc") or raw.get("arc"), 1600),
        "themes": _string_list(raw.get("themes")),
        "entities": _string_list(raw.get("entities")),
        "terms": _string_list(raw.get("terms") or raw.get("key_terms")),
        "translation_notes": _string_list(raw.get("translation_notes") or raw.get("notes")),
        "status": status,
    }


def _digest_text(digest: Dict[str, Any]) -> str:
    return (
        f"[{digest.get('unit_id', '')} · 段 {digest.get('start_segment', '')}-"
        f"{digest.get('end_segment', '')}]\n"
        f"主旨：{digest.get('summary', '')}\n"
        f"实体：{'、'.join(digest.get('key_entities') or [])}\n"
        f"概念：{'、'.join(digest.get('key_terms') or [])}\n"
        f"提示：{'、'.join(digest.get('translation_notes') or [])}"
    )


def _synopsis_text(synopsis: Dict[str, Any], index: int) -> str:
    return (
        f"[中间概要 {index + 1}]\n概要：{synopsis.get('summary', '')}\n"
        f"发展：{synopsis.get('document_arc', '')}\n"
        f"主题：{'、'.join(synopsis.get('themes') or [])}\n"
        f"实体：{'、'.join(synopsis.get('entities') or [])}\n"
        f"概念：{'、'.join(synopsis.get('terms') or [])}\n"
        f"翻译提示：{'、'.join(synopsis.get('translation_notes') or [])}"
    )


def _text_chunks(items: Sequence[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    chars = 0
    for item in items:
        item = str(item or "")
        if len(item) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, chars = [], 0
            chunks.extend(item[index:index + max_chars]
                          for index in range(0, len(item), max_chars))
            continue
        if current and chars + len(item) > max_chars:
            chunks.append("\n\n".join(current))
            current, chars = [], 0
        current.append(item)
        chars += len(item)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _reduce_synopsis(
    items: Sequence[str],
    provider: str,
    api_key: str,
    model: str,
    target_lang: str,
    call_llm: Callable,
    max_chars: int,
) -> Dict[str, Any]:
    chunk_limit = min(MAX_SYNOPSIS_CHARS, max(1000, int(max_chars or 12000)))
    chunks = _text_chunks(items, chunk_limit)
    if not chunks:
        return {"summary": "", "status": "unavailable"}
    reduced: List[Dict[str, Any]] = []
    for chunk in chunks:
        raw = _parse_object(_call(
            call_llm, provider, api_key, model, _synopsis_system_prompt(),
            f"目标语言：{target_lang}\n语义摘要块：\n{chunk}"))
        if raw is None:
            raise ValueError("模型未返回结构化 JSON")
        reduced.append(_normalize_synopsis(raw))
    if len(reduced) == 1:
        return reduced[0]
    return _reduce_synopsis(
        [_synopsis_text(item, index) for index, item in enumerate(reduced)],
        provider, api_key, model, target_lang, call_llm,
        min(MAX_SYNOPSIS_CHARS, chunk_limit * 2))


def generate_document_synopsis(
    digests: Sequence[Dict[str, Any]],
    provider: str,
    api_key: str,
    model: str,
    target_lang: str = "",
    call_llm: Optional[Callable] = None,
    max_chunk_chars: int = 12000,
) -> Tuple[Dict[str, Any], List[str]]:
    """Map unit digests into a hierarchical document-level synopsis."""
    if not digests:
        return {"summary": "", "status": "unavailable"}, ["全文概要跳过：没有语义摘要"]
    if call_llm is None:
        import core
        call_llm = core.call_llm
    try:
        return _reduce_synopsis(
            [_digest_text(digest) for digest in digests],
            provider, api_key, model, target_lang, call_llm, max_chunk_chars), []
    except Exception as exc:
        raise_if_fatal(exc)
        if "任务已请求取消" in str(exc) or "请求取消" in str(exc):
            raise RuntimeError("任务已请求取消") from exc
        detail = _provider_failure_detail(
            exc, str(exc)[:160] or "模型请求失败")
        warning = f"全文概要失败（{detail}）"
    else:
        warning = "全文概要失败：模型未返回结构化 JSON"

    fallback = _normalize_synopsis({
        "summary": "；".join(d.get("summary", "") for d in digests if d.get("summary"))[:2400],
        "translation_notes": [
            note for d in digests for note in (d.get("translation_notes") or [])
        ],
    }, status="deterministic_fallback")
    fallback["warning"] = warning
    return fallback, [warning]


def build_document_understanding(
    paragraphs: Sequence[str],
    document_profile: Optional[Dict[str, Any]],
    provider: str,
    api_key: str,
    model: str,
    target_lang: str = "",
    call_llm: Optional[Callable] = None,
    max_chars: int = DEFAULT_UNIT_CHARS,
    max_workers: int = DEFAULT_DIGEST_WORKERS,
    checkpoint_dir: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Build understanding artifacts with durable per-unit digest checkpoints."""
    units = build_semantic_units(paragraphs, document_profile, max_chars=max_chars)

    stored_digests: List[Dict[str, Any]] = []
    stored_synopsis: Optional[Dict[str, Any]] = None
    stored_complete = False
    if checkpoint_dir:
        root = Path(checkpoint_dir)
        try:
            saved_units = json.loads((root / "semantic_units.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            saved_units = None
        if saved_units != units:
            saved_units = None
        try:
            candidate_digests = json.loads(
                (root / "section_digests.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            candidate_digests = []
        if saved_units is not None and isinstance(candidate_digests, list):
            stored_digests = candidate_digests
        try:
            candidate_synopsis = json.loads(
                (root / "document_synopsis.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            candidate_synopsis = None
        if isinstance(candidate_synopsis, dict):
            stored_synopsis = candidate_synopsis
        digest_by_id = {
            str(item.get("unit_id")): item for item in stored_digests
            if isinstance(item, dict) and item.get("unit_id")
        }
        stored_complete = len(digest_by_id) == len(units) and all(
            unit["unit_id"] in digest_by_id
            and unit.get("start_segment") == digest_by_id[unit["unit_id"]].get("start_segment")
            and unit.get("end_segment") == digest_by_id[unit["unit_id"]].get("end_segment")
            for unit in units
        )
        if not stored_complete:
            stored_synopsis = None
        write_understanding_artifacts(
            root, units, stored_digests,
            stored_synopsis if stored_synopsis and stored_synopsis.get("status") != "pending"
            and stored_complete else {"summary": "", "status": "pending"})

    def checkpoint(digests: List[Dict[str, Any]]) -> None:
        if checkpoint_dir:
            write_understanding_artifacts(
                Path(checkpoint_dir), units, digests,
                {"summary": "", "status": "pending"})

    digests, warnings = generate_section_digests(
        units, provider, api_key, model, target_lang, call_llm,
        max_workers=max_workers, existing_digests=stored_digests,
        on_progress=checkpoint if checkpoint_dir else None)
    if (stored_synopsis and stored_synopsis.get("status") not in {
            "pending", "deterministic_fallback"}
            and all(digest.get("status") != "deterministic_fallback"
                    for digest in digests)
            and len(digests) == len(units) and stored_complete):
        synopsis, synopsis_warnings = stored_synopsis, []
    else:
        synopsis, synopsis_warnings = generate_document_synopsis(
            digests, provider, api_key, model, target_lang, call_llm)
    if checkpoint_dir:
        write_understanding_artifacts(Path(checkpoint_dir), units, digests, synopsis)
    return units, digests, synopsis, warnings + synopsis_warnings


def write_understanding_artifacts(
    job_root: Path,
    units: Sequence[Dict[str, Any]],
    digests: Sequence[Dict[str, Any]],
    synopsis: Dict[str, Any],
) -> None:
    """Persist the named understanding artifacts without touching state.json."""
    job_root.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("semantic_units.json", list(units)),
        ("section_digests.json", list(digests)),
        ("document_synopsis.json", synopsis),
    ):
        tmp = job_root / f"{name}.tmp"
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(job_root / name)


def digest_for_segment(digests: Sequence[Dict[str, Any]], segment_index: int) -> Optional[Dict[str, Any]]:
    for digest in digests or []:
        if not isinstance(digest, dict):
            continue
        try:
            index = int(segment_index)
            start = int(digest.get("start_segment"))
            end = int(digest.get("end_segment"))
        except (TypeError, ValueError):
            continue
        if start <= index <= end:
            return digest
    return None


def target_context_level(pair: Dict[str, Any]) -> str:
    """Return the strongest provenance available for target continuity."""
    if pair.get("human_accepted") or pair.get("accepted_by_human"):
        return "human_accepted"
    provenance = str(pair.get("target_provenance") or "")
    if provenance in ("human_accepted", "human"):
        return "human_accepted"
    if pair.get("reviewed") and (pair.get("from_tm") or provenance == "tm_approved"):
        return "tm_approved"
    if pair.get("reviewed") or provenance == "reviewed":
        return "reviewed"
    return "generated"


_LEVEL_INDEX = {level: index for index, level in enumerate(TARGET_CONTEXT_LEVELS)}


def select_target_context(
    pairs: Sequence[Dict[str, Any]],
    before_index: int,
    limit: int = 2,
) -> List[Dict[str, Any]]:
    """Select recent target context with explicit provenance priority.

    与"先建完整候选列表、再按 `TARGET_CONTEXT_LEVELS` 遍历"的朴素写法输出逐字
    等价（`tests/context_target_selection_equivalence_test.py` 用原实现副本做
    随机差分守住），但**从不物化前缀**：Performance Phase 5 的 setup 审计里，
    `list(pairs)[:before_index]` + 每个历史段落一个 dict 在 20k 段文档上占了
    100s setup 窗口中的 31s（`previous_target_selection` 的第二个调用点）。

    做法：反向扫描，每个 provenance 层只保留最新 `limit` 条，`_clean` 只对最终
    入选的少数几条执行。**提前退出是有条件的**——见下方注释，只有最高优先层可以
    提前退出；其余层必须扫到 index 0，否则结果可能被更早的同层段落改写。
    """
    limit = max(0, limit)
    if limit == 0:
        return []
    try:
        available = len(pairs)
    except TypeError:
        pairs = list(pairs)
        available = len(pairs)
    before = max(0, min(before_index, available))
    newest: List[List[Any]] = [[] for _ in TARGET_CONTEXT_LEVELS]
    for index in range(before - 1, -1, -1):
        pair = pairs[index]
        target = pair.get("accepted_target") or pair.get("target")
        if not target:
            continue
        level = target_context_level(pair)
        slot = _LEVEL_INDEX.get(level)
        if slot is None:
            continue
        bucket = newest[slot]
        if len(bucket) >= limit:
            continue
        bucket.append((index, level))
        # 提前退出只对最高优先层成立。`selected` 是按层拼接"该层最新 limit 条"后
        # 再取 [:limit]，所以低位层一旦被用到就贡献**它全部的**候选——若更早的位置
        # 还有同层段落，结果会变，而"没有更早的同层段落"只有扫到 index 0 才能证明。
        # 最高优先层是唯一例外：它凑满 limit 条后，更旧的本层段落永远进不了
        # `selected[:limit]`。
        if slot == 0 and len(bucket) >= limit:
            break
    picked: List[Any] = []
    remaining = limit
    for bucket in newest:
        if remaining <= 0:
            break
        take = bucket[:remaining]
        remaining -= len(take)
        picked.extend(take)
    picked.sort(key=lambda item: item[0])
    return [{
        "segment_index": index,
        "source": _clean(pairs[index].get("source"), 800),
        "target": _clean(pairs[index].get("accepted_target")
                         or pairs[index].get("target"), 1200),
        "level": level,
    } for index, level in picked]


def recent_target_context(
    pairs: Sequence[Dict[str, Any]],
    before_index: int,
    count: int = 2,
    exclude_indexes: Optional[Sequence[int]] = None,
) -> List[Dict[str, Any]]:
    """返回 `before_index` 之前最近 `count` 个有译文的段落，按 segment 升序。

    **与 `select_target_context(pairs, before_index, limit=max(count, before_index))`
    过滤掉 `exclude_indexes` 后取末 `count` 个逐字等价**——因为该调用点总是传入
    `limit >= before_index >= 候选数`，层级优先循环不可能截断，结果就是"全部候选按
    segment 升序"再取尾部。

    差异只在代价：原式要 `list(pairs)[:before_index]` 并为每个历史段落构造字典
    （每批 O(offset)，整篇 O(n²)：20k 段实测 1.12 亿次元素访问、占 setup 65%）。
    本函数**从后往前扫并在取满 `count` 后立刻停止**，典型 O(count)，最坏仍为 O(offset)。
    语义等价性由 `tests/context_target_selection_equivalence_test.py` 逐例差分守住。

    注意：本函数**不**实现 `select_target_context` 的层级优先截断语义。只有当
    `count >= 候选数`（即 `limit` 不参与截断）时两者才等价；需要层级优先的调用点
    必须继续用 `select_target_context`。
    """
    wanted = max(0, int(count or 0))
    if wanted == 0:
        return []
    try:
        available = len(pairs)
    except TypeError:
        pairs = list(pairs)
        available = len(pairs)
    before = max(0, min(int(before_index or 0), available))
    excluded = exclude_indexes or ()
    selected: List[Dict[str, Any]] = []
    for index in range(before - 1, -1, -1):
        if index in excluded:
            continue
        pair = pairs[index]
        target = pair.get("accepted_target") or pair.get("target")
        if not target:
            continue
        selected.append({
            "segment_index": index,
            "source": _clean(pair.get("source"), 800),
            "target": _clean(target, 1200),
            "level": target_context_level(pair),
        })
        if len(selected) >= wanted:
            break
    selected.reverse()
    return selected


def compile_context_packet(
    document_profile: Optional[Dict[str, Any]],
    document_synopsis: Optional[Dict[str, Any]],
    section_digest: Optional[Dict[str, Any]],
    glossary_text: str,
    previous_source: Sequence[str],
    previous_target: Sequence[Dict[str, Any]],
    next_source: Sequence[str],
    current_batch: Sequence[str],
    style_rules: str = "",
    entity_hints: Optional[Sequence[Dict[str, Any]]] = None,
    context_budget_chars: int = DEFAULT_CONTEXT_BUDGET_CHARS,
) -> Dict[str, Any]:
    """Compile a stable-order packet for generation/review prompts.

    The packet keeps the source batch authoritative and treats all surrounding
    material as bounded context.  Exact duplicate source snippets are removed
    at the boundary so callers can safely provide overlapping windows.
    """
    current = [str(item or "").strip() for item in current_batch or []]
    current = [item for item in current if item]
    raw_previous_source_count = len(previous_source or [])
    raw_next_source_count = len(next_source or [])
    raw_previous_targets = list(previous_target or [])
    previous_target: List[Dict[str, Any]] = []
    seen_target = set()
    for raw in raw_previous_targets:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = (item.get("segment_index"), item.get("source"), item.get("target"))
        if key in seen_target:
            continue
        seen_target.add(key)
        previous_target.append(item)
        if len(previous_target) >= 8:
            break
    # A paragraph that is about to be sent again as the "原文" of its accepted
    # translation must not also be sent as a bare previous-source bullet: same
    # text twice in one prompt, and the second copy carries strictly more
    # information (it is attached to the accepted target).
    accepted_sources = [item.get("source") for item in previous_target
                        if str(item.get("source") or "").strip()]
    previous_source = _dedupe_strings(
        previous_source, exclude=[*current, *accepted_sources], limit=8)
    next_source = _dedupe_strings(
        next_source, exclude=[*current, *previous_source, *accepted_sources],
        limit=8)
    try:
        budget = max(1200, int(context_budget_chars or DEFAULT_CONTEXT_BUDGET_CHARS))
    except (TypeError, ValueError):
        budget = DEFAULT_CONTEXT_BUDGET_CHARS
    return {
        "document_profile": document_profile or {},
        "document_synopsis": document_synopsis or {},
        "section_digest": section_digest or {},
        "locked_glossary": glossary_text or "",
        "style_rules": style_rules or "",
        "previous_source_context": previous_source,
        "previous_accepted_target_context": previous_target,
        "next_source_context": next_source,
        "current_batch": current,
        "entity_hints": [dict(item) for item in entity_hints or []],
        "context_budget_chars": budget,
        "context_dedupe_removed_count": max(
            0, raw_previous_source_count - len(previous_source)
        ) + max(0, raw_next_source_count - len(next_source)),
    }


def _compact_profile(profile: Dict[str, Any]) -> str:
    """Render high-value profile fields without repeating raw JSON noise."""
    if not isinstance(profile, dict) or not profile:
        return "{}"
    fields = (
        ("domain", "领域"), ("subdomain", "细分领域"), ("genre", "文类"),
        ("audience", "读者"), ("register", "语域"),
        ("style_constraints", "风格约束"),
    )
    lines = [
        f"{label}：{_clean(profile.get(key), 260)}"
        for key, label in fields
        if _clean(profile.get(key), 260)
    ]
    sections = profile.get("sections") or []
    labels = [
        _clean(item.get("topic") or item.get("section_id"), 100)
        for item in sections if isinstance(item, dict)
    ]
    labels = [item for item in labels if item]
    if labels:
        lines.append("章节：" + "；".join(labels[:12]))
    return "\n".join(lines)[:1800] or "{}"


# Prompt-cost categories.  Every rendered context section belongs to exactly one
# bucket so a run can report *where* input tokens go instead of only a total.
# The mapping is deliberately coarse: it exists to steer optimization, not to
# bill anyone.
PROMPT_COST_CATEGORIES = {
    "【文档画像】": "document_context",
    "【全文概要】": "document_context",
    "【当前语义单元摘要】": "document_context",
    "【当前单元翻译提示】": "document_context",
    "【文体与翻译规则】": "style",
    "【锁定术语与范围规则（项目人工锁定优先）】": "glossary",
    "【专名/实体连续性提示（仅作建议；人工实体选择优先）】": "entities",
    "【前文原文上下文】": "neighbor_context",
    "【前文已接受译文连续性】": "neighbor_context",
    "【后文原文上下文】": "neighbor_context",
    # dynamic_v2-only sections.  A `fixed` packet never carries these keys, so
    # they never appear in a production prompt or its cost report.
    "【翻译记忆参考（近似匹配，仅供一致译法参考）】": "tm",
    "【远距离相关段落（历史上下文，仅供一致性参考）】": "distant_context",
    "【项目知识反馈（人工确认优先）】": "project_knowledge",
}
CURRENT_BATCH_TITLE = "【待翻译段落（按序号返回等长数组）】"


def _render_entity_hints(hints: Sequence[Dict[str, Any]]) -> str:
    """Render advisory entity continuity hints in the compactest useful form.

    The section header already states that these are suggestions and that human
    decisions win, so repeating a constant provenance/precedence string on every
    line is pure prompt noise.  Only the fields that differ per entity and can
    change the translation survive: the pair itself, its type, aliases and an
    optional identity note.
    """
    lines = []
    for item in hints:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_form") or "").strip()
        target = str(item.get("preferred_target") or "").strip()
        if not source and not target:
            continue
        line = f"- {source} -> {target}"
        entity_type = str(item.get("entity_type") or "").strip()
        if entity_type:
            line += f"（{entity_type}）"
        aliases = [str(alias).strip() for alias in item.get("aliases") or []
                   if str(alias).strip()]
        if aliases:
            line += f"；别名：{'、'.join(aliases)}"
        note = str(item.get("note") or "").strip()
        if note:
            line += f"；说明：{note}"
        lines.append(line)
    return "\n".join(lines)


def _render_context_packet(packet: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Render a packet and return prompt text plus bounded-context diagnostics."""
    profile = _compact_profile(packet.get("document_profile") or {})
    synopsis = packet.get("document_synopsis") or {}
    digest = packet.get("section_digest") or {}
    sections = [
        ("【文档画像】", profile),
        ("【文体与翻译规则】", _clean(packet.get("style_rules"), 1600)),
        # Locked terminology is authoritative and therefore gets budget
        # priority over broad narrative context.
        ("【锁定术语与范围规则（项目人工锁定优先）】",
         _clean(packet.get("locked_glossary"), 2200)),
        ("【当前语义单元摘要】", _clean(digest.get("summary"), 1600)),
        ("【当前单元翻译提示】", "、".join(digest.get("translation_notes") or [])),
        # One whole-document narrative and one local narrative.  The synopsis
        # also carries `document_arc`, which is a second whole-document
        # narrative produced from the same section digests; measured 8-gram
        # containment against `summary` is >60%, so shipping both bought a
        # restatement for the price of a full section.  `document_arc` stays in
        # the persisted synopsis for review/UI; it is simply not repeated in
        # every batch prompt.
        ("【全文概要】", _clean(synopsis.get("summary"), 2400)),
        ("【专名/实体连续性提示（仅作建议；人工实体选择优先）】",
         _render_entity_hints(packet.get("entity_hints") or [])),
        ("【前文原文上下文】", "\n".join(
            f"- {item}" for item in packet.get("previous_source_context") or [])),
        ("【前文已接受译文连续性】", "\n".join(
            f"- 段 {item.get('segment_index', '?')} [{item.get('level', 'generated')}] "
            f"原文：{item.get('source', '')}\n  译文：{item.get('target', '')}"
            for item in packet.get("previous_accepted_target_context") or [])),
        ("【后文原文上下文】", "\n".join(
            f"- {item}" for item in packet.get("next_source_context") or [])),
    ]
    # dynamic_v2-only sections.  Appended after the fixed sections and before
    # the current batch so the source batch stays last and authoritative.  When
    # these keys are absent (every `fixed` packet) the rendered output is
    # byte-identical to the pre-dynamic renderer.
    dynamic_sections = []
    tm_items = packet.get("tm_context") or []
    if tm_items:
        dynamic_sections.append((
            "【翻译记忆参考（近似匹配，仅供一致译法参考）】",
            "\n".join(
                f"- 相似度 {float(item.get('similarity') or 0):.2f}｜原文：{item.get('source', '')}"
                f"\n  参考译文：{item.get('target', '')}"
                for item in tm_items if isinstance(item, dict))))
    distant_items = packet.get("distant_context") or []
    if distant_items:
        dynamic_sections.append((
            "【远距离相关段落（历史上下文，仅供一致性参考）】",
            "\n".join(
                f"- {item.get('source', '')}"
                for item in distant_items if isinstance(item, dict))))
    knowledge_items = [str(item).strip() for item in packet.get("project_knowledge") or []
                       if str(item).strip()]
    if knowledge_items:
        dynamic_sections.append((
            "【项目知识反馈（人工确认优先）】",
            "\n".join(f"- {item}" for item in knowledge_items)))
    sections.extend(dynamic_sections)
    try:
        budget = max(1200, int(packet.get("context_budget_chars") or
                               DEFAULT_CONTEXT_BUDGET_CHARS))
    except (TypeError, ValueError):
        budget = DEFAULT_CONTEXT_BUDGET_CHARS
    rendered_sections: List[str] = []
    used = 0
    truncated = False
    section_chars: Dict[str, int] = {}
    for title, body in sections:
        body = str(body or "").strip()
        if not body:
            continue
        full = f"{title}\n{body}"
        remaining = budget - used
        if remaining <= len(title) + 1:
            truncated = True
            break
        if len(full) > remaining:
            body = body[:max(0, remaining - len(title) - 1)].rstrip()
            if not body:
                truncated = True
                break
            clipped = f"{title}\n{body}…"
            rendered_sections.append(clipped)
            section_chars[title] = len(clipped)
            used = budget
            truncated = True
            break
        rendered_sections.append(full)
        section_chars[title] = len(full)
        used += len(full) + 2
    prefix_text = "\n\n".join(rendered_sections)
    if len(prefix_text) > budget:
        # Account for the separators inserted by the join.  This final guard
        # makes the advertised budget a hard bound even when a clipped section
        # ends exactly at the boundary.
        prefix_text = prefix_text[:budget]
        truncated = True
    current = packet.get("current_batch") or []
    current_section = CURRENT_BATCH_TITLE + "\n" + "\n".join(
        f"{index + 1}. {source}" for index, source in enumerate(current))
    rendered = "\n\n".join([prefix_text, current_section]) if prefix_text \
        else current_section
    # Coarse prompt-cost buckets.  Character counts, not tokens: the caller
    # converts with the same estimator the usage ledger already uses.
    category_chars: Dict[str, int] = {}
    for title, count in section_chars.items():
        category = PROMPT_COST_CATEGORIES.get(title, "other")
        category_chars[category] = category_chars.get(category, 0) + count
    category_chars["source"] = len(current_section)
    return rendered, {
        "context_budget_chars": budget,
        "context_prefix_chars": len(prefix_text),
        "context_truncated": truncated,
        "dedupe_removed_count": int(packet.get("context_dedupe_removed_count") or 0),
        "section_chars": section_chars,
        "category_chars": category_chars,
        "source_chars": len(current_section),
    }


def render_context_packet(packet: Dict[str, Any]) -> str:
    """Render the packet with a stable prefix and current batch at the end."""
    return _render_context_packet(packet)[0]


def render_context_packet_with_stats(
        packet: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Render a packet together with the per-category prompt-cost split.

    Callers that account for prompt tokens use this instead of rendering twice.
    """
    return _render_context_packet(packet)


def context_metadata(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Small audit record; do not persist full prompt text in job state."""
    rendered, render_stats = _render_context_packet(packet)
    metadata = {
        "section_id": (packet.get("section_digest") or {}).get("unit_id"),
        "previous_source_count": len(packet.get("previous_source_context") or []),
        "previous_target_segments": [
            item.get("segment_index") for item in packet.get("previous_accepted_target_context") or []
        ],
        "previous_target_levels": [
            item.get("level") for item in packet.get("previous_accepted_target_context") or []
        ],
        "next_source_count": len(packet.get("next_source_context") or []),
        "current_batch_count": len(packet.get("current_batch") or []),
        "prompt_chars": len(rendered),
        "context_prefix_chars": render_stats["context_prefix_chars"],
        "context_budget_chars": render_stats["context_budget_chars"],
        "context_truncated": render_stats["context_truncated"],
        "dedupe_removed_count": render_stats["dedupe_removed_count"],
        "current_batch_chars": sum(len(item) for item in packet.get("current_batch") or []),
        "entity_hint_count": len(packet.get("entity_hints") or []),
    }
    # Experimental-selector keys are attached ONLY to a dynamic_v2 packet.  A
    # production `fixed` packet never carries `context_mode`, so its audit record
    # is unchanged and stays byte-comparable with the accepted baseline.
    # See docs/dynamic-context-v2-adr.md.
    if packet.get("context_mode") == "dynamic_v2":
        metadata["context_mode"] = "dynamic_v2"
        metadata["dynamic_context"] = packet.get("dynamic_diagnostics") or {}
    return metadata
