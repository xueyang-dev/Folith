"""OCR backend boundary: one block contract, honest runtime metadata.

Why a boundary at all
---------------------
Folith's OCR was Tesseract-shaped all the way through: the extractor produced
Tesseract TSV dicts, the runtime metadata hard-coded ``device=cpu``, and there
was no place to put an accelerated backend even if one existed.  This module
gives every backend one contract so that swapping engines is a configuration
change rather than a rewrite — while keeping Tesseract as the zero-extra-model
fallback that always works.

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

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

TESSERACT_BACKEND = "tesseract"
PADDLEOCR_BACKEND = "paddleocr"


@dataclass(frozen=True)
class OCRBlock:
    """Backend-independent OCR block."""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: Optional[float]
    page_index: int
    page_number: int
    backend: str
    device: str
    provider: str
    model: str
    backend_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Lean mapping used by the existing cleanup/segmentation path."""
        return {
            "text": self.text,
            "confidence": self.confidence,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }

    def to_contract_dict(self) -> Dict[str, Any]:
        """Full block contract: geometry, confidence, page identity, provenance."""
        return {
            **self.to_dict(),
            "page_index": self.page_index,
            "page_number": self.page_number,
            "backend": self.backend,
            "device": self.device,
            "provider": self.provider,
            "model": self.model,
            "backend_version": self.backend_version,
        }

    @classmethod
    def from_legacy(cls, block: Dict[str, Any], *, page_index: int, page_number: int,
                    backend: str, device: str, provider: str, model: str,
                    backend_version: str = "") -> "OCRBlock":
        try:
            confidence = float(block.get("confidence"))
        except (TypeError, ValueError):
            confidence = None

        def _coord(key: str) -> float:
            try:
                return float(block.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            text=str(block.get("text") or ""),
            left=_coord("left"), top=_coord("top"),
            right=_coord("right"), bottom=_coord("bottom"),
            confidence=confidence, page_index=int(page_index),
            page_number=int(page_number), backend=backend, device=device,
            provider=provider, model=model, backend_version=backend_version,
        )


@dataclass
class OCRRuntimeInfo:
    """What will actually execute OCR — recorded in the performance artifact."""

    backend: str
    model: str
    device: str
    provider: str
    execution_provider: str
    gpu_name: Optional[str] = None
    backend_version: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "device": self.device,
            "provider": self.provider,
            "execution_provider": self.execution_provider,
            "gpu_name": self.gpu_name,
            "backend_version": self.backend_version,
            **self.extra,
        }


class OCRBackend:
    """Interface every OCR backend implements."""

    name = "abstract"

    def available(self) -> bool:
        raise NotImplementedError

    def runtime_info(self, language: str) -> OCRRuntimeInfo:
        raise NotImplementedError

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        raise NotImplementedError


class TesseractBackend(OCRBackend):
    """Zero-extra-model fallback. Always CPU, always honest about it."""

    name = TESSERACT_BACKEND

    def __init__(self, command: str, version: str = "",
                 page_ocr: Optional[Callable[..., Any]] = None) -> None:
        self.command = str(command or "")
        self.version = str(version or "")
        self._page_ocr = page_ocr

    def available(self) -> bool:
        return bool(self.command)

    def runtime_info(self, language: str) -> OCRRuntimeInfo:
        return OCRRuntimeInfo(
            backend=self.name,
            model="tesseract-lstm",
            device="cpu",
            provider="tesseract",
            execution_provider="native_cpu",
            gpu_name=None,
            backend_version=self.version,
            extra={"language": language},
        )

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        if self._page_ocr is None:
            return [], "Tesseract backend 未绑定页面 OCR 实现"
        blocks, error = self._page_ocr(self.command, png_bytes, language)
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


