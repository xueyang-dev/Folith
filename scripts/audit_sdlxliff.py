"""Read-only structural audit of SDLXLIFF samples for issue #145 Gate A.

Scans sample files and reports every observed `sdl:` element, attribute and
confirmation-state value, plus XLIFF 1.2 structural facts, so that the
compatibility matrix in docs/ can cite concrete per-file evidence.

Usage:
    python scripts/audit_sdlxliff.py [PATH ...]

With no arguments, audits tests/fixtures/sdlxliff/*.sdlxliff.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
SDL_NS_PREFIX = "http://sdl.com/FileTypes/SdlXliff"


def localname(tag: str) -> str:
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        prefix = "sdl" if uri.startswith(SDL_NS_PREFIX) else "x"
        return f"{prefix}:{local}"
    return tag


def audit_file(path: Path) -> dict:
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    facts: dict = {
        "file": path.name,
        "bytes": len(raw),
        "has_bom": raw.startswith(b"\xef\xbb\xbf"),
    }

    # Declared namespaces on the root.
    facts["root_namespaces"] = {
        k if k else "default": v for k, v in root.attrib.items() if k.startswith("xmlns")
    }
    facts["xliff_version"] = root.get("version")
    # ElementTree strips xmlns declarations from attrib, so recover the sdl
    # namespace URI from the element tags instead.
    sdl_uri = next(
        (
            el.tag[1:].partition("}")[0]
            for el in root.iter()
            if el.tag.startswith("{") and el.tag[1:].startswith(SDL_NS_PREFIX)
        ),
        None,
    )
    facts["sdl_version_attr"] = root.get(f"{{{sdl_uri}}}version") if sdl_uri else None

    files = root.findall(f"{{{XLIFF_NS}}}file")
    facts["file_count"] = len(files)
    fattrs: list[dict] = []
    for f in files:
        fattrs.append({k: v for k, v in f.attrib.items() if not k.startswith("xmlns")})
    facts["file_attrs"] = fattrs

    # Header children (what must be preserved verbatim).
    header_info = []
    for f in files:
        header = f.find(f"{{{XLIFF_NS}}}header")
        if header is None:
            header_info.append(None)
            continue
        counts = Counter(localname(el.tag) for el in header.iter())
        header_info.append(dict(counts))
    facts["header_children"] = header_info

    tus = root.findall(f".//{{{XLIFF_NS}}}trans-unit")
    facts["trans_unit_count"] = len(tus)
    groups = root.findall(f".//{{{XLIFF_NS}}}group")
    facts["group_count"] = len(groups)
    mrks = root.findall(f".//{{{XLIFF_NS}}}mrk")
    facts["mrk_mtypes"] = dict(Counter(m.get("mtype") for m in mrks))

    # Non-sdl, non-xliff inline namespaced elements inside body (vendor filters).
    foreign = Counter()
    for el in root.iter():
        if el.tag.startswith("{") and not el.tag.startswith(
            f"{{{XLIFF_NS}}}"
        ) and not el.tag.startswith(f"{{{SDL_NS_PREFIX}"):
            uri = el.tag[1:].partition("}")[0]
            foreign[uri] += 1
    facts["foreign_namespaces_in_body"] = dict(foreign)

    # All sdl: element and attribute names observed, with occurrence counts.
    sdl_elems: Counter = Counter()
    sdl_attrs: Counter = Counter()
    conf_values: Counter = Counter()
    seg_attr_values: dict[str, Counter] = {}
    seg_children: Counter = Counter()
    cmt_samples: list[str] = []
    locked_count = 0
    seg_count = 0

    for el in root.iter():
        if not el.tag.startswith(f"{{{SDL_NS_PREFIX}"):
            continue
        sdl_elems[localname(el.tag)] += 1
        for k, v in el.attrib.items():
            sdl_attrs[localname(k)] += 1
            if localname(k) == "conf":
                conf_values[v] += 1
            if localname(k) == "locked" and v.lower() == "true":
                locked_count += 1
        if localname(el.tag) == "sdl:seg":
            seg_count += 1
            for k, v in el.attrib.items():
                ln = localname(k)
                seg_attr_values.setdefault(ln, Counter())[v] += 1
            for child in el:
                seg_children[localname(child.tag)] += 1
                if localname(child.tag) in ("sdl:cmt", "sdl:comm") and len(cmt_samples) < 5:
                    cmt_samples.append(child.text or "")

    facts["sdl_elements"] = dict(sdl_elems)
    facts["sdl_attributes"] = dict(sdl_attrs)
    facts["seg_count"] = seg_count
    facts["conf_values"] = dict(conf_values)
    facts["seg_attribute_values"] = {
        k: dict(v) for k, v in seg_attr_values.items()
    }
    facts["seg_children"] = dict(seg_children)
    facts["locked_true_count"] = locked_count
    facts["comment_samples"] = cmt_samples

    # sdl:seg-defs children other than sdl:seg (e.g. sdl:cmt-defs at TU level).
    segdefs_children: Counter = Counter()
    for el in root.iter():
        if localname(el.tag) == "sdl:seg-defs":
            for child in el:
                seg_children.setdefault("__segdefs_child__", Counter())[localname(child.tag)] += 1
    if seg_children.get("__segdefs_child__"):
        segdefs_children = seg_children.pop("__segdefs_child__")
    facts["segdefs_children"] = dict(segdefs_children)

    # Where do sdl: elements attach? (parent element localname)
    parents: Counter = Counter()
    for parent in root.iter():
        for child in parent:
            if child.tag.startswith(f"{{{SDL_NS_PREFIX}"):
                parents[f"{localname(parent.tag)}->{localname(child.tag)}"] += 1
    facts["sdl_attachment_points"] = dict(parents)

    return facts


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(
        Path(__file__).resolve().parent.parent.glob("tests/fixtures/sdlxliff/*.sdlxliff")
    )
    all_facts = [audit_file(p) for p in paths]
    print(json.dumps(all_facts, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
