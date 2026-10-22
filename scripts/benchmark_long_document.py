"""Long-document performance gate: fixed workload, compared against a baseline.

Why this exists: `tests/red_team_acceptance_test.py` used to fail the release
gate on an absolute wall-clock limit, which measured how idle the runner was.
It now only screens coarsely.  The quantitative gate is this script, run on an
idle machine, twice on the two revisions being compared.

It deliberately reuses the test module's synthetic document so the workload is
byte-identical to the one the pytest gate exercises; a different document here
would not be comparable.

Usage:

    python scripts/benchmark_long_document.py --repeat 3 --label main
    python scripts/benchmark_long_document.py --repeat 3 --label fix --output perf-fix.json

The JSON it prints contains, per mode, the elapsed seconds of each repetition
(median reported), the per-segment marginal ratio

    half_scale = t(2 000) / (2 × t(1 000))

and the ten-times ratio

    ten_times = t(2 000) / (10 × t(200))

`half_scale` is the number to compare across revisions: it is dimensionless and
approximately machine-independent, so a change in it between two revisions on
the same host is a change in the code.  It does not catch a uniform slowdown
(everything slower by the same factor) — that needs different hardware or an
absolute baseline, both of which are out of scope for a shared CI runner.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
import red_team_acceptance_test as rt  # noqa: E402

#: Documents to time, in increasing size.  ``calibration`` and ``target`` are
#: the pytest gate's pair; ``half`` exists only to give the marginal ratio.
DOCUMENTS = (("calibration", 40), ("half", 200), ("target", 400))


def _glossary():
    entry = rt._entry
    return [
        entry("bank", "银行", status="locked", scope="section:finance"),
        entry("bank", "河岸", status="locked", scope="section:river"),
        entry("Skopos theory", "目的论", status="locked", scope="global"),
        entry("MT", "机器翻译", status="locked", scope="global"),
        entry("provisional term", "建议术语", status="provisional",
              scope="global"),
    ]


def _once(output_dir, mode, repeat, glossary, kwargs):
    timings = {}
    for tag, paragraphs in DOCUMENTS:
        samples = []
        for index in range(repeat):
            jid = f"{mode[0]}{tag[:3]}{repeat}{index}"[:16].ljust(16, "0")
            state, seconds = rt._run_synthetic_document(
                jid, paragraphs, mode, glossary, kwargs)
            assert state["pairs"], f"{mode}/{tag} produced no segments"
            samples.append(seconds)
        timings[tag] = {"samples_s": [round(s, 3) for s in samples],
                        "median_s": round(statistics.median(samples), 3),
                        "segments": len(state["pairs"])}
    calibration = timings["calibration"]["median_s"]
    half = timings["half"]["median_s"]
    target = timings["target"]["median_s"]
    return {
        "mode": mode,
        "documents": timings,
        "target_over_calibration_x10": round(target / (10 * calibration), 3),
        "half_scale": round(target / (2 * half), 3),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=3,
                        help="repetitions per document; the median is reported")
    parser.add_argument("--label", default="",
                        help="free-form tag recorded in the output, e.g. a revision")
    parser.add_argument("--output", type=Path, default=None,
                        help="write the JSON here instead of stdout only")
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="longdoc-bench-"))
    old_dir, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp
    glossary = _glossary()
    kwargs = dict(provider="DeepSeek", api_key="k", model="deepseek-chat",
                  target_lang="简体中文", auto_term=False, enable_report=False,
                  translation_theory="目的论 (Skopos Theory)",
                  user_glossary=glossary)
    report = {"label": args.label, "repeat": args.repeat,
              "python": sys.version.split()[0],
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "modes": []}
    try:
        for mode in ("quick", "quality"):
            row = _once(tmp, mode, max(args.repeat, 1), glossary, kwargs)
            report["modes"].append(row)
            print(f"{mode:8s} target={row['documents']['target']['median_s']:7.2f}s "
                  f"half_scale={row['half_scale']:.3f} "
                  f"x10={row['target_over_calibration_x10']:.3f}", flush=True)
    finally:
        core.OUTPUT_DIR = old_dir
        core.call_llm = old_llm
        shutil.rmtree(tmp, ignore_errors=True)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
