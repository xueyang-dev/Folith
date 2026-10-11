"""Folith semantic theme tokens — the single source of truth for every theme.

Why this module exists
----------------------
The stylesheet in :mod:`folith.ui.styles` is a large literal CSS document.  Its
colours used to be re-interpreted per theme by a hue/lightness heuristic that
guessed a "role" for every ``#hex`` it found.  That works for a theme that is a
re-tint of the same cool palette, but it cannot express a theme whose accent is
not a blue: in Paper every role that the heuristic resolved to ``primary`` (or to
``primary-soft``) came back as saturated Folith blue.  It also cannot express
Night, which is deliberately **not** a re-tint: it is a neutral graphite palette
matched to the macOS dark window chrome (§ ``TOKENS['dark']``).

So the direction is now one-way and explicit::

    component selector  ->  --tp-<role>  ->  --<semantic token>  ->  theme value

* ``TOKENS`` owns the *values*; one dict per theme, identical key sets.
* ``ROLE_ALIASES`` keeps the legacy ``--tp-*`` names alive as thin aliases, so
  the ~6k lines of existing component CSS did not have to be rewritten at once.
* The generated ``:root`` block is appended *after* the legacy normalizer runs,
  so it always wins the cascade without ``!important``.

Paper is therefore not "Light with a beige background": it is a complete,
independent palette (warm paper surfaces, ink-slate accent, warm-low-saturation
status colours) that every component inherits for free.
"""
from __future__ import annotations

# Presentation order used by the appearance settings page and by generated CSS.
THEME_ORDER = ('light', 'dark', 'paper', 'green')

SCHEMES = {'light': 'light', 'dark': 'dark', 'paper': 'light', 'green': 'light'}

