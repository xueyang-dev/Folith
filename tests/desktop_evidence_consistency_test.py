"""Documentation-truth checks for the desktop milestones (prompt §25).

The closeouts classify every roadmap requirement and cite evidence IDs.  Those
citations are only meaningful if the cited evidence actually exists, and a
requirement row without a classification is not an audit.  These tests keep the
audit trail honest as the docs evolve.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

LOGS = {
    "D0": DOCS / "desktop-d0" / "evidence-log.md",
    "D1": DOCS / "desktop-d1" / "evidence-log.md",
    "D2": DOCS / "desktop-d2" / "evidence-log.md",
    "D3": DOCS / "desktop-d3" / "evidence-log.md",
}
CLOSEOUTS = {
    "D0": DOCS / "desktop-d0" / "d0-decision.md",
    "D1": DOCS / "desktop-d1" / "closeout.md",
    "D2": DOCS / "desktop-d2" / "closeout.md",
    "D3": DOCS / "desktop-d3" / "evidence-log.md",
}
CLASSIFICATIONS = ("PASS", "FAIL", "BLOCKED", "NOT APPLICABLE", "NOT YET TESTED")
RECOMMENDATIONS = (
    "PROCEED TO D1",
    "PROCEED TO D1 WITH DOCUMENTED NON-BLOCKING RISKS",
    "DO NOT PROCEED",
    "BLOCKED BY EXTERNAL ENVIRONMENT",
)


def _sections(path: Path) -> set[str]:
    return set(re.findall(r"^##\s+(D\d-\d+[a-z]?)", path.read_text(encoding="utf-8"), re.M))


@pytest.mark.parametrize("milestone", sorted(CLOSEOUTS))
def test_cited_evidence_ids_exist(milestone):
    """A closeout may only cite evidence that has a section in the log."""
    closeout = CLOSEOUTS[milestone]
    assert closeout.is_file(), f"missing closeout: {closeout}"
    defined = {name: _sections(path) for name, path in LOGS.items()}
    missing = []
    for identifier in sorted(set(re.findall(r"\b(D\d-\d+[a-z]?)\b",
                                           closeout.read_text(encoding="utf-8")))):
        family = identifier.split("-")[0]
        if family in defined and identifier not in defined[family]:
            missing.append(identifier)
    assert not missing, f"{milestone} cites evidence that does not exist: {missing}"


@pytest.mark.parametrize("milestone", ("D1", "D2"))
def test_requirement_rows_are_classified(milestone):
    """Every requirement row must say PASS / FAIL / BLOCKED / N.A. / NOT YET TESTED."""
    unclassified = []
    for number, line in enumerate(CLOSEOUTS[milestone].read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue  # only numbered requirement rows are audited
        if not any(marker in line for marker in CLASSIFICATIONS):
            unclassified.append(f"{milestone}:{number}: {line[:90]}")
    assert not unclassified, "unclassified requirement rows: " + "; ".join(unclassified)


def test_d0_decision_uses_the_required_recommendation_vocabulary():
    text = (DOCS / "desktop-d0" / "d0-decision.md").read_text(encoding="utf-8")
    found = [item for item in RECOMMENDATIONS if item in text]
    assert found, "the D0 decision must state one of the allowed recommendations"
    # 'PROCEED TO D1' is a prefix of the longer variant, so require the longest match.
    longest = max(found, key=len)
    assert longest in ("PROCEED TO D1 WITH DOCUMENTED NON-BLOCKING RISKS",
                       "DO NOT PROCEED", "BLOCKED BY EXTERNAL ENVIRONMENT"), longest


def test_d0_report_covers_every_required_section():
    text = (DOCS / "desktop-d0" / "d0-decision.md").read_text(encoding="utf-8")
    for heading in ("## A.", "## B.", "## C.", "## D.", "## E.", "## F.", "## G.",
                    "## H.", "## I."):
        assert heading in text, f"missing required D0 report section {heading}"


def test_closeouts_do_not_claim_windows_support():
    """The product owner deferred Windows; no doc may claim it ships."""
    forbidden = (
        "Windows x64 安装器 | **PASS**",
        "Windows 已签名",
        "Windows 版本已提供",
        "双端均通过",
        "Windows / PyInstaller** | **GO",
    )
    for path in list(CLOSEOUTS.values()) + [DOCS / "desktop-d3" / "release-notes-0.5.0.md"]:
        text = path.read_text(encoding="utf-8")
        for claim in forbidden:
            assert claim not in text, f"{path.name} claims Windows support: {claim}"
        # The deferral must be stated somewhere in the umbrella docs.
        if path.name in ("closeout.md", "release-notes-0.5.0.md"):
            assert any(marker in text for marker in ("BLOCKED", "未提供", "不宣称双端成立")), path
