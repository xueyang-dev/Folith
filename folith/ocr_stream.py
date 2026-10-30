"""OCR API streaming: 边 OCR 边翻译（channel B，ocr_api 后端）。

``ScanStream`` 的后台线程用 :class:`OcrApiStreamProvider` 逐页调用 OCR API
（``OcrApiBackend.recognize_page``），把结果交给 :class:`OcrDocumentAssembler`。
主线程在每个识别窗口满后调用 :meth:`ScanStream.prepare_window`，触发
``OcrDocumentAssembler.prepare_window``：对累积的 OCR 原始行执行
``cleanup_source_paragraphs``，清洗完的段落才算定稿，可以送去翻译。

与 VLM 通道的差异：
- provider 的缓存身份与批量路径（``_ocr_pdf_text_with_warnings``）完全一致，
  已缓存的页不会重新识别；
- 每个窗口的清洗检查点用「窗口序号」隔开，不互相覆盖；
- 末段可能跨窗口边界：每个窗口的最后一段暂不定稿，等看见下一个窗口后再确认。

与 VLM 通道的共同点：
- 失败的页记为 late pages、全书读完后补读，补读的段落追加到末尾，
  译完后整本按原页序重排；
- 交付门禁检查 ``delivery.unrecovered_scan_pages`` 与 VLM 路径相同。
"""
from __future__ import annotations

import copy
import hashlib
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from folith.document_ir import PageEnvelope, PageUnderstanding, SemanticBlock, new_document
from folith.document_understanding import DocumentUnderstandingError, PageFailed
from folith.ocr_cache import OCRPageCache, OCRPageIdentity, OCR_PREPROCESSING_VERSION
from folith.ocr_evidence import OCRPageResult
from folith.ocr_language import OCR_CONFIG_VERSION, DEFAULT_PSM, try_resolve_ocr_language

__all__ = [
    "OcrApiStreamProvider",
    "OcrDocumentAssembler",
    "build_ocr_cache_version",
]

# ---------------------------------------------------------------------------
# Cache version string (must exactly match the batch path computation)
# ---------------------------------------------------------------------------

def build_ocr_cache_version(runtime_metadata: Dict[str, Any]) -> str:
    """Compute cache_backend_version as the batch OCR path does in core.py.

    Mirrors the computation at ``_ocr_pdf_text_with_warnings``::

        cache_backend_version = backend_version
        if model_version:
            cache_backend_version += "|models=" + model_version
        if runtime_metadata.get("inference_config"):
            cache_backend_version += "|config=" + runtime_metadata["inference_config"]
    """
    version = str(runtime_metadata.get("backend_version") or "")
    model_version = str(runtime_metadata.get("model_version") or "")
    if model_version:
        version += "|models=" + model_version
    inference_config = str(runtime_metadata.get("inference_config") or "")
    if inference_config:
        version += "|config=" + inference_config
    return version


# ---------------------------------------------------------------------------
# Page provider for iter_page_understanding
# ---------------------------------------------------------------------------

