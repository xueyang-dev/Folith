"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import pandas as pd
import re
from defusedxml import ElementTree as ET
from folith import models as _models
from folith.engine._link import core as _c


def parse_termbase(file_stream):
    """解析用户上传的术语库 Excel，返回概念化术语条目列表。

    必选列：Source / Target；可选列：Behavior（translate/preserve）、
    Status（locked/provisional）、Preferred（首选译名）、Forbidden（禁止译名，
    可用 ; 或 , 分隔）、Scope、Note。解析失败抛出 ValueError（不再静默吞错）。
    """
    try:
        df = pd.read_excel(file_stream)
    except Exception as e:
        raise ValueError(f"无法读取 Excel 文件：{e}") from e
    df.columns = [str(c).strip() for c in df.columns]
    return _termbase_df_to_entries(df)


def _termbase_df_to_entries(df):
    if "Source" not in df.columns or "Target" not in df.columns:
        raise ValueError("术语库缺少 Source / Target 列，请检查表头")
    df = df.dropna(subset=["Source", "Target"])
    entries = []
    for _, row in df.iterrows():
        entry = {"source": str(row["Source"]).strip(),
                 "target": str(row["Target"]).strip()}
        for col, key in (("Behavior", "behavior"), ("Status", "status"),
                         ("Preferred", "preferred"), ("Scope", "scope"),
                         ("Note", "note")):
            if col in df.columns and pd.notna(row[col]):
                entry[key] = str(row[col]).strip()
        if "Forbidden" in df.columns and pd.notna(row["Forbidden"]):
            entry["forbidden"] = [x.strip() for x in
                                  re.split(r"[;；,，]", str(row["Forbidden"])) if x.strip()]
        entries.append(entry)
    return entries


def parse_termbase_csv(file_stream):
    """CSV 术语库：列约定与 Excel 一致（Source/Target 必选）。"""
    try:
        df = pd.read_csv(file_stream)
    except Exception as e:
        raise ValueError(f"无法读取 CSV 文件：{e}") from e
    df.columns = [str(c).strip() for c in df.columns]
    return _termbase_df_to_entries(df)


def _local_name(tag):
    return str(tag).rsplit("}", 1)[-1]


def parse_termbase_tbx(file_stream):
    """解析 TBX 术语库（Trados MultiTerm 等标准格式）。

    每个 termEntry 取前两种语言的第一个 term，作为 source/target；
    导入条目默认 status=locked（导入即视为已固定译名）。
    """
    try:
        root = ET.fromstring(file_stream.read())
    except Exception as e:
        raise ValueError(f"无法解析 TBX 文件：{e}") from e
    entries = []
    for te in root.iter():
        if _local_name(te.tag) != "termEntry":
            continue
        langs = []
        for ls in te.iter():
            if _local_name(ls.tag) != "langSet":
                continue
            # ElementTree 会把 xml: 前缀自动展开为命名空间键
            lang = ls.get("{http://www.w3.org/XML/1998/namespace}lang") \
                or ls.get("xml:lang") or ls.get("lang") or ""
            term = None
            for node in ls.iter():
                if _local_name(node.tag) == "term" and (node.text or "").strip():
                    term = node.text.strip()
                    break
            if term and lang and lang not in [x[0] for x in langs]:
                langs.append((lang, term))
            if len(langs) >= 2:
                break
        if len(langs) >= 2:
            entries.append({"source": langs[0][1], "target": langs[1][1],
                            "behavior": "translate", "status": "locked"})
    if not entries:
        raise ValueError("TBX 中未找到可导入的术语（需要至少两种语言的 langSet）")
    return entries


#: TMX 1.4b 的行内代码元素：内容是原格式的标记（`<b>`、`{1}` 之类），不是
#: 译文的一部分——丢掉元素内容，保留元素之后的文字（tail）。
_TMX_CODE_TAGS = frozenset({"bpt", "ept", "it", "ph", "ut"})


