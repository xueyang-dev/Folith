"""Theme application for the Folith design system.

Three layers, applied in this order:

1. **Legacy literal normalizer** — ``folith.ui.styles`` is a large literal CSS
   document; for every theme except Day (which *is* that document) each ``#hex``
   is replaced with the ``--tp-<role>`` reference the heuristic in
   :func:`_color_role` resolves for it.  This is a compatibility shim so the
   component CSS did not have to be rewritten to convert it: it never invents a
   colour, it only rewrites a literal into a role reference.
2. **Semantic token root** — :func:`folith.ui.theme_tokens.root_css` declares the
   theme's values once (``--bg-page``, ``--accent``, ...) and binds every legacy
   ``--tp-*`` role to them.  Because it is appended after the stylesheet, it wins
   the cascade without ``!important``.
3. **Component layer** — tokens that cannot be expressed as a value alone: the
   three-option theme selector, the live preview, the preferences page layout and
   the native Streamlit widgets the legacy sheet never re-coloured.

Consequence: Paper mode no longer "overrides" anything. It declares its own
values, and every component inherits them.
"""
from __future__ import annotations
import colorsys
import re

from .theme_tokens import (normalize, preview_css, root_css, settings_page_css,
                           theme_selector_css, utility_css, widget_css)

# --------------------------------------------------------------------------
# 1. Legacy literal normalizer
# --------------------------------------------------------------------------
def _color_role(match: re.Match) -> str:
    """Resolve a legacy literal CSS color to the palette role it stands for."""
    raw = match.group(0)[1:]
    if len(raw) == 3:
        raw = ''.join(c * 2 for c in raw)
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, saturation = colorsys.rgb_to_hls(r, g, b)
    if light > .88:
        role = ('danger-soft' if saturation > .35 and (hue < .05 or hue > .93)
                else 'warn-soft' if saturation > .35 and .05 <= hue < .18
                else 'success-soft' if saturation > .35 and .18 <= hue < .48
                else 'primary-soft' if saturation > .35 and .48 <= hue < .78
                else 'surface' if min(r, g, b) > .98 else 'canvas-soft')
    elif saturation < .35:
        role = 'line' if light > .7 else 'faint' if light > .48 else 'sub' if light > .3 else 'ink'
    elif hue < .06 or hue > .93:
        role = 'danger'
    elif hue < .18:
        role = 'warn'
    elif hue < .48:
        role = 'success'
    else:
        role = 'border' if light > .72 else 'primary'
    return f'var(--tp-{role})'


