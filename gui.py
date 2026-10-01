"""译页跨平台 HTML GUI 启动器。

把 Streamlit 的 HTML 界面包装成可直接使用的形式，Windows / macOS / Linux
三平台同一入口：

- 默认：**只监听本机回环地址 127.0.0.1**，启动后自动打开默认浏览器；
- 可选原生窗口：安装 pywebview（见 requirements-desktop.txt）后自动弹出
  桌面窗口渲染同一 HTML 界面；未安装则回退到浏览器；
- --lan：显式选择局域网模式，监听 0.0.0.0，供受信任局域网内的设备使用；
  当前无认证层，因此这是**必须显式开启**的开关，不是默认行为；
- 关闭窗口或 Ctrl+C 即停止服务。
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from folith import brand

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8501
APP_TITLE = brand.APP_TITLE_ZH

# 绑定地址是**产品边界**，不能交给 Streamlit 的隐式默认值。
# Streamlit 不指定 `--server.address` 时监听所有网卡（`0.0.0.0`），也就是
# 同一局域网内的任何设备都能打开这个无认证层的应用。因此默认必须显式
# 收敛到回环地址，局域网只有 `--lan` 这一条显式路径。
LOCAL_BIND_ADDRESS = "127.0.0.1"
LAN_BIND_ADDRESS = "0.0.0.0"  # nosec B104 - 仅由 --lan 显式选择


def bind_address(lan: bool) -> str:
    """服务绑定地址：默认回环；局域网是显式 opt-in。"""
    return LAN_BIND_ADDRESS if lan else LOCAL_BIND_ADDRESS


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    """首选端口被占用时顺延（最多尝试 20 个）。"""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((LOCAL_BIND_ADDRESS, port)) != 0:
                return port
    return preferred


def server_args(port: int, lan: bool) -> list[str]:
    """构造 streamlit 服务启动参数（headless，由本启动器负责打开界面）。

    `--server.address` **总是显式给出**：默认 `127.0.0.1`，只有 `lan=True`
    才绑到 `0.0.0.0`。依赖 Streamlit 的默认值会让应用在用户不知情的情况下
    暴露到局域网。
    """
    args = ["-m", "streamlit", "run", str(ROOT / "app.py"),
            "--server.headless", "true",
            "--server.port", str(port),
            "--server.address", bind_address(lan),
            "--theme.primaryColor", "#004cfd",
            "--theme.textColor", "#131c2e",
            "--browser.gatherUsageStats", "false"]
    return args


def lan_ip() -> str:
    """探测本机局域网 IP（UDP 假连接，不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def url_for(port: int, lan: bool) -> str:
    host = lan_ip() if lan else LOCAL_BIND_ADDRESS
    return f"http://{host}:{port}"


def wait_ready(url: str, timeout: float = 60.0) -> bool:
    """轮询等待 Streamlit 服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # url_for() constructs this URL from a fixed HTTP host and integer port.
            with urllib.request.urlopen(url, timeout=2) as resp:  # nosec B310
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def open_native_window(url: str) -> bool:
    """用 pywebview 弹出原生桌面窗口渲染 HTML 界面；不可用时返回 False。"""
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    try:
        webview.create_window(APP_TITLE, url, width=1280, height=860,
                              min_size=(960, 640))
        webview.start()
        return True
    except Exception:
        return False


def _check_streamlit() -> bool:
    try:
        import streamlit  # noqa: F401
        return True
    except ImportError:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="译页跨平台 HTML GUI 启动器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="服务端口（默认 8501，被占用时自动顺延）")
    parser.add_argument("--lan", action="store_true",
                        help="监听受信任局域网（当前无认证层）")
    parser.add_argument("--browser", action="store_true",
                        help="强制用默认浏览器打开（即使已安装 pywebview）")
    parser.add_argument("--no-browser", action="store_true",
                        help="只启动服务，不打开任何窗口")
    args = parser.parse_args(argv)

    if not _check_streamlit():
        print("[错误] 未找到 Streamlit。请先运行启动器安装依赖，"
              "或执行：python -m pip install -r requirements.txt")
        return 1

    port = pick_port(args.port)
    proc = subprocess.Popen([sys.executable, *server_args(port, args.lan)])
    url = url_for(port, args.lan)
    try:
        if not wait_ready(url):
            print(f"[错误] 服务启动失败（{url} 无法访问）。"
                  "请检查端口占用或终端中的报错信息。")
            return 1
        print("=" * 52)
        print(f"  {APP_TITLE} 已启动")
        print(f"  本机访问：http://{LOCAL_BIND_ADDRESS}:{port}")
        if args.lan:
            print(f"  局域网访问：{url}（手机/平板需与电脑同一网络）")
        print("  关闭窗口或按 Ctrl+C 停止服务。")
        print("=" * 52)

        if args.no_browser:
            proc.wait()
        elif (not args.browser) and open_native_window(url):
            return 0
        else:
            webbrowser.open(url)
            proc.wait()
        return 0
    except KeyboardInterrupt:
        print("\n[系统] 正在停止服务...")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
