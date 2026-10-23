"""Legacy runtime reversal for C1-C4; not a complete baseline by itself.

The authoritative baseline is a tree produced by
``python -m benchmarks.performance.revert_to_baseline <target-dir>``, which
reverses all six optimisations including C5/C6 at source level and writes a
``.folith-baseline-reverted`` marker.  The runner refuses ``--baseline`` on a
tree without that marker because the patches below only cover C1-C4.  On a
marked tree this module is idempotent and remains applied for continuity.

The benchmark has to measure the *same* application twice.  Re-checking out an
older revision is not possible in this working copy (there is no VCS here), so
the "before" behaviour is re-installed at runtime by patching the few places
the optimisation touched:

* the batch-progress runtime status goes back to one durable write per phase;
* the runtime event log is fsync'd on every record again;
* the frozen-delivery identity is computed from a deep copy again;
* the workbench computes its derived projections on the GUI thread again.

C5 (async home poll) and C6 (segment-table append reuse) are **not** reversed
here; they are reversed by ``revert_to_baseline.py``.
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Dict

_INSTALLED = False


def install() -> None:
    """Patch the process back to the pre-optimisation behaviour."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    import core
    from folith import snapshots
    from folith.engine import delivery_state
    from folith.qt import adapter
    from folith.qt import views

    # 1) Every batch phase writes the runtime status again.
    def _submit_runtime_status(job_id, **changes):
        return core.update_runtime_state(job_id, **changes)

    core._submit_runtime_status = _submit_runtime_status

    # 2) Every runtime event is fsync'd again.
    original_append = core._append_runtime_event

    def _append_runtime_event(job_id, event, *, stage=None, operation=None,
                              metadata=None, visibility=None, category=None,
                              sync=True, event_name=None):
        name = event_name or (event.get("event") if isinstance(event, dict) else None)
        if not name:
            name = core._runtime_event_code(
                event.get("message") if isinstance(event, dict) else event)
        visibility, category = core._runtime_event_meta(name, visibility, category)
        record = {
            "timestamp": core._utc_now_iso(),
            "job_id": job_id,
            "pipeline": event.get("pipeline") if isinstance(event, dict) else None,
            "stage": stage or "",
            "operation": operation or "",
            "event": name,
            "message": event.get("message") if isinstance(event, dict) else str(event),
            "visibility": visibility,
            "category": category,
        }
        if record["pipeline"] is None:
            record["pipeline"] = "document_pipeline"
        if metadata:
            record["metadata"] = metadata
        path = core.runtime_events_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        with core._RUNTIME_PERSIST_LOCK:
            core._RUNTIME_PERSIST_STATS["event_appends"] += 1
            core._RUNTIME_PERSIST_STATS["fsyncs"] += 1
            core._RUNTIME_PERSIST_STATS["event_append_seconds"] += (
                time.perf_counter() - started)
        return record

    core._append_runtime_event = _append_runtime_event

    # 3) The frozen-delivery identity is computed from a deep copy again.
    def _state_identity(state: Dict[str, Any]) -> str:
        value = copy.deepcopy(state)
        for key in ("_source_bin", "delivery_snapshots",
                    "latest_delivery_snapshot_version", "final_qa",
                    "dependency_impact"):
            value.pop(key, None)
        academic = value.get("academic_state")
        if isinstance(academic, dict):
            academic.pop("artifact_status", None)
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        return snapshots._sha256(payload.encode("utf-8"))

    snapshots.state_identity = _state_identity
    delivery_state._snapshots.state_identity = _state_identity

    # 4) The frozen-delivery status reads (and hashes) the snapshot assets.
    def _delivery_snapshot_status(job_id, state=None):
        latest = delivery_state._snapshots.latest_snapshot(core.job_dir(job_id))
        if latest is None:
            return {"latest": None, "current": False, "diverged": False,
                    "integrity": False}
        state = state if state is not None else core.load_job_state(job_id)
        current_identity = delivery_state._snapshots.state_identity(state or {})
        matches = current_identity == latest.get("translation_state_identity")
        assets = delivery_state.delivery_snapshot_assets(
            job_id, latest["snapshot_version"])
        integrity = bool(assets) and all(data is not None for data in assets.values())
        return {
            "latest": latest,
            "current": bool(matches and integrity and state
                            and state.get("delivery_status") == "final"),
            "diverged": not matches,
            "integrity": integrity,
        }

    delivery_state.delivery_snapshot_status = _delivery_snapshot_status
    core.delivery_snapshot_status = _delivery_snapshot_status

    # 5) The workbench computes its derived projections on the GUI thread.
    def _workbench_models(job_id, state, **kwargs):
        return {}

    adapter.workbench_models = _workbench_models

    # 6) The delivery panel computes its own preview, on the GUI thread.
    def _render_delivery(self, *, force: bool = False) -> None:
        from folith.qt import i18n
        from folith.qt.views import _elide

        now = time.monotonic()
        key = (self.job_id, len(self._snapshots),
               str(self._state.get("delivery_status") or ""))
        unchanged = self._delivery_rendered and key == self._delivery_key
        if unchanged and not force and (
                now - self._delivery_preview_at) < views.DELIVERY_REFRESH_SECONDS:
            return
        if unchanged and (now - self._delivery_preview_at) < views.DELIVERY_FORCE_FLOOR_SECONDS:
            return
        preview = adapter.delivery_preview(self.job_id, self.config, state=self._state) \
            if self.job_id else {}
        self._delivery_preview = preview
        self._delivery_rendered = True
        self._delivery_preview_at = now
        self._delivery_key = key
        status = i18n.tr_dynamic(preview.get("status_label")) or "—"
        issues = preview.get("issues") or []
        detail = (i18n.tr("；还需处理：{v0}",
                          v0=_elide("；".join(i18n.tr_dynamic(str(i)) for i in issues), 200))
                  if issues
                  else i18n.tr("；交付文件按任务声明生成，冻结后不可修改。"))
        missing = [index for index, pair in enumerate(self._state.get("pairs") or [])
                   if not pair.get("excluded") and not str(pair.get("target") or "").strip()]
        if missing:
            numbers = "、".join(str(index + 1) for index in missing[:20])
            detail += i18n.tr("\n有 {v0} 段尚无译文：第 {v1}{v2} 段。", v0=len(missing), v1=numbers,
                              v2=i18n.tr(" 等") if len(missing) > 20 else "")
        if (preview.get("snapshot") or {}).get("current"):
            delivery_tone = "done"
        elif issues or missing:
            delivery_tone = "attention"
        elif preview.get("can_deliver"):
            delivery_tone = "active"
        else:
            delivery_tone = "idle"
        self.delivery_label.set_status(status, delivery_tone,
                                       detail.strip().lstrip("；;؛、 ").strip())
        self.deliver_button.setEnabled(bool(preview.get("can_deliver")))
        self.risk_button.setVisible(bool(preview.get("can_accept_risk")))
        self.delivery_note.setEnabled(bool(preview.get("can_deliver")
                                           or preview.get("can_accept_risk")))
        snapshot = preview.get("snapshot") or {}
        if snapshot.get("current"):
            self.deliver_button.setText(
                i18n.tr('已冻结交付 v{v0}', v0=snapshot.get('latest', {}).get('snapshot_version')))
        elif preview.get("can_deliver"):
            self.deliver_button.setText(
                i18n.tr('冻结交付（生成 v{v0}）', v0=len(self._snapshots) + 1))
        else:
            self.deliver_button.setText(i18n.tr('审校完成后冻结交付'))
        self.export_button.setEnabled(bool(self._state.get("p2_done")))
        latest = self._snapshots[-1] if self._snapshots else None
        self.save_snapshot_button.setVisible(latest is not None)
        if latest is not None:
            self.save_snapshot_button.setText(
                i18n.tr('保存冻结交付文件 v{v0}…', v0=latest.get("snapshot_version")))
        self.asset_list.clear()
        self._fill_delivery_assets(snapshot.get("latest"), snapshot=snapshot)
        offer = bool(snapshot.get("current") or preview.get("can_deliver"))
        self._fill_supplements(adapter.supplementable_outputs(self.job_id, self._state)
                               if offer and self.job_id else [])

    views.WorkbenchView._render_delivery = _render_delivery