# --------------------------------------------------------------------------
# Semantic tokens
# --------------------------------------------------------------------------
# Every theme declares exactly the same keys.  `light` values are the values the
# product already shipped, so wiring the token layer in changes nothing visible
# for Day mode; dark and paper are re-derived from them on purpose.
#
# Paper palette rationale (docs: "high quality book paper + modern editor"):
#   * surfaces are warm but not yellow (R-B delta stays small), never pure white
#   * the accent is an ink slate — related to the brand by hue family, unrelated
#     by saturation, so no UI element competes with the logo
#   * text is warm near-black; muted text still clears 4.5:1 on both the page and
#     the surface colour
#   * shadows are warm and very light; hierarchy comes from spacing and type
TOKENS: dict[str, dict[str, str]] = {
    'light': {
        'bg-page': '#f4f7fc',
        'bg-sidebar': '#f9fafb',
        # Golden Gate：边到边侧栏/检查器的“玻璃”底色，以及工具栏控件的面与描边。
        'bg-glass': '#eef2f8',
        'control-bg': '#ffffff',
        'control-border': 'rgba(19,28,46,.16)',
        'bg-surface': '#ffffff',
        'bg-surface-subtle': '#f9fafb',
        'bg-surface-sunken': '#f2f4f7',
        'bg-surface-raised': '#ffffff',
        'bg-hover': '#f7f9fc',
        'bg-selected': '#eef4ff',
        'text-primary': '#131c2e',
        'text-secondary': '#667085',
        'text-muted': '#8a94a6',
        'text-on-accent': '#ffffff',
        'border-faint': '#ebeef3',
        'border-hairline': '#eef0f4',
        'border-divider': '#e4e7ec',
        'border-subtle': '#dee3ea',
        'border-strong': '#c9d2df',
        'border-sidebar': '#e7eaf0',
        'accent': '#004cfd',
        'accent-hover': '#003fd6',
        'accent-active': '#0034b0',
        'accent-soft': '#eef4ff',
        'accent-soft-hover': '#e3edff',
        'accent-border': '#b9d1ff',
        'focus-ring': 'rgba(0,76,253,.22)',
        'focus-halo': 'rgba(18,103,232,.14)',
        'success': '#22a06b',
        'success-soft': '#ecfdf3',
        'warning': '#b54708',
        'warning-soft': '#fffaeb',
        'danger': '#dc2626',
        'danger-soft': '#fef3f2',
        # Info = the "paused / noted" violet (design sheet: Info #8B5CF6).  Not an
        # action colour: only status pills and a few icon tints use it.
        'info': '#6f4bdc',
        'info-soft': '#f1ecff',
        # Page wash: the home canvas fades from the page colour into a faint
        # brand-blue tint (135deg).  Dark stays flat on purpose (graphite).
        'bg-gradient-start': '#f8fbff',
        'bg-gradient-end': '#e9f1ff',
        'brand-navy': '#000d2d',
        'brand-ink': '#0b1f3b',
        'brand-logo-blue': '#004cfd',
        'brand-cyan': '#00e8fe',
        'brand-azure': '#0088fd',
        'disabled-bg': '#d9e5f6',
        'disabled-ink': '#8ca2c0',
        'overlay-veil': 'rgba(247,248,250,.96)',
        'surface-glass': 'rgba(248,251,255,.96)',
        'glass-border': '#c9dcfb',
        'glass-shadow': 'rgba(24,55,105,.08)',
        'shadow-sm': '0 1px 2px rgba(16,24,40,.04)',
        'shadow-md': '0 2px 8px rgba(16,24,40,.06), 0 1px 2px rgba(16,24,40,.04)',
        'shadow-lg': '0 8px 24px rgba(16,24,40,.08), 0 2px 6px rgba(16,24,40,.04)',
        # The switch knob stays light on every theme (as on macOS).
        'switch-knob': '#ffffff',
    },
    # Night mode is a **neutral graphite** palette, not a re-tint of the cool day
    # palette.  Rationale: on macOS the window chrome (native titlebar, menus,
    # sheets) is drawn by AppKit in its own neutral graphite family; a blue-black
    # content area therefore reads as "a web page inside a Mac window".  The
    # values below sit in the same lightness band as macOS's dark window
    # background (#1C1C1E is the dark-mode systemGray6) so the titlebar and the
    # content area read as one material.
    #
    # Two structural rules the component layer relies on:
    #   * surfaces carry the hierarchy — the ladder below is monotone and every
    #     step is a *neutral* gray (R=G, B at most +2, matching #1C1C1E);
    #   * separators/borders/hovers/text-muted are translucent white, not opaque
    #     gray-blue lines.  A translucent hairline composites over whatever
    #     surface it is drawn on, which is what makes it read as a weak
    #     reflection instead of a drawn box.
    'dark': {
        # --- surfaces (window → sidebar → card → raised control/popover) ---
        'bg-page': '#1C1C1E',
        'bg-sidebar': '#202022',
        # Golden Gate：边到边侧栏/检查器的“玻璃”底色，以及工具栏控件的面与描边。
        'bg-glass': '#232326',
        'control-bg': 'rgba(255,255,255,.08)',
        'control-border': 'rgba(255,255,255,.14)',
        'bg-surface': '#242426',
        'bg-surface-subtle': '#212123',
        'bg-surface-sunken': '#1A1A1C',
        'bg-surface-raised': '#2A2A2D',
        'bg-hover': 'rgba(255,255,255,.05)',
        # selection = the accent as a wash; there is no separate "selected navy".
        'bg-selected': 'rgba(130,172,255,.18)',
        # --- text ---
        'text-primary': 'rgba(255,255,255,.92)',
        'text-secondary': 'rgba(235,235,245,.62)',
        'text-muted': 'rgba(235,235,245,.38)',
        'text-on-accent': '#131316',
        # --- separators / borders (weak reflections, never blue-gray lines) ---
        'border-faint': 'rgba(255,255,255,.040)',
        'border-hairline': 'rgba(255,255,255,.055)',
        'border-divider': 'rgba(255,255,255,.065)',
        'border-subtle': 'rgba(255,255,255,.085)',
        'border-strong': 'rgba(255,255,255,.14)',
        'border-sidebar': 'rgba(255,255,255,.055)',
        # --- accent (interaction only: action, selection, focus, progress) ---
        'accent': '#82acff',
        'accent-hover': '#a1c0ff',
        'accent-active': '#b6ceff',
        'accent-soft': 'rgba(130,172,255,.16)',
        'accent-soft-hover': 'rgba(130,172,255,.24)',
        'accent-border': 'rgba(130,172,255,.34)',
        'focus-ring': 'rgba(130,172,255,.30)',
        'focus-halo': 'rgba(130,172,255,.22)',
        # --- status (pastel ink + a tint wash, not a saturated coloured panel) ---
        'success': '#80d9af',
        'success-soft': 'rgba(128,217,175,.14)',
        'warning': '#f2c97c',
        'warning-soft': 'rgba(242,201,124,.14)',
        'danger': '#ffaaa4',
        'danger-soft': 'rgba(255,170,164,.14)',
        'info': '#c0a8ff',
        'info-soft': 'rgba(192,168,255,.16)',
        'bg-gradient-start': '#1C1C1E',
        'bg-gradient-end': '#1C1C1E',
        'brand-navy': '#f2f2f7',
        'brand-ink': '#f2f2f7',
        'brand-logo-blue': '#82acff',
        'brand-cyan': '#00e8fe',
        'brand-azure': '#0088fd',
        'disabled-bg': '#232325',
        'disabled-ink': 'rgba(235,235,245,.28)',
        'overlay-veil': 'rgba(28,28,30,.94)',
        'surface-glass': 'rgba(36,36,38,.96)',
        'glass-border': 'rgba(255,255,255,.07)',
        'glass-shadow': 'rgba(0,0,0,.35)',
        'shadow-sm': '0 1px 2px rgba(0,0,0,.28)',
        'shadow-md': '0 2px 8px rgba(0,0,0,.34), 0 1px 2px rgba(0,0,0,.26)',
        'shadow-lg': '0 10px 26px rgba(0,0,0,.42), 0 2px 6px rgba(0,0,0,.28)',
        'switch-knob': '#ffffff',
    },
    'paper': {
        'bg-page': '#f2eadb',
        'bg-sidebar': '#ece3d2',
        # Golden Gate：边到边侧栏/检查器的“玻璃”底色，以及工具栏控件的面与描边。
        'bg-glass': '#e9dfcc',
        'control-bg': '#fbf7ee',
        'control-border': 'rgba(70,60,45,.22)',
        'bg-surface': '#fbf7ee',
        'bg-surface-subtle': '#f6f0e2',
        'bg-surface-sunken': '#e9dfcb',
        'bg-surface-raised': '#fdfaf3',
        'bg-hover': '#eae2d1',
        'bg-selected': '#e1dcce',
        'text-primary': '#2d2923',
        'text-secondary': '#5f594e',
        'text-muted': '#6f6858',
        'text-on-accent': '#faf7ef',
        'border-faint': 'rgba(70,60,45,.07)',
        'border-hairline': 'rgba(70,60,45,.09)',
        'border-divider': 'rgba(70,60,45,.11)',
        'border-subtle': 'rgba(70,60,45,.15)',
        'border-strong': 'rgba(70,60,45,.28)',
        'border-sidebar': 'rgba(70,60,45,.16)',
        'accent': '#4c5a66',
        'accent-hover': '#3e4b56',
        'accent-active': '#333e48',
        'accent-soft': '#e1dcce',
        'accent-soft-hover': '#d8d2c1',
        'accent-border': 'rgba(76,90,102,.42)',
        'focus-ring': 'rgba(76,90,102,.42)',
        'focus-halo': 'rgba(76,90,102,.18)',
        'success': '#2f6b4f',
        'success-soft': '#e3e9d8',
        'warning': '#8a5a17',
        'warning-soft': '#f1e4c4',
        'danger': '#a63a31',
        'danger-soft': '#f2ded6',
        'info': '#6a5a86',
        'info-soft': '#e6dfef',
        'bg-gradient-start': '#f4ecdd',
        'bg-gradient-end': '#ece2cf',
        'brand-navy': '#2a251e',
        'brand-ink': '#2a251e',
        # The logo keeps the brand colours in every theme: it is the anchor that
        # makes "paper" read as the same product rather than a different app.
        'brand-logo-blue': '#004cfd',
        'brand-cyan': '#00e8fe',
        'brand-azure': '#0088fd',
        'disabled-bg': '#e4dcca',
        'disabled-ink': '#9a9280',
        'overlay-veil': 'rgba(248,243,232,.95)',
        'surface-glass': 'rgba(250,246,237,.96)',
        'glass-border': 'rgba(70,60,45,.14)',
        'glass-shadow': 'rgba(80,66,44,.10)',
        'shadow-sm': '0 1px 2px rgba(72,58,38,.05)',
        'shadow-md': '0 2px 8px rgba(72,58,38,.07), 0 1px 2px rgba(72,58,38,.05)',
        'shadow-lg': '0 8px 24px rgba(72,58,38,.10), 0 2px 6px rgba(72,58,38,.05)',
        'switch-knob': '#fbf7ee',
    },
    # Green is the fourth theme: a light, fresh palette whose accent is the
    # design sheet's "Primary Green" (#0F6B4F).  Like Paper it is a complete
    # palette, not a re-tint of Day: the accent, selection washes, focus rings and
    # page wash are all green, while text / border neutrals stay the cool slate
    # of the sheet (#0F172A / #64748B / #E5E7EB).  The logo keeps its brand
    # colours (see ``brand-logo-blue``), so the product stays recognisable.
    'green': {
        'bg-page': '#f4f8f6',
        'bg-sidebar': '#f8faf9',
        # Golden Gate：边到边侧栏/检查器的“玻璃”底色，以及工具栏控件的面与描边。
        'bg-glass': '#ebf3ef',
        'control-bg': '#ffffff',
        'control-border': 'rgba(15,40,30,.18)',
        'bg-surface': '#ffffff',
        'bg-surface-subtle': '#f8faf9',
        'bg-surface-sunken': '#eef3f0',
        'bg-surface-raised': '#ffffff',
        'bg-hover': '#f0f6f3',
        'bg-selected': '#e6f4ee',
        'text-primary': '#0f172a',
        'text-secondary': '#64748b',
        'text-muted': '#7a8699',
        'text-on-accent': '#ffffff',
        'border-faint': '#edf1ef',
        'border-hairline': '#e8edeb',
        'border-divider': '#e5e7eb',
        'border-subtle': '#dde3e0',
        'border-strong': '#c6d0cb',
        'border-sidebar': '#e2e8e5',
        'accent': '#0f6b4f',
        'accent-hover': '#0b5a42',
        'accent-active': '#084833',
        'accent-soft': '#e6f4ee',
        'accent-soft-hover': '#d5ece2',
        'accent-border': '#a8d8c4',
        'focus-ring': 'rgba(15,107,79,.26)',
        'focus-halo': 'rgba(46,204,138,.18)',
        'success': '#13955f',
        'success-soft': '#e4f6ee',
        'warning': '#b54708',
        'warning-soft': '#fffaeb',
        'danger': '#dc2626',
        'danger-soft': '#fef3f2',
        'info': '#6f4bdc',
        'info-soft': '#f1ecff',
        'bg-gradient-start': '#f7fbf9',
        'bg-gradient-end': '#e2f3ea',
        'brand-navy': '#0d2b21',
        'brand-ink': '#0d2b21',
        'brand-logo-blue': '#004cfd',
        'brand-cyan': '#00e8fe',
        'brand-azure': '#0088fd',
        'disabled-bg': '#e1eae6',
        'disabled-ink': '#93a39b',
        'overlay-veil': 'rgba(244,248,246,.96)',
        'surface-glass': 'rgba(248,250,249,.96)',
        'glass-border': '#cfe3d9',
        'glass-shadow': 'rgba(15,60,45,.08)',
        'shadow-sm': '0 1px 2px rgba(16,40,30,.05)',
        'shadow-md': '0 2px 8px rgba(16,40,30,.07), 0 1px 2px rgba(16,40,30,.05)',
        'shadow-lg': '0 8px 24px rgba(16,40,30,.09), 0 2px 6px rgba(16,40,30,.05)',
        'switch-knob': '#ffffff',
    },
}

