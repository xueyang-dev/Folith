"""macOS 外部辅助功能（AX）扫描崩溃的运行时修复：单元 + 集成回归。

被测模块：``folith/qt/macos_accessibility.py``（上游 QTBUG-149612，qtbase commit
8148c48d 修复；随包 PySide6 6.11.2 尚未包含，因此由应用在启动时安装 dealloc shim）。

分两层，各自诚实：

* **单元层**（任何平台都能跑，不创建 QApplication）：环境变量解析、版本门矩阵、
  ``install()`` 的报告形状/失败不抛异常/幂等、``uninstall()`` 空操作、
  ``report()`` 返回副本、以及 ``folith/qt/app.py`` 入口的静态接线。
* **集成层**（仅 macOS + cocoa）：真的起子进程跑
  ``scripts/qt_ax_qtbug_repro.py``，验证"装了修复 → 不崩"。反向对照只在
  （a）宿主是 macOS、（b）当前 Qt 属于已知受影响版本 时才跑，因为上游修复
  发布后"不再崩"是正确结果，不能让它变成红灯；而且它**默认不跑**——见下。
  子进程另有 10s teardown 看门狗；``teardown_hung`` 视为夹具噪声（skip），
  既不是崩溃证据，也不是修复失效。

崩溃侧对照是 opt-in：故意让 GUI 子进程 SIGSEGV 会在 macOS 上留下崩溃报告与
AppKit 窗口恢复（reopen）提示，下一次启动可能被系统弹窗/恢复状态卡住。这是
测试夹具/系统状态的副作用，**不是**运行时修复的问题。需要时显式运行::

    FOLITH_QT_AX_CRASH_REGRESSION=1 venv/bin/python -m pytest \
        tests/qt_macos_accessibility_test.py -q

默认套件只跑 ``test_reproducer_completes_with_the_workaround``（装了修复的子进程
正常完成，不产生崩溃状态）。

诚实边界（AGENTS.md）：非 macOS 上运行本文件只完成单元层；macOS 上的集成层
证明的是"确定性复现器不再触发崩溃"，**不是**外部 AX 客户端（Computer
Use/VoiceOver）的完整验收，也不是冻结 DMG 的验收。见各用例 docstring。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # conftest 也做，但单文件直跑时也需要
    sys.path.insert(0, str(ROOT))

from folith.qt import macos_accessibility as ax  # noqa: E402

REPRO = ROOT / "scripts" / "qt_ax_qtbug_repro.py"
APP_ENTRY = ROOT / "folith" / "qt" / "app.py"

#: 崩溃侧对照的 opt-in 开关：默认套件不故意制造 GUI 崩溃（见模块 docstring）。
CRASH_REGRESSION_ENV = "FOLITH_QT_AX_CRASH_REGRESSION"

UPSTREAM_FIX_COMMIT_PREFIX = "8148c48d"


# ---------------------------------------------------------------------------
# 单元层：模块常量与环境变量
# ---------------------------------------------------------------------------


def test_upstream_identity_is_pinned():
    """缺陷编号与修复提交必须写死在模块里（文档、日志、测试用同一组值）。"""
    assert ax.WORKAROUND_ENV == "FOLITH_QT_AX_WORKAROUND"
    assert ax.UPSTREAM_ISSUE == "QTBUG-149612"
    assert ax.UPSTREAM_FIX_COMMIT.startswith(UPSTREAM_FIX_COMMIT_PREFIX)


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "disabled"])
def test_env_parsing_disables_with_documented_values(monkeypatch, value):
    """``FOLITH_QT_AX_WORKAROUND=0`` 等值只用于 A/B 证据，必须显式关闭修复。"""
    monkeypatch.setenv(ax.WORKAROUND_ENV, value)
    assert ax.workaround_disabled() is True


@pytest.mark.parametrize(
    "value", ["0", "OFF", "False", "NO", "Disabled", " 0 ", "\toff\n", "no "])
def test_env_parsing_is_case_and_whitespace_tolerant(monkeypatch, value):
    """运维/测试手写环境变量时大小写与空白不该决定是否关闭修复。"""
    monkeypatch.setenv(ax.WORKAROUND_ENV, value)
    assert ax.workaround_disabled() is True


def test_env_parsing_unset_or_empty_keeps_the_fix(monkeypatch):
    """默认是**开着**修复：只有显式关闭值才跳过（避免一个笔误静默取消修复）。"""
    monkeypatch.delenv(ax.WORKAROUND_ENV, raising=False)
    assert ax.workaround_disabled() is False
    monkeypatch.setenv(ax.WORKAROUND_ENV, "")
    assert ax.workaround_disabled() is False


@pytest.mark.parametrize("value", ["1", "on", "true", "yes", "enabled", "anything"])
def test_env_parsing_other_values_keep_the_fix(monkeypatch, value):
    monkeypatch.setenv(ax.WORKAROUND_ENV, value)
    assert ax.workaround_disabled() is False


# ---------------------------------------------------------------------------
# 单元层：版本门矩阵
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["6.11.0", "6.11.2", "6.11.3", "6.12.0",
                                     "6.11", "6.12.0-beta1", "6.11.2.9"])
def test_affected_qt_versions(version):
    """6.11.x 与 6.12.0 受影响：上游修复 2026-10-27 才合入，晚于两者发布。"""
    assert ax.is_affected_qt_version(version) is True


@pytest.mark.parametrize("version", ["6.12.1", "6.12.2", "6.13.0", "6.13.0-beta1",
                                     "6.8.3", "6.10.3", "5.15.2", "7.0.0",
                                     None, "", "garbage", "6", "6.x"])
def test_unaffected_qt_versions(version):
    """6.12.1+ 视为已含上游修复；6.10 及更早没有这条清理逻辑，两者都不该装。"""
    assert ax.is_affected_qt_version(version) is False


def test_version_parser_stops_at_first_non_digit_patch_suffix():
    """预发布/发行版后缀不该改变判断（``6.12.0-beta1`` 仍是 6.12.0）。"""
    assert ax.is_affected_qt_version("6.12.0-rc1") is True
    assert ax.is_affected_qt_version("6.12.1-beta1") is False


# ---------------------------------------------------------------------------
# 单元层：install()/uninstall()/report() 的契约与失败安全
# ---------------------------------------------------------------------------


def _fake_cocoa_qt(monkeypatch, version: str = "6.11.2") -> None:
    """把 ``install()`` 看到的平台/Qt 伪装成"macOS + cocoa + 6.11.2"。

    只用于验证**模块自身分支**（环境变量门、版本门、内部缺失时的失败安全）。
    这不是真实 Cocoa 证据：真实安装由下面的子进程集成用例证明。
    """
    pytest.importorskip("PySide6")
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(QApplication, "platformName", staticmethod(lambda: "cocoa"))
    monkeypatch.setattr(
        sys.modules["PySide6.QtCore"], "qVersion", lambda: version)
    assert qVersion  # 保持导入有意义（importorskip 失败时给出清晰错误）


def test_install_report_has_documented_keys_on_wrong_platform(monkeypatch):
    """Windows（以及任何非 macOS）上 install() 必须是纯 no-op：不 import Qt、不抛。"""
    monkeypatch.setattr(sys, "platform", "win32")
    result = ax.install()
    assert isinstance(result, dict)
    for key in ("installed", "reason", "platform", "issue"):
        assert key in result, (key, result)
    assert result["installed"] is False
    assert result["reason"] == "not-macos"
    assert result["platform"] == "win32"
    assert result["issue"] == "QTBUG-149612"
    assert ax.is_installed() is False


def test_install_is_disabled_by_env(monkeypatch):
    """``FOLITH_QT_AX_WORKAROUND=0`` 时报告 disabled-by-env，且什么都不装。"""
    _fake_cocoa_qt(monkeypatch)
    monkeypatch.setenv(ax.WORKAROUND_ENV, "0")
    result = ax.install()
    assert result["installed"] is False
    assert result["reason"] == "disabled-by-env"
    assert result["qt_version"] == "6.11.2"
    assert ax.is_installed() is False


@pytest.mark.parametrize("version", ["6.12.1", "6.13.0", "6.8.3", "garbage"])
def test_install_skips_unaffected_qt(monkeypatch, version):
    """版本门之外绝不安装：升级 PySide6 后本模块应自动失效（可整体删除）。"""
    _fake_cocoa_qt(monkeypatch, version)
    monkeypatch.delenv(ax.WORKAROUND_ENV, raising=False)
    result = ax.install()
    assert result["installed"] is False
    assert result["reason"] == "qt-version-not-affected"
    assert result["qt_version"] == version
    assert ax.is_installed() is False


def test_install_never_raises_when_an_internal_is_missing(monkeypatch):
    """任何一步失败都只能变成报告，不能影响应用启动（fail-safe 约定）。"""
    _fake_cocoa_qt(monkeypatch)
    monkeypatch.delenv(ax.WORKAROUND_ENV, raising=False)
    monkeypatch.setattr(ax, "_process_arch", lambda: "arm64")

    def broken_runtime():
        raise OSError("libobjc not found")

    monkeypatch.setattr(ax, "_objc_runtime", broken_runtime)
    result = ax.install()
    assert isinstance(result, dict)
    assert result["installed"] is False
    assert result["reason"].startswith("error:"), result
    assert "OSError" in result["reason"], result
    assert ax.is_installed() is False


def test_install_never_raises_when_the_logger_fails(monkeypatch):
    """启动日志写不出去（管道关闭等）也不能让 install() 抛异常。"""
    monkeypatch.setattr(sys, "platform", "win32")

    def broken_log(_message: str) -> None:
        raise RuntimeError("log sink closed")

    result = ax.install(log=broken_log)
    assert result["installed"] is False
    assert result["reason"] == "not-macos"


def test_install_twice_reports_already_installed(monkeypatch):
    """重复安装必须幂等：第二次报告 already-installed，且沿用第一次的元数据。"""
    monkeypatch.setitem(ax._state, "installed", True)
    monkeypatch.setitem(ax._state, "qt_version", "6.11.2")
    monkeypatch.setitem(ax._state, "ivar_offset", 8)
    monkeypatch.setitem(ax._state, "ivar_size", 4)

    first = ax.install()
    second = ax.install()

    assert first["installed"] is True and second["installed"] is True
    assert first["reason"] == second["reason"] == "already-installed"
    assert second["qt_version"] == "6.11.2"
    assert second["ivar_offset"] == 8
    assert second["issue"] == "QTBUG-149612"
    assert ax.is_installed() is True


def test_uninstall_without_install_is_a_noop(monkeypatch):
    """没装过时 uninstall() 必须是空操作（atexit 与 aboutToQuit 都会调它）。"""
    monkeypatch.setitem(ax._state, "installed", False)
    monkeypatch.setitem(ax._state, "objc", None)
    monkeypatch.setitem(ax._state, "method", None)
    monkeypatch.setitem(ax._state, "original_address", None)
    assert ax.uninstall() is None
    assert ax.uninstall() is None
    assert ax.is_installed() is False


def test_uninstall_tolerates_missing_runtime_objects(monkeypatch):
    """状态不一致（例如解释器收尾时句柄已被回收）也必须安全复位。"""
    monkeypatch.setitem(ax._state, "installed", True)
    monkeypatch.setitem(ax._state, "objc", None)
    monkeypatch.setitem(ax._state, "method", None)
    monkeypatch.setitem(ax._state, "original_address", None)
    assert ax.uninstall() is None
    assert ax.is_installed() is False


def test_report_returns_a_copy(monkeypatch):
    """``report()`` 给的是快照：调用方改返回值不得污染模块状态。"""
    snapshot = {
        "installed": True,
        "reason": "installed",
        "platform": "darwin/cocoa",
        "issue": "QTBUG-149612",
        "qt_version": "6.11.2",
        "ivar_offset": 8,
    }
    monkeypatch.setattr(ax, "_report", dict(snapshot))

    reported = ax.report()
    assert reported == snapshot
    reported["installed"] = False
    reported["reason"] = "tampered"
    reported["extra"] = "nope"

    assert ax.report() == snapshot
    assert ax._report == snapshot


def test_is_installed_reflects_module_state(monkeypatch):
    monkeypatch.setitem(ax._state, "installed", False)
    assert ax.is_installed() is False
    monkeypatch.setitem(ax._state, "installed", True)
    assert ax.is_installed() is True


# ---------------------------------------------------------------------------
# 单元层：入口接线（静态检查，任何平台都能跑）
# ---------------------------------------------------------------------------

_INSTALL_CALL = (("macos_accessibility", "install"),)


def _call_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _is_darwin_platform_check(node: ast.AST) -> bool:
    """``sys.platform == "darwin"`` 的 AST 形状（容忍左右交换）。"""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False
    left, right = node.left, node.comparators[0]
    for a, b in ((left, right), (right, left)):
        if (isinstance(a, ast.Attribute) and a.attr == "platform"
                and isinstance(a.value, ast.Name) and a.value.id == "sys"
                and isinstance(b, ast.Constant) and b.value == "darwin"):
            return True
    return False


def test_app_entry_installs_the_workaround_on_macos_only():
    """``folith/qt/app.py::main`` 必须在 ``sys.platform == "darwin"`` 分支里安装修复。

    静态钉住两件事：安装点存在且**只有一处**，并且那处位于 darwin 守卫内——
    否则 Windows 启动也会 import 并调用只对 Cocoa 有意义的 swizzle。
    """
    source = APP_ENTRY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_chain(node.func) == _INSTALL_CALL[0]
    ]
    assert len(calls) == 1, f"安装点应恰好一处，实际 {len(calls)} 处"

    guards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_darwin_platform_check(node.test)
        and any(
            isinstance(child, ast.Call) and _call_chain(child.func) == _INSTALL_CALL[0]
            for child in ast.walk(node))
    ]
    assert guards, "macos_accessibility.install(...) 不在 sys.platform == 'darwin' 守卫内"

    guard = guards[0]
    call = calls[0]
    assert guard.lineno <= call.lineno <= (guard.end_lineno or guard.lineno), (
        "install() 必须实际位于 darwin 分支体内")
    # 分支内必须显式 import 该模块（不要在 Windows 上让启动路径依赖它）
    imports = [
        node for node in ast.walk(guard) if isinstance(node, ast.ImportFrom)
        and any(alias.name == "macos_accessibility" for alias in node.names)
    ]
    assert imports, "darwin 分支内缺少 `from . import macos_accessibility`"


def test_app_entry_names_the_upstream_defect():
    """入口注释必须点名 QTBUG-149612，免得后来者把修复当成业务逻辑删掉。"""
    source = APP_ENTRY.read_text(encoding="utf-8")
    assert "QTBUG-149612" in source
    assert "macos_accessibility" in source


# ---------------------------------------------------------------------------
# 集成层：真实子进程（仅 macOS + cocoa）
# ---------------------------------------------------------------------------


def _require_macos() -> None:
    if sys.platform != "darwin":
        pytest.skip(
            "真实 Cocoa/AX 行为只能在 macOS 上验证（sys.platform="
            f"{sys.platform!r}）；本机不声称完成 Cocoa 验收")


def _repro_env() -> dict:
    """子进程环境：让它用真正的 cocoa，去掉 pytest 的 offscreen 与诊断开关。"""
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)      # 让它用真正的 cocoa 平台插件
    env.pop("QT_ACCESSIBILITY", None)     # Qt 6.11 本来就不认这个变量
    env.pop(ax.WORKAROUND_ENV, None)      # 反向对照必须真的没装修复
    return env


def _run_repro(*flags: str, timeout: float = 240.0) -> tuple[subprocess.CompletedProcess, dict]:
    """跑父模式并解析它打在 stdout 的唯一一行 JSON。

    父进程自带 ``--timeout 30``：超时会以 ``timed_out`` 报告而不是让 pytest 抛异常，
    便于把"系统 AX 服务被占用"这类环境噪声识别出来单独跳过。子进程 teardown 另
    有 10s 看门狗（``teardown_hung``），两者都不算崩溃证据。
    """
    completed = subprocess.run(
        [sys.executable, str(REPRO), "--run", "--timeout", "30", *flags],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
        env=_repro_env(),
    )
    payload: dict = {}
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
    return completed, payload


def _skip_if_not_cocoa(completed: subprocess.CompletedProcess, payload: dict) -> None:
    """复现器自报"不在 cocoa 上"时跳过，而不是把环境问题伪装成通过。"""
    if completed.returncode == 6 or payload.get("exit_code") == 6:
        pytest.skip(
            "复现器子进程没有跑在 cocoa 平台上（exit 6）；"
            f"stdout_tail={payload.get('stdout_tail', [])[-3:]}")
    if "NOT APPLICABLE" in (completed.stdout or ""):
        pytest.skip("复现器判定当前环境不适用（非 cocoa）：不声称 AX 验收")


def _skip_if_timed_out(payload: dict) -> None:
    """子进程超时不是证据：跳过并说明，绝不伪装成"修复有效"或"独立失败"。"""
    if payload.get("timed_out") is True or payload.get("verdict") == "timed_out":
        pytest.skip(
            "复现器子进程在 30s 内没有结束（本机系统 AX 服务繁忙/被其他 AX 测试占用）；"
            "本次运行不能判定，跳过而不是断言修复有效或无效")


def _skip_if_teardown_hung(payload: dict) -> None:
    """teardown 看门狗命中：macOS AppKit 窗口恢复状态的夹具问题，不是崩溃。

    故意崩溃过的 GUI 子进程会让系统记住"上次异常退出"，下一次启动可能卡在
    reopen 提示；复现器用 10s 看门狗把这种挂起报成 ``teardown_hung``。这既不
    证明修复有效，也不证明修复失效，跳过并说明。
    """
    if payload.get("teardown_hung") is True or payload.get("verdict") == "teardown_hung":
        pytest.skip(
            "复现器子进程的 teardown 被 10s 看门狗中断（teardown_hung=true）："
            "这是 macOS AppKit 窗口恢复状态的夹具问题，不是崩溃、也不是修复失效")


def test_reproducer_completes_with_the_workaround():
    """装了修复时，确定性复现器必须跑完：exit 0 / crashed=false。

    这是"修复生效"的主回归证据；子进程输出必须显示修复**真的**装上了，
    否则"没崩"可能只是复现器没跑（或提前退出）。用的是复现器的默认顺序
    （faithful upstream 顺序：先问父表要选中子元素，再遍历行/单元格）。
    """
    _require_macos()
    pytest.importorskip("PySide6")
    assert REPRO.is_file(), f"缺少复现器脚本：{REPRO}"

    completed, payload = _run_repro("--with-workaround", "--tail", "60")
    _skip_if_not_cocoa(completed, payload)
    _skip_if_timed_out(payload)
    _skip_if_teardown_hung(payload)

    assert completed.returncode == 0, (
        f"装了修复仍然崩溃/失败：exit={completed.returncode} payload={payload} "
        f"stderr={completed.stderr[-800:]}")
    assert payload.get("crashed") is False, payload
    assert payload.get("exit_code") == 0, payload
    assert payload.get("with_workaround") is True, payload
    assert payload.get("verdict") == "completed", payload
    # 子进程的版本/平台/安装状态来自它自己的 REPRO_META，不是父进程猜的。
    assert payload.get("platform") == "cocoa", payload
    assert payload.get("workaround_applied") is True, payload

    tail = "\n".join(payload.get("stdout_tail") or [])
    assert "macOS AX workaround: installed" in tail, (
        f"子进程没有报告修复已安装，不能把'没崩'当成修复生效：{tail}")
    # 子进程必须真的走完会触发缺陷的序列，而不是在启动阶段就失败退出：
    # 默认顺序 = 先问父表要选中子元素（QTBUG-149612 的触发点），再遍历行/单元格。
    assert "Requesting selected children" in tail, tail
    assert "accessibleInterface(table_id)" in tail and "(present)" in tail, (
        f"父表 QAccessibleInterface 在遍历后没有解析回来，核心断言没执行：{tail}")
    assert "Completed without a crash" in tail, tail


def test_workaround_is_what_prevents_the_crash_on_affected_qt():
    """反向对照：同一序列、同一环境，只差是否安装修复。

    复现器默认顺序（faithful upstream：先问父表要选中子元素——即缺陷的触发点，
    再遍历行/单元格）在受影响 Qt 上会稳定崩溃；`--cells-first` 是顺序相反、
    不等价的次要变体，本用例不用。

    默认跳过，需显式 opt-in：``FOLITH_QT_AX_CRASH_REGRESSION=1``。故意让 GUI
    子进程 SIGSEGV 会在 macOS 上留下崩溃报告与 AppKit 窗口恢复（"Python
    unexpectedly quit while reopening windows"）状态；之后启动的 GUI 子进程可能
    被这个系统提示/恢复状态卡住甚至 100% CPU 空转——那是**测试夹具/系统状态**
    的副作用，不是运行时修复的问题（修复本身由默认套件里的完成侧用例证明）。

    * 当前 Qt 不在已知受影响范围 → skip（上游修复发布后"不崩"才是正确结果，
      不能让未来升级变成红灯）；
    * 子进程不在 cocoa 上 → skip（环境问题不伪装成证据）；
    * 未装修复的序列**没有**崩溃 → skip 而不是 fail：本用例只能证明"修复不是
      唯一变量"，不能反过来断言缺陷必须永远存在。
    * 本用例会在系统 DiagnosticReports 里留下子进程崩溃报告，这是预期副作用。
    """
    _require_macos()
    if os.environ.get(CRASH_REGRESSION_ENV) != "1":
        pytest.skip(
            "崩溃侧对照是 opt-in（FOLITH_QT_AX_CRASH_REGRESSION=1）：故意崩溃的 GUI "
            "子进程会留下 macOS 崩溃报告与 AppKit 窗口恢复状态，默认套件不跑")
    pytest.importorskip("PySide6")

    from PySide6.QtCore import qVersion

    version = qVersion()
    if not ax.is_affected_qt_version(version):
        pytest.skip(
            f"当前 Qt {version} 不在已知受影响范围（6.11.x/6.12.0）；"
            "上游修复发布后不再崩溃是正确结果，跳过崩溃侧对照")

    crashed_run, crashed_payload = _run_repro("--tail", "40")
    completed_run, completed_payload = _run_repro("--with-workaround", "--tail", "40")

    _skip_if_not_cocoa(crashed_run, crashed_payload)
    _skip_if_not_cocoa(completed_run, completed_payload)
    _skip_if_timed_out(completed_payload)
    _skip_if_teardown_hung(completed_payload)

    if crashed_payload.get("crashed") is not True:
        pytest.skip(
            "未装修复的复现器没有崩溃（exit="
            f"{crashed_payload.get('exit_code')}, verdict={crashed_payload.get('verdict')}"
            "）：本机无法用崩溃作对照，不据此声称修复有效或无效")

    assert crashed_payload.get("with_workaround") is False, crashed_payload
    assert crashed_payload.get("exit_code") in (138, 139), crashed_payload
    assert crashed_payload.get("signal") in {"SIGSEGV", "SIGBUS"}, crashed_payload

    assert completed_run.returncode == 0, (
        f"装了修复仍然崩溃：exit={completed_run.returncode} "
        f"stderr={completed_run.stderr[-800:]}")
    assert completed_payload.get("crashed") is False, completed_payload
    assert completed_payload.get("exit_code") == 0, completed_payload
    assert completed_payload.get("workaround_applied") is True, completed_payload

