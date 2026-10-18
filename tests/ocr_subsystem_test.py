"""Engine boundary, evidence, lazy lifecycle and durable intake regressions."""
import io
import json
import sys
from pathlib import Path

import fitz
import pytest
from PIL import Image

import core
from folith import ocr_backends, ocr_paddle
from folith.ocr_cache import OCRPageIdentity
from folith.ocr_order import order_lines


def png():
    stream = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    return stream.getvalue()


def pdf(pages=3):
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page(width=144, height=144)
        return doc.tobytes()


def prediction(texts=None):
    return {"rec_texts": texts or ["مرحبا Ali 123،", "السطر الثاني ١٢٣."],
            "rec_scores": [.85, .95],
            "rec_polys": [[[10, 50], [80, 50], [80, 60], [10, 60]],
                          [[10, 80], [80, 80], [80, 90], [10, 90]]]}


@pytest.fixture
def fake_paddle(monkeypatch):
    calls = []
    loads = []
    class Engine:
        def predict(self, image):
            calls.append(image.shape)
            return [prediction()]
    def load(backend, on_status=None, cancel_check=None):
        if backend._engine is None:
            loads.append(backend.language)
            backend._in_process = True
            backend._engine = Engine()
            backend._state = "ready"
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "load", load)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "_model_version", lambda self: "fixture-v1")
    monkeypatch.setattr(core, "_find_tesseract", lambda: "")
    return calls, loads


def test_missing_optional_runtime_is_a_structured_explicit_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    monkeypatch.setattr(core, "_find_tesseract", lambda: "/bin/tesseract")
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        pdf(1), backend="paddle", ocr_language="ara", return_details=True)
    assert text == ""
    assert details["ocr_error"] == "runtime_missing"
    assert details["actual_engine"] is None
    assert warnings and not any("fallback" in w for w in warnings)


