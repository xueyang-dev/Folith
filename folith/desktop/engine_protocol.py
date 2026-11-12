"""Engine-host protocol: one start line, then optional restart lines.

Why this module exists
----------------------
Architecture Phase 5 (0.8.2) moves the *whole pipeline* into a child process:
the desktop app re-executes itself with ``--engine-host`` and keeps that child
alive for one task run.  E-0 established that no new state channel is needed --
progress, cancellation and exit already travel through ``state_journal.jsonl``
and ``runtime_state.json`` -- so the only thing the pipe carries is what
genuinely cannot be persisted:

* **the start request** (which task, and the resolved model connection);
* **a restart request** after the user changes the connection (the in-process
  path keeps that payload in ``core._RUNTIME_PENDING_RESTART`` precisely
  because it carries the API key and must never be written to disk).

Transport
---------
Line-delimited UTF-8 JSON on the child's **stdin**, following the conventions
already used by :mod:`folith.desktop.protocol` for the launcher/service pipe:
a version field ``v``, a command field ``cmd``, a hard size limit, no embedded
newlines, unknown fields rejected (a typo must fail loudly, not be ignored).

Secret rule
-----------
The model credential appears **only** in these lines.  It is never placed in
``argv``, never in the environment, never written to a file and never logged.
Consequently :class:`EngineProtocolError` messages name the offending *field*
and never quote a value, and :meth:`ConnectionSpec.redacted` is the only view
that may be printed by a caller.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Union

__all__ = [
    "CMD_RESTART",
    "CMD_START",
    "MODE_RESUME",
    "MODE_START",
    "PROTOCOL_VERSION",
    "ConnectionSpec",
    "EngineProtocolError",
    "RestartRequest",
    "StartRequest",
    "decode_line",
    "encode_restart_request",
    "encode_start_request",
]

#: Wire version.  Bumped whenever a field changes meaning; a mismatch is fatal.
PROTOCOL_VERSION = 1

#: Guard against a runaway/corrupt channel rather than a real message.
MAX_MESSAGE_BYTES = 64 * 1024

CMD_START = "start"
CMD_RESTART = "restart"

MODE_START = "start"
MODE_RESUME = "resume"

#: Task ids become directory names, so the wire format only accepts the shape
#: the core itself produces.  This is a path-traversal guard, not cosmetics.
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

_START_KEYS = frozenset({"v", "cmd", "job_id", "mode", "filename",
                         "connection", "reviewer", "reasoning_effort"})
_RESTART_KEYS = frozenset({"v", "cmd", "connection", "reviewer",
                           "reasoning_effort"})
_CONNECTION_KEYS = frozenset({"provider", "model", "base_url", "api_key"})


class EngineProtocolError(ValueError):
    """A malformed or unusable engine-host message.

    The message names fields, never values: a credential mistakenly placed in
    the wrong field must not end up in stderr or a log.
    """


@dataclass(frozen=True)
class ConnectionSpec:
    """The resolved model connection the engine must use.

    Mirrors the connection triple the core already treats as one unit
    (provider / model / endpoint plus the credential) and carries the optional
    independent reviewer role.  ``api_key`` fields are excluded from ``repr`` so
    an incidental traceback or debug print cannot leak them.
    """

    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: str = field(default="", repr=False)
    reviewer_provider: str = ""
    reviewer_model: str = ""
    reviewer_base_url: Optional[str] = None
    reviewer_api_key: str = field(default="", repr=False)
    reasoning_effort: str = ""

    @property
    def has_reviewer(self) -> bool:
        return bool(self.reviewer_provider and self.reviewer_model)

    def redacted(self) -> Dict[str, Any]:
        """The only diagnostics-safe view of a connection."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": "***" if self.api_key else "",
            "reviewer_provider": self.reviewer_provider,
            "reviewer_model": self.reviewer_model,
            "reviewer_base_url": self.reviewer_base_url,
            "reviewer_api_key": "***" if self.reviewer_api_key else "",
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class StartRequest:
    """The first line on stdin: run (or resume) exactly one task."""

    job_id: str
    connection: ConnectionSpec
    mode: str = MODE_RESUME
    filename: str = ""
    protocol_version: int = PROTOCOL_VERSION

    def redacted(self) -> Dict[str, Any]:
        return {
            "v": self.protocol_version,
            "cmd": CMD_START,
            "job_id": self.job_id,
            "mode": self.mode,
            "filename": self.filename,
            "connection": self.connection.redacted(),
        }