class OcrApiStreamProvider:
    """OCR API page provider plugged into ``iter_page_understanding``.

    ``cache_identity = None`` disables the VLM checkpoint in
    ``_understand_page_job``; this provider manages its own caching via
    :class:`~folith.ocr_cache.OCRPageCache` so the identity is shared with
    the batch path.

    On a failed page, ``understand_page`` raises
    :class:`~folith.document_understanding.DocumentUnderstandingError` with
    diagnostics so that ``_understand_page_job``'s ``skip_failed=True`` path
    produces a :class:`~folith.document_understanding.PageFailed` value.
    """

    # Disables the VLM per-page checkpoint in _understand_page_job.
    cache_identity = None

    def __init__(
        self,
        backend,
        cache: OCRPageCache,
        *,
        source_sha256: str,
        language: str,
        dpi: int,
        psm: int = DEFAULT_PSM,
        direction: str = "ltr",
        cache_backend_version: str,
        backend_options: str = "",
    ) -> None:
        self._backend = backend
        self._cache = cache
        self._source_sha256 = source_sha256
        self._language = language
        self._dpi = int(dpi)
        self._psm = int(psm)
        self._direction = str(direction)
        self._cache_backend_version = str(cache_backend_version)
        self._backend_options = str(backend_options)
        self._model = backend.config.model or "unconfigured"
        self._backend_name = backend.name

    def _identity(self, page_index: int) -> OCRPageIdentity:
        return OCRPageIdentity(
            source_sha256=self._source_sha256,
            page_index=int(page_index),
            backend=self._backend_name,
            model=self._model,
            backend_version=self._cache_backend_version,
            language=self._language,
            dpi=self._dpi,
            preprocessing_version=OCR_PREPROCESSING_VERSION,
            psm=self._psm,
            direction=self._direction,
            ocr_config_version=OCR_CONFIG_VERSION,
            backend_options=self._backend_options,
        )

    def understand_page(self, envelope: PageEnvelope) -> PageUnderstanding:
        """OCR one page; check cache first, store on success.

        Returns a :class:`~folith.document_ir.PageUnderstanding` whose blocks
        are :class:`~folith.document_ir.SemanticBlock` objects (one per non-empty
        OCR line, ``region_ids=[]``).  The OCR-specific metadata (timings,
        warnings, attempt count) is packed into ``diagnostics``.

        Raises :class:`~folith.document_understanding.DocumentUnderstandingError`
        on failure so that ``_understand_page_job`` with ``skip_failed=True``
        produces a :class:`~folith.document_understanding.PageFailed`.
        """
        identity = self._identity(envelope.page_index)
        # Try cache (load returns ({str(index): record}, manifest))
        records, _ = self._cache.load(
            source_sha256=self._source_sha256,
            indices=[envelope.page_index],
            language=self._language,
            backend=self._backend_name,
            model=self._model,
            backend_version=self._cache_backend_version,
            dpi=self._dpi,
            preprocessing_version=OCR_PREPROCESSING_VERSION,
            psm=self._psm,
            direction=self._direction,
            ocr_config_version=OCR_CONFIG_VERSION,
            backend_options=self._backend_options,
        )
        if str(envelope.page_index) in records:
            record = records[str(envelope.page_index)]
            ocr_result = _record_to_page_result(record, envelope.page_index)
            return _ocr_result_to_page_understanding(ocr_result, cache_hit=True)

        # Not in cache — run OCR.
        if not envelope.image:
            raise DocumentUnderstandingError(
                f"第 {envelope.page_index + 1} 页没有图像数据",
                {"error_code": "missing_image", "http_status": None},
            )

        ocr_result: OCRPageResult = self._backend.recognize_page(
            envelope.image,
            page_index=envelope.page_index,
            page_number=envelope.page_index + 1,
            language=self._language,
            dpi=self._dpi,
        )

        if ocr_result.error:
            code = _error_code(ocr_result.error)
            raise DocumentUnderstandingError(
                ocr_result.error,
                {"error_code": code, "http_status": None,
                 "attempts": int((ocr_result.request or {}).get("attempt_count") or 1)},
            )

        # Store in cache
        record = ocr_result.to_record(
            float(envelope.width),
            float(envelope.height),
        )
        self._cache.store_page(identity, record)
        return _ocr_result_to_page_understanding(ocr_result, cache_hit=False)


def _error_code(error_str: str) -> str:
    """Extract a short code from 「code: message」 error strings."""
    match = re.match(r"^([a-z_]+):", str(error_str or ""))
    return match.group(1) if match else "ocr_error"