def test_availability_never_imports_paddle_or_downloads(monkeypatch):
    original = __import__("builtins").__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"paddle", "paddleocr", "paddlex"}:
            raise AssertionError("heavy runtime import during diagnostics")
        return original(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", guarded)
    monkeypatch.setattr(ocr_paddle.importlib.metadata, "version", lambda name: ocr_paddle.PADDLE_VERSIONS[name])
    backend = ocr_paddle.PaddleOCRBackend("ara")
    assert backend.available()
    assert backend._engine is None
    assert backend.status()["state"] in {"available", "model_missing"}


@pytest.mark.parametrize("language,model", [("ara", "arabic"), ("ara+eng", "arabic"), ("eng", "en"), ("rus", "eslav")])
def test_ppocrv5_language_and_model_configuration(language, model):
    backend = ocr_paddle.PaddleOCRBackend(language)
    assert backend.model_names() == ("PP-OCRv5_server_det", f"{model}_PP-OCRv5_mobile_rec")
    assert backend.capabilities()["word_boxes"] is False


@pytest.mark.parametrize("language", ["heb", "ara+fra"])
def test_unsupported_language_never_uses_another_model(language):
    with pytest.raises((ocr_paddle.OCRUnavailable, ValueError)):
        ocr_paddle.PaddleOCRBackend(language).model_names()


def test_native_paddle_confidence_is_not_tesseract_confidence(fake_paddle):
    backend = ocr_paddle.PaddleOCRBackend("ara")
    page = backend.recognize_page(png(), page_index=2, page_number=3, language="ara", dpi=144)
    assert not page.error
    first = page.blocks[0]
    assert first.confidence is None
    assert first.engine_confidence == .85
    assert first.confidence_kind == "paddle_rec_score_0_1"
    record = page.to_record(50, 50)
    assert record["blocks"][0]["bbox_pdf"] == [5, 25, 40, 30]
    assert record["raw_text"] == record["normalized_text"]
    assert record["direction"] == "rtl"
    json.dumps(record, ensure_ascii=False)


@pytest.mark.parametrize("text", ["اللغة العربية", "العدد 123", "العدد ١٢٣", "مرحبا Ali Smith", "قال: نعم، صحيح؟", "عنوان قصير"])
def test_reading_order_preserves_unicode_and_mixed_spans(text):
    lines = [{"text": "تالي", "left": 10, "right": 80, "top": 30, "bottom": 40},
             {"text": text, "left": 15, "right": 85, "top": 10, "bottom": 20}]
    assert [x["text"] for x in order_lines(lines, "rtl")] == [text, "تالي"]


def test_same_row_fragments_read_right_to_left_without_reversing_text():
    lines = [{"text": "123 Ali", "left": 10, "right": 30, "top": 10, "bottom": 20},
             {"text": "مرحبا", "left": 40, "right": 70, "top": 11, "bottom": 21}]
    assert [x["text"] for x in order_lines(lines, "rtl")] == ["مرحبا", "123 Ali"]


def test_paddle_intake_does_not_require_tesseract_and_loads_once(tmp_path, fake_paddle):
    calls, loads = fake_paddle
    payload = pdf()
    progress = []
    args = dict(backend="paddle", ocr_language="ara", checkpoint_dir=tmp_path,
                dpi=144, workers=8, return_details=True, on_progress=progress.append)
    first = core._ocr_pdf_text_with_warnings(payload, **args)
    assert first[0] and first[2]["completed_pages"] == 3
    assert first[2]["workers"] == 1
    assert len(loads) == 1 and len(calls) == 3
    assert any("PaddleOCR · PP-OCRv5" in message for message in progress)
    assert all("mobile_rec" not in message and "workers=" not in message for message in progress)
    assert all(value is None for value in first[2]["paragraph_confidences"])
    second = core._ocr_pdf_text_with_warnings(payload, **args)
    assert second[0] == first[0]
    assert second[2]["ocr_cached_pages"] == 3
    assert len(calls) == 3 and len(loads) == 1
    record = json.loads(next((tmp_path / "ocr_cache" / "pages").glob("*.json")).read_text())["record"]
    assert record["engine"]["model_version"] == "fixture-v1"
    assert record["blocks"][0]["bbox_pdf"]
    assert record["raw_text"]


def test_auto_records_fallback_when_default_runtime_missing(fake_paddle):
    result = core._ocr_pdf_text_with_warnings(pdf(1), backend="auto", ocr_language="ara", return_details=True)
    assert result[2]["requested_engine"] == "auto"
    assert result[2]["actual_engine"] == "paddleocr"
    assert "tesseract_runtime_unavailable" in result[2]["fallbacks"][0]


def test_bad_paddle_result_is_a_page_failure_and_next_page_survives(fake_paddle):
    backend = ocr_paddle.PaddleOCRBackend("ara")
    backend.load()
    backend._engine.predict = lambda image: [{"rec_texts": ["broken"], "rec_scores": [], "rec_polys": []}]
    blocks, error = backend.ocr_page(png(), page_index=0, page_number=1, language="ara", dpi=144)
    assert blocks == [] and "result_parsing_error" in error
    backend._engine.predict = lambda image: [prediction()]
    blocks, error = backend.ocr_page(png(), page_index=1, page_number=2, language="ara", dpi=144)
    assert len(blocks) == 2 and error is None


def test_cancel_resume_only_recognizes_missing_paddle_pages(tmp_path, fake_paddle):
    calls, loads = fake_paddle
    def cancelled():
        return len(calls) >= 1
    args = dict(backend="paddle", ocr_language="ara", dpi=144,
                checkpoint_dir=tmp_path, workers=1, queue_size=1, return_details=True)
    payload = pdf()
    with pytest.raises(RuntimeError, match="取消"):
        core._ocr_pdf_text_with_warnings(payload, cancel_check=cancelled, **args)
    assert len(calls) == 1
    resumed = core._ocr_pdf_text_with_warnings(payload, **args)
    assert resumed[2]["ocr_cached_pages"] == 1
    assert resumed[2]["page_order"] == [1, 2, 3]
    assert len(calls) == 3


def test_model_and_inference_changes_cannot_reuse_cache(tmp_path, fake_paddle, monkeypatch):
    calls, _ = fake_paddle
    payload = pdf(1)
    args = dict(backend="paddle", ocr_language="ara", dpi=144, checkpoint_dir=tmp_path, return_details=True)
    core._ocr_pdf_text_with_warnings(payload, **args)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "_model_version", lambda self: "fixture-v2")
    updated = core._ocr_pdf_text_with_warnings(payload, **args)
    assert len(calls) == 2 and updated[2]["ocr_cached_pages"] == 0
    monkeypatch.setenv("FOLIOTHREAD_OCR_DEVICE", "auto")
    updated = core._ocr_pdf_text_with_warnings(payload, **args)
    assert len(calls) == 3 and updated[2]["ocr_cached_pages"] == 0


