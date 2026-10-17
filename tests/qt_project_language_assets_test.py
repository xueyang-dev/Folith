"""Qt 适配层：候选只提升单条术语、「未分类」不提升。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.qt_delivery_supplement_test import _finished, env  # noqa: F401,E402  (fixture)


def _candidate_state(core, job_id, project_id):
    state = core.load_job_state(job_id)
    state["project_id"] = project_id
    core.save_job_state(job_id, state)


def test_accepting_in_unsorted_stays_in_the_task(env, monkeypatch):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    monkeypatch.setattr(core, "review_knowledge_candidate",
                        lambda *a, **k: (core.load_job_state(job_id), True, "ok"))
    message = adapter.decide_candidate(
        {"job_id": job_id, "candidate_id": "c", "source": "hub"}, "project_term")
    assert "未分类" in message
    assert not (core.load_project(core.system_project_id()) or {}).get("glossary")


def test_accepting_in_a_project_promotes_only_that_term(env, monkeypatch):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    project = adapter.create_project("客户 C")
    _candidate_state(core, job_id, project["project_id"])
    state = core.load_job_state(job_id)
    state["glossary"] = [
        {"source": "hub", "target": "集线器", "preferred": "集线器", "status": "locked"},
        {"source": "node", "target": "节点", "preferred": "节点", "status": "locked"}]
    core.save_job_state(job_id, state)
    monkeypatch.setattr(core, "review_knowledge_candidate",
                        lambda *a, **k: (core.load_job_state(job_id), True, "ok"))
    adapter.decide_candidate(
        {"job_id": job_id, "candidate_id": "c", "source": "hub"}, "project_term")
    assert [e["source"] for e in core.load_project(project["project_id"])["glossary"]] == ["hub"]
