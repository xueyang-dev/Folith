"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import hashlib
import json
from datetime import datetime
from datetime import timezone
from folith import entity_registry as _entity_registry
from folith import finalization as _finalization
from folith import knowledge as _knowledge
from folith import models as _models
from folith.engine.glossary_checks import normalize_glossary
from folith.engine.locks import _serialized_job_mutation
from folith.engine.tm_store import state_target_lang
from folith.engine.tm_store import tm_project_id
from folith.engine._link import core as _c


def load_context_artifacts(job_id, state=None):
    """Load persisted context artifacts, tolerating older or partial jobs."""
    state = state if state is not None else _c.load_job_state(job_id)
    state = state or {}

    def read_json(name, default):
        path = _c.job_dir(job_id) / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return default

    units = state.get("semantic_units") or read_json("semantic_units.json", [])
    digests = state.get("section_digests") or read_json("section_digests.json", [])
    synopsis = state.get("document_synopsis")
    stored_synopsis = read_json("document_synopsis.json", None)
    if not isinstance(synopsis, dict):
        synopsis = {}
    if not synopsis or (synopsis.get("status") == "pending"
                        and isinstance(stored_synopsis, dict)
                        and stored_synopsis.get("status") != "pending"):
        synopsis = stored_synopsis or {}
    return {
        "semantic_units": units if isinstance(units, list) else [],
        "section_digests": digests if isinstance(digests, list) else [],
        "document_synopsis": synopsis if isinstance(synopsis, dict) else {},
        "warnings": list(state.get("understanding_warnings") or []),
    }


# ================= 术语审核状态（草稿 / 锁定 / 拒绝 / 冻结）=================
@_serialized_job_mutation
def save_glossary_draft(job_id, entries):
    """保存术语审核草稿（不冻结）。刷新/重启后从 TERMS_PREPARED 恢复。"""
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    norm = normalize_glossary(entries)
    state["glossary_draft"] = norm
    state["glossary"] = norm
    state["stage"] = "TERMS_PREPARED"
    _c.save_job_state(job_id, state)
    return state


def _glossary_mutation_note(action, detail, actor):
    return {
        "action": action,
        "finding_id": detail.get("finding_id") or "glossary",
        "note": detail.get("note") or "",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": str(actor or "user"),
        "actor_type": "human",
        "previous_status": detail.get("previous_status"),
        "new_status": detail.get("new_status"),
    }


