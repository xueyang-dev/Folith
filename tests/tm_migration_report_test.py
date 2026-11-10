"""Structured import verification reports for TMX / TBX / XLSX / CSV (#150).

Issue #150 asks the engine layer for one import report shape shared by the four
asset entry points:

* language-pair check between the language the file declares and the language
  the caller imports into (explicit warning or explicit block -- never a silent
  retag);
* classified counts (total / imported / conflict / ineligible / unsupported
  inline content / language unknown / missing pair / language mismatch);
* a metadata-loss inventory with an exact affected-entry count per category;
* JSON + Markdown persistence next to the project's translation memory so the
  report can be archived as Gate 1 evidence.

These tests pin the numbers item by item from the fixtures in
``tests/fixtures/tm_migration/``; they are deliberately exact, not "about".
"""
from __future__ import annotations

import io
import json
import os
import time
import uuid
from pathlib import Path

import pandas as pd
import pytest

import core
from folith.engine import glossary_checks, migration_report

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tm_migration"
SCHEMA_FILE = FIXTURES / "report_schema_v1.json"

TMX_TARGET = "简体中文"


@pytest.fixture()
def output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.reset_tm_dir_cache()
    return tmp_path


def _fixture(name: str) -> io.BytesIO:
    stream = io.BytesIO((FIXTURES / name).read_bytes())
    stream.name = name
    return stream


def _seed_memory(source="Existing entry", target="已有条目", target_lang=TMX_TARGET):
    tm = core.load_tm()
    tm[core.tm_scope_key(target_lang, source)] = core.tm_record(target, target_lang)
    core.save_tm(tm)


def _schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _loss(report: dict) -> dict:
    return {category["category"]: category
            for category in report["metadata_loss"]["categories"]}


def _assert_shape(report: dict, kind: str):
    """Every report -- all four entry points -- has exactly the documented keys."""
    schema = _schema()
    assert set(report) == set(schema["top_level"])
    assert report["schema"] == migration_report.REPORT_SCHEMA
    assert report["kind"] == kind
    assert set(report["file"]) == set(schema["file"])
    assert set(report["language_pair"]) == set(schema["language_pair"])
    assert set(report["counts"]) == set(schema["counts"])
    assert set(report["persistence"]) == set(schema["persistence"])
    assert [category["category"] for category in report["metadata_loss"]["categories"]] \
        == schema["loss_categories"][kind]
    for category in report["metadata_loss"]["categories"]:
        assert set(category) == set(schema["loss_entry"])
    counts = report["counts"]
    assert counts["total"] == counts["imported"] + sum(
        value for key, value in counts.items()
        if key.startswith("skipped_")), "总单元数必须等于已导入 + 全部跳过分类"


# ============================ TMX ============================