@dataclass(frozen=True)
class RestartRequest:
    """A later line on the same stdin: re-enter the pipeline on a new connection.

    Only ever held in memory (it includes the credential), and applied at the
    pipeline's next batch boundary -- the same rule the in-process
    ``core._RUNTIME_PENDING_RESTART`` path already implements.
    """

    connection: ConnectionSpec
    protocol_version: int = PROTOCOL_VERSION

    def redacted(self) -> Dict[str, Any]:
        return {
            "v": self.protocol_version,
            "cmd": CMD_RESTART,
            "connection": self.connection.redacted(),
        }


Request = Union[StartRequest, RestartRequest]


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

def decode_line(line: Union[bytes, str]) -> Request:
    """Parse one protocol line into a validated :class:`StartRequest`/``RestartRequest``.

    Raises :class:`EngineProtocolError` for anything that is not exactly one
    well-formed message of the configured protocol version.
    """
    message = _decode_object(line)
    version = message.get("v")
    if version is None:
        raise EngineProtocolError("missing protocol version field 'v'")
    if isinstance(version, bool) or not isinstance(version, int):
        raise EngineProtocolError("protocol version 'v' must be an integer")
    if version != PROTOCOL_VERSION:
        raise EngineProtocolError(
            f"unsupported engine-host protocol version (this build speaks "
            f"v{PROTOCOL_VERSION})")
    command = message.get("cmd")
    if command is None:
        raise EngineProtocolError("missing 'cmd' field")
    if command == CMD_START:
        return _decode_start(message)
    if command == CMD_RESTART:
        return _decode_restart(message)
    raise EngineProtocolError("unknown 'cmd' (expected 'start' or 'restart')")


def _decode_object(line: Union[bytes, str]) -> Dict[str, Any]:
    if isinstance(line, bytes):
        if len(line) > MAX_MESSAGE_BYTES:
            raise EngineProtocolError("protocol message exceeds the size limit")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EngineProtocolError(
                "protocol message is not valid UTF-8") from exc
    else:
        text = line
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise EngineProtocolError("protocol message exceeds the size limit")
    text = text.strip()
    if not text:
        raise EngineProtocolError("empty protocol message")
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineProtocolError(
            "protocol message is not valid JSON "
            f"(line {exc.lineno} column {exc.colno})") from exc
    if not isinstance(message, dict):
        raise EngineProtocolError("protocol message must be a JSON object")
    return message


def _decode_start(message: Mapping[str, Any]) -> StartRequest:
    _reject_unknown(message, _START_KEYS, "start request")
    job_id = _required_text(message, "job_id")
    if not _JOB_ID.fullmatch(job_id):
        raise EngineProtocolError(
            "'job_id' must match [A-Za-z0-9_-]{1,128}")
    mode = _optional_text(message, "mode", MODE_RESUME)
    if mode not in (MODE_START, MODE_RESUME):
        raise EngineProtocolError("'mode' must be 'start' or 'resume'")
    filename = _optional_text(message, "filename", "")
    if "\x00" in filename or "\n" in filename or "\r" in filename:
        raise EngineProtocolError("'filename' must be a single-line string")
    return StartRequest(
        job_id=job_id,
        connection=_decode_connection(message),
        mode=mode,
        filename=filename,
    )


def _decode_restart(message: Mapping[str, Any]) -> RestartRequest:
    _reject_unknown(message, _RESTART_KEYS, "restart request")
    return RestartRequest(connection=_decode_connection(message))


