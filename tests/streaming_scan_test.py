"""边识别边翻译：整本扫描书识别完前 20 页就开始翻译，识别在后台继续。

视觉模型与翻译模型都是离线替身（``tests/offline_provider.py``），不访问网络。
"""
from __future__ import annotations

import threading

import fitz
import pytest

import core
from folith.document_ir import PageUnderstanding, SemanticBlock
from folith.document_understanding import (VisualDocumentAssembler, page_worker_count,
                                           understand_visual_pdf)

PAGES = 45
#: 段落在这些页的页尾被截断、下一页小写续写。19 → 20 正好跨第一个识别窗口。
CUT_PAGES = {19, 33}


def scanned_pdf(pages=PAGES):
    with fitz.open() as doc:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
        for _ in range(pages):
            page = doc.new_page(width=200, height=300)
            page.insert_image(page.rect, pixmap=pix)
        return doc.tobytes()


def page_blocks(index):
    blocks = [SemanticBlock("page_number", str(index + 1))]
    if index == 0:
        blocks.append(SemanticBlock("heading", "Chapter One"))
    if index - 1 in CUT_PAGES:
        blocks.append(SemanticBlock(
            "paragraph", f"with the rest of the sentence from page {index}."))
    blocks.append(SemanticBlock("paragraph", f"Page {index + 1} has its own sentence."))
    if index in CUT_PAGES:
        blocks.append(SemanticBlock(
            "paragraph", f"This paragraph on page {index + 1} is cut off at the end of the page and goes on"))
    return blocks


class PacedVisionProvider:
    """读页替身：第 20 页以后的页要等翻译开始才返回，证明翻译没有等识别读完。"""

    cache_identity = None

    def __init__(self, translation_started):
        self.translation_started = translation_started
        self.max_page_requested = -1
        self.lock = threading.Lock()
        self.pages_read_before_translation = None

    def understand_page(self, envelope):
        with self.lock:
            self.max_page_requested = max(self.max_page_requested, envelope.page_index)
        if envelope.page_index >= 25:
            assert self.translation_started.wait(20), "翻译没有在识别完成前开始"
        return PageUnderstanding(page_blocks(envelope.page_index), "vlm",
                                 {"model": "vision-fixture"})


@pytest.fixture
def streaming_job(tmp_path, monkeypatch):
    from offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    started = threading.Event()
    vision = PacedVisionProvider(started)
    translator = OfflineProvider()

    def call_llm(provider, api_key, model, system_prompt, user_prompt, *args, **kwargs):
        if "学术翻译专家" in (system_prompt or "") and not started.is_set():
            vision.pages_read_before_translation = vision.max_page_requested + 1
            started.set()
        return translator(provider, api_key, model, system_prompt, user_prompt,
                          *args, **kwargs)

    monkeypatch.setattr(core, "call_llm", call_llm)
    monkeypatch.setattr(core, "_vlm_runtime", lambda *a, **k: (vision, None))
    args = dict(provider="DeepSeek", api_key="offline", model="fixture",
                target_lang="简体中文", auto_term=False, enable_report=False,
                translation_theory="", user_glossary=[], enable_review=False,
                enable_annotate=False, use_tm=False, enable_understanding=False,
                delivery_config={"deliver_report": False},
                ocr_mode="force", ocr_backend="online")
    return vision, started, args


def test_translation_starts_before_recognition_finishes(streaming_job):
    vision, started, args = streaming_job
    source = scanned_pdf()
    state = core.run_job_pipeline("stream", "scan.pdf", source, **args)

    assert started.is_set()
    assert vision.pages_read_before_translation < PAGES
    assert state["streaming_ingest"]["status"] == "complete"
    assert state["streaming_ingest"]["pages_done"] == PAGES
    assert state["p2_done"] and len(state["pairs"]) == len(state["paras"])
    assert state["extraction_report"]["status"] != "partial"
    # 跨窗口边界（第 20 → 21 页）的段落接回了一段。
    joined = [p for p in state["source_paragraphs"] if "cut off" in p]
    assert joined == [
        "This paragraph on page 20 is cut off at the end of the page and goes on "
        "with the rest of the sentence from page 20.",
        "This paragraph on page 34 is cut off at the end of the page and goes on "
        "with the rest of the sentence from page 34.",
    ]
    spans = state["segment_source_spans"]
    assert len(spans) == len(state["paras"])
    cut = next(i for i, p in enumerate(state["paras"]) if "page 20 is cut off" in p)
    assert {s["page_id"] for s in spans[cut]} == {"p20", "p21"}


