"""CJK delivery must render and remain searchable without system fonts."""
import sys
from pathlib import Path
from functools import partial

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from folith.pdfrender import typeset
from original_pdf_renderer_test import _pdf_with_lines, _state, _render


@pytest.fixture()
def no_system_fonts(monkeypatch):
    monkeypatch.setattr(typeset, "FONT_DIRS", ())
    monkeypatch.setattr(typeset.typst, "compile", partial(
        typeset.typst.compile, ignore_system_fonts=True))
    monkeypatch.setattr(typeset.typst, "query", partial(
        typeset.typst.query, ignore_system_fonts=True))
    caches = [typeset.available_font_files, typeset.font_index,
              typeset._measure_cached, typeset._compile_cached]
    for fn in caches:
        fn.cache_clear()
    yield
    for fn in caches:
        fn.cache_clear()


def test_cjk_font_covers_every_character_without_system_fonts(no_system_fonts):
    choice = typeset.choose_fonts("敏捷的棕色狐狸跳过了懒狗。")
    assert not choice.missing
    assert "Droid Sans Fallback" in choice.families


def test_cjk_delivery_has_real_glyphs_and_a_complete_text_layer(no_system_fonts):
    target = "敏捷的棕色狐狸跳过了懒狗。"
    source = _pdf_with_lines(["The quick brown fox jumps over the lazy dog."])
    result = _render(source, _state(source, [(
        "The quick brown fox jumps over the lazy dog.", target)]))
    with pymupdf.open(stream=result.data, filetype="pdf") as doc:
        assert target in doc[0].get_text()
        assert doc[0].search_for("敏捷的棕色狐狸")
        glyphs = [char for span in doc[0].get_texttrace() for char in span["chars"]]
        assert glyphs and all(char[1] != 0 for char in glyphs)
    assert result.report["totals"]["missing_glyph_slots"] == 0
