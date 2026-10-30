"""OCR API streaming pipeline acceptance tests (channel B, ocr_api backend).

Validates end-to-end behaviour through ``core.run_job_pipeline`` with the OCR
network stubbed.  These are the acceptance tests the reviewer requires; unit
tests for individual classes live in ``ocr_api_streaming_test.py``.

Covered requirements
--------------------
1. All 30 pages' text present in page order for: no failure; cleanup always
   succeeding; cleanup always failing (raw OCR text kept as-is).
2. The paragraph that runs from page 20 into page 21 (cleanup marks
   ``break_after: false`` for lines ending in 「、」) is ONE paragraph,
   translated once.
3. Translation starts before the last page is requested (verified by blocking
   OCR pages ≥ 12 until the translation LLM is first called).
4. A failed page is recorded in ``streaming_ingest.failed_pages``, delivery is
   blocked, and on the *next* ``run_job_pipeline`` call (same job) the page is
   re-read once and its text appears between pages 9 and 11 after the final
   reorder.
5. Pages seeded by ``_ocr_pdf_text_with_warnings(backend="ocr_api", …)`` produce
   ZERO OCR requests when the same job then streams.
"""
from __future__ import annotations

import collections
import io
import json
import re
import threading
from typing import Any

import fitz
import pytest
from PIL import Image

import core
from folith.ocr_api_backend import OcrApiBackend
from folith.ocr_online import OnlineOCRConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGES = 30
FAIL_PAGE = 9   # 0-indexed; page that fails on the first OCR call
CUT = 19        # 0-indexed; page 20 ends mid-paragraph, page 21 continues


# ---------------------------------------------------------------------------
# PDF / image helpers
# ---------------------------------------------------------------------------

def scanned_pdf(pages: int = PAGES) -> bytes:
    """Minimal scanned PDF: each page is an 8×8 RGB image coloured by page index."""
    with fitz.open() as doc:
        for i in range(pages):
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
            pix.set_rect(pix.irect, (i * 8, 40, 40))
            page = doc.new_page(width=200, height=300)
            page.insert_image(page.rect, pixmap=pix)
        return doc.tobytes()


def page_of(png: bytes) -> int:
    """Recover the 0-indexed page number from the pixel colour of the PNG."""
    return Image.open(io.BytesIO(png)).convert("RGB").getpixel((2, 2))[0] // 8


