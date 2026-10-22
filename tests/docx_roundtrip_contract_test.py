"""DOCX 往返契约测试（B8）：语料入 CI + Folith 版语义等价。

FoliTH 的译文 DOCX 是**重建式重排**（它自己的导出说明里就写着“不保留原页码和
坐标”），不是在原包上逐字符回填，所以不能做 XML 树/字节级比对，等价必须按
FoliTH 自己的契约定义：

1. 段落文本流一致（按 FoliTH 自己的单元拼接规则，而不是源段落原文）；
2. 媒体字节一致；
3. 丢失的结构必须被逐项声明（``unsupported``）。

**不比** XML 字节、样式展开、页眉页脚（B7-a 已把页眉页脚排除在翻译单元之外）。

语料与 golden 值在 ``tests/fixtures/rich_source.docx`` /
``rich_source.json``：语料由 python-docx 生成，内容是为本测试编写的合成文本。

本文件自包含：不从其它测试模块 import fixture（回归脚本逐文件独立进程运行）。
"""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import core
from folith.engine.document_export import build_translation_docx
from folith.segmentation import reconstruct_source_paragraph

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "rich_source.docx"
GOLDEN = FIXTURES / "rich_source.json"


def _golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _import_fixture():
    data = FIXTURE.read_bytes()
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        FIXTURE.name, data)
    state = core.new_job_state(FIXTURE.name)
    state["source_paras"] = list(paragraphs)
    units, meta = core._apply_source_segmentation(state, list(paragraphs), mode="sentence")
    return data, paragraphs, units, meta, report


def _state_with_translations(data, paragraphs, units, report):
    """按真实导入路径构态；target 取源文本身，便于逐段比对。

    必须走 ``_apply_source_segmentation``：它负责写入 ``source_paragraphs``
    与 ``segmentation``，导出时的图文关联绑定依赖这两项。
    """
    state = core.new_job_state(FIXTURE.name)
    state["source_paras"] = list(paragraphs)
    rebuilt, _meta = core._apply_source_segmentation(
        state, list(paragraphs), mode="sentence")
    assert rebuilt == list(units)
    state.update(paras=list(units), extraction_report=report, target_lang="zh-CN",
                 p1_done=True, p2_done=True,
                 pairs=[{"source": unit, "target": unit, "reviewed": True,
                         "target_provenance": "human_edit"} for unit in units])
    return state


def _media_hashes(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist() if name.startswith("word/media/")}


def test_import_contract_is_locked_by_the_golden_manifest():
    golden = _golden()
    data, paragraphs, units, meta, report = _import_fixture()

    # 语料本身没被换掉。
    assert hashlib.sha256(data).hexdigest() == golden["provenance"]["sha256"]

    # 1) 导入：段与单元的契约值。
    assert paragraphs == golden["contract"]["paragraphs"]
    assert units == golden["contract"]["units"]
    assert len(paragraphs) == golden["contract"]["paragraph_count"]
    assert len(units) == golden["contract"]["unit_count"]

    # 2) B6 在真实文档上生效：切分处的原始空白被精确保留并可重建原段落。
    ranges = meta.get("paragraph_ranges") or []
    split_rows = [row for row in ranges if row["end"] - row["start"] > 1]
    assert split_rows, "fixture 必须包含至少一个被切分的段落"
    for row in split_rows:
        assert row["separators"] == [" "]
        assert row["gate"]["separators_recorded"] is True
        assert row["gate"]["whitespace_exact"] is False
        assert reconstruct_source_paragraph(
            units[row["start"]:row["end"]], row["separators"]
        ) == paragraphs[row["paragraph_index"]]


def test_exported_translation_reimports_into_the_same_paragraph_stream():
    golden = _golden()
    data, paragraphs, units, meta, report = _import_fixture()
    state = _state_with_translations(data, paragraphs, units, report)

    payload_io, export_report = build_translation_docx(state, data)
    payload = payload_io.getvalue() if hasattr(payload_io, "getvalue") else payload_io

    # 媒体：数量与字节都必须一致。
    assert export_report["image_count"] == 1
    assert _media_hashes(payload) == _media_hashes(data)

    # 再导入：译文 DOCX 是重建的，多出一个“译文”标题段。
    again, _warnings, _report = core.extract_document_paragraphs_with_report(
        "exported.docx", payload)
    assert len(again) == len(paragraphs) + 1
    assert again[0] == "译文"
    assert again[1:] == [
        " ".join(units[row["start"]:row["end"]])
        for row in (meta.get("paragraph_ranges") or [])
    ]
    assert again[1:] == golden["contract"]["paragraphs"]


def test_lost_structures_are_declared_one_by_one():
    golden = _golden()
    data, paragraphs, units, meta, report = _import_fixture()

    extracted = report.get("extracted") or {}
    unsupported = extracted.get("unsupported") or report.get("unsupported") or []
    declared = {str(item.get("kind")): int(item.get("count") or 0)
                for item in unsupported}
    assert declared == {str(item.get("kind")): int(item.get("count") or 0)
                        for item in golden["contract"]["unsupported"]}
    assert declared == {"table": 1, "header": 1, "footer": 1, "image": 1}
    status = extracted.get("status") or report.get("status") or ""
    assert status == golden["contract"]["status"] == "partial"

    state = _state_with_translations(data, paragraphs, units, report)
    payload_io, export_report = build_translation_docx(state, data)
    payload = payload_io.getvalue() if hasattr(payload_io, "getvalue") else payload_io
    assert payload
    warnings = " ".join(export_report.get("warnings") or [])
    assert "表格" in warnings
    assert "重新排版" in warnings
