"""OCR backend boundary contracts.

The rules being pinned:

* Tesseract stays the default, zero-extra-model fallback;
* an unavailable requested backend falls back **with a visible warning** rather
  than silently claiming it ran;
* every backend speaks the same block contract;
* runtime metadata records the device that will really execute OCR — CUDA being
  installed is never treated as evidence that the GPU is in use.
"""
from __future__ import annotations

import sys
import types
import pytest

import core
from folith import ocr_backends


def _fake_paddle(selected_device: str, *, cuda_compiled: bool = False):
    paddle = types.ModuleType("paddle")
    paddle.__version__ = "3.0.0-fake"

    class _Device:
        @staticmethod
        def get_device():
            return selected_device

        @staticmethod
        def is_compiled_with_cuda():
            return cuda_compiled

    paddle.device = _Device()
    return paddle


def test_tesseract_is_the_default_backend():
    backend, warnings = ocr_backends.resolve_backend(None, tesseract_command="/bin/t")
    assert isinstance(backend, ocr_backends.TesseractBackend)
    assert warnings == []
    info = backend.runtime_info("eng")
    assert info.backend == "tesseract"
    assert info.device == "cpu"
    assert info.execution_provider == "native_cpu"
    assert info.gpu_name is None


def test_explicit_unavailable_backend_never_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    with pytest.raises(ocr_backends.OCRUnavailable, match="未安装"):
        ocr_backends.resolve_backend("paddle", tesseract_command="/bin/t")


def test_unknown_backend_name_is_reported_not_ignored():
    with pytest.raises(ocr_backends.OCRUnavailable, match="未知的 OCR backend"):
        ocr_backends.resolve_backend("definitely-not-a-backend", tesseract_command="/bin/t")


def test_paddle_metadata_does_not_import_or_infer_a_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddle", _fake_paddle("gpu:0", cuda_compiled=True))
    info = ocr_backends.PaddleOCRBackend().runtime_info("eng")
    assert info.device == "cpu"
    assert info.execution_provider == "native_cpu"
    assert info.gpu_name is None


def test_paddle_reports_the_device_selected_by_its_session():
    backend = ocr_backends.PaddleOCRBackend()
    backend.device = "gpu:0"
    info = backend.runtime_info("eng")
    assert info.device == "gpu:0"
    assert info.execution_provider == "cuda"


def test_absent_paddle_diagnostics_do_not_initialize_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddle", None)
    backend = ocr_backends.PaddleOCRBackend()
    assert not backend.available()
    assert backend.status()["state"] == "runtime_missing"
    assert backend._engine is None


def test_block_contract_carries_geometry_confidence_page_and_provenance():
    block = ocr_backends.OCRBlock(
        text="hello", left=1.0, top=2.0, right=3.0, bottom=4.0,
        confidence=88.5, page_index=6, page_number=7, backend="tesseract",
        device="cpu", provider="tesseract", model="tesseract-lstm",
        backend_version="5.5.0")
    contract = block.to_contract_dict()
    for key in ("text", "left", "top", "right", "bottom", "confidence",
                "page_index", "page_number", "backend", "device", "provider",
                "model", "backend_version"):
        assert key in contract, key
    lean = block.to_dict()
    assert set(lean) == {"text", "confidence", "left", "top", "right", "bottom"}
    assert ocr_backends.OCRBlock.from_legacy(
        lean, page_index=6, page_number=7, backend="tesseract", device="cpu",
        provider="tesseract", model="tesseract-lstm").text == "hello"


def test_tesseract_backend_returns_contract_blocks(monkeypatch):
    def fake_page_ocr(_command, _png, _language):
        return ([{"text": "line", "confidence": 91.0, "left": 1.0, "top": 2.0,
                  "right": 3.0, "bottom": 4.0}], None)

    backend = ocr_backends.TesseractBackend("/bin/t", "5.5.0", fake_page_ocr)
    blocks, error = backend.ocr_page(b"png", page_index=0, page_number=1,
                                     language="eng", dpi=220)
    assert error is None
    assert len(blocks) == 1
    assert blocks[0].page_index == 0
    assert blocks[0].device == "cpu"
    assert blocks[0].backend_version == "5.5.0"


def test_backend_report_records_the_real_device_and_availability():
    report = core.ocr_backend_report()
    by_name = {entry["backend"]: entry for entry in report}
    assert by_name["tesseract"]["default"] is True
    assert by_name["tesseract"]["runtime"]["device"] == "cpu"
    assert by_name["tesseract"]["runtime"]["execution_provider"] == "native_cpu"
    assert by_name["paddleocr"]["default"] is False
    if not by_name["paddleocr"]["available"]:
        assert by_name["paddleocr"]["runtime"] == {}
        assert by_name["paddleocr"]["unavailable_reason"]


def test_ocr_runtime_metadata_keeps_the_gpu_fallback_warning(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_OCR_DEVICE", "cuda")
    metadata, warnings = core._ocr_runtime_metadata("eng", 2, command="")
    assert metadata["backend"] == "tesseract"
    assert metadata["device"] == "cpu"
    assert metadata["execution_provider"] == "native_cpu"
    assert metadata["gpu_name"] is None
    assert any("实际使用 CPU" in warning for warning in warnings)
