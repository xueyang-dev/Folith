"""Local model endpoints must not be routed through a system proxy.

With Clash (or any HTTP proxy) configured, requests to a model on 127.0.0.1 /
the LAN were sent to the proxy and came back as 502.  Local endpoints bypass the
proxy; remote ones keep using it (many users need it to reach OpenAI, Gemini…).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import llm_transport  # noqa: E402


@pytest.mark.parametrize("url, local", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:1234/v1", True),
    ("http://[::1]:8000/v1", True),
    ("http://192.168.1.20:8080/v1", True),
    ("http://10.0.0.5/v1", True),
    ("http://172.20.3.4/v1", True),
    ("http://mac-studio.local:8080/v1", True),
    ("http://relay.localhost/v1", True),
    ("127.0.0.1:9000/v1", True),
    ("https://api.openai.com/v1", False),
    ("https://api.deepseek.com", False),
    ("http://8.8.8.8/v1", False),
    ("", False),
    (None, False),
])
def test_local_endpoint_detection(url, local):
    assert llm_transport.is_local_endpoint(url) is local


def test_remote_endpoints_keep_the_provider_flag():
    assert llm_transport.proxy_bypass_for(True, "https://opencode.ai/zen/go/v1") is True
    assert llm_transport.proxy_bypass_for(False, "https://api.openai.com/v1") is False


@pytest.fixture()
def stub_url(tmp_path):
    ready = tmp_path / "ready.json"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"), "--port", "0",
         "--ready-file", str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        yield json.loads(ready.read_text(encoding="utf-8"))["url"]
    finally:
        process.terminate()
        process.wait(timeout=30)


def test_a_local_relay_works_while_a_proxy_is_configured(stub_url, monkeypatch):
    # A proxy that cannot answer: without the bypass every request would fail.
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    llm_transport.close_all_clients()
    try:
        ok, message = core.test_provider("自定义中转站", "k", "stub-model", base_url=stub_url)
    finally:
        llm_transport.close_all_clients()
    assert ok, message
