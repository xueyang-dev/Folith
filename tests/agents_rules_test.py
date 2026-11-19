"""AGENTS.md 里**机器能查**的四条平台规则（#459）。

为什么要有这些测试：AGENTS.md 的规则是写给人和 agent 看的，违反了没人会红。
把能机械判定的四条变成测试之后，规则从"提示"变成"门"：

1. **Cocoa / AppKit 只出现在窗口模块**：Cocoa 集成集中在 `folith/qt/macwindow.py`，
   共享 UI 不按系统分叉；
2. **打开/在文件管理器里定位文件只走 `folith/desktop`**：路径与平台差异在那一层收口；
3. **界面里不写死快捷键文本**：快捷键的绑定与可见提示都要来自
   `folith/qt/platform_ui.py` 的同一份定义；
4. **`folith/application` 不 import Qt**：应用层不能依赖界面框架。

每条规则允许一份**写明理由**的豁免名单（`ALLOWED`），豁免条目本身就是评审记录：
加一条就要在 PR 里解释为什么它属于例外。这里只做静态检查，不运行被测代码。

运行：`python -m pytest tests/agents_rules_test.py -q`
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 规则只覆盖产品代码：测试、构建产物与工具目录不在范围内。
_SCAN_ROOTS = ("folith",)
_SCAN_FILES = ("core.py",)
_SKIP_DIRS = {"__pycache__", ".loop", "build", "dist", "node_modules", ".git"}

#: 规则 1 的唯一允许落点。
COCOA_HOME = "folith/qt/macwindow.py"
#: 规则 1 的豁免：`single_instance` 用 AppKit 做的**不是窗口集成**，而是
#: `NSRunningApplication` 的应用激活（第二次启动时把已有实例带到前台）。
#: 把它搬进 `qt/` 会让 `desktop` 反向依赖界面层，所以留在原地并在此登记。
COCOA_ALLOWED = {
    "folith/desktop/single_instance.py":
        "NSRunningApplication activation for the second-launch path, not window integration",
}
_COCOA_ROOTS = {"AppKit", "Cocoa", "Foundation", "Quartz", "objc", "AppKit"}

#: 规则 2 的唯一允许落点（平台命令都在这一层）。
FILE_ACTIONS_HOME = "folith/desktop/file_actions.py"
#: 规则 2 的豁免：设置里的「打开链接」是**网址**，不是文件。
FILE_ACTIONS_ALLOWED = {
    "folith/qt/settings_pages.py": "opens an https URL, not a path",
}

#: 规则 3 的唯一允许落点与豁免（运行后按实际命中逐条登记）。
SHORTCUT_HOME = "folith/qt/platform_ui.py"
SHORTCUT_ALLOWED: dict[str, str] = {}

#: 规则 4：应用层不得出现的依赖。
_FORBIDDEN_APP_IMPORTS = ("PySide6", "folith.qt")

_SHORTCUT_GLYPHS = ("⌘", "⇧", "⌥", "⌃", "Ctrl+", "Cmd+", "Alt+")

#: 样式表里的注释会提到键帽（例如「右侧 ⌘K 提示是一枚小键帽」），那不是界面文本。
_STYLESHEET_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _product_files() -> list[Path]:
    files: list[Path] = [ROOT / name for name in _SCAN_FILES if (ROOT / name).is_file()]
    for root in _SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if _SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
                continue
            files.append(path)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(tree: ast.Module) -> set[str]:
    """Every absolute module name an import statement (anywhere) names."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: not a third-party root
                continue
            if node.module:
                names.add(node.module)
                names.add(node.module.split(".")[0])
    return names


def _call_name(node: ast.Call) -> str:
    """``os.startfile(...)`` -> ``os.startfile``; ``f(...)`` -> ``f``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        parts, current = [func.attr], func.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _string_literal_nodes(tree: ast.Module) -> list[ast.Constant]:
    """String constants that are **code**, not docstrings or comments."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


# ---------------------------------------------------------------------------
# 1. Cocoa / AppKit 只在窗口模块
# ---------------------------------------------------------------------------

