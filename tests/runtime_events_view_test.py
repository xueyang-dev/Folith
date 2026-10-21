"""Status and wording of the run record (runtime_events_view)."""
from __future__ import annotations

from folith import runtime_events_view as view


def _row(message, name="x"):
    rows = view.activity_rows([{"event": name, "message": message,
                                "timestamp": "2026-10-10T08:00:00+00:00"}])
    assert len(rows) == 1
    return rows[0]


def test_failed_profile_is_a_warning_and_is_not_rewritten_as_done():
    row = _row("⚠️ 文档画像失败，已跳过（可在 UI 中人工填写）。")
    assert row["status"] == "warning"
    assert row["label"].startswith("文档画像失败")
    assert "完成" not in row["label"]


def test_successful_profile_is_still_summarised_as_done():
    row = _row("✅ 文档画像完成：领域「法律」· 文本类型「合同」")
    assert (row["status"], row["label"]) == ("completed", "完成文档画像")


def test_hard_failure_stays_failed_with_its_own_wording():
    row = _row("双语翻译失败：模型连接超时")
    assert row["status"] == "failed"
    assert "完成" not in row["label"]


def test_in_progress_messages_are_running_not_done():
    for message in ("🌍 正在翻译第 1-8 段（并发批次 1，共 80 段）...",
                    "🧭 正在建立全文概要与当前单元摘要...",
                    "【阶段1.3】全文语义理解（语义单元摘要 + 全文概要）..."):
        row = _row(message)
        assert row["status"] == "running", message
        assert "完成" not in row["label"], message


def test_degraded_and_skipped_steps_are_warnings():
    assert _row("⚠️ 批次 3 翻译返回格式异常，降级为逐段翻译...")["status"] == "warning"
    assert _row("⚠️ 全文理解降级：12 个语义单元")["status"] == "warning"
    assert _row("自动标注部分完成：2 批失败")["status"] == "warning"


def test_emoji_in_old_logs_is_removed_everywhere():
    for message in ("✅ 成功提取 5 个候选术语", "🤖 正在提取术语...", "⚠️ 文档画像失败，已跳过",
                    "🔧 批次 2 发现 1 个确定性问题，正在自动修复..."):
        row = _row(message)
        assert not view._EMOJI.search(row["label"]), row
    notice = _row("⚠️ 保存失败", name="client_error")
    assert notice["status"] == "failed" and notice["label"] == "保存失败"


def test_internal_summary_status_is_not_shown():
    row = _row("✅ 全文理解完成：10 个语义单元 · 概要状态 deterministic_fallback")
    assert "概要状态" not in row["label"] and "deterministic_fallback" not in row["label"]
    assert row["status"] == "completed"
    assert row["label"] == "全文理解完成：10 个语义单元"


def test_arrows_and_text_symbols_are_kept():
    assert view.strip_emoji("EN → 中文，⌘S 保存") == "EN → 中文，⌘S 保存"


def test_event_names_decide_failure_and_running():
    assert _row("", name="job_failed")["status"] == "failed"
    assert _row("", name="llm_request_started")["status"] == "running"
    assert _row("", name="job_completed")["status"] == "completed"
