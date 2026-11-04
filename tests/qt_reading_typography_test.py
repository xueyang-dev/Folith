"""F6 的契约：阅读排版（行距倍率 + 阅读字体设置）。

2026-11-04 UI 审查：阅读区英文行距 ≈1.2、中文 ≈1.4（都是字体自带的行距），
两栏基线不齐，拉丁那栏偏紧，而这是用户盯几个小时的地方。约定：

* 行距是**规则**不是口味：拉丁 1.5、中文 1.7（``theme.READING_LINE_HEIGHT``）；
* 阅读字体是**可选设置**，默认跟随界面字体——不写死任何平台的字体名；
* 选了族就把它放在链首，后面必须仍接着中文 / 阿拉伯文回退族（缺字不是"排版不好看"，
  是一排方框）；
* 行距通过 ``QTextDocument`` 的块格式落地（QSS 没有 line-height）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

from folith.qt import preferences, style, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.apply_theme(instance, "light")
    yield instance


@pytest.fixture()
def reading_font_restored():
    previous = theme.reading_font()
    yield
    theme.set_reading_font(previous)


def reading_rule(theme_name: str = "light") -> str:
    """QSS 里阅读区那条规则的规则体。"""
    bodies = re.findall(
        r"QTableWidget#segmentGrid, QPlainTextEdit#inlineSource, "
        r"QPlainTextEdit#targetEdit\s*\{([^}]*)\}", style.build_qss(theme_name))
    assert bodies, "the reading rule is missing"
    return bodies[0]


def test_line_height_is_the_documented_pair():
    assert theme.READING_LINE_HEIGHT == {"latin": 1.5, "cjk": 1.7}


def test_line_height_follows_the_script():
    assert style.reading_line_height("Long documents fail in translation") == 1.5
    assert style.reading_line_height("Записка о переводе") == 1.5      # 西里尔
    assert style.reading_line_height("ملاحظة عن الترجمة") == 1.5        # 阿拉伯
    assert style.reading_line_height("长文档的翻译") == 1.7
    assert style.reading_line_height("漢字かなカナ한글") == 1.7
    assert style.reading_line_height("") == 1.5


def _one_line_height(document: QTextDocument) -> float:
    """文档里一行文字占多高（单行文本的文档高度就是一行的行高）。"""
    document.setTextWidth(100000)
    return document.size().height()


@pytest.mark.parametrize("ratio", (1.5, 1.7))
def test_the_document_gets_an_explicit_line_height(app, ratio):
    document = QTextDocument()
    font = style.font_for("light")
    font.setPixelSize(theme.FONT_SIZES["reading"])
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    document.setPlainText("A term rendered one way in chapter two.")
    style.set_reading_line_height(document, ratio)
    from PySide6.QtGui import QTextBlockFormat

    block = document.begin().blockFormat()
    assert block.lineHeightType() == int(
        QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
    # 倍率是相对**字号**的：1.5 × 14px = 21px（不是相对字体天然行距）。
    assert block.lineHeight() == pytest.approx(theme.FONT_SIZES["reading"] * ratio)
    assert _one_line_height(document) == pytest.approx(
        theme.FONT_SIZES["reading"] * ratio, abs=0.6)


def test_cjk_and_latin_lines_keep_their_own_ratios_in_the_same_font(app):
    """中文走回退字体，天然行距比拉丁大；显式行距要按同一个"相对字号"口径给。"""
    font = style.font_for("light")
    font.setPixelSize(theme.FONT_SIZES["reading"])
    measured = {}
    for name, text in (("latin", "A term rendered one way."), ("cjk", "同一个术语在第二章。")):
        document = QTextDocument()
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        document.setPlainText(text)
        ratio = style.reading_line_height(text)
        style.set_reading_line_height(document, ratio)
        measured[name] = _one_line_height(document) / theme.FONT_SIZES["reading"]
    assert measured["latin"] == pytest.approx(1.5, abs=0.05)
    assert measured["cjk"] == pytest.approx(1.7, abs=0.05)


def test_following_the_ui_font_writes_no_family_at_all(app, reading_font_restored):
    """默认 = 跟随界面字体：QSS 里不出现 font-family，也就不会写死任何平台字体。"""
    theme.set_reading_font("")
    rule = reading_rule()
    assert "font-family" not in rule
    assert "font-size: 14px" in rule
    for platform_face in ("SF Pro Text", "Segoe UI", "PingFang SC"):
        assert platform_face not in rule


def test_a_chosen_family_keeps_the_cjk_and_arabic_fallbacks(app, reading_font_restored):
    theme.set_reading_font("Georgia")
    rule = reading_rule()
    assert "'Georgia'" in rule
    # 缺中文字形的西文字体：中文与阿拉伯文回退必须是链上的下一站。
    assert "PingFang SC" in rule or "Noto Sans SC" in rule
    assert "'Geeza Pro'" in rule or "Noto Sans Arabic" in rule
    # Windows 与 macOS 的界面字体都在链上 → 不是"只对某个平台好看"的排版。
    assert "Segoe UI" in rule and "SF Pro Text" in rule


def test_preference_round_trip_do_not_touch_the_real_store(app, monkeypatch, tmp_path):
    from folith.qt import style as style_module

    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(preferences, "settings", lambda: store)
    monkeypatch.setattr(style_module, "_READING_PREFERENCE_LOADED", False)
    style.save_reading_font("Charter")
    assert store.value(theme.READING_FONT_KEY) == "Charter"
    assert style.load_reading_font() == "Charter"

    # 空串 = 跟随界面字体：偏好里不留键，重启后回到系统字体。
    style.save_reading_font("")
    assert store.value(theme.READING_FONT_KEY) is None
    theme.set_reading_font("reload-me")
    monkeypatch.setattr(style_module, "_READING_PREFERENCE_LOADED", False)
    assert style.load_reading_font() == ""


def test_reading_widgets_pick_the_family_up_from_the_stylesheet(app, reading_font_restored):
    theme.set_reading_font("")
    style.apply_reading_font(app)
    plain = QTableWidget()
    plain.setObjectName("segmentGrid")
    plain.ensurePolished()
    default_family = plain.font().family()

    theme.set_reading_font("Georgia")
    style.apply_reading_font(app)
    chosen = QTableWidget()
    chosen.setObjectName("segmentGrid")
    chosen.ensurePolished()
    assert chosen.font().family() == "Georgia"
    assert chosen.font().pixelSize() == theme.FONT_SIZES["reading"]
    assert default_family != "Georgia"


def test_reading_text_height_uses_the_line_metrics(app):
    """行高测量与绘制同口径：1.5 × 14px = 21、1.7 × 14px ≈ 23.8 → 24（向上取整）。"""
    font = style.font_for("light")
    font.setPixelSize(theme.FONT_SIZES["reading"])
    assert style.reading_text_height("A term.", font, 640) == 21
    assert style.reading_text_height("同一个术语。", font, 640) == 24
    wide = style.reading_text_height("A term rendered one way in chapter two.", font, 400)
    narrow = style.reading_text_height("A term rendered one way in chapter two.", font, 120)
    assert narrow > wide, (narrow, wide)
