"""Immutable final-delivery snapshot regressions."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

import core
from folith import snapshots


def _job(job_id, *, target="译文", findings=None, glossary=None):
    state = core.new_job_state("snapshot.docx")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False,
        paras=["Source text"],
        pairs=[{
            "source": "Source text", "target": target,
            "initial_target": "初译", "reviewed": True,
            "target_provenance": "reviewed",
        }],
        findings=findings or [],
        glossary=glossary or [],
        delivery_status="review_required" if findings else "draft",
        has_blocking=bool(findings),
    )
    core.save_source(job_id, b"source document bytes")
    core.save_job_state(job_id, state)
    if glossary:
        state = core.freeze_glossary(job_id, entries=glossary, frozen_by="test")
    return state


def test_first_approval_freezes_assets_manifest_and_identity():
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-first-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotfirst00001"
        state = _job(job_id, glossary=[{
            "source": "Other term", "target": "其他术语",
            "preferred": "其他术语", "status": "locked", "scope": "document",
        }])
        approved, ok, errors = core.approve_delivery(
            job_id, note="首版确认", actor="reviewer")
        assert ok, errors
        assert approved["delivery_status"] == "final"
        assert approved["latest_delivery_snapshot_version"] == 1
        reloaded = core.load_job_state(job_id)
        assert reloaded["delivery_status"] == "final"
        assert core.delivery_snapshot_status(job_id, reloaded)["current"]

        manifest = core.list_delivery_snapshots(job_id)[0]
        assert manifest["job_identity"]["job_id"] == job_id
        assert manifest["approval"]["actor"] == "reviewer"
        assert manifest["approval"]["note"] == "首版确认"
        assert manifest["source_identity"]["source_hash"] == hashlib.sha256(
            b"source document bytes").hexdigest()
        assert manifest["active_terminology_version"]["version"] == 1
        assert manifest["translation_state_identity"]
        assert manifest["assets"]
        for item in manifest["assets"]:
            data = core.delivery_snapshot_assets(job_id, 1)[item["name"]]
            assert data is not None
            assert item["sha256"] == hashlib.sha256(data).hexdigest()
        archive = core.delivery_snapshot_archive(job_id, 1)
        assert archive
        with ZipFile(BytesIO(archive)) as bundle:
            assert "snapshot_manifest.json" in bundle.namelist()
            assert "delivery_manifest.json" in bundle.namelist()
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_old_final_snapshot_stays_current_after_qa_defaults_are_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "snapshotlegacy0001"
    _job(job_id)
    _, ok, errors = core.approve_delivery(job_id, note="0.8.0 frozen")
    assert ok, errors

    # Reconstruct the 0.8.0 on-disk shape, before these four fields existed.
    job_root = core.job_dir(job_id)
    state_path = job_root / "state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    for key in ("qa_triage_enabled", "finding_triage",
                "qa_escalation_enabled", "qa_escalation"):
        raw.pop(key, None)
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    manifest_path = job_root / "delivery_snapshots" / "v1" / "snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["translation_state_identity"] = snapshots.state_identity(raw)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = core.load_job_state(job_id)
    assert loaded["delivery_status"] == "final"
    assert loaded["delivery_approval"] == raw["delivery_approval"]
    assert core.delivery_snapshot_status(job_id, loaded)["current"]
    assert core.delivery_snapshot_status_light(job_id)["current"]

    edited = dict(loaded, qa_triage_enabled=True)
    assert core.delivery_snapshot_status(job_id, edited)["diverged"]
    loaded["pairs"][0]["target"] = "编辑后的译文"
    core.save_job_state(job_id, loaded)
    assert core.load_job_state(job_id)["delivery_status"] == "draft"
    assert core.delivery_snapshot_status(job_id)["diverged"]
    assert core.list_delivery_snapshots(job_id)[0] == manifest


def test_mutation_keeps_old_bytes_and_second_approval_creates_new_version():
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-versions-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotversions01"
        _job(job_id)
        first, ok, errors = core.approve_delivery(job_id, note="v1")
        assert ok, errors
        old_manifest = core.list_delivery_snapshots(job_id)[0]
        old_bytes = core.delivery_snapshot_assets(job_id, 1)["bilingual.jsonl"]

        changed = core.load_job_state(job_id)
        changed["pairs"][0]["target"] = "工作版本新译文"
        core.save_job_state(job_id, changed)
        working = core.load_job_state(job_id)
        assert working["delivery_status"] == "draft"
        assert core.delivery_snapshot_assets(job_id, 1)["bilingual.jsonl"] == old_bytes
        assert core.delivery_snapshot_status(job_id, working)["diverged"]

        second, ok, errors = core.approve_delivery(job_id, note="v2")
        assert ok, errors
        assert second["latest_delivery_snapshot_version"] == 2
        assert len(core.list_delivery_snapshots(job_id)) == 2
        assert core.list_delivery_snapshots(job_id)[0] == old_manifest
        assert core.delivery_snapshot_assets(job_id, 1)["bilingual.jsonl"] == old_bytes
        assert core.delivery_snapshot_assets(job_id, 2)["bilingual.jsonl"] != old_bytes
        reloaded = core.load_job_state(job_id)
        assert reloaded["latest_delivery_snapshot_version"] == 2
        assert len(core.list_delivery_snapshots(job_id)) == 2
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_terminology_change_invalidates_current_but_preserves_snapshot():
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-terms-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotterms00001"
        _job(job_id, glossary=[{
            "source": "Other term", "target": "其他术语",
            "preferred": "其他术语", "status": "locked", "scope": "document",
        }])
        _, ok, errors = core.approve_delivery(job_id, note="旧术语版")
        assert ok, errors
        old_manifest = core.list_delivery_snapshots(job_id)[0]
        old_bytes = core.delivery_snapshot_assets(job_id, 1)["delivery_manifest.json"]
        changed = core.freeze_glossary(job_id, entries=[{
            "source": "Changed term", "target": "变更术语",
            "preferred": "变更术语", "status": "locked", "scope": "document",
        }], frozen_by="terminologist")
        assert changed["delivery_status"] == "draft"
        assert changed["delivery_approved_by_human"] is False
        assert core.list_delivery_snapshots(job_id)[0] == old_manifest
        assert core.delivery_snapshot_assets(job_id, 1)["delivery_manifest.json"] == old_bytes
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_retranslation_invalidates_current_but_preserves_snapshot(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-retranslate-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotretranslate1"
        state = _job(job_id)
        state["paras"] = ["Source text"]
        core.save_job_state(job_id, state)
        _, ok, errors = core.approve_delivery(job_id, note="原译文")
        assert ok, errors
        old_bytes = core.delivery_snapshot_assets(job_id, 1)["bilingual.jsonl"]
        monkeypatch.setattr(core, "translate_batch", lambda *args, **kwargs: ["重新翻译"])
        updated, fixed = core.retranslate_segments(
            job_id, [0], "DeepSeek", "key", "model", "简体中文", glossary=[])
        assert fixed == [0]
        assert updated["delivery_status"] == "draft"
        assert updated["delivery_approval"] is None
        assert core.delivery_snapshot_assets(job_id, 1)["bilingual.jsonl"] == old_bytes
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_blocking_requires_explicit_risk_acceptance_and_snapshot_records_it():
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-risk-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotrisk00001"
        state = _job(job_id, findings=[{
            "segment_index": 0, "severity": "blocking", "type": "check",
            "reason": "需要人工确认",
        }])
        _, ok, errors = core.approve_delivery(job_id)
        assert not ok and errors
        assert core.list_delivery_snapshots(job_id) == []
        approved, ok, errors = core.approve_delivery(
            job_id, note="明确接受风险", actor="risk-owner", accept_blocking=True)
        assert ok, errors
        manifest = core.list_delivery_snapshots(job_id)[0]
        assert approved["findings"][0]["resolution"]["action"] == "accepted_risk"
        assert manifest["accepted_risks"]
        assert manifest["accepted_risks"][0]["actor"] == "risk-owner"
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_configured_delivery_builds_every_selected_format_and_nothing_else():
    tmp = Path(tempfile.mkdtemp(prefix="delivery-configured-assets-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotconfigured"
        state = _job(job_id)
        # EPUB reads the source package to retain media and paragraph order.
        # A placeholder byte string is insufficient for the all-format fixture.
        from docx import Document
        source_document = Document()
        source_document.add_paragraph("Source text")
        source_buffer = BytesIO()
        source_document.save(source_buffer)
        core.save_source(job_id, source_buffer.getvalue())
        state["glossary"] = [{
            "source": "Source", "target": "来源", "preferred": "来源",
            "status": "locked", "scope": "document",
        }]
        config = {key: True for key in core.DELIVERY_CONFIG_DEFAULTS}
        state.update(
            report_enabled=True, p3_done=True, report_status="generated",
            case_reviews={"case-1": {
                "review_status": "approved", "content_stale": False,
                "review_reason": "fixture approved", "reviewed_at": "fixture",
            }},
            p3_md="# 翻译实践报告\n\n报告正文。",
            enable_annotate=True,
            annotations={0: [{"type": "domain", "src_span": [0, 6],
                              "tgt_span": [0, 2], "note": "术语"}]},
            annotations_done=True,
            delivery_config=core.normalize_delivery_config(
                config, enable_report=True, enable_annotate=True),
        )
        artifacts = {
            "research-model.json": {"schema_version": "test"},
            "argument-plan.json": {"schema_version": "test"},
            "selected-cases.json": {"cases": [{"case_id": "case-1"}]},
            "academic-outline.json": {"sections": []},
            "case-analysis-plans.json": {"plans": []},
            "human-evidence-questions.json": {"questions": []},
        }
        for filename, payload in artifacts.items():
            (core.job_dir(job_id) / filename).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        core.save_job_state(job_id, state)

        approved, ok, errors = core.approve_delivery(job_id, note="全格式")
        assert ok, errors
        assets = core.delivery_snapshot_assets(job_id, 1)
        expected = {
            "translation.docx", "bilingual.docx", "translation.pdf",
            "translation.epub", "epub_export_report.json", "docx_export_report.json",
                "original_format.docx", "original_format_report.json",
            "annotated_bilingual.docx", "terms.xlsx", "terms.tbx",
            "memory.tmx", "bilingual.jsonl", "segment_evidence.jsonl",
            "selected_cases.json", "academic_workspace.zip",
            "review_report.md", "report.docx", "report.md",
            "delivery_manifest.json",
        }
        assert set(assets) == expected
        assert approved["delivery_status"] == "final"
        assert assets["translation.pdf"].startswith(b"%PDF")
        with core.fitz.open(stream=assets["translation.pdf"], filetype="pdf") as pdf:
            assert pdf.page_count >= 1
            assert "译文" in "".join(page.get_text() for page in pdf)
        with ZipFile(BytesIO(assets["translation.docx"])) as docx:
            assert "word/document.xml" in docx.namelist()
        with ZipFile(BytesIO(assets["translation.epub"])) as epub:
            assert epub.read("mimetype") == b"application/epub+zip"
            assert "EPUB/content.opf" in epub.namelist()
        with ZipFile(BytesIO(assets["terms.xlsx"])) as workbook:
            assert "xl/workbook.xml" in workbook.namelist()
        with ZipFile(BytesIO(assets["academic_workspace.zip"])) as workspace:
            assert set(workspace.namelist()) == set(artifacts)
        manifest = json.loads(assets["delivery_manifest.json"])
        assert set(manifest["generated_assets"]) == expected

        minimal = core.load_job_state(job_id)
        minimal["delivery_status"] = "draft"
        minimal["delivery_approved_by_human"] = False
        minimal["delivery_approval"] = None
        minimal["delivery_config"] = {
            key: key == "deliver_jsonl" for key in core.DELIVERY_CONFIG_DEFAULTS
        }
        minimal["report_enabled"] = False
        minimal["enable_annotate"] = False
        core.save_job_state(job_id, minimal)
        working = core.build_delivery_assets(job_id, minimal)
        assert set(working) == {"bilingual.jsonl", "delivery_manifest.json"}
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_storage_is_local_output_data_not_package_input():
    root = Path(__file__).resolve().parent.parent
    assert "outputs/" in (root / ".gitignore").read_text(encoding="utf-8")
    assert snapshots.SNAPSHOT_DIR == "delivery_snapshots"


def test_load_asset_rehashes_after_same_size_rewrite_with_restored_mtime():
    """Export must not trust the status cache's (path, mtime, size) key."""
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-tamper-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshottamper0001"
        _job(job_id)
        _, ok, errors = core.approve_delivery(job_id, note="tamper", actor="reviewer")
        assert ok, errors
        manifest = core.list_delivery_snapshots(job_id)[0]
        name = manifest["assets"][0]["name"]
        root = Path(core.job_dir(job_id))
        path = snapshots.asset_path(root, 1, name)
        original = path.read_bytes()
        stat = path.stat()
        snapshots._ASSET_HASH_CACHE.clear()
        assert snapshots.load_asset(root, 1, name) == original

        changed = bytearray(original)
        changed[0] ^= 0xFF
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert path.stat().st_size == stat.st_size
        assert path.stat().st_mtime_ns == stat.st_mtime_ns

        assert snapshots.load_asset(root, 1, name) is None
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_this_module_reads_and_writes_utf8_explicitly():
    """RC3 S4-01: a GBK-default host must not decide how these fixtures decode.

    The acceptance hosts default to cp936, so a bare ``read_text()`` decodes a
    UTF-8 fixture as GBK and raises ``UnicodeDecodeError``.  Every read/write in
    this module therefore names UTF-8 explicitly, and this guard fails as soon
    as a new call forgets it — the same defect cannot come back silently.
    """
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute):
            # Path.read_text/Path.write_text; `fitz.open(...)` is not a text read.
            name = function.attr
            if name not in {"read_text", "write_text"}:
                continue
        elif isinstance(function, ast.Name) and function.id == "open":
            name = "open"   # the builtin: text mode unless told otherwise
        else:
            continue
        if any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        offenders.append(f"{name}() at line {node.lineno} without encoding=")
    assert not offenders, offenders


