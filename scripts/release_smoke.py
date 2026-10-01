#!/usr/bin/env python3
"""对**已安装的 wheel** 做端到端冒烟：确认修复真的在发行物里，而不只是在仓库里。

`scripts/release_preflight.py` 检查的是静态事实（空白 / CHANGELOG / 密钥 / 产物内容与
一致性）。本脚本补上另一半：**把 wheel 装进一个干净环境，然后从那个环境里验证行为**。
两者合起来才是完整的发布门禁。

用法：

    python -m venv /tmp/folio-verify
    /tmp/folio-verify/bin/pip install dist/foliothread-0.4.0-py3-none-any.whl
    /tmp/folio-verify/bin/python scripts/release_smoke.py

**必须用装了 wheel 的那个解释器运行**，不要用仓库自带的 venv。
脚本会先断言 `core` 是从 site-packages 导入的、而不是仓库——因为"从仓库导入"
会让所有检查在**未打包的源码**上通过，那种绿是没有意义的。
（用仓库 venv 跑时 `import core` 会直接 ModuleNotFoundError，也算失败。）

退出码：全部通过 0，任一失败 1。
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import pathlib
import socket
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


class Smoke:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, label: str, fn) -> None:
        try:
            detail = fn()
            self.results.append((label, True, detail or "ok"))
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本：任何异常都算失败
            self.results.append((label, False, f"{type(exc).__name__}: {exc}"))

    def report(self) -> int:
        print(f"python : {sys.version.split()[0]}")
        print(f"prefix : {sys.prefix}")
        print(f"repo   : {REPO}")
        print()
        width = max(len(label) for label, _, _ in self.results)
        failed = 0
        for label, ok, detail in self.results:
            if not ok:
                failed += 1
            print(f"  [{'PASS' if ok else 'FAIL'}] {label:<{width}}  {detail}")
        print()
        print(f"{len(self.results) - failed}/{len(self.results)} 通过")
        return 1 if failed else 0


def _import_origin() -> str:
    import core

    origin = pathlib.Path(core.__file__).resolve()
    if "site-packages" not in str(origin) and str(origin).startswith(str(REPO)):
        raise AssertionError(
            f"`core` 来自仓库（{origin}）而不是已安装包——"
            "请在装了 wheel 的干净环境里运行本脚本"
        )
    return f"core -> {origin.parent}"


def _imports() -> str:
    modules = [
        "app", "core", "gui", "folith",
        "folith.textual", "folith.translation_memory",
        "folith.project", "folith.checkpoint",
    ]
    for name in modules:
        importlib.import_module(name)
    assert importlib.util.find_spec("transpraxis") is None, "安装环境仍含旧代码包"
    return f"{len(modules)} 个模块导入成功；无旧代码包"


def _binding() -> str:
    """缺陷 #1：默认只监听回环，且不依赖框架隐式默认值。"""
    import gui

    assert gui.bind_address(False) == "127.0.0.1", gui.bind_address(False)
    assert gui.bind_address(True) == "0.0.0.0", gui.bind_address(True)
    args = gui.server_args(18851, False)
    assert "--server.address" in args, args
    assert args[args.index("--server.address") + 1] == "127.0.0.1", args
    lan_args = gui.server_args(18851, True)
    assert lan_args[lan_args.index("--server.address") + 1] == "0.0.0.0", lan_args
    return "默认 127.0.0.1 / --lan 0.0.0.0，且显式传 --server.address"


def _tm_scope() -> str:
    """缺陷 #2：翻译记忆按目标语言隔离，且信息不足时失败关闭。"""
    from folith import translation_memory as tm

    zh = tm.tm_scope_key("简体中文", "会议明天开始。")
    fr = tm.tm_scope_key("Français", "会议明天开始。")
    assert zh != fr, "不同目标语言生成了同一个键"
    assert tm.tm_unscope_key(zh)[1] == "会议明天开始。"
    assert tm.tm_record_language(zh, {"target": "x"}) == "简体中文"

    store: dict = {}
    assert tm.tm_put(store, "会议明天开始。", "译文", "") == "", "无语言时不应写入"
    assert not store, "无语言时不应留下条目"
    return "作用域键隔离 + 无语言时不写入（fail closed）"


