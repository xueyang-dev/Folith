"""Desktop concept decisions over the existing registry and candidate contracts.

No new task truth: decisions use concept_cards; text changes use candidates.
The desktop holds the exact preview, not a permission to recompute new edits.
"""
from __future__ import annotations

from folith import concept_cards as cc, concept_revision as cr
from folith import translation_candidates as candidates
from folith.entity_registry import EntityRegistry
from folith.engine._link import core
from folith.engine.locks import _serialized_job_mutation


def _state(job_id, *, writing=False):
    if writing and core._job_execution_alive(job_id):
        raise ValueError("任务仍在执行，请等待后台处理结束")
    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError("找不到任务")
    return state


def _cards(state):
    return cc.build_concept_cards(state.get("entity_registry") or [],
                                  state.get("pairs") or [],
                                  state.get("concept_decisions"))


def view(job_id):
    return {"cards": _cards(_state(job_id))}


def _card(state, expected):
    card = next((c for c in _cards(state)
                 if c["card_id"] == expected.get("card_id")), None)
    if card != expected:
        raise ValueError("概念或译文已变化，请重新打开概念决策。")
    return card


@_serialized_job_mutation
def save_decision(job_id, expected, fields):
    state = _state(job_id, writing=True)
    card = _card(state, expected)
    preferred = str(fields.get("preferred_target") or "").strip()
    keep = bool(fields.get("keep_variants"))
    if not keep and (not preferred or len(preferred) > 300):
        raise ValueError("首选译名不能为空，且不能超过 300 个字符。")
    exceptions = fields.get("exceptions") or []
    for exception in exceptions:
        scope = str(exception.get("scope") or "")
        # Existing section exceptions are carried through unchanged; the UI
        # creates segment exceptions using the engine's zero-based indexes.
        if scope.startswith("segment:"):
            index = int(scope.removeprefix("segment:"))
            if index not in card["affected_segments"]:
                raise ValueError("例外段落不属于当前概念。")
        elif exception not in (card.get("decision") or {}).get("exceptions", []):
            raise ValueError("请选择当前概念中的段落作为例外。")
        if not str(exception.get("target") or "").strip() or len(exception["target"]) > 300:
            raise ValueError("例外译名不能为空，且不能超过 300 个字符。")
    if len({e["scope"] for e in exceptions}) != len(exceptions):
        raise ValueError("同一段落只能有一个例外译名。")
    decisions = cc.normalize_concept_decisions(state.get("concept_decisions"))
    registry = EntityRegistry(state.get("entity_registry") or [])
    cc.record_concept_decision(decisions, card=card, registry=registry,
                               preferred_target=preferred, keep_variants=keep,
                               exceptions=exceptions, note=fields.get("note") or "")
    state["concept_decisions"] = decisions
    state["entity_registry"] = registry.to_list()
    # A decision is not a text adoption or a review approval.
    from folith.engine.glossary_ops import _refresh_entity_consistency
    _refresh_entity_consistency(state, actor="user")
    core._invalidate_final_delivery_state(state)
    core.save_job_state(job_id, state)
    return {"cards": _cards(state)}


@_serialized_job_mutation
def split(job_id, expected, form_a, form_b):
    state = _state(job_id, writing=True)
    card = _card(state, expected)
    if form_a == form_b or not {form_a, form_b} <= set(card["members"]):
        raise ValueError("请选择两个不同的原文形式。")
    decisions = cc.normalize_concept_decisions(state.get("concept_decisions"))
    registry = EntityRegistry(state.get("entity_registry") or [])
    registry.split_variants(form_a, form_b)
    cc.split_concept_card(decisions, form_a, form_b, registry=registry)
    state["concept_decisions"] = decisions
    state["entity_registry"] = registry.to_list()
    from folith.engine.glossary_ops import _refresh_entity_consistency
    _refresh_entity_consistency(state, actor="user")
    core._invalidate_final_delivery_state(state)
    core.save_job_state(job_id, state)
    return {"cards": _cards(state)}


def _preview(state, card):
    preview = cr.preview_concept_revision(
        card, state.get("entity_registry") or [], state.get("pairs") or [],
        state.get("concept_decisions"))
    # Include pair identity/protection/review baselines, not only visible text.
    baselines = [{"index": r["segment_index"],
                  "pair": candidates.pair_signature(state["pairs"][r["segment_index"]])}
                 for r in preview["rows"]]
    protected = [{**r, "source": state["pairs"][r["segment_index"]].get("source", ""),
                  "before": state["pairs"][r["segment_index"]].get("target", "")}
                 for r in preview["protected"]]
    return {"card": card, "preview": preview, "baselines": baselines,
            "protected": protected}


def preview(job_id, expected):
    state = _state(job_id)
    return _preview(state, _card(state, expected))


@_serialized_job_mutation
def apply(job_id, expected_preview, selected):
    state = _state(job_id, writing=True)
    card = _card(state, expected_preview["card"])
    current = _preview(state, card)
    if current != expected_preview:
        raise ValueError("译文已变化，请重新查看修改预览")
    indexes = {r["segment_index"] for r in current["preview"]["rows"]}
    selected = set(selected)
    if not selected or not selected <= indexes:
        raise ValueError("请选择预览中的可修改段落。")
    # The outer task lock spans preview comparison, staging and adoption.
    staged = core.stage_concept_revision(job_id, card,
                                        exclusions=sorted(indexes - selected))
    ids = [r["candidate_id"] for r in staged["created"]]
    result = core.adopt_translation_candidates(job_id, ids, allow_protected=False)
    return {**{k: result[k] for k in ("changed", "results", "skipped")},
            "view": {"cards": _cards(result["state"])}}