def test_tmx_classified_counts_and_loss_inventory_are_exact(output_dir):
    _seed_memory()
    result = core.import_tmx(_fixture("metadata_rich.tmx"),
                             target_lang=TMX_TARGET, report=True)

    assert result["added"] == 4
    # Legacy key: conflict + unsupported inline content (its historical meaning).
    assert result["skipped"] == 2

    report = result["report"]
    _assert_shape(report, "tmx")
    assert report["counts"] == {
        "total": 8,
        "imported": 4,
        "skipped_conflict": 1,
        "skipped_ineligible": 1,
        "skipped_unsupported_inline": 1,
        "skipped_language_unknown": 0,
        "skipped_language_mismatch": 0,
        "skipped_missing_pair": 1,
        "skipped_write_failed": 0,
    }

    loss = _loss(report)
    assert loss["tmx_tu_attributes"]["affected"] == 2
    assert loss["tmx_tu_attributes"]["fields"] == [
        "changeid", "creationdate", "creationid"]
    assert loss["tmx_prop"]["affected"] == 1
    assert loss["tmx_prop"]["fields"] == ["client", "domain"]
    assert loss["tmx_note"]["affected"] == 1
    assert loss["tmx_note"]["fields"] == ["explanation"]
    assert loss["tmx_tuv_attributes"]["affected"] == 1
    assert loss["tmx_tuv_attributes"]["fields"] == ["creationdate"]
    assert loss["tmx_extra_languages"]["affected"] == 1
    assert loss["tmx_extra_languages"]["fields"] == ["fr"]
    # File-level header metadata is dropped once per imported file; it is not a
    # per-unit loss, so it does not bump total_affected (documented as such).
    assert loss["tmx_header"]["affected"] == 1
    assert loss["tmx_header"]["fields"] == [
        "adminlang", "creationdate", "creationtool", "creationtoolversion",
        "datatype", "note", "o-tmf", "segtype", "srclang"]
    # te-3 (conflict) and te-4 (ineligible) carry metadata too, but their
    # metadata never reached the store, so it is not "lost" -- only dropped
    # along with the rejected unit.  The total counts imported units only.
    assert report["metadata_loss"]["total_affected"] == 3

    assert report["status"] == "partial"
    assert report["language_pair"] == {
        "expected_target": TMX_TARGET,
        "expected_tag": "zh-CN",
        "declared_source": "en",
        "declared_targets": ["zh-CN"],
        "declared_target_tags": ["zh-CN"],
        "status": "match",
        "action": "none",
        "mismatch_units": 0,
        "unverified_units": 0,
        "undeclared_target_units": 1,
        "message": report["language_pair"]["message"],
    }

    # The conflict did not overwrite the reviewed entry; the rest is retrievable.
    tm = core.load_tm()
    assert core.tm_lookup(tm, "Existing entry", target_lang=TMX_TARGET)[0]["target"] \
        == "已有条目"
    assert core.tm_lookup(tm, "Hello world", target_lang=TMX_TARGET)[0]["target"] \
        == "你好世界"
    assert core.tm_lookup(tm, "###", target_lang=TMX_TARGET)[0] is None
    assert core.tm_lookup(tm, "Water is", target_lang=TMX_TARGET)[0] is None


def test_tmx_without_expected_language_counts_language_unknown(output_dir):
    result = core.import_tmx(_fixture("metadata_rich.tmx"), report=True)

    assert result["added"] == 4
    report = result["report"]
    assert report["counts"]["total"] == 8
    assert report["counts"]["imported"] == 4
    assert report["counts"]["skipped_language_unknown"] == 1
    assert report["counts"]["skipped_missing_pair"] == 1
    assert report["language_pair"]["status"] == "not_checked"
    assert report["language_pair"]["action"] == "none"
    # The unlabelled unit must not have been guessed into any language.
    assert "未标注语言的译文" not in [record["target"]
                                     for record in core.load_tm().values()]


def test_tmx_zero_import_keeps_value_error_and_attaches_report(output_dir):
    _seed_memory()
    payload = (FIXTURES / "metadata_rich.tmx").read_bytes()
    # Keep only the conflicting unit: total=1, added=0 -> legacy ValueError.
    stream = io.BytesIO(payload)
    stream.name = "conflict_only.tmx"
    existing = core.load_tm()
    existing[core.tm_scope_key(TMX_TARGET, "The contract is binding.")] = \
        core.tm_record("旧译文", TMX_TARGET)
    existing[core.tm_scope_key(TMX_TARGET, "Hello world")] = \
        core.tm_record("旧译文", TMX_TARGET)
    existing[core.tm_scope_key(TMX_TARGET, "Plain unit")] = \
        core.tm_record("旧译文", TMX_TARGET)
    existing[core.tm_scope_key(TMX_TARGET, "Unlabelled target unit")] = \
        core.tm_record("旧译文", TMX_TARGET)
    core.save_tm(existing)

    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(stream, target_lang=TMX_TARGET, report=True)
    report = excinfo.value.report
    assert report["counts"]["imported"] == 0
    assert report["counts"]["skipped_conflict"] == 5
    assert report["status"] == "nothing_imported"
    assert Path(report["persistence"]["json"]).is_file()
    assert Path(report["persistence"]["markdown"]).is_file()


