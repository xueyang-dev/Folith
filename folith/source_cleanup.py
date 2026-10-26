"""LLM-assisted OCR source cleanup with a strict, auditable contract.

The PDF ingester owns layout extraction.  This module only repairs the text
that extraction produced: obvious OCR substitutions, broken line joins, and
paragraph boundaries that are artifacts of the PDF text layer.  Every input
item must be returned exactly once, so the model cannot silently omit or add
source material.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

from .textual import has_textual_content
from .workflow_errors import raise_if_fatal
from .request_control import ProviderRequestController as _ProviderRequestController


MAX_BATCH_CHARS = 6200
MAX_BATCH_ITEMS = 32
DEFAULT_PARALLELISM = 4
DEFAULT_CONFIDENCE_THRESHOLD = 88.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
CHECKPOINT_VERSION = 1
CONTEXT_WINDOW_VERSION = 1

# 稳定的事件枚举：`changed_segments` 与 `deterministic_merges` 共用同一个值，
# 下游审计与测试只认这三个字面量，不要改写成同义词。
REASON_WHITESPACE = "whitespace_normalization"
REASON_HYPHENATION = "hyphenation"
REASON_OBVIOUS_LINE_JOIN = "obvious_line_join"
REASON_STRAY_PUNCTUATION = "stray_punctuation_fragment"

# 分句器会在这里断句的字符（`segmentation._SENTENCE_CHARS` 的同集合）。
# Arabic ``؟`` (question mark) and Urdu ``۔`` (full stop) are terminals; the
# Arabic comma ``،`` and semicolon ``؛`` deliberately are not.
_SENTENCE_STRIP_CHARS = set(".!?。！？؟۔")
# 可能跟在句末标点后面的闭合引号/括号。
_TRAILING_CLOSERS = set('"\'”’»)]}】》」』〉）］')

#: Any Unicode letter run of length >= 2.  Used by the hallucination guard so it
#: is script-aware instead of Latin-only: ``[A-Za-z]{2,}`` found zero "words" in
#: Arabic, which silently disabled the guard for the whole document.
_UNICODE_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

#: Characters from cased scripts (Latin / Greek / Cyrillic).  A character-level
#: similarity threshold calibrated for English is too permissive for Arabic,
#: where the shared letter inventory inflates ``SequenceMatcher`` even between
#: unrelated texts.  Uncased scripts therefore get a stricter floor.
_CASED_RANGES = ((0x0041, 0x024F), (0x0370, 0x03FF), (0x0400, 0x04FF))


def _is_cased_word(word: str) -> bool:
    return any(
        any(start <= ord(char) <= end for start, end in _CASED_RANGES)
        for char in word
    )


def _similarity_floor(source_words: Sequence[str]) -> float:
    cased = sum(1 for word in source_words if _is_cased_word(word))
    return 0.2 if cased * 2 >= len(source_words) else 0.5

SYSTEM_PROMPT = """你是扫描版 PDF 原文的 OCR 纠错与排版整理器。
你的工作对象是 OCR/文本层的原文，不是翻译。只做有证据的最小修复：
1. 修正常见 OCR 错字、混淆字符、乱码和明显的标点识别错误；
2. 把 PDF 版面造成的错误换行拼回自然句子；英文行末连字符只有在确实是断词时才去掉；
3. 判断相邻输入项是否属于同一段或标题，并用 break_after 表示段落边界。

