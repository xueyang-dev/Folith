"""Task-level PaddleOCR model selection.

Why this module exists
----------------------
PP-OCRv5 ships one Arabic recognizer, but a *book-specific* adaptation of that
recognizer can be trained and exported (see
``docs/paddle-arabic-finetuning.md``).  Before this module the only way to use
such an adaptation was to export ``FOLITH_OCR_MODEL_DIR`` before the process
started, which is a **process-global** switch: two tasks in one session could
not use different models, and switching meant mutating the environment of a
running application.

This module turns the model into a *task-level selection*:

* every selectable model has a stable ``model_id`` and an explicit resource
  directory;
* the selection (id + resource location + effective model version) is persisted
  with the task, so a resume reuses the model the task was created with;
* a model whose resources are missing reports that state and is **not**
  silently replaced by another model — downloading is only ever allowed for the
  official models that PaddleX can fetch, never for a local book adaptation;
* a model that was only validated on one language says so and refuses other
  languages instead of producing confident-looking rubbish.

The environment variable ``FOLITH_OCR_MODEL_DIR`` keeps its documented public
meaning: it overrides where the *official/default* model resources live.  An
explicit task selection always wins over it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: ``model_id`` of the stock PP-OCRv5 model set.  This is the historical
#: behaviour and therefore the default for every existing task.
OFFICIAL_MODEL_ID = "official"
#: Directory (relative to the project root) scanned for exported book models.
BOOK_MODEL_DIRNAME = "benchmark/paddle-models"
#: PaddleOCR version whose resource layout the adapters expect.
PADDLEOCR_VERSION = "3.7.0"

_MODEL_CARD_FILENAME = "model-card.json"


@dataclass(frozen=True)
class PaddleModelOption:
    """One selectable PaddleOCR model set."""

    model_id: str
    label: str
    description: str = ""
    #: Base directory that contains ``paddle/<version>/official_models``.  An
    #: empty value means "use the environment variable / per-user data dir".
    resource_dir: str = ""
    #: Languages this model set may be used for.  Empty means "the full
    #: PP-OCRv5 language registry".
    validated_languages: Tuple[str, ...] = ()
    #: ``True`` only for the official model set: PaddleX may download it.
    allow_download: bool = False
    note: str = ""
    source: str = "builtin"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "label": self.label,
            "description": self.description,
            "resource_dir": self.resource_dir,
            "validated_languages": list(self.validated_languages),
            "allow_download": bool(self.allow_download),
            "note": self.note,
            "source": self.source,
        }


def project_root() -> Path:
    """Repository/install root that contains ``folith/``."""
    return Path(__file__).resolve().parents[1]


def _normalize_language_code(value: str) -> str:
    """Map a registry key / alias onto the Tesseract engine code used in tasks."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    try:
        from folith.ocr_language import try_resolve_ocr_language
        resolved = try_resolve_ocr_language(text)
    except Exception:  # noqa: BLE001 - registry must never break model listing
        resolved = None
    return resolved.engine_code if resolved is not None else text


def _card_languages(card: Dict[str, Any]) -> Tuple[str, ...]:
    """Normalize the language claim of a local model card.

    A card without a language claim is treated as *unscoped* would be wrong for
    a book adaptation, so an unrecognized card defaults to Arabic: every
    exported model in this repository so far is the Arabic adaptation, and the
    safe failure mode is refusing a language rather than running it.
    """
    explicit = card.get("validated_languages") or card.get("languages")
    if isinstance(explicit, str):
        explicit = [explicit]
    if isinstance(explicit, list):
        codes = tuple(dict.fromkeys(_normalize_language_code(item)
                                    for item in explicit if str(item).strip()))
        codes = tuple(code for code in codes if code)
        if codes:
            return codes
    base = str(card.get("base_model") or "").lower()
    if "arabic" in base:
        return ("ara", "fas", "urd")
    return ("ara",)


