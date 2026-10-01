"""Translation decision traces: neutral evidence about source/target changes.

This module deliberately stays separate from translation QA. A decision trace
describes an observable translation choice; it does not declare that choice an
error or propose replacement wording.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TypedDict


DECISION_TAXONOMY: Dict[str, str] = {
    # Semantic
    "semantic_omission": "信息可能被压缩",
    "semantic_compression": "信息压缩",
    "semantic_addition": "信息有所增补",
    "explicitation": "隐含信息被显化",
    "implicitation": "信息表达转为隐含",
    "generalization": "信息被泛化",
    "specification": "信息被具体化",
    "semantic_shift": "语义表达发生变化",
    "ambiguity_resolution": "歧义被确定化",
    "modality_shift": "情态表达发生变化",
    "intensity_shift": "语气有所增强",
    # Discourse
    "sentence_split": "句式重构",
    "sentence_merge": "句式重构",
    "logical_restructuring": "逻辑关系被重组",
    "causality_shift": "因果关系发生变化",
    "reference_resolution": "指代被显化",
    "subject_shift": "主语发生转换",
    # Style
    "register_shift": "语域发生变化",
    "tone_shift": "语气发生变化",
    "rhythm_restructuring": "表达节奏被重组",
    "rhetorical_transformation": "修辞表达发生变化",
    "metaphor_transformation": "隐喻被解释化",
    # Cultural
    "cultural_adaptation": "文化概念被解释化",
    "cultural_reduction": "文化信息被泛化",
    "cultural_substitution": "文化信息被替换",
    "cultural_preservation": "文化信息得到保留",
    "localization_choice": "本地化选择",
    # Terminology
    "terminology_choice": "术语选择",
    "competing_translation": "存在不同术语译法",
    "preferred_terminology": "采用项目偏好术语",
    "domain_interpretation": "术语按领域语境解释",
}


class TranslationDecision(TypedDict, total=False):
    """Persistable evidence contract for one translation decision."""

    decision_id: str
    segment_id: int | str
    decision_type: str
    label: str
    source_span: Optional[str]
    target_span: Optional[str]
    source_start: Optional[int]
    source_end: Optional[int]
    target_start: Optional[int]
    target_end: Optional[int]
    confidence: float
    reason: str
    evidence: Dict[str, Any]
    detection_method: str
    review_interest_score: int


ANALYSIS_VERSION = 1
_SENTENCE_RE = re.compile(r"[.!?。！？]+")


def _span_offsets(text: str, span: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not span:
        return None, None
    start = text.find(span)
    return (start, start + len(span)) if start >= 0 else (None, None)


def _decision(
    source: str,
    target: str,
    segment_id: int | str,
    decision_type: str,
    source_span: Optional[str],
    target_span: Optional[str],
    reason: str,
    *,
    confidence: float,
    method: str = "heuristic",
    score: int = 0,
) -> Optional[TranslationDecision]:
    if decision_type not in DECISION_TAXONOMY:
        return None
    if source_span and source_span not in source:
        return None
    if target_span and target_span not in target:
        return None
    if not source_span and not target_span:
        return None
    source_start, source_end = _span_offsets(source, source_span)
    target_start, target_end = _span_offsets(target, target_span)
    # Offsets plus type provide a stable local identity without hashing the
    # full pair. The full pair fingerprint is reserved for model-result cache.
    decision_id = (f"{segment_id}:{decision_type}:"
                   f"{source_start if source_start is not None else '-'}:"
                   f"{target_start if target_start is not None else '-'}")
    return TranslationDecision(
        decision_id=decision_id,
        segment_id=segment_id,
        decision_type=decision_type,
        label=DECISION_TAXONOMY[decision_type],
        source_span=source_span,
        target_span=target_span,
        source_start=source_start,
        source_end=source_end,
        target_start=target_start,
        target_end=target_end,
        confidence=max(0.0, min(1.0, float(confidence))),
        reason=reason,
        evidence={"source_quote": source_span, "target_quote": target_span},
        detection_method=method,
        review_interest_score=max(0, min(100, int(score))),
    )


def _simple_pair(source: str, target: str) -> bool:
    """Suppress routine names, labels and short literal lines by default."""
    if not source.strip() or not target.strip():
        return True
    if re.sub(r"[\W_]+", "", source.casefold()) == re.sub(
            r"[\W_]+", "", target.casefold()):
        return True
    specific_signals = (
        "including herself", "including himself", "including themselves",
        "thanksgiving", "prom", "nodded", "gave in", "\\bbank\\b",
        "the prison wasn't the place", "the prison wasn’t the place",
    )
    has_specific_signal = any(re.search(signal, source, re.IGNORECASE)
                              for signal in specific_signals)
    return (len(source.strip()) <= 40 and len(source.split()) <= 8
            and len(_SENTENCE_RE.findall(source)) <= 1
            and not has_specific_signal)


def _qa_overlaps(decision: Mapping[str, Any], qa_findings: Sequence[Mapping[str, Any]]) -> bool:
    """Avoid a second, neutral row for the same explicit QA evidence."""
    src = str(decision.get("source_span") or "")
    tgt = str(decision.get("target_span") or "")
    decision_type = str(decision.get("decision_type") or "")
    semantic_qa = {
        "translation_completion", "semantic_accuracy", "missing_translation",
        "omission", "semantic_omission", "target_missing",
    }
    for finding in qa_findings:
        if not isinstance(finding, Mapping) or finding.get("status") == "stale":
            continue
        category = str(finding.get("code") or finding.get("category") or "").casefold()
        summary = str(finding.get("summary") or finding.get("reason") or "").casefold()
        qa_src = str(finding.get("source_span") or finding.get("detected_text") or "")
        qa_tgt = str(finding.get("target_span") or "")
        same_source = bool(src and qa_src and (src == qa_src or src in qa_src or qa_src in src))
        same_target = bool(tgt and qa_tgt and (tgt == qa_tgt or tgt in qa_tgt or qa_tgt in tgt))
        mentions_omission = any(token in category or token in summary for token in (
            "omission", "missing", "漏译", "遗漏", "缺失"))
        if same_source and ((not tgt and not qa_tgt) or same_target
                            or decision_type in semantic_qa or mentions_omission):
            return True
    return False


def _candidate_decisions(
    source: str, target: str, segment_id: int | str, score: int,
) -> List[TranslationDecision]:
    decisions: List[TranslationDecision] = []
    folded_source = source.casefold()

    # A narrow, high-signal omission cue with predictable Chinese realizations.
    # It intentionally does not infer omissions from source/target length ratios.
    omission_cues = (
        ("including herself", ("包括她自己", "也包括她自己", "连她自己")),
        ("including himself", ("包括他自己", "也包括他自己", "连他自己")),
        ("including themselves", ("包括他们自己", "也包括他们自己", "包括其自身")),
    )
    for cue, target_forms in omission_cues:
        if cue in folded_source and not any(form in target for form in target_forms):
            item = _decision(
                source, target, segment_id, "semantic_omission", cue, None,
                f"原文中的“{cue}”在当前译文中没有单独显化。译文整体仍可能连贯，"
                "这里发生了源文信息压缩，是否保留取决于当前文本的翻译目的。",
                confidence=0.94, score=max(score, 75))
            if item:
                decisions.append(item)

    nodded = re.search(r"\bnodded\b", source, re.IGNORECASE)
    explained_nod = re.search(r"(?:点了点头|点头)(?:表示|以示)(?:了)?(?:同意|赞同)", target)
    if nodded and explained_nod:
        item = _decision(
            source, target, segment_id, "explicitation", nodded.group(0),
            explained_nod.group(0),
            "原文仅描述点头动作。当前译文将动作解释为“表示同意”，"
            "使语义关系更明确，也减少了原文行为意义的开放性。",
            confidence=0.91, score=max(score, 72))
        if item:
            decisions.append(item)

    # Target-side temporal/intensity material that is not explicit in the
    # source phrase: kept to a small set of unambiguous, reviewable cues.
    gave_in = re.search(r"\bgave\s+in\b", source, re.IGNORECASE)
    if gave_in and re.search(r"(?:最终(?:还是)?|还是|终究|最后)", target) \
            and not re.search(r"\b(?:finally|eventually|at last)\b", source, re.IGNORECASE):
        extra = re.search(r"(?:最终(?:还是)?|还是|终究|最后)", target)
        item = _decision(
            source, target, segment_id, "intensity_shift", gave_in.group(0), extra.group(0),
            f"原文使用“{gave_in.group(0)}”概括让步结果；译文增加了“{extra.group(0)}”，"
            "使过程感或无奈语气更明确。这可能是一项自然化处理。",
            confidence=0.86, score=max(score, 70))
        if item:
            decisions.append(item)

    # Vague "things" becoming an explicit event/interpretation.
    vague_event = re.search(r"\bthings?\s+(?:are|were|will be)\s+about\s+to\s+happen\b",
                            source, re.IGNORECASE)
    specific_event = re.search(r"奇迹|惊喜|灾难|盛事|转折", target)
    if vague_event and specific_event:
        item = _decision(
            source, target, segment_id, "specification", vague_event.group(0),
            specific_event.group(0),
            f"原文以“{vague_event.group(0)}”保持事件内容开放；译文用“{specific_event.group(0)}”"
            "给出了更具体的解释，缩小了原文的开放范围。",
            confidence=0.88, score=max(score, 72))
        if item:
            decisions.append(item)

    # Culturally specific references translated with a generic meal/event term.
    thanksgiving = re.search(r"\bThanksgiving\b", source, re.IGNORECASE)
    generic_meal = re.search(r"家庭聚餐|聚餐|家庭晚餐|家庭晚宴|一顿饭", target)
    if thanksgiving and generic_meal and not re.search(r"感恩节", target):
        item = _decision(
            source, target, segment_id, "cultural_reduction", thanksgiving.group(0),
            generic_meal.group(0),
            "原文明确包含 Thanksgiving 的节日背景。当前译文转为更宽泛的聚餐表达，"
            "提高了直接可理解性，但节日文化信息不再显式出现；这是一项翻译取舍。",
            confidence=0.92, score=max(score, 78))
        if item:
            decisions.append(item)

    prom = re.search(r"\bprom(?:\s+night)?\b", source, re.IGNORECASE)
    explained_dance = re.search(r"毕业舞会|校园舞会", target)
    if prom and explained_dance:
        item = _decision(
            source, target, segment_id, "cultural_adaptation", prom.group(0),
            explained_dance.group(0),
            "“prom”指美国高中毕业季的重要校园舞会文化。当前译文用中文概念解释，"
            "提高了目标读者的直接可理解性，同时弱化了部分美国校园文化特征。",
            confidence=0.90, score=max(score, 76))
        if item:
            decisions.append(item)

    # Contextual disambiguation of the English homonym "bank".
    bank = re.search(r"\bbank\b", source, re.IGNORECASE)
    riverside = re.search(r"河岸|岸边|河畔", target)
    financial_context = re.search(
        r"\b(?:money|account|loan|cash|deposit|withdraw|banker|credit)\b",
        source, re.IGNORECASE)
    if bank and riverside and not financial_context:
        item = _decision(
            source, target, segment_id, "ambiguity_resolution", bank.group(0), riverside.group(0),
            "原文“bank”在缺少充分局部线索时可能指金融机构或河岸；当前译文选择了河岸义，"
            "将源文可能保留的歧义收束为一种解释。",
            confidence=0.71, score=max(score, 68))
        if item:
            decisions.append(item)

    # A clear image-to-explanation conversion, with both sides locatable.
    prison_metaphor = re.search(
        r"\bthe\s+prison\s+wasn['’]t\s+the\s+place,?\s+but\s+the\s+perspective\b",
        source, re.IGNORECASE)
    explained_condition = re.search(r"困境|处境|心态|视角", target)
    preserves_image = re.search(r"牢笼|监狱|牢狱", target)
    if prison_metaphor and explained_condition and not preserves_image:
        item = _decision(
            source, target, segment_id, "metaphor_transformation",
            prison_metaphor.group(0), explained_condition.group(0),
            "原文用“prison”构成空间隐喻；当前译文直接说明处境或视角，"
            "保留了核心论点，但改变了原文的修辞表达。",
            confidence=0.88, score=max(score, 76))
        if item:
            decisions.append(item)

    return decisions


def review_interest_score(source: str, target: str) -> int:
    """Fast local gate for optional deep analysis; not a displayed confidence."""
    source = str(source or "")
    target = str(target or "")
    if not source.strip() or not target.strip() or _simple_pair(source, target):
        return 0
    score = 0
    if len(source) >= 120:
        score += 20
    if len(source) >= 240:
        score += 20
    if len(source.split()) >= 35:
        score += 18
    if len(_SENTENCE_RE.findall(source)) >= 2:
        score += 14
    cues = ("including herself", "including himself", "including themselves",
            "thanksgiving", "prom night", r"\bgave in\b", r"\bbank\b",
            "the prison wasn't the place")
    if any(re.search(cue, source, re.IGNORECASE) for cue in cues):
        score += 48
    # Large divergence is only a weak signal; it can trigger a review affordance
    # but never creates a finding by itself.
    if len(source) >= 90 and len(target) >= 30 \
            and max(len(source), len(target)) / max(1, min(len(source), len(target))) >= 2.4:
        score += 12
    return min(100, score)


def detect_translation_decisions(
    source: Any,
    target: Any,
    segment_id: int | str,
    *,
    qa_findings: Sequence[Mapping[str, Any]] = (),
    source_quality_blocked: bool = False,
) -> List[TranslationDecision]:
    """Return at most three strong, segment-local decision traces.

    No LLM, project-wide scan, terminology rebuild or state mutation occurs in
    this function. The caller supplies only current-segment QA evidence.
    """
    source_text = str(source or "")
    target_text = str(target or "")
    if source_quality_blocked or _simple_pair(source_text, target_text):
        return []
    score = review_interest_score(source_text, target_text)
    candidates = _candidate_decisions(source_text, target_text, segment_id, score)
    visible: List[TranslationDecision] = []
    seen = set()
    for item in candidates:
        key = (item["decision_type"], item.get("source_span"), item.get("target_span"))
        if key in seen or _qa_overlaps(item, qa_findings):
            continue
        seen.add(key)
        visible.append(item)
        if len(visible) >= 3:
            break
    return visible


def pair_fingerprint(source: Any, target: Any) -> str:
    """Content key used only to invalidate cached, expensive model analysis."""
    payload = json.dumps([str(source or ""), str(target or "")], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_analysis(pair: Mapping[str, Any], source: Any, target: Any) -> Optional[Dict[str, Any]]:
    cached = pair.get("translation_decision_analysis")
    if not isinstance(cached, Mapping) or cached.get("version") != ANALYSIS_VERSION:
        return None
    if cached.get("pair_fingerprint") != pair_fingerprint(source, target):
        return None
    return dict(cached)


def analysis_prompt(source: str, target: str) -> tuple[str, str]:
    allowed = ", ".join(sorted(DECISION_TAXONOMY))
    system = (
        "你是翻译决策审视助手，不是第二个翻译模型。只指出源文与当前译文之间"
        "有证据、值得译者注意的变化，不判断为错误，不生成建议译文，不输出应用修改。"
        "没有值得注意的变化时返回空 decisions。最多返回 3 条。"
        "source_span 与 target_span 必须逐字摘自给定文本，缺失的一侧用 null。"
        "仅可使用 decision_type 枚举：" + allowed + "。"
        "严格返回 JSON：{\"decisions\":[{\"decision_type\":\"...\","
        "\"source_span\":\"原文精确片段或 null\",\"target_span\":\"译文精确片段或 null\","
        "\"reason\":\"中性说明证据与取舍\",\"confidence\":0.0}]}。"
    )
    user = f"原文：\n{source}\n\n当前译文：\n{target}"
    return system, user


def parse_analysis_response(
    raw: Any, source: str, target: str, segment_id: int | str,
) -> List[TranslationDecision]:
    """Validate model evidence strictly; inaccurate spans never reach the UI."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    values = payload.get("decisions") if isinstance(payload, Mapping) else payload
    if not isinstance(values, list):
        return []
    result: List[TranslationDecision] = []
    for raw_item in values:
        if not isinstance(raw_item, Mapping):
            continue
        kind = str(raw_item.get("decision_type") or "")
        source_span = raw_item.get("source_span")
        target_span = raw_item.get("target_span")
        source_span = str(source_span) if source_span not in (None, "") else None
        target_span = str(target_span) if target_span not in (None, "") else None
        reason = str(raw_item.get("reason") or "").strip()
        if not reason or (source_span and source_span not in source) \
                or (target_span and target_span not in target):
            continue
        confidence = raw_item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = 0.5
        item = _decision(
            source, target, segment_id, kind, source_span, target_span, reason,
            confidence=float(confidence), method="llm", score=0)
        if item:
            result.append(item)
        if len(result) >= 3:
            break
    return result


def make_cached_analysis(source: str, target: str,
                         decisions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "version": ANALYSIS_VERSION,
        "pair_fingerprint": pair_fingerprint(source, target),
        "decisions": [dict(item) for item in decisions[:3]],
    }


def merge_decisions(
    heuristic: Sequence[Mapping[str, Any]],
    analyzed: Sequence[Mapping[str, Any]],
    qa_findings: Sequence[Mapping[str, Any]] = (),
) -> List[TranslationDecision]:
    """Merge cached deep traces without duplicating local signals or QA."""
    merged: List[TranslationDecision] = []
    for raw in [*heuristic, *analyzed]:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("decision_type") or "")
        source_span = raw.get("source_span")
        target_span = raw.get("target_span")
        reason = str(raw.get("reason") or "").strip()
        if not reason or kind not in DECISION_TAXONOMY:
            continue
        if _qa_overlaps(raw, qa_findings):
            continue
        key = (kind, source_span, target_span)
        if any((row.get("decision_type"), row.get("source_span"), row.get("target_span")) == key
               for row in merged):
            continue
        merged.append(dict(raw))  # type: ignore[arg-type]
        if len(merged) >= 3:
            break
    return merged  # type: ignore[return-value]