def test_streaming_produces_the_same_units_as_the_whole_book_path(streaming_job, monkeypatch):
    vision, started, args = streaming_job
    source = scanned_pdf()
    streamed = core.run_job_pipeline("stream", "scan.pdf", source, **args)
    monkeypatch.setenv(core.STREAMING_SCAN_ENV, "0")
    started.set()
    whole = core.run_job_pipeline("whole", "scan.pdf", source, **args)
    assert "streaming_ingest" not in whole
    assert streamed["source_paragraphs"] == whole["source_paragraphs"]
    assert streamed["paras"] == whole["paras"]
    assert streamed["segmentation"]["paragraph_ranges"] == whole["segmentation"]["paragraph_ranges"]
    assert streamed["segment_source_spans"] == whole["segment_source_spans"]


def test_interrupted_stream_resumes_and_appends(streaming_job, monkeypatch):
    vision, started, args = streaming_job
    source = scanned_pdf()
    original = core._advance_scan_stream
    calls = {"n": 0}

    def interrupt(*a, **k):
        # 第一次取窗口在阶段一；第二次是第一批翻完后取下一窗口——在这里中断。
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("任务已请求取消")
        return original(*a, **k)

    monkeypatch.setattr(core, "_advance_scan_stream", interrupt)
    with pytest.raises(RuntimeError):
        core.run_job_pipeline("resume", "scan.pdf", source, **args)
    partial = core.load_job_state("resume")
    assert partial["streaming_ingest"]["status"] == "running"
    first_pairs = [dict(pair) for pair in partial["pairs"]]
    assert first_pairs and len(first_pairs) < 60

    state = core.run_job_pipeline("resume", "scan.pdf", None, **args)
    assert state["streaming_ingest"]["status"] == "complete" and state["p2_done"]
    assert len(state["pairs"]) == len(state["paras"])
    assert [p["source"] for p in state["pairs"][:len(first_pairs)]] == \
        [p["source"] for p in first_pairs]


def test_page_workers_default_and_clamp(monkeypatch):
    monkeypatch.delenv("FOLIOTHREAD_VLM_PAGE_WORKERS", raising=False)
    assert page_worker_count() == 5
    monkeypatch.setenv("FOLIOTHREAD_VLM_PAGE_WORKERS", "40")
    assert page_worker_count() == 8
    assert page_worker_count(1) == 1


def test_pages_are_read_concurrently_but_assembled_in_order():
    barrier = threading.Barrier(4, timeout=10)

    class Concurrent:
        cache_identity = None

        def understand_page(self, envelope):
            if envelope.page_index < 4:
                barrier.wait()  # 四页同时在途，否则这里会超时
            return PageUnderstanding(page_blocks(envelope.page_index), "vlm", {})

    paragraphs, document = understand_visual_pdf("scan.pdf", scanned_pdf(8), Concurrent(), workers=4)
    assert paragraphs[0] == "Chapter One"
    assert [p for p in paragraphs if p.startswith("Page ")] == [
        f"Page {i} has its own sentence." for i in range(1, 9)]
    assert [p["page_id"] for p in document["pages"]] == [f"p{i}" for i in range(1, 9)]


def test_short_unpunctuated_lines_are_not_joined_across_pages():
    from folith.document_ir import PageEnvelope
    from folith.spatial_index import SpatialTextIndex, align_blocks

    assembler = VisualDocumentAssembler("x.pdf")
    for index, blocks in enumerate([[SemanticBlock("paragraph", "CANONGATE")],
                                    [SemanticBlock("paragraph", "First published in 2020")]]):
        envelope = PageEnvelope("x.pdf", f"p{index + 1}", index, 100, 100, 0)
        semantic = PageUnderstanding(blocks, "vlm", {})
        spatial = SpatialTextIndex(envelope.page_id)
        assembler.add_page(envelope, (semantic, align_blocks(envelope, blocks, spatial), spatial, 0.0))
    assert assembler.finish()[0] == ["CANONGATE", "First published in 2020"]


def test_each_window_extends_digests_synopsis_and_terms(streaming_job):
    vision, started, args = streaming_job
    args = {**args, "enable_understanding": True, "auto_term": True}
    state = core.run_job_pipeline("knowledge", "scan.pdf", scanned_pdf(), **args)
    assert state["streaming_ingest"]["status"] == "complete" and state["p2_done"]
    units = state["semantic_units"]
    # 语义单元按窗口追加，首尾相接覆盖全部翻译单元——批次划分据此不跨窗口。
    covered = [i for unit in units for i in range(unit["start_segment"], unit["end_segment"] + 1)]
    assert covered == list(range(len(state["paras"])))
    assert len(state["section_digests"]) == len(units)
    assert state["document_synopsis"].get("summary")