# Legacy role name (as used by the existing component CSS) -> semantic token.
ROLE_ALIASES: dict[str, str] = {
    'canvas': 'bg-page',
    'canvas-soft': 'bg-sidebar',
    'surface': 'bg-surface',
    'surface-sunken': 'bg-surface-sunken',
    'surface-raised': 'bg-surface-raised',
    'ink': 'text-primary',
    'sub': 'text-secondary',
    'faint': 'text-muted',
    'line': 'border-subtle',
    'line-subtle': 'border-faint',
    'hairline': 'border-hairline',
    'hairline-strong': 'border-divider',
    'sidebar-line': 'border-sidebar',
    'border-hairline': 'border-hairline',
    'border-strong': 'border-strong',
    'border': 'accent-border',
    'primary': 'accent',
    'primary-hover': 'accent-hover',
    'primary-active': 'accent-active',
    'primary-soft': 'accent-soft',
    'primary-soft-hover': 'accent-soft-hover',
    'tint-active': 'bg-selected',
    'tint-hover': 'bg-hover',
    'focus-ring': 'focus-ring',
    'focus-halo': 'focus-halo',
    'on-accent': 'text-on-accent',
    'warn': 'warning',
    'warn-soft': 'warning-soft',
    'danger': 'danger',
    'danger-soft': 'danger-soft',
    'success': 'success',
    'success-soft': 'success-soft',
    'navy': 'brand-navy',
    'brand-ink': 'brand-ink',
    'logo-blue': 'brand-logo-blue',
    'cyan': 'brand-cyan',
    'azure': 'brand-azure',
    'disabled-bg': 'disabled-bg',
    'disabled-ink': 'disabled-ink',
    'overlay-veil': 'overlay-veil',
    'surface-glass': 'surface-glass',
    'glass-border': 'glass-border',
    'glass-shadow': 'glass-shadow',
    'shadow-sm': 'shadow-sm',
    'shadow-md': 'shadow-md',
    'shadow-lg': 'shadow-lg',
}


