"""#527/#596: XLIFF 1.2 output follows the standard and honest state mapping."""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith import xliff_writer  # noqa: E402

NS = {"x": "urn:oasis:names:tc:xliff:document:1.2"}


def _job(**over):
    job = {
        "name": "sample.docx", "source_filename": "sample.docx",
        "source_lang": "en", "target_lang": "zh-CN", "source_type": "docx",
        "pairs": [
            {"uid": "s-1", "source": "Hello", "target": ""},
            {"uid": "s-2", "source": "World & <friends>", "target": "世界"},
            {"uid": "s-3", "source": "Confirmed", "target": "已确认",
             "review_status": "approved"},
            {"uid": "s-4", "source": "Skipped", "target": "跳过", "excluded": True},
        ],
    }
    job.update(over)
    return job


def _units(xml):
    return ET.fromstring(xml).findall(".//x:trans-unit", NS)


def test_document_is_valid_xliff_12_with_unique_stable_ids():
    xml = xliff_writer.build_xliff(_job())
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    units = _units(xml)
    assert [u.attrib["id"] for u in units] == ["seg-s-1", "seg-s-2", "seg-s-3", "seg-s-4"]
    assert len({u.attrib["id"] for u in units}) == 4


def test_state_and_approval_live_on_target():
    units = _units(xliff_writer.build_xliff(_job(), open_findings=[1]))
    states = [u.find("x:target", NS).get("state") for u in units]
    assert states == ["new", "needs-review-translation", "signed-off", "new"]
    assert units[2].find("x:target", NS).get("approved") == "yes"
    assert units[3].attrib.get("translate") == "no"
    assert units[3].find("x:note", NS) is not None


def test_human_edit_without_approval_is_only_translated():
    units = _units(xliff_writer.build_xliff(_job(pairs=[
        {"uid": "x", "source": "A", "target": "甲", "human_edited": True}])))
    assert units[0].find("x:target", NS).get("state") == "translated"
    assert units[0].find("x:target", NS).get("approved") is None


def test_escaping_and_canonical_language_attributes():
    file_el = ET.fromstring(xliff_writer.build_xliff(_job())).find("x:file", NS)
    assert file_el.attrib["source-language"] == "en-US"
    assert file_el.attrib["target-language"] == "zh-CN"
    assert file_el.attrib["original"] == "sample.docx"
    assert file_el.attrib["datatype"] == "docx"
    assert _units(xliff_writer.build_xliff(_job()))[1].find("x:source", NS).text == \
        "World & <friends>"


def test_invalid_xml10_character_is_rejected():
    try:
        xliff_writer.build_xliff(_job(pairs=[{"uid": "bad", "source": "a\x00b", "target": "x"}]))
    except ValueError:
        return
    raise AssertionError("XML 1.0 无法表示的字符应明确报错")