@_serialized_job_mutation
def update_glossary_entry(job_id, entry_id, *, preferred=None, domain=None,
                          status=None, note=None, actor="user"):
    """原地修改一条术语条目并生成新的术语版本。返回 (state, ok, message)。

    与 `save_glossary_draft` 的区别：后者是「翻译前准备术语」，会把任务阶段改回
    TERMS_PREPARED。语言资产管理中心对**已完成任务**做一次术语修订，不应该把
    任务打回准备阶段，因此这里直接改条目、记录人工动作，然后走既有的
    `freeze_glossary` —— 术语决策变化必须产生新版本并失效受影响段落，这条
    不变量不能被绕过。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        return None, False, "任务不存在"
    entries = normalize_glossary(state.get("glossary") or [])
    target = next((item for item in entries
                   if str(item.get("id")) == str(entry_id)), None)
    if target is None:
        return state, False, "找不到该术语"
    before = {key: target.get(key)
              for key in ("preferred", "target", "domain", "status", "note")}
    if preferred is not None:
        text = str(preferred or "").strip()
        if not text:
            return state, False, "推荐译法不能为空"
        target["preferred"] = text
        target["target"] = text
        target["proposed_target"] = text
    if domain is not None:
        target["domain"] = str(domain or "").strip()
    if status is not None:
        if status not in _models.STATUSES:
            return state, False, f"非法术语状态：{status}"
        target["status"] = status
    if note is not None:
        target["note"] = str(note or "").strip()
    after = {key: target.get(key) for key in before}
    if before == after:
        return state, True, "没有需要保存的改动"
    entries = normalize_glossary(entries)
    state["glossary"] = entries
    state["glossary_draft"] = entries
    state.setdefault("human_actions", []).append(_glossary_mutation_note(
        "glossary_entry_updated",
        {"finding_id": f"glossary:{entry_id}",
         "note": "在语言资产管理中心修改术语条目",
         "previous_status": before.get("status"),
         "new_status": after.get("status")}, actor))
    _c.save_job_state(job_id, state)
    frozen = freeze_glossary(job_id, entries=entries, frozen_by=actor)
    version = (frozen or {}).get("glossary_frozen", {}).get("version") \
        if isinstance(frozen, dict) else None
    suffix = f"，术语版本已更新为 v{version}" if version else ""
    return frozen or state, True, f"已更新术语并生成新的术语版本{suffix}"


@_serialized_job_mutation
def delete_glossary_entry(job_id, entry_id, actor="user"):
    """删除一条术语条目并生成新的术语版本。返回 (state, ok, message)。"""
    state = _c.load_job_state(job_id)
    if state is None:
        return None, False, "任务不存在"
    entries = normalize_glossary(state.get("glossary") or [])
    kept = [item for item in entries if str(item.get("id")) != str(entry_id)]
    if len(kept) == len(entries):
        return state, False, "找不到该术语"
    state["glossary"] = kept
    state["glossary_draft"] = kept
    state.setdefault("human_actions", []).append(_glossary_mutation_note(
        "glossary_entry_deleted",
        {"finding_id": f"glossary:{entry_id}",
         "note": "在语言资产管理中心删除术语条目"}, actor))
    _c.save_job_state(job_id, state)
    frozen = freeze_glossary(job_id, entries=kept, frozen_by=actor)
    return frozen or state, True, "已删除该术语并生成新的术语版本"


@_serialized_job_mutation
def add_glossary_entry(job_id, source, target, *, domain="", scope="document",
                       status="locked", actor="user"):
    """在指定任务中新增一条术语并生成新的术语版本。

    返回 (state, ok, message, entry_id)。长期保存位置只有「项目术语」——
    术语写在任务的术语表里并通过冻结版本生效；当前没有独立的全局术语库。
    """
    source = str(source or "").strip()
    target = str(target or "").strip()
    if not source:
        return None, False, "原术语不能为空", ""
    if not target:
        return None, False, "推荐译法不能为空", ""
    if status not in _models.STATUSES:
        return None, False, f"非法术语状态：{status}", ""
    state = _c.load_job_state(job_id)
    if state is None:
        return None, False, "任务不存在", ""
    entries = normalize_glossary(state.get("glossary") or [])
    existing = next((item for item in entries
                     if str(item.get("source") or "").casefold()
                     == source.casefold()), None)
    if existing is not None:
        current = str(existing.get("preferred") or existing.get("target") or "").strip()
        if current.casefold() != target.casefold():
            return state, False, f"该术语已存在，推荐译法为「{current}」；请先编辑原条目", \
                str(existing.get("id") or "")
        return state, False, "该术语已存在", str(existing.get("id") or "")
    entry = _models.normalize_glossary_entry({
        "source": source, "target": target, "preferred": target,
        "behavior": "translate", "status": status, "scope": scope,
        "domain": str(domain or "").strip(),
        "note": "由人工在语言资产管理中心新增",
        "evidence": [{
            "evidence_type": "user", "source_name": "语言资产管理中心",
            "note": "人工新增术语", "quote": "", "url": "",
        }],
    })
    if entry is None:
        return state, False, "术语内容无效", ""
    entries.append(entry)
    entries = normalize_glossary(entries)
    state["glossary"] = entries
    state["glossary_draft"] = entries
    state.setdefault("human_actions", []).append(_glossary_mutation_note(
        "glossary_entry_added",
        {"finding_id": f"glossary:{entry.get('id')}",
         "note": f"在语言资产管理中心新增术语「{source}」"}, actor))
    _c.save_job_state(job_id, state)
    frozen = freeze_glossary(job_id, entries=entries, frozen_by=actor)
    return frozen or state, True, "已新增术语并生成新的术语版本", str(entry.get("id") or "")


def _apply_glossary_staleness(state, job_id=None):
    """把受冻结术语表变更影响的段落标记 stale，并清除其 TM 信任。

    权威集合：每次调用都重新计算（先清旧标记/旧 stale finding，
    再标记当前受影响段）。stale 段：
    - stale_due_to_glossary = True，reviewed = False，from_tm = False；
    - 追加 blocking finding（type=glossary_stale），交付回到 review_required；
    - 从 translation_memory.json 中清除，防止后续任务精确命中旧译文。
    """
    from folith import delivery as _delivery
    from folith.terminology import stale_segments_for_glossary

    stale = stale_segments_for_glossary(state)
    pairs = state.get("pairs") or []
    current_glossary = normalize_glossary(
        (state.get("glossary_frozen") or {}).get("entries")
        or state.get("glossary") or [])
    current_glossary_hash = _models.glossary_hash(current_glossary)
    stale_event_ids = []
    for event in state.get("review_evidence") or []:
        if not isinstance(event, dict) or not (
                event.get("review_scope") == "current_translation"
                or event.get("phase") == "formal_review"):
            continue
        event_hash = str((event.get("translation_core") or {}).get(
            "glossary_hash") or event.get("glossary_hash") or "")
        if event_hash and event_hash != current_glossary_hash \
                and event.get("review_event_id"):
            stale_event_ids.append(str(event["review_event_id"]))
    if stale or stale_event_ids:
        _c._invalidate_translation_reviews(
            state, stale,
            "canonical glossary changed; independent review must be refreshed",
            review_event_ids=stale_event_ids)
    for p in pairs:
        p.pop("stale_due_to_glossary", None)
    state["findings"] = [f for f in state.get("findings") or []
                         if f.get("type") != "glossary_stale"]
    if not stale:
        return state, []

    state["knowledge_candidates"] = _knowledge.discard_candidates_for_segments(
        state.get("knowledge_candidates") or [], stale)
    state["translation_continuity"] = list(state["knowledge_candidates"])

    fg = state.get("glossary_frozen") or {}
    for i in stale:
        p = pairs[i]
        p["stale_due_to_glossary"] = True
        p["reviewed"] = False
        p["from_tm"] = False
        p["review_status"] = "not_reviewed"
        for key in ("accepted_target", "human_accepted", "accepted_by_human",
                    "target_provenance"):
            p.pop(key, None)
        state["findings"].append({
            "segment_id": i, "segment_index": i,
            "severity": "blocking",
            "type": "glossary_stale",
            "entry_id": None,
            "category": "terminology_consistency",
            "summary": "冻结术语表已变更，本段需要重新确认",
            "source_span": None, "target_span": None,
            "explanation": f"冻结术语表已从当前版本变更为 v{fg.get('version')}，本段原有译文可能不再符合最新术语规则。",
            "recommendation": "检查本段涉及的术语；确认译法后标记已处理，或使用最新术语重新翻译。",
            "confidence": None, "detector": "Terminology QA",
            "diagnostic_version": 1,
            "reason": f"冻结术语表已变更（当前 v{fg.get('version')}），本段需复核或重译",
        })

    # 受影响段不得继续作为可信翻译记忆
    tm = _c.load_tm(tm_project_id(state))
    dirty = False
    job_lang = state_target_lang(state)
    for i in stale:
        src = pairs[i]["source"]
        if _c.tm_discard(tm, src, job_lang):
            dirty = True
    if dirty:
        _c.save_tm(tm, tm_project_id(state))
    state["delivery_approved_by_human"] = False
    state["delivery_approval"] = None

    stats = state.setdefault("review_stats", {})
    stats["blocking"] = sum(1 for f in state["findings"]
                            if f["severity"] == "blocking")
    stats["actionable"] = sum(1 for f in state["findings"]
                              if f["severity"] == "actionable")
    stats["informational"] = sum(1 for f in state["findings"]
                                 if f["severity"] == "informational")
    state["has_blocking"] = stats["blocking"] > 0
    state["delivery_status"] = _delivery.compute_delivery_status(state)
    if job_id:
        from folith import academic_writer
        segment_ids = [
            _finalization.segment_id(job_id, index, pairs[index])
            for index in stale if 0 <= index < len(pairs)
        ]
        propagated = academic_writer.propagate_artifact_staleness(
            state, input_segment_ids=segment_ids)
        enriched = dict(state)
        enriched["_finalization_artifacts"] = _c._finalization_artifacts(job_id)
        state["dependency_impact"] = _finalization.build_dependency_impact(
            enriched, job_id, stale, "冻结术语表变化")
        affected_cases = [name.split(":", 1)[1] for name in propagated
                          if str(name).startswith("case:")]
        _finalization.mark_case_reviews_stale(
            state, affected_cases, "冻结术语表变化影响了本案例绑定段落")
        if propagated:
            academic = state.setdefault("academic_state", {})
            if any(name in propagated for name in {"sections", "report", "validation", "review"}):
                state["p3_done"] = False
                state["p3_md"] = ""
                state["p3_sections"] = []
                academic["status"] = "stale"
            _c._reset_final_qa(state, "冻结术语表变化；相关案例与学术下游需重新检查")
            _c._invalidate_final_delivery_state(state)
    return state, stale


@_serialized_job_mutation
def set_glossary_entry_status(job_id, entry_ids, status):
    """批量修改术语状态（candidate/provisional/locked/rejected）。"""
    if status not in ("candidate", "provisional", "locked", "rejected"):
        raise ValueError(f"非法状态：{status}")
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    ids = set(entry_ids or [])
    for e in state.get("glossary") or []:
        if e.get("id") in ids:
            e["status"] = status
    _c.save_job_state(job_id, state)
    return state


def review_knowledge_candidate(job_id, candidate_id, decision, actor="user"):
    """Apply an explicit human decision to one persisted knowledge candidate.

    This preserves the existing task-level mutation contract: ``project_term``
    locks the candidate in the task glossary and freezes its version. Callers
    that need cross-task reuse must then explicitly pass that confirmed state
    through ``promote_job_to_project`` (the Memory gate). There is intentionally
    no global glossary store.
    """
    allowed = {"project_term", "task_only", "rejected"}
    if decision not in allowed:
        raise ValueError(f"非法知识候选决策：{decision}")
    state = _c.load_job_state(job_id)
    if state is None:
        return None, False, "任务不存在"
    actor = str(actor or "").strip()
    if not actor:
        return state, False, "人工确认必须记录 actor"
    candidate = next((item for item in state.get("knowledge_candidates") or []
                      if _knowledge.candidate_id(item) == str(candidate_id)), None)
    if candidate is None:
        return state, False, "找不到待确认词条"
    if candidate.get("decision"):
        return state, False, "该词条已经处理过"

    context = _knowledge.candidate_context(candidate, state)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous_status = str(candidate.get("status") or "emergent_candidate")

    def record(action, note, event):
        candidate["decision"] = decision
        candidate["decision_at"] = timestamp
        candidate["decision_by"] = actor
        candidate["decision_note"] = note
        candidate["status"] = {
            "project_term": "promoted_project_term",
            "task_only": "accepted_task",
            "rejected": "rejected",
        }[decision]
        state["translation_continuity"] = [
            dict(item) for item in state.get("knowledge_candidates") or []
            if isinstance(item, dict)
        ]
        state.setdefault("knowledge_events", []).append({
            "type": "human_candidate_decision",
            "candidate_id": context["candidate_id"],
            "source": context["source"],
            "observed_target": context["proposed_target"],
            "decision": decision,
            "scope": "document" if decision == "project_term" else "task",
            "timestamp": timestamp,
            "actor": actor,
            "actor_type": "human",
            "note": note,
            "previous_status": previous_status,
            "new_status": candidate["status"],
            **event,
        })
        state.setdefault("human_actions", []).append({
            "action": action,
            "finding_id": f"knowledge:{context['candidate_id']}",
            "note": note,
            "timestamp": timestamp,
            "actor": actor,
            "actor_type": "human",
            "previous_status": previous_status,
            "new_status": candidate["status"],
        })

    if decision == "project_term":
        if context["conflicts"]:
            return state, False, "与现有项目术语冲突，未覆盖现有术语"
        entries = normalize_glossary(
            state.get("glossary") or (state.get("glossary_frozen") or {}).get("entries") or [])
        matching = [entry for entry in entries
                    if entry.get("source", "").casefold() == context["source"].casefold()]
        target = context["proposed_target"]
        changed = False
        if matching:
            entry = matching[0]
            current = str(entry.get("preferred") or entry.get("target") or "").strip()
            if current.casefold() != target.casefold():
                return state, False, "与现有术语译名冲突，未覆盖现有术语"
            if entry.get("status") != "locked":
                entry["status"] = "locked"
                entry["preferred"] = target
                entry["target"] = target
                changed = True
            entry_id = entry.get("id")
        else:
            entry = _models.normalize_glossary_entry({
                "source": context["source"],
                "proposed_target": target,
                "target": target,
                "preferred": target,
                "behavior": "translate",
                "status": "locked",
                "scope": "document",
                "occurrences": context["occurrences"],
                "note": "由人工从翻译流知识候选提升为项目术语",
                "evidence": [{
                    "evidence_type": "user",
                    "source_name": "译页知识候选",
                    "note": f"来自第 {(context['first_observed_segment'] + 1) if context['first_observed_segment'] is not None else '?'} 段的人工确认",
                    "quote": context["source_context"],
                    "url": "",
                }],
            })
            if entry is None:
                return state, False, "词条内容无效，未加入项目术语"
            entries.append(entry)
            entry_id = entry.get("id")
            changed = True
        candidate["promotion_entry_id"] = entry_id
        record("knowledge_project_term", "人工确认并加入项目术语；术语版本将更新", {
            "entry_id": entry_id,
        })
        state["glossary"] = entries
        _c.save_job_state(job_id, state)
        if changed or not state.get("glossary_frozen"):
            state = freeze_glossary(job_id, entries=entries, frozen_by=actor)
        return state, True, "已加入项目术语，并通过术语版本冻结流程保存"

    if decision == "task_only":
        record("knowledge_task_only", "人工确认仅在本任务采用，不加入项目术语", {})
        _c.save_job_state(job_id, state)
        return state, True, "已记录为仅本任务采用"

    record("knowledge_rejected", "人工拒绝该知识候选", {})
    _c.save_job_state(job_id, state)
    return state, True, "已拒绝该知识候选"


@_serialized_job_mutation
def confirm_translation_style_rule(
    job_id, rule, actor, *, actor_type, note="", source_finding_id="",
):
    """Explicitly promote one human-confirmed style rule into Project Memory."""
    actor = str(actor or "").strip()
    text = str(rule or "").strip()
    if actor_type != "human" or not actor:
        raise ValueError("only an identified human may confirm style knowledge")
    if not text:
        raise ValueError("confirmed style rule must not be empty")
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    rule_id = _models.stable_id(text.casefold(), prefix="s")
    existing = next((item for item in state.get("confirmed_style_rules") or []
                     if isinstance(item, dict) and item.get("rule_id") == rule_id), None)
    if existing is not None:
        return state, existing
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "rule_id": rule_id,
        "rule": text,
        "status": "confirmed",
        "confirmed_by": actor,
        "confirmed_at": timestamp,
        "note": str(note or "").strip(),
        "source_finding_id": str(source_finding_id or "").strip(),
    }
    state.setdefault("confirmed_style_rules", []).append(record)
    state.setdefault("human_actions", []).append({
        "finding_id": record["source_finding_id"] or f"style:{rule_id}",
        "action": "confirm_style_rule",
        "actor": actor,
        "actor_type": "human",
        "timestamp": timestamp,
        "note": record["note"] or text,
        "previous_status": "candidate",
        "new_status": "confirmed",
        "rule_id": rule_id,
    })
    excluded = _c._translation_review_finding(state, record["source_finding_id"])
    excluded_segment = excluded.get("segment_id") if excluded else None
    indexes = [index for index in range(len(state.get("pairs") or []))
               if index != excluded_segment]
    _c._invalidate_translation_reviews(
        state, indexes, "confirmed style knowledge changed review dependencies")
    if indexes:
        _c._invalidate_final_delivery_state(state)
    _c.save_job_state(job_id, state)
    return state, record


@_serialized_job_mutation
def freeze_glossary(job_id, entries=None, frozen_by="user"):
    """冻结术语表：生成新版本 + 确定性 glossary_hash。

    修改后再次冻结 -> 新版本追加到 glossary_versions，不悄悄覆盖旧冻结状态。
    相同 canonical 内容再次冻结 -> 不创建新版本（幂等）。
    冻结新版本后立即对已翻译段落执行术语依赖失效（stale 标记 + TM 清除）。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    was_final = state.get("delivery_status") == "final"
    norm = normalize_glossary(entries if entries is not None
                              else state.get("glossary") or [])
    versions = state.get("glossary_versions") or []
    if versions and isinstance(versions[-1], dict) \
            and _models.entries_equal(versions[-1].get("entries") or [], norm):
        # 相同内容：不创建新版本（决策 A），保持原 frozen 快照
        state["glossary"] = norm
        state["glossary_frozen"] = versions[-1]
        state["stage"] = "GLOSSARY_FROZEN"
        _c.save_job_state(job_id, state)
        return state
    source_hash = ""
    src = _c.job_dir(job_id) / "source.bin"
    if src.is_file():
        source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    version = len(versions) + 1
    frozen = {
        "version": version,
        "source_hash": source_hash,
        "entries": norm,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "glossary_hash": _models.glossary_hash(norm),
        "frozen_by": frozen_by,
    }
    state["glossary"] = norm
    state["glossary_frozen"] = frozen
    versions.append(frozen)
    state["glossary_versions"] = versions
    state["stage"] = "GLOSSARY_FROZEN"
    if state.get("delivery_status") not in ("approved", "final"):
        state["delivery_status"] = "draft"
    # 术语决策变化 -> 立即失效受影响段落
    state, stale = _apply_glossary_staleness(state, job_id)
    if was_final and not stale:
        _c._invalidate_final_delivery_state(state)
    _c.save_job_state(job_id, state)
    return state


