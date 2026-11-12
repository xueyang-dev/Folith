"""F7 / F10 的契约：控件可点性靠"有没有面"，次要文字按 WCAG AA 达标。

2026-11-04 UI 审查（`docs/ui-overhaul/01-master-plan.md`）：

* **F7** 工具按钮用 `bg-surface-sunken` 压在页面上，两颗灰几乎同色，整排读成"已禁用"；
  而禁用按钮反而有实底，比可用按钮更重。约定是：工具按钮 = 控件底 `control-bg` +
  发丝边 `border-hairline`；**禁用态去掉填充**（只剩灰字与极淡轮廓）。
* **F10** 次要文字对比度 2.98–3.62，未达 4.5:1。`text-muted` / `text-secondary`
  必须在**所有真实表面**上 >= 4.5（侧栏那层 shell 渐变最深处是 `bg-shell-end`）。

对比度口径与 `tmp/overhaul-tokens/contrast_audit.py` 相同：带 alpha 的 token 先叠到
背景上，再按 WCAG 2.x 相对亮度算。
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

from folith.qt import style, theme  # noqa: E402
from folith.ui import theme_tokens  # noqa: E402

#: 文字真正会落在的表面：侧栏（shell 渐变两端 + 玻璃底）、页面、卡片、沉底、
#: 浮起面、选中行（半透明 accent 渍，先叠到页面底上再算）。
SURFACES = ("bg-shell-start", "bg-shell-end", "bg-glass", "bg-page", "bg-sidebar",
            "bg-surface", "bg-surface-subtle", "bg-surface-sunken", "bg-surface-raised",
            "bg-selected")
MINIMUM = 4.5


def _rgba(value: str) -> tuple[tuple[float, float, float], float]:
    text = str(value).strip()
    if text.lower().startswith(("rgba(", "rgb(")):
        parts = [part.strip() for part in text.partition("(")[2].rstrip(")").split(",")]
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        if alpha > 1:
            alpha /= 255
        return (float(parts[0]), float(parts[1]), float(parts[2])), alpha
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    return tuple(float(int(text[index:index + 2], 16)) for index in (0, 2, 4)), 1.0


def _over(fore, back):
    (red, green, blue), alpha = _rgba(fore)
    (bred, bgreen, bblue), _ = _rgba(back)
    return (red * alpha + bred * (1 - alpha),
            green * alpha + bgreen * (1 - alpha),
            blue * alpha + bblue * (1 - alpha))


def _luminance(rgb) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _surface(name: str, name_of_theme: str) -> str:
    value = theme.tokens(name_of_theme)[name]
    if _rgba(value)[1] < 1.0:                  # 半透明的面：先叠到页面底
        red, green, blue = _over(value, theme.tokens(name_of_theme)["bg-page"])
        return "#%02x%02x%02x" % (round(red), round(green), round(blue))
    return value


def contrast(token: str, surface: str, name_of_theme: str) -> float:
    tokens = theme.tokens(name_of_theme)
    first = _luminance(_over(tokens[token], _surface(surface, name_of_theme)))
    second = _luminance(_rgba(_surface(surface, name_of_theme))[0])
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("name", theme.THEME_ORDER)
@pytest.mark.parametrize("token", ("text-secondary", "text-muted"))
def test_secondary_text_reaches_wcag_aa_on_every_surface(name, token):
    failures = {surface: round(contrast(token, surface, name), 2)
                for surface in SURFACES if contrast(token, surface, name) < MINIMUM}
    assert not failures, failures


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_muted_stays_lighter_than_secondary(name):
    """F10 的另一种结论是"两档合并"；这里选择保留层级，那么 muted 必须仍比重一些的
    secondary 更轻——否则两个 token 就是同一档，不如合并。"""
    for surface in SURFACES:
        muted = contrast("text-muted", surface, name)
        secondary = contrast("text-secondary", surface, name)
        assert muted < secondary, (name, surface, muted, secondary)


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_tool_buttons_use_the_control_surface_not_the_sunken_track(name):
    """F7：工具按钮是"浮起的一小块面"（control-bg + border-hairline），不是沉底槽。"""
    qss = style.build_qss(name)
    tokens = theme.tokens(name)
    bodies = re.findall(r"QToolButton#toolbarButton\s*\{([^}]*)\}", qss)
    assert bodies, "toolbarButton has no rule"
    body = bodies[0]
    assert tokens["control-bg"] in body, body
    assert tokens["border-hairline"] in body, body
    assert tokens["bg-surface-sunken"] not in body, body


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_disabled_controls_have_no_fill(name):
    """F7：禁用态去掉填充。有填充 = 有一块面 = 读成可点；
    禁用按钮比可用按钮更重，正是审查里被读反的那一处。"""
    qss = style.build_qss(name)
    tokens = theme.tokens(name)
    for selector in (r"QPushButton:disabled", r'QPushButton\[kind="primary"\]:disabled',
                     r'QPushButton\[kind="danger"\]:disabled',
                     r"QToolButton#toolbarButton:disabled"):
        bodies = re.findall(selector + r"\s*\{([^}]*)\}", qss)
        assert bodies, selector
        for body in bodies:
            assert "background: transparent" in body, (selector, body)
            assert tokens["bg-surface-sunken"] not in body, (selector, body)


def test_every_theme_declares_the_same_tokens():
    """F10 改的是取值，不是键集合：两套主题的键必须仍然完全一致。"""
    sets = {frozenset(values) for values in theme_tokens.TOKENS.values()}
    assert len(sets) == 1
