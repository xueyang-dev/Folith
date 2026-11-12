"""Release-stabilisation E2E for Decision Studio, driven through real core APIs.

Every scenario builds a disposable job on disk (``core.OUTPUT_DIR`` is a tmp
directory), runs the real deterministic detectors, and moves state only through
the supported transaction APIs.  Nothing here assembles UI dictionaries by hand.
Reopening a task is a fresh ``load_job_state`` after cache invalidation.
"""
from __future__ import annotations

import pytest

import core
from folith import consistency, delivery, finding_triage as ft
from folith.engine import candidates as eng
from folith.engine import triage_resolution as tr
from folith.storage import job_repository as repo

LANG = "简体中文"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield tmp_path
    repo.invalidate_cache()


def _make(job_id, src, tgt, *, enabled=True, glossary=None):
    state = core.new_job_state("doc.docx")
    state["pairs"] = [{"source": s, "target": t, "initial_target": t}
                      for s, t in zip(src, tgt)]
    state["glossary"] = glossary or []
    state["target_lang"] = LANG
    state["p2_done"] = True
    # A realistic parsed document: one paragraph per segment.
    state.update(
        source_paras=list(src), source_paragraphs=list(src), paras=list(src),
        segmentation={"paragraph_ranges": [
            {"paragraph_index": i, "start": i, "end": i + 1} for i in range(len(src))]})
    state["qa_triage_enabled"] = bool(enabled)
    found = core.check_translation_batch(src, tgt, state["glossary"], LANG)
    for f in found:
        f.setdefault("segment_id", f.get("segment_index"))
    state["findings"] = found
    core.save_job_state(job_id, state)
    return state


def _reopen(job_id):
    repo.invalidate_cache()
    return core.load_job_state(job_id)


def _redetect(job_id):
    """Refresh QA the way an edit does: through the supported save path."""
    return _reopen(job_id)


# -- A: missing translation -------------------------------------------------

def test_a_missing_translation_is_detected_projected_and_cleared_by_edit(job):
    _make("a", ["Hello world today.", "Second sentence here."],
          ["", "第二句在这里。"])
    state = _reopen("a")
    missing = [f for f in state["findings"] if f.get("segment_index") == 0]
    assert missing, "detector must flag the untranslated segment"
    assert state["pairs"][0]["target"] == ""            # UI shows it truthfully empty
    # Delivery treats missing text as a visible notice, never a hard block.
    queue = delivery.review_queue_findings(state)
    assert any(f.get("segment_index") == 0 for f in queue)
    assert all(f.get("severity") != "blocking"
               for f in delivery.unresolved_findings(state)
               if f.get("segment_index") == 0)
    assert delivery.is_missing_translation_finding(state, missing[0])

    core.save_translation_edit("a", 0, "你好，今天的世界。")
    state = _reopen("a")
    assert state["pairs"][0]["target"] == "你好，今天的世界。"
    assert not any(delivery.is_missing_translation_finding(state, f)
                   for f in delivery.unresolved_findings(state)
                   if f.get("segment_index") == 0)


# -- B: source == target ----------------------------------------------------

def test_b_identical_source_target_is_flagged_and_persists(job):
    src = ["Keep this text now.", "Another line goes here."]
    _make("b", src, ["Keep this text now.", "另一行在这里。"])
    state = _reopen("b")
    flagged = [f for f in state["findings"] if f.get("segment_index") == 0]
    assert flagged, "identical source/target must be flagged"
    assert state["pairs"][0]["target"] == state["pairs"][0]["source"]
    # The decision to explicitly keep it is recorded and survives reopening.
    fid = delivery.finding_id(flagged[0], 0)
    delivery.add_human_action(state, fid, "keep_as_is", note="proper name") \
        if "note" in delivery.add_human_action.__code__.co_varnames else \
        delivery.add_human_action(state, fid, "keep_as_is")
    core.save_job_state("b", state)
    again = _reopen("b")
    assert any(a.get("action") == "keep_as_is" for a in again.get("human_actions") or [])
    assert again["pairs"][0]["target"] == src[0]             # no mutation implied


