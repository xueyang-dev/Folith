import hashlib
import os
from pathlib import Path

import pytest

import core
from folith import interop


DATA = b'''<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
 <file original="a"><body>
  <trans-unit id="1"><source>Hello</source><target>Bonjour</target></trans-unit>
  <trans-unit id="2" locked="true"><source>Locked</source><target>Garde &amp; garde</target></trans-unit>
  <trans-unit id="3" translate="no"><source>Skip</source><target>Skip</target></trans-unit>
 </body></file>
</xliff>'''


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    return core.OUTPUT_DIR


def test_import_reopen_and_generic_export(outputs, tmp_path):
    imported = core.import_xliff_task("a.xlf", DATA)
    job_id = imported["job_id"]
    assert imported["created"] and imported["dialect"] == "xliff12"
    state = core.load_job_state(job_id)
    assert (core.job_dir(job_id) / "source.bin").read_bytes() == DATA
    assert state["xliff"]["source_sha256"] == hashlib.sha256(DATA).hexdigest()
    assert state["xliff"]["ledger"]["generation"] == 1
    assert len(state["pairs"]) == 2 and len(state["paras"]) == 2
    assert imported["excluded"][0]["reason"] == "translate_no"
    assert state["pairs"][1]["readonly"] is True
    core.save_translation_edit(job_id, 0, "Salut")
    reopened = core.import_xliff_task("a.xlf", DATA)
    assert reopened["created"] is False
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "Salut"
    original = tmp_path / "original.xlf"
    assert core.export_xliff_task(job_id, original)["changed_count"] == 0
    assert original.read_bytes() == DATA
    written = tmp_path / "translated.xlf"
    result = core.export_xliff_task(job_id, written, write_targets=True)
    assert result["changed_count"] == 1
    doc = interop.parse_xliff12(written.read_bytes())
    assert doc.target_of(state["pairs"][0]["external_segment"]["external_id"]) == "Salut"
    assert b'<target>Garde &amp; garde</target>' in written.read_bytes()
    assert (core.job_dir(job_id) / "source.bin").read_bytes() == DATA


def test_locked_mutation_and_identity_corruption_refused(outputs, tmp_path):
    job_id = core.import_xliff_task("a.xlf", DATA)["job_id"]
    state = core.load_job_state(job_id)
    state["pairs"][1]["target"] = "changed"
    state["pairs"][1]["readonly"] = False
    core.save_job_state(job_id, state)
    path = tmp_path / "output.xlf"
    path.write_bytes(b"existing")
    with pytest.raises(ValueError, match="锁定段"):
        core.export_xliff_task(job_id, path, write_targets=True)
    assert path.read_bytes() == b"existing"
    assert core.export_xliff_task(job_id, path)["changed_count"] == 0
    assert path.read_bytes() == DATA
    state = core.load_job_state(job_id)
    state["pairs"][1]["target"] = "Garde & garde"
    state["pairs"][0]["external_segment"]["external_id"] = "forged"
    core.save_job_state(job_id, state)
    with pytest.raises(ValueError, match="身份"):
        core.export_xliff_task(job_id, path, write_targets=True)


@pytest.mark.parametrize("suffix", ["sdlxliff", "mqxliff"])
def test_dialects_export_original_even_after_edit(outputs, tmp_path, suffix):
    job_id = core.import_xliff_task(f"a.{suffix}", DATA)["job_id"]
    state = core.load_job_state(job_id)
    assert all(pair["readonly"] for pair in state["pairs"])
    state["pairs"][0]["target"] = "modified"
    core.save_job_state(job_id, state)
    out = tmp_path / "out"
    core.export_xliff_task(job_id, out)
    assert out.read_bytes() == DATA
    with pytest.raises(ValueError, match="仅支持原字节"):
        core.export_xliff_task(job_id, out, write_targets=True)
    assert out.read_bytes() == DATA


def test_original_protection_and_corruption(outputs, tmp_path):
    job_id = core.import_xliff_task("a.xlf", DATA)["job_id"]
    source = core.job_dir(job_id) / "source.bin"
    with pytest.raises(ValueError, match="内部"):
        core.export_xliff_task(job_id, source)
    link = tmp_path / "link"
    os.link(source, link)
    with pytest.raises(ValueError, match="内部"):
        core.export_xliff_task(job_id, link)
    source.write_bytes(b"broken")
    with pytest.raises(ValueError, match="损坏"):
        core.export_xliff_task(job_id, tmp_path / "out")


def test_import_and_export_fail_atomically(outputs, tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        core.import_xliff_task("bad.xlf", b"broken")
    assert not outputs.exists() or not core.list_jobs()
    job_id = core.import_xliff_task("a.xlf", DATA)["job_id"]
    output = tmp_path / "out.xlf"
    output.write_bytes(b"previous")
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(core._atomic_file, "replace_file", fail)
    with pytest.raises(OSError, match="replace failed"):
        core.export_xliff_task(job_id, output)
    assert output.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".folith-xliff-*.tmp"))


