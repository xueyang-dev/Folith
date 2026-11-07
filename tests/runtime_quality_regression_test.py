"""System-level regression coverage for the long-document runtime contract."""
import base64
import io
import json
import re
import sys

import fitz
import pytest
from docx import Document

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import core
from folith import assets, context, delivery, entity_registry, knowledge, model_roles
from folith.engine import delivery_state as delivery_state_module
from folith.translation_protocol import (
    TranslationProtocolError,
    parse_translation_array,
    parse_translation_response,
)


def _make_docx(paragraphs):
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _make_layout_pdf():
    """Create a small real PDF with an image/caption interruption and furniture."""
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    document = fitz.open()
    for page_number in range(2):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 30), "Folith running header", fontsize=9)
        page.insert_text((72, 100), "The winds, temperature, and", fontsize=11)
        if page_number == 0:
            page.insert_image(fitz.Rect(72, 115, 500, 250), stream=image)
            page.insert_text(
                (90, 265),
                "Figure 1. Odilon Redon, The Eye Like a Strange Balloon",
                fontsize=8,
            )
            page.insert_text(
                (72, 285), "air pressure all play a part, and", fontsize=11
            )
            page.insert_text(
                (72, 715), "1. This footnote must not enter the body.", fontsize=7
            )
        else:
            page.insert_text(
                (72, 115), "the experiment continues on the next page.", fontsize=11
            )
        page.insert_text((72, 780), str(page_number + 1), fontsize=9)
    try:
        return document.tobytes()
    finally:
        document.close()


def test_pdf_layout_roles_keep_caption_and_furniture_out_of_body():
    pdf_bytes = _make_layout_pdf()
    blocks = core._pdf_ingestion.extract_layout_blocks(pdf_bytes)
    assert blocks and {"page_number", "bbox", "font_size", "font_flags",
                       "font_style", "block_id", "reading_order",
                       "relative_width", "page_width", "page_height"} <= set(blocks[0])
    classified = core._pdf_ingestion.classify_blocks(blocks)
    roles = {block.get("role") for block in classified}
    assert {"caption", "footnote", "header", "page_number", "image"} <= roles

    paragraphs = core.extract_pdf_paragraphs(pdf_bytes)
    joined = " ".join(paragraphs)
    assert "The winds, temperature, and air pressure all play a part, and" in joined
    assert "the experiment continues on the next page." in joined
    assert "Odilon Redon" not in joined
    assert "This footnote must not enter the body" not in joined
    assert "running header" not in joined
    assert not any(re.fullmatch(r"\d{1,4}", paragraph) for paragraph in paragraphs)


def test_translation_protocol_has_one_canonical_rejection_point():
    with pytest.raises(TranslationProtocolError):
        parse_translation_response('["A", "B"]', 1)
    with pytest.raises(TranslationProtocolError):
        parse_translation_response('{"foo": "bar"}', 1)
    assert parse_translation_response("正常译文", 1) == ["正常译文"]
    assert parse_translation_response('["甲", "乙"]', 2) == ["甲", "乙"]
    assert parse_translation_response("1. 甲\n2. 乙", 2) == ["甲", "乙"]
    assert parse_translation_array('["A", "B"]', 1) is None


def test_malformed_translation_response_retries_and_never_becomes_target():
    calls = []

    def malformed(*args, **kwargs):
        calls.append((args, kwargs))
        return '["A", "B"]'

    with pytest.raises(RuntimeError, match="批次翻译失败"):
        core.translate_batch(
            ["One source sentence."], [], [], "", "", "中文",
            "unknown", "key", "model", call_llm_fn=malformed
        )
    assert len(calls) == 3


