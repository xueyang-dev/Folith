"""提供商路由 / 中转站 / 多格式导入 / 模式语义 / 自定义标注颜色测试。"""
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core


def test_provider_registry_sane():
    for name, cfg in core.PROVIDERS.items():
        assert cfg["kind"] in ("openai", "openai_compat", "gemini"), name
        if cfg.get("custom_base_url"):
            assert cfg["base_url"] is None and name == "自定义中转站"
        if name == "OpenCode Go":
            assert cfg["models"] == [
                "glm-5.2", "glm-5.1", "deepseek-v4.1-flash", "deepseek-v4-pro", "deepseek-v4-flash",
                "kimi-k3", "kimi-k2.7-code", "kimi-k2.6",
                "mimo-v2.5-pro", "mimo-v2.5", "hy3", "grok-4.5",
            ], "OpenCode Go 只能列出 /chat/completions 模型"
        if name == "DeepSeek":
            assert cfg["models"] == ["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"]
        if name == "Gemini":
            assert "gemini-2.0-flash" not in cfg["models"]
    print("  ✓ PROVIDERS 注册表（openai_compat / 模型目录完整）")


def test_custom_relay_base_url():
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = type("Message", (), {"content": "OK"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    original = core.OpenAI
    core.OpenAI = FakeOpenAI
    try:
        core.set_llm_base_url("https://relay.example.com/v1")
        out = core.call_llm("自定义中转站", "k", "relay-model", "s", "u")
        assert out == "OK"
        client_kwargs = next(k for tag, k in calls if tag == "client")
        assert client_kwargs["base_url"] == "https://relay.example.com/v1"
        create_kwargs = next(k for k in calls if not isinstance(k, tuple))
        assert create_kwargs["model"] == "relay-model"

        calls.clear()
        core.call_llm("自定义中转站", "k", "relay-model", "s", "u",
                      base_url="https://relay.example.com/v1/chat/completions")
        # 归一化后与上面是同一个地址 → 必须复用已建立的客户端（这正是连接复用的
        # 意义所在）。契约不是"每次都新建客户端"，而是"生效的 base_url 正确"。
        assert not [entry for entry in calls
                    if isinstance(entry, tuple) and entry[0] == "client"], \
            "归一化后相同的 base_url 必须复用客户端，而不是每请求重建"
        create_kwargs = next(k for k in calls if not isinstance(k, tuple))
        assert create_kwargs["model"] == "relay-model"

        # 换一个真正不同的中转地址 → 必须新建客户端，且不得串用上一个地址的客户端。
        calls.clear()
        core.call_llm("自定义中转站", "k", "relay-model", "s", "u",
                      base_url="https://other-relay.example.com/v1/chat/completions")
        client_kwargs = next(k for tag, k in calls if tag == "client")
        assert client_kwargs["base_url"] == "https://other-relay.example.com/v1"

        # 预设中转站使用注册表默认 base_url
        calls.clear()
        core.call_llm("OpenRouter", "k", "anthropic/claude-sonnet-4", "s", "u")
        client_kwargs = next(k for tag, k in calls if tag == "client")
        assert client_kwargs["base_url"] == "https://openrouter.ai/api/v1"

        # 清除线程级地址后，自定义中转站必须失败闭合，不能静默回退到
        # OpenAI SDK 默认地址（否则中转站密钥会产生误导性的 401）。
        core.set_llm_base_url(None)
        calls.clear()
        try:
            core.call_llm("自定义中转站", "k", "m", "s", "u")
        except ValueError as exc:
            assert "API 地址" in str(exc)
        else:
            raise AssertionError("custom relay without base URL must fail closed")

        core.set_llm_base_url("https://relay.example.com/v1")
        core.set_llm_reasoning_effort("medium")
        calls.clear()
        core.call_llm("自定义中转站", "k", "deepseek-r1", "s", "u")
        create_kwargs = next(k for k in calls if not isinstance(k, tuple))
        assert create_kwargs["reasoning_effort"] == "medium"
        calls.clear()
        core.call_llm("自定义中转站", "k", "relay-model", "s", "u")
        create_kwargs = next(k for k in calls if not isinstance(k, tuple))
        assert "reasoning_effort" not in create_kwargs
        core.set_llm_reasoning_effort(None)
    finally:
        core.OpenAI = original
        core.set_llm_base_url(None)
        core.set_llm_reasoning_effort(None)
        # 上面建立的客户端来自测试替身，必须丢弃，避免同一进程内的后续用例
        # 复用到一个由替身构造的客户端。
        core.reset_llm_clients()
    print("  ✓ 自定义中转站 base_url（线程级上下文 + 预设中转站）")


def test_reasoning_effort_capability():
    assert core.reasoning_effort_options("OpenAI", "gpt-5.2") == (
        "low", "medium", "high")
    assert core.reasoning_effort_options("OpenAI", "gpt-4.1-mini") == ()
    assert core.reasoning_effort_options("自定义中转站", "deepseek-v4.1-flash") == ()
    assert core.reasoning_effort_options("自定义中转站", "deepseek-r1") == (
        "low", "medium", "high")
    print("  ✓ 推理强度只对已识别的推理模型开放")


def test_provider_config_roundtrip():
    tmp = Path(tempfile.mkdtemp(prefix="provider-config-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        core.save_provider_config(
            "自定义中转站", "deepseek-r1", "relay-key",
            "https://relay.example.com/v1/chat/completions",
            reasoning_effort="medium")
        saved = core.load_provider_config()
        assert saved["base_url"] == "https://relay.example.com/v1"
        assert saved["reasoning_effort"] == "medium"
        if sys.platform != "win32":
            assert (core.provider_config_path().stat().st_mode & 0o077) == 0

        core.save_provider_config(
            "自定义中转站", "relay-model", "relay-key",
            "https://relay.example.com/v1", reasoning_effort="unsupported")
        assert core.load_provider_config()["reasoning_effort"] == ""
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ✓ Provider 配置规范化 / 原子保存 / 旧值容错")


def test_provider_probe():
    class FakeCompletions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": "OK"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    original = core.OpenAI
    core.OpenAI = FakeOpenAI
    try:
        ok, msg = core.test_provider("DeepSeek", "k", "deepseek-v4-flash")
        assert ok and "OK" in msg
    finally:
        core.OpenAI = original
        core.reset_llm_clients()

    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("401 Unauthorized")

    core.OpenAI = Boom
    try:
        ok, msg = core.test_provider("DeepSeek", "bad", "deepseek-v4-flash")
        assert not ok and "401" in msg
    finally:
        core.OpenAI = original
        core.reset_llm_clients()
    print("  ✓ test_provider（成功 / 失败路径）")


def test_provider_error_status_is_user_readable():
    request = httpx.Request("GET", "https://relay.example.com/v1/models")
    response = httpx.Response(
        403, request=request,
        json={"code": "INSUFFICIENT_BALANCE", "message": "余额不足"})
    error = httpx.HTTPStatusError(
        "403", request=request, response=response)

    status = core.provider_error_status(error)
    assert status["status"] == "insufficient_balance"
    assert status["label"] == "余额不足"
    assert "余额不足" in status["message"]
    assert status["http_status"] == 403
    assert "INSUFFICIENT_BALANCE" == status["provider_code"]
    assert "INSUFFICIENT_BALANCE" not in core.provider_error_message(error)

    sdk_error = APIStatusError(
        "Error code: 403", response=response,
        body={"code": "INSUFFICIENT_BALANCE", "message": "余额不足"})
    assert core.provider_error_status(sdk_error)["status"] == \
        "insufficient_balance"

    class RelayError(Exception):
        status_code = 403
        body = {"error": {"code": "INSUFFICIENT_BALANCE", "message": "余额不足"}}

    assert core.provider_error_status(RelayError())["status"] == \
        "insufficient_balance"
    print("  ✓ Provider 错误归一化（余额不足 / 不泄露原始错误体）")


def test_fetch_provider_models():
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "model-z"}, {"id": "model-a"},
                              {"id": "model-a"}]}

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return FakeResponse()

    original_get = core.httpx.get
    core.httpx.get = fake_get
    try:
        ok, models, msg, modalities = core.fetch_provider_models(
            "自定义中转站", "k", "https://relay.example.com/v1/chat/completions")
        assert ok and models == ["model-a", "model-z"] and "2" in msg
        assert modalities == {}, "目录没有声明输入模态时必须为空（= 未知），不能编造"
        assert calls[0][0] == "https://relay.example.com/v1/models"
        assert calls[0][1]["Authorization"] == "Bearer k"
    finally:
        core.httpx.get = original_get

    request = httpx.Request("GET", "https://relay.example.com/v1/models")
    response = httpx.Response(
        403, request=request,
        json={"code": "INSUFFICIENT_BALANCE", "message": "余额不足"})
    error = httpx.HTTPStatusError(
        "403", request=request, response=response)

    def fake_insufficient_balance(url, headers, timeout):
        raise error

    core.httpx.get = fake_insufficient_balance
    try:
        ok, models, msg, modalities = core.fetch_provider_models(
            "自定义中转站", "k", "https://relay.example.com/v1")
        assert not ok and models == []
        assert "余额不足" in msg
        assert "INSUFFICIENT_BALANCE" not in msg
        assert modalities == {}
    finally:
        core.httpx.get = original_get
    print("  ✓ 自定义中转站模型目录获取")