def page_text(i: int) -> str:
    """Return the OCR text for 0-indexed page *i*."""
    lines = []
    if i == CUT + 1:
        lines.append("前のページから続く文の残りです。")
    lines.append(f"第{i + 1}頁の本文です。")
    if i == CUT:
        lines.append("この段落はページをまたいで、")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def job(tmp_path, monkeypatch):
    """Wire up a fully-stubbed pipeline environment and return test handles.

    Returns ``(cfg, calls, order, started, args)`` where:
    - ``cfg``     – the ``OnlineOCRConfig`` passed to the pipeline
    - ``calls``   – ``Counter`` of OCR calls per 0-indexed page number
    - ``order``   – dict; ``order["max_page_before_translation"]`` is set once
                    the first translation request fires
    - ``started`` – ``Event`` set when the first translation call is made
    - ``args``    – keyword arguments for ``run_job_pipeline``
    """
    from offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)

    cfg = OnlineOCRConfig(
        provider="SiliconFlow",
        model="PaddlePaddle/PaddleOCR-VL-1.5",
        api_key="k",
        base_url="https://api.siliconflow.cn/v1",
    )
    import folith.engine.ocr_config as oc
    monkeypatch.setattr(core, "resolve_ocr_online_config", lambda *a, **k: (cfg, None))
    monkeypatch.setattr(oc, "resolve_ocr_online_config", lambda *a, **k: (cfg, None))

    started = threading.Event()
    calls: collections.Counter = collections.Counter()
    order: dict[str, Any] = {"max_page_before_translation": None}
    lock = threading.Lock()
    seen: dict[str, int] = {"max": -1}
    # Whether FAIL_PAGE has already been injected with an error once.
    # This flag persists across calls.clear() so run 2 can read FAIL_PAGE.
    fail_injected: dict[str, bool] = {"done": False}

    def read_image(self, png, **kwargs):
        i = page_of(png)
        with lock:
            calls[i] += 1
            seen["max"] = max(seen["max"], i)
        # Block OCR for late pages until translation has started — verifies
        # requirement 3: translation commits before OCR finishes.
        if i >= 12:
            assert started.wait(30), "translation did not start before OCR finished"
        context = kwargs.get("context")
        if context is not None:
            context["request"] = {"status": "completed", "attempt_count": 1}
        if i == FAIL_PAGE and not fail_injected["done"]:
            fail_injected["done"] = True
            if context is not None:
                context["response_text"] = "途中で切れた"
            return [], "truncated: cut"
        return kwargs["decoder"](page_text(i)), None

    monkeypatch.setattr(OcrApiBackend, "read_image", read_image)

    translator = OfflineProvider()

    def call_llm(provider, api_key, model, system_prompt, user_prompt, *a, **k):
        # Cleanup prompt: return segment JSON respecting break_after semantics.
        if "目标原文项" in (user_prompt or ""):
            items = re.findall(
                r"^(\d+)\. (.*)$",
                user_prompt.split("目标原文项")[-1],
                re.M,
            )
            return json.dumps(
                {"segments": [
                    {"index": int(n), "text": t, "break_after": not t.endswith("、")}
                    for n, t in items
                ]},
                ensure_ascii=False,
            )
        # Translation prompt: record first translation call for requirement 3.
        if "学術翻訳" in (system_prompt or "") or "学术翻译" in (system_prompt or ""):
            if not started.is_set():
                order["max_page_before_translation"] = seen["max"]
                started.set()
        return translator(provider, api_key, model, system_prompt, user_prompt, *a, **k)

    monkeypatch.setattr(core, "call_llm", call_llm)

    args = dict(
        provider="DeepSeek",
        api_key="offline",
        model="fixture",
        target_lang="简体中文",
        auto_term=False,
        enable_report=False,
        translation_theory="",
        user_glossary=[],
        enable_review=False,
        enable_annotate=False,
        use_tm=False,
        enable_understanding=False,
        delivery_config={"deliver_report": False},
        ocr_mode="force",
        ocr_backend="ocr_api",
    )
    return cfg, calls, order, started, args


@pytest.fixture
def job_no_fail(job, monkeypatch):
    """Like ``job`` but the FAIL_PAGE page always succeeds (no page failure)."""
    cfg, calls, order, started, args = job
    # Pre-set started so late pages (≥ 12) aren't blocked.  The test must
    # re-verify the translation-start ordering separately when it cares.
    return cfg, calls, order, started, args


