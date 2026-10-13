"""Global segment scopes survive batching, TM filtering and manual review."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folith import models, terminology


def _entry(segment_id):
    return models.normalize_glossary_entry({
        "source": "ecological term", "target": "生态术语", "status": "locked",
        "scope": f"segment:{segment_id}",
    })


def test_segment_42_is_only_injected_in_its_batch():
    entry = _entry(42)
    batches = [list(range(0, 20)), list(range(20, 40)), list(range(40, 45))]
    for ids in batches:
        selected, injected = terminology.select_glossary_for_segments(
            ["An ecological term occurs here."] * len(ids), [entry],
            segment_ids=ids)
        assert injected == ([entry["id"]] if 42 in ids else [])
        assert selected == ([entry] if 42 in ids else [])


def test_omitting_segment_ids_preserves_local_index_behavior():
    entries = [_entry(0), _entry(42)]
    selected, injected = terminology.select_glossary_for_segments(
        ["An ecological term occurs here."], entries)
    assert selected == [entries[0]]
    assert injected == [entries[0]["id"]]


def test_translation_stage_aligns_global_ids_after_tm_filtering(tmp_path, monkeypatch):
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    entry = _entry(42)
    paras = [f"An ecological term occurs in paragraph {i}." for i in range(45)]
    state = core.new_job_state("scoped.docx")
    state.update(paras=paras, p1_done=True)
    tm = {core.tm_scope_key("简体中文", paras[40]):
          core.tm_record("生态术语出现在第 40 段。", "简体中文")}
    monkeypatch.setattr(core, "load_tm", lambda *args: tm)
    monkeypatch.setattr(core, "call_llm", lambda *args, **kwargs: "[]")
    calls = []

    def translate(segments, ctx_prev, ctx_next, glossary_text, *args, **kwargs):
        calls.append((list(segments), glossary_text))
        return [f"生态术语出现在第 {source.split()[-1]} 段。" for source in segments]

    monkeypatch.setattr(core, "translate_batch", translate)
    result = core.translate_stage(
        state, "scoped", [entry], "DeepSeek", "k", "m", "简体中文", "",
        enable_review=False, use_tm=True, batch_size=20, max_batch_chars=10000,
        translation_concurrency=1, use_translation_cache=False,
        use_knowledge_cache=False)
    assert len(calls) == 3
    assert [entry["id"] in glossary for _, glossary in calls] == [False, False, True]
    assert paras[40] not in calls[2][0]
    assert paras[42] in calls[2][0]
    assert result["pairs"][40]["from_tm"] is True
    assert result["pairs"][42]["glossary_entry_ids"] == [entry["id"]]


def test_single_segment_review_injects_global_scope(tmp_path, monkeypatch):
    import core
    from folith import translation_evidence

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    entry = _entry(42)
    state = core.new_job_state("review-scoped.docx")
    paras = [f"An ecological term occurs in paragraph {i}." for i in range(43)]
    state.update(paras=paras, p1_done=True, p2_done=True, use_tm=False,
                 glossary=[entry], pairs=[{"source": source, "target": "生态术语。"}
                                           for source in paras])
    core.save_job_state("review-scoped", state)
    prompts = []
    glossary_blocks = []
    real_review = translation_evidence.review_translation_batch_with_evidence

    def review_with_glossary(sources, targets, glossary_text, *args, **kwargs):
        glossary_blocks.append(glossary_text)
        return real_review(sources, targets, glossary_text, *args, **kwargs)

    monkeypatch.setattr(translation_evidence, "review_translation_batch_with_evidence",
                        review_with_glossary)

    def reviewer(provider, key, model, system, user, **kwargs):
        prompts.append(system + "\n" + user)
        return json.dumps({"findings": [], "evidence_requests": []})

    _, result = core.review_translation_segments(
        "review-scoped", [42], "DeepSeek", "k", "m", "简体中文",
        call_llm_fn=reviewer)
    assert result["reviewed_segment_ids"] == [42]
    assert prompts and entry["id"] in prompts[0]
    assert glossary_blocks and entry["id"] in glossary_blocks[0]
