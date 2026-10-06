"""Internal desktop service mode: `--serve`.

Roadmap §3.1: a frozen build must have an explicit internal service mode that
starts Streamlit only and never recursively creates a window.  The service:

1. takes over stdout as the private control channel (Streamlit output is
   re-pointed at stderr so it can never corrupt the protocol);
2. reads the launcher's `hello` (instance id + per-launch startup token) from
   stdin -- the token never appears on a command line, URL, log or diagnostic;
3. binds an OS-assigned ephemeral port on 127.0.0.1 and reads the real port back
   from Streamlit's own config after the server is genuinely serving;
4. reports `ready` with its real PID, port, app version and build id;
5. stops on an authenticated `stop` command, on stdin EOF (launcher crashed),
   or on SIGTERM/SIGINT, acknowledging with `stopped`.

Streamlit's private bootstrap surface is used deliberately and probed: D0 must
verify this hook against the frozen Streamlit version rather than assume it.
"""
from __future__ import annotations

import argparse
import hmac
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import paths as desktop_paths
from .protocol import (
    EVENT_ERROR,
    EVENT_READY,
    EVENT_STOPPED,
    InstanceIdentity,
    ProtocolError,
    ServiceIdentity,
    decode_message,
    encode_message,
)

HELLO_TIMEOUT_SECONDS = 30.0
# Exit codes are part of the launcher contract; keep them distinguishable.
EXIT_OK = 0
EXIT_BAD_USAGE = 2
EXIT_NO_HELLO = 3
EXIT_HOOK_UNAVAILABLE = 4
EXIT_STARTUP_FAILED = 5

_DESKTOP_STREAMLIT_OPTIONS: dict[str, Any] = {
    "server_port": 0,  # OS-assigned ephemeral port; the real port is read back
    "server_address": "127.0.0.1",
    "server_headless": True,
    "server_fileWatcherType": "none",
    "browser_gatherUsageStats": False,
    "client_showErrorDetails": "full",
    "global_developmentMode": False,
}


class ControlChannel:
    """Private, bidirectional, parent-child-only control channel over stdio."""

    def __init__(self, write_fd: int, read_fd: int = 0) -> None:
        self._write_fd = write_fd
        self._read_fd = read_fd
        self._write_lock = threading.Lock()
        self._closed = False

    @classmethod
    def take_over_stdio(cls) -> "ControlChannel":
        """Reserve the original stdout for the protocol; send everything else to stderr."""
        try:
            sys.stdout.flush()
        except Exception:  # pragma: no cover - defensive
            pass
        write_fd = os.dup(1)
        os.set_inheritable(write_fd, False)
        # From here on, anything written to "stdout" (Streamlit's CLI output)
        # lands in the launcher-captured stderr log instead of the channel.
        os.dup2(2, 1)
        return cls(write_fd=write_fd)

    def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            return
        payload = encode_message(message)
        with self._write_lock:
            try:
                written = 0
                while written < len(payload):
                    written += os.write(self._write_fd, payload[written:])
            except OSError:
                # The launcher is gone; nothing useful is left to report.
                self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._write_fd)
        except OSError:  # pragma: no cover - defensive
            pass


def _read_lines(fd: int, sink: "queue.Queue[bytes | None]") -> None:
    """Read newline-delimited frames from a raw fd until EOF."""
    buffer = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            sink.put(None)
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            sink.put(line + b"\n")


def _await_hello(inbox: "queue.Queue[bytes | None]", timeout: float) -> dict[str, Any]:
    try:
        line = inbox.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("no launcher handshake within the startup window") from exc
    if line is None:
        raise ConnectionError("control channel closed before the launcher handshake")
    return decode_message(line)