def _tmx_seg_text(node):
    """`<seg>` 的完整文字；含无法可靠处理的行内内容时返回 None。

    只读 `seg.text` 会在第一个行内标签处截断（`The <hi>meeting</hi> starts`
    只剩 `The`），截断后的半句还会作为记忆入库。`<hi>` 是带格式的正文，照常
    读出；代码元素见 `_TMX_CODE_TAGS`；其他元素（含 `<sub>`）语义不明，整个
    单元跳过，宁可不导入，不导入半句。
    """
    parts = [node.text or ""]
    for child in node:
        name = _local_name(child.tag)
        if name == "hi":
            inner = _tmx_seg_text(child)
            if inner is None:
                return None
            parts.append(inner)
        elif name not in _TMX_CODE_TAGS:
            return None
        parts.append(child.tail or "")
    return "".join(parts)


def import_tmx(file_stream, project_id=None, target_lang=None):
    """导入 TMX 翻译记忆（Trados / memoQ 等导出的标准格式）。

    按 <tu> 的 <tuv><seg> 文本对入库：仅接受源文含字母/数字且译文非空的
    单元；与现有翻译记忆冲突的源文跳过（不覆盖项目内已审校条目）。<seg> 里的
    行内标签按 `_tmx_seg_text` 处理，含无法可靠处理的行内内容的单元也计入跳过。

    条目必须带上**目标语言**，否则不入库——语言未知的记忆一旦落盘，就会成为
    下一次跨语言命中的来源。语言取调用方显式给出的 `target_lang`（调用方
    知道"这份记忆是给哪个目标语言的"）；调用方没给时退回目标 <tuv xml:lang>。
    刻意**不**把 BCP-47 代码（`zh-CN`）翻译成本应用的显示名（`简体中文`）：
    猜测式映射会让导入"看起来成功、实际永不命中"，比显式失败更糟。

    导入目标为指定项目的记忆（默认项目即历史的全局记忆）。
    返回 {"added": n, "skipped": m}。
    """
    try:
        root = ET.fromstring(file_stream.read())
    except Exception as e:
        raise ValueError(f"无法解析 TMX 文件：{e}") from e
    existing = _c.load_tm(project_id)
    declared_lang = str(target_lang or "").strip()
    added = skipped = 0
    for tu in root.iter():
        if _local_name(tu.tag) != "tu":
            continue
        texts = []
        languages = []
        unsupported = False
        for tuv in tu:
            if _local_name(tuv.tag) != "tuv":
                continue
            seg = next((c for c in tuv if _local_name(c.tag) == "seg"), None)
            text = _tmx_seg_text(seg) if seg is not None else ""
            if text is None:
                unsupported = True
                break
            if text.strip():
                texts.append(text.strip())
                languages.append(tuv.get("{http://www.w3.org/XML/1998/namespace}lang")
                                 or tuv.get("lang") or "")
            if len(texts) >= 2:
                break
        if unsupported:
            skipped += 1
            continue
        if len(texts) >= 2:
            src, tgt = texts[0], texts[-1]
            language = declared_lang or str(languages[-1] or "").strip()
            key = _c.tm_scope_key(language, src)
            if _c._tm_eligible(src, tgt) and key:
                if key not in existing:
                    record = _c.tm_record(tgt, language)
                    record["source"] = "tmx_import"
                    existing[key] = record
                    added += 1
                else:
                    skipped += 1
    if not added:
        raise ValueError(
            "TMX 中未找到可导入的新翻译单元（源文需含字母/数字、目标语言需显式指定"
            "或可从 xml:lang 确定，且不与现有记忆冲突）")
    _c.save_tm(existing, project_id)
    return {"added": added, "skipped": skipped}


def extract_auto_terms(paragraphs, target_lang, provider, api_key, model):
    """自动抽取术语库（兼容旧接口：返回 {source: target}）。

    新实现（folith.terminology.extract_auto_terms_v2）：分布式采样、
    全量 occurrences、candidate 状态与 model_knowledge 证据。
    """
    from folith.terminology import extract_auto_terms_v2
    entries, _warnings = extract_auto_terms_v2(
        paragraphs, target_lang, provider, api_key, model)
    return {e["source"]: e["target"] for e in entries}


