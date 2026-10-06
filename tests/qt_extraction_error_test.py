"""A failed extraction must surface the *specific* reason (roadmap §6.C/§6.D).

Found by the Qt OCR missing-pack probe: the frozen app failed correctly (no
silent English fallback) but told the user "OCR 未生成可用段落；请安装语言包或
手动选择风格" instead of naming the missing pack, because the precise blocking
error was shadowed by a generic follow-up warning.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402


def test_specific_reason_wins_over_the_generic_follow_up():
    specific = ("OCR 语言包未安装：Deutsch（deu）。请先安装对应的 Tesseract "
                "traineddata，或在任务设置中改用已安装的语言。当前已安装语言："
                "ara, chi_sim, eng。")
    generic = "OCR 未生成可用段落；请安装语言包或手动选择风格"
    assert core.extraction_failure_detail([specific, generic]) == specific
    assert core.extraction_failure_detail([generic, specific]) == specific
    # Nothing specific available: keep the old behaviour rather than inventing.
    assert core.extraction_failure_detail([generic]) == generic
    assert core.extraction_failure_detail([]) == "未知提取错误"


def test_missing_engine_reason_is_preserved():
    engine = ("未找到本机 Tesseract OCR。请安装 Tesseract，或设置 "
              "FOLIOTHREAD_TESSERACT_CMD。")
    assert core.extraction_failure_detail(
        [engine, "OCR 未生成可用段落；请安装语言包或手动选择风格"]) == engine


@pytest.mark.skipif(not core._find_tesseract(),
                    reason="no local Tesseract to exercise the OCR gate")
def test_real_scan_with_missing_pack_reports_the_pack(tmp_path):
    """End to end: a requested-but-absent language must name itself."""
    import d0_fixtures

    manifest = d0_fixtures.build(["pdf_scanned"], tmp_path / "fixtures")
    fixture = Path(manifest["inputs"]["pdf_scanned"]["path"])
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        fixture.name, fixture.read_bytes(), ocr_language="Deutsch",
        ocr_mode="always")
    assert paragraphs == []
    joined = "\n".join(warnings)
    assert "Deutsch" in joined and "deu" in joined, joined
    # The blocking OCR error is the reason; the generic follow-up must not be
    # appended on top of it (that is what used to become the surfaced message).
    assert not any("OCR 未生成可用段落" in warning for warning in warnings), warnings
    # The engine must not have produced English text as a silent fallback.
    assert report.get("ocr", {}).get("actual_engine") in (None, "", "tesseract")
    detail = core.extraction_failure_detail(warnings)
    assert "Deutsch" in detail
    assert "OCR 未生成可用段落" not in detail


def test_corrupt_and_encrypted_inputs_explain_themselves_in_chinese():
    """损坏/加密/空文档必须给出可执行的中文原因，而不是原始英文异常。

    真实缺陷：损坏的 DOCX 会把 zipfile 的英文异常（"There is no item named
    '[Content_Types].xml' …"）原样显示给用户，加密 PDF 显示 "document closed or
    encrypted"，空文档显示"未知提取错误"——都不可行动。
    """
    cases = {
        "document closed or encrypted": "已加密",
        "There is no item named '[Content_Types].xml' in the archive": "DOCX",
        "Failed to open stream": "PDF",
        "File is not a zip file": "DOCX",
        "EOF marker not found": "不完整",
        "Syntax Error: Couldn't read xref table": "损坏",
    }
    for raw, needle in cases.items():
        reason = core.friendly_extraction_reason(raw)
        assert needle in reason, (raw, reason)
        assert raw not in reason, f"原始英文异常不应出现在用户可见原因里：{reason}"
        assert any("\u4e00" <= char <= "\u9fff" for char in reason), reason

    # 空文本 -> "没有可翻译的文本"（而不是"未知提取错误"）
    empty_reason = core.friendly_extraction_reason("")
    assert "没有可翻译的文本" in empty_reason
    assert "OCR" in empty_reason, "要告诉用户扫描件可以启用 OCR"

    # 无法识别的信息保留原文（不假装知道原因）
    assert core.friendly_extraction_reason("totally unknown problem") == \
        "totally unknown problem"


def test_broken_docx_reports_a_readable_reason_end_to_end(tmp_path, monkeypatch):
    """端到端：导入损坏的 DOCX 并启动，运行视图给出的原因必须是中文可行动的。"""
    import json
    import subprocess
    import time
    import zipfile

    scripts = ROOT / "scripts"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))

    from folith.qt import adapter

    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("hello.txt", "not a docx")

    ready = tmp_path / "ready.json"
    stub = subprocess.Popen(
        [sys.executable, str(scripts / "d0_fake_llm_server.py"), "--port", "0",
         "--log", str(tmp_path / "stub.jsonl"), "--ready-file", str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.1)
        url = json.loads(ready.read_text(encoding="utf-8"))["url"]
        config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="k",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        job_id = adapter.import_documents([broken], config)[0].job_id
        adapter.start_job(job_id, "broken.docx", broken.read_bytes(), config)
        detail = ""
        for _ in range(240):
            runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
            detail = str(runtime.get("detail") or "")
            if runtime.get("status") in ("failed", "completed", "cancelled"):
                break
            time.sleep(0.5)
        assert "无法读取这个 DOCX" in detail, detail
        assert "Content_Types" not in detail, f"不应泄露原始异常：{detail}"
        assert "未提取到有效文本" in detail
    finally:
        stub.terminate()
        stub.wait(timeout=30)
