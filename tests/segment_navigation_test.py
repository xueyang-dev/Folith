"""段落跳转的状态机回归。

这一组守的是「发现问题 → 点 #N → 正文定位 #N → 右栏显示 #N Inspector」这条闭环。

起因是一个真实 bug：点击问题锚点后正文变白并持续 loading。根因不是滚动没写好，
而是跳转链路本身没有收敛——每个入口各写各的（设置选中 + `st.rerun()`），
而 `st.pills` 的选中状态在 rerun 后依然保留，同一个值被反复返回、反复触发
`st.rerun()`，形成 rerun loop。

修法是四条约定，这里逐条守住：

1. 唯一入口 `_navigate_to_segment`，只设置状态；跳转挂在 `on_click` 回调上，
   回调在 rerun **之前**执行，脚本里不再手动 `st.rerun()`；
2. scroll intent 消费一次就清空，绝不在 session state 里残留；
3. 目标被筛选/搜索挡住时，先让筛选回到能显示它的状态；
4. UI 用按钮而不是 `st.pills`：pills 的选中值会在 rerun 后重放，是 loop 的来源。

JS 真滚动本身不好做单元测试，但状态机完全可以测——不允许无限 rerun 正是
这里要锁住的东西。
"""
from pathlib import Path

import core
from folith import assets

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
PENDING = "pending_scroll_segment_id"


def _state(count=6):
    state = core.new_job_state("technical-paper-part3.pdf")
    pairs = []
    for index in range(count):
        source = f"Segment {index + 1} about aerial view and drones."
        # 每段都不用术语表里的首选译名，保证每段都命中一条发现（锚点覆盖全文）
        target = f"第 {index + 1} 段译文（未使用项目术语）。"
        pairs.append({"source": source, "target": target,
                      "initial_target": target, "reviewed": index in {0, 1}})
    state.update(
        p1_done=True, p2_done=True,
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        glossary=[{"id": "term-aerial", "source": "aerial view",
                   "preferred": "鸟瞰视角", "target": "鸟瞰视角",
                   "status": "locked"}],
        translation_core_review_required=False,
        delivery_status="draft",
    )
    return state


def _open(tmp_path, job_id, *, section="translation"):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = section
    at.run()
    assert not at.exception, at.exception
    return at


def _click(at, key):
    """点一个按钮并重渲染。

    跳转都挂在 `on_click` 回调上，所以这是真实路径——也正是要守住的那条链路。
    """
    next(button for button in at.button if button.key == key).click()
    at.run()
    assert not at.exception, at.exception
    return at


def _click_anchor(at, job_id, segment):
    """点问题抽屉里指向 `segment` 的锚点。"""
    key = next(str(button.key) for button in at.button
               if str(button.key).startswith(f"issue_anchor_{job_id}_")
               and str(button.key).endswith(f"_{segment}"))
    next(button for button in at.button if button.key == key).click()
    at.run()
    assert not at.exception, at.exception
    return at


def _markdown(at):
    return "\n".join(str(item.value) for item in at.markdown)


def _active_segment(at):
    """从渲染出的 active 标记里读出目标段号。"""
    for value in (str(item.value) for item in at.markdown):
        if "is-active" in value and "data-segment=" in value:
            return int(value.split('data-segment="')[1].split('"')[0])
    return None


def test_grid_selection_sets_and_consumes_scroll_once(tmp_path, monkeypatch):
    """点段号：选中更新、scroll intent 消费一次后被清空，绝不留存。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate01"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)

    _click(at, f"cat_sel_{job_id}_3")
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)
    assert PENDING not in at.session_state, \
        "scroll intent 消费后必须清空，否则之后每次 rerun 都会再跳一次"
    assert _active_segment(at) == 3, "目标行必须成为 active"

    # 再渲染两次确认没有"复活"
    at.run()
    at.run()
    assert PENDING not in at.session_state
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)


def test_source_cell_selection_activates_the_work_row(tmp_path, monkeypatch):
    """点击原文单元格也必须切换当前工作行。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navsource01"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)

    _click(at, f"cat_source_activate_{job_id}_3")
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)
    assert _active_segment(at) == 3


def test_repeated_navigation_converges(tmp_path, monkeypatch):
    """连续跳转多个段落都必须收敛，不产生 rerun 堆积。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate02"
    core.save_job_state(job_id, _state(count=12))
    at = _open(tmp_path, job_id)

    for target in (1, 8, 11, 0, 5):
        _click(at, f"cat_sel_{job_id}_{target}")
        assert PENDING not in at.session_state, f"#{target + 1} 的 scroll intent 未清空"
        assert at.session_state["selected_segment_id"] == \
            assets.segment_id(job_id, target)
        assert _active_segment(at) == target, f"#{target + 1} 必须是 active"
        # Preview rows retain their editors/selection targets, but only the
        # active work row exposes operations (including overflow actions).
        edit_buttons = [button.key for button in at.button
                        if str(button.key).startswith("cat_action_source_")]
        assert edit_buttons == [f"cat_action_source_{job_id}_{target}"]


def test_tail_segment_is_reachable(tmp_path, monkeypatch):
    """#82 这种尾部段落也必须能定位，而不是只有首屏附近的段落能工作。

    发现是**聚合**的（一条发现带一大串段落），抽屉里只给前几个做锚点，
    所以尾段依赖"搜索收敛 + 段号按钮"这条路径——它同样必须收敛、不残留。
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate03"
    core.save_job_state(job_id, _state(count=82))
    at = _open(tmp_path, job_id)

    # 搜索把 #82 收进渲染窗口，段号按钮才会存在
    at.session_state[f"translation_search_{job_id}"] = "第 82 段"
    at.run()
    assert not at.exception, at.exception
    _click(at, f"cat_sel_{job_id}_81")

    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 81)
    assert PENDING not in at.session_state, "scroll intent 必须已被消费"
    assert _active_segment(at) == 81, "#82 必须在渲染集合内且成为 active"
    # 再渲染两次确认没有复活
    at.run()
    at.run()
    assert PENDING not in at.session_state


