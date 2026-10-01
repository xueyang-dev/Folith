"""Phase 3 Streamlit review-workbench interaction regressions."""
from pathlib import Path

import core
from folith import assets, delivery, translation_evidence
from folith.translation_core import fingerprint, normalize_finding
from folith.workbench_view import review_workbench_view


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _event(segment_id, event_id, value, decision="findings"):
    return {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_id],
        "decision": decision,
        "freshness_status": "current",
        "stale_segment_ids": [],
        "completion_receipt": {
            "status": "completed", "reviewed_segment_ids": [segment_id],
        },
        "translation_core": {"final_consumed_input_fingerprint": value},
    }


def _finding(segment_id, event_id, value, summary, location, *,
             suggested_target="", category="completeness"):
    finding = normalize_finding({
        "category": category,
        "severity": "blocking",
        "status": "open",
        "segment_id": segment_id,
        "location_key": location,
        "requires_human_confirmation": True,
        "summary": summary,
        "source_span": "Source",
        "target_span": "译文",
        "explanation": "当前译文需要人工确认。",
        "recommendation": "核对后选择安全动作。",
        "detector": "Semantic QA",
    }, input_fingerprint=value)
    finding.update({
        "type": "review", "segment_index": segment_id,
        "review_event_id": event_id, "reason": summary,
    })
    if suggested_target:
        finding["suggested_target"] = suggested_target
    return finding


def _state(findings_by_segment, *, suggested=False, style=False,
           review_required=True, legacy=False):
    state = core.new_job_state("phase3-workbench.docx")
    pairs = [
        {"source": f"Source {index + 1}", "target": f"译文 {index + 1}",
         "initial_target": f"译文 {index + 1}", "reviewed": False}
        for index in range(max(findings_by_segment) + 1)
    ]
    findings, events = [], []
    for segment_id, count in findings_by_segment.items():
        value = fingerprint({"segment": segment_id, "target": pairs[segment_id]["target"]})
        event_id = f"review-{segment_id}"
        events.append(_event(segment_id, event_id, value))
        for ordinal in range(count):
            finding = _finding(
                segment_id, event_id, value,
                f"第 {segment_id + 1} 段问题 {ordinal + 1}",
                f"source-offset:{ordinal}",
                suggested_target="建议译文" if suggested and ordinal == 0 else "",
                category="style" if style and ordinal == 0 else "completeness",
            )
            if legacy:
                finding.pop("finding_id", None)
                finding.pop("input_fingerprint", None)
            findings.append(finding)
    state.update(
        p1_done=True,
        p2_done=True,
        report_enabled=False,
        translation_core_review_required=review_required,
        target_lang="简体中文",
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        findings=findings,
        review_evidence=events,
        human_actions=[],
        review_stats={"reviewed_segments": 0},
        delivery_status="review_required",
    )
    return state


def _open_workspace(job_id, section, *, api_key=""):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    at.session_state["provider_choice"] = "DeepSeek"
    at.session_state["api_key_DeepSeek"] = api_key
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = section
    at.run()
    assert not at.exception, at.exception
    return at


def _settle_segment_action(at, job_id):
    """Wait for mocked background work and render its completion feedback."""
    worker = core._RUNTIME_WORKERS.get(job_id)
    if worker:
        worker.join(timeout=3)
        assert not worker.is_alive()
    at.run()
    assert not at.exception, at.exception


def _queue(at):
    return next(item for item in at.radio if item.label == "审校队列")


def _save_row_editor(at, job_id, index):
    """提交中央网格第 `index` 行的行内译文表单。

    译文编辑器是段落网格的一部分（`cat_save_btn_{job_id}_{index}`）；
    右栏 Inspector 只读不写，所以这里按行内保存按钮定位。
    保存按钮只在有未保存改动时渲染，所以先 rerun 让 dirty 状态生效。
    """
    at.run()
    return next(button for button in at.button
                if button.key == f"cat_save_btn_{job_id}_{index}").click()


