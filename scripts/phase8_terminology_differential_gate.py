"""Phase 8 §7 — terminology matcher differential gate.

把**改造前的匹配实现**作为 oracle 留在文件里（`legacy_term_matches`），用确定性随机
生成的大量用例做逐例差分：

    legacy_term_matches(term, text)  ==  CompiledGlossary.matches(term, text)

要求 100%。任何一例不等即 FAIL，并打印最小复现。

维度覆盖 §6 的全部要求：ASCII 单词 / CJK / 含标点 / 连字符 / 大小写 /
词边界 / 类复数邻词 / 子串假阳性 / 正则元字符 / 空白归一 / Unicode 变体
（NFC vs NFD、全角、希腊字母）/ 不在术语表内的字符串（回落路径）。

用法：

    ./venv/bin/python scripts/phase8_terminology_differential_gate.py \
        --cases 20000 --glossary-size 5000 --json tmp/phase8/diff_gate.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import terminology  # noqa: E402


# ---------------- oracle：改造前的实现（每次现编译，无任何缓存）----------------

def legacy_term_matches(term: str, text: str) -> bool:
    """Phase 8 之前的 `terminology.term_matches` **逐字**实现。

    刻意保留一份 legacy copy 作为 test oracle：它是语义的权威定义，不随生产实现
    变化。走 `terminology.re` 而不是函数内 `import re`，这样审计脚本对编译次数的
    计数代理也能观测到这一段。
    """
    term = (term or "").strip()
    text = text or ""
    if not term or not text:
        return False
    if terminology._CJK_RE.search(term):
        return term in text
    pattern = terminology.re.compile(
        r"(?<![A-Za-z0-9_])" + terminology.re.escape(term) + r"(?![A-Za-z0-9_])",
        terminology.re.IGNORECASE)
    return bool(pattern.search(text))


# ---------------- 夹具语料 ----------------

_ASCII_TERMS = (
    "AI", "MT", "GA", "term", "alpha", "BERT", "L2", "co-op", "e.g.",
    "C++", "A/B", "x.y", "foo_bar", "Skopos", "MTI", "GAB", "a-b", "MT.",
    "séance", "naïve", "Straße", "Ω", "µ", "ﬁle", "é", "目的论EN",
)
_CJK_TERMS = ("目的论", "功能对等", "翻译", "术语", "語", "简体字", "繁體字", "翻译目的")
_MIXED_TERMS = ("BERT模型", "L2习得", "MT系统", "AI·ML", "译页Folith")
_ALL_TERMS = _ASCII_TERMS + _CJK_TERMS + _MIXED_TERMS

_FILLER = (
    "the", "a", "of", "and", "in", "theory", "model", "text", "readers",
    "chapter", "three", "analysis", "系统", "翻译", "理论", "读者", "段落",
)
#: 前缀/后缀：既覆盖正常词界，也覆盖「粘连」与「类复数」邻词。
_PREFIXES = ("", " ", "(", "[", "\"", "the ", "pre", "x", "非", "】", "\t", "\n")
_SUFFIXES = ("", " ", ")", "]", ".", ",", "s", "es", "ing", "20", "_", "后", "。", "\n")
_SPLITTERS = (" ", "", ", ", ". ", "、", "，", "\t", "\n", " - ", "/")
_UNICODE_ALTS = {
    "é": ("\u00e9", "e\u0301"),          # NFC vs NFD
    "ﬁ": ("\ufb01", "fi"),                # 连字 vs 展开
    "Ω": ("\u03a9", "\u2126"),            # 希腊字母 vs 欧姆符号
    "，": ("\uff0c", ","),                # 全角 vs 半角
    "Ａ": ("\uff21", "A"),                # 全角字母
}


def _mutate_unicode(text: str, rng: random.Random) -> str:
    for key, options in _UNICODE_ALTS.items():
        if key in text:
            text = text.replace(key, rng.choice(options))
    if rng.random() < 0.25:
        text = unicodedata.normalize(rng.choice(("NFC", "NFD")), text)
    return text


def _absent_text(rng: random.Random) -> str:
    return " ".join(rng.choice(_FILLER) for _ in range(rng.randint(1, 12)))


def _has_cjk(term: str) -> bool:
    return bool(terminology._CJK_RE.search(term))


def _pick(rng: random.Random, pool: Sequence[str], predicate) -> str:
    candidates = [t for t in pool if predicate(t)]
    return rng.choice(candidates) if candidates else rng.choice(pool)


def _embed(rng: random.Random, term: str, *, glue: str = "") -> str:
    """把 term 埋进 filler 文本，前后拼 `glue`（用于制造/破坏词界）。"""
    before = rng.choice(_SPLITTERS).join(
        rng.choice(_FILLER) for _ in range(rng.randint(0, 4)))
    after = rng.choice(_SPLITTERS).join(
        rng.choice(_FILLER) for _ in range(rng.randint(0, 4)))
    lead = rng.choice(("", " ", "\t", "\n", "(", "\""))
    tail = rng.choice(("", " ", ".", ",", ")", "\n"))
    return f"{before}{lead}{glue}{term}{glue}{tail}{after}"


def _case_variant(rng: random.Random, term: str) -> str:
    return rng.choice((term, term.upper(), term.lower(), term.title(),
                       term.swapcase()))


#: §6 要求的每个语义维度一个生成器。权重决定该维度在总用例中的占比。
_WEIGHTED = (
    ("ascii_word", 8), ("multiword", 5), ("cjk", 6), ("mixed", 5),
    ("punctuation", 6), ("hyphen", 5), ("case", 10), ("word_boundary", 8),
    ("plural_like", 8), ("substring", 8), ("regex_meta", 6),
    ("whitespace", 6), ("unicode", 8), ("absent", 7), ("fallback", 5),
    ("empty", 3),
)


def _build_case(rng: random.Random, dimension: str, index: int,
                pool: Sequence[str]) -> Dict[str, Any]:
    ascii_plain = lambda t: not _has_cjk(t) and not any(
        c in t for c in ".+*/[](){}|^$\\-")
    ascii_any = lambda t: not _has_cjk(t)
    cjk_only = _has_cjk
    mixed = lambda t: _has_cjk(t) and not t.isascii()
    meta = lambda t: any(c in t for c in ".+*/[](){}|^$\\")
    hyphen = lambda t: "-" in t
    spaced = lambda t: " " in t

    if dimension == "ascii_word":
        term = _pick(rng, pool, ascii_plain)
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "multiword":
        term = _pick(rng, pool, lambda t: spaced(t) and not _has_cjk(t))
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "cjk":
        term = _pick(rng, pool, cjk_only)
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "mixed":
        term = _pick(rng, pool, mixed)
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "punctuation":
        term = _pick(rng, pool, lambda t: any(c in t for c in ".,/"))
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "hyphen":
        term = _pick(rng, pool, hyphen)
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "case":
        term = _pick(rng, pool, ascii_any)
        return {"term": term, "text": _embed(rng, _case_variant(rng, term))}
    if dimension == "word_boundary":
        term = _pick(rng, pool, ascii_plain)
        glue = rng.choice(("a", "z", "9", "_", "0"))
        return {"term": term, "text": _embed(rng, term, glue=glue)}
    if dimension == "plural_like":
        term = _pick(rng, pool, ascii_plain)
        return {"term": term,
                "text": _embed(rng, term + rng.choice(("s", "es", "ed", "ing")))}
    if dimension == "substring":
        term = _pick(rng, pool, lambda t: ascii_plain(t) and len(t) <= 6)
        return {"term": term,
                "text": _embed(rng, rng.choice(("pre", "x", "non")) + term
                               + rng.choice(("post", "s", "able")))}
    if dimension == "regex_meta":
        term = _pick(rng, pool, meta)
        return {"term": term, "text": _embed(rng, term)}
    if dimension == "whitespace":
        term = _pick(rng, pool, ascii_any)
        padded = rng.choice((f"  {term}  ", f"\t{term}", f"{term}\n"))
        text = _embed(rng, term)
        if rng.random() < 0.5:
            text = text.replace(" ", rng.choice(("  ", "\t", "\n")), 1)
        return {"term": padded, "text": text}
    if dimension == "unicode":
        term = _pick(rng, pool, lambda t: any(k in t for k in _UNICODE_ALTS))
        return {"term": term,
                "text": _mutate_unicode(_embed(rng, term), rng)}
    if dimension == "absent":
        term = _pick(rng, pool, ascii_any)
        return {"term": term, "text": _mutate_unicode(_absent_text(rng), rng)}
    if dimension == "fallback":
        term = rng.choice(_ALL_TERMS) + f"_{index}"
        return {"term": term, "text": _mutate_unicode(_embed(rng, term), rng)}
    term = rng.choice(("", "  ", "\t", None, "x"))
    return {"term": term, "text": rng.choice(("", "  ", "text", None, "  x  "))}


def generate_cases(rng: random.Random, count: int,
                   glossary_terms: Sequence[str]) -> List[Dict[str, Any]]:
    """确定性生成 count 个差分用例（维度驱动，覆盖 §6 全部语义面）。"""
    names = [name for name, _ in _WEIGHTED]
    weights = [weight for _, weight in _WEIGHTED]
    pool = list(glossary_terms)
    cases: List[Dict[str, Any]] = []
    for index in range(count):
        dimension = rng.choices(names, weights=weights, k=1)[0]
        case = _build_case(rng, dimension, index, pool)
        case["dimension"] = dimension
        cases.append(case)
    return cases


def run_gate(cases: Sequence[Dict[str, Any]],
             glossary_terms: Sequence[str]) -> Dict[str, Any]:
    entries = [{"source": term, "target": "译名", "status": "locked"}
               for term in glossary_terms]
    from folith import models
    matcher = terminology.compiled_glossary(models.normalize_glossary(entries))

    mismatches: List[Dict[str, Any]] = []
    positives = 0
    by_dimension: Dict[str, int] = {}
    for case in cases:
        term, text = case["term"], case["text"]
        expected = legacy_term_matches(term, text)
        actual = matcher.matches(term, text)
        by_dimension[case["dimension"]] = by_dimension.get(case["dimension"], 0) + 1
        if expected:
            positives += 1
        if expected != actual:
            mismatches.append({**case, "legacy": expected, "compiled": actual})

    return {
        "cases": len(cases),
        "positive_matches": positives,
        "negative_matches": len(cases) - positives,
        "glossary_terms": len(glossary_terms),
        "compiled_patterns": matcher.pattern_count,
        "compiled_plain": matcher.plain_count,
        "dimensions": dict(sorted(by_dimension.items())),
        "mismatches": len(mismatches),
        "first_mismatches": mismatches[:10],
        "result": "EQUIVALENT" if not mismatches else "DIFFERENT",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--glossary-size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    glossary_terms = list(dict.fromkeys(
        list(_ALL_TERMS)
        + [f"term{index}"
           for index in range(max(0, args.glossary_size - len(_ALL_TERMS)))]))
    cases = generate_cases(rng, args.cases, glossary_terms)
    report = run_gate(cases, glossary_terms)
    report["seed"] = args.seed

    print(f"seed={args.seed} cases={report['cases']} "
          f"glossary_terms={report['glossary_terms']}")
    print(f"  positive_matches={report['positive_matches']} "
          f"negative_matches={report['negative_matches']}")
    print("  dimensions:")
    for name, count in report["dimensions"].items():
        print(f"    {name:<28}{count:>8}")
    print(f"  compiled: patterns={report['compiled_patterns']} "
          f"plain={report['compiled_plain']}")
    print(f"  mismatches={report['mismatches']}")
    for bad in report["first_mismatches"]:
        print(f"    MISMATCH {bad}")
    print(f"RESULT: {report['result']}")

    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"wrote {target}")
    return 0 if report["result"] == "EQUIVALENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
