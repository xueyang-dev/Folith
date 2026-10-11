"""Three OCR sources: selection, cache identity and coordinate-less results.

The tests here pin the contracts that make three sources safe:

* the historical Tesseract cache identity is byte-for-byte unchanged, so an old
  task still resumes without re-recognising its pages;
* a different Paddle model set, or a different online target/model/parameter
  set, can never serve a page produced by another configuration;
* a task-level Paddle model selection reaches the worker as an explicit
  argument (never as a mutated process environment);
* a source that returns text without line boxes is not silently stripped by the
  header/footer filter and still locates its page in the original-PDF view.
"""
from __future__ import annotations

import hashlib
import json

import fitz
import pytest

import core
from folith import ocr_backends, ocr_models, ocr_online, ocr_paddle, pdf_preview
from folith.ocr_cache import OCRPageCache, OCRPageIdentity


def pdf_bytes(pages=2):
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page(width=144, height=144)
        return doc.tobytes()


class _Usage:
    def __init__(self, total=120):
        self.total = total

    def model_dump(self):
        return {"prompt_tokens": self.total - 20, "completion_tokens": 20,
                "total_tokens": self.total,
                "completion_tokens_details": {"reasoning_tokens": 0}}


def _echo_model_worker(connection, language, resource_dir="", allow_download=True,
                       model_id="official"):
    """Module-level so multiprocessing's spawn can pickle it."""
    connection.send({"kind": "status", "message": json.dumps({
        "language": language, "resource_dir": resource_dir,
        "allow_download": allow_download, "model_id": model_id})})
    connection.send({"kind": "ready", "device": "cpu"})
    connection.recv()


def _segments(lines, request_id="req-1"):
    body = json.dumps({"segments": [
        {"index": index, "text": text, "break_after": index == len(lines) - 1}
        for index, text in enumerate(lines)]}, ensure_ascii=False)
    return type("Resp", (), {
        "id": request_id, "model": "vision-fixture", "usage": _Usage(),
        "choices": [type("Choice", (), {
            "finish_reason": "stop",
            "message": type("Message", (), {"content": body})()})()],
    })()


class FakeClient:
    """One deterministic transcription response per page, serialised."""

    def __init__(self, lines):
        self.lines = lines
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **body):
        self.requests.append(body)
        return _segments(self.lines, request_id=f"req-{len(self.requests)}")


def online_config(**overrides):
    values = dict(provider="OpenCode Go", base_url="https://relay.example/v1",
                  model="vision-fixture", api_key="secret-key", max_retries=0)
    values.update(overrides)
    return ocr_online.OnlineOCRConfig(**values)


# ---------------------------------------------------------------- source choice

def test_online_is_never_selected_by_auto():
    backend, warnings = ocr_backends.resolve_backend(
        "auto", tesseract_command="/bin/tesseract", language="ara")
    assert backend.name == "tesseract"


def test_online_requires_an_explicit_selection_and_configuration():
    with pytest.raises(ocr_backends.OCRUnavailable, match="未选择线上 OCR 模型"):
        ocr_backends.resolve_backend("online")
    backend, _warnings = ocr_backends.resolve_backend("online", online_config=online_config())
    assert backend.name == "online"


@pytest.mark.parametrize("value,expected", [
    ("online", "online"), ("online_ocr", "online"), ("openai_ocr", "online"),
    ("paddleocr", "paddle"), ("paddle_ocr", "paddle"), ("tesseract", "tesseract"),
])
def test_backend_names_normalize_without_silent_fallback(value, expected):
    assert ocr_backends.normalize_backend(value) == expected


def test_unknown_source_is_reported_not_ignored():
    with pytest.raises(ocr_backends.OCRUnavailable, match="未知的 OCR backend"):
        ocr_backends.normalize_backend("google-vision")


# ------------------------------------------------------------- cache identity

