#!/usr/bin/env python3
"""F1 取证：四种界面语言下主窗口 / 首页的最小宽度（minimumSizeHint）。

报告（``tmp/plan/plan.txt`` 的 F1）称首页最小宽度是「中 1238 / 英 1348 / 西 1415 /
俄 1497」，但没有定位是哪个控件把宽度撑起来。这个脚本用离屏渲染复现，并逐层定位。

    venv/bin/python scripts/ui_overhaul_measure_widths.py
    venv/bin/python scripts/ui_overhaul_measure_widths.py --lang zh-CN     # 只跑一门语言
    venv/bin/python scripts/ui_overhaul_measure_widths.py --detail          # 附组件级明细
    venv/bin/python scripts/ui_overhaul_measure_widths.py --json /tmp/f1.json

实现要点：

* 每种语言独立起一个子进程：``FOLITH_UI_LANGUAGE`` 在 ``MainWindow`` 建视图时读取，
  同进程换语言要重建整窗口，一语言一进程更干净（与 ``qt_showcase_screenshots.py`` 一致）。
* 演示数据复用 ``scripts/qt_showcase_screenshots.py`` 的 ``seed()``，量与报告用的是同一份
  合成任务（含一条阻断性 finding）。
* 偏好目录用 ``FOLITH_QT_SETTINGS_DIR`` 指到临时目录，不读不写本机 QSettings。
* 两层证据：**结构值**（控件/布局的 ``minimumSizeHint()``）和**消融值**（隐藏某控件后
  首页最小宽度减少多少）。结构值大不等于撑宽（可以被 stretch 吸收），所以两份都看。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

#: 报告与截图脚本覆盖的四门界面语言。
LANGUAGES = ("zh-CN", "en", "es", "ru")
#: 测量用的窗口尺寸（逻辑像素）；最小宽度与窗口大小无关，固定一个值便于复现。
WINDOW_SIZE = (1440, 900)


# --------------------------------------------------------------------------
# 子进程：建窗口、量宽度
# --------------------------------------------------------------------------
def _configure_env(lang: str, scale: float, settings_dir: Path) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["FOLITH_UI_LANGUAGE"] = lang
    # 偏好隔离：不读不写本机 com.folith.QtDesktop。
    os.environ["FOLITH_QT_SETTINGS_DIR"] = str(settings_dir)
    if abs(scale - 1.0) > 1e-6:
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    else:
        os.environ.pop("QT_SCALE_FACTOR", None)


def _profile(widget) -> dict:
    minimum = widget.minimumSizeHint()
    hint = widget.sizeHint()
    return {
        "min_w": minimum.width(), "min_h": minimum.height(),
        "hint_w": hint.width(), "hint_h": hint.height(),
        "min_width": widget.minimumWidth(),
        "max_width": widget.maximumWidth(),
        "visible": bool(widget.isVisibleTo(widget.window())),
    }


def _tag(widget) -> str:
    """控件的一行标识：类名#objectName（跨语言比对按这个对）。"""
    name = widget.objectName() or ""
    cls = type(widget).__name__
    return f"{cls}#{name}" if name else cls


def _text(widget) -> str:
    getter = getattr(widget, "text", None)
    if not callable(getter):
        return ""
    try:
        return " ".join(str(getter()).split())[:40]
    except Exception:  # noqa: BLE001 - 取证脚本不因某个控件取不到文字而失败
        return ""


