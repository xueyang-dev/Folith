"""A failing file must leave its complete output where CI can download it."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_regression


def test_failure_excerpt_keeps_the_assertion_instead_of_only_the_tail():
    """Printing the last 80 lines used to hide the assertion behind a state dump."""
    filler = [f"filler {index}" for index in range(400)]
    output = "\n".join(
        ["collected 33 items",
         "=================================== FAILURES ===================================",
         'E       assert 1 == 2',
         "E        +  where 1 = len(state['pairs'])"]
        + filler)
    tail = "\n".join(output.splitlines()[-80:])
    assert "assert 1 == 2" not in tail, "this test needs a tail that hides the assertion"

    excerpt = run_regression.failure_excerpt(output)
    assert "assert 1 == 2" in excerpt
    assert "另有" in excerpt
    assert len(excerpt.splitlines()) <= run_regression.FAILURE_EXCERPT_LINES + 1


def test_failure_excerpt_returns_short_output_unchanged():
    output = "1 failed, 2 passed in 3.4s"
    assert run_regression.failure_excerpt(output) == output


def test_a_failing_file_leaves_its_complete_output_in_the_artifact_dir(
        tmp_path, monkeypatch, capsys):
    """The artifact is `.regression-logs/`: it must hold the whole output."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "log_capture_test.py").write_text(
        textwrap.dedent('''
            def test_fails_after_a_long_tail():
                for index in range(400):
                    print(f"filler {index}")
                assert 1 == 2, "the assertion that must survive in the logs"
        '''), encoding="utf-8")
    monkeypatch.setattr(run_regression, "ROOT", tmp_path)
    monkeypatch.setattr(run_regression, "TESTS", tests_dir)
    monkeypatch.setattr(run_regression, "LOGS", tmp_path / ".regression-logs")
    monkeypatch.setattr(sys, "argv", ["run_regression.py", "-k", "log_capture"])
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert run_regression.main() == 1

    log = (tmp_path / ".regression-logs" / "log_capture_test.log").read_text(encoding="utf-8")
    assert "the assertion that must survive in the logs" in log
    assert len(log.splitlines()) > 100, "the artifact must keep the whole run, not 80 lines"

    printed = capsys.readouterr().out
    assert "失败片段" in printed
    assert "the assertion that must survive in the logs" in printed


def test_a_repeated_split_unit_gets_its_own_log_name():
    """`foo_test.py[2/3]` and `foo_test.py[3/3]` must not overwrite each other."""
    assert run_regression._log_stem("foo_test.py[2/3]") == "foo_test-2of3"
    assert run_regression._log_stem("foo_test.py") == "foo_test"
