"""译文保存机制的界面回归：真实输入、防丢稿、连续编辑、Agent 失败隔离。

为什么这批断言必须走界面而不只是数据层：上一轮的结论是"段落编辑已接入"，
但保存按钮是否**真的会出现在用户面前**，取决于输入框的值能否在提交前到达服务端。
旧实现把输入框放进 `st.form`，测试直接改 `session_state` 就绕过了真实打字路径，
于是"按钮永远不出现"这种问题不会被任何断言抓住。

这里因此刻意用 `at.text_area(...).set_value(...)`（把新值交给客户端再跑一轮，
等价于用户失焦/⌘+Enter）而不是直接写 `session_state`，并守住：

- 输入后有保存入口与成功反馈；
- 切筛选/切段落回来，未保存内容仍在输入框里，**保存写的是用户敲的内容**；
- 连续审校：保存并进入下一段；
- 合并确认框勾选后按钮必须解锁（此前确认框在 `st.form` 里，勾了也解不开）；
- Agent 失败/空结果永远不提供"应用到译文"；
- 草稿落在任务目录里，新会话（刷新/关标签页）能恢复回来，且恢复本身不写文档；
- 交付/冻结之前必须先处理未保存草稿，不能让用户导出修改前的版本；
- 保存时若磁盘上的译文已被别处改过，必须问，不许静默覆盖。

**不在这里守的东西**：焦点回位、3s 轮询下的输入安全、滚动定位。它们是浏览器
行为，`AppTest` 拿不到 `document.activeElement`，也没有真实的计时器与网络往返
（`st.html(unsafe_allow_javascript=True)` 的内容在 `AppTest` 的元素树里根本不出现）。
把它们写成 `AppTest` 断言只会得到"看起来测过了"的假绿——那正是上一轮被指出的问题。
这四项由 `docs/ui-audit/25-save-flow/runtime_edit_audit.js` 用真实 Chrome 验证。
"""
import json
from pathlib import Path

import core

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _ui_state(count=4):
    state = core.new_job_state("save-flow.pdf")
    pairs = []
    for index in range(count):
        pairs.append({
            "source": f"Source segment {index + 1}",
            "target": f"译文 {index + 1}",
            "initial_target": f"译文 {index + 1}",
            # 前两段已审校：用来做"筛掉某一段"的筛选切换。
            "reviewed": index in {0, 1},
            "from_tm": False,
            "glossary_entry_ids": [],
        })
    state.update(
        p1_done=True, p2_done=True,
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        review_stats={"reviewed_segments": 2},
        delivery_status="draft",
    )
    return state


