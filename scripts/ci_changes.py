#!/usr/bin/env python3
"""Classify a pull request's changed files for the tiered CI (PR Fast Checks).

Writes ``key=value`` lines to ``$GITHUB_OUTPUT`` (or stdout) and a short
explanation to ``$GITHUB_STEP_SUMMARY``:

    code       true when anything other than documentation changed; the full
               Linux regression, the shared Qt platform checks and the package
               smoke then run.
    risk       true when persistence, task recovery, state migration, locking or
               concurrency code changed; the Windows risk regression then runs.
    risk_tests the ``run_regression.py -k`` needles for that risk regression.
    packaging  true when the desktop bundle, DMG or Windows installer inputs
               changed; Release Validation then runs on the pull request too.
    docs_tests the ``-k`` needles for a documentation-only PR: every test file
               that reads ``docs/`` or a README, because several tests check
               evidence logs, acceptance documents and the README against code.

The classifier only ever widens what runs: an unknown path counts as code, and a
change to the CI itself (workflows, this script, the regression runner) counts as
both code and risk so the new pipeline exercises every branch it defines.

Usage: ``python scripts/ci_changes.py <base-sha> <head-sha>``
       ``python scripts/ci_changes.py --files a.py docs/b.md``  (local check)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOC_PATTERNS = (
    re.compile(r"^docs/"),
    re.compile(r"^promo/"),
    re.compile(r"^[^/]+\.md$"),
    re.compile(r"^LICENSE"),
    re.compile(r"^\.gitignore$"),
)

CI_PATTERNS = (
    re.compile(r"^\.github/"),
    re.compile(r"^scripts/run_regression\.py$"),
    re.compile(r"^scripts/ci_changes\.py$"),
)

PACKAGING_PATH = re.compile(
    r"^(packaging/|requirements[^/]*\.txt$|pyproject\.toml$|folith/resources/brand/|"
    r"scripts/(build_qt_|build_app_icon|check_release_archives|check_windows_|"
    r"qt_(desktop|dmg)_probe|qt_ocr_baseline_runner|qt_release_checks|"
    r"qt_acceptance_kit|release_preflight|stage_tesseract_payload|"
    r"write_desktop_build_info|write_release_checksums|create_update_manifest|embed_macos_updater|prepare_macos_update)|"
    r"folith/(desktop/(updates\.py|install-update\.ps1)|qt/(updater|sparkle)\.py))"
)

# Source paths whose changes can corrupt or lose user data, or break resuming a
# task: state files and journals, storage and locks, migrations, recovery and
# resume, schedulers and workers, atomic writes.
RISK_PATH = re.compile(
    r"(state|journal|persist|storage|store_lock|job_lock|migrat|recover|resume|"
    r"checkpoint|snapshot|atomic|concurren|schedul|worker|runtime|lock|"
    r"knowledge_cache|ocr_cache|desktop/)",
    re.I,
)
RISK_TESTS = (
    "state_", "journal", "persistence", "storage", "migration", "recovery",
    "resume", "runtime_", "lock", "atomic", "concurrency", "scheduler",
    "worker", "checkpoint", "snapshot", "task_overview_state", "knowledge_async",
)

DOC_TEST_MARKERS = re.compile(r'"docs"|docs/|README|release-notes')


def _is_doc(path: str) -> bool:
    return any(p.search(path) for p in DOC_PATTERNS)


def _docs_tests() -> list[str]:
    return sorted(
        f.name for f in (ROOT / "tests").glob("*_test.py")
        if DOC_TEST_MARKERS.search(f.read_text(encoding="utf-8", errors="replace"))
    )


def classify(files: list[str]) -> dict[str, str]:
    ci = any(p.search(f) for f in files for p in CI_PATTERNS)
    code = ci or any(not _is_doc(f) for f in files) or not files
    risk = ci or any(
        RISK_PATH.search(f) for f in files
        if f.endswith(".py") and not f.startswith("tests/") and not _is_doc(f)
    ) or any(
        f.startswith("tests/") and any(n in Path(f).name for n in RISK_TESTS)
        for f in files
    )
    return {
        "code": str(code).lower(),
        "risk": str(risk).lower(),
        "packaging": str(any(PACKAGING_PATH.search(f) for f in files)).lower(),
        "risk_tests": ",".join(RISK_TESTS),
        "docs_tests": ",".join(_docs_tests()),
        "ci": str(ci).lower(),
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["--files"]:
        files = argv[1:]
    elif len(argv) == 2:
        out = subprocess.run(["git", "diff", "--name-only", f"{argv[0]}...{argv[1]}"],
                             cwd=ROOT, check=True, capture_output=True, text=True)
        files = [line for line in out.stdout.splitlines() if line]
    else:
        print(__doc__, file=sys.stderr)
        return 2

    result = classify(files)
    lines = [f"{k}={v}" for k, v in result.items()]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        mode = "code" if result["code"] == "true" else "documentation only"
        shown = "\n".join(f"- `{f}`" for f in files[:40])
        more = f"\n- … {len(files) - 40} more" if len(files) > 40 else ""
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Change classification: {mode}\n\n"
                     f"risk regression: **{result['risk']}**, CI changed: "
                     f"**{result['ci']}**, {len(files)} file(s)\n\n{shown}{more}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
