"""Read-only structural audit of MQXLIFF (memoQ XLIFF) samples for issue #146 Gate A.

Scans sample files and reports every observed `mq:` element, attribute and
segment-status value, plus XLIFF 1.2 structural facts, so that the
compatibility matrix in docs/ can cite concrete per-file evidence.

Key dialect fact: memoQ writes the mq: namespace with the literal URI
``MQXliff`` (not a URL), and sometimes re-declares it as a default namespace
mid-document (observed on <mq:commitinfos>), so detection must match the URI
string, never the prefix.

Usage:
    python scripts/audit_mqxliff.py [PATH ...]

With no arguments, audits tests/fixtures/mqxliff/*.mqxliff.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
MQ_NS_URIS = {"MQXliff", "MQXLIFF"}


def _ns_uri(tag: str) -> str | None:
    if tag.startswith("{"):
        return tag[1:].partition("}")[0]
    return None


def _is_mq(tag: str) -> bool:
    uri = _ns_uri(tag)
    return uri is not None and uri.upper() == "MQXLIFF"


def localname(tag: str) -> str:
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        prefix = "mq" if uri.upper() == "MQXLIFF" else "x"
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

    facts["xliff_version"] = root.get("version")
    # URI(s) actually used by mq-namespaced elements; the literal "MQXliff"
    # string is not recoverable from root.attrib because ElementTree strips
    # xmlns declarations.
    mq_uris = sorted({_ns_uri(el.tag) for el in root.iter() if _is_mq(el.tag)})
    facts["mq_ns_uris_in_use"] = mq_uris
    facts["root_namespaces"] = {
        k if k else "default": v for k, v in root.attrib.items() if k.startswith("xmlns")
    }
    # Default-namespace re-declarations of MQXliff deeper in the tree.
    # ElementTree strips xmlns declarations from attrib, so detect them on the
    # raw bytes instead.
    defaults = Counter(
        m.group(1).strip() or "(implicit on root)"
        for m in re.finditer(r'<([a-zA-Z:][^\s>]*)[^>]*?\sxmlns="(MQXliff)"', raw.decode("utf-8-sig"))
    )
    facts["mq_default_ns_redeclarations"] = dict(defaults)

    files = root.findall(f"{{{XLIFF_NS}}}file")
    facts["file_count"] = len(files)
    facts["file_attrs"] = [
        {k if k.startswith("xmlns") else localname(k): v for k, v in f.attrib.items()}
        for f in files
    ]
    tool = root.find(f".//{{{XLIFF_NS}}}tool")
    facts["tool"] = dict(tool.attrib) if tool is not None else None

    header_info = []
    for f in files:
        header = f.find(f"{{{XLIFF_NS}}}header")
        if header is None:
            header_info.append(None)
            continue
        header_info.append(dict(Counter(localname(el.tag) for el in header.iter())))
    facts["header_children"] = header_info

    tus = root.findall(f".//{{{XLIFF_NS}}}trans-unit")
    facts["trans_unit_count"] = len(tus)
    facts["seg_source_count"] = len(root.findall(f".//{{{XLIFF_NS}}}seg-source"))
    mrks = root.findall(f".//{{{XLIFF_NS}}}mrk")
    facts["mrk_count"] = len(mrks)

    # trans-unit level mq: attributes (the memoQ segment-status carrier).
    tu_mq_attrs: Counter = Counter()
    status_values: Counter = Counter()
    locked_values: Counter = Counter()
    proofread_values: Counter = Counter()
    translate_no = 0
    for tu in tus:
        if tu.get("translate", "").lower() == "no":
            translate_no += 1
        for k, v in tu.attrib.items():
            if _ns_uri(k) and _ns_uri(k).upper() == "MQXLIFF":
                tu_mq_attrs[localname(k)] += 1
                if localname(k) == "mq:status":
                    status_values[v] += 1
                if localname(k) == "mq:locked":
                    locked_values[v] += 1
                if localname(k) == "mq:segmentproofread":
                    proofread_values[v] += 1
    facts["tu_mq_attributes"] = dict(tu_mq_attrs)
    facts["status_values"] = dict(status_values)
    # memoQ writes presence-as-value for flags: the value is the local name
    # ("locked"), never "true"/"false" — detection must be presence-based.
    facts["locked_values"] = dict(locked_values)
    facts["proofread_values"] = dict(proofread_values)
    facts["tu_translate_no_count"] = translate_no

    # All mq: element and attribute names observed anywhere, with counts.
    mq_elems: Counter = Counter()
    mq_attrs: Counter = Counter()
    for el in root.iter():
        if _is_mq(el.tag):
            mq_elems[localname(el.tag)] += 1
        for k in el.attrib:
            if _ns_uri(k) and _ns_uri(k).upper() == "MQXLIFF":
                mq_attrs[localname(k)] += 1
    facts["mq_elements"] = dict(mq_elems)
    facts["mq_attributes"] = dict(mq_attrs)

    # Inline tag markup inside source/target (standard XLIFF bpt/ept/ph vs
    # native tags) and where mq: elements attach.
    inline_tags: Counter = Counter()
    for el in root.iter():
        if el.tag in (
            f"{{{XLIFF_NS}}}bpt", f"{{{XLIFF_NS}}}ept", f"{{{XLIFF_NS}}}ph",
            f"{{{XLIFF_NS}}}it", f"{{{XLIFF_NS}}}x",
        ):
            inline_tags[localname(el.tag)] += 1
    facts["standard_inline_tags"] = dict(inline_tags)

    parents: Counter = Counter()
    for parent in root.iter():
        for child in parent:
            if _is_mq(child.tag):
                parents[f"{localname(parent.tag)}->{localname(child.tag)}"] += 1
    facts["mq_attachment_points"] = dict(parents)

    # Where does the segment status live: confirm one trans-unit == one
    # segment (no seg-source/mrk splitting) in this file.
    facts["one_tu_one_segment"] = facts["seg_source_count"] == 0 and facts["mrk_count"] == 0

    # Standard <note> vs mq:comment duplication.
    facts["standard_note_count"] = len(root.findall(f".//{{{XLIFF_NS}}}note"))

    # Foreign namespaces in body (vendor extensions other than mq:).
    foreign = Counter()
    for el in root.iter():
        uri = _ns_uri(el.tag)
        if uri and uri.upper() != "MQXLIFF" and uri != XLIFF_NS:
            foreign[uri] += 1
    facts["foreign_namespaces"] = dict(foreign)

    return facts


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(
        Path(__file__).resolve().parent.parent.glob("tests/fixtures/mqxliff/*.mqxliff")
    )
    all_facts = [audit_file(p) for p in paths]
    print(json.dumps(all_facts, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