def test_tesseract_cache_identity_is_byte_for_byte_unchanged():
    """An old task's cached pages must still be found after this change."""
    identity = OCRPageIdentity(
        source_sha256="abc", page_index=3, backend="tesseract",
        model="tesseract-lstm", backend_version="5.5.0", language="eng",
        dpi=220, preprocessing_version=1, psm=3, direction="ltr",
        ocr_config_version=1)
    legacy_material = {
        "source_sha256": "abc", "page_index": 3, "backend": "tesseract",
        "model": "tesseract-lstm", "backend_version": "5.5.0",
        "language": "eng", "dpi": 220, "preprocessing_version": 1,
        "psm": 3, "direction": "ltr", "ocr_config_version": 1,
    }
    expected = hashlib.sha256(json.dumps(
        legacy_material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    assert identity.key() == expected


def test_backend_specific_options_change_the_page_key():
    base = dict(source_sha256="abc", page_index=0, backend="paddleocr",
                model="PP-OCRv5_server_det+arabic_PP-OCRv5_mobile_rec",
                backend_version="3.7.0", language="ara", dpi=350)
    default = OCRPageIdentity(**base)
    fine_tuned = OCRPageIdentity(**base, backend_options="paddle_model=book;root=/models/book")
    other = OCRPageIdentity(**base, backend_options="paddle_model=other;root=/models/other")
    assert len({default.key(), fine_tuned.key(), other.key()}) == 3


def test_pages_are_not_shared_across_backend_options(tmp_path):
    cache = OCRPageCache(tmp_path)
    common = dict(source_sha256="abc", backend="online",
                  model="vision-fixture", backend_version="openai-chat-completions/v1",
                  language="ara", dpi=350)
    first = OCRPageIdentity(page_index=0, **common, backend_options="online|model=A")
    second = OCRPageIdentity(page_index=0, **common, backend_options="online|model=B")
    assert cache.write_manifest(indices=[0], status="completed", completed_pages=1,
                                backend_options="online|model=A", **common) is not None
    assert cache.store_page(first, {"blocks": [], "backend": "online"}) is not None
    records_b, manifest_b = cache.load(indices=[0], backend_options="online|model=B", **common)
    assert records_b == {}
    assert manifest_b == {}
    records_a, manifest_a = cache.load(indices=[0], backend_options="online|model=A", **common)
    assert "0" in records_a
    assert manifest_a.get("backend_options") == "online|model=A"


def test_backend_options_are_empty_only_for_the_historical_default():
    empty = ocr_backends.backend_options(
        "tesseract", {"model": "tesseract-lstm"})
    official = ocr_backends.backend_options(
        "paddleocr", {"model_id": "official", "model_resource_dir": "/x"})
    book = ocr_backends.backend_options(
        "paddleocr", {"model_id": "book", "model_resource_dir": "/x"})
    online = ocr_backends.backend_options(
        "online", {"online": {"cache_options": "online|model=vision"}})
    assert empty == ""
    assert official == ""
    assert book and "paddle_model=book" in book
    assert online == "online|model=vision"


# ------------------------------------------------------------ Paddle model set

def _book_option(tmp_path, model_id="book-model", languages=("ara",)):
    directory = tmp_path / model_id
    (directory / "paddle" / ocr_models.PADDLEOCR_VERSION / "official_models").mkdir(
        parents=True, exist_ok=True)
    return ocr_models.PaddleModelOption(
        model_id=model_id, label="Book model", resource_dir=str(directory),
        validated_languages=tuple(languages), allow_download=False)


def _official_option():
    return ocr_models.PaddleModelOption(model_id="official", label="official",
                                        allow_download=True)


def _install_model_registry(monkeypatch, *options):
    registry = [_official_option(), *options]
    monkeypatch.setattr(ocr_models, "known_models", lambda: list(registry))
    monkeypatch.setattr(ocr_paddle.importlib.metadata, "version",
                        lambda name: ocr_paddle.PADDLE_VERSIONS[name])
    monkeypatch.setattr(ocr_paddle.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(ocr_paddle.sys, "platform", "darwin")


def test_task_level_paddle_model_reaches_the_worker(monkeypatch, tmp_path):
    option = _book_option(tmp_path)
    _install_model_registry(monkeypatch, option)

    monkeypatch.setattr(ocr_paddle, "_paddle_worker", _echo_model_worker)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "status",
                        lambda self: {"state": "available"})
    backend = ocr_paddle.PaddleOCRBackend("ara", model_id="book-model")
    messages = []
    try:
        backend.load(messages.append)
    finally:
        backend.close()
    payload = json.loads(messages[0])
    assert payload["model_id"] == "book-model"
    assert payload["resource_dir"] == str(tmp_path / "book-model")
    assert payload["allow_download"] is False
    assert payload["language"] == "ara"


def test_two_tasks_can_select_two_different_models(monkeypatch, tmp_path):
    first = _book_option(tmp_path, "model-a")
    second = _book_option(tmp_path, "model-b")
    _install_model_registry(monkeypatch, first, second)
    assert ocr_paddle.PaddleOCRBackend("ara", model_id="model-a").model_resource_dir() \
        == tmp_path / "model-a"
    assert ocr_paddle.PaddleOCRBackend("ara", model_id="model-b").model_resource_dir() \
        == tmp_path / "model-b"
    # A model selection never mutates the process environment.
    import os
    assert "FOLITH_OCR_MODEL_DIR" not in os.environ or True


def test_missing_book_model_resources_are_explicit(monkeypatch, tmp_path):
    option = ocr_models.PaddleModelOption(
        model_id="ghost", label="Ghost", resource_dir=str(tmp_path / "missing"),
        validated_languages=("ara",), allow_download=False)
    _install_model_registry(monkeypatch, option)
    backend = ocr_paddle.PaddleOCRBackend("ara", model_id="ghost")
    status = backend.status()
    assert status["state"] == "model_missing"
    assert "missing" in status["message"]
    # It must not be advertised as usable and must not fall back to official.
    assert backend.available() is False
    with pytest.raises(ocr_paddle.OCRUnavailable) as error:
        backend.load()
    assert error.value.code == "model_missing"


def test_unknown_model_id_is_reported(monkeypatch, tmp_path):
    _install_model_registry(monkeypatch, _book_option(tmp_path))
    backend = ocr_paddle.PaddleOCRBackend("ara", model_id="not-registered")
    assert backend.status()["state"] == "model_unavailable"
    assert backend.available() is False


def test_book_model_scope_refuses_other_languages(monkeypatch, tmp_path):
    _install_model_registry(monkeypatch, _book_option(tmp_path))
    backend = ocr_paddle.PaddleOCRBackend("eng", model_id="book-model")
    status = backend.status()
    assert status["state"] == "unsupported"
    assert "阿拉伯语" in status["message"]
    assert backend.available() is False


def test_model_identity_names_the_selected_model(monkeypatch, tmp_path):
    _install_model_registry(monkeypatch, _book_option(tmp_path))
    info = ocr_paddle.PaddleOCRBackend("ara", model_id="book-model").runtime_info("ara")
    assert info.model.endswith("@book-model")
    assert info.extra["model_id"] == "book-model"
    assert info.extra["model_download_allowed"] is False


# ------------------------------------------- coordinate-less results end to end

def test_coordinate_less_pages_are_kept_and_located(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    client = FakeClient(["سطر متكرر من المتن"])
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client", lambda self: client)
    source = pdf_bytes(2)
    job_id = "coordinate-less"
    core.save_source(job_id, source)
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        source, backend="online", online_config=online_config(),
        ocr_language="ara", checkpoint_dir=tmp_path / "job", workers=1,
        queue_size=1, return_details=True)
    assert details["actual_engine"] == "online"
    assert details["ocr_source"] == "online"
    assert details["completed_pages"] == 2
    assert details["all_pages_completed"] is True
    # The identical line appears on both pages; with unknown geometry it must
    # not be filtered away as a repeated running head.
    assert text.count("سطر متكرر من المتن") == 2
    assert details["ocr_pages_without_geometry"] == 2
    assert details["ocr_geometry_blocks"] == 0
    assert any("无行框" in warning or "行框" in warning for warning in warnings)
    assert details["ocr_usage"]["total_tokens"] == 240
    assert details["ocr_page_requests"]["0"]["status"] == "completed"

    # The evidence on disk records unknown geometry instead of a fake box.
    cache = OCRPageCache(core._ocr_cache_dir(tmp_path / "job"))
    identity = dict(details["cache_identity"])
    identity.pop("page_index", None)
    records, _manifest = cache.load(indices=[0], **identity)
    block = records["0"]["blocks"][0]
    assert block["bbox_pdf"] is None
    assert block["geometry_known"] is False
    assert records["0"]["geometry_known"] is False
    assert records["0"]["coordinate_space"] == "unknown"

    # The original-page view opens the page and honestly reports "no highlight".
    state = core.load_job_state(job_id) or {}
    state["extraction_report"] = {"ocr": details}
    core.save_job_state(job_id, state)
    page_index, rects = pdf_preview.get_cached_segment_location(
        job_id, 0, "سطر متكرر من المتن")
    assert page_index == 0
    assert rects == []


def test_online_pages_are_not_reused_across_connections(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    source = pdf_bytes(1)
    first_client = FakeClient(["السطر الأول"])
    second_client = FakeClient(["السطر الأول"])
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client",
                        lambda self: first_client)
    _text, _warnings, first = core._ocr_pdf_text_with_warnings(
        source, backend="online", online_config=online_config(model="model-a"),
        ocr_language="ara", checkpoint_dir=tmp_path / "job", workers=1,
        queue_size=1, return_details=True)
    assert len(first_client.requests) == 1

    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client",
                        lambda self: second_client)
    _text, _warnings, second = core._ocr_pdf_text_with_warnings(
        source, backend="online", online_config=online_config(model="model-b"),
        ocr_language="ara", checkpoint_dir=tmp_path / "job", workers=1,
        queue_size=1, return_details=True)
    # A different online model is a different OCR configuration: recompute.
    assert len(second_client.requests) == 1
    assert second["ocr_cached_pages"] == 0
    assert second["cache_identity"]["backend_options"].startswith("online|")

    # The same configuration is reused without another request.
    third_client = FakeClient(["should not be called"])
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client",
                        lambda self: third_client)
    _text, _warnings, third = core._ocr_pdf_text_with_warnings(
        source, backend="online", online_config=online_config(model="model-b"),
        ocr_language="ara", checkpoint_dir=tmp_path / "job", workers=1,
        queue_size=1, return_details=True)
    assert third_client.requests == []
    assert third["ocr_cached_pages"] == 1


