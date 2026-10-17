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
