"""侧栏信息架构：**上半部分 = 工作**，**下半部分 = 工具**。

产品决策（本文件守住的边界，取代了旧的「AI Engine 贴底 status module」）：

1. 低频的系统偏好不再与核心工作入口同级。侧栏一级导航只剩
   新建任务 / 项目 / 历史任务 / 术语与翻译记忆；`外观与语言` **不再是导航行**。
2. 主题 / 界面语言 / AI 模型 / 设置 收进贴底的 compact Utility Bar：四个等权、
   icon-only 的控件，每个 36px 点击区，各自有可访问名与 Tooltip。
3. 前三个是 **就地面板**（anchor 在各自的 icon 上），只有 ⚙ 是页面入口：
   - 侧栏**不再常驻**显示完整模型名与连接状态；
   - 完整模型名、连接状态、切换模型、测试连接只在 AI 面板里出现一次。
4. AI 的**连接状态**（颜色点）与**当前选中**（tint）是两件不同的事。
5. **Quick Control ≠ Full Configuration**：AI 面板里**没有**任何去设置页的入口
   （「管理 AI 引擎」已删除），⚙ 是完整设置页的唯一入口 —— 不允许两个入口做同一件事。
6. 四个字形来自同一套 Material Symbols（界面语言用 Globe，不再用「文」排版字形）。

运行：`python -m pytest tests/sidebar_ai_engine_footer_test.py -q`
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402

APP_PATH = ROOT / "app.py"
STYLES_PATH = ROOT / "folith" / "ui" / "styles.py"
MODEL_CENTER_VIEW = "settings"


@contextmanager
def sidebar_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="sidebar-ia-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP_PATH), default_timeout=90)


def _sidebar_labels(at):
    return [b.label for b in at.sidebar.button]


def _sidebar_markup(at):
    return "\n".join(str(m.value) for m in at.sidebar.markdown)


def _sidebar_button(at, key):
    return next((b for b in at.sidebar.button if b.key == key), None)


# ================= 结构契约（AST）=================


def _source_tree():
    return ast.parse(APP_PATH.read_text(encoding="utf-8"))


def _node_by_name(name):
    for node in ast.walk(_source_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"app.py 必须有 `{name}`")


def _sidebar_block():
    for node in ast.walk(_source_tree()):
        if isinstance(node, ast.With) and ast.unparse(node.items[0].context_expr) == "st.sidebar":
            return node
    raise AssertionError("app.py 必须有一个 `with st.sidebar:` 块")


def _button_labels(node):
    """给定子树里所有按钮的第一个位置参数（`st.button` / `col.button` 都算）。"""
    labels = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        if ast.unparse(call.func).endswith(".button") and call.args:
            label = call.args[0]
            if isinstance(label, ast.Call) and isinstance(label.func, ast.Name) and label.func.id == "_t":
                label = label.args[0]
            labels.append(ast.unparse(label).replace('"', "'"))
    return labels


def test_workspace_group_is_only_core_work_entries():
    """G1：「工作区」标题退休，一级导航只剩 历史任务 / 术语与翻译记忆。"""
    block = _sidebar_block()
    labels = _button_labels(block)
    assert "'历史任务'" in labels and "'术语与翻译记忆'" in labels, labels
    assert "'外观与语言'" not in labels, \
        f"「外观与语言」必须退出侧栏一级导航（它是 Utility，不是工作入口）：{labels}"
    assert "'工作区'" not in labels, \
        f"只剩两个入口时不该再人为加一个分组标题：{labels}"
    source = ast.unparse(block)
    assert 'tp-nav-label">工作区' not in source, \
        "「工作区」这个 section heading 必须退休（只剩两个入口，标题只是重复结构）"


def test_sidebar_renders_a_bottom_utility_bar_with_four_controls():
    """G2：贴底 Utility Bar 恰好四个控件，且每一个都自带可访问名与 Tooltip。"""
    cell = _node_by_name("_utility_cell")
    # 唯一一颗按钮：可访问名 = 第一个位置参数（真实文案），并且必须带 tooltip。
    buttons = [call for call in ast.walk(cell)
               if isinstance(call, ast.Call) and ast.unparse(call.func).endswith(".button")]
    assert len(buttons) == 1, [ast.unparse(call) for call in buttons]
    button = buttons[0]
    assert ast.unparse(button.args[0]) == "label", ast.unparse(button)
    keywords = {kw.arg for kw in button.keywords}
    assert "help" in keywords, "每个工具控件都必须有 Tooltip（icon-only 也要可发现）"
    assert "width" in keywords, "点击区必须铺满 cell"
    bar = _node_by_name("_sidebar_utility_bar")
    calls = [call for call in ast.walk(bar)
             if isinstance(call, ast.Call) and ast.unparse(call.func) == "_utility_cell"]
    assert len(calls) == 4, [ast.unparse(call) for call in calls]
    assert [ast.unparse(kw.value) for call in calls for kw in call.keywords
            if kw.arg == "name"] == ["'theme'", "'language'", "'ai'", "'settings'"]
    assert [ast.unparse(kw.value) for call in calls for kw in call.keywords
            if kw.arg == "glyph"] == ["'contrast'", "'language'", "'memory'", "'settings'"], \
        "四个字形必须来自同一套图标体系（界面语言用 Globe）"
    block = _sidebar_block()
    assert any(isinstance(n, ast.Call) and ast.unparse(n.func) == "_sidebar_utility_bar"
               for n in ast.walk(block)), "Utility Bar 必须由侧栏渲染"


def test_utility_panel_is_anchored_to_the_bar_not_floating():
    """G3：面板是 bottom-attached panel —— 紧贴 bar、按 icon 对齐、阴影克制。

    侧栏底部没有向下展开的空间，面板只能出现在 bar 之上；如果它飘在页面上方、离
    触发它的 icon 几十像素远，就会被读成"一张孤立卡片"。这里用 CSS 契约钉住三件事：
    ① 与 bar 的间距是 8px 级；② 左右按触发源缩进（Theme/Globe 起边、AI 终边）；
    ③ 只有一层轻阴影，没有 glow / 渐变 / 大圆角。
    """
    styles = STYLES_PATH.read_text(encoding="utf-8")
    panel = next(chunk for chunk in styles.split('\n\n')
                 if '[class*="st-key-utility_"][class*="_panel"] {' in chunk
                 and 'border-radius' in chunk)
    assert "var(--tp-utility-anchor-gap)" in styles, "面板与 bar 之间必须有受控的贴靠间距"
    assert "margin-bottom: var(--tp-utility-anchor-gap) !important" in styles
    assert "var(--tp-utility-anchor" in styles and "var(--tp-utility-panel-width" in styles, \
        "面板左右边缘必须按触发它的 icon 对齐，宽度按内容定死"
    # AI 走终边（auto margin 靠右），Theme / Globe 走起边（贴左）。
    assert ".st-key-sidebar_utility:has(.st-key-utility_ai_panel) {" in styles
    assert "--tp-utility-anchor: auto;" in styles, "AI 面板必须靠右对齐 AI icon"
    assert "--tp-utility-anchor: 6px;" in styles, "主题/语言面板必须靠左对齐 icon"
    for name in ("theme", "language", "ai"):
        assert f".st-key-sidebar_utility:has(.st-key-utility_{name}_panel)" in styles, name
    # 克制的浮层语言：轻阴影、无 glow/渐变。断言只看规则体，不看注释。
    body = panel.split('{', 1)[1].rsplit('}', 1)[0]
    assert "box-shadow: var(--tp-shadow-sm)" in body, body
    assert "gradient" not in body and "glow" not in body, body
    assert "border-radius: 11px" in body, body
    # 点击区与面板同为 36px 级的紧凑控件：模型下拉 30px、动作 30px。
    assert "min-height: 30px" in styles


def test_utility_tooltips_carry_state_and_labels_stay_out_of_the_bar():
    """G3b：四个 Tooltip 就是需求里那四条文案；Label 不进 bar。"""
    bar = _node_by_name("_sidebar_utility_bar")
    labels = sorted(ast.unparse(kw.value) for call in ast.walk(bar)
                    if isinstance(call, ast.Call) and ast.unparse(call.func) == "_utility_cell"
                    for kw in call.keywords if kw.arg == "label")
    assert labels == sorted([
        "_t('外观：{v0}', v0=theme_label)",
        "_t('界面语言：{v0}', v0=locale_label)",
        "_t('AI 模型：{v0}', v0=model_name) + ' · ' + _t(view['label'])",
        "_t('设置')",
    ]), labels
    # AI 的 Tooltip 额外带连接状态（icon 旁边的点只是提示，文案才是说明）。
    assert "' · '" in [label for label in labels if "AI 模型" in label][0]


def test_utility_bar_is_pinned_to_the_bottom():
    """G3c：bar 被压到侧栏底部（大面积留白是设计的一部分），面板在其正上方。"""
    styles = STYLES_PATH.read_text(encoding="utf-8")
    assert ".st-key-sidebar_utility {" in styles, "Utility Bar 的容器 key 必须保留"
    assert "margin-top: auto" in styles.split(".st-key-sidebar_utility {")[1].split("}")[0], \
        "Utility Bar 必须被压到侧栏底部"
    bar = ast.unparse(_node_by_name("_sidebar_utility_bar"))
    assert bar.index("_UTILITY_POPOVERS[open_panel]['panel']") < bar.index("'utility_bar'"), \
        "面板必须先于 bar 渲染（DOM 在上方，视觉上仍贴住 bar）"


def test_ai_panel_is_a_quick_control_with_no_settings_duplicate():
    """G4：AI 面板是 **Quick Control**，不含任何去设置页的重复入口。

    ⚙ 已经是完整设置页的唯一入口；面板里再放一个「管理 AI 引擎 →」就是同一件事
    的第二条入口。改个名字不算解决 —— 所以这里同时禁掉"指向设置页"和"叫管理"。
    """
    panel = _node_by_name("_utility_panel_ai")
    labels = _button_labels(panel)
    assert labels == ["'测试连接'"], \
        f"AI 面板只留「测试连接」这一个就地动作：{labels}"
    assert "'管理'" not in labels, "旧的「管理」行已经退休"
    assert not any("管理" in label for label in labels), \
        f"「管理 AI 引擎」这类跳转必须删除（换个名字也不行）：{labels}"
    source = ast.unparse(panel)
    assert "_open_provider_settings" not in source, \
        "AI 面板不得包含去设置页的跳转（那是 ⚙ 的职责）"
    assert "arrow_forward" not in source, "面板里不该再有「→ 去别处」的导航 affordance"
    # 侧栏本身**不得**渲染服务商 / 模型配置控件（那是设置页面的职责）。
    block = ast.unparse(_sidebar_block())
    for forbidden in ("provider_choice", "selectbox"):
        assert forbidden not in block, f"侧栏不该内嵌完整配置控件：{forbidden}"
    assert "manage_provider" not in APP_PATH.read_text(encoding="utf-8"), \
        "旧的贴底 status module 已退休，不该留下死 key"
    assert "utility_ai_manage" not in APP_PATH.read_text(encoding="utf-8"), \
        "重复入口退役后不该留下死 key"


def test_all_four_glyphs_come_from_one_icon_system():
    """G4b：四个字形同属一套 Material Symbols（界面语言不再是「文」排版字形）。"""
    bar = _node_by_name("_sidebar_utility_bar")
    glyphs = [ast.unparse(kw.value) for call in ast.walk(bar)
              if isinstance(call, ast.Call) and ast.unparse(call.func) == "_utility_cell"
              for kw in call.keywords if kw.arg == "glyph"]
    assert glyphs == ["'contrast'", "'language'", "'memory'", "'settings'"], glyphs
    # `language` 是 Material Symbols 的 Globe 字形（不是 emoji，也不是「文」）。
    assert "'文'" not in glyphs, "界面语言必须换成 Globe，不再用排版字形"
    cell = _node_by_name("_utility_cell")
    source = ast.unparse(cell)
    assert "material-symbols-rounded" in source, "字形必须走现有 icon system"
    assert "tp-utility-cjk" not in source, "排版字形这条特例已经退休"
    styles = STYLES_PATH.read_text(encoding="utf-8")
    assert ".tp-utility-cjk" not in styles, "排版字形的样式也应一并退休"


def test_connection_status_and_selected_state_are_separate_styles():
    """G5：状态点表达连接状态；"当前选中"用 tint 表达，两者互不替代。"""
    styles = STYLES_PATH.read_text(encoding="utf-8")
    assert ".tp-utility-dot.is-connected" in styles
    assert ".tp-utility-dot.is-unverified" in styles
    assert ".tp-utility-dot.is-error" in styles
    assert ".tp-utility-dot.is-unconfigured" in styles
    assert ":has(.tp-utility-open)" in styles, \
        "当前选中/面板展开必须有自己的视觉语法，不能借用状态色"


# ================= 运行时契约（AppTest）=================


def test_sidebar_keeps_full_model_name_out_of_the_default_view():
    """G6：默认侧栏只有一个 AI 字形 + 状态点；完整模型名只属于 AI 面板。"""
    with sidebar_env():
        at = _app()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        labels = _sidebar_labels(at)
        assert "外观：日间模式" in labels, labels
        assert "界面语言：简体中文" in labels, labels
        assert "设置" in labels, labels
        assert "外观与语言" not in labels, labels
        assert "工作区" not in labels, labels
        assert any(label.startswith("AI 模型：") for label in labels), labels

        markup = _sidebar_markup(at)
        assert "tp-utility-glyph" in markup and "tp-utility-dot" in markup, markup
        # 常驻区**没有**模型名与状态文案（它们只在 Popover 里出现）。
        assert "deepseek-flash" not in markup, \
            f"完整模型名不该常驻侧栏：{markup}"


def test_ai_popover_reveals_model_state_and_test_connection():
    """G7：AI 面板里才有完整模型名 / 连接状态 / 测试连接，且状态点跟随结果变化。"""
    with sidebar_env():
        at = _app()
        at.run()
        at.session_state["api_key_DeepSeek"] = "sk-sidebar-test"
        at.run()
        _sidebar_button(at, "utility_action_ai").click().run()
        assert not at.exception, [e.value for e in at.exception]

        markup = _sidebar_markup(at)
        assert "tp-utility-ai-model" in markup and "deepseek-flash" in markup, markup
        assert "尚未验证连接" in markup, markup
        assert "tp-utility-dot is-unverified" in markup, markup

        core.test_provider = lambda *args, **kwargs: (True, "响应「OK」· 耗时 0.4s")
        _sidebar_button(at, "utility_ai_test").click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["provider_connection_status"] == "connected"
        markup = _sidebar_markup(at)
        assert "tp-utility-dot is-connected" in markup, markup
        assert "tp-utility-feedback is-ok" in markup, markup


def test_ai_popover_switches_model_without_duplicating_configuration():
    """G8：切换模型复用 `model_choice_*`，并且把连接状态退回未验证。"""
    with sidebar_env():
        at = _app()
        at.run()
        at.session_state["provider_connection_status"] = "connected"
        at.run()
        _sidebar_button(at, "utility_action_ai").click().run()
        at.sidebar.selectbox(key="utility_model_choice_DeepSeek_deepseek-flash") \
            .set_value("deepseek-v4-pro").run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["model_choice_DeepSeek"] == "deepseek-v4-pro"
        assert at.session_state["provider_connection_status"] == "unverified", \
            "换模型后连接状态必须回到未验证"


def test_ai_panel_offers_no_route_to_settings():
    """G9：面板里不存在去设置页的按钮 —— 完整配置只能从 ⚙ 进入。"""
    with sidebar_env():
        at = _app()
        at.run()
        _sidebar_button(at, "utility_action_ai").click().run()
        assert not at.exception, [e.value for e in at.exception]
        panel_keys = [b.key for b in at.sidebar.button if b.key and b.key.startswith("utility_ai")]
        assert panel_keys == ["utility_ai_test"], panel_keys
        assert at.session_state["app_view"] != MODEL_CENTER_VIEW, \
            "打开 AI 面板不该顺带进入设置页"
        # ⚙ 仍然是唯一能进设置页的控件，并且它**直接**导航（没有中间菜单）。
        _sidebar_button(at, "utility_action_settings").click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == MODEL_CENTER_VIEW
        assert any(s.label == "服务商" for s in at.selectbox), "设置页必须渲染服务商选择"
        assert any(s.label == "模型" for s in at.selectbox), "设置页必须渲染模型选择"


def test_theme_popover_switches_and_persists_without_a_settings_trip():
    """G10：主题在 Popover 里就近切换，立即生效并写进 `ui_preferences.json`。"""
    with sidebar_env() as tmp:
        at = _app()
        at.run()
        assert at.session_state["ui_theme"] == "light"
        _sidebar_button(at, "utility_action_theme").click().run()
        assert not at.exception, [e.value for e in at.exception]
        at.sidebar.radio(key="utility_theme_choice_light").set_value("paper").run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["ui_theme"] == "paper"
        assert json.loads((tmp / "ui_preferences.json").read_text(encoding="utf-8")) == \
            {"theme": "paper", "locale": "zh-CN"}
        # Paper 的 token 必须立刻生效（不是只改了状态）。
        css = next(item.value for item in at.markdown if item.value.startswith("<style>"))
        assert "--bg-page:#f2eadb" in css, "纸面模式的语义 token 必须立即注入"
        assert "外观：纸面模式" in _sidebar_labels(at), _sidebar_labels(at)


def test_language_popover_switches_and_persists_without_a_settings_trip():
    """G11：界面语言同样就近切换，并复用自己的会话键与偏好文件。"""
    with sidebar_env() as tmp:
        at = _app()
        at.run()
        _sidebar_button(at, "utility_action_language").click().run()
        assert not at.exception, [e.value for e in at.exception]
        at.sidebar.radio(key="utility_language_choice_zh-CN").set_value("en").run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["ui_locale"] == "en"
        assert json.loads((tmp / "ui_preferences.json").read_text(encoding="utf-8")) == \
            {"theme": "light", "locale": "en"}
        assert "Interface language: English" in _sidebar_labels(at), _sidebar_labels(at)
        assert "Settings" in _sidebar_labels(at), _sidebar_labels(at)


def test_utility_popovers_are_mutually_exclusive_and_close_on_toggle():
    """G12：四个控件是同级：同时只开一个面板，再点一次收起。"""
    with sidebar_env():
        at = _app()
        at.run()
        _sidebar_button(at, "utility_action_theme").click().run()
        assert at.session_state["utility_theme_open"] is True
        _sidebar_button(at, "utility_action_language").click().run()
        assert at.session_state["utility_language_open"] is True
        assert at.session_state["utility_theme_open"] is False, \
            "打开语言面板必须收起主题面板（同级控件不是一个栈）"
        _sidebar_button(at, "utility_action_language").click().run()
        assert at.session_state["utility_language_open"] is False, "再点一次收起"


def test_gear_opens_settings_and_settings_owns_the_full_preference_entry():
    """G13：⚙ 进入「设置」；外观与语言的完整配置是设置页下的一行入口。"""
    with sidebar_env():
        at = _app()
        at.run()
        _sidebar_button(at, "utility_action_settings").click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == MODEL_CENTER_VIEW
        page = "\n".join(str(item.value) for item in at.markdown)
        assert "tp-settings-section-head" in page and "偏好" in page, page
        entry = next((b for b in at.button if b.key == "settings_appearance_entry"), None)
        assert entry is not None and entry.label == "外观与语言", \
            [b.label for b in at.button]
        entry.click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == "appearance"
        assert at.title[0].value == "外观与语言"