def token(theme: str, name: str) -> str:
    """Value of one semantic token for one theme (falls back to light)."""
    return TOKENS.get(theme, TOKENS['light'])[name]


def normalize(theme: str) -> str:
    return theme if theme in TOKENS else 'light'


def root_css(theme: str) -> str:
    """The theme's ``:root`` block: semantic tokens plus legacy role aliases."""
    theme = normalize(theme)
    values = TOKENS[theme]
    declared = ';'.join(f'--{name}:{value}' for name, value in values.items())
    aliases = ';'.join(f'--tp-{role}:var(--{name})'
                       for role, name in ROLE_ALIASES.items())
    return (':root{' + declared + ';' + aliases
            + ';color-scheme:' + SCHEMES[theme] + ';}')


# --------------------------------------------------------------------------
# Appearance page: theme selector, live preview, preference rows
# --------------------------------------------------------------------------
def _theme_swatch_rules(container: str) -> str:
    """Paint every theme option with its own paper/accent colours.

    ``st.radio`` labels its inputs with the option *index* (not the option value),
    so a swatch is keyed to the **display order** the container renders —
    ``THEME_ORDER``, which both the appearance page and the sidebar popover follow.
    """
    rules = []
    for index, name in enumerate(THEME_ORDER):
        values = TOKENS[name]
        rules.append(
            f'{container} '
            f'[data-testid="stRadioGroup"] > div:nth-child({index + 1}) '
            f'[data-testid="stRadioOption"]::before'
            f'{{background:linear-gradient(135deg,{values["bg-surface"]} 0 52%,'
            f'{values["accent"]} 52% 100%);border-color:{values["border-strong"]};}}'
        )
    return '\n'.join(rules)