def test_engine_cache_identity_is_distinct():
    common = dict(source_sha256="source", page_index=0, model="model", backend_version="1", language="ara", dpi=350)
    assert OCRPageIdentity(backend="tesseract", **common).key() != OCRPageIdentity(backend="paddleocr", **common).key()


def test_real_tesseract_goldens_preserve_text_boundaries_and_confidence():
    root = Path(__file__).resolve().parents[1]
    expected = json.loads((root / "tests/fixtures/ocr_tesseract_preservation.json").read_text())
    for index, records in expected.items():
        payload = (root / f"tests/fixtures/ocr_tesseract_page_{int(index):04d}.tsv").read_text()
        blocks = core._parse_tesseract_tsv(payload, direction="rtl")
        assert [{k: x.get(k) for k in ["text", "top", "bottom"]} for x in blocks] == [
            {k: x.get(k) for k in ["text", "top", "bottom"]} for x in records]
        # Python 3.12's compensated float sum differs by a few ULPs from the
        # 3.11 golden. Text and geometry remain exact; confidence stays precise.
        for block, record in zip(blocks, records):
            assert block["confidence"] == pytest.approx(
                record["confidence"], rel=0, abs=1e-12)
        assert all(x["left"] < x["right"] and x["lines"][0]["words"] for x in blocks)


def _crashing_worker(connection, language, *worker_args, **worker_kwargs):
    """Real OS exit: verifies native failures cannot terminate the parent."""
    import os
    from dataclasses import asdict
    from folith.ocr_evidence import OCRBlock
    connection.send({"kind": "ready", "device": "cpu"})
    while True:
        _, kwargs = connection.recv()
        if kwargs["page_number"] == 1:
            os._exit(17)
        block = OCRBlock("surviving page", 10, 10, 20, 20, None,
                         kwargs["page_index"], kwargs["page_number"],
                         "paddleocr", "cpu", "paddleocr", "fixture")
        connection.send({"kind": "page", "blocks": [asdict(block)], "error": None})


def test_native_worker_exit_is_isolated_and_next_page_restarts(monkeypatch):
    monkeypatch.setattr(ocr_paddle, "_paddle_worker", _crashing_worker)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "status", lambda self: {"state": "available"})
    backend = ocr_paddle.PaddleOCRBackend("ara")
    try:
        backend.load()
        pid = backend._process.pid
        blocks, error = backend.ocr_page(png(), page_index=0, page_number=1, language="ara", dpi=144)
        assert not blocks and "worker_terminated" in error
        blocks, error = backend.ocr_page(png(), page_index=1, page_number=2, language="ara", dpi=144)
        assert error is None and blocks[0].text == "surviving page"
        assert backend._process.pid != pid
        restarted_pid = backend._process.pid
        backend.load()
        assert backend._process.pid == restarted_pid
    finally:
        backend.close()
    assert backend._process is None


def test_invalid_image_is_classified_before_inference(fake_paddle):
    backend = ocr_paddle.PaddleOCRBackend("ara")
    backend.load()
    blocks, error = backend.ocr_page(b"invalid", page_index=0, page_number=1, language="ara", dpi=144)
    assert blocks == [] and "invalid_image" in error
    assert fake_paddle[0] == []


