"""Desktop Instance Identity protocol: Launcher <-> this instance's service.

Roadmap §3.1: the control plane only exists to let the desktop launcher prove
that *this* Streamlit service is the child process it just created, on the port
it actually bound.  It is deliberately not a general RPC layer.

Transport: the launcher spawns the service with piped stdin/stdout.  Stdout is
reserved for this protocol (the service re-points its own stdout at stderr so
Streamlit logs never corrupt the channel); stdin carries control commands.

The `startup_token` is generated per launch and written *only* through the pipe,
never on the command line, in a URL, in a log, or in diagnostics, so an
unrelated localhost HTTP server or a stale Folith process cannot impersonate
the instance.

Messages are single-line UTF-8 JSON objects.
"""
from __future__ import annotations

import hmac
import json
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# Guard against a runaway/corrupt channel rather than a real message.
MAX_MESSAGE_BYTES = 64 * 1024

CMD_HELLO = "hello"
CMD_STOP = "stop"
EVENT_READY = "ready"
EVENT_STOPPED = "stopped"
EVENT_ERROR = "error"


class ProtocolError(RuntimeError):
    """Malformed or oversized control-channel traffic."""


class IdentityMismatch(ProtocolError):
    """The service answered, but not as the instance this launcher created."""


@dataclass(frozen=True)
class InstanceIdentity:
    """Per-launch identity shared by exactly one launcher/service pair."""

    instance_id: str
    startup_token: str
    launcher_pid: int
    app_version: str
    build_id: str
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def create(
        cls,
        *,
        launcher_pid: int,
        app_version: str,
        build_id: str,
    ) -> "InstanceIdentity":
        return cls(
            instance_id=uuid.uuid4().hex,
            startup_token=secrets.token_urlsafe(32),
            launcher_pid=int(launcher_pid),
            app_version=str(app_version),
            build_id=str(build_id),
        )

    def hello_message(self) -> dict[str, Any]:
        return {
            "v": self.protocol_version,
            "cmd": CMD_HELLO,
            "instance_id": self.instance_id,
            "startup_token": self.startup_token,
            "launcher_pid": self.launcher_pid,
            "app_version": self.app_version,
            "build_id": self.build_id,
        }

    def stop_message(self) -> dict[str, Any]:
        return {
            "v": self.protocol_version,
            "cmd": CMD_STOP,
            "startup_token": self.startup_token,
        }

    def redacted(self) -> dict[str, Any]:
        """Diagnostics-safe view: the token never leaves the control channel."""
        return {
            "instance_id": self.instance_id,
            "launcher_pid": self.launcher_pid,
            "app_version": self.app_version,
            "build_id": self.build_id,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class ServiceIdentity:
    """What the service reports once it is genuinely serving."""

    instance_id: str
    startup_token: str
    service_pid: int
    port: int
    app_version: str
    build_id: str
    protocol_version: int = PROTOCOL_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def ready_message(self) -> dict[str, Any]:
        message = {
            "v": self.protocol_version,
            "event": EVENT_READY,
            "instance_id": self.instance_id,
            "startup_token": self.startup_token,
            "service_pid": int(self.service_pid),
            "port": int(self.port),
            "app_version": self.app_version,
            "build_id": self.build_id,
        }
        message.update(self.extra)
        return message

    def redacted(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "service_pid": self.service_pid,
            "port": self.port,
            "app_version": self.app_version,
            "build_id": self.build_id,
            "protocol_version": self.protocol_version,
            **self.extra,
        }


def encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("control message exceeds size limit")
    if b"\n" in payload:
        raise ProtocolError("control message must not contain newlines")
    return payload + b"\n"


def decode_message(line: bytes | str) -> dict[str, Any]:
    if isinstance(line, bytes):
        if len(line) > MAX_MESSAGE_BYTES:
            raise ProtocolError("control message exceeds size limit")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise ProtocolError("control message is not valid UTF-8") from exc
    else:
        text = line
    text = text.strip()
    if not text:
        raise ProtocolError("empty control message")
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"control message is not valid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("control message must be a JSON object")
    return message


def verify_ready_message(message: dict[str, Any], identity: InstanceIdentity) -> ServiceIdentity:
    """Verify a `ready` event really belongs to `identity`.

    Raises IdentityMismatch on any disagreement; a service that merely answers
    HTTP 200 on some port can never satisfy this.
    """
    if message.get("event") != EVENT_READY:
        raise IdentityMismatch(
            f"expected a {EVENT_READY!r} event, received {message.get('event')!r}"
        )
    version = message.get("v")
    if version != identity.protocol_version:
        raise IdentityMismatch(
            f"protocol version mismatch: service={version!r} launcher={identity.protocol_version!r}"
        )
    if message.get("instance_id") != identity.instance_id:
        raise IdentityMismatch("instance_id does not match this launch")
    token = message.get("startup_token")
    if not isinstance(token, str) or not hmac.compare_digest(token, identity.startup_token):
        raise IdentityMismatch("startup_token does not match this launch")
    try:
        service_pid = int(message.get("service_pid"))
        port = int(message.get("port"))
    except (TypeError, ValueError) as exc:
        raise IdentityMismatch("service_pid/port must be integers") from exc
    if service_pid <= 0:
        raise IdentityMismatch("service_pid must be positive")
    if not (0 < port < 65536):
        raise IdentityMismatch(f"service reported an unusable port: {port!r}")
    app_version = message.get("app_version")
    build_id = message.get("build_id")
    if not isinstance(app_version, str) or not isinstance(build_id, str):
        raise IdentityMismatch("app_version/build_id must be strings")
    known = {
        "v", "event", "instance_id", "startup_token", "service_pid",
        "port", "app_version", "build_id",
    }
    extra = {key: value for key, value in message.items() if key not in known}
    return ServiceIdentity(
        instance_id=identity.instance_id,
        startup_token=identity.startup_token,
        service_pid=service_pid,
        port=port,
        app_version=app_version,
        build_id=build_id,
        protocol_version=identity.protocol_version,
        extra=extra,
    )
