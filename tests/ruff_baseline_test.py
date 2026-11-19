"""ruff 冻结基线的比较逻辑（#460）。

`scripts/check_ruff_baseline.py` 的语义只有三条，这里各钉一条：

1. **新违例失败**——不比基线多的部分非空就红；
2. **修掉旧违例通过**——基线里多出来的（stale）只提示，不失败，否则修 bug 反而红；
3. **行号漂移不算新违例**——键里不含行号，改一行代码不该让几百条基线同时失效。

不跑 ruff：比较函数是纯函数，喂合成数据就能测，CI 里也不需要先装 ruff 才能跑这条。
"""
from __future__ import annotations

import collections
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_ruff_baseline",
    Path(__file__).resolve().parents[1] / "scripts" / "check_ruff_baseline.py")
ruff_baseline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ruff_baseline)


def _counter(*keys: str) -> collections.Counter:
    return collections.Counter(keys)


def test_a_new_finding_beyond_the_baseline_fails():
    baseline = _counter("F401\tcore.py\timport os")
    current = _counter("F401\tcore.py\timport os", "F841\tcore.py\tdead = 1")
    new, stale = ruff_baseline.compare(current, baseline)
    assert dict(new) == {"F841\tcore.py\tdead = 1": 1}
    assert not stale


def test_fixing_an_old_finding_passes_and_is_only_a_note():
    baseline = _counter("F401\tcore.py\timport os", "F841\tcore.py\tdead = 1")
    current = _counter("F401\tcore.py\timport os")
    new, stale = ruff_baseline.compare(current, baseline)
    assert not new
    assert dict(stale) == {"F841\tcore.py\tdead = 1": 1}


def test_the_same_line_twice_is_compared_by_count_not_by_presence():
    baseline = _counter("F811\ttests/a.py\tdef test_same():")
    current = _counter("F811\ttests/a.py\tdef test_same():",
                       "F811\ttests/a.py\tdef test_same():")
    new, _stale = ruff_baseline.compare(current, baseline)
    assert dict(new) == {"F811\ttests/a.py\tdef test_same():": 1}


def test_the_baseline_key_ignores_line_numbers():
    root = Path(__file__).resolve().parents[1]
    target = root / "tests" / "_ruff_key_probe.py"
    target.write_text("import os\nunused = 1\n", encoding="utf-8")
    try:
        finding = {"code": "F841", "filename": str(target),
                   "location": {"row": 2}, "message": "unused"}
        key = ruff_baseline.key_for(root, finding)
        assert key == "F841\ttests/_ruff_key_probe.py\tunused = 1"
        # Same offending line, different row -> the same key.
        moved = {**finding, "location": {"row": 1}}
        assert ruff_baseline.key_for(root, moved) != key, "row 1 is a different line"
    finally:
        target.unlink()


def test_the_committed_baseline_uses_the_bug_class_rule_set():
    """基线只能装这批规则的结果；混进别的规则说明有人在别处改了配置。"""
    root = Path(__file__).resolve().parents[1]
    baseline = root / "docs" / "ruff-baseline.txt"
    assert baseline.is_file(), "docs/ruff-baseline.txt 缺失"
    selectors = ruff_baseline.RULES.split(",")
    families = {selector for selector in selectors if len(selector) == 1}
    exact = {selector for selector in selectors if len(selector) > 1}
    codes = set()
    for line in baseline.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        codes.add(line.split("\t", 1)[0])

    def allowed(code: str) -> bool:
        return code in exact or any(code.startswith(family) for family in families)

    unexpected = sorted(code for code in codes if not allowed(code))
    assert not unexpected, f"基线里出现了规则集之外的代码：{unexpected}"
    # The bug classes the issue names explicitly must really be selected:
    # undefined names (F821) and unused imports (F401) come with the F family.
    assert "F" in families, "pyflakes 全家没开，未定义名与未使用导入就漏了"
    assert "E722" in exact, "bare except 没开"