def _probe_streamlit_hook() -> tuple[Any, Any, Any]:
    """Verify the Streamlit bootstrap surface this mode depends on exists.

    Returns ``(bootstrap, server_cls, config)`` or raises RuntimeError.
    """
    from streamlit import config as st_config
    from streamlit.web import bootstrap
    from streamlit.web.server.server import Server

    missing = [
        name
        for name in (
            "_fix_sys_path",
            "_fix_sys_argv",
            "_install_config_watchers",
            "_set_up_signal_handler",
            "_run_server_loop",
        )
        if not callable(getattr(bootstrap, name, None))
    ]
    if missing:
        raise RuntimeError(
            "Streamlit bootstrap hook is unavailable in this build: missing "
            + ", ".join(missing)
        )
    if not callable(getattr(Server, "start", None)):
        raise RuntimeError("Streamlit Server.start is unavailable in this build")
    try:
        int(st_config.get_option("server.port"))
    except Exception as exc:
        raise RuntimeError(f"Streamlit config has no usable server.port option: {exc}") from exc
    return bootstrap, Server, st_config


def run_service(
    app_script: Path,
    *,
    control: ControlChannel,
    hello_timeout: float = HELLO_TIMEOUT_SECONDS,
    dev_identity: InstanceIdentity | None = None,
) -> int:
    """Run the Streamlit service for one launcher instance."""
    inbox: "queue.Queue[bytes | None]" = queue.Queue()
    reader = threading.Thread(
        target=_read_lines, args=(control._read_fd, inbox), name="folith-control", daemon=True
    )
    reader.start()

    if dev_identity is None:
        try:
            hello = _await_hello(inbox, hello_timeout)
        except (TimeoutError, ConnectionError, ProtocolError) as exc:
            print(f"[folith-desktop] handshake failed: {exc}", file=sys.stderr, flush=True)
            return EXIT_NO_HELLO
        if hello.get("cmd") != "hello":
            print(
                f"[folith-desktop] expected a 'hello' command, received {hello.get('cmd')!r}",
                file=sys.stderr, flush=True,
            )
            return EXIT_NO_HELLO
        identity = InstanceIdentity(
            instance_id=str(hello.get("instance_id") or ""),
            startup_token=str(hello.get("startup_token") or ""),
            launcher_pid=int(hello.get("launcher_pid") or 0),
            app_version=str(hello.get("app_version") or desktop_paths.app_version()),
            build_id=str(hello.get("build_id") or desktop_paths.build_id()),
        )
        if not identity.instance_id or not identity.startup_token:
            print("[folith-desktop] handshake lacked instance_id/startup_token",
                  file=sys.stderr, flush=True)
            return EXIT_NO_HELLO
    else:
        identity = dev_identity

    try:
        bootstrap, Server, st_config = _probe_streamlit_hook()
    except Exception as exc:
        control.send({"v": 1, "event": EVENT_ERROR, "reason": "hook-unavailable",
                      "message": str(exc)})
        print(f"[folith-desktop] {exc}", file=sys.stderr, flush=True)
        return EXIT_HOOK_UNAVAILABLE

    script_path = str(app_script)
    bootstrap._fix_sys_path(script_path)
    bootstrap._fix_sys_argv(script_path, [])
    # Mirrors `streamlit run`: flags are applied explicitly, then watched.  Doing
    # only the watcher registration silently leaves server.port at its 8501
    # default, which would defeat OS-assigned random ports.
    bootstrap.load_config_options(flag_options=dict(_DESKTOP_STREAMLIT_OPTIONS))
    bootstrap._install_config_watchers(dict(_DESKTOP_STREAMLIT_OPTIONS))
    try:
        st_config._server_mode = "starlette-managed"
    except Exception:  # pragma: no cover - defensive
        pass

    import asyncio

    server = Server(script_path, is_hello=False)
    stop_requested = threading.Event()

    def request_stop() -> None:
        stop_requested.set()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - defensive
            server.stop()
        else:
            loop.call_soon_threadsafe(server.stop)

    def watch_control() -> None:
        while True:
            try:
                line = inbox.get()
            except Exception:  # pragma: no cover - defensive
                return
            if line is None:
                # Launcher died or closed the pipe: never outlive our parent.
                print("[folith-desktop] control channel closed; stopping service",
                      file=sys.stderr, flush=True)
                request_stop()
                return
            try:
                message = decode_message(line)
            except ProtocolError as exc:
                print(f"[folith-desktop] ignoring malformed control message: {exc}",
                      file=sys.stderr, flush=True)
                continue
            if message.get("cmd") == "stop":
                token = message.get("startup_token")
                if not isinstance(token, str) or not hmac.compare_digest(
                        token, identity.startup_token):
                    print("[folith-desktop] rejected unauthenticated stop request",
                          file=sys.stderr, flush=True)
                    continue
                request_stop()
                return

    watcher = threading.Thread(target=watch_control, name="folith-control-watch", daemon=True)
    watcher.start()

    async def run_server() -> None:
        await server.start()
        # The real port is only knowable after Streamlit has bound the socket;
        # this is the hook D0 must validate against the frozen version.
        actual_port = int(st_config.get_option("server.port"))
        if actual_port <= 0:
            raise RuntimeError(
                "Streamlit did not report a bound port; refusing to claim readiness"
            )
        try:
            bootstrap._on_server_start(server)
        except Exception:  # pragma: no cover - non-fatal CLI niceties only
            pass
        bootstrap._set_up_signal_handler(server)

        service = ServiceIdentity(
            instance_id=identity.instance_id,
            startup_token=identity.startup_token,
            service_pid=os.getpid(),
            port=actual_port,
            app_version=identity.app_version,
            build_id=identity.build_id,
            extra={
                "frozen": desktop_paths.is_frozen(),
                "python_version": sys.version.split()[0],
                "streamlit_version": _streamlit_version(),
                "platform": sys.platform,
                "started_at": time.time(),
            },
        )
        control.send(service.ready_message())
        print(
            f"[folith-desktop] service ready pid={service.service_pid} port={actual_port}",
            file=sys.stderr, flush=True,
        )

        await server.stopped

        control.send({
            "v": identity.protocol_version,
            "event": EVENT_STOPPED,
            "instance_id": identity.instance_id,
            "startup_token": identity.startup_token,
            "service_pid": os.getpid(),
            "stop_requested": stop_requested.is_set(),
        })

    try:
        bootstrap._run_server_loop(run_server())
    except Exception as exc:
        control.send({"v": 1, "event": EVENT_ERROR, "reason": "startup-failed",
                      "message": str(exc)})
        print(f"[folith-desktop] service startup failed: {exc}", file=sys.stderr, flush=True)
        return EXIT_STARTUP_FAILED
    finally:
        control.close()
    return EXIT_OK