def _pump(app, seconds: float = 0.5) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _layout_tree(node, depth: int = 0, rows: list | None = None,
                 max_depth: int = 5) -> list[dict]:
    """把布局树摊平成「缩进 + 最小宽度」的行，用于说明宽度是一层层加出来的。"""
    from PySide6.QtWidgets import QLayout

    rows = rows if rows is not None else []
    if depth > max_depth:
        return rows
    if isinstance(node, QLayout):
        label, kind, minimum = f"<{type(node).__name__}>", type(node).__name__, \
            node.minimumSize().width()
        rows.append({"depth": depth, "label": label, "kind": kind, "min_w": minimum,
                     "rows": node.count()})
        for index in range(node.count()):
            item = node.itemAt(index)
            if item is None:
                continue
            sub = item.layout()
            if sub is not None:
                _layout_tree(sub, depth + 1, rows, max_depth)
                continue
            widget = item.widget()
            if widget is not None:
                rows.append({"depth": depth + 1, "label": _tag(widget), "kind": "widget",
                             "min_w": item.minimumSize().width(),
                             "text": _text(widget)})
                sub_layout = widget.layout()
                if sub_layout is not None and sub_layout.count() and depth + 2 <= max_depth:
                    _layout_tree(sub_layout, depth + 2, rows, max_depth)
        return rows
    widget = node
    rows.append({"depth": depth, "label": _tag(widget), "kind": "widget",
                 "min_w": widget.minimumSizeHint().width(), "text": _text(widget)})
    layout = widget.layout()
    if layout is not None:
        _layout_tree(layout, depth + 1, rows, max_depth)
    return rows


def _ablate(app, target, candidates: list[tuple[str, object]], label: str) -> list[dict]:
    """隐藏每个候选控件后重测 target 的最小宽度；差值 = 它真正占的最小宽度。"""
    base = target.minimumSizeHint().width()
    rows: list[dict] = []
    for name, widget in candidates:
        if widget is None:
            continue
        was_visible = widget.isVisible()
        widget.hide()
        _pump(app, 0.05)
        target.updateGeometry()
        _pump(app, 0.05)
        after = target.minimumSizeHint().width()
        if was_visible:
            widget.show()
        _pump(app, 0.05)
        target.updateGeometry()
        _pump(app, 0.05)
        rows.append({"name": name, "visible_before": bool(was_visible),
                     "base": base, "after": after, "delta": base - after,
                     "target": label})
    rows.sort(key=lambda row: row["delta"], reverse=True)
    return rows


