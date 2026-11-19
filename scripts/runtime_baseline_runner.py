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
* S4 — eight-hour endurance run against the local mock gateway (#455).

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

    # frozen app: flow control only.  It runs the app's own --selftest flow, so
    # --seconds does not apply (it is refused rather than ignored) and its
    # tool_share is null, not 0 — see TOOL_SPLIT_UNAVAILABLE.
    venv/bin/python scripts/runtime_baseline_runner.py --out /tmp/baseline-frozen \
        --paras 10000 --mode frozen \
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
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.trace import compute_percentiles  # noqa: E402 - needs ROOT on sys.path

NOT_IMPLEMENTED = {
    "S2": "long scanned PDF: paging/thumbnails while OCR runs",
}

#: The measurement definition implemented by this file.
#: v1: the harness pumped the GUI thread (its own frames were the biggest stall).
#: v2: steps run from Qt event-loop callbacks.
#: v3: phases (R/I), per-operation latency, S3, and the tool's own share.
MEASUREMENT_VERSION = 3

#: Interaction window for the source modes when ``--seconds`` is not given.
DEFAULT_SECONDS = 90.0

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


def limits_for(offscreen: bool) -> list[str]:
    """The limits that actually apply to *this* run's window.

    ``LIMITS`` describes an offscreen QPA.  A run in a real window does not have
    those limits and does have a different one (no frozen product), so printing
    the offscreen list there would describe a run that was not performed.
    """
    if offscreen:
        return list(LIMITS)
    return [
        "source run in a real window: not a frozen product (no .app bundle or DMG)",
        "this harness drives the window from Python; a real user's input timing differs",
        "one machine per run: a baseline needs the same scenarios on macOS and Windows",
        "per-operation latency is the synchronous GUI-thread span of the call; work a "
        "later paint/timer does is outside it",
        "the broadcast of rendered frames is not measured; frame pacing is approximate",
    ]


def motion_state() -> dict:
    """What the animation switch was, in the same order the product reads it.

    ``folith.qt.motion.reduce_motion`` prefers ``FOLITH_REDUCE_MOTION``, then
    treats offscreen/non-interactive QPA as "reduced", then asks the system.
    The system branch cannot be read from here, so it is reported as unknown
    rather than assumed either way.
    """
    raw = (os.environ.get("FOLITH_REDUCE_MOTION") or "").strip()
    if raw:
        reduced = raw.lower() not in ("0", "false", "no")
        source = "FOLITH_REDUCE_MOTION"
    elif os.environ.get("FOLITH_QT_NONINTERACTIVE") == "1" \
            or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        reduced = True
        source = "forced by the offscreen/non-interactive QPA"
    else:
        return {"reduce_motion": None, "motion_enabled": None, "env_value": None,
                "source": "system setting: this harness cannot read it from here"}
    return {"reduce_motion": reduced, "motion_enabled": not reduced,
            "env_value": raw, "source": source}


def note_for(offscreen: bool) -> str:
    if offscreen:
        return ("offscreen/mock numbers are labelled and cannot replace the "
                "macOS/Windows frozen-app baseline the issue asks for")
    return ("source run in a real window; still not a frozen product, so it cannot "
            "replace the frozen-app baseline the issue asks for")



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_epub(path: Path, paragraphs: int, salt: str = "") -> Path:
    """Deterministic EPUB with *paragraphs* segments (no random content).

    ``salt`` marks a document as a distinct one: the endurance loop (S4) imports
    a document per cycle, and the product de-duplicates identical imports, so the
    same bytes would hand back the same task instead of starting a new cycle.
    The salt only lands in the first paragraph, so the workload stays comparable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"<p>第 {index + 1} 段：基线场景用于在同一份文档上重复测量主线程响应。"
        f"The runtime baseline keeps the workload identical across runs "
        f"(segment {index + 1}).{' ' + salt if index == 0 and salt else ''}</p>"
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


TOOL_SPLIT_UNAVAILABLE = (
    "this mode does not run the harness's instrumentation marks, so the "
    "\"including tool\" view and the tool's share are not measured here: the "
    "window only carries the app's own counters, and reporting 0 would claim "
    "\"the tool added nothing\" instead of \"this path cannot separate it\"")


def _tier_block(window: dict, seconds: float | None, *, tier: str,
                tool_split_measured: bool = True) -> dict:
    """Both readings of one tier plus the tool's share of it.

    ``tool_split_measured=False`` (the frozen path) reports the including-tool
    view and the share as ``null`` with a reason.  The frozen arm drives the app
    through its own scripted flow, which never takes an instrumentation mark, so
    a ``0.0`` share there is not a measurement — P1-2 measured 25 % and 100 % of
    its stalls coming from the probe in the source arm, so silently reading the
    frozen ``0.0`` as "pure product" would invert the comparison.
    """
    if tier == "slow_100ms":
        product = int(window.get("slow_count") or 0)
        tool = int(window.get("instrumentation_slow_count") or 0)
    else:
        product = int(window.get("severe_count") or 0)
        tool = int(window.get("instrumentation_severe_count") or 0)
    total = product + tool
    block = {
        "excluding_tool": {"count": product, "per_minute": _rate_per_minute(product, seconds)},
        "tool_split_measured": bool(tool_split_measured),
    }
    if not tool_split_measured:
        block["including_tool"] = {"count": None, "per_minute": None,
                                  "reason": TOOL_SPLIT_UNAVAILABLE}
        block["tool_events"] = None
        block["tool_share"] = None
        block["tool_share_reason"] = TOOL_SPLIT_UNAVAILABLE
        return block
    block.update({
        "including_tool": {"count": total, "per_minute": _rate_per_minute(total, seconds)},
        "tool_events": tool,
        "tool_share": round(tool / total, 4) if total else None,
    })
    return block


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


class _R_OnlyAssembled(Exception):
    """Control-flow marker: an R-only round has already built its translation
    block, so the settling branch below must not overwrite it."""


def _tile_finished_state(state: dict, copies: int) -> dict:
    """Copy a *finished* task's state ``copies`` times, renumbering positions.

    Plan D1 needs a finished 10,000-segment task.  The whole-document projections
    (review queue, delivery gate, terms) are what the I phase costs, so a task
    holding only translations would measure a lighter document: the template's
    findings, review evidence and aggregates are tiled at the same density.
    """
    import copy as _copy

    tiled = _copy.deepcopy(state)
    base_paras = list(state.get("paras") or [])
    base_pairs = list(state.get("pairs") or [])
    base_findings = list(state.get("findings") or [])
    base_evidence = list(state.get("review_evidence") or [])
    base_actions = list(state.get("human_actions") or [])
    size = len(base_pairs)
    if size <= 0 or copies <= 1:
        return tiled
    max_batch = max([int(item.get("batch") or 0) for item in base_evidence] or [0]) + 1

    paras, pairs = list(base_paras), list(base_pairs)
    findings, evidence, actions = (list(base_findings), list(base_evidence),
                                   list(base_actions))
    for copy_index in range(1, copies):
        offset = copy_index * size
        paras.extend(_copy.deepcopy(base_paras))
        pairs.extend(_copy.deepcopy(base_pairs))
        for item in base_findings:
            item2 = _copy.deepcopy(item)
            for key in ("segment_index", "segment_id"):
                if isinstance(item2.get(key), int):
                    item2[key] = item2[key] + offset
            findings.append(item2)
        for item in base_evidence:
            item2 = _copy.deepcopy(item)
            ids = item2.get("segment_ids")
            if isinstance(ids, list):
                item2["segment_ids"] = [int(value) + offset for value in ids
                                        if isinstance(value, int)]
            if isinstance(item2.get("batch"), int):
                item2["batch"] = item2["batch"] + copy_index * max_batch
            evidence.append(item2)
        for item in base_actions:
            item2 = _copy.deepcopy(item)
            if isinstance(item2.get("finding_id"), str):
                item2["finding_id"] = f"{item2['finding_id']}#c{copy_index}"
            actions.append(item2)
    tiled.update({"paras": paras, "pairs": pairs, "findings": findings,
                  "review_evidence": evidence, "human_actions": actions})
    stats = dict(tiled.get("review_stats") or {})
    for key, value in list(stats.items()):
        if isinstance(value, int):
            stats[key] = value * copies
    tiled["review_stats"] = stats
    for key in ("tm_used_count", "tm_recovered_count"):
        if isinstance(tiled.get(key), int):
            tiled[key] = tiled[key] * copies
    return tiled


def _seed_tiled_task(adapter, template_job: str, target_segments: int, *,
                     fixture: Path, filename: str) -> dict:
    """Write a finished task of ~``target_segments`` segments from a template.

    The template is a task the engine really finished; this only repeats its
    state, renumbered, through the product's own state writer.  What is measured
    afterwards (opening and driving a finished big task) is unchanged, and the
    report says the task was seeded rather than produced by the pipeline.
    """
    import math

    core = adapter.core
    template = core.load_job_state(template_job) or {}
    size = len(template.get("pairs") or [])
    if size <= 0:
        raise RuntimeError(
            f"seed template {template_job} has no pairs: is it finished?")
    copies = max(1, int(math.ceil(float(target_segments) / float(size))))
    tiled = _tile_finished_state(template, copies)
    tiled["p2_done"] = True
    tiled["p3_done"] = True
    tiled["stage"] = template.get("stage") or "REPORT_GENERATED"
    tiled["filename"] = filename
    created = adapter.import_documents(
        [fixture], adapter.EngineConfig(target_lang=template.get("target_lang") or "简体中文"))
    job_id = created[0].job_id
    # The new task has its own revision and journal: carrying the template's over
    # would be rejected by the product's snapshot guard (and point at another
    # task's journal).  Everything else is the template's, tiled.
    shell = core.load_job_state(job_id) or {}
    for key in ("snapshot_revision", "journal_cursor", "state_journal", "project_id"):
        tiled[key] = shell.get(key, template.get(key))
    for key in ("segment_structure_history", "segment_structure_undo_log"):
        tiled.pop(key, None)
    core.save_job_state(job_id, tiled)
    try:
        core.update_runtime_state(job_id, status="completed")
    except Exception:  # noqa: BLE001 - the state is what the UI reads
        pass
    return {"job_id": job_id, "template": template_job, "copies": copies,
            "segments": len(tiled.get("pairs") or []),
            "findings": len(tiled.get("findings") or [])}


def _task_fingerprint(adapter, job_id: str) -> dict:
    """Segments and a content digest, so two arms can prove the same load.

    The digest is over the committed source texts, in order: same fixture plus
    same seed must produce the same value on both arms, and a different value is
    the honest evidence that the load was not the same.
    """
    import hashlib

    try:
        state = adapter.load_state(job_id) or {}
    except Exception as exc:  # noqa: BLE001 - the report is the evidence
        return {"job_id": job_id, "segments": None, "digest": None,
                "error": f"{type(exc).__name__}: {exc}"}
    pairs = list(state.get("pairs") or [])
    sources = [str(pair.get("source") or "") for pair in pairs]
    digest = hashlib.sha256("\n".join(sources).encode("utf-8")).hexdigest()[:16]
    return {"job_id": job_id, "segments": len(pairs),
            "translated": sum(1 for pair in pairs
                              if str(pair.get("target") or "").strip()),
            "digest": f"sha256:{digest}"}


def _parse_r_window(value) -> tuple[float, float] | None:
    """``--r-window START:END`` in seconds after the translation started.

    The round measures only that slice of the run (plan D1: R here, I on S3).
    Both versions must use the same bounds, so the pair is echoed into the report
    and checked there.
    """
    if value in (None, "", False):
        return None
    text = str(value).strip()
    if ":" not in text:
        raise SystemExit(f"--r-window must be START:END seconds, got {text!r}")
    start_text, end_text = text.split(":", 1)
    try:
        start, end = float(start_text), float(end_text)
    except ValueError:
        raise SystemExit(f"--r-window must be START:END seconds, got {text!r}")
    if not (0.0 <= start < end):
        raise SystemExit(f"--r-window needs 0 <= START < END, got {text!r}")
    return (start, end)


def _empty_phase(reason: str) -> dict:
    return {"measured": False, "reason": reason}


def _phase_block(label: str, window: dict, seconds: float | None, *,
                 window_name: str, source: str,
                 tool_split_measured: bool = True) -> dict:
    runner_frames = _runner_frame_events(window)
    product_slow = int(window.get("slow_count") or 0)
    return {
        "label": label,
        "window": window_name,
        "window_source": source,
        "measured": True,
        "observed_s": round(seconds, 3) if seconds is not None else None,
        "tool_split_measured": bool(tool_split_measured),
        "tiers": {
            "slow_100ms": _tier_block(window, seconds, tier="slow_100ms",
                                      tool_split_measured=tool_split_measured),
            "severe_500ms": _tier_block(window, seconds, tier="severe_500ms",
                                        tool_split_measured=tool_split_measured),
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


def _phase_metrics(scenario: dict, run: dict | None, *,
                   tool_split_measured: bool = True,
                   r_only: dict | None = None) -> dict:
    """R (翻译中) and I (完成后) from the scenario and run windows (plan §9 D1).

    ``scenario`` is the whole measured session; ``run`` is the translating window.
    R is the run window, I is the remainder of the scenario window.  Subtraction
    happens on counters and on observed seconds, so both windows must be measured
    — a window with no evidence produces "not measured" rather than a zero rate.

    S3 has no translating window at all (its tasks are already finished): R is
    then reported as not applicable and I covers the whole scenario window.

    ``r_only`` marks a round that measures only R (plan D1 puts I on S3).  Such a
    round has no completion phase by construction, so I is reported as provided by
    S3 and the round stays valid: the void rule exists to catch a round that was
    supposed to measure I and could not, not a round that never had one.
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
        # Phase-level, not definition-level: a reader must be able to tell a
        # measured 0.0 share from a path that cannot separate the tool at all.
        "tool_split_measured": bool(tool_split_measured),
        "tool_split_reason": None if tool_split_measured else TOOL_SPLIT_UNAVAILABLE,
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
                   "task settled", tool_split_measured=tool_split_measured)
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
            source="scenario window minus the run window; includes import/settle tail",
            tool_split_measured=tool_split_measured)
        result["I"]["residual_note"] = (
            "by definition this bucket is everything outside the run window, so it "
            "also contains the import/open/settle tail, not only post-completion time")
        if (r_only or {}).get("r_only"):
            # Plan D1: I is measured on S3 (a finished 10,000-segment task), so an
            # R-only round has no completion phase of its own.  Reporting it as
            # "short" would void a round that did exactly what it was asked.
            result["R"]["valid"] = True
            result["I"] = {
                **_empty_phase("由 S3 提供：本轮只测 R 阶段"),
                "provided_by": "S3",
                "valid": True,
            }
            result["I"]["residual_note"] = (
                "D1 puts the completion phase on S3 (a finished 10,000-segment "
                "task); this round's R window is the whole measurement")
            result["r_only"] = True
            result["r_window"] = dict((r_only or {}).get("r_window") or {})
            result["i_phase_s"] = None
            result["valid_round"] = True
            result["discarded_rounds"] = 0
            result["discard_reasons"] = []
            return result
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
            source="the whole scenario window (no run window to subtract)",
            tool_split_measured=tool_split_measured)
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
                 settle_timeout, result, operations=None, clock=time.monotonic,
                 r_only: bool = False, r_window=(0.0, 0.0)):
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
        # R-only rounds measure one fixed slice of the run (plan D1).
        self.r_only = bool(r_only)
        self.r_window = (float(r_window[0]), float(r_window[1]))
        self._translation_started_at: float | None = None
        self._r_window_opened_at: float | None = None
        self._r_window_opened_elapsed: float | None = None
        self.r_window_info: dict | None = None
        # "The grid was not ready" is a *fact about the round*, not a silent skip:
        # an empty grid produces 0 scroll/select/type samples, and that is exactly
        # how P1-5e stayed invisible until the numbers were read by hand.
        self.skipped = {"scroll_not_scrollable": 0, "edit_table_not_ready": 0}
        self._last_edit_skip = 0.0
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
        if self.r_only:
            # Plan D1: measure a fixed slice of the run instead of "until the task
            # settles".  The window opens later, in _tick_r_only; the loop has to
            # stay alive until its end.
            self._translation_started_at = now
            self._interact_until = now + self.r_window[1] + 5.0
        else:
            self._open_run_window(now)
            self._interact_until = now + self.seconds
        self._last_scroll = self._last_edit = self._last_page = now
        self._last_panel = now
        self.state = "interact"

    def _tick_r_only(self, now: float) -> bool:
        """Open/close the fixed R slice.  True means this tick was consumed.

        ``start_s``/``end_s`` are measured from the moment the translation
        started, so both arms measure the same part of the run.
        """
        start_s, end_s = self.r_window
        elapsed = now - float(self._translation_started_at or now)
        if not self._run_open and self._r_window_opened_at is None \
                and elapsed >= start_s:
            self._open_run_window(now)
            self._r_window_opened_at = now
            self._r_window_opened_elapsed = elapsed
            # The cadences restart inside the window: the first samples must
            # belong to R, not to the warm-up before it.
            self._last_scroll = self._last_edit = self._last_page = now
            self._last_panel = now
            self.result["steps"].append({"step": "r_window_opened", "ok": True,
                                         "at_s": round(elapsed, 2)})
            return False
        if self._run_open and self._r_window_opened_at is not None \
                and now - self._r_window_opened_at >= (end_s - start_s):
            self._close_run_window(now)
            info = {"start_s": float(start_s), "end_s": float(end_s),
                    "measured_s": round(max(0.0, now - self._r_window_opened_at), 3),
                    "opened_at_s": round(float(self._r_window_opened_elapsed
                                               if self._r_window_opened_elapsed is not None
                                               else start_s), 2)}
            self.r_window_info = info
            self.result["r_window"] = info
            self.result["steps"].append({"step": "r_window_closed", "ok": True, **info})
            self._stop_task()
            self._finish(settled=True)
            return True
        return False

    def _stop_task(self) -> None:
        """Stop the run on purpose: the round's window is over, not the task."""
        try:
            already = bool(self.job_id) and _job_settled(self.adapter, self.job_id)
        except Exception:  # noqa: BLE001 - a predicate failure is not a reason to hang
            already = True
        if already:
            return
        try:
            self.window.cancel_job()
        except Exception as exc:  # noqa: BLE001 - recorded, the round still ends
            self.result["failures"].append(f"stop_task: {type(exc).__name__}: {exc}")
        else:
            self.result["steps"].append({"step": "task_stopped", "ok": True})

    def _tick_interact(self, now: float) -> None:
        if self.r_only and self._tick_r_only(now):
            return
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
        if now - self._last_scroll >= 0.1:
            if bar.maximum() > 0:
                self._last_scroll = now
                self._phase("scroll")
                step = max(bar.maximum() // 12, 1)
                with OperationTimer(self.operations, "scroll", phase=phase,
                                    clock=self.clock):
                    bar.setValue(0 if bar.value() + step > bar.maximum()
                                 else bar.value() + step)
                self.scrolls += 1
            else:
                # Counted, not skipped silently: a grid with no range means the
                # rows are not on screen, which changes what the round measured.
                self.skipped["scroll_not_scrollable"] += 1
        # A row can only be selected once the table actually has rows: right after
        # open, ``_rows`` is filled before the QTableWidget is resized, and a
        # selection change then paints a row whose items do not exist yet
        # (views.py:6097 ``number.text()`` on None).  Editing an unpopulated table
        # is not the scenario anyway, so the slot is skipped until it is ready.
        table_ready = bool(workbench.visible_segment_count()) and table.rowCount() > 0
        if now - self._last_edit >= 2.0 and not table_ready \
                and now - self._last_edit_skip >= 1.0:
            self._last_edit_skip = now
            self.skipped["edit_table_not_ready"] += 1
        if now - self._last_edit >= 2.0 and table_ready:
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
                 settle_timeout, result, operations=None, clock=time.monotonic,
                 reuse_tasks: bool = False):
        self.reuse_tasks = bool(reuse_tasks)
        self.seed_template = ""
        self.seed_segments = 0
        self.tasks_marker_path = None
        self.build_time_scale = None
        self._build_started: float | None = None
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
        if self.reuse_tasks and self._reuse_existing():
            return
        self.state = "import"
        self.result["steps"].append({"step": "import_dispatched", "ok": True,
                                     "fixtures": len(self.fixtures)})
        self._known = self._instrumented(
            lambda: {row.job_id for row in self.adapter.list_jobs()})
        self._dispatch_import()

    def _reuse_existing(self) -> bool:
        """Skip import+build when the finished tasks are already on disk.

        The build is 10,000 segments of translation; plan D1 only needs the
        *finished* task, so it is built once and reused across rounds.
        """
        marker = self.result.get("tasks_marker") or {}
        jobs = [str(item) for item in (marker.get("jobs") or []) if item]
        if not jobs:
            return False
        known = {row.job_id for row in
                 self._instrumented(lambda: self.adapter.list_jobs())}
        if not set(jobs) <= known:
            self.result["steps"].append({
                "step": "reuse_rejected", "ok": False,
                "reason": "the recorded tasks are not in the data dir any more",
                "jobs": jobs})
            return False
        self.job_ids = list(jobs)
        self._known = set(known)
        self.big_job_id = marker.get("big_job_id") or jobs[0]
        self.small_job_id = marker.get("small_job_id") or (jobs[-1] if len(jobs) > 1 else "")
        self.result["job_id"] = self.big_job_id
        fingerprint = dict(marker.get("fingerprint") or {})
        self.result["tasks"] = {"built": False, "reused": True,
                                "jobs": list(self.job_ids),
                                "segments": fingerprint.get("segments"),
                                "digest": fingerprint.get("digest"),
                                "job_id": fingerprint.get("job_id"),
                                "fingerprint": fingerprint}
        self.result["steps"].append({"step": "tasks_reused", "ok": True,
                                     "jobs": self.job_ids})
        self._start_measurement(self.clock())
        return True

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
        if self._build_started is None:
            self._build_started = now
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
            self._record_tasks(now)
            self._start_measurement(now)
            return
        if now - self._settle_started > self.build_timeout:
            self._fail("the task did not finish translating inside the build budget")

    # -- measured phase ----------------------------------------------------
    def _record_tasks(self, now: float) -> None:
        """Remember what was built (segments, digest) and reuse it next round."""
        fingerprint = _task_fingerprint(self.adapter, self.big_job_id)
        self.result["tasks"] = {
            "built": True,
            "reused": False,
            "build_seconds": round(max(0.0, now - float(self._build_started or now)), 2),
            "build_time_scale": getattr(self, "build_time_scale", None),
            "jobs": list(self.job_ids),
            "big_job_id": self.big_job_id,
            "small_job_id": self.small_job_id,
            "segments": fingerprint.get("segments"),
            "digest": fingerprint.get("digest"),
            "fingerprint": fingerprint,
        }
        path = getattr(self, "tasks_marker_path", None)
        if path:
            try:
                Path(path).write_text(json.dumps({
                    "schema": 1,
                    "jobs": list(self.job_ids),
                    "big_job_id": self.big_job_id,
                    "small_job_id": self.small_job_id,
                    "fingerprint": fingerprint,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                self.result["steps"].append({"step": "tasks_recorded", "ok": True,
                                             "path": str(path)})
            except OSError as exc:
                self.result["failures"].append(f"tasks marker not written: {exc}")

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
    if (os.environ.get(QT_MESSAGES_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}:
        _install_qt_message_handler(result)
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


#: P2-21 第三步：把 Qt 自己的诊断消息抓下来（`qFatal` 会先打一句话再 abort）。
#: 默认关闭；`FOLITH_QT_MESSAGES=1` 打开，写到 ``FOLITH_QT_MESSAGES_OUT``（默认 qt-messages.log）。
QT_MESSAGES_ENV = "FOLITH_QT_MESSAGES"
QT_MESSAGES_OUT_ENV = "FOLITH_QT_MESSAGES_OUT"
_qt_message_handle = None


def _qt_message_level_name(mode) -> str:
    try:
        from PySide6.QtCore import QtMsgType

        return {
            QtMsgType.QtDebugMsg: "debug",
            QtMsgType.QtInfoMsg: "info",
            QtMsgType.QtWarningMsg: "warning",
            QtMsgType.QtCriticalMsg: "critical",
            QtMsgType.QtFatalMsg: "fatal",
        }.get(mode, str(int(mode)) if hasattr(mode, "__int__") else str(mode))
    except Exception:  # noqa: BLE001
        return str(mode)


def _install_qt_message_handler(result: dict) -> None:
    """每条 Qt 消息一行，带线程名，**立即 flush**（abort 时缓冲区不会丢）。"""
    global _qt_message_handle
    if _qt_message_handle is not None:
        return
    target = os.environ.get(QT_MESSAGES_OUT_ENV) or "qt-messages.log"
    try:
        handle = open(target, "w", encoding="utf-8", buffering=1)
    except OSError as exc:
        result.setdefault("failures", []).append(f"qt messages log unavailable: {exc}")
        return

    def handler(mode, context, message) -> None:
        try:
            handle.write(
                f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}\t"
                f"{threading.current_thread().name}\t"
                f"{_qt_message_level_name(mode)}\t{message}\n")
            handle.flush()
        except Exception:  # noqa: BLE001 - 记录器不能再抛
            pass

    from PySide6.QtCore import qInstallMessageHandler

    qInstallMessageHandler(handler)
    _qt_message_handle = handle
    result["qt_messages_log"] = target


#: P2-21：`QBasicTimer::stop: Failed. Possibly trying to stop from a different thread`
#: 已经抓到（worker 线程 `Dummy-1` 上停了一个 GUI 线程的定时器）。这个开关把
#: ``QTimer.stop`` / ``QObject.killTimer`` 包一层：**非主线程调用**时把线程名和 Python
#: 栈记进 `FOLITH_QT_MESSAGES_OUT`，直接指出是谁跨线程停的。
QT_THREAD_GUARD_ENV = "FOLITH_QT_THREAD_GUARD"


def _install_qt_thread_guard(result: dict) -> None:
    import traceback

    from PySide6.QtCore import QObject, QTimer

    target = os.environ.get(QT_MESSAGES_OUT_ENV) or "qt-messages.log"
    try:
        handle = open(target, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        result.setdefault("failures", []).append(f"qt thread guard log unavailable: {exc}")
        return
    main_ident = threading.main_thread().ident
    installed: list[str] = []

    def wrap(owner, name: str, label: str) -> None:
        original = getattr(owner, name, None)
        if original is None or getattr(original, "_p21_guard", False):
            return

        def guarded(self, *args, **kwargs):
            if threading.get_ident() != main_ident:
                stack = " | ".join(
                    f"{Path(fr.filename).name}:{fr.lineno} {fr.name}"
                    for fr in traceback.extract_stack()[-8:-1])
                try:
                    handle.write(
                        f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}\t"
                        f"{threading.current_thread().name}\toff-thread-call\t"
                        f"{label}({type(self).__name__})\t{stack}\n")
                    handle.flush()
                except Exception:  # noqa: BLE001
                    pass
            return original(self, *args, **kwargs)

        guarded._p21_guard = True
        setattr(owner, name, guarded)
        installed.append(label)

    wrap(QTimer, "stop", "QTimer.stop")
    wrap(QObject, "killTimer", "QObject.killTimer")
    result["qt_thread_guard"] = installed


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


#: S4 (#455): an eight-hour run must show 0 crashes, 0 "not responding" episodes
#: and no steady growth of idle RSS after the last cycle.  These defaults are the
#: verdict's numbers; the round still reports what it measured when it misses.
S4_DEFAULT_SECONDS = 8 * 3600.0
S4_SAMPLE_SECONDS = 30.0
S4_CYCLE_TIMEOUT_S = 900.0
S4_IDLE_SECONDS = 120.0
S4_IDLE_SAMPLE_SECONDS = 15.0
S4_RSS_GROWTH_MB = 64.0
S4_RSS_GROWTH_FRACTION = 0.20
#: Distinct fixtures the loop rotates through (the product de-duplicates imports).
S4_FIXTURE_POOL = 12


def current_rss_mb() -> float | None:
    """Resident set size of this process, in MB, or ``None`` if unreadable."""
    if sys.platform.startswith("linux"):
        try:
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return pages * (os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0))
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                                 capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()) / 1024.0
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    try:  # Windows
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001 - a number we cannot read is not a failure
        return None


def current_handle_count() -> int | None:
    """Open file descriptors / handles of this process, or ``None``."""
    if sys.platform.startswith("linux"):
        try:
            return len(os.listdir("/proc/self/fd"))
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            return len(os.listdir("/dev/fd"))
        except OSError:
            return None
    try:  # Windows
        import ctypes
        from ctypes import wintypes

        count = wintypes.DWORD()
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.kernel32.GetProcessHandleCount(handle, ctypes.byref(count)):
            return None
        return int(count.value)
    except Exception:  # noqa: BLE001
        return None


def endurance_verdict(*, cycles: int, crashes: int, not_responding: int,
                      rss_samples: list[float], idle_rss_mb: list[float],
                      growth_limit_mb: float = S4_RSS_GROWTH_MB,
                      growth_fraction: float = S4_RSS_GROWTH_FRACTION) -> dict:
    """#455's acceptance: 0 crashes, 0 "not responding", idle RSS does not grow.

    ``idle_rss_mb`` is the idle tail measured *after* the last cycle: growth is
    ``last - first``, and it must stay under both an absolute limit (a small run
    must not look fine just because 20% of 20 MB is nothing) and a fraction of
    the baseline (a large run must not grow without bound).
    """
    idle_growth = None
    idle_ok: bool | None = None
    idle_limit = None
    if len(idle_rss_mb) >= 2:
        idle_growth = round(idle_rss_mb[-1] - idle_rss_mb[0], 2)
        allowed = max(growth_limit_mb, idle_rss_mb[0] * growth_fraction)
        idle_ok = idle_growth <= allowed
        idle_limit = round(allowed, 2)
    # A run with no idle tail (a very short one) has no evidence *for* stability,
    # but it also has none against it: report null rather than a fake pass.
    ok = bool(cycles > 0 and crashes == 0 and not_responding == 0
              and idle_ok is not False)
    return {
        "cycles": cycles,
        "crashes": crashes,
        "not_responding": not_responding,
        "rss_samples_mb": [round(value, 2) for value in rss_samples],
        "idle_rss_mb": [round(value, 2) for value in idle_rss_mb],
        "idle_rss_growth_mb": idle_growth,
        "idle_rss_growth_limit_mb": idle_limit,
        "criteria": {
            "no_crash": crashes == 0,
            "no_not_responding": not_responding == 0,
            "idle_rss_stable": idle_ok,
            "idle_rss_measured": len(idle_rss_mb) >= 2,
            "had_cycles": cycles > 0,
        },
        "ok": ok,
        "verdict": ("pass" if ok else "fail:" + ",".join(
            name for name, passed in (
                ("no_crash", crashes == 0),
                ("no_not_responding", not_responding == 0),
                ("idle_rss_stable", idle_ok),
                ("had_cycles", cycles > 0)) if not passed)),
    }


class _EnduranceDriver:
    """S4: run the product's import → translate → deliver cycle in a loop.

    #455 asks for an eight-hour run with three facts: no crash, no "not
    responding" episode, and idle RSS that stops growing.  The loop therefore
    drives *real* cycles (not sleeps), bounds each one (a cycle that outlives its
    budget *is* the not-responding evidence), samples RSS/handles while it runs,
    and takes a quiet idle tail after the last cycle.
    """

    def __init__(self, *, app, window, adapter, jank, fixtures, seconds,
                 settle_timeout, result, sample_seconds=S4_SAMPLE_SECONDS,
                 cycle_timeout=S4_CYCLE_TIMEOUT_S, idle_seconds=S4_IDLE_SECONDS,
                 idle_sample_seconds=S4_IDLE_SAMPLE_SECONDS, clock=time.monotonic,
                 fixture_builder=None):
        self.app = app
        self.window = window
        self.adapter = adapter
        self.jank = jank
        self.fixtures = [str(item) for item in fixtures]
        self.seconds = max(5.0, float(seconds))
        self.settle_timeout = max(5.0, float(settle_timeout))
        self.result = result
        self.sample_seconds = max(1.0, float(sample_seconds))
        self.cycle_timeout = max(5.0, float(cycle_timeout))
        self.idle_seconds = max(0.0, float(idle_seconds))
        self.idle_sample_seconds = max(1.0, float(idle_sample_seconds))
        self.clock = clock
        self.state = "created"
        self.finished = False
        self.cycles = 0
        self.crashes = 0
        self.not_responding = 0
        self.cycle_records: list[dict] = []
        self.rss_samples: list[float] = []
        self.idle_rss_mb: list[float] = []
        self.handle_samples: list[int] = []
        self.job_id = ""
        self._deadline = 0.0
        self._cycle_started = 0.0
        self._cycle_deadline = 0.0
        self._next_sample = 0.0
        self._idle_until = 0.0
        self._next_idle_sample = 0.0
        self._predicate_every = 0.05
        self._last_predicate = 0.0
        self._fixture_index = 0
        self.fixture_builder = fixture_builder

    # -- sampling ---------------------------------------------------------
    def _sample(self) -> None:
        rss = current_rss_mb()
        if rss is not None:
            self.rss_samples.append(rss)
        handles = current_handle_count()
        if handles is not None:
            self.handle_samples.append(handles)

    def begin(self) -> None:
        self.result["steps"].append({"step": "endurance_started", "ok": True})
        self._deadline = self.clock() + self.seconds
        self._sample()
        self._next_sample = self.clock() + self.sample_seconds
        self._start_cycle()

    def _start_cycle(self) -> None:
        self._fixture_index += 1
        if self.fixture_builder is not None:
            fixture = str(self.fixture_builder(self._fixture_index))
        else:
            fixture = self.fixtures[self._fixture_index % len(self.fixtures)]
        self._cycle_started = self.clock()
        # The run deadline ends the loop; it is not a cycle budget.  Mixing the two
        # counted the last, perfectly healthy cycle as "not responding" because the
        # run's time ran out inside it.
        self._cycle_deadline = self._cycle_started + self.cycle_timeout
        self.job_id = ""
        self._jobs_before: set[str] = set()
        self.state = "import"
        try:
            self._jobs_before = {job.job_id for job in self.adapter.list_jobs()}
        except Exception:  # noqa: BLE001 - an unreadable list just means no filter
            self._jobs_before = set()
        try:
            self.window.import_paths([fixture], autostart=False)
        except Exception as exc:  # noqa: BLE001 - a crash is a finding, not a stop
            self.crashes += 1
            self._record_cycle(ok=False, reason=f"import raised {type(exc).__name__}: {exc}")
            self._after_cycle()

    def _record_cycle(self, *, ok: bool, reason: str = "") -> None:
        self.cycles += 1
        self.cycle_records.append({
            "cycle": self.cycles,
            "ok": ok,
            "seconds": round(self.clock() - self._cycle_started, 2),
            "job_id": self.job_id,
            "reason": reason,
        })

    def _after_cycle(self) -> None:
        now = self.clock()
        if now >= self._deadline:
            self._begin_idle(now)
        else:
            self._start_cycle()

    def _begin_idle(self, now: float) -> None:
        self._idle_until = now + self.idle_seconds
        self._next_idle_sample = now
        self.state = "idle"

    def tick(self) -> bool:
        if self.finished:
            return True
        try:
            getattr(self, "_tick_" + self.state)(self.clock())
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            self.crashes += 1
            self.result["failures"].append(
                f"endurance {self.state}: {type(exc).__name__}: {exc}")
            self._finish()
        return self.finished

    def _predicate_due(self, now: float) -> bool:
        if now - self._last_predicate < self._predicate_every:
            return False
        self._last_predicate = now
        return True

    def _run_window_over(self, now: float) -> bool:
        """True when the endurance window has ended (the last cycle is truncated)."""
        if now < self._deadline:
            return False
        if self.state in {"import", "open", "settle"}:
            self.cycles += 1
            self.cycle_records.append({
                "cycle": self.cycles,
                "ok": None,
                "truncated": True,
                "seconds": round(now - self._cycle_started, 2),
                "job_id": self.job_id,
                "reason": "run window ended while this cycle was still running",
            })
        self._begin_idle(now)
        return True

    def _tick_import(self, now: float) -> None:
        if self._run_window_over(now):
            return
        if now >= self._next_sample:
            self._sample()
            self._next_sample = now + self.sample_seconds
        if not self._predicate_due(now):
            return
        try:
            jobs = self.adapter.list_jobs()
            busy = self.window.runner.busy
        except Exception as exc:  # noqa: BLE001
            self.crashes += 1
            self._record_cycle(ok=False, reason=f"listing failed: {exc}")
            self._after_cycle()
            return
        fresh = [job.job_id for job in jobs if job.job_id not in self._jobs_before]
        if busy or not fresh:
            # A cycle that cannot even get its task imported is a not-responding
            # episode once it outlives the budget; before that it is just work.
            if now > self._cycle_deadline:
                self.not_responding += 1
                self._record_cycle(ok=False, reason="import did not produce a task in time")
                self._after_cycle()
            return
        self.job_id = fresh[0]
        self.state = "open"
        self.window.open_job(self.job_id)

    def _tick_open(self, now: float) -> None:
        if self._run_window_over(now):
            return
        if now >= self._next_sample:
            self._sample()
            self._next_sample = now + self.sample_seconds
        if getattr(self.window, "_opening_job_id", ""):
            if now > self._cycle_deadline:
                self.not_responding += 1
                self._record_cycle(ok=False, reason="task did not open in time")
                self._after_cycle()
            return
        if getattr(self.window, "_current_job_id", "") != self.job_id:
            if now > self._cycle_deadline:
                self.not_responding += 1
                self._record_cycle(ok=False, reason="task did not open")
                self._after_cycle()
            return
        try:
            self.window.start_or_resume()
        except Exception as exc:  # noqa: BLE001
            self.crashes += 1
            self._record_cycle(ok=False, reason=f"start raised {type(exc).__name__}: {exc}")
            self._after_cycle()
            return
        self.state = "settle"

    def _tick_settle(self, now: float) -> None:
        if self._run_window_over(now):
            return
        if now >= self._next_sample:
            self._sample()
            self._next_sample = now + self.sample_seconds
        if not self._predicate_due(now):
            return
        try:
            runtime = self.adapter.core.load_runtime_state(self.job_id) or {}
            state = self.adapter.load_state(self.job_id) or {}
        except Exception as exc:  # noqa: BLE001
            self.crashes += 1
            self._record_cycle(ok=False, reason=f"state read failed: {exc}")
            self._after_cycle()
            return
        status = str(runtime.get("status") or "")
        done = bool(state.get("p2_done")) or status == "completed"
        failed = status in {"failed", "interrupted", "cancelled"}
        if done:
            self._record_cycle(ok=True)
            self._after_cycle()
            return
        if failed:
            self._record_cycle(ok=False, reason=f"cycle ended {status}")
            self._after_cycle()
            return
        if now > self._cycle_deadline:
            # The cycle outlived its budget: this is the "not responding" fact.
            self.not_responding += 1
            self._record_cycle(ok=False, reason=f"cycle exceeded {self.cycle_timeout:.0f}s")
            self._after_cycle()

    def _tick_idle(self, now: float) -> None:
        if now >= self._next_idle_sample:
            rss = current_rss_mb()
            if rss is not None:
                self.idle_rss_mb.append(rss)
            self._next_idle_sample = now + self.idle_sample_seconds
        if now >= self._idle_until:
            self._finish()

    def _finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.state = "finished"
        # ``_run_scenario_event_loop`` is ``app.exec()``: it only returns when the
        # application is asked to quit.  S1 gets that from closing its window; an
        # endurance run has no single interaction window to close, so it quits.
        try:
            self.window.close()
        except Exception:  # noqa: BLE001 - the quit below is what matters
            pass
        try:
            self.app.quit()
        except Exception:  # noqa: BLE001
            pass


def run_source_s4(args: argparse.Namespace, fixtures: list[Path], data_dir: Path,
                  log_dir: Path, base_url: str) -> dict:
    """S4: the eight-hour endurance run (#455).

    Every cycle is the product's real path (import → translate → deliver the
    snapshot), driven from Qt timer callbacks like S1.  The verdict is #455's:
    0 crashes, 0 not-responding episodes, idle RSS stable after the last cycle.
    R/I phases do not apply -- an endurance run has no single translating window
    -- and the report says so instead of leaving the fields empty.
    """
    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": [],
                    "tool_split_measured": True,
                    "window_control": {
                        "controlled_by": "the harness event loop",
                        "seconds_applied": True,
                        "seconds": args.seconds,
                    }}
    paths, app, window, adapter, monitor, jank = _prepare_window(
        args, data_dir, log_dir, result)
    # One file, rewritten with a unique salt per cycle: the product de-duplicates
    # identical imports, so reusing the bytes would stop the loop (the first
    # version imported the same document twice and then waited for a task that
    # could never appear).  A single path keeps an eight-hour run from leaving
    # tens of thousands of fixture files behind.
    base_paras = max(3, int(getattr(args, "paras", 6) or 6))
    cycle_path = args.out / "fixtures" / "s4-cycle.epub"

    def _cycle_fixture(cycle: int) -> Path:
        return build_epub(cycle_path, base_paras, salt=f"cycle-{cycle}")

    driver = _EnduranceDriver(
        app=app, window=window, adapter=adapter, jank=jank,
        fixtures=[str(item) for item in fixtures], fixture_builder=_cycle_fixture,
        seconds=args.seconds, settle_timeout=args.settle_timeout, result=result,
        sample_seconds=float(getattr(args, "s4_sample_seconds", S4_SAMPLE_SECONDS)),
        cycle_timeout=float(getattr(args, "s4_cycle_timeout", S4_CYCLE_TIMEOUT_S)),
        idle_seconds=float(getattr(args, "s4_idle_seconds", S4_IDLE_SECONDS)),
        idle_sample_seconds=float(
            getattr(args, "s4_idle_sample_seconds", S4_IDLE_SAMPLE_SECONDS)))
    started = time.monotonic()
    try:
        _drive_event_loop(app, driver, result)
        result["endurance"] = endurance_verdict(
            cycles=driver.cycles, crashes=driver.crashes,
            not_responding=driver.not_responding,
            rss_samples=driver.rss_samples, idle_rss_mb=driver.idle_rss_mb)
        result["cycles"] = driver.cycle_records
        result["handle_samples"] = driver.handle_samples
        result["translation"] = {
            "settled": bool(driver.cycles),
            "completed": driver.cycles > 0,
            "mode": "s4_endurance",
            "cycles": driver.cycles,
            "crashes": driver.crashes,
            "not_responding": driver.not_responding,
        }
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
        result["endurance"] = endurance_verdict(
            cycles=driver.cycles, crashes=driver.crashes + 1,
            not_responding=driver.not_responding,
            rss_samples=driver.rss_samples, idle_rss_mb=driver.idle_rss_mb)
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        result["run_window"] = {
            "measured": False,
            "present": False,
            "reason": ("S4 is an endurance loop: there is no single translating "
                       "window, so R does not apply and the verdict is not a "
                       "jank rate"),
        }
        if getattr(driver, "scenario_window", None):
            result["gui_jank"] = driver.scenario_window
    return result


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
    r_window = _parse_r_window(getattr(args, "r_window", None))
    r_only = bool(getattr(args, "r_only", False)) and r_window is not None
    window_control = {
        "controlled_by": "the harness interaction window",
        "seconds_applied": True,
        "seconds": args.seconds,
        "settle_timeout": args.settle_timeout,
    }
    if r_only:
        # The round is bounded by a fixed slice of the run, not by --seconds.
        window_control = {
            "controlled_by": "the --r-window bounds (translation start + start_s .. end_s)",
            "seconds_applied": True,
            "seconds": float(r_window[1]),
            "r_window": [float(r_window[0]), float(r_window[1])],
        }
    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": [],
                    "tool_split_measured": True,
                    "r_only": r_only,
                    "window_control": window_control}
    paths, app, window, adapter, monitor, jank = _prepare_window(
        args, data_dir, log_dir, result)
    checkpoint = jank.checkpoint() if jank is not None else None
    operations = OperationRecorder()

    started = time.monotonic()
    driver = _ScenarioDriver(app=app, window=window, adapter=adapter, jank=jank,
                             fixture=fixture, seconds=args.seconds,
                             settle_timeout=args.settle_timeout, result=result,
                             operations=operations, r_only=r_only,
                             r_window=r_window or (0.0, 0.0))
    try:
        _drive_event_loop(app, driver, result)
        if not driver.finished:
            result["failures"].append("scenario did not finish (event loop ended early)")
        job_id = driver.job_id
        state = (adapter.load_state(job_id) or {}) if job_id else {}
        runtime_status = ((adapter.core.load_runtime_state(job_id) or {}).get("status")
                          if job_id else "")
        if r_only:
            # An R-only round ends *by design* while the task is still running:
            # "completed" here means "this round did what it was asked", not "the
            # translation finished".  The stop is reported, never hidden.
            result["translation"] = {
                "settled": True,
                "completed": True,
                "mode": "r_only",
                "interrupted_by_harness": True,
                "r_window": result.get("r_window") or {},
                "runtime_status": runtime_status,
                "p2_done": bool(state.get("p2_done")),
                "segments": len(state.get("pairs") or []),
                "edited_segments": driver.edited,
                "page_switches": driver.pages,
                "scroll_events": driver.scrolls,
                "panel_events": driver.panels,
            }
            if job_id:
                result["run_records"] = read_run_records(paths.data_dir, job_id)
            raise _R_OnlyAssembled()
        result["translation"] = {
            "settled": driver.settled,
            # ``settled`` is true for a *failed* task too (the driver waits for a
            # terminal state, not for success).  ``completed`` is the field that
            # means "the translation produced its result", and ``ok`` uses it: a
            # run whose model endpoint refused every call must not look like a
            # measured round.
            "completed": bool(driver.settled and state.get("p2_done")),
            "runtime_status": runtime_status,
            "p2_done": bool(state.get("p2_done")),
            "segments": len(state.get("pairs") or []),
            "edited_segments": driver.edited,
            "page_switches": driver.pages,
            "scroll_events": driver.scrolls,
            "panel_events": driver.panels,
        }
        if job_id:
            result["run_records"] = read_run_records(paths.data_dir, job_id)
    except _R_OnlyAssembled:
        pass
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        result["operations"] = operations.report()
        result["operations_skipped"] = dict(driver.skipped)
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
                    "steps": [], "failures": [],
                    "tool_split_measured": True,
                    "window_control": {
                        "controlled_by": "the harness event loop",
                        "seconds_applied": True,
                        "seconds": args.seconds,
                    }}
    paths, app, window, adapter, monitor, jank = _prepare_window(
        args, data_dir, log_dir, result)
    operations = OperationRecorder()

    started = time.monotonic()
    marker_path = data_dir / "s3-tasks.json"
    marker: dict = {}
    if getattr(args, "seed_template", "") and int(getattr(args, "seed_segments", 0) or 0):
        # D1 wants a *finished* big task, not a pipeline run.  Seed it here, while
        # nothing is importing: doing it inside the driver raced the import of the
        # small task and the product refused the second write.
        seeded = _seed_tiled_task(
            adapter, str(args.seed_template), int(args.seed_segments),
            fixture=fixtures[0],
            filename=f"seeded-{int(args.seed_segments)}-segments.epub")
        small = adapter.import_documents(
            [fixtures[1]], adapter.EngineConfig(
                target_lang=str((adapter.load_state(seeded["job_id"]) or {})
                                .get("target_lang") or "简体中文")))[0].job_id
        fingerprint = _task_fingerprint(adapter, seeded["job_id"])
        marker = {
            "schema": 1,
            "jobs": [seeded["job_id"], small],
            "big_job_id": seeded["job_id"],
            "small_job_id": small,
            "fingerprint": fingerprint,
        }
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        result["seeded"] = seeded
        result["steps"].append({"step": "task_seeded", "ok": True, **seeded})
        # The tasks now exist: the driver only has to open and drive them.
        args.reuse_tasks = True
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result["failures"].append(f"tasks marker unreadable: {exc}")
            marker = {}
    if marker:
        # The build is 10,000 segments of translation: reuse it when asked, and
        # report what it contained so both arms can be compared.
        result["tasks_marker"] = marker
    driver = _TaskSwitchDriver(app=app, window=window, adapter=adapter, jank=jank,
                               fixtures=fixtures, seconds=args.seconds,
                               settle_timeout=args.settle_timeout, result=result,
                               operations=operations,
                               reuse_tasks=bool(getattr(args, "reuse_tasks", False)))
    driver.tasks_marker_path = marker_path
    driver.build_time_scale = getattr(args, "build_time_scale", None)
    driver.seed_template = str(getattr(args, "seed_template", "") or "")
    driver.seed_segments = int(getattr(args, "seed_segments", 0) or 0)
    try:
        _drive_event_loop(app, driver, result)
        measured_s3 = bool(getattr(driver, "scenario_window", None)) \
            and bool((driver.scenario_window or {}).get("measured"))
        result["translation"] = {
            "settled": measured_s3,
            "completed": measured_s3,
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
    result: dict = {"mode": "frozen_selftest", "steps": [], "failures": [],
                    # The frozen app runs its own scripted flow; the harness never
                    # takes an instrumentation mark inside that process, so the
                    # tool's share is not measured here (P1-2/P1-4 §5.3).
                    "tool_split_measured": False,
                    "window_control": {
                        "controlled_by": "the frozen app's own --selftest flow",
                        "seconds_applied": False,
                        "paras_applied": args.paras,
                        "reason": "run_frozen() hands the app a fixture, not an "
                                  "interaction window: the measured window is the "
                                  "one the app's own flow produced",
                    }}
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
    frozen_run = report.get("run") or {}
    result["translation"] = {
        "settled": bool(frozen_run.get("p2_done")),
        "completed": bool(report.get("ok") and frozen_run.get("p2_done")),
        "runtime_status": frozen_run.get("runtime_status"),
        "error": (frozen_run.get("error") or {}).get("message")
        if isinstance(frozen_run.get("error"), dict) else frozen_run.get("error"),
        "segments": frozen_run.get("segments"),
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
    _motion = motion_state()
    # Older callers (and the tests) hand over only ``mode``; keep answering the
    # same way for them instead of silently switching them to real-window limits.
    _offscreen = environment.get("offscreen")
    if _offscreen is None:
        _offscreen = str(environment.get("mode") or "").endswith("_offscreen")
    else:
        _offscreen = bool(_offscreen)
    scenario_window = _normalize_window(result.get("gui_jank") or {})
    run_window = result.get("run_window") or {}
    if not isinstance(run_window, dict) or not run_window:
        run_window = {"measured": False,
                      "reason": "this mode did not collect a translating window"}
    elif "measured" not in run_window:
        run_window = _normalize_window(run_window)
    tool_split_measured = bool(result.get("tool_split_measured", True))
    phases = _phase_metrics(
        scenario_window, run_window, tool_split_measured=tool_split_measured,
        r_only={"r_only": bool(result.get("r_only")),
                "r_window": result.get("r_window") or {}})
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
                # A frozen round reports tool_share as null, never as 0: the
                # app's own scripted flow takes no instrumentation mark, so a
                # zero there would be "not separable", not "no tool cost".
                "measured": tool_split_measured,
                "reason": None if tool_split_measured else TOOL_SPLIT_UNAVAILABLE,
            },
        },
        "started_at": started_at,
        "finished_at": now_iso(),
        "fixture": fixture_block,
        "window_control": result.get("window_control") or {
            "controlled_by": "unknown",
            "seconds_applied": None,
        },
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
        "r_only": bool(result.get("r_only")),
        "seeded": result.get("seeded") or {},
        "tasks": result.get("tasks") or {},
        "operations_skipped": result.get("operations_skipped") or {},
        "limits": limits_for(_offscreen),
        "real_machine_baseline": False,
        "motion": _motion,
        "motion_enabled": _motion.get("motion_enabled"),
        "note": note_for(_offscreen),
    }
    if scenario == "S4":
        # #455's verdict is not a jank rate: the endurance block carries the
        # cycle count, the crash/not-responding counters and the idle RSS tail.
        payload["endurance"] = result.get("endurance")
        payload["cycles"] = result.get("cycles") or []
        payload["handle_samples"] = result.get("handle_samples") or []
        payload["wall_time_s"] = result.get("wall_time_s")
    translation = result.get("translation") or {}
    if scenario == "S4":
        endurance = result.get("endurance") or {}
        payload["ok"] = bool(endurance.get("ok") and not result.get("failures"))
    else:
        payload["ok"] = bool(
            translation.get("completed")
            and not result.get("failures"))
    return payload


