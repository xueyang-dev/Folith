#!/usr/bin/env python3
"""Fixed-scenario runtime baseline runner (issue #323, A0).

The point is to make "卡顿 / 无响应 / 占用" repeatable *numbers*, not to be
another probe.  A scenario run produces one JSON file that carries:

* the GUI-thread stall window measured by ``folith.qt.jank_monitor`` (p50/p99,
  longest, per-minute counts, Top-N stacks by accumulated stall time),
* the task's own performance record — ``.performance/runs/run_*/summary.json``
  with its ``resources`` block, ``gui_jank`` block and ``gui_jank.json``,
* the UI-health incident (out-of-process watchdog) status.

Scenarios (S1 and S3 are implemented; the rest are declared so the report
format is stable):

* S1 — a large EPUB is being translated while the driver scrolls the paragraph
  table, edits a translation and switches stage pages.
* S2 — long scanned PDF, paging and thumbnails while OCR runs.   (skeleton)
* S3 — open a finished large task and switch tasks (10,000 segments by default).
* S4 — eight-hour run against a local mock gateway.               (skeleton)

Measurement v3 (issue #347, plan §9 D1 / §11 / §13 G2-G3)
    ``measurement.version = 3``.  The session is split into two phases so the
    two numbers the plan judges are *separately* computable:

    * **R (翻译中)** — jank events inside the run window (translation started →
      task settled) divided by that window's duration.
    * **I (完成后)** — the rest of the scenario window: ``(scenario − run)``
      events over ``(scenario − run)`` seconds.  A round whose I phase is
      shorter than :data:`I_PHASE_MIN_SECONDS` cannot separate the phases and is
      voided; the report counts the voided rounds instead of dropping them.

    Both are reported per tier (>100 ms and >500 ms) and twice over: with the
    measuring tool's own stalls included and with them excluded, plus the tool's
    share, because a tool that is the biggest stall source makes every historical
    absolute number incomparable.  The driver also records the latency of the
    five core operations (plan §10) as p50/p95/p99 per phase.

    No window is ever allowed to read as "zero stalls" when nothing was
    sampled: an empty, disabled or never-pulsed window is ``measured: false``
    with a reason (F5).

Usage::

    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/runtime_baseline_runner.py \
        --out /tmp/runtime-baseline --paras 200 --seconds 120

    # real machine, frozen app (the run the baseline report needs):
    venv/bin/python scripts/runtime_baseline_runner.py --out /tmp/baseline-mac \
        --paras 10000 --seconds 1800 --mode frozen \
        --frozen-app dist/qt-desktop/Folith.app/Contents/MacOS/Folith

    # median of several rounds of the same variant (plan: ≥3 rounds × ≥5 min):
    venv/bin/python scripts/runtime_baseline_runner.py --aggregate /tmp/baseline-mac

Offscreen numbers are labelled ``mode: source_offscreen`` and can never stand in
for the macOS/Windows frozen-app baseline: an offscreen QPA has no compositor,
no GPU path and no native input, and this machine is not the tester's machine.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.trace import compute_percentiles  # noqa: E402 - needs ROOT on sys.path

NOT_IMPLEMENTED = {
    "S2": "long scanned PDF: paging/thumbnails while OCR runs",
    "S4": "eight-hour run against a local mock gateway",
}

#: The measurement definition implemented by this file.
#: v1: the harness pumped the GUI thread (its own frames were the biggest stall).
#: v2: steps run from Qt event-loop callbacks.
#: v3: phases (R/I), per-operation latency, S3, and the tool's own share.
MEASUREMENT_VERSION = 3

#: A round whose "after the translation finished" phase is shorter than this
#: cannot separate R from I, so the round is void (plan §9 D1).
I_PHASE_MIN_SECONDS = 15.0

#: Sampler thresholds the two tiers are named after.
TIER_SLOW_MS = 100.0
TIER_SEVERE_MS = 500.0

#: How often the driver asks whether the task settled *while still interacting*.
#: Without it the run window would end where the interaction window ends, which
#: dilutes R with idle time after the translation is done.  Polling is harness
#: work, so every poll runs under the sampler's instrumentation mark.
SETTLE_POLL_S = 0.5

#: Frame names that identify this harness inside a captured stack.  A stall whose
#: innermost frame is one of these is the *tool*, not the product (except the
#: application-event-loop frame, which is where a native stall legitimately shows).
RUNNER_FILE = Path(__file__).name
APPLICATION_LOOP_FRAME = "_run_scenario_event_loop"

LIMITS = (
    "offscreen QPA: no compositor, no GPU paint path, no native input events",
    "this harness drives the window from Python; a real user's input timing differs",
    "one machine per run: a baseline needs the same scenarios on macOS and Windows",
    "per-operation latency is the synchronous GUI-thread span of the call; work a "
    "later paint/timer does is outside it",
    "the broadcast of rendered frames is not measured; frame pacing is approximate",
)



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_epub(path: Path, paragraphs: int) -> Path:
    """Deterministic EPUB with *paragraphs* segments (no random content)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"<p>第 {index + 1} 段：基线场景用于在同一份文档上重复测量主线程响应。"
        f"The runtime baseline keeps the workload identical across runs "
        f"(segment {index + 1}).</p>"
        for index in range(max(1, paragraphs)))
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles><rootfile full-path=\"book.opf\"/></rootfiles></container>")
        book.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest><spine><itemref idref=\"chapter\"/></spine></package>")
        book.writestr(
            "chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            + body + "</body></html>")
    return path


class MockGateway:
    """Local mock model endpoint, so a run needs no API key and no network."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.out_dir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        self._handle = (self.out_dir / "stub-server.log").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--log", str(self.out_dir / "stub-llm.jsonl"),
             "--ready-file", str(ready)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("mock gateway exited before becoming ready")
            time.sleep(0.1)
        raise RuntimeError("mock gateway did not become ready in 30 s")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - hung child
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def read_run_records(data_dir: Path, job_id: str) -> list[dict]:
    """The task's own performance records, newest last; nothing is re-derived."""
    rows: list[dict] = []
    base = data_dir / job_id / ".performance" / "runs"
    for summary_path in sorted(base.glob("run_*/summary.json")):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        jank_path = summary_path.parent / "gui_jank.json"
        rows.append({
            "run_id": payload.get("run_id") or summary_path.parent.name,
            "directory": str(summary_path.parent),
            "wall_time_s": payload.get("wall_time_s"),
            "resources": payload.get("resources"),
            "gui_jank": payload.get("gui_jank"),
            "gui_jank_file": jank_path.is_file(),
            "resources_jsonl_lines": _count_lines(summary_path.parent / "resources.jsonl"),
        })
    return rows


def _window_observed_s(block) -> float | None:
    """Seconds a sampler window actually observed, from either scope's shape.

    The process scope puts ``observed_s`` at the top level; the run scope nests
    it under ``window``.  Both are read here so a caller never has to know which
    shape it was handed.
    """
    if not isinstance(block, dict):
        return None
    candidates = [block.get("observed_s")]
    nested = block.get("window")
    if isinstance(nested, dict):
        candidates.append(nested.get("observed_s"))
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            continue
        return float(candidate)
    return None


def _window_pulse_count(block) -> int | None:
    if not isinstance(block, dict):
        return None
    value = block.get("pulse_count")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _unmeasured_reason(block: dict) -> str:
    """Name *why* a window is not evidence, so ``measured: false`` is actionable."""
    if not isinstance(block, dict) or not block:
        return "empty stall window: the producer returned no sampler block"
    if block.get("enabled") is False:
        return "stall sampler disabled (FOLITH_JANK_MONITOR=0)"
    if block.get("measured") is False:
        return str(block.get("reason") or "the producer marked this window unmeasured")
    observed = _window_observed_s(block)
    if observed is not None and observed <= 0.0:
        return "the window observed no time (observed_s <= 0)"
    pulses = _window_pulse_count(block)
    if pulses is not None:
        return f"the window contains no sampler pulse (pulse_count={pulses})"
    return ("the window carries no observation evidence: no pulse_count and no "
            "observed_s, so a zero here would be a guess")


def _window_is_measured(block) -> bool:
    """Whether a window block is evidence of observation, not an empty shell.

    F5: "nothing was sampled" and "nothing stalled" must not read the same.  An
    empty dict, a never-pulsed window and a disabled sampler all answer ``False``,
    so no reader can mistake a zero-filled block for a measured zero.
    """
    if not isinstance(block, dict) or not block:
        return False
    if block.get("measured") is False:
        return False
    if block.get("enabled") is False:
        return False
    observed = _window_observed_s(block)
    if observed is not None and observed <= 0.0:
        return False
    pulses = _window_pulse_count(block)
    if pulses is not None and pulses <= 0:
        return False
    # At least one piece of evidence that the sampler ran over real time.
    if pulses is None and observed is None:
        return False
    return True


def _normalize_window(window: dict) -> dict:
    """Make a sampler window answer "measured?" the same way a run record does.

    Before v3 only ``enabled is False`` was flagged, so an empty window came back
    as a block of zeros that read like "zero stalls" (F5).  Unmeasured windows
    now say ``measured: false`` and name the reason; the counters are left
    untouched so a reader can still see what the sampler reported.
    """
    block = dict(window) if isinstance(window, dict) else {}
    if not block:
        return {"measured": False, "reason": _unmeasured_reason(block)}
    if block.get("enabled") is False:
        block["measured"] = False
        block["reason"] = _unmeasured_reason(block)
        return block
    if _window_is_measured(block):
        block["measured"] = True
        return block
    # Name the reason *before* stamping measured: false, or the stamp itself
    # would become the explanation.
    reason = _unmeasured_reason(block)
    block["measured"] = False
    block.setdefault("reason", reason)
    return block


def _stall_window_from_records(records: list[dict]) -> dict:
    """The last stall window a run record actually measured, else an explicit miss.

    "No window" and "zero stalls" must not look the same to a reader: a record
    without a measured window says so instead of collapsing to ``{}``.  A record
    whose window exists but carries no observation evidence is not accepted
    either (F5) — it is reported as unmeasured rather than as a clean zero.
    """
    seen_unmeasured: dict = {}
    for row in reversed(records):
        block = row.get("gui_jank")
        if isinstance(block, dict) and _window_is_measured(block):
            return _normalize_window(block)
        if isinstance(block, dict) and block:
            seen_unmeasured = block
    if seen_unmeasured:
        return _normalize_window(seen_unmeasured)
    return {
        "measured": False,
        "reason": "no run record carried a GUI stall window (sources list is empty "
                  "or the sampler was disabled)",
    }



def _run_window_from_records(records: list[dict]) -> dict:
    """The product's own translating window, from its run records.

    Used where the harness has no checkpoints of its own (the frozen path drives
    the app as a subprocess): the app's run record already measured exactly the
    translating window, so R is still computable.  The longest measured record
    wins, because a scenario can leave more than one run behind.
    """
    candidates = [row.get("gui_jank") for row in records
                  if isinstance(row.get("gui_jank"), dict)
                  and _window_is_measured(row.get("gui_jank"))]
    if not candidates:
        return {"measured": False,
                "reason": "no run record carried a measured translating window"}
    best = max(candidates, key=lambda block: _window_observed_s(block) or 0.0)
    return _normalize_window(best)


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Per-operation latency (plan §10 / §13 G3)
# ---------------------------------------------------------------------------

#: The five core operations whose p95 the plan budgets on a 10,000-segment task.
#: ``budget_ms`` is the plan §10 number; ``first_budget_ms`` is the separate
#: allowance for the first (cold) open of a page.
CORE_OPERATIONS = {
    "scroll": {"label": "滚动段落网格", "budget_ms": 50.0},
    "select_segment": {"label": "选中段落到详情渲染完成", "budget_ms": 100.0},
    "type_echo": {"label": "键入到回显", "budget_ms": 50.0},
    "page_switch": {"label": "切换阶段页", "budget_ms": 150.0,
                    "first_budget_ms": 300.0},
    "open_panel": {"label": "打开抽屉或浮层", "budget_ms": 100.0},
}

#: Operations the driver records because a reader needs them to interpret the
#: five above (setup work, and the cost of closing what was opened).  They have
#: no plan budget, so no verdict is attached to them.
SUPPORT_OPERATIONS = {
    "import": {"label": "导入文档"},
    "open_task": {"label": "打开任务（点击到工作台可用）"},
    "start_translation": {"label": "开始/继续翻译"},
    "submit_edit": {"label": "提交译文修改"},
    "close_panel": {"label": "收起抽屉或浮层"},
    "switch_task": {"label": "切换任务（S3）"},
}

#: ``setup`` is work before the translation starts, ``run`` is the translating
#: phase, ``complete`` is everything after it finished — the same split as R/I.
OPERATION_PHASES = ("setup", "run", "complete")


class OperationRecorder:
    """Latency samples per operation, bucketed by measurement phase.

    A sample is one synchronous GUI-thread span: the clock is read immediately
    before the call and immediately after it returns, so the number is how long
    the user's input was occupying the main thread.  Work Qt defers to a later
    paint or timer is outside the sample; that limit is written into the report.
    """

    def __init__(self) -> None:
        self._samples: dict[str, dict[str, list[float]]] = {}
        self._variants: dict[str, dict[str, int]] = {}

    def add(self, name: str, phase: str, elapsed_ms: float) -> None:
        bucket = self._samples.setdefault(name, {})
        bucket.setdefault(phase if phase in OPERATION_PHASES else "setup", []).append(
            max(0.0, float(elapsed_ms)))

    def count(self, name: str, phase: str) -> int:
        return len((self._samples.get(name) or {}).get(phase) or [])

    def note_variant(self, name: str, variant: str) -> None:
        row = self._variants.setdefault(name, {})
        row[variant] = row.get(variant, 0) + 1

    def report(self) -> dict:
        operations: dict[str, dict] = {}
        specs = {**CORE_OPERATIONS, **SUPPORT_OPERATIONS}
        for name, spec in specs.items():
            phases: dict[str, dict] = {}
            for phase in OPERATION_PHASES:
                values = list((self._samples.get(name) or {}).get(phase) or [])
                if not values:
                    phases[phase] = {
                        "measured": False,
                        "count": 0,
                        "reason": "no sample of this operation in this phase",
                    }
                    continue
                phases[phase] = self._stats(values, spec)
            entry = {"label": spec["label"], "core": name in CORE_OPERATIONS,
                     "phases": phases}
            if name in self._variants:
                entry["variants"] = dict(self._variants[name])
            operations[name] = entry
        return {
            "unit": "ms",
            "clock": "time.monotonic around the operation call (synchronous GUI-thread span)",
            "phases": list(OPERATION_PHASES),
            "phase_meaning": {
                "setup": "before the translation started (import, open, start)",
                "run": "while the translation was running (R window)",
                "complete": "after it finished (the I phase)",
            },
            "operations": operations,
            "missing": [name for name in CORE_OPERATIONS
                        if not any(self.count(name, phase) for phase in OPERATION_PHASES)],
            "note": "p95 is a percentile of the samples in that phase; a phase with no "
                    "sample is reported as unmeasured, never as a zero latency",
        }

    @staticmethod
    def _stats(values: list[float], spec: dict) -> dict:
        raw = compute_percentiles(values)
        entry: dict = {"measured": True}
        for key, value in raw.items():
            entry[key] = int(value) if key == "count" else round(float(value), 3)
        if spec.get("budget_ms") is not None:
            entry["budget_ms"] = float(spec["budget_ms"])
            entry["p95_within_budget"] = bool(raw["p95"] <= float(spec["budget_ms"]))
        if spec.get("first_budget_ms") is not None:
            first = round(float(values[0]), 3)
            entry["first_ms"] = first
            entry["first_budget_ms"] = float(spec["first_budget_ms"])
            entry["first_within_budget"] = bool(first <= float(spec["first_budget_ms"]))
        return entry


class OperationTimer:
    """``with timer("scroll"):`` around one user-visible operation."""

    def __init__(self, recorder: OperationRecorder, name: str, *,
                 phase: str | None = None, clock=time.monotonic) -> None:
        self.recorder = recorder
        self.name = name
        self.phase = phase
        self.clock = clock

    def __enter__(self) -> "OperationTimer":
        self._started = self.clock()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed = (self.clock() - self._started) * 1000.0
        # A failed operation still cost the user time, so it is recorded; the
        # failure itself is recorded by the caller's handler.
        self.recorder.add(self.name, self.phase, elapsed)
        return False


# ---------------------------------------------------------------------------
# R / I phases and the tool's own share
# ---------------------------------------------------------------------------

def _rate_per_minute(count: int, seconds: float | None) -> float | None:
    if not seconds or seconds <= 0.0:
        return None
    return round(count * 60.0 / seconds, 2)


def _tier_block(window: dict, seconds: float | None, *, tier: str) -> dict:
    """Both readings of one tier plus the tool's share of it."""
    if tier == "slow_100ms":
        product = int(window.get("slow_count") or 0)
        tool = int(window.get("instrumentation_slow_count") or 0)
    else:
        product = int(window.get("severe_count") or 0)
        tool = int(window.get("instrumentation_severe_count") or 0)
    total = product + tool
    return {
        "excluding_tool": {"count": product, "per_minute": _rate_per_minute(product, seconds)},
        "including_tool": {"count": total, "per_minute": _rate_per_minute(total, seconds)},
        "tool_events": tool,
        "tool_share": round(tool / total, 4) if total else None,
    }


def _runner_frame_events(window: dict) -> dict:
    """Product-side stalls whose innermost captured frame is this harness (P0-5v).

    These are counted *inside* the product numbers, so they are reported as an
    attribution of that number, never added to the tool total — otherwise the
    "including tool" and "excluding tool" groups would not reconcile.
    ``_run_scenario_event_loop`` is excluded on purpose: a stall captured while Qt
    dispatches natively has no Python frame below, so the application's own event
    loop is the innermost frame and that is the product, not the harness.
    """
    rows = window.get("top_stacks")
    if rows is None:
        return {
            "count": None,
            "stacks": [],
            "criteria": "innermost captured frame is a runtime_baseline_runner function "
                        "other than _run_scenario_event_loop",
            "reason": "the residual window has no per-stack breakdown: subtracting "
                      "counters cannot attribute individual frames, so this number "
                      "is not derivable for I (it is in the R window instead)",
        }
    hits: list[dict] = []
    total = 0
    for row in rows or []:
        stack = row.get("stack") or []
        if not stack:
            continue
        innermost = str(stack[-1])
        if RUNNER_FILE not in innermost or APPLICATION_LOOP_FRAME in innermost:
            continue
        count = int(row.get("count") or 0)
        total += count
        hits.append({"stack": list(stack[-3:]), "count": count,
                     "total_ms": row.get("total_ms"), "max_ms": row.get("max_ms")})
    return {
        "count": total,
        "stacks": hits[:5],
        "criteria": "innermost captured frame is a runtime_baseline_runner function "
                    "other than _run_scenario_event_loop",
    }


def _empty_phase(reason: str) -> dict:
    return {"measured": False, "reason": reason}


def _phase_block(label: str, window: dict, seconds: float | None, *,
                 window_name: str, source: str) -> dict:
    runner_frames = _runner_frame_events(window)
    product_slow = int(window.get("slow_count") or 0)
    return {
        "label": label,
        "window": window_name,
        "window_source": source,
        "measured": True,
        "observed_s": round(seconds, 3) if seconds is not None else None,
        "tiers": {
            "slow_100ms": _tier_block(window, seconds, tier="slow_100ms"),
            "severe_500ms": _tier_block(window, seconds, tier="severe_500ms"),
        },
        "runner_frame_events": runner_frames["count"],
        "runner_frame_share_of_product": (
            round(runner_frames["count"] / product_slow, 4)
            if product_slow and runner_frames["count"] is not None else None),
        "runner_frame_reason": runner_frames.get("reason"),
        "runner_frame_stacks": runner_frames["stacks"],
        "runner_frame_criteria": runner_frames["criteria"],
        "stall_ms": window.get("stall_ms"),
        "longest": _longest_summary(window.get("longest"), window,
                                    residual=window.get("longest_residual", False)),
    }


def _longest_summary(longest, window: dict | None = None, *, residual: bool = False) -> dict:
    """The sampler's longest product stall, with its own honesty about coverage.

    F1 (P1-1) makes ``longest`` window-exact by keeping a suffix-maxima chain
    instead of deriving it from the bounded ``recent`` ring, and the sampler says
    so in its own ``notes``.  This reads that statement rather than assuming the
    maximum is exact: a build whose longest still comes from the ring may miss a
    longer stall that has already been evicted, and the report must not present
    it as the window's maximum.
    """
    if not isinstance(longest, dict):
        reason = ("the residual window has no event list: subtracting counters cannot "
                  "produce a longest stall, so this is not derivable for I"
                  if residual else "the sampler reported no longest stall")
        return {"measured": False, "reason": reason}
    stack = [str(item) for item in (longest.get("stack") or [])]
    entry = {
        "measured": True,
        "interval_ms": longest.get("interval_ms"),
        "blocked_ms": longest.get("blocked_ms"),
        "severity": longest.get("severity"),
        "at": longest.get("at"),
        "innermost_frame": stack[-1] if stack else None,
    }
    notes = [str(note) for note in ((window or {}).get("notes") or [])]
    exact_note = next((note for note in notes if "suffix-maxima" in note), "")
    if "longest_exact" in longest:
        entry["exact"] = bool(longest.get("longest_exact"))
        entry["exact_reason"] = "the sampler published an explicit exactness marker"
    elif exact_note:
        entry["exact"] = True
        entry["exact_reason"] = (
            "the sampler reports a window-scoped suffix-maxima chain for the "
            "longest stall, so it is not truncated by the bounded recent ring")
    else:
        entry["exact"] = None
        entry["exact_reason"] = (
            "this sampler build derives longest from the bounded event ring; "
            "a longer stall that already left it would be missed")
    return entry


def _phase_metrics(scenario: dict, run: dict | None) -> dict:
    """R (翻译中) and I (完成后) from the scenario and run windows (plan §9 D1).

    ``scenario`` is the whole measured session; ``run`` is the translating window.
    R is the run window, I is the remainder of the scenario window.  Subtraction
    happens on counters and on observed seconds, so both windows must be measured
    — a window with no evidence produces "not measured" rather than a zero rate.

    S3 has no translating window at all (its tasks are already finished): R is
    then reported as not applicable and I covers the whole scenario window.
    """
    scenario_measured = _window_is_measured(scenario)
    run_measured = bool(run) and _window_is_measured(run)
    scenario_s = _window_observed_s(scenario) if scenario_measured else None
    run_s = _window_observed_s(run) if run_measured else None

    discard_reasons: list[str] = []
    result: dict = {
        "definition": {
            "R": "运行窗口卡顿次数 ÷ 运行窗口时长（翻译中）",
            "I": "(场景窗口次数 − 运行窗口次数) ÷ (场景窗口时长 − 运行窗口时长)（完成后）",
            "tiers_ms": {"slow": TIER_SLOW_MS, "severe": TIER_SEVERE_MS},
            "i_phase_min_seconds": I_PHASE_MIN_SECONDS,
            "tool_split": "excluding_tool = the sampler's product counters; "
                          "including_tool = product + the sampler's instrumentation "
                          "counters (the harness's own stalls)",
        },
    }

    if not scenario_measured:
        return {
            **result,
            "R": _empty_phase("the scenario window was not measured, so R has no basis"),
            "I": _empty_phase("the scenario window was not measured, so I has no basis"),
            "valid_round": False,
            "discarded_rounds": 1,
            "discard_reasons": [str(scenario.get("reason") if isinstance(scenario, dict)
                                    else "scenario window missing")],
        }

    if run_measured and run_s is not None and scenario_s is not None:
        result["R"] = _phase_block(
            "翻译中", run, run_s, window_name="run_window",
            source="runner checkpoints: translation start → first tick that saw the "
                   "task settled")
        i_s = max(0.0, scenario_s - run_s)
        i_window = {
            key: max(0, int(scenario.get(key) or 0) - int(run.get(key) or 0))
            for key in ("slow_count", "severe_count", "instrumentation_slow_count",
                        "instrumentation_severe_count", "pulse_count")
        }
        # Counters subtract cleanly; per-stack and per-event detail does not, so it
        # is reported as not derivable instead of as a zero.
        i_window["top_stacks"] = None
        i_window["stall_ms"] = None
        i_window["longest"] = None
        i_window["longest_residual"] = True
        i_window["instrumentation_total_ms"] = max(
            0.0, float(scenario.get("instrumentation_total_ms") or 0.0)
            - float(run.get("instrumentation_total_ms") or 0.0))
        result["I"] = _phase_block(
            "完成后", i_window, i_s, window_name="scenario_window - run_window",
            source="scenario window minus the run window; includes import/settle tail")
        result["I"]["residual_note"] = (
            "by definition this bucket is everything outside the run window, so it "
            "also contains the import/open/settle tail, not only post-completion time")
        valid = i_s >= I_PHASE_MIN_SECONDS
        result["I"]["valid"] = valid
        result["R"]["valid"] = True
        if not valid:
            result["I"]["invalid_reason"] = (
                f"I phase is {round(i_s, 2)} s, shorter than the "
                f"{I_PHASE_MIN_SECONDS:g} s minimum")
            discard_reasons.append(result["I"]["invalid_reason"])
        result["i_phase_s"] = round(i_s, 3)
    else:
        reason = (run.get("reason") if isinstance(run, dict) and run.get("reason")
                  else "no translating window exists in this scenario")
        result["R"] = _empty_phase(
            f"not applicable: {reason}")
        result["I"] = _phase_block(
            "完成后", scenario, scenario_s, window_name="scenario_window",
            source="the whole scenario window (no run window to subtract)")
        valid = (scenario_s or 0.0) >= I_PHASE_MIN_SECONDS
        result["I"]["valid"] = valid
        result["I"]["r_applicable"] = False
        result["i_phase_s"] = round(scenario_s, 3) if scenario_s is not None else None
        if not valid:
            result["I"]["invalid_reason"] = (
                f"I phase is {round(scenario_s or 0.0, 2)} s, shorter than the "
                f"{I_PHASE_MIN_SECONDS:g} s minimum")
            discard_reasons.append(result["I"]["invalid_reason"])

    result["valid_round"] = not discard_reasons
    result["discarded_rounds"] = 1 if discard_reasons else 0
    result["discard_reasons"] = discard_reasons
    return result


def scenario_environment(base_url: str, data_dir: Path, log_dir: Path) -> dict:
    """Environment for the window under test (keeps the caller's PATH/HOME)."""
    env = dict(os.environ)
    env["FOLITH_QT_SELFTEST_BASE_URL"] = base_url
    env["FOLITH_QT_NONINTERACTIVE"] = "1"
    env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    env["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    env["FOLITH_DESKTOP_LOG_DIR"] = str(log_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


_TICK_MS = 25
_PREDICATE_INTERVAL_S = 0.2


def _job_settled(adapter, job_id: str) -> bool:
    """The task reached a terminal state (failed/cancelled counts as settled)."""
    runtime = adapter.core.load_runtime_state(job_id) or {}
    status = runtime.get("status")
    if status in ("failed", "stalled", "interrupted", "cancelled"):
        return True
    if status in adapter.core.RUNTIME_ACTIVE_STATUSES:
        return False
    return bool((adapter.load_state(job_id) or {}).get("p2_done"))


class _ScenarioDriver:
    """Drive S1 from Qt event-loop callbacks instead of pumping the GUI (#327).

    The previous version called ``QApplication.processEvents()`` in a loop from
    script code.  That makes the measuring tool itself the biggest stall bucket:
    the GUI thread sits inside the harness frame, so every native/layout stall
    is attributed to the driver and the measured p99 contains the harness.
    Here every step runs from a ``QTimer`` callback (the caller wires ``begin``
    to a zero-delay single-shot and ``tick`` to a repeating timer), and the
    driver never pumps events.  Only predicate polling (job listing, runtime
    state reads) is marked as instrumentation, because that is harness work the
    product does not do, so it cannot be counted against the product.

    Measurement v3 adds three things that are not scenario logic:

    * a **run window** checkpoint at translation start and another the moment the
      task is seen settled (polled during interaction too, so the window ends
      when the translation ends, not when the interaction window does),
    * **per-operation latency** for the five core operations, tagged with the
      phase they happened in (setup / run / complete),
    * the phase of every sample, so the report can compute R and I from the same
      run instead of two different runs.

    The state machine itself is Qt-free and deterministic, so a test can call
    ``begin()``/``tick()`` directly with stub objects.
    """

    def __init__(self, *, app, window, adapter, jank, fixture, seconds,
                 settle_timeout, result, operations=None, clock=time.monotonic):
        self.app = app
        self.window = window
        self.adapter = adapter
        self.jank = jank
        self.fixture = str(fixture)
        self.seconds = max(1.0, float(seconds))
        self.settle_timeout = max(1.0, float(settle_timeout))
        self.result = result
        self.clock = clock
        self.operations = operations if operations is not None else OperationRecorder()
        self.state = "created"
        self.finished = False
        self.settled = False
        self.job_id = ""
        self.edited = self.pages = self.scrolls = self.panels = 0
        # Phase windows (measurement v3).  ``run_window`` stays None unless a
        # translating phase actually existed and the sampler was usable.
        self.run_window: dict | None = None
        self.run_observed_s: float | None = None
        self.run_started_at: float | None = None
        self.translation_finished_at: float | None = None
        self._run_open = False
        self._run_checkpoint: dict | None = None
        self._started = 0.0
        self._deadline = 0.0
        self._open_started = 0.0
        self._open_span_start = 0.0
        self._interact_until = 0.0
        self._settle_started = 0.0
        self._last_predicate = -1.0
        self._last_settle_poll = -1.0
        self._last_scroll = 0.0
        self._last_edit = 0.0
        self._last_page = 0.0
        self._last_panel = 0.0
        self._panel_open = False
        self._page_ids = ("translate", "review", "deliver")
        self._panel_kinds = ("activity_popover", "followup_drawer")

    # -- measurement phase ------------------------------------------------
    @property
    def measurement_phase(self) -> str:
        """Which phase a latency sample belongs to: setup / run / complete."""
        if self._run_open:
            return "run"
        if self.run_observed_s is not None:
            return "complete"
        return "setup"

    # -- event-loop callbacks ---------------------------------------------
    def begin(self) -> None:
        """First callback: dispatch the import, then let the event loop run."""
        self.state = "import"
        self._started = self.clock()
        self._deadline = self._started + self.settle_timeout
        self.result["steps"].append({"step": "import_dispatched", "ok": True})
        with OperationTimer(self.operations, "import", phase="setup", clock=self.clock):
            self.window.import_paths([self.fixture], autostart=False)

    def tick(self) -> bool:
        """One event-loop tick; returns True once the scenario is over."""
        if self.finished:
            return True
        try:
            getattr(self, "_tick_" + self.state)(self.clock())
        except Exception as exc:  # noqa: BLE001 - recorded in the report
            self.result["failures"].append(f"{self.state}: {type(exc).__name__}: {exc}")
            self._finish(settled=False)
        return self.finished

    # -- states ------------------------------------------------------------
    def _tick_import(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        imported = self._instrumented(
            lambda: not self.window.runner.busy and len(self.adapter.list_jobs()) >= 1)
        if not imported:
            if now > self._deadline:
                self._fail("import did not produce a task")
            return
        jobs = self._instrumented(lambda: len(self.adapter.list_jobs()))
        self.result["steps"].append({"step": "imported", "ok": True, "jobs": jobs})
        self.job_id = self._instrumented(lambda: self.adapter.list_jobs()[0].job_id)
        self.result["job_id"] = self.job_id
        self._open_span_start = now
        self.window.open_job(self.job_id)
        self._open_started = now
        self.state = "open"

    def _tick_open(self, now: float) -> None:
        if getattr(self.window, "_opening_job_id", ""):
            if now - self._open_started > min(120.0, self.settle_timeout):
                self._fail("task did not open")
            return
        if getattr(self.window, "_current_job_id", "") != self.job_id:
            self._fail("task did not open")
            return
        # "Open task" is click → workbench usable: the window reads the task
        # off-thread, so the span has to be closed here, across ticks.
        self.operations.add("open_task", "setup",
                            (now - self._open_span_start) * 1000.0)
        self.result["steps"].append({"step": "opened", "ok": True})
        self._phase("translate")
        with OperationTimer(self.operations, "start_translation", phase="setup",
                            clock=self.clock):
            self.window.start_or_resume()
        self.result["steps"].append({"step": "translate_started", "ok": True})
        self._open_run_window(now)
        self._interact_until = now + self.seconds
        self._last_scroll = self._last_edit = self._last_page = now
        self._last_panel = now
        self.state = "interact"

    def _tick_interact(self, now: float) -> None:
        if now >= self._interact_until:
            self._phase("settle")
            self._settle_started = now
            self.state = "settle"
            return
        # Close the run window as soon as the task is seen finished, but keep
        # interacting: the remaining interaction *is* the I phase.
        if self._run_open and now - self._last_settle_poll >= SETTLE_POLL_S:
            self._last_settle_poll = now
            if self._instrumented(lambda: _job_settled(self.adapter, self.job_id)):
                self._close_run_window(now)
        phase = self.measurement_phase
        workbench = self.window.workbench
        table = workbench.segment_list
        bar = table.verticalScrollBar()
        if now - self._last_scroll >= 0.1 and bar.maximum() > 0:
            self._last_scroll = now
            self._phase("scroll")
            step = max(bar.maximum() // 12, 1)
            with OperationTimer(self.operations, "scroll", phase=phase, clock=self.clock):
                bar.setValue(0 if bar.value() + step > bar.maximum()
                             else bar.value() + step)
            self.scrolls += 1
        if now - self._last_edit >= 2.0 and workbench.visible_segment_count():
            self._last_edit = now
            self._phase("edit")
            index = self.edited % max(1, workbench.visible_segment_count())
            try:
                with OperationTimer(self.operations, "select_segment", phase=phase,
                                    clock=self.clock):
                    selected = workbench.select_segment(index)
                if selected:
                    with OperationTimer(self.operations, "type_echo", phase=phase,
                                        clock=self.clock):
                        workbench.set_editor_text_for_test(f"基线场景人工修改译文 {index}")
                    with OperationTimer(self.operations, "submit_edit", phase=phase,
                                        clock=self.clock):
                        workbench.flush_draft()
                        self.window.submit_edit(index)
                    self.edited += 1
            except Exception as exc:  # noqa: BLE001 - recorded, run continues
                self.result["failures"].append(f"edit({index}): {type(exc).__name__}: {exc}")
        if now - self._last_page >= 4.0:
            self._last_page = now
            self._phase("page")
            with OperationTimer(self.operations, "page_switch", phase=phase,
                                clock=self.clock):
                workbench.show_page(self._page_ids[self.pages % len(self._page_ids)])
            self.pages += 1
        if now - self._last_panel >= 6.0:
            self._last_panel = now
            kind = self._panel_kinds[(self.panels + self.pages) % len(self._panel_kinds)]
            name = "close_panel" if self._panel_open else "open_panel"
            self._phase(name)
            try:
                with OperationTimer(self.operations, name, phase=phase, clock=self.clock):
                    self._toggle_panel(workbench, kind, not self._panel_open)
                if name == "open_panel":
                    self.operations.note_variant("open_panel", kind)
                self._panel_open = not self._panel_open
                self.panels += 1
            except Exception as exc:  # noqa: BLE001 - recorded, run continues
                self.result["failures"].append(f"panel({kind}): {type(exc).__name__}: {exc}")

    def _tick_settle(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        if self._instrumented(lambda: _job_settled(self.adapter, self.job_id)):
            self._close_run_window(now)
            self._finish(settled=True)
            return
        if now - self._settle_started > self.settle_timeout:
            self._close_run_window(now)
            self._finish(settled=False)

    @staticmethod
    def _toggle_panel(workbench, kind: str, open_: bool) -> None:
        """Open/close one of the two popup surfaces the plan budgets as one op."""
        if kind == "activity_popover":
            if open_:
                workbench.open_activity()
                return
            # components.Popover is a QWidget: closing it goes through closeEvent
            # (which honours the reduce-motion switch, so offscreen closes now).
            popover = getattr(workbench, "activity_popover", None)
            if popover is not None:
                popover.close()
            return
        workbench.set_followup_drawer_open(open_)

    # -- phase windows -----------------------------------------------------
    def _checkpoint(self) -> dict | None:
        if self.jank is None or not hasattr(self.jank, "checkpoint"):
            return None
        return self.jank.checkpoint()

    def _window_since(self, checkpoint) -> dict | None:
        if checkpoint is None or self.jank is None:
            return None
        summary = getattr(self.jank, "summary_since", None)
        if summary is None:
            return None
        return _normalize_window(summary(checkpoint))

    def _open_run_window(self, now: float) -> None:
        self._run_checkpoint = self._checkpoint()
        self.run_started_at = now
        self._run_open = True
        self._last_settle_poll = now

    def _close_run_window(self, now: float) -> None:
        """Freeze the run window; the interaction continues into the I phase."""
        if not self._run_open:
            return
        self._run_open = False
        self.run_observed_s = max(0.0, now - float(self.run_started_at or now))
        self.translation_finished_at = now
        window = self._window_since(self._run_checkpoint)
        if window is not None:
            self.run_window = window
        self.result["steps"].append({
            "step": "run_window_closed",
            "ok": True,
            "observed_s": round(self.run_observed_s, 3),
            "measured": bool(self.run_window and self.run_window.get("measured")),
        })

    # -- helpers -----------------------------------------------------------
    def _predicate_due(self, now: float) -> bool:
        if self._last_predicate >= 0.0 and now - self._last_predicate < _PREDICATE_INTERVAL_S:
            return False
        self._last_predicate = now
        return True

    def _instrumented(self, call):
        """Run harness-only work (predicate polling) under the sampler's mark."""
        if self.jank is None:
            return call()
        with self.jank.instrumentation():
            return call()

    def _phase(self, name: str) -> None:
        if self.jank is not None:
            self.jank.set_phase(name)

    def _fail(self, message: str) -> None:
        self.result["failures"].append(message)
        self._finish(settled=False)

    def _finish(self, *, settled: bool) -> None:
        if self._run_open:
            # Never leave the run window dangling: an aborted scenario still has
            # a measured run window, it is just shorter.
            self._close_run_window(self.clock())
        self.settled = bool(settled) and "settle" in (self.state, "done")
        self.state = "done"
        self.finished = True
        self.app.quit()


class _TaskSwitchDriver:
    """Drive S3: open a finished large task and switch between tasks.

    The finished tasks do not exist when the run starts, so this driver builds
    them first — import, then translate to completion against the mock gateway —
    and takes the measurement checkpoint *afterwards*.  The measured window
    therefore begins with both tasks already complete, which is what S3 is for:
    opening a finished 10,000-segment task and switching between tasks.  The
    build is reported (its duration and outcome) but it is not part of any rate.

    S3 has no translating window, so R is not applicable and I covers the whole
    measured window; the report says so instead of inventing a run window.
    """

    def __init__(self, *, app, window, adapter, jank, fixtures, seconds,
                 settle_timeout, result, operations=None, clock=time.monotonic):
        self.app = app
        self.window = window
        self.adapter = adapter
        self.jank = jank
        self.fixtures = [str(item) for item in fixtures]
        self.seconds = max(1.0, float(seconds))
        self.settle_timeout = max(1.0, float(settle_timeout))
        self.build_timeout = max(self.settle_timeout, 900.0)
        self.result = result
        self.clock = clock
        self.operations = operations if operations is not None else OperationRecorder()
        self.state = "created"
        self.finished = False
        self.job_ids: list[str] = []
        self.big_job_id = ""
        self.small_job_id = ""
        self.cycles = 0
        self.scenario_window: dict | None = None
        self.measure_observed_s: float | None = None
        self._known: set[str] = set()
        self._import_index = 0
        self._build_index = 0
        self._measure_checkpoint: dict | None = None
        self._measure_started = 0.0
        self._measure_until = 0.0
        self._visit_started = 0.0
        self._open_deadline = 0.0
        self._settle_started = 0.0
        self._last_predicate = -1.0
        self._targets: list[str] = []
        self._target_index = 0
        self._panel_open = False
        self._page_index = 0
        self._page_ids = ("translate", "review", "deliver")
        self._panel_kinds = ("activity_popover", "followup_drawer")

    # -- event-loop callbacks ---------------------------------------------
    def begin(self) -> None:
        self.state = "import"
        self.result["steps"].append({"step": "import_dispatched", "ok": True,
                                     "fixtures": len(self.fixtures)})
        self._known = self._instrumented(
            lambda: {row.job_id for row in self.adapter.list_jobs()})
        self._dispatch_import()

    def tick(self) -> bool:
        if self.finished:
            return True
        try:
            getattr(self, "_tick_" + self.state)(self.clock())
        except Exception as exc:  # noqa: BLE001 - recorded in the report
            self.result["failures"].append(f"{self.state}: {type(exc).__name__}: {exc}")
            self._finish(measured=False)
        return self.finished

    def _dispatch_import(self) -> None:
        fixture = self.fixtures[self._import_index]
        self._import_started = self.clock()
        with OperationTimer(self.operations, "import", phase="setup", clock=self.clock):
            self.window.import_paths([fixture], autostart=False)

    # -- states ------------------------------------------------------------
    def _tick_import(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        rows = self._instrumented(lambda: list(self.adapter.list_jobs()))
        fresh = [row for row in rows if row.job_id not in self._known]
        if not fresh:
            if now - self._import_started > min(180.0, self.build_timeout):
                self._fail("import did not produce a task")
            return
        new_id = fresh[0].job_id
        self._known.add(new_id)
        self.job_ids.append(new_id)
        self.result["steps"].append({"step": "imported", "ok": True,
                                     "job_id": new_id,
                                     "index": self._import_index})
        self._import_index += 1
        if self._import_index < len(self.fixtures):
            self._dispatch_import()
            return
        # Largest first: the plan's S3 is "a finished 10,000-segment task".
        self.big_job_id = self.job_ids[0]
        self.small_job_id = self.job_ids[-1] if len(self.job_ids) > 1 else ""
        self.result["job_id"] = self.big_job_id
        self._start_build(now)

    def _start_build(self, now: float) -> None:
        self._build_target = self.job_ids[self._build_index]
        self._open_span_start = now
        self._open_deadline = now + min(120.0, self.build_timeout)
        self.window.open_job(self._build_target)
        self.state = "build_open"

    def _tick_build_open(self, now: float) -> None:
        if getattr(self.window, "_opening_job_id", ""):
            if now > self._open_deadline:
                self._fail("task did not open during the build phase")
            return
        if getattr(self.window, "_current_job_id", "") != self._build_target:
            self._fail("task did not open during the build phase")
            return
        self.operations.add("open_task", "setup", (now - self._open_span_start) * 1000.0)
        with OperationTimer(self.operations, "start_translation", phase="setup",
                            clock=self.clock):
            self.window.start_or_resume()
        self._settle_started = now
        self.state = "build_run"

    def _tick_build_run(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        if self._instrumented(lambda: _job_settled(self.adapter, self._build_target)):
            self._build_index += 1
            self.result["steps"].append({"step": "built", "ok": True,
                                         "job_id": self._build_target,
                                         "seconds": round(now - self._settle_started, 2)})
            if self._build_index < len(self.job_ids):
                self._start_build(now)
                return
            self._start_measurement(now)
            return
        if now - self._settle_started > self.build_timeout:
            self._fail("the task did not finish translating inside the build budget")

    # -- measured phase ----------------------------------------------------
    def _start_measurement(self, now: float) -> None:
        """Take the measurement checkpoint *after* the tasks are finished."""
        self._measure_checkpoint = self._checkpoint()
        self._measure_started = now
        if not self._measure_checkpoint:
            self.result["failures"].append(
                "no stall checkpoint: the sampler did not start, so S3 cannot be measured")
            self._finish(measured=False)
            return
        self._measure_until = now + max(self.seconds, I_PHASE_MIN_SECONDS + 1.0)
        targets = [self.big_job_id]
        if self.small_job_id:
            targets.append(self.small_job_id)
        self._targets = targets
        self._target_index = 0
        self.result["steps"].append({
            "step": "measure_started", "ok": True,
            "big_job_id": self.big_job_id,
            "small_job_id": self.small_job_id,
            "until_s": round(self._measure_until - now, 2),
        })
        self.state = "visit"
        self._visit_started = now

    def _tick_visit(self, now: float) -> None:
        target = self._targets[self._target_index % len(self._targets)]
        self._visit_started = now
        self._open_deadline = now + min(120.0, self.settle_timeout)
        self._visit_target = target
        self._visit_switched = bool(self.job_ids and target != self.job_ids[0])
        self.window.open_job(target)
        self.state = "visit_open"

    def _tick_visit_open(self, now: float) -> None:
        if getattr(self.window, "_opening_job_id", ""):
            if now > self._open_deadline:
                self._fail("task did not open during the measured phase")
            return
        if getattr(self.window, "_current_job_id", "") != self._visit_target:
            self._fail("task did not open during the measured phase")
            return
        name = "switch_task" if self._visit_switched else "open_task"
        self.operations.add(name, "complete", (now - self._visit_started) * 1000.0)
        self.cycles += 1
        self.result["steps"].append({"step": "opened", "ok": True,
                                     "job_id": self._visit_target,
                                     "switched": self._visit_switched,
                                     "cycle": self.cycles})
        self._run_operation_burst()
        self._target_index += 1
        if now >= self._measure_until:
            self._close_measurement(now)
            return
        self.state = "visit"

    def _run_operation_burst(self) -> None:
        """One sample of each core operation on the task that is now open."""
        workbench = self.window.workbench
        phase = "complete"
        table = workbench.segment_list
        bar = table.verticalScrollBar()
        if bar.maximum() > 0:
            step = max(bar.maximum() // 12, 1)
            with OperationTimer(self.operations, "scroll", phase=phase, clock=self.clock):
                bar.setValue(0 if bar.value() + step > bar.maximum()
                             else bar.value() + step)
        visible = workbench.visible_segment_count()
        if visible:
            index = self.cycles % visible
            with OperationTimer(self.operations, "select_segment", phase=phase,
                                clock=self.clock):
                selected = workbench.select_segment(index)
            if selected:
                with OperationTimer(self.operations, "type_echo", phase=phase,
                                    clock=self.clock):
                    workbench.set_editor_text_for_test(f"完成后阶段人工修改译文 {index}")
        with OperationTimer(self.operations, "page_switch", phase=phase, clock=self.clock):
            workbench.show_page(self._page_ids[self._page_index % len(self._page_ids)])
        self._page_index += 1
        kind = self._panel_kinds[self._page_index % len(self._panel_kinds)]
        name = "close_panel" if self._panel_open else "open_panel"
        with OperationTimer(self.operations, name, phase=phase, clock=self.clock):
            self._toggle_panel(workbench, kind, not self._panel_open)
        if name == "open_panel":
            self.operations.note_variant("open_panel", kind)
        self._panel_open = not self._panel_open

    def _close_measurement(self, now: float) -> None:
        self.measure_observed_s = max(0.0, now - self._measure_started)
        window = self._window_since(self._measure_checkpoint)
        if window is not None:
            self.scenario_window = window
        self.result["steps"].append({
            "step": "measure_closed", "ok": True,
            "observed_s": round(self.measure_observed_s, 3),
            "measured": bool(self.scenario_window and self.scenario_window.get("measured")),
        })
        self._finish(measured=True)

    # -- plumbing ----------------------------------------------------------
    _toggle_panel = staticmethod(_ScenarioDriver._toggle_panel)

    def _checkpoint(self) -> dict | None:
        if self.jank is None or not hasattr(self.jank, "checkpoint"):
            return None
        return self.jank.checkpoint()

    def _window_since(self, checkpoint) -> dict | None:
        if checkpoint is None or self.jank is None:
            return None
        summary = getattr(self.jank, "summary_since", None)
        if summary is None:
            return None
        return _normalize_window(summary(checkpoint))

    def _predicate_due(self, now: float) -> bool:
        if self._last_predicate >= 0.0 and now - self._last_predicate < _PREDICATE_INTERVAL_S:
            return False
        self._last_predicate = now
        return True

    def _instrumented(self, call):
        if self.jank is None:
            return call()
        with self.jank.instrumentation():
            return call()

    def _fail(self, message: str) -> None:
        self.result["failures"].append(message)
        self._finish(measured=False)

    def _finish(self, *, measured: bool) -> None:
        if self.state != "done" and self._measure_checkpoint is not None \
                and self.scenario_window is None:
            self._close_measurement(self.clock())
            return
        self.state = "done"
        self.finished = True
        self.result["measurement_completed"] = bool(measured)
        self.app.quit()


def load_engine_config(path: Path):
    """Build the real engine configuration from a provider config file (#327).

    The file is read in memory only: the API key is never copied into the
    report, the isolated data directory or any log.  The shape matches what
    ``EngineConfig.load()`` reads from the app's own data dir, so the client
    talks to the real endpoint with the user's real settings.
    """
    from folith.qt import adapter
    from folith.model_connections import effective_config

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not (raw.get("provider") or raw.get("connections")):
        raise RuntimeError(f"not a provider config: {path}")
    config = adapter.EngineConfig(
        provider=str(raw.get("provider") or ""),
        model=str(raw.get("model") or ""),
        api_key=str(raw.get("api_key") or ""),
        base_url=str(raw.get("base_url") or ""),
        reasoning_effort=str(raw.get("reasoning_effort") or ""),
    )
    reviewer = raw.get("reviewer") or {}
    if isinstance(reviewer, dict) and reviewer.get("provider"):
        config.reviewer_mode = "separate"
        config.reviewer_provider = str(reviewer.get("provider") or "")
        config.reviewer_model = str(reviewer.get("model") or "")
        config.reviewer_api_key = str(reviewer.get("api_key") or "")
        config.reviewer_base_url = str(reviewer.get("base_url") or "")
    config.connections = list(raw.get("connections") or [])
    config.connection_roles = dict(raw.get("connection_roles") or {})
    config.connection_models = dict(raw.get("connection_models") or {})
    config.ensure_connections()
    return effective_config(config, config.connections, config.connection_roles)


def _run_scenario_event_loop(app) -> None:
    """Run the application's own event loop for the whole scenario (#327).

    A stall captured while Qt dispatches natively (paint, layout, plugin work,
    with no Python frame below) shows this frame as its innermost Python frame.
    That is the *application's* event loop, not the harness pumping events: the
    shipping client calls the same ``app.exec()`` from ``folith/qt/app.py``.
    The frame name is kept explicit so a baseline report does not mistake it for
    the old ``processEvents`` pump.
    """
    app.exec()


def _prepare_window(args: argparse.Namespace, data_dir: Path, log_dir: Path,
                    result: dict):
    """App + main window + UI health monitor for a source-mode scenario.

    Shared by S1 and S3 so both measure the same shipping window.  The engine
    configuration is the only branch: the mock gateway (default) or the user's
    real endpoint, read in memory and never written into the report.
    """
    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=log_dir)
    from folith.qt import adapter, selftest
    from folith.qt import window as window_module
    from folith.qt.ui_health import UiHealthMonitor

    app = window_module.build_application([sys.argv[0]])
    if args.engine_config:
        config = load_engine_config(Path(args.engine_config).expanduser())
        problem = config.connection_problem()
        if problem:
            result["failures"].append(f"real engine not usable: {problem}")
        from urllib.parse import urlparse

        result["real_engine"] = {
            "provider": config.provider,
            "model": config.model,
            "endpoint_host": urlparse(config.base_url or "").netloc,
            "reviewer_mode": config.reviewer_mode,
            "connection_problem": problem,
            "api_key_loaded": bool(config.api_key),
        }
    else:
        config = selftest.engine_from_env(adapter, adapter.EngineConfig.load())
    window = window_module.MainWindow(config, paths, None)
    window.show()
    # A foreground session: the user is looking at and interacting with this
    # window.  Raising it keeps the measured session comparable across runs (and
    # out of background timer-throttling), and uses only Qt's cross-platform API.
    window.raise_()
    window.activateWindow()

    monitor = UiHealthMonitor(app, window, paths, identity=adapter.app_identity())
    try:
        monitor.start()
        result["ui_health"] = {"available": True, "timeout_seconds": monitor.timeout,
                               "directory": str(monitor.directory)}
    except OSError as exc:
        result["ui_health"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    jank = monitor.jank
    if jank is None:
        result["failures"].append("stall sampler was not created")
    return paths, app, window, adapter, monitor, jank


def _drive_event_loop(app, driver, result: dict) -> None:
    """Run the scenario inside the application's own event loop (#327).

    ``driver.begin`` is a zero-delay single-shot and ``driver.tick`` is a
    repeating timer: steps are event-loop callbacks, never a processEvents pump.
    """
    from PySide6.QtCore import QTimer

    timer = QTimer(app)
    timer.setInterval(_TICK_MS)

    def on_tick() -> None:
        if driver.tick():
            timer.stop()

    timer.timeout.connect(on_tick)
    safety = QTimer(app)
    safety.setSingleShot(True)
    safety.timeout.connect(app.quit)
    budget = getattr(driver, "seconds", 60.0) + getattr(driver, "settle_timeout", 300.0)
    if isinstance(driver, _TaskSwitchDriver):
        # S3 spends its setup translating two tasks before the measured window.
        budget += driver.build_timeout
    safety.start(int((budget + 120.0) * 1000))
    try:
        QTimer.singleShot(0, driver.begin)
        timer.start()
        # The scenario runs in the application's event loop, as callbacks — no
        # processEvents pump from script code.
        _run_scenario_event_loop(app)
    finally:
        timer.stop()
        safety.stop()
    if not driver.finished:
        result["failures"].append("scenario did not finish (event loop ended early)")


def run_source(args: argparse.Namespace, fixture: Path, data_dir: Path,
               log_dir: Path, base_url: str) -> dict:
    """S1 on the shared Qt window, driven from the event loop (#327).

    The scenario runs inside ``app.exec()``; ``_ScenarioDriver`` steps are Qt
    timer callbacks.  This function never pumps events itself, so a stall is
    never attributed to the harness's own ``processEvents()`` frame.

    Measurement v3: the scenario window (from the checkpoint taken before the
    import) and the run window (translation start → settled) are both collected,
    so R and I come from one run.  The product's own run record is kept next to
    them as a cross-check, never substituted silently.
    """
    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": []}
    paths, app, window, adapter, monitor, jank = _prepare_window(
        args, data_dir, log_dir, result)
    checkpoint = jank.checkpoint() if jank is not None else None
    operations = OperationRecorder()

    started = time.monotonic()
    driver = _ScenarioDriver(app=app, window=window, adapter=adapter, jank=jank,
                             fixture=fixture, seconds=args.seconds,
                             settle_timeout=args.settle_timeout, result=result,
                             operations=operations)
    try:
        _drive_event_loop(app, driver, result)
        if not driver.finished:
            result["failures"].append("scenario did not finish (event loop ended early)")
        job_id = driver.job_id
        state = (adapter.load_state(job_id) or {}) if job_id else {}
        result["translation"] = {
            "settled": driver.settled,
            "runtime_status": ((adapter.core.load_runtime_state(job_id) or {}).get("status")
                               if job_id else ""),
            "p2_done": bool(state.get("p2_done")),
            "segments": len(state.get("pairs") or []),
            "edited_segments": driver.edited,
            "page_switches": driver.pages,
            "scroll_events": driver.scrolls,
            "panel_events": driver.panels,
        }
        if job_id:
            result["run_records"] = read_run_records(paths.data_dir, job_id)
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        result["operations"] = operations.report()
        result["run_window"] = driver.run_window or {
            "measured": False,
            "reason": ("no translating window was observed: the scenario never "
                       "reached a started translation"),
        }
        if driver.run_observed_s is not None:
            result["run_window"]["runner_observed_s"] = round(driver.run_observed_s, 3)
        if jank is not None and checkpoint is not None:
            result["gui_jank"] = _normalize_window(jank.summary_since(checkpoint))
            result["gui_jank_process"] = _normalize_window(jank.summary())
        else:
            result["gui_jank"] = {
                "measured": False,
                "reason": "the UI health monitor did not start, so no stall "
                          "window exists for this run",
            }
            result["gui_jank_process"] = _normalize_window({})
        monitor.stop()
        _teardown(app, window)
    return result


def run_source_s3(args: argparse.Namespace, fixtures: list[Path], data_dir: Path,
                  log_dir: Path, base_url: str) -> dict:
    """S3: open a finished large task and switch tasks (plan §9 D1).

    The tasks are built inside the run (import + translate against the mock
    gateway) and the measurement window starts afterwards, so the numbers
    describe a user opening and switching between *finished* tasks.  There is no
    translating window, so ``run_window`` is reported as not applicable and I
    covers the whole measured window.
    """
    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": []}
    paths, app, window, adapter, monitor, jank = _prepare_window(
        args, data_dir, log_dir, result)
    operations = OperationRecorder()

    started = time.monotonic()
    driver = _TaskSwitchDriver(app=app, window=window, adapter=adapter, jank=jank,
                               fixtures=fixtures, seconds=args.seconds,
                               settle_timeout=args.settle_timeout, result=result,
                               operations=operations)
    try:
        _drive_event_loop(app, driver, result)
        result["translation"] = {
            "settled": bool(getattr(driver, "scenario_window", None))
                        and bool((driver.scenario_window or {}).get("measured")),
            "mode": "s3_finished_tasks",
            "cycles": driver.cycles,
            "big_job_id": driver.big_job_id,
            "small_job_id": driver.small_job_id,
            "build_seconds": round((driver._measure_started - started), 2)
                             if driver._measure_started else None,
            "measure_seconds": round(driver.measure_observed_s or 0.0, 2),
        }
        if driver.big_job_id:
            result["run_records"] = read_run_records(paths.data_dir, driver.big_job_id)
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        result["operations"] = operations.report()
        result["run_window"] = {
            "measured": False,
            "present": False,
            "reason": ("S3 has no translating phase: its tasks are already finished, "
                       "so R does not apply and I covers the whole measured window"),
        }
        if driver.scenario_window is not None:
            result["gui_jank"] = driver.scenario_window
        else:
            result["gui_jank"] = _normalize_window({})
        result["gui_jank_process"] = (_normalize_window(jank.summary())
                                      if jank is not None else _normalize_window({}))
        monitor.stop()
        _teardown(app, window)
    return result


def _teardown(app, window) -> None:
    try:
        window.timer.stop()
    except Exception:  # noqa: BLE001 - best effort shutdown
        pass
    app.processEvents()
    try:
        window.close()
    except Exception:  # noqa: BLE001 - a failing close must not hide the report
        pass
    app.processEvents()


def run_frozen(args: argparse.Namespace, fixture: Path, data_dir: Path,
               log_dir: Path, base_url: str) -> dict:
    """The real baseline path: the frozen app runs its own scripted flow."""
    result: dict = {"mode": "frozen_selftest", "steps": [], "failures": []}
    report_path = args.out / "selftest.json"
    env = scenario_environment(base_url, data_dir, log_dir)
    env["QT_QPA_PLATFORM"] = "offscreen"
    command = [str(Path(args.frozen_app).expanduser()),
               "--data-dir", str(data_dir), "--log-dir", str(log_dir),
               "--selftest", "--selftest-source", str(fixture),
               "--result", str(report_path)]
    started = time.monotonic()
    run = subprocess.run(command, env=env, cwd=str(args.out), capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=args.timeout)
    result["wall_time_s"] = round(time.monotonic() - started, 2)
    result["exit_code"] = run.returncode
    report: dict = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            result["failures"].append("selftest report was not valid JSON")
    result["steps"].append({"step": "selftest", "ok": bool(report.get("ok"))})
    if run.returncode != 0:
        result["failures"].append("frozen app exited with a non-zero status")
        result["stdout_tail"] = run.stdout[-800:]
        result["stderr_tail"] = run.stderr[-800:]
    job_ids = [str(item) for item in (report.get("job_ids") or []) if item]
    result["job_ids"] = job_ids
    result["run_records"] = []
    for job_id in job_ids:
        result["run_records"].extend(read_run_records(data_dir, job_id))
    result["translation"] = {
        "settled": bool((report.get("run") or {}).get("p2_done")),
        "segments": (report.get("run") or {}).get("segments"),
        "edited_segments": None,
    }
    # The selftest entry point now starts the UI health monitor before the flow,
    # so its run records carry a measured window; if a build predates that, the
    # report says "not measured" instead of implying zero stalls.
    result["gui_jank"] = _stall_window_from_records(result["run_records"])
    # v3: the frozen path cannot take harness checkpoints (the app is a
    # subprocess), but the app measured its own translating window, so R is still
    # computable and this is not a silently missing phase.
    result["run_window"] = _run_window_from_records(result["run_records"])
    result["operations"] = OperationRecorder().report()
    return result


def write_report(args: argparse.Namespace, payload: dict) -> Path:
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "runtime-baseline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def assemble_report(*, scenario: str, paras: int, fixtures: list[Path],
                    result: dict, environment: dict, started_at: str) -> dict:
    """Build the report from one run's collected evidence.

    Kept separate from :func:`main` so the field contract — ``measurement.version``,
    the R/I phase blocks, per-operation percentiles and the "not measured"
    markers — is testable without a Qt window (P0-6).
    """
    scenario_window = _normalize_window(result.get("gui_jank") or {})
    run_window = result.get("run_window") or {}
    if not isinstance(run_window, dict) or not run_window:
        run_window = {"measured": False,
                      "reason": "this mode did not collect a translating window"}
    elif "measured" not in run_window:
        run_window = _normalize_window(run_window)
    phases = _phase_metrics(scenario_window, run_window)
    operations = result.get("operations") or OperationRecorder().report()
    fixture_block = {"kind": "epub", "paragraphs": paras,
                     "path": str(fixtures[0]) if fixtures else ""}
    if len(fixtures) > 1:
        fixture_block["secondary"] = [str(item) for item in fixtures[1:]]
    payload = {
        "scenario": scenario,
        "implemented": True,
        "report_version": 1,
        # #347: which measurement definition produced these numbers.  A report
        # without this marker predates measurement v3; ``version: 2`` predates
        # the phase split, the per-operation latency and the tool-share numbers.
        "measurement": {
            "version": MEASUREMENT_VERSION,
            "driver": "qt_event_loop_callbacks",
            "instrumentation_split": True,
            "phases": {
                "R": "翻译中: the run window (translation start → task settled)",
                "I": "完成后: the scenario window minus the run window",
                "i_phase_min_seconds": I_PHASE_MIN_SECONDS,
                "void_rule": "a round whose I phase is shorter than "
                             "i_phase_min_seconds is void; the report counts it",
                "discarded_rounds": phases.get("discarded_rounds"),
            },
            "operations": {
                "unit": "ms",
                "core": sorted(CORE_OPERATIONS),
                "budgets_ms": {name: spec.get("budget_ms")
                               for name, spec in CORE_OPERATIONS.items()},
                "phases": list(OPERATION_PHASES),
                "missing": operations.get("missing") or [],
            },
            "tool_share": {
                "definition": "the sampler's instrumentation stalls (predicate "
                              "polling and other harness-only work) divided by all "
                              "stalls including them",
                "reported_as": "excluding_tool / including_tool per phase and tier",
            },
        },
        "started_at": started_at,
        "finished_at": now_iso(),
        "fixture": fixture_block,
        "environment": environment,
        "translation": result.get("translation") or {},
        "steps": result.get("steps") or [],
        "failures": result.get("failures") or [],
        "metrics": {
            # The scenario window is the whole measured session; the run window is
            # the translating part of it.  Both come from the same sampler, so the
            # phase subtraction is legal.
            "gui_jank": scenario_window,
            "gui_jank_process": result.get("gui_jank_process") or {
                "measured": False,
                "reason": "this mode did not collect a session-level stall view",
            },
            "run_window": run_window,
            # The product's own copy of the translating window, kept beside the
            # harness measurement as a cross-check (never a silent substitute).
            "run_record_window": _run_window_from_records(
                result.get("run_records") or []),
            "phases": phases,
            "operations": operations,
            "ui_health": result.get("ui_health") or {},
            "run_records": result.get("run_records") or [],
        },
        "limits": list(LIMITS),
        "real_machine_baseline": False,
        "note": "offscreen/mock numbers are labelled and cannot replace the "
                "macOS/Windows frozen-app baseline the issue asks for",
    }
    payload["ok"] = bool(
        (result.get("translation") or {}).get("settled")
        and not result.get("failures"))
    return payload


def _summary_line(payload: dict) -> dict:
    """The one-line stdout summary: the numbers a console reader needs first."""
    phases = (payload.get("metrics") or {}).get("phases") or {}
    run = (phases.get("R") or {})
    after = (phases.get("I") or {})
    line = {
        "ok": payload.get("ok"),
        "report": payload.get("report_path"),
        "scenario": payload.get("scenario"),
        "mode": (payload.get("environment") or {}).get("mode"),
        "measurement_version": (payload.get("measurement") or {}).get("version"),
        "gui_jank_measured": (payload.get("metrics") or {}).get("gui_jank", {}).get("measured"),
        "R_valid": run.get("valid"),
        "I_valid": after.get("valid"),
        "discarded_rounds": phases.get("discarded_rounds"),
    }
    for label, block in (("R", run), ("I", after)):
        tiers = block.get("tiers") or {}
        for tier_name, tier in tiers.items():
            line[f"{label}_{tier_name}_excluding_tool_per_min"] = (
                (tier.get("excluding_tool") or {}).get("per_minute"))
            line[f"{label}_{tier_name}_including_tool_per_min"] = (
                (tier.get("including_tool") or {}).get("per_minute"))
            line[f"{label}_{tier_name}_tool_share"] = tier.get("tool_share")
    return line


#: Thresholds for the aggregation view: two tiers, two tool readings, two phases.
_AGGREGATE_METRICS = tuple(
    f"{phase}_{tier}_{reading}_per_minute"
    for phase in ("R", "I")
    for tier in ("slow_100ms", "severe_500ms")
    for reading in ("excluding_tool", "including_tool")
)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(float(ordered[middle]), 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 2)


def aggregate_reports(directory: Path) -> dict:
    """Medians over the rounds in *directory* (plan: ≥3 rounds × ≥5 min).

    Voided rounds (I phase too short, or a window that was not measured) are
    excluded from the medians and counted, so a variant cannot look stable by
    silently dropping the rounds that did not fit the definition.
    """
    rows: list[dict] = []
    skipped: list[dict] = []
    for path in sorted(Path(directory).glob("*/runtime-baseline.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            skipped.append({"report": str(path), "reason": f"unreadable: {exc}"})
            continue
        phases = ((payload.get("metrics") or {}).get("phases") or {})
        if (payload.get("measurement") or {}).get("version") != MEASUREMENT_VERSION:
            skipped.append({"report": str(path),
                            "reason": "measurement version is not v3"})
            continue
        if not phases.get("valid_round"):
            skipped.append({"report": str(path),
                            "reason": "; ".join(phases.get("discard_reasons") or [])
                                      or "round was void"})
            continue
        rows.append({"report": str(path), "name": path.parent.name,
                     "scenario": payload.get("scenario"), "phases": phases})
    medians: dict[str, float | None] = {}
    samples: dict[str, list[float]] = {}
    for row in rows:
        for label in ("R", "I"):
            for tier in ("slow_100ms", "severe_500ms"):
                block = ((row["phases"].get(label) or {}).get("tiers") or {}).get(tier) or {}
                for reading in ("excluding_tool", "including_tool"):
                    value = (block.get(reading) or {}).get("per_minute")
                    key = f"{label}_{tier}_{reading}_per_minute"
                    if isinstance(value, (int, float)):
                        samples.setdefault(key, []).append(float(value))
    for key in _AGGREGATE_METRICS:
        medians[key] = _median(samples.get(key) or [])
    scenarios = sorted({str(row.get("scenario")) for row in rows})
    return {
        "directory": str(directory),
        "rounds": len(rows),
        "scenarios": scenarios,
        "mixed_scenarios": len(scenarios) > 1,
        "voided_rounds": len([item for item in skipped
                              if "void" in str(item.get("reason"))
                              or "minimum" in str(item.get("reason"))
                              or "measured" in str(item.get("reason"))]),
        "skipped": skipped,
        "medians": medians,
        "round_detail": [{"name": row["name"], "report": row["report"],
                          "scenario": row.get("scenario"),
                          "i_phase_s": row["phases"].get("i_phase_s")} for row in rows],
        "note": "medians are over the valid rounds only; a variant needs the same "
                "scenarios, the same machine and the same load to be comparable",
        "warning": ("the rounds under this directory mix scenarios "
                    f"({', '.join(scenarios)}): a median across them is not one "
                    "measurement" if len(scenarios) > 1 else None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Folith runtime baseline runner (S1/S3)")
    parser.add_argument("--out", default=None, help="output directory for this run")
    parser.add_argument("--scenario", default="S1", choices=("S1", "S2", "S3", "S4"))
    parser.add_argument("--paras", type=int, default=200, help="paragraph count (S1)")
    parser.add_argument("--seconds", type=float, default=90.0,
                        help="measured interaction window (translation keeps running)")
    parser.add_argument("--settle-timeout", type=float, default=300.0,
                        help="how long to wait for the task to settle after interacting")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="subprocess budget for the frozen path")
    parser.add_argument("--mode", default="source", choices=("source", "frozen"))
    parser.add_argument("--frozen-app", default=None,
                        help="frozen Folith executable (implies --mode frozen)")
    parser.add_argument("--offscreen", action="store_true",
                        help="force QT_QPA_PLATFORM=offscreen for the window")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--base-url", default=None,
                        help="reuse an already running mock gateway")
    parser.add_argument("--engine-config", default=None,
                        help="real provider config file, read in memory: runs the "
                             "scenario against the real endpoint instead of the mock "
                             "gateway (the key is never written into any output)")
    parser.add_argument("--s3-segments", type=int, default=10000,
                        help="segments of the finished task S3 opens and switches to")
    parser.add_argument("--s3-small-paras", type=int, default=40,
                        help="segments of the second (small) task S3 switches to")
    parser.add_argument("--aggregate", default=None,
                        help="do not measure: print medians over the rounds under "
                             "this directory (each round is */runtime-baseline.json)")
    args = parser.parse_args(argv)

    if args.aggregate:
        summary = aggregate_reports(Path(args.aggregate).expanduser().resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["rounds"] else 1

    if not args.out:
        parser.error("--out is required unless --aggregate is used")
    args.out = Path(args.out).expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.frozen_app:
        args.mode = "frozen"
    started_at = now_iso()
    if args.scenario in NOT_IMPLEMENTED:
        payload = {
            "scenario": args.scenario,
            "implemented": False,
            "reason": f"{args.scenario} is declared but not implemented in this round: "
                      f"{NOT_IMPLEMENTED[args.scenario]}",
            "measurement": {"version": MEASUREMENT_VERSION,
                            "discarded_rounds": 1,
                            "note": "not implemented: no round was measured"},
            "limits": list(LIMITS),
            "started_at": started_at,
            "finished_at": now_iso(),
        }
        path = write_report(args, payload)
        payload["report_path"] = str(path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else args.out / "data"
    log_dir = Path(args.log_dir).expanduser().resolve() if args.log_dir else args.out / "logs"
    for directory in (data_dir, log_dir):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)

    if args.scenario == "S3":
        segments = max(2, int(args.s3_segments))
        fixtures = [
            build_epub(args.out / "fixtures" / f"s3-{segments}p.epub", segments),
            build_epub(args.out / "fixtures" / f"s3-{max(1, args.s3_small_paras)}p.epub",
                       max(1, args.s3_small_paras)),
        ]
        paras = segments
    else:
        fixtures = [build_epub(args.out / "fixtures" / f"s1-{args.paras}p.epub", args.paras)]
        paras = args.paras
    gateway = None
    base_url = (args.base_url or "").strip()
    try:
        if args.engine_config:
            # Real endpoint: no mock gateway, and no selftest endpoint override.
            base_url = ""
        elif not base_url:
            gateway = MockGateway(args.out / "gateway")
            gateway.out_dir.mkdir(parents=True, exist_ok=True)
            base_url = gateway.start()
        env_before = dict(os.environ)
        os.environ.update({key: value for key, value in
                           scenario_environment(base_url, data_dir, log_dir).items()})
        try:
            if args.mode == "frozen":
                result = run_frozen(args, fixtures[0], data_dir, log_dir, base_url)
            elif args.scenario == "S3":
                result = run_source_s3(args, fixtures, data_dir, log_dir, base_url)
            else:
                result = run_source(args, fixtures[0], data_dir, log_dir, base_url)
        finally:
            os.environ.clear()
            os.environ.update(env_before)
    finally:
        if gateway is not None:
            gateway.stop()

    environment = {
        "mode": result.get("mode"),
        "offscreen": os.environ.get("QT_QPA_PLATFORM") == "offscreen",
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "frozen_app": args.frozen_app,
        "base_url_is_mock": gateway is not None,
        "real_engine": result.get("real_engine") or None,
        "data_dir": str(data_dir),
    }
    payload = assemble_report(scenario=args.scenario, paras=paras, fixtures=fixtures,
                              result=result, environment=environment,
                              started_at=started_at)
    path = write_report(args, payload)
    payload["report_path"] = str(path)
    print(json.dumps(_summary_line(payload), ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