def measure(lang: str, scale: float, detail: bool, with_job: bool = False) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"folith-f1-{lang}-"))
    settings_dir = work / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    _configure_env(lang, scale, settings_dir)

    import qt_showcase_screenshots as showcase  # noqa: E402 - 复用 seed()/演示数据

    from folith.qt import bootstrap  # noqa: E402

    data_dir = work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".onboarded").touch()
    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=work / "logs")
    from folith.qt import adapter  # noqa: E402
    from folith.qt import window as window_module  # noqa: E402

    demo = showcase.DEMOS[lang]
    showcase.seed(adapter.core, data_dir, demo)
    app = window_module.build_application([])
    # 主题固定 light（宽度与配色无关，固定下来只为排除变量）。偏好走隔离的 ini。
    from folith.qt import preferences  # noqa: E402

    settings = preferences.settings()
    settings.setValue("appearance/theme", "light")
    settings.setValue("appearance/language", lang)
    settings.sync()

    config = adapter.EngineConfig(provider="DeepSeek", model="deepseek-v4-flash",
                                  api_key="demo", target_lang=demo.target_lang)
    window = window_module.MainWindow(config, paths, None)
    # 对照：窗口还没 show() 时的最小宽度（布局尚未激活，值与 show() 后不同，
    # 报告若是这样量的，数字会对不上）。
    pre_show = {
        "window_min": window.minimumSizeHint().width(),
        "home_min": window.home.minimumSizeHint().width(),
        "workbench_min": window.workbench.minimumSizeHint().width(),
    }
    window.resize(*WINDOW_SIZE)
    window.show()
    _pump(app, 1.5)
    window.show_home()
    _pump(app, 1.5)
    job_state: dict = {}
    if with_job:
        # 报告里的「工作台自身 1006–1265」只有在任务真正打开时才可能量到：
        # 打开任务后工作台才装上段落网格、检查器与工具栏。
        from folith.qt.selftest import open_job_and_wait

        open_job_and_wait(window, "showcase-book")
        _pump(app, 1.5)
        job_state = {
            "job_id": window._current_job_id,
            "workbench_page_min_w": window.workbench.minimumSizeHint().width(),
            "window_min_w": window.minimumSizeHint().width(),
            "home_page_min_w": window.home.minimumSizeHint().width(),
        }
        window.show_home()
        _pump(app, 1.0)

    from PySide6.QtWidgets import QFrame

    unified_toolbar = window.workbench.findChild(QFrame, "unifiedToolbar")
    chrome = {
        "menubar": _profile(window.menuBar()),
        "central": _profile(window.centralWidget()),
        "unified_toolbar": _profile(unified_toolbar) if unified_toolbar else None,
    }
    from PySide6.QtWidgets import QLabel, QWidget

    home = window.home
    # 首页大标题是局部变量，没有暴露成属性：按 objectName 找回来。
    title = next((label for label in home.findChildren(QLabel)
                  if label.objectName() == "displayTitle"), None)
    named: dict[str, object] = {
        "title": title,
        "subtitle": getattr(home, "subtitle", None),
        "stats_line": getattr(home, "stats_line", None),
        "search_edit": getattr(home, "search_edit", None),
        "refresh_icon_button": getattr(home, "refresh_icon_button", None),
        "view_switch": getattr(home, "view_switch", None),
        "status_filter": getattr(home, "status_filter", None),
        "sort_box": getattr(home, "sort_box", None),
        "project_tabs": getattr(home, "project_tabs", None),
        "project_tabs_row": getattr(home, "project_tabs_row", None),
        "continue_card": getattr(home, "continue_card", None),
        "table": getattr(home, "table", None),
        "card_scroll": getattr(home, "card_scroll", None),
        "drop_tile": getattr(home, "drop_tile", None),
        "actions_frame": getattr(home, "actions_frame", None),
        "list_card": getattr(home, "list_card", None),
        "empty_state": getattr(home, "empty_state", None),
        "setup_banner": getattr(home, "setup_banner", None),
        "import_button": getattr(home, "import_button", None),
        "engine_card": getattr(home, "engine_card", None),
        "engine_label": getattr(home, "engine_label", None),
        "sidebar": getattr(window, "sidebar_clip", None),
        "home_page": home,
        "workbench_page": window.workbench,
        "projects_page": window.projects,
    }

    result: dict = {
        "lang": lang,
        "scale": scale,
        "window": _profile(window),
        "pages": {
            "home": _profile(home),
            "workbench": _profile(window.workbench),
            "projects": _profile(window.projects),
        },
        "sidebar": _profile(window.sidebar_clip),
        "shell": _profile(window.centralWidget()),
        "stack": _profile(window.stack),
        "job": job_state,
        "pre_show": pre_show,
        "widgets": {},
        "layout_tree": _layout_tree(home),
        "workbench_tree": _layout_tree(window.workbench),
    }
    result["chrome"] = chrome
    for name, widget in named.items():
        if widget is None:
            continue
        entry = _profile(widget)
        entry["tag"] = _tag(widget)
        entry["text"] = _text(widget)
        result["widgets"][name] = entry

    # 消融 1：谁决定整个窗口的最小宽度（答案解析前的页面栈）。
    result["window_ablation"] = _ablate(app, window, [
        ("sidebar", named["sidebar"]),
        ("home_page", home),
        ("workbench_page", window.workbench),
        ("projects_page", window.projects),
    ], "window")

    # 消融 2：谁决定首页的最小宽度。顺序 = 页面自上而下，方便读。
    result["home_ablation"] = _ablate(app, home, [
        ("page_header.title", named["title"]),
        ("page_header.subtitle", named["subtitle"]),
        ("page_header.stats_line", named["stats_line"]),
        ("page_header.search", named["search_edit"]),
        ("page_header.refresh", named["refresh_icon_button"]),
        ("setup_banner", named["setup_banner"]),
        ("continue_card", named["continue_card"]),
        ("list_header.status_filter", named["status_filter"]),
        ("list_header.view_switch", named["view_switch"]),
        ("list_header.sort_box", named["sort_box"]),
        ("list_header.project_tabs", named["project_tabs_row"]),
        ("list_card.all", named["list_card"]),
        ("list_body.table", named["table"]),
        ("list_body.card_scroll", named["card_scroll"]),
        ("list_body.drop_tile", named["drop_tile"]),
        ("list_footer.actions", named["actions_frame"]),
        ("empty_state", named["empty_state"]),
    ], "home")

    # 「继续上次的工作」卡片是首页最小宽度唯一的决定项：再拆到卡片内部看谁撑的。
    continue_card = named["continue_card"]
    if continue_card is not None:
        result["continue_ablation"] = _ablate(app, continue_card, [
            ("continue_paper", getattr(home, "continue_paper", None)),
            ("continue_title", getattr(home, "continue_title", None)),
            ("continue_meta", getattr(home, "continue_meta", None)),
            ("continue_divider", getattr(home, "continue_divider", None)),
            ("continue_stage", getattr(home, "continue_stage", None)),
            ("continue_button", getattr(home, "continue_button", None)),
        ], "continue_card")

    if detail:
        rows = []
        for widget in home.findChildren(QWidget):
            if not widget.isVisibleTo(window):
                continue
            entry = _profile(widget)
            entry["tag"] = _tag(widget)
            entry["text"] = _text(widget)
            rows.append(entry)
        rows.sort(key=lambda row: row["min_w"], reverse=True)
        result["detail"] = rows[:40]

    # 反事实：工作台页不参与最小宽度时，窗口能缩到多窄。
    # QStackedWidget 的最小宽度取**所有页**的最大值（含当前不可见的工作台），
    # 所以量一下"工作台不参与"能到多少 = 只修工作台页头的收益上限。
    workbench_widget = window.workbench
    workbench_widget.hide()
    _pump(app, 0.3)
    window.updateGeometry()
    _pump(app, 0.3)
    window_without_workbench = window.minimumSizeHint().width()
    stack_without_workbench = window.stack.minimumSizeHint().width()
    workbench_widget.show()
    _pump(app, 0.3)
    result["counterfactual"] = {
        "window_min_without_workbench": window_without_workbench,
        "stack_min_without_workbench": stack_without_workbench,
    }

    # 窗口能缩到多窄是 QMainWindow 用布局最小尺寸强制的：从 900 起每 10px 试一次，
    # 第一个"要多少给多少"的宽度就是实际下限（离屏也能量，与真机同一条布局路径）。
    floor = None
    probes = [900, 1000, 1100, 1174, 1200, 1238, 1280, 1302, 1348, 1374, 1415, 1451, 1497]
    probe_rows = []
    for target in probes:
        window.resize(target, WINDOW_SIZE[1])
        _pump(app, 0.05)
        probe_rows.append({"requested": target, "actual": window.width()})
    for target in range(900, 1701, 10):
        window.resize(target, WINDOW_SIZE[1])
        _pump(app, 0.03)
        if window.width() == target:
            floor = target
            break
    window.resize(*WINDOW_SIZE)
    _pump(app, 0.4)

    # 反事实：工作台页不参与最小宽度时，窗口能缩到多窄。
    # QStackedWidget 的最小宽度取**所有页**的最大值（含当前不可见的工作台），
    # 所以量一下"工作台不参与"能到多少 = 只修工作台页头的收益上限。
    workbench_widget = window.workbench
    workbench_widget.hide()
    _pump(app, 0.3)
    window.updateGeometry()
    _pump(app, 0.3)
    window_without_workbench = window.minimumSizeHint().width()
    stack_without_workbench = window.stack.minimumSizeHint().width()
    workbench_widget.show()
    _pump(app, 0.3)
    result["counterfactual"] = {
        "window_min_without_workbench": window_without_workbench,
        "stack_min_without_workbench": stack_without_workbench,
    }

    # 窗口能缩到多窄是 QMainWindow 用布局最小尺寸强制的：先按几个目标宽度试，再从 900
    # 起每 10px 找下限。放在所有结构测量之后，避免中途 resize 影响结构值。
    probes = [900, 1000, 1100, 1174, 1200, 1238, 1280, 1302, 1348, 1374, 1415, 1451, 1497]
    probe_rows = []
    for target in probes:
        window.resize(target, WINDOW_SIZE[1])
        _pump(app, 0.05)
        probe_rows.append({"requested": target, "actual": window.width()})
    floor = None
    for target in range(900, 1701, 10):
        window.resize(target, WINDOW_SIZE[1])
        _pump(app, 0.03)
        if window.width() == target:
            floor = target
            break
    window.resize(*WINDOW_SIZE)
    _pump(app, 0.3)
    result["resize"] = {"floor": floor, "probes": probe_rows}

    result["notes"] = {"settings_dir": str(settings_dir), "data_dir": str(data_dir)}
    window.close()
    _pump(app, 0.3)
    return result


