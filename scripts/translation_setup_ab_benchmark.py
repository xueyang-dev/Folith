#!/usr/bin/env python3
"""Performance Phase 5：translation `_setup` 优化的**同进程中立化对照**。

为什么不能直接比两次跨进程 wall：本机不是静默环境（常驻 Streamlit + 并行工作流），
同一份代码连续两次跑实测能差 **64%**（wall 7.594s vs 12.455s）。所以这里把优化前的
函数体作为 shim **装回同一个进程**，交替跑 `legacy / opt / opt / legacy` 四段并取中位数
—— 对称排列可以抵消"越跑越慢"的单调漂移。同时报 `time.process_time()`：流水线
translator 并发恒为 1，CPU 时间对外部负载几乎免疫，是判"省下来的是不是我们的 CPU"的
主证据。

用法：

    venv/bin/python scripts/translation_setup_ab_benchmark.py            # 全部用例
    venv/bin/python scripts/translation_setup_ab_benchmark.py micro      # 函数级对照
    venv/bin/python scripts/translation_setup_ab_benchmark.py loaded_n100 doc_n8000

注意：`loaded_*` 用例单个 legacy 段可能跑好几分钟（glossary 成本随"段落数 × 术语数"
增长），建议连同 `--micro-only` 的 profiler 一起按需选择。

对仓库是只读的：shim 只存在于进程内，退出时按逆序还原，并清空模式缓存。
相关：`scripts/translation_setup_audit.py`（字节级同源的 CaseConfig / 只读 profiler）、
`docs/translation-setup-hotpath-audit.md`。
"""
from __future__ import annotations

import contextlib
import io
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from folith import context as ctx          # noqa: E402
from folith import terminology as term_mod  # noqa: E402

import translation_setup_audit as audit          # noqa: E402


# --------------------------------------------------------------------------- #
# 优化前的函数体（逐字副本，Phase 5 替换掉的那一版）
# --------------------------------------------------------------------------- #
def legacy_select_target_context(pairs, before_index, limit=2):
    """Opt C：反向扫描改写之前的 `select_target_context`。"""
    candidates = []
    for index, pair in enumerate(list(pairs)[:max(0, before_index)]):
        target = pair.get("accepted_target") or pair.get("target")
        if target:
            candidates.append({
                "segment_index": index,
                "source": ctx._clean(pair.get("source"), 800),
                "target": ctx._clean(target, 1200),
                "level": ctx.target_context_level(pair),
            })
    selected = []
    for level in ctx.TARGET_CONTEXT_LEVELS:
        selected.extend(item for item in reversed(candidates)
                        if item["level"] == level)
        if len(selected) >= max(0, limit):
            break
    return sorted(selected[:max(0, limit)],
                  key=lambda item: item["segment_index"])


def legacy_recent_target_context(pairs, before_index, count=2,
                                 exclude_indexes=None):
    """Opt A：`_process_batch_payload` 原来那个调用点表达式。"""
    return [
        item for item in legacy_select_target_context(
            pairs, before_index, limit=max(count, before_index))
        if item.get("segment_index") not in (exclude_indexes or ())
    ][-count:]


def legacy_term_matches(term: str, text: str) -> bool:
    """Opt B：有界模式缓存之前的 `term_matches`。

    走 `term_mod.re` 而不是函数内 `import re`，这样审计脚本的编译计数代理仍能观测到
    这一段（否则 legacy 段会假报 0 次编译）。
    """
    term = (term or "").strip()
    text = text or ""
    if not term or not text:
        return False
    if term_mod._CJK_RE.search(term):
        return term in text
    pattern = term_mod.re.compile(
        r"(?<![A-Za-z0-9_])" + term_mod.re.escape(term) + r"(?![A-Za-z0-9_])",
        term_mod.re.IGNORECASE)
    return bool(pattern.search(text))


SHIMS = (
    (ctx, "select_target_context", legacy_select_target_context),
    (ctx, "recent_target_context", legacy_recent_target_context),
    (term_mod, "term_matches", legacy_term_matches),
)


@contextlib.contextmanager
def neutralised(enabled: bool):
    """在块内安装 legacy 函数体；退出时按逆序还原并清空模式缓存。"""
    saved = []
    if enabled:
        for owner, name, replacement in SHIMS:
            saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, replacement)
        term_mod.reset_term_pattern_cache()
    try:
        yield
    finally:
        for owner, name, original in reversed(saved):
            setattr(owner, name, original)
        term_mod.reset_term_pattern_cache()


def measure(config, enabled: bool):
    """跑一段，返回 wall / CPU / 审计脚本自己的计数。"""
    wall0, cpu0 = time.perf_counter(), time.process_time()
    with neutralised(enabled), \
            contextlib.redirect_stdout(io.StringIO()):
        result = audit.run_case(config)
    return {
        "variant": "legacy" if enabled else "opt",
        "wall_s": round(time.perf_counter() - wall0, 3),
        "cpu_s": round(time.process_time() - cpu0, 3),
        "setup_s": round(result["total_setup_s"], 4),
        "batches": result["batches"],
        "regex_compiles": result["regex_compiles"],
    }


