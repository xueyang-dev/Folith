"""Outline anchoring (#186): sidecar, paragraph lineage, honest resolution.

#187's panel is only allowed to consume a persisted outline and its resolver.
These tests pin the chain it depends on: the outline is parsed once and stored
next to the drafts, a structural edit re-identifies nodes by ``segment_uid``,
and every failure is reported with a reason instead of a silent position.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import outline_anchor as anchor  # noqa: E402
from folith import source_outline  # noqa: E402

JOB = "outlineanchor00001"
SOURCE = b"raw document bytes"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    return data_dir


def _outline(paragraphs, spans):
    """A parsed outline with the same shape ``parse_source_outline`` returns."""
    nodes = []
    for index, (title, start, count) in enumerate(spans):
        nodes.append({
            "id": f"n{index:04d}",
            "parent_id": "",
            "provenance": "heading",
            "kind": "chapter",
            "title": title,
            "level": 1,
            "page_start": None,
            "page_end": None,
            "pages": 0,
            "textless_pages": 0,
            "para_start": start,
            "para_count": count,
            "chars": sum(len(item) for item in paragraphs[start:start + count]),
            "sample": paragraphs[start] if count else "",
            "scanned": False,
            "paragraphs": list(paragraphs[start:start + count]),
        })
    outline = {
        "format": "DOCX",
        "parser_version": source_outline.OUTLINE_PARSER_VERSION,
        "nodes": nodes,
        "paragraphs": list(paragraphs),
        "pages": 0,
        "scanned": False,
        "partial": False,
        "total_paragraphs": len(paragraphs),
        "total_chars": sum(len(item) for item in paragraphs),
    }
    outline["fingerprint"] = source_outline.outline_fingerprint(outline)
    return outline


def _paragraphs():
    return ["第一章开篇。", "第一章正文。", "第二章开篇。", "第二章正文。"]


def _state(paragraphs, *, uids=True):
    state = core.new_job_state("outline.docx")
    pairs = []
    for index, source in enumerate(paragraphs):
        pair = {"source": source, "target": f"译文 {index}"}
        if uids:
            pair["segment_uid"] = f"seg-{JOB}-{index:04d}"
        pairs.append(pair)
    state.update(
        p1_done=True, p2_done=True, stage="TRANSLATED",
        source_paras=list(paragraphs), paras=list(paragraphs), pairs=pairs,
        segmentation={
            "input_paragraph_count": len(paragraphs),
            "paragraph_ranges": [
                {"paragraph_index": index, "start": index, "end": index + 1}
                for index in range(len(paragraphs))],
        },
        source_cleanup={
            "output_input_indices": [[index] for index in range(len(paragraphs))],
        },
    )
    return state


def _save(state, data=SOURCE):
    core.save_job_state(JOB, state)
    core.save_source(JOB, data)
    return state


def _parsed(outline):
    def parser(_filename, _data):
        return outline
    return parser


def _resolve(state, outline, *, persist=False):
    payload = {"outline": outline, "fingerprint": outline.get("fingerprint"),
               "mapping": {}, "parsed": False, "invalidated": ""}
    return anchor.resolve(JOB, state, payload, persist=persist)


def test_the_outline_is_parsed_once_and_stored_beside_the_task(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    calls = []

    def parser(filename, _data):
        calls.append(filename)
        return outline

    first = anchor.load_outline(JOB, state, parser=parser)
    assert first["parsed"] is True
    assert first["invalidated"] == "missing"
    assert first["outline"]["fingerprint"] == outline["fingerprint"]
    path = anchor.outline_sidecar_path(JOB)
    assert path.name == "source_outline.json"
    assert path.is_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["source_sha256"] == anchor.source_sha256(SOURCE)
    assert set(stored["outline"]) >= {"nodes", "paragraphs", "fingerprint"}

    def refuse(*_args):
        raise AssertionError("a warm sidecar must not be parsed again")

    second = anchor.load_outline(JOB, state, parser=refuse)
    assert second["parsed"] is False
    assert second["invalidated"] == ""
    assert second["outline"]["fingerprint"] == outline["fingerprint"]
    assert len(calls) == 1


def test_a_changed_source_invalidates_the_cached_outline(data_dir):
    state = _save(_state(_paragraphs()))
    old = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    anchor.store_outline(JOB, old, data=SOURCE)
    new = _outline(["只有一段。"], [("全文", 0, 1)])
    core.save_source(JOB, b"different bytes")

    result = anchor.load_outline(JOB, state, parser=_parsed(new))
    assert result["parsed"] is True
    assert result["invalidated"] == "source_changed"
    assert result["outline"]["fingerprint"] == new["fingerprint"]


def test_a_new_parser_version_invalidates_the_cached_outline(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    sidecar = {
        "version": anchor.OUTLINE_SIDECAR_VERSION,
        "parser_version": source_outline.OUTLINE_PARSER_VERSION - 1,
        "source_sha256": anchor.source_sha256(SOURCE),
        "fingerprint": outline["fingerprint"],
        "outline": outline,
        "mapping": {},
    }
    anchor.outline_sidecar_path(JOB).write_text(
        json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    result = anchor.load_outline(JOB, state, parser=_parsed(outline))
    assert result["parsed"] is True
    assert result["invalidated"] == "parser_changed"


def test_deleting_the_sidecar_only_drops_the_derived_artifact(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    anchor.store_outline(JOB, outline, data=SOURCE)
    anchor.delete_outline_sidecar(JOB)
    assert not anchor.outline_sidecar_path(JOB).exists()
    assert anchor.load_outline(JOB, state, parser=_parsed(outline))["parsed"] is True


def test_ensure_source_outline_writes_the_sidecar_for_a_new_task(data_dir):
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    document.add_heading("第一章", level=1)
    document.add_paragraph("第一章的正文内容，长度足够被保留下来。")
    document.add_heading("第二章", level=1)
    document.add_paragraph("第二章的正文内容，长度也足够被保留。")
    document.save(buffer)
    core.save_job_state(JOB, core.new_job_state("sample.docx"))
    core.save_source(JOB, buffer.getvalue())

    core.ensure_source_outline(JOB)

    assert anchor.outline_sidecar_path(JOB).is_file()
    assert core.load_job_state(JOB)["source_outline"]["nodes"]


def test_each_node_resolves_to_its_segment_span(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])

    mapping = _resolve(state, outline)

    assert mapping["source"] == "paragraphs"
    first, second = mapping["nodes"]["n0000"], mapping["nodes"]["n0001"]
    assert first["state"] == "resolved"
    assert (first["first_segment_index"], first["last_segment_index"]) == (0, 1)
    assert first["segment_uids"] == [f"seg-{JOB}-0000", f"seg-{JOB}-0001"]
    assert (second["first_segment_index"], second["last_segment_index"]) == (2, 3)


def test_unmatched_paragraphs_make_a_node_partial_not_resolved(data_dir):
    paragraphs = _paragraphs()
    state = _save(_state(paragraphs))
    outline = _outline(paragraphs, [("第一章", 0, 2), ("第二章", 2, 2)])
    state["source_paras"][1] = "这一段在提取后变了。"

    mapping = _resolve(state, outline)

    entry = mapping["nodes"]["n0000"]
    assert entry["state"] == "partial"
    assert entry["reason"] == "partial_paragraph_match"
    assert entry["matched_paragraphs"] == 1
    assert entry["unmatched_paragraphs"] == 1
    assert entry["first_segment_index"] == 0


def test_a_node_whose_paragraphs_are_all_excluded_says_so(data_dir):
    paragraphs = _paragraphs()
    state = _save(_state(paragraphs))
    outline = _outline(paragraphs, [("被排除的一节", 2, 1)])
    state["segmentation"]["paragraph_ranges"] = [
        {"paragraph_index": 0, "start": 0, "end": 1},
        {"paragraph_index": 1, "start": 1, "end": 2},
        {"paragraph_index": 3, "start": 2, "end": 3},
    ]

    entry = _resolve(state, outline)["nodes"]["n0000"]
    assert entry["state"] == "unresolved"
    assert entry["reason"] == "node_excluded"


def test_a_node_without_readable_paragraphs_says_so(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline([], [("扫描页", 0, 0)])
    outline["nodes"][0].update(scanned=True, paragraphs=[])

    entry = _resolve(state, outline)["nodes"]["n0000"]
    assert entry["state"] == "unresolved"
    assert entry["reason"] == "no_paragraph_text"


def test_a_task_without_segmentation_reports_that_reason(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2)])
    state.pop("segmentation")

    entry = _resolve(state, outline)["nodes"]["n0000"]
    assert entry["state"] == "unresolved"
    assert entry["reason"] == "segmentation_missing"


def test_missing_cleanup_lineage_refuses_to_guess(data_dir):
    state = _save(_state(_paragraphs()))
    outline = _outline(_paragraphs(), [("第一章", 0, 2)])
    state["source_cleanup"] = {"output_input_indices": []}
    state["segmentation"]["input_paragraph_count"] = len(_paragraphs()) + 1

    entry = _resolve(state, outline)["nodes"]["n0000"]
    assert entry["state"] == "unresolved"
    assert entry["reason"] == "cleanup_lineage_missing"


def test_a_legacy_task_without_uids_is_navigable_but_flagged(data_dir):
    state = _save(_state(_paragraphs(), uids=False))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])

    mapping = _resolve(state, outline)

    entry = mapping["nodes"]["n0000"]
    assert entry["state"] == "resolved"
    assert entry["reason"] == "positional_fallback"
    assert entry["segment_uids"] == []
    assert entry["first_segment_index"] == 0


def _persisted_mapping(outline):
    state = _save(_state(_paragraphs()))
    anchor.store_outline(JOB, outline, data=SOURCE)
    return anchor.resolve(JOB, state, {"outline": outline, "mapping": {}},
                          persist=True)


def test_excluding_a_segment_reidentifies_by_uid_and_names_the_exclusion(data_dir):
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    _persisted_mapping(outline)
    core.mutate_translation_segments(JOB, 1, "exclude")
    state = core.load_job_state(JOB)

    mapping = anchor.resolve(JOB, state)
    first = mapping["nodes"]["n0000"]
    second = mapping["nodes"]["n0001"]

    assert mapping["source"] == "uid"
    assert first["state"] == "partial"
    assert first["reason"] == "node_partial_excluded"
    assert (first["first_segment_index"], first["last_segment_index"]) == (0, 0)
    assert (second["first_segment_index"], second["last_segment_index"]) == (1, 2)


def test_a_split_segment_refuses_the_old_position(data_dir):
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    _persisted_mapping(outline)
    core.mutate_translation_segments(
        JOB, 0, "split", source_a="第一章开篇。", source_b="第一章正文。",
        target_a="译文 0", target_b="译文 1")
    state = core.load_job_state(JOB)

    mapping = anchor.resolve(JOB, state)

    entry = mapping["nodes"]["n0000"]
    assert entry["state"] == "unresolved"
    assert entry["reason"] == "structure_edited"
    assert entry["first_segment_index"] is None
    second = mapping["nodes"]["n0001"]
    assert second["state"] == "resolved"
    assert (second["first_segment_index"], second["last_segment_index"]) == (3, 4)


def test_a_legacy_mapping_refuses_old_positions_after_a_structural_edit(data_dir):
    state = _save(_state(_paragraphs(), uids=False))
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    anchor.store_outline(JOB, outline, data=SOURCE)
    anchor.resolve(JOB, state, {"outline": outline, "mapping": {}}, persist=True)

    core.mutate_translation_segments(JOB, 1, "exclude")
    state = core.load_job_state(JOB)
    mapping = anchor.resolve(JOB, state)

    assert mapping["source"] == "uid"
    for node_id in ("n0000", "n0001"):
        entry = mapping["nodes"][node_id]
        assert entry["state"] == "unresolved"
        assert entry["reason"] == "structure_edited"
        assert entry["first_segment_index"] is None


def _resegment_after_cleanup(state):
    """Simulate cleanup merging two paragraphs, then re-splitting them.

    The segment count is unchanged (4), so every segment uid still exists, but
    the nodes now cover other segments: exactly what a cleanup + resegmentation
    run does when the uid scheme is positional (``seg-{job}-{index:04d}``).
    """
    state["source_cleanup"] = {"output_input_indices": [[0], [1, 2], [3]]}
    state["segmentation"] = {
        "input_paragraph_count": 3,
        "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1},
            {"paragraph_index": 1, "start": 1, "end": 3},
            {"paragraph_index": 2, "start": 3, "end": 4},
        ],
    }
    return state


def test_cleanup_resegmentation_does_not_reuse_the_cached_mapping(data_dir):
    """#186/#204: cleanup may move node positions without moving segment uids.

    The uids are positional, so the old mapping's uids all still exist and the
    structure revision is unchanged.  A cached mapping must nevertheless not be
    served: its positions describe the pre-cleanup segmentation.
    """
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    first = _persisted_mapping(outline)
    assert first["source"] == "paragraphs"
    assert (first["nodes"]["n0000"]["first_segment_index"],
            first["nodes"]["n0000"]["last_segment_index"]) == (0, 1)
    assert (first["nodes"]["n0001"]["first_segment_index"],
            first["nodes"]["n0001"]["last_segment_index"]) == (2, 3)

    state = _resegment_after_cleanup(core.load_job_state(JOB))
    core.save_job_state(JOB, state)
    second = anchor.resolve(JOB, state, persist=True)

    assert second["source"] != "cache"
    first_node, second_node = second["nodes"]["n0000"], second["nodes"]["n0001"]
    assert (first_node["first_segment_index"],
            first_node["last_segment_index"]) == (0, 0)
    assert (second_node["first_segment_index"],
            second_node["last_segment_index"]) == (3, 3)


def test_reset_after_cleanup_drops_the_mapping_but_keeps_the_outline(data_dir):
    """The reset keeps the outline (source bytes unchanged) and drops positions."""
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    _persisted_mapping(outline)
    state = core.load_job_state(JOB)
    state["source_outline"] = outline

    core._reset_source_dependent_state(state, job_id=JOB)

    stored = json.loads(anchor.outline_sidecar_path(JOB).read_text(encoding="utf-8"))
    assert stored["mapping"] == {}
    assert stored["outline"]["fingerprint"] == outline["fingerprint"]
    assert "source_outline" not in state


def test_reset_without_a_sidecar_is_harmless(data_dir):
    _save(_state(_paragraphs()))
    core._reset_source_dependent_state({}, job_id=JOB)
    assert not anchor.outline_sidecar_path(JOB).exists()


def _pdf_bytes():
    import fitz

    with fitz.open() as document:
        page = document.new_page(width=144, height=144)
        page.insert_text((20, 40), "scan", fontsize=10)
        return document.tobytes()


def test_rescanning_the_source_drops_the_persisted_outline(data_dir, monkeypatch):
    """A rescan replaces the source bytes: outline and mapping both go (#204)."""
    state = _state(_paragraphs())
    state["filename"] = "scan.pdf"
    _save(state, _pdf_bytes())
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    anchor.store_outline(JOB, outline, data=_pdf_bytes())
    anchor.resolve(JOB, state, {"outline": outline, "mapping": {}}, persist=True)
    assert json.loads(anchor.outline_sidecar_path(JOB).read_text())["mapping"]

    raw = list(_paragraphs())
    monkeypatch.setattr(
        core, "extract_document_paragraphs_with_report",
        lambda *args, **kwargs: (raw, [], {"semantic_source": "text"}))
    monkeypatch.setattr(
        core, "cleanup_source_paragraphs",
        lambda *args, **kwargs: (list(raw), {"status": "ok"}, []))

    core.rescan_pdf_source(
        JOB, filename="scan.pdf", provider="DeepSeek", api_key="offline",
        model="fixture")

    assert not anchor.outline_sidecar_path(JOB).exists()


def test_reimporting_the_document_drops_the_persisted_outline(data_dir):
    """A re-import may replace the source bytes: the outline is dropped (#204)."""
    from folith.application import import_document

    values = {
        "provider": "OpenAI", "model": "gpt-5.1", "api_key": "sk",
        "translator_base_url": "https://api.openai.com/v1",
        "target_lang": "English", "enable_report": False,
    }
    first = import_document("outline.docx", SOURCE, values)
    outline = _outline(_paragraphs(), [("第一章", 0, 2), ("第二章", 2, 2)])
    anchor.store_outline(first.job_id, outline, data=SOURCE)
    assert anchor.outline_sidecar_path(first.job_id).is_file()

    again = import_document("outline.docx", SOURCE, values)

    assert again.created is False
    assert again.job_id == first.job_id
    assert not anchor.outline_sidecar_path(first.job_id).exists()
