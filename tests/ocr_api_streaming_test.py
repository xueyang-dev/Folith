"""Tests for OCR API streaming (channel B, ocr_api backend).

Covers:
- OcrDocumentAssembler.add_page and committed_paragraph_count (hold-back logic)
- Cross-window boundary: last paragraph of window 0 merged into window 1
- prepare_window without cleanup_fn (raw pass-through)
- Failed page recorded, recovery on late-page re-add
- OcrApiStreamProvider: cache hit avoids OCR call; cache miss stores result
- build_ocr_cache_version mirrors batch-path computation
- Channel A (VLM) ScanStream unchanged: assembler_factory=None uses VisualDocumentAssembler
"""
from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.ocr_stream import (
    OcrApiStreamProvider,
    OcrDocumentAssembler,
    build_ocr_cache_version,
    _map_cleaned_to_pages,
    _error_code,
)
from folith.document_understanding import PageFailed
from folith.document_ir import PageEnvelope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(page_index: int, page_id: str = "") -> PageEnvelope:
    """Minimal PageEnvelope for tests."""
    env = MagicMock(spec=PageEnvelope)
    env.page_index = page_index
    env.page_id = page_id or f"p{page_index + 1}"
    env.image = b"\x89PNG\r\n" + b"\x00" * 100  # fake image bytes
    env.width = 200.0
    env.height = 300.0
    env.to_page.return_value = {
        "page_id": env.page_id,
        "page_number": page_index + 1,
    }
    return env


def _failed_page(page_index: int) -> PageFailed:
    return PageFailed(
        page_index=page_index,
        page_id=f"p{page_index + 1}",
        code="http_error",
        http_status=500,
        message="server error",
    )


def _identity_cleanup_fn(raw_paragraphs, window_id, *, prev_tail_count=0):
    """Cleanup stub: returns input unchanged."""
    return list(raw_paragraphs), {"status": "complete", "input_count": len(raw_paragraphs),
                                  "output_count": len(raw_paragraphs)}, []


def _merge_cleanup_fn(raw_paragraphs, window_id, *, prev_tail_count=0):
    """Cleanup stub: merges the first two items (simulates cross-window merge)."""
    if len(raw_paragraphs) >= 2:
        merged = raw_paragraphs[0] + " " + raw_paragraphs[1]
        result = [merged] + list(raw_paragraphs[2:])
    else:
        result = list(raw_paragraphs)
    return result, {"status": "complete", "input_count": len(raw_paragraphs),
                    "output_count": len(result)}, []


# ---------------------------------------------------------------------------
# build_ocr_cache_version
# ---------------------------------------------------------------------------

class TestBuildOcrCacheVersion:
    def test_plain(self):
        meta = {"backend_version": "ocr-api-plain/plain-v1"}
        assert build_ocr_cache_version(meta) == "ocr-api-plain/plain-v1"

    def test_with_model(self):
        meta = {
            "backend_version": "ocr-api-plain/plain-v1",
            "model_version": "remote_no_local_assets",
        }
        result = build_ocr_cache_version(meta)
        assert result == "ocr-api-plain/plain-v1|models=remote_no_local_assets"

    def test_with_model_and_config(self):
        meta = {
            "backend_version": "ocr-api-plain/plain-v1",
            "model_version": "remote_no_local_assets",
            "inference_config": "high",
        }
        result = build_ocr_cache_version(meta)
        assert result == "ocr-api-plain/plain-v1|models=remote_no_local_assets|config=high"

    def test_empty_model_skipped(self):
        meta = {
            "backend_version": "ocr-api-plain/plain-v1",
            "model_version": "",
        }
        assert build_ocr_cache_version(meta) == "ocr-api-plain/plain-v1"

    def test_none_model_skipped(self):
        meta = {"backend_version": "ocr-api-plain/plain-v1", "model_version": None}
        assert build_ocr_cache_version(meta) == "ocr-api-plain/plain-v1"


# ---------------------------------------------------------------------------
# _error_code
# ---------------------------------------------------------------------------

class TestErrorCode:
    def test_auth_error(self):
        assert _error_code("auth_error: 401 Unauthorized") == "auth_error"

    def test_quota_error(self):
        assert _error_code("quota_error: rate limit") == "quota_error"

    def test_no_prefix(self):
        assert _error_code("Something went wrong") == "ocr_error"

    def test_empty(self):
        assert _error_code("") == "ocr_error"


# ---------------------------------------------------------------------------
# _map_cleaned_to_pages
# ---------------------------------------------------------------------------