# ================= 概念化术语表（对齐 localize-anything 的 Glossary 模型）=================
def normalize_glossary(entries):
    """标准化术语条目（委托 folith.models，兼容旧字段并新增 id/occurrences/evidence）。"""
    return _models.normalize_glossary(entries)


def glossary_block(glossary):
    """把术语表渲染成注入翻译/审校 prompt 的文本块。"""
    from folith.terminology import source_forms
    locked_translate = [e for e in glossary if e["behavior"] == "translate" and e["status"] == "locked"]
    preserve = [e for e in glossary if e["behavior"] == "preserve"]
    provisional = [e for e in glossary if e["behavior"] == "translate" and e["status"] != "locked"]
    lines = []
    if locked_translate:
        lines.append("【锁定术语（必须使用首选译名，不得使用禁止译名）】：")
        for e in locked_translate:
            seg = f"- {' / '.join(source_forms(e))} -> {e['preferred']}"
            if e["forbidden"]:
                seg += f"（禁止：{'、'.join(e['forbidden'])}）"
            lines.append(seg)
    if preserve:
        lines.append("【必须保留原文的术语/名称】：" + "、".join(e["source"] for e in preserve))
    if provisional:
        lines.append("【建议术语（仅供参考，请优先采用）】：")
        for e in provisional:
            lines.append(f"- {' / '.join(source_forms(e))} -> {e['target']}")
    return "\n".join(lines)


def glossary_to_terms(glossary):
    """翻译行为术语 -> 扁平 dict（供报告生成等场景使用）。"""
    return {e["source"]: (e["preferred"] or e["target"])
            for e in glossary if e["behavior"] == "translate" and e["target"]}


def check_glossary_compliance(src, tgt, glossary, segment_id=None,
                              section_profile=None, entries=None, matcher=None):
    """锁定术语的确定性合规检查（委托 folith.terminology：entry_id/segment_id 级）。

    `entries` / `matcher` 可选（Phase 8 §9）：批次内术语表不变时，调用方预派生一次
    即可，避免每个 segment 都重新归一化整张术语表。
    """
    from folith.terminology import check_glossary_compliance as _qa
    return _qa(src, tgt, glossary, segment_id=segment_id,
               section_profile=section_profile, entries=entries, matcher=matcher)


def extract_preserved_tokens(text):
    """提取源文本中必须原样保留的 token（占位符/URL/邮箱/DOI/引用标注）。"""
    return {m.group(0): m.lastgroup for m in _c.PRESERVE_RE.finditer(text or "")}


def find_residuals(src, tgt, target_lang):
    """检测目标语言中残留的源语言片段。

    返回 [(片段, severity)]：连续 ≥2 个源语单词/较长汉字串 -> actionable；
    单个词（可能是专有名词）-> informational。启发式，不替代审校。
    """
    tgt_clean = _c.PRESERVE_RE.sub(" ", tgt or "")
    if target_lang == "English":
        source_runs = set(re.findall(r'[\u4e00-\u9fff]{2,}', src or ""))
        return [(c, "actionable" if len(c) >= 4 else "informational")
                for c in re.findall(r'[\u4e00-\u9fff]{2,}', tgt_clean)
                if any(c in run for run in source_runs)]
    src_words = set(w.lower() for w in re.findall(r'[A-Za-z]{5,}', src or ""))
    allowed = {"mti"}  # 产品名等明确保留词白名单（审校负责语义判断）
    words = re.findall(r'[A-Za-z]{5,}', tgt_clean)
    hits = [w for w in words if w.lower() in src_words and w.lower() not in allowed]
    result, run = [], []
    for w in words:
        if w in hits:
            run.append(w)
        else:
            if run:
                result.append((" ".join(run),
                               "actionable" if len(run) >= 2 else "informational"))
                run = []
    if run:
        result.append((" ".join(run), "actionable" if len(run) >= 2 else "informational"))
    return result


# A shared surname in a credit ("Alice M. and Robert J. Smith") makes the
# two isolated periods unambiguously name initials. Keep this narrow: treating
# every single-letter token as an initial would hide "Plan A. We ...".
_SHARED_SURNAME_INITIALS = re.compile(
    r"\b[A-Z][a-z]+\s+[A-Z]\.\s+and\s+[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+\b")


