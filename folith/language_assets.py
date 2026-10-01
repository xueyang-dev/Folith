"""Language Assets 的只读视图模型：术语 / 翻译记忆 / 待审核候选。

这个模块只做一件事：把**已经落盘**的运行时状态投影成高密度的行模型，供
「术语与翻译记忆」工作区渲染。它是纯函数层，不 import Streamlit，不写任何
东西，也不发明后端无法证明的字段。

诚实性原则（本模块的硬约束）：

- 术语的「使用次数」= ``occurrences`` 的长度，不是猜测的引用计数；
- 翻译记忆条目只有 ``target`` / ``reviewed`` / ``updated_at`` 三个字段，
  因此**不提供**来源文档、出现次数或 fuzzy match 百分比——后端没有这些数据；
- 候选的「出现位置」优先用 ``occurrences``（真实段落命中），缺失时退回
  ``observed_segments``（观察到的段落），并明确区分「出现次数未知」；
- 作用域会区分 ``project`` / ``global`` / ``document`` / ``section:<id>`` /
  ``segment:<id>`` 以及历史自由文本；项目级条目只在调用方提供了真实项目记忆
  时进入行模型，不把任务条目冒充成项目记忆。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import knowledge, models
from .translation_memory import tm_unscope_key


# ---------------- 标签 ----------------

TERM_STATUS_LABELS = {
    "candidate": "候选",
    "provisional": "暂定",
    "locked": "已锁定",
    "rejected": "已拒绝",
}

TERM_BEHAVIOR_LABELS = {"translate": "翻译", "preserve": "保留原文"}

# 实体类观察（knowledge.observe_batch 会把这些 kind 归为 entity_observation，
# 不进术语候选队列，但历史状态里可能残留）。
_ENTITY_KINDS = {
    "name", "person", "place", "organization", "artwork", "book",
    "article", "film", "project", "named_concept", "named_object",
}

CANDIDATE_KIND_LABELS = {
    "term": "术语",
    "expression": "固定表达",
    "name": "专名",
}

# 过滤器选项（全部由真实字段支撑）。
SCOPE_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部"),
    ("project", "本项目"),
    ("document", "文档级"),
    ("global", "全局"),
)
# 注意：这些标签会出现在窄的 selectbox 里，长度必须短；维度名由 selectbox
# 自己的 label（置信度 / 候选类型）承担，所以这里不再重复「全部XX」。
CONFIDENCE_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部"),
    ("high", "高置信度"),
    ("low", "低置信度"),
    ("unknown", "未知"),
)
KIND_FILTERS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部"),
    ("term", "术语"),
    ("expression", "固定表达"),
    ("name", "专名"),
)

# 高置信度阈值：knowledge._make_candidate 只产出 0.35 / 0.7 两档，取 0.70。
HIGH_CONFIDENCE_THRESHOLD = 0.70

TERM_TABS: Tuple[Tuple[str, str], ...] = (
    ("terms", "术语库"),
    ("tm", "翻译记忆"),
    ("review", "待审核"),
)
VALID_TABS = tuple(key for key, _ in TERM_TABS)


def kind_label(kind: Any) -> str:
    """候选类型的人类标签；实体类统一叫「专名」。"""
    text = str(kind or "term").strip().casefold()
    if text in _ENTITY_KINDS:
        return CANDIDATE_KIND_LABELS["name"]
    return CANDIDATE_KIND_LABELS.get(text, text or "术语")


def status_label(status: Any) -> str:
    return TERM_STATUS_LABELS.get(str(status or "").strip().casefold(), "—")


def behavior_label(behavior: Any) -> str:
    return TERM_BEHAVIOR_LABELS.get(str(behavior or "").strip().casefold(), "—")


def scope_bucket(scope: Any) -> str:
    """把作用域收敛成列表过滤器使用的真实桶。"""
    text = str(scope or "").strip().casefold()
    if text in {"project", "项目", "project_term", "项目记忆"}:
        return "project"
    if text in {"global", "全局"}:
        return "global"
    return "document"


def scope_label(scope: Any) -> str:
    """作用域的人类标签；未知自由文本原样展示，不硬塞进错误的桶。"""
    raw = str(scope or "").strip()
    low = raw.casefold()
    if not raw or low in {"document", "文档"}:
        return "本文档"
    if low in {"project", "项目", "project_term", "项目记忆"}:
        return "本项目"
    if low in {"global", "全局"}:
        return "全局"
    if low.startswith("section:"):
        return f"章节 · {raw[8:]}"
    if low.startswith("segment:"):
        return f"段落 · {raw[8:]}"
    return raw


# ---------------- 段落引用（真实章节标签） ----------------

def section_index(state: Mapping[str, Any]) -> List[Tuple[int, int, str]]:
    """从 ``semantic_units`` 提取 (start, end, label)，按起点排序。"""
    units = (state or {}).get("semantic_units") or []
    index: List[Tuple[int, int, str]] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        start, end = unit.get("start_segment"), unit.get("end_segment")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        label = str(unit.get("label") or unit.get("title") or "").strip()
        index.append((start, end, label))
    index.sort(key=lambda item: item[0])
    return index


def segment_reference(state: Mapping[str, Any], segment_id: Any) -> str:
    """段落的人类引用：有章节标签时是「章节 · #123」，否则退回「#123」。

    段号按 1 起算展示（与工作台一致），但**不做任何跳转**——跳转由 app 层
    通过既有的 ``selected_segment_id`` 机制完成。
    """
    if isinstance(segment_id, bool) or not isinstance(segment_id, int) \
            or segment_id < 0:
        return ""
    label = ""
    for start, end, text in section_index(state):
        if start <= segment_id <= end:
            label = text
            break
    position = f"#{segment_id + 1}"
    return f"{label} · {position}" if label else position


# ---------------- 术语 ----------------

def _glossary_entries(state: Mapping[str, Any]) -> List[Any]:
    """任务术语的权威来源：草稿 glossary → 冻结版本 → 旧的 auto_terms。"""
    entries = state.get("glossary")
    if isinstance(entries, list) and entries:
        return entries
    frozen = state.get("glossary_frozen")
    if isinstance(frozen, Mapping) and frozen.get("entries"):
        return list(frozen["entries"])
    auto = state.get("auto_terms")
    if isinstance(auto, Mapping) and auto:
        return [{"id": f"auto-{index}", "source": source,
                 "target": value if isinstance(value, str) else "",
                 "preferred": value if isinstance(value, str) else "",
                 "status": "provisional"}
                for index, (source, value) in enumerate(auto.items())]
    return []


def _frozen_version(state: Mapping[str, Any]) -> Any:
    frozen = state.get("glossary_frozen")
    if isinstance(frozen, Mapping):
        return frozen.get("version")
    return None


def build_term_rows(jobs: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """每个「任务 × 术语条目」一行；聚合交给 :func:`group_terms`。"""
    rows: List[Dict[str, Any]] = []
    for job in jobs or []:
        state = job.get("state") or {}
        if not isinstance(state, Mapping):
            continue
        job_id = str(job.get("job_id") or "")
        document = str(state.get("filename") or "")
        config = state.get("pipeline_config")
        target_lang = str((config or {}).get("target_lang") or "") \
            if isinstance(config, Mapping) else ""
        version = _frozen_version(state)
        for entry in models.normalize_glossary(_glossary_entries(state)):
            occurrences = list(entry.get("occurrences") or [])
            rows.append({
                "entry_id": entry["id"],
                "job_id": job_id,
                "project_id": str(state.get("project_id") or ""),
                "project_name": str(state.get("project_name") or ""),
                "document": document,
                "target_lang": target_lang,
                "frozen_version": version,
                "source": entry["source"],
                "preferred": entry.get("preferred") or entry.get("target") or "",
                "proposed_target": entry.get("proposed_target") or "",
                "forbidden": list(entry.get("forbidden") or []),
                "domain": entry.get("domain") or "",
                "scope": entry.get("scope") or "",
                "scope_bucket": scope_bucket(entry.get("scope")),
                "scope_label": scope_label(entry.get("scope")),
                "behavior": entry.get("behavior") or "translate",
                "status": entry.get("status") or "provisional",
                "usage": len(occurrences),
                "occurrences": occurrences,
                "note": entry.get("note") or "",
                "evidence": list(entry.get("evidence") or []),
            })
    return rows


def build_project_term_rows(
    projects: Sequence[Mapping[str, Any]],
    *,
    task_rows: Sequence[Mapping[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """把已持久化的项目术语投影成术语行。

    项目文件中的术语没有文档 occurrence；使用次数只能从同一批任务的真实
    occurrence 汇总得到，缺失时保持 0。项目条目仍然保留独立的
    ``project_id`` / ``project_name``，供 Inspector 说明来源。
    """
    rows: List[Dict[str, Any]] = []
    for project in projects or ():
        if not isinstance(project, Mapping):
            continue
        project_id = str(project.get("project_id") or "").strip()
        if not project_id:
            continue
        project_name = str(project.get("name") or "未命名项目").strip()
        for entry in models.normalize_glossary(project.get("glossary") or []):
            source = str(entry.get("source") or "").strip()
            preferred = str(entry.get("preferred") or entry.get("target") or "").strip()
            if not source or not preferred:
                continue
            rows.append({
                "entry_id": entry["id"],
                "job_id": "",
                "project_id": project_id,
                "project_name": project_name,
                "document": "",
                "target_lang": "",
                "frozen_version": (
                    (project.get("glossary_versions") or [{}])[-1].get("version")
                    if isinstance(project.get("glossary_versions"), list)
                    and project.get("glossary_versions")
                    and isinstance((project.get("glossary_versions") or [{}])[-1], Mapping)
                    else None
                ),
                "source": source,
                "preferred": preferred,
                "proposed_target": entry.get("proposed_target") or "",
                "forbidden": list(entry.get("forbidden") or []),
                "domain": entry.get("domain") or "",
                "scope": "project",
                "scope_bucket": "project",
                "scope_label": f"项目 · {project_name}",
                "behavior": entry.get("behavior") or "translate",
                "status": entry.get("status") or "locked",
                # 任务 occurrence 会在同一 source→preferred 的 group 中汇总；
                # 这里不能再把同一批任务的计数加一遍。项目独有条目没有真实
                # occurrence，因此保持 0，而不是估算使用次数。
                "usage": 0,
                "occurrences": [],
                "note": entry.get("note") or "",
                "evidence": list(entry.get("evidence") or []),
            })
    return rows


def _term_row_id(source: str, preferred: str) -> str:
    return f"term::{source.casefold()}::{preferred.casefold()}"


def group_terms(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """把跨任务相同的 source → preferred 合并成一行，并保留每个任务的明细。

    合并只影响展示：``tasks`` 保留每个任务的原始行，因此 Inspector 能显示
    「出现在哪些任务」，编辑/删除也仍然作用在**具体某个任务**上。
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        key = _term_row_id(str(row.get("source") or ""),
                           str(row.get("preferred") or ""))
        item = grouped.get(key)
        if item is None:
            item = dict(row)
            item["row_id"] = key
            item["tasks"] = []
            item["project_sources"] = []
            item["project_ids"] = []
            item["project_names"] = []
            item["usage"] = 0
            item["documents"] = []
            grouped[key] = item
            order.append(key)
        row_copy = dict(row)
        if row_copy.get("project_id") and not row_copy.get("job_id"):
            item["project_sources"].append(row_copy)
            project_id = str(row_copy.get("project_id") or "")
            if project_id and project_id not in item["project_ids"]:
                item["project_ids"].append(project_id)
            project_name = str(row_copy.get("project_name") or "").strip()
            if project_name and project_name not in item["project_names"]:
                item["project_names"].append(project_name)
        else:
            item["tasks"].append(row_copy)
        if row_copy.get("scope_bucket") == "project":
            project_id = str(row_copy.get("project_id") or "")
            if project_id and project_id not in item["project_ids"]:
                item["project_ids"].append(project_id)
            project_name = str(row_copy.get("project_name") or "").strip()
            if project_name and project_name not in item["project_names"]:
                item["project_names"].append(project_name)
        item["usage"] += int(row.get("usage") or 0)
        document = str(row.get("document") or "")
        if document and document not in item["documents"]:
            item["documents"].append(document)
        # 合并后的展示字段取「最强」的那个：已锁定 > 暂定 > 候选 > 已拒绝。
        if _status_rank(row.get("status")) < _status_rank(item.get("status")):
            for field in ("status", "preferred", "proposed_target", "behavior"):
                item[field] = row.get(field)
        if not item.get("domain") and row.get("domain"):
            item["domain"] = row.get("domain")
        if not item.get("target_lang") and row.get("target_lang"):
            item["target_lang"] = row.get("target_lang")
        if row.get("scope_bucket") == "project":
            item["scope_bucket"] = "project"
            names = item.get("project_names") or []
            item["scope_label"] = (
                f"项目 · {names[0]}" if len(names) == 1
                else "多个项目" if len(names) > 1 else "本项目")
        elif row.get("scope_bucket") == "global" \
                and item.get("scope_bucket") != "project":
            item["scope_bucket"] = "global"
            item["scope_label"] = "全局"
        if row.get("forbidden"):
            merged = list(dict.fromkeys([*(item.get("forbidden") or []),
                                         *row["forbidden"]]))
            item["forbidden"] = merged
    result = [grouped[key] for key in order]
    result.sort(key=lambda item: (-int(item.get("usage") or 0),
                                 str(item.get("source") or "").casefold()))
    return result


