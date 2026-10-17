"""运行透明度：用量、复用、全文理解、失败批次（adapter.run_facts 与两处界面）。

这些都是核心在运行中已经记下、界面此前从不显示的事实。钉住三件事：

* 没有数据就不显示（不编造零值）；
* token 只要有一次是按字符估算，整体就标为估算（界面必须说"不是账单"）；
* 已被当前译文补上的失败批次不再列出；「检查与修复」能定位到失败段。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, style  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


STATE = {
    "llm_usage": {
        "calls": 12, "failures": 1, "latency_seconds": 90.0, "total_tokens": 34567,
        "prompt_tokens": 30000, "completion_tokens": 4567, "exact_token_calls": 10,
        "by_role": {
            "review": {"calls": 2, "failures": 0, "total_tokens": 4567},
            "translation": {"calls": 10, "failures": 1, "total_tokens": 30000},
        },
        "by_provider_model": {"OpenCode Go::deepseek-v4": {"calls": 12, "total_tokens": 34567}},
    },
    "tm_used_count": 3, "tm_recovered_count": 0,
    "translation_cache": {"hits": 5, "misses": 7},
    "document_synopsis": {"summary": "一本讲城市热岛的书。", "document_arc": "从现象到对策",
                          "themes": ["健康"], "status": "model"},
    "section_digests": [
        {"label": "第一章", "start_segment": 0, "end_segment": 9, "summary": "现象",
         "key_terms": ["热岛"], "status": "model"},
        {"label": "第二章", "start_segment": 10, "end_segment": 19, "summary": "首句",
         "status": "deterministic_fallback"},
    ],
    "understanding_status": "degraded",
    "understanding_warnings": ["1 个单元降级"],
    "translation_failures": [
        {"batch": 2, "offset": 20, "segment_count": 5, "reason": "JSON 解析失败", "recovered": False},
        {"batch": 0, "offset": 0, "segment_count": 5, "reason": "超时", "recovered": True},
    ],
}


def test_run_facts_reads_what_the_core_recorded():
    facts = adapter.run_facts(STATE)
    usage = facts["usage"]
    assert usage["available"] and usage["calls"] == 12 and usage["total_tokens"] == 34567
    assert usage["estimated"] is True, "12 次里只有 10 次有精确 token，整体必须标为估算"
    assert [role["role"] for role in usage["roles"]] == ["translation", "review"]
    assert facts["reuse"] == {"tm_used": 3, "tm_recovered": 0, "cache_hits": 5,
                              "cache_misses": 7, "available": True}
    understanding = facts["understanding"]
    assert understanding["available"] and understanding["summary"].startswith("一本")
    assert [item["fallback"] for item in understanding["sections"]] == [False, True]
    assert facts["failures"] == [{"batch": 3, "start": 20, "end": 24, "count": 5,
                                  "reason": "JSON 解析失败"}]
    assert facts["failures_recovered"] == 1


def test_run_facts_shows_nothing_for_an_untouched_task():
    facts = adapter.run_facts({})
    assert not facts["usage"]["available"]
    assert not facts["reuse"]["available"]
    assert not facts["understanding"]["available"]
    assert facts["failures"] == []


def test_exact_tokens_are_not_called_estimates():
    state = {"llm_usage": {"calls": 2, "exact_token_calls": 2, "total_tokens": 10}}
    assert adapter.run_facts(state)["usage"]["estimated"] is False


def test_task_details_shows_understanding_and_usage(app):
    from folith.qt.task_details_panel import TaskDetailsPanel

    panel = TaskDetailsPanel()
    panel.apply_run_facts(adapter.run_facts(STATE))
    assert not panel.understanding_card.isHidden()
    # 任务详情只放全文摘要（2026-10-17 试用反馈：太多太杂）；各单元摘要不再铺开。
    text = panel.understanding_view.text()
    assert "一本讲城市热岛的书" in text and "第一章" not in text
    assert not panel.usage_card.isHidden()
    assert "12" in panel.usage_summary.text() and "34,567" in panel.usage_summary.text()
    assert "不是账单" in panel.usage_summary.toolTip()
    assert "翻译记忆直接复用 3 段" in panel.reuse_label.text()
    panel.apply_run_facts(adapter.run_facts({}))
    assert panel.understanding_card.isHidden() and panel.usage_card.isHidden()
    panel.deleteLater()


def test_followup_lists_failed_batches_and_locates_them(app):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    located: list[int] = []
    panel.locateRequested.connect(located.append)
    panel.apply({"failures": adapter.run_facts(STATE)["failures"]})
    assert not panel.failures_card.isHidden()
    assert panel.failures_list.count() == 1
    assert "第 21–25 段" in panel.failures_list.item(0).text()
    assert panel.empty.isHidden(), "有失败批次时不能显示「没有待处理的事项」"
    panel.failures_list.setCurrentRow(0)
    panel.failures_locate.click()
    assert located == [20]
    panel.apply({})
    assert panel.failures_card.isHidden()
    panel.deleteLater()
