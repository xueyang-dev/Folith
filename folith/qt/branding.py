"""Brand assets for the Qt client (icon, wordmark lockup).

Assets are looked up through :func:`folith.desktop.paths.resource_path`, so the
same code finds them in the source tree and inside a frozen bundle.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from folith.desktop import paths as desktop_paths

APP_ICON = "folith/resources/brand/folith-app-icon.png"
# `docs/brand.md` §1 asset matrix: the horizontal lockup has a night variant that
# differs only in the wordmark colour, and it is the one meant for a dark
# surface.  Using the day lockup on a dark background makes the wordmark vanish.
LOGO_BY_THEME = {
    "dark": "folith/resources/brand/folith-lockup-transparent-dark.png",
    "light": "folith/resources/brand/folith-lockup-transparent.png",
    "paper": "folith/resources/brand/folith-lockup-transparent.png",
}
LOGO_FALLBACKS = ("folith/resources/brand/folith-lockup-transparent.png",
                  "folith/resources/brand/folith-logo-stacked.png")
#: 只有图形、不含文字的标记。侧栏/页头里和文字 lockup 并排使用——
#: 直接把整幅字标（自带品牌名）和文字放一起会出现"Folith 出现两次"的重复感。
MARK_BY_THEME = {
    "dark": "folith/resources/brand/_mono-on-dark.png",
    "light": "folith/resources/brand/_mono-on-light.png",
    "paper": "folith/resources/brand/_mono-on-light.png",
}
MARK_FALLBACKS = ("folith/resources/brand/foliothread-favicon.png",
                  "folith/resources/brand/foliothread-logo.png")


def asset_path(relative: str) -> Path:
    return desktop_paths.resource_path(*relative.split("/"))


@lru_cache(maxsize=4)
def _icon(relative: str):
    from PySide6.QtGui import QIcon

    path = asset_path(relative)
    return QIcon(str(path)) if path.is_file() else QIcon()


def app_icon():
    """The application/window icon, or an empty icon when assets are absent."""
    return _icon(APP_ICON)


@lru_cache(maxsize=8)
def mark_pixmap(height: int = 28, theme: str = "light"):
    """只有图形的品牌标记（用于和文字 lockup 并排），缺失时退回字标。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    primary = MARK_BY_THEME.get(str(theme or "light").lower(), MARK_BY_THEME["light"])
    for candidate in (primary, *MARK_FALLBACKS):
        path = asset_path(candidate)
        if not path.is_file():
            continue
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            continue
        return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    return logo_pixmap(height, theme)


def logo_pixmap(height: int = 26, theme: str = "light"):
    """The wordmark lockup for a surface, scaled to a fixed height.

    Picks the variant the brand standard documents for that theme; missing
    assets degrade to the day lockup instead of rendering nothing.
    """
    from PySide6.QtGui import QPixmap

    primary = LOGO_BY_THEME.get(str(theme or "light").lower(),
                               LOGO_BY_THEME["light"])
    for candidate in (primary, *LOGO_FALLBACKS):
        path = asset_path(candidate)
        if not path.is_file():
            continue
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            continue
        return pixmap.scaledToHeight(height,
                                     __import__("PySide6.QtCore", fromlist=["Qt"])
                                     .Qt.TransformationMode.SmoothTransformation)
    return QPixmap()