# -- C: terminology conflict ------------------------------------------------

def _terminology_state(job_id):
    src = ["The Skopos theory is central.", "Skopos is introduced early.",
           "We apply Skopos here."]
    tgt = ["目的论是核心。", "斯科普斯在早期被引入。", "我们在此应用目的论。"]
    state = _make(job_id, src, tgt)
    record = {"source_form": "Skopos", "preferred_target": "目的论",
              "status": "candidate", "aliases": [],
              "observations": [{"target": "目的论", "segment_id": 0},
                               {"target": "斯科普斯", "segment_id": 1},
                               {"target": "目的论", "segment_id": 2}],
              "occurrences": [0, 1, 2], "first_seen_segment": 0}
    state["entity_registry"] = [record]
    state["findings"] = consistency.findings([record], state["pairs"])
    for f in state["findings"]:
        f["id"] = f.get("id") or "term-skopos"
    core.save_job_state(job_id, state)
    return record


def test_c_terminology_conflict_stages_candidate_without_touching_text(job):
    record = _terminology_state("c")
    state = _reopen("c")
    conflict = [f for f in state["findings"] if f["type"] == "entity_conflict"]
    assert conflict and set(conflict[0]["observed_targets"]) == {"目的论", "斯科普斯"}

    preview = consistency.preview(record, state["pairs"], "目的论")
    assert [row["index"] for row in preview] == [1]          # only the divergent segment

    before = [p["target"] for p in state["pairs"]]
    result = eng.stage_term_revision("c", "Skopos", "斯科普斯", "目的论")
    state = _reopen("c")
    assert [p["target"] for p in state["pairs"]] == before   # staging never edits text
    pending = [c for c in state.get("translation_candidates") or []
               if c["status"] == "pending"]
    assert pending and result["preview"]

    # Adopting is the separate, versioned step; only then does text change.
    eng.adopt_translation_candidate("c", pending[0]["candidate_id"])
    state = _reopen("c")
    assert state["pairs"][1]["target"] == "目的论在早期被引入。"
    assert consistency.findings([record], state["pairs"]) == []


# -- D: deterministic repair, adopt, revoke ---------------------------------

SRC_D, WRONG_D, FIXED_D = "In 131 the war began.", "In 132 the war began.", "In 131 the war began."


def _repair_job(job_id):
    state = _make(job_id, [SRC_D], [WRONG_D])
    finding = next(f for f in state["findings"] if f.get("kind") == "number")
    finding["segment_index"] = finding["segment_id"] = 0
    core.save_job_state(job_id, state)
    return tr.triage_key(finding)


def test_d_repair_commit_audit_and_revert_never_overwrites(job):
    key = _repair_job("d")
    assert tr.stage_auto_resolution("d", key)["staged"] is True
    assert _reopen("d")["pairs"][0]["target"] == WRONG_D

    assert tr.commit_auto_resolution("d", key)["committed"] is True
    state = _reopen("d")
    assert state["pairs"][0]["target"] == FIXED_D
    record = state["finding_triage"][key]
    assert record["snapshot"]["target"] == WRONG_D           # before
    assert [e["kind"] for e in record["evidence"]] == [
        "structural_repair", "candidate_verification"]       # provenance
    row = [a for a in state["human_actions"] if a["action"] == "candidate_adopted"][-1]
    assert row["actor"] == "qa_triage" and row["record_type"] != "human"

    reverted = tr.revert_auto_resolution("d", key)
    assert reverted["reverted"] is True and reverted["candidate_id"]
    state = _reopen("d")
    assert state["pairs"][0]["target"] == FIXED_D            # revoke is not an overwrite
    revert = next(c for c in state["translation_candidates"]
                  if c["candidate_id"] == reverted["candidate_id"])
    assert revert["status"] == "pending" and revert["candidate_target"] == WRONG_D
    assert ft.disposition_of(state, tr.keyed_finding(
        next(f for f in state["findings"] if tr.triage_key(f) == key))) == "human_required"


