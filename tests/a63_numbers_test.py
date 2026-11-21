"""A-63: Unicode decimal digits still denote paragraph numbers."""
from __future__ import annotations

from folith.engine import segments


def _state():
    return {'pairs': [
        {'source': '第一段', 'target': 'one'},
        {'source': '第二段', 'target': 'two'},
        {'source': '含²上标', 'target': 'three'},
    ]}


def test_unicode_decimal_digits_select_second_segment():
    for query in ('2', '#2', '٢', '#٢', '۲', '２'):
        assert segments.translation_visible_indexes(_state(), search=query) == [1], query


def test_superscript_is_still_text():
    assert segments.translation_visible_indexes(_state(), search='²') == [2]
