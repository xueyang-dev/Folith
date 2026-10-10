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


def import_tmx(file_stream, project_id=None, target_lang=None):
    """导入 TMX 翻译记忆（Trados / memoQ 等导出的标准格式）。

    按 <tu> 的 <tuv><seg> 文本对入库：仅接受源文含字母/数字且译文非空的
    单元；与现有翻译记忆冲突的源文跳过（不覆盖项目内已审校条目）。

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
        for tuv in tu:
            if _local_name(tuv.tag) != "tuv":
                continue
            seg = next((c for c in tuv if _local_name(c.tag) == "seg"), None)
            if seg is not None and (seg.text or "").strip():
                texts.append(seg.text.strip())
                languages.append(tuv.get("{http://www.w3.org/XML/1998/namespace}lang")
                                 or tuv.get("lang") or "")
            if len(texts) >= 2:
                break
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
    locked_translate = [e for e in glossary if e["behavior"] == "translate" and e["status"] == "locked"]
    preserve = [e for e in glossary if e["behavior"] == "preserve"]
    provisional = [e for e in glossary if e["behavior"] == "translate" and e["status"] != "locked"]
    lines = []
    if locked_translate:
        lines.append("【锁定术语（必须使用首选译名，不得使用禁止译名）】：")
        for e in locked_translate:
            seg = f"- {e['source']} -> {e['preferred']}"
            if e["forbidden"]:
                seg += f"（禁止：{'、'.join(e['forbidden'])}）"
            lines.append(seg)
    if preserve:
        lines.append("【必须保留原文的术语/名称】：" + "、".join(e["source"] for e in preserve))
    if provisional:
        lines.append("【建议术语（仅供参考，请优先采用）】：")
        for e in provisional:
            lines.append(f"- {e['source']} -> {e['target']}")
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


def _count_sentences(text):
    """粗粒度句数统计：按终结符切分（引号/括号闭合归并到前一句）。"""
    text = _c._ABBREV_RE.sub(" ", text)
    parts = re.split(r"[.!?…。！？؟]+[”\"'’)\]]*", text)
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
