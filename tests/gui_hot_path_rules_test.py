"""GUI 线程热路径的标记约定与「不许阻塞」检查（#505）。

**约定**（也写在 `docs/gui-hot-paths.md`）：跑在 GUI 线程上的热路径函数用名字标记——
`paintEvent` / `showEvent` / `eventFilter`，`_paint*`、`_poll*`、`_apply_delta`、
`_apply_row_delta`、`_queue_segment_paint`、`apply_delta`。凡是这些名字的函数，都是在
「每一帧 / 每一次轮询」的预算里执行的。

**为什么需要它**：G2 的卡顿就是这一类问题——比如打开任务时同步构建增量模型（88 ms，
P2-20）。这类代码写起来完全合法、类型检查也过，只有「它跑在 GUI 线程上」这个隐含前提
被违反时才出事。约定 + 检查把这件事从评审记忆变成门。

**检查什么**：被标记的函数体内**直接**出现下列行为即失败——文件读写、JSON 解析、
任务状态 / journal 读取、子进程 / 网络调用、以及 `time.sleep`：

- 内建 `open()`；
- `Path.read_text/read_bytes/write_text/write_bytes/open()`、`os.startfile`；
- `json.load/loads`、`tomllib.load/loads`；
- `load_job_state*`、`load_runtime_state`、`read_mutations*`、`replay_from`、`*_read_json`；
- `subprocess.*`、`socket.*`、`urllib.*`、`requests.*`、`httpx.*`；
- `time.sleep()`。

**已知边界**：只查直接调用，不查间接（`self._load_everything()` 里读文件不会被发现）。
这条限制是刻意的——间接检查需要调用图，误报会把门变成噪声。让热路径只做「取已就绪的
数据 + 画」才是这个约定的本意。

**豁免**：`ALLOWED` 里按「文件 + 函数名」登记，并写明理由；加一条就是一次评审。

运行：`python -m pytest tests/gui_hot_path_rules_test.py -q`
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 约定：这些前缀的函数在 GUI 线程的热路径上执行。
HOT_PREFIXES = (
    "paintEvent", "showEvent", "eventFilter",
    "_paint", "_poll", "apply_delta", "_apply_delta",
    "_apply_row_delta", "_queue_segment_paint",
)

#: 已评审的豁免：(相对路径, 函数名) -> 理由。空表示今天没有例外。
ALLOWED: dict[tuple[str, str], str] = {}

#: 直调即违规的函数名（不看接收者，名字本身就是阻塞操作）。
_FORBIDDEN_NAMES = {
    "open", "read_text", "read_bytes", "write_text", "write_bytes",
    "startfile", "load", "loads",
    "load_job_state", "load_job_state_cached", "load_runtime_state",
    "read_mutations", "read_mutations_from", "replay_from", "sleep",
}
#: 这些模块下的任何调用都算阻塞（子进程、网络、状态文件）。
#: 注意 `time` 不在这里：`time.perf_counter()` 是热路径里合法的计时，
#: 真正要禁的 `time.sleep` 由 `_FORBIDDEN_NAMES` 里的 `sleep` 抓。
_FORBIDDEN_ROOTS = {
    "subprocess", "socket", "urllib", "requests", "httpx",
    "state_journal", "job_lock", "shutil",
}
#: 名字以这些结尾也算（`_read_json`、`read_state` 之类的包装）。
_FORBIDDEN_SUFFIXES = ("_read_json", "_read_state", "_load_state")


def _call_parts(call: ast.Call) -> tuple[str, str]:
    """``(root, name)``: ``json.loads(x)`` -> ``("json", "loads")``."""
    func = call.func
    name = ""
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    root = ""
    current = func
    while isinstance(current, ast.Attribute):
        if isinstance(current.value, ast.Name):
            root = current.value.id
            break
        current = current.value
    if not root and isinstance(func, ast.Name):
        root = func.id
    return root, name


def violations_in_source(source: str, *, filename: str = "<memory>") -> list[str]:
    """Return one message per blocking call found inside a hot-path function."""
    tree = ast.parse(source, filename=filename)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith(HOT_PREFIXES):
            continue
        if (filename, node.name) in ALLOWED:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            root, name = _call_parts(inner)
            blocking = (
                name in _FORBIDDEN_NAMES
                or root in _FORBIDDEN_ROOTS
                or name.endswith(_FORBIDDEN_SUFFIXES)
            )
            if blocking:
                called = f"{root}.{name}" if root and root != name else name
                found.append(
                    f"{filename}:{inner.lineno} {node.name}() 直调 {called}()")
    return found


def _scan(targets) -> list[str]:
    problems: list[str] = []
    for path in targets:
        rel = path.relative_to(ROOT).as_posix()
        problems.extend(violations_in_source(
            path.read_text(encoding="utf-8"), filename=rel))
    return problems


def test_gui_hot_paths_do_no_blocking_work_inline():
    targets = sorted((ROOT / "folith").rglob("*.py"))
    problems = _scan(targets)
    assert not problems, (
        "GUI 线程热路径（名字约定见 tests/gui_hot_path_rules_test.py 顶部）里"
        "不能有文件/JSON/状态读取、子进程或 sleep；把工作交给后台再回来画。"
        "确属例外的登记到 ALLOWED 并写明理由：\n  " + "\n  ".join(problems))


def test_the_convention_actually_matches_code():
    """约定不能空转：仓库里必须真的有一批被标记的函数。"""
    marked = 0
    for path in sorted((ROOT / "folith" / "qt").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        marked += sum(
            1 for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith(HOT_PREFIXES))
    assert marked >= 50, f"只匹配到 {marked} 个热路径函数，约定与代码脱节了"


def test_a_planted_file_read_inside_a_marked_function_fails():
    """验收原话：热路径里塞一次文件读取必须被抓住。"""
    planted = '''
def paintEvent(self, event):
    payload = open("/tmp/state.json", encoding="utf-8").read()
    self.draw(payload)
'''
    problems = violations_in_source(planted, filename="planted.py")
    assert len(problems) == 1, problems
    assert "paintEvent" in problems[0] and "open" in problems[0]


def test_a_planted_json_parse_and_state_read_are_both_caught():
    planted = '''
def _poll_workbench(self):
    state = core.load_job_state("job")
    data = json.loads(state_text)

def _paint_row(self, row):
    time.sleep(0.01)
'''
    problems = violations_in_source(planted, filename="planted.py")
    joined = "\n".join(problems)
    assert len(problems) == 3, problems
    for expected in ("load_job_state", "json.loads", "time.sleep"):
        assert expected in joined, joined


def test_clean_hot_and_dirty_cold_code_is_left_alone():
    """冷路径不受约束；热路径里的普通构造/绘制调用也不报。"""
    source = '''
def paintEvent(self, event):
    painter = QPainter(self)
    painter.drawText(10, 10, self._label)

def _load_everything(self):
    with open("/tmp/x.json", encoding="utf-8") as stream:
        return json.load(stream)
'''
    assert violations_in_source(source, filename="ok.py") == []
