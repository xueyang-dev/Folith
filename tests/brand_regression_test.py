"""用户可见品牌回归：新品牌必须完整出现，旧品牌只能留在显式兼容边界。

仓库里仍有包名、环境变量、项目格式、历史 changelog 和旧资源文件不能改名。
因此这里采用逐文件/逐行 allowlist，而不是对整个目录做粗暴忽略；任何新的旧品牌
命中都会先在本测试中失败，再由维护者把它归入真实兼容边界或改掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import brand

ROOT = Path(__file__).resolve().parent.parent
TOKENS = (
    "FolioThread",
    "Foliothread",
    "Folio Thread",
    "foliothread",
    "FOLIOTHREAD",
    "folio_thread",
    "folio-thread",
    "transpraxis",
    "TransPraxis",
    "TRANSPRAXIS",
)
TEXT_SUFFIXES = {
    ".bat", ".command", ".css", ".html", ".js", ".json", ".md", ".py",
    ".sh", ".svg", ".toml", ".ts", ".tsx", ".txt", ".yml", ".yaml",
}

# These files are historical evidence or technical compatibility contracts. They are
# named individually so a newly created public file is scanned by default.
LEGACY_ALLOWLIST_FILES = {
    "CHANGELOG.md",
    "docs/assets/foliothread-workflow.html",
    "docs/assets/foliothread-workflow.svg",
    "docs/brand.md",
    "pyproject.toml",
    "folith/brand.py",
    "folith/project.py",
    "folith/source_cleanup.py",
    "folith/translation_core/memory.py",
}

# Current docs and source files may mention a legacy token only when the whole line is
# an explicit package, path, environment variable, schema, or published-repo reference.
LEGACY_LINE_ALLOWLIST = {
    # Technical identifiers and evidence paths, not brand prose: environment
    # variable names, the legacy asset fallbacks, the project schema id and the
    # absolute paths recorded in verification evidence.
    "folith/application/settings.py": ("FOLIOTHREAD_",),
    "folith/store_lock.py": ("FOLIOTHREAD_STATE_LOCK_TIMEOUT",),
    "folith/qt/branding.py": ("foliothread-favicon.png", "foliothread-logo.png"),
    "folith/qt/dialogs.py": ("~/Dev/FolioThread/",),
    "scripts/qt_beta_chain.sh": ("/Users/xueyang/Dev/FolioThread/",),
    "docs/platform-migration-readiness-audit.md": ("foliothread-project-v1",),
    "docs/qt-beta/computer-use-acceptance-2026-10-05.md": ("/Users/xueyang/Dev/FolioThread/",),
    "docs/qt-beta/handoff-loop-prompt.md": ("/Users/xueyang/Dev/FolioThread",),
    "docs/qt-spike/evidence-log.md": ("~/Dev/FolioThread/",),
    "docs/dynamic-context-v2-evidence/comparison.json": ("/Users/xueyang/Dev/FolioThread/",),
    "docs/dynamic-context-v2-evidence/consistency.json": ("/Users/xueyang/Dev/FolioThread/",),
    "docs/dynamic-context-v2-evidence/report.md": ("/Users/xueyang/Dev/FolioThread/",),
    "docs/architecture-foundation-phase0-2.md": ("FOLIOTHREAD", "FolioThread"),
    "docs/architecture-foundation-phase4.md": ("FOLIOTHREAD"),
    "docs/desktop-d1/d1-13-diagnostics-adversarial-bundle.json": ("FolioThread"),
    "docs/desktop-d1/d1-14-unsafe-import-evidence.json": ("FolioThread"),
    "docs/desktop-handover.md": ("FOLIOTHREAD", "FolioThread"),
    "README.md": (
        "foliothread-0.4.0",
        "foliothread-0.5.0",
        "`foliothread`",
        "foliothread  # v0.4.0 compatibility command",
        "FOLIOTHREAD_",
        "0.5.0 移除旧 TransPraxis",
        "docs/foliothread-agentic-native-blueprint.md",
    ),
    "core.py": ("FOLIOTHREAD_",),
    "docs/architecture-boundaries.md": (
        "foliothread-agentic-native-blueprint.md",
        "`foliothread`",
    ),
    "docs/console-loop.md": ("foliothread-agentic-native-blueprint.md",),
    "docs/docx-contract-v1.md": ("foliothread-agentic-native-blueprint.md",),
    "docs/foliothread-agentic-native-blueprint.md": ("foliothread/",),
    "docs/mti-practice-driven-roadmap.md": ("foliothread-agentic-native-blueprint.md",),
    "docs/pdf-ingestion-performance-audit.md": ("FOLIOTHREAD_",),
    "docs/performance-hardening-round2.md": ("FOLIOTHREAD_",),
    # The persistence reports quote the retained legacy env-var prefix
    # `FOLIOTHREAD_STATE_SNAPSHOT_*` verbatim (read by core.py; `FOLIOTHREAD_*`
    # env vars are part of the compatibility boundary per docs/brand.md). They
    # contain no legacy brand in prose or identifiers.
    "docs/persistence-performance-phase3.md": ("FOLIOTHREAD_",),
    "docs/persistence-performance-round4-tm.md": ("FOLIOTHREAD_",),
    "docs/persistence-performance-round5-io-caliber.md": ("FOLIOTHREAD_",),
    "docs/project-memory.md": (
        "foliothread-agentic-native-blueprint.md",
        "foliothread-project-memory",
    ),
    "docs/release-plan.md": (
        "foliothread-agentic-native-blueprint.md",
        "foliothread-0.4.0",
        "foliothread-0.5.0",
        "`foliothread`",
        "`transpraxis` 命令",
    ),
    # The Reviewer Overlap Pipeline report quotes the retained `FOLIOTHREAD_*`
    # environment-variable prefix verbatim (the compatibility boundary per
    # docs/brand.md); it contains no legacy brand in prose or identifiers.
    "docs/reviewer-overlap-pipeline.md": ("FOLIOTHREAD_",),
    "docs/scenario-gate.md": ("foliothread-agentic-native-blueprint.md",),
    "docs/translation-core-phase2-completion.md": (
        "foliothread-agentic-native-blueprint.md",
    ),
    "scripts/make_scenario_fixtures.py": (
        "foliothread-agentic-native-blueprint.md",
    ),
    ".github/workflows/release-gate.yml": ('"transpraxis/"',),
    "scripts/check_release_archives.py": ('"transpraxis/"',),
    "scripts/release_smoke.py": ("foliothread-0.4.0",
        "foliothread-0.5.0", '"foliothread"', 'find_spec("transpraxis")'),
    # Perf modules only use the retained `FOLIOTHREAD_*` environment-variable
    # prefix (see docs/architecture-boundaries.md §5); they contain no legacy
    # brand in prose or identifiers.
    "folith/trace.py": ("FOLIOTHREAD_",),
    "folith/llm_transport.py": ("FOLIOTHREAD_",),
    "folith/ocr_backends.py": ("FOLIOTHREAD_",),
    "folith/ocr_paddle.py": ("FOLIOTHREAD_",),
    "docs/ocr-subsystem.md": ("FOLIOTHREAD_",),
    # Desktop work already in this checkout records compatibility identifiers
    # and historical evidence. Keep these exceptions file- and line-specific:
    # ordinary product prose in these files is still scanned.
    "folith/desktop/paths.py": ('version("foliothread")', "FOLIOTHREAD_"),
    "docs/desktop-d0/environment.md": ("FolioThread/venv",),
    "docs/desktop-d0/evidence-log.md": (
        "FolioThread/venv", 'version("foliothread")'),
    "docs/desktop-d2/evidence-log.md": (
        "FOLIOTHREAD_", "foliothread-app-icon.png", "`foliothread-*`"),
    "scripts/release_checks.py": ('"/FolioThread/venv"',),
    "scripts/write_desktop_build_info.py": ("editable `foliothread 0.4.0`",),
    "scripts/build_app_icon.py": ("legacy `foliothread-app-icon.png`",),
    "scripts/d0_slice_runner.py": ("FOLIOTHREAD_",),
    "folith/request_control.py": ("FOLIOTHREAD_",),
    "folith/section_scheduler.py": ("FOLIOTHREAD_",),
    "scripts/llm_transport_benchmark.py": ("FOLIOTHREAD_",),
}


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = _relative(path)
        if rel.startswith((".git/", "venv/", ".venv/", "outputs/", "dist/",
                           "build/", ".pytest_cache/", ".audit/", ".workbuddy/",
                           ".workbuddy-ai/", ".localize-anything/", "exports/", "tmp/", "skills/", "tests/",
                           # Generated working areas (see .gitignore): benchmark output
                           # and the local video scaffold are not shipped source.
                           "benchmark/", "video/")):
            continue
        if rel.endswith(".egg-info/SOURCES.txt") or ".egg-info/" in rel:
            continue
        if rel.startswith("folith/resources/brand/foliothread-"):
            continue
        yield path


def _legacy_hits() -> list[str]:
    hits: list[str] = []
    for path in _iter_text_files():
        rel = _relative(path)
        if rel in LEGACY_ALLOWLIST_FILES:
            continue
        allowed = LEGACY_LINE_ALLOWLIST.get(rel, ())
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found = [token for token in TOKENS if token in line]
            if not found:
                continue
            if allowed and any(marker in line for marker in allowed):
                continue
            hits.append(f"{rel}:{number}: {line.strip()}")
    return hits


def test_public_brand_constants_are_canonical():
    assert brand.APP_NAME == "Folith"
    assert brand.APP_NAME_ZH == "译页"
    assert brand.WORDMARK == "Folith·译页"
    assert brand.TAGLINE == "Agentic Translation Workspace"
    assert brand.TAGLINE_ZH == "智能体翻译工作台"

    app = (ROOT / "app.py").read_text(encoding="utf-8")
    gui = (ROOT / "gui.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '_BRAND_LOGO = _BRAND_DIR / "folith-lockup-transparent.png"' in app
    assert '_BRAND_FAVICON = _BRAND_DIR / "folith-mark.png"' in app
    assert "译页 智能体翻译工作台" in app
    assert "brand.APP_TITLE_ZH" in gui
    assert 'description = "Folith·译页 — Agentic Translation Workspace"' in pyproject
    assert "Folith·译页" in readme
    assert "Agentic Translation Workspace" in readme
    assert "智能体翻译工作台" in readme


def test_legacy_brand_hits_are_allowlisted():
    hits = _legacy_hits()
    assert not hits, "发现未归类的旧品牌命中：\n" + "\n".join(hits)


def test_technical_allowlist_does_not_allow_product_brand_prose(tmp_path, monkeypatch):
    path = tmp_path / "folith" / "ocr_paddle.py"
    path.parent.mkdir()
    path.write_text('device = "FOLIOTHREAD_OCR_DEVICE"\n# Welcome to FolioThread\n')
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _legacy_hits() == ["folith/ocr_paddle.py:2: # Welcome to FolioThread"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
