"""侧栏 AI Engine status module：重复导航收敛的回归测试。

产品决策（本文件守住的边界）：

1. 「工作区」分组只放**跨项目的资料**：历史任务 / 术语与翻译记忆。
   独立「⚙ 设置」行已退休 —— 当前产品没有独立的 General Settings 信息架构，
   它当时唯一的落点就是 AI Engine / Model Center，与贴底 status module 上的
   「管理」完全同义。同一个页面在侧栏出现两个入口，正是要消除的重复导航。
2. AI Engine 区是 **runtime status module**，不是「工作区」分组里的第四行导航：

       ○ AI引擎                        管理
       deepseek-v4-flash-0731
       尚未验证连接

   - 「AI引擎」是 status label（不是可点击的导航项）
   - 「管理」是**唯一**的 action，进入 AI Engine / Model Center
   - 模型名是 secondary text，连接状态是 tertiary/status text
   - 整块**不是** clickable card，也不套用 `.st-key-library_nav` 的导航行样式
3. 侧栏里**只能有一个**指向 Model Center 的入口（`manage_provider`）。
   Model Center 自身的 route（`app_view == "settings"`）与功能不变 —— 主工作区里
   那些「前往设置 / 配置 API Key」的就地按钮仍然用它。

运行：`python -m pytest tests/sidebar_ai_engine_footer_test.py -q`
"""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402

APP_PATH = ROOT / "app.py"

MODEL_CENTER_VIEW = "settings"


@contextmanager
def sidebar_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="sidebar-ai-engine-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _sidebar_labels(at):
    return [b.label for b in at.sidebar.button]


def _sidebar_markup(at):
    return "\n".join(str(m.value) for m in at.sidebar.markdown)


def _sidebar_button(at, label):
    return next((b for b in at.sidebar.button if b.label == label), None)


# ================= 结构契约（AST）=================


def _sidebar_block():
    """`with st.sidebar:` 块 —— 侧栏的重复入口只能从结构上守。"""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and ast.unparse(node.items[0].context_expr) == "st.sidebar":
            return node
    raise AssertionError("app.py 必须有一个 `with st.sidebar:` 块")


def _settings_assignments(node):
    """把 `app_view` 设成 Model Center 的赋值语句（在给定子树里）。"""
    found = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Assign) or len(child.targets) != 1:
            continue
        target = ast.unparse(child.targets[0])
        value = ast.unparse(child.value).replace('"', "'")
        if target == "st.session_state.app_view" and value == "'settings'":
            found.append(child)
    return found


def _button_labels(node):
    """侧栏块里所有按钮的第一个位置参数（`st.button` / `col.button` 都算）。"""
    labels = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        if ast.unparse(call.func).endswith(".button") and call.args:
            label = call.args[0]
            if isinstance(label, ast.Call) and isinstance(label.func, ast.Name) and label.func.id == "_t":
                label = label.args[0]
            labels.append(ast.unparse(label).replace('"', "'"))
    return labels


def test_sidebar_source_has_no_standalone_settings_nav_row():
    """G1：侧栏源码里不再渲染「设置」行；AI Engine 的 action 仍叫「管理」。"""
    block = _sidebar_block()
    labels = _button_labels(block)
    assert "'设置'" not in labels, \
        f"独立「设置」导航行必须退休（它只是 Model Center 的别名）：{labels}"
    assert "'管理'" in labels, f"AI Engine status module 必须保留「管理」action：{labels}"
    assert "'历史任务'" in labels and "'术语与翻译记忆'" in labels, labels


def test_manage_action_has_no_tooltip_wrapper():
    """「管理」**不能**带 `help=`。

    带 tooltip 的按钮会被 Streamlit 包进 `stTooltipHoverTarget`，`button` 就不再是
    `.stButton` 的直接子元素；而把「管理」压成右对齐文字 action 的那条规则
    （`.st-key-provider_status .stButton > button`）用的是**直接子选择器** —— 一旦
    加上 tooltip 它会静默落空，整块 status module 退回 Streamlit 默认的描边按钮。
    要么保持无 tooltip，要么把那条 CSS 改成 `.stButton button` 后代写法。
    """
    block = _sidebar_block()
    manage = [call for call in (n for n in ast.walk(block) if isinstance(n, ast.Call))
              if ast.unparse(call.func).endswith(".button") and call.args
              and "管理" in ast.unparse(call.args[0])]
    assert len(manage) == 1, [ast.unparse(c) for c in manage]
    assert not any(kw.arg == "help" for kw in manage[0].keywords), \
        "「管理」带 tooltip 会让 `.stButton > button` 直接子选择器静默失效"
    css = APP_PATH.read_text(encoding="utf-8")
    styles_file = ROOT / "folith" / "ui" / "styles.py"
    if styles_file.is_file():
        css += "\n" + styles_file.read_text(encoding="utf-8")
    assert ".st-key-provider_status .stButton > button {" in css, \
        "这条直接子选择器仍然存在，所以上面的约束是必需的"


