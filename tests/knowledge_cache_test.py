"""Knowledge-feedback reuse contracts.

Knowledge feedback is stateful: the model call produces observations, and a
separate deterministic merge folds them into project knowledge.  Reuse therefore
has to satisfy two things at once — the remote computation is not paid for
twice, *and* the local state effect is still replayed.  These tests pin both, plus
the identity rules that decide when reuse is allowed at all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import core
from folith import knowledge
from folith import knowledge_cache


TRANSLATION_MARKER = "【待翻译段落（按序号返回等长数组）】"
KNOWLEDGE_MARKER = "翻译流知识抽取器"


def _translations(user_prompt: str) -> str:
    tail = user_prompt.split(TRANSLATION_MARKER, 1)[-1]
    sources = re.findall(r"(?m)^\d+\.\s+(.*)$", tail)
    # Preserve each source's number: the deterministic number check (added for
    # #101) correctly flags drafts that drop one, and these fixtures are about
    # knowledge feedback, not about losing numbers.
    results = []
    for source in sources:
        numbers = re.findall(r"\d+", source)
        suffix = f" {numbers[-1]}" if numbers else ""
        results.append(f"欧洲空间局译文{suffix}")
    return json.dumps(results, ensure_ascii=False)


class Provider:
    def __init__(self, observation=None):
        self.knowledge_calls = 0
        self.observation = observation if observation is not None else {
            "segment_id": None, "source_expression": "European Space Agency",
            "observed_target": "欧洲空间局", "kind": "organization"}

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        if KNOWLEDGE_MARKER in (system_prompt or ""):
            self.knowledge_calls += 1
            observation = dict(self.observation)
            # The extractor must name the segment it observed.
            first = re.search(r"segment_id:\s*(\d+)", user_prompt or "")
            observation["segment_id"] = int(first.group(1)) if first else 0
            return json.dumps([observation], ensure_ascii=False)
        return _translations(user_prompt)


PARAGRAPHS = [
    "The European Space Agency leads the programme number 0.",
    "The European Space Agency reports progress number 1.",
    "A plain paragraph with no names at all, number 2.",
    "Another plain paragraph with no names at all, number 3.",
]


def _run(tmp_path: Path, job_id: str, *, provider, use_knowledge_cache=True,
         glossary=None, state_mutator=None, paragraphs=None, interval=None):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        state = core.new_job_state("knowledge-cache.docx")
        state["paras"] = list(paragraphs or PARAGRAPHS)
        if state_mutator is not None:
            state_mutator(state)
        core.save_job_state(job_id, state)
        core.call_llm = provider
        return core.translate_stage(
            state, job_id, list(glossary or []), "DeepSeek", "key", "model",
            "简体中文", "", enable_review=False, use_tm=False, batch_size=4,
            knowledge_feedback_interval=interval,
            use_knowledge_cache=use_knowledge_cache,
            # The exact translation cache is a separate optimisation; leaving it
            # on would make the second run reuse the *translation* and hide the
            # knowledge-cache behaviour under test.
            use_translation_cache=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_and_ignores_nothing_material():
    base = dict(provider="P", model="M", system_prompt="S", user_prompt="U",
                temperature=0.1)
    first = knowledge_cache.feedback_fingerprint(**base)
    assert first == knowledge_cache.feedback_fingerprint(**base)
    assert len(first) == 64
    for field, value in (("provider", "P2"), ("model", "M2"),
                         ("system_prompt", "S2"), ("user_prompt", "U2"),
                         ("temperature", 0.2)):
        changed = dict(base)
        changed[field] = value
        assert knowledge_cache.feedback_fingerprint(**changed) != first, field


def test_fingerprint_covers_every_render_field():
    """The rendered request is the identity: ids, source and target all count."""
    seen = set()
    for segment_id, source, target in ((0, "a", "甲"), (1, "a", "甲"),
                                       (0, "b", "甲"), (0, "a", "乙")):
        user = f"segment_id: {segment_id}\n原文：{source}\n译文：{target}"
        seen.add(knowledge_cache.feedback_fingerprint(
            provider="P", model="M", system_prompt="S", user_prompt=user,
            temperature=knowledge.FEEDBACK_TEMPERATURE))
    assert len(seen) == 4


# ---------------------------------------------------------------------------
# Store behaviour
# ---------------------------------------------------------------------------


def test_store_round_trips_through_disk(tmp_path):
    path = tmp_path / "knowledge_cache.json"
    cache = knowledge_cache.KnowledgeObservationCache()
    key = "k" * 64
    assert cache.put(key, [{"source_expression": "ESA", "observed_target": "欧空局",
                            "kind": "organization", "segment_id": 3}],
                     provider="P", model="M")
    assert knowledge_cache.save(cache, path)
    reloaded = knowledge_cache.load(path)
    assert reloaded.get(key) == [{"source_expression": "ESA",
                                  "observed_target": "欧空局",
                                  "kind": "organization", "segment_id": 3}]
    assert reloaded.stats()["entries"] == 1


def test_empty_observation_list_is_a_real_result():
    """A batch that yields nothing is still a paid-for, reusable answer."""
    cache = knowledge_cache.KnowledgeObservationCache()
    assert cache.put("k" * 64, [], provider="P", model="M")
    assert cache.get("k" * 64) == []
    assert cache.stats()["hits"] == 1


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "knowledge_cache.json"
    path.write_text("{not json", encoding="utf-8")
    cache = knowledge_cache.load(path)
    assert cache.stats()["entries"] == 0
    assert cache.get("k" * 64) is None


def test_corrupt_entry_is_dropped_at_load_and_at_lookup():
    # Malformed entries never survive construction ...
    cache = knowledge_cache.KnowledgeObservationCache({
        "good": {"observations": [{"source_expression": "a", "observed_target": "甲",
                                   "kind": "term", "segment_id": 0}]},
        "bad": {"observations": [{"source_expression": "a"}]},
    })
    assert "bad" not in cache.entries
    assert cache.get("bad") is None
    assert cache.get("good") is not None
    # ... and an entry corrupted after construction degrades to a safe miss.
    cache.entries["rotten"] = {"observations": [{"segment_id": "not-an-int",
                                                 "source_expression": "a",
                                                 "observed_target": "甲",
                                                 "kind": "term"}]}
    assert cache.get("rotten") is None
    assert cache.stats()["invalid_entries"] == 1
    assert "rotten" not in cache.entries


def test_version_bump_invalidates_the_whole_file():
    payload = {"version": knowledge_cache.CACHE_VERSION + 1,
               "entries": {"k": {"observations": []}}}
    cache = knowledge_cache.from_payload(payload)
    assert cache.stats()["entries"] == 0


def test_store_is_bounded():
    cache = knowledge_cache.KnowledgeObservationCache(
        max_entries=2, max_total_chars=10_000_000)
    for index in range(4):
        cache.put(f"k{index}", [{"source_expression": f"t{index}",
                                 "observed_target": "译", "kind": "term",
                                 "segment_id": index}])
    assert cache.stats()["entries"] == 2
    assert cache.stats()["evictions"] == 2
    assert cache.get("k0") is None and cache.get("k3") is not None


def test_oversized_entry_is_rejected():
    cache = knowledge_cache.KnowledgeObservationCache()
    huge = [{"source_expression": "x" * 500, "observed_target": "y" * 500,
             "kind": "term", "segment_id": index} for index in range(100)]
    assert cache.put("k" * 64, huge) is False
    assert cache.stats()["rejected"] == 1


# ---------------------------------------------------------------------------
# Reuse through the real pipeline
# ---------------------------------------------------------------------------


def test_warm_rerun_skips_every_remote_feedback_call(tmp_path):
    cold = Provider()
    first = _run(tmp_path, "warm-cold", provider=cold)
    assert cold.knowledge_calls > 0
    assert first["knowledge_feedback_policy"]["cache_misses"] == \
        len(first["knowledge_feedback_policy"]["observed_batches"])

    warm = Provider()
    second = _run(tmp_path, "warm-hot", provider=warm)
    policy = second["knowledge_feedback_policy"]
    assert warm.knowledge_calls == 0, "an identical request must not be paid twice"
    assert policy["cache_hits"] == len(policy["observed_batches"]) > 0
    assert policy["cache_misses"] == 0
    # Billed usage must not count the replayed calls.
    assert int(second["llm_usage"]["by_role"].get("auxiliary", {}).get("calls", 0)) == 0
    assert int(first["llm_usage"]["by_role"]["auxiliary"]["calls"]) == cold.knowledge_calls


def test_cache_hit_still_rebuilds_project_knowledge(tmp_path):
    first = _run(tmp_path, "merge-cold", provider=Provider())
    second = _run(tmp_path, "merge-hot", provider=Provider())
    assert first["entity_registry"] == second["entity_registry"]
    assert first["knowledge_candidates"] == second["knowledge_candidates"]
    assert first["knowledge_events"] == second["knowledge_events"]
    assert second["entity_registry"], "replay must still produce the entity"


def test_cache_hit_preserves_order_and_targets(tmp_path):
    first = _run(tmp_path, "order-cold", provider=Provider())
    second = _run(tmp_path, "order-hot", provider=Provider())
    assert [p["source"] for p in second["pairs"]] == \
        [p["source"] for p in first["pairs"]]
    assert [p["target"] for p in second["pairs"]] == \
        [p["target"] for p in first["pairs"]]
    assert [c["source"] for c in second["knowledge_candidates"]] == \
        [c["source"] for c in first["knowledge_candidates"]]


MANY = [f"Paragraph number {index} about space activities and reporting."
        for index in range(12)]


def test_translated_text_change_invalidates_only_that_batch(tmp_path):
    first = _run(tmp_path, "inv-cold", provider=Provider(), paragraphs=MANY,
                 interval=1)
    assert first["knowledge_feedback_policy"]["cache_misses"] == 3

    class ChangingProvider(Provider):
        def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                     temperature=0.1, **kwargs):
            if KNOWLEDGE_MARKER in (system_prompt or ""):
                return super().__call__(provider, api_key, model, system_prompt,
                                        user_prompt, temperature, **kwargs)
            tail = user_prompt.split(TRANSLATION_MARKER, 1)[-1]
            sources = re.findall(r"(?m)^\d+\.\s+(.*)$", tail)
            # Only the final batch's rendering changes.
            suffix = "（修订）" if "number 8 " in tail else ""
            results = []
            for source in sources:
                numbers = re.findall(r"\d+", source)
                kept = f" {numbers[-1]}" if numbers else ""
                results.append(f"欧洲空间局译文{kept}{suffix}")
            return json.dumps(results, ensure_ascii=False)

    altered = _run(tmp_path, "inv-hot", provider=ChangingProvider(),
                   paragraphs=MANY, interval=1)
    policy = altered["knowledge_feedback_policy"]
    # Granular: exactly the batch whose rendering changed misses.
    assert policy["cache_misses"] == 1
    assert policy["cache_hits"] == 2


def test_unrelated_project_state_does_not_invalidate(tmp_path):
    """Project state is not in the rendered request, so it must not invalidate."""
    _run(tmp_path, "state-cold", provider=Provider())

    def seed(state):
        state["knowledge_candidates"] = [{
            "source": "unrelated seeded term", "observed_target": "无关",
            "kind": "term", "status": "emergent_candidate"}]

    warm = Provider()
    second = _run(tmp_path, "state-hot", provider=warm, state_mutator=seed)
    assert warm.knowledge_calls == 0
    assert second["knowledge_feedback_policy"]["cache_hits"] > 0


def test_model_change_invalidates(tmp_path):
    _run(tmp_path, "model-cold", provider=Provider())
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    provider = Provider()
    try:
        state = core.new_job_state("knowledge-cache.docx")
        state["paras"] = list(PARAGRAPHS)
        core.save_job_state("model-hot", state)
        core.call_llm = provider
        result = core.translate_stage(
            state, "model-hot", [], "DeepSeek", "key", "different-model",
            "简体中文", "", enable_review=False, use_tm=False, batch_size=4,
            use_translation_cache=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm
    assert provider.knowledge_calls > 0
    assert result["knowledge_feedback_policy"]["cache_misses"] > 0


def test_cache_disabled_makes_every_call_live(tmp_path):
    first = Provider()
    _run(tmp_path, "off-cold", provider=first)
    second = Provider()
    _run(tmp_path, "off-hot", provider=second, use_knowledge_cache=False)
    assert second.knowledge_calls == first.knowledge_calls > 0


def test_mandatory_mode_still_runs_every_batch_and_reuses(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        for job_id in ("mand-cold", "mand-hot"):
            provider = Provider()
            state = core.new_job_state("knowledge-cache.docx")
            state["paras"] = list(PARAGRAPHS)
            core.save_job_state(job_id, state)
            core.call_llm = provider
            result = core.translate_stage(
                state, job_id, [], "DeepSeek", "key", "model", "简体中文", "",
                enable_review=False, use_tm=False, batch_size=4,
                knowledge_feedback_interval=1, use_translation_cache=False)
            policy = result["knowledge_feedback_policy"]
            assert policy["force_every_batch"] is True
            assert len(policy["observed_batches"]) == 1
            if job_id == "mand-cold":
                assert provider.knowledge_calls == 1
            else:
                assert provider.knowledge_calls == 0
                assert policy["cache_hits"] == 1
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


# ---------------------------------------------------------------------------
# Replay still merges against *current* state
# ---------------------------------------------------------------------------


def test_replay_merges_against_current_glossary_not_the_cached_one():
    """The remote answer is reused; the merge verdict must be recomputed."""
    cache = knowledge_cache.KnowledgeObservationCache()
    reply = json.dumps([{"segment_id": 1, "source_expression": "river bank",
                         "observed_target": "河流银行", "kind": "term"}])
    paragraphs = ["The river bank is old.", "The river bank is wide."]
    pairs = [{"source": paragraphs[0], "target": "河岸很老。"}]

    def call_llm(*args, **kwargs):
        return reply

    _candidates, events, _warning = knowledge.observe_batch(
        [paragraphs[1]], ["河流银行很宽。"], paragraphs, pairs, [], 1,
        "P", "k", "m", call_llm=call_llm, segment_ids=[1], cache=cache)
    assert [e["type"] for e in events] == ["emergent_candidate"]
    assert cache.stats()["misses"] == 1

    # Same request, same cache entry, but the project has since locked the term
    # to a different rendering: the replay must report the conflict.
    locked = [{"source": "river bank", "target": "河岸", "status": "locked",
               "behavior": "translate"}]
    _candidates, events, _warning = knowledge.observe_batch(
        [paragraphs[1]], ["河流银行很宽。"], paragraphs, pairs, locked, 1,
        "P", "k", "m", call_llm=call_llm, segment_ids=[1], cache=cache)
    assert [e["type"] for e in events] == ["target_conflict"]
    assert events[0]["preferred_target"] == "河岸"
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_phase_events_distinguish_live_replay_and_skip(tmp_path):
    _run(tmp_path, "phase-cold", provider=Provider(), paragraphs=MANY,
         interval=1)
    _run(tmp_path, "phase-hot", provider=Provider(), paragraphs=MANY,
         interval=1)

    def phases(job_id):
        path = tmp_path / job_id / "events.jsonl"
        return [json.loads(line)["phase"]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    cold = phases("phase-cold")
    # Every batch observed at interval 1 is a live call, none are skipped.
    assert cold.count("knowledge_feedback_done") == 3
    assert cold.count("knowledge_feedback_cache_hit") == 0
    assert cold.count("knowledge_feedback_skipped") == 0
    # A replay must be recorded as a replay, never as a fresh model call.
    warm = phases("phase-hot")
    assert warm.count("knowledge_feedback_cache_hit") == 3
    assert warm.count("knowledge_feedback_done") == 0


def test_cached_replay_does_not_count_as_model_usage(tmp_path):
    cold = _run(tmp_path, "usage-cold", provider=Provider(), paragraphs=MANY,
                interval=1)
    warm = _run(tmp_path, "usage-hot", provider=Provider(), paragraphs=MANY,
                interval=1)
    cold_aux = cold["llm_usage"]["by_role"]["auxiliary"]
    assert cold_aux["calls"] == 3
    assert cold_aux["prompt_tokens"] > 0
    assert "auxiliary" not in warm["llm_usage"]["by_role"], \
        "a replayed request must not be billed as model usage"
    # The saving is reported on the cache, never mixed into the usage ledger.
    assert warm["knowledge_cache"]["hits"] == 3
    assert warm["knowledge_cache"]["retained_observation_tokens"] > 0


def test_endpoint_change_invalidates(tmp_path):
    """Same provider and model name behind another relay is another model."""
    def run(job_id, base_url, provider):
        old_output, old_llm = core.OUTPUT_DIR, core.call_llm
        core.OUTPUT_DIR = tmp_path
        core.set_llm_base_url(base_url)
        try:
            state = core.new_job_state("knowledge-cache.docx")
            state["paras"] = list(PARAGRAPHS)
            core.save_job_state(job_id, state)
            core.call_llm = provider
            return core.translate_stage(
                state, job_id, [], "自定义中转站", "key", "model",
                "简体中文", "", enable_review=False, use_tm=False, batch_size=4,
                use_translation_cache=False)
        finally:
            core.set_llm_base_url(None)
            core.OUTPUT_DIR, core.call_llm = old_output, old_llm

    run("relay-a", "https://relay-a.example/v1", Provider())
    provider = Provider()
    result = run("relay-b", "https://relay-b.example/v1", provider)
    assert provider.knowledge_calls > 0
    assert result["knowledge_feedback_policy"]["cache_hits"] == 0


def test_fingerprint_covers_the_route():
    base = dict(provider="P", model="M", system_prompt="S", user_prompt="U",
                temperature=0.1)
    assert knowledge_cache.feedback_fingerprint(
        **base, route={"base_url": "https://a/v1"}) != \
        knowledge_cache.feedback_fingerprint(
            **base, route={"base_url": "https://b/v1"})