def theme_selector_css() -> str:
    """Compact theme cards with a per-theme colour preview.

    A radio is kept as the control (keyboard + aria semantics come for free);
    only its presentation changes.  Each option shows what that theme's paper and
    ink actually look like, so the choice is legible before reading the label.
    """
    return _theme_swatch_rules('.st-key-appearance_theme_control') + """
.st-key-appearance_theme_control [data-testid="stRadio"] { margin:0; }
.st-key-appearance_theme_control [data-testid="stRadioGroup"] {
 display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; align-items:stretch;
}
.st-key-appearance_theme_control [data-testid="stRadioOption"] {
 display:flex; align-items:center; gap:9px; margin:0;
 padding:10px 12px; min-height:48px; width:100%;
 border:1px solid var(--border-subtle); border-radius:10px;
 background:var(--bg-surface); color:var(--text-secondary) !important;
 font-size:13px !important; font-weight:500; cursor:pointer;
 transition:background .12s ease, border-color .12s ease, color .12s ease;
}
.st-key-appearance_theme_control [data-testid="stRadioOption"]::before {
 content:""; flex:0 0 16px; width:16px; height:16px; border-radius:5px;
 border:1px solid var(--border-strong); box-sizing:border-box;
}
.st-key-appearance_theme_control [data-testid="stRadioOption"]:hover {
 background:var(--bg-hover); border-color:var(--border-strong);
}
.st-key-appearance_theme_control [data-testid="stRadioOption"]:has(input:focus-visible) {
 outline:2px solid var(--accent); outline-offset:2px;
}
/* The native indicator is redundant: the card *is* the control, and the swatch
   already shows the theme. Checked state carries a swatch, a tinted surface, an
   ink border AND a checkmark, so it never depends on colour alone. */
.st-key-appearance_theme_control
 [data-testid="stRadioOption"] > div > div:first-child { display:none !important; }
.st-key-appearance_theme_control [data-testid="stRadioOption"][data-selected="true"] {
 background:var(--accent-soft); border-color:var(--accent); color:var(--text-primary) !important;
 box-shadow:inset 0 0 0 1px var(--accent);
}
.st-key-appearance_theme_control [data-testid="stRadioOption"][data-selected="true"]::after {
 content:"✓"; margin-inline-start:auto; color:var(--accent); font-size:12px; font-weight:700;
}
.st-key-appearance_theme_control [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"],
.st-key-appearance_theme_control [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] *,
.st-key-appearance_theme_control [data-testid="stRadioOption"] p {
 margin:0 !important; color:inherit !important; font-size:13px !important; font-weight:inherit;
}
@media (max-width: 900px) {
 .st-key-appearance_theme_control [data-testid="stRadioGroup"] {
  grid-template-columns:1fr;
 }
}
"""