def test_new_jobs_record_paragraph_batching_and_legacy_jobs_keep_sentences(streaming_job):
    vision, started, args = streaming_job
    source = scanned_pdf()
    state = core.run_job_pipeline("batching", "scan.pdf", source, **args)
    assert state["pipeline_config"]["batch_strategy"] == "paragraph"
    assert state["batch_plan"]["strategy"] == "paragraph"
    # 老任务：已有译文、配置里没有记录组批方式 → 恢复时沿用每批 4 句。
    saved = core.load_job_state("batching")
    saved["pipeline_config"].pop("batch_strategy")
    saved["p2_done"] = False
    core.save_job_state("batching", saved)
    resumed = core.run_job_pipeline("batching", "scan.pdf", None, **args)
    assert resumed["pipeline_config"]["batch_strategy"] == "sentence"


def _assemble(pages):
    from folith.document_ir import PageEnvelope
    from folith.spatial_index import SpatialTextIndex, align_blocks

    assembler = VisualDocumentAssembler("x.pdf")
    for index, blocks in enumerate(pages):
        envelope = PageEnvelope("x.pdf", f"p{index + 1}", index, 100, 100, 0)
        semantic = PageUnderstanding(blocks, "vlm", {})
        spatial = SpatialTextIndex(envelope.page_id)
        assembler.add_page(envelope, (semantic, align_blocks(envelope, blocks, spatial), spatial, 0.0))
    return assembler.finish()[0]


LONG_OPEN = "She looked out at the dark garden and said —"
LONG_CLOSED = "She looked out at the dark garden and closed the curtains."


def test_layout_flags_beat_punctuation_in_both_directions():
    # 页尾以破折号收尾、但下一页首行缩进（p=0）：是新段，不接。
    assert _assemble([[SemanticBlock("paragraph", LONG_OPEN)],
                      [SemanticBlock("paragraph", "Nobody answered.", continues_previous=False)]]) \
        == [LONG_OPEN, "Nobody answered."]
    # 页尾以句号结束、但下一页首行顶格续写（p=1）：同一段，接上。
    assert _assemble([[SemanticBlock("paragraph", LONG_CLOSED)],
                      [SemanticBlock("paragraph", "Then she sat down.", continues_previous=True)]]) \
        == [LONG_CLOSED + " Then she sat down."]
    # 下一页没给 p 时，看上一页末行的 n。
    assert _assemble([[SemanticBlock("paragraph", LONG_CLOSED, continues_next=True)],
                      [SemanticBlock("paragraph", "Then she sat down.")]]) \
        == [LONG_CLOSED + " Then she sat down."]


def test_text_rules_when_the_model_gives_no_layout_flags():
    # 小写开头：续段，哪怕上一页以句号结束。
    assert len(_assemble([[SemanticBlock("paragraph", LONG_CLOSED)],
                          [SemanticBlock("paragraph", "and then she sat down.")]])) == 1
    # 开引号开头：新的一句对话，通常是新段……
    assert len(_assemble([[SemanticBlock("paragraph", LONG_OPEN)],
                          [SemanticBlock("paragraph", "‘Hello?’")]])) == 2
    # ……除非上一页停在逗号上。
    assert len(_assemble([[SemanticBlock("paragraph", "She turned to the librarian and said,")],
                          [SemanticBlock("paragraph", "‘Hello?’")]])) == 1


def test_layout_flags_are_decoded_and_bad_values_ignored():
    from folith.document_understanding import decode_semantics
    blocks = decode_semantics('{"b":[{"t":"paragraph","s":"a b","p":1,"n":0},'
                              '{"t":"paragraph","s":"c d","p":"yes"}]}')
    assert (blocks[0].continues_previous, blocks[0].continues_next) == (True, False)
    assert (blocks[1].continues_previous, blocks[1].continues_next) == (None, None)


# ---- 暂停 / 逐页进度 / 首窗口 -------------------------------------------------

class SlowVisionProvider:
    """每页慢慢读（单线程），记录每页被请求的次数。"""

    cache_identity = None

    def __init__(self, delay=0.3):
        self.delay = delay
        self.requests = {}
        self.lock = threading.Lock()

    def understand_page(self, envelope):
        with self.lock:
            self.requests[envelope.page_index] = self.requests.get(envelope.page_index, 0) + 1
        threading.Event().wait(self.delay)
        return PageUnderstanding(page_blocks(envelope.page_index), "vlm",
                                 {"model": "slow", "attempt_count": 2 if envelope.page_index == 1 else 1})