@_serialized_job_mutation
def unfreeze_glossary(job_id):
    """返回修改：解除冻结（翻译开始前），回到 TERMS_PREPARED；旧冻结版本保留。"""
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    if state.get("p2_done"):
        # 翻译已开始：不允许解除冻结，只能生成新版本（见 freeze_glossary）
        return state
    state["glossary_frozen"] = None
    state["stage"] = "TERMS_PREPARED"
    if state.get("delivery_status") not in ("approved", "final"):
        state["delivery_status"] = "draft"
    _c.save_job_state(job_id, state)
    return state


@_serialized_job_mutation
def save_document_profile(job_id, profile):
    """人工填写/修改文档画像后保存。"""
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    normalized = _models.normalize_document_profile(profile)
    changed = normalized != _models.normalize_document_profile(
        state.get("document_profile"))
    state["document_profile"] = normalized
    state["profile_done"] = True
    if changed and state.get("p2_done"):
        _c._invalidate_translation_reviews(
            state, list(range(len(state.get("pairs") or []))),
            "document profile changed; review context must be refreshed")
        _c._invalidate_final_delivery_state(state)
    _c.save_job_state(job_id, state)
    return state


@_serialized_job_mutation
def set_entity_translation(job_id, source_form, target, *, entity_type="other_proper_noun",
                           locked=True, actor="user", note=""):
    """Persist a human entity choice; it outranks generated continuity hints."""
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    registry = _entity_registry.EntityRegistry(state.get("entity_registry") or [])
    record = (registry.lock if locked else registry.accept)(
        source_form, target, entity_type=entity_type, note=note)
    state["entity_registry"] = registry.to_list()
    state.setdefault("human_actions", []).append({
        "finding_id": f"entity:{record.get('id') or source_form}",
        "action": "entity_locked" if locked else "entity_accepted",
        "note": note or f"人工确认实体译名：{source_form} -> {target}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
    })
    if state.get("p2_done"):
        state["targeted_final_review"] = {
            "status": "not_run", "segment_ids": [],
            "reviewed_segment_ids": [], "failed_segment_ids": [],
        }
    if (state.get("delivery_status") in ("approved", "final")
            or state.get("delivery_approved_by_human")):
        _c._invalidate_final_delivery_state(state)
    _c.save_job_state(job_id, state)
    return state


