"""HTML GUI 启动器（gui.py）测试：不真正启动 Streamlit、不打开窗口。

绑定语义用**真实的监听 socket** 验证，而不是只断言配置字符串：配置写对了
但没生效（例如被 Streamlit 的隐式默认值覆盖）一样会把无认证层的应用暴露到
局域网，那种情况只有真的连一次才看得出来。

**夹具约定（踩过坑）**：每个探测都用**全新的**监听 socket，且**只连一次**。
原因是 `listen(1)` 的 backlog 只有 1，而 `accept()` 永远不会被调用——同一个
监听 socket 上连第二次时，内核会直接 RST 掉多余的连接，`connect_ex` 返回
`ECONNRESET(54)`。那看起来像"绑定错了"，其实是夹具把 backlog 挤爆了：
一度造成这个用例约 1/3 的运行概率性失败。
"""
import errno
import os
import socket
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui

# backlog 给足余量，避免"第二次连接被 RST"再次伪装成绑定错误。
_LISTEN_BACKLOG = 8
# 明确的"不可达"信号。ECONNRESET / ETIMEDOUT **不在此列**：它们指向
# 夹具或网络栈的问题，不能拿来当"默认模式没有暴露"的证据。
_UNREACHABLE = {errno.ECONNREFUSED, errno.EHOSTUNREACH}


def test_brand_title():
    assert gui.APP_TITLE == "译页 · 智能体翻译工作台"
    print("  ✓ 桌面窗口品牌标题")


def test_server_args():
    args = gui.server_args(8501, lan=False)
    assert args[:3] == ["-m", "streamlit", "run"]
    assert Path(args[3]) == gui.ROOT / "app.py"
    assert "--server.headless" in args and "true" in args
    assert "--server.port" in args and "8501" in args
    assert args[args.index("--theme.primaryColor") + 1] == "#004cfd"
    assert args[args.index("--theme.textColor") + 1] == "#131c2e"

    # 默认必须**显式**绑定回环：不能依赖 Streamlit 的隐式默认值（那是 0.0.0.0）。
    assert args.count("--server.address") == 1, "默认模式必须显式给出绑定地址"
    assert args[args.index("--server.address") + 1] == "127.0.0.1"

    lan_args = gui.server_args(9000, lan=True)
    assert lan_args.count("--server.address") == 1
    assert lan_args[lan_args.index("--server.address") + 1] == "0.0.0.0"
    print("  ✓ server_args（headless/端口/默认回环/lan 显式绑定）")


def _connect_result(host, port):
    """从 `host` 连一次，返回 `connect_ex` 的 errno（0 = 可达）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2.0)
        return probe.connect_ex((host, port))


def _probe_binding(address, host):
    """新起一个绑在 `address` 的监听 socket，只探测一次，然后关掉。

    每个断言一条独立 socket：既避开 backlog 溢出，也让各条断言之间
    没有任何共享状态可互相干扰。
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((address, 0))
    server.listen(_LISTEN_BACKLOG)
    try:
        return _connect_result(host, server.getsockname()[1])
    finally:
        server.close()


def test_default_binding_serves_loopback_and_not_lan():
    """默认绑定：回环可达、局域网明确拒绝。`--lan`：两者都可达。"""
    lan = gui.lan_ip()
    lan_is_distinct = not lan.startswith("127.")

    # 默认模式：本机可达
    assert _probe_binding(gui.bind_address(lan=False), "127.0.0.1") == 0, \
        "默认模式必须能本机访问"

    # 默认模式：局域网必须**明确拒绝**。断言具体 errno 而不是 `!= 0`，
    # 否则 ECONNRESET 这类夹具问题会伪装成"没暴露"而让用例假通过。
    if lan_is_distinct:
        result = _probe_binding(gui.bind_address(lan=False), lan)
        assert result in _UNREACHABLE, \
            f"默认模式必须从局域网 IP 明确拒绝，实际 errno={result}"

    # --lan 模式：本机与局域网都必须可达
    assert _probe_binding(gui.bind_address(lan=True), "127.0.0.1") == 0, \
        "lan 模式也必须能本机访问"
    if lan_is_distinct:
        assert _probe_binding(gui.bind_address(lan=True), lan) == 0, \
            "显式 --lan 必须真的能局域网访问"

    if not lan_is_distinct:
        print("  ✓ 绑定语义（回环可达；本环境无独立局域网 IP，跳过 LAN 探测）")
    else:
        print(f"  ✓ 绑定语义（默认仅回环且 LAN 明确拒绝；--lan 后 {lan} 可达）")


