"""The README translations must keep the same structure as the maintained Chinese one."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = "README.md"
TRANSLATIONS = ("README.en.md", "README.ru.md", "README.es.md")
SWITCHER = ("README.en.md", "README.ru.md", "README.es.md")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _h2(text: str) -> list[str]:
    return re.findall(r"^## ", text, flags=re.M)


def _images(text: str) -> list[str]:
    return [p for p in re.findall(r'<img src="([^"]+)"', text) if not p.startswith("http")]


def _local_links(text: str) -> set[str]:
    links = re.findall(r"\]\(([^)#]+)\)", text)
    return {p for p in links if not p.startswith(("http", "mailto:")) and not p.startswith("README")}


def test_translations_match_the_chinese_structure():
    main = _read(MAIN)
    for name in TRANSLATIONS:
        text = _read(name)
        assert len(_h2(text)) == len(_h2(main)), f"{name}: section count differs from {MAIN}"
        assert text.count("<img ") == main.count("<img "), f"{name}: image count differs"
        assert {p for p in _images(text) if "workflow" not in p} == \
            {p for p in _images(main) if "workflow" not in p}, f"{name}: screenshots differ"
        assert _local_links(text) == _local_links(main), f"{name}: doc links differ"


def test_every_readme_has_the_language_switcher():
    for name in (MAIN, *TRANSLATIONS):
        text = _read(name)
        for target in SWITCHER + (MAIN,):
            if target != name:
                assert f'href="{target}"' in text, f"{name}: missing link to {target}"


def test_readme_local_paths_exist():
    for name in (MAIN, *TRANSLATIONS):
        text = _read(name)
        paths = set(_images(text)) | _local_links(text)
        for path in paths:
            assert (ROOT / path).exists(), f"{name}: {path} does not exist"
