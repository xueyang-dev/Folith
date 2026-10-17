"""Brand assets for the Qt client (icon, wordmark lockup).

Assets are looked up through :func:`folith.desktop.paths.resource_path`, so the
same code finds them in the source tree and inside a frozen bundle.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from folith.desktop import paths as desktop_paths

APP_ICON = "folith/resources/brand/folith-app-icon-white.png"
# `docs/brand.md` §1 asset matrix: the horizontal lockup has a night variant that
# differs only in the wordmark colour, and it is the one meant for a dark
# surface.  Using the day lockup on a dark background makes the wordmark vanish.
LOGO_BY_THEME = {
    "dark": "folith/resources/brand/folith-lockup-transparent-dark.png",
    "light": "folith/resources/brand/folith-lockup-transparent.png",
    "paper": "folith/resources/brand/folith-lockup-transparent.png",
    "green": "folith/resources/brand/folith-lockup-transparent.png",
}
LOGO_FALLBACKS = ("folith/resources/brand/folith-lockup-transparent.png",
                  "folith/resources/brand/folith-logo-stacked.png")
#: 只有图形、不含文字的标记（侧栏 / 页头里和文字并排）。每个主题色一份向量图：
#: 蓝色就是 ``folith-mark-hires.png`` 的向量原稿（docs/logo-exploration/folith-mark-final.svg，
#: 两者逐像素一致）；翠绿 / 石板只换后页与前页两条渐变的色标，形状与字形完全相同。
#: 按设备像素比直接从 SVG 渲染，换主题色也不会糊、不会缩小（旧做法是把 28px 位图逐像素改色）。
#: （``folith-mark.svg`` 是更早的另一版画法，不能用在这里。）
MARK_SVG = {
    "blue": "folith/resources/brand/folith-mark-blue.svg",
    "green": "folith/resources/brand/folith-mark-green.svg",
    "slate": "folith/resources/brand/folith-mark-slate.svg",
}
#: SVG viewBox 的宽高比（1254 × 1254，与 hires 位图相同）。
MARK_ASPECT = 1.0
MARK_FALLBACKS = ("folith/resources/brand/folith-mark-hires.png",
                  "folith/resources/brand/foliothread-favicon.png")


#: 各界面语言的字标：名称 + 一句短副标题。阿、俄音译名字（不带「译页」），英、西写 Folith。
WORDMARKS = {
    "zh-CN": ("Folith · 译页", "智能体翻译工作台"),
    "en": ("Folith", "Agentic Translation Workspace"),
    "ar": ("فوليث", "ترجمة بالوكلاء الأذكياء"),
    "ru": ("Фолит", "Перевод с ИИ\u2011агентами"),
    "es": ("Folith", "Traducción con agentes de IA"),
}


def wordmark(language: str) -> tuple[str, str]:
    return WORDMARKS.get(language, WORDMARKS["en"])


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


def mark_variant(theme: str = "light", accent: str = "blue") -> str:
    """主题色 → 标记的配色（旧的「翠绿 / 纸面」主题名也认）。"""
    if accent == "green" or theme == "green":
        return "green"
    if accent == "slate" or theme == "paper":
        return "slate"
    return "blue"


@lru_cache(maxsize=16)
def mark_pixmap(height: int = 28, variant: str = "blue", dpr: float = 2.0):
    """只有图形的品牌标记：从这个主题色的 SVG 按 ``dpr`` 渲染成 ``height`` 逻辑像素高。"""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QIcon, QPixmap

    size = QSize(round(height * MARK_ASPECT), height)
    path = asset_path(MARK_SVG.get(variant, MARK_SVG["blue"]))
    if path.is_file():
        pixmap = QIcon(str(path)).pixmap(size, dpr)
        if not pixmap.isNull():
            return pixmap
    for candidate in MARK_FALLBACKS:
        fallback = asset_path(candidate)
        if fallback.is_file():
            pixmap = QPixmap(str(fallback)).scaledToHeight(
                round(height * dpr), Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(dpr)
            return pixmap
    return QPixmap()


#: 资源图里品牌蓝的色相（`#004cfd`）。着色时以它为"原点"：目标色与它同色相，
#: 偏移量就是 0，图片原样不动——日间主题因此与原图逐像素一致。
BRAND_HUE = 0.6166


def tint_pixmap(pixmap, accent: str):
    """把 logo 里的**彩色部分**换成主题强调色的色相。

    只动色相与饱和度，不动明度与透明度：白色字形、灰阶、深色字标的明暗关系都保持，
    渐变（钴蓝 → 青）整体平移到目标色相，因此仍然是"同一个 logo 换了一身色"。
    低饱和度的像素（白、灰、抗锯齿边缘的白底）原样保留。
    """
    from PySide6.QtGui import QColor, QImage, QPixmap

    from . import theme as theme_module

    target = theme_module.qcolor(accent)
    hue, saturation, _value, _alpha = target.getHsvF()
    if hue < 0:                                   # 纯灰的强调色没有色相：不着色
        return pixmap
    shift = hue - BRAND_HUE
    # 强调色本身很淡（夜间的 #82acff）或偏灰（纸面的石板色）时，logo 不应该变成灰白，
    # 所以饱和度只在 [0.6, 1.0] 之间随强调色变化。
    scale = max(0.6, min(1.0, saturation))
    if abs(shift) < 0.002 and scale >= 0.999:
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    for y in range(height):
        for x in range(width):
            colour = image.pixelColor(x, y)
            if colour.alpha() == 0:
                continue
            h, s, v, a = colour.getHsvF()
            if s < 0.12 or h < 0:
                continue
            out = QColor.fromHsvF((h + shift) % 1.0, min(1.0, s * scale), v, a)
            image.setPixelColor(x, y, out)
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(pixmap.devicePixelRatio())
    return result


#: 个别主题给 logo 一条**指定的渐变色带**（而不是单纯的色相偏移）。
#: 每一项是 ``(参考亮度, 颜色)``，亮度 0..1，取样自设计师给的翠绿版 logo：
#: 后面的卡片是深翠绿（#01342e → #16765c），前面的卡片从中绿（#4cc69f）渐变到近白的薄荷色。
LOGO_RAMPS = {
    "green": (
        (0.16, "#01342e"), (0.23, "#014c3e"), (0.38, "#16765c"), (0.50, "#309776"),
        (0.66, "#4cc69f"), (0.74, "#68d7af"), (0.90, "#aff8d8"), (0.96, "#e1fcf0"),
        (0.99, "#f1fffa"),
    ),
}
#: 原图彩色部分的亮度范围（深色字标 ≈ 0.05，钴蓝卡片 ≈ 0.29，青色高光 ≈ 0.75+）
#: 线性拉到色带上：低于下限的（字标）取色带最深处，高于上限的取最浅处。
_RAMP_INPUT = (0.18, 0.80)


def ramp_pixmap(pixmap, stops):
    """按亮度把 logo 的彩色部分映射到一条渐变色带上（白色字形与灰阶原样保留）。"""
    from PySide6.QtGui import QColor, QImage, QPixmap

    anchors = [(position, QColor(colour)) for position, colour in stops]
    low, high = anchors[0][0], anchors[-1][0]
    in_low, in_high = _RAMP_INPUT

    def colour_at(luma: float) -> tuple[int, int, int]:
        t = max(0.0, min(1.0, (luma - in_low) / (in_high - in_low)))
        target = low + t * (high - low)
        for (p0, c0), (p1, c1) in zip(anchors, anchors[1:]):
            if target <= p1:
                f = (target - p0) / (p1 - p0) if p1 > p0 else 0.0
                f = max(0.0, min(1.0, f))
                return (round(c0.red() + (c1.red() - c0.red()) * f),
                        round(c0.green() + (c1.green() - c0.green()) * f),
                        round(c0.blue() + (c1.blue() - c0.blue()) * f))
        last = anchors[-1][1]
        return last.red(), last.green(), last.blue()

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    cache: dict[int, int] = {}
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            alpha = colour.alpha()
            if alpha == 0:
                continue
            if colour.hsvSaturationF() < 0.12:
                continue
            luma = (0.2126 * colour.red() + 0.7152 * colour.green()
                    + 0.0722 * colour.blue()) / 255
            key = round(luma * 255)
            rgb = cache.get(key)
            if rgb is None:
                red, green, blue = colour_at(key / 255)
                rgb = cache[key] = (red << 16) | (green << 8) | blue
            image.setPixelColor(x, y, QColor((rgb >> 16) & 255, (rgb >> 8) & 255,
                                             rgb & 255, alpha))
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(pixmap.devicePixelRatio())
    return result


def logo_pixmap(height: int = 26, theme: str = "light", *, tint: str | None = None):
    """The wordmark lockup for a surface, scaled to a fixed height.

    Picks the variant the brand standard documents for that theme; missing
    assets degrade to the day lockup instead of rendering nothing.  ``tint`` is
    an accent colour (token value): the logo's blue is shifted to that hue so the
    mark follows the theme (see :func:`tint_pixmap`), or — for themes listed in
    :data:`LOGO_RAMPS` — mapped onto that theme's designed colour ramp;
    ``None`` keeps the asset as is.
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
        pixmap = pixmap.scaledToHeight(
            height, __import__("PySide6.QtCore", fromlist=["Qt"])
            .Qt.TransformationMode.SmoothTransformation)
        if tint:
            stops = LOGO_RAMPS.get(str(theme or "").lower())
            return ramp_pixmap(pixmap, stops) if stops else tint_pixmap(pixmap, tint)
        return pixmap
    return QPixmap()
