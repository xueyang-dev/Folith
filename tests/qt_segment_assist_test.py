"""工作台右栏的本段参考：需要注意、术语、前后文、询问 Agent、从选中文字加入术语库。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.qt_inline_editor_test import _pump, bench  # noqa: F401,E402  (fixture)


def _state():
    return {
        "pairs": [
            {"source": "The Midnight Library opens.", "target": "午夜图书馆开门了。"},
            {"source": "Skipped.", "target": "略。", "excluded": True},
            {"source": "Nora reads in the Midnight Library.",
             "target": "诺拉在子夜图书馆读书。", "glossary_entry_ids": ["g2"]},
            {"source": "The start of art.", "target": "艺术的开端。"},
        ],
        "glossary": [
            {"id": "g1", "source": "Midnight Library", "preferred": "午夜图书馆",
             "status": "locked", "forbidden": ["子夜图书馆"]},
            {"id": "g2", "source": "Nora", "preferred": "诺拉", "status": "locked"},
            {"id": "g3", "source": "art", "preferred": "艺术", "status": "candidate"},
            {"id": "g4", "source": "Library", "preferred": "藏书馆", "status": "rejected"},
        ],
    }


def test_terms_match_the_source_live_and_flag_forbidden_renderings():
    from folith.qt import adapter

    terms = adapter.segment_terms(_state(), 2)
    # 附在段上的排前面；翻译之后新加的术语按原文命中也列出；被否决的不列。
    assert [term["source"] for term in terms] == ["Nora", "Midnight Library"]
    library = terms[1]
    assert library["target"] == "午夜图书馆"
    assert library["forbidden_hits"] == ["子夜图书馆"]
    assert terms[0]["forbidden_hits"] == []
    # 拉丁字母整词匹配："art" 不算进 "start"，但独立的 "art" 算。
    assert [term["source"] for term in adapter.segment_terms(_state(), 3)] == ["art"]
    assert adapter.segment_terms(_state(), 99) == []


def test_context_skips_excluded_neighbours():
    from folith.qt import adapter

    context = adapter.segment_context(_state(), 2)
    assert context["previous"]["index"] == 0
    assert context["next"]["index"] == 3
    assert adapter.segment_context(_state(), 0)["previous"] is None


def test_decision_traces_are_local_and_bounded():
    from folith.qt import adapter

    decisions = adapter.segment_decisions(_state(), 2)
    assert isinstance(decisions, list) and len(decisions) <= 3
    assert adapter.segment_decisions(_state(), 99) == []


def test_ask_agent_answers_without_rewriting(monkeypatch, tmp_path):
    from folith.qt import adapter

    seen = {}

    def fake_llm(provider, api_key, model, system, prompt, **kwargs):
        seen.update(system=system, prompt=prompt)
        return "因为这是专名。"

    monkeypatch.setattr(adapter, "load_state", lambda _job: _state())
    monkeypatch.setattr(adapter.core, "call_llm", fake_llm)
    config = adapter.EngineConfig(provider="DeepSeek", model="m", api_key="k")
    answer = adapter.ask_about_segment("job", 2, "为什么这样译？", config)
    assert answer == "因为这是专名。"
    assert "不要生成改写译文" in seen["system"]
    assert "为什么这样译？" in seen["prompt"]
    assert "前后文" in seen["prompt"]
    with pytest.raises(adapter.AdapterError):
        adapter.ask_about_segment("job", 2, "  ", config)


def test_panel_inserts_terms_and_offers_term_add_from_selection(bench):  # noqa: F811
    view, app = bench
    view.select_segment(0)
    _pump(app)
    view._insert_into_target("【术语】")
    assert "【术语】" in view.target_edit.toPlainText()

    requests = []
    view.termAddRequested.connect(lambda source, target: requests.append((source, target)))
    source_view = view.inline_editor.source_view
    assert source_view.isVisible() and source_view.toPlainText().startswith("First")
    cursor = source_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfWord, QTextCursor.MoveMode.KeepAnchor)
    source_view.setTextCursor(cursor)
    view.term_add_button.click()
    assert requests == [("First", "")]


def test_attention_items_route_to_the_same_decisions_as_the_review_page(bench):  # noqa: F811
    view, app = bench
    resolved, applied = [], []
    view.findingResolveRequested.connect(lambda ids, action, note: resolved.append((ids, action)))
    view.suggestionApplyRequested.connect(lambda item, note: applied.append(item))
    item = {"kind": "finding", "finding_id": "f1", "segment_index": 0,
            "severity": "actionable", "summary": "用了禁用译名", "suggested_target": "新译文"}
    view.attention_list.set_items([item], [], 0)
    keep, accept = [button for button in view.attention_list.findChildren(
        type(view.submit_button)) if button.text() in ("保留译文", "采用建议")]
    keep.click()
    accept.click()
    from folith.qt import adapter

    assert resolved == [(["f1"], adapter.RESOLVE_PRESERVED)]
    assert applied and applied[0]["suggested_target"] == "新译文"
    assert "用了禁用译名" in view.attention_list.texts()


def test_context_row_jumps_and_ask_box_resets_per_segment(bench):  # noqa: F811
    view, app = bench
    view.select_segment(1)
    _pump(app)
    asked = []
    view.askAgentRequested.connect(lambda index, question: asked.append((index, question)))
    view.ask_box.input.setText("这里为什么这样译？")
    view.ask_box.button.click()
    assert asked == [(1, "这里为什么这样译？")]
    view.set_agent_answer(1, "回答")
    assert view.ask_box.answer.text() == "回答"
    view.context_view.rows["next"].clicked.emit()
    _pump(app)
    assert view.current_index() == 2
    assert view.ask_box.answer.text() == ""
    QApplication.processEvents()


def test_suggestion_is_generated_but_not_written(monkeypatch, bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    seen = {}

    def fake_batch(segments, prev, nxt, glossary_text, rules, target_lang, *args, **kwargs):
        seen["rules"] = rules
        return ["更口语的译文"]

    monkeypatch.setattr(adapter.core, "translate_batch", fake_batch)
    config = adapter.EngineConfig(provider="DeepSeek", model="m", api_key="k",
                                  target_lang="简体中文")
    before = adapter.load_state(view.job_id)["pairs"][1]["target"]
    candidate = adapter.suggest_translation(view.job_id, 1, "更口语一点", config)
    assert candidate["candidate_target"] == "更口语的译文"
    assert candidate["origin"] == "instructed_retranslate"
    assert candidate["status"] == "pending"
    assert "更口语一点" in seen["rules"]
    assert adapter.load_state(view.job_id)["pairs"][1]["target"] == before, \
        "生成候选不写正文"
    with pytest.raises(adapter.AdapterError):
        adapter.suggest_translation(view.job_id, 1, " ", config)

    view.select_segment(1)
    _pump(app)
    requested = []
    view.suggestRequested.connect(lambda index, text: requested.append((index, text)))
    view.assist_input.setText("更口语一点")
    view.assist_retranslate.click()
    assert requested == [(1, "更口语一点")]
    view.show_candidate(1)
    assert view.candidate_box.isVisibleTo(view)
    assert view.candidate_box.candidate_text() == "更口语的译文"
    assert "更口语的" in view.candidate_box.proposal.text(), "差异预览里能看到新增片段"
    adopted = []
    view.candidateAdoptRequested.connect(adopted.append)
    view.candidate_box.adopt_button.click()
    assert adopted == [candidate["candidate_id"]]
    assert adapter.load_state(view.job_id)["pairs"][1]["target"] == before, \
        "点采用只发出请求；写入由窗口经核心事务完成"


def test_deep_analysis_is_cached_and_verbatim_only(monkeypatch, bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    state = adapter.load_state(view.job_id)
    source = state["pairs"][1]["source"]
    target = state["pairs"][1]["target"]
    calls = []

    def fake_llm(provider, api_key, model, system, prompt, **kwargs):
        calls.append(prompt)
        return ('{"decisions":[{"decision_type":"semantic_omission","source_span":"%s",'
                '"target_span":"%s","reason":"省略了修饰语","confidence":0.7},'
                '{"decision_type":"omission","source_span":"不在原文里","target_span":null,'
                '"reason":"捏造的证据"}]}') % (source.split()[1], target)

    monkeypatch.setattr(adapter.core, "call_llm", fake_llm)
    config = adapter.EngineConfig(provider="DeepSeek", model="m", api_key="k")
    decisions = adapter.analyze_segment_decisions(view.job_id, 1, config)
    reasons = [item.get("reason") for item in decisions]
    assert "省略了修饰语" in reasons
    assert "捏造的证据" not in reasons            # 不是逐字摘录的证据被丢弃
    assert adapter.analyze_segment_decisions(view.job_id, 1, config) == decisions
    assert len(calls) == 1                        # 第二次直接用缓存

    view.apply(view.job_id, adapter.load_state(view.job_id),
               adapter.segment_rows(view.job_id), {}, [])
    view.select_segment(1)
    _pump(app)
    assert "省略了修饰语" in " ".join(view.attention_list.texts())
    decision = next(item for item in decisions if item.get("reason") == "省略了修饰语")
    view._locate_decision(decision)
    assert view.inline_editor.source_view.extraSelections()
    assert view.target_edit.extraSelections()
    assert view.target_edit.toPlainText() == target      # 只高亮，不改文字


def test_notes_live_outside_the_task_state(bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    state_before = adapter.load_state(view.job_id)
    uid = view._all_rows[1].segment_uid
    adapter.save_segment_note(view.job_id, uid, "客户要求保留英文书名")
    assert adapter.segment_notes(view.job_id)[uid]["text"] == "客户要求保留英文书名"
    assert adapter.load_state(view.job_id) == state_before     # 批注不进任务状态
    view.apply(view.job_id, state_before, adapter.segment_rows(view.job_id), {}, [])
    view.select_segment(1)
    _pump(app)
    assert view.note_box.edit.toPlainText() == "客户要求保留英文书名"
    assert view.segment_list.item(view._rows.index(view._all_rows[1]), 0).text().endswith("✎")
    view.filter_actions["notes"].setChecked(True)
    assert view.filtered_indexes() == [1]
    view.filter_actions["notes"].setChecked(False)
    view.note_box.edit.setPlainText("")
    view.note_box.save_button.click()
    assert uid not in adapter.segment_notes(view.job_id)


def test_repetitions_and_find_replace(env_job):
    from folith.qt import adapter

    job_id = env_job
    assert adapter.repetitions(job_id, 0) == [2]
    assert adapter.propagate_translation(job_id, 0, [2]) == 1
    pairs = adapter.load_state(job_id)["pairs"]
    assert pairs[2]["target"] == pairs[0]["target"] and pairs[2]["human_edited"]
    assert adapter.repetitions(job_id, 0) == []

    matches = adapter.find_in_targets(adapter.load_state(job_id), "图书馆", "藏书楼")
    assert [item["index"] for item in matches] == [0, 1, 2]
    assert matches[1]["after"] == "藏书楼关门了。"
    result = adapter.replace_in_targets(job_id, "图书馆", "藏书楼", indexes=[1])
    assert result["staged"] == 1 and result["changed"] == 1
    assert adapter.load_state(job_id)["pairs"][1]["target"] == "藏书楼关门了。"
    assert adapter.find_in_targets(adapter.load_state(job_id), "") == []


@pytest.fixture()
def env_job(tmp_path, monkeypatch):
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(tmp_path))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path, raising=False)
    from folith.qt import adapter
    from tests.qt_inline_editor_test import _docx

    sources = ["The library opens.", "The library closes.", "The  library opens."]
    path = tmp_path / "rep.docx"
    path.write_bytes(_docx(sources))
    job_id = adapter.import_documents([path], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = sources
    state["pairs"] = [{"source": sources[0], "target": "图书馆开门了。", "segment_uid": "s0"},
                      {"source": sources[1], "target": "图书馆关门了。", "segment_uid": "s1"},
                      {"source": sources[2], "target": "图书馆开放了。", "segment_uid": "s2"}]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)
    return job_id