def preview_css() -> str:
    """Live reading preview, painted from the *pending* theme's own tokens."""
    blocks = []
    for name in THEME_ORDER:
        values = TOKENS[name]
        declared = ';'.join(f'--pv-{key}:{value}' for key, value in values.items())
        blocks.append(f'.tp-theme-preview[data-preview-theme="{name}"]{{{declared}}}')
    return '\n'.join(blocks) + """
.tp-theme-preview {
 border:1px solid var(--pv-border-subtle); border-radius:12px; overflow:hidden;
 background:var(--pv-bg-page); color:var(--pv-text-primary);
 box-shadow:none; font-size:13px; line-height:1.7;
}
.tp-theme-preview .tp-pv-head {
 display:flex; align-items:center; gap:8px;
 padding:9px 14px; background:var(--pv-bg-sidebar);
 border-bottom:1px solid var(--pv-border-subtle);
 color:var(--pv-text-muted); font-size:11px; font-weight:600;
}
.tp-theme-preview .tp-pv-dot {
 width:7px; height:7px; border-radius:50%; background:var(--pv-accent); flex:0 0 7px;
}
.tp-theme-preview .tp-pv-body { padding:14px 16px 16px; background:var(--pv-bg-surface); }
.tp-theme-preview .tp-pv-label {
 display:block; margin:0 0 5px; color:var(--pv-text-muted);
 font-size:10px; font-weight:700; letter-spacing:.08em;
}
.tp-theme-preview .tp-pv-source,
.tp-theme-preview .tp-pv-target {
 margin:0 0 14px; color:var(--pv-text-primary); font-size:13px; line-height:1.8;
}
.tp-theme-preview .tp-pv-term {
 margin:0; padding:8px 11px; background:var(--pv-accent-soft); color:var(--pv-text-primary); font-size:12px;
}
.tp-theme-preview .tp-pv-term b { color:var(--pv-accent); font-weight:600; }
.tp-theme-preview .tp-pv-badge {
 display:inline-block; margin:0; margin-inline-start:auto; padding:1px 7px; border-radius:999px;
 background:var(--pv-accent); color:var(--pv-text-on-accent);
 font-size:10px; font-weight:700; letter-spacing:0;
}
"""


