"""List numbers a model puts in front of translations must not reach the target text."""
from folith.translation_protocol import strip_echoed_numbering


def test_a_continued_list_number_is_removed_when_the_source_has_none():
    sources = ["「現地調査報告」とあるのは、各地の", "その二 十九春、二十秋山東作戦"]
    targets = ["1. 所谓“现地调查报告”，是指各地的", "2. 其二 十九春、二十秋山东作战"]
    assert strip_echoed_numbering(sources, targets) == ["所谓“现地调查报告”，是指各地的", "其二 十九春、二十秋山东作战"]


def test_a_number_in_front_of_the_sources_own_enumerator_is_removed():
    assert strip_echoed_numbering(["一、「慰霊実行委員会」と略記したもの"],
                                  ["2. 一、文中将“慰灵实行委员会”作为简称的"]) == ["一、文中将“慰灵实行委员会”作为简称的"]


def test_a_number_the_source_really_has_is_kept():
    sources = ["1. First item", "(2) Second item", "一、本书的内容"]
    targets = ["1. 第一项", "(2) 第二项", "1. 本书的内容"]          # 一、→1. is a real conversion
    assert strip_echoed_numbering(sources, targets) == targets


def test_a_translation_that_is_only_a_number_is_left_alone():
    assert strip_echoed_numbering(["注"], ["1. "]) == ["1. "]


def test_a_number_in_front_of_a_bracketed_enumerator_is_removed():
    assert strip_echoed_numbering(["（二） 宿舎施設"], ["3. （二）宿舍设施"]) == ["（二）宿舍设施"]
