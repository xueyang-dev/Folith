"""交付文件内容校验探针自身的用例（探针不能"永远通过"）。

校验逻辑如果只会返回成功，那它就不是证据。这里用**合成**的交付目录验证：
正常内容必须通过；被改动、缺文件、行数不符必须失败。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("docx")
pytest.importorskip("openpyxl")


def _build_delivery(root: Path, *, tamper_translation: bool = False,
                    drop_terms: bool = False) -> tuple[Path, Path]:
    import docx
    from openpyxl import Workbook

    delivery = root / "交付 结果 中文"
    delivery.mkdir(parents=True, exist_ok=True)
    pairs = [
        {"source": "第一段原文。", "target": "First paragraph.", "reviewed": True,
         "human_edited": False},
        {"source": "第二段原文。", "target": "人工改过的译文。", "reviewed": True,
         "human_edited": True},
        {"source": "第三段原文。", "target": "Third paragraph.", "reviewed": True,
         "human_edited": False},
    ]
    state_path = root / "state.json"
    state_path.write_text(json.dumps({"pairs": pairs, "glossary": []},
                                     ensure_ascii=False), encoding="utf-8")

    document = docx.Document()
    document.add_paragraph("译文")
    for index, pair in enumerate(pairs):
        text = pair["target"]
        if tamper_translation and index == 1:
            text = "被人改坏的译文。"
        document.add_paragraph(text)
    document.save(str(delivery / "translation.docx"))

    bilingual = docx.Document()
    bilingual.add_paragraph("图例：……")
    table = bilingual.add_table(rows=1 + len(pairs), cols=2)
    table.cell(0, 0).text = "原文"
    table.cell(0, 1).text = "译文"
    for index, pair in enumerate(pairs):
        table.cell(index + 1, 0).text = pair["source"]
        table.cell(index + 1, 1).text = pair["target"]
    bilingual.save(str(delivery / "bilingual.docx"))

    if not drop_terms:
        book = Workbook()
        sheet = book.active
        sheet.cell(1, 1, "Source")
        sheet.cell(1, 2, "Target")
        book.save(str(delivery / "terms.xlsx"))

    (delivery / "delivery_manifest.json").write_text(json.dumps({
        "delivery_status": "final", "job_id": "synthetic",
        "provider": "自定义中转站", "model": "stub-model",
        "blocking": 0, "actionable": 0, "informational": 0,
        "generated_assets": [{"name": "translation.docx"},
                             {"name": "bilingual.docx"},
                             {"name": "terms.xlsx"}],
    }, ensure_ascii=False), encoding="utf-8")
    with (delivery / "segment_evidence.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(len(pairs)):
            handle.write(json.dumps({"segment_index": index}) + "\n")
    return delivery, state_path


def test_content_probe_accepts_a_faithful_delivery(tmp_path):
    import qt_delivery_content_probe as probe

    delivery, state = _build_delivery(tmp_path)
    report = probe.check(delivery, state)
    assert report["ok"] is True, report["problems"]
    assert report["segments"] == 3
    assert report["evidence_records"] == 3
    assert report["manifest"]["delivery_status"] == "final"
    assert report["translation"]["human_edited_segments"] == [1]


def test_content_probe_rejects_a_tampered_translation(tmp_path):
    """交付文档里必须逐段等于当前译文：改坏了就得报出来。"""
    import qt_delivery_content_probe as probe

    delivery, state = _build_delivery(tmp_path, tamper_translation=True)
    report = probe.check(delivery, state)
    assert report["ok"] is False
    assert any("第 2 段" in item and "不一致" in item for item in report["problems"]), \
        report["problems"]


def test_content_probe_rejects_missing_or_incomplete_files(tmp_path):
    """缺文件、行数不符、表头不对都必须失败。"""
    import qt_delivery_content_probe as probe

    delivery, state = _build_delivery(tmp_path, drop_terms=True)
    report = probe.check(delivery, state)
    assert report["ok"] is False
    assert any("terms.xlsx" in item for item in report["problems"])

    # 删掉双语文件 → 必须报缺失
    (delivery / "bilingual.docx").unlink()
    report = probe.check(delivery, state)
    assert any("bilingual.docx" in item for item in report["problems"])

    # 段落数不符（少一段）→ 必须报出来
    delivery2, state2 = _build_delivery(tmp_path / "second")
    import docx

    document = docx.Document(str(delivery2 / "translation.docx"))
    document.paragraphs[-1]._element.getparent().remove(
        document.paragraphs[-1]._element)
    document.save(str(delivery2 / "translation.docx"))
    report = probe.check(delivery2, state2)
    assert report["ok"] is False
    assert any("段落数" in item for item in report["problems"]), report["problems"]
