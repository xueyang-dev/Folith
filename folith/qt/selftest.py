"""Scripted verification of the Beta main flow on the *real* window.

Why this lives in the package: the same code has to run in a source checkout
(``scripts/qt_desktop_probe.py``) and inside the frozen ``.app`` (``qt_client.py
--selftest``).  A probe that only exists in the source tree cannot prove anything
about the artifact a tester receives, and duplicating the flow would let the two
drift apart.

It drives the window exactly the way a person does — import, start, type into the
editor, submit, review, freeze the delivery, copy the files out, quit through the
window's own exit path, reopen — and returns a JSON-serialisable report.  It
cannot replace what needs a human (native panels, a real input method, sleep and
a clean machine); the report says so by construction.
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any

LONG_PARAGRAPHS = (
    "这是第一段，涉及术语 MT 与机器翻译流程。",
    "这是第二段，涉及术语 CAT 与计算机辅助翻译。",
    "这是第三段，用于验证交付与重开恢复。",
)

TYPED = "人工修改：这是桌面端写入的译文。"


def open_job_and_wait(window, job_id: str, *, timeout: float = 120.0) -> None:
    """Automation waits for the real asynchronous navigation to finish."""
    from PySide6.QtWidgets import QApplication

    failures_before = len(window.failures)
    window.open_job(job_id)
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while getattr(window, "_opening_job_id", "") and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if (window._current_job_id != job_id or getattr(window, "_opening_job_id", "")
            or len(window.failures) != failures_before):
        raise AssertionError(f"task did not open: {job_id}; failures={window.failures}")


def make_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)


def make_text_pdf(path: Path, paragraphs: list[str]) -> None:
    """A real text-layer PDF (not a renamed text file)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for text in paragraphs:
        page.insert_text((72, y), text, fontsize=12)
        y += 24
    doc.save(str(path))
    doc.close()


def make_scanned_pdf(path: Path, lines: list[str]) -> None:
    """A real *scanned-style* PDF: the page is an image, there is no text layer.

    Built by rendering text to a pixmap and embedding that image, which is what
    makes the ingestion path go through OCR rather than the text layer.
    """
    import pymupdf

    source = pymupdf.open()
    page = source.new_page()
    y = 80
    for line in lines:
        page.insert_text((72, y), line, fontsize=20)
        y += 40
    pixmap = page.get_pixmap(dpi=200)
    image_bytes = pixmap.tobytes("png")
    source.close()

    scanned = pymupdf.open()
    scanned_page = scanned.new_page(width=pixmap.width / 2,
                                    height=pixmap.height / 2)
    scanned_page.insert_image(scanned_page.rect, stream=image_bytes)
    scanned.save(str(path))
    scanned.close()


