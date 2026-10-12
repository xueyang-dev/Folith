"""任务详情页与文档画像：开始翻译后仍看得到当时的设置；画像可以改。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.qt_inline_editor_test import _pump, bench  # noqa: F401,E402  (fixture)


def test_profile_summary_and_save(bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    assert adapter.profile_summary({}) == ""
    assert adapter.profile_summary({"domain": "文学", "genre": "小说", "audience": ""}) \
        == "文学 · 小说"
    saved = adapter.save_document_profile(view.job_id, {
        "domain": "文学", "genre": "长篇小说", "audience": "大众读者", "register": "叙事"})
    assert saved["genre"] == "长篇小说"
    state = adapter.load_state(view.job_id)
    assert state["document_profile"]["audience"] == "大众读者"
    assert state["profile_done"] is True


def test_task_details_lists_the_settings_in_use(bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    groups = adapter.task_details(view.config, view.job_id)
    titles = [group["title"] for group in groups]
    assert titles[0] == "文档" and "翻译设置" in titles and "交付内容" in titles
    document = dict(groups[0]["rows"])
    assert document["文件"] == "book.docx"
    assert document["目标语言"] == "简体中文"
    assert "3 段" in document["翻译单元"]
    # 没有值的行不写。
    for group in groups:
        assert all(value for _label, value in group["rows"])


def test_details_page_is_reachable_and_edits_the_profile(bench):  # noqa: F811
    view, app = bench
    from folith.qt import adapter

    adapter.save_document_profile(view.job_id, {"domain": "文学", "genre": "小说"})
    state = adapter.load_state(view.job_id)
    view.apply(view.job_id, state, adapter.segment_rows(view.job_id, state), {}, [])
    view.show_page("details")
    _pump(app)
    assert view.current_page_id() == "details"
    assert "文学 · 小说" in view.details_panel.profile_label.text()
    assert any(text.startswith("文件：") for text in view.details_panel.texts())
    asked = []
    view.profileEditRequested.connect(lambda: asked.append(True))
    view.details_panel.profile_button.click()
    assert asked == [True]


def test_details_are_not_rebuilt_while_the_page_is_closed(bench, monkeypatch):  # noqa: F811
    """翻译运行时状态每批都会换：任务详情不在眼前就不重算，也不为同一份状态重新读盘。"""
    view, app = bench
    from folith.qt import adapter

    calls: list[int] = []
    real = adapter.task_details
    monkeypatch.setattr(adapter, "task_details",
                        lambda *args, **kwargs: calls.append(1) or real(*args, **kwargs))
    view.show_page("segments")
    for _ in range(3):
        state = dict(adapter.load_state(view.job_id))
        view.apply(view.job_id, state, adapter.segment_rows(view.job_id, state), {}, [])
    assert calls == []

    reads: list[str] = []
    real_load = adapter.load_state
    monkeypatch.setattr(adapter, "load_state",
                        lambda job_id: reads.append(job_id) or real_load(job_id))
    view.show_page("details")
    _pump(app)
    assert calls == [1]
    assert reads == []          # 用的是工作台手里那份状态
    assert any(text.startswith("文件：") for text in view.details_panel.texts())