def _option_from_card(card_path: Path) -> Optional[PaddleModelOption]:
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(card, dict):
        return None
    directory = card_path.parent
    model_id = str(card.get("model_id") or directory.name).strip()
    if not model_id or model_id == OFFICIAL_MODEL_ID:
        return None
    name = str(card.get("name") or model_id).strip()
    status = str(card.get("status") or "").strip()
    limitations = card.get("limitations") or []
    note = "；".join(str(item) for item in limitations if str(item).strip())[:300]
    return PaddleModelOption(
        model_id=model_id,
        label=f"{name}（实验性）" if status.startswith("experimental") else name,
        description=status,
        resource_dir=str(directory),
        validated_languages=_card_languages(card),
        allow_download=False,
        note=note,
        source="local",
    )


def _discover_local_models() -> List[PaddleModelOption]:
    root = project_root() / BOOK_MODEL_DIRNAME
    if not root.is_dir():
        return []
    found: List[PaddleModelOption] = []
    for card_path in sorted(root.glob(f"*/{_MODEL_CARD_FILENAME}")):
        option = _option_from_card(card_path)
        if option is not None:
            found.append(option)
    return found


def known_models() -> List[PaddleModelOption]:
    """Every selectable model, official first, then locally exported models."""
    options = [PaddleModelOption(
        model_id=OFFICIAL_MODEL_ID,
        label="PP-OCRv5（官方）",
        description="PaddleOCR 官方多语言模型；首次使用时按需下载。",
        resource_dir="",
        allow_download=True,
        source="builtin",
    )]
    seen = {OFFICIAL_MODEL_ID}
    for option in _discover_local_models():
        if option.model_id in seen:
            continue
        seen.add(option.model_id)
        options.append(option)
    return options


def resolve(model_id: Optional[str]) -> PaddleModelOption:
    """Resolve a stored model id, failing loudly on an unknown one.

    Falling back to the official model here is exactly the silent substitution
    this module exists to prevent.
    """
    wanted = str(model_id or "").strip() or OFFICIAL_MODEL_ID
    for option in known_models():
        if option.model_id == wanted:
            return option
    raise KeyError(wanted)


def try_resolve(model_id: Optional[str]) -> Optional[PaddleModelOption]:
    try:
        return resolve(model_id)
    except KeyError:
        return None


def model_options() -> List[Dict[str, Any]]:
    return [option.to_dict() for option in known_models()]


def resource_base(option: PaddleModelOption) -> Path:
    """The resource location *above* the ``paddle/<version>`` suffix.

    This is exactly the documented meaning of ``FOLITH_OCR_MODEL_DIR``: point it
    at a directory that contains ``paddle/3.7.0/official_models``.  A registered
    book model stores the same kind of directory, so one selection rule covers
    both; an explicit task selection wins over the environment variable.
    """
    if option.resource_dir:
        return Path(option.resource_dir).expanduser().resolve()
    explicit = os.environ.get("FOLITH_OCR_MODEL_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    from folith.desktop.paths import resolve_paths
    return (resolve_paths().data_dir / "models" / "ocr").resolve()


def resource_root(option: PaddleModelOption) -> Path:
    """Where ``official_models/`` lives for this model.

    The ``paddle/<version>`` suffix is always appended, matching the existing
    ``FOLITH_OCR_MODEL_DIR`` contract, so a directory that PaddleX also uses for
    its download/cache state stays self-contained.
    """
    return resource_base(option) / "paddle" / PADDLEOCR_VERSION


def language_supported(option: PaddleModelOption, language: str) -> bool:
    """Whether the validated scope of this model covers ``language``."""
    if not option.validated_languages:
        return True
    code = str(language or "").strip().lower()
    if not code:
        return False
    # ``ara+eng`` style combinations: the primary code decides.
    primary = code.split("+")[0]
    return primary in option.validated_languages


def language_scope_label(option: PaddleModelOption) -> str:
    if not option.validated_languages:
        return "全部 PP-OCRv5 语言"
    labels = {"ara": "阿拉伯语", "fas": "波斯语", "urd": "乌尔都语"}
    return "、".join(labels.get(code, code) for code in option.validated_languages)


def card_digest(option: PaddleModelOption) -> str:
    """SHA-256 of the model card, part of the recorded model version."""
    if not option.resource_dir:
        return ""
    path = Path(option.resource_dir) / _MODEL_CARD_FILENAME
    try:
        payload = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(payload).hexdigest()
