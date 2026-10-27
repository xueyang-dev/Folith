"""Unit tests for TM candidates matching, scoring, and provenance (Issue #151)."""
import pytest

from folith.engine.tm_store import tm_candidates
from folith.translation_memory import tm_record, tm_scope_key, tm_context_fields


def test_exact_and_normalized_matching():
    target_lang = "zh-CN"
    tm = {}
    
    # 1. Exact match
    key1 = tm_scope_key(target_lang, "Save and continue")
    tm[key1] = tm_record("保存并继续", target_lang, reviewed=True)
    
    # 2. Normalized match (contains non-breaking space / quote differences)
    key2 = tm_scope_key(target_lang, "“Hello world”")
    tm[key2] = tm_record("“你好世界”", target_lang, reviewed=True)

    # Exact query
    cands = tm_candidates(tm, "Save and continue", target_lang=target_lang)
    assert len(cands) == 1
    assert cands[0]["score"] == 100
    assert cands[0]["match_type"] == "exact"
    assert cands[0]["target"] == "保存并继续"
    assert cands[0]["provenance"] == "项目 TM"
    assert cands[0]["conflict_warning"] is False

    # Normalized query: straight quotes "Hello world" vs curly quotes “Hello world”
    cands_norm = tm_candidates(tm, '"Hello world"', target_lang=target_lang)
    assert len(cands_norm) == 1
    assert cands_norm[0]["score"] == 99
    assert cands_norm[0]["match_type"] == "normalized"
    assert cands_norm[0]["target"] == "“你好世界”"


def test_fuzzy_matching_and_scoring():
    target_lang = "zh-CN"
    tm = {}
    
    key = tm_scope_key(target_lang, "The quick brown fox jumps over the lazy dog.")
    tm[key] = tm_record("敏捷的棕色狐狸跳过了懒狗。", target_lang, reviewed=True)

    # Slight variation (fuzzy match)
    query = "The fast brown fox jumps over the lazy dog."
    cands = tm_candidates(tm, query, target_lang=target_lang, min_score=70)
    assert len(cands) == 1
    assert cands[0]["match_type"] == "fuzzy"
    assert 70 <= cands[0]["score"] < 100
    assert cands[0]["target"] == "敏捷的棕色狐狸跳过了懒狗。"


def test_context_conflict_warning():
    target_lang = "zh-CN"
    tm = {}
    
    # Stored with context prev="Chapter 1", next="Chapter 2"
    ctx_fields = tm_context_fields("Chapter 1", "Chapter 2")
    rec = tm_record("关闭", target_lang, reviewed=True)
    rec.update(ctx_fields)
    key = tm_scope_key(target_lang, "Close")
    tm[key] = rec

    # Query with matching context
    cands_ok = tm_candidates(
        tm, "Close", target_lang=target_lang,
        context=("Chapter 1", "Chapter 2")
    )
    assert len(cands_ok) == 1
    assert cands_ok[0]["conflict_warning"] is False
    assert cands_ok[0]["context_status"] == "match"

    # Query with conflicting context
    cands_conflict = tm_candidates(
        tm, "Close", target_lang=target_lang,
        context=("Dialog Title", "Cancel button")
    )
    assert len(cands_conflict) == 1
    assert cands_conflict[0]["conflict_warning"] is True
    assert cands_conflict[0]["context_status"] == "conflict"


def test_legacy_unscoped_not_shown():
    target_lang = "zh-CN"
    tm = {}
    
    # Legacy record without target language scoping
    tm["Legacy text"] = {
        "target": "遗留译文",
        "reviewed": True,
        # No target_lang or lang fields
    }

    cands = tm_candidates(tm, "Legacy text", target_lang=target_lang)
    # Legacy unscoped entries must NOT be displayed
    assert len(cands) == 0


def test_provenance_imported():
    target_lang = "zh-CN"
    tm = {}
    
    key = tm_scope_key(target_lang, "Welcome user")
    rec = tm_record("欢迎用户", target_lang, reviewed=True)
    rec["source"] = "tmx_import"
    tm[key] = rec

    cands = tm_candidates(tm, "Welcome user", target_lang=target_lang)
    assert len(cands) == 1
    assert cands[0]["provenance"] == "导入 TM"


def test_no_target_language_or_empty_source():
    tm = {"key": {"target": "ok", "reviewed": True}}
    assert tm_candidates(tm, "Some source", target_lang="") == []
    assert tm_candidates(tm, "", target_lang="zh-CN") == []
