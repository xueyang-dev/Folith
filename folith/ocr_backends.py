"""OCR backend boundary: one block contract, honest runtime metadata.

Why a boundary at all
---------------------
Folith's OCR was Tesseract-shaped all the way through: the extractor produced
Tesseract TSV dicts, the runtime metadata hard-coded ``device=cpu``, and there
was no place to put an accelerated backend even if one existed.  This module
gives every backend one contract so that swapping engines is a configuration
change rather than a rewrite — while keeping Tesseract as the default local
engine when its executable and requested language data are installed.

The block contract
------------------
Every backend returns :class:`OCRBlock` values carrying the text, a real
``bbox`` (left/top/right/bottom), confidence, the page identity, and the runtime
backend/device/provider that actually produced them.  ``to_dict()`` emits the
legacy mapping so existing downstream code keeps working unchanged.

Honesty rule
------------
``runtime_info()`` reports the device the backend will really execute on.  A
backend must never infer "GPU enabled" from the mere presence of CUDA: the
metadata records the execution provider that was actually selected, and when a
non-CPU device is requested but not available, a visible warning is emitted
instead of a silent claim.
"""
from __future__ import annotations

import inspect
import hashlib
import re
import subprocess
from pathlib import Path
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

TESSERACT_BACKEND = "tesseract"
PADDLEOCR_BACKEND = "paddleocr"
ONLINE_OCR_BACKEND = "online"

#: Values a task may store for the OCR source.  ``auto`` keeps its historical
#: local-only meaning: it prefers Tesseract and falls back to PaddleOCR, and it
#: never silently sends page images to a remote service.
SUPPORTED_BACKENDS = ("auto", "tesseract", "paddle", "online")


from folith.ocr_evidence import OCRBlock, OCRRuntimeInfo, OCRBackend
from folith.ocr_online import ONLINE_OCR_BACKEND as _ONLINE_NAME, OnlineOCRConfig


class TesseractBackend(OCRBackend):
    """Default local engine; preserves the existing TSV recognition logic."""

    name = TESSERACT_BACKEND
    display_name = "Tesseract"

    def __init__(self, command: str, version: str = "",
                 page_ocr: Optional[Callable[..., Any]] = None,
                 psm: Optional[int] = None) -> None:
        self.command = str(command or "")
        self.version = str(version or "")
        self._page_ocr = page_ocr
        self.psm = psm
        self._page_ocr_params: Optional[set] = None
        self._model_versions = {}

    def available(self) -> bool:
        return bool(self.command)

    def capabilities(self):
        from folith.ocr_language import OCR_LANGUAGE_REGISTRY
        return {**super().capabilities(),
                "languages": [value["tesseract"] for value in OCR_LANGUAGE_REGISTRY.values()],
                "platforms": ["macOS", "Windows", "Linux"]}

    def _accepted_params(self) -> Optional[set]:
        if self._page_ocr is None:
            return set()
        if self._page_ocr_params is None:
            try:
                parameters = inspect.signature(self._page_ocr).parameters
            except (TypeError, ValueError):
                return None  # unknown: pass nothing optional
            if any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                   for parameter in parameters.values()):
                return None
            self._page_ocr_params = set(parameters)
        return self._page_ocr_params

    def _model_version(self, language):
        if language in self._model_versions:
            return self._model_versions[language]
        root = os.environ.get("TESSDATA_PREFIX", "")
        if not root and Path(self.command).is_file():
            try:
                result = subprocess.run([self.command, "--list-langs"], capture_output=True,
                                        text=True, timeout=10)
                match = re.search(r'in "([^"]+)"', result.stdout + result.stderr)
                root = match.group(1) if match else ""
            except (OSError, subprocess.TimeoutExpired):
                root = ""
        paths = [Path(root) / (code + ".traineddata") for code in language.split("+")] if root else []
        # This small one-time read substitutes for rerunning page OCR on resume.
        value = ";".join(path.name + "=" + hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in paths if path.is_file())
        self._model_versions[language] = value
        return value

    def runtime_info(self, language: str, *, include_model_version=True) -> OCRRuntimeInfo:
        return OCRRuntimeInfo(
            backend=self.name,
            model="tesseract-lstm",
            device="cpu",
            provider="tesseract",
            execution_provider="native_cpu",
            gpu_name=None,
            backend_version=self.version,
            extra={"language": language, "model_version": self._model_version(language) if include_model_version else "not_inspected"},
        )

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        if self._page_ocr is None:
            return [], "Tesseract backend 未绑定页面 OCR 实现"
        accepted = self._accepted_params()
        kwargs: Dict[str, Any] = {}
        # ``psm`` is only forwarded to an implementation that declares it, so
        # lightweight test doubles keep the historical 3-argument signature.
        if self.psm is not None and (accepted is None or "psm" in accepted):
            kwargs["psm"] = self.psm
        blocks, error = self._page_ocr(self.command, png_bytes, language, **kwargs)
        if error:
            return [], error
        info = self.runtime_info(language)
        return [
            OCRBlock.from_legacy(
                block, page_index=page_index, page_number=page_number,
                backend=info.backend, device=info.device, provider=info.provider,
                model=info.model, backend_version=info.backend_version)
            for block in (blocks or [])
        ], None