def test_sidebar_has_exactly_one_entry_into_the_model_center():
    """G7：侧栏只能有一个指向 Model Center 的入口，且它归「管理」所有。"""
    block = _sidebar_block()
    assignments = _settings_assignments(block)
    assert len(assignments) == 1, \
        f"侧栏里指向 Model Center 的入口必须恰好一个，实际 {len(assignments)} 个"

    guard = next((node for node in ast.walk(block)
                  if isinstance(node, ast.If)
                  and "manage_provider" in ast.unparse(node.test)), None)
    assert guard is not None, "「管理」按钮的点击分支必须存在"
    guarded = _settings_assignments(guard)
    assert len(guarded) == 1, "「管理」必须且只能设置一次 Model Center 路由"
    assert guarded[0] is assignments[0] or \
        guard.lineno <= assignments[0].lineno <= (guard.end_lineno or guard.lineno), \
        "唯一的路由赋值必须落在「管理」的分支里，不能被别的入口偷偷复用"


# ================= 运行时契约（AppTest）=================


def test_ai_engine_footer_still_renders_as_a_status_module():
    """G2：贴底 status module 仍在，并且不是导航行样式。"""
    with sidebar_env():
        at = _app()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        markup = _sidebar_markup(at)
        assert "tp-provider" in markup and "AI引擎" in markup, \
            "AI Engine status module 必须仍在侧栏渲染"
        assert "tp-engine-detail" in markup, "模型名与连接状态必须仍在模块内"

        # 它必须是 status module，而不是「工作区」分组的第四行导航：
        # 标题用 status label 的语法，而不是 `.tp-nav-label` 那种分组标题。
        assert "AI引擎" not in _sidebar_labels(at), \
            "「AI引擎」是 status label，不能变成可点击的导航项"
        assert 'tp-nav-label">AI引擎' not in markup, \
            "AI Engine 区不能伪装成「工作区」分组里的一个分组标题"
        source = APP_PATH.read_text(encoding="utf-8")
        styles_file = ROOT / "folith" / "ui" / "styles.py"
        if styles_file.is_file():
            source += "\n" + styles_file.read_text(encoding="utf-8")
        assert 'st-key-provider_status' in source, "status module 的容器 key 必须保留"
        assert '.st-key-library_nav .stButton > button' in source, \
            "「工作区」导航行样式仍服务于 历史任务 / 术语与翻译记忆"


def test_manage_action_still_opens_the_model_center():
    """G3：点击「管理」进入现有 AI Engine / Model Center 页面。"""
    with sidebar_env():
        at = _app()
        at.run()
        manage = _sidebar_button(at, "管理")
        assert manage is not None, _sidebar_labels(at)
        manage.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == MODEL_CENTER_VIEW, \
            "「管理」必须进入 AI Engine / Model Center"
        assert any(s.label == "服务商" for s in at.selectbox), \
            "Model Center 必须渲染服务商选择"
        assert any(s.label == "模型" for s in at.selectbox), "Model Center 必须渲染模型选择"


def test_model_center_route_is_unaffected_by_the_removed_row():
    """G4：删掉「设置」行不影响 Model Center 自身的 route。"""
    with sidebar_env():
        at = _app()
        at.run()
        at.session_state["app_view"] = MODEL_CENTER_VIEW
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert any(s.label == "服务商" for s in at.selectbox), \
            "直接落到 Model Center 路由仍应渲染配置页"

        # 主工作区里的就地入口（新建任务第 4 步的「前往设置」）也仍然工作。
        at2 = _app()
        at2.run()
        assert any(b.label == "前往设置" for b in at2.button), \
            "工作流内的 AI 配置入口不应被这次收敛影响"


def test_current_model_is_shown_in_the_footer():
    """G5：AI Engine 当前模型仍正确显示。"""
    with sidebar_env():
        at = _app()
        at.run()
        assert "deepseek-v4-flash" in _sidebar_markup(at), \
            "侧栏应显示当前默认模型"

        at.session_state["model_choice_DeepSeek"] = "deepseek-v4-pro"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        markup = _sidebar_markup(at)
        assert "deepseek-v4-pro" in markup, markup
        assert "deepseek-v4-flash" not in markup, "切换模型后侧栏不得残留旧模型名"
        assert "tp-engine-model" in markup, "模型名必须带 secondary 语义 class"


def test_connection_state_is_shown_in_the_footer():
    """G6：connection state 仍正确显示，并带有 status 语义。"""
    with sidebar_env():
        at = _app()
        at.run()
        assert "API 凭据未配置" in _sidebar_markup(at), \
            "未配置凭据时应显示凭据缺失状态"

        at.session_state["api_key_DeepSeek"] = "sk-sidebar-test"
        at.session_state["provider_connection_status"] = "unverified"
        at.run()
        markup = _sidebar_markup(at)
        assert "尚未验证连接" in markup, markup
        assert "tp-engine-state" in markup and "is-unverified" in markup, markup

        at.session_state["provider_connection_status"] = "connected"
        at.run()
        markup = _sidebar_markup(at)
        assert "连接正常" in markup, markup
        assert "is-connected" in markup, markup

        at.session_state["provider_connection_status"] = "error"
        at.run()
        markup = _sidebar_markup(at)
        assert "连接失败" in markup, markup
        assert "is-error" in markup, markup
