"""P7：快速 / 标准 / 精细 的梯度真实生效，并在准备页给出针对这份文档的预估。

docs/qt-beta/acceptance-feedback-2026-10-14.md §13（13.5 B、13.6-2、13.6-3）。

* 准备页保存的模式必须**原样**到达流水线：并发、批次和每批知识反馈此前跟的是全局
  设置（与 P0 修过的审校 / 术语识别是同一类缺陷），快速档因此一点都不快；
* 快速档真的不做每批知识反馈（真实流水线 + 本地 stub 模型）；
* 预估与基准实测一致，三档的差距在卡片上看得见。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.application import task_setup  # noqa: E402
from folith.qt import adapter  # noqa: E402

PRESETS = tuple(task_setup.STRATEGY_PRESETS)
# Generated, varied English paragraphs (the calibration workload itself is not
# tracked in git; its size is recorded in MEASURED below).
_SUBJECTS = ("The committee", "Regional planners", "The ministry", "Local firms",
             "Researchers at the institute", "The central bank", "Most households")
_VERBS = ("reviewed", "questioned", "expanded", "postponed", "funded", "measured")
_OBJECTS = ("the energy transition plan", "public transport subsidies",
            "the skills programme", "water infrastructure", "digital services",
            "the housing strategy")
WORKLOAD = [
    f"{_SUBJECTS[i % 7]} {_VERBS[i % 6]} {_OBJECTS[(i * 5) % 6]} in {2010 + i % 14}. "
    f"Paragraph {i + 1} notes that results varied by region and by sector."
    for i in range(120)]


def _docx(texts) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data))
    monkeypatch.setattr(core, "OUTPUT_DIR", data, raising=False)
    return data


def _prepared_job(tmp_path, config, preset: str, texts=WORKLOAD[:40]) -> str:
    source = tmp_path / f"{preset}.docx"
    source.write_bytes(_docx(texts))
    job_id = adapter.import_documents([source], config)[0].job_id
    setup = dict(adapter.task_setup_view(config, job_id))
    setup["strategy"] = dict(task_setup.STRATEGY_PRESETS[preset])
    setup["strategy_preset"] = preset
    setup["delivery"] = dict(task_setup.DELIVERY_PRESET_CONFIGS["standard"])
    adapter.apply_task_setup(config, job_id, setup)
    return job_id


# ------------------------------------------------------------ 模式到达流水线 --

@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("globals_on", [True, False], ids=["global-on", "global-off"])
def test_the_saved_mode_reaches_the_pipeline_whatever_the_global_defaults(
        tmp_path, data_dir, preset, globals_on):
    config = adapter.EngineConfig(
        provider="自定义中转站", model="m", api_key="k", base_url="http://127.0.0.1:9/v1",
        target_lang="简体中文", enable_review=globals_on, auto_term=globals_on,
        use_tm=globals_on, enable_understanding=globals_on,
        translation_concurrency=8 if globals_on else 1)
    job_id = _prepared_job(tmp_path, config, preset)
    kwargs = adapter.pipeline_kwargs(config, adapter.load_state(job_id))
    expected = task_setup.STRATEGY_PRESETS[preset]
    batch_size, max_chars = task_setup.batch_params(expected)
    for key in ("auto_term", "use_tm", "enable_review", "strict_terminology_governance",
                "translation_concurrency", "knowledge_feedback"):
        assert kwargs[key] == expected[key], (preset, key)
    assert (kwargs["batch_size"], kwargs["max_batch_chars"]) == (batch_size, max_chars)


def test_the_panel_reopens_on_the_saved_mode(tmp_path, data_dir):
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _prepared_job(tmp_path, config, "快速")
    view = adapter.task_setup_view(config, job_id)
    assert view["strategy_preset"] == "快速"
    assert view["strategy"]["knowledge_feedback"] is False
    assert task_setup.strategy_preset_for(view["strategy"]) == "快速"


def test_a_resumed_task_keeps_the_configuration_it_started_with(tmp_path, data_dir):
    """已经开始的任务按它当初保存的 pipeline_config 继续（批次划分不能中途变）。"""
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _prepared_job(tmp_path, config, "标准")
    state = adapter.load_state(job_id)
    state["pipeline_config"] = {"translation_concurrency": 2, "batch_size": 3,
                                "max_batch_chars": 999, "knowledge_feedback": False}
    state["p1_done"] = True
    core.save_job_state(job_id, state)
    kwargs = adapter.pipeline_kwargs(config, adapter.load_state(job_id))
    assert (kwargs["translation_concurrency"], kwargs["batch_size"],
            kwargs["max_batch_chars"], kwargs["knowledge_feedback"]) == (2, 3, 999, False)


def test_legacy_tasks_without_the_switch_keep_knowledge_feedback(tmp_path, data_dir):
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _prepared_job(tmp_path, config, "标准")
    state = adapter.load_state(job_id)
    state.pop("knowledge_feedback", None)
    state["pipeline_config"] = {"batch_size": 4, "max_batch_chars": 2400}
    state["p1_done"] = True
    core.save_job_state(job_id, state)
    assert adapter.pipeline_kwargs(config, adapter.load_state(job_id))["knowledge_feedback"]


# ------------------------------------------------- 真实流水线 + 本地 stub 模型 --

class _Stub:
    def __init__(self, tmp_path: Path) -> None:
        self.ready = tmp_path / "stub-ready.json"
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--ready-file", str(self.ready)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while not self.ready.exists():
            if time.monotonic() > deadline or self.process.poll() is not None:
                raise RuntimeError("stub LLM server did not start")
            time.sleep(0.05)
        self.url = json.loads(self.ready.read_text(encoding="utf-8"))["url"]

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=10)


def _run(tmp_path, preset: str, url: str, texts) -> dict:
    config = adapter.EngineConfig(provider="自定义中转站", model="stub-model", api_key="k",
                                  base_url=url, target_lang="简体中文",
                                  enable_report=False, enable_source_cleanup=False)
    job_id = _prepared_job(tmp_path, config, preset, texts=texts)
    source = (tmp_path / f"{preset}.docx").read_bytes()
    adapter.start_job(job_id, f"{preset}.docx", source, config)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = adapter.load_state(job_id)
        status = adapter.runtime_view(job_id, state).get("status")
        if status in ("completed", "failed", "cancelled", "interrupted"):
            break
        time.sleep(0.2)
    from qt_workers import drain_job_workers

    drain_job_workers(core)
    state = adapter.load_state(job_id)
    assert status == "completed", status
    return state


def test_fast_mode_really_skips_knowledge_feedback_and_uses_bigger_batches(
        tmp_path, data_dir, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    stub = _Stub(tmp_path)
    try:
        # Different text per run: importing identical content reuses the task.
        fast = _run(tmp_path, "快速", stub.url, WORKLOAD[:48])
        standard = _run(tmp_path, "标准", stub.url, WORKLOAD[48:96])
    finally:
        stub.stop()
    fast_roles = (fast.get("llm_usage") or {}).get("by_role") or {}
    standard_roles = (standard.get("llm_usage") or {}).get("by_role") or {}
    assert not (fast_roles.get("auxiliary") or {}).get("calls"), fast_roles
    assert (standard_roles.get("auxiliary") or {}).get("calls"), standard_roles
    policy = fast["knowledge_feedback_policy"]
    assert policy["disabled"] is True and set(policy["skip_reasons"]) == {"disabled_by_strategy"}
    assert fast["pipeline_config"]["knowledge_feedback"] is False
    assert fast["pipeline_config"]["translation_concurrency"] == 6
    assert (fast_roles["translation"]["calls"]
            < standard_roles["translation"]["calls"]), "bigger batches mean fewer calls"


# --------------------------------------------------------------------- 预估 --

#: benchmark/translation-efficiency/workload.json (239 segments, 31,775 chars),
#: real pipeline + local stub model, P7 re-measure 2026-10-15: (calls, input tokens).
MEASURED_SIZE = {"segments": 239, "chars": 31_775}
MEASURED = {"快速": (67, 34_230), "标准": (149, 65_856), "学术增强": (222, 137_707)}


def test_the_estimate_reproduces_the_benchmark_measurement():
    size = dict(MEASURED_SIZE)
    estimates = adapter.strategy_estimates(size)
    for preset, (calls, tokens) in MEASURED.items():
        assert estimates[preset]["calls"] == pytest.approx(calls, rel=0.06), preset
        assert estimates[preset]["input_tokens"] == pytest.approx(tokens, rel=0.05), preset
    fast, standard, fine = (estimates[key]["input_tokens"] for key in PRESETS)
    assert fast < 0.6 * standard and fine > 1.8 * standard, "a clear gradient"
    assert estimates["快速"]["minutes"] < estimates["标准"]["minutes"] < \
        estimates["学术增强"]["minutes"]


def test_the_estimate_follows_the_actual_switches():
    base = dict(task_setup.STRATEGY_PRESETS["标准"])
    plain = adapter.strategy_estimate(base, 400, 60_000)
    reviewed = adapter.strategy_estimate(dict(base, enable_review=True), 400, 60_000)
    quiet = adapter.strategy_estimate(dict(base, knowledge_feedback=False), 400, 60_000)
    assert reviewed["input_tokens"] > 2 * plain["input_tokens"] - plain["input_tokens"] * 0.3
    assert quiet["input_tokens"] < plain["input_tokens"]
    assert adapter.strategy_estimate(base, 0, 0) == {"calls": 0, "input_tokens": 0,
                                                     "minutes": 0.0}


def test_source_size_reads_the_text_layer_without_a_model(tmp_path, data_dir):
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _prepared_job(tmp_path, config, "标准", texts=WORKLOAD[:30])
    size = adapter.source_size(job_id)
    assert size["segments"] == 30
    assert size["chars"] == sum(len(text) for text in WORKLOAD[:30])
    assert size["approximate"] is False


def test_the_mode_cards_show_each_modes_cost(tmp_path, data_dir):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from folith.qt import i18n
    from folith.qt.setup_panel import TaskSetupPanel

    app = QApplication.instance() or QApplication([])
    i18n.set_language("zh-CN")
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _prepared_job(tmp_path, config, "标准", texts=WORKLOAD[:60])
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(config, job_id))
    deadline = time.monotonic() + 10
    while not panel.source_size() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert panel.source_size()["segments"] == 60
    cards = {key: panel.mode_selector.button(key).estimate() for key in PRESETS}
    assert all("token" in text and "分钟" in text and "次调用" in text for text in cards.values()), cards
    assert all(text in panel.mode_selector.button(key).accessibleDescription()
               for key, text in cards.items())
    assert "预估" in panel.mode_summary.text()
    assert "倍" not in panel.mode_summary.text(), "标准 is the reference itself"
    panel.mode_selector.button("学术增强").click()
    app.processEvents()
    assert "约为「标准」的 2" in panel.mode_summary.text()
    panel.deleteLater()