_STATUS_RANK = {"locked": 0, "provisional": 1, "candidate": 2, "rejected": 3}


def _status_rank(status: Any) -> int:
    return _STATUS_RANK.get(str(status or "").strip().casefold(), 4)


# ---------------- 翻译记忆 ----------------

def build_tm_rows(tm: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """翻译记忆行。字段**只有**后端真正存下来的那些。

    只收 ``reviewed`` 为真的条目：界面把它们标成「已确认」，未确认的条目
    一旦出现在这里，那个标签就成了假话。`core.load_tm` 已经做过同样的过滤，
    这里再挡一次，保证行模型自身是诚实的。

    记忆键是**作用域键**（目标语言 + 原文），因此展示前必须拆开：界面上要看到
    的是"原文 / 译文 / 目标语言"，而不是内部键的拼接形态。语言无法证明的旧
    条目照常展示（它是用户的真实数据），但 ``target_lang`` 为空——界面据此
    说明"这条记忆不会自动复用"，而不是假装它属于某种语言。
    """
    rows: List[Dict[str, Any]] = []
    for index, (key, record) in enumerate((tm or {}).items()):
        if not isinstance(record, Mapping) or not record.get("reviewed"):
            continue
        target = str(record.get("target") or "")
        head, text = tm_unscope_key(key)
        if not text.strip() or not target.strip():
            continue
        rows.append({
            "row_id": f"tm::{index}",
            "source": text,
            "target": target,
            "target_lang": str(record.get("target_lang") or "") or head,
            "updated_at": str(record.get("updated_at") or ""),
            "reviewed": bool(record.get("reviewed")),
            "source_chars": len(text),
        })
    rows.sort(key=lambda row: (row["updated_at"], row["source"]), reverse=True)
    return rows


# ---------------- 待审核候选 ----------------

def build_candidate_rows(
    jobs: Sequence[Mapping[str, Any]], *, include_decided: bool = False,
) -> List[Dict[str, Any]]:
    """待审核候选行；``decision`` 非空的条目默认不出现（已处理）。"""
    rows: List[Dict[str, Any]] = []
    for job in jobs or []:
        state = job.get("state") or {}
        if not isinstance(state, Mapping):
            continue
        job_id = str(job.get("job_id") or "")
        document = str(state.get("filename") or "")
        for candidate in state.get("knowledge_candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            if candidate.get("decision") and not include_decided:
                continue
            context = knowledge.candidate_context(dict(candidate), dict(state))
            occurrences = [item for item in context.get("occurrences") or []
                           if isinstance(item, int) and not isinstance(item, bool)]
            observed = [item for item in context.get("observed_segments") or []
                        if isinstance(item, int) and not isinstance(item, bool)]
            positions_source = occurrences or observed
            # 去重按**真实段落下标**，而不是按显示文案；同时保留下标本身，
            # 让 UI 能跳回翻译工作台的对应 segment（下标与 pairs 下标同源）。
            unique_segments = list(dict.fromkeys(positions_source))
            position_entries = []
            for segment in unique_segments:
                label = segment_reference(state, segment)
                if label:
                    position_entries.append({"label": label, "index": segment})
            # 列表级只带前 12 条标签（够用且省），Inspector 用完整的
            # position_entries 展开，两者不要互相撒谎。
            positions = [entry["label"] for entry in position_entries[:12]]
            first = context.get("first_observed_segment")
            confidence = _as_float(context.get("confidence"))
            conflicts = list(context.get("conflicts") or [])
            rows.append({
                "row_id": f"cand::{job_id}::{context['candidate_id']}",
                "candidate_id": context["candidate_id"],
                "job_id": job_id,
                "document": document,
                "source": context["source"],
                "target": context["proposed_target"],
                "kind": context.get("kind") or "term",
                "kind_label": kind_label(context.get("kind")),
                # 「新术语」是一个可复核的关系派生字段：候选 source 在当前任务
                # 的真实术语表里完全不存在，而不是把 kind=term 偷换成「新」。
                "is_new": not bool(context.get("existing_entries")),
                "confidence": confidence,
                "high_confidence": bool(confidence is not None
                                        and confidence >= HIGH_CONFIDENCE_THRESHOLD),
                "occurrences": occurrences,
                "observed_segments": observed,
                "occurrence_count": len(occurrences),
                "positions_known": bool(occurrences),
                "positions": positions,
                "position_entries": position_entries,
                # 真实去重后的段落总数（不受 12 条展示上限影响），
                # 否则「查看全部出现位置（N）」会少报。
                "position_total": len(unique_segments),
                "first_observed_segment": first,
                "first_reference": segment_reference(state, first),
                "source_context": str(context.get("source_context") or ""),
                "target_context": str(context.get("target_context") or ""),
                "conflicts": conflicts,
                "has_conflict": bool(conflicts),
                "conflict_summary": _conflict_summary(conflicts),
                "origin": str(context.get("origin") or ""),
                "decision": context.get("decision"),
            })
    rows.sort(key=lambda row: (
        -int(row.get("occurrence_count") or 0),
        str(row.get("source") or "").casefold(),
    ))
    return rows


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _conflict_summary(conflicts: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for item in conflicts:
        target = str(item.get("target") or "").strip() or "（空）"
        label = status_label(item.get("status"))
        parts.append(f"{target}（{label}）")
    return "；".join(parts)


# ---------------- 摘要 ----------------

def language_asset_summary(
    jobs: Sequence[Mapping[str, Any]], tm_count: int,
) -> Dict[str, int]:
    """顶部摘要条。每个数字都来自真实数据源。"""
    terms = group_terms(build_term_rows(jobs))
    candidates = build_candidate_rows(jobs)
    return {
        "terms": len(terms),
        "tm": int(tm_count or 0),
        "review": len(candidates),
        "conflicts": sum(1 for row in candidates if row["has_conflict"]),
    }


# ---------------- 过滤 ----------------

def _matches(query: str, *values: Any) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    haystack = " ".join(str(value or "") for value in values).casefold()
    return needle in haystack


def filter_terms(
    rows: Sequence[Mapping[str, Any]], *, query: str = "", scope: str = "all",
    domain: str = "", status: str = "", target_lang: str = "",
) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        if scope in {"project", "document", "global"} \
                and row.get("scope_bucket") != scope:
            continue
        if domain and str(row.get("domain") or "") != domain:
            continue
        if status and str(row.get("status") or "") != status:
            continue
        if target_lang and str(row.get("target_lang") or "") != target_lang:
            continue
        if not _matches(query, row.get("source"), row.get("preferred"),
                        row.get("proposed_target"), row.get("domain"),
                        row.get("note"), row.get("document"),
                        " ".join(row.get("forbidden") or [])):
            continue
        result.append(dict(row))
    return result


def filter_tm(rows: Sequence[Mapping[str, Any]], *, query: str = "",
              target_lang: str = "") -> List[Dict[str, Any]]:
    """翻译记忆筛选：关键词 + 目标语言。

    目标语言是**记忆身份**的一部分，因此它既是筛选项，也是"为什么这条记忆
    不会被另一种语言的任务命中"的答案。
    """
    result = []
    for row in rows:
        if target_lang and str(row.get("target_lang") or "") != target_lang:
            continue
        if _matches(query, row.get("source"), row.get("target")):
            result.append(dict(row))
    return result


def filter_candidates(
    rows: Sequence[Mapping[str, Any]], *, query: str = "", kind: str = "",
    document: str = "", confidence: str = "all", only_conflicts: bool = False,
    only_high: bool = False, only_new: bool = False,
) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        if kind and kind != "all" and str(row.get("kind") or "") != kind:
            continue
        if document and str(row.get("document") or "") != document:
            continue
        if only_conflicts and not row.get("has_conflict"):
            continue
        if only_new and not row.get("is_new"):
            continue
        if only_high and not row.get("high_confidence"):
            continue
        if confidence == "high" and not row.get("high_confidence"):
            continue
        if confidence == "low" and (
                row.get("confidence") is None or row.get("high_confidence")):
            continue
        if confidence == "unknown" and row.get("confidence") is not None:
            continue
        if not _matches(query, row.get("source"), row.get("target"),
                        row.get("document"), row.get("source_context"),
                        row.get("target_context"),
                        " ".join(row.get("positions") or [])):
            continue
        result.append(dict(row))
    return result


def confidence_text(row: Mapping[str, Any]) -> str:
    value = row.get("confidence")
    return f"{float(value):.2f}" if isinstance(value, (int, float)) else "未知"


def usage_text(row: Mapping[str, Any]) -> str:
    """候选的「出现」文案：只有真实段落命中才敢说次数。"""
    if row.get("positions_known"):
        return f"出现 {int(row.get('occurrence_count') or 0)} 次"
    observed = len(row.get("observed_segments") or [])
    if observed:
        return f"观察到 {observed} 次"
    return "出现次数未知"


def group_candidates_by_document(
    rows: Sequence[Mapping[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """按来源文档分组，保持传入顺序（调用方已按出现次数排序）。"""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for row in rows:
        key = str(row.get("document") or "未命名文档")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(dict(row))
    return [(key, buckets[key]) for key in order]


def document_options(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    seen: List[str] = []
    for row in rows:
        name = str(row.get("document") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return sorted(seen)


def domain_options(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    return sorted({str(row.get("domain") or "").strip()
                   for row in rows if str(row.get("domain") or "").strip()})


def target_language_options(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    """返回术语任务实际持久化的目标语言，不把它冒充成完整语言对。"""
    return sorted({str(row.get("target_lang") or "").strip()
                   for row in rows if str(row.get("target_lang") or "").strip()})
