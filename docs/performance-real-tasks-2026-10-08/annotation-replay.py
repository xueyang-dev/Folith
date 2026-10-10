"""Offline scheduling benchmark; no provider request or real task mutation."""
import copy
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
with redirect_stdout(sys.stderr):
    import core


def replay(workers, output):
    state = core.new_job_state("annotation-replay.docx")
    state["pipeline_config"] = {"translation_concurrency": workers}
    state["pairs"] = [
        {"source": f"Section {i} discusses Urban Air Mobility and its operational requirements.",
         "target": f"第{i}节讨论城市空中交通及其运行要求。"}
        for i in range(65)
    ]
    calls = []

    def response(pairs, *_args):
        calls.append(pairs[0]["source"])
        time.sleep(0.02)
        return core.AnnotationBatchResult([
            {"seg": 0, "src": "Urban Air Mobility", "tgt": "城市空中交通",
             "type": "domain", "note": "专业术语"}])

    with patch.object(core, "OUTPUT_DIR", output), patch.object(core, "ANNOT_BATCH_SIZE", 1), \
            patch.object(core, "annotate_batch", response):
        started = time.perf_counter()
        core.annotate_stage(state, f"replay-{workers}", [], "DeepSeek", "unused", "fixed-response", "简体中文")
        elapsed = time.perf_counter() - started
    result = {key: copy.deepcopy(state[key]) for key in
              ["annotations", "annotation_batches", "annotations_done", "annotations_done_offset"]}
    return {"workers": workers, "calls": len(calls), "wall_time_s": elapsed}, result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="folith-annotation-replay-") as root:
        serial, a = replay(1, Path(root))
        parallel, b = replay(2, Path(root))
    assert a == b, "business results must match"
    print(json.dumps({"kind": "synthetic_fixed_response", "batches": 65,
                      "simulated_request_latency_s": 0.02,
                      "serial": serial, "parallel": parallel, "results_equal": True,
                      "wall_time_reduction_percent": 100 * (1 - parallel["wall_time_s"] / serial["wall_time_s"])},
                     ensure_ascii=False, indent=2))