# --------------------------------------------------------------------------
# 父进程：逐语言起子进程，汇总成 Markdown
# --------------------------------------------------------------------------
def _table(rows: list[dict], columns: list[str], headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def _run_child(language: str, scale: float, detail: bool, with_job: bool) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--child",
               "--lang", language, "--scale", str(scale)]
    if detail:
        command.append("--detail")
    if with_job:
        command.append("--with-job")
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"{language}: 子进程失败（exit {completed.returncode}）")
    for line in completed.stdout.splitlines():
        if line.startswith("F1_JSON "):
            return json.loads(line[len("F1_JSON "):])
    sys.stderr.write(completed.stdout)
    raise SystemExit(f"{language}: 子进程没有输出 F1_JSON")


def report(results: list[dict], detail: bool) -> None:
    langs = [row["lang"] for row in results]
    print("# F1 最小宽度实测（macOS 离屏，QT_QPA_PLATFORM=offscreen）")
    print()
    print(f"窗口 {WINDOW_SIZE[0]}×{WINDOW_SIZE[1]}，QT_SCALE_FACTOR={results[0]['scale']}，"
          f"语言：{' / '.join(langs)}")
    print()

    print("## 1. 窗口与页面")
    rows = []
    for result in results:
        window = result["window"]
        rows.append({
            "语言": result["lang"],
            "窗口 minimumSizeHint": f"{window['min_w']}×{window['min_h']}",
            "窗口 minimumWidth": window["min_width"],
            "窗口 sizeHint": f"{window['hint_w']}×{window['hint_h']}",
            "首页 minimumSizeHint": result["pages"]["home"]["min_w"],
            "工作台": result["pages"]["workbench"]["min_w"],
            "项目页": result["pages"]["projects"]["min_w"],
            "shell(central)": result["shell"]["min_w"],
            "stack": result["stack"]["min_w"],
            "show 前 window": result["pre_show"]["window_min"],
            "show 前 首页": result["pre_show"]["home_min"],
            "反事实：工作台不参与": result["counterfactual"]["window_min_without_workbench"],
        })
    print(_table(rows, list(rows[0]), list(rows[0])))

    print()
    print("## 2. 结构值：首页各控件的 minimumSizeHint().width()")
    keys = ["title", "subtitle", "stats_line", "search_edit", "refresh_icon_button",
            "status_filter", "view_switch", "sort_box", "project_tabs", "project_tabs_row",
            "continue_card", "table", "card_scroll", "actions_frame", "list_card",
            "empty_state", "import_button", "engine_card", "sidebar"]
    rows = []
    for key in keys:
        row = {"控件": key}
        for result in results:
            entry = result["widgets"].get(key)
            row[result["lang"]] = entry["min_w"] if entry else "-"
        rows.append(row)
    print(_table(rows, ["控件"] + langs, ["控件"] + langs))

    print()
    print("## 2b. 外壳与环境：窗口实际能缩到多窄")
    rows = []
    for result in results:
        rows.append({
            "语言": result["lang"],
            "实际最小宽度": result["resize"]["floor"],
            "菜单栏 min_w": (result["chrome"]["menubar"] or {}).get("min_w", "-"),
            "centralWidget min_w": (result["chrome"]["central"] or {}).get("min_w", "-"),
            "工作台顶栏 min_w": (result["chrome"]["unified_toolbar"] or {}).get("min_w", "-"),
        })
    print(_table(rows, list(rows[0]), list(rows[0])))
    print()
    print("按目标宽度 resize 后窗口的实际宽度（离屏同一套布局约束）：")
    rows = []
    for result in results:
        row = {"语言": result["lang"]}
        for probe in result["resize"]["probes"]:
            row[str(probe["requested"])] = probe["actual"]
        rows.append(row)
    print(_table(rows, list(rows[0]), list(rows[0])))

    print()
    print("## 2c. 「继续上次的工作」卡片内部：隐藏后卡片最小宽度减少多少")
    rows = []
    names = [row["name"] for row in results[0].get("continue_ablation", [])]
    for name in names:
        row = {"控件": name}
        for result in results:
            entry = next((r for r in result.get("continue_ablation", [])
                          if r["name"] == name), None)
            row[result["lang"]] = entry["delta"] if entry else "-"
        rows.append(row)
    if rows:
        print(_table(rows, ["控件"] + langs, ["控件"] + langs))

    print()
    print("## 3. 消融：隐藏该控件后首页最小宽度减少多少（= 它真正占的宽度）")
    rows = []
    names = [row["name"] for row in results[0]["home_ablation"]]
    for name in names:
        row = {"控件": name}
        for result in results:
            entry = next((r for r in result["home_ablation"] if r["name"] == name), None)
            row[result["lang"]] = entry["delta"] if entry else "-"
        rows.append(row)
    print(_table(rows, ["控件"] + langs, ["控件"] + langs))

    print()
    print("## 4. 消融：隐藏该页面后窗口最小宽度减少多少")
    rows = []
    names = [row["name"] for row in results[0]["window_ablation"]]
    for name in names:
        row = {"页面": name}
        for result in results:
            entry = next((r for r in result["window_ablation"] if r["name"] == name), None)
            row[result["lang"]] = entry["delta"] if entry else "-"
        rows.append(row)
    print(_table(rows, ["页面"] + langs, ["页面"] + langs))

    print()
    print("## 5. 首页布局树（缩进 + 该项的最小宽度）")
    for result in results:
        print()
        print(f"### {result['lang']}")
        rows = []
        for row in result["layout_tree"]:
            label = "  " * row["depth"] + row["label"]
            if row.get("text"):
                label += f"  「{row['text']}」"
            rows.append({"控件/布局": label, "类型": row["kind"], "min_w": row["min_w"]})
        print(_table(rows, ["控件/布局", "类型", "min_w"], ["控件/布局", "类型", "min_w"]))

    print()
    print("## 5b. 工作台布局树（窗口最小宽度的真正来源）")
    for result in results:
        print()
        print(f"### {result['lang']}")
        rows = []
        for row in result["workbench_tree"]:
            label = "  " * row["depth"] + row["label"]
            if row.get("text"):
                label += f"  「{row['text']}」"
            rows.append({"控件/布局": label, "类型": row["kind"], "min_w": row["min_w"]})
        print(_table(rows, ["控件/布局", "类型", "min_w"], ["控件/布局", "类型", "min_w"]))

    if detail:
        print()
        print("## 6. 首页子树里最小宽度最大的控件（每门语言前 15）")
        for result in results:
            print()
            print(f"### {result['lang']}")
            rows = [{"控件": entry["tag"], "min_w": entry["min_w"],
                     "hint_w": entry["hint_w"], "文本": entry["text"]}
                    for entry in result.get("detail", [])[:15]]
            print(_table(rows, ["控件", "min_w", "hint_w", "文本"],
                         ["控件", "min_w", "hint_w", "文本"]))

    print()
    print("## 7. 原始 JSON")
    print("```json")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    print("```")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", action="append", choices=LANGUAGES,
                        help="只测这几门语言（默认全部四门）")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="QT_SCALE_FACTOR（最小宽度是逻辑像素，默认 1.0）")
    parser.add_argument("--detail", action="store_true", help="附组件级明细")
    parser.add_argument("--with-job", action="store_true",
                        help="先打开 showcase-book 任务再量（工作台才有内容）")
    parser.add_argument("--json", metavar="PATH", help="把原始数据写到这个文件")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        if not args.lang or len(args.lang) != 1:
            raise SystemExit("--child 需要恰好一个 --lang")
        print("F1_JSON " + json.dumps(
            measure(args.lang[0], args.scale, args.detail, args.with_job),
            ensure_ascii=False))
        return 0

    languages = tuple(args.lang) if args.lang else LANGUAGES
    results = [_run_child(language, args.scale, args.detail, args.with_job)
               for language in languages]
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    report(results, args.detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
