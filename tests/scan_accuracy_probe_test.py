"""Check the issue #8 reproduction script's measurement helpers, offline."""
import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "scan_accuracy_probe", ROOT / "benchmark/scan_accuracy_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_reported_misreads_are_detected_without_the_correct_form():
    page = ("الفصل الأول\n"
            "كان كافعامل في الريف وقد احواله تغيّرت عن كال من قبل، "
            "وانكرة شعرية بلا موضوع.\n")
    result = probe.checkpoint_hits(page)
    assert result["total"] == 4
    assert result["misread_reproduced"] == 4
    assert result["correct_form_only"] == 0
    assert result["neither_form_present"] == 0


def test_correct_page_is_not_reported_as_a_reproduction():
    page = ("كان كافعالهم في الريف وقد احوالهم تغيّرت عن كمال من قبل، "
            "والفكرة شعرية واضحة.\n")
    result = probe.checkpoint_hits(page)
    assert result["misread_reproduced"] == 0
    assert result["correct_form_only"] == 4


def test_plausible_class_is_not_privileged():
    # The hardest reported error (احواله/احوالهم) is checked exactly like the
    # nonwords: this script reproduces the *observation*, it does not claim to
    # detect the plausible-but-wrong class automatically.
    kinds = {row["kind"] for row in probe.checkpoint_hits("كان احواله واضحا")["checkpoints"]}
    assert kinds == {"nonword", "plausible"}


def test_diacritics_do_not_hide_a_match():
    # A model that re-adds vowel marks must not be credited as a correction.
    page = "وكان كَافِعَامِل في الريف"
    result = probe.checkpoint_hits(page)
    row = next(r for r in result["checkpoints"] if r["read"] == "كافعامل")
    assert row["read_found"] is True


def test_page_number_line_is_flagged_when_kept():
    kept = probe.structure_summary(["نص", "١٣", "نص آخر"])
    excluded = probe.structure_summary(["نص", "نص آخر"])
    assert kept["page_number_lines"] == 1 and kept["page_number_excluded"] is False
    assert excluded["page_number_excluded"] is True


def test_reference_metrics_are_optional_and_reuse_bench_lib():
    assert probe.reference_metrics("", "أي نص") is None
    metrics = probe.reference_metrics("بسم الله", "بسم الله")
    assert metrics["character_errors"]["cer"] == 0
    assert not math.isnan(metrics["word_errors"]["wer"])


def test_record_path_has_no_path_separators():
    path = probe._record_path(13, 150, "opencode/deepseek-v4.1:flash")
    assert "/" not in path.name and ":" not in path.name
    assert path.name.startswith("page-0013-150dpi-")
