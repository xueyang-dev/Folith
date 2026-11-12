"""D2 (#220): source-script leftovers inside an otherwise translated segment.

The check is described by writing system, never by language pair: a script the
target language is not written in, left over from the source, means the segment
is unfinished.  Two existing repository mechanisms supply the facts:

* script identity comes from the character's Unicode name (no range table here,
  so a script that was never listed before is still recognised);
* the target language's scripts come from ``folith.ocr_language``, the same
  registry the rest of the product resolves languages through; an unknown target
  language is not checked rather than guessed at.

Quoted / bracketed originals count as deliberately kept and are not reported.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from folith import script_leak  # noqa: E402
from folith.ocr_language import OCR_LANGUAGE_REGISTRY  # noqa: E402


def test_script_identity_comes_from_unicode_not_a_range_table():
    expected = {
        "a": "latin", "漢": "han", "あ": "kana", "ア": "kana", "ｱ": "kana",
        "한": "hangul", "Ж": "cyrillic", "م": "arabic", "א": "hebrew",
        "अ": "devanagari", "ก": "thai", "Ա": "armenian", "ა": "georgian",
        "অ": "bengali", "அ": "tamil", "ㄅ": "bopomofo",
    }
    for character, script in expected.items():
        assert script_leak.script_of(character) == script, character
    # A style prefix does not hide the script; a lone ideograph is still Han.
    assert script_leak.script_of("Ａ") == "latin"      # FULLWIDTH LATIN CAPITAL A
    assert script_leak.script_of("々") is None         # IDEOGRAPHIC ITERATION MARK
    # Digits, punctuation and private-use code points are not letters.
    for character in ("1", "。", "・", "—", " "):
        assert script_leak.script_of(character) is None, character


def test_every_registry_language_has_its_scripts_declared():
    """Adding a language to the registry without its scripts must fail loudly."""
    assert OCR_LANGUAGE_REGISTRY
    for key, entry in OCR_LANGUAGE_REGISTRY.items():
        allowed = script_leak.allowed_scripts(entry["label"])
        assert allowed is not None, f"{key} 的书写系统未声明"
        assert script_leak.allowed_scripts(key) == allowed, key
        # Latin is allowed in every target (names, brands, abbreviations).
        assert "latin" in allowed, key


def test_labels_aliases_and_codes_resolve_to_one_script_set():
    for label in ("English", "english", "en", "英语", "英文", "eng"):
        assert script_leak.allowed_scripts(label) == script_leak.allowed_scripts(
            "English"), label
    assert script_leak.allowed_scripts("ja") == script_leak.allowed_scripts("日本語")
    assert script_leak.allowed_scripts("zh-TW") == script_leak.allowed_scripts("繁體中文")
    assert script_leak.allowed_scripts("العربية") == script_leak.allowed_scripts("ar")


def test_partial_leaks_are_detected_by_writing_system():
    # The complaint in #229: a segment that is translated except for one source
    # fragment must be caught, however short it is.
    assert script_leak.leaked_scripts(
        "その二 全員蜂起", "その二 全员起义", "简体中文") == {"kana": 2}
    assert script_leak.leaked_scripts(
        "作者的话", "作者的话 Author's note", "English") == {"han": 4}
    assert script_leak.leaked_scripts(
        "전쟁과 노동", "战争与노동", "简体中文") == {"hangul": 2}
    assert script_leak.leaked_scripts(
        "Предисловие", "Preface Предисловие", "English") == {"cyrillic": 11}
    # A script no range table ever listed before is still recognised.
    assert script_leak.leaked_scripts(
        "Բարեւ աշխարհ", "你好 Բարեւ աշխարհ", "简体中文") == {"armenian": 11}
    # A finished translation has nothing to report.
    assert script_leak.leaked_scripts(
        "二 戦争と根こそぎ労働動員", "二 战争与彻底的劳动动员", "简体中文") == {}


def test_shared_scripts_and_translated_terms_are_not_false_positives():
    # Chinese and Japanese share Han; a translated sentence keeps no foreign script.
    assert script_leak.leaked_scripts(
        "加藤恭平の証言", "加藤恭平的证词", "简体中文") == {}
    # Latin names, brands and abbreviations stay in a non-Latin target.
    assert script_leak.leaked_scripts(
        "作者的话", "作者的话 (Claude 5.5, WWDC)", "简体中文") == {}
    # A script the target language itself uses is never a leftover.
    assert script_leak.leaked_scripts("作者的话", "著者の言葉", "日本語") == {}
    # A script the source never had cannot be left over from the source.
    assert script_leak.leaked_scripts("Hello world", "你好 world 世界", "简体中文") == {}


def test_quoted_originals_are_treated_as_deliberately_kept():
    # A quoted title is kept on purpose: no finding.
    assert script_leak.leaked_scripts(
        "東京物語の記録", "这是「東京物語」的翻译", "简体中文") == {}
    assert script_leak.leaked_scripts(
        "東京物語", "（東京物語）", "简体中文") == {}
    # Only the letters outside quoting count, so a real leftover is still found.
    assert script_leak.leaked_scripts(
        "その二 全員蜂起", "その二 是「その二」", "简体中文") == {"kana": 2}
    # An unbalanced double quote must not swallow the rest of the segment.
    assert script_leak.leaked_scripts(
        "その二", '他说 " 然后 その二', "简体中文") == {"kana": 2}


def test_an_unknown_target_language_is_not_checked():
    assert script_leak.allowed_scripts("Klingon") is None
    assert script_leak.allowed_scripts("") is None
    assert script_leak.leaked_scripts("その二", "その二", "Klingon") == {}


def test_partial_leak_reaches_the_existing_actionable_repair_path():
    """The finding keeps the shape the deterministic repair pass already selects.

    The repair pass takes ``blocking`` / ``actionable`` findings only; a
    copy-through leftover therefore needs no second mechanism.  Whether that path
    is safe to keep for these findings is argued in
    ``docs/issue-220-remaining-0.8.2.md``.
    """
    import core

    findings = core.check_translation_batch(
        ["その二 全員蜂起"], ["その二 全员起义"], [], "简体中文")
    leaks = [finding for finding in findings if finding.get("kind") == "script_leak"]
    assert len(leaks) == 1
    assert leaks[0]["severity"] == "actionable"
    assert leaks[0]["category"] == "translation_completion"
    assert "kana 2 个字" in leaks[0]["reason"]

    # The same rule inside the real pipeline: a quoted-only leftover stays quiet.
    quoted = core.check_translation_batch(
        ["東京物語の記録"], ["这是「東京物語」的翻译"], [], "简体中文")
    assert not [item for item in quoted if item.get("kind") == "script_leak"]
