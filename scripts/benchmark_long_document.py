"""Long-document performance gate: fixed workload, compared against a baseline.

Why this exists: `tests/red_team_acceptance_test.py` used to fail the release
gate on an absolute wall-clock limit, which measured how idle the runner was.
Wall clock turned out to be unusable as a per-PR criterion for a second reason
too: the per-segment cost ratio of the very same revision measures 1.44 on
Windows x64 and 3.20 on Linux, because this flow rewrites the whole
`state.json` (about 3.2 MB at 2 000 segments) on every batch and a byte written
costs a different fraction of a batch on each platform.  So the pytest case now
asserts budgets on full saves, reconciliation work and snapshot reads, and leaves every
quantitative statement to this script, run on an idle machine, twice on the two
revisions being compared.

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

`half_scale` is the number to compare across revisions: it is dimensionless, so
a change between two revisions measured on the same host is a change in the
code rather than in the hardware.  It is **not** comparable across platforms —
the same revision measures 1.25 on Windows x64 and 1.01 on Linux, because the
fixed overhead of a short run differs (see
`docs/stability-milestone-4/performance-gate.md`).  Record a baseline per
platform.

It does not catch a uniform slowdown (everything slower by the same factor) —
that needs a recorded absolute baseline on fixed hardware.
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
from folith import review_gate  # noqa: E402

#: Documents to time, in increasing size.  ``calibration`` and ``target`` are
#: the pytest gate's pair; ``half`` exists only to give the marginal ratio.
DOCUMENTS = (("calibration", 40), ("half", 200), ("target", 400))


def _run_synthetic_document(jid, paragraphs, mode, glossary, kwargs):
    """Run the gate's workload with balanced sections at each document size."""
    half = paragraphs // 2
    # The gate's 400 paragraphs contain 200 finance then 200 river paragraphs.
    # Taking both halves keeps the smaller samples balanced as well.
    source = rt._synthetic_segments(400)
    document = rt._make_docx(source[:half] + source[200:200 + half])
    profile = rt.models.normalize_document_profile({
        "domain": "测试", "confidence": 0.8,
        "sections": [
            {"section_id": "finance", "start_segment": 0, "end_segment": half - 1,
             "topic": "金融", "domain": "金融", "style": "说明"},
            {"section_id": "river", "start_segment": half,
             "end_segment": paragraphs - 1,
             "topic": "地理", "domain": "地理", "style": "叙事"},
        ]})
    core.tm_path().unlink(missing_ok=True)
    core._job_repo.invalidate_cache()
    core.call_llm = rt._compliant_translation_llm(glossary)
    started = time.perf_counter()
    if mode == "quality":
        core.save_job_state(jid, core.new_job_state("long.docx"))
        core.freeze_glossary(jid, entries=glossary, frozen_by="rt")
        state = core.load_job_state(jid)
        state.update(document_profile=profile, profile_done=True)
        core.save_job_state(jid, state)
    state = core.run_job_pipeline(jid, "long.docx", document, mode=mode,
                                  batch_size=core.BATCH_SIZE, **kwargs)
    return state, time.perf_counter() - started


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
            mode_id = "qk" if mode == "quick" else "ql"
            jid = f"{mode_id}{tag[:3]}{repeat}{index}"[:16].ljust(16, "0")
            state, seconds = _run_synthetic_document(
                jid, paragraphs, mode, glossary, kwargs)
            assert len(state["pairs"]) == paragraphs * 5, (mode, tag)
            assert all(p.get("reviewed") for p in state["pairs"]), (mode, tag)
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
    old_review_mode = review_gate.DEFAULT_REVIEW_MODE
    # Match the pytest gate's established always-review fixture.
    review_gate.DEFAULT_REVIEW_MODE = review_gate.REVIEW_MODE_ALWAYS
    core.OUTPUT_DIR = tmp
    glossary = _glossary()
    kwargs = dict(provider="DeepSeek", api_key="k", model="deepseek-chat",
                  target_lang="简体中文", auto_term=False, enable_report=False,
                  translation_theory="目的论 (Skopos Theory)",
                  user_glossary=glossary)
    report = {"label": args.label, "repeat": max(args.repeat, 1),
              "review_mode": "always",
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
        review_gate.DEFAULT_REVIEW_MODE = old_review_mode
        shutil.rmtree(tmp, ignore_errors=True)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
