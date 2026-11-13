"""E-6p: the lifecycle probe passes the same main path in both execution models.

``scripts/qt_lifecycle_probe.py --scenario lifecycle-main`` drives
start → pause → continue → cancel → exit on the real window.  This test runs
that scenario **twice, as a real process**, in the two models the product has:

* ``FOLITH_ENGINE_PROCESS=0`` — the pipeline runs in a worker thread of the GUI
  process;
* ``FOLITH_ENGINE_PROCESS=1`` — the GUI supervises one engine child per task
  (``folith.desktop.engine_supervisor``), so ``core.is_job_worker_alive`` in the
  GUI process is *false while the task runs*.

The point of the node: the probe's criteria must be true in the second model
too, and its waits must end when the child exits.  The assertions therefore
look at the report's own evidence — liveness at start, the child handle, the
pause/resume/cancel statuses, the exit wait — not at wall-clock timings.

The gateway is the repository's deterministic one
(``scripts/d0_fake_llm_server.py``), started by the probe itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "qt_lifecycle_probe.py"


def _run_probe(tmp_path: Path, *, engine_process: str, name: str) -> dict:
    data_dir = tmp_path / name / "data"
    report_path = tmp_path / name / "report.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "FOLITH_QT_SETTINGS_DIR": str(tmp_path / name / "settings"),
        "FOLITH_QT_NONINTERACTIVE": "1",
        "FOLITH_ENGINE_PROCESS": engine_process,
    })
    # The probe pins its own data/log dirs from --data-dir.
    env.pop("FOLITH_OUTPUT_DIR", None)
    result = subprocess.run(
        [sys.executable, str(PROBE), "--scenario", "lifecycle-main",
         "--data-dir", str(data_dir), "--out", str(report_path),
         "--stub-delay", "0.2", "--timeout", "180"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert report_path.is_file(), (
        f"no report; rc={result.returncode}\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    return json.loads(report_path.read_text(encoding="utf-8")), result


@pytest.mark.parametrize("engine_process,expected", [("0", False), ("1", True)])
def test_lifecycle_main_path_in_both_execution_models(tmp_path, engine_process, expected):
    pytest.importorskip("PySide6")
    report, result = _run_probe(tmp_path, engine_process=engine_process,
                                name=f"mode-{engine_process}")
    assert result.returncode == 0, (
        f"probe failed (engine_process={engine_process}); report={json.dumps(report, ensure_ascii=False)[:3000]}")

    # The report says which model it is evidence for.
    assert report["engine_process"] is expected
    assert report["ok"] is True, json.dumps(report, ensure_ascii=False)[:3000]

    # 1) start: "running" means an owner exists — a local thread or a child.
    assert report["start"]["alive"] is True
    assert report["start"]["status"] not in ("", "idle", "idle_incomplete")
    # 2) pause: the product's pause keeps the run (and its lock) alive.
    assert report["pause"] == {"paused": True, "status": "paused", "alive": True}
    # 3) continue: back to running, still owned.
    assert report["resume"]["resumed"] is True
    assert report["resume"]["status"] not in ("paused", "pausing")
    assert report["resume"]["alive"] is True
    # 4) cancel: the run really ended (a notification alone is not "cancelled").
    assert report["cancel"]["cancelled"] is True
    assert report["cancel"]["alive"] is False
    # 5) exit: the window closed and no worker lock is left behind.
    assert report["exit"]["closed"] is True
    assert not report["exit"]["lock"]
    assert report["failures"] == []

    # The subprocess model must be proven by a child, not assumed from the flag.
    if expected:
        child = report["start"]["engine_child"]
        assert child and child["running"] is True and child["pid"] > 0, child
        assert report["engine_child_observed"] is True
        # The decisive difference: the old thread probe is false while the task
        # really is running in a child, and the adopted entry is true.
        assert report["start"]["worker_thread"] is False
        assert report["start"]["alive"] is True
        # ... and the wait ended with the child gone, while the GUI process
        # never had a worker thread for it.
        assert report["cancel"]["engine_child"] is None
    else:
        assert report["start"]["engine_child"] is None
        assert report["engine_child_observed"] is False
        assert report["start"]["worker_thread"] is True

    # Every wait the probe performed ended on its own (no silent timeout).
    waits = report["waits"]
    assert waits, "the probe must record its waits"
    assert all(not item["timed_out"] for item in waits), waits
    labels = {item["label"] for item in waits}
    assert {"lifecycle-main-start", "lifecycle-main-cancel",
            "lifecycle-main-exit"} <= labels
    # "execution is over" must have been observed, i.e. the exit wait did not
    # merely skip: it reports when it settled.
    assert all(isinstance(item["waited_s"], (int, float)) for item in waits)