def test_issue_anchor_targets_aggregated_segments(tmp_path, monkeypatch):
    """聚合发现里的段落锚点：点第一个命中段，抽屉关闭、该段 active。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate11"
    core.save_job_state(job_id, _state(count=30))
    at = _open(tmp_path, job_id)

    _click(at, f"workspace_issues_{job_id}")
    assert at.session_state["issues_panel_open"] is True
    _click_anchor(at, job_id, 5)

    assert "issues_panel_open" not in at.session_state
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 5)
    assert PENDING not in at.session_state
    assert _active_segment(at) == 5


def test_hidden_target_reveals_filters_before_rendering(tmp_path, monkeypatch):
    """目标被筛选挡住时，必须先放宽筛选再渲染——否则目标根本不在 DOM 里。

    原来的实现表现为"滚动失败"，但真实情况是目标不存在。
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate04"
    core.save_job_state(job_id, _state(count=20))
    at = _open(tmp_path, job_id)

    # 用户处在「已审校」筛选下，而锚点指向的 #6 是未审校段落
    at.session_state[f"translation_filter_{job_id}"] = "已审校"
    at.run()
    assert not at.exception, at.exception

    _click(at, f"workspace_issues_{job_id}")
    _click_anchor(at, job_id, 5)

    assert at.session_state[f"translation_filter_{job_id}"] == "全部", \
        "跳转到被筛选隐藏的段落时必须把筛选放宽到「全部」"
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 5)
    assert _active_segment(at) == 5, "放宽筛选后目标段落必须真的进入渲染集合"


def test_hidden_target_clears_search_and_tells_the_user(tmp_path, monkeypatch):
    """搜索把目标挡住时要清空搜索，并明确告知用户改动了什么。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate05"
    core.save_job_state(job_id, _state(count=20))
    at = _open(tmp_path, job_id)

    at.session_state[f"translation_search_{job_id}"] = "第 1 段"
    at.run()
    _click(at, f"workspace_issues_{job_id}")
    _click_anchor(at, job_id, 5)

    assert at.session_state[f"translation_search_{job_id}"] == ""
    captions = " ".join(str(item.value) for item in at.caption)
    assert "已清除搜索" in captions, "静默改掉用户的筛选状态比多一行提示糟糕得多"
    assert _active_segment(at) == 5


def test_navigation_marks_whole_row_active(tmp_path, monkeypatch):
    """跳转后 active 感知必须是整行，不能只靠段号。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate06"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)

    _click(at, f"cat_sel_{job_id}_4")
    marker = next((value for value in (str(item.value) for item in at.markdown)
                   if 'data-segment="4"' in value), None)
    assert marker is not None
    assert "is-active" in marker, "目标行必须带 is-active（整行 tint + 3px 蓝条）"


def test_issues_panel_replaces_popover(tmp_path, monkeypatch):
    """「查看全部」切的是右栏 Issues 模式，不是覆盖正文的浮层。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate07"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)

    assert not any(str(button.key).startswith("agent_jump_") for button in at.button), \
        "不应再有旧的 pills 跳转控件（它的选中值会在 rerun 后重放）"

    _click(at, f"workspace_issues_{job_id}")
    assert at.session_state["issues_panel_open"] is True
    page = _markdown(at)
    assert "tp-issues-summary" in page, "Issues 面板必须显示分级计数"
    assert 'class="tp-cat-head"' in page, "打开问题列表不得遮挡/移除正文"


def test_issue_anchor_closes_panel_and_selects_segment(tmp_path, monkeypatch):
    """点问题里的 #N：关闭抽屉 + 选中该段 + 一次性滚动。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate08"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)

    _click(at, f"workspace_issues_{job_id}")
    assert at.session_state["issues_panel_open"] is True
    _click_anchor(at, job_id, 0)

    assert "issues_panel_open" not in at.session_state, "点锚点后必须回到段落 Inspector"
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)
    assert PENDING not in at.session_state, "scroll intent 必须已被消费"
    assert _active_segment(at) == 0


def test_close_button_leaves_issues_panel(tmp_path, monkeypatch):
    """抽屉里的「返回段落」也要能退出 Issues 模式，且不改变选中段落。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate10"
    core.save_job_state(job_id, _state())
    at = _open(tmp_path, job_id)
    before = at.session_state["selected_segment_id"]

    _click(at, f"workspace_issues_{job_id}")
    assert at.session_state["issues_panel_open"] is True
    _click(at, f"issues_close_{job_id}")
    assert "issues_panel_open" not in at.session_state
    assert at.session_state["selected_segment_id"] == before
    assert PENDING not in at.session_state, "切视图不该留下任何跳转意图"


def test_no_stale_pending_scroll_after_refresh(tmp_path, monkeypatch):
    """刷新后不能残留旧的 pending scroll（它只存在于 session state）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "navstate09"
    core.save_job_state(job_id, _state())
    first = _open(tmp_path, job_id)
    _click(first, f"cat_sel_{job_id}_2")
    assert PENDING not in first.session_state

    refreshed = _open(tmp_path, job_id)
    assert PENDING not in refreshed.session_state
    assert not refreshed.exception
