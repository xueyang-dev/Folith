"""Explicit OCR language configuration for the Tesseract ingestion chain.

Why this module exists
----------------------
The previous selector chose the OCR language from the set of language packs
installed on the machine, preferring ``chi_sim+eng`` and then ``eng``.  That is
a functional bug: an Arabic-only scan was OCR'd with ``-l eng`` whenever the
English pack happened to be installed, and the resulting (garbage) text looked
successful to every downstream stage.

The contract now is the opposite: **the user chooses the OCR language**, it is
persisted with the task, and a missing language pack fails visibly.  This module
is the single source of truth for that mapping so no business logic needs a
scattered ``if language == ...`` chain.

It does not detect the language of a document.  Direction is a property of the
declared script, not a guess from the image or the OCR output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .textual import text_direction

#: Bump when the meaning of a stored OCR language/direction pair changes, so a
#: checkpoint written by an older contract cannot be silently reused.
OCR_CONFIG_VERSION = 1

DEFAULT_PSM = 3
SUPPORTED_PSM = (3, 6, 4, 11)

#: Rasterization quality profiles.  The historical profile is a deliberate
#: opt-in for difficult old scans; nothing raises the DPI automatically.
OCR_QUALITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": {"label": "Standard", "dpi": 220},
    "high": {"label": "High quality", "dpi": 300},
    "historical": {"label": "Historical / difficult scan", "dpi": 350},
}
DEFAULT_OCR_QUALITY = "standard"
DEFAULT_OCR_DPI = OCR_QUALITY_PROFILES[DEFAULT_OCR_QUALITY]["dpi"]

#: The canonical registry.  ``tesseract`` is the engine code used for ``-l``;
#: ``direction`` is the script reading direction, never a per-page guess.
OCR_LANGUAGE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "en": {"label": "English", "tesseract": "eng", "direction": "ltr",
           "aliases": ("english", "eng", "en-us", "en-gb", "英语", "英文")},
    "zh-CN": {"label": "简体中文", "tesseract": "chi_sim", "direction": "ltr",
              "aliases": ("chinese", "simplified chinese", "chi_sim", "zh",
                          "zh-hans", "中文", "汉语")},
    "zh-TW": {"label": "繁體中文", "tesseract": "chi_tra", "direction": "ltr",
              "aliases": ("traditional chinese", "chi_tra", "zh-hant", "繁体中文")},
    "ar": {"label": "العربية", "tesseract": "ara", "direction": "rtl",
           "aliases": ("arabic", "ara", "ar-sa", "modern standard arabic",
                       "阿拉伯语", "阿拉伯文")},
    "ja": {"label": "日本語", "tesseract": "jpn", "direction": "ltr",
           "aliases": ("japanese", "jpn", "ja-jp", "日语", "日文")},
    "ko": {"label": "한국어", "tesseract": "kor", "direction": "ltr",
           "aliases": ("korean", "kor", "ko-kr", "韩语", "朝鲜语")},
    "ru": {"label": "Русский", "tesseract": "rus", "direction": "ltr",
           "aliases": ("russian", "rus", "ru-ru", "俄语")},
    "de": {"label": "Deutsch", "tesseract": "deu", "direction": "ltr",
           "aliases": ("german", "deu", "ger", "de-de", "德语")},
    "fr": {"label": "Français", "tesseract": "fra", "direction": "ltr",
           "aliases": ("french", "fra", "fre", "fr-fr", "法语")},
    "es": {"label": "Español", "tesseract": "spa", "direction": "ltr",
           "aliases": ("spanish", "spa", "es-es", "西班牙语")},
    "pt": {"label": "Português", "tesseract": "por", "direction": "ltr",
           "aliases": ("portuguese", "por", "pt-br", "葡萄牙语")},
    "it": {"label": "Italiano", "tesseract": "ita", "direction": "ltr",
           "aliases": ("italian", "ita", "it-it", "意大利语")},
    "nl": {"label": "Nederlands", "tesseract": "nld", "direction": "ltr",
           "aliases": ("dutch", "nld", "dut", "荷兰语")},
    "pl": {"label": "Polski", "tesseract": "pol", "direction": "ltr",
           "aliases": ("polish", "pol", "波兰语")},
    "tr": {"label": "Türkçe", "tesseract": "tur", "direction": "ltr",
           "aliases": ("turkish", "tur", "土耳其语")},
    "uk": {"label": "Українська", "tesseract": "ukr", "direction": "ltr",
           "aliases": ("ukrainian", "ukr", "乌克兰语")},
    "he": {"label": "עברית", "tesseract": "heb", "direction": "rtl",
           "aliases": ("hebrew", "heb", "iw", "希伯来语")},
    "fa": {"label": "فارسی", "tesseract": "fas", "direction": "rtl",
           "aliases": ("persian", "farsi", "fas", "per", "波斯语")},
    "ur": {"label": "اردو", "tesseract": "urd", "direction": "rtl",
           "aliases": ("urdu", "urd", "乌尔都语")},
    "hi": {"label": "हिन्दी", "tesseract": "hin", "direction": "ltr",
           "aliases": ("hindi", "hin", "印地语")},
    "vi": {"label": "Tiếng Việt", "tesseract": "vie", "direction": "ltr",
           "aliases": ("vietnamese", "vie", "越南语")},
    "th": {"label": "ไทย", "tesseract": "tha", "direction": "ltr",
           "aliases": ("thai", "tha", "泰语")},
}

#: Language pairs offered as one-click combinations in the UI.  English is never
#: appended implicitly — the user has to pick the combined option.
_COMBINATION_KEYS: Tuple[Tuple[str, ...], ...] = (
    ("ar", "en"),
    ("zh-CN", "en"),
    ("ja", "en"),
    ("ko", "en"),
    ("ru", "en"),
    ("he", "en"),
    ("fa", "en"),
    ("ur", "en"),
)


@dataclass(frozen=True)
class OCRLanguage:
    """A resolved OCR language selection."""

    key: str
    label: str
    engine_code: str
    direction: str
    keys: Tuple[str, ...]

    @property
    def engine_codes(self) -> Tuple[str, ...]:
        return tuple(code for code in self.engine_code.split("+") if code)

    @property
    def is_rtl(self) -> bool:
        return self.direction == "rtl"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ocr_language": self.engine_code,
            "ocr_language_key": self.key,
            "ocr_language_label": self.label,
            "ocr_language_engine_code": self.engine_code,
            "ocr_text_direction": self.direction,
            "ocr_direction": self.direction,
        }


def _lookup_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for key, entry in OCR_LANGUAGE_REGISTRY.items():
        index[str(key).casefold()] = key
        index[str(entry["tesseract"]).casefold()] = key
        index[str(entry["label"]).casefold()] = key
        for alias in entry.get("aliases") or ():
            index[str(alias).casefold()] = key
    return index


_LOOKUP = _lookup_index()


def _split_parts(value: str) -> List[str]:
    normalized = str(value or "").replace("＋", "+").replace("，", ",")
    normalized = normalized.replace("&", "+").replace("/", "+")
    parts: List[str] = []
    for chunk in normalized.replace(",", "+").split("+"):
        text = chunk.strip()
        if text:
            parts.append(text)
    return parts


def _resolve_part(part: str) -> Tuple[str, str, str]:
    """Return ``(key, engine_code, label)`` for one language token."""
    folded = part.casefold()
    key = _LOOKUP.get(folded)
    if key is not None:
        entry = OCR_LANGUAGE_REGISTRY[key]
        return key, str(entry["tesseract"]), str(entry["label"])
    # An unknown-but-plausible Tesseract code (``dan``, ``swe`` …) is accepted
    # so the UI's "other language" path keeps working; direction is then derived
    # from the code's script via the shared helper.
    if part and all(char.isalnum() or char == "_" for char in part):
        code = part.lower()
        return code, code, part
    raise ValueError(f"未知的 OCR 语言：{part!r}")


def resolve_ocr_language(value: Any) -> OCRLanguage:
    """Resolve a UI label, registry key or Tesseract code to one contract.

    Accepts single languages (``"Arabic"`` / ``"ar"`` / ``"ara"``) and explicit
    combinations (``"Arabic + English"`` / ``"ara+eng"``).  Raises ``ValueError``
    for an empty or unrecognised value rather than silently choosing one.
    """
    parts = _split_parts(str(value or ""))
    if not parts:
        raise ValueError("OCR 语言不能为空")
    keys: List[str] = []
    codes: List[str] = []
    labels: List[str] = []
    directions: List[str] = []
    for part in parts:
        key, code, label = _resolve_part(part)
        keys.append(key)
        codes.append(code)
        labels.append(label)
        registry_direction = str(
            (OCR_LANGUAGE_REGISTRY.get(key) or {}).get("direction") or "")
        directions.append(registry_direction or text_direction("", code))
    # A single RTL component makes the whole line RTL: mixed Arabic + English
    # text still reads right-to-left.
    direction = "rtl" if "rtl" in directions else "ltr"
    return OCRLanguage(
        key="+".join(keys),
        label=" + ".join(labels),
        engine_code="+".join(codes),
        direction=direction,
        keys=tuple(keys),
    )


def try_resolve_ocr_language(value: Any) -> Optional[OCRLanguage]:
    try:
        return resolve_ocr_language(value)
    except (TypeError, ValueError):
        return None


def tesseract_code(value: Any) -> str:
    return resolve_ocr_language(value).engine_code


def direction_for(value: Any) -> str:
    return resolve_ocr_language(value).direction


def is_rtl(value: Any) -> bool:
    return direction_for(value) == "rtl"


def normalize_quality(value: Any) -> str:
    profile = str(value or "").strip().lower()
    return profile if profile in OCR_QUALITY_PROFILES else DEFAULT_OCR_QUALITY


def dpi_for_quality(value: Any) -> int:
    return int(OCR_QUALITY_PROFILES[normalize_quality(value)]["dpi"])


def quality_for_dpi(value: Any) -> str:
    """Map a configured DPI back to the closest named profile (for the UI)."""
    try:
        dpi = int(value)
    except (TypeError, ValueError):
        return DEFAULT_OCR_QUALITY
    exact = [name for name, entry in OCR_QUALITY_PROFILES.items()
             if int(entry["dpi"]) == dpi]
    if exact:
        return exact[0]
    return "historical" if dpi >= 330 else "high" if dpi >= 260 else "standard"


def quality_options() -> List[Dict[str, Any]]:
    return [{"value": name, "label": str(entry["label"]),
             "dpi": int(entry["dpi"])}
            for name, entry in OCR_QUALITY_PROFILES.items()]


def ocr_language_options() -> List[Dict[str, Any]]:
    """Selectable OCR languages for the new-task UI.

    ``value`` is the persisted engine code (``ara`` / ``ara+eng``); ``label`` is
    the user-facing name.  Single languages come first, then the explicit
    combinations.
    """
    options: List[Dict[str, Any]] = []
    for key, entry in OCR_LANGUAGE_REGISTRY.items():
        options.append({
            "value": str(entry["tesseract"]),
            "label": str(entry["label"]),
            "direction": str(entry["direction"]),
            "key": key,
        })
    for combo in _COMBINATION_KEYS:
        if not all(key in OCR_LANGUAGE_REGISTRY for key in combo):
            continue
        resolved = resolve_ocr_language("+".join(
            OCR_LANGUAGE_REGISTRY[key]["tesseract"] for key in combo))
        options.append({
            "value": resolved.engine_code,
            "label": resolved.label,
            "direction": resolved.direction,
            "key": resolved.key,
        })
    return options


def validate_language_packs(value: Any, installed: Iterable[str]) -> Dict[str, Any]:
    """Check the requested engine codes against the installed packs.

    Returns a report instead of falling back: the caller must surface
    ``missing`` as a blocking error, because OCR text produced by the wrong
    language pack silently poisons the translation pipeline.
    """
    resolved = resolve_ocr_language(value)
    installed_set = {str(item) for item in (installed or [])}
    missing = [code for code in resolved.engine_codes if code not in installed_set]
    return {
        **resolved.to_dict(),
        "ok": not missing,
        "missing": missing,
        "installed_languages": sorted(installed_set),
        "engine_code": resolved.engine_code,
    }


def missing_pack_message(value: Any, installed: Sequence[str]) -> str:
    resolved = resolve_ocr_language(value)
    available = ", ".join(sorted({str(item) for item in (installed or [])})) or "（无）"
    missing = ", ".join(
        code for code in resolved.engine_codes
        if code not in {str(item) for item in (installed or [])})
    return (
        f"OCR 语言包未安装：{resolved.label}（{missing or resolved.engine_code}）。"
        f"请先安装对应的 Tesseract traineddata，或在任务设置中改用已安装的语言。"
        f"当前已安装语言：{available}。"
    )


__all__ = [
    "DEFAULT_OCR_DPI",
    "DEFAULT_OCR_QUALITY",
    "DEFAULT_PSM",
    "OCR_CONFIG_VERSION",
    "OCR_LANGUAGE_REGISTRY",
    "OCR_QUALITY_PROFILES",
    "OCRLanguage",
    "SUPPORTED_PSM",
    "direction_for",
    "dpi_for_quality",
    "is_rtl",
    "missing_pack_message",
    "normalize_quality",
    "ocr_language_options",
    "quality_for_dpi",
    "quality_options",
    "resolve_ocr_language",
    "tesseract_code",
    "try_resolve_ocr_language",
    "validate_language_packs",
]
