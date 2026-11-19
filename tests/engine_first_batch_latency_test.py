"""G-E 第 5 项：冻结版「开始 → 首批译文」的延迟（#325 门槛 GE 第 6 条）。

门的原话是「Start 后 1 秒内界面出现启动状态；到首批译文的额外等待在冻结版里
**中位 ≤ 3 s**」。前半条由 `folith/desktop/engine_supervisor.publish_starting_status`
（#534）覆盖；这个文件测后半条。

**口径**（也写在 `.loop/reports/g-e-evidence.md` §5.1）：

- 起点从「发出启动请求」开始，终点是**第一批译文落盘并且可被界面看到**：`runtime_state`
  里出现第一条 `state_commit_done`（「已保存第 1 批译文」），或 `state.json` 的
  `pairs` 非空——不是"引擎起来了"。**不能看 journal**：干净跑完的批次会在快照包含它们
  之后被 compaction 退休，日志文件随即消失；
- 含子进程 ``import core`` 的冷启动（E-1 实测冻结版约 1.2 s）与第一次模型往返；
- 确定性网关（仓库自带的 ``scripts/d0_fake_llm_server.py``），不看真实模型速度；
- 冻结版：``FOLITH_FROZEN_APP`` 指向 ``Folith`` 可执行文件（.app 里的
  ``Contents/MacOS/Folith``）；不设这个变量时测试**跳过**，所以 CI 不需要冻结包也能过。

n=5 取中位，判据 ≤ 3 s。只测子进程模式（``--engine-host`` 本身就是子进程入口）；
进程内模式的对照数在 0.8.2 的 E-7 里。

运行（本机，冻结包）：

    FOLITH_FROZEN_APP="$PWD/Folith.app/Contents/MacOS/Folith" \\
    QT_QPA_PLATFORM=offscreen \\
    venv/bin/python -m pytest tests/engine_first_batch_latency_test.py -q -s
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

from folith.desktop import engine_protocol as protocol  # noqa: E402
from folith.desktop.engine_protocol import ConnectionSpec  # noqa: E402

PROVIDER = "自定义中转站"
MODEL = "d0-fake-model"
#: 门限（#325 gate GE 第 6 条）：冻结版首批译文的中位延迟。
FIRST_BATCH_BUDGET_SECONDS = 3.0
ROUNDS = 5

SOURCE_TEXT = "".join(f"Sentence {index} about cold starts.\n" for index in range(6))


def _frozen_app() -> Path:
    value = os.environ.get("FOLITH_FROZEN_APP", "").strip()
    if not value:
        pytest.skip("FOLITH_FROZEN_APP 未设置：这条只对冻结版跑")
    path = Path(value)
    assert path.is_file(), f"FOLITH_FROZEN_APP 不是可执行文件：{path}"
    return path


@pytest.fixture()
def gateway(tmp_path):
    """The repository's deterministic OpenAI-compatible endpoint, in-process."""
    from scripts import d0_fake_llm_server

    server = d0_fake_llm_server.serve(0, str(tmp_path / "gateway.jsonl"), 0.0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _create_job(job_id: str) -> None:
    state = core.new_job_state("g-e-first-batch.txt")
    state.update({
        "target_lang": "简体中文", "auto_term": False,
        "enable_review": False, "enable_annotate": False,
        "report_enabled": False, "use_tm": False,
        "enable_understanding": False, "enable_source_cleanup": False,
        "style_rules": "", "segmentation_mode": "sentence",
        "knowledge_feedback": False,
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, SOURCE_TEXT.encode("utf-8"))


def _first_batch_seconds(job_dir: Path, started_at: float,
                         timeout: float) -> float | None:
    """Seconds from ``started_at`` until the first batch is committed on disk.

    Two observables, whichever lands first: the runtime state's
    ``state_commit_done`` event (what the workbench reacts to) or a non-empty
    ``pairs`` list in the snapshot (the authoritative result).
    """
    runtime_path = job_dir / "runtime_state.json"
    state_path = job_dir / "state.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            runtime = {}
        for event in runtime.get("events") or []:
            if isinstance(event, dict) and event.get("event") == "state_commit_done":
                return time.monotonic() - started_at
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("pairs"):
            return time.monotonic() - started_at
        time.sleep(0.01)
    return None


def _run_one(app: Path, data_dir: Path, log_dir: Path, gateway_url: str,
             job_id: str, timeout: float = 180.0) -> float:
    """Start one engine child and return its time-to-first-batch."""
    _create_job(job_id)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("FOLITH", "FOLIOTHREAD"))}
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FOLITH_ENGINE_EXIT_TIMEOUT"] = "15"
    proc = subprocess.Popen(
        [str(app), "--engine-host", "--data-dir", str(data_dir),
         "--log-dir", str(log_dir)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=env)
    job_dir = data_dir / job_id
    started_at = time.monotonic()
    try:
        line = protocol.encode_start_request(
            job_id,
            ConnectionSpec(provider=PROVIDER, model=MODEL,
                           base_url=gateway_url, api_key="sk-ge-firstbatch"),
            mode="start")
        proc.stdin.write(line)
        proc.stdin.flush()
        elapsed = _first_batch_seconds(job_dir, started_at, timeout)
        assert elapsed is not None, (
            f"no first batch within {timeout}s; "
            f"runtime={json.dumps(core.load_runtime_state(job_id) or {}, ensure_ascii=False)[:500]}")
        return elapsed
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_frozen_engine_first_batch_median_is_within_budget(gateway, tmp_path,
                                                           monkeypatch):
    app = _frozen_app()
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()
    # The task fixtures are written by *this* process, so point the core at the
    # same tree the child is given; never touch the repository's outputs/.
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir)
    samples = []
    for index in range(ROUNDS):
        elapsed = _run_one(app, data_dir, log_dir, gateway,
                           f"gefirstbatch{index:02d}")
        samples.append(round(elapsed, 3))
        print(f"first batch #{index + 1}: {elapsed:.3f}s", flush=True)
    median = statistics.median(samples)
    print(f"first batch median: {median:.3f}s  samples={samples}", flush=True)
    assert median <= FIRST_BATCH_BUDGET_SECONDS, (
        f"冻结版首批译文中位 {median:.3f}s，超过 {FIRST_BATCH_BUDGET_SECONDS:.1f}s 的门限；"
        f"samples={samples}")