def _streamlit_version() -> str:
    try:
        import streamlit

        return str(getattr(streamlit, "__version__", "unknown"))
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="folith --serve",
        description="Folith internal desktop service (not a user-facing entry point)",
    )
    parser.add_argument("--serve", action="store_true", help="run the internal service mode")
    parser.add_argument("--app-script", default=None,
                        help="Streamlit main script (defaults to the bundled app.py)")
    parser.add_argument("--handshake-timeout", type=float, default=HELLO_TIMEOUT_SECONDS)
    parser.add_argument(
        "--serve-dev", action="store_true",
        help="debugging only: skip the launcher handshake and print the URL to stderr",
    )
    args, _unknown = parser.parse_known_args(argv)

    app_script = Path(args.app_script).expanduser().resolve() if args.app_script \
        else desktop_paths.app_script_path()
    if not app_script.exists():
        print(f"[folith-desktop] app script not found: {app_script}", file=sys.stderr, flush=True)
        return EXIT_BAD_USAGE

    control = ControlChannel.take_over_stdio()
    dev_identity = None
    if args.serve_dev:
        dev_identity = InstanceIdentity.create(
            launcher_pid=os.getpid(),
            app_version=desktop_paths.app_version(),
            build_id=desktop_paths.build_id(),
        )
        print("[folith-desktop] --serve-dev: launcher handshake disabled (debug only)",
              file=sys.stderr, flush=True)
    return run_service(
        app_script,
        control=control,
        hello_timeout=args.handshake_timeout,
        dev_identity=dev_identity,
    )
