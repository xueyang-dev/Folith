"""Language Assets Workspace（术语与翻译记忆）的回归测试。

覆盖两层：

1. `folith.language_assets` 的纯投影（不依赖 Streamlit）：聚合、过滤、
   段落引用、以及「后端没有的字段不伪造」这条硬约束；
2. `app.py` 里的工作区：三个 Tab、搜索、过滤、Inspector、两阶段接受、
   Quick Accept、仅此次采用、拒绝、多选、批量接受/拒绝、审核后列表更新、
   空状态与错误状态，以及既有 review API 契约没有被破坏。
"""
import shutil
import sys
import tempfile
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import knowledge, language_assets, models

ROOT = Path(__file__).resolve().parent.parent

_STYLE_BLOCK = re.compile(r"<style>.*?</style>", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _css_rules(css):
    """把样式表切成 (选择器, 声明体) 列表。

    **刻意不用正则切规则。** `([^{}]*)\\{` 这种写法在「有花括号的 CSS 后面跟着
    一大段没有花括号的文本」时会退化成 O(n²)：正则要在每个起始位置把后面的字符
    逐个回吐去找 `{`。而页面上注入的 markdown 恰好就是「CSS + 大段 HTML」这个形状，
    实测足以把整个 AppTest 跑到像卡死一样。`str.split` 是线性的，而且同样能还原
    「选择器跨行」与 `@media { … }` 里嵌套的规则。
    """
    parts = css.split("{")
    return [(" ".join(parts[index - 1].rsplit("}", 1)[-1].split()),
             parts[index].split("}", 1)[0])
            for index in range(1, len(parts))]


def _css_decl(css, selector, prop):
    """取某条 CSS 规则的某个声明值（没有该规则 / 该声明则返回 None）。

    * 同权重的多条规则取**最后**一条 —— 那才是级联里真正生效的。
    * 选择器按空白折成单空格再比对，所以跨行的组合选择器不会被误当成别条规则。
    """
    clean = _CSS_COMMENT.sub("", css)
    target = " ".join(selector.split())
    bodies = [body for sel, body in _css_rules(clean) if sel == target]
    if not bodies:
        return None
    match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", bodies[-1])
    return match.group(1).strip() if match else None


# ================= 夹具 =================

def _job(job_id, filename, *, glossary=None, candidates=None, units=None,
         target_lang="简体中文", project_id=None):
    state = core.new_job_state(filename)
    state.update(
        p1_done=True, p2_done=True, report_enabled=False,
        pipeline_config={"target_lang": target_lang},
        paras=["How do earth drones and their volumetric sensors define space?",
               "The point cloud matters for remote sensing method."],
        pairs=[{
            "source": "How do earth drones and their volumetric sensors define space?",
            "target": "地球无人机及其体积传感器如何定义空间？",
            "reviewed": True, "target_provenance": "reviewed",
            "accepted_target": "地球无人机及其体积传感器如何定义空间？",
        }],
        semantic_units=units or [{"unit_id": "u1", "kind": "section",
                                  "label": "Chapter 5",
                                  "start_segment": 0, "end_segment": 1}],
        glossary=list(glossary or []),
        knowledge_candidates=list(candidates or []),
    )
    if project_id:
        state["project_id"] = project_id
    return state


def _candidate(source, target, *, segment=0, occurrences=None, kind="term",
               confidence=0.86):
    return {
        "source": source, "observed_target": target,
        "first_observed_segment": segment,
        "occurrences": list(occurrences if occurrences is not None else [segment]),
        "observed_segments": [segment],
        "status": "emergent_candidate", "origin": "translation_observation",
        "kind": kind, "confidence": confidence,
    }


def _term(source, target, *, status="locked", domain="技术", scope="document",
          occurrences=3, **extra):
    entry = {"source": source, "target": target, "preferred": target,
             "status": status, "domain": domain, "behavior": "translate",
             "scope": scope, "occurrences": list(range(occurrences))}
    entry.update(extra)
    return entry


class _Workspace:
    """临时 outputs 目录 + 预置任务的上下文管理器。"""

    def __init__(self, jobs=(), tm=None, tm_project=None):
        self.jobs = jobs
        self.tm = tm or {}
        self.tm_project = tm_project

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="la-workspace-"))
        self.old = core.OUTPUT_DIR
        core.OUTPUT_DIR = self.tmp
        for job_id, state in self.jobs:
            core.save_job_state(job_id, state)
        if self.tm:
            core.save_tm(self.tm, self.tm_project)
        return self

    def __exit__(self, *exc):
        core.OUTPUT_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def _app(session=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["app_view"] = "library"
    for key, value in (session or {}).items():
        at.session_state[key] = value
    at.run()
    return at


def _keys(elements, prefix):
    return [element for element in elements
            if str(getattr(element, "key", "") or "").startswith(prefix)]


def _buttons(at, prefix):
    return _keys(at.button, prefix)


def _captions(at):
    return "\n".join(str(item.value) for item in at.caption)


def _page_markup(at):
    """主区域的原始 HTML（AppTest 的 markdown 含注入的 <style>，做否定断言前要留意）。"""
    return "\n".join(str(item.value) for item in at.markdown)


def _page_content(at):
    """剥掉注入的 `<style>` 之后的主区域 HTML。

    必需的：样式表里含界面类名与文案（`.la-inspector-title`、注释里的说明文字），
    不剥掉的话「某个元素不存在」这类否定断言会被 CSS 文本假性命中。
    """
    return _STYLE_BLOCK.sub("", _page_markup(at))


def _tab_labels(at):
    """一级 Tab 上的文案。计数就挂在这里，不再有另一行统计卡。"""
    return list(at.segmented_control[0].options)


# ================= 1. 纯投影层 =================

def test_term_rows_aggregate_across_tasks_and_keep_real_counts():
    jobs = [
        {"job_id": "a", "state": _job("a", "Book A.docx",
                                      glossary=[_term("volumetric sensors", "体积传感器",
                                                      occurrences=22)])},
        {"job_id": "b", "state": _job("b", "Book B.docx",
                                      glossary=[_term("volumetric sensors", "体积传感器",
                                                      status="provisional",
                                                      occurrences=8)])},
    ]
    rows = language_assets.group_terms(language_assets.build_term_rows(jobs))
    assert len(rows) == 1, "同一 source→preferred 必须合并成一行"
    row = rows[0]
    assert row["usage"] == 30, "使用次数是 occurrences 之和，不是估算"
    assert sorted(row["documents"]) == ["Book A.docx", "Book B.docx"]
    assert row["status"] == "locked", "合并后展示最强的状态"
    assert len(row["tasks"]) == 2


def test_term_scope_buckets_keep_project_global_and_document_distinct():
    assert language_assets.scope_bucket("global") == "global"
    assert language_assets.scope_bucket("project") == "project"
    assert language_assets.scope_bucket("") == "document"
    assert language_assets.scope_bucket("section:s1") == "document"
    assert language_assets.scope_label("") == "本文档"
    assert language_assets.scope_label("project") == "本项目"


def test_term_language_filter_uses_persisted_target_language():
    jobs = [
        {"job_id": "zh", "state": _job("zh", "Chinese.docx",
                                          glossary=[_term("drone", "无人机")])},
        {"job_id": "ar", "state": _job("ar", "Arabic.docx",
                                          target_lang="العربية",
                                          glossary=[_term("drone", "طائرة")])},
    ]
    rows = language_assets.group_terms(language_assets.build_term_rows(jobs))
    assert set(language_assets.target_language_options(rows)) == {"简体中文", "العربية"}
    filtered = language_assets.filter_terms(rows, target_lang="العربية")
    assert [row["preferred"] for row in filtered] == ["طائرة"]
    assert language_assets.scope_label("global") == "全局"
    assert language_assets.scope_label("section:s1") == "章节 · s1"
    assert language_assets.scope_label("自定义作用域") == "自定义作用域"


def test_project_term_rows_use_real_project_memory_and_do_not_double_count_usage():
    project = {
        "project_id": "p1", "name": "无人机研究",
        "glossary": [_term("volumetric sensors", "体积传感器", occurrences=0)],
        "glossary_versions": [{"version": 3}],
    }
    job_id = "laprojectrows00001"
    state = _job(job_id, "Book A.docx", project_id="p1",
                 glossary=[_term("volumetric sensors", "体积传感器", occurrences=22)])
    task_rows = language_assets.build_term_rows([{"job_id": job_id, "state": state}])
    project_rows = language_assets.build_project_term_rows([project], task_rows=task_rows)
    rows = language_assets.group_terms([*task_rows, *project_rows])
    assert len(rows) == 1
    assert rows[0]["scope_bucket"] == "project"
    assert rows[0]["scope_label"] == "项目 · 无人机研究"
    assert rows[0]["usage"] == 22, "项目记忆行不能把任务 occurrence 计数加两遍"
    assert rows[0]["project_names"] == ["无人机研究"]
    assert rows[0]["tasks"][0]["job_id"] == job_id


def test_tm_rows_do_not_invent_source_document_or_match_percentage():
    rows = language_assets.build_tm_rows({
        "The point cloud matters.": {"target": "点云很重要。", "reviewed": True,
                                     "updated_at": "2026-09-01T08:15:00+03:00"},
    })
    assert len(rows) == 1
    assert set(rows[0]) == {"row_id", "source", "target", "target_lang",
                            "updated_at", "reviewed", "source_chars"}, \
        "翻译记忆行只暴露后端真正存下来的字段（含作用域：目标语言）"
    assert not rows[0]["target_lang"], \
        "没有语言标注的旧条目必须如实显示为空，而不是猜一种语言"


def test_tm_rows_split_the_scoped_key_into_source_and_target_language():
    """记忆键是「目标语言 + 原文」；界面要看到原文与目标语言，而不是内部键。"""
    tm = {}
    core.tm_put(tm, "The point cloud matters.", "点云很重要。", "简体中文")
    core.tm_put(tm, "The point cloud matters.", "Le nuage de points compte.",
                "Français")
    rows = language_assets.build_tm_rows(tm)
    assert len(rows) == 2, "同一原文的两种目标语言是两条独立记忆"
    assert {row["source"] for row in rows} == {"The point cloud matters."}
    assert {row["target_lang"] for row in rows} == {"简体中文", "Français"}
    assert language_assets.filter_tm(rows, target_lang="Français")[0]["target"] == \
        "Le nuage de points compte."


def test_tm_rows_drop_unreviewed_and_empty_entries():
    rows = language_assets.build_tm_rows({
        "a": {"target": "甲", "reviewed": True},
        "b": {"target": "乙"},
        "c": {"target": "", "reviewed": True},
    })
    assert [row["source"] for row in rows] == ["a"]


def test_candidate_rows_carry_real_positions_and_honest_usage():
    jobs = [{"job_id": "a", "state": _job(
        "a", "Book A.docx",
        candidates=[_candidate("point cloud", "点云", segment=1,
                               occurrences=[1, 1, 1])])}]
    row = language_assets.build_candidate_rows(jobs)[0]
    assert row["occurrence_count"] == 3
    assert row["positions_known"] is True
    assert row["positions"] == ["Chapter 5 · #2"], "段落引用要带真实章节标签"
    assert language_assets.usage_text(row) == "出现 3 次"
    assert language_assets.confidence_text(row) == "0.86"
    assert row["high_confidence"] is True


def test_candidate_rows_say_unknown_instead_of_zero_when_no_occurrences():
    jobs = [{"job_id": "a", "state": _job(
        "a", "Book A.docx",
        candidates=[_candidate("ghost term", "幽灵术语", occurrences=[])])}]
    row = language_assets.build_candidate_rows(jobs)[0]
    assert row["positions_known"] is False
    assert language_assets.usage_text(row) == "观察到 1 次"


def test_decided_candidates_leave_the_review_queue():
    state = _job("a", "Book A.docx",
                 candidates=[_candidate("point cloud", "点云")])
    state["knowledge_candidates"][0]["decision"] = "project_term"
    rows = language_assets.build_candidate_rows([{"job_id": "a", "state": state}])
    assert rows == []
    kept = language_assets.build_candidate_rows([{"job_id": "a", "state": state}],
                                                include_decided=True)
    assert len(kept) == 1


def test_candidate_conflicts_are_reported_from_existing_terms():
    state = _job("a", "Book A.docx",
                 glossary=[_term("continuity", "连续性")],
                 candidates=[_candidate("continuity", "连贯性")])
    row = language_assets.build_candidate_rows([{"job_id": "a", "state": state}])[0]
    assert row["has_conflict"] is True
    assert "连续性" in row["conflict_summary"]


def test_new_candidate_filter_is_derived_from_existing_terms_not_kind():
    state = _job(
        "lanewcandidate0001", "Book A.docx",
        glossary=[_term("known term", "已知术语")],
        candidates=[_candidate("known term", "已知术语"),
                    _candidate("brand new", "全新术语")],
    )
    rows = language_assets.build_candidate_rows([{
        "job_id": "lanewcandidate0001", "state": state,
    }])
    assert {row["source"]: row["is_new"] for row in rows} == {
        "brand new": True, "known term": False}
    assert [row["source"] for row in
            language_assets.filter_candidates(rows, only_new=True)] == ["brand new"]


def test_filters_are_backed_by_real_fields():
    jobs = [{"job_id": "a", "state": _job(
        "a", "Book A.docx",
        candidates=[_candidate("point cloud", "点云", confidence=0.86),
                    _candidate("sensorium", "感知域", confidence=0.35),
                    _candidate("Dr. Elena", "Elena 博士", kind="name",
                               confidence=0.35)])}]
    rows = language_assets.build_candidate_rows(jobs)
    assert len(language_assets.filter_candidates(rows, confidence="high")) == 1
    assert len(language_assets.filter_candidates(rows, confidence="low")) == 2
    assert len(language_assets.filter_candidates(rows, kind="term")) == 2
    assert len(language_assets.filter_candidates(rows, query="point")) == 1
    assert len(language_assets.filter_candidates(rows, only_high=True)) == 1
    assert len(language_assets.filter_candidates(rows, kind="all")) == 3, \
        "kind='all' 是「不过滤」，不是「kind 必须等于 all」"


def test_unknown_confidence_is_not_mislabeled_as_low_confidence():
    jobs = [{"job_id": "launknownconfidence1", "state": _job(
        "launknownconfidence1", "Book A.docx",
        candidates=[_candidate("unknown", "未知", confidence=None)])}]
    rows = language_assets.build_candidate_rows(jobs)
    assert len(language_assets.filter_candidates(rows, confidence="unknown")) == 1
    assert language_assets.filter_candidates(rows, confidence="low") == []


def test_summary_counts_only_real_sources():
    jobs = [{"job_id": "a", "state": _job(
        "a", "Book A.docx", glossary=[_term("point cloud", "点云")],
        candidates=[_candidate("sensorium", "感知域")])}]
    summary = language_assets.language_asset_summary(jobs, tm_count=7)
    assert summary == {"terms": 1, "tm": 7, "review": 1, "conflicts": 0}


# ================= 2. 页面：Tab / 搜索 / 过滤 =================


# ================= 3. Inspector =================


# ================= 3b. 出现位置 → 工作台深链 =================

def test_candidate_positions_carry_real_segment_indices():
    """出现位置必须同时带显示文案和**真实段落下标**，否则跳转无从下手。"""
    job_id = "laposidx000000001"
    state = _job(job_id, "Book A.docx",
                 candidates=[_candidate("point cloud", "点云", segment=1,
                                        occurrences=[1, 1, 0])])
    with _Workspace([(job_id, state)]):
        rows = language_assets.build_candidate_rows(core.list_jobs())
        row = next(item for item in rows if item["source"] == "point cloud")
        assert row["positions"] == ["Chapter 5 · #2", "Chapter 5 · #1"], \
            "重复出现要去重，顺序按首次出现"
        assert [entry["index"] for entry in row["position_entries"]] == [1, 0], \
            "下标必须是原始段落下标，不能被格式化掉"
        assert row["position_total"] == 2


# ================= 4. 两阶段决策 =================

def _review_app(job_id, state):
    return _Workspace([(job_id, state)])


# ================= 5. 批量审核 =================


# ================= 6. 空状态 / 错误状态 =================


# ================= 7. 既有契约与新增后端能力 =================

def test_existing_review_api_contract_is_unchanged():
    """重构只能改 IA；review_knowledge_candidate 的语义必须原样保留。"""
    tmp = Path(tempfile.mkdtemp(prefix="la-contract-"))
    old = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "lacontract00000001"
        state = _job(job_id, "Book A.docx",
                     candidates=[_candidate("point cloud", "点云")])
        core.save_job_state(job_id, state)
        candidate_id = knowledge.candidate_id(state["knowledge_candidates"][0])
        promoted, ok, message = core.review_knowledge_candidate(
            job_id, candidate_id, "project_term")
        assert ok, message
        assert promoted["glossary_frozen"]["version"] == 1
        assert promoted["glossary"][0]["status"] == "locked"
        assert promoted["glossary_frozen"]["glossary_hash"] == \
            models.glossary_hash(promoted["glossary"])
        again, again_ok, _ = core.review_knowledge_candidate(
            job_id, candidate_id, "task_only")
        assert not again_ok, "同一条候选不能被处理两次"
        assert again["knowledge_candidates"][0]["decision"] == "project_term"
        try:
            core.review_knowledge_candidate(job_id, candidate_id, "nonsense")
        except ValueError:
            pass
        else:
            raise AssertionError("非法决策必须抛 ValueError")
    finally:
        core.OUTPUT_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_glossary_mutation_helpers_keep_the_freeze_invariant():
    """编辑/新增/删除术语必须生成新的术语版本，而不是悄悄改草稿。"""
    tmp = Path(tempfile.mkdtemp(prefix="la-mutate-"))
    old = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "lamutate0000000001"
        core.save_job_state(job_id, _job(
            job_id, "Book A.docx",
            glossary=[_term("volumetric sensors", "体积传感器", status="provisional")]))
        entry_id = models.normalize_glossary(
            core.load_job_state(job_id)["glossary"])[0]["id"]

        state, ok, message = core.update_glossary_entry(
            job_id, entry_id, preferred="体积传感装置")
        assert ok, message
        assert state["glossary"][0]["preferred"] == "体积传感装置"
        assert state["glossary_frozen"]["version"] == 1
        assert state["stage"] == "GLOSSARY_FROZEN"
        assert any(action["action"] == "glossary_entry_updated"
                   for action in state["human_actions"])

        state, ok, message, new_id = core.add_glossary_entry(
            job_id, "point cloud", "点云", domain="技术")
        assert ok, message and new_id
        assert state["glossary_frozen"]["version"] == 2
        assert {entry["source"] for entry in state["glossary"]} == \
            {"volumetric sensors", "point cloud"}

        state, ok, message = core.add_glossary_entry(job_id, "point cloud", "点云集")[:3]
        assert not ok and "已存在" in message, "同名不同译法必须被拒绝"

        state, ok, message = core.delete_glossary_entry(job_id, new_id)
        assert ok, message
        assert [entry["source"] for entry in state["glossary"]] == ["volumetric sensors"]
        assert state["glossary_frozen"]["version"] == 3
    finally:
        core.OUTPUT_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