_HEX = re.compile(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b')

# ``rgba()`` literals are invisible to the hex normalizer, so a blue focus halo
# written as ``rgba(18,103,232,.14)`` used to survive into every theme. They are
# fixed at the source in ``styles.py``; this guard makes a regression loud in
# tests instead of silently shipping cobalt into Paper.
_UNRESOLVED_RGBA = re.compile(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)')


def audit_theme_leaks(css: str, theme: str) -> list[str]:
    """Report saturated blue literals that survived normalizing for a theme.

    Used by the regression test: Paper must not inherit the brand cobalt for any
    interactive surface. Near-black shadows and the brand anchors (the logo) are
    deliberately not "leaks".
    """
    def is_cobalt(r: float, g: float, b: float) -> bool:
        hue, light, saturation = colorsys.rgb_to_hls(r, g, b)
        return (saturation > .45 and .52 <= hue < .72 and .12 < light < .85
                and b > r and b > g)

    leaks = []
    for match in _HEX.finditer(css):
        raw = match.group(0)[1:]
        if len(raw) == 3:
            raw = ''.join(c * 2 for c in raw)
        rgb = tuple(int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
        if is_cobalt(*rgb):
            leaks.append(match.group(0).lower())
    for match in _UNRESOLVED_RGBA.finditer(css):
        rgb = tuple(int(value) / 255 for value in match.groups())
        if is_cobalt(*rgb):
            leaks.append(match.group(0).lower())
    # The logo keeps the brand colours in every theme on purpose.
    allowed = ('#004cfd', '#0088fd', '#00e8fe', '#82acff', '#a1c0ff', '#b6ceff',
               '#4c709e', 'rgba(0,76,253',
               # 日间界面主色（与 logo 同色相、低彩度）的焦点环默认值：
               # 由各主题的 :root 覆盖，和原来的钴蓝焦点环是同一个位置。
               'rgba(43,95,209')
    return sorted({leak for leak in leaks
                   if not any(leak.startswith(prefix) for prefix in allowed)})


# --------------------------------------------------------------------------
# 3a. Shared normalization: third-party defaults our components must beat
# --------------------------------------------------------------------------
# These are not theme patches — they beat Streamlit/BaseWeb defaults that are
# injected into the document after this stylesheet, and they are written in terms
# of tokens so they hold for all three themes.
SHARED_CSS = """
[data-baseweb="popover"], [role="listbox"], [data-baseweb="menu"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-baseweb="select"] > div { background:var(--tp-surface) !important; color:var(--tp-ink) !important; }
[data-testid="stTextInputRootElement"], [data-baseweb="input"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; }
/* 侧栏 secondary 按钮默认是**没有面的导航行**，不是白卡片。
   这里刻意用 `:where()` 把 `[data-testid="stSidebar"]` 那一段的权重清成 0：
   Project Switcher 是"有边框的控件"，它在 `styles.py` 里以 (0,2,1) + !important
   重新声明自己的 surface 与 hairline，必须压过这一条 —— 权重留 0 是**故意**的。

   旧写法是 `…:not(.st-key-current_project_selector button)`：它在 Chromium 里确实
   能生效，但有两个问题 —— ①共享层因此必须知道**某个组件自己的 key**（组件一改名，
   单侧就会静默失效）；②它依赖 `:not()` 接受复杂选择器（带后代组合子），这属于
   Selectors 4 的较新能力，旧引擎会整段丢掉 `:not()`，于是"除 switcher 之外"退化成
   "所有 sidebar secondary 按钮"，把 switcher 的底色和边框一起清成透明。
   权重归零之后，谁该有面、谁不该有面由**组件自己的规则**决定，不再需要跨层例外。 */
:where([data-testid="stSidebar"]) button[kind="secondary"] { background:transparent !important; border-color:transparent !important; }
[data-testid="stSidebarCollapseButton"] button { color:var(--tp-sub) !important; }
:where(.stButton button[kind="secondary"], [data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-popover"]) { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
.stButton button:disabled { color:var(--tp-disabled-ink) !important; }
[data-testid="stButtonGroup"] [role="radio"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
[data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] { background:var(--tp-primary-soft) !important; border-color:var(--tp-border) !important; color:var(--tp-brand-ink) !important; }
[data-testid="stExpander"] { background:var(--tp-surface) !important; border-color:var(--tp-line) !important; }
[data-testid="stPopoverButton"], [data-testid="stPopover"] > button { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
/* Source activation is a transparent overlay above the document text. */
[class*="st-key-cat_source_activate_"] .stButton button[kind="secondary"] { background:transparent !important; border-color:transparent !important; color:transparent !important; }
"""

# Dark needs its own logo lockup (the light one is dark-on-transparent).
_DARK_ONLY_CSS = '.tp-brand-logo { mix-blend-mode:normal; content:url("data:image/png;base64,%s"); }'


def _dark_logo() -> str:
    import base64
    from pathlib import Path
    return base64.b64encode((Path(__file__).resolve().parents[1]
                             / 'resources/brand/folith-lockup-transparent-dark.png').read_bytes()).decode('ascii')


# Right-to-left layout is orthogonal to the palette and stays locale-driven.
RTL_CSS = """
.stMain, [data-testid="stSidebar"], [data-baseweb="popover"] { direction:rtl; text-align:start; }
[data-testid="stWidgetLabel"] { text-align:start; }
.stMain h1, .stMain h2, .stMain h3, .stMain h4,
.stMain [data-testid="stMarkdownContainer"], .stMain [data-testid="stCaptionContainer"], .tp-title, .tp-section-title,
.tp-section-sub, .tp-theme-preview { text-align:right !important; }
.stMain input[type="radio"] { direction:rtl; }
[data-testid="stDialog"], [data-testid="stDialog"] [role="dialog"] { direction:rtl; text-align:right; }
body:has([data-testid="stDialog"] [role="dialog"]) [data-testid="stPopoverBody"] { visibility:hidden; }
.tp-theme-preview [dir="auto"] { text-align:start !important; unicode-bidi:plaintext; }
.stMain, [data-testid="stSidebar"], .stMain button, .stMain input, .stMain textarea { font-family:Tahoma, Arial, sans-serif !important; }
.tp-brand-kicker, code, pre, .tp-engine-model { direction:ltr; unicode-bidi:isolate; }
[class*="st-key-api_key_"] input, [class*="st-key-base_url_"] input,
.st-key-custom_base_url input, .st-key-custom_api_key input,
.st-key-reviewer_api_key input, .st-key-reviewer_base_url input { direction:ltr; text-align:left; unicode-bidi:isolate; }
[data-testid="stSidebar"] { border-right:0; border-left:1px solid var(--tp-sidebar-line); }
[data-testid="stIconMaterial"], .material-symbols-rounded { unicode-bidi:isolate; }
.st-key-workspace_main_col, [class*="st-key-cat_grid_"], .tp-cat-head { direction:ltr; }
.tp-cat-source, [class*="st-key-cat_source_cell_"] { text-align:start; }
[data-testid="stTextArea"] textarea { unicode-bidi:plaintext; }
[data-testid="stTextInput"] input { unicode-bidi:plaintext; }
[class*="st-key-workspace_chap_seg_"] button,
[class*="st-key-workspace_block_seg_"] button { direction:ltr; unicode-bidi:plaintext; text-align:left; }
.tp-nav-current { left:auto; right:0; }
[class*="st-key-history_cta_"] { right:auto; left:15px; }
[class*="st-key-history_del_"] { right:auto; left:125px; }
[class*="st-key-history_move_"] { right:auto; left:210px; }
.tp-hcard-foot { padding-right:0; padding-left:210px; }
.tp-hcard.is-orphan .tp-hcard-foot { padding-right:0; padding-left:310px; }

[class*="st-key-next_to_"] [data-testid="stIconMaterial"],
[class*="st-key-back_to_"] [data-testid="stIconMaterial"],
[class*="st-key-workspace_back_"] [data-testid="stIconMaterial"] { transform:scaleX(-1); }
"""


def normalize_legacy(css: str, theme: str) -> str:
    """Rewrite every literal colour in the legacy stylesheet to a role reference.

    Day mode *is* the literal stylesheet, so it is returned untouched; the other
    two themes re-point each literal at the role it stands for and then receive
    real values from the generated ``:root`` block.
    """
    if normalize(theme) == 'light':
        return css
    css = _HEX.sub(_color_role, css)
    return re.sub(r'(?<=:)\s*white\b', ' var(--tp-surface)', css)


def apply_theme(css: str, theme: str = 'light', locale: str = 'zh-CN') -> str:
    theme = normalize(theme)
    css = normalize_legacy(css, theme)
    css += root_css(theme)
    css += SHARED_CSS
    css += theme_selector_css()
    css += utility_css()
    css += preview_css()
    css += settings_page_css()
    css += widget_css()
    if theme == 'dark':
        css += _DARK_ONLY_CSS % _dark_logo()
    if locale == 'ar':
        css += RTL_CSS
    return css