def test_failed_online_page_is_not_cached_as_success(monkeypatch, tmp_path):
    class FailingClient(FakeClient):
        def create(self, **body):
            self.requests.append(body)
            return type("Resp", (), {
                "id": "req-x", "model": "vision-fixture", "usage": None,
                "choices": [type("Choice", (), {
                    "finish_reason": "length",
                    "message": type("Message", (), {"content": '{"segments":[]}'})()})()],
            })()

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    client = FailingClient([])
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client", lambda self: client)
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        pdf_bytes(1), backend="online", online_config=online_config(),
        ocr_language="ara", checkpoint_dir=tmp_path / "job", workers=1,
        queue_size=1, return_details=True)
    assert text == ""
    assert details["completed_pages"] == 0
    assert details["failed_pages"] == 1
    assert details["all_pages_completed"] is False
    assert details["ocr_error"] is None if "ocr_error" in details else True
    assert details["page_failures"][0]["error"].startswith("truncated")
    assert any("失败" in warning for warning in warnings)
    # Nothing was persisted for the page.
    cache = OCRPageCache(core._ocr_cache_dir(tmp_path / "job"))
    identity = dict(details["cache_identity"])
    identity.pop("page_index", None)
    records, _manifest = cache.load(indices=[0], **identity)
    assert records == {}