def _open_workspace(job_id, tmp_path, monkeypatch, state=None):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_job_state(job_id, state if state is not None else _ui_state())
    at = AppTest.from_file(str(APP_PATH), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception
    return at


def _drafts(at):
    # AppTest 的 session_state 不支持 `.get`（缺 key 会抛 KeyError）。
    try:
        return dict(at.session_state["translation_edit_drafts"] or {})
    except KeyError:
        return {}


def _text_area(at, key):
    """按段身份找译文输入框。

    必须容忍 key 尾部带一个"重挂载序号"（`translation_editor_<段身份>#3`）：
    服务端要强制前端**重建**输入框时必须换 key（清 session_state 对已经 dirty 的
    前端组件无效），见 `app.py: _reset_translation_editor`。断言关心的是"用户看到
    哪个框、里面是什么"，不是那个序号的数值。
    """
    area = next((item for item in at.text_area if item.key == key), None)
    if area is not None:
        return area
    return next((item for item in at.text_area
                 if str(item.key).startswith(f"{key}#")), None)


def _editor_keys(at, base):
    """这个段身份下所有用过的输入框 key（含各代序号）。"""
    return [str(item.key) for item in at.text_area
            if str(item.key) == base or str(item.key).startswith(f"{base}#")]


def _button(at, key):
    return next((button for button in at.button if button.key == key), None)


def _open_inspector(at, job_id):
    if at.session_state["workspace_inspector_open"]:
        return at
    button = _button(at, f"workspace_inspector_toggle_{job_id}")
    assert button is not None
    button.click()
    at.run()
    assert not at.exception, at.exception
    return at


def _expander_labels(at):
    """展开器的"标签"不是 markdown，必须从 `at.expander` 读。"""
    return [str(item.label) for item in at.expander]


def _markdown(at):
    return "\n".join(str(item.value) for item in at.markdown)


def _page_text(at):
    """把页面上"能读到的字"合起来：markdown 之外还有 code / caption / 各类提示。"""
    parts = [_markdown(at)]
    for group in (at.code, at.caption, at.error, at.warning, at.info, at.success):
        parts.extend(str(item.value) for item in group)
    return "\n".join(parts)


# ------------------------------------------------------- 真实输入 → 保存入口


# ------------------------------------------------------- 合并确认框可解锁


# ------------------------------------------------------- 排除入口在工作台可见


# ------------------------------------------------------- Agent 候选与失败隔离


# ------------------------------------------------------- 连续编辑的快捷动作


# ------------------------------------------------------- 拆分后编辑框不串段


_APP_MODULE = None


def _app_module():
    """把 app.py 当模块加载一次，用于直接验证纯逻辑（不经界面）。"""
    global _APP_MODULE
    if _APP_MODULE is None:
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("folio_app_under_test",
                                                      str(APP_PATH))
        module = importlib.util.module_from_spec(spec)
        sys.modules["folio_app_under_test"] = module
        spec.loader.exec_module(module)
        _APP_MODULE = module
    return _APP_MODULE


# ------------------------------------------- 草稿持久化（跨刷新/关标签页不丢稿）

def test_draft_file_round_trip_and_removal(tmp_path, monkeypatch):
    """数据层契约：草稿是**独立文件**，没有草稿时不留空文件，损坏时按"没有"处理。

    草稿必须和 `state.json` 分开：它是"还没被确认过的输入"，不是文档内容。
    混进去会让审校、交付资产、计数全部读到未经确认的文本。
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "saveflow0022"
    core.save_job_state(job_id, _ui_state())

    assert core.load_translation_drafts(job_id) == {}, "初始没有草稿"
    core.save_translation_drafts(
        job_id, {"seg-a": {"text": "草稿 A", "baseline": "旧 A"}})
    path = core.translation_drafts_path(job_id)
    assert path.is_file() and path.name == "translation_drafts.json", path
    assert core.load_translation_drafts(job_id)["seg-a"]["text"] == "草稿 A"
    # 草稿没有污染文档状态。
    document = json.dumps(core.load_job_state(job_id), ensure_ascii=False)
    assert "草稿 A" not in document
    # 审校结论也没有被顺手改掉。
    assert core.load_job_state(job_id)["review_stats"]["reviewed_segments"] == 2

    core.save_translation_drafts(job_id, {})
    assert not path.is_file(), "没有草稿时不该留一个空文件"

    # 损坏文件按"没有草稿"处理：读草稿失败不能连带把整个工作台打不开。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert core.load_translation_drafts(job_id) == {}

    core.save_translation_drafts(job_id, {"seg-b": {"text": "x", "baseline": "y"}})
    core.clear_translation_drafts(job_id)
    assert not path.is_file()


# ------------------------------------------------- 交付前必须先处理未保存草稿


# ------------------------------------------------- 覆盖冲突：问，而不是静默覆盖

def _open_conflict(at, job_id, segment_id, mine="我的新译文", theirs="别人写的译文"):
    """制造"另一个标签页先写过同一段"的现场，并点保存触发冲突。"""
    _text_area(at, f"translation_editor_{segment_id}").set_value(mine)
    at.run()
    assert f"{job_id}|{segment_id}" in _drafts(at)

    other = core.load_job_state(job_id)
    other["pairs"][2]["target"] = theirs
    core.save_job_state(job_id, other)

    _button(at, f"cat_save_btn_{job_id}_2").click()
    at.run()
    assert not at.exception, at.exception
    return mine, theirs


# ---------------- 服务端换内容时必须换 key（强迫前端重挂载） ----------------