def _count_sentences(text):
    """粗粒度句数统计：按终结符切分（引号/括号闭合归并到前一句）。"""
    text = _c._ABBREV_RE.sub(" ", text)
    text = _SHARED_SURNAME_INITIALS.sub(lambda match: match.group().replace(".", ""), text)
    parts = re.split(r"[.!?…。！？؟۔]+[”\"'’)\]]*", text)
    return sum(1 for p in parts if p.strip())


def is_incomplete_translation(src, tgt):
    """疑似漏译/截断判定（双重规则，实测调优）：
    1. 字符级：长原文（≥120 字符）配极短译文（<15%）——只拦灾难性截断，
       英译中正常比例可低至 0.2-0.3，不能用高阈值；
    2. 句子级：原文 ≥2 句而译文不足一半句数，且字符占比 <35%——
       截断译文必然句数对不上，完整译文即使语言再凝练也很少掉一半句。
    """
    tgt = (tgt or "").strip()
    if not tgt:
        return True
    if len(src) >= 120 and len(tgt) < 0.15 * len(src):
        return True
    src_sents = _count_sentences(src)
    if src_sents >= 2:
        tgt_sents = _count_sentences(tgt)
        if tgt_sents < src_sents * 0.5 and len(tgt) < 0.35 * len(src):
            return True
    return False


def _deterministic_finding(segment_index, severity, category, summary,
                           explanation, recommendation, *, source_span=None,
                           target_span=None, reason=None, kind=None,
                           detected_text=None, **extra):
    """Build an evidence-free but actionable deterministic QA finding."""
    finding = {
        "segment_index": segment_index, "segment_id": segment_index,
        "type": "check", "severity": severity,
        "category": category, "summary": summary,
        "source_span": source_span, "target_span": target_span,
        "explanation": explanation, "recommendation": recommendation,
        "confidence": None, "detector": "Deterministic QA",
        "diagnostic_version": 1,
        "reason": reason or summary,
    }
    if kind:
        finding["kind"] = kind
    if detected_text:
        finding["detected_text"] = detected_text
    finding.update({key: value for key, value in extra.items() if value is not None})
    return finding


def _globalize_batch_findings(findings, offset):
    """Persist repair findings in document-global, never batch-local, space."""
    return [
        {**finding,
         "segment_id": offset + finding["segment_id"],
         "segment_index": offset + finding["segment_index"]}
        for finding in findings
    ]


# ---------------------------------------------------------------------------
# No-translation-needed suppression
#
# A segment whose whole content is a machine identifier or pure number does not
# need translation.  The identical-to-source check (translation_completion) must
# not fire for these; they are definitionally correct when target == source.
#
# In scope (whole-segment match only):
#   • bare email address, URL (http/https/www), DOI, or ISBN/ISSN
#   • pure numbers / numeric dates / ranges: ASCII digits and structural
#     punctuation only — no letters, no CJK, no Eastern Arabic numerals
#   • table rows (pipe- or tab-delimited) where every non-empty cell is one
#     of the above
#
# Out of scope (keeps firing):
#   • Chinese/Japanese/Korean text, Latin words, era+number, index lines,
#     anything with a unit word, any sentence that merely contains a URL or
#     a number
#
# Numerals decision: only ASCII digits [0-9] qualify as "pure numbers".
# Eastern Arabic digit strings (٠-٩, ۰-۹) are excluded so that identical
# Eastern Arabic digits for a non-Arabic target still fire (the digits should
# have been converted by the translation pipeline).
# ---------------------------------------------------------------------------

