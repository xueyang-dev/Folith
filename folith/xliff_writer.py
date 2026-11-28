"""Generate XLIFF 1.2 from any task (issue #527).

Only adds an output; it never rewrites job state or Folith's own files.
Uses ElementTree for XML serialisation so text is always escaped correctly.
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Any, Iterable, Mapping

NS = "urn:oasis:names:tc:xliff:document:1.2"
ET.register_namespace("", NS)

STATE_NEW = "new"
STATE_TRANSLATED = "translated"
STATE_SIGNED_OFF = "signed-off"
STATE_NEEDS_REVIEW = "needs-review-translation"

_LANG_MAP = {"zh-CN": "zh-CN", "zh": "zh-CN", "en": "en-US", "english": "en-US",
             "简体中文": "zh-CN"}
_DATATYPE_MAP = {"docx": "docx", "pdf": "pdf", "epub": "x-html", "txt": "plaintext",
                 "md": "plaintext", "markdown": "plaintext"}


def _lang(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    return _LANG_MAP.get(raw.lower(), raw or fallback)


def _datatype(value: Any) -> str:
    raw = str(value or "").strip().lower().lstrip(".")
    return _DATATYPE_MAP.get(raw, raw or "plaintext")


def segment_state(pair: Mapping[str, Any], open_findings: Iterable[int] = (),
                  index: int | None = None) -> str:
    at = index if index is not None else pair.get("index")
    if at is not None and at in set(open_findings):
        return STATE_NEEDS_REVIEW
    # Only an explicit human-confirmed/reviewed decision counts as sign-off;
    # a bare human edit is still "translated".
    if (pair.get("human_accepted") or pair.get("accepted_by_human")
            or pair.get("review_status") == "approved") and \
            str(pair.get("target") or "").strip():
        return STATE_SIGNED_OFF
    if str(pair.get("target") or "").strip():
        return STATE_TRANSLATED
    return STATE_NEW


def segment_uid(pair: Mapping[str, Any], index: int) -> str:
    """Unique id: persistent uid first, then a deterministic fallback."""
    for key in ("uid", "segment_uid", "external_id", "id"):
        value = pair.get(key)
        if value:
            return f"seg-{value}"
    return f"seg-{index:06d}"


def _check_xml10(text: str) -> None:
    for char in text:
        code = ord(char)
        if code in (0x9, 0xA, 0xD):
            continue
        if 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or \
                0x10000 <= code <= 0x10FFFF:
            continue
        raise ValueError(f"字符 U+{code:04X} 无法在 XML 1.0 中表示")


def build_xliff(job: Mapping[str, Any], *, open_findings: Iterable[int] = ()) -> str:
    """Return a complete XLIFF 1.2 document for *job*."""
    pairs = list(job.get("pairs") or [])
    findings = set(open_findings)
    source_lang = _lang(job.get("source_lang") or job.get("source_language"), "en-US")
    target_lang = _lang(job.get("target_lang"), "zh-CN")
    original = str(job.get("source_filename") or job.get("name") or job.get("source_path") or "")
    if not original:
        raise ValueError("任务缺少源文件名，无法生成 XLIFF original")
    datatype = _datatype(job.get("source_type") or Path(original).suffix.lstrip("."))

    root = ET.Element(f"{{{NS}}}xliff", {"version": "1.2"})
    file_el = ET.SubElement(root, f"{{{NS}}}file", {
        "original": original, "source-language": source_lang,
        "target-language": target_lang, "datatype": datatype})
    body = ET.SubElement(file_el, f"{{{NS}}}body")
    seen: set[str] = set()
    for index, pair in enumerate(pairs):
        uid = segment_uid(pair, index)
        if uid in seen:
            uid = f"{uid}-{index}"
        seen.add(uid)
        excluded = bool(pair.get("excluded") or pair.get("translate") is False)
        attrs = {"id": uid}
        if excluded:
            attrs["translate"] = "no"
        unit = ET.SubElement(body, f"{{{NS}}}trans-unit", attrs)
        # CX-09 #596: locked/inline-tag payloads are rejected in the first
        # version rather than silently losing the <> structure.
        if pair.get("locked") or pair.get("inline_tags") or pair.get("placeholders"):
            raise ValueError(
                f"段 {index}（id={uid}）：含锁定标记或内联标签，XLIFF 1.2 首版暂不支持；"
                "请先在 Folith 中清除该段的锁/标签后再导出")
        source_text = str(pair.get("source") or "")
        target_text = str(pair.get("target") or "")
        _check_xml10(source_text); _check_xml10(target_text)
        ET.SubElement(unit, f"{{{NS}}}source").text = source_text
        target_el = ET.SubElement(unit, f"{{{NS}}}target")
        target_el.text = target_text
        state = "new" if excluded else segment_state(pair, findings, index)
        target_el.set("state", state)
        if state == STATE_SIGNED_OFF:
            target_el.set("approved", "yes")
        if excluded:
            ET.SubElement(unit, f"{{{NS}}}note").text = "excluded: not translated"
    return ET.tostring(root, encoding="unicode", xml_declaration=False) \
        .replace("<xliff", '<?xml version="1.0" encoding="UTF-8"?>\n<xliff', 1)
