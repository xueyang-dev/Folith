"""Guards the Qt spike's core assumptions (S1).

The spike's whole premise is that the business core is reusable without
Streamlit and that the existing data format can be read as-is.  If either stops
being true, the spike's plan (and its migration estimate) is wrong, so pin both.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "qt_spike" / "main.py"


def test_core_does_not_import_streamlit():
    """The Qt app must be able to use the business core without Streamlit."""
    text = (ROOT / "core.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("import streamlit", "from streamlit")), \
            f"core.py must stay Streamlit-free: {stripped}"


def test_spike_smoke_reads_the_existing_data_format(tmp_path):
    """A smoke run over an empty data dir must succeed and report zero jobs."""
    if not SPIKE.is_file():
        import pytest

        pytest.skip("qt_spike/main.py not present")
    result = tmp_path / "smoke.json"
    run = subprocess.run(
        [sys.executable, str(SPIKE), "--data-dir", str(tmp_path / "data"),
         "--smoke", "--result", str(result)],
        capture_output=True, text=True, timeout=300,
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["jobs"] == 0
    assert data["platform"] == "offscreen"


def test_spike_reads_real_jobs_from_a_data_dir(tmp_path):
    """Jobs written in the existing format must be listed by the Qt window."""
    data_dir = tmp_path / "data"
    job = data_dir / "job-abc"
    job.mkdir(parents=True)
    (job / "state.json").write_text(
        json.dumps({"pairs": [], "paras": [], "filename": "sample.pdf"}),
        encoding="utf-8")
    result = tmp_path / "smoke.json"
    run = subprocess.run(
        [sys.executable, str(SPIKE), "--data-dir", str(data_dir),
         "--smoke", "--result", str(result)],
        capture_output=True, text=True, timeout=300,
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["jobs"] >= 1, data
