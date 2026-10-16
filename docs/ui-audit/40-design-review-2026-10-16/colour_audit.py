"""色彩审校的量化：日间 token 的 OKLCH 取值、关键对比度、截图里的饱和色面积。

用法：venv/bin/python docs/ui-audit/40-design-review-2026-10-16/colour_audit.py [截图.png ...]
"""
import sys, math, colorsys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from folith.ui.theme_tokens import TOKENS

def srgb_to_lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def oklch(hexv):
    hexv = hexv.lstrip("#"); r, g, b = (int(hexv[i:i+2], 16) for i in (0, 2, 4))
    r, g, b = map(srgb_to_lin, (r, g, b))
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l, m, s = (x ** (1/3) for x in (l, m, s))
    L = 0.2104542553*l + 0.7936177850*m - 0.0040720468*s
    A = 1.9779984951*l - 2.4285922050*m + 0.4505937099*s
    B = 0.0259040371*l + 0.7827717662*m - 0.8086757660*s
    return L, math.hypot(A, B), (math.degrees(math.atan2(B, A)) % 360)
def lum(hexv):
    hexv = hexv.lstrip("#"); r, g, b = (srgb_to_lin(int(hexv[i:i+2], 16)) for i in (0, 2, 4))
    return 0.2126*r + 0.7152*g + 0.0722*b
def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + 0.05) / (lb + 0.05)

t = TOKENS["light"]
print("== light tokens in OKLCH (L, C, h)")
for k in ("accent","hero","success","warning","danger","info","brand-cyan","text-primary","text-secondary","text-muted","bg-page","bg-gradient-end","accent-soft","success-soft","warning-soft","danger-soft","info-soft","border-strong"):
    v = t[k]
    if v.startswith("#"):
        L,C,h = oklch(v); print(f"{k:18} {v}  L={L:.2f} C={C:.3f} h={h:5.1f}")
print("contrast warning on warning-soft", round(contrast(t['warning'], t['warning-soft']),2))
print("contrast text-muted on bg-page", round(contrast(t['text-muted'], t['bg-page']),2))
print("contrast text-secondary on surface", round(contrast(t['text-secondary'], '#ffffff'),2))
print("contrast accent on white", round(contrast(t['accent'], '#ffffff'),2))

print("\n== screen colour share (pixels with HSV saturation>.45 and value>.35)")
bins = {"blue":(200,250),"cyan":(170,200),"green":(90,170),"yellow/orange":(20,60),"red":(345,380),"violet":(250,300)}
for name in sys.argv[1:]:
    im = Image.open(name).convert("RGB").resize((600, 360))
    px = list(im.getdata()); n = len(px); counts = {k:0 for k in bins}; sat = 0
    for r,g,b in px:
        h,s,v = colorsys.rgb_to_hsv(r/255,g/255,b/255)
        if s > .45 and v > .35:
            sat += 1; hd = h*360
            for k,(a,z) in bins.items():
                if a <= hd < z or a <= hd+360 < z: counts[k]+=1; break
    hues = [k for k,c in counts.items() if c/n > 0.0005]
    print(f"{name.split('/')[-1]:28} saturated {100*sat/n:5.1f}%  hues present: {', '.join(f'{k} {100*counts[k]/n:.1f}%' for k in hues)}")
