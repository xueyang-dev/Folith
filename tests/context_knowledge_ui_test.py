"""Context visibility and human knowledge-promotion regressions."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import knowledge, models


def _candidate(source="continuity", target="连续性", segment=0):
    return {
        "source": source,
        "observed_target": target,
        "first_observed_segment": segment,
        "occurrences": [segment],
        "observed_segments": [segment],
        "status": "emergent_candidate",
        "origin": "translation_observation",
        "kind": "term",
        "confidence": 0.5,
    }


def test_context_artifacts_load_and_missing_artifacts_are_safe():
    tmp = Path(tempfile.mkdtemp(prefix="context-artifacts-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "contextartifact0001"
        state = core.new_job_state("context.docx")
        state.update(
            semantic_units=[{
                "unit_id": "unit-0001", "label": "导论",
                "start_segment": 0, "end_segment": 1,
            }],
            section_digests=[{
                "unit_id": "unit-0001", "start_segment": 0,
                "end_segment": 1, "summary": "导论摘要",
            }],
            document_synopsis={"summary": "全文概要", "status": "model"},
        )
        core.save_job_state(job_id, state)
        artifacts = core.load_context_artifacts(job_id)
        assert artifacts["semantic_units"][0]["label"] == "导论"
        assert artifacts["section_digests"][0]["summary"] == "导论摘要"
        assert artifacts["document_synopsis"]["summary"] == "全文概要"

        missing = core.load_context_artifacts("missing-context-job")
        assert missing == {
            "semantic_units": [], "section_digests": [],
            "document_synopsis": {}, "warnings": [],
        }
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_knowledge_decisions_preserve_scope_conflicts_and_invalidation():
    tmp = Path(tempfile.mkdtemp(prefix="knowledge-decisions-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        promoted_id = "knowledgepromote0001"
        state = core.new_job_state("promote.docx")
        state.update(
            p1_done=True, p2_done=True, paras=["The continuity matters."],
            pairs=[{
                "source": "The continuity matters.", "target": "连续性很重要。",
                "reviewed": True, "from_tm": False,
            }],
            glossary=[], delivery_status="draft",
            knowledge_candidates=[_candidate()],
        )
        core.save_job_state(promoted_id, state)
        candidate_id = knowledge.candidate_id(state["knowledge_candidates"][0])
        promoted, ok, message = core.review_knowledge_candidate(
            promoted_id, candidate_id, "project_term")
        assert ok, message
        assert promoted["glossary_frozen"]["version"] == 1
        assert promoted["glossary"][0]["status"] == "locked"
        assert promoted["knowledge_candidates"][0]["decision"] == "project_term"
        assert promoted["knowledge_candidates"][0]["promotion_entry_id"]
        assert promoted["pairs"][0]["stale_due_to_glossary"] is True
        assert any(event["decision"] == "project_term"
                   for event in promoted["knowledge_events"])
        assert any(action["action"] == "knowledge_project_term"
                   for action in promoted["human_actions"])
        promotion = next(action for action in promoted["human_actions"]
                         if action["action"] == "knowledge_project_term")
        assert promotion["actor_type"] == "human"
        assert promotion["previous_status"] == "emergent_candidate"
        assert promotion["new_status"] == "promoted_project_term"
        assert promoted["glossary_frozen"]["glossary_hash"] == \
            models.glossary_hash(promoted["glossary"])
        repeated, repeated_ok, _ = core.review_knowledge_candidate(
            promoted_id, candidate_id, "task_only")
        assert not repeated_ok and repeated["knowledge_candidates"][0]["decision"] == "project_term"

        conflict_id = "knowledgeconflict0001"
        conflict = core.new_job_state("conflict.docx")
        locked = {
            "source": "continuity", "target": "连续性", "preferred": "连续性",
            "behavior": "translate", "status": "locked", "scope": "document",
        }
        conflict.update(
            p1_done=True, glossary=[locked], knowledge_candidates=[_candidate(target="连贯性")])
        core.save_job_state(conflict_id, conflict)
        core.freeze_glossary(conflict_id, entries=conflict["glossary"])
        conflict = core.load_job_state(conflict_id)
        conflict_candidate_id = knowledge.candidate_id(conflict["knowledge_candidates"][0])
        unchanged, conflict_ok, conflict_message = core.review_knowledge_candidate(
            conflict_id, conflict_candidate_id, "project_term")
        assert not conflict_ok and "冲突" in conflict_message
        assert unchanged["glossary"][0]["preferred"] == "连续性"
        assert not unchanged["knowledge_candidates"][0].get("decision")

        task_id = "knowledgetask000001"
        task = core.new_job_state("task.docx")
        task.update(knowledge_candidates=[_candidate("task term", "任务术语")])
        core.save_job_state(task_id, task)
        task_candidate_id = knowledge.candidate_id(task["knowledge_candidates"][0])
        accepted, accepted_ok, accepted_message = core.review_knowledge_candidate(
            task_id, task_candidate_id, "task_only")
        assert accepted_ok, accepted_message
        assert accepted["glossary"] == []
        assert accepted["knowledge_candidates"][0]["status"] == "accepted_task"

        rejected_id = "knowledgereject0001"
        rejected = core.new_job_state("reject.docx")
        rejected.update(knowledge_candidates=[_candidate("reject term", "拒绝术语")])
        core.save_job_state(rejected_id, rejected)
        rejected_candidate_id = knowledge.candidate_id(rejected["knowledge_candidates"][0])
        rejected_state, rejected_ok, rejected_message = core.review_knowledge_candidate(
            rejected_id, rejected_candidate_id, "rejected")
        assert rejected_ok, rejected_message
        assert rejected_state["knowledge_candidates"][0]["status"] == "rejected"
        assert knowledge.provisional_hints(rejected_state["knowledge_candidates"]) == []
        rejection = next(action for action in rejected_state["human_actions"]
                         if action["action"] == "knowledge_rejected")
        assert rejection["actor_type"] == "human"
        assert rejection["previous_status"] == "emergent_candidate"
        assert rejection["new_status"] == "rejected"
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