def _decode_connection(message: Mapping[str, Any]) -> ConnectionSpec:
    raw = message.get("connection")
    if raw is None:
        raise EngineProtocolError("missing 'connection' object")
    if not isinstance(raw, dict):
        raise EngineProtocolError("'connection' must be a JSON object")
    _reject_unknown(raw, _CONNECTION_KEYS, "connection")
    provider = _required_text(raw, "provider", where="connection")
    model = _required_text(raw, "model", where="connection")
    api_key = _required_text(raw, "api_key", where="connection",
                             allow_empty=True)
    base_url = _optional_text(raw, "base_url", None, where="connection")

    reviewer_raw = message.get("reviewer")
    reviewer: Dict[str, Any] = {}
    if reviewer_raw is not None:
        if not isinstance(reviewer_raw, dict):
            raise EngineProtocolError("'reviewer' must be a JSON object or null")
        _reject_unknown(reviewer_raw, _CONNECTION_KEYS, "reviewer")
        reviewer = {
            "provider": _required_text(reviewer_raw, "provider",
                                       where="reviewer"),
            "model": _required_text(reviewer_raw, "model", where="reviewer"),
            "api_key": _required_text(reviewer_raw, "api_key",
                                      where="reviewer", allow_empty=True),
            "base_url": _optional_text(reviewer_raw, "base_url", None,
                                       where="reviewer"),
        }

    reasoning_effort = _optional_text(message, "reasoning_effort", "")
    return ConnectionSpec(
        provider=provider,
        model=model,
        base_url=base_url or None,
        api_key=api_key,
        reviewer_provider=reviewer.get("provider", ""),
        reviewer_model=reviewer.get("model", ""),
        reviewer_base_url=reviewer.get("base_url") or None,
        reviewer_api_key=reviewer.get("api_key", ""),
        reasoning_effort=reasoning_effort,
    )


def _reject_unknown(message: Mapping[str, Any], allowed: frozenset,
                    where: str) -> None:
    unknown = sorted(key for key in message if key not in allowed)
    if unknown:
        # Field *names* only: a value in the wrong place may be a credential.
        raise EngineProtocolError(
            f"unknown {where} field(s): {', '.join(unknown)}")


def _required_text(message: Mapping[str, Any], key: str, *,
                   where: str = "request", allow_empty: bool = False) -> str:
    if key not in message:
        raise EngineProtocolError(f"missing '{_path(where, key)}'")
    value = message[key]
    if not isinstance(value, str):
        raise EngineProtocolError(f"'{_path(where, key)}' must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise EngineProtocolError(f"'{_path(where, key)}' must not be empty")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise EngineProtocolError(
            f"'{_path(where, key)}' must be a single-line string")
    return text


def _optional_text(message: Mapping[str, Any], key: str, default: Any, *,
                   where: str = "request") -> Any:
    if key not in message or message[key] is None:
        return default
    value = message[key]
    if not isinstance(value, str):
        raise EngineProtocolError(f"'{_path(where, key)}' must be a string")
    text = value.strip()
    if "\x00" in text or "\n" in text or "\r" in text:
        raise EngineProtocolError(
            f"'{_path(where, key)}' must be a single-line string")
    return text


def _path(where: str, key: str) -> str:
    return key if where == "request" else f"{where}.{key}"


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def encode_start_request(job_id: str, connection: ConnectionSpec, *,
                         mode: str = MODE_RESUME,
                         filename: str = "") -> bytes:
    """One start line, newline-terminated.  The caller writes it to stdin."""
    message: Dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "cmd": CMD_START,
        "job_id": job_id,
        "mode": mode,
        "connection": _connection_message(connection),
    }
    if filename:
        message["filename"] = filename
    _add_optional_roles(message, connection)
    return encode_message(message)


def encode_restart_request(connection: ConnectionSpec) -> bytes:
    """One restart line, newline-terminated, carrying only the new connection."""
    message: Dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "cmd": CMD_RESTART,
        "connection": _connection_message(connection),
    }
    _add_optional_roles(message, connection)
    return encode_message(message)


def _connection_message(connection: ConnectionSpec) -> Dict[str, Any]:
    return {
        "provider": connection.provider,
        "model": connection.model,
        "base_url": connection.base_url or "",
        "api_key": connection.api_key,
    }


def _add_optional_roles(message: Dict[str, Any], connection: ConnectionSpec) -> None:
    if connection.has_reviewer:
        message["reviewer"] = {
            "provider": connection.reviewer_provider,
            "model": connection.reviewer_model,
            "base_url": connection.reviewer_base_url or "",
            "api_key": connection.reviewer_api_key,
        }
    if connection.reasoning_effort:
        message["reasoning_effort"] = connection.reasoning_effort


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Serialise one control message with the same limits as the wire format."""
    payload = json.dumps(dict(message), ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise EngineProtocolError("protocol message exceeds the size limit")
    if b"\n" in payload:  # pragma: no cover - json.dumps always escapes it
        raise EngineProtocolError("protocol message must not contain newlines")
    return payload + b"\n"