_NO_TRANS_EMAIL = re.compile(r'^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$', re.IGNORECASE)
_NO_TRANS_URL = re.compile(r'^(?:https?://\S+|www\.\S+)$', re.IGNORECASE)
_NO_TRANS_DOI = re.compile(r'^10\.\d{4,9}/\S+$', re.IGNORECASE)
# ISBN or ISSN with optional separators; also handles Japanese "ISBN<13 digits> C<4 digits>"
# NDC catalogue codes (e.g. "C3022") that follow the ISBN on the same line.
_NO_TRANS_ISBN_ISSN = re.compile(
    r'^(?:ISBN[-\s:]?[\d\s-]+(?:\s+[A-Z]\d{4})?|ISSN[-\s:]?\d{4}-\d{3}[\dXx])$',
    re.IGNORECASE,
)
# Pure numbers: ASCII digits plus structural punctuation only.
# No letters (Latin or other), no CJK, no Eastern Arabic digits.
_NO_TRANS_NUMBER = re.compile(
    r'^[0-9\s\-–—./,:;()[\]{}<>+%°\'"#$€£¥₩₽₺₿∞×*÷‰′″~^|@!?،]+$'
)


def _is_no_trans_cell(text: str) -> bool:
    """True if *text* (stripped) is a bare machine identifier or pure number."""
    s = text.strip()
    return bool(
        s and (
            _NO_TRANS_EMAIL.match(s)
            or _NO_TRANS_URL.match(s)
            or _NO_TRANS_DOI.match(s)
            or _NO_TRANS_ISBN_ISSN.match(s)
            or _NO_TRANS_NUMBER.match(s)
        )
    )


def is_no_translation_needed(src: str) -> bool:
    """Return True if the whole source segment deterministically needs no translation.

    Suppresses the identical-to-source check for bare emails, URLs, DOIs,
    ISBNs/ISSNs, pure ASCII numbers/dates/ranges, and table rows whose every
    cell is one of those.  Every other case — Han text, Latin words, index
    lines, era+number, unit phrases, sentences containing a URL — returns False
    so the check keeps firing.
    """
    raw = str(src or '')
    s = re.sub(r'\s+', ' ', raw).strip()
    if not s:
        return False
    if _is_no_trans_cell(s):
        return True
    # Table row: pipe- or tab-delimited; suppress only when ALL cells are
    # in-scope (a row that mixes numbers with translated labels is not safe).
    # Pipe rows are checked on the normalised string.  Tab rows must be split
    # on the original ``raw`` string because normalisation converts tabs to
    # spaces, making '\t' in s always False.
    if '|' in s:
        cells = [c.strip() for c in s.split('|') if c.strip()]
        if cells and all(_is_no_trans_cell(c) for c in cells):
            return True
    elif '\t' in raw:
        cells = [re.sub(r'\s+', ' ', c).strip() for c in raw.split('\t')]
        cells = [c for c in cells if c]
        if cells and all(_is_no_trans_cell(c) for c in cells):
            return True
    return False


# ---------------------------------------------------------------------------
# Han-script awareness for Simplified Chinese targets
#
# When the target language is Simplified Chinese and the source is identical to
# the target, there are three possible explanations:
#   (a) The source contains Traditional/JP kanji that SHOULD be converted →
#       actionable finding, kind="han_not_simplified".
#   (b) The source is short and already in valid Simplified/shared-Han form →
#       informational (the chars are genuinely ambiguous; must still be visible).
#   (c) The source is long and already in valid Simplified/shared-Han form →
#       keep the original actionable finding (a long text left unchanged is
#       suspicious even if no individual char signals conversion).
#
# Threshold: segments ≤ _SHORT_HAN_THRESHOLD chars (excluding a trailing page-
# number list) use case (b); longer use (c).  Dot-leader lines (table-of-contents
# format) are never eligible for the informational downgrade regardless of length;
# that guard lives in core.py where `leader_line` is computed.
# ---------------------------------------------------------------------------

_SHORT_HAN_THRESHOLD = 10  # characters (not bytes)

# Trailing page-number pattern: space + digit(s) + optional commas/dashes/spaces
# e.g. " 56", " 15, 26-30", " 1, 3–5, 42".  Stripped before measuring length so
# that index lines do not inflate into the long-actionable group.
_PAGE_NUM_SUFFIX_RE = re.compile(r'\s+\d[\d\s,;.–—-]*$')