def test_import_publish_failure_leaves_no_task(outputs, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("publish failed")
    monkeypatch.setattr(core.os, "rename", fail)
    with pytest.raises(OSError, match="publish failed"):
        core.import_xliff_task("a.xlf", DATA)
    assert core.list_jobs() == []
    assert not list(tmp_path.glob(".folith-xliff-*"))


def test_partial_subsegment_lock_cannot_be_overwritten(outputs, tmp_path):
    data = b'''<xliff version="1.2" xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"><file original="a"><body>
    <trans-unit id="1"><source>Hello</source><target><mrk mtype="seg" mid="1">one</mrk><mrk mtype="seg" mid="2">two</mrk></target>
    <sdl:seg id="1" locked="true"/><sdl:seg id="2" locked="false"/></trans-unit>
    </body></file></xliff>'''
    # Namespace detection must keep a disguised dialect read-only.
    job = core.import_xliff_task("a.xlf", data)
    assert job["dialect"] == "sdlxliff"
    state = core.load_job_state(job["job_id"])
    # One row per trans-unit: the kernel's subsegment rows would show the whole
    # unit source next to a single subsegment's target.
    assert len(state["pairs"]) == 1
    assert state["pairs"][0]["readonly"] is True
    assert state["pairs"][0]["readonly_reason"] == "partial_subsegment_lock"
    assert "部分子段" in " ".join(state["xliff"]["report"]["warnings"])
    with pytest.raises(ValueError, match="仅支持原字节"):
        core.export_xliff_task(job["job_id"], tmp_path / "out", write_targets=True)


def test_no_target_and_invalid_placeholder_fail_without_overwrite(outputs, tmp_path):
    data = b'''<xliff version="1.2"><file original="a"><body>
    <trans-unit id="1"><source>hello</source><target>A<x id="1"/>B</target></trans-unit>
    <trans-unit id="2"><source>missing</source></trans-unit>
    </body></file></xliff>'''
    job_id = core.import_xliff_task("a.xlf", data)["job_id"]
    state = core.load_job_state(job_id)
    state["pairs"][0]["target"] = "no placeholder"
    core.save_job_state(job_id, state)
    out = tmp_path / "out"
    out.write_bytes(b"old")
    with pytest.raises(ValueError, match="占位符"):
        core.export_xliff_task(job_id, out, write_targets=True)
    assert out.read_bytes() == b"old"
    state = core.load_job_state(job_id)
    state["pairs"][0]["target"] = interop.build_import(interop.parse_xliff12(data)).pairs[0]["target"]
    state["pairs"][1]["target"] = "new"
    core.save_job_state(job_id, state)
    with pytest.raises(ValueError, match="没有 <target>"):
        core.export_xliff_task(job_id, out, write_targets=True)
    assert out.read_bytes() == b"old"


def _entity_repair_state(*, all_readonly=False):
    state = core.new_job_state("entities.xlf")
    state.update(p1_done=True, p2_done=True, annotations_done=True,
                 annotations_done_offset=2,
                 paras=["Lydia first", "Lydia second"],
                 pairs=[{"source": "Lydia first", "target": "莉迪娅 first", "readonly": True,
                         "reviewed": True, "review_status": "reviewed"},
                        {"source": "Lydia second", "target": "莉迪娅 second", "readonly": all_readonly,
                         "reviewed": True, "review_status": "reviewed", "human_accepted": True}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪娅", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    state["findings"] = [{"type": "entity_conflict", "source": "Lydia", "resolved": False}]
    return state


@pytest.mark.parametrize("all_readonly", [False, True])
def test_entity_repair_skips_readonly_but_keeps_conflict(outputs, all_readonly):
    state = _entity_repair_state(all_readonly=all_readonly)
    core.save_job_state("entity", state)
    preview = core.preview_entity_repair(state, "Lydia", "莉迪亚")
    assert [change["index"] for change in preview] == [0, 1]
    updated, indexes, skipped = core.apply_entity_repair_skipping_readonly(
        "entity", "Lydia", "莉迪亚", preview)
    assert indexes == ([] if all_readonly else [1])
    assert skipped == (2 if all_readonly else 1)
    assert updated["pairs"][0] == state["pairs"][0]
    if not all_readonly:
        assert updated["pairs"][1]["target"] == "莉迪亚 second"
        assert not updated["pairs"][1]["reviewed"]
        assert "human_accepted" not in updated["pairs"][1]
        assert updated["translation_truth"]["last_change"]["segment_indexes"] == [1]
        assert not updated["annotations_done"]
    else:
        assert updated["pairs"][1] == state["pairs"][1]
    entity = updated["entity_registry"][0]
    assert entity["preferred_target"] == "莉迪亚" and entity["status"] == "locked"
    assert not entity["accepted_variants"]
    queue = core.entity_consistency_queue(updated)
    assert any(0 in item["segment_indexes"] for item in queue)
    assert not next(f for f in updated["findings"] if f.get("type") == "entity_conflict")["resolved"]
    persisted = core.load_job_state("entity")
    assert persisted["pairs"][0]["target"] == "莉迪娅 first"
    assert core.entity_consistency_queue(persisted)


def test_entity_repair_rejects_stale_full_preview(outputs):
    state = _entity_repair_state()
    core.save_job_state("stale", state)
    preview = core.preview_entity_repair(state, "Lydia", "莉迪亚")
    state = core.load_job_state("stale")
    state["pairs"][0]["target"] += " changed"
    core.save_job_state("stale", state)
    before = core.load_job_state("stale")
    with pytest.raises(ValueError, match="预览"):
        core.apply_entity_repair_skipping_readonly("stale", "Lydia", "莉迪亚", preview)
    assert core.load_job_state("stale") == before
    with pytest.raises(ValueError, match="预览"):
        core.apply_entity_repair_skipping_readonly("stale", "Lydia", "莉迪亚", preview[1:])
    assert core.load_job_state("stale") == before


def test_entity_repair_refuses_hidden_locked_conflict(outputs, monkeypatch):
    state = _entity_repair_state()
    core.save_job_state("hidden", state)
    preview = core.preview_entity_repair(state, "Lydia", "莉迪亚")
    before = core.load_job_state("hidden")
    monkeypatch.setattr(core, "entity_consistency_queue", lambda state: [])
    with pytest.raises(ValueError, match="冲突无法保留"):
        core.apply_entity_repair_skipping_readonly("hidden", "Lydia", "莉迪亚", preview)
    assert core.load_job_state("hidden") == before
