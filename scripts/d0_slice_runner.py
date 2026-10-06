#!/usr/bin/env python3
"""D0 vertical-slice runner: real service + real UI + controlled model responses.

Runs the roadmap §6.1 slice against either the frozen artifact or the source
tree, through the same launcher path used by the desktop app:

    fixtures -> local OpenAI-compatible stub -> verified Folith service ->
    browser-driven UI (import / task / edit / commit / review / delivery /
    download) -> on-disk verification -> clean shutdown -> reopen verification

Nothing is injected into session state or job state; the only non-UI actions are
starting the service, writing the fixture files, and reading the results.

Usage:
    venv/bin/python scripts/d0_slice_runner.py \
        --source-kind docx --out /tmp/d0-slice --review \
        [--frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

PLAYWRIGHT_CANDIDATES = (
    "~/Dev/grok-workspace/node_modules/playwright-core",
    "~/.hermes/hermes-agent/node_modules/playwright-core",
)
CHROME_GLOBS = (
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/*.app/Contents/MacOS/*",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def resolve_playwright() -> str | None:
    env = os.environ.get("FOLIO_PLAYWRIGHT_CORE")
    if env and Path(env).exists():
        return env
    for candidate in PLAYWRIGHT_CANDIDATES:
        path = Path(candidate).expanduser()
        if (path / "package.json").exists():
            return str(path)
    return None


def resolve_chrome() -> str | None:
    env = os.environ.get("FOLIO_CHROME")
    if env and Path(env).exists():
        return env
    for pattern in CHROME_GLOBS:
        matches = sorted(glob.glob(str(Path(pattern).expanduser())))
        if matches:
            return matches[0]
    return None


def resolve_node() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    for candidate in ("~/.local/bin/node", "/usr/local/bin/node", "/opt/homebrew/bin/node"):
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    return None


# Kept alive so the child's log file descriptor is never closed early by GC.
_STUB_LOG_HANDLES: list = []


def start_stub_llm(out_dir: Path, *, delay: float = 0.0,
                   name: str = "stub") -> tuple[subprocess.Popen, str, Path]:
    """Start the controlled model endpoint.

    The server's stdout/stderr go to a file, not a pipe: `BaseHTTPRequestHandler`
    logs every request to stderr, and an unread pipe fills up (~64 KB), which
    blocks the server mid-run and surfaces as a misleading "Connection error" in
    the app under test.
    """
    ready = out_dir / f"{name}-ready.json"
    log = out_dir / "stub-llm.jsonl"
    server_log = out_dir / f"{name}-server.log"
    handle = server_log.open("ab")
    _STUB_LOG_HANDLES.append(handle)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
         "--port", "0", "--log", str(log), "--ready-file", str(ready),
         "--delay", str(delay)],
        stdout=handle, stderr=subprocess.STDOUT,
    )
    for _ in range(200):
        if ready.exists():
            info = json.loads(ready.read_text(encoding="utf-8"))
            return proc, info["url"], log
        if proc.poll() is not None:
            tail = ""
            try:
                tail = server_log.read_text(encoding="utf-8", errors="replace")[-500:]
            except OSError:
                pass
            raise RuntimeError(f"stub LLM server exited: {tail}")
        time.sleep(0.1)
    raise RuntimeError("stub LLM server did not become ready")


def prepare_tessdata(out_dir: Path, langs: list[str]) -> Path | None:
    """Assemble the OCR language set D0 is allowed to use (eng/chi_sim/ara).

    Tesseract's `tsv` output mode is a *config file* (`configs/tsv`), not a flag,
    so a language-only tessdata directory makes `--psm N tsv` fail with
    "read_params_file: Can't open tsv" and the app then reports "no confidence
    text" instead of a clear configuration error.  The config directories must
    travel with the language packs.
    """
    sources = {
        "eng": Path("/opt/homebrew/share/tessdata/eng.traineddata"),
        "chi_sim": Path("/opt/homebrew/share/tessdata/chi_sim.traineddata"),
        "osd": Path("/opt/homebrew/share/tessdata/osd.traineddata"),
        "ara": ROOT / "benchmark" / "tessdata_fast" / "ara.traineddata",
    }
    target = out_dir / "tessdata"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for lang in langs:
        source = sources.get(lang)
        if source is None or not source.exists():
            return None
        destination = target / f"{lang}.traineddata"
        if not destination.exists():
            shutil.copy2(source, destination)
        copied.append(lang)
    # tsv/hocr/pdf output configs and the auxiliary tessdata files.
    for extra in ("configs", "tessconfigs"):
        source_dir = Path("/opt/homebrew/share/tessdata") / extra
        if source_dir.is_dir():
            shutil.copytree(source_dir, target / extra, dirs_exist_ok=True)
    for extra_file in ("pdf.ttf",):
        source_file = Path("/opt/homebrew/share/tessdata") / extra_file
        if source_file.exists() and not (target / extra_file).exists():
            shutil.copy2(source_file, target / extra_file)
    return target if copied else None


def docx_contains(path: Path, needle: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                text = archive.read(name).decode("utf-8", errors="replace")
                if needle in text:
                    return True
    return False


def inspect_state(data_dir: Path, job_id: str) -> dict:
    state_path = data_dir / job_id / "state.json"
    if not state_path.exists():
        return {"state_file": str(state_path), "exists": False}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pairs = state.get("pairs") or state.get("segments") or []
    human_edited = [
        index for index, pair in enumerate(pairs)
        if isinstance(pair, dict) and pair.get("human_edited")
    ]
    actions = state.get("human_actions") or []
    decisions = [
        item for item in actions
        if isinstance(item, dict) and item.get("record_type") == "human_decision"
    ]
    snapshots = sorted((data_dir / job_id / "delivery_snapshots").glob("v*")) \
        if (data_dir / job_id / "delivery_snapshots").exists() else []
    return {
        "state_file": str(state_path),
        "exists": True,
        "pair_count": len(pairs),
        "human_edited_indexes": human_edited,
        "human_decisions": len(decisions),
        "delivery_status": state.get("delivery_status"),
        "review_required": state.get("translation_core_review_required"),
        "snapshot_versions": [item.name for item in snapshots],
    }


def _bundle_tesseract_payload(app: Path) -> Path | None:
    """Locate the OCR payload shipped inside a frozen .app bundle."""
    bundle = app
    while bundle.suffix != ".app" and bundle != bundle.parent:
        bundle = bundle.parent
    for relative in ("Contents/Frameworks/tesseract", "Contents/Resources/tesseract"):
        candidate = bundle / relative
        if (candidate / "bin" / "tesseract").is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the D0 desktop vertical slice")
    import d0_fixtures

    parser.add_argument("--source-kind", default="docx",
                        choices=sorted(d0_fixtures.BUILDERS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", default=None)
    parser.add_argument("--url", default=None,
                        help="run against an already-running instance (e.g. Finder-launched app)")
    parser.add_argument("--data-dir", default=None,
                        help="data dir of that instance (defaults to the macOS/Windows default)")
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--target-lang", default="简体中文")
    parser.add_argument("--source-name", default=None,
                        help="rename the generated source file (Chinese / space paths)")
    parser.add_argument("--edit-text", default=None,
                        help="override the committed edit text (e.g. Arabic for RTL)")
    parser.add_argument("--ocr-language", default=None)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    parser.add_argument("--stub-delay", type=float, default=0.0,
                        help="seconds per controlled model response (keeps a job running)")
    parser.add_argument("--fragment-probe", action="store_true",
                        help="observe fragment-isolated runtime polling instead of the full slice")
    parser.add_argument("--skip-reopen", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_kind": args.source_kind,
        "frozen": bool(args.frozen_app),
        "review": bool(args.review),
    }

    node = resolve_node()
    playwright = resolve_playwright()
    chrome = resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable",
                          "node": node, "playwright": playwright, "chrome": chrome}, indent=2))
        return 2
    evidence["automation"] = {"node": node, "playwright_core": playwright, "chrome": chrome}

    manifest = d0_fixtures.build([args.source_kind], out_dir / "fixtures")
    source = Path(manifest["inputs"][args.source_kind]["path"])
    if args.source_name:
        renamed = source.with_name(args.source_name)
        source.rename(renamed)
        source = renamed
        manifest["inputs"][args.source_kind]["path"] = str(renamed)
        manifest["inputs"][args.source_kind]["renamed_from"] = source.name
    evidence["fixtures"] = manifest

    if args.frozen_app:
        os.environ["FOLITH_DESKTOP_EXECUTABLE"] = str(Path(args.frozen_app).expanduser())
    os.environ[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    os.environ[desktop_paths.LOG_DIR_ENV] = str(log_dir)

    if args.frozen_app:
        # Verify the payload the *bundle* ships, not a harness-assembled dir:
        # the launcher normally sets these, but here the harness (source mode)
        # builds the service environment, so it must point at the bundle itself.
        payload = _bundle_tesseract_payload(Path(args.frozen_app).expanduser())
        if payload is not None:
            os.environ["FOLIOTHREAD_TESSERACT_CMD"] = str(payload / "bin" / "tesseract")
            os.environ["TESSDATA_PREFIX"] = str(payload / "share" / "tessdata")
            evidence["ocr_payload"] = str(payload)
        else:
            evidence["ocr_payload"] = "not-found-in-bundle"
    else:
        tessdata = prepare_tessdata(out_dir, ["eng", "chi_sim", "ara", "osd"])
        if tessdata is not None:
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
            evidence["tessdata"] = str(tessdata)
    # A machine may legitimately have no system tesseract (the desktop build ships
    # its own); the harness must record that instead of crashing.
    try:
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                               text=True, check=False)
        evidence["tesseract_langs"] = [line.strip() for line in langs.stdout.splitlines()[1:]
                                       if line.strip()]
        evidence["tesseract_source"] = shutil.which("tesseract") or ""
    except FileNotFoundError:
        evidence["tesseract_langs"] = []
        evidence["tesseract_source"] = "not-on-PATH (desktop build is expected to bundle it)"

    stub, stub_url, stub_log = start_stub_llm(out_dir, delay=args.stub_delay)
    evidence["stub_llm_url"] = stub_url
    external = bool(args.url)
    if external:
        # Ordinary-desktop-launch mode: use the instance the user/OS started
        # (Finder, Start Menu) instead of one this harness spawned.  Its data
        # directory and PATH are then the real desktop ones (roadmap §6.1/§14).
        data_dir = Path(args.data_dir).expanduser() if args.data_dir \
            else desktop_paths.resolve_paths().data_dir
        log_dir = Path(args.log_dir).expanduser() if args.log_dir \
            else desktop_paths.resolve_paths().log_dir
        service = None
        evidence["external_instance"] = {"url": args.url, "data_dir": str(data_dir),
                                         "log_dir": str(log_dir)}
        evidence["url"] = args.url
    else:
        service = desktop_launcher.DesktopService(
            paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
            ready_timeout=args.ready_timeout,
        )
    slice_result_path = out_dir / "slice.json"
    try:
        if service is not None:
            launch = service.start()
            evidence["service"] = launch.evidence.as_dict()
            evidence["url"] = launch.url
        else:
            launch = type("L", (), {"url": args.url})()

        command = [
            node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
            "--url", launch.url,
            "--source", str(source),
            "--out-dir", str(out_dir),
            "--stub-url", stub_url,
            "--result", str(slice_result_path),
            "--target-lang", args.target_lang,
        ]
        if args.review:
            command.append("--review")
        if args.fragment_probe:
            command.append("--fragment-probe")
        if args.edit_text:
            command.extend(["--edit-text", args.edit_text])
        if args.ocr_language:
            command.extend(["--ocr-language", args.ocr_language])
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        slice_run = subprocess.run(command, capture_output=True, text=True, env=env, timeout=3600)
        evidence["slice_exit_code"] = slice_run.returncode
        evidence["slice_stdout_tail"] = slice_run.stdout[-4000:]
        evidence["slice_stderr_tail"] = slice_run.stderr[-2000:]
        if slice_result_path.exists():
            payload = json.loads(slice_result_path.read_text(encoding="utf-8"))
            evidence["slice"] = {
                "ok": payload.get("ok"),
                "job_id": payload.get("job_id"),
                "steps": payload.get("steps"),
                "alerts": payload.get("alerts"),
                "page_errors": payload.get("page_errors"),
                "download": payload.get("download"),
                "edit_text": payload.get("edit_text"),
                "error": payload.get("error"),
            }
            job_id = payload.get("job_id")
            edit_text = payload.get("edit_text")
            download = payload.get("download") or {}
        else:
            job_id = None
            edit_text = None
            download = {}
            evidence["slice"] = {"ok": False, "error": "no slice result written"}

        verification: dict = {"download_contains_edit": None, "snapshot": None,
                              "state": None, "provider_config": None}
        if job_id:
            verification["state"] = inspect_state(data_dir, job_id)
            snapshot_dir = data_dir / job_id / "delivery_snapshots"
            versions = sorted(snapshot_dir.glob("v*")) if snapshot_dir.exists() else []
            if versions:
                snapshot_docx = versions[-1] / "translation.docx"
                verification["snapshot"] = {
                    "dir": str(versions[-1]),
                    "translation_docx": str(snapshot_docx),
                    "exists": snapshot_docx.exists(),
                    "contains_edit": docx_contains(snapshot_docx, edit_text) if snapshot_docx.exists() else None,
                    "manifest": (versions[-1] / "snapshot_manifest.json").exists(),
                }
            if download.get("path"):
                verification["download_contains_edit"] = docx_contains(Path(download["path"]), edit_text)
        provider_config = data_dir / "provider_config.json"
        if provider_config.exists():
            config = json.loads(provider_config.read_text(encoding="utf-8"))
            verification["provider_config"] = {
                "path": str(provider_config),
                "provider": config.get("provider"),
                "base_url": config.get("base_url"),
                "model": config.get("model"),
                "api_key_is_stub": config.get("api_key") == "d0-local",
            }
        evidence["verification"] = verification
        evidence["stub_requests"] = _stub_summary(stub_log)
    finally:
        if service is None:
            evidence["shutdown"] = {"external_instance": True}
        else:
            stop = service.stop()
            evidence["shutdown"] = {
                "acknowledged": stop.acknowledged,
                "child_gone": stop.launcher_child_gone,
                "service_gone": stop.service_pid_gone,
                "port_released": stop.port_released,
                "residual_pids": stop.residual_pids,
                "forced": stop.forced,
            }
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            stub.kill()

    if not args.skip_reopen and evidence.get("slice", {}).get("job_id") and service is not None:
        evidence["reopen"] = _reopen_check(
            out_dir, node, playwright, chrome, evidence, args
        )

    evidence["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    shutdown = evidence.get("shutdown", {})
    ok = bool(
        evidence.get("slice", {}).get("ok")
        and evidence.get("verification", {}).get("download_contains_edit")
        and evidence.get("verification", {}).get("snapshot", {}).get("contains_edit")
        and (shutdown.get("external_instance") or shutdown.get("service_gone"))
        and not shutdown.get("residual_pids")
        and (evidence.get("reopen", {}).get("editor_contains_edit", True))
    )
    evidence["ok"] = ok
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "ok": ok,
        "job_id": evidence.get("slice", {}).get("job_id"),
        "verification": evidence.get("verification"),
        "shutdown": evidence.get("shutdown"),
        "reopen": evidence.get("reopen"),
        "evidence": str(out_dir / "evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _stub_summary(log: Path) -> dict:
    if not log.exists():
        return {"requests": 0}
    kinds: dict[str, int] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kinds[entry.get("kind", "?")] = kinds.get(entry.get("kind", "?"), 0) + 1
    return {"requests": sum(kinds.values()), "by_kind": kinds, "log": str(log)}


def _reopen_check(out_dir: Path, node: str, playwright: str, chrome: str,
                  evidence: dict, args) -> dict:
    """Restart the service on the same data dir and reopen the job through the UI."""
    job_id = evidence["slice"]["job_id"]
    edit_text = evidence["slice"]["edit_text"]
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=out_dir / "data", log_dir=out_dir / "logs"),
        ready_timeout=args.ready_timeout,
    )
    result_path = out_dir / "reopen.json"
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_reopen.js"),
             "--url", launch.url, "--job", job_id, "--expect", edit_text,
             "--out-dir", str(out_dir), "--result", str(result_path)],
            capture_output=True, text=True, env=env, timeout=900,
        )
        payload = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        return {
            "exit_code": run.returncode,
            "editor_contains_edit": payload.get("editor_contains_edit"),
            "editor_excerpt": payload.get("editor_excerpt"),
            "job_visible": payload.get("job_visible"),
            "stderr_tail": run.stderr[-1000:],
        }
    finally:
        stop = service.stop()
        evidence.setdefault("reopen_shutdown", {})
        evidence["reopen_shutdown"] = {
            "service_gone": stop.service_pid_gone,
            "port_released": stop.port_released,
            "residual_pids": stop.residual_pids,
        }


if __name__ == "__main__":
    sys.exit(main())
