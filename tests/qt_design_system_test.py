"""设计系统层的用例：主题化图标、组件库、首页的新结构。

钉住的是**行为**而不是像素：

* 图标只携带 token，主题切换后同一个 ``QIcon`` 再画出来的颜色必须变（不需要重建）；
* 禁用态用 ``disabled-ink``，可勾选按钮选中（``QIcon.State.On``）用 ``color_on``；
* 三套主题的 token 键集一致，新增的 ``info`` / 渐变 token 都在；
* 首页：状态筛选只影响列表、不影响指标；侧栏「最近项目」与项目筛选双向同步。
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

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, components, icons, style, theme, views  # noqa: E402
from folith.ui import theme_tokens  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    style.apply_theme(instance, "light")


def _close_top_level_widgets(app) -> None:
    """Drop this test's Qt widgets before the next theme application.

    A full run accumulates Qt top-level widgets from every earlier test; the
    next `setStyleSheet` then repolishes all of them on Windows and can take
    tens of minutes. Test-only widgets must not outlive a single test.
    """
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    for widget in list(QApplication.topLevelWidgets()):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(autouse=True)
def _drop_top_level_widgets(app):
    _close_top_level_widgets(app)
    yield
    _close_top_level_widgets(app)


def _centre_rgb(pixmap) -> tuple[int, int, int]:
    image = pixmap.toImage()
    # 取一个必然落在"plus"竖线上的像素：24 网格的 (12, 12) 在中心。
    colour = image.pixelColor(image.width() // 2, image.height() // 2)
    return colour.red(), colour.green(), colour.blue()


def test_icon_recolours_when_the_theme_changes(app):
    style.apply_theme(app, "light")
    icon = icons.icon("plus", 24, "text-primary")
    light = _centre_rgb(icon.pixmap(QSize(48, 48)))
    style.apply_theme(app, "dark")
    dark = _centre_rgb(icon.pixmap(QSize(48, 48)))
    assert light != dark
    expected = theme.qcolor(theme.tokens("dark")["text-primary"])
    assert dark[:3] == (expected.red(), expected.green(), expected.blue())


def test_disabled_icons_use_the_disabled_ink_token(app):
    style.apply_theme(app, "light")
    icon = icons.icon("plus", 24, "text-primary")
    disabled = _centre_rgb(icon.pixmap(QSize(48, 48), QIcon.Mode.Disabled))
    wanted = theme.qcolor(theme.tokens("light")["disabled-ink"])
    assert disabled == (wanted.red(), wanted.green(), wanted.blue())


def test_checked_state_uses_color_on(app):
    style.apply_theme(app, "light")
    icon = icons.icon("plus", 24, "text-secondary", color_on="accent")
    off = _centre_rgb(icon.pixmap(QSize(48, 48), QIcon.Mode.Normal, QIcon.State.Off))
    on = _centre_rgb(icon.pixmap(QSize(48, 48), QIcon.Mode.Normal, QIcon.State.On))
    accent = theme.qcolor(theme.tokens("light")["accent"])
    assert off != on
    assert on == (accent.red(), accent.green(), accent.blue())


def test_literal_colours_stay_fixed(app):
    style.apply_theme(app, "light")
    icon = icons.icon("plus", 24, "#ff0000")
    style.apply_theme(app, "dark")
    assert _centre_rgb(icon.pixmap(QSize(48, 48))) == (255, 0, 0)


def test_every_icon_draws_something(app):
    for name in icons.names():
        pixmap = icons.pixmap(name, 32, "text-primary")
        image = pixmap.toImage()
        opaque = sum(1 for x in range(image.width()) for y in range(image.height())
                     if image.pixelColor(x, y).alpha() > 0)
        assert opaque > 0, name


def test_duotone_adds_a_wash_to_closed_shapes(app):
    style.apply_theme(app, "light")
    plain = icons.pixmap("file-text", 48, "accent").toImage()
    washed = icons.pixmap("file-text", 48, "accent", duotone=True).toImage()
    # (24, 24) 在页面内部、不在描边上：单色图标这里是透明的，双色图标有底色。
    assert plain.pixelColor(20, 14).alpha() == 0
    assert washed.pixelColor(20, 14).alpha() > 0


def test_theme_token_sets_match_and_include_the_new_roles():
    keys = {name: set(values) for name, values in theme_tokens.TOKENS.items()}
    assert len({frozenset(value) for value in keys.values()}) == 1
    for name in ("info", "info-soft", "bg-gradient-start", "bg-gradient-end"):
        assert name in keys["light"]
    # 界面主色与 logo 分开（2026-10-16 色彩审校）：logo 仍是钴蓝，界面是同色相的低彩度蓝。
    assert theme_tokens.TOKENS["light"]["accent"] == "#2b5fd1"
    assert theme_tokens.TOKENS["light"]["brand-logo-blue"] == "#004cfd"


def _job(job_id, name, project_id=None, *, status="待处理", active=False,
         snapshot=None, error=""):
    return adapter.JobRow(
        job_id=job_id, filename=name, project_id=project_id, target_lang="简体中文",
        status_label=status, progress_label="", runtime_status="idle",
        segment_count=3, updated_at="2026-10-05T19:40:00", active=active,
        snapshot_version=snapshot, error=error)


ROWS = [
    _job("a", "a.docx", "p1", active=True),
    _job("b", "b.docx", "p1", snapshot=1),
    _job("c", "c.docx", "p2", error="boom"),
    _job("d", "d.docx", "p2"),
]
PROJECTS = [
    {"project_id": "p1", "name": "Alpha"},
    {"project_id": "p2", "name": "Beta"},
    {"project_id": "", "name": "未分类", "is_system": True},
]


@pytest.fixture()
def home(app):
    style.apply_theme(app, "light")
    view = views.HomeView()
    view.resize(1280, 800)
    view.set_projects(PROJECTS)
    view.set_rows(ROWS)
    yield view
    view.deleteLater()


def test_status_chip_filters_the_list_but_not_the_metrics(home):
    assert home.table.rowCount() == 4
    home.status_filter.set_value("errored")
    home.status_filter.changed.emit("errored")
    assert home.table.rowCount() == 1
    # 指标仍按"项目 + 搜索"的范围统计
    assert home.stats.value("total") == "4"
    # 概览是四格（总数 / 运行中 / 待处理问题 / 已交付）；出错的任务数写在「已交付」下面一行。
    assert "1" in home.stats.cell("delivered").hint.text()
    home.status_filter.changed.emit("active")
    assert home.table.rowCount() == 1
    home.status_filter.changed.emit("all")
    assert home.table.rowCount() == 4


def test_sidebar_projects_follow_and_drive_the_project_filter(home):
    buttons = home._project_nav_buttons
    assert list(buttons) == ["p1", "p2"]            # 系统项目（未分类）不进快捷入口
    assert home.project_nav_label.isHidden() is False
    buttons["p2"].click()
    assert home.project_filter.currentData() == "p2"
    assert buttons["p2"].isChecked() and not buttons["p1"].isChecked()
    assert home.table.rowCount() == 2
    buttons["p2"].click()                            # 再点一次 = 取消筛选
    assert home.project_filter.currentData() == ""
    assert not buttons["p2"].isChecked()
    home.project_filter.setCurrentIndex(home.project_filter.findData("p1"))
    assert buttons["p1"].isChecked()


def test_project_tabs_replace_the_dropdown(home):
    """任务列表上方是一排项目标签（全部 / 每个项目 + 任务数），下拉框只是数据模型。"""
    tabs = home.project_tabs
    assert not home.project_tabs_row.isHidden() and home.project_filter.isHidden()
    assert home.list_card.isAncestorOf(tabs), "tabs sit inside the list card, under its header"
    assert list(tabs.tabs) == ["", "p1", "p2"], "空着的系统项目不占标签"
    assert [tab.count for tab in tabs.tabs.values()] == [4, 2, 2]
    assert tabs.tabs["p1"].text() == "Alpha"
    tabs.tabs["p2"].click()
    assert home.project_filter.currentData() == "p2"
    assert home.table.rowCount() == 2
    assert home._project_nav_buttons["p2"].isChecked(), "sidebar follows the tabs"
    home._project_nav_buttons["p1"].click()
    assert tabs.value() == "p1" and tabs.tabs["p1"].isChecked()
    tabs.tabs[""].click()
    assert home.project_filter.currentData() == "" and home.table.rowCount() == 4
    # 颜色按项目 id 固定：刷新、换顺序都不变。
    assert components.project_dot_token("p1") == components.project_dot_token("p1")
    assert components.project_dot_token("") == "text-muted"


def test_project_tabs_hide_without_user_projects(app):
    view = views.HomeView()
    view.set_projects([{"project_id": "sys", "name": "未分类", "is_system": True}])
    view.set_rows(ROWS)
    assert view.project_tabs_row.isHidden()
    view.deleteLater()


def test_nav_item_is_themed_and_checkable(app):
    button = components.nav_item("任务库", "projects", checkable=True)
    assert button.isCheckable() and button.objectName() == "navItem"
    assert not button.icon().isNull()


def test_metric_cards_keep_the_stat_strip_api(app):
    cards = components.MetricCards([("x", "X", "projects", "accent")])
    cards.set_value("x", 7, emphasis="danger")
    assert cards.value("x") == "7"
    assert cards.cell("x") is not None
    assert cards.value("missing") == ""


def test_search_bar_hint_hides_while_typing(app):
    bar = components.SearchBar("搜索")
    bar.show()
    assert not bar.hint.isHidden()
    bar.setText("abc")
    assert bar.hint.isHidden()
    bar.clear()
    assert not bar.hint.isHidden()


def test_sidebar_logo_is_large_enough_to_read(home):
    assert home.LOGO_HEIGHT >= 48
    assert "Folith" in home._logo_label.text()
    assert "智能体翻译工作台" in home._logo_label.text()
    assert "Agentic Translation Workspace" not in home._logo_label.text()
    assert not home._brand_mark.pixmap().isNull()


def test_filter_chips_use_the_pill_skin(app):
    chips = components.FilterChips([("a", "A"), ("b", "B")])
    assert chips.objectName() == "pillTrack"
    assert chips.button("a").objectName() == "pill"
    # 原分段控件的皮肤不受影响
    plain = style.SegmentedControl([("a", "A")])
    assert plain.objectName() == "segmentTrack"
    assert plain.button("a").objectName() == "segment"


# ---- 第四套主题（翠绿）与 logo 着色 -------------------------------------------------
def test_green_theme_is_a_complete_light_theme(app):
    assert "green" in theme_tokens.THEME_ORDER
    assert theme_tokens.SCHEMES["green"] == "light"
    tokens = theme_tokens.TOKENS["green"]
    assert tokens["accent"] == "#0f6b4f"            # 设计稿 Primary Green
    assert tokens["accent-soft"] == "#e6f4ee"       # Green Light
    assert set(tokens) == set(theme_tokens.TOKENS["light"])
    assert theme.label("green") == "翠绿"
    assert theme.resolve("green") == "green"
    assert style.apply_theme(app, "green") == "green"
    assert not style.night_mode()


def _hue(pixmap) -> float:
    image = pixmap.toImage()
    total, count = 0.0, 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 200 and colour.hsvSaturationF() > 0.6 and colour.valueF() > 0.6:
                total += colour.hsvHueF()
                count += 1
    return total / max(count, 1)


def test_logo_follows_the_theme_accent(app):
    from folith.qt import branding

    original = branding.logo_pixmap(48, "light")
    day = branding.logo_pixmap(48, "light", tint=theme.tokens("light")["brand-logo-blue"])
    green = branding.logo_pixmap(48, "green", tint=theme.tokens("green")["accent"])
    # 日间：logo 保持品牌钴蓝（界面主色已与 logo 分开，侧栏日间不着色），图片逐像素不变
    assert day.toImage() == original.toImage()
    # 翠绿：彩色部分的色相移到绿区（约 0.37–0.45），不再是蓝（≈0.6）
    assert 0.3 < _hue(green) < 0.5 < _hue(original)


def test_home_sidebar_logo_is_tinted_per_theme(app):
    style.apply_theme(app, "light")
    light_view = views.HomeView()
    light_hue = _hue(light_view._brand_mark.pixmap())
    style.apply_theme(app, "green")
    green_view = views.HomeView()
    assert _hue(green_view._brand_mark.pixmap()) < light_hue - 0.1
    style.apply_theme(app, "light")
    light_view.deleteLater()
    green_view.deleteLater()


def test_green_logo_uses_the_designed_colour_ramp(app):
    from folith.qt import branding

    logo = branding.logo_pixmap(104, "green", tint=theme.tokens("green")["accent"])
    image = logo.toImage()
    colours = {(c.red(), c.green(), c.blue())
               for c in (image.pixelColor(x, y) for x in range(0, image.width(), 2)
                         for y in range(0, image.height(), 2))
               if c.alpha() == 255 and c.hsvSaturationF() > 0.3}
    # 彩色部分既有深翠绿（后卡片 / 字标）也有薄荷色（前卡片），且全部落在绿色区间
    assert any(g < 0x60 and g > r and g >= b for r, g, b in colours)
    assert any(g > 0xb0 and g > r and g >= b for r, g, b in colours)
    assert all(g >= r and g >= b - 8 for r, g, b in colours)


def test_day_sidebar_logo_is_the_untouched_asset(app):
    """日间、蓝色主题色：侧栏标记就是品牌向量图本身，按屏幕像素比渲染。"""
    from folith.qt import branding

    style.apply_theme(app, "light")
    view = views.HomeView()
    shown = view._brand_mark.pixmap()
    dpr = max(1.0, view.devicePixelRatioF())
    original = branding.mark_pixmap(28, "blue", dpr)
    assert shown.devicePixelRatio() == original.devicePixelRatio()
    assert shown.toImage() == original.toImage()
    view.deleteLater()


def test_accent_switch_redraws_the_mark_from_its_own_svg_and_back(app):
    """换主题色：标记换成该色的 SVG，尺寸与清晰度不变；换回来与最初逐像素相同。"""
    from folith.qt import branding

    style.apply_theme(app, "light")
    view = views.HomeView()
    first = view._brand_mark.pixmap().toImage()
    size = view._brand_mark.pixmap().deviceIndependentSize()
    try:
        for accent in ("green", "slate", "blue"):
            theme.set_accent(accent)
            style.apply_theme(app, "light")
            app.processEvents()
            view._restyle_rich_text()
            shown = view._brand_mark.pixmap()
            assert shown.deviceIndependentSize() == size, accent
            expected = branding.mark_pixmap(28, branding.mark_variant("light", accent),
                                            max(1.0, view.devicePixelRatioF()))
            assert shown.toImage() == expected.toImage(), accent
        assert view._brand_mark.pixmap().toImage() == first
    finally:
        theme.set_accent("blue")
        style.apply_theme(app, "light")
        view.deleteLater()