def entity_consistency_queue(state):
    return _entity_registry.EntityRegistry(
        state.get("entity_registry") or []).consistency_findings(state.get("pairs") or [])


def preview_entity_repair(state, source_form, target):
    from folith import consistency
    record = _entity_registry.EntityRegistry(state.get("entity_registry") or [])._find(source_form)
    if record is None:
        raise ValueError("找不到该实体")
    return consistency.preview(record, state.get("pairs") or [], target)


@_serialized_job_mutation
def apply_entity_repair(job_id, source_form, target, preview, *, actor="user"):
    """Apply only the exact before/after edits the operator reviewed."""
    from folith import consistency
    if _c._job_execution_alive(job_id):
        raise RuntimeError("任务仍在执行，请等待后台处理结束")
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError("找不到任务")
    current = preview_entity_repair(state, source_form, target)
    if current != preview:
        raise ValueError("译文已变化，请重新查看修改预览")
    registry = _entity_registry.EntityRegistry(state.get("entity_registry") or [])
    record = registry._find(source_form)
    record["accepted_variants"] = []
    registry.lock(source_form, consistency.spelling(target), entity_type=record["entity_type"])
    state["entity_registry"] = registry.to_list()
    indexes = []
    for change in current:
        index = change["index"]
        pair = state["pairs"][index]
        if _c.is_excluded(pair):
            raise ValueError("预览包含已排除的段落，请重新查看预览")
        pair.setdefault("_translation_edit_restore", {
            key: pair.get(key) for key in ("target", "reviewed", "review_status", "target_provenance", "from_tm")})
        pair.update(target=change["after"], human_edited=True, reviewed=False,
                    review_status="not_reviewed", target_provenance="human_edit", from_tm=False)
        for key in ("accepted_target", "human_accepted", "accepted_by_human"):
            pair.pop(key, None)
        indexes.append(index)
    if indexes:
        _c._recount_reviewed_segments(state)
        _c._mark_translation_truth_changed(job_id, state, indexes,
            f"人工统一实体译名：{source_form} → {target}", actor=actor, action="entity_consistency_repair")
    else:
        _c._invalidate_final_delivery_state(state)
    _refresh_entity_consistency(state, actor=actor)
    _c._recheck_delivery_invariants_for_segments(state, indexes, actor=actor)
    _c.save_job_state(job_id, state)
    return state, indexes