# -- E: verified_safe -------------------------------------------------------

def test_e_verified_safe_needs_live_evidence_and_never_mutates_text(job):
    # Since #312 a spelling is only "verified safe" when the live entity registry
    # shows the forms differ by outer brackets alone (traditional/simplified
    # variants are no longer treated as equivalent).
    state = _make("e", ["Sinology is taught here.", "Sinology is taught again."],
                  ["汉学在这里教授。", "〈汉学〉再次被教授。"])
    state["entity_registry"] = [{
        "source_form": "Sinology", "preferred_target": "汉学", "aliases": [],
        "accepted_variants": [],
        "observations": [{"target": "汉学", "segment_id": 0},
                         {"target": "〈汉学〉", "segment_id": 1}],
        "occurrences": [0, 1]}]
    finding = {"type": "entity_conflict", "severity": "actionable",
               "segment_index": 0, "segment_id": 0, "finding_id": "e-orth",
               "source": "Sinology", "preferred_target": "汉学",
               "observed_targets": ["〈汉学〉"]}
    verdict = ft.classify(state, finding)
    assert verdict["disposition"] == ft.VERIFIED_SAFE
    before = state["pairs"][0]["target"]
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=verdict["evidence"])
    state["findings"] = [finding]
    core.save_job_state("e", state)
    state = _reopen("e")
    assert state["pairs"][0]["target"] == before
    assert ft.disposition_of(state, finding) == ft.VERIFIED_SAFE

    # No evidence, or evidence that does not hold, fails closed.
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule")
    unrelated = dict(finding, finding_id="e-other", source="Unrelated term")
    with pytest.raises(ValueError):
        ft.record(state, unrelated, disposition=ft.VERIFIED_SAFE, actor_type="rule",
                  evidence=verdict["evidence"])
    # Stale evidence: the live text changed after recording (a different spelling
    # now appears in the document), so the old verdict no longer holds.
    state["pairs"][1]["target"] = "中国学再次被教授。"
    state["entity_registry"][0]["observations"][1]["target"] = "中国学"
    assert ft.disposition_of(state, finding) != ft.VERIFIED_SAFE


# -- F: independent review disabled ----------------------------------------

def test_f_disabled_review_is_distinct_from_deterministic_qa(job):
    _make("f", [SRC_D], [WRONG_D], enabled=False)
    state = _reopen("f")
    assert ft.enabled(state) is False
    # Deterministic findings still exist and drive the original delivery flow.
    assert any(f.get("kind") == "number" for f in state["findings"])
    assert delivery.compute_delivery_status(state) in ("draft", "review_required")
    # No triage record may claim the task passed independent review.
    assert not state.get("finding_triage")
    key = tr.triage_key(next(f for f in state["findings"] if f.get("kind") == "number"))
    assert tr.stage_auto_resolution("f", key)["staged"] is False
    assert not _reopen("f").get("finding_triage")


# -- G: persistence ---------------------------------------------------------

def test_g_findings_decisions_and_candidates_survive_reopen(job):
    key = _repair_job("g")
    tr.stage_auto_resolution("g", key)
    first = _reopen("g")
    tr.commit_auto_resolution("g", key)
    tr.revert_auto_resolution("g", key)
    second = _reopen("g")
    assert second["finding_triage"][key]["revoked_at"]
    assert [c["status"] for c in second["translation_candidates"]].count("adopted") == 1
    assert any(c["status"] == "pending" for c in second["translation_candidates"])
    assert ft.validate_state(second) == []
    assert len(second["findings"]) == len(first["findings"])
