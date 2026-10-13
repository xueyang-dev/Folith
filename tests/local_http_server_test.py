"""Binding local probe sockets must not depend on reverse DNS."""
import socket
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.local_http_server import LocalHTTPServer, LocalThreadingHTTPServer
from scripts import d0_fake_llm_server


@pytest.mark.parametrize("server_type", [LocalHTTPServer, LocalThreadingHTTPServer])
def test_local_server_binds_without_reverse_dns(monkeypatch, server_type):
    def forbidden_lookup(*args):
        raise AssertionError("local binding must not perform reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", forbidden_lookup)
    with server_type(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1] > 0


def test_d0_stub_binds_without_reverse_dns(monkeypatch):
    monkeypatch.setattr(socket, "getfqdn", lambda *args: pytest.fail("reverse DNS"))
    with d0_fake_llm_server.serve(0, None) as server:
        assert server.server_port > 0