不要翻译、改写、摘要、润色、补写原文没有的信息，也不要删除任何内容。
每个输入 index 必须原样出现一次，顺序不能改变。通常只修改明显错误的字符；不确定时保留原文。
必须只返回 JSON，不要 Markdown 或解释，格式为：
{"segments":[{"index":0,"text":"修复后的原文","break_after":false}]}
其中 break_after=true 表示该项后开始新段落；如果当前请求的最后一项显然还在句子中，
可以返回 false，它会与下一批的第一项继续合并。整个文档的最后一项必须为 true。
"""


def _language_context(ocr_language="", source_language="", text_direction="",
                      ocr_engine_code="", ocr_backend="tesseract") -> str:
    """Build the per-task language block appended to :data:`SYSTEM_PROMPT`.

    The cleanup contract has to hold for every source language, not only
    English.  Telling the model the declared language and script direction keeps
    it from applying Latin assumptions (sentence case, ``-`` hyphenation) to an
    RTL or CJK document.
    """
    lines = []
    if source_language:
        lines.append(f"Source language: {source_language}")
    if text_direction:
        lines.append(f"Script direction: {str(text_direction).upper()}")
    engine = str(ocr_engine_code or ocr_language or "").strip()
    if engine:
        lines.append(f"OCR backend: {ocr_backend} {engine}")
    if not lines:
        return ""
    return (
        "\n\n本次任务的原文语言上下文（只用于判断标点、断行与段落边界，"
        "不改变上面的任何规则）：\n"
        + "\n".join(lines)
        + "\n原文可能是古典或历史文本：除非有明确的 OCR 错字证据，"
        "必须保留历史拼写、用词、语法与风格，不得按现代语言习惯改写。\n"
    )


def _normalise_text(value: Any) -> str:
    """Collapse physical line breaks while retaining meaningful spacing."""
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _is_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value or "")


def _join(left: str, right: str) -> str:
    left, right = _normalise_text(left), _normalise_text(right)
    if not left:
        return right
    if not right:
        return left
    # English hyphenation repair only applies to cased scripts: an Arabic word
    # is never "lowercase", so ``islower()`` is False and this cannot fire on
    # Arabic.  Do not replace it with a language-agnostic guess.
    if left.endswith(("-", "‐", "‑")) and right[:1].islower():
        return left[:-1] + right
    if ((_is_cjk(left[-1:]) and _is_cjk(right[:1]))
            or right.startswith(tuple(",.!?;:%)]}»”’。，！？；：％）】」』"
                                      "،؛؟۔"))):
        return left + right
    return f"{left} {right}"


def _is_sentence_terminal(text: str) -> bool:
    value = _normalise_text(text)
    if not value:
        return False
    value = re.sub(r"[\]\)}」』”’]+$", "", value).rstrip()
    return value[-1:] in set('.!?"”’…:;)؟۔')


def _is_pure_symbol_fragment(text: Any) -> bool:
    """这一项是否**完全没有**文字内容（纯标点/装饰符号）。

    PDF 自带文本层在字形解码失败时会吐出行首孤立的符号行（``.`` / ``©`` /
    ``— ]`` / ``|``）。它们不属于任何段落，只是错映射的残留。
    """
    value = str(text or "").strip()
    return bool(value) and not has_textual_content(value)


def _is_unfinished_line(text: Any) -> bool:
    """这一项是否"明显还没写完"。

    只认最强的一类证据：**以字母收尾且不是句末**。刻意不把以 ``’`` ``—`` ``,``
    收尾的行算进来 —— 那些行后面常常跟着合法的冒号或引号（``Neil’: ‘You went…``），
    把它们也并起来会把真标点删掉。
    """
    value = str(text or "").rstrip()
    if not value or _is_sentence_terminal(value):
        return False
    return value[-1:].isalpha() or _is_cjk(value[-1:])


def _strip_trailing_sentence_terminal(text: str) -> str:
    """删掉结尾处**新增**的句末标点（连同它后面的闭合引号）。

    只作用于确定性层已经判定"这一行还没写完"的位置：模型可以修字，但不许在这个
    位置上凭空造一个句末 —— 否则它就是换了个地方重新制造同一个假句末
    （``…in the`` 被改写成 ``…in the.``）。逗号、冒号不在删除范围内：它们不是
    分句器的句界，删掉反而是在消灭真标点。
    """
    value = _normalise_text(text)
    body = value
    while body and body[-1] in _TRAILING_CLOSERS:
        body = body[:-1].rstrip()
    trimmed = body
    while trimmed and trimmed[-1] in _SENTENCE_STRIP_CHARS:
        trimmed = trimmed[:-1].rstrip()
    if trimmed == body or not trimmed:
        return value
    return trimmed


def _stray_fragment_indices(items: Sequence[str]) -> List[int]:
    """找出夹在两行正文之间、应被当作噪声丢弃的纯符号行。

    判据（三条同时成立，全部是确定性事实，不需要模型判断正文）：
    前一行以字母收尾且未结句；本行没有任何文字内容；后一行非空。
    """
    found: List[int] = []
    for index in range(1, max(0, len(items) - 1)):
        if not _is_pure_symbol_fragment(items[index]):
            continue
        if not str(items[index + 1] or "").strip():
            continue
        if _is_unfinished_line(items[index - 1]):
            found.append(index)
    return found


def _schema(expected: int) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "foliothread_source_cleanup",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "segments": {
                        "type": "array",
                        "minItems": expected,
                        "maxItems": expected,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "index": {"type": "integer", "minimum": 0},
                                "text": {"type": "string", "minLength": 1},
                                "break_after": {"type": "boolean"},
                            },
                            "required": ["index", "text", "break_after"],
                        },
                    },
                },
                "required": ["segments"],
            },
        },
    }


def _decode(response: Any, expected: int) -> List[Dict[str, Any]]:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("原文纠错模型返回为空")
    raw = response.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        # A few compatible endpoints prepend a short sentence.  Recover only
        # a JSON object, never arbitrary plain text.
        start = raw.find("{")
        if start < 0:
            raise ValueError("原文纠错响应不是 JSON") from exc
        try:
            value = json.JSONDecoder().raw_decode(raw[start:])[0]
        except (TypeError, ValueError) as nested:
            raise ValueError("原文纠错响应不是 JSON") from nested
    rows = value.get("segments") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != expected:
        raise ValueError(f"原文纠错响应段数错误：期望 {expected} 项")
    indexes: List[int] = []
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        if (not isinstance(row, dict)
                or isinstance(row.get("index"), bool)
                or not isinstance(row.get("index"), int)):
            raise ValueError("原文纠错响应缺少连续 index")
        if not isinstance(row.get("text"), str):
            raise ValueError("原文纠错响应缺少文本字段")
        # Some compatible relays emit an empty string for a punctuation-only
        # OCR item even though the schema asks for a non-empty string.  Keep
        # the row and let ``_safe_candidate`` fall back to the extracted text;
        # rejecting the whole batch would discard valid corrections for every
        # neighbouring item.
        text = _normalise_text(row.get("text"))
        break_after = row.get("break_after")
        if not isinstance(break_after, bool):
            raise ValueError("原文纠错响应缺少 break_after")
        indexes.append(row["index"])
        parsed.append({"index": row["index"], "text": text,
                       "break_after": break_after})
    if indexes != list(range(expected)):
        raise ValueError("原文纠错响应 index 不连续")
    return parsed


def _safe_candidate(before: str, after: str) -> str:
    """Reject clearly destructive model rewrites while accepting OCR fixes."""
    before, after = _normalise_text(before), _normalise_text(after)
    if not after:
        return before
    if has_textual_content(before) and not has_textual_content(after):
        return before
    # A correction should stay close to the OCR text.  This catches a model
    # that followed the translation instruction accidentally or hallucinated a
    # paragraph while still allowing long-word and punctuation repairs.
    if len(before) >= 24:
        lower = max(8, int(len(before) * 0.35))
        upper = max(160, int(len(before) * 3.0))
        if len(after) < lower or len(after) > upper:
            return before
    source_words = _UNICODE_WORD_RE.findall(before)
    target_words = {word.casefold() for word in _UNICODE_WORD_RE.findall(after)}
    if len(source_words) >= 4:
        overlap = sum(word.casefold() in target_words for word in source_words)
        similarity = SequenceMatcher(None, before.casefold(), after.casefold()).ratio()
        if (overlap / len(source_words) < 0.25
                and similarity < _similarity_floor(source_words)):
            return before
    return after


def _call_model(
    call_llm: Callable[..., Any], provider: str, api_key: str, model: str,
    user_prompt: str, expected: int, system_prompt: str = SYSTEM_PROMPT,
) -> str:
    kwargs = {"temperature": 0.0, "response_format": _schema(expected)}
    try:
        return call_llm(provider, api_key, model, system_prompt, user_prompt, **kwargs)
    except TypeError:
        # Test doubles and older provider adapters may not expose
        # response_format yet.  The response is still validated strictly.
        return call_llm(provider, api_key, model, system_prompt, user_prompt,
                        temperature=0.0)


def _is_cancelled(cancel_check: Callable[[], bool] | None) -> bool:
    if cancel_check is None:
        return False
    return bool(cancel_check())


def _retryable(exc: BaseException) -> bool:
    """Retry transport/provider throttling, not malformed model output."""
    if "任务已请求取消" in str(exc):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    try:
        import core

        status = core.provider_error_status(exc).get("status")
        return status in {"rate_limited", "network_error", "provider_unavailable"}
    except Exception:  # pragma: no cover - cleanup must work without core
        text = str(exc).lower()
        return any(marker in text for marker in ("429", "rate limit", "timeout", "temporarily"))


class _RequestGate(_ProviderRequestController):
    """Paced request gate for cleanup — one implementation, not two.

    This used to be a private copy of the same idea.  It is now the shared
    provider-scoped controller (:mod:`folith.request_control`), so cleanup
    pacing and body-translation pacing cannot drift apart.  ``wait`` keeps the
    original call signature for existing callers.
    """

    def __init__(self, interval_s: float = 0.0) -> None:
        super().__init__(f"source_cleanup:{id(self)}", min_interval=interval_s)

    def wait(self, cancel_check: Callable[[], bool] | None = None) -> float:
        return self.acquire(cancel_check)


def _load_cleanup_checkpoint(path: Path | None, fingerprint: str) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("fingerprint") != fingerprint:
        return {}
    if value.get("version") != CHECKPOINT_VERSION:
        return {}
    return value


def _deterministic_prepare(items: Sequence[str], *, frozen: Iterable[int] = (),
                           hard_breaks: Iterable[int] = (),
                           ) -> Tuple[List[str], List[bool], List[Dict[str, Any]], List[str]]:
    """Normalize text, drop stray symbol lines and mark obvious joins as safe work.

    The third stream of work here is the text-layer corruption that produces a
    period in the middle of a sentence: ``[…in the] [.] [Midnight Library…]``.
    The lone symbol line is emptied so the ordered merge cannot glue it onto the
    previous line (which is what fabricated ``…in the. Midnight Library`` and
    turned into a false sentence boundary downstream).  The two neighbours are
    pinned into the same paragraph, and the deletion is recorded as
    :data:`REASON_STRAY_PUNCTUATION`.
    """
    frozen = {int(index) for index in frozen}
    # 冻结项前后、以及显式的硬断点之后，一律断段：不与任何东西拼接。
    locked = set(hard_breaks) | frozen | {index - 1 for index in frozen}
    cleaned = []
    changed = []
    for index, raw in enumerate(items):
        before = str(raw or "")
        after = before if index in frozen else _normalise_text(before)
        cleaned.append(after)
        if after != before:
            changed.append({"index": index, "before": before, "after": after,
                            "reason": REASON_WHITESPACE})

    break_after = [True] * len(cleaned)
    deterministic_merges: List[Dict[str, Any]] = []
    seen_merges = set()

    def mark_join(index: int, reason: str, indices_override=None) -> None:
        span = list(indices_override) if indices_override else [index, index + 1]
        key = tuple(span)
        if key in seen_merges:
            return
        seen_merges.add(key)
        break_after[index] = False
        deterministic_merges.append({"input_indices": span, "reason": reason})

    for index in range(max(0, len(cleaned) - 1)):
        left, right = cleaned[index], cleaned[index + 1]
        if not left or not right or index in locked:
            continue
        obvious_hyphen = left.endswith(("-", "‐", "‑")) and right[:1].islower()
        obvious_line_join = (
            len(left) < 180
            and not _is_sentence_terminal(left)
            and (right[:1].islower() or (_is_cjk(left[-1:]) and _is_cjk(right[:1])))
        )
        if obvious_hyphen or obvious_line_join:
            mark_join(index, REASON_HYPHENATION if obvious_hyphen
                      else REASON_OBVIOUS_LINE_JOIN)

    # 纯符号行必须**删除**而不能仅仅"不换段"：只要它还留在流里，`_join` 就会把
    # 句点贴到前一行末尾，等于把假句末交给后面的分句器。
    for index in _stray_fragment_indices(cleaned):
        if {index - 1, index} & locked or index in frozen or index + 1 in frozen:
            continue
        fragment = cleaned[index]
        cleaned[index] = ""
        changed.append({"index": index, "before": str(items[index] or ""),
                        "after": "", "reason": REASON_STRAY_PUNCTUATION})
        # 前后两行都属于同一段：这一段本来就是被错映射的符号行截断的一句话。
        mark_join(index - 1, REASON_STRAY_PUNCTUATION,
                  indices_override=[index - 1, index, index + 1])
        break_after[index] = False
    return cleaned, break_after, deterministic_merges, changed


def _confidence_for(confidence_by_index: Any, index: int) -> float | None:
    if isinstance(confidence_by_index, dict):
        value = confidence_by_index.get(index, confidence_by_index.get(str(index)))
    elif isinstance(confidence_by_index, Sequence) and not isinstance(
        confidence_by_index, (str, bytes)
    ):
        value = confidence_by_index[index] if index < len(confidence_by_index) else None
    else:
        value = None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _uncertainty_reasons(text: str, confidence: float | None, threshold: float) -> List[str]:
    reasons: List[str] = []
    if confidence is None:
        reasons.append("missing_ocr_confidence")
    elif confidence < threshold:
        reasons.append("low_ocr_confidence")
    if "�" in text or "□" in text:
        reasons.append("replacement_character")
    if re.search(r"([!?.,:;])\1{2,}", text):
        reasons.append("repeated_punctuation")
    alpha_numeric = sum(1 for char in text if char.isalnum())
    if text and alpha_numeric / max(1, len(text)) < 0.28:
        reasons.append("low_text_density")
    return reasons


def _checkpoint_payload(
    fingerprint: str,
    batches: Sequence[Sequence[Tuple[int, str]]],
    results: Dict[int, Dict[str, Any]],
    *,
    status: str,
) -> Dict[str, Any]:
    """Legacy single-file payload shape (read compatibility only)."""
    return {
        "version": CHECKPOINT_VERSION,
        "fingerprint": fingerprint,
        "batch_count": len(batches),
        "selected_indices": [index for batch in batches for index, _ in batch],
        "status": status,
        "results": {str(index): value for index, value in sorted(results.items())
                     if value.get("status") == "completed"},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


CLEANUP_CHECKPOINT_DIRNAME = "source_cleanup_checkpoint"
CLEANUP_CHECKPOINT_LEGACY_FILE = "source_cleanup_checkpoint.json"


class _CleanupCheckpointStore:
    """Manifest + one file per completed batch.

    The previous checkpoint rewrote a single growing ``results`` payload after
    every successful batch, so batch *n* re-serialised batches 1..n.  Here a
    successful batch writes exactly one small file and the manifest stays a
    constant-size run record.

    Read compatibility is preserved: a legacy single-file checkpoint with the
    same fingerprint is still honoured, so an in-flight job resumes instead of
    re-sending batches it already paid for.
    """

    def __init__(self, directory: str | Path | None) -> None:
        self.directory = (Path(directory) / CLEANUP_CHECKPOINT_DIRNAME
                          if directory else None)
        self.manifest_path = self.directory / "manifest.json" if self.directory else None
        self.batches_dir = self.directory / "batches" if self.directory else None
        self.legacy_path = (Path(directory) / CLEANUP_CHECKPOINT_LEGACY_FILE
                            if directory else None)
        # Created on first batch write only: a redundant mkdir per batch is a
        # syscall cost with no benefit once the directory exists.
        self._batches_dir_ready = False
        self._stats = {
            "batch_writes": 0,
            "batch_bytes_written": 0,
            "manifest_writes": 0,
            "manifest_bytes_written": 0,
            "legacy_resumed_batches": 0,
        }

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def batch_path(self, batch_index: int) -> Path:
        return self.batches_dir / f"batch-{int(batch_index):05d}.json"

    def load(self, fingerprint: str) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
        results: Dict[int, Dict[str, Any]] = {}
        legacy = _load_cleanup_checkpoint(self.legacy_path, fingerprint)
        for key, value in (legacy.get("results") or {}).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and isinstance(value.get("rows"), list):
                results[index] = {**value, "status": "completed"}
        self._stats["legacy_resumed_batches"] = len(results)

        manifest = self._read_manifest()
        if manifest and (manifest.get("fingerprint") != fingerprint
                         or manifest.get("version") != CHECKPOINT_VERSION):
            manifest = {}
        if self.batches_dir and self.batches_dir.is_dir():
            for path in sorted(self.batches_dir.glob("batch-*.json")):
                payload = self._read_json(path)
                if not payload:
                    continue
                if payload.get("fingerprint") != fingerprint:
                    continue
                if payload.get("version") != CHECKPOINT_VERSION:
                    continue
                try:
                    index = int(payload.get("batch_index"))
                except (TypeError, ValueError):
                    continue
                value = payload.get("result")
                if isinstance(value, dict) and isinstance(value.get("rows"), list):
                    results[index] = {**value, "status": "completed"}
        return results, manifest or {}

    def save_batch(self, fingerprint: str, batch_index: int,
                   result: Dict[str, Any]) -> None:
        if self.batches_dir is None:
            return
        payload = {
            "version": CHECKPOINT_VERSION,
            "fingerprint": fingerprint,
            "batch_index": int(batch_index),
            "result": result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        text = json.dumps(payload, ensure_ascii=False)
        if not self._batches_dir_ready:
            self.batches_dir.mkdir(parents=True, exist_ok=True)
            self._batches_dir_ready = True
        path = self.batch_path(batch_index)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        self._stats["batch_writes"] += 1
        self._stats["batch_bytes_written"] += len(text.encode("utf-8"))

    def save_manifest(self, fingerprint: str, batches: Sequence[Sequence[Any]],
                      *, status: str, completed: int, failed: int) -> None:
        if self.directory is None:
            return
        manifest = {
            "version": CHECKPOINT_VERSION,
            "fingerprint": fingerprint,
            "batch_count": len(batches),
            "selected_indices": [index for batch in batches for index, _ in batch],
            "completed_batches": int(completed),
            "failed_batches": int(failed),
            "status": str(status or "running"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        text = json.dumps(manifest, ensure_ascii=False, indent=2)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_name(self.manifest_path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.manifest_path)
        self._stats["manifest_writes"] += 1
        self._stats["manifest_bytes_written"] += len(text.encode("utf-8"))

    def _read_manifest(self) -> Dict[str, Any]:
        if self.manifest_path is None:
            return {}
        return self._read_json(self.manifest_path) or {}

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


def cleanup_source_paragraphs(
    paragraphs: Sequence[str], provider: str, api_key: str, model: str,
    *, call_llm: Callable[..., Any], on_progress: Callable[[str], None] | None = None,
    max_batch_chars: int = MAX_BATCH_CHARS,
    max_batch_items: int = MAX_BATCH_ITEMS,
    parallelism: int = DEFAULT_PARALLELISM,
    confidence_by_index: Any = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    checkpoint_dir: str | Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    request_interval_seconds: float = 0.0,
    profiler: Any = None,
    ocr_language: str = "",
    source_language: str = "",
    text_direction: str = "",
    ocr_engine_code: str = "",
    ocr_backend: str = "tesseract",
    frozen_indices: Iterable[int] = (),
    hard_breaks_after: Iterable[int] = (),
) -> Tuple[List[str], Dict[str, Any], List[str]]:
    """Repair OCR paragraphs and return ``(paragraphs, metadata, warnings)``.

    ``frozen_indices``: items that are already trusted semantic text (blocks a
    vision model read from a scanned page inside an otherwise native PDF).
    They pass through unchanged, are never sent to the model and never merged
    with a neighbour.  ``hard_breaks_after``: items after which a paragraph
    must end (e.g. the last native line before a skipped page).

    The input boundaries are preserved as indexed items in each request; the
    model may only change their text and mark joins.  A failed batch falls back
    to its deterministic input and never blocks the translation pipeline.

    ``ocr_language`` / ``source_language`` / ``text_direction`` describe the
    declared source language and are passed to the model as context, so the
    cleanup contract is not English-only.  They are part of the checkpoint
    fingerprint: changing the OCR language must not resume another language's
    cleanup results.
    """
    original = [_normalise_text(item) for item in (paragraphs or [])]
    if not original:
        if profiler is not None:
            for stage_name in ("deterministic_cleanup", "llm_cleanup"):
                profiler.skipped(stage_name, metadata={"reason": "empty_input"})
        return [], {"status": "skipped", "input_count": 0, "output_count": 0}, []

    deterministic_stage = None
    if profiler is not None:
        deterministic_stage = profiler.start_stage(
            "deterministic_cleanup", concurrency=1, item_count=len(original),
            metadata={"operations": [REASON_WHITESPACE, REASON_OBVIOUS_LINE_JOIN,
                                     REASON_STRAY_PUNCTUATION]},
        )
    frozen = {int(index) for index in frozen_indices or ()
              if 0 <= int(index) < len(original)}
    hard_breaks = {int(index) for index in hard_breaks_after or ()}
    deterministic_items, break_after, deterministic_merges, changed = _deterministic_prepare(
        original, frozen=frozen, hard_breaks=hard_breaks)
    boundary_locks = hard_breaks | frozen | {index - 1 for index in frozen}
    for index in boundary_locks:
        if 0 <= index < len(break_after):
            break_after[index] = True
    if deterministic_stage is not None:
        for index, before in enumerate(original):
            deterministic_stage.item(
                index, deterministic_stage.started_monotonic,
                metadata={"kind": "item", "changed": deterministic_items[index] != before},
            )
        deterministic_stage.finish(
            status="completed", deterministic_merge_count=len(deterministic_merges)
        )

    threshold = max(0.0, min(100.0, float(confidence_threshold or 0.0)))
    uncertainty: Dict[int, List[str]] = {}
    for index, text in enumerate(deterministic_items):
        reasons = _uncertainty_reasons(text, _confidence_for(confidence_by_index, index), threshold)
        if reasons:
            uncertainty[index] = reasons
    # 被确定性层判为噪声并清空的行没有可修复的正文：不送模型，模型也无权把它填回来
    # （否则"猜正文"就会以"修一个孤立的句点"的名义重新发生）。
    llm_indices = [index for index in uncertainty
                   if deterministic_items[index] and index not in frozen]
    # 碎片两侧的断段决定由确定性层拍板，模型只能改文字、不能把这一句重新截断。
    stray_spans = [
        list(merge.get("input_indices") or [])
        for merge in deterministic_merges
        if merge.get("reason") == REASON_STRAY_PUNCTUATION
    ]
    locked_breaks = {int(index) for span in stray_spans for index in span[:2]} | boundary_locks
    locked_tails = {int(span[0]) for span in stray_spans if span}
    batches: List[List[Tuple[int, str]]] = []
    current: List[Tuple[int, str]] = []
    chars = 0
    for index in llm_indices:
        text = deterministic_items[index]
        size = len(text) + 24
        if current and (len(current) >= max_batch_items or chars + size > max_batch_chars):
            batches.append(current)
            current, chars = [], 0
        current.append((index, text))
        chars += size
    if current:
        batches.append(current)

    cleaned_items = list(deterministic_items)
    merges: List[Dict[str, Any]] = []
    warnings: List[str] = []
    failed_batches = 0
    retry_counts: Dict[int, int] = {}
    api_latencies: Dict[int, float] = {}
    queue_waits: Dict[int, float] = {}
    checkpoint_store = _CleanupCheckpointStore(checkpoint_dir) if checkpoint_dir else None
    language_context = _language_context(
        ocr_language=ocr_language, source_language=source_language,
        text_direction=text_direction, ocr_engine_code=ocr_engine_code,
        ocr_backend=ocr_backend)
    system_prompt = SYSTEM_PROMPT + language_context
    fingerprint = hashlib.sha256(json.dumps({
        "items": deterministic_items,
        "llm_indices": llm_indices,
        "context_window_version": CONTEXT_WINDOW_VERSION,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "max_batch_chars": int(max_batch_chars),
        "max_batch_items": int(max_batch_items),
        "confidence_threshold": threshold,
        "ocr_language": str(ocr_language or ""),
        "ocr_engine_code": str(ocr_engine_code or ""),
        "text_direction": str(text_direction or ""),
        "source_language": str(source_language or ""),
        # The prompt names the actual OCR engine. Preserve legacy Tesseract
        # fingerprints while separating the newly supported Paddle prompt.
        **({"ocr_backend": str(ocr_backend)} if ocr_backend != "tesseract" else {}),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    checkpoint_results, _checkpoint_manifest = (
        checkpoint_store.load(fingerprint) if checkpoint_store else ({}, {}))
    result_map: Dict[int, Dict[str, Any]] = {}
    for batch_index, value in checkpoint_results.items():
        result_map[batch_index] = value
        retry_counts[batch_index] = int(value.get("retry_count") or 0)
        api_latencies[batch_index] = float(value.get("api_latency_s") or 0.0)
        queue_waits[batch_index] = float(value.get("queue_wait_s") or 0.0)
    resumed_batches = len(result_map)
    checkpoint_lock = threading.RLock()

    def save_batch_checkpoint(batch_index: int, result: Dict[str, Any]) -> None:
        """Persist exactly one completed batch — no growing payload."""
        if checkpoint_store is None:
            return
        with checkpoint_lock:
            try:
                checkpoint_store.save_batch(fingerprint, batch_index, result)
            except OSError:
                pass

    def save_checkpoint(status: str = "running") -> None:
        if checkpoint_store is None:
            return
        with checkpoint_lock:
            try:
                checkpoint_store.save_manifest(
                    fingerprint, batches, status=status,
                    completed=sum(1 for value in result_map.values()
                                  if value.get("status") == "completed"),
                    failed=sum(1 for value in result_map.values()
                               if value.get("status") != "completed"))
            except OSError:
                pass

    def check_cancel() -> None:
        if _is_cancelled(cancel_check):
            raise RuntimeError("任务已请求取消")

    # Constant-size run record written up front, so a crash leaves evidence of
    # the requested batch plan rather than an empty directory.
    save_checkpoint("running")
    request_gate = _RequestGate(request_interval_seconds)

    def call_with_retry(prompt: str, expected: int) -> Tuple[List[Dict[str, Any]], int, float, float]:
        retry_count = 0
        total_api_latency = 0.0
        total_gate_wait = 0.0
        while True:
            check_cancel()
            total_gate_wait += request_gate.wait(cancel_check)
            started = time.perf_counter()
            try:
                response = _call_model(call_llm, provider, api_key, model, prompt,
                                       expected, system_prompt)
                total_api_latency += time.perf_counter() - started
                check_cancel()
                return _decode(response, expected), retry_count, total_api_latency, total_gate_wait
            except Exception as exc:  # noqa: BLE001 - classify before deciding to retry
                raise_if_fatal(exc)
                total_api_latency += time.perf_counter() - started
                if "任务已请求取消" in str(exc):
                    raise
                if not _retryable(exc) or retry_count >= max(0, int(max_retries or 0)):
                    # Preserve the attempt accounting even when the final
                    # provider/protocol error falls back to the source text.
                    # The cleanup caller must not lose evidence merely because
                    # the last retry raised instead of returning a row set.
                    setattr(exc, "_cleanup_retry_count", retry_count)
                    setattr(exc, "_cleanup_api_latency_s", total_api_latency)
                    setattr(exc, "_cleanup_gate_wait_s", total_gate_wait)
                    raise
                retry_count += 1
                delay = max(0.0, float(retry_backoff_seconds or 0.0)) * (2 ** (retry_count - 1))
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    check_cancel()
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def run_batch(batch_index: int, batch: List[Tuple[int, str]], queue_wait_s: float):
        numbered = "\n".join(f"{local}. {text}" for local, (_, text) in enumerate(batch))
        target_indices = {index for index, _ in batch}
        context_rows = []
        context_seen = set()
        context_budget = max(
            0, min(1200, int(max_batch_chars) - len(numbered) - 256)
        )
        context_chars = 0
        for global_index, _ in batch:
            for neighbor_index in (global_index - 1, global_index + 1):
                if (neighbor_index < 0 or neighbor_index >= len(deterministic_items)
                        or neighbor_index in target_indices
                        or neighbor_index in context_seen):
                    continue
                neighbor_text = deterministic_items[neighbor_index]
                if not neighbor_text:
                    continue
                remaining = context_budget - context_chars
                if remaining <= 0:
                    break
                clipped = neighbor_text[:remaining]
                context_rows.append(f"上下文项 {neighbor_index}（只读）：{clipped}")
                context_seen.add(neighbor_index)
                context_chars += len(clipped)
            if context_chars >= context_budget:
                break
        context_section = ""
        if context_rows:
            context_section = (
                "以下相邻项只用于判断断行和段落边界，不要修改，也不要在 JSON 中返回：\n"
                + "\n".join(context_rows) + "\n\n"
            )
        prompt = (
            "下面是按 PDF 阅读顺序提取的目标原文项。请逐项纠错并标记段落边界；"
            "只能返回目标项，index 从 0 开始且必须完整返回。\n\n"
            + context_section + numbered
        )
        try:
            rows, retries, api_latency, gate_wait = call_with_retry(prompt, len(batch))
        except Exception as exc:  # noqa: BLE001 - cleanup is a non-blocking aid
            raise_if_fatal(exc)
            if "任务已请求取消" in str(exc):
                raise
            try:
                import core
                provider_status = core.provider_error_status(exc)
                detail = (provider_status["message"]
                          if provider_status["status"] != "unknown"
                          else str(exc))
            except Exception:  # pragma: no cover - defensive import fallback
                detail = str(exc)
            return {
                "status": "failed", "rows": None, "error": detail,
                "context_item_count": len(context_rows),
                "retry_count": int(getattr(exc, "_cleanup_retry_count", 0) or 0),
                "api_latency_s": float(getattr(exc, "_cleanup_api_latency_s", 0.0) or 0.0),
                "queue_wait_s": queue_wait_s + float(
                    getattr(exc, "_cleanup_gate_wait_s", 0.0) or 0.0),
            }
        return {
            "status": "completed", "rows": rows, "error": None,
            "context_item_count": len(context_rows),
            "retry_count": retries, "api_latency_s": api_latency,
            "queue_wait_s": queue_wait_s + gate_wait,
        }

    # Large books benefit from a small amount of parallelism: one slow relay
    # request should not serialize every independent cleanup batch.  Results
    # are still applied in document order, and each batch keeps the same strict
    # schema/fallback contract as the sequential path.
    workers = max(1, min(int(parallelism or DEFAULT_PARALLELISM), max(1, len(batches))))
    pending = [(index, batch) for index, batch in enumerate(batches)
               if index not in result_map]
    llm_stage = None
    if profiler is not None:
        if batches:
            llm_stage = profiler.start_stage(
                "llm_cleanup", concurrency=workers, batch_count=len(batches),
                item_count=len(llm_indices),
                metadata={"max_concurrency": workers, "cached_batches": len(result_map)},
            )
        else:
            profiler.skipped("llm_cleanup", metadata={"reason": "confidence_gate"})

    progress_lock = threading.RLock()
    active_batches: set[int] = set()
    finished_count = len(result_map)
    first_error: List[BaseException] = []
    stop_event = threading.Event()
    work_queue: queue.Queue = queue.Queue(maxsize=max(1, workers * 2))

    def emit_progress() -> None:
        if not on_progress:
            return
        with progress_lock:
            active = " ".join(f"#{index + 1}" for index in sorted(active_batches))
            suffix = f"；处理中 {active}" if active else ""
            message = (
                f"【阶段一】LLM 原文纠错与断行整理（批次 {finished_count}/{len(batches)}"
                f"{suffix}）…"
            )
        on_progress(message)

    def worker_loop() -> None:
        nonlocal finished_count
        while True:
            task = work_queue.get()
            try:
                if task is None:
                    return
                batch_index, batch, enqueued_at = task
                if stop_event.is_set():
                    continue
                started = time.perf_counter()
                queue_wait_s = max(0.0, started - enqueued_at)
                with progress_lock:
                    active_batches.add(batch_index)
                # 用途标注（#167）：清理请求跑在池线程上，worker 内设置用途。
                import core
                with core.llm_call_purpose(
                        f"原文清理批次 #{batch_index + 1}"):
                    result = run_batch(batch_index, batch, queue_wait_s)
                result_map[batch_index] = result
                retry_counts[batch_index] = int(result.get("retry_count") or 0)
                api_latencies[batch_index] = float(result.get("api_latency_s") or 0.0)
                queue_waits[batch_index] = float(result.get("queue_wait_s") or 0.0)
                if result.get("status") == "completed":
                    save_batch_checkpoint(batch_index, result)
                with progress_lock:
                    active_batches.discard(batch_index)
                    finished_count += 1
                if profiler is not None and llm_stage is not None:
                    llm_stage.item(
                        f"batch-{batch_index + 1}", started,
                        queue_wait_s=result.get("queue_wait_s") or 0.0,
                        api_latency_s=result.get("api_latency_s") or 0.0,
                        retry_count=result.get("retry_count") or 0,
                        status="completed" if result.get("status") == "completed" else "failed",
                        metadata={"kind": "batch", "batch_index": batch_index + 1,
                                  "context_item_count": result.get("context_item_count", 0)},
                    )
                emit_progress()
            except BaseException as exc:  # noqa: BLE001 - propagate cancellation
                if not first_error:
                    first_error.append(exc)
                stop_event.set()
                with progress_lock:
                    active_batches.clear()
            finally:
                work_queue.task_done()

    threads = [threading.Thread(target=worker_loop, name=f"source-cleanup-{index}", daemon=True)
               for index in range(workers)]
    cancelled = False
    try:
        for thread in threads:
            thread.start()
        emit_progress()
        for batch_index, batch in pending:
            check_cancel()
            while not stop_event.is_set():
                try:
                    work_queue.put((batch_index, batch, time.perf_counter()), timeout=0.2)
                    break
                except queue.Full:
                    check_cancel()
            if stop_event.is_set():
                break
        while not stop_event.is_set() and finished_count < len(batches):
            check_cancel()
            time.sleep(0.1)
        save_checkpoint()
    except BaseException:
        cancelled = True
        stop_event.set()
        raise
    finally:
        stop_event.set() if first_error else None
        for _ in threads:
            while True:
                try:
                    work_queue.put(None, timeout=0.2)
                    break
                except queue.Full:
                    if not any(thread.is_alive() for thread in threads):
                        break
        for thread in threads:
            thread.join(timeout=10)
        if first_error and any("任务已请求取消" in str(error) for error in first_error):
            cancelled = True
        save_checkpoint("cancelled" if cancelled else "completed")
        if llm_stage is not None:
            stage_status = ("cancelled" if cancelled else
                            "failed" if first_error else "completed")
            llm_stage.finish(
                status=stage_status,
                failed_batches=sum(
                    1 for value in result_map.values() if value.get("status") != "completed"
                ),
            )
        if first_error:
            raise first_error[0]

    for batch_index, batch in enumerate(batches):
        result = result_map.get(batch_index) or {}
        rows, error = result.get("rows"), result.get("error")
        if error is not None or not isinstance(rows, list) or len(rows) != len(batch):
            failed_batches += 1
            err_msg = str(error or "批次结果缺失或格式不完整")
            warnings.append(
                f"原文纠错批次 {batch_index + 1}/{len(batches)} 失败，已保留提取文本：{err_msg[:180]}"
            )
            for global_index, before in batch:
                cleaned_items[global_index] = before
            continue
        for row, (global_index, before) in zip(rows, batch):
            row_text = row.get("text") if isinstance(row, dict) else None
            if not before:
                # 确定性层清掉的噪声行不接受模型的任何填充。
                cleaned_items[global_index] = ""
                break_after[global_index] = False
                continue
            after = _safe_candidate(before, row_text) if row_text is not None else before
            if global_index in locked_tails:
                guarded = _strip_trailing_sentence_terminal(after)
                if guarded != after:
                    changed.append({"index": global_index, "before": after,
                                    "after": guarded,
                                    "reason": REASON_STRAY_PUNCTUATION})
                    after = guarded
            cleaned_items[global_index] = after
            if global_index not in locked_breaks:
                break_after[global_index] = bool(row.get("break_after")) if isinstance(row, dict) else False
            if after != before:
                changed.append({"index": global_index, "before": before, "after": after})

    output: List[str] = []
    output_input_indices: List[List[int]] = []
    current_indices: List[int] = []
    current_text = ""
    for index, text in enumerate(cleaned_items):
        current_text = _join(current_text, text)
        current_indices.append(index)
        if break_after[index] or index == len(cleaned_items) - 1:
            # Preserve headings, separators, page marks and other non-letter
            # items too.  They are part of the extracted reading order even
            # when translation later treats them as pass-through content.
            if current_text:
                output.append(current_text)
                output_input_indices.append(list(current_indices))
                if len(current_indices) > 1:
                    merges.append({"input_indices": list(current_indices),
                                   "text": current_text})
            current_text, current_indices = "", []
    if not output:
        output = list(original)
        output_input_indices = [[index] for index in range(len(original))]
    status = "completed" if not failed_batches else "partial"
    metadata = {
        "status": status,
        "input_count": len(original),
        "output_count": len(output),
        "batch_count": len(batches),
        "failed_batches": failed_batches,
        "changed_segments": changed,
        "paragraph_merges": merges,
        # Keep the complete input-to-output lineage, including one-to-one rows.
        # Source-admission evidence must survive both model cleanup and later
        # sentence segmentation; paragraph_merges alone only records joins.
        "output_input_indices": output_input_indices,
        "deterministic_merges": deterministic_merges,
        "llm_input_count": len(llm_indices),
        "llm_bypassed_count": len(original) - len(llm_indices),
        "llm_bypass_ratio": round((len(original) - len(llm_indices)) / len(original), 6),
        "confidence_threshold": threshold,
        "uncertainty_counts": {
            reason: sum(reason in reasons for reasons in uncertainty.values())
            for reason in sorted({reason for reasons in uncertainty.values() for reason in reasons})
        },
        "parallelism": workers,
        "max_retries": max(0, int(max_retries or 0)),
        "retry_count": sum(retry_counts.values()),
        "api_latency_s": round(sum(api_latencies.values()), 6),
        "queue_wait_s": round(sum(queue_waits.values()), 6),
        "resumed_batches": resumed_batches,
        "checkpoint_dir": str(checkpoint_store.directory) if checkpoint_store else "",
        "checkpoint_stats": checkpoint_store.stats() if checkpoint_store else {},
        "ocr_language": str(ocr_language or ""),
        "ocr_engine_code": str(ocr_engine_code or ""),
        "text_direction": str(text_direction or ""),
        "source_language": str(source_language or ""),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return output, metadata, warnings
