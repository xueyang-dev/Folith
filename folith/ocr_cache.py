"""Page-granular durable OCR cache.

Why this replaces ``ocr_checkpoint.json``
-----------------------------------------
The previous checkpoint serialised **every** page record it had ever seen and
rewrote the whole JSON after each successful page.  For a 120-page scan that is
O(n²) bytes written: page 120 re-writes pages 1..120.  It also keyed validity on
the *requested index set*, so the smart-profile pass (pages 0 / mid / last) could
never be reused by the full pass (all pages) even when the OCR work was byte for
byte identical.

This cache is page-granular and self-verifying:

* one small file per page under ``pages/page-<index>-<key>.json`` — a successful
  page persists only that page;
* ``manifest.json`` is a constant-size run record (identity + requested indices
  + status) written at run start and run end, never per page;
* the cache identity includes the document SHA-256, page index, OCR
  backend / model / version, language, DPI and preprocessing version, so a result
  produced under different conditions is never silently reused;
* each page file embeds its own identity key and page index, so a stale or
  foreign file cannot be attributed to the wrong page ("no cross-file mixing").

Recovery is by construction: a page file either exists with a matching key or it
does not, so restoring keeps input order, never duplicates a page and never
substitutes another page's text.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

OCR_CACHE_VERSION = 3
OCR_PREPROCESSING_VERSION = 1
#: Bumped when the OCR configuration contract changes shape, so a page produced
#: under an older contract is never silently reused.
OCR_CONFIG_VERSION = 1

#: The one legacy identity that ``ocr_checkpoint.json`` could ever have produced.
#: Reusing those pages is not a guess: the legacy code path hard-coded exactly
#: these values.  Anything else must be recomputed.
LEGACY_OCR_IDENTITY = {
    "backend": "tesseract",
    "model": "tesseract-lstm",
    "dpi": 220,
    "preprocessing_version": 1,
}

MANIFEST_FILE = "manifest.json"
PAGES_DIRNAME = "pages"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OCRPageIdentity:
    """Everything that can change the bytes of one OCR page result."""

    source_sha256: str
    page_index: int
    backend: str
    model: str
    backend_version: str
    language: str
    dpi: int
    preprocessing_version: int = OCR_PREPROCESSING_VERSION
    #: Page segmentation mode and the declared script direction are part of the
    #: result identity: the same page OCR'd with ``--psm 6`` or read as RTL is a
    #: different result and must not be served from another configuration.
    psm: int = 3
    direction: str = "ltr"
    ocr_config_version: int = OCR_CONFIG_VERSION
    #: Non-secret descriptor of backend-specific configuration that changes the
    #: bytes of a page: the selected Paddle model set, or the online service
    #: target + model + prompt/parameter version.  Empty means "the historical
    #: default", and is deliberately *omitted* from the key material so pages
    #: cached by the pre-existing Tesseract configuration remain reusable.
    backend_options: str = ""

    def key(self) -> str:
        material = {
            "source_sha256": self.source_sha256,
            "page_index": int(self.page_index),
            "backend": self.backend,
            "model": self.model,
            "backend_version": self.backend_version,
            "language": self.language,
            "dpi": int(self.dpi),
            "preprocessing_version": int(self.preprocessing_version),
            "psm": int(self.psm),
            "direction": str(self.direction),
            "ocr_config_version": int(self.ocr_config_version),
        }
        if str(self.backend_options or ""):
            material["backend_options"] = str(self.backend_options)
        return _sha256_text(json.dumps(material, ensure_ascii=False, sort_keys=True))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "page_index": int(self.page_index),
            "backend": self.backend,
            "model": self.model,
            "backend_version": self.backend_version,
            "language": self.language,
            "dpi": int(self.dpi),
            "preprocessing_version": int(self.preprocessing_version),
            "psm": int(self.psm),
            "direction": str(self.direction),
            "ocr_config_version": int(self.ocr_config_version),
            "backend_options": str(self.backend_options or ""),
        }


class OCRPageCache:
    """Manifest + per-page atomic files."""

    def __init__(self, directory: Optional[Path | str]) -> None:
        self.directory = Path(directory) if directory else None
        self.pages_dir = self.directory / PAGES_DIRNAME if self.directory else None
        self.manifest_path = self.directory / MANIFEST_FILE if self.directory else None
        # Created on first write only: a redundant mkdir per page is a real
        # syscall cost on the OCR hot path for no benefit.
        self._pages_dir_ready = False
        self._stats = {
            "page_hits": 0,
            "page_writes": 0,
            "page_bytes_written": 0,
            "manifest_writes": 0,
            "manifest_bytes_written": 0,
            "identity_mismatches": 0,
            "corrupt_page_files": 0,
        }

    # -- stats ------------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def bytes_written(self) -> int:
        return self._stats["page_bytes_written"] + self._stats["manifest_bytes_written"]

    # -- paths ------------------------------------------------------------
    def page_path(self, identity: OCRPageIdentity) -> Path:
        return self.pages_dir / (
            f"page-{int(identity.page_index):06d}-{identity.key()[:12]}.json")

    # -- load -------------------------------------------------------------
    def load(
        self,
        *,
        source_sha256: str,
        indices: Sequence[int],
        language: str,
        backend: str,
        model: str,
        backend_version: str,
        dpi: int,
        preprocessing_version: int = OCR_PREPROCESSING_VERSION,
        psm: int = 3,
        direction: str = "ltr",
        ocr_config_version: int = OCR_CONFIG_VERSION,
        backend_options: str = "",
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """Return ``({str(index): record}, manifest)`` for the reusable pages.

        Only pages whose embedded identity key matches the *current* identity are
        returned.  A page produced at a different DPI / backend / version is
        skipped rather than guessed at.
        """
        manifest = self._read_manifest()
        if manifest and manifest.get("version") != OCR_CACHE_VERSION:
            manifest = {}
        if manifest:
            if manifest.get("source_sha256") != source_sha256:
                manifest = {}
            elif manifest.get("language") != language:
                manifest = {}
            elif (manifest.get("backend"), manifest.get("model"),
                  str(manifest.get("backend_version") or ""),
                  int(manifest.get("dpi") or 0),
                  int(manifest.get("preprocessing_version") or 0),
                  int(manifest.get("psm") or 0),
                  str(manifest.get("direction") or ""),
                  int(manifest.get("ocr_config_version") or 0),
                  str(manifest.get("backend_options") or "")) != (
                      backend, model, str(backend_version or ""), int(dpi),
                      int(preprocessing_version), int(psm), str(direction),
                      int(ocr_config_version), str(backend_options or "")):
                manifest = {}
        if self.pages_dir is None or not self.pages_dir.is_dir():
            return {}, manifest or {}
        records: Dict[str, Dict[str, Any]] = {}
        for index in indices:
            identity = OCRPageIdentity(
                source_sha256=source_sha256, page_index=int(index),
                backend=backend, model=model, backend_version=backend_version,
                language=language, dpi=int(dpi),
                preprocessing_version=int(preprocessing_version),
                psm=int(psm), direction=str(direction),
                ocr_config_version=int(ocr_config_version),
                backend_options=str(backend_options or ""))
            path = self.page_path(identity)
            if not path.is_file():
                continue
            payload = self._read_page_file(path)
            if payload is None:
                self._stats["corrupt_page_files"] += 1
                continue
            if payload.get("key") != identity.key():
                self._stats["identity_mismatches"] += 1
                continue
            try:
                if int(payload.get("page_index", -1)) != int(index):
                    self._stats["identity_mismatches"] += 1
                    continue
            except (TypeError, ValueError):
                self._stats["identity_mismatches"] += 1
                continue
            record = payload.get("record")
            if not isinstance(record, dict):
                continue
            records[str(index)] = record
            self._stats["page_hits"] += 1
        return records, manifest or {}

    # -- store ------------------------------------------------------------
    def store_page(self, identity: OCRPageIdentity, record: Dict[str, Any]) -> Optional[Path]:
        """Atomically persist one page. Only this page's bytes are written."""
        if self.pages_dir is None:
            return None
        payload = {
            "version": OCR_CACHE_VERSION,
            "key": identity.key(),
            "page_index": int(identity.page_index),
            "completed_at": _now_iso(),
            "identity": identity.to_dict(),
            "record": record,
        }
        text = json.dumps(payload, ensure_ascii=False)
        path = self.page_path(identity)
        try:
            if not self._pages_dir_ready:
                self.pages_dir.mkdir(parents=True, exist_ok=True)
                self._pages_dir_ready = True
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return None
        self._stats["page_writes"] += 1
        self._stats["page_bytes_written"] += len(text.encode("utf-8"))
        return path

    def write_manifest(
        self,
        *,
        source_sha256: str,
        indices: Sequence[int],
        language: str,
        backend: str,
        model: str,
        backend_version: str,
        dpi: int,
        status: str,
        completed_pages: int = 0,
        failed_pages: int = 0,
        preprocessing_version: int = OCR_PREPROCESSING_VERSION,
        psm: int = 3,
        direction: str = "ltr",
        ocr_config_version: int = OCR_CONFIG_VERSION,
        backend_options: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Write the constant-size run record (never one entry per page)."""
        if self.directory is None:
            return None
        manifest = {
            "version": OCR_CACHE_VERSION,
            "source_sha256": source_sha256,
            "language": language,
            "backend": backend,
            "model": model,
            "backend_version": str(backend_version or ""),
            "dpi": int(dpi),
            "preprocessing_version": int(preprocessing_version),
            "psm": int(psm),
            "direction": str(direction),
            "ocr_config_version": int(ocr_config_version),
            "backend_options": str(backend_options or ""),
            "requested_indices": [int(index) for index in indices],
            "requested_page_count": len(list(indices)),
            "completed_pages": int(completed_pages),
            "failed_pages": int(failed_pages),
            "status": str(status or "running"),
            "updated_at": _now_iso(),
        }
        if extra:
            manifest.update(extra)
        text = json.dumps(manifest, ensure_ascii=False, indent=2)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.manifest_path.with_name(self.manifest_path.name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(self.manifest_path)
        except OSError:
            return None
        self._stats["manifest_writes"] += 1
        self._stats["manifest_bytes_written"] += len(text.encode("utf-8"))
        return manifest

    # -- internals --------------------------------------------------------
    def _read_manifest(self) -> Dict[str, Any]:
        if self.manifest_path is None or not self.manifest_path.is_file():
            return {}
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _read_page_file(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None


def read_legacy_checkpoint_pages(
    path: Optional[Path | str],
    *,
    source_sha256: str,
    indices: Iterable[int],
    language: str,
    backend: str,
    model: str,
    dpi: int,
    preprocessing_version: int = OCR_PREPROCESSING_VERSION,
    psm: int = 3,
    direction: str = "ltr",
    ocr_config_version: int = OCR_CONFIG_VERSION,
) -> Dict[str, Dict[str, Any]]:
    """Adopt pages from a v1 ``ocr_checkpoint.json`` — only when identity matches.

    The legacy format had no backend/DPI field, so it is only trusted when the
    caller is running the exact identity that format could have produced.  Any
    other combination returns nothing instead of guessing.
    """
    if (str(backend) != LEGACY_OCR_IDENTITY["backend"]
            or str(model) != LEGACY_OCR_IDENTITY["model"]
            or int(dpi) != int(LEGACY_OCR_IDENTITY["dpi"])
            or int(preprocessing_version) != int(
                LEGACY_OCR_IDENTITY["preprocessing_version"])):
        return {}
    # The legacy format also predates PSM / direction / OCR config versioning;
    # it could only have been produced with the historical defaults.
    if int(psm) != 3 or str(direction) != "ltr" or int(ocr_config_version) < 1:
        return {}
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("version") != 1:
        return {}
    if value.get("source_sha256") != source_sha256 or value.get("language") != language:
        return {}
    if value.get("indices") != list(indices):
        return {}
    pages = value.get("pages")
    if not isinstance(pages, dict):
        return {}
    return {str(key): record for key, record in pages.items()
            if isinstance(record, dict)}
