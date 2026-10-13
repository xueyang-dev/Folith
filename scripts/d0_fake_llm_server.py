#!/usr/bin/env python3
"""D0 controlled model responses: a local OpenAI-compatible endpoint.

Roadmap §6.1 requires the frozen vertical slice to run on *controlled model
responses* first (a real authorized model call is a separate, later step).  This
server speaks the OpenAI `/chat/completions` shape that Folith's "自定义中转站"
(openai_compat) provider uses, so the slice exercises the real transport, worker
and delivery gates rather than a monkeypatched function.

It answers deterministically from the request itself:

* source-cleanup batches  -> ``{"segments": [{"index", "text", "break_after"}]}``
* translation batches     -> JSON array of ``【译】<source>``, one per numbered item
* repair batches          -> JSON array of the original targets (no new changes)
* review batches          -> ``[]`` (no findings)
* anything else           -> ``[]``

Every request/response pair is appended to ``--log`` as JSONL for evidence.

Usage:
    python scripts/d0_fake_llm_server.py --port 0 --log /tmp/d0-llm.jsonl
    # prints {"port": N, "url": "http://127.0.0.1:N/v1"} on stdout
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import re
import sys
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NUMBERED = re.compile(r"^\s*(\d+)[.)、]\s*(.*)$")
NUMBERED_PAIR = re.compile(r"^\s*(\d+)[.)、]\s*原文[:：]\s*(.*)$")
CURRENT_BATCH_MARKER = "【待翻译段落"
CONTEXT_ROW = re.compile(r"^上下文项\s*\d+（只读）")

TRANSLATION_PREFIX = "【译】"


def _numbered_items(text: str) -> list[str]:
    """Numbered source items, ignoring read-only context rows."""
    items: list[str] = []
    for line in text.splitlines():
        if CONTEXT_ROW.match(line.strip()):
            continue
        match = NUMBERED.match(line)
        if match:
            items.append(match.group(2).strip())
    return items


def _current_batch(text: str) -> list[str]:
    """Only the trailing current-batch section of a translation prompt."""
    index = text.rfind(CURRENT_BATCH_MARKER)
    section = text[index:] if index >= 0 else text
    return _numbered_items(section)


def _last_user(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _system_text(messages: list[dict]) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in (messages or []) if message.get("role") == "system"
    )


def _offline_provider():
    """Reuse the repository's canonical deterministic provider (tests/offline_provider.py).

    Keeping one dispatch implementation means the D0 slice exercises the same
    prompt contracts as the existing offline acceptance tests instead of a
    second, drifting fake.
    """
    global _OFFLINE_PROVIDER
    if _OFFLINE_PROVIDER is None:
        root = Path(__file__).resolve().parents[1]
        if str(root / "tests") not in sys.path:
            sys.path.insert(0, str(root / "tests"))
        from offline_provider import OfflineProvider

        _OFFLINE_PROVIDER = OfflineProvider()
    return _OFFLINE_PROVIDER


_OFFLINE_PROVIDER = None


def build_content(body: dict) -> tuple[str, str]:
    """Return ``(content, kind)`` for one chat-completions request."""
    messages = body.get("messages") or []
    user = _last_user(messages)
    system = _system_text(messages)
    schema = json.dumps(body.get("response_format") or {}, ensure_ascii=False)

    # 1. Source cleanup (OCR repair) contract: {"segments": [...]}.
    if "segments" in schema or "纠错" in system or "原文纠错" in user:
        items = _numbered_items(user)
        if items:
            rows = [
                {"index": index, "text": text, "break_after": False}
                for index, text in enumerate(items)
            ]
            return json.dumps({"segments": rows}, ensure_ascii=False), "cleanup"

    # 2. Quick style profile: a valid JSON object with a known style id, so the
    #    "智能画像" step completes instead of degrading to the generic fallback.
    if "快速画像与风格推荐" in system:
        try:
            from folith.style_profile import STYLE_PROFILES

            style = "academic" if "academic" in STYLE_PROFILES else sorted(STYLE_PROFILES)[0]
        except Exception:  # pragma: no cover - defensive
            style = "general"
        return json.dumps({
            "document_profile": {
                "domain": "生态学",
                "subdomain": "恢复生态学",
                "genre": "学术专著",
                "audience": "研究人员",
                "register": "正式书面语",
                "style_constraints": "保持术语一致，不使用口语化表达。",
                "confidence": 0.8,
                "sections": [],
            },
            "style_recommendation": {
                "document_type": "academic_monograph",
                "domain": ["生态学", "恢复生态学"],
                "register": "正式书面语",
                "target_audience": "研究人员",
                "recommended_style": style,
                "confidence": 0.8,
                "reasons": ["术语密集", "长句比例较高", "面向专业读者"],
                "recommended_rules": {},
            },
        }, ensure_ascii=False), "style_profile"

    # 3. Every other recognized pipeline prompt uses the canonical provider.
    try:
        return _offline_provider()("自定义中转站", "d0-local", "d0-stub-model",
                                   system, user), "offline"
    except AssertionError:
        pass

    # 4. Connectivity probe and anything unrecognized.
    if "OK" in user and len(user) < 40:
        return "OK", "probe"
    return "[]", "unrecognized"


class Handler(BaseHTTPRequestHandler):
    server_version = "FolithD0FakeLLM/1.0"

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {
                "object": "list",
                "data": [{"id": "d0-fake-model", "object": "model", "owned_by": "d0"}],
            })
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send(400, {"error": {"message": "invalid json"}})
            return
        try:
            delay = float(getattr(self.server, "delay_s", 0.0) or 0.0)
            if delay:
                time.sleep(delay)
            content, kind = build_content(body)
        except Exception as exc:  # fail loudly: an unrecognized prompt must not pass
            import traceback

            traceback.print_exc()
            self._record(body, f"ERROR: {exc}", "error")
            self._send(500, {"error": {"message": f"d0 stub could not answer: {exc}"}})
            return
        self._record(body, content, kind)
        self._send(200, {
            "id": f"chatcmpl-d0-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "d0-fake-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _record(self, body: dict, content: str, kind: str) -> None:
        path = getattr(self.server, "log_path", None)
        if not path:
            return
        entry = {
            "at": time.time(),
            "kind": kind,
            "model": body.get("model"),
            "response_format": body.get("response_format"),
            "roles": [m.get("role") for m in (body.get("messages") or [])],
            "system_prompt_excerpt": _system_text(body.get("messages") or [])[:400],
            "user_prompt_excerpt": _last_user(body.get("messages") or [])[:400],
            "content_excerpt": content[:400],
            "content_chars": len(content),
        }
        with getattr(self.server, "log_lock"):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_message(self, *args):  # noqa: D102
        return


# `socketserver` defaults to a listen backlog of 5.  The app under test issues
# genuinely parallel requests (translation concurrency plus review/knowledge
# workers), and a burst beyond the backlog makes the kernel refuse connections —
# which the client can only report as "Connection error", turning a harness
# limitation into a product-looking failure (observed intermittently before this
# was raised).
LISTEN_BACKLOG = 256


def serve(port: int, log_path: str | None, delay: float = 0.0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.request_queue_size = LISTEN_BACKLOG
    server.daemon_threads = True
    server.log_path = log_path
    server.delay_s = float(delay or 0.0)
    server.log_lock = threading.Lock()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D0 controlled model endpoint")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", default=None)
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds to wait before answering (keeps a job running)")
    parser.add_argument("--ready-file", default=None,
                        help="write {\"port\": N, \"url\": ...} here once listening")
    args = parser.parse_args(argv)

    # The probe waits 30 seconds for readiness. Preserve a startup stack in its
    # server log if binding stalls, before the probe terminates this process.
    faulthandler.dump_traceback_later(20)
    try:
        server = serve(args.port, args.log, args.delay)
    finally:
        faulthandler.cancel_dump_traceback_later()
    port = server.server_address[1]
    info = {"port": port, "url": f"http://127.0.0.1:{port}/v1"}
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
    print(json.dumps(info), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
