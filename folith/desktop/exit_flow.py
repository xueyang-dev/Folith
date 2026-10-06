"""Save & stop: flush unsaved WebView edits, stop a running task, then exit.

Roadmap §3.3 first-version behaviour:

* nothing running and nothing unsaved  -> exit and reclaim the service;
* unsaved edits in the WebView          -> flush the still-focused textarea,
  commit through the existing UI path, and only exit once the write is
  confirmed on disk (a failed commit must never be reported as "saved");
* a task still running                  -> stop scheduling new work by using the
  product's existing cancel semantics, wait for a bounded time, reuse the
  checkpoint/journal, and only then stop the service;
* waiting longer than the bound         -> the caller decides (continue waiting
  or force quit); a forced quit only promises that already-persisted results
  are recoverable.

This module deliberately **does not create new truth**: it reads the existing
`<job>/translation_drafts.json` (written by `core.save_translation_drafts`) and
`<job>/runtime_state.json`, and it commits edits by clicking the same UI buttons
a human would click.  It never writes job state itself.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DRAFTS_FILE = "translation_drafts.json"
RUNTIME_STATE_FILE = "runtime_state.json"
WORKER_LOCK_FILE = ".worker.lock"

# Mirrors `core.RUNTIME_ACTIVE_STATUSES` (core.py:8005).  Kept as a local copy so
# the launcher does not import the heavy business core just to read one set;
# `tests/desktop_exit_flow_test.py` asserts the two stay identical.
ACTIVE_STATUSES = frozenset({
    "resume_requested", "queued", "starting", "running",
    "waiting_external", "cancelling", "pausing", "paused",
})


@dataclass(frozen=True)
class PendingDraft:
    segment_id: str
    text: str
    baseline: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ExitSnapshot:
    job_id: str | None
    drafts: tuple[PendingDraft, ...]
    active_status: str | None
    worker_lock_held: bool

    @property
    def task_running(self) -> bool:
        return self.active_status in ACTIVE_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "draft_count": len(self.drafts),
            "draft_segments": [item.segment_id for item in self.drafts],
            "active_status": self.active_status,
            "task_running": self.task_running,
            "worker_lock_held": self.worker_lock_held,
        }


# --- read-only inspection of existing artifacts ----------------------------


def active_job_id_from_url(url: str | None) -> str | None:
    """Extract `?task=<job>` (the app's own routing parameter)."""
    if not url:
        return None
    from urllib.parse import parse_qs, urlparse

    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for key in ("task", "job"):
        values = query.get(key) or []
        if values and values[0].strip():
            return values[0].strip()
    return None


def read_pending_drafts(data_dir: Path, job_id: str) -> tuple[PendingDraft, ...]:
    """Uncommitted drafts, tolerant of a missing/corrupt file (never raises)."""
    path = Path(data_dir) / job_id / DRAFTS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    items = raw.get("drafts")
    if not isinstance(items, list):
        return ()
    drafts: list[PendingDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "").strip()
        if not segment_id:
            continue
        drafts.append(PendingDraft(
            segment_id=segment_id,
            text=str(item.get("text") or ""),
            baseline=str(item.get("baseline") or ""),
            updated_at=str(item.get("updated_at") or ""),
        ))
    return tuple(drafts)


def read_runtime_status(data_dir: Path, job_id: str) -> str | None:
    path = Path(data_dir) / job_id / RUNTIME_STATE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    status = raw.get("status") or raw.get("runtime_status")
    return str(status) if status else None


def worker_lock_held(data_dir: Path, job_id: str) -> bool:
    """True while the job's worker still holds its write lock.

    Uses the same OS lock the worker takes (`folith.job_lock`), so "free" here is
    real evidence that the worker released it — not a guess from a file's
    existence.
    """
    from folith import job_lock

    path = Path(data_dir) / job_id / WORKER_LOCK_FILE
    if not path.parent.exists():
        return False
    try:
        stream = job_lock.acquire(path, blocking=False)
    except OSError:  # pragma: no cover - defensive
        return False
    if stream is None:
        return True
    try:
        stream.close()
    except OSError:  # pragma: no cover - defensive
        pass
    return False


def active_job_ids(data_dir: Path) -> list[str]:
    """Jobs whose runtime state is active, whether or not the window shows them.

    The desktop window may be on the home page (or another job) while a task
    keeps running; closing the app must still stop that task, so the exit flow
    cannot rely on the URL alone.
    """
    root = Path(data_dir)
    if not root.is_dir():
        return []
    active: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        status = read_runtime_status(root, entry.name)
        if status in ACTIVE_STATUSES:
            active.append(entry.name)
    return active


def navigate_to_job(window, job_id: str, *, timeout: float = 30.0) -> bool:
    """Drive the app's own routing so the job's controls exist in the DOM."""
    script = (
        "(() => { const url = new URL(window.location.href);"
        " url.searchParams.set('task', %s);"
        " url.searchParams.set('section', 'translation');"
        " if (window.location.href !== url.toString()) { window.location.href = url.toString(); }"
        " return true; })()" % json.dumps(job_id)
    )
    try:
        evaluate(window, script)
    except Exception:
        return False
    return _wait(
        lambda: bool(evaluate(
            window,
            "!!document.querySelector('[class*=\"st-key-workspace_runtime_actions_\"]')"
            " || !!document.querySelector('[class*=\"st-key-cat_row_\"]')")),
        timeout,
    )


def snapshot(data_dir: Path, job_id: str | None) -> ExitSnapshot:
    if not job_id:
        return ExitSnapshot(job_id=None, drafts=(), active_status=None,
                            worker_lock_held=False)
    return ExitSnapshot(
        job_id=job_id,
        drafts=read_pending_drafts(data_dir, job_id),
        active_status=read_runtime_status(data_dir, job_id),
        worker_lock_held=worker_lock_held(data_dir, job_id),
    )


# --- WebView interaction ---------------------------------------------------

_BLUR_JS = """
(() => {
  const el = document.activeElement;
  const tag = el ? (el.tagName || '').toLowerCase() : '';
  const value = el && typeof el.value === 'string' ? el.value : '';
  if (el && typeof el.blur === 'function') {
    el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.blur();
  }
  return { tag, value_tail: value.slice(-40), value_length: value.length };
})()
"""

_ACTIVE_JOB_JS = "new URLSearchParams(window.location.search).get('task') || ''"

_COMMIT_ONE_JS = """
(() => {
  const wanted = %s;
  const rows = [...document.querySelectorAll('[class*="st-key-cat_row_"]')];
  for (const row of rows) {
    const area = row.querySelector('textarea');
    if (!area) continue;
    const value = (area.value || '').trim();
    if (!wanted.includes(value)) continue;
    const button = row.querySelector('[class*="st-key-cat_save_btn_"] button')
      || row.querySelector('[class*="st-key-cat_save_next_btn_"] button')
      || row.querySelector('[class*="st-key-translation_save_next_"] button');
    if (!button) return { clicked: false, reason: 'save-button-missing' };
    if (button.disabled) return { clicked: false, reason: 'save-button-disabled' };
    button.click();
    return { clicked: true, value_length: value.length };
  }
  return { clicked: false, reason: 'no-matching-row' };
})()
"""

_CANCEL_JS = """
(() => {
  const key = %s;
  const scoped = document.querySelector(`[class*="st-key-${key}"] button`);
  if (scoped && !scoped.disabled) { scoped.click(); return { clicked: true, via: 'scoped' }; }
  const buttons = [...document.querySelectorAll('button')];
  const fallback = buttons.find(b => (b.innerText || '').trim() === '取消' && !b.disabled);
  if (fallback) { fallback.click(); return { clicked: true, via: 'label' }; }
  return { clicked: false, reason: 'cancel-control-not-found' };
})()
"""


def evaluate(window, script: str) -> Any:
    """`window.evaluate_js` with a single retry (WebView can be mid-rerun)."""
    try:
        return window.evaluate_js(script)
    except Exception:
        time.sleep(0.4)
        return window.evaluate_js(script)


def current_job_id(window) -> str | None:
    try:
        value = evaluate(window, _ACTIVE_JOB_JS)
    except Exception:
        return None
    return str(value).strip() or None if value else None


def flush_focused_editor(window) -> dict[str, Any]:
    """Blur whatever the user was typing in so Streamlit records the draft."""
    try:
        result = evaluate(window, _BLUR_JS)
    except Exception as exc:
        return {"flushed": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"flushed": False, "error": "unexpected-js-result"}
    result["flushed"] = bool(result.get("value_length"))
    return result


def _wait(predicate: Callable[[], bool], timeout: float, interval: float = 0.4) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if predicate():
                return True
        except Exception:  # pragma: no cover - defensive
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def wait_for_draft_text(data_dir: Path, job_id: str, text: str, timeout: float) -> bool:
    """Wait until the flushed text is actually recorded in the draft file."""
    if not text:
        return True
    needle = text.strip()
    return _wait(
        lambda: any(item.text.strip() == needle
                    for item in read_pending_drafts(data_dir, job_id)),
        timeout,
    )


def commit_pending_drafts(window, data_dir: Path, job_id: str, *,
                          timeout: float = 20.0, max_rounds: int = 20) -> dict[str, Any]:
    """Commit every pending draft through the UI, verifying the file each time.

    One draft per round: each click triggers a Streamlit rerun, and batching
    clicks in a single round risks Streamlit coalescing them.
    """
    committed: list[str] = []
    failures: list[str] = []
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        drafts = read_pending_drafts(data_dir, job_id)
        if not drafts:
            break
        texts = [item.text.strip() for item in drafts if item.text.strip()]
        if not texts:
            failures.append("draft-without-text")
            break
        try:
            outcome = evaluate(window, _COMMIT_ONE_JS % json.dumps(texts, ensure_ascii=False))
        except Exception as exc:
            failures.append(f"commit-error: {type(exc).__name__}: {exc}")
            break
        if not isinstance(outcome, dict) or not outcome.get("clicked"):
            failures.append(str((outcome or {}).get("reason") or "commit-not-clicked"))
            break
        before = {item.segment_id for item in drafts}
        settled = _wait(
            lambda: {item.segment_id for item in read_pending_drafts(data_dir, job_id)} != before,
            timeout,
        )
        if not settled:
            failures.append("draft-file-did-not-change")
            break
        after = {item.segment_id for item in read_pending_drafts(data_dir, job_id)}
        committed.extend(sorted(before - after))
    remaining = read_pending_drafts(data_dir, job_id)
    return {
        "committed_segments": committed,
        "remaining": len(remaining),
        "rounds": rounds,
        "failures": failures,
        "confirmed": not remaining,
    }


# How long to keep looking for the cancel control before the click is a miss.
CANCEL_CLICK_WAIT = 15.0


def cancel_running_task(window, data_dir: Path, job_id: str, *,
                        timeout: float = 30.0) -> dict[str, Any]:
    """Reuse the product's cancel semantics, then wait for worker lock release."""
    # The cancel control lives inside the runtime strip, which renders
    # asynchronously (and only after the page has been driven to this job), so
    # retry the click for a bounded while instead of giving up on the first miss.
    clicked: dict[str, Any] = {"clicked": False, "reason": "cancel-control-not-found"}
    click_deadline = time.monotonic() + min(timeout, CANCEL_CLICK_WAIT)
    while True:
        try:
            result = evaluate(window, _CANCEL_JS % json.dumps(f"runtime_cancel_{job_id}"))
        except Exception as exc:
            result = {"clicked": False, "reason": f"{type(exc).__name__}: {exc}"}
        clicked = result if isinstance(result, dict) else {"clicked": bool(result)}
        if clicked.get("clicked"):
            break
        if read_runtime_status(data_dir, job_id) not in ACTIVE_STATUSES:
            break
        if time.monotonic() >= click_deadline:
            break
        time.sleep(1.0)
    left_active = _wait(
        lambda: read_runtime_status(data_dir, job_id) not in ACTIVE_STATUSES,
        timeout,
    )
    lock_free = _wait(lambda: not worker_lock_held(data_dir, job_id), timeout)
    return {
        "cancel_clicked": bool((clicked or {}).get("clicked")),
        "cancel_detail": clicked,
        "left_active_status": left_active,
        "worker_lock_released": lock_free,
        "status": read_runtime_status(data_dir, job_id),
    }


def confirm_force_exit(window, message: str) -> bool:
    """Ask the user whether to force-quit; default is the native dialog.

    Roadmap §3.3: once the bounded wait is exceeded the product must explicitly
    offer "keep waiting" or "force quit" instead of silently killing the worker.
    """
    if os.environ.get("FOLITH_EXIT_FORCE_PROMPT") == "0":
        # Automation switch: never open a modal dialog (it would block a
        # scripted run forever).  The unsafe report still keeps the window open.
        return False
    try:
        return bool(window.create_confirmation_dialog("译页", message))
    except Exception:
        return False


def run_exit_flow(window, *, data_dir: Path, flush_timeout: float = 20.0,
                  cancel_timeout: float = 30.0, commit_timeout: float = 20.0,
                  confirm: Callable[[Any, str], bool] | None = None,
                  on_step: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Full save-and-stop sequence; always returns a report, never raises."""
    report: dict[str, Any] = {"started_at": time.time()}
    confirm = confirm or confirm_force_exit
    steps: list[str] = report.setdefault("steps", [])  # type: ignore[assignment]

    def mark(name: str) -> None:
        steps.append(name)
        if on_step is not None:
            try:
                on_step(name)
            except Exception:  # pragma: no cover - logging must never break exit
                pass
    try:
        mark("locate-job")
        job_id = current_job_id(window)
        report["job_id_in_window"] = job_id
        if not job_id:
            # The window may be on the home page while a task keeps running.
            actives = active_job_ids(data_dir)
            report["active_jobs_on_disk"] = actives
            job_id = actives[0] if actives else None
            if job_id:
                report["job_source"] = "data-dir"
                mark("navigate-to-job")
                report["navigated_to_job"] = navigate_to_job(window, job_id)
        report["job_id"] = job_id
        if not job_id:
            report["action"] = "nothing-to-save"
            return report

        mark("flush-editor")
        flushed = flush_focused_editor(window)
        report["flush"] = flushed
        if flushed.get("value_tail"):
            report["flush_settled"] = _wait(
                lambda: bool(read_pending_drafts(data_dir, job_id)), flush_timeout
            )

        state = snapshot(data_dir, job_id)
        report["before"] = state.as_dict()
        if state.task_running:
            mark("cancel-task")
            report["cancel"] = cancel_running_task(
                window, data_dir, job_id, timeout=cancel_timeout)

        mark("commit-drafts")
        report["commit"] = commit_pending_drafts(
            window, data_dir, job_id, timeout=commit_timeout)

        # The window shows one job, but the data directory may hold several
        # active tasks (the product lets a user start another task while one
        # runs).  Roadmap §3.3 requires the running-task exit path to use the
        # product's cancel semantics for every running task: stopping only the
        # one in the window would tear the others down with the service and
        # report a "safe" exit while they are still running.
        others = [other for other in active_job_ids(data_dir) if other != job_id]
        if others:
            mark("cancel-other-tasks")
            report["other_cancels"] = {}
            for other in others:
                navigate_to_job(window, other)
                report["other_cancels"][other] = cancel_running_task(
                    window, data_dir, other, timeout=cancel_timeout)

        mark("final-check")
        final = snapshot(data_dir, job_id)
        report["after"] = final.as_dict()
        other_states = {other: snapshot(data_dir, other) for other in others}
        report["other_after"] = {other: state.as_dict()
                                 for other, state in other_states.items()}
        safe = not final.drafts and not final.worker_lock_held \
            and not final.task_running \
            and all(not state.drafts and not state.worker_lock_held
                    and not state.task_running for state in other_states.values())
        report["safe_to_exit"] = safe
        report["action"] = "saved-and-stopped" if safe else "incomplete"
        if not safe:
            # Bounded wait exceeded: offer keep-waiting vs force-quit.
            pending = len(final.drafts)
            detail = []
            if pending:
                detail.append(f"还有 {pending} 处编辑未提交")
            if final.task_running:
                detail.append(f"任务仍在停止中（{final.active_status}）")
            if final.worker_lock_held:
                detail.append("worker 仍持有写入锁")
            still_running = [other for other, state in other_states.items()
                             if state.task_running or state.worker_lock_held]
            if still_running:
                detail.append(f"另有 {len(still_running)} 个任务仍在运行："
                              f"{'、'.join(still_running)}")
            message = ("；".join(detail) or "退出前检查未完成") + \
                "。\n\n选择「确定」将强制退出，只保证已经落盘的结果可恢复；" \
                "选择「取消」返回应用继续等待。"
            mark("force-prompt")
            report["force_prompt"] = message
            report["forced"] = bool(confirm(window, message))
            if report["forced"]:
                report["safe_to_exit"] = True
                report["action"] = "forced-exit"
    except Exception as exc:  # never block the window close on our own bug
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe_to_exit"] = False
        report["action"] = "error"
    report["finished_at"] = time.time()
    return report


def describe(report: dict[str, Any]) -> str:
    """One-line human summary for the launcher log."""
    action = report.get("action")
    if action == "nothing-to-save":
        return "退出前检查：无未提交编辑"
    commit = report.get("commit") or {}
    cancel = report.get("cancel") or {}
    parts = [
        f"提交草稿 {len(commit.get('committed_segments') or [])} 处",
        f"剩余草稿 {commit.get('remaining', 0)} 处",
    ]
    if cancel:
        parts.append(f"取消运行任务：{'是' if cancel.get('cancel_clicked') else '否'}")
        parts.append(f"worker 锁释放：{'是' if cancel.get('worker_lock_released') else '否'}")
    if report.get("forced"):
        parts.append("按用户选择强制退出（仅保证已落盘内容可恢复）")
    if report.get("job_source") == "data-dir":
        parts.append("任务来自数据目录扫描（窗口未停留在该任务）")
    if report.get("error"):
        parts.append(f"错误：{report['error']}")
    return "退出前检查：" + "；".join(parts)
