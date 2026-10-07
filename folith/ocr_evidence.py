"""Serializable page evidence; engine objects never cross this boundary."""
from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple

OCR_EVIDENCE_VERSION = 1
LANGUAGE_SCRIPTS = {
    **dict.fromkeys(["eng", "deu", "fra", "spa", "por", "ita", "nld", "pol", "tur", "vie"], "Latin"),
    **dict.fromkeys(["ara", "fas", "urd"], "Arabic"),
    "heb": "Hebrew", "rus": "Cyrillic", "ukr": "Cyrillic",
    "chi_sim": "Han", "chi_tra": "Han", "jpn": "Han/Kana", "kor": "Hangul",
    "hin": "Devanagari", "tha": "Thai",
}


@dataclass
class OCRPageResult:
    page_index: int
    page_number: int
    language: str
    direction: str
    dpi: int
    blocks: List[Any]
    engine: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    error: str = ""
    #: Request accounting for a remote recognizer: request id, token usage,
    #: retries and whether the service reported any billable amount.  Always
    #: secret-free — credentials never reach this structure.
    request: Dict[str, Any] = field(default_factory=dict)

    def to_record(self, width: float, height: float) -> Dict[str, Any]:
        scale = 72.0 / self.dpi
        blocks = []
        geometry_pages = set()
        for block in self.blocks:
            item = block.to_contract_dict()
            known = bool(getattr(block, "geometry_known", True))
            item["geometry_known"] = known
            geometry_pages.add(known)
            if known:
                item["bbox_pdf"] = [value * scale for value in
                                    (block.left, block.top, block.right, block.bottom)]
            else:
                # Never fabricate a rectangle: an unknown position stays unknown
                # so the preview cannot highlight a wrong spot.
                item["bbox_pdf"] = None
            lines = []
            for line in item.get("lines") or []:
                line = dict(line)
                line.setdefault("geometry_known", known)
                if known:
                    line["bbox_pdf"] = [float(line[key]) * scale for key in
                                        ("left", "top", "right", "bottom")]
                else:
                    line["bbox_pdf"] = None
                lines.append(line)
            item["lines"] = lines
            blocks.append(item)
        if not geometry_pages or geometry_pages == {True}:
            # A page with no text blocks has nothing that contradicts the
            # historical default; do not report a blank page as "coordinate-less".
            coordinate_space = "image_pixels"
        elif geometry_pages == {False}:
            coordinate_space = "unknown"
        else:
            coordinate_space = "mixed"
        return {
            "evidence_version": OCR_EVIDENCE_VERSION,
            "page_index": self.page_index, "page_number": self.page_number,
            "page_width": width, "page_height": height, "dpi": self.dpi,
            "coordinate_space": coordinate_space, "pixels_to_points": scale,
            "geometry_known": not geometry_pages or geometry_pages == {True},
            "preprocessing": {"version": 1, "operations": ["render_rgb"]},
            "language": self.language,
            "script": LANGUAGE_SCRIPTS.get(self.language.split("+")[0], "Unknown"),
            "scripts": list(dict.fromkeys(LANGUAGE_SCRIPTS.get(code, "Unknown") for code in self.language.split("+"))),
            "direction": self.direction, "engine": self.engine,
            "backend": self.engine["backend"], "model": self.engine["model"],
            "device": self.engine["device"], "provider": self.engine["provider"],
            "execution_provider": self.engine["execution_provider"],
            "raw_text": "\n\n".join(b.raw_text or b.text for b in self.blocks),
            "normalized_text": "\n\n".join(b.text for b in self.blocks),
            "blocks": blocks, "warnings": self.warnings, "timings": self.timings,
            "error": self.error, "request": dict(self.request),
        }


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
    engine_confidence: Optional[float] = None
    confidence_kind: str = "tesseract_word_mean_0_100"
    raw_text: str = ""
    lines: List[Dict[str, Any]] = field(default_factory=list)
    #: ``False`` when the backend returned text without any real geometry (for
    #: example a remote transcriber that reads the page image).  Coordinates in
    #: that case are placeholders and must never be drawn as a highlight.
    geometry_known: bool = True
    geometry_source: str = "engine"

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
            "engine_confidence": self.engine_confidence if self.engine_confidence is not None else self.confidence,
            "confidence_kind": self.confidence_kind,
            "geometry_known": bool(self.geometry_known),
            "geometry_source": self.geometry_source,
            "raw_text": self.raw_text or self.text, "lines": self.lines,
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
            engine_confidence=confidence, raw_text=str(block.get("raw_text") or block.get("text") or ""),
            lines=block.get("lines") or [],
            geometry_known=bool(block.get("geometry_known", True)),
            geometry_source=str(block.get("geometry_source") or "engine"),
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
    max_workers = 8

    def capabilities(self):
        return {"cpu": True, "gpu": False, "line_boxes": True,
                "word_boxes": True, "confidence_kind": "tesseract_word_mean_0_100",
                "rtl": True, "max_workers": self.max_workers}

    def load(self, on_status=None, cancel_check=None):
        return None

    def close(self):
        pass

    def recognize_page(self, png_bytes, *, page_index, page_number, language, dpi):
        from folith.ocr_evidence import OCRPageResult
        from folith.ocr_language import direction_for
        started = time.perf_counter()
        blocks, error = self.ocr_page(png_bytes, page_index=page_index,
            page_number=page_number, language=language, dpi=dpi)
        return OCRPageResult(page_index, page_number, language, direction_for(language),
            dpi, blocks or [], self.runtime_info(language).to_dict(),
            timings={"recognition_s": time.perf_counter() - started,
                     **getattr(self, "_last_timings", {})}, error=error or "",
            request=dict(getattr(self, "_last_request", {}) or {}))

    def available(self) -> bool:
        raise NotImplementedError

    def runtime_info(self, language: str, *, include_model_version=True) -> OCRRuntimeInfo:
        raise NotImplementedError

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        raise NotImplementedError