def test_tmx_language_pair_mismatch_blocks_and_stores_nothing(output_dir):
    project_id = str(uuid.uuid4())
    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(_fixture("language_pair_mismatch.tmx"),
                        project_id=project_id, target_lang=TMX_TARGET, report=True)

    report = excinfo.value.report
    assert report["counts"] == {
        "total": 3,
        "imported": 0,
        "skipped_conflict": 0,
        "skipped_ineligible": 0,
        "skipped_unsupported_inline": 0,
        "skipped_language_unknown": 0,
        "skipped_language_mismatch": 3,
        "skipped_missing_pair": 0,
        "skipped_write_failed": 0,
    }
    assert report["status"] == "blocked_language_pair"
    assert report["language_pair"]["status"] == "mismatch"
    assert report["language_pair"]["action"] == "blocked"
    assert report["language_pair"]["declared_targets"] == ["zh-TW"]
    assert any(warning["code"] == "language_pair_mismatch"
               for warning in report["warnings"])

    # Not a single wrong-language entry was stored, and no TM file was created.
    assert core.load_tm(project_id) == {}
    assert not core.tm_path(project_id).is_file()
    assert Path(report["persistence"]["json"]).is_file()
    assert Path(report["persistence"]["json"]).parent \
        == core.tm_path(project_id).parent / "import_reports"


def test_tmx_partial_language_mismatch_warns_and_rejects_only_mismatched(output_dir):
    result = core.import_tmx(_fixture("partial_language_mismatch.tmx"),
                             target_lang=TMX_TARGET, report=True)

    report = result["report"]
    assert result["added"] == 2
    # M3: the legacy `skipped` field now includes language-mismatch rejections,
    # so the adapter's "skipped" number can no longer hide them.
    assert result["skipped"] == 1
    assert report["counts"]["skipped_language_mismatch"] == 1
    assert report["counts"]["total"] == 3
    assert report["status"] == "partial"
    assert report["language_pair"]["status"] == "partial_mismatch"
    assert report["language_pair"]["action"] == "warned"
    assert report["language_pair"]["mismatch_units"] == 1

    tm = core.load_tm()
    assert core.tm_lookup(tm, "Traditional only unit", target_lang=TMX_TARGET)[0] is None
    assert core.tm_lookup(tm, "Traditional only unit", target_lang="繁体中文")[0] is None
    assert len(tm) == 2, "只允许语言对匹配的两条入库"


def test_tmx_unknown_expected_language_is_explicitly_warned(output_dir):
    result = core.import_tmx(_fixture("partial_language_mismatch.tmx"),
                             target_lang="Klingon", report=True)
    report = result["report"]
    assert report["language_pair"]["status"] == "unknown_expected"
    assert report["language_pair"]["action"] == "warned"
    assert any(warning["code"] == "language_pair_unresolvable"
               for warning in report["warnings"])
    # Existing behaviour: an explicit non-empty target_lang is the import
    # language, even when it cannot be resolved to a BCP-47 tag.
    assert result["added"] == 3
    assert core.tm_lookup(core.load_tm(), "Matching unit one",
                          target_lang="Klingon")[0]["target"] == "匹配单元一"


def test_tmx_return_shape_without_report_is_stable(output_dir):
    """Default path keeps the shape/keys; selection semantics are documented.

    This fixture declares a matching language, so it cannot see the language
    policy; that policy has its own tests below (B1).
    """
    result = core.import_tmx(_fixture("metadata_rich.tmx"), target_lang=TMX_TARGET)
    assert set(result) == {"added", "skipped"}
    assert result == {"added": 5, "skipped": 1}


def test_tmx_end_to_end_retrieval_exact_and_normalized(output_dir):
    core.import_tmx(_fixture("metadata_rich.tmx"), target_lang=TMX_TARGET)
    tm = core.load_tm()

    exact, reason = core.tm_lookup(tm, "The contract is binding.",
                                   target_lang=TMX_TARGET)
    assert reason == "exact" and exact["target"] == "合同具有约束力。"

    normalized, reason = core.tm_lookup(tm, "The  contract is binding.",
                                        target_lang=TMX_TARGET)
    assert reason == "normalized" and normalized["target"] == "合同具有约束力。"

    # Target language is part of memory identity: another language is a miss.
    assert core.tm_lookup(tm, "The contract is binding.",
                          target_lang="Français")[0] is None


