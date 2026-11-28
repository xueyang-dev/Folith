"""CX-34: URL credentials must not travel inside task state or a backup.

A custom relay may authenticate through the endpoint itself —
``https://user:pass@host/v1?api_key=xxx`` — and the connection form accepts it
because that is what the request needs.  Two copies outlive the process and are
read by people the secret is not for:

* ``runtime_state.json`` records ``worker_connection`` (the connection identity
  the resume path compares), and the backup export ships that file;
* an upgraded task's ``state.json`` still holds the raw role endpoint that 0.8.3
  wrote before #637 cleaned new ones.

Both are cleaned here.  A credential-only edit must still count as a connection
change, so the dropped part contributes a short fingerprint instead of
disappearing.
"""
import json

import pytest

import core
from folith.engine import llm_provider
from folith.qt import support as qt_support


CREDENTIAL_URL = "https://user:pass@relay.example/v1?api_key=xxx&region=eu"
CLEAN_URL = "https://relay.example/v1?region=eu"


def _kwargs(base_url, api_key="sk-translator"):
    return {"provider": "Custom", "model": "relay-model",
            "translator_base_url": base_url, "api_key": api_key}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    return data_dir


def test_connection_signature_keeps_url_credentials_out():
    signature = core._connection_signature_from_kwargs(_kwargs(CREDENTIAL_URL))
    stored = json.dumps(signature, ensure_ascii=False)
    assert "pass" not in stored, stored
    assert "xxx" not in stored, stored
    assert "user" not in stored, stored
    translator = signature["translator"]
    # Routing information survives; only the credential carriers are gone.
    assert translator["base_url"] == CLEAN_URL
    # The dropped part still participates, so a credential edit is visible.
    assert translator["base_url_credential_fp"]


def test_clean_endpoint_signature_keeps_the_historical_shape():
    signature = core._connection_role_signature("Custom", "m", "https://relay.example/v1/")
    # No credentials -> exactly the historical keys (an existing signature still
    # compares equal); only the trailing slash is normalized, as before.
    assert signature == {"provider": "Custom", "model": "m",
                         "base_url": "https://relay.example/v1"}


def test_credential_only_edit_still_reads_as_a_connection_change(isolated):
    job_id = "cx34-cred-change"
    core.save_job_state(job_id, core.new_job_state("x.txt"))
    core.update_runtime_state(
        job_id, on_timeout="raise", require_replace=True,
        worker_connection=core._connection_signature_from_kwargs(_kwargs(CREDENTIAL_URL)))

    assert core._worker_connection_changed(job_id, _kwargs(CREDENTIAL_URL)) is False
    other = _kwargs("https://user:other@relay.example/v1?api_key=xxx&region=eu")
    assert core._worker_connection_changed(job_id, other) is True
    # A routing change is still a change, and a legacy raw-URL record compares
    # unequal exactly once (the intended single restart, not a migration).
    assert core._worker_connection_changed(
        job_id, _kwargs("https://relay.example/v2?api_key=xxx&region=eu")) is True


def test_legacy_raw_url_record_compares_as_changed_once(isolated):
    job_id = "cx34-legacy"
    core.save_job_state(job_id, core.new_job_state("x.txt"))
    core.update_runtime_state(
        job_id, on_timeout="raise", require_replace=True,
        worker_connection={
            "translator": {"provider": "Custom", "model": "relay-model",
                           "base_url": CREDENTIAL_URL, "api_key_fp": "abc"},
            "reviewer": {"provider": "", "model": "", "base_url": "", "api_key_fp": ""},
            "reasoning_effort": "",
        })
    assert core._worker_connection_changed(job_id, _kwargs(CREDENTIAL_URL)) is True


def test_backup_copy_drops_credentials_from_runtime_state_and_state():
    runtime_state = {
        "status": "interrupted",
        "worker_connection": {
            "translator": {"provider": "Custom", "model": "relay-model",
                           "base_url": CREDENTIAL_URL, "api_key_fp": "abc"},
            "reviewer": {"provider": "", "model": "", "base_url": "", "api_key_fp": ""},
            "reasoning_effort": "",
        },
    }
    cleaned = qt_support._clean_export_member(
        "runtime_state.json", json.dumps(runtime_state).encode("utf-8"))
    text = cleaned.decode("utf-8")
    assert "pass" not in text, text
    assert "xxx" not in text, text
    assert CLEAN_URL in text

    legacy_state = {
        "pipeline_config": {
            "translator": {"provider": "Custom", "model": "relay-model",
                           "base_url": CREDENTIAL_URL, "configured": True},
            "reviewer": {"provider": "", "model": "", "base_url": "", "configured": False},
        },
        "auxiliary_config": {"provider": "Custom", "model": "cheap",
                             "base_url": "https://user:pass@aux.example/v1?key=xxx",
                             "configured": True},
    }
    cleaned = qt_support._clean_export_member(
        "state.json", json.dumps(legacy_state).encode("utf-8"))
    text = cleaned.decode("utf-8")
    assert "pass" not in text, text
    assert "xxx" not in text, text
    assert "user:" not in text, text
    parsed = json.loads(text)
    assert parsed["pipeline_config"]["translator"]["base_url"] == CLEAN_URL
    assert parsed["auxiliary_config"]["base_url"] == "https://aux.example/v1"


class _RemoteError(Exception):
    """An error shaped like a relay reply that echoes the URL credential."""

    status_code = 400
    body = {"error": {"message": "bad request"}}
    response = object()
    request_id = "pass"


def test_profile_warning_does_not_persist_a_request_id():
    text = llm_provider.persisted_auxiliary_failure(
        _RemoteError("bad request"), api_key="sk-translator", stage="document_profile")
    assert "request_id" not in text, text
    assert "pass" not in text, text
    # The summary itself is still useful.
    assert "class=" in text and "stage=document_profile" in text


def test_other_diagnostics_keep_their_request_id():
    """The switch is scoped to the persisted profile path, not to every caller."""
    detail = llm_provider.provider_error_detail(
        _RemoteError("bad request"), stage="translate", known_secrets=("sk-translator",))
    assert "request_id=pass" in detail, detail