def _textual() -> str:
    """缺陷 #3：正文判定用 Unicode 类别，不是 ASCII/脚本区间。"""
    from folith.textual import has_textual_content

    for text in ["Привет мир", "안녕하세요", "مرحبا", "Γειά σου",
                 "日本語のテキスト", "hello world", "1234", "混合 mixed 文本"]:
        assert has_textual_content(text), f"应判为正文却判成装饰: {text!r}"
    for text in ["", "   ", "———", "···", "***", "— — —", "•••"]:
        assert not has_textual_content(text), f"应判为装饰却判成正文: {text!r}"
    return "西里尔/谚文/阿拉伯/希腊/日文均判为正文；纯符号判为装饰"


def _task_identity() -> str:
    """缺陷 #4：任务身份 = 文档内容 + 项目 + 目标语言。"""
    import core

    assert hasattr(core, "resolve_task_id"), "缺少 resolve_task_id"
    assert hasattr(core, "task_job_id"), "缺少 task_job_id"

    blob = b"same document bytes"
    same = core.task_job_id(blob, target_lang="简体中文", project_id="p1")
    again = core.task_job_id(blob, target_lang="简体中文", project_id="p1")
    other_lang = core.task_job_id(blob, target_lang="Français", project_id="p1")
    other_proj = core.task_job_id(blob, target_lang="简体中文", project_id="p2")

    assert same == again, "同文档+同项目+同语言应得到同一任务（保证可恢复）"
    assert same != other_lang, "换目标语言必须是不同任务"
    assert same != other_proj, "换项目必须是不同任务"
    return "换项目/换语言 → 新任务；完全相同 → 可恢复"


def _entry_points() -> str:
    bindir = pathlib.Path(sys.executable).parent
    found = [n for n in ("folith", "foliothread")
             if (bindir / n).exists()]
    entries = importlib.metadata.distribution("foliothread").entry_points
    assert {e.name for e in entries if e.group == "console_scripts"} == {"folith", "foliothread"}
    assert len(found) == 2, f"缺少控制台入口点，只有 {found}"
    return f"入口点存在: {', '.join(found)}"


def _help() -> str:
    bindir = pathlib.Path(sys.executable).parent
    proc = subprocess.run(
        [str(bindir / "folith"), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"exit={proc.returncode}\n{proc.stderr[:400]}"
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "--port" in out and "--lan" in out, out[:400]
    return "退出码 0，用法里含 --port / --lan"


def _socket_probe() -> str:
    """用真实 socket 验证绑定语义，而不只是断言配置字符串。"""
    import gui

    lan = gui.lan_ip()
    if lan.startswith("127."):
        return f"本机无独立局域网地址（lan_ip={lan}），跳过局域网探测"

    def probe(address: str, host: str) -> int:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((address, 0))
        server.listen(8)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                return client.connect_ex((host, server.getsockname()[1]))
        finally:
            server.close()

    assert probe(gui.bind_address(False), "127.0.0.1") == 0, "默认下回环应可连"
    refused = probe(gui.bind_address(False), lan)
    assert refused in (61, 65), f"默认下局域网应被拒绝，实际 errno={refused}"
    assert probe(gui.bind_address(True), lan) == 0, "--lan 下局域网应可连"
    return f"默认回环可连 / 局域网 errno={refused}；--lan 局域网可连"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对已安装的 wheel 做端到端冒烟（需用装了 wheel 的解释器运行）"
    )
    parser.parse_args()

    smoke = Smoke()
    smoke.check("导入来源是已安装包（非仓库）", _import_origin)
    smoke.check("全部模块可导入", _imports)
    smoke.check("#1 默认绑定回环", _binding)
    smoke.check("#2 TM 语言隔离", _tm_scope)
    smoke.check("#3 Unicode 正文判定", _textual)
    smoke.check("#4 任务身份隔离", _task_identity)
    smoke.check("控制台入口点", _entry_points)
    smoke.check("folith --help", _help)
    smoke.check("真实 socket 绑定语义", _socket_probe)
    return smoke.report()


if __name__ == "__main__":
    sys.exit(main())