def test_tmx_parse_failure_has_no_partial_import_and_reports_failure(output_dir):
    _seed_memory()
    before = core.tm_path().read_bytes()
    stream = io.BytesIO(b"<tmx><body><tu><tuv>")
    stream.name = "broken.tmx"

    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(stream, target_lang=TMX_TARGET, report=True)
    assert "无法解析 TMX 文件" in str(excinfo.value)
    report = excinfo.value.report
    assert report["status"] == "failed"
    assert report["error"]
    assert report["counts"]["imported"] == 0
    assert report["counts"]["total"] == 0
    assert core.tm_path().read_bytes() == before, "解析失败不得产生半成品导入"
    assert Path(report["persistence"]["json"]).is_file()
    assert Path(report["persistence"]["markdown"]).is_file()


def test_tmx_report_persistence_is_archivable_json_plus_markdown(output_dir):
    result = core.import_tmx(_fixture("metadata_rich.tmx"),
                             target_lang=TMX_TARGET, report=True)
    report = result["report"]
    json_path = Path(report["persistence"]["json"])
    markdown_path = Path(report["persistence"]["markdown"])
    assert json_path.suffix == ".json" and markdown_path.suffix == ".md"
    assert json_path.parent == output_dir / "import_reports"
    assert json_path.parent == markdown_path.parent

    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk == report, "落盘 JSON 必须与调用方拿到的报告一致"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# 导入校验报告" in markdown
    assert "metadata_rich.tmx" in markdown
    assert "skipped_conflict" in markdown
    assert "tmx_tu_attributes" in markdown


def test_report_directory_follows_project_translation_memory_path(output_dir):
    project_id = str(uuid.uuid4())
    result = core.import_tmx(_fixture("partial_language_mismatch.tmx"),
                             project_id=project_id, target_lang=TMX_TARGET,
                             report=True)
    report_path = Path(result["report"]["persistence"]["json"])
    assert report_path.parent == core.tm_path(project_id).parent / "import_reports"
    assert report_path.is_file()


def test_tmx_real_scale_import_performance_smoke(output_dir):
    units = "".join(
        f'<tu><tuv xml:lang="en"><seg>Source unit {index}</seg></tuv>'
        f'<tuv xml:lang="zh-CN"><seg>译文单元 {index}</seg></tuv></tu>'
        for index in range(1200))
    payload = (f'<?xml version="1.0"?><tmx version="1.4">'
               f'<header srclang="en"/><body>{units}</body></tmx>').encode("utf-8")
    stream = io.BytesIO(payload)
    stream.name = "scale-1200.tmx"

    started = time.perf_counter()
    result = core.import_tmx(stream, target_lang=TMX_TARGET, report=True)
    elapsed = time.perf_counter() - started

    assert result["added"] == 1200
    assert result["report"]["counts"]["total"] == 1200
    # Loose upper bound: this guards against accidental quadratic parsing, not
    # against a slow machine.
    assert elapsed < 5.0, f"1200 条 TMX 导入耗时 {elapsed:.2f}s，疑似 O(n^2) 退化"


# ============================ TBX ============================

