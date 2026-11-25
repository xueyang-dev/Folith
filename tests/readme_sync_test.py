"""The README translations must keep the same structure as the maintained Chinese one."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = "README.zh-CN.md"
TRANSLATIONS = ("README.md", "README.ru.md", "README.es.md")
SWITCHER = ("README.md", "README.ru.md", "README.es.md")

#: 译本 README 的界面语言 → 截图文件名的语言后缀。中文是基准语言，文件名没有后缀。
LANGUAGE_SUFFIX = {"README.md": ".en", "README.ru.md": ".ru", "README.es.md": ".es"}


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _h2(text: str) -> list[str]:
    # Section icons are shared across translations and identify their order.
    return re.findall(r"^## (\S+)", text, flags=re.M)


def _images(text: str) -> list[str]:
    return [p for p in re.findall(r'<img src="([^"]+)"', text) if not p.startswith("http")]


def _local_links(text: str) -> set[str]:
    links = re.findall(r"\]\(([^)#]+)\)", text)
    return {p for p in links if not p.startswith(("http", "mailto:")) and not p.startswith("README")}


def _screenshots(text: str) -> set[str]:
    """界面截图（流程图除外），去掉语言后缀后的文件名集合。"""
    return {p for p in _images(text) if "workflow" not in p}


def _qt_screenshots(text: str) -> set[str]:
    """Qt 客户端的界面截图（``docs/assets/qt-*.png``）。"""
    return {p for p in _images(text) if p.startswith("docs/assets/qt-")}


def _strip_suffix(path: str) -> str:
    """``qt-01-tasks.en.png`` → ``qt-01-tasks.png``（去掉语言后缀，便于和中文那批比对）。"""
    return re.sub(r"\.(en|ru|es)\.png$", ".png", path)


def test_translations_match_the_chinese_structure():
    main = _read(MAIN)
    for name in TRANSLATIONS:
        text = _read(name)
        assert _h2(text) == _h2(main), f"{name}: section order differs from {MAIN}"
        assert len(_images(text)) == len(_images(main)), f"{name}: local image count differs"
        assert {_strip_suffix(p) for p in _screenshots(text)} == \
            _screenshots(main), f"{name}: screenshots differ"
        assert _local_links(text) == _local_links(main), f"{name}: doc links differ"


def test_readmes_describe_smart_qa_and_original_format_boundaries():
    # These are user-visible limitations, not just matching section counts.
    markers = {
        "README.md": ("Smart QA triage is experimental and off by default", "off by default"),
        "README.zh-CN.md": ("智能 QA 分诊为实验功能，默认关闭", "默认关闭"),
        "README.ru.md": ("триаж QA — экспериментальная функция, по умолчанию выключена", "по умолчанию выключен"),
        "README.es.md": ("triaje inteligente de QA es experimental y está desactivado por defecto", "desactivado por defecto"),
    }
    for name, (qa, default) in markers.items():
        text = _read(name)
        assert qa in text, f"{name}: missing Smart QA limitation"
        delivery_rows = [line for line in text.splitlines() if line.startswith("|") and "`original_format.docx`" in line]
        assert len(delivery_rows) == 1 and default in delivery_rows[0], f"{name}: missing optional DOCX delivery"


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


def test_translations_use_their_own_screenshots():
    """每个译本都必须指向自己语言的截图，不能又指回中文那批。"""
    for name, suffix in LANGUAGE_SUFFIX.items():
        text = _read(name)
        shots = _qt_screenshots(text)
        assert shots, f"{name}: no screenshots found"
        for path in shots:
            assert path.endswith(f"{suffix}.png"), \
                f"{name}: {path} is not a {suffix} screenshot"
            assert (ROOT / path).exists(), f"{name}: {path} does not exist"
