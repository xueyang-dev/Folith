"""蓝图 §8 端到端验收门禁：三个场景 + 人类控制点，全程离线。

蓝图 `docs/foliothread-agentic-native-blueprint.md` §8 把发布进度定义为三个
端到端场景，取代"完成了多少模块"。本模块是那三个场景的可执行版本：

| 场景 | 本模块证明 |
| --- | --- |
| 20 页 DOCX | 导入、术语准备、翻译、审校、双语交付可完成 |
| 100 页 PDF | 版面恢复、章节上下文、中断恢复、受影响范围重建可用 |
| 术语密集文档 | 术语确认、TM 复用、独立审校和交付清单可信 |

外加两个人类控制点测试（§4.2）：Memory gate 与 Delivery gate。

设计约束：

- **完全离线**：所有 provider 调用由 `OfflineProvider` 承接，无网络、无 API key、
  无 `pytest.mark.skip`。CI 可以直接运行。
- **不提交二进制 fixture**：文档由 `scripts/make_scenario_fixtures.py` 确定性重建。
- **不声称翻译质量**：离线 provider 产出的是"结构有效"的译文，用于驱动状态机。
  语言质量评估属于 `eval/`，见 `eval/README.md`。

运行：`python -m pytest tests/scenario_gate_test.py -q`
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

import core  # noqa: E402
from make_scenario_fixtures import build_all  # noqa: E402
from offline_provider import OfflineProvider  # noqa: E402
from folith import pdf_ingestion  # noqa: E402
from folith import snapshots  # noqa: E402
from folith.translation_evidence import (  # noqa: E402
    record_runtime_human_decision,
    translation_review_readiness,
)

PROVIDER = "OfflineFixture"
MODEL = "offline-fixture"
TARGET_LANG = "简体中文"

# 运行时提示词判别标记（与 core.py / terminology.py / context.py 中的实际
# system prompt 对应；改动提示词时这里的判别会直接失败，而不是静默错配）。
# ================= 测试基础设施 =================


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> dict:
    """确定性生成三个场景的源文档，模块内复用。"""
    target = tmp_path_factory.mktemp("scenarios")
    return {"dir": target, "manifest": build_all(target)}


@contextmanager
def offline_job(provider: OfflineProvider):
    """把 core 的 provider 调用与输出目录替换为离线的临时环境。"""
    tmp = Path(tempfile.mkdtemp(prefix="scenario-gate-"))
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR, core.call_llm = tmp, provider
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
        shutil.rmtree(tmp, ignore_errors=True)


def run_pipeline(job_id: str, path: Path, *, provider: OfflineProvider,
                 glossary: list[dict] | None = None, enable_review: bool = True,
                 auto_term: bool = False, strict: bool = False) -> dict:
    """走真实编排入口 `core.run_job_pipeline`（core.py:5952）。"""
    return core.run_job_pipeline(
        job_id, path.name, path.read_bytes(),
        provider=PROVIDER, api_key="offline", model=MODEL,
        target_lang=TARGET_LANG, auto_term=auto_term, enable_report=False,
        translation_theory="", user_glossary=glossary or [],
        style_rules="保持学术书面语。", enable_review=enable_review,
        enable_annotate=False, use_tm=True, strict_terminology_governance=strict,
        delivery_config={"deliver_report": False})


def locked_glossary(fixtures: dict) -> list[dict]:
    """读取术语密集场景的锁定术语表。"""
    raw = json.loads(
        (fixtures["dir"] / "scenario-terms.glossary.json").read_text(encoding="utf-8"))
    return raw


def blocking_findings(state: dict) -> list[dict]:
    return [item for item in (state.get("findings") or [])
            if item.get("severity") == "blocking"]


def assert_all_segments_translated(state: dict) -> None:
    pairs = state["pairs"]
    assert len(pairs) == len(state["paras"]), "每个源段落都必须有译文"
    assert all((pair.get("target") or "").strip() for pair in pairs), "不允许空译文"


def test_scenario_fixtures_are_content_deterministic(fixtures, tmp_path):
    """fixture 必须可重复生成：重新生成一次，内容指纹必须一致。

    容器字节（DOCX 的 zip 时间戳、PDF 的内部文档 ID）不保证一致，
    因此这里比较 `content_sha256` 而不是文件 sha256。
    """
    rebuilt = build_all(tmp_path / "regenerated")
    origin = fixtures["manifest"]["scenarios"]
    assert set(rebuilt["scenarios"]) == set(origin), "场景集合必须稳定"
    for name, info in origin.items():
        assert rebuilt["scenarios"][name]["content_sha256"] == info["content_sha256"], \
            f"场景 {name} 的内容发生漂移"
        assert rebuilt["scenarios"][name]["paragraphs"] == info["paragraphs"]


# ================= 场景一：20 页 DOCX =================


def test_scenario_20_page_docx_import_to_delivery(fixtures):
    """20 页 DOCX：导入 → 术语 → 翻译 → 审校 → 双语交付可完成。"""
    path = fixtures["dir"] / "scenario-20p.docx"
    provider = OfflineProvider()
    with offline_job(provider):
        state = run_pipeline("scenario20docx", path, provider=provider,
                             enable_review=True)

        # --- Import：结构化导入，无警告 ---
        assert state["p1_done"] is True
        assert len(state["paras"]) == 745, "句级分段后保留全部正文与标题"
        assert not [w for w in state["warnings"] if "提取" in w]

        # --- Understand：文档画像与章节上下文 ---
        assert state["profile_done"] is True
        assert (state.get("document_profile") or {}).get("domain") == "生态学"

        # --- Translate：全部段落完成 ---
        assert state["p2_done"] is True
        assert_all_segments_translated(state)

        # --- 保留项必须原样回填（确定性检查的核心契约）---
        reserved = core.extract_preserved_tokens(" ".join(state["paras"]))
        joined = " ".join(pair["target"] for pair in state["pairs"])
        missing = [token for token in reserved if token not in joined]
        assert not missing, f"交付译文丢失保留项：{missing}"

        # --- Review：独立审校覆盖每个段落，且无未决 blocking ---
        readiness = translation_review_readiness(state)
        assert readiness["required"] is True
        assert readiness["ready"] is True, readiness
        assert readiness["status"] == "current"
        assert provider.calls["review"] > 0, "独立审校必须真的被调用"
        assert not blocking_findings(state)

        # --- Deliver：人工确认 → final → 可追溯快照 ---
        # `core.approve_delivery` 自己冻结快照（core.py:5804），不要再手工创建第二个。
        state, ok, errors = core.approve_delivery(
            "scenario20docx", note="验收场景人工确认", actor="user")
        assert ok is True, errors
        assert state["delivery_status"] == "final"

        manifest = snapshots.latest_snapshot(core.job_dir("scenario20docx"))
        assert manifest is not None, "人工确认必须产出交付快照"
        assert manifest["snapshot_version"] == 1
        assert manifest["delivery_status"] == "final"
        assert manifest["approval"]["actor"] == "user", "快照必须记录确认人"

        # 快照必须绑定到当前源文档与当前译文，而不是"某个版本"
        assert manifest["source_identity"]["source_hash"]
        assert manifest["translation_truth_hash"] == snapshots.translation_truth_hash(state)
        assert manifest["translation_state_identity"]
        # 双语交付资产必须真的产出
        names = {item["name"] for item in manifest["assets"]}
        assert names, "交付快照必须包含资产"
        assert any(name.endswith(".docx") for name in names), names


def test_scenario_20_page_docx_snapshot_is_stable_and_diverges_on_edit(fixtures):
    """冻结后的快照可枚举且完整；译文一旦被修改，必须报告偏离而不是静默可交付。"""
    path = fixtures["dir"] / "scenario-20p.docx"
    provider = OfflineProvider()
    job_id = "scenario20snap"
    with offline_job(provider):
        run_pipeline(job_id, path, provider=provider)
        state, ok, errors = core.approve_delivery(job_id, actor="user")
        assert ok is True, errors

        status = core.delivery_snapshot_status(job_id, state)
        assert status["latest"]["snapshot_version"] == 1
        assert status["current"] is True, "刚冻结的交付应与当前译文一致"
        assert status["diverged"] is False
        assert status["integrity"] is True, "快照资产必须全部可读回"

        # 资产必须真的能读回字节，而不是只有清单
        assets = core.delivery_snapshot_assets(job_id, 1)
        assert assets and all(data for data in assets.values())

        # 修改译文 -> 必须报告偏离
        reloaded = core.load_job_state(job_id)
        reloaded["pairs"][0]["target"] = reloaded["pairs"][0]["target"] + "（人工修改）"
        core.save_job_state(job_id, reloaded)
        diverged = core.delivery_snapshot_status(
            job_id, core.load_job_state(job_id))
        assert diverged["diverged"] is True, "修改译文后必须与冻结交付脱钩"
        assert diverged["current"] is False
        assert diverged["latest"]["snapshot_version"] == 1, "旧快照必须保留，不被覆盖"


# ================= 场景二：100 页 PDF =================


def test_scenario_100_page_pdf_layout_recovery(fixtures):
    """100 页 PDF：版面噪声剔除、断词还原、段落与页码恢复。"""
    path = fixtures["dir"] / "scenario-100p.pdf"
    expected = fixtures["manifest"]["scenarios"]["pdf_100p"]

    blocks = pdf_ingestion.classify_blocks(
        pdf_ingestion.extract_layout_blocks(path.read_bytes()))
    roles: dict[str, int] = {}
    for block in blocks:
        roles[block.get("role")] = roles.get(block.get("role"), 0) + 1

    # 每页一个重复页眉 + 一个页码，必须被识别为版面噪声而不是正文
    assert roles.get("header") == expected["pages"], roles
    assert roles.get("page_number") == expected["pages"], roles
    assert roles.get("body") == expected["paragraphs"], roles

    paragraphs = pdf_ingestion.reconstruct_paragraphs(blocks)
    assert len(paragraphs) == expected["paragraphs"], "段落边界必须按首行缩进恢复"
    assert not [p for p in paragraphs if expected["running_header"] in p], "页眉不得进入正文"
    assert not [p for p in paragraphs if p.strip().isdigit()], "页码不得进入正文"
    # 跨行连字符必须还原成完整单词
    assert any(expected["hyphenation"]["joined"] in p for p in paragraphs)
    assert not [p for p in paragraphs if "can-" in p or " opy" in p]


def test_scenario_100_page_pdf_translate_and_resume(fixtures):
    """100 页 PDF：中断后可从最近安全断点继续，且不重译已完成的批次。"""
    path = fixtures["dir"] / "scenario-100p.pdf"
    job_id = "scenario100pdf"

    class Interrupting(OfflineProvider):
        """第一批正常完成并落盘，之后模拟 provider 中断。"""

        def _translate(self, user_prompt):
            if self.calls["translate"] > 1:
                raise RuntimeError("simulated provider outage")
            return super()._translate(user_prompt)

    interruptions = Interrupting()
    resumed = OfflineProvider()

    class Switchable:
        """在同一个输出目录内切换 provider，模拟进程重启后换用新的调用方。"""

        def __init__(self):
            self.delegate = interruptions

        def __call__(self, *args, **kwargs):
            return self.delegate(*args, **kwargs)

    switchable = Switchable()
    # 注意：复用同一个 OUTPUT_DIR 才是真实恢复场景（本地状态留在磁盘上）。
    with offline_job(switchable):
        with pytest.raises(Exception):
            run_pipeline(job_id, path, provider=interruptions)

        partial = core.load_job_state(job_id)
        assert partial is not None, "中断后必须仍有可恢复的本地状态"
        committed = len(partial["pairs"])
        assert committed > 0, "首批应已提交，否则无法验证恢复"
        assert partial["p2_done"] is False
        assert committed < len(partial["paras"]), "中断必须发生在文档完成之前"
        assert (core.job_dir(job_id) / "source.bin").is_file(), \
            "源文件必须留存，恢复时不需要重新上传"
        first_target = partial["pairs"][0]["target"]

        # ---- 恢复：同一输出目录，新 provider ----
        switchable.delegate = resumed
        full_total = len(partial["paras"])
        state = run_pipeline(job_id, path, provider=resumed)

        assert state["p2_done"] is True
        assert_all_segments_translated(state)
        # PDF source paragraphs are retained separately; translation uses the
        # sentence-sized CAT units introduced by the scanned-PDF segmentation
        # contract.
        assert len(state["source_paragraphs"]) == 600
        assert len(state["paras"]) > len(state["source_paragraphs"])
        readiness = translation_review_readiness(state)
        assert readiness["ready"] is True, readiness
        # 已提交的批次必须原样复用，而不是从零重译
        assert state["pairs"][0]["target"] == first_target
        # Derived metadata attached at finalization (`document_ir.attach_pair_spans`
        # writes `source_spans`, the EPUB binder writes `epub_source_paragraphs`)
        # is excluded: this assertion is about the committed *translations* being
        # reused instead of retranslated.
        derived = {"epub_source_paragraphs", "source_spans"}
        assert [{k: v for k, v in pair.items() if k not in derived}
                for pair in state["pairs"][:committed]] == [
                    {k: v for k, v in pair.items() if k not in derived}
                    for pair in partial["pairs"]]
        # 恢复只需处理剩余段落：翻译调用数必须小于整篇所需批次数
        assert resumed.calls["translate"] < full_total / 4, (
            f"恢复应跳过已提交批次，实际调用 {resumed.calls['translate']} 次")


def test_scenario_100_page_pdf_glossary_change_invalidates_affected_range(fixtures):
    """受影响范围重建：冻结术语表变化后，依赖它的译文必须变 stale。"""
    path = fixtures["dir"] / "scenario-20p.docx"
    glossary = [{"source": "canopy", "target": "林冠", "preferred": "林冠",
                 "behavior": "translate", "status": "locked", "scope": "document"}]
    provider = OfflineProvider(glossary=glossary)
    job_id = "scenariostale"
    with offline_job(provider):
        state = run_pipeline(job_id, path, provider=provider, glossary=glossary)
        assert_all_segments_translated(state)
        affected = [index for index, para in enumerate(state["paras"])
                    if "canopy" in para.casefold()]
        assert affected, "场景文档必须包含待验证术语"

        # 人工修改锁定译名并重新冻结 -> 依赖该术语的段落必须被标记 stale
        updated = [dict(glossary[0], target="冠层", preferred="冠层")]
        core.freeze_glossary(job_id, updated, frozen_by="user")
        after = core.load_job_state(job_id)
        stale = [index for index, pair in enumerate(after["pairs"])
                 if pair.get("reviewed") is False or pair.get("glossary_stale")]
        assert stale, "术语变更后必须有段落被标记为受影响，而不是静默沿用旧译文"
        # 受影响范围应当与真实出现位置相关，而不是整篇失效
        assert len(stale) < len(after["pairs"]), "不应把未受影响段落一并作废"


# ================= 场景三：术语密集文档 =================


def test_scenario_terminology_dense_confirmation_and_tm_reuse(fixtures):
    """术语密集文档：术语确认、TM 复用、交付清单可信。"""
    path = fixtures["dir"] / "scenario-terms.docx"
    glossary = locked_glossary(fixtures)
    assert len(glossary) == 6

    provider = OfflineProvider(glossary=glossary)
    job_id = "scenarioterms"
    with offline_job(provider):
        # --- Prepare：术语提取 -> 严格治理下等待人工确认（翻译不得开始）---
        prepared = run_pipeline(job_id, path, provider=provider, glossary=glossary,
                                auto_term=True, strict=True)
        assert prepared["p2_done"] is False and prepared["pairs"] == []
        assert prepared["glossary_frozen"] in ({}, None)

        # --- Memory gate：人工确认并冻结术语，形成带 hash 的冻结版本 ---
        frozen_state = core.freeze_glossary(job_id, prepared["glossary"],
                                            frozen_by="user")
        frozen = frozen_state["glossary_frozen"]
        assert frozen["version"] == 1, "首次冻结必须是 v1"
        assert frozen["glossary_hash"], "冻结版本必须带确定性 glossary_hash"
        assert len(frozen["entries"]) >= len(glossary)
        assert frozen["frozen_by"] == "user"

        # --- Translate：冻结后继续，术语严格注入 ---
        state = run_pipeline(job_id, path, provider=provider, glossary=glossary,
                             auto_term=True, strict=True)
        assert state["p2_done"] is True, "冻结术语后必须能够继续翻译"
        assert_all_segments_translated(state)

        # 首选译名必须被遵守，禁止译名不得出现
        joined = " ".join(pair["target"] for pair in state["pairs"])
        for entry in glossary:
            if entry["behavior"] == "preserve":
                continue
            assert entry["target"] in joined, f"锁定术语未使用首选译名：{entry['source']}"
            for forbidden in entry["forbidden"]:
                assert forbidden not in joined, f"出现禁止译名：{forbidden}"

        # --- 交付清单必须绑定术语冻结版本，并包含术语资产 ---
        state, ok, errors = core.approve_delivery(job_id, actor="user")
        assert ok is True, errors
        manifest = snapshots.latest_snapshot(core.job_dir(job_id))
        assert manifest["active_terminology_version"]["version"] == 1
        assert manifest["active_terminology_version"]["glossary_hash"] == \
            frozen["glossary_hash"], "交付必须绑定当时冻结的术语版本"
        names = {item["name"] for item in manifest["assets"]}
        assert names, names

        # --- TM 复用：同一份文档第二次运行必须命中翻译记忆 ---
        second = "scenarioterms2"
        state2 = run_pipeline(second, path, provider=provider, glossary=glossary)
        assert state2["tm_used_count"] > 0, "重复文档必须复用已审校译文"
        assert state2["pairs"][0].get("from_tm") is True


def test_findings_alias_survives_per_batch_checkpoint():
    """回归：`save_job_state` 不得让 `findings_all` 变成孤儿列表。

    `translate_stage` 在整篇文档范围内持有
    `findings_all = state.setdefault("findings", [])`（core.py:1566），而每批
    结束都会 `save_job_state` -> `delivery.normalize_state_findings`。该函数
    曾经重新绑定 `state["findings"]`，导致第一批之后的所有 QA/审校发现都被写进
    一个已被丢弃的列表：state.json 里没有它们，但 `review_stats` 和
    `has_blocking`（由孤儿列表统计）仍然计数——即交付门禁看不到这些 blocking。

    这个缺陷只在"多批次 + 第一批之后出现缺陷"时暴露，单函数单元测试无法发现。
    """
    from folith import delivery

    with offline_job(OfflineProvider()):
        job_id = "aliasregression"
        state = core.new_job_state("alias.docx")
        core.save_job_state(job_id, state)
        findings_all = state.setdefault("findings", [])

        # 模拟批次边界：每批提交一次
        core.save_job_state(job_id, state)
        assert findings_all is state["findings"], \
            "save_job_state 不得替换 findings 列表对象"

        findings_all.append({
            "type": "check", "severity": "blocking", "category": "format_integrity",
            "segment_index": 7, "segment_id": 7,
            "summary": "译文遗漏必须保留的placeholder「{{artifact_path}}」",
        })
        assert len(state["findings"]) == 1
        assert len(delivery.unresolved_blocking(state)) == 1, \
            "blocking 必须对交付门禁可见"

        # 再提交一次，记录仍须存活并被持久化
        core.save_job_state(job_id, state)
        persisted = core.load_job_state(job_id)
        assert len(persisted["findings"]) == 1
        assert len(delivery.unresolved_blocking(persisted)) == 1


# ================= 人类控制点（蓝图 §4.2）=================


def test_memory_gate_blocks_translation_until_glossary_is_frozen(fixtures):
    """Memory gate：严格治理下，未审核的候选术语必须阻止翻译开始。"""
    path = fixtures["dir"] / "scenario-20p.docx"
    provider = OfflineProvider()  # 术语抽取返回 1 条 candidate
    job_id = "scenariomemorygate"
    with offline_job(provider):
        state = run_pipeline(job_id, path, provider=provider, auto_term=True,
                             strict=True)
        # 翻译不得开始：没有译文，也就没有任何可交付内容
        assert state["p2_done"] is False
        assert state["pairs"] == []
        assert state["review_stats"]["reviewed_segments"] == 0
        assert state["glossary_frozen"] in ({}, None)
        # 门禁必须向用户解释"为什么没有开始"，而不是静默跳过
        assert any("术语" in warning for warning in state["warnings"]), \
            state["warnings"]

        # 人工冻结候选术语后，同一任务可以继续
        core.freeze_glossary(job_id, state["glossary"], frozen_by="user")
        resumed = run_pipeline(job_id, path, provider=provider, auto_term=True,
                               strict=True)
        assert resumed["p2_done"] is True, "冻结术语后必须能够继续翻译"
        assert_all_segments_translated(resumed)


def test_delivery_gate_requires_human_decision_for_blocking_finding(fixtures):
    """Delivery gate：模型无法修好的结构破坏必须留给人，不能静默交付。"""
    path = fixtures["dir"] / "scenario-20p.docx"
    # 每个批次的第一段都丢掉保留项，且修复不生效 -> 必然留下 blocking。
    provider = OfflineProvider(drop_preserved_on={0}, repair_leaves_defect=True)
    job_id = "scenariodeliverygate"
    with offline_job(provider):
        state = run_pipeline(job_id, path, provider=provider)

        # 缺陷必须真的出现在交付译文里（否则这个场景没有测到东西）
        lost = [index for index, para in enumerate(state["paras"])
                if "{{artifact_path}}" in para
                and "{{artifact_path}}" not in state["pairs"][index]["target"]]
        assert lost, "注入的缺陷必须真实存在，否则门禁断言无意义"

        blockers = blocking_findings(state)
        assert blockers, "丢弃保留项必须产生 blocking finding"
        assert state["has_blocking"] is True
        assert [f for f in blockers
                if f.get("category") == "format_integrity"], blockers
        # review_stats 与 state["findings"] 必须一致（历史缺陷：二者会不一致）
        assert state["review_stats"]["blocking"] == len(blockers)

        # 交付必须被拒绝，并且必须说明是哪一段、什么原因
        _, ok, errors = core.approve_delivery(job_id, actor="user")
        assert ok is False, "存在未决 blocking 时不得进入 final"
        assert errors
        assert not any(error == "译文未通过最终交付检查" for error in errors), \
            f"拒绝必须给出可定位的原因，而不是泛化提示：{errors}"
        assert any("保留" in error for error in errors), errors
        assert core.delivery_snapshot_status(job_id)["latest"] is None


def test_delivery_gate_requires_human_decision_for_semantic_blocking(fixtures):
    """Review gate + Delivery gate：语义 blocking 必须由人决定后才能交付。"""
    path = fixtures["dir"] / "scenario-terms.docx"
    target_segment = 3
    provider = OfflineProvider(review_blocking_segments={target_segment})
    job_id = "scenariosemantic"
    with offline_job(provider):
        state = run_pipeline(job_id, path, provider=provider)

        # 语义审校必须真的产出一条要求人工确认的 blocking
        semantic = [item for item in state["findings"]
                    if item.get("severity") == "blocking"
                    and item.get("segment_id") == target_segment]
        assert semantic, "独立审校必须报出指定段落的 blocking"

        readiness = translation_review_readiness(state)
        assert readiness["ready"] is False
        assert readiness["blocking_finding_ids"], readiness
        finding_id = readiness["blocking_finding_ids"][0]

        # 未决时不得交付
        _, ok, errors = core.approve_delivery(job_id, actor="user")
        assert ok is False, errors
        assert core.delivery_snapshot_status(job_id)["latest"] is None

        # 模型不得为自己的输出背书：非人类 actor 必须被拒绝
        reloaded = core.load_job_state(job_id)
        with pytest.raises(ValueError):
            record_runtime_human_decision(
                reloaded, finding_id, "dismiss", "reviewer-model",
                actor_type="model", note="模型自我批准")

        # 人工驳回该发现（真实 HumanDecision 路径）
        reloaded, finding, audit = record_runtime_human_decision(
            reloaded, finding_id, "dismiss", "human-reviewer",
            actor_type="human", note="已人工核对，接受当前译文")
        assert audit["record_type"] == "human_decision"
        assert finding["status"] == "dismissed" and finding["resolved"] is True
        core.save_job_state(job_id, reloaded)

        after = core.load_job_state(job_id)
        assert translation_review_readiness(after)["ready"] is True, \
            "人工处理该发现后必须可以继续交付"

        # 决策必须留在任务里可追溯，而不是被丢弃
        decisions = [item for item in after.get("human_actions") or []
                     if item.get("record_type") == "human_decision"]
        assert decisions and decisions[-1]["actor"] == "human-reviewer"

        state, ok, errors = core.approve_delivery(job_id, actor="user")
        assert ok is True, errors
        manifest = snapshots.latest_snapshot(core.job_dir(job_id))
        assert manifest is not None


def test_delivery_gate_allows_explicit_human_risk_acceptance(fixtures):
    """Delivery gate：人工显式接受语义风险后可以交付，并留下审计记录。

    结构破坏（保留项丢失）不属于可接受风险，因此这里用语义 blocking 验证：
    `accept_blocking=True` 只对"审校发现 + 人工风险接受"这条路径生效。
    """
    path = fixtures["dir"] / "scenario-terms.docx"
    provider = OfflineProvider(review_blocking_segments={3})
    job_id = "scenarioriskaccept"
    with offline_job(provider):
        run_pipeline(job_id, path, provider=provider)
        state, ok, errors = core.approve_delivery(
            job_id, note="已知语义风险，人工接受", actor="user", accept_blocking=True)
        assert ok is True, errors
        assert state["delivery_status"] == "final"
        actions = [item for item in state.get("human_actions") or []
                   if item.get("action") == "accepted_risk"]
        assert actions, "风险接受必须写入人工审计记录"

        # 冻结交付必须把风险接受记录一并带进清单
        manifest = snapshots.latest_snapshot(core.job_dir(job_id))
        assert manifest["accepted_risks"], "交付清单必须可追溯被接受的风险"
        assert manifest["approval"]["note"] == "已知语义风险，人工接受"
