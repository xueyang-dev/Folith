"""Desktop verification harness contract.

These are not product tests: they pin down harness bugs that produced misleading
failures during D1/D2 verification, so they cannot come back silently.

* an unread pipe on the controlled model endpoint fills up and blocks it mid-run
  (surfacing as "Connection error" in the app under test);
* starting a second endpoint on another port invalidates the URL stored in the
  app's provider config;
* a probe that never routes the window to the job cannot see the runtime controls.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SLICE = ROOT / "scripts" / "d0_slice_runner.py"
PAUSED_RUNNER = ROOT / "scripts" / "d1_paused_exit_runner.py"
PAUSED_PROBE = ROOT / "scripts" / "desktop_d1_paused_probe.py"
RESUME_JS = ROOT / "scripts" / "desktop_d1_resume.js"


def test_stub_endpoint_output_goes_to_a_file_not_a_pipe():
    source = SLICE.read_text(encoding="utf-8")
    start = source.index("def start_stub_llm")
    body = source[start:start + 1600]
    assert "stdout=subprocess.PIPE" not in body, \
        "an unread pipe blocks the stub once the request log fills it"
    assert "server_log" in body and '.open("ab")' in body
    assert "stderr=subprocess.STDOUT" in body


def test_paused_runner_keeps_one_endpoint_for_the_whole_run():
    source = PAUSED_RUNNER.read_text(encoding="utf-8")
    assert "stub2" not in source, \
        "the app stores the endpoint URL, so a second server breaks the resume phase"
    assert source.count("start_stub_llm(") == 1


def test_paused_probe_routes_the_window_before_using_runtime_controls():
    source = PAUSED_PROBE.read_text(encoding="utf-8")
    assert "navigate_to_job" in source
    navigate_at = source.index("_navigate(window, job)")
    pause_at = source.index("runtime_pause_")
    assert navigate_at < pause_at, "the job page must be open before the pause click"
    # A job that was interrupted by the setup phase must be resumed first.
    assert "runtime_resume" in source and "runtime_retry" in source


def test_resume_probe_covers_every_stopped_state_control():
    source = RESUME_JS.read_text(encoding="utf-8")
    for key in ("runtime_resume_", "runtime_continue_", "runtime_retry_"):
        assert key in source, f"{key} must be handled (interrupted / paused / stalled)"


def test_stub_ready_file_can_be_namespaced():
    source = SLICE.read_text(encoding="utf-8")
    assert 'name: str = "stub"' in source, "endpoints must not collide on one ready file"
    assert '{name}-ready.json' in source


SWEEP = ROOT / "scripts" / "desktop_e2e_sweep.py"


def test_multi_segment_editing_verification_is_wired():
    """Roadmap §6.1: multi-sentence switching/commit must stay verified."""
    runner = ROOT / "scripts" / "d0_multiseg_runner.py"
    probe = ROOT / "scripts" / "desktop_d0_multiseg.js"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "human_edited" in body, "the on-disk state must be checked, not just the DOM"
    assert "--verify-only" in body, "the edits must be re-read after a restart"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d0_multiseg_runner.py" in sweep


def test_first_launch_and_preference_checks_are_wired():
    """Roadmap §3.2: the app must create its own dirs; preferences must persist."""
    runner = ROOT / "scripts" / "d2_firstlaunch_prefs_runner.py"
    probe = ROOT / "scripts" / "desktop_ui_language.js"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "pre_existing" in body and "dirs_created" in body
    assert "ui_preferences.json" in body
    assert "--keep" in body, "the switch must be left in place to prove persistence"
    assert "--read-only" in body, "the restart must be read, not re-switched"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d2_firstlaunch_prefs_runner.py" in sweep


def test_dry_run_noop_verification_is_wired():
    """§3.2: the dry run must not write to either tree."""
    runner = ROOT / "scripts" / "d1_dryrun_noop_runner.py"
    assert runner.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "legacy_diff" in body and "data_diff" in body, "both trees must be fingerprinted"
    assert "user_key_untouched" in body
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_dryrun_noop_runner.py" in sweep


def test_unsafe_import_verification_is_wired():
    """§3.2: unsafe sources must be refused with nothing written."""
    runner = ROOT / "scripts" / "d1_unsafe_import_runner.py"
    assert runner.is_file()
    body = runner.read_text(encoding="utf-8")
    for case in ("nonexistent", "regular-file", "same-as-target", "target-parent",
                 "target-child", "filesystem-root"):
        assert case in body, f"{case} missing from the unsafe-source cases"
    assert "target_unchanged" in body
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_unsafe_import_runner.py" in sweep


def test_exit_timeout_branch_verification_is_wired():
    """§3.3: exceeding the bounded wait must not be reported as a safe exit."""
    runner = ROOT / "scripts" / "d1_exit_timeout_runner.py"
    probe = ROOT / "scripts" / "desktop_d1_timeout_probe.py"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "report_unsafe" in body and "report_safe" in body
    assert 'action") == "incomplete"' in body
    assert "force-prompt" in body
    probe_body = probe.read_text(encoding="utf-8")
    assert "job_lock.acquire" in probe_body, "the probe must hold the real OS lock"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_exit_timeout_runner.py" in sweep


def test_two_task_exit_verification_is_wired():
    """§3.3: every active task must be stopped, not just the one in the window."""
    runner = ROOT / "scripts" / "d1_two_tasks_runner.py"
    probe = ROOT / "scripts" / "desktop_d1_two_tasks_probe.py"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "ACTIVE_STATUSES" in body, "use the product's own notion of active"
    assert "other_cancels" in body
    assert "several_active_before_exit" in body and "active_before_exit" in body
    assert "expected_others" in body, "the check must key on what was active at exit"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_two_tasks_runner.py" in sweep


def test_sweep_exercises_the_concurrency_shapes():
    """§3.3: several tasks, and a paused task next to a running one."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts"))
    import desktop_e2e_sweep as sweep

    by_name = {case["name"]: case for case in sweep.CASES}
    for name in ("two-tasks-exit", "three-tasks-exit", "paused-plus-running-exit",
                 "cancelling-at-exit"):
        assert name in by_name, f"{name} missing from the sweep"
    assert by_name["three-tasks-exit"]["extra"] == ["--scenario", "three-running"]
    assert by_name["paused-plus-running-exit"]["extra"] == ["--scenario",
                                                            "paused-plus-running"]
    assert by_name["cancelling-at-exit"]["extra"] == ["--scenario", "cancelling-at-exit"]


