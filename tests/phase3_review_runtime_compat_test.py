"""Phase 3 reviewer-role routing and no-review policy regressions."""

import core
from folith import delivery, model_roles


def _job(tmp_path, monkeypatch, job_id, *, review_required=True):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state(f"{job_id}.docx")
    state.update(
        p1_done=True,
        p2_done=True,
        paras=["Source sentence."],
        pairs=[{
            "source": "Source sentence.", "target": "当前译文。",
            "initial_target": "当前译文。", "reviewed": False,
        }],
        findings=[],
        review_evidence=[],
        human_actions=[],
        target_lang="简体中文",
        use_tm=False,
        translation_core_review_required=review_required,
    )
    core.save_job_state(job_id, state)
    return state


def test_resolve_review_runtime_selects_same_or_separate_role():
    translator = {
        "provider": "DeepSeek", "model": "translator-model",
        "api_key": "translator-key", "base_url": "https://translator/v1",
    }
    reviewer = {
        "provider": "OpenAI", "model": "reviewer-model",
        "api_key": "reviewer-key", "base_url": "https://reviewer/v1",
    }

    assert model_roles.resolve_review_runtime("same", translator, reviewer) == translator
    assert model_roles.resolve_review_runtime("separate", translator, reviewer) == reviewer


def test_manual_review_uses_reviewer_runtime_and_base_url(tmp_path, monkeypatch):
    _job(tmp_path, monkeypatch, "review-runtime-0001")
    calls = []

    def fake_call(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        calls.append((provider, api_key, model, kwargs.get("base_url")))
        return "[]"

    monkeypatch.setattr(core, "call_llm", fake_call)
    core.review_translation_segments(
        "review-runtime-0001", [0], "OpenAI", "reviewer-key", "reviewer-model",
        "简体中文", base_url="https://reviewer.example/v1")

    assert calls == [("OpenAI", "reviewer-key", "reviewer-model",
                      "https://reviewer.example/v1")]


def test_retranslate_uses_translator_then_separate_reviewer_runtime(tmp_path, monkeypatch):
    _job(tmp_path, monkeypatch, "review-runtime-0002")
    translation_calls, review_calls = [], []

    def fake_retranslate(job_id, indexes, provider, api_key, model, target_lang,
                         *args, **kwargs):
        translation_calls.append((provider, api_key, model))
        return core.load_job_state(job_id), list(indexes)

    def fake_review(job_id, indexes, provider, api_key, model, target_lang,
                    *, style_rules="", base_url=None):
        review_calls.append((provider, api_key, model, base_url))
        return core.load_job_state(job_id), {
            "reviewed_segment_ids": list(indexes), "failed_segment_ids": [],
        }

    monkeypatch.setattr(delivery, "retranslate_segments", fake_retranslate)
    monkeypatch.setattr(core, "review_translation_segments", fake_review)

    core.retranslate_segments(
        "review-runtime-0002", [0], "DeepSeek", "translator-key", "translator-model",
        "简体中文", reviewer_provider="OpenAI", reviewer_api_key="reviewer-key",
        reviewer_model="reviewer-model", reviewer_base_url="https://reviewer/v1")

    assert translation_calls == [("DeepSeek", "translator-key", "translator-model")]
    assert review_calls == [("OpenAI", "reviewer-key", "reviewer-model",
                             "https://reviewer/v1")]


def test_retranslate_same_mode_defaults_review_to_translator_runtime(tmp_path, monkeypatch):
    _job(tmp_path, monkeypatch, "review-runtime-0003")
    review_calls = []

    monkeypatch.setattr(
        delivery, "retranslate_segments",
        lambda job_id, indexes, *args, **kwargs:
            (core.load_job_state(job_id), list(indexes)),
    )

    def fake_review(job_id, indexes, provider, api_key, model, target_lang,
                    *, style_rules="", base_url=None):
        review_calls.append((provider, api_key, model, base_url))
        return core.load_job_state(job_id), {
            "reviewed_segment_ids": list(indexes), "failed_segment_ids": [],
        }

    monkeypatch.setattr(core, "review_translation_segments", fake_review)
    core.retranslate_segments(
        "review-runtime-0003", [0], "DeepSeek", "translator-key", "translator-model",
        "简体中文")

    assert review_calls == [("DeepSeek", "translator-key", "translator-model", None)]


def test_no_review_retranslation_does_not_call_independent_review(tmp_path, monkeypatch):
    _job(tmp_path, monkeypatch, "review-runtime-0004", review_required=False)
    review_calls = []

    monkeypatch.setattr(
        delivery, "retranslate_segments",
        lambda job_id, indexes, *args, **kwargs:
            (core.load_job_state(job_id), list(indexes)),
    )
    monkeypatch.setattr(
        core, "review_translation_segments",
        lambda *args, **kwargs: review_calls.append((args, kwargs)),
    )

    core.retranslate_segments(
        "review-runtime-0004", [0], "DeepSeek", "translator-key", "translator-model",
        "简体中文", reviewer_provider="OpenAI", reviewer_api_key="reviewer-key",
        reviewer_model="reviewer-model", reviewer_base_url="https://reviewer/v1")

    assert review_calls == []
