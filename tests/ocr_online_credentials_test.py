"""OCR connection configuration is independent of the translation model.

The contract being pinned:

* the online OCR connection lives in its own 0600 file;
* a task that has saved its (secret-free) OCR connection keeps that service,
  model and endpoint even after the user switches the translation model;
* referencing the saved translation credentials reuses the *credential* but not
  a silently changed OCR target.
"""
from __future__ import annotations

import json
import os
import stat

import core


def test_ocr_connection_file_is_separate_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_provider_config("DeepSeek", "deepseek-flash", "translation-key",
                              "https://api.deepseek.com")
    path = core.save_ocr_online_config(provider="OpenCode Go", model="vision-A",
                                       api_key="ocr-key", base_url="")
    assert path == core.ocr_online_config_path()
    assert path != core.provider_config_path()
    assert path.parent == core.provider_config_path().parent
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, oct(mode)
    profile = core.load_ocr_online_config()
    assert profile["provider"] == "OpenCode Go"
    assert profile["model"] == "vision-A"
    assert profile["api_key"] == "ocr-key"
    # The translation profile is untouched by the OCR settings.
    assert core.load_provider_config()["model"] == "deepseek-flash"


def test_a_saved_ocr_connection_survives_a_translation_model_change(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_ocr_online_config(provider="OpenCode Go", model="vision-A",
                                api_key="ocr-key", base_url="")
    descriptor, _error = core.resolve_ocr_online_config({"credential_source": "ocr_profile"})
    snapshot = descriptor.describe()
    assert "ocr-key" not in json.dumps(snapshot, ensure_ascii=False)

    # The user switches the translation engine entirely.
    core.save_provider_config("DeepSeek", "deepseek-flash", "translation-key",
                              "https://api.deepseek.com")

    resolved, error = core.resolve_ocr_online_config(snapshot)
    assert error is None
    assert resolved.provider == "OpenCode Go"
    assert resolved.model == "vision-A"
    assert resolved.base_url == "https://opencode.ai/zen/go/v1"
    assert resolved.api_key == "ocr-key"

    # The saved OCR target still wins over the (now different) translation service,
    # and its key follows it: DeepSeek's translation key must never be sent to the
    # OpenCode Go endpoint, so the key comes from the connection that owns that endpoint.
    referencing = {**snapshot, "credential_source": "translation_profile"}
    resolved_reference, error = core.resolve_ocr_online_config(referencing)
    assert error is None
    assert resolved_reference.provider == "OpenCode Go"
    assert resolved_reference.model == "vision-A"
    assert resolved_reference.api_key == "ocr-key"
    assert resolved_reference.credential_owner == resolved_reference.endpoint_id()


def test_status_is_readable_without_any_recognition_request(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    calls = []
    from folith import ocr_online
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_attempt",
                        lambda self, *args, **kwargs: calls.append(args))
    assert core.ocr_online_status()["ok"] is False
    core.save_ocr_online_config(provider="OpenCode Go", model="vision-A",
                                api_key="ocr-key", base_url="")
    status = core.ocr_online_status()
    assert status["ok"] is True and status["state"] == "configured"
    assert "ocr-key" not in json.dumps(status, ensure_ascii=False)
    assert calls == []