def _ocr_result_to_page_understanding(ocr: OCRPageResult, *, cache_hit: bool) -> PageUnderstanding:
    """Wrap an :class:`OCRPageResult` as a :class:`PageUnderstanding`.

    Each non-empty OCR line becomes a ``SemanticBlock(type="body", text=line,
    region_ids=[])``.  ``region_ids=[]`` tells ``align_blocks`` to fall back to
    text-based alignment (no VLM bounding boxes available here).

    OCR-specific metadata (timings, warnings, attempt count) is packed into the
    ``diagnostics`` dict so ``OcrDocumentAssembler.add_page`` can read them back.
    """
    blocks = [
        SemanticBlock(type="body", text=str(b.text or "").strip())
        for b in (ocr.blocks or [])
        if str(b.text or "").strip()
    ]
    diagnostics: Dict[str, Any] = {
        "provider": "ocr_api",
        "model": str((ocr.blocks[0].model if ocr.blocks else "") or ""),
        "cache_hit": cache_hit,
        "latency_s": float((ocr.timings or {}).get("recognition_s") or 0),
        "attempt_count": int((ocr.request or {}).get("attempt_count") or 1),
        "ocr_warnings": list(ocr.warnings or []),
    }
    return PageUnderstanding(blocks=blocks, provider="ocr_api", diagnostics=diagnostics)


def _record_to_page_result(record: Dict[str, Any], page_index: int) -> OCRPageResult:
    """Reconstruct a thin OCRPageResult from a cached record for the assembler."""
    from folith.ocr_evidence import OCRBlock
    blocks = []
    for b in record.get("blocks") or []:
        text = str(b.get("text") or "").strip()
        if not text:
            continue
        blocks.append(OCRBlock(
            text=text,
            raw_text=str(b.get("raw_text") or text),
            left=float((b.get("bbox") or [0, 0, 0, 0])[0] if isinstance(b.get("bbox"), list) else 0),
            top=float((b.get("bbox") or [0, 0, 0, 0])[1] if isinstance(b.get("bbox"), list) else 0),
            right=float((b.get("bbox") or [0, 0, 0, 0])[2] if isinstance(b.get("bbox"), list) else 0),
            bottom=float((b.get("bbox") or [0, 0, 0, 0])[3] if isinstance(b.get("bbox"), list) else 0),
            confidence=b.get("confidence"),
            page_index=page_index,
            page_number=page_index + 1,
            backend=str(record.get("backend") or "ocr_api"),
            device=str(record.get("device") or "remote"),
            provider=str(record.get("provider") or ""),
            model=str(record.get("model") or ""),
            backend_version=str((record.get("engine") or {}).get("backend_version") or ""),
            geometry_known=bool(b.get("geometry_known", False)),
        ))
    return OCRPageResult(
        page_index=page_index,
        page_number=page_index + 1,
        language=str(record.get("language") or ""),
        direction=str(record.get("direction") or "ltr"),
        dpi=int(record.get("dpi") or 150),
        blocks=blocks,
        engine=dict(record.get("engine") or {}),
        warnings=list(record.get("warnings") or []),
        error="",
        request=dict(record.get("request") or {}),
    )


# ---------------------------------------------------------------------------
# Document assembler for OCR streaming
# ---------------------------------------------------------------------------