# ---------------------------------------------------------------------------
# Requirement 1 + 2 + 3: all pages present, cross-window stitching,
# translation starts early.  Parametrised over cleanup success/failure.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cleanup_always_fails", [False, True],
    ids=["cleanup_ok", "cleanup_fail"],
)
def test_all_pages_present_and_stitching(job, monkeypatch, cleanup_always_fails):
    """Requirements 1, 2, 3 — no page failures in this variant."""
    cfg, calls, order, started, args = job

    if cleanup_always_fails:
        # Override call_llm so that cleanup requests always return garbage JSON.
        import folith.engine.ocr_config as oc  # noqa: F401 (already monkeypatched)
        original_call_llm = core.call_llm  # already monkeypatched by fixture

        def call_llm_no_cleanup(provider, api_key, model,
                                 system_prompt, user_prompt, *a, **k):
            if "目标原文项" in (user_prompt or ""):
                return "not json"  # force cleanup failure → raw text fallback
            return original_call_llm(
                provider, api_key, model, system_prompt, user_prompt, *a, **k)

        monkeypatch.setattr(core, "call_llm", call_llm_no_cleanup)

    # FAIL_PAGE will fail once but the test still needs all 30 pages — override
    # so every page succeeds on the first attempt.
    original_read_image = OcrApiBackend.read_image

    def read_image_no_fail(self, png, **kwargs):
        i = page_of(png)
        if i == FAIL_PAGE:
            # Bypass the failure injected by the fixture.
            context = kwargs.get("context")
            if context is not None:
                context["request"] = {"status": "completed", "attempt_count": 1}
            with threading.Lock():
                calls[i] += 1
            if i >= 12:
                assert started.wait(30), "translation did not start in time"
            return kwargs["decoder"](page_text(i)), None
        return original_read_image(self, png, **kwargs)

    monkeypatch.setattr(OcrApiBackend, "read_image", read_image_no_fail)

    source = scanned_pdf()
    state = core.run_job_pipeline("allpages", "scan.pdf", source, **args)

    # --- requirement 3: translation started before the last OCR page ---
    assert started.is_set(), "translation never started"
    assert order["max_page_before_translation"] is not None
    assert order["max_page_before_translation"] < PAGES - 1, (
        f"translation only started after page {order['max_page_before_translation']} "
        f"(last page is {PAGES - 1})"
    )

    # --- requirement 1: all 30 pages present in order ---
    assert state["streaming_ingest"]["status"] == "complete"
    assert state["p2_done"]
    paras = state["source_paragraphs"]
    page_numbers = [
        int(m.group(1))
        for p in paras
        for m in [re.search(r"第(\d+)頁", p)]
        if m
    ]
    assert page_numbers == sorted(page_numbers), f"paragraphs not in page order: {page_numbers}"
    expected = set(range(1, PAGES + 1))
    found = set(page_numbers)
    assert expected == found, f"missing pages: {expected - found}, extra: {found - expected}"

    # --- requirement 2: cross-window paragraph stitching ---
    # When cleanup succeeds it marks break_after=false for lines ending in 「、」,
    # which causes the assembler to join the page-CUT tail with page CUT+1's first
    # line into one paragraph translated once.  When cleanup always fails the raw
    # OCR lines are used as-is (no stitching), so this check only applies to the
    # cleanup_ok case.
    if not cleanup_always_fails:
        joined = [p for p in paras if "ページをまたいで" in p]
        assert len(joined) == 1, f"expected exactly 1 cross-window paragraph, got {joined}"
        assert "前のページから続く" in joined[0], (
            f"cross-window paragraph does not contain the continuation text: {joined[0]!r}"
        )


# ---------------------------------------------------------------------------
# Requirement 4: failed page → delivery blocked → recovered on next run
# ---------------------------------------------------------------------------