@_serialized_job_mutation
def accept_entity_variants(job_id, source_form, *, actor="user"):
    from folith import consistency
    if _c._job_execution_alive(job_id):
        raise RuntimeError("任务仍在执行，请等待后台处理结束")
    state = _c.load_job_state(job_id)
    registry = _entity_registry.EntityRegistry(state.get("entity_registry") or [])
    record = registry._find(source_form)
    if record is None:
        raise ValueError("找不到该实体")
    record["accepted_variants"] = sorted({
        value for row in consistency.current_occurrences(record, state.get("pairs") or [])
        for value in row["variants"]})
    record["status"] = "human_accepted"
    state["entity_registry"] = registry.to_list()
    state.setdefault("human_actions", []).append({
        "action": "entity_variants_accepted", "finding_id": f"entity:{record['id']}",
        "note": f"确认语境允许这些译名：{record['accepted_variants']}",
        "timestamp": _finalization.now_iso(), "actor": actor})
    _refresh_entity_consistency(state, actor=actor)
    _c._recheck_delivery_invariants_for_segments(state, [], actor=actor)
    _c.save_job_state(job_id, state)
    return state


def _refresh_entity_consistency(state, *, actor="system"):
    active = {item["source"] for item in entity_consistency_queue(state)}
    for finding in state.get("findings") or []:
        if finding.get("type") == "entity_conflict" and not finding.get("resolved") \
                and finding.get("source") not in active:
            finding["resolved"] = True
            finding["resolution"] = {"action": "entity_consistency_rechecked",
                "note": "当前译文已统一或语境变体已人工确认", "actor": actor,
                "timestamp": _finalization.now_iso()}


