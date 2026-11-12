"""Recover completed annotation candidates across process restarts.

Records are derived task data, replayed only for the same generation request
and successful normalized schema. They do not provide exactly-once remote
execution or a host-power-loss guarantee for the authoritative state snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping, Optional, Sequence

from folith.storage.atomic_file import read_shared_text, replace_file
from folith.workflow_errors import AnnotationStagingError


STAGING_DIR_NAME = ".annotation_staging"
STAGING_SCHEMA_VERSION = 2
TEMP_PREFIX = ".candidate-"


class AnnotationFailStop:
    """First storage error shared by one annotation execution and its workers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._error = None

    def fail(self, error):
        with self._lock:
            if self._error is None:
                self._error = error

    def check(self):
        with self._lock:
            error = self._error
        if error is not None:
            raise error


def compute_batch_fingerprint(
    pairs_slice: Sequence[Mapping[str, Any]],
    target_lang: str,
    model: str,
    *,
    eligible_indexes: Optional[Sequence[int]] = None,
    request_context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Bind actual eligible inputs, task mapping and effective request contract.

    Only this digest is persisted. Context must not contain credentials; runtime
    callers supply the effective route and exact prompts, not an entire config.
    """
    indexes = list(range(len(pairs_slice))) if eligible_indexes is None else list(eligible_indexes)
    payload = {
        "schema": STAGING_SCHEMA_VERSION,
        "target_lang": target_lang,
        "model": model,
        "inputs": [{"local_index": i, "source": pairs_slice[i].get("source"),
                    "target": pairs_slice[i].get("target"),
                    "segment_id": pairs_slice[i].get("segment_id"),
                    "id": pairs_slice[i].get("id")} for i in indexes],
        "request": dict(request_context or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _get_staging_dir(job_id: Optional[str], job_dir_path: Optional[Path] = None) -> Optional[Path]:
    if job_dir_path is not None:
        return Path(job_dir_path) / STAGING_DIR_NAME
    if not job_id:
        return None
    try:
        import core
        return core.job_dir(job_id) / STAGING_DIR_NAME
    except Exception:  # noqa: BLE001
        return None


def save_staged_annotation(
    job_id: Optional[str],
    offset: int,
    bi: int,
    items: Any,
    fingerprint: str,
    *,
    job_dir_path: Optional[Path] = None,
) -> bool:
    """Atomically stage a completed annotation result to disk."""
    staging_dir = _get_staging_dir(job_id, job_dir_path)
    if staging_dir is None:
        return False
    temp_path = None
    write_error = None
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        status = getattr(items, "status", "success" if items is not None else "empty")
        if status not in {"success", "empty"}:
            return False

        serialized_items = []
        if items is not None:
            for item in items:
                if isinstance(item, Mapping):
                    serialized_items.append(dict(item))

        payload = {
            "schema": STAGING_SCHEMA_VERSION,
            "offset": int(offset),
            "batch_id": int(bi),
            "fingerprint": str(fingerprint),
            "status": str(status),
            "error": "",
            "items": serialized_items,
        }

        target_file = staging_dir / f"{offset}.json"
        with NamedTemporaryFile("w", dir=str(staging_dir), prefix=TEMP_PREFIX,
                                delete=False, encoding="utf-8") as tf:
            temp_path = Path(tf.name)
            json.dump(payload, tf, ensure_ascii=False)
            tf.flush()
            os.fsync(tf.fileno())
        replace_file(temp_path, target_file)
        return True
    except (OSError, TypeError, ValueError) as error:
        write_error = error
        raise AnnotationStagingError(error) from error
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as error:
                # Cleanup denial must not replace the original write failure.
                if write_error is None:
                    raise AnnotationStagingError(error) from error


def load_staged_annotation(
    job_id: Optional[str],
    offset: int,
    expected_fingerprint: str,
    *,
    job_dir_path: Optional[Path] = None,
) -> Optional[Any]:
    """Load a staged annotation result if its fingerprint matches current inputs."""
    staging_dir = _get_staging_dir(job_id, job_dir_path)
    if staging_dir is None:
        return None
    target_file = staging_dir / f"{offset}.json"
    if not target_file.is_file():
        return None
    try:
        data = json.loads(read_shared_text(target_file))
        if not isinstance(data, dict):
            return None
        if (data.get("schema") != STAGING_SCHEMA_VERSION
                or data.get("offset") != offset
                or data.get("status") not in {"success", "empty"}
                or not isinstance(data.get("items"), list)
                or any(not isinstance(item, dict) for item in data.get("items", []))
                or data.get("fingerprint") != expected_fingerprint):
            # Inputs (e.g. target translation) changed: discard invalid staged result.
            try:
                target_file.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return None

        import core
        items = data.get("items")
        if any(not isinstance(item.get("seg"), int)
               or isinstance(item.get("seg"), bool) or item["seg"] < 0
               or item.get("type") not in core.ANNOTATION_COLORS
               or any(not isinstance(item.get(key), str)
                      for key in ("src", "tgt", "note"))
               for item in items):
            target_file.unlink(missing_ok=True)
            return None
        status = data.get("status", "success")
        error = data.get("error", "")
        return core.AnnotationBatchResult(items, status=status, error=error)
    except Exception:  # noqa: BLE001
        return None


def confirm_staged_annotation(
    job_id: Optional[str],
    offset: int,
    *,
    job_dir_path: Optional[Path] = None,
) -> None:
    """Consumer confirmation: remove staged file once committed to durable job state."""
    staging_dir = _get_staging_dir(job_id, job_dir_path)
    if staging_dir is None:
        return
    target_file = staging_dir / f"{offset}.json"
    try:
        target_file.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def clear_staged_annotation(
    job_id: Optional[str],
    offset: int,
    *,
    job_dir_path: Optional[Path] = None,
) -> None:
    """Invalidate and remove a specific staged batch (e.g. when segment truth changes)."""
    confirm_staged_annotation(job_id, offset, job_dir_path=job_dir_path)


def cleanup_staged_temporaries(job_id: Optional[str]) -> None:
    """Sweep interrupted-write files before starting workers, under job ownership."""
    staging_dir = _get_staging_dir(job_id)
    if staging_dir is None or not staging_dir.is_dir():
        return
    for path in staging_dir.iterdir():
        if path.is_file() and path.suffix != ".json":
            path.unlink(missing_ok=True)


def clear_all_staged_annotations(
    job_id: Optional[str],
    *,
    job_dir_path: Optional[Path] = None,
) -> None:
    """Remove the staging directory after successful stage completion."""
    staging_dir = _get_staging_dir(job_id, job_dir_path)
    if staging_dir is None or not staging_dir.is_dir():
        return
    try:
        # Called under task ownership, after workers have joined. Legacy temp
        # names are included: this directory contains only derived stage data.
        for f in staging_dir.iterdir():
            if not f.is_file():
                continue
            try:
                f.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        staging_dir.rmdir()
    except Exception:  # noqa: BLE001
        pass
