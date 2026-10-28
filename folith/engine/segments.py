"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import json
import re
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
               and pair.get("target_provenance") in {
                   "generated", "human_edit", "reviewed", "tm_approved",
                   # Adopted candidates are real current translations: a recovered
                   # segment must not stay marked as failed.
                   "candidate_adopted"}
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
        record = {
            "excluded_id": str(item.get("excluded_id") or f"x{position:06d}"),
            "segment_index": item.get("index"),
            "source": str(item.get("source") or ""),
            "target": str(item.get("target") or ""),
            "reason": str(item.get("reason") or ""),
            "excluded_at": str(item.get("excluded_at") or ""),
            "excluded_by": str(item.get("excluded_by") or ""),
            "restorable": bool(item.get("pair") is not None
                               or str(item.get("source") or "").strip()),
        }
        if item.get("node_id"):
            record["node_id"] = str(item["node_id"])
        if item.get("scope_key"):
            record["scope_key"] = str(item["scope_key"])
        records.append(record)
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
    """交付包里如实说明"哪些内容没有进入译文文档"。

    报告把三类"不在译文里"的内容**分开陈述，绝不混算**（issue #28）：

    - 用户跳过（逐段排除或按页/章/节的范围排除）：``origin=user``。跳过永远
      不计为已翻译、不写翻译记忆、不需要审校；
    - 系统判定的结构性片段（页眉页码等）：``origin=system``，同样不计为已翻译；
    - 解析失败（OCR 部分页失败、无法纳入的图片/表格等）：来自提取报告，不是
      排除——它们在 ``parse_failures`` 里单列，与跳过互不归属。
    """
    records = []
    for record in excluded_segment_records(state):
        item = dict(record)
        item["origin"] = "system" \
            if str(record.get("excluded_by") or "").startswith("system:") else "user"
        records.append(item)
    origins = {
        "user": sum(1 for item in records if item["origin"] == "user"),
        "system": sum(1 for item in records if item["origin"] == "system"),
    }
    report = (state or {}).get("extraction_report") or {}
    manifest = {
        "version": 2,
        "rule": "被排除段落不进入译文的生成式输出，也不占用待完成与审校配额；"
                "原文与排除时的译文在此完整保留，可在工作台恢复。"
                "跳过永远不计为已翻译。",
        "skip_semantics": "排除（origin=user/system）是**跳过**：不计为已翻译、"
                          "不进入审校与质量指标；解析失败不是排除，单独列出。",
        "count": len(records),
        "origins": origins,
        "records": records,
    }
    # 解析失败与跳过的分界：提取报告里的"未纳入内容"与部分失败如实带出；
    # 提取完整成功时这里就是零，不制造一个空的"失败"分组。
    unsupported = [item for item in report.get("unsupported") or []
                   if isinstance(item, dict) and item.get("count")]
    parse_failures = {
        "extraction_status": str(report.get("status") or ""),
        "unsupported_count": sum(int(item.get("count") or 0) for item in unsupported),
        "unsupported": [{"kind": str(item.get("kind") or ""),
                         "count": int(item.get("count") or 0)}
                        for item in unsupported],
    }
    if (state or {}).get("warnings"):
        parse_failures["warnings"] = [str(item) for item in state["warnings"]]
    manifest["parse_failures"] = parse_failures
    return manifest