def test_review_decisions_follow_evidence_in_the_inspector(
        tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3actionbar01"
        core.save_job_state(job_id, _state({0: 1}))
        at = _open_workspace(job_id, "review", api_key="translator-key")
        labels = [button.label for button in at.button]

        assert "继续处理 1 个必须处理的问题" not in labels
        for label in ("确认已解决", "修改译文", "保留当前译文", "重新翻译并复审"):
            assert label in labels
        assert labels.index("确认已解决") < labels.index("重新翻译并复审")
        markup = "\n".join(item.value for item in at.markdown)
        assert "tp-review-readiness is-" not in markup
        assert markup.index("tp-review-problem") < markup.index('<h4>证据</h4>') < markup.index('<h4>建议</h4>')
        assert any(expander.label == "历史" for expander in at.expander)
    finally:
        core.OUTPUT_DIR = old_output


def test_legacy_no_review_delivery_gate_is_not_applicable(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3deliverylegacy1"
        core.save_job_state(job_id, _state(
            {0: 1}, review_required=False, legacy=True))
        at = _open_workspace(job_id, "delivery", api_key="translator-key")
        rendered = "\n".join(item.value for item in at.markdown)

        assert "当前任务未启用独立审校（翻译审校不适用）" in rendered
        assert "翻译审校已完成" not in rendered
    finally:
        core.OUTPUT_DIR = old_output


def test_suggested_target_action_edits_rereviews_and_clears_current_work(
        tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3suggested01"
        state = _state({0: 1}, suggested=True)
        core.save_job_state(job_id, state)

        def fake_review(job_id_, indexes, provider, api_key, model, target_lang,
                        *, style_rules="", call_llm_fn=None, base_url=None):
            current = core.load_job_state(job_id_)
            reviewed = []
            for segment_id in indexes:
                pair = current["pairs"][segment_id]
                value = fingerprint({"segment": segment_id,
                                     "target": pair["target"], "refresh": True})
                event_id = f"review-refresh-{segment_id}"
                translation_evidence.register_runtime_review_event(
                    current, _event(segment_id, event_id, value, decision="clean"),
                    [], event_id, [segment_id])
                pair.update(reviewed=True, review_status="reviewed_clean",
                            accepted_target=pair["target"],
                            target_provenance="reviewed")
                reviewed.append(segment_id)
            current["delivery_status"] = delivery.compute_delivery_status(current)
            core.save_job_state(job_id_, current)
            return current, {"reviewed_segment_ids": reviewed,
                             "failed_segment_ids": []}

        monkeypatch.setattr(core, "review_translation_segments", fake_review)
        at = _open_workspace(job_id, "review", api_key="test-key")

        assert any(button.label == "应用建议并复审" and button.disabled is False
                   for button in at.button)
        assert any(button.label == "修改译文" for button in at.button)
        assert any(button.label == "保留当前译文" for button in at.button)
        assert any(button.label == "重新翻译并复审" for button in at.button)
        next(button for button in at.button
             if button.label == "应用建议并复审").click()
        at.run()
        _settle_segment_action(at, job_id)

        assert not at.exception, at.exception
        updated = core.load_job_state(job_id)
        assert updated["pairs"][0]["target"] == "建议译文"
        old_finding = updated["findings"][0]
        assert old_finding["status"] == "stale"
        assert old_finding["status_before_stale"] == "open"
        assert old_finding.get("resolved") is not True
        assert any(action.get("decision") == "request_revision"
                   for action in updated["human_actions"])
        assert review_workbench_view(updated)["queue_items"] == []
        assert review_workbench_view(updated)["delivery"]["title"] == "翻译审校已完成"
        # Async completion may consume the one-shot toast in an intermediate rerun.
        # The persisted clean review is the completion contract.
        assert updated["pairs"][0]["review_status"] == "reviewed_clean"
        assert updated["pairs"][0]["accepted_target"] == "建议译文"
        assert any(button.label == "前往交付" for button in at.button)
    finally:
        core.OUTPUT_DIR = old_output


def test_review_actions_select_same_segment_next_then_cross_navigate(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3selection01"
        core.save_job_state(job_id, _state({0: 2, 1: 1}))
        loaded = core.load_job_state(job_id)
        current = review_workbench_view(loaded)["queue_items"]
        first_segment_items = [item for item in current if item["segment_id"] == 0]
        second_segment_item = next(item for item in current if item["segment_id"] == 1)

        at = _open_workspace(job_id, "review")
        filter_control = next(item for item in at.segmented_control
                              if item.label == "筛选审校任务")
        filter_control.set_value("待处理 3")
        at.run()
        queue = _queue(at)
        first_label = next(label for label in queue.options
                           if first_segment_items[0]["summary"] in label)
        queue.set_value(first_label)
        at.run()
        next(button for button in at.button if button.label == "确认已解决").click()
        at.run()

        assert not at.exception, at.exception
        assert at.session_state["selected_finding_id"] == \
            first_segment_items[1]["finding_id"]
        assert any("已确认问题解决" in item.value for item in at.success)

        next(button for button in at.button if button.label == "保留当前译文").click()
        at.run()
        assert at.session_state["selected_finding_id"] == second_segment_item["finding_id"]
        assert any("已保留当前译文" in item.value for item in at.success)

        next(button for button in at.button if button.label == "修改译文").click()
        at.run()
        assert at.session_state["workspace_section"] == "translation"
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 1)
        assert any("段落 #2" in item.value for item in at.markdown)

        next(button for button in at.button if button.key == "workspace_nav_review").click()
        at.run()
        assert at.session_state["workspace_section"] == "review"
        assert at.session_state["selected_finding_id"] == second_segment_item["finding_id"]
    finally:
        core.OUTPUT_DIR = old_output


def test_style_rule_requires_explicit_confirmation(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3style00001"
        core.save_job_state(job_id, _state({0: 1}, style=True))
        at = _open_workspace(job_id, "review")

        assert not core.load_job_state(job_id).get("confirmed_style_rules")
        assert any(expander.label == "保存为项目风格规则"
                   for expander in at.expander)
        rule = next(item for item in at.text_area if item.label == "规则")
        rule.set_value("采用正式、克制的书面语。")
        next(button for button in at.button if button.label == "确认保存").click()
        at.run()

        saved = core.load_job_state(job_id)["confirmed_style_rules"]
        assert [item["rule"] for item in saved] == ["采用正式、克制的书面语。"]
        assert any("项目风格规则已由你确认保存" in item.value for item in at.success)
    finally:
        core.OUTPUT_DIR = old_output


def test_delivery_risk_action_is_visible_only_for_current_blockers(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3risk000001"
        core.save_job_state(job_id, _state({0: 1}))
        at = _open_workspace(job_id, "delivery")

        assert any("接受风险并继续交付" in item.value for item in at.markdown)
        assert any(button.label == "确认风险并创建交付版本" for button in at.button)
        assert any(item.label == "我确认理解这些问题仍然存在，并决定继续交付"
                   for item in at.checkbox)

        core.save_translation_edit(job_id, 0, "人工修改后的译文")
        at.run()
        assert not at.exception, at.exception
        assert not any(button.label == "确认风险并创建交付版本" for button in at.button)
        assert any("需要重新审校" in item.value or "上次审校后发生变化" in item.value
                   for item in [*at.markdown, *at.error, *at.warning])
    finally:
        core.OUTPUT_DIR = old_output


def _configure_separate_reviewer(at, *, api_key="reviewer-key"):
    at.session_state["reviewer_mode"] = "separate"
    at.session_state["reviewer_provider_choice"] = "OpenAI"
    at.session_state["reviewer_model"] = "reviewer-model"
    at.session_state["reviewer_api_key"] = api_key
    at.session_state["reviewer_base_url"] = "https://reviewer.example/v1"
    at.run()
    assert not at.exception, at.exception


def test_separate_reviewer_manual_rereview_uses_reviewer_credentials(
        tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3reviewer01"
        core.save_job_state(job_id, _state({0: 1}))
        core.save_translation_edit(job_id, 0, "人工修改后的译文")
        calls = []

        def fake_review(job_id_, indexes, provider, api_key, model, target_lang,
                        *, style_rules="", base_url=None):
            calls.append((provider, api_key, model, base_url))
            return core.load_job_state(job_id_), {
                "reviewed_segment_ids": list(indexes), "failed_segment_ids": [],
            }

        monkeypatch.setattr(core, "review_translation_segments", fake_review)
        at = _open_workspace(job_id, "review")
        _configure_separate_reviewer(at)

        review_button = next(button for button in at.button
                             if button.label == "重新审校此段")
        assert review_button.disabled is False
        review_button.click()
        at.run()
        _settle_segment_action(at, job_id)

        assert calls == [("OpenAI", "reviewer-key", "reviewer-model",
                          "https://reviewer.example/v1")]
    finally:
        core.OUTPUT_DIR = old_output


def test_separate_reviewer_missing_key_disables_manual_rereview(
        tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3reviewer02"
        core.save_job_state(job_id, _state({0: 1}))
        core.save_translation_edit(job_id, 0, "人工修改后的译文")
        at = _open_workspace(job_id, "review", api_key="translator-key")
        _configure_separate_reviewer(at, api_key="")

        review_button = next(button for button in at.button
                             if button.label == "重新审校此段")
        assert review_button.disabled is True
        assert any("需要先完成审校模型配置" in item.value for item in at.caption)
    finally:
        core.OUTPUT_DIR = old_output


def test_separate_reviewer_suggested_target_uses_reviewer_credentials(
        tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3reviewer03"
        core.save_job_state(job_id, _state({0: 1}, suggested=True))
        calls = []

        def fake_review(job_id_, indexes, provider, api_key, model, target_lang,
                        *, style_rules="", base_url=None):
            calls.append((provider, api_key, model, base_url))
            return core.load_job_state(job_id_), {
                "reviewed_segment_ids": list(indexes), "failed_segment_ids": [],
            }

        monkeypatch.setattr(core, "review_translation_segments", fake_review)
        at = _open_workspace(job_id, "review", api_key="translator-key")
        _configure_separate_reviewer(at)

        apply_button = next(button for button in at.button
                            if button.label == "应用建议并复审")
        assert apply_button.disabled is False
        apply_button.click()
        at.run()
        _settle_segment_action(at, job_id)

        assert calls == [("OpenAI", "reviewer-key", "reviewer-model",
                          "https://reviewer.example/v1")]
    finally:
        core.OUTPUT_DIR = old_output


def test_legacy_no_review_suggestion_does_not_claim_or_run_rereview(
        tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3legacy01"
        core.save_job_state(job_id, _state(
            {0: 1}, suggested=True, review_required=False, legacy=True))

        def unexpected_review(*args, **kwargs):
            raise AssertionError("legacy no-review action invoked independent review")

        monkeypatch.setattr(core, "review_translation_segments", unexpected_review)
        at = _open_workspace(job_id, "review")

        apply_button = next(button for button in at.button
                            if button.label == "应用建议")
        assert apply_button.disabled is False
        assert not any(button.label == "应用建议并复审" for button in at.button)
        apply_button.click()
        at.run()

        updated = core.load_job_state(job_id)
        assert updated["pairs"][0]["target"] == "建议译文"
        assert updated["findings"][0]["resolved"] is True
        assert any("建议译文已应用。" in item.value for item in at.success)
    finally:
        core.OUTPUT_DIR = old_output


def test_legacy_no_review_retranslation_uses_translation_only_copy(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase3legacy02"
        core.save_job_state(job_id, _state(
            {0: 1}, review_required=False, legacy=True))
        at = _open_workspace(job_id, "review", api_key="translator-key")

        assert any(button.label == "重新翻译" for button in at.button)
        assert not any(button.label == "重新翻译并复审" for button in at.button)
    finally:
        core.OUTPUT_DIR = old_output


def test_no_review_translation_edit_and_restore_feedback_has_no_review_claims(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "phase3legacy03"
    core.save_job_state(job_id, _state(
        {0: 1}, review_required=False, legacy=True))

    at = _open_workspace(job_id, "translation")
    selected_id = assets.segment_id(job_id, 0)
    editor_key = f"translation_editor_{selected_id}"
    at.session_state[editor_key] = "修改后的译文"
    # 译文编辑器现在在中央网格的行内表单里；右栏不再有第二个编辑器和全局"保存修改"。
    _save_row_editor(at, job_id, 0)
    at.run()

    updated = core.load_job_state(job_id)
    assert updated["pairs"][0]["target"] == "修改后的译文"
    save_feedback = " ".join(str(item.value) for item in at.success)
    assert "已保存" in save_feedback and "第 1 段" in save_feedback, save_feedback
    assert not any(word in save_feedback for word in ("审校", "复审", "过期"))
    view = review_workbench_view(updated)
    assert view["readiness"]["status"] == "not_required"
    assert not any(item["kind"] in {"stale", "missing", "failed"}
                   for item in view["queue_items"])

    next(button for button in at.button if button.label == "恢复原译").click()
    at.run()

    restored = core.load_job_state(job_id)
    assert restored["pairs"][0]["target"] == "译文 1"
    assert any("已恢复原译" in item.value for item in at.success)
    assert not any(word in item.value
                   for item in at.success for word in ("审校", "复审", "过期"))
    restored_view = review_workbench_view(restored)
    assert restored_view["readiness"]["status"] == "not_required"
    assert not any(item["kind"] in {"stale", "missing", "failed"}
                   for item in restored_view["queue_items"])


def test_review_required_translation_edit_and_restore_feedback_keeps_rereview_copy(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "phase3modern03"
    core.save_job_state(job_id, _state({0: 1}, review_required=True))

    at = _open_workspace(job_id, "translation")
    selected_id = assets.segment_id(job_id, 0)
    editor_key = f"translation_editor_{selected_id}"
    at.session_state[editor_key] = "修改后的译文"
    _save_row_editor(at, job_id, 0)
    at.run()

    assert any("需要重新审校" in item.value for item in at.warning)
    next(button for button in at.button if button.label == "恢复原译").click()
    at.run()

    assert any("需要重新审校" in item.value for item in at.warning)


def test_issue_drawer_includes_persisted_review_and_document_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "mixedissuesui"
    state = _state({0: 2}, review_required=False)
    state["glossary"] = [{"id": "source-term", "source": "Source",
                           "preferred": "正确术语", "status": "locked"}]
    core.save_job_state(job_id, state)
    at = _open_workspace(job_id, "translation")
    next(button for button in at.button if button.key == f"workspace_issues_{job_id}").click().run()
    assert not at.exception, at.exception
    page = "\n".join(item.value for item in at.markdown)
    assert "全文检查 · 建议" in page
    assert "审校记录 · 必须处理" in page
    assert "第 1 段问题 1" in page and "第 1 段问题 2" in page
    assert '<b>2</b> 必须处理' in page
    assert '<b>1</b> 建议检查' in page
    anchor = next(button for button in at.button
                  if str(button.key).startswith(f"issue_anchor_{job_id}_finding:"))
    anchor.click().run()
    assert not at.exception, at.exception
    assert any('tp-inspector-segment-head' in item.value for item in at.markdown)


def test_cancelled_optional_work_keeps_prepare_delivery_action(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "optionalcancelui"
    state = _state({0: 0}, review_required=False)
    core.save_job_state(job_id, state)
    core.update_runtime_state(job_id, status="cancelled", stage_id="annotation")
    at = _open_workspace(job_id, "translation")
    button = next(button for button in at.button if button.key == f"workspace_topbar_cta_{job_id}")
    assert button.label == "准备交付"
    button.click().run()
    assert not at.exception, at.exception
    assert at.session_state["workspace_section"] == "delivery"
    assert not any(button.key == f"workspace_topbar_cta_{job_id}" for button in at.button)