def test_cocoa_and_appkit_imports_stay_in_the_window_module():
    offenders = []
    for path in _product_files():
        rel = _rel(path)
        if rel == COCOA_HOME or rel in COCOA_ALLOWED:
            continue
        roots = _imported_roots(_tree(path))
        hit = sorted(roots & _COCOA_ROOTS)
        if hit:
            offenders.append(f"{rel}: {', '.join(hit)}")
    assert not offenders, (
        "Cocoa / AppKit 只能出现在 "
        f"{COCOA_HOME}（或在 ALLOWED 里登记理由）：\n  " + "\n  ".join(offenders))


def test_the_cocoa_allowlist_still_matches_reality():
    """豁免名单不能烂掉：它登记的文件必须真的还在用 AppKit。"""
    for rel in COCOA_ALLOWED:
        path = ROOT / rel
        assert path.is_file(), f"豁免名单里的文件不存在：{rel}"
        roots = _imported_roots(_tree(path))
        assert roots & _COCOA_ROOTS, (
            f"{rel} 已经不再 import Cocoa/AppKit，请把它从 ALLOWED 里删掉")


# ---------------------------------------------------------------------------
# 2. 打开/定位文件只走 folith/desktop
# ---------------------------------------------------------------------------

_FORBIDDEN_FILE_CALLS = ("os.startfile", "webbrowser.open", "webbrowser.open_new_tab")
#: 这些 argv 说明有人绕过 `file_actions` 自己调平台命令。
_FORBIDDEN_ARGV = {"xdg-open", "explorer", "open", "start"}


def _file_open_violations(path: Path) -> list[str]:
    tree = _tree(path)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _FORBIDDEN_FILE_CALLS:
            found.append(f"{name}()")
        if name.startswith("subprocess."):
            for arg in node.args:
                if isinstance(arg, ast.List):
                    literals = [item.value for item in arg.elts
                                if isinstance(item, ast.Constant)
                                and isinstance(item.value, str)]
                    if any(literal in _FORBIDDEN_ARGV for literal in literals):
                        found.append(f"{name}({literals})")
        if name == "QUrl.fromLocalFile":
            found.append("QUrl.fromLocalFile() (file paths belong to file_actions)")
    return found


def test_files_are_opened_only_through_the_desktop_package():
    offenders = []
    for path in _product_files():
        rel = _rel(path)
        if rel in (FILE_ACTIONS_HOME, *FILE_ACTIONS_ALLOWED):
            continue
        violations = _file_open_violations(path)
        if violations:
            offenders.append(f"{rel}: {', '.join(sorted(set(violations)))}")
    assert not offenders, (
        "打开/定位文件必须走 folith/desktop（file_actions）；"
        "网址请在 FILE_ACTIONS_ALLOWED 里登记：\n  " + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# 3. 界面不写死快捷键文本
# ---------------------------------------------------------------------------

def test_the_interface_does_not_hardcode_shortcut_text():
    offenders = []
    for path in _product_files():
        rel = _rel(path)
        if not rel.startswith("folith/qt/") or rel == SHORTCUT_HOME \
                or rel in SHORTCUT_ALLOWED:
            continue
        for node in _string_literal_nodes(_tree(path)):
            text = _STYLESHEET_COMMENT.sub("", node.value)
            if any(glyph in text for glyph in _SHORTCUT_GLYPHS):
                offenders.append(f"{rel}:{node.lineno}: {text.strip()[:70]!r}")
    assert not offenders, (
        "快捷键的绑定与提示都来自 folith/qt/platform_ui.py；"
        "在别处写死会随平台漂移（确属例外的登记到 SHORTCUT_ALLOWED）：\n  "
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# 4. 应用层不 import Qt
# ---------------------------------------------------------------------------

def test_the_application_layer_does_not_import_qt():
    offenders = []
    for path in sorted((ROOT / "folith" / "application").rglob("*.py")):
        if _SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        roots = _imported_roots(_tree(path))
        hit = sorted(root for root in roots
                     if any(root == name or root.startswith(name + ".")
                            for name in _FORBIDDEN_APP_IMPORTS))
        if hit:
            offenders.append(f"{_rel(path)}: {', '.join(hit)}")
    assert not offenders, (
        "folith/application 是应用层，不能依赖界面框架：\n  " + "\n  ".join(offenders))
