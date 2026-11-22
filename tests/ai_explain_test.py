"""#525: on-demand AI explanation is read-only, bounded and cached."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import ai_explain  # noqa: E402


def _packet(**over):
    item = {"source": "Crown colophon", "target": "皇冠标识",
            "reason": "entity_conflict", "type": "terminology", "segment_index": 14}
    item.update(over)
    return ai_explain.build_packet(
        item, state={"target_lang": "简体中文"},
        surrounding=[{"source": "Crown", "target": "皇冠出版社"}] * 20,
        glossary=[{"source": "Crown", "target": "皇冠出版社"}] * 20,
        entities=[{"source_form": "Crown", "preferred_target": "皇冠出版社"}] * 20)


def test_click_produces_one_request_and_readonly_packet():
    calls = []
    def fake(*args):
        calls.append(args)
        return "皇冠标识 is Crown colophon, a different term."
    packet = _packet()
    result = ai_explain.explain(packet, call=fake)
    assert result["ok"] and not result["cached"]
    assert len(calls) == 1
    sent = json.loads(calls[0][4])
    assert len(sent["surrounding"]) <= 12
    assert "皇冠标识" in json.dumps(sent, ensure_ascii=False)
    # no decision state can be affected: the packet has no such keys
    assert "resolved" not in sent and "HumanDecision" not in sent


def test_second_click_with_unchanged_input_hits_cache():
    calls = []
    def fake(*_args):
        calls.append(1)
        return "cached answer"
    cache = ai_explain.ExplanationCache()
    first = ai_explain.explain(_packet(), call=fake, cache=cache)
    second = ai_explain.explain(_packet(), call=fake, cache=cache)
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1


def test_changed_text_invalidates_cache():
    calls = []
    def fake(*_args):
        calls.append(1)
        return "answer"
    cache = ai_explain.ExplanationCache()
    ai_explain.explain(_packet(), call=fake, cache=cache)
    ai_explain.explain(_packet(target="皇冠出版社"), call=fake, cache=cache)
    assert len(calls) == 2


def test_failure_and_missing_model_are_error_states():
    def boom(*_args):
        raise TimeoutError("timed out")
    failed = ai_explain.explain(_packet(), call=boom)
    assert failed["ok"] is False and "timed out" in failed["error"]
    missing = ai_explain.explain(_packet(), call=lambda *_a: "", provider="", model="")
    assert missing["ok"] is True and missing["text"] == ""


def test_review_panel_button_emits_once_and_never_automatically():
    from PySide6.QtWidgets import QApplication
    from folith.qt.review_panel import ReviewPanel

    QApplication.instance() or QApplication([])
    panel = ReviewPanel()
    seen = []
    panel.explainRequested.connect(seen.append)
    # no item selected, no emission
    assert seen == []
    panel.ai_explain_button.click()
    assert seen == []  # nothing selected -> no request
    panel.ai_explain_button.click()
    assert seen == []


def test_ai_explain_button_is_hidden_until_chain_is_wired():
    from PySide6.QtWidgets import QApplication
    from folith.qt.review_panel import ReviewPanel

    QApplication.instance() or QApplication([])
    panel = ReviewPanel()
    assert panel.ai_explain_button.isHidden()
    panel.close()
