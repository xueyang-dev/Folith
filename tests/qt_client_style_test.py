"""Qt 样式层：品牌 token 落地、三套主题、状态芯片。

这一层的作用是让 Qt 客户端与旧界面共用同一份视觉真值，所以测试的重点是
"颜色只能来自 token"、"三套主题都能编译"、"切换真的生效"，
而不是断言某个像素长什么样。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.ui import theme_tokens  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_every_theme_compiles_to_stylesheet(app):
    """三套主题都必须能编译出样式表，且颜色确实来自该主题的 token。"""
    from folith.qt import style, theme

    for name in theme.theme_names():
        qss = style.build_qss(name)
        assert qss.strip(), name
        assert "{" in qss and "}" in qss
        tokens = theme.tokens(name)
        assert tokens["accent"] in qss, f"{name}: accent token missing from QSS"
        assert tokens["bg-page"] in qss
        # No format placeholder may survive: that would silently drop rules.
        assert "{{" not in qss and "}}" not in qss
        # A stylesheet with unbalanced braces is rejected by Qt wholesale.
        assert qss.count("{") == qss.count("}")


def test_tokens_are_the_single_source_of_truth(app):
    """Qt 侧不定义颜色：同一主题名的取值必须与 theme_tokens 完全一致。"""
    from folith.qt import theme

    for name in theme.theme_names():
        assert theme.tokens(name) == dict(theme_tokens.TOKENS[name])
    assert theme.theme_names() == tuple(theme_tokens.THEME_ORDER)


def test_apply_theme_and_follow_system(app):
    from folith.qt import style, theme

    for name in theme.theme_names():
        assert style.apply_theme(app, name) == name
        assert theme.tokens(name)["accent"] in app.styleSheet()

    # "跟随系统" resolves from the reported system appearance.
    assert theme.resolve(theme.FOLLOW_SYSTEM, system_dark=True) == "dark"
    assert theme.resolve(theme.FOLLOW_SYSTEM, system_dark=False) == "light"
    assert theme.resolve("paper") == "paper"
    assert theme.resolve("nonsense") == "light"


def test_chip_tones_map_to_tokens(app):
    from folith.qt import style, theme

    chip = style.Chip("已冻结交付", "ok")
    assert chip.property("tone") == "ok"
    chip.set_tone("danger")
    assert chip.property("tone") == "danger"
    chip.set_tone("not-a-tone")
    assert chip.property("tone") == "muted"
    # Every tone used by the UI has a rule in the stylesheet.
    qss = style.build_qss("light")
    for tone in style.CHIP_TONES:
        assert f'QLabel[tone="{tone}"]' in qss, tone
    assert theme.tokens("light")["success"] in qss


def test_brand_lockup_uses_the_documented_night_variant():
    """brand.md 的资产矩阵要求深色底用夜间字标，否则字标在深色上看不见。"""
    from folith.qt import branding

    dark_path = branding.asset_path(branding.LOGO_BY_THEME["dark"])
    light_path = branding.asset_path(branding.LOGO_BY_THEME["light"])
    assert dark_path.is_file() and light_path.is_file()
    assert dark_path.name.endswith("dark.png")
    assert branding.LOGO_BY_THEME["light"] == branding.LOGO_FALLBACKS[0]

    dark = branding.logo_pixmap(24, "dark")
    light = branding.logo_pixmap(24, "light")
    assert not dark.isNull() and not light.isNull()
    assert dark.toImage() != light.toImage(), \
        "the night lockup must differ from the day lockup"


def _button_background(button) -> str:
    """按钮左侧边缘的像素色（避开文字与圆角中心）。"""
    image = button.grab().toImage()
    return image.pixelColor(6, image.height() // 2).name()


def test_mark_primary_applies_the_theme_accent(app):
    """构造之后再标主行动的按钮必须真的变主色（需要重新 polish）。

    QSS 的对象名选择器只在 polish 时解析；`QDialogButtonBox` 造出来的按钮在
    `setObjectName` 之前已经被 polish 过，不重新 polish 就仍然是默认外观——
    这正是设置对话框"保存"按钮曾经看起来不像主按钮的原因。
    """
    from PySide6.QtWidgets import QDialogButtonBox

    from folith.qt import style, theme

    style.apply_theme(app, "light")
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save)
    box.show()
    app.processEvents()
    button = box.button(QDialogButtonBox.StandardButton.Save)

    button.setObjectName("primaryAction")          # 只设名字：不生效
    app.processEvents()
    plain = _button_background(button)

    style.mark_primary(button)                     # 设名字 + 重新 polish
    app.processEvents()
    marked = _button_background(button)

    accent = theme.tokens("light")["accent"]
    assert plain != accent, "这个用例的前提失效了：仅设名字就已经变色"
    assert marked.lower() == accent.lower(), (marked, accent)
    box.close()


def test_settings_save_button_renders_as_the_primary_action(app):
    """设置对话框的"保存"必须是主行动按钮（不是默认按钮外观）。"""
    from PySide6.QtWidgets import QDialogButtonBox

    from folith.qt import adapter, style, theme
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    for name in theme.theme_names():
        style.apply_theme(app, name)
        dialog = EngineDialog(adapter.EngineConfig(), TaskRunner())
        dialog.show()
        app.processEvents()
        box = dialog.findChild(QDialogButtonBox)
        save = box.button(QDialogButtonBox.StandardButton.Save)
        assert save.objectName() == "primaryAction"
        assert _button_background(save).lower() == \
            theme.tokens(name)["accent"].lower(), name
        dialog.close()


def test_theme_previews_use_token_colours(app):
    """外观页的三张色卡必须来自 token（不写死颜色）。"""
    from folith.qt import adapter, style, theme
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    style.apply_theme(app, "light")
    dialog = EngineDialog(adapter.EngineConfig(), TaskRunner())
    previews = dialog._build_theme_previews()
    styles = [child.styleSheet() for child in previews.findChildren(type(previews))]
    blob = " ".join(styles) + " " + previews.styleSheet()
    for name in theme.theme_names():
        tokens = theme.tokens(name)
        assert tokens["accent"] in blob, name
        assert tokens["bg-surface"] in blob, name
    dialog.close()
