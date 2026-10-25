"""Exercise the actual PowerShell helper against disposable native EXEs.

No registry, installed Folith or Start Menu changes. This checks process wait,
silent installer arguments (spaces), verification, rollback and relaunch.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Real Windows helper")
CSC = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
HELPER = Path(__file__).resolve().parents[1] / "folith/desktop/install-update.ps1"


def _literal(path):
    return '@"' + str(path).replace('"', '""') + '"'


def _compile(path, body, version="0.7.1"):
    if not CSC.exists():
        pytest.skip("C# fixture compiler unavailable")
    source = path.with_suffix(".cs")
    source.write_text('using System; using System.IO; using System.Reflection;\n'
        f'[assembly:AssemblyInformationalVersion("{version}")]\n'
        f'class Program {{ static int Main(string[] args) {{ {body} }} }}', encoding="utf-8")
    subprocess.run([str(CSC), "/nologo", "/target:winexe", "/out:" + str(path), str(source)],
                    check=True, capture_output=True)


@pytest.mark.parametrize("mode", ["success", "failure", "wrong-version", "corrupt"])
def test_actual_helper_installs_or_restores_and_restarts(tmp_path, mode):
    install = tmp_path / "Programs with spaces" / "Folith"
    install.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    data = tmp_path / "user data"
    data.mkdir()
    secret = data / "config.txt"
    secret.write_text("private settings", encoding="utf-8")
    marker = logs / "launched.txt"
    _compile(install / "Folith.exe", f'File.WriteAllText({_literal(marker)}, "old"); return 0;')
    original = (install / "Folith.exe").read_bytes()
    new = tmp_path / "new.exe"
    _compile(new, f'File.WriteAllText({_literal(marker)}, "new"); return 0;',
             version="0.7.1" if mode == "wrong-version" else "0.8.0")
    installer = tmp_path / "installer.exe"
    # NSIS /D deliberately consumes the rest of the command line, including spaces.
    _compile(installer, 'string cmd = Environment.CommandLine; '
        'int index = cmd.IndexOf("/D="); string target = cmd.Substring(index + 3).Trim(); '
        f'File.Copy({_literal(new)}, Path.Combine(target, "Folith.exe"), true); '
        f'return {3 if mode == "failure" else 0};')
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if mode == "corrupt":
        digest = "0" * 64
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(HELPER), "-ParentId", "99999999", "-Installer", str(installer),
        "-Sha256", digest, "-TargetVersion", "0.8.0", "-InstallDir", str(install),
        "-DataDir", str(data), "-LogDir", str(logs)], check=True, capture_output=True, timeout=40)
    result = json.loads((logs / "update-result.json").read_text(encoding="utf-8-sig"))
    assert result["state"] == ("installed" if mode == "success" else "failed"), result
    content = ""
    for _ in range(100):
        if marker.exists():
            try:
                content = marker.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            if content:
                break
        time.sleep(0.05)
    assert content == ("new" if mode == "success" else "old")
    assert secret.read_text() == "private settings"
    if mode != "success":
        expected = {"failure": "Installer exit code 3", "wrong-version": "Installed version does not match",
                    "corrupt": "Update package validation failed"}[mode]
        assert expected in result["message"]
        assert (install / "Folith.exe").read_bytes() == original
    else:
        assert (install.with_name("Folith.update-backup") / "Folith.exe").read_bytes() == original


def test_helper_with_compiled_production_nsis_installer(tmp_path):
    """Run the production installer logic with isolated registration/shortcuts."""
    makensis = shutil.which("makensis")
    if not makensis:
        pytest.skip("NSIS compiler unavailable")
    import uuid
    from PIL import Image
    test_name = "FolithUpdateTest-" + uuid.uuid4().hex
    install = tmp_path / "Programs with spaces" / "Folith"
    install.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "user.txt").write_text("preserved")
    marker = logs / "launched.txt"
    _compile(install / "Folith.exe", "return 0;")
    base = tmp_path / "build-inputs"
    bundle = base / "dist/qt-windows/Folith"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "_internal/runtime.txt").write_text("new runtime")
    _compile(bundle / "Folith.exe", f'File.WriteAllText({_literal(marker)}, "new"); return 0;', "0.8.0")
    icon = base / "build/qt-windows/Folith.ico"
    icon.parent.mkdir(parents=True)
    Image.new("RGBA", (32, 32), "green").save(icon)
    source = HELPER.parents[2] / "packaging/folith-windows.nsi"
    script = tmp_path / "isolated-installer.nsi"
    # Only registration and shortcut namespace differ from production.
    script.write_text(source.read_text(encoding="utf-8-sig").replace(
        '!define APP_NAME "Folith"', f'!define APP_NAME "{test_name}"').replace(
        r'Uninstall\Folith"', 'Uninstall\\' + test_name + '"'), encoding="utf-8-sig")
    installer = tmp_path / "Folith-Setup-0.8.0-windows-x64.exe"
    subprocess.run([makensis, "-V2", "-DAPP_VERSION=0.8.0", "-DBASE=" + str(base),
                    "-DOUT_FILE=" + str(installer), str(script)], check=True, capture_output=True)
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(HELPER), "-ParentId", "99999999", "-Installer", str(installer),
            "-Sha256", hashlib.sha256(installer.read_bytes()).hexdigest(), "-TargetVersion", "0.8.0",
            "-InstallDir", str(install), "-DataDir", str(data), "-LogDir", str(logs)],
            check=True, capture_output=True, timeout=40)
        result = json.loads((logs / "update-result.json").read_text(encoding="utf-8-sig"))
        assert result["state"] == "installed", result
        assert (install / "_internal/runtime.txt").read_text() == "new runtime"
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.read_text() == "new"
        assert (data / "user.txt").read_text() == "preserved"
    finally:
        uninstaller = install / "Uninstall.exe"
        if uninstaller.exists():
            subprocess.run(f'"{uninstaller}" /S _?={install}', check=True, timeout=30)