def test_pick_port():
    # 占用一个端口后，pick_port 应顺延到下一个可用端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        taken = s.getsockname()[1]
        picked = gui.pick_port(taken)
        assert picked != taken, "被占用端口应被跳过"
        assert taken < picked < taken + 20
    print("  ✓ pick_port（端口顺延）")


def test_url_for():
    assert gui.url_for(8501, lan=False) == "http://127.0.0.1:8501"
    lan_url = gui.url_for(9000, lan=True)
    assert lan_url == f"http://{gui.lan_ip()}:9000"
    print("  ✓ url_for（本机 / 局域网）")


def test_lan_ip_is_ipv4():
    ip = gui.lan_ip()
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)
    print("  ✓ lan_ip 返回 IPv4 地址")


def main():
    test_brand_title()
    test_server_args()
    test_default_binding_serves_loopback_and_not_lan()
    test_pick_port()
    test_url_for()
    test_lan_ip_is_ipv4()
    print("GUI 启动器逻辑测试通过 ✅")


if __name__ == "__main__":
    main()


# --- 桌面 / 源码分派（roadmap §3.1「源码 CLI 保留解释器启动路径」） ----------


class _FakeProc:
    """Minimal stand-in for the Streamlit child process."""

    def __init__(self):
        self.waited = False
        self.terminated = False
        self._alive = True

    def wait(self, timeout=None):  # noqa: D401 - 模拟子进程
        self.waited = True
        self._alive = False
        return 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.terminated = True
        self._alive = False


def _dispatch_probe(monkeypatch, argv, *, frozen):
    """记录 main() 走的是桌面路径还是源码路径，不真正启动任何东西。"""
    calls = {"desktop": 0, "popened": [], "waited": False}

    def fake_run_desktop(args):
        calls["desktop"] += 1
        return 7

    monkeypatch.setattr(gui, "run_desktop", fake_run_desktop)
    from folith.desktop import paths as desktop_paths

    # gui.main() imports this module locally, so patch the module itself.
    monkeypatch.setattr(desktop_paths, "is_frozen", lambda: frozen)
    monkeypatch.setattr(gui, "_check_streamlit", lambda: True)
    monkeypatch.setattr(gui, "pick_port", lambda port: 8600)
    monkeypatch.setattr(gui, "wait_ready", lambda url: True)

    proc = _FakeProc()

    def fake_popen(cmd, *args, **kwargs):
        calls["popened"].append(cmd)
        return proc

    monkeypatch.setattr(gui.subprocess, "Popen", fake_popen)
    code = gui.main(argv)
    calls["waited"] = proc.waited
    return code, calls


def test_legacy_flag_keeps_the_interpreter_launch_path(monkeypatch):
    code, calls = _dispatch_probe(monkeypatch, ["--legacy", "--no-browser"], frozen=True)
    assert calls["desktop"] == 0, "--legacy must never open a desktop window"
    assert calls["popened"], "the source path must spawn the Streamlit server"
    command = calls["popened"][0]
    assert command[:3] == [sys.executable, "-m", "streamlit"]
    assert code == 0


def test_frozen_without_legacy_uses_the_desktop_path(monkeypatch):
    code, calls = _dispatch_probe(monkeypatch, [], frozen=True)
    assert calls["desktop"] == 1 and code == 7
    assert not calls["popened"], "the frozen app must not spawn a second Streamlit server"


