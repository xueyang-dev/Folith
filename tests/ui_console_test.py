"""UI 控制台闭环测试：用真实 `app.py` 验证"人能走完一遍"。

`scenario_gate_test.py` 证明后端闭环成立；本模块证明**界面闭环**成立：用户能在
真实 Streamlit 应用里从任务工作区走到审校决定、再到冻结交付，且界面上只呈现
当前任务的上下文。

为什么需要它：`tests/app_boot_test.py` 是 `main()` 入口，pytest 不收集，
因此在本次改动之前**没有任何 UI 断言进入 CI**。控制台的退化（导航消失、
交付按钮不见、把别的任务的数据渲染进来）不会被任何自动化发现。

设计约束与 `scenario_gate_test.py` 一致：完全离线、不发网络请求、不写真实
`outputs/`；任务由离线 provider 真实跑一遍管线产生，而不是手写 state，
这样"界面读到的"就是"管线真正写下的"。

运行：`python -m pytest tests/ui_console_test.py -q`
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from make_scenario_fixtures import (  # noqa: E402
    build_docx_scenario,
    build_terminology_scenario,
)
from offline_provider import OfflineProvider  # noqa: E402

PROVIDER = "OfflineFixture"
MODEL = "offline-fixture"
TARGET_LANG = "简体中文"

# 工作区导航：蓝图 Phase 3 要求的 Projects → Job → Translate → Review → Deliver。
# 任务工作台没有「概览」一级页面：全局摘要由任务 Banner 承担，打开任务即落到翻译。
WORKSPACE_SECTIONS = ("翻译", "术语", "审校", "交付")


@pytest.fixture(scope="module")
def source_doc(tmp_path_factory) -> dict:
    """术语密集场景（90 段，最小可用规模，跑得快）+ 一份用于对照的第二文档。"""
    target = tmp_path_factory.mktemp("ui-console")
    docx = target / "ui-scenario.docx"
    glossary = target / "ui-scenario.glossary.json"
    info = build_terminology_scenario(docx, glossary)
    # 对照文档：文件名与内容都与主场景不同，用于验证"界面只渲染当前任务"。
    other = target / "ui-other-document.docx"
    build_docx_scenario(other, sections=2, paragraphs=6)
    return {"dir": target, "docx": docx, "other": other,
            "glossary_path": glossary, "info": info,
            "glossary": __import__("json").loads(glossary.read_text(encoding="utf-8"))}


@contextmanager
def offline_console(provider: OfflineProvider):
    """把 core 的 provider 调用与输出目录替换为离线的临时环境。"""
    tmp = Path(tempfile.mkdtemp(prefix="ui-console-"))
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR, core.call_llm = tmp, provider
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
        shutil.rmtree(tmp, ignore_errors=True)


def run_pipeline(job_id: str, docx: Path, provider: OfflineProvider,
                 glossary: list[dict]) -> dict:
    """真实编排入口，产出界面将要读取的落盘状态。"""
    return core.run_job_pipeline(
        job_id, docx.name, docx.read_bytes(),
        provider=PROVIDER, api_key="offline", model=MODEL,
        target_lang=TARGET_LANG, auto_term=False, enable_report=False,
        translation_theory="", user_glossary=glossary,
        style_rules="保持学术书面语。", enable_review=True,
        enable_annotate=False, use_tm=True, delivery_config={"deliver_report": False})


def open_workspace(job_id: str, section: str = "translation"):
    """打开真实应用的任务工作区；返回 AppTest（已 run 一次）。"""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = section
    at.run()
    return at


def button(at, label: str):
    return next((b for b in at.button if b.label == label), None)


def labels(at) -> list[str]:
    return [b.label for b in at.button]


# ================= 闭环：工作区 → 审校 → 交付 =================


def test_ui_workspace_shows_the_closed_loop_navigation(source_doc):
    """工作区必须给出完整闭环导航，且不暴露第二套工作区表面。"""
    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider):
        run_pipeline("ui-loop", source_doc["docx"], provider, source_doc["glossary"])
        at = open_workspace("ui-loop")

        assert not at.exception, [e.value for e in at.exception]
        for section in WORKSPACE_SECTIONS:
            assert section in labels(at), f"缺少工作区导航入口：{section}"

        # 只应存在一套工作区导航；历史遗留的 `当前工作区` 切换器不得出现。
        assert not any(r.label == "当前工作区" for r in at.radio), \
            "工作区不应再出现第二套表面切换器"
        # 当前任务的上下文只应出现当前任务自己的资产面板。
        asset_panels = [e.label for e in at.expander
                        if str(e.label).startswith("资产与交付")]
        assert asset_panels == [], \
            f"任务工作区不应内联其它任务的资产面板：{asset_panels[:4]}"


def test_ui_delivery_freezes_a_final_snapshot(source_doc):
    """交付闭环：人可以在界面上冻结最终版本，且落盘成为 final + 快照。"""
    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider):
        run_pipeline("ui-freeze", source_doc["docx"], provider, source_doc["glossary"])
        at = open_workspace("ui-freeze", section="delivery")

        assert not at.exception, [e.value for e in at.exception]
        freeze = button(at, "创建交付版本 v1")
        assert freeze is not None, f"交付页必须有冻结动作，实际按钮：{labels(at)}"
        assert freeze.disabled is False, "干净任务必须可以冻结交付"

        freeze.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        state = core.load_job_state("ui-freeze")
        assert state["delivery_status"] == "final"
        from folith import snapshots
        manifest = snapshots.latest_snapshot(core.job_dir("ui-freeze"))
        assert manifest is not None, "界面冻结必须产出不可变快照"
        assert manifest["snapshot_version"] == 1

        # 界面必须把冻结结果呈现出来（版本 + 可下载资产）
        page = " ".join(str(m.value) for m in at.markdown)
        assert "v1" in page or "冻结" in page, "冻结后界面必须显示版本状态"
        assert at.download_button, "冻结交付必须提供可下载资产"


def test_ui_review_gate_blocks_delivery_then_human_decision_unblocks(source_doc):
    """审校闭环：界面必须显示阻塞，人工决定后可继续交付。"""
    target_segment = 3
    provider = OfflineProvider(glossary=source_doc["glossary"],
                              review_blocking_segments={target_segment})
    with offline_console(provider):
        run_pipeline("ui-gate", source_doc["docx"], provider, source_doc["glossary"])

        # --- 任务 Banner：必须把"必须处理的问题"作为下一步动作呈现 ---
        opened = open_workspace("ui-gate")
        assert not opened.exception, [e.value for e in opened.exception]
        primary = [b for b in opened.button if "问题" in b.label]
        assert primary, f"Banner 必须给出处理阻塞的下一步动作：{labels(opened)}"

        # --- 交付：未决时不得提供冻结动作 ---
        delivery = open_workspace("ui-gate", section="delivery")
        freeze = button(delivery, "创建交付版本 v1")
        assert freeze is None or freeze.disabled, \
            "存在未决 blocking 时不得允许冻结交付"

        # --- 审校：人工决定入口必须存在 ---
        review = open_workspace("ui-gate", section="review")
        assert not review.exception, [e.value for e in review.exception]
        preserve = button(review, "保留当前译文")
        assert preserve is not None, f"审校页必须提供人工决定动作：{labels(review)}"
        assert preserve.disabled is False, "语义 blocking 必须可由人决定"

        preserve.click()
        review.run()
        assert not review.exception, [e.value for e in review.exception]

        # --- 决定必须真的落盘为人类决定，并解除阻塞 ---
        state = core.load_job_state("ui-gate")
        decisions = [item for item in state.get("human_actions") or []
                     if item.get("record_type") == "human_decision"]
        assert decisions, "界面动作必须记录为 HumanDecision 审计"
        assert decisions[-1]["actor_type"] == "human"
        from folith.translation_evidence import translation_review_readiness
        assert translation_review_readiness(state)["ready"] is True, \
            "人工决定后必须解除交付阻塞"

        # --- 交付：现在可以冻结了 ---
        after = open_workspace("ui-gate", section="delivery")
        freeze = button(after, "创建交付版本 v1")
        assert freeze is not None and freeze.disabled is False, \
            "阻塞解除后必须可以冻结交付"


# ================= 入口：新建任务 → 开始 → 落到工作区 =================


def test_ui_new_task_journey_lands_in_the_workspace(source_doc):
    """入口闭环：配置就绪后点击「开始任务」，必须离开四步向导并进入任务工作区。

    这里把 worker 启动替换为桩：本测试验证的是**界面的路由与状态迁移**，
    而不是后台进程本身（后者在进程内无法被 monkeypatch 覆盖，会真的发起网络
    请求）。任务状态按**任务身份**（文档身份 + 本地化上下文）预先落盘，
    因此界面读到的 job_id 与真实一致：内容哈希只是文档身份，不是任务身份。
    """
    from streamlit.testing.v1 import AppTest
    import json as _json

    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider) as tmp:
        # 可用的 provider 配置：必须使用注册表里真实存在的服务商与模型，
        # 否则界面按设计会忽略未注册的配置。离线替身会接住所有模型调用。
        real_provider = "DeepSeek"
        real_model = core.PROVIDERS[real_provider]["models"][0]
        (tmp / "provider_config.json").write_text(_json.dumps({
            "provider": real_provider, "model": real_model,
            "api_key": "offline-fixture-key", "base_url": "",
        }, ensure_ascii=False), encoding="utf-8")

        data = source_doc["docx"].read_bytes()
        # 与界面同源的推导：未选择项目 -> 系统工作区；目标语言取默认值。
        job_id = core.task_job_id(data, project_id=core.system_project_id(),
                                  target_lang=TARGET_LANG)
        state = core.new_job_state(source_doc["docx"].name)
        core.save_job_state(job_id, state)
        core.save_source(job_id, data)

        started: list[str] = []
        old_start, old_alive = core.start_job_worker, core.is_job_worker_alive
        core.start_job_worker = lambda jid, name, payload, kwargs, base_url=None: (
            started.append(jid), True)[1]
        core.is_job_worker_alive = lambda jid: True
        try:
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
            at.session_state["app_view"] = "new"
            at.session_state["task_step"] = 4
            at.session_state["task_files"] = [
                {"name": source_doc["docx"].name, "bytes": data}]
            at.run()
            assert not at.exception, [e.value for e in at.exception]

            start = button(at, "开始任务")
            assert start is not None, f"确认页必须有开始动作：{labels(at)}"
            assert start.disabled is False, \
                "provider 已配置且已选择模型时，开始任务必须可用"

            start.click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]
        finally:
            core.start_job_worker, core.is_job_worker_alive = old_start, old_alive

        assert started == [job_id], "开始任务必须为上传的文档启动对应任务"
        # 必须离开四步向导，进入任务工作区
        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["workspace_mode"] is True
        assert at.session_state["active_job_id"] == job_id
        # 导航标签可能带状态后缀（例如"术语 · 不适用"），按前缀匹配，
        # 但必须确认是导航项本身而不是正文里的同名文字。
        nav_labels = labels(at)
        for section in WORKSPACE_SECTIONS:
            assert any(label == section or label.startswith(f"{section} · ")
                       for label in nav_labels), \
                f"落地工作区后缺少导航：{section}"
        # 向导步骤不应再与工作区导航并列出现
        assert not any(label.startswith("01  ") for label in labels(at)), \
            "进入工作区后不应继续显示四步向导导航"
        page = "\n".join([str(m.value) for m in at.markdown]
                         + [str(c.value) for c in at.caption])
        assert source_doc["docx"].name in page, "工作区必须显示当前任务的源文件"


# ================= Prepare 门禁：术语确认 → 冻结 → 继续翻译 =================


def test_ui_terminology_gate_freezes_and_continues_translation(source_doc):
    """准备阶段闭环：术语未确认时翻译不得开始，人工冻结后必须继续翻译。

    严格术语治理（研究与报告预设）下，后端会停在术语门禁；本测试验证界面上
    存在人工确认入口、冻结会写入带 hash 的版本，并且冻结后真的继续了任务。
    """
    import json as _json

    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider) as tmp:
        (tmp / "provider_config.json").write_text(_json.dumps({
            "provider": "DeepSeek", "model": "deepseek-v4-flash",
            "api_key": "offline-fixture-key", "base_url": "",
        }, ensure_ascii=False), encoding="utf-8")

        job_id = "ui-terms-gate"
        state = core.new_job_state("gate-paused.docx")
        state.update({
            "p1_done": True, "p2_done": False,
            "paras": ["The canopy closure index was recomputed.",
                      "Independent estimates of soil respiration differed."],
            "pairs": [], "quality_mode": True, "stage": "GLOSSARY_PENDING",
            "target_lang": TARGET_LANG,
            "glossary": [
                {"id": "t-canopy", "source": "canopy closure", "target": "林冠郁闭",
                 "preferred": "林冠郁闭", "status": "candidate",
                 "behavior": "translate", "occurrences": [0]},
                {"id": "t-soil", "source": "soil respiration", "target": "土壤呼吸",
                 "preferred": "土壤呼吸", "status": "candidate",
                 "behavior": "translate", "occurrences": [1]},
            ],
        })
        core.save_job_state(job_id, state)
        core.save_source(job_id, b"fixture source")

        resumed: list[str] = []
        old_resume, old_start, old_alive = (
            core.resume_job, core.start_job_worker, core.is_job_worker_alive)
        core.resume_job = lambda jid, name, kwargs, base_url=None: (
            resumed.append(jid), True)[1]
        core.start_job_worker = lambda *a, **k: True
        core.is_job_worker_alive = lambda jid: True
        try:
            at = open_workspace(job_id, section="terms")
            assert not at.exception, [e.value for e in at.exception]

            # 门禁必须明说"未冻结则不能继续翻译"
            page = "\n".join([str(w.value) for w in at.warning]
                             + [str(i.value) for i in at.info]
                             + [str(c.value) for c in at.caption])
            assert "术语尚未冻结" in page, f"术语门禁必须说明当前阻塞：{page[:300]}"

            freeze = button(at, "冻结并继续翻译")
            assert freeze is not None, f"术语页必须提供冻结入口：{labels(at)}"
            assert freeze.disabled is False, "存在待确认术语时必须允许人工冻结"

            freeze.click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]
        finally:
            core.resume_job, core.start_job_worker, core.is_job_worker_alive = (
                old_resume, old_start, old_alive)

        # 冻结必须落盘为带 hash 的人工版本
        after = core.load_job_state(job_id)
        frozen = after.get("glossary_frozen") or {}
        assert frozen.get("version") == 1, "首次冻结必须是 v1"
        assert frozen.get("glossary_hash"), "冻结版本必须带确定性 glossary_hash"
        assert frozen.get("frozen_by") == "用户"
        assert len(frozen.get("entries") or []) == 2

        # 冻结后必须真的继续任务，而不是停在原地
        assert resumed == [job_id], "冻结并继续必须触发任务继续（未被触发）"


# ================= 控制台的单一表面约束 =================


def test_ui_does_not_render_other_jobs_into_the_active_workspace(source_doc):
    """打开任务 A 时，界面不得出现任务 B 的资产/审校/报告内容。

    历史遗留的旧渲染面会把工作区里**每一个**任务的资产面板、审校队列和
    报告依次渲染到同一页；只要它再次可达，这个断言就会失败。
    """
    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider):
        run_pipeline("ui-a", source_doc["docx"], provider, source_doc["glossary"])
        run_pipeline("ui-b", source_doc["other"], provider, source_doc["glossary"])
        assert core.load_job_state("ui-a")["filename"] != \
            core.load_job_state("ui-b")["filename"], "对照任务必须文件名不同"
        other = core.load_job_state("ui-b")["filename"]

        at = open_workspace("ui-a")
        assert not at.exception, [e.value for e in at.exception]
        page = "\n".join([str(m.value) for m in at.markdown]
                         + [str(e.label) for e in at.expander]
                         + [str(c.value) for c in at.caption])
        assert other not in page, f"当前任务工作区里出现了其它任务的内容：{other}"

        # 审校队列只能有一个（当前任务的）；出现多个说明又渲染了别人的队列。
        queues = [r for r in at.radio if r.label == "审校队列"]
        assert len(queues) <= 1, f"审校队列出现 {len(queues)} 个"


def test_ui_app_boot_smoke_runs_in_ci():
    """把既有的 AppTest 冷启动冒烟纳入 CI。

    `tests/app_boot_test.py` 是 `main()` 入口，pytest 默认不收集，因此它的
    覆盖一直是"写了但没跑"。这里用一行 shim 让它在每次 `pytest` 中执行。
    """
    import app_boot_test

    app_boot_test.main()


# ================= 交付门禁一致性（UX 审查发现的缺陷）=================


def test_ui_delivery_gate_matrix_is_consistent(source_doc):
    """交付页的按钮可用性必须与"下一步"给出的门禁判断一致。

    审查发现的缺陷：冻结按钮过去只检查报告与 QA 门禁，于是翻译未完成或审校
    未就绪的任务也显示**可点击**的「确认并冻结最终版本」，点了必然失败；
    而报告/QA 阻塞时按钮却是禁用的——同一页两种口径。
    """
    from streamlit.testing.v1 import AppTest

    def delivery_state(provider, job_id, label):
        run_pipeline(job_id, source_doc["docx"], provider, source_doc["glossary"])
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.session_state["active_job_id"] = job_id
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["workspace_section"] = "delivery"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    # 干净任务：可以冻结
    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider):
        clean = delivery_state(provider, "gate-clean", "clean")
        freeze = button(clean, "创建交付版本 v1")
        assert freeze is not None and freeze.disabled is False, \
            "干净任务必须可以冻结交付"

    # 审校未就绪：不得提供可点击的冻结
    blocked = OfflineProvider(glossary=source_doc["glossary"],
                              review_blocking_segments={3})
    with offline_console(blocked):
        at = delivery_state(blocked, "gate-blocked", "blocked")
        freeze = button(at, "创建交付版本 v1")
        assert freeze is None or freeze.disabled, \
            "审校未就绪时冻结必须不可用"
        # 并且必须说明下一步该做什么
        page = "\n".join(str(m.value) for m in at.markdown)
        assert "下一步" in page

    # 翻译未完成：冻结必须禁用，且给出翻译进度
    with offline_console(OfflineProvider(glossary=source_doc["glossary"])) as tmp:
        job_id = "gate-untranslated"
        state = core.new_job_state(source_doc["docx"].name)
        state.update({"p1_done": True, "p2_done": False, "paras": ["a", "b"],
                      "pairs": [], "target_lang": TARGET_LANG})
        core.save_job_state(job_id, state)
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.session_state["active_job_id"] = job_id
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["workspace_section"] = "delivery"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        freeze = button(at, "创建交付版本 v1")
        assert freeze is None or freeze.disabled, \
            "翻译未完成时不得允许创建交付版本"
        page = "\n".join(str(m.value) for m in at.markdown)
        assert "翻译尚未完成" in page, page[:300]
        assert "0 / 2 段已翻译" in page, page[:300]


def test_ui_delivery_does_not_invent_downstream_work(source_doc):
    """纯翻译任务不得被告知"重建 N 项下游产物"。

    审查发现的缺陷：`dependency_impact_view` 只要译文变化就把所有已知下游产物
    标为 stale（包括本任务不适用的报告/QA 产物），于是交付页一边在网格里显示
    "学术产物同步：当前任务未启用 ✓"，一边要求用户"重建 9 项下游产物"。
    """
    from streamlit.testing.v1 import AppTest

    provider = OfflineProvider(glossary=source_doc["glossary"])
    with offline_console(provider):
        out = run_pipeline("no-downstream", source_doc["docx"], provider,
                           source_doc["glossary"])
        assert not out.get("report_enabled"), "本用例必须是纯翻译任务"
        impact = core.dependency_impact_view("no-downstream", out)
        # 前置事实：影响视图确实会把不适用的下游标记为 stale
        assert impact.get("status") == "stale" and impact.get("affected"), \
            "前提不成立：影响视图没有标记任何下游产物"

        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.session_state["active_job_id"] = "no-downstream"
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["workspace_section"] = "delivery"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        page = "\n".join(str(m.value) for m in at.markdown)
        assert "更新受影响的报告产物" not in page, \
            "纯翻译任务不应要求重建报告产物"
        assert "回到冻结操作" in page or "生成冻结交付" in page, page[:300]

        # 任务 Banner 同样不应声称"报告需要更新"
        banner = open_workspace("no-downstream")
        page = "\n".join(str(m.value) for m in banner.markdown)
        assert "报告需要更新" not in page, page[:300]


# ================= 审计 fixture 必须像真实任务 =================


def test_ui_audit_fixtures_match_real_job_state(tmp_path):
    """审计 fixture 的运行状态必须与业务状态一致，否则截图会误导。

    `scripts/ui_audit_fixtures.py` 生成的合成任务过去完全不写 runtime_state.json、
    也缺少 `enable_annotate` 等真实任务一定有的字段，于是**已完成**的任务会被
    判定为未完成，任务 Banner 随之渲染运行区（worker / lease / checkpoint）并出现
    「继续处理」——审计截图因此比真实情况杂乱。
    """
    import importlib.util

    from streamlit.testing.v1 import AppTest

    spec = importlib.util.spec_from_file_location(
        "ui_audit_fixtures", ROOT / "scripts" / "ui_audit_fixtures.py")
    fixtures_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures_module)
    fixtures_module.OUTPUT = tmp_path
    fixtures_module.PDF_PATH = tmp_path / "fixture-source.pdf"
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        fixtures_module.main()

        jobs = sorted(p.name for p in tmp_path.iterdir()
                      if p.is_dir() and p.name.startswith("ui-audit-"))
        assert len(jobs) >= 18, f"fixture 数量异常：{len(jobs)}"

        completed = []
        for job_id in jobs:
            state = core.load_job_state(job_id)
            business = core._runtime_business_complete(state)
            status = core.build_job_runtime_view(job_id, state).get("runtime_status")
            expected = "completed" if business else "idle_incomplete"
            assert status == expected, \
                f"{job_id}: runtime={status} 与业务完成={business} 不一致"
            if business:
                completed.append(job_id)
        assert len(completed) >= 15, "大多数 fixture 应当是已完成状态"

        # 真实任务一定留下 runtime_state.json，且进行中的任务带有真实进度。
        # 这一项无法靠"读取时推断"补上：推断只能给出状态，给不出进度。
        runtime_file = core.job_dir("ui-audit-in-progress") / core.RUNTIME_STATE_FILE
        assert runtime_file.is_file(), "fixture 必须写入 runtime_state.json"
        runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
        assert runtime.get("completed_units") == 2 and runtime.get("total_units") == 4, \
            f"进行中的 fixture 必须记录真实进度：{runtime.get('completed_units')}/" \
            f"{runtime.get('total_units')}"

        # 端到端：已完成任务的任务 Banner 不得出现运行区与「继续处理」
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.session_state["active_job_id"] = "ui-audit-clean"
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert not any("worker id" in str(c.value) for c in at.caption), \
            "已完成任务不应在 Banner 渲染运行详情"
        assert not any(b.label == "继续处理" for b in at.button), \
            "已完成任务不应出现「继续处理」"
        assert not any(e.label == "运行详情" for e in at.expander), \
            "已完成任务不应出现运行详情面板"
    finally:
        core.OUTPUT_DIR = old_output


# ================= 工作台 shell 的响应式契约 =================

def test_workspace_responsive_rules_anchor_on_a_rendered_column():
    """工作台的响应式规则必须锚在**真实存在**的容器上。

    回归：`@media (max-width: 900px)` 里"正文与 Inspector 改为堆叠"的规则原来
    写的是 `:has(.st-key-workspace_nav_col)`，而 `st-key-workspace_nav_col`
    **只存在于 CSS**——页面导航早已改成 Banner 下的横向工具条，渲染代码从不产出
    这个 key。于是"窄屏改为堆叠"从来没有生效过：720 CSS px（≈1440×900 的 200%
    缩放）下正文只剩 ~500px，搜索框与「筛选 ▾」的标签被截断成 `筛..`（实测见
    `docs/ui-audit/24-minimal-workspace/phase1/06-workbench-zoom200.png`）。

    钉两件事：死锚点不得再出现在任何选择器里；堆叠规则锚在当前真实渲染的
    Inspector 列上。注释里说明这段历史是允许的，所以比对前先剥掉 CSS 注释。
    """
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    styles_file = ROOT / "folith" / "ui" / "styles.py"
    if styles_file.is_file():
        source += "\n" + styles_file.read_text(encoding="utf-8")
    without_comments = re.sub(r"/\*[\s\S]*?\*/", "", source)

    assert "st-key-workspace_nav_col" not in without_comments, \
        "响应式规则又挂在了渲染代码里不存在的容器上（这类规则是静默失效的）"
    assert ":has(.st-key-workspace_context_col)" in source, \
        "窄屏堆叠规则必须锚在真实渲染的 Inspector 列上"
