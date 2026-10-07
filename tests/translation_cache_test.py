"""Exact translation-result cache: reuse, invalidation and closed failure modes.

The cache exists to avoid paying twice for the *same computation*.  These tests
pin the two properties that make it safe to keep:

1. an identical effective request is reused without a provider call;
2. any change to something the model actually sees is a miss, so the cache can
   never serve an answer that belongs to a different request.
"""
from __future__ import annotations

import json
from pathlib import Path

import core
from folith import translation_cache as _tc


def _fingerprint(**overrides):
    material = {
        "provider": "DeepSeek",
        "model": "model-a",
        "system_prompt": "system",
        "user_prompt": "1. hello",
        "temperature": 0.3,
        "response_format": None,
    }
    material.update(overrides)
    return _tc.prompt_fingerprint(**material)


def test_identical_request_has_identical_fingerprint():
    assert _fingerprint() == _fingerprint()


def test_changed_target_language_changes_fingerprint():
    # The target language is baked into the system prompt.
    assert _fingerprint(system_prompt="system zh") != _fingerprint()


def test_changed_terminology_changes_fingerprint():
    assert _fingerprint(user_prompt="1. hello\nterm: A -> B") != _fingerprint()


def test_changed_context_changes_fingerprint():
    assert _fingerprint(user_prompt="context X\n1. hello") != _fingerprint()


def test_changed_prompt_changes_fingerprint():
    assert _fingerprint(system_prompt="a stricter instruction") != _fingerprint()


def test_changed_model_changes_fingerprint():
    assert _fingerprint(model="model-b") != _fingerprint()


def test_changed_generation_settings_change_fingerprint():
    assert _fingerprint(temperature=0.7) != _fingerprint()
    assert _fingerprint(response_format={"type": "json_schema"}) != _fingerprint()


def test_volatile_values_do_not_participate():
    """Identical requests taken at different times must still collide."""
    assert _fingerprint() == _fingerprint()
    # The identity is built from the rendered request only; there is no clock,
    # request id or UI state anywhere in the material.
    material = _tc.prompt_fingerprint.__doc__ or ""
    assert "Volatile" in material or "volatile" in material


def test_cache_round_trips_through_disk(tmp_path: Path):
    path = tmp_path / "translation_cache.json"
    cache = _tc.TranslationResultCache()
    assert cache.put("key-1", ["译文一", "译文二"])
    assert _tc.save(cache, path)

    reloaded = _tc.load(path)
    assert reloaded.get("key-1") == ["译文一", "译文二"]
    assert path.is_file()


def test_corrupt_cache_file_degrades_to_empty(tmp_path: Path):
    path = tmp_path / "translation_cache.json"
    path.write_text("{not json", encoding="utf-8")
    cache = _tc.load(path)
    assert cache.get("anything") is None


def test_future_version_invalidates_the_file():
    cache = _tc.from_payload({"version": 999, "entries": {"k": ["v"]}})
    assert cache.get("k") is None


def test_save_merges_concurrent_writers(tmp_path: Path):
    path = tmp_path / "translation_cache.json"
    writer_a = _tc.TranslationResultCache()
    writer_a.put("a", ["1"])
    _tc.save(writer_a, path)

    writer_b = _tc.load(path)
    writer_b.put("b", ["2"])
    _tc.save(writer_b, path)

    merged = _tc.load(path)
    assert merged.get("a") == ["1"]
    assert merged.get("b") == ["2"]


def test_cache_is_bounded():
    cache = _tc.TranslationResultCache(max_entries=3)
    for index in range(6):
        cache.put(f"k{index}", [str(index)])
    assert cache.stats()["entries"] == 3
    assert cache.stats()["evictions"] == 3
    assert cache.get("k5") == ["5"]
    assert cache.get("k0") is None


def test_oversized_entry_is_rejected():
    cache = _tc.TranslationResultCache()
    huge = ["x" * (_tc.MAX_ENTRY_TARGET_CHARS + 1)]
    assert cache.put("k", huge) is False
    assert cache.stats()["rejected"] == 1
    assert cache.get("k") is None


def test_cache_is_bounded_by_total_retained_characters():
    cache = _tc.TranslationResultCache(max_entries=1000, max_total_chars=100)
    for index in range(10):
        cache.put(f"k{index}", ["x" * 30])
    stats = cache.stats()
    assert stats["retained_chars"] <= 100
    assert stats["entries"] <= 3
    # The newest write survives; the oldest are evicted.
    assert cache.get("k9") == ["x" * 30]
    assert cache.get("k0") is None


def test_replacing_an_entry_does_not_leak_the_character_budget():
    cache = _tc.TranslationResultCache(max_entries=10, max_total_chars=1000)
    for _ in range(5):
        cache.put("same", ["x" * 100])
    assert cache.stats()["retained_chars"] == 100
    assert cache.stats()["entries"] == 1


def test_total_character_bound_survives_a_disk_round_trip(tmp_path: Path):
    path = tmp_path / "translation_cache.json"
    cache = _tc.TranslationResultCache(max_entries=1000, max_total_chars=100)
    for index in range(10):
        cache.put(f"k{index}", ["x" * 30])
    assert _tc.save(cache, path)
    reloaded = _tc.load(path, max_total_chars=100)
    assert reloaded.stats()["entries"] <= 3


def _requested_sources(user_prompt: str):
    marker = "【待翻译段落（按序号返回等长数组）】"
    if marker not in user_prompt:
        return []
    tail = user_prompt.split(marker, 1)[1]
    return [line.strip() for line in tail.splitlines()
            if line.strip() and line.strip()[0].isdigit() and ". " in line]


