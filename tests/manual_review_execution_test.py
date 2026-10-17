"""Manual review owns worker execution, with durable per-segment results."""
import threading

import pytest

import core


def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("review.docx")
    state.update(paras=["The first complete sentence.", "The second complete sentence."],
                 pairs=[{"source": "The first complete sentence.", "target": "第一句完整的译文。"},
                        {"source": "The second complete sentence.", "target": "第二句完整的译文。"}],
                 p1_done=True, p2_done=True, target_lang="简体中文", use_tm=False)
    core.save_job_state("review", state)


def review(call, indexes=(0,)):
    return core.review_translation_segments("review", indexes, "p", "k", "m", "简体中文",
                                            call_llm_fn=call)


def run_thread(call, indexes=(0,)):
    results, errors = [], []

    def run():
        try:
            results.append(review(call, indexes))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, results, errors


def test_waiting_for_review_does_not_hold_state_lock_or_allow_duplicate_writers(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def call(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return "[]"

    thread, results, errors = run_thread(call)
    try:
        assert entered.wait(5)
        # A state-lock timeout here would reproduce the reported failure.
        with core.job_state_mutation_lock("review", timeout=0):
            pass
        assert core.job_is_active("review")
        with pytest.raises(ValueError, match="正在运行，无法删除"):
            core.delete_job("review")
        with pytest.raises(RuntimeError, match="正在后台处理"):
            review(lambda *a, **kw: pytest.fail("duplicate model request"))
        with pytest.raises(RuntimeError, match="正在后台处理"):
            core.retranslate_segments("review", [0], "p", "k", "m", "简体中文")
        assert not core.start_job_worker("review", "review.docx", None, {})
        with pytest.raises(RuntimeError, match="正在后台处理"):
            core.save_translation_edit("review", 0, "concurrent edit")
        assert core.load_job_state("review")["pairs"][0]["target"] == "第一句完整的译文。"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert results[0][1]["reviewed_segment_ids"] == [0]
    assert not core.job_is_active("review")
    # Ownership is released after completion; ordinary edits work again.
    state = core.save_translation_edit("review", 0, "人工修改后的译文。")
    assert state["pairs"][0]["target"] == "人工修改后的译文。"


def test_bulk_review_checkpoints_before_next_request_and_survives_interruption(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def call(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            entered.set()
            assert release.wait(5)
            raise KeyboardInterrupt("simulated interruption")
        return "[]"

    thread, results, errors = run_thread(call, (0, 1))
    try:
        assert entered.wait(5)
        saved = core.load_job_state("review")
        assert saved["pairs"][0]["reviewed"]
        assert len(saved["review_evidence"]) == 1
        assert saved["review_stats"]["reviewed_segments"] == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], KeyboardInterrupt)
    saved = core.load_job_state("review")
    assert saved["pairs"][0]["reviewed"]
    # The interrupted operation also releases ownership, allowing a retry.
    state, result = review(lambda *a, **kw: "[]", (1,))
    assert result["reviewed_segment_ids"] == [1]
    assert all(pair["reviewed"] for pair in state["pairs"])
