"""C1 (#220): conservative default vision concurrency and the gateway-timeout note.

The trial put thinking on and lost 4 of the first 5 pages to HTTP 502 at ~110 s
while the client timeout was 180 s: the gateway gives up before the client does,
so piling more page requests behind it makes the failure worse, not better.  The
default vision concurrency therefore depends on the connection kind (official
endpoint vs custom relay / gateway); an explicit value and the environment
variable keep their old meaning.

Existing configuration semantics must not change: this file also locks the
timeout / retry defaults so a future edit cannot quietly move them.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from folith import document_understanding  # noqa: E402
from folith import ocr_online  # noqa: E402
from folith.engine import ocr_config  # noqa: E402


def test_default_concurrency_is_lower_on_a_relay_than_on_an_official_endpoint():
    assert ocr_config.VISION_PAGE_WORKERS_RELAY_DEFAULT \
        < ocr_config.VISION_PAGE_WORKERS_DEFAULT
    # An official provider with its registered endpoint gets the normal default.
    assert ocr_config.vision_connection_is_relay(
        "DeepSeek", "https://api.deepseek.com") is False
    assert ocr_config.vision_page_workers(
        "DeepSeek", "https://api.deepseek.com") \
        == ocr_config.VISION_PAGE_WORKERS_DEFAULT
    # A custom relay, an unknown provider or a moved base_url is a relay.
    assert ocr_config.vision_page_workers(
        "Custom relay", "https://relay.example/v1") \
        == ocr_config.VISION_PAGE_WORKERS_RELAY_DEFAULT
    assert ocr_config.vision_connection_is_relay(
        "DeepSeek", "https://my-gateway.example/v1") is True
    # Trailing slash / case differences are the same endpoint.
    assert ocr_config.vision_connection_is_relay(
        "DeepSeek", "HTTPS://API.DEEPSEEK.COM/") is False


def test_an_explicit_value_and_the_environment_keep_their_meaning(monkeypatch):
    monkeypatch.delenv("FOLIOTHREAD_VLM_PAGE_WORKERS", raising=False)
    # Explicit argument beats the connection default, clamped to 1..8 like before.
    assert ocr_config.vision_page_workers(workers=1) == 1
    assert ocr_config.vision_page_workers(workers=8) == 8
    assert ocr_config.vision_page_workers(workers=40) == 8
    assert ocr_config.vision_page_workers(workers=0) == 1
    # Environment variable beats the default, same clamp.
    monkeypatch.setenv("FOLIOTHREAD_VLM_PAGE_WORKERS", "4")
    assert ocr_config.vision_page_workers("DeepSeek", "https://api.deepseek.com") == 4
    # An unparsable value falls back to the connection default, never crashes.
    monkeypatch.setenv("FOLIOTHREAD_VLM_PAGE_WORKERS", "many")
    assert ocr_config.vision_page_workers(
        "DeepSeek", "https://api.deepseek.com") \
        == ocr_config.VISION_PAGE_WORKERS_DEFAULT
    # Explicit argument still wins over the environment.
    monkeypatch.setenv("FOLIOTHREAD_VLM_PAGE_WORKERS", "6")
    assert ocr_config.vision_page_workers(workers=2) == 2


def test_page_worker_count_uses_the_connection_it_is_given():
    assert document_understanding.page_worker_count(
        provider="DeepSeek", base_url="https://api.deepseek.com") \
        == ocr_config.VISION_PAGE_WORKERS_DEFAULT
    assert document_understanding.page_worker_count(
        provider="Custom relay", base_url="https://relay.example/v1") \
        == ocr_config.VISION_PAGE_WORKERS_RELAY_DEFAULT
    # Without any connection information the cautious default is used.
    assert document_understanding.page_worker_count() \
        == ocr_config.VISION_PAGE_WORKERS_RELAY_DEFAULT
    assert document_understanding.VLM_PAGE_WORKERS_MAX \
        == ocr_config.VISION_PAGE_WORKERS_MAX


def test_the_gateway_timeout_note_names_both_timeouts():
    hint = ocr_config.VISION_GATEWAY_TIMEOUT_HINT
    # The diagnosis is only useful with both numbers: the gateway gives up first.
    assert "502" in hint
    assert "110" in hint
    assert "180" in hint
    assert "并发" in hint


def test_existing_vision_timeout_and_retry_defaults_are_unchanged():
    # C1 changes defaults/notes only; the recorded configuration keeps meaning.
    assert ocr_online.DEFAULT_TIMEOUT_S == 180.0
    assert ocr_online.DEFAULT_MAX_RETRIES == 2
    config = ocr_online.OnlineOCRConfig(provider="DeepSeek",
                                        model="x", api_key="k").normalized()
    assert config.timeout_s == 180.0
    assert config.max_retries == 2
