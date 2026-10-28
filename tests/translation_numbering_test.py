"""Numbers inside translated text are content, not transport markers."""
import core
from folith.translation_protocol import parse_translation_response


def test_structured_targets_keep_numbered_text_even_if_source_is_not_numbered():
    def call(*args, **kwargs):
        return '["1. First result", "2. Second result"]'

    result = core.translate_batch(["第一项", "第二项"], "", "", "", "",
                                  "English", "p", "k", "m", call_llm_fn=call)
    assert result == ["1. First result", "2. Second result"]


def test_cached_targets_keep_numbered_text():
    class Cache:
        def get(self, key):
            return ["1. First result"]

    result = core.translate_batch(["第一项"], "", "", "", "", "English",
                                  "p", "k", "m", result_cache=Cache())
    assert result == ["1. First result"]


def test_single_plain_numbered_line_is_ambiguous_and_kept():
    assert parse_translation_response("1. First result", 1) == ["1. First result"]


def test_numbered_response_sequence_still_parses_outer_numbers():
    assert parse_translation_response("1. First result\n2. Second result", 2) == [
        "First result", "Second result"]


def test_prompt_distinguishes_input_indexes_from_source_numbering():
    prompt = core._translator_system("", "", "English")
    assert "输入段落前的序号仅用于对齐" in prompt
    assert "原文本身的编号和数字须保留" in prompt