def test_tbx_classified_counts_and_loss_inventory_are_exact(output_dir):
    entries = glossary_checks.parse_termbase_tbx(
        _fixture("metadata_rich.tbx"), target_lang=TMX_TARGET, report=True)

    assert entries == [
        {"source": "Skopos theory", "target": "目的论",
         "behavior": "translate", "status": "locked"},
        {"source": "fidelity", "target": "忠实性",
         "behavior": "translate", "status": "locked"},
    ]
    report = entries.report
    _assert_shape(report, "tbx")
    assert report["counts"] == {
        "total": 5,
        "imported": 2,
        "skipped_conflict": 0,
        "skipped_ineligible": 0,
        "skipped_unsupported_inline": 0,
        "skipped_language_unknown": 1,
        "skipped_language_mismatch": 1,
        "skipped_missing_pair": 1,
        "skipped_write_failed": 0,
    }

    loss = _loss(report)
    assert loss["tbx_term_entry_attributes"]["affected"] == 0
    assert loss["tbx_term_note"]["affected"] == 2
    assert loss["tbx_term_note"]["fields"] == ["termStatus"]
    assert loss["tbx_extra_languages"]["affected"] == 1
    assert loss["tbx_extra_languages"]["fields"] == ["fr"]
    assert loss["tbx_multiple_terms"]["affected"] == 1
    assert loss["tbx_multiple_terms"]["fields"] == ["zh-CN"]
    assert loss["tbx_descrip"]["affected"] == 1
    assert loss["tbx_descrip"]["fields"] == ["definition"]
    assert loss["tbx_entry_metadata"]["affected"] == 1
    assert loss["tbx_entry_metadata"]["fields"] == ["context", "xref"]
    assert loss["tbx_root_metadata"]["affected"] == 1
    assert loss["tbx_root_metadata"]["fields"] == [
        "admin", "encodingDesc", "fileDesc", "martifHeader", "p", "sourceDesc",
        "transac"]
    assert report["metadata_loss"]["total_affected"] == 2

    assert report["status"] == "partial"
    assert report["language_pair"]["declared_source"] == "en"
    assert report["language_pair"]["declared_targets"] == ["zh-CN", "zh-TW"]
    assert report["language_pair"]["status"] == "partial_mismatch"
    assert report["language_pair"]["action"] == "warned"


def test_tbx_without_expected_language_keeps_language_pair_unchecked(output_dir):
    entries = glossary_checks.parse_termbase_tbx(
        _fixture("metadata_rich.tbx"), report=True)
    # No expected target: existing behaviour imports the declared pair as-is.
    assert [entry["target"] for entry in entries] == ["目的论", "忠实性", "僅繁體"]
    assert entries.report["language_pair"]["status"] == "not_checked"
    assert entries.report["counts"]["skipped_language_mismatch"] == 0


def test_termbase_parsers_keep_plain_list_return_without_report(output_dir):
    entries = core.parse_termbase_tbx(_fixture("metadata_rich.tbx"))
    assert type(entries) is list
    assert len(entries) == 3
    csv_entries = core.parse_termbase_csv(_fixture("terms_extra_columns.csv"))
    assert type(csv_entries) is list and len(csv_entries) == 2


# ============================ XLSX / CSV ============================

def test_csv_report_counts_and_extra_column_loss_are_exact(output_dir):
    entries = core.parse_termbase_csv(
        _fixture("terms_extra_columns.csv"), target_lang=TMX_TARGET, report=True)
    report = entries.report
    _assert_shape(report, "csv")

    assert len(entries) == 2
    assert report["counts"] == {
        "total": 4,
        "imported": 2,
        "skipped_conflict": 0,
        "skipped_ineligible": 0,
        "skipped_unsupported_inline": 0,
        "skipped_language_unknown": 0,
        "skipped_language_mismatch": 0,
        "skipped_missing_pair": 2,
        "skipped_write_failed": 0,
    }
    loss = _loss(report)
    assert loss["termbase_extra_columns"]["affected"] == 2
    assert loss["termbase_extra_columns"]["fields"] == [
        "Comments", "Legacy ID", "Owner"]
    assert report["metadata_loss"]["total_affected"] == 2

    # A CSV/XLSX termbase declares no language at all: warn, never pretend.
    assert report["language_pair"]["status"] == "undeclared"
    assert report["language_pair"]["action"] == "warned"
    assert any(warning["code"] == "language_pair_undeclared"
               for warning in report["warnings"])
    assert report["status"] == "partial"


