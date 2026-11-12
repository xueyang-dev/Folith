"""E-2: the engine-host wire format (``folith.desktop.engine_protocol``).

The start request and the restart line are the only things that travel on the
pipe, and they carry the model credential.  These tests pin the two properties
that matter: a malformed line fails loudly (never silently runs an empty job),
and no error path ever echoes a secret.
"""
from __future__ import annotations

import json

import pytest

from folith.desktop import engine_protocol as protocol
from folith.desktop.engine_protocol import ConnectionSpec, EngineProtocolError

# Deliberately not shaped like a provider key: releases/preflight flags any
# ``sk-`` literal of 20+ characters in the HEAD tree as a suspected real
# credential, and a fixture is not worth an exemption there.
SECRET = "FAKE-e2-protocol-secret-do-not-print"
REVIEWER_SECRET = "FAKE-e2-reviewer-secret-do-not-print"


def _connection(**overrides) -> ConnectionSpec:
    values = dict(provider="自定义中转站", model="m1",
                  base_url="http://127.0.0.1:9/v1", api_key=SECRET)
    values.update(overrides)
    return ConnectionSpec(**values)


def _start_message(**overrides) -> dict:
    message = {
        "v": protocol.PROTOCOL_VERSION,
        "cmd": "start",
        "job_id": "job-123",
        "mode": "start",
        "connection": {
            "provider": "自定义中转站",
            "model": "m1",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": SECRET,
        },
    }
    message.update(overrides)
    return message


def _decode(message: dict):
    return protocol.decode_line(json.dumps(message, ensure_ascii=False))


# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------

def test_start_line_round_trips_with_reviewer_and_reasoning():
    connection = _connection(
        reviewer_provider="OpenAI", reviewer_model="r1",
        reviewer_base_url="https://reviewer.example/v1",
        reviewer_api_key=REVIEWER_SECRET, reasoning_effort="low")
    line = protocol.encode_start_request("job-123", connection, mode="resume",
                                         filename="book.docx")
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    request = protocol.decode_line(line)
    assert isinstance(request, protocol.StartRequest)
    assert request.job_id == "job-123"
    assert request.mode == "resume"
    assert request.filename == "book.docx"
    assert request.connection.provider == "自定义中转站"
    assert request.connection.model == "m1"
    assert request.connection.base_url == "http://127.0.0.1:9/v1"
    assert request.connection.api_key == SECRET
    assert request.connection.has_reviewer is True
    assert request.connection.reviewer_api_key == REVIEWER_SECRET
    assert request.connection.reasoning_effort == "low"


def test_restart_line_round_trips_and_carries_only_the_connection():
    line = protocol.encode_restart_request(_connection(model="m2"))
    request = protocol.decode_line(line)
    assert isinstance(request, protocol.RestartRequest)
    assert request.connection.model == "m2"
    assert set(json.loads(line)) == {"v", "cmd", "connection"}
    assert not hasattr(request, "job_id")


def test_mode_defaults_to_resume_when_omitted():
    message = _start_message()
    del message["mode"]
    request = _decode(message)
    assert request.mode == protocol.MODE_RESUME


def test_empty_api_key_is_explicit_and_allowed():
    message = _start_message()
    message["connection"]["api_key"] = ""
    request = _decode(message)
    assert request.connection.api_key == ""
    assert request.connection.redacted()["api_key"] == ""


# ---------------------------------------------------------------------------
# validation failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line, needle", [
    (b"", "empty"),
    (b"   \n", "empty"),
    (b"not json\n", "not valid JSON"),
    (b"[1, 2]\n", "JSON object"),
    (json.dumps({"cmd": "start"}).encode(), "protocol version"),
    (json.dumps({"v": 1}).encode(), "cmd"),
    (json.dumps({"v": 1, "cmd": "nope"}).encode(), "unknown 'cmd'"),
    (json.dumps({"v": 2, "cmd": "start", "job_id": "j"}).encode(), "unsupported"),
    (json.dumps({"v": True, "cmd": "start", "job_id": "j"}).encode(),
     "must be an integer"),
    (json.dumps({"v": "1", "cmd": "start", "job_id": "j"}).encode(),
     "must be an integer"),
])
def test_malformed_lines_are_rejected(line, needle):
    with pytest.raises(EngineProtocolError, match=needle):
        protocol.decode_line(line)


def test_start_requires_a_safe_job_id():
    for job_id in ("", "   ", "../escape", "a/b", "a\nb", "x" * 129):
        message = _start_message(job_id=job_id)
        with pytest.raises(EngineProtocolError):
            _decode(message)


def test_start_rejects_the_wrong_mode():
    with pytest.raises(EngineProtocolError, match="'mode'"):
        _decode(_start_message(mode="restart"))


def test_connection_fields_are_required_and_typed():
    for field in ("provider", "model", "api_key"):
        message = _start_message()
        del message["connection"][field]
        with pytest.raises(EngineProtocolError, match=field):
            _decode(message)
    message = _start_message()
    message["connection"]["model"] = 3
    with pytest.raises(EngineProtocolError, match="model"):
        _decode(message)


def test_unknown_fields_are_rejected_by_name_only():
    message = _start_message()
    # A credential typed into the wrong place must not be reproduced in the
    # error text (the GUI is trusted, but stderr is a log).
    message["connection"]["api"] = SECRET
    with pytest.raises(EngineProtocolError) as info:
        _decode(message)
    text = str(info.value)
    assert "api" in text
    assert SECRET not in text


def test_values_in_wrong_types_never_echo_the_value():
    message = _start_message()
    message["connection"]["api_key"] = SECRET  # still a string: valid
    assert _decode(message).connection.api_key == SECRET
    message["connection"]["api_key"] = 12345
    with pytest.raises(EngineProtocolError) as info:
        _decode(message)
    assert "12345" not in str(info.value)
    assert "api_key" in str(info.value)


def test_restart_rejects_start_only_fields():
    line = protocol.encode_restart_request(_connection())
    message = json.loads(line)
    message["job_id"] = "job-123"
    with pytest.raises(EngineProtocolError, match="job_id"):
        protocol.decode_line(json.dumps(message))


def test_oversized_message_is_rejected():
    huge = {"v": 1, "cmd": "start", "job_id": "j" * (protocol.MAX_MESSAGE_BYTES + 1)}
    with pytest.raises(EngineProtocolError, match="size limit"):
        protocol.decode_line(json.dumps(huge))


def test_filename_must_be_single_line():
    with pytest.raises(EngineProtocolError, match="filename"):
        _decode(_start_message(filename="a\nb"))


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------

def test_redacted_views_hide_both_credentials():
    request = protocol.decode_line(protocol.encode_start_request(
        "job-123",
        _connection(reviewer_provider="OpenAI", reviewer_model="r1",
                    reviewer_api_key=REVIEWER_SECRET)))
    text = json.dumps(request.redacted(), ensure_ascii=False)
    assert SECRET not in text
    assert REVIEWER_SECRET not in text
    assert "***" in text
    # repr of the dataclasses must not contain them either.
    assert SECRET not in repr(request)
    assert REVIEWER_SECRET not in repr(request.connection)


def test_engine_process_switch_defaults_off(monkeypatch):
    from folith.desktop import engine_host

    monkeypatch.delenv(engine_host.ENGINE_PROCESS_ENV, raising=False)
    assert engine_host.engine_process_enabled() is False
    for off in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(engine_host.ENGINE_PROCESS_ENV, off)
        assert engine_host.engine_process_enabled() is False
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv(engine_host.ENGINE_PROCESS_ENV, on)
        assert engine_host.engine_process_enabled() is True
