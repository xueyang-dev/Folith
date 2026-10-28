"""Eastern Arabic digits in a translation become the target's numerals."""
import pytest

import core
from folith import numerals


@pytest.mark.parametrize("text, expected", [
    # The cases from the trial: a phone line and footnote markers.
    ("电话：٢٣٣٣٩٨（5条线路）— 传真：٢٦٧٥٦٧٤", "电话：233398（5条线路）— 传真：2675674"),
    ("(١) 晨食：早晨所吃的东西。", "(1) 晨食：早晨所吃的东西。"),
    ("大海鲢(١)对水茉莉说……更不用说(٢)爬了！", "大海鲢(1)对水茉莉说……更不用说(2)爬了！"),
    # Persian digits and the Arabic decimal / thousands separators.
    ("共 ۱۲۳ 页", "共 123 页"),
    ("约 ٣٫٥ 公里，人口 ١٢٬٣٤٥", "约 3.5 公里，人口 12,345"),
    # Nothing to do.
    ("第 12 页", "第 12 页"),
    ("", ""),
])
def test_digits_are_converted_for_chinese(text, expected):
    assert numerals.normalize_digits(text, "简体中文") == expected


def test_arabic_text_quoted_in_the_translation_keeps_its_digits():
    text = "书名保留原文：سورة ٢ 与 الجزء ٣（第 ٤ 卷）"
    assert numerals.normalize_digits(text, "English") == \
        "书名保留原文：سورة ٢ 与 الجزء ٣（第 4 卷）"


@pytest.mark.parametrize("target", ["العربية", "Arabic", "阿拉伯语", "فارسی", "Urdu"])
def test_targets_that_use_eastern_digits_are_untouched(target):
    assert numerals.normalize_digits("(١) ٢٣٣٣٩٨", target) == "(١) ٢٣٣٣٩٨"


def test_translate_batch_converts_fresh_and_cached_results():
    def call(*args, **kwargs):
        return '["电话：٢٣٣٣٩٨", "(١) 晨食"]'

    targets = core.translate_batch(["تليفون: ٢٣٣٣٩٨", "(١) الصبوح"], "", "", "", "",
                                   "简体中文", "p", "k", "m", call_llm_fn=call)
    assert targets == ["电话：233398", "(1) 晨食"]

    class Cache:
        def get(self, key):
            return ["(١) 晨食"]

        def put(self, key, value):
            raise AssertionError("a cache hit is not written back")

    cached = core.translate_batch(["(١) الصبوح"], "", "", "", "", "简体中文",
                                  "p", "k", "m", call_llm_fn=call, result_cache=Cache())
    assert cached == ["(1) 晨食"]


def test_translator_prompt_states_the_digit_rule():
    prompt = core._translator_system("", "", "简体中文")
    assert "(١) 写作 (1)" in prompt


# An Arabic source with a footnote marker, a page number, a citation and a URL
# whose path ends in an Eastern Arabic digit.
_MIXED_SOURCE = "انظر الحاشية (١) وص ١٢ والمرجع [٣] و http://x.org/٤"


@pytest.mark.parametrize("text", [
    "见 http://x.org/٤ 与 www.x.org/a٥",
    "写信到 user٢@x.org",
    "DOI 10.1234/abc٦",
])
def test_identifier_digits_are_never_converted(text):
    assert numerals.normalize_digits(text, "简体中文") == text


@pytest.mark.parametrize("target, model_output", [
    ("简体中文", "见注释 (١) 和第 ١٢ 页及参考文献 [٣] 与 http://x.org/٤"),
    ("English", "See note (١), p. ١٢, reference [٣] and http://x.org/٤"),
])
def test_converted_citations_and_kept_urls_pass_the_check(target, model_output):
    translation = numerals.normalize_digits(model_output, target)
    assert "[3]" in translation and "http://x.org/٤" in translation
    findings = core.check_translation_batch([_MIXED_SOURCE], [translation], [], target)
    assert [f for f in findings if f["category"] == "format_integrity"] == []


def test_a_changed_url_or_a_missing_citation_is_still_reported():
    findings = core.check_translation_batch(
        [_MIXED_SOURCE], ["见注释 (1) 和第 12 页及参考文献 与 http://x.org/4"], [], "简体中文")
    reasons = {f["reason"] for f in findings if f["category"] == "format_integrity"}
    assert reasons == {"保留项 citation「[٣]」在译文中丢失",
                       "保留项 url「http://x.org/٤」在译文中丢失"}


def test_arabic_targets_still_compare_citations_exactly():
    translation = "انظر المرجع [٣]"
    assert numerals.normalize_digits(translation, "العربية") == translation
    assert core.check_translation_batch(["See [٣]"], [translation], [], "العربية") == []
    findings = core.check_translation_batch(["See [٣]"], ["انظر المرجع [3]"], [], "العربية")
    assert [f["reason"] for f in findings] == ["保留项 citation「[٣]」在译文中丢失"]


def test_translator_prompt_no_longer_freezes_citation_digits():
    prompt = core._translator_system("", "", "简体中文")
    assert "[٣] 写作 [3]" in prompt
    assert "引用标注（如 [12]）和其他机器标识符" not in prompt


def _unfinished(source, target, lang="简体中文"):
    return [f for f in core.check_translation_batch([source], [target], [], lang)
            if f.get("category") == "translation_completion"]


def test_source_copied_back_is_flagged_for_any_source_script():
    # Not tied to one language pair: kana, Cyrillic, Arabic and Latin all count.
    for source in ("二 戦争と根こそぎ労働動員", "Предисловие автора", "مقدمة المؤلف",
                   "Introduction to the volume"):
        assert _unfinished(source, source), source
    # Lone Latin words (names), digits and dot leaders alone are not text.
    assert not _unfinished("Tokyo", "Tokyo")
    assert not _unfinished("..........  7", "..........  7")
    # A real translation is never flagged.
    assert not _unfinished("二 戦争と根こそぎ労働動員", "二 战争与彻底的劳动动员")


def _leaks(source, target, lang="简体中文"):
    return [f for f in core.check_translation_batch([source], [target], [], lang)
            if f.get("kind") == "script_leak"]


def test_script_left_over_from_the_source_is_flagged_by_writing_system():
    # kana in a Chinese translation of Japanese: unfinished, however short the line is
    assert _leaks("その二 全員蜂起", "その二 全员起义")
    assert _leaks("説教されたものだ。", "説教されたものだ。")
    # the same rule for other pairs, no pair is hard-coded
    assert _leaks("Предисловие автора", "Author's Предисловие", "English")
    assert _leaks("مقدمة المؤلف", "مقدمة المؤلف", "Español")
    assert _leaks("전쟁과 노동", "战争与노동", "简体中文")


def test_script_check_leaves_legitimate_text_alone():
    # kanji shared with Chinese, Latin names and abbreviations in a non-Latin target
    assert not _leaks("加藤恭平の証言", "加藤恭平的证词")
    assert not _leaks("二 戦争と労働動員", "二 战争与劳动动员 (WWII)")
    # an unknown target language is not guessed at; a source without that script cannot leak it
    assert not _leaks("その二 全員蜂起", "その二 全员起义", "Klingon")
    assert not _leaks("Hello world", "你好 world 世界")


def test_script_leak_is_covered_by_the_existing_auto_repair_severity():
    finding = _leaks("その二 全員蜂起", "その二 全员起义")[0]
    assert finding["severity"] == "actionable"      # actionable findings go to the repair pass
