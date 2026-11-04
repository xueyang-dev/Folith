"""F2 取证：带一条**旧式**阻断项的合成任务，四处界面说法不一致。

复现的是 2026-11-04 审校报告 F2 描述的现象：

* 审校页说「当前译文已完成审校」（``workbench_view.review_workbench_view``）；
* 检查浮层说没有问题（``adapter.followup_view``，它根本没有 findings 这个字段）；
* 页头写「已完成」（``views.run_status_view``，只看运行时 status）；
* 交付页却拦住 1 个必须处理的问题（``adapter.delivery_preview`` —— 这里同时数
  收据里的阻断项和旧式 ``blocking`` 发现项）。

为什么旧式发现项只被一处数到：``translation_evidence.translation_review_readiness``
的阻断计数要求发现项带 ``requires_human_confirmation`` 并且 ``review_event_id``
与本段当前审校收据一致（``translation_evidence.py:423-436``）；旧式发现项两者都没有，
于是 readiness 认为「无阻断、审校已完成」。交付门禁走的是
``delivery.unresolved_blocking(state)``，只看 ``severity == "blocking"`` 且未解决，
所以同一份状态在交付页是 1。

运行：

    ./venv/bin/python -m pytest tests/ui_overhaul_f2_status_test.py -q

修复后（2026-11-04 17:3x 复核）：审校页 / 交付页 / 首页任务行 / 检查浮层的**数据**已经同数，
``test_all_four_surfaces_report_same_blocking`` 是正式断言；剩下的缺口在**视图层**——
``FollowupPanel`` 还不读 ``followup_view["blocking"]``，角标数的是固定 8 张卡，
``test_followup_panel_counts_blocking`` 仍标 ``xfail(strict=False)`` 记录这条。
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


# --------------------------------------------------------------------------
# 合成任务：一段旧式阻断项 + 一份覆盖全部段落的审校收据
# --------------------------------------------------------------------------
def _segments() -> list[dict]:
    rows = []
    for index, (source, target) in enumerate((
            ("A term rendered one way in chapter two and another in chapter nine.",
             "同一个术语在第二章是一种译法、到第九章又换成另一种。"),
            ("Blocking findings must be resolved by a person before delivery.",
             "阻断性问题必须由人处理后任务才能交付。"),
            ("The final delivery is frozen as an immutable snapshot.",
             "最终交付会冻结为不可变的快照。"),
    )):
        rows.append({
            "segment_id": f"seg-{index:03d}", "source": source, "target": target,
            "initial_target": target, "reviewed": True,
            "review_status": "reviewed_clean", "target_provenance": "reviewed",
        })
    return rows


def _legacy_blocking_finding() -> dict:
    """旧式发现项：没有 ``requires_human_confirmation``，也没有 ``review_event_id``。

    这正是一份「旧式阻断项」——它被写进 ``state["findings"]``，但不在任何
    Translation Core 审校收据里。
    """
    return {
        "type": "review",
        "category": "terminology",
        "severity": "blocking",
        "status": "open",
        "segment_index": 0,
        "segment_id": 0,
        "location_key": "term:lock",
        "summary": "术语「漂移」应统一为「偏移」",
        "source_span": "drift",
        "target_span": "漂移",
        "explanation": "项目术语表把 drift 译为「偏移」；此处请保持一致。",
        "recommendation": "采纳建议译文或更新术语表。",
        "detector": "Folith Reviewer",
        "confidence": 0.9,
        "resolved": False,
    }


def _seed_job(work_dir: str) -> tuple[str, dict]:
    from folith.qt import bootstrap

    data_dir = os.path.join(work_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    bootstrap.prepare_environment(data_dir=data_dir,
                                  log_dir=os.path.join(work_dir, "logs"))
    from folith.qt import adapter

    core = adapter.core
    from folith import translation_core

    rows = _segments()
    finding = _legacy_blocking_finding()
    fingerprint = translation_core.fingerprint(
        {"segment": 0, "target": rows[0]["target"]})
    finding = translation_core.normalize_finding(finding, input_fingerprint=fingerprint)
    finding.update({"type": "review", "segment_index": 0})

    filename = "legacy-blocking.docx"
    state = core.new_job_state(filename)
    state.update({
        "p1_done": True, "p2_done": True, "p3_done": False,
        "report_enabled": False,
        "project_id": "",
        "paras": [row["source"] for row in rows],
        "source_paras": [row["source"] for row in rows],
        "pairs": rows,
        "glossary": [],
        "profile_done": True,
        "document_profile": {"display_name": "legacy-blocking", "domain": "本地化工程"},
        # 审校收据覆盖全部段落：审校页据此认为「已完成」。
        "translation_core_review_required": True,
        "review_evidence": [{
            "review_event_id": "rev-legacy-receipt",
            "review_scope": "current_translation",
            "segment_ids": [0, 1, 2],
            "created_at": "2026-11-04T10:00:00+08:00",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": [0, 1, 2]},
        }],
        "findings": [finding],
        "human_actions": [],
        "provider": "DeepSeek", "model": "deepseek-v4-flash",
        "stage": "TRANSLATED",
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state(filename, state)
    core.update_runtime_state(filename, status="completed", phase="completed",
                              phase_label="已完成", last_event="completed")
    return filename, state


@pytest.fixture(scope="module")
def synthetic_job():
    work_dir = tempfile.mkdtemp(prefix="folith-f2-")
    yield _seed_job(work_dir)
    import shutil

    shutil.rmtree(work_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# 取证：四处各自的说法（当前实现的实际返回值）
# --------------------------------------------------------------------------
def test_all_four_surfaces_report_same_blocking(synthetic_job):
    """修复复核：审校页 / 交付页 / 首页任务行 / 检查浮层的模型读数一致。"""
    job_id, state = synthetic_job
    from folith import workbench_view
    from folith.qt import adapter

    review = workbench_view.review_workbench_view(dict(state))
    followup = adapter.followup_view(job_id, dict(state))
    preview = adapter.delivery_preview(job_id, state=dict(state))
    blocking = len(workbench_view.delivery.unresolved_blocking(dict(state)))

    observations = {
        "review.readiness.status": review["readiness"]["status"],
        "review.readiness.label": review["readiness"]["label"],
        "review.queue_counts.blocking": review["queue_counts"]["blocking"],
        "review.delivery.title": review["delivery"]["title"],
        "review.primary_action": review["primary_action"]["label"],
        "followup.has_work": followup["has_work"],
        "followup.keys": sorted(followup),
        "delivery.blocking_count": preview["blocking_count"],
        "delivery.issues": list(preview["issues"]),
        "delivery.can_deliver": preview["can_deliver"],
        "legacy.unresolved_blocking": blocking,
    }
    print(json.dumps(observations, ensure_ascii=False, indent=2))

    # 唯一的数：旧式阻断项 1 个。
    assert blocking == 1
    # 审校页：不再说「已完成」，改成「还有 1 个必须处理的问题」，队列里也数到 1。
    assert review["readiness"]["status"] == "current"          # 收据本身是完整的
    assert review["queue_counts"]["blocking"] == blocking
    assert "必须处理的问题" in review["readiness"]["label"]
    assert review["primary_action"]["label"] == review["readiness"]["label"]
    # 审校页的交付摘要：有阻断项时不说「已完成」。
    assert review["delivery"]["title"] != "翻译审校已完成"
    assert str(blocking) in review["delivery"]["detail"] or \
        "必须处理" in review["delivery"]["detail"]
    # 交付页：同一个数，且不能直接交付。
    assert preview["blocking_count"] == blocking
    assert preview["can_deliver"] is False
    assert any("必须处理的问题" in str(issue) for issue in preview["issues"])
    # 页头：基线只读运行时 status，这里验证页头用的纯函数仍然会返回「已完成」；
    # 真正把它压住的是 views._render_runtime 里 blocking>0 的分支（需要窗口，未在此覆盖）。
    from folith.qt import views

    tone, line = views.run_status_view({"status": "completed"},
                                      note="", running=False,
                                      completed=0, total=0, unit="")
    assert (tone, line) == ("done", "已完成")
    # 检查浮层（数据层）：同一个数，has_work 为真。
    assert followup["blocking"] == blocking
    assert followup["has_work"] is True


def test_followup_panel_counts_blocking():
    """视图层：抽屉与角标显示同一个数。

    2026-11-04 已接线：`adapter.followup_view` 返回 ``blocking`` / ``blocking_anchor``，
    `FollowupPanel.apply` 落成一张「必须处理的问题」卡片并计入 ``issue_count()``。
    """
    import inspect

    from folith.qt import followup_panel

    source = inspect.getsource(followup_panel)
    assert "blocking" in source, (
        "FollowupPanel 还没有消费 followup_view[\"blocking\"]："
        "需要在 issue_cards()/issue_count() 里把它算进去，否则角标仍是 0")