def _excluded_segments_md(manifest):
    lines = [
        "# 已排除段落清单",
        "",
        f"- 排除数量：{manifest.get('count', 0)}"
        f"（用户跳过 {int((manifest.get('origins') or {}).get('user', 0))}，"
        f"系统判定 {int((manifest.get('origins') or {}).get('system', 0))}）",
        f"- 规则：{manifest.get('rule', '')}",
        "- 跳过不是翻译：被排除的段落不计为已翻译；解析失败不是跳过，"
        "另见下方「解析失败」。",
        "",
        "| 原位置 | 来源 | 排除原因 | 原文 |",
        "| --- | --- | --- | --- |",
    ]
    for record in manifest.get("records") or []:
        index = record.get("segment_index")
        position = f"第 {int(index) + 1} 段" if isinstance(index, int) else "—"
        source = " ".join(str(record.get("source") or "").split())
        if len(source) > 160:
            source = source[:159] + "…"
        reason = str(record.get("reason") or "未填写")
        origin = "用户跳过" if record.get("origin") == "user" else "系统判定"
        lines.append(f"| {position} | {origin} | {reason} | {source or '（空）'} |")
    failures = manifest.get("parse_failures") or {}
    if failures.get("unsupported_count"):
        lines += ["", "## 解析失败（不是排除）", ""]
        for item in failures.get("unsupported") or []:
            lines.append(f"- {item.get('kind') or '未知内容'}：{item.get('count')} 处"
                         "（保留在源文件里，未进入译文）")
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
                             before_source="", after_source="",
                             node_id=None, scope_key=None):
    """构造一条 ``excluded_segments`` 记录。

    人工「排除本段」与系统判定的不可翻译段落共用同一份记录形状：交付清单、
    恢复入口与撤销日志都按它读取，两条路径不能各自长出一套字段。
    """
    excluded_id_value = f"x{int(state.get('exclusion_seq') or 0):06d}"
    state["exclusion_seq"] = int(state.get("exclusion_seq") or 0) + 1
    record = {
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
    record["positional_artifacts"] = _capture_positional_artifacts(state, index)
    if node_id:
        record["node_id"] = str(node_id)
    if scope_key:
        record["scope_key"] = str(scope_key)
    return record


def _capture_positional_artifacts(state, index):
    """Save the removed segment's provenance and paragraph membership."""
    count = len(state.get("paras") or [])
    saved = {"arrays": {}}
    for key in ("segment_source_spans", "segment_source_marks"):
        values = state.get(key)
        if isinstance(values, list) and values:
            if len(values) != count:
                raise ValueError("段落来源位置数据与当前段落数量不一致，无法安全排除。")
            saved["arrays"][key] = json.loads(json.dumps(values[index]))
    ranges = (state.get("segmentation") or {}).get("paragraph_ranges") or []
    owners = [i for i, row in enumerate(ranges) if isinstance(row, dict)
              and isinstance(row.get("start"), int) and isinstance(row.get("end"), int)
              and row["start"] <= index < row["end"]]
    if len(owners) > 1:
        raise ValueError("段落区间重叠，无法安全排除。")
    if owners:
        row_index = owners[0]
        saved["paragraph"] = {"row": row_index,
                              "paragraph_index": ranges[row_index].get("paragraph_index")}
    return saved


def _restore_positional_artifacts(state, index, record):
    """Insert saved provenance at the restored position; never guess membership."""
    saved = record.get("positional_artifacts")
    count = len(state.get("paras") or [])
    if not isinstance(saved, dict):
        # Older manual exclusions left positional data in place; older scope
        # exclusions removed it without saving it. Only the former can be
        # restored without inventing source provenance.
        for key in ("segment_source_spans", "segment_source_marks"):
            values = state.get(key)
            if isinstance(values, list) and values and len(values) != count + 1:
                raise ValueError("旧排除记录缺少来源位置数据，无法安全恢复被排除的段落。")
        segmentation = state.get("segmentation") or {}
        if segmentation.get("paragraph_ranges") and segmentation.get("output_segment_count") == count:
            raise ValueError("旧排除记录缺少段落区间信息，无法安全恢复被排除的段落。")
        return
    arrays = saved.get("arrays") or {}
    for key in ("segment_source_spans", "segment_source_marks"):
        values = state.get(key)
        if isinstance(values, list) and values and key not in arrays:
            raise ValueError("段落来源位置数据已变化，无法安全恢复被排除的段落。")
    for key, value in arrays.items():
        values = state.get(key)
        if not isinstance(values, list) or len(values) != count:
            raise ValueError("段落来源位置数据已变化，无法安全恢复被排除的段落。")
    segmentation = state.get("segmentation")
    ranges = segmentation.get("paragraph_ranges") if isinstance(segmentation, dict) else None
    owner = saved.get("paragraph")
    owner_index = None
    if owner is not None:
        owner_index = owner.get("row")
        if not isinstance(ranges, list) or not isinstance(owner_index, int) or not 0 <= owner_index < len(ranges):
            raise ValueError("原段落区间已变化，无法安全恢复被排除的段落。")
        row = ranges[owner_index]
        if not isinstance(row, dict) or row.get("paragraph_index") != owner.get("paragraph_index") \
                or not isinstance(row.get("start"), int) or not isinstance(row.get("end"), int) \
                or not row["start"] <= index <= row["end"]:
            raise ValueError("原段落区间已变化，无法安全恢复被排除的段落。")
    for key, value in arrays.items():
        state[key].insert(index, json.loads(json.dumps(value)))
    if isinstance(ranges, list) and ranges:
        updated = []
        for row_index, row in enumerate(ranges):
            if not isinstance(row, dict) or not isinstance(row.get("start"), int) or not isinstance(row.get("end"), int):
                updated.append(row)
                continue
            row = dict(row)
            start, end = row["start"], row["end"]
            if row_index == owner_index:
                # A removed internal separator cannot be reconstructed safely.
                row["end"] = end + 1
                if "separators" in row:
                    row["separators"] = []
                    row["gate"] = {**(row.get("gate") or {}), "separators_recorded": False}
            else:
                # Empty earlier paragraphs at this boundary stay before the
                # restored segment; later paragraphs move with their content.
                earlier_empty = (owner_index is not None and row_index < owner_index
                                 and start == end == index)
                row["start"] = start + (start >= index and not earlier_empty)
                row["end"] = end + (end > index or (start >= index and end == index
                                                     and not earlier_empty))
            updated.append(row)
        segmentation["paragraph_ranges"] = updated
        segmentation["output_segment_count"] = count + 1


def _shift_paragraph_ranges(paragraph_ranges, removed_index):
    """把被移出的段落从 ``segmentation.paragraph_ranges`` 中剔除。

    段落区间是「清洗后段落 → 翻译单元」的位置契约；少了一段之后，后面每一段都
    要前移一位，包含它的区间则收窄一格。留着旧区间会让原文血缘审计把门禁结论
    落到错误的段落上。

    边界分隔符（``separators``）跟着区间一起搬，但只有在新旧边界一致时才
    成立：段被从段落**内部**移除时，新的相邻关系（例如段0↔段2）在原文里
    从未存在过，此时必须作废而不是错位沿用；去掉首段/末段时，剩下的恰好是
    原边界的一个子集，可以安全保留。
    """
    shifted = []
    for row in paragraph_ranges or []:
        if not isinstance(row, dict):
            continue
        start, end = row.get("start"), row.get("end")
        if not (isinstance(start, int) and isinstance(end, int)):
            shifted.append(dict(row))
            continue
        count = end - start
        separators = row.get("separators")
        updated = None
        if isinstance(separators, list) and separators and count > 1:
            if start < removed_index < end - 1:
                # 段被从段落内部移除时，原文里已不存在段0↔段2 的新边界：
                # 旧的 separators 描述的是另一组边界，必须作废而不是错位沿用。
                # 注意末段（removed_index == end - 1）不属于内部移除：删掉末段
                # 只是少了一个边界，前面的边界仍然成立。
                updated = {**row, "separators": [],
                           "gate": {**(row.get("gate") or {}),
                                    "separators_recorded": False}}
            elif removed_index == start:
                # 去掉首段：剩下的边界是原边界的后一段，仍然成立。
                updated = {**row, "separators": list(separators[1:])}
            elif removed_index == end - 1:
                # 去掉末段：留下的是前面的边界，仍然成立。
                updated = {**row, "separators": list(separators[:-1])}
        if start > removed_index:
            start -= 1
        if end > removed_index:
            end -= 1
        shifted.append({**(updated or row), "start": start, "end": max(start, end)})
    return shifted


def _drop_positional_artifacts(state, index, segment_count):
    """段落被移出后，同步按位置排列的派生产物（原文锚点、标记、段落区间）。"""
    for key in ("segment_source_spans", "segment_source_marks"):
        values = state.get(key)
        if isinstance(values, list) and 0 <= index < len(values):
            values.pop(index)
    segmentation = state.get("segmentation")
    if isinstance(segmentation, dict) and segmentation.get("paragraph_ranges"):
        segmentation["paragraph_ranges"] = _shift_paragraph_ranges(
            segmentation["paragraph_ranges"], index)
        segmentation["output_segment_count"] = segment_count


def _drop_segment_at(state, index):
    """从工作列表中移出一个段落，并同步它的位置型派生产物。"""
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    if pairs:
        pairs.pop(index)
    if 0 <= index < len(paras):
        paras.pop(index)
    _drop_positional_artifacts(state, index, len(paras))


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


# ================= 翻译范围（译前按页 / 章 / 节选择翻译范围） =================
#
# 范围选择只是**既有排除账本的批量入口**，不另设"排除真相"：页/章/节是从文档
# 结构（IR 区块类型 + 标题启发式）算出的只读投影，映射到当前工作列表的连续索引
# 区间；批量排除复用单段排除的记录形状（`excluded_segments` + 移出 pairs/paras），
# 恢复复用原文锚点，因此交付清单、恢复入口与"排除不计为已翻译"的既有契约全部
# 自动成立。被排除的段落始终留在排除清单里可定位、可恢复。

_SCOPE_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[0-9〇零一二三四五六七八九十百千]+\s*(?:章|回|部|编|卷)"
    r"|[0-9]+\s*(?:章|回)\b"
    r"|(?:chapter|part|book|volume)\s+[0-9ivxlcdm]+)\b",
    re.IGNORECASE,
)
_SCOPE_LABEL_MAX = 40


def _scope_page_numbers(state, count):
    """每个工作段落所在的页码（1 基，与 block_id ``p{n}`` 一致）；未知为 None。

    页码来自 `segment_source_spans` 里每个 span 的 ``page_id``（``p3`` → 第 3 页）。
    没有血缘（如纯文本导入）时全部为 None，调用方就不显示"按页"分组。
    """
    spans = state.get("segment_source_spans")
    if not isinstance(spans, list) or len(spans) != count:
        return [None] * count
    pages = []
    for item in spans:
        page = None
        for span in (item if isinstance(item, list) else []):
            match = re.match(r"p(\d+)", str((span or {}).get("page_id") or ""))
            if match:
                page = int(match.group(1))
                break
        pages.append(page)
    return pages


