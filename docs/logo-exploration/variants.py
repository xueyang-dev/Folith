"""Three geometric corrections of the original mark, for comparison.

  A  rotate      pages become true rectangles turned as a whole; recentred
  B  symmetric   original shear kept; equal pages offset symmetrically; recentred
  C  both        rotated and symmetric; recentred

Every version keeps the original colours and glyphs, has the same filled area
as the original, and sits with its visual centroid 1.5% above the canvas
centre (the optical centre).

    python3 docs/logo-exploration/variants.py   # needs cv2 + numpy
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from page_model import Model, Page, open_disk, page_polygon, rounded_rect  # noqa: E402
from trace_original import contour_d, load, segment  # noqa: E402

SIZE = 1254
UP = 4
LIFT = 0.015


@dataclass
class Frame:
    """A page as an affine map from its unit square plus a size and radius."""
    m: np.ndarray        # 2x3: (u, v) in [0,1]^2 -> canvas
    w: float
    h: float
    r: float

    def polygon(self) -> np.ndarray:
        local = rounded_rect(self.w, self.h, self.r)
        uv = np.column_stack([local[:, 0] / self.w, local[:, 1] / self.h, np.ones(len(local))])
        return uv @ self.m.T

    def at(self, u: float, v: float) -> np.ndarray:
        return self.m @ np.array([u, v, 1.0])

    def inverse(self, p: np.ndarray) -> np.ndarray:
        a = np.vstack([self.m, [0, 0, 1]])
        return (np.linalg.inv(a) @ np.array([p[0], p[1], 1.0]))[:2]


def sheared(cx: float, cy: float, w: float, h: float, slope: float, r: float) -> Frame:
    x0, y0 = cx - w / 2, cy - h / 2 - slope * w / 2
    return Frame(np.array([[w, 0, x0], [slope * w, h, y0]]), w, h, r)


def rotated(cx: float, cy: float, w: float, h: float, deg: float, r: float) -> Frame:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    ux, uy = np.array([c, s]) * w, np.array([-s, c]) * h
    o = np.array([cx, cy]) - ux / 2 - uy / 2
    return Frame(np.array([[ux[0], uy[0], o[0]], [ux[1], uy[1], o[1]]]), w, h, r)


def original_frames() -> tuple[Frame, Frame, Model]:
    d = json.loads((HERE / "page_model.json").read_text())
    m = Model(Page(**d["light"]), Page(**d["dark"]), d["l_y0"], d["l_s"], d["l_r"])

    def frame(p: Page) -> Frame:
        w, h = p.x1 - p.x0, p.bottom - p.top
        s = (p.s_top + p.s_bot) / 2
        return Frame(np.array([[w, 0, p.x0], [s * w, h, p.top]]), w, h, p.r)
    return frame(m.light), frame(m.dark), m


def glyphs(img: np.ndarray) -> list[dict]:
    """文, cursor and A as soft coverage maps with their centroids."""
    seg = segment(img)
    white = seg["white"]
    near = cv2.dilate(white.astype(np.uint8), np.ones((9, 9), np.uint8))
    soft = np.where(near > 0, np.clip((img[..., 0] - 20) / 215, 0, 1), 0) * (img[..., 3] / 255)
    n, lab = cv2.connectedComponents(near, connectivity=8)
    comps = []
    for i in range(1, n):
        mask = lab == i
        if mask.sum() < 400:
            continue
        ys, xs = np.nonzero(mask & white)
        comps.append({"soft": np.where(mask, soft, 0).astype(np.float32),
                      "c": np.array([xs.mean(), ys.mean()]), "area": len(xs)})
    # Name them: the cursor is the largest; 文 is the top one of the rest.
    comps.sort(key=lambda g: -g["area"])
    cursor, rest = comps[0], sorted(comps[1:], key=lambda g: g["c"][1])
    wen = rest[0]
    a = rest[-1]
    # 文 can be several components (its strokes); merge all small top-left ones.
    for g in rest[1:-1]:
        if g["c"][1] < cursor["c"][1]:
            wen["soft"] = np.maximum(wen["soft"], g["soft"])
    return [dict(wen, name="wen"), dict(cursor, name="cursor"), dict(a, name="a")]


def build_regions(light: Frame, dark: Frame, line_p: np.ndarray, line_deg: float,
                  l_r: float, scale: float) -> dict[str, np.ndarray]:
    shape = (int(SIZE * scale), int(SIZE * scale))

    def fill(poly):
        m = np.zeros(shape, np.uint8)
        cv2.fillPoly(m, [np.round(poly * scale * 16).astype(np.int32)], 1, shift=4)
        return m.astype(bool)
    lf, df = fill(light.polygon()), fill(dark.polygon())
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]] / scale
    t = math.tan(math.radians(line_deg))
    below = yy > line_p[1] + t * (xx - line_p[0])
    strip = open_disk(df & below, l_r * scale)
    vis = open_disk(lf & ~strip, l_r * scale)
    return {"light": vis, "dark_full": df, "silhouette": lf | df}


def gradient_defs(svg: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in
            re.finditer(r'<linearGradient id="(\w+)".*?</linearGradient>', svg)}


def with_transform(defn: str, matrix: np.ndarray, new_id: str) -> str:
    a, c, e = matrix[0]
    b, d, f = matrix[1]
    defn = re.sub(r'id="\w+"', f'id="{new_id}"', defn, count=1)
    return defn.replace("<linearGradient ", f'<linearGradient gradientTransform="matrix({a:.5f} {b:.5f} {c:.5f} {d:.5f} {e:.2f} {f:.2f})" ', 1)


def frame_map(src: Frame, dst: Frame) -> np.ndarray:
    a = np.vstack([src.m, [0, 0, 1]])
    b = np.vstack([dst.m, [0, 0, 1]])
    return (b @ np.linalg.inv(a))[:2]


def variant(kind: str, ol: Frame, od: Frame, om: Model, img: np.ndarray, glyph_list, grads, target_area,
            line_shift: float = 0.0, tag: str = ""):
    lc, dc = ol.at(0.5, 0.5), od.at(0.5, 0.5)
    deg = math.degrees(math.atan(0.225))
    if kind == "A":
        lw, lh, dw, dh = ol.w, ol.h, od.w, (od.h + od.h + 0.165 * od.w) / 2
        mk = lambda c, w, h, r: rotated(c[0], c[1], w, h, deg, r)
        light, dark = mk(lc, lw, lh, ol.r), mk(dc, dw, dh, od.r)
        # Interlock line at the same height through the dark page as before.
        frac = 0.552
        line_p, line_deg = dark.at(0.5, frac), deg
    else:
        w, h, r = (ol.w + od.w) / 2, (ol.h + od.h) / 2 + 30, (ol.r + od.r) / 2
        c = (lc + dc) / 2
        off = (lc - dc) / 2
        mk = (lambda p: sheared(p[0], p[1], w, h, 0.225, r)) if kind == "B" else \
             (lambda p: rotated(p[0], p[1], w, h, deg, r))
        light, dark = mk(c + off), mk(c - off)
        # Through the centre the light/dark swap is symmetric; `line_shift` moves it
        # down along the pages (fraction of page height) so more of the light page shows.
        # The outline stays point-symmetric either way: only the split inside changes.
        line_p, line_deg = c + (dark.at(0.5, 1.0) - dark.at(0.5, 0.0)) * line_shift, deg

    # Global fit: same filled area as the original, centroid at the optical centre.
    reg = build_regions(light, dark, line_p, line_deg, om.l_r, 0.5)
    sil = reg["silhouette"]
    ys, xs = np.nonzero(sil)
    gs = math.sqrt(target_area / (len(xs) / 0.25))
    cen = np.array([xs.mean(), ys.mean()]) / 0.5
    goal = np.array([SIZE / 2, SIZE / 2 - LIFT * SIZE])
    g = np.array([[gs, 0, goal[0] - gs * cen[0]], [0, gs, goal[1] - gs * cen[1]]])
    G = np.vstack([g, [0, 0, 1]])
    light = Frame(g @ np.vstack([light.m, [0, 0, 1]]), light.w, light.h, light.r)
    dark = Frame(g @ np.vstack([dark.m, [0, 0, 1]]), dark.w, dark.h, dark.r)
    line_p = (G @ np.r_[line_p, 1])[:2]
    l_r = om.l_r * gs
    # light/dark frames above carry the global map; rebuild frames' radii in canvas units
    light.r, dark.r = light.r, dark.r

    reg = build_regions(light, dark, line_p, line_deg, l_r, UP)
    down = lambda m: cv2.resize(m.astype(np.float32), (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    light_soft, dark_soft = down(reg["light"]), down(reg["dark_full"])
    share = reg["light"].sum() / max(1, reg["silhouette"].sum())
    print(f"  {kind}{tag}: light page shows {share * 100:.1f}% of the mark")

    # Glyphs follow their page (文 -> light, A -> dark); the cursor sits between them.
    placed = {}
    for gl in glyph_list:
        if gl["name"] == "wen":
            new = light.at(*ol.inverse(gl["c"]))
        elif gl["name"] == "a":
            new = dark.at(*od.inverse(gl["c"]))
        else:
            continue
        placed[gl["name"]] = new
    wen_o = next(x for x in glyph_list if x["name"] == "wen")["c"]
    a_o = next(x for x in glyph_list if x["name"] == "a")["c"]
    cur_o = next(x for x in glyph_list if x["name"] == "cursor")["c"]
    placed["cursor"] = (placed["wen"] + placed["a"]) / 2 + gs * (cur_o - (wen_o + a_o) / 2)
    glyph_soft = np.zeros((SIZE, SIZE), np.float32)
    for gl in glyph_list:
        dst = placed[gl["name"]]
        m = np.array([[gs, 0, dst[0] - gs * gl["c"][0]], [0, gs, dst[1] - gs * gl["c"][1]]])
        glyph_soft = np.maximum(glyph_soft, cv2.warpAffine(gl["soft"], m, (SIZE, SIZE), flags=cv2.INTER_CUBIC))
    glyph_soft = np.clip(glyph_soft, 0, 1)

    defs = (with_transform(grads["light"], frame_map(ol, light), f"light{kind}{tag}")
            + with_transform(grads["dark"], frame_map(od, dark), f"dark{kind}{tag}"))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" role="img" '
            f'aria-label="Folith mark, version {kind}{tag}"><title>Folith mark, version {kind}{tag}</title>'
            f'<defs>{defs}</defs>'
            f'<path fill="url(#dark{kind}{tag})" fill-rule="evenodd" d="{contour_d(dark_soft > 0.5, soft=dark_soft)}"/>'
            f'<path fill="url(#light{kind}{tag})" fill-rule="evenodd" d="{contour_d(light_soft > 0.5, soft=light_soft)}"/>'
            f'<path fill="#FFFFFF" fill-rule="evenodd" d="{contour_d(glyph_soft > 0.5, sigma=1.2, step=5.0, soft=glyph_soft)}"/>'
            "</svg>\n")


def main() -> None:
    img = load()
    ol, od, om = original_frames()
    target_area = float((img[..., 3] > 127).sum())
    grads = gradient_defs((HERE / "folith-mark-replica.svg").read_text())
    gl = glyphs(img)
    names = {"A": "rotated", "B": "symmetric", "C": "rotated-symmetric"}
    for kind, name in names.items():
        out = HERE / f"folith-mark-{name}.svg"
        out.write_text(variant(kind, ol, od, om, img, gl, grads, target_area), encoding="utf-8")
        print(out)
    for shift in (0.06, 0.12, 0.18):
        tag = f"-{int(shift * 100):02d}"
        out = HERE / f"folith-mark-symmetric{tag}.svg"
        out.write_text(variant("B", ol, od, om, img, gl, grads, target_area, shift, tag), encoding="utf-8")
        print(out)


if __name__ == "__main__":
    main()