def _summary_line(payload: dict) -> dict:
    """The one-line stdout summary: the numbers a console reader needs first."""
    if payload.get("scenario") == "S4":
        endurance = payload.get("endurance") or {}
        return {
            "ok": payload.get("ok"),
            "report": payload.get("report_path"),
            "scenario": "S4",
            "mode": payload.get("mode") or (payload.get("environment") or {}).get("mode"),
            "cycles": endurance.get("cycles"),
            "crashes": endurance.get("crashes"),
            "not_responding": endurance.get("not_responding"),
            "idle_rss_growth_mb": endurance.get("idle_rss_growth_mb"),
            "verdict": endurance.get("verdict"),
            "wall_time_s": payload.get("wall_time_s"),
            "report_path": payload.get("report_path"),
        }
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
        "seconds_applied": (payload.get("window_control") or {}).get("seconds_applied"),
        "tool_split_measured": (payload.get("measurement") or {})
        .get("tool_share", {}).get("measured"),
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
        if not (payload.get("translation") or {}).get("completed"):
            # Counters from a task that failed (e.g. the model endpoint was not
            # reachable) are not a measurement of the product, however tidy the
            # phase arithmetic looks.
            skipped.append({"report": str(path),
                            "reason": "translation did not complete "
                                      f"(runtime_status="
                                      f"{(payload.get('translation') or {}).get('runtime_status')!r})"})
            continue
        rows.append({"report": str(path), "name": path.parent.name,
                     "scenario": payload.get("scenario"), "phases": phases,
                     "r_only": bool(phases.get("r_only")),
                     "tasks_digest": (payload.get("tasks") or {}).get("digest")})
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


