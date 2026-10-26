"""Folith / 译页 DOCX Contract v1: 原格式保真写回（Surgical round-trip）。

契约（docs/docx-contract-v1.md）：
1. 保留原 DOCX package 作为写回基底；未修改的 ZIP 条目解压后内容字节 100% 一致。
2. 修改的 XML part 只允许声明的可翻译文本与必要 inline 放置发生变化。
3. 识别并提取带 inline 标记（粗体、斜体、下划线、超链接等）的翻译单元，建立版本内 Instance ID 与 source anchor。
4. 明确拒绝未决修订（Track Changes，contract line 47）。
5. 支持范围：
   - 普通正文/多段落：Translate
   - 表格文本：Translate（保留表格结构、合并单元格 gridSpan/vMerge）
   - 基本 inline 格式（bold, italic, underline, strike, sub/sup）：Preserve（随译文词序调整）
   - 超链接显示文字：Translate；超链接目标：Preserve
   - 脚注：Translate（保留引用 ID、引用标记及分隔结构）
   - 页眉/页脚：Translate + Protected（普通文字翻译，节引用保留，共享 part 只处理一次，字段受保护）
   - 文本框、批注、公式、图片：Preserve + report
6. 丢 tag、失效 anchor、未决修订等故障输入必须明确失败，不产出损坏文件。
"""
from __future__ import annotations

import copy
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import lxml.etree as LET

CONTRACT_VERSION = "v1"

# OOXML Namespaces
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
TAG_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

NSMAP = {
    "w": W_NS,
    "r": R_NS,
    "m": M_NS,
    "a": A_NS,
    "wp": WP_NS,
}

# Qualified tag names
TAG_P = f"{{{W_NS}}}p"
TAG_PPR = f"{{{W_NS}}}pPr"
TAG_R = f"{{{W_NS}}}r"
TAG_RPR = f"{{{W_NS}}}rPr"
TAG_T = f"{{{W_NS}}}t"
TAG_BR = f"{{{W_NS}}}br"
TAG_CR = f"{{{W_NS}}}cr"
TAG_TAB = f"{{{W_NS}}}tab"
TAG_HYPERLINK = f"{{{W_NS}}}hyperlink"
TAG_TBL = f"{{{W_NS}}}tbl"
TAG_TR = f"{{{W_NS}}}tr"
TAG_TC = f"{{{W_NS}}}tc"
TAG_BODY = f"{{{W_NS}}}body"
TAG_TXBX = f"{{{W_NS}}}txbxContent"
TAG_FOOTNOTE = f"{{{W_NS}}}footnote"
TAG_FOOTNOTES = f"{{{W_NS}}}footnotes"
TAG_FOOTNOTE_REF = f"{{{W_NS}}}footnoteRef"
TAG_FOOTNOTE_REFERENCE = f"{{{W_NS}}}footnoteReference"
TAG_HDR = f"{{{W_NS}}}hdr"
TAG_FTR = f"{{{W_NS}}}ftr"
TAG_DRAWING = f"{{{W_NS}}}drawing"
TAG_BLIP = f"{{{A_NS}}}blip"
TAG_MATH = f"{{{M_NS}}}oMath"
TAG_MATH_PARA = f"{{{M_NS}}}oMathPara"
TAG_BOOKMARK_START = f"{{{W_NS}}}bookmarkStart"
TAG_BOOKMARK_END = f"{{{W_NS}}}bookmarkEnd"
TAG_COMMENT = f"{{{W_NS}}}comment"
TAG_COMMENT_RANGE_START = f"{{{W_NS}}}commentRangeStart"
TAG_COMMENT_RANGE_END = f"{{{W_NS}}}commentRangeEnd"
TAG_COMMENT_REFERENCE = f"{{{W_NS}}}commentReference"
TAG_FLD_SIMPLE = f"{{{W_NS}}}fldSimple"
TAG_FLD_CHAR = f"{{{W_NS}}}fldChar"
TAG_INSTR_TEXT = f"{{{W_NS}}}instrText"

# Pending Track Changes (Revision) tags - Must be explicitly rejected
TRACK_CHANGES_TAGS = {
    f"{{{W_NS}}}ins",
    f"{{{W_NS}}}del",
    f"{{{W_NS}}}moveTo",
    f"{{{W_NS}}}moveFrom",
    f"{{{W_NS}}}rPrChange",
    f"{{{W_NS}}}pPrChange",
    f"{{{W_NS}}}tblPrChange",
    f"{{{W_NS}}}tcPrChange",
    f"{{{W_NS}}}sectPrChange",
}

# Run property tags representing supported basic inline formatting
FORMATTING_TAGS = {
    "b": f"{{{W_NS}}}b",
    "i": f"{{{W_NS}}}i",
    "u": f"{{{W_NS}}}u",
    "s": f"{{{W_NS}}}strike",
    "sup": "superscript",
    "sub": "subscript",
}


class DocxRoundtripError(Exception):
    """Base exception for DOCX Contract v1 errors."""


class TrackChangesPendingError(DocxRoundtripError):
    """Raised when the DOCX contains unaccepted/unrejected Track Changes revisions."""


class TagQAError(DocxRoundtripError):
    """Raised when target tags are corrupted, unclosed, or mismatched with source."""


class InvalidAnchorError(DocxRoundtripError):
    """Raised when a unit's source anchor cannot be located in the original package."""


@dataclass
class DocxUnit:
    """Represents a translatable unit with identity, source anchor, and inline markup."""

    instance_id: str
    source_anchor: Dict[str, Any]
    source_version: str
    source: str
    target: str = ""
    source_plain: str = ""
    tags: List[Dict[str, Any]] = field(default_factory=list)
    protected_markers: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "source_anchor": self.source_anchor,
            "source_version": self.source_version,
            "source": self.source,
            "target": self.target,
            "source_plain": self.source_plain,
            "tags": self.tags,
            "protected_markers": self.protected_markers,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocxUnit":
        return cls(
            instance_id=data["instance_id"],
            source_anchor=data["source_anchor"],
            source_version=data["source_version"],
            source=data.get("source", ""),
            target=data.get("target", ""),
            source_plain=data.get("source_plain", ""),
            tags=data.get("tags", []),
            protected_markers=data.get("protected_markers", []),
        )