class OcrDocumentAssembler:
    """Collects raw OCR output per page; runs windowed cleanup on demand.

    Used in place of :class:`~folith.document_understanding.VisualDocumentAssembler`
    when ``ScanStream`` is created for the ``ocr_api`` backend.

    The interface matches ``VisualDocumentAssembler`` so that ``ScanStream`` can
    use it without knowing which path is active.

    Cleanup is run via :meth:`prepare_window` — a hook called by the main thread
    between ``wait_for_pages`` and ``snapshot``.  This keeps the lock-hold time
    in ``snapshot`` short; OCR API calls in the thread pool are never paused.

    Window boundary handling: the last paragraph of each cleanup run is held
    back (not counted as committed) until the next window arrives, because the
    OCR model may have split a paragraph across the page break.  The next
    cleanup call receives the held-back raw lines as its first item and may
    merge them with the next page's first line.
    """

    def __init__(
        self,
        filename: str,
        known_paragraphs: Optional[List[str]] = None,
        *,
        cleanup_fn: Optional[Callable[..., Tuple[List[str], Dict[str, Any], List[str]]]] = None,
    ) -> None:
        """
        ``cleanup_fn(raw_paragraphs, window_id, *, prev_tail_lines, is_last_window)``
        → ``(cleaned_paragraphs, metadata, warnings)``

        Called from :meth:`prepare_window` in the main thread.  ``prev_tail_lines``
        is the raw lines held back from the previous window (may be empty).
        ``is_last_window`` is True when ``finish()`` calls this for the final flush.
        """
        self._filename = filename
        self._known = list(known_paragraphs or [])
        self._cleanup_fn = cleanup_fn

        # Accumulated per-page raw output (added by background thread under lock).
        # Each element: (page_index, page_id, blocks_or_none_or_failed, page_warnings)
        self._raw_pages: List[Tuple[int, str, Any, List[str]]] = []

        # Number of raw_pages that have been processed through cleanup.
        self._cleanup_page_count = 0

        # Paragraphs committed through cleanup (all but the held-back tail).
        self._committed: List[str] = []
        self._committed_pages: List[int] = []  # page index per committed paragraph

        # Raw lines of the last paragraph from the previous window (held back).
        self._tail_lines: List[str] = []
        self._tail_page: Optional[int] = None

        # All paragraphs including the held-back tail (for snapshot reads).
        self._paragraphs: List[str] = []
        self._paragraph_pages: List[int] = []

        # Per-window cleanup metadata and warnings (accumulated, for the report).
        self._cleanup_meta: Dict[str, Any] = {
            "status": "running",
            "input_count": 0,
            "output_count": 0,
            "window_count": 0,
        }
        self._cleanup_warnings: List[str] = []

        # Window counter — used to make cleanup checkpoints distinct per window.
        self._window_index = 0

        # Failed pages: {page_id: PageFailed}
        self.failed_pages: Dict[str, "PageFailed"] = {}

        # Diagnostics list (mirrors VisualDocumentAssembler.diagnostics).
        self.diagnostics: List[Dict[str, Any]] = []

        # Minimal DocumentIR (pages only; blocks added per committed paragraph).
        self.document: Dict[str, Any] = new_document(filename, filename)
        self.document["provenance"] = {"provider": "ocr_api", "page_diagnostics": self.diagnostics}

        # Lock for the fields written by the background thread.
        self._lock = threading.Lock()

    # -- interface for ScanStream (called under ScanStream._cond) ---------

    def add_page(self, envelope: PageEnvelope, result, *, late: bool = False) -> None:
        """Record an OCR result for one page (called by background thread).

        ``result`` is one of:
        - ``None``: page skipped (not scanned / not selected)
        - :class:`~folith.document_understanding.PageFailed`: OCR failed
        - 4-tuple ``(PageUnderstanding, anchors, index, started)`` from
          ``iter_page_understanding`` / ``_understand_page_job``: successful OCR

        The 4-tuple form is what ``ScanStream`` always delivers (it goes through
        ``iter_page_understanding``).  The ``PageUnderstanding`` here has
        ``provider="ocr_api"`` and its ``diagnostics`` carry the OCR-specific
        metadata packed by :func:`_ocr_result_to_page_understanding`.
        """
        page = envelope.to_page()
        with self._lock:
            if not late:
                self.document["pages"].append(page)
            if isinstance(result, PageFailed):
                self._tail_lines = []  # boundary: don't merge across failed pages
                self._tail_page = None
                self.failed_pages[envelope.page_id] = result
                for entry in self.document["pages"]:
                    if entry.get("page_id") == envelope.page_id:
                        entry["recognition"] = "failed"
                        entry["recognition_error"] = {
                            "code": result.code, "http_status": result.http_status,
                            "message": (result.message or "")[:200]}
                self.diagnostics.append({
                    "page_id": envelope.page_id, "provider": "ocr_api",
                    "semantic_result": "failed", "error_code": result.code,
                    "http_status": result.http_status, "error": (result.message or "")[:200],
                })
                # Still register the raw page slot so indexing stays consistent.
                self._raw_pages.append(
                    (envelope.page_index, envelope.page_id, None, []))
                return

            if late:
                self.failed_pages.pop(envelope.page_id, None)
                for entry in self.document["pages"]:
                    if entry.get("page_id") == envelope.page_id:
                        entry.pop("recognition", None)
                        entry.pop("recognition_error", None)

            if result is None:
                # Skipped page (native-text page in a mixed PDF, etc.)
                self._tail_lines = []
                self._tail_page = None
                self._raw_pages.append(
                    (envelope.page_index, envelope.page_id, None, []))
                self.diagnostics.append({
                    "page_id": envelope.page_id, "provider": "ocr_api",
                    "semantic_result": "skipped",
                })
                return

            # Successful OCR result — arrives as a (PageUnderstanding, anchors,
            # index, started) 4-tuple from iter_page_understanding.
            semantic: PageUnderstanding
            if isinstance(result, tuple) and len(result) == 4:
                semantic, _anchors, _index, started_ts = result
            elif isinstance(result, OCRPageResult):
                # Legacy: direct OCRPageResult (unit tests pass these directly).
                semantic = _ocr_result_to_page_understanding(result, cache_hit=False)
                started_ts = 0.0
            else:
                # Defensive: caller passed a PageUnderstanding directly.
                semantic = result
                started_ts = 0.0
            diag = semantic.diagnostics or {}
            lines = [b.text for b in (semantic.blocks or []) if b.text]
            page_warnings = list(diag.get("ocr_warnings") or [])
            self._raw_pages.append(
                (envelope.page_index, envelope.page_id, lines, page_warnings))
            self.diagnostics.append({
                "page_id": envelope.page_id, "provider": "ocr_api",
                "semantic_result": "complete",
                "block_count": len(lines),
                "latency_s": float(diag.get("latency_s") or 0),
                "attempt_count": int(diag.get("attempt_count") or 1),
            })

    def committed_paragraph_count(self) -> int:
        """Paragraphs ready for translation (called by ScanStream under lock).

        Everything in ``self._paragraphs`` except the held-back tail is committed.
        The tail is held back until the next window shows whether it continues.
        """
        n = len(self._paragraphs)
        if n == 0:
            return 0
        # At finish() the tail is flushed; before that, hold back the last para.
        return n - 1

    @property
    def paragraphs(self) -> List[str]:
        return self._paragraphs

    @property
    def paragraph_pages(self) -> List[int]:
        return self._paragraph_pages

    # -- pre-snapshot hook (called by ScanStream.prepare_window, main thread) --

    def prepare_window(self) -> None:
        """Run cleanup on pages accumulated since the last window.

        Called by the main thread (via ``ScanStream.prepare_window()``) after
        ``wait_for_pages`` returns and before ``snapshot()`` acquires the lock.
        Because this runs WITHOUT ``ScanStream._cond``, the background thread
        can add more pages concurrently — we only process up to the count we
        observe at the start.
        """
        if self._cleanup_fn is None:
            # No cleanup configured (unit-test stub, or streaming disabled).
            self._flush_raw_as_paragraphs()
            return

        with self._lock:
            pages_snapshot = list(self._raw_pages)
        pending = pages_snapshot[self._cleanup_page_count:]
        if not pending and not self._tail_lines:
            return

        # Collect raw text lines from the new pages.
        new_lines_by_page: List[Tuple[int, List[str], List[str]]] = []  # (page_idx, lines, warnings)
        page_warnings_acc: List[str] = []
        for page_index, page_id, lines, page_warnings in pending:
            if lines:
                new_lines_by_page.append((page_index, lines, page_warnings))
                for w in page_warnings:
                    if w not in page_warnings_acc:
                        page_warnings_acc.append(w)

        if not new_lines_by_page and not self._tail_lines:
            self._cleanup_page_count += len(pending)
            return

        # Build the flat list of raw paragraphs for cleanup:
        # [ tail_from_previous_window ] + [ page lines joined by '\n\n' ]
        raw_for_cleanup: List[str] = []
        page_of_raw: List[int] = []  # page index for each raw para
        tail_count = 0  # how many raw items belong to the previous tail

        if self._tail_lines:
            # The held-back lines from the previous window. Feed them as the
            # first raw paragraph so cleanup can merge them with the next page.
            raw_for_cleanup.append("\n\n".join(self._tail_lines))
            page_of_raw.append(self._tail_page if self._tail_page is not None
                                else (new_lines_by_page[0][0] if new_lines_by_page else 0))
            tail_count = 1

        for page_idx, lines, _ in new_lines_by_page:
            for line in lines:
                raw_for_cleanup.append(line)
                page_of_raw.append(page_idx)

        if not raw_for_cleanup:
            self._cleanup_page_count += len(pending)
            return

        window_id = self._window_index
        self._window_index += 1

        try:
            cleaned, meta, clean_warns = self._cleanup_fn(
                raw_for_cleanup, window_id,
                prev_tail_count=tail_count)
        except Exception as exc:  # noqa: BLE001 - cleanup failure: use raw text
            cleaned = list(raw_for_cleanup)
            meta = {"status": "failed", "error": str(exc), "input_count": len(raw_for_cleanup),
                    "output_count": len(cleaned)}
            clean_warns = [f"窗口 {window_id} 原文清洗失败，使用原始 OCR 文字：{exc}"]

        # Map each cleaned paragraph back to a page index.
        # Cleanup may merge consecutive raw items, but the page-index of the
        # FIRST merged item is a reasonable representative.
        cleaned_page: List[int] = _map_cleaned_to_pages(
            raw_for_cleanup, cleaned, page_of_raw)

        # Accumulate the per-window cleanup meta.
        self._cleanup_meta["input_count"] = int(self._cleanup_meta.get("input_count") or 0) + len(raw_for_cleanup)
        self._cleanup_meta["output_count"] = int(self._cleanup_meta.get("output_count") or 0) + len(cleaned)
        self._cleanup_meta["window_count"] = int(self._cleanup_meta.get("window_count") or 0) + 1
        for w in clean_warns + page_warnings_acc:
            if w not in self._cleanup_warnings:
                self._cleanup_warnings.append(w)

        # Commit all but the last cleaned paragraph (hold back the tail).
        # When the tail from the previous window was merged, that paragraph was
        # already counted as "committed" in the previous window's output length.
        # Adjustment: if tail_count > 0 and cleanup produced fewer paragraphs
        # than raw items (merges happened), the first result incorporates the
        # tail and replaces what we previously committed.
        if tail_count > 0 and cleaned:
            # The first cleaned paragraph MAY incorporate the previous tail.
            # Replace the last committed paragraph (which was the tail) if it
            # was already in _paragraphs; otherwise just prepend it.
            # Simpler: because the tail was NEVER committed (held back), we can
            # just treat cleaned[0] as a fresh paragraph.
            pass  # The tail was in _paragraphs[-1] but not committed; overwrite below.

        with self._lock:
            if tail_count > 0 and self._paragraphs and not self._committed:
                # Very first window with a tail that wasn't committed: clear the
                # previous partial paragraph and replace with the new cleaned result.
                pass
            elif tail_count > 0 and self._paragraphs:
                # The tail is already the last item in _paragraphs (held back);
                # remove it so we can replace with the cleaned result.
                self._paragraphs.pop()
                self._paragraph_pages.pop()

            new_paragraphs = list(cleaned)
            new_pages = list(cleaned_page)
            if not new_paragraphs:
                # All lines were noise — nothing to commit.
                self._tail_lines = []
                self._tail_page = None
                self._cleanup_page_count += len(pending)
                return

            # Commit all but last; hold back last as the new tail.
            to_commit = new_paragraphs[:-1]
            to_commit_pages = new_pages[:-1]
            new_tail_text = new_paragraphs[-1]
            new_tail_page = new_pages[-1]

            # Build blocks in document for committed paragraphs.
            for para, pg in zip(to_commit, to_commit_pages):
                block_id = f"p{pg + 1}-ocr-{len(self.document['blocks'])}"
                self.document["blocks"].append({
                    "block_id": block_id,
                    "reading_order": len(self.document["blocks"]),
                    "type": "paragraph",
                    "text": para,
                    "source_anchor": {"source_spans": [
                        {"page_id": f"p{pg + 1}", "bbox": None, "block_id": block_id,
                         "geometry_known": False}]},
                    "provenance": {"provider": "ocr_api"},
                })
                self.document["translation_block_ids"].append(block_id)
                self._committed.append(para)
                self._committed_pages.append(pg)

            self._paragraphs = list(self._committed) + [new_tail_text]
            self._paragraph_pages = list(self._committed_pages) + [new_tail_page]
            self._tail_lines = [new_tail_text]
            self._tail_page = new_tail_page
            self._cleanup_page_count += len(pending)

    def _flush_raw_as_paragraphs(self) -> None:
        """Fallback: no cleanup_fn — expose raw OCR lines as paragraphs directly."""
        with self._lock:
            pages_snapshot = list(self._raw_pages)
        pending = pages_snapshot[self._cleanup_page_count:]
        for page_index, page_id, lines, _ in pending:
            if not lines:
                continue
            for line in lines:
                if line.strip():
                    with self._lock:
                        self._paragraphs.append(line)
                        self._paragraph_pages.append(page_index)
        with self._lock:
            self._cleanup_page_count += len(pending)
            self._committed = list(self._paragraphs[:-1]) if self._paragraphs else []
            self._committed_pages = list(self._paragraph_pages[:-1]) if self._paragraph_pages else []

    # -- finish (called by ScanStream._run, then snapshot reads the result) --

    def finish(self) -> Tuple[List[str], Dict[str, Any]]:
        """Flush the held-back tail; return final (paragraphs, document)."""
        # Add a block for the held-back tail if it's not yet in the document.
        with self._lock:
            n_blocks = len(self.document["translation_block_ids"])
            n_paras = len(self._paragraphs)
            if n_paras > n_blocks:
                # The tail paragraph needs a document block.
                for i in range(n_blocks, n_paras):
                    para = self._paragraphs[i]
                    pg = self._paragraph_pages[i] if i < len(self._paragraph_pages) else 0
                    block_id = f"p{pg + 1}-ocr-{len(self.document['blocks'])}"
                    self.document["blocks"].append({
                        "block_id": block_id,
                        "reading_order": len(self.document["blocks"]),
                        "type": "paragraph",
                        "text": para,
                        "source_anchor": {"source_spans": [
                            {"page_id": f"p{pg + 1}", "bbox": None, "block_id": block_id,
                             "geometry_known": False}]},
                        "provenance": {"provider": "ocr_api"},
                    })
                    self.document["translation_block_ids"].append(block_id)
            self.document["provenance"] = {
                "provider": "ocr_api",
                "page_diagnostics": list(self.diagnostics),
            }
            return list(self._paragraphs), copy.deepcopy(self.document)

    # -- cleanup metadata accessors ---------------------------------------

    def cleanup_meta(self) -> Dict[str, Any]:
        """Current aggregate cleanup metadata (for state["source_cleanup"])."""
        with self._lock:
            meta = dict(self._cleanup_meta)
            meta["paragraph_count"] = len(self._paragraphs)
            return meta

    def cleanup_warnings(self) -> List[str]:
        """Accumulated per-window warnings (repetition, blank pages, …)."""
        with self._lock:
            return list(self._cleanup_warnings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_cleaned_to_pages(
    raw: List[str],
    cleaned: List[str],
    raw_page: List[int],
) -> List[int]:
    """Assign a page index to each cleaned paragraph.

    Cleanup can merge consecutive raw items; we attribute each cleaned paragraph
    to the page of the first raw item it originates from.  A simple heuristic:
    find the earliest raw item whose text appears at the start of the cleaned
    paragraph.
    """
    if not cleaned:
        return []
    if len(cleaned) == len(raw):
        return list(raw_page)
    # Fall back: distribute cleaned paragraphs proportionally.
    result: List[int] = []
    ratio = len(raw) / max(len(cleaned), 1)
    for i in range(len(cleaned)):
        raw_idx = min(int(i * ratio), len(raw_page) - 1)
        result.append(raw_page[raw_idx])
    return result
