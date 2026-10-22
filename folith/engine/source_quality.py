"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import copy
import os
import threading
from collections import OrderedDict
from datetime import datetime
from datetime import timezone
from folith import document_ir as _document_ir
from folith import finalization as _finalization
from folith import segmentation as _segmentation
from folith import source_cleanup as _source_cleanup
from folith import source_quality as _source_quality
from folith.engine.locks import _serialized_job_mutation
from folith.engine.ocr_config import normalize_ocr_mode
from folith.engine.tm_store import state_target_lang
from folith.engine.tm_store import tm_project_id
from pathlib import Path
from folith.engine._link import core as _c


def _text_layer_decision(quality, ocr_mode):
    """Return ``(use_text_layer, trigger, warning)`` for one document.

    ``force`` and ``off`` are hard user contracts and are checked first, so the
    automatic gate can never override them.
    """
    status = str((quality or {}).get("status") or "absent")
    mode = normalize_ocr_mode(ocr_mode)
    if mode == "force":
        return False, "ocr_mode_force", ""
    if mode == "off":
        warning = ""
        if status == "rejected":
            warning = ("OCR 模式为「仅文本层」：文本层质量检测未通过，"
                       "但按设置未执行 OCR，正文可能不可用。")
        elif status == "absent":
            warning = ("OCR 模式为「仅文本层」：该 PDF 没有可用文本层，"
                       "按设置未执行 OCR，可能提取不到正文。")
        return True, "ocr_mode_text_layer_only", warning
    # auto
    if status == "accepted":
        return True, "text_layer_accepted", ""
    if status == "uncertain":
        return True, "text_layer_uncertain", (
            "PDF 含有文本层，但其提取质量不确定；已按「自动」使用文本层。"
            "如结果异常，请在任务设置中改用「强制 OCR」。")
    if status == "absent":
        return False, "no_text_layer", ""
    return False, "text_layer_quality_rejected", ""


def friendly_extraction_reason(text):
    """把提取失败的原始信息翻译成可执行的中文说明（无法识别时保留原文）。"""
    raw = str(text or "").strip()
    if not raw:
        return _c._NO_TEXT_REASON
    lowered = raw.casefold()
    for needle, reason in _c._EXTRACTION_FAILURE_HINTS:
        if needle in lowered:
            return reason
    return raw


def extraction_failure_detail(warnings):
    """Most specific reason for a failed extraction.

    The OCR path can append a generic follow-up ("OCR 未生成可用段落…") *after* a
    precise blocking error ("OCR 语言包未安装：Deutsch（deu）…").  Surfacing only
    the last warning hides the actual cause — the user is told to install a
    language pack without being told which one, or that English was used.  Prefer
    a concrete reason; fall back to the last warning when every entry is generic.
    """
    items = [str(item or "").strip() for item in warnings or []]
    items = [item for item in items if item]
    for warning in reversed(items):
        if _c._GENERIC_EXTRACTION_WARNING not in warning:
            return warning
    return items[-1] if items else "未知提取错误"