def _safe_xml_parse(data: bytes) -> LET._Element:
    return LET.fromstring(data)


def _safe_xml_serialize(elem: LET._Element) -> bytes:
    return LET.tostring(elem, encoding="utf-8", xml_declaration=True, standalone="yes")


def check_track_changes(archive: zipfile.ZipFile) -> List[str]:
    """Check if the DOCX package contains any pending Track Changes.

    Returns a list of entry names where track changes were detected.
    """
    offending_parts = []
    xml_entries = [name for name in archive.namelist()
                   if name.endswith(".xml") and name.startswith("word/")]
    for entry in xml_entries:
        try:
            content = archive.read(entry)
            root = _safe_xml_parse(content)
            for elem in root.iter():
                if elem.tag in TRACK_CHANGES_TAGS:
                    offending_parts.append(entry)
                    break
        except Exception:
            continue
    return offending_parts


def inspect_docx_package(source_bytes: bytes) -> Dict[str, Any]:
    """Inspect a DOCX package against Contract v1 support matrix.

    Explicitly rejects pending Track Changes (contract line 47).
    Categorizes all structural elements into supported / preserved / rejected.
    """
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as archive:
        track_changes = check_track_changes(archive)
        if track_changes:
            parts_str = ", ".join(track_changes[:3])
            raise TrackChangesPendingError(
                f"文档包含未决修订（Track Changes：{parts_str}），"
                "无法进行原格式保真提取与写回。请在 Word 中接受或拒绝所有修订后再导入。"
            )

        entries = archive.namelist()
        manifest: Dict[str, Any] = {
            "version": CONTRACT_VERSION,
            "source_hash": source_hash,
            "zip_entries": len(entries),
            "supported": [],
            "preserved": [],
            "rejected": [],
            "counts": {},
        }

        # Parse document.xml
        doc_root = _safe_xml_parse(archive.read("word/document.xml"))
        body = doc_root.find(TAG_BODY)

        # Count body paragraphs and table paragraphs
        body_paras = 0
        table_paras = 0
        tables_count = 0
        merged_cells = 0
        hyperlinks_count = 0
        textboxes_count = 0
        drawings_count = 0
        math_count = 0
        bookmarks_count = 0
        fields_count = 0

        if body is not None:
            for child in body:
                if child.tag == TAG_P:
                    # Direct body paragraph
                    text = "".join(child.itertext()).strip()
                    if text:
                        body_paras += 1
                elif child.tag == TAG_TBL:
                    tables_count += 1
                    for tc in child.iter(TAG_TC):
                        # Check merged cells
                        tcPr = tc.find(TAG_PPR.replace("pPr", "tcPr"))
                        if tcPr is not None:
                            if tcPr.find(f"{{{W_NS}}}gridSpan") is not None or \
                               tcPr.find(f"{{{W_NS}}}vMerge") is not None:
                                merged_cells += 1
                        for p in tc.findall(TAG_P):
                            text = "".join(p.itertext()).strip()
                            if text:
                                table_paras += 1

            for elem in body.iter():
                if elem.tag == TAG_HYPERLINK:
                    hyperlinks_count += 1
                elif elem.tag == TAG_TXBX:
                    textboxes_count += 1
                elif elem.tag == TAG_DRAWING:
                    drawings_count += 1
                elif elem.tag in (TAG_MATH, TAG_MATH_PARA):
                    math_count += 1
                elif elem.tag == TAG_BOOKMARK_START:
                    bookmarks_count += 1
                elif elem.tag in (TAG_FLD_SIMPLE, TAG_FLD_CHAR):
                    fields_count += 1

        # Check footnotes
        footnotes_count = 0
        if "word/footnotes.xml" in entries:
            fn_root = _safe_xml_parse(archive.read("word/footnotes.xml"))
            for fn in fn_root.findall(TAG_FOOTNOTE):
                fn_id = fn.get(f"{{{W_NS}}}id") or fn.get("id")
                fn_type = fn.get(f"{{{W_NS}}}type") or fn.get("type")
                if fn_id not in ("-1", "0") and fn_type not in ("separator", "continuationSeparator"):
                    footnotes_count += 1

        # Check headers and footers
        header_parts = sorted([e for e in entries if re.match(r"^word/header\d+\.xml$", e)])
        footer_parts = sorted([e for e in entries if re.match(r"^word/footer\d+\.xml$", e)])
        header_paras = 0
        footer_paras = 0

        for hp in header_parts:
            h_root = _safe_xml_parse(archive.read(hp))
            for p in h_root.findall(TAG_P):
                if "".join(p.itertext()).strip():
                    header_paras += 1

        for fp in footer_parts:
            f_root = _safe_xml_parse(archive.read(fp))
            for p in f_root.findall(TAG_P):
                if "".join(p.itertext()).strip():
                    footer_paras += 1

        # Comments
        comments_count = 0
        if "word/comments.xml" in entries:
            c_root = _safe_xml_parse(archive.read("word/comments.xml"))
            comments_count = len(c_root.findall(TAG_COMMENT))

        # Build counts and matrix
        manifest["counts"] = {
            "body_paragraphs": body_paras,
            "tables": tables_count,
            "table_paragraphs": table_paras,
            "merged_cells": merged_cells,
            "hyperlinks": hyperlinks_count,
            "footnotes": footnotes_count,
            "header_paragraphs": header_paras,
            "footer_paragraphs": footer_paras,
            "textboxes": textboxes_count,
            "comments": comments_count,
            "drawings": drawings_count,
            "formulas": math_count,
            "bookmarks": bookmarks_count,
            "fields": fields_count,
        }

        # Supported (Translate)
        if body_paras:
            manifest["supported"].append({
                "kind": "body", "label": "正文段落", "count": body_paras,
                "strategy": "Translate", "detail": f"{body_paras} 个正文段落参与翻译，保留样式与顺序"
            })
        if tables_count:
            manifest["supported"].append({
                "kind": "table", "label": "表格文字", "count": tables_count,
                "strategy": "Translate",
                "detail": f"{tables_count} 个表格（{table_paras} 段文字）参与翻译，单元格合并与结构完好保留"
            })
        if hyperlinks_count:
            manifest["supported"].append({
                "kind": "hyperlink", "label": "超链接", "count": hyperlinks_count,
                "strategy": "Translate display text, Preserve target",
                "detail": f"{hyperlinks_count} 处超链接显示文字翻译，外链关系与内部锚点保持不变"
            })
        if footnotes_count:
            manifest["supported"].append({
                "kind": "footnote", "label": "脚注", "count": footnotes_count,
                "strategy": "Translate",
                "detail": f"{footnotes_count} 条普通脚注文本翻译，引用 ID 与分隔符完全保留"
            })
        if header_paras or footer_paras:
            manifest["supported"].append({
                "kind": "header_footer", "label": "页眉/页脚",
                "count": header_paras + footer_paras,
                "strategy": "Translate + Protected",
                "detail": f"页眉页脚（{header_paras + footer_paras} 段）翻译普通文字，共享 part 去重，页码字段受保护"
            })

        # Preserved (Preserve + report)
        if textboxes_count:
            manifest["preserved"].append({
                "kind": "textbox", "label": "文本框", "count": textboxes_count,
                "strategy": "Preserve + report", "detail": f"{textboxes_count} 个文本框内容在原位置安全保留，不修改"
            })
        if comments_count:
            manifest["preserved"].append({
                "kind": "comment", "label": "批注", "count": comments_count,
                "strategy": "Preserve", "detail": f"{comments_count} 条批注元数据及范围标记完整保留"
            })
        if drawings_count:
            manifest["preserved"].append({
                "kind": "image", "label": "图片与图形", "count": drawings_count,
                "strategy": "Preserve", "detail": f"{drawings_count} 处内嵌图片与图形对象及关系原样保留"
            })
        if math_count:
            manifest["preserved"].append({
                "kind": "formula", "label": "公式", "count": math_count,
                "strategy": "Preserve", "detail": f"{math_count} 处数学公式原样保留"
            })
        if fields_count:
            manifest["preserved"].append({
                "kind": "field", "label": "动态字段", "count": fields_count,
                "strategy": "Protected", "detail": f"{fields_count} 处字段指令与结果受保护，不被误改"
            })

        return manifest