def test_snapshot_manifest_bytes_match_between_the_folder_and_the_zip():
    """RC3 S4-05: the ZIP entry and the frozen file must be the same bytes."""
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-manifest-bytes-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotmanifest001"
        _job(job_id)
        _, ok, errors = core.approve_delivery(job_id, note="字节一致")
        assert ok, errors
        manifest_path = snapshots.asset_path(
            Path(core.job_dir(job_id)), 1, snapshots.MANIFEST_FILE)
        frozen = manifest_path.read_bytes()

        # Text-mode writing on Windows rewrote every "\n" as "\r\n", while the
        # ZIP stored the LF-only string: parsed equal, byte-different.
        assert b"\r\n" not in frozen
        assert frozen.endswith(b"\n") and not frozen.endswith(b"\n\n")
        # The frozen bytes are the canonical serialization of their own JSON,
        # so freezing the same state again cannot produce different bytes.
        assert snapshots.manifest_bytes(json.loads(frozen)) == frozen

        archive = core.delivery_snapshot_archive(job_id, 1)
        assert archive
        with ZipFile(BytesIO(archive)) as bundle:
            assert bundle.read(snapshots.MANIFEST_FILE) == frozen
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_manifest_bytes_do_not_depend_on_key_insertion_order():
    """One canonical serializer: sorted keys, two-space indent, one newline."""
    first = {"zeta": 1, "alpha": {"b": 2, "a": 3}, "assets": [{"name": "a"}]}
    second = {"assets": [{"name": "a"}], "alpha": {"a": 3, "b": 2}, "zeta": 1}
    assert snapshots.manifest_bytes(first) == snapshots.manifest_bytes(second)
    assert snapshots.MANIFEST_INDENT == 2
    text = snapshots.manifest_bytes(first).decode("utf-8")
    assert text.startswith('{\n  "alpha": {')
    assert text.endswith("\n") and "\r\n" not in text
    # UTF-8 without ASCII escaping: a reviewer's note stays readable.
    assert "首版确认".encode("utf-8") in snapshots.manifest_bytes(
        {"note": "首版确认"})