CASES = {
    # 术语密集：同时覆盖 Opt B（1000 条术语表）与 Opt A / Opt C。单批 glossary 成本
    # 随"段落数 × 术语数"增长，所以 `loaded_n300` 的单个 legacy 段就会超过 5 分钟 ——
    # 需要快速结果时选 `loaded_n100`。
    "loaded_n100": audit.CaseConfig(
        100, batch_size=4, glossary_size=1000, entity_count=1000,
        candidate_count=1000, section_count=25, enable_review=True),
    "loaded_n300": audit.CaseConfig(
        300, batch_size=4, glossary_size=1000, entity_count=1000,
        candidate_count=1000, section_count=12, enable_review=True),
    # 长文档：无术语表，O(n^2) 的上下文扫描占主导，覆盖 Opt A 与 Opt C。
    "doc_n5000": audit.CaseConfig(
        5000, batch_size=4, glossary_size=0, entity_count=0,
        candidate_count=0, section_count=200, enable_review=True),
    "doc_n8000": audit.CaseConfig(
        8000, batch_size=4, glossary_size=0, entity_count=0,
        candidate_count=0, section_count=320, enable_review=True),
    "doc_n10000": audit.CaseConfig(
        10000, batch_size=4, glossary_size=0, entity_count=0,
        candidate_count=0, section_count=400, enable_review=True),
}


def micro_function_level() -> int:
    """函数级对照：同一份输入上比较 legacy 与 opt，并顺带断言输出逐字相同。"""
    import random

    print("=== select_target_context (Opt C) ===")
    print(f"{'n':>7} {'provenance':>11} {'legacy ms':>11} {'opt ms':>10} "
          f"{'speedup':>9} {'equal':>6}")
    for size in (1000, 5000, 10000, 20000):
        for mixed in (False, True):
            pairs = []
            for index in range(size):
                if not mixed:
                    prov, reviewed = "generated", False
                elif index % 97 == 0:
                    prov, reviewed = "human", True
                else:
                    prov, reviewed = "generated", False
                pairs.append({"source": "s" * 800, "target": "t" * 1200,
                              "accepted_target": "t" * 1200 if reviewed else None,
                              "target_provenance": prov, "reviewed": reviewed,
                              "from_tm": False})
            rows = []
            for enabled in (True, False, False, True):
                samples = []
                with neutralised(enabled):
                    for _ in range(5):
                        t0 = time.perf_counter()
                        out = ctx.select_target_context(pairs, size, limit=2)
                        samples.append((time.perf_counter() - t0) * 1000)
                rows.append((enabled, statistics.median(samples), out))
            legacy = statistics.median([r[1] for r in rows if r[0]])
            opt = statistics.median([r[1] for r in rows if not r[0]])
            equal = all(r[2] == rows[0][2] for r in rows)
            print(f"{size:>7} {('mixed' if mixed else 'uniform'):>11} "
                  f"{legacy:>11.3f} {opt:>10.3f} {legacy / opt:>8.2f}x "
                  f"{str(equal):>6}")

    print()
    print("=== term_matches (Opt B) ===")
    rng = random.Random(11)
    terms = [f"Term{index:04d}" for index in range(1000)]
    text = " ".join(f"Term{rng.randrange(1000):04d}" for _ in range(60))
    for variant, fn in (("legacy", legacy_term_matches),
                        ("opt", term_mod.term_matches)):
        samples = []
        for _ in range(3):
            t0 = time.perf_counter()
            for _ in range(10):                     # 10 个"批次"遍历整份术语表
                for term in terms:
                    fn(term, text)
            samples.append((time.perf_counter() - t0) * 1000)
        print(f"  {variant:<7} {statistics.median(samples):>10.3f} ms "
              f"for 10 x 1000 matches")
    return 0


def _ratio(legacy: float, opt: float) -> str:
    if not opt:
        return "n/a"
    return f"{legacy / opt:.2f}x"


def main(argv=None) -> int:
    names = list(argv) if argv else list(CASES)
    if names == ["micro"]:
        return micro_function_level()
    unknown = [n for n in names if n not in CASES]
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}\n"
              f"available: {', '.join(CASES)}, micro", file=sys.stderr)
        return 2
    print(f"{'case':<14} {'variant':<8} {'wall_s':>9} {'cpu_s':>9} "
          f"{'setup_s':>9} {'regex':>9}", flush=True)
    for name in names:
        config = CASES[name]
        rows = []
        for enabled in (True, False, False, True):   # legacy / opt / opt / legacy
            row = measure(config, enabled)
            rows.append(row)
            print(f"{name:<14} {row['variant']:<8} {row['wall_s']:>9.3f} "
                  f"{row['cpu_s']:>9.3f} {row['setup_s']:>9.4f} "
                  f"{row['regex_compiles']:>9}", flush=True)
        for key in ("wall_s", "cpu_s", "setup_s", "regex_compiles"):
            legacy = statistics.median(
                [r[key] for r in rows if r["variant"] == "legacy"])
            opt = statistics.median(
                [r[key] for r in rows if r["variant"] == "opt"])
            print(f"{'':<14} {key:<8} {legacy:>9.3f} -> {opt:>9.3f}  "
                  f"= {_ratio(legacy, opt)} faster", flush=True)
        print(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