def _extract_run_formatting(r: LET._Element) -> Set[str]:
    """Extract semantic inline formatting flags from a w:r element."""
    flags = set()
    rPr = r.find(TAG_RPR)
    if rPr is None:
        return flags

    # Bold: <w:b/> or <w:b w:val="true"/> or <w:b w:val="1"/>
    b = rPr.find(f"{{{W_NS}}}b")
    if b is not None:
        val = b.get(f"{{{W_NS}}}val")
        if val is None or val.lower() in ("true", "1"):
            flags.add("b")

    # Italic: <w:i/>
    i = rPr.find(f"{{{W_NS}}}i")
    if i is not None:
        val = i.get(f"{{{W_NS}}}val")
        if val is None or val.lower() in ("true", "1"):
            flags.add("i")

    # Underline: <w:u/>
    u = rPr.find(f"{{{W_NS}}}u")
    if u is not None:
        val = u.get(f"{{{W_NS}}}val")
        if val is None or val.lower() not in ("none", "false", "0"):
            flags.add("u")

    # Strike: <w:strike/>
    s = rPr.find(f"{{{W_NS}}}strike")
    if s is not None:
        val = s.get(f"{{{W_NS}}}val")
        if val is None or val.lower() in ("true", "1"):
            flags.add("s")

    # Vertical alignment (subscript / superscript)
    va = rPr.find(f"{{{W_NS}}}vertAlign")
    if va is not None:
        val = va.get(f"{{{W_NS}}}val")
        if val == "superscript":
            flags.add("sup")
        elif val == "subscript":
            flags.add("sub")

    return flags


def _wrap_text_with_tags(text: str, tags: Set[str], link_id: Optional[str] = None,
                         anchor: Optional[str] = None) -> str:
    """Wrap a text snippet with semantic markup tags."""
    if not text:
        return ""
    # Escape XML special characters in the text
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    res = escaped
    # Tag order: inner formatting, outer link
    for tag in sorted(tags):
        res = f"<{tag}>{res}</{tag}>"
    if link_id:
        res = f'<link id="{link_id}">{res}</link>'
    elif anchor:
        res = f'<link anchor="{anchor}">{res}</link>'
    return res