def settings_page_css() -> str:
    """Preferences page layout: sections, dividers, label/control rows.

    Deliberately *not* a dashboard: no outer card, no nested surfaces. Structure
    comes from a hairline, spacing and type hierarchy. The live preview is the
    only real surface on the page, because it is an object (a rendered document).
    """
    return """
.stMain:has(.st-key-appearance_page) [data-testid="stMainBlockContainer"] {
 padding-top:34px !important; padding-bottom:64px !important;
}
.stMain:has(.st-key-appearance_page) h1 { font-size:26px !important; }
.stMain:has(.st-key-appearance_page) [data-testid="stCaptionContainer"] {
 margin:-6px 0 0; color:var(--text-secondary) !important; font-size:13.5px !important;
}
.st-key-appearance_page [data-testid="stVerticalBlock"] { gap:0; }
.st-key-appearance_page [data-testid="stElementContainer"] { margin:0; }
/* Streamlit gives the last markdown block in a container a -16px bottom margin
   to cancel a paragraph margin we do not use. With `gap:0` that negative margin
   pulls the next element up and clips the description once the columns stack. */
.st-key-appearance_page [data-testid="stMarkdownContainer"] { margin-bottom:0 !important; }
.st-key-appearance_page [class*="st-key-appearance_row_"] { padding:14px 0 18px; }
.st-key-appearance_page [data-testid="stHorizontalBlock"] { align-items:flex-start; }
.tp-pref-section-title {
 margin:0; padding-top:22px; border-top:1px solid var(--border-hairline);
 color:var(--text-primary); font-size:13px; font-weight:700;
}
.tp-pref-label { margin:0 !important; color:var(--text-primary) !important; font-size:14px !important; font-weight:600; }
.tp-pref-desc { margin:5px 0 0 !important; color:var(--text-muted) !important; font-size:12.5px !important; line-height:1.65; }
.st-key-appearance_row_language [data-testid="stSelectbox"] { max-width:320px; }
.st-key-appearance_action { padding-top:24px; border-top:1px solid var(--border-hairline); }
.st-key-appearance_action [data-testid="stColumn"]:last-child {
 display:flex; flex-direction:column; align-items:flex-end;
}
.st-key-appearance_action .stButton > button { min-height:38px; padding:0 18px; border-radius:9px; font-size:13.5px; }
.tp-pref-saved {
 display:flex; align-items:center; gap:6px; margin:10px 0 0 !important;
 color:var(--success) !important; font-size:12px !important; font-weight:500;
}
@media (max-width: 900px) {
 .stMain:has(.st-key-appearance_page) [data-testid="stMainBlockContainer"] { padding-left:18px !important; padding-right:18px !important; }
 .st-key-appearance_page [data-testid="stHorizontalBlock"] { flex-direction:column; }
 .st-key-appearance_page [data-testid="stColumn"] { width:100% !important; flex:1 1 auto !important; }
 .st-key-appearance_action [data-testid="stColumn"]:last-child { align-items:stretch; }
}
"""


