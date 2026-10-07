"""Typesetting for the original-layout renderer.

The engine is Typst: it is the only available typesetter in this environment
that shapes Arabic correctly *and* emits a text layer that extracts back as base
characters.  Measured 2026-10-07 on an Arabic+Chinese sample: the Typst output
answers ``search_for("مرحبا")``, while the same text through ``fitz.Story``
extracts as Arabic presentation forms and is not searchable.  Searchable target
text is an acceptance requirement, so the choice is not cosmetic.

Everything here is deterministic and local: no network, no model call.
"""
from __future__ import annotations

import functools
import pathlib
import sys
from dataclasses import dataclass, field

import typst

#: Directories searched for installable fonts, in platform order.
FONT_DIRS: tuple[str, ...] = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    "~/Library/Fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "C:/Windows/Fonts",
    "~/AppData/Local/Microsoft/Windows/Fonts",
)

#: Family candidates per role.  The first family that covers the characters is
#: used; a second family is appended when it adds coverage Typst would otherwise
#: have to substitute silently.
FAMILIES: dict[str, tuple[str, ...]] = {
    "serif": ("Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif",
              "Noto Serif", "Georgia"),
    "sans": ("Helvetica", "Arial", "Liberation Sans", "DejaVu Sans", "Noto Sans",
             "Verdana"),
    "mono": ("Courier New", "Liberation Mono", "DejaVu Sans Mono", "Menlo", "Consolas"),
}

#: Script fallbacks, tried in order, appended after the role family.
SCRIPT_FAMILIES: dict[str, tuple[str, ...]] = {
    "cjk": ("Songti SC", "STSong", "PingFang SC", "Hiragino Sans GB", "Heiti SC",
            "Noto Sans CJK SC", "Noto Serif CJK SC", "Source Han Sans SC",
            "Microsoft YaHei", "SimSun"),
    "arabic": ("Geeza Pro", "Al Nile", "Noto Naskh Arabic", "Noto Sans Arabic",
               "Amiri", "Times New Roman"),
    "hebrew": ("Arial Hebrew", "Noto Sans Hebrew", "Times New Roman"),
    "cyrillic": ("Times New Roman", "Arial", "DejaVu Sans", "Noto Sans"),
    "devanagari": ("Kohinoor Devanagari", "Noto Sans Devanagari", "DejaVu Sans"),
}

ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
                 (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))
