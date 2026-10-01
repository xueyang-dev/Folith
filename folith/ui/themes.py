"""Display palettes for the existing Folith design system."""
from __future__ import annotations
import colorsys
import re

PALETTES = {
 'dark': {
  'canvas':'#111820','surface':'#1b2531','canvas-soft':'#17212c','surface-sunken':'#141e28','surface-raised':'#22303e',
  'ink':'#e6edf5','sub':'#b6c3d3','faint':'#97a8bc','line':'#3b4b5d','line-subtle':'#2c3a49','sidebar-line':'#344457',
  'border-hairline':'#2c3a49','border-strong':'#4e6075','primary':'#82acff','primary-hover':'#a1c0ff','primary-active':'#b6ceff',
  'primary-soft':'#203855','primary-soft-hover':'#29476b','border':'#4c709e','tint-active':'#203855','tint-hover':'#253343',
  'brand-ink':'#e6edf5','navy':'#e6edf5','warn':'#f2c97c','warn-soft':'#3a3021','danger':'#ffaaa4','danger-soft':'#40282c',
  'success':'#80d9af','success-soft':'#193c30'},
 'paper': {
  'canvas':'#f1e8d5','surface':'#fbf5e7','canvas-soft':'#f6efdf','surface-sunken':'#eee3cd','surface-raised':'#fff9ee',
  'ink':'#322c23','sub':'#645a49','faint':'#7a6d58','line':'#d6c8ad','line-subtle':'#e6dac2','sidebar-line':'#d6c8ad',
  'border-hairline':'#e6dac2','border-strong':'#c4b391','primary':'#2251af','primary-hover':'#1b408a','primary-active':'#173773',
  'primary-soft':'#e7ebef','primary-soft-hover':'#dce3ed','border':'#b0c0d6','tint-active':'#e7ebef','tint-hover':'#f0e6d2',
  'brand-ink':'#322c23','navy':'#322c23','warn':'#855316','warn-soft':'#f5e6bb','danger':'#ac302b','danger-soft':'#f8e2d8',
  'success':'#296c47','success-soft':'#e4ecd5'}
}

def _color_role(match: re.Match) -> str:
    """Resolve legacy literal CSS colors to the same palette roles as new UI."""
    raw = match.group(0)[1:]
    if len(raw) == 3:
        raw = ''.join(c * 2 for c in raw)
    r,g,b = (int(raw[i:i+2],16)/255 for i in (0,2,4))
    hue, light, saturation = colorsys.rgb_to_hls(r,g,b)
    if light > .88:
        role = ('danger-soft' if saturation > .35 and (hue < .05 or hue > .93)
                else 'warn-soft' if saturation > .35 and .05 <= hue < .18
                else 'success-soft' if saturation > .35 and .18 <= hue < .48
                else 'primary-soft' if saturation > .35 and .48 <= hue < .78
                else 'surface' if min(r,g,b) > .98 else 'canvas-soft')
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

def apply_theme(css: str, theme: str = 'light', locale: str = 'zh-CN') -> str:
    if theme in PALETTES:
        css = re.sub(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b', _color_role, css)
        # CSS uses a few named colors in older workspace declarations.
        css = re.sub(r'(?<=:)\s*white\b', ' var(--tp-surface)', css)
        palette = ';'.join(f'--tp-{key}:{value}' for key,value in PALETTES[theme].items())
        css += ':root{' + palette + ';color-scheme:' + ('dark' if theme == 'dark' else 'light') + ';}'
    css += '''
[data-testid="stSidebar"] { background:var(--tp-canvas-soft) !important; }
[data-baseweb="popover"], [role="listbox"], [data-baseweb="menu"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-baseweb="select"] > div { background:var(--tp-surface) !important; color:var(--tp-ink) !important; }
.tp-theme-preview { padding:24px; background:var(--tp-surface); border:1px solid var(--tp-line); border-radius:8px; color:var(--tp-ink); }
.tp-theme-preview p { margin:12px 0 0; line-height:1.8; color:var(--tp-sub); }
.st-key-appearance_settings, .st-key-language_settings { margin-bottom:16px; }
.stMain:has(.st-key-appearance_settings) [data-testid="stMainBlockContainer"] { padding-top:28px !important; padding-bottom:40px !important; }
.st-key-provider_status [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { flex:0 0 64px !important; min-width:64px !important; }
.st-key-provider_status [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex:1 1 auto !important; min-width:0 !important; }
.st-key-provider_status button { white-space:nowrap !important; }
[data-testid="stSidebarCollapseButton"] button { color:var(--tp-sub) !important; }

'''
    if theme in PALETTES:
        css += '''
.stButton button[kind="secondary"], [data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-popover"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
.stButton button:disabled { color:var(--tp-faint) !important; }
[data-testid="stButtonGroup"] [role="radio"] { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
[data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] { background:var(--tp-primary-soft) !important; border-color:var(--tp-border) !important; }
[data-testid="stExpander"] { background:var(--tp-surface) !important; border-color:var(--tp-line) !important; }
[data-testid="stPopoverButton"], [data-testid="stPopover"] > button { background:var(--tp-surface) !important; color:var(--tp-ink) !important; border-color:var(--tp-line) !important; }
/* Source activation is a transparent overlay above the document text. */
[class*="st-key-cat_source_activate_"] .stButton button[kind="secondary"] { background:transparent !important; border-color:transparent !important; color:transparent !important; }
'''
    if theme == 'dark':
        css += '.tp-brand-logo { mix-blend-mode:normal; content:url("data:image/png;base64,' + _dark_logo() + '"); }'
        css += '''button[kind="primary"], [data-testid="stBaseButton-primary"] { color:var(--tp-ink) !important; }
.stMain .stButton button[kind="primary"] { background:var(--tp-primary) !important; color:#111820 !important; }
[data-testid="stSidebar"] [class*="st-key-new_task_action"] button[kind="primary"] { background:var(--tp-primary) !important; color:#111820 !important; }
.st-key-workspace_topbar [class*="st-key-topbar_cta_primary_"] button { color:#111820 !important; }
'''
    if locale == 'ar':
        css += '''
.stMain, [data-testid="stSidebar"], [data-baseweb="popover"] { direction:rtl; text-align:start; }
[data-testid="stWidgetLabel"] { text-align:start; }
.stMain h1, .stMain h2, .stMain h3, .stMain h4,
.stMain [data-testid="stMarkdownContainer"], .stMain [data-testid="stCaptionContainer"], .tp-title, .tp-section-title,
.tp-section-sub, .tp-theme-preview { text-align:right !important; }
.stMain input[type="radio"] { direction:rtl; }
.tp-brand-kicker, code, pre, .tp-engine-model { direction:ltr; unicode-bidi:isolate; }
.st-key-workspace_main_col, [class*="st-key-cat_grid_"], .tp-cat-head { direction:ltr; }
.tp-cat-source, [class*="st-key-cat_source_cell_"] { text-align:start; }
[data-testid="stTextArea"] textarea { unicode-bidi:plaintext; }
[data-testid="stTextInput"] input { unicode-bidi:plaintext; }
.tp-nav-current { left:auto; right:0; }
'''
    return css

def _dark_logo() -> str:
    import base64
    from pathlib import Path
    return base64.b64encode((Path(__file__).resolve().parents[1] / 'resources/brand/folith-lockup-transparent-dark.png').read_bytes()).decode('ascii')
