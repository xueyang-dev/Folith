"""#48（core 层）：模型输入类型的两个来源——目录声明与「测试视觉」实测。

钉住的事实：

* ``fetch_provider_models`` 只把**服务商声明过**的输入模态带回来，声明之外一律
  缺席（= 未知），不猜；
* ``test_provider_vision`` 是唯一会发图像的探测入口，判断只依据这一次请求的
  真实反应，函数体里没有任何按模型名写死的清单（acceptance gate：no
  hard-coded model lists）；
* 探测用的图片是一张程序生成的极小 PNG，不依赖任何素材文件。
"""
from __future__ import annotations

import base64
import inspect
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, result):
        self._result = result
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Client:
    def __init__(self, result):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(result)


def _patch_acquire(monkeypatch, result):
    clients = []

    def fake_acquire(cfg, provider, api_key, base_url=None):
        client = _Client(result)
        clients.append((provider, api_key, base_url, client))
        return "https://resolved.example.com/v1", client

    monkeypatch.setattr(core, "_acquire_openai_compat_client", fake_acquire)
    return clients


def test_probe_image_is_a_tiny_valid_png():
    png = core._probe_image_png(16)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (16, 16)
    # 压缩像素解得开，且行数 = 高度（每行一个 filter 字节 + 16 个 RGB 像素）。
    idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 4]
    raw = zlib.decompress(idat)
    assert len(raw) == 16 * (1 + 16 * 3)
    assert b"\xff\x00\x00" in raw, "必须是能被模型认出的纯红色方块"


def test_vision_probe_sends_one_tiny_image_and_passes_on_color(monkeypatch):
    clients = _patch_acquire(monkeypatch, _Response("红色"))
    ok, message = core.test_provider_vision("DeepSeek", "k", "deepseek-v4-flash")
    assert ok, message
    assert "支持图像输入" in message
    (provider, api_key, base_url, client), = clients
    assert (provider, api_key, base_url) == ("DeepSeek", "k", None)
    request, = client.chat.completions.requests
    assert request["model"] == "deepseek-v4-flash"
    content = request["messages"][1]["content"]
    image_part = next(part for part in content if part["type"] == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # 随图带的那句问的是颜色，答案不依赖图像以外的东西。
    text_part = next(part for part in content if part["type"] == "text")
    assert "颜色" in text_part["text"]
    payload = base64.b64decode(url.split(",", 1)[1])
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"


def test_vision_probe_fails_honestly_when_model_ignores_the_image(monkeypatch):
    _patch_acquire(monkeypatch, _Response("我看不到任何图片"))
    ok, message = core.test_provider_vision("DeepSeek", "k", "deepseek-v4-flash")
    assert not ok
    assert "未验证通过" in message and "我看不到任何图片" in message


def test_vision_probe_fails_honestly_on_empty_reply(monkeypatch):
    _patch_acquire(monkeypatch, _Response("  "))
    ok, message = core.test_provider_vision("DeepSeek", "k", "deepseek-v4-flash")
    assert not ok and "空内容" in message


def test_vision_probe_reports_api_errors_without_raw_details(monkeypatch):
    _patch_acquire(monkeypatch, RuntimeError("boom https://secret.example/x"))
    ok, message = core.test_provider_vision("DeepSeek", "k", "deepseek-v4-flash")
    assert not ok
    # 未知错误按既有约定收敛成可读短语，不把原始 SDK 文本（可能带地址）透给用户。
    assert "图像输入测试失败" in message
    assert "secret.example" not in message and "boom" not in message


def test_vision_probe_refuses_non_openai_compat_providers(monkeypatch):
    clients = _patch_acquire(monkeypatch, _Response("红色"))
    ok, message = core.test_provider_vision("Gemini", "k", "gemini-3.6-flash")
    assert not ok and "OpenAI 兼容" in message
    assert clients == [], "拒绝必须发生在发请求之前"


def test_vision_probe_has_no_hard_coded_model_lists():
    """验收红线：判断不能看模型名，只能看这一次请求的反应。"""
    source = inspect.getsource(core.test_provider_vision)
    for banned in ("startswith", "gpt-", "claude", "gemini-", "deepseek-", "glm-",
                   "qwen", "kimi"):
        assert banned not in source, f"test_provider_vision 不应引用模型名清单：{banned}"


def test_fetched_modalities_keep_provider_declarations_verbatim(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [
                {"id": "vision-a", "architecture": {"input_modalities": ["text", "image"],
                                                    "output_modalities": ["text"]}},
                {"id": "plain-b", "input_modalities": ["text"]},
                {"id": "mystery-c"},
                "bare-d",
            ]}

    monkeypatch.setattr(core.httpx, "get", lambda *a, **k: FakeResponse())
    ok, models, _msg, modalities = core.fetch_provider_models(
        "OpenRouter", "k", "https://relay.example.com/v1")
    assert ok
    assert models == ["bare-d", "mystery-c", "plain-b", "vision-a"]
    # 声明过的原样保留；没声明的（mystery-c / bare-d）必须缺席 = 未知。
    assert modalities == {"vision-a": ["image", "text"], "plain-b": ["text"]}