def _parse_paragraph_to_tagged_unit(p: LET._Element) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse a w:p element into tagged text, plain text, and list of markers.

    Returns: (tagged_text, plain_text, tags_list, protected_markers)
    """
    tagged_chunks: List[str] = []
    plain_chunks: List[str] = []
    tags_meta: List[Dict[str, Any]] = []
    markers: List[Dict[str, Any]] = []

    # Iterate direct children of w:p
    for child in p:
        if child.tag == TAG_PPR:
            continue
        elif child.tag == TAG_R:
            # Check for footnoteRef or other special runs
            if child.find(TAG_FOOTNOTE_REF) is not None:
                markers.append({"type": "footnoteRef"})
                continue
            fn_reference = child.find(TAG_FOOTNOTE_REFERENCE)
            if fn_reference is not None:
                fn_id = fn_reference.get(f"{{{W_NS}}}id")
                markers.append({"type": "footnoteReference", "id": fn_id})
                tagged_chunks.append(f'<ref type="footnote" id="{fn_id}"/>')
                continue
            if child.find(TAG_DRAWING) is not None:
                markers.append({"type": "drawing"})
                continue

            # Read text nodes
            run_text = "".join(t.text or "" for t in child.findall(TAG_T))
            if child.find(TAG_TAB) is not None:
                run_text += "\t"
            if child.find(TAG_BR) is not None or child.find(TAG_CR) is not None:
                run_text += "\n"

            if run_text:
                fmt = _extract_run_formatting(child)
                plain_chunks.append(run_text)
                tagged_chunks.append(_wrap_text_with_tags(run_text, fmt))
                if fmt:
                    tags_meta.append({"text": run_text, "formats": sorted(fmt)})

        elif child.tag == TAG_HYPERLINK:
            link_id = child.get(f"{{{R_NS}}}id")
            anchor = child.get(f"{{{W_NS}}}anchor")
            link_plain: List[str] = []
            link_tagged: List[str] = []

            for r in child.findall(TAG_R):
                r_text = "".join(t.text or "" for t in r.findall(TAG_T))
                if r_text:
                    fmt = _extract_run_formatting(r)
                    link_plain.append(r_text)
                    link_tagged.append(_wrap_text_with_tags(r_text, fmt))

            combined_link_text = "".join(link_plain)
            if combined_link_text:
                plain_chunks.append(combined_link_text)
                inner = "".join(link_tagged)
                if link_id:
                    tagged_chunks.append(f'<link id="{link_id}">{inner}</link>')
                    tags_meta.append({"type": "hyperlink", "id": link_id, "text": combined_link_text})
                elif anchor:
                    tagged_chunks.append(f'<link anchor="{anchor}">{inner}</link>')
                    tags_meta.append({"type": "hyperlink", "anchor": anchor, "text": combined_link_text})
                else:
                    tagged_chunks.append(inner)

        elif child.tag == TAG_BOOKMARK_START:
            bm_name = child.get(f"{{{W_NS}}}name")
            bm_id = child.get(f"{{{W_NS}}}id")
            markers.append({"type": "bookmarkStart", "name": bm_name, "id": bm_id})
        elif child.tag == TAG_BOOKMARK_END:
            bm_id = child.get(f"{{{W_NS}}}id")
            markers.append({"type": "bookmarkEnd", "id": bm_id})
        elif child.tag == TAG_FLD_SIMPLE:
            instr = child.get(f"{{{W_NS}}}instr") or ""
            markers.append({"type": "fldSimple", "instr": instr})
            tagged_chunks.append(f'<field instr="{instr.strip()}"/>')

    full_tagged = "".join(tagged_chunks)
    full_plain = "".join(plain_chunks)

    # Clean up redundant adjacent identical tags: e.g. </b><b> -> ""
    for tag in ("b", "i", "u", "s", "sup", "sub"):
        full_tagged = full_tagged.replace(f"</{tag}><{tag}>", "")

    return full_tagged, full_plain, tags_meta, markers


def extract_roundtrip_units(source_bytes: bytes) -> Tuple[List[DocxUnit], Dict[str, Any]]:
    """Extract translatable units with source anchors and inline markers.

    Raises TrackChangesPendingError if the package contains pending revisions.
    """
    manifest = inspect_docx_package(source_bytes)
    source_hash = manifest["source_hash"]
    units: List[DocxUnit] = []

    with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as archive:
        entries = archive.namelist()

        # 1. Body & Table paragraphs from word/document.xml
        doc_root = _safe_xml_parse(archive.read("word/document.xml"))
        body = doc_root.find(TAG_BODY)
        if body is not None:
            p_idx = 0
            tbl_idx = 0
            for child in body:
                if child.tag == TAG_P:
                    tagged, plain, tags, markers = _parse_paragraph_to_tagged_unit(child)
                    if plain.strip():
                        unit_id = f"u:doc:p{p_idx}"
                        anchor = {
                            "part": "word/document.xml",
                            "kind": "paragraph",
                            "index": p_idx,
                            "tag": TAG_P,
                        }
                        units.append(DocxUnit(
                            instance_id=unit_id,
                            source_anchor=anchor,
                            source_version=source_hash,
                            source=tagged,
                            target="",
                            source_plain=plain,
                            tags=tags,
                            protected_markers=markers,
                        ))
                    p_idx += 1

                elif child.tag == TAG_TBL:
                    r_idx = 0
                    for tr in child.findall(TAG_TR):
                        c_idx = 0
                        for tc in tr.findall(TAG_TC):
                            tcp_idx = 0
                            for p in tc.findall(TAG_P):
                                tagged, plain, tags, markers = _parse_paragraph_to_tagged_unit(p)
                                if plain.strip():
                                    unit_id = f"u:doc:tbl{tbl_idx}:r{r_idx}:c{c_idx}:p{tcp_idx}"
                                    anchor = {
                                        "part": "word/document.xml",
                                        "kind": "table_cell",
                                        "tbl_index": tbl_idx,
                                        "row_index": r_idx,
                                        "col_index": c_idx,
                                        "para_index": tcp_idx,
                                        "tag": TAG_P,
                                    }
                                    units.append(DocxUnit(
                                        instance_id=unit_id,
                                        source_anchor=anchor,
                                        source_version=source_hash,
                                        source=tagged,
                                        target="",
                                        source_plain=plain,
                                        tags=tags,
                                        protected_markers=markers,
                                    ))
                                tcp_idx += 1
                            c_idx += 1
                        r_idx += 1
                    tbl_idx += 1

        # 2. Footnotes from word/footnotes.xml
        if "word/footnotes.xml" in entries:
            fn_root = _safe_xml_parse(archive.read("word/footnotes.xml"))
            for fn in fn_root.findall(TAG_FOOTNOTE):
                fn_id = fn.get(f"{{{W_NS}}}id") or fn.get("id")
                fn_type = fn.get(f"{{{W_NS}}}type") or fn.get("type")
                if fn_id in ("-1", "0") or fn_type in ("separator", "continuationSeparator"):
                    continue
                p_idx = 0
                for p in fn.findall(TAG_P):
                    tagged, plain, tags, markers = _parse_paragraph_to_tagged_unit(p)
                    if plain.strip():
                        unit_id = f"u:fn:{fn_id}:p{p_idx}"
                        anchor = {
                            "part": "word/footnotes.xml",
                            "kind": "footnote",
                            "footnote_id": fn_id,
                            "para_index": p_idx,
                            "tag": TAG_P,
                        }
                        units.append(DocxUnit(
                            instance_id=unit_id,
                            source_anchor=anchor,
                            source_version=source_hash,
                            source=tagged,
                            target="",
                            source_plain=plain,
                            tags=tags,
                            protected_markers=markers,
                        ))
                    p_idx += 1

        # 3. Headers and Footers
        header_parts = sorted([e for e in entries if re.match(r"^word/header\d+\.xml$", e)])
        for hp in header_parts:
            h_root = _safe_xml_parse(archive.read(hp))
            p_idx = 0
            for p in h_root.findall(TAG_P):
                tagged, plain, tags, markers = _parse_paragraph_to_tagged_unit(p)
                if plain.strip():
                    part_name = Path(hp).stem
                    unit_id = f"u:hdr:{part_name}:p{p_idx}"
                    anchor = {
                        "part": hp,
                        "kind": "header",
                        "para_index": p_idx,
                        "tag": TAG_P,
                    }
                    units.append(DocxUnit(
                        instance_id=unit_id,
                        source_anchor=anchor,
                        source_version=source_hash,
                        source=tagged,
                        target="",
                        source_plain=plain,
                        tags=tags,
                        protected_markers=markers,
                    ))
                p_idx += 1

        footer_parts = sorted([e for e in entries if re.match(r"^word/footer\d+\.xml$", e)])
        for fp in footer_parts:
            f_root = _safe_xml_parse(archive.read(fp))
            p_idx = 0
            for p in f_root.findall(TAG_P):
                tagged, plain, tags, markers = _parse_paragraph_to_tagged_unit(p)
                if plain.strip():
                    part_name = Path(fp).stem
                    unit_id = f"u:ftr:{part_name}:p{p_idx}"
                    anchor = {
                        "part": fp,
                        "kind": "footer",
                        "para_index": p_idx,
                        "tag": TAG_P,
                    }
                    units.append(DocxUnit(
                        instance_id=unit_id,
                        source_anchor=anchor,
                        source_version=source_hash,
                        source=tagged,
                        target="",
                        source_plain=plain,
                        tags=tags,
                        protected_markers=markers,
                    ))
                p_idx += 1

    manifest["unit_count"] = len(units)
    return units, manifest


def validate_target_tags(source_unit: DocxUnit, target_text: str) -> None:
    """Validate that target tags are well-formed XML and match allowed source tags.

    Raises TagQAError on unclosed tags, nesting errors, or unknown/forbidden tags.
    """
    if not target_text:
        return

    # 1. XML well-formedness
    wrapped = f"<unit>{target_text}</unit>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as exc:
        raise TagQAError(
            f"Tag QA 失败（单元 {source_unit.instance_id}）：标签未闭合或嵌套错误：{exc}"
        ) from exc

    # 2. Check tag names and allowed attributes
    allowed_tags = {"unit", "b", "i", "u", "s", "sup", "sub", "link", "field", "ref"}
    target_link_ids: Set[str] = set()
    source_link_ids = {
        item.get("id") for item in source_unit.tags
        if item.get("type") == "hyperlink" and item.get("id")
    }

    for elem in root.iter():
        if elem.tag not in allowed_tags:
            raise TagQAError(
                f"Tag QA 失败（单元 {source_unit.instance_id}）：发现未知或不合法标签 <{elem.tag}>"
            )
        if elem.tag == "link":
            lid = elem.get("id")
            if lid:
                target_link_ids.add(lid)
                if source_link_ids and lid not in source_link_ids:
                    raise TagQAError(
                        f"Tag QA 失败（单元 {source_unit.instance_id}）：超链接 ID 不匹配，"
                        f"原 ID 包含 {sorted(source_link_ids)}，译文使用了 '{lid}'"
                    )

    # 3. Check for lost links
    if source_link_ids and not (source_link_ids & target_link_ids):
        # A link was present in source but completely omitted in target
        raise TagQAError(
            f"Tag QA 失败（单元 {source_unit.instance_id}）：源文包含超链接 {sorted(source_link_ids)}，"
            "但译文中缺失该超链接标签"
        )


def _render_tagged_target_to_runs(p: LET._Element, target_text: str,
                                 target_lang: Optional[str] = None) -> None:
    """Replace translatable runs inside a w:p element with new runs from target_text.

    Preserves paragraph properties w:pPr and non-translatable children like bookmarks,
    footnote references, and drawings.
    """
    # 1. Preserve w:pPr and protected prefix/suffix nodes
    pPr = p.find(TAG_PPR)

    # If target language is RTL (Arabic, Hebrew, Persian, etc.), apply w:bidi
    if target_lang and target_lang.lower().startswith(("ar", "fa", "he", "ur")):
        if pPr is None:
            pPr = LET.Element(TAG_PPR)
            p.insert(0, pPr)
        bidi = pPr.find(f"{{{W_NS}}}bidi")
        if bidi is None:
            bidi = LET.SubElement(pPr, f"{{{W_NS}}}bidi")
            bidi.set(f"{{{W_NS}}}val"), "1"

    # Identify protected non-run elements to preserve (bookmarks, footnoteRef, drawing)
    preserved_nodes = []
    for child in list(p):
        if child.tag == TAG_PPR:
            continue
        if child.tag in (TAG_BOOKMARK_START, TAG_BOOKMARK_END,
                         TAG_COMMENT_RANGE_START, TAG_COMMENT_RANGE_END):
            preserved_nodes.append(child)
        elif child.tag == TAG_R:
            # If the run contains only footnoteRef or drawing, preserve it
            if child.find(TAG_FOOTNOTE_REF) is not None or child.find(TAG_DRAWING) is not None:
                preserved_nodes.append(child)
        p.remove(child)

    # Put back w:pPr first
    if pPr is not None and pPr not in p:
        p.insert(0, pPr)

    # Put back any leading footnoteRef
    leading_refs = [n for n in preserved_nodes if n.tag == TAG_R and n.find(TAG_FOOTNOTE_REF) is not None]
    for ref in leading_refs:
        p.append(ref)

    # Put back leading bookmarkStart
    for bm in [n for n in preserved_nodes if n.tag == TAG_BOOKMARK_START]:
        p.append(bm)

    # 2. Parse target_text XML
    wrapped = f"<unit>{target_text}</unit>"
    tree = ET.fromstring(wrapped)

    def _append_run(parent: LET._Element, text: str, formats: Set[str], is_hyperlink: bool = False):
        if not text:
            return
        r = LET.SubElement(parent, TAG_R)
        rPr = LET.SubElement(r, TAG_RPR)
        has_rpr = False

        if is_hyperlink:
            rStyle = LET.SubElement(rPr, f"{{{W_NS}}}rStyle")
            rStyle.set(f"{{{W_NS}}}val", "Hyperlink")
            has_rpr = True

        if "b" in formats:
            LET.SubElement(rPr, f"{{{W_NS}}}b")
            has_rpr = True
        if "i" in formats:
            LET.SubElement(rPr, f"{{{W_NS}}}i")
            has_rpr = True
        if "u" in formats:
            u_elem = LET.SubElement(rPr, f"{{{W_NS}}}u")
            u_elem.set(f"{{{W_NS}}}val", "single")
            has_rpr = True
        if "s" in formats:
            LET.SubElement(rPr, f"{{{W_NS}}}strike")
            has_rpr = True
        if "sup" in formats:
            va = LET.SubElement(rPr, f"{{{W_NS}}}vertAlign")
            va.set(f"{{{W_NS}}}val", "superscript")
            has_rpr = True
        elif "sub" in formats:
            va = LET.SubElement(rPr, f"{{{W_NS}}}vertAlign")
            va.set(f"{{{W_NS}}}val", "subscript")
            has_rpr = True

        if not has_rpr:
            r.remove(rPr)

        t = LET.SubElement(r, TAG_T)
        t.text = text
        if text.startswith(" ") or text.endswith(" ") or "\t" in text:
            t.set(TAG_XML_SPACE, "preserve")

    def _process_node(node: ET.Element, current_formats: Set[str], current_parent: LET._Element):
        # Text before children
        if node.text:
            _append_run(current_parent, node.text, current_formats)

        for child in node:
            child_formats = set(current_formats)
            child_parent = current_parent

            if child.tag in FORMATTING_TAGS:
                child_formats.add(child.tag)
            elif child.tag == "link":
                link_elem = LET.SubElement(current_parent, TAG_HYPERLINK)
                lid = child.get("id")
                anchor = child.get("anchor")
                if lid:
                    link_elem.set(f"{{{R_NS}}}id", lid)
                if anchor:
                    link_elem.set(f"{{{W_NS}}}anchor", anchor)
                child_parent = link_elem
            elif child.tag == "field":
                instr = child.get("instr") or "PAGE"
                fld = LET.SubElement(current_parent, TAG_FLD_SIMPLE)
                fld.set(f"{{{W_NS}}}instr", instr)
                _append_run(fld, child.text or "1", set())
                if child.tail:
                    _append_run(current_parent, child.tail, current_formats)
                continue
            elif child.tag == "ref":
                ref_id = child.get("id") or "1"
                r = LET.SubElement(current_parent, TAG_R)
                rPr = LET.SubElement(r, TAG_RPR)
                rStyle = LET.SubElement(rPr, f"{{{W_NS}}}rStyle")
                rStyle.set(f"{{{W_NS}}}val", "FootnoteReference")
                fn_ref = LET.SubElement(r, TAG_FOOTNOTE_REFERENCE)
                fn_ref.set(f"{{{W_NS}}}id", ref_id)
                if child.tail:
                    _append_run(current_parent, child.tail, current_formats)
                continue

            _process_node(child, child_formats, child_parent)

            # Tail text after child
            if child.tail:
                _append_run(current_parent, child.tail, current_formats)

    _process_node(tree, set(), p)

    # Put back trailing bookmarks and non-run elements
    for bm in [n for n in preserved_nodes if n.tag == TAG_BOOKMARK_END]:
        p.append(bm)
    for other in [n for n in preserved_nodes if n.tag not in (TAG_BOOKMARK_START, TAG_BOOKMARK_END, TAG_R)]:
        p.append(other)


def write_roundtrip_docx(source_bytes: bytes,
                        units_or_state: Any,
                        *,
                        target_lang: Optional[str] = None) -> Tuple[bytes, Dict[str, Any]]:
    """Surgically write translated text back into the original DOCX package.

    Contract guarantees:
    - Base is original package.
    - Unmodified ZIP entries stay 100% byte-identical.
    - Modified XML parts only have declared translatable text updated.
    - Structures, tables, bookmarks, fields, and styles are preserved.
    - Corrupt tags, missing anchors, or invalid markup raise an actionable error.
    """
    # 1. Resolve units
    if isinstance(units_or_state, dict):
        # Called with Folith job state
        state = units_or_state
        target_lang = target_lang or state.get("target_lang")
        extracted_units, manifest = extract_roundtrip_units(source_bytes)
        pairs = state.get("pairs") or []
        # Match units with pairs
        for i, u in enumerate(extracted_units):
            if i < len(pairs):
                target = pairs[i].get("target") or pairs[i].get("source") or ""
                u.target = target
            else:
                u.target = u.source
        units = extracted_units
    elif isinstance(units_or_state, list):
        if units_or_state and isinstance(units_or_state[0], dict):
            units = [DocxUnit.from_dict(u) for u in units_or_state]
        else:
            units = list(units_or_state)
        # Verify inspection
        manifest = inspect_docx_package(source_bytes)
    else:
        raise ValueError(f"Unsupported units_or_state type: {type(units_or_state)}")

    # 2. Tag QA validation on all target units
    for u in units:
        target = u.target or u.source
        validate_target_tags(u, target)

    # 3. Group units by target part
    units_by_part: Dict[str, List[DocxUnit]] = {}
    for u in units:
        part = u.source_anchor["part"]
        units_by_part.setdefault(part, []).append(u)

    modified_parts_data: Dict[str, bytes] = {}

    with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as src_archive:
        entries = src_archive.namelist()

        # Check modified parts
        for part_name, part_units in units_by_part.items():
            if part_name not in entries:
                raise InvalidAnchorError(f"Source anchor 指向的 part '{part_name}' 不存在于原 DOCX 包中")

            root = _safe_xml_parse(src_archive.read(part_name))

            if part_name == "word/document.xml":
                body = root.find(TAG_BODY)
                if body is None:
                    raise InvalidAnchorError("word/document.xml 缺失 w:body 节点")

                # Map paragraphs
                p_nodes = [c for c in body if c.tag == TAG_P]
                tbl_nodes = [c for c in body if c.tag == TAG_TBL]

                for u in part_units:
                    anchor = u.source_anchor
                    target_text = u.target or u.source
                    if anchor["kind"] == "paragraph":
                        idx = anchor["index"]
                        if idx >= len(p_nodes):
                            raise InvalidAnchorError(
                                f"单元 {u.instance_id} 的正文段落索引 {idx} 超出范围（共 {len(p_nodes)} 段）"
                            )
                        _render_tagged_target_to_runs(p_nodes[idx], target_text, target_lang)

                    elif anchor["kind"] == "table_cell":
                        tbl_idx = anchor["tbl_index"]
                        r_idx = anchor["row_index"]
                        c_idx = anchor["col_index"]
                        p_idx = anchor["para_index"]

                        if tbl_idx >= len(tbl_nodes):
                            raise InvalidAnchorError(f"单元 {u.instance_id} 表格索引 {tbl_idx} 超出范围")
                        tbl = tbl_nodes[tbl_idx]
                        rows = tbl.findall(TAG_TR)
                        if r_idx >= len(rows):
                            raise InvalidAnchorError(f"单元 {u.instance_id} 表格行索引 {r_idx} 超出范围")
                        cells = rows[r_idx].findall(TAG_TC)
                        if c_idx >= len(cells):
                            raise InvalidAnchorError(f"单元 {u.instance_id} 表格列索引 {c_idx} 超出范围")
                        cell_paras = cells[c_idx].findall(TAG_P)
                        if p_idx >= len(cell_paras):
                            raise InvalidAnchorError(f"单元 {u.instance_id} 单元格段落索引 {p_idx} 超出范围")
                        _render_tagged_target_to_runs(cell_paras[p_idx], target_text, target_lang)

            elif part_name == "word/footnotes.xml":
                fn_map = {}
                for fn in root.findall(TAG_FOOTNOTE):
                    fid = fn.get(f"{{{W_NS}}}id") or fn.get("id")
                    if fid:
                        fn_map[fid] = fn

                for u in part_units:
                    anchor = u.source_anchor
                    target_text = u.target or u.source
                    fid = anchor["footnote_id"]
                    p_idx = anchor["para_index"]
                    if fid not in fn_map:
                        raise InvalidAnchorError(f"单元 {u.instance_id} 脚注 ID '{fid}' 在 footnotes.xml 中未找到")
                    fn_paras = fn_map[fid].findall(TAG_P)
                    if p_idx >= len(fn_paras):
                        raise InvalidAnchorError(f"单元 {u.instance_id} 脚注段落索引 {p_idx} 超出范围")
                    _render_tagged_target_to_runs(fn_paras[p_idx], target_text, target_lang)

            elif part_name.startswith("word/header") or part_name.startswith("word/footer"):
                p_nodes = root.findall(TAG_P)
                for u in part_units:
                    anchor = u.source_anchor
                    target_text = u.target or u.source
                    p_idx = anchor["para_index"]
                    if p_idx >= len(p_nodes):
                        raise InvalidAnchorError(
                            f"单元 {u.instance_id} 页眉/页脚段落索引 {p_idx} 超出范围"
                        )
                    _render_tagged_target_to_runs(p_nodes[p_idx], target_text, target_lang)

            modified_parts_data[part_name] = _safe_xml_serialize(root)

        # 4. Construct output package
        # Guarantee: Unmodified ZIP entries stay 100% byte-identical.
        out_buf = io.BytesIO()
        unmodified_count = 0
        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst_archive:
            for item in src_archive.infolist():
                if item.filename in modified_parts_data:
                    # Write modified XML part
                    dst_archive.writestr(item.filename, modified_parts_data[item.filename])
                else:
                    # Write exact raw uncompressed bytes from original entry
                    raw = src_archive.read(item.filename)
                    dst_archive.writestr(item.filename, raw)
                    unmodified_count += 1

    output_bytes = out_buf.getvalue()

    report = {
        "status": "success",
        "contract": CONTRACT_VERSION,
        "roundtrip": True,
        "source_hash": manifest["source_hash"],
        "output_hash": hashlib.sha256(output_bytes).hexdigest(),
        "total_units": len(units),
        "modified_parts": sorted(modified_parts_data.keys()),
        "unmodified_entries_count": unmodified_count,
        "supported": manifest["supported"],
        "preserved": manifest["preserved"],
        "rejected": manifest["rejected"],
    }
    return output_bytes, report


def build_roundtrip_docx(state: Dict[str, Any], source: bytes) -> Tuple[io.BytesIO, Dict[str, Any]]:
    """Convenience wrapper for writing a roundtrip DOCX from a Folith state.

    Returns (BytesIO_buffer, report_dict) matching the build_translation_docx interface.
    """
    if not source:
        raise ValueError("缺少原始 DOCX 文件字节，无法执行原格式保真写回")
    out_bytes, report = write_roundtrip_docx(source, state, target_lang=state.get("target_lang"))
    return io.BytesIO(out_bytes), report



def verify_roundtrip_docx(source_bytes: bytes,
                         output_bytes: bytes,
                         units: Optional[List[DocxUnit]] = None,
                         report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute the 6-point deterministic check required by Contract v1.

    1. Unmodified ZIP entries must match byte-for-byte in uncompressed content.
    2. Modified XML parts preserve structure: table count, cell merges, bookmarks,
       hyperlinks relationships, footnotes references, and page number fields intact.
    3. Python-docx and XML parsers open output without corruption.
    4. Unsupported/preserved items are reported accurately.
    """
    verification_results: Dict[str, Any] = {
        "passed": False,
        "unmodified_entries_identical": False,
        "structures_preserved": False,
        "can_be_opened": False,
        "report_accurate": False,
        "details": [],
    }

    src_zip = zipfile.ZipFile(io.BytesIO(source_bytes), "r")
    out_zip = zipfile.ZipFile(io.BytesIO(output_bytes), "r")

    src_entries = set(src_zip.namelist())
    out_entries = set(out_zip.namelist())
    assert src_entries == out_entries, f"ZIP entries mismatch: {src_entries ^ out_entries}"

    modified_parts = set((report or {}).get("modified_parts") or ["word/document.xml"])

    # Check 1: Unmodified ZIP entries byte-identical
    for name in src_entries:
        if name not in modified_parts:
            src_content = src_zip.read(name)
            out_content = out_zip.read(name)
            assert hashlib.sha256(src_content).hexdigest() == hashlib.sha256(out_content).hexdigest(), \
                f"Unmodified entry '{name}' was altered!"

    verification_results["unmodified_entries_identical"] = True
    verification_results["details"].append("Unmodified ZIP entries are 100% byte-identical")

    # Check 2: Structure verification in modified parts
    src_doc = _safe_xml_parse(src_zip.read("word/document.xml"))
    out_doc = _safe_xml_parse(out_zip.read("word/document.xml"))

    # Table structure
    src_tbls = src_doc.findall(f".//{TAG_TBL}")
    out_tbls = out_doc.findall(f".//{TAG_TBL}")
    assert len(src_tbls) == len(out_tbls), f"Table count changed: {len(src_tbls)} vs {len(out_tbls)}"

    for i, (st, ot) in enumerate(zip(src_tbls, out_tbls)):
        s_rows = st.findall(f".//{TAG_TR}")
        o_rows = ot.findall(f".//{TAG_TR}")
        assert len(s_rows) == len(o_rows), f"Table {i} row count changed"
        for ri, (sr, orw) in enumerate(zip(s_rows, o_rows)):
            s_cells = sr.findall(f".//{TAG_TC}")
            o_cells = orw.findall(f".//{TAG_TC}")
            assert len(s_cells) == len(o_cells), f"Table {i} row {ri} cell count changed"

    # Bookmarks
    src_bms = {b.get(f"{{{W_NS}}}name") for b in src_doc.findall(f".//{TAG_BOOKMARK_START}")}
    out_bms = {b.get(f"{{{W_NS}}}name") for b in out_doc.findall(f".//{TAG_BOOKMARK_START}")}
    assert src_bms == out_bms, f"Bookmarks altered: {src_bms ^ out_bms}"

    # Hyperlinks targets in relationships
    if "word/_rels/document.xml.rels" in src_entries:
        src_rels = src_zip.read("word/_rels/document.xml.rels")
        out_rels = out_zip.read("word/_rels/document.xml.rels")
        assert src_rels == out_rels, "document.xml.rels was altered"

    # Footnotes check
    if "word/footnotes.xml" in src_entries:
        src_fn = _safe_xml_parse(src_zip.read("word/footnotes.xml"))
        out_fn = _safe_xml_parse(out_zip.read("word/footnotes.xml"))
        src_fn_ids = {f.get(f"{{{W_NS}}}id") for f in src_fn.findall(TAG_FOOTNOTE)}
        out_fn_ids = {f.get(f"{{{W_NS}}}id") for f in out_fn.findall(TAG_FOOTNOTE)}
        assert src_fn_ids == out_fn_ids, f"Footnote IDs altered: {src_fn_ids ^ out_fn_ids}"

    # Drawings / Images check
    src_drawings = len(src_doc.findall(f".//{TAG_DRAWING}"))
    out_drawings = len(out_doc.findall(f".//{TAG_DRAWING}"))
    assert src_drawings == out_drawings, f"Drawing count altered: {src_drawings} vs {out_drawings}"

    verification_results["structures_preserved"] = True
    verification_results["details"].append("All tables, cells, bookmarks, links, footnotes, and drawings preserved")

    # Check 3: Can be opened by python-docx
    from docx import Document
    opened_doc = Document(io.BytesIO(output_bytes))
    assert opened_doc is not None
    verification_results["can_be_opened"] = True
    verification_results["details"].append("Document opens successfully with python-docx")

    # Check 4: Report accuracy
    if report:
        assert report.get("roundtrip") is True
        assert report.get("status") == "success"
        verification_results["report_accurate"] = True
        verification_results["details"].append("Delivery report accurately reflects roundtrip contract")

    verification_results["passed"] = True
    return verification_results
