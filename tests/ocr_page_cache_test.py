"""Page-granular OCR cache and quick-profile reuse.

The contracts pinned here are the ones that decide whether a cached OCR page may
be reused at all:

* a successful page persists only that page's bytes (no quadratic rewrite);
* the cache identity covers document hash, page index, backend/model/version,
  language, DPI and preprocessing version;
* resume preserves page order, has no duplicates and never mixes pages;
* the smart-profile representative pages are reused by the full run.
"""
from __future__ import annotations

import json

import pytest

import core
from folith import ocr_cache


def _pdf_bytes(page_count: int) -> bytes:
    import fitz
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=595, height=842)
    payload = document.tobytes()
    document.close()
    return payload


def _blocks(text: str, confidence: float = 99.0):
    return [{"text": text, "confidence": confidence, "top": 100.0, "bottom": 200.0}]


@pytest.fixture
def fake_ocr(monkeypatch):
    """Deterministic fake engine; records every OCR call."""
    calls = []

    def install(confidence=99.0, text_for=None):
        def engine(_command, png_bytes, _language):
            index = len(calls)
            calls.append({"png": png_bytes, "index": index})
            text = text_for(index) if text_for else f"page body {index}"
            return _blocks(text, confidence), None
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
        monkeypatch.setattr(core, "_tesseract_languages", lambda _cmd: ["eng"])
        monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
        monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)

    install()
    return calls, install


def test_one_file_per_page_and_constant_size_manifest(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(6)
    result = core._ocr_pdf_text_with_warnings(
        pdf, workers=2, queue_size=2, checkpoint_dir=tmp_path, return_details=True)
    assert len(calls) == 6
    details = result[2]
    stats = details["ocr_cache_stats"]
    assert stats["page_writes"] == 6
    # manifest is a run record, written at start and end — never per page
    assert stats["manifest_writes"] == 2

    pages_dir = tmp_path / "ocr_cache" / "pages"
    files = sorted(path.name for path in pages_dir.glob("page-*.json"))
    assert len(files) == 6
    for name in files:
        payload = json.loads((pages_dir / name).read_text(encoding="utf-8"))
        assert payload["identity"]["page_index"] == int(name.split("-")[1])
        assert payload["record"]["page_number"] == payload["identity"]["page_index"] + 1


def test_quick_profile_pages_are_reused_by_the_full_run(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(9)
    first = core._ocr_pdf_text_with_warnings(
        pdf, max_pages=3, workers=2, queue_size=2, checkpoint_dir=tmp_path,
        return_details=True)
    assert len(calls) == 3
    assert first[2]["page_order"] == [1, 5, 9]  # first / middle / last

    calls.clear()
    second = core._ocr_pdf_text_with_warnings(
        pdf, max_pages=None, workers=2, queue_size=2, checkpoint_dir=tmp_path,
        return_details=True)
    # only the six pages the quick profile never touched
    assert len(calls) == 6
    assert second[2]["ocr_cached_pages"] == 3
    assert second[2]["page_order"] == list(range(1, 10))
    assert second[2]["completed_pages"] == 9


def test_cache_identity_rejects_a_different_dpi(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(3)
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, dpi=220)
    assert len(calls) == 3
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, dpi=300)
    # a 300 DPI result is not a 220 DPI result; nothing may be reused
    assert len(calls) == 3


def test_cache_identity_rejects_a_different_backend_version(tmp_path, fake_ocr, monkeypatch):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(3)
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path)
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.3.0")
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path)
    assert len(calls) == 3


def test_cancel_then_resume_keeps_order_without_duplicates(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(5)
    cancel = {"flag": False}

    def cancel_check():
        if len(calls) >= 2:
            cancel["flag"] = True
        return cancel["flag"]

    with pytest.raises(RuntimeError, match="取消"):
        core._ocr_pdf_text_with_warnings(
            pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path,
            cancel_check=cancel_check)
    assert len(calls) >= 1

    cancel["flag"] = False
    calls.clear()
    result = core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, return_details=True)
    order = result[2]["page_order"]
    assert order == [1, 2, 3, 4, 5]
    assert len(set(order)) == 5
    assert result[2]["completed_pages"] == 5


def test_corrupt_or_foreign_page_file_is_ignored(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(3)
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path)
    pages_dir = tmp_path / "ocr_cache" / "pages"
    victim = sorted(pages_dir.glob("page-*.json"))[0]
    # Rewrite the page file with another page's identity key: it must be refused.
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["key"] = "0" * 64
    victim.write_text(json.dumps(payload), encoding="utf-8")
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path)
    assert len(calls) == 1  # only the tampered page is recomputed