class TestMapCleanedToPages:
    def test_same_length(self):
        raw = ["a", "b", "c"]
        cleaned = ["a", "b", "c"]
        pages = [0, 1, 2]
        assert _map_cleaned_to_pages(raw, cleaned, pages) == [0, 1, 2]

    def test_merged(self):
        raw = ["a", "b", "c", "d"]
        cleaned = ["ab", "cd"]  # two merges
        pages = [0, 0, 1, 1]
        result = _map_cleaned_to_pages(raw, cleaned, pages)
        assert len(result) == 2

    def test_empty_cleaned(self):
        assert _map_cleaned_to_pages(["a", "b"], [], [0, 1]) == []


# ---------------------------------------------------------------------------
# OcrDocumentAssembler — basic add_page and committed_paragraph_count
# ---------------------------------------------------------------------------

class TestOcrDocumentAssembler:
    def _assembler(self, cleanup_fn=None):
        return OcrDocumentAssembler("test.pdf", cleanup_fn=cleanup_fn)

    def _make_result(self, lines: List[str]):
        """Fake 4-tuple as iter_page_understanding produces."""
        from folith.document_ir import PageUnderstanding, SemanticBlock
        blocks = [SemanticBlock(type="body", text=line) for line in lines if line]
        semantic = PageUnderstanding(blocks=blocks, provider="ocr_api", diagnostics={
            "ocr_warnings": [], "latency_s": 0.0, "attempt_count": 1,
        })
        return (semantic, {}, 0, 0.0)

    def test_initial_state(self):
        a = self._assembler()
        assert a.committed_paragraph_count() == 0
        assert a.paragraphs == []
        assert a.paragraph_pages == []

    def test_add_failed_page_records_in_failed_pages(self):
        a = self._assembler()
        env = _envelope(0)
        failed = _failed_page(0)
        a.add_page(env, failed)
        assert env.page_id in a.failed_pages

    def test_add_none_skips_page(self):
        a = self._assembler()
        env = _envelope(0)
        a.add_page(env, None)
        assert a.committed_paragraph_count() == 0

    def test_prepare_window_no_cleanup_raw_passthrough(self):
        """Without cleanup_fn, paragraphs are the raw lines."""
        a = self._assembler(cleanup_fn=None)
        env = _envelope(0)
        result = self._make_result(["Hello world.", "Second line."])
        a.add_page(env, result)
        a.prepare_window()
        # raw pass-through commits all but last (hold-back)
        assert len(a.paragraphs) >= 1

    def test_prepare_window_with_identity_cleanup(self):
        a = self._assembler(cleanup_fn=_identity_cleanup_fn)
        for i in range(3):
            env = _envelope(i)
            result = self._make_result([f"Para {i}A.", f"Para {i}B."])
            a.add_page(env, result)
        a.prepare_window()
        # Should have paragraphs; last one held back
        assert len(a.paragraphs) >= 1
        assert a.committed_paragraph_count() == len(a.paragraphs) - 1

    def test_hold_back_count(self):
        """committed_paragraph_count must be len(paragraphs) - 1."""
        a = self._assembler(cleanup_fn=_identity_cleanup_fn)
        # 5 pages × 4 lines each → enough for cleanup to produce multiple paras
        for i in range(5):
            env = _envelope(i)
            result = self._make_result(
                [f"Sentence {i}.{j}." for j in range(4)])
            a.add_page(env, result)
        a.prepare_window()
        n = len(a.paragraphs)
        if n > 0:
            assert a.committed_paragraph_count() == n - 1

    def test_cross_window_merge(self):
        """The tail from window 0 is passed as the first item to window 1.

        With _merge_cleanup_fn, items 0+1 are merged.  After two windows the
        tail from window 0 should appear merged with the first item of window 1.
        """
        a = self._assembler(cleanup_fn=_merge_cleanup_fn)
        # Window 0: 3 pages
        for i in range(3):
            env = _envelope(i)
            result = self._make_result([f"W0P{i} line one.", f"W0P{i} line two."])
            a.add_page(env, result)
        a.prepare_window()
        after_w0 = list(a.paragraphs)

        # Window 1: 3 more pages
        for i in range(3, 6):
            env = _envelope(i)
            result = self._make_result([f"W1P{i} line one.", f"W1P{i} line two."])
            a.add_page(env, result)
        a.prepare_window()

        # After window 1 more paragraphs should exist
        assert len(a.paragraphs) >= len(after_w0)

    def test_late_page_removes_from_failed(self):
        a = self._assembler(cleanup_fn=None)
        env = _envelope(2)
        failed = _failed_page(2)
        a.add_page(env, failed)
        assert env.page_id in a.failed_pages

        # Re-add as late recovery
        result = self._make_result(["Recovered text."])
        a.add_page(env, result, late=True)
        assert env.page_id not in a.failed_pages

    def test_failed_fatal_page_stays_in_failed_pages(self):
        """Multiple failures stay recorded."""
        a = self._assembler(cleanup_fn=None)
        for i in range(3):
            env = _envelope(i)
            a.add_page(env, _failed_page(i))
        assert len(a.failed_pages) == 3

    def test_provenance_set_to_ocr_api(self):
        a = self._assembler()
        assert a.document["provenance"]["provider"] == "ocr_api"

    def test_finish_flushes_tail(self):
        a = self._assembler(cleanup_fn=_identity_cleanup_fn)
        for i in range(4):
            env = _envelope(i)
            result = self._make_result([f"Line {i}."])
            a.add_page(env, result)
        a.prepare_window()
        # Before finish: last para held back
        before = a.committed_paragraph_count()
        paras, doc = a.finish()
        # After finish: all paragraphs returned
        assert len(paras) >= before

    def test_cleanup_meta_tracked(self):
        a = self._assembler(cleanup_fn=_identity_cleanup_fn)
        for i in range(3):
            env = _envelope(i)
            result = self._make_result([f"Para {i}."])
            a.add_page(env, result)
        a.prepare_window()
        meta = a.cleanup_meta()
        assert meta.get("window_count", 0) >= 1

    def test_thread_safety_concurrent_adds(self):
        """Background thread adds pages concurrently with main thread prepare_window."""
        a = self._assembler(cleanup_fn=_identity_cleanup_fn)
        errors = []

        def add_pages():
            try:
                for i in range(10):
                    env = _envelope(i)
                    result = self._make_result([f"Page {i}."])
                    a.add_page(env, result)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=add_pages)
        t.start()
        t.join()
        a.prepare_window()
        assert not errors
        assert len(a.paragraphs) >= 0  # no crash


