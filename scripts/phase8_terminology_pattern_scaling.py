"""Phase 8 — terminology pattern scaling baseline (micro benchmark).

测量对象：`terminology.term_matches` 在**真实访问形态**下的 regex 编译行为，即
「整个术语表 × 每批多个段落 × 多批」。这是 translation setup / review 两条热路径
共同的核心循环。

本脚本本身**不优化任何东西**：它只测量当前 revision。三个层级的计数器互相独立，
用来把「重复编译」这件事定位到具体哪一层：

| 计数器 | 含义 |
| --- | --- |
| `pattern_calls` / `hits` / `misses` | 本项目那层 4096 条目 LRU（`_TERM_PATTERN_CACHE`） |
| `re_compile_calls` | 走到 `re.compile()` 入口的次数（应恒等于 misses） |
| `sre_compiles` | **真实编译次数**（`re._compiler.compile`，两层缓存都未命中） |

`re_compile_calls` 与 `sre_compiles` 的差就是 Python `re` 模块自带那 512 条缓存
吸掉的量。判断 cliff 只看 `sre_compiles`：它才是不可摊销的 CPU 成本。

用法：

    ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py \
        --glossary-sizes 100,500,1000,4096,5000,10000,20000 \
        --json tmp/phase8/micro_current.json

peak RSS 是进程高水位，一个进程里测多档会互相污染，因此：

    for g in 100 500 1000 4096 5000 10000 20000; do
        ./venv/bin/python scripts/phase8_terminology_pattern_scaling.py --glossary-sizes $g
    done

（`--glossary-sizes` 传单值时就是"只测这一档"，RSS 即该档的真实高水位。）
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import terminology  # noqa: E402

DEFAULT_GLOSSARY_SIZES = (100, 500, 1000, 4096, 5000, 10000, 20000)
DEFAULT_SEGMENTS = 6
DEFAULT_BATCHES = 3

# ---------------- 术语夹具（形状覆盖 §6 的语义面）----------------

#: 音节表。用伪词（而不是「Stem123」这种带序号的串）是为了让**字符串形态**贴近真实
#: 术语表：长度、大小写、连字符、多词结构都会影响 `re` 的编译与匹配开销。
_SYLLABLES = (
    "trans", "logue", "emic", "etic", "morph", "synt", "pragm", "sema",
    "lexic", "phon", "graphe", "socio", "cogni", "discou", "narrat", "rhetor",
    "hermen", "polys", "idio", "inter",
)
_BASE = len(_SYLLABLES)
_SECOND_STEMS = ("Theory", "Model", "Analysis", "Framework", "Approach", "Shift")
_CJK_TERMS = (
    "目的论", "功能对等", "翻译规范", "语域", "语篇", "阐释学", "多元系统",
    "互文性", "归化", "异化", "直译", "意译", "语料库", "术语学", "可译性",
)


def _pseudo_word(index: int) -> str:
    """由 index 的 B 进制展开生成唯一的伪词（4 音节 → 160,000 个不同词）。"""
    parts = []
    value = index
    for _ in range(4):
        parts.append(_SYLLABLES[value % _BASE])
        value //= _BASE
    word = "".join(parts).rstrip(" ")
    return word


def _ascii_source(index: int) -> str:
    """6 种 ASCII 形状轮转：单词 / 多词 / 连字符 / 标点 / 首字母大写 / 下划线。"""
    shape = index % 6
    w = _pseudo_word(index)
    w2 = _pseudo_word(index + 3_000)
    if shape == 0:
        return w
    if shape == 1:
        return f"{w} {_SECOND_STEMS[index % len(_SECOND_STEMS)].lower()}"
    if shape == 2:
        return f"{w}-{w2[:6]}"
    if shape == 3:
        return f"({w}/{w2[:5]})"
    if shape == 4:
        return f"{w.capitalize()} {w2.capitalize()}"
    return f"{w}_{w2[:4]}"


def _cjk_source(index: int) -> str:
    """3 种 CJK 形状：纯 CJK / 前混 / 后混（都走子串分支，不编译 pattern）。

    用**整个**伪词而不是截断前缀：伪词的低位数字落在字符串前面，截断会把基数砍到
    20 种，撞出重复条目。
    """
    base = _CJK_TERMS[index % len(_CJK_TERMS)]
    word = _pseudo_word(index)
    shape = index % 3
    if shape == 0:
        return f"{base}{word}"
    if shape == 1:
        return f"{word}{base}"
    return f"{base}{word}{base[:2]}"


def build_glossary(size: int, cjk_fraction: float = 0.10) -> List[Dict[str, Any]]:
    """确定性生成 size 条**互不相同**的 normalized 术语条目。

    `cjk_fraction` 控制走子串分支（不编译正则）的条目比例。默认 0.10，贴近真实
    学术术语表的 CJK 占比；需要精确压 `_TERM_PATTERN_CACHE_MAX` 边界时可设 0。
    """
    from folith import models

    n_cjk = int(round(size * max(0.0, min(1.0, cjk_fraction))))
    raw: List[Dict[str, Any]] = []
    seen = set()
    index = 0
    while len(raw) < size:
        if index < n_cjk:
            source = _cjk_source(index)
        else:
            source = _ascii_source(index - n_cjk)
        index += 1
        if source in seen:
            source = f"{source} {index}"   # 兜底去重（正常情况下不会触发）
            if source in seen:
                continue
        seen.add(source)
        raw.append({
            "source": source,
            "target": f"译名{len(raw)}",
            "behavior": "translate",
            "status": "locked" if len(raw) % 4 == 0 else "provisional",
            "scope": "document",
        })
    return models.normalize_glossary(raw)


# ---------------- 段落夹具 ----------------

_SENTENCE_TEMPLATES = (
    "The chapter opens with a short survey of the field and its neighbours.",
    "Readers familiar with the earlier volume may skip the next three pages.",
    "A second example makes the same point with a different vocabulary.",
    "This section closes by listing the questions left unanswered above.",
    "The argument depends on a distinction drawn in the previous paragraph.",
    "Much of the evidence comes from a corpus assembled over five years.",
    "The author returns to this problem at the very end of the book.",
    "Translation practice here diverges sharply from the stated ideal.",
)


def build_segments(batches: int, per_batch: int) -> List[List[str]]:
    """每批一个段落窗口。术语命中率刻意很低，让扫描不因短路提前结束。"""
    pool = list(_SENTENCE_TEMPLATES)
    return [
        [pool[(b * per_batch + i) % len(pool)] for i in range(per_batch)]
        for b in range(batches)
    ]


# ---------------- 计数 ----------------

class Counters:
    """三层计数器 + 安装/还原。不改任何匹配语义。"""

    def __init__(self) -> None:
        self.data = {
            "pattern_calls": 0, "pattern_hits": 0, "pattern_misses": 0,
            "re_compile_calls": 0, "sre_compiles": 0,
            "term_matches_calls": 0,
        }
        self._saved: List[Tuple[Any, str, Any]] = []

    def _patch(self, owner: Any, name: str, value: Any) -> None:
        self._saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self) -> "Counters":
        data = self.data
        counters = self
        real_term_pattern = terminology._term_pattern
        real_re_compile = re.compile
        real_sre_compile = re._compiler.compile  # type: ignore[attr-defined]
        real_term_matches = terminology.term_matches

        def counting_term_pattern(term: str):
            data["pattern_calls"] += 1
            if term in terminology._TERM_PATTERN_CACHE:
                data["pattern_hits"] += 1
            else:
                data["pattern_misses"] += 1
            return real_term_pattern(term)

        def counting_re_compile(*args: Any, **kwargs: Any):
            data["re_compile_calls"] += 1
            return real_re_compile(*args, **kwargs)

        def counting_sre_compile(*args: Any, **kwargs: Any):
            data["sre_compiles"] += 1
            return real_sre_compile(*args, **kwargs)

        # `term_matches` 把 `term_matches` 当模块全局查表，因此包装要落在模块属性上，
        # 这样连 `from terminology import term_matches` 之外的调用点也一并覆盖。
        def counting_term_matches(term: str, text: str) -> bool:
            data["term_matches_calls"] += 1
            return real_term_matches(term, text)

        counters._patch(terminology, "_term_pattern", counting_term_pattern)
        counters._patch(terminology, "term_matches", counting_term_matches)
        counters._patch(re, "compile", counting_re_compile)
        counters._patch(re._compiler, "compile", counting_sre_compile)  # type: ignore[attr-defined]
        return self

    def __exit__(self, *exc: Any) -> None:
        for owner, name, original in reversed(self._saved):
            setattr(owner, name, original)
        self._saved.clear()


def _rss_kb() -> int:
    """当前进程 RSS（KiB）。macOS 的 ru_maxrss 单位是字节，Linux 是 KiB。"""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw // 1024 if sys.platform == "darwin" else raw


# ---------------- 单档测量 ----------------

def measure_cell(entries: Sequence[Dict[str, Any]], batches: List[List[str]],
                 repeat: int, variant: str = "current",
                 trace: bool = True) -> Dict[str, Any]:
    sources = [e["source"] for e in entries]
    unique_sources = len(set(sources))
    unique_patterns = len({s for s in sources if not terminology._CJK_RE.search(s)})

    best: Dict[str, Any] = {}
    for _ in range(max(1, repeat)):
        # 两个缓存都要清干净：
        # - `_TERM_PATTERN_CACHE` 是本项目那层（4096）；
        # - `re.purge()` 清 `re` 模块自身的三级缓存（512 条，含 `_compiler._cache`）。
        # 只清前者会得到**被上一档污染**的数字：同进程里跑过 G=500 之后，G=1000 的
        # 前 450 个 pattern 已在 `re` 缓存里，编译数会假报 450 而不是 900。
        terminology.reset_term_pattern_cache()
        terminology.reset_compiled_glossary_cache()
        re.purge()

        matcher = None
        build_cpu = 0.0
        build_wall = 0.0
        retained_peak = 0
        retained_current = 0
        rss_before = _rss_kb()
        # 计数器在 build **之前**安装：编译事件要区分「构建期一次性」与「扫描期重复」，
        # 这正是本轮的核心判据。
        counters = Counters()
        with counters:
            if variant == "compiled":
                if trace:
                    import tracemalloc
                    tracemalloc.start()
                build_wall0, build_cpu0 = time.perf_counter(), time.process_time()
                matcher = terminology.compiled_glossary(entries)
                build_wall = time.perf_counter() - build_wall0
                build_cpu = time.process_time() - build_cpu0
                if trace:
                    retained_current, retained_peak = tracemalloc.get_traced_memory()
                    tracemalloc.stop()
                else:
                    retained_current = matcher.retained_bytes()
                    retained_peak = retained_current
            build_compiles = counters.data["sre_compiles"]
            rss_after_build = _rss_kb()

            wall0, cpu0 = time.perf_counter(), time.process_time()
            for segments in batches:
                for source in sources:
                    # 与生产代码同形：any() 短路
                    if matcher is None:
                        any(terminology.term_matches(source, seg)
                            for seg in segments)
                    else:
                        any(matcher.matches(source, seg) for seg in segments)
            wall = time.perf_counter() - wall0
            cpu = time.process_time() - cpu0

        row = dict(counters.data)
        scan_compiles = row["sre_compiles"] - build_compiles
        row.update({
            "wall_s": wall, "cpu_s": cpu,
            "scan_sre_compiles": scan_compiles,
            "build_sre_compiles": build_compiles,
            "build_cpu_s": build_cpu, "build_wall_s": build_wall,
            "matcher_retained_bytes": retained_current,
            "matcher_peak_alloc_bytes": retained_peak,
            "rss_before_build_kb": rss_before,
            "rss_after_build_kb": rss_after_build,
            "total_cpu_s": cpu + build_cpu,
        })
        if matcher is not None:
            row["matcher_pattern_count"] = matcher.pattern_count
            row["matcher_plain_count"] = matcher.plain_count
            row["matcher_retained_bytes_est"] = matcher.retained_bytes()
        # 跨运行取 min（沙箱负载漂移）
        if not best or row["wall_s"] < best["wall_s"]:
            best = row
            best["compiles_over_patterns"] = (
                row["sre_compiles"] / unique_patterns if unique_patterns else 0.0)

    batch_count = len(batches)
    best.update({
        "variant": variant,
        "entries": len(entries),
        "unique_sources": unique_sources,
        "unique_patterns": unique_patterns,
        "unique_cjk_terms": unique_sources - unique_patterns,
        "batches": batch_count,
        "segments_per_batch": len(batches[0]) if batches else 0,
        "term_matches_calls_per_pass": row["term_matches_calls"] // max(1, batch_count),
        "sre_compiles_per_pass": scan_compiles // max(1, batch_count),
        "compiles_per_unique_pattern": (
            row["sre_compiles"] / unique_patterns if unique_patterns else 0.0),
        "cache_size_after": terminology.term_pattern_cache_info()["size"],
        "cache_max": terminology.term_pattern_cache_info()["max"],
        "peak_rss_kb": _rss_kb(),
    })
    terminology.reset_term_pattern_cache()
    terminology.reset_compiled_glossary_cache()
    return best


def print_table(rows: List[Dict[str, Any]]) -> None:
    head = ("G", "uniq", "pat", "calls", "hits", "miss", "re.compile", "sre",
            "sre/pass", "cpu_s", "wall_s", "RSS_MB")
    print("  " + "".join(f"{h:>11}" for h in head))
    for r in rows:
        print("  " + "".join(f"{v:>11}" for v in (
            r["entries"], r["unique_sources"], r["unique_patterns"],
            r["term_matches_calls"], r["pattern_hits"], r["pattern_misses"],
            r["re_compile_calls"], r["sre_compiles"], r["sre_compiles_per_pass"],
            f"{r['cpu_s']:.3f}", f"{r['wall_s']:.3f}",
            f"{r['peak_rss_kb'] / 1024:.1f}",
        )))
    print()
    print("  每档说明：")
    for r in rows:
        extra = ""
        if r.get("variant") == "compiled":
            extra = (f"  build_cpu={r['build_cpu_s']:.3f}s"
                     f"  retained={r['matcher_retained_bytes'] / 1024:.0f}KiB"
                     f"  est={r['matcher_retained_bytes_est'] / 1024:.0f}KiB")
        print(f"    G={r['entries']:>6}  unique_patterns={r['unique_patterns']:>6}"
              f"  sre_compiles/unique_pattern={r['compiles_per_unique_pattern']:>7.2f}"
              f"  cache={r['cache_size_after']}/{r['cache_max']}{extra}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glossary-sizes", default=",".join(
        str(v) for v in DEFAULT_GLOSSARY_SIZES))
    parser.add_argument("--segments", type=int, default=DEFAULT_SEGMENTS)
    parser.add_argument("--batches", type=int, default=DEFAULT_BATCHES)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--cjk-fraction", type=float, default=0.10,
                        help="走子串分支（不编译正则）的条目比例；0 = 全 ASCII，"
                             "用于精确压 _TERM_PATTERN_CACHE_MAX 边界")
    parser.add_argument("--variant", default="current",
                        choices=("current", "compiled"),
                        help="current = 现状（term 级 4096 LRU）；"
                             "compiled = CompiledGlossary 路径")
    parser.add_argument("--no-tracemalloc", action="store_true",
                        help="关掉保留量追踪（G 很大时 tracemalloc 自身开销会顶爆进程）")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    sizes = [int(v) for v in str(args.glossary_sizes).split(",") if v.strip()]
    batches = build_segments(args.batches, args.segments)

    print(f"variant={args.variant} segments_per_batch={args.segments} "
          f"batches={args.batches} repeat={args.repeat} "
          f"cjk_fraction={args.cjk_fraction}")
    rows = []
    for size in sizes:
        entries = build_glossary(size, args.cjk_fraction)
        row = measure_cell(entries, batches, args.repeat, args.variant,
                           trace=not args.no_tracemalloc)
        rows.append(row)
        print(f"  G={size} done: sre={row['sre_compiles']} "
              f"cpu={row['cpu_s']:.3f}s", file=sys.stderr)

    print_table(rows)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "config": {"segments_per_batch": args.segments,
                       "batches": args.batches, "repeat": args.repeat,
                       "cjk_fraction": args.cjk_fraction,
                       "variant": args.variant},
            "rows": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