def test_explicit_desktop_flag_uses_the_desktop_path_in_source_mode(monkeypatch):
    code, calls = _dispatch_probe(monkeypatch, ["--desktop"], frozen=False)
    assert calls["desktop"] == 1 and code == 7


def test_source_mode_without_flags_stays_on_the_source_path(monkeypatch):
    code, calls = _dispatch_probe(monkeypatch, ["--no-browser"], frozen=False)
    assert calls["desktop"] == 0
    assert calls["popened"], "source runs keep the existing interpreter launch path"
    assert code == 0


# --- 桌面模式退出码契约（roadmap §3.1「失败显示原因和日志入口」） ------------


class _FakeDesktopService:
    """Minimal desktop service so the exit-code contract can be tested."""

    def __init__(self, **kwargs):
        self.paths = types.SimpleNamespace(
            data_dir=Path("/tmp/folith-exit-test/data"),
            log_dir=Path("/tmp/folith-exit-test/logs"),
            service_log=Path("/tmp/folith-exit-test/logs/service.log"),
            launcher_log=Path("/tmp/folith-exit-test/logs/launcher.log"))
        self.stopped = False
        self.running = False

    def start(self):
        identity = types.SimpleNamespace(instance_id="test-instance", startup_token="token")
        service = types.SimpleNamespace(pid=os.getpid(), port=51999)
        evidence = types.SimpleNamespace(instance_id="test-instance", service_pid=os.getpid(),
                                        port=51999, app_version="0.5.0", build_id="test-build")
        return types.SimpleNamespace(url="http://127.0.0.1:51999", identity=identity,
                                     service=service, evidence=evidence, paths=self.paths)

    def stop(self):
        self.stopped = True
        return types.SimpleNamespace(launcher_child_gone=True, service_pid_gone=True,
                                     port_released=True, acknowledged=True,
                                     residual_pids=[])


class _FakeInstanceLock:
    def __init__(self, paths):
        self.paths = paths

    def acquire(self):
        return True

    def record(self):
        return None

    def publish(self, record):
        return None

    def release(self):
        return None


def _desktop_run(monkeypatch, *, pywebview=True, window_ok=True, script=None):
    """Run run_desktop against fakes; `script` triggers the automation-load path."""

    from folith.desktop import diagnostics as desktop_diagnostics
    from folith.desktop import launcher as desktop_launcher
    from folith.desktop import single_instance

    monkeypatch.setattr(desktop_launcher, "DesktopService", _FakeDesktopService)
    monkeypatch.setattr(single_instance, "AppInstanceLock", _FakeInstanceLock)
    monkeypatch.setattr(single_instance, "build_record", lambda identity, service: object())
    monkeypatch.setattr(desktop_diagnostics, "write_diagnostics",
                        lambda paths, pywebview_version=None: paths.log_dir / "diagnostics.json")
    monkeypatch.setattr(gui, "_check_pywebview", lambda: pywebview)
    monkeypatch.setattr(gui, "open_native_window", lambda url, **kwargs: window_ok)

    class Args:
        # Only the attributes run_desktop actually reads.
        ready_timeout = 1.0
        browser = False
        no_browser = False
        automation_script = script
        automation_result = None

    return gui.run_desktop(Args())


def test_missing_pywebview_fails_with_code_3(monkeypatch):
    assert _desktop_run(monkeypatch, pywebview=False) == 3


def test_window_creation_failure_fails_with_code_4(monkeypatch):
    assert _desktop_run(monkeypatch, window_ok=False) == 4


def test_automation_script_failure_fails_with_code_8(monkeypatch):
    assert _desktop_run(monkeypatch, script="/definitely/missing/probe.py") == 8


def test_healthy_desktop_run_returns_zero(monkeypatch):
    assert _desktop_run(monkeypatch, script=None) == 0