# ---------------------------------------------------------------------------
# Codec-based non-simplified character detection
#
# A Han character is considered "non-simplified" when it encodes in Shift-JIS
# but NOT in GB2312.  Shift-JIS covers Japanese and Traditional forms that
# have no Simplified Chinese equivalent in GB2312.  Characters that encode in
# both standards (e.g. 後, 浜) may still be Traditional/JP forms — those are
# listed in _FORCED_TRUE so they still trigger.
#
# _EXCEPTIONS contains Shift-JIS-only characters that legitimately appear in
# Simplified Chinese text (proper names, classical vocabulary, Buddhist terms)
# and should NOT trigger the han_not_simplified check.
# ---------------------------------------------------------------------------

_FORCED_TRUE: frozenset[str] = frozenset({
    '後',  # U+5F8C: in GB2312 for compatibility; Traditional/JP form of 后
    '浜',  # U+6D5C: in GB2312; JP shinjitai of 濱, not standard Simplified Chinese
})

_EXCEPTIONS: frozenset[str] = frozenset({
    # Place names and personal names with no standard Simplified alternative:
    '塚',  # U+585A: Japanese place/personal name component (e.g. 一里塚, 塚田)
    '潟',  # U+6F5F: Japanese geography (新潟 = Niigata); no Simplified equivalent
    '昇',  # U+6607: personal name component; also variant of 升 in classical texts
    '祐',  # U+7950: personal name component (e.g. 天祐, 祐子); common in proper nouns
    '楯',  # U+696F: literary/onomastic variant for "shield"; used in Simplified names
    # Classical and specialised vocabulary retained in Simplified Chinese writing:
    '祇',  # U+7947: Buddhist term 祇园 (Jetavana grove); widely used in Simplified scholarship
    '畤',  # U+7564: ancient altar term in Classical Chinese history (e.g. 四畤 in 史记)
})

# Normalized Simplified Chinese target-language labels (normalize_language output).
_SIMP_CHINESE_LABELS: frozenset[str] = frozenset({
    "简体中文", "simplified chinese", "chi_sim",
    "zh-cn", "zh_cn", "zh", "chinese", "中文", "汉语",
})

# Han Unified Ideographs (all planes) — same range the script-leak check uses.
_HAN_CRE = re.compile(
    r"[一-鿿㐀-䶿"
    r"\U00020000-\U0002A6DF"
    r"\U0002A700-\U0002CEAF\U0002CEB0-\U0002EBEF]"
)
# Hiragana, Katakana (full- and half-width), Katakana Phonetic Extensions.
_KANA_CRE = re.compile(r"[぀-ヿㇰ-ㇿ･-ﾟ]")
# Japanese quotation marks that do not appear in Chinese.
_JP_BRACKET_CRE = re.compile(r"[『』「」]")
# Any Latin letter.
_LATIN_CRE = re.compile(r"[A-Za-z]")


def is_simplified_chinese_target(target_lang: str) -> bool:
    """Return True when *target_lang* resolves to Simplified Chinese (loose)."""
    from folith.textual import normalize_language
    return normalize_language(str(target_lang or "")) in _SIMP_CHINESE_LABELS


def _qualifies_for_han_check(src: str) -> bool:
    """True if *src* has Han chars and no kana, no Latin, no JP corner brackets.

    Segments with kana are definitively Japanese; segments with Latin are mixed
    or technical; segments with JP-exclusive quotation marks are likely Japanese
    prose.  Those are left to the existing actionable path unchanged.
    """
    s = str(src or "")
    return (
        bool(_HAN_CRE.search(s))
        and not _KANA_CRE.search(s)
        and not _JP_BRACKET_CRE.search(s)
        and not _LATIN_CRE.search(s)
    )


def _is_sjis_encodable(c: str) -> bool:
    """Return True if the single character *c* encodes without error in Shift-JIS."""
    try:
        c.encode('shift_jis')
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _is_gb2312_encodable(c: str) -> bool:
    """Return True if the single character *c* encodes without error in GB2312."""
    try:
        c.encode('gb2312')
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _non_simplified_chars(text: str) -> list[str]:
    """Return Han chars in *text* that are non-simplified, in order, deduplicated.

    A character is considered non-simplified when:
    * it is in ``_FORCED_TRUE`` (GB2312-compatible but Traditional/JP), OR
    * it encodes in Shift-JIS but NOT in GB2312, AND is NOT in ``_EXCEPTIONS``.

    Shift-JIS-only characters are Japanese or Traditional forms with no
    Simplified equivalent in GB2312.  Characters in ``_EXCEPTIONS`` are Shift-
    JIS-only but legitimately used in Simplified Chinese proper names or
    classical texts and should not trigger the check.
    """
    seen: set[str] = set()
    result: list[str] = []
    for c in str(text or ''):
        if c in seen or not _HAN_CRE.match(c):
            continue
        if c in _FORCED_TRUE:
            seen.add(c)
            result.append(c)
        elif c not in _EXCEPTIONS and _is_sjis_encodable(c) and not _is_gb2312_encodable(c):
            seen.add(c)
            result.append(c)
    return result