def test_exchange_formats():
    tmp = Path(tempfile.mkdtemp(prefix="folith-exchange-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        # CSV
        csv = io.BytesIO("Source,Target,Behavior,Status,Preferred\n"
                         "MT,machine translation,translate,locked,机器翻译\n".encode())
        entries = core.parse_termbase_csv(csv)
        assert len(entries) == 1 and entries[0]["preferred"] == "机器翻译"
        assert entries[0]["status"] == "locked"

        # TBX（带命名空间，Trados MultiTerm 风格）
        tbx = io.BytesIO('''<?xml version="1.0"?>
<martif type="TBX" xml:lang="en">
  <text><body>
    <termEntry id="1">
      <langSet xml:lang="en"><tig><term>Skopos theory</term></tig></langSet>
      <langSet xml:lang="zh-CN"><tig><term>目的论</term></tig></langSet>
    </termEntry>
    <termEntry id="2">
      <langSet xml:lang="en"><tig><term>fidelity</term></tig></langSet>
      <langSet xml:lang="zh-CN"><tig><term>忠实性</term></tig></langSet>
    </termEntry>
  </body></text>
</martif>'''.encode("utf-8"))
        entries = core.parse_termbase_tbx(tbx)
        assert len(entries) == 2
        assert entries[0] == {"source": "Skopos theory", "target": "目的论",
                              "behavior": "translate", "status": "locked"}

        # TMX：入库 + 与现有记忆冲突时跳过（记忆条目按目标语言作用域）
        core.save_tm({core.tm_scope_key("简体中文", "existing"):
                      core.tm_record("已有译文", "简体中文")})
        tmx = io.BytesIO('''<?xml version="1.0"?>
<tmx version="1.4">
  <body>
    <tu><tuv xml:lang="en"><seg>Hello world</seg></tuv><tuv xml:lang="zh-CN"><seg>你好世界</seg></tuv></tu>
    <tu><tuv xml:lang="en"><seg>existing</seg></tuv><tuv xml:lang="zh-CN"><seg>冲突跳过</seg></tuv></tu>
  </body>
</tmx>'''.encode("utf-8"))
        result = core.import_tmx(tmx, target_lang="简体中文")
        assert result == {"added": 1, "skipped": 1}
        tm = core.load_tm()
        assert core.tm_lookup(tm, "Hello world", target_lang="简体中文")[0]["target"] \
            == "你好世界"
        assert core.tm_lookup(tm, "existing", target_lang="简体中文")[0]["target"] \
            == "已有译文", "冲突源文不得覆盖项目内记忆"
        # 不同目标语言下同一个源文是另一条记忆，不会被这次导入覆盖
        assert core.tm_lookup(tm, "Hello world", target_lang="Français")[0] is None

        # 语言无法确定的 TMX 不得入库（宁可不导入，不可猜语言）
        unlabelled = io.BytesIO('''<?xml version="1.0"?>
<tmx version="1.4">
  <body>
    <tu><tuv><seg>No language declared</seg></tuv><tuv><seg>没有语言标注</seg></tuv></tu>
  </body>
</tmx>'''.encode("utf-8"))
        try:
            core.import_tmx(unlabelled)
        except ValueError:
            pass
        else:
            raise AssertionError("语言无法确定的 TMX 必须被拒绝，而不是猜一种语言")
        assert core.tm_lookup(core.load_tm(), "No language declared",
                              target_lang="简体中文")[0] is None

        hostile_xml = b'''<?xml version="1.0"?>
<!DOCTYPE data [<!ENTITY injected "entity text">]>
<data>&injected;</data>'''
        for parser in (core.parse_termbase_tbx, core.import_tmx):
            try:
                parser(io.BytesIO(hostile_xml))
            except ValueError:
                pass
            else:
                raise AssertionError("TBX/TMX 导入必须拒绝 XML 实体声明")
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ✓ CSV / TBX / TMX 导入（含冲突跳过）")


def _numbered(user_prompt):
    import re
    segs = [(int(m.group(1)), m.group(2).strip())
            for m in re.finditer(r'^\s*(\d+)\.\s+(.+?)\s*$', user_prompt, re.M)]
    segs.sort(key=lambda x: x[0])
    return segs


def _make_docx(texts):
    from docx import Document
    doc = Document()
    for t in texts:
        doc.add_paragraph(t)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_mode_semantics():
    tmp = Path(tempfile.mkdtemp(prefix="folith-mode-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    prompts = []
    original_llm = core.call_llm
    original_report = core.generate_mti_report
    try:
        def llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
            prompts.append(system_prompt)
            if "文档画像" in system_prompt:
                # 画像要的是对象而不是译文列表；此前这个 mock 对画像也回译文列表，
                # 画像一直失败，只是旧代码把失败也标成 profile_done=True 才没露馅（#49）。
                return json.dumps({"domain": "翻译学", "genre": "学术论述",
                                   "confidence": 0.9, "sections": []})
            return json.dumps([f"译文：{s}" for _, s in _numbered(user_prompt)])

        core.call_llm = llm
        docx_bytes = _make_docx(["The Skopos theory is important.", "Fidelity matters."])

        # 标准运行时把全文理解与严格术语治理解耦：即使不启用冻结门禁，
        # 也应生成画像和语义理解产物。
        jid = "qm000000000000001"
        state = core.run_job_pipeline(
            jid, "q.docx", docx_bytes, provider="DeepSeek", api_key="k",
            model="deepseek-chat", target_lang="简体中文", auto_term=False,
            enable_report=False, enable_review=False, enable_annotate=False,
            translation_theory="目的论 (Skopos Theory)", user_glossary=[],
            strict_terminology_governance=False)
        assert state["p2_done"] and state["profile_done"], \
            "Standard runtime 不应因关闭严格术语治理而跳过文档画像"
        assert state["understanding_done"]

        # 严格术语治理 + 导入锁定术语 + 无自动候选：无需冻结直接翻译
        jid2 = "qm000000000000002"
        user_glossary = [{"source": "Skopos theory", "target": "目的论",
                          "status": "locked", "behavior": "translate"}]
        state2 = core.run_job_pipeline(
            jid2, "q.docx", docx_bytes, provider="DeepSeek", api_key="k",
            model="deepseek-chat", target_lang="简体中文", auto_term=False,
            enable_report=False, enable_review=False, enable_annotate=False,
            translation_theory="目的论 (Skopos Theory)", user_glossary=user_glossary,
            strict_terminology_governance=True)
        assert state2["p2_done"], "导入锁定术语后不应被冻结门禁卡住"
        assert state2["profile_done"], "严格术语治理应执行文档画像"

        # 实践报告属于输出配置，不能隐式开启画像或术语冻结门禁。
        core.generate_mti_report = lambda *args, **kwargs: "# 测试实践报告"
        jid3 = "qm000000000000003"
        state3 = core.run_job_pipeline(
            jid3, "q.docx", docx_bytes, provider="DeepSeek", api_key="k",
            model="deepseek-chat", target_lang="简体中文", auto_term=False,
            enable_report=True, enable_review=False, enable_annotate=False,
            translation_theory="自动推荐", user_glossary=[],
            strict_terminology_governance=False)
        assert state3["p2_done"] and state3["p3_done"], \
            "开启实践报告后应完成翻译与报告"
        assert state3["profile_done"] and state3["understanding_done"] \
            and not state3["quality_mode"], \
            "实践报告不应控制文档理解或严格术语治理"
    finally:
        core.call_llm = original_llm
        core.generate_mti_report = original_report
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ✓ 术语治理语义（与实践报告解耦；仅有锁定术语时免冻结）")


def test_translation_memory_switch():
    tmp = Path(tempfile.mkdtemp(prefix="folith-tm-switch-"))
    old_dir = core.OUTPUT_DIR
    old_llm = core.call_llm
    core.OUTPUT_DIR = tmp
    try:
        source = "Translation memory should be optional."
        core.save_tm({source: {"target": "不应复用的旧译文", "reviewed": True}})

        def llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
            return json.dumps(["本次新译文"])

        core.call_llm = llm
        state = core.new_job_state("tm-off.docx")
        state.update(p1_done=True, paras=[source])
        core.translate_stage(
            state, "tmoff000000000001", [], "DeepSeek", "k", "deepseek-chat",
            "简体中文", "", enable_review=False, use_tm=False)
        assert state["pairs"][0]["target"] == "本次新译文"
        assert not state["pairs"][0]["from_tm"], "关闭翻译记忆后不应复用历史译文"
    finally:
        core.call_llm = old_llm
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ✓ 翻译记忆开关（关闭后不读取历史译文）")


def test_custom_annotation_colors():
    pairs = [{"source": "Skopos theory", "target": "目的论"}]
    annotations = {0: [{"type": "rare", "src_span": [0, 5], "tgt_span": [0, 1]},
                       {"type": "domain", "src_span": [6, 12], "tgt_span": [1, 2]},
                       {"type": "hard", "src_span": [12, 13], "tgt_span": [2, 3]}]}
    buf = core.pairs_to_word(pairs, annotations=annotations,
                             colors={"rare": "FF0000", "domain": "00FF00",
                                     "hard": "0000FF"})
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    for color in ("FF0000", "00FF00", "0000FF"):
        assert f'w:val="{color}"' in xml, f"缺少自定义颜色 {color}"
    assert "FF0000" in xml and "图例" in xml
    print("  ✓ 自定义标注颜色（docx 渲染 + 图例）")


def main():
    test_provider_registry_sane()
    test_custom_relay_base_url()
    test_reasoning_effort_capability()
    test_provider_config_roundtrip()
    test_provider_probe()
    test_provider_error_status_is_user_readable()
    test_fetch_provider_models()
    test_exchange_formats()
    test_mode_semantics()
    test_translation_memory_switch()
    test_custom_annotation_colors()
    print("提供商 / 交换格式 / 模式 / 颜色测试通过 ✅")


if __name__ == "__main__":
    main()


def test_go_catalog_is_public_and_uses_matching_transport(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'data': [{'id': 'deepseek-v4.1-flash'}, {'id': 'glm-5.3'},
                     {'id': 'gpt-6-luna'}, {'id': 'qwen3.8-max'}]})

    monkeypatch.setattr(core.httpx, 'get', get)
    ok, models, _msg, modalities = core.fetch_provider_models('OpenCode Go', '')
    assert ok and models == ['deepseek-v4.1-flash', 'glm-5.3']
    assert calls[0][0] == 'https://opencode.ai/zen/go/v1/models'
    assert calls[0][1]['trust_env'] is False
    assert 'Authorization' not in calls[0][1]['headers']
    assert modalities == {}, "Go 目录只有 ID，没有模态声明"


def test_gemini_native_model_catalog_paginates_and_filters(monkeypatch):
    calls = []
    def get(url, headers, params, timeout):
        calls.append((url, headers, dict(params)))
        rows = ([{'name': 'models/gemini-new', 'supportedGenerationMethods': ['generateContent']},
                 {'name': 'models/embedding-new', 'supportedGenerationMethods': ['embedContent']}]
                if len(calls) == 1 else
                [{'name': 'models/gemma-new', 'supportedGenerationMethods': ['generateContent']},
                 {'name': 'models/gemini-new-tts', 'supportedGenerationMethods': ['generateContent']}])
        payload = {'models': rows}
        if len(calls) == 1: payload['nextPageToken'] = 'page-two'
        return httpx.Response(200, request=httpx.Request('GET', url), json=payload)
    monkeypatch.setattr(core.httpx, 'get', get)
    ok, models, _msg, modalities = core.fetch_provider_models('Gemini', 'gemini-key')
    assert ok and models == ['gemini-new', 'gemma-new']
    assert calls[0][0] == 'https://generativelanguage.googleapis.com/v1beta/models'
    assert calls[0][1]['x-goog-api-key'] == 'gemini-key'
    assert calls[1][2]['pageToken'] == 'page-two'
    assert modalities == {}


def test_catalog_protocol_filters_and_provider_routes(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        rows = [{'id': 'gpt-4.1'}, {'id': 'o3'}, {'id': 'o3-pro'},
                {'id': 'ft:gpt-4.1:org:custom:123'},
                {'id': 'text-embedding-3-small'}, {'id': 'gpt-4o-realtime-preview'},
                {'id': 'gpt-5.3-codex'}, {'id': 'gpt-image-1'}]
        if 'openrouter' in url:
            rows = [{'id': 'text-model', 'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']}},
                    {'id': 'image-model', 'architecture': {'input_modalities': ['text'], 'output_modalities': ['image']}},
                    {'id': 'vision-model', 'input_modalities': ['text', 'image'],
                     'output_modalities': ['text']}]
        return httpx.Response(200, request=httpx.Request('GET', url), json={'data': rows})
    monkeypatch.setattr(core.httpx, 'get', get)
    assert core.fetch_provider_models('OpenAI', 'openai-key')[1] == ['ft:gpt-4.1:org:custom:123', 'gpt-4.1', 'o3']
    assert calls[-1][0] == 'https://api.openai.com/v1/models'
    _ok, router_models, _msg, router_modalities = core.fetch_provider_models('OpenRouter', '')
    assert router_models == ['text-model', 'vision-model']
    # #48：目录声明原样保留——仅文本和文本+图像都是「已知」，其余才是未知。
    assert router_modalities == {'text-model': ['text'],
                                 'vision-model': ['image', 'text']}
    assert 'Authorization' not in calls[-1][1]['headers']
    core.fetch_provider_models('SiliconFlow', 'sf-key')
    assert calls[-1][0] == 'https://api.siliconflow.cn/v1/models?sub_type=chat'


def test_preset_endpoint_overrides_do_not_fall_back_silently():
    assert core.resolve_openai_base_url('Moonshot (Kimi)', 'https://api.moonshot.ai/v1') == 'https://api.moonshot.ai/v1'
    assert core.resolve_openai_base_url('DeepSeek', '') == 'https://api.deepseek.com'
    try:
        core.resolve_openai_base_url('Moonshot (Kimi)', 'not-an-api-url')
    except ValueError:
        pass
    else:
        raise AssertionError('An invalid preset override must not send its key to OpenAI')


def test_official_reasoning_auto_mode_omits_temperature(monkeypatch):
    calls = []
    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type('Response', (), {'choices': [type('Choice', (), {'message': type('Message', (), {'content': 'OK'})()})()]})()
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = type('Chat', (), {'completions': FakeCompletions()})()
    monkeypatch.setattr(core, 'OpenAI', FakeOpenAI)
    core.reset_llm_clients()
    try:
        for model in ('o3', 'o4-mini', 'gpt-5-mini'):
            core.call_llm('OpenAI', 'key', model, 'system', 'user', reasoning_effort='')
            assert 'temperature' not in calls[-1]
        core.call_llm('OpenAI', 'key', 'gpt-4.1', 'system', 'user', temperature=0.2)
        assert calls[-1]['temperature'] == 0.2
        assert core.reasoning_effort_options('OpenAI', 'gpt-5-chat-latest') == ()
    finally:
        core.reset_llm_clients()


def _tmx(body: str) -> io.BytesIO:
    return io.BytesIO(f'''<?xml version="1.0"?>
<tmx version="1.4"><body>{body}</body></tmx>'''.encode("utf-8"))


def test_tmx_inline_tags_do_not_truncate_the_segment(tmp_path, monkeypatch):
    """`seg.text` 只到第一个行内标签为止；截断的半句不得作为记忆入库。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    result = core.import_tmx(_tmx(
        '<tu><tuv xml:lang="en"><seg>The <hi>meeting</hi> starts tomorrow.</seg></tuv>'
        '<tuv xml:lang="zh-CN"><seg><hi>会议</hi>明天开始。</seg></tuv></tu>'
        '<tu><tuv xml:lang="en"><seg>Press <bpt i="1">&lt;b&gt;</bpt>Save'
        '<ept i="1">&lt;/b&gt;</ept> now.</seg></tuv>'
        '<tuv xml:lang="zh-CN"><seg>现在按<bpt i="1">&lt;b&gt;</bpt>保存'
        '<ept i="1">&lt;/b&gt;</ept>。</seg></tuv></tu>'), target_lang="简体中文")

    assert result == {"added": 2, "skipped": 0}
    tm = core.load_tm()
    assert core.tm_lookup(tm, "The meeting starts tomorrow.",
                          target_lang="简体中文")[0]["target"] == "会议明天开始。"
    assert core.tm_lookup(tm, "Press Save now.",
                          target_lang="简体中文")[0]["target"] == "现在按保存。", \
        "行内代码是原格式标记，不是译文"
    assert core.tm_lookup(tm, "The", target_lang="简体中文")[0] is None


def test_tmx_unit_with_unknown_inline_content_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    result = core.import_tmx(_tmx(
        '<tu><tuv xml:lang="en"><seg>Kept as is</seg></tuv>'
        '<tuv xml:lang="zh-CN"><seg>保持原样</seg></tuv></tu>'
        '<tu><tuv xml:lang="en"><seg>Odd <foo>inline</foo> unit</seg></tuv>'
        '<tuv xml:lang="zh-CN"><seg>奇怪的单元</seg></tuv></tu>'), target_lang="简体中文")

    assert result == {"added": 1, "skipped": 1}
    assert core.tm_lookup(core.load_tm(), "Odd", target_lang="简体中文")[0] is None
