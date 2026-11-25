"""P15: locale sorting, order check and conflict merge."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_adds_distinct_new_keys():
    merge = _load('merge_locale_conflict').merge
    text = ('<<<<<<< ours\n'
            '{"a": "A", "b": "B", "x": "X"}\n'
            '=======\n'
            '{"a": "A", "b": "B", "y": "Y"}\n'
            '>>>>>>> theirs\n')
    assert merge(text) == {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y'}


def test_merge_refuses_same_key_conflict():
    merge = _load('merge_locale_conflict').merge
    text = ('<<<<<<< ours\n'
            '{"a": "ours"}\n'
            '=======\n'
            '{"a": "theirs"}\n'
            '>>>>>>> theirs\n')
    with pytest.raises(ValueError, match='conflicting value'):
        merge(text)


def test_merge_refuses_unparsable():
    merge = _load('merge_locale_conflict').merge
    with pytest.raises(ValueError, match='no conflict markers'):
        merge('{"a": "A"}\n')


def test_checker_passes_on_repo_catalogs():
    check = _load('check_locale_order').check
    assert check() == []
