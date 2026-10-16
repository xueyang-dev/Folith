"""Trace folith-mark-hires.png into a faithful SVG replica.

The source is a raster, so the replica is traced rather than redrawn:
pixels are split into the light page, the dark page and the white glyphs,
each region's outline becomes a path, and each page gets a linear gradient
fitted to its measured colours.

    python3 docs/logo-exploration/trace_original.py   # needs cv2 + numpy
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "folith/resources/brand/folith-mark-hires.png"
OUT = Path(__file__).resolve().parent / "folith-mark-replica.svg"
EPS = 0.9  # outline tolerance in source pixels


def load() -> np.ndarray:
    img = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA).astype(np.float64)


def fit_plane(rgb: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    a = np.column_stack([np.ones_like(xs), xs, ys])
    coef, *_ = np.linalg.lstsq(a, rgb, rcond=None)
    return coef  # 3x3: rows = 1, x, y


def predict(coef: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones_like(xs), xs, ys]) @ coef


def largest(mask: np.ndarray, keep: int = 1, min_area: int = 200) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    order = sorted(range(1, n), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    out = np.zeros_like(mask, dtype=bool)
    for i in order[:keep]:
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out |= lab == i
    return out


def segment(img: np.ndarray) -> dict[str, np.ndarray]:
    rgb, alpha = img[..., :3], img[..., 3]
    solid = alpha > 127
    white = solid & (rgb.min(axis=2) > 180)
    blue = solid & ~white
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w]

    def seed(boxes):
        m = np.zeros_like(blue)
        for x0, y0, x1, y1 in boxes:
            m[y0:y1, x0:x1] = True
        return m & blue

    light_seed = seed([(620, 240, 1100, 520), (1080, 600, 1160, 940), (340, 180, 420, 600)])
    dark_seed = seed([(100, 380, 280, 960), (380, 860, 660, 1080), (700, 900, 1000, 1120)])
    models = {}
    for name, m in (("light", light_seed), ("dark", dark_seed)):
        ys, xs = np.nonzero(m)
        models[name] = fit_plane(rgb[ys, xs], ys.astype(float), xs.astype(float))

    ys, xs = np.nonzero(blue)
    px = rgb[ys, xs]
    err = {k: np.linalg.norm(px - predict(c, ys.astype(float), xs.astype(float)), axis=1)
           for k, c in models.items()}
    is_light = err["light"] < err["dark"]
    # The light page's left edge sits near x=305 and the dark page's right edge
    # near x=1056: outside those bounds the colour test cannot be wrong.
    is_light = (is_light & (xs >= 298)) | (xs > 1062)
    light = np.zeros_like(blue)
    light[ys[is_light], xs[is_light]] = True
    dark = blue & ~light

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    clean = lambda m: cv2.morphologyEx(cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, kernel),
                                       cv2.MORPH_CLOSE, kernel).astype(bool)
    light, dark = largest(clean(light)), largest(clean(dark))
    # Glyphs: split white into cursor (largest), 文 (top-left) and A (bottom-right).
    n, lab, stats, cent = cv2.connectedComponentsWithStats(white.astype(np.uint8), 8)
    comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 150]
    return {"light": light, "dark": dark, "white": np.isin(lab, comps),
            "models": models, "solid": solid}


def _gauss(n: int, sigma: float) -> np.ndarray:
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return k / k.sum()


def _corners(pts: np.ndarray, k: int = 7, limit: float = 38.0) -> list[int]:
    n = len(pts)
    a = pts[(np.arange(n) - k) % n]
    b = pts[(np.arange(n) + k) % n]
    v1, v2 = pts - a, b - pts
    cos = (v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1) + 1e-9)
    turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    idx = []
    for i in np.argsort(-turn):
        if turn[i] < limit:
            break
        if all(min(abs(i - j), n - abs(i - j)) > 2 * k for j in idx):
            idx.append(int(i))
    return sorted(idx)


def _smooth_run(run: np.ndarray, sigma: float, closed: bool) -> np.ndarray:
    if len(run) < 5:
        return run
    g = _gauss(len(run), sigma)
    r = len(g) // 2
    if closed:
        ext = np.concatenate([run[-r:], run, run[:r]])
    else:
        ext = np.concatenate([np.repeat(run[:1], r, 0), run, np.repeat(run[-1:], r, 0)])
    out = np.column_stack([np.convolve(ext[:, i], g, mode="valid") for i in range(2)])
    if not closed:
        out[0], out[-1] = run[0], run[-1]
    return out


def _bezier(points: np.ndarray, closed: bool) -> str:
    """Catmull-Rom through the points, written as cubic Beziers."""
    n = len(points)
    seg = []
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = points[(i - 1) % n] if (closed or i > 0) else points[i]
        p1, p2 = points[i], points[(i + 1) % n]
        p3 = points[(i + 2) % n] if (closed or i + 2 < n) else p2
        c1 = p1 + (p2 - p0) / 6
        c2 = p2 - (p3 - p1) / 6
        seg.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}")
    return " ".join(seg)


def _resample(run: np.ndarray, step: float) -> np.ndarray:
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(run, axis=0), axis=1))]
    if d[-1] < step:
        return run[[0, -1]]
    t = np.linspace(0, d[-1], max(2, int(round(d[-1] / step)) + 1))
    return np.column_stack([np.interp(t, d, run[:, i]) for i in range(2)])


UP = 4  # trace at 4x so antialiased edges land at sub-pixel positions


def contour_d(mask: np.ndarray, sigma: float = 2.2, step: float = 9.0,
              soft: np.ndarray | None = None) -> str:
    """Outline a region. `soft` (0..1 coverage) gives sub-pixel edges when known."""
    if soft is None:
        soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 1.2)
    big = cv2.resize(soft.astype(np.float32), None, fx=UP, fy=UP, interpolation=cv2.INTER_CUBIC)
    m = (big > 0.5).astype(np.uint8) * 255
    contours, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for c in contours:
        if cv2.contourArea(c) < 60 * UP * UP:
            continue
        pts = (c.reshape(-1, 2).astype(float) + 0.5) / UP
        pts = _resample(np.vstack([pts, pts[:1]]), 0.5)[:-1]
        cs = _corners(pts)
        if not cs:
            sm = _smooth_run(pts, sigma, closed=True)
            pt = _resample(np.vstack([sm, sm[:1]]), step)[:-1]
            parts.append(f"M{pt[0][0]:.1f} {pt[0][1]:.1f} " + _bezier(pt, True) + "Z")
            continue
        pts = np.roll(pts, -cs[0], axis=0)
        cs = [(i - cs[0]) % len(pts) for i in cs] + [len(pts)]
        d = [f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"]
        for a, b in zip(cs, cs[1:]):
            run = np.vstack([pts[a:b], pts[b % len(pts)][None]])
            sm = _smooth_run(run, sigma, closed=False)
            pt = _resample(sm, step)
            d.append(_bezier(pt, False) if len(pt) > 2 else f"L{pt[-1][0]:.1f} {pt[-1][1]:.1f}")
        parts.append(" ".join(d) + "Z")
    return " ".join(parts)


def gradient(name: str, coef: np.ndarray, mask: np.ndarray, img: np.ndarray) -> str:
    """Linear gradient along the direction the colour changes fastest."""
    gx, gy = coef[1], coef[2]
    direction = np.array([np.linalg.norm(gx), 0.0])
    # Use the green channel (largest variation) for the direction, signs kept.
    v = np.array([gx[1], gy[1]])
    v = v / (np.linalg.norm(v) or 1)
    ys, xs = np.nonzero(mask)
    t = xs * v[0] + ys * v[1]
    t0, t1 = np.percentile(t, 0.5), np.percentile(t, 99.5)
    rgb = img[ys, xs, :3]
    stops = []
    for f in np.linspace(0, 1, 7):
        tc = t0 + f * (t1 - t0)
        near = np.abs(t - tc) < (t1 - t0) / 24
        col = np.median(rgb[near], axis=0) if near.any() else rgb.mean(axis=0)
        stops.append(f'<stop offset="{f:.3f}" stop-color="#{int(col[0]):02X}{int(col[1]):02X}{int(col[2]):02X}"/>')
    p0 = (v * t0)
    p1 = (v * t1)
    del direction
    return (f'<linearGradient id="{name}" gradientUnits="userSpaceOnUse" '
            f'x1="{p0[0]:.1f}" y1="{p0[1]:.1f}" x2="{p1[0]:.1f}" y2="{p1[1]:.1f}">'
            + "".join(stops) + "</linearGradient>")


def fill_holes(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros_like(m)
    cv2.drawContours(out, contours, -1, 1, thickness=cv2.FILLED)
    return out.astype(bool)


def smooth(mask: np.ndarray, k: int) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    m = cv2.GaussianBlur(m.astype(np.float32), (0, 0), k / 4)
    return m > 0.5


def main() -> None:
    img = load()
    seg = segment(img)
    h, w = img.shape[:2]
    white = seg["white"]
    # Glyphs are white on a blue with almost no red: red is their coverage.
    near = cv2.dilate(white.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    glyph_soft = np.where(near, np.clip((img[..., 0] - 20) / 215, 0, 1), 0) * (img[..., 3] / 255)
    # Pages as whole shapes: the glyph holes are filled, the glyphs sit on top.
    dark = smooth(fill_holes(seg["dark"] | (white & fill_holes(seg["dark"]))), 31)
    light = smooth(fill_holes(seg["light"] | (white & ~dark)), 31)
    # Tuck the light page 3 px under the dark one so no hairline seam shows.
    grow = cv2.dilate(light.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    light = light | (grow & dark)
    defs = (gradient("light", seg["models"]["light"], seg["light"], img)
            + gradient("dark", seg["models"]["dark"], seg["dark"], img))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" '
           f'aria-label="Folith mark"><title>Folith mark</title><defs>{defs}</defs>'
           f'<path fill="url(#light)" fill-rule="evenodd" d="{contour_d(light)}"/>'
           f'<path fill="url(#dark)" fill-rule="evenodd" d="{contour_d(dark & ~light | (dark & ~seg["light"]))}"/>'
           f'<path fill="#FFFFFF" fill-rule="evenodd" d="{contour_d(white, sigma=1.2, step=5.0, soft=glyph_soft)}"/>'
           "</svg>\n")
    OUT.write_text(svg, encoding="utf-8")
    print(OUT, len(svg))


if __name__ == "__main__":
    main()
