"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import json
from datetime import datetime
from datetime import timezone
from folith import finalization as _finalization
from folith import translation_evidence as _translation_evidence
from folith.engine.locks import _serialized_job_mutation
from folith.engine.source_quality import _source_quality_dispositions
from folith.engine._link import core as _c
from folith.storage.atomic_file import replace_file


def _reconcile_translation_failures(state):
    """Keep failure history while recording recovery proved by current pairs."""
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    for failure in state.get("translation_failures") or []:
        if failure.get("recovered"):
            continue
        offset, count = failure.get("offset"), failure.get("segment_count")
        if not isinstance(offset, int) or not isinstance(count, int) or count <= 0 or offset < 0:
            continue
        affected = pairs[offset:offset + count]
        if len(affected) != count:
            continue
        if all(pair.get("target", "").strip()
               and pair.get("target_provenance") in {"generated", "human_edit", "reviewed", "tm_approved"}
               and offset + local < len(paras)
               and pair.get("source", "").replace('\n', ' ') == str(paras[offset + local]).replace('\n', ' ')
               for local, pair in enumerate(affected)):
            failure["recovered"] = True
            failure["recovered_at"] = _finalization.now_iso()
            failure["recovery_evidence"] = "current_translation_present"


def translation_drafts_path(job_id):
    return _c.job_dir(job_id) / _c.TRANSLATION_DRAFTS_FILE