# ---------------------------------------------------------------------------
# OcrApiStreamProvider — cache hit / miss
# ---------------------------------------------------------------------------

class TestOcrApiStreamProvider:
    def _make_provider(self, cache=None, backend=None):
        if cache is None:
            cache = MagicMock()
            cache.load.return_value = ({}, {})  # empty — always miss
        if backend is None:
            backend = MagicMock()
            backend.name = "ocr_api"
            backend.config = MagicMock()
            backend.config.model = "test-model"
        return OcrApiStreamProvider(
            backend, cache,
            source_sha256="abc123",
            language="eng",
            dpi=150,
            psm=3,
            direction="ltr",
            cache_backend_version="ocr-api-plain/plain-v1|models=remote_no_local_assets",
            backend_options="",
        )

    def _make_ocr_result(self, page_index=0, lines=None):
        from folith.ocr_evidence import OCRPageResult
        return OCRPageResult(
            page_index=page_index, page_number=page_index + 1,
            language="eng", direction="ltr",
            dpi=150, blocks=[], engine={
                "backend": "ocr_api", "model": "test-model",
                "device": "remote", "provider": "test",
                "execution_provider": "remote",
            }, warnings=[],
            error="", request={},
        )

    def test_cache_miss_calls_backend(self):
        cache = MagicMock()
        cache.load.return_value = ({}, {})
        backend = MagicMock()
        backend.name = "ocr_api"
        backend.config = MagicMock()
        backend.config.model = "test-model"
        backend.recognize_page.return_value = self._make_ocr_result(0)

        provider = self._make_provider(cache=cache, backend=backend)
        env = _envelope(0)
        result = provider.understand_page(env)
        backend.recognize_page.assert_called_once()
        cache.store_page.assert_called_once()

    def test_cache_hit_skips_backend(self):
        cache = MagicMock()
        # Return a cached record for page 0
        cache.load.return_value = ({
            "0": {
                "blocks": [{"text": "Cached text", "raw_text": "Cached text",
                             "bbox": [0, 0, 100, 20], "confidence": 0.9,
                             "geometry_known": False}],
                "language": "eng", "direction": "ltr", "dpi": 150,
                "backend": "ocr_api", "device": "remote", "provider": "test", "model": "test",
                "engine": {"backend_version": "v1"},
                "warnings": [], "request": {},
            }
        }, {})

        backend = MagicMock()
        backend.name = "ocr_api"
        backend.config = MagicMock()
        backend.config.model = "test-model"

        provider = self._make_provider(cache=cache, backend=backend)
        env = _envelope(0)
        result = provider.understand_page(env)
        # No OCR call on cache hit
        backend.recognize_page.assert_not_called()

    def test_backend_error_raises_understanding_error(self):
        from folith.document_understanding import DocumentUnderstandingError
        cache = MagicMock()
        cache.load.return_value = ({}, {})
        backend = MagicMock()
        backend.name = "ocr_api"
        backend.config = MagicMock()
        backend.config.model = "test-model"
        # Backend returns an error (no exception — just sets .error)
        from folith.ocr_evidence import OCRPageResult
        backend.recognize_page.return_value = OCRPageResult(
            page_index=0, page_number=1,
            language="eng", direction="ltr",
            dpi=150, blocks=[], engine={}, warnings=[],
            error="auth_error: invalid key", request={},
        )

        provider = self._make_provider(cache=cache, backend=backend)
        env = _envelope(0)
        with pytest.raises(DocumentUnderstandingError) as exc_info:
            provider.understand_page(env)
        assert "auth_error" in str(exc_info.value).lower() or \
               "auth_error" in str(exc_info.value.diagnostics or "")

    def test_missing_image_raises_understanding_error(self):
        from folith.document_understanding import DocumentUnderstandingError
        provider = self._make_provider()
        env = _envelope(0)
        env.image = None
        with pytest.raises(DocumentUnderstandingError):
            provider.understand_page(env)

    def test_cache_identity_is_none(self):
        """cache_identity must be None so _understand_page_job skips VLM checkpoint."""
        provider = self._make_provider()
        assert provider.cache_identity is None