def test_xlsx_report_uses_the_same_structure_as_csv(output_dir, tmp_path):
    source = FIXTURES / "terms_extra_columns.csv"
    dataframe = pd.read_csv(source)
    xlsx_path = tmp_path / "terms_extra_columns.xlsx"
    dataframe.to_excel(xlsx_path, index=False)
    stream = io.BytesIO(xlsx_path.read_bytes())
    stream.name = "terms_extra_columns.xlsx"

    entries = core.parse_termbase(stream, target_lang=TMX_TARGET, report=True)
    report = entries.report
    _assert_shape(report, "xlsx")
    assert len(entries) == 2
    assert report["counts"]["total"] == 4
    assert report["counts"]["imported"] == 2
    assert report["counts"]["skipped_missing_pair"] == 2
    assert _loss(report)["termbase_extra_columns"]["affected"] == 2
    assert report["language_pair"]["status"] == "undeclared"
    assert Path(report["persistence"]["json"]).parent == output_dir / "import_reports"


def test_all_four_entry_points_share_one_report_shape(output_dir, tmp_path):
    _seed_memory()
    xlsx_path = tmp_path / "terms_extra_columns.xlsx"
    pd.read_csv(FIXTURES / "terms_extra_columns.csv").to_excel(xlsx_path, index=False)
    xlsx_stream = io.BytesIO(xlsx_path.read_bytes())
    xlsx_stream.name = "terms_extra_columns.xlsx"

    reports = [
        core.import_tmx(_fixture("metadata_rich.tmx"), target_lang=TMX_TARGET,
                        report=True)["report"],
        glossary_checks.parse_termbase_tbx(
            _fixture("metadata_rich.tbx"), target_lang=TMX_TARGET,
            report=True).report,
        core.parse_termbase_csv(_fixture("terms_extra_columns.csv"),
                                target_lang=TMX_TARGET, report=True).report,
        core.parse_termbase(xlsx_stream, target_lang=TMX_TARGET,
                            report=True).report,
    ]
    for report, kind in zip(reports, ("tmx", "tbx", "csv", "xlsx")):
        _assert_shape(report, kind)
        assert report["persistence"]["json"]
        assert report["persistence"]["markdown"]


def test_termbase_parse_failure_reports_and_returns_nothing(output_dir):
    stream = io.BytesIO(b"not,a,termbase")
    stream.name = "broken.csv"
    with pytest.raises(ValueError) as excinfo:
        core.parse_termbase_csv(stream, target_lang=TMX_TARGET, report=True)
    report = excinfo.value.report
    assert report["status"] == "failed"
    assert report["counts"]["imported"] == 0
    assert Path(report["persistence"]["json"]).is_file()


# ==================== B1: language-pair policy (review 332) ====================

def _tmx_with_target_lang(xml_lang, source="Hello there", target="你好"):
    return io.BytesIO(f'''<?xml version="1.0"?>
<tmx version="1.4"><header srclang="en"/><body>
<tu><tuv xml:lang="en"><seg>{source}</seg></tuv>
<tuv xml:lang="{xml_lang}"><seg>{target}</seg></tuv></tu>
</body></tmx>'''.encode("utf-8"))


@pytest.mark.parametrize("declared", ["zh", "zh-CN", "ZH-cn"])
def test_tmx_compatible_target_tags_are_imported(output_dir, declared):
    result = core.import_tmx(_tmx_with_target_lang(declared),
                             target_lang=TMX_TARGET, report=True)
    assert result["added"] == 1
    assert result["skipped"] == 0
    pair = result["report"]["language_pair"]
    assert pair["status"] == "match" and pair["action"] == "none"
    assert core.tm_lookup(core.load_tm(), "Hello there",
                          target_lang=TMX_TARGET)[0]["target"] == "你好"


def test_tmx_display_name_and_wider_tag_are_mutually_recognised(output_dir):
    result = core.import_tmx(_tmx_with_target_lang("en"),
                             target_lang="English", report=True)
    assert result["added"] == 1
    assert result["report"]["language_pair"]["status"] == "match"