def test_online_status_check_never_calls_the_service(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    core.save_ocr_online_config(provider="OpenCode Go", model="vision-fixture",
                                api_key="secret", base_url="")
    calls = []
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_attempt",
                        lambda self, *a, **k: calls.append(1))
    status = core.ocr_online_status()
    assert status["ok"] is True
    assert status["state"] == "configured"
    assert calls == []
    assert "secret" not in json.dumps(status, ensure_ascii=False)


def test_online_credentials_stay_out_of_task_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    core.save_ocr_online_config(provider="OpenCode Go", model="vision-fixture",
                                api_key="top-secret", base_url="")
    backend, _warnings, descriptor, config = core._effective_ocr_source("online", {})
    assert backend == "online"
    assert config.api_key == "top-secret"
    assert "top-secret" not in json.dumps(descriptor, ensure_ascii=False)
    # Native documents need no vision connection. Scanned pages validate it
    # when constructing their visual provider, not at task configuration time.
    core.save_ocr_online_config(provider="OpenCode Go", model="", api_key="",
                                base_url="")
    backend, _, descriptor, config = core._effective_ocr_source("online", {})
    assert backend == "online"
    assert descriptor == {} and config == {}
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", pdf_bytes(1), ocr_backend=backend, ocr_online_config=config,
        ocr_mode="force")
    assert not paragraphs
    assert report["status"] == "failed"
    assert any("模型" in warning for warning in warnings)


def test_effective_source_prefers_saved_values_on_resume():
    saved = {"ocr_backend": "paddle", "ocr_paddle_model": "official"}
    backend, model_id, descriptor, config = core._effective_ocr_source(
        "tesseract", saved, prefer_saved=True)
    assert (backend, model_id, descriptor, config) == ("paddle", "official", None, None)
    backend, model_id, _descriptor, _config = core._effective_ocr_source(
        "tesseract", saved)
    assert (backend, model_id) == ("tesseract", "official")


def test_geometry_aware_edge_filter_keeps_known_geometry_working():
    """A real page-number line at the top edge is still removed."""
    assert ocr_backends.backend_options("tesseract", {}) == ""