class PaddleOCRBackend(OCRBackend):
    """Optional accelerated backend.

    ``available()`` is the only thing that may claim the backend exists, and it
    is driven by a real import.  ``runtime_info()`` asks PaddlePaddle which
    device it actually selected — CUDA being installed is never treated as
    evidence that OCR will run on the GPU.
    """

    name = PADDLEOCR_BACKEND

    def __init__(self, language: str = "en") -> None:
        self.language = str(language or "en")
        self._engine = None
        self._import_error = ""

    # -- availability -----------------------------------------------------
    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception as exc:  # noqa: BLE001 - optional dependency
            self._import_error = str(exc)[:200]
            return False

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return self._import_error or "paddleocr 未安装"

    # -- runtime facts ----------------------------------------------------
    def runtime_info(self, language: str) -> OCRRuntimeInfo:
        device = "cpu"
        gpu_name = None
        version = ""
        try:
            import paddle  # type: ignore
            version = str(getattr(paddle, "__version__", "") or "")
            selected = ""
            try:
                selected = str(paddle.device.get_device() or "")
            except Exception:  # noqa: BLE001
                selected = ""
            if selected:
                device = selected
        except Exception:  # noqa: BLE001
            device = "cpu"
        if device.startswith("gpu") or device.startswith("cuda"):
            execution_provider = "cuda"
        elif device.startswith("xpu"):
            execution_provider = "xpu"
        else:
            device = "cpu"
            execution_provider = "native_cpu"
        return OCRRuntimeInfo(
            backend=self.name,
            model="paddleocr",
            device=device,
            provider="paddleocr",
            execution_provider=execution_provider,
            gpu_name=gpu_name,
            backend_version=version,
            extra={"language": language, "requested_language": self.language},
        )

    # -- page OCR ---------------------------------------------------------
    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        engine, error = self._ensure_engine(language)
        if engine is None:
            return [], error
        import io

        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        try:
            image = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
            raw = engine.ocr(image, cls=True)
        except Exception as exc:  # noqa: BLE001 - one page must not abort the book
            return [], str(exc)[:180]
        info = self.runtime_info(language)
        blocks: List[OCRBlock] = []
        for line in (raw or [[]])[0] or []:
            try:
                box, (text, confidence) = line[0], line[1]
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
            except (TypeError, ValueError, IndexError):
                continue
            if not str(text or "").strip():
                continue
            blocks.append(OCRBlock(
                text=str(text), left=min(xs), top=min(ys),
                right=max(xs), bottom=max(ys), confidence=float(confidence) * 100.0,
                page_index=page_index, page_number=page_number,
                backend=info.backend, device=info.device, provider=info.provider,
                model=info.model, backend_version=info.backend_version,
            ))
        return blocks, None

    def _ensure_engine(self, language: str):
        if self._engine is not None:
            return self._engine, None
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return None, f"paddleocr 不可用：{str(exc)[:160]}"
        try:
            self._engine = PaddleOCR(use_angle_cls=True, lang=str(language or "en"),
                                     show_log=False)
        except TypeError:
            try:
                self._engine = PaddleOCR(use_angle_cls=True, lang=str(language or "en"))
            except Exception as exc:  # noqa: BLE001
                return None, f"paddleocr 初始化失败：{str(exc)[:160]}"
        except Exception as exc:  # noqa: BLE001
            return None, f"paddleocr 初始化失败：{str(exc)[:160]}"
        return self._engine, None


def resolve_backend(requested: Optional[str] = None, *,
                    tesseract_command: str = "", tesseract_version: str = "",
                    page_ocr: Optional[Callable[..., Any]] = None,
                    language: str = "eng") -> Tuple[OCRBackend, List[str]]:
    """Return ``(backend, warnings)``; never silently pretends a backend ran.

    An unavailable requested backend produces a warning naming the reason and
    falls back to Tesseract, which is the documented zero-extra-model path.
    """
    warnings: List[str] = []
    name = str(requested or os.environ.get("FOLIOTHREAD_OCR_BACKEND") or "").strip()
    fallback = TesseractBackend(tesseract_command, tesseract_version, page_ocr)
    if not name or name == TESSERACT_BACKEND:
        return fallback, warnings
    if name == PADDLEOCR_BACKEND:
        candidate = PaddleOCRBackend(language)
        if candidate.available():
            return candidate, warnings
        warnings.append(
            f"请求的 OCR backend「{name}」不可用（{candidate.unavailable_reason()}），"
            "已回退到 Tesseract CPU。")
        return fallback, warnings
    warnings.append(f"未知的 OCR backend「{name}」，已回退到 Tesseract CPU。")
    return fallback, warnings


def backend_report(*, tesseract_command: str = "",
                   tesseract_version: str = "") -> List[Dict[str, Any]]:
    """Availability + honest runtime facts for every known backend."""
    report = []
    tesseract = TesseractBackend(tesseract_command, tesseract_version)
    report.append({
        "backend": tesseract.name,
        "available": tesseract.available(),
        "runtime": tesseract.runtime_info("eng").to_dict(),
        "default": True,
    })
    paddle = PaddleOCRBackend()
    report.append({
        "backend": paddle.name,
        "available": paddle.available(),
        "runtime": paddle.runtime_info("eng").to_dict() if paddle.available() else {},
        "unavailable_reason": paddle.unavailable_reason(),
        "default": False,
    })
    return report