def utility_css() -> str:
    """Sidebar utility (bottom bar) popovers: compact, keyboard-complete rows.

    Same control as the appearance page -- a radio, so arrow keys and ``aria``
    semantics come for free, with the same per-theme swatches -- only denser:
    these live inside a 236px sidebar panel, so a row is 32px, not a 48px card.

    Every selector is built **per scope**. A comma-separated scope list must never
    be interpolated into a longer selector: in ``A, B > div`` the declaration lands
    on ``A`` verbatim, which once turned a "hide the native radio ring" rule into
    ``display:none`` on the whole option label (an invisible theme picker).
    """
    scopes = ('.st-key-utility_theme_panel', '.st-key-utility_language_panel')
    groups = ',\n'.join(f'{scope} [data-testid="stRadioGroup"]' for scope in scopes)

    def option(suffix: str = '') -> str:
        return ',\n'.join(f'{scope} [data-testid="stRadioOption"]{suffix}'
                          for scope in scopes)

    return _theme_swatch_rules(scopes[0]) + f"""
{groups} {{ display:flex; flex-direction:column; gap:2px; align-items:stretch; }}
{option()} {{
 display:flex; align-items:center; gap:0; margin:0; width:100%;
 min-height:32px; padding:6px 8px; white-space:nowrap;
 border:1px solid transparent; border-radius:8px;
 background:transparent; color:var(--text-secondary) !important;
 font-size:12.5px !important; font-weight:500; cursor:pointer;
 transition:background .12s ease, color .12s ease;
}}
/* 行内版式交给内容层：原生 input 的 <span> 是 0 宽空盒，不该带来 8px 缩进。 */
{option(' > div')} {{ display:flex; align-items:center; gap:8px; width:100%; min-width:0; }}
{option(':hover')} {{ background:var(--bg-hover); color:var(--text-primary) !important; }}
{option(':has(input:focus-visible)')} {{
 outline:2px solid var(--accent); outline-offset:1px;
}}
/* 只有主题行带色块：语言行保持纯文字，不假装自己有配色。 */
.st-key-utility_theme_panel [data-testid="stRadioOption"]::before {{
 content:""; flex:0 0 14px; width:14px; height:14px; border-radius:4px;
 border:1px solid var(--border-strong); box-sizing:border-box;
}}
/* 原生圆点整只隐藏（外环 + 内点）：整行就是控件。选中态同时有 tint、ink 文字与
   ✓，不靠颜色单独表达；32px 的行里再叠一只圆环只会变吵。 */
{option(' > span')},
{option(' > div > div:first-child')} {{ display:none !important; }}
{option('[data-selected="true"]')} {{
 background:var(--accent-soft); color:var(--text-primary) !important; font-weight:600;
}}
{option('[data-selected="true"]::after')} {{
 content:"✓"; margin-inline-start:auto; color:var(--accent);
 font-size:11px; font-weight:700;
}}
{option(' [data-testid="stMarkdownContainer"]')},
{option(' [data-testid="stMarkdownContainer"] *')},
{option(' p')} {{
 margin:0 !important; color:inherit !important;
 font-size:12.5px !important; font-weight:inherit;
}}
"""


def widget_css() -> str:
    """Native Streamlit widgets that the legacy stylesheet never re-coloured.

    These fall through to Streamlit's own theme (``--theme.primaryColor``), which
    is fixed at launch and therefore cannot follow Paper. Pinning them to tokens
    is what removes the last "cobalt in a paper page" leaks (checkbox ticks,
    toggle switches, slider fill, progress bars).
    """
    return """
label[data-selected="true"]:has(input[type="checkbox"]:not([role="switch"])) svg {
 stroke:var(--text-on-accent) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
 background:var(--accent) !important; border-color:var(--accent) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] > div > div:first-child,
[data-testid="stSlider"] [data-baseweb="slider"] div[role="progressbar"] {
 background:var(--accent) !important;
}
[data-testid="stProgress"] div[role="progressbar"] > div { background:var(--accent) !important; }
[data-testid="stSpinner"] svg circle,
[data-testid="stSpinner"] > div > div { color:var(--accent) !important; }
[data-testid="stSpinner"] i, .stSpinner i { background:var(--accent) !important; border-color:var(--accent) !important; }
"""
