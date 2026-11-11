"""The runtime baseline harness must declare what it did not measure.

Only the pure parts run here: S1's Qt window is exercised by
``tests/jank_monitor_test.py`` (sampler) and by running the script itself.  What
this file pins down is that an unimplemented scenario reports itself as such
instead of writing a report that looks measured, and that the fixture is the
deterministic EPUB the scenario promises.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import runtime_baseline_runner as runner  # noqa: E402


def test_unimplemented_scenario_writes_a_declared_report(tmp_path):
    code = runner.main(["--out", str(tmp_path), "--scenario", "S2"])
    assert code == 2
    payload = json.loads((tmp_path / "runtime-baseline.json").read_text(encoding="utf-8"))
    assert payload["scenario"] == "S2"
    assert payload["implemented"] is False
    assert "not implemented in this round" in payload["reason"]
    assert payload["limits"]


def test_s1_fixture_is_a_deterministic_epub(tmp_path):
    path = runner.build_epub(tmp_path / "fixtures" / "s1.epub", 17)
    with zipfile.ZipFile(path) as book:
        assert book.read("mimetype") == b"application/epub+zip"
        chapter = book.read("chapter.xhtml").decode("utf-8")
    assert chapter.count("<p>") == 17
    # Same input, same content: the baseline must not drift through randomness.
    again = runner.build_epub(tmp_path / "fixtures" / "s1-again.epub", 17)
    with zipfile.ZipFile(again) as book:
        assert book.read("chapter.xhtml") == chapter.encode("utf-8")


def test_runner_reads_the_product_run_record_not_a_parallel_one(tmp_path):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "job123" / ".performance" / "runs" / "run_20260101_000000_abc"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": "run_20260101_000000_abc",
        "wall_time_s": 12.5,
        "resources": {"process_rss_peak_mb": 321.0},
        "gui_jank": {"slow_count": 4},
    }), encoding="utf-8")
    (run_dir / "gui_jank.json").write_text(json.dumps({"slow_count": 4}), encoding="utf-8")
    (run_dir / "resources.jsonl").write_text('{"process_rss_mb": 300.0}\n', encoding="utf-8")
    rows = runner.read_run_records(data_dir, "job123")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_20260101_000000_abc"
    assert rows[0]["resources"]["process_rss_peak_mb"] == 321.0
    assert rows[0]["gui_jank"] == {"slow_count": 4}
    assert rows[0]["gui_jank_file"] is True
    assert rows[0]["resources_jsonl_lines"] == 1


def test_stall_window_is_explicit_about_not_being_measured():
    """Review F3: a missing window must produce `measured: false`, never `{}`."""
    empty = runner._stall_window_from_records([])
    assert empty["measured"] is False
    assert empty["reason"]

    unmeasured = runner._stall_window_from_records(
        [{"gui_jank": {"measured": False, "reason": "no source"}}])
    assert unmeasured["measured"] is False
    assert unmeasured["reason"]

    measured = {"measured": True, "slow_count": 2, "top_stacks": []}
    chosen = runner._stall_window_from_records(
        [{"gui_jank": {"measured": False}}, {"gui_jank": measured}])
    assert chosen == measured


def test_disabled_sampler_window_says_not_measured():
    """`enabled: false` is not the same statement as `measured: false`."""
    block = runner._normalize_window({"enabled": False, "slow_count": 0})
    assert block["measured"] is False
    assert "FOLITH_JANK_MONITOR" in block["reason"]
    measured = runner._normalize_window({"enabled": True, "slow_count": 0})
    assert measured["measured"] is True