def _wait(predicate, timeout=20.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def slow_job(streaming_job, monkeypatch):
    _vision, started, args = streaming_job
    started.set()
    monkeypatch.setenv("FOLIOTHREAD_VLM_PAGE_WORKERS", "1")
    slow = SlowVisionProvider()
    monkeypatch.setattr(core, "_vlm_runtime", lambda *a, **k: (slow, None))
    outcome = {}

    def run(job_id, pages=30):
        def target():
            try:
                outcome["state"] = core.run_job_pipeline(job_id, "scan.pdf", scanned_pdf(pages), **args)
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    return slow, outcome, run


def _runtime(job_id):
    return core.load_runtime_state(job_id)


def test_first_window_is_five_pages():
    from folith.streaming_ingest import DEFAULT_WINDOW_PAGES, FIRST_WINDOW_PAGES, ScanStream
    assert FIRST_WINDOW_PAGES == 5 and DEFAULT_WINDOW_PAGES == 20
    stream = ScanStream("a.pdf", scanned_pdf(3), SlowVisionProvider(0))
    try:
        assert stream.first_window_pages == 5 and stream.window_pages == 20
    finally:
        stream.close()


def test_translation_starts_after_first_window_not_twenty(slow_job, monkeypatch):
    slow, outcome, run = slow_job
    original = core._advance_scan_stream
    commits = []

    def spy(job_id, state, *a, **k):
        result = original(job_id, state, *a, **k)
        commits.append(state["streaming_ingest"]["pages_done"])
        return result

    monkeypatch.setattr(core, "_advance_scan_stream", spy)
    run("first-window", 30).join(60)
    assert "error" not in outcome, outcome.get("error")
    # 单线程慢读：第一次提交恰在 5 页（至多多一两页在途）；下一窗口再加 20 页。
    assert 5 <= commits[0] <= 7
    assert commits[1] == 30 or commits[1] - commits[0] >= 20


def test_progress_moves_per_page_and_pause_does_not_deadlock(slow_job):
    slow, outcome, run = slow_job
    thread = run("pause-job")
    # 每完成一页，进度条与「最后进展」就更新（窗口还没满，没有任何提交）。
    assert _wait(lambda: int((_runtime("pause-job").get("stage_progress") or {}).get("completed") or 0) >= 1)
    first = _runtime("pause-job")
    assert first["stage_progress"]["unit"] == "页" and first["stage_progress"]["total"] == 30
    assert first["last_progress_at"]
    assert _wait(lambda: int(_runtime("pause-job")["stage_progress"]["completed"]) >= 2)

    core.update_runtime_state("pause-job", pause_requested=True, status="pausing")
    assert _wait(lambda: _runtime("pause-job")["status"] == "paused", timeout=5), \
        "暂停应在一页的在途时间内到达，而不是等窗口满"
    paused_at = int(_runtime("pause-job")["stage_progress"]["completed"])
    assert paused_at < 5  # 首窗口没满就暂停了
    assert core.load_job_state("pause-job") is not None
    import time
    time.sleep(1.2)  # 暂停期间不再提交新页（在途的页可以读完）
    assert int(_runtime("pause-job")["stage_progress"]["completed"]) <= paused_at + 2
    assert thread.is_alive()

    core.update_runtime_state("pause-job", pause_requested=False, status="running")
    thread.join(60)
    assert not thread.is_alive() and "error" not in outcome, outcome.get("error")
    state = outcome["state"]
    assert state["streaming_ingest"]["status"] == "complete" and state["p2_done"]
    assert set(slow.requests.values()) == {1}  # 续跑没有重读任何一页
    assert len(slow.requests) == 30
    events = [e["message"] for e in _runtime("pause-job")["events"]]
    assert any("正在暂停：等正在识别的页完成" in m for m in events)
    # 事件日志不逐页刷屏：30 页最多每 5 页一条进度事件。
    assert sum(1 for e in _runtime("pause-job")["events"] if e["event"] == "scan_progress") <= 7


def test_cancel_while_paused_during_streaming(slow_job):
    slow, outcome, run = slow_job
    thread = run("cancel-job")
    assert _wait(lambda: int((_runtime("cancel-job").get("stage_progress") or {}).get("completed") or 0) >= 1)
    core.update_runtime_state("cancel-job", pause_requested=True, status="pausing")
    assert _wait(lambda: _runtime("cancel-job")["status"] == "paused", timeout=5)
    core.update_runtime_state("cancel-job", cancel_requested=True)
    thread.join(30)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), RuntimeError) and "取消" in str(outcome["error"])


def test_cancel_while_waiting_for_window(slow_job):
    slow, outcome, run = slow_job
    thread = run("cancel-wait")
    assert _wait(lambda: int((_runtime("cancel-wait").get("stage_progress") or {}).get("completed") or 0) >= 1)
    core.update_runtime_state("cancel-wait", cancel_requested=True)
    thread.join(30)
    assert not thread.is_alive() and "取消" in str(outcome.get("error"))