def _content_len_for_threshold(src_norm: str) -> int:
    """Length of *src_norm* after stripping a trailing page-number list.

    Index entries like "渡口重国 15, 26-30" report a length of 4 (the Han
    name) rather than the full string, so the threshold correctly places them
    in the short / informational group instead of inflating them into the
    long / actionable group.
    """
    return len(_PAGE_NUM_SUFFIX_RE.sub('', src_norm))


# ---------------------------------------------------------------------------
# #101: numeric / unit / tag structure checks
#
# These are mechanical signals, not semantic ones: a number that disappears, a
# unit that changes meaning (km -> m) or a markup tag that no longer exists are
# objective structural drift.  The findings carry a ``kind`` so the grouping
# layer can present one traceable group per token type; the checks stay local
# and deterministic (no model call, no prescan).
# ---------------------------------------------------------------------------

# ASCII word boundaries on purpose: ``\w`` is Unicode-aware, so "第0段" would
# look like a digit glued to a word and every CJK-adjacent number would be
# "missing".  Digits inside ASCII identifiers (``Term0``, ``v2``) are still
# excluded, and those are preserved-token territory, not numbers.
_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)*(?![A-Za-z0-9_.])")
_UNIT_TOKEN_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(km|kg|cm|mm|mi|ft|lb|oz|mph|kph|"
    r"ms|min|h|s|%|°C|℃)\b", re.IGNORECASE)
_UNIT_EQUIVALENTS = {
    "km": {"km", "公里", "千米"}, "kg": {"kg", "公斤", "千克"},
    "cm": {"cm", "厘米"}, "mm": {"mm", "毫米"}, "m": {"m", "米"},
    "mi": {"mi", "英里"}, "ft": {"ft", "英尺"}, "lb": {"lb", "磅"},
    "oz": {"oz", "盎司"}, "mph": {"mph", "英里每小时"},
    "kph": {"kph", "公里每小时"}, "ms": {"ms", "毫秒"},
    "min": {"min", "分钟"}, "h": {"h", "小时"}, "s": {"s", "秒"},
    "%": {"%", "％"}, "°C": {"°C", "℃", "摄氏度"},
}
_TAG_TOKEN_RE = re.compile(r"</?[A-Za-z!][^<>]{0,158}>")
_UNIT_CANONICAL = {
    "°C": "°C", "℃": "°C", "％": "%",
}


