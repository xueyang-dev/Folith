"""B-108 · 探针读报告不能依赖平台默认编码（Windows 上是 GBK）。

`scripts/qt_desktop_probe.py` 的 `--frozen-app` 路径曾在流程跑成功后，用
`Path(args.out).read_text()` 读 UTF-8 报告；在默认编码是 GBK 的 Windows 上抛
`UnicodeDecodeError`，脚本退出码 1，看起来像流程失败。

写报告的一方（`folith.qt.selftest.write_report`）一直写 UTF-8，所以读取端必须
显式指定编码。这里钉两件事：

1. 读取端确实要求 UTF-8（跨平台，直接看调用参数）；
2. 在默认编码不是 UTF-8 的解释器里，旧写法会失败、`load_report` 能读回中文
   （只在 POSIX 上跑：用 C locale 把默认编码降到 ASCII，复现同一个失败形状；
   本机装不了 zh_CN.GBK，但「默认编码解不了 UTF-8 字节」是同一个故障）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _probe_module():
    path = ROOT / "scripts" / "qt_desktop_probe.py"
    spec = importlib.util.spec_from_file_location("qt_desktop_probe_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_reader_asks_for_utf8(monkeypatch, tmp_path):
    probe = _probe_module()
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"note": "中文", "ok": True}, ensure_ascii=False),
                      encoding="utf-8")
    seen = {}
    real = Path.read_text

    def spy(self, *args, **kwargs):
        seen.update(kwargs)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    assert probe.load_report(report) == {"note": "中文", "ok": True}
    assert seen.get("encoding") == "utf-8", seen


def test_a_missing_report_is_an_empty_mapping(tmp_path):
    probe = _probe_module()
    assert probe.load_report(tmp_path / "absent.json") == {}


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows 无法把解释器默认编码降成非 UTF-8；由上面的参数检查覆盖")
def test_a_non_utf8_default_encoding_reproduces_the_failure(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"note": "中文报告"}, ensure_ascii=False),
                      encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f"""
        import importlib.util, json, sys
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "probe", {str(ROOT / "scripts" / "qt_desktop_probe.py")!r})
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)

        report = Path({str(report)!r})
        print("default", sys.getdefaultencoding(), __import__("locale").getpreferredencoding(False))
        try:
            report.read_text()
        except UnicodeDecodeError as exc:
            print("plain_read_failed", type(exc).__name__)
        else:
            print("plain_read_succeeded")
        print("loaded", json.dumps(probe.load_report(report), ensure_ascii=False))
    """), encoding="utf-8")
    # C locale 让解释器的默认编码落到 ASCII：与 GBK 一样解不了 UTF-8 字节。
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0",
               PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(driver)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env)
    assert result.returncode == 0, result.stderr
    assert "plain_read_failed UnicodeDecodeError" in result.stdout, result.stdout
    assert '"note": "中文报告"' in result.stdout, result.stdout
