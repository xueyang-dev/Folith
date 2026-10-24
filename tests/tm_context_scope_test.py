"""Translation-memory context and honest-lookup contract (#107 / #108).

#107: a same-source memory hit only proves the *sentence* is identical.  When
the recorded neighbours differ from the current ones the entry is a suggestion,
never an approved reuse: no model call, no automatic approval, an explicit
``tm_context_conflict`` state plus a reviewable finding.

#108: "the TM never ran", "the language is unprovable", "only a legacy
unscoped entry exists" and "there is no entry" are different facts.  The run
record and the workbench must say which one happened, and a disabled TM must
not manufacture one miss per segment.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core
from folith import checkpoint, persistence_profiler
from folith.storage import job_repository as repo
from folith.trace import RunContext

TARGET_LANG = "简体中文"


@pytest.fixture
def temp_job_env(tmp_path: Path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    persistence_profiler.reset()
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        persistence_profiler.disable()
        persistence_profiler.reset()


def _fake_llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
    if "独立的翻译审校专家" in (system_prompt or ""):
        return json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    return json.dumps([f"第{line.split('.', 1)[0].strip()}段译文。" for line in lines],
                      ensure_ascii=False)


def _run(job_id: str, paragraphs, *, use_tm: bool = True,
         target_lang: str = TARGET_LANG, llm_call=None, batch_size: int = 3):
    """Run one translation stage and return (state, prompts, lookup reasons)."""
    state = core.new_job_state("doc.txt")
    state["paras"] = list(paragraphs)
    core.save_job_state(job_id, state)

    prompts = []
    reasons = []
    original_call = core.call_llm
    from folith import trace as trace_module
    original_record = trace_module.trace.record_tm_lookup

    def llm(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        # Only translator requests carry segments to translate; review packets
        # legitimately quote neighbouring source text and must not be counted.
        if "独立的翻译审校专家" not in (system_prompt or ""):
            prompts.append(user_prompt)
        return (llm_call or _fake_llm)(provider, api_key, model, system_prompt,
                                       user_prompt, **kwargs)

    def record(hit_type, duration_ms=0.0, **kwargs):
        reasons.append(hit_type)
        return original_record(hit_type, duration_ms=duration_ms, **kwargs)

    core.call_llm = llm
    trace_module.trace.record_tm_lookup = record
    try:
        core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang=target_lang, style_rules="",
            enable_review=True, use_tm=use_tm, batch_size=batch_size,
            translation_concurrency=1)
    finally:
        core.call_llm = original_call
        trace_module.trace.record_tm_lookup = original_record
    return core.load_job_state(job_id), prompts, reasons


def _numbered_sources(prompts):
    """Sources actually offered for translation (context lines excluded)."""
    sources = []
    for prompt in prompts:
        for line in prompt.splitlines():
            text = line.strip()
            if text[:1].isdigit():
                parts = text.split(".", 1)
                if len(parts) == 2:
                    sources.append(parts[1].strip())
    return sources


def _seed(source: str, target: str, *, previous=None, next_=None):
    tm = core.load_tm(None)
    core.tm_put(tm, source, target, TARGET_LANG,
                **core.tm_context_fields(previous, next_))
    core.save_tm(tm)


# --------------------------------------------------------------------------
# lookup policy (unit level)
# --------------------------------------------------------------------------
def test_tm_lookup_reports_context_conflict_only_when_neighbours_differ():
    tm = {}
    core.tm_put(tm, "Close", "关闭", TARGET_LANG,
                **core.tm_context_fields("Menu section", "Exit the dialog."))
    index = core.tm_index(tm, TARGET_LANG)

    # Same source and same neighbours -> ordinary reuse.
    hit, kind = core.tm_lookup(
        tm, "Close", index, target_lang=TARGET_LANG,
        context=("Menu section", "Exit the dialog."))
    assert hit["target"] == "关闭" and kind == "exact"

    # Same source, different neighbours -> suggestion, not reuse.
    hit, kind = core.tm_lookup(
        tm, "Close", index, target_lang=TARGET_LANG,
        context=("The shop will", "at noon."))
    assert hit["target"] == "关闭" and kind == "context_conflict"

    # A record without fingerprints (older TM) keeps the old reuse behaviour.
    legacy = {}
    core.tm_put(legacy, "Close", "关闭", TARGET_LANG)
    hit, kind = core.tm_lookup(
        legacy, "Close", core.tm_index(legacy, TARGET_LANG),
        target_lang=TARGET_LANG, context=("anything", "else"))
    assert hit["target"] == "关闭" and kind == "exact"


def test_state_reconstruction_keeps_context_fingerprints(temp_job_env: Path):
    state = {"pairs": [
        {"source": "First.", "target": "第一。", "reviewed": True},
        {"source": "Shared.", "target": "共用。", "reviewed": True},
        {"source": "Last.", "target": "最后。", "reviewed": True},
    ]}
    tm = {}
    changed, _pending = checkpoint.reconcile_translation_memory(
        tm, state, temp_job_env, scope=lambda s, t: (
            core.tm_scope_key(TARGET_LANG, s), core.tm_record(t, TARGET_LANG)))
    assert changed
    record = tm[core.tm_scope_key(TARGET_LANG, "Shared.")]
    assert record[core.TM_CONTEXT_PREV] == core.tm_context_fingerprint("First.")
    assert record[core.TM_CONTEXT_NEXT] == core.tm_context_fingerprint("Last.")


# --------------------------------------------------------------------------
# #107 end-to-end: conflict degrades to a reviewable suggestion
# --------------------------------------------------------------------------
def test_same_source_different_context_is_suggested_not_auto_reused(
        temp_job_env: Path):
    _seed("The shared sentence.", "共享句旧译。",
          previous="Old previous paragraph.", next_="Old next paragraph.")
    document = ["First line.", "The shared sentence.", "Last line."]
    state, prompts, reasons = _run("tm-context-conflict", document)

    shared = state["pairs"][1]
    assert shared["target"] == "共享句旧译。", "译文应作为建议预填"
    assert shared["tm_context_conflict"] is True
    assert shared["from_tm"] is False
    # The conflicted hit is pre-filled but *routed to independent review*
    # instead of being adopted; a clean review is what makes it deliverable.
    assert shared["review_status"] == "reviewed_context_conflict"
    assert shared["reviewed"] is True
    assert shared["target_provenance"] == "tm_context_conflict_reviewed"
    assert state.get("tm_conflict_count") == 1
    assert state.get("tm_used_count") in (None, 0), "冲突不得计为直接复用"
    # No translation call was spent on the conflicted segment.
    offered = _numbered_sources(prompts)
    assert "The shared sentence." not in offered, "冲突段落不得送去翻译"
    assert "First line." in offered and "Last line." in offered
    assert reasons.count("context_conflict") == 1
    assert reasons.count("miss") == 2, "其余两段是真实 miss"

    # The conflicted entry must not be overwritten with the prefill: context is
    # still the old one, so the next run conflicts again instead of silently
    # laundering a guess into "reviewed memory".
    record = core.load_tm(None)[core.tm_scope_key(TARGET_LANG, "The shared sentence.")]
    assert record[core.TM_CONTEXT_PREV] == core.tm_context_fingerprint(
        "Old previous paragraph.")


def test_matching_context_is_still_reused_without_a_model_call(temp_job_env: Path):
    _seed("The shared sentence.", "共享句旧译。",
          previous="First line.", next_="Last line.")
    state, prompts, reasons = _run(
        "tm-context-match", ["First line.", "The shared sentence.", "Last line."])

    shared = state["pairs"][1]
    assert shared["reviewed"] is True and shared["from_tm"] is True
    assert shared["review_status"] == "tm_approved"
    assert state.get("tm_used_count") == 1
    assert state.get("tm_conflict_count") in (None, 0)
    assert "The shared sentence." not in _numbered_sources(prompts)
    assert "exact" in reasons


# --------------------------------------------------------------------------
# #108: honest reasons and no miss storms
# --------------------------------------------------------------------------
def test_disabled_tm_records_one_run_state_not_a_miss_per_segment(temp_job_env: Path):
    state, prompts, reasons = _run(
        "tm-disabled", ["Sentence one.", "Sentence two.", "Sentence three."],
        use_tm=False)
    assert reasons == ["disabled"], "关闭 TM 不是逐段 miss：应只记录一次运行状态"
    assert state["tm_status"] == "disabled"
    # Translation still happened for every segment.
    assert len(prompts) >= 1
    assert all(pair["target"] for pair in state["pairs"])


def test_missing_target_language_is_reported_once(temp_job_env: Path):
    state, _prompts, reasons = _run(
        "tm-no-language", ["Sentence one.", "Sentence two."],
        target_lang="")
    assert reasons == ["no_target_language"]
    assert state["tm_status"] == "no_target_language"


def test_legacy_unscoped_entry_is_reported_and_never_reused(temp_job_env: Path):
    source = "The canopy closure index was recomputed."
    core.save_tm({source: {"target": "林冠郁闭指数被重新计算。", "reviewed": True}})
    state, prompts, reasons = _run(
        "tm-legacy-unscoped", [source, "Another sentence."])

    assert reasons.count("legacy_unscoped") == 1
    assert "exact" not in reasons and "normalized" not in reasons
    assert state.get("tm_legacy_unused") == 1
    pair = state["pairs"][0]
    assert pair.get("from_tm") is not True
    assert pair["target"] != "林冠郁闭指数被重新计算。", "旧条目不得被自动复用"
    assert any(source in prompt for prompt in prompts)


def test_run_context_summary_distinguishes_tm_states(tmp_path: Path):
    ctx = RunContext("tm-state-summary", output_dir=tmp_path)
    ctx.record_tm_lookup("disabled")
    summary = ctx.finish()
    tm = summary["tm_and_cache"]
    assert tm["tm_disabled"] is True
    assert tm["tm_lookups"] == 0
    assert tm["tm_misses"] == 0

    ctx = RunContext("tm-state-summary-2", output_dir=tmp_path)
    for hit_type in ("exact", "context_conflict", "legacy_unscoped", "miss"):
        ctx.record_tm_lookup(hit_type, duration_ms=0.1)
    tm = ctx.finish()["tm_and_cache"]
    assert tm["tm_lookups"] == 4
    assert tm["tm_context_conflicts"] == 1
    assert tm["tm_legacy_unscoped"] == 1
    assert tm["tm_misses"] == 1
    assert tm["tm_hit_ratio"] == 0.25


def test_workbench_label_says_why_a_tm_hit_was_not_reused():
    """#107 acceptance: the workbench must explain the downgraded hit."""
    from folith.qt import views

    label, note = views.translation_source_label({
        "tm_context_conflict": True, "review_status": "tm_context_conflict",
        "reviewed": False}, human_edited=False)
    assert "上下文不同" in label and "待确认" in label
    assert "已匹配并复用" not in label
    assert "待确认" in note

    label, note = views.translation_source_label({
        "tm_context_conflict": True, "review_status": "reviewed_context_conflict",
        "reviewed": True}, human_edited=False)
    assert "上下文不同" in label and "已独立审校" in label

    label, note = views.translation_source_label(
        {"from_tm": True, "reviewed": True}, human_edited=False)
    assert "已匹配并复用" in label and note == "来自翻译记忆"

    label, _note = views.translation_source_label(
        {"tm_context_conflict": True, "reviewed": False}, human_edited=True)
    assert label == "人工修改"


def test_pending_promotion_carries_context_through_recovery(temp_job_env: Path):
    """A crash-recovered promotion must not silently lose its neighbours."""
    checkpoint.append_event(temp_job_env, {
        "batch": 3, "offset": 9, "phase": "tm_promotion_pending",
        "entries": [{"source": "Shared.", "target": "共用。",
                     **core.tm_context_fields("Previous.", "Next.")}],
    })
    tm = {}
    changed, pending = checkpoint.reconcile_translation_memory(
        tm, {"pairs": []}, temp_job_env,
        scope=lambda s, t: (core.tm_scope_key(TARGET_LANG, s),
                            core.tm_record(t, TARGET_LANG)))
    assert changed and pending == 1
    record = tm[core.tm_scope_key(TARGET_LANG, "Shared.")]
    assert record[core.TM_CONTEXT_PREV] == core.tm_context_fingerprint("Previous.")
    assert record[core.TM_CONTEXT_NEXT] == core.tm_context_fingerprint("Next.")