from folith.ocr_paddle import PaddleOCRBackend, OCRUnavailable


def resolve_backend(requested: Optional[str] = None, *,
                    tesseract_command: str = "", tesseract_version: str = "",
                    page_ocr: Optional[Callable[..., Any]] = None,
                    language: str = "eng",
                    psm: Optional[int] = None,
                    paddle_model: Optional[str] = None,
                    online_config: Optional[OnlineOCRConfig] = None
                    ) -> Tuple[OCRBackend, List[str]]:
    """Return ``(backend, warnings)``; never silently pretends a backend ran.

    Explicit selection never falls back. Auto prefers Tesseract and records a
    fallback to Paddle only when the Tesseract executable is absent. The online
    source is only ever used when it is explicitly selected.
    """
    warnings: List[str] = []
    name = normalize_backend(requested)
    fallback = TesseractBackend(tesseract_command, tesseract_version, page_ocr, psm)
    if name == "auto":
        if fallback.available():
            return fallback, []
        candidate = PaddleOCRBackend(language, model_id=paddle_model)
        if candidate.available():
            return candidate, ["Auto fallback: tesseract -> paddleocr; reason=tesseract_runtime_unavailable"]
        return fallback, []
    if name == TESSERACT_BACKEND:
        return fallback, []
    if name == "paddle":
        candidate = PaddleOCRBackend(language, model_id=paddle_model)
        if not candidate.available():
            status = candidate.status()
            raise OCRUnavailable(status["state"], status["message"])
        return candidate, []
    if name == ONLINE_OCR_BACKEND:
        from folith.ocr_online import OnlineOCRBackend
        candidate = OnlineOCRBackend(online_config or OnlineOCRConfig())
        if not candidate.available():
            raise OCRUnavailable("unconfigured", candidate.unavailable_reason())
        return candidate, []
    raise OCRUnavailable("unsupported", f"未知的 OCR backend：{name}")


def normalize_backend(value=None):
    name = str(value or "tesseract").strip().lower().replace("-", "_")
    if name in {"paddleocr", "paddle_ocr"}:
        name = "paddle"
    if name in {"online_ocr", "online_api", "openai_ocr", "api"}:
        name = ONLINE_OCR_BACKEND
    if name not in SUPPORTED_BACKENDS:
        raise OCRUnavailable("unsupported", f"未知的 OCR backend：{name}")
    return name


def backend_options(backend_name: str, metadata: Dict[str, Any]) -> str:
    """Non-secret cache identity for backend-specific configuration.

    Returns ``""`` for the historical Tesseract default so pages cached before
    this contract existed remain reusable. A Paddle model selection and every
    online configuration produce a distinct value.
    """
    name = str(backend_name or "")
    if name == "paddleocr":
        model_id = str((metadata or {}).get("model_id") or "")
        if model_id and model_id != "official":
            return (f"paddle_model={model_id};"
                    f"root={str((metadata or {}).get('model_resource_dir') or '')}")
        return ""
    if name == ONLINE_OCR_BACKEND:
        online = (metadata or {}).get("online") or {}
        return str(online.get("cache_options") or "")
    return ""



def backend_report(*, tesseract_command: str = "",
                   tesseract_version: str = "",
                   online_config: Optional[OnlineOCRConfig] = None,
                   paddle_models: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Availability + honest runtime facts for every known backend.

    Configuration status only: no model is loaded and no billable online
    request is issued.
    """
    report = []
    tesseract = TesseractBackend(tesseract_command, tesseract_version)
    report.append({
        "backend": tesseract.name,
        "available": tesseract.available(),
        "runtime": tesseract.runtime_info("eng", include_model_version=False).to_dict(),
        "default": True,
        "capabilities": tesseract.capabilities(),
    })
    paddle = PaddleOCRBackend()
    report.append({
        "backend": paddle.name,
        "available": paddle.available(),
        "runtime": paddle.runtime_info("eng", include_model_version=False).to_dict() if paddle.available() else {},
        "unavailable_reason": paddle.unavailable_reason(),
        "default": False,
        "capabilities": paddle.capabilities(),
        **paddle.status(),
    })
    if paddle_models is not None:
        report[-1]["models"] = list(paddle_models)
    from folith.ocr_online import OnlineOCRBackend
    online = OnlineOCRBackend(online_config or OnlineOCRConfig())
    report.append({
        "backend": online.name,
        "available": online.available(),
        "runtime": online.runtime_info("eng", include_model_version=False).to_dict(),
        "unavailable_reason": online.unavailable_reason(),
        "default": False,
        "remote": True,
        "capabilities": online.capabilities(),
        **online.status(),
    })
    return report


def paddle_model_report(language: str = "eng") -> List[Dict[str, Any]]:
    """Per-model availability for the Paddle source, for the settings UI."""
    from folith import ocr_models
    entries = []
    for option in ocr_models.known_models():
        backend = PaddleOCRBackend(language, model_id=option.model_id)
        status = backend.status()
        entries.append({
            **option.to_dict(),
            "available": backend.available(),
            "state": status.get("state"),
            "message": status.get("message"),
            "language_supported": ocr_models.language_supported(option, language),
            "language_scope": ocr_models.language_scope_label(option),
            "resource_root": str(ocr_models.resource_root(option)),
        })
    return entries