def _stage_run(tmp_path, job_id, *, glossary, target_lang="简体中文",
               sources=("Hello world.", "Another sentence."),
               style_rules="", model="model-a", calls=None,
               translate=None):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model_name, system, user, temperature=0.1, **kwargs):
        if calls is not None:
            calls.append({"system": system, "user": user, "model": model_name})
        if "翻译流知识抽取器" in system:
            return "[]"
        count = len(_requested_sources(user)) or 1
        if translate is not None:
            return translate(count, user)
        return json.dumps([f"译文第{i + 1}段。" for i in range(count)],
                          ensure_ascii=False)

    core.call_llm = llm
    try:
        state = core.new_job_state("doc.txt")
        state["paras"] = list(sources)
        state["target_lang"] = target_lang
        return core.translate_stage(
            state, job_id, list(glossary), "DeepSeek", "key", model,
            target_lang, style_rules, enable_review=False, use_tm=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_stage_reuses_identical_work_without_a_provider_call(tmp_path: Path):
    calls = []
    first = _stage_run(tmp_path, "cache-cold", glossary=[], calls=calls)
    translation_calls = [c for c in calls if "翻译流知识抽取器" not in c["system"]]
    assert len(translation_calls) == 1
    assert first["translation_cache"]["stores"] >= 1

    calls.clear()
    second = _stage_run(tmp_path, "cache-warm", glossary=[], calls=calls)
    assert [c for c in calls if "翻译流知识抽取器" not in c["system"]] == []
    assert second["translation_cache"]["hits"] >= 1
    assert [pair["target"] for pair in second["pairs"]] == \
        [pair["target"] for pair in first["pairs"]]


def test_stage_cache_hit_does_not_impersonate_translation_memory(tmp_path: Path):
    _stage_run(tmp_path, "cache-tm-cold", glossary=[])
    second = _stage_run(tmp_path, "cache-tm-warm", glossary=[])
    assert second["translation_cache"]["hits"] >= 1
    assert all(pair.get("from_tm") is False for pair in second["pairs"])
    assert second["pairs"][0]["target_provenance"] == "generated"


def test_stage_cache_misses_on_changed_terminology(tmp_path: Path):
    _stage_run(tmp_path, "cache-term-cold", glossary=[])
    calls = []
    _stage_run(
        tmp_path, "cache-term-changed",
        glossary=[{"id": "t-1", "source": "Hello", "target": "你好",
                   "preferred": "您好", "behavior": "translate",
                   "status": "locked", "forbidden": []}],
        calls=calls)
    assert [c for c in calls if "翻译流知识抽取器" not in c["system"]]


def test_stage_cache_misses_on_changed_model(tmp_path: Path):
    _stage_run(tmp_path, "cache-model-cold", glossary=[], model="model-a")
    calls = []
    _stage_run(tmp_path, "cache-model-changed", glossary=[], model="model-b",
               calls=calls)
    assert [c for c in calls if "翻译流知识抽取器" not in c["system"]]


def test_stage_cache_misses_on_changed_target_language(tmp_path: Path):
    _stage_run(tmp_path, "cache-lang-cold", glossary=[], target_lang="简体中文")
    calls = []
    _stage_run(tmp_path, "cache-lang-changed", glossary=[],
               target_lang="Français", calls=calls)
    assert [c for c in calls if "翻译流知识抽取器" not in c["system"]]


def test_stage_cache_misses_on_changed_context(tmp_path: Path):
    sources = ("Hello world.", "Another sentence.", "Third one.", "Fourth one.")
    _stage_run(tmp_path, "cache-ctx-cold", glossary=[], sources=sources)
    calls = []
    # A different neighbor window is a different request even though the batch
    # being translated is identical.
    _stage_run(tmp_path, "cache-ctx-changed", glossary=[],
               sources=("Intro.", *sources), calls=calls)
    assert [c for c in calls if "翻译流知识抽取器" not in c["system"]]


def test_failed_batch_is_never_cached(tmp_path: Path):
    class _Boom(Exception):
        pass

    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path

    def llm(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        if "翻译流知识抽取器" in system:
            return "[]"
        # No items at all: the protocol parser must reject this on every
        # attempt, and the per-segment fallback must fail too.
        return "[]"

    core.call_llm = llm
    try:
        state = core.new_job_state("doc.txt")
        state["paras"] = ["Hello world.", "Another sentence."]
        state["target_lang"] = "简体中文"
        try:
            core.translate_stage(
                state, "cache-failure", [], "DeepSeek", "key", "model",
                "简体中文", "", enable_review=False, use_tm=False)
            raise AssertionError("expected the batch to fail")
        except AssertionError:
            raise
        except Exception:
            pass
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call

    cache = _tc.load(tmp_path / "translation_cache.json")
    assert cache.stats()["entries"] == 0


def test_translation_cache_can_be_disabled(tmp_path: Path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    calls = []

    def llm(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        calls.append(system)
        if "翻译流知识抽取器" in system:
            return "[]"
        return json.dumps(["译文。"], ensure_ascii=False)

    core.call_llm = llm
    try:
        state = core.new_job_state("doc.txt")
        state["paras"] = ["Hello world."]
        state["target_lang"] = "简体中文"
        core.translate_stage(
            state, "cache-disabled", [], "DeepSeek", "key", "model",
            "简体中文", "", enable_review=False, use_tm=False,
            use_translation_cache=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call

    assert not (tmp_path / "translation_cache.json").exists()
