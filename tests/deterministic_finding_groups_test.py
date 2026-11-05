"""Deterministic finding grouping for numbers/units/tags/placeholders (#101).

Pins the acceptance points:
* each type has positive and negative detection examples;
* grouping preserves evidence, location, decisions and modification records;
* excluded occurrences stay out of the group while remaining findable;
* handling previews are read-only and the P0 per-item path is the only writer;
* a deterministic number fix is a candidate and a stale candidate cannot commit;
* no model call; disabled grouping leaves findings individually inspectable.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import core
from folith import finding_groups as fg
from folith import models
from folith import persistence_profiler
from folith import workbench_view
from folith.storage import job_repository as repo


@pytest.fixture
def temp_job_env(tmp_path):
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


def _findings(src, tgt, lang="简体中文"):
    return core.check_translation_batch([src], [tgt], [], lang)


def _by_kind(findings, kind):
    return [item for item in findings if item.get("kind") == kind]


def test_number_detection_positive_and_negative():
    missing = _findings("The sample weighs 12.5 grams today.",
                        "样本今天重克。")
    numbers = _by_kind(missing, "number")
    assert [item["group_token"] for item in numbers] == ["12.5"]
    assert numbers[0]["group_type"] == "number"
    assert numbers[0]["severity"] == "actionable"

    # Digits written in the target's numerals are not "missing".
    assert _by_kind(_findings("The sample weighs 12.5 grams.",
                              "样本重 12.5 克。"), "number") == []
    # A citation's digits belong to the preserved-token check, not to numbers.
    assert _by_kind(_findings("See [12] for details.", "详见 [12]。"), "number") == []
    # A number that changes only its digit script is accepted where folded.
    assert _by_kind(_findings("Item 3 is ready.", "第 ٣ 项已就绪。"),
                    "number") == []

    # CJK-adjacent digits are ordinary numbers, not word-glued identifiers:
    # ``\w`` is Unicode-aware, so a naive boundary would hide "第0段" and report
    # every Chinese sentence with a number as "missing".
    assert _by_kind(_findings(
        "Segment number 0 mentions Term0 and Gadget0.",
        "第0段提到Term0和Gadget0。"), "number") == []
    assert _by_kind(_findings("第3章有5个例子。", "Chapter 3 has 5 examples."),
                    "number") == []
    # Digits inside ASCII identifiers stay preserved-token territory.
    assert _by_kind(_findings("Use Term0 here.", "第0项使用Term0。"),
                    "number") == []


def test_unit_detection_positive_and_negative():
    kept = _findings("The village is 5 km from here.", "村庄离这里 5 公里。")
    assert _by_kind(kept, "unit") == []

    drifted = _findings("The village is 5 km from here.", "村庄离这里 5 英里。")
    units = _by_kind(drifted, "unit")
    assert len(units) == 1
    assert units[0]["group_token"] == "km"
    assert units[0]["expected_number"] == "5"
    assert units[0]["expected_unit"] == "km"

    missing = _findings("The village is 5 km from here.", "村庄离这里很近。")
    assert len(_by_kind(missing, "unit")) == 1


def test_tag_detection_positive_and_negative():
    lost = _findings("<b>Warning</b> read this.", "警告：请阅读。")
    tags = _by_kind(lost, "tag")
    assert {item["group_token"] for item in tags} == {"<b>", "</b>"}
    assert _by_kind(_findings("<b>Warning</b> read this.",
                              "<b>警告</b>：请阅读。"), "tag") == []


def test_placeholder_detection_is_grouped_too():
    findings = _findings("Hello {name}, welcome.", "你好，欢迎。")
    placeholders = _by_kind(findings, "placeholder")
    assert placeholders, "existing placeholder check must keep working"
    groups = fg.group_findings(findings)
    assert any(group["group_type"] == "placeholder" for group in groups)


def test_grouping_preserves_evidence_and_decisions():
    findings = [
        {**_findings("The sample weighs 12.5 grams.", "样本重克。")[0],
         "finding_id": "f-1", "decision": "request_revision"},
        {**_findings("It was 12.5 metres long.", "它长 12 米。")[0],
         "finding_id": "f-2"},
        {**_findings("<b>x</b>", "x")[0], "finding_id": "f-3"},
    ]
    findings[0]["segment_index"] = findings[0]["segment_id"] = 0
    findings[1]["segment_index"] = findings[1]["segment_id"] = 1
    findings[2]["segment_index"] = findings[2]["segment_id"] = 2
    groups = fg.group_findings(findings)
    numbers = next(group for group in groups if group["group_type"] == "number")
    assert numbers["token"] == "12.5" and numbers["occurrence_count"] == 2
    assert numbers["segments"] == [0, 1]
    assert {member["finding_id"] for member in numbers["members"]} == {"f-1", "f-2"}
    assert {member["decision"] for member in numbers["members"]} == {
        "request_revision", "open"}
    assert numbers["open_count"] == 1 or numbers["open_count"] == 2
    assert all(member["reason"] for member in numbers["members"])
    assert any(group["group_type"] == "tag" for group in groups)
    # Non-groupable findings never leak into a group.
    assert all(group["group_type"] in fg.GROUP_TYPES for group in groups)


def test_exceptions_keep_occurrences_unchanged_and_inspectable():
    findings = []
    for index, (src, tgt) in enumerate([
            ("The sample weighs 12.5 grams.", "样本重克。"),
            ("It was 12.5 metres long.", "它长 12 米。")]):
        item = _findings(src, tgt)[0]
        item["segment_index"] = item["segment_id"] = index
        findings.append(item)
    exceptions = [
        {"group_id": fg.group_id_for(findings[0]), "reason": "数字按版式改写"},
    ]
    groups = fg.group_findings(findings, exceptions=exceptions)
    number_group = next(group for group in groups if group["group_type"] == "number")
    assert number_group["excluded_count"] == 2
    assert number_group["members"] == []
    assert {item["segment_index"] for item in number_group["excluded"]} == {0, 1}
    annotated = fg.annotate_findings(findings, exceptions=exceptions)
    assert all(not item.get("finding_group_id") for item in annotated)

    # A single-occurrence exception excludes only that occurrence.
    exceptions = [{"group_type": "number", "token": "12.5", "segment_index": 0}]
    groups = fg.group_findings(findings, exceptions=exceptions)
    number_group = next(group for group in groups if group["group_type"] == "number")
    assert number_group["occurrence_count"] == 1
    assert number_group["excluded_count"] == 1


def test_grouping_can_be_disabled_without_touching_findings(temp_job_env):
    state = core.new_job_state("groups.docx")
    findings = _findings("The sample weighs 12.5 grams.", "样本重克。")
    findings[0]["segment_index"] = findings[0]["segment_id"] = 0
    state["findings"] = findings
    assert core.deterministic_finding_groups(state=state)

    os.environ[fg.ENV_SWITCH] = "0"
    try:
        assert core.deterministic_finding_groups(state=state) == []
        view = workbench_view.review_workbench_view(state)
        assert view["finding_groups"] == []
        assert state["findings"] == findings, "关闭分组不得改写发现项"
    finally:
        os.environ.pop(fg.ENV_SWITCH, None)


def test_preview_is_read_only_and_shows_current_text(temp_job_env):
    state = core.new_job_state("preview.docx")
    state["paras"] = ["The sample weighs 12.5 grams.", "It was 12.5 metres."]
    state["pairs"] = [
        {"source": "The sample weighs 12.5 grams.", "target": "样本重克。"},
        {"source": "It was 12.5 metres.", "target": "它长 12 米。"},
    ]
    findings = []
    for index, pair in enumerate(state["pairs"]):
        item = _findings(pair["source"], pair["target"])[0]
        item["segment_index"] = item["segment_id"] = index
        findings.append(item)
    state["findings"] = findings
    group = core.deterministic_finding_groups(state=state)[0]
    before = json.dumps(state["pairs"], ensure_ascii=False, sort_keys=True)
    preview = core.deterministic_finding_group_preview(
        "preview", group["group_id"], state=state)
    assert preview["would_change_text"] is False
    assert [row["segment_index"] for row in preview["rows"]] == [0, 1]
    assert json.dumps(state["pairs"], ensure_ascii=False, sort_keys=True) == before


def _make_number_fix_job(job_id, tmp_path):
    state = core.new_job_state("numbers.docx")
    state["paras"] = [
        "The sample weighs 12.5 grams.",
        "It was 12.5 metres long.",
    ]
    state["pairs"] = [
        {"source": "The sample weighs 12.5 grams.", "target": "样本重 12 克。"},
        {"source": "It was 12.5 metres long.", "target": "它长 12 米。"},
    ]
    state["target_lang"] = "简体中文"
    findings = []
    for index, pair in enumerate(state["pairs"]):
        for item in _findings(pair["source"], pair["target"]):
            if item.get("kind") == "number":
                item["segment_index"] = item["segment_id"] = index
                findings.append(item)
    state["findings"] = findings
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def test_number_fix_is_a_candidate_and_stale_candidate_cannot_commit(temp_job_env):
    job_id = "group-fix"
    state = _make_number_fix_job(job_id, temp_job_env)
    groups = core.deterministic_finding_groups(state=state)
    assert groups and groups[0]["group_type"] == "number"

    result = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    assert result["candidate"] is not None
    mid = core.load_job_state(job_id)
    assert mid["pairs"][0]["target"] == "样本重 12 克。", "候选不写正文"

    # Edit the segment after materialisation -> the baseline no longer matches.
    mid["pairs"][0]["target"] = "样本重 12 克，已复核。"
    core.save_job_state(job_id, mid)
    adopted = core.adopt_translation_candidate(
        job_id, result["candidate"]["candidate_id"], actor="tester")
    assert adopted.get("committed") is False
    final = core.load_job_state(job_id)
    assert final["pairs"][0]["target"] == "样本重 12 克，已复核。"

    # The other segment still resolves cleanly when adopted explicitly.
    other = core.load_job_state(job_id)
    other["groups_second"] = True
    core.save_job_state(job_id, other)
    groups = core.deterministic_finding_groups(state=core.load_job_state(job_id))
    second = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    if second["candidate"] is not None:
        ok = core.adopt_translation_candidate(
            job_id, second["candidate"]["candidate_id"], actor="tester")
        assert ok.get("committed") is True


def test_grouping_and_fix_build_add_no_model_call(monkeypatch, temp_job_env):
    def boom(*_args, **_kwargs):
        raise AssertionError("deterministic grouping must not call a model")

    monkeypatch.setattr(core, "call_llm", boom)
    job_id = "group-nomodel"
    state = _make_number_fix_job(job_id, temp_job_env)
    groups = core.deterministic_finding_groups(state=state)
    assert groups
    preview = core.deterministic_finding_group_preview(
        job_id, groups[0]["group_id"], state=state)
    assert preview["rows"]
    result = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    assert result["candidate"] is not None


def test_local_grouping_cost_is_bounded():
    findings = []
    for index in range(2000):
        findings.append({
            "type": "check", "severity": "actionable", "kind": "number",
            "group_type": "number", "group_token": f"12.{index % 7}",
            "detected_text": f"12.{index % 7}", "segment_index": index,
            "segment_id": index, "summary": "数字缺失",
            "reason": "数字缺失", "source_span": f"12.{index % 7}",
            "target_span": "",
        })
    started = time.perf_counter()
    groups = fg.group_findings(findings)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert len(groups) == 7
    assert sum(group["occurrence_count"] for group in groups) == 2000
    assert elapsed_ms < 500, f"grouping 2000 findings took {elapsed_ms:.1f} ms"


# ---------------------------------------------------------------------------
# is_no_translation_needed: suppression of whole-segment machine identifiers
# and pure numbers from the identical-to-source (translation_completion) check.
# ---------------------------------------------------------------------------

def _identical_findings(src, lang="简体中文"):
    """translation_completion findings for a segment whose target equals its source."""
    return [f for f in core.check_translation_batch([src], [src], [], lang)
            if f.get("category") == "translation_completion"]


# --- positive cases: each in-scope category must NOT fire ---

def test_bare_email_address_is_not_flagged():
    assert not _identical_findings("avaswani@google.com")
    assert not _identical_findings("user.name+tag@sub.example.co.uk")
    assert not _identical_findings("illia.polosukhin@gmail.com")


def test_bare_url_is_not_flagged():
    assert not _identical_findings("https://www.example.com/path?q=1")
    assert not _identical_findings("http://arxiv.org/abs/1706.03762")
    assert not _identical_findings("www.stmartins.com")


def test_bare_doi_is_not_flagged():
    assert not _identical_findings("10.1234/abc.efg-hij")
    assert not _identical_findings("10.18653/v1/2020.acl-main.100")


def test_isbn_issn_is_not_flagged():
    # ISBN-13 with Japanese NDC catalogue code
    assert not _identical_findings("ISBN978-4-7629-6638-5 C3022")
    assert not _identical_findings("ISBN978-4-8140-0137-8 C3322")
    assert not _identical_findings("ISSN 1234-567X")


def test_pure_ascii_number_is_not_flagged():
    assert not _identical_findings("2024")
    assert not _identical_findings("3.14")
    assert not _identical_findings("2024/01/01")
    assert not _identical_findings("1-100")
    assert not _identical_findings("12,345.67")


def test_table_row_of_pure_identifiers_is_not_flagged():
    # Pipe-delimited row whose every cell is in-scope
    assert not _identical_findings("user@a.com | 10.1234/x | 2024")
    # Tab-delimited: numbers only (also passes via _NO_TRANS_NUMBER on the joined string)
    assert not _identical_findings("2024\t3.14\t100")


def test_tab_delimited_url_row_is_not_flagged():
    """Tab-delimited row of URLs must be suppressed.

    After space-normalisation "https://a.com https://b.org" does NOT match the
    URL regex (two tokens joined by a space) so the row was incorrectly flagged
    before the dead-tab-branch fix.  The fix checks ``raw`` for '\\t' so the
    correct per-cell check is applied.
    """
    assert not _identical_findings("https://a.com\thttps://b.org")
    assert not _identical_findings("user@a.com\tuser@b.com")


# --- negative controls: these MUST still fire ---

def test_sentence_containing_url_still_fires():
    # A sentence that merely contains a URL is not suppressed.
    assert _identical_findings("Visit https://x.com now")
    assert _identical_findings("See http://example.org for details.")


def test_number_with_unit_word_still_fires():
    # Digits plus a unit abbreviation that is a Latin word trigger source_has_text.
    # "9.00 a.m." has two Latin words (a and m separated); fires as before.
    assert _identical_findings("9.00 a.m.")
    # A phrase with unit words clearly has text — not a bare number.
    assert _identical_findings("The result is 42 kg per hour")


def test_date_with_month_name_still_fires():
    # Month names are Latin words; a phrase with 2+ words triggers source_has_text.
    assert _identical_findings("January report 2024")
    assert _identical_findings("15 March deadline 2023")


def test_han_term_still_fires():
    # CJK text must keep firing.
    assert _identical_findings("太田秀通")
    assert _identical_findings("二 戦争と根こそぎ労働動員")


def test_index_line_still_fires():
    # May hide untranslated names; keep firing.
    assert _identical_findings("1. Introduction to the volume")


def test_latin_words_still_fire():
    # Runs of Latin words are out of scope.
    assert _identical_findings("Introduction to the volume")


def test_era_plus_number_still_fires():
    # Japanese eras contain CJK and are out of scope.
    assert _identical_findings("死亡月日 昭和二十年一月二十日")


def test_blocking_finding_is_never_suppressed():
    # A blocking completeness finding (empty target) must never be suppressed.
    # This verifies the fix only touches the translation_completion check.
    blocking = [f for f in core.check_translation_batch(
        ["some text"], [""], [], "简体中文")
                if f.get("severity") == "blocking"]
    assert blocking, "empty target must still produce a blocking finding"


# ---------------------------------------------------------------------------
# Task 2 — Han-script awareness for Simplified Chinese target
# ---------------------------------------------------------------------------

def _han_findings(src, lang="简体中文"):
    """translation_completion findings for src==tgt."""
    return [f for f in core.check_translation_batch([src], [src], [], lang)
            if f.get("category") == "translation_completion"]


def _has_kind(findings, kind):
    return any(f.get("kind") == kind for f in findings)


# --- branch (a): Traditional/JP chars → actionable, kind="han_not_simplified" ---

def test_traditional_char_causes_han_not_simplified():
    # 賢 (U+8CE2) is Traditional Chinese; not in GB2312, only in Shift-JIS.
    findings = _han_findings("大里健賢")
    assert findings, "should fire"
    assert _has_kind(findings, "han_not_simplified"), f"expected han_not_simplified, got {findings}"
    assert any(f.get("severity") == "actionable" for f in findings)


def test_jp_shinjitai_causes_han_not_simplified():
    # 嶋 (U+5D8B) is Shift-JIS-only and not in _EXCEPTIONS; must trigger.
    findings = _han_findings("小嶋茂稔")
    assert findings
    assert _has_kind(findings, "han_not_simplified")


def test_mixed_jp_trad_chars_all_named():
    # 鍋 (U+934B) and 輝 (U+8F1D) are both Shift-JIS-only; both must be named.
    findings = _han_findings("尾鍋輝彦")
    assert findings
    assert _has_kind(findings, "han_not_simplified")
    # Both 鍋 and 輝 should appear in the reason / detail text.
    combined = " ".join(f.get("reason", "") + f.get("detail", "") for f in findings)
    assert "鍋" in combined or "锅" in combined
    assert "輝" in combined or "辉" in combined


def test_jp_shinjitai_with_浜():
    # 浜 (U+6D5C) is in _FORCED_TRUE (GB2312-compatible but Traditional/JP form).
    findings = _han_findings("浜田省吾")
    assert findings
    assert _has_kind(findings, "han_not_simplified")


# --- branch (b): short, unchanged → informational ---

def test_short_unchanged_han_is_informational():
    # 谷角常子: all chars are already valid Simplified/shared; 4 chars → short.
    findings = _han_findings("谷角常子")
    assert findings, "must still appear"
    assert any(f.get("severity") == "informational" for f in findings), (
        f"expected informational, got {findings}")
    assert not _has_kind(findings, "han_not_simplified")


def test_渡口重国_is_informational():
    findings = _han_findings("渡口重国")
    assert findings
    assert any(f.get("severity") == "informational" for f in findings)


def test_short_all_simplified_chars_is_informational():
    # Short segment whose chars are all valid Simplified/shared — no conversion
    # needed — should produce an informational finding, not actionable.
    # ("手紙" alone is only 2 chars so source_has_text is False; use 4+ chars.)
    findings = _han_findings("天地人和德")  # 5 chars, all Simplified/shared, no conversion
    assert findings, "must still appear"
    assert any(f.get("severity") == "informational" for f in findings)
    assert not _has_kind(findings, "han_not_simplified")


# --- branch (c): long and unchanged → actionable (no "han_not_simplified") ---

def test_long_unchanged_han_stays_actionable():
    # 篇第三章 is 4 chars (short → informational in isolation), but we can
    # construct a longer segment to verify the long-path stays actionable.
    # Use a segment of > _SHORT_HAN_THRESHOLD chars that do not need conversion.
    long_seg = "山川大地风云变幻，一切皆为过眼云烟"  # 16 chars, all Simplified
    findings = _han_findings(long_seg)
    assert findings
    assert any(f.get("severity") == "actionable" for f in findings)
    assert not _has_kind(findings, "han_not_simplified")


# --- source has kana → falls through to original behaviour ---

def test_source_with_kana_does_not_enter_han_check():
    # Kana disqualifies the Han check; falls through to standard actionable.
    findings = _han_findings("太田さん")
    assert findings
    # Should be standard actionable, not han_not_simplified
    assert any(f.get("severity") == "actionable" for f in findings)
    assert not _has_kind(findings, "han_not_simplified")


def test_source_with_latin_does_not_enter_han_check():
    # Latin in source → standard path (not han_not_simplified).
    # Need enough words/letters to clear source_has_text first.
    findings = _han_findings("Introduction 第一章 overview guide")
    assert findings
    assert not _has_kind(findings, "han_not_simplified")


def test_jp_corner_brackets_bypass_han_check():
    # 「」marks Japanese prose → standard path (not han_not_simplified).
    # Need enough Han chars (≥4) to clear source_has_text.
    findings = _han_findings("「第一章」天下大地万事可追")
    assert findings
    assert not _has_kind(findings, "han_not_simplified")


# --- non-Simplified-Chinese target → original behaviour ---

def test_non_simplified_target_stays_actionable():
    # For Traditional Chinese target, standard actionable (no Han check).
    findings = _han_findings("大里健賢", lang="繁体中文")
    assert findings
    assert any(f.get("severity") == "actionable" for f in findings)
    assert not _has_kind(findings, "han_not_simplified")


def test_english_target_never_produces_han_not_simplified():
    # English target is exempt from the identical-to-source check
    # (target_lang != "English" gate), so han_not_simplified must not fire.
    # (Other checks such as script_leak may still fire for Han chars in an
    # English target — that is intended behaviour.)
    findings = _han_findings("大里健賢", lang="English")
    assert not _has_kind(findings, "han_not_simplified")


# --- preserve entry suppresses the whole check ---

def test_preserve_entry_suppresses_han_check():
    glossary = [{"source": "大里健賢", "behavior": "preserve", "status": "locked"}]
    findings = [f for f in core.check_translation_batch(
        ["大里健賢"], ["大里健賢"], glossary, "简体中文")
                if f.get("category") == "translation_completion"]
    assert not findings, "preserve entry must suppress the check"


# --- GB2312 filter: false-alarm chars removed, allowlist chars kept -----------

def test_gb2312_false_alarm_chars_do_not_trigger():
    """GB2312-valid chars (沪疏醋欠予芸) are legitimate Simplified Chinese;
    they must not cause a han_not_simplified finding even when src == tgt."""
    # Need ≥4 Han letters to clear source_has_text.  Pad each char with
    # neutral Simplified Chinese chars (人地方年 are all shared/Simplified).
    for c in ('沪', '疏', '醋', '欠', '予', '芸'):
        src = c + "人地方"   # 4 chars; all Simplified
        findings = _han_findings(src)
        assert not _has_kind(findings, "han_not_simplified"), (
            f"{c!r} (U+{ord(c):04X}) is a valid GB2312 Simplified char and must "
            f"not trigger han_not_simplified (got {findings})"
        )


def test_gb2312_allowlist_chars_still_trigger():
    """後 is in GB2312 for compatibility but is a Traditional form; 浜 is a
    JP shinjitai; both must still trigger han_not_simplified."""
    # 後藤武雄: 後 (U+5F8C) → 后; 4 chars clears source_has_text.
    findings_go = _han_findings("後藤武雄")
    assert _has_kind(findings_go, "han_not_simplified"), (
        "後 is in GB2312 allowlist; should still trigger han_not_simplified"
    )
    assert any("後" in f.get("reason", "") for f in findings_go), \
        "reason should name 後"

    # 浜田省吾: existing test covers 浜; verify here too for the allowlist invariant.
    findings_hama = _han_findings("浜田省吾")
    assert _has_kind(findings_hama, "han_not_simplified"), \
        "浜 is in GB2312 allowlist; should still trigger han_not_simplified"


def test_codec_rule_must_trigger_list():
    """Codec rule must trigger for the coordinator's must-trigger list:
    鍋 輝 賢 嶋 瀬 論 増 淵 龍 啓 浜 後."""
    from folith.engine.glossary_checks import _non_simplified_chars
    must_trigger = ['鍋', '輝', '賢', '嶋', '瀬', '論', '増', '淵', '龍', '啓', '浜', '後']
    for c in must_trigger:
        result = _non_simplified_chars(c)
        assert result, (
            f"{c!r} (U+{ord(c):04X}) must trigger but _non_simplified_chars returned empty"
        )


def test_codec_rule_must_not_trigger_list():
    """Codec rule must NOT trigger for the coordinator's must-not-trigger list:
    沪 疏 醋 欠 予 芸 豨 磾 卬 蚡 镕 喆."""
    from folith.engine.glossary_checks import _non_simplified_chars
    must_not_trigger = ['沪', '疏', '醋', '欠', '予', '芸', '豨', '磾', '卬', '蚡', '镕', '喆']
    for c in must_not_trigger:
        result = _non_simplified_chars(c)
        assert not result, (
            f"{c!r} (U+{ord(c):04X}) must NOT trigger but "
            f"_non_simplified_chars returned {result!r}"
        )


# --- page-number length fix --------------------------------------------------

def test_index_line_with_long_page_list_is_informational():
    """An index entry like '杉村伸二 15, 26-30' should be informational
    (content is 4 chars) not actionable (which the page-number suffix would
    cause without the _content_len_for_threshold fix)."""
    # Without the fix: len("杉村伸二 15, 26-30") > _SHORT_HAN_THRESHOLD → actionable.
    # With the fix: content_len = len("杉村伸二") = 4 ≤ 10 → informational.
    findings = _han_findings("杉村伸二 15, 26-30")
    assert findings, "index line should fire"
    assert any(f.get("severity") == "informational" for f in findings), (
        f"index line with page numbers should be informational; got {findings}"
    )


def test_long_name_without_page_numbers_stays_actionable():
    """A name longer than the threshold without a page list stays actionable."""
    # "山田太郎次郎三郎四郎" = 10 chars, no conversion needed, no page list.
    # _content_len_for_threshold = 10 = threshold → boundary: 10 ≤ 10 → informational.
    # But "山田太郎次郎三郎四郎五郎" = 12 → actionable.
    long_name = "山田太郎次郎三郎四郎五郎"  # 12 Simplified/shared chars
    findings = _han_findings(long_name)
    assert findings
    assert any(f.get("severity") == "actionable" for f in findings), (
        f"long unchanged Han segment without page numbers must stay actionable; got {findings}"
    )
