"""A parametric model of the two pages in folith-mark-hires.png.

Each page is a rounded rectangle whose sides stay vertical while its top and
bottom edges slope (a shear, as in the original). The darker page comes to
the front below a sloped "interlock" line, so the lighter page shows above it.
`fit()` searches the parameters that best reproduce the traced regions.

    python3 docs/logo-exploration/page_model.py   # prints the fitted parameters
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


@dataclass
class Page:
    x0: float      # left side
    x1: float      # right side
    top: float     # top edge y at x0
    bottom: float  # bottom edge y at x0
    s_top: float   # top edge slope
    s_bot: float   # bottom edge slope
    r: float       # corner radius (in the page's own unsheared space)


@dataclass
class Model:
    light: Page = field(default_factory=lambda: Page(305, 1177, 61, 831, 0.23, 0.23, 110))
    dark: Page = field(default_factory=lambda: Page(78, 1054, 231, 982, 0.23, 0.26, 110))
    l_y0: float = 662.0   # interlock line y at x = 78
    l_s: float = 0.18     # interlock line slope
    l_r: float = 60.0     # rounding of the corners the interlock line makes


def rounded_rect(w: float, h: float, r: float, n: int = 24) -> np.ndarray:
    r = min(r, w / 2, h / 2)
    pts = []
    for cx, cy, a0 in ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
        for t in np.linspace(a0, a0 + 90, n):
            pts.append((cx + r * np.cos(np.radians(t)), cy + r * np.sin(np.radians(t))))
    return np.array(pts)


def page_polygon(p: Page) -> np.ndarray:
    """Round the corners in an unsheared w x h box, then shear it into place."""
    w = p.x1 - p.x0
    h = p.bottom - p.top
    local = rounded_rect(w, h, p.r)
    u, v = local[:, 0], local[:, 1] / h
    top = p.top + p.s_top * u
    bot = p.bottom + p.s_bot * u
    return np.column_stack([p.x0 + u, top + v * (bot - top)])


def fill(poly: np.ndarray, shape: tuple[int, int], scale: float) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    pts = np.round(poly * scale * 16).astype(np.int32)
    cv2.fillPoly(m, [pts], 1, lineType=cv2.LINE_8, shift=4)
    return m.astype(bool)


def open_disk(mask: np.ndarray, r: float) -> np.ndarray:
    """Morphological opening by a disk, via distance transforms (exact, fast)."""
    if r <= 0.5:
        return mask
    inside = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    core = inside > r
    out = cv2.distanceTransform((~core).astype(np.uint8), cv2.DIST_L2, 5)
    return out <= r


def regions(m: Model, shape: tuple[int, int], scale: float) -> dict[str, np.ndarray]:
    light = fill(page_polygon(m.light), shape, scale)
    dark = fill(page_polygon(m.dark), shape, scale)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w] / scale
    below = yy > m.l_y0 + m.l_s * (xx - 78)
    strip = open_disk(dark & below, m.l_r * scale)
    visible_light = open_disk(light & ~strip, m.l_r * scale)
    return {"light": visible_light, "dark": dark & ~visible_light,
            "light_full": light, "dark_full": dark, "strip": strip}


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return (a & b).sum() / max(1, (a | b).sum())


def targets(scale: float) -> dict[str, np.ndarray]:
    from trace_original import load, segment, fill_holes, smooth
    img = load()
    seg = segment(img)
    white = seg["white"]
    dark = smooth(fill_holes(seg["dark"] | (white & fill_holes(seg["dark"]))), 31)
    light = smooth(fill_holes(seg["light"] | (white & ~dark)), 31) & ~dark
    size = (int(img.shape[1] * scale), int(img.shape[0] * scale))
    down = lambda x: cv2.resize(x.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
    return {"light": down(light), "dark": down(dark)}


def score(m: Model, tgt: dict[str, np.ndarray], scale: float) -> float:
    reg = regions(m, tgt["light"].shape, scale)
    return iou(reg["light"], tgt["light"]) + iou(reg["dark"], tgt["dark"])


def fit(rounds: int = 6) -> Model:
    m = Model()
    for scale, steps in ((0.25, (16.0, 8.0, 4.0)), (0.5, (2.0, 1.0, 0.5))):
        tgt = targets(scale)
        best = score(m, tgt, scale)
        for step in steps:
            for _ in range(rounds):
                improved = False
                for obj, names in ((m.light, ("x0", "x1", "top", "bottom", "s_top", "s_bot", "r")),
                                   (m.dark, ("x0", "x1", "top", "bottom", "s_top", "s_bot", "r")),
                                   (m, ("l_y0", "l_s", "l_r"))):
                    for name in names:
                        unit = step * (0.0025 if name.startswith(("s_", "l_s")) else 1.0)
                        for sign in (1, -1):
                            old = getattr(obj, name)
                            setattr(obj, name, old + sign * unit)
                            s = score(m, tgt, scale)
                            if s > best + 1e-6:
                                best, improved = s, True
                            else:
                                setattr(obj, name, old)
                if not improved:
                    break
        print(f"scale {scale}: IoU sum {best:.4f}", flush=True)
    return m


if __name__ == "__main__":
    model = fit()
    (HERE / "page_model.json").write_text(json.dumps(asdict(model), indent=2))
    print(json.dumps(asdict(model), indent=2))