def test_tmx_unprovable_tags_fall_back_to_caller_language_with_warning(output_dir):
    """zh-Hans has no existing repo mapping: import like main, but warn.

    `folith/source_languages.py` has no Hans/Hant/Script inference, so the
    review's rule 3 applies: never block on an unprovable tag.
    """
    result = core.import_tmx(_tmx_with_target_lang("zh-Hans"),
                             target_lang=TMX_TARGET, report=True)
    assert result["added"] == 1
    pair = result["report"]["language_pair"]
    assert pair["status"] == "unverified"
    assert pair["action"] == "warned"
    assert pair["unverified_units"] == 1
    warnings = {warning["code"]: warning for warning
                in result["report"]["warnings"]}
    assert warnings["language_pair_unverified"]["count"] == 1
    assert core.tm_lookup(core.load_tm(), "Hello there",
                          target_lang=TMX_TARGET)[0]["target"] == "你好"


@pytest.mark.parametrize("declared", ["zh-TW", "en-US", "en"])
def test_tmx_provably_different_target_is_rejected_with_reason(output_dir, declared):
    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(_tmx_with_target_lang(declared),
                        target_lang=TMX_TARGET, report=True)
    message = str(excinfo.value)
    assert "语言不符" in message, message
    assert "1 条" in message, message
    report = excinfo.value.report
    assert report["counts"]["skipped_language_mismatch"] == 1
    assert report["counts"]["imported"] == 0
    assert report["language_pair"]["status"] == "mismatch"
    assert core.load_tm() == {}


def test_adapter_view_surfaces_language_mismatch_reason(output_dir, tmp_path):
    """`report=False` is what the Qt adapter actually calls."""
    pytest.importorskip("PySide6")
    from folith.qt import adapter

    state = core.new_job_state("doc.docx")
    state["target_lang"] = TMX_TARGET
    core.save_job_state("tmxlang000000000001", {**state})
    core.save_job_state("tmxlang000000000002", {**state})

    compatible = tmp_path / "compatible.tmx"
    compatible.write_bytes(_tmx_with_target_lang("zh").getvalue())
    result = adapter.attach_termbase("tmxlang000000000001", str(compatible))
    assert result["kind"] == "tm" and result["count"] == 1

    traditional = tmp_path / "traditional.tmx"
    traditional.write_bytes(_tmx_with_target_lang("zh-TW").getvalue())
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.attach_termbase("tmxlang000000000002", str(traditional))
    assert "语言不符" in str(excinfo.value)


def test_error_report_is_only_attached_when_requested(output_dir):
    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(_tmx_with_target_lang("zh-TW"), target_lang=TMX_TARGET)
    assert excinfo.value.report is None
    assert "语言不符" in str(excinfo.value)


# ==================== B2: report persistence is a side product ====================

def _import_partial_with_report(**kwargs):
    return core.import_tmx(_fixture("partial_language_mismatch.tmx"),
                           target_lang=TMX_TARGET, report=True, **kwargs)