def _build_extraction_report(filename, paragraphs, *, ocr_used=False,
                             unsupported=None, facts=None, warnings=None):
    values = [str(item or "") for item in paragraphs or []]
    unsupported = [item for item in unsupported or [] if item.get("count")]
    notes = []
    ext = Path(str(filename or "")).suffix.lower() or "未知格式"
    if ocr_used:
        notes.append("本文档没有文本层，正文由本地 OCR 生成；OCR 会引入错字与假断行。")
    if unsupported:
        notes.append("未纳入翻译的内容已在下方逐项列出；它们保留在源文件里，"
                     "不会出现在译文文档中。")
    notes.append("导出是**重建的译文文档**，不是原文件的格式保真输出："
                 "排版、表格、图片与样式不还原。")
    return {
        "version": _c.EXTRACTION_REPORT_VERSION,
        "format": ext.lstrip("."),
        "filename": str(filename or ""),
        "status": "partial" if unsupported else "complete",
        "extracted": {
            "paragraphs": len(values),
            "characters": sum(len(item) for item in values),
            "pages": (facts or {}).get("pages"),
            "text_pages": (facts or {}).get("text_pages"),
            "empty_pages": (facts or {}).get("empty_pages"),
            "images": (facts or {}).get("images", 0),
            "ocr_used": bool(ocr_used),
        },
        "unsupported": unsupported,
        "unsupported_total": sum(int(item.get("count") or 0) for item in unsupported),
        "notes": notes,
        "warnings": [str(item) for item in warnings or []],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_document_paragraphs(filename, file_bytes, *, ocr_max_pages=3,
                                 on_progress=None, ocr_mode=None,
                                 source_language=None, ocr_backend=None):
    """Backward-compatible wrapper: ``(paragraphs, warnings)``.

    New callers should use :func:`extract_document_paragraphs_with_report` so
    the import scope can be shown to the user.  ``ocr_mode`` / ``source_language``
    are forwarded so legacy callers can still pin the ingestion contract.
    """
    paragraphs, warnings, _report = _c.extract_document_paragraphs_with_report(
        filename, file_bytes, ocr_max_pages=ocr_max_pages, on_progress=on_progress,
        ocr_mode=ocr_mode, source_language=source_language, ocr_backend=ocr_backend)
    return paragraphs, warnings


def cleanup_source_paragraphs(paragraphs, provider, api_key, model, *,
                              call_llm_fn=None, on_progress=None,
                              max_batch_chars=None, max_batch_items=None,
                              parallelism=None, confidence_by_index=None,
                              confidence_threshold=None, checkpoint_dir=None,
                              cancel_check=None, max_retries=None,
                              retry_backoff_seconds=None,
                              request_interval_seconds=None, profiler=None,
                              ocr_language="", source_language="",
                              text_direction="", ocr_engine_code="", ocr_backend="tesseract",
                              frozen_indices=(), hard_breaks_after=()):
    """Repair OCR characters/line breaks while preserving indexed source items.

    This compatibility wrapper keeps the cleanup contract in the core API so
    callers and tests do not need to know the internal module layout.
    """
    cleanup_call = call_llm_fn
    if cleanup_call is None:
        # ``source_cleanup`` executes provider calls in worker threads.  The
        # normal LLM router keeps base_url/reasoning/runtime job id in
        # thread-local context, so copy the caller's context explicitly; a
        # worker must not silently fall back to the provider's default URL.
        inherited_base_url = getattr(_c._LLM_CTX, "base_url", None)
        inherited_reasoning = getattr(_c._LLM_CTX, "reasoning_effort", None)
        inherited_job_id = getattr(_c._RUNTIME_CTX, "job_id", None)

        def cleanup_call(provider_name, api_key_value, model_name,
                         system_prompt, user_prompt, **kwargs):
            if inherited_job_id:
                _c._RUNTIME_CTX.job_id = inherited_job_id
            inherited_kwargs = {}
            if inherited_base_url:
                inherited_kwargs["base_url"] = inherited_base_url
            if inherited_reasoning:
                inherited_kwargs["reasoning_effort"] = inherited_reasoning
            return _c.call_llm(
                provider_name, api_key_value, model_name, system_prompt,
                user_prompt, **inherited_kwargs, **kwargs)

    return _source_cleanup.cleanup_source_paragraphs(
        paragraphs, provider, api_key, model,
        call_llm=cleanup_call or _c.call_llm,
        on_progress=on_progress,
        max_batch_chars=max_batch_chars or _source_cleanup.MAX_BATCH_CHARS,
        max_batch_items=max_batch_items or _source_cleanup.MAX_BATCH_ITEMS,
        parallelism=parallelism or _source_cleanup.DEFAULT_PARALLELISM,
        confidence_by_index=confidence_by_index,
        confidence_threshold=(confidence_threshold
                              if confidence_threshold is not None
                              else _source_cleanup.DEFAULT_CONFIDENCE_THRESHOLD),
        checkpoint_dir=checkpoint_dir,
        cancel_check=cancel_check,
        max_retries=(max_retries if max_retries is not None
                     else _source_cleanup.DEFAULT_MAX_RETRIES),
        retry_backoff_seconds=(retry_backoff_seconds
                               if retry_backoff_seconds is not None
                               else _source_cleanup.DEFAULT_RETRY_BACKOFF_SECONDS),
        request_interval_seconds=request_interval_seconds or 0.0,
        profiler=profiler,
        ocr_language=ocr_language or "",
        source_language=source_language or "",
        text_direction=text_direction or "",
        ocr_engine_code=ocr_engine_code or "",
        ocr_backend=ocr_backend,
        frozen_indices=frozen_indices,
        hard_breaks_after=hard_breaks_after,
    )


def cleanup_constraints(extraction_report, count):
    """原文整理在"混合 PDF"上的约束：哪些段不能动、哪些段之后必须断开。

    混合 PDF = 有文字层的页 + 少量扫描页（扫描页由视觉模型读出）。文字层页面的
    断行、页眉残片、孤立符号行和普通原生 PDF 一样需要整理；视觉模型读出的段已经
    是成段的语义文本，原样保留、不与邻段拼接。两段原生文字之间隔着一页（扫描页
    或空页）时也必须断开：句子不会跨过中间那一页接上。

    返回 ``cleanup_source_paragraphs`` 的关键字参数；不是混合来源时为空。
    """
    report = extraction_report if isinstance(extraction_report, dict) else {}
    if report.get("semantic_source") != "mixed":
        return {}
    document = report.get("document_ir") or {}
    blocks = {b.get("block_id"): b for b in document.get("blocks") or []}
    pages = {p.get("page_id"): p.get("page_index") for p in document.get("pages") or []}
    ids = list(document.get("translation_block_ids") or [])[:count]
    frozen, page_of = [], {}
    for index, block_id in enumerate(ids):
        block = blocks.get(block_id) or {}
        if (block.get("provenance") or {}).get("provider") != "native_pdf":
            frozen.append(index)
        spans = (block.get("source_anchor") or {}).get("source_spans") or []
        found = [pages.get(span.get("page_id")) for span in spans]
        found = [value for value in found if value is not None]
        if found:
            page_of[index] = (min(found), max(found))
    hard_breaks = [index for index in range(len(ids) - 1)
                   if index in page_of and index + 1 in page_of
                   and page_of[index + 1][0] - page_of[index][1] > 1]
    return {"frozen_indices": frozen, "hard_breaks_after": hard_breaks}


def _source_segmentation_mode(state=None, explicit=None):
    """Resolve the CAT segmentation mode.

    Natural sentence units are now the default for every imported format.  An
    explicit ``paragraph`` request remains supported for controlled legacy or
    manual imports, while a saved legacy paragraph marker is treated as a
    migration trigger rather than silently preserving the old CAT behaviour.
    """
    saved = {}
    if isinstance(state, dict):
        saved = state.get("segmentation") or {}
    configured = explicit
    if configured is None:
        configured = os.environ.get("FOLIOTHREAD_SEGMENTATION_MODE")
    if isinstance(saved, dict) and saved.get("locked") \
            and saved.get("mode") in _segmentation.SUPPORTED_MODES:
        # Once stage 1 has started, the positional CAT contract is immutable.
        # Ignore later UI/environment overrides so a resume cannot reindex
        # existing translations underneath the reviewer.
        configured = saved.get("mode")
    if configured is None and isinstance(saved, dict):
        # ``paragraph`` is the pre-sentence default.  Let the state migration
        # gate decide which individual paragraphs can be upgraded safely.
        configured = (saved.get("mode") if saved.get("locked") else
                      "sentence" if saved.get("mode") == "paragraph"
                      else saved.get("mode"))
    return _segmentation.resolve_mode(
        configured,
        default="sentence",
    )


def segment_source_paragraphs(paragraphs, *, state=None, mode=None,
                              source_lang=None):
    """Convert cleaned paragraphs into ordered CAT translation units.

    ``source_paragraphs`` is kept by the caller for audit/export.  The return
    value is the translation-facing sequence plus a compact paragraph mapping.
    """
    resolved_mode = _source_segmentation_mode(state, mode)
    language = source_lang
    if language is None and isinstance(state, dict):
        language = state.get("source_lang")
    return _segmentation.segment_paragraphs(
        paragraphs, mode=resolved_mode, source_lang=language)


#: Last few audits keyed by the exact source text.  The audit is a pure
#: function of the paragraphs, and the workbench asks for it several times per
#: refresh while only the translations change (#75).
_AUDIT_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_AUDIT_CACHE_MAX = 4
_AUDIT_CACHE_LOCK = threading.Lock()


def audit_source_quality(paragraphs):
    """Deterministically quarantine OCR fragments before translation."""
    key = tuple(str(item) if item is not None else None for item in paragraphs or [])
    with _AUDIT_CACHE_LOCK:
        cached = _AUDIT_CACHE.get(key)
        if cached is not None:
            _AUDIT_CACHE.move_to_end(key)
            return copy.deepcopy(cached)
    report = _source_quality.audit_source_segments(paragraphs)
    with _AUDIT_CACHE_LOCK:
        _AUDIT_CACHE[key] = copy.deepcopy(report)
        while len(_AUDIT_CACHE) > _AUDIT_CACHE_MAX:
            _AUDIT_CACHE.popitem(last=False)
    return report


def _source_quality_gate_enabled(state):
    """Apply the OCR admission gate to rough OCR/text-layer PDF sources only.

    A PDF whose semantics came from the VLM document-understanding path holds
    trusted semantic blocks, not OCR fragments.  The OCR-era heuristics
    (isolated numerals, lowercase stubs, unmatched brackets, short spaced-dash
    runs) would quarantine perfectly valid short headings and paragraph blocks
    merely because the file is a PDF.  Provenance comes from
    ``extraction_report.semantic_source``, which stage 1 persists before any
    consumer asks for the gate; legacy states without the field keep the gate
    (fail closed).
    """
    state = state or {}
    if not str(state.get("filename") or "").lower().endswith(".pdf"):
        return False
    semantic_source = (state.get("extraction_report") or {}).get("semantic_source")
    return semantic_source not in _c._VLM_SEMANTIC_SOURCES


def _confirmed_source_quality_indexes(state, paragraphs):
    """Return confirmations that still match both stable segment ID and source."""
    pairs = (state or {}).get("pairs") or []
    confirmations = (state or {}).get("source_quality_confirmations") or []
    confirmed = set()
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or index >= len(paragraphs):
            continue
        uid = str(pair.get("segment_uid") or "")
        source = str(paragraphs[index] or "")
        if not uid or not source or str(pair.get("source") or "") != source:
            continue
        if any(isinstance(item, dict)
               and str(item.get("segment_uid") or "") == uid
               and str(item.get("source") or "") == source
               for item in confirmations):
            confirmed.add(index)
    return confirmed


def _filter_confirmed_source_quality(report, state, paragraphs):
    confirmed = _confirmed_source_quality_indexes(state, paragraphs)
    if not confirmed:
        return report
    report["flags"] = [
        flag for flag in report.get("flags") or []
        if flag.get("segment_index") not in confirmed
    ]
    report["source_lineage_flags"] = [
        flag for flag in report.get("source_lineage_flags") or []
        if flag.get("segment_index") not in confirmed
    ]
    report["clusters"] = [
        [index for index in cluster if index not in confirmed]
        for cluster in report.get("clusters") or []
        if any(index not in confirmed for index in cluster)
    ]
    report["flagged_count"] = len(report["flags"])
    report["status"] = "blocked" if report["flags"] else "passed"
    report["confirmed_count"] = len(confirmed)
    return report


def _grounded_block_index(state):
    """Map IR block ids to the blocks that carry their text and geometry."""
    document = ((state or {}).get("extraction_report") or {}).get("document_ir")
    blocks = document.get("blocks") if isinstance(document, dict) else None
    return {
        str(block.get("block_id")): block
        for block in blocks or []
        if isinstance(block, dict) and block.get("block_id")
    }


def _block_bbox(block):
    anchor = (block or {}).get("source_anchor") or {}
    spans = anchor.get("source_spans") or []
    if spans and isinstance(spans[0], dict):
        return _document_ir.normalized_bbox(spans[0].get("bbox"))
    return None


def _body_text_bbox(state, by_id=None):
    """Robust body text column of the grounded paragraph blocks.

    Percentiles rather than a bounding union: publisher stamps and rotated
    margin text are sometimes typed as ``paragraph`` too, and a single outlier
    must not widen the column until the margin counts as "inside" it.
    """
    boxes = []
    if by_id is None:
        by_id = _grounded_block_index(state)
    for block in by_id.values():
        if str(block.get("type") or "") != "paragraph":
            continue
        box = _block_bbox(block)
        if box:
            boxes.append(box)
    if not boxes:
        return None

    def pick(values, fraction):
        ordered = sorted(values)
        position = int(round(fraction * (len(ordered) - 1)))
        return ordered[max(0, min(position, len(ordered) - 1))]

    return [
        pick([box[0] for box in boxes], 0.10),
        pick([box[1] for box in boxes], 0.05),
        pick([box[2] for box in boxes], 0.90),
        pick([box[3] for box in boxes], 0.95),
    ]


def _segment_source_quality_evidence(state, index, paragraphs=None, *,
                                     by_id=None, body_bbox=None):
    """Resolve the grounded container that decides one flagged segment.

    The IR block is preferred because it is the verified extraction unit; the
    cleaned paragraph is the fallback for sources without document IR.
    ``by_id`` and ``body_bbox`` are document-wide: a caller classifying many
    segments passes them once instead of rescanning every block per segment
    (#75: about a second per workbench refresh on a 5000-block PDF).
    """
    paragraphs = (paragraphs if paragraphs is not None
                  else (state or {}).get("paras") or [])
    pair = None
    pairs = (state or {}).get("pairs") or []
    if 0 <= index < len(pairs) and isinstance(pairs[index], dict):
        pair = pairs[index]
    text = ""
    if 0 <= index < len(paragraphs):
        text = str(paragraphs[index] or "")
    if not text and pair is not None:
        text = str(pair.get("source") or "")

    document = ((state or {}).get("extraction_report") or {}).get("document_ir")
    blocks = document.get("blocks") if isinstance(document, dict) else None
    spans = (pair or {}).get("source_spans") or []
    if not spans and isinstance(blocks, list):
        # Pre-pair stage: `segment_source_spans` is the positional mapping the
        # segmentation step itself persisted, so it stays correct where the
        # raw block order does not (one block can yield several segments).
        persisted = (state or {}).get("segment_source_spans") or []
        if 0 <= index < len(persisted) and isinstance(persisted[index], list):
            spans = persisted[index]

    if by_id is None:
        by_id = _grounded_block_index(state)
    if body_bbox is None:
        body_bbox = _body_text_bbox(state, by_id)
    block = None
    for span in spans:
        if not isinstance(span, dict):
            continue
        block = by_id.get(str(span.get("block_id") or ""))
        if block is not None:
            break
    return {
        "text": text,
        "block_type": str((block or {}).get("type") or ""),
        "container_text": str((block or {}).get("text") or "") or text,
        "block_bbox": _block_bbox(block),
        "body_bbox": body_bbox,
        "preceding_text": (str(paragraphs[index - 1] or "")
                           if index > 0 and index - 1 < len(paragraphs) else ""),
    }


def _source_quality_dispositions(state, report, paragraphs=None):
    """Classify every current source-quality flag into a deterministic outcome."""
    outcomes = {}
    by_id = _grounded_block_index(state)
    body_bbox = _body_text_bbox(state, by_id)
    for flag in (report or {}).get("flags") or []:
        if not isinstance(flag, dict):
            continue
        index = flag.get("segment_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        evidence = _segment_source_quality_evidence(
            state, index, paragraphs, by_id=by_id, body_bbox=body_bbox)
        outcomes[index] = _source_quality.classify_quarantined_segment(
            text=flag.get("text") or evidence["text"],
            block_type=evidence["block_type"],
            container_text=evidence["container_text"],
            preceding_text=evidence["preceding_text"],
            block_bbox=evidence["block_bbox"],
            body_bbox=evidence["body_bbox"],
        )
    return outcomes


def _apply_source_quality_admissions(state, report):
    """Withdraw flags that are verified wrap/number continuations, not fragments.

    A segment cut out of a longer, gate-passing container is ordinary source
    text.  Keeping the flag would send it to the human-confirmation dead-end,
    so the admission is recorded on the report for audit and the flag is
    dropped.  The translated unit itself is untouched.
    """
    outcomes = _source_quality_dispositions(state, report)
    admitted = {
        index: outcome for index, outcome in outcomes.items()
        if outcome.get("disposition") == "integrated_admission"
    }
    if not admitted:
        return report
    report["flags"] = [
        flag for flag in report.get("flags") or []
        if flag.get("segment_index") not in admitted
    ]
    report["clusters"] = [
        [index for index in cluster if index not in admitted]
        for cluster in report.get("clusters") or []
        if any(index not in admitted for index in cluster)
    ]
    report["admissions"] = [
        {"segment_index": index, "evidence": list(outcome.get("evidence") or []),
         "block_type": outcome.get("block_type") or ""}
        for index, outcome in sorted(admitted.items())
    ]
    report["admitted_count"] = len(admitted)
    report["flagged_count"] = len(report["flags"])
    report["status"] = "blocked" if report["flags"] else "passed"
    return report


def _source_quality_finding(flag):
    """Build the persisted blocking finding for one quarantined source item."""
    segment_index = int(flag.get("segment_index"))
    reasons = "；".join(str(reason) for reason in flag.get("reasons") or [])
    source_text = str(flag.get("text") or "")
    return {
        "segment_index": segment_index,
        "segment_id": segment_index,
        "type": "source_quality_gate",
        "severity": "blocking",
        "category": "source_quality",
        "summary": "原文片段未通过翻译准入门",
        "source_span": source_text,
        "target_span": source_text,
        "explanation": (
            "该段是短小、未闭合或含异常空格连字符的 OCR 片段，"
            "仅凭当前文本无法证明它是可翻译的正文。"
        ),
        "recommendation": (
            "回看 PDF 原页，确认应合并、删除还是保留；确认前系统只保留源文，"
            "不让模型猜译，也不会把它写入翻译记忆。"
        ),
        "confidence": None,
        "detector": "Source quality gate",
        "diagnostic_version": _source_quality.VERSION,
        "reason": f"源文质量门拦截：{reasons or '疑似 OCR 碎片'}",
        "detected_text": source_text,
        "requires_human_confirmation": True,
    }


@_serialized_job_mutation
def confirm_source_quality_segment(job_id, index, *, actor="user", note=""):
    """Record that a person checked one flagged PDF segment against its source.

    Confirmation is bound to the current stable segment ID and exact source
    text. Any later source edit, split, or merge makes it inapplicable.
    """
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    paragraphs = state.get("paras") or []
    if not isinstance(index, int) or not 0 <= index < len(pairs) \
            or index >= len(paragraphs):
        raise IndexError(f"段落索引超出范围：{index}")
    if not _source_quality_gate_enabled(state):
        raise ValueError("原文确认只适用于 PDF 源文质量门")
    if _c.is_excluded(pairs[index]):
        raise ValueError("已排除的段落不能确认或翻译")
    active_gate = _c._audit_source_quality_for_state(state)
    if index not in {item.get("segment_index") for item in active_gate.get("flags") or []}:
        raise ValueError("该段没有待确认的源文质量问题；请刷新后重试")
    source = str(paragraphs[index] or "")
    if not source or str(pairs[index].get("source") or "") != source:
        raise ValueError("段落原文已变化，请刷新后重新核对")
    if not str(note or "").strip():
        raise ValueError("请说明已核对的原文依据")
    _c.assign_missing_segment_uids(state, job_id=job_id)
    pair = pairs[index]
    uid = _c.segment_uid(pair)
    confirmations = state.setdefault("source_quality_confirmations", [])
    confirmation = {
        "segment_uid": uid,
        "source": source,
        "note": str(note).strip(),
        "actor": str(actor or "user"),
        "confirmed_at": _finalization.now_iso(),
    }
    confirmations[:] = [
        item for item in confirmations
        if not (isinstance(item, dict)
                and str(item.get("segment_uid") or "") == uid)
    ]
    confirmations.append(confirmation)
    previous_target = str(pair.get("target") or "")
    previous_accepted = str(pair.get("accepted_target") or "")
    had_translation_truth = bool(
        previous_target or previous_accepted or pair.get("reviewed")
        or pair.get("from_tm") or pair.get("human_accepted")
    )
    if had_translation_truth:
        pair.setdefault("source_quality_target_history", []).append({
            "source": source,
            "target": previous_target,
            "initial_target": str(pair.get("initial_target") or ""),
            "accepted_target": previous_accepted,
            "target_provenance": str(pair.get("target_provenance") or ""),
            "review_status": str(pair.get("review_status") or ""),
            "captured_at": confirmation["confirmed_at"],
        })
    pair["target"] = ""
    pair["initial_target"] = ""
    pair["reviewed"] = False
    pair["review_status"] = "source_confirmed_translation_pending"
    pair["target_provenance"] = "source_review_required"
    pair["from_tm"] = False
    for key in ("accepted_target", "human_accepted", "accepted_by_human", "tm_match"):
        pair.pop(key, None)
    annotations = state.get("annotations")
    if isinstance(annotations, dict):
        annotations.pop(index, None)
        annotations.pop(str(index), None)
        state["annotations_done"] = False
    from folith import knowledge as _knowledge
    state["knowledge_candidates"] = _knowledge.discard_candidates_for_segments(
        state.get("knowledge_candidates") or [], [index])
    state["translation_continuity"] = list(state["knowledge_candidates"])
    for finding in state.setdefault("findings", []):
        if (isinstance(finding, dict)
                and finding.get("type") == "source_quality_gate"
                and finding.get("segment_index") == index
                and not finding.get("resolved")):
            finding["resolved"] = True
            finding["resolution"] = {
                "action": "source_confirmed",
                "note": confirmation["note"],
                "timestamp": confirmation["confirmed_at"],
                "actor": confirmation["actor"],
            }
    if had_translation_truth:
        _c._mark_translation_truth_changed(
            job_id, state, [index],
            "源文人工核对后撤销旧译文，必须重新生成并审校",
            actor=str(actor or "user"), action="source_quality_confirmed")
    else:
        _c._invalidate_translation_reviews(
            state, [index], "源文已人工核对，译文必须重新生成并审校")
        _c._invalidate_final_delivery_state(state)
    tm = _c.load_tm(tm_project_id(state))
    if _c.tm_discard(tm, source, state_target_lang(state)):
        _c.save_tm(tm, tm_project_id(state))
    state["source_quality_gate"] = _c._audit_source_quality_for_state(state)
    state.setdefault("human_actions", []).append({
        "finding_id": f"source:{uid}",
        "action": "source_quality_confirmed",
        "actor": confirmation["actor"],
        "actor_type": "human",
        "timestamp": confirmation["confirmed_at"],
        "note": confirmation["note"],
    })
    _c.save_job_state(job_id, state)
    return state, confirmation