def _scope_document_block_types(state):
    """block_id → IR 区块类型（``extraction_report.document_ir`` 的只读投影）。"""
    blocks = {}
    document = (state.get("extraction_report") or {}).get("document_ir") or {}
    for block in (document.get("blocks") or []):
        if isinstance(block, dict) and block.get("block_id"):
            blocks[str(block["block_id"])] = str(block.get("type") or "")
    return blocks


def _scope_heading_kinds(state, sources):
    """每个段落是否开启一章 / 一节；返回 ``"chapter" | "section" | None`` 列表。

    两个信号取并集：IR 区块类型（``title``→章、``heading``→节）与文本启发式
    （「第X章 / Chapter N」→章，其余短标题→节）。IR 缺失时退化到纯文本判定。
    """
    from folith.segmentation import _looks_like_heading

    count = len(sources)
    types = _scope_document_block_types(state)
    spans = state.get("segment_source_spans")
    kinds = []
    for index, text in enumerate(sources):
        block_types = set()
        if isinstance(spans, list) and index < len(spans) \
                and isinstance(spans[index], list):
            block_types = {types.get(str((span or {}).get("block_id") or ""), "")
                           for span in spans[index]}
        if "title" in block_types or _SCOPE_CHAPTER_RE.match(text):
            kinds.append("chapter")
        elif "heading" in block_types or _looks_like_heading(text):
            kinds.append("section")
        else:
            kinds.append(None)
    return kinds


def _scope_contiguous_nodes(kind, split_kinds, sources, label_of, *, start=0):
    """按"哪一段开启新节点"的标记，把 [start, len) 切成连续索引区间节点。"""
    nodes = []
    current = start
    total = len(sources)
    for index in range(start + 1, total):
        if split_kinds[index]:
            nodes.append((current, index))
            current = index
    nodes.append((current, total))
    result = []
    for position, (node_start, node_end) in enumerate(nodes):
        if node_start >= node_end:
            continue
        label = label_of(node_start) or (f"{kind}-{position}")
        result.append({
            "key": f"{kind}:{position:04d}",
            "kind": kind,
            "label": label,
            "start": node_start,
            "end": node_end,
            "segments": node_end - node_start,
            "chars": sum(len(sources[item]) for item in range(node_start, node_end)),
        })
    return result


#: 译前范围排除计划在 state 里的键。计划是大纲段落坐标上的"意图"，任务开始、
#: 翻译单元建好那一刻由 :func:`apply_scope_exclusion_plan` 落进排除账本。
SCOPE_PLAN_KEY = "scope_exclusion_plan"

_OUTLINE_KIND_LABELS = {"chapter": "按章", "page": "按页", "group": "按组"}