def test_report_write_oserror_does_not_fail_a_successful_import(output_dir, monkeypatch):
    def broken_write(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(migration_report, "write_report", broken_write)

    result = _import_partial_with_report()
    assert result["added"] == 2
    assert len(core.load_tm()) == 2, "TM 已写入，报告失败不得回滚或抛错"
    report = result["report"]
    assert report["persistence"] == {"directory": "", "json": "", "markdown": ""}
    assert report["status"] == "partial"
    assert report["error"] == ""
    codes = {warning["code"] for warning in report["warnings"]}
    assert "report_not_written" in codes


def test_termbase_report_write_failure_still_returns_entries(output_dir, monkeypatch):
    def broken_write(*args, **kwargs):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(migration_report, "write_report", broken_write)

    entries = core.parse_termbase_csv(_fixture("terms_extra_columns.csv"),
                                      target_lang=TMX_TARGET, report=True)
    assert len(entries) == 2
    assert entries.report["persistence"]["json"] == ""
    assert any(warning["code"] == "report_not_written"
               for warning in entries.report["warnings"])


def test_long_report_filename_is_truncated_instead_of_raising(output_dir):
    stream = io.BytesIO((FIXTURES / "partial_language_mismatch.tmx").read_bytes())
    stream.name = "x" * 250 + ".tmx"

    result = core.import_tmx(stream, target_lang=TMX_TARGET, report=True)
    assert result["added"] == 2
    report_path = Path(result["report"]["persistence"]["json"])
    assert report_path.is_file()
    assert len(report_path.name) < 200


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位；Windows 无对应用例")
def test_unwritable_report_directory_does_not_fail_the_import(output_dir, tmp_path):
    readonly = tmp_path / "readonly-reports"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    try:
        result = _import_partial_with_report(report_dir=readonly)
    finally:
        os.chmod(readonly, 0o700)
    assert result["added"] == 2
    assert len(core.load_tm()) == 2
    report = result["report"]
    assert report["persistence"]["json"] == ""
    assert any(warning["code"] == "report_not_written"
               for warning in report["warnings"])


# ==================== M1: TBX loss inventory alignment ====================

def test_tbx_loss_inventory_uses_actually_imported_languages(output_dir):
    entries = glossary_checks.parse_termbase_tbx(
        _fixture("tbx_langset_without_term.tbx"), target_lang=TMX_TARGET,
        report=True)
    assert entries == [{"source": "Hello", "target": "你好",
                        "behavior": "translate", "status": "locked"}]
    loss = _loss(entries.report)
    # en + zh-CN are the imported pair; fr is the third *term-bearing* language.
    assert loss["tbx_extra_languages"]["affected"] == 1
    assert loss["tbx_extra_languages"]["fields"] == ["fr"]
    assert "zh-CN" not in loss["tbx_extra_languages"]["fields"]
    # The de langSet's descrip belongs to a non-imported language: not "lost".
    assert loss["tbx_descrip"]["affected"] == 0
    assert loss["tbx_descrip"]["fields"] == []
    assert loss["tbx_multiple_terms"]["affected"] == 0
    # The imported entry did lose one field (the third language fr); the point
    # is that zh-CN is not misreported and the non-imported de metadata is not
    # counted, so the only affected entry is the imported one.
    assert entries.report["metadata_loss"]["total_affected"] == 1
    assert entries.report["language_pair"]["status"] == "match"


# ==================== M2: termbase path-like input ====================

def test_termbase_parsers_accept_path_like_arguments(output_dir, tmp_path):
    csv_path = tmp_path / "terms.csv"
    csv_path.write_bytes((FIXTURES / "terms_extra_columns.csv").read_bytes())
    assert len(core.parse_termbase_csv(str(csv_path))) == 2
    entries = core.parse_termbase_csv(csv_path, target_lang=TMX_TARGET,
                                      report=True)
    assert len(entries) == 2 and entries.report["kind"] == "csv"

    xlsx_path = tmp_path / "terms.xlsx"
    pd.read_csv(FIXTURES / "terms_extra_columns.csv").to_excel(
        xlsx_path, index=False)
    assert len(core.parse_termbase(str(xlsx_path))) == 2
    xlsx_entries = core.parse_termbase(xlsx_path, report=True)
    assert xlsx_entries.report["kind"] == "xlsx"


# ==================== m2: write-failure counts stay balanced ====================

def test_write_failure_counts_stay_balanced_and_metadata_is_zeroed(
        output_dir, monkeypatch):
    def broken_save(*args, **kwargs):
        raise OSError(28, "disk full")
    monkeypatch.setattr(core, "save_tm", broken_save)

    with pytest.raises(ValueError) as excinfo:
        core.import_tmx(_fixture("partial_language_mismatch.tmx"),
                        target_lang=TMX_TARGET, report=True)
    report = excinfo.value.report
    counts = report["counts"]
    assert counts["imported"] == 0
    assert counts["skipped_write_failed"] == 2
    assert counts["total"] == sum(value for key, value in counts.items()
                                  if key != "total")
    assert report["metadata_loss"]["total_affected"] == 0
    assert report["status"] == "failed"
    assert not core.tm_path().is_file()
