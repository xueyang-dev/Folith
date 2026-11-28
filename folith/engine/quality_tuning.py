"""Translation quality helpers: prompts, temperature, neighbor context, QA.

Centralises the 0.8.4 translation-quality upgrades outside ``core.py`` so the
core stays within its line budget and each rule can be unit-tested directly.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from folith import style_profile as _style_profile

#: Arabic-Indic (U+0660-U+0669) and Extended Arabic-Indic (U+06F0-U+06F9) digits.
_ARABIC_INDIC_DIGIT_RE = re.compile(r"[\u0660-\u0669\u06F0-\u06F9]")
_ARABIC_SCRIPT_TARGETS = frozenset({"arabic", "العربية", "阿拉伯语", "persian", "farsi", "urdu"})

_CJK_TARGETS = frozenset({
    "简体中文", "繁体中文", "繁體中文", "中文", "chinese", "zh", "zh-cn", "zh-tw",
    "日本語", "日语", "japanese", "ja", "한국어", "韩语", "korean", "ko",
})

_OPEN_QUOTES = ('"', "“", "«", "「", "『")
_CLOSE_QUOTES = ('"', "”", "»", "」", "』")
#: Any mark a target language may open or close speech with. Conventions differ
#: (German „…“, Danish »…«, British ‘…’), so a target is not judged by the
#: source's mark shapes. ``’`` is left out: it doubles as an apostrophe.
_TARGET_QUOTES = ('"', "“", "”", "„", "«", "»", "‹", "›", "‘", "‚", "「", "」", "『", "』")
#: Languages such as Spanish, French and Russian mark dialogue with a dash.
_DIALOGUE_DASHES = ("—", "–", "―")
#: Short function words that stay lowercase inside Title Case titles.
_TITLE_MINOR_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or over the to via vs with "
    "de del der des die du et la le les van von".split())

#: Character budgets for local neighbor context around a translation batch.
PREV_CONTEXT_MIN_ITEMS = 2
PREV_CONTEXT_MAX_ITEMS = 6
PREV_CONTEXT_TARGET_CHARS = 700
NEXT_CONTEXT_MIN_ITEMS = 2
NEXT_CONTEXT_MAX_ITEMS = 4
NEXT_CONTEXT_TARGET_CHARS = 350
RECENT_TARGET_CONTEXT_COUNT = 3


def _needs_arabic_digit_rule(source_text: str, target_lang: str) -> bool:
    if source_text and _ARABIC_INDIC_DIGIT_RE.search(source_text):
        return True
    return str(target_lang or "").strip().casefold() in _ARABIC_SCRIPT_TARGETS


def build_translator_system(
    glossary_text: str,
    style_rules: str,
    target_lang: str,
    *,
    source_text: str = "",
) -> str:
    """Build a focused, non-contradictory system prompt for batch translation.

    Keeps the ``专业翻译人员`` role marker relied on by runtime/test doubles,
    omits internal architecture jargon and irrelevant niche rules (e.g.
    Arabic-Indic digit conversion when no Arabic digits are present), and
    explicitly guards quotation marks and natural target-language flow.
    """
    lines = [
        f"你是一位专业翻译人员，请将用户提供的段落翻译成{target_lang}。",
        "忠实传达原文含义、语气与体裁，译文须自然流畅，避免逐词直译和生硬翻译腔；"
        "译文与输入段落一一对应并保持顺序。",
        "专有名词、人名、地名、机构名与作品名须遵循已提供的术语表、实体提示和前文既定译法；"
        "无既定译法时结合文体选择自然、连贯的表达并在后文保持一致，仅对明确要求保留原文的词条原样保留。",
        "人工锁定实体/术语优先于审校、TM 和生成式建议；生成式实体提示不得覆盖锁定术语。",
        "URL、邮箱、DOI 及代码/机器标识符逐字符保持不变；引用标注（如 [12]）、数字与日期数值保持不变。"
        "原文段落开头或结尾的对话引号须在译文中对应保留，不要无故省略。",
    ]
    if _needs_arabic_digit_rule(source_text, target_lang):
        lines.append(
            "原文的阿拉伯-印度数字（如 ١٢٣、۴۵）在中文、英文等译文中改写为 123、45，"
            "脚注标号 (١) 写作 (1)、引用标注 [٣] 写作 [3]；目标语言本身使用此类数字时保持原样。"
        )
    if str(glossary_text or "").strip():
        lines.append(str(glossary_text).strip())
    if str(style_rules or "").strip():
        lines.append(str(style_rules).strip())
    lines.extend([
        "注意结合前后文把握指代关系（他/她/它/他们/她们）、说话人口吻与习语隐喻；"
        "输入段落前的序号仅用于对齐，不要写入译文，原文本身的编号须保留。",
        "不要输出分析过程，请严格输出合法的 JSON 字符串数组，不要包含任何解释文字。",
    ])
    return "\n".join(lines)


def translation_temperature(
    style_rules: str = "",
    document_profile: Optional[Dict[str, Any]] = None,
) -> float:
    """Choose sampling temperature by document genre / style rules.

    Literary and publicity prose benefit from slightly warmer phrasing (0.5);
    legal and technical documents stay conservative (0.2); everything else
    keeps 0.3.
    """
    profile_id = _style_profile.infer_style_profile_id(document_profile)
    rules = str(style_rules or "")
    if profile_id in ("literary", "publicity") or any(
        k in rules for k in ("文学", "叙事语气", "人物口吻", "感染力", "适度本地化")
    ):
        return 0.5
    if profile_id in ("legal", "technical") or any(
        k in rules for k in ("法条与条款", "条款结构", "代码、命令与路径")
    ):
        return 0.2
    return 0.3


def select_neighbor_source_context(
    paras: Sequence[str],
    start_index: int,
    end_index: int,
    *,
    direction: str,
    min_items: Optional[int] = None,
    max_items: Optional[int] = None,
    target_chars: Optional[int] = None,
    blocked: Iterable[int] = (),
) -> List[str]:
    """Select preceding or following source segments using a character budget.

    Short dialogue or hard-wrapped lines (e.g. ``"No."`` / ``"Wait."``) pull up
    to ``max_items`` neighbors until ``target_chars`` is reached, while long
    paragraphs stop at ``min_items`` so prompt size stays bounded.
    """
    blocked_set = set(blocked or ())
    total = len(paras)
    if direction == "prev":
        min_n = PREV_CONTEXT_MIN_ITEMS if min_items is None else int(min_items)
        max_n = PREV_CONTEXT_MAX_ITEMS if max_items is None else int(max_items)
        budget = PREV_CONTEXT_TARGET_CHARS if target_chars is None else int(target_chars)
        collected: List[Tuple[int, str]] = []
        chars = 0
        idx = min(start_index, total) - 1
        while idx >= 0 and len(collected) < max_n:
            if idx not in blocked_set:
                text = str(paras[idx] or "")
                collected.append((idx, text))
                chars += len(text)
                if len(collected) >= min_n and chars >= budget:
                    break
            idx -= 1
        collected.reverse()
        return [text for _, text in collected]

    min_n = NEXT_CONTEXT_MIN_ITEMS if min_items is None else int(min_items)
    max_n = NEXT_CONTEXT_MAX_ITEMS if max_items is None else int(max_items)
    budget = NEXT_CONTEXT_TARGET_CHARS if target_chars is None else int(target_chars)
    collected = []
    chars = 0
    idx = max(0, end_index)
    while idx < total and len(collected) < max_n:
        if idx not in blocked_set:
            text = str(paras[idx] or "")
            collected.append((idx, text))
            chars += len(text)
            if len(collected) >= min_n and chars >= budget:
                break
        idx += 1
    return [text for _, text in collected]


def format_repair_context(
    context_packet: Optional[Dict[str, Any]] = None,
    ctx_prev: Optional[Sequence[str]] = None,
    ctx_next: Optional[Sequence[str]] = None,
) -> str:
    """Render concise surrounding context so repair_batch never rewrites blind."""
    parts: List[str] = []
    if isinstance(context_packet, dict):
        prev_src = context_packet.get("previous_source_context") or []
        prev_tgt = context_packet.get("previous_accepted_target_context") or []
        next_src = context_packet.get("next_source_context") or []
        entities = context_packet.get("entity_hints") or []
        if prev_src:
            parts.append("【前文原文】：\n" + "\n".join(f"- {s}" for s in prev_src[-3:]))
        if prev_tgt:
            lines = [
                f"- {item.get('source', '')} => {item.get('target', '')}"
                for item in prev_tgt[-3:]
                if isinstance(item, dict) and item.get("target")
            ]
            if lines:
                parts.append("【前文译文参考】：\n" + "\n".join(lines))
        if next_src:
            parts.append("【后文原文】：\n" + "\n".join(f"- {s}" for s in next_src[:2]))
        if entities:
            el = [
                f"- {e.get('source_form')} -> {e.get('preferred_target')}"
                for e in entities[:8]
                if isinstance(e, dict) and e.get("source_form") and e.get("preferred_target")
            ]
            if el:
                parts.append("【专名连续性提示】：\n" + "\n".join(el))
    else:
        if ctx_prev:
            parts.append("【前文原文】：\n" + "\n".join(f"- {s}" for s in list(ctx_prev)[-3:]))
        if ctx_next:
            parts.append("【后文原文】：\n" + "\n".join(f"- {s}" for s in list(ctx_next)[:2]))
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _is_cjk_target(target_lang: str) -> bool:
    return str(target_lang or "").strip().casefold() in _CJK_TARGETS


def check_quote_preservation(
    src: str,
    tgt: str,
    target_lang: str,
    segment_index: int,
) -> List[Dict[str, Any]]:
    """Detect dropped leading/trailing quotation marks against the source (#630).

    Report-only: ``informational`` severity keeps it out of the automatic repair
    pass, because quote conventions vary and placement depends on the sentence.
    """
    src_s = str(src or "").strip()
    tgt_s = str(tgt or "").strip()
    if not src_s or not tgt_s:
        return []

    from folith.engine.glossary_checks import _deterministic_finding

    if tgt_s.startswith(_DIALOGUE_DASHES) or any(q in tgt_s for q in _TARGET_QUOTES):
        return []
    missing: List[str] = []
    if src_s[0] in _OPEN_QUOTES:
        missing.append("开头引号")
    if src_s[-1] in _CLOSE_QUOTES:
        missing.append("结尾引号")
    if not missing:
        return []

    label = "与".join(missing)
    return [_deterministic_finding(
        segment_index, "informational", "format_integrity",
        f"译文可能遗漏原文的{label}",
        f"原文在段首/段尾包含对话引号，但当前译文中未出现对应的{label}。",
        "核对对话边界，按目标语言标点规范补回缺失的引号。",
        source_span=src_s[:1] if missing[0] == "开头引号" else src_s[-1:],
        target_span="",
        reason=f"原文包含{label}但译文缺失",
        kind="quote_missing",
    )]


_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{1,}")


def check_partial_copy_through(
    src: str,
    tgt: str,
    target_lang: str,
    segment_index: int,
    *,
    preserve_re: Optional[re.Pattern] = None,
) -> List[Dict[str, Any]]:
    """Detect substantial verbatim untranslated source clauses in CJK targets (#572).

    Targets partial leaks where the model translated part of a segment and
    copied a whole English/Latin phrase (>= 6 consecutive source words, or
    >= 5 words covering >= 40% of source words) verbatim into a CJK target.

    Title Case runs (cited book titles, organisation names) are skipped. The
    finding is ``informational`` so a kept quotation or sentence-case reference
    title is never rewritten automatically.
    """
    if not _is_cjk_target(target_lang):
        return []
    src_s = str(src or "").strip()
    tgt_s = str(tgt or "").strip()
    if not src_s or not tgt_s or src_s == tgt_s:
        return []

    src_clean = preserve_re.sub(" ", src_s) if preserve_re is not None else src_s
    tgt_clean = preserve_re.sub(" ", tgt_s) if preserve_re is not None else tgt_s

    src_words = [w.casefold() for w in _LATIN_WORD_RE.findall(src_clean)]
    if len(src_words) < 5:
        return []

    # Find longest contiguous run of source words copied verbatim into target.
    tgt_tokens = _LATIN_WORD_RE.findall(tgt_clean)
    if len(tgt_tokens) < 5:
        return []

    src_seq = " ".join(src_words)
    best_run: List[str] = []
    cur_run: List[str] = []
    for tok in tgt_tokens:
        candidate = cur_run + [tok]
        if " ".join(w.casefold() for w in candidate) in src_seq:
            cur_run = candidate
            if len(cur_run) > len(best_run):
                best_run = list(cur_run)
        else:
            # Slide window from previous token if possible.
            cur_run = [tok] if tok.casefold() in src_words else []

    prose_words = [w for w in best_run
                   if w[0].islower() and w.casefold() not in _TITLE_MINOR_WORDS]
    if len(prose_words) >= 2 and (len(best_run) >= 6 or (
        len(best_run) >= 5 and len(best_run) / len(src_words) >= 0.4
    )):
        from folith.engine.glossary_checks import _deterministic_finding
        snippet = " ".join(best_run)
        return [_deterministic_finding(
            segment_index, "informational", "translation_completion",
            "译文包含大段未翻译的原文片段",
            f"译文中原样保留了连续 {len(best_run)} 个源语词（「{snippet[:80]}」），疑似部分漏译或抄写原文。",
            "将未翻译的源语片段译为目标语言；若确属需保留的专名或引文，请确认后放行。",
            source_span=snippet[:120],
            target_span=snippet[:120],
            reason=f"译文疑似部分原样抄写原文（连续 {len(best_run)} 词）",
            kind="partial_copy_through",
            detected_text=snippet[:120],
        )]
    return []
