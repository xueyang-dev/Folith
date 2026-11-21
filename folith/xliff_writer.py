"""Generate XLIFF 1.2 from an ordinary task (issue #527).

Only adds an output; it never rewrites the job state or Folith's own files.
One ``trans-unit`` per segment, with the stable segment uid as ``id`` so
re-export produces identical ids.  Excluded segments are emitted with
``translate="no"`` and a note — never silently dropped.
"""
from __future__ import annotations

from xml.sax.saxutils import escape
from typing import Any, Iterable, Mapping

# Documented state mapping (release notes mirror this table):
#   untranslated          -> state="new"
#   machine translated    -> state="translated"
#   human confirmed       -> state="signed-off", approved="yes"
#   open review finding   -> state="needs-review-translation"
STATE_NEW = "new"
STATE_TRANSLATED = "translated"
STATE_SIGNED_OFF = "signed-off"
STATE_NEEDS_REVIEW = "needs-review-translation"


def segment_state(pair: Mapping[str, Any], open_findings: Iterable[int] = (),
                  index: int | None = None) -> str:
    findings = set(open_findings)
    at = index if index is not None else pair.get("index")
    if at is not None and at in findings:
        return STATE_NEEDS_REVIEW
    if pair.get("human_edited") or pair.get("reviewed") or pair.get("human_accepted"):
        return STATE_SIGNED_OFF
    if pair.get("target") and str(pair.get("target")).strip():
        return STATE_TRANSLATED
    return STATE_NEW


def _approved(state: str) -> str:
    return ' approved="yes"' if state == STATE_SIGNED_OFF else ""


def segment_uid(pair: Mapping[str, Any], index: int) -> str:
    """Stable id: the segment uid, falling back to the index for old jobs."""
    return str(pair.get("uid") or pair.get("segment_uid") or f"seg-{index:04d}")


def build_xliff(job: Mapping[str, Any], *,
                open_findings: Iterable[int] = ()) -> str:
    """Return a complete XLIFF 1.2 document for *job* (no filesystem writes)."""
    pairs = list(job.get("pairs") or [])
    findings = set(open_findings)
    source_lang = str(job.get("source_lang") or job.get("source_language") or "en")
    target_lang = str(job.get("target_lang") or "zh-CN")
    original = escape(str(job.get("source_filename") or job.get("name") or ""))
    datatype = str(job.get("source_type") or "plaintext")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">',
        f'  <file original="{original}" source-language="{escape(source_lang)}" '
        f'target-language="{escape(target_lang)}" datatype="{escape(datatype)}">',
        "    <body>",
    ]
    for index, pair in enumerate(pairs):
        uid = escape(segment_uid(pair, index))
        excluded = bool(pair.get("excluded") or pair.get("translate") is False)
        attrs = f'id="{uid}"'
        if excluded:
            attrs += ' translate="no"'
        state = segment_state(pair, findings, index)
        lines.append(f"      <trans-unit {attrs} state=\"{state}\"{_approved(state)}>")
        lines.append(f"        <source>{escape(str(pair.get('source') or ''))}</source>")
        target = str(pair.get("target") or "")
        if target:
            lines.append(f"        <target>{escape(target)}</target>")
        if excluded:
            lines.append("        <note>excluded: not translated</note>")
        lines.append("      </trans-unit>")
    lines.extend(("    </body>", "  </file>", "</xliff>", ""))
    return "\n".join(lines)