CJK_RANGES = ((0x2E80, 0x2EFF), (0x3000, 0x303F), (0x3040, 0x30FF),
              (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
              (0xFF00, 0xFFEF), (0x20000, 0x2FA1F))
CYRILLIC_RANGES = ((0x0400, 0x04FF), (0x0500, 0x052F))
DEVANAGARI_RANGES = ((0x0900, 0x097F),)


def _in_ranges(code: int, ranges) -> bool:
    return any(low <= code <= high for low, high in ranges)


def script_of(char: str) -> str:
    code = ord(char)
    if _in_ranges(code, ARABIC_RANGES):
        return "arabic"
    if _in_ranges(code, HEBREW_RANGES):
        return "hebrew"
    if _in_ranges(code, CJK_RANGES):
        return "cjk"
    if _in_ranges(code, CYRILLIC_RANGES):
        return "cyrillic"
    if _in_ranges(code, DEVANAGARI_RANGES):
        return "devanagari"
    return "latin"


def text_scripts(text: str) -> set[str]:
    return {script_of(ch) for ch in text if not ch.isspace()}


def dominant_direction(text: str) -> str:
    """RTL when the first strong character is Arabic/Hebrew, else LTR."""
    for char in text:
        script = script_of(char)
        if script in {"arabic", "hebrew"}:
            return "rtl"
        if char.isalpha():
            return "ltr"
    return "ltr"


@functools.lru_cache(maxsize=1)
def available_font_files() -> tuple[pathlib.Path, ...]:
    found: list[pathlib.Path] = []
    for raw in FONT_DIRS:
        base = pathlib.Path(raw).expanduser()
        if not base.is_dir():
            continue
        for pattern in ("*.ttf", "*.otf", "*.ttc", "*.otc", "**/*.ttf", "**/*.otf"):
            for path in sorted(base.glob(pattern)):
                if path.is_file():
                    found.append(path)
    return tuple(dict.fromkeys(found))


@functools.lru_cache(maxsize=512)
def _font_faces(path_str: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """(family, weight, italic, covered codepoints) for every face in a file."""
    from fontTools.ttLib import TTFont, TTCollection

    # fontTools warns loudly about cosmetic damage in shipped system fonts; the
    # warnings are not actionable and would drown the renderer's own output.
    import logging
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    logging.getLogger("fontTools.ttLib").setLevel(logging.ERROR)

    path = pathlib.Path(path_str)
    faces = []
    try:
        if path.suffix.lower() in {".ttc", ".otc"}:
            collection = TTCollection(str(path), lazy=True)
            fonts = list(collection.fonts)
        else:
            fonts = [TTFont(str(path), lazy=True, fontNumber=0)]
    except Exception:
        return ()
    for font in fonts:
        try:
            names = font["name"]
            family = None
            for name_id in (16, 1):
                record = names.getDebugName(name_id)
                if record:
                    family = str(record).split("\x00")[0].strip()
                    break
            if not family:
                continue
            os2 = font.get("OS/2")
            weight = int(getattr(os2, "usWeightClass", 400) or 400)
            italic = bool(getattr(os2, "fsSelection", 0) & 1) or bool(
                getattr(font["head"], "macStyle", 0) & 2)
            cmap = font.getBestCmap() or {}
            faces.append((family, (weight, int(italic)), tuple(sorted(cmap))))
        except Exception:
            continue
        finally:
            try:
                font.close()
            except Exception:
                pass
    return tuple(faces)


@functools.lru_cache(maxsize=1)
def font_index() -> tuple[dict[str, tuple[tuple[int, frozenset], ...]], ...]:
    """family -> faces; and codepoint -> families that cover it."""
    by_family: dict[str, list[tuple[int, frozenset]]] = {}
    for path in available_font_files():
        for family, (weight, italic), codes in _font_faces(str(path)):
            by_family.setdefault(family, []).append(
                (weight, frozenset(codes)))
    families = {name: tuple(faces) for name, faces in by_family.items()}
    return (families,)


def family_covers(family: str, codepoints: set[int]) -> bool:
    faces = font_index()[0].get(family)
    if not faces:
        return False
    union: set[int] = set()
    for _weight, codes in faces:
        union |= codes
    return codepoints <= union


def coverage_report(text: str, families: list[str]) -> list[str]:
    """Return the characters no listed family can draw."""
    codepoints = {ord(ch) for ch in text if not ch.isspace()}
    covered: set[int] = set()
    index = font_index()[0]
    for family in families:
        for _weight, codes in index.get(family, ()):
            covered |= codes
    return sorted({chr(c) for c in codepoints - covered})


@dataclass
class FontChoice:
    families: list[str]
    missing: list[str] = field(default_factory=list)


def choose_fonts(text: str, *, font_style: str = "", bold: bool = False,
                 italic: bool = False) -> FontChoice:
    """Pick a Typst font list that can actually draw ``text``.

    ``font_style`` is the source font name recorded by ingestion; it is only a
    hint for the role (serif/sans/mono) because the source font is usually a
    subsetted embedded face that cannot render new text.
    """
    lowered = (font_style or "").lower()
    if any(token in lowered for token in ("mono", "courier", "consol", "menlo")):
        role = "mono"
    elif any(token in lowered for token in ("sans", "arial", "helvet", "calibri",
                                            "verdana", "roboto", "noto sans",
                                            "dejavu sans", "tahoma")):
        role = "sans"
    else:
        role = "serif"

    scripts = text_scripts(text)
    wanted: list[str] = list(FAMILIES[role])
    if "cjk" in scripts:
        wanted += list(SCRIPT_FAMILIES["cjk"])
    if "arabic" in scripts:
        wanted += list(SCRIPT_FAMILIES["arabic"])
    if "hebrew" in scripts:
        wanted += list(SCRIPT_FAMILIES["hebrew"])

    index = font_index()[0]
    families: list[str] = []
    covered: set[int] = set()
    codepoints = {ord(ch) for ch in text if not ch.isspace()}

    # A role family is only useful when it covers the script we are setting.
    # Latin families are preferred first so mixed text keeps its original look.
    for script in ("latin",) + tuple(sorted(scripts - {"latin"})):
        script_codes = {c for c in codepoints if script_of(chr(c)) == script}
        if not script_codes:
            continue
        pool = list(FAMILIES[role])
        if script != "latin":
            pool += list(SCRIPT_FAMILIES.get(script, ()))
        for family in pool:
            if family in families:
                continue
            faces = index.get(family)
            if not faces:
                continue
            union: set[int] = set()
            for _weight, codes in faces:
                union |= codes
            if script_codes <= union:
                families.append(family)
                covered |= script_codes
                break

    # Second pass: any remaining family that closes a gap.
    for family in wanted:
        if codepoints <= covered:
            break
        if family in families:
            continue
        for _weight, codes in index.get(family, ()):
            if codes & (codepoints - covered):
                families.append(family)
                covered |= set(codes)
                break

    if not families:
        families = ["Times New Roman"]
    missing = sorted({chr(c) for c in codepoints - covered})
    return FontChoice(families=families, missing=missing)


# --------------------------------------------------------------------------
# Typst compilation
# --------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape Typst markup so user text is typeset literally."""
    out = []
    for char in text:
        if char in "\\#$*_`<>@~[]":
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\ ")
        elif char == "\t":
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class Block:
    """One text block to typeset at an absolute position on a page."""

    x: float           # page points, left edge of the box
    y: float           # page points, top edge of the box
    width: float       # box width
    height: float      # space available below y
    text: str
    size: float        # font size in points
    families: list[str]
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: str = "ltr"
    align: str = "start"
    line_height: float = 1.22
    paragraph_indent: float = 0.0
    weight: str = "regular"
    italic: bool = False


def _styled_body(block: Block) -> str:
    """Typst content for one block, without the enclosing block container."""
    color = '"#' + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in block.color) + '"'
    align = {"start": "start", "left": "start", "right": "end",
             "center": "center", "justify": "start"}.get(block.align, "start")
    indent = (f"first-line-indent: {block.paragraph_indent:.2f}pt,"
              if block.paragraph_indent else "")
    weight = ', weight: "bold"' if block.weight == "bold" else ""
    style = ', style: "italic"' if block.italic else ""
    return (
        f"#set text(font: ({', '.join(_quoted(f) for f in block.families)}), "
        f"size: {block.size:.2f}pt, fill: rgb({color}), "
        f"lang: \"{_lang_for(block)}\"{weight}{style})\n"
        f"#set par(leading: {block.line_height - 1:.3f}em, justify: "
        f"{'true' if block.align == 'justify' else 'false'}"
        f"{',' + indent if indent else ''})\n"
        f"#text(dir: {block.direction})[\n"
        f"#align({align})[{_escape(block.text)}]\n]\n"
    )


def _block_source(block: Block, *, exact_height: bool) -> str:
    """Typst body for one block, wrapped in a width-constrained container.

    The block is always ``height: auto``.  A Typst block with an explicit height
    silently clips its content instead of overflowing, which would make any
    fitting check built on it report "fits" for text that is far too tall.
    """
    return (f"#block(width: {block.width:.2f}pt, height: auto, inset: 0pt, "
            f"outset: 0pt, fill: none, stroke: none)[\n"
            f"{_styled_body(block)}]\n")


_MEASURE_TEMPLATE = (
    "#set page(width: {width:.2f}pt, height: {tall:.2f}pt, margin: 0pt, fill: none)\n"
    "#context {{\n"
    "  let h = measure(block(width: {width:.2f}pt)[\n{body}\n]).height\n"
    "  [#metadata((h: h)) <opdf>]\n"
    "}}\n"
)


@functools.lru_cache(maxsize=20000)
def _measure_cached(text: str, width: float, size: float,
                    families: tuple, color: tuple, direction: str, align: str,
                    line_height: float, weight: str, italic: bool) -> float:
    block = Block(x=0.0, y=0.0, width=width, height=0.0, text=text, size=size,
                  families=list(families), color=color, direction=direction,
                  align=align, line_height=line_height, weight=weight,
                  italic=italic)
    source = _MEASURE_TEMPLATE.format(width=block.width, tall=100000.0,
                                      body=_styled_body(block))
    import json as _json
    raw_result = typst.query(source.encode("utf-8"), "<opdf>")
    found = _json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    if not found:
        return float("inf")
    value = found[0].get("value") or {}
    raw = value.get("h")
    if raw is None:
        return float("inf")
    return _to_points(raw)


def _to_points(value) -> float:
    text = str(value).strip()
    for suffix, scale in (("pt", 1.0), ("mm", 72.0 / 25.4), ("cm", 72.0 / 2.54),
                          ("in", 72.0), ("em", 0.0)):
        if text.endswith(suffix):
            number = text[:-len(suffix)].strip()
            try:
                return float(number) * scale
            except ValueError:
                return float("inf")
    try:
        return float(text)
    except ValueError:
        return float("inf")


def measure_height(block: Block) -> float:
    """Exact laid-out height of ``block`` in points, measured by Typst."""
    return _measure_cached(
        block.text, round(block.width, 2), round(block.size, 3),
        tuple(block.families), tuple(round(c, 4) for c in block.color),
        block.direction, block.align, round(block.line_height, 3),
        block.weight, bool(block.italic))


def fit_for_height(block: Block, max_height: float, *, min_size: float,
                   max_size: float) -> tuple[float, float, bool]:
    """Largest size whose measured height fits ``max_height``.

    Returns ``(size, height, overflowed)``.  The search uses Typst's own
    measurement rather than a rendered page count, and caches every probe.
    """
    if max_size <= min_size:
        height = measure_height(Block(**{**block.__dict__, "size": min_size}))
        return min_size, height, height > max_height + 0.5

    top = Block(**{**block.__dict__, "size": max_size})
    height = measure_height(top)
    if height <= max_height + 0.5:
        return max_size, height, False

    low_block = Block(**{**block.__dict__, "size": min_size})
    low_height = measure_height(low_block)
    if low_height > max_height + 0.5:
        return min_size, low_height, True

    # Height grows faster than linearly with the size (a larger size both makes
    # each line taller and fits fewer words per line), so seed with a quadratic
    # estimate and then bisect the small remaining bracket.
    ratio = max_height / max(height, 1e-6)
    guess = max_size * (ratio ** 0.5)
    guess = max(min_size, min(max_size, guess))
    low, high = min_size, max_size
    candidate = guess
    for _ in range(10):
        candidate = max(min_size, min(max_size, candidate))
        probe = Block(**{**block.__dict__, "size": candidate})
        probe_height = measure_height(probe)
        if probe_height <= max_height + 0.5:
            low = candidate
            if high - low < 0.25:
                break
            candidate = (candidate + high) / 2
        else:
            high = candidate
            if high - low < 0.25:
                break
            candidate = (low + candidate) / 2
    final = Block(**{**block.__dict__, "size": round(low, 2)})
    return round(low, 2), measure_height(final), False


def _lang_for(block: Block) -> str:
    scripts = text_scripts(block.text)
    if "arabic" in scripts:
        return "ar"
    if "cjk" in scripts:
        return "zh"
    if "hebrew" in scripts:
        return "he"
    return "en"


@functools.lru_cache(maxsize=4096)
def _compile_cached(source: str) -> bytes:
    return typst.compile(source.encode("utf-8"))


def fits(block: Block) -> bool:
    """True when the block's measured height fits its own box."""
    return measure_height(block) <= max(block.height, 0.0) + 0.5


def fit_size(block: Block, *, min_size: float, max_size: float) -> tuple[float, bool]:
    """Backwards-compatible wrapper around :func:`fit_for_height`."""
    size, _height, overflowed = fit_for_height(
        block, max(block.height, 0.0), min_size=min_size, max_size=max_size)
    return size, overflowed


@functools.lru_cache(maxsize=1024)
def _ink_line_em(families: tuple, direction: str, sample: str) -> float:
    """Rendered line-box height of ``sample`` in em, measured from a real PDF.

    Typst's own ``measure`` reports its line box (about 0.66–0.70em for these
    fonts), while a viewer sees the glyph box (about 1.0em).  Typesetting at the
    source document's Latin leading therefore makes CJK lines collide even
    though Typst reports the paragraph as fitting.
    """
    import pymupdf

    probe = Block(x=0.0, y=0.0, width=2000.0, height=0.0, text=sample, size=20.0,
                  families=list(families), direction=direction, line_height=1.0)
    data = render_page(2000.0, 200.0, [probe])
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        tallest = 0.0
        for block in doc[0].get_text("dict").get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                box = line["bbox"]
                tallest = max(tallest, (box[3] - box[1]) / 20.0)
    return tallest or 1.0


#: A probe per script rather than per string: the rendered line height depends
#: on the font and script, not on the particular words, and probing per slot
#: would cost an extra Typst render for every block.
_PROBES = {"cjk": "汉", "arabic": "مرحبا",
           "hebrew": "שלום", "latin": "Hxg"}


def probe_sample(text: str) -> str:
    scripts = text_scripts(text) or {"latin"}
    ordered = [name for name in ("latin", "cjk", "arabic", "hebrew")
               if name in scripts]
    return "".join(_PROBES.get(name, "Hxg") for name in ordered) or "Hxg"


def ink_pad(size: float) -> float:
    """Extra height a rendered line's glyph box needs below its measured box."""
    return 0.15 * max(0.0, float(size))


def minimum_line_height(families, direction: str, text: str) -> float:
    """Smallest line-advance multiple that keeps rendered lines from touching."""
    sample = probe_sample(text)
    families = tuple(families)
    natural = measure_height(Block(
        x=0.0, y=0.0, width=100000.0, height=0.0, text=sample, size=20.0,
        families=list(families), direction=direction, line_height=1.0)) / 20.0
    ink = _ink_line_em(families, direction, sample)
    return max(1.0, min(2.2, 1.0 + ink - natural + 0.02))


def render_page(page_width: float, page_height: float, blocks: list[Block]) -> bytes:
    """Compile one page of absolutely placed text blocks."""
    parts = [
        f"#set page(width: {page_width:.2f}pt, height: {page_height:.2f}pt, "
        f"margin: 0pt, fill: none)\n"
    ]
    seen: set[tuple] = set()
    for block in blocks:
        key = (round(block.x, 2), round(block.y, 2), block.text, block.size,
               round(block.width, 2), tuple(block.families), block.color,
               block.direction, block.align, block.weight, block.italic)
        if key in seen:
            continue
        seen.add(key)
        body = _block_source(block, exact_height=False)
        parts.append(
            f"#place(dx: {block.x:.2f}pt, dy: {block.y:.2f}pt)[\n{body}]\n")
    return _compile_cached("".join(parts))


def typst_version() -> str:
    try:
        return str(getattr(typst, "__version__", "") or "unknown")
    except Exception:
        return "unknown"


def font_dirs_for_report() -> list[str]:
    return [str(pathlib.Path(d).expanduser()) for d in FONT_DIRS
            if pathlib.Path(d).expanduser().is_dir()]


def platform_tag() -> str:
    return f"{sys.platform}"