def load_translation_drafts(job_id):
    """读回未保存草稿，返回 `{segment_id: {"text", "baseline", "updated_at"}}`。

    读不到、格式不对、文件损坏一律返回空字典：草稿是"尽力而为"的兜底，
    它的读取失败绝不能让工作台打不开。
    """
    path = translation_drafts_path(job_id)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 草稿损坏按"没有草稿"处理
        return {}
    if not isinstance(raw, dict):
        return {}
    items = raw.get("drafts")
    if not isinstance(items, list):
        return {}
    drafts = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "")
        if not segment_id:
            continue
        drafts[segment_id] = {
            "segment_id": segment_id,
            "text": str(item.get("text") or ""),
            "baseline": str(item.get("baseline") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
    return drafts


def save_translation_drafts(job_id, drafts):
    """原子写入草稿文件。没有草稿时**删除文件**，不留空壳。"""
    d = _c.job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / _c.TRANSLATION_DRAFTS_FILE
    items = []
    for segment_id, record in (drafts or {}).items():
        if not isinstance(record, dict):
            continue
        items.append({
            "segment_id": str(segment_id),
            "text": str(record.get("text") or ""),
            "baseline": str(record.get("baseline") or ""),
            "updated_at": str(record.get("updated_at")
                              or datetime.now(timezone.utc).isoformat()),
        })
    if not items:
        path.unlink(missing_ok=True)
        return
    items.sort(key=lambda item: item["segment_id"])
    payload = {
        "version": _c.TRANSLATION_DRAFTS_VERSION,
        "job_id": str(job_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "drafts": items,
    }
    tmp = d / (_c.TRANSLATION_DRAFTS_FILE + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def clear_translation_drafts(job_id):
    """丢弃本任务全部未保存草稿（文件层面）。"""
    translation_drafts_path(job_id).unlink(missing_ok=True)


def _recount_reviewed_segments(state):
    """Keep the segment review count in sync with the canonical pair records."""
    state.setdefault("review_stats", {})["reviewed_segments"] = sum(
        bool(pair.get("reviewed")) for pair in state.get("pairs") or [])


def segment_uid(pair):
    """一段的稳定身份；没有时返回空串（调用方负责退化到索引）。"""
    return str((pair or {}).get(_c.SEGMENT_UID_FIELD) or "")


def is_excluded(pair):
    """这一段是否被用户显式排除在翻译与交付之外。"""
    return bool((pair or {}).get("excluded"))


def active_pair_indexes(state):
    """参与翻译、审校与交付的段落索引（排除项不属于任何一项）。"""
    return [index for index, pair in enumerate((state or {}).get("pairs") or [])
            if isinstance(pair, dict) and not is_excluded(pair)]


def next_segment_uid(state, job_id=""):
    """分配一个新的稳定身份。单调递增，撤销不回退（已用过的身份不再复用）。"""
    seq = int(state.get(_c._UID_SEQ_FIELD) or 0)
    state[_c._UID_SEQ_FIELD] = seq + 1
    return f"seg-{job_id}-u{seq:06d}" if job_id else f"u{seq:06d}"


def assign_missing_segment_uids(state, *, job_id=""):
    """给缺少稳定身份的段落补齐身份，返回新分配的数量。

    旧任务（v0.4 之前）没有这个字段，补齐动作发生在**第一次结构编辑或人工保存**
    上，因此普通渲染不会因为"读了一次状态"就改写磁盘。

    初始身份刻意沿用导出资产既有的格式 `seg-<job_id>-<index>`：升级瞬间不会发生
    身份漂移，浏览器里已经存在的选中态、编辑框与未保存草稿都继续有效。结构操作
    产生的新段落用 `seg-<job_id>-u<n>`，与索引格式不可能碰撞，且永不复用。
    """
    from folith.epub import bind_source_references
    bind_source_references(state)
    from folith.document_ir import attach_pair_spans
    attach_pair_spans(state)
    pairs = (state or {}).get("pairs")
    if not isinstance(pairs, list):
        return 0
    seq = int(state.get(_c._UID_SEQ_FIELD) or 0)
    seen = set()
    assigned = 0
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            continue
        uid = segment_uid(pair)
        if uid and uid not in seen:
            seen.add(uid)
            continue
        if job_id:
            pair[_c.SEGMENT_UID_FIELD] = f"seg-{job_id}-{index:04d}"
        else:
            pair[_c.SEGMENT_UID_FIELD] = f"u{seq:06d}"
            seq += 1
        seen.add(pair[_c.SEGMENT_UID_FIELD])
        assigned += 1
    state[_c._UID_SEQ_FIELD] = max(seq, len(pairs), len(seen))
    return assigned


def excluded_segment_records(state):
    """被排除段落的清单——导出、交付说明与「恢复」入口的唯一来源。

    排除采用"移出 pairs/paras + 在此留档"的模型：只要不在工作列表里，它就自然
    不进入翻译、不计入待完成数量、不参与审校门禁、不进导出；而原文与当时的译文
    在这里完整保留，所以恢复是精确还原而不是重新猜。
    """
    records = []
    for position, item in enumerate((state or {}).get("excluded_segments") or []):
        if not isinstance(item, dict):
            continue
        records.append({
            "excluded_id": str(item.get("excluded_id") or f"x{position:06d}"),
            "segment_index": item.get("index"),
            "source": str(item.get("source") or ""),
            "target": str(item.get("target") or ""),
            "reason": str(item.get("reason") or ""),
            "excluded_at": str(item.get("excluded_at") or ""),
            "excluded_by": str(item.get("excluded_by") or ""),
            "restorable": bool(item.get("pair") is not None
                               or str(item.get("source") or "").strip()),
        })
    # 兼容极早期状态：pair 上直接带 excluded 标记。
    for index, pair in enumerate((state or {}).get("pairs") or []):
        if not isinstance(pair, dict) or not is_excluded(pair):
            continue
        records.append({
            "excluded_id": segment_uid(pair) or f"index-{index}",
            "segment_index": index,
            "source": str(pair.get("source") or ""),
            "target": str(pair.get("target") or ""),
            "reason": str(pair.get("excluded_reason") or ""),
            "excluded_at": str(pair.get("excluded_at") or ""),
            "excluded_by": str(pair.get("excluded_by") or ""),
            "restorable": False,
        })
    return records


def excluded_segments_summary(state):
    """排除范围的只读摘要（交付页/导入报告共用，避免各处自己数一遍）。"""
    records = excluded_segment_records(state)
    working = len((state or {}).get("pairs") or []) \
        or len((state or {}).get("paras") or [])
    return {
        "count": len(records),
        "working_segments": working,
        "total_segments": working + len(records),
        "records": records,
    }


def excluded_segments_manifest(state):
    """交付包里如实说明"哪些内容没有进入译文文档"。"""
    records = excluded_segment_records(state)
    return {
        "version": 1,
        "rule": "被排除段落不进入译文的生成式输出，也不占用待完成与审校配额；"
                "原文与排除时的译文在此完整保留，可在工作台恢复。",
        "count": len(records),
        "records": records,
    }


def _excluded_segments_md(manifest):
    lines = [
        "# 已排除段落清单",
        "",
        f"- 排除数量：{manifest.get('count', 0)}",
        f"- 规则：{manifest.get('rule', '')}",
        "",
        "| 原位置 | 排除原因 | 原文 |",
        "| --- | --- | --- |",
    ]
    for record in manifest.get("records") or []:
        index = record.get("segment_index")
        position = f"第 {int(index) + 1} 段" if isinstance(index, int) else "—"
        source = " ".join(str(record.get("source") or "").split())
        if len(source) > 160:
            source = source[:159] + "…"
        reason = str(record.get("reason") or "未填写")
        lines.append(f"| {position} | {reason} | {source or '（空）'} |")
    lines.append("")
    return "\n".join(lines)


def _segment_source_list(state):
    """当前的段落原文序列（有译文时以 pairs 为准，否则用 paras）。

    排除/恢复需要用原文做锚点：索引会随结构操作漂移，原文文本不会。
    """
    pairs = (state or {}).get("pairs") or []
    if pairs:
        return [str((pair or {}).get("source") or "") for pair in pairs]
    return [str(item or "") for item in (state or {}).get("paras") or []]


def _excluded_segment_record(state, index, *, reason, actor, source="",
                             target="", pair=None, in_pairs=False,
                             before_source="", after_source=""):
    """构造一条 ``excluded_segments`` 记录。

    人工「排除本段」与系统判定的不可翻译段落共用同一份记录形状：交付清单、
    恢复入口与撤销日志都按它读取，两条路径不能各自长出一套字段。
    """
    excluded_id_value = f"x{int(state.get('exclusion_seq') or 0):06d}"
    state["exclusion_seq"] = int(state.get("exclusion_seq") or 0) + 1
    return {
        "excluded_id": excluded_id_value,
        "index": index,
        "source": str(source or ""),
        "target": str(target or ""),
        "pair": json.loads(json.dumps(pair)) if isinstance(pair, dict) else None,
        "in_pairs": bool(in_pairs),
        "before_source": str(before_source or ""),
        "after_source": str(after_source or ""),
        "reason": str(reason or "").strip(),
        "excluded_at": _finalization.now_iso(),
        "excluded_by": str(actor or "user"),
    }


def _shift_paragraph_ranges(paragraph_ranges, removed_index):
    """把被移出的段落从 ``segmentation.paragraph_ranges`` 中剔除。

    段落区间是「清洗后段落 → 翻译单元」的位置契约；少了一段之后，后面每一段都
    要前移一位，包含它的区间则收窄一格。留着旧区间会让原文血缘审计把门禁结论
    落到错误的段落上。
    """
    shifted = []
    for row in paragraph_ranges or []:
        if not isinstance(row, dict):
            continue
        start, end = row.get("start"), row.get("end")
        if not (isinstance(start, int) and isinstance(end, int)):
            shifted.append(dict(row))
            continue
        if start > removed_index:
            start -= 1
        if end > removed_index:
            end -= 1
        shifted.append({**row, "start": start, "end": max(start, end)})
    return shifted


def _drop_segment_at(state, index):
    """从工作列表中移出一个段落，并同步它的位置型派生产物。"""
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    if pairs:
        pairs.pop(index)
    if 0 <= index < len(paras):
        paras.pop(index)
    for key in ("segment_source_spans", "segment_source_marks"):
        values = state.get(key)
        if isinstance(values, list) and 0 <= index < len(values):
            values.pop(index)
    segmentation = state.get("segmentation")
    if isinstance(segmentation, dict) and segmentation.get("paragraph_ranges"):
        segmentation["paragraph_ranges"] = _shift_paragraph_ranges(
            segmentation["paragraph_ranges"], index)
        segmentation["output_segment_count"] = len(paras)


def _non_translatable_exclusions(state, *, paragraphs=None):
    """系统判定的「无需翻译」段落，按索引从大到小返回。

    只做判定，不改状态；调用方负责在**位置契约尚未建成**时应用，避免让已存在
    的译文/审校按错位索引漂移。判定依据全部来自既有的段落元数据：IR 区块类型与
    版面几何、以及既有 source-quality 语义谓词。
    """
    report = _c._audit_source_quality_for_state(state, apply_admissions=False)
    outcomes = _source_quality_dispositions(state, report, paragraphs)
    return sorted(
        (index for index, outcome in outcomes.items()
         if outcome.get("disposition") == "non_translatable_structural"),
        reverse=True,
    ), outcomes


def _auto_exclude_non_translatable_segments(job_id, state):
    """把系统已证明不可翻译的段落移出工作列表。

    复用既有「排除」契约（``state["excluded_segments"]`` + 移出 pairs/paras），
    因此这些段落不会进入翻译、不写翻译记忆、不计入审校与质量指标、不需要审校，
    交付时以「已排除」如实登记；但它们**不会**被标成 translated/reviewed/approved
    ——排除不是审校通过。
    """
    pairs = state.get("pairs") or []
    if pairs:
        # The positional translation contract already exists; a silent index
        # shift here would misalign persisted findings, review evidence and
        # delivery assets.  Exclusion is only decided while the list is built.
        return []
    indexes, outcomes = _non_translatable_exclusions(state)
    if not indexes:
        return []
    paragraphs = state.get("paras") or []
    sources = _segment_source_list(state)
    records = []
    for index in indexes:
        if not 0 <= index < len(paragraphs):
            continue
        outcome = outcomes.get(index) or {}
        evidence = "、".join(str(item) for item in outcome.get("evidence") or [])
        record = _excluded_segment_record(
            state, index,
            reason=("系统判定为无需翻译的结构性片段"
                    f"（{evidence or 'non_translatable'}）"),
            actor="system:source_quality",
            source=str(paragraphs[index] or ""),
            target="",
            pair=None, in_pairs=False,
            before_source=sources[index - 1] if index > 0 else "",
            after_source=(sources[index + 1]
                          if index + 1 < len(sources) else ""),
        )
        _drop_segment_at(state, index)
        records.append(record)
    state["excluded_segments"] = list(
        state.get("excluded_segments") or []) + records
    audit = list(state.get("source_quality_auto_exclusions") or [])
    audit.extend({
        "index": record["index"],
        "excluded_id": record["excluded_id"],
        "evidence": list((outcomes.get(record["index"]) or {}).get("evidence") or []),
        "block_type": (outcomes.get(record["index"]) or {}).get("block_type") or "",
        "excluded_at": record["excluded_at"],
        "excluded_by": record["excluded_by"],
    } for record in records)
    state["source_quality_auto_exclusions"] = audit
    state["source_quality_gate"] = _c._audit_source_quality_for_state(state)
    for record in records:
        _c._append_runtime_technical_log(
            job_id,
            "source quality: auto-excluded non-translatable segment "
            f"#{record['index']} ({record['excluded_id']}) "
            f"reason={record['reason']}")
    return records


def _exclusion_restore_index(state, record):
    """被排除段落应当插回的位置：优先后锚点，其次前锚点，最后退回原索引。"""
    sources = _segment_source_list(state)
    after = str((record or {}).get("after_source") or "")
    before = str((record or {}).get("before_source") or "")
    if after:
        for position, text in enumerate(sources):
            if text == after:
                return position
    if before:
        for position, text in enumerate(sources):
            if text == before:
                return position + 1
    return max(0, min(int((record or {}).get("index") or 0), len(sources)))


def can_undo_translation_segment_structure(state):
    """最近一次结构操作是否可撤销（供界面决定是否显示入口）。"""
    history = [record for record in (state or {}).get("segment_structure_history") or []
               if isinstance(record, dict)]
    if not history:
        return {"available": False, "label": "", "operation": "", "index": None}
    record = history[-1]
    operation = str(record.get("operation") or "")
    return {
        "available": isinstance(record.get("restore_pairs"), list),
        "label": _c.SEGMENT_STRUCTURE_OPERATION_LABELS.get(operation, "段落结构已更新"),
        "operation": operation,
        "index": record.get("index"),
    }


def translation_terms_for_pair(state, pair):
    """Resolve only glossary entries explicitly attached to one pair."""
    ids = {str(item) for item in pair.get("glossary_entry_ids") or []}
    entries = state.get("glossary") or []
    if not entries:
        frozen = state.get("glossary_frozen") or {}
        entries = frozen.get("entries") or [] if isinstance(frozen, dict) else []
    if not entries:
        versions = state.get("glossary_versions") or []
        if versions and isinstance(versions[-1], dict):
            entries = versions[-1].get("entries") or []
    if not ids or not isinstance(entries, list):
        return []
    terms = []
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("id") or "") not in ids:
            continue
        source = str(entry.get("source") or "")
        if source:
            terms.append((source, str(entry.get("preferred") or entry.get("target") or "—"),
                          "项目术语"))
    return terms[:8]


def translation_visible_indexes(state, search="", status_filter="全部",
                               filter_terms=False, filter_edited=False,
                               filter_issues=False, filter_tm=False,
                               issue_indexes=None):
    """Return visible pair indexes without mutating the loaded state."""
    pairs = state.get("pairs") or []
    query = str(search or "").strip().casefold()
    issue_indexes = set(issue_indexes or [])
    visible = []
    for index, pair in enumerate(pairs):
        source = str(pair.get("source") or "")
        target = str(pair.get("target") or "")
        if query:
            paragraph_query = query.lstrip("#").strip()
            if paragraph_query.isdigit():
                if int(paragraph_query) != index + 1:
                    continue
            elif query not in f"{source}\n{target}".casefold():
                continue
        if status_filter == "待审" and pair.get("reviewed"):
            continue
        if status_filter == "已审校" and not pair.get("reviewed"):
            continue
        if filter_terms and not translation_terms_for_pair(state, pair):
            continue
        if filter_edited and not (pair.get("human_edited") or
                                  pair.get("source_human_edited")):
            continue
        if filter_tm and not pair.get("from_tm"):
            continue
        if filter_issues and index not in issue_indexes:
            continue
        visible.append(index)
    return visible


def _recheck_delivery_invariants_for_segments(state, indexes, *, actor="user"):
    """Refresh deterministic target blockers after a working-text edit.

    A target invariant describes the text that existed when it was recorded.
    When a user edits that target, a resolved old invariant must not continue
    to block delivery; if the same problem remains, the existing finding stays
    open and the current validation report remains authoritative.
    """
    from folith import delivery as _delivery

    indexes = {int(index) for index in indexes}
    report = _c.validate_delivery_translation_state(state)
    active = {
        (issue.get("segment_index"), issue.get("code"))
        for issue in report.get("issues") or []
    }
    for finding in state.setdefault("findings", []):
        if not isinstance(finding, dict) \
                or finding.get("type") != "delivery_invariant" \
                or finding.get("segment_index") not in indexes \
                or finding.get("resolved"):
            continue
        key = (finding.get("segment_index"), finding.get("invariant_code"))
        if key in active:
            continue
        finding["resolved"] = True
        finding["resolution"] = {
            "action": "target_rechecked",
            "note": "CURRENT_TRANSLATION 编辑后重新通过目标文本门禁",
            "timestamp": _finalization.now_iso(),
            "actor": actor,
        }
        _delivery.add_human_action(
            state, _delivery.finding_id(finding), "target_rechecked",
            "CURRENT_TRANSLATION 编辑后重新通过目标文本门禁", actor)
    _c._record_delivery_validation_findings(state, report)
    state["delivery_status"] = _delivery.compute_delivery_status(state)
    return report


@_serialized_job_mutation
def mutate_translation_segments(
    job_id, index, operation, *, source_text=None, source_a=None,
    source_b=None, target_a=None, target_b=None, exclude_reason=None,
    excluded_id=None, actor="user",
):
    """Apply a user initiated CAT segment-structure edit.

    ``pairs`` and ``paras`` are a positional contract throughout the pipeline:
    review findings, checkpoints and delivery assets all refer to the same
    segment index.  This is therefore deliberately one state-layer operation
    instead of a UI-only splice.  Structural edits are rejected while a worker
    is active, stale every existing review dependency, and persist a compact
    history record for auditability (including a content snapshot, so
    :func:`undo_translation_segment_structure` can restore the previous text).

    Supported operations:

    ``source_edit``
        Correct the source text in place while retaining the current target.
    ``split``
        Replace one segment with two explicitly supplied source/target pairs.
    ``merge``
        Join the selected segment with the following segment using a newline.
    ``insert``
        Add an empty manual segment immediately after ``index``.
    ``delete``
        Remove an empty manual segment.  Existing content cannot be deleted by
        this path, which prevents an accidental paragraph loss.
    ``exclude``
        Take a segment out of translation, review and delivery without touching
        its text.  The source text is always retained, so ``include`` restores
        it exactly.  This is the general entry point for OCR junk, page numbers
        and repeated running heads that no character-level repair can fix.
    ``include``
        Undo an exclusion.

    The returned value is the persisted state, matching the existing edit APIs.
    """
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")

    runtime = _c.get_job_runtime_status(job_id, state)
    runtime_status = str(runtime.get("status") or "")
    if runtime_status in _c.RUNTIME_ACTIVE_STATUSES:
        raise RuntimeError("任务正在运行，段落结构编辑将在任务完成或暂停后可用。")

    operation = str(operation or "").strip().lower()
    aliases = {
        "source": "source_edit", "edit_source": "source_edit",
        "split_segment": "split", "merge_segment": "merge",
        "insert_segment": "insert", "delete_segment": "delete",
        "exclude_segment": "exclude", "include_segment": "include",
        "restore_segment": "include",
    }
    operation = aliases.get(operation, operation)
    if operation not in {"source_edit", "split", "merge", "insert", "delete",
                         "exclude", "include"}:
        raise ValueError(f"不支持的段落结构操作：{operation or '—'}")

    # 结构操作是界面身份最容易漂移的地方：先给所有段落补齐稳定身份，再动列表。
    assign_missing_segment_uids(state, job_id=job_id)

    pairs = list(state.get("pairs") or [])
    paras = list(state.get("paras") or [])
    if pairs and len(paras) != len(pairs):
        # A completed translation must never be silently repaired here: a
        # mismatch means another invariant has already been violated and the
        # user needs the existing delivery gate to surface it.
        raise ValueError("当前任务的源文与双语段落数量不一致，无法安全编辑段落结构。")
    if not pairs and not paras:
        raise ValueError("当前任务还没有可编辑的段落。")
    if not pairs and operation not in {"exclude", "include"}:
        raise ValueError("当前任务还没有可编辑的译文段落。")
    if not all(isinstance(pair, dict) for pair in pairs):
        raise ValueError("当前任务包含无效的段落记录，无法安全编辑。")

    # 「恢复」的目标不在 pairs/paras 里（它正是被移出去的那一段），所以它的位置
    # 由原文锚点反推，而不是当作索引来校验。
    include_record = None
    if operation == "include":
        wanted = str(excluded_id or "")
        include_record = next(
            (item for item in state.get("excluded_segments") or []
             if isinstance(item, dict)
             and str(item.get("excluded_id") or "") == wanted), None)
        if include_record is None:
            raise ValueError("找不到要恢复的已排除段落。")
        index = _exclusion_restore_index(state, include_record)

    limit = len(pairs) if pairs else len(paras)
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("段落索引无效。")
    if operation == "include":
        if not 0 <= index <= limit:
            raise IndexError(f"段落索引超出范围：{index}")
    elif not 0 <= index < limit:
        raise IndexError(f"段落索引超出范围：{index}")

    # 操作前的内容快照：撤销需要的是**原文本身**，只记"操作名 + 位置"恢复不了
    # 被拆分/合并掉的内容。capture 是操作前被替换的切片；undo 描述撤销时要用
    # 多少次替换回去（insert 是插入、delete 是删除，形状与 capture 不同）。
    _SHAPES = {
        "source_edit": (index, 1, 1),
        "split": (index, 1, 2),
        "merge": (index, 2, 1),
        "insert": (index + 1, 0, 1),
        "delete": (index, 1, 0),
        # 排除把段落移出 pairs/paras；撤销要把同一条记录放回原位。
        "exclude": (index, 1, 0),
        # 恢复把已排除段落插回；撤销要再把它移出去，并还原排除记录。
        "include": (index, 0, 1),
    }
    undo_index, capture_span, undo_span = _SHAPES[operation]

    def normalized(value):
        return str(value or "").replace("\r\n", "\n").strip()

    def reset_trust(pair, source, target, *, initial_target=None,
                    source_edited=False, manual_segment=False):
        """Copy a pair while revoking trust tied to the old segmentation."""
        result = dict(pair or {})
        result["source"] = normalized(source)
        result["target"] = normalized(target)
        if initial_target is not None:
            result["initial_target"] = normalized(initial_target)
        else:
            result["initial_target"] = normalized(
                result.get("initial_target") or result.get("target"))
        result["reviewed"] = False
        result["review_status"] = "not_reviewed"
        result["from_tm"] = False
        result["target_provenance"] = "human_edit"
        result.pop("_translation_edit_restore", None)
        if source_edited:
            result["source_human_edited"] = True
            result["source_provenance"] = "human_edit"
        if manual_segment:
            result["manual_segment"] = True
            result["segment_origin"] = "manual"
        return result

    old_indexes = list(range(len(pairs)))
    # 撤销要恢复的是**内容**：只记"操作名 + 位置"恢复不了被拆分/合并掉的原句。
    # 快照只存操作前被替换的那一小段（0–2 条记录），50 条历史也不会影响体积。
    _restore_pairs = ([json.loads(json.dumps(item))
                       for item in pairs[undo_index:undo_index + capture_span]]
                      if capture_span else [])
    _restore_paras = ([str(item or "")
                       for item in paras[undo_index:undo_index + capture_span]]
                      if capture_span else [])
    reason_by_operation = {
        "source_edit": "人工修订了源文段落，原有译文审校需要重新确认",
        "split": "人工拆分了段落，原有译文审校需要重新确认",
        "merge": "人工合并了段落，原有译文审校需要重新确认",
        "insert": "人工插入了段落，原有译文审校需要重新确认",
        "delete": "人工删除了空段落，原有译文审校需要重新确认",
        "exclude": "人工排除了段落，交付范围发生变化，原有审校需要重新确认",
        "include": "人工恢复了被排除的段落，交付范围发生变化，原有审校需要重新确认",
    }
    reason = reason_by_operation[operation]

    # Stale the old ordinal dependencies before changing the list.  The normal
    # truth mutation below uses the new indexes; this pre-pass also covers a
    # deleted tail segment whose old index no longer exists afterward.  A source
    # text correction keeps list positions stable, so it only touches the
    # edited segment; every other operation changes the ordinal mapping.
    #
    # `exclude` / `include` 也属于"改变序号含义"的一类：移出或插回一段会让**它
    # 之后每一段**的索引都位移一位，而审校发现、检查点与交付资产全部按索引引用
    # 同一条记录。只把 `index` 判过期，会让后面那些段的旧发现静默指到别的段落上
    # ——这正是"结构操作后问题定位对不上"的根因。
    pre_stale_indexes = [index] if operation == "source_edit" else old_indexes
    _translation_evidence.mark_runtime_review_stale(
        state, pre_stale_indexes, reason, dependency_change=True)

    # 排除/恢复对导出清单的增量；撤销时按这两份增量反向操作。
    excluded_ids_added = []
    excluded_records_restored = []

    if operation == "source_edit":
        new_source = normalized(source_text)
        if not new_source:
            raise ValueError("原文不能为空；如需留白，请插入空段。")
        current = pairs[index]
        updated_pair = reset_trust(
            current, new_source, current.get("target") or "",
            initial_target=current.get("initial_target"), source_edited=True)
        pairs[index] = updated_pair
        paras[index] = new_source
        affected_hint = [index]
    elif operation == "split":
        left_source, right_source = normalized(source_a), normalized(source_b)
        if not left_source or not right_source:
            raise ValueError("拆分后的两段原文都不能为空。")
        current = pairs[index]
        left_target, right_target = normalized(target_a), normalized(target_b)
        original_ids = list(current.get("glossary_entry_ids") or [])
        left = reset_trust(
            current, left_source, left_target, initial_target=left_target,
            source_edited=True)
        right = reset_trust(
            current, right_source, right_target, initial_target=right_target,
            source_edited=True)
        # 拆出来的两段是**新**工作单元：必须拿到新身份，否则右半段会继承原段
        # 的身份，界面上的编辑框/选中态就会认错段落。
        left[_c.SEGMENT_UID_FIELD] = next_segment_uid(state, job_id)
        right[_c.SEGMENT_UID_FIELD] = next_segment_uid(state, job_id)
        if original_ids:
            left["glossary_entry_ids"] = list(original_ids)
            right["glossary_entry_ids"] = list(original_ids)
        pairs[index:index + 1] = [left, right]
        paras[index:index + 1] = [left_source, right_source]
        affected_hint = [index, index + 1]
    elif operation == "merge":
        if index + 1 >= len(pairs):
            raise ValueError("最后一段没有下一段可合并。")
        current, following = pairs[index], pairs[index + 1]
        merged_source = "\n".join(
            item for item in (normalized(current.get("source")),
                              normalized(following.get("source"))) if item)
        merged_target = "\n".join(
            item for item in (normalized(current.get("target")),
                              normalized(following.get("target"))) if item)
        merged_initial = "\n".join(
            item for item in (normalized(current.get("initial_target")),
                              normalized(following.get("initial_target"))) if item)
        merged = reset_trust(
            current, merged_source, merged_target,
            initial_target=merged_initial, source_edited=True)
        # 合并保留左段的身份（用户是在左段上发起操作的），右段身份随之消失。
        merged[_c.SEGMENT_UID_FIELD] = segment_uid(current) or next_segment_uid(state, job_id)
        from folith.epub import SOURCE_REFS
        if SOURCE_REFS in current and SOURCE_REFS in following:
            merged[SOURCE_REFS] = list(dict.fromkeys(
                current[SOURCE_REFS] + following[SOURCE_REFS]))
        if "source_spans" in current or "source_spans" in following:
            merged["source_spans"] = []
            for span in list(current.get("source_spans") or []) + list(following.get("source_spans") or []):
                if span not in merged["source_spans"]:
                    merged["source_spans"].append(dict(span))
        merged_ids = list(dict.fromkeys(
            list(current.get("glossary_entry_ids") or []) +
            list(following.get("glossary_entry_ids") or [])))
        if merged_ids:
            merged["glossary_entry_ids"] = merged_ids
        merged["merged_segment_count"] = int(
            current.get("merged_segment_count") or 1) + int(
                following.get("merged_segment_count") or 1)
        pairs[index:index + 2] = [merged]
        paras[index:index + 2] = [merged_source]
        affected_hint = [index]
    elif operation == "insert":
        inserted = reset_trust(
            {}, "", "", initial_target="", manual_segment=True)
        from folith.epub import SOURCE_REFS
        if SOURCE_REFS in pairs[index]:
            inserted[SOURCE_REFS] = list(pairs[index][SOURCE_REFS])
        if state.get("segment_source_spans"):
            inserted["source_spans"] = []
        inserted[_c.SEGMENT_UID_FIELD] = next_segment_uid(state, job_id)
        pairs.insert(index + 1, inserted)
        paras.insert(index + 1, "")
        affected_hint = [index + 1]
    elif operation == "exclude":
        # 「排除」把这一段整体移出 pairs/paras：不进入翻译、不计入待完成数量、
        # 不参与审校门禁、不进导出。原文（连同当时的译文）完整存进
        # `state["excluded_segments"]`，因此恢复是精确的、不是重新猜。
        sources = _segment_source_list(state)
        source_value = str(paras[index] or "")
        target_value = str(pairs[index].get("target") or "") if pairs else ""
        if not normalized(source_value) and not normalized(target_value):
            raise ValueError("该段落没有内容，无需排除；如需清理空段请使用「删除空段」。")
        excluded_record = _excluded_segment_record(
            state, index,
            reason=exclude_reason, actor=actor,
            source=source_value, target=target_value,
            pair=(pairs[index] if pairs else None), in_pairs=bool(pairs),
            before_source=sources[index - 1] if index > 0 else "",
            after_source=(sources[index + 1]
                          if index + 1 < len(sources) else ""),
        )
        excluded_id_value = excluded_record["excluded_id"]
        state["excluded_segments"] = list(
            state.get("excluded_segments") or []) + [excluded_record]
        excluded_ids_added = [excluded_id_value]
        if pairs:
            pairs.pop(index)
        paras.pop(index)
        affected_hint = [max(0, min(index, len(pairs) - 1))] if pairs else []
    elif operation == "include":
        excluded_record = dict(include_record)
        state["excluded_segments"] = [
            item for item in state.get("excluded_segments") or []
            if not (isinstance(item, dict)
                    and str(item.get("excluded_id") or "")
                    == str(excluded_record.get("excluded_id") or ""))
        ]
        # 排除发生在翻译之前时没有 pair 记录，此时只需要把原文插回 paras。
        restored_pair = excluded_record.get("pair")
        if pairs and isinstance(restored_pair, dict):
            # 内容原样回来，但**不**连带把旧的"已审校"标记也一起点亮：它的审校
            # 依赖在排除那一刻已经被判过期，重新显示"已审校"会是假绿（撤销路径
            # 出于同样的理由在 `undo_translation_segment_structure` 里清标记）。
            restored_pair = json.loads(json.dumps(restored_pair))
            restored_pair["reviewed"] = False
            restored_pair["review_status"] = "not_reviewed"
            restored_pair.pop("accepted_target", None)
            restored_pair.pop("human_accepted", None)
            restored_pair.pop("accepted_by_human", None)
            pairs.insert(index, restored_pair)
        paras.insert(index, str(excluded_record.get("source") or ""))
        affected_hint = [index]
        excluded_records_restored = [excluded_record]
    else:  # delete
        candidate = pairs[index]
        if not candidate.get("manual_segment"):
            raise ValueError("只能删除手动插入且仍为空的段落；"
                             "导入内容请使用「排除本段」。")
        if normalized(candidate.get("source")) or normalized(candidate.get("target")):
            raise ValueError("该段落已有内容，请先清空原文和译文后再删除。")
        pairs.pop(index)
        paras.pop(index)
        affected_hint = [max(0, min(index, len(pairs) - 1))] if pairs else []

    state["pairs"] = pairs
    state["paras"] = paras
    # Splitting, merging, inserting or deleting changes the meaning of every
    # following ordinal.  Revoke their trust together so no old review badge or
    # TM reuse survives under a different source/target pairing.  A plain
    # source correction only resets the pair already rebuilt above, and an
    # exclusion changes the delivery scope without touching other segments.
    if operation not in {"source_edit", "exclude", "include"}:
        for pair in pairs:
            pair["reviewed"] = False
            pair["review_status"] = "not_reviewed"
            pair["from_tm"] = False
            for key in ("accepted_target", "human_accepted", "accepted_by_human"):
                pair.pop(key, None)
    state["annotations"] = {}
    state["annotations_done"] = False
    state["annotations_done_offset"] = 0
    state["annotation_batches"] = {}
    state["annotations_failed_batches"] = 0
    state["segment_structure_version"] = int(
        state.get("segment_structure_version") or 0) + 1
    history = list(state.get("segment_structure_history") or [])
    history.append({
        "operation": operation,
        "index": index,
        "affected_indexes": affected_hint,
        "timestamp": _finalization.now_iso(),
        "actor": actor,
        # 撤销要恢复的是**内容**：只记"操作名 + 位置"恢复不了被拆分/合并掉的原句。
        # 快照只存操作前被替换的那一小段（0–2 条记录），50 条历史也不影响体积。
        "undo_index": undo_index,
        "undo_span": undo_span,
        "restore_pairs": _restore_pairs,
        "restore_paras": _restore_paras,
        "excluded_ids_added": list(excluded_ids_added),
        "excluded_records_restored": [
            json.loads(json.dumps(item)) for item in excluded_records_restored],
    })
    state["segment_structure_history"] = history[-50:]

    # 结构操作会成片清掉 `reviewed`（或把它留给恢复的那一段），计数必须跟着重算，
    # 否则报告模板里的 "已审校段落" 与段落本身脱节。
    _recount_reviewed_segments(state)

    all_new_indexes = list(range(len(pairs)))
    # source_edit 原地改字，影响面就是那一段；其余操作改的是序号映射，整张表都要
    # 按新索引重新对账（排除/恢复也一样）。
    trust_indexes = (list(affected_hint) or [index]
                     if operation == "source_edit"
                     else all_new_indexes)
    _c._mark_translation_truth_changed(
        job_id, state, trust_indexes, reason, actor=actor,
        action=f"segment_{operation}")
    _recheck_delivery_invariants_for_segments(
        state, all_new_indexes, actor=actor)
    _c.save_job_state(job_id, state)
    return state


@_serialized_job_mutation
def undo_translation_segment_structure(job_id, *, actor="user"):
    """撤销最近一次段落结构操作，恢复到操作前的原文/译文。

    只撤销**一步**：历史记录被弹出并留档到 `segment_structure_undo_log`，因此
    连续点两次撤销不会"撤销掉撤销"。被恢复的段落会失去审校通过标记——它们的
    内容虽然是操作前的原文，但下游依赖已经在操作时被判定为过期，重新点亮
    "已审校"会是假绿。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")

    runtime = _c.get_job_runtime_status(job_id, state)
    if str(runtime.get("status") or "") in _c.RUNTIME_ACTIVE_STATUSES:
        raise RuntimeError("任务正在运行，段落结构编辑将在任务完成或暂停后可用。")

    history = [record for record in state.get("segment_structure_history") or []
               if isinstance(record, dict)]
    if not history:
        raise ValueError("没有可撤销的段落结构操作。")
    record = history[-1]
    restore_pairs = record.get("restore_pairs")
    if not isinstance(restore_pairs, list):
        raise ValueError("最近一次结构操作没有保存内容快照，无法撤销。")

    pairs = list(state.get("pairs") or [])
    paras = list(state.get("paras") or [])
    if len(paras) < len(pairs):
        raise ValueError("当前任务的源文与双语段落数量不一致，无法安全撤销。")

    undo_index = int(record.get("undo_index", record.get("index") or 0) or 0)
    undo_span = int(record.get("undo_span") or 0)
    undo_index = max(0, min(undo_index, max(len(pairs), len(paras))))
    pairs_end = max(undo_index, min(len(pairs), undo_index + undo_span))
    paras_end = max(undo_index, min(len(paras), undo_index + undo_span))

    restored = [dict(item) for item in restore_pairs if isinstance(item, dict)]
    for pair in restored:
        pair["reviewed"] = False
        pair["review_status"] = "not_reviewed"
    restored_paras = [str(item or "") for item in record.get("restore_paras") or []]
    if len(restored_paras) != len(restored):
        restored_paras = [str(pair.get("source") or "") for pair in restored]

    # 排除发生在翻译之前时 pairs 本来就是空的，撤销只动 paras。
    if pairs:
        pairs[undo_index:pairs_end] = restored
    paras[undo_index:paras_end] = restored_paras

    # 排除/恢复对清单的增量按反方向还原，保证撤销之后"已排除清单"和段落列表一致。
    added_ids = {str(item) for item in record.get("excluded_ids_added") or []}
    if added_ids:
        state["excluded_segments"] = [
            item for item in state.get("excluded_segments") or []
            if not (isinstance(item, dict)
                    and str(item.get("excluded_id") or "") in added_ids)]
    put_back = [dict(item) for item in record.get("excluded_records_restored") or []
                if isinstance(item, dict)]
    if put_back:
        state["excluded_segments"] = list(
            state.get("excluded_segments") or []) + put_back
    state["pairs"] = pairs
    state["paras"] = paras
    state["annotations"] = {}
    state["annotations_done"] = False
    state["annotations_done_offset"] = 0
    state["annotation_batches"] = {}
    state["annotations_failed_batches"] = 0
    state["segment_structure_version"] = int(
        state.get("segment_structure_version") or 0) + 1
    state["segment_structure_history"] = history[:-1]
    undo_log = list(state.get("segment_structure_undo_log") or [])
    undo_log.append({**record, "undone_at": _finalization.now_iso(),
                     "undone_by": actor})
    state["segment_structure_undo_log"] = undo_log[-20:]
    # 恢复的段落失去了 `reviewed`，计数必须跟着降下来。
    _recount_reviewed_segments(state)

    all_indexes = list(range(len(pairs)))
    _c._mark_translation_truth_changed(
        job_id, state, all_indexes,
        "撤销了最近一次段落结构操作；恢复的译文需要重新审校",
        actor=actor, action="segment_undo")
    _recheck_delivery_invariants_for_segments(state, all_indexes, actor=actor)
    _c.save_job_state(job_id, state)
    return state


@_serialized_job_mutation
def restore_translation_edit(job_id, index, actor="user"):
    """Restore the translation state that existed before the first edit."""
    from folith import delivery as _delivery

    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    if not (0 <= index < len(pairs)):
        raise IndexError(f"段落索引超出范围：{index}")
    pair = pairs[index]
    restore = pair.pop("_translation_edit_restore", None)
    if restore is None and not pair.get("human_edited"):
        return state
    restore = restore or {}
    pair["target"] = restore.get("target") or pair.get("initial_target") or ""
    pair["reviewed"] = bool(restore.get("reviewed"))
    pair["review_status"] = restore.get("review_status", "not_reviewed")
    pair["target_provenance"] = restore.get("target_provenance", "generated")
    pair["from_tm"] = bool(restore.get("from_tm"))
    pair.pop("human_edited", None)
    _c._recount_reviewed_segments(state)

    _c._mark_translation_truth_changed(
        job_id, state, [index], "恢复前版本也改变了 CURRENT_TRANSLATION；相关下游需要重建",
        actor=actor, action="translation_restore")
    _c.save_job_state(job_id, state)
    return state
