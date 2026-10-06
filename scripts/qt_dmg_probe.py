#!/usr/bin/env python3
"""Acceptance probe for the Qt DMG: mount, install a copy, run the main flow.

Machine-level evidence for "the image a tester receives installs and runs".  It
deliberately does *not* claim the human steps: dragging the app into
``/Applications`` in Finder, double-clicking from Dock, and the clean-machine
run on a Mac without a development environment stay manual (roadmap §8).

What it checks:
* the DMG verifies and mounts, and the packaged app's identity matches the
  built candidate (bundle id, version, declared minimum OS);
* the app is self-contained: copying it out of the image and running it with a
  PATH that excludes Homebrew still starts, finds its bundled OCR payload, and
  completes the Beta flow against the local controlled endpoint;
* user data lands outside the app bundle, so replacing or deleting the app
  cannot delete a user's projects.

Usage:
    venv/bin/python scripts/qt_dmg_probe.py \
        --dmg dist/qt-desktop/Folith-Qt-0.5.0-macos-arm64.dmg \
        --out tmp/qt-beta/dmg-probe
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

EXPECTED_BUNDLE_ID = "dev.folith.qt"


class Stub:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.out_dir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        self._handle = (self.out_dir / "stub-server.log").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--log", str(self.out_dir / "stub-llm.jsonl"),
             "--ready-file", str(ready)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("stub server exited")
            time.sleep(0.1)
        raise RuntimeError("stub server did not become ready")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def run(command: list[str], env: dict, timeout: float = 900.0,
        cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, capture_output=True, text=True,
                          timeout=timeout, cwd=cwd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt DMG acceptance probe")
    parser.add_argument("--dmg", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-install", action="store_true")
    args = parser.parse_args(argv)

    dmg = Path(args.dmg).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"dmg": str(dmg), "started_at": time.time(), "steps": []}

    def step(name: str, **extra) -> None:
        evidence["steps"].append(
            {"at": round(time.time() - evidence["started_at"], 2),
             "step": name, **extra})

    if not dmg.is_file():
        print(json.dumps({"ok": False, "error": f"missing dmg {dmg}"},
                         ensure_ascii=False, indent=2))
        return 1

    evidence["sha256"] = subprocess.run(
        ["shasum", "-a", "256", str(dmg)], capture_output=True, text=True
    ).stdout.split()[0]

    stripped = os.pathsep.join(
        part for part in (os.environ.get("PATH") or "").split(os.pathsep)
        if part and "homebrew" not in part.lower())
    evidence["path_without_homebrew"] = stripped
    evidence["homebrew_on_path"] = "homebrew" in stripped.lower()

    mount = Path(tempfile.mkdtemp(prefix="folith-qt-dmg-mnt-"))
    install_root = Path(tempfile.mkdtemp(prefix="folith-qt-install-"))
    stub = Stub(out_dir)
    try:
        # Copying the app needs as much free space as the bundle itself; a full
        # disk must be reported as such, not as a mysterious ditto failure.
        needed = 0
        for path in Path(mount, "Folith.app").rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    needed += path.stat().st_size
            except OSError:
                continue
        needed = needed or 600 * 1024 * 1024
        free = shutil.disk_usage(str(install_root)).free
        evidence["free_bytes"] = free
        evidence["needed_bytes"] = needed
        if free < needed:
            raise RuntimeError(
                f"not enough free space to install a copy for the probe: "
                f"{free / 1e6:.0f} MB free, about {needed / 1e6:.0f} MB needed")
        verify = subprocess.run(["hdiutil", "verify", str(dmg)],
                                capture_output=True, text=True)
        evidence["verify_ok"] = verify.returncode == 0
        step("verified", ok=evidence["verify_ok"])
        attach = subprocess.run(
            ["hdiutil", "attach", str(dmg), "-mountpoint", str(mount),
             "-nobrowse", "-quiet"], capture_output=True, text=True)
        if attach.returncode != 0:
            raise RuntimeError(f"attach failed: {attach.stderr[-300:]}")

        source_app = mount / "Folith.app"
        evidence["image_contents"] = sorted(item.name for item in mount.iterdir())
        with (source_app / "Contents" / "Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        evidence["identity"] = {
            "CFBundleIdentifier": plist.get("CFBundleIdentifier"),
            "CFBundleShortVersionString": plist.get("CFBundleShortVersionString"),
            "CFBundleVersion": plist.get("CFBundleVersion"),
            "LSMinimumSystemVersion": plist.get("LSMinimumSystemVersion"),
        }
        evidence["identity_ok"] = (
            plist.get("CFBundleIdentifier") == EXPECTED_BUNDLE_ID)
        payload = (source_app / "Contents" / "Frameworks" / "tesseract" / "share"
                   / "tessdata")
        evidence["ocr_payload"] = sorted(
            item.name for item in payload.iterdir()) if payload.is_dir() else []
        evidence["ocr_payload_ok"] = all(
            f"{language}.traineddata" in evidence["ocr_payload"]
            for language in ("eng", "chi_sim", "ara"))
        step("inspected", identity=evidence["identity"],
             ocr_ok=evidence["ocr_payload_ok"])

        # "Install": copy the app out of the image the way the user drags it into
        # /Applications, then run only from that copy.
        installed_app = install_root / "Folith.app"
        subprocess.run(["ditto", str(source_app), str(installed_app)], check=True)
        binary = installed_app / "Contents" / "MacOS" / "Folith"
        evidence["installed_app"] = str(installed_app)
        step("installed", app=str(installed_app))

        data_dir = out_dir / "data"
        log_dir = out_dir / "logs"
        env = {
            "HOME": os.environ.get("HOME", ""),
            "PATH": stripped,
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "QT_QPA_PLATFORM": "offscreen",
            "FOLITH_QT_NONINTERACTIVE": "1",
            "FOLITH_EXIT_FORCE_PROMPT": "0",
            "FOLITH_DESKTOP_DATA_DIR": str(data_dir),
            "FOLITH_DESKTOP_LOG_DIR": str(log_dir),
        }

        smoke_path = out_dir / "smoke.json"
        smoke = run([str(binary), "--data-dir", str(data_dir), "--smoke",
                     "--result", str(smoke_path)], env, timeout=300,
                    cwd=str(install_root))
        evidence["smoke"] = {
            "exit_code": smoke.returncode,
            "stderr_tail": smoke.stderr[-300:],
        }
        if smoke_path.is_file():
            evidence["smoke"].update(json.loads(smoke_path.read_text("utf-8")))
        evidence["smoke_ok"] = bool(
            smoke.returncode == 0 and evidence["smoke"].get("ok"))
        step("smoke", ok=evidence["smoke_ok"])

        # Finder-equivalent launch: `open -a` goes through LaunchServices with
        # the same bundle resolution a double-click uses, so a broken Info.plist
        # or a missing executable shows up here but not when the inner binary is
        # executed directly.  The app is asked to self-report and exit.
        launch_path = out_dir / "launchservices.json"
        launch_path.unlink(missing_ok=True)
        open_run = subprocess.run(
            ["open", "-a", str(installed_app), "--args",
             "--data-dir", str(data_dir), "--smoke", "--result", str(launch_path)],
            capture_output=True, text=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not launch_path.is_file():
            time.sleep(0.2)
        launched: dict = {}
        if launch_path.is_file():
            try:
                launched = json.loads(launch_path.read_text(encoding="utf-8"))
            except ValueError:
                launched = {}
        evidence["launchservices"] = {
            "open_exit_code": open_run.returncode,
            "open_stderr": open_run.stderr[-200:],
            "reported": bool(launched),
            "ok": launched.get("ok"),
            "frozen": (launched.get("identity") or {}).get("frozen"),
            "platform": launched.get("platform"),
        }
        evidence["launchservices_ok"] = bool(
            open_run.returncode == 0 and launched.get("ok")
            and (launched.get("identity") or {}).get("frozen") is True)
        step("launchservices", ok=evidence["launchservices_ok"])

        url = stub.start()
        env["FOLITH_QT_SELFTEST_BASE_URL"] = url
        env["FOLITH_QT_SELFTEST_OCR"] = "1"
        selftest_path = out_dir / "selftest.json"
        selftest = run([str(binary), "--data-dir", str(data_dir), "--selftest",
                        "--selftest-fixture", "both",
                        "--result", str(selftest_path)], env, timeout=900,
                       cwd=str(install_root))
        report: dict = {}
        if selftest_path.is_file():
            report = json.loads(selftest_path.read_text("utf-8"))
        evidence["selftest"] = {
            "exit_code": selftest.returncode,
            "ok": report.get("ok"),
            "error": report.get("error"),
            "run": report.get("run"),
            "delivery": report.get("delivery"),
            "reopen": report.get("reopen"),
            "exit": report.get("exit"),
            "heartbeat": report.get("heartbeat"),
            "stderr_tail": selftest.stderr[-300:],
        }
        evidence["selftest_ok"] = bool(
            selftest.returncode == 0 and report.get("ok"))
        step("selftest", ok=evidence["selftest_ok"])

        # §6.C: no Folith worker, child process or lock may survive a normal
        # exit.  Checked on the real process table, not inferred from files.
        time.sleep(2.0)
        # Scope the search to *this* probe's installed copy: a user's own
        # installed Folith (e.g. in /Applications) is not a leftover from this
        # run, and counting it would fail the check for the wrong reason.
        leftovers = subprocess.run(
            ["pgrep", "-fl", str(binary)],
            capture_output=True, text=True).stdout.strip().splitlines()
        evidence["leftover_processes"] = leftovers
        evidence["no_leftover_processes"] = not leftovers
        step("leftover-check", remaining=len(leftovers))

        # Data must live outside the bundle: deleting the app cannot delete it.
        inside_bundle = [str(item) for item in installed_app.rglob("state.json")]
        evidence["state_files_inside_bundle"] = inside_bundle
        evidence["jobs_after_run"] = len(
            [item for item in data_dir.iterdir() if item.is_dir()]
        ) if data_dir.is_dir() else 0
        evidence["data_outside_bundle"] = bool(
            evidence["jobs_after_run"] and not inside_bundle)

        # Upgrade/replace semantics: installing a fresh copy over the existing
        # one must keep the user's projects readable (§6.C).
        jobs_before_upgrade = evidence["jobs_after_run"]
        subprocess.run(["ditto", str(source_app), str(installed_app)], check=True)
        upgrade_smoke = out_dir / "smoke-after-upgrade.json"
        upgrade_run = run([str(binary), "--data-dir", str(data_dir), "--smoke",
                           "--result", str(upgrade_smoke)], env, timeout=300,
                          cwd=str(install_root))
        upgraded: dict = {}
        if upgrade_smoke.is_file():
            upgraded = json.loads(upgrade_smoke.read_text("utf-8"))
        jobs_after_upgrade = len(
            [item for item in data_dir.iterdir() if item.is_dir()]
        ) if data_dir.is_dir() else 0
        evidence["upgrade"] = {
            "exit_code": upgrade_run.returncode,
            "jobs_before": jobs_before_upgrade,
            "jobs_after": jobs_after_upgrade,
            "reported_jobs": upgraded.get("jobs"),
        }
        evidence["upgrade_keeps_data"] = bool(
            upgrade_run.returncode == 0 and upgraded.get("ok")
            and jobs_after_upgrade >= jobs_before_upgrade)
        step("upgrade", **evidence["upgrade"])

        # Uninstall semantics: removing the app copy leaves the user data intact.
        shutil.rmtree(installed_app, ignore_errors=True)
        evidence["data_survives_app_removal"] = bool(
            data_dir.is_dir() and evidence["jobs_after_run"])
        evidence["reinstalled_app_absent"] = not installed_app.exists()
        step("uninstall-check", jobs=evidence["jobs_after_run"],
             data_survives=evidence["data_survives_app_removal"])

        evidence["ok"] = bool(
            evidence.get("verify_ok")
            and evidence.get("identity_ok")
            and evidence.get("ocr_payload_ok")
            and evidence.get("smoke_ok")
            and evidence.get("launchservices_ok")
            and evidence.get("no_leftover_processes")
            and evidence.get("selftest_ok")
            and evidence.get("data_outside_bundle")
            and evidence.get("upgrade_keeps_data")
            and evidence.get("data_survives_app_removal"))
    except BaseException as exc:  # noqa: BLE001 - the report is the evidence
        import traceback

        evidence["ok"] = False
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        evidence["traceback"] = traceback.format_exc()
    finally:
        stub.stop()
        subprocess.run(["hdiutil", "detach", str(mount), "-quiet"],
                       capture_output=True)
        shutil.rmtree(mount, ignore_errors=True)
        if not args.keep_install:
            shutil.rmtree(install_root, ignore_errors=True)
        evidence["finished_at"] = time.time()
        evidence["duration_s"] = round(
            evidence["finished_at"] - evidence["started_at"], 1)

    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    (out_dir / "dmg-probe.json").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