def test_delivery_gate_rechecks_injected_transport_wrapper(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "target-invariant-0001"
        state = core.new_job_state("fixture.docx")
        state.update(
            p1_done=True,
            p2_done=True,
            paras=["A source paragraph."],
            pairs=[{"source": "A source paragraph.", "target": '["A", "B"]'}],
            target_lang="简体中文",
            delivery_status="final",
        )
        core.save_job_state(job_id, state)
        with pytest.raises(RuntimeError, match="Translation Target Invariant"):
            core.build_delivery_assets(job_id)
        loaded = core.load_job_state(job_id)
        assert loaded["delivery_validation"]["blocking"] is True
        assert loaded["delivery_status"] != "final"
        assert any(
            finding.get("type") == "delivery_invariant"
            and finding.get("invariant_code") == "transport_wrapper"
            for finding in loaded["findings"]
        )
        loaded, approved, reasons = core.approve_delivery(job_id)
        assert not approved and reasons
        assert loaded["delivery_status"] != "final"
    finally:
        core.OUTPUT_DIR = old_output


def test_standard_runtime_runs_understanding_and_injects_it_into_translation_context(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    prompts = []
    try:
        def llm(provider, key, model, system, user, temperature=0.1):
            prompts.append((system, user))
            if "文档画像" in system:
                return json.dumps({
                    "domain": "翻译学", "genre": "学术专著", "register": "正式书面语",
                    "confidence": 0.9,
                    "sections": [{"section_id": "s1", "start_segment": 0,
                                  "end_segment": 1, "topic": "理论"}],
                })
            if "文档理解器" in system and "全书理解器" not in system:
                return json.dumps({
                    "summary": "当前单元摘要内容",
                    "key_terms": ["理论连续性"],
                    "translation_notes": ["保持指代连续"],
                })
            if "全书理解器" in system:
                return json.dumps({
                    "summary": "全文概要内容", "document_arc": "全文论证发展",
                    "themes": ["理论"],
                })
            if "翻译流知识抽取器" in system:
                return "[]"
            if "学术翻译专家" in system:
                current = user.split("【待翻译段落（按序号返回等长数组）】", 1)[-1]
                count = len(re.findall(r"(?m)^\d+\.\s+", current))
                return json.dumps(["这是完整的中文译文。"] * count)
            raise AssertionError(f"unexpected mock prompt: {system[:80]}")

        core.call_llm = llm
        state = core.run_job_pipeline(
            "standard-understanding-0001", "fixture.docx",
            _make_docx(["The first theoretical sentence.", "The second sentence follows."]),
            provider="DeepSeek", api_key="key", model="model-standard",
            target_lang="简体中文", auto_term=False, enable_report=False,
            enable_review=False, enable_annotate=False,
            translation_theory="功能对等理论", strict_terminology_governance=False,
        )
        assert state["profile_done"] and state["understanding_done"]
        assert state["semantic_units"] and state["section_digests"]
        assert state["document_synopsis"]["summary"] == "全文概要内容"
        translation_prompts = [user for system, user in prompts if "学术翻译专家" in system]
        assert translation_prompts
        assert "全文概要内容" in translation_prompts[0]
        assert "当前单元摘要内容" in translation_prompts[0]
        assert state["context_packet_log"][0]["current_batch_count"] == 2
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


def test_standard_continuity_observation_is_bounded_and_available_without_review(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    calls = []
    try:
        paragraphs = [
            "The volumetric sensing method appears in this paragraph."
            if index in (0, 4) else f"A contextual paragraph number {index}."
            for index in range(8)
        ]

        def llm(provider, key, model, system, user, temperature=0.1):
            calls.append((provider, model, system, user))
            if "翻译流知识抽取器" in system:
                observations = []
                for match in re.finditer(
                    r"segment_id:\s*(\d+)\n原文：(.*?)\n译文：(.*?)(?=\n\nsegment_id:|$)",
                    user, re.S,
                ):
                    segment_id, source, target = match.groups()
                    if "volumetric sensing" in source:
                        observations.append({
                            "segment_id": int(segment_id),
                            "source_expression": "volumetric sensing",
                            "observed_target": "体积感知",
                            "kind": "term",
                        })
                return json.dumps(observations)
            if "学术翻译专家" in system:
                current = user.split("【待翻译段落（按序号返回等长数组）】", 1)[-1]
                current_sources = re.findall(r"(?m)^\d+\.\s+(.+)$", current)
                return json.dumps([
                    "体积感知在这里。" if "volumetric sensing" in source
                    else "这是完整的中文译文。"
                    for source in current_sources
                ])
            raise AssertionError(f"unexpected prompt: {system[:80]}")

        core.call_llm = llm
        state = core.new_job_state("continuity.docx")
        state.update(p1_done=True, paras=paragraphs, quality_mode=False)
        result = core.translate_stage(
            state, "continuity-0001", [], "DeepSeek", "key", "model-standard",
            "简体中文", "", enable_review=False, use_tm=False,
        )
        assert result["pairs"] and len(result["pairs"]) == 8
        candidates = result["translation_continuity"]
        assert candidates and candidates[0]["provenance"] == "generated_continuity"
        later_translation_users = [
            user for _, _, system, user in calls if "学术翻译专家" in system
        ]
        assert len(later_translation_users) == 2
        assert "volumetric sensing -> 体积感知" in later_translation_users[1]
        assert all(item.get("scope") == "document" for item in candidates)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


def test_auxiliary_model_and_adaptive_feedback_policy_are_audited(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    calls = []
    try:
        paragraphs = [f"A paragraph containing a recurring term number {index}."
                      for index in range(16)]

        def llm(provider, key, model, system, user, temperature=0.1, **kwargs):
            calls.append((provider, model, system))
            if "翻译流知识抽取器" in system:
                return json.dumps([{
                    "segment_id": 0,
                    "source_expression": "recurring term",
                    "observed_target": "重复术语",
                    "kind": "term",
                }])
            if "学术翻译专家" in system:
                current = user.split("【待翻译段落（按序号返回等长数组）】", 1)[-1]
                count = len(re.findall(r"(?m)^\d+\.\s+", current))
                return json.dumps(["重复术语在这里。"] * count)
            raise AssertionError(f"unexpected prompt: {system[:80]}")

        core.call_llm = llm
        state = core.new_job_state("adaptive.docx")
        state.update(p1_done=True, paras=paragraphs, quality_mode=False)
        result = core.translate_stage(
            state, "adaptive-0001", [], "DeepSeek", "main-key", "main-model",
            "简体中文", "", enable_review=False, use_tm=False,
            auxiliary_config={"provider": "DeepSeek", "api_key": "aux-key",
                              "model": "cheap-model"},
        )
        policy = result["knowledge_feedback_policy"]
        # 16 paragraphs at the default batch size is a 4-batch document.  Only
        # the first and final batch are observed: no batch carries a name-like
        # form the project does not already hold and the periodic checkpoint
        # (interval 4) does not fall inside a 4-batch document.
        assert policy["interval"] == 4
        assert policy["observed_batches"] == [0, 3]
        assert policy["skipped_batches"] == [1, 2]
        assert policy["skip_reasons"] == {"no_signal": 2}
        assert any(provider == "DeepSeek" and model == "cheap-model"
                   for provider, model, system in calls
                   if "翻译流知识抽取器" in system)
        assert all(model != "cheap-model" for provider, model, system in calls
                   if "学术翻译专家" in system)
        assert result["llm_usage"]["by_role"]["translation"]["calls"] > 0
        assert result["llm_usage"]["by_role"]["auxiliary"]["calls"] > 0
        assert result["llm_usage"]["estimated_total_tokens"] > 0
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


def test_locked_glossary_and_human_entity_choice_outrank_generated_observations():
    candidates = [{
        "source": "volumetric sensing", "observed_target": "体积感知",
        "kind": "term", "provenance": "generated_continuity",
        "scope": "document", "confidence": 0.35,
    }]
    assert knowledge.provisional_hints(
        candidates,
        authoritative_entries=[{
            "source": "volumetric sensing", "target": "容积传感", "status": "locked",
        }],
    ) == []

    registry = entity_registry.EntityRegistry()
    registry.observe(
        "The Repellent Fence", "《驱离之篱》", entity_type="artwork",
        segment_id=10, provenance="generated_observation", confidence=0.35,
    )
    assert registry.hints_for(["The Repellent Fence"])[0]["preferred_target"] == "《驱离之篱》"
    registry.lock("The Repellent Fence", "《驱逐围栏》", entity_type="artwork")
    registry.observe(
        "The Repellent Fence", "《错误译名》", entity_type="artwork",
        segment_id=100, provenance="generated_observation", confidence=0.2,
    )
    hint = registry.hints_for(["The Repellent Fence"])[0]
    assert hint["preferred_target"] == "《驱逐围栏》"
    assert hint["status"] == "locked"
    assert registry.consistency_findings()


def test_translator_and_reviewer_role_routing_is_distinct(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    calls = []
    try:
        def llm(provider, key, model, system, user, temperature=0.1):
            calls.append((provider, model, system))
            if "独立的翻译审校专家" in system:
                return "[]"
            if "翻译流知识抽取器" in system:
                return "[]"
            if "学术翻译专家" in system:
                return '["这是完整的中文译文。"]'
            raise AssertionError(f"unexpected prompt: {system[:80]}")

        core.call_llm = llm
        state = core.new_job_state("roles.docx")
        state.update(p1_done=True, paras=["One source sentence."])
        result = core.translate_stage(
            state, "roles-0001", [], "translator-provider", "translator-key",
            "model-A", "简体中文", "", enable_review=True, use_tm=False,
            translator_config={"provider": "translator-provider", "api_key": "translator-key",
                               "model": "model-A"},
            reviewer_config={"provider": "reviewer-provider", "api_key": "reviewer-key",
                             "model": "model-B"},
        )
        assert result["pairs"][0]["reviewed"] is True
        translation_calls = [item for item in calls if "学术翻译专家" in item[2]]
        review_calls = [item for item in calls if "独立的翻译审校专家" in item[2]]
        assert translation_calls and all(item[:2] == ("translator-provider", "model-A")
                                         for item in translation_calls)
        assert review_calls and all(item[:2] == ("reviewer-provider", "model-B")
                                    for item in review_calls)
        manifest = assets.build_delivery_manifest(
            result, "roles-0001", "简体中文", "translator-provider", "model-A",
            translator_config={"provider": "translator-provider", "model": "model-A",
                               "api_key": "must-not-be-exported"},
            reviewer_config={"provider": "reviewer-provider", "model": "model-B",
                             "api_key": "must-not-be-exported"},
        )
        assert manifest["translator"]["model"] == "model-A"
        assert manifest["reviewer"]["model"] == "model-B"
        assert "api_key" not in json.dumps(manifest, ensure_ascii=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


def test_provider_capability_unknown_endpoint_safely_falls_back_to_parser():
    native = core._native_translation_response_format("OpenAI", "gpt-4.1", 2)
    assert native and native["type"] == "json_schema"
    assert core._native_translation_response_format("DeepSeek", "deepseek-v4", 2) is None
    assert model_roles.provider_capabilities(core.PROVIDERS, "unlisted-relay")["plain_text_only"]


def test_restart_preserves_context_registry_and_provenance(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        state = core.new_job_state("restart.docx")
        state.update(
            semantic_units=[{"unit_id": "unit-0001", "start_segment": 0,
                             "end_segment": 0, "source": "source"}],
            section_digests=[{"unit_id": "unit-0001", "start_segment": 0,
                              "end_segment": 0, "summary": "摘要"}],
            document_synopsis={"summary": "概要", "status": "model"},
            translation_continuity=[{
                "source": "volumetric sensing", "observed_target": "体积感知",
                "provenance": "generated_continuity", "scope": "document",
            }],
            entity_registry=[{
                "source_form": "The Repellent Fence", "preferred_target": "《驱离之篱》",
                "entity_type": "artwork", "provenance": "generated_observation",
                "status": "observed", "confidence": 0.35,
            }],
        )
        core.save_job_state("restart-0001", state)
        loaded = core.load_job_state("restart-0001")
        assert loaded["section_digests"][0]["summary"] == "摘要"
        assert loaded["document_synopsis"]["summary"] == "概要"
        assert loaded["translation_continuity"][0]["provenance"] == "generated_continuity"
        assert loaded["entity_registry"][0]["entity_type"] == "artwork"
    finally:
        core.OUTPUT_DIR = old_output


def test_malformed_translation_does_not_pollute_tm_knowledge_or_entities(tmp_path):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        def malformed(*args, **kwargs):
            return '["译文 A", "译文 B"]'

        core.call_llm = malformed
        state = core.new_job_state("malformed.docx")
        state.update(p1_done=True, paras=["One source sentence."])
        with pytest.raises(RuntimeError):
            core.translate_stage(
                state, "malformed-0001", [], "DeepSeek", "key", "model",
                "简体中文", "", enable_review=False, use_tm=True,
            )
        assert state["pairs"] == []
        assert state["knowledge_candidates"] == []
        assert state["entity_registry"] == []
        assert core.load_tm() == {}
        candidates, events, warning = knowledge.observe_batch(
            ["One source sentence."], ['["译文 A", "译文 B"]'],
            ["One source sentence."], [], [], 0, "DeepSeek", "key", "model",
            call_llm=lambda *args, **kwargs: '[{"source_expression":"source",'
            '"observed_target":"译文","kind":"term"}]',
        )
        assert candidates == [] and events == [] and warning
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


# ================= 交付门禁与持久化发现项的对账（issue 2 / issue 3） =================
#
# 交付门禁的报告（validate_delivery_translation_state）是唯一真值来源：
# * 旧任务里持久化的 delivery_invariant 若当前报告不再出现，必须在打开任务时
#   标记 resolved（带审计记录），不能永远挡住交付；
# * 报告里真实存在的确定性阻断项（漏译/截断等）必须进入 review queue，否则
#   首页/交付页的 status_label 会和门禁互相矛盾；
# * 对账只清「当前报告不再报告」的项，模型审校发现等无关项绝不误清。


def test_open_path_repairs_only_stale_delivery_invariants(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "gate-reconcile-0001"
        wrapper_reason = "译文仍是 JSON/Markdown transport wrapper，不能作为普通正文交付"
        state = core.new_job_state("gate.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=["[1]", "A source paragraph.", "Another source paragraph."],
            pairs=[{"source": "[1]", "target": "[1]"},
                   {"source": "A source paragraph.", "target": '["真实包装"]'},
                   {"source": "Another source paragraph.", "target": "另一段译文"}],
            findings=[
                # 脚注编号 [1] 现在已豁免，这条旧 invariant 是过时的。
                {"type": "delivery_invariant", "severity": "blocking",
                 "segment_index": 0, "segment_id": 0,
                 "invariant_code": "transport_wrapper",
                 "summary": wrapper_reason, "reason": wrapper_reason},
                # 这一段确实还是 transport wrapper：必须保留。
                {"type": "delivery_invariant", "severity": "blocking",
                 "segment_index": 1, "segment_id": 1,
                 "invariant_code": "transport_wrapper",
                 "summary": wrapper_reason, "reason": wrapper_reason},
                # 旧数据可能没有 invariant_code，只能按 reason 文本匹配。
                {"type": "delivery_invariant", "severity": "blocking",
                 "segment_index": 2, "segment_id": 2,
                 "summary": wrapper_reason, "reason": wrapper_reason},
                # 模型审校发现与交付门禁无关，任何对账都不能清掉。
                {"type": "check", "severity": "blocking",
                 "segment_index": 2, "segment_id": 2,
                 "category": "semantic_accuracy",
                 "summary": "语义偏差", "reason": "译文与原文含义不一致",
                 "detector": "Translation Core"},
            ],
            delivery_status="draft",
        )
        core.save_job_state(job_id, state)

        fresh, report = delivery_state_module.reconcile_delivery_gate(job_id)
        invariants = {finding.get("segment_index"): finding
                      for finding in fresh["findings"]
                      if finding.get("type") == "delivery_invariant"}
        assert invariants[0]["resolved"] is True
        assert invariants[0]["resolution"]["action"] == "target_rechecked"
        assert invariants[0]["resolution"]["actor"] == "system"
        assert invariants[2]["resolved"] is True
        # 真实阻断项保留，没有被「全量对账」误清。
        assert invariants[1].get("resolved") is not True
        review_finding = next(item for item in fresh["findings"]
                              if item.get("detector") == "Translation Core")
        assert review_finding.get("resolved") is not True
        assert report["blocking"] is True
        assert fresh["has_blocking"] is True
        assert fresh["review_stats"]["blocking"] == 2
        assert fresh["delivery_status"] == "review_required"
        assert any(action.get("action") == "target_rechecked"
                   for action in fresh["human_actions"])
        # 打开路径只清过时项，不新增当前报告里的阻断项。
        assert len(fresh["findings"]) == 4

        # 修复后再次打开不应重复写盘（只读轮询零写入）。
        signature = core._job_repo.state_signature(job_id)
        delivery_state_module.reconcile_delivery_gate(job_id)
        assert core._job_repo.state_signature(job_id) == signature
    finally:
        core.OUTPUT_DIR = old_output


def test_open_path_never_adds_or_reopens_current_blockers(tmp_path):
    """打开任务不是交付决定：报告里当前存在的阻断项不在这里写进 findings。

    900 段合成书有 450 段触发确定性阻断；若打开时全部持久化，一次 open 会
    写入 450 条 finding、推进 revision，poll 契约与内存/卡顿都会退化。新增 /
    重开只允许发生在显式交付、审校动作（_record_delivery_validation_findings）。
    """
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "gate-blocker-0001"
        source = "At thirty-six, he was still renting in L.A. with his wife."
        truncated = "三十六岁了，他和妻子仍在洛杉矶租房住。"
        reason = "疑似漏译/截断：原文 58 字符/3 句，译文仅 19 字符/1 句"
        state = core.new_job_state("Bad Words.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=[source], pairs=[{"source": source, "target": truncated}],
            findings=[{
                "segment_index": 0, "segment_id": 0, "type": "check",
                "severity": "blocking", "category": "completeness",
                "summary": "译文疑似遗漏或被截断", "reason": reason,
                "detector": "Deterministic QA",
                "resolved": True,
                "resolution": {"action": "human_fixed",
                               "note": "人工核对后确认问题已解决",
                               "timestamp": "2026-11-03T06:23:44+00:00",
                               "actor": "user"},
            }],
            delivery_status="draft",
        )
        core.save_job_state(job_id, state)
        report = core.validate_delivery_translation_state(state)
        assert [finding.get("summary") for finding in report["blocking_findings"]] == \
            ["译文疑似遗漏或被截断"]

        signature = core._job_repo.state_signature(job_id)
        fresh, _report = delivery_state_module.reconcile_delivery_gate(job_id)
        assert core._job_repo.state_signature(job_id) == signature, "打开路径不得写盘"
        assert len(fresh["findings"]) == 1
        assert fresh["findings"][0].get("resolved") is True
        assert fresh["findings"][0].get("gate_source") is None
        assert delivery.unresolved_blocking(fresh) == []
        # 首页零写入也要与门禁一致：人工 resolved 但文本仍截断 → 不得承诺可冻结。
        assert core.task_status_label(fresh) == "暂不满足交付条件"
    finally:
        core.OUTPUT_DIR = old_output


def test_review_action_reopens_deterministic_blocker_and_open_clears_it_when_fixed(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "gate-blocker-0002"
        source = "At thirty-six, he was still renting in L.A. with his wife."
        truncated = "三十六岁了，他和妻子仍在洛杉矶租房住。"
        reason = "疑似漏译/截断：原文 58 字符/3 句，译文仅 19 字符/1 句"
        state = core.new_job_state("Bad Words.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=[source], pairs=[{"source": source, "target": truncated}],
            findings=[{
                "segment_index": 0, "segment_id": 0, "type": "check",
                "severity": "blocking", "category": "completeness",
                "summary": "译文疑似遗漏或被截断", "reason": reason,
                "detector": "Deterministic QA",
                "resolved": True,
                "resolution": {"action": "human_fixed",
                               "note": "人工核对后确认问题已解决",
                               "timestamp": "2026-11-03T06:23:44+00:00",
                               "actor": "user"},
            }],
            delivery_status="draft",
        )
        core.save_job_state(job_id, state)

        # 显式审校/交付动作：报告仍报它 → reopen 并写盘（唯一允许新增/重开的路径）。
        current = core.load_job_state(job_id)
        report = core.validate_delivery_translation_state(current)
        core._record_delivery_validation_findings(current, report)
        core.save_job_state(job_id, current)
        reopened = current["findings"][0]
        assert reopened.get("resolved") is False
        assert reopened.get("gate_source") == delivery_state_module.DELIVERY_GATE_SOURCE
        assert core.task_status_label(current) == "待审校"
        assert len(delivery.unresolved_blocking(current)) == 1
        assert current["review_stats"]["blocking"] == 1

        # 文本真的修好后：打开路径只清过时项（不再报 → resolved + 审计记录）。
        current["pairs"][0]["target"] = (
            "三十六岁时，他和妻子仍在洛杉矶租房住。他们还没有自己的房子。日子并不宽裕。")
        core.save_job_state(job_id, current)
        healed, healed_report = delivery_state_module.reconcile_delivery_gate(job_id)
        assert healed_report["blocking"] is False
        assert delivery.unresolved_blocking(healed) == []
        cleared = healed["findings"][0]
        assert cleared["resolved"] is True
        assert cleared["resolution"]["action"] == "target_rechecked"
        assert healed["review_stats"]["blocking"] == 0
        assert core.task_status_label(healed) == "可以冻结交付"
    finally:
        core.OUTPUT_DIR = old_output


def test_open_path_never_rewrites_a_frozen_task(tmp_path):
    """冻结快照的 state identity 不能被打开动作改写，否则会误报"已偏离"。"""
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "gate-final-0001"
        wrapper_reason = "译文仍是 JSON/Markdown transport wrapper，不能作为普通正文交付"
        state = core.new_job_state("frozen.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=["A source paragraph."],
            pairs=[{"source": "A source paragraph.", "target": "一段正常译文"}],
            findings=[{"type": "delivery_invariant", "severity": "blocking",
                       "segment_index": 0, "segment_id": 0,
                       "invariant_code": "transport_wrapper",
                       "summary": wrapper_reason, "reason": wrapper_reason}],
            delivery_status="final",
        )
        core.save_job_state(job_id, state)
        signature = core._job_repo.state_signature(job_id)

        fresh, _report = delivery_state_module.reconcile_delivery_gate(job_id)
        assert fresh["delivery_status"] == "final"
        assert fresh["findings"][0].get("resolved") is not True
        assert core._job_repo.state_signature(job_id) == signature
    finally:
        core.OUTPUT_DIR = old_output


def test_open_path_does_not_invent_document_level_blockers(tmp_path):
    """打开任务不是交付决定：文档级门禁问题仍由交付/审校动作记录。

    pair_count_mismatch / source_pages_missing 没有 segment 可对账；打开路径
    只修复按 segment 定位的过时 invariant 与确定性 QA 阻断项，不在这里改写
    文档级结论（也避免每次打开都推进 state revision）。
    """
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "gate-document-level-0001"
        state = core.new_job_state("mismatch.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=["甲。", "乙。"],
            pairs=[{"source": "甲。", "target": "A."}],
            delivery_status="draft",
        )
        core.save_job_state(job_id, state)
        report = core.validate_delivery_translation_state(state)
        assert report["blocking"] is True
        signature = core._job_repo.state_signature(job_id)
        fresh, _report = delivery_state_module.reconcile_delivery_gate(job_id)
        assert fresh["findings"] == []
        assert core._job_repo.state_signature(job_id) == signature
    finally:
        core.OUTPUT_DIR = old_output