def test_refreezing_the_same_state_writes_the_same_manifest_bytes(tmp_path):
    """The manifest is a function of the state, not of time or dict order."""
    state = {
        "pairs": [{"source": "Source text", "target": "译文"}],
        "delivery_approval": {"actor": "reviewer", "note": "首版确认",
                              "timestamp": "2026-01-01T00:00:00+00:00"},
    }
    root = tmp_path / "job"
    root.mkdir()
    manifests = [
        snapshots.create_snapshot(root, "same-state", dict(state),
                                  {"a.txt": b"a"}, {}, {})
        for _ in range(2)
    ]
    assert [item["snapshot_version"] for item in manifests] == [1, 2]
    without_version = [
        snapshots.manifest_bytes({key: value for key, value in item.items()
                                  if key != "snapshot_version"})
        for item in manifests
    ]
    assert without_version[0] == without_version[1]
    for version in (1, 2):
        on_disk = snapshots.snapshot_root(root) / f"v{version}" / snapshots.MANIFEST_FILE
        assert on_disk.read_bytes() == snapshots.manifest_bytes(manifests[version - 1])


def test_legacy_snapshot_manifest_bytes_survive_into_the_zip():
    """A snapshot frozen before the canonical serializer still ships as-is.

    Changing the serializer must not rewrite history: the ZIP is an archive of
    the frozen folder, so an older CRLF manifest must reach the ZIP unchanged.
    """
    tmp = Path(tempfile.mkdtemp(prefix="delivery-snapshot-legacy-manifest-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        job_id = "snapshotlegacybytes"
        _job(job_id)
        _, ok, errors = core.approve_delivery(job_id, note="旧版清单")
        assert ok, errors
        manifest_path = snapshots.asset_path(
            Path(core.job_dir(job_id)), 1, snapshots.MANIFEST_FILE)
        legacy = manifest_path.read_text(encoding="utf-8").replace(
            "\n", "\r\n").encode("utf-8")
        manifest_path.write_bytes(legacy)

        archive = core.delivery_snapshot_archive(job_id, 1)
        assert archive
        with ZipFile(BytesIO(archive)) as bundle:
            assert bundle.read(snapshots.MANIFEST_FILE) == legacy
    finally:
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)