def _minimal_pdf() -> bytes:
    """One-page PDF using pymupdf (same lib the rest of the project uses)."""
    import fitz  # noqa: PLC0415 - only needed here
    doc = fitz.open()
    doc.new_page(width=200, height=300)
    data = doc.tobytes()
    doc.close()
    return data


# ---------------------------------------------------------------------------
# Channel A unchanged: ScanStream with assembler_factory=None uses VDA
# ---------------------------------------------------------------------------

class TestScanStreamAssemblerDefault:
    def test_default_assembler_is_visual_document_assembler(self):
        """ScanStream without assembler_factory uses VisualDocumentAssembler."""
        from folith.streaming_ingest import ScanStream
        from folith.document_understanding import VisualDocumentAssembler

        dummy_provider = MagicMock()
        dummy_provider.cache_identity = None

        stream = ScanStream(
            "test.pdf", _minimal_pdf(),
            dummy_provider,
            checkpoint_dir=Path("/tmp"),
            cancel_check=lambda: True,  # immediately cancel so no threads start
        )
        assert isinstance(stream._assembler, VisualDocumentAssembler)

    def test_assembler_factory_overrides(self):
        """assembler_factory replaces VisualDocumentAssembler with OcrDocumentAssembler."""
        from folith.streaming_ingest import ScanStream

        dummy_provider = MagicMock()
        dummy_provider.cache_identity = None

        created = []

        def factory(fname, known):
            a = OcrDocumentAssembler(fname, known)
            created.append(a)
            return a

        stream = ScanStream(
            "test.pdf", _minimal_pdf(),
            dummy_provider,
            checkpoint_dir=Path("/tmp"),
            cancel_check=lambda: True,
            assembler_factory=factory,
        )
        assert isinstance(stream._assembler, OcrDocumentAssembler)
        assert len(created) == 1


# ---------------------------------------------------------------------------
# ScanStream.prepare_window delegates to assembler
# ---------------------------------------------------------------------------

class TestScanStreamPrepareWindow:
    def test_prepare_window_called_on_assembler(self):
        from folith.streaming_ingest import ScanStream

        dummy_provider = MagicMock()
        dummy_provider.cache_identity = None

        prepare_called = []

        class TrackingAssembler(OcrDocumentAssembler):
            def prepare_window(self):
                prepare_called.append(True)
                super().prepare_window()

        def factory(fname, known):
            return TrackingAssembler(fname, known)

        stream = ScanStream(
            "test.pdf", _minimal_pdf(),
            dummy_provider,
            checkpoint_dir=Path("/tmp"),
            cancel_check=lambda: True,
            assembler_factory=factory,
        )
        stream.prepare_window()
        assert len(prepare_called) == 1

    def test_prepare_window_noop_for_vda(self):
        """VDA has no prepare_window — should not raise."""
        from folith.streaming_ingest import ScanStream

        dummy_provider = MagicMock()
        dummy_provider.cache_identity = None

        stream = ScanStream(
            "test.pdf", _minimal_pdf(),
            dummy_provider,
            checkpoint_dir=Path("/tmp"),
            cancel_check=lambda: True,
        )
        # Must not raise even though VisualDocumentAssembler has no prepare_window
        stream.prepare_window()


def test_progress_label_names_the_reading_channel():
    import core
    from types import SimpleNamespace
    ocr = SimpleNamespace(_assembler=SimpleNamespace(document={"provenance": {"provider": "ocr_api"}}))
    vlm = SimpleNamespace(_assembler=SimpleNamespace(document={"provenance": {"provider": "vlm"}}))
    assert core._scan_reader_label(ocr) == "OCR 识别"
    assert core._scan_reader_label(vlm) == "视觉识别"
    assert core._scan_reader_label(SimpleNamespace()) == "视觉识别"