def build_fixtures(out_dir: Path, kind: str) -> list[Path]:
    fixtures: list[Path] = []
    target_dir = out_dir / "fixtures"
    target_dir.mkdir(parents=True, exist_ok=True)
    if kind in ("docx", "both"):
        path = target_dir / "selftest.docx"
        make_docx(path, list(LONG_PARAGRAPHS))
        fixtures.append(path)
    if kind in ("pdf", "both"):
        path = target_dir / "selftest.pdf"
        make_text_pdf(path, ["Text layer page one.", "Text layer page two."])
        fixtures.append(path)
    if kind == "scan":
        path = target_dir / "selftest-scan.pdf"
        make_scanned_pdf(path, ["Scanned page one for OCR baseline.",
                               "Second scanned line in English."])
        fixtures.append(path)
    if kind in ("txt", "md"):
        path = target_dir / f"selftest.{kind}"
        text = "\n\n".join(LONG_PARAGRAPHS)
        if kind == "md":
            text = "# Markdown intake\n\n" + text
        path.write_text(text, encoding="utf-8")
        fixtures.append(path)
    if kind == "epub":
        import zipfile
        from html import escape
        path = target_dir / "selftest.epub"
        with zipfile.ZipFile(path, "w") as book:
            book.writestr("mimetype", "application/epub+zip")
            book.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
            book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
            book.writestr("chapter.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + ''.join(f'<p>{escape(text)}</p>' for text in LONG_PARAGRAPHS) + '</body></html>')
        fixtures.append(path)
    return fixtures


def ocr_facts(core) -> dict:
    """What OCR would actually do in *this* build, without Homebrew in PATH."""
    facts: dict[str, Any] = {}
    try:
        facts["languages_installed"] = list(core.installed_ocr_languages())
        facts["engine"] = core.ocr_backend_report()
        facts["baseline"] = {language: core.ocr_language_status(language)
                             for language in ("eng", "chi_sim", "ara")}
        # A language we deliberately do not ship: the client must report the
        # missing pack instead of silently falling back to English.
        facts["missing_example"] = core.ocr_language_status("deu")
    except Exception as exc:  # noqa: BLE001 - a report must never raise
        facts["error"] = f"{type(exc).__name__}: {exc}"
    return facts


class Flow:
    """Runs the flow and records what happened, step by step."""

    def __init__(self, window, app, *, out_dir: Path, timeout: float = 600.0,
                 screenshot: str | None = None) -> None:
        self.window = window
        self.app = app
        self.out_dir = out_dir
        self.timeout = timeout
        self.screenshot = screenshot
        self.report: dict[str, Any] = {"started_at": time.time(), "steps": []}
        self.heartbeat = {"ticks": 0, "max_gap_ms": 0.0, "last": time.monotonic()}
        self._timer = None
        # Which phase the GUI thread was in when it stalled; a gap without this
        # context cannot be attributed to a cause.
        self.phase = "startup"
        self._worst_gaps: list[tuple[float, str]] = []

    # -- helpers -----------------------------------------------------------
    def step(self, name: str, **extra: Any) -> None:
        self.phase = name
        self.report["steps"].append(
            {"at": round(time.time() - self.report["started_at"], 2),
             "step": name, **extra})

    def start_heartbeat(self, interval_ms: int = 50) -> None:
        """A GUI-thread timer: a blocked GUI shows up as a large gap."""
        from PySide6.QtCore import QTimer

        def beat() -> None:
            now = time.monotonic()
            gap_ms = (now - self.heartbeat["last"]) * 1000
            if gap_ms > self.heartbeat["max_gap_ms"]:
                self.heartbeat["max_gap_ms"] = gap_ms
            if gap_ms > 250:
                self._worst_gaps.append((round(gap_ms, 1), self.phase))
                self._worst_gaps.sort(reverse=True)
                del self._worst_gaps[5:]
            self.heartbeat["last"] = now
            self.heartbeat["ticks"] += 1

        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(beat)
        self._timer.start()

    def stop_heartbeat(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def pump(self, seconds: float, predicate=None, interval: float = 0.02) -> bool:
        """Drive the event loop until ``predicate`` holds or the time is up.

        ``interval`` matters: a predicate that parses the full job state costs
        tens of milliseconds, and calling it every 20 ms would occupy the very
        GUI thread the responsiveness measurement is about.  Cheap predicates
        keep the default; state-based ones pass a coarser interval.
        """
        deadline = time.monotonic() + seconds
        next_check = 0.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            now = time.monotonic()
            if predicate is not None and now >= next_check:
                if predicate():
                    return True
                next_check = now + interval
            # Polling slowly must not suspend the GUI timer we are measuring.
            time.sleep(min(interval, 0.01))
        self.app.processEvents()
        return bool(predicate()) if predicate is not None else True

    def _await_review(self, core, job_id: str) -> bool:
        """Wait until the review action the window started has really finished.

        The window's own task runner is the authority.  Runtime state only
        tracks the translation pipeline, and a receipt-based signal cannot tell
        a re-review of already-reviewed segments from an idle app - waiting on
        either one would start a second review on top of a running one and
        deadlock on the task state lock.
        """
        # The task is queued before ``review_all``/``review_selected`` returns;
        # give the pool a moment to show it as running.
        self.pump(30.0, lambda: self.window.runner.busy, interval=0.1)
        if not self.pump(self.timeout, lambda: not self.window.runner.busy,
                         interval=0.5):
            return False
        # The gate decides whether every expected segment has a receipt.
        return self.pump(60.0, lambda: not self._missing_review_indexes(core, job_id),
                         interval=0.5)

    @staticmethod
    def _review_readiness(core, job_id: str) -> dict:
        from folith.translation_evidence import translation_review_readiness

        return translation_review_readiness(core.load_job_state(job_id) or {})

    @staticmethod
    def _open_blocking_findings(core, job_id: str) -> list:
        """Review findings that ask a human to decide before delivery."""
        state = core.load_job_state(job_id) or {}
        return [
            finding for finding in state.get("findings") or []
            if isinstance(finding, dict)
            and finding.get("type") == "review"
            and finding.get("severity") == "blocking"
            and finding.get("status") == "open"
            and finding.get("requires_human_confirmation")
            and not finding.get("resolved")
        ]

    def _resolve_open_blocking_findings(self, core, adapter, job_id: str) -> list:
        """Answer the review queue the way the operator does.

        When the reviewer proposed a concrete replacement, the operator applies
        it in the editor and the segment is re-reviewed — the product's normal
        "采纳建议并复核" loop.  Otherwise the operator accepts the reviewer's
        resolution through the product's own human-decision path.  Both are
        first-class product actions, and both are recorded in the report.
        """
        resolved = []
        for finding in self._open_blocking_findings(core, job_id):
            index = finding.get("segment_index")
            suggested = str(finding.get("suggested_target") or "").strip()
            record = {
                "finding_id": str(finding.get("finding_id") or ""),
                "segment_index": index,
                "reason": str(finding.get("reason") or "")[:160],
            }
            if isinstance(index, int) and suggested:
                self.window.workbench.select_segment(index)
                self.app.processEvents()
                self.window.workbench.set_editor_text_for_test(suggested)
                self.app.processEvents()
                self.window.workbench.flush_draft()
                self.window.submit_edit(index)
                self.pump(5)
                record.update(action="apply_suggested_target",
                              suggested_target=suggested)
            else:
                core.decide_translation_review_finding(
                    job_id, record["finding_id"], "accept_resolution",
                    "验收操作员", actor_type="human",
                    note="验收操作员接受该审校处理")
                record["action"] = "accept_resolution"
            resolved.append(record)
        return resolved

    @classmethod
    def _missing_review_indexes(cls, core, job_id: str) -> list:
        """Expected segments the reviewer has not produced any receipt for yet.

        This is the completion signal for a review pass: while the worker is
        still running, the segments it has not reached are ``missing``.  A
        ``stale`` or ``failed`` receipt is terminal for the pass and is what the
        retry loop below exists for, so it must not keep the wait spinning.
        """
        readiness = cls._review_readiness(core, job_id)
        return sorted(readiness.get("missing_segment_ids") or [])

    @classmethod
    def _pending_review_indexes(cls, core, job_id: str) -> list:
        """Segments the delivery review gate still cannot accept as current."""
        readiness = cls._review_readiness(core, job_id)
        return sorted(
            set(readiness.get("missing_segment_ids") or [])
            | set(readiness.get("stale_segment_ids") or [])
            | set(readiness.get("failed_segment_ids") or [])
        )

    # -- the flow ----------------------------------------------------------
    def _job_settled(self, adapter, core, job_id: str) -> bool:
        runtime = core.load_runtime_state(job_id) or {}
        status = runtime.get("status")
        if status in ("failed", "stalled", "interrupted", "cancelled"):
            return True
        if status in core.RUNTIME_ACTIVE_STATUSES:
            return False
        return bool((adapter.load_state(job_id) or {}).get("p2_done"))

    def _check_pdf_source(self, adapter, job_id: str, state: dict) -> dict:
        """Exercise the bundled renderer and the workbench's real source action."""
        from PySide6.QtGui import QImage

        if not adapter.pdf_available(job_id):
            raise AssertionError("PDF source is unavailable (check bundled PyMuPDF)")
        if self.window.workbench.view_source_button.isHidden():
            raise AssertionError("PDF source action is hidden in the workbench")
        pages = adapter.pdf_page_count(job_id)
        if pages < 1:
            raise AssertionError("PDF source has no readable pages")
        png = adapter.pdf_page_png(job_id, 0)
        if not png or QImage.fromData(png, "PNG").isNull():
            raise AssertionError("PDF page 0 did not render to a valid PNG")
        location = adapter.segment_pdf_location(job_id, 0, state=state)
        page = location.get("page")
        if page is None or not 0 <= page < pages:
            raise AssertionError("PDF segment 0 did not resolve to a source page")
        # The text-layer fixture must retain real highlight geometry. Online
        # OCR may legitimately locate a page without line coordinates.
        if state.get("filename") == "selftest.pdf" and not location.get("rects"):
            raise AssertionError("Text PDF segment 0 has no source highlight")
        return {"job_id": job_id, "pages": pages, "png_bytes": len(png),
                "segment_page": page, "highlight_rects": len(location.get("rects") or [])}

    def run(self, adapter, core, fixtures: list[Path]) -> dict:
        report = self.report
        report["data_dir"] = str(adapter.desktop_paths.resolve_paths().data_dir)
        # Which preferences the run actually used: "user" means the machine's
        # own QSettings, so the task defaults were not the probe's.
        report["settings_isolation"] = (
            os.environ.get("FOLITH_QT_SETTINGS_DIR") or "user")
        report["fixtures"] = [str(item) for item in fixtures]
        self.start_heartbeat()
        try:
            self.phase = "import"
            self.window.import_paths([str(item) for item in fixtures],
                                     autostart=False)
            # Wait for the import *task* to finish, not merely for a job
            # directory to appear: the directory is created before the source is
            # written, and starting the pipeline in that window is a race.
            ok = self.pump(
                120, lambda: not self.window.runner.busy
                and len(adapter.list_jobs()) >= len(fixtures) and all(
                    adapter.core.load_source(row.job_id)
                    for row in adapter.list_jobs()))
            self.step("imported", jobs=len(adapter.list_jobs()), ok=ok)
            if not ok:
                raise AssertionError("导入没有产生任务")

            job_ids = [row.job_id for row in adapter.list_jobs()]
            report["job_ids"] = job_ids
            job_id = job_ids[0]
            open_job_and_wait(self.window, job_id)
            self.step("opened", job_id=job_id)

            self.phase = "run"
            self.window.start_or_resume()

            # Product-only responsiveness window: for a few seconds the driver
            # does nothing but drive the event loop, so the measured gaps are
            # the window's own work (the 1 s poll, list/state refresh) rather
            # than this script's polling.  Both numbers are reported, labelled.
            quiet = {"ticks": 0, "max_gap_ms": 0.0, "last": time.monotonic()}
            quiet_deadline = time.monotonic() + 3.0
            while time.monotonic() < quiet_deadline:
                self.app.processEvents()
                now = time.monotonic()
                gap_ms = (now - quiet["last"]) * 1000
                quiet["max_gap_ms"] = max(quiet["max_gap_ms"], gap_ms)
                quiet["last"] = now
                quiet["ticks"] += 1
                time.sleep(0.01)
            report["heartbeat_quiet_window"] = {
                "seconds": 3.0,
                "ticks": quiet["ticks"],
                "max_gap_ms": round(quiet["max_gap_ms"], 1),
                "note": "driver only pumps the event loop; measures the window's own work",
            }
            quiet["last"] = time.monotonic()

            def settled() -> bool:
                # Poll the *lightweight* runtime state: parsing the full job
                # state (57 ms on a 400-segment job) every 20 ms in the GUI
                # thread would itself show up as unresponsiveness and hide the
                # UI's real cost.  The full state is read once, after settling.
                return self._job_settled(adapter, core, job_id)

            done = self.pump(self.timeout, settled)
            state = adapter.load_state(job_id)
            runtime = core.get_job_runtime_status(job_id, state)
            report["run"] = {
                "settled": done,
                "runtime_status": runtime.get("status"),
                "error": runtime.get("error"),
                "p1_done": bool(state.get("p1_done")),
                "p2_done": bool(state.get("p2_done")),
                "segments": len(state.get("pairs") or []),
                "reviewed": sum(1 for pair in state.get("pairs") or []
                                if pair.get("reviewed")),
                "source_lang": state.get("source_lang"),
                "ocr_language": state.get("ocr_language"),
                "ocr_config": state.get("ocr_config"),
                "paras": [str(item if isinstance(item, str)
                              else (item or {}).get("text") or "")[:80]
                          for item in (state.get("paras") or [])][:5],
            }
            if os.environ.get("FOLITH_QT_SELFTEST_OCR") == "1":
                report["ocr"] = ocr_facts(core)
            self.step("translated", **report["run"])
            if not report["run"]["p1_done"] or not report["run"]["p2_done"]:
                # Surface the concrete reason instead of a bare "not finished":
                # a blocked extraction and a slow model must be distinguishable.
                raise AssertionError(f"任务未完成：{report['run']}")

            # `both`, used by the frozen macOS/Windows probes, imports DOCX
            # first. Run its PDF too: checking only the primary task would
            # silently skip the bundled PDF source path again.
            report["pdf_sources"] = []
            for pdf_id in job_ids:
                pdf_state = adapter.load_state(pdf_id)
                if Path(str(pdf_state.get("filename") or "")).suffix.lower() != ".pdf":
                    continue
                self.phase = "pdf_source"
                open_job_and_wait(self.window, pdf_id)
                if not pdf_state.get("p2_done"):
                    self.window.start_or_resume()
                    self.pump(self.timeout, lambda: self._job_settled(adapter, core, pdf_id))
                    pdf_state = adapter.load_state(pdf_id)
                    if not pdf_state.get("p2_done"):
                        raise AssertionError(f"PDF fixture did not finish: {core.load_runtime_state(pdf_id)}")
                    # Refresh the workbench with the extracted source anchors.
                    open_job_and_wait(self.window, pdf_id)
                facts = self._check_pdf_source(adapter, pdf_id, pdf_state)
                report["pdf_sources"].append(facts)
                self.step("pdf_source_verified", **facts)
            open_job_and_wait(self.window, job_id)

            self.phase = "edit"
            rows = adapter.segment_rows(job_id, state)
            index = 1 if len(rows) > 1 else 0
            uid = rows[index].segment_uid
            # A person editing a real document edits *the translation*.  Writing
            # fixture placeholder text over a real paragraph would make the
            # reviewer flag the harness's own defect as a blocking finding, and
            # the delivery gate would then (correctly) refuse to freeze.
            typed = str(rows[index].target or "").strip() or TYPED
            self.window.workbench.select_segment(index)
            self.app.processEvents()
            self.window.workbench.set_editor_text_for_test(typed)
            self.app.processEvents()
            self.window.workbench.flush_draft()
            drafts = core.load_translation_drafts(job_id)
            self.step("draft-saved", segment=uid, drafts=len(drafts),
                      text_matches=bool(drafts.get(uid, {}).get("text") == typed))

            self.window.submit_edit(index)
            self.pump(15)
            state = adapter.load_state(job_id)
            pair = (state.get("pairs") or [])[index]
            report["edit"] = {
                "index": index,
                "target": pair.get("target"),
                "human_edited": bool(pair.get("human_edited")),
                "provenance": pair.get("target_provenance"),
                "drafts_left": len(core.load_translation_drafts(job_id)),
            }
            self.step("edit-submitted", **report["edit"])
            if pair.get("target") != typed or not pair.get("human_edited"):
                raise AssertionError(f"人工修改未提交：{report['edit']}")

            self.phase = "review"
            self.window.review_all()
            # The budget is the flow timeout, not a fixed 300 s: an independent
            # reviewer call per segment is the normal cost, and a real
            # document's review legitimately runs longer than a fixed guess.
            # Giving up early would hand the delivery gate a half-reviewed book
            # and blame the product for a harness timeout.
            settled = self._await_review(core, job_id)
            # Two things a person does after a review, both bounded and both
            # reported here:
            #
            # * answer the review queue — a blocking finding is the reviewer
            #   asking for a human decision, and the operator either applies the
            #   reviewer's own replacement in the editor or accepts the
            #   resolution through the product's decision path;
            # * press 审校 again for the segments the gate still cannot accept
            #   (a rejected round, or a segment whose receipt an edit made
            #   stale).
            report["review_retries"] = []
            report["resolved_findings"] = []
            for _ in range(3):
                report["resolved_findings"].extend(
                    self._resolve_open_blocking_findings(core, adapter, job_id))
                pending = self._pending_review_indexes(core, job_id)
                if not pending or not settled:
                    break
                report["review_retries"].append(pending)
                self.window.review_selected(pending)
                settled = self._await_review(core, job_id)
            state = adapter.load_state(job_id)
            report["review"] = {
                "reviewed": sum(1 for item in state.get("pairs") or []
                                if item.get("reviewed")),
                "total": len(state.get("pairs") or []),
                "settled": settled,
                "pending_after_retries": self._pending_review_indexes(core, job_id),
                "open_blocking": [
                    finding.get("finding_id")
                    for finding in self._open_blocking_findings(core, job_id)
                ],
            }
            self.step("reviewed", **report["review"])

            self.phase = "deliver"
            self.window.deliver()
            # `approve_delivery` writes the snapshot files *before* the final
            # state, so wait for both: otherwise the report can read
            # delivery_status=draft for a delivery that did succeed.
            # Snapshot files are cheap to list; the final state needs a full
            # parse, so it is polled coarsely.
            completed = self.pump(self.timeout, lambda: not self.window.runner.busy)
            if not completed or not adapter.delivery_snapshots(job_id):
                raise AssertionError(f"交付未生成快照: {self.window.failures}")
            self.pump(max(120.0, self.timeout / 4),
                      lambda: adapter.load_state(job_id).get("delivery_status")
                      == "final", interval=0.5)
            snapshots = adapter.delivery_snapshots(job_id)
            latest = snapshots[-1] if snapshots else {}
            report["delivery"] = {
                "snapshots": [item.get("snapshot_version") for item in snapshots],
                "assets": sorted(asset.get("name")
                                 for asset in latest.get("assets") or []),
                "delivery_status": adapter.load_state(job_id).get("delivery_status"),
                "current": adapter.delivery_preview(job_id).get(
                    "snapshot", {}).get("current"),
            }
            self.step("delivered", **report["delivery"])
            if not snapshots:
                raise AssertionError("没有生成交付快照")

            self.phase = "save"
            export_dir = self.out_dir / "交付 结果 中文"
            written = adapter.copy_delivery_assets(
                job_id, int(latest.get("snapshot_version")), export_dir)
            report["saved_files"] = [str(path) for path in written]
            self.step("saved", count=len(written))

            if self.screenshot:
                self.window.grab().save(self.screenshot)
                report["screenshot"] = self.screenshot
                self.step("screenshot", path=self.screenshot)

            self.phase = "exit"
            before = adapter.exit_report(job_id)
            report["exit_before"] = {key: before.get(key) for key in
                                     ("safe_to_exit", "draft_count",
                                      "worker_lock_held", "active_jobs")}
            self.window.close()
            closed = self.pump(120, lambda: self.window._closing_accepted)
            report["exit"] = {"closing_accepted": bool(closed),
                              "failures": list(self.window.failures)}
            self.step("exit", **report["exit"])
            if not closed:
                raise AssertionError("窗口拒绝正常退出")

            reopened = adapter.segment_rows(job_id)
            report["reopen"] = {
                "target": reopened[index].target if index < len(reopened) else None,
                "human_edited": reopened[index].human_edited
                if index < len(reopened) else None,
                "snapshots": len(adapter.delivery_snapshots(job_id)),
            }
            self.step("reopened", **report["reopen"])
            if index < len(reopened) and reopened[index].target != typed:
                raise AssertionError("重开后人工修改丢失")

            report["ok"] = True
        except Exception as exc:  # noqa: BLE001 - the report *is* the evidence
            import traceback

            report["ok"] = False
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()
        finally:
            self.stop_heartbeat()
            report["heartbeat"] = {
                "ticks": self.heartbeat["ticks"],
                "max_gap_ms": round(self.heartbeat["max_gap_ms"], 1),
                "interval_ms": 50,
                "worst_gaps": self._worst_gaps,
            }
            report["failures"] = list(self.window.failures)
            report["finished_at"] = time.time()
            report["duration_s"] = round(
                report["finished_at"] - report["started_at"], 1)
        return report


def engine_from_env(adapter, base=None):
    """Build the engine config for a scripted run.

    The controlled endpoint's URL must be supplied by the caller; the client
    never invents a model connection.  ``base`` is the config the window loaded
    from the client's own preferences: the scripted run must keep the user's
    delivery/task choices (which formats to freeze), otherwise it would verify a
    delivery set nobody asked for.  Only the connection and the flow switches
    are overridden here.
    """
    import dataclasses

    url = os.environ.get("FOLITH_QT_SELFTEST_BASE_URL", "").strip()
    if not url:
        raise SystemExit("FOLITH_QT_SELFTEST_BASE_URL is required for --selftest")
    language = os.environ.get("FOLITH_QT_SELFTEST_OCR_LANGUAGE", "").strip()
    mode = os.environ.get("FOLITH_QT_SELFTEST_OCR_MODE", "auto").strip() or "auto"
    connection = dict(
        provider=os.environ.get("FOLITH_QT_SELFTEST_PROVIDER", "自定义中转站"),
        model=os.environ.get("FOLITH_QT_SELFTEST_MODEL", "stub-model"),
        api_key=os.environ.get("FOLITH_QT_SELFTEST_API_KEY", "stub-key"),
        base_url=url,
        target_lang="简体中文",
        enable_review=True,
        enable_report=False,
        enable_understanding=False,
        enable_source_cleanup=False,
        ocr_language=language,
        ocr_mode=mode,
    )
    # The existing bundled-OCR baseline flag explicitly selects the local
    # engine; scanned jobs otherwise correctly use the normal VLM default.
    if os.environ.get("FOLITH_QT_SELFTEST_OCR") == "1":
        connection["ocr_backend"] = "tesseract"
        # This probe checks scanned text and bundled language packs, so its
        # delivery must not request formats that need a native text source.
        connection["deliver_epub"] = False
        connection["deliver_original_pdf"] = False
    if base is None:
        return adapter.EngineConfig(**connection)
    return dataclasses.replace(base, **connection)


def write_report(report: dict, path: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    # The console copy is diagnostic only.  A frozen Windows GUI has a GBK
    # stdio stream; printing Arabic report fields would raise UnicodeEncodeError
    # after the report was written and leave a PyInstaller error dialog blocking
    # process exit.  The file keeps the real Unicode text; the console gets
    # ASCII escapes that every code page can encode.
    print(json.dumps(report, ensure_ascii=True, indent=2, default=str), flush=True)