def test_legacy_checkpoint_is_adopted_only_for_the_legacy_identity(tmp_path, fake_ocr):
    calls, _ = fake_ocr
    pdf = _pdf_bytes(3)
    legacy = {
        "version": 1,
        "source_sha256": __import__("hashlib").sha256(pdf).hexdigest(),
        "indices": [0, 1, 2],
        "language": "eng",
        "pages": {"0": {"page_number": 1, "page_width": 595, "page_height": 842,
                        "blocks": _blocks("legacy page one")}},
    }
    (tmp_path / "ocr_checkpoint.json").write_text(
        json.dumps(legacy), encoding="utf-8")
    result = core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, dpi=220,
        return_details=True)
    assert len(calls) == 2  # page 0 came from the legacy file
    assert "legacy page one" in result[0]
    assert result[2]["ocr_cached_pages"] == 1

    # A different DPI makes the legacy identity a guess, so nothing is adopted.
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, dpi=300)
    assert len(calls) == 3


def test_cache_module_identity_covers_every_dimension():
    base = dict(source_sha256="a" * 64, page_index=1, backend="tesseract",
                model="tesseract-lstm", backend_version="5.0.0",
                language="eng", dpi=220, preprocessing_version=1)
    keys = {}
    for field in ("source_sha256", "page_index", "backend", "model",
                  "backend_version", "language", "dpi", "preprocessing_version"):
        variant = dict(base)
        variant[field] = "changed" if isinstance(base[field], str) else 999
        keys[field] = ocr_cache.OCRPageIdentity(**variant).key()
    assert len(set(keys.values())) == len(keys)
    assert ocr_cache.OCRPageIdentity(**base).key() not in set(keys.values())


# ---------------------------------------------------------------- profile cache
# The quick profile runs before a job exists, so its representative pages cannot
# live in the job directory.  They go to a shared, content-addressed root instead,
# and the formal run reads that root *in addition to* its own job-scoped cache.

def test_profile_cache_dir_is_content_addressed_and_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    assert core.ocr_profile_cache_dir(None) is None
    assert core.ocr_profile_cache_dir(b"") is None
    first = core.ocr_profile_cache_dir(b"document-a")
    second = core.ocr_profile_cache_dir(b"document-b")
    assert first is not None and second is not None
    assert first != second, "different documents must not share a cache bucket"
    assert first == core.ocr_profile_cache_dir(b"document-a")
    assert first.parent.name == core.OCR_PROFILE_CACHE_DIRNAME


def test_full_run_reuses_pages_written_by_a_different_cache_directory(
        tmp_path, fake_ocr):
    """The job-scoped run must read the shared profile cache, not just its own."""
    calls, _ = fake_ocr
    pdf = _pdf_bytes(9)
    profile_dir = tmp_path / "shared-profile-cache"
    job_dir = tmp_path / "job-cache"

    # 1) profiling writes the representative pages into the shared root
    preview = core._ocr_pdf_text_with_warnings(
        pdf, max_pages=3, workers=2, queue_size=2, checkpoint_dir=profile_dir,
        return_details=True)
    assert len(calls) == 3
    assert preview[2]["page_order"] == [1, 5, 9]

    # 2) the formal run has a *different* checkpoint directory and no local hits
    calls.clear()
    full = core._ocr_pdf_text_with_warnings(
        pdf, max_pages=None, workers=2, queue_size=2, checkpoint_dir=job_dir,
        profile_cache_dir=profile_dir, return_details=True)
    assert len(calls) == 6, "the three representative pages must not be redone"
    assert full[2]["ocr_cached_pages"] == 3
    assert full[2]["page_order"] == list(range(1, 10))
    assert full[2]["completed_pages"] == 9
    assert full[2]["failed_pages"] == 0


def test_profile_cache_is_never_written_by_the_formal_run(tmp_path, fake_ocr):
    """The shared root stays read-only for the formal run (no duplicated pages)."""
    calls, _ = fake_ocr
    pdf = _pdf_bytes(6)
    profile_dir = tmp_path / "shared"
    job_dir = tmp_path / "job"
    core._ocr_pdf_text_with_warnings(
        pdf, max_pages=3, workers=2, queue_size=2, checkpoint_dir=profile_dir,
        return_details=True)
    before = sorted(p.name for p in
                    (profile_dir / "ocr_cache" / "pages").glob("page-*.json"))
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, max_pages=None, workers=2, queue_size=2, checkpoint_dir=job_dir,
        profile_cache_dir=profile_dir, return_details=True)
    after = sorted(p.name for p in
                   (profile_dir / "ocr_cache" / "pages").glob("page-*.json"))
    assert before == after, "the formal run must not extend the shared cache"
    # ...but the job-scoped cache does hold everything it computed
    assert len(list((job_dir / "ocr_cache" / "pages").glob("page-*.json"))) == 3


def test_profile_cache_identity_still_gates_dpi(tmp_path, fake_ocr):
    """A page cached at another DPI in the shared root must not be reused."""
    calls, _ = fake_ocr
    pdf = _pdf_bytes(6)
    profile_dir = tmp_path / "shared"
    job_dir = tmp_path / "job"
    core._ocr_pdf_text_with_warnings(
        pdf, max_pages=3, workers=2, queue_size=2, checkpoint_dir=profile_dir,
        return_details=True)
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, max_pages=None, workers=2, queue_size=2, checkpoint_dir=job_dir,
        profile_cache_dir=profile_dir, dpi=300, return_details=True)
    assert len(calls) == 6, "a different DPI must invalidate the shared pages"