def write_profile_artifacts(job_id, document_profile, style_profile):
    """把 Step 01 的画像产物落盘为版本化 artifact：
    document_profile.json / style_profile.json（含 style_profile_id 哈希）。
    返回写入的 style_profile_id，失败返回 None。
    """
    try:
        from folith.style_profile import style_profile_id
        job_root = _c.job_dir(job_id)
        job_root.mkdir(parents=True, exist_ok=True)
        (job_root / "document_profile.json").write_text(
            json.dumps(_models.normalize_document_profile(document_profile),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        profile_id = style_profile_id(style_profile or {})
        artifact = dict(style_profile or {})
        artifact["style_profile_id"] = profile_id
        (job_root / "style_profile.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return profile_id
    except Exception:  # noqa: BLE001 - artifact 落盘失败不影响主流程
        return None


@_serialized_job_mutation
def bypass_freeze(job_id, frozen_by="user"):
    """快速模式跳过人工冻结：允许以 provisional 术语直接翻译（记录审计标记）。"""
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    state["quality_bypass"] = True
    state.setdefault("human_actions", []).append({
        "action": "bypass_freeze",
        "note": "快速模式：跳过人工术语冻结，以 provisional 术语直接翻译",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": frozen_by,
    })
    _c.save_job_state(job_id, state)
    return state
