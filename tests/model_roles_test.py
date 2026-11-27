"""CX-21 §2：任务状态里的 URL 副本不带凭据。

自定义连接允许把凭据放进 URL（``user:pass@host`` 或 ``?key=``），因为请求本身
需要它。但 ``public_role_config`` 的产物会写进任务状态，之后随导出、诊断和备份
离开本机。测试钉住「状态副本去掉两类凭据、其余逐字保留」，并钉住请求路径
（``normalize_role_config``）仍然拿到完整 URL。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import model_roles  # noqa: E402


def test_userinfo_is_dropped_from_the_published_url():
    assert model_roles.public_base_url(
        "https://user:p%40ss@relay.example/v1") == "https://relay.example/v1"


def test_a_password_may_contain_the_separator():
    # Everything before the last "@" is userinfo, so a password with "@" is
    # stripped whole instead of leaving a fragment behind.
    assert model_roles.public_base_url(
        "https://user:p@ss@relay.example/v1") == "https://relay.example/v1"


def test_credential_query_parameters_are_removed_and_others_kept():
    published = model_roles.public_base_url(
        "https://relay.example/v1?key=synthetic-key&region=eu&api_key=k2")
    assert published == "https://relay.example/v1?region=eu"


def test_non_credential_query_text_is_preserved_byte_for_byte():
    # A relay can route on a query parameter; re-encoding it would change what
    # the state says the connection was.
    original = "https://relay.example/v1?token=t&route=a%2Fb+c&page=2"
    assert model_roles.public_base_url(original) == \
        "https://relay.example/v1?route=a%2Fb+c&page=2"


def test_urls_without_credentials_are_unchanged():
    for url in ("https://relay.example/v1",
                "http://127.0.0.1:8080/v1/chat",
                "https://relay.example/v1?region=eu#docs"):
        assert model_roles.public_base_url(url) == url, url


def test_empty_and_unparsable_values_do_not_raise():
    assert model_roles.public_base_url("") == ""
    assert model_roles.public_base_url("  ") == ""
    # No scheme: nothing to strip, and the value is returned as given.
    assert model_roles.public_base_url("relay.example/v1?key=x") == \
        "relay.example/v1?key=x"


def test_the_published_role_config_has_no_key_and_no_url_credential():
    published = model_roles.public_role_config({
        "provider": "自定义中转站",
        "model": "stub-model",
        "api_key": "sk-synthetic-role-key",
        "base_url": "https://user:pass@relay.example/v1?key=synthetic-url-key",
    })
    assert published == {
        "provider": "自定义中转站",
        "model": "stub-model",
        "base_url": "https://relay.example/v1",
        "configured": True,
    }
    dump = json.dumps(published, ensure_ascii=False)
    for secret in ("pass", "sk-synthetic-role-key", "synthetic-url-key", "@"):
        assert secret not in dump, secret


def test_the_request_path_keeps_the_full_url():
    # The persisted copy is reduced; what builds a request is not.  Otherwise a
    # relay that authenticates through the URL would stop authenticating.
    raw = {
        "provider": "自定义中转站",
        "model": "stub-model",
        "api_key": "sk-synthetic-role-key",
        "base_url": "https://user:pass@relay.example/v1?key=synthetic-url-key",
    }
    runtime = model_roles.normalize_role_config(raw)
    assert runtime["base_url"] == raw["base_url"]
    assert runtime["api_key"] == raw["api_key"]


def test_role_metadata_publishes_both_roles_without_credentials():
    metadata = model_roles.role_metadata(
        {"provider": "自定义中转站", "model": "translator-model",
         "base_url": "https://user:pass@relay.example/v1"},
        {"provider": "OpenAI", "model": "reviewer-model",
         "base_url": "https://relay.example/v1?api_key=synthetic-url-key"},
    )
    assert metadata["translator"]["base_url"] == "https://relay.example/v1"
    assert metadata["reviewer"]["base_url"] == "https://relay.example/v1"
    dump = json.dumps(metadata, ensure_ascii=False)
    assert "pass" not in dump and "synthetic-url-key" not in dump
