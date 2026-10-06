#!/usr/bin/env python3
"""Qt Beta OCR baseline: eng / chi_sim / ara from the *bundled* engine.

Runs the frozen Qt app with a PATH that excludes Homebrew, on real scanned pages
(image-only, 300 DPI, correctly shaped Arabic/CJK), and checks the recognised
source text.  OCR can therefore only succeed through the payload inside
`Folith.app`.

The Qt candidate's OCR path is the same core code the old shell used, but the
claim "works without Homebrew" has to be re-proved for *this* artifact.

Usage:
    venv/bin/python scripts/qt_ocr_baseline_runner.py --out /tmp/qt-ocr \
        --frozen-app dist/qt-desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CASES = (
    {"kind": "pdf_scanned", "engine_code": "eng", "ocr_language": "eng",
     "expect": "Ecological restoration"},
    {"kind": "pdf_chinese_scanned", "engine_code": "chi_sim",
     "ocr_language": "简体中文", "expect": "生态恢复"},
    {"kind": "pdf_arabic_scanned", "engine_code": "ara",
     "ocr_language": "العربية", "expect": "استعادة"},
)


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


def source_text(data_dir: Path, job_id: str) -> str:
    try:
        state = json.loads((data_dir / job_id / "state.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    pairs = state.get("pairs") or []
    if pairs:
        return "\n".join(str(pair.get("source") or "") for pair in pairs
                         if isinstance(pair, dict))
    return "\n".join(str(item) for item in state.get("paras") or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt bundled OCR baseline")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--python", default=None,
                        help="source interpreter used to build the fixtures")
    parser.add_argument("--with-negative", dest="with_negative",
                        action="store_true", default=True,
                        help="also verify that a missing language pack fails loudly")
    parser.add_argument("--no-negative", dest="with_negative",
                        action="store_false")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    python = args.python or sys.executable

    # Strip Homebrew (and any other user toolchain) from the app's PATH.
    stripped = os.pathsep.join(
        part for part in (os.environ.get("PATH") or "").split(os.pathsep)
        if part and "homebrew" not in part.lower())
    evidence: dict = {
        "app": str(Path(args.frozen_app).expanduser()),
        "path_without_homebrew": stripped,
        "homebrew_on_path": "homebrew" in stripped.lower(),
        "cases": {},
    }

    sys.path.insert(0, str(ROOT / "scripts"))
    import d0_fixtures  # noqa: PLC0415  (source-only fixture builders)

    stub = Stub(out_dir)
    url = stub.start()
    try:
        for case in CASES:
            case_dir = out_dir / case["ocr_language"]
            case_dir.mkdir(parents=True, exist_ok=True)
            manifest = d0_fixtures.build([case["kind"]], case_dir / "fixtures")
            fixture = Path(manifest["inputs"][case["kind"]]["path"])
            data_dir = case_dir / "data"
            # A fresh data dir per case: the fixture is re-rasterised every run,
            # so a leftover task from a previous run would make the job under
            # test ambiguous.
            shutil.rmtree(data_dir, ignore_errors=True)
            result_path = case_dir / "report.json"
            env = {
                "HOME": os.environ.get("HOME", ""),
                "PATH": stripped,
                "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
                "QT_QPA_PLATFORM": "offscreen",
                "FOLITH_QT_NONINTERACTIVE": "1",
                "FOLITH_EXIT_FORCE_PROMPT": "0",
                "FOLITH_QT_SELFTEST_BASE_URL": url,
                "FOLITH_QT_SELFTEST_OCR": "1",
                "FOLITH_QT_SELFTEST_OCR_LANGUAGE": case["ocr_language"],
                "FOLITH_DESKTOP_DATA_DIR": str(data_dir),
                "FOLITH_DESKTOP_LOG_DIR": str(case_dir / "logs"),
            }
            command = [
                str(Path(args.frozen_app).expanduser()),
                "--data-dir", str(data_dir),
                "--selftest", "--selftest-source", str(fixture),
                "--result", str(result_path),
            ]
            started = time.monotonic()
            run = subprocess.run(command, env=env, capture_output=True, text=True,
                                 timeout=900, cwd=str(out_dir))
            case_evidence: dict = {
                "kind": case["kind"],
                "fixture": str(fixture),
                "ocr_language": case["ocr_language"],
                "expected_substring": case["expect"],
                "exit_code": run.returncode,
                "seconds": round(time.monotonic() - started, 1),
            }
            report: dict = {}
            if result_path.is_file():
                try:
                    report = json.loads(result_path.read_text(encoding="utf-8"))
                except ValueError:
                    report = {}
            # Take the job id from the run's own report rather than guessing
            # which directory in the data dir belongs to this run.
            job_id = str(((report.get("job_ids") or [""])[0]) or "")
            text = source_text(data_dir, job_id) if job_id else ""
            state_path = data_dir / job_id / "state.json" if job_id else None
            ocr_config: dict = {}
            if state_path is not None and state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    ocr_config = state.get("ocr_config") or {}
                except ValueError:
                    ocr_config = {}
            # Tesseract inserts spaces between CJK glyphs ("生态 恢复 的"), so the
            # expectation is matched with whitespace removed as well.  The
            # backend and language code are checked too: a silent fall back to
            # English would otherwise still "find" the English expectation.
            case_evidence["recognised_source"] = text[:600]
            case_evidence["expected_found"] = (
                case["expect"] in text
                or case["expect"] in re.sub(r"\s+", "", text))
            case_evidence["ocr_backend"] = ocr_config.get("ocr_backend")
            case_evidence["ocr_language_engine_code"] = ocr_config.get(
                "ocr_language_engine_code")
            case_evidence["segments"] = len(
                ((report.get("run") or {}).get("paras") or []))
            case_evidence["ocr_languages_installed"] = (
                (report.get("ocr") or {}).get("languages_installed"))
            case_evidence["ok"] = bool(
                run.returncode == 0
                and case_evidence["expected_found"]
                and case_evidence["ocr_backend"] == "tesseract"
                and case_evidence["ocr_language_engine_code"]
                == case["engine_code"])
            if not case_evidence["ok"]:
                case_evidence["stdout_tail"] = run.stdout[-500:]
                case_evidence["stderr_tail"] = run.stderr[-500:]
            evidence["cases"][case["kind"]] = case_evidence
            print(json.dumps(case_evidence, ensure_ascii=False, indent=2))

        if args.with_negative:
            evidence["missing_pack_case"] = _missing_pack_case(
                out_dir, args, python, stub.url, stripped)
    finally:
        stub.stop()

    negative = evidence.get("missing_pack_case") or {}
    evidence["ok"] = bool(evidence["cases"]) and all(
        item.get("ok") for item in evidence["cases"].values()) \
        and (not args.with_negative or negative.get("ok") is True)
    evidence["finished_at"] = time.time()
    (out_dir / "ocr-baseline.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if evidence["ok"] else 1


def _missing_pack_case(out_dir: Path, args, python: str, url: str,
                       stripped: str) -> dict:
    """A requested language we do not ship must fail loudly, never fall back.

    Roadmap §6.D: "缺包准确报错，不静默改用英语".  The fixture is an English
    scanned page, so a silent fallback to English would look like success — the
    case is built so that the *only* acceptable outcome is a blocking error that
    names the missing pack.
    """
    import d0_fixtures  # noqa: PLC0415  (source-only fixture builders)

    case_dir = out_dir / "missing-pack"
    case_dir.mkdir(parents=True, exist_ok=True)
    manifest = d0_fixtures.build(["pdf_scanned"], case_dir / "fixtures")
    fixture = Path(manifest["inputs"]["pdf_scanned"]["path"])
    data_dir = case_dir / "data"
    shutil.rmtree(data_dir, ignore_errors=True)
    result_path = case_dir / "report.json"
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": stripped,
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "QT_QPA_PLATFORM": "offscreen",
        "FOLITH_QT_NONINTERACTIVE": "1",
        "FOLITH_EXIT_FORCE_PROMPT": "0",
        "FOLITH_QT_SELFTEST_BASE_URL": url,
        "FOLITH_QT_SELFTEST_OCR": "1",
        # Deutsch is deliberately not bundled (baseline is eng/chi_sim/ara).
        "FOLITH_QT_SELFTEST_OCR_LANGUAGE": "Deutsch",
        "FOLITH_DESKTOP_DATA_DIR": str(data_dir),
        "FOLITH_DESKTOP_LOG_DIR": str(case_dir / "logs"),
    }
    run = subprocess.run(
        [str(Path(args.frozen_app).expanduser()),
         "--data-dir", str(data_dir), "--selftest",
         "--selftest-source", str(fixture), "--result", str(result_path)],
        env=env, capture_output=True, text=True, timeout=900, cwd=str(out_dir))
    report: dict = {}
    if result_path.is_file():
        try:
            report = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            report = {}
    job_id = str(((report.get("job_ids") or [""])[0]) or "")
    text = source_text(data_dir, job_id) if job_id else ""
    runtime = (report.get("run") or {})
    error = runtime.get("error")
    message = str((error or {}).get("message") if isinstance(error, dict)
                  else error or "")
    # The scan page says "Ecological restoration…"; if that text appears, the
    # app OCR'd with English despite the explicit missing selection.
    english_leak = "Ecological restoration" in text
    case = {
        "kind": "pdf_scanned",
        "requested_language": "Deutsch",
        "requested_engine_code": "deu",
        "exit_code": run.returncode,
        "selftest_ok": report.get("ok"),
        "runtime_status": runtime.get("runtime_status"),
        "error_message": message,
        "p1_done": runtime.get("p1_done"),
        "p2_done": runtime.get("p2_done"),
        "recognised_source": text[:300],
        "english_fallback_detected": english_leak,
        "mentions_missing_pack": ("语言包未安装" in message
                                  or "language_pack_missing" in message
                                  or "deu" in message),
        "p1_done_is_false": runtime.get("p1_done") is False,
    }
    # Acceptable: the run fails, the reason names the missing pack, and no
    # English text was produced.  The exit code is expected to be non-zero.
    case["ok"] = bool(case["mentions_missing_pack"]
                      and not english_leak
                      and case["p1_done_is_false"]
                      and run.returncode != 0)
    if not case["ok"]:
        case["stdout_tail"] = run.stdout[-500:]
        case["stderr_tail"] = run.stderr[-500:]
    print(json.dumps(case, ensure_ascii=False, indent=2))
    return case


if __name__ == "__main__":
    sys.exit(main())
