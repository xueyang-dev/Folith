"""Reproduce #99 local scan cost and offline pipeline overhead; no network.

python scripts/retrospective_benchmark.py --baseline a58ad4f
The baseline is used only for the local observation comparison. End-to-end
measurements compare the revised production path with suggestions on/off.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with redirect_stdout(sys.stderr):
    import core
from folith import retrospective as revised
from folith.storage import job_repository


def fixture():
    glossary = [{"id": f"term-{i}", "source": f"Term{i:02}",
                 "preferred": f"首选{i:02}", "forbidden": [f"旧译{i:02}"],
                 "status": "locked", "behavior": "translate"} for i in range(20)]
    pairs = [{"source": f"The archive contains Term{i % 20:02} among its records.",
              "target": f"档案馆保存了{'旧译' if i < 200 or i >= 1800 else '首选'}{i % 20:02}的档案。"}
             for i in range(2000)]
    findings = [{"type": "glossary", "conflict": True,
                 "entry_id": f"term-{i % 20}", "segment_id": i,
                 "target_span": f"旧译{i % 20:02}"} for i in range(1800, 2000)]
    return glossary, pairs, findings


def local_cost(module, repeats):
    glossary, pairs, findings = fixture()
    cold, warm, clean = [], [], []
    for _ in range(repeats):
        state = {"pairs": pairs}
        started = time.perf_counter()
        module.observe_batch(state, findings=findings, pairs=pairs, glossary=glossary, batch=10)
        cold.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        module.observe_batch(state, findings=findings, pairs=pairs, glossary=glossary, batch=10)
        warm.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        module.observe_batch(state, findings=[], pairs=pairs, glossary=glossary, batch=11)
        clean.append((time.perf_counter() - started) * 1000)
    return {"first_observe_median_ms": statistics.median(cold),
            "replay_median_ms": statistics.median(warm),
            "clean_batch_median_ms": statistics.median(clean),
            "evidence": len(state[module.EVIDENCE_KEY]),
            "suggestions": len(state[module.SUGGESTIONS_KEY]),
            "earlier_suggestions": sum(item["segment_index"] < 1800
                                       for item in state[module.SUGGESTIONS_KEY])}


def pipeline_cost(repeats):
    sources = [f"Violet visits the northern archive on a quiet morning {i}." for i in range(8)]
    targets = [f"{'紫罗兰' if i % 2 == 0 else '维奥莱特'}在安静的早晨{i}来到北方档案馆。"
               for i in range(8)]
    by_source = dict(zip(sources, targets))
    glossary = [{"id": "violet", "source": "Violet", "preferred": "维奥莱特",
                 "target": "维奥莱特", "forbidden": ["紫罗兰"],
                 "status": "locked", "behavior": "translate"}]
    results = {"enabled": [], "disabled": []}
    calls_by_arm = {}
    with tempfile.TemporaryDirectory(prefix="folith-issue99-") as directory:
        with patch.object(core, "OUTPUT_DIR", Path(directory)):
            for repeat in range(repeats):
                # Alternate order to reduce warmup/order bias.
                for arm in (("disabled", "enabled") if repeat % 2 else ("enabled", "disabled")):
                    calls = []

                    def llm(provider, key, model, system, user, **kwargs):
                        calls.append(user)
                        if "【待翻译段落（按序号返回等长数组）】" in user:
                            content = user.split("【待翻译段落（按序号返回等长数组）】")[-1]
                        elif "以下译文未通过检查" in user:
                            content = user.split("问题清单：")[0]
                        else:
                            raise AssertionError("Unexpected model operation")
                        values = [target for line in content.splitlines()
                                  for source, target in by_source.items() if line.endswith(source)]
                        assert values
                        return json.dumps(values, ensure_ascii=False)

                    job_id = f"{arm}-{repeat}"
                    state = core.new_job_state("offline.docx")
                    state["paras"] = sources
                    core.save_job_state(job_id, state)
                    with patch.dict("os.environ", {revised.ENV_SWITCH: "1" if arm == "enabled" else "0"}), \
                            patch.object(core, "call_llm", llm):
                        started = time.perf_counter()
                        core.translate_stage(state, job_id, glossary=glossary, provider="DeepSeek",
                                             api_key="offline", model="offline", target_lang="简体中文",
                                             style_rules="", enable_review=False, use_tm=False,
                                             batch_size=2, translation_concurrency=1,
                                             knowledge_feedback=False, use_translation_cache=False,
                                             use_knowledge_cache=False)
                        elapsed = (time.perf_counter() - started) * 1000
                    reopened = core.load_job_state(job_id)
                    assert [pair["target"] for pair in reopened["pairs"]] == targets
                    assert len(reopened.get(revised.SUGGESTIONS_KEY) or []) == (4 if arm == "enabled" else 0)
                    results[arm].append(elapsed)
                    if arm in calls_by_arm:
                        assert calls_by_arm[arm] == calls
                    calls_by_arm[arm] = calls
                    job_repository.invalidate_cache()
            assert calls_by_arm["enabled"] == calls_by_arm["disabled"]
    return {"segments": 8, "batch_size": 2, "network": False,
            "provider": "scripted; unchanged targets even on repair",
            "knowledge_feedback": False, "review": False, "cache": False,
            "model_calls_per_arm": len(calls_by_arm["enabled"]), "additional_model_calls": 0,
            "wall_ms": results,
            "median_ms": {arm: statistics.median(values) for arm, values in results.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="origin/main")
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    baseline = types.ModuleType("baseline_retrospective")
    source = subprocess.check_output(["git", "show", f"{args.baseline}:folith/retrospective.py"], text=True)
    exec(compile(source, "baseline_retrospective.py", "exec"), baseline.__dict__)
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "baseline": subprocess.check_output(["git", "rev-parse", args.baseline], text=True).strip(),
              "revised_base": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "repeats": args.repeats,
              "local_fixture": {"segments": 2000, "terms": 20, "later_conflicts": 200},
              "baseline_local": local_cost(baseline, args.repeats),
              "revised_local": local_cost(revised, args.repeats),
              "revised_pipeline": pipeline_cost(args.repeats)}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
