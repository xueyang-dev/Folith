"""A-57 PR-2: superscript digits are text, not paragraph numbers."""
from __future__ import annotations

from folith.engine import segments


def _state():
    return {'pairs': [
        {'source': '普通文本', 'target': 'plain'},
        {'source': '含²上标', 'target': 'superscript'},
        {'source': '第三段', 'target': 'third'},
    ]}


def test_hash_number_selects_paragraph() -> None:
    assert segments.translation_visible_indexes(_state(), search='#2') == [1]


def test_superscript_digit_is_text_not_a_number() -> None:
    state = _state()
    result = segments.translation_visible_indexes(state, search='²')
    assert result == [1]
    # '²'.isdigit() would raise ValueError on int(); no exception here.


def test_empty_query_returns_all() -> None:
    assert segments.translation_visible_indexes(_state(), search='') == [0, 1, 2]


def test_combined_filter() -> None:
    state = _state()
    state['pairs'][1]['reviewed'] = False
    state['pairs'][2]['reviewed'] = True
    assert segments.translation_visible_indexes(
        state, search='²', status_filter='待审') == [1]