def test_scanned_preview_reads_exact_engine_cache_and_pdf_boxes(tmp_path, fake_paddle, monkeypatch):
    from folith import pdf_preview
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "scan-preview"
    source = pdf(1)
    core.save_source(job_id, source)
    text, _, details = core._ocr_pdf_text_with_warnings(source, backend="paddle", ocr_language="ara",
        dpi=144, checkpoint_dir=core.job_dir(job_id), return_details=True)
    state = core.new_job_state("scan.pdf")
    state["extraction_report"] = {"ocr": details}
    core.save_job_state(job_id, state)
    location = pdf_preview.get_cached_segment_location(job_id, 0, "مرحبا Ali 123، السطر الثاني ١٢٣.")
    assert location == (0, [[5, 25, 40, 30], [5, 40, 40, 45]])
    assert pdf_preview.find_segment_page_and_rects(job_id, "نص أعيدت صياغته تماما") == (None, [])
    # An incompatible engine identity must never consume a leftover Paddle file.
    state["extraction_report"]["ocr"]["cache_identity"]["backend"] = "tesseract"
    core.save_job_state(job_id, state)
    cache = core.job_dir(job_id) / "ocr_cache" / "manifest.json"
    cache.touch()
    assert pdf_preview.get_cached_segment_location(job_id, 0, "مرحبا Ali 123، السطر الثاني ١٢٣.") == (None, [])


def test_paddle_runs_shared_segmentation_translation_export_and_resume(tmp_path, fake_paddle, monkeypatch):
    from offline_provider import OfflineProvider
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    args = dict(provider="DeepSeek", api_key="offline", model="fixture", target_lang="简体中文",
        auto_term=False, enable_report=False, translation_theory="", user_glossary=[],
        enable_review=False, enable_annotate=False, use_tm=False,
        enable_understanding=False, enable_source_cleanup=False,
        delivery_config={"deliver_report": False}, ocr_language="ara", ocr_dpi=144)
    source = pdf(1)
    job_id = "paddle-pipeline"
    state = core.run_job_pipeline(job_id, "scan.pdf", source, ocr_backend="paddle", **args)
    assert state["p1_done"] and state["p2_done"]
    assert state["pairs"] and state["source_paras"]
    assert state["extraction_report"]["ocr"]["actual_engine"] == "paddleocr"
    assert state["extraction_report"]["ocr"]["model_version"] == "fixture-v1"
    assert state["pipeline_config"]["ocr_backend"] == "paddle"
    assert core.build_delivery_assets(job_id, state)["translation.docx"].startswith(b"PK")
    performance = json.loads((core.job_dir(job_id) / "performance.json").read_text())
    recorded = performance["stages"]["ocr"]
    assert recorded["status"] == "completed" and recorded["finished_at"]
    assert recorded["metadata"]["actual_engine"] == "paddleocr"
    assert recorded["items"][0]["status"] == "completed"
    calls = len(fake_paddle[0])
    # Changed UI arguments must not switch a persisted task on restart.
    resumed = core.run_job_pipeline(job_id, "scan.pdf", source, ocr_backend="tesseract", **args)
    assert resumed["pipeline_config"]["ocr_backend"] == "paddle"
    assert len(fake_paddle[0]) == calls


def test_old_task_without_engine_resumes_as_tesseract(tmp_path, monkeypatch):
    from offline_provider import OfflineProvider
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    state = core.new_job_state("old.pdf")
    state.update(p1_done=True, source_paras=["Old scanned paragraph."], paras=["Old scanned paragraph."],
                 source_cleanup_done=True, pipeline_config={"ocr_language": "eng"})
    core.save_job_state("old-engine", state)
    result = core.run_job_pipeline("old-engine", "old.pdf", None,
        provider="DeepSeek", api_key="offline", model="fixture", target_lang="简体中文",
        auto_term=False, enable_report=False, translation_theory="", user_glossary=[],
        enable_review=False, enable_annotate=False, use_tm=False, enable_understanding=False,
        enable_source_cleanup=False, delivery_config={"deliver_report": False}, ocr_backend="paddle")
    assert result["pipeline_config"]["ocr_backend"] == "tesseract"
    assert result["paras"] == ["Old scanned paragraph."]