def _outline_scope_nodes(outline):
    """大纲节点 → 范围树节点（只留有文字层的部分；扫描章不冒充可选范围）。"""
    nodes = []
    for node in (outline or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        start = int(node.get("para_start") or 0)
        count = int(node.get("para_count") or 0)
        if count <= 0:
            continue
        nodes.append({
            "key": f"outline:{node.get('id') or ''}",
            "kind": str(node.get("kind") or "chapter"),
            "label": str(node.get("title") or "未命名部分"),
            "start": start,
            "end": start + count,
            "segments": count,
            "chars": int(node.get("chars") or 0),
        })
    return nodes


def _scope_plan_entries(state):
    plan = (state or {}).get(SCOPE_PLAN_KEY)
    if not isinstance(plan, dict) or not isinstance(plan.get("entries"), list):
        return []
    return [entry for entry in plan["entries"] if isinstance(entry, dict)]


def _scope_plan_records(state):
    """译前计划排除的只读投影（形状对齐 ``excluded_segment_records``）。

    ``position`` 用原文段落区间描述位置：计划还没变成翻译单元，段内索引
    不存在，不能拿"原位置未知"糊弄用户。
    """
    records = []
    for entry in _scope_plan_entries(state):
        start = int(entry.get("para_start") or 0)
        end = int(entry.get("para_end") or 0)
        plan_rec = {
            "excluded_id": str(entry.get("excluded_id") or ""),
            "segment_index": None,
            "planned": True,
            "source_start": start + 1,
            "source_end": end,
            "position": (f"原文第 {start + 1}–{end} 段" if end > start + 1
                         else f"原文第 {start + 1} 段"),
            "source": str(entry.get("anchor_first") or ""),
            "target": "",
            "reason": str(entry.get("reason") or ""),
            "excluded_at": str(entry.get("planned_at") or ""),
            "excluded_by": str(entry.get("planned_by") or ""),
            "restorable": True,
        }
        if entry.get("node_id"):
            plan_rec["node_id"] = str(entry["node_id"])
        if entry.get("key"):
            plan_rec["scope_key"] = str(entry["key"])
        records.append(plan_rec)
    return records


def _pre_start_scope_tree(state):
    """任务还没有段落时的范围树：坐标来自 #27 的本地原文大纲。"""
    outline = state.get("source_outline")
    outline = outline if isinstance(outline, dict) else {}
    nodes = _outline_scope_nodes(outline)
    records = _scope_plan_records(state)
    plan_totals = scope_plan_totals(state)
    base_segments = int(outline.get("total_paragraphs") or 0)
    base_chars = int(outline.get("total_chars") or 0)
    if not base_segments:
        # 兜底：旧任务或手工构造的大纲可能没有汇总字段，按节点重算。
        base_segments = sum(node["segments"] for node in nodes)
        base_chars = sum(node["chars"] for node in nodes)
    # 总量扣除已计划排除的范围：预估里的数字是"还会翻译多少"，不是原文全量。
    total = {"segments": max(0, base_segments - plan_totals["segments"]),
             "chars": max(0, base_chars - plan_totals["chars"])}
    excluded = {"count": plan_totals["segments"], "records": records}
    if not nodes:
        return {"available": False, "groups": [], "total": total,
                "excluded": excluded, "pre_start": True}
    planned_keys = {str(entry.get("key") or "") for entry in _scope_plan_entries(state)}
    nodes = [node for node in nodes if node["key"] not in planned_keys]
    groups = [{"kind": kind, "label": _OUTLINE_KIND_LABELS.get(kind, kind),
               "nodes": [node for node in nodes if node["kind"] == kind]}
              for kind in ("chapter", "page", "group")
              if any(node["kind"] == kind for node in nodes)]
    return {"available": True, "groups": groups, "total": total,
            "excluded": excluded, "pre_start": True}


def translation_scope_tree(state):
    """译前范围选择的只读结构：页 / 章 / 节 → 当前工作列表的连续索引区间。

    排除账本已经把移出的段落拿出了工作列表，所以这里的区间就是"现在还能翻译
    的内容"；恢复之后下一次调用会重新算出该段的归属。没有页信息或没有标题时
    对应分组为空——界面只展示存在的分组，绝不用猜出来的结构冒充文档结构。

    任务还没有段落时（#28 的验收场景：开始翻译**之前**排除不想付费翻译的
    章节），回落到 ``state["source_outline"]``（#27 的本地原文结构）：大纲的
    章节/页/段落分组就是译前范围的坐标系，排除记成计划（见
    :func:`_plan_scope_exclusion`），等翻译单元建好再落账。
    """
    sources = _segment_source_list(state)
    total = {"segments": len(sources),
             "chars": sum(len(item) for item in sources)}
    excluded = excluded_segments_summary(state)
    if state.get("p1_done") and _scope_plan_entries(state):
        excluded["records"] = list(excluded["records"]) + _scope_plan_records(state)
        excluded["unapplied_plan"] = True
    if not sources:
        if state.get("p1_done"):
            # 任务已开始（例如全部段落都被排除了）：工作列表为空是排除的结果，
            # 不是"还没开始"——账本里的记录必须能在这个对话框里恢复，绝不能
            # 落进译前计划的坐标系里。
            return {"available": False, "groups": [], "total": total,
                    "excluded": excluded, "pre_start": False}
        return _pre_start_scope_tree(state)

    groups = []

    # 按页：页码变化处切节点；读不出页码的段并入当前节点（它们本来就属于
    # 前面的流）。全程没有页码时不提供按页分组。
    pages = _scope_page_numbers(state, len(sources))
    if any(page is not None for page in pages):
        def page_label(index):
            return f"第 {pages[index]} 页" if pages[index] is not None else ""
        split_pages = [False] * len(sources)
        for index in range(1, len(sources)):
            if pages[index] is not None and pages[index] != pages[index - 1]:
                split_pages[index] = True
        nodes = _scope_contiguous_nodes("page", split_pages, sources, page_label)
        if len(nodes) > 1:
            groups.append({"kind": "page", "label": "按页", "nodes": nodes})

    # 按章 / 按节：标题处切节点；节同时在章边界重开（章的范围不被节吞掉）。
    # 只剩一个节点时仍要展示——前提是它真的来自标题（排除把别的章移走之后，
    # 剩下的那一章仍是可选择的范围）；纯文本没有任何标题时整个分组不出现。
    kinds = _scope_heading_kinds(state, sources)

    def heading_label(index):
        text = " ".join(str(sources[index] or "").split())
        if len(text) > _SCOPE_LABEL_MAX:
            text = text[:_SCOPE_LABEL_MAX - 1] + "…"
        return text

    chapter_splits = [kind == "chapter" for kind in kinds]
    chapter_nodes = _scope_contiguous_nodes("chapter", chapter_splits, sources, heading_label)
    if len(chapter_nodes) > 1 or any(kind == "chapter" for kind in kinds):
        groups.append({"kind": "chapter", "label": "按章", "nodes": chapter_nodes})

    section_splits = [kind in {"chapter", "section"} for kind in kinds]
    section_nodes = _scope_contiguous_nodes("section", section_splits, sources, heading_label)
    if len(section_nodes) > 1 or any(kind == "section" for kind in kinds):
        groups.append({"kind": "section", "label": "按节", "nodes": section_nodes})

    return {"available": True, "groups": groups,
            "total": total, "excluded": excluded}


@_serialized_job_mutation
def exclude_translation_scope(job_id, keys, *, actor="user"):
    """按范围（页 / 章 / 节）批量排除段落——映射到既有排除账本的批量入口。

    语义与逐段 `mutate_translation_segments(exclude)` 完全一致：段落移出
    pairs/paras、进 `state["excluded_segments"]` 留档（含原文锚点），因此不进入
    翻译、不产生付费模型调用、不计入待完成与审校配额、交付时如实登记；恢复
    走同一份账本（:func:`restore_translation_scope` 或逐段 include）。

    与逐段排除的两点差别（刻意为之）：
    - 一次持久化、一条历史记录。几百段的书按范围排除时，逐段落盘既慢又会把
      撤销历史刷满；
    - 不接入"撤销上一次结构操作"——批量恢复走锚点（见 restore），不需要内容
      快照，历史记录里因此没有 ``restore_pairs``，撤销入口对它自动不可用。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")

    runtime = _c.get_job_runtime_status(job_id, state)
    if str(runtime.get("status") or "") in _c.RUNTIME_ACTIVE_STATUSES:
        raise RuntimeError("任务正在运行，翻译范围将在任务完成或暂停后可修改。")

    wanted = list(dict.fromkeys(
        str(key or "").strip() for key in (keys or []) if str(key or "").strip()))
    if not wanted:
        raise ValueError("请选择要排除的范围。")

    scope_state = state
    if not state.get("pairs") and not state.get("paras") and not state.get("p1_done"):
        from folith import outline_corrections, outline_recovery
        scope_state = dict(state)
        recovered = outline_recovery.load(job_id, state.get("source_outline") or {})
        scope_state["source_outline"], correction_status = outline_corrections.apply(
            job_id, recovered)
        if correction_status == outline_corrections.UNREADABLE:
            raise ValueError("无法读取结构修正文件；原文件已保留。")
    tree = translation_scope_tree(scope_state)
    nodes = {node["key"]: node
             for group in tree.get("groups") or []
             for node in group.get("nodes") or []}
    missing = [key for key in wanted if key not in nodes]
    if missing:
        raise ValueError("范围与当前段落不匹配（任务状态可能已变化），"
                         "请重新打开翻译范围后重试。")
    # #28 的闭环：开始翻译之前（还没有任何翻译单元）排除的是"意图"——记成
    # 计划，等任务开始、翻译单元建好那一刻由 apply_scope_exclusion_plan 落进
    # 同一份排除账本。这样"不付费翻译不想要的章节"在第一次模型调用之前就定了。
    # 判据用 p1_done 而不是"列表为空"：已开始的任务把段落全部排除后列表同样
    # 是空的，但那必须走账本路径。
    if not state.get("pairs") and not state.get("paras") \
            and not state.get("p1_done"):
        return _plan_scope_exclusion(state, job_id, wanted, nodes, actor=actor,
                                     outline=scope_state.get("source_outline"))
    indexes = sorted(
        {index for key in wanted
         for index in range(nodes[key]["start"], nodes[key]["end"])},
        reverse=True)
    if not indexes:
        raise ValueError("所选范围里没有可排除的段落。")

    pairs = list(state.get("pairs") or [])
    paras = list(state.get("paras") or [])
    if pairs and len(paras) != len(pairs):
        raise ValueError("当前任务的源文与双语段落数量不一致，无法安全修改翻译范围。")
    if not pairs and not paras:
        raise ValueError("当前任务还没有可编辑的段落。")
    assign_missing_segment_uids(state, job_id=job_id)

    # 锚点在任何移除之前、对**原始**序列计算：锚点是原文文本，移除之后依然
    # 唯一可查，恢复时按它们插回。
    sources = _segment_source_list(state)
    reason_by_index = {}
    node_by_index = {}
    key_by_index = {}
    for key in wanted:
        node = nodes[key]
        node_id_val = node.get("id") or (key.split(":", 1)[1] if key.startswith("outline:") else None)
        for index in range(node["start"], node["end"]):
            reason_by_index[index] = f"范围排除：{node['label']}"
            node_by_index[index] = node_id_val
            key_by_index[index] = key

    records = []
    old_count = len(sources)
    for index in indexes:  # 从大到小移出，前面的索引不受影响
        source_value = str(sources[index] or "")
        target_value = str(pairs[index].get("target") or "") if pairs else ""
        records.append(_excluded_segment_record(
            state, index, reason=reason_by_index[index], actor=actor,
            source=source_value, target=target_value,
            pair=(pairs[index] if pairs else None), in_pairs=bool(pairs),
            before_source=sources[index - 1] if index > 0 else "",
            after_source=(sources[index + 1]
                          if index + 1 < len(sources) else ""),
            node_id=node_by_index.get(index),
            scope_key=key_by_index.get(index)))
        _drop_segment_at(state, index)
    # 账本按文档顺序排列（移除必须从大到小做，落账不必）；交付清单与恢复
    # 入口读到的顺序与原文一致。
    records.sort(key=lambda record: int(record.get("index") or 0))
    state["excluded_segments"] = list(
        state.get("excluded_segments") or []) + records

    labels = "、".join(dict.fromkeys(nodes[key]["label"] for key in wanted))
    reason = (f"按范围排除了 {len(records)} 段（{labels[:80]}），"
              "交付范围发生变化，原有审校需要重新确认")
    if pairs:
        _translation_evidence.mark_runtime_review_stale(
            state, list(range(old_count)), reason, dependency_change=True)

    state["annotations"] = {}
    state["annotations_done"] = False
    state["annotations_done_offset"] = 0
    state["annotation_batches"] = {}
    state["annotations_failed_batches"] = 0
    state["segment_structure_version"] = int(
        state.get("segment_structure_version") or 0) + 1
    history = list(state.get("segment_structure_history") or [])
    history.append({
        "operation": "scope_exclude",
        "index": indexes[-1],
        "affected_indexes": [],
        "timestamp": _finalization.now_iso(),
        "actor": actor,
        # 批量排除不存内容快照（恢复走排除账本的锚点），撤销入口因此对它
        # 自动不可用——见 docstring。
        "excluded_ids_added": [record["excluded_id"] for record in records],
    })
    state["segment_structure_history"] = history[-50:]

    _recount_reviewed_segments(state)
    remaining_pairs = list(state.get("pairs") or [])
    remaining_indexes = list(range(len(remaining_pairs)))
    _c._mark_translation_truth_changed(
        job_id, state, remaining_indexes, reason, actor=actor,
        action="scope_exclude")
    _recheck_delivery_invariants_for_segments(
        state, remaining_indexes, actor=actor)
    _c._append_runtime_technical_log(
        job_id, f"translation scope: excluded {len(records)} segment(s) "
        f"by scope ({labels[:80]}), "
        f"{len(remaining_pairs) or len(state.get('paras') or [])} segment(s) remain")
    _c.save_job_state(job_id, state)
    return {
        "excluded": len(records),
        "remaining": len(remaining_pairs) or len(state.get("paras") or []),
        "excluded_ids": [record["excluded_id"] for record in records],
        "state": state,
    }


def _exclusion_chronological_key(record):
    """排除的先后（``x000123`` 里的序号单调递增）；无序号的早期记录排最前。"""
    match = re.fullmatch(r"x(\d+)", str((record or {}).get("excluded_id") or ""))
    return (1, int(match.group(1))) if match else (0, 0)


@_serialized_job_mutation
def restore_translation_scope(job_id, excluded_ids, *, actor="user"):
    """按 ``excluded_id`` 批量恢复被排除的段落（范围排除 / 逐段排除通用）。

    按**排除的逆序**（后排除的先恢复）逐条插回：每一步恢复时的工作列表恰好
    等于该记录被排除那一刻的状态，因此 `_exclusion_restore_index` 的锚点与
    回退位置在这一步是精确的——这是"按排除栈出栈"的语义，与逐段恢复共用
    同一套定位逻辑。与单段恢复一致：内容原样回来，但审校通过标记不点亮。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")

    runtime = _c.get_job_runtime_status(job_id, state)
    if str(runtime.get("status") or "") in _c.RUNTIME_ACTIVE_STATUSES:
        raise RuntimeError("任务正在运行，翻译范围将在任务完成或暂停后可修改。")

    wanted = list(dict.fromkeys(
        str(item or "").strip() for item in (excluded_ids or [])
        if str(item or "").strip()))
    if not wanted:
        raise ValueError("请选择要恢复的已排除段落。")
    planned_ids = {str(entry.get("excluded_id") or "") for entry in _scope_plan_entries(state)}
    withdrawing = [item for item in wanted if item in planned_ids]
    if withdrawing:
        result = _unplan_scope_exclusion(state, job_id, withdrawing, actor=actor)
        wanted = [item for item in wanted if item not in planned_ids]
        if not wanted:
            return result
    # 译前排除还没有落账：恢复就是把计划条目撤下来，同样走这一个入口。
    # 判据同 exclude：p1_done 才代表翻译单元契约已存在（全部排除后列表也是空的）。
    if not state.get("pairs") and not state.get("paras") \
            and not state.get("p1_done"):
        return _unplan_scope_exclusion(state, job_id, wanted, actor=actor)
    by_id = {}
    for item in state.get("excluded_segments") or []:
        if isinstance(item, dict) \
                and str(item.get("excluded_id") or "") in set(wanted):
            by_id[str(item.get("excluded_id") or "")] = item
    missing = [key for key in wanted if key not in by_id]
    if missing:
        raise ValueError("找不到要恢复的已排除段落（可能已被恢复）。")

    # 直接在 state 的列表上插入：逐条恢复时 `_exclusion_restore_index` 要在
    # **当前**（已插回若干段）的序列里找锚点，本地副本会让它看不到前一步。
    # 注意空列表也要用 state 里的原对象（`or []` 会新建列表，插入就丢了）。
    pairs = state.get("pairs")
    paras = state.get("paras")
    if not isinstance(pairs, list):
        pairs = []
        state["pairs"] = pairs
    if not isinstance(paras, list):
        paras = []
        state["paras"] = paras
    if pairs and len(paras) != len(pairs):
        raise ValueError("当前任务的源文与双语段落数量不一致，无法安全修改翻译范围。")

    records = sorted((dict(item) for item in by_id.values()),
                     key=_exclusion_chronological_key, reverse=True)
    restored = []
    for record in records:
        index = _exclusion_restore_index(state, record)
        _restore_positional_artifacts(state, index, record)
        excluded_pair = record.get("pair")
        if excluded_pair is None and pairs:
            # 译前排除没有译文快照；任务已翻译后恢复时，补回待译的空槽，
            # 不能只插 paras 而让后面的 pairs 错配原文。
            excluded_pair = {"source": str(record.get("source") or ""),
                             "target": "", "initial_target": "",
                             "target_provenance": "generated", "from_tm": False}
        if isinstance(excluded_pair, dict):
            # 与单段恢复一致：内容原样回来，但旧的"已审校"不跟着点亮。
            # 用记录里是否存了 pair 来判断，而不是当前 pairs 是否为空——
            # 全部段落被排除时 pairs 恰好是空列表，但它们都要插回来。
            if not isinstance(pairs, list):
                pairs = []
                state["pairs"] = pairs
            excluded_pair = json.loads(json.dumps(excluded_pair))
            excluded_pair["reviewed"] = False
            excluded_pair["review_status"] = "not_reviewed"
            for key in ("accepted_target", "human_accepted", "accepted_by_human"):
                excluded_pair.pop(key, None)
            pairs.insert(index, excluded_pair)
        paras.insert(index, str(record.get("source") or ""))
        state["excluded_segments"] = [
            item for item in state.get("excluded_segments") or []
            if not (isinstance(item, dict)
                    and str(item.get("excluded_id") or "")
                    == str(record.get("excluded_id") or ""))]
        restored.append(record)

    reason = (f"恢复了 {len(restored)} 段被排除的段落，交付范围发生变化，"
              "原有审校需要重新确认")
    if pairs:
        _translation_evidence.mark_runtime_review_stale(
            state, list(range(len(pairs))), reason, dependency_change=True)

    state["annotations"] = {}
    state["annotations_done"] = False
    state["annotations_done_offset"] = 0
    state["annotation_batches"] = {}
    state["annotations_failed_batches"] = 0
    state["segment_structure_version"] = int(
        state.get("segment_structure_version") or 0) + 1
    history = list(state.get("segment_structure_history") or [])
    history.append({
        "operation": "scope_include",
        "index": int((restored or [{}])[0].get("index") or 0),
        "affected_indexes": [],
        "timestamp": _finalization.now_iso(),
        "actor": actor,
        "excluded_records_restored": [
            json.loads(json.dumps(item)) for item in restored],
    })
    state["segment_structure_history"] = history[-50:]

    _recount_reviewed_segments(state)
    _c._mark_translation_truth_changed(
        job_id, state, list(range(len(pairs))), reason, actor=actor,
        action="scope_include")
    _recheck_delivery_invariants_for_segments(
        state, list(range(len(pairs))), actor=actor)
    _c._append_runtime_technical_log(
        job_id, f"translation scope: restored {len(restored)} excluded "
        f"segment(s), {len(pairs) or len(paras)} segment(s) now in scope")
    _c.save_job_state(job_id, state)
    return {
        "restored": len(restored),
        "remaining": len(pairs) or len(paras),
        "state": state,
    }


def scope_plan_totals(state):
    """译前排除计划覆盖的段数与字符数（大纲坐标；无计划返回零）。

    预估与范围树的总量都要扣掉它：排除决定必须先于任何"这份活有多大"的
    数字反映出来，否则译前看到的成本还是全量原文。
    """
    outline = (state or {}).get("source_outline")
    outline = outline if isinstance(outline, dict) else {}
    paragraphs = [str(item or "") for item in outline.get("paragraphs") or []]
    segments = chars = 0
    for entry in _scope_plan_entries(state):
        start = int(entry.get("para_start") or 0)
        end = int(entry.get("para_end") or 0)
        if not 0 <= start < end <= len(paragraphs):
            continue
        segments += end - start
        chars += sum(len(item) for item in paragraphs[start:end])
    return {"segments": segments, "chars": chars}


def _planned_paragraph_total(state):
    """按大纲口径：总段数 − 已计划排除的段数（开始前的"还会翻译多少"）。"""
    outline = (state or {}).get("source_outline")
    outline = outline if isinstance(outline, dict) else {}
    remaining = int(outline.get("total_paragraphs") or 0)
    if not remaining:
        remaining = sum(int(node.get("para_count") or 0)
                        for node in outline.get("nodes") or []
                        if isinstance(node, dict))
    remaining -= scope_plan_totals(state)["segments"]
    return max(0, remaining)


def _plan_scope_exclusion(state, job_id, wanted, nodes, *, actor, outline=None):
    """把译前选定的范围记成计划（大纲段落坐标 + 首尾锚点）。

    计划在 ``apply_scope_exclusion_plan`` 之前不触碰 ``excluded_segments``：
    排除账本描述的是"已从工作列表移出的翻译单元"，而这里还没有翻译单元。
    ``excluded_id`` 与账本共用同一个序号源，落账时沿用，绝不重号。
    """
    outline = outline if outline is not None else state.get("source_outline")
    outline = outline if isinstance(outline, dict) else {}
    paragraphs = [str(item or "") for item in outline.get("paragraphs") or []]
    plan = state.get(SCOPE_PLAN_KEY)
    if not isinstance(plan, dict) or not isinstance(plan.get("entries"), list):
        plan = {"entries": []}
    planned_keys = {str(entry.get("key") or "")
                    for entry in plan["entries"] if isinstance(entry, dict)}
    added = []
    for key in wanted:
        if key in planned_keys:
            continue
        node = nodes[key]
        start, end = int(node["start"]), int(node["end"])
        excluded_id = f"x{int(state.get('exclusion_seq') or 0):06d}"
        state["exclusion_seq"] = int(state.get("exclusion_seq") or 0) + 1
        node_id_val = node.get("id") or (key.split(":", 1)[1] if str(key).startswith("outline:") else None)
        added.append({
            "excluded_id": excluded_id,
            "key": key,
            "node_id": node_id_val,
            "kind": str(node.get("kind") or ""),
            "label": str(node.get("label") or ""),
            "para_start": start,
            "para_end": end,
            "anchor_first": (paragraphs[start]
                             if 0 <= start < len(paragraphs) else ""),
            "anchor_last": (paragraphs[end - 1]
                            if 0 < end <= len(paragraphs) else ""),
            "reason": f"范围排除（译前）：{node.get('label')}",
            "planned_at": _finalization.now_iso(),
            "planned_by": str(actor or "user"),
        })
    if not added:
        raise ValueError("所选范围已在译前排除计划里，无需重复排除。")
    plan["entries"] = list(plan["entries"]) + added
    plan["fingerprint"] = str(outline.get("fingerprint") or "")
    plan["updated_at"] = _finalization.now_iso()
    state[SCOPE_PLAN_KEY] = plan
    planned_segments = sum(int(entry["para_end"]) - int(entry["para_start"])
                           for entry in added)
    labels = "、".join(str(entry.get("label") or "") for entry in added)
    _c._append_runtime_technical_log(
        job_id, "translation scope: planned pre-start exclusion of "
        f"{planned_segments} paragraph(s) ({labels[:80]}); the exclusion "
        "ledger is written when translation units are built")
    _c.save_job_state(job_id, state)
    return {
        "planned": len(added),
        "planned_segments": planned_segments,
        "excluded": 0,
        "remaining": _planned_paragraph_total(state),
        "excluded_ids": [entry["excluded_id"] for entry in added],
        "state": state,
    }


def _unplan_scope_exclusion(state, job_id, wanted, *, actor):
    """撤下译前排除计划里的条目（恢复入口与译后共用同一个对话框）。"""
    plan = state.get(SCOPE_PLAN_KEY)
    entries = plan.get("entries") if isinstance(plan, dict) else None
    by_id = {str(entry.get("excluded_id") or ""): entry
             for entry in entries or [] if isinstance(entry, dict)}
    missing = [item for item in wanted if item not in by_id]
    if missing:
        raise ValueError("找不到要恢复的已排除段落（可能已被恢复）。")
    state[SCOPE_PLAN_KEY] = {
        **plan,
        "entries": [entry for entry in plan["entries"]
                    if str(entry.get("excluded_id") or "") not in set(wanted)],
        "updated_at": _finalization.now_iso(),
    }
    _c._append_runtime_technical_log(
        job_id, "translation scope: removed "
        f"{len(wanted)} pre-start exclusion plan entrie(s) by {actor}")
    _c.save_job_state(job_id, state)
    return {
        "restored": len(wanted),
        "unplanned": len(wanted),
        "remaining": (len(_segment_source_list(state)) if state.get("p1_done")
                      else _planned_paragraph_total(state)),
        "state": state,
    }


def _raw_to_cleaned_map(state, raw_count, cleaned_count):
    """原始提取段落（大纲坐标）→ 清洗后段落（翻译契约坐标）的索引映射。

    非整理路径一一对应；整理过的 PDF 用 ``source_cleanup.output_input_indices``
    （清洗后段 → 原始段列表）的逆映射。两条线索都不成立时返回 ``None``：
    映射不出来就不动，绝不按比例猜。
    """
    output_inputs = (state.get("source_cleanup") or {}).get("output_input_indices")
    if isinstance(output_inputs, list) and output_inputs:
        mapping = {}
        for cleaned_index, inputs in enumerate(output_inputs):
            if not isinstance(inputs, list):
                continue
            for raw_index in inputs:
                if isinstance(raw_index, int) and 0 <= raw_index < raw_count:
                    mapping.setdefault(raw_index, cleaned_index)
        return mapping or None
    if raw_count == cleaned_count:
        return {index: index for index in range(raw_count)}
    return None


def _plan_entry_raw_span(entry, raw):
    """锚点验证后的原始段落区间；大纲与导入结构对不上时返回 ``None``（不猜）。

    首尾锚点必须仍在原始提取段的计划位置上（大纲与导入共用同一条文字层
    提取路径，锚点不一致说明两边读出了不同结构）。
    """
    start = int(entry.get("para_start") or 0)
    end = int(entry.get("para_end") or 0)
    if not 0 <= start < end <= len(raw):
        return None
    anchor_first = str(entry.get("anchor_first") or "")
    anchor_last = str(entry.get("anchor_last") or "")
    if anchor_first and str(raw[start] or "") != anchor_first:
        return None
    if anchor_last and str(raw[end - 1] or "") != anchor_last:
        return None
    return start, end


def _plan_entry_cleaned_units(span, raw_to_cleaned):
    """原始段落区间映射到的清洗后段落集合；映射不完整时 ``None``。"""
    if raw_to_cleaned is None:
        return None
    start, end = span
    cleaned_indexes = set()
    for raw_index in range(start, end):
        cleaned_index = raw_to_cleaned.get(raw_index)
        if cleaned_index is None:
            return None
        cleaned_indexes.add(cleaned_index)
    return cleaned_indexes


def _cleaned_unit_inputs(raw_to_cleaned):
    """清洗后段落 → 组成它的原始段落集合（``raw_to_cleaned`` 的逆映射）。"""
    inputs_by_unit = {}
    for raw_index, cleaned_index in raw_to_cleaned.items():
        inputs_by_unit.setdefault(cleaned_index, set()).add(raw_index)
    return inputs_by_unit


def _plan_entry_segment_indexes(units, ranges, segment_count):
    """清洗后段落集合 → 翻译单元索引（经 ``paragraph_ranges``）。"""
    indexes = set()
    for row in ranges:
        if not isinstance(row, dict):
            continue
        paragraph_index = row.get("paragraph_index")
        span_start, span_end = row.get("start"), row.get("end")
        if not isinstance(paragraph_index, int) \
                or paragraph_index not in units:
            continue
        if not (isinstance(span_start, int) and isinstance(span_end, int)):
            continue
        indexes.update(range(span_start, span_end))
    indexes = {index for index in indexes if 0 <= index < segment_count}
    return indexes or None


def apply_scope_exclusion_plan(job_id, state):
    """翻译单元建好那一刻，把译前范围排除计划落进排除账本。

    只在 ``pairs`` 尚不存在、位置契约尚未建立时调用（与
    ``_auto_exclude_non_translatable_segments`` 同一时刻，紧随其后）。映射链：
    大纲段落（锚点验证）→ 清洗后段落 → ``segmentation.paragraph_ranges`` 的
    翻译单元区间；映射不到的条目保持未应用并写入警告与技术日志——绝不猜位置、
    绝不静默丢弃。应用与未应用都留 ``scope_exclusion_applied`` 审计；计划与
    大纲随之清出 state，未应用的计划保留供用户撤下；之后范围入口回到工作列表口径。
    不落盘：调用方随阶段
    收尾统一 ``save_job_state``。
    """
    entries = _scope_plan_entries(state)
    if not entries:
        state.pop(SCOPE_PLAN_KEY, None)
        state.pop("source_outline", None)
        return {"applied": [], "unapplied": []}
    if state.get("pairs"):
        # 位置契约已存在：事后静默位移会打乱已持久化的发现与审校引用，
        # 计划保持未应用并如实上报。
        return {"applied": [], "unapplied": entries}
    raw = [str(item or "") for item in state.get("source_paras") or []]
    cleaned_count = len(state.get("source_paragraphs") or [])
    raw_to_cleaned = _raw_to_cleaned_map(state, len(raw), cleaned_count)
    ranges = (state.get("segmentation") or {}).get("paragraph_ranges") or []
    sources = _segment_source_list(state)

    # 阶段一（映射）：每个条目的翻译单元索引都对着**移除之前**的原始序列计算。
    # 多个条目必须先全部映射完再动列表——逐条"边映射边删除"会让后续条目的
    # 索引全部错位（账本记了四段，正文却只移走一半）。
    planned = []
    unapplied = []
    for entry in entries:
        span = _plan_entry_raw_span(entry, raw)
        if span is None:
            unapplied.append(entry)
            continue
        units = _plan_entry_cleaned_units(span, raw_to_cleaned)
        if units is None:
            unapplied.append(entry)
            continue
        planned.append((entry, span, units))
    # 清洗合并可能跨越排除边界：一个清洗后段落同时含选中的原始段与**用户
    # 保留**的原始段时，删掉它就会误伤保留内容。判定以全部条目的并集为准
    # ——两条目共同覆盖一个合并单元是合法的；任何一条自身的覆盖不完整，
    # 整条按未应用上报（带警告），绝不部分删除一个翻译单元。
    union_raws = set()
    for _entry, (span_start, span_end), _units in planned:
        union_raws.update(range(span_start, span_end))
    inputs_by_unit = _cleaned_unit_inputs(raw_to_cleaned) \
        if raw_to_cleaned else {}
    entry_indexes = {}
    partial_coverage = []
    for entry, _span, units in planned:
        if raw_to_cleaned is None or not units <= set(inputs_by_unit) \
                or any(inputs_by_unit[unit] - union_raws for unit in units):
            unapplied.append(entry)
            partial_coverage.append(entry)
            continue
        indexes = _plan_entry_segment_indexes(
            units, ranges, len(sources))
        if indexes is None:
            unapplied.append(entry)
            continue
        entry_indexes[str(entry.get("key") or "")] = indexes
    # 条目按文档顺序认领索引；条目间区间重叠时（不该发生，防御性处理）
    # 已被前面条目认领的段落不再重复排除。
    claimed = set()
    for entry in entries:
        indexes = entry_indexes.get(str(entry.get("key") or ""))
        if indexes is None:
            continue
        entry_indexes[str(entry.get("key") or "")] = indexes - claimed
        claimed |= indexes

    # 阶段二（移除）：全部索引合并后**一次**从大到小移出，锚点都取自原始序列。
    records_by_entry = {key: [] for key in entry_indexes}
    for index in sorted(claimed, reverse=True):
        entry_key = next(key for key, indexes in entry_indexes.items()
                         if index in indexes)
        entry = next(item for item in entries
                     if str(item.get("key") or "") == entry_key)
        records_by_entry[entry_key].append(_excluded_segment_record(
            state, index,
            reason=str(entry.get("reason")
                       or f"范围排除（译前）：{entry.get('label')}"),
            actor=str(entry.get("planned_by") or "user"),
            source=str(sources[index] or ""),
            target="",
            pair=None, in_pairs=False,
            before_source=sources[index - 1] if index > 0 else "",
            after_source=(sources[index + 1]
                          if index + 1 < len(sources) else ""),
            node_id=entry.get("node_id") or (entry_key.split(":", 1)[1] if str(entry_key).startswith("outline:") else None),
            scope_key=entry_key))
        _drop_segment_at(state, index)
    ledger = list(state.get("excluded_segments") or [])
    applied_records = []
    for entry in entries:
        entry_key = str(entry.get("key") or "")
        records = records_by_entry.get(entry_key) or []
        if not records:
            continue
        records.sort(key=lambda record: int(record.get("index") or 0))
        ledger.extend(records)
        applied_records.append({
            "key": entry_key,
            "label": str(entry.get("label") or ""),
            "excluded_ids": [record["excluded_id"] for record in records],
            "excluded_count": len(records),
            "planned_at": str(entry.get("planned_at") or ""),
            "applied_at": _finalization.now_iso(),
        })
    if ledger != list(state.get("excluded_segments") or []):
        state["excluded_segments"] = ledger
    audit = list(state.get("scope_exclusion_applied") or []) + applied_records
    partial_keys = {str(entry.get("key") or "") for entry in partial_coverage}
    if unapplied:
        def _reason_of(entry):
            if str(entry.get("key") or "") in partial_keys:
                return ("清洗把选中的内容与保留的内容合并成了同一段，"
                        "为避免误删保留内容，请重新勾选或调整切分")
            return "导入结构与本地大纲不一致"
        labels = "、".join(str(entry.get("label") or entry.get("key") or "")
                           for entry in unapplied)
        warning = f"译前范围排除未能应用（{_reason_of(unapplied[0])}）：{labels}"
        warnings = state.setdefault("warnings", [])
        if warning not in warnings:
            warnings.append(warning)
        audit.append({
            "unapplied": True,
            "labels": labels,
            "count": len(unapplied),
            "partial_coverage": sorted(partial_keys),
            "recorded_at": _finalization.now_iso(),
        })
    state["scope_exclusion_applied"] = audit[-50:]
    if unapplied:
        state[SCOPE_PLAN_KEY] = {**state[SCOPE_PLAN_KEY], "entries": unapplied}
    else:
        state.pop(SCOPE_PLAN_KEY, None)
    state.pop("source_outline", None)
    for item in applied_records:
        _c._append_runtime_technical_log(
            job_id, "translation scope: applied pre-start exclusion "
            f"({item['label']}): {item['excluded_count']} segment(s) excluded")
    for entry in unapplied:
        _c._append_runtime_technical_log(
            job_id, "translation scope: could not apply pre-start exclusion "
            f"({entry.get('label')}); {_reason_of(entry)}")
    return {"applied": applied_records, "unapplied": unapplied}


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
    # Keep pair attribution historical; resolve merged IDs through the recorded
    # human decisions instead of rewriting the glossary version used by a pair.
    redirects = {}
    for action in state.get('human_actions') or []:
        if not isinstance(action, dict) or action.get('action') != 'glossary_entries_merged':
            continue
        originals = action.get('merged_originals') or []
        if len(originals) == 2 and all(isinstance(item, dict) for item in originals):
            old_id, new_id = str(originals[1].get('id') or ''), str(originals[0].get('id') or '')
            if old_id and new_id and old_id != new_id:
                redirects[old_id] = new_id
    for old_id in list(ids):
        seen = set()
        current = old_id
        while current in redirects and current not in seen:
            seen.add(current)
            current = redirects[current]
        ids.add(current)
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
    if not pairs and not paras and operation != "include":
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
        # 人工排除与系统排除走同一套位置契约：锚点、标记与段落区间一起前移，
        # 否则补读的页按页序排回时会因区间与译文数量不一致而失败。
        _drop_positional_artifacts(state, index, len(paras))
        affected_hint = [max(0, min(index, len(pairs) - 1))] if pairs else []
    elif operation == "include":
        excluded_record = dict(include_record)
        _restore_positional_artifacts(state, index, excluded_record)
        state["excluded_segments"] = [
            item for item in state.get("excluded_segments") or []
            if not (isinstance(item, dict)
                    and str(item.get("excluded_id") or "")
                    == str(excluded_record.get("excluded_id") or ""))
        ]
        # 排除发生在翻译之前时没有 pair 记录，此时只需要把原文插回 paras。
        restored_pair = excluded_record.get("pair")
        if restored_pair is None and pairs:
            restored_pair = {"source": str(excluded_record.get("source") or ""),
                             "target": "", "initial_target": "",
                             "target_provenance": "generated", "from_tm": False}
        if isinstance(restored_pair, dict):
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

    operation = str(record.get("operation") or "")
    if operation == "exclude":
        added = set(record.get("excluded_ids_added") or [])
        excluded = next((item for item in state.get("excluded_segments") or []
                         if isinstance(item, dict) and item.get("excluded_id") in added), None)
        if excluded is not None:
            _restore_positional_artifacts(state, undo_index, excluded)
    elif operation == "include":
        restored_records = record.get("excluded_records_restored") or []
        if restored_records and isinstance(restored_records[0].get("positional_artifacts"), dict):
            _drop_positional_artifacts(state, undo_index, len(paras) - 1)

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
    """Restore the text that existed before the first edit of this segment.

    Undo is a **new** CURRENT_TRANSLATION change, not a rollback of history:

    * the text goes back, and the change is recorded like any other mutation
      (``action="translation_restore"``) -- the edit it undoes stays in the log;
    * ``reviewed`` / ``review_status`` / ``from_tm`` are **not** restored.  The
      reviewed marker described the text on screen during that review, and the
      TM entry was discarded when the edit invalidated it.  Bringing either
      back would claim a verification that never happened for this text, and
      would hand the translator memory a final entry that was never re-checked.
    """
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
    pair["reviewed"] = False
    pair["review_status"] = "not_reviewed"
    # Provenance describes who wrote the text that is current now, and stays in
    # the vocabulary the rest of the pipeline understands.
    restored_from = str(restore.get("target_provenance") or "")
    pair["target_provenance"] = "human_edit" if restored_from in {
        "human_edit", "reviewed", "tm_approved"} else "generated"
    pair["from_tm"] = False
    pair.pop("human_edited", None)
    pair.pop("adopted_candidate_id", None)
    _c._recount_reviewed_segments(state)

    _c._mark_translation_truth_changed(
        job_id, state, [index], "撤销修改也是一次新的译文变更；相关下游需要重新验证",
        actor=actor, action="translation_restore")
    _c.save_job_state(job_id, state)
    return state
