"""Change classification for the tiered CI (scripts/ci_changes.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ci_changes", Path(__file__).resolve().parent.parent / "scripts" / "ci_changes.py")
ci_changes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_changes)


def test_documentation_only_runs_the_tests_that_read_docs():
    result = ci_changes.classify(["docs/guide.md", "README.md", "promo/x.html"])
    assert result["code"] == "false" and result["risk"] == "false"
    assert "readme_sync_test.py" in result["docs_tests"].split(",")


def test_shared_code_runs_the_full_pr_tier_without_risk():
    result = ci_changes.classify(["folith/qt/views.py"])
    assert (result["code"], result["risk"], result["packaging"]) == ("true", "false", "false")


def test_persistence_recovery_and_concurrency_paths_need_the_risk_regression():
    for path in ("folith/state_migration.py", "folith/store_lock.py",
                 "folith/review_scheduler.py", "tests/workflow_recovery_test.py"):
        assert ci_changes.classify([path])["risk"] == "true", path


def test_ci_changes_exercise_everything_and_unknown_paths_count_as_code():
    assert ci_changes.classify([".github/workflows/pr-checks.yml"])["risk"] == "true"
    assert ci_changes.classify(["some/new/thing.txt"])["code"] == "true"
    assert ci_changes.classify([])["code"] == "true"


def test_packaging_inputs_request_release_validation():
    for path in ("packaging/folith-windows.nsi", "requirements.txt",
                 "scripts/build_qt_dmg.sh", "scripts/qt_release_checks.py"):
        assert ci_changes.classify([path])["packaging"] == "true", path


def test_async_knowledge_commits_are_checked_on_windows():
    filename = "knowledge_async_commit_test.py"
    result = ci_changes.classify([f"tests/{filename}"])
    assert result["risk"] == "true"
    assert any(needle in filename for needle in result["risk_tests"].split(","))