def test_many_jobs_check_is_wired():
    """A long-lived install (many tasks) must stay usable and honestly reported."""
    runner = ROOT / "scripts" / "d1_many_jobs_runner.py"
    probe = ROOT / "scripts" / "desktop_d1_many_jobs.js"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "rendered_items" in body and "job_count" in body
    assert "copy_seconds" in body, "record the setup cost as well as the UI timings"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_many_jobs_runner.py" in sweep


def test_history_delete_verification_is_wired():
    """§3.2: the only deletion entry must require typed confirmation."""
    runner = ROOT / "scripts" / "d1_history_delete_runner.py"
    probe = ROOT / "scripts" / "desktop_d1_history_delete.js"
    assert runner.is_file() and probe.is_file()
    body = probe.read_text(encoding="utf-8")
    for step in ("wrong_name", "closed", "correct_name", "after_confirm"):
        assert step in body, f"{step} missing from the delete probe"
    assert "history_delete_cancel" in body, "the dismiss control must be exercised"
    assert "neighbour_snapshot_files" in runner.read_text(encoding="utf-8")
    running_probe = ROOT / "scripts" / "desktop_d1_delete_running.js"
    assert running_probe.is_file(), "the running-task refusal probe must exist"
    assert "refusal_text" in running_probe.read_text(encoding="utf-8")
    body = runner.read_text(encoding="utf-8")
    assert "status_at_attempt" in body, "the running scenario must record the live status"
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_history_delete_runner.py" in sweep
    assert '"--scenario", "running"' in sweep
    assert '"--scenario", "paused"' in sweep
    assert "paused-ready" in body, "the paused scenario must be two-phase"
    paused_probe = ROOT / "scripts" / "desktop_d1_delete_paused.js"
    assert paused_probe.is_file() and "go-delete" in paused_probe.read_text(encoding="utf-8")


def test_project_delete_verification_is_wired():
    """§3.2: project deletion needs the typed name and writes a backup first."""
    runner = ROOT / "scripts" / "d1_project_delete_runner.py"
    probe = ROOT / "scripts" / "desktop_d1_project_delete.js"
    assert runner.is_file() and probe.is_file()
    body = runner.read_text(encoding="utf-8")
    assert "backup_mentions_project" in body and "project_still_present" in body
    assert "project_form_confirm" in probe.read_text(encoding="utf-8")
    sweep = (ROOT / "scripts" / "desktop_e2e_sweep.py").read_text(encoding="utf-8")
    assert "d1_project_delete_runner.py" in sweep


def test_sweep_covers_every_lifecycle_and_artifact_path():
    """The sweep must re-verify the mandated paths against the current build."""
    source = SWEEP.read_text(encoding="utf-8")
    for name in ("exit-matrix", "edit-exit", "quit-exit", "running-exit", "paused-exit",
                 "crash-recovery", "legacy-import", "ocr-baseline", "file-actions",
                 "dmg-launch", "upgrade"):
        assert f'"{name}"' in source, f"{name} missing from the sweep"
    # it must judge by exit code plus the runner's own reported result
    assert "_ok_from_output" in source and "exit_code" in source
    assert "sweep.json" in source


def test_sweep_scripts_exist_and_accept_the_shared_flags():
    import re

    source = SWEEP.read_text(encoding="utf-8")
    scripts = set(re.findall(r'"script": "([a-z0-9_.]+)"', source))
    assert scripts, "the sweep must reference runner scripts"
    for name in scripts:
        path = ROOT / "scripts" / name
        assert path.is_file(), f"{name} does not exist"
        body = path.read_text(encoding="utf-8")
        assert '"--out"' in body, f"{name} must accept --out"


CLEAN_ACCEPTANCE = ROOT / "scripts" / "clean_machine_acceptance.sh"
CLEAN_RUNBOOK = ROOT / "docs" / "desktop-d2" / "clean-machine-acceptance.md"


def test_clean_machine_acceptance_tooling_is_wired():
    """D2's external item must be executable on a machine without Python."""
    assert CLEAN_ACCEPTANCE.is_file() and CLEAN_RUNBOOK.is_file()
    body = CLEAN_ACCEPTANCE.read_text(encoding="utf-8")
    # No Python/Homebrew dependency: only macOS built-ins.
    for tool in ("hdiutil", "shasum", "defaults", "curl", "ditto"):
        assert tool in body, f"{tool} missing from the acceptance script"
    assert "python3" not in body.replace("command -v python3", ""), \
        "the script must not require Python"
    for check in ("hdiutil verify", "HTTP 200", "chi_sim", "job_state", "residual"):
        assert check in body or check.lower() in body.lower(), f"{check} missing"
    runbook = CLEAN_RUNBOOK.read_text(encoding="utf-8")
    assert "clean_machine_acceptance.sh" in runbook
    assert "Gatekeeper" in runbook and "未签名" in runbook
    assert "NOT YET TESTED" in runbook
