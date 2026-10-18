"""Report reader, review actions, and DOCX export regressions."""
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import core


def _report_state(filename="report-fixture.docx", *, generated=True, quality="pass"):
    state = core.new_job_state(filename)
    state.update(
        p1_done=True,
        p2_done=True,
        p3_done=generated,
        report_enabled=True,
        paras=["Source paragraph"],
        pairs=[{"source": "Source paragraph", "target": "源文段落"}],
        p3_md=(
            "# 翻译实践报告\n\n## 1 引言\n<!--rq:RQ1-->\n\n"
            "### 1 引言\n\n正文。\n\n## 3 案例分析\n\n案例。"
            if generated else ""
        ),
        academic_state={
            "status": quality,
            "quality_status": quality,
            "updated_at": "2026-08-23T12:00:00+00:00",
            "artifacts": {},
        },
        delivery_status="draft",
    )
    return state


def _write_artifact(job_id, filename, payload):
    path = core.job_dir(job_id) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _render_report(tmp_path, state, artifacts=None):
    from streamlit.testing.v1 import AppTest

    job_id = "reportui0000001"
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    core.save_job_state(job_id, state)
    for filename, payload in (artifacts or {}).items():
        _write_artifact(job_id, filename, payload)
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"),
                           default_timeout=30)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "report"
    at.run()
    return at, old_output


def _base_artifacts():
    return {
        "selected-cases.json": {
            "cases": [{"case_id": "seg-report-0001", "case_type": "authentic_revision"}],
            "authentic_selection_status": "sufficient_revision_cases",
            "minimum_core_case_count": 1,
        },
        "academic-outline.json": {
            "sections": [{"section_id": "1", "title": "引言"},
                          {"section_id": "3", "title": "案例分析"}],
        },
        "academic-validation.json": {"status": "pass", "issues": []},
    }


def test_report_docx_is_editable_ooxml_with_academic_formatting():
    report = (
        "# 翻译实践报告\n\n## 1 引言\n\n### 1.1 背景\n\n"
        "这一段包含 **Latin** 与中文。\n\n"
        "> 原文：Although the text is short.\n\n"
        "| 原文 | 译文 |\n| --- | --- |\n| source | 目标 |"
    )
    data = core.markdown_to_word(report, "功能对等理论").getvalue()
    assert data.startswith(b"PK")
    with ZipFile(BytesIO(data)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "word/document.xml" in archive.namelist()
        styles = archive.read("word/styles.xml").decode("utf-8")
        document = archive.read("word/document.xml").decode("utf-8")
    assert "Times New Roman" in styles and "宋体" in styles
    assert "w:firstLine" in styles and "w:line" in styles
    assert "w:tbl" in document
    assert document.count("1 引言") == 1


