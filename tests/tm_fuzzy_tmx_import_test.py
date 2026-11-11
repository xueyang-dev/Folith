"""#148 端到端：导入 1,000 条 TMX 后，相似原文能拿到带分数的候选。

验收原文：「After importing 1,000 TMX entries, an end-to-end test in which a
similar source returns scored candidates (reusing ``import_tmx``).」

这里刻意**不**走任何测试专用的捷径：TMX 由真实的 `import_tmx` 解析并写入
真实的 `translation_memory.json`，再由真实的 `load_tm` 读回，最后交给
`tm_candidates`。整条链路证明的是「同一份 TM 真值 + 同一套作用域键」，
不需要改动 `core.py` 的翻译管线。
"""
from __future__ import annotations

import io

import pytest

import core
from folith.engine.tm_store import tm_candidate_index, tm_candidates

TARGET_LANG = "简体中文"
ENTRY_COUNT = 1000


def _tmx(entries) -> bytes:
    """构造一个最小的 Trados 风格 TMX 1.4 文件。"""
    units = []
    for source, target in entries:
        units.append(
            "<tu>"
            '<tuv xml:lang="en"><seg>'
            f"{source}"
            "</seg></tuv>"
            '<tuv xml:lang="zh-CN"><seg>'
            f"{target}"
            "</seg></tuv>"
            "</tu>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<tmx version="1.4"><header srclang="en" adminlang="en"'
        ' segtype="sentence" o-tmf="folith-test"/><body>'
        + "".join(units)
        + "</body></tmx>").encode("utf-8")


def _entries(count=ENTRY_COUNT):
    return [(f"Station {index} recorded the sediment budget against the "
             f"2019 baseline before the winter closure.",
             f"第 {index} 号监测站对照 2019 年基准记录了泥沙收支，"
             f"时值冬季封闭之前。")
            for index in range(count)]


@pytest.fixture
def temp_job_env(tmp_path, monkeypatch):
    from folith import persistence_profiler
    from folith.storage import job_repository as repo

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    try:
        yield tmp_path
    finally:
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        persistence_profiler.disable()


def test_tmx_import_then_similar_source_returns_scored_candidates(temp_job_env):
    result = core.import_tmx(io.BytesIO(_tmx(_entries())),
                             project_id=None, target_lang=TARGET_LANG)
    assert result["added"] == ENTRY_COUNT
    assert core.tm_path(None).is_file(), "必须真的落盘，而不是只在内存里"

    tm = core.load_tm(None)
    assert len(tm) == ENTRY_COUNT

    # 只改了一个数字（2019 -> 2024）的相似原文。
    query = ("Station 617 recorded the sediment budget against the "
             "2024 baseline before the winter closure.")
    items = tm_candidates(tm, query, TARGET_LANG)
    assert items, "导入 1,000 条 TMX 后，相似原文必须能拿到候选"
    top = items[0]
    assert top["kind"] in ("exact", "normalized", "fuzzy")
    assert 75 <= top["score"] <= 100
    assert "Station 617" in top["source"], "最相近的一条必须排第一"
    assert top["provenance"] == "tmx_import", "候选要能说明来源"
    # 排序确实把最近的一条放在最前：其余 999 条都少了一个数字的相似度。
    assert [item["score"] for item in items] == sorted(
        (item["score"] for item in items), reverse=True)

    # 完全相同的原文是精确命中，分数 100。
    exact = tm_candidates(tm, _entries()[617][0], TARGET_LANG)
    assert exact[0]["score"] == 100 and exact[0]["kind"] == "exact"

    # 预构建索引与逐次构建结果一致（一次运行内复用索引的调用约定）。
    index = tm_candidate_index(tm, TARGET_LANG)
    assert tm_candidates(tm, query, TARGET_LANG, index=index) == items


def test_tmx_import_is_queryable_through_the_existing_index_too(temp_job_env):
    """新接口是加法：现有 `tm_index` 仍按原语义工作。"""
    core.import_tmx(io.BytesIO(_tmx(_entries(50))), project_id=None,
                    target_lang=TARGET_LANG)
    tm = core.load_tm(None)
    source = _entries(50)[3][0]
    index = core.tm_index(tm, TARGET_LANG)
    key = index[core.tm_normalize(source)]
    assert core.tm_lookup(tm, source, index, target_lang=TARGET_LANG) == (
        tm[key], "exact")