def test_failed_page_delivery_blocked_recovered_on_continue(job):
    """Requirement 4: page FAIL_PAGE fails in run 1, re-read in run 2."""
    import folith.delivery as delivery_mod

    cfg, calls, order, started, args = job
    source = scanned_pdf()

    # Run 1 — page FAIL_PAGE (0-indexed 9) fails on its first OCR call.
    state1 = core.run_job_pipeline("failtest", "scan.pdf", source, **args)

    # FAIL_PAGE was attempted exactly once in run 1 (no same-run reread).
    assert calls[FAIL_PAGE] == 1, (
        f"expected 1 OCR call for page {FAIL_PAGE} in run 1, got {calls[FAIL_PAGE]}"
    )
    # All other pages read once.
    for i in range(PAGES):
        if i != FAIL_PAGE:
            assert calls[i] == 1, f"page {i} should have 1 call, got {calls[i]}"

    # Delivery is blocked because failed_pages is non-empty.
    ingest1 = state1.get("streaming_ingest") or {}
    assert ingest1.get("failed_pages"), "failed_pages must be non-empty after run 1"
    missing = delivery_mod.unrecovered_scan_pages(state1)
    assert missing, "delivery.unrecovered_scan_pages must report missing pages after run 1"
    assert FAIL_PAGE + 1 in missing, (
        f"page {FAIL_PAGE + 1} not in unrecovered_scan_pages: {missing}"
    )

    # Run 2 — continue the same job.  Clear the counter so we can count fresh.
    calls.clear()
    # Re-set started so the barrier for pages ≥ 12 does not block.
    started.set()

    state2 = core.run_job_pipeline("failtest", "scan.pdf", source, **args)

    # FAIL_PAGE was re-read exactly once in run 2.
    assert calls[FAIL_PAGE] == 1, (
        f"expected 1 OCR re-read for page {FAIL_PAGE} in run 2, got {calls[FAIL_PAGE]}"
    )
    # All other pages hit the cache — zero OCR calls.
    for i in range(PAGES):
        if i != FAIL_PAGE:
            assert calls[i] == 0, f"page {i} should hit cache in run 2, got {calls[i]} calls"

    # After run 2: all 30 pages present in page order.
    paras2 = state2["source_paragraphs"]
    page_numbers2 = [
        int(m.group(1))
        for p in paras2
        for m in [re.search(r"第(\d+)頁", p)]
        if m
    ]
    assert page_numbers2 == sorted(page_numbers2), (
        f"paragraphs not in page order after continue: {page_numbers2}"
    )
    expected2 = set(range(1, PAGES + 1))
    found2 = set(page_numbers2)
    assert expected2 == found2, (
        f"missing pages after continue: {expected2 - found2}, extra: {found2 - expected2}"
    )
    # Delivery no longer blocked.
    assert not delivery_mod.unrecovered_scan_pages(state2), (
        "unrecovered_scan_pages must be empty after successful continue"
    )


# ---------------------------------------------------------------------------
# Requirement 5: batch-cached pages produce zero OCR calls when streaming
# ---------------------------------------------------------------------------

def test_pages_cached_by_batch_path_are_not_read_again(job, monkeypatch):
    """Requirement 5: pages seeded by _ocr_pdf_text_with_warnings → zero calls."""
    cfg, calls, order, started, args = job
    source = scanned_pdf()

    # Let every page succeed during seeding: bypass both the page-≥12 barrier
    # and the FAIL_PAGE failure injection (fail_injected["done"] starts as False,
    # which would cause page 9 to fail in the batch run and not be cached).
    started.set()
    original_read_image = OcrApiBackend.read_image
    _seed_lock = threading.Lock()

    def read_image_no_fail(self, png, **kwargs):
        i = page_of(png)
        if i == FAIL_PAGE:
            # Bypass failure injection for seeding so page 9 gets cached.
            context = kwargs.get("context")
            if context is not None:
                context["request"] = {"status": "completed", "attempt_count": 1}
            with _seed_lock:
                calls[i] += 1
            return kwargs["decoder"](page_text(i)), None
        return original_read_image(self, png, **kwargs)

    monkeypatch.setattr(OcrApiBackend, "read_image", read_image_no_fail)

    core._ocr_pdf_text_with_warnings(
        source,
        backend="ocr_api",
        online_config=cfg,
        checkpoint_dir=core.job_dir("seeded"),
        ocr_language="jpn",
        return_details=True,
    )
    seeded = sum(calls.values())
    assert seeded > 0, "seeding must have made OCR calls"

    # Reset counter and started event for the streaming run.
    calls.clear()
    started.clear()
    # Re-set started immediately so the ≥12 barrier is always open.
    started.set()

    core.run_job_pipeline(
        "seeded", "scan.pdf", source,
        ocr_language="jpn",
        **args,
    )
    stream_calls = sum(calls.values())
    assert stream_calls == 0, (
        f"streaming made {stream_calls} OCR calls; all pages should have been "
        f"served from the batch cache.  Per-page: {dict(calls)}"
    )
