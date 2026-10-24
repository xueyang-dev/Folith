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


def test_retranslation_generation_does_not_review_unadopted_text(tmp_path, monkeypatch):
    """重译只生成候选，不审校：审校证据只能针对用户真正选择的译文。

    这里同时锁定原先由本文件覆盖的一点——独立审校角色只在**显式审校**时生效。
    生成候选时，即使调用方传了 reviewer_*，也不允许触发审校（否则会给尚未采用的
    建议生成证据）。
    """
    _job(tmp_path, monkeypatch, "review-runtime-0002", review_required=True)
    review_calls = []
    monkeypatch.setattr(core, "translate_batch", lambda *a, **k: ["当前译文 v2。"])
    monkeypatch.setattr(core, "check_translation_batch", lambda *a, **k: [])
    monkeypatch.setattr(
        core, "review_translation_segments",
        lambda *args, **kwargs: review_calls.append((args, kwargs)))

    created_state, created = core.retranslate_segments(
        "review-runtime-0002", [0], "DeepSeek", "translator-key", "translator-model",
        "简体中文", reviewer_provider="OpenAI", reviewer_api_key="reviewer-key",
        reviewer_model="reviewer-model", reviewer_base_url="https://reviewer/v1")

    assert created == [0]
    assert review_calls == [], "生成候选不得产生审校调用"
    record = created_state["translation_candidates"][0]
    assert record["status"] == "pending"
    assert created_state["pairs"][0]["target"] == "当前译文。"
    assert created_state["review_evidence"] == []


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