def test_cleanup_checkpoint_separates_engine_prompts_and_preserves_raw_input(tmp_path):
    from folith.source_cleanup import cleanup_source_paragraphs
    calls = []
    def llm(provider, key, model, system, user, **kwargs):
        calls.append(system)
        return json.dumps({"segments": [{"index": 0, "text": "A sentence.", "break_after": True}]})
    original = ["A � sentence."]
    args = dict(call_llm=llm, checkpoint_dir=tmp_path, parallelism=1, max_retries=0)
    cleanup_source_paragraphs(original, "offline", "k", "m", ocr_backend="tesseract", **args)
    cleanup_source_paragraphs(original, "offline", "k", "m", ocr_backend="paddleocr", **args)
    assert len(calls) == 2
    cleanup_source_paragraphs(original, "offline", "k", "m", ocr_backend="paddleocr", **args)
    assert len(calls) == 2
    assert original == ["A � sentence."]


def _initializing_worker(connection, language, *worker_args, **worker_kwargs):
    connection.send({"kind": "status", "message": "Initializing fixture"})
    connection.recv()


def test_cancel_during_model_initialization_closes_worker(monkeypatch):
    monkeypatch.setattr(ocr_paddle, "_paddle_worker", _initializing_worker)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "status", lambda self: {"state": "available"})
    backend = ocr_paddle.PaddleOCRBackend("ara")
    statuses = []
    with pytest.raises(RuntimeError, match="任务已请求取消"):
        backend.load(statuses.append, cancel_check=lambda: bool(statuses))
    assert backend._process is None
    assert backend._connection is None


def test_model_graph_changes_invalidate_asset_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("FOLITH_OCR_MODEL_DIR", str(tmp_path))
    backend = ocr_paddle.PaddleOCRBackend("ara")
    for name in backend.model_names():
        root = ocr_paddle.model_root() / "official_models" / name
        root.mkdir(parents=True)
        for file in ["inference.pdiparams", "inference.yml", "inference.json"]:
            (root / file).write_text("fixture")
    initial = backend._model_version()
    (root / "inference.json").write_text("changed model graph")
    assert ocr_paddle.PaddleOCRBackend("ara")._model_version() != initial


def test_diagnostics_never_hash_models(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Diagnostics must not hash assets")
    monkeypatch.setattr(ocr_backends.TesseractBackend, "_model_version", forbidden)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "_model_version", forbidden)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    reports = ocr_backends.backend_report(tesseract_command="fixture")
    assert all(report["runtime"]["model_version"] == "not_inspected" for report in reports)


def test_download_cache_is_configured_before_optional_import(tmp_path, monkeypatch):
    import os
    import builtins
    monkeypatch.setenv("FOLITH_OCR_MODEL_DIR", str(tmp_path))
    for name in ["PADDLE_PDX_CACHE_HOME", "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE"]:
        monkeypatch.setenv(name, "original")
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "available", lambda self: True)
    monkeypatch.setattr(ocr_paddle.PaddleOCRBackend, "status", lambda self: {"state": "available"})
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "paddleocr":
            root = ocr_paddle.model_root()
            assert os.environ["PADDLE_PDX_CACHE_HOME"] == str(root)
            assert os.environ["HF_HOME"] == str(root / "downloads" / "huggingface")
            assert os.environ["HF_XET_CACHE"].startswith(str(root))
            raise ImportError("fixture missing native library")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    backend = ocr_paddle.PaddleOCRBackend("ara", _in_process=True)
    with pytest.raises(ocr_paddle.OCRUnavailable) as error:
        backend.load()
    assert error.value.code == "runtime_unavailable"