def verify_round(directory: Path) -> dict:
    """Check one finished round against the contract P1-5 depends on (no Qt).

    Written for the P1-5 prep smoke: after a round the operator needs to know, in
    one command, whether the artifacts the plan's numbers come from actually
    exist — ``runtime-baseline.json`` with measurement v3, a measured scenario
    window, a measurable R, the product's own ``gui_jank.json`` and a
    ``summary.json`` that carries the stage table — plus whether the two wiring
    facts the run must not lie about hold: ``tool_share`` is null (never 0) on a
    path that cannot separate the tool, and the round says whether ``--seconds``
    controlled its window.

    It does not judge the numbers, only that they are *measurable* and honestly
    labelled.  ``ok`` is the exit verdict; ``aggregate_ready`` additionally says
    whether :func:`aggregate_reports` will accept this round.
    """
    directory = Path(directory).expanduser().resolve()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail, *, required: bool = True) -> None:
        checks.append({"check": name, "ok": bool(ok), "required": required,
                       "detail": detail})

    report_path = directory / "runtime-baseline.json"
    payload: dict = {}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            add("report_present", True, str(report_path))
        except ValueError as exc:
            add("report_present", False, f"{report_path}: {exc}")
    else:
        add("report_present", False, f"{report_path} does not exist")
    if not payload:
        return {"directory": str(directory), "ok": False, "checks": checks,
                "aggregate_ready": False}

    measurement = payload.get("measurement") or {}
    metrics = payload.get("metrics") or {}
    phases = metrics.get("phases") or {}
    if payload.get("scenario") == "S4":
        # S4 is an endurance loop: there is no single translating window, so the
        # R/I checks below cannot apply (#455 judges crashes, not-responding
        # episodes and idle RSS instead).  Verifying an S4 round must check the
        # verdict's own evidence, not run the jank contract over it and call the
        # missing windows a failure.
        endurance = payload.get("endurance") or {}
        cycles = payload.get("cycles") or []
        finished = [item for item in cycles if item.get("ok") is True]
        add("implemented", payload.get("implemented") is True,
            {"implemented": payload.get("implemented")})
        add("endurance_block_present", bool(endurance), endurance)
        add("cycles_ran", bool(finished), {"cycles": endurance.get("cycles"),
                                           "finished": len(finished)})
        add("crash_count_recorded", "crashes" in endurance,
            {"crashes": endurance.get("crashes")})
        add("not_responding_count_recorded", "not_responding" in endurance,
            {"not_responding": endurance.get("not_responding")})
        add("idle_rss_measured", bool(endurance.get("criteria", {})
                                      .get("idle_rss_measured")),
            {"idle_rss_mb": endurance.get("idle_rss_mb")})
        add("samples_present", bool(payload.get("handle_samples")),
            {"handle_samples": len(payload.get("handle_samples") or [])},
            required=False)
        add("verdict_recorded", bool(endurance.get("verdict")),
            {"verdict": endurance.get("verdict")})
        ok = all(check["ok"] for check in checks if check.get("required", True))
        return {"directory": str(directory), "ok": bool(ok), "checks": checks,
                "aggregate_ready": False,
                "note": "S4 is an endurance round: R/I do not apply, the "
                        "endurance block is the evidence"}

    window_control = payload.get("window_control") or {}
    seconds_applied = window_control.get("seconds_applied")
    mode = (payload.get("environment") or {}).get("mode")

    add("measurement_v3", measurement.get("version") == MEASUREMENT_VERSION,
        {"version": measurement.get("version"), "expected": MEASUREMENT_VERSION})
    add("scenario_window_measured", (metrics.get("gui_jank") or {}).get("measured") is True,
        {"reason": (metrics.get("gui_jank") or {}).get("reason")})
    r_block = phases.get("R") or {}
    add("R_valid", r_block.get("valid") is True,
        {"measured": r_block.get("measured"), "reason": r_block.get("reason")})
    i_block = phases.get("I") or {}
    # The frozen arm's window belongs to the app's own flow, so it has no I phase
    # to validate: required only where --seconds controlled the window.
    r_only = bool(phases.get("r_only")) \
        or str((payload.get("translation") or {}).get("mode") or "") == "r_only"
    add("I_valid", i_block.get("valid") is True,
        {"measured": i_block.get("measured"),
         "reason": i_block.get("reason") or i_block.get("invalid_reason"),
         "i_phase_s": phases.get("i_phase_s"),
         "provided_by": i_block.get("provided_by")},
        # Plan D1 measures I on S3, so an R-only round has no I of its own and
        # must not be voided for the phase it never had.
        required=bool(seconds_applied) and not r_only)
    if r_only:
        window = phases.get("r_window") or {}
        start_s, end_s = window.get("start_s"), window.get("end_s")
        add("r_window_recorded",
            isinstance(start_s, (int, float)) and isinstance(end_s, (int, float))
            and float(end_s) > float(start_s)
            and float(window.get("measured_s") or 0) > 0,
            {"r_window": window,
             "why": "both arms must measure the same slice of the run"})
        add("i_phase_provided_by_s3", i_block.get("provided_by") == "S3",
            {"provided_by": i_block.get("provided_by"),
             "note": "D1: the completion phase is measured on S3, not here"})
    translation = payload.get("translation") or {}
    add("translation_completed", translation.get("completed") is True,
        {"settled": translation.get("settled"),
         "completed": translation.get("completed"),
         "runtime_status": translation.get("runtime_status"),
         "p2_done": translation.get("p2_done"),
         "error": translation.get("error")})
    add("no_failures", not (payload.get("failures") or []),
        {"failures": (payload.get("failures") or [])[:5]})

    records = metrics.get("run_records") or []
    add("gui_jank_json_on_disk",
        any(row.get("gui_jank_file") for row in records),
        {"runs": len(records),
         "with_file": sum(1 for row in records if row.get("gui_jank_file"))})

    stage_rows = []
    for row in records:
        path = Path(str(row.get("directory") or "")) / "summary.json"
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stage_rows.append({"summary": str(path),
                           "stages": len(summary.get("stages") or {}),
                           "gui_jank_measured": (summary.get("gui_jank") or {})
                           .get("measured")})
    add("product_stages_present",
        any(row["stages"] > 0 for row in stage_rows),
        {"summaries": stage_rows})

    # The ruling: a frozen round's tool share is null, not 0.  A 0.0 here would
    # read as "the tool added nothing" and poison the baseline comparison.
    shares = []
    for label in ("R", "I"):
        for tier_name, tier in ((phases.get(label) or {}).get("tiers") or {}).items():
            shares.append((f"{label}.{tier_name}", tier.get("tool_share"),
                           tier.get("tool_split_measured")))
    split_measured = (measurement.get("tool_share") or {}).get("measured")
    add("tool_share_honest",
        bool(split_measured) or all(value is None for _, value, _ in shares),
        {"tool_split_measured": split_measured,
         "shares": {name: value for name, value, _ in shares}})
    # P1-5e: "the grid was not ready" used to be a silent skip, so a round whose
    # table never populated looked like a round that simply did not scroll.  These
    # three operations only happen while the grid has rows; a source round that
    # sampled none of them during R measured an empty table, and its R numbers
    # cannot be compared with the other arm's.
    if str(mode or "").startswith("source"):
        op_spec = (metrics.get("operations") or {}).get("operations") or {}
        run_samples = {}
        for name in ("scroll", "select_segment", "type_echo"):
            run_phase = (((op_spec.get(name) or {}).get("phases") or {})
                         .get("run") or {})
            run_samples[name] = run_phase.get("count")
        add("run_phase_operations_sampled",
            all(isinstance(count, int) and count > 0
                for count in run_samples.values()),
            {"samples_in_R": run_samples,
             "skipped": payload.get("operations_skipped") or {},
             "why": ("scroll/select/type need rows in the grid; a zero means the "
                      "grid was empty while translating (P1-5e)")})
    add("window_control_recorded", isinstance(seconds_applied, bool),
        {"seconds_applied": seconds_applied,
         "controlled_by": window_control.get("controlled_by")})

    verdict = all(item["ok"] for item in checks
                  if item["required"])
    return {
        "directory": str(directory),
        "ok": verdict,
        "mode": mode,
        "seconds_applied": seconds_applied,
        "tool_split_measured": split_measured,
        "valid_round": phases.get("valid_round"),
        "aggregate_ready": bool(phases.get("valid_round"))
        and translation.get("completed") is True
        and measurement.get("version") == MEASUREMENT_VERSION,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Folith runtime baseline runner (S1/S3)")
    parser.add_argument("--out", default=None, help="output directory for this run")
    parser.add_argument("--scenario", default="S1", choices=("S1", "S2", "S3", "S4"))
    parser.add_argument("--paras", type=int, default=200, help="paragraph count (S1)")
    parser.add_argument("--seconds", type=float, default=None,
                        help="measured interaction window (translation keeps "
                             "running); source modes default to 90 and the frozen "
                             "mode refuses it, because the window there belongs to "
                             "the app's own --selftest flow")
    parser.add_argument("--settle-timeout", type=float, default=300.0,
                        help="how long to wait for the task to settle after interacting")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="subprocess budget for the frozen path")
    parser.add_argument("--mode", default="source", choices=("source", "frozen"))
    parser.add_argument("--frozen-app", default=None,
                        help="frozen Folith executable (implies --mode frozen)")
    parser.add_argument("--seed-template", default="",
                        help="S3: job id of a finished task in this data dir; the "
                             "big task is tiled from its state instead of translated")
    parser.add_argument("--seed-segments", type=int, default=0,
                        help="S3: target segment count for the seeded task "
                             "(0 = off; 10000 is the plan's 10,000-segment task)")
    parser.add_argument("--reuse-tasks", action="store_true",
                        help="S3: keep the data dir and skip import+build when the "
                             "finished tasks are already recorded there")
    parser.add_argument("--build-time-scale", type=float, default=None,
                        help="S3: time scale the tasks are built at (the build is "
                             "not part of any measured rate)")
    parser.add_argument("--r-only", action="store_true",
                        help="measure one fixed slice of the run (plan D1: R here, "
                             "I on S3) and stop the task when the window ends")
    parser.add_argument("--r-window", default=None,
                        help="START:END seconds after the translation started, "
                             "e.g. 120:720; requires --r-only")
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
    parser.add_argument("--s4-sample-seconds", type=float, default=S4_SAMPLE_SECONDS,
                        help="how often S4 samples this process's RSS and handle count")
    parser.add_argument("--s4-cycle-timeout", type=float, default=S4_CYCLE_TIMEOUT_S,
                        help="one S4 cycle may not exceed this; a cycle that does is "
                             "counted as a not-responding episode")
    parser.add_argument("--s4-idle-seconds", type=float, default=S4_IDLE_SECONDS,
                        help="quiet tail after the last S4 cycle (idle RSS baseline)")
    parser.add_argument("--s4-idle-sample-seconds", type=float,
                        default=S4_IDLE_SAMPLE_SECONDS,
                        help="sampling interval inside the S4 idle tail")
    parser.add_argument("--s3-segments", type=int, default=10000,
                        help="segments of the finished task S3 opens and switches to")
    parser.add_argument("--s3-small-paras", type=int, default=40,
                        help="segments of the second (small) task S3 switches to")
    parser.add_argument("--verify", default=None,
                        help="do not measure: check one finished round directory "
                             "against the P0/P1 report contract (exit 1 if a "
                             "required artifact or field is missing)")
    parser.add_argument("--aggregate", default=None,
                        help="do not measure: print medians over the rounds under "
                             "this directory (each round is */runtime-baseline.json)")
    args = parser.parse_args(argv)

    if args.verify:
        verdict = verify_round(Path(args.verify))
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["ok"] else 1

    if args.aggregate:
        summary = aggregate_reports(Path(args.aggregate).expanduser().resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["rounds"] else 1

    if not args.out:
        parser.error("--out is required unless --aggregate is used")
    seconds_provided = args.seconds is not None
    if args.frozen_app:
        args.mode = "frozen"
    if args.mode == "frozen" and seconds_provided:
        # Silently ignoring it produced reports whose window (13.9 s in P1-4) had
        # nothing to do with the requested duration.  Refuse instead: a wrong
        # window that looks configured is worse than a startup error.
        parser.error(
            "--seconds is not applicable to --mode frozen: the frozen arm runs the "
            "app's own --selftest flow, which owns the measured window (the fixture "
            "size, --paras, is what changes its length).  Drop --seconds for the "
            "frozen control arm, and use --mode source for a timed window.")
    if args.seconds is None:
        args.seconds = DEFAULT_SECONDS
    args.out = Path(args.out).expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=True)
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
            "limits": limits_for(bool(args.offscreen)),
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
        # --reuse-tasks keeps the built tasks (10,000 segments of translation are
        # not something to rebuild per round); everything else starts clean.
        keep = bool(getattr(args, "reuse_tasks", False)
                    or getattr(args, "seed_template", ""))
        if not (keep and directory.is_dir()):
            shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)

    if args.scenario == "S3":
        segments = max(2, int(args.s3_segments))
        if getattr(args, "seed_template", ""):
            # The big task is seeded from a template.  Two distinct fixtures: the
            # product de-duplicates imports, so re-importing the same file for the
            # shell and for the small task would hand back one job twice.
            fixtures = [
                build_epub(args.out / "fixtures" / "s3-shell.epub", 2),
                build_epub(args.out / "fixtures" / f"s3-{max(1, args.s3_small_paras)}p.epub",
                           max(1, args.s3_small_paras)),
            ]
            paras = int(getattr(args, "seed_segments", 0) or segments)
        else:
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
            elif args.scenario == "S4":
                result = run_source_s4(args, fixtures, data_dir, log_dir, base_url)
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
