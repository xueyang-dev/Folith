"""#527: XLIFF 1.2 generated from any task, with honest state mapping."""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith import xliff_writer  # noqa: E402

NS = {"x": "urn:oasis:names:tc:xliff:document:1.2"}


def _job():
    return {
        "name": "sample.docx", "source_filename": "sample.docx",
        "source_lang": "en", "target_lang": "zh-CN", "source_type": "docx",
        "pairs": [
            {"uid": "s-1", "source": "Hello", "target": ""},
            {"uid": "s-2", "source": "World & <friends>", "target": "世界"},
            {"uid": "s-3", "source": "Confirmed", "target": "已确认", "human_edited": True},
            {"uid": "s-4", "source": "Skipped", "target": "跳过", "excluded": True},
        ],
    }


def test_document_is_valid_xliff_12_with_stable_ids():
    xml = xliff_writer.build_xliff(_job(), open_findings=[0])
    root = ET.fromstring(xml)
    assert root.attrib["version"] == "1.2"
    units = root.findall(".//x:trans-unit", NS)
    assert [unit.attrib["id"] for unit in units] == ["s-1", "s-2", "s-3", "s-4"]
    # re-export gives identical ids
    assert [u.attrib["id"] for u in ET.fromstring(
        xliff_writer.build_xliff(_job())).findall(".//x:trans-unit", NS)] == \
        [u.attrib["id"] for u in units]


def test_state_mapping_and_excluded_unit():
    root = ET.fromstring(xliff_writer.build_xliff(_job(), open_findings=[0]))
    units = root.findall(".//x:trans-unit", NS)
    assert units[0].attrib["state"] == xliff_writer.STATE_NEEDS_REVIEW
    assert units[1].attrib["state"] == xliff_writer.STATE_TRANSLATED
    assert units[2].attrib["state"] == xliff_writer.STATE_SIGNED_OFF
    assert units[2].attrib.get("approved") == "yes"
    assert units[3].attrib.get("translate") == "no"
    assert units[3].find("x:note", NS) is not None


def test_escaping_and_language_attributes():
    root = ET.fromstring(xliff_writer.build_xliff(_job()))
    file_el = root.find("x:file", NS)
    assert file_el.attrib["source-language"] == "en"
    assert file_el.attrib["target-language"] == "zh-CN"
    source = root.findall(".//x:source", NS)[1]
    assert source.text == "World & <friends>"


def test_untranslated_has_no_target():
    units = ET.fromstring(xliff_writer.build_xliff(_job())).findall(".//x:trans-unit", NS)
    assert units[0].find("x:target", NS) is None
