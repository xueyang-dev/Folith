"""发布前一条真实任务链的离线验收。

这不是模型质量测试。它用确定性 provider 替身，验证新人完成一条任务后，
源文档噪声、翻译中断恢复、段落结构调整、人工修改、独立审校和交付冻结
仍然共享同一份任务真值，并且排除的段落在交付包里留下可追溯记录。
"""

from __future__ import annotations

import io
import json
import re

from docx import Document

import core


def _docx_bytes(paragraphs: list[str]) -> bytes:
    buffer = io.BytesIO()
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(buffer)
    return buffer.getvalue()


def _translation_llm(*args, **kwargs):
    system = str(args[3] if len(args) > 3 else kwargs.get("system_prompt", ""))
    user = str(args[4] if len(args) > 4 else kwargs.get("user_prompt", ""))
    if "专业翻译人员" in system:
        section = user.rsplit("待翻译段落", 1)[-1]
        sources = re.findall(r"^\s*\d+\.\s+(.+?)\s*$", section, re.MULTILINE)
        return json.dumps(
            [f"第 {index + 1} 段中文译文" for index, _ in enumerate(sources)],
            ensure_ascii=False,
        )
    if "翻译审校专家" in system or "独立的翻译审校专家" in system:
        return "[]"
    return "[]"


def test_new_task_noisy_import_to_frozen_delivery(tmp_path, monkeypatch):
    """导入→中断恢复→排除/拆分/合并→人工修改→审校→交付。"""
    job_id = "releasee2e000000001"
    old_output = core.OUTPUT_DIR
    old_call = core.call_llm
    core.OUTPUT_DIR = tmp_path

    paragraphs = [
        "Alpha source sentence.",
        "Page 3",  # 正文提取能看见，但应由译者排除的页码噪声。
        "Beta source sentence.",
        "Gamma source sentence.",
    ]
    source = _docx_bytes(paragraphs)
    pipeline = dict(
        provider="DeepSeek",
        api_key="test-key",
        model="deepseek-chat",
        target_lang="简体中文",
        auto_term=False,
        enable_report=False,
        translation_theory="目的论",
        enable_review=False,
        enable_annotate=False,
        use_tm=False,
        enable_understanding=False,
        enable_source_cleanup=False,
        delivery_config=core.default_delivery_config(),
    )

    def interrupted_llm(*args, **kwargs):
        system = str(args[3] if len(args) > 3 else kwargs.get("system_prompt", ""))
        if "专业翻译人员" in system:
            raise RuntimeError("simulated interruption")
        return "[]"

    try:
        monkeypatch.setattr(core, "call_llm", interrupted_llm)
        try:
            core.run_job_pipeline(job_id, "new-noisy.docx", source, **pipeline)
        except RuntimeError as exc:
            assert "simulated interruption" in str(exc)

        interrupted = core.load_job_state(job_id)
        assert interrupted["p1_done"] is True
        assert interrupted["p2_done"] is False
        assert interrupted["paras"] == paragraphs

        # 重新打开任务后继续处理，验证源文件和阶段一 checkpoint 可恢复。
        monkeypatch.setattr(core, "call_llm", _translation_llm)
        state = core.run_job_pipeline(job_id, "new-noisy.docx", None, **pipeline)
        assert state["p2_done"] is True
        assert len(state["pairs"]) == 4

        # 译者排除页码，并把一段拆开后再合并，确认结构操作保留可编辑真值。
        state = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="页码噪声")
        state = core.mutate_translation_segments(
            job_id,
            1,
            "split",
            source_a="Beta first",
            source_b="Beta second",
            target_a="贝塔前半",
            target_b="贝塔后半",
        )
        state = core.mutate_translation_segments(job_id, 1, "merge")
        assert len(state["pairs"]) == 3
        assert state["excluded_segments"][0]["source"] == "Page 3"
        assert "\n" in state["pairs"][1]["source"]

        core.save_translation_edit(job_id, 0, "人工修订后的译文")
        reviewed_state, review_result = core.review_translation_segments(
            job_id,
            list(range(3)),
            "DeepSeek",
            "test-key",
            "deepseek-chat",
            "简体中文",
            style_rules="",
        )
        assert review_result["reviewed_segment_ids"] == [0, 1, 2]
        assert review_result["failed_segment_ids"] == []
        assert reviewed_state["review_stats"]["reviewed_segments"] == 3

        state = core.load_job_state(job_id)
        assets = core.build_delivery_assets(
            job_id, state, target_lang="简体中文", provider="DeepSeek", model="deepseek-chat")
        assert {
            "translation.docx",
            "bilingual.docx",
            "excluded_segments.md",
            "excluded_segments.json",
            "delivery_manifest.json",
        }.issubset(assets), sorted(assets)

        frozen, ok, errors = core.approve_delivery(
            job_id,
            note="发布前端到端验收",
            target_lang="简体中文",
            provider="DeepSeek",
            model="deepseek-chat",
        )
        assert ok, errors
        assert frozen["delivery_status"] == "final"
        assert frozen["latest_delivery_snapshot_version"] == 1
    finally:
        core.OUTPUT_DIR = old_output
        core.call_llm = old_call
