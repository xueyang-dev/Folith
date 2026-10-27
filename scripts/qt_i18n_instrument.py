#!/usr/bin/env python3
"""把 Qt 客户端的界面文案标记成 `i18n.tr(...)`，并生成/刷新 Qt 目录。

为什么用脚本而不是手改 800 处：漏一处就是一处永远不翻译的字。脚本用 AST 定位
"UI 出口"（控件构造、setText/setWindowTitle/addTab/addItem…、自定义控件的文案参数），
**只替换这些字符串字面量的源码区间**（按行列偏移做精确切片），因此：

* 注释、日志、QSS 文本、异常文本都不会被误伤；
* 同一行上有多个文案也不会互相覆盖（不是按行替换，而是按字节区间）。

f-string 按仓库既有约定转成 `{v0}`/`{v1}` 占位符（Web 目录里就是这个写法）：
`f"已导入 {n} 个任务"` → `i18n.tr("已导入 {v0} 个任务", v0=n)`。

用法：

    venv/bin/python scripts/qt_i18n_instrument.py --check      # 只报告，不改文件
    venv/bin/python scripts/qt_i18n_instrument.py --write      # 就地改写
    venv/bin/python scripts/qt_i18n_instrument.py --catalogs   # 生成 qt-*.json + 覆盖率
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "folith" / "qt"
LOCALES = ROOT / "folith" / "resources" / "locales"
LANGUAGES = ("zh-CN", "en", "es", "ru", "ar", "fr", "de")

CJK = re.compile(r"[\u4e00-\u9fff]")

CTOR_ARG0 = {
    "QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QAction",
    "QListWidgetItem", "QTableWidgetItem", "QGroupBox", "QToolButton",
    # 仓库自己那套控件（`style.CheckBox` 带自绘对勾等）
    "CheckBox", "RadioButton",
}
#: 第一个位置参数就是文案的自定义控件
TEXT_CTORS = {"SectionLabel", "Chip", "KpiCard", "EmptyState", "NoticeStrip"}
#: prompts 的对话框：文案从第 1 个位置参数开始（第 0 个是 parent）
PROMPT_CTORS = {"ConfirmDialog": (1, 2), "NoticeDialog": (1, 2),
                "TextInputDialog": (1, 2), "BusyDialog": (1,)}
METHOD_ARGS = {
    "setText": (0,), "setWindowTitle": (0,), "setPlaceholderText": (0,),
    "setToolTip": (0,), "setLabelText": (0,), "setDetailedText": (0,),
    "addTab": (2,), "addItem": (0,), "addAction": (1,),
    "addMenu": (0,), "setHorizontalHeaderLabels": (0,),
    "set_message": (0,), "set_text": (0,), "set_action": (0,),
    # 应用自己的提示入口：标题与正文都是界面文案
    "_failure": (0, 1), "_notice": (0, 1), "_critical": (0, 1),
    "_mark_dirty_status": (0,), "showMessage": (0,),
    "set_engine_problem": (0, 1), "set_status": (0,),
    "_ask_name": (0, 1), "_set_status": (0,),
    # 等待指示器的文案（此前整条没被标记，俄语界面会露出中文）
    "_busy": (0,), "_run_with_busy": (0, 1),
    # 确认框：标题 / 正文 / 两个按钮文字
    "_confirm": (0, 1),
    # 设置页与检查器的版式辅助函数（`_field` 的第 1 个参数是控件，不会被误包）
    "_panel": (0, 1), "_field": (0, 1, 2),
    # 侧栏导航与图标按钮（`icon_button(名称, 提示)` 的第 0 个是图标名）
    "nav_button": (0,), "icon_button": (1,),
    # prompts 的类方法与实例方法（构造函数已在上面的集合里）
    "notify": (1, 2), "ask": (1, 2), "get_text": (1, 2),
    "add_button": (0,), "set_status_text": (0,),
}
KW_TEXT = {"text", "title", "hint", "label", "message", "detail", "placeholder",
           "yes", "no", "ok", "tooltip"}
#: 有些关键字只在特定函数里是文案（`_confirm(..., yes=, no=)`）
KW_FUNCS = {"_confirm": {"yes", "no"}, "_field": {"hint"}}
#: 按函数名限定要包的关键字（`kind` 太通用，只在写文件冲突那一处包）
KW_BY_FUNC = {"_confirm_overwrite": {"kind"}}

#: **运行时才出现**的界面文字：由核心拼出来（状态标签、目标语言、段落状态），
#: 源码里只有模板或根本没有。它们同样要进目录，否则状态永远是中文。
DYNAMIC_VOCAB = (
    # 目标语言（adapter.TARGET_LANGUAGES）
    "简体中文", "繁体中文", "日本語", "한국어",
    # 任务状态（core.task_status_label）
    "待处理", "处理中断", "已完成", "可以冻结交付", "待审校", "待学术复核",
    "待术语确认", "暂不满足交付条件", "无法读取", "已冻结交付",
    "工作版本已偏离冻结交付", "已冻结交付 v{v0}",
    "工作版本已偏离冻结交付 v{v0}",
    # 运行状态（views.RUNTIME_STATUS_TONES）
    "正在运行", "正在恢复任务", "正在取消", "正在暂停", "已暂停",
    "上次运行已中断", "未完成", "暂无运行信号", "当前步骤失败",
    "任务已取消", "空闲",
    # 核心给出的进度单位（`runtime.stage_progress.unit`）
    "批次",
    # 段落状态
    "已审校", "人工修改", "已排除", "翻译失败", "审校失败",
    # 模型连接问题的固定措辞（`EngineConfig.connection_problem()`）
    "尚未选择模型提供商", "尚未填写模型名称", "尚未填写 API Key",
    # 表头（类常量元组，脚本看不到字面量；在渲染处逐项 tr）
    "文档", "状态", "目标语言", "段落", "更新时间", "段", "原文", "译文",
    # 阶段步骤条的步骤名（`stage.STEP_LABELS`，按键取值后才 tr）
    "准备",
    # 文档结构面板（#187）：节点状态后缀与说明按 reason 取值后才 tr
    "文档结构", "收起文档结构", "结构", "未能定位",
    "部分定位", "部分原文段落没有和当前文本对上；点击会跳到已定位的第一段。",
    "结构已变化", "译文结构已经编辑过，不能按旧位置跳转；请按原文重新定位。",
    "没有可定位的原文", "这个范围没有可读取的原文段落。",
    "原文整理没有留下段落对应关系，无法安全定位。",
    "已全部排除", "这个范围内的段落已经被排除，不参与翻译与交付。",
    "部分已排除", "这个范围的一部分段落已被排除；点击会跳到仍在翻译的第一段。",
    "这个范围没有对应的翻译段落。", "任务还没有分段信息。",
    "按位置定位", "旧任务没有稳定段身份，按当前段落位置定位。",
    "这个范围的原文段落没有和当前文本对上。", "{count} 段",
    # 审校投影（`folith.workbench_view`）里带数字的推荐动作与交付说明，
    # 作为模板在显示时翻译（`i18n.tr_dynamic`）。
    "重试失败的 {v0} 段", "重新审校 {v0} 段", "审校尚未完成的 {v0} 段",
    "继续处理 {v0} 个必须处理的问题", "检查 {v0} 个建议", "前往交付",
    "{v0} 段审校未完成，请重试", "{v0} 段译文在上次审校后发生变化，需要重新审校",
    "还有 {v0} 个必须处理的问题", "还有 {v0} 段尚未完成审校",
    "当前译文的审校尚未完成",
)

SKIP_CALLS = {
    "print", "re.compile", "re.search", "re.match", "json.dumps", "json.loads",
    "setObjectName", "setProperty", "setShortcut", "QKeySequence", "setValue",
    "setStyleSheet", "open", "Path", "write_text", "read_text",
}


def _is_cjk(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(CJK.search(node.value))
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(part, ast.Constant) and CJK.search(str(part.value))
                   for part in node.values)
    return False


class Collector(ast.NodeVisitor):
    """收集"需要包 tr(...)"的节点（不做改写）。"""

    def __init__(self) -> None:
        self.targets: list[ast.AST] = []

    def _maybe(self, node) -> None:
        if node is not None and _is_cjk(node) and node not in self.targets:
            self.targets.append(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        # 已经是 i18n.tr(...) 的调用不再包第二层（重复运行必须是幂等的）
        if name in ("tr", "tr_n") and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "i18n":
            return
        if name in SKIP_CALLS:
            return
        if name in CTOR_ARG0 and node.args:
            self._maybe(node.args[0])
        if name in TEXT_CTORS and node.args:
            self._maybe(node.args[0])
        if name in PROMPT_CTORS:
            for index in PROMPT_CTORS[name]:
                if len(node.args) > index:
                    self._maybe(node.args[index])
        if name in METHOD_ARGS:
            for index in METHOD_ARGS[name]:
                if len(node.args) > index:
                    self._maybe(node.args[index])
        for keyword in node.keywords:
            if keyword.arg in KW_BY_FUNC.get(name, set()):
                self._maybe(keyword.value)
        if name in TEXT_CTORS | CTOR_ARG0 | {
                "notify", "ask", "get_text", "add_button"} | set(PROMPT_CTORS) \
                or name in KW_FUNCS:
            for keyword in node.keywords:
                if keyword.arg in KW_TEXT:
                    self._maybe(keyword.value)
        if name in {"addItems", "setHorizontalHeaderLabels"} and node.args \
                and isinstance(node.args[0], (ast.List, ast.Tuple)):
            for element in node.args[0].elts:
                self._maybe(element)
        self.generic_visit(node)


def _line_offsets(source: str) -> list[int]:
    """按 **UTF-8 字节** 记行首偏移。

    CPython 的 `col_offset` 是 UTF-8 字节偏移，不是字符偏移；中文行上两者能差出
    好几倍，按字符切片会把文件切碎（第一版就是这么写坏的）。
    """
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line.encode("utf-8")))
    return offsets


def _slice(data: bytes, offsets: list[int], node: ast.AST) -> str:
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    return data[start:end].decode("utf-8")


def _replacement(data: bytes, offsets: list[int], node: ast.AST) -> tuple[int, int, str]:
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    if isinstance(node, ast.Constant):
        return start, end, f"i18n.tr({node.value!r})"
    template = ""
    keywords: list[str] = []
    for part in node.values:  # ast.JoinedStr
        if isinstance(part, ast.Constant):
            template += str(part.value)
        else:
            name = f"v{len(keywords)}"
            template += "{" + name + "}"
            keywords.append(f"{name}={_slice(data, offsets, part.value)}")
    call = f"i18n.tr({template!r}"
    if keywords:
        call += ", " + ", ".join(keywords)
    return start, end, call + ")"


def rewrite(path: Path, *, write: bool) -> int:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    collector = Collector()
    collector.visit(tree)
    if not collector.targets:
        return 0
    data = source.encode("utf-8")
    offsets = _line_offsets(source)
    replacements = [_replacement(data, offsets, node) for node in collector.targets]
    # 去掉重复区间（同一个节点可能被两种规则同时命中）
    unique: dict[tuple[int, int], str] = {}
    for start, end, text in replacements:
        unique[(start, end)] = text
    spliced = data
    for (start, end) in sorted(unique, key=lambda item: -item[0]):
        spliced = spliced[:start] + unique[(start, end)].encode("utf-8") + spliced[end:]
    new_source = spliced.decode("utf-8")
    if write:
        if "i18n.tr(" in new_source and "from . import i18n" not in new_source:
            new_source = _add_import(new_source)
        path.write_text(new_source, encoding="utf-8")
    return len(unique)


def _add_import(source: str) -> str:
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("from . import "):
            lines.insert(index, "from . import i18n\n")
            return "".join(lines)
    for index, line in enumerate(lines):
        if line.startswith("from PySide6") and index > 0:
            lines.insert(index, "from . import i18n\n\n")
            return "".join(lines)
    return source


def collect_tr_strings() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for text in DYNAMIC_VOCAB:
        if CJK.search(text):
            found.setdefault(text, []).append("(runtime)")
    for path in sorted(QT_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("tr", "tr_n") and node.args \
                    and isinstance(node.args[0], ast.Constant):
                value = str(node.args[0].value)
                if CJK.search(value):
                    found.setdefault(value, []).append(path.name)
    return found


def write_catalogs() -> dict:
    strings = collect_tr_strings()
    LOCALES.mkdir(parents=True, exist_ok=True)
    web = {code: json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
           for code in LANGUAGES if (LOCALES / f"{code}.json").is_file()}
    report: dict[str, dict] = {}
    for code in LANGUAGES:
        path = LOCALES / f"qt-{code}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data: dict[str, str] = {}
        reused = 0
        missing: list[str] = []
        for source in sorted(strings):
            if code == "zh-CN":
                data[source] = source
            elif source in existing:
                data[source] = existing[source]
            elif source in web.get(code, {}):
                data[source] = web[code][source]
                reused += 1
            else:
                missing.append(source)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        report[code] = {"total": len(strings), "filled": len(data),
                        "reused_from_web": reused, "missing": len(missing),
                        "missing_sample": missing[:5]}
    return {"strings": len(strings), "by_locale": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--catalogs", action="store_true")
    args = parser.parse_args()

    if args.catalogs:
        print(json.dumps(write_catalogs(), ensure_ascii=False, indent=2))
        return 0

    total = 0
    for path in sorted(QT_DIR.glob("*.py")):
        if path.name == "i18n.py":
            continue
        count = rewrite(path, write=args.write)
        if count:
            print(f"{path.name:16} {'改写' if args.write else '可改写'} {count} 处")
            total += count
    print(f"合计 {total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
