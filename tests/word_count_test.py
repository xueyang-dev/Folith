"""字数统计：计数规则与按状态分项。"""
from __future__ import annotations

from dataclasses import dataclass

from folith import word_count


def test_counting_rules_follow_cat_practice():
    assert word_count.count_text("When Nora Seed finds herself.") == {"words": 5, "chars": 25}
    # 中文、日文按字；标点不算词。
    assert word_count.count_text("诺拉发现了图书馆。")["words"] == 8
    assert word_count.count_text("日本語のテキスト")["words"] == 8
    # 撇号、连字符、小数点连着的算一个词。
    assert word_count.count_text("don't e-mail 3.14")["words"] == 3
    assert word_count.count_text("مرحبا بالعالم")["words"] == 2
    assert word_count.count_text("Привет, мир!")["words"] == 2
    assert word_count.count_text("   ") == {"words": 0, "chars": 0}
    # 中英混排：英文按词、中文按字。
    assert word_count.count_text("使用 Midnight Library 的书")["words"] == 2 + 2 + 2


@dataclass
class Row:
    index: int
    source: str
    target: str = ""
    reviewed: bool = False
    human_edited: bool = False
    excluded: bool = False


def test_document_count_buckets():
    rows = [Row(0, "One two three.", "一二三。", reviewed=True),
            Row(1, "Four five.", ""),
            Row(2, "One  two three.", "一二三。", human_edited=True),
            Row(3, "Skipped words here.", "", excluded=True)]
    count = word_count.document_count(rows)
    assert count.total.segments == 3 and count.total.words == 8
    assert count.translated.words == 6 and count.untranslated.words == 2
    assert count.reviewed.words == 3 and count.human_edited.words == 3
    assert count.repetitions.segments == 1          # 第 3 段与第 1 段原文相同（忽略空白）
    assert count.excluded.words == 3                 # 排除的单独计，不进总数
    assert count.percent(count.translated) == 75
    assert count.target_words == 6
    selection = word_count.selection_count(rows[:2])
    assert selection == {"segments": 2, "source_words": 5, "source_chars": 21,
                         "target_words": 3, "target_chars": 4}