def _multiset(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _strip_preserved(text):
    """Remove identifiers that the preserved-token check already covers."""
    return _c.PRESERVE_RE.sub(" ", str(text or ""))


def check_number_structure(src, tgt, target_lang, segment_index=0):
    """Report source numbers that no longer occur in the target.

    Numbers inside URLs / citations / placeholders are excluded here: those are
    reported by the preserved-token check, and duplicating them would make one
    real problem look like two.  A unit's number is reported by the unit check.
    """
    findings = []
    src_clean = _strip_preserved(src)
    tgt_clean = _strip_preserved(tgt)
    unit_spans = [(m.start(), m.end()) for m in _UNIT_TOKEN_RE.finditer(src_clean)]

    def _inside_unit(position):
        return any(start <= position < end for start, end in unit_spans)

    src_numbers = [(m.group(0), m.start()) for m in _NUMBER_TOKEN_RE.finditer(src_clean)
                   if not _inside_unit(m.start())]
    if not src_numbers:
        return findings
    target_counts = _multiset(
        _c._numerals.fold_digits(m.group(0), target_lang)
        for m in _NUMBER_TOKEN_RE.finditer(tgt_clean))
    missing = {}
    for token, _position in src_numbers:
        folded = _c._numerals.fold_digits(token, target_lang)
        if target_counts.get(folded, 0) > 0:
            target_counts[folded] -= 1
        else:
            missing[token] = missing.get(token, 0) + 1
    for token, count in missing.items():
        findings.append(_deterministic_finding(
            segment_index, "actionable", "numeric_integrity",
            f"译文遗漏数字「{token}」",
            f"原文中的数字「{token}」在当前译文中找不到（出现 {count} 次）。"
            "数字、页码、金额或引用的改动会直接改变事实含义。",
            "核对原文数字并在译文中补回；若数字确实应随语境变化，请人工确认后忽略本提示。",
            source_span=token, target_span="", kind="number",
            detected_text=token, group_token=token, group_type="number",
            occurrence=count, reason=f"数字「{token}」在译文中缺失"))
    return findings


def check_unit_structure(src, tgt, target_lang, segment_index=0):
    """Report units whose number or unit system drifted between source and target."""
    findings = []
    src_clean = _strip_preserved(src)
    tgt_clean = _strip_preserved(tgt)
    target_lower = tgt_clean.casefold()
    for match in _UNIT_TOKEN_RE.finditer(src_clean):
        number, unit = match.group(1), match.group(2)
        canonical = _UNIT_CANONICAL.get(unit, unit)
        folded = _c._numerals.fold_digits(number, target_lang)
        allowed = _UNIT_EQUIVALENTS.get(canonical, {canonical})
        number_present = any(
            _c._numerals.fold_digits(other, target_lang) == folded
            for other in _NUMBER_TOKEN_RE.findall(tgt_clean))
        unit_present = any(
            candidate.casefold() in target_lower for candidate in allowed)
        if number_present and unit_present:
            continue
        problems = []
        if not number_present:
            problems.append("数字缺失")
        if not unit_present:
            problems.append("单位缺失或已改写")
        findings.append(_deterministic_finding(
            segment_index, "actionable", "unit_integrity",
            f"译文数量单位「{number} {canonical}」不完整",
            f"原文使用「{number} {canonical}」，译文缺少对应数字或单位（"
            + "、".join(problems) + "）。单位改写会改变量级或含义。",
            "核对数值与单位后补回或改写；无法从原文唯一确定的，请人工确认。",
            source_span=f"{number} {canonical}", target_span="",
            kind="unit", detected_text=f"{number} {canonical}",
            group_token=f"{canonical}", group_type="unit",
            expected_number=number, expected_unit=canonical,
            reason=f"单位「{number} {canonical}」在译文中不完整"))
    return findings


def check_tag_structure(src, tgt, segment_index=0):
    """Report markup tags lost from the target (structure drift, not semantics)."""
    findings = []
    src_tags = _multiset(_TAG_TOKEN_RE.findall(str(src or "")))
    tgt_tags = _multiset(_TAG_TOKEN_RE.findall(str(tgt or "")))
    missing = {tag: count - tgt_tags.get(tag, 0)
               for tag, count in src_tags.items()
               if count > tgt_tags.get(tag, 0)}
    for tag, count in missing.items():
        findings.append(_deterministic_finding(
            segment_index, "actionable", "format_integrity",
            f"译文遗漏标记「{tag}」",
            f"原文中的标记「{tag}」在译文中缺失（{count} 处）。标记丢失会破坏"
            "排版、链接或富文本结构。",
            "补回标记并保持其成对关系；不要为了可读性删除结构标记。",
            source_span=tag, target_span="", kind="tag", detected_text=tag,
            group_token=tag, group_type="tag", occurrence=count,
            reason=f"标记「{tag}」在译文中缺失"))
    return findings


def check_token_structure(src, tgt, target_lang, segment_index=0):
    """All #101 deterministic structure checks for one segment."""
    return (check_number_structure(src, tgt, target_lang, segment_index)
            + check_unit_structure(src, tgt, target_lang, segment_index)
            + check_tag_structure(src, tgt, segment_index))
