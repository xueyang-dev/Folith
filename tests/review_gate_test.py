"""Deterministic pre-review gating: unit rules and pipeline behaviour.

The gate exists so review tokens are spent only where review can add value.
These tests lock the two properties that matter:

* the rule itself (which signals force review, which batch may be skipped);
* the *provenance contract* — a skipped batch is never reported as reviewed,
  approved, or translation-memory eligible, in ``always`` mode nothing changes,
  and a resume never re-reviews or re-skips a committed batch.

Measured evidence for the policy lives in
``docs/review-efficiency-pass.md``; this module only pins the behaviour.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import review_gate as _gate  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402

TRANSLATOR_MARKER = "专业翻译人员"
REVIEW_MARKER = "独立的翻译审校专家"
KNOWLEDGE_MARKER = "翻译流知识抽取器"
REPAIR_MARKER = "问题清单"

REVIEW_CLEAN = json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)

SEGMENTS = [
    "Space has become an important component of economic development.",
    "Germany has emerged as one of Europe's leading space nations.",
    "The European Space Agency coordinates European space activities.",
    "Urban air mobility introduces new operational challenges.",
    "Battery capacity and weight trade off against each other.",
    "Vertiports are required for take-off and landing operations.",
    "Artemis is a lunar exploration programme led by NASA.",
    "The European Service Module supports propulsion and power.",
]
TARGETS = [
    "太空已成为经济发展的重要组成部分。",
    "德国已成为欧洲领先的航天国家之一。",
    "欧洲空间局负责协调欧洲的航天活动。",
    "城市空中交通带来了新的运营挑战。",
    "电池容量与重量之间存在权衡。",
    "垂直起降场是起降运行所必需的。",
    "阿尔忒弥斯计划是由美国国家航空航天局主导的月球探索项目。",
    "欧洲服务舱为推进与供电提供支持。",
]

LOCKED_TERM = {
    "id": "term-esa",
    "source": "European Space Agency",
    "target": "欧洲空间局",
    "preferred": "欧洲空间局",
    "forbidden": [],
    "behavior": "translate",
    "status": "locked",
    "scope": "global",
    "note": "",
}


class ScriptedProvider:
    """Deterministic provider; only the review role is interesting here."""

    def __init__(self, *, targets: Dict[str, str], review_payload: str = REVIEW_CLEAN,
                 review_after_batch: int = 0) -> None:
        self.targets = dict(targets)
        self.review_payload = review_payload
        self.review_after_batch = review_after_batch
        self.calls: List[Dict[str, Any]] = []
        self.review_calls = 0
        self.translation_batches = 0

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = system_prompt or ""
        if REVIEW_MARKER in system_prompt:
            return "review"
        if KNOWLEDGE_MARKER in system_prompt:
            return "auxiliary"
        if REPAIR_MARKER in (user_prompt or ""):
            return "repair"
        if TRANSLATOR_MARKER in system_prompt:
            return "translation"
        return "unknown"

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        role = self.classify(system_prompt, user_prompt)
        self.calls.append({"role": role})
        if role == "review":
            self.review_calls += 1
            return self.review_payload
        if role == "auxiliary":
            return "[]"
        sources = self._sources(user_prompt)
        if role == "translation":
            self.translation_batches += 1
            return json.dumps(
                [self.targets.get(s, "") for s in sources], ensure_ascii=False)
        if role == "repair":
            return json.dumps(
                [self.targets.get(s, "") for s in sources], ensure_ascii=False)
        return json.dumps([], ensure_ascii=False)

    @staticmethod
    def _sources(user_prompt: str) -> List[str]:
        out: List[str] = []
        for line in (user_prompt or "").splitlines():
            stripped = line.strip()
            head, sep, rest = stripped.partition(". ")
            if sep and head.isdigit():
                out.append(rest)
            elif stripped.startswith("原文："):
                out.append(stripped[len("原文："):])
        return out


def _run(tmp_path: Path, *, provider: ScriptedProvider, review_mode: str,
         job_id: str = "gate-job", existing_state: Dict[str, Any] | None = None,
         enable_review: bool = True, batch_size: int = 2) -> Dict[str, Any]:
    core.OUTPUT_DIR = tmp_path
    state = existing_state if existing_state is not None \
        else core.new_job_state("gate.txt")
    if existing_state is None:
        state["paras"] = list(SEGMENTS)
        state["target_lang"] = "简体中文"
        state["pairs"] = []
        # A frozen glossary is what production freezes before translation; the
        # review-truth reconciler compares the persisted glossary hash against
        # the one each review event recorded.
        from folith import models as _models
        state["glossary_frozen"] = {
            "entries": [dict(LOCKED_TERM)],
            "glossary_hash": _models.glossary_hash(
                _models.normalize_glossary([dict(LOCKED_TERM)])),
        }
    state["review_mode"] = review_mode
    state["style_rules"] = ""
    original = core.call_llm
    core.call_llm = provider
    try:
        core.translate_stage(
            state, job_id, [dict(LOCKED_TERM)], "Benchmark", "key", "model",
            "简体中文", "", enable_review=enable_review, use_tm=True,
            batch_size=batch_size, max_batch_chars=100000,
            translation_concurrency=1, review_mode=review_mode)
    finally:
        core.call_llm = original
    return state


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #

def test_clean_batch_is_skipped():
    signals = _gate.batch_signals_from_context(
        findings=[{"severity": "informational"}],
        batch_pairs=[{"target": "译文", "from_tm": False}],
        segment_ids=[0, 1])
    assert _gate.should_run_independent_review(signals) == (False, "clean_deterministic")


def test_blocking_finding_forces_review():
    signals = _gate.batch_risk_signals(findings=[{"severity": "blocking"}])
    assert _gate.should_run_independent_review(signals) == (
        True, "blocking deterministic finding in batch")


def test_actionable_finding_forces_review():
    signals = _gate.batch_risk_signals(findings=[{"severity": "actionable"}])
    assert _gate.should_run_independent_review(signals)[0] is True


def test_terminology_conflict_forces_review():
    # A locked-term violation surfaces as an actionable deterministic finding.
    signals = _gate.batch_signals_from_context(
        findings=[{"severity": "actionable", "category": "terminology_consistency"}],
        batch_pairs=[{"target": "译文", "from_tm": False}],
        segment_ids=[0])
    assert _gate.should_run_independent_review(signals)[0] is True


def test_malformed_translation_forces_review():
    signals = _gate.batch_signals_from_context(
        batch_pairs=[{"target": "", "from_tm": False}], segment_ids=[0])
    assert _gate.should_run_independent_review(signals) == (
        True, "empty, truncated or structurally invalid target")


def test_repair_attempt_forces_review():
    signals = _gate.batch_risk_signals(repair_attempted=True)
    assert _gate.should_run_independent_review(signals) == (
        True, "deterministic repair changed this batch")


def test_protocol_failure_forces_review():
    signals = _gate.batch_risk_signals(protocol_failure=True)
    assert _gate.should_run_independent_review(signals)[0] is True


def test_entity_conflict_forces_review():
    signals = _gate.batch_signals_from_context(
        batch_pairs=[{"target": "译文", "from_tm": False}],
        findings_all=[{"type": "entity_conflict", "segment_id": 3}],
        segment_ids=[2, 3])
    assert _gate.should_run_independent_review(signals)[0] is True


def test_already_reviewed_tm_content_is_never_re_reviewed():
    signals = _gate.batch_signals_from_context(
        batch_pairs=[{"target": "已审校译文", "from_tm": True}], segment_ids=[0])
    assert _gate.should_run_independent_review(signals) == (
        False, "already_reviewed_tm")


@pytest.mark.default_review_mode
def test_unknown_review_mode_falls_back_to_default_risk_selective():
    assert _gate.DEFAULT_REVIEW_MODE == _gate.REVIEW_MODE_RISK_SELECTIVE
    assert _gate.resolve_review_mode() == _gate.REVIEW_MODE_RISK_SELECTIVE
    assert _gate.resolve_review_mode("nonsense") == _gate.REVIEW_MODE_RISK_SELECTIVE
    # A task that recorded ``always`` keeps it.
    assert _gate.resolve_review_mode(None, None, {"review_mode": "always"}) ==         _gate.REVIEW_MODE_ALWAYS
    assert _gate.resolve_review_mode(None, {"review_mode": "risk_selective"}) == \
        _gate.REVIEW_MODE_RISK_SELECTIVE
    assert _gate.resolve_review_mode(None, None, {"review_mode": "risk_selective"}) == \
        _gate.REVIEW_MODE_RISK_SELECTIVE
    assert _gate.resolve_review_mode("always", {"review_mode": "risk_selective"}) == \
        _gate.REVIEW_MODE_ALWAYS


# --------------------------------------------------------------------------- #
# Pipeline behaviour
# --------------------------------------------------------------------------- #

def test_always_mode_reviews_every_batch(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider, review_mode=_gate.REVIEW_MODE_ALWAYS)
    assert provider.translation_batches == 4
    assert provider.review_calls == 4
    assert state["review_stats"]["batches_reviewed"] == 4
    assert state["review_stats"].get("batches_skipped_low_risk", 0) == 0
    assert all(p["review_status"] == "reviewed_clean" for p in state["pairs"])


@pytest.mark.default_review_mode
def test_new_task_without_explicit_mode_defaults_to_selective(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    core.OUTPUT_DIR = tmp_path
    state = core.new_job_state("gate.txt")
    state["paras"] = list(SEGMENTS)
    state["target_lang"] = "简体中文"
    state["pairs"] = []
    state["style_rules"] = ""
    original = core.call_llm
    core.call_llm = provider
    try:
        core.translate_stage(
            state, "default-mode-job", [], "Benchmark", "key", "model",
            "简体中文", "", enable_review=True, use_tm=True, batch_size=2,
            max_batch_chars=100000, translation_concurrency=1)
    finally:
        core.call_llm = original
    assert state["review_mode"] == _gate.REVIEW_MODE_RISK_SELECTIVE
    assert provider.review_calls == 0
    assert all(p["review_status"] == _gate.SKIPPED_LOW_RISK for p in state["pairs"])
    assert all(p["reviewed"] is False for p in state["pairs"])


def test_selective_mode_skips_clean_batches_with_explicit_provenance(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE)
    assert provider.translation_batches == 4
    assert provider.review_calls == 0
    stats = state["review_stats"]
    assert stats["batches_reviewed"] == 0
    assert stats["batches_skipped_low_risk"] == 4
    assert stats["skipped_segments"] == len(SEGMENTS)
    # A skip is never presented as a review, and never earns TM eligibility.
    assert all(p["review_status"] == _gate.SKIPPED_LOW_RISK for p in state["pairs"])
    assert all(p["reviewed"] is False for p in state["pairs"])
    assert all(p.get("accepted_target") in (None, "") for p in state["pairs"])
    events = [e for e in state.get("review_evidence") or []
              if e.get("phase") == "risk_selective_skip"]
    assert len(events) == 4
    assert all(e["decision"] == _gate.SKIPPED_LOW_RISK for e in events)
    assert all(e["completion_receipt"]["status"] == _gate.SKIPPED_LOW_RISK
               for e in events)


def test_selective_mode_still_reviews_a_risky_batch(tmp_path):
    """A batch whose translation breaks a locked term must still be reviewed."""
    targets = dict(zip(SEGMENTS, TARGETS))
    targets[SEGMENTS[2]] = "该机构负责协调欧洲的航天活动。"  # drops 欧洲空间局
    provider = ScriptedProvider(targets=targets)
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE)
    assert provider.review_calls >= 1
    reviewed = [p for p in state["pairs"]
                if p["review_status"] != _gate.SKIPPED_LOW_RISK]
    assert reviewed, "the batch carrying the terminology conflict must be reviewed"
    assert state["review_stats"]["batches_reviewed"] >= 1


def test_always_mode_marks_reviewed_batches_tm_eligible(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider, review_mode=_gate.REVIEW_MODE_ALWAYS)
    assert state["review_stats"]["reviewed_segments"] == len(SEGMENTS)
    assert all(p["target_provenance"] == "reviewed" for p in state["pairs"])


def test_selective_mode_never_promotes_skipped_batches_to_tm(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE)
    assert state["review_stats"]["reviewed_segments"] == 0
    assert all(p["target_provenance"] == "generated" for p in state["pairs"])


def test_selective_mode_preserves_order_and_targets(tmp_path):
    always = _run(tmp_path / "always",
                  provider=ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS))),
                  review_mode=_gate.REVIEW_MODE_ALWAYS, job_id="order-always")
    selective = _run(tmp_path / "selective",
                     provider=ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS))),
                     review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE,
                     job_id="order-selective")
    assert [p["source"] for p in always["pairs"]] == \
        [p["source"] for p in selective["pairs"]]
    assert [p["target"] for p in always["pairs"]] == \
        [p["target"] for p in selective["pairs"]]


def test_resume_does_not_re_review_or_re_skip(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE, job_id="resume-job")
    first_review_calls = provider.review_calls
    resumed_provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    resumed = _run(tmp_path, provider=resumed_provider,
                   review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE,
                   job_id="resume-job", existing_state=json.loads(json.dumps(state)))
    assert resumed_provider.translation_batches == 0
    assert resumed_provider.review_calls == 0
    assert resumed["review_stats"]["batches_skipped_low_risk"] == 4
    assert first_review_calls == 0


def test_readiness_reports_skipped_segments_separately(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE)
    readiness = _evidence.translation_review_readiness(state)
    assert readiness["review_mode"] == _gate.REVIEW_MODE_RISK_SELECTIVE
    assert sorted(readiness["skipped_segment_ids"]) == list(range(len(SEGMENTS)))
    assert readiness["current_segment_ids"] == []
    assert readiness["missing_segment_ids"] == []
    assert readiness["ready"] is True
    assert readiness["reviewed_count"] == 0
    assert readiness["skipped_count"] == len(SEGMENTS)
    assert readiness["coverage_complete"] is False
    assert readiness["coverage_status"] == "skipped"
    assert readiness["skipped_reason_counts"] == {
        "clean_deterministic": len(SEGMENTS)}


def test_readiness_in_always_mode_still_requires_every_segment(tmp_path):
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider, review_mode=_gate.REVIEW_MODE_ALWAYS)
    readiness = _evidence.translation_review_readiness(state)
    assert readiness["skipped_segment_ids"] == []
    assert sorted(readiness["current_segment_ids"]) == list(range(len(SEGMENTS)))
    assert readiness["ready"] is True
    assert readiness["reviewed_count"] == len(SEGMENTS)
    assert readiness["skipped_count"] == 0
    assert readiness["coverage_complete"] is True
    assert readiness["coverage_status"] == "complete"


def _seed_tm() -> Dict[str, Any]:
    tm: Dict[str, Any] = {}
    for source, target in zip(SEGMENTS, TARGETS):
        core.tm_put(tm, source, target, "简体中文")
    return tm


def test_tm_approved_content_is_not_re_reviewed_in_selective_mode(tmp_path, monkeypatch):
    """Already-reviewed TM content must not pay for a second independent review."""
    tm = _seed_tm()
    monkeypatch.setattr(core, "load_tm", lambda *args, **kwargs: dict(tm))
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider,
                 review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE)
    assert provider.translation_batches == 0, "TM should serve every segment"
    assert provider.review_calls == 0
    assert state["review_stats"]["batches_skipped_low_risk"] == 4


def test_tm_approved_content_is_still_reviewed_in_always_mode(tmp_path, monkeypatch):
    """The default policy is unchanged: ``always`` reviews TM-served batches too."""
    tm = _seed_tm()
    monkeypatch.setattr(core, "load_tm", lambda *args, **kwargs: dict(tm))
    provider = ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS)))
    state = _run(tmp_path, provider=provider, review_mode=_gate.REVIEW_MODE_ALWAYS)
    assert provider.translation_batches == 0
    assert provider.review_calls == 4
    assert state["review_stats"]["batches_reviewed"] == 4


def _run_overlap(tmp_path: Path, *, provider: ScriptedProvider,
                 review_mode: str, job_id: str) -> Dict[str, Any]:
    """Same run, but through the reviewer-overlap scheduler path."""
    core.OUTPUT_DIR = tmp_path
    state = core.new_job_state("overlap.txt")
    state["paras"] = list(SEGMENTS)
    state["target_lang"] = "简体中文"
    state["pairs"] = []
    state["style_rules"] = ""
    from folith import models as _models
    state["glossary_frozen"] = {
        "entries": [dict(LOCKED_TERM)],
        "glossary_hash": _models.glossary_hash(
            _models.normalize_glossary([dict(LOCKED_TERM)])),
    }
    state["pipeline_config"] = {"review_overlap": True, "max_review_in_flight": 2}
    state["review_mode"] = review_mode
    original = core.call_llm
    core.call_llm = provider
    try:
        core.translate_stage(
            state, job_id, [dict(LOCKED_TERM)], "Benchmark", "key", "model",
            "简体中文", "", enable_review=True, use_tm=True,
            batch_size=2, max_batch_chars=100000, translation_concurrency=1,
            review_overlap=True, review_mode=review_mode)
    finally:
        core.call_llm = original
    return state


def test_overlap_path_honours_the_gate_in_both_modes(tmp_path):
    always = _run_overlap(
        tmp_path / "always",
        provider=ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS))),
        review_mode=_gate.REVIEW_MODE_ALWAYS, job_id="overlap-always")
    assert always["review_stats"]["batches_reviewed"] == 4
    assert {p["review_status"] for p in always["pairs"]} == {"reviewed_clean"}

    selective = _run_overlap(
        tmp_path / "selective",
        provider=ScriptedProvider(targets=dict(zip(SEGMENTS, TARGETS))),
        review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE, job_id="overlap-selective")
    assert selective["review_stats"]["batches_reviewed"] == 0
    assert selective["review_stats"]["batches_skipped_low_risk"] == 4
    assert {p["review_status"] for p in selective["pairs"]} == {_gate.SKIPPED_LOW_RISK}
    assert all(p["reviewed"] is False for p in selective["pairs"])


def test_overlap_path_still_reviews_a_risky_batch(tmp_path):
    targets = dict(zip(SEGMENTS, TARGETS))
    targets[SEGMENTS[2]] = "该机构负责协调欧洲的航天活动。"
    provider = ScriptedProvider(targets=targets)
    state = _run_overlap(tmp_path, provider=provider,
                         review_mode=_gate.REVIEW_MODE_RISK_SELECTIVE,
                         job_id="overlap-risky")
    assert provider.review_calls >= 1
    assert state["review_stats"]["batches_reviewed"] >= 1
    assert any(p["review_status"] != _gate.SKIPPED_LOW_RISK for p in state["pairs"])
